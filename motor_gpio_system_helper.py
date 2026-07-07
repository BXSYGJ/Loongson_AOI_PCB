#!/usr/bin/env python3
"""Run TB6600 GPIO pulses through the board system Python gpiod binding.

The Loongson website normally runs on Python 3.12, while the board-provided
working gpiod extension is installed for the system Python 3.7.  This helper is
therefore intentionally Python 3.7 compatible and receives one complete motion
segment as JSON on stdin.
"""

import json
import sys
import time


def interval_for_tick(dominant, tick, total, speed_scale):
    max_rate = max(float(dominant["max_speed_mm_s"]) * float(dominant["pulses_per_mm"]) * speed_scale, 1.0)
    start_rate = min(max(float(dominant["start_speed_mm_s"]) * float(dominant["pulses_per_mm"]), 1.0), max_rate)
    ramp_ticks = int(total * max(0.0, min(float(dominant["ramp_fraction"]), 0.45)))
    if ramp_ticks <= 1:
        rate = max_rate
    elif tick <= ramp_ticks:
        alpha = float(tick) / float(ramp_ticks)
        rate = start_rate + (max_rate - start_rate) * alpha
    elif tick > total - ramp_ticks:
        alpha = max(float(total - tick) / float(ramp_ticks), 0.0)
        rate = start_rate + (max_rate - start_rate) * alpha
    else:
        rate = max_rate
    return max(1.0 / rate, float(dominant["pulse_width_s"]))


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


def main():
    import gpiod

    payload = json.load(sys.stdin)
    consumer = payload.get("consumer") or "loongson-aoi-motor"
    total_ticks = int(payload["total_ticks"])
    speed_scale = float(payload.get("speed_scale") or 1.0)
    axes = payload["axes"]
    progressed = dict((axis["axis"], 0) for axis in axes)
    counts = dict((axis["axis"], int(axis["count"])) for axis in axes)
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
        time.sleep(0.001)

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
            time.sleep(pulse_width_s)
            for axis in axes:
                if axis["axis"] in due_axes:
                    for channel in axis["channels"]:
                        gpio.set_value(int(channel["step_offset"]), 0)

            interval_s = interval_for_tick(payload["dominant"], tick, total_ticks, speed_scale)
            sleep_s = max(0.0, interval_s - pulse_width_s)
            if sleep_s:
                time.sleep(sleep_s)

        settle_s = max(float(axis.get("settle_s") or 0.0) for axis in axes)
        if settle_s:
            time.sleep(settle_s)
    finally:
        gpio.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write("%s\n" % exc)
        sys.exit(1)
