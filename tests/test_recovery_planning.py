# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-13 [52df354] Verify recovery safety paths and release gates
# 2026-10-13 [54a31e5] Implement interrupted print recovery handover

"""Read-only reconstruction at a selected original motion command."""
import hashlib
from dataclasses import replace
import os
import tempfile
import unittest
from pathlib import Path

from mpf.gcode.GCodeIndex import build_index_from_file
from mpf.gcode.IndexView import IndexView
from mpf.application.RecoveryPlanning import RecoveryPlanError, scan_source, suggest_lead_in


class RecoveryPlanningTests(unittest.TestCase):
    def plan(self, data, layer, split):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(layer, split)
            return scan_source(path, boundary)

    def test_preview_contains_only_deposition_before_recorded_progress_even_for_future_resume(self):
        data = b"G90\nM82\nG92 E0\n;LAYER:0\nG1 X1 Y1 Z0.2 F1800\nG1 X2 E1\nG1 X3 E2\nG1 X4 E3\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle: handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 3)
            plan = scan_source(path, boundary, preview_offset=data.index(b"G1 X3"))
            self.assertEqual(plan.preview_segments, ((1., 1., .2, 2., 1., .2),))
            self.assertEqual(plan.state.position, (3., 1., .2))

    def test_preview_curves_follow_arc_geometry(self):
        data = b"G90\nM82\nG92 E0\n;LAYER:0\nG1 X10 Y10 Z1 F1800\nG2 X20 Y10 I5 J0 E2\nG1 X21 E3\n"
        plan = self.plan(data, 0, 2)
        self.assertGreater(len(plan.preview_segments), 5)
        self.assertEqual(plan.preview_segments[0][:3], (10., 10., 1.))
        self.assertEqual(plan.preview_segments[-1][3:], (20., 10., 1.))
        self.assertTrue(any(abs(edge[4] - 10) > 1 for edge in plan.preview_segments))

    def test_preview_has_a_bounded_size_for_large_prints(self):
        data = b"G90\nM83\n;LAYER:0\nG1 X1 Y1 Z1 F1800\n" + b"G1 X2 E1\nG1 X1 E1\n" * 13000
        plan = self.plan(data, 0, 25000)
        self.assertGreater(len(plan.preview_segments), 5000)
        self.assertLessEqual(len(plan.preview_segments), 12000)

    def test_reconstructs_modes_e_resets_feed_and_thermal_targets(self):
        data = (b"G90\nM82\nG92 E0\nM104 S210\nM140 S60\nM106 S128\n"
                b";LAYER:0\nG1 X1 Y2 Z0.2 E0.5 F1800\n"
                b"G92 E0\nM83\nG1 X2 E0.25 F2400\n"
                b"M220 S80\nM221 S95\n"
                b"G1 X3 E0.1\n")
        plan = self.plan(data, 0, 2)
        self.assertEqual(plan.boundary_offset, data.index(b"G1 X3"))
        self.assertEqual(plan.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(plan.state.position, (2.0, 2.0, 0.2))
        self.assertAlmostEqual(plan.state.e, 0.25)
        self.assertFalse(plan.state.absolute_e)
        self.assertEqual(plan.state.feedrate, 2400)
        self.assertEqual((plan.state.speed_percent, plan.state.flow_percent), (80, 95))
        self.assertEqual((plan.state.hotend_target, plan.state.bed_target), (210, 60))
        self.assertEqual(plan.state.fan_speed, 128)

    def test_m82_does_not_override_g91_relative_extrusion(self):
        data = (b"G90\nM83\nG92 E0\n;LAYER:0\nG1 X1 E0.2\n"
                b"G91\nM82\nG1 X1 E0.3\nG90\nG1 X3 E1.0\n")
        plan = self.plan(data, 0, 2)
        self.assertEqual(plan.state.position, (2.0, 0.0, 0.0))
        self.assertAlmostEqual(plan.state.e, 0.5)
        self.assertTrue(plan.state.absolute_e)

    def test_accumulated_e_and_relative_e_at_rewind_boundaries(self):
        absolute = (b"G90\nM82\nG92 E0\n;LAYER:0\n"
                    b"G1 X1 E1.25\nG1 X2 E2.50\nG1 X3 E3.75\n")
        plan = self.plan(absolute, 0, 2)
        self.assertTrue(plan.state.absolute_e)
        self.assertAlmostEqual(plan.state.e, 2.50)
        relative = (b"G90\nM83\nG92 E0\n;LAYER:0\n"
                    b"G1 X1 E1.25\nG1 X2 E1.25\nG1 X3 E1.25\n")
        plan = self.plan(relative, 0, 2)
        self.assertFalse(plan.state.absolute_e)
        self.assertAlmostEqual(plan.state.e, 2.50)

    def test_clearance_scans_printed_geometry_after_earlier_rewind(self):
        data = (b"G90\nM82\nG92 E0\n;LAYER:0\n"
                b"G1 X1 Z0.2 E0.2\nG1 X2 E0.4\n"
                b"G92 E0\nG1 X3 Z12 E0.2\nG1 X4 E0.4\n")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 1)
            plan = scan_source(path, boundary, len(data))
        self.assertEqual(plan.state.position, (1, 0, 0.2))
        self.assertAlmostEqual(plan.state.e, 0.2)
        self.assertEqual(plan.printed_top_z, 12)

    def test_explicit_source_macro_contracts_and_acceleration(self):
        data = (b"PRINT_START BED=60 EXTRUDER=210\nG92 E0\nG0 X1 Y2 Z0.2\nM82\nM204 S700\n"
                b";LAYER:0\nG1 X2 E0.2\nTIMELAPSE_TAKE_FRAME\nG1 X3 E0.4\n")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 1)
            with self.assertRaises(RecoveryPlanError):
                scan_source(path, boundary, len(data))
            plan = scan_source(path, boundary, len(data),
                setup_macros=("PRINT_START",), transparent_macros=("TIMELAPSE_TAKE_FRAME",))
        self.assertEqual(plan.state.position, (2, 2, 0.2))
        self.assertEqual(plan.state.acceleration, 700)
        self.assertEqual((plan.state.hotend_target, plan.state.bed_target), (210, 60))
        self.assertAlmostEqual(plan.state.e, 0.2)

    def test_latest_temperature_before_restart_overrides_custom_setup_arguments(self):
        data = (b"PRINT_START TOOL=205 PLATE=55\nG92 E0\nG0 X1 Y2 Z0.2\n"
                b"M104 S215\nM140 S65\n;LAYER:0\nG1 X2 E0.2\n"
                b"M109 S225\nM190 S70\nG1 X3 E0.4\n")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            before_change = scan_source(path, view.recovery_boundary(0, 0),
                setup_macros=("PRINT_START",), nozzle_parameter="TOOL", bed_parameter="PLATE")
            after_change = scan_source(path, view.recovery_boundary(0, 1),
                setup_macros=("PRINT_START",), nozzle_parameter="TOOL", bed_parameter="PLATE")
        self.assertEqual((before_change.state.hotend_target, before_change.state.bed_target),
                         (215, 65))
        self.assertEqual((after_change.state.hotend_target, after_change.state.bed_target),
                         (225, 70))

    def test_setup_macro_requires_explicit_position_and_e_reset(self):
        data = b"PRINT_START\n;LAYER:0\nG1 X1 Y2 E0.2\nG1 X2 E0.4\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 1)
            with self.assertRaisesRegex(RecoveryPlanError, "full absolute XYZ move and G92 E"):
                scan_source(path, boundary, setup_macros=("PRINT_START",))

    def test_refuses_unmodelled_state_and_changed_command(self):
        for command in (b"PRINT_START", b"SET_GCODE_OFFSET Z=0.2",
                        b"G92 X0", b"G20", b"T1", b"G10"):
            with self.subTest(command=command):
                data = b";LAYER:0\nG1 X1 E0.1\n" + command + b"\nG1 X2 E0.2\n"
                with self.assertRaises(RecoveryPlanError):
                    self.plan(data, 0, 1)

    def test_refuses_misaligned_or_mutated_source(self):
        data = b";LAYER:0\nG1 X1 E0.1\nG1 X2 E0.2\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 1)
            with open(path, "r+b") as handle:
                handle.seek(boundary.next_motion_offset)
                handle.write(b"M117 ")
            with self.assertRaises(RecoveryPlanError):
                scan_source(path, boundary)

    def test_refuses_replay_across_another_pause(self):
        data = b";LAYER:0\nG1 X1 E0.1\nG1 X2 E0.2\nPAUSE\nG1 X3 E0.3\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 1)
            with self.assertRaisesRegex(RecoveryPlanError, "another pause"):
                scan_source(path, boundary, len(data))

    def test_lead_in_rewinds_contiguous_extrusion_without_crossing_travel(self):
        data = (b";LAYER:0\nG0 X0 Y0\nG1 X3 E0.1\nG1 X6 E0.2\n"
                b"G0 X10\nG1 X13 E0.3\n")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            suspect = view.recovery_boundary(0, 3)
            suggestion = suggest_lead_in(view, suspect, 5.0)
            self.assertEqual(suggestion.boundary.next_motion_offset, data.index(b"G1 X3"))
            self.assertEqual(suggestion.replayed_motions, 2)
            self.assertAlmostEqual(suggestion.extrusion_path_mm, 6.0)
            limited = suggest_lead_in(view, suspect, 7.0)
            self.assertTrue(limited.limited_by_seam)
            self.assertAlmostEqual(limited.extrusion_path_mm, 6.0)
            self.assertEqual(suggest_lead_in(view, suspect, 5.0, minimum_moves=1).replayed_motions, 1)
            self.assertIsNone(suggest_lead_in(view, view.recovery_boundary(0, 5), 5.0))

    def test_invalid_modal_commands_refuse_before_a_rewind_boundary(self):
        for command in (b"G1 F0", b"M220 S0", b"M221 S-2", b"M204",
                        b"M106 P1", b"M106 S300", b"M104", b"M190 S-1"):
            with self.subTest(command=command):
                data = b";LAYER:0\nG1 X1 E0.1\n" + command + b"\nG1 X2 E0.2\n"
                with self.assertRaises(RecoveryPlanError):
                    self.plan(data, 0, 2 if command.startswith(b"G1") else 1)

    def test_boundary_offsets_and_macro_contracts_are_checked(self):
        data = b";LAYER:0\nG1 X1 E0.1\nG1 X2 E0.2\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            boundary = view.recovery_boundary(0, 1)
            for selected, observed, kwargs in (
                (None, None, {}),
                (replace(boundary, next_motion_offset=len(data)), None, {}),
                (boundary, -1, {}),
                (boundary, None, {"nozzle_parameter": "invalid-name"}),
                (boundary, None, {"setup_macros": ("PRINT_START",),
                                  "transparent_macros": ("PRINT_START",)}),
                (replace(boundary, position=(99, 0, 0)), None, {}),
            ):
                with self.subTest(selected=selected, observed=observed, kwargs=kwargs):
                    with self.assertRaises(RecoveryPlanError):
                        scan_source(path, selected, observed, **kwargs)

    def test_rewind_cannot_cross_setup_macro_after_printed_geometry(self):
        data = (b";LAYER:0\nG1 X1 Y1 Z0.2 E0.1\n"
                b"PRINT_START BED=60 EXTRUDER=210\nG1 X2 E0.2\n")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            with self.assertRaisesRegex(RecoveryPlanError, "setup macro cannot be crossed"):
                scan_source(path, view.recovery_boundary(0, 1), len(data),
                            setup_macros=("PRINT_START",))

    def test_unit_and_tool_selection_are_reconstructed_without_changing_position(self):
        data = b"G21\nT0\n;LAYER:0\nG1 X1 E0.1\nG1 X2 E0.2\n"
        plan = self.plan(data, 0, 1)
        self.assertEqual(plan.state.position, (1.0, 0.0, 0.0))
        invalid = b";LAYER:0\nG1 X1 E0.1\n@UNKNOWN\nG1 X2 E0.2\n"
        with self.assertRaises(RecoveryPlanError):
            self.plan(invalid, 0, 1)

    def test_invalid_lead_in_distance_is_not_silently_accepted(self):
        for value in (None, "bad", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(RecoveryPlanError):
                suggest_lead_in(None, None, value)

    def test_scanner_rejects_oversized_and_unparseable_commands(self):
        from types import SimpleNamespace
        from mpf.gcode.IndexLimits import _MAX_LINE_BYTES
        for prefix in (b"@UNKNOWN\n", b";" + b"x" * (_MAX_LINE_BYTES + 1) + b"\n"):
            with self.subTest(prefix=prefix[:12]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.gcode"
                path.write_bytes(prefix + b"G1 X1 E1\n")
                with self.assertRaises(RecoveryPlanError):
                    scan_source(path, SimpleNamespace(next_motion_offset=len(prefix), position=(0, 0, 0)))

    def test_no_lead_in_exists_before_first_motion(self):
        data = b";LAYER:0\nG1 X1 E0.1\nG1 X2 E0.2\n"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            with open(path, "wb") as handle:
                handle.write(data)
            view = IndexView(("print.gcode", len(data), 1), build_index_from_file(path))
            self.assertIsNone(suggest_lead_in(view, view.recovery_boundary(0, 0), 5.0))


if __name__ == "__main__":
    unittest.main()
