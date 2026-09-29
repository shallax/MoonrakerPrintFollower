"""Executable qt follower integration contracts."""
from tests import qt_integration_support as harness

class QtRuntimeTests(harness.QtRuntimeTests):
    def test_full_follower_bootstrap_migrates_before_first_connection(self):
        prefs = harness.Preferences({
            "moonraker/instances": harness.json.dumps({"A": {"url": "http://imported", "api_key": "import-key"}}),
            "moonrakerprintfollower/printer_configs_v1": harness.json.dumps({"A": {"feed_mode": "http"}}),
        })
        app, follower, transport = self.follower(preferences=prefs)
        self.assertEqual(transport.identity, ("http://imported", "import-key"))
        self.assertEqual(follower.client.session.base_url, "http://imported")
        self.assertTrue(transport.requests)

    def test_the_stored_cache_budget_reaches_the_running_stores(self):
        # The boot e2e: the v1 record's stored budget binds the
        # stores through the REAL construction + migration boot, and
        # the migrated connection keeps its identity. (The migration
        # cannot itself change the budget: cache_max_mb post-dates
        # every legacy source and the v1 record is the early read's
        # own input — the change-without-restart case is the apply
        # path, pinned in the namespace tests.)
        prefs = harness.Preferences({
            "moonraker/instances": harness.json.dumps({"A": {
                "url": "http://imported", "api_key": "import-key"}}),
            "moonrakerprintfollower/printer_configs_v1": harness.json.dumps(
                {"A": {"feed_mode": "http", "cache_max_mb": 384}}),
        })
        app, follower, transport = self.follower(preferences=prefs)
        parts = follower._runtime
        self.assertEqual(transport.identity, ("http://imported", "import-key"))
        self.assertEqual(parts.index._prepared.max_bytes, 384 * 1024 * 1024,
                         "the stored budget never reached the prepared store")
        self.assertEqual(parts.index._cache.max_bytes, 384 * 1024 * 1024,
                         "the stored budget never reached the index store")
        self.assertIsNone(parts.index._cache.max_entries,
                          "the runtime cache kept a hidden count cap")

    def test_session_invalidation_cancels_in_flight_downloads(self):
        from PyQt6.QtCore import QObject, pyqtSignal
        app, follower, transport = self.follower()
        class NeverReply(QObject):
            readyRead = pyqtSignal()
            finished = pyqtSignal()
            def setReadBufferSize(self, size): pass
            def readAll(self): return b""
            def rawHeader(self, name): return b""
            def error(self):
                from PyQt6.QtNetwork import QNetworkReply
                return QNetworkReply.NetworkError.NoError
            def errorString(self): return ""
            def abort(self): pass
            def deleteLater(self): pass
        transport.network = harness.SimpleNamespace(get=lambda request: NeverReply())
        messages = []
        follower.download_failed.connect(messages.append)
        follower.request_file_download("prints/part.gcode")
        follower.client.sessionInvalidated.emit()
        self.assertEqual(len(messages), 1)
        self.assertIn("cancelled", messages[0])

    def test_deinitialize_with_in_flight_download_delivers_the_cancel_error(self):
        from PyQt6.QtCore import QObject, pyqtSignal
        app, follower, transport = self.follower()
        class NeverReply(QObject):
            readyRead = pyqtSignal()
            finished = pyqtSignal()
            def setReadBufferSize(self, size): pass
            def readAll(self): return b""
            def rawHeader(self, name): return b""
            def error(self):
                from PyQt6.QtNetwork import QNetworkReply
                return QNetworkReply.NetworkError.NoError
            def errorString(self): return ""
            def abort(self): pass
            def deleteLater(self): pass
        transport.network = harness.SimpleNamespace(get=lambda request: NeverReply())
        messages = []
        follower.download_failed.connect(messages.append)
        # The download flow now opens the save picker first — patch the
        # CLASS (this harness process has no QApplication for a real
        # dialog, and the runtime loads its own module copies that
        # module-level patches would miss).
        with harness.patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName",
                   return_value=("/tmp/never-written.gcode", "")):
            follower.request_file_download("prints/part.gcode")
        follower.deinitialize()
        self.assertEqual(len(messages), 1)
        self.assertIn("cancelled", messages[0])

    def test_reconnect_rearms_the_monitor_data(self):
        # A session invalidation deactivates the monitor's data feed;
        # only the manual reconnect re-activated it before — an
        # automatic reconnect (a transport handover) left the model
        # alive but the discovery chain dead forever (the harness's
        # suite handover scenario caught it: webcams empty,
        # temperatures gone).
        model, client, _transport = self.monitor()
        self.assertTrue(model._data._active)
        client.sessionInvalidated.emit()
        self.assertFalse(model._data._active)
        client.connectionChanged.emit(True, "Moonraker connected over websocket")
        self.assertTrue(model._data._active)

    def test_discovery_watchdog_refires_the_dead_chain(self):
        # On ~30-40% of cold boots the discovery chain arms dead and
        # stays dead until a reconnect or a Klippy restart (the
        # harness's boot probes). The watchdog fires 3 s after the
        # connect and heals it exactly the way the Klippy-ready
        # broadcast does: the discovery re-fire + the re-subscribe.
        model, client, transport = self.monitor()
        self.assertTrue(model._data._active)
        client.connectionChanged.emit(True, "Moonraker connected over websocket")
        self.assertTrue(model._data._watchdog.isActive())
        # The dead state: no objects list, no aux data arrived (the
        # snapshot is a frozen dataclass — updated through _update).
        model._data._update(objects=(), auxiliary={})
        before = len(transport.requests)
        model._data._watch_discovery()
        self.assertGreater(len(transport.requests), before,
                           "the watchdog must re-fire the discovery requests")
        self.assertFalse(model._data._watchdog.isActive(),
                         "the watchdog is one-shot per connect")

    def test_m117_message_publishes_from_the_aux_snapshot(self):
        # The aux snapshot's objects arrive as mappingproxies (the
        # MonitorData contract); the M117 slot reads the message from
        # whatever mapping shape they take. The isinstance(dict)
        # check never matched the live shape and the message never
        # reached the slot (the live report; the harness's
        # scenario 3 caught it).
        model, _client, _transport = self.monitor()
        model._data._update(auxiliary={"display_status": {"message": "probe-m117-x", "progress": 0.6}})
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.monitorMessage, "probe-m117-x")

    def test_filtered_aux_refresh_preserves_the_discovery_settings(self):
        # The diameter read's residency claim (round-1 B1, round-2
        # engineering F2): the discovery lane delivers the WHOLE
        # configfile — settings included — and the later
        # field-filtered aux refresh must not erase it: the merge
        # carries previous keys through (MonitorData._merge_aux).
        model, _client, _transport = self.monitor()
        data = model._data
        data._objects({"result": {"objects": ["print_stats", "configfile", "toolhead"]}}, None)
        data._aux({"result": {"status": {"configfile": {
            "save_config_pending": False, "save_config_pending_items": {},
            "config": {"extruder": {"filament_diameter": "1.75"}},
            "settings": {"extruder": {"filament_diameter": 1.75}}}}}}, None)
        # The aux lane's filtered refresh carries ONLY the save-config
        # fields for configfile — settings must survive from before.
        data._aux({"result": {"status": {"configfile": {
            "save_config_pending": True, "save_config_pending_items": {}}}}}, None)
        configfile = data.snapshot.auxiliary.get("configfile") or {}
        self.assertTrue(configfile.get("save_config_pending"))
        self.assertEqual(configfile.get("settings", {}).get("extruder", {}).get("filament_diameter"), 1.75)

    def test_connection_state_is_tristate_until_observed(self):
        # The policy prerequisite (round-2 A2/S2/F3): 'unknown' until
        # this session has observed a connect, then 'yes'/'no'; a
        # session invalidation resets to 'unknown'. The legacy bool
        # `connected` keeps its exact old semantics throughout.
        model, client, _transport = self.monitor()
        data = model._data
        self.assertEqual(data.connection_state, "unknown")
        self.assertFalse(data.connected)
        # The real connect flow flips the client's flag before the
        # signal — mirror that order on the scripted client.
        client._connected = True
        client.connectionChanged.emit(True, "Moonraker connected over http polling")
        self.assertEqual(data.connection_state, "yes")
        self.assertTrue(data.connected)
        client._connected = False
        client.connectionChanged.emit(False, "polling stopped")
        self.assertEqual(data.connection_state, "no")
        self.assertFalse(data.connected)
        # A session invalidation is a fresh generation: unknown again.
        data.set_owner_active(False)
        self.assertEqual(data.connection_state, "unknown")

    def test_observation_carries_the_pushins_and_the_assumption(self):
        # The record assembles in MonitorData with the two closed
        # push-ins (controlsLocked from the model chrome, busy from
        # the command lane) and the client's e-stop assumption.
        model, client, _transport = self.monitor()
        data = model._data
        data.set_controls_locked(True)
        data.set_commands_busy(True)
        client.assume_print_stopped()
        data._update(core={"print_stats": {"state": "printing"}}, auxiliary={
            "toolhead": {"homed_axes": "xyz"},
            "configfile": {"save_config_pending": False}})
        obs = data.observation
        self.assertTrue(obs.controls_locked)
        self.assertTrue(obs.busy)
        self.assertTrue(obs.assumed_stopped)
        self.assertEqual(obs.state, "printing")
        self.assertEqual(obs.homed_axes, "xyz")

    def test_unknown_machine_migration_is_retried_when_stack_appears(self):
        prefs = harness.Preferences({"moonrakerprintfollower/url": "http://legacy",
                             "moonrakerprintfollower/enabled": True})
        app, follower, transport = self.follower(preferences=prefs, machine=False)
        key = self.qt.load("PrinterConfig").PrinterConfigStore.MIGRATED_KEY
        self.assertFalse(prefs.getValue(key))
        app.stack = self.qt.Machine("A")
        app.globalContainerStackChanged.emit()
        self.assertEqual(transport.identity[0], "http://legacy")
        self.assertTrue(prefs.getValue(key))

    def test_failed_migration_does_not_prevent_bootstrap(self):
        config_type = self.qt.load("PrinterConfig").PrinterConfigStore
        with harness.patch.object(config_type, "migrate_legacy_to_current_machine", side_effect=ValueError("old data")):
            self.follower()

    def test_unconfigured_url_placeholder_does_not_start_networking(self):
        app, follower, transport = self.follower()
        self.assertEqual(transport.requests, [])
        binding = self.qt.load("PrinterBinding").PrinterBinding
        for placeholder in ("", "http://", "https://", "http:", "https:"):
            self.assertFalse(binding.usable(self.qt.load("PrinterConfig").normalise_url(placeholder)))

    def test_override_detach_stays_detached_until_the_user_reattaches(self):
        # The ruling: ANY layer intervention detaches, and the
        # detach persists — no watchdog, no snap-back. The view-swap
        # re-attach (leaving the stage while attached) is the only
        # automatic one.
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True, feed_mode="http"))
        coordinator = follower._runtime.coordinator
        preview = coordinator._preview
        view = harness.SimpleNamespace(layer=4, minimum=0, path=0.0, minpath=0)
        view.getCurrentLayer = lambda: view.layer
        view.getMinimumLayer = lambda: view.minimum
        view.getCurrentPath = lambda: view.path
        view.getMinimumPath = lambda: view.minpath
        app.controller.view = view
        coordinator._cura._view = view
        preview.attach(True)
        view.layer = 10
        coordinator._position_changed()
        self.assertFalse(preview.state.attached)
        # Continued movement and the passage of time change nothing:
        # the detach holds until the user re-attaches.
        view.layer = 11
        coordinator._position_changed()
        self.qt.events(4000)
        self.assertFalse(preview.state.attached)

    def test_scheduled_pause_does_not_fire_against_an_ended_print(self):
        # 4.2.0 N4: the autonomous dispatch re-checks an OBSERVED
        # terminal state — the poll that called observe may already
        # be stale, and a PAUSE against a finished print must not go
        # out.
        client, transport = self.client()
        pauses = self.qt.load("PauseController").PauseController(client)
        self.addCleanup(pauses.close)
        pauses.bind(("part", 100, 1))
        self.assertTrue(pauses.toggle(4, 0, 10))
        client._handle_http_status({"result": {"status": {"print_stats": {"state": "complete"}}}},
                                   None, client._generation, harness.time.monotonic())
        pauses.observe(5)
        self.assertEqual(pauses.states, {})
        self.assertEqual([r for r in transport.requests if r.channel == "scheduled"], [])

    def test_pause_entry_leaves_only_when_observed_paused(self):
        # The verified-pause-only ruling, amended 2026-09-16: an
        # OBSERVED pause leaves the list only when the user removes
        # it — it stays, dimmed "passed". A missed pause stays
        # listed, marked.
        client, transport = self.client()
        pauses = self.qt.load("PauseController").PauseController(client)
        self.addCleanup(pauses.close)
        pauses.bind(("part", 100, 1))
        self.assertTrue(pauses.toggle(4, 0, 10))
        pauses.observe(5)
        self.assertEqual(pauses.states, {4: "fired"})
        self.assertIn(4, pauses.layers)
        request = next(r for r in transport.requests if r.channel == "scheduled")
        request.callback({"result": {}}, None)  # the PAUSE script was accepted
        client._handle_http_status({"result": {"status": {"print_stats": {"state": "paused"}}}},
                                   None, client._generation, harness.time.monotonic())
        self.qt.events(1)
        self.assertIn(4, pauses.layers)
        self.assertEqual(pauses.states.get(4), "passed")
        pauses.remove(4)
        self.assertNotIn(4, pauses.layers)
        # A missed pause stays listed, restyled — never silently dropped.
        self.assertTrue(pauses.toggle(6, 0, 10))
        pauses.observe(7)
        self.assertEqual(pauses.states.get(6), "fired")
        client.session.commands.get("ScheduledPause").issued_at -= 20
        client.expire_commands()
        self.qt.events(1)
        self.assertIn(6, pauses.layers)
        self.assertEqual(pauses.states.get(6), "timed_out")

    def test_connection_edit_invalidates_follower_domains(self):
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", feed_mode="http"))
        follower.client.statusReceived.emit({"print_stats": {"state": "printing", "filename": "same.gcode"}, "virtual_sdcard": {"file_size": 100}})
        follower._runtime.pauses.toggle(4, 0, 10)
        generation = follower._runtime.cura.generation
        follower.apply_printer_config(config_type(url="http://printer-b", feed_mode="http"))
        self.assertIsNone(follower.print_state.job_key)
        self.assertFalse(follower._runtime.pauses.layers)
        self.assertGreater(follower._runtime.cura.generation, generation)
        self.assertEqual(transport.identity[0], "http://printer-b")

    def test_same_endpoint_machine_switch_still_invalidates_generation(self):
        prefs = harness.Preferences({"moonrakerprintfollower/printer_configs_v1": harness.json.dumps({
            "A": {"url": "http://same"}, "B": {"url": "http://same"}})})
        app, follower, transport = self.follower(preferences=prefs)
        generation = follower.session.generation
        app.stack = self.qt.Machine("B")
        app.globalContainerStackChanged.emit()
        self.assertEqual(transport.identity[0], "http://same")
        self.assertGreater(follower.session.generation, generation)

    def test_active_print_status_executes_real_follower_metadata_path(self):
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True, path_follow=False, feed_mode="http"))
        # The metadata pull serves the Preview, so it only runs once the
        # print's G-code is loaded in Cura.
        app.controller.view = harness.SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
        app.controller.activeViewChanged.emit()
        self.qt.events()
        follower.client.statusReceived.emit({"print_stats": {"state": "printing", "filename": "part.gcode",
            "info": {"current_layer": 2}}, "virtual_sdcard": {"file_size": 100}})
        self.assertEqual(follower.print_state.observation.filename, "part.gcode")
        self.assertTrue(any(r.channel == "metadata" for r in transport.requests))

    def test_unloaded_print_does_not_pull_metadata_or_index(self):
        # A print that is active on Moonraker but not loaded in Cura must
        # not download its metadata or G-code: the status stays quiet
        # until the user loads the print.
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True, feed_mode="http"))
        follower.client.statusReceived.emit({"print_stats": {"state": "printing", "filename": "part.gcode",
            "info": {"current_layer": 2}}, "virtual_sdcard": {"file_size": 100}})
        self.assertFalse(any(r.channel == "metadata" for r in transport.requests))

    def test_stale_index_completion_does_not_install_into_new_job(self):
        app, follower, transport = self.follower()
        service = follower._runtime.index
        service.bind(("old.gcode", 100, 1))
        old = service.generation
        service.bind(("new.gcode", 100, 2))
        index = self.qt.load("GCodeIndex").LayerMotionIndex(ranges=[(0, 100)])
        service._finish(old, "build", index, None, None)
        self.assertIsNone(service.view)
        self.assertGreater(service.generation, old)

    def test_same_file_restart_cannot_lose_new_metadata_reservation(self):
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", path_follow=False, feed_mode="http"))
        files = follower._runtime.files
        files.bind(("part.gcode", 100, 1))
        files.request_metadata()
        old = transport.requests[-1]
        files.bind(("part.gcode", 100, 2))
        files.request_metadata()
        current = transport.requests[-1]
        old.callback({"result": {"size": 100}}, None)
        self.assertEqual(files.phase, "resolving")
        current.callback({"result": {"size": 100}}, None)
        self.assertEqual(files.phase, "idle")
        self.assertEqual(files.identity.filename, "part.gcode")
        self.assertEqual(files.job_key, ("part.gcode", 100, 2))

    def test_pending_scheduled_pause_keeps_original_command_identity(self):
        app, follower, transport = self.follower()
        follower.apply_printer_config(self.qt.load("PrinterConfig").PrinterConfig(url="http://printer-a", feed_mode="http"))
        pauses = follower._runtime.pauses
        pauses.bind(("part.gcode", 100, 1))
        pauses.toggle(2, 1, 10)
        pauses.toggle(3, 1, 10)
        pauses.observe(3)
        pauses.observe(4)
        self.assertEqual(pauses._target, 2)
        self.assertEqual(sum(r.owner == "pause" for r in transport.requests), 1)

    def test_stale_core_reply_cannot_complete_new_coalescer_slot(self):
        client, transport = self.client()
        client.start()
        old = transport.requests[-1]
        client.configure("http://printer-b", "new-key", 750)
        self.assertTrue(client.session.coalescer.is_in_flight("core"))
        old.callback({"result": {"status": {}}}, None)
        self.assertTrue(client.session.coalescer.is_in_flight("core"))
        self.assertEqual(client.status, {})

    def test_periodic_ticks_do_not_queue_forced_followups(self):
        client, transport = self.client()
        client.start()
        first = transport.requests[-1]
        for _ in range(10):
            client._poll_timer.timeout.emit()
        first.callback({"result": {"status": {"print_stats": {"state": "printing"}}}}, None)
        self.qt.events()
        self.assertEqual(len(transport.requests), 1)

    def test_forced_refreshes_coalesce_to_exactly_one_followup(self):
        client, transport = self.client()
        client.start()
        for _ in range(10): client.force_refresh()
        transport.requests[0].callback({"result": {"status": {}}}, None)
        self.qt.events()
        self.assertEqual(len(transport.requests), 2)

    def test_failure_backoff_survives_pending_and_forced_refreshes(self):
        client, transport = self.client()
        client.start()
        # A PROVEN endpoint: the ladder protects its outages. (The
        # never-connected cap — the eager first connection, the
        # ruling — is pinned in the client feed tests.)
        transport.requests[-1].callback({"result": {"status": {"print_stats": {"state": "idle"}}}}, None)
        self.qt.events()
        client.force_refresh()
        transport.requests[-1].callback(None, "offline")
        self.qt.events()
        client.set_pause_guard(True)
        client.force_refresh()
        self.assertEqual(len(transport.requests), 2)
        self.assertGreaterEqual(client._poll_timer.interval(), 5000)

    def test_queued_core_refresh_is_discarded_after_rebind(self):
        client, transport = self.client()
        client.start()
        client.force_refresh()
        transport.requests[0].callback({"result": {"status": {}}}, None)
        client.configure("http://printer-b", "", 750)
        self.qt.events()
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse(client.session.coalescer.complete("core"))

    def test_status_subscriber_can_rebind_without_corrupting_new_request(self):
        client, transport = self.client()
        client.start()
        client.statusReceived.connect(lambda status: client.configure("http://printer-b", "", 750))
        transport.requests[0].callback({"result": {"status": {}}}, None)
        self.assertTrue(client.session.coalescer.is_in_flight("core"))
        self.assertEqual(client.status, {})

    def test_malformed_core_result_is_failure_not_connection_success(self):
        client, transport = self.client()
        client.start()
        transport.requests[0].callback({"result": {}}, None)
        self.assertFalse(client.connected)
        self.assertEqual(client.session.snapshot.revision, 0)

    def test_capability_subscriber_rebind_discards_old_status(self):
        client, transport = self.client()
        client.start()
        statuses = []
        client.statusReceived.connect(statuses.append)
        client.capabilitiesChanged.connect(lambda caps:
            client.configure("http://printer-b", "", 750) if caps["objects"] else None)
        transport.requests[0].callback({"result": {"status": {"print_stats": {"state": "printing"}}}}, None)
        self.assertEqual(statuses, [])
        self.assertTrue(client.session.coalescer.is_in_flight("core"))

    def test_command_timer_expires_without_successful_polls(self):
        client, transport = self.client()
        events = []
        client.commandChanged.connect(events.append)
        client.track_command("ScheduledPause", {"paused"}, timeout_s=0.1)
        client.accept_command("ScheduledPause")
        self.qt.events(300)
        self.assertEqual(events[-1]["outcome"], "timed_out")
        self.assertFalse(client._command_timer.isActive())

    def test_expiry_and_unrelated_patch_cannot_confirm_cached_state(self):
        client, transport = self.client()
        client.session.merge_status({"print_stats": {"state": "paused"}})
        client.track_command("Pause", {"paused"})
        client.accept_command("Pause")
        client.expire_commands()
        client.session.merge_status({"gcode_move": {"speed_factor": 1}})
        self.assertFalse(client.session.commands.get("Pause").terminal)
        client.session.merge_status({"print_stats": {"state": "paused"}})
        self.assertEqual(client.session.commands.get("Pause").outcome, "confirmed")

    def test_the_controls_speed_projection_follows_a_live_change(self):
        # The harness's c2-05/06 regression (the 5.7/5.8 re-verify):
        # a live gcode_move speed/flow change must reach the model's
        # published projection — the readout sat at 100 while the sim
        # moved to 1.37/1.28.
        model, client, transport = self.monitor()
        model.updateMoonrakerStatus({"gcode_move": {"speed_factor": 1.0, "extrude_factor": 1.0}})
        self.qt.events(300)
        self.assertEqual(model.speedFactorPercent, 100)
        model.updateMoonrakerStatus({"gcode_move": {"speed_factor": 1.37, "extrude_factor": 1.28}})
        self.qt.events(300)
        self.assertEqual(model.speedFactorPercent, 137)
        self.assertEqual(model.flowFactorPercent, 128)

    def test_monitor_unchanged_intervals_are_not_restarted(self):
        model, client, transport = self.monitor()
        timer = next(iter(model._data._timers.values()))
        with harness.patch.object(timer, "setInterval", wraps=timer.setInterval) as setter:
            for _ in range(20): model.updateMoonrakerStatus({})
        setter.assert_not_called()

    def test_monitor_background_timers_fire_during_frequent_status_updates(self):
        model, client, transport = self.monitor()
        policy = self.qt.load("MoonrakerSession").PollPolicy
        # Auxiliary/console pace from the user's cadence, floored at
        # 250 ms (the sliders ruling); the policy constants now pace
        # only power/system/endstops/discovery.
        client.configure("http://printer-a", "test-key", 750, aux_interval_ms=250, console_interval_ms=250)
        client.session.state.poll_policy = policy(power_ms=40, system_ms=50, endstops_ms=70, console_idle_ms=250, discovery_ms=60)
        model._data._intervals()
        counts = [0, 0, 0, 0, 0, 0]  # auxiliary, power, system, endstops, console, discovery
        for index, timer in enumerate(model._data._timers.values()):
            timer.timeout.connect(lambda i=index: counts.__setitem__(i, counts[i] + 1))
        updates = self.qt.QTimer()
        updates.setInterval(5)
        updates.timeout.connect(lambda: model.updateMoonrakerStatus({}))
        updates.start()
        # The two cadence timers need 250 ms each, so the window must
        # outlast the floor, not just the short constant timers.
        self.qt.events(400)
        updates.stop()
        self.assertTrue(all(count >= 1 for count in counts), counts)

    def test_monitor_idle_active_policy_and_disconnect(self):
        model, client, transport = self.monitor()
        timer = next(iter(model._data._timers.values()))
        self.assertEqual(timer.interval(), 2500)
        client.session.merge_status({"print_stats": {"state": "printing"}})
        model.updateMoonrakerStatus(client.status)
        self.assertEqual(timer.interval(), 2500)
        client.connectionChanged.emit(False, "offline")
        self.assertEqual(model.monitorState, "Disconnected")

    def test_deactivated_monitor_clears_peripherals_and_delayed_refreshes(self):
        model, client, transport = self.monitor(typed=True)
        callback = harness.Mock()
        model._data._update(auxiliary={"fan": {"speed": 1}})
        model._data.later(0, callback)
        model.setMonitoringActive(False)
        model.setMonitoringActive(True)
        self.qt.events()
        callback.assert_not_called()
        self.assertEqual(dict(model._data.snapshot.auxiliary), {})

    def test_write_is_cancelled_before_credentials_change(self):
        app, device, plugin, client, transport = self.output()
        path = device._upload._source.path
        completed, identities = [], []
        device.writeFinished.connect(completed.append)
        client.sessionInvalidated.connect(lambda: identities.append(transport.identity))
        client.configure("http://printer-b", "new-key", 750)
        self.assertFalse(device._upload._active)
        self.assertFalse(harness.os.path.exists(path))
        self.assertEqual(identities, [("http://printer-a", "test-key")])
        self.qt.events()
        self.assertEqual(completed, [device])

    def test_old_write_timer_cannot_run_after_new_write_same_device(self):
        app, device, plugin, client, transport = self.output()
        callback = harness.Mock()
        device._upload._later(20, callback)
        device.deactivate()
        self.qt.events()
        device.requestWrite(None)
        self.qt.events(40)
        callback.assert_not_called()
        self.assertTrue(device._upload.busy)

    def test_cancel_is_deferred_and_completes_once_without_write_error(self):
        app, device, plugin, client, transport = self.output()
        completed, errors = [], []
        device.writeFinished.connect(completed.append)
        device.writeError.connect(errors.append)
        path = device._upload._source.path
        device.cancelUpload()
        self.assertTrue(harness.os.path.exists(path))
        self.qt.events()
        self.assertFalse(harness.os.path.exists(path))
        self.assertEqual(completed, [device])
        self.assertEqual(errors, [])
        device.deactivate()
        self.qt.events()
        self.assertEqual(completed, [device])

    def test_new_write_cannot_overtake_previous_terminal_signal(self):
        app, device, plugin, client, transport = self.output()
        device.deactivate()
        error_type = self.qt.load("MoonrakerOutputDevice").OutputDeviceError.DeviceBusyError
        with self.assertRaises(error_type):
            device.requestWrite(None)
        self.qt.events()
        device.requestWrite(None)
        self.assertTrue(device._upload.busy)

    def test_upload_abort_can_finish_synchronously_without_double_completion(self):
        app, device, plugin, client, transport = self.output()
        completed, errors = [], []
        device.writeFinished.connect(completed.append)
        device.writeError.connect(errors.append)
        reply = harness.Mock()
        reply.isRunning.return_value = True
        generation = device._upload._generation
        reply.abort.side_effect = lambda: device._upload._uploaded(reply, generation)
        device._upload._reply = reply
        device.deactivate()
        self.qt.events()
        reply.abort.assert_called_once()
        self.assertEqual(completed, [device])
        self.assertEqual(errors, [])

    def test_late_json_callback_cannot_affect_new_write(self):
        app, device, plugin, client, transport = self.output()
        callback = harness.Mock()
        device._upload._request("GET", "server/info", callback)
        old = transport.requests[-1]
        device.deactivate()
        self.qt.events()
        device.requestWrite(None)
        old.callback({"result": {"klippy_state": "ready"}}, None)
        callback.assert_not_called()

    def test_real_http_transport_json_errors_and_owner_cancellation(self):
        received = []
        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                received.append(self.headers.get("X-Api-Key"))
                body = b"[]" if self.path == "/bad" else b'{"result":{"ok":true}}'
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The owner-cancellation test deliberately closes a socket.
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.close)
        results = []
        transport.send_json("test", "good", "GET", "/good", lambda p, e: results.append((p, e)))
        transport.send_json("test", "bad", "GET", "/bad", lambda p, e: results.append((p, e)))
        cancelled = harness.Mock()
        transport.send_json("cancelled", "one", "GET", "/good", cancelled)
        transport.cancel_owner("cancelled")
        for _ in range(100):
            if len(results) == 2: break
            self.qt.events(10)
        self.assertEqual(len(results), 2)
        self.assertTrue(any(p == {"result": {"ok": True}} and e is None for p, e in results))
        self.assertTrue(any(e and "non-object" in e for p, e in results))
        self.assertTrue(all(key == "test-key" for key in received))
        cancelled.assert_not_called()

    def test_camera_bridge_relays_the_stream_with_the_key(self):
        # The key-carrying republisher: the loader asks a keyless
        # loopback port; the bridge fetches the same path upstream WITH
        # the key and relays the multipart stream verbatim.
        from PyQt6.QtNetwork import QTcpSocket
        received = []

        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get("X-Api-Key")))
                body = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8\xff\xd9\r\n"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args): pass

        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        bridge = self.qt.load("CameraBridge").CameraBridge()
        self.assertTrue(bridge.configure("http://127.0.0.1:" + str(server.server_port), "test-key"))
        self.assertGreater(bridge.port, 0)
        self.addCleanup(bridge.stop)
        socket = QTcpSocket()
        self.addCleanup(socket.abort)
        socket.connectToHost("127.0.0.1", bridge.port)
        socket.write(b"GET /webcam/?action=stream HTTP/1.1\r\nHost: local\r\n\r\n")
        payload = bytearray()
        for _ in range(200):
            self.qt.events(10)
            payload.extend(bytes(socket.readAll()))
            if b"--frame" in payload:
                break
        self.assertIn(b"--frame", bytes(payload))
        self.assertIn(b"multipart/x-mixed-replace", bytes(payload))
        self.assertTrue(any(path == "/webcam/?action=stream" and key == "test-key"
                            for path, key in received), received)
        # The bridge must release its relay objects: the accepted
        # socket is parented to the server and the reply to the NAM —
        # without deleteLater both survive every request (the live
        # report: 100 completed requests left 100 sockets alive).
        socket.abort()
        for _ in range(200):
            self.qt.events(10)
            if not bridge._server.findChildren(QTcpSocket):
                break
        self.assertEqual(bridge._server.findChildren(QTcpSocket), [])
        self.assertEqual(bridge._relays, {})

    def test_cancelled_consumer_aborts_the_upstream_before_first_bytes(self):
        # E (the 2026-09-19 review): a local client that disconnects
        # before the first upstream bytes MUST abort its upstream —
        # this is the exact mechanism the double-start produced
        # (T7 with no T8). The server side sees the abort as a closed
        # connection.
        from PyQt6.QtNetwork import QTcpSocket
        closed = harness.threading.Event()
        entered = harness.threading.Event()

        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                entered.set()
                try:
                    while self.rfile.read(4096):
                        pass
                except Exception:
                    pass
                closed.set()

            def log_message(self, *_args): pass

        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        bridge = self.qt.load("CameraBridge").CameraBridge()
        self.assertTrue(bridge.configure("http://127.0.0.1:" + str(server.server_port), "test-key"))
        self.addCleanup(bridge.stop)
        socket = QTcpSocket()
        self.addCleanup(socket.abort)
        socket.connectToHost("127.0.0.1", bridge.port)
        socket.write(b"GET /webcam/?action=stream HTTP/1.1\r\nHost: local\r\n\r\n")
        for _ in range(200):
            self.qt.events(10)
            if any(relay[0] is not None for relay in bridge._relays.values()):
                break
        # The bridge keys its relays on the SERVER-side socket object —
        # a different Python wrapper for the same connection.
        self.assertTrue(any(relay[0] is not None for relay in bridge._relays.values()),
                        "the upstream must be created before the cancel")
        # A created QNetworkReply can still have its GET queued. Prove
        # the server received it so closure is observable there, rather
        # than mistaking cancellation before connection for a leak.
        for _ in range(200):
            self.qt.events(10)
            if entered.is_set():
                break
        self.assertTrue(entered.is_set(), "the upstream must receive the GET before cancellation")
        socket.abort()
        for _ in range(200):
            self.qt.events(10)
            if not bridge._relays:
                break
        self.assertEqual(bridge._relays, {}, "the cancelled consumer's relay must release")
        self.assertTrue(closed.wait(2.0), "the upstream must see the connection close")
        self.assertFalse(bridge._first_upstream_bytes)

    def test_recovery_signal_means_first_proven_bytes(self):
        # F (the 2026-09-19 review): issuing a request is NOT
        # recovery; the first real upstream bytes are, exactly once
        # per failure transition.
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtNetwork import QNetworkReply, QTcpSocket
        bridge = self.qt.load("CameraBridge").CameraBridge()
        self.assertTrue(bridge.configure("http://127.0.0.1:9", "test-key"))
        self.addCleanup(bridge.stop)
        emitted = []
        bridge.upstreamStarted.connect(lambda: emitted.append(1))

        class BytesReply(QObject):
            readyRead = pyqtSignal()
            finished = pyqtSignal()

            def __init__(self):
                super().__init__()
                self._error = QNetworkReply.NetworkError.NoError

            def setReadBufferSize(self, _size): pass
            def readAll(self): return b""
            def bytesAvailable(self): return 46314
            def attribute(self, _name): return None
            def header(self, _name): return None
            def error(self): return self._error
            def errorString(self): return "simulated"
            def abort(self): pass
            def deleteLater(self): pass

        replies = []
        def fake_get(_request):
            reply = BytesReply()
            replies.append(reply)
            return reply

        socket = QTcpSocket()
        self.addCleanup(socket.abort)
        socket.connectToHost("127.0.0.1", bridge.port)
        socket.write(b"GET /webcam/?action=stream HTTP/1.1\r\nHost: local\r\n\r\n")
        bridge._nam.get = fake_get
        for _ in range(200):
            self.qt.events(10)
            relay = bridge._relays.get(socket)
            if relay is not None and relay[0] is not None:
                break
        self.assertEqual(1, len(replies))
        self.assertEqual([], emitted, "issuing the request is not recovery")
        # The bridge keys its relays on the SERVER-side socket wrapper;
        # the client-side Python object is a different wrapper.
        server_socket = next(iter(bridge._relays))
        reply = replies[0]
        bridge._on_upstream_ready(server_socket, reply)
        bridge._on_upstream_ready(server_socket, reply)
        self.assertEqual([1], emitted, "first bytes mark recovery exactly once")
        bridge._stream_healthy = False
        bridge._on_upstream_ready(server_socket, reply)
        self.assertEqual([1, 1], emitted, "recovery re-arms after a failure transition")

    def test_real_http_thumbnail_fetch_follows_metadata_path(self):
        # Live-proven: a real Moonraker answers <file>.png with 404 —
        # the thumbnail lives at the metadata's relative_path under
        # .thumbs/. The service must fetch THAT path, land a valid PNG
        # in its session cache, publish a file:// URL, and never fetch
        # rows whose metadata has no thumbnail.
        from PyQt6.QtCore import QUrl
        requests = []
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d49444154789c636460f85f0f0002850100af47ba920000000049454e44ae426082"
        )
        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                requests.append(self.path)
                body = png if self.path.endswith(".thumbs/test-300x300.png") else b'{"error": {"code": 404, "message": "Not Found"}}'
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.close)
        manager = self.qt.load("FileManager").FileManager(harness.SimpleNamespace(transport=transport))
        FileRow = self.qt.load("FileManagerPolicy").FileRow
        manager.request_thumbnails([
            FileRow(filename="test.gcode", relpath="test.gcode", root="gcodes",
                    thumb_path=".thumbs/test-300x300.png"),
            FileRow(filename="plain.gcode", relpath="plain.gcode", root="gcodes"),
        ])
        for _ in range(200):
            entry = manager.thumbnail_payload().get("test.gcode") or {}
            if entry.get("state") == "ready":
                break
            self.qt.events(10)
        payload = manager.thumbnail_payload()
        self.assertEqual(payload["test.gcode"]["state"], "ready")
        with open(QUrl(payload["test.gcode"]["url"]).toLocalFile(), "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertEqual(payload["plain.gcode"]["state"], "none")
        self.assertEqual(requests, ["/server/files/gcodes/.thumbs/test-300x300.png"])

    def test_real_http_delete_files_surfaces_the_refusal_words(self):
        # Snapshot 3: deleting the printing file draws Moonraker's
        # 403 — the service must keep the row and put the server's
        # own words in the note (the ruling: refusals
        # surface, never vanish).
        class Handler(harness.PipeSafeHandler):
            def do_DELETE(self):
                body = b'{"error": {"code": 403, "message": "File currently in use"}}'
                try:
                    self.send_response(403)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.close)
        manager = self.qt.load("FileManager").FileManager(harness.SimpleNamespace(transport=transport))
        # Seed the resident row directly — the delete path needs no
        # walk.
        FileRow = self.qt.load("FileManagerPolicy").FileRow
        manager._rows["gcodes/busy.gcode"] = FileRow(
            filename="busy.gcode", relpath="busy.gcode", root="gcodes")
        notes = []
        manager.note.connect(notes.append)
        manager.delete_files(["busy.gcode"], "")
        for _ in range(200):
            if notes:
                break
            self.qt.events(10)
        self.assertEqual(notes, ["Delete refused: File currently in use"])
        self.assertIsNotNone(manager.row_for("busy.gcode"))

    def test_real_http_upload_posts_the_multipart_to_the_current_directory(self):
        # Snapshot 3 upload over a real socket: the multipart body
        # carries the file, the root and the current directory; a
        # success notes and refreshes.
        received = []
        class Handler(harness.PipeSafeHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append((self.path, self.rfile.read(length)))
                payload = b'{"result": {"item": {"path": "gcodes/prints/bench.gcode"}}}'
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.close)
        manager = self.qt.load("FileManager").FileManager(harness.SimpleNamespace(transport=transport))
        manager._directory = ["prints"]
        notes = []
        progress_events = []
        finished_events = []
        manager.note.connect(notes.append)
        manager.uploadProgress.connect(progress_events.append)
        manager.uploadFinished.connect(lambda ok, detail: finished_events.append((ok, detail)))
        directory = harness.tempfile.mkdtemp()
        source = harness.os.path.join(directory, "bench.gcode")
        with open(source, "wb") as handle:
            handle.write(b"; test gcode\n")
        self.assertTrue(manager.upload_file(source))
        for _ in range(300):
            if finished_events:
                break
            self.qt.events(10)
        self.assertEqual(len(received), 1)
        path, body = received[0]
        self.assertEqual(path, "/server/files/upload")
        self.assertIn(b'name="file"; filename="bench.gcode"', body)
        self.assertIn(b'name="root"', body)
        self.assertIn(b"gcodes", body)
        self.assertIn(b'name="path"', body)
        self.assertIn(b"prints", body)
        # The popup's feed: progress reached 100 and the verdict is
        # the success (the live request).
        self.assertEqual(finished_events, [(True, "bench.gcode")])
        self.assertEqual(max(progress_events), 100)
        self.assertEqual(notes, ["Uploaded bench.gcode."])

    def test_real_http_delete_verb_key_stripping_and_refusal_bodies(self):
        # Round-2 E1/F2/F4 against a real socket: the delete must
        # arrive as HTTP DELETE at the file's own URL (the in-process
        # fake cannot prove the verb), the key must never ride a
        # foreign origin, a 403's JSON body must surface as the
        # refusal message, and unknown verbs must fail loudly.
        seen = []
        class Handler(harness.PipeSafeHandler):
            def do_DELETE(self):
                seen.append(("DELETE", self.path, self.headers.get("X-Api-Key")))
                self._ok(b"{}")
            def do_GET(self):
                if self.path == "/refused":
                    self._respond(403, b'{"error": {"message": "File currently in use"}}')
                else:
                    seen.append(("GET", self.path, self.headers.get("X-Api-Key")))
                    self._ok(b"{}")
            def _ok(self, body):
                self._respond(200, body)
            def _respond(self, status, body):
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args):
                pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        foreign_server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        foreign_thread = harness.threading.Thread(target=foreign_server.serve_forever, daemon=True)
        foreign_thread.start()
        self.addCleanup(foreign_server.server_close)
        self.addCleanup(foreign_server.shutdown)

        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        base = "http://127.0.0.1:" + str(server.server_port)
        transport.configure(base, "test-key")
        self.addCleanup(transport.close)
        results = []
        transport.send_json("fm", "delete", "DELETE", "server/files/gcodes/foo.gcode", lambda p, e: results.append(("delete", p, e)))
        transport.send_json("fm", "refused", "GET", "/refused", lambda p, e: results.append(("refused", p, e)))
        transport.send_json("fm", "foreign", "GET",
            "http://127.0.0.1:" + str(foreign_server.server_port) + "/x",
            lambda p, e: results.append(("foreign", p, e)))
        with self.assertRaises(ValueError):
            transport.send_json("fm", "bogus", "PATCH", "/x", lambda p, e: None)
        for _ in range(200):
            if len(results) == 3:
                break
            self.qt.events(10)
        self.assertEqual(len(results), 3)
        delete_row = next(row for row in seen if row[0] == "DELETE")
        self.assertEqual(delete_row[1], "/server/files/gcodes/foo.gcode")
        self.assertEqual(delete_row[2], "test-key")
        refused = next(row for row in results if row[0] == "refused")
        self.assertIsNotNone(refused[1], "the refusal body must stay in the payload")
        self.assertIn("File currently in use", refused[2] or "")
        foreign_seen = next(row for row in seen if row[0] == "GET" and row[1] == "/x")
        self.assertIsNone(foreign_seen[2], "the key must not ride a foreign origin")
        foreign = next(row for row in results if row[0] == "foreign")
        self.assertIsNotNone(foreign[1])

    def test_real_http_moonraker_400_surfaces_the_tracebacks_words(self):
        # A live report: a cold extrude surfaced a bare
        # 400 while Moonraker's real words sat in the traceback tail
        # ({'code', 'message': 'Unknown', 'traceback'}). The error
        # must read "Extrude below minimum temp", not a status code
        # or the whole dict — and the script endpoint answers HTTP
        # 200 with the error DICT inside "error" (the
        # second report: "Extrude refused: {'code': 400, ...}").
        def body_with(shape):
            inner = {
                "code": 400,
                "message": (
                    "Traceback (most recent call last):\n"
                    "  File \"application.py\", line 707, in _process_http_request\n"
                    "moonraker.utils.exceptions.ServerError: Extrude below minimum temp\n"
                    "See the 'min_extrude_temp' config option for details\n"
                ),
                "traceback": (
                    "Traceback (most recent call last):\n\n"
                    "  File \"application.py\", line 707, in _process_http_request\n"
                    "    raise tornado.web.HTTPError(\n"
                    "        e.status_code, reason=str(e)) from e\n"
                    "tornado.web.HTTPError: HTTP 400: Extrude below minimum temp\n"
                    "See the 'min_extrude_temp' config option for details\n"
                ),
            }
            return harness.json.dumps(inner if shape == "flat" else {"error": inner}).encode()
        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                status = 200 if self.path == "/rpc" else 400
                body = body_with("nested" if self.path == "/rpc" else "flat")
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args):
                pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.close)
        results = []
        transport.send_json("fm", "cold", "GET", "/cold", lambda p, e: results.append(("flat", p, e)))
        transport.send_json("fm", "rpc", "GET", "/rpc", lambda p, e: results.append(("nested", p, e)))
        for _ in range(200):
            if len(results) == 2:
                break
            self.qt.events(10)
        self.assertEqual(len(results), 2)
        for kind, payload, error in results:
            self.assertIsNotNone(payload, "the refusal body must stay in the payload")
            self.assertEqual(error, "Extrude below minimum temp", kind)

    def test_one_shot_download_streams_a_file_into_the_temp_root(self):
        # Snapshot 2: the file-manager Download lane — one file, one
        # stream, into a fresh temp directory, with the content
        # intact.
        body = b"G1 X0\nG1 X10\n"
        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args):
                pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = harness.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.close)
        service = self.qt.load("RemoteFileService").RemoteFileService(transport)
        self.addCleanup(service.close)
        results = []
        service.download_once("prints/benchy.gcode", on_ready=lambda path, error: results.append((path, error)))
        for _ in range(200):
            if results:
                break
            self.qt.events(10)
        self.assertEqual(len(results), 1)
        path, error = results[0]
        self.assertIsNone(error)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), body)

    def test_moonraker_error_text_covers_every_known_shape(self):
        # A live request: ALL 400-class errors must read
        # like the cold-extrude one — the server's words, one line,
        # never a dict, a code or a whole exception. Pure shapes.
        from mpf.moonraker.MoonrakerTransport import _moonraker_error_text
        traceback = ("Traceback (most recent call last):\n\n"
                     "moonraker.utils.exceptions.ServerError: Move out of range\n"
                     "See the 'position_min' config option for details\n"
                     "tornado.web.HTTPError: HTTP 400: Move out of range\n")
        self.assertEqual(_moonraker_error_text({"message": "Unknown", "traceback": traceback}),
                         "Move out of range")
        self.assertEqual(_moonraker_error_text({"message": "File currently in use"}),
                         "File currently in use")
        self.assertEqual(_moonraker_error_text({"message": "Unknown"}), "")
        self.assertEqual(_moonraker_error_text({}), "")
        # The whole exception in `message` alone: the marker scan
        # still finds the one line that matters.
        message_only = ("Traceback (most recent call last):\n"
                        "ServerError: Extrude below minimum temp\n"
                        "See the 'min_extrude_temp' config option for details\n")
        self.assertEqual(_moonraker_error_text({"message": message_only}),
                         "Extrude below minimum temp")

    def test_stage_switch_echoes_do_not_detach_the_follower(self):
        # Rapid Preview <-> Monitor switching (or merely re-activating
        # Cura's window) recreates the SimulationView and Cura can hang,
        # applying its restoration of the new view seconds later. Neither
        # the swap echo nor the late restore may stick a detach: the view
        # swap re-attaches, the echo window absorbs the restoration, and
        # the watchdog re-attaches any detach that outlives both.
        from PyQt6.QtCore import QObject, pyqtSignal
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True, feed_mode="http"))
        follower.client._handle_http_status({"result": {"status": {
            "print_stats": {"state": "printing", "filename": "part.gcode"},
            "virtual_sdcard": {"file_size": 10, "file_position": 2}}}},
            None, follower.client._generation, harness.time.monotonic())
        self.qt.events()
        app.controller.stage = harness.SimpleNamespace(getId=lambda: "PreviewStage")
        preview = follower._runtime.coordinator._preview
        preview.attach(True)

        def fake_view(layer):
            class FakeView(QObject):
                currentLayerNumChanged = pyqtSignal()
            view = FakeView()
            view.layer = layer
            view.getCurrentLayer = lambda: view.layer
            view.setLayer = lambda value: setattr(view, "layer", value)
            view.getCurrentPath = lambda: 0.0
            view.getMinimumPath = lambda: 0
            view.getActivity = lambda: True
            # The toolpath's own signature (the 2026-09-17 ruling:
            # the view-swap re-attach needs a toolpath).
            view.getMaxLayers = lambda: 100
            return view

        first = fake_view(40)
        app.controller.view = first
        app.controller.activeViewChanged.emit()
        self.qt.events()
        preview.remember()  # armed: the follower expects layer 40
        self.assertEqual(preview.state.expected_layer, 40)

        second = fake_view(0)
        app.controller.view = second
        app.controller.activeViewChanged.emit()
        self.qt.events()
        # The swap re-attaches and re-arms on whatever the new view shows.
        self.assertTrue(preview.state.attached)
        self.assertEqual(preview.state.expected_layer, 0)

        # Cura's late restoration moves the view: with the echo window
        # gone (the ruling — ANY layer intervention detaches),
        # a late restore detaches like a user action. That spurious
        # detach is the accepted cost; the swap itself keeps the
        # attachment.
        self.qt.events(2400)
        second.layer = 10
        second.currentLayerNumChanged.emit()
        self.qt.events()
        self.assertFalse(preview.state.attached)
        # The detach re-baselines on the deviated view (attach(False)
        # remembers) — and NOTHING re-attaches automatically.
        self.assertEqual(preview.state.expected_layer, 10)
        second.layer = 42
        second.currentLayerNumChanged.emit()
        self.qt.events()
        self.assertFalse(preview.state.attached)
        self.qt.events(3100)
        self.assertFalse(preview.state.attached)
        # Only the user (or a view swap while attached) re-attaches.
        preview.attach(True)
        self.assertTrue(preview.state.attached)


