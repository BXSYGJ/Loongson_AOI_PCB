$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CloudDir = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $CloudDir

if (-not $env:AOI_STORAGE_BACKEND) { $env:AOI_STORAGE_BACKEND = "local" }
if (-not $env:AOI_DETECTOR_BACKEND) { $env:AOI_DETECTOR_BACKEND = "fake" }
if (-not $env:AOI_PUBLIC_API_BASE) { $env:AOI_PUBLIC_API_BASE = "http://127.0.0.1:8010" }
if (-not $env:AOI_PUBLIC_WS_BASE) { $env:AOI_PUBLIC_WS_BASE = "ws://127.0.0.1:8010" }

Write-Host "Starting Loongson AOI cloud backend..."
Write-Host "Storage: $env:AOI_STORAGE_BACKEND"
Write-Host "Detector: $env:AOI_DETECTOR_BACKEND"
Write-Host "API: $env:AOI_PUBLIC_API_BASE"
Write-Host "WS: $env:AOI_PUBLIC_WS_BASE"

python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
