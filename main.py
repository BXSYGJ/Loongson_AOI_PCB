import asyncio
import math
import os
import platform
import re
import socket
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import camera_trigger
import motor_controller


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

FOV_WIDTH_MM = 30.0
FOV_HEIGHT_MM = 30.0
MAX_X_MM = 135.0
MAX_Y_MM = 135.0
DEBOUNCE_SECONDS = float(os.getenv("LOONGSON_AOI_DEBOUNCE_SECONDS", "0.05"))
VIDEO_SPEED_MM_S = float(os.getenv("LOONGSON_AOI_VIDEO_SPEED_MM_S", "2.0"))
VIDEO_INFERENCE_FPS = float(os.getenv("LOONGSON_AOI_VIDEO_INFERENCE_FPS", "5.0"))
VIDEO_PREVIEW_FPS = float(os.getenv("LOONGSON_AOI_VIDEO_PREVIEW_FPS", "8.0"))
VIDEO_OVERLAP = float(os.getenv("LOONGSON_AOI_VIDEO_OVERLAP", "0.2"))
MOCK_INFERENCE_SECONDS = 0.35
LAN_LISTEN_HOST = "0.0.0.0"
LAN_LISTEN_PORT = 8000
OPERATION_MODES = {"handheld", "simulation", "real", "video", "mock"}


app = FastAPI(title="Loongson AOI PCB Console", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

scan_lock = asyncio.Lock()
active_stop_event: asyncio.Event | None = None
active_scan_task: asyncio.Task | None = None


def file_status(path: Path) -> dict[str, Any]:
    exists = path.exists()
    status: dict[str, Any] = {
        "path": str(path.relative_to(ROOT)).replace("\\", "/") if path.is_relative_to(ROOT) else str(path),
        "exists": exists,
    }
    if exists:
        stat = path.stat()
        status["size_bytes"] = stat.st_size
        status["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    return status


def detect_script_status() -> dict[str, Any]:
    try:
        script = camera_trigger.resolve_detect_script()
        status = file_status(script)
        status["resolved"] = True
        return status
    except camera_trigger.CameraTriggerError as exc:
        return {
            "path": "algorithm/scripts/detect.py or algorithm/core/detect.py",
            "exists": False,
            "resolved": False,
            "message": str(exc),
        }


def system_status() -> dict[str, Any]:
    scan_running = bool(active_scan_task and not active_scan_task.done())
    try:
        motor = motor_controller.get_status()
        motor["status"] = "TB6600 GPIO driver ready." if motor.get("connected") else "TB6600 GPIO driver dry-run mode."
        motor["interfaces"] = ["home_all_axes", "move_z_step", "move_to_xy", "move_to_xy_stream"]
    except Exception as exc:
        motor = {
            "driver_mode": "error",
            "connected": False,
            "status": str(exc),
            "interfaces": ["home_all_axes", "move_z_step", "move_to_xy", "move_to_xy_stream"],
        }

    camera_runtime = camera_trigger.camera_status(probe=True)
    detector_runtime = camera_trigger.detector_status()
    return {
        "motor": motor,
        "camera": {
            "runtime": camera_runtime,
            "capture_image": file_status(camera_trigger.DEFAULT_IMAGE),
            "status": "ready" if camera_runtime.get("opened") else "camera_unavailable",
        },
        "simulation": {
            "import_dir": str(camera_trigger.SIMULATION_IMPORT_DIR),
        },
        "algorithm": {
            "detect_script": detect_script_status(),
            "weights": file_status(camera_trigger.DEFAULT_WEIGHTS),
            "config": file_status(camera_trigger.DEFAULT_CONFIG),
            "result_json": file_status(camera_trigger.RESULT_JSON),
            "runtime": detector_runtime,
        },
        "scan": {
            "running": scan_running,
            "modes": ["handheld", "simulation", "real", "video"],
            "mode_hint": "handheld uses the real camera without slide motion; simulation uses an uploaded image; real uses stop-and-shoot slide motion; video uses continuous raster motion and live YOLO inference.",
            "video": camera_trigger.video_status(),
        },
        "network": network_status(),
    }


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    hostname = socket.gethostname()

    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        try:
            probe.close()
        except UnboundLocalError:
            pass

    addresses.update(platform_ipv4_addresses())

    return sorted(
        ip
        for ip in addresses
        if not ip.startswith("127.") and not ip.startswith("169.254.") and ip != "0.0.0.0"
    )


def platform_ipv4_addresses() -> set[str]:
    system = platform.system().lower()
    command = ["ipconfig"] if system == "windows" else ["hostname", "-I"]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()

    if system == "windows":
        return {
            match.group(0)
            for line in result.stdout.splitlines()
            if "IPv4" in line
            for match in re.finditer(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
        }

    return set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", result.stdout))


def network_status() -> dict[str, Any]:
    ips = local_ipv4_addresses()
    return {
        "listen_host": LAN_LISTEN_HOST,
        "port": LAN_LISTEN_PORT,
        "lan_ips": ips,
        "lan_urls": [f"http://{ip}:{LAN_LISTEN_PORT}/" for ip in ips],
        "hint": "Keep the phone and PC on the same LAN and allow TCP 8000 through the firewall.",
    }


def build_grid_plan(
    pcb_length_mm: float,
    pcb_width_mm: float,
    origin_x_mm: float = 0.0,
    origin_y_mm: float = 0.0,
) -> dict[str, Any]:
    if pcb_length_mm <= 0 or pcb_width_mm <= 0:
        raise ValueError("PCB length and width must be positive.")
    if origin_x_mm < 0 or origin_y_mm < 0:
        raise ValueError("Origin coordinates must be non-negative.")

    cols = math.ceil(pcb_length_mm / FOV_WIDTH_MM)
    rows = math.ceil(pcb_width_mm / FOV_HEIGHT_MM)
    covered_length = cols * FOV_WIDTH_MM
    covered_width = rows * FOV_HEIGHT_MM
    overflow_right = max(0.0, covered_length - pcb_length_mm)
    overflow_bottom = max(0.0, covered_width - pcb_width_mm)

    max_x = origin_x_mm + max(cols - 1, 0) * FOV_WIDTH_MM
    max_y = origin_y_mm + max(rows - 1, 0) * FOV_HEIGHT_MM
    if max_x > MAX_X_MM or max_y > MAX_Y_MM:
        raise ValueError(
            f"Grid exceeds slide travel: max_x={max_x:.2f}mm, max_y={max_y:.2f}mm, "
            f"limit={MAX_X_MM:.2f}x{MAX_Y_MM:.2f}mm."
        )

    visible_last_col = (pcb_length_mm - (cols - 1) * FOV_WIDTH_MM) / FOV_WIDTH_MM if cols else 1.0
    visible_last_row = (pcb_width_mm - (rows - 1) * FOV_HEIGHT_MM) / FOV_HEIGHT_MM if rows else 1.0

    return {
        "rows": rows,
        "cols": cols,
        "fov_mm": {"width": FOV_WIDTH_MM, "height": FOV_HEIGHT_MM},
        "pcb_mm": {"length": pcb_length_mm, "width": pcb_width_mm},
        "covered_mm": {"length": covered_length, "width": covered_width},
        "origin_mm": {"x": origin_x_mm, "y": origin_y_mm},
        "logical_boundary": {
            "overflow_right_mm": round(overflow_right, 4),
            "overflow_bottom_mm": round(overflow_bottom, 4),
            "last_col_visible_ratio": round(max(0.0, min(1.0, visible_last_col)), 4),
            "last_row_visible_ratio": round(max(0.0, min(1.0, visible_last_row)), 4),
            "edge_cells_clipped": overflow_right > 0 or overflow_bottom > 0,
        },
        "total_cells": rows * cols,
    }


def iter_snake_cells(plan: dict[str, Any]) -> list[dict[str, float | int]]:
    rows = int(plan["rows"])
    cols = int(plan["cols"])
    origin = plan["origin_mm"]
    cells: list[dict[str, float | int]] = []

    for row in range(rows):
        col_range = range(cols) if row % 2 == 0 else range(cols - 1, -1, -1)
        for col in col_range:
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "x_mm": float(origin["x"]) + col * FOV_WIDTH_MM,
                    "y_mm": float(origin["y"]) + row * FOV_HEIGHT_MM,
                }
            )
    return cells


def _video_rows(y_max_mm: float, line_pitch_mm: float) -> list[float]:
    rows: list[float] = []
    y = 0.0
    while y < y_max_mm:
        rows.append(round(y, 4))
        y += line_pitch_mm
    if not rows or not math.isclose(rows[-1], y_max_mm, abs_tol=0.001):
        rows.append(round(y_max_mm, 4))
    return rows


def build_video_plan(
    speed_mm_s: float = VIDEO_SPEED_MM_S,
    inference_fps: float = VIDEO_INFERENCE_FPS,
    preview_fps: float = VIDEO_PREVIEW_FPS,
    overlap: float = VIDEO_OVERLAP,
) -> dict[str, Any]:
    speed = float(speed_mm_s)
    inference = float(inference_fps)
    preview = float(preview_fps)
    overlap_value = float(overlap)
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed_mm_s must be positive.")
    if not math.isfinite(inference) or inference <= 0:
        raise ValueError("inference_fps must be positive.")
    if not math.isfinite(preview) or preview <= 0:
        raise ValueError("preview_fps must be positive.")
    if not math.isfinite(overlap_value) or overlap_value < 0 or overlap_value >= 0.85:
        raise ValueError("overlap must be between 0 and 0.85.")

    motor = motor_controller.get_status()
    axes = motor.get("axes", {})
    x_max = float(axes.get("x", {}).get("max_mm", MAX_X_MM))
    y_max = float(axes.get("y", {}).get("max_mm", MAX_Y_MM))
    z_max = float(axes.get("z", {}).get("max_mm", MAX_Y_MM))
    line_pitch = FOV_HEIGHT_MM * (1.0 - overlap_value)
    row_positions = _video_rows(y_max, line_pitch)
    final_x = x_max if (len(row_positions) - 1) % 2 == 0 else 0.0
    final_y = row_positions[-1]
    scan_distance = len(row_positions) * x_max
    transition_distance = sum(abs(row_positions[i] - row_positions[i - 1]) for i in range(1, len(row_positions)))
    return_distance = abs(final_x) + abs(final_y)
    estimated_seconds = (scan_distance + transition_distance + return_distance) / speed
    homed = {
        axis: bool(axes.get(axis, {}).get("homed"))
        for axis in ("x", "y", "z")
    }

    return {
        "scan_type": "continuous_video",
        "operation_mode": "video",
        "rows": len(row_positions),
        "cols": 1,
        "total_cells": len(row_positions),
        "row_positions_mm": row_positions,
        "fov_mm": {"width": FOV_WIDTH_MM, "height": FOV_HEIGHT_MM},
        "travel_mm": {"x_max": x_max, "y_max": y_max, "z_max": z_max},
        "origin_mm": {"x": 0.0, "y": 0.0},
        "line_pitch_mm": round(line_pitch, 4),
        "overlap": round(overlap_value, 4),
        "speed_mm_s": speed,
        "inference_fps": inference,
        "preview_fps": preview,
        "estimated_seconds": round(estimated_seconds, 2),
        "homed": homed,
        "requires_soft_home": not all(homed.values()),
    }


def video_area_mm(x_mm: float, y_mm: float, plan: dict[str, Any]) -> dict[str, Any]:
    fov = plan["fov_mm"]
    travel = plan["travel_mm"]
    width = float(fov["width"])
    height = float(fov["height"])
    x_max = float(travel["x_max"])
    y_max = float(travel["y_max"])
    return {
        "x": round(float(x_mm), 4),
        "y": round(float(y_mm), 4),
        "width": width,
        "height": height,
        "clipped_right_mm": round(max(0.0, float(x_mm) + width - x_max), 4),
        "clipped_bottom_mm": round(max(0.0, float(y_mm) + height - y_max), 4),
    }


def video_position_payload(
    position: dict[str, float],
    plan: dict[str, Any],
    segment: dict[str, Any] | None = None,
    progress: float | None = None,
) -> dict[str, Any]:
    x_mm = float(position.get("x_mm", 0.0))
    y_mm = float(position.get("y_mm", 0.0))
    payload: dict[str, Any] = {
        "x_mm": round(x_mm, 4),
        "y_mm": round(y_mm, 4),
        "area_mm": video_area_mm(x_mm, y_mm, plan),
    }
    if segment is not None:
        payload["segment"] = dict(segment)
    if progress is not None:
        payload["progress"] = progress
    return payload


async def send_packet(websocket: WebSocket, packet_type: str, payload: dict[str, Any], scan_id: str | None = None) -> None:
    packet: dict[str, Any] = {"type": packet_type, "payload": payload}
    if scan_id is not None:
        packet["scan_id"] = scan_id
    await websocket.send_json(packet)


async def run_scan(
    websocket: WebSocket,
    scan_id: str,
    plan: dict[str, Any],
    image_path: str | None,
    capture_mode: str,
    operation_mode: str,
    stop_event: asyncio.Event,
) -> None:
    cells = iter_snake_cells(plan)
    total = len(cells)
    motion_enabled = operation_mode == "real"

    plan_payload = dict(plan)
    plan_payload["operation_mode"] = operation_mode
    await send_packet(websocket, "grid_plan", plan_payload, scan_id)

    for index, cell in enumerate(cells, start=1):
        if stop_event.is_set():
            await send_packet(
                websocket,
                "scan_stopped",
                {"message": "Scan stopped before next XY move.", "progress": {"done": index - 1, "total": total}},
                scan_id,
            )
            return

        row = int(cell["row"])
        col = int(cell["col"])
        x_mm = float(cell["x_mm"])
        y_mm = float(cell["y_mm"])

        await send_packet(
            websocket,
            "scan_phase",
            {
                "phase": "move_xy",
                "row": row,
                "col": col,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "operation_mode": operation_mode,
                "motion": "physical" if motion_enabled else "simulated",
            },
            scan_id,
        )
        if motion_enabled:
            try:
                motor_controller.move_to_xy(x_mm, y_mm)
            except Exception as exc:
                await send_packet(
                    websocket,
                    "scan_error",
                    {
                        "message": f"XY move failed at row={row}, col={col}: {exc}",
                        "row": row,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                    },
                    scan_id,
                )
                return

        await send_packet(
            websocket,
            "scan_phase",
            {"phase": "debounce", "duration_ms": int(DEBOUNCE_SECONDS * 1000), "row": row, "col": col},
            scan_id,
        )
        await asyncio.sleep(DEBOUNCE_SECONDS)

        if stop_event.is_set():
            await send_packet(
                websocket,
                "scan_stopped",
                {"message": "Scan stopped after debounce.", "progress": {"done": index - 1, "total": total}},
                scan_id,
            )
            return

        if operation_mode == "mock" or capture_mode == "mock":
            await send_packet(
                websocket,
                "scan_phase",
                {"phase": "capture_inference", "row": row, "col": col, "x_mm": x_mm, "y_mm": y_mm},
                scan_id,
            )
            result = await mock_capture_detect_and_read(row, col, x_mm, y_mm, stop_event)
        else:
            capture_source = "simulation_image" if operation_mode == "simulation" else "camera"
            await send_packet(
                websocket,
                "scan_phase",
                {
                    "phase": "capture_frame",
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "source": capture_source,
                },
                scan_id,
            )
            if operation_mode == "simulation":
                capture = await camera_trigger.capture_simulation_cell(
                    row,
                    col,
                    x_mm,
                    y_mm,
                    scan_id,
                    image_path,
                    plan,
                    stop_event,
                )
            else:
                capture = await camera_trigger.capture_frame(row, col, x_mm, y_mm, scan_id, stop_event)

            if not capture.get("ok"):
                packet_type = "scan_stopped" if capture.get("error") == "scan_stopped" else "scan_error"
                await send_packet(websocket, packet_type, capture, scan_id)
                return

            if stop_event.is_set():
                await send_packet(
                    websocket,
                    "scan_stopped",
                    {"message": "Scan stopped after frame capture.", "progress": {"done": index - 1, "total": total}},
                    scan_id,
                )
                return

            await send_packet(
                websocket,
                "scan_phase",
                {
                    "phase": "detect_inference",
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "raw_image": capture.get("raw_image"),
                    "capture_ms": capture.get("capture_ms"),
                    "source": capture_source,
                },
                scan_id,
            )
            result = await camera_trigger.detect_captured_frame(
                row,
                col,
                x_mm,
                y_mm,
                scan_id,
                capture["raw_image"],
                stop_event,
            )
            if result.get("ok"):
                result["capture_ms"] = capture.get("capture_ms")
                if capture.get("source_image"):
                    result["source_image"] = capture.get("source_image")
                if capture.get("simulation_crop"):
                    result["simulation_crop"] = capture.get("simulation_crop")

        if not result.get("ok"):
            packet_type = "scan_stopped" if result.get("error") == "scan_stopped" else "scan_error"
            await send_packet(websocket, packet_type, result, scan_id)
            return

        await send_packet(
            websocket,
            "scan_result",
            {
                "row": row,
                "col": col,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "phase": "result",
                "progress": {"done": index, "total": total},
                "result_json": result["result_json"],
                "defects": result["defects"],
                "raw_image": result.get("raw_image"),
                "annotated_image": result.get("annotated_image"),
                "capture_ms": result.get("capture_ms"),
                "inference_ms": result.get("inference_ms"),
                "source_image": result.get("source_image"),
                "simulation_crop": result.get("simulation_crop"),
                "operation_mode": operation_mode,
            },
            scan_id,
        )

    origin = plan["origin_mm"]
    origin_x_mm = float(origin["x"])
    origin_y_mm = float(origin["y"])
    await send_packet(
        websocket,
        "scan_phase",
        {
            "phase": "return_origin",
            "x_mm": origin_x_mm,
            "y_mm": origin_y_mm,
            "operation_mode": operation_mode,
            "motion": "physical" if motion_enabled else "simulated",
        },
        scan_id,
    )
    if motion_enabled:
        try:
            motor_controller.move_to_xy(origin_x_mm, origin_y_mm)
        except Exception as exc:
            await send_packet(
                websocket,
                "scan_error",
                {
                    "message": f"Return to scan origin failed: {exc}",
                    "x_mm": origin_x_mm,
                    "y_mm": origin_y_mm,
                    "progress": {"done": total, "total": total},
                },
                scan_id,
            )
            return

    await send_packet(
        websocket,
        "scan_complete",
        {
            "message": "Scan complete. Origin restored.",
            "progress": {"done": total, "total": total},
            "returned_to_origin": True,
            "origin_mm": {"x": origin_x_mm, "y": origin_y_mm},
            "operation_mode": operation_mode,
            "return_motion": "physical" if motion_enabled else "simulated",
        },
        scan_id,
    )


async def run_video_scan(
    websocket: WebSocket,
    scan_id: str,
    plan: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    send_lock = asyncio.Lock()

    async def emit(packet_type: str, payload: dict[str, Any]) -> None:
        async with send_lock:
            await send_packet(websocket, packet_type, payload, scan_id)

    await emit("video_plan", plan)

    if plan.get("requires_soft_home"):
        await emit(
            "video_phase",
            {
                "phase": "soft_home_required",
                "message": "请先手动移动到物理原点并执行三轴回零，再启动视频检测。",
                "homed": plan.get("homed", {}),
            },
        )
        await emit(
            "video_error",
            {
                "message": "Video scan requires X/Y/Z soft home before continuous travel.",
                "homed": plan.get("homed", {}),
            },
        )
        return

    await emit("video_phase", {"phase": "prepare_video", "message": "Opening camera and loading YOLO model."})
    try:
        await asyncio.to_thread(camera_trigger.prepare_video_detection)
    except Exception as exc:
        await emit("video_error", {"message": f"Video detector is not ready: {exc}"})
        return

    loop = asyncio.get_running_loop()

    def ignore_send_result(future: asyncio.Future) -> None:
        try:
            future.exception()
        except Exception:
            pass

    def progress_callback(progress: dict[str, Any]) -> None:
        position = progress.get("position", {})
        segment = progress.get("segment", {})
        payload = video_position_payload(
            position,
            plan,
            segment=segment,
            progress={"done": segment.get("index", 0), "total": segment.get("total", plan["rows"]), "ratio": progress.get("progress", 0.0)},
        )
        camera_trigger.update_video_position(payload["x_mm"], payload["y_mm"], payload["area_mm"])
        future = asyncio.run_coroutine_threadsafe(emit("video_position", payload), loop)
        future.add_done_callback(ignore_send_result)

    async def on_detection(payload: dict[str, Any]) -> None:
        await emit("video_phase", {"phase": "detect_inference", "frame_id": payload.get("frame_id")})
        await emit("video_detection", payload)

    async def on_detection_error(payload: dict[str, Any]) -> None:
        await emit("video_error", payload)

    start_position = motor_controller.get_position()
    start_payload = video_position_payload(start_position, plan, segment={"type": "start", "index": 0, "total": plan["rows"]})
    camera_trigger.update_video_position(start_payload["x_mm"], start_payload["y_mm"], start_payload["area_mm"])
    await emit("video_position", start_payload)

    video_task = asyncio.create_task(
        camera_trigger.run_video_detection(
            scan_id,
            stop_event,
            inference_fps=float(plan["inference_fps"]),
            preview_fps=float(plan["preview_fps"]),
            on_detection=on_detection,
            on_error=on_detection_error,
        )
    )

    row_positions = [float(value) for value in plan["row_positions_mm"]]
    x_max = float(plan["travel_mm"]["x_max"])
    speed = float(plan["speed_mm_s"])
    total_segments = len(row_positions)

    try:
        for row_index, y_mm in enumerate(row_positions):
            if stop_event.is_set():
                await emit("video_stopped", {"message": "Video scan stopped before next raster line."})
                return

            target_x = x_max if row_index % 2 == 0 else 0.0
            current = motor_controller.get_position()
            if not math.isclose(float(current.get("y_mm", 0.0)), y_mm, abs_tol=0.001):
                await emit(
                    "video_phase",
                    {
                        "phase": "line_transition",
                        "row": row_index,
                        "x_mm": float(current.get("x_mm", 0.0)),
                        "y_mm": y_mm,
                    },
                )
                await asyncio.to_thread(
                    motor_controller.move_to_xy_stream,
                    float(current.get("x_mm", 0.0)),
                    y_mm,
                    speed,
                    progress_callback,
                    0.2,
                    {"type": "line_transition", "row": row_index, "index": row_index, "total": total_segments},
                )

            await emit(
                "video_phase",
                {
                    "phase": "continuous_move",
                    "row": row_index,
                    "target_x_mm": target_x,
                    "y_mm": y_mm,
                    "speed_mm_s": speed,
                },
            )
            await asyncio.to_thread(
                motor_controller.move_to_xy_stream,
                target_x,
                y_mm,
                speed,
                progress_callback,
                0.2,
                {"type": "raster_line", "row": row_index, "index": row_index + 1, "total": total_segments},
            )

        if stop_event.is_set():
            await emit("video_stopped", {"message": "Video scan stopped after final raster line."})
            return

        await emit("video_phase", {"phase": "return_origin", "x_mm": 0.0, "y_mm": 0.0})
        await asyncio.to_thread(
            motor_controller.move_to_xy_stream,
            0.0,
            0.0,
            speed,
            progress_callback,
            0.2,
            {"type": "return_origin", "row": None, "index": total_segments, "total": total_segments},
        )
        await emit(
            "video_complete",
            {
                "message": "Video scan complete. Origin restored.",
                "returned_to_origin": True,
                "origin_mm": {"x": 0.0, "y": 0.0},
            },
        )
    except Exception as exc:
        if stop_event.is_set():
            await emit("video_stopped", {"message": "Video scan stopped by request."})
        else:
            await emit("video_error", {"message": f"Video scan motion failed: {exc}"})
    finally:
        stop_event.set()
        motor_controller.request_stop()
        camera_trigger.stop_video_detection()
        try:
            await asyncio.wait_for(video_task, timeout=3.0)
        except asyncio.TimeoutError:
            video_task.cancel()
        except Exception:
            pass


async def run_handheld_detection(
    websocket: WebSocket,
    scan_id: str,
    stop_event: asyncio.Event,
    inference_fps: float,
    preview_fps: float,
) -> None:
    send_lock = asyncio.Lock()

    async def emit(packet_type: str, payload: dict[str, Any]) -> None:
        async with send_lock:
            await send_packet(websocket, packet_type, payload, scan_id)

    await emit(
        "handheld_phase",
        {
            "phase": "prepare_video",
            "message": "Opening camera and loading YOLO model for handheld detection.",
        },
    )
    try:
        await asyncio.to_thread(camera_trigger.prepare_video_detection)
    except Exception as exc:
        await emit("handheld_error", {"message": f"Handheld detector is not ready: {exc}"})
        return

    handheld_area = {
        "x": 0.0,
        "y": 0.0,
        "width": 0.0,
        "height": 0.0,
        "mode": "handheld",
        "unmapped": True,
        "message": "Handheld mode has no slide XY coordinate mapping.",
    }
    camera_trigger.update_video_position(0.0, 0.0, handheld_area)

    async def on_detection(payload: dict[str, Any]) -> None:
        handheld_payload = dict(payload)
        handheld_payload["operation_mode"] = "handheld"
        handheld_payload["coordinate_mode"] = "unmapped"
        handheld_payload["area_mm"] = dict(handheld_payload.get("area_mm") or handheld_area)
        handheld_payload["area_mm"]["unmapped"] = True
        await emit("handheld_phase", {"phase": "detect_inference", "frame_id": handheld_payload.get("frame_id")})
        await emit("handheld_detection", handheld_payload)

    async def on_detection_error(payload: dict[str, Any]) -> None:
        handheld_payload = dict(payload)
        handheld_payload["operation_mode"] = "handheld"
        await emit("handheld_error", handheld_payload)

    await emit(
        "handheld_phase",
        {
            "phase": "detect_inference",
            "message": "Handheld realtime detection is running.",
            "coordinate_mode": "unmapped",
        },
    )

    try:
        await camera_trigger.run_video_detection(
            scan_id,
            stop_event,
            inference_fps=inference_fps,
            preview_fps=preview_fps,
            on_detection=on_detection,
            on_error=on_detection_error,
        )
        await emit("handheld_stopped", {"message": "Handheld detection stopped."})
    except Exception as exc:
        if stop_event.is_set():
            await emit("handheld_stopped", {"message": "Handheld detection stopped by request."})
        else:
            await emit("handheld_error", {"message": f"Handheld detection failed: {exc}"})
    finally:
        stop_event.set()
        camera_trigger.stop_video_detection()


async def mock_capture_detect_and_read(
    row: int,
    col: int,
    x_mm: float,
    y_mm: float,
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    steps = 7
    for _ in range(steps):
        if stop_event.is_set():
            return {
                "ok": False,
                "error": "scan_stopped",
                "message": "Mock scan stopped before simulated result.",
                "row": row,
                "col": col,
                "x_mm": x_mm,
                "y_mm": y_mm,
            }
        await asyncio.sleep(MOCK_INFERENCE_SECONDS / steps)

    defects: list[dict[str, Any]] = []
    if (row + col) % 5 == 3:
        defects.append(
            {
                "type": "mock_solder_defect",
                "label": "模拟焊点异常",
                "bbox": [84, 72, 188, 158],
                "confidence": 0.86,
            }
        )

    return {
        "ok": True,
        "defects": defects,
        "result_json": "mock://no-hardware/result.json",
        "row": row,
        "col": col,
        "x_mm": x_mm,
        "y_mm": y_mm,
    }


def normalize_operation_mode(payload: dict[str, Any]) -> tuple[str, str]:
    capture_mode = str(payload.get("capture_mode", "")).strip().lower()
    operation_mode = str(payload.get("operation_mode", "")).strip().lower()
    aliases = {
        "camera": "handheld",
        "virtual": "handheld",
        "mock_camera": "handheld",
        "handheld": "handheld",
        "handheld_detection": "handheld",
        "simulation": "simulation",
        "simulate": "simulation",
        "image": "simulation",
        "real": "real",
        "hardware": "real",
        "video": "video",
        "video_detection": "video",
        "mock": "mock",
    }

    if not operation_mode:
        operation_mode = "mock" if capture_mode == "mock" else "real"
    operation_mode = aliases.get(operation_mode, operation_mode)
    if operation_mode not in OPERATION_MODES:
        raise ValueError("operation_mode must be handheld, simulation, real, video, or mock.")

    if capture_mode not in {"mock", "real"}:
        capture_mode = "mock" if operation_mode == "mock" else "real"
    if operation_mode in {"handheld", "simulation", "real"}:
        capture_mode = "real"
    return operation_mode, capture_mode


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/status")
async def api_system_status() -> dict[str, Any]:
    return system_status()


@app.get("/api/camera/status")
async def api_camera_status(probe: bool = Query(True)) -> dict[str, Any]:
    return camera_trigger.camera_status(probe=probe)


@app.get("/api/camera/stream.mjpg")
async def api_camera_stream() -> StreamingResponse:
    return StreamingResponse(
        camera_trigger.stream_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/video/status")
async def api_video_status() -> dict[str, Any]:
    return camera_trigger.video_status()


@app.get("/api/video/stream.mjpg")
async def api_video_stream() -> StreamingResponse:
    return StreamingResponse(
        camera_trigger.stream_video_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/api/simulation/upload")
async def api_simulation_upload(
    request: Request,
    filename: str = Query("pcb-image.jpg"),
) -> dict[str, Any]:
    try:
        content = await request.body()
        return camera_trigger.save_simulation_upload(filename, content)
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/artifact")
async def api_artifact(path: str = Query(...)) -> FileResponse:
    try:
        artifact = camera_trigger.resolve_artifact(path)
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(artifact)


@app.get("/api/network/status")
async def api_network_status() -> dict[str, Any]:
    return network_status()


@app.get("/api/grid/preview")
async def grid_preview(
    pcb_length_mm: float = Query(..., gt=0),
    pcb_width_mm: float = Query(..., gt=0),
    origin_x_mm: float = Query(0.0, ge=0),
    origin_y_mm: float = Query(0.0, ge=0),
) -> dict[str, Any]:
    try:
        return build_grid_plan(pcb_length_mm, pcb_width_mm, origin_x_mm, origin_y_mm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/ws/control")
async def websocket_control(websocket: WebSocket) -> None:
    global active_stop_event, active_scan_task

    await websocket.accept()
    await send_packet(websocket, "ack", {"message": "WebSocket connected."})

    try:
        while True:
            message = await websocket.receive_json()
            packet_type = message.get("type")
            payload = message.get("payload") or {}
            request_id = message.get("request_id")

            if packet_type == "ping":
                await send_packet(websocket, "ack", {"request_id": request_id, "message": "pong"})
                continue

            if packet_type == "home_all_axes":
                if active_scan_task and not active_scan_task.done():
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Cannot home axes while scan is running."})
                    continue
                motor_controller.home_all_axes()
                await send_packet(websocket, "ack", {"request_id": request_id, "message": "home_all_axes accepted."})
                continue

            if packet_type == "z_jog":
                if active_scan_task and not active_scan_task.done():
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Z axis is locked during automatic scan."})
                    continue
                direction = payload.get("direction")
                dist = payload.get("dist")
                if direction not in {"up", "down"} or not isinstance(dist, (int, float)) or dist <= 0:
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Invalid z_jog payload."})
                    continue
                motor_controller.move_z_step(direction, dist)
                await send_packet(websocket, "ack", {"request_id": request_id, "message": "z_jog accepted."})
                continue

            if packet_type == "stop_scan":
                if active_stop_event is not None and active_scan_task and not active_scan_task.done():
                    active_stop_event.set()
                    motor_controller.request_stop()
                    camera_trigger.stop_video_detection()
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "stop_scan accepted."})
                else:
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "No scan is running."})
                continue

            if packet_type == "stop_video_scan":
                if active_stop_event is not None and active_scan_task and not active_scan_task.done():
                    active_stop_event.set()
                    motor_controller.request_stop()
                    camera_trigger.stop_video_detection()
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "stop_video_scan accepted."})
                else:
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "No video scan is running."})
                continue

            if packet_type == "stop_handheld_detection":
                if active_stop_event is not None and active_scan_task and not active_scan_task.done():
                    active_stop_event.set()
                    camera_trigger.stop_video_detection()
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "stop_handheld_detection accepted."})
                else:
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "No handheld detection is running."})
                continue

            if packet_type == "start_handheld_detection":
                if scan_lock.locked() or (active_scan_task and not active_scan_task.done()):
                    await send_packet(websocket, "handheld_error", {"request_id": request_id, "message": "Another scan is already running."})
                    continue

                try:
                    inference_fps = float(payload.get("inference_fps", VIDEO_INFERENCE_FPS))
                    preview_fps = float(payload.get("preview_fps", VIDEO_PREVIEW_FPS))
                except (TypeError, ValueError) as exc:
                    await send_packet(websocket, "handheld_error", {"request_id": request_id, "message": str(exc)})
                    continue

                scan_id = str(uuid.uuid4())
                stop_event = asyncio.Event()
                active_stop_event = stop_event

                async def locked_handheld_detection() -> None:
                    global active_stop_event
                    async with scan_lock:
                        try:
                            await run_handheld_detection(websocket, scan_id, stop_event, inference_fps, preview_fps)
                        finally:
                            active_stop_event = None

                active_scan_task = asyncio.create_task(locked_handheld_detection())
                await send_packet(
                    websocket,
                    "ack",
                    {
                        "request_id": request_id,
                        "scan_id": scan_id,
                        "operation_mode": "handheld",
                        "message": "start_handheld_detection accepted.",
                    },
                )
                continue

            if packet_type == "start_video_scan":
                if scan_lock.locked() or (active_scan_task and not active_scan_task.done()):
                    await send_packet(websocket, "video_error", {"request_id": request_id, "message": "Another scan is already running."})
                    continue

                try:
                    plan = build_video_plan(
                        float(payload.get("speed_mm_s", VIDEO_SPEED_MM_S)),
                        float(payload.get("inference_fps", VIDEO_INFERENCE_FPS)),
                        float(payload.get("preview_fps", VIDEO_PREVIEW_FPS)),
                        float(payload.get("overlap", VIDEO_OVERLAP)),
                    )
                except (TypeError, ValueError) as exc:
                    await send_packet(websocket, "video_error", {"request_id": request_id, "message": str(exc)})
                    continue

                scan_id = str(uuid.uuid4())
                stop_event = asyncio.Event()
                active_stop_event = stop_event

                async def locked_video_scan() -> None:
                    global active_stop_event
                    async with scan_lock:
                        try:
                            await run_video_scan(websocket, scan_id, plan, stop_event)
                        finally:
                            active_stop_event = None

                active_scan_task = asyncio.create_task(locked_video_scan())
                await send_packet(
                    websocket,
                    "ack",
                    {
                        "request_id": request_id,
                        "scan_id": scan_id,
                        "operation_mode": "video",
                        "message": "start_video_scan accepted.",
                    },
                )
                continue

            if packet_type == "start_scan":
                if scan_lock.locked() or (active_scan_task and not active_scan_task.done()):
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Another scan is already running."})
                    continue

                try:
                    plan = build_grid_plan(
                        float(payload["pcb_length_mm"]),
                        float(payload["pcb_width_mm"]),
                        float(payload.get("origin_x_mm", 0.0)),
                        float(payload.get("origin_y_mm", 0.0)),
                    )
                    operation_mode, capture_mode = normalize_operation_mode(payload)
                    if operation_mode == "video":
                        raise ValueError("Use start_video_scan for continuous video detection mode.")
                    if operation_mode == "handheld":
                        raise ValueError("Use start_handheld_detection for handheld realtime detection mode.")
                except (KeyError, TypeError, ValueError) as exc:
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": str(exc)})
                    continue

                image_path = payload.get("image_path")
                scan_id = str(uuid.uuid4())
                stop_event = asyncio.Event()
                active_stop_event = stop_event

                async def locked_scan() -> None:
                    global active_stop_event
                    async with scan_lock:
                        try:
                            await run_scan(
                                websocket,
                                scan_id,
                                plan,
                                image_path,
                                capture_mode,
                                operation_mode,
                                stop_event,
                            )
                        finally:
                            active_stop_event = None

                active_scan_task = asyncio.create_task(locked_scan())
                await send_packet(
                    websocket,
                    "ack",
                    {
                        "request_id": request_id,
                        "scan_id": scan_id,
                        "operation_mode": operation_mode,
                        "message": "start_scan accepted.",
                    },
                )
                continue

            await send_packet(websocket, "scan_error", {"request_id": request_id, "message": f"Unknown packet type: {packet_type}"})

    except WebSocketDisconnect:
        if active_stop_event is not None:
            active_stop_event.set()
            motor_controller.request_stop()
            camera_trigger.stop_video_detection()
    finally:
        if active_scan_task and active_scan_task.done():
            active_scan_task = None
