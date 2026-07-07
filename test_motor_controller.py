import sys
import types
import unittest

import motor_controller


class MotorControllerTests(unittest.TestCase):
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
