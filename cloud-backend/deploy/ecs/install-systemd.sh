#!/usr/bin/env sh
set -eu

APP_DIR="/root/Loongson_AOI_PCB/cloud-backend"
SERVICE_SRC="$APP_DIR/deploy/ecs/aoi-cloud.service"
SERVICE_DST="/etc/systemd/system/aoi-cloud.service"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "Missing $APP_DIR/.env"
  exit 1
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "Missing Python venv at $APP_DIR/.venv"
  exit 1
fi

cp "$SERVICE_SRC" "$SERVICE_DST"
chmod 644 "$SERVICE_DST"
systemctl daemon-reload
systemctl enable aoi-cloud.service
systemctl restart aoi-cloud.service
systemctl status aoi-cloud.service --no-pager
