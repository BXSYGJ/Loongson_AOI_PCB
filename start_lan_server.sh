#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

echo "Starting FastAPI on 0.0.0.0:8000 ..."
echo "Open http://<this-machine-ip>:8000/ from other devices on the same LAN."

python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
