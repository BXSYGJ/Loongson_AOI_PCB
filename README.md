# Loongson AOI PCB - Hardware Slide Driver Branch

This branch contains the slide-table motor-control code for the Loongson AOI
PCB project. The target mechanism is an XYZ ball-screw positioning table driven
by TB6600 stepper drivers.

The current mechanical plan uses two Y lead screws driven by one TB6600 driver.
Software therefore exposes only one Y channel. Do not enable `Y2_*` variables
unless the machine is later changed to two independent Y-axis drivers.

## Main File

| File | Purpose |
| --- | --- |
| `motor_controller.py` | TB6600 step/direction control, soft home, optional limit-switch home, bounded XY/Z movement, fixed-point scan movement |
| `motor_gpio_system_helper.py` | Helper that lets the Python 3.12 web process call the system Python 3.7 libgpiod v1 runtime on the Loongson board |
| `motor_test.py` | Standalone slide movement plus camera capture smoke test |

## Public Python API

The UI branch imports this module through the following functions:

```python
home_all_axes()
move_to_xy(x_mm, y_mm)
move_to_xy_stream(x_mm, y_mm, speed_mm_s, progress_callback=None)
move_z_step(direction, dist_mm)
get_status()
reset_position(x=0.0, y=0.0, z=0.0)
```

All distances are in millimetres.

## Default Mechanical Parameters

| Item | Default |
| --- | --- |
| XYZ travel | `135 mm` |
| Lead screw | `8 mm / rev` |
| Stepper angle | `1.8 deg`, `200 steps / rev` |
| TB6600 microstep | `1` |
| Pulse density | `25 pulses / mm` |

Default GPIO line offsets:

| Axis | DIR | STEP |
| --- | ---: | ---: |
| X | `57` | `59` |
| Y | `3` | `60` |
| Z | `1` | `2` |

## No-Hardware Check

On a Windows or development PC, use dry-run mode:

```powershell
cd D:\AAAED\code\Loongson_AOI_PCB\worktrees\hardware-sm
python -m py_compile motor_controller.py
python .\motor_controller.py --dry-run status
python .\motor_controller.py --dry-run home
python .\motor_controller.py --dry-run move-xy 30 30
python .\motor_controller.py --dry-run z-jog up 1
```

On the Loongson board, install and enable the GPIO library required by the
runtime environment, then run without `--dry-run`.

The tested Loongson board currently has two Python GPIO environments:

- system `python3`: libgpiod v1 binding that exposes `Chip.OPEN_BY_NUMBER` and
  can drive the slide table when run as root;
- `~/aoi_venv_312/bin/python`: Python 3.12 environment used by the website,
  where the installed `gpiod` package is v2-style and may fail on this board.

For the website, use the system-python helper path:

```sh
sudo -E env \
  AOI_VENV=/home/loongson/aoi_venv_312 \
  LOONGSON_AOI_MOTOR_DRY_RUN=0 \
  LOONGSON_AOI_HOME_MODE=soft \
  LOONGSON_AOI_MOTOR_GPIO_BACKEND=system_python \
  LOONGSON_AOI_SYSTEM_GPIO_PYTHON=python3 \
  sh deploy/loongson/run_phone_server.sh
```

If the board is configured with a `gpio` group and udev rules, root may not be
required. In the current hardware test setup, root is the reliable path.

## Soft Home and Limit Switches

The current slide table has no proximity or limit switches, so the default home
mode is soft home:

1. Manually move X/Y/Z to the physical origin.
2. Call `home_all_axes()` or run `python .\motor_controller.py home`.
3. The current mechanical position is recorded as system zero.

After installing limit switches, switch to hardware homing:

```powershell
$env:LOONGSON_AOI_HOME_MODE = "limit"
$env:LOONGSON_AOI_X_LIMIT_OFFSET = "10"
$env:LOONGSON_AOI_Y_LIMIT_OFFSET = "11"
$env:LOONGSON_AOI_Z_LIMIT_OFFSET = "12"
```

Use real GPIO offsets for the installed switches.

## Important Environment Variables

General:

```powershell
$env:LOONGSON_AOI_GPIO_CHIP = "0"
$env:LOONGSON_AOI_MOTOR_DRY_RUN = "0"
$env:LOONGSON_AOI_HOME_MODE = "soft"
$env:LOONGSON_AOI_MOTOR_STATE = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\hardware-sm\motor_state.json"
```

Motion tuning:

```powershell
$env:LOONGSON_AOI_MICROSTEP = "1"
$env:LOONGSON_AOI_LEAD_MM = "8"
$env:LOONGSON_AOI_MAX_SPEED_MM_S = "3"
$env:LOONGSON_AOI_START_SPEED_MM_S = "0.8"
$env:LOONGSON_AOI_PULSE_WIDTH_US = "5000"
$env:LOONGSON_AOI_RAMP_FRACTION = "0.15"
```

Travel limits:

```powershell
$env:LOONGSON_AOI_X_MAX_MM = "135"
$env:LOONGSON_AOI_Y_MAX_MM = "135"
$env:LOONGSON_AOI_Z_MAX_MM = "135"
```

Per-axis GPIO override:

```powershell
$env:LOONGSON_AOI_X_DIR_OFFSET = "57"
$env:LOONGSON_AOI_X_STEP_OFFSET = "59"
$env:LOONGSON_AOI_Y_DIR_OFFSET = "3"
$env:LOONGSON_AOI_Y_STEP_OFFSET = "60"
$env:LOONGSON_AOI_Z_DIR_OFFSET = "1"
$env:LOONGSON_AOI_Z_STEP_OFFSET = "2"
```

## Motor and Camera Smoke Test

`motor_test.py` moves through the teammate-provided XY points, waits for the
stage to stabilize, and saves one camera image per point.

```powershell
python .\motor_test.py --camera-source 1 --camera-backend DSHOW --soft-home
```

On the Loongson board, use the V4L2 source:

```powershell
python .\motor_test.py --camera-source /dev/video0 --soft-home
```

The point list can be overridden without editing code:

```powershell
$env:LOONGSON_AOI_MOTOR_TEST_POINTS = "0,0;16,8;16,40;32,8;32,40;0,0"
```

Optional future dual-Y independent driver:

```powershell
$env:LOONGSON_AOI_Y2_DIR_OFFSET = "<gpio-line>"
$env:LOONGSON_AOI_Y2_STEP_OFFSET = "<gpio-line>"
$env:LOONGSON_AOI_Y2_DIR_POSITIVE = "1"
```

## Real Fixed-Point Scan Support

`move_to_xy_stream(...)` is used by the web console real mode. Despite the name,
the current production path is not continuous video scanning. It moves between
fixed XY capture points at the requested speed, then the camera captures a
still image.

The real-mode scan uses 16 capture points. The first image is captured before
any XY movement. After all 16 images are captured, the website starts mosaic
generation before issuing the return-to-origin movement.

The function is bounded by the configured software travel limits. Without limit
switches or encoders, returning to origin depends on the stepper motors not
losing steps.

## UI Integration Notes

The `cloud-cooperative-platform` and synchronized `ui-integration` branches use
this driver in real mode:

- capture the origin frame first;
- move to the next fixed XY point;
- wait for the configured settle time;
- capture a frame;
- enqueue detection and continue the scan;
- build the 16-image mosaic after all captures;
- return to origin after the mosaic result is emitted.

Simulation and phone-photo modes do not move the slide table and do not require
soft home.

When testing hardware from the UI branch, keep these assumptions aligned:

```powershell
$env:LOONGSON_AOI_HOME_MODE = "soft"
$env:LOONGSON_AOI_X_MAX_MM = "135"
$env:LOONGSON_AOI_Y_MAX_MM = "135"
$env:LOONGSON_AOI_Z_MAX_MM = "135"
```

If limit switches are added later, configure the three limit offsets in this
branch first, then expose the same environment variables to the UI server.

## Branch Relationship

- `hardware-sm`: this TB6600/XYZ slide-table driver
- `cloud-cooperative-platform`: FastAPI browser UI, cloud/phone/local platform routing, real-mode scan orchestration
- `ui-integration`: synchronized browser UI branch
- `algorithm-ai`: YOLO training and detection scripts
- `main`: kept untouched unless tested branches are merged later
