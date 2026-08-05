#!/bin/sh
set -eu

ZIP_PATH="${1:-/root/Loongson_AOI_PCB_huawei_cloud.zip}"
PROJECT_ROOT="/root/Loongson_AOI_PCB"
STAGE_ROOT="/root/aoi_v1_update"
BACKUP_ROOT="/root/aoi_v1_backup_$(date +%Y%m%d_%H%M%S)"

export DEBIAN_FRONTEND=noninteractive
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-90}"
apt-get update
apt-get install -y python3 python3-venv python3-pip rsync unzip libgl1 libglib2.0-0t64

systemctl stop aoi-web 2>/dev/null || true
systemctl stop aoi-cloud 2>/dev/null || true
rm -rf "$STAGE_ROOT"
mkdir -p "$STAGE_ROOT" "$BACKUP_ROOT"
unzip -q -o "$ZIP_PATH" -d "$STAGE_ROOT"
SOURCE_ROOT="$STAGE_ROOT/Loongson_AOI_PCB"

if [ ! -f "$SOURCE_ROOT/VERSION" ] || [ ! -d "$SOURCE_ROOT/cloud-backend" ] || [ ! -d "$SOURCE_ROOT/worktrees/cloud-cooperative-platform" ]; then
  echo "v1.0.0 package layout is invalid" >&2
  exit 1
fi

mkdir -p "$PROJECT_ROOT/worktrees"
for path in cloud-backend worktrees/cloud-cooperative-platform worktrees/algorithm-ai; do
  if [ -e "$PROJECT_ROOT/$path" ]; then
    mkdir -p "$BACKUP_ROOT/$(dirname "$path")"
    cp -a "$PROJECT_ROOT/$path" "$BACKUP_ROOT/$path"
  fi
done

mkdir -p "$PROJECT_ROOT/cloud-backend" "$PROJECT_ROOT/worktrees/cloud-cooperative-platform" "$PROJECT_ROOT/worktrees/algorithm-ai"
rsync -a --delete --exclude .env --exclude .venv --exclude .data --exclude __pycache__ --exclude .pytest_cache "$SOURCE_ROOT/cloud-backend/" "$PROJECT_ROOT/cloud-backend/"
rsync -a --delete --exclude .env --exclude .venv --exclude .data --exclude algorithm/outputs --exclude __pycache__ --exclude .pytest_cache "$SOURCE_ROOT/worktrees/cloud-cooperative-platform/" "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/"
rsync -a --delete --exclude algorithm/outputs "$SOURCE_ROOT/worktrees/algorithm-ai/" "$PROJECT_ROOT/worktrees/algorithm-ai/"
cp "$SOURCE_ROOT/VERSION" "$PROJECT_ROOT/VERSION"

if [ ! -f "$PROJECT_ROOT/cloud-backend/.env" ]; then
  cp "$SOURCE_ROOT/config/private/cloud-backend.env" "$PROJECT_ROOT/cloud-backend/.env"
fi
if [ ! -f "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.env" ]; then
  cp "$SOURCE_ROOT/config/private/cloud-web.env" "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.env"
fi
if ! grep -q '^LOONGSON_AOI_STATUS_CAMERA_PROBE=' "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.env"; then
  printf '%s\n' 'LOONGSON_AOI_STATUS_CAMERA_PROBE=0' >> "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.env"
fi
cp "$SOURCE_ROOT/config/private/llm_config.local.ps1" "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/llm_config.local.ps1"

if [ ! -x "$PROJECT_ROOT/cloud-backend/.venv/bin/python" ]; then
  python3 -m venv "$PROJECT_ROOT/cloud-backend/.venv"
fi
if [ ! -x "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.venv/bin/python" ]; then
  python3 -m venv "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.venv"
fi
"$PROJECT_ROOT/cloud-backend/.venv/bin/python" -m pip install -r "$PROJECT_ROOT/cloud-backend/requirements.txt"
"$PROJECT_ROOT/worktrees/cloud-cooperative-platform/.venv/bin/python" -m pip install -r "$PROJECT_ROOT/worktrees/cloud-cooperative-platform/requirements.txt"

cp "$SOURCE_ROOT/deploy/huawei/aoi-cloud.service" /etc/systemd/system/aoi-cloud.service
cp "$SOURCE_ROOT/deploy/huawei/aoi-web.service" /etc/systemd/system/aoi-web.service
systemctl daemon-reload
systemctl enable aoi-cloud aoi-web
systemctl restart aoi-cloud
systemctl restart aoi-web
sleep 3
curl --fail --silent --show-error http://127.0.0.1:8010/health
echo
curl --fail --silent --show-error http://127.0.0.1:8020/health
echo
echo "v1.0.0 deployment complete; backup: $BACKUP_ROOT"
