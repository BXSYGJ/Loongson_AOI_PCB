"""TB6600 stepper driver for the Loongson AOI XYZ slide table.

The public functions intentionally keep the contract used by
``worktrees/ui-integration/main.py``:

    home_all_axes()
    move_z_step(direction, dist)
    move_to_xy(x, y)
    move_to_xy_stream(x, y, speed_mm_s, progress_callback)

Distances are millimetres.  The default mechanics and GPIO offsets now match
the teammate-provided Loongson board wiring:

    X_DIR=57, X_PULSE=59
    Y_DIR=3,  Y_PULSE=60
    Z_DIR=1,  Z_PULSE=2

The defaults assume a 135 mm travel, an 8 mm lead screw, and a TB6600 set to
full-step mode, which gives 200 pulses per 8 mm.  If the TB6600 DIP switches
are changed to microstepping, set ``LOONGSON_AOI_MICROSTEP`` or the per-axis
``LOONGSON_AOI_X_MICROSTEP``/``Y``/``Z`` environment variables accordingly.
GPIO line offsets can also be changed through environment variables without
editing this file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class MotorError(RuntimeError):
    """Base error for motion-control failures."""


class TravelLimitError(MotorError):
    """Raised when a requested move exceeds the configured soft limits."""


class LimitSwitchError(MotorError):
    """Raised when a hard-home move cannot find its limit switch."""


ROOT = Path(__file__).resolve().parent
STATE_PATH = Path(os.getenv("LOONGSON_AOI_MOTOR_STATE", ROOT / "motor_state.json"))
CONSUMER = "loongson-aoi-motor"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value, 0)


def _env_optional_int(*names: str) -> int | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return int(value, 0)
    return None


def _env_axis_int(axis: str, field: str, default: int) -> int:
    return _env_int(f"LOONGSON_AOI_{axis}_{field}_OFFSET", _env_int(f"LOONGSON_AOI_{axis}_{field}", default))


def _env_axis_bool(axis: str, field: str, default: bool) -> bool:
    return _env_bool(f"LOONGSON_AOI_{axis}_{field}", default)


def _log(message: str) -> None:
    if _env_bool("LOONGSON_AOI_MOTOR_VERBOSE", True):
        print(f"[motor] {message}")


def _gpio_chip_path() -> str:
    return f"/dev/gpiochip{GPIO_CHIP}" if str(GPIO_CHIP).isdigit() else str(GPIO_CHIP)


def _gpio_offsets_text(output_offsets: set[int], input_offsets: set[int]) -> str:
    outputs = ",".join(str(offset) for offset in sorted(output_offsets)) or "-"
    inputs = ",".join(str(offset) for offset in sorted(input_offsets)) or "-"
    return f"outputs={outputs}; inputs={inputs}"


def _gpio_debug_command() -> str:
    chip = _gpio_chip_path()
    return (
        "python -c \"import gpiod; "
        f"c=gpiod.Chip('{chip}'); "
        "info=c.get_info(); print(info); print('num_lines=', getattr(info, 'num_lines', None))\""
    )


def _gpio_permission_message(exc: BaseException) -> str:
    chip = _gpio_chip_path()
    return (
        f"GPIO permission denied while opening {chip}: {exc}. "
        "Run the real-motion web server with sudo, or grant the current user read/write permission "
        "to the gpiochip device. Keep LOONGSON_AOI_MOTOR_DRY_RUN=1 when testing without GPIO access."
    )


def _gpio_request_message(exc: BaseException, api: str, output_offsets: set[int], input_offsets: set[int]) -> str:
    chip = _gpio_chip_path()
    return (
        f"GPIO request failed on {chip} api={api}: {exc}. "
        f"Requested offsets: {_gpio_offsets_text(output_offsets, input_offsets)}. "
        "If this is (22, 'Invalid argument'), check whether the configured offsets exceed gpiochip num_lines "
        f"or force the legacy path with LOONGSON_AOI_GPIOD_API=v1. Diagnostic: {_gpio_debug_command()}"
    )


def _gpio_chip_number() -> int | None:
    try:
        return int(str(GPIO_CHIP), 0)
    except ValueError:
        return None


@dataclass(frozen=True)
class StepChannel:
    """One TB6600 DIR/PUL channel.

    ``dir_positive`` is the GPIO value used when the logical axis moves in
    the positive direction.  Set it to 0 for a motor mounted in reverse.
    """

    dir_offset: int
    step_offset: int
    dir_positive: int = 1


@dataclass(frozen=True)
class AxisConfig:
    name: str
    max_mm: float
    lead_mm_per_rev: float
    motor_steps_per_rev: int
    microstep: int
    channels: tuple[StepChannel, ...]
    max_speed_mm_s: float
    start_speed_mm_s: float
    pulse_width_s: float
    ramp_fraction: float
    settle_s: float
    home_direction: int
    home_speed_mm_s: float
    home_backoff_mm: float
    home_extra_mm: float
    limit_offset: int | None
    limit_active_high: bool

    @property
    def pulses_per_mm(self) -> float:
        return (self.motor_steps_per_rev * self.microstep) / self.lead_mm_per_rev

    @property
    def max_steps(self) -> int:
        return int(round(self.max_mm * self.pulses_per_mm))


def _axis_config(axis: str, default_dir: int, default_step: int) -> AxisConfig:
    common_lead = _env_float("LOONGSON_AOI_LEAD_MM", 8.0)
    common_steps = _env_int("LOONGSON_AOI_STEPS_PER_REV", 200)
    common_microstep = _env_int("LOONGSON_AOI_MICROSTEP", 1)

    channels: list[StepChannel] = [
        StepChannel(
            dir_offset=_env_axis_int(axis, "DIR", default_dir),
            step_offset=_env_axis_int(axis, "STEP", default_step),
            dir_positive=1 if _env_axis_bool(axis, "DIR_POSITIVE", True) else 0,
        )
    ]

    # The current hardware plan drives both Y lead-screw motors from one
    # TB6600, so the normal Y channel is enough.  Defining Y2_* is only for a
    # future change where each Y motor has its own synchronised driver.
    if axis == "Y":
        y2_dir = _env_optional_int("LOONGSON_AOI_Y2_DIR_OFFSET", "LOONGSON_AOI_Y2_DIR")
        y2_step = _env_optional_int("LOONGSON_AOI_Y2_STEP_OFFSET", "LOONGSON_AOI_Y2_STEP")
        if y2_dir is not None and y2_step is not None:
            channels.append(
                StepChannel(
                    dir_offset=y2_dir,
                    step_offset=y2_step,
                    dir_positive=1 if _env_bool("LOONGSON_AOI_Y2_DIR_POSITIVE", True) else 0,
                )
            )

    return AxisConfig(
        name=axis.lower(),
        max_mm=_env_float(f"LOONGSON_AOI_{axis}_MAX_MM", 135.0),
        lead_mm_per_rev=_env_float(f"LOONGSON_AOI_{axis}_LEAD_MM", common_lead),
        motor_steps_per_rev=_env_int(f"LOONGSON_AOI_{axis}_STEPS_PER_REV", common_steps),
        microstep=_env_int(f"LOONGSON_AOI_{axis}_MICROSTEP", common_microstep),
        channels=tuple(channels),
        max_speed_mm_s=_env_float(f"LOONGSON_AOI_{axis}_MAX_SPEED_MM_S", _env_float("LOONGSON_AOI_MAX_SPEED_MM_S", 3.0)),
        start_speed_mm_s=_env_float(f"LOONGSON_AOI_{axis}_START_SPEED_MM_S", _env_float("LOONGSON_AOI_START_SPEED_MM_S", 0.8)),
        pulse_width_s=_env_float(f"LOONGSON_AOI_{axis}_PULSE_WIDTH_US", _env_float("LOONGSON_AOI_PULSE_WIDTH_US", 5000.0)) / 1_000_000.0,
        ramp_fraction=_env_float(f"LOONGSON_AOI_{axis}_RAMP_FRACTION", _env_float("LOONGSON_AOI_RAMP_FRACTION", 0.15)),
        settle_s=_env_float(f"LOONGSON_AOI_{axis}_SETTLE_S", _env_float("LOONGSON_AOI_SETTLE_S", 0.02)),
        home_direction=-1 if _env_bool(f"LOONGSON_AOI_{axis}_HOME_NEGATIVE", True) else 1,
        home_speed_mm_s=_env_float(f"LOONGSON_AOI_{axis}_HOME_SPEED_MM_S", _env_float("LOONGSON_AOI_HOME_SPEED_MM_S", 1.0)),
        home_backoff_mm=_env_float(f"LOONGSON_AOI_{axis}_HOME_BACKOFF_MM", _env_float("LOONGSON_AOI_HOME_BACKOFF_MM", 2.0)),
        home_extra_mm=_env_float(f"LOONGSON_AOI_{axis}_HOME_EXTRA_MM", _env_float("LOONGSON_AOI_HOME_EXTRA_MM", 5.0)),
        limit_offset=_env_optional_int(f"LOONGSON_AOI_{axis}_LIMIT_OFFSET", f"LOONGSON_AOI_{axis}_HOME_LIMIT_OFFSET"),
        limit_active_high=_env_bool(f"LOONGSON_AOI_{axis}_LIMIT_ACTIVE_HIGH", False),
    )


GPIO_CHIP = os.getenv("LOONGSON_AOI_GPIO_CHIP", "0")
DRY_RUN = _env_bool("LOONGSON_AOI_MOTOR_DRY_RUN", False)
HOME_MODE = os.getenv("LOONGSON_AOI_HOME_MODE", "soft").strip().lower()
Z_UP_POSITIVE = _env_bool("LOONGSON_AOI_Z_UP_POSITIVE", True)
GPIOD_API = os.getenv("LOONGSON_AOI_GPIOD_API", "v1").strip().lower()
MOTOR_GPIO_BACKEND = os.getenv("LOONGSON_AOI_MOTOR_GPIO_BACKEND", "system_python").strip().lower()
SYSTEM_GPIO_PYTHON = os.getenv("LOONGSON_AOI_SYSTEM_GPIO_PYTHON", "/usr/bin/python3").strip() or "/usr/bin/python3"
SYSTEM_GPIO_HELPER = Path(os.getenv("LOONGSON_AOI_SYSTEM_GPIO_HELPER", ROOT / "motor_gpio_system_helper.py")).expanduser()

AXES: dict[str, AxisConfig] = {
    "x": _axis_config("X", default_dir=57, default_step=59),
    "y": _axis_config("Y", default_dir=3, default_step=60),
    "z": _axis_config("Z", default_dir=1, default_step=2),
}

_motion_lock = threading.RLock()
_stop_requested = threading.Event()

MotionProgressCallback = Callable[[dict[str, Any]], None]


class MotionState:
    """Open-loop position state in motor steps."""

    def __init__(self, axes: dict[str, AxisConfig], path: Path) -> None:
        self.axes = axes
        self.path = path
        self.steps = {axis: 0 for axis in axes}
        self.homed = {axis: False for axis in axes}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for axis in self.axes:
            axis_data = data.get("axes", {}).get(axis, {})
            saved_steps = int(axis_data.get("steps", self.steps[axis]))
            saved_ppm = axis_data.get("pulses_per_mm")
            saved_position_mm = axis_data.get("position_mm")
            current_ppm = self.axes[axis].pulses_per_mm
            if saved_ppm is not None and saved_position_mm is not None:
                try:
                    if abs(float(saved_ppm) - current_ppm) > 1e-6:
                        position_mm = float(saved_position_mm)
                        if math.isfinite(position_mm) and 0.0 <= position_mm <= self.axes[axis].max_mm:
                            saved_steps = int(round(position_mm * current_ppm))
                except (TypeError, ValueError):
                    pass
            self.steps[axis] = saved_steps
            self.homed[axis] = bool(axis_data.get("homed", self.homed[axis]))

    def save(self) -> None:
        data = {
            "units": "steps",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "axes": {
                axis: {
                    "steps": int(self.steps[axis]),
                    "position_mm": self.mm(axis),
                    "homed": bool(self.homed[axis]),
                    "pulses_per_mm": self.axes[axis].pulses_per_mm,
                }
                for axis in self.axes
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def mm(self, axis: str) -> float:
        return self.steps[axis] / self.axes[axis].pulses_per_mm

    def target_steps(self, axis: str, mm_value: float) -> int:
        cfg = self.axes[axis]
        if not math.isfinite(mm_value):
            raise TravelLimitError(f"{axis.upper()} target must be finite.")
        if mm_value < 0 or mm_value > cfg.max_mm:
            raise TravelLimitError(f"{axis.upper()} target {mm_value:.3f} mm is outside 0..{cfg.max_mm:.3f} mm.")
        return int(round(mm_value * cfg.pulses_per_mm))

    def set_zero(self, axis: str) -> None:
        self.steps[axis] = 0
        self.homed[axis] = True


_state = MotionState(AXES, STATE_PATH)


class GpioSession:
    """Small adapter for libgpiod v1/v2 plus a dry-run mode."""

    def __init__(self, axes: list[AxisConfig], include_limits: bool = False) -> None:
        self.axes = axes
        self.include_limits = include_limits
        self.dry_run = DRY_RUN
        self._api = ""
        self._gpiod: Any = None
        self._chip: Any = None
        self._request: Any = None
        self._lines: dict[int, Any] = {}
        self._output_offsets: set[int] = set()
        self._input_offsets: set[int] = set()

        for cfg in axes:
            for channel in cfg.channels:
                self._output_offsets.add(channel.dir_offset)
                self._output_offsets.add(channel.step_offset)
            if include_limits and cfg.limit_offset is not None:
                self._input_offsets.add(cfg.limit_offset)

    def __enter__(self) -> "GpioSession":
        if self.dry_run:
            _log("dry-run GPIO session opened")
            return self

        try:
            import gpiod  # type: ignore[import-not-found]
        except ImportError as exc:
            raise MotorError("python gpiod is not installed; set LOONGSON_AOI_MOTOR_DRY_RUN=1 for simulation.") from exc

        self._gpiod = gpiod
        self._open_requested_api()

        for offset in self._output_offsets:
            self.set_value(offset, 0)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self.dry_run:
            for cfg in self.axes:
                for channel in cfg.channels:
                    self.set_value(channel.step_offset, 0)
            if self._api == "v2" and self._request is not None:
                self._request.release()
            elif self._api == "v1":
                for line in self._lines.values():
                    line.release()
                if self._chip is not None:
                    self._chip.close()
        self._request = None
        self._chip = None
        self._lines.clear()

    def _open_requested_api(self) -> None:
        requested = GPIOD_API if GPIOD_API in {"auto", "v1", "v2"} else "auto"
        errors: list[tuple[str, BaseException]] = []
        order = ["v1", "v2"] if requested == "auto" else [requested]
        for api in order:
            try:
                if api == "v1":
                    self._open_v1()
                else:
                    self._open_v2()
                return
            except PermissionError as exc:
                raise MotorError(_gpio_permission_message(exc)) from exc
            except OSError as exc:
                if getattr(exc, "errno", None) == 13:
                    raise MotorError(_gpio_permission_message(exc)) from exc
                errors.append((api, exc))
            except (AttributeError, TypeError, ValueError) as exc:
                errors.append((api, exc))

        if errors:
            api, exc = errors[-1]
            detail = "; ".join(f"{item_api}: {item_exc}" for item_api, item_exc in errors)
            raise MotorError(_gpio_request_message(RuntimeError(detail), api, self._output_offsets, self._input_offsets)) from exc
        raise MotorError(_gpio_request_message(RuntimeError("no GPIO API attempted"), requested, self._output_offsets, self._input_offsets))

    def _open_v1(self) -> None:
        gpiod = self._gpiod
        assert gpiod is not None
        chip_attempts: list[tuple[Any, ...]] = []
        chip_number = _gpio_chip_number()
        if hasattr(gpiod.Chip, "OPEN_BY_NUMBER") and chip_number is not None:
            chip_attempts.append((str(chip_number), gpiod.Chip.OPEN_BY_NUMBER))
            chip_attempts.append((chip_number, gpiod.Chip.OPEN_BY_NUMBER))
        chip_attempts.append((_gpio_chip_path(),))
        chip_attempts.append((str(GPIO_CHIP),))

        errors: list[BaseException] = []
        for args in chip_attempts:
            try:
                self._chip = gpiod.Chip(*args)
                break
            except (OSError, TypeError, ValueError) as exc:
                errors.append(exc)
        if self._chip is None:
            detail = "; ".join(str(error) for error in errors) or "no chip open attempts"
            raise OSError(detail)
        self._api = "v1"

        for offset in sorted(self._output_offsets):
            line = self._chip.get_line(offset)
            try:
                line.request(consumer=CONSUMER, type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
            except TypeError:
                line.request(consumer=CONSUMER, type=gpiod.LINE_REQ_DIR_OUT, default_val=0)
            self._lines[offset] = line

        for offset in sorted(self._input_offsets):
            line = self._chip.get_line(offset)
            line.request(consumer=CONSUMER, type=gpiod.LINE_REQ_DIR_IN)
            self._lines[offset] = line

    def _open_v2(self) -> None:
        gpiod = self._gpiod
        assert gpiod is not None
        from gpiod.line import Direction, Value  # type: ignore[import-not-found]

        config: dict[int, Any] = {}
        for offset in sorted(self._output_offsets):
            config[offset] = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)
        for offset in sorted(self._input_offsets):
            config[offset] = gpiod.LineSettings(direction=Direction.INPUT)

        chip = _gpio_chip_path()
        self._request = gpiod.request_lines(chip, consumer=CONSUMER, config=config)
        self._api = "v2"

    def set_value(self, offset: int, value: int) -> None:
        if self.dry_run:
            return
        if self._api == "v2":
            from gpiod.line import Value  # type: ignore[import-not-found]

            self._request.set_value(offset, Value.ACTIVE if value else Value.INACTIVE)
        else:
            self._lines[offset].set_value(1 if value else 0)

    def get_value(self, offset: int) -> bool:
        if self.dry_run:
            return False
        if self._api == "v2":
            value = self._request.get_value(offset)
            return bool(getattr(value, "value", value))
        return bool(self._lines[offset].get_value())

    def set_axis_direction(self, cfg: AxisConfig, direction: int) -> None:
        logical_positive = direction >= 0
        for channel in cfg.channels:
            value = channel.dir_positive if logical_positive else 1 - channel.dir_positive
            self.set_value(channel.dir_offset, value)
        time.sleep(0.001)

    def pulse_axis(self, cfg: AxisConfig) -> None:
        self.pulse_axes([cfg])

    def pulse_axes(self, configs: list[AxisConfig]) -> float:
        pulse_width_s = max(cfg.pulse_width_s for cfg in configs)
        for cfg in configs:
            for channel in cfg.channels:
                self.set_value(channel.step_offset, 1)
        time.sleep(pulse_width_s)
        for cfg in configs:
            for channel in cfg.channels:
                self.set_value(channel.step_offset, 0)
        return pulse_width_s


def _check_stop() -> None:
    if _stop_requested.is_set():
        raise MotorError("Motion stopped by request.")


def request_stop() -> None:
    """Ask any in-progress motion loop to stop at the next pulse boundary."""

    _stop_requested.set()


def clear_stop() -> None:
    _stop_requested.clear()


def emergency_stop() -> None:
    request_stop()


def _dominant_axis(deltas: dict[str, int]) -> str:
    return max(deltas, key=lambda axis: abs(deltas[axis]))


def _interval_for_tick(cfg: AxisConfig, tick: int, total: int, speed_scale: float = 1.0) -> float:
    max_rate = max(cfg.max_speed_mm_s * cfg.pulses_per_mm * speed_scale, 1.0)
    start_rate = min(max(cfg.start_speed_mm_s * cfg.pulses_per_mm, 1.0), max_rate)
    ramp_ticks = int(total * max(0.0, min(cfg.ramp_fraction, 0.45)))

    if ramp_ticks <= 1:
        rate = max_rate
    elif tick <= ramp_ticks:
        alpha = tick / ramp_ticks
        rate = start_rate + (max_rate - start_rate) * alpha
    elif tick > total - ramp_ticks:
        alpha = max((total - tick) / ramp_ticks, 0.0)
        rate = start_rate + (max_rate - start_rate) * alpha
    else:
        rate = max_rate
    return max(1.0 / rate, cfg.pulse_width_s)


def _validate_target_steps(axis: str, steps: int) -> None:
    cfg = AXES[axis]
    if steps < 0 or steps > cfg.max_steps:
        target_mm = steps / cfg.pulses_per_mm
        raise TravelLimitError(f"{axis.upper()} target {target_mm:.3f} mm is outside 0..{cfg.max_mm:.3f} mm.")


def _use_system_python_gpio() -> bool:
    return not DRY_RUN and MOTOR_GPIO_BACKEND in {"system_python", "system", "external"}


def _axis_motion_payload(axis: str, cfg: AxisConfig, sign: int, count: int) -> dict[str, Any]:
    return {
        "axis": axis,
        "direction": sign,
        "count": count,
        "channels": [
            {
                "dir_offset": channel.dir_offset,
                "step_offset": channel.step_offset,
                "dir_value": channel.dir_positive if sign >= 0 else 1 - channel.dir_positive,
            }
            for channel in cfg.channels
        ],
        "pulse_width_s": cfg.pulse_width_s,
        "settle_s": cfg.settle_s,
    }


def _run_system_python_motion(
    configs: list[AxisConfig],
    signs: dict[str, int],
    counts: dict[str, int],
    dominant_cfg: AxisConfig,
    total_ticks: int,
    speed_scale: float,
) -> None:
    if not SYSTEM_GPIO_HELPER.exists():
        raise MotorError(f"System GPIO helper not found: {SYSTEM_GPIO_HELPER}")

    payload = {
        "gpio_chip": GPIO_CHIP,
        "consumer": CONSUMER,
        "total_ticks": total_ticks,
        "speed_scale": speed_scale,
        "dominant": {
            "max_speed_mm_s": dominant_cfg.max_speed_mm_s,
            "start_speed_mm_s": dominant_cfg.start_speed_mm_s,
            "pulses_per_mm": dominant_cfg.pulses_per_mm,
            "pulse_width_s": dominant_cfg.pulse_width_s,
            "ramp_fraction": dominant_cfg.ramp_fraction,
        },
        "axes": [
            _axis_motion_payload(cfg.name, cfg, signs[cfg.name], counts[cfg.name])
            for cfg in configs
        ],
    }
    try:
        completed = subprocess.run(
            shlex.split(SYSTEM_GPIO_PYTHON) + [str(SYSTEM_GPIO_HELPER)],
            input=json.dumps(payload, ensure_ascii=True),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise MotorError(f"Failed to start system GPIO helper with {SYSTEM_GPIO_PYTHON}: {exc}") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise MotorError(f"System GPIO helper failed: {message or 'unknown error'}")


def _move_axes_to(
    targets: dict[str, int],
    speed_scale: float = 1.0,
    progress_callback: MotionProgressCallback | None = None,
    progress_interval_s: float = 0.2,
    segment: dict[str, Any] | None = None,
) -> dict[str, float]:
    clear_stop()
    for axis, steps in targets.items():
        _validate_target_steps(axis, steps)

    deltas = {axis: targets[axis] - _state.steps[axis] for axis in targets}
    deltas = {axis: delta for axis, delta in deltas.items() if delta != 0}
    if not deltas:
        return get_position()

    configs = [AXES[axis] for axis in deltas]
    dominant = _dominant_axis(deltas)
    dominant_cfg = AXES[dominant]
    total_ticks = abs(deltas[dominant])
    progressed = {axis: 0 for axis in deltas}
    signs = {axis: 1 if delta > 0 else -1 for axis, delta in deltas.items()}
    counts = {axis: abs(delta) for axis, delta in deltas.items()}
    next_progress_at = time.perf_counter()
    progress_interval_s = max(0.02, float(progress_interval_s))

    _log(
        "move "
        + ", ".join(f"{axis.upper()} {deltas[axis]} pulses ({deltas[axis] / AXES[axis].pulses_per_mm:.3f} mm)" for axis in deltas)
    )

    if _use_system_python_gpio():
        try:
            _run_system_python_motion(configs, signs, counts, dominant_cfg, total_ticks, speed_scale)
            for axis, target in targets.items():
                _state.steps[axis] = target
            if progress_callback is not None:
                payload = {
                    "position": get_position(),
                    "tick": total_ticks,
                    "total_ticks": total_ticks,
                    "progress": 1.0,
                }
                if segment is not None:
                    payload["segment"] = dict(segment)
                progress_callback(payload)
            time.sleep(max(cfg.settle_s for cfg in configs))
        finally:
            _state.save()
        return get_position()

    try:
        with GpioSession(configs) as gpio:
            for axis, sign in signs.items():
                gpio.set_axis_direction(AXES[axis], sign)

            for tick in range(1, total_ticks + 1):
                _check_stop()
                due_axes: list[str] = []
                for axis, count in counts.items():
                    expected = (tick * count) // total_ticks
                    if expected > progressed[axis]:
                        due_axes.append(axis)
                        progressed[axis] = expected

                pulse_width_s = gpio.pulse_axes([AXES[axis] for axis in due_axes])
                for axis in due_axes:
                    _state.steps[axis] += signs[axis]

                now = time.perf_counter()
                if progress_callback is not None and (now >= next_progress_at or tick == total_ticks):
                    payload: dict[str, Any] = {
                        "position": get_position(),
                        "tick": tick,
                        "total_ticks": total_ticks,
                        "progress": tick / total_ticks,
                    }
                    if segment is not None:
                        payload["segment"] = dict(segment)
                    progress_callback(payload)
                    next_progress_at = now + progress_interval_s

                interval_s = _interval_for_tick(dominant_cfg, tick, total_ticks, speed_scale)
                sleep_s = max(0.0, interval_s - pulse_width_s)
                if sleep_s:
                    time.sleep(sleep_s)

            time.sleep(max(cfg.settle_s for cfg in configs))
    finally:
        _state.save()

    return get_position()


def _limit_active(cfg: AxisConfig, gpio: GpioSession) -> bool:
    if cfg.limit_offset is None:
        return False
    if gpio.dry_run:
        return False
    raw = gpio.get_value(cfg.limit_offset)
    return raw if cfg.limit_active_high else not raw


def _pulse_home_step(axis: str, direction: int, gpio: GpioSession, interval_s: float) -> None:
    cfg = AXES[axis]
    gpio.pulse_axis(cfg)
    _state.steps[axis] += direction
    time.sleep(max(0.0, interval_s - cfg.pulse_width_s))


def _hard_home_axis(axis: str, gpio: GpioSession) -> None:
    cfg = AXES[axis]
    if cfg.limit_offset is None:
        raise LimitSwitchError(f"{axis.upper()} has no configured home limit switch.")

    home_dir = 1 if cfg.home_direction >= 0 else -1
    away_dir = -home_dir
    max_search_steps = int(round((cfg.max_mm + cfg.home_extra_mm) * cfg.pulses_per_mm))
    backoff_steps = max(1, int(round(cfg.home_backoff_mm * cfg.pulses_per_mm)))
    interval_s = max(1.0 / max(cfg.home_speed_mm_s * cfg.pulses_per_mm, 1.0), cfg.pulse_width_s)

    _log(f"hard-home {axis.upper()} on GPIO line {cfg.limit_offset}")

    gpio.set_axis_direction(cfg, away_dir)
    for _ in range(backoff_steps):
        if not _limit_active(cfg, gpio):
            break
        _check_stop()
        _pulse_home_step(axis, away_dir, gpio, interval_s)

    gpio.set_axis_direction(cfg, home_dir)
    for searched in range(max_search_steps):
        _check_stop()
        if _limit_active(cfg, gpio):
            break
        _pulse_home_step(axis, home_dir, gpio, interval_s)
    else:
        raise LimitSwitchError(f"{axis.upper()} home switch was not reached after {max_search_steps} pulses.")

    gpio.set_axis_direction(cfg, away_dir)
    for _ in range(backoff_steps):
        _check_stop()
        _pulse_home_step(axis, away_dir, gpio, interval_s)

    gpio.set_axis_direction(cfg, home_dir)
    for _ in range(backoff_steps * 2):
        _check_stop()
        if _limit_active(cfg, gpio):
            break
        _pulse_home_step(axis, home_dir, gpio, interval_s * 1.5)
    else:
        raise LimitSwitchError(f"{axis.upper()} home switch was not reached during the final slow approach.")

    _state.set_zero(axis)


def get_position() -> dict[str, float]:
    """Return the current open-loop position in millimetres."""

    return {f"{axis}_mm": round(_state.mm(axis), 4) for axis in ("x", "y", "z")}


def get_status() -> dict[str, Any]:
    """Return driver configuration and open-loop state for UI/status pages."""

    return {
        "driver_mode": "dry_run" if DRY_RUN else "gpiod_tb6600",
        "connected": not DRY_RUN,
        "gpio_chip": GPIO_CHIP,
        "gpiod_api": GPIOD_API,
        "gpio_backend": "dry_run" if DRY_RUN else MOTOR_GPIO_BACKEND,
        "system_gpio_python": SYSTEM_GPIO_PYTHON,
        "system_gpio_helper": str(SYSTEM_GPIO_HELPER),
        "home_mode": HOME_MODE,
        "state_path": str(STATE_PATH),
        "position": get_position(),
        "axes": {
            axis: {
                "max_mm": cfg.max_mm,
                "pulses_per_mm": cfg.pulses_per_mm,
                "channels": [
                    {"dir_offset": channel.dir_offset, "step_offset": channel.step_offset, "dir_positive": channel.dir_positive}
                    for channel in cfg.channels
                ],
                "limit_offset": cfg.limit_offset,
                "homed": _state.homed[axis],
            }
            for axis, cfg in AXES.items()
        },
    }


def all_axes_homed(axes: tuple[str, ...] = ("x", "y", "z")) -> bool:
    """Return whether the requested axes have a valid software zero."""

    return all(bool(_state.homed.get(axis)) for axis in axes)


def _speed_scale_for_targets(targets: dict[str, int], speed_mm_s: float) -> float:
    speed = float(speed_mm_s)
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed_mm_s must be a positive millimetre-per-second value.")

    deltas = {axis: targets[axis] - _state.steps[axis] for axis in targets}
    deltas = {axis: delta for axis, delta in deltas.items() if delta != 0}
    if not deltas:
        return 1.0

    dominant = _dominant_axis(deltas)
    cfg = AXES[dominant]
    return max(0.01, min(1.0, speed / max(cfg.max_speed_mm_s, 0.001)))


def reset_position(x_mm: float = 0.0, y_mm: float = 0.0, z_mm: float = 0.0) -> dict[str, float]:
    """Set the open-loop coordinate without moving motors.

    Use this only after physically placing the table at the matching
    coordinate, or after a known manual recovery.
    """

    with _motion_lock:
        _state.steps["x"] = _state.target_steps("x", float(x_mm))
        _state.steps["y"] = _state.target_steps("y", float(y_mm))
        _state.steps["z"] = _state.target_steps("z", float(z_mm))
        for axis in _state.homed:
            _state.homed[axis] = True
        _state.save()
        _log(f"position reset to X={x_mm:.3f} Y={y_mm:.3f} Z={z_mm:.3f} mm")
        return get_position()


def home_all_axes() -> dict[str, float]:
    """Home all axes.

    Default HOME_MODE is ``soft`` because the purchased slide table does not
    include proximity switches.  In soft mode this function only records the
    current physical position as X/Y/Z zero.  Install switches and set
    ``LOONGSON_AOI_HOME_MODE=limit`` to perform real switch-based homing.
    """

    with _motion_lock:
        clear_stop()
        mode = HOME_MODE
        if mode not in {"soft", "limit", "auto"}:
            raise MotorError("LOONGSON_AOI_HOME_MODE must be soft, limit, or auto.")

        if mode == "soft":
            for axis in ("x", "y", "z"):
                _state.set_zero(axis)
            _state.save()
            _log("soft-home complete; current physical position is now X/Y/Z zero")
            return get_position()

        order = tuple(os.getenv("LOONGSON_AOI_HOME_ORDER", "z,x,y").replace(" ", "").split(","))
        configs = [AXES[axis] for axis in order if axis in AXES]
        with GpioSession(configs, include_limits=True) as gpio:
            for axis in order:
                if axis not in AXES:
                    continue
                if AXES[axis].limit_offset is None:
                    if mode == "auto":
                        _state.set_zero(axis)
                        continue
                    raise LimitSwitchError(f"{axis.upper()} limit switch is required for limit home mode.")
                _hard_home_axis(axis, gpio)

        _state.save()
        _log("home complete")
        return get_position()


def move_z_step(direction: str, dist: float) -> dict[str, float]:
    """Jog the Z axis by ``dist`` millimetres.

    ``direction`` accepts ``up`` or ``down``.  By default ``up`` is the
    positive Z direction; set ``LOONGSON_AOI_Z_UP_POSITIVE=0`` if the motor
    wiring or coordinate convention is opposite.
    """

    norm_direction = str(direction).strip().lower()
    if norm_direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'.")
    dist_mm = float(dist)
    if not math.isfinite(dist_mm) or dist_mm <= 0:
        raise ValueError("dist must be a positive millimetre value.")

    sign = 1 if norm_direction == "up" else -1
    if not Z_UP_POSITIVE:
        sign *= -1

    with _motion_lock:
        cfg = AXES["z"]
        delta_steps = int(round(sign * dist_mm * cfg.pulses_per_mm))
        target = _state.steps["z"] + delta_steps
        return _move_axes_to({"z": target})


def move_to_xy(x: float, y: float) -> dict[str, float]:
    """Move the X/Y stage to an absolute millimetre coordinate."""

    x_mm = float(x)
    y_mm = float(y)
    with _motion_lock:
        target_x = _state.target_steps("x", x_mm)
        target_y = _state.target_steps("y", y_mm)
        return _move_axes_to({"x": target_x, "y": target_y})


def move_to_xy_stream(
    x: float,
    y: float,
    speed_mm_s: float = 2.0,
    progress_callback: MotionProgressCallback | None = None,
    progress_interval_s: float = 0.2,
    segment: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Move X/Y at a requested line-scan speed and report progress.

    The function is still bounded by the configured software travel limits.
    It is intended for continuous video scanning, where the UI needs frequent
    open-loop XY updates while the camera keeps inferring frames.
    """

    x_mm = float(x)
    y_mm = float(y)
    with _motion_lock:
        targets = {
            "x": _state.target_steps("x", x_mm),
            "y": _state.target_steps("y", y_mm),
        }
        speed_scale = _speed_scale_for_targets(targets, speed_mm_s)
        return _move_axes_to(
            targets,
            speed_scale=speed_scale,
            progress_callback=progress_callback,
            progress_interval_s=progress_interval_s,
            segment=segment,
        )


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _main() -> int:
    parser = argparse.ArgumentParser(description="Loongson AOI TB6600 motor driver")
    parser.add_argument("--dry-run", action="store_true", help="simulate GPIO without requiring gpiod")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("home")

    move_xy_parser = subparsers.add_parser("move-xy")
    move_xy_parser.add_argument("x", type=float)
    move_xy_parser.add_argument("y", type=float)

    z_parser = subparsers.add_parser("z-jog")
    z_parser.add_argument("direction", choices=("up", "down"))
    z_parser.add_argument("dist", type=float)

    reset_parser = subparsers.add_parser("reset-position")
    reset_parser.add_argument("--x", type=float, default=0.0)
    reset_parser.add_argument("--y", type=float, default=0.0)
    reset_parser.add_argument("--z", type=float, default=0.0)

    args = parser.parse_args()
    if args.dry_run:
        global DRY_RUN
        DRY_RUN = True

    if args.command == "status":
        _print_json(get_status())
    elif args.command == "home":
        _print_json(home_all_axes())
    elif args.command == "move-xy":
        _print_json(move_to_xy(args.x, args.y))
    elif args.command == "z-jog":
        _print_json(move_z_step(args.direction, args.dist))
    elif args.command == "reset-position":
        _print_json(reset_position(args.x, args.y, args.z))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
