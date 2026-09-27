"""Independent telemetry for the two native smooth-following scenarios."""
import unittest

from tests.harness.gcodegen import penguin_playback, playback_sample


class PenguinTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.rows = penguin_playback()

    def test_three_layers_and_materials_have_real_motion_byte_offsets(self):
        self.assertEqual({row["layer"] for row in self.rows}, {0, 1, 2})
        self.assertEqual({row["tool"] for row in self.rows}, {0, 1, 2})
        self.assertGreater(len(self.rows), 100)
        previous = 0
        for row in self.rows:
            self.assertGreater(row["offset"], previous)
            previous = row["offset"]
            line = self.data[:row["offset"]].splitlines()[-1].decode("ascii")
            self.assertTrue(line.startswith(("G0 ", "G1 ")), line)
            for key, coordinate in zip("XY", row["end"][:2], strict=True):
                if key in line:
                    self.assertIn(f"{key}{coordinate:.3f}", line)

    def test_penguin_is_centred_on_the_native_harness_bed(self):
        for axis in (0, 1):
            coordinates = [row["end"][axis] for row in self.rows]
            self.assertAlmostEqual((min(coordinates) + max(coordinates)) / 2, 125.0, delta=.5)

    def test_interpolation_stays_on_the_commanded_segment_at_corners(self):
        rows = [dict(at=1.0, start=[0, 0, 0, 0], end=[10, 0, 0, 1]),
                dict(at=2.0, start=[10, 0, 0, 1], end=[10, 10, 0, 2])]
        self.assertEqual(playback_sample(rows, .25)["position"], [5, 0, 0, .5])
        self.assertEqual(playback_sample(rows, .75)["position"], [10, 5, 0, 1.5])
        self.assertEqual(playback_sample(rows, -1)["position"], rows[0]["start"])
        self.assertEqual(playback_sample(rows, 2)["position"], rows[-1]["end"])

    def test_velocity_distinguishes_extrusion_travel_and_a_stopped_print(self):
        rows = [dict(at=1.0, start=[0, 0, 0, 0], end=[10, 0, 0, 1]),
                dict(at=2.0, start=[10, 0, 0, 1], end=[10, 10, 0, 1])]
        self.assertEqual(playback_sample(rows, .25)["extruder_velocity"], 1.0)
        self.assertEqual(playback_sample(rows, .75)["extruder_velocity"], 0.0)
        self.assertEqual(playback_sample(rows, .75)["velocity"], 10.0)
        for fraction in (-1, 0, 1, 2):
            sample = playback_sample(rows, fraction)
            self.assertEqual((sample["velocity"], sample["extruder_velocity"]), (0, 0))

    def test_simulator_finishes_in_twenty_seconds_and_reset_restores_the_file(self):
        try:
            from tests.harness.simulator import PrinterState
        except ImportError as exc:
            self.skipTest(str(exc))
        printer = PrinterState()
        original = printer.gcode_bytes, printer.files
        printer.scenario(gcode_fixture="penguin")
        printer.state["print_stats"]["state"] = "printing"
        printer.scenario(gcode_playback_s=20)
        for _ in range(80):
            printer.push_patch()
        sample = playback_sample(self.rows, 1)
        self.assertEqual(printer.state["print_stats"]["info"]["current_layer"], 3)
        self.assertEqual(printer.state["motion_report"]["live_position"], sample["end"])
        self.assertEqual(printer.state["virtual_sdcard"]["file_position"], sample["offset"])
        printer.scenario(gcode_playback_s=100)
        self.assertEqual(printer._playback_duration, 30)
        printer.reset()
        self.assertEqual((printer.gcode_bytes, printer.files), original)
        self.assertEqual(printer._playback_rows, [])


if __name__ == "__main__":
    unittest.main()
