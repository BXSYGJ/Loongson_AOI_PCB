# Loongson AOI PCB - Cloud Cooperative Platform

This branch contains the cloud-cooperative web console for the PCB AOI workflow.
It is derived from the phone-browser-inference branch, but the detection
priority is now:

```text
Huawei Cloud ECS detection > same-LAN phone/browser inference > local computer or Loongson board fallback
```

The Loongson Education board is still responsible for real-mode slide-table
movement and OV9281 capture. Defect inference is offloaded whenever possible,
with local board inference kept as the lowest-priority fallback.

The branch connects:

- OV9281 camera capture and MJPEG preview
- Huawei Cloud ECS + OBS ONNX detection
- same-LAN phone/browser ONNX inference worker
- local OpenCV DNN fallback detection
- XYZ slide-table scan orchestration
- WebSocket event updates for the browser UI
- Handheld, simulated, real stepped-scan, continuous video scan, and phone-upload workflows

The current UI is designed as a white industrial control panel. It is the branch
to run when you want the local website to coordinate camera/slide-table control
while sending detection jobs to Huawei Cloud first.

## Main Files

| File                    | Purpose                                                               |
| ----------------------- | --------------------------------------------------------------------- |
| `main.py`             | FastAPI app, WebSocket control protocol, scan orchestration           |
| `camera_trigger.py`   | Camera manager, YOLO detector, still capture, video detection session |
| `motor_controller.py` | UI-side motor interface used by scan commands                         |
| `static/index.html`   | Browser shell                                                         |
| `static/app.js`       | Frontend state machine, WebSocket client, mode logic                  |
| `static/styles.css`   | White industrial UI styling                                           |
| `requirements.txt`    | Python runtime dependencies                                           |

## Modes

| Mode               | Hardware Needed             | Behavior                                                                                                             |
| ------------------ | --------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Handheld detection | Camera + YOLO               | No slide movement. The camera can be moved by hand and the video stream shows live YOLO boxes.                       |
| Simulation mode    | Existing PCB image          | Upload one or more PCB images and simulate a snake-grid slide scan over the image.                                   |
| Real mode          | Camera + slide table + YOLO | Step the slide to each grid cell, settle, capture one image, run YOLO, then move to the next cell.                   |
| Video detection    | Camera + slide table + YOLO | Continuous snake scan with live YOLO video and a moving FOV box on the PCB board.                                    |
| Phone photo        | Phone or desktop browser     | Upload phone photos, convert color images to grayscale, then detect through cloud, phone worker, or local fallback. |

All five modes use the same detection scheduler. If
`LOONGSON_AOI_CLOUD_API_BASE` is configured and reachable, the raw captured or
uploaded image is sent to Huawei Cloud first. If cloud detection is unavailable,
the website tries the same-LAN phone/browser inference worker. If that is also
unavailable, it falls back to local OpenCV DNN inference.

## Quick Start on Windows

From this branch checkout:

```powershell
cd D:\AAAED\code\Loongson_AOI_PCB
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

For LAN phone upload, bind to all interfaces:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open the full UI or the phone-only upload page from the phone:

```text
http://<computer-lan-ip>:8000/
http://<computer-lan-ip>:8000/phone
```

If port `8000` is already occupied, either close the old server or use another
port:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Then open `http://127.0.0.1:8001/`.

## Camera Source

The camera source can be changed without editing code.

The Loongson board with OV9281 is usually `0`.  Some Windows or USB camera
setups may enumerate the camera as `1`, so change only this value when moving
between devices.

Option 1: write the source into `camera_source.txt`:

```powershell
Set-Content -Path .\camera_source.txt -Value "0"
# Or, on another device:
Set-Content -Path .\camera_source.txt -Value "1"
```

Option 2: set an environment variable before starting the server:

```powershell
$env:LOONGSON_AOI_CAMERA_SOURCE = "0"
# Or, on another device:
$env:LOONGSON_AOI_CAMERA_SOURCE = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful camera variables:

```powershell
$env:LOONGSON_AOI_CAMERA_SOURCE = "0"
$env:LOONGSON_AOI_CAMERA_WIDTH = "1280"
$env:LOONGSON_AOI_CAMERA_HEIGHT = "800"
$env:LOONGSON_AOI_CAMERA_BACKEND = "DSHOW"
```

On Windows, `DSHOW` is often more stable than the default MSMF backend for USB
or industrial cameras. On the Loongson Linux board, use sources such as
`/dev/video0`.

## ONNX Detector Weights

The UI now defaults to OpenCV DNN + FP32 ONNX inference so it can run on the
Loongson board without PyTorch or Ultralytics.  Export `best.pt` to ONNX on a
PC, then place it under the algorithm branch layout:

```text
worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx
```

Recommended export command on the PC:

```powershell
yolo export model=runs/detect/pcb_defects_20260704_035505/weights/best.pt format=onnx imgsz=640 opset=12 simplify=False
```

You can override the model path and runtime:

```powershell
$env:LOONGSON_AOI_WEIGHTS = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai\runs\detect\pcb_defects\weights\best.onnx"
$env:LOONGSON_AOI_ALGORITHM_ROOT = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai"
$env:LOONGSON_AOI_DETECTOR_BACKEND = "onnx"
$env:LOONGSON_AOI_YOLO_CONF = "0.5"
$env:LOONGSON_AOI_YOLO_IMGSZ = "640"
```

Passing a `.pt` path and `LOONGSON_AOI_DETECTOR_BACKEND=ultralytics` still works
on development machines that have `torch` and `ultralytics`, but it is not the
recommended board runtime.

## Huawei Cloud Detection Priority

Configure the local website to use the Huawei Cloud backend before starting
Uvicorn:

```powershell
$env:LOONGSON_AOI_CLOUD_DETECTION_ENABLED = "1"
$env:LOONGSON_AOI_CLOUD_API_BASE = "http://1.92.113.191:8010"
$env:LOONGSON_AOI_CLOUD_TIMEOUT_SECONDS = "90"
$env:LOONGSON_AOI_CLOUD_POLL_SECONDS = "0.35"
```

Then start the website:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

`GET /api/system/status` reports the active detection priority and cloud
configuration:

```json
{
  "detection_priority": ["huawei_cloud", "phone_browser", "local_fallback"],
  "cloud_detection": {
    "enabled": true,
    "configured": true,
    "api_base": "http://1.92.113.191:8010"
  }
}
```

Huawei Cloud results are written back into the same local output layout as local
detection. This keeps the UI, manifest, mosaic, and artifact viewer compatible
with the existing simulation, real, video, and phone workflows.

## Loongson 2K1000LA Simulation-First Deployment

For the first board-side validation stage, use simulation mode before phone
photo, camera, or slide-table control. Import a known PCB image, set the PCB
size to `30mm x 30mm`, and run a single-cell scan so the board only performs
one ONNX inference after mosaic restoration. This is the safest path for
proving the website, OpenCV DNN runtime, event stream, and output images on the
2K1000LA.

The deployment guide and scripts are in:

```text
LOONGSON_2K1000LA_PHONE_DEPLOY.md
deploy/loongson/
```

From Windows PowerShell, build the transfer bundle with:

```powershell
.\deploy\loongson\make_loongson_phone_bundle.ps1
```

Copy `dist\loongson_phone_bundle.zip` to `~/Documents` on the board, unzip it,
then run:

```sh
cd ~/Documents/Loongson_AOI_PCB/worktrees/ui-integration
sh deploy/loongson/install_phone_deps.sh
sh deploy/loongson/check_phone_runtime.sh
sh deploy/loongson/run_phone_server.sh
```

The Loongson script enables board-safe defaults:

```sh
LOONGSON_AOI_BOARD_SAFE_MODE=1
LOONGSON_AOI_OPENCV_THREADS=1
LOONGSON_AOI_SIMULATION_MAX_DIMENSION=960
LOONGSON_AOI_SIMULATION_JPEG_QUALITY=82
```

It also starts Uvicorn through `nice -n 10` by default so ONNX inference has a
lower chance of starving the SSH/FinalShell session. After opening
`http://<board-ip>:8000/`, enter simulation mode, import a `640x640` image or a
PCB image with long side no larger than `960`, set PCB length/width to
`30mm x 30mm`, then start detection.

## Phone Browser Inference Fallback

The phone module remains useful in two ways:

- as the fifth mode for taking or selecting PCB photos and sending them to the
  cloud platform;
- as a same-LAN browser inference worker when Huawei Cloud is unavailable.

The phone can either open the complete UI or the minimal `/phone` page. The
phone page has separate buttons for camera capture and gallery selection.

When `/phone` loads `best.onnx` successfully, it can act as the second-priority
inference worker for all five modes:

- handheld realtime detection
- simulation grid detection and final mosaic review
- real slide-table step-and-capture detection
- continuous video scan detection
- phone photo whole-board detection

If Huawei Cloud is unavailable and no phone worker is online, the Windows
computer or Loongson board falls back to local inference automatically. The
desktop status panel shows both `华为云检测` and `手机推理` so the active path is
visible during demos.

On the 2K1000LA, use phone detection only after simulation mode is stable.
Large phone photos can still be much heavier than the simulation single-cell
path because tiled detection may require multiple ONNX forwards.

Phone uploads are normalized before inference:

- browser-side conversion from color to grayscale JPEG
- server-side grayscale conversion as a second safety check
- original board dimensions are preserved for UI display and box coordinates
- YOLO inference uses in-memory tiled detection rather than resizing the board
- the website offers two detection profiles: `快速模式` for normal demos and
  `高精模式` for slower multi-profile review

Default fast profile settings:

```powershell
$env:LOONGSON_AOI_PHONE_FAST_TILE_SIZES = "640"
$env:LOONGSON_AOI_PHONE_FAST_IMGSZS = "640"
$env:LOONGSON_AOI_PHONE_FAST_TILE_OVERLAP = "0.5"
```

High-precision profile settings:

```powershell
$env:LOONGSON_AOI_PHONE_PRECISION_TILE_SIZES = "512,640,800"
$env:LOONGSON_AOI_PHONE_PRECISION_IMGSZS = "640,960"
$env:LOONGSON_AOI_PHONE_PRECISION_TILE_OVERLAP = "0.5"
```

The fast profile keeps latency low for the Loongson board by running one light
tile/imgsz profile while retaining 50% overlap to avoid missing defects near
tile borders. The high-precision profile tests several tile/imgsz profiles,
then fuses overlapping boxes so repeated medium-confidence evidence can become
a confirmed defect while weaker one-off evidence is kept as suspected. Fusion
thresholds can be tuned without code changes:

```powershell
$env:LOONGSON_AOI_PHONE_CONFIRM_CONF = "0.5"
$env:LOONGSON_AOI_PHONE_MULTI_CONFIRM_CONF = "0.35"
$env:LOONGSON_AOI_PHONE_SUSPECTED_CONF = "0.2"
$env:LOONGSON_AOI_PHONE_FUSION_IOU = "0.45"
```

Phone outputs are written under:

```text
algorithm/outputs/mobile_uploads/<upload_id>/
  raw.jpg
  annotated.jpg
  result.json
  metadata.json
```

## Slide Scan Settings

Common scan and video-scan variables:

```powershell
$env:LOONGSON_AOI_DEBOUNCE_SECONDS = "0.05"
$env:LOONGSON_AOI_VIDEO_SPEED_MM_S = "2.0"
$env:LOONGSON_AOI_VIDEO_INFERENCE_FPS = "5"
$env:LOONGSON_AOI_VIDEO_PREVIEW_FPS = "8"
$env:LOONGSON_AOI_VIDEO_OVERLAP = "0.2"
```

Real slide modes require the slide to be placed at the physical origin and
soft-homed before scanning when no limit switches are installed.

## HTTP Interfaces

| Endpoint                                  | Purpose                                                |
| ----------------------------------------- | ------------------------------------------------------ |
| `GET /`                                 | Web console                                            |
| `GET /health`                           | Server health check                                    |
| `GET /api/system/status`                | Motor, camera, algorithm, and scan readiness           |
| `GET /api/camera/status`                | Camera open state and latest capture metadata          |
| `GET /api/camera/stream.mjpg`           | Raw camera preview stream                              |
| `GET /api/video/status`                 | Live YOLO video session status                         |
| `GET /api/video/stream.mjpg`            | Annotated YOLO video stream                            |
| `POST /api/simulation/upload`           | Upload PCB images for simulation mode                  |
| `GET /phone`                            | Minimal phone upload page                              |
| `POST /api/phone/upload`                | Upload phone camera/gallery images                     |
| `GET /api/phone/uploads`                | List phone upload queue                                |
| `POST /api/phone/detect`                | Run tiled whole-board YOLO detection on a phone upload |
| `DELETE /api/phone/uploads/{upload_id}` | Delete a phone upload record and files                 |
| `WS /ws/control`                        | Start/stop scan commands and live events               |

## Validation

Basic local checks:

```powershell
python -m py_compile main.py camera_trigger.py motor_controller.py
node --check static/app.js
python -m unittest test_mosaic
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/system/status
```

## Branch Relationship

- `ui-integration`: this web console and integration runtime
- `hardware-sm`: TB6600/XYZ slide-table driver
- `algorithm-ai`: YOLO training, evaluation, and standalone camera detection
- `main`: kept untouched unless the team decides to merge tested branches
