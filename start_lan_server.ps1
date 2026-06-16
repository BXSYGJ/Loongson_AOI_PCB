$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Starting FastAPI on 0.0.0.0:8000 ..."
Write-Host "Open http://<this-machine-ip>:8000/ from other devices on the same LAN."

python -m uvicorn main:app --host 0.0.0.0 --port 8000
