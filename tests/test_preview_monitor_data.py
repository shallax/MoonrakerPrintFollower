"""Executable preview monitor data contracts."""
from tests import preview_family_support as harness

class MonitorConnectionTests(harness.MonitorConnectionTests):
    def test_a_connect_reams_an_invalidated_owned_monitor_and_rearms_a_live_one(self):
        self.activate()
        self.assertEqual(1, self.client.refreshes)
        before = len(self.client.transport.sent)
        # The invalidation suspends the RUNTIME; ownership survives it
        # (the 4.5.0 ownership split), so the reconnect re-arms.
        self.client.connected = False
        self.data._session_invalidated()
        self.assertFalse(self.data.active)
        self.assertTrue(self.data._owner_active)
        self.client.connected = True
        self.client.connectionChanged.emit(True, "reconnected")
        self.assertTrue(self.data.active)
        self.assertEqual(2, self.client.refreshes, "the reconnect re-arms the owned monitor")
        self.assertGreater(len(self.client.transport.sent), before)
        self.client.connectionChanged.emit(True, "reconnected")
        self.assertEqual(3, self.client.refreshes, "a live reconnect re-fires the lanes")

    def test_a_connect_never_arms_a_monitor_without_ownership(self):
        self.client.connected = True
        self.client.connectionChanged.emit(True, "handshake ok")
        self.assertFalse(self.data.active)
        self.assertEqual(("yes", "handshake ok"), (self.data.connection_state,
                                                   self.data.connection_detail))
        self.assertEqual(0, self.client.refreshes)

    def test_a_disconnect_reads_no_once_a_connection_has_been_seen(self):
        self.client.connectionChanged.emit(True, "")
        self.client.connected = False
        self.client.connectionChanged.emit(False, "dropped")
        self.assertEqual("no", self.data.connection_state)
        self.assertEqual("no", self.states[-1])

    def test_a_never_connected_session_reads_unknown(self):
        self.assertEqual("unknown", self.data.connection_state)
        self.assertFalse(self.data.connected)
        self.client.connectionChanged.emit(False, "")
        self.assertEqual("unknown", self.data.connection_state)

    def test_a_bare_connection_event_falls_back_to_the_client_flag(self):
        self.client.connected = True
        self.data.set_owner_active(True)  # owned (the re-arm gate)
        self.data._connection_changed()
        self.assertTrue(self.data.active)
        self.assertEqual("", self.data.connection_detail)

    def test_a_session_invalidation_deactivates_the_monitor(self):
        self.activate()
        self.client.connected = False        # the transport went with the session
        self.client.sessionInvalidated.emit()
        self.assertFalse(self.data.active)
        self.assertEqual("unknown", self.data.connection_state)

    def test_the_watchdog_re_subscribes_a_dead_discovery_chain_once(self):
        self.data.refresh_discovery = harness.Mock()
        self.activate()
        armed = self.data.refresh_discovery.call_count
        live = self.client.resubscribes
        self.data._watch_discovery()
        self.assertEqual(live + 1, self.client.resubscribes)
        self.assertEqual(armed + 1, self.data.refresh_discovery.call_count)
        self.data._update(objects=("extruder",), auxiliary={"extruder": {"temperature": 20}})
        self.data._watch_discovery()
        self.assertEqual(live + 1, self.client.resubscribes, "a live chain is left alone")

    def test_the_watchdog_does_nothing_while_inactive(self):
        self.data._watch_discovery()
        self.assertEqual(0, self.client.resubscribes)

    def test_deactivation_clears_the_session_and_cancels_its_owner(self):
        self.activate()
        self.data.set_console_expanded(True, 5.0)
        self.data._console_entries = [{"text": "x"}]
        self.data.set_owner_active(False)
        self.assertEqual(["monitor"], self.client.transport.cancelled)
        self.assertFalse(self.data._console_expanded)
        self.assertIsNone(self.data._console_seed)
        self.assertEqual([], self.data.console_entries)
        self.assertEqual(1, len(self.invalidations))
        self.assertTrue(self.blocks[-1]["inactive"],
                        "the cleared monitor publishes the absent shape")

    def test_the_poll_intervals_follow_the_session_policy(self):
        from mpf.moonraker.MoonrakerSession import RequestCategory
        self.client.session.forced = {RequestCategory.AUXILIARY: 4321}
        self.activate()
        self.assertEqual(4321, self.data._timers[RequestCategory.AUXILIARY].interval())
        self.assertTrue([call for call in self.client.session.calls
                         if call[0] == RequestCategory.AUXILIARY])

    def test_reconnect_cycles_the_client_and_rearms_an_owned_monitor(self):
        # The manual reconnect re-arms the RUNTIME of an OWNED monitor
        # — a deposed monitor's reconnect is a no-op (the 4.5.0
        # ownership close-out).
        self.data.set_owner_active(True)
        self.data.reconnect()
        self.assertEqual((1, 1), (self.client.stopped, self.client.started))
        self.assertTrue(self.data.active)

    def test_reconnect_is_a_no_op_without_ownership(self):
        self.data.reconnect()
        self.assertEqual((0, 0), (self.client.stopped, self.client.started))
        self.assertFalse(self.data.active)
        self.assertFalse(self.data._owner_active)

    def test_an_emergency_reconnect_needs_an_armed_connected_client(self):
        self.data.reconnect_after_emergency()
        self.assertEqual(0, self.client.stopped, "nothing to cycle while inactive")
        self.activate()
        self.client.connected = False
        self.data.reconnect_after_emergency()
        self.assertEqual(0, self.client.stopped, "nothing to cycle while disconnected")
        self.client.connected = True
        self.data.reconnect_after_emergency()
        self.assertEqual(1, self.client.stopped)
        self.assertTrue(self.data.active)


class MonitorRequestTests(harness.MonitorRequestTests):
    def test_a_request_needs_an_active_monitor_and_a_session_url(self):
        self.assertFalse(self.data.request("x", "GET", "p", lambda payload, error: None))
        self.activate()
        self.client.session.base_url = ""
        self.assertFalse(self.data.request("x", "GET", "p", lambda payload, error: None))
        self.client.session.base_url = "http://printer/"
        self.assertTrue(self.data.request("x", "GET", "p", lambda payload, error: None))

    def test_a_reply_from_a_dead_generation_is_dropped(self):
        answers = []
        self.activate()
        self.data.request("probe", "GET", "p", lambda payload, error: answers.append(payload))
        stale = self.request_to("probe")
        self.data.set_owner_active(False)
        stale.callback({"result": 1}, None)
        self.assertEqual([], answers, "a stale session must not reach the callback")
        self.data.set_owner_active(True)
        self.data.request("probe", "GET", "p", lambda payload, error: answers.append(payload))
        self.request_to("probe").callback({"result": 2}, None)
        self.assertEqual([{"result": 2}], answers)

    def test_a_reply_from_the_live_generation_is_delivered(self):
        answers = []
        self.activate()
        self.data.request("probe", "GET", "p", lambda payload, error: answers.append(payload))
        self.request_to("probe").callback({"result": 1}, None)
        self.assertEqual([{"result": 1}], answers)

    def test_the_rpc_lane_answers_over_the_socket_without_touching_the_wire(self):
        self.activate()
        self.client.rpc_ok = True
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("aux", "POST", "printer/objects/query",
                                          lambda payload, error: None,
                                          rpc=("printer.objects.query", {"objects": {}})))
        self.assertEqual("printer.objects.query", self.client.rpcs[-1][0])
        self.assertEqual(before, len(self.client.transport.sent))

    def test_a_boot_window_rpc_failure_skips_the_wire_but_retries_discovery(self):
        self.activate()
        self.client.effective_feed_mode = "websocket"
        self.data.later = harness.Mock()
        from mpf.moonraker.MoonrakerSession import RequestCategory
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("objects", "GET", "printer/objects/list",
                                          lambda payload, error: None,
                                          category=RequestCategory.DISCOVERY,
                                          rpc=("printer.objects.list", {})))
        self.assertEqual(before, len(self.client.transport.sent), "the wire is skipped")
        # One deferred re-arm timer per boot window; the re-arm
        # re-fires the chain, which defers AGAIN while the lane is
        # down — one more timer, never the wire.
        self.assertEqual(1000, self.data.later.call_args[0][0])
        self.data.later.call_args[0][1]()
        self.assertEqual(before, len(self.client.transport.sent), "still no wire")
        self.assertEqual(2, self.data.later.call_count, "the re-arm re-arms the defer once")
        # With the lane up, the NEXT re-arm dispatches through the RPC
        # lane — the gate reopened rather than wedging shut.
        self.client.rpc_ok = True
        self.data.later.call_args[0][1]()
        self.assertGreaterEqual(len([method for method, _params, _cb in self.client.rpcs
                                     if method == "printer.objects.list"]), 1)

    def test_a_boot_window_rpc_failure_without_discovery_just_waits(self):
        self.activate()
        self.client.effective_feed_mode = "websocket"
        self.data.later = harness.Mock()
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("aux", "POST", "printer/objects/query",
                                          lambda payload, error: None,
                                          rpc=("printer.objects.query", {})))
        self.assertEqual(before, len(self.client.transport.sent))
        self.assertEqual(0, self.data.later.call_count)

    def test_an_http_session_falls_through_to_the_wire(self):
        self.activate()
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("aux", "POST", "printer/objects/query",
                                          lambda payload, error: None,
                                          rpc=("printer.objects.query", {})))
        self.assertEqual(before + 1, len(self.client.transport.sent))

    def test_a_deferred_callback_is_bound_to_its_generation(self):
        calls = []
        self.activate()
        self.data.later(0, lambda: calls.append("fired"))
        self.pump()
        self.assertEqual(["fired"], calls)
        self.data.set_owner_active(False)
        self.data.later(0, lambda: calls.append("stale"))
        self.pump()
        self.assertEqual(["fired"], calls)


class MonitorProjectionTests(harness.MonitorProjectionTests):
    def test_the_observation_record_assembles_from_both_lanes(self):
        self.activate()
        self.client.assumed_stopped = True
        self.data._update(core={"print_stats": {"state": "printing"},
                                "pause_resume": {"is_paused": True}},
                          auxiliary={"configfile": {"save_config_pending": True},
                                     "toolhead": {"homed_axes": "xyz"}},
                          objects=("pause_resume", "extruder"))
        observed = self.data.observation
        self.assertEqual(("printing", "xyz", True, True), (observed.state, observed.homed_axes,
                                                           observed.assumed_stopped,
                                                           observed.save_config_pending))
        self.assertTrue(observed.is_paused)
        self.assertTrue(observed.pause_resume_supported)
        self.assertEqual("yes", observed.connection)
        self.assertTrue(observed.active)

    def test_an_unobserved_object_list_reads_as_no_capability_signal(self):
        self.data._update(objects=(), auxiliary={"toolhead": {}})
        self.assertIsNone(self.data.observation.pause_resume_supported)
        self.assertIsNone(self.data.observation.is_paused)

    def test_an_observed_list_without_the_module_fails_the_row_closed(self):
        self.data._update(objects=("stepper_enable",))
        self.assertFalse(self.data.observation.pause_resume_supported)

    def test_the_chrome_and_lane_pushes_update_without_a_stale_rebuild(self):
        self.activate()          # a deferred push is dropped while inactive
        self.pump()
        self.changes.clear()
        self.data.set_controls_locked(True)
        self.assertTrue(self.data.observation.controls_locked)
        self.pump()
        self.assertEqual(1, len(self.changes), "the locked push publishes once")
        self.data.set_controls_locked(True)
        self.pump()
        self.assertEqual(1, len(self.changes), "an unchanged push is a no-op")
        self.data.set_commands_busy(True)
        self.assertTrue(self.data.observation.busy)
        self.assertEqual(1, len(self.changes), "the busy push never re-publishes")
        self.data.set_commands_busy(False)
        self.assertFalse(self.data.observation.busy)

    def test_freeze_deep_freezes_a_nested_payload(self):
        frozen = harness.freeze({"a": [1, {"b": 2}], "c": (3,), "d": "s"})
        self.assertEqual({"a": (1, {"b": 2}), "c": (3,), "d": "s"}, dict(frozen))
        with self.assertRaises(TypeError):
            frozen["a"] = ()
        with self.assertRaises(TypeError):
            frozen["a"][1]["b"] = 9

    def test_the_client_passthroughs_reach_the_client(self):
        self.data.track_command("Resume", ("paused",), timeout_s=0.5)
        self.data.accept_command("Resume")
        self.data.fail_command("Resume", "no")
        self.data.force_refresh()
        self.data.assume_print_stopped()
        self.data.set_toolhead_guard(True)
        self.assertEqual(("Resume", ("paused",), 0.5), self.client.tracked[-1])
        self.assertEqual(["Resume"], self.client.accepted)
        self.assertEqual([("Resume", "no")], self.client.failed)
        self.assertEqual(1, self.client.refreshes)
        self.assertEqual(1, self.client.assumed_stops)
        self.assertEqual([True], self.client.guards)

    def test_the_read_only_properties_project_the_snapshot(self):
        self.assertEqual(self.client.status, self.data.status)
        self.assertTrue(self.data.wants_object("extruder"))
        self.assertFalse(self.data.wants_object("print_stats"))
        self.assertIs(self.data.snapshot, self.data._snapshot)
        self.assertFalse(self.data.active)

    def test_observe_filters_non_mappings_and_ignores_a_dead_monitor(self):
        self.data.observe({"print_stats": {"state": "printing"}, "junk": 5})
        self.assertEqual({}, dict(self.data.snapshot.core), "an inactive monitor stores nothing")
        self.data.observe("not a mapping")
        self.activate()
        self.data.observe({"print_stats": {"state": "printing"}, "junk": 5})
        self.assertEqual({"print_stats": {"state": "printing"}}, dict(self.data.snapshot.core))


class MonitorAuxTests(harness.MonitorAuxTests):
    def test_the_aux_lane_heals_the_discovery_chain_before_objects_arrive(self):
        self.data.refresh_discovery = harness.Mock()
        self.activate()
        armed = self.data.refresh_discovery.call_count
        self.data.refresh_aux()
        self.assertEqual(armed + 1, self.data.refresh_discovery.call_count)

    def test_the_socket_is_a_source_and_its_fragments_merge(self):
        self.activate()
        self.data._update(objects=("extruder",))
        self.client.effective_feed_mode = "websocket"
        self.client.aux_patch = {"extruder": {"temperature": 200.0}}
        self.data.refresh_aux()
        self.assertEqual(1, self.client.drains)
        self.assertEqual(200.0, self.data.snapshot.auxiliary["extruder"]["temperature"])

    def test_an_empty_socket_drain_re_issues_the_subscription(self):
        self.activate()
        self.data._update(objects=("extruder", "stepper_enable"))
        self.client.effective_feed_mode = "websocket"
        self.data.refresh_aux()
        self.assertEqual({"extruder"}, self.client.aux_sets[-1])

    def test_an_http_aux_poll_queries_only_the_wanted_objects(self):
        self.activate()
        self.data._update(objects=("extruder", "stepper_enable"))
        self.data.refresh_aux()
        item = self.request_to("aux")
        self.assertEqual(["extruder"], list(item.body["objects"]))
        self.assertIsNone(item.body["objects"]["extruder"])

    def test_an_http_aux_poll_with_nothing_wanted_sends_nothing(self):
        self.activate()
        self.data._update(objects=("stepper_enable",))
        before = len(self.client.transport.sent)
        self.data.refresh_aux()
        self.assertEqual(before, len(self.client.transport.sent))

    def test_the_configfile_aux_query_asks_for_the_pending_flags(self):
        self.activate()
        self.data._update(objects=("configfile",))
        self.data.refresh_aux()
        self.assertEqual(["save_config_pending", "save_config_pending_items"],
                         self.request_to("aux").body["objects"]["configfile"])

    def test_a_reconcile_only_rides_a_socket_session(self):
        self.activate()
        self.data._update(objects=("extruder",))
        self.data._reconcile_aux_subscription()
        self.assertEqual([], self.client.aux_sets)
        self.client.effective_feed_mode = "websocket"
        self.data._reconcile_aux_subscription()
        self.assertEqual([{"extruder"}], self.client.aux_sets)

    def test_the_aux_reply_merges_into_the_wanted_set_and_rebuilds(self):
        self.activate()
        self.data._update(objects=("extruder", "heater_bed"))
        self.data._merge_aux({"extruder": {"temperature": 200.0}})
        self.data._merge_aux({"extruder": {"target": 210.0}, "ghost": {"x": 1}})
        self.assertEqual({"temperature": 200.0, "target": 210.0},
                         dict(self.data.snapshot.auxiliary["extruder"]))
        self.assertNotIn("ghost", self.data.snapshot.auxiliary)
        self.assertGreaterEqual(len(self.aux_changes), 2)
        self.assertIsInstance(self.blocks[-1], dict)

    def test_a_newly_seen_device_joins_the_subscription(self):
        self.activate()
        self.data._update(objects=())
        self.data._merge_aux({"extruder": {"temperature": 20.0}})
        self.assertEqual({"extruder"}, self.client.aux_sets[-1])
        self.assertIn("extruder", self.data.snapshot.auxiliary)

    def test_a_non_mapping_aux_reply_is_dropped(self):
        self.activate()
        before = dict(self.data.snapshot.auxiliary)
        self.data._aux("nonsense", None)
        self.data._aux({"result": {"status": {"extruder": {"temperature": 1.0}}}}, "error")
        self.assertEqual(before, dict(self.data.snapshot.auxiliary))
        self.data._aux({"result": {"status": {"extruder": {"temperature": 1.0}}}}, None)
        self.assertEqual({"temperature": 1.0}, dict(self.data.snapshot.auxiliary["extruder"]))

    def test_the_discovery_chain_feeds_objects_presets_and_webcams(self):
        self.activate()
        # The activate-time refresh_all already issued the webcam RPC;
        # the chain's own refresh coalesces onto it (the in-flight
        # gate — one webcam request per cycle) and feeds objects and
        # presets itself.
        before = len(self.client.transport.sent)
        self.data.refresh_discovery()
        fresh = self.client.transport.sent[before:]
        self.assertEqual(2, len(fresh))
        self.assertEqual({"objects", "presets"}, {item.channel for item in fresh})
        self.request_to("webcams").callback({"result": {"webcams": [{"name": "cam", "enabled": True}]}}, None)
        self.assertEqual(({"name": "cam", "enabled": True},), self.data.snapshot.webcams)
        # The landed reply reopens the gate: a fresh call issues again.
        self.data.refresh_webcams()
        self.assertEqual(2, len([item for item in self.client.transport.sent if item.channel == "webcams"]))
        self.request_to("objects").callback({"result": {"objects": ["configfile", "extruder"]}}, None)
        self.assertEqual(("configfile", "extruder"), self.data.snapshot.objects)
        self.assertTrue([item for item in self.client.transport.sent
                         if item.channel == "config-static"])
        self.request_to("presets").callback({"result": {"value": {"pla": {"temp": 200}}}}, None)
        self.assertEqual({"pla": {"temp": 200}}, dict(self.data.snapshot.presets))
        self.request_to("presets").callback({"result": {}}, "error")
        self.assertEqual({"pla": {"temp": 200}}, dict(self.data.snapshot.presets))

    def test_a_broken_or_empty_objects_reply_leaves_the_lists_alone(self):
        self.activate()
        self.data._objects({"result": {"objects": "extruder"}}, None)
        self.assertEqual((), self.data.snapshot.objects)
        self.data._objects({"result": {"objects": ["extruder"]}}, "error")
        self.assertEqual((), self.data.snapshot.objects, "a failed poll never erases the list")


class MonitorLaneTests(harness.MonitorLaneTests):
    def test_the_endstop_poll_waits_for_a_ready_klippy_and_an_idle_printer(self):
        self.activate()
        self.data._update(server={"klippy_state": "shutdown"})
        before = len(self.client.transport.sent)
        self.data.refresh_endstops()
        self.assertEqual(before, len(self.client.transport.sent), "klippy is not ready")
        self.data._update(server={"klippy_state": "ready"},
                          core={"print_stats": {"state": "printing"}})
        self.data.refresh_endstops()
        self.assertEqual(before, len(self.client.transport.sent), "the toolhead must not dwell")
        self.data._update(core={"print_stats": {"state": "standby"}})
        self.data.refresh_endstops()
        item = self.request_to("endstops")
        item.callback({"result": {"x": "open"}}, None)
        self.assertEqual({"x": "open"}, dict(self.data.snapshot.endstops))

    def test_a_failed_endstop_poll_keeps_the_last_known_states(self):
        self.activate()
        self.data._update(server={"klippy_state": "ready"},
                          core={"print_stats": {"state": "standby"}},
                          endstops={"x": "TRIGGERED"})
        self.data.refresh_endstops()
        self.request_to("endstops").callback({"result": {}}, "timeout")
        self.assertEqual({"x": "TRIGGERED"}, dict(self.data.snapshot.endstops))

    def test_power_system_and_webcam_replies_project_into_the_snapshot(self):
        self.activate()
        self.data.refresh_power()
        self.request_to("power-list").callback({"result": {"devices": [{"device": "psu"}]}}, None)
        self.assertEqual(({"device": "psu"},), self.data.snapshot.power)
        self.data.refresh_system()
        self.request_to("server-info").callback({"result": {"klippy_state": "ready"}}, None)
        self.request_to("printer-info").callback({"result": {"state": "ready"}}, None)
        self.assertEqual({"klippy_state": "ready"}, dict(self.data.snapshot.server))
        self.assertEqual({"state": "ready"}, dict(self.data.snapshot.printer))
        self.data.refresh_webcams()
        self.request_to("webcams").callback({"result": {"webcams": [
            {"name": "cam", "enabled": True}, {"name": "off", "enabled": False}, "junk"]}}, None)
        self.assertEqual(({"name": "cam", "enabled": True},), self.data.snapshot.webcams)

    def test_a_failed_power_or_webcam_poll_keeps_the_last_known_values(self):
        self.activate()
        self.data._update(power=({"device": "psu"},), webcams=({"name": "cam"},))
        self.data.refresh_power()
        self.request_to("power-list").callback({"result": {}}, "timeout")
        self.assertEqual(({"device": "psu"},), self.data.snapshot.power)
        self.data.refresh_webcams()
        self.request_to("webcams").callback({"result": {}}, "timeout")
        self.assertEqual(({"name": "cam"},), self.data.snapshot.webcams)

    def test_refresh_all_is_a_no_op_while_inactive(self):
        self.client.force_refresh = harness.Mock()
        self.data.refresh_all()
        self.assertEqual(0, self.client.force_refresh.call_count)
        self.activate()
        before = self.client.force_refresh.call_count
        self.data.refresh_all()
        self.assertEqual(before + 1, self.client.force_refresh.call_count)


class MonitorConsoleTests(harness.MonitorConsoleTests):
    def test_an_expand_seeds_the_skip_and_polls_immediately(self):
        self.activate()
        self.data.set_console_expanded(True, 10.0)
        self.assertTrue(self.data._console_expanded)
        self.assertEqual(10.0, self.data._console_seed)
        self.assertFalse(self.data._console_watch.isActive())
        self.request_to("console-store")

    def test_the_first_fetch_skips_the_seeded_history_permanently(self):
        self.activate()
        self.data.set_console_expanded(True, 10.0)
        item = self.request_to("console-store")
        item.callback(self.store([
            {"type": "response", "time": 5.0, "message": "stale"},
            {"type": "response", "time": 20.0, "message": "ok"},
            {"type": "response", "time": 20.0, "message": "ok"},
            {"type": "command", "time": 21.0, "message": "G28"},
            {"type": "response", "time": 22.0, "message": ""},
            {"type": "response", "time": 23.0, "message": "!! fail"},
            {"type": "response", "time": 24.0, "message": "// echo"},
        ]), None)
        entries = self.data.console_entries
        self.assertEqual(["ok", "!! fail", "// echo"], [entry["text"] for entry in entries])
        self.assertEqual([False, True, False], [entry["error"] for entry in entries])
        self.assertEqual([True, False, False], [entry["success"] for entry in entries])
        self.assertEqual(1, len(self.console_changes))
        # The skipped history stays skipped on the next poll.
        self.data.refresh_console_store()
        self.request_to("console-store").callback(self.store(
            [{"type": "response", "time": 5.0, "message": "stale"},
             {"type": "response", "time": 25.0, "message": "fresh"}]), None)
        self.assertEqual(["fresh"], [entry["text"] for entry in self.data.console_entries])

    def test_a_no_op_resume_verdict_settles_the_tracked_command(self):
        self.activate()
        self.data.set_console_expanded(True, 0.0)
        self.request_to("console-store").callback(self.store(
            [{"type": "response", "time": 5.0, "message": "Resume aborted: no paused print"}]), None)
        self.assertEqual([("Resume", "Nothing to resume")], self.client.settled)

    def test_an_empty_or_broken_store_reply_publishes_nothing(self):
        self.activate()
        self.data.set_console_expanded(True, 0.0)
        self.request_to("console-store").callback(self.store([]), None)
        self.assertEqual([], self.data.console_entries)
        self.request_to("console-store").callback("nonsense", None)
        self.request_to("console-store").callback({"result": {"gcode_store": "nope"}}, None)
        self.request_to("console-store").callback(self.store([{"type": "response"}]), "error")
        self.assertEqual(0, len(self.console_changes))

    def test_a_collapsed_console_polls_slowly_and_only_while_active(self):
        self.data.set_console_expanded(False)
        self.assertTrue(self.data._console_watch.isActive(), "the bell's feed arms on collapse")
        self.data.refresh_console_store = harness.Mock()
        self.data._refresh_console_watch()
        self.assertEqual(0, self.data.refresh_console_store.call_count, "inactive: no poll")
        self.activate()
        self.data._refresh_console_watch()
        self.assertEqual(1, self.data.refresh_console_store.call_count)
        self.data.set_console_expanded(True, 0.0)     # the expand polls once itself
        expanded = self.data.refresh_console_store.call_count
        self.data._refresh_console_watch()
        self.assertEqual(expanded, self.data.refresh_console_store.call_count,
                         "expanded: the fast poll owns it")

    def test_a_collapsed_store_poll_is_forced_past_the_expansion_gate(self):
        self.activate()
        before = len(self.client.transport.sent)
        self.data.refresh_console_store()
        self.assertEqual(before, len(self.client.transport.sent))
        self.data.refresh_console_store(force=True)
        self.request_to("console-store")

    def test_a_re_expand_re_seeds_from_the_persisted_stamp(self):
        self.activate()
        self.data.set_console_expanded(True, 10.0)
        self.data.set_console_expanded(False)
        self.assertTrue(self.data._console_watch.isActive())
        self.data.set_console_expanded(True, 50.0)
        self.assertEqual(50.0, self.data._console_seed)


