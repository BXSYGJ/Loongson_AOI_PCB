"""Standalone slide-table movement and camera capture test.

This file is based on the teammate-provided motor/camera smoke test, but keeps
configuration outside the source code so the same script can run on Windows
dry-run, the Loongson board, or another USB camera index.

Example:

    python motor_test.py --camera-source 1 --output-dir captured_images

On the Loongson board, set motor dry-run off only after the wiring is checked:

    export LOONGSON_AOI_MOTOR_DRY_RUN=0
    python motor_test.py --camera-source /dev/video0
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import cv2

from motor_controller import home_all_axes, move_to_xy


DEFAULT_SCAN_POINTS: tuple[tuple[float, float], ...] = (
    (0, 0),
    (16, 8),
    (16, 40),
    (32, 8),
    (32, 40),
    (48, 8),
    (48, 40),
    (0, 0),
)


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


def _camera_source(value: str) -> int | str:
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    return value


def _backend_flag(name: str) -> int | None:
    normalized = name.strip().upper()
    if not normalized:
        return None
    mapping = {
        "DSHOW": getattr(cv2, "CAP_DSHOW", None),
        "MSMF": getattr(cv2, "CAP_MSMF", None),
        "V4L2": getattr(cv2, "CAP_V4L2", None),
    }
    return mapping.get(normalized)


def open_camera(source: int | str, width: int, height: int, backend: str = ""):
    backend_flag = _backend_flag(backend)
    cap = cv2.VideoCapture(source, backend_flag) if backend_flag is not None else cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera source: {source}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Camera resolution: {actual_width}x{actual_height}")
    return cap


def capture_once(cap, output_dir: Path, prefix: str, warmup_frames: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(max(0, warmup_frames)):
        cap.read()

    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("Failed to read frame from camera.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    image_path = output_dir / f"{prefix}_{timestamp}.jpg"
    if not cv2.imwrite(str(image_path), frame):
        raise RuntimeError(f"Failed to save image: {image_path}")

    print(f"Saved: {image_path}")
    return image_path


def parse_points(text: str) -> tuple[tuple[float, float], ...]:
    if not text.strip():
        return DEFAULT_SCAN_POINTS
    points: list[tuple[float, float]] = []
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        x_text, y_text = item.split(",", 1)
        points.append((float(x_text), float(y_text)))
    return tuple(points)


def run_scan(
    points: Iterable[tuple[float, float]],
    camera_source: int | str,
    width: int,
    height: int,
    output_dir: Path,
    stable_delay: float,
    warmup_frames: int,
    backend: str = "",
    soft_home: bool = False,
) -> list[Path]:
    if soft_home:
        print("Soft-home: current physical position is recorded as X/Y/Z zero.")
        home_all_axes()

    cap = open_camera(camera_source, width, height, backend)
    saved_images: list[Path] = []
    try:
        point_list = list(points)
        for index, (x, y) in enumerate(point_list):
            print(f"Step {index + 1} / {len(point_list)}: move to x={x}, y={y}")
            move_to_xy(x, y)

            print("Waiting for stage to stabilize...")
            time.sleep(max(0.0, stable_delay))

            image_path = capture_once(
                cap,
                output_dir,
                f"step{index:02d}_x{x:g}_y{y:g}",
                warmup_frames,
            )
            saved_images.append(image_path)
    finally:
        cap.release()

    print(f"Scan finished. Total images: {len(saved_images)}")
    for path in saved_images:
        print(path)
    return saved_images


def main() -> int:
    parser = argparse.ArgumentParser(description="Loongson AOI slide-table camera smoke test")
    parser.add_argument("--camera-source", default=os.getenv("LOONGSON_AOI_CAMERA_SOURCE", "0"))
    parser.add_argument("--camera-backend", default=os.getenv("LOONGSON_AOI_CAMERA_BACKEND", ""))
    parser.add_argument("--width", type=int, default=_env_int("LOONGSON_AOI_CAMERA_WIDTH", 1280))
    parser.add_argument("--height", type=int, default=_env_int("LOONGSON_AOI_CAMERA_HEIGHT", 800))
    parser.add_argument("--output-dir", default=os.getenv("LOONGSON_AOI_MOTOR_TEST_OUTPUT_DIR", "captured_images"))
    parser.add_argument("--stable-delay", type=float, default=_env_float("LOONGSON_AOI_MOTOR_TEST_STABLE_DELAY", 1.0))
    parser.add_argument("--warmup-frames", type=int, default=_env_int("LOONGSON_AOI_MOTOR_TEST_WARMUP_FRAMES", 5))
    parser.add_argument(
        "--points",
        default=os.getenv("LOONGSON_AOI_MOTOR_TEST_POINTS", ""),
        help="Semicolon-separated XY list, for example: 0,0;16,8;16,40;0,0",
    )
    parser.add_argument("--soft-home", action="store_true", help="record current physical position as X/Y/Z zero first")
    args = parser.parse_args()

    run_scan(
        parse_points(args.points),
        _camera_source(args.camera_source),
        args.width,
        args.height,
        Path(args.output_dir),
        args.stable_delay,
        args.warmup_frames,
        args.camera_backend,
        args.soft_home,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
