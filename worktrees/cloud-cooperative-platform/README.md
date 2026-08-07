# Loongson AOI PCB - Cloud Cooperative Platform

This branch contains the current cloud-cooperative web console for the PCB AOI
workflow. It is the main branch to run on Windows, Huawei Cloud ECS, or the
Loongson Education board when coordinating the complete AOI demo.

The product UI separates **operation mode** from an **experiment profile**:

```text
Operation mode: real | phone
Experiment profile: local_link_sim | phone_link_sim | cloud_baseline
Executed platform: Huawei Cloud
```

The profile order is always Local (Loongson), Phone, Huawei Cloud, with Local
selected by default. All three profiles execute inference on Huawei Cloud. The
phone workflow keeps the configured link-delay experiment; real mode adds no
simulated delay.

The Loongson Education board is responsible for real-mode slide-table movement
and OV9281 capture. Product inference is handled by Huawei Cloud. The lower-level
phone/browser worker and local OpenCV DNN routes remain available for diagnostics
and legacy API compatibility, but are not selected by the product UI.

The branch connects:

- OV9281 camera capture and MJPEG preview
- Huawei Cloud ECS + OBS ONNX detection
- same-LAN phone/browser ONNX inference worker
- local OpenCV DNN detection platform
- XYZ slide-table scan orchestration
- WebSocket event updates for the browser UI
- real fixed-point scan and phone-upload product workflows

The current UI is designed as a white industrial control panel. It is the branch
to run when you want the local website to coordinate camera/slide-table control
and compare three experiment profiles while keeping actual inference on Huawei Cloud.

## Main Files

| File                    | Purpose                                                               |
| ----------------------- | --------------------------------------------------------------------- |
| `main.py`             | FastAPI app, WebSocket control protocol, scan orchestration           |
| `camera_trigger.py`   | Camera manager, OpenCV DNN/ONNX detector, still capture, mosaic fusion |
| `motor_controller.py` | UI-side motor interface used by scan commands                         |
| `static/index.html`   | Browser shell                                                         |
| `static/app.js`       | Frontend state machine, WebSocket client, mode logic                  |
| `static/styles.css`   | White industrial UI styling                                           |
| `requirements.txt`    | Python runtime dependencies                                           |
| `cloud-backend/`      | Huawei Cloud ECS + OBS async detection service                        |
| `miniprogram/`        | WeChat mini program aligned with the phone web page                   |

## Modes

| Mode            | Hardware Needed                     | Behavior                                                                                                       |
| --------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Simulation mode | Existing PCB image                  | Import a PCB image, simulate a stepped scan, fill the board view, then build a restored mosaic for review.      |
| Real mode       | OV9281 + slide table                | Capture the first point immediately, then move through a fixed 16-point scan, capture each point, and mosaic.   |
| Phone photo     | Phone or desktop browser image input | Upload or capture a PCB photo, convert color input to grayscale, and run whole-image detection.                 |

Each mode carries `detection_platform` in its request payload:

- `huawei_cloud`: send the image to the ECS/OBS backend.
- `phone_browser`: use a connected phone/browser inference worker.
- `local_dnn`: run `best.onnx` through OpenCV DNN on the current backend host.

The current implementation avoids automatic platform fallback. This makes
competition demos easier to explain because every result clearly states which
platform produced it.

## Quick Start on Windows

From this branch checkout:

```powershell
cd D:\AAAED\code\Loongson_AOI_PCB
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

Open:

```text
http://127.0.0.1:8020/
```

For LAN phone upload, bind to all interfaces:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8020
```

Then open the full UI or the phone-only upload page from the phone:

```text
http://<computer-lan-ip>:8020/
http://<computer-lan-ip>:8020/phone
```

If port `8020` is already occupied, either close the old server or use another
port:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8021
```

Then open `http://127.0.0.1:8021/`.

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
python -m uvicorn main:app --host 127.0.0.1 --port 8020
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
$env:LOONGSON_AOI_YOLO_CONF = "0.3"
$env:LOONGSON_AOI_YOLO_IMGSZ = "640"
```

Passing a `.pt` path and `LOONGSON_AOI_DETECTOR_BACKEND=ultralytics` still works
on development machines that have `torch` and `ultralytics`, but it is not the
recommended board runtime.

## Huawei Cloud Detection Platform

Configure the local website to use the Huawei Cloud backend before starting
Uvicorn:

```powershell
$env:LOONGSON_AOI_CLOUD_DETECTION_ENABLED = "1"
$env:LOONGSON_AOI_CLOUD_API_BASE = "http://116.205.117.33:8010"
$env:LOONGSON_AOI_CLOUD_TIMEOUT_SECONDS = "90"
$env:LOONGSON_AOI_CLOUD_POLL_SECONDS = "0.35"
```

Then start the website:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8020
```

`GET /api/system/status` reports platform availability and cloud configuration:

```json
{
  "detection_platforms": {
    "huawei_cloud": {"available": true},
    "phone_browser": {"available": false},
    "local_dnn": {"available": true}
  },
  "cloud_detection": {
    "enabled": true,
    "configured": true,
    "api_base": "http://116.205.117.33:8010"
  }
}
```

Huawei Cloud results are written back into the same output layout. This keeps
the UI, manifest, mosaic, and artifact viewer compatible with real and phone
workflows. Internal simulation helpers remain available for tests.

## Loongson 2K1000LA Deployment

Use the formal three-platform release package. Validate the website in motor
dry-run first, then enable the physical slide table only after checking origin,
wiring, travel limits, camera focus, and emergency-stop conditions.

The deployment guide and scripts are in:

```text
LOONGSON_2K1000LA_PHONE_DEPLOY.md
deploy/loongson/
```

From Windows PowerShell, build the formal release packages with:

```powershell
pwsh -File .\tools\build_packages.ps1
```

Use `packages/Loongson_AOI_PCB_loongson_2k1000la.zip`. Do not use the legacy
`dist/loongson_phone_bundle.zip`. After extracting the formal package, follow
`docs/DEPLOY_LOONGSON.md` or `deploy/loongson/MANUAL_DEPLOY.md`.

The Loongson script enables board-safe defaults:

```sh
LOONGSON_AOI_BOARD_SAFE_MODE=1
LOONGSON_AOI_OPENCV_THREADS=1
LOONGSON_AOI_SIMULATION_MAX_DIMENSION=960
LOONGSON_AOI_SIMULATION_JPEG_QUALITY=82
```

It also starts Uvicorn through `nice -n 10` by default so ONNX inference has a
lower chance of starving the SSH/FinalShell session. The board script defaults
to port `8000`; set `AOI_PORT=8020` before running it if you want the same port
as the ECS/Windows demo. After opening `http://<board-ip>:8000/` or
`http://<board-ip>:8020/`, the product UI offers real mode and phone-photo mode.
Both default to the Local (Loongson) experiment profile, while actual inference
is sent to Huawei Cloud.

## Phone Photo Workflow

The phone module takes or selects PCB photos and uploads them for Huawei Cloud
inference. Its Local, Phone, and Huawei Cloud choices are experiment profiles,
not actual inference routes.

The phone can either open the complete UI or the minimal `/phone` page. The
phone page has separate buttons for camera capture and gallery selection.

The product UI does not start or wait for the legacy phone inference worker.
If Huawei Cloud is unavailable, preflight blocks all three experiment profiles.
Local DNN and phone-worker capabilities remain available only for diagnostics
and compatible legacy callers.

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
$env:LOONGSON_AOI_PHONE_CONFIRM_CONF = "0.3"
$env:LOONGSON_AOI_PHONE_MULTI_CONFIRM_CONF = "0.3"
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

## Real-Mode Fixed 16-Point Scan

Real mode does not require the user to know the PCB size. The website scans a
fixed 16-point window using the teammate-provided step spacing:

```text
X step: 30 mm
Y step: 18 mm
Camera frame: 1280 x 800
```

The first frame is captured before any XY movement. After all 16 images are
captured, the backend requests an origin return immediately, then drains the
per-cell detection queue and builds the mosaic. This keeps the slide from
waiting at the final scan point while the software finishes stitching and
summary work.

The real-mode UI exposes two motion parameters:

- `滑台速度 mm/s`: sent as `motion_speed_mm_s`.
- `停稳时间 s`: sent as `settle_seconds`.

The motor driver now uses `motion_speed_mm_s` as the requested pulse rate for
continuous XY moves instead of silently clamping every value above the nominal
axis speed to the same output. Extremely high values are still physically capped
by the configured pulse width, so practical tuning should compare values such as
`1`, `3`, and `6` mm/s before going higher.

The fixed-16 mosaic path has a validation test that cuts a real `val` PCB image
into 16 overlapping camera tiles and restores it through the same stitcher:

```powershell
python -m unittest test_mosaic.ScanMosaicTests.test_auto_crop_mosaic_restores_real_val_image_from_sixteen_overlapping_tiles
```

If a real-board mosaic is unreadable while this test passes, inspect the scan's
`mosaic/mosaic_manifest.json`, especially `geometry.estimated_step_px`,
`geometry.cell_image_transform`, and `geometry.axis_mapping`. The current camera
mount needs horizontal mirroring before stitching, so `LOONGSON_AOI_STITCH_MIRROR_X`
defaults to `1`. Set it to `0` only if a future camera installation no longer
needs that correction.

Common scan variables:

```powershell
$env:LOONGSON_AOI_DEBOUNCE_SECONDS = "0.05"
$env:LOONGSON_AOI_MOTOR_DRY_RUN = "0"
$env:LOONGSON_AOI_HOME_MODE = "soft"
$env:LOONGSON_AOI_CAMERA_SOURCE = "0"
$env:LOONGSON_AOI_STITCH_MIRROR_X = "1"
```

With no limit switches installed, place the slide at the physical origin and run
soft home before scanning. On the Loongson board, real GPIO usually requires
root or a correctly configured gpio group/udev rule.

## HTTP Interfaces

| Endpoint                                  | Purpose                                                |
| ----------------------------------------- | ------------------------------------------------------ |
| `GET /`                                 | Web console                                            |
| `GET /health`                           | Server health check                                    |
| `GET /api/system/status`                | Motor, camera, algorithm, and scan readiness           |
| `GET /api/camera/status`                | Camera open state and latest capture metadata          |
| `GET /api/camera/stream.mjpg`           | Raw camera preview stream                              |
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
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8020/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8020/api/system/status
```

## GitHub Update Notes

The project uses long-lived feature branches instead of pushing tested work to
`main` directly:

- `cloud-cooperative-platform`: complete website, mini program, ECS backend, and
  Loongson deployment scripts.
- `ui-integration`: synchronized website integration branch.
- `phone-browser-inference`: synchronized phone/browser inference branch.
- `hardware-sm`: motor controller and GPIO helper.
- `algorithm-ai`: training assets, configs, and exported ONNX weights.

`gh` is optional for normal pushes. If a teammate wants to create pull requests
or inspect GitHub checks from the command line, run `gh auth login` first.

## Branch Relationship

- `cloud-cooperative-platform`: this web console, mini program, and ECS integration runtime
- `ui-integration`: synchronized copy of the current web console for local integration tests
- `hardware-sm`: TB6600/XYZ slide-table driver
- `algorithm-ai`: YOLO training, evaluation, and standalone camera detection
- `main`: kept untouched unless the team decides to merge tested branches
