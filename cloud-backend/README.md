# Loongson AOI Cloud Backend

This service is the Huawei Cloud ECS-side detection backend for the AOI project.
It normally listens on port `8010` and is called by the full website running on
Windows, Loongson, or ECS port `8020`.

It supports:

- WeChat mini program and website image upload jobs.
- Local mock storage for development.
- Huawei OBS object storage for deployed demos.
- Asynchronous OpenCV DNN + ONNX detection.
- WebSocket job progress and result delivery.

The backend is intentionally independent from slide-table control. Real-mode
movement and OV9281 capture stay in the full website/Loongson process; this
service only receives images and returns `annotated.jpg` plus `result.json`.

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

For the current competition deployment:

```text
Full website: http://1.92.113.191:8020/
Cloud backend: http://1.92.113.191:8010/
OBS bucket: loongson-aoi-pcb
Region: cn-north-4
```

The website should point to this backend through:

```powershell
$env:LOONGSON_AOI_CLOUD_DETECTION_ENABLED = "1"
$env:LOONGSON_AOI_CLOUD_API_BASE = "http://1.92.113.191:8010"
```

## API Flow

1. `POST /api/cloud/jobs`
2. Upload image to returned `upload_url`.
3. `POST /api/cloud/jobs/{job_id}/uploaded`
4. Listen on `ws_url` or poll `GET /api/cloud/jobs/{job_id}`.

The local storage mode is intentionally API-compatible with OBS mode, so the mini program can be tested before Huawei Cloud resources are created.

## Relationship to the Full Website

Use `cloud-backend` when you only need the ECS detection API. Use
`worktrees/cloud-cooperative-platform` when you need the browser UI, phone page,
LLM report button, camera preview, slide-table control, and real-mode scan
orchestration.

The full website can choose one of three detection platforms:

- Huawei Cloud: calls this service.
- Phone/browser worker: sends work to a connected `/phone` worker.
- Local DNN: runs `best.onnx` in the website process.
