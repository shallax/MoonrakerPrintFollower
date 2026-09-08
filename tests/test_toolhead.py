"""Pure tests for manual toolhead control policy: scripts, gates, queue."""
from __future__ import annotations

import unittest

from plugins.ToolheadPolicy import (
    EXTRUDE_STEP_MM,
    MAX_PENDING_OPS,
    STATUS_QUEUE_FULL,
    axis_ok,
    extrude_script,
    home_script,
    homed_text,
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
        self.assertEqual(extrude_script(EXTRUDE_STEP_MM), "G91\nG1 E5 F300\nG90")
        self.assertEqual(extrude_script(-5.0), "G91\nG1 E-5 F300\nG90")
        self.assertEqual(extrude_script(2.5, absolute_coordinates=False), "G91\nG1 E2.5 F300")
        with self.assertRaises(ValueError):
            extrude_script(15.0)

    def test_readout_helpers(self):
        self.assertEqual(homed_text("xyz"), "X Y Z")
        self.assertEqual(homed_text("xz"), "X Z")
        self.assertEqual(homed_text(""), "—")
        self.assertEqual(homed_text(None), "—")
        self.assertEqual(position_mode_text(True), "Absolute")
        self.assertEqual(position_mode_text(False), "Relative")
        self.assertTrue(axis_ok("y"))
        self.assertFalse(axis_ok("e"))


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

    def test_extrude_ops_merge_but_never_cross_kind(self):
        pending = ()
        pending, _ = push_op(pending, make_extrude_op(5.0, True))
        pending, _ = push_op(pending, make_extrude_op(-2.0, True))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].script, "G91\nG1 E3 F300\nG90")
        pending, _ = push_op(pending, make_jog_op("x", 1.0, True))
        self.assertEqual(len(pending), 2)

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


if __name__ == "__main__":
    unittest.main()
