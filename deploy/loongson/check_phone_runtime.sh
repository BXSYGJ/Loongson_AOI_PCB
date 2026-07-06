#!/usr/bin/env sh
set -eu

VENV_DIR="${AOI_VENV:-$HOME/aoi_venv_312}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
UI_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "Python venv not found: $VENV_DIR" >&2
    exit 1
fi

. "$VENV_DIR/bin/activate"
. "$SCRIPT_DIR/env_phone_detection.sh"
cd "$UI_DIR"

echo "Compile check ..."
python -m py_compile main.py camera_trigger.py motor_controller.py

echo "Dependency check ..."
python - <<'PY'
import cv2
import fastapi
import numpy
import uvicorn
print("deps ok")
print("fastapi", fastapi.__version__)
print("uvicorn", uvicorn.__version__)
print("opencv", cv2.__version__, "dnn", hasattr(cv2, "dnn"))
print("numpy", numpy.__version__)
PY

echo "Detector check ..."
python - <<'PY'
import json
import camera_trigger
status = camera_trigger.detector_status()
print(json.dumps(status, ensure_ascii=False, indent=2))
if status.get("backend") != "onnx_opencv":
    raise SystemExit("detector backend is not onnx_opencv")
if not status.get("weights_exists"):
    raise SystemExit("ONNX weights not found")
if int(status.get("onnx_input_imgsz") or 0) != 640:
    raise SystemExit("ONNX input size is not 640")
PY

echo "Runtime check passed."
