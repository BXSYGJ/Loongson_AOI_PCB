import unittest
from unittest import mock

import motor_gpio_system_helper as helper


class MotorGpioSystemHelperTests(unittest.TestCase):
    def test_interval_is_constant_across_entire_segment(self):
        dominant = {
            "max_speed_mm_s": 3.0,
            "start_speed_mm_s": 0.8,
            "pulses_per_mm": 25.0,
            "ramp_fraction": 0.15,
        }

        intervals = [
            helper.interval_for_tick(dominant, tick, total=750, speed_scale=1.0)
            for tick in (1, 2, 112, 375, 638, 749, 750)
        ]

        self.assertEqual(len(set(intervals)), 1)
        self.assertAlmostEqual(intervals[0], 1.0 / 75.0)

    def test_execute_motion_reports_exact_dual_axis_dda_counts(self):
        class FakeLine:
            def __init__(self, offset):
                self.offset = offset
                self.values = []

            def request(self, **kwargs):
                self.request_kwargs = kwargs

            def set_value(self, value):
                self.values.append(int(value))

            def release(self):
                pass

        class FakeChip:
            OPEN_BY_NUMBER = object()

            def __init__(self, *args):
                self.lines = {}

            def get_line(self, offset):
                self.lines.setdefault(offset, FakeLine(offset))
                return self.lines[offset]

            def close(self):
                pass

        class FakeGpiod:
            Chip = FakeChip
            LINE_REQ_DIR_OUT = object()

        payload = {
            "gpio_chip": "0",
            "consumer": "test",
            "total_ticks": 3,
            "speed_scale": 1.0,
            "motor_nice": 0,
            "direction_setup_s": 0.0,
            "dominant": {
                "max_speed_mm_s": 3.0,
                "start_speed_mm_s": 3.0,
                "pulses_per_mm": 25.0,
                "ramp_fraction": 0.0,
            },
            "axes": [
                {
                    "axis": "x",
                    "count": 3,
                    "pulse_width_s": 0.001,
                    "min_low_s": 0.0005,
                    "settle_s": 0.0,
                    "channels": [{"dir_offset": 57, "step_offset": 59, "dir_value": 1}],
                },
                {
                    "axis": "y",
                    "count": 2,
                    "pulse_width_s": 0.001,
                    "min_low_s": 0.0005,
                    "settle_s": 0.0,
                    "channels": [{"dir_offset": 3, "step_offset": 60, "dir_value": 1}],
                },
            ],
        }

        with mock.patch.object(helper, "apply_process_priority", return_value={"requested": 0, "applied": 0, "warning": None}):
            telemetry = helper.execute_motion(payload, FakeGpiod())

        self.assertEqual(telemetry["requested_pulses"], {"x": 3, "y": 2})
        self.assertEqual(telemetry["emitted_pulses"], {"x": 3, "y": 2})
        self.assertEqual(telemetry["motion_profile"], "constant_speed")

    def test_pulse_timing_preserves_minimum_low_time_at_all_supported_speeds(self):
        dominant = {
            "max_speed_mm_s": 3.0,
            "start_speed_mm_s": 0.8,
            "pulses_per_mm": 25.0,
            "ramp_fraction": 0.0,
        }

        for speed_mm_s in (3.0, 10.0, 25.0):
            timing = helper.pulse_timing_for_tick(
                dominant,
                tick=10,
                total=20,
                speed_scale=speed_mm_s / 3.0,
                pulse_high_s=0.001,
                min_low_s=0.0005,
            )
            self.assertGreaterEqual(timing["low_s"], 0.0005)
            self.assertGreaterEqual(timing["period_s"], 0.0015)

    def test_low_deadline_does_not_compress_low_phase_after_scheduler_delay(self):
        deadline = helper.low_phase_deadline(
            high_started=10.0,
            low_started=10.004,
            period_s=0.0016,
            min_low_s=0.0005,
        )

        self.assertEqual(deadline, 10.0045)


if __name__ == "__main__":
    unittest.main()
