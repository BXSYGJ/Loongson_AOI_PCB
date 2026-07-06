#!/usr/bin/env sh
set -eu

VENV_DIR="${AOI_VENV:-$HOME/aoi_venv_312}"
INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
UI_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "Python venv not found: $VENV_DIR" >&2
    echo "Set AOI_VENV to the Python 3.12 venv path and retry." >&2
    exit 1
fi

. "$VENV_DIR/bin/activate"
cd "$UI_DIR"

echo "Using Python: $(command -v python)"
python -V

python - <<'PY'
import cv2
import numpy
print("OpenCV", cv2.__version__, "dnn", hasattr(cv2, "dnn"))
print("NumPy", numpy.__version__)
PY

echo "Installing web dependencies from $INDEX_URL ..."
if python -m pip install fastapi uvicorn websockets anyio sniffio h11 click typing_extensions -i "$INDEX_URL" --timeout 120 --retries 10; then
    :
else
    echo "Latest FastAPI stack failed; retrying pydantic v1 compatible stack ..."
    python -m pip install -r requirements-loongson.txt -i "$INDEX_URL" --timeout 120 --retries 10
fi

python - <<'PY'
import fastapi
import uvicorn
import cv2
import numpy
print("deps ok", fastapi.__version__, uvicorn.__version__, cv2.__version__, numpy.__version__)
PY
