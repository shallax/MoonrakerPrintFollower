"""Executable preview toolhead scripts contracts."""
from tests import preview_family_support as harness

class ToolheadScriptTests(harness.ToolheadScriptTests):
    def test_a_jog_sandwiches_a_relative_move_and_restores_absolute(self):
        self.assertEqual("G91\nG1 X25 F3000\nG90", harness.jog_script("X", harness.JOG_DISTANCE_DEFAULT))
        self.assertEqual("G91\nG1 Z1 F600", harness.jog_script("z", 1.0, absolute_coordinates=False))
        self.assertEqual("G91\nG1 Y-5 F3000\nG90", harness.jog_script(" Y ", -5.0))

    def test_a_jog_outside_the_bounds_or_off_axis_is_refused(self):
        with self.assertRaises(ValueError):
            harness.jog_script("e", 5.0)
        for distance in (0.0, harness.JOG_DISTANCE_MIN / 2, harness.JOG_DISTANCE_MAX + 1, float("nan")):
            with self.assertRaises(ValueError):
                harness.jog_script("x", distance)

    def test_home_scripts_cover_all_axes_and_one(self):
        self.assertEqual("G28", harness.home_script())
        self.assertEqual("G28 X", harness.home_script(" x "))
        with self.assertRaises(ValueError):
            harness.home_script("e")
        self.assertEqual("M18", harness.motors_off_script())

    def test_absolute_moves_wrap_to_leave_a_relative_printer_relative(self):
        self.assertEqual("G1 X5 Y6 Z50 F3000", harness.center_script(5, 6))
        self.assertEqual("G90\nG1 X5 Y6 Z50 F3000\nG91", harness.center_script(5, 6, absolute_coordinates=False))
        self.assertEqual("G90\nG1 Z0 F600\nG91", harness.z0_script(absolute_coordinates=False))
        self.assertEqual("G1 Z0 F600", harness.z0_script())
        self.assertIn("Z%g" % harness.CENTER_Z_MM, harness.center_script(1, 1))

    def test_extrusion_scripts_cover_both_modes_and_bounds(self):
        self.assertEqual("G91\nG1 E5 F300\nG90", harness.extrude_script(5.0))
        self.assertEqual("G91\nG1 E-5 F1500", harness.extrude_script(-5.0, speed_mm_per_min=1500,
                                                            absolute_coordinates=False))
        with self.assertRaises(ValueError):
            harness.extrude_script(0.05)
        with self.assertRaises(ValueError):
            harness.extrude_script(5.0, speed_mm_per_min=harness.EXTRUDE_SPEED_MAX + 1)

    def test_axis_and_range_validators_reject_junk(self):
        self.assertTrue(harness.axis_ok(" Z "))
        self.assertFalse(harness.axis_ok("e"))
        self.assertFalse(harness.axis_ok(None))
        self.assertTrue(harness.jog_distance_ok(-harness.JOG_DISTANCE_MAX))
        self.assertFalse(harness.jog_distance_ok("far"))
        self.assertFalse(harness.jog_distance_ok(None))
        self.assertTrue(harness.extrude_distance_ok(100.0))
        self.assertFalse(harness.extrude_distance_ok(""))
        self.assertTrue(harness.extrude_speed_ok(30))
        self.assertFalse(harness.extrude_speed_ok("fast"))
        self.assertFalse(harness.extrude_speed_ok(harness.EXTRUDE_SPEED_MAX + 0.5))

    def test_position_mode_text_names_the_mode(self):
        self.assertEqual("Absolute", harness.position_mode_text(True))
        self.assertEqual("Relative", harness.position_mode_text(0))

    def test_the_jog_gate_unlocks_terminated_states_only(self):
        self.assertEqual("pause-first", harness.jog_gate(" PRINTING "))
        for state in ("standby", "paused", "complete", "cancelled", "error"):
            self.assertEqual("allowed", harness.jog_gate(state))
        for state in ("", None, "homing", "offline"):
            self.assertEqual("disabled", harness.jog_gate(state))


class ClampRelativeMoveTests(harness.ClampRelativeMoveTests):
    def test_a_move_into_negative_territory_is_forbidden_entirely(self):
        self.assertEqual(0.0, harness.clamp_relative_move(-5.0, 2.0, minimum=0.0))
        self.assertEqual(0.0, harness.clamp_relative_move(-1.0, 0.5), "unknown floor still floors at zero")
        self.assertEqual(0.0, harness.clamp_relative_move(0.0, 12.0))

    def test_a_move_past_the_maximum_lands_on_the_boundary(self):
        self.assertEqual(3.0, harness.clamp_relative_move(10.0, 7.0, maximum=10.0))
        self.assertEqual(0.0, harness.clamp_relative_move(10.0, 12.0, maximum=10.0), "already past: no move")

    def test_a_move_inside_the_range_passes_through_unchanged(self):
        self.assertEqual(2.5, harness.clamp_relative_move(2.5, 1.0, minimum=0.0, maximum=10.0))
        self.assertEqual(-2.5, harness.clamp_relative_move(-2.5, 9.0, minimum=0.0, maximum=10.0))

    def test_an_inverted_range_is_decided_by_the_floor(self):
        # minimum > maximum never satisfies both clamps at once, so the
        # inverted-range guard at the end of the function cannot return; the
        # floor check decides first and the move is refused.
        self.assertEqual(0.0, harness.clamp_relative_move(1.0, 5.0, minimum=10.0, maximum=None))
        self.assertEqual(0.0, harness.clamp_relative_move(1.0, 5.0, minimum=10.0, maximum=3.0))


class JogQueueTests(harness.JogQueueTests):
    def test_operation_builders_pre_render_their_scripts(self):
        jog = harness.make_jog_op("Z", -1.0, True)
        self.assertEqual(("jog", "z", -1.0, "Jog Z", "G91\nG1 Z-1 F600\nG90"),
                         (jog.kind, jog.axis, jog.distance, jog.label, jog.script))
        self.assertEqual("Home all", harness.make_home_op().label)
        self.assertEqual("G28 Y", harness.make_home_op("y").script)
        self.assertEqual("Home Y", harness.make_home_op("y").label)
        self.assertEqual(("motors-off", "Motors off", "M18"),
                         (harness.make_motors_off_op().kind, harness.make_motors_off_op().label,
                          harness.make_motors_off_op().script))
        center = harness.make_center_op(5.0, 6.0, False)
        self.assertEqual(("center", harness.CENTER_Z_MM, "G90\nG1 X5 Y6 Z50 F3000\nG91"),
                         (center.kind, center.distance, center.script))
        self.assertEqual(("z0", "Z to 0", "G1 Z0 F600"),
                         (harness.make_z0_op(True).kind, harness.make_z0_op(True).label, harness.make_z0_op(True).script))
        self.assertEqual("Retract", harness.make_extrude_op(-5.0, 300, True).label)
        extrude = harness.make_extrude_op(5.0, 300, True)
        self.assertEqual(("extrude", "e", 5.0, 300.0, "Extrude"),
                         (extrude.kind, extrude.axis, extrude.distance, extrude.speed, extrude.label))

    def test_operation_builders_refuse_a_bad_axis(self):
        with self.assertRaises(ValueError):
            harness.make_jog_op("e", 5.0, True)
        with self.assertRaises(ValueError):
            harness.make_home_op("e")

    def test_the_queue_rejects_the_newest_tap_at_the_depth_cap(self):
        op = harness.make_home_op()
        pending = ()
        for _ in range(harness.MAX_PENDING_OPS):
            pending, why = harness.push_op(pending, op)
            self.assertIsNone(why)
        self.assertEqual(harness.MAX_PENDING_OPS, len(pending))
        kept, why = harness.push_op(pending, op)
        self.assertEqual(harness.STATUS_QUEUE_FULL, why)
        self.assertEqual(pending, kept, "already-queued intent is kept")

    def test_equal_and_opposite_moves_are_never_coalesced(self):
        merged, _ = harness.push_op((harness.make_extrude_op(-5.0, 300, True),), harness.make_extrude_op(-5.0, 300, True))
        self.assertEqual(2, len(merged))


