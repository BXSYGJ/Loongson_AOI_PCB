import sys
import types
import unittest

import motor_controller


class MotorControllerTests(unittest.TestCase):
    def test_swap_xy_mapping_routes_logical_x_to_motor_y_and_reports_logical_position(self):
        original_steps = dict(motor_controller._state.steps)
        original_homed = dict(motor_controller._state.homed)
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
