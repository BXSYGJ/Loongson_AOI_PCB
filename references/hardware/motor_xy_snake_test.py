#!/usr/bin/env python3
"""Python 3.7 compatible 4x4 snake motion test for the Loongson stage."""

from __future__ import print_function

import argparse
import os
import sys
import time


GPIO_CHIP = "0"
PULSES_PER_MM = 25.0
PULSE_HIGH_S = 0.001
MIN_LOW_S = 0.0005
DIR_SETUP_S = 0.001
MAX_TEST_SPEED_MM_S = 25.0

# This matches LOONGSON_AOI_MOTOR_AXIS_MAPPING=
# physical_x_motor_y_physical_y_motor_x in the website deployment.
AXES = {
    "x": {"dir_offset": 3, "step_offset": 60, "positive_value": 1},
    "y": {"dir_offset": 57, "step_offset": 59, "positive_value": 1},
}


def build_snake_points():
    points = []
    x_values = [0.0, 30.0, 60.0, 90.0]
    y_values = [0.0, 18.0, 36.0, 54.0]
    for row_index, y_mm in enumerate(y_values):
        row_x_values = x_values if row_index % 2 == 0 else list(reversed(x_values))
        for x_mm in row_x_values:
            points.append((x_mm, y_mm))
    return points


def describe_segment(start, target):
    start_x, start_y = float(start[0]), float(start[1])
    target_x, target_y = float(target[0]), float(target[1])
    delta_x = target_x - start_x
    delta_y = target_y - start_y
    if abs(delta_x) > 1e-9 and abs(delta_y) > 1e-9:
        raise ValueError("Only one logical axis may move in a snake segment.")
    if abs(delta_x) <= 1e-9 and abs(delta_y) <= 1e-9:
        raise ValueError("A motion segment must have a non-zero distance.")

    axis_name = "x" if abs(delta_x) > 1e-9 else "y"
    delta_mm = delta_x if axis_name == "x" else delta_y
    distance_mm = abs(delta_mm)
    pulses = int(round(distance_mm * PULSES_PER_MM))
    if pulses <= 0:
        raise ValueError("Motion distance is too small to produce a pulse.")
    return {
        "axis": axis_name,
        "direction": 1 if delta_mm > 0 else -1,
        "distance_mm": distance_mm,
        "pulses": pulses,
        "start": (start_x, start_y),
        "target": (target_x, target_y),
    }


def sleep_until(clock, deadline):
    remaining = float(deadline) - float(clock.perf_counter())
    if remaining > 0:
        clock.sleep(remaining)


class GpioSession(object):
    def __init__(self, gpiod_module, chip_number=GPIO_CHIP):
        self.gpiod = gpiod_module
        self.chip_number = str(chip_number)
        self.chip = None
        self.lines = {}

    def open(self):
        self.chip = self.gpiod.Chip(self.chip_number, self.gpiod.Chip.OPEN_BY_NUMBER)
        try:
            for axis_name in ("x", "y"):
                config = AXES[axis_name]
                for line_name, offset in (
                    ("dir", config["dir_offset"]),
                    ("step", config["step_offset"]),
                ):
                    line = self.chip.get_line(offset)
                    try:
                        line.request(
                            consumer="aoi-xy-snake-test",
                            type=self.gpiod.LINE_REQ_DIR_OUT,
                            default_vals=[0],
                        )
                    except TypeError:
                        line.request(
                            consumer="aoi-xy-snake-test",
                            type=self.gpiod.LINE_REQ_DIR_OUT,
                            default_val=0,
                        )
                    self.lines[(axis_name, line_name)] = line
        except Exception:
            self.close()
            raise
        return self

    def set_direction(self, axis_name, direction):
        config = AXES[axis_name]
        positive_value = int(config["positive_value"])
        value = positive_value if direction > 0 else 1 - positive_value
        self.lines[(axis_name, "dir")].set_value(value)

    def set_step(self, axis_name, value):
        self.lines[(axis_name, "step")].set_value(int(value))

    def close(self):
        for axis_name in ("x", "y"):
            line = self.lines.get((axis_name, "step"))
            if line is not None:
                try:
                    line.set_value(0)
                except Exception:
                    pass
        for line in list(self.lines.values()):
            try:
                line.release()
            except Exception:
                pass
        self.lines = {}
        if self.chip is not None:
            try:
                self.chip.close()
            except Exception:
                pass
            self.chip = None


def execute_segment(gpio, segment, speed_mm_s, clock=time):
    speed_mm_s = float(speed_mm_s)
    if speed_mm_s <= 0 or speed_mm_s > MAX_TEST_SPEED_MM_S:
        raise ValueError("speed must be greater than 0 and no more than 25 mm/s")

    axis_name = segment["axis"]
    requested_pulses = int(segment["pulses"])
    pulse_rate_hz = speed_mm_s * PULSES_PER_MM
    period_s = max(1.0 / pulse_rate_hz, PULSE_HIGH_S + MIN_LOW_S)
    emitted_pulses = 0
    max_scheduler_delay_s = 0.0

    gpio.set_direction(axis_name, int(segment["direction"]))
    clock.sleep(DIR_SETUP_S)
    started_at = clock.perf_counter()
    next_high_at = started_at

    for _ in range(requested_pulses):
        sleep_until(clock, next_high_at)
        high_started = clock.perf_counter()
        max_scheduler_delay_s = max(max_scheduler_delay_s, max(0.0, high_started - next_high_at))

        gpio.set_step(axis_name, 1)
        high_deadline = high_started + PULSE_HIGH_S
        sleep_until(clock, high_deadline)
        high_ended = clock.perf_counter()
        max_scheduler_delay_s = max(max_scheduler_delay_s, max(0.0, high_ended - high_deadline))

        gpio.set_step(axis_name, 0)
        emitted_pulses += 1
        low_started = clock.perf_counter()
        low_deadline = max(high_started + period_s, low_started + MIN_LOW_S)
        sleep_until(clock, low_deadline)
        low_ended = clock.perf_counter()
        max_scheduler_delay_s = max(max_scheduler_delay_s, max(0.0, low_ended - low_deadline))
        next_high_at = low_deadline

    elapsed_s = max(float(clock.perf_counter()) - float(started_at), 0.0)
    distance_mm = float(segment["distance_mm"])
    return {
        "axis": axis_name,
        "start": segment["start"],
        "target": segment["target"],
        "requested_pulses": requested_pulses,
        "emitted_pulses": emitted_pulses,
        "elapsed_s": elapsed_s,
        "actual_speed_mm_s": distance_mm / elapsed_s if elapsed_s else 0.0,
        "max_scheduler_delay_ms": max_scheduler_delay_s * 1000.0,
    }


def print_segment_plan(index, segment):
    print(
        "Segment {0:02d}: {1} -> {2}, axis={3}, distance={4:.3f}mm, pulses={5}".format(
            index,
            segment["start"],
            segment["target"],
            segment["axis"].upper(),
            segment["distance_mm"],
            segment["pulses"],
        ),
        flush=True,
    )


def print_telemetry(index, telemetry):
    print(
        "Segment {0:02d} complete: pulses={1}/{2}, elapsed={3:.3f}s, "
        "actual_speed={4:.3f}mm/s, max_delay={5:.3f}ms".format(
            index,
            telemetry["emitted_pulses"],
            telemetry["requested_pulses"],
            telemetry["elapsed_s"],
            telemetry["actual_speed_mm_s"],
            telemetry["max_scheduler_delay_ms"],
        ),
        flush=True,
    )


def run_motion_test(speed_mm_s=3.0, pause_s=1.0, dry_run=False, gpiod_module=None, clock=time):
    speed_mm_s = float(speed_mm_s)
    pause_s = float(pause_s)
    if speed_mm_s <= 0 or speed_mm_s > MAX_TEST_SPEED_MM_S:
        raise ValueError("speed must be greater than 0 and no more than 25 mm/s")
    if pause_s < 0 or pause_s > 10:
        raise ValueError("pause must be between 0 and 10 seconds")

    points = build_snake_points()
    segments = []
    current = points[0]
    for target in points[1:]:
        segments.append(describe_segment(current, target))
        current = target
    segments.append(describe_segment(current, points[0]))

    print("4x4 snake motion test", flush=True)
    print("Logical X: DIR=3 STEP=60, step=30mm", flush=True)
    print("Logical Y: DIR=57 STEP=59, step=18mm", flush=True)
    print("Speed={0:.3f}mm/s, pause={1:.3f}s".format(speed_mm_s, pause_s), flush=True)
    print("Point 01 reached: {0}".format(points[0]), flush=True)

    if dry_run:
        for index, segment in enumerate(segments, start=1):
            print_segment_plan(index, segment)
        print("DRY RUN complete. GPIO was not imported or accessed.", flush=True)
        return {"completed": True, "dry_run": True, "segments": segments, "final_position": points[0]}

    if gpiod_module is None:
        import gpiod as gpiod_module

    gpio = GpioSession(gpiod_module).open()
    telemetry_items = []
    try:
        if pause_s:
            clock.sleep(pause_s)
        for index, segment in enumerate(segments, start=1):
            is_return = index == len(segments)
            label = "Return" if is_return else "Segment"
            print("{0} {1:02d} starting".format(label, index), flush=True)
            print_segment_plan(index, segment)
            telemetry = execute_segment(gpio, segment, speed_mm_s, clock=clock)
            telemetry_items.append(telemetry)
            if telemetry["requested_pulses"] != telemetry["emitted_pulses"]:
                raise RuntimeError("pulse count mismatch; open-loop position is untrusted")
            print_telemetry(index, telemetry)
            if not is_return and pause_s:
                print("Stopped at {0} for {1:.3f}s".format(segment["target"], pause_s), flush=True)
                clock.sleep(pause_s)
    finally:
        gpio.close()

    print("Test complete. Stage returned to logical origin (0.0, 0.0).", flush=True)
    return {
        "completed": True,
        "dry_run": False,
        "telemetry": telemetry_items,
        "final_position": points[0],
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a Python 3.7 GPIO-only 4x4 snake stage test.")
    parser.add_argument("--speed", type=float, default=3.0, help="Motion speed in mm/s (default: 3).")
    parser.add_argument("--pause", type=float, default=1.0, help="Pause at each scan point in seconds (default: 1).")
    parser.add_argument("--dry-run", action="store_true", help="Print the route without importing or accessing gpiod.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.dry_run:
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            print("ERROR: run the hardware test as root with sudo.", file=sys.stderr)
            return 2
        print("WARNING: the stage must start at logical origin (0,0).", flush=True)
        print("Required clear travel area: X=90mm, Y=54mm.", flush=True)
        confirmation = input("Type YES to start the automatic motion test: ").strip()
        if confirmation != "YES":
            print("Cancelled. GPIO was not accessed.", flush=True)
            return 1

    try:
        run_motion_test(speed_mm_s=args.speed, pause_s=args.pause, dry_run=args.dry_run)
        return 0
    except KeyboardInterrupt:
        print("\nINTERRUPTED: GPIO released; position is untrusted. Do not auto-return.", file=sys.stderr)
        return 130
    except Exception as exc:
        print("ERROR: {0}".format(exc), file=sys.stderr)
        print("GPIO released; position is untrusted. Do not assume the stage is at origin.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
