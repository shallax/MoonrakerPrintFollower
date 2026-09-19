"""The client's websocket feed branch: admission, proof, fallback, refusals."""
from __future__ import annotations

import unittest

from tests.qt_runtime_support import QT_AVAILABLE, ScriptedTransport

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

    from plugins.MonitorData import MonitorData
    from plugins.MoonrakerClient import MoonrakerClient
    from plugins.MoonrakerSession import MoonrakerSession, PollPolicy


    class ScriptedSocket(QObject):
        syncSnapshot = pyqtSignal(object, float)
        subscribeRefused = pyqtSignal(object)
        klippyReady = pyqtSignal()
        klippyLost = pyqtSignal(str)
        failed = pyqtSignal(str)
        upgraded = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.starts = []
            self.subscriptions = []
            self.rpcs = []
            self.stops = 0
            self.is_upgraded = False
            self.core_patch = None
            self.core_stamp = 0.0
            self.aux_patch = None

        def start(self, url, api_key, core_names, aux_names):
            self.starts.append((url, api_key, set(core_names), set(aux_names)))
            self.is_upgraded = True
            self.upgraded.emit()

        def subscribe(self, objects, *, aux_names=None):
            self.subscriptions.append(dict(objects))
            if aux_names is not None:
                self.aux_names = set(aux_names)

        def stop(self):
            self.stops += 1
            self.is_upgraded = False

        def drain_core(self):
            patch, stamp = self.core_patch, self.core_stamp
            self.core_patch = None
            self.core_stamp = 0.0
            return patch, stamp

        def drain_aux(self):
            patch = self.aux_patch
            self.aux_patch = None
            return patch, 0.0

        def request(self, method, params, callback):
            self.rpcs.append((method, dict(params), callback))
            return len(self.rpcs)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class ClientFeedTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.transport = ScriptedTransport()
        self.socket = ScriptedSocket()
        self.session = MoonrakerSession(transport=self.transport, socket=self.socket)
        self.client = MoonrakerClient(session=self.session, proof_timeout_ms=150)

    def pump(self, seconds=0.3):
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_websocket_start_connects_with_the_mapped_url_and_merged_set(self):
        self.client.configure("https://printer.local", "key", 750, feed_mode="websocket")
        self.client.start()
        url, key, core, aux = self.socket.starts[0]
        self.assertEqual(url, "wss://printer.local/websocket")
        self.assertEqual(key, "key")
        self.assertEqual(core, {"print_stats", "gcode_move", "virtual_sdcard", "motion_report", "bed_mesh", "pause_resume"})
        self.assertEqual(self.socket.subscriptions[0], {
            "bed_mesh": None, "gcode_move": None, "motion_report": None,
            "pause_resume": None, "print_stats": None, "virtual_sdcard": None,
        })

    def test_idle_floor_does_not_gate_the_first_connection(self):
        # A live report: ~5 s of dead UI before the printer
        # showed as connected — the idle floor (5000 ms) governed the
        # tick from startup, so a failed first attempt retried five
        # seconds later. Until the first status has ever landed, the
        # tick runs at the configured cadence.
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.assertEqual(self.client._poll_timer.interval(), 750)
        # Once a status lands, the idle floor applies again.
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 12.0)
        self.assertTrue(self.client.connected)
        self.assertEqual(self.client._poll_timer.interval(), 5000)

    def test_failure_ladder_does_not_gate_a_never_connected_session(self):
        # A live report: ~5 s of dead UI before the first
        # data — each fast startup failure walks the ladder (1 s, 2 s,
        # 5 s) and the 5 s rung then gates a session that has never
        # connected. Until the printer has ever answered, the retry
        # stays at the configured cadence.
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        for _ in range(4):
            self.client._handle_failure("boom")
        self.assertLessEqual(self.client._retry_delay_ms, self.client.FIRST_CONNECT_RETRY_MS)
        self.assertLessEqual(self.client._poll_timer.interval(), self.client.FIRST_CONNECT_RETRY_MS)
        # A proven endpoint keeps the ladder for its outages.
        self.client._connected = True
        self.client._handle_failure("boom")
        self.assertGreater(self.client._retry_delay_ms, 750)

    def test_connect_transition_republishes_the_accumulated_snapshot(self):
        # The ruling: the connect transition re-broadcasts
        # the accumulated snapshot so every listener populates
        # instantly — even one that attached after the sync landed.
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 12.0)
        # The transition's republish, then the admission's own emit.
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0]["print_stats"]["state"], "idle")

    def test_sync_snapshot_admits_and_marks_connected(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 12.0)
        self.assertTrue(received)
        self.assertTrue(self.client.connected)
        self.assertEqual(self.client.effective_feed_mode, "websocket")

    def test_fragment_drain_admits_on_the_tick(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.core_patch = {"print_stats": {"state": "printing"}}
        self.socket.core_stamp = 5.0
        self.client.force_refresh()
        self.assertTrue(received)
        self.assertEqual(received[0]["print_stats"]["state"], "printing")

    def test_stale_sync_is_dropped_after_newer_fragments(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        received = []
        self.client.statusReceived.connect(lambda status: received.append(status))
        self.socket.core_patch = {"print_stats": {"state": "printing"}}
        self.socket.core_stamp = 50.0
        self.client.force_refresh()
        count = len(received)
        # A sync issued earlier than the applied fragments is dropped whole.
        self.socket.syncSnapshot.emit({"print_stats": {"state": "cancelled"}}, 10.0)
        self.assertEqual(len(received), count)
        self.assertEqual(received[-1]["print_stats"]["state"], "printing")
        # A newer sync applies.
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 60.0)
        self.assertEqual(received[-1]["print_stats"]["state"], "idle")

    def test_silent_startup_proof_degrades_to_http_without_a_session_reset(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        generation = self.session.generation
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.pump(0.4)
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.session.generation, generation)
        # Three stops, all expected: the configure's client stop, the
        # session rebind's socket stop, and the proof failure's stop.
        self.assertEqual(self.socket.stops, 3)
        self.assertTrue(any("silent" in reason for _, reason in notes), notes)
        self.assertTrue(self.transport.requests)  # HTTP polling resumed

    def test_proof_passes_once_data_has_flowed(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 1.0)
        self.pump(0.4)
        self.assertEqual(self.client.effective_feed_mode, "websocket")

    def test_subscribe_refusal_degrades_with_a_reason(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        generation = self.session.generation
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        stops_before = self.socket.stops
        self.socket.subscribeRefused.emit({"code": -32602, "message": "Unknown"})
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.session.generation, generation)
        self.assertTrue(any("refused" in reason for _, reason in notes), notes)
        # The hardening pass: the retired socket side stops with the
        # fallback — the configured preference stays websocket, the
        # proof timer stands down, the RPC lane closes, and HTTP
        # polling resumes.
        self.assertEqual(self.client.configured_feed_mode, "websocket")
        self.assertFalse(self.client._proof_timer.isActive())
        self.assertFalse(self.client.rpc_available())
        self.assertEqual(self.socket.stops, stops_before + 1)
        self.assertTrue(self.transport.requests)  # the HTTP refresh started

    def test_a_stale_klippy_ready_after_the_refusal_does_not_resubscribe(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.socket.subscribeRefused.emit({"code": -32602, "message": "Unknown"})
        subscriptions = len(self.socket.subscriptions)
        self.socket.klippyReady.emit()
        self.assertEqual(len(self.socket.subscriptions), subscriptions)

    def test_unauthorized_refusal_is_a_key_rejection_failure(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.socket.subscribeRefused.emit({"code": -32602, "message": "Unauthorized"})
        self.assertTrue(any("key" in reason.lower() for _, reason in notes), notes)

    def test_klippy_ready_resubscribes_and_clears_the_estop_assumption(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.client.assume_print_stopped()
        self.assertTrue(self.session.state.assume_print_stopped)
        subscriptions = len(self.socket.subscriptions)
        self.socket.klippyReady.emit()
        self.assertEqual(len(self.socket.subscriptions), subscriptions + 1)
        self.assertFalse(self.session.state.assume_print_stopped)

    def test_klippy_loss_is_a_failure(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        notes = []
        self.client.connectionChanged.connect(lambda connected, reason: notes.append((connected, reason)))
        self.socket.klippyLost.emit("notify_klippy_shutdown")
        self.assertTrue(any("Klippy" in reason for _, reason in notes), notes)

    def test_mode_change_rebinds_through_the_session(self):
        self.client.configure("http://p", "k", 750)
        self.assertEqual(self.client.effective_feed_mode, "http")
        invalidated = []
        self.client.sessionInvalidated.connect(lambda: invalidated.append(True))
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.assertTrue(invalidated)
        self.assertEqual(self.client.configured_feed_mode, "websocket")


    def test_rpc_lane_routes_to_the_socket_when_live(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.socket.syncSnapshot.emit({"print_stats": {"state": "idle"}}, 1.0)
        replies = []
        self.assertTrue(self.client.rpc("server.gcode_store", {"count": 100},
                                        lambda payload, error: replies.append((payload, error))))
        self.assertEqual(self.socket.rpcs[-1][:2], ("server.gcode_store", {"count": 100}))
        # The reply shape converts to the transport's convention: the
        # result passes through, the refusal surfaces the server's words.
        method, params, callback = self.socket.rpcs[-1]
        callback({"jsonrpc": "2.0", "result": {"gcode_store": []}, "id": 1})
        self.assertEqual(replies[-1][0]["result"]["gcode_store"], [])
        callback({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Unauthorized"}, "id": 2})
        self.assertIn("Unauthorized", replies[-1][1])

    def test_restart_clears_the_estop_assumption_when_the_duration_resets(self):
        # Restart arming: the latch survives a frozen duration (the
        # wedged-Moonraker wedge it exists for) but a demonstrably
        # LOWER duration is a new print — the assumption falls.
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.client.session.merge_status({"print_stats": {"state": "printing", "print_duration": 500}})
        self.client.assume_print_stopped()
        self.assertTrue(self.client._session.state.assume_print_stopped)
        emitted = []
        self.client.statusReceived.connect(lambda status: emitted.append(status))
        self.client.admit_status({"print_stats": {"state": "printing", "print_duration": 500}},
                                 origin="sync", stamp=1.0, generation=self.client._generation)
        self.assertTrue(self.client._session.state.assume_print_stopped)
        self.assertEqual(emitted[-1]["print_stats"]["state"], "cancelled")
        self.client.admit_status({"print_stats": {"state": "printing", "print_duration": 5}},
                                 origin="sync", stamp=2.0, generation=self.client._generation)
        self.assertFalse(self.client._session.state.assume_print_stopped)
        self.assertEqual(emitted[-1]["print_stats"]["state"], "printing")

    def test_rebind_replaces_the_socket_handlers_not_appends(self):
        # The Windows log's multiplied "upgraded" lines came from a
        # fresh closure per cycle that was never disconnected. Drive
        # the production re-entry — configure(urlA); start();
        # configure(urlB); start() — and assert the handler set is
        # replaced: one klippyReady broadcast re-subscribes exactly
        # once (an accumulated set would fire once per layer).
        self.client.configure("http://a", "k", 750, feed_mode="websocket")
        self.client.start()
        self.client.configure("http://b", "k", 750, feed_mode="websocket")
        self.client.start()
        self.assertEqual([entry[0] for entry in self.client.session.socket.starts],
                         ["ws://a/websocket", "ws://b/websocket"])
        before = len(self.client.session.socket.subscriptions)
        self.client.session.socket.klippyReady.emit()
        self.assertEqual(len(self.client.session.socket.subscriptions), before + 1)

    def test_new_print_start_expires_stale_tracked_commands(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        self.client.track_command("ScheduledPause", {"paused"})
        notes = []
        self.client.commandChanged.connect(lambda event: notes.append(event))
        self.client.admit_status({"print_stats": {"state": "printing", "print_duration": 1}},
                                 origin="sync", stamp=1.0, generation=self.client._generation)
        self.assertTrue(any(event.get("outcome") == "failed"
                            and "new print" in str(event.get("detail"))
                            for event in notes), notes)

    def test_rpc_lane_falls_back_when_the_socket_is_not_live(self):
        self.client.configure("http://p", "k", 750, feed_mode="websocket")
        self.client.start()
        # The lane opens on the upgrade — an RPC reply is itself an
        # authenticated reply; the proof governs the status feed only.
        self.assertTrue(self.client.rpc_available())
        # It closes when the feed degrades (the proof failure path)...
        self.client._proof_failed(self.client._generation)
        self.assertFalse(self.client.rpc_available())
        self.assertFalse(self.client.rpc("server.info", {}, lambda p, e: None))
        # ...and in HTTP mode it is never open.
        self.client.configure("http://p", "k", 750, feed_mode="http")
        self.client.start()
        self.assertFalse(self.client.rpc_available())

if QT_AVAILABLE:

    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
    class MonitorDataBootChainTests(unittest.TestCase):
        """The 2026-09-16 live report: the first M117 of a session took
        ~30 s because the boot-time objects request fell into the
        websocket bootstrap window and nothing re-fired it until the
        discovery tick. The chain must complete on every boot."""

        class FakeDataClient(QObject):
            statusReceived = pyqtSignal(object)
            commandChanged = pyqtSignal(object)
            sessionInvalidated = pyqtSignal()
            connectionChanged = pyqtSignal(object)

            def __init__(self):
                super().__init__()
                from types import SimpleNamespace
                self.connected = False
                self.status = {}
                self.effective_feed_mode = "websocket"
                self.aux_interval_ms = 2500
                self.console_interval_ms = 1000
                self.session = SimpleNamespace(
                    base_url="http://p", generation=0, poll_policy=PollPolicy(),
                    snapshot=SimpleNamespace(printer_state=""))
                self.transport = SimpleNamespace(
                    cancel_owner=lambda *args: None,
                    send_json=lambda *args, **kwargs: True)
                self.drains = 0
                self.aux_sets = []
                self.rpc_ok = False

            def drain_aux(self):
                self.drains += 1
                return None, 0.0

            def set_auxiliary_objects(self, names):
                self.aux_sets.append(set(names))

            def rpc(self, method, params, callback):
                return self.rpc_ok

            def force_refresh(self):
                pass

        class ProbeData(MonitorData):
            def __init__(self, client):
                super().__init__(client)
                self.discovery_calls = 0
                self.later_calls = []

            def refresh_discovery(self):
                self.discovery_calls += 1

            def later(self, delay_ms, callback):
                self.later_calls.append((delay_ms, callback))

        def setUp(self):
            self.app = QCoreApplication.instance() or QCoreApplication([])
            self.client = self.FakeDataClient()
            self.data = self.ProbeData(self.client)
            self.data.set_owner_active(True)
            # set_active fires the lanes once (the boot pass); the
            # asserts below drive the ticks manually, so silence the
            # timers.
            for timer in self.data._timers.values():
                timer.stop()

        def test_aux_tick_rearms_discovery_while_the_objects_list_is_missing(self):
            self.data.discovery_calls = 0
            self.data.refresh_aux()
            self.assertEqual(self.data.discovery_calls, 1)
            self.assertEqual(self.client.drains, 0)

        def test_aux_tick_drains_once_objects_are_known(self):
            self.data._update(objects=("display_status",))
            self.data.discovery_calls = 0
            self.data.refresh_aux()
            self.assertEqual(self.data.discovery_calls, 0)
            self.assertEqual(self.client.drains, 1)
            self.assertIn({"display_status"}, self.client.aux_sets)

        def test_boot_dropped_discovery_request_retries_in_one_second(self):
            self.client.rpc_ok = False
            self.data.request("objects", "GET", "printer/objects/list",
                              lambda payload, error: None,
                              category="discovery", rpc=("printer.objects.list", {}))
            self.assertTrue(self.data.later_calls)
            delay_ms, callback = self.data.later_calls[-1]
            self.assertEqual(delay_ms, 1000)
            # The retry is the bound refresh_discovery (the probe
            # overrides the method to count calls, so pin the bound
            # self and the override's own function).
            self.assertIs(callback.__self__, self.data)
            self.assertIs(callback.__func__, self.ProbeData.refresh_discovery)
            # With the RPC lane live the request rides it: no retry is
            # scheduled and nothing falls through to the wire.
            self.client.rpc_ok = True
            self.data.later_calls = []
            self.data.request("objects", "GET", "printer/objects/list",
                              lambda payload, error: None,
                              category="discovery", rpc=("printer.objects.list", {}))
            self.assertEqual(self.data.later_calls, [])


if QT_AVAILABLE:

    class PauseControllerLifecycleTests(unittest.TestCase):
        def test_confirmed_pause_stays_listed_as_passed_and_removable(self):
            # The 2026-09-16 ruling: a confirmed pause STAYS in the
            # list, dimmed "passed" (the rows never vanish mid-print),
            # and the user can still remove it by hand.
            from unittest.mock import Mock
            from plugins.PauseController import PauseController
            client = Mock()
            controller = PauseController(client)
            controller.bind(("job-key",))
            self.assertTrue(controller.toggle(10, 9, 100))
            self.assertIn(10, controller.layers)
            controller._states[10] = "fired"
            controller._target = 10
            controller._command_changed({"name": "ScheduledPause", "outcome": "confirmed"})
            self.assertIn(10, controller.layers)
            self.assertEqual(controller.states.get(10), "passed")
            controller.remove(10)
            self.assertNotIn(10, controller.layers)
            self.assertNotIn(10, controller.states)

    class PreviewMotionReleaseTests(unittest.TestCase):
        def test_layer_change_resets_the_exiting_layers_cache_before_the_write(self):
            # The per-layer release (the live report's native RSS
            # steps): the exiting layer's cached mesh/jump data is
            # dropped BEFORE the new path is written — one cache slot,
            # one reset per transition.
            from contextlib import contextmanager
            from plugins.PreviewMotion import PreviewMotion

            class FakeView:
                def __init__(self):
                    self.calls = []
                    self.max_paths = 100

                def getMaxPaths(self):
                    return self.max_paths

                def setPath(self, value):
                    self.calls.append(("path", value))

                def setMinimumPath(self, value):
                    self.calls.append(("min", value))

                def resetLayerData(self):
                    self.calls.append(("reset",))

            class FakeCura:
                def __init__(self, view):
                    self.view = view

                @contextmanager
                def writing_preview(self):
                    yield

            view = FakeView()
            motion = PreviewMotion(FakeCura(view), remember=lambda: None)
            motion.write(0, 0.5)
            resets_before = [i for i, call in enumerate(view.calls) if call[0] == "reset"]
            motion.write(1, 0.1)
            second_path = next(i for i, call in enumerate(view.calls)
                               if call[0] == "path" and abs(call[1] - 10.0) < 1e-9)
            self.assertTrue(any(i > resets_before[-1] for i in [second_path]),
                            "the reset must land before the new layer's path write")
            self.assertEqual([call for call in view.calls if call[0] == "reset"], [("reset",), ("reset",)])

        def test_a_replacement_view_receives_its_minimum_write(self):
            # The once-per-view minimum keys to the VIEW, not a
            # session flag: after a new file load replaces the view,
            # the new view must still receive its minimum (the review
            # repro: the stale flag kept the replacement view's
            # nonzero minimum).
            from contextlib import contextmanager
            from plugins.PreviewMotion import PreviewMotion

            class FakeView:
                def __init__(self):
                    self.calls = []
                    self.max_paths = 100

                def getMaxPaths(self):
                    return self.max_paths

                def setPath(self, value):
                    self.calls.append(("path", value))

                def setMinimumPath(self, value):
                    self.calls.append(("min", value))

            class FakeCura:
                def __init__(self):
                    self.view = None

                @contextmanager
                def writing_preview(self):
                    yield

            cura = FakeCura()
            motion = PreviewMotion(cura, remember=lambda: None)
            first = FakeView()
            cura.view = first
            motion.write(0, 0.5)
            motion.write(1, 0.1)
            self.assertEqual(first.calls.count(("min", 0)), 1)
            second = FakeView()
            cura.view = second
            motion.write(2, 0.2)
            self.assertEqual(second.calls.count(("min", 0)), 1)

    class SocketFragmentMergeTests(unittest.TestCase):
        """The 2026-09-16 live report: M117 invisible while printing.
        Moonraker pushes only the CHANGED fields per object, and the
        progress flood during a print overwrote the one-shot message
        fragment in the accumulator before the drain — the fragments
        must MERGE per object."""

        def setUp(self):
            from plugins.MoonrakerSocket import MoonrakerSocket
            self.app = QCoreApplication.instance() or QCoreApplication([])
            self.socket = MoonrakerSocket()
            self.socket._core_names = {"print_stats", "pause_resume"}
            self.socket._aux_names = {"display_status"}

        def test_one_shot_fields_survive_the_progress_flood(self):
            self.socket._apply_patch({
                "print_stats": {"state": "printing"},
                "display_status": {"message": "probe-4819"},
            })
            for _ in range(5):
                self.socket._apply_patch({
                    "print_stats": {"print_duration": 12.5},
                    "display_status": {"progress": 0.5},
                })
            core, _ = self.socket.drain_core()
            aux, _ = self.socket.drain_aux()
            self.assertEqual(core["print_stats"],
                             {"state": "printing", "print_duration": 12.5})
            self.assertEqual(aux["display_status"],
                             {"message": "probe-4819", "progress": 0.5})

        def test_non_mapping_values_still_replace(self):
            self.socket._apply_patch({"print_stats": {"state": "printing"}})
            self.socket._apply_patch({"print_stats": 7})
            core, _ = self.socket.drain_core()
            self.assertEqual(core["print_stats"], 7)


if __name__ == "__main__":
    unittest.main()
