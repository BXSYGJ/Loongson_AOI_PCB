import asyncio
import math
import platform
import re
import socket
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import camera_trigger
import motor_controller


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

FOV_WIDTH_MM = 30.0
FOV_HEIGHT_MM = 30.0
MAX_X_MM = 135.0
MAX_Y_MM = 135.0
DEBOUNCE_SECONDS = 0.05
MOCK_INFERENCE_SECONDS = 0.35
LAN_LISTEN_HOST = "0.0.0.0"
LAN_LISTEN_PORT = 8000


app = FastAPI(title="Loongson AOI PCB Console", version="0.1.0")
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
    return {
        "motor": {
            "driver_mode": "mock_stub",
            "connected": False,
            "status": "未接入真实滑台驱动，当前仅执行 motor_controller.py 的 print 桩函数。",
            "interfaces": ["home_all_axes", "move_z_step", "move_to_xy"],
        },
        "camera": {
            "capture_image": file_status(camera_trigger.DEFAULT_IMAGE),
            "status": "ready" if camera_trigger.DEFAULT_IMAGE.exists() else "missing_capture_image",
        },
        "algorithm": {
            "detect_script": detect_script_status(),
            "weights": file_status(camera_trigger.DEFAULT_WEIGHTS),
            "result_json": file_status(camera_trigger.RESULT_JSON),
        },
        "scan": {
            "running": scan_running,
            "mode_hint": "Mock联调模式用于未接滑台/相机阶段；关闭后会要求真实抓拍图片和 YOLO 权重就绪。",
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
        if not ip.startswith("127.")
        and not ip.startswith("169.254.")
        and ip != "0.0.0.0"
    )


def platform_ipv4_addresses() -> set[str]:
    system = platform.system().lower()
    if system == "windows":
        command = ["ipconfig"]
    else:
        command = ["hostname", "-I"]

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
        "hint": "手机和电脑需连接同一局域网；如果打不开，请检查防火墙是否放行 TCP 8000。",
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

    visible_last_col = (
        (pcb_length_mm - (cols - 1) * FOV_WIDTH_MM) / FOV_WIDTH_MM if cols else 1.0
    )
    visible_last_row = (
        (pcb_width_mm - (rows - 1) * FOV_HEIGHT_MM) / FOV_HEIGHT_MM if rows else 1.0
    )

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
    stop_event: asyncio.Event,
) -> None:
    cells = iter_snake_cells(plan)
    total = len(cells)

    await send_packet(websocket, "grid_plan", plan, scan_id)

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
            {"phase": "move_xy", "row": row, "col": col, "x_mm": x_mm, "y_mm": y_mm},
            scan_id,
        )
        # TODO: 岗位二同学在此注入物理驱动
        motor_controller.move_to_xy(x_mm, y_mm)

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
                {"message": "Scan stopped after 50ms debounce.", "progress": {"done": index - 1, "total": total}},
                scan_id,
            )
            return

        await send_packet(
            websocket,
            "scan_phase",
            {"phase": "capture_inference", "row": row, "col": col, "x_mm": x_mm, "y_mm": y_mm},
            scan_id,
        )
        if capture_mode == "mock":
            result = await mock_capture_detect_and_read(row, col, x_mm, y_mm, stop_event)
        else:
            result = await camera_trigger.capture_detect_and_read(row, col, x_mm, y_mm, image_path, stop_event)

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
            },
            scan_id,
        )

    await send_packet(websocket, "scan_complete", {"message": "Scan complete.", "progress": {"done": total, "total": total}}, scan_id)


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
                "label": "Mock焊点异常",
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
                # TODO: 岗位二同学在此注入物理驱动
                motor_controller.home_all_axes()
                await send_packet(websocket, "ack", {"request_id": request_id, "message": "home_all_axes accepted."})
                continue

            if packet_type == "z_jog":
                if active_scan_task and not active_scan_task.done():
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Z axis is locked during automatic blind scan."})
                    continue
                direction = payload.get("direction")
                dist = payload.get("dist")
                if direction not in {"up", "down"} or not isinstance(dist, (int, float)) or dist <= 0:
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Invalid z_jog payload."})
                    continue
                # TODO: 岗位二同学在此注入物理驱动
                motor_controller.move_z_step(direction, dist)
                await send_packet(websocket, "ack", {"request_id": request_id, "message": "z_jog accepted."})
                continue

            if packet_type == "stop_scan":
                if active_stop_event is not None and active_scan_task and not active_scan_task.done():
                    active_stop_event.set()
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "stop_scan accepted."})
                else:
                    await send_packet(websocket, "ack", {"request_id": request_id, "message": "当前没有正在运行的盲扫任务。"})
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
                except (KeyError, TypeError, ValueError) as exc:
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": str(exc)})
                    continue

                image_path = payload.get("image_path")
                capture_mode = str(payload.get("capture_mode", "mock")).lower()
                if capture_mode not in {"mock", "real"}:
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "capture_mode must be mock or real."})
                    continue
                scan_id = str(uuid.uuid4())
                stop_event = asyncio.Event()
                active_stop_event = stop_event

                async def locked_scan() -> None:
                    global active_stop_event
                    async with scan_lock:
                        try:
                            await run_scan(websocket, scan_id, plan, image_path, capture_mode, stop_event)
                        finally:
                            active_stop_event = None

                active_scan_task = asyncio.create_task(locked_scan())
                await send_packet(websocket, "ack", {"request_id": request_id, "scan_id": scan_id, "message": "start_scan accepted."})
                continue

            await send_packet(websocket, "scan_error", {"request_id": request_id, "message": f"Unknown packet type: {packet_type}"})

    except WebSocketDisconnect:
        if active_stop_event is not None:
            active_stop_event.set()
    finally:
        if active_scan_task and active_scan_task.done():
            active_scan_task = None
