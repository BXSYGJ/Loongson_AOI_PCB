import asyncio
import json
import math
import os
import platform
import random
import re
import socket
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import camera_trigger
from detection_router import CloudDetectionUnavailable, cloud_detection_client
import llm_chat
import llm_reporter
from local_dnn_detector import LocalDnnError, local_dnn_detector
import motor_controller


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

FOV_WIDTH_MM = 30.0
FOV_HEIGHT_MM = 30.0
MAX_X_MM = 135.0
MAX_Y_MM = 135.0
DEBOUNCE_SECONDS = float(os.getenv("LOONGSON_AOI_DEBOUNCE_SECONDS", "0.05"))
GRID_OVERLAP = float(os.getenv("LOONGSON_AOI_GRID_OVERLAP", "0.2"))
VIDEO_SPEED_MM_S = float(os.getenv("LOONGSON_AOI_VIDEO_SPEED_MM_S", "2.0"))
VIDEO_INFERENCE_FPS = float(os.getenv("LOONGSON_AOI_VIDEO_INFERENCE_FPS", "5.0"))
VIDEO_PREVIEW_FPS = float(os.getenv("LOONGSON_AOI_VIDEO_PREVIEW_FPS", "8.0"))
VIDEO_OVERLAP = float(os.getenv("LOONGSON_AOI_VIDEO_OVERLAP", "0.2"))
REAL_SCAN_MOTION_SPEED_MM_S = float(
    os.getenv("LOONGSON_AOI_REAL_SCAN_SPEED_MM_S", os.getenv("LOONGSON_AOI_MOTION_SPEED_MM_S", "3.0"))
)
MOCK_INFERENCE_SECONDS = 0.35
LAN_LISTEN_HOST = "0.0.0.0"
LAN_LISTEN_PORT = int(os.getenv("LOONGSON_AOI_LAN_PORT", os.getenv("PORT", "8000")))
OPERATION_MODES = {"simulation", "real", "phone", "mock"}
DETECTION_PLATFORMS = {"huawei_cloud", "phone_browser", "local_dnn"}
DEFAULT_DETECTION_PLATFORM = "huawei_cloud"
PHONE_EXPERIMENT_PROFILES = {"cloud_baseline", "phone_link_sim", "local_link_sim"}
PHONE_WORKER_TIMEOUT_SECONDS = float(os.getenv("LOONGSON_AOI_PHONE_WORKER_TIMEOUT_SECONDS", "20"))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value, 0)


def normalize_motion_speed(value: Any = None, default: float = REAL_SCAN_MOTION_SPEED_MM_S) -> float:
    speed = float(default if value is None or str(value).strip() == "" else value)
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("motion_speed_mm_s must be a positive millimetre-per-second value.")
    return round(max(0.05, min(speed, 25.0)), 4)


def normalize_settle_seconds(value: Any = None, default: float = DEBOUNCE_SECONDS) -> float:
    seconds = float(default if value is None or str(value).strip() == "" else value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("settle_seconds must be a non-negative second value.")
    return round(max(0.0, min(seconds, 10.0)), 4)


def _is_loongson_host() -> bool:
    return "loong" in platform.machine().lower()


EDGE_SAFE_MODE = _env_bool("LOONGSON_AOI_EDGE_SAFE_MODE", _is_loongson_host())
STATUS_CAMERA_PROBE = _env_bool("LOONGSON_AOI_STATUS_CAMERA_PROBE", not EDGE_SAFE_MODE)
PHONE_WORKER_ALLOW_DESKTOP = _env_bool("LOONGSON_AOI_ALLOW_DESKTOP_PHONE_WORKER", False)
REAL_SCAN_PRESET = os.getenv("LOONGSON_AOI_REAL_SCAN_PRESET", "fixed16").strip().lower()
FIXED_SCAN_STEP_X_MM = _env_float("LOONGSON_AOI_FIXED_SCAN_STEP_X_MM", 30.0)
FIXED_SCAN_STEP_Y_MM = _env_float("LOONGSON_AOI_FIXED_SCAN_STEP_Y_MM", 18.0)
FIXED_SCAN_IMAGE_WIDTH = _env_int("LOONGSON_AOI_CAMERA_WIDTH", 1280)
FIXED_SCAN_IMAGE_HEIGHT = _env_int("LOONGSON_AOI_CAMERA_HEIGHT", 800)
FIXED_SCAN_FOV_LONG_MM = _env_float("LOONGSON_AOI_CAMERA_FOV_LONG_MM", 39.0)
FIXED_SCAN_FOV_SHORT_MM = _env_float(
    "LOONGSON_AOI_CAMERA_FOV_SHORT_MM",
    FIXED_SCAN_FOV_LONG_MM * FIXED_SCAN_IMAGE_HEIGHT / max(FIXED_SCAN_IMAGE_WIDTH, 1),
)
FIXED_SCAN_AXIS_MAPPING = os.getenv(
    "LOONGSON_AOI_STITCH_AXIS_MAPPING",
    "slide_x_image_x_slide_y_image_y",
).strip().lower()
FIXED_SCAN_SETTLE_SECONDS = _env_float("LOONGSON_AOI_FIXED_SCAN_SETTLE_SECONDS", 1.0)
FIXED_SCAN_POINTS_DEFAULT = tuple(
    (float(col * FIXED_SCAN_STEP_X_MM), float(row * FIXED_SCAN_STEP_Y_MM))
    for row in range(4)
    for col in range(4)
)


def _is_phone_worker(info: dict[str, Any]) -> bool:
    if PHONE_WORKER_ALLOW_DESKTOP:
        return True
    user_agent = str(info.get("user_agent") or "").lower()
    return bool(re.search(r"android|iphone|ipad|ipod|mobile|harmonyos|honor|huawei|miuibrowser", user_agent))


app = FastAPI(title="Loongson AOI PCB Console", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

scan_lock = asyncio.Lock()
phone_detection_lock = asyncio.Lock()
phone_batch_tasks: dict[str, asyncio.Task] = {}
active_stop_event: asyncio.Event | None = None
active_scan_task: asyncio.Task | None = None


class RealScanRuntimeControl:
    def __init__(self, motion_speed_mm_s: float) -> None:
        self.motion_speed_mm_s = normalize_motion_speed(motion_speed_mm_s)
        self.updated_at = datetime.now().isoformat(timespec="seconds")

    def update_speed(self, motion_speed_mm_s: Any) -> float:
        self.motion_speed_mm_s = normalize_motion_speed(motion_speed_mm_s)
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.motion_speed_mm_s

    def current_speed(self) -> float:
        return float(self.motion_speed_mm_s)


active_real_scan_control: RealScanRuntimeControl | None = None


class PhoneInferenceUnavailable(RuntimeError):
    pass


class PhoneInferenceHub:
    def __init__(self) -> None:
        self._workers: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        ready_workers = [
            {
                "worker_id": worker_id,
                "ready": bool(info.get("ready")),
                "user_agent": info.get("user_agent"),
                "connected_at": info.get("connected_at"),
                "last_seen": info.get("last_seen"),
                "runtime": info.get("runtime") or {},
            }
            for worker_id, info in self._workers.items()
            if bool(info.get("ready")) and _is_phone_worker(info)
        ]
        ignored_desktop_workers = [
            worker_id
            for worker_id, info in self._workers.items()
            if bool(info.get("ready")) and not _is_phone_worker(info)
        ]
        return {
            "ready": bool(ready_workers),
            "worker_count": len(self._workers),
            "ready_worker_count": len(ready_workers),
            "ignored_desktop_worker_count": len(ignored_desktop_workers),
            "desktop_worker_allowed": PHONE_WORKER_ALLOW_DESKTOP,
            "pending_jobs": len(self._pending),
            "timeout_seconds": PHONE_WORKER_TIMEOUT_SECONDS,
            "workers": ready_workers,
            "message": "Phone browser inference worker is ready." if ready_workers else "No phone device inference worker is connected.",
        }

    async def detect_artifact(self, raw_image: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            ready_items = [
                (worker_id, info)
                for worker_id, info in self._workers.items()
                if bool(info.get("ready")) and _is_phone_worker(info)
            ]
            if not ready_items:
                raise PhoneInferenceUnavailable("no_phone_worker_online")
            worker_id, worker = ready_items[0]
            websocket: WebSocket = worker["websocket"]
            job_id = str(uuid.uuid4())
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending[job_id] = future
            payload = {
                "job_id": job_id,
                "raw_image": raw_image,
                "image_url": f"/api/artifact?path={raw_image}",
                "metadata": dict(metadata or {}),
                "requested_at": datetime.now().isoformat(timespec="seconds"),
            }
            try:
                await websocket.send_json({"type": "inference_request", "payload": payload})
            except Exception as exc:
                self._pending.pop(job_id, None)
                self._workers.pop(worker_id, None)
                raise PhoneInferenceUnavailable(f"phone_worker_send_failed: {exc}") from exc

        try:
            result = await asyncio.wait_for(future, timeout=PHONE_WORKER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            self._pending.pop(job_id, None)
            raise PhoneInferenceUnavailable("phone_worker_timeout") from exc
        if not result.get("ok", True):
            raise PhoneInferenceUnavailable(result.get("message") or result.get("error") or "phone_worker_failed")
        return result

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        worker_id = str(uuid.uuid4())
        self._workers[worker_id] = {
            "websocket": websocket,
            "ready": False,
            "connected_at": datetime.now().isoformat(timespec="seconds"),
            "last_seen": datetime.now().isoformat(timespec="seconds"),
        }
        await websocket.send_json({"type": "worker_registered", "payload": {"worker_id": worker_id, "runtime": phone_inference_runtime_config()}})
        try:
            while True:
                packet = await websocket.receive_json()
                packet_type = str(packet.get("type") or "")
                payload = packet.get("payload") or {}
                worker = self._workers.get(worker_id)
                if worker is not None:
                    worker["last_seen"] = datetime.now().isoformat(timespec="seconds")
                if packet_type in {"worker_hello", "worker_ready"}:
                    if worker is not None:
                        worker["ready"] = packet_type == "worker_ready" or bool(payload.get("ready", True))
                        worker["user_agent"] = payload.get("user_agent")
                        worker["runtime"] = payload.get("runtime") or {}
                    await websocket.send_json({"type": "worker_ack", "payload": {"worker_id": worker_id, "status": self.status()}})
                    continue
                if packet_type in {"inference_result", "inference_error"}:
                    job_id = str(payload.get("job_id") or "")
                    future = self._pending.pop(job_id, None)
                    if future is not None and not future.done():
                        if packet_type == "inference_error":
                            payload.setdefault("ok", False)
                        else:
                            payload.setdefault("ok", True)
                        future.set_result(payload)
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            self._workers.pop(worker_id, None)


phone_inference_hub: Any = PhoneInferenceHub()


def write_detection_outputs(
    raw_path: Path,
    annotated_path: Path,
    result_json_path: Path,
    detections: list[dict[str, Any]],
) -> None:
    cv2 = camera_trigger._import_cv2()
    image = cv2.imread(str(raw_path))
    if image is None:
        raise camera_trigger.CameraTriggerError(f"Could not read image for annotation: {raw_path}")
    for detection in detections:
        camera_trigger.draw_detection(
            image,
            [int(value) for value in detection.get("bbox", [0, 0, 1, 1])],
            str(detection.get("label") or detection.get("type") or "defect"),
            float(detection.get("confidence", 0.0)),
        )
    annotated_path.parent.mkdir(parents=True, exist_ok=True)
    result_json_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(annotated_path), image):
        raise camera_trigger.CameraTriggerError(f"Failed to write annotated image: {annotated_path}")
    result_json_path.write_text(json.dumps(detections, ensure_ascii=False, indent=2), encoding="utf-8")


def _platform_profile(metadata: dict[str, Any]) -> str:
    profile = str(metadata.get("detection_profile") or metadata.get("profile") or "").strip()
    if profile:
        return profile
    operation_mode = str(metadata.get("operation_mode") or "").strip()
    return "precision" if operation_mode == "phone" else "fast"


def normalize_detection_platform(value: Any = None) -> str:
    platform_value = str(value or DEFAULT_DETECTION_PLATFORM).strip().lower()
    aliases = {
        "cloud": "huawei_cloud",
        "huawei": "huawei_cloud",
        "huawei_cloud": "huawei_cloud",
        "ecs": "huawei_cloud",
        "phone": "phone_browser",
        "mobile": "phone_browser",
        "phone_browser": "phone_browser",
        "browser": "phone_browser",
        "local": "local_dnn",
        "local_dnn": "local_dnn",
        "opencv_dnn": "local_dnn",
        "windows": "local_dnn",
        "loongson": "local_dnn",
    }
    detection_platform = aliases.get(platform_value, platform_value)
    if detection_platform not in DETECTION_PLATFORMS:
        raise ValueError("detection_platform must be huawei_cloud, phone_browser, or local_dnn.")
    return detection_platform


def normalize_phone_experiment_profile(value: Any = None, legacy_platform: Any = None) -> str:
    profile_value = str(value or "").strip().lower()
    aliases = {
        "cloud_baseline": "cloud_baseline",
        "huawei_cloud": "cloud_baseline",
        "cloud": "cloud_baseline",
        "phone_link_sim": "phone_link_sim",
        "phone_browser": "phone_link_sim",
        "phone": "phone_link_sim",
        "local_link_sim": "local_link_sim",
        "local_dnn": "local_link_sim",
        "local": "local_link_sim",
    }
    if not profile_value:
        profile_value = str(legacy_platform or DEFAULT_DETECTION_PLATFORM).strip().lower()
    experiment_profile = aliases.get(profile_value, profile_value)
    if experiment_profile not in PHONE_EXPERIMENT_PROFILES:
        raise ValueError(
            "phone_experiment_profile must be cloud_baseline, phone_link_sim, or local_link_sim."
        )
    return experiment_profile


def phone_experiment_delay_seconds(
    experiment_profile: str,
    detection_profile: str,
    uniform_fn: Any = None,
) -> float:
    profile = normalize_phone_experiment_profile(experiment_profile)
    if profile == "cloud_baseline":
        return 0.0
    ranges = {
        "phone_link_sim": (2.5, 3.0),
        "local_link_sim": (4.5, 5.5),
    }
    lower, upper = ranges[profile]
    if str(detection_profile or "precision").strip().lower() == "fast":
        lower *= 0.5
        upper *= 0.5
    random_uniform = uniform_fn or random.uniform
    return round(float(random_uniform(lower, upper)), 3)


def host_platform_label() -> str:
    system = platform.system()
    machine = platform.machine()
    system_lower = system.lower()
    machine_lower = machine.lower()
    if "loong" in machine_lower:
        return "龙芯"
    if system_lower == "windows":
        return "Windows"
    if system_lower == "linux":
        return "Linux/ECS"
    return f"{system or 'Local'} {machine}".strip()


async def detect_artifact_platform_first(
    raw_image: str | Path,
    annotated_path: Path,
    result_json_path: Path,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_path = camera_trigger.resolve_artifact(raw_image)
    metadata = dict(metadata or {})
    detection_platform = normalize_detection_platform(metadata.get("detection_platform"))
    started = time.perf_counter()

    if detection_platform == "huawei_cloud":
        try:
            cloud_result = await asyncio.to_thread(
                cloud_detection_client.detect_file,
                raw_path,
                filename=raw_path.name,
                profile=_platform_profile(metadata),
                metadata=metadata,
            )
            detections = list(cloud_result.get("defects") or [])
            write_detection_outputs(raw_path, annotated_path, result_json_path, detections)
            inference_ms = float(cloud_result.get("inference_ms") or ((time.perf_counter() - started) * 1000.0))
            return {
                "ok": True,
                "defects": detections,
                "result_json": camera_trigger._rel(result_json_path),
                "raw_image": camera_trigger._rel(raw_path),
                "annotated_image": camera_trigger._rel(annotated_path),
                "inference_ms": round(inference_ms, 2),
                "inference_location": "huawei_cloud",
                "detection_platform": "huawei_cloud",
                "local_fallback": False,
                "cloud_job_id": cloud_result.get("cloud_job_id") or cloud_result.get("job_id"),
                "cloud_object_key": cloud_result.get("cloud_object_key") or cloud_result.get("object_key"),
                "cloud_raw_image_url": cloud_result.get("raw_image_url"),
                "cloud_annotated_image_url": cloud_result.get("annotated_image_url"),
                "cloud_result_json_url": cloud_result.get("result_json_url"),
                "detection_details": cloud_result.get("detection_details") or {},
            }
        except CloudDetectionUnavailable as exc:
            message = str(exc) or "Huawei cloud detection is not configured."
        except Exception as exc:
            message = f"Huawei cloud detection failed: {exc}"
        return {
            "ok": False,
            "error": "detection_platform_failed",
            "message": message,
            "raw_image": camera_trigger._rel(raw_path),
            "detection_platform": "huawei_cloud",
            "inference_location": "huawei_cloud",
        }

    if detection_platform == "phone_browser":
        try:
            if not phone_inference_hub.status().get("ready"):
                raise PhoneInferenceUnavailable("no_phone_worker_online")
            phone_result = await phone_inference_hub.detect_artifact(camera_trigger._rel(raw_path), metadata)
            detections = list(phone_result.get("defects") or [])
            write_detection_outputs(raw_path, annotated_path, result_json_path, detections)
            inference_ms = float(phone_result.get("inference_ms") or ((time.perf_counter() - started) * 1000.0))
            return {
                "ok": True,
                "defects": detections,
                "result_json": camera_trigger._rel(result_json_path),
                "raw_image": camera_trigger._rel(raw_path),
                "annotated_image": camera_trigger._rel(annotated_path),
                "inference_ms": round(inference_ms, 2),
                "inference_location": "phone_browser",
                "detection_platform": "phone_browser",
                "local_fallback": False,
                "phone_job_id": phone_result.get("job_id"),
                "detection_details": phone_result.get("detection_details") or {},
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": "detection_platform_failed",
                "message": f"Phone browser detection failed: {exc}",
                "raw_image": camera_trigger._rel(raw_path),
                "detection_platform": "phone_browser",
                "inference_location": "phone_browser",
            }

    try:
        return await asyncio.to_thread(local_dnn_detector.detect_image, raw_path, annotated_path, result_json_path)
    except (LocalDnnError, Exception) as exc:
        return {
            "ok": False,
            "error": "detection_platform_failed",
            "message": f"Local OpenCV DNN detection failed: {exc}",
            "raw_image": camera_trigger._rel(raw_path),
            "detection_platform": "local_dnn",
            "inference_location": "local_dnn",
        }


detect_artifact_phone_first = detect_artifact_platform_first


async def detect_phone_upload_selected_platform(
    upload_id: str,
    detection_profile: str | None = None,
    detection_platform: str | None = None,
    operation_mode: str = "real_browser_camera",
) -> dict[str, Any]:
    profile_config = camera_trigger.phone_detection_profile_config(detection_profile)
    selected_platform = normalize_detection_platform(detection_platform)
    metadata = camera_trigger.get_phone_upload(upload_id)
    raw_path = camera_trigger.resolve_artifact(metadata["raw_image"])
    upload_dir = raw_path.parent
    result = await detect_artifact_platform_first(
        metadata["raw_image"],
        upload_dir / "annotated.jpg",
        upload_dir / "result.json",
        {
            "operation_mode": operation_mode,
            "upload_id": upload_id,
            "detection_profile": profile_config["profile"],
            "detection_profile_label": profile_config["label"],
            "detection_platform": selected_platform,
        },
    )
    if not result.get("ok"):
        raise camera_trigger.CameraTriggerError(result.get("message") or "Uploaded image detection failed.")
    result["upload_id"] = upload_id
    result["detection_profile"] = profile_config["profile"]
    result["detection_profile_label"] = profile_config["label"]
    result["detection_platform"] = selected_platform
    result["operation_mode"] = operation_mode
    return camera_trigger.store_phone_detection_result(
        upload_id,
        result,
        profile_config["profile"],
        profile_config["label"],
    )


async def detect_phone_upload_phone_first(
    upload_id: str,
    detection_profile: str | None = None,
    detection_platform: str | None = None,
    phone_experiment_profile: str | None = None,
) -> dict[str, Any]:
    profile_config = camera_trigger.phone_detection_profile_config(detection_profile)
    experiment_profile = normalize_phone_experiment_profile(phone_experiment_profile, detection_platform)
    metadata = camera_trigger.get_phone_upload(upload_id)
    raw_path = camera_trigger.resolve_artifact(metadata["raw_image"])
    upload_dir = raw_path.parent
    started = time.perf_counter()
    result = await detect_artifact_platform_first(
        metadata["raw_image"],
        upload_dir / "annotated.jpg",
        upload_dir / "result.json",
        {
            "operation_mode": "phone",
            "upload_id": upload_id,
            "detection_profile": profile_config["profile"],
            "detection_profile_label": profile_config["label"],
            "detection_platform": "huawei_cloud",
            "phone_experiment_profile": experiment_profile,
        },
    )
    if not result.get("ok"):
        raise camera_trigger.CameraTriggerError(result.get("message") or "Phone upload detection failed.")
    result["upload_id"] = upload_id
    result["detection_profile"] = profile_config["profile"]
    result["detection_profile_label"] = profile_config["label"]
    cloud_inference_ms = float(result.get("inference_ms") or ((time.perf_counter() - started) * 1000.0))
    delay_seconds = phone_experiment_delay_seconds(experiment_profile, profile_config["profile"])
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    measured_total_ms = (time.perf_counter() - started) * 1000.0
    result["experiment_profile"] = experiment_profile
    result["executed_platform"] = "huawei_cloud"
    result["detection_platform"] = "huawei_cloud"
    result["inference_location"] = "huawei_cloud"
    result["cloud_inference_ms"] = round(cloud_inference_ms, 2)
    result["simulated_delay_ms"] = round(delay_seconds * 1000.0, 2)
    result["total_elapsed_ms"] = round(
        max(measured_total_ms, cloud_inference_ms + delay_seconds * 1000.0),
        2,
    )
    result["operation_mode"] = "phone"
    return camera_trigger.store_phone_detection_result(
        upload_id,
        result,
        profile_config["profile"],
        profile_config["label"],
    )


async def run_phone_batch_detection(
    batch_id: str,
    detection_profile: str = "precision",
    phone_experiment_profile: str = "cloud_baseline",
    *,
    detector: Any = None,
) -> dict[str, Any]:
    detector_fn = detector or detect_phone_upload_phone_first
    profile_config = camera_trigger.phone_detection_profile_config(detection_profile)
    experiment_profile = normalize_phone_experiment_profile(phone_experiment_profile, "huawei_cloud")
    camera_trigger.update_phone_batch(
        batch_id,
        status="queued",
        detection_profile=profile_config["profile"],
        detection_profile_label=profile_config["label"],
        experiment_profile=experiment_profile,
        error=None,
    )
    try:
        async with phone_detection_lock:
            camera_trigger.update_phone_batch(batch_id, status="detecting", error=None)
            batch = camera_trigger.get_phone_batch(batch_id)
            for item in batch.get("items") or []:
                upload_id = str(item.get("upload_id") or "")
                if not upload_id or item.get("status") == "detected":
                    continue
                camera_trigger.update_phone_upload_status(upload_id, "detecting", None)
                camera_trigger.update_phone_batch_item(batch_id, upload_id, status="detecting", error=None)
                try:
                    await detector_fn(
                        upload_id,
                        profile_config["profile"],
                        "huawei_cloud",
                        experiment_profile,
                    )
                    camera_trigger.update_phone_batch_item(batch_id, upload_id, status="detected", error=None)
                except asyncio.CancelledError:
                    camera_trigger.update_phone_upload_status(upload_id, "detection_error", "Batch detection interrupted.")
                    camera_trigger.update_phone_batch_item(
                        batch_id,
                        upload_id,
                        status="detection_error",
                        error="Batch detection interrupted.",
                    )
                    raise
                except Exception as exc:
                    camera_trigger.update_phone_upload_status(upload_id, "detection_error", str(exc))
                    camera_trigger.update_phone_batch_item(
                        batch_id,
                        upload_id,
                        status="detection_error",
                        error=str(exc),
                    )

            completed = camera_trigger.get_phone_batch(batch_id)
            final_status = "done" if completed.get("upload_count", 0) > 0 and completed.get("failed_count", 0) == 0 else "partial_failed"
            return camera_trigger.update_phone_batch(batch_id, status=final_status)
    except asyncio.CancelledError:
        camera_trigger.update_phone_batch(batch_id, status="interrupted", error="Batch detection interrupted.")
        raise
    except Exception as exc:
        return camera_trigger.update_phone_batch(batch_id, status="partial_failed", error=str(exc))


def _phone_batch_task_done(batch_id: str, task: asyncio.Task) -> None:
    current = phone_batch_tasks.get(batch_id)
    if current is task:
        phone_batch_tasks.pop(batch_id, None)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def phone_batch_runtime_status(batch_id: str) -> dict[str, Any]:
    batch = camera_trigger.get_phone_batch(batch_id)
    task = phone_batch_tasks.get(batch_id)
    if batch.get("status") in {"queued", "detecting"} and (task is None or task.done()):
        batch = camera_trigger.update_phone_batch(
            batch_id,
            status="interrupted",
            error="Batch detection was interrupted and can be resumed.",
        )
    batch["task_running"] = bool(task and not task.done())
    return batch


def start_phone_batch_detection(
    batch_id: str,
    detection_profile: str,
    phone_experiment_profile: str,
) -> dict[str, Any]:
    existing = phone_batch_tasks.get(batch_id)
    if existing is not None and not existing.done():
        raise camera_trigger.CameraTriggerError("Phone batch is already detecting.")
    batch = camera_trigger.get_phone_batch(batch_id)
    if not batch.get("items"):
        raise camera_trigger.CameraTriggerError("Phone batch has no uploaded images.")
    camera_trigger.update_phone_batch(
        batch_id,
        status="queued",
        detection_profile=detection_profile,
        experiment_profile=phone_experiment_profile,
        error=None,
    )
    task = asyncio.create_task(
        run_phone_batch_detection(batch_id, detection_profile, phone_experiment_profile),
        name=f"phone-batch-{batch_id}",
    )
    phone_batch_tasks[batch_id] = task
    task.add_done_callback(lambda completed, current_batch_id=batch_id: _phone_batch_task_done(current_batch_id, completed))
    return phone_batch_runtime_status(batch_id)


async def detect_frame_phone_first(
    frame: Any,
    annotated_path: Path,
    metadata: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Any, float, dict[str, Any]]:
    cv2 = camera_trigger._import_cv2()
    raw_path = camera_trigger.CAMERA_OUTPUT_DIR / "phone_worker_frame.jpg"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(raw_path), frame):
        raise camera_trigger.CameraTriggerError(f"Failed to write phone worker frame: {raw_path}")
    result = await detect_artifact_phone_first(
        camera_trigger._rel(raw_path),
        annotated_path,
        camera_trigger.VIDEO_LATEST_JSON,
        metadata or {},
    )
    annotated = cv2.imread(str(annotated_path))
    if annotated is None:
        annotated = frame.copy()
    extra = {
        "inference_location": result.get("inference_location"),
        "detection_platform": result.get("detection_platform"),
        "fallback_reason": result.get("fallback_reason"),
        "cloud_fallback_reason": result.get("cloud_fallback_reason"),
        "local_fallback": result.get("local_fallback"),
        "cloud_job_id": result.get("cloud_job_id"),
        "cloud_annotated_image_url": result.get("cloud_annotated_image_url"),
        "cloud_result_json_url": result.get("cloud_result_json_url"),
    }
    return list(result.get("defects") or []), annotated, float(result.get("inference_ms") or 0.0), extra


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
    detector_runtime = camera_trigger.detector_status()
    if detector_runtime.get("backend") == "onnx_opencv":
        return {
            "path": "OpenCV DNN ONNX runtime",
            "exists": True,
            "resolved": True,
            "message": "ONNX mode does not require algorithm/core/detect.py or ultralytics.",
        }
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


def phone_inference_runtime_config() -> dict[str, Any]:
    try:
        names, cn_names = camera_trigger.yolo_detector._load_label_maps()
    except Exception:
        names = {
            0: "dry_solder_joint",
            1: "improper_installation",
            2: "pcb_damage",
            3: "short_circuit",
            4: "insufficient_solder",
            5: "reverse_component",
            6: "excess_solder",
        }
        cn_names = {}
    runtime = camera_trigger.detector_status()
    return {
        "ok": True,
        "model_url": "/api/phone-inference/model",
        "backend": "onnxruntime-web",
        "imgsz": int(runtime.get("onnx_input_imgsz") or runtime.get("imgsz") or 640),
        "conf": float(runtime.get("conf") or 0.5),
        "names": {str(class_id): label for class_id, label in names.items()},
        "cn_names": cn_names,
        "weights_exists": bool(camera_trigger.DEFAULT_WEIGHTS.exists()),
        "weights_size_bytes": camera_trigger.DEFAULT_WEIGHTS.stat().st_size if camera_trigger.DEFAULT_WEIGHTS.exists() else 0,
        "timeout_seconds": PHONE_WORKER_TIMEOUT_SECONDS,
    }


def system_status(request: Request | None = None) -> dict[str, Any]:
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

    camera_runtime = camera_trigger.camera_status(probe=STATUS_CAMERA_PROBE)
    detector_runtime = camera_trigger.detector_status()
    cloud_status = cloud_detection_client.status()
    phone_status = phone_inference_hub.status()
    local_status = local_dnn_detector.status()
    host_label = host_platform_label()
    return {
        "motor": motor,
        "camera": {
            "runtime": camera_runtime,
            "capture_image": file_status(camera_trigger.DEFAULT_IMAGE),
            "status": "ready" if camera_runtime.get("opened") else "camera_unavailable",
        },
        "simulation": {
            "import_dir": str(camera_trigger.SIMULATION_IMPORT_DIR),
            "runtime": camera_trigger.simulation_runtime_config(),
        },
        "algorithm": {
            "detect_script": detect_script_status(),
            "weights": file_status(camera_trigger.DEFAULT_WEIGHTS),
            "config": file_status(camera_trigger.DEFAULT_CONFIG),
            "result_json": file_status(camera_trigger.RESULT_JSON),
            "runtime": detector_runtime,
        },
        "cloud_detection": cloud_status,
        "detection_priority": [DEFAULT_DETECTION_PLATFORM],
        "phone_inference": phone_status,
        "detection_platforms": {
            "default": DEFAULT_DETECTION_PLATFORM,
            "options": ["huawei_cloud", "phone_browser", "local_dnn"],
            "host_label": host_label,
            "edge_safe_mode": EDGE_SAFE_MODE,
            "status_camera_probe": STATUS_CAMERA_PROBE,
            "huawei_cloud": cloud_status,
            "phone_browser": phone_status,
            "local_dnn": local_status,
        },
        "llm_report": llm_reporter.status(),
        "scan": {
            "running": scan_running,
            "modes": ["simulation", "real", "phone"],
            "mode_hint": "simulation uses an uploaded image; real uses stop-and-shoot slide motion and camera capture; phone uses LAN image upload and whole-image detection.",
            "video": camera_trigger.video_status(),
        },
        "network": network_status(request),
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


def network_status(request: Request | None = None) -> dict[str, Any]:
    ips = local_ipv4_addresses()
    port = request.url.port if request is not None and request.url.port else LAN_LISTEN_PORT
    scheme = request.url.scheme if request is not None else "http"
    origins: list[str] = []
    if request is not None:
        request_origin = str(request.base_url).rstrip("/")
        if request_origin:
            origins.append(request_origin)
    origins.extend(f"{scheme}://{ip}:{port}" for ip in ips)
    origins = list(dict.fromkeys(origins))
    return {
        "listen_host": LAN_LISTEN_HOST,
        "port": port,
        "lan_ips": ips,
        "lan_urls": [f"{origin}/" for origin in origins],
        "phone_urls": [f"{origin}/phone" for origin in origins],
        "hint": f"Keep the phone and PC on the same LAN and allow TCP {port} through the firewall.",
    }


def _parse_fixed_scan_points() -> list[tuple[float, float]]:
    value = os.getenv("LOONGSON_AOI_FIXED_SCAN_POINTS", "").strip()
    if not value:
        return list(FIXED_SCAN_POINTS_DEFAULT)
    points: list[tuple[float, float]] = []
    for item in re.split(r"[;|]+", value):
        item = item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(",")]
        if len(parts) != 2:
            raise ValueError("LOONGSON_AOI_FIXED_SCAN_POINTS must use x,y;x,y format.")
        points.append((float(parts[0]), float(parts[1])))
    if not points:
        raise ValueError("LOONGSON_AOI_FIXED_SCAN_POINTS did not contain any usable points.")
    return points


def build_fixed_16_scan_plan(origin_x_mm: float = 0.0, origin_y_mm: float = 0.0) -> dict[str, Any]:
    if origin_x_mm < 0 or origin_y_mm < 0:
        raise ValueError("Origin coordinates must be non-negative.")
    points = _parse_fixed_scan_points()
    x_values = sorted({round(point[0], 4) for point in points})
    y_values = sorted({round(point[1], 4) for point in points})
    if len(points) != len(x_values) * len(y_values):
        raise ValueError("Fixed scan points must form a complete rectangular grid.")
    if not x_values or not y_values:
        raise ValueError("Fixed scan points are empty.")

    max_x = origin_x_mm + max(x_values)
    max_y = origin_y_mm + max(y_values)
    if max_x > MAX_X_MM or max_y > MAX_Y_MM:
        raise ValueError(
            f"Fixed scan points exceed slide travel: max_x={max_x:.2f}mm, max_y={max_y:.2f}mm, "
            f"limit={MAX_X_MM:.2f}x{MAX_Y_MM:.2f}mm."
        )

    x_to_col = {value: index for index, value in enumerate(x_values)}
    y_to_row = {value: index for index, value in enumerate(y_values)}
    point_set = {(round(float(x), 4), round(float(y), 4)) for x, y in points}
    scan_points: list[dict[str, Any]] = []
    for row_index, y in enumerate(y_values):
        x_order = x_values if row_index % 2 == 0 else list(reversed(x_values))
        for x in x_order:
            if (round(x, 4), round(y, 4)) not in point_set:
                raise ValueError("Fixed scan points must form a complete rectangular grid.")
            order = len(scan_points)
            x = round(float(x), 4)
            y = round(float(y), 4)
            scan_points.append(
                {
                    "order": order,
                    "row": y_to_row[round(y, 4)],
                    "col": x_to_col[round(x, 4)],
                    "x_mm": round(origin_x_mm + x, 4),
                    "y_mm": round(origin_y_mm + y, 4),
                }
            )

    rows = len(y_values)
    cols = len(x_values)
    covered_length = cols * FIXED_SCAN_STEP_X_MM
    covered_width = rows * FIXED_SCAN_STEP_Y_MM
    if FIXED_SCAN_AXIS_MAPPING == "slide_y_image_x_slide_x_image_y":
        fov_width_mm = FIXED_SCAN_FOV_SHORT_MM
        fov_height_mm = FIXED_SCAN_FOV_LONG_MM
    else:
        fov_width_mm = FIXED_SCAN_FOV_LONG_MM
        fov_height_mm = FIXED_SCAN_FOV_SHORT_MM
    return {
        "scan_type": "fixed_points_16",
        "pcb_size_known": False,
        "rows": rows,
        "cols": cols,
        "total_cells": len(scan_points),
        "scan_points": scan_points,
        "point_order": "y_major_horizontal_snake_fixed_points",
        "fov_mm": {
            "long": round(FIXED_SCAN_FOV_LONG_MM, 4),
            "short": round(FIXED_SCAN_FOV_SHORT_MM, 4),
            "width": round(fov_width_mm, 4),
            "height": round(fov_height_mm, 4),
            "image_width": round(FIXED_SCAN_FOV_LONG_MM, 4),
            "image_height": round(FIXED_SCAN_FOV_SHORT_MM, 4),
        },
        "step_mm": {"x": FIXED_SCAN_STEP_X_MM, "y": FIXED_SCAN_STEP_Y_MM},
        "image_px": {"width": FIXED_SCAN_IMAGE_WIDTH, "height": FIXED_SCAN_IMAGE_HEIGHT},
        "axis_mapping": FIXED_SCAN_AXIS_MAPPING,
        "origin_mm": {"x": origin_x_mm, "y": origin_y_mm},
        "covered_mm": {"length": round(covered_length, 4), "width": round(covered_width, 4)},
        "mosaic_mode": "physical_fov_crop_stitch",
        "settle_seconds": normalize_settle_seconds(FIXED_SCAN_SETTLE_SECONDS),
        "motion_speed_mm_s": normalize_motion_speed(),
        "z_policy": "fixed_during_scan",
        "logical_boundary": {
            "overflow_right_mm": 0.0,
            "overflow_bottom_mm": 0.0,
            "last_col_visible_ratio": 1.0,
            "last_row_visible_ratio": 1.0,
            "edge_cells_clipped": False,
        },
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

    overlap = max(0.0, min(0.85, GRID_OVERLAP))
    step_x = FOV_WIDTH_MM * (1.0 - overlap)
    step_y = FOV_HEIGHT_MM * (1.0 - overlap)

    def axis_count(size_mm: float, fov_mm: float, step_mm: float) -> int:
        if size_mm <= fov_mm:
            return 1
        return math.ceil((size_mm - fov_mm) / step_mm) + 1

    cols = axis_count(pcb_length_mm, FOV_WIDTH_MM, step_x)
    rows = axis_count(pcb_width_mm, FOV_HEIGHT_MM, step_y)
    covered_length = max(0, cols - 1) * step_x + FOV_WIDTH_MM
    covered_width = max(0, rows - 1) * step_y + FOV_HEIGHT_MM
    overflow_right = max(0.0, covered_length - pcb_length_mm)
    overflow_bottom = max(0.0, covered_width - pcb_width_mm)

    max_x = origin_x_mm + max(cols - 1, 0) * step_x
    max_y = origin_y_mm + max(rows - 1, 0) * step_y
    if max_x > MAX_X_MM or max_y > MAX_Y_MM:
        raise ValueError(
            f"Grid exceeds slide travel: max_x={max_x:.2f}mm, max_y={max_y:.2f}mm, "
            f"limit={MAX_X_MM:.2f}x{MAX_Y_MM:.2f}mm."
        )

    visible_last_col = (pcb_length_mm - (cols - 1) * step_x) / FOV_WIDTH_MM if cols else 1.0
    visible_last_row = (pcb_width_mm - (rows - 1) * step_y) / FOV_HEIGHT_MM if rows else 1.0

    return {
        "scan_type": "stepped_grid",
        "rows": rows,
        "cols": cols,
        "fov_mm": {"width": FOV_WIDTH_MM, "height": FOV_HEIGHT_MM},
        "step_mm": {"x": round(step_x, 4), "y": round(step_y, 4)},
        "overlap": round(overlap, 4),
        "pcb_mm": {"length": pcb_length_mm, "width": pcb_width_mm},
        "covered_mm": {"length": round(covered_length, 4), "width": round(covered_width, 4)},
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
    if plan.get("scan_points"):
        return [
            {
                "row": int(point["row"]),
                "col": int(point["col"]),
                "x_mm": float(point["x_mm"]),
                "y_mm": float(point["y_mm"]),
            }
            for point in plan["scan_points"]
        ]
    rows = int(plan["rows"])
    cols = int(plan["cols"])
    origin = plan["origin_mm"]
    step = plan.get("step_mm") or plan.get("fov_mm")
    step_x = float(step["x"] if "x" in step else step["width"])
    step_y = float(step["y"] if "y" in step else step["height"])
    cells: list[dict[str, float | int]] = []

    for row in range(rows):
        col_range = range(cols) if row % 2 == 0 else range(cols - 1, -1, -1)
        for col in col_range:
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "x_mm": round(float(origin["x"]) + col * step_x, 4),
                    "y_mm": round(float(origin["y"]) + row * step_y, 4),
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


async def run_real_scan_capture_queue(
    websocket: WebSocket,
    scan_id: str,
    plan: dict[str, Any],
    detection_platform: str,
    stop_event: asyncio.Event,
) -> None:
    cells = iter_snake_cells(plan)
    total = len(cells)
    settle_seconds = float(plan.get("settle_seconds", DEBOUNCE_SECONDS) or DEBOUNCE_SECONDS)
    control = active_real_scan_control or RealScanRuntimeControl(plan.get("motion_speed_mm_s"))
    motion_speed_mm_s = control.current_speed()

    def current_motion_speed() -> float:
        return (active_real_scan_control or control).current_speed()

    plan_payload = dict(plan)
    plan_payload["operation_mode"] = "real"
    plan_payload["detection_platform"] = detection_platform
    plan_payload["capture_pipeline"] = "capture_first_async_detection_queue"
    plan_payload["motion_speed_mm_s"] = motion_speed_mm_s

    send_lock = asyncio.Lock()

    async def emit(packet_type: str, payload: dict[str, Any]) -> None:
        async with send_lock:
            await send_packet(websocket, packet_type, payload, scan_id)

    await emit("grid_plan", plan_payload)

    manifest_cells: dict[str, dict[str, Any]] = {}

    def update_manifest_cell(row: int, col: int, data: dict[str, Any]) -> None:
        key = f"{row}:{col}"
        cell = manifest_cells.setdefault(
            key,
            {
                "row": row,
                "col": col,
                "x_mm": data.get("x_mm"),
                "y_mm": data.get("y_mm"),
                "status": "planned",
            },
        )
        cell.update({field: value for field, value in data.items() if value is not None})
        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            "real",
            list(manifest_cells.values()),
            status="running",
        )

    camera_trigger.write_scan_manifest(scan_id, plan_payload, "real", [], status="running")

    detection_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    queue_stats: dict[str, Any] = {
        "captured": 0,
        "completed": 0,
        "detected": 0,
        "errors": 0,
        "active": None,
    }

    def queue_payload() -> dict[str, Any]:
        pending = max(0, int(queue_stats["captured"]) - int(queue_stats["completed"]))
        active = queue_stats.get("active")
        return {
            "pending": pending,
            "captured": int(queue_stats["captured"]),
            "completed": int(queue_stats["completed"]),
            "detected": int(queue_stats["detected"]),
            "errors": int(queue_stats["errors"]),
            "total": total,
            "active": active,
            "idle": pending == 0 and active is None,
            "operation_mode": "real",
            "detection_platform": detection_platform,
        }

    async def emit_queue() -> None:
        await emit("detection_queue", queue_payload())

    async def detection_worker() -> None:
        while True:
            item = await detection_queue.get()
            if item is None:
                detection_queue.task_done()
                break

            row = int(item["row"])
            col = int(item["col"])
            x_mm = float(item["x_mm"])
            y_mm = float(item["y_mm"])
            capture = dict(item["capture"])
            queue_stats["active"] = {"row": row, "col": col}
            update_manifest_cell(row, col, {"x_mm": x_mm, "y_mm": y_mm, "status": "detecting"})
            await emit(
                "scan_phase",
                {
                    "phase": "detect_inference",
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "raw_image": capture.get("raw_image"),
                    "capture_ms": capture.get("capture_ms"),
                    "source": "camera",
                    "detection_platform": detection_platform,
                    "queue": queue_payload(),
                },
            )
            await emit_queue()

            try:
                result = await detect_artifact_phone_first(
                    capture["raw_image"],
                    camera_trigger.SCAN_OUTPUT_DIR / scan_id / f"r{row:03d}_c{col:03d}" / "annotated.jpg",
                    camera_trigger.SCAN_OUTPUT_DIR / scan_id / f"r{row:03d}_c{col:03d}" / "result.json",
                    {
                        "operation_mode": "real",
                        "scan_id": scan_id,
                        "row": row,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "capture_source": "camera",
                        "detection_platform": detection_platform,
                    },
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": "detect_exception",
                    "message": str(exc),
                    "raw_image": capture.get("raw_image"),
                    "detection_platform": detection_platform,
                    "inference_location": detection_platform,
                }

            queue_stats["completed"] = int(queue_stats["completed"]) + 1
            queue_stats["active"] = None

            if not result.get("ok"):
                queue_stats["errors"] = int(queue_stats["errors"]) + 1
                update_manifest_cell(
                    row,
                    col,
                    {
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "status": "detect_failed",
                        "raw_image": capture.get("raw_image"),
                        "capture_ms": capture.get("capture_ms"),
                        "error": result.get("error"),
                        "message": result.get("message"),
                        "detection_platform": detection_platform,
                    },
                )
                await emit(
                    "scan_phase",
                    {
                        "phase": "detect_failed",
                        "row": row,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "raw_image": capture.get("raw_image"),
                        "error": result.get("error"),
                        "message": result.get("message"),
                        "queue": queue_payload(),
                    },
                )
                detection_queue.task_done()
                await emit_queue()
                continue

            queue_stats["detected"] = int(queue_stats["detected"]) + 1
            result["row"] = row
            result["col"] = col
            result["x_mm"] = x_mm
            result["y_mm"] = y_mm
            result["capture_ms"] = capture.get("capture_ms")
            result["source_image"] = capture.get("source_image")
            result["simulation_crop"] = capture.get("simulation_crop")

            update_manifest_cell(
                row,
                col,
                {
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "status": "detected",
                    "raw_image": result.get("raw_image"),
                    "annotated_image": result.get("annotated_image"),
                    "result_json": result.get("result_json"),
                    "defects": result.get("defects", []),
                    "capture_ms": result.get("capture_ms"),
                    "inference_ms": result.get("inference_ms"),
                    "inference_location": result.get("inference_location"),
                    "detection_platform": result.get("detection_platform"),
                    "platform_priority": result.get("platform_priority"),
                    "fallback_reason": result.get("fallback_reason"),
                    "cloud_fallback_reason": result.get("cloud_fallback_reason"),
                    "local_fallback": result.get("local_fallback"),
                    "cloud_job_id": result.get("cloud_job_id"),
                    "cloud_annotated_image_url": result.get("cloud_annotated_image_url"),
                    "cloud_result_json_url": result.get("cloud_result_json_url"),
                },
            )

            await emit(
                "scan_result",
                {
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "phase": "result",
                    "progress": {"done": int(queue_stats["completed"]), "total": total},
                    "result_json": result["result_json"],
                    "defects": result["defects"],
                    "raw_image": result.get("raw_image"),
                    "annotated_image": result.get("annotated_image"),
                    "capture_ms": result.get("capture_ms"),
                    "inference_ms": result.get("inference_ms"),
                    "inference_location": result.get("inference_location"),
                    "detection_platform": result.get("detection_platform"),
                    "platform_priority": result.get("platform_priority"),
                    "fallback_reason": result.get("fallback_reason"),
                    "cloud_fallback_reason": result.get("cloud_fallback_reason"),
                    "local_fallback": result.get("local_fallback"),
                    "cloud_job_id": result.get("cloud_job_id"),
                    "cloud_annotated_image_url": result.get("cloud_annotated_image_url"),
                    "cloud_result_json_url": result.get("cloud_result_json_url"),
                    "operation_mode": "real",
                    "queue": queue_payload(),
                },
            )
            detection_queue.task_done()
            await emit_queue()

    worker_task = asyncio.create_task(detection_worker())
    stopped_early = False
    capture_failure: dict[str, Any] | None = None

    for index, cell in enumerate(cells, start=1):
        if stop_event.is_set():
            stopped_early = True
            break

        row = int(cell["row"])
        col = int(cell["col"])
        x_mm = float(cell["x_mm"])
        y_mm = float(cell["y_mm"])
        first_cell_capture = index == 1

        update_manifest_cell(
            row,
            col,
            {"order": index, "x_mm": x_mm, "y_mm": y_mm, "status": "capturing" if first_cell_capture else "moving"},
        )

        if first_cell_capture:
            move_speed = current_motion_speed()
            await emit(
                "scan_phase",
                {
                    "phase": "capture_first_frame",
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "operation_mode": "real",
                    "motion": "none",
                    "message": "首点先拍照，随后再进入滑台移动流程。",
                    "motion_speed_mm_s": move_speed,
                },
            )
        else:
            move_speed = current_motion_speed()
            await emit(
                "scan_phase",
                {
                    "phase": "move_xy",
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "operation_mode": "real",
                    "motion": "physical",
                    "motion_speed_mm_s": move_speed,
                },
            )
            try:
                motion_result = await asyncio.to_thread(motor_controller.move_to_xy_stream, x_mm, y_mm, move_speed)
            except Exception as exc:
                await emit(
                    "scan_error",
                    {
                        "message": f"XY move failed at row={row}, col={col}: {exc}",
                        "row": row,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                    },
                )
                stopped_early = True
                break

            motion_telemetry = dict(motion_result.get("motion_telemetry") or {})
            await emit(
                "motion_complete",
                {
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "requested_speed_mm_s": move_speed,
                    "position": {
                        "x_mm": motion_result.get("x_mm"),
                        "y_mm": motion_result.get("y_mm"),
                        "z_mm": motion_result.get("z_mm"),
                    },
                    "motion_telemetry": motion_telemetry,
                },
            )

            await emit(
                "scan_phase",
                {"phase": "debounce", "duration_ms": int(settle_seconds * 1000), "row": row, "col": col},
            )
            await asyncio.sleep(settle_seconds)

        if stop_event.is_set():
            stopped_early = True
            break

        await emit(
            "scan_phase",
            {
                "phase": "capture_frame",
                "row": row,
                "col": col,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "source": "camera",
                "queue": queue_payload(),
            },
        )
        capture = await camera_trigger.capture_frame(row, col, x_mm, y_mm, scan_id, stop_event)
        if not capture.get("ok"):
            update_manifest_cell(
                row,
                col,
                {
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "status": "capture_failed",
                    "error": capture.get("error"),
                    "message": capture.get("message"),
                },
            )
            capture_failure = capture
            stopped_early = True
            break

        queue_stats["captured"] = int(queue_stats["captured"]) + 1
        update_manifest_cell(
            row,
            col,
            {
                "x_mm": x_mm,
                "y_mm": y_mm,
                "status": "captured",
                "raw_image": capture.get("raw_image"),
                "latest_image": capture.get("latest_image"),
                "capture_ms": capture.get("capture_ms"),
            },
        )
        await emit(
            "scan_phase",
            {
                "phase": "capture_frame",
                "row": row,
                "col": col,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "raw_image": capture.get("raw_image"),
                "capture_ms": capture.get("capture_ms"),
                "source": "camera",
                "queued_for_detection": True,
                "queue": queue_payload(),
            },
        )
        await detection_queue.put(
            {
                "row": row,
                "col": col,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "capture": capture,
                "index": index,
            }
        )
        await emit_queue()
        await asyncio.sleep(0)

    origin = plan["origin_mm"]
    origin_x_mm = float(origin["x"])
    origin_y_mm = float(origin["y"])
    full_capture_completed = not capture_failure and not stopped_early and int(queue_stats["captured"]) >= total
    returned_to_origin = False
    return_origin_error: Exception | None = None

    if full_capture_completed:
        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            "real",
            list(manifest_cells.values()),
            status="captured_returning",
            extra={"detection_queue": queue_payload()},
        )
        await emit(
            "scan_phase",
            {
                "phase": "return_origin",
                "x_mm": origin_x_mm,
                "y_mm": origin_y_mm,
                "operation_mode": "real",
                "detection_platform": detection_platform,
                "motion": "physical",
                "motion_speed_mm_s": current_motion_speed(),
                "queue": queue_payload(),
                "message": "All frames captured. Returning to origin before final detection and mosaic.",
            },
        )
        try:
            return_result = await asyncio.to_thread(
                motor_controller.move_to_xy_stream, origin_x_mm, origin_y_mm, current_motion_speed()
            )
            returned_to_origin = True
            await emit(
                "motion_complete",
                {
                    "phase": "return_origin",
                    "x_mm": origin_x_mm,
                    "y_mm": origin_y_mm,
                    "requested_speed_mm_s": current_motion_speed(),
                    "position": {
                        "x_mm": return_result.get("x_mm"),
                        "y_mm": return_result.get("y_mm"),
                        "z_mm": return_result.get("z_mm"),
                    },
                    "motion_telemetry": dict(return_result.get("motion_telemetry") or {}),
                },
            )
            camera_trigger.write_scan_manifest(
                scan_id,
                plan_payload,
                "real",
                list(manifest_cells.values()),
                status="captured_returned",
                extra={"detection_queue": queue_payload(), "returned_to_origin": True},
            )
        except Exception as exc:
            return_origin_error = exc

    await emit(
        "scan_phase",
        {
            "phase": "drain_detection_queue",
            "operation_mode": "real",
            "cell_count": int(queue_stats["captured"]),
            "queue": queue_payload(),
        },
    )
    await detection_queue.join()
    await detection_queue.put(None)
    await detection_queue.join()
    await worker_task
    await emit_queue()

    if capture_failure:
        packet_type = "scan_stopped" if capture_failure.get("error") == "scan_stopped" else "scan_error"
        await emit(packet_type, capture_failure)
        return

    if stopped_early or int(queue_stats["captured"]) < total:
        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            "real",
            list(manifest_cells.values()),
            status="stopped",
        )
        await emit(
            "scan_stopped",
            {
                "message": "Scan stopped before all 16 capture points were completed.",
                "progress": {"done": int(queue_stats["captured"]), "total": total},
                "queue": queue_payload(),
            },
        )
        return

    if return_origin_error is not None:
        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            "real",
            list(manifest_cells.values()),
            status="return_origin_failed",
            extra={"return_origin_error": str(return_origin_error), "detection_queue": queue_payload()},
        )
        await emit(
            "scan_error",
            {
                "message": f"Return to scan origin failed: {return_origin_error}",
                "x_mm": origin_x_mm,
                "y_mm": origin_y_mm,
                "progress": {"done": int(queue_stats["captured"]), "total": total},
                "queue": queue_payload(),
            },
        )
        return

    mosaic_payload: dict[str, Any] | None = None
    skip_mosaic_detection = should_skip_mosaic_detection("real", detection_platform)
    await emit(
        "scan_phase",
        {"phase": "mosaic_stitching", "operation_mode": "real", "cell_count": len(manifest_cells), "queue": queue_payload()},
    )
    if not skip_mosaic_detection:
        await emit(
            "scan_phase",
            {"phase": "mosaic_detecting", "operation_mode": "real", "cell_count": len(manifest_cells)},
        )
    try:
        mosaic_payload = await asyncio.to_thread(camera_trigger.build_scan_mosaic, scan_id, False)
        mosaic_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id / "mosaic"
        if skip_mosaic_detection:
            mosaic_payload["mosaic_annotated"] = mosaic_payload.get("mosaic_annotated") or mosaic_payload.get("mosaic_raw")
            mosaic_payload["inference_ms"] = 0
            mosaic_payload["inference_location"] = "skipped"
            mosaic_payload["detection_platform"] = detection_platform
            mosaic_payload["mosaic_detection_skipped"] = True
            mosaic_payload["skip_reason"] = "real_mode_traceable_cell_fusion"
            await emit(
                "scan_phase",
                {
                    "phase": "mosaic_detection_skipped",
                    "operation_mode": "real",
                    "detection_platform": detection_platform,
                    "message": "真实模式使用可追溯的逐格融合结果，整板图仅用于展示和报告。",
                },
            )
        else:
            mosaic_detection = await detect_artifact_phone_first(
                mosaic_payload["mosaic_raw"],
                mosaic_dir / "mosaic_annotated.jpg",
                mosaic_dir / "mosaic_result.json",
                {
                    "operation_mode": "real",
                    "scan_id": scan_id,
                    "phase": "mosaic_detecting",
                    "detection_platform": detection_platform,
                },
            )
            if mosaic_detection.get("ok"):
                mosaic_payload["defects"] = mosaic_detection.get("defects", [])
                mosaic_payload["mosaic_annotated"] = mosaic_detection.get("annotated_image")
                mosaic_payload["mosaic_result_json"] = mosaic_detection.get("result_json")
                mosaic_payload["inference_ms"] = mosaic_detection.get("inference_ms")
                mosaic_payload["inference_location"] = mosaic_detection.get("inference_location")
                mosaic_payload["detection_platform"] = mosaic_detection.get("detection_platform")
                mosaic_payload["platform_priority"] = mosaic_detection.get("platform_priority")
                mosaic_payload["fallback_reason"] = mosaic_detection.get("fallback_reason")
                mosaic_payload["cloud_fallback_reason"] = mosaic_detection.get("cloud_fallback_reason")
                mosaic_payload["local_fallback"] = mosaic_detection.get("local_fallback")
                mosaic_payload["cloud_job_id"] = mosaic_detection.get("cloud_job_id")
                mosaic_payload["cloud_annotated_image_url"] = mosaic_detection.get("cloud_annotated_image_url")
                mosaic_payload["cloud_result_json_url"] = mosaic_detection.get("cloud_result_json_url")

        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            "real",
            list(manifest_cells.values()),
            status="complete",
            extra={"mosaic": mosaic_payload, "detection_queue": queue_payload()},
        )
        await emit("mosaic_result", mosaic_payload)
    except Exception as exc:
        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            "real",
            list(manifest_cells.values()),
            status="complete_with_mosaic_error",
            extra={"mosaic_error": str(exc), "detection_queue": queue_payload()},
        )
        await emit(
            "mosaic_error",
            {"message": f"Mosaic stitching or detection failed: {exc}", "operation_mode": "real"},
        )

    await emit(
        "scan_complete",
        {
            "message": "Scan complete. Origin restored.",
            "progress": {"done": int(queue_stats["completed"]), "total": total},
            "returned_to_origin": returned_to_origin,
            "origin_mm": {"x": origin_x_mm, "y": origin_y_mm},
            "operation_mode": "real",
            "detection_platform": detection_platform,
            "return_motion": "physical",
            "mosaic": mosaic_payload,
            "queue": queue_payload(),
            "detection_errors": int(queue_stats["errors"]),
        },
    )


async def run_scan(
    websocket: WebSocket,
    scan_id: str,
    plan: dict[str, Any],
    image_path: str | None,
    capture_mode: str,
    operation_mode: str,
    detection_platform: str,
    stop_event: asyncio.Event,
) -> None:
    if operation_mode == "real" and capture_mode != "mock":
        global active_real_scan_control
        active_real_scan_control = RealScanRuntimeControl(plan.get("motion_speed_mm_s"))
        try:
            await run_real_scan_capture_queue(websocket, scan_id, plan, detection_platform, stop_event)
        finally:
            active_real_scan_control = None
        return

    cells = iter_snake_cells(plan)
    total = len(cells)
    motion_enabled = operation_mode == "real"
    settle_seconds = float(plan.get("settle_seconds", DEBOUNCE_SECONDS) or DEBOUNCE_SECONDS)

    plan_payload = dict(plan)
    plan_payload["operation_mode"] = operation_mode
    plan_payload["detection_platform"] = detection_platform
    await send_packet(websocket, "grid_plan", plan_payload, scan_id)
    manifest_cells: dict[str, dict[str, Any]] = {}

    def update_manifest_cell(row: int, col: int, data: dict[str, Any]) -> None:
        key = f"{row}:{col}"
        cell = manifest_cells.setdefault(
            key,
            {
                "row": row,
                "col": col,
                "x_mm": data.get("x_mm"),
                "y_mm": data.get("y_mm"),
                "status": "planned",
            },
        )
        cell.update({key: value for key, value in data.items() if value is not None})
        camera_trigger.write_scan_manifest(
            scan_id,
            plan_payload,
            operation_mode,
            list(manifest_cells.values()),
            status="running",
        )

    camera_trigger.write_scan_manifest(scan_id, plan_payload, operation_mode, [], status="running")

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
        update_manifest_cell(row, col, {"order": index, "x_mm": x_mm, "y_mm": y_mm, "status": "moving"})

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
            {"phase": "debounce", "duration_ms": int(settle_seconds * 1000), "row": row, "col": col},
            scan_id,
        )
        await asyncio.sleep(settle_seconds)

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
                update_manifest_cell(
                    row,
                    col,
                    {
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "status": "capture_failed",
                        "error": capture.get("error"),
                        "message": capture.get("message"),
                    },
                )
                packet_type = "scan_stopped" if capture.get("error") == "scan_stopped" else "scan_error"
                await send_packet(websocket, packet_type, capture, scan_id)
                return

            update_manifest_cell(
                row,
                col,
                {
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "status": "captured",
                    "raw_image": capture.get("raw_image"),
                    "latest_image": capture.get("latest_image"),
                    "capture_ms": capture.get("capture_ms"),
                    "source_image": capture.get("source_image"),
                    "simulation_crop": capture.get("simulation_crop"),
                },
            )
            await send_packet(
                websocket,
                "scan_phase",
                {
                    "phase": "capture_frame",
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
                "detection_platform": detection_platform,
            },
            scan_id,
        )
            result = await detect_artifact_phone_first(
                capture["raw_image"],
                camera_trigger.SCAN_OUTPUT_DIR / scan_id / f"r{row:03d}_c{col:03d}" / "annotated.jpg",
                camera_trigger.SCAN_OUTPUT_DIR / scan_id / f"r{row:03d}_c{col:03d}" / "result.json",
                {
                    "operation_mode": operation_mode,
                    "scan_id": scan_id,
                    "row": row,
                    "col": col,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "capture_source": capture_source,
                    "detection_platform": detection_platform,
                },
            )
            if result.get("ok"):
                result["row"] = row
                result["col"] = col
                result["x_mm"] = x_mm
                result["y_mm"] = y_mm
                result["capture_ms"] = capture.get("capture_ms")
                result["source_image"] = capture.get("source_image")
                result["simulation_crop"] = capture.get("simulation_crop")

        if not result.get("ok"):
            update_manifest_cell(
                row,
                col,
                {
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "status": "detect_failed",
                    "error": result.get("error"),
                    "message": result.get("message"),
                },
            )
            packet_type = "scan_stopped" if result.get("error") == "scan_stopped" else "scan_error"
            await send_packet(websocket, packet_type, result, scan_id)
            return

        update_manifest_cell(
            row,
            col,
            {
                "x_mm": x_mm,
                "y_mm": y_mm,
                "status": "detected",
                "raw_image": result.get("raw_image"),
                "annotated_image": result.get("annotated_image"),
                "result_json": result.get("result_json"),
                "defects": result.get("defects", []),
                "capture_ms": result.get("capture_ms"),
                "inference_ms": result.get("inference_ms"),
                "inference_location": result.get("inference_location"),
                "detection_platform": result.get("detection_platform"),
                "platform_priority": result.get("platform_priority"),
                "fallback_reason": result.get("fallback_reason"),
                "cloud_fallback_reason": result.get("cloud_fallback_reason"),
                "local_fallback": result.get("local_fallback"),
                "cloud_job_id": result.get("cloud_job_id"),
                "cloud_annotated_image_url": result.get("cloud_annotated_image_url"),
                "cloud_result_json_url": result.get("cloud_result_json_url"),
                "source_image": result.get("source_image"),
                "simulation_crop": result.get("simulation_crop"),
            },
        )

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
                "inference_location": result.get("inference_location"),
                "detection_platform": result.get("detection_platform"),
                "platform_priority": result.get("platform_priority"),
                "fallback_reason": result.get("fallback_reason"),
                "cloud_fallback_reason": result.get("cloud_fallback_reason"),
                "local_fallback": result.get("local_fallback"),
                "cloud_job_id": result.get("cloud_job_id"),
                "cloud_annotated_image_url": result.get("cloud_annotated_image_url"),
                "cloud_result_json_url": result.get("cloud_result_json_url"),
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
                "detection_platform": detection_platform,
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

    mosaic_payload: dict[str, Any] | None = None
    if operation_mode in {"real", "simulation"}:
        skip_mosaic_detection = should_skip_mosaic_detection(operation_mode, detection_platform)
        await send_packet(
            websocket,
            "scan_phase",
            {"phase": "mosaic_stitching", "operation_mode": operation_mode, "cell_count": len(manifest_cells)},
            scan_id,
        )
        if not skip_mosaic_detection:
            await send_packet(
                websocket,
                "scan_phase",
                {"phase": "mosaic_detecting", "operation_mode": operation_mode, "cell_count": len(manifest_cells)},
                scan_id,
            )
        try:
            mosaic_payload = await asyncio.to_thread(camera_trigger.build_scan_mosaic, scan_id, False)
            mosaic_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id / "mosaic"
            if skip_mosaic_detection:
                mosaic_payload["mosaic_annotated"] = mosaic_payload.get("mosaic_annotated") or mosaic_payload.get("mosaic_raw")
                mosaic_payload["inference_ms"] = 0
                mosaic_payload["inference_location"] = "skipped"
                mosaic_payload["detection_platform"] = detection_platform
                mosaic_payload["mosaic_detection_skipped"] = True
                mosaic_payload["skip_reason"] = "real_mode_traceable_cell_fusion"
                await send_packet(
                    websocket,
                    "scan_phase",
                    {
                        "phase": "mosaic_detection_skipped",
                        "operation_mode": operation_mode,
                        "detection_platform": detection_platform,
                        "message": "真实模式使用可追溯的逐格融合结果，整板图仅用于展示和报告。",
                    },
                    scan_id,
                )
            else:
                mosaic_detection = await detect_artifact_phone_first(
                    mosaic_payload["mosaic_raw"],
                    mosaic_dir / "mosaic_annotated.jpg",
                    mosaic_dir / "mosaic_result.json",
                    {
                        "operation_mode": operation_mode,
                        "scan_id": scan_id,
                        "phase": "mosaic_detecting",
                        "detection_platform": detection_platform,
                    },
                )
                if mosaic_detection.get("ok"):
                    mosaic_payload["defects"] = mosaic_detection.get("defects", [])
                    mosaic_payload["mosaic_annotated"] = mosaic_detection.get("annotated_image")
                    mosaic_payload["mosaic_result_json"] = mosaic_detection.get("result_json")
                    mosaic_payload["inference_ms"] = mosaic_detection.get("inference_ms")
                    mosaic_payload["inference_location"] = mosaic_detection.get("inference_location")
                    mosaic_payload["detection_platform"] = mosaic_detection.get("detection_platform")
                    mosaic_payload["platform_priority"] = mosaic_detection.get("platform_priority")
                    mosaic_payload["fallback_reason"] = mosaic_detection.get("fallback_reason")
                    mosaic_payload["cloud_fallback_reason"] = mosaic_detection.get("cloud_fallback_reason")
                    mosaic_payload["local_fallback"] = mosaic_detection.get("local_fallback")
                    mosaic_payload["cloud_job_id"] = mosaic_detection.get("cloud_job_id")
                    mosaic_payload["cloud_annotated_image_url"] = mosaic_detection.get("cloud_annotated_image_url")
                    mosaic_payload["cloud_result_json_url"] = mosaic_detection.get("cloud_result_json_url")
            camera_trigger.write_scan_manifest(
                scan_id,
                plan_payload,
                operation_mode,
                list(manifest_cells.values()),
                status="complete",
                extra={"mosaic": mosaic_payload},
            )
            await send_packet(websocket, "mosaic_result", mosaic_payload, scan_id)
        except Exception as exc:
            camera_trigger.write_scan_manifest(
                scan_id,
                plan_payload,
                operation_mode,
                list(manifest_cells.values()),
                status="complete_with_mosaic_error",
                extra={"mosaic_error": str(exc)},
            )
            await send_packet(
                websocket,
                "mosaic_error",
                {"message": f"Mosaic stitching or detection failed: {exc}", "operation_mode": operation_mode},
                scan_id,
            )
    else:
        camera_trigger.write_scan_manifest(scan_id, plan_payload, operation_mode, list(manifest_cells.values()), status="complete")

    await send_packet(
        websocket,
        "scan_complete",
        {
            "message": "Scan complete. Origin restored.",
            "progress": {"done": total, "total": total},
            "returned_to_origin": True,
            "origin_mm": {"x": origin_x_mm, "y": origin_y_mm},
            "operation_mode": operation_mode,
            "detection_platform": detection_platform,
            "return_motion": "physical" if motion_enabled else "simulated",
            "mosaic": mosaic_payload,
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
            inference_handler=lambda frame, annotated_path: detect_frame_phone_first(
                frame,
                annotated_path,
                {"operation_mode": "video", "scan_id": scan_id},
            ),
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
            inference_handler=lambda frame, annotated_path: detect_frame_phone_first(
                frame,
                annotated_path,
                {"operation_mode": "handheld", "scan_id": scan_id},
            ),
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


async def run_phone_detection(
    websocket: WebSocket,
    scan_id: str,
    upload_id: str,
    stop_event: asyncio.Event,
    detection_profile: str = "precision",
    detection_platform: str = DEFAULT_DETECTION_PLATFORM,
    phone_experiment_profile: str | None = None,
) -> None:
    try:
        profile_config = camera_trigger.phone_detection_profile_config(detection_profile)
        experiment_profile = normalize_phone_experiment_profile(phone_experiment_profile, detection_platform)
        selected = await asyncio.to_thread(camera_trigger.get_phone_upload, upload_id)
        selected["operation_mode"] = "phone"
        selected["detection_profile"] = profile_config["profile"]
        selected["detection_profile_label"] = profile_config["label"]
        selected["detection_platform"] = "huawei_cloud"
        selected["phone_experiment_profile"] = experiment_profile
        await send_packet(websocket, "phone_upload_selected", selected, scan_id)
        if stop_event.is_set():
            await send_packet(
                websocket,
                "phone_error",
                {"upload_id": upload_id, "message": "Phone detection stopped before inference.", "operation_mode": "phone"},
                scan_id,
            )
            return
        await send_packet(
            websocket,
            "phone_detecting",
            {
                "upload_id": upload_id,
                "raw_image": selected.get("raw_image"),
                "operation_mode": "phone",
                "detection_profile": profile_config["profile"],
                "detection_profile_label": profile_config["label"],
                "detection_platform": "huawei_cloud",
                "phone_experiment_profile": experiment_profile,
                "message": f"Running whole-image YOLO inference ({profile_config['label']}).",
            },
            scan_id,
        )
        async with phone_detection_lock:
            result = await detect_phone_upload_phone_first(
                upload_id,
                profile_config["profile"],
                detection_platform,
                experiment_profile,
            )
        result["operation_mode"] = "phone"
        await send_packet(websocket, "phone_result", result, scan_id)
    except Exception as exc:
        await send_packet(
            websocket,
            "phone_error",
            {"upload_id": upload_id, "message": str(exc), "operation_mode": "phone"},
            scan_id,
        )


def should_skip_mosaic_detection(operation_mode: str, _detection_platform: str) -> bool:
    return operation_mode == "real"


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
        "camera": "real",
        "virtual": "mock",
        "mock_camera": "mock",
        "simulation": "simulation",
        "simulate": "simulation",
        "image": "simulation",
        "real": "real",
        "hardware": "real",
        "phone": "phone",
        "mobile": "phone",
        "mobile_upload": "phone",
        "mock": "mock",
    }

    if not operation_mode:
        operation_mode = "mock" if capture_mode == "mock" else "real"
    operation_mode = aliases.get(operation_mode, operation_mode)
    if operation_mode not in OPERATION_MODES:
        raise ValueError("operation_mode must be simulation, real, phone, or mock.")

    if capture_mode not in {"mock", "real"}:
        capture_mode = "mock" if operation_mode == "mock" else "real"
    if operation_mode in {"simulation", "real", "phone"}:
        capture_mode = "real"
    return operation_mode, capture_mode


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/phone")
async def phone_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "phone.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/status")
async def api_system_status(request: Request) -> dict[str, Any]:
    return system_status(request)


@app.get("/api/camera/status")
async def api_camera_status(probe: bool = Query(False)) -> dict[str, Any]:
    return camera_trigger.camera_status(probe=probe)


@app.post("/api/camera/source")
async def api_camera_source(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    try:
        return camera_trigger.set_camera_source(str(payload.get("source", "")))
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.get("/api/phone-inference/status")
async def api_phone_inference_status() -> dict[str, Any]:
    return {"ok": True, **phone_inference_hub.status()}


@app.get("/api/phone-inference/runtime")
async def api_phone_inference_runtime() -> dict[str, Any]:
    return phone_inference_runtime_config()


@app.get("/api/phone-inference/model")
async def api_phone_inference_model() -> FileResponse:
    if not camera_trigger.DEFAULT_WEIGHTS.exists():
        raise HTTPException(status_code=404, detail=f"ONNX model not found: {camera_trigger.DEFAULT_WEIGHTS}")
    return FileResponse(camera_trigger.DEFAULT_WEIGHTS, media_type="application/octet-stream", filename="best.onnx")


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


@app.post("/api/phone/upload")
async def api_phone_upload(
    request: Request,
    filename: str = Query("phone-pcb.jpg"),
    batch_id: str | None = Query(None),
    batch_index: int | None = Query(None),
) -> dict[str, Any]:
    try:
        content = await request.body()
        return camera_trigger.save_phone_upload(filename, content, batch_id=batch_id, batch_index=batch_index)
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/phone/uploads")
async def api_phone_uploads() -> dict[str, Any]:
    return {"ok": True, "uploads": camera_trigger.list_phone_uploads()}


@app.post("/api/phone/batches")
async def api_phone_batch_create(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    source = str(payload.get("source") or "web") if isinstance(payload, dict) else "web"
    upload_ids = payload.get("upload_ids") if isinstance(payload, dict) else None
    if upload_ids is not None and not isinstance(upload_ids, list):
        raise HTTPException(status_code=400, detail="upload_ids must be an array.")
    try:
        return camera_trigger.create_phone_batch(source, upload_ids=upload_ids)
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/phone/batches")
async def api_phone_batches() -> dict[str, Any]:
    batches = []
    for batch in camera_trigger.list_phone_batches():
        try:
            batches.append(phone_batch_runtime_status(str(batch["batch_id"])))
        except camera_trigger.CameraTriggerError:
            continue
    return {"ok": True, "batches": batches}


@app.get("/api/phone/batches/{batch_id}")
async def api_phone_batch_detail(batch_id: str) -> dict[str, Any]:
    try:
        return phone_batch_runtime_status(batch_id)
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/phone/batches/{batch_id}/upload-error")
async def api_phone_batch_upload_error(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
        return camera_trigger.record_phone_batch_upload_error(
            batch_id,
            batch_index=int(payload.get("batch_index", -1)),
            filename=str(payload.get("filename") or "upload.jpg"),
            error=str(payload.get("error") or "Upload failed."),
        )
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid upload error payload.") from exc


@app.post("/api/phone/batches/{batch_id}/detect", status_code=202)
async def api_phone_batch_detect(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    detection_profile = str(payload.get("detection_profile") or payload.get("profile") or "precision").strip()
    try:
        experiment_profile = normalize_phone_experiment_profile(
            payload.get("phone_experiment_profile"),
            payload.get("detection_platform") or "huawei_cloud",
        )
        return start_phone_batch_detection(batch_id, detection_profile, experiment_profile)
    except camera_trigger.CameraTriggerError as exc:
        status_code = 409 if "already detecting" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/phone/batches/{batch_id}")
async def api_phone_batch_delete(batch_id: str) -> dict[str, Any]:
    task = phone_batch_tasks.get(batch_id)
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail="Phone batch is currently detecting and cannot be deleted.")
    try:
        return camera_trigger.delete_phone_batch(batch_id)
    except camera_trigger.CameraTriggerError as exc:
        status_code = 409 if "currently detecting" in str(exc) else 404
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/api/phone/batches/{batch_id}/annotated.zip")
async def api_phone_batch_annotated_archive(batch_id: str) -> FileResponse:
    try:
        archive_path = camera_trigger.build_phone_batch_annotated_archive(batch_id)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"phone_batch_{batch_id}_annotated.zip",
        )
    except camera_trigger.CameraTriggerError as exc:
        message = str(exc)
        if "currently detecting" in message:
            status_code = 409
        elif "no successful annotated" in message:
            status_code = 404
        else:
            status_code = 404
        raise HTTPException(status_code=status_code, detail=message) from exc


@app.post("/api/phone/uploads/delete-selection")
async def api_phone_upload_delete_selection(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    upload_ids = payload.get("upload_ids") if isinstance(payload, dict) else None
    if not isinstance(upload_ids, list):
        raise HTTPException(status_code=400, detail="upload_ids must be an array.")
    try:
        return camera_trigger.delete_phone_uploads(upload_ids)
    except camera_trigger.CameraTriggerError as exc:
        message = str(exc)
        status_code = 409 if "currently detecting" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@app.post("/api/phone/detect")
async def api_phone_detect(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    upload_id = str(payload.get("upload_id", "")).strip()
    if not upload_id:
        raise HTTPException(status_code=400, detail="upload_id is required.")
    detection_profile = str(payload.get("detection_profile") or payload.get("profile") or "precision").strip()
    operation_mode = str(payload.get("operation_mode") or "phone").strip().lower()
    if operation_mode not in {"phone", "real_browser_camera"}:
        raise HTTPException(status_code=400, detail="operation_mode must be phone or real_browser_camera.")
    try:
        detection_platform = normalize_detection_platform(payload.get("detection_platform"))
        experiment_profile = normalize_phone_experiment_profile(
            payload.get("phone_experiment_profile"),
            detection_platform,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        async with phone_detection_lock:
            if operation_mode == "real_browser_camera":
                return await detect_phone_upload_selected_platform(
                    upload_id,
                    detection_profile,
                    detection_platform,
                    operation_mode,
                )
            return await detect_phone_upload_phone_first(
                upload_id,
                detection_profile,
                detection_platform,
                experiment_profile,
            )
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/phone/uploads/{upload_id}/annotated")
async def api_phone_annotated(upload_id: str) -> FileResponse:
    try:
        metadata = camera_trigger.get_phone_upload(upload_id)
        annotated_image = str(metadata.get("annotated_image") or "").strip()
        if not annotated_image:
            raise camera_trigger.CameraTriggerError("Phone upload has no annotated detection result.")
        annotated_path = camera_trigger.resolve_artifact(annotated_image)
        if not annotated_path.is_file():
            raise camera_trigger.CameraTriggerError("Phone annotated image does not exist.")
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_upload_id = re.sub(r"[^A-Za-z0-9_-]+", "_", upload_id).strip("_") or "phone"
    return FileResponse(
        annotated_path,
        media_type="image/jpeg",
        filename=f"{safe_upload_id}_annotated.jpg",
    )


@app.post("/api/llm/report")
async def api_llm_report(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    try:
        report = await asyncio.to_thread(llm_reporter.generate_report, payload)
        await asyncio.to_thread(llm_chat.append_report, payload, report)
        return report
    except llm_reporter.LlmReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_chat.LlmChatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/llm/chat/stream")
async def api_llm_chat_stream(request: Request) -> StreamingResponse:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    return StreamingResponse(
        llm_chat.stream_chat(payload),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/llm/chat/session")
async def api_llm_chat_session(
    mode: str = Query(...),
    scan_id: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    upload_id: str | None = Query(default=None),
) -> dict[str, Any]:
    payload = {
        "mode": mode,
        "scan_id": scan_id,
        "batch_id": batch_id,
        "upload_id": upload_id,
    }
    try:
        return await asyncio.to_thread(llm_chat.ensure_session, payload)
    except llm_chat.LlmChatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/llm/chat/download")
async def api_llm_chat_download(
    mode: str = Query(...),
    scan_id: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    upload_id: str | None = Query(default=None),
) -> FileResponse:
    payload = {
        "mode": mode,
        "scan_id": scan_id,
        "batch_id": batch_id,
        "upload_id": upload_id,
    }
    try:
        session = await asyncio.to_thread(llm_chat.ensure_session, payload)
        markdown_path, filename = await asyncio.to_thread(llm_chat.write_session_markdown, session)
    except llm_chat.LlmChatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        markdown_path,
        media_type="text/markdown; charset=utf-8",
        filename=filename,
    )


@app.get("/api/llm/report/download")
async def api_llm_report_download(
    target_type: str = Query(...),
    scan_id: str | None = Query(default=None),
    row: int | None = Query(default=None),
    col: int | None = Query(default=None),
    upload_id: str | None = Query(default=None),
) -> FileResponse:
    payload = {
        "target_type": target_type,
        "scan_id": scan_id,
        "row": row,
        "col": col,
        "upload_id": upload_id,
    }
    try:
        report_path, filename = llm_reporter.resolve_report_download(payload)
    except llm_reporter.LlmReportError as exc:
        status_code = 404 if "尚未生成" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return FileResponse(
        report_path,
        media_type="text/markdown; charset=utf-8",
        filename=filename,
    )


@app.delete("/api/phone/uploads/{upload_id}")
async def api_phone_delete(upload_id: str) -> dict[str, Any]:
    try:
        return camera_trigger.delete_phone_upload(upload_id)
    except camera_trigger.CameraTriggerError as exc:
        status_code = 409 if "currently detecting" in str(exc) else 404
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/api/artifact")
async def api_artifact(path: str = Query(...)) -> FileResponse:
    try:
        artifact = camera_trigger.resolve_artifact(path)
    except camera_trigger.CameraTriggerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(artifact)


@app.get("/api/network/status")
async def api_network_status(request: Request) -> dict[str, Any]:
    return network_status(request)


@app.websocket("/ws/phone-inference")
async def phone_inference_ws(websocket: WebSocket) -> None:
    await phone_inference_hub.connect(websocket)


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


@app.get("/api/grid/fixed16-preview")
async def fixed16_grid_preview(
    origin_x_mm: float = Query(0.0, ge=0),
    origin_y_mm: float = Query(0.0, ge=0),
) -> dict[str, Any]:
    try:
        return build_fixed_16_scan_plan(origin_x_mm, origin_y_mm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/ws/control")
async def websocket_control(websocket: WebSocket) -> None:
    global active_stop_event, active_scan_task, active_real_scan_control

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

            if packet_type == "update_real_scan_speed":
                if active_real_scan_control is None or not (active_scan_task and not active_scan_task.done()):
                    await send_packet(
                        websocket,
                        "scan_error",
                        {"request_id": request_id, "message": "No real scan is running; speed update ignored."},
                    )
                    continue
                try:
                    speed = active_real_scan_control.update_speed(payload.get("motion_speed_mm_s"))
                except (TypeError, ValueError) as exc:
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": str(exc)})
                    continue
                await send_packet(
                    websocket,
                    "real_scan_speed_updated",
                    {
                        "request_id": request_id,
                        "motion_speed_mm_s": speed,
                        "applies": "next_motion_segment",
                        "updated_at": active_real_scan_control.updated_at,
                    },
                )
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
                await send_packet(
                    websocket,
                    "handheld_error",
                    {
                        "request_id": request_id,
                        "message": "手持实时检测模式已移除；请使用手机拍照模式或真实模式相机预览。",
                    },
                )
                continue

            if packet_type == "start_phone_detection":
                if scan_lock.locked() or (active_scan_task and not active_scan_task.done()):
                    await send_packet(websocket, "phone_error", {"request_id": request_id, "message": "Another scan is already running."})
                    continue

                upload_id = str(payload.get("upload_id", "")).strip()
                if not upload_id:
                    await send_packet(websocket, "phone_error", {"request_id": request_id, "message": "upload_id is required."})
                    continue
                detection_profile = str(payload.get("detection_profile") or payload.get("profile") or "precision").strip()
                try:
                    detection_platform = normalize_detection_platform(payload.get("detection_platform"))
                    experiment_profile = normalize_phone_experiment_profile(
                        payload.get("phone_experiment_profile"),
                        detection_platform,
                    )
                except ValueError as exc:
                    await send_packet(websocket, "phone_error", {"request_id": request_id, "message": str(exc)})
                    continue

                scan_id = str(uuid.uuid4())
                stop_event = asyncio.Event()
                active_stop_event = stop_event

                async def locked_phone_detection() -> None:
                    global active_stop_event
                    async with scan_lock:
                        try:
                            await run_phone_detection(
                                websocket,
                                scan_id,
                                upload_id,
                                stop_event,
                                detection_profile,
                                detection_platform,
                                experiment_profile,
                            )
                        finally:
                            active_stop_event = None

                active_scan_task = asyncio.create_task(locked_phone_detection())
                await send_packet(
                    websocket,
                    "ack",
                    {
                        "request_id": request_id,
                        "scan_id": scan_id,
                        "operation_mode": "phone",
                        "detection_platform": "huawei_cloud",
                        "phone_experiment_profile": experiment_profile,
                        "message": "start_phone_detection accepted.",
                    },
                )
                continue

            if packet_type == "start_video_scan":
                await send_packet(
                    websocket,
                    "video_error",
                    {
                        "request_id": request_id,
                        "message": "连续视频检测模式已从 UI 主流程移除；真实模式仍保留实时相机预览。",
                    },
                )
                continue

            if packet_type == "start_scan":
                if scan_lock.locked() or (active_scan_task and not active_scan_task.done()):
                    await send_packet(websocket, "scan_error", {"request_id": request_id, "message": "Another scan is already running."})
                    continue

                try:
                    operation_mode, capture_mode = normalize_operation_mode(payload)
                    if operation_mode == "phone":
                        raise ValueError("Use start_phone_detection for phone upload detection mode.")
                    origin_x_mm = float(payload.get("origin_x_mm", 0.0))
                    origin_y_mm = float(payload.get("origin_y_mm", 0.0))
                    if operation_mode == "real" and REAL_SCAN_PRESET == "fixed16":
                        plan = build_fixed_16_scan_plan(origin_x_mm, origin_y_mm)
                    else:
                        plan = build_grid_plan(
                            float(payload["pcb_length_mm"]),
                            float(payload["pcb_width_mm"]),
                            origin_x_mm,
                            origin_y_mm,
                        )
                    if operation_mode == "real":
                        plan["motion_speed_mm_s"] = normalize_motion_speed(
                            payload.get("motion_speed_mm_s", plan.get("motion_speed_mm_s"))
                        )
                        plan["settle_seconds"] = normalize_settle_seconds(
                            payload.get("settle_seconds", plan.get("settle_seconds"))
                        )
                    detection_platform = normalize_detection_platform(payload.get("detection_platform"))
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
                                detection_platform,
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
                        "detection_platform": detection_platform,
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
