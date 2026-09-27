"""Executable toolhead coverage contracts."""
from tests import control_owner_support as harness

class ToolheadCoverageTests(harness.ToolheadCoverageTests):
    def test_distance_presets_reject_everything_out_of_range(self):
        controller, _, _ = self._make()
        for value in ("wide", 0, -5, 0.001, 500):
            controller.set_distance(value)
            self.assertEqual(controller.values["jogDistance"], 25.0)
        controller.set_distance("1.5")
        self.assertEqual(controller.values["jogDistance"], 1.5)
        for value in ("wide", 0, 0.01, 500):
            controller.set_extrude_distance(value)
            self.assertEqual(controller.values["extrudeDistance"], 5.0)
        controller.set_extrude_distance(10)
        self.assertEqual(controller.values["extrudeDistance"], 10.0)
        for value in ("fast", 0, 5000):
            controller.set_extrude_speed(value)
            self.assertEqual(controller.values["extrudeSpeed"], 300.0)
        controller.set_extrude_speed(120)
        self.assertEqual(controller.values["extrudeSpeed"], 120.0)

    def test_a_jog_is_dropped_when_under_a_minimum_move_of_travel_is_left(self):
        # The clamp can land under the smallest legal move; that is not a
        # jog, so nothing is sent.
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0),
                                             maximum=(0.005, 200, 200))
        controller.jog("x", 1)
        self.assertEqual(commands.sent, [])

    def test_a_configured_negative_z_floor_still_refuses_the_nudge(self):
        # Printers configure a negative Z minimum for probe travel; the
        # pad must never send the head below 0.00.
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0),
                                             minimum=(0, 0, -5))
        notes = []
        controller.rejectedNote.connect(notes.append)
        controller.jog("z", -1)
        self.assertEqual(commands.sent, [])
        self.assertEqual(len(notes), 1)
        controller.jog("z", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 Z25 F600\nG90"])

    def test_a_refused_send_leaves_the_move_queued(self):
        controller, _, commands = self._make(state="paused")
        commands.refuse = True
        controller.jog("x", 1)
        self.assertEqual(commands.sent, [])
        self.assertEqual(len(controller._pending), 1)
        commands.refuse = False
        commands.changed.emit()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_nested_pump_is_dropped(self):
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        commands.busy = False
        # A dispatch already running must not re-enter and double-send.
        controller._pumping = True
        controller._pump()
        self.assertEqual(commands.sent, [])
        controller._pumping = False
        controller._pump()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_jog_with_a_bad_axis_or_direction_is_dropped(self):
        controller, _, commands = self._make()
        controller.jog("w", 1)
        controller.jog("x", 2)
        controller.jog("x", "sideways")
        controller.jog("x", None)
        self.assertEqual(commands.sent, [])

    def test_a_z_nudge_into_the_floor_is_reported_once_per_burst(self):
        controller, data, commands = self._make(live=(10.0, 10.0, 0.0, 0.0))
        notes = []
        controller.rejectedNote.connect(notes.append)
        controller.jog("z", -1)
        controller.jog("z", -1)
        self.assertEqual(notes, ["Z nudge rejected — the head would go below 0.00 Z."])
        self.assertEqual(controller.values["jogStatus"],
                         "Z nudge rejected — the head would go below 0.00 Z")
        self.assertEqual(commands.sent, [])
        # A successful Z move re-arms the note for the next burst — once
        # the poll reports the moved level, so the head is measured where
        # it really is.
        controller.set_distance(5)
        controller.jog("z", 1)
        commands.complete()
        data.set_state("paused", live=(10.0, 10.0, 5.0, 0.0))
        self.assertAlmostEqual(controller._axis_estimate["z"], 5.0)
        controller.jog("z", -1)  # lands exactly on the floor: allowed
        controller.jog("z", -1)  # below it: the note fires again
        self.assertEqual(len(notes), 2)

    def test_a_configured_axis_floor_forbids_the_negative_side(self):
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0))
        controller.jog("x", -1)
        self.assertEqual(commands.sent, [])
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_jog_without_position_data_refuses_the_negative_side(self):
        controller, data, commands = self._make()
        data.snapshot = harness.SimpleNamespace(core={}, auxiliary={})
        data.changed.emit()
        controller.jog("y", -1)
        controller.jog("y", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 Y25 F3000\nG90"])

    def test_the_queue_re_clamps_a_merged_tail(self):
        # The documented overshoot: a tail whose clamp at queue time no
        # longer holds against the re-read position is rewritten, and a
        # refused tail is dropped with it.
        from plugins.ToolheadPolicy import make_jog_op, make_motors_off_op
        controller, _, _ = self._make(live=(10.0, 10.0, 10.0, 0.0))
        clamped = controller._clamp_tail((make_jog_op("x", 250.0, True),))
        self.assertEqual([op.distance for op in clamped], [190.0])
        pending = (make_jog_op("x", 190.0, True), make_jog_op("x", -50.0, True))
        self.assertEqual(controller._clamp_tail(pending), pending[:1])
        # An in-range tail and a non-jog tail are both left alone.
        pending = (make_jog_op("x", 100.0, True),)
        self.assertEqual(controller._clamp_tail(pending), pending)
        home_tail = (make_motors_off_op(),)
        self.assertEqual(controller._clamp_tail(home_tail), home_tail)
        self.assertEqual(controller._clamp_tail(()), ())

    def test_the_z_projection_advances_with_every_accepted_move(self):
        controller, data, commands = self._make(live=(0.0, 0.0, 10.0, 0.0))
        commands.busy = True
        controller.set_distance(5)
        controller.jog("z", 1)
        self.assertAlmostEqual(controller._axis_estimate["z"], 15.0)
        controller.jog("z", 1)
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        # While a Z move is queued the poll never re-syncs the estimate.
        data.set_state("paused", live=(0.0, 0.0, 10.0, 0.0))
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        commands.complete()
        commands.complete()
        # Both moves have left the queue, but the poll still reads the
        # pre-command level: the head has not been reported at 20 yet, so
        # the projection is unreconciled, not stale — dropping it here
        # would re-arm the estimate for the next tap (the dispatch seam).
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        self.assertAlmostEqual(controller._axis_up_pending["z"], 10.0)
        # The poll reporting the commanded level is the head arriving:
        # adopt it and lift the owed reflection.
        data.set_state("paused", live=(0.0, 0.0, 20.0, 0.0))
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        self.assertAlmostEqual(controller._axis_up_pending["z"], 0.0)

    def test_a_stale_poll_after_dispatch_keeps_the_upward_projection(self):
        # Repeated +5 jogs from X=195 with the maximum at 200. Each send
        # frees the lane while the poll still reads 195 — the level the
        # move started from. Adopting it re-armed the projection, so one
        # command left the plugin per tap and the burst walked the head
        # past the maximum.
        controller, data, commands = self._make(live=(195.0, 10.0, 10.0, 0.0),
                                                maximum=(200, 200, 200))
        controller.set_distance(5)
        for _ in range(3):
            controller.jog("x", 1)
            commands.complete()  # the send frees the lane
            data.set_state("paused", live=(195.0, 10.0, 10.0, 0.0),
                           maximum=(200, 200, 200))  # the delayed poll
        self.assertEqual(self._scripts(commands), ["G91\nG1 X5 F3000\nG90"])
        self.assertAlmostEqual(controller._axis_estimate["x"], 200.0)

    def test_the_upward_projection_holds_through_the_queue_drain(self):
        # Two legal +5 taps from X=190 queue behind a held lane and cover
        # the 200 maximum between them; the third is refused outright.
        # The polls that land while the queue drains must not re-open the
        # boundary, and a tap in the other direction still reads the
        # projection rather than the stale poll.
        controller, data, commands = self._make(live=(190.0, 10.0, 10.0, 0.0),
                                                maximum=(200, 200, 200))
        controller.set_distance(5)
        commands.busy = True  # hold the lane: both taps queue
        controller.jog("x", 1)
        controller.jog("x", 1)
        controller.jog("x", 1)  # no headroom is left: nothing is queued
        self.assertAlmostEqual(controller._axis_estimate["x"], 200.0)
        commands.busy = False
        commands.changed.emit()  # the lane clears: the queue starts draining
        self.assertEqual(len(self._scripts(commands)), 1)
        data.set_state("paused", live=(190.0, 10.0, 10.0, 0.0),
                       maximum=(200, 200, 200))
        commands.complete()  # the last queued move goes out
        self.assertEqual(len(self._scripts(commands)), 2)
        self.assertEqual(self._scripts(commands)[-1], "G91\nG1 X5 F3000\nG90")
        # The queue is drained and every poll so far is pre-move: the
        # boundary tap stays refused.
        data.set_state("paused", live=(190.0, 10.0, 10.0, 0.0),
                       maximum=(200, 200, 200))
        controller.jog("x", 1)
        self.assertEqual(len(self._scripts(commands)), 2)
        # The opposite direction still measures against the projection
        # (200), not the stale poll (190).
        commands.complete()  # the lane clears for the next move
        controller.jog("x", -1)
        self.assertEqual(self._scripts(commands)[-1], "G91\nG1 X-5 F3000\nG90")

    def test_a_poll_below_the_pre_command_level_still_adopts(self):
        # The reconciliation's other edge: an upward move's own reflection
        # lags at the pre-command level, but a poll that drops BELOW it is
        # the head genuinely moving down (an external macro, a home) and
        # must be adopted — the projection may not freeze above the truth.
        controller, data, _ = self._make(live=(10.0, 10.0, 10.0, 0.0))
        controller.set_distance(5)
        controller.jog("x", 1)  # projection 15
        self.assertAlmostEqual(controller._axis_estimate["x"], 15.0)
        data.set_state("paused", live=(10.0, 10.0, 10.0, 0.0))  # mid-flight
        self.assertAlmostEqual(controller._axis_estimate["x"], 15.0)
        data.set_state("paused", live=(4.0, 10.0, 10.0, 0.0))  # below the start
        self.assertAlmostEqual(controller._axis_estimate["x"], 4.0)

    def test_polled_z_prefers_the_live_motion_report(self):
        controller, data, _ = self._make()
        self.assertAlmostEqual(controller._polled_axis("z"), 10.0)
        data.snapshot = harness.SimpleNamespace(
            core={"motion_report": {"live_position": [0, 0, "high"]},
                  "gcode_move": {"gcode_position": [0, 0, 3.5]}}, auxiliary={})
        self.assertAlmostEqual(controller._polled_axis("z"), 3.5)
        data.snapshot = harness.SimpleNamespace(
            core={"motion_report": {"live_position": [0, 0]},
                  "gcode_move": {"gcode_position": [0, 0, "north"]}}, auxiliary={})
        self.assertIsNone(controller._polled_axis("z"))
        data.snapshot = harness.SimpleNamespace(
            core={"motion_report": {"live_position": [0, 0]},
                  "gcode_move": {"gcode_position": []}}, auxiliary={})
        self.assertIsNone(controller._polled_axis("z"))

    def test_a_bad_axis_limit_abandons_the_clamp(self):
        controller, _, commands = self._make(live=(10.0, 10.0, 10.0, 0.0),
                                             minimum=("low", 0, 0))
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_home_centre_and_park_moves(self):
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0),
                                             minimum=(0, 0, 0), maximum=(200, 100, 200))
        controller.home()
        commands.complete()
        controller.home("y")
        commands.complete()
        controller.home("nope")
        controller.motors_off()
        commands.complete()
        controller.center_toolhead()
        commands.complete()
        controller.z_to_zero()
        commands.complete()
        scripts = self._scripts(commands)
        self.assertEqual(scripts, ["G28", "G28 Y", "M18", "G1 X100 Y50 Z50 F3000",
                                   "G1 Z0 F600"])

    def test_centre_is_refused_without_usable_axis_limits(self):
        controller, data, commands = self._make()
        data.snapshot = harness.SimpleNamespace(core={}, auxiliary={"toolhead": {"axis_minimum": [0]}})
        controller.center_toolhead()
        data.snapshot = harness.SimpleNamespace(
            core={}, auxiliary={"toolhead": {"axis_minimum": [0, "north"],
                                             "axis_maximum": [200, 200]}})
        controller.center_toolhead()
        self.assertEqual(commands.sent, [])

    def test_extrude_edges(self):
        controller, _, commands = self._make()
        controller.extrude(0)
        controller.extrude("sideways")
        controller.extrude(-1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 E-5 F300\nG90"])

    def test_the_absolute_toggle_reaches_the_printer_and_holds_the_readout(self):
        controller, data, commands = self._make()
        controller.set_absolute(False)
        self.assertEqual(controller.values["positionMode"], "Relative")
        self.assertEqual(commands.sent[-1], ("Relative mode", "printer/gcode/script",
                                             {"script": "G91"}))
        # The poll still reports absolute: the latch holds the user's choice.
        data.set_state("paused")
        self.assertEqual(controller.values["positionMode"], "Relative")
        # Once the printer adopts it, the next poll releases the latch.
        data.snapshot.core["gcode_move"]["absolute_coordinates"] = False
        data.changed.emit()
        self.assertIsNone(controller._mode_latch)
        commands.complete()
        controller.set_absolute(True)
        self.assertEqual(commands.sent[-1][2], {"script": "G90"})
        self.assertEqual(controller.values["positionMode"], "Absolute")

    def test_an_expired_mode_latch_stops_holding(self):
        controller, data, _ = self._make()
        controller.set_absolute(False)
        controller._mode_latch = harness.time.monotonic() - 1.0
        data.set_state("paused")  # the poll reports absolute again
        self.assertEqual(controller.values["positionMode"], "Absolute")
        self.assertIsNone(controller._mode_latch)

    def test_a_printing_jog_waits_for_the_pause_it_requested(self):
        controller, data, commands = self._make(state="printing")
        self.assertFalse(controller.values["jogEnabled"])
        controller.jog("x", 1)
        self.assertEqual(controller.values["jogStatus"], "Waiting for the printer to pause…")
        self.assertEqual([path for _, path, _ in commands.sent], ["printer/print/pause"])
        self.assertTrue(controller._pause_waiting)
        self.assertTrue(controller._pause_in_flight)
        self.assertTrue(controller._deadline.isActive())
        # A second tap must not re-send the Pause.
        data.changed.emit()
        self.assertEqual(len(commands.sent), 1)
        # A confirmed pause stops the deadline and moves once the state lands.
        controller._command_changed({"name": "Pause", "terminal": True, "outcome": "confirmed"})
        self.assertFalse(controller._deadline.isActive())
        self.assertEqual(controller.values["jogStatus"], "Printer paused — moving now.")
        commands.complete()
        data.set_state("paused")
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_refused_pause_drops_the_queue_and_says_so(self):
        controller, data, _ = self._make(state="printing")
        controller.jog("x", 1)
        data.commandChanged.emit({"name": "Pause", "terminal": True, "outcome": "failed"})
        self.assertEqual(controller.values["jogStatus"],
                         "The printer did not pause — queued moves were cancelled.")
        self.assertEqual(controller._pending, ())
        self.assertFalse(controller._pause_waiting)
        self.assertFalse(controller._deadline.isActive())

    def test_non_pause_and_non_terminal_command_events_are_ignored(self):
        controller, data, _ = self._make(state="printing")
        controller.jog("x", 1)
        data.commandChanged.emit({"name": "Extrude", "terminal": True, "outcome": "failed"})
        self.assertTrue(controller._pause_waiting)
        data.commandChanged.emit({"name": "Pause", "terminal": False})
        self.assertTrue(controller._pause_waiting)

    def test_a_resume_mid_drain_cancels_the_rest(self):
        controller, data, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        self.assertEqual(len(controller._pending), 1)
        controller._draining = True
        data.observation = harness.replace(data.observation, state="printing")
        controller._pump_dispatch()
        self.assertEqual(controller.values["jogStatus"],
                         "The print resumed — queued moves were cancelled.")
        self.assertEqual(controller._pending, ())
        self.assertEqual(commands.sent, [])

    def test_the_pause_deadline_cancels_the_queue(self):
        controller, _, _ = self._make(state="printing")
        controller.jog("x", 1)
        controller._deadline.timeout.emit()
        self.assertEqual(controller.values["jogStatus"],
                         "The printer did not pause — queued moves were cancelled.")
        self.assertFalse(controller._pause_waiting)
        self.assertEqual(controller._pending, ())
        # A late pause does not resurrect anything.
        controller._pause_failed()
        self.assertEqual(controller._pending, ())

    def test_a_disabled_gate_drops_the_queue(self):
        controller, data, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        data.observation = harness.replace(data.observation, connection="no")
        controller.observe()
        controller._pump()
        self.assertFalse(controller.values["jogEnabled"])
        self.assertEqual(controller.values["jogStatus"], "Printer is not ready for toolhead moves.")
        self.assertEqual(controller._pending, ())
        self.assertEqual(commands.sent, [])

    def test_an_unobserved_print_state_fails_closed(self):
        controller, data, commands = self._make(state="paused")
        data.observation = None
        controller.observe()
        controller.jog("x", 1)
        self.assertFalse(controller.values["jogEnabled"])
        self.assertEqual(commands.sent, [])

    def test_a_busy_lane_holds_the_queue_until_it_clears(self):
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        self.assertEqual(commands.sent, [])
        self.assertEqual(len(controller._pending), 1)
        commands.complete()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])
        self.assertEqual(controller._pending, ())

    def test_the_queue_full_cap_rejects_the_newest_tap(self):
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        # A distance the axis projection never clamps (16 x 5 mm of
        # headroom from x=10): the cap, not the limit, is what the
        # newest tap hits.
        controller.set_distance(5)
        for _ in range(16):
            controller.jog("x", 1)
        controller.jog("x", 1)
        self.assertEqual(len(controller._pending), 16)
        self.assertEqual(controller.values["jogStatus"],
                         "Too many queued moves — wait for the printer to catch up.")

    def test_a_rejected_tap_at_the_queue_cap_never_advances_the_projection(self):
        # Sixteen taps fill the queue; the seventeenth is refused by the
        # depth cap. The projection tracks the QUEUE, so a refused entry
        # must leave it — and the owed reflection — where the queue is.
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        controller.set_distance(5)
        for _ in range(16):
            controller.jog("x", 1)
        self.assertAlmostEqual(controller._axis_estimate["x"], 90.0)
        controller.jog("x", 1)
        self.assertEqual(len(controller._pending), 16)
        self.assertAlmostEqual(controller._axis_estimate["x"], 90.0)
        self.assertAlmostEqual(controller._axis_up_pending["x"], 80.0)
        self.assertEqual(controller.values["jogStatus"],
                         "Too many queued moves — wait for the printer to catch up.")
        # The refusal is about the queue's depth alone: the retained moves
        # still run once the lane frees.
        commands.busy = False
        commands.changed.emit()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X5 F3000\nG90"])

    def test_a_re_clamped_tail_that_is_refused_retreats_the_projection(self):
        # The depth cap's refusal still re-clamps the tail it inherits.
        # This queue walked the head down to the floor, so the tail's
        # clamp no longer holds: it is dropped, and the distance it
        # advanced leaves the projection with it — while the refused tap
        # itself (3 mm) never reaches it.
        controller, _, commands = self._make(live=(16.0, 10.0, 10.0, 0.0),
                                             minimum=(0, 0, 0), maximum=(200, 200, 200))
        commands.busy = True
        controller.set_distance(1)
        for _ in range(16):
            controller.jog("x", -1)
        self.assertEqual(len(controller._pending), 16)
        self.assertAlmostEqual(controller._axis_estimate["x"], 0.0)
        controller.set_distance(3)
        controller.jog("x", 1)
        self.assertEqual(controller.values["jogStatus"],
                         "Too many queued moves — wait for the printer to catch up.")
        self.assertEqual(len(controller._pending), 15)
        self.assertAlmostEqual(controller._axis_estimate["x"], 1.0)
        self.assertAlmostEqual(controller._axis_down_pending["x"], 15.0)
        self.assertAlmostEqual(controller._axis_up_pending["x"], 0.0)

    def test_a_dropped_queue_reconciles_the_projection(self):
        # X=1: a queued +25 that never dispatches may not leave 25 mm of
        # phantom headroom behind, or the next -25 is clamped against a
        # position the head never reached and walks it below the floor.
        controller, data, commands = self._make(live=(1.0, 10.0, 10.0, 0.0),
                                                minimum=(0, 0, 0), maximum=(200, 200, 200))
        commands.busy = True  # hold the lane: the tap has to stay queued
        controller.jog("x", 1)
        self.assertAlmostEqual(controller._axis_estimate["x"], 26.0)
        self.assertAlmostEqual(controller._axis_up_pending["x"], 25.0)
        # The lock lands before the queued move dispatches: the drop
        # withdraws the projection and the reflection owed for it.
        data.observation = harness.replace(data.observation, controls_locked=True)
        controller.observe()
        controller._pump()
        self.assertEqual(controller._pending, ())
        self.assertAlmostEqual(controller._axis_estimate["x"], 1.0)
        self.assertAlmostEqual(controller._axis_up_pending["x"], 0.0)
        # Unlocked: the -25 tap measures against the reconciled
        # projection, so the floor refuses it.
        data.observation = harness.replace(data.observation, controls_locked=False)
        controller.observe()
        commands.busy = False
        controller.jog("x", -1)
        self.assertEqual(self._scripts(commands), [])
        self.assertEqual(controller._pending, ())
        # The other direction measures against the same reconciliation: a
        # +25 from 1 is legal and lands where the projection says.
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])
        self.assertAlmostEqual(controller._axis_estimate["x"], 26.0)

    def test_a_drop_leaves_a_projection_that_never_advanced_alone(self):
        # The withdrawal's guards: a move queued with no position data
        # advanced nothing, and a move that is not a relative axis move
        # (extrude) projects nothing. A drop may not invent a retreat for
        # either.
        controller, data, commands = self._make()
        data.snapshot = harness.SimpleNamespace(core={}, auxiliary={})
        data.changed.emit()
        commands.busy = True
        controller.jog("y", 1)  # no position data: nothing was advanced
        controller.extrude(1)   # axis "e": no axis projection at all
        self.assertEqual(len(controller._pending), 2)
        self.assertIsNone(controller._axis_estimate["y"])
        data.observation = harness.replace(data.observation, controls_locked=True)
        controller.observe()
        controller._pump()
        self.assertEqual(controller._pending, ())
        self.assertIsNone(controller._axis_estimate["y"])
        self.assertEqual(controller._axis_up_pending["y"], 0.0)

    def test_a_drop_keeps_the_dispatched_moves_in_the_projection(self):
        # Only the moves that never dispatched are withdrawn: the one the
        # lane already took still counts, and its reflection stays owed
        # until the poll reports the head arriving.
        controller, data, commands = self._make(live=(1.0, 10.0, 10.0, 0.0),
                                                minimum=(0, 0, 0), maximum=(200, 200, 200))
        controller.jog("x", 1)  # 1 -> 26, dispatched
        self.assertEqual(len(self._scripts(commands)), 1)
        controller.jog("x", 1)  # 26 -> 51, held in the queue
        self.assertEqual(len(controller._pending), 1)
        self.assertAlmostEqual(controller._axis_estimate["x"], 51.0)
        data.observation = harness.replace(data.observation, controls_locked=True)
        controller.observe()
        controller._pump()  # the lock drops the queued move, not the sent one
        self.assertEqual(controller._pending, ())
        self.assertAlmostEqual(controller._axis_estimate["x"], 26.0)
        self.assertAlmostEqual(controller._axis_up_pending["x"], 25.0)
        # The pre-command poll still cannot pull the projection back: the
        # dispatched move is committed.
        data.observation = harness.replace(data.observation, controls_locked=False)
        data.set_state("paused", live=(1.0, 10.0, 10.0, 0.0),
                       minimum=(0, 0, 0), maximum=(200, 200, 200))
        self.assertAlmostEqual(controller._axis_estimate["x"], 26.0)

    def test_a_cancelled_pause_reconciles_away_the_queued_moves(self):
        # The pause never lands, so the tap queued behind it is cancelled
        # with it — and the projection may not keep the headroom that tap
        # claimed (the failure and the deadline are two doors to the same
        # drop).
        for failed in (True, False):
            controller, data, commands = self._make(state="printing",
                                                   live=(1.0, 10.0, 10.0, 0.0),
                                                   minimum=(0, 0, 0), maximum=(200, 200, 200))
            controller.jog("x", 1)
            self.assertAlmostEqual(controller._axis_estimate["x"], 26.0)
            if failed:
                data.commandChanged.emit({"name": "Pause", "terminal": True,
                                          "outcome": "failed"})
            else:
                controller._deadline.timeout.emit()
            self.assertEqual(controller._pending, ())
            self.assertAlmostEqual(controller._axis_estimate["x"], 1.0)
            self.assertAlmostEqual(controller._axis_up_pending["x"], 0.0)
            # The next tap is measured against the truth: -25 from 1 is
            # refused rather than queued behind a pause that will not come.
            controller.jog("x", -1)
            self.assertEqual(controller._pending, ())
            self.assertNotIn("G1 X-25", self._scripts(commands))

    def test_the_guard_cooldown_releases_the_fast_poll_floor(self):
        controller, data, commands = self._make(state="paused")
        controller.jog("x", 1)
        self.assertTrue(controller._guard_latched)
        self.assertTrue(data.guard_calls[-1])
        commands.complete()
        self.assertFalse(controller._guard_latched)
        self.assertTrue(controller._guard_cooldown.isActive())
        self.assertTrue(data.guard_calls[-1])  # the cooldown still holds it
        controller._guard_cooldown.timeout.emit()
        self.assertFalse(data.guard_calls[-1])

    def test_a_data_owner_without_a_guard_setter_is_tolerated(self):
        data = harness._ToolheadData()
        commands = harness._ToolheadCommands()
        data.set_state("paused")
        controller = self.controller_class(data, commands)
        self.addCleanup(controller.close)
        # The guard is optional: a data owner exposing no setter is a
        # plain no-op, never an AttributeError in the middle of a move.
        data.set_toolhead_guard = None
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_an_emergency_stop_and_a_session_reset_clear_everything(self):
        controller, data, commands = self._make(state="printing")
        controller.jog("x", 1)
        commands.emergencyStopped.emit()
        self.assertEqual(controller._pending, ())
        self.assertFalse(controller._pause_waiting)
        self.assertEqual(controller.values["jogStatus"], "")
        controller.jog("x", 1)
        data.invalidated.emit()
        self.assertEqual(controller._pending, ())
        self.assertFalse(data.guard_calls[-1])

    def test_the_state_readout_needs_an_active_connected_printer(self):
        controller, data, _ = self._make(state="paused")
        self.assertEqual(controller._state(), "paused")
        data.active = False
        self.assertEqual(controller._state(), "")
        data.active = True
        data.connected = False
        self.assertEqual(controller._state(), "")
        data.connected = True
        data.snapshot.core.pop("print_stats")
        self.assertEqual(controller._state(), "")

    def test_close_stops_both_timers(self):
        controller, _, _ = self._make(state="printing")
        controller.jog("x", 1)
        controller.close()
        self.assertFalse(controller._deadline.isActive())
        self.assertFalse(controller._guard_cooldown.isActive())

    def test_the_values_block_carries_the_whole_control_surface(self):
        controller, _, _ = self._make(state="standby")
        controller.set_distance(5)
        controller.set_extrude_distance(2.5)
        controller.set_extrude_speed(60)
        values = controller.values
        self.assertEqual(values, {"jogEnabled": True, "jogDistance": 5.0,
                                  "extrudeDistance": 2.5, "extrudeSpeed": 60.0,
                                  "homedAxes": "xyz", "positionMode": "Absolute",
                                  "jogStatus": ""})
        # The property hands back a copy: a caller cannot rewrite the model.
        values["jogDistance"] = 999
        self.assertEqual(controller.values["jogDistance"], 5.0)


