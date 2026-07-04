import asyncio
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable


ROOT = Path(__file__).resolve().parent


class CameraTriggerError(RuntimeError):
    pass


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value, 0)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _is_algorithm_root(path: Path) -> bool:
    return (path / "algorithm" / "core" / "detect.py").exists() or (
        path / "algorithm" / "configs" / "pcb_defects.yaml"
    ).exists()


def resolve_algorithm_root() -> Path:
    env_root = os.getenv("LOONGSON_AOI_ALGORITHM_ROOT")
    candidates: list[Path] = []
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend(
        [
            ROOT,
            ROOT / "worktrees" / "algorithm-ai",
            ROOT.parent / "algorithm-ai",
            ROOT.parent / "worktrees" / "algorithm-ai",
        ]
    )

    for candidate in candidates:
        resolved = candidate.resolve()
        if _is_algorithm_root(resolved):
            return resolved
    return candidates[0].resolve() if candidates else ROOT


ALGORITHM_ROOT = resolve_algorithm_root()
CAMERA_OUTPUT_DIR = ALGORITHM_ROOT / "algorithm" / "outputs" / "camera"
DETECTION_OUTPUT_DIR = ALGORITHM_ROOT / "algorithm" / "outputs" / "detections"
SCAN_OUTPUT_DIR = ALGORITHM_ROOT / "algorithm" / "outputs" / "scans"
SIMULATION_OUTPUT_DIR = ALGORITHM_ROOT / "algorithm" / "outputs" / "simulation"
SIMULATION_IMPORT_DIR = SIMULATION_OUTPUT_DIR / "imports"
RESULT_JSON = DETECTION_OUTPUT_DIR / "result.json"
RESULT_IMAGE = DETECTION_OUTPUT_DIR / "result.jpg"
DEFAULT_IMAGE = CAMERA_OUTPUT_DIR / "latest.jpg"
VIDEO_LATEST_IMAGE = CAMERA_OUTPUT_DIR / "video_latest.jpg"
VIDEO_LATEST_JSON = CAMERA_OUTPUT_DIR / "video_latest.json"
DEFAULT_CONFIG = ALGORITHM_ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
DEFAULT_WEIGHTS = Path(
    os.getenv(
        "LOONGSON_AOI_WEIGHTS",
        str(ALGORITHM_ROOT / "runs" / "detect" / "pcb_defects" / "weights" / "best.pt"),
    )
).expanduser()

SIMULATION_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_DEFAULT_INFERENCE_FPS = _env_float("LOONGSON_AOI_VIDEO_INFERENCE_FPS", 5.0)
VIDEO_DEFAULT_PREVIEW_FPS = _env_float("LOONGSON_AOI_VIDEO_PREVIEW_FPS", 8.0)
CAMERA_SOURCE_FILE = Path(os.getenv("LOONGSON_AOI_CAMERA_SOURCE_FILE", ROOT / "camera_source.txt")).expanduser()
DEFAULT_CAMERA_SOURCE = "1"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ALGORITHM_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _safe_filename(filename: str) -> str:
    source = Path(filename or "pcb-image").name
    suffix = Path(source).suffix.lower()
    stem = Path(source).stem
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", stem).strip("._-") or "pcb-image"
    return f"{stem}{suffix}"


def parse_source(source: str) -> int | str:
    return int(source) if source.isdigit() else source


def resolve_camera_source() -> dict[str, str]:
    env_source = os.getenv("LOONGSON_AOI_CAMERA_SOURCE")
    if env_source is not None and env_source.strip() != "":
        return {"source": env_source.strip(), "origin": "env", "config_file": str(CAMERA_SOURCE_FILE)}

    if CAMERA_SOURCE_FILE.exists():
        try:
            for line in CAMERA_SOURCE_FILE.read_text(encoding="utf-8").splitlines():
                value = line.strip()
                if value and not value.startswith("#"):
                    return {"source": value, "origin": "file", "config_file": str(CAMERA_SOURCE_FILE)}
        except OSError:
            pass

    return {"source": DEFAULT_CAMERA_SOURCE, "origin": "default", "config_file": str(CAMERA_SOURCE_FILE)}


def _camera_backend_code(cv2: Any, source: int | str) -> int | None:
    backend = os.getenv("LOONGSON_AOI_CAMERA_BACKEND", "").strip().upper()
    if backend in {"", "AUTO"}:
        if isinstance(source, str) and source.startswith("/dev/video"):
            return cv2.CAP_V4L2
        if sys.platform.startswith("win") and isinstance(source, int):
            return cv2.CAP_DSHOW
        return None

    backends = {
        "ANY": cv2.CAP_ANY,
        "DSHOW": cv2.CAP_DSHOW,
        "MSMF": cv2.CAP_MSMF,
        "V4L2": cv2.CAP_V4L2,
    }
    if backend not in backends:
        raise CameraTriggerError(f"Unsupported LOONGSON_AOI_CAMERA_BACKEND: {backend}")
    return backends[backend]


def resolve_detect_script() -> Path:
    preferred = ALGORITHM_ROOT / "algorithm" / "scripts" / "detect.py"
    fallback = ALGORITHM_ROOT / "algorithm" / "core" / "detect.py"
    if preferred.exists():
        return preferred
    if fallback.exists():
        return fallback
    raise CameraTriggerError("No YOLO detect script found in algorithm/scripts or algorithm/core.")


def _import_cv2():
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CameraTriggerError("OpenCV/cv2 is not installed in this Python environment.") from exc
    return cv2


def _normalize_frame(frame: Any) -> Any:
    cv2 = _import_cv2()
    if frame is None:
        return frame
    if len(frame.shape) == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if len(frame.shape) == 3 and frame.shape[2] == 1:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if len(frame.shape) == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame


class CameraManager:
    def __init__(self) -> None:
        source_config = resolve_camera_source()
        self.source = source_config["source"]
        self.source_origin = source_config["origin"]
        self.source_config_file = source_config["config_file"]
        self.width = _env_int("LOONGSON_AOI_CAMERA_WIDTH", 1280)
        self.height = _env_int("LOONGSON_AOI_CAMERA_HEIGHT", 800)
        self.warmup_frames = _env_int("LOONGSON_AOI_CAMERA_WARMUP_FRAMES", 3)
        self.read_retries = _env_int("LOONGSON_AOI_CAMERA_READ_RETRIES", 8)
        self.preview_fps = _env_float("LOONGSON_AOI_CAMERA_PREVIEW_FPS", 6.0)
        self.jpeg_quality = _env_int("LOONGSON_AOI_CAMERA_JPEG_QUALITY", 82)
        self.fourcc = os.getenv("LOONGSON_AOI_CAMERA_FOURCC", "").strip().upper()
        self._lock = threading.RLock()
        self._cap: Any = None
        self._last_frame_at: str | None = None
        self._last_error: str | None = None
        self._opened_at: str | None = None

    def _release_unlocked(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = None

    def _refresh_source_unlocked(self) -> None:
        source_config = resolve_camera_source()
        if source_config["source"] != self.source:
            self._release_unlocked()
            self.source = source_config["source"]
            self._opened_at = None
        self.source_origin = source_config["origin"]
        self.source_config_file = source_config["config_file"]

    def _open_unlocked(self) -> None:
        self._refresh_source_unlocked()
        if self._cap is not None and self._cap.isOpened():
            return

        cv2 = _import_cv2()
        source = parse_source(self.source)
        backend = _camera_backend_code(cv2, source)
        cap = cv2.VideoCapture(source, backend) if backend is not None else cv2.VideoCapture(source)

        if not cap.isOpened():
            self._last_error = f"Could not open camera source: {self.source}"
            try:
                cap.release()
            except Exception:
                pass
            raise CameraTriggerError(self._last_error)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if self.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc[:4]))
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self._cap = cap
        self._opened_at = _now_iso()
        self._last_error = None

    def ensure_open(self) -> None:
        with self._lock:
            self._open_unlocked()

    def close(self) -> None:
        with self._lock:
            self._release_unlocked()

    def read_frame(self, warmup: int = 0) -> Any:
        with self._lock:
            self._open_unlocked()
            assert self._cap is not None

            frame = None
            ok = False
            for _ in range(max(0, warmup)):
                self._cap.read()
            for _ in range(max(1, self.read_retries)):
                ok, frame = self._cap.read()
                if ok and frame is not None:
                    break
                time.sleep(0.08)
            if not ok:
                self._last_error = "Failed to read frame from camera."
                self.close()
                self._open_unlocked()
                assert self._cap is not None
                time.sleep(0.2)
                for _ in range(max(1, self.read_retries)):
                    ok, frame = self._cap.read()
                    if ok and frame is not None:
                        break
                    time.sleep(0.08)

            if not ok or frame is None:
                self._last_error = "Failed to read frame from camera after reopen."
                raise CameraTriggerError(self._last_error)

            self._last_frame_at = _now_iso()
            self._last_error = None
            return _normalize_frame(frame)

    def capture_to(self, output_path: Path, warmup: int | None = None) -> tuple[Path, float]:
        start = time.perf_counter()
        frame = self.read_frame(self.warmup_frames if warmup is None else warmup)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2 = _import_cv2()
        if not cv2.imwrite(str(output_path), frame):
            raise CameraTriggerError(f"Failed to write captured image: {output_path}")
        return output_path, (time.perf_counter() - start) * 1000.0

    def status(self, probe: bool = False) -> dict[str, Any]:
        if probe:
            try:
                self.ensure_open()
            except Exception as exc:
                self._last_error = str(exc)

        opened = False
        with self._lock:
            self._refresh_source_unlocked()
            opened = bool(self._cap is not None and self._cap.isOpened())
        return {
            "opened": opened,
            "source": self.source,
            "source_origin": self.source_origin,
            "source_config_file": self.source_config_file,
            "backend": os.getenv("LOONGSON_AOI_CAMERA_BACKEND", "AUTO"),
            "width": self.width,
            "height": self.height,
            "last_frame_at": self._last_frame_at,
            "opened_at": self._opened_at,
            "last_error": self._last_error,
            "latest_image": _rel(DEFAULT_IMAGE),
        }

    def mjpeg_frames(self):
        cv2 = _import_cv2()
        boundary = b"--frame\r\n"
        interval = 1.0 / max(1.0, self.preview_fps)
        while True:
            started = time.perf_counter()
            try:
                frame = self.read_frame(warmup=0)
                ok, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, self.jpeg_quality))],
                )
                if ok:
                    yield boundary
                    yield b"Content-Type: image/jpeg\r\n\r\n"
                    yield encoded.tobytes()
                    yield b"\r\n"
            except GeneratorExit:
                break
            except Exception as exc:
                self._last_error = str(exc)
                time.sleep(0.5)

            elapsed = time.perf_counter() - started
            if elapsed < interval:
                time.sleep(interval - elapsed)


class YoloDetector:
    def __init__(self) -> None:
        self.weights = DEFAULT_WEIGHTS
        self.config = DEFAULT_CONFIG
        self.conf = _env_float("LOONGSON_AOI_YOLO_CONF", 0.25)
        self.imgsz = _env_int("LOONGSON_AOI_YOLO_IMGSZ", 960)
        self._lock = threading.RLock()
        self._model: Any = None
        self._names: dict[int, str] | None = None
        self._cn_names: dict[str, str] | None = None
        self._last_error: str | None = None
        self._loaded_at: str | None = None

    def _load_label_maps(self) -> tuple[dict[int, str], dict[str, str]]:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CameraTriggerError("PyYAML is not installed; cannot read pcb_defects.yaml.") from exc

        if not self.config.exists():
            raise CameraTriggerError(f"YOLO class config does not exist: {self.config}")
        with self.config.open("r", encoding="utf-8") as file:
            config = yaml.safe_load(file)
        names = {int(class_id): name for class_id, name in config["names"].items()}
        cn_names = config.get("cn_names", {}) or {}
        return names, cn_names

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not self.weights.exists():
                raise CameraTriggerError(f"YOLO weights do not exist: {self.weights}")
            try:
                from ultralytics import YOLO  # type: ignore[import-not-found]

                self._names, self._cn_names = self._load_label_maps()
                self._model = YOLO(str(self.weights))
                self._loaded_at = _now_iso()
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def status(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "loaded_at": self._loaded_at,
            "weights": str(self.weights),
            "weights_exists": self.weights.exists(),
            "config": str(self.config),
            "config_exists": self.config.exists(),
            "conf": self.conf,
            "imgsz": self.imgsz,
            "last_error": self._last_error,
        }

    def detect_image(
        self,
        image_path: Path,
        annotated_path: Path,
        json_path: Path,
        compatible_result_image: Path = RESULT_IMAGE,
        compatible_result_json: Path = RESULT_JSON,
    ) -> tuple[list[dict[str, Any]], float]:
        start = time.perf_counter()
        cv2 = _import_cv2()
        image = cv2.imread(str(image_path))
        if image is None:
            raise CameraTriggerError(f"Could not read captured image: {image_path}")

        with self._lock:
            self.ensure_loaded()
            assert self._model is not None
            assert self._names is not None
            assert self._cn_names is not None
            results = self._model.predict(source=str(image_path), conf=self.conf, imgsz=self.imgsz, verbose=False)

            detections: list[dict[str, Any]] = []
            for box in results[0].boxes:
                class_id = int(box.cls[0].item())
                confidence = float(box.conf[0].item())
                defect_type = self._names.get(class_id, str(class_id))
                label = self._cn_names.get(defect_type, defect_type)
                bbox = [int(value) for value in box.xyxy[0].tolist()]
                detections.append(
                    {
                        "type": defect_type,
                        "label": label,
                        "bbox": bbox,
                        "confidence": round(confidence, 4),
                    }
                )
                draw_detection(image, bbox, label, confidence)

        annotated_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        compatible_result_image.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(annotated_path), image)
        cv2.imwrite(str(compatible_result_image), image)
        for target in (json_path, compatible_result_json):
            with target.open("w", encoding="utf-8") as file:
                json.dump(detections, file, ensure_ascii=False, indent=2)

        return detections, (time.perf_counter() - start) * 1000.0

    def detect_frame(self, frame: Any, annotated_path: Path | None = None) -> tuple[list[dict[str, Any]], Any, float]:
        start = time.perf_counter()
        image = _normalize_frame(frame).copy()

        with self._lock:
            self.ensure_loaded()
            assert self._model is not None
            assert self._names is not None
            assert self._cn_names is not None
            results = self._model.predict(source=image, conf=self.conf, imgsz=self.imgsz, verbose=False)

            detections: list[dict[str, Any]] = []
            for box in results[0].boxes:
                class_id = int(box.cls[0].item())
                confidence = float(box.conf[0].item())
                defect_type = self._names.get(class_id, str(class_id))
                label = self._cn_names.get(defect_type, defect_type)
                bbox = [int(value) for value in box.xyxy[0].tolist()]
                detections.append(
                    {
                        "type": defect_type,
                        "label": label,
                        "bbox": bbox,
                        "confidence": round(confidence, 4),
                    }
                )
                draw_detection(image, bbox, label, confidence)

        if annotated_path is not None:
            annotated_path.parent.mkdir(parents=True, exist_ok=True)
            cv2 = _import_cv2()
            cv2.imwrite(str(annotated_path), image)

        return detections, image, (time.perf_counter() - start) * 1000.0


def draw_detection(image: Any, bbox: list[int], label: str, confidence: float) -> None:
    cv2 = _import_cv2()
    x1, y1, x2, y2 = bbox
    text = f"{label} {confidence:.2f}"
    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
    cv2.putText(image, text, (x1, max(y1 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 0), 2)


camera_manager = CameraManager()
yolo_detector = YoloDetector()


class VideoDetectionSession:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._running = False
        self._scan_id: str | None = None
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._last_frame_at: str | None = None
        self._last_error: str | None = None
        self._frame_id = 0
        self._fps = 0.0
        self._inference_fps = VIDEO_DEFAULT_INFERENCE_FPS
        self._preview_fps = VIDEO_DEFAULT_PREVIEW_FPS
        self._latest_jpeg: bytes | None = None
        self._latest_payload: dict[str, Any] | None = None
        self._position: dict[str, Any] = {
            "x_mm": 0.0,
            "y_mm": 0.0,
            "area_mm": {"x": 0.0, "y": 0.0, "width": 30.0, "height": 30.0},
        }

    def update_position(self, x_mm: float, y_mm: float, area_mm: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._position = {
                "x_mm": round(float(x_mm), 4),
                "y_mm": round(float(y_mm), 4),
                "area_mm": dict(area_mm or {"x": x_mm, "y": y_mm, "width": 30.0, "height": 30.0}),
            }

    def _position_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "x_mm": self._position["x_mm"],
                "y_mm": self._position["y_mm"],
                "area_mm": dict(self._position["area_mm"]),
            }

    def stop(self) -> None:
        self._stop_requested.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "scan_id": self._scan_id,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "frame_id": self._frame_id,
                "fps": round(self._fps, 2),
                "inference_fps": self._inference_fps,
                "preview_fps": self._preview_fps,
                "last_frame_at": self._last_frame_at,
                "last_error": self._last_error,
                "latest_image": _rel(VIDEO_LATEST_IMAGE),
                "latest_json": _rel(VIDEO_LATEST_JSON),
                "position": dict(self._position),
            }

    def _store_frame(self, frame: Any, payload: dict[str, Any]) -> None:
        encoded = self._encode_jpeg(frame)

        VIDEO_LATEST_JSON.parent.mkdir(parents=True, exist_ok=True)
        with VIDEO_LATEST_JSON.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        with self._lock:
            self._latest_jpeg = encoded
            self._latest_payload = dict(payload)
            self._frame_id = int(payload["frame_id"])
            self._fps = float(payload.get("fps", 0.0))
            self._last_frame_at = _now_iso()
            self._last_error = None

    def _encode_jpeg(self, frame: Any) -> bytes:
        cv2 = _import_cv2()
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, camera_manager.jpeg_quality))],
        )
        if not ok:
            raise CameraTriggerError("Failed to encode video detection frame.")
        return encoded.tobytes()

    def _store_preview_frame(self, frame: Any) -> None:
        encoded = self._encode_jpeg(frame)
        with self._lock:
            self._latest_jpeg = encoded
            self._last_frame_at = _now_iso()

    async def run(
        self,
        scan_id: str,
        stop_event: asyncio.Event,
        inference_fps: float | None = None,
        preview_fps: float | None = None,
        on_detection: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_error: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        inference_fps = float(inference_fps or VIDEO_DEFAULT_INFERENCE_FPS)
        preview_fps = float(preview_fps or VIDEO_DEFAULT_PREVIEW_FPS)
        inference_interval = 1.0 / max(0.2, inference_fps)
        preview_interval = 1.0 / max(1.0, preview_fps)

        with self._lock:
            if self._running:
                raise CameraTriggerError("Video detection is already running.")
            self._running = True
            self._scan_id = scan_id
            self._started_at = _now_iso()
            self._stopped_at = None
            self._last_error = None
            self._frame_id = 0
            self._fps = 0.0
            self._inference_fps = inference_fps
            self._preview_fps = preview_fps
            self._latest_payload = None
            self._latest_jpeg = None
            self._stop_requested.clear()

        prev_inference_time = time.perf_counter()
        last_inference_started = 0.0
        pending_inference: asyncio.Task | None = None
        pending_frame_id = 0

        async def finish_inference(task: asyncio.Task, frame_id: int) -> None:
            nonlocal prev_inference_time
            try:
                detections, annotated, inference_ms = await task
                now = time.perf_counter()
                fps = 1.0 / max(now - prev_inference_time, 1e-6)
                prev_inference_time = now
                position = self._position_snapshot()
                payload = {
                    "frame_id": frame_id,
                    "scan_id": scan_id,
                    "x_mm": position["x_mm"],
                    "y_mm": position["y_mm"],
                    "area_mm": position["area_mm"],
                    "defects": detections,
                    "fps": round(fps, 2),
                    "inference_ms": round(inference_ms, 2),
                    "annotated_image": _rel(VIDEO_LATEST_IMAGE),
                    "latest_json": _rel(VIDEO_LATEST_JSON),
                    "captured_at": _now_iso(),
                }
                self._store_frame(annotated, payload)
                if on_detection is not None:
                    await on_detection(payload)
            except Exception as exc:
                error_payload = {
                    "scan_id": scan_id,
                    "error": "video_detection_failed",
                    "message": str(exc),
                }
                with self._lock:
                    self._last_error = str(exc)
                if on_error is not None:
                    await on_error(error_payload)

        try:
            while not stop_event.is_set() and not self._stop_requested.is_set():
                started = time.perf_counter()
                try:
                    frame = await asyncio.to_thread(camera_manager.read_frame, 0)
                    now = time.perf_counter()
                    await asyncio.to_thread(self._store_preview_frame, frame)

                    if pending_inference is not None and pending_inference.done():
                        await finish_inference(pending_inference, pending_frame_id)
                        pending_inference = None

                    if pending_inference is None and now - last_inference_started >= inference_interval:
                        pending_frame_id = self._frame_id + 1
                        inference_frame = frame.copy()
                        pending_inference = asyncio.create_task(
                            asyncio.to_thread(yolo_detector.detect_frame, inference_frame, VIDEO_LATEST_IMAGE)
                        )
                        last_inference_started = now
                except Exception as exc:
                    error_payload = {
                        "scan_id": scan_id,
                        "error": "video_detection_failed",
                        "message": str(exc),
                    }
                    with self._lock:
                        self._last_error = str(exc)
                    if on_error is not None:
                        await on_error(error_payload)
                    await asyncio.sleep(0.5)

                elapsed = time.perf_counter() - started
                if elapsed < preview_interval:
                    await asyncio.sleep(preview_interval - elapsed)
        finally:
            if pending_inference is not None and not pending_inference.done():
                try:
                    await asyncio.wait_for(pending_inference, timeout=3.0)
                except Exception:
                    pending_inference.cancel()
            with self._lock:
                self._running = False
                self._stopped_at = _now_iso()

    def mjpeg_frames(self):
        boundary = b"--frame\r\n"
        while True:
            with self._lock:
                jpeg = self._latest_jpeg
                preview_fps = self._preview_fps
            if jpeg:
                yield boundary
                yield b"Content-Type: image/jpeg\r\n\r\n"
                yield jpeg
                yield b"\r\n"
            time.sleep(1.0 / max(1.0, preview_fps))


video_detection_session = VideoDetectionSession()


def _cell_dir(scan_id: str, row: int, col: int) -> Path:
    return SCAN_OUTPUT_DIR / scan_id / f"r{row:03d}_c{col:03d}"


async def capture_frame(
    row: int,
    col: int,
    x: float,
    y: float,
    scan_id: str,
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    if stop_event.is_set():
        return _stopped("Camera capture skipped by stop_scan.", row, col, x, y)

    cell_dir = _cell_dir(scan_id, row, col)
    raw_path = cell_dir / "raw.jpg"

    try:
        latest_path, capture_ms = await asyncio.to_thread(camera_manager.capture_to, DEFAULT_IMAGE)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(latest_path.read_bytes())
    except Exception as exc:
        return _error("capture_failed", str(exc), row, col, x, y)

    return {
        "ok": True,
        "row": row,
        "col": col,
        "x_mm": x,
        "y_mm": y,
        "raw_image": _rel(raw_path),
        "latest_image": _rel(DEFAULT_IMAGE),
        "capture_ms": round(capture_ms, 2),
    }


def save_simulation_upload(filename: str, content: bytes) -> dict[str, Any]:
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SIMULATION_IMAGE_SUFFIXES:
        raise CameraTriggerError(
            f"Unsupported simulation image type: {suffix or '(none)'}. "
            f"Allowed: {', '.join(sorted(SIMULATION_IMAGE_SUFFIXES))}"
        )
    if not content:
        raise CameraTriggerError("Uploaded simulation image is empty.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = SIMULATION_IMPORT_DIR / f"{timestamp}_{safe_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {
        "ok": True,
        "name": safe_name,
        "path": _rel(target),
        "size_bytes": target.stat().st_size,
    }


async def capture_simulation_cell(
    row: int,
    col: int,
    x: float,
    y: float,
    scan_id: str,
    source_image: str | Path | None,
    plan: dict[str, Any],
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    if stop_event.is_set():
        return _stopped("Simulation capture skipped by stop_scan.", row, col, x, y)
    if not source_image:
        return _error("simulation_image_missing", "Simulation mode requires an uploaded PCB image.", row, col, x, y)

    try:
        source_path = resolve_artifact(source_image)
        raw_path, capture_ms, crop_meta = await asyncio.to_thread(
            _write_simulation_crop,
            source_path,
            row,
            col,
            scan_id,
            plan,
        )
    except Exception as exc:
        return _error("simulation_capture_failed", str(exc), row, col, x, y)

    return {
        "ok": True,
        "row": row,
        "col": col,
        "x_mm": x,
        "y_mm": y,
        "raw_image": _rel(raw_path),
        "latest_image": _rel(DEFAULT_IMAGE),
        "capture_ms": round(capture_ms, 2),
        "source_image": _rel(source_path),
        "simulation_crop": crop_meta,
    }


def _write_simulation_crop(
    source_path: Path,
    row: int,
    col: int,
    scan_id: str,
    plan: dict[str, Any],
) -> tuple[Path, float, dict[str, int]]:
    start = time.perf_counter()
    cv2 = _import_cv2()
    image = cv2.imread(str(source_path))
    if image is None:
        raise CameraTriggerError(f"Could not read simulation image: {source_path}")

    rows = max(1, int(plan.get("rows", 1)))
    cols = max(1, int(plan.get("cols", 1)))
    height, width = image.shape[:2]
    cell_w = max(1, int((width + cols - 1) / cols))
    cell_h = max(1, int((height + rows - 1) / rows))
    x1 = min(width - 1, max(0, col * cell_w))
    y1 = min(height - 1, max(0, row * cell_h))
    x2 = min(width, max(x1 + 1, (col + 1) * cell_w))
    y2 = min(height, max(y1 + 1, (row + 1) * cell_h))

    crop = image[y1:y2, x1:x2].copy()
    if crop.size == 0:
        raise CameraTriggerError(f"Simulation crop is empty for row={row}, col={col}.")

    cell_dir = _cell_dir(scan_id, row, col)
    raw_path = cell_dir / "raw.jpg"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(raw_path), crop):
        raise CameraTriggerError(f"Failed to write simulation raw image: {raw_path}")
    if not cv2.imwrite(str(DEFAULT_IMAGE), crop):
        raise CameraTriggerError(f"Failed to write latest simulation image: {DEFAULT_IMAGE}")

    crop_meta = {
        "source_width": int(width),
        "source_height": int(height),
        "x1": int(x1),
        "y1": int(y1),
        "x2": int(x2),
        "y2": int(y2),
    }
    return raw_path, (time.perf_counter() - start) * 1000.0, crop_meta


async def detect_captured_frame(
    row: int,
    col: int,
    x: float,
    y: float,
    scan_id: str,
    raw_image: str | Path,
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    if stop_event.is_set():
        return _stopped("YOLO detection skipped by stop_scan.", row, col, x, y)

    raw_path = _resolve_artifact(raw_image)
    cell_dir = _cell_dir(scan_id, row, col)
    annotated_path = cell_dir / "annotated.jpg"
    json_path = cell_dir / "result.json"

    try:
        detections, inference_ms = await asyncio.to_thread(
            yolo_detector.detect_image,
            raw_path,
            annotated_path,
            json_path,
        )
    except Exception as exc:
        return _error("detect_failed", str(exc), row, col, x, y)

    return {
        "ok": True,
        "defects": detections,
        "result_json": _rel(json_path),
        "compatible_result_json": _rel(RESULT_JSON),
        "raw_image": _rel(raw_path),
        "annotated_image": _rel(annotated_path),
        "capture_ms": None,
        "inference_ms": round(inference_ms, 2),
        "row": row,
        "col": col,
        "x_mm": x,
        "y_mm": y,
    }


async def capture_detect_and_read(
    row: int,
    col: int,
    x: float,
    y: float,
    image_path: str | Path | None,
    stop_event: asyncio.Event,
    scan_id: str = "manual",
) -> dict[str, Any]:
    capture = await capture_frame(row, col, x, y, scan_id, stop_event)
    if not capture.get("ok"):
        return capture
    detect = await detect_captured_frame(row, col, x, y, scan_id, capture["raw_image"], stop_event)
    if detect.get("ok"):
        detect["capture_ms"] = capture.get("capture_ms")
    return detect


def camera_status(probe: bool = False) -> dict[str, Any]:
    return camera_manager.status(probe=probe)


def detector_status() -> dict[str, Any]:
    return yolo_detector.status()


def prepare_video_detection() -> None:
    camera_manager.ensure_open()
    yolo_detector.ensure_loaded()


def update_video_position(x_mm: float, y_mm: float, area_mm: dict[str, Any] | None = None) -> None:
    video_detection_session.update_position(x_mm, y_mm, area_mm)


async def run_video_detection(
    scan_id: str,
    stop_event: asyncio.Event,
    inference_fps: float | None = None,
    preview_fps: float | None = None,
    on_detection: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_error: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> None:
    await video_detection_session.run(
        scan_id,
        stop_event,
        inference_fps=inference_fps,
        preview_fps=preview_fps,
        on_detection=on_detection,
        on_error=on_error,
    )


def stop_video_detection() -> None:
    video_detection_session.stop()


def video_status() -> dict[str, Any]:
    return video_detection_session.status()


def stream_mjpeg():
    return camera_manager.mjpeg_frames()


def stream_video_mjpeg():
    return video_detection_session.mjpeg_frames()


def _resolve_artifact(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (ALGORITHM_ROOT / candidate).resolve()
    if not _is_within(resolved, ALGORITHM_ROOT.resolve()):
        raise CameraTriggerError(f"Artifact path is outside algorithm root: {path}")
    return resolved


def resolve_artifact(path: str | Path) -> Path:
    resolved = _resolve_artifact(path)
    if not resolved.exists():
        raise CameraTriggerError(f"Artifact does not exist: {path}")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _error(error: str, message: str, row: int, col: int, x: float, y: float) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "message": message,
        "row": row,
        "col": col,
        "x_mm": x,
        "y_mm": y,
    }


def _stopped(message: str, row: int, col: int, x: float, y: float) -> dict[str, Any]:
    return _error("scan_stopped", message, row, col, x, y)
