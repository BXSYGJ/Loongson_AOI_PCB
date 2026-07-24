import sys
import types
import unittest
from unittest import mock

import motor_controller


class MotorControllerTests(unittest.TestCase):
    def test_motion_interval_is_constant_across_entire_segment(self):
        cfg = motor_controller.AXES["x"]
        intervals = [
            motor_controller._interval_for_tick(cfg, tick, total=750, speed_scale=1.0)
            for tick in (1, 2, 112, 375, 638, 749, 750)
        ]

        self.assertEqual(len(set(intervals)), 1)
        expected = max(
            1.0 / (cfg.max_speed_mm_s * cfg.pulses_per_mm),
            cfg.pulse_width_s + cfg.min_low_s,
        )
        self.assertAlmostEqual(intervals[0], expected)

    def test_xy_motion_is_rejected_until_untrusted_position_is_reset(self):
        original_trusted = motor_controller._state.position_trusted
        try:
            motor_controller._state.position_trusted = False
            with self.assertRaises(motor_controller.MotorError) as raised:
                motor_controller.move_to_xy_stream(8.0, 0.0, 3.0)
            self.assertIn("untrusted", str(raised.exception).lower())
        finally:
            motor_controller._state.position_trusted = original_trusted

    def test_system_helper_telemetry_mismatch_invalidates_position_without_advancing(self):
        original_steps = dict(motor_controller._state.steps)
        original_homed = dict(motor_controller._state.homed)
        original_trusted = motor_controller._state.position_trusted
        original_save = motor_controller._state.save
        target = motor_controller._state.target_steps("x", 8.0)

        try:
            motor_controller._state.steps.update({"x": 0, "y": 0, "z": 0})
            motor_controller._state.homed.update({"x": True, "y": True, "z": True})
            motor_controller._state.position_trusted = True
            motor_controller._state.save = lambda: None
            telemetry = {
                "status": "complete",
                "requested_pulses": {"x": target},
                "emitted_pulses": {"x": target - 1},
            }
            with mock.patch.object(motor_controller, "_use_system_python_gpio", return_value=True), mock.patch.object(
                motor_controller, "_run_system_python_motion", return_value=telemetry
            ):
                with self.assertRaises(motor_controller.MotorError):
                    motor_controller._move_axes_to({"x": target})

            self.assertEqual(motor_controller._state.steps["x"], 0)
            self.assertFalse(motor_controller._state.position_trusted)
            self.assertFalse(motor_controller._state.homed["x"])
        finally:
            motor_controller._state.steps.update(original_steps)
            motor_controller._state.homed.update(original_homed)
            motor_controller._state.position_trusted = original_trusted
            motor_controller._state.save = original_save

    def test_system_helper_telemetry_match_updates_position_and_status(self):
        original_steps = dict(motor_controller._state.steps)
        original_homed = dict(motor_controller._state.homed)
        original_trusted = motor_controller._state.position_trusted
        original_telemetry = motor_controller._last_motion_telemetry
        original_save = motor_controller._state.save
        target = motor_controller._state.target_steps("x", 8.0)

        try:
            motor_controller._state.steps.update({"x": 0, "y": 0, "z": 0})
            motor_controller._state.homed.update({"x": True, "y": True, "z": True})
            motor_controller._state.position_trusted = True
            motor_controller._state.save = lambda: None
            telemetry = {
                "status": "complete",
                "requested_pulses": {"x": target},
                "emitted_pulses": {"x": target},
                "elapsed_s": 1.5,
                "average_tick_hz": 133.3,
                "max_scheduler_delay_ms": 2.0,
            }
            with mock.patch.object(motor_controller, "_use_system_python_gpio", return_value=True), mock.patch.object(
                motor_controller, "_run_system_python_motion", return_value=telemetry
            ):
                result = motor_controller._move_axes_to({"x": target})

            self.assertEqual(result["x_mm"], 8.0)
            self.assertTrue(motor_controller._state.position_trusted)
            self.assertEqual(motor_controller.get_status()["last_motion_telemetry"]["emitted_pulses"]["x"], target)
        finally:
            motor_controller._state.steps.update(original_steps)
            motor_controller._state.homed.update(original_homed)
            motor_controller._state.position_trusted = original_trusted
            motor_controller._last_motion_telemetry = original_telemetry
            motor_controller._state.save = original_save

    def test_swap_xy_mapping_routes_logical_x_to_motor_y_and_reports_logical_position(self):
        original_steps = dict(motor_controller._state.steps)
        original_homed = dict(motor_controller._state.homed)
        original_trusted = motor_controller._state.position_trusted
        original_dry_run = motor_controller.DRY_RUN
        original_mapping = motor_controller.MOTOR_AXIS_MAPPING
        original_save = motor_controller._state.save
        original_move_axes_to = motor_controller._move_axes_to
        captured_targets = {}

        def fake_move_axes_to(targets, **kwargs):
            captured_targets.update(targets)
            motor_controller._state.steps.update(targets)
            return motor_controller.get_position()

        try:
            motor_controller.DRY_RUN = True
            motor_controller.MOTOR_AXIS_MAPPING = "physical_x_motor_y_physical_y_motor_x"
            motor_controller._state.steps.update({"x": 0, "y": 0, "z": 0})
            motor_controller._state.homed.update({"x": True, "y": True, "z": True})
            motor_controller._state.position_trusted = True
            motor_controller._state.save = lambda: None
            motor_controller._move_axes_to = fake_move_axes_to

            result = motor_controller.move_to_xy(30.0, 18.0)

            self.assertEqual(result["x_mm"], 30.0)
            self.assertEqual(result["y_mm"], 18.0)
            self.assertEqual(captured_targets["x"], motor_controller._state.target_steps("x", 18.0))
            self.assertEqual(captured_targets["y"], motor_controller._state.target_steps("y", 30.0))
            self.assertEqual(motor_controller._state.steps["x"], motor_controller._state.target_steps("x", 18.0))
            self.assertEqual(motor_controller._state.steps["y"], motor_controller._state.target_steps("y", 30.0))
        finally:
            motor_controller.DRY_RUN = original_dry_run
            motor_controller.MOTOR_AXIS_MAPPING = original_mapping
            motor_controller._state.steps.update(original_steps)
            motor_controller._state.homed.update(original_homed)
            motor_controller._state.position_trusted = original_trusted
            motor_controller._state.save = original_save
            motor_controller._move_axes_to = original_move_axes_to

    def test_stream_speed_scale_reflects_requested_speed_above_nominal_default(self):
        original_steps = dict(motor_controller._state.steps)
        try:
            motor_controller._state.steps["x"] = 0
            target = {"x": motor_controller._state.target_steps("x", 16.0)}

            scale_3 = motor_controller._speed_scale_for_targets(target, 3.0)
            scale_6 = motor_controller._speed_scale_for_targets(target, 6.0)

            self.assertGreater(scale_6, scale_3)
        finally:
            motor_controller._state.steps.update(original_steps)

    def test_gpiod_v1_retries_numeric_chip_when_string_chip_is_missing(self):
        fake_gpiod = types.ModuleType("gpiod")
        attempts = []

        class FakeChip:
            OPEN_BY_NUMBER = object()

            def __init__(self, *args):
                attempts.append(args)
                if isinstance(args[0], str) and args[0] == "0":
                    raise FileNotFoundError(2, "No such file or directory")
                self.lines = []

            def get_line(self, offset):
                line = FakeLine(offset)
                self.lines.append(line)
                return line

            def close(self):
                pass

        class FakeLine:
            def __init__(self, offset):
                self.offset = offset

            def request(self, **kwargs):
                self.kwargs = kwargs

            def set_value(self, value):
                self.value = value

            def release(self):
                pass

        fake_gpiod.Chip = FakeChip
        fake_gpiod.LINE_REQ_DIR_OUT = object()
        fake_gpiod.LINE_REQ_DIR_IN = object()

        original_gpiod = sys.modules.get("gpiod")
        original_dry_run = motor_controller.DRY_RUN
        original_api = motor_controller.GPIOD_API
        try:
            sys.modules["gpiod"] = fake_gpiod
            motor_controller.DRY_RUN = False
            motor_controller.GPIOD_API = "v1"

            with motor_controller.GpioSession([motor_controller.AXES["y"]]) as gpio:
                self.assertEqual(gpio._api, "v1")

            self.assertEqual(attempts[0][0], "0")
            self.assertEqual(attempts[1][0], 0)
        finally:
            motor_controller.DRY_RUN = original_dry_run
            motor_controller.GPIOD_API = original_api
            if original_gpiod is None:
                sys.modules.pop("gpiod", None)
            else:
                sys.modules["gpiod"] = original_gpiod

    def test_gpio_invalid_argument_reports_api_offsets_and_num_lines_hint(self):
        fake_gpiod = types.ModuleType("gpiod")

        class Direction:
            OUTPUT = object()
            INPUT = object()

        class Value:
            INACTIVE = object()

        class LineSettings:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def request_lines(*args, **kwargs):
            raise ValueError(22, "Invalid argument")

        fake_gpiod.LineSettings = LineSettings
        fake_gpiod.request_lines = request_lines

        fake_line = types.ModuleType("gpiod.line")
        fake_line.Direction = Direction
        fake_line.Value = Value

        original_gpiod = sys.modules.get("gpiod")
        original_line = sys.modules.get("gpiod.line")
        original_dry_run = motor_controller.DRY_RUN
        original_api = motor_controller.GPIOD_API
        try:
            sys.modules["gpiod"] = fake_gpiod
            sys.modules["gpiod.line"] = fake_line
            motor_controller.DRY_RUN = False
            motor_controller.GPIOD_API = "v2"

            with self.assertRaises(motor_controller.MotorError) as raised:
                with motor_controller.GpioSession([motor_controller.AXES["x"]]):
                    pass

            message = str(raised.exception)
            self.assertIn("GPIO request failed", message)
            self.assertIn("api=v2", message)
            self.assertIn("Requested offsets", message)
            self.assertIn("outputs=57,59", message)
            self.assertIn("/dev/gpiochip0", message)
            self.assertIn("num_lines", message)
        finally:
            motor_controller.DRY_RUN = original_dry_run
            motor_controller.GPIOD_API = original_api
            if original_gpiod is None:
                sys.modules.pop("gpiod", None)
            else:
                sys.modules["gpiod"] = original_gpiod
            if original_line is None:
                sys.modules.pop("gpiod.line", None)
            else:
                sys.modules["gpiod.line"] = original_line


if __name__ == "__main__":
    unittest.main()
