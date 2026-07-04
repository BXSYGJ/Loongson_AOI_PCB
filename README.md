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
| `motor_controller.py` | TB6600 step/direction control, soft home, optional limit-switch home, bounded XY/Z movement, continuous scan movement |

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
| TB6600 microstep | `16` |
| Pulse density | `400 pulses / mm` |

Default GPIO line offsets:

| Axis | DIR | STEP |
| --- | ---: | ---: |
| X | `4` | `7` |
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
$env:LOONGSON_AOI_MICROSTEP = "16"
$env:LOONGSON_AOI_LEAD_MM = "8"
$env:LOONGSON_AOI_MAX_SPEED_MM_S = "3"
$env:LOONGSON_AOI_START_SPEED_MM_S = "0.8"
$env:LOONGSON_AOI_PULSE_WIDTH_US = "500"
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
$env:LOONGSON_AOI_X_DIR_OFFSET = "4"
$env:LOONGSON_AOI_X_STEP_OFFSET = "7"
$env:LOONGSON_AOI_Y_DIR_OFFSET = "3"
$env:LOONGSON_AOI_Y_STEP_OFFSET = "60"
$env:LOONGSON_AOI_Z_DIR_OFFSET = "1"
$env:LOONGSON_AOI_Z_STEP_OFFSET = "2"
```

Optional future dual-Y independent driver:

```powershell
$env:LOONGSON_AOI_Y2_DIR_OFFSET = "<gpio-line>"
$env:LOONGSON_AOI_Y2_STEP_OFFSET = "<gpio-line>"
$env:LOONGSON_AOI_Y2_DIR_POSITIVE = "1"
```

## Continuous Video Scan Support

`move_to_xy_stream(...)` is used by the UI branch video-detection mode. It
moves across long X/Y segments at a requested speed and reports progress so the
browser can keep the FOV box synchronized with the open-loop slide position.

The function is still bounded by the configured software travel limits. Without
limit switches or encoders, returning to origin depends on the stepper motors
not losing steps.

## Branch Relationship

- `hardware-sm`: this TB6600/XYZ slide-table driver
- `ui-integration`: FastAPI browser UI that imports the motor API
- `algorithm-ai`: YOLO training and detection scripts
- `main`: kept untouched unless tested branches are merged later
