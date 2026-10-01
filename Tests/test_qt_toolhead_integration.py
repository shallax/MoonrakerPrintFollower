"""Executable qt toolhead integration contracts."""
from tests import qt_integration_support as harness

class ToolheadControllerTests(harness.ToolheadControllerTests):
    def test_toolhead_guard_speeds_the_poll_floor_while_moving(self):
        self.data.set_state("paused")
        self.controller.set_distance(1)
        self.controller.jog("x", 1)
        self.assertEqual(self.data.guard_calls[-1], True)  # moving: fast floor
        self.commands.complete()
        self.assertEqual(self.data.guard_calls[-1], True)  # cooldown still holds it
        self.controller._guard_cooldown.timeout.emit()
        self.assertEqual(self.data.guard_calls[-1], False)  # settled: release
        # A reset releases the guard immediately.
        self.controller.jog("x", 1)
        self.assertEqual(self.data.guard_calls[-1], True)
        self.controller._reset()
        self.assertEqual(self.data.guard_calls[-1], False)

    def test_paused_jogs_send_immediately_and_drain_in_order(self):
        self.data.set_state("paused")
        self.assertTrue(self.controller.values["jogEnabled"])
        self.assertEqual(self.controller.values["homedAxes"], "xyz")
        self.assertEqual(self.controller.values["positionMode"], "Absolute")
        # Defaults: 25 mm jogs, 5 mm extrusion at 5 mm/s.
        self.assertEqual(self.controller.values["jogDistance"], 25.0)
        self.assertEqual(self.controller.values["extrudeDistance"], 5.0)
        self.assertEqual(self.controller.values["extrudeSpeed"], 300.0)
        self.controller.set_distance(1)
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90"])
        self.controller.jog("y", 1)
        self.commands.complete()
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90", "G91\nG1 Y1 F3000\nG90"])

    def test_printing_jog_pauses_first_and_never_double_sends(self):
        self.data.set_state("printing")
        self.assertFalse(self.controller.values["jogEnabled"])  # UI gate: pause first
        self.controller.set_distance(1)
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts(), [])
        self.assertEqual(len(self.pauses()), 1)
        self.assertIn("Waiting for the printer to pause", self.controller.values["jogStatus"])
        # The pause is confirmed (event) before the fresh state arrives; the
        # flags stay armed so no redundant Pause is sent against the stale
        # "printing" state.
        self.commands.busy = False
        self.commands.changed.emit()
        self.data.commandChanged.emit({"name": "Pause", "outcome": "confirmed", "terminal": True, "detail": "paused"})
        self.assertEqual(len(self.pauses()), 1)
        # The fresh paused state drains the queue.
        self.data.set_state("paused")
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90"])

    def test_jogs_clamp_to_the_axis_limits(self):
        self.data.set_state("paused")
        self.controller.set_distance(25)
        # 10 - 25 would cross the minimum: the move is forbidden outright.
        self.controller.jog("x", -1)
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.controller._pending, ())
        # 195 + 25 would cross the maximum: the jog lands exactly on 200.
        self.data.snapshot.core["motion_report"]["live_position"][0] = 195.0
        self.data.changed.emit()
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts(), ["G91\nG1 X5 F3000\nG90"])
        self.commands.complete()
        # At the boundary the tap is a no-op.
        self.data.snapshot.core["motion_report"]["live_position"][0] = 200.0
        self.data.changed.emit()
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts()[-1], "G91\nG1 X5 F3000\nG90")

    def test_merged_jog_tails_never_overshoot_the_axis_limits(self):
        self.data.set_state("paused")
        self.controller.set_distance(25)
        # Two rapid Z- taps at z=10 would cross the minimum: forbidden.
        self.controller.jog("z", -1)
        self.controller.jog("z", -1)
        self.assertEqual(self.controller._pending, ())
        # On the maximum side the FIRST tap clamps to the boundary,
        # and the client-side Z estimate (the live report:
        # stale-poll clamping let rapid taps overshoot) makes the
        # second tap a no-op.
        self.data.snapshot.core["motion_report"]["live_position"][2] = 195.0
        self.data.changed.emit()
        self.controller.jog("z", 1)
        self.assertIn("G91\nG1 Z5 F600\nG90", self.scripts())
        self.controller.jog("z", 1)
        self.assertEqual(len(self.scripts()), 1)  # at the boundary: no-op
        self.assertEqual(self.controller._axis_estimate["z"], 200.0)
        self.commands.complete()
        # A fresh poll at the boundary keeps the tap a no-op.
        self.data.snapshot.core["motion_report"]["live_position"][2] = 200.0
        self.data.changed.emit()
        self.controller.jog("z", 1)
        self.assertEqual(self.controller._pending, ())

    def test_merged_x_and_y_tails_never_overshoot_the_axis_limits(self):
        # The stale-poll overshoot, X- and Y-flavoured (the Z fix
        # generalised): rapid taps at the axis MAXIMUM used to clamp
        # each against the stale poll, so the merged queue walked the
        # head past the limit — the client-side projection now
        # advances with every queued move on every axis, and the
        # second tap at the boundary is a no-op.
        self.data.set_state("paused")
        self.controller.set_distance(25)
        for axis, index in (("x", 0), ("y", 1)):
            self.controller._reset()
            self.data.snapshot.core["motion_report"]["live_position"][index] = 195.0
            self.data.changed.emit()
            self.controller.jog(axis, 1)
            self.assertIn("G91\nG1 %s5 F3000\nG90" % axis.upper(), self.scripts())
            self.assertEqual(self.controller._axis_estimate[axis], 200.0)
            before = len(self.scripts())
            self.controller.jog(axis, 1)
            self.assertEqual(len(self.scripts()), before,
                             "the %s+ tail overshot the maximum" % axis)
            self.commands.complete()
            # A fresh poll at the boundary keeps the tap a no-op.
            self.data.snapshot.core["motion_report"]["live_position"][index] = 200.0
            self.data.changed.emit()
            self.controller.jog(axis, 1)
            self.assertEqual(self.controller._pending, (),
                             "the %s+ tap fired at the boundary" % axis)

    def test_z_floor_is_zero_even_with_a_negative_configured_minimum(self):
        # The live ruling: the jog pad must never send the
        # head below 0.00 Z — whatever position_min says (many
        # printers configure a negative Z minimum for probe travel).
        self.data.set_state("paused")
        self.controller.set_distance(1)
        self.data.snapshot.auxiliary["toolhead"]["axis_minimum"] = [0, 0, -5]
        self.data.snapshot.core["motion_report"]["live_position"][2] = 0.1
        self.data.changed.emit()
        self.controller.jog("z", -1)
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.controller._pending, ())

    def test_rejected_z_nudge_reports_and_notes_once_per_burst(self):
        # A live request: a rejected nudge must explain
        # itself in the jog status AND the console — the note once
        # per burst, so a flurry of taps cannot flood the feed.
        self.data.set_state("paused")
        self.controller.set_distance(1)
        notes = []
        self.controller.rejectedNote.connect(notes.append)
        self.data.snapshot.core["motion_report"]["live_position"][2] = 0.1
        self.data.changed.emit()
        self.controller.jog("z", -1)
        self.assertEqual(self.controller._status,
                         "Z nudge rejected — the head would go below 0.00 Z")
        self.assertEqual(len(notes), 1)
        self.controller.jog("z", -1)  # same burst: no second note
        self.assertEqual(len(notes), 1)
        # An accepted move re-arms the note for the next burst. The
        # polls must report the head where it is: a poll still reading
        # the pre-command level would be the accepted move's own
        # reflection lagging (the dispatch seam), not the head the
        # nudge is measured against.
        self.controller.jog("z", 1)
        self.data.snapshot.core["motion_report"]["live_position"][2] = 1.1
        self.data.changed.emit()  # the head reported at the moved level
        self.data.snapshot.core["motion_report"]["live_position"][2] = 0.1
        self.data.changed.emit()  # ...and back one nudge above the floor
        self.controller.jog("z", -1)
        self.assertEqual(len(notes), 2)

    def test_center_and_z0_moves(self):
        self.data.set_state("paused")
        self.controller.center_toolhead()
        self.assertEqual(self.scripts(), ["G1 X100 Y100 Z50 F3000"])
        self.commands.complete()
        self.controller.z_to_zero()
        self.assertEqual(self.scripts()[-1], "G1 Z0 F600")
        self.commands.complete()
        # Without axis data the centre move is a no-op.
        self.data.snapshot = harness.SimpleNamespace(
            core={"print_stats": {"state": "paused"},
                  "gcode_move": {"absolute_coordinates": True}},
            auxiliary={"toolhead": {"homed_axes": "xyz"}})
        self.data.changed.emit()
        self.controller.center_toolhead()
        self.assertEqual(self.scripts()[-1], "G1 Z0 F600")

    def test_moves_toward_the_minimum_are_forbidden_without_position_data(self):
        self.data.set_state("paused")
        self.controller.set_distance(25)
        self.data.snapshot = harness.SimpleNamespace(
            core={"print_stats": {"state": "paused"},
                  "gcode_move": {"absolute_coordinates": True}},
            auxiliary={"toolhead": {"homed_axes": "xyz"}})
        self.data.changed.emit()
        self.controller.jog("z", -1)
        self.assertEqual(self.controller._pending, ())
        self.controller.jog("z", 1)  # away from the minimum is safe
        self.assertEqual(self.scripts(), ["G91\nG1 Z25 F600\nG90"])

    def test_custom_distance_and_extrusion_controls(self):
        self.data.set_state("paused")
        self.controller.set_distance(42.5)
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts(), ["G91\nG1 X42.5 F3000\nG90"])
        self.commands.complete()
        # Out-of-range distances are ignored.
        self.controller.set_distance(0.001)
        self.controller.set_distance(500)
        self.assertEqual(self.controller.values["jogDistance"], 42.5)
        self.controller.set_extrude_distance(10)
        self.controller.set_extrude_speed(120)
        self.controller.extrude(1)
        self.assertEqual(self.scripts()[-1], "G91\nG1 E10 F120\nG90")
        self.commands.complete()
        self.controller.extrude(-1)
        self.assertEqual(self.scripts()[-1], "G91\nG1 E-10 F120\nG90")
        self.commands.complete()
        self.assertEqual(self.controller.values["extrudeDistance"], 10.0)
        self.assertEqual(self.controller.values["extrudeSpeed"], 120.0)

    def test_refused_pause_send_drops_the_queue_without_re_sending(self):
        # A refused Pause completes synchronously with a terminal failed
        # event inside send(); the re-entrant pump must not re-send it.
        self.data.set_state("printing")
        self.commands.fail_next = True
        self.controller.jog("x", 1)
        self.assertEqual(len(self.pauses()), 0)
        self.assertEqual(self.controller._pending, ())
        self.assertIn("did not pause", self.controller.values["jogStatus"])

    def test_pause_timeout_drops_the_queue(self):
        controller = self.qt.load("ToolheadController")
        with harness.patch.object(controller, "PAUSE_WAIT_TIMEOUT_S", 0.05):
            timed = controller.ToolheadController(self.data, self.commands)
            self.addCleanup(timed.close)
            self.data.set_state("printing")
            timed.jog("x", 1)
            self.qt.events(200)
        self.assertEqual(self.scripts(), [])
        self.assertIn("did not pause", timed.values["jogStatus"])

    def test_resume_mid_drain_drops_remaining_moves(self):
        self.data.set_state("printing")
        self.controller.set_distance(1)
        self.controller.jog("x", 1)
        self.controller.jog("y", 1)
        self.data.set_state("paused")
        # The tracked pause reaches terminal confirmation, clearing busy.
        self.commands.busy = False
        self.commands.changed.emit()
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90"])
        # The print resumes before the first move completes; the remaining
        # move must never run mid-print.
        self.data.set_state("printing")
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90"])
        self.assertIn("resumed", self.controller.values["jogStatus"])

    def test_taps_queue_separately_while_a_move_is_in_flight(self):
        self.data.set_state("paused")
        self.controller.set_distance(1)
        self.controller.jog("x", 1)
        self.controller.jog("x", 1)
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90"])
        # Each queued move drains on its own completion cycle (the
        # no-coalescing ruling: the queue holds separate ops).
        self.commands.complete()
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90", "G91\nG1 X1 F3000\nG90"])
        self.commands.complete()
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90", "G91\nG1 X1 F3000\nG90", "G91\nG1 X1 F3000\nG90"])
        self.assertEqual(self.controller.values["jogStatus"], "")


