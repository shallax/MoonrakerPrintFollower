"""Pure tests for manual toolhead control policy: scripts, gates, queue."""
from __future__ import annotations

import unittest

from plugins.ToolheadPolicy import (
    CENTER_Z_MM,
    EXTRUDE_DISTANCES,
    EXTRUDE_SPEED_DEFAULT,
    JOG_DISTANCE_DEFAULT,
    JOG_DISTANCES,
    MAX_PENDING_OPS,
    STATUS_QUEUE_FULL,
    center_script,
    z0_script,
    axis_ok,
    clamp_relative_move,
    extrude_distance_ok,
    extrude_script,
    extrude_speed_ok,
    home_script,
    jog_distance_ok,
    jog_gate,
    jog_script,
    make_extrude_op,
    make_home_op,
    make_jog_op,
    make_motors_off_op,
    motors_off_script,
    position_mode_text,
    push_op,
)


class ToolheadScriptTests(unittest.TestCase):
    def test_jog_script_absolute_sandwich(self):
        self.assertEqual(jog_script("x", 1.0), "G91\nG1 X1 F3000\nG90")
        self.assertEqual(jog_script("X", -0.1), "G91\nG1 X-0.1 F3000\nG90")
        self.assertEqual(jog_script("y", 10.0), "G91\nG1 Y10 F3000\nG90")

    def test_z_jog_uses_the_z_feedrate(self):
        self.assertEqual(jog_script("z", 1.0), "G91\nG1 Z1 F600\nG90")

    def test_relative_mode_omits_the_restore(self):
        self.assertEqual(jog_script("x", 1.0, absolute_coordinates=False), "G91\nG1 X1 F3000")

    def test_jog_script_rejects_unknown_axes(self):
        with self.assertRaises(ValueError):
            jog_script("e", 1.0)

    def test_home_scripts(self):
        self.assertEqual(home_script(), "G28")
        self.assertEqual(home_script("x"), "G28 X")
        self.assertEqual(home_script("Z"), "G28 Z")
        with self.assertRaises(ValueError):
            home_script("q")

    def test_motors_off_script(self):
        self.assertEqual(motors_off_script(), "M18")

    def test_extrude_script(self):
        self.assertEqual(extrude_script(5.0), "G91\nG1 E5 F300\nG90")
        self.assertEqual(extrude_script(-5.0), "G91\nG1 E-5 F300\nG90")
        self.assertEqual(extrude_script(2.5, speed_mm_per_min=120), "G91\nG1 E2.5 F120\nG90")
        self.assertEqual(extrude_script(2.5, absolute_coordinates=False), "G91\nG1 E2.5 F300")
        with self.assertRaises(ValueError):
            extrude_script(150.0)
        with self.assertRaises(ValueError):
            extrude_script(5.0, speed_mm_per_min=2000)

    def test_distance_and_speed_bounds(self):
        self.assertTrue(jog_distance_ok(0.01))
        self.assertTrue(jog_distance_ok(300))
        self.assertFalse(jog_distance_ok(0.001))
        self.assertFalse(jog_distance_ok(500))
        self.assertFalse(jog_distance_ok("junk"))
        self.assertTrue(extrude_distance_ok(0.1))
        self.assertTrue(extrude_distance_ok(100))
        self.assertFalse(extrude_distance_ok(0.05))
        self.assertFalse(extrude_distance_ok(-150))
        self.assertTrue(extrude_speed_ok(30))
        self.assertTrue(extrude_speed_ok(1800))
        self.assertFalse(extrude_speed_ok(20))
        self.assertFalse(extrude_speed_ok(2400))

    def test_preset_lists(self):
        self.assertEqual(JOG_DISTANCES, (0.1, 0.5, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 125.0))
        self.assertEqual(JOG_DISTANCE_DEFAULT, 25.0)
        self.assertEqual(EXTRUDE_DISTANCES, (5.0, 10.0, 15.0, 25.0, 75.0, 100.0))
        self.assertEqual(EXTRUDE_SPEED_DEFAULT, 300.0)

    def test_readout_helpers(self):
        self.assertEqual(position_mode_text(True), "Absolute")
        self.assertEqual(position_mode_text(False), "Relative")
        self.assertTrue(axis_ok("y"))
        self.assertFalse(axis_ok("e"))


class ToolheadClampTests(unittest.TestCase):
    def test_clamp_relative_move(self):
        self.assertEqual(clamp_relative_move(25.0, 190.0, 0.0, 200.0), 10.0)   # clamp to max
        self.assertEqual(clamp_relative_move(-25.0, 10.0, 0.0, 200.0), 0.0)    # crossing min: forbidden
        self.assertEqual(clamp_relative_move(25.0, 10.0, 0.0, 200.0), 25.0)    # inside
        self.assertEqual(clamp_relative_move(-25.0, 190.0, 0.0, 200.0), -25.0)
        self.assertEqual(clamp_relative_move(-25.0, 0.0, 0.0, 200.0), 0.0)     # at the limit
        self.assertEqual(clamp_relative_move(25.0, 200.0, 0.0, 200.0), 0.0)
        self.assertEqual(clamp_relative_move(-25.0, 190.0, None, None), -25.0)  # no bounds
        self.assertEqual(clamp_relative_move(-25.0, -5.0, 0.0, 200.0), 0.0)     # already out
        self.assertEqual(clamp_relative_move(25.0, 205.0, 0.0, 200.0), 0.0)


class ToolheadParkTests(unittest.TestCase):
    def test_center_script_absolute_and_relative(self):
        self.assertEqual(center_script(100.0, 100.0), "G1 X100 Y100 Z50 F3000")
        self.assertEqual(center_script(100.0, 100.0, z=20.0), "G1 X100 Y100 Z20 F3000")
        self.assertEqual(center_script(100.0, 100.0, absolute_coordinates=False),
                         "G90\nG1 X100 Y100 Z50 F3000\nG91")

    def test_z0_script_absolute_and_relative(self):
        self.assertEqual(z0_script(), "G1 Z0 F600")
        self.assertEqual(z0_script(absolute_coordinates=False), "G90\nG1 Z0 F600\nG91")

    def test_center_and_z0_ops_never_merge(self):
        from plugins.ToolheadPolicy import make_center_op, make_jog_op, make_z0_op, push_op
        pending = ()
        pending, _ = push_op(pending, make_center_op(100.0, 100.0, True))
        pending, _ = push_op(pending, make_z0_op(True))
        pending, _ = push_op(pending, make_jog_op("x", 1.0, True))
        self.assertEqual(len(pending), 3)
        self.assertEqual(CENTER_Z_MM, 50.0)


class ToolheadGateTests(unittest.TestCase):
    def test_state_matrix(self):
        self.assertEqual(jog_gate("printing"), "pause-first")
        for state in ("standby", "paused", "complete", "cancelled"):
            self.assertEqual(jog_gate(state), "allowed", state)
        for state in ("", "error", "mystery"):
            self.assertEqual(jog_gate(state), "disabled", state)
        self.assertEqual(jog_gate(None), "disabled")


class ToolheadQueueTests(unittest.TestCase):
    def test_adjacent_same_axis_jogs_merge(self):
        pending = ()
        for _ in range(5):
            pending, status = push_op(pending, make_jog_op("x", 1.0, True))
            self.assertIsNone(status)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].script, "G91\nG1 X5 F3000\nG90")
        self.assertEqual(pending[0].distance, 5.0)

    def test_no_merge_across_axes_or_home(self):
        pending = ()
        for op in (make_jog_op("x", 1.0, True), make_jog_op("y", 2.0, True),
                   make_jog_op("x", 3.0, True), make_home_op("z")):
            pending, _ = push_op(pending, op)
        self.assertEqual([op.script for op in pending],
            ["G91\nG1 X1 F3000\nG90", "G91\nG1 Y2 F3000\nG90",
             "G91\nG1 X3 F3000\nG90", "G28 Z"])

    def test_extrude_ops_merge_at_the_same_speed_only(self):
        pending = ()
        pending, _ = push_op(pending, make_extrude_op(5.0, 300.0, True))
        pending, _ = push_op(pending, make_extrude_op(-2.0, 300.0, True))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].script, "G91\nG1 E3 F300\nG90")
        # A different feedrate must not merge with the queued move.
        pending, _ = push_op(pending, make_extrude_op(2.0, 120.0, True))
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[1].script, "G91\nG1 E2 F120\nG90")
        pending, _ = push_op(pending, make_jog_op("x", 1.0, True))
        self.assertEqual(len(pending), 3)

    def test_merging_respects_relative_mode(self):
        pending = ()
        pending, _ = push_op(pending, make_jog_op("x", 1.0, False), absolute_coordinates=False)
        pending, _ = push_op(pending, make_jog_op("x", 1.0, False), absolute_coordinates=False)
        self.assertEqual(pending[0].script, "G91\nG1 X2 F3000")

    def test_cap_rejects_newest_and_reports(self):
        pending = (make_home_op(),)
        full = False
        for index in range(MAX_PENDING_OPS):
            before = pending
            op = make_jog_op("x" if index % 2 == 0 else "y", 0.1, True)
            pending, status = push_op(pending, op)
            if status == STATUS_QUEUE_FULL:
                full = True
                self.assertIs(pending, before)
                break
        self.assertTrue(full)
        self.assertEqual(len(pending), MAX_PENDING_OPS)

    def test_motors_off_never_merges(self):
        pending = ()
        pending, _ = push_op(pending, make_motors_off_op())
        pending, _ = push_op(pending, make_motors_off_op())
        self.assertEqual(len(pending), 2)

    def test_equal_and_opposite_taps_cancel(self):
        # A queued +25 jog followed by a -25 tap must cancel the pair,
        # never raise out of the Qt slot or leave the +25 to execute.
        pending, status = push_op((), make_jog_op("x", 25.0, True))
        pending, status = push_op(pending, make_jog_op("x", -25.0, True))
        self.assertEqual(pending, ())
        self.assertIsNone(status)
        pending, status = push_op((), make_extrude_op(5.0, 300.0, True))
        pending, status = push_op(pending, make_extrude_op(-5.0, 300.0, True))
        self.assertEqual(pending, ())
        self.assertIsNone(status)

    def test_non_finite_free_text_is_rejected(self):
        # NaN and infinities must never pass validation (the QML free-text
        # fields forward whatever parseFloat produces).
        for distance in ("nan", "NaN", "inf", "-inf", "1e400"):
            self.assertFalse(jog_distance_ok(distance), distance)
            self.assertFalse(extrude_distance_ok(distance), distance)
        self.assertFalse(extrude_speed_ok("nan"))
        self.assertFalse(extrude_speed_ok("inf"))


if __name__ == "__main__":
    unittest.main()
