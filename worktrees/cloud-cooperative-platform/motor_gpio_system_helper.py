#!/usr/bin/env python3
"""Run TB6600 GPIO pulses through the board system Python gpiod binding.

The Loongson website normally runs on Python 3.12, while the board-provided
working gpiod extension is installed for the system Python 3.7.  This helper is
therefore intentionally Python 3.7 compatible and receives one complete motion
segment as JSON on stdin.
"""

import json
import os
import sys
import time


def interval_for_tick(dominant, tick, total, speed_scale):
    del tick, total
    rate = max(float(dominant["max_speed_mm_s"]) * float(dominant["pulses_per_mm"]) * speed_scale, 1.0)
    return 1.0 / rate


def pulse_timing_for_tick(dominant, tick, total, speed_scale, pulse_high_s, min_low_s):
    requested_period_s = interval_for_tick(dominant, tick, total, speed_scale)
    period_s = max(requested_period_s, float(pulse_high_s) + float(min_low_s))
    return {
        "requested_period_s": requested_period_s,
        "period_s": period_s,
        "high_s": float(pulse_high_s),
        "low_s": period_s - float(pulse_high_s),
    }


def low_phase_deadline(high_started, low_started, period_s, min_low_s):
    return max(float(high_started) + float(period_s), float(low_started) + float(min_low_s))


def sleep_until(deadline):
    remaining = float(deadline) - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)


def apply_process_priority(requested_nice):
    result = {"requested": int(requested_nice), "applied": None, "warning": None}
    if not hasattr(os, "getpriority") or not hasattr(os, "setpriority"):
        result["warning"] = "process priority APIs are unavailable"
        return result
    try:
        current = int(os.getpriority(os.PRIO_PROCESS, 0))
        if current != int(requested_nice):
            os.setpriority(os.PRIO_PROCESS, 0, int(requested_nice))
        result["applied"] = int(os.getpriority(os.PRIO_PROCESS, 0))
    except Exception as exc:
        result["warning"] = "could not set motor helper nice value: %s" % exc
        try:
            result["applied"] = int(os.getpriority(os.PRIO_PROCESS, 0))
        except Exception:
            pass
    return result


def open_chip(gpiod, chip_value):
    errors = []
    attempts = []
    try:
        chip_number = int(str(chip_value), 0)
    except ValueError:
        chip_number = None
    if hasattr(gpiod.Chip, "OPEN_BY_NUMBER") and chip_number is not None:
        attempts.append((str(chip_number), gpiod.Chip.OPEN_BY_NUMBER))
        attempts.append((chip_number, gpiod.Chip.OPEN_BY_NUMBER))
    attempts.append(("/dev/gpiochip%s" % chip_value,))
    attempts.append((str(chip_value),))

    for args in attempts:
        try:
            return gpiod.Chip(*args)
        except Exception as exc:
            errors.append("%r: %s" % (args, exc))
    raise RuntimeError("could not open gpiochip %s: %s" % (chip_value, "; ".join(errors)))


def request_line(line, consumer, direction, init_value):
    try:
        line.request(consumer=consumer, type=direction, default_vals=[init_value])
    except TypeError:
        line.request(consumer=consumer, type=direction, default_val=init_value)


class GpioHandle(object):
    def __init__(self, chip, lines, request, value_active, value_inactive):
        self.chip = chip
        self.lines = lines
        self.request = request
        self.value_active = value_active
        self.value_inactive = value_inactive

    def set_value(self, offset, value):
        if self.request is not None:
            request_value = self.value_active if value else self.value_inactive
            try:
                self.request.set_value(offset, request_value)
            except TypeError:
                self.request.set_value(offset, int(value))
        else:
            self.lines[offset].set_value(int(value))

    def close(self):
        if self.request is not None:
            try:
                self.request.release()
            except Exception:
                pass
        for line in self.lines.values():
            try:
                line.set_value(0)
            except Exception:
                pass
            try:
                line.release()
            except Exception:
                pass
        try:
            self.chip.close()
        except Exception:
            pass


def request_gpio_handle(gpiod, chip, chip_value, consumer, initial_values):
    if hasattr(chip, "get_line"):
        lines = {}
        for offset, init_value in initial_values.items():
            line = chip.get_line(offset)
            request_line(line, consumer, gpiod.LINE_REQ_DIR_OUT, init_value)
            lines[offset] = line
        return GpioHandle(chip, lines, None, 1, 0)

    try:
        from gpiod.line import Direction, Value
    except Exception as exc:
        raise RuntimeError("gpiod Chip has no get_line and gpiod.line Direction/Value is unavailable: %s" % exc)
    if not hasattr(gpiod, "LineSettings"):
        raise RuntimeError("gpiod Chip has no get_line and gpiod.LineSettings is unavailable")

    config = {}
    for offset, init_value in initial_values.items():
        config[offset] = gpiod.LineSettings(
            direction=Direction.OUTPUT,
            output_value=Value.ACTIVE if init_value else Value.INACTIVE,
        )

    request_errors = []
    chip_path = "/dev/gpiochip%s" % chip_value
    if hasattr(gpiod, "request_lines"):
        try:
            request = gpiod.request_lines(chip_path, consumer=consumer, config=config)
            return GpioHandle(chip, {}, request, Value.ACTIVE, Value.INACTIVE)
        except Exception as exc:
            request_errors.append("module request_lines(%s): %s" % (chip_path, exc))
    if hasattr(chip, "request_lines"):
        try:
            request = chip.request_lines(consumer=consumer, config=config)
            return GpioHandle(chip, {}, request, Value.ACTIVE, Value.INACTIVE)
        except Exception as exc:
            request_errors.append("chip request_lines: %s" % exc)
    if not request_errors:
        raise RuntimeError("gpiod Chip has no get_line and no request_lines API is available")

    chip_info = "unknown"
    num_lines = "unknown"
    try:
        info = chip.get_info()
        chip_info = repr(info)
        num_lines = str(getattr(info, "num_lines", "unknown"))
    except Exception as exc:
        chip_info = "get_info failed: %s" % exc
    raise RuntimeError(
        "request_lines failed. offsets=%s; chip_info=%s; num_lines=%s; gpiod_file=%s; errors=%s"
        % (sorted(initial_values.keys()), chip_info, num_lines, getattr(gpiod, "__file__", "-"), "; ".join(request_errors))
    )


def execute_motion(payload, gpiod):
    consumer = payload.get("consumer") or "loongson-aoi-motor"
    total_ticks = int(payload["total_ticks"])
    speed_scale = float(payload.get("speed_scale") or 1.0)
    axes = payload["axes"]
    progressed = dict((axis["axis"], 0) for axis in axes)
    counts = dict((axis["axis"], int(axis["count"])) for axis in axes)
    emitted = dict((axis["axis"], 0) for axis in axes)
    priority = apply_process_priority(int(payload.get("motor_nice", 0)))
    chip_value = payload.get("gpio_chip", "0")
    chip = open_chip(gpiod, chip_value)
    initial_values = {}
    for axis in axes:
        for channel in axis["channels"]:
            initial_values[int(channel["dir_offset"])] = int(channel["dir_value"])
            initial_values[int(channel["step_offset"])] = 0
    gpio = request_gpio_handle(gpiod, chip, chip_value, consumer, initial_values)

    try:
        for axis in axes:
            for channel in axis["channels"]:
                gpio.set_value(int(channel["dir_offset"]), int(channel["dir_value"]))
        direction_setup_s = max(float(payload.get("direction_setup_s", 0.001)), 0.0)
        if direction_setup_s:
            time.sleep(direction_setup_s)

        started_at = time.perf_counter()
        max_scheduler_delay_s = 0.0
        for tick in range(1, total_ticks + 1):
            due_axes = []
            for axis_name, count in counts.items():
                expected = (tick * count) // total_ticks
                if expected > progressed[axis_name]:
                    due_axes.append(axis_name)
                    progressed[axis_name] = expected
            pulse_width_s = 0.0
            for axis in axes:
                if axis["axis"] in due_axes:
                    pulse_width_s = max(pulse_width_s, float(axis["pulse_width_s"]))
                    for channel in axis["channels"]:
                        gpio.set_value(int(channel["step_offset"]), 1)
            min_low_s = max(
                [float(axis.get("min_low_s") or 0.0) for axis in axes if axis["axis"] in due_axes] or [0.0]
            )
            timing = pulse_timing_for_tick(
                payload["dominant"], tick, total_ticks, speed_scale, pulse_width_s, min_low_s
            )
            high_started = time.perf_counter()
            sleep_until(high_started + timing["high_s"])
            high_ended = time.perf_counter()
            max_scheduler_delay_s = max(
                max_scheduler_delay_s,
                max(0.0, high_ended - (high_started + timing["high_s"])),
            )
            for axis in axes:
                if axis["axis"] in due_axes:
                    for channel in axis["channels"]:
                        gpio.set_value(int(channel["step_offset"]), 0)
                    emitted[axis["axis"]] += 1

            low_started = time.perf_counter()
            low_deadline = low_phase_deadline(high_started, low_started, timing["period_s"], min_low_s)
            sleep_until(low_deadline)
            woke_at = time.perf_counter()
            max_scheduler_delay_s = max(max_scheduler_delay_s, max(0.0, woke_at - low_deadline))

        settle_s = max(float(axis.get("settle_s") or 0.0) for axis in axes)
        if settle_s:
            time.sleep(settle_s)
        elapsed_s = max(time.perf_counter() - started_at, 0.0)
        dominant_ppm = max(float(payload["dominant"]["pulses_per_mm"]), 0.001)
        dominant_distance_mm = float(total_ticks) / dominant_ppm
        return {
            "status": "complete",
            "motion_profile": "constant_speed",
            "requested_pulses": counts,
            "emitted_pulses": emitted,
            "total_ticks": total_ticks,
            "elapsed_s": elapsed_s,
            "average_tick_hz": float(total_ticks) / elapsed_s if elapsed_s else 0.0,
            "achieved_dominant_speed_mm_s": dominant_distance_mm / elapsed_s if elapsed_s else 0.0,
            "max_scheduler_delay_ms": max_scheduler_delay_s * 1000.0,
            "priority": priority,
            "timing": {
                "period_mode": "constant",
                "direction_setup_s": direction_setup_s,
                "pulse_high_s": max(float(axis["pulse_width_s"]) for axis in axes),
                "min_low_s": max(float(axis.get("min_low_s") or 0.0) for axis in axes),
            },
        }
    finally:
        gpio.close()


def main():
    import gpiod

    payload = json.load(sys.stdin)
    return execute_motion(payload, gpiod)


if __name__ == "__main__":
    try:
        result = main()
        sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    except Exception as exc:
        sys.stderr.write("%s\n" % exc)
        sys.exit(1)
