# Loongson AOI PCB - UI Integration Branch

This branch contains the web console that connects the PCB AOI workflow:

- OV9281 camera capture and MJPEG preview
- YOLO PCB defect inference
- XYZ slide-table scan orchestration
- WebSocket event updates for the browser UI
- Handheld, simulated, real stepped-scan, continuous video scan, and phone-upload workflows

The current UI is designed as a white industrial control panel. It is the
branch to run when you want to verify the camera, open the web page, test the
mock/simulation flow, or connect the UI to the hardware and algorithm branches.

## Main Files

| File | Purpose |
| --- | --- |
| `main.py` | FastAPI app, WebSocket control protocol, scan orchestration |
| `camera_trigger.py` | Camera manager, YOLO detector, still capture, video detection session |
| `motor_controller.py` | UI-side motor interface used by scan commands |
| `static/index.html` | Browser shell |
| `static/app.js` | Frontend state machine, WebSocket client, mode logic |
| `static/styles.css` | White industrial UI styling |
| `requirements.txt` | Python runtime dependencies |

## Modes

| Mode | Hardware Needed | Behavior |
| --- | --- | --- |
| Handheld detection | Camera + YOLO | No slide movement. The camera can be moved by hand and the video stream shows live YOLO boxes. |
| Simulation mode | Existing PCB image | Upload one or more PCB images and simulate a snake-grid slide scan over the image. |
| Real mode | Camera + slide table + YOLO | Step the slide to each grid cell, settle, capture one image, run YOLO, then move to the next cell. |
| Video detection | Camera + slide table + YOLO | Continuous snake scan with live YOLO video and a moving FOV box on the PCB board. |
| Phone photo | Phone browser + YOLO | Upload phone photos from the same LAN, convert color images to grayscale, then run whole-board tiled YOLO detection. |

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

Option 1: write the source into `camera_source.txt`:

```powershell
Set-Content -Path .\camera_source.txt -Value "1"
```

Option 2: set an environment variable before starting the server:

```powershell
$env:LOONGSON_AOI_CAMERA_SOURCE = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Useful camera variables:

```powershell
$env:LOONGSON_AOI_CAMERA_SOURCE = "1"
$env:LOONGSON_AOI_CAMERA_WIDTH = "1280"
$env:LOONGSON_AOI_CAMERA_HEIGHT = "800"
$env:LOONGSON_AOI_CAMERA_BACKEND = "DSHOW"
```

On Windows, `DSHOW` is often more stable than the default MSMF backend for USB
or industrial cameras. On the Loongson Linux board, use sources such as
`/dev/video0`.

## YOLO Weights

By default the UI looks for weights under the algorithm branch layout:

```text
worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.pt
```

You can override it:

```powershell
$env:LOONGSON_AOI_WEIGHTS = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai\runs\detect\pcb_defects\weights\best.pt"
$env:LOONGSON_AOI_ALGORITHM_ROOT = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai"
$env:LOONGSON_AOI_YOLO_CONF = "0.5"
$env:LOONGSON_AOI_YOLO_IMGSZ = "960"
```

## Phone Photo Detection

The phone module is intended for quick LAN capture when the slide table or
OV9281 is not being used. The phone can either open the complete UI or the
minimal `/phone` page. The phone page has separate buttons for camera capture
and gallery selection.

Phone uploads are normalized before inference:

- browser-side conversion from color to grayscale JPEG
- server-side grayscale conversion as a second safety check
- original board dimensions are preserved for UI display and box coordinates
- YOLO inference uses in-memory tiled detection rather than resizing the board

Default tiled detection settings:

```powershell
$env:LOONGSON_AOI_PHONE_TILE_SIZES = "640,800"
$env:LOONGSON_AOI_PHONE_TILE_OVERLAP = "0.5"
```

The `640` tile keeps compatibility with the training image size. The `800`
tile improves phone whole-board photos where defects are slightly larger or
where the phone crop differs from the dataset. To force the old single-scale
behavior:

```powershell
$env:LOONGSON_AOI_PHONE_TILE_SIZE = "640"
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

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Web console |
| `GET /health` | Server health check |
| `GET /api/system/status` | Motor, camera, algorithm, and scan readiness |
| `GET /api/camera/status` | Camera open state and latest capture metadata |
| `GET /api/camera/stream.mjpg` | Raw camera preview stream |
| `GET /api/video/status` | Live YOLO video session status |
| `GET /api/video/stream.mjpg` | Annotated YOLO video stream |
| `POST /api/simulation/upload` | Upload PCB images for simulation mode |
| `GET /phone` | Minimal phone upload page |
| `POST /api/phone/upload` | Upload phone camera/gallery images |
| `GET /api/phone/uploads` | List phone upload queue |
| `POST /api/phone/detect` | Run tiled whole-board YOLO detection on a phone upload |
| `DELETE /api/phone/uploads/{upload_id}` | Delete a phone upload record and files |
| `WS /ws/control` | Start/stop scan commands and live events |

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
