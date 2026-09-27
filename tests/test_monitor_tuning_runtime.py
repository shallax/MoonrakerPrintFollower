"""Executable monitor tuning runtime contracts."""
from tests import monitor_test_support as harness

class MonitorQtTests(harness.MonitorQtTests):
    def test_emergency_stop_clears_pending_jog_queue(self):
        model = self.monitor()
        self.deliver_state("printing")
        model.jog("x", 1)  # pause-first cycle starts; the pause holds busy
        model.emergencyStopClick()
        model.emergencyStopClick()
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(model._toolhead._pending, ())
        self.assertFalse(model._toolhead._pause_waiting)
        self.assertFalse(model._toolhead._pause_in_flight)
        self.assertFalse(model.actionBusy)

    def test_last_action_rows_are_labelled_and_always_visible(self):
        # The permanent caption row (the ruling): a label so
        # the row explains itself before first use, "—" until the first
        # event, and no visibility gate to make it pop in and out. It
        # lives ONLY in the Monitor's Print job grid (first row, so its
        # columns are the grid's columns — a separate row read as
        # misaligned); the Dashboard's print section does not repeat it.
        self.assertIn('text: "Last action"', harness.JOB_SECTION_QML)
        self.assertIn('root.printerModel.actionStatus.length > 0 ? root.printerModel.actionStatus : "—"', harness.JOB_SECTION_QML)
        self.assertNotIn("visible: root.printer != null && root.printer.actionStatus.length > 0", harness.MONITOR_QML)
        self.assertLess(harness.JOB_SECTION_QML.index('text: "Last action"'), harness.JOB_SECTION_QML.index('text: "Layer"'))
        self.assertNotIn('text: "Last action"', harness.DASHBOARD_QML)

    def test_temperature_chart_config_persists_across_model_instances(self):
        model = self.monitor()
        auxiliary = {"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5},
                     "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.2}}
        self.feed_chart(model, auxiliary)
        # Defaults: everything visible, palette colours, toggles on.
        default = self.legend_of(model)
        self.assertTrue(default["showTargets"])
        self.assertTrue(default["showPower"])
        model.setTemperatureSensorVisible("extruder", False)
        model.setTemperatureSensorColor("heater_bed", "#123456")
        model.setShowTemperatureTargets(False)
        model.setShowTemperaturePower(False)
        # The chart config persists per printer (sensor names differ
        # between machines), never in the global chrome file.
        self.assertEqual(self.follower.current_printer_config().temperature_chart, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        # A fresh model restores the config from the file.
        second = self.monitor()
        self.feed_chart(second, auxiliary)
        legend = self.legend_of(second)
        self.assertFalse(legend["showTargets"])
        self.assertFalse(legend["showPower"])
        extruder = next(item for item in legend["series"] if item["name"] == "extruder")
        bed = next(item for item in legend["series"] if item["name"] == "heater_bed")
        self.assertFalse(extruder["visible"])
        self.assertEqual(bed["color"], "#123456")

    def test_temperature_chart_defaults_when_the_block_is_missing(self):
        section_path = self.follower.persistence.state_global_path
        with open(section_path, "w", encoding="utf-8") as handle:
            harness.json.dump({"sections": {"setup": False}}, handle)
        model = self.monitor()
        legend = self.legend_of(model)
        self.assertTrue(legend["showTargets"])
        self.assertTrue(legend["showPower"])
        self.assertTrue(all(item["visible"] for item in legend["series"]))

    def test_emergency_stop_pending_tokens_survive_stale_completions(self):
        # The empirical drift: 3 sends → emergency stop → 2 fresh sends →
        # 3 stale completions → pending 0 (should be 2). In-flight tokens
        # fix it: completions belong to a specific entry, and dead
        # requests' entries were dropped with the stop.
        model = self.monitor()
        for text in ("G28", "M105", "G90"):
            self.assertTrue(model.sendConsoleCommand(text))
        scripts = self.scripts()
        self.assertEqual(len(scripts), 3)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consolePending, 3)
        model._commands.emergencyStopped.emit()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consolePending, 0)
        for text in ("M105", "G1 X0"):
            self.assertTrue(model.sendConsoleCommand(text))
        self.qt.events()
        self.assertEqual(model.consolePending, 2)
        # The three dead requests complete late — nothing drains.
        for script in scripts:
            script.callback(None, "aborted")
        self.qt.events(1)
        self.assertEqual(model.consolePending, 2)

    def test_console_burst_drains_pending_per_completion(self):
        # Each console send posts its own request (the shared lane is
        # the card's ticker, off-limits for console traffic), and each
        # request's own callback drains exactly one pending slot — the
        # per-idle-epoch accounting that once leaked phantoms on the
        # lane is gone with the lane.
        model = self.monitor()
        for i in range(3):
            self.assertTrue(model.sendConsoleCommand(f"G1 X{i}"))
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.consolePending, 3)
        scripts = self.scripts()
        self.assertEqual(len(scripts), 3)
        for i in range(3):
            scripts[i].callback({"result": "ok"}, None)
            self.qt.events()  # the publish coalescer flushes on the next turn
            self.assertEqual(model.consolePending, 3 - (i + 1))
        self.assertEqual(model.consolePending, 0)

    def test_endstop_failed_poll_keeps_last_known_states(self):
        # A transient poll failure must not erase last-known pin states
        # into a false "Not homed yet" while connected; the states
        # blank only on invalidation/disconnect.
        model = self.monitor()
        model._data._update(server={"klippy_state": "ready"})
        model._data._update(endstops={"x": "TRIGGERED", "y": "open"})
        model._data.refresh_endstops()
        self.qt.events(1)
        requests = [request for request in self.transport.requests
                    if request.path == "printer/query_endstops/status"]
        self.assertTrue(requests)
        requests[-1].callback(None, "network blip")
        self.qt.events(1)
        self.assertEqual(model._data.snapshot.endstops, {"x": "TRIGGERED", "y": "open"})

    def test_regrabbing_slider_keeps_last_released_value_until_next_release(self):
        model = self.monitor()
        self.deliver()
        model._tuning.DEBOUNCE_MS = 1000

        # First release is published immediately, while its G-code remains
        # debounced. This is the position the next gesture must start from.
        model.setSpeedFactor(137)
        self.assertEqual(model.speedFactorPercent, 137)

        # Re-grab before the command has been sent. A status poll still reports
        # the printer's old 100% value, but must not push the bound Slider back.
        model.previewSpeedFactor(145)
        self.assertEqual(model.speedFactorPercent, 137)
        self.deliver()
        self.assertEqual(model.speedFactorPercent, 137)

        # Releasing the second gesture replaces the cancelled 137% command and
        # sends only the new value after the debounce interval.
        model._tuning.DEBOUNCE_MS = 10
        model.setSpeedFactor(145)
        self.assertEqual(model.speedFactorPercent, 145)
        self.qt.events(25)
        commands = [request for request in self.transport.requests if request.channel == "quick-speed-factor"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S145"})

    def test_slider_preview_does_not_publish_during_drag_and_commit_still_sends(self):
        model = self.monitor()
        self.deliver()
        tuning_changes = []
        control_changes = []
        model._tuning.changed.connect(lambda: tuning_changes.append(True))
        model.controlsChanged.connect(lambda: control_changes.append(True))

        for value in range(110, 121):
            model.previewSpeedFactor(value)
        self.assertEqual(tuning_changes, [])
        self.assertEqual(model._tuning.value("speed-factor", 100), 100)
        self.assertEqual(model.speedFactorPercent, 100)

        # A normal Moonraker poll during the active gesture must not publish the
        # preview value back through the bound Qt property and reset the Slider.
        self.deliver()
        self.assertEqual(model.speedFactorPercent, 100)
        self.assertEqual(control_changes, [])

        model._tuning.DEBOUNCE_MS = 10
        model.setSpeedFactor(137)
        self.assertEqual(len(tuning_changes), 1)
        self.assertEqual(model.speedFactorPercent, 137)
        self.assertEqual(len(control_changes), 1)
        self.qt.events(25)
        commands = [request for request in self.transport.requests if request.channel == "quick-speed-factor"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S137"})


