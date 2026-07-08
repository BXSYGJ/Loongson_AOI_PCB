import asyncio
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
import zipfile
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


ASCII_LABEL_ALIASES = {
    "short": "short_circuit",
    "short_circuit": "short_circuit",
    "dry_solder_joint": "dry_solder_joint",
    "improper_installation": "improper_installation",
    "pcb_damage": "pcb_damage",
    "insufficient_solder": "insufficient_solder",
    "reverse_component": "reverse_component",
    "excess_solder": "excess_solder",
    "missing_hole": "missing_hole",
    "mouse_bite": "mouse_bite",
    "open_circuit": "open_circuit",
    "spur": "spur",
    "spurious_copper": "spurious_copper",
    "missing_component": "missing_component",
}


def _is_ascii_text(text: str) -> bool:
    return all(32 <= ord(char) <= 126 for char in text)


def _normalize_label_text(label: Any, ascii_label: str | None = None) -> str:
    text = str(label or ascii_label or "defect").strip() or "defect"
    for prefix in ("suspected ", "conflict "):
        if text.startswith(prefix):
            return f"{prefix}{_normalize_label_text(text[len(prefix):], ascii_label)}"
    ascii_candidate = str(ascii_label or "").strip()
    if ascii_candidate and _is_ascii_text(ascii_candidate):
        return ascii_candidate
    if _is_ascii_text(text):
        return ASCII_LABEL_ALIASES.get(text, text)
    cleaned = re.sub(r"[^\x20-\x7E]+", "", text).strip()
    return cleaned or "defect"


def _format_annotation_text(label: Any, confidence: float, ascii_label: str | None = None) -> str:
    return f"{_normalize_label_text(label, ascii_label)} {confidence:.2f}"


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
PHONE_CONFIRM_CONF = _env_float("LOONGSON_AOI_PHONE_CONFIRM_CONF", 0.3)
PHONE_MULTI_CONFIRM_CONF = _env_float("LOONGSON_AOI_PHONE_MULTI_CONFIRM_CONF", 0.3)
PHONE_SUSPECTED_CONF = _env_float("LOONGSON_AOI_PHONE_SUSPECTED_CONF", 0.2)
PHONE_MULTI_SUSPECTED_CONF = _env_float("LOONGSON_AOI_PHONE_MULTI_SUSPECTED_CONF", 0.1)
MOSAIC_FUSION_IOU = _env_float("LOONGSON_AOI_MOSAIC_FUSION_IOU", 0.45)
MOSAIC_MIN_RETAINED_RATIO = _env_float("LOONGSON_AOI_MOSAIC_MIN_RETAINED_RATIO", 0.3)
MOSAIC_CONFLICT_CONF = _env_float("LOONGSON_AOI_MOSAIC_CONFLICT_CONF", 0.25)
VIDEO_DEFAULT_INFERENCE_FPS = _env_float("LOONGSON_AOI_VIDEO_INFERENCE_FPS", 5.0)
VIDEO_DEFAULT_PREVIEW_FPS = _env_float("LOONGSON_AOI_VIDEO_PREVIEW_FPS", 8.0)
CAMERA_SOURCE_FILE = Path(os.getenv("LOONGSON_AOI_CAMERA_SOURCE_FILE", ROOT / "camera_source.txt")).expanduser()
DEFAULT_CAMERA_SOURCE = "0" if "loong" in platform.machine().lower() else "1"
ONNX_INPUT_IMGSZ = _env_int("LOONGSON_AOI_ONNX_INPUT_IMGSZ", 0)
OPENCV_THREADS = _env_int("LOONGSON_AOI_OPENCV_THREADS", 0)
FIXED_STEP_X_PX = _env_int("LOONGSON_AOI_FIXED_STEP_X_PX", 0)
FIXED_STEP_Y_PX = _env_int("LOONGSON_AOI_FIXED_STEP_Y_PX", 0)
FIXED_STEP_X_MM = _env_float("LOONGSON_AOI_FIXED_SCAN_STEP_X_MM", 30.0)
FIXED_STEP_Y_MM = _env_float("LOONGSON_AOI_FIXED_SCAN_STEP_Y_MM", 18.0)
CAMERA_FOV_LONG_MM = _env_float("LOONGSON_AOI_CAMERA_FOV_LONG_MM", 39.0)
CAMERA_FOV_SHORT_MM = _env_float("LOONGSON_AOI_CAMERA_FOV_SHORT_MM", 0.0)
STITCH_AXIS_MAPPING = os.getenv(
    "LOONGSON_AOI_STITCH_AXIS_MAPPING",
    "slide_x_image_x_slide_y_image_y",
).strip().lower()
STITCH_MIRROR_X = _env_bool("LOONGSON_AOI_STITCH_MIRROR_X", True)


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
        self.conf = _env_float("LOONGSON_AOI_YOLO_CONF", 0.3)
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

        if ONNX_INPUT_IMGSZ > 0:
            self._onnx_input_imgsz = ONNX_INPUT_IMGSZ
            return self._onnx_input_imgsz

        probed_imgsz = self._probe_onnx_input_imgsz()
        if probed_imgsz:
            self._onnx_input_imgsz = probed_imgsz
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
                    "label": defect_type,
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
                    "label": defect_type,
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
                    ascii_label=str(detection.get("type") or ""),
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
                    ascii_label=str(detection.get("type") or ""),
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
    ascii_label: str | None = None,
) -> None:
    cv2 = _import_cv2()
    x1, y1, x2, y2 = bbox
    text = _format_annotation_text(label, confidence, ascii_label)
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


def _bbox_area(bbox: list[float] | list[int]) -> float:
    if len(bbox) != 4:
        return 0.0
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


def _normalize_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _clip_bbox(bbox: list[float], clip_rect: list[float]) -> list[float] | None:
    clipped = [
        max(float(bbox[0]), float(clip_rect[0])),
        max(float(bbox[1]), float(clip_rect[1])),
        min(float(bbox[2]), float(clip_rect[2])),
        min(float(bbox[3]), float(clip_rect[3])),
    ]
    return clipped if _bbox_area(clipped) > 0 else None


def _round_bbox(bbox: list[float]) -> list[int]:
    return [int(round(value)) for value in bbox]


def _mirror_bbox_x(bbox: list[float], image_width: float) -> list[float]:
    return [
        max(0.0, image_width - float(bbox[2])),
        float(bbox[1]),
        min(image_width, image_width - float(bbox[0])),
        float(bbox[3]),
    ]


def _cell_key(row: Any, col: Any) -> str:
    return f"{row}:{col}"


def _fuse_cell_detections_for_mosaic(
    manifest: dict[str, Any],
    placed_cells: list[dict[str, Any]],
    board_crop: dict[str, Any],
) -> dict[str, Any]:
    placements = {
        _cell_key(cell.get("row"), cell.get("col")): cell
        for cell in placed_cells
        if cell.get("row") is not None and cell.get("col") is not None
    }
    crop_bbox = board_crop.get("bbox_px") if board_crop.get("status") == "ok" else None
    crop_rect = None
    coordinate_space = "mosaic"
    if isinstance(crop_bbox, list) and len(crop_bbox) == 4:
        crop_x, crop_y, crop_w, crop_h = [float(value) for value in crop_bbox]
        if crop_w > 0 and crop_h > 0:
            crop_rect = [crop_x, crop_y, crop_x + crop_w, crop_y + crop_h]
            coordinate_space = "board_crop"

    candidates: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for cell in manifest.get("cells", []):
        row = cell.get("row")
        col = cell.get("col")
        placement = placements.get(_cell_key(row, col))
        if placement is None:
            continue
        src_crop = placement.get("source_crop_px") or [0, 0, placement.get("width_px", 0), placement.get("height_px", 0)]
        if not isinstance(src_crop, list) or len(src_crop) != 4:
            src_crop = [0, 0, placement.get("width_px", 0), placement.get("height_px", 0)]
        src_x, src_y, src_w, src_h = [float(value) for value in src_crop]
        source_rect = [src_x, src_y, src_x + src_w, src_y + src_h]
        dst_x = float(placement.get("x_px", 0))
        dst_y = float(placement.get("y_px", 0))
        full_tile = placement.get("full_tile_px") or {}
        tile_width = float(full_tile.get("width") or placement.get("width_px") or src_w)
        mirror_x = bool(placement.get("mirror_x"))

        for index, defect in enumerate(cell.get("defects") or []):
            if not isinstance(defect, dict):
                continue
            bbox = _normalize_bbox(defect.get("bbox"))
            if bbox is None:
                discarded.append({"row": row, "col": col, "index": index, "reason": "invalid_bbox", "defect": defect})
                continue
            bbox_area = _bbox_area(bbox)
            mapped_bbox = _mirror_bbox_x(bbox, tile_width) if mirror_x else bbox
            source_bbox = _clip_bbox(mapped_bbox, source_rect)
            if source_bbox is None:
                discarded.append({"row": row, "col": col, "index": index, "reason": "outside_retained_source_crop", "defect": defect})
                continue
            retained_ratio = _bbox_area(source_bbox) / max(bbox_area, 1e-6)
            if retained_ratio < MOSAIC_MIN_RETAINED_RATIO:
                discarded.append(
                    {
                        "row": row,
                        "col": col,
                        "index": index,
                        "reason": "mostly_discarded_overlap",
                        "retained_ratio": round(retained_ratio, 4),
                        "defect": defect,
                    }
                )
                continue

            mosaic_bbox = [
                dst_x + source_bbox[0] - src_x,
                dst_y + source_bbox[1] - src_y,
                dst_x + source_bbox[2] - src_x,
                dst_y + source_bbox[3] - src_y,
            ]
            output_bbox = mosaic_bbox
            board_retained_ratio = 1.0
            if crop_rect is not None:
                cropped_bbox = _clip_bbox(mosaic_bbox, crop_rect)
                if cropped_bbox is None:
                    discarded.append({"row": row, "col": col, "index": index, "reason": "outside_board_crop", "defect": defect})
                    continue
                board_retained_ratio = _bbox_area(cropped_bbox) / max(_bbox_area(mosaic_bbox), 1e-6)
                if board_retained_ratio < MOSAIC_MIN_RETAINED_RATIO:
                    discarded.append(
                        {
                            "row": row,
                            "col": col,
                            "index": index,
                            "reason": "mostly_outside_board_crop",
                            "retained_ratio": round(board_retained_ratio, 4),
                            "defect": defect,
                        }
                    )
                    continue
                output_bbox = [cropped_bbox[0] - crop_rect[0], cropped_bbox[1] - crop_rect[1], cropped_bbox[2] - crop_rect[0], cropped_bbox[3] - crop_rect[1]]

            candidate = dict(defect)
            candidate["bbox"] = _round_bbox(output_bbox)
            candidate["mosaic_bbox"] = _round_bbox(mosaic_bbox)
            candidate["original_bbox"] = _round_bbox(bbox)
            candidate["mosaic_source_bbox"] = _round_bbox(mapped_bbox)
            candidate["source_mirror_x"] = mirror_x
            candidate["source"] = "cell_mosaic_candidate"
            candidate["coordinate_space"] = coordinate_space
            candidate["source_cell"] = {
                "row": row,
                "col": col,
                "x_mm": cell.get("x_mm"),
                "y_mm": cell.get("y_mm"),
                "raw_image": cell.get("raw_image"),
                "annotated_image": cell.get("annotated_image"),
            }
            candidate["retained_ratio"] = round(min(retained_ratio, board_retained_ratio), 4)
            candidates.append(candidate)

    clusters: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("confidence", 0.0)), reverse=True):
        bbox = candidate.get("bbox") or [0, 0, 0, 0]
        best_cluster: dict[str, Any] | None = None
        best_iou = 0.0
        for cluster in clusters:
            cluster_iou = max(
                _bbox_iou(bbox, item.get("bbox") or [0, 0, 0, 0])
                for item in cluster["items"]
            )
            if cluster_iou >= MOSAIC_FUSION_IOU and cluster_iou > best_iou:
                best_iou = cluster_iou
                best_cluster = cluster
        if best_cluster is None:
            clusters.append({"items": [candidate]})
        else:
            best_cluster["items"].append(candidate)

    fused: list[dict[str, Any]] = []
    low_confidence_discarded: list[dict[str, Any]] = []
    for cluster in clusters:
        items = sorted(cluster["items"], key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        by_type: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_type.setdefault(str(item.get("type") or item.get("label") or ""), []).append(item)
        primary_type, primary_items = max(
            by_type.items(),
            key=lambda pair: (max(float(item.get("confidence", 0.0)) for item in pair[1]), len(pair[1])),
        )
        primary_items = sorted(primary_items, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        best = primary_items[0]
        support_cells: list[dict[str, Any]] = []
        seen_cells: set[str] = set()
        for item in primary_items:
            source_cell = item.get("source_cell") or {}
            key = _cell_key(source_cell.get("row"), source_cell.get("col"))
            if key in seen_cells:
                continue
            seen_cells.add(key)
            support_cells.append(source_cell)

        confidences = [float(item.get("confidence", 0.0)) for item in primary_items]
        max_confidence = max(confidences, default=0.0)
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        support_count = len(support_cells)
        evidence_score = min(0.99, max_confidence + 0.05 * max(0, support_count - 1))
        if max_confidence >= PHONE_CONFIRM_CONF or (support_count >= 2 and max_confidence >= PHONE_MULTI_CONFIRM_CONF):
            decision = "confirmed"
        elif max_confidence >= PHONE_SUSPECTED_CONF or (support_count >= 2 and max_confidence >= PHONE_MULTI_SUSPECTED_CONF):
            decision = "suspected"
        else:
            decision = "discarded"

        conflict_alternatives: list[dict[str, Any]] = []
        for defect_type, type_items in by_type.items():
            if defect_type == primary_type:
                continue
            alt_best = max(type_items, key=lambda item: float(item.get("confidence", 0.0)))
            if float(alt_best.get("confidence", 0.0)) >= MOSAIC_CONFLICT_CONF:
                conflict_alternatives.append(
                    {
                        "type": alt_best.get("type"),
                        "label": alt_best.get("label") or alt_best.get("type"),
                        "confidence": round(float(alt_best.get("confidence", 0.0)), 4),
                        "bbox": alt_best.get("bbox"),
                        "mosaic_bbox": alt_best.get("mosaic_bbox"),
                        "support_count": len(type_items),
                    }
                )

        fused_item = {
            "type": best.get("type"),
            "label": best.get("label") or best.get("type"),
            "bbox": best.get("bbox"),
            "mosaic_bbox": best.get("mosaic_bbox"),
            "original_bbox": best.get("original_bbox"),
            "mosaic_source_bbox": best.get("mosaic_source_bbox"),
            "source_mirror_x": best.get("source_mirror_x"),
            "confidence": round(evidence_score, 4),
            "max_confidence": round(max_confidence, 4),
            "mean_confidence": round(mean_confidence, 4),
            "support_count": support_count,
            "support_cells": support_cells,
            "decision": decision,
            "source": "cell_mosaic_fusion",
            "coordinate_space": coordinate_space,
            "has_conflict": bool(conflict_alternatives),
            "conflict_alternatives": conflict_alternatives,
            "source_candidates": [
                {
                    "type": item.get("type"),
                    "label": item.get("label") or item.get("type"),
                    "confidence": item.get("confidence"),
                    "bbox": item.get("bbox"),
                    "mosaic_bbox": item.get("mosaic_bbox"),
                    "original_bbox": item.get("original_bbox"),
                    "mosaic_source_bbox": item.get("mosaic_source_bbox"),
                    "source_mirror_x": item.get("source_mirror_x"),
                    "source_cell": item.get("source_cell"),
                    "retained_ratio": item.get("retained_ratio"),
                }
                for item in items
            ],
        }
        if crop_rect is not None:
            fused_item["board_crop_bbox_px"] = _round_bbox(crop_rect)

        if decision == "discarded":
            low_confidence_discarded.append(fused_item)
        else:
            fused.append(fused_item)

    fused = sorted(fused, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    return {
        "detections": fused,
        "audit": {
            "strategy": "cell_to_mosaic_coordinate_fusion",
            "coordinate_space": coordinate_space,
            "fusion_rules": {
                "iou_threshold": MOSAIC_FUSION_IOU,
                "min_retained_ratio": MOSAIC_MIN_RETAINED_RATIO,
                "conflict_conf": MOSAIC_CONFLICT_CONF,
                "confirm_conf": PHONE_CONFIRM_CONF,
                "multi_confirm_conf": PHONE_MULTI_CONFIRM_CONF,
                "suspected_conf": PHONE_SUSPECTED_CONF,
                "multi_suspected_conf": PHONE_MULTI_SUSPECTED_CONF,
            },
            "candidate_count": len(candidates),
            "fused_count": len(fused),
            "discarded_count": len(discarded) + len(low_confidence_discarded),
            "candidates": candidates,
            "discarded_defects": discarded + low_confidence_discarded,
            "board_crop": board_crop,
        },
    }


def _remap_mosaic_detections_for_crop(detections: list[dict[str, Any]], board_crop: dict[str, Any]) -> list[dict[str, Any]]:
    crop_bbox = board_crop.get("bbox_px") if board_crop.get("status") == "ok" else None
    if not isinstance(crop_bbox, list) or len(crop_bbox) != 4:
        return [
            {
                **dict(detection),
                "coordinate_space": detection.get("coordinate_space") or "mosaic",
                "source": detection.get("source") or "mosaic_detection",
            }
            for detection in detections
            if isinstance(detection, dict)
        ]

    crop_x, crop_y, crop_w, crop_h = [float(value) for value in crop_bbox]
    if crop_w <= 0 or crop_h <= 0:
        return detections
    crop_rect = [crop_x, crop_y, crop_x + crop_w, crop_y + crop_h]
    remapped: list[dict[str, Any]] = []
    for detection in detections:
        if not isinstance(detection, dict):
            continue
        bbox = _normalize_bbox(detection.get("bbox"))
        if bbox is None:
            continue
        clipped = _clip_bbox(bbox, crop_rect)
        if clipped is None:
            continue
        retained_ratio = _bbox_area(clipped) / max(_bbox_area(bbox), 1e-6)
        if retained_ratio < MOSAIC_MIN_RETAINED_RATIO:
            continue
        item = dict(detection)
        item["mosaic_bbox"] = _round_bbox(bbox)
        item["bbox"] = _round_bbox([clipped[0] - crop_x, clipped[1] - crop_y, clipped[2] - crop_x, clipped[3] - crop_y])
        item["coordinate_space"] = "board_crop"
        item["board_crop_bbox_px"] = _round_bbox(crop_rect)
        item["source"] = item.get("source") or "mosaic_detection"
        item["retained_ratio"] = round(retained_ratio, 4)
        remapped.append(item)
    return remapped


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


def _is_auto_crop_manifest(manifest: dict[str, Any]) -> bool:
    plan = manifest.get("plan") or {}
    return plan.get("mosaic_mode") == "auto_crop_overlap" or plan.get("scan_type") == "fixed_points_16"


def _gray_small(image: Any) -> tuple[Any, float]:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    scale = min(1.0, 320.0 / max(width, height))
    if scale < 1.0:
        gray = cv2.resize(
            gray,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    return gray.astype(np.float32), scale


def _translation_score(first: Any, second: Any, axis: str, step: int) -> float:
    import numpy as np  # type: ignore[import-not-found]

    if axis == "x":
        first_patch = first[:, step:]
        second_patch = second[:, : first.shape[1] - step]
    else:
        first_patch = first[step:, :]
        second_patch = second[: first.shape[0] - step, :]
    if first_patch.size < 64 or second_patch.size < 64:
        return -1.0
    first_norm = first_patch - float(first_patch.mean())
    second_norm = second_patch - float(second_patch.mean())
    denominator = float(np.linalg.norm(first_norm) * np.linalg.norm(second_norm))
    if denominator <= 1e-6:
        return -1.0
    return float((first_norm * second_norm).sum() / denominator)


def _estimate_pair_step_px(first_image: Any, second_image: Any, axis: str) -> tuple[int, float]:
    first_small, scale = _gray_small(first_image)
    second_small, _ = _gray_small(second_image)
    limit = first_small.shape[1] if axis == "x" else first_small.shape[0]
    min_step = max(1, int(round(limit * 0.03)))
    max_step = max(min_step + 1, int(round(limit * 0.92)))
    best_step = min_step
    best_score = -1.0
    for step in range(min_step, max_step):
        score = _translation_score(first_small, second_small, axis, step)
        if score > best_score:
            best_score = score
            best_step = step
    return max(1, int(round(best_step / max(scale, 1e-6)))), best_score


def _round_vector_px(dx: float, dy: float) -> dict[str, int]:
    rounded_dx = int(round(dx))
    rounded_dy = int(round(dy))
    return {"dx": 0 if abs(rounded_dx) <= 1 else rounded_dx, "dy": 0 if abs(rounded_dy) <= 1 else rounded_dy}


def _normalize_vector_direction(vector: dict[str, int]) -> dict[str, int]:
    dx = int(vector.get("dx", 0))
    dy = int(vector.get("dy", 0))
    if abs(dx) >= abs(dy):
        return {"dx": abs(dx), "dy": dy if dx >= 0 else -dy}
    return {"dx": dx if dy >= 0 else -dx, "dy": abs(dy)}


def _edge_phase_displacement_px(first_image: Any, second_image: Any) -> dict[str, Any] | None:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    first = cv2.cvtColor(first_image, cv2.COLOR_BGR2GRAY) if first_image.ndim == 3 else first_image
    second = cv2.cvtColor(second_image, cv2.COLOR_BGR2GRAY) if second_image.ndim == 3 else second_image
    if first.shape[:2] != second.shape[:2]:
        second = cv2.resize(second, (first.shape[1], first.shape[0]), interpolation=cv2.INTER_AREA)
    height, width = first.shape[:2]
    scale = min(1.0, 640.0 / max(float(width), float(height), 1.0))
    if scale < 1.0:
        size = (max(8, int(round(width * scale))), max(8, int(round(height * scale))))
        first = cv2.resize(first, size, interpolation=cv2.INTER_AREA)
        second = cv2.resize(second, size, interpolation=cv2.INTER_AREA)
    first_blur = cv2.GaussianBlur(first, (3, 3), 0)
    second_blur = cv2.GaussianBlur(second, (3, 3), 0)
    first_edge = cv2.Laplacian(first_blur, cv2.CV_32F, ksize=3)
    second_edge = cv2.Laplacian(second_blur, cv2.CV_32F, ksize=3)
    if float(np.std(first_edge)) <= 1e-6 or float(np.std(second_edge)) <= 1e-6:
        return None
    window = cv2.createHanningWindow((first_edge.shape[1], first_edge.shape[0]), cv2.CV_32F)
    (shift_x, shift_y), response = cv2.phaseCorrelate(first_edge * window, second_edge * window)
    return {
        "dx": float(shift_x / max(scale, 1e-6)),
        "dy": float(shift_y / max(scale, 1e-6)),
        "score": float(response),
        "source": "edge_phase",
    }


def _orb_displacement_px(first_image: Any, second_image: Any) -> dict[str, Any] | None:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    first = cv2.cvtColor(first_image, cv2.COLOR_BGR2GRAY) if first_image.ndim == 3 else first_image
    second = cv2.cvtColor(second_image, cv2.COLOR_BGR2GRAY) if second_image.ndim == 3 else second_image
    if first.shape[:2] != second.shape[:2]:
        second = cv2.resize(second, (first.shape[1], first.shape[0]), interpolation=cv2.INTER_AREA)
    try:
        orb = cv2.ORB_create(nfeatures=2000)
    except Exception:
        return None
    keypoints_first, descriptors_first = orb.detectAndCompute(first, None)
    keypoints_second, descriptors_second = orb.detectAndCompute(second, None)
    if descriptors_first is None or descriptors_second is None or len(keypoints_first) < 8 or len(keypoints_second) < 8:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(matcher.match(descriptors_first, descriptors_second), key=lambda item: item.distance)[:80]
    if len(matches) < 8:
        return None
    points_first = np.float32([keypoints_first[match.queryIdx].pt for match in matches])
    points_second = np.float32([keypoints_second[match.trainIdx].pt for match in matches])
    affine, inliers = cv2.estimateAffinePartial2D(points_first, points_second, method=cv2.RANSAC, ransacReprojThreshold=5)
    if affine is None or inliers is None:
        return None
    inlier_count = int(inliers.sum())
    if inlier_count < 8:
        return None
    inlier_ratio = inlier_count / max(len(matches), 1)
    score = min(1.0, inlier_ratio * min(1.0, inlier_count / 20.0) * 2.0)
    return {
        "dx": float(affine[0, 2]),
        "dy": float(affine[1, 2]),
        "score": float(score),
        "source": "orb_ransac",
        "matches": len(matches),
        "inliers": inlier_count,
    }


def _estimate_pair_displacement_px(first_image: Any, second_image: Any) -> dict[str, Any]:
    candidates = []
    phase = _edge_phase_displacement_px(first_image, second_image)
    if phase is not None:
        candidates.append(phase)
    orb = _orb_displacement_px(first_image, second_image)
    if orb is not None:
        candidates.append(orb)
    if not candidates:
        return {"dx": 0.0, "dy": 0.0, "score": 0.0, "source": "unavailable", "candidates": []}
    candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    phase_candidates = [item for item in candidates if item.get("source") == "edge_phase" and float(item.get("score", 0.0)) >= 0.08]
    selected = phase_candidates[0] if phase_candidates else candidates[0]
    result = dict(selected)
    result["candidates"] = candidates
    return result


def _estimate_axis_vector_px(
    cells: list[dict[str, Any]],
    images: dict[str, Any],
    axis: str,
) -> tuple[dict[str, int] | None, float, list[dict[str, Any]]]:
    import numpy as np  # type: ignore[import-not-found]

    manual = FIXED_STEP_X_PX if axis == "x" else FIXED_STEP_Y_PX
    if manual > 0:
        vector = {"dx": int(manual), "dy": 0} if axis == "x" else {"dx": 0, "dy": int(manual)}
        return vector, 1.0, [{"source": "manual_env", "vector_px": vector, "score": 1.0}]

    group_key = "y_mm" if axis == "x" else "x_mm"
    sort_key = "x_mm" if axis == "x" else "y_mm"
    groups: dict[float, list[dict[str, Any]]] = {}
    for cell in cells:
        groups.setdefault(round(float(cell.get(group_key, 0.0)), 4), []).append(cell)

    vectors: list[tuple[float, float]] = []
    scores: list[float] = []
    pair_details: list[dict[str, Any]] = []
    for group_cells in groups.values():
        ordered = sorted(group_cells, key=lambda item: float(item.get(sort_key, 0.0)))
        for first, second in zip(ordered, ordered[1:]):
            first_image = images.get(str(first.get("raw_image")))
            second_image = images.get(str(second.get("raw_image")))
            if first_image is None or second_image is None:
                continue
            estimate = _estimate_pair_displacement_px(first_image, second_image)
            dx = float(estimate.get("dx", 0.0))
            dy = float(estimate.get("dy", 0.0))
            score = float(estimate.get("score", 0.0))
            norm = float(np.hypot(dx, dy))
            if score >= 0.08 and norm >= 2.0:
                vectors.append((dx, dy))
                scores.append(score)
            pair_details.append(
                {
                    "axis": axis,
                    "from": {"row": first.get("row"), "col": first.get("col")},
                    "to": {"row": second.get("row"), "col": second.get("col")},
                    "vector_px": _round_vector_px(dx, dy),
                    "score": round(score, 4),
                    "source": estimate.get("source"),
                    "candidates": [
                        {
                            "source": candidate.get("source"),
                            "vector_px": _round_vector_px(float(candidate.get("dx", 0.0)), float(candidate.get("dy", 0.0))),
                            "score": round(float(candidate.get("score", 0.0)), 4),
                        }
                        for candidate in estimate.get("candidates", [])
                    ],
                }
            )

    if not vectors:
        return None, 0.0, pair_details
    array = np.array(vectors, dtype=np.float32)
    median = np.median(array, axis=0)
    distances = np.linalg.norm(array - median, axis=1)
    median_norm = float(np.linalg.norm(median))
    threshold = max(24.0, median_norm * 0.45)
    inlier_mask = distances <= threshold
    if not np.any(inlier_mask):
        inlier_mask = np.ones(len(vectors), dtype=bool)
    inlier_array = array[inlier_mask]
    vector = _normalize_vector_direction(
        _round_vector_px(float(np.median(inlier_array[:, 0])), float(np.median(inlier_array[:, 1])))
    )
    inlier_scores = np.array(scores, dtype=np.float32)[inlier_mask]
    confidence = round(float(np.median(inlier_scores)) * (float(inlier_mask.sum()) / max(len(vectors), 1)), 4)
    return vector, confidence, pair_details


def _estimate_axis_step_px(
    cells: list[dict[str, Any]],
    images: dict[str, Any],
    axis: str,
) -> tuple[int | None, float, list[dict[str, Any]]]:
    import numpy as np  # type: ignore[import-not-found]

    manual = FIXED_STEP_X_PX if axis == "x" else FIXED_STEP_Y_PX
    if manual > 0:
        return int(manual), 1.0, [{"source": "manual_env", "step_px": int(manual), "score": 1.0}]

    group_key = "y_mm" if axis == "x" else "x_mm"
    sort_key = "x_mm" if axis == "x" else "y_mm"
    groups: dict[float, list[dict[str, Any]]] = {}
    for cell in cells:
        groups.setdefault(round(float(cell.get(group_key, 0.0)), 4), []).append(cell)

    estimates: list[int] = []
    scored_pairs: list[float] = []
    pair_details: list[dict[str, Any]] = []
    for group_cells in groups.values():
        ordered = sorted(group_cells, key=lambda item: float(item.get(sort_key, 0.0)))
        for first, second in zip(ordered, ordered[1:]):
            first_image = images.get(str(first.get("raw_image")))
            second_image = images.get(str(second.get("raw_image")))
            if first_image is None or second_image is None:
                continue
            step_px, score = _estimate_pair_step_px(first_image, second_image, axis)
            if score >= 0.45:
                estimates.append(step_px)
                scored_pairs.append(score)
            pair_details.append(
                {
                    "axis": axis,
                    "from": {"row": first.get("row"), "col": first.get("col")},
                    "to": {"row": second.get("row"), "col": second.get("col")},
                    "step_px": int(step_px),
                    "score": round(score, 4),
                }
            )

    if not estimates:
        return None, 0.0, pair_details
    return (
        int(round(float(np.median(np.array(estimates, dtype=np.float32))))),
        round(float(np.median(np.array(scored_pairs, dtype=np.float32))), 4),
        pair_details,
    )


def _detect_board_crop(image: Any) -> dict[str, Any]:
    cv2 = _import_cv2()

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"status": "not_found", "reason": "no_contours"}

    height, width = gray.shape[:2]
    image_area = float(width * height)
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < image_area * 0.08:
        return {"status": "not_found", "reason": "largest_contour_too_small", "area_ratio": round(area / image_area, 4)}

    x, y, crop_width, crop_height = cv2.boundingRect(contour)
    if crop_width <= 0 or crop_height <= 0:
        return {"status": "failed", "reason": "invalid_bbox"}
    margin = max(2, int(round(min(width, height) * 0.01)))
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(width, x + crop_width + margin)
    y2 = min(height, y + crop_height + margin)
    crop_area = float((x2 - x1) * (y2 - y1))
    return {
        "status": "ok",
        "bbox_px": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
        "area_ratio": round(crop_area / image_area, 4),
        "threshold": "otsu",
        "confidence": round(min(1.0, max(0.0, area / max(1.0, crop_area))), 4),
    }


def _physical_fov_short_mm(tile_width: int, tile_height: int) -> float:
    if CAMERA_FOV_SHORT_MM > 0:
        return CAMERA_FOV_SHORT_MM
    return CAMERA_FOV_LONG_MM * float(tile_height) / max(float(tile_width), 1e-6)


def _axis_crop_bounds(origins: list[int], tile_size: int) -> dict[int, tuple[int, int]]:
    unique_origins = sorted(set(int(origin) for origin in origins))
    bounds: dict[int, tuple[int, int]] = {}
    for index, origin in enumerate(unique_origins):
        if index == 0:
            left = origin
        else:
            previous_origin = unique_origins[index - 1]
            left = int(round((previous_origin + tile_size + origin) / 2.0))
        if index == len(unique_origins) - 1:
            right = origin + tile_size
        else:
            next_origin = unique_origins[index + 1]
            right = int(round((origin + tile_size + next_origin) / 2.0))
        bounds[origin] = (left, right)
    return bounds


def _physical_origin_px(
    cell: dict[str, Any],
    origin_x_mm: float,
    origin_y_mm: float,
    px_per_mm_x: float,
    px_per_mm_y: float,
    axis_mapping: str,
) -> tuple[float, float]:
    slide_x_mm = float(cell.get("x_mm", 0.0)) - origin_x_mm
    slide_y_mm = float(cell.get("y_mm", 0.0)) - origin_y_mm
    if axis_mapping == "slide_x_image_x_slide_y_image_y":
        return slide_x_mm * px_per_mm_x, slide_y_mm * px_per_mm_y
    if axis_mapping != "slide_y_image_x_slide_x_image_y":
        raise CameraTriggerError(
            "LOONGSON_AOI_STITCH_AXIS_MAPPING must be slide_y_image_x_slide_x_image_y "
            "or slide_x_image_x_slide_y_image_y."
        )
    return slide_y_mm * px_per_mm_x, slide_x_mm * px_per_mm_y


def _physical_step_vectors_px(
    step_x_mm: float,
    step_y_mm: float,
    px_per_mm_x: float,
    px_per_mm_y: float,
    axis_mapping: str,
) -> tuple[dict[str, int], dict[str, int], int, int, str]:
    if axis_mapping == "slide_x_image_x_slide_y_image_y":
        physical_x_px = int(round(step_x_mm * px_per_mm_x))
        physical_y_px = int(round(step_y_mm * px_per_mm_y))
        step_x_px = int(FIXED_STEP_X_PX) if FIXED_STEP_X_PX > 0 else physical_x_px
        step_y_px = int(FIXED_STEP_Y_PX) if FIXED_STEP_Y_PX > 0 else physical_y_px
        source = "manual_env" if FIXED_STEP_X_PX > 0 or FIXED_STEP_Y_PX > 0 else "physical_fov"
        return {"dx": step_x_px, "dy": 0}, {"dx": 0, "dy": step_y_px}, step_x_px, step_y_px, source

    if axis_mapping != "slide_y_image_x_slide_x_image_y":
        raise CameraTriggerError(
            "LOONGSON_AOI_STITCH_AXIS_MAPPING must be slide_y_image_x_slide_x_image_y "
            "or slide_x_image_x_slide_y_image_y."
        )

    physical_x_px = int(round(step_x_mm * px_per_mm_y))
    physical_y_px = int(round(step_y_mm * px_per_mm_x))
    step_x_px = int(FIXED_STEP_X_PX) if FIXED_STEP_X_PX > 0 else physical_x_px
    step_y_px = int(FIXED_STEP_Y_PX) if FIXED_STEP_Y_PX > 0 else physical_y_px
    source = "manual_env" if FIXED_STEP_X_PX > 0 or FIXED_STEP_Y_PX > 0 else "physical_fov"
    return {"dx": 0, "dy": step_x_px}, {"dx": step_y_px, "dy": 0}, step_x_px, step_y_px, source


def _build_physical_fov_crop_mosaic_image(manifest: dict[str, Any]) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    cells = [cell for cell in manifest.get("cells", []) if cell.get("raw_image")]
    if not cells:
        raise CameraTriggerError("No captured raw images found in scan manifest.")

    images: dict[str, Any] = {}
    first_image = None
    for cell in cells:
        raw_image = str(cell["raw_image"])
        image = cv2.imread(str(resolve_artifact(raw_image)))
        if image is None:
            continue
        images[raw_image] = image
        if first_image is None:
            first_image = image
    if first_image is None:
        raise CameraTriggerError("Could not read captured raw images for physical-FOV mosaic.")

    tile_height, tile_width = first_image.shape[:2]
    fov_long_mm = CAMERA_FOV_LONG_MM
    fov_short_mm = _physical_fov_short_mm(tile_width, tile_height)
    px_per_mm_x = tile_width / max(fov_long_mm, 1e-6)
    px_per_mm_y = tile_height / max(fov_short_mm, 1e-6)

    plan = manifest.get("plan") or {}
    step_mm = plan.get("step_mm") or {"x": FIXED_STEP_X_MM, "y": FIXED_STEP_Y_MM}
    step_x_mm = float(step_mm.get("x", FIXED_STEP_X_MM))
    step_y_mm = float(step_mm.get("y", FIXED_STEP_Y_MM))
    origin = plan.get("origin_mm") or {"x": 0.0, "y": 0.0}
    origin_x_mm = float(origin.get("x", 0.0))
    origin_y_mm = float(origin.get("y", 0.0))
    axis_mapping = str(plan.get("axis_mapping") or STITCH_AXIS_MAPPING or "slide_x_image_x_slide_y_image_y").strip().lower()
    x_vector_px, y_vector_px, step_x_px, step_y_px, step_px_source = _physical_step_vectors_px(
        step_x_mm,
        step_y_mm,
        px_per_mm_x,
        px_per_mm_y,
        axis_mapping,
    )

    raw_origins: list[tuple[dict[str, Any], float, float]] = []
    for cell in cells:
        x_units = (float(cell.get("x_mm", 0.0)) - origin_x_mm) / max(step_x_mm, 1e-6)
        y_units = (float(cell.get("y_mm", 0.0)) - origin_y_mm) / max(step_y_mm, 1e-6)
        x_float = x_units * float(x_vector_px["dx"]) + y_units * float(y_vector_px["dx"])
        y_float = x_units * float(x_vector_px["dy"]) + y_units * float(y_vector_px["dy"])
        raw_origins.append((cell, x_float, y_float))

    min_x = min(origin_x for _, origin_x, _ in raw_origins)
    min_y = min(origin_y for _, _, origin_y in raw_origins)
    origins_by_key: dict[str, tuple[int, int]] = {}
    x_origins: list[int] = []
    y_origins: list[int] = []
    for cell, x_float, y_float in raw_origins:
        x_origin = int(round(x_float - min_x))
        y_origin = int(round(y_float - min_y))
        origins_by_key[str(cell["raw_image"])] = (x_origin, y_origin)
        x_origins.append(x_origin)
        y_origins.append(y_origin)

    canvas_width = max(origin + tile_width for origin in x_origins)
    canvas_height = max(origin + tile_height for origin in y_origins)
    accumulator = np.zeros((canvas_height, canvas_width, 3), dtype=np.float32)
    weight_sum = np.zeros((canvas_height, canvas_width), dtype=np.float32)
    placed_cells: list[dict[str, Any]] = []

    def tile_weight(height: int, width: int) -> Any:
        blend = max(8, min(height, width) // 10)
        y = np.ones(height, dtype=np.float32)
        x = np.ones(width, dtype=np.float32)
        ramp = np.linspace(0.15, 1.0, blend, dtype=np.float32)
        y[:blend] = ramp
        y[-blend:] = ramp[::-1]
        x[:blend] = ramp
        x[-blend:] = ramp[::-1]
        return np.outer(y, x)

    base_weight = tile_weight(tile_height, tile_width)

    for cell in cells:
        raw_image = str(cell["raw_image"])
        image = images.get(raw_image)
        if image is None:
            continue
        if image.shape[1] != tile_width or image.shape[0] != tile_height:
            image = cv2.resize(image, (tile_width, tile_height), interpolation=cv2.INTER_AREA)
        if STITCH_MIRROR_X:
            image = cv2.flip(image, 1)

        origin_x, origin_y = origins_by_key[raw_image]
        dst_x1 = origin_x
        dst_y1 = origin_y
        dst_x2 = dst_x1 + tile_width
        dst_y2 = dst_y1 + tile_height
        accumulator[dst_y1:dst_y2, dst_x1:dst_x2] += image.astype(np.float32) * base_weight[:, :, None]
        weight_sum[dst_y1:dst_y2, dst_x1:dst_x2] += base_weight
        placed_cells.append(
            {
                "row": cell.get("row"),
                "col": cell.get("col"),
                "x_px": dst_x1,
                "y_px": dst_y1,
                "width_px": tile_width,
                "height_px": tile_height,
                "source_crop_px": [0, 0, tile_width, tile_height],
                "full_origin_px": {"x": int(origin_x), "y": int(origin_y)},
                "full_tile_px": {"width": tile_width, "height": tile_height},
                "mirror_x": bool(STITCH_MIRROR_X),
                "raw_image": cell.get("raw_image"),
                "annotated_image": cell.get("annotated_image"),
            }
        )

    if not placed_cells:
        raise CameraTriggerError("No captured cells could be placed into the physical-FOV mosaic.")
    valid_mask = weight_sum > 1e-6
    if not np.any(valid_mask):
        raise CameraTriggerError("Physical-FOV mosaic produced an empty canvas.")

    mosaic_float = np.zeros_like(accumulator, dtype=np.float32)
    mosaic_float[valid_mask] = accumulator[valid_mask] / weight_sum[valid_mask, None]
    ys, xs = np.where(valid_mask)
    crop_left = int(xs.min())
    crop_top = int(ys.min())
    crop_right = int(xs.max()) + 1
    crop_bottom = int(ys.max()) + 1
    if crop_left > 0 or crop_top > 0 or crop_right < mosaic_float.shape[1] or crop_bottom < mosaic_float.shape[0]:
        mosaic_float = mosaic_float[crop_top:crop_bottom, crop_left:crop_right]
        for placement in placed_cells:
            placement["x_px"] = int(placement["x_px"]) - crop_left
            placement["y_px"] = int(placement["y_px"]) - crop_top
            full_origin = placement.get("full_origin_px") or {}
            full_origin["x"] = int(full_origin.get("x", 0)) - crop_left
            full_origin["y"] = int(full_origin.get("y", 0)) - crop_top
            placement["full_origin_px"] = full_origin
    mosaic = np.clip(mosaic_float, 0, 255).astype(np.uint8)

    overlap_x_px = int(max(0, tile_width - abs(x_vector_px["dx"]) - abs(y_vector_px["dx"])))
    overlap_y_px = int(max(0, tile_height - abs(x_vector_px["dy"]) - abs(y_vector_px["dy"])))
    low_overlap_axes = []
    if overlap_x_px < max(32, int(tile_width * 0.05)):
        low_overlap_axes.append("image_x")
    if overlap_y_px < max(32, int(tile_height * 0.05)):
        low_overlap_axes.append("image_y")
    if axis_mapping == "slide_y_image_x_slide_x_image_y":
        fov_width_mm = fov_short_mm
        fov_height_mm = fov_long_mm
    else:
        fov_width_mm = fov_long_mm
        fov_height_mm = fov_short_mm

    geometry = {
        "mode": "physical_fov_crop_stitch",
        "source": "physical_fov",
        "step_px_source": step_px_source,
        "crop_strategy": "weighted_overlap",
        "axis_mapping": axis_mapping,
        "tile_px": {"width": tile_width, "height": tile_height},
        "canvas_px": {"width": int(mosaic.shape[1]), "height": int(mosaic.shape[0])},
        "fov_mm": {
            "long": round(fov_long_mm, 6),
            "short": round(fov_short_mm, 6),
            "width": round(fov_width_mm, 6),
            "height": round(fov_height_mm, 6),
            "image_width": round(fov_long_mm, 6),
            "image_height": round(fov_short_mm, 6),
        },
        "px_per_mm": round((px_per_mm_x + px_per_mm_y) / 2.0, 6),
        "px_per_mm_axes": {"image_x": round(px_per_mm_x, 6), "image_y": round(px_per_mm_y, 6)},
        "stride_px": {"slide_x": int(step_x_px), "slide_y": int(step_y_px)},
        "estimated_step_px": {"x": int(step_x_px), "y": int(step_y_px)},
        "estimated_step_vectors_px": {"x": x_vector_px, "y": y_vector_px},
        "overlap_px": {"image_x": overlap_x_px, "image_y": overlap_y_px},
        "overlap_warning": (
            "Low overlap on " + ", ".join(low_overlap_axes) + "; seams may be visible."
            if low_overlap_axes
            else None
        ),
        "estimated_px_per_mm": {
            "x": round(step_x_px / max(step_x_mm, 1e-6), 6),
            "y": round(step_y_px / max(step_y_mm, 1e-6), 6),
        },
        "physical_stride_px": {
            "x": int(round(step_x_mm * px_per_mm_y if axis_mapping == "slide_y_image_x_slide_x_image_y" else step_x_mm * px_per_mm_x)),
            "y": int(round(step_y_mm * px_per_mm_x if axis_mapping == "slide_y_image_x_slide_x_image_y" else step_y_mm * px_per_mm_y)),
        },
        "estimated_fov_mm": {
            "width": round(fov_width_mm, 4),
            "height": round(fov_height_mm, 4),
        },
        "stitch_confidence": 1.0,
        "pcb_size_known": False,
        "step_mm": {"x": step_x_mm, "y": step_y_mm},
        "cell_image_transform": {"mirror_x": bool(STITCH_MIRROR_X)},
    }
    return mosaic, geometry, placed_cells


def _build_auto_crop_mosaic_image(manifest: dict[str, Any]) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    return _build_physical_fov_crop_mosaic_image(manifest)


def _build_auto_vector_mosaic_image(manifest: dict[str, Any]) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    cv2 = _import_cv2()
    import numpy as np  # type: ignore[import-not-found]

    cells = [cell for cell in manifest.get("cells", []) if cell.get("raw_image")]
    if not cells:
        raise CameraTriggerError("No captured raw images found in scan manifest.")

    images: dict[str, Any] = {}
    first_image = None
    for cell in cells:
        raw_image = str(cell["raw_image"])
        image = cv2.imread(str(resolve_artifact(raw_image)))
        if image is None:
            continue
        images[raw_image] = image
        if first_image is None:
            first_image = image
    if first_image is None:
        raise CameraTriggerError("Could not read captured raw images for auto-crop mosaic.")

    tile_height, tile_width = first_image.shape[:2]
    x_vector_px, score_x, x_pairs = _estimate_axis_vector_px(cells, images, "x")
    y_vector_px, score_y, y_pairs = _estimate_axis_vector_px(cells, images, "y")
    if x_vector_px is None or y_vector_px is None:
        raise CameraTriggerError(
            "Could not estimate overlap from adjacent images. Set LOONGSON_AOI_FIXED_STEP_X_PX and "
            "LOONGSON_AOI_FIXED_STEP_Y_PX as a manual fallback."
        )
    if min(score_x, score_y) < 0.08:
        raise CameraTriggerError(
            f"Estimated stitch confidence is too low: x={score_x:.4f}, y={score_y:.4f}."
        )

    plan = manifest.get("plan") or {}
    step_mm = plan.get("step_mm") or {"x": FIXED_STEP_X_MM, "y": FIXED_STEP_Y_MM}
    step_x_mm = float(step_mm.get("x", FIXED_STEP_X_MM))
    step_y_mm = float(step_mm.get("y", FIXED_STEP_Y_MM))
    origin = plan.get("origin_mm") or {"x": 0.0, "y": 0.0}
    origin_x_mm = float(origin.get("x", 0.0))
    origin_y_mm = float(origin.get("y", 0.0))

    for cell in cells:
        x_units = (float(cell.get("x_mm", 0.0)) - origin_x_mm) / max(step_x_mm, 1e-6)
        y_units = (float(cell.get("y_mm", 0.0)) - origin_y_mm) / max(step_y_mm, 1e-6)
        cell["_origin_px_float"] = {
            "x": x_units * float(x_vector_px["dx"]) + y_units * float(y_vector_px["dx"]),
            "y": x_units * float(x_vector_px["dy"]) + y_units * float(y_vector_px["dy"]),
        }

    min_x = min(float(cell["_origin_px_float"]["x"]) for cell in cells)
    min_y = min(float(cell["_origin_px_float"]["y"]) for cell in cells)
    for cell in cells:
        cell["_origin_px"] = {
            "x": int(round(float(cell["_origin_px_float"]["x"]) - min_x)),
            "y": int(round(float(cell["_origin_px_float"]["y"]) - min_y)),
        }

    max_right = max(int(cell["_origin_px"]["x"]) + tile_width for cell in cells)
    max_bottom = max(int(cell["_origin_px"]["y"]) + tile_height for cell in cells)
    accumulator = np.zeros((max_bottom, max_right, 3), dtype=np.float32)
    weight_sum = np.zeros((max_bottom, max_right), dtype=np.float32)
    placed_cells: list[dict[str, Any]] = []

    def tile_weight(height: int, width: int) -> Any:
        blend = max(8, min(height, width) // 10)
        y = np.ones(height, dtype=np.float32)
        x = np.ones(width, dtype=np.float32)
        ramp = np.linspace(0.15, 1.0, blend, dtype=np.float32)
        y[:blend] = ramp
        y[-blend:] = ramp[::-1]
        x[:blend] = ramp
        x[-blend:] = ramp[::-1]
        return np.outer(y, x)

    base_weight = tile_weight(tile_height, tile_width)

    for cell in cells:
        raw_image = str(cell["raw_image"])
        image = images.get(raw_image)
        if image is None:
            continue
        if image.shape[1] != tile_width or image.shape[0] != tile_height:
            image = cv2.resize(image, (tile_width, tile_height), interpolation=cv2.INTER_AREA)
        x0 = int(cell["_origin_px"]["x"])
        y0 = int(cell["_origin_px"]["y"])
        x1 = x0 + tile_width
        y1 = y0 + tile_height
        if x1 <= x0 or y1 <= y0:
            continue
        accumulator[y0:y1, x0:x1] += image.astype(np.float32) * base_weight[:, :, None]
        weight_sum[y0:y1, x0:x1] += base_weight
        placed_cells.append(
            {
                "row": cell.get("row"),
                "col": cell.get("col"),
                "x_px": x0,
                "y_px": y0,
                "width_px": tile_width,
                "height_px": tile_height,
                "source_crop_px": [0, 0, tile_width, tile_height],
                "raw_image": cell.get("raw_image"),
                "annotated_image": cell.get("annotated_image"),
            }
        )

    if not placed_cells:
        raise CameraTriggerError("No captured cells could be placed into the auto-crop mosaic.")
    valid_mask = weight_sum > 1e-6
    if not np.any(valid_mask):
        raise CameraTriggerError("Auto vector mosaic produced an empty canvas.")
    mosaic = np.zeros_like(accumulator, dtype=np.float32)
    mosaic[valid_mask] = accumulator[valid_mask] / weight_sum[valid_mask, None]
    ys, xs = np.where(valid_mask)
    crop_left = int(xs.min())
    crop_top = int(ys.min())
    crop_right = int(xs.max()) + 1
    crop_bottom = int(ys.max()) + 1
    if crop_left > 0 or crop_top > 0 or crop_right < mosaic.shape[1] or crop_bottom < mosaic.shape[0]:
        mosaic = mosaic[crop_top:crop_bottom, crop_left:crop_right]
        for placement in placed_cells:
            placement["x_px"] = int(placement["x_px"]) - crop_left
            placement["y_px"] = int(placement["y_px"]) - crop_top
    mosaic = np.clip(mosaic, 0, 255).astype(np.uint8)

    step_x_px = int(round(float(np.hypot(float(x_vector_px["dx"]), float(x_vector_px["dy"])))))
    step_y_px = int(round(float(np.hypot(float(y_vector_px["dx"]), float(y_vector_px["dy"])))))
    px_per_mm_x = step_x_px / max(step_x_mm, 1e-6)
    px_per_mm_y = step_y_px / max(step_y_mm, 1e-6)

    geometry = {
        "mode": "auto_vector_overlap",
        "tile_px": {"width": tile_width, "height": tile_height},
        "canvas_px": {"width": int(mosaic.shape[1]), "height": int(mosaic.shape[0])},
        "estimated_step_px": {"x": int(step_x_px), "y": int(step_y_px)},
        "estimated_step_vectors_px": {"x": x_vector_px, "y": y_vector_px},
        "estimated_px_per_mm": {"x": round(px_per_mm_x, 6), "y": round(px_per_mm_y, 6)},
        "estimated_fov_mm": {
            "width": round(tile_width / max(px_per_mm_x, 1e-6), 4),
            "height": round(tile_height / max(px_per_mm_y, 1e-6), 4),
        },
        "stitch_confidence": round(min(score_x, score_y), 4),
        "axis_pair_scores": {"x": x_pairs, "y": y_pairs},
        "pcb_size_known": False,
        "step_mm": {"x": step_x_mm, "y": step_y_mm},
    }
    return mosaic, geometry, placed_cells


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
                "source_crop_px": [int(src_x1), int(src_y1), int(src_x2 - src_x1), int(src_y2 - src_y1)],
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
                "source_crop_px": [0, 0, int(expected_width), int(expected_height)],
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


def _build_frames_archive(
    manifest: dict[str, Any],
    archive_path: Path,
    image_field: str,
    suffix_label: str,
) -> dict[str, Any]:
    cells = [cell for cell in manifest.get("cells", []) if cell.get(image_field) or cell.get("raw_image")]
    ordered_cells = [
        cell
        for _, cell in sorted(
            enumerate(cells, start=1),
            key=lambda indexed: (int(indexed[1].get("order", indexed[0])), indexed[0]),
        )
    ]
    frames: list[dict[str, Any]] = []
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, cell in enumerate(ordered_cells, start=1):
            image_ref = str(cell.get(image_field) or "")
            fallback_raw = False
            if not image_ref and image_field != "raw_image":
                image_ref = str(cell.get("raw_image") or "")
                fallback_raw = True
            if not image_ref:
                continue
            source_path = resolve_artifact(image_ref)
            if not source_path.exists():
                continue
            suffix = source_path.suffix.lower() or ".jpg"
            entry_name = (
                f"frame_{index:03d}_r{int(cell.get('row', 0)):03d}_c{int(cell.get('col', 0)):03d}_"
                f"{suffix_label}{suffix}"
            )
            archive.write(source_path, entry_name)
            frames.append(
                {
                    "index": index,
                    "row": cell.get("row"),
                    "col": cell.get("col"),
                    "x_mm": cell.get("x_mm"),
                    "y_mm": cell.get("y_mm"),
                    "entry": entry_name,
                    image_field: image_ref,
                    "raw_image": cell.get("raw_image"),
                    "annotated_image": cell.get("annotated_image"),
                    "fallback_raw": fallback_raw,
                    "defect_count": len(cell.get("defects") or []),
                    "status": cell.get("status"),
                }
            )
        archive.writestr(
            "frames_manifest.json",
            json.dumps(
                {
                    "scan_id": manifest.get("scan_id"),
                    "created_at": _now_iso(),
                    "frame_count": len(frames),
                    "point_order": (manifest.get("plan") or {}).get("point_order"),
                    "image_field": image_field,
                    "suffix_label": suffix_label,
                    "frames": frames,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return {f"{suffix_label}_frames_zip": _rel(archive_path), f"{suffix_label}_frame_count": len(frames)}


def _build_raw_frames_archive(manifest: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    return _build_frames_archive(manifest, archive_path, "raw_image", "raw")


def _build_annotated_frames_archive(manifest: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    return _build_frames_archive(manifest, archive_path, "annotated_image", "annotated")


def build_scan_mosaic(scan_id: str, run_detection: bool = True) -> dict[str, Any]:
    start = time.perf_counter()
    manifest = _load_scan_manifest(scan_id)
    pixel_restore = _is_pixel_restore_manifest(manifest)
    auto_crop_status = "not_used"
    if pixel_restore:
        mosaic, geometry, placed_cells = _build_pixel_restored_mosaic_image(manifest)
    elif _is_auto_crop_manifest(manifest):
        try:
            mosaic, geometry, placed_cells = _build_auto_crop_mosaic_image(manifest)
            auto_crop_status = "ok"
        except CameraTriggerError as exc:
            mosaic, geometry, placed_cells = _build_mosaic_image(manifest)
            geometry["mode"] = "fixed_points_contact_sheet_fallback"
            geometry["auto_crop_error"] = str(exc)
            auto_crop_status = "failed"
    else:
        mosaic, geometry, placed_cells = _build_mosaic_image(manifest)
    cv2 = _import_cv2()

    mosaic_dir = _scan_dir(scan_id) / "mosaic"
    mosaic_dir.mkdir(parents=True, exist_ok=True)
    raw_path = mosaic_dir / ("mosaic_raw.png" if pixel_restore else "mosaic_raw.jpg")
    annotated_path = mosaic_dir / "mosaic_annotated.jpg"
    result_json_path = mosaic_dir / "mosaic_result.json"
    mosaic_manifest_path = mosaic_dir / "mosaic_manifest.json"
    cell_defect_audit_path = mosaic_dir / "cell_defect_audit.json"
    board_crop_raw_path = mosaic_dir / "board_crop_raw.jpg"
    board_crop_annotated_path = mosaic_dir / "board_crop_annotated.jpg"
    board_crop_metadata_path = mosaic_dir / "board_crop_metadata.json"
    raw_frames_zip_path = mosaic_dir / "raw_frames.zip"
    annotated_frames_zip_path = mosaic_dir / "annotated_frames.zip"
    raw_frames_archive = _build_raw_frames_archive(manifest, raw_frames_zip_path)
    annotated_frames_archive = _build_annotated_frames_archive(manifest, annotated_frames_zip_path)

    detections: list[dict[str, Any]] = []
    cell_defect_audit: dict[str, Any] = {}
    inference_ms = 0.0
    annotated = mosaic.copy()
    if run_detection:
        if pixel_restore:
            detections, annotated, inference_ms = yolo_detector.detect_frame(mosaic)
        else:
            detections, inference_ms = _detect_mosaic_tiles(mosaic, geometry)
            for detection in detections:
                draw_detection(annotated, detection["bbox"], str(detection.get("label") or detection.get("type")), float(detection.get("confidence", 0.0)))

    board_crop = _detect_board_crop(mosaic)
    if run_detection:
        detections = _remap_mosaic_detections_for_crop(detections, board_crop)
        cell_defect_audit = {
            "strategy": "mosaic_detection",
            "coordinate_space": "board_crop" if board_crop.get("status") == "ok" else "mosaic",
            "fused_count": len(detections),
            "board_crop": board_crop,
        }
    else:
        fusion = _fuse_cell_detections_for_mosaic(manifest, placed_cells, board_crop)
        detections = list(fusion["detections"])
        cell_defect_audit = dict(fusion["audit"])
        for detection in detections:
            draw_bbox = detection.get("mosaic_bbox") or detection.get("bbox")
            if not draw_bbox:
                continue
            label = str(detection.get("label") or detection.get("type"))
            if detection.get("has_conflict"):
                label = f"conflict {label}"
            color = (0, 170, 255) if detection.get("decision") == "suspected" else (0, 180, 0)
            if detection.get("has_conflict"):
                color = (0, 80, 255)
            draw_detection(annotated, draw_bbox, label, float(detection.get("confidence", 0.0)), color=color)

    cv2.imwrite(str(raw_path), mosaic)
    cv2.imwrite(str(annotated_path), annotated)
    if board_crop.get("status") == "ok":
        x, y, width, height = [int(value) for value in board_crop["bbox_px"]]
        cv2.imwrite(str(board_crop_raw_path), mosaic[y : y + height, x : x + width])
        cv2.imwrite(str(board_crop_annotated_path), annotated[y : y + height, x : x + width])
        board_crop["board_crop_raw"] = _rel(board_crop_raw_path)
        board_crop["board_crop_annotated"] = _rel(board_crop_annotated_path)
    with board_crop_metadata_path.open("w", encoding="utf-8") as file:
        json.dump(board_crop, file, ensure_ascii=False, indent=2)
    with result_json_path.open("w", encoding="utf-8") as file:
        json.dump(detections, file, ensure_ascii=False, indent=2)
    with cell_defect_audit_path.open("w", encoding="utf-8") as file:
        json.dump(cell_defect_audit, file, ensure_ascii=False, indent=2)
    with mosaic_manifest_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "scan_id": scan_id,
                "created_at": _now_iso(),
                "geometry": geometry,
                "auto_crop_status": auto_crop_status,
                "cells": placed_cells,
                "mosaic_raw": _rel(raw_path),
                "mosaic_annotated": _rel(annotated_path),
                "mosaic_result_json": _rel(result_json_path),
                "cell_defect_audit": _rel(cell_defect_audit_path),
                "board_crop": board_crop,
                "raw_frames_zip": raw_frames_archive.get("raw_frames_zip"),
                "raw_frame_count": raw_frames_archive.get("raw_frame_count", 0),
                "annotated_frames_zip": annotated_frames_archive.get("annotated_frames_zip"),
                "annotated_frame_count": annotated_frames_archive.get("annotated_frame_count", 0),
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
        "cell_defect_audit": _rel(cell_defect_audit_path),
        "board_crop_status": board_crop.get("status", "failed"),
        "board_crop_raw": board_crop.get("board_crop_raw"),
        "board_crop_annotated": board_crop.get("board_crop_annotated"),
        "board_crop_metadata": _rel(board_crop_metadata_path),
        "auto_crop_status": auto_crop_status,
        "defects": detections,
        "cell_count": len(placed_cells),
        "stitch_ms": round((time.perf_counter() - start) * 1000.0, 2),
        "inference_ms": round(inference_ms, 2),
        "geometry": geometry,
        "raw_frames_zip": raw_frames_archive.get("raw_frames_zip"),
        "raw_frame_count": raw_frames_archive.get("raw_frame_count", 0),
        "annotated_frames_zip": annotated_frames_archive.get("annotated_frames_zip"),
        "annotated_frame_count": annotated_frames_archive.get("annotated_frame_count", 0),
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
