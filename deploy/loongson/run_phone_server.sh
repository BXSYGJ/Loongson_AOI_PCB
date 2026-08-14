#!/usr/bin/env sh
set -eu

VENV_DIR="${AOI_VENV:-$HOME/aoi_venv_312}"
HOST="${AOI_HOST:-0.0.0.0}"
PORT="${AOI_PORT:-8000}"
NICE_LEVEL="${AOI_NICE:-10}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
UI_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "Python venv not found: $VENV_DIR" >&2
    echo "Set AOI_VENV to the Python 3.12 venv path and retry." >&2
    exit 1
fi

. "$VENV_DIR/bin/activate"
. "$SCRIPT_DIR/env_phone_detection.sh"
export LOONGSON_AOI_BOARD_LITE_UI="${LOONGSON_AOI_BOARD_LITE_UI:-1}"
cd "$UI_DIR"

echo "Starting Loongson AOI simulation-first server ..."
echo "UI dir: $UI_DIR"
echo "Algorithm root: $LOONGSON_AOI_ALGORITHM_ROOT"
echo "Weights: $LOONGSON_AOI_WEIGHTS"
echo "Motor dry-run: $LOONGSON_AOI_MOTOR_DRY_RUN"
echo "Board safe mode: $LOONGSON_AOI_BOARD_SAFE_MODE"
echo "Board lite UI: $LOONGSON_AOI_BOARD_LITE_UI"
echo "Simulation max dimension: $LOONGSON_AOI_SIMULATION_MAX_DIMENSION"
echo "OpenCV threads: $LOONGSON_AOI_OPENCV_THREADS"
echo "Listen: $HOST:$PORT"
if command -v hostname >/dev/null 2>&1; then
    echo "LAN candidates: $(hostname -I 2>/dev/null || true)"
fi

if command -v nice >/dev/null 2>&1 && [ "$NICE_LEVEL" != "0" ]; then
    exec nice -n "$NICE_LEVEL" python -m uvicorn main:app --host "$HOST" --port "$PORT"
fi

exec python -m uvicorn main:app --host "$HOST" --port "$PORT"
