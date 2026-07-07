# Loongson AOI Cloud Backend

This service is the first cloud-side endpoint for the AOI project.
It supports WeChat mini program image upload, local mock storage, optional Huawei OBS storage, asynchronous ONNX detection, and WebSocket job result delivery.

## Local Mock Run

```powershell
cd D:\AAAED\code\Loongson_AOI_PCB\cloud-backend
$env:AOI_STORAGE_BACKEND = "local"
$env:AOI_DETECTOR_BACKEND = "fake"
$env:AOI_PUBLIC_API_BASE = "http://127.0.0.1:8010"
$env:AOI_PUBLIC_WS_BASE = "ws://127.0.0.1:8010"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010/health` to verify the service.

## ECS ONNX Run

```powershell
$env:AOI_STORAGE_BACKEND = "obs"
$env:AOI_DETECTOR_BACKEND = "onnx"
$env:AOI_MODEL_PATH = "D:\path\to\best.onnx"
$env:AOI_CONFIG_PATH = "D:\path\to\pcb_defects.yaml"
$env:HUAWEI_OBS_ENDPOINT = "https://obs.cn-north-4.myhuaweicloud.com"
$env:HUAWEI_OBS_BUCKET = "your-bucket"
$env:HUAWEI_OBS_AK = "your-ak"
$env:HUAWEI_OBS_SK = "your-sk"
$env:AOI_PUBLIC_API_BASE = "https://aoi-api.example.com"
$env:AOI_PUBLIC_WS_BASE = "wss://aoi-api.example.com"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Keep long-term AK/SK only on ECS. The mini program receives only short-lived upload URLs.

## API Flow

1. `POST /api/cloud/jobs`
2. Upload image to returned `upload_url`.
3. `POST /api/cloud/jobs/{job_id}/uploaded`
4. Listen on `ws_url` or poll `GET /api/cloud/jobs/{job_id}`.

The local storage mode is intentionally API-compatible with OBS mode, so the mini program can be tested before Huawei Cloud resources are created.
