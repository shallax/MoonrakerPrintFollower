"""Executable monitor model runtime contracts."""
from tests import monitor_test_support as harness

class MonitorQtTests(harness.MonitorQtTests):
    def test_resume_waits_for_acknowledgement_through_slow_heating(self):
        model = self.monitor()
        self.deliver_state("paused")
        self.qt.events()
        model._commands.send("Resume", "printer/print/resume")
        request = next(r for r in self.transport.requests if r.path == "printer/print/resume")
        self.assertEqual(request.options["timeout_ms"], 120000)
        tracker = self.follower.client._session.commands
        command = tracker._commands["Resume"]
        self.assertEqual(tracker.expire(now=command.issued_at + 119), [])
        self.assertTrue(model._commands.busy)
        # The printing frame can precede the delayed HTTP acknowledgement.
        self.deliver_state("printing")
        request.callback({"result": "ok"}, None)
        self.qt.events()
        self.assertFalse(model.actionBusy)
        self.assertIn("Printer state is printing", model.actionStatus)

    def test_resume_transport_timeout_does_not_claim_the_print_was_cancelled(self):
        model = self.monitor()
        self.deliver_state("paused")
        model._commands.send("Resume", "printer/print/resume")
        request = next(r for r in self.transport.requests if r.path == "printer/print/resume")
        request.callback(None, "Operation canceled")
        self.qt.events()
        self.assertIn("Resume: outcome unknown: Operation canceled", model.actionStatus)
        self.assertFalse(model.actionBusy)

    def test_restart_last_print_requires_previous_job_and_revalidates_at_dispatch(self):
        from unittest.mock import patch
        model = self.monitor()
        self.assertFalse(model.canRestartLastPrint)
        self.deliver_state("printing")
        self.qt.events()
        self.deliver_state("complete")
        self.qt.events()
        self.assertTrue(model.canRestartLastPrint)
        with patch.object(model._file_manager, "start_print") as start:
            model.restartLastPrint()
            start.assert_called_once_with("part.gcode")
            for state in ("printing", "paused"):
                self.deliver_state(state)
                self.qt.events()
                self.assertFalse(model.canRestartLastPrint)
                model.restartLastPrint()
            self.assertEqual(start.call_count, 1)
            self.deliver_state("complete")
            model.setControlsLocked(True)
            model.restartLastPrint()
            self.assertEqual(start.call_count, 1)
        model.setControlsLocked(False)
        client = self.follower.client
        client._handle_http_status({"result": {"status": {"print_stats": {"filename": ""}}}},
                                   None, client._generation, self.status_stamp())
        model._publish()
        self.assertTrue(model.canRestartLastPrint,
                        "SDCARD_RESET_FILE must not erase the previous restart target")

    def test_restart_target_is_not_inherited_from_an_earlier_cura_session(self):
        model = self.monitor()
        self.deliver_state("complete")
        self.qt.events()
        self.assertFalse(model.canRestartLastPrint)
        self.deliver_state("printing")
        self.qt.events()
        self.deliver_state("complete")
        self.qt.events()
        self.assertTrue(model.canRestartLastPrint)
        model.restartLastPrint()
        self.assertFalse(model.canRestartLastPrint)
        starts = [r for r in self.transport.requests if "printer/print/start" in r.path]
        self.assertEqual(len(starts), 1)
        self.assertIn("part.gcode", starts[0].path)

    def test_indexed_print_start_does_not_offer_another_download(self):
        model = self.monitor()
        self.deliver_state("printing")
        model.setFollowerPopoverOpen(True)
        coordinator = self.follower._runtime.coordinator
        coordinator._snapshot = harness.replace(coordinator._snapshot, index_ready=True, plate_progress=None)
        model._publish()
        self.assertTrue(model.printIndexReady)
        self.assertIn("!root.printerModel.printIndexReady", harness.JOB_SECTION_QML)
        self.assertIn("Print indexed", model.plateProgressReason)
        with harness.patch.object(model, "_request_monitor_download") as download:
            model.improveEta()
            download.assert_not_called()
        coordinator._snapshot = harness.replace(coordinator._snapshot, index_ready=False)
        self.deliver_state("standby")
        model._publish()
        self.assertEqual(model.plateProgressReason, "No active print.")

    def test_live_tracking_readiness_updates_with_both_follower_surfaces_closed(self):
        model = self.monitor()
        self.deliver_state("printing")
        model.setFollowerPopoverOpen(False)
        model._sections["plateprogress"] = False
        self.assertFalse(model.plateTrackingAvailable)
        self._with_layers(model, 0, 100)
        self.assertTrue(model.plateTrackingAvailable)
        self.assertFalse(model.plateProgressAvailable,
                         "the closed popover must still freeze its own surface state")

    def test_last_action_timestamp_is_event_time_not_publication_time(self):
        from unittest.mock import patch
        model = self.monitor()
        commands = model._commands
        with patch.object(commands, "_timestamp", side_effect=["10:11:12", "10:12:13"]):
            commands.report_status("Operation cancelled")
            model._publish()
            self.assertEqual(model.actionStatus, "Operation cancelled")
            self.assertEqual(model.actionTimestamp, "10:11:12")
            model._publish()
            self.assertEqual(model.actionTimestamp, "10:11:12")
            commands.report_status("Operation cancelled")
            model._publish()
            self.assertEqual(model.actionTimestamp, "10:12:13")

    def test_new_print_clears_old_action_but_resume_does_not(self):
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events()
        commands = model._commands
        commands.report_status("Job cancelled")
        self.deliver_state("paused")
        self.qt.events()
        self.deliver_state("printing")
        self.qt.events()
        self.assertEqual(commands.status, "Job cancelled")
        self.deliver_state("cancelled")
        self.qt.events()
        self.deliver_state("printing")
        self.qt.events()
        self.assertEqual(commands.status, "")
        self.assertEqual(commands.status_timestamp, "")

    def test_new_print_does_not_inherit_a_late_cancel_confirmation(self):
        model = self.monitor()
        commands = model._commands
        commands.observe_job(("part.gcode", 100, 1))
        commands.send("Cancel", "printer/print/cancel")
        commands.observe_job(("part.gcode", 100, 2))
        self.assertTrue(commands.busy)
        commands._command_changed({"name": "Cancel", "outcome": "confirmed",
                                   "detail": "cancelled", "terminal": True})
        self.assertFalse(commands.busy)
        self.assertEqual(commands.status, "")

    def test_improve_eta_hourglass_survives_the_registration_gap(self):
        # The red-run catch: improveEta publishes its own flag-set,
        # and the coordinator's load_active flips only on its NEXT
        # snapshot rebuild — the improve's own publish must not clear
        # the hourglass with the stale snapshot (the flag cleared
        # instantly and the improve silently no-opped whenever a
        # previous load's tail was not still holding load_active set).
        model = self.monitor()
        self.deliver()
        model.improveEta()
        self.assertTrue(model.improvingEta,
                        "the hourglass must survive the improve's own publish")

    def test_toolhead_slots_send_exact_scripts(self):
        model = self.monitor()
        self.deliver_state("standby")
        self.assertTrue(model.jogEnabled)
        self.assertEqual(model.positionMode, "Absolute")
        model.setJogDistance(10)
        self.assertEqual(model.jogDistance, 10.0)

        def next_script(action, *args):
            before = len(self.scripts())
            action(*args)
            self.qt.events(10)
            scripts = self.scripts()
            self.assertEqual(len(scripts), before + 1)
            scripts[-1].callback({}, None)
            self.qt.events(10)
            return scripts[-1].options["body"]
        self.assertEqual(next_script(model.jog, "x", 1), {"script": "G91\nG1 X10 F3000\nG90"})
        # The Z jog from 0.4 by -10 crosses zero with no configured
        # minimum: forbidden outright (a live report —
        # the head must never microstep below 0.00 Z).
        before = len(self.scripts())
        model.jog("z", -1)
        self.qt.events(10)
        self.assertEqual(len(self.scripts()), before)
        self.assertEqual(next_script(model.home, "y"), {"script": "G28 Y"})
        self.assertEqual(next_script(model.home, ""), {"script": "G28"})
        self.assertEqual(next_script(model.motorsOff), {"script": "M18"})
        # Extrude uses the configured distance and speed (defaults 5 mm, 5 mm/s).
        self.assertEqual(next_script(model.extrude, 1), {"script": "G91\nG1 E5 F300\nG90"})
        self.assertEqual(next_script(model.extrude, -1), {"script": "G91\nG1 E-5 F300\nG90"})
        model.setExtrudeDistance(10)
        model.setExtrudeSpeed(120)
        self.assertEqual(next_script(model.extrude, 1), {"script": "G91\nG1 E10 F120\nG90"})
        model.setExtrudeDistance(10)
        model.setExtrudeSpeed(300)
        model.setJogDistance(42.5)
        self.assertEqual(next_script(model.jog, "x", 1), {"script": "G91\nG1 X42.5 F3000\nG90"})
        model.setJogDistance(10)

    def test_negative_free_text_distances_are_rejected(self):
        # The free-text fields hold magnitudes; the buttons carry the
        # direction. A negative entry must never invert the arrows.
        model = self.monitor()
        self.deliver_state("standby")
        model.setJogDistance(10)
        model.setJogDistance(-25)
        self.assertEqual(model.jogDistance, 10.0)
        model.setExtrudeDistance(-5)
        self.assertEqual(model.extrudeDistance, 5.0)
        model.setExtrudeSpeed(-120)
        self.assertEqual(model.extrudeSpeed, 300.0)
        model.jog("x", 1)
        self.qt.events(10)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X10 F3000\nG90"})

    def test_toolhead_guard_releases_and_polls_do_not_rearm_it(self):
        # The guard drops the poll floor while moves run and for a short
        # settle afterwards. Polls arriving during the settle must NOT
        # re-arm the cooldown — at poll cadence that would make the
        # release unreachable and hold the urgent floor forever.
        model = self.monitor()
        self.deliver_state("standby")
        model._toolhead._guard_cooldown.setInterval(500)
        model.setJogDistance(1)
        model.jog("x", 1)
        client = self.follower.client
        self.assertTrue(client._session.toolhead_guard)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        scripts[0].callback({}, None)
        self.qt.events(10)
        for _ in range(3):
            self.deliver_state("standby")
            self.qt.events(10)
        self.assertTrue(client._session.toolhead_guard)  # settling, not re-armed
        self.qt.events(700)
        self.assertFalse(client._session.toolhead_guard)  # released on schedule

    def test_jog_keeps_its_place_behind_queued_one_shots(self):
        # The toolhead sends on the shared command lane. A jog tapped
        # while one-shots are queued must run AFTER them, not jump the
        # queue when the in-flight command completes.
        model = self.monitor()
        self.deliver_state("standby")
        model.setJogDistance(1)
        model.jog("x", 1)          # in flight on the lane
        model.homeAll()            # queues behind the in-flight jog
        model.jog("y", 1)          # waits in the toolhead queue
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({}, None)  # jog X completes; Home must go next
        self.qt.events(10)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual([s.options["body"]["script"] for s in scripts[-2:]],
                         ["G91\nG1 X1 F3000\nG90", "G28"])
        scripts[-1].callback({}, None)  # Home completes; now the Y jog runs
        self.qt.events(10)
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        self.assertEqual(scripts[-1].options["body"],
                         {"script": "G91\nG1 Y1 F3000\nG90"})

    def test_endpoint_change_invalidates_subscribers_once(self):
        # PrinterBinding tears the poller down silently before the rebind;
        # the client's configure emits exactly one invalidation wave on
        # the old identity.
        client = self.follower.client
        waves = []
        client.sessionInvalidated.connect(lambda: waves.append(True))
        self.follower.apply_printer_config(
            self.config_type(url="http://printer-b", api_key="new-key", path_follow=False))
        self.qt.events(10)
        self.assertEqual(len(waves), 1)

    def test_pause_first_jog_waits_for_paused_confirmation_then_drains(self):
        model = self.monitor()
        self.deliver_state("printing")
        self.assertFalse(model.jogEnabled)  # moves need an explicit pause first
        model.setJogDistance(1)
        model.jog("x", 1)
        model.jog("x", 1)  # queues as its own move (no coalescing)
        pauses = [r for r in self.transport.requests if r.path == "printer/print/pause"]
        self.assertEqual(len(pauses), 1)
        self.assertEqual(self.scripts(), [])
        self.assertIn("Waiting for the printer to pause", model.jogStatus)
        # The harness must ack the pause HTTP request: acceptance precedes
        # state confirmation, exactly as in production.
        pauses[0].callback({}, None)
        self.deliver_state("paused")
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        # Each queued move drains on its own completion cycle (the
        # no-coalescing ruling: the queue holds separate ops).
        scripts[0].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[1].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        self.assertEqual(model.jogStatus, "")

    def test_pause_timeout_drops_queued_jogs(self):
        controller = self.qt.load("ToolheadController")
        with harness.patch.object(controller, "PAUSE_WAIT_TIMEOUT_S", 0.05):
            model = self.monitor()
            self.deliver_state("printing")
            model.jog("x", 1)
            self.qt.events(200)
        self.assertEqual(self.scripts(), [])
        self.assertIn("did not pause", model.jogStatus)

    def test_resume_during_drain_drops_remaining_moves(self):
        model = self.monitor()
        self.deliver_state("printing")
        model.setJogDistance(1)
        model.jog("x", 1)
        model.jog("y", 1)  # different axis: two distinct ops
        pauses = [r for r in self.transport.requests if r.path == "printer/print/pause"]
        pauses[0].callback({}, None)  # HTTP acceptance before state confirmation
        self.deliver_state("paused")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)  # the first op drains
        self.assertEqual(scripts[0].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        # The print resumes before the first move completes: the remaining
        # move must be dropped, never force-executed mid-print.
        self.deliver_state("printing")
        self.assertEqual(self.scripts(), scripts)
        self.assertIn("resumed", model.jogStatus)

    def test_emergency_stop_clears_queued_scripts_and_busy(self):
        model = self.monitor()
        self.deliver_state("standby")
        commands = model._commands
        commands.script("Home", "G28")
        commands.script("QGL", "QUAD_GANTRY_LEVEL")  # queued behind the in-flight send
        self.assertEqual(len(commands._queue), 1)
        model.emergencyStopClick()
        model.emergencyStopClick()
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(commands._queue, [])
        self.assertFalse(commands.busy)
        self.assertFalse(model.actionBusy)  # power toggles and restarts unlock

    def test_early_release_cancels_the_hold(self):
        model = self.monitor()
        self.deliver_state("standby")
        model.emergencyStopClick()
        model.emergencyStopClick()
        model.emergencyHoldStarted()
        model.emergencyHoldReleased()
        self.qt.events(200)
        self.assertEqual([r for r in self.transport.requests if r.channel == "emergency-stop"], [])
        # The arm persists: a second, completed hold fires.
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(sum(r.channel == "emergency-stop" for r in self.transport.requests), 1)

    def test_emergency_stop_during_a_print_releases_the_print_guards(self):
        # A live report: an e-stop mid-print left the
        # "disabled during print" guards up and the printer
        # unrecoverable. The stop ASSUMES the print was cancelled:
        # the snapshot-derived guards release immediately, and the
        # snapshot's real state supersedes the assumption when it
        # lands.
        model = self.monitor()
        self.deliver_state("printing")
        # Published guards arrive through the owner's coalescing timer.
        deadline = harness.time.monotonic() + 1
        while not model.printActive and harness.time.monotonic() < deadline:
            self.qt.events(10)
        self.assertTrue(model.printActive)
        self.assertFalse(model.jogEnabled)
        model._commands._clicks = 2
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        estops = [r for r in self.transport.requests if "emergency_stop" in r.path]
        estops[-1].callback({}, None)
        self.qt.events(10)
        # The snapshot still says printing — the ASSUMPTION releases
        # the guards. (The automatic reconnect lands on its own 1.5 s
        # delay, outside this window — its own test below.)
        self.assertFalse(model.printActive)
        self.assertTrue(model.jogEnabled)
        self.assertFalse(model.canPausePrint)
        # The printer's real state supersedes the assumption (the
        # client-level flag clears when the observation lands).
        self.deliver_state("standby")
        self.assertFalse(self.follower.client._session.state.assume_print_stopped)
        self.assertFalse(model.printActive)

    def test_queued_restart_revalidates_at_dispatch(self):
        # 4.2.0 N1: a restart queued behind an in-flight command was
        # valid when clicked; a print starting in the window must
        # drop it at dispatch, with the policy's reason.
        model = self.monitor()
        self.deliver_state("standby")
        model.jog("x", 1)  # in flight on the lane
        model.klipperRestart()  # a valid click (standby), queued
        self.deliver_state("printing")
        scripts = [r for r in self.transport.requests if r.path == "printer/gcode/script"]
        scripts[0].callback({}, None)  # the jog completes; the pump runs
        self.qt.events(20)
        self.assertEqual([r for r in self.transport.requests if r.path == "printer/restart"], [])
        self.assertIn("Klipper restart cancelled", model.actionStatus)

    def test_print_start_dispatch_gate_refuses_a_print_that_started(self):
        # 4.2.0 S3/N2: the dialog's click was valid when it opened;
        # the dispatch re-checks — a print running by confirm time
        # refuses with the policy's reason, nothing reaches the wire.
        model = self.monitor()
        self.deliver_state("standby")
        model._file_print_confirm = {"relpath": "part.gcode"}
        self.deliver_state("printing")
        model.fileConfirmPrint()
        self.assertEqual([r for r in self.transport.requests if r.path == "printer/print/start"], [])
        self.assertIn("Print start refused", model.actionStatus)

    def test_emergency_stop_reconnects_once_automatically(self):
        # The ruling (2026-09-10, live-proven on a real
        # printer): after the stop the host refuses commands until
        # the connection is cycled — the plugin cycles the client
        # once (stop + start = two generation bumps) and the monitor
        # comes back armed, reading a fresh status.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(10)
        generation_before = self.follower.client._generation
        commands_module = self.qt.load("MonitorCommands")
        with harness.patch.object(commands_module.MonitorCommands, "RECONNECT_DELAY_MS", 0):
            model._commands._clicks = 2
            model._commands._hold_timer.setInterval(30)
            model.emergencyHoldStarted()
            self.qt.events(100)
        self.assertEqual(self.follower.client._generation, generation_before + 2)
        self.assertTrue(model._data.active)
        self.deliver_state("standby")
        self.qt.events(10)
        self.assertTrue(model.jogEnabled)

    def test_emergency_stop_ignores_the_pre_stop_command_reply(self):
        # A live request: after the stop the plugin
        # assumes the print was cancelled — the in-flight command's
        # terminal reply must not overwrite "Emergency stop issued".
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        model._commands._clicks = 2
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        estops = [r for r in self.transport.requests if "emergency_stop" in r.path]
        self.assertEqual(len(estops), 1)
        estops[-1].callback({}, None)
        self.qt.events(10)
        self.assertIn("Emergency stop", model.actionStatus)
        # The pre-stop command's reply lands late: it must be ignored.
        scripts[0].callback({}, None)
        self.qt.events(10)
        self.assertIn("Emergency stop", model.actionStatus)
        self.assertNotIn("TEST_MACRO", model.actionStatus)

    def test_rapid_z_nudges_cannot_walk_the_head_below_zero(self):
        # A live report: nudge taps outran the poll, each
        # clamped against the STALE position, and the queue walked
        # the head below 0.00 Z. The client-side estimate advances
        # per accepted move: four 0.1 nudges from 0.4 land at zero;
        # the fifth is forbidden outright.
        model = self.monitor()
        self.deliver_state("standby")  # live_position z = 0.4
        model.setJogDistance(0.1)
        for _ in range(5):
            model.jog("z", -1)
        self.qt.events(10)
        # Four nudges are accepted (0.4 → 0.0); the fifth is
        # forbidden by the estimate — the head never goes below zero.
        # (The drain merges queued taps, so the script COUNT is not
        # pinned; the estimate is the guard.)
        self.assertAlmostEqual(model._toolhead._axis_estimate["z"], 0.0)
        z_scripts = [r for r in self.scripts() if "G1 Z" in str(r.options.get("body"))]
        self.assertGreaterEqual(len(z_scripts), 1)

    def test_unchanged_projections_are_not_rebuilt_on_heartbeats(self):
        # I (the 2026-09-19 performance review): the console's
        # transcript projection, the controls' deep copy and the
        # peripheral scan all rebuild only when their inputs actually
        # changed — an unchanged heartbeat serves the same objects.
        from unittest.mock import patch
        model = self.monitor()
        self.qt.events(1)
        # Warm-up: the connection transition's note is a REAL
        # transcript change and must land before the window.
        self.deliver_state("standby")
        self.qt.events(1)
        console_first = model._console.values
        controls_first = model._controls.values
        module = self.qt.load("MoonrakerMonitorModel")
        scans = []
        original = module.peripheral_values
        def counting(snapshot):
            scans.append(1)
            return original(snapshot)
        with patch.object(module, "peripheral_values", counting):
            for _ in range(5):
                self.deliver_state("standby")
                self.qt.events(1)
            self.assertEqual(scans, [], "core-only landings must not rescan peripherals")
            model._data._merge_aux({"extruder": {"temperature": 200.0, "target": 210.0}})
            self.qt.events(1)
            self.assertEqual(len(scans), 1, "an aux landing rescans exactly once")
        self.assertIs(console_first["consoleLines"], model._console.values["consoleLines"],
                      "an unchanged transcript must keep its projection")
        self.assertIs(controls_first, model._controls.values,
                      "unchanged controls must keep their copy")

    def test_stale_polls_cannot_raise_the_z_projection_between_dispatched_moves(self):
        # B (the 2026-09-19 review): an op leaves the queue at
        # dispatch, so a stale poll between the dispatch and the
        # physical move's reflection re-synced the projection upward
        # and every following tap was accepted against the old
        # position again — repeated stale polls could walk the
        # accepted downward distance past the 0.40 mm of real
        # headroom.
        model = self.monitor()
        self.deliver_state("standby")  # live_position z = 0.4
        model.setJogDistance(0.1)
        for _ in range(4):
            model.jog("z", -1)
            self.qt.events(1)
            # The stale poll: the head has not moved yet, the report
            # still reads 0.40.
            self.deliver_state("standby")
            self.qt.events(1)
        # Four accepted moves cover exactly the 0.40 headroom; the
        # fifth must be forbidden — the projection floors at zero
        # instead of re-arming against each stale poll.
        model.jog("z", -1)
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._axis_estimate["z"], 0.0)
        self.assertIn("rejected", model._toolhead._status)

    def test_z_projection_follows_fresh_telemetry_and_upward_motion(self):
        # B's catch-up half: once the poll reports the commanded
        # level (or below), the projection adopts the truth again —
        # and an upward jog followed by a downward one tracks both
        # ways instead of freezing at a stale floor.
        model = self.monitor()
        self.deliver_state("standby")  # z = 0.4
        model.setJogDistance(0.1)
        model.jog("z", -1)  # projection 0.3
        self.qt.events(1)
        def deliver_z(z):
            status = {"print_stats": {"state": "standby"},
                      "gcode_move": {"gcode_position": [0, 0, z, 0]},
                      "motion_report": {"live_position": [0, 0, z, 0]}}
            self.follower.client._handle_http_status({"result": {"status": status}}, None,
                                                     self.follower.client._generation, self.status_stamp())
        deliver_z(0.3)  # the head arrived: the poll adopts
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._axis_estimate["z"], 0.3)
        model.jog("z", 1)  # upward: projection 0.4
        self.qt.events(1)
        deliver_z(0.4)
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._axis_estimate["z"], 0.4)
        model.jog("z", -1)  # downward again: projection 0.3
        self.qt.events(1)
        deliver_z(0.3)
        self.qt.events(1)
        self.assertAlmostEqual(model._toolhead._axis_estimate["z"], 0.3)

    def test_command_reply_errors_are_reported_as_outcome_unknown(self):
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        # A timed-out reply must not claim the command failed: the printer
        # may well have executed it.
        scripts[0].callback(None, "Operation canceled")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertIn("outcome unknown", model.actionStatus)

    def test_extrude_refusal_reads_the_servers_words_in_the_status(self):
        # A live report: a cold extrude showed a bare 400
        # in the Printer status field. The REAL toolhead path — the
        # extrude rides the shared commands lane — must surface the
        # server's words, not a status code.
        model = self.monitor()
        self.deliver_state("standby")
        model.setExtrudeDistance(5)
        model.extrude(1)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({"code": 400, "message": "Unknown",
                             "traceback": "... HTTPError: HTTP 400: Extrude below minimum temp\n"
                                          "See the 'min_extrude_temp' config option for details"},
                            "Extrude below minimum temp")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertIn("refused", model.actionStatus)
        self.assertIn("Extrude below minimum temp", model.actionStatus)
        self.assertNotIn("400", model.actionStatus)

    def test_extrude_and_jog_selection_persists(self):
        # A live report: the chosen extrude options were
        # not saved between sessions.
        from mpf.monitor.MoonrakerMonitorModel import _read_state
        model = self.monitor()
        self.deliver_state("standby")
        model.setExtrudeDistance(25)
        model.setExtrudeSpeed(120)
        model.setJogDistance(10)
        stored = _read_state(self.follower.persistence)["toolhead"]
        self.assertEqual(stored["extrudeDistance"], 25.0)
        self.assertEqual(stored["extrudeSpeed"], 120.0)
        self.assertEqual(stored["jogDistance"], 10.0)

    def test_collapsed_console_keeps_a_slow_error_poll(self):
        # The error bell's feed (the live request): the
        # store fetch stops while expanded-only, so a SLOW watch
        # keeps the bell able to ring while collapsed.
        model = self.monitor()
        self.deliver_state("standby")
        model.setConsoleExpanded(False)
        self.assertTrue(model._data._console_watch.isActive())
        stores = lambda: [r for r in self.transport.requests if "gcode_store" in r.path]
        before = len(stores())
        model._data.refresh_console_store()  # gated: a no-op while collapsed
        self.assertEqual(len(stores()), before)
        model._data._refresh_console_watch()  # the bell's slow poll
        self.assertEqual(len(stores()), before + 1)
        model.setConsoleExpanded(True)
        self.assertFalse(model._data._console_watch.isActive())

    def test_command_reply_with_error_body_is_reported_as_refused(self):
        # A server ANSWER with an error body is a refusal, not an
        # unknown: the command did not run (a live
        # report — a cold extrude showed a bare 400).
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({"error": "Extrude below minimum temp"}, "Extrude below minimum temp")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertIn("refused", model.actionStatus)
        self.assertIn("Extrude below minimum temp", model.actionStatus)
        self.assertNotIn("outcome unknown", model.actionStatus)

    def test_console_error_bell_rings_while_collapsed_and_clears_on_expand(self):
        # A live request: an error line landing while the
        # console is collapsed rings a red bell next to its header
        # until the console expands. Restored lines never ring.
        model = self.monitor()
        self.deliver_state("standby")
        model._sections["console"] = False
        model._console._append_entries([{"kind": "command", "text": "!! cold",
                                         "error": False, "success": False, "restored": True}])
        model._console._emit_changed()  # the real error path emits through the send callback
        self.assertFalse(model.consoleErrorBell)
        model._console._append_entries([{"kind": "command", "text": "!! cold",
                                         "error": True, "success": False, "restored": False}])
        model._console._emit_changed()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertTrue(model.consoleErrorBell)
        # Expanding clears it.
        model._sections["console"] = True
        model._console._emit_changed()
        self.qt.events()
        self.assertFalse(model.consoleErrorBell)
        # Old errors never re-ring after collapsing again.
        model._sections["console"] = False
        model._console._emit_changed()
        self.qt.events()
        self.assertFalse(model.consoleErrorBell)

    def test_action_status_receipt_overlays_then_reverts_to_durable(self):
        # The lane's completion receipts are transient: "X sent"
        # overlays for RECEIPT_MS, then the row reverts to the durable
        # value beneath — never to nothing (the ruling: age
        # out to the previous durable value).
        model = self.monitor()
        self.deliver_state("standby")
        model._commands._status = "Pause: paused"
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.qt.events()  # the publish coalescer flushes on the next turn
        # In flight: the lifecycle text overlays the durable status.
        self.assertEqual(model.actionStatus, "Macro TEST_MACRO requested…")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback(None, None)
        self.qt.events()  # the publish coalescer flushes on the next turn
        # Completed: the receipt, with send-family copy — never
        # "accepted", which would claim an outcome the POST ack cannot
        # vouch for.
        self.assertEqual(model.actionStatus, "Macro TEST_MACRO sent")
        self.assertNotIn("accepted", model.actionStatus)
        # Aged out: the row falls to "—" under its permanent caption —
        # never back to a stale durable claim from an earlier action
        # (the panel ruling: "Pause: paused" resurfacing after a newer
        # action reads as fresh printer activity).
        self.qt.events(model._commands.RECEIPT_MS + 500)
        # Under the parallel coverage wave's load the receipt's timer
        # can lag the simulated window: poll within a budget instead
        # of asserting once (the assert_model pattern).
        budget = 5000
        while model.actionStatus != "" and budget > 0:
            self.qt.events(100)
            budget -= 100
        self.assertEqual(model.actionStatus, "")

    def test_console_sends_never_touch_the_action_status(self):
        # Console traffic left the card ticker: the feed carries
        # console feedback, and the card row keeps showing whatever
        # durable value it had (the panel UX ruling).
        model = self.monitor()
        model._commands._status = "Pause: paused"
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.actionStatus, "Pause: paused")
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback(None, None)
        # The completion is a console lane cycle, not a card event.
        self.assertEqual(model.actionStatus, "Pause: paused")

    def test_console_error_lines_speak_in_the_feed(self):
        # The live ruling: no status banners or labels outside
        # the feed — a live "!!" line is its own red signal, and
        # nothing asserts "Klipper reported an error" anywhere.
        model = self.monitor()
        self.assertFalse(hasattr(model, "consoleStatus"))
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.assertEqual(model.actionStatus, "")
        model._console.append_responses([{"text": "!! Must home first", "error": True,
                                          "success": False, "time": model._console._store_time + 1.0}])
        self.qt.events()  # the publish coalescer flushes on the next turn
        lines = model.consoleLines.value()
        self.assertTrue(lines[-1]["error"])
        self.assertEqual(model.actionStatus, "")
        self.assertTrue(model.sendConsoleCommand("G28"))

    def test_macros_refuse_while_printing(self):
        model = self.monitor()
        # observe() rebuilds the macro table from the snapshot on every
        # delivery, so re-seed it after each state change.
        self.deliver_state("printing")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.assertEqual(self.scripts(), [])
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.assertEqual([r.options["body"] for r in self.scripts()], [{"script": "TEST_MACRO"}])
        # The Run button carries the same gate in the UI.
        self.assertIn("!root.printerModel.printActive", harness.MACROS_SECTION_QML)

    def test_system_restarts_are_queued_one_shot_commands(self):
        model = self.monitor()
        self.deliver_state("standby")
        model.firmwareRestart()
        # The host's own endpoint (the ruled route): the gcode form
        # disconnects immediately, so its ack never arrives.
        restarts = [r for r in self.transport.requests if r.path == "printer/firmware_restart"]
        self.assertEqual(len(restarts), 1)
        restarts[0].callback({}, None)
        self.qt.events(10)
        model.hostRestart()
        reboot = [r for r in self.transport.requests if r.path == "machine/reboot"]
        self.assertEqual(len(reboot), 1)
        reboot[0].callback({}, None)
        self.qt.events(10)
        # A full Klipper restart hits Moonraker's RESTART endpoint (the
        # request — heavier than FIRMWARE_RESTART).
        model.klipperRestart()
        self.qt.events(10)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "printer/restart"]), 1)
        # Restarts refuse while a print is active.
        self.deliver_state("printing")
        model.firmwareRestart()
        model.hostRestart()
        model.klipperRestart()
        self.assertEqual(len([r for r in self.transport.requests if r.path == "printer/firmware_restart"]), 1)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "machine/reboot"]), 1)
        self.assertEqual(len([r for r in self.transport.requests if r.path == "printer/restart"]), 1)

    def test_panel_state_persists_across_model_instances(self):
        model = self.monitor()
        self.assertNotEqual(model._sections.get("toolhead"), False)  # default expanded
        model.setSectionExpanded("toolhead", False)
        model.setControlsCollapsed(True)
        model.setControlsLocked(True)
        model.setInfoCollapsed(True)
        model.setStatusCollapsed(True)
        # The write really lands in the plugin-owned JSON file, so a Cura
        # restart round-trips through the file rather than any model state
        # or Uranium preference-store behaviour.
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(harness.json.load(handle), {
                # A fresh model stores no what's-new marker — the
                # overlay shows until a dismissal records one.
                "whatsNewSeen": "",
                "sections": {"toolhead": False},
                "controlsCollapsed": True,
                "controlsLocked": True,
                "infoCollapsed": True,
                "statusCollapsed": True,
                # An undragged console writes 0 — "never dragged", so
                # the pane renders at its own default size.
                "consoleHeight": 0,
                # The column config (Snapshot 3): widths only hold
                # user-set values; the order is the pinned sequence
                # until changed.
                "fileManagerColumns": {
                    "widths": {},
                    "order": ["Modified", "Size", "Attempts", "Status", "Object height",
                              "Layer height", "Est. time", "Last print", "Slicer",
                              "Extruder", "Bed", "Filament"],
                    "hidden": [],
                },
                # The jog/extrude selection persists too (the
                # live report: the chosen options were not
                # saved between sessions).
                "toolhead": {"jogDistance": 25.0, "extrudeDistance": 5.0, "extrudeSpeed": 300.0},
                # The follower view settings are global (the live
                # ruling) — the defaults ride the fresh document.
                "followerView": {"showPrevious": True, "showNext": True,
                                 "showBase": True, "showTravels": False, "showRetractions": False, "showUnretractions": False, "trueThickness": False,
                                 "antialiasing": False, "keepCentred": False, "lineScale": 1.0},
            })
            # The chart config is per-printer now: the global file must
            # not carry it, and the per-printer record defaults empty.
            self.assertEqual(self.follower.current_printer_config().temperature_chart, {})
        # A fresh model reads the stored file back.
        second = self.monitor()
        self.assertEqual(second._sections["toolhead"], False)
        self.assertTrue(second.controlsCollapsed)
        self.assertTrue(second.controlsLocked)
        self.assertTrue(second.infoCollapsed)
        self.assertTrue(second.statusCollapsed)
        second.setSectionExpanded("toolhead", True)
        self.assertEqual(second._sections["toolhead"], True)

    def test_a_refused_layer_says_so_instead_of_claiming_to_load(self):
        # The live report: the label stood on "Loading layer…" forever
        # with no way back — a refused layer was indistinct from one
        # still arriving. A payload that carries the refusal names it.
        model = self.monitor()
        model.setFollowerPopoverOpen(True)
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=7, count=12)
        self.assertEqual(model.plateProgressReason, "",
                         "a served layer carries no reason")
        base = coordinator._snapshot
        loading = {"layers": {"prev": None, "current": None, "next": None},
                   "split": None, "anchor": 7, "method": "unavailable",
                   "motionTotal": 0, "refusal": ""}
        coordinator._snapshot = harness.replace(base, plate_progress=loading)
        model._publish()
        self.assertFalse(model.plateProgressAvailable)
        self.assertEqual(model.plateProgressReason, "Loading layer…")
        # The same absent layer, but the service has said it will never
        # arrive: the label names the refusal instead of promising a
        # load that is not coming — and names WHICH refusal.
        for refusal, text in (("failed", "This layer failed to load."),
                              ("outside", "This layer is not in this file.")):
            coordinator._snapshot = harness.replace(
                base, plate_progress=dict(loading, refusal=refusal))
            model._publish()
            self.assertFalse(model.plateProgressAvailable)
            self.assertEqual(model.plateProgressReason, text,
                             "a refused layer must say so, not claim to be loading")

    def test_detaching_holds_the_layer_the_face_shows_and_reattaching_rejoins(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=7, count=12)
        model.setFollowerPopoverOpen(True)
        model.setFollowerAttached(False)
        self.assertFalse(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, 7)
        self.assertEqual(coordinator._plate_anchor, 7,
                         "the frozen layer's window was never asked for")
        # Detaching from the live layer seeds the scrub with the split
        # the face stood at — the frozen view never jumps (the live
        # report: a detach that changed nothing read as dead).
        self.assertEqual(coordinator._plate_split, 0,
                         "the detach did not seed the scrub with the live split")
        # A manual layer IS a detach: the face cannot follow the print
        # and hold another layer at once. The seek abandons the scrub —
        # the split belonged to the layer the face left.
        model.setFollowerLayerAnchor(3)
        self.assertFalse(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, 3)
        self.assertEqual(coordinator._plate_anchor, 3)
        # A seek lands at the FULL layer (the live request).
        self.assertEqual(coordinator._plate_split, -1)
        model.setFollowerAttached(True)
        self.assertTrue(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, -1)
        self.assertIsNone(coordinator._plate_anchor, "re-attaching never rejoined the print")
        self.assertIsNone(coordinator._plate_split, "re-attaching kept the scrub")

    def test_detaching_from_layer_zero_freezes_zero_not_missing(self):
        # The P0 zero-index bug: `0 or -1` read the first layer as
        # missing, so the detach was refused on layer 0 — a valid
        # zero must freeze like any other layer.
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=0, count=12, split=37)
        model.setFollowerPopoverOpen(True)
        model.setFollowerAttached(False)
        self.assertFalse(model.followerAttached,
                         "the detach on layer zero was refused")
        self.assertEqual(model.followerLayerAnchor, 0)
        self.assertEqual(coordinator._plate_anchor, 0,
                         "the frozen layer's window was never asked for")
        self.assertEqual(coordinator._plate_split, 37,
                         "the detach did not seed the scrub with the live split")
        # A fresh publish must not undo the freeze (the refusal path
        # used to flip the follower back to attached).
        self._with_layers(model, anchor=0, count=12, split=40)
        self.assertFalse(model.followerAttached,
                         "the republish re-attached the layer-zero detach")
        self.assertEqual(model.followerLayerAnchor, 0)
        self.assertEqual(coordinator._plate_anchor, 0)

    def test_reattaching_from_layer_zero_abandons_the_freeze(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=0, count=12, split=9)
        model.setFollowerPopoverOpen(True)
        model.setFollowerAttached(False)
        model.setFollowerAttached(True)
        self.assertTrue(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, -1)
        self.assertIsNone(coordinator._plate_anchor,
                          "re-attaching never rejoined the print")
        self.assertIsNone(coordinator._plate_split,
                          "re-attaching kept the scrub")

    def test_a_detach_with_no_layer_to_hold_is_refused(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        model.setFollowerAttached(False)
        self.assertTrue(model.followerAttached,
                        "the detach published a freeze the coordinator cannot serve")
        self.assertEqual(model.followerLayerAnchor, -1)
        self.assertIsNone(coordinator._plate_anchor)

    def test_an_indexed_print_can_detach_before_its_first_live_layer(self):
        model = self.monitor()
        model.setFollowerPopoverOpen(True)
        coordinator = self.follower._runtime.coordinator
        coordinator._snapshot = harness.replace(
            coordinator._snapshot, index_ready=True, plate_progress=None,
            plate_manual_progress=None, plate_layer_count=12)
        model._publish()
        self.assertEqual(model.plateProgressAnchor, -1)
        self.assertEqual(model.plateProgressReason,
                         "Print indexed — waiting for the print to reach an indexed layer.")
        requests = []
        model._request_plate_anchor = requests.append
        model.setFollowerAttached(False)
        self.assertFalse(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, 0)
        self.assertEqual(requests, [0])
        coordinator._snapshot = harness.replace(
            coordinator._snapshot,
            plate_manual_progress={"layers": {"prev": None, "current": {"classes": {}},
                                              "next": None},
                                   "split": None, "anchor": 0,
                                   "method": "unavailable", "motionTotal": 100})
        model._publish()
        self.assertTrue(model.plateProgressAvailable)
        self.assertEqual(model.plateProgressReason, "")
        self.assertEqual(model.plateProgressAnchor, 0)
        self.assertEqual(model.plateLayerMotionCount, 100)
        model.setFollowerLayerAnchor(3)
        self.assertEqual(model.followerLayerAnchor, 3)
        self.assertEqual(requests, [0, 3],
                         "the indexed print refused a manual layer seek")

    def test_detach_can_latch_during_the_ready_to_count_publish_gap(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        coordinator._snapshot = harness.replace(
            coordinator._snapshot, index_ready=True, plate_progress=None,
            plate_manual_progress=None, plate_layer_count=0)
        model._publish()
        self.assertTrue(model.printIndexReady)
        self.assertEqual(model.plateLayerCount, 0)
        requests = []
        model._request_plate_anchor = requests.append
        model.setFollowerAttached(False)
        self.assertFalse(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, 0)
        self.assertEqual(requests, [0])

    def test_a_collapsed_mini_section_freezes_the_live_payload(self):
        model = self.monitor()
        self._with_layers(model, anchor=7, count=12)
        self.assertEqual(model.plateLiveAnchor, 7)
        model.setSectionExpanded("plateprogress", False)
        self._with_layers(model, anchor=8, count=12)
        self.assertEqual(model.plateLiveAnchor, 7,
                         "the collapsed section took the fresh payload")
        model.setSectionExpanded("plateprogress", True)
        self.assertEqual(model.plateLiveAnchor, 8,
                         "expanding never resumed the live payload")

    def test_section_layout_persists_across_model_instances(self):
        # 4.4.0: the configure popups' committed reorder and hidden
        # set round-trip through the plugin-owned JSON file — a Cura
        # restart rehydrates the layout before any popup opens. The
        # fresh model's EFFECTIVE layout (sectionLayoutFor) reads the
        # stored order and the hidden set, never the pane default.
        model = self.monitor()
        order = ["job", "temps", "fansinfo", "filament", "systeminfo", "mcus"]
        self.assertIsNone(model.setSectionLayout("status", order, ["mcus"]))
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = harness.json.load(handle)
            self.assertEqual(payload["sectionLayout"], {"status": {"order": order, "hidden": ["mcus"]}})
        second = self.monitor()
        effective = second.sectionLayoutFor("status")
        self.assertEqual(effective["order"], order)
        self.assertEqual(effective["hidden"], ["mcus"])

    def test_resetting_one_pane_does_not_touch_anothers_layout(self):
        # The live report: the controls popup's reset-to-defaults
        # visibly reset the information pane's customised sections.
        # The model must keep every other pane's entry untouched —
        # the reset re-normalises the whole document with ONE pane's
        # entry cleared.
        model = self.monitor()
        info_order = list(self.qt.load("SectionLayoutPolicy").PANE_SECTION_ORDER["information"])
        custom = [info_order[1], info_order[0]] + info_order[2:]
        model.setSectionLayout("information", custom, [info_order[0]])
        model.setSectionLayout("controls", [], [])
        effective = model.sectionLayoutFor("information")
        self.assertEqual(effective["order"], custom)
        self.assertEqual(effective["hidden"], [info_order[0]])

    def test_console_height_persists_and_clamps_across_model_instances(self):
        # 3.6.0: the console's drag handle sets a pane height the model
        # owns. It round-trips through the plugin-owned JSON file (a Cura
        # restart rehydrates it before the pane exists), never hydrates
        # negative, and an unchanged height is not rewritten — a drag
        # riding its clamp must stop touching the disk.
        model_module = self.qt.load("MoonrakerMonitorModel")
        section_path = self.follower.persistence.state_global_path
        model = self.monitor()
        self.assertEqual(model.consoleHeight, 0)  # never dragged
        model.setConsoleHeight(240)
        self.assertEqual(model.consoleHeight, 240)
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(harness.json.load(handle)["consoleHeight"], 240)
        # A fresh model rehydrates it.
        second = self.monitor()
        self.assertEqual(second.consoleHeight, 240)
        # No negative height can ever land, whatever the drag reports.
        second.setConsoleHeight(-40)
        self.assertEqual(second.consoleHeight, 0)
        # Absurd values clamp to the model's ceiling (the pane bounds are
        # the QML's clamp — it is the only side that can see them).
        second.setConsoleHeight(999999)
        self.assertEqual(second.consoleHeight, model_module.CONSOLE_HEIGHT_MAX)
        with open(section_path, "r", encoding="utf-8") as handle:
            self.assertEqual(harness.json.load(handle)["consoleHeight"], model_module.CONSOLE_HEIGHT_MAX)
        # The unchanged-height pin: an unchanged height is not
        # rewritten — a drag riding its clamp must stop touching the
        # disk. The 4.5.0 store slot is the facade; the spy rides the
        # facade's global merge.
        with harness.patch.object(second._store, "merge_state_global",
                          wraps=second._store.merge_state_global) as write:
            second.setConsoleHeight(180)
            second.setConsoleHeight(180)
            self.assertEqual(write.call_count, 1)

    def test_a_sections_less_document_with_sibling_keys_is_not_a_flat_map(self):
        # The flat-map legacy shape is recognised ONLY when every value
        # is a bool: a document that lacks `sections` and carries the
        # UI-state store's sibling keys must not hydrate them as
        # sections (the silent collapse-state reset, 4.3.0).
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"sectionSizes": {"info": 240.0}, "setup": False}, handle)
        model = self.monitor()
        self.assertEqual(model._sections, {})

    def test_the_ui_state_store_owns_the_sections_writes(self):
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"sections": {"setup": False},
                       "controlsLocked": True,
                       "sectionSizes": {"info": 240.0}}, handle)
        model = self.monitor()
        model.setSectionExpanded("toolhead", False)
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = harness.json.load(handle)
        # The section write merged: the sibling keys survive untouched.
        self.assertEqual(payload["sections"], {"setup": False, "toolhead": False})
        self.assertTrue(payload["controlsLocked"])
        self.assertEqual(payload["sectionSizes"], {"info": 240.0})

    def test_the_store_delete_drops_only_the_named_keys(self):
        from mpf.settings.StateStore import StateStore
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            store = StateStore(path)
            store.write({"sections": {"setup": False}, "temperatureChart": {"visible": {}},
                         "controlsLocked": True})
            store.write({}, delete=("temperatureChart",))
            with open(path, "r", encoding="utf-8") as handle:
                payload = harness.json.load(handle)
            self.assertNotIn("temperatureChart", payload)
            self.assertEqual(payload["sections"], {"setup": False})
            self.assertTrue(payload["controlsLocked"])

    def test_the_full_chart_payload_hydrates_only_while_the_popover_is_open(self):
        # K (the 2026-09-19 review): closed serves the dormant empty
        # object — the full payload, its QVariant conversion and its
        # signal all stay asleep per feed; open hydrates the full
        # payload; closing returns it to dormancy on the very next
        # publish. The mini preview is a separate, bounded payload
        # that keeps serving while the pop-over is closed.
        model = self.monitor()
        self.feed_chart(model, {
            "extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
            "temperature_sensor chamber": {"temperature": 40.0},
        })
        self.assertEqual(self.chart_of(model)["series"], [], "closed: the full payload is dormant")
        mini = self.mini_of(model)
        self.assertLessEqual(len(mini["series"]), 2, "the mini payload carries only its own series")
        self.assertEqual(len(self.legend_of(model)["series"]), 3, "the legend keeps every series")
        model.setChartOpen(True)
        full = self.chart_of(model)
        self.assertEqual([s["name"] for s in full["series"] if s.get("points")],
                         ["extruder", "heater_bed", "temperature_sensor chamber"])
        model.setChartOpen(False)
        self.assertEqual(self.chart_of(model)["series"], [], "closing returns the full payload to dormancy")

    def test_the_full_payload_stays_dormant_across_ticks_while_closed(self):
        # The same dormant object across chart ticks: the STORED
        # value's identity is stable, so the full-chart property never
        # re-converts and its signal never fires while the pop-over is
        # closed. (The property read itself crosses QVariant, so the
        # identity is asserted on the stored value, not the read.)
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0, "target": 210.0}})
        fired = []
        model.temperatureChartFullChanged.connect(lambda: fired.append(1))
        self.assertEqual(self.chart_of(model)["series"], [])
        dormant = model._values["temperatureChartFull"]
        for tick in range(5):
            model._data._update(auxiliary={"extruder": {"temperature": 200.0 + tick}})
            model._on_chart_tick()
            self.qt.events()
        self.assertIs(model._values["temperatureChartFull"], dormant,
                      "a feed must not rebuild the closed full payload")
        self.assertEqual(fired, [], "the full-chart signal fired while closed")

    def test_toggles_hydrate_the_open_chart_immediately(self):
        # Targets/power toggled while the pop-over is ALREADY open
        # must hydrate on the toggle's own publish — never wait for
        # the next auxiliary sample. The history is seeded directly:
        # the harness's data lane drops target/power from injected
        # auxiliary (probe-proven, old code included), so the lane is
        # bypassed and the payload plumbing under test is the toggle's.
        model = self.monitor()
        # Two samples: a one-sample track never forms a drawable
        # segment (the lone-setpoint rule), and the toggle contract
        # needs a real one.
        model._temperature._history.observe({"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}},
                               1000.0, 1000000.0)
        model._temperature._history.observe({"extruder": {"temperature": 200.5, "target": 210.0, "power": 0.5}},
                               1002.5, 1000002.5)
        model._schedule_publish()
        self.qt.events(1)
        model.setShowTemperatureTargets(True)
        model.setShowTemperaturePower(True)
        model.setChartOpen(True)
        self.assertTrue(self.chart_of(model)["series"][0]["targets"])
        model.setShowTemperatureTargets(False)
        self.assertEqual(self.chart_of(model)["series"][0]["targets"], [])
        self.assertTrue(self.chart_of(model)["series"][0]["powers"])
        model.setShowTemperaturePower(False)
        self.assertEqual(self.chart_of(model)["series"][0]["powers"], [])
        model.setShowTemperatureTargets(True)
        self.assertTrue(self.chart_of(model)["series"][0]["targets"],
                        "re-enabling hydrates on the same toggle")

    def test_history_feeds_once_per_chart_tick_not_per_publish(self):
        model = self.monitor()
        self.deliver()  # the client's connect transition
        auxiliary = {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}}
        model._data._update(auxiliary=auxiliary)
        self.qt.events()
        self.assertEqual(len(self.mini_of(model)["series"]), 0,
                         "an aux arrival alone never feeds the chart")
        # Core-only publishes must not append samples either: the old
        # per-publish feed duplicated samples and halved the effective
        # window.
        for _ in range(5):
            model._data._update(core={"print_stats": {"state": "printing"}})
        self.assertEqual(len(self.mini_of(model)["series"]), 0)
        # The fixed 1 s tick samples the latest snapshot once.
        model._on_chart_tick()
        self.qt.events()
        self.assertEqual(len(self.mini_of(model)["series"][0]["points"]), 1)
        # A second tick appends exactly one more sample.
        model._on_chart_tick()
        self.qt.events()
        self.assertEqual(len(self.mini_of(model)["series"][0]["points"]), 2)

    def test_history_resets_when_the_session_is_invalidated(self):
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}})
        self.assertEqual(len(self.mini_of(model)["series"]), 1)
        model._data.set_owner_active(False)  # emits invalidated
        self.assertEqual(self.mini_of(model)["series"], [])
        # Every render cache empties with the reset — the legend and
        # the full payload included.
        self.assertEqual(self.legend_of(model)["series"], [])
        self.assertEqual(self.chart_of(model)["series"], [])

    def test_chart_setters_are_idempotent_and_validate(self):
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}})
        writes = []
        model._temperature._apply_chart_config = lambda: writes.append(1)
        # Re-applying the same value must not rewrite the state file
        # (the legend re-binds every second and used to re-save each
        # time).
        model.setTemperatureSensorVisible("extruder", True)
        model.setTemperatureSensorColor("extruder", "#d32f2f")  # the palette default: a no-op
        self.assertEqual(writes, [])
        model.setTemperatureSensorVisible("extruder", False)
        self.assertEqual(writes, [1])
        model.setTemperatureSensorVisible("extruder", False)
        self.assertEqual(writes, [1])
        # Invalid colours are rejected outright.
        model.setTemperatureSensorColor("extruder", "#fff")
        self.assertEqual(writes, [1])
        legend = self.legend_of(model)
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        self.assertEqual(extruder["color"], "#d32f2f")  # the palette default, unchanged
        model.setTemperatureSensorColor("extruder", "#123456")
        self.assertEqual(writes, [1, 1])

    def test_chart_config_prunes_vanished_sensors(self):
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0}})
        model.setTemperatureSensorColor("ghost_sensor", "#123456")
        # Any later change prunes keys for sensors no longer present —
        # but never while the live set is empty.
        model.setTemperatureSensorVisible("extruder", False)
        chart = self.follower.current_printer_config().temperature_chart
        self.assertNotIn("ghost_sensor", chart["colors"])
        self.assertEqual(chart["colors"], {})

    def test_chart_config_set_before_history_arrives_still_persists(self):
        # The suspicion: changing colours before the first aux
        # reply must survive — the history loads AFTER the config.
        model = self.monitor()
        model.setTemperatureSensorVisible("extruder", False)
        model.setTemperatureSensorColor("heater_bed", "#123456")
        self.assertEqual(self.follower.current_printer_config().temperature_chart,
                         {"visible": {"extruder": False}, "colors": {"heater_bed": "#123456"},
                          "showTargets": True, "showPower": True})
        self.feed_chart(model, {"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        legend = self.legend_of(model)
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        bed = next(item for item in legend["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")
        second = self.monitor()
        self.feed_chart(second, {"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        legend = self.legend_of(second)
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        bed = next(item for item in legend["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")

    def test_legacy_global_chart_block_migrates_into_the_per_printer_record(self):
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"sections": {"setup": False},
                       "temperatureChart": {"visible": {"extruder": False},
                                            "colors": {"heater_bed": "#123456"},
                                            "showTargets": False, "showPower": False}}, handle)
        model = self.monitor()
        self.feed_chart(model, {"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}})
        # The legacy block was adopted once into the per-printer record…
        self.assertEqual(self.follower.current_printer_config().temperature_chart, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        extruder = next(item for item in self.legend_of(model)["series"] if item["name"] == "extruder")
        bed = next(item for item in self.legend_of(model)["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")
        # …and the global file keeps chrome only afterwards.
        with open(section_path, "r", encoding="utf-8") as handle:
            payload = harness.json.load(handle)
        self.assertNotIn("temperatureChart", payload)

    def test_console_sends_scripts_and_persists_history_per_printer(self):
        model = self.monitor()
        model.sendConsoleCommand("  M104 S200  ")
        scripts = [request for request in self.transport.requests
                   if request.path == "printer/gcode/script"]
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].options["body"], {"script": "M104 S200"})
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, ["M104 S200"])
        # The TRANSCRIPT persists per printer, never in the global file
        # (the typed history is now derived from it).
        # The persisted record carries kind/text/error/success; the
        # controller stamps restored=True on load (everything loaded
        # predates this session — the pane greys it).
        self.assertEqual(self.stored_transcript()[-1],
                         {"kind": "command", "text": "M104 S200", "error": False, "success": False})
        second = self.monitor()
        self.assertEqual(second.consoleHistory, ["M104 S200"])
        # The pane list serves the transcript; restored lines carry the
        # stamped flag (everything persisted predates this session).
        self.assertEqual(second.consoleLines.value(), [{"kind": "command", "text": "M104 S200",
                                                        "error": False, "success": False, "restored": True}])

    def test_console_persist_keeps_commands_against_chatty_responses(self):
        # A chatty Klipper fills the 50-entry persist window with
        # responses; the newest commands must be retained in the
        # persisted record (the "none of my requests are
        # restored" report — the window had trimmed them away).
        model = self.monitor()
        model._console._transcript = [
            {"kind": "command", "text": "C1", "error": False, "success": False, "restored": False},
            *[{"kind": "response", "text": "B:%d.0" % i, "error": False, "success": False, "restored": False}
              for i in range(60)],
            {"kind": "command", "text": "C2", "error": False, "success": False, "restored": False},
        ]
        model._console._persist()
        stored = self.stored_transcript()
        commands = [entry["text"] for entry in stored if entry["kind"] == "command"]
        self.assertIn("C1", commands)
        self.assertIn("C2", commands)
        # The retained commands must survive the ROUND TRIP: the load
        # once trimmed the record back to MAX_TRANSCRIPT and cut the
        # commands at the front (the "my requests are missing
        # from the restore").
        second = self.monitor()
        restored = [entry["text"] for entry in second.consoleLines.value() if entry["kind"] == "command"]
        self.assertIn("C1", restored)
        self.assertIn("C2", restored)

    def test_console_persist_keeps_the_success_flag(self):
        # The restored "ok" renders green only if the success flag
        # survives the config cleaning (it was dropped once, greying
        # every restored response — the "never seen a
        # coloured line" report).
        model = self.monitor()
        model._console._transcript = [
            {"kind": "command", "text": "G28", "error": False, "success": False, "restored": False},
            {"kind": "response", "text": "ok", "error": False, "success": True, "restored": False},
        ]
        model._console._persist()
        stored = self.stored_transcript()
        self.assertEqual(stored[-1]["success"], True)

    def test_console_empty_input_and_clear_and_refused_sends(self):
        model = self.monitor()
        # The slot reports acceptance so the UI can keep the draft on
        # a refusal instead of destroying an unsent G-code line.
        self.assertFalse(model.sendConsoleCommand("   "))
        self.assertEqual(model.consoleHistory, [])
        # An empty Enter is simply nothing — no note, no banner (the
        # ruling: nothing sent carries no information).
        self.assertEqual(model.consoleLines.value(), [])
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, ["G28"])
        model.clearConsoleHistory()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, [])
        self.assertEqual(self.stored_transcript(), [])
        # A refused send (lane full / Moonraker down) explains itself
        # as a neutral "//" feed line and does not enter the history.
        model._console._data.request = lambda *args, **kwargs: False
        self.assertFalse(model.sendConsoleCommand("G1 X10"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consoleHistory, [])
        self.assertIn("try again", model.consoleLines.value()[-1]["text"])
        # The note is the plugin's own feed line — kind "note", not a
        # Moonraker response.
        self.assertEqual(model.consoleLines.value()[-1]["kind"], "note")
        self.assertFalse(model.consoleLines.value()[-1]["error"])

    def test_console_reloads_the_transcript_when_it_constructed_empty(self):
        # The plugin constructs the console before the active machine
        # exists, so the early load reads an empty record; by the time
        # the pane attaches the identity is real and the console must
        # re-load the per-printer transcript (the "completely
        # empty at app start" report).
        model = self.monitor()
        self.assertTrue(model.sendConsoleCommand("M104 S200"))
        model._console._transcript = []  # the early, empty construction
        model._console._loaded_identity = None  # the latch dies with it
        model.setConsoleExpanded(True)
        self.qt.events()  # the publish coalescer flushes on the next turn
        lines = model.consoleLines.value()
        self.assertTrue(any(entry["kind"] == "command" and entry["text"] == "M104 S200"
                            for entry in lines))

    def test_console_replay_of_the_record_keeps_commands(self):
        # The real record: 53 entries, 8 commands scattered,
        # 3 pinned at the head (the retention's shape). A session that
        # loads it, backfills responses and persists must NOT drop the
        # commands (the "it's just a bunch of responses").
        kinds = (["command"] * 3
                 + ["response"] * 12
                 + ["command", "response"] * 3
                 + ["response"] * 5
                 + ["command", "response"] * 2
                 + ["response"] * 8
                 + ["command", "response", "response", "response"])
        transcript = [{"kind": kind, "text": f"{kind}@{i}", "error": False,
                       "success": False, "restored": True}
                      for i, kind in enumerate(kinds)]
        model = self.monitor()
        model._console._transcript = [dict(entry) for entry in transcript]
        # A backfill of two fresh server responses arrives.
        model._console.append_responses([
            {"text": "B:55.0 /55.0", "error": False, "success": True,
             "time": model._console._store_time + 1.0},
            {"text": "// Unknown command:\"123\"", "error": False, "success": False,
             "time": model._console._store_time + 2.0},
        ])
        model._console._persist()
        stored = self.stored_transcript()
        commands = [entry for entry in stored if entry["kind"] == "command"]
        self.assertGreaterEqual(len(commands), 8)
        # The three head commands survive the window as the record head.
        self.assertEqual([entry["text"] for entry in stored[:3]],
                         ["command@0", "command@1", "command@2"])

    def test_console_send_verdict_colours_the_typed_line(self):
        # The send POST's own result is the execution verdict (the
        # endpoint returns "ok" on completion); the store feed cannot
        # pair, but this callback belongs to THIS request — the entry
        # is captured at send time, so identical commands in flight can
        # never swap verdicts.
        model = self.monitor()
        self.assertTrue(model.sendConsoleCommand("G28"))
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        scripts[0].callback({"result": "ok"}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertTrue(lines[0]["success"])
        self.assertFalse(lines[0]["error"])
        # A server ANSWER with an error body is a real refusal: red.
        self.assertTrue(model.sendConsoleCommand("M999"))
        self.scripts()[-1].callback({"error": {"message": "Command refused"}}, "Command refused")
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertTrue(lines[1]["error"])
        self.assertFalse(lines[1]["success"])
        # A transport-level failure (timeout, network) is NO verdict:
        # the command may still be executing — a client timeout must
        # never paint a running command red (the domain panel: blocking
        # commands legitimately outlast the 30 s client timeout). The
        # status note says so, honestly.
        self.assertTrue(model.sendConsoleCommand("M190 S60"))
        self.scripts()[-1].callback(None, "Connection timed out")
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertFalse(lines[2]["error"])
        self.assertFalse(lines[2]["success"])
        # The honest note lands as the plugin's own feed line.
        self.assertIn("may still be running", lines[-1]["text"])
        self.assertEqual(lines[-1]["kind"], "note")
        self.assertFalse(lines[-1]["error"])
        self.assertFalse(lines[-1]["success"])

    def test_console_verdicts_pair_by_captured_entry_not_text(self):
        # Two identical commands in flight: the older request's verdict
        # must land on the OLDER line, never on the newest twin with
        # the same text (the old text+recency scan swapped them).
        model = self.monitor()
        self.assertTrue(model.sendConsoleCommand("G28"))
        self.assertTrue(model.sendConsoleCommand("G28"))
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        # The OLDER request completes first, successfully.
        scripts[0].callback({"result": "ok"}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertTrue(lines[0]["success"])
        self.assertFalse(lines[1]["success"])
        # The NEWER request then fails at the server.
        scripts[1].callback({"error": {"message": "refused"}}, "refused")
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertFalse(lines[0]["error"])
        self.assertTrue(lines[1]["error"])
        self.assertFalse(lines[1]["success"])

    def test_fresh_tracked_outcome_survives_a_stale_receipt_timer(self):
        # The engineering panel's receipt resurrection: a macro banner
        # arms, then a tracked Pause completes inside the window — the
        # old "Macro sent" banner must never resurface over the fresh
        # terminal status (it did: send() never stopped the timer).
        model = self.monitor()
        self.deliver_state("standby")
        model._controls._macros = {"TEST_MACRO": "macro-name"}
        model.runMacro("TEST_MACRO", "")
        self.scripts()[0].callback(None, None)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.actionStatus, "Macro TEST_MACRO sent")
        model._commands.send("Pause", "printer/print/pause")
        self.qt.events(1)
        # The harness's command tracker delivers a "pending" event
        # synchronously on track; the live text resolves immediately.
        self.assertEqual(model.actionStatus, "Pause: pending")
        model._commands._command_changed({"name": "Pause", "outcome": "confirmed",
                                          "detail": "paused", "terminal": True})
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.actionStatus, "Pause: paused")
        self.qt.events(model._commands.RECEIPT_MS + 500)
        self.assertEqual(model.actionStatus, "Pause: paused")

    def test_mark_saved_only_claims_the_persisted_window(self):
        # The engineering panel's over-promise: mark_saved once greys
        # every live command "on disk", including entries beyond the
        # persisted window that die with the session. Only the window's
        # entries (last 50 + up to 10 pinned commands) may claim saved.
        model = self.monitor()
        model._console._transcript = [
            *[{"kind": "command", "text": "OLD%d" % i, "error": False, "success": False,
               "restored": False, "saved": False} for i in range(12)],
            *[{"kind": "response", "text": "B:%d.0" % i, "error": False,
               "success": False, "restored": False} for i in range(60)],
        ]
        model._console._persist()  # only a successful write may be claimed
        model._console.mark_saved()
        self.qt.events()  # the publish coalescer flushes on the next turn
        lines = model.consoleLines.value()
        self.assertFalse(lines[0]["saved"])  # OLD0: beyond the pin reach
        self.assertTrue(lines[2]["saved"])   # OLD2: pinned into the window

    def test_endstop_and_eta_surfaces(self):
        # The Improve-ETA action is a small download glyph beside the
        # Remaining value, not a full-width button row.
        improve = harness.JOB_SECTION_QML[harness.JOB_SECTION_QML.index('Qt.resolvedUrl("../resources/svg/Download.svg")'):harness.JOB_SECTION_QML.index("onClicked: root.printerModel.improveEta()")]
        self.assertIn("Download.svg", improve)
        self.assertNotIn("Improve ETA — download", harness.MONITOR_QML)
        for token in ("endstopItems", "endstopSummary",
                      "modelData.name + \": \" + modelData.state", "modelData.triggered"):
            self.assertIn(token, harness.TOOLHEAD_SECTION_QML)  # the readout lives in the Toolhead section
        self.assertNotIn('title: "Endstops"', harness.MONITOR_QML)
        self.assertNotIn('sectionId: "endstops"', harness.MONITOR_QML)
        # The empty-set copy lives in the projection, not the QML —
        # and it makes no causal claim (the live report: homed axes
        # with no endstop pins, e.g. sensorless homing, read "not
        # homed yet").
        self.assertIn("No endstop states reported", harness.FORMATTING)
        for token in ("endstopItems", "endstopSummary", "endstopsChanged",
                      "printer/query_endstops/status", "refresh_endstops"):
            self.assertIn(token, harness.MONITOR_MODEL + (harness.PLUGINS / "MonitorData.py").read_text(encoding="utf-8"))
        for token in ("improveEta()", "monitorEtaBasis === \"blend\"", "monitorEtaBasis === \"index\""):
            self.assertIn(token, harness.JOB_SECTION_QML)
        for token in ("monitorEtaBasis", "def improveEta(", "layer_eta", "remaining_end",
                      "request_monitor_download"):
            self.assertIn(token, harness.MONITOR_MODEL + (harness.PLUGINS / "MonitorFormatting.py").read_text(encoding="utf-8")
                          + (harness.PLUGINS / "PreviewFollower.py").read_text(encoding="utf-8") + (harness.PLUGINS / "PrintState.py").read_text(encoding="utf-8"))
        self.assertIn("confirmDownloadForMonitor", (harness.PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8"))

    def test_send_console_command_slot_registers_a_bool_for_qml(self):
        # Without result=bool the metaobject registers the slot as void
        # and QML receives undefined — falsy — so the console draft
        # would never clear on an accepted send and Enter would re-send
        # the same command. Python calls cannot see this; only the
        # registered metaobject can.
        model = self.monitor()
        meta = model.metaObject()
        method = meta.method(meta.indexOfMethod("sendConsoleCommand(QString)"))
        self.assertGreaterEqual(meta.indexOfMethod("sendConsoleCommand(QString)"), 0)
        self.assertEqual(method.typeName(), "bool")

    def test_improve_eta_downloads_for_the_monitor_without_a_preview_load(self):
        # The optimisation: the Monitor's Improve-ETA action
        # downloads and indexes the print WITHOUT loading it into the
        # preview; the index service pulls the file itself.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        # The capability gates on a configured binding, which needs an
        # attached machine — the harness applies config directly, so
        # simulate the attachment and re-apply the config against it.
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        self.qt.events(1)
        requests = [request for request in self.transport.requests if request.owner == "files"]
        self.assertTrue(requests)  # metadata pull for the index build

    def test_improve_eta_flips_to_hourglass_until_the_index_lands(self):
        # The glyph turns into a non-clickable hourglass while the
        # monitor-only download runs; the state ends when the snapshot
        # reports the index ready.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        # The latch is the contract: the harness's index fixture
        # reports ready, which zeroes the published value — the
        # hourglass flag itself must survive the stale publishes.
        self.assertTrue(model._improving_eta)
        # While the index builds the phase reads Indexing… and the bar
        # goes indeterminate (-1); then the index lands and the state
        # ends. The fake snapshot needs the full core_values shape —
        # the poll-driven publishes during teardown keep reading it.
        original = model._print_state
        model._print_state = lambda: harness.SimpleNamespace(
            layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=False,
            download_fraction=None, indexing=True, load_active=True,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False)
        model._publish()
        self.assertTrue(model.improvingEta)
        self.assertEqual(model.improveEtaPhase, "Indexing…")
        self.assertEqual(model.improveEtaProgress, -1.0)
        model._print_state = lambda: harness.SimpleNamespace(
            layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=True,
            download_fraction=None, indexing=False, load_active=False,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False)
        model._publish()
        self.assertFalse(model.improvingEta)
        model._print_state = original

    def test_publish_coerces_every_optional_snapshot_field(self):
        # The live crash: an Optional snapshot field fed None into a
        # typed value_property raised "unable to convert a Python
        # 'NoneType' object to a C++ 'double' instance" every poll.
        # Every Optional rides a sentinel through the values dict —
        # this test feeds None for ALL of them at once.
        model = self.monitor()
        original = model._print_state
        model._print_state = lambda: harness.SimpleNamespace(
            layer=harness.SimpleNamespace(index=None, total=None, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=False,
            download_fraction=None, indexing=False, load_active=False,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False,
            filament_total=None)
        model._publish()
        self.assertEqual(model.nextPauseFraction, -1.0)
        self.assertEqual(model.nextPauseLayer, -1)
        self.assertFalse(model.nextPauseBaked)
        self.assertEqual(model.improveEtaProgress, -1.0)
        self.assertFalse(model.improvingEta)
        model._print_state = original

    def test_improve_eta_hourglass_ends_when_the_download_fails(self):
        # Panel P1-1: a failed monitor-only download used to strand the
        # hourglass forever (_monitor_requested only cleared when the
        # index landed or the BUILD failed — the download's error phase
        # was not terminal, the retry ladder is consumer-driven, and the
        # disabled glyph removed the only retry affordance). Now the
        # files-service error phase is terminal and the model's flag
        # clears as soon as nothing is in flight.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        # The identity change now REBINDS the namespace stores (the
        # budget-follow finding): the service's job resets with them,
        # and the next status frame re-establishes it — exactly the
        # production order for an identity resolution.
        self.deliver_state("printing")
        self.qt.events(1)
        model.improveEta()
        self.qt.events(1)
        self.qt.events()  # the publish coalescer flushes on the next turn
        # The LATCH is the contract here: the harness's index fixture
        # reports ready, which zeroes the published value, but the
        # hourglass latch must survive every stale publish until the
        # coordinator's own termination (the coalescer exposed the
        # premature clear).
        self.assertTrue(model._improving_eta)
        files = [request for request in self.transport.requests if request.owner == "files"]
        self.assertTrue(files)
        for request in files:
            request.callback(None, "boom")
        self.deliver_state("printing")  # refresh recomputes the snapshot
        self.qt.events(1)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertFalse(model._improving_eta)
        coordinator = self.follower._runtime.coordinator
        self.assertFalse(coordinator._loads.monitor_requested)
        # The glyph stays the retry affordance: the QML no longer gates
        # it on the busy flag.
        self.assertNotIn("!root.printer.improvingEta", harness.MONITOR_QML)

    def test_binding_reset_clears_the_monitor_download_flag(self):
        # Panel P1-1 (session-interruption wedge): reset_binding must
        # not leak _monitor_requested into the next binding.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        model.improveEta()
        coordinator = self.follower._runtime.coordinator
        self.assertTrue(coordinator._loads.monitor_requested)
        coordinator.reset_binding()
        self.assertFalse(coordinator._loads.monitor_requested)
        model._publish()  # the model clears its flag on the next snapshot
        self.assertFalse(model.improvingEta)

    def test_metadata_fetch_latches_after_success_and_retries_after_failure(self):
        # A successful fetch is terminal for the job; a same-name
        # RESTART with a failed fetch never serves the previous job's
        # payload and retries after the throttle window — reachable
        # without poking private state (the old test's `_mr_meta = {}`
        # poke was itself proof the retry was unreachable in
        # production).
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client

        def deliver(position, duration=30):
            status = {
                "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": duration,
                                "info": {"current_layer": 2, "total_layer": 20}},
                "virtual_sdcard": {"file_size": 100, "file_position": position},
                "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                               "absolute_coordinates": True},
                "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
            }
            client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())

        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0)
        # The coordinator's clock is patched from the START: the fetch
        # timestamps must live on the same fake clock as the retry
        # window, or the real/fake mix blocks the throttle forever.
        with patch.object(module, "time", fake_time):
            deliver(20)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 1)
            meta[0].callback({"result": {"layer_height": 0.2, "estimated_time": 3600}}, None)
            self.qt.events(1)
            # A null job id needs no history cross-check.
            self.assertEqual([r for r in self.transport.requests if r.channel == "mr-history"], [])
            self.assertEqual(coordinator._mr_meta_key, ("part.gcode", coordinator._files.job_key))
            for step in range(3):
                tick[0] += 31.0
                deliver(20 + step)  # each delivery differs so the poll always refreshes
                self.qt.events(1)
            self.assertEqual(len([r for r in self.transport.requests if r.channel == "metadata-only"]), 1)
            # A same-name restart (the duration reset is a new job):
            # its failed fetch never latches, the old payload is never
            # served, and the retry fires on its own after the window.
            deliver(5, duration=5)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 2)
            meta[1].callback({}, "boom")
            self.qt.events(1)
            self.assertEqual(coordinator._mr_metadata_for("part.gcode", coordinator._files.job_key), {})
            tick[0] += 31.0
            deliver(6, duration=5)
            self.qt.events(1)
            self.assertEqual(len([r for r in self.transport.requests if r.channel == "metadata-only"]), 3)

    def test_metadata_with_job_id_cross_checks_the_current_print(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client

        def deliver(duration):
            status = {
                "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": duration,
                                "info": {"current_layer": 2, "total_layer": 20}},
                "virtual_sdcard": {"file_size": 100, "file_position": 20},
                "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                               "absolute_coordinates": True},
                "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
            }
            client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())

        with patch.object(module, "time", SimpleNamespace(monotonic=lambda: 1000.0, time=lambda: 1700000000.0)):
            deliver(30)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 1)
            meta[0].callback({"result": {"layer_height": 0.2, "estimated_time": 3600, "job_id": "1A2B"}}, None)
            self.qt.events(1)
            history = [r for r in self.transport.requests if r.channel == "mr-history"]
            self.assertEqual(len(history), 1)
            # The newest history row matches: the payload latches.
            history[0].callback({"result": {"count": 1, "jobs": [{"job_id": "1A2B", "status": "in_progress"}]}}, None)
            self.qt.events(1)
            key = ("part.gcode", coordinator._files.job_key)
            self.assertEqual(coordinator._mr_meta_key, key)
            self.assertEqual(coordinator._mr_metadata_for(*key).get("layer_height"), 0.2)
            # A same-name restart whose row mismatches never latches.
            deliver(5)
            self.qt.events(1)
            meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
            self.assertEqual(len(meta), 2)
            meta[1].callback({"result": {"layer_height": 0.3, "job_id": "9Z9Z"}}, None)
            self.qt.events(1)
            history = [r for r in self.transport.requests if r.channel == "mr-history"]
            self.assertEqual(len(history), 2)
            history[1].callback({"result": {"count": 1, "jobs": [{"job_id": "1A2B", "status": "finished"}]}}, None)
            self.qt.events(1)
            new_key = ("part.gcode", coordinator._files.job_key)
            self.assertEqual(coordinator._mr_metadata_for(*new_key), {})

    def test_metadata_cross_check_refuses_a_mismatched_job_without_latching(self):
        # The bounded give-up (4.3.0): a cross-check that can never
        # pass (the history stays empty) is silent and permanent
        # otherwise — after MR_META_CHECK_LIMIT failures for the same
        # key the payload latches with the failure flagged in the log.
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client
        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0)

        def deliver(duration):
            status = {
                "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": duration,
                                "info": {"current_layer": 2, "total_layer": 20}},
                "virtual_sdcard": {"file_size": 100, "file_position": 20},
                "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                               "absolute_coordinates": True},
                "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
            }
            client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())

        with patch.object(module, "time", fake_time):
            deliver(30)
            self.qt.events(1)
            for step in range(coordinator.MR_META_CHECK_LIMIT):
                meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
                meta[-1].callback({"result": {"layer_height": 0.2, "job_id": "1A2B"}}, None)
                self.qt.events(1)
                history = [r for r in self.transport.requests if r.channel == "mr-history"]
                # A MISMATCHED job id (not an empty history — an empty
                # history is unattestable and give-up-able by the
                # contract): the refusal is permanent.
                history[-1].callback({"result": {"count": 1, "jobs": [{"job_id": "DIFFERENT"}]}}, None)
                self.qt.events(1)
                key = ("part.gcode", coordinator._files.job_key)
                if step < coordinator.MR_META_CHECK_LIMIT - 1:
                    self.assertEqual(coordinator._mr_metadata_for(*key), {})
                    tick[0] += 31.0
                    deliver(31 + step)  # each delivery differs so the poll refreshes
                    self.qt.events(1)
            # A mismatched job id is PROOF the payload describes a
            # different job — the give-up must never latch it (the
            # identity bleed the cross-check exists to prevent). The
            # anchors stay empty for the whole print.
            self.assertEqual(coordinator._mr_metadata_for(*key), {})

    def test_metadata_cross_check_gives_up_only_for_unattestable_replies(self):
        # The bounded give-up applies to the causes that cannot ATTEST
        # (the history request failed, the reply was unattestable) —
        # after the limit the payload latches with the failure flagged.
        from types import SimpleNamespace
        from unittest.mock import patch
        import sys
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        module = sys.modules[type(coordinator).__module__]
        client = self.follower.client
        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0)
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1,
                           "extrude_factor": 1, "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        with patch.object(module, "time", fake_time):
            client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())
            self.qt.events(1)
            for step in range(coordinator.MR_META_CHECK_LIMIT):
                meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
                meta[-1].callback({"result": {"layer_height": 0.2, "job_id": "1A2B"}}, None)
                self.qt.events(1)
                history = [r for r in self.transport.requests if r.channel == "mr-history"]
                history[-1].callback(None, "boom")
                self.qt.events(1)
                key = ("part.gcode", coordinator._files.job_key)
                if step < coordinator.MR_META_CHECK_LIMIT - 1:
                    self.assertEqual(coordinator._mr_metadata_for(*key), {})
                    tick[0] += 31.0
                    client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())
                    self.qt.events(1)
            # After the limit: the payload latched, flagged.
            self.assertEqual(coordinator._mr_metadata_for(*key).get("layer_height"), 0.2)

    def test_metadata_reply_after_reset_never_latches(self):
        # A reply landing after a binding reset must not latch the old
        # job's payload (the stale-request guard) — and must not fire
        # a pointless history cross-check.
        self.monitor()
        coordinator = self.follower._runtime.coordinator
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())
        self.qt.events(1)
        meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
        self.assertEqual(len(meta), 1)
        coordinator.reset_binding()
        meta[0].callback({"result": {"layer_height": 0.2, "estimated_time": 3600, "job_id": "1A2B"}}, None)
        self.qt.events(1)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_metadata_for("part.gcode", ("part.gcode", 100, 1)), {})
        self.assertEqual([r for r in self.transport.requests if r.channel == "mr-history"], [])

    def test_metadata_request_keeps_subfolder_slashes(self):
        # Panel DOM-P2-3: the coordinator's URL escaped subfolder
        # separators to %2F while MoonrakerProtocol.metadata_endpoint
        # does not; picky proxies 404 the escaped form.
        self.monitor()
        client = self.follower.client
        status = {
            "print_stats": {"filename": "PLA/part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())
        self.qt.events(1)
        meta = [r for r in self.transport.requests if r.channel == "metadata-only"]
        self.assertEqual(len(meta), 1)
        self.assertIn("PLA/part.gcode", meta[0].path)
        self.assertNotIn("%2F", meta[0].path)

    def test_one_core_landing_produces_at_most_one_publish(self):
        # G (the 2026-09-19 performance review): one data.changed
        # fans out through controls/toolhead/camera/console changes
        # into three or four full model projections. After the
        # coalescer, one core landing must publish at most once.
        # The counter patches the RUNTIME's class before construction:
        # the collaborator signals bind _publish at connect time, so
        # an instance patch would let the bound methods slip past it,
        # and the harness's package namespace is the only class
        # object the runtime uses.
        from unittest.mock import patch
        model_class = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel
        publishes = []
        original = model_class._publish
        def counting(self):
            publishes.append(1)
            return original(self)
        with patch.object(model_class, "_publish", counting):
            self.monitor()
            self.qt.events(1)
            self.deliver_state("standby")  # warm-up: the transition publishes
            self.qt.events(2)
            publishes.clear()
            # A representative HEARTBEAT: the same state again — the
            # observers run but nothing outward changed, so the whole
            # landing must collapse into one publish.
            self.deliver_state("standby")
            self.qt.events(2)
        self.assertGreaterEqual(len(publishes), 1, "the landing still publishes")
        self.assertLessEqual(len(publishes), 1,
                             "one core landing fans out into %d publishes" % len(publishes))

    def test_warm_heartbeats_make_no_persistence_reads(self):
        # H (the 2026-09-19 performance review): after hydration, the
        # monitor heartbeat must not parse settings.json or the
        # machine shard — the migration record is cached, the binding
        # serves its cached config, and an empty-but-loaded console
        # must not re-read its shard forever.
        from unittest.mock import patch
        model = self.monitor()
        self.qt.events(1)
        # Hydrate the console (an empty transcript is authoritative
        # once loaded) before the counting window.
        model.setConsoleExpanded(True)
        self.qt.events(1)
        state_store = self.qt.load("PluginPersistence").StateStore
        reads = []
        original = state_store.read
        def counting_read(self):
            reads.append(1)
            return original(self)
        with patch.object(state_store, "read", counting_read):
            for _ in range(20):
                self.deliver_state("standby")
                self.qt.events(1)
            for i in range(5):
                model._data._merge_aux({"extruder": {"temperature": 200.0 + i, "target": 210.0}})
                self.qt.events(1)
        self.assertEqual(reads, [], "warm heartbeats must not read the persistence files")

    def test_one_auxiliary_landing_produces_at_most_one_publish(self):
        # G: an auxiliary landing additionally fires auxiliaryChanged
        # -> _on_auxiliary -> _publish() on top of the changed
        # fanout; the coalescer must collapse the whole landing.
        from unittest.mock import patch
        model_class = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel
        publishes = []
        original = model_class._publish
        def counting(self):
            publishes.append(1)
            return original(self)
        with patch.object(model_class, "_publish", counting):
            model = self.monitor()
            self.qt.events(1)
            publishes.clear()
            model._data._merge_aux({"extruder": {"temperature": 200.0, "target": 210.0}})
            self.qt.events(2)
        self.assertGreaterEqual(len(publishes), 1, "the landing still publishes")
        self.assertLessEqual(len(publishes), 1,
                             "one auxiliary landing fans out into %d publishes" % len(publishes))

    def test_gcode_store_feed_appends_klippers_output_without_duplicates(self):
        # The console echo (the ruling): the store is polled
        # ONLY while the console is expanded, Klipper's response entries
        # land in the transcript feed, "!!" lines carry the error flag,
        # commands from the store are ignored (ours are already in the
        # pane), and a re-poll never repeats an entry.
        model = self.monitor()
        self.qt.events(1)
        self.assertEqual([r for r in self.transport.requests if r.channel == "console-store"], [])
        # The store polls at 1 s while PRINTING; an idle printer gets
        # the idle floor (5 s), so seed a printing state for the 1 s
        # cadence this test pumps.
        self.deliver_state("printing")
        model.setConsoleExpanded(True)
        store = [r for r in self.transport.requests if r.channel == "console-store"]
        self.assertEqual(len(store), 1)
        self.assertIn("server/gcode_store", store[0].path)
        store[0].callback({"result": {"gcode_store": [
            {"message": "M104 S200", "type": "command", "time": 9.0},
            {"message": "ok", "type": "response", "time": 10.0},
            {"message": "!! Heater extruder not heating", "type": "response", "time": 11.0},
        ]}}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertEqual([entry["text"] for entry in lines if entry["kind"] != "note"],
                         ["ok", "!! Heater extruder not heating"])
        feed = [entry for entry in lines if entry["kind"] != "note"]
        self.assertFalse(feed[0]["error"])
        self.assertTrue(feed[0]["success"])
        self.assertTrue(feed[1]["error"])
        self.assertFalse(feed[1]["success"])
        # The next poll repeats the old entries with one new line: the
        # last-seen stamp dedups and only the new line lands. (1200 ms:
        # the 1 s timer was started a hair before this pump, so a
        # 1000 ms window can end just short of its due point.)
        self.qt.events(1200)
        later = [r for r in self.transport.requests if r.channel == "console-store"][1:]
        self.assertTrue(later)
        later[-1].callback({"result": {"gcode_store": [
            {"message": "ok", "type": "response", "time": 10.0},
            {"message": "!! Heater extruder not heating", "type": "response", "time": 11.0},
            {"message": "Target reached", "type": "response", "time": 12.0},
        ]}}, None)
        self.qt.events(1)
        lines = model.consoleLines.value()
        self.assertEqual([entry["text"] for entry in lines if entry["kind"] != "note"],
                         ["ok", "!! Heater extruder not heating", "Target reached"])
        # The transcript persists with the responses (after the
        # response-churn debounce's window).
        model._console._flush_debounced()
        transcript = self.stored_transcript()
        self.assertEqual([entry["text"] for entry in transcript], ["ok", "!! Heater extruder not heating", "Target reached"])
        # The store holds Klipper's output VERBATIM — Moonraker strips
        # nothing (data_store.py stores the payload as delivered) — and
        # modern Klipper's response lines carry no "ok" prefix at all
        # (the "ok" is the RPC result, never console output). So the
        # store can never attest success: a line that is neither "!!"
        # nor a "//" echo is inferred success.
        self.qt.events(1200)
        later = [r for r in self.transport.requests if r.channel == "console-store"][1:]
        later[-1].callback({"result": {"gcode_store": [
            {"message": "B:55.0 /55.0 T0:200.3 /200.0", "type": "response", "time": 13.0},
            {"message": "// Unknown command:\"HELLO\"", "type": "response", "time": 14.0},
        ]}}, None)
        self.qt.events(1)
        lines = [entry for entry in model.consoleLines.value() if entry["kind"] != "note"][-2:]
        self.assertTrue(lines[0]["success"])
        self.assertFalse(lines[0]["error"])
        self.assertFalse(lines[1]["success"])
        self.assertFalse(lines[1]["error"])

    def test_console_store_seed_skips_the_stale_buffer_beyond_the_first_poll(self):
        # The expand seed is one-shot by design — but the entries it
        # skips must stay skipped for the whole session. The regression:
        # the second poll re-delivered Moonraker's entire stale buffer
        # (a live report of the console re-fetching the
        # printer's history on load).
        model = self.monitor()
        self.deliver_state("printing")
        model._data.set_console_expanded(True, 11.0)
        store = [r for r in self.transport.requests if r.channel == "console-store"]
        self.assertEqual(len(store), 1)
        store[0].callback({"result": {"gcode_store": [
            {"message": "old one", "type": "response", "time": 10.0},
            {"message": "old two", "type": "response", "time": 11.0},
            {"message": "fresh", "type": "response", "time": 12.0},
        ]}}, None)
        self.qt.events(1)
        # Plugin notes ("# Connected…") may interleave the feed — the
        # store feed itself must be exactly the fresh line.
        self.assertEqual([entry["text"] for entry in model.consoleLines.value() if entry["kind"] != "note"], ["fresh"])
        # The next poll repeats the same buffer: nothing new may land.
        self.qt.events(1200)
        later = [r for r in self.transport.requests if r.channel == "console-store"][1:]
        self.assertTrue(later)
        later[-1].callback({"result": {"gcode_store": [
            {"message": "old one", "type": "response", "time": 10.0},
            {"message": "old two", "type": "response", "time": 11.0},
            {"message": "fresh", "type": "response", "time": 12.0},
        ]}}, None)
        self.qt.events(1)
        self.assertEqual([entry["text"] for entry in model.consoleLines.value() if entry["kind"] != "note"], ["fresh"])

    def test_sweep_phase_advances_on_the_real_engine(self):
        # The sweep's position is a binding on the bar's sweepPhase; a
        # bare unqualified reference did NOT resolve through the visual
        # parent (ReferenceError, sweep frozen). Instantiate the exact
        # pattern on the real engine and pin that the phase moves.
        from PyQt6.QtQml import QQmlEngine, QQmlComponent
        from PyQt6.QtCore import QUrl
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData("""
import QtQuick 2.15
Item {
    id: probeRoot
    width: 300
    height: 40
    property bool improving: true
    property real progress: -1
    Item {
        id: probeBar
        objectName: "probeBar"
        anchors.fill: parent
        property real sweepPhase: 0
        NumberAnimation on sweepPhase {
            running: probeRoot.improving && probeRoot.progress < 0
            from: 0
            to: 1
            duration: 1000
            loops: Animation.Infinite
        }
        Rectangle {
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: parent.width / 3
            visible: probeRoot.improving && probeRoot.progress < 0
            x: (1 - Math.abs(2 * probeBar.sweepPhase - 1)) * (parent.width - width)
        }
    }
}
""".encode(), QUrl("sweep-pin.qml"))
        self.assertFalse(component.isError(), [e.toString() for e in component.errors()])
        item = component.create()
        self.assertIsNotNone(item)
        from PyQt6.QtCore import QObject
        bar = item.findChild(QObject, "probeBar")
        before = bar.property("sweepPhase")
        # The harness pumps a timed event loop; 300 ms of animation
        # must move the phase.
        self.qt.events(300)
        after = bar.property("sweepPhase")
        self.assertNotEqual(after, before)  # the phase advances

    def test_bed_mesh_visibility_signal_chain_toggles_and_publishes(self):
        # The "Hide bed mesh does nothing in the empty
        # preview" report: the overlay's signal was never connected to the
        # presentation (pre-3.4.0 regression). Pin the presenter side
        # of the chain — the signal must flip the flag and republish.
        mesh = self.follower._runtime.bed_mesh
        presentation = self.follower._runtime.presentation
        self.assertTrue(mesh.visible)
        presentation.bedMeshVisibilityRequested.emit(False)
        self.assertFalse(mesh.visible)
        self.assertFalse(presentation._values.get("bedMeshVisible"))
        presentation.bedMeshVisibilityRequested.emit(True)
        self.assertTrue(mesh.visible)

    def test_preview_load_lights_the_monitor_improving_state(self):
        # The shared load state: a load kicked off from the PREVIEW
        # must show the Monitor's hourglass too (the sync
        # report), and both clear when the index lands.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        coordinator = self.follower._runtime.coordinator
        coordinator.request_load()
        coordinator.refresh()
        model._publish()
        self.assertTrue(model.improvingEta)
        self.assertEqual(model.improveEtaPhase, "Resolving…")
        # The index lands: both surfaces clear.
        model._print_state = lambda: harness.SimpleNamespace(
            layer=harness.SimpleNamespace(index=0, total=0, source="", thickness=None),
            estimated_time=None, metadata_complete=False, layer_eta=None,
            layer_progress=None, index_ready=True,
            download_fraction=None, indexing=False, load_active=False,
            index_fraction=None,
            next_pause_layer=None, next_pause_eta="",
            next_pause_fraction=None, next_pause_baked=False)
        model._publish()
        self.assertFalse(model.improvingEta)

    def test_preview_load_state_is_busy_until_terminal(self):
        # The Load current print button must stay disabled through the
        # whole download+index+render; the presentation carries the
        # busy state for the indicator.
        self.monitor()
        self.deliver_state("printing")
        self.qt.events(1)
        self.follower._runtime.binding._machine_id = "printer-a"
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        coordinator = self.follower._runtime.coordinator
        presentation = self.follower._runtime.presentation
        coordinator.request_load()
        coordinator.refresh()
        self.assertTrue(presentation._values.get("loadBusy"))
        self.assertIn("Resolving current print…", presentation._values.get("loadPhase", ""))

    def test_load_request_clears_when_no_print_exists(self):
        # The stuck-"Resolving" report: a standby printer sends no
        # status frame, so observe() never clears the request — the
        # refresh-side clearing settles it from the known snapshot
        # state once the request is past its grace.
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        coordinator = self.follower._runtime.coordinator
        presentation = self.follower._runtime.presentation
        coordinator.request_load()
        coordinator._loads._load_requested_at = 0.0  # an aged request
        coordinator.refresh()
        self.assertFalse(coordinator._loads.load_requested)
        self.assertFalse(presentation._values.get("loadBusy"))
        self.assertNotIn("Resolving current print…", presentation._values.get("loadPhase", ""))
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_preview_load_feedback_surfaces(self):
        card = (harness.PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
        indicator = (harness.PLUGINS / "LoadProgressIndicator.qml").read_text(encoding="utf-8")
        self.assertIn("enabled: !base.loadBusy", card)
        self.assertIn("LoadProgressIndicator {", card)
        # Declared on the root: undeclared dynamic names read as
        # undefined at load time and the bindings were dropped.
        self.assertIn("property bool loadBusy: false", card)
        self.assertIn("property real loadProgress: -1", card)
        self.assertIn("property string loadPhase: \"\"", card)
        # The Attach/Detach button HIDES without a toolpath (the
        # 2026-09-17 ruling — the follower has nothing to drive),
        # and the load button takes the whole row then.
        self.assertIn("visible: base.hasToolpath", card)
        self.assertIn("enabled: base.followingEnabled || base.followingPaused", card)
        self.assertIn("width: base.hasToolpath ? buttons.width - base.buttonSpacing - followButton.width : buttons.width", card)
        self.assertIn("indicatorBar.sweepPhase", indicator)
        self.assertIn("busy: false", indicator)
        presentation = (harness.PLUGINS / "PreviewPresentation.py").read_text(encoding="utf-8")
        self.assertIn('("bedMeshVisibilityRequested", self.bedMeshVisibilityRequested.emit)', presentation)
        dialog = (harness.PLUGINS / "MoonrakerUploadDialog.qml").read_text(encoding="utf-8")
        # Enter resolves through the dialog's own accepted signal; the
        # unresolved-close wedge heals in the device's requestWrite.
        self.assertIn("onAccepted: {", dialog)
        self.assertIn("onClicked: base.accept()", dialog)
        device = (harness.PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        self.assertIn("A closed-but-unresolved dialog resets here", device)

    def test_console_qml_surface(self):
        for token in ("id: consoleSection", '"G-code command…"', "sendConsoleCommand(",
                      "clearConsoleHistory()", 'text: "Console"',
                      "Keys.onReturnPressed", "Keys.onUpPressed", "Keys.onDownPressed",
                      '"monospace"', "id: consoleText",
                      "consoleRecallIndex", "consoleDraft",
                      # The pane is ONE rich TextEdit (multi-line
                      # selection) with the terminal-feed colours and
                      # the append-with-selection-restore sync; the face
                      # itself is picked at runtime (monoFamily pin).
                      "consoleSyncLines", "consoleLineHtml",
                      "textFormat: TextEdit.RichText", "selectionStart",
                      "wasAtEnd", "consoleFlick",
                      # Appends must land on fresh lines, the ring
                      # rotation must rebuild, and the rebuild SETS the
                      # document directly (clear+insert produced an
                      # empty pane in the real engine).
                      "consoleText.text = html",
                      'html = "<br>" + html',
                      "consoleDroppedSeen",
                      "consoleRevisionsSeen",
                      "root.printer.setConsoleExpanded(expanding)",
                      'setConsoleExpanded(root.printer.sectionExpandedMap["console"] !== false)',
                      # The console is a collapsing pane beneath the
                      # webcam: a top-left chevron toggle, a "Console"
                      # title in the panes' style, and the camera fills
                      # the pane only while it is collapsed.
                      'sectionExpandedMap["console"]',
                      # The toggle keeps the other panes' button
                      # style with the theme's up/down chevrons inside
                      # it (the rulings).
                      "ChevronSingleUp",
                      "ChevronSingleDown",
                      "fixedWidthMode: true",
                      "consoleCollapseButton",
                      # Terminal ethics: follow the tail ONLY while at it
                      # and not selecting.
                      "consoleLines", "selectByMouse",
                      "server/gcode_store?count=100"):
            self.assertIn(token, harness.MONITOR_QML + (harness.PLUGINS / "MonitorData.py").read_text(encoding="utf-8"))
        # The input row's hit-region contract (the 5.11/5.12 sweep): the
        # field shrinks and clips INSIDE its own cell, so Send and Clear
        # keep theirs and the presses aimed at them land on them.
        input_cell = harness.MONITOR_QML[harness.MONITOR_QML.index('objectName: "moonrakerConsoleInput"'):
                                 harness.MONITOR_QML.index('objectName: "moonrakerConsoleSend"')]
        self.assertIn("Layout.minimumWidth: 0", input_cell)
        self.assertIn("clip: true", input_cell)
        self.assertIn('objectName: "moonrakerConsoleClear"', harness.MONITOR_QML)
        # The webcam pane's title moved with the card (CameraPane.qml).
        self.assertIn('text: "Webcam"', harness.CAMERA_PANE_QML)
        # The poll gate opens on printer attach — never wired to the
        # info pane's collapse (infoCollapsed defaults to false, which
        # left the feed dead in the default layout). The collapse
        # handlers that exist (4.4.0) drive ONLY the readout fits —
        # their bodies are pinned to the update calls.
        self.assertNotIn("setConsoleExpanded(!root.infoCollapsed)", harness.MONITOR_QML)
        self.assertIn("onInfoCollapsedChanged: {", harness.MONITOR_QML)
        self.assertIn("Qt.callLater(root.updateInfoReadoutFits)", harness.MONITOR_QML)
        self.assertIn("fitInfoRetry.restart()", harness.MONITOR_QML)
        self.assertIn("onStatusCollapsedChanged: {", harness.MONITOR_QML)
        self.assertIn("Qt.callLater(root.updateStatusReadoutFits)", harness.MONITOR_QML)
        self.assertIn("fitStatusRetry.restart()", harness.MONITOR_QML)
        # The feed's three voices (the live rulings):
        # commands carry ">", Moonraker's responses carry "<", and the
        # plugin's notes carry "#" in amber. Responses render bright
        # red/green; saved commands keep their text light grey and put
        # the verdict on the ">" prompt only — green matches the input
        # row's prompt, red is a failure — and the restored hues are
        # contrast-bumped (the old muted family sat near 2:1).
        for token in ('MoonrakerTheme.errorRed', 'MoonrakerTheme.consoleSuccess', 'MoonrakerTheme.consolePromptError', 'MoonrakerTheme.successGreen', 'MoonrakerTheme.consoleWarn',
                      '&gt; "', '&lt; "', '# "', 'MoonrakerTheme.consoleCommand', 'MoonrakerTheme.consoleHistoryError', 'MoonrakerTheme.consoleHistorySuccess'):
            self.assertIn(token, harness.MONITOR_QML)
        # The selection is captured BEFORE the rebuild wipe (the old
        # order made the restore a silent no-op); the rotation rebuild
        # compensates the content dropped above the viewport.
        self.assertLess(harness.MONITOR_QML.index("var selStart"), harness.MONITOR_QML.index("consoleDroppedSeen = dropped;"))
        for token in ("prevTextHeight", "rotationDrop"):
            self.assertIn(token, harness.MONITOR_QML)
        # The scrollbar's handle drag cancels the pending restore (it
        # drives contentY directly and never fires onMovementStarted).
        self.assertIn("onPressedChanged:", harness.MONITOR_QML)
        self.assertIn("if (pressed)", harness.MONITOR_QML)
        for token in ("consoleHistory", "consolePending", "consoleChanged",
                      "consoleLines", "consoleDropped", "def setConsoleExpanded(",
                      "def sendConsoleCommand(", "def clearConsoleHistory(",
                      "filamentUsed", "filamentRemaining"):
            self.assertIn(token, harness.MONITOR_MODEL)
        self.assertNotIn("consoleStatus", harness.MONITOR_MODEL)
        # The filament rows are caption/value grid rows placed AFTER
        # the Finish row (the chosen placement). They outlive the
        # print through complete/cancelled until the next job starts
        # (the UX panel): the gate is the model's readout flag, not
        # printActive.
        self.assertIn('text: "Filament used"', harness.JOB_SECTION_QML)
        self.assertIn('text: "Filament remaining"', harness.JOB_SECTION_QML)
        self.assertLess(harness.JOB_SECTION_QML.index('text: "Finish"'), harness.JOB_SECTION_QML.index('text: "Filament used"'))
        # NO-REFLOW RULE: the rows are permanent — the values read "—"
        # until Klipper reports them; nothing hides them any more, and
        # the readout-visibility gate is gone from the model too.
        self.assertNotIn('visible: root.printer != null && root.printer.filamentReadoutVisible', harness.MONITOR_QML)
        self.assertNotIn("filamentReadoutVisible", harness.MONITOR_MODEL)
        # The z-offset nudge buttons take an exact quarter of the row
        # (a bound preferred width, not layout distribution): fillWidth
        # alone left "↑ 0.005" wider than "↑ 0.05" (the report).
        self.assertIn("Layout.preferredWidth: (zOffsetGrid.width - 3 * zOffsetGrid.buttonSpacing) / 4", harness.TUNING_SECTION_QML)
        # The expanded chart's power axis carries its 0-100% legend,
        # pinned (never scaled), drawn INSIDE the plot's right edge
        # (the live ruling — the outside gutter's last glyph clipped
        # at the card edge), painted last so the data never covers it.
        self.assertIn("function _rightGutter()", harness.TEMP_CHART_QML)
        self.assertIn('ctx.fillText("100%", plotWidth - 4, 4 + ascent)', harness.TEMP_CHART_QML)
        self.assertIn('ctx.fillText("0%", plotWidth - 4, plotBottom - descent - 1)', harness.TEMP_CHART_QML)
        self.assertNotIn("fillRect(chipX", harness.TEMP_CHART_QML)

    def test_console_resize_handle_surface(self):
        # 3.6.0: the console card's TOP edge is a drag handle. The
        # load-bearing semantics are pinned here — the pane-bounds clamp
        # window, the pane-frame drag measurement, the single commit on
        # release, and the collapse behaviour (the request: pull
        # the pane down to the collapse position and it collapses; drag
        # back out and it expands).
        for token in ("id: consoleResizeHandle",
                      'objectName: "consoleResizeHandle"',
                      'objectName: "consoleResizeArea"',
                      "cursorShape: Qt.SizeVerCursor",
                      # The handle holds its OWN strip: an overlay across
                      # the header row would eat the collapse toggle's
                      # hit area.
                      "Layout.preferredHeight: consolePanel.consoleHandleHeight",
                      # The pointer is read in the PANE's frame, never the
                      # handle's own: the handle rides the edge it moves,
                      # so a local measurement is self-referential (the
                      # run-away-the-pointer bug).
                      "mapToItem(cameraArea, mouse.x, mouse.y)",
                      "consolePanel.consoleResizeStartHeight = consolePanel.height",
                      "consoleResizeStartHeight + (consoleResizeStartY - paneY)",
                      "consoleResizeTo",
                      "consoleResizeCommit",
                      "onCanceled: consolePanel.consoleResizeCommit()",
                      # The collapse position is the drag FLOOR, not a
                      # jump: the height is continuous across it, so the
                      # edge never detaches from the pointer.
                      "Math.max(consoleCollapsedHeight, Math.min(consoleMaxHeight, height))",
                      "consoleSetExpanded(height > consoleCollapsedHeight + 0.5)",
                      "root.printer.setConsoleHeight(Math.round(height))",
                      # The clamp window and the stored/effective heights.
                      "consoleCollapseButton.height + 2 * UM.Theme.getSize(\"thin_margin\").height + consoleHandleHeight",
                      "root.printer.consoleHeight > 0 ? root.printer.consoleHeight : consoleDefaultHeight",
                      "Math.max(consoleMinHeight, Math.min(consoleMaxHeight, consoleStoredHeight))",
                      "consoleDragHeight > 0 ? consoleDragHeight : consoleSettledHeight",
                      "Layout.preferredHeight: consoleExpanded ? consoleCurrentHeight : consoleCollapsedHeight",
                      # The tail stays pinned through a resize (a reader
                      # scrolled up is never yanked — the golden rule).
                      "consoleFlick.restoreScrollPending = true",
                      "Drag to resize the console.",
                      # The grip reads as a grab bar at a glance (the
                      # live ruling: the first one was too
                      # subtle), and the closing pane fades its body out
                      # instead of crushing it through the transition.
                      "width: 72 * screenScaleFactor",
                      "height: 5 * screenScaleFactor",
                      "opacity: consolePanel.consoleBodyOpacity",
                      "consoleBodyOpacity",
                      # The fade starts where the body stops fitting, not
                      # at the collapse position: a gradual fade left the
                      # crushed input row fully opaque for most of the
                      # travel (a second live report).
                      "readonly property real consoleBodyFadeSpan: 24 * screenScaleFactor",
                      "(height - (consoleMinHeight - consoleBodyFadeSpan)) / consoleBodyFadeSpan"):
            self.assertIn(token, harness.MONITOR_QML)
        for token in ("consoleHeight", "consoleHeightChanged", "def setConsoleHeight(",
                      "CONSOLE_HEIGHT_MAX", "def _state_height("):
            self.assertIn(token, harness.MONITOR_MODEL)
        # The handle sits ABOVE the header row in the layout, never over
        # it, and the card's height binding owns both states.
        handle_start = harness.MONITOR_QML.index("id: consoleResizeHandle")
        button_start = harness.MONITOR_QML.index("id: consoleCollapseButton")
        self.assertLess(handle_start, button_start)
        self.assertIn("consoleResizeCommit", harness.MONITOR_QML[handle_start:button_start])
        # The card clips: mid-drag it is SHORTER than its inner column's
        # minimum, and its content must never paint over the webcam card.
        card_start = harness.MONITOR_QML.index("id: consolePanel")
        self.assertIn("clip: true", harness.MONITOR_QML[card_start:handle_start])
        # The well clips too, and that is a live report: the
        # prompt, the input and its buttons live INSIDE the black border,
        # so a squeezed column must cut them at the well's own edge
        # rather than letting them float outside the terminal's
        # background. The slice ends at the flick's own clip.
        well_start = harness.MONITOR_QML.index("id: consoleWell")
        flick_start = harness.MONITOR_QML.index("id: consoleFlick")
        self.assertLess(well_start, flick_start)
        self.assertIn("clip: true", harness.MONITOR_QML[well_start:flick_start])
        # The pane frame is not optional: a local-coordinate delta is the
        # bug this pin exists to prevent.
        self.assertNotIn("consoleResizeStartY = mouse.y", harness.MONITOR_QML)
        self.assertNotIn("consoleResizeStartY - mouse.y", harness.MONITOR_QML)
        # The drag commits ONCE, on release — a commit per move would
        # rewrite the state file at pointer rate.
        self.assertNotIn("setConsoleHeight(Math.round(height))", harness.MONITOR_QML[handle_start:button_start])

    def test_capture_harness_mocks_every_live_input(self):
        # Determinism discipline: the captures must not read ANY live
        # input. The wall clock slipped through once — the formatter's
        # monitorFinish called datetime.now() and captures made in
        # different minutes differed by one clock glyph, failing CI's
        # byte-compare. The harness must freeze BOTH wall-clock readers
        # (the formatter's finish clock and PreviewFollower's ETA
        # finish), and because the Qt runtime registers plugin modules
        # under synthetic names (the same trap as the model below), it
        # must patch EVERY module object loaded from each frozen source
        # file, after the plugin tree has loaded.
        self.assertIn("class FrozenDatetime", harness.CAPTURE_HARNESS)
        self.assertIn("def now(cls, tz=None)", harness.CAPTURE_HARNESS)
        self.assertIn("MonitorFormatting.py", harness.CAPTURE_HARNESS)
        self.assertIn("PreviewFollower.py", harness.CAPTURE_HARNESS)
        self.assertIn("_freeze_plugin_clocks()", harness.CAPTURE_HARNESS)
        # The model's own time reference stays patched module-scoped, so
        # the synthetic history seeds from a fixed clock.
        self.assertIn('patch.object(model_module, "time", fake_time)', harness.CAPTURE_HARNESS)
        # The console pane renders no caret: a blinking cursor made the
        # captures phase-dependent.
        self.assertIn("cursorVisible: false", harness.MONITOR_QML)

    def test_no_controls_disappear_controls_disable(self):
        # NO-REFLOW RULE (the ruling, 2026-09-10): no control
        # ever disappears — it disables. Nothing reflows unless the
        # user asked for it (section collapse, resize, and the 4.4.0
        # section hide/reorder rulings). The jog-reflow
        # hazard came from pause/cancel (and other state-gated controls)
        # vanishing and returning, shifting the pane under the pointer.
        #
        # STRUCTURAL pin (the panel's upgrade): every `visible:` in
        # every plugin QML whose expression is not whitelisted must be
        # on the explicit allow-list — so a new state-gated visibility
        # cannot slip through a reformat or a new file.
        # The file-manager popup joins the carve-out by the
        # round-2 ruling ("Reflowing the file manager is fine, there's
        # nothing critical on that") — but ONLY its own file: the
        # Monitor files must never be exempt, and the set must not
        # grow silently (round-2 security F13). Inside the popup the
        # chrome still uses enabled/opacity, never visible:.
        exempt_files = {"MoonrakerFollowerConfiguration.qml", "ConnectionSettings.qml", "FollowingSettings.qml", "UploadSettings.qml", "DiagnosticsSettings.qml", "MoonrakerUploadDialog.qml"}
        self.assertEqual(exempt_files, {"MoonrakerFollowerConfiguration.qml", "ConnectionSettings.qml", "FollowingSettings.qml", "UploadSettings.qml", "DiagnosticsSettings.qml", "MoonrakerUploadDialog.qml"})
        for monitor_file in ("MoonrakerMonitor.qml", "MoonrakerMonitorDashboard.qml", "MoonrakerPreviewCard.qml"):
            self.assertNotIn(monitor_file, exempt_files)
        # The camera's configured gate moved into CameraPane as
        # `configured` (read there as root.configured): the token
        # follows the code, so the pane's veil and Live badge stay
        # reviewed under this rule.
        whitelist = (
            "openPopOver", "sectionExpandedMap", "sectionHiddenMap", "Collapsed", "platformActivity",
            "previewStageActive", "configuredForFollowing", "modelData.type", "hasWhite",
            "root.configured", "tooltipText", "sectionIcon", "macroParameters",
            "webcamNames", "root.busy", "root.progress", "improveEtaProgress",
            "temperatureChartLegend.series", "allChartSensorsHidden", "selectedChartSensor",
            "hoverClockProxy", "root.compact",
        )
        # The whitelist is itself frozen (round-2 security F13: the
        # set must not grow silently) — an addition is a visible diff.
        self.assertEqual(whitelist, (
            "openPopOver", "sectionExpandedMap", "sectionHiddenMap", "Collapsed", "platformActivity",
            "previewStageActive", "configuredForFollowing", "modelData.type", "hasWhite",
            "root.configured", "tooltipText", "sectionIcon", "macroParameters",
            "webcamNames", "root.busy", "root.progress", "improveEtaProgress",
            "temperatureChartLegend.series", "allChartSensorsHidden", "selectedChartSensor",
            "hoverClockProxy", "root.compact",
        ))
        allowed = {
            # Capability-static gates (the UX panel's ruling): these
            # only change on a printer switch, which is user-initiated.
            "visible: root.printerModel != null && root.printerModel.hasQuadGantryLevel",
            "visible: root.printerModel != null && root.printerModel.hasBedMesh",
            # The webcam FPS bar: parked until the pane is wide enough
            # and the stream is live — layout-static within the pane.
            "visible: root.cameraBarFits && root.cameraControlLive",
            # The camera bar's two modes (zoom / FPS) and its compact
            # chip: mode and size gates inside the camera pane's own
            # reserved strip — never layout-shifting elsewhere.
            'visible: root.cameraBarMode === "zoom"',
            'visible: root.cameraBarMode === "fps"',
            # The green snapshot range is decoration within the FPS
            # bar and appears only when the webcam offers that mode.
            "visible: root.cameraSnapshotAvailable",
            # The FPS scale's mirrored graduation edge: decoration
            # inside the bar's own reserved strip (the zoom scope's
            # own !major mirror).
            "visible: !line",
            "visible: !root.cameraBarFits && root.cameraControlLive && root.cameraPictureWidth >= 96 * screenScaleFactor && root.cameraPictureHeight >= 96 * screenScaleFactor",
            "visible: root.configured && root.printerModel != null && root.printerModel.monitorConnected && cameraImage.visible && cameraImage.imageWidth > 0 && root.cameraPictureWidth >= cameraLiveBadge.width + width + 3 * badgeGap",
            # The plate's toolhead dot: scene-graph decoration INSIDE
            # the canvas's reserved slot — it can never shift layout,
            # only its own marker can appear inside the fixed map.
            "visible: root._plot != null && root.dot != null && root.dot.valid === true",
            "visible: mapping._plot != null && root.dot != null && root.dot.valid === true",
            # The follower's unavailable state: an overlay INSIDE the
            # face's own slot (the popover is a transient surface),
            # never a layout shift.
            "visible: !root.available()",
            "visible: !root.available() && !root.compact",
            "visible: !root.available() && root.compact",
            # The mapping hides while the index is unavailable: the
            # bed grid must not sit under the download offer's text —
            # inside the face's own slot, never a layout shift.
            # GPU render passes overlap inside the fixed canvas; visibility
            # changes cannot reflow controls or any surrounding layout.
            "visible: gpuFollower.visible",
            'visible: gpuFollower.visible && (modelData === "ghost" ? root.showBase : (modelData === "prev" ? root.showPrevious || root._handoffOpacity > 0 : root.showNext))',
            "visible: root.available()",
            # The objects list's current-row bar: a highlight behind
            # the text, never a layout shift.
            "visible: modelData.current",
            # The picker's printed-legend row and its index offer:
            # the printed state derives from the index, so both gate
            # on its availability — inside the transient card.
            "visible: root.printer != null && root.printer.plateTrackingAvailable",
            "visible: root.printer != null && root.printer.plateHasObjects && !root.printer.plateTrackingAvailable",
            # The download prompt swaps into noninteractive waiting text
            # inside the transient plate cards once their index is ready.
            "visible: !root.indexReady",
            "visible: root.indexReady",
            # The scope's right-edge tick: the majors draw one full
            # line, the halves and quarters an edge pair — scene-graph
            # decoration inside the scope.
            "visible: !major",
            # The picker's gate (the live ruling): during a print the
            # section shows the plate when the data exists, the
            # download offer otherwise; it clears with the job epoch.
            "visible: root.printer != null && root.printer.sectionHiddenMap[\"plate\"] !== true && (root.printer.printActive || root.printer.plateHasObjects)",
            # The picker's download offer: inside the transient card,
            # shown while the plate is empty.
            "visible: root.printer != null && !root.printer.plateHasObjects",
            # The follower's dot rides the layers: no index, no dot
            # (scene decoration inside the canvas slot). A detached
            # face draws the frozen layer, which the live position is
            # not: the dot goes with the follow.
            "visible: root.available() && root.attached && mapping._plot != null && root.dot != null && root.dot.valid === true",
            # Firmware-regulated fans swap the slider for a read-only
            # row (a live report): the model's writable
            # flag picks the face.
            "visible: modelData.writable",
            "visible: !modelData.writable",
            # Carve-outs awaiting the ruling (DECISIONS round 6):
            "visible: base.followingEnabled && base.pauseAtLayerActive && base.pauseAtLayerItems.length > 0 && (base.hasToolpath || base.pauseAtLayerHasBaked)",
            # The toolpath-gated faces (the 2026-09-17 rulings): the
            # attach control, the pause button and its selection line
            # hide without a toolpath; the clear-all hides while only
            # baked rows are listed; the layer/height readout row
            # hides whole while the resolver has no layer.
            "visible: base.hasToolpath",
            "visible: base.pauseAtLayerHasClearable",
            "visible: base.layerReadoutAvailable",
            # The preview card's layer/height row pair (the readout
            # ruling) — written imperatively from the readout's own
            # availability signal. The height is independently
            # optional, so it carries its own gate.
            "visible: layerHeightRowsVisible",
            "visible: heightReadoutAvailable",
            # The availability gates (the live ruling): unavailable
            # values hide their glyphs and cells whole; the X/Y/Z
            # tuple hides when any one axis is absent.
            "visible: root.positionAvailable",
            "visible: root.zOffsetAvailable",
            "visible: root.flowAvailable",
            "visible: root.etaAvailable",
            "visible: root.finishAvailable",
            "visible: root.layerCountAvailable",
            "visible: root.infoHotendText !== \"—\"",
            "visible: root.infoBedText !== \"—\"",
            # The status strip's progress group (the stacked bar's
            # glyph, label and track share the print gate).
            "visible: root.printer != null && root.printer.printActive",
            "visible: root.printer != null && root.printer.printActive && root.printer.monitorEta !== \"—\"",
            "visible: root.printer != null && root.printer.printActive && root.printer.monitorFinish !== \"—\"",
            "visible: root.printer != null && root.printer.monitorLayer !== \"—\"",
            "visible: root.printerModel != null && root.printerModel.monitorPositionX !== \"—\" && root.printerModel.monitorPositionX !== \"\" && root.printerModel.monitorPositionY !== \"—\" && root.printerModel.monitorPositionY !== \"\" && root.printerModel.monitorPositionZ !== \"—\" && root.printerModel.monitorPositionZ !== \"\"",
            # The next-pause row is a PERMANENT slot (the M117
            # precedent): its visibility flip reflowed the section
            # stack and fed a layout polish loop (the live report).
            # Its labels read empty while no pause lies ahead.
            "visible: root.printerModel != null && root.printerModel.nextPauseFraction >= 0",
            "visible: root.printer != null && root.printer.nextPauseFraction >= 0",
            # The job bar's optimisation band (the live request): a
            # state-gated sweep that vanishes at completion — it never
            # flips per poll once the pass settles.
            "visible: root.printerModel != null && root.printerModel.platePassFraction > 0 && root.printerModel.platePassFraction < 1",
            # The preview strip's own derived validity (computed in
            # updateStrip, not a model value).
            "visible: stripValid",
            # The Endstops summary row yields to the chips once they
            # exist (the live ruling — the chips ARE the
            # readout); it sits below the jog pad.
            "visible: root.printerModel == null || root.printerModel.endstopItems.length === 0",
            "visible: root.miniHasSeries",
            # The collapsed status readout's relevance gates (the bars
            # show only while they mean something), plus the fit
            # conjunction the imperative update writes (4.4.0).
            "visible: root.printer != null && root.printer.printActive && fitVisible",
            "visible: root.printer != null && root.printer.monitorLayerProgress >= 0 && fitVisible",
            # The controls configure pop-up's own switch and its scrim
            # (the one-pane dashboard has no openPopOver family).
            "visible: root.configurePaneOpen !== \"\"",
            "visible: root.configurePaneOpen === \"controls\"",
            # The filter rows' marker pair: radio-ness is static per
            # category, so the circle and the native checkbox swap by
            # it (the Uranium-controls ruling) — the selection state
            # lives inside the marker.
            "visible: modelData.radio",
            "visible: !modelData.radio",
            "visible: root.printerModel != null && !root.miniHasSeries",
            # The console error bell (the live request) is a
            # presence signal, not a session gate: it shows only
            # while an unseen error waits and the console is
            # collapsed.
            "visible: root.printer != null && root.printer.consoleErrorBell",
            # The Objects section's empty-state line (the
            # live request): the list arrives mid-print, an empty one
            # says so.
            # The console grab bar hides under the auto-collapse
            # width (the live ruling — a resize handle for
            # an expansion that cannot happen is a lie).
            "visible: !consolePanel.tooNarrow",
            # The file manager's popup: reflow is fine there, nothing critical on it (the ruling, ROADMAP 3.6.0) — each state-gated entry lands here by name.
            "visible: open",
            # The chart hover tooltip's rows follow the legend's
            # visibility: a floating pop-over whose reflow is its
            # nature (the file manager's carve-out precedent) — the
            # rows must not linger as "—" ghosts for hidden sensors.
            "visible: modelData.visible",
            # The what's-new overlay: the pre-collapsed sections ARE
            # the feature (the latest entry open, previous versions
            # gated behind their headers) — each gate lands here by
            # name.
            # The follower's legend changes content with its colour mode.
            "visible: root.mode === 0",
            "visible: root.mode >= 2",
            "visible: root.mode === 1",
            "visible: !modelData.isLatest",
            "visible: modelData.isLatest || entry.open",
            # The overlay's scroll chevrons (the file manager's
            # idiom): they appear only while more content hides off
            # the scrolled edge.
            "visible: flick.height > 0 && flick.contentY > 2",
            "visible: flick.height > 0 && flick.contentY < flick.contentHeight - flick.height - 2",
            # The pause-list chevrons: the file manager's idiom, on
            # the capped five-row ListView the preview card and the
            # follower popover each keep.
            "visible: pauseListView.height > 0 && pauseListView.contentY > 2",
            "visible: pauseListView.height > 0 && pauseListView.contentY < pauseListView.contentHeight - pauseListView.height - 2",
            # The follower face's travel raster: it hands over with the
            # class raster (a peer's commit) — decoration inside the
            # face's own slot, never a layout shift.
            'visible: _travelsShown() || (_presentationDecision().kind === "heldFull" && root._heldFullTravelSource !== "")',
            # The bed-mesh legend collapses when the mesh is hidden —
            # the reflow was granted (the card reflows instead
            # of keeping a faded gap).
            "visible: base.bedMeshAvailable && base.bedMeshVisible",
            # The follower's zoom-gated toolhead row and the Pending
            # option that only the live view carries (the 4.6.0 live
            # requests).
            "visible: progressFace.viewScale > 1.0 && progressFace.attached",
            # The Reset view control: the host's checkbox-row label,
            # hidden in place while at 100% — its row is permanent.
            "visible: progressFace.available() && !progressFace.compact && progressFace.viewScale > 1.0",
            "visible: progressFace.attached",
            # Static renderer capability: smoothing is available only
            # in the GPU development install, independent of print state.
            "visible: progressFace.gpuRendering",
            # The Print job status row collapses while no ETA build runs.
            "visible: root.printerModel != null && root.printerModel.improvingEta",
            "visible: root.printerModel != null && root.printerModel.nextPauseEta.length > 0",
            # The plate face's native raster stack (the 4.6.0 render
            # architecture): the images own their state gates — a
            # full layer's raster, its travels, the grey partial
            # base and the ghost pair.
            "visible: _fullRaster()",
            # Renderer ownership and the attached-only centring preference.
            "visible: root.gpuRendering",
            'visible: root.gpuRendering && text !== ""',
            "visible: root.gpuRendering && root.available()",
            'visible: root.gpuRendering ? root.available() && !gpuFollower.ready : root._presentation.kind === "preparing" && !root._presentation.ready',
            "visible: !root.gpuRendering",
            "visible: !root.gpuRendering && root.available() && (root.showRetractions || root.showUnretractions)",
            "visible: !root.gpuRendering && root._interactionActive",
            'visible: !root.gpuRendering && root._interactionActive && (root._gestureNavSource !== "" || navigationData() !== "")',
            "visible: jumpButton.visible",
            "visible: root.showTravels && _fullRaster() && _travelsOf(root.progress.layers.current)",
            "visible: root.available() && root.showPrevious && _ghost(\"prev\") != null && _rasterOf(_ghost(\"prev\"))",
            "visible: root.available() && root.showNext && _ghost(\"next\") != null && _rasterOf(_ghost(\"next\"))",
            "visible: _partialBase() && _baseOf(root.progress.layers.current)",
            # Exact ink ownership is one pure compositor decision.
            'visible: root._presentation.prefix === "current"',
            'visible: root._presentation.kind === "preparing" && !root._presentation.ready',
            'visible: root._presentation.prefix === "retained"',
            "visible: root._interactionActive",
            # The full raster's standing: the full state OR the
            # 100% -> partial entry's transaction — the predicate
            # itself holds the previous composition until the
            # replacement is presentation-ready (zero blank frames).
            "visible: _fullPictureStanding()",
            # The interaction raster: the warm full-bed composite
            # owns the heavy scene during a camera gesture — the
            # source latches at entry so a mid-gesture retirement
            # never unloads the scene in hand (an interaction driven
            # without an entry falls back to the live eligible URL).
            "visible: root._interactionActive && (root._gestureNavSource !== \"\" || navigationData() !== \"\")",
            # The monitor's loading prompt: the printer binding not
            # resolved yet (the entry window) or connected with no
            # data landed (the 2026-09-16 request).
            "visible: !root.statusCollapsed && (root.printer == null || root.printer.monitorLoading)",
            "visible: root.thumbStateLarge(root.confirmRelpath()) === \"ready\" && confirmThumb.status !== Image.Error",
            "visible: root.thumbStateLarge(root.confirmRelpath()) === \"loading\"",
            "visible: root.thumbStateLarge(root.confirmRelpath()) === \"failed\" || root.thumbStateLarge(root.confirmRelpath()) === \"none\"",
            "visible: root.deleteBlockedCount() > 0",
            "visible: root.printerModel != null && root.printerModel.fileRenameConflict",
            "visible: root.uploadProgressState() === \"uploading\"",
            "visible: root.uploadProgressState() === \"failed\"",
            "visible: root.downloadProgressName() !== \"\"",
            "visible: root.thumbState(modelData.relpath) === \"ready\" && recentsThumb.status !== Image.Error",
            "visible: root.thumbState(modelData.relpath) === \"loading\"",
            "visible: root.thumbState(modelData.relpath) === \"failed\" || root.thumbState(modelData.relpath) === \"none\"",
            "visible: !root.narrowMode",
            "visible: root.printerModel == null || root.printerModel.fileManagerSearch.length === 0",
            'visible: root.printerModel != null && root.printerModel.fileManagerSearch.length > 0 && modelData.folder !== ""',
            "visible: root.printerModel != null && root.printerModel.fileManagerHistoryLoaded > 0 && !root.printerModel.fileManagerHistoryExhausted",
            "visible: searchField.text.length > 0",
            "visible: root.filterActive(\"slicer\")",
            "visible: !root.filterActive(\"slicer\")",
            "visible: root.filterActive(\"modified\")",
            "visible: !root.filterActive(\"modified\")",
            "visible: root.filterActive(\"print_time\")",
            "visible: !root.filterActive(\"print_time\")",
            "visible: root.filterActive(\"never_printed\")",
            "visible: !root.filterActive(\"never_printed\")",
            "visible: !root.narrowMode && root.printerModel != null && root.printerModel.fileManagerSearch.length === 0 && (root.printerModel.fileManagerDirectory.length > 0 || root.activeDirectories.length > 0)",
            "visible: root.printerModel != null && root.printerModel.fileManagerDirectory.length > 0",
            "visible: root.narrowMode",
            "visible: modelData.printing === true",
            "visible: root.thumbState(modelData.relpath) === \"ready\" && thumbImage.status !== Image.Error",
            "visible: modelData[0] === \"Status\" && root.rowChecked(rowDelegate.rowData)",
            "visible: gridVertical.height > 0 && gridVertical.contentY > 2",
            "visible: gridVertical.height > 0 && gridVertical.contentY < gridVertical.contentHeight - gridVertical.height - 2",
            "visible: root.printerModel != null && root.printerModel.fileManagerWalkError !== \"\"",
            "visible: root.printerModel != null && root.activeRows.length === 0",
            "visible: root.printerModel != null && (root.walkErrorText() !== \"\" || (root.printerModel.fileManagerRefreshedAt !== \"Not yet refreshed\" && root.printerModel.fileManagerEmptyKind === \"over_filtered\"))",
            "visible: root.printerModel == null || root.printerModel.fileManagerSelected > 0",
            "visible: root.printerModel == null || root.activeRows.length > 0",
            # The camera image's static hidden default (the duplicate-
            # start fix): visible arrives ONLY through applyCamera, so
            # the false default is the owned state, not a disappearing
            # control.
            "visible: false",
        }
        for path in sorted(harness.PLUGINS.rglob("*.qml")):
            if path.name in exempt_files:
                continue  # settings dialogs carve-out (user-opened surfaces)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = harness.re.search(r"(visible:\s*.+)$", line)
                if not match:
                    continue
                expression = match.group(1).rstrip()
                if any(token in expression for token in whitelist):
                    continue
                # The extrude distance/speed rows highlight their
                # SELECTION by swapping button faces (the
                # live report: the boxes never stayed highlighted) —
                # one family, one carve-out, not ten near-identical
                # whitelist entries.
                if harness.re.match(r"visible: (root\.printerModel == null \|\| root\.printerModel\.(extrudeDistance|extrudeSpeed) !== \d+|root\.printerModel != null && root\.printerModel\.(extrudeDistance|extrudeSpeed) === \d+)$", expression):
                    continue
                # The tooltip-popup family (the tooltip rule): the
                # popup's hover-driven visibility is not a control
                # disappearing — one pattern covers every unique
                # HoverHandler id.
                if harness.re.match(r"visible: (parent\.hovered|(parent\.enabled && )?tooltipHover\d+\.hovered( && root\.tooltipText\.length > 0)?)$", expression):
                    continue
                self.assertIn(expression, allowed,
                              f"{path.name}:{number}: state-gated visible: {expression}")
        # The hide masks: one sectionHiddenMap occurrence per section
        # (Dashboard 13 controls; Monitor 4
        # information + 6 status after the move and the follower's own
        # section).
        # A new adopter trips the count — the whitelist's substring
        # blessing must not cover an unbounded family.
        for monitor_file, expected in (("MoonrakerMonitorDashboard.qml", 13),
                                       ("MoonrakerMonitor.qml", 10)):
            self.assertEqual(
                (harness.PLUGINS / monitor_file).read_text(encoding="utf-8").count("sectionHiddenMap["),
                expected, monitor_file)
        # The replacement: every SESSION state lives in `enabled`.
        for enabled in (
            "enabled: root.printerModel != null && root.printerModel.canPausePrint",
            "enabled: root.printerModel != null && root.printerModel.canResumePrint",
            "enabled: root.printerModel != null && root.printerModel.canCancelPrint",
            "enabled: root.printer != null && root.printer.monitorConnected && root.printer.consoleLines.length > 0",
            "enabled: base.bedMeshAvailable",
        ):
            self.assertIn(enabled, harness.MONITOR_QML + harness.DASHBOARD_QML + harness.PREVIEW_CONTROLS_QML + harness.PRINT_SECTION_QML)
        # The Preview load button keeps its full width: the follow button
        # no longer vanishes to widen it. The attach-gate round made the
        # width conditional on the toolpath (the hidden follow button
        # leaves the load button the whole row).
        self.assertIn("width: base.hasToolpath ? buttons.width - base.buttonSpacing - followButton.width : buttons.width", harness.PREVIEW_CONTROLS_QML)

    def test_disconnected_disables_every_monitor_control(self):
        # The ruling (2026-09-10): while DISCONNECTED no
        # Monitor-page control is enabled — the emergency stop included.
        # The model publishes the connection state; the section gates,
        # the console, the camera refresh and the emergency stop all
        # disable on it.
        self.assertIn("monitorConnected", harness.MONITOR_MODEL)
        self.assertIn("enabled: root.printerModel != null && root.printerModel.monitorConnected", harness.FILE_MANAGER_SECTION_QML)
        self.assertIn("enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)", harness.FANS_SECTION_QML + harness.LEDS_SECTION_QML + harness.PWM_SECTION_QML + harness.POWER_SECTION_QML + harness.SYSTEM_SECTION_QML + harness.SAVE_SECTION_QML + harness.TUNING_SECTION_QML + harness.PRINT_SECTION_QML + harness.SETUP_SECTION_QML + harness.TOOLHEAD_SECTION_QML + harness.MACROS_SECTION_QML + harness.PROFILES_SECTION_QML + harness.FILE_MANAGER_SECTION_QML)
        # The abs/rel word's CLICK obeys the same gate as its styling —
        # a locked control must not act (caught in testing).
        self.assertIn("root.printerModel != null && root.printerModel.jogEnabled", harness.TOOLHEAD_SECTION_QML)
        self.assertIn("enabled: root.printer != null && root.printer.monitorConnected", harness.MONITOR_QML)
        # The console is special: the SECTION stays enabled while
        # disconnected (scrolling, selecting and copying the restored
        # history keep working — the ruling); only the input,
        # Send and Clear disable. The well itself turns grey so the
        # disconnected state is obvious.
        self.assertIn("enabled: root.printer != null\n                                property int consoleRecallIndex", harness.MONITOR_QML)
        self.assertIn('color: root.printer != null && root.printer.monitorConnected ? MoonrakerTheme.consoleBackground : MoonrakerTheme.consoleBackgroundOffline', harness.MONITOR_QML)
        self.assertIn("anchors.bottom: parent.bottom", harness.MONITOR_QML)
        # The connection DOT rides the Printer status pane's title in
        # BOTH pane states (expanded header and the collapsed strip) —
        # the chosen spot. Plus the camera's Live badge and
        # the disconnected grey veil over stale frames.
        self.assertIn("connectionDotColour", harness.MONITOR_QML)
        self.assertIn('text: root.printer != null && root.printer.monitorConnected ? (root.printer.connectionDetail.length > 0 ? "Connected to Moonraker — " + root.printer.connectionDetail + "." : "Connected to Moonraker.") : "Disconnected from Moonraker."', harness.MONITOR_QML)
        self.assertIn("id: statusCollapsedTitle", harness.MONITOR_QML)
        self.assertIn('text: "Live"', harness.CAMERA_PANE_QML)
        self.assertIn('color: MoonrakerTheme.cameraVeil', harness.CAMERA_PANE_QML)
        self.assertIn('text: (root.printerModel != null && root.printerModel.cameraRecovering) ? "Camera recovering…" : "Camera offline"', harness.CAMERA_PANE_QML)
        model = self.monitor()
        # The harness may connect asynchronously during construction —
        # pin the TRANSITIONS, which are synchronous.
        self.follower.client.connectionChanged.emit(False, "offline")
        self.assertFalse(model.monitorConnected)
        # A flapping link re-emits the same state — the console notes
        # only real transitions, never repeats.
        self.follower.client.connectionChanged.emit(False, "offline")
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(len([entry for entry in model.consoleLines.value()
                              if entry["kind"] == "note" and "Disconnected" in entry["text"]]), 1)
        self.deliver_state("standby")
        self.assertTrue(model.monitorConnected)

    def test_no_bisect_debris_and_the_console_is_visible(self):
        # The 3.5.0 release shipped with the console behind a
        # "visible: false // BISECT" flag and six labels stripped of
        # their elide — both invisible to token pins. Behavioural pins:
        # no BISECT markers may exist, and the console section must not
        # carry a visibility gate.
        self.assertNotIn("BISECT", harness.MONITOR_QML)
        self.assertNotIn("BISECT", harness.DASHBOARD_QML)
        console = harness.MONITOR_QML[harness.MONITOR_QML.index("id: consoleSection"):harness.MONITOR_QML.index("id: consoleInput")]
        self.assertNotIn("visible: false", console)

    def test_publishes_without_aux_do_not_append_history(self):
        # The feed is revision-gated: a publish with no auxiliary
        # arrival must leave the history revision untouched (the old
        # per-publish feed duplicated samples and halved the window).
        model = self.monitor()
        model._data._update(auxiliary={"extruder": {"temperature": 200.0}})
        model._data.auxiliaryChanged.emit()
        revision = model._temperature._history.revision
        model._data._update(core={"print_stats": {"state": "printing"}})
        self.assertEqual(model._temperature._history.revision, revision)

    def test_connect_transition_fires_every_lane_immediately(self):
        # A live report: after the connect the aux lanes
        # stayed unpopulated until their timers' next ticks. The
        # connection transition itself must fire every lane — the
        # request traffic grows the moment the connection lands.
        self.monitor()
        self.qt.events(2)
        before = len(self.transport.requests)
        self.deliver_state("standby")
        self.qt.events(2)
        self.assertGreater(len(self.transport.requests), before)

    def test_endstop_query_uses_the_documented_get(self):
        model = self.monitor()
        # The poll is readiness-gated; seed a ready Klippy so the
        # request fires (see test_endstop_poll_waits_for_klippy_ready).
        model._data._update(server={"klippy_state": "ready"})
        model._data.refresh_endstops()
        self.qt.events(3)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        # refresh_all fires one at activation too, so at least one —
        # and every endstop poll must use the documented GET.
        self.assertGreaterEqual(len(requests), 1)
        self.assertTrue(all(request.method == "GET" for request in requests))

    def test_endstop_poll_waits_for_klippy_ready(self):
        # During a Klippy restart every endstop poll landed in the
        # gcode store as "!! Internal Error on WebRequest" (the
        # live report) — the poll must hold until server/info
        # reports ready, and resume on the next readiness.
        model = self.monitor()
        model._data._update(server={"klippy_state": "startup"})
        model._data.refresh_endstops()
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        self.assertEqual(len(requests), 0)
        model._data._update(server={"klippy_state": "ready"})
        model._data.refresh_endstops()
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        self.assertGreaterEqual(len(requests), 1)

    def test_setup_scripts_queue_behind_the_in_flight_command(self):
        model = self.monitor()
        self.deliver_state("standby")
        commands = model._commands
        model.homeAll()  # the real setup path
        # While the first script is in flight, further one-shot scripts
        # queue instead of being dropped.
        self.assertTrue(commands.script("QGL", "QUAD_GANTRY_LEVEL"))
        self.assertTrue(commands.script("Mesh", "BED_MESH_CALIBRATE"))
        self.assertTrue(model.canRunSetup)  # the gate stays open for queueing
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        # Stateful commands still refuse while busy: they never queue.
        self.assertFalse(commands.send("Pause", "printer/print/pause"))
        self.assertEqual([r for r in self.transport.requests if r.path == "printer/print/pause"], [])
        scripts[0].callback({}, None)
        self.qt.events(10)
        self.assertEqual(len(self.scripts()), 2)  # the next queued script drains automatically
        self.scripts()[1].callback({}, None)
        self.qt.events(10)
        bodies = [r.options["body"] for r in self.scripts()]
        self.assertEqual(bodies, [{"script": "G28"}, {"script": "QUAD_GANTRY_LEVEL"}, {"script": "BED_MESH_CALIBRATE"}])
        self.assertEqual(model._commands._queue, [])

    def test_rapid_jogs_while_paused_queue_separately(self):
        model = self.monitor()
        self.deliver_state("paused")
        model.setJogDistance(1)
        model.jog("x", 1)  # sent immediately
        scripts = self.scripts()
        self.assertEqual(len(scripts), 1)
        model.jog("x", 1)  # queued behind the in-flight send
        model.jog("x", 1)  # queues as its own move (no coalescing)
        self.assertEqual(self.scripts(), scripts)  # nothing new in flight
        scripts[0].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[1].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})
        # Each queued move drains on its own completion cycle.
        scripts[1].callback({}, None)
        self.qt.events(20)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 3)
        self.assertEqual(scripts[2].options["body"], {"script": "G91\nG1 X1 F3000\nG90"})

    def test_monitor_device_is_registered_with_output_manager(self):
        # The Monitor stage shows Cura's "connect the printer" placeholder when
        # no output device is registered; refresh() must register the incoming
        # device with the output-device manager on every transition, including
        # the very first one at startup.
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        manager = output.getOutputDeviceManager()
        self.assertIsNotNone(output._current)
        manager.addOutputDevice.assert_called_once_with(output._current)

    def test_monitor_does_not_broadcast_unrelated_ui_signals_on_every_poll(self):
        model = self.monitor()
        self.deliver()
        webcam_changes = []
        control_changes = []
        model.webcamsChanged.connect(lambda: webcam_changes.append(True))
        model.controlsChanged.connect(lambda: control_changes.append(True))

        self.deliver()
        self.assertEqual(webcam_changes, [])
        self.assertEqual(control_changes, [])

        model._data._update(webcams=[{"uid": "front", "name": "Front", "stream_url": "/front"}])
        self.qt.events()  # the publish coalescer flushes on the next turn
        # The webcam family's grouped signal fires again when the
        # one-turn restore adopts the index; the important half is
        # that controls stay silent.
        self.assertGreaterEqual(len(webcam_changes), 1)
        self.assertEqual(control_changes, [])

    def test_the_decode_rate_persists_per_machine_and_costs_no_reconnect(self):
        # The throttle is the renderer's decode cadence, never the
        # stream: committing a rate must persist it per machine and
        # leave the connection exactly as it was — no new generation,
        # no rebind, no re-request of anything.
        model = self.monitor()
        self.deliver()
        cameras = [{"uid": "front-uid", "name": "Front", "stream_url": "/front", "target_fps": 30}]
        model._data._update(webcams=cameras)
        self.qt.events()
        self.assertEqual(model.cameraFps, 15.0, "the product default until the user moves it")
        generation = self.follower.client._generation
        requests = len(self.transport.requests)

        model.setCameraFps(12.0)
        self.qt.events()

        self.assertEqual(model.cameraFps, 12.0, "the published rate is the one committed")
        self.assertEqual(self.follower.current_printer_config().camera_fps, 12.0)
        document = self.follower.persistence.settings_document()
        machine_id = self.follower.current_printer_identity()[0]
        self.assertEqual(document["machines"][machine_id]["camera_fps"], 12.0,
                         "the rate rides the same per-machine settings record")
        self.assertEqual(self.follower.client._generation, generation,
                         "a rate change is never a reconnect")
        self.assertEqual(len(self.transport.requests), requests,
                         "and it asks the printer for nothing")

        # The range it publishes is the camera's own, and the rate
        # survives a restart of the whole follower.
        self.assertEqual(model.cameraFpsMin, 0.5)
        self.assertEqual(model.cameraFpsMax, 30.0)
        app2 = self.qt.Application(preferences=self.app.preferences)
        transport2 = harness.ScriptedTransport()
        runtime_module = self.qt.load("FollowerRuntime")
        real_client = runtime_module.MoonrakerClient
        with harness.patch.object(runtime_module, "MoonrakerClient", lambda parent: real_client(parent, transport=transport2, socket=harness.ScriptedSocket())):
            follower2 = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app2)
        self.addCleanup(follower2.deinitialize)
        self.assertEqual(follower2.current_printer_config().camera_fps, 12.0,
                         "the rate is restored from the settings document")
        restored = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app2, follower2)
        restored.start()
        self.addCleanup(restored.stop)
        restored_model = restored._current.activePrinter
        restored_model._data._update(webcams=cameras)
        self.qt.events()
        self.assertEqual(restored_model.cameraFps, 12.0,
                         "and the restored machine renders at it")

    def test_an_idle_rate_control_never_saves_the_config(self):
        # The commit is a real change or nothing: a stale widget echoing
        # back the rate the model already holds (the pane's own binding
        # round trip) must not write the settings document, and neither
        # must a value that clamps to where the model already stands.
        model = self.monitor()
        model._data._update(webcams=[
            {"uid": "cam", "name": "Cam", "stream_url": "/cam", "target_fps": 15}])
        self.qt.events(10)

        def stored():
            return harness.json.dumps(self.follower.persistence.settings_document(), sort_keys=True)

        settling = stored()
        model.setCameraFps(15.0)  # already the effective rate
        self.qt.events(10)
        self.assertEqual(stored(), settling, "a stale echo of the current rate saves nothing")
        model.setCameraFps(30.0)  # past the camera; clamps back onto it
        self.qt.events(10)
        self.assertEqual(stored(), settling, "and neither does a commit that cannot move it")
        model.setCameraFps(0.2)  # below the floor
        self.qt.events(10)
        self.assertNotEqual(stored(), settling, "the floor clamp is a real change")
        self.assertEqual(model.cameraFps, 0.5)
