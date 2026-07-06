import asyncio
import json
import os
import platform
import re
import shutil
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


def _env_int_list(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    items: list[int] = []
    for part in re.split(r"[,; ]+", value.strip()):
        if not part:
            continue
        items.append(int(part, 0))
    return tuple(items) if items else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


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
PHONE_UPLOAD_DIR = ALGORITHM_ROOT / "algorithm" / "outputs" / "mobile_uploads"
RESULT_JSON = DETECTION_OUTPUT_DIR / "result.json"
RESULT_IMAGE = DETECTION_OUTPUT_DIR / "result.jpg"
DEFAULT_IMAGE = CAMERA_OUTPUT_DIR / "latest.jpg"
VIDEO_LATEST_IMAGE = CAMERA_OUTPUT_DIR / "video_latest.jpg"
VIDEO_LATEST_JSON = CAMERA_OUTPUT_DIR / "video_latest.json"
DEFAULT_CONFIG = ALGORITHM_ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
DEFAULT_PT_WEIGHTS = ALGORITHM_ROOT / "runs" / "detect" / "pcb_defects" / "weights" / "best.pt"


def _first_existing_path(candidates: list[Path], fallback: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return fallback


DEFAULT_ONNX_WEIGHTS = Path(
    os.getenv(
        "LOONGSON_AOI_ONNX_WEIGHTS",
        str(
            _first_existing_path(
                [
                    ALGORITHM_ROOT / "runs" / "detect" / "pcb_defects" / "weights" / "best.onnx",
                    ALGORITHM_ROOT / "runs" / "detect" / "pcb_defects_20260704_035505" / "weights" / "best.onnx",
                    ROOT.parent.parent
                    / "Loongson_AOI_PCB"
                    / "runs"
                    / "detect"
                    / "pcb_defects_20260704_035505"
                    / "weights"
                    / "best.onnx",
                ],
                ALGORITHM_ROOT / "runs" / "detect" / "pcb_defects" / "weights" / "best.onnx",
            )
        ),
    )
).expanduser()
DEFAULT_WEIGHTS = Path(os.getenv("LOONGSON_AOI_WEIGHTS", str(DEFAULT_ONNX_WEIGHTS))).expanduser()
DETECTOR_BACKEND = os.getenv("LOONGSON_AOI_DETECTOR_BACKEND", "auto").strip().lower()

SIMULATION_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
BOARD_SAFE_MODE = _env_bool("LOONGSON_AOI_BOARD_SAFE_MODE", False)
SIMULATION_MAX_DIMENSION = _env_int("LOONGSON_AOI_SIMULATION_MAX_DIMENSION", 0)
SIMULATION_JPEG_QUALITY = _env_int("LOONGSON_AOI_SIMULATION_JPEG_QUALITY", 82)
PHONE_MAX_UPLOAD_BYTES = _env_int("LOONGSON_AOI_PHONE_MAX_UPLOAD_MB", 20) * 1024 * 1024
_PHONE_TILE_SIZE_ENV = os.getenv("LOONGSON_AOI_PHONE_TILE_SIZE")
PHONE_TILE_SIZE = _env_int("LOONGSON_AOI_PHONE_TILE_SIZE", 640)
PHONE_TILE_SIZES = _env_int_list(
    "LOONGSON_AOI_PHONE_TILE_SIZES",
    (PHONE_TILE_SIZE,) if _PHONE_TILE_SIZE_ENV else (512, 640, 800),
)
_DEFAULT_PHONE_IMGSZ = _env_int("LOONGSON_AOI_YOLO_IMGSZ", 960)
PHONE_IMGSZS = _env_int_list(
    "LOONGSON_AOI_PHONE_IMGSZS",
    (640, _DEFAULT_PHONE_IMGSZ) if _DEFAULT_PHONE_IMGSZ != 640 else (640,),
)
PHONE_TILE_OVERLAP = _env_float("LOONGSON_AOI_PHONE_TILE_OVERLAP", 0.5)
PHONE_FAST_TILE_SIZES = _env_int_list("LOONGSON_AOI_PHONE_FAST_TILE_SIZES", (640,))
PHONE_FAST_IMGSZS = _env_int_list("LOONGSON_AOI_PHONE_FAST_IMGSZS", (640,))
PHONE_FAST_TILE_OVERLAP = _env_float("LOONGSON_AOI_PHONE_FAST_TILE_OVERLAP", 0.5)
PHONE_PRECISION_TILE_SIZES = _env_int_list("LOONGSON_AOI_PHONE_PRECISION_TILE_SIZES", PHONE_TILE_SIZES)
PHONE_PRECISION_IMGSZS = _env_int_list("LOONGSON_AOI_PHONE_PRECISION_IMGSZS", PHONE_IMGSZS)
PHONE_PRECISION_TILE_OVERLAP = _env_float("LOONGSON_AOI_PHONE_PRECISION_TILE_OVERLAP", PHONE_TILE_OVERLAP)
PHONE_FUSION_IOU = _env_float("LOONGSON_AOI_PHONE_FUSION_IOU", 0.45)
PHONE_CONFIRM_CONF = _env_float("LOONGSON_AOI_PHONE_CONFIRM_CONF", 0.5)
PHONE_MULTI_CONFIRM_CONF = _env_float("LOONGSON_AOI_PHONE_MULTI_CONFIRM_CONF", 0.35)
PHONE_SUSPECTED_CONF = _env_float("LOONGSON_AOI_PHONE_SUSPECTED_CONF", 0.2)
PHONE_MULTI_SUSPECTED_CONF = _env_float("LOONGSON_AOI_PHONE_MULTI_SUSPECTED_CONF", 0.1)
VIDEO_DEFAULT_INFERENCE_FPS = _env_float("LOONGSON_AOI_VIDEO_INFERENCE_FPS", 5.0)
VIDEO_DEFAULT_PREVIEW_FPS = _env_float("LOONGSON_AOI_VIDEO_PREVIEW_FPS", 8.0)
CAMERA_SOURCE_FILE = Path(os.getenv("LOONGSON_AOI_CAMERA_SOURCE_FILE", ROOT / "camera_source.txt")).expanduser()
DEFAULT_CAMERA_SOURCE = "0" if "loong" in platform.machine().lower() else "1"
ONNX_INPUT_IMGSZ = _env_int("LOONGSON_AOI_ONNX_INPUT_IMGSZ", 0)
OPENCV_THREADS = _env_int("LOONGSON_AOI_OPENCV_THREADS", 0)


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


def simulation_runtime_config() -> dict[str, Any]:
    return {
        "board_safe_mode": BOARD_SAFE_MODE,
        "max_dimension": SIMULATION_MAX_DIMENSION,
        "jpeg_quality": SIMULATION_JPEG_QUALITY,
        "recommended_pcb_mm": {"length": 30.0, "width": 30.0},
        "recommended_mode": "simulation_single_cell",
    }


def normalize_phone_detection_profile(profile: str | None) -> str:
    value = str(profile or "fast").strip().lower()
    aliases = {
        "fast": "fast",
        "quick": "fast",
        "speed": "fast",
        "快速": "fast",
        "快速模式": "fast",
        "precision": "precision",
        "precise": "precision",
        "accurate": "precision",
        "high": "precision",
        "high_precision": "precision",
        "高精": "precision",
        "高精模式": "precision",
    }
    if value not in aliases:
        raise CameraTriggerError(f"Unsupported phone detection profile: {profile}")
    return aliases[value]


def phone_detection_profile_config(profile: str | None = None) -> dict[str, Any]:
    normalized = normalize_phone_detection_profile(profile)
    if normalized == "precision":
        return {
            "profile": "precision",
            "label": "高精模式",
            "tile_sizes": PHONE_PRECISION_TILE_SIZES,
            "imgszs": PHONE_PRECISION_IMGSZS,
            "overlap": PHONE_PRECISION_TILE_OVERLAP,
        }
    return {
        "profile": "fast",
        "label": "快速模式",
        "tile_sizes": PHONE_FAST_TILE_SIZES,
        "imgszs": PHONE_FAST_IMGSZS,
        "overlap": PHONE_FAST_TILE_OVERLAP,
    }


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
    if OPENCV_THREADS > 0:
        try:
            cv2.setNumThreads(OPENCV_THREADS)
        except Exception:
            pass
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
        self.conf = _env_float("LOONGSON_AOI_YOLO_CONF", 0.5)
        self.imgsz = _env_int("LOONGSON_AOI_YOLO_IMGSZ", 640)
        self.backend_preference = DETECTOR_BACKEND
        self._lock = threading.RLock()
        self._model: Any = None
        self._backend: str | None = None
        self._names: dict[int, str] | None = None
        self._cn_names: dict[str, str] | None = None
        self._last_error: str | None = None
        self._loaded_at: str | None = None
        self._onnx_input_imgsz: int | None = None

    def _load_label_maps(self) -> tuple[dict[int, str], dict[str, str]]:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError:
            return self._load_label_maps_without_yaml()

        if not self.config.exists():
            raise CameraTriggerError(f"YOLO class config does not exist: {self.config}")
        with self.config.open("r", encoding="utf-8") as file:
            config = yaml.safe_load(file)
        names = {int(class_id): name for class_id, name in config["names"].items()}
        cn_names = config.get("cn_names", {}) or {}
        return names, cn_names

    def _load_label_maps_without_yaml(self) -> tuple[dict[int, str], dict[str, str]]:
        default_names = {
            0: "dry_solder_joint",
            1: "improper_installation",
            2: "pcb_damage",
            3: "short_circuit",
            4: "insufficient_solder",
            5: "reverse_component",
            6: "excess_solder",
        }
        if not self.config.exists():
            return default_names, {}

        section: str | None = None
        names: dict[int, str] = {}
        cn_names: dict[str, str] = {}
        for line in self.config.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith((" ", "\t")) and stripped.endswith(":"):
                section = stripped[:-1]
                continue
            if section == "names":
                match = re.match(r"\s*(\d+)\s*:\s*(.+?)\s*$", line)
                if match:
                    names[int(match.group(1))] = match.group(2).strip().strip("'\"")
            elif section == "cn_names":
                match = re.match(r"\s*([0-9A-Za-z_]+)\s*:\s*(.*?)\s*$", line)
                if match:
                    cn_names[match.group(1)] = match.group(2).strip().strip("'\"")
        return (names or default_names), cn_names

    def _resolve_backend(self) -> str:
        preference = self.backend_preference or "auto"
        if preference in {"onnx", "opencv", "opencv_dnn", "onnx_opencv"}:
            return "onnx_opencv"
        if preference in {"pt", "torch", "ultralytics", "yolo"}:
            return "ultralytics"
        if preference != "auto":
            raise CameraTriggerError(f"Unsupported LOONGSON_AOI_DETECTOR_BACKEND: {self.backend_preference}")
        if self.weights.suffix.lower() == ".onnx":
            return "onnx_opencv"
        return "ultralytics"

    def _resolve_onnx_input_imgsz(self) -> int | None:
        if self._onnx_input_imgsz:
            return self._onnx_input_imgsz

        try:
            import onnx  # type: ignore[import-not-found]

            model = onnx.load(str(self.weights))
            if not model.graph.input:
                return self.imgsz
            dims = model.graph.input[0].type.tensor_type.shape.dim
            if len(dims) >= 4:
                height = int(dims[2].dim_value or 0)
                width = int(dims[3].dim_value or 0)
                if height > 0 and width > 0 and height == width:
                    self._onnx_input_imgsz = height
                    return self._onnx_input_imgsz
        except Exception:
            pass

        probed_imgsz = self._probe_onnx_input_imgsz()
        if probed_imgsz:
            self._onnx_input_imgsz = probed_imgsz
            return self._onnx_input_imgsz

        if ONNX_INPUT_IMGSZ > 0:
            self._onnx_input_imgsz = ONNX_INPUT_IMGSZ
            return self._onnx_input_imgsz

        self._onnx_input_imgsz = self.imgsz
        return self._onnx_input_imgsz

    def _probe_onnx_input_imgsz(self) -> int | None:
        cv2 = _import_cv2()
        try:
            import numpy as np
        except ImportError:
            return None

        candidates: list[int] = []
        for value in (ONNX_INPUT_IMGSZ, self.imgsz, 640, 960, 1024, 1280):
            if value and int(value) > 0 and int(value) not in candidates:
                candidates.append(int(value))

        for size in candidates:
            try:
                net = cv2.dnn.readNetFromONNX(str(self.weights))
                blob = np.zeros((1, 3, size, size), dtype=np.float32)
                net.setInput(blob)
                net.forward()
                return size
            except Exception:
                continue
        return None

    def compatible_imgszs(self, requested_imgszs: list[int]) -> tuple[list[int], list[int], int | None]:
        normalized: list[int] = []
        for requested in requested_imgszs:
            value = max(32, int(requested))
            if value not in normalized:
                normalized.append(value)
        if self._resolve_backend() != "onnx_opencv":
            return normalized, [], None

        fixed_imgsz = self._resolve_onnx_input_imgsz()
        if not fixed_imgsz:
            return normalized, [], None

        compatible = [value for value in normalized if value == fixed_imgsz]
        skipped = [value for value in normalized if value != fixed_imgsz]
        if not compatible:
            compatible = [fixed_imgsz]
        return compatible, skipped, fixed_imgsz

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not self.weights.exists():
                raise CameraTriggerError(f"YOLO/ONNX weights do not exist: {self.weights}")
            try:
                self._names, self._cn_names = self._load_label_maps()
                self._backend = self._resolve_backend()
                if self._backend == "onnx_opencv":
                    cv2 = _import_cv2()
                    self._resolve_onnx_input_imgsz()
                    self._model = cv2.dnn.readNetFromONNX(str(self.weights))
                    try:
                        self._model.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                        self._model.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                    except Exception:
                        pass
                else:
                    from ultralytics import YOLO  # type: ignore[import-not-found]

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
            "backend": self._backend or self._resolve_backend(),
            "backend_preference": self.backend_preference,
            "weights": str(self.weights),
            "weights_exists": self.weights.exists(),
            "onnx_weights": str(DEFAULT_ONNX_WEIGHTS),
            "onnx_weights_exists": DEFAULT_ONNX_WEIGHTS.exists(),
            "config": str(self.config),
            "config_exists": self.config.exists(),
            "conf": self.conf,
            "imgsz": self.imgsz,
            "onnx_input_imgsz": self._onnx_input_imgsz or (self._resolve_onnx_input_imgsz() if self._resolve_backend() == "onnx_opencv" else None),
            "opencv_threads": OPENCV_THREADS,
            "last_error": self._last_error,
        }

    def _letterbox(self, image: Any, imgsz: int) -> tuple[Any, float, float, float]:
        cv2 = _import_cv2()
        height, width = image.shape[:2]
        scale = min(imgsz / max(width, 1), imgsz / max(height, 1))
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        pad_x = (imgsz - new_width) / 2.0
        pad_y = (imgsz - new_height) / 2.0
        left = int(round(pad_x - 0.1))
        right = int(round(pad_x + 0.1))
        top = int(round(pad_y - 0.1))
        bottom = int(round(pad_y + 0.1))
        padded = cv2.copyMakeBorder(
            resized,
            top,
            bottom,
            left,
            right,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        return padded, scale, float(left), float(top)

    def _onnx_predict(self, image: Any, imgsz: int) -> list[dict[str, Any]]:
        import numpy as np  # type: ignore[import-not-found]

        cv2 = _import_cv2()
        assert self._model is not None
        assert self._names is not None
        assert self._cn_names is not None

        fixed_imgsz = self._resolve_onnx_input_imgsz()
        if fixed_imgsz and int(imgsz) != int(fixed_imgsz):
            raise CameraTriggerError(
                f"ONNX model has fixed input {fixed_imgsz}x{fixed_imgsz}; requested imgsz={imgsz}. "
                "Export a dynamic/matching ONNX model or use LOONGSON_AOI_ONNX_INPUT_IMGSZ to match the model."
            )

        original_height, original_width = image.shape[:2]
        input_image, scale, pad_x, pad_y = self._letterbox(image, imgsz)
        blob = cv2.dnn.blobFromImage(input_image, scalefactor=1 / 255.0, size=(imgsz, imgsz), swapRB=True, crop=False)
        self._model.setInput(blob)
        output = self._model.forward()
        if isinstance(output, (list, tuple)):
            output = output[0]
        predictions = np.asarray(output)
        if predictions.ndim == 3:
            predictions = predictions[0]
        if predictions.ndim != 2:
            raise CameraTriggerError(f"Unsupported ONNX output shape: {tuple(np.asarray(output).shape)}")
        class_count = len(self._names)
        if predictions.shape[0] == class_count + 4:
            predictions = predictions.T
        elif predictions.shape[1] != class_count + 4:
            raise CameraTriggerError(f"Unsupported ONNX output shape: {tuple(np.asarray(output).shape)}")

        raw_detections: list[dict[str, Any]] = []
        for prediction in predictions:
            class_scores = prediction[4 : 4 + class_count]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])
            if confidence < self.conf:
                continue

            cx, cy, width, height = [float(value) for value in prediction[:4]]
            x1 = int(round((cx - width / 2.0 - pad_x) / scale))
            y1 = int(round((cy - height / 2.0 - pad_y) / scale))
            x2 = int(round((cx + width / 2.0 - pad_x) / scale))
            y2 = int(round((cy + height / 2.0 - pad_y) / scale))
            x1 = max(0, min(original_width - 1, x1))
            y1 = max(0, min(original_height - 1, y1))
            x2 = max(x1 + 1, min(original_width, x2))
            y2 = max(y1 + 1, min(original_height, y2))

            defect_type = self._names.get(class_id, str(class_id))
            raw_detections.append(
                {
                    "type": defect_type,
                    "label": self._cn_names.get(defect_type, defect_type),
                    "bbox": [x1, y1, x2, y2],
                    "confidence": round(confidence, 4),
                }
            )
        return _nms_detections(raw_detections)

    def _ultralytics_predict(self, source: Any, image_for_shape: Any, imgsz: int) -> list[dict[str, Any]]:
        assert self._model is not None
        assert self._names is not None
        assert self._cn_names is not None
        results = self._model.predict(source=source, conf=self.conf, imgsz=imgsz, verbose=False)

        detections: list[dict[str, Any]] = []
        image_height, image_width = image_for_shape.shape[:2]
        for box in results[0].boxes:
            class_id = int(box.cls[0].item())
            confidence = float(box.conf[0].item())
            defect_type = self._names.get(class_id, str(class_id))
            bbox = [int(value) for value in box.xyxy[0].tolist()]
            bbox[0] = max(0, min(image_width - 1, bbox[0]))
            bbox[1] = max(0, min(image_height - 1, bbox[1]))
            bbox[2] = max(bbox[0] + 1, min(image_width, bbox[2]))
            bbox[3] = max(bbox[1] + 1, min(image_height, bbox[3]))
            detections.append(
                {
                    "type": defect_type,
                    "label": self._cn_names.get(defect_type, defect_type),
                    "bbox": bbox,
                    "confidence": round(confidence, 4),
                }
            )
        return detections

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
            if self._backend == "onnx_opencv":
                detections = self._onnx_predict(image, self.imgsz)
            else:
                detections = self._ultralytics_predict(str(image_path), image, self.imgsz)
            for detection in detections:
                draw_detection(
                    image,
                    detection["bbox"],
                    str(detection.get("label") or detection.get("type")),
                    float(detection.get("confidence", 0.0)),
                )

        annotated_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        compatible_result_image.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(annotated_path), image)
        cv2.imwrite(str(compatible_result_image), image)
        for target in (json_path, compatible_result_json):
            with target.open("w", encoding="utf-8") as file:
                json.dump(detections, file, ensure_ascii=False, indent=2)

        return detections, (time.perf_counter() - start) * 1000.0

    def detect_frame(
        self,
        frame: Any,
        annotated_path: Path | None = None,
        imgsz: int | None = None,
    ) -> tuple[list[dict[str, Any]], Any, float]:
        start = time.perf_counter()
        image = _normalize_frame(frame).copy()
        predict_imgsz = int(imgsz or self.imgsz)

        with self._lock:
            self.ensure_loaded()
            assert self._model is not None
            assert self._names is not None
            assert self._cn_names is not None
            if self._backend == "onnx_opencv":
                detections = self._onnx_predict(image, predict_imgsz)
            else:
                detections = self._ultralytics_predict(image, image, predict_imgsz)
            for detection in detections:
                draw_detection(
                    image,
                    detection["bbox"],
                    str(detection.get("label") or detection.get("type")),
                    float(detection.get("confidence", 0.0)),
                )

        if annotated_path is not None:
            annotated_path.parent.mkdir(parents=True, exist_ok=True)
            cv2 = _import_cv2()
            cv2.imwrite(str(annotated_path), image)

        return detections, image, (time.perf_counter() - start) * 1000.0


def draw_detection(
    image: Any,
    bbox: list[int],
    label: str,
    confidence: float,
    color: tuple[int, int, int] = (0, 180, 0),
) -> None:
    cv2 = _import_cv2()
    x1, y1, x2, y2 = bbox
    text = f"{label} {confidence:.2f}"
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    cv2.putText(image, text, (x1, max(y1 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


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
        inference_handler: Callable[[Any, Path], Awaitable[tuple[Any, Any, float] | tuple[Any, Any, float, dict[str, Any]]]] | None = None,
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

        async def infer_frame(frame: Any) -> tuple[Any, Any, float, dict[str, Any]]:
            if inference_handler is not None:
                result = await inference_handler(frame, VIDEO_LATEST_IMAGE)
                if len(result) == 4:
                    detections, annotated, inference_ms, extra = result
                    return detections, annotated, inference_ms, dict(extra or {})
                detections, annotated, inference_ms = result
                return detections, annotated, inference_ms, {}
            detections, annotated, inference_ms = await asyncio.to_thread(yolo_detector.detect_frame, frame, VIDEO_LATEST_IMAGE)
            return detections, annotated, inference_ms, {
                "inference_location": "local_fallback",
                "fallback_reason": "no_phone_worker_online",
                "local_fallback": True,
            }

        async def finish_inference(task: asyncio.Task, frame_id: int) -> None:
            nonlocal prev_inference_time
            try:
                detections, annotated, inference_ms, extra_payload = await task
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
                payload.update(extra_payload)
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
                        pending_inference = asyncio.create_task(infer_frame(inference_frame))
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


def _scan_dir(scan_id: str) -> Path:
    return SCAN_OUTPUT_DIR / scan_id


def scan_manifest_path(scan_id: str) -> Path:
    return _scan_dir(scan_id) / "manifest.json"


def write_scan_manifest(
    scan_id: str,
    plan: dict[str, Any],
    operation_mode: str,
    cells: list[dict[str, Any]],
    status: str = "running",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "scan_id": scan_id,
        "status": status,
        "operation_mode": operation_mode,
        "updated_at": _now_iso(),
        "plan": plan,
        "cells": cells,
    }
    if extra:
        manifest.update(extra)
    path = scan_manifest_path(scan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
    return {"manifest": _rel(path), "cell_count": len(cells), "status": status}


def _load_scan_manifest(scan_id: str) -> dict[str, Any]:
    path = scan_manifest_path(scan_id)
    if not path.exists():
        raise CameraTriggerError(f"Scan manifest does not exist: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _tile_starts(total: int, tile: int, stride: int) -> list[int]:
    if total <= tile:
        return [0]
    starts = list(range(0, max(1, total - tile + 1), max(1, stride)))
    final = total - tile
    if starts[-1] != final:
        starts.append(final)
    return starts


def _bbox_iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def _nms_detections(detections: list[dict[str, Any]], iou_threshold: float = 0.45) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = {}
    for detection in detections:
        by_type.setdefault(str(detection.get("type", "")), []).append(detection)

    for items in by_type.values():
        ordered = sorted(items, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        class_kept: list[dict[str, Any]] = []
        for item in ordered:
            bbox = item.get("bbox") or [0, 0, 0, 0]
            if all(_bbox_iou(bbox, kept_item.get("bbox") or [0, 0, 0, 0]) < iou_threshold for kept_item in class_kept):
                class_kept.append(item)
        kept.extend(class_kept)
    return sorted(kept, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)


def _profile_key(profile: dict[str, Any]) -> str:
    return f"{int(profile.get('tile_size', 0))}@{int(profile.get('imgsz', 0))}"


def _source_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "tile_size": int(profile.get("tile_size", 0)),
        "imgsz": int(profile.get("imgsz", 0)),
        "tile_stride": int(profile.get("tile_stride", 0)),
    }


def _fuse_profile_detections(
    profile_results: list[dict[str, Any]],
    iou_threshold: float = PHONE_FUSION_IOU,
    confirm_conf: float = PHONE_CONFIRM_CONF,
    multi_confirm_conf: float = PHONE_MULTI_CONFIRM_CONF,
    suspected_conf: float = PHONE_SUSPECTED_CONF,
    multi_suspected_conf: float = PHONE_MULTI_SUSPECTED_CONF,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for profile in profile_results:
        profile_info = _source_profile(profile)
        profile_key = _profile_key(profile)
        for detection in profile.get("detections", []):
            candidate = dict(detection)
            candidate["_profile"] = profile_info
            candidate["_profile_key"] = profile_key
            candidates.append(candidate)

    clusters: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("confidence", 0.0)), reverse=True):
        bbox = candidate.get("bbox") or [0, 0, 0, 0]
        defect_type = str(candidate.get("type", ""))
        best_cluster: dict[str, Any] | None = None
        best_iou = 0.0
        for cluster in clusters:
            if str(cluster["type"]) != defect_type:
                continue
            iou = _bbox_iou(bbox, cluster["bbox"])
            if iou >= iou_threshold and iou > best_iou:
                best_iou = iou
                best_cluster = cluster
        if best_cluster is None:
            clusters.append({"type": defect_type, "bbox": bbox, "items": [candidate]})
        else:
            best_cluster["items"].append(candidate)

    confirmed: list[dict[str, Any]] = []
    suspected: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for cluster in clusters:
        items = sorted(cluster["items"], key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        best = items[0]
        profile_map: dict[str, dict[str, Any]] = {}
        for item in items:
            profile_map.setdefault(str(item["_profile_key"]), dict(item["_profile"]))
        confidences = [float(item.get("confidence", 0.0)) for item in items]
        max_confidence = max(confidences, default=0.0)
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        support_count = len(profile_map)
        evidence_score = min(0.99, max_confidence + 0.05 * max(0, support_count - 1))

        if max_confidence >= confirm_conf or (support_count >= 2 and max_confidence >= multi_confirm_conf):
            decision = "confirmed"
        elif max_confidence >= suspected_conf or (support_count >= 2 and max_confidence >= multi_suspected_conf):
            decision = "suspected"
        else:
            decision = "discarded"

        fused = {
            "type": best.get("type"),
            "label": best.get("label") or best.get("type"),
            "bbox": [int(value) for value in best.get("bbox", [0, 0, 0, 0])],
            "confidence": round(evidence_score, 4),
            "max_confidence": round(max_confidence, 4),
            "mean_confidence": round(mean_confidence, 4),
            "support_count": int(support_count),
            "decision": decision,
            "source_profiles": list(profile_map.values()),
            "source_tile": best.get("source_tile"),
        }
        if decision == "confirmed":
            confirmed.append(fused)
        elif decision == "suspected":
            suspected.append(fused)
        else:
            discarded.append(fused)

    confirmed = sorted(confirmed, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    suspected = sorted(suspected, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    discarded = sorted(discarded, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    return {
        "detections": confirmed + suspected,
        "confirmed_defects": confirmed,
        "suspected_defects": suspected,
        "discarded_defects": discarded,
        "fusion_rules": {
            "iou_threshold": iou_threshold,
            "confirm_conf": confirm_conf,
            "multi_confirm_conf": multi_confirm_conf,
            "suspected_conf": suspected_conf,
            "multi_suspected_conf": multi_suspected_conf,
        },
    }


def _mosaic_geometry(manifest: dict[str, Any], tile_width: int, tile_height: int) -> dict[str, Any]:
    plan = manifest.get("plan") or {}
    fov = plan.get("fov_mm") or {"width": 30.0, "height": 30.0}
    origin = plan.get("origin_mm") or {"x": 0.0, "y": 0.0}
    covered = plan.get("covered_mm") or {}
    cells = [cell for cell in manifest.get("cells", []) if cell.get("raw_image")]
    if not cells:
        raise CameraTriggerError("No captured raw images found in scan manifest.")

    fov_width = float(fov.get("width", 30.0))
    fov_height = float(fov.get("height", 30.0))
    px_per_mm_x = tile_width / max(fov_width, 1e-6)
    px_per_mm_y = tile_height / max(fov_height, 1e-6)
    origin_x = float(origin.get("x", 0.0))
    origin_y = float(origin.get("y", 0.0))
    max_x_mm = max(float(cell.get("x_mm", 0.0)) - origin_x + fov_width for cell in cells)
    max_y_mm = max(float(cell.get("y_mm", 0.0)) - origin_y + fov_height for cell in cells)
    covered_x_mm = max(float(covered.get("length", 0.0) or 0.0), max_x_mm)
    covered_y_mm = max(float(covered.get("width", 0.0) or 0.0), max_y_mm)

    return {
        "fov_mm": {"width": fov_width, "height": fov_height},
        "origin_mm": {"x": origin_x, "y": origin_y},
        "px_per_mm": {"x": px_per_mm_x, "y": px_per_mm_y},
        "tile_px": {"width": tile_width, "height": tile_height},
        "canvas_px": {
            "width": max(1, int(round(covered_x_mm * px_per_mm_x))),
            "height": max(1, int(round(covered_y_mm * px_per_mm_y))),
        },
        "covered_mm": {"length": covered_x_mm, "width": covered_y_mm},
    }


def _build_mosaic_image(manifest: dict[str, Any]) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    cells = [cell for cell in manifest.get("cells", []) if cell.get("raw_image")]
    if not cells:
        raise CameraTriggerError("No captured raw images found in scan manifest.")

    first_image = cv2.imread(str(resolve_artifact(cells[0]["raw_image"])))
    if first_image is None:
        raise CameraTriggerError(f"Could not read first raw image: {cells[0]['raw_image']}")
    tile_height, tile_width = first_image.shape[:2]
    geometry = _mosaic_geometry(manifest, tile_width, tile_height)
    canvas_width = int(geometry["canvas_px"]["width"])
    canvas_height = int(geometry["canvas_px"]["height"])
    px_per_mm_x = float(geometry["px_per_mm"]["x"])
    px_per_mm_y = float(geometry["px_per_mm"]["y"])
    origin = geometry["origin_mm"]

    accum = np.zeros((canvas_height, canvas_width, 3), dtype=np.float32)
    weights = np.zeros((canvas_height, canvas_width, 1), dtype=np.float32)
    placed_cells: list[dict[str, Any]] = []

    for cell in cells:
        image = cv2.imread(str(resolve_artifact(cell["raw_image"])))
        if image is None:
            continue
        if image.shape[1] != tile_width or image.shape[0] != tile_height:
            image = cv2.resize(image, (tile_width, tile_height), interpolation=cv2.INTER_AREA)

        x_px = int(round((float(cell.get("x_mm", 0.0)) - float(origin["x"])) * px_per_mm_x))
        y_px = int(round((float(cell.get("y_mm", 0.0)) - float(origin["y"])) * px_per_mm_y))
        dst_x1 = max(0, x_px)
        dst_y1 = max(0, y_px)
        dst_x2 = min(canvas_width, x_px + tile_width)
        dst_y2 = min(canvas_height, y_px + tile_height)
        if dst_x2 <= dst_x1 or dst_y2 <= dst_y1:
            continue
        src_x1 = dst_x1 - x_px
        src_y1 = dst_y1 - y_px
        src_x2 = src_x1 + (dst_x2 - dst_x1)
        src_y2 = src_y1 + (dst_y2 - dst_y1)
        accum[dst_y1:dst_y2, dst_x1:dst_x2] += image[src_y1:src_y2, src_x1:src_x2].astype(np.float32)
        weights[dst_y1:dst_y2, dst_x1:dst_x2] += 1.0
        placed_cells.append(
            {
                "row": cell.get("row"),
                "col": cell.get("col"),
                "x_px": dst_x1,
                "y_px": dst_y1,
                "width_px": dst_x2 - dst_x1,
                "height_px": dst_y2 - dst_y1,
                "raw_image": cell.get("raw_image"),
                "annotated_image": cell.get("annotated_image"),
            }
        )

    safe_weights = np.maximum(weights, 1.0)
    mosaic = np.clip(accum / safe_weights, 0, 255).astype(np.uint8)
    return mosaic, geometry, placed_cells


def _is_pixel_restore_manifest(manifest: dict[str, Any]) -> bool:
    if manifest.get("operation_mode") != "simulation":
        return False
    cells = [cell for cell in manifest.get("cells", []) if cell.get("raw_image")]
    return bool(cells) and all(
        (cell.get("simulation_crop") or {}).get("pixel_geometry") == "source_restore"
        for cell in cells
    )


def _build_pixel_restored_mosaic_image(manifest: dict[str, Any]) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    cells = [cell for cell in manifest.get("cells", []) if cell.get("raw_image")]
    if not cells:
        raise CameraTriggerError("No captured raw images found in scan manifest.")

    first_crop = cells[0].get("simulation_crop") or {}
    canvas_width = int(first_crop.get("source_width") or 0)
    canvas_height = int(first_crop.get("source_height") or 0)
    if canvas_width <= 0 or canvas_height <= 0:
        raise CameraTriggerError("Simulation manifest does not contain source image dimensions.")

    accum = np.zeros((canvas_height, canvas_width, 3), dtype=np.float32)
    weights = np.zeros((canvas_height, canvas_width, 1), dtype=np.float32)
    placed_cells: list[dict[str, Any]] = []

    for cell in cells:
        crop_meta = cell.get("simulation_crop") or {}
        if crop_meta.get("pixel_geometry") != "source_restore":
            raise CameraTriggerError("Simulation cell is missing source pixel crop metadata.")

        image = cv2.imread(str(resolve_artifact(cell["raw_image"])))
        if image is None:
            continue

        x1 = int(crop_meta["x1"])
        y1 = int(crop_meta["y1"])
        x2 = int(crop_meta["x2"])
        y2 = int(crop_meta["y2"])
        if x1 < 0 or y1 < 0 or x2 > canvas_width or y2 > canvas_height or x2 <= x1 or y2 <= y1:
            raise CameraTriggerError(f"Invalid simulation crop rectangle for row={cell.get('row')}, col={cell.get('col')}.")

        expected_width = x2 - x1
        expected_height = y2 - y1
        if image.shape[1] != expected_width or image.shape[0] != expected_height:
            raise CameraTriggerError(
                "Simulation crop dimensions do not match source pixel rectangle; refusing to resize during pixel restore."
            )

        accum[y1:y2, x1:x2] += image.astype(np.float32)
        weights[y1:y2, x1:x2] += 1.0
        placed_cells.append(
            {
                "row": cell.get("row"),
                "col": cell.get("col"),
                "x_px": x1,
                "y_px": y1,
                "width_px": expected_width,
                "height_px": expected_height,
                "raw_image": cell.get("raw_image"),
                "annotated_image": cell.get("annotated_image"),
                "simulation_crop": crop_meta,
            }
        )

    if not placed_cells:
        raise CameraTriggerError("No simulation crops could be placed into the mosaic.")
    if np.any(weights == 0):
        raise CameraTriggerError("Simulation pixel crops do not cover the full source image.")

    mosaic = np.clip(accum / weights, 0, 255).astype(np.uint8)
    plan = manifest.get("plan") or {}
    geometry = {
        "mode": "pixel_restore",
        "source_px": {"width": canvas_width, "height": canvas_height},
        "canvas_px": {"width": canvas_width, "height": canvas_height},
        "fov_mm": plan.get("fov_mm"),
        "step_mm": plan.get("step_mm"),
        "covered_mm": plan.get("covered_mm"),
        "note": "Simulation mosaic restores crops to their original source pixel rectangles.",
    }
    return mosaic, geometry, placed_cells


def _detect_mosaic_tiles(mosaic: Any, geometry: dict[str, Any], tile_stride_ratio: float = 0.8) -> tuple[list[dict[str, Any]], float]:
    start = time.perf_counter()
    tile_width = int(geometry["tile_px"]["width"])
    tile_height = int(geometry["tile_px"]["height"])
    height, width = mosaic.shape[:2]
    stride_x = max(1, int(tile_width * tile_stride_ratio))
    stride_y = max(1, int(tile_height * tile_stride_ratio))
    px_per_mm_x = float(geometry["px_per_mm"]["x"])
    px_per_mm_y = float(geometry["px_per_mm"]["y"])
    origin_x = float(geometry["origin_mm"]["x"])
    origin_y = float(geometry["origin_mm"]["y"])

    detections: list[dict[str, Any]] = []
    for y in _tile_starts(height, tile_height, stride_y):
        for x in _tile_starts(width, tile_width, stride_x):
            tile = mosaic[y : min(height, y + tile_height), x : min(width, x + tile_width)]
            if tile.size == 0:
                continue
            tile_detections, _, _ = yolo_detector.detect_frame(tile)
            for detection in tile_detections:
                x1, y1, x2, y2 = [int(value) for value in detection["bbox"]]
                global_bbox = [x + x1, y + y1, x + x2, y + y2]
                mapped = dict(detection)
                mapped["bbox"] = global_bbox
                mapped["bbox_mm"] = [
                    round(origin_x + global_bbox[0] / px_per_mm_x, 4),
                    round(origin_y + global_bbox[1] / px_per_mm_y, 4),
                    round(origin_x + global_bbox[2] / px_per_mm_x, 4),
                    round(origin_y + global_bbox[3] / px_per_mm_y, 4),
                ]
                mapped["source_tile"] = {"x": x, "y": y, "width": int(tile.shape[1]), "height": int(tile.shape[0])}
                detections.append(mapped)

    return _nms_detections(detections), (time.perf_counter() - start) * 1000.0


def build_scan_mosaic(scan_id: str, run_detection: bool = True) -> dict[str, Any]:
    start = time.perf_counter()
    manifest = _load_scan_manifest(scan_id)
    pixel_restore = _is_pixel_restore_manifest(manifest)
    if pixel_restore:
        mosaic, geometry, placed_cells = _build_pixel_restored_mosaic_image(manifest)
    else:
        mosaic, geometry, placed_cells = _build_mosaic_image(manifest)
    cv2 = _import_cv2()

    mosaic_dir = _scan_dir(scan_id) / "mosaic"
    mosaic_dir.mkdir(parents=True, exist_ok=True)
    raw_path = mosaic_dir / ("mosaic_raw.png" if pixel_restore else "mosaic_raw.jpg")
    annotated_path = mosaic_dir / "mosaic_annotated.jpg"
    result_json_path = mosaic_dir / "mosaic_result.json"
    mosaic_manifest_path = mosaic_dir / "mosaic_manifest.json"

    detections: list[dict[str, Any]] = []
    inference_ms = 0.0
    annotated = mosaic.copy()
    if run_detection:
        if pixel_restore:
            detections, annotated, inference_ms = yolo_detector.detect_frame(mosaic)
        else:
            detections, inference_ms = _detect_mosaic_tiles(mosaic, geometry)
            for detection in detections:
                draw_detection(annotated, detection["bbox"], str(detection.get("label") or detection.get("type")), float(detection.get("confidence", 0.0)))

    cv2.imwrite(str(raw_path), mosaic)
    cv2.imwrite(str(annotated_path), annotated)
    with result_json_path.open("w", encoding="utf-8") as file:
        json.dump(detections, file, ensure_ascii=False, indent=2)
    with mosaic_manifest_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "scan_id": scan_id,
                "created_at": _now_iso(),
                "geometry": geometry,
                "cells": placed_cells,
                "mosaic_raw": _rel(raw_path),
                "mosaic_annotated": _rel(annotated_path),
                "mosaic_result_json": _rel(result_json_path),
            },
            file,
            ensure_ascii=False,
            indent=2,
        )

    return {
        "ok": True,
        "scan_id": scan_id,
        "mosaic_raw": _rel(raw_path),
        "mosaic_annotated": _rel(annotated_path),
        "mosaic_result_json": _rel(result_json_path),
        "mosaic_manifest": _rel(mosaic_manifest_path),
        "defects": detections,
        "cell_count": len(placed_cells),
        "stitch_ms": round((time.perf_counter() - start) * 1000.0, 2),
        "inference_ms": round(inference_ms, 2),
        "geometry": geometry,
    }


def detect_tiled_image(
    image_path: Path,
    annotated_path: Path,
    json_path: Path,
    tile_size: int | list[int] | tuple[int, ...] = PHONE_TILE_SIZES,
    imgsz: int | list[int] | tuple[int, ...] | None = PHONE_IMGSZS,
    overlap: float = PHONE_TILE_OVERLAP,
    compatible_result_image: Path = RESULT_IMAGE,
    compatible_result_json: Path = RESULT_JSON,
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    start = time.perf_counter()
    cv2 = _import_cv2()
    image = cv2.imread(str(image_path))
    if image is None:
        raise CameraTriggerError(f"Could not read image for tiled detection: {image_path}")

    height, width = image.shape[:2]
    overlap = max(0.0, min(0.9, float(overlap)))
    if isinstance(tile_size, (list, tuple, set)):
        requested_tile_sizes = [int(value) for value in tile_size]
    else:
        requested_tile_sizes = [int(tile_size)]
    tile_sizes: list[int] = []
    for requested in requested_tile_sizes:
        normalized = max(32, requested)
        if normalized not in tile_sizes:
            tile_sizes.append(normalized)
    if imgsz is None:
        requested_imgszs = [int(yolo_detector.imgsz)]
    elif isinstance(imgsz, (list, tuple, set)):
        requested_imgszs = [int(value) for value in imgsz]
    else:
        requested_imgszs = [int(imgsz)]
    normalized_requested_imgszs: list[int] = []
    for requested in requested_imgszs:
        normalized = max(32, requested)
        if normalized not in normalized_requested_imgszs:
            normalized_requested_imgszs.append(normalized)

    if hasattr(yolo_detector, "compatible_imgszs"):
        imgszs, skipped_imgszs, fixed_onnx_imgsz = yolo_detector.compatible_imgszs(normalized_requested_imgszs)
    else:
        imgszs, skipped_imgszs, fixed_onnx_imgsz = normalized_requested_imgszs, [], None

    profile_results: list[dict[str, Any]] = []
    tile_strides: dict[int, int] = {}
    for current_tile_size in tile_sizes:
        stride = max(1, int(round(current_tile_size * (1.0 - overlap))))
        tile_strides[current_tile_size] = stride
        xs = _tile_starts(width, current_tile_size, stride)
        ys = _tile_starts(height, current_tile_size, stride)
        for current_imgsz in imgszs:
            raw_detections: list[dict[str, Any]] = []
            tile_count = 0
            profile_error: str | None = None
            try:
                for y in ys:
                    for x in xs:
                        crop = image[y : min(height, y + current_tile_size), x : min(width, x + current_tile_size)]
                        if crop.size == 0:
                            continue
                        tile_count += 1
                        tile_detections, _, _ = yolo_detector.detect_frame(crop, imgsz=current_imgsz)
                        for detection in tile_detections:
                            x1, y1, x2, y2 = [int(value) for value in detection.get("bbox", [0, 0, 0, 0])]
                            global_bbox = [
                                max(0, min(width, x + x1)),
                                max(0, min(height, y + y1)),
                                max(0, min(width, x + x2)),
                                max(0, min(height, y + y2)),
                            ]
                            if global_bbox[2] <= global_bbox[0] or global_bbox[3] <= global_bbox[1]:
                                continue
                            mapped = dict(detection)
                            mapped["bbox"] = global_bbox
                            mapped["source_tile"] = {
                                "x": int(x),
                                "y": int(y),
                                "width": int(crop.shape[1]),
                                "height": int(crop.shape[0]),
                                "tile_size": int(current_tile_size),
                                "imgsz": int(current_imgsz),
                            }
                            raw_detections.append(mapped)
            except Exception as exc:
                raw_detections = []
                profile_error = str(exc)

            profile_detections = _nms_detections(raw_detections)
            best_confidence = max((float(item.get("confidence", 0.0)) for item in profile_detections), default=0.0)
            profile_result = {
                "tile_size": int(current_tile_size),
                "imgsz": int(current_imgsz),
                "tile_stride": int(stride),
                "tile_count": int(tile_count),
                "raw_detection_count": len(raw_detections),
                "nms_detection_count": len(profile_detections),
                "best_confidence": round(best_confidence, 4),
                "detections": profile_detections,
            }
            if profile_error:
                profile_result["error"] = profile_error
            profile_results.append(profile_result)

    selected_profile = max(
        profile_results,
        key=lambda item: (float(item["best_confidence"]), int(item["nms_detection_count"])),
        default={
            "tile_size": tile_sizes[0],
            "imgsz": imgszs[0],
            "tile_stride": tile_strides[tile_sizes[0]],
            "tile_count": 0,
            "raw_detection_count": 0,
            "nms_detection_count": 0,
            "best_confidence": 0.0,
            "detections": [],
        },
    )
    fusion = _fuse_profile_detections(profile_results)
    detections = list(fusion["detections"])
    annotated = image.copy()
    for detection in detections:
        decision = str(detection.get("decision", "confirmed"))
        color = (0, 180, 0) if decision == "confirmed" else (0, 170, 255)
        label = str(detection.get("label") or detection.get("type"))
        if decision == "suspected":
            label = f"suspected {label}"
        draw_detection(
            annotated,
            detection["bbox"],
            label,
            float(detection.get("confidence", 0.0)),
            color=color,
        )

    annotated_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    compatible_result_image.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(annotated_path), annotated)
    cv2.imwrite(str(compatible_result_image), annotated)
    for target in (json_path, compatible_result_json):
        with target.open("w", encoding="utf-8") as file:
            json.dump(detections, file, ensure_ascii=False, indent=2)

    details = {
        "strategy": "profile_ensemble" if len(profile_results) > 1 else "tiled",
        "selection_metric": "multi_profile_evidence",
        "selected_profile": {
            "tile_size": int(selected_profile["tile_size"]),
            "imgsz": int(selected_profile["imgsz"]),
            "tile_stride": int(selected_profile["tile_stride"]),
            "tile_count": int(selected_profile["tile_count"]),
            "best_confidence": float(selected_profile["best_confidence"]),
        },
        "tile_size": int(selected_profile["tile_size"]),
        "tile_sizes": tile_sizes,
        "imgsz": int(selected_profile["imgsz"]),
        "imgszs": imgszs,
        "requested_imgszs": normalized_requested_imgszs,
        "skipped_imgszs": skipped_imgszs,
        "onnx_input_imgsz": fixed_onnx_imgsz,
        "imgsz_adjusted_for_backend": bool(skipped_imgszs),
        "tile_overlap": overlap,
        "tile_stride": int(selected_profile["tile_stride"]),
        "tile_strides": {str(size): stride for size, stride in tile_strides.items()},
        "tile_count": sum(int(profile["tile_count"]) for profile in profile_results),
        "selected_tile_count": int(selected_profile["tile_count"]),
        "raw_detection_count": sum(int(profile["raw_detection_count"]) for profile in profile_results),
        "nms_detection_count": len(detections),
        "confirmed_count": len(fusion["confirmed_defects"]),
        "suspected_count": len(fusion["suspected_defects"]),
        "discarded_count": len(fusion["discarded_defects"]),
        "confirmed_defects": fusion["confirmed_defects"],
        "suspected_defects": fusion["suspected_defects"],
        "fusion_rules": fusion["fusion_rules"],
        "profile_summaries": [
            {
                "tile_size": int(profile["tile_size"]),
                "imgsz": int(profile["imgsz"]),
                "tile_stride": int(profile["tile_stride"]),
                "tile_count": int(profile["tile_count"]),
                "raw_detection_count": int(profile["raw_detection_count"]),
                "nms_detection_count": int(profile["nms_detection_count"]),
                "best_confidence": float(profile["best_confidence"]),
                "error": profile.get("error"),
            }
            for profile in profile_results
        ],
    }
    return detections, (time.perf_counter() - start) * 1000.0, details


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
    target_name = safe_name
    original_size = len(content)
    preprocess: list[str] = []
    image_width: int | None = None
    image_height: int | None = None
    original_width: int | None = None
    original_height: int | None = None

    if SIMULATION_MAX_DIMENSION > 0:
        cv2 = _import_cv2()
        import numpy as np  # type: ignore[import-not-found]

        encoded = np.frombuffer(content, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise CameraTriggerError("Uploaded simulation image could not be decoded.")

        original_height, original_width = image.shape[:2]
        max_side = max(original_width, original_height)
        if max_side > SIMULATION_MAX_DIMENSION:
            scale = SIMULATION_MAX_DIMENSION / max_side
            image_width = max(1, int(round(original_width * scale)))
            image_height = max(1, int(round(original_height * scale)))
            image = cv2.resize(image, (image_width, image_height), interpolation=cv2.INTER_AREA)
            preprocess.append(f"resize_max_{SIMULATION_MAX_DIMENSION}")
        else:
            image_height, image_width = image.shape[:2]

        target_name = f"{Path(safe_name).stem}.jpg"
        target = SIMULATION_IMPORT_DIR / f"{timestamp}_{target_name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        quality = max(1, min(100, SIMULATION_JPEG_QUALITY))
        if not cv2.imwrite(str(target), image, [int(cv2.IMWRITE_JPEG_QUALITY), quality]):
            raise CameraTriggerError(f"Failed to write simulation image: {target}")
        preprocess.append("jpeg_reencode")
    else:
        target = SIMULATION_IMPORT_DIR / f"{timestamp}_{safe_name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

        try:
            cv2 = _import_cv2()
            image = cv2.imread(str(target))
            if image is not None:
                image_height, image_width = image.shape[:2]
                original_height, original_width = image_height, image_width
        except Exception:
            pass

    payload = {
        "ok": True,
        "name": target_name,
        "original_name": safe_name,
        "path": _rel(target),
        "original_size_bytes": original_size,
        "size_bytes": target.stat().st_size,
        "board_safe_mode": BOARD_SAFE_MODE,
        "max_dimension": SIMULATION_MAX_DIMENSION,
        "preprocess": preprocess,
    }
    if original_width is not None and original_height is not None:
        payload["original_width"] = int(original_width)
        payload["original_height"] = int(original_height)
    if image_width is not None and image_height is not None:
        payload["width"] = int(image_width)
        payload["height"] = int(image_height)
        payload["resized"] = bool(
            original_width is not None
            and original_height is not None
            and (int(image_width) != int(original_width) or int(image_height) != int(original_height))
        )
    return payload


def _phone_upload_path(upload_id: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z_-]{8,80}", upload_id or ""):
        raise CameraTriggerError("Invalid phone upload id.")
    path = (PHONE_UPLOAD_DIR / upload_id).resolve()
    if not _is_within(path, PHONE_UPLOAD_DIR.resolve()):
        raise CameraTriggerError("Phone upload path is outside upload root.")
    return path


def _phone_metadata_path(upload_id: str) -> Path:
    return _phone_upload_path(upload_id) / "metadata.json"


def _write_phone_metadata(metadata: dict[str, Any]) -> None:
    upload_id = str(metadata["upload_id"])
    metadata_path = _phone_metadata_path(upload_id)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)


def _read_phone_metadata(upload_id: str) -> dict[str, Any]:
    metadata_path = _phone_metadata_path(upload_id)
    if not metadata_path.exists():
        raise CameraTriggerError(f"Phone upload does not exist: {upload_id}")
    with metadata_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _phone_upload_public_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    payload = dict(metadata)
    payload["ok"] = True
    return payload


def save_phone_upload(filename: str, content: bytes) -> dict[str, Any]:
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SIMULATION_IMAGE_SUFFIXES:
        raise CameraTriggerError(
            f"Unsupported phone image type: {suffix or '(none)'}. "
            f"Allowed: {', '.join(sorted(SIMULATION_IMAGE_SUFFIXES))}"
        )
    if not content:
        raise CameraTriggerError("Uploaded phone image is empty.")
    if len(content) > PHONE_MAX_UPLOAD_BYTES:
        raise CameraTriggerError(f"Uploaded phone image exceeds {PHONE_MAX_UPLOAD_BYTES // 1024 // 1024}MB.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_id = f"{timestamp}_{os.urandom(4).hex()}"
    upload_dir = _phone_upload_path(upload_id)
    incoming_path = upload_dir / f"upload{suffix}"
    raw_path = upload_dir / "raw.jpg"
    upload_dir.mkdir(parents=True, exist_ok=True)
    incoming_path.write_bytes(content)

    cv2 = _import_cv2()
    image = cv2.imread(str(incoming_path))
    if image is None:
        try:
            shutil.rmtree(upload_dir)
        except OSError:
            pass
        raise CameraTriggerError("Uploaded phone image could not be decoded.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if not cv2.imwrite(str(raw_path), image):
        try:
            shutil.rmtree(upload_dir)
        except OSError:
            pass
        raise CameraTriggerError("Uploaded phone image could not be converted to grayscale.")
    try:
        incoming_path.unlink(missing_ok=True)
    except OSError:
        pass

    height, width = image.shape[:2]
    metadata = {
        "upload_id": upload_id,
        "name": safe_name,
        "raw_image": _rel(raw_path),
        "size_bytes": raw_path.stat().st_size,
        "width": int(width),
        "height": int(height),
        "color_mode": "grayscale",
        "preprocess": ["phone_color_to_gray"],
        "uploaded_at": _now_iso(),
        "status": "uploaded",
        "source": "phone",
    }
    _write_phone_metadata(metadata)
    return _phone_upload_public_payload(metadata)


def list_phone_uploads() -> list[dict[str, Any]]:
    if not PHONE_UPLOAD_DIR.exists():
        return []
    uploads: list[dict[str, Any]] = []
    for child in PHONE_UPLOAD_DIR.iterdir():
        if not child.is_dir():
            continue
        metadata_path = child / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            with metadata_path.open("r", encoding="utf-8") as file:
                uploads.append(_phone_upload_public_payload(json.load(file)))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(uploads, key=lambda item: str(item.get("uploaded_at", "")), reverse=True)


def get_phone_upload(upload_id: str) -> dict[str, Any]:
    return _phone_upload_public_payload(_read_phone_metadata(upload_id))


def detect_phone_upload(upload_id: str, detection_profile: str | None = None) -> dict[str, Any]:
    metadata = _read_phone_metadata(upload_id)
    raw_path = resolve_artifact(metadata["raw_image"])
    upload_dir = _phone_upload_path(upload_id)
    annotated_path = upload_dir / "annotated.jpg"
    result_json_path = upload_dir / "result.json"

    profile_config = phone_detection_profile_config(detection_profile)
    detections, inference_ms, detection_details = detect_tiled_image(
        raw_path,
        annotated_path,
        result_json_path,
        tile_size=profile_config["tile_sizes"],
        imgsz=profile_config["imgszs"],
        overlap=float(profile_config["overlap"]),
    )
    detection_details["phone_detection_profile"] = profile_config["profile"]
    detection_details["phone_detection_profile_label"] = profile_config["label"]
    metadata.update(
        {
            "status": "detected",
            "detected_at": _now_iso(),
            "annotated_image": _rel(annotated_path),
            "result_json": _rel(result_json_path),
            "defects": detections,
            "defect_count": len(detections),
            "confirmed_defects": detection_details.get("confirmed_defects", detections),
            "suspected_defects": detection_details.get("suspected_defects", []),
            "confirmed_count": int(detection_details.get("confirmed_count", len(detections))),
            "suspected_count": int(detection_details.get("suspected_count", 0)),
            "detection_profile": profile_config["profile"],
            "detection_profile_label": profile_config["label"],
            "inference_ms": round(inference_ms, 2),
            "detection_strategy": detection_details["strategy"],
            "detection_details": detection_details,
            "operation_mode": "phone",
        }
    )
    _write_phone_metadata(metadata)
    return _phone_upload_public_payload(metadata)


def detect_phone_upload_local_fallback(
    upload_id: str,
    detection_profile: str | None = None,
    fallback_reason: str = "no_phone_worker_online",
) -> dict[str, Any]:
    result = detect_phone_upload(upload_id, detection_profile)
    result["inference_location"] = "local_fallback"
    result["fallback_reason"] = fallback_reason
    result["local_fallback"] = True
    return result


def store_phone_detection_result(
    upload_id: str,
    result: dict[str, Any],
    detection_profile: str | None = None,
    detection_profile_label: str | None = None,
) -> dict[str, Any]:
    metadata = _read_phone_metadata(upload_id)
    detections = list(result.get("defects") or [])
    metadata.update(
        {
            "status": "detected",
            "detected_at": _now_iso(),
            "annotated_image": result.get("annotated_image"),
            "result_json": result.get("result_json"),
            "defects": detections,
            "defect_count": len(detections),
            "confirmed_defects": result.get("confirmed_defects", detections),
            "suspected_defects": result.get("suspected_defects", []),
            "confirmed_count": int(result.get("confirmed_count", len(detections))),
            "suspected_count": int(result.get("suspected_count", 0)),
            "detection_profile": detection_profile or result.get("detection_profile") or "fast",
            "detection_profile_label": detection_profile_label or result.get("detection_profile_label") or "快速模式",
            "inference_ms": result.get("inference_ms"),
            "detection_strategy": result.get("detection_strategy") or "phone_first",
            "detection_details": result.get("detection_details") or {},
            "inference_location": result.get("inference_location"),
            "fallback_reason": result.get("fallback_reason"),
            "local_fallback": bool(result.get("local_fallback")),
            "operation_mode": "phone",
        }
    )
    _write_phone_metadata(metadata)
    return _phone_upload_public_payload(metadata)


def delete_phone_upload(upload_id: str) -> dict[str, Any]:
    upload_dir = _phone_upload_path(upload_id)
    if not upload_dir.exists():
        raise CameraTriggerError(f"Phone upload does not exist: {upload_id}")
    shutil.rmtree(upload_dir)
    return {"ok": True, "upload_id": upload_id, "deleted": True}


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
        result_json_path = _cell_dir(scan_id, row, col) / "result.json"
        result_json_path.parent.mkdir(parents=True, exist_ok=True)
        with result_json_path.open("w", encoding="utf-8") as file:
            json.dump([], file, ensure_ascii=False, indent=2)
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
        "result_json": _rel(result_json_path),
        "capture_ms": round(capture_ms, 2),
        "source_image": _rel(source_path),
        "simulation_crop": crop_meta,
    }


def _simulation_pixel_crop_rect(plan: dict[str, Any], source_width: int, source_height: int, row: int, col: int) -> tuple[int, int, int, int]:
    fov = plan.get("fov_mm") or {"width": 30.0, "height": 30.0}
    step = plan.get("step_mm") or {"x": fov.get("width", 30.0), "y": fov.get("height", 30.0)}
    covered = plan.get("covered_mm") or {}

    fov_w = max(float(fov.get("width", 30.0)), 1e-6)
    fov_h = max(float(fov.get("height", 30.0)), 1e-6)
    step_x = max(float(step.get("x", fov_w)), 1e-6)
    step_y = max(float(step.get("y", fov_h)), 1e-6)
    cols = max(int(plan.get("cols") or 1), 1)
    rows = max(int(plan.get("rows") or 1), 1)
    covered_w = max(float(covered.get("length") or ((cols - 1) * step_x + fov_w)), fov_w)
    covered_h = max(float(covered.get("width") or ((rows - 1) * step_y + fov_h)), fov_h)

    x_start_mm = min(col * step_x, covered_w)
    y_start_mm = min(row * step_y, covered_h)
    x_end_mm = min(x_start_mm + fov_w, covered_w)
    y_end_mm = min(y_start_mm + fov_h, covered_h)

    x1 = int(round((x_start_mm / covered_w) * source_width))
    y1 = int(round((y_start_mm / covered_h) * source_height))
    x2 = int(round((x_end_mm / covered_w) * source_width))
    y2 = int(round((y_end_mm / covered_h) * source_height))

    x1 = max(0, min(source_width - 1, x1))
    y1 = max(0, min(source_height - 1, y1))
    x2 = max(x1 + 1, min(source_width, x2))
    y2 = max(y1 + 1, min(source_height, y2))
    return x1, y1, x2, y2


def _write_simulation_crop(
    source_path: Path,
    row: int,
    col: int,
    scan_id: str,
    plan: dict[str, Any],
) -> tuple[Path, float, dict[str, Any]]:
    start = time.perf_counter()
    cv2 = _import_cv2()
    image = cv2.imread(str(source_path))
    if image is None:
        raise CameraTriggerError(f"Could not read simulation image: {source_path}")

    height, width = image.shape[:2]
    fov = plan.get("fov_mm") or {"width": 30.0, "height": 30.0}
    step = plan.get("step_mm") or {"x": fov.get("width", 30.0), "y": fov.get("height", 30.0)}
    origin = plan.get("origin_mm") or {"x": 0.0, "y": 0.0}

    fov_w = float(fov.get("width", 30.0))
    fov_h = float(fov.get("height", 30.0))
    step_x = float(step.get("x", fov_w))
    step_y = float(step.get("y", fov_h))
    origin_x = float(origin.get("x", 0.0))
    origin_y = float(origin.get("y", 0.0))
    x_mm = origin_x + col * step_x
    y_mm = origin_y + row * step_y
    x1, y1, x2, y2 = _simulation_pixel_crop_rect(plan, width, height, row, col)

    crop = image[y1:y2, x1:x2].copy()
    if crop.size == 0:
        raise CameraTriggerError(f"Simulation crop is empty for row={row}, col={col}.")

    cell_dir = _cell_dir(scan_id, row, col)
    raw_path = cell_dir / "raw.png"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(raw_path), crop):
        raise CameraTriggerError(f"Failed to write simulation raw image: {raw_path}")
    if not cv2.imwrite(str(DEFAULT_IMAGE), crop):
        raise CameraTriggerError(f"Failed to write latest simulation image: {DEFAULT_IMAGE}")

    crop_meta = {
        "source_width": int(width),
        "source_height": int(height),
        "pixel_geometry": "source_restore",
        "x_mm": round(x_mm, 4),
        "y_mm": round(y_mm, 4),
        "fov_width_mm": round(fov_w, 4),
        "fov_height_mm": round(fov_h, 4),
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
        "inference_location": "local_fallback",
        "fallback_reason": "no_phone_worker_online",
        "local_fallback": True,
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


def set_camera_source(source: str) -> dict[str, Any]:
    value = str(source or "").strip()
    if not value:
        raise CameraTriggerError("Camera source cannot be empty.")
    if any(ch in value for ch in "\r\n"):
        raise CameraTriggerError("Camera source must be a single line.")
    CAMERA_SOURCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAMERA_SOURCE_FILE.write_text(
        "# Camera source selected from the AOI web UI.\n"
        "# Examples: 0, 1, /dev/video0, rtsp://...\n"
        f"{value}\n",
        encoding="utf-8",
    )
    camera_manager.close()
    status = camera_manager.status(probe=False)
    env_source = os.getenv("LOONGSON_AOI_CAMERA_SOURCE")
    return {
        "ok": True,
        "requested_source": value,
        "active_source": status.get("source"),
        "source_origin": status.get("source_origin"),
        "config_file": str(CAMERA_SOURCE_FILE),
        "env_override": bool(env_source and env_source.strip()),
        "status": status,
    }


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
    inference_handler: Callable[[Any, Path], Awaitable[tuple[Any, Any, float] | tuple[Any, Any, float, dict[str, Any]]]] | None = None,
    on_detection: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_error: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> None:
    await video_detection_session.run(
        scan_id,
        stop_event,
        inference_fps=inference_fps,
        preview_fps=preview_fps,
        inference_handler=inference_handler,
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
