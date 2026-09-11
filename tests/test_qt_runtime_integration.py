"""Behavioural coverage of the assembled plugin using real Qt, not source strings."""
from __future__ import annotations

import json
import os
import tempfile
import time
from http.server import ThreadingHTTPServer
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, Preferences, ScriptedTransport, runtime


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
        # The metadata pull serves the Preview, so it only runs once the
        # print's G-code is loaded in Cura.
        app.controller.view = SimpleNamespace(getActivity=lambda: True)
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
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True))
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
        client.session.state.poll_policy = policy(auxiliary_idle_ms=30, power_ms=40, system_ms=50, endstops_ms=70, console_ms=30, console_idle_ms=30, discovery_ms=60)
        model._data._intervals()
        counts = [0, 0, 0, 0, 0, 0]  # auxiliary, power, system, endstops, console, discovery
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
        self.assertEqual(timer.interval(), 2500)
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
        class Handler(PipeSafeHandler):
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
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.cancel_all)
        manager = self.qt.load("FileManager").FileManager(SimpleNamespace(transport=transport))
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
        # own words in the note (the author's ruling: refusals
        # surface, never vanish).
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.cancel_all)
        manager = self.qt.load("FileManager").FileManager(SimpleNamespace(transport=transport))
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
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.cancel_all)
        manager = self.qt.load("FileManager").FileManager(SimpleNamespace(transport=transport))
        manager._directory = ["prints"]
        notes = []
        progress_events = []
        finished_events = []
        manager.note.connect(notes.append)
        manager.uploadProgress.connect(progress_events.append)
        manager.uploadFinished.connect(lambda ok, detail: finished_events.append((ok, detail)))
        directory = tempfile.mkdtemp()
        source = os.path.join(directory, "bench.gcode")
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
        # the success (the author's live request).
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
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        foreign_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        foreign_thread = threading.Thread(target=foreign_server.serve_forever, daemon=True)
        foreign_thread.start()
        self.addCleanup(foreign_server.server_close)
        self.addCleanup(foreign_server.shutdown)

        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        base = "http://127.0.0.1:" + str(server.server_port)
        transport.configure(base, "test-key")
        self.addCleanup(transport.cancel_all)
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
        # The author's live report: a cold extrude surfaced a bare
        # 400 while Moonraker's real words sat in the traceback tail
        # ({'code', 'message': 'Unknown', 'traceback'}). The error
        # must read "Extrude below minimum temp", not a status code
        # or the whole dict — and the script endpoint answers HTTP
        # 200 with the error DICT inside "error" (the author's
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
            return json.dumps(inner if shape == "flat" else {"error": inner}).encode()
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.cancel_all)
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
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = self.qt.load("MoonrakerTransport").MoonrakerHttpTransport()
        transport.configure("http://127.0.0.1:" + str(server.server_port), "test-key")
        self.addCleanup(transport.cancel_all)
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
        # The author's live request: ALL 400-class errors must read
        # like the cold-extrude one — the server's words, one line,
        # never a dict, a code or a whole exception. Pure shapes.
        from plugins.MoonrakerTransport import _moonraker_error_text
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
        follower.apply_printer_config(config_type(url="http://printer-a", enabled=True))
        follower.client._handle_http_status({"result": {"status": {
            "print_stats": {"state": "printing", "filename": "part.gcode"},
            "virtual_sdcard": {"file_size": 10, "file_position": 2}}}},
            None, follower.client._generation)
        self.qt.events()
        app.controller.stage = SimpleNamespace(getId=lambda: "PreviewStage")
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

        # Cura's late restoration moves the view: within the echo window
        # (but past the settle grace, which would otherwise mask it) the
        # mismatch is absorbed instead of detaching.
        self.qt.events(2400)
        second.layer = 10
        second.currentLayerNumChanged.emit()
        self.qt.events()
        self.assertTrue(preview.state.attached)
        self.assertIsNone(preview.state.expected_layer)

        # A restoration landing outside every window still detaches — but
        # the watchdog re-attaches after the view has been quiet and the
        # user is still in the Preview stage.
        preview.remember()  # the follower re-arms on the settled view
        self.assertEqual(preview.state.expected_layer, 10)
        preview._echo_until = 0.0
        second.layer = 42
        second.currentLayerNumChanged.emit()
        self.qt.events()
        self.assertFalse(preview.state.attached)
        self.qt.events(3100)
        self.assertTrue(preview.state.attached)

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
        # The constructor's observe() published the Front model and scheduled
        # its selection restore for the next Qt turn.
        self.assertTrue(camera._restore_pending)
        first_signature = camera._camera_signature

        # A second snapshot replaces the webcam set before that turn arrives.
        # The change reaches observe() exactly as in production
        # (data.changed -> observe) and re-publishes the model for Rear.
        fake.snapshot = SimpleNamespace(webcams=(
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},))
        fake.changed.emit()
        self.assertNotEqual(camera._camera_signature, first_signature)
        self.assertEqual(camera.values["activeWebcamIndex"], -1)

        # The stale Front restore fires first: it was scheduled for a snapshot
        # that is no longer current, so it must bail and keep the restore
        # pending rather than publish an index against the Rear model early.
        camera._restore_after_population(first_signature)
        self.assertTrue(camera._restore_pending)
        self.assertEqual(camera.values["activeWebcamIndex"], -1)

        # The restore scheduled against the current snapshot then completes.
        self.qt.events()
        self.assertFalse(camera._restore_pending)
        self.assertEqual(camera.values["activeWebcamIndex"], 0)
        self.assertEqual(camera.values["cameraName"], "Rear")

    def test_camera_url_guard_rejects_whitespace_masked_external_hosts(self):
        # QUrl strips surrounding whitespace per RFC 3986, so a
        # " //evil.example/x" stream must be rejected on the STRIPPED
        # form — otherwise a hostile listing points Cura's loader at
        # an arbitrary host (the adversarial round's catch).
        from PyQt6.QtCore import QObject, pyqtSignal
        camera_module = self.qt.load("MonitorCamera")
        config = self.qt.load("PrinterConfig").PrinterConfig(url="http://printer-a")
        class FakeData(QObject):
            changed = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.active = True
                self.snapshot = SimpleNamespace(webcams=(
                    {"uid": "front-uid", "name": "Front", "stream_url": " //evil.example/x"},))
        fake = FakeData()
        camera = camera_module.MonitorCamera(fake, lambda: config, lambda value: None)
        self.assertEqual(camera.values["webcamNames"], ["Front"])
        self.assertEqual(camera.url, "")

@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class PreviewMotionTests(unittest.TestCase):
    def setUp(self):
        from contextlib import contextmanager

        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.view = SimpleNamespace(path=0.0, minimum=0)
        def set_path(value): self.view.path = value
        def set_minimum(value): self.view.minimum = value
        self.view.setPath = set_path
        self.view.setMinimumPath = set_minimum
        self.view.getCurrentPath = lambda: self.view.path
        self.view.getMinimumPath = lambda: self.view.minimum
        self.view.getMaxPaths = lambda: 100

        @contextmanager
        def writing_preview():
            yield self.view

        self.cura = SimpleNamespace(view=self.view, writing_preview=writing_preview)
        self.remembers = 0
        def remembered():
            self.remembers += 1
        self.motion = self.qt.load("PreviewMotion").PreviewMotion(self.cura, remembered)
        self.addCleanup(self.motion.close)

    def test_layer_change_jumps_and_same_layer_cruises(self):
        # The fake view exposes 100 max paths; the driver works in fractions.
        self.motion.write(0, 0.5)
        self.assertEqual(self.view.path, 50.0)  # first observation jumps
        # Build a filled observation window so the cruise rate is estimated.
        self.qt.events(300)
        self.motion.write(0, 0.55)
        self.qt.events(300)
        self.motion.write(0, 0.6)
        self.qt.events(400)
        # The head cruises at the windowed rate: it has advanced past the
        # first observation's position and never exceeds the newest
        # observation (the hard ceiling).
        self.assertGreater(self.view.path, 50.0)
        self.assertLessEqual(self.view.path, 60.0)
        # A new layer jumps straight to its target; no cross-layer animation.
        self.motion.write(1, 0.05)
        self.assertEqual(self.view.path, 5.0)

    def test_target_ramps_between_observations(self):
        # Two observations 0.6 s apart: the target is reconstructed between
        # them, so the head glides forward instead of holding at the first
        # observation until the poll lands and then stepping.
        self.motion.write(0, 0.5)
        self.assertEqual(self.view.path, 50.0)
        self.qt.events(600)
        self.motion.write(0, 0.8)
        self.qt.events(250)
        # Mid-ramp: the head has advanced continuously past the first
        # observation but never exceeds the newest one (the hard cap).
        self.assertGreater(self.view.path, 50.0)
        self.assertLess(self.view.path, 80.0)
        # Once the ramp saturates, the display converges to the newest
        # observation, never through it and never backwards.
        self.qt.events(700)
        self.assertLessEqual(self.view.path, 80.0)

    def test_timer_snaps_and_stops_when_decay_converges(self):
        # With no velocity estimate, gap decay approaches the target
        # asymptotically; the last invisible sliver must snap so the timer
        # does not tick at 30 Hz for the whole duration of a pause.
        self.motion.write(0, 0.5)
        self.motion._history.clear()  # a single sample carries no rate
        self.motion.write(0, 0.6)
        self.motion._displayed = 0.6 - 1e-7
        self.motion._tick()
        self.assertEqual(self.motion._displayed, 0.6)
        self.assertFalse(self.motion._timer.isActive())
        self.assertEqual(self.view.path, 60.0)

    def test_slow_poll_interval_scales_the_velocity_window(self):
        # A 5 s poll interval would prune a fixed 2 s window to a single
        # sample and freeze the rate estimate; the window must scale with
        # the measured interval.
        time_module = self.qt.load("PreviewMotion").time
        with patch.object(time_module, "monotonic", side_effect=[0.0, 5.0, 10.0]):
            self.motion._inter_poll = 5.0
            self.motion.write(0, 0.5)
            self.motion.write(0, 0.8)
            self.motion.write(0, 0.9)
        self.assertGreater(self.motion._velocity, 0.0)

    def test_ramp_chains_from_reached_position_and_reset_clears_it(self):
        time_module = self.qt.load("PreviewMotion").time
        with patch.object(time_module, "monotonic", side_effect=[0.0, 0.5, 1.0, 1.25]):
            self.motion.write(0, 0.5)
            self.motion.write(0, 0.8)
            self.motion.write(0, 0.9)
            # The previous ramp (0.5 -> 0.8 over 0.5 s) had saturated by the
            # time the next observation arrived, so the new ramp continues
            # from the reached position without a jump.
            self.assertAlmostEqual(self.motion._ramp_from, 0.8)
            self.assertEqual(self.motion._ramp_to, 0.9)
            self.assertAlmostEqual(self.motion._inter_poll, 0.5)
            # Mid-ramp the reconstructed target is linearly between the two.
            self.assertAlmostEqual(self.motion._current_target(1.25), 0.85)
        self.motion.reset()
        self.assertIsNone(self.motion._ramp_from)
        self.assertIsNone(self.motion._ramp_to)
        self.assertIsNone(self.motion._obs_time)
        self.assertEqual(self.motion._inter_poll, 0.5)  # survives a reset

    def test_trace_writes_only_when_a_path_is_provided(self):
        with tempfile.TemporaryDirectory() as directory:
            traced = self.qt.load("PreviewMotion").PreviewMotion(
                self.cura, lambda: None, trace_path=os.path.join(directory, "trace.csv"))
            self.addCleanup(traced.close)
            traced.write(0, 0.5)
            traced.write(0, 0.55)
            path = os.path.join(directory, "trace.csv")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            # The header is written on rollover; observation rows always are.
            self.assertIn(",obs,0,0.500000,", content)

    def test_target_behind_display_never_moves_backwards(self):
        self.motion.write(0, 0.9)
        self.qt.events(200)
        reached = self.view.path
        self.motion.write(0, 0.3)  # stale/ambiguous observation behind us
        self.qt.events(200)
        self.assertGreaterEqual(self.view.path, reached)
        self.assertLessEqual(self.view.path, 90.0)

    def test_writes_are_remembered(self):
        self.motion.write(0, 0.5)
        self.qt.events(60)
        self.assertGreater(self.remembers, 0)

    def test_reset_stops_animation_until_next_observation(self):
        self.motion.write(0, 0.7)
        self.qt.events(60)
        moving = self.view.path
        self.motion.reset()
        self.qt.events(120)
        self.assertEqual(self.view.path, moving)
        self.motion.write(0, 0.75)
        self.assertEqual(self.view.path, 75.0)  # re-synchronises with a jump


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class RemoteFileServiceDownloadTests(unittest.TestCase):
    """The streamed-download state machine: failure latch and backoff retry."""

    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.transport.configure("http://printer-a", "test-key")
        module = self.qt.load("RemoteFileService")
        self.files = module.RemoteFileService(self.transport, None)
        self.addCleanup(self.files.close)
        self.files.bind(("part.gcode", 100, 1))
        self.files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 0, modified=1)
        self.files._want_file = True

    def _reply_double(self, error=False):
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtNetwork import QNetworkReply

        class FakeReply(QObject):
            readyRead = pyqtSignal()
            finished = pyqtSignal()
            def __init__(self, error):
                super().__init__()
                self._error = QNetworkReply.NetworkError.ContentNotFoundError if error else QNetworkReply.NetworkError.NoError
            def setReadBufferSize(self, size): pass
            def readAll(self): return b"G1 X0 Y0\n"
            def header(self, name): return None  # no declared size: the bounded read path applies
            def read(self, maxsize): return self.readAll()
            def bytesAvailable(self): return 0
            def error(self): return self._error
            def errorString(self): return "simulated"
            def abort(self): pass
            def deleteLater(self): pass
        return FakeReply(error)

    def test_failure_latches_until_the_backoff_window_passes(self):
        failures = []
        self.files.failed.connect(failures.append)
        self.files._fail("Connection refused")
        self.assertEqual(failures, ["Connection refused"])
        self.assertEqual(self.files.phase, "error")
        # Inside the window a consumer re-request must not hit the network.
        self.files.request_file()
        self.assertEqual(self.files.phase, "error")
        self.assertEqual(len(self.transport.requests), 0)
        # Past the window the next re-request restarts the download and a
        # successful reply completes it.
        self.files._download_retry_at = 0.0
        reply = self._reply_double()
        self.transport.network = SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        self.assertEqual(self.files.phase, "downloading")
        reply.finished.emit()
        self.assertEqual(self.files.phase, "ready")
        self.assertEqual(self.files._error, "")
        self.assertTrue(self.files.path and self.files.path.endswith("part.gcode"))

    def test_errored_reply_fails_and_schedules_a_retry(self):
        reply = self._reply_double(error=True)
        self.transport.network = SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        reply.finished.emit()
        self.assertEqual(self.files.phase, "error")
        self.assertEqual(self.files._download_attempts, 1)
        self.assertGreater(self.files._download_retry_at, time.monotonic())


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class ToolheadControllerTests(unittest.TestCase):
    """The jog queue and pause-first sequencing against scripted doubles."""

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

        class Data(QObject):
            changed = pyqtSignal()
            invalidated = pyqtSignal()
            commandChanged = pyqtSignal(object)
            def __init__(self):
                super().__init__()
                self.active = True
                self.connected = True
                self.guard_calls = []
                self.snapshot = SimpleNamespace(core={}, auxiliary={})
            def set_toolhead_guard(self, active):
                self.guard_calls.append(active)
            def set_state(self, state):
                self.snapshot = SimpleNamespace(
                    core={"print_stats": {"state": state},
                          "gcode_move": {"absolute_coordinates": True},
                          "motion_report": {"live_position": [10.0, 10.0, 10.0, 0.0]}},
                    auxiliary={"toolhead": {"homed_axes": "xyz",
                                             "axis_minimum": [0, 0, 0],
                                             "axis_maximum": [200, 200, 200]}})
                self.changed.emit()

        class Commands(QObject):
            changed = pyqtSignal()
            emergencyStopped = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.busy = False
                self.fail_next = False
                self.sent = []
            def send(self, label, path, body=None):
                if self.busy:
                    return False
                if self.fail_next:
                    # A refused send completes synchronously: the real
                    # MonitorCommands clears busy, emits changed (re-entering
                    # the controller's pump), then the tracker emits the
                    # terminal failed event.
                    self.fail_next = False
                    self.changed.emit()
                    self.owner.commandChanged.emit(
                        {"name": label, "outcome": "failed", "terminal": True, "detail": "unavailable"})
                    return False
                self.sent.append((label, path, body))
                self.busy = True
                return True
            def complete(self):
                self.busy = False
                self.changed.emit()

        self.data = Data()
        self.commands = Commands()
        self.commands.owner = self.data
        module = self.qt.load("ToolheadController")
        self.controller = module.ToolheadController(self.data, self.commands)
        self.addCleanup(self.controller.close)

    def scripts(self):
        return [body["script"] for label, path, body in self.commands.sent if path == "printer/gcode/script"]

    def pauses(self):
        return [1 for label, path, body in self.commands.sent if path == "printer/print/pause"]

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
        # and the client-side Z estimate (the author's live report:
        # stale-poll clamping let rapid taps overshoot) makes the
        # second tap a no-op.
        self.data.snapshot.core["motion_report"]["live_position"][2] = 195.0
        self.data.changed.emit()
        self.controller.jog("z", 1)
        self.assertIn("G91\nG1 Z5 F600\nG90", self.scripts())
        self.controller.jog("z", 1)
        self.assertEqual(len(self.scripts()), 1)  # at the boundary: no-op
        self.assertEqual(self.controller._z_estimate, 200.0)
        self.commands.complete()
        # A fresh poll at the boundary keeps the tap a no-op.
        self.data.snapshot.core["motion_report"]["live_position"][2] = 200.0
        self.data.changed.emit()
        self.controller.jog("z", 1)
        self.assertEqual(self.controller._pending, ())

    def test_z_floor_is_zero_even_with_a_negative_configured_minimum(self):
        # The author's live ruling: the jog pad must never send the
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
        # The author's live request: a rejected nudge must explain
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
        # An accepted move re-arms the note for the next burst.
        self.controller.jog("z", 1)
        self.data.snapshot.core["motion_report"]["live_position"][2] = 0.1
        self.data.changed.emit()
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
        self.data.snapshot = SimpleNamespace(
            core={"print_stats": {"state": "paused"},
                  "gcode_move": {"absolute_coordinates": True}},
            auxiliary={"toolhead": {"homed_axes": "xyz"}})
        self.data.changed.emit()
        self.controller.center_toolhead()
        self.assertEqual(self.scripts()[-1], "G1 Z0 F600")

    def test_moves_toward_the_minimum_are_forbidden_without_position_data(self):
        self.data.set_state("paused")
        self.controller.set_distance(25)
        self.data.snapshot = SimpleNamespace(
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
        with patch.object(controller, "PAUSE_WAIT_TIMEOUT_S", 0.05):
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

    def test_taps_coalesce_while_a_move_is_in_flight(self):
        self.data.set_state("paused")
        self.controller.set_distance(1)
        self.controller.jog("x", 1)
        self.controller.jog("x", 1)
        self.controller.jog("x", 1)
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90"])
        self.commands.complete()
        self.assertEqual(self.scripts(), ["G91\nG1 X1 F3000\nG90", "G91\nG1 X2 F3000\nG90"])
        self.assertEqual(self.controller.values["jogStatus"], "")


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






