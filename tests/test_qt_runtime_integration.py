"""Behavioural coverage of the assembled plugin using real Qt, not source strings."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, Preferences, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class QtRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.clients = []
        self.followers = []
        self.addCleanup(self.close_runtime)

    def close_runtime(self):
        for follower in self.followers:
            follower.deinitialize()
        for client in self.clients:
            client.stop()
        self.qt.events()

    def client(self):
        transport = ScriptedTransport()
        client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=transport)
        client.configure("http://printer-a", "test-key", 750)
        self.clients.append(client)
        return client, transport

    def follower(self, *, preferences=None, machine=True):
        app = self.qt.Application(preferences=preferences, machine=machine)
        module = self.qt.load("MoonrakerClient")
        real_client = module.MoonrakerClient
        transport = ScriptedTransport()
        # Inject transport through the real client's constructor; instantiate all
        # composed services/signals, including the real binding startup.
        with patch.object(self.qt.load("FollowerRuntime"), "MoonrakerClient",
                          lambda parent: real_client(parent, transport=transport)):
            follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.followers.append(follower)
        return app, follower, transport

    def output(self):
        client, transport = self.client()
        app = self.qt.Application()
        config = self.qt.load("PrinterConfig").PrinterConfig(
            url="http://printer-a", api_key="test-key", upload_dialog=True)
        follower = SimpleNamespace(client=client, transport=transport, session=client.session,
            current_printer_config=lambda: config,
            current_printer_identity=lambda: (app.stack.getId(), app.stack.getName()),
            apply_printer_config=lambda updated: None)
        device = self.qt.load("MoonrakerOutputDevice").MoonrakerOutputDevice(app, "A",
            client=client, config=follower.current_printer_config,
            apply_config=follower.apply_printer_config, active_identity=follower.current_printer_identity)
        plugin = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app, follower)
        plugin._devices["A"] = device
        plugin._current = device
        self.addCleanup(device.deactivate)
        app.createQmlComponent = Mock(return_value=Mock())
        device.requestWrite(None)
        return app, device, plugin, client, transport

    def monitor(self, *, typed=False):
        client, transport = self.client()
        config = self.qt.load("PrinterConfig").PrinterConfig(url="http://printer-a", api_key="test-key")
        from PyQt6.QtCore import QObject, pyqtSignal
        class Mesh(QObject):
            changed = pyqtSignal()
            visible = True
            def __init__(self):
                super().__init__()
                self.snapshot = {}
            def update(self, value):
                if value != self.snapshot:
                    self.snapshot = value
                    self.changed.emit()
            def set_visible(self, value):
                self.visible = value
                self.changed.emit()
        model = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel(None, 1,
            client=client, print_state=lambda: self.qt.load("PrintState").PrintSnapshot(),
            config=lambda: config, apply_config=lambda value: None, bed_mesh=Mesh())
        client._poll_timer.stop()
        self.addCleanup(model.setMonitoringActive, False)
        return model, client, transport

    def test_full_follower_bootstrap_migrates_before_first_connection(self):
        prefs = Preferences({"moonraker/instances": json.dumps({"A": {"url": "http://imported", "api_key": "import-key"}})})
        app, follower, transport = self.follower(preferences=prefs)
        self.assertEqual(transport.identity, ("http://imported", "import-key"))
        self.assertEqual(follower.client.session.base_url, "http://imported")
        self.assertTrue(transport.requests)

    def test_unknown_machine_migration_is_retried_when_stack_appears(self):
        prefs = Preferences({"moonraker_print_follower/url": "http://legacy",
                             "moonraker_print_follower/enabled": True})
        app, follower, transport = self.follower(preferences=prefs, machine=False)
        key = self.qt.load("PrinterConfig").PrinterConfigStore.MIGRATED_KEY
        self.assertFalse(prefs.getValue(key))
        app.stack = self.qt.Machine("A")
        app.globalContainerStackChanged.emit()
        self.assertEqual(transport.identity[0], "http://legacy")
        self.assertTrue(prefs.getValue(key))

    def test_failed_migration_does_not_prevent_bootstrap(self):
        config_type = self.qt.load("PrinterConfig").PrinterConfigStore
        with patch.object(config_type, "migrate_legacy_to_current_machine", side_effect=ValueError("old data")):
            self.follower()

    def test_unconfigured_url_placeholder_does_not_start_networking(self):
        app, follower, transport = self.follower()
        self.assertEqual(transport.requests, [])
        binding = self.qt.load("PrinterBinding").PrinterBinding
        for placeholder in ("", "http://", "https://", "http:", "https:"):
            self.assertFalse(binding.usable(self.qt.load("PrinterConfig").normalise_url(placeholder)))

    def test_connection_edit_invalidates_follower_domains(self):
        app, follower, transport = self.follower()
        config_type = self.qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a"))
        follower.client.statusReceived.emit({"print_stats": {"state": "printing", "filename": "same.gcode"}, "virtual_sdcard": {"file_size": 100}})
        follower._runtime.pauses.toggle(4, 0, 10)
        generation = follower._runtime.cura.generation
        follower.apply_printer_config(config_type(url="http://printer-b"))
        self.assertIsNone(follower.print_state.job_key)
        self.assertFalse(follower._runtime.pauses.layers)
        self.assertGreater(follower._runtime.cura.generation, generation)
        self.assertEqual(transport.identity[0], "http://printer-b")

    def test_same_endpoint_machine_switch_still_invalidates_generation(self):
        prefs = Preferences({"moonraker_print_follower/printer_configs_v1": json.dumps({
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
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True, path_follow=False))
        follower.client.statusReceived.emit({"print_stats": {"state": "printing", "filename": "part.gcode",
            "info": {"current_layer": 2}}, "virtual_sdcard": {"file_size": 100}})
        self.assertEqual(follower.print_state.observation.filename, "part.gcode")
        self.assertTrue(any(r.channel == "metadata" for r in transport.requests))

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
        follower.apply_printer_config(config_type(url="http://printer-a", path_follow=False))
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
        follower.apply_printer_config(self.qt.load("PrinterConfig").PrinterConfig(url="http://printer-a"))
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
        client.force_refresh()
        transport.requests[-1].callback(None, "offline")
        self.qt.events()
        client.set_pause_guard(True)
        client.force_refresh()
        self.assertEqual(len(transport.requests), 1)
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

    def test_monitor_unchanged_intervals_are_not_restarted(self):
        model, client, transport = self.monitor()
        timer = next(iter(model._data._timers.values()))
        with patch.object(timer, "setInterval", wraps=timer.setInterval) as setter:
            for _ in range(20): model.updateMoonrakerStatus({})
        setter.assert_not_called()

    def test_monitor_background_timers_fire_during_frequent_status_updates(self):
        model, client, transport = self.monitor()
        policy = self.qt.load("MoonrakerSession").PollPolicy
        client.session.state.poll_policy = policy(auxiliary_idle_ms=30, power_ms=40, system_ms=50, discovery_ms=60)
        model._data._intervals()
        counts = [0, 0, 0, 0]
        for index, timer in enumerate(model._data._timers.values()):
            timer.timeout.connect(lambda i=index: counts.__setitem__(i, counts[i] + 1))
        updates = self.qt.QTimer()
        updates.setInterval(5)
        updates.timeout.connect(lambda: model.updateMoonrakerStatus({}))
        updates.start()
        self.qt.events(160)
        updates.stop()
        self.assertTrue(all(count >= 1 for count in counts), counts)

    def test_monitor_idle_active_policy_and_disconnect(self):
        model, client, transport = self.monitor()
        timer = next(iter(model._data._timers.values()))
        self.assertEqual(timer.interval(), 2500)
        client.session.merge_status({"print_stats": {"state": "printing"}})
        model.updateMoonrakerStatus(client.status)
        self.assertEqual(timer.interval(), 1000)
        client.connectionChanged.emit(False, "offline")
        self.assertEqual(model.monitorState, "Disconnected")

    def test_deactivated_monitor_clears_peripherals_and_delayed_refreshes(self):
        model, client, transport = self.monitor(typed=True)
        callback = Mock()
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
        self.assertFalse(os.path.exists(path))
        self.assertEqual(identities, [("http://printer-a", "test-key")])
        self.qt.events()
        self.assertEqual(completed, [device])

    def test_old_write_timer_cannot_run_after_new_write_same_device(self):
        app, device, plugin, client, transport = self.output()
        callback = Mock()
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
        self.assertTrue(os.path.exists(path))
        self.qt.events()
        self.assertFalse(os.path.exists(path))
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
        reply = Mock()
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
        callback = Mock()
        device._upload._request("GET", "server/info", callback)
        old = transport.requests[-1]
        device.deactivate()
        self.qt.events()
        device.requestWrite(None)
        old.callback({"result": {"klippy_state": "ready"}}, None)
        callback.assert_not_called()

    def test_real_http_transport_json_errors_and_owner_cancellation(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.cancel_all)
        results = []
        transport.send_json("test", "good", "GET", "/good", lambda p, e: results.append((p, e)))
        transport.send_json("test", "bad", "GET", "/bad", lambda p, e: results.append((p, e)))
        cancelled = Mock()
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


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class MonitorDataAuxTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=self.transport)
        self.client.configure("http://printer-a", "test-key", 750)
        self.addCleanup(self.client.stop)
        self.data = self.qt.load("MonitorData").MonitorData(self.client, None)
        self.data.set_active(True)
        self.addCleanup(self.data.set_active, False)

    def deliver(self, channel, payload, error=None):
        request = next((r for r in self.transport.requests if r.channel == channel), None)
        self.assertIsNotNone(request, channel)
        request.callback(payload, error)

    def test_auxiliary_snapshot_prunes_removed_objects(self):
        self.deliver("objects", {"result": {"objects": ["fan", "heater_bed"]}})
        self.deliver("aux", {"result": {"status": {"fan": {"speed": 0.5}, "heater_bed": {"target": 60}}}})
        self.assertEqual(set(self.data.snapshot.auxiliary), {"fan", "heater_bed"})

        # The fan disappears from printer/objects/list; the next aux response
        # must stop keeping its stale values alive.
        self.transport.requests.clear()
        self.data.refresh_discovery()
        self.deliver("objects", {"result": {"objects": ["heater_bed"]}})
        self.deliver("aux", {"result": {"status": {"heater_bed": {"target": 55}}}})
        self.assertEqual(set(self.data.snapshot.auxiliary), {"heater_bed"})
        self.assertEqual(self.data.snapshot.auxiliary["heater_bed"]["target"], 55)

    def test_camera_restore_bails_when_webcams_changed_before_turn(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeData(QObject):
            changed = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.active = True
                self.snapshot = SimpleNamespace(webcams=(
                    {"uid": "front-uid", "name": "Front", "stream_url": "/front"},))

        camera_module = self.qt.load("MonitorCamera")
        config = self.qt.load("PrinterConfig").PrinterConfig(camera_selected="front-uid")
        fake = FakeData()
        camera = camera_module.MonitorCamera(fake, lambda: config, lambda value: None)
        # The constructor's observe() already scheduled the zero-timer restore.

        # Swap the webcam set before the scheduled zero-timer fires.
        fake.snapshot = SimpleNamespace(webcams=(
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},))
        self.qt.events()

        # The stale restore must have bailed; a fresh observe against the
        # current snapshot restores cleanly.
        self.assertTrue(camera._restore_pending)
        fake.changed.emit()
        self.qt.events()
        self.assertEqual(camera.values["activeWebcamIndex"], 0)
        self.assertEqual(camera.values["cameraName"], "Rear")


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class RemoteFileServiceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.transport.configure("http://printer-a", "test-key")
        self.service = self.qt.load("RemoteFileService").RemoteFileService(self.transport, None)
        self.addCleanup(self.service.close)
        self.service.bind(("part.gcode", 1000, 1))

    def test_metadata_failure_retries_after_backoff_and_completes_on_success(self):
        self.service.METADATA_RETRY_DELAYS_MS = (5,)
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 1)

        # A failure installs a fallback download identity but not completeness.
        self.transport.requests[-1].callback(None, "connection refused")
        self.assertEqual(self.service.identity.filename, "part.gcode")
        self.assertEqual(self.service.identity.size, 1000)
        self.assertFalse(self.service.metadata_complete)

        # Inside the backoff window a retry for the same job is suppressed.
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 1)

        # After the window the same job retries; success completes metadata.
        self.qt.events(15)
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 2)
        self.transport.requests[-1].callback({"result": {"estimated_time": 3600, "uuid": "u-1"}}, None)
        self.assertTrue(self.service.metadata_complete)
        self.assertEqual(self.service.identity.uuid, "u-1")
        self.assertEqual(self.service.metadata["estimated_time"], 3600)

        # Complete metadata is never refetched for the same job.
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 2)

    def test_bind_resets_metadata_state_for_a_new_job(self):
        self.service.request_metadata()
        self.transport.requests[-1].callback({"result": {"uuid": "u-1"}}, None)
        self.assertTrue(self.service.metadata_complete)

        self.service.bind(("part.gcode", 1000, 2))
        self.assertFalse(self.service.metadata_complete)
        self.assertIsNone(self.service.identity)


if __name__ == "__main__":
    unittest.main()






