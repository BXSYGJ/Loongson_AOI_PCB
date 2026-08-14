#!/usr/bin/env sh
set -eu

# Usage:
#   sh start_loongson_aoi_server.sh          # real stage + real camera
#   sh start_loongson_aoi_server.sh dryrun   # simulated stage, safer for testing
#
# This script is intended to run on the Loongson board.

MODE="${1:-real}"
PROJECT_DIR="${AOI_PROJECT_DIR:-/home/loongson/Documents/Loongson_AOI_PCB/worktrees/cloud-cooperative-platform}"
VENV_DIR="${AOI_VENV:-/home/loongson/aoi_venv_312}"

if [ "$MODE" = "dryrun" ] || [ "$MODE" = "safe" ]; then
    MOTOR_DRY_RUN=1
    BOARD_SAFE_MODE=1
else
    MOTOR_DRY_RUN=0
    BOARD_SAFE_MODE=0
fi

cd "$PROJECT_DIR"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "Python venv not found: $VENV_DIR" >&2
    exit 1
fi

. "$VENV_DIR/bin/activate"

export AOI_VENV="$VENV_DIR"
export AOI_HOST="${AOI_HOST:-0.0.0.0}"
export AOI_PORT="${AOI_PORT:-8000}"

export LOONGSON_AOI_CAMERA_SOURCE="${LOONGSON_AOI_CAMERA_SOURCE:-0}"
export LOONGSON_AOI_CAMERA_FOV_LONG_MM="${LOONGSON_AOI_CAMERA_FOV_LONG_MM:-39.0}"

export LOONGSON_AOI_MOTOR_DRY_RUN="$MOTOR_DRY_RUN"
export LOONGSON_AOI_BOARD_SAFE_MODE="$BOARD_SAFE_MODE"
export LOONGSON_AOI_HOME_MODE="${LOONGSON_AOI_HOME_MODE:-soft}"
export LOONGSON_AOI_MOTOR_GPIO_BACKEND="${LOONGSON_AOI_MOTOR_GPIO_BACKEND:-system_python}"
export LOONGSON_AOI_SYSTEM_GPIO_PYTHON="${LOONGSON_AOI_SYSTEM_GPIO_PYTHON:-/usr/bin/python3}"
export LOONGSON_AOI_MOTOR_AXIS_MAPPING="${LOONGSON_AOI_MOTOR_AXIS_MAPPING:-physical_x_motor_y_physical_y_motor_x}"

export LOONGSON_AOI_STITCH_AXIS_MAPPING="${LOONGSON_AOI_STITCH_AXIS_MAPPING:-slide_x_image_x_slide_y_image_y}"

# Keep this as onnx: camera_trigger only supports local detector backend names here.
# Huawei Cloud is enabled by the two cloud variables below.
export LOONGSON_AOI_DETECTOR_BACKEND="${LOONGSON_AOI_DETECTOR_BACKEND:-onnx}"
export LOONGSON_AOI_CLOUD_DETECTION_ENABLED="${LOONGSON_AOI_CLOUD_DETECTION_ENABLED:-1}"
export LOONGSON_AOI_CLOUD_API_BASE="${LOONGSON_AOI_CLOUD_API_BASE:-http://116.205.117.33:8010}"

export LOONGSON_AOI_ONNX_INPUT_IMGSZ="${LOONGSON_AOI_ONNX_INPUT_IMGSZ:-640}"
export LOONGSON_AOI_YOLO_IMGSZ="${LOONGSON_AOI_YOLO_IMGSZ:-640}"
export LOONGSON_AOI_YOLO_CONF="${LOONGSON_AOI_YOLO_CONF:-0.5}"
export LOONGSON_AOI_OPENCV_THREADS="${LOONGSON_AOI_OPENCV_THREADS:-1}"

echo "Starting AOI server"
echo "Project: $PROJECT_DIR"
echo "Mode: $MODE"
echo "Motor dry-run: $LOONGSON_AOI_MOTOR_DRY_RUN"
echo "Board safe mode: $LOONGSON_AOI_BOARD_SAFE_MODE"
echo "Cloud detection: $LOONGSON_AOI_CLOUD_DETECTION_ENABLED"
echo "Cloud API: $LOONGSON_AOI_CLOUD_API_BASE"
echo "Listen: $AOI_HOST:$AOI_PORT"

sh deploy/loongson/run_phone_server.sh
