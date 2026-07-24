from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _default_algorithm_root() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        Path(os.getenv("AOI_ALGORITHM_ROOT", "")) if os.getenv("AOI_ALGORITHM_ROOT") else None,
        here.parents[2] / "worktrees" / "algorithm-ai",
        here.parents[2] / "algorithm",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()
    return (here.parents[2] / "worktrees" / "algorithm-ai").resolve()


@dataclass(slots=True)
class CloudSettings:
    storage_backend: str = field(default_factory=lambda: os.getenv("AOI_STORAGE_BACKEND", "local").strip().lower())
    storage_root: Path = field(
        default_factory=lambda: Path(os.getenv("AOI_STORAGE_ROOT", str(Path(__file__).resolve().parents[1] / ".data"))).resolve()
    )
    public_api_base: str = field(default_factory=lambda: os.getenv("AOI_PUBLIC_API_BASE", "http://127.0.0.1:8010").rstrip("/"))
    public_ws_base: str = field(default_factory=lambda: os.getenv("AOI_PUBLIC_WS_BASE", "ws://127.0.0.1:8010").rstrip("/"))
    upload_expires_seconds: int = field(default_factory=lambda: _env_int("AOI_UPLOAD_EXPIRES_SECONDS", 900))
    detector_backend: str = field(default_factory=lambda: os.getenv("AOI_DETECTOR_BACKEND", "onnx").strip().lower())
    algorithm_root: Path = field(default_factory=_default_algorithm_root)
    model_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "AOI_MODEL_PATH",
                str(_default_algorithm_root() / "runs" / "detect" / "pcb_defects" / "weights" / "best.onnx"),
            )
        ).resolve()
    )
    config_path: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "AOI_CONFIG_PATH",
                str(_default_algorithm_root() / "algorithm" / "configs" / "pcb_defects.yaml"),
            )
        ).resolve()
    )
    yolo_conf: float = field(default_factory=lambda: _env_float("AOI_YOLO_CONF", 0.5))
    yolo_imgsz: int = field(default_factory=lambda: _env_int("AOI_YOLO_IMGSZ", 640))
    onnx_input_imgsz: int = field(default_factory=lambda: _env_int("AOI_ONNX_INPUT_IMGSZ", 0))
    obs_endpoint: str = field(default_factory=lambda: os.getenv("HUAWEI_OBS_ENDPOINT", "").strip())
    obs_bucket: str = field(default_factory=lambda: os.getenv("HUAWEI_OBS_BUCKET", "").strip())
    obs_ak: str = field(default_factory=lambda: os.getenv("HUAWEI_OBS_AK", "").strip())
    obs_sk: str = field(default_factory=lambda: os.getenv("HUAWEI_OBS_SK", "").strip())
    obs_region: str = field(default_factory=lambda: os.getenv("HUAWEI_OBS_REGION", "").strip())

    @classmethod
    def from_env(cls) -> "CloudSettings":
        return cls()
