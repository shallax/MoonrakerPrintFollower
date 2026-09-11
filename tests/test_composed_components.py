"""Executable contracts for the completed component boundaries."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from http.server import ThreadingHTTPServer
import json
import os
import pathlib
import tempfile
import threading
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, ScriptedSocket, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ComposedComponentTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        self.socket = ScriptedSocket()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=self.socket)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.parts = self.follower._runtime
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        # The harness tests HTTP semantics; the product default stays in
        # PrinterConfig, never in the harness.
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def status(self, *, layer=10, filename="part.gcode", duration=30, position=40, state="printing"):
        return {"print_stats": {"filename": filename, "state": state, "print_duration": duration,
                "info": {"current_layer": layer, "total_layer": 50}},
            "virtual_sdcard": {"file_size": 100, "file_position": position},
            "gcode_move": {"gcode_position": [1, 1, 2, 10], "speed_factor": 1, "extrude_factor": 1}}

    def deliver(self, status):
        import time
        client = self.follower.client
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def test_facade_has_no_legacy_private_state_or_mixin_bases(self):
        for name in ("_remote_job_service", "_preview_follower_service", "_simulation_view", "_apply_path_progress", "_config_store"):
            self.assertFalse(hasattr(self.follower, name), name)
        self.assertFalse(any("Mixin" in cls.__name__ for cls in type(self.follower).__mro__))

    def test_thumbnail_publishes_coalesce_onto_one_flush(self):
        # A burst of landings repaints the QML once, not once per
        # callback (the landing storm stalled scrolling).
        model = self.monitor()
        count = []
        model.fileManagerThumbsChanged.connect(lambda: count.append(1))
        # A real change in the payload, then a burst of signals: the
        # flush emits once, and a no-change flush emits nothing.
        model._file_manager._thumbs["x.gcode"] = {"state": "ready", "url": ""}
        for _ in range(3):
            model._file_manager.thumbsChanged.emit()
        self.qt.events(10)
        self.assertEqual(count, [])
        self.qt.events(200)
        self.assertEqual(count, [1])
        model._file_manager.thumbsChanged.emit()
        self.qt.events(200)
        self.assertEqual(count, [1])

    def test_monitor_and_preview_consume_one_physical_observation(self):
        model = self.monitor()
        self.deliver(self.status(layer=10))
        before = self.follower.print_state
        self.assertEqual(before.layer.index, 9)
        self.assertEqual(model.monitorLayer, "10 / 50")
        for _ in range(5):
            model._publish()
        self.assertIs(self.follower.print_state, before)
        with self.assertRaises(FrozenInstanceError):
            before.layer.index = 20

    def test_monitor_snapshot_is_deeply_immutable(self):
        model = self.monitor()
        self.deliver(self.status())
        snapshot = model._data.snapshot
        with self.assertRaises(TypeError): snapshot.core["print_stats"]["state"] = "paused"
        with self.assertRaises(TypeError): snapshot.core["print_stats"] = {}

    def test_no_borrowed_cura_file_released_by_unrelated_completion_or_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            pathlib.Path(path).write_text("G1 X0")
            releases = []
            lease = self.qt.load("RemoteFileService").FileLease(path, releases.append)
            self.assertTrue(self.parts.cura.load(lease))
            self.app.fileCompleted.emit(os.path.join(directory, "unrelated.stl"))
            self.assertEqual(releases, [])
            self.assertTrue(self.parts.cura.loading)
            self.parts.cura.close()
            self.assertEqual(releases, [])
            self.app.fileCompleted.emit(path)
            self.assertEqual(releases, [path])

    def test_rebind_retires_file_but_lease_keeps_it_alive(self):
        files = self.parts.files
        files.bind(("old.gcode", 100, 1))
        directory = tempfile.mkdtemp(dir=files._root)
        path = os.path.join(directory, "old.gcode")
        pathlib.Path(path).write_text("G1 X0")
        files._path = path
        lease = files.lease()
        files.bind(("new.gcode", 100, 2))
        self.assertTrue(os.path.exists(path))
        lease.close()
        self.assertFalse(os.path.exists(path))
        lease.close()

    def test_cache_restore_is_off_ui_thread_and_request_queue_is_bounded(self):
        service, files = self.parts.index, self.parts.files
        key = ("part.gcode", 100, 1)
        files.bind(key)
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 100, modified=1)
        service.bind(key)
        entered, release = threading.Event(), threading.Event()
        threads = []
        def load(identity):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return None
        self.addCleanup(release.set)
        with patch.object(service._cache, "load", load), patch.object(files, "request_file"):
            service.request()
            self.assertTrue(entered.wait(1))
            for _ in range(50): service.request()
            self.assertEqual(len(threads), 1)
            self.assertNotEqual(threads[0], threading.get_ident())
            service.bind(None)
            release.set()
            for _ in range(50):
                self.qt.events(5)
                if not service._busy: break
        self.assertIsNone(service.view)

    def test_index_view_does_not_expose_motion_arrays(self):
        module = self.qt.load("GCodeIndexService")
        index = self.qt.load("GCodeIndex").LayerMotionIndex(ranges=[(0, 100)], current_layer_map={1: 0})
        view = module.IndexView(("part.gcode", 100, 1), index)
        self.assertEqual(view.layer_at(50), 0)
        self.assertIsInstance(view.ranges, tuple)
        with self.assertRaises(TypeError): view.current_layer_map[2] = 1
        self.assertFalse(hasattr(view, "motion_offsets"))

    def test_start_print_power_probe_checks_every_device(self):
        config = self.config_type(url="http://printer-a", power_devices="socket,psu")
        upload = self.qt.load("UploadController").UploadController(
            self.follower.client, "A", self.follower.current_printer_identity)
        self.addCleanup(upload.abort)
        upload.begin(config, "part.gcode")
        upload._power = ["socket", "psu"]
        upload._power_off = []
        upload._probe_power(list(upload._power))

        def gets():
            return [r for r in self.transport.requests if r.method == "GET" and "device_power" in r.path]
        self.assertEqual(len(gets()), 1)
        gets()[0].callback({"result": {"socket": "on"}}, None)
        # The probe continues past an already-on device instead of assuming
        # the whole chain is powered.
        self.assertEqual(len(gets()), 2)
        gets()[1].callback({"result": {"psu": "off"}}, None)
        posts = [r for r in self.transport.requests if r.method == "POST" and "device_power" in r.path]
        self.assertEqual(len(posts), 1)
        self.assertIn("device=psu", posts[0].path)
        self.assertIn("action=on", posts[0].path)

    def test_failed_hydration_is_latched_until_a_new_file_arrives(self):
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        handle = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False)
        handle.write(b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n")
        handle.close()
        self.addCleanup(os.remove, handle.name)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(handle.name, compact=True)
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
        files._path = os.path.join(files._root, "job-1", "part.gcode")
        files._want_file = True

        def wait_idle():
            for _ in range(200):
                self.qt.events(5)
                if not service._busy and not service._hydrate: break
        with patch.object(module, "hydrate_layer_from_file", return_value=False) as hydrate:
            service.request_hydration(0)
            wait_idle()
            # A second poll re-requests the same layer; the latch must stop
            # the whole-file re-read.
            service.request_hydration(0)
            wait_idle()
        self.assertEqual(hydrate.call_count, 2)  # layers 0 and 1, once each
        self.assertEqual(service._failed_hydrate, {0, 1})
        self.assertEqual(service._busy, "")
        # A new file invalidates the latch: hydration is attempted again.
        files.changed.emit()
        with patch.object(module, "hydrate_layer_from_file", return_value=True) as hydrate2:
            service.request_hydration(0)
            wait_idle()
        self.assertEqual(hydrate2.call_count, 2)
        self.assertEqual(service._failed_hydrate, set())

    def test_service_failure_signals_are_logged(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "plugins" / "PrintCoordinator.py").read_text(encoding="utf-8")
        self.assertIn("files.failed.connect", source)
        self.assertIn("index.failed.connect", source)

    def test_smoothing_trace_is_opt_in(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "plugins" / "FollowerRuntime.py").read_text(encoding="utf-8")
        self.assertIn("MOONRAKER_FOLLOWER_SMOOTHING_TRACE", source)
        self.assertIn("os.environ.get", source)

    def test_file_backed_writer_and_duplicate_preparation_ownership(self):
        module = self.qt.load("CuraOutputWriter")
        config = self.config_type(url="http://printer-a", upload_dialog=True)
        writer = module.CuraOutputWriter(self.app)
        prepared = writer.prepare(config, "part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(pathlib.Path(prepared.path).read_text(), "G1 X0\n")
        upload = self.qt.load("UploadController").UploadController(self.follower.client, "A", self.follower.current_printer_identity)
        self.addCleanup(upload.abort)
        upload.begin(config, "part.gcode")
        upload.prepared(prepared)
        second = writer.prepare(config, "second.gcode")
        with self.assertRaises(RuntimeError): upload.prepared(second)
        self.assertFalse(os.path.exists(second.path))
        self.assertTrue(os.path.exists(prepared.path))

    def test_tuning_debounce_keeps_latest_value_and_ignores_old_callbacks(self):
        model = self.monitor()
        self.deliver(self.status())
        tuning = model._tuning
        tuning.DEBOUNCE_MS = 10
        for value in range(110, 140): model.setSpeedFactor(value)
        self.qt.events(25)
        commands = [r for r in self.transport.requests if r.channel.startswith("quick-")]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S139"})
        model.setMonitoringActive(False)
        commands[0].callback({}, None)
        self.assertFalse(tuning._pending)

    def test_emergency_stop_bypasses_busy_command_but_requires_the_held_third_press(self):
        model = self.monitor()
        model._commands._busy = True
        model.emergencyStopClick()
        model.emergencyStopClick()
        self.assertFalse(any(r.channel == "emergency-stop" for r in self.transport.requests))
        # The third press must be held; the test shortens the hold window.
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(sum(r.channel == "emergency-stop" for r in self.transport.requests), 1)
        # Releasing the fired hold delivers a click that must not arm anew.
        model.emergencyStopClick()
        self.assertEqual(model.emergencyStopClicks, 0)
        # A genuinely new click starts a fresh arm sequence.
        model.emergencyStopClick()
        self.assertEqual(model.emergencyStopClicks, 1)

    def test_power_lock_blocks_mutation_during_print(self):
        model = self.monitor()
        self.deliver(self.status())
        model._data._update(power=[{"device": "printer", "status": "on", "locked_while_printing": True}])
        before = len(self.transport.requests)
        model.setPowerDevice("printer", False)
        self.assertEqual(len(self.transport.requests), before)

    def test_macro_definitions_are_cached_until_config_changes(self):
        model = self.monitor()
        data = model._data
        data._update(objects=("gcode_macro TEST",), auxiliary={"configfile": {"config": {
            "gcode_macro TEST": {"gcode": "{{ params.COUNT|default(2)|int }}"}}}})
        controls_module = self.qt.load("MonitorControls")
        with patch.object(controls_module, "infer_macro_parameters", wraps=controls_module.infer_macro_parameters) as infer:
            model.macroParameterDefinitions("TEST")
            model.macroParameterDefinitions("TEST")
            data._update(core={"print_stats": {"state": "standby"}})
            model.macroParameterDefinitions("TEST")
            self.assertEqual(infer.call_count, 1)

    def test_pwm_discovery_scaling_and_led_color_commands(self):
        model = self.monitor()
        self.deliver(self.status())
        model._data._update(auxiliary={
            "configfile": {"config": {"output_pin case_light": {"pwm": "True", "scale": "2"},
                "output_pin relay": {"pwm": "False"}, "neopixel strip": {"color_order": "RGB"}}},
            "output_pin case_light": {"value": 1}, "output_pin relay": {"value": 1},
            "neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        items = model.pwmOutputItems.value()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["percent"], 50)
        self.assertEqual(items[0]["scale"], 2)
        model._tuning.DEBOUNCE_MS = 10
        model.setPwmOutput("output_pin case_light", 75)
        model.setLedColor("neopixel strip", 0, 100, 0, 100, 50)
        self.qt.events(25)
        scripts = [(r.options.get("body") or {}).get("script", "") for r in self.transport.requests]
        self.assertIn("SET_PIN PIN=case_light VALUE=1.5", scripts)
        self.assertTrue(any("GREEN=0.5000" in script and "WHITE=0.0000" in script for script in scripts))

    def test_temperature_presets_report_actual_targets_not_last_selection(self):
        model = self.monitor()
        model._data._update(presets={"presets": {"pla": {"name": "PLA", "values": {
            "extruder": {"bool": True, "value": 200}}}}}, auxiliary={"extruder": {"target": 180}})
        self.assertFalse(model.temperaturePresetItems.value()[0]["active"])
        model._data._update(auxiliary={"extruder": {"target": 200}})
        self.assertTrue(model.temperaturePresetItems.value()[0]["active"])

    def test_malformed_monitor_objects_degrade_without_throwing(self):
        model = self.monitor()
        model.updateMoonrakerStatus({"print_stats": "bad", "virtual_sdcard": [], "gcode_move": 7, "motion_report": None})
        self.assertEqual(model.monitorPosition, "—")
        self.assertEqual(model.monitorProgress, 0)

    def test_file_backed_multipart_upload_and_terminal_ordering(self):
        received = []
        class Handler(PipeSafeHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                body = b'{"result":{"item":{"path":"part.gcode"}}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)
        client = self.qt.load("MoonrakerClient").MoonrakerClient()
        client.configure(url, "", 750)
        self.addCleanup(client.stop)
        config = self.config_type(url=url, upload_dialog=False, upload_start_print=False)
        module = self.qt.load("MoonrakerOutputDevice")
        device = module.MoonrakerOutputDevice(self.app, "A", client=client, config=lambda: config,
            apply_config=lambda value: None, active_identity=lambda: ("A", "A"))
        self.addCleanup(device.deactivate)
        terminal, reentrant = [], []
        def success(_device):
            try: device.requestWrite(None)
            except module.OutputDeviceError.DeviceBusyError: reentrant.append("blocked")
        device.writeSuccess.connect(success)
        device.writeFinished.connect(terminal.append)
        device.requestWrite(None)
        for _ in range(200):
            if terminal: break
            self.qt.events(10)
        self.assertEqual(terminal, [device])
        self.assertEqual(reentrant, ["blocked"])
        self.assertEqual(len(received), 1)
        self.assertIn(b"G1 X0", received[0])
        self.assertIn(b'name="root"', received[0])
        self.assertFalse(device._upload.busy)

    def test_real_download_index_and_cura_load_pipeline(self):
        content = (pathlib.Path(__file__).parent / "fixtures" / "gcode" / "cura.gcode").read_bytes()
        status = self.status(layer=2)
        status["virtual_sdcard"]["file_size"] = len(content)
        class Handler(PipeSafeHandler):
            def do_GET(self):
                if self.path.startswith("/server/files/gcodes/"):
                    body = content
                elif self.path.startswith("/server/files/metadata"):
                    body = json.dumps({"result": {"size": len(content), "modified": 1}}).encode()
                else:
                    body = json.dumps({"result": {"status": status}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        app = self.qt.Application()
        follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.addCleanup(follower.deinitialize)
        follower.apply_printer_config(self.config_type(url="http://127.0.0.1:" + str(server.server_port), enabled=True, path_follow=True, feed_mode="http"))
        parts = follower._runtime
        # An active-but-unloaded print pulls nothing: the metadata and
        # index serve the Preview, which needs the print loaded in Cura.
        parts.coordinator.request_load()
        for _ in range(100):
            if app.loaded_paths: break
            self.qt.events(10)
        self.assertEqual(app.loaded_paths, [parts.files.path])
        self.assertTrue(parts.cura.loading)
        # The load gives Cura the toolpath; only then does the
        # metadata/index pull start and the index build.
        app.controller.view = SimpleNamespace(getActivity=lambda: True)
        app.controller.activeViewChanged.emit()
        for _ in range(300):
            if parts.index.view is not None: break
            self.qt.events(10)
        self.assertIsNotNone(parts.index.view)
        self.assertEqual(len(parts.index.view.ranges), 3)
        self.assertEqual(pathlib.Path(parts.files.path).read_bytes(), content)
        app.fileCompleted.emit(parts.files.path)
        self.assertFalse(parts.cura.loading)

    def test_mesh_observation_flows_through_coordinator_not_monitor(self):
        model = self.monitor()
        status = self.status()
        status["bed_mesh"] = {"mesh_matrix": [[0, 1], [2, 3]], "mesh_min": [0, 0], "mesh_max": [10, 10]}
        self.deliver(status)
        self.assertTrue(self.follower.bed_mesh.snapshot)
        # Monitor deactivation must not touch the shared mesh observation;
        # the coordinator is its only writer.
        model.setMonitoringActive(False)
        model._controls.observe()
        self.assertTrue(self.follower.bed_mesh.snapshot)
        self.follower.client.stop()
        self.assertFalse(self.follower.bed_mesh.snapshot)

    def test_file_manager_paging_with_resident_data(self):
        # The author's live report: the page carousel stopped. This
        # exercises the REAL model end to end — walk, publish, page
        # slice — with 30 resident files.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": f"f{i:02d}.gcode", "modified": 10.0, "size": 100}
                              for i in range(30)],
                    "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()

        def rows():
            value = model.fileManagerRows
            return value.value() if hasattr(value, "value") else list(value)
        self.assertEqual(len(rows()), 25)
        self.assertEqual(rows()[0]["name"], "f00.gcode")
        # No print_start_time in the metadata means the file has
        # genuinely never printed — the status says so even while
        # the history window is only partially loaded (the author's
        # live ruling).
        self.assertEqual(rows()[0]["status"], "Never printed")
        self.assertEqual(model.fileManagerPageCount, 2)
        model.setFilePage(2)
        self.assertEqual(model.fileManagerPageIndex, 2)
        self.assertEqual(len(rows()), 5)
        self.assertEqual(rows()[0]["name"], "f25.gcode")

    def test_file_manager_print_confirmation_flow(self):
        # Snapshot 2: the confirmation carries the row's payload and
        # the printer's name; confirming POSTs the root-exclusive
        # print/start; the print_stats transition is the success (the
        # POST reply is never it — round-2 D4).
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "benchy.gcode", "modified": 10.0, "size": 100,
                               "estimated_time": 6120.0, "filament_total": 12340.0}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileRequestPrint("benchy.gcode")
        confirm = model.filePrintConfirm
        if hasattr(confirm, "value"):
            confirm = confirm.value()
        self.assertEqual(confirm["name"], "benchy.gcode")
        self.assertTrue(confirm["printerName"])
        # The fixture's mock carries no server state and no homing:
        # the readiness line must say so (the author's live report —
        # an unhomed printer failed silently).
        self.assertIn("readyText", confirm)
        self.assertFalse(confirm["homed"])
        self.assertTrue(confirm["readyText"])
        model.fileConfirmPrint()
        self.assertEqual(model.filePrintConfirm, "")
        # The confirm dismisses the popup immediately — never a wait
        # on the watchdog or the transition (the author's ruling).
        self.assertFalse(model.fileManagerOpen)
        posts = [r for r in self.transport.requests if "print/start" in r.path]
        self.assertEqual(len(posts), 1)
        # The composed fixture's binding carries a base URL: the full
        # form (the service test pins the relative variant).
        self.assertEqual(posts[0].path, "http://printer-a/printer/print/start?filename=benchy.gcode")
        # The watchdog: a start that never transitions explains
        # itself in the console, and the verdict NEVER rewrites the
        # observed printer state (a live print must not read
        # "cancelled" — the e-stop alone owns that assumption).
        module = self.qt.load("MoonrakerMonitorModel")
        with patch.object(module.MoonrakerMonitorModel, "FILE_PRINT_START_TIMEOUT_S", 0.0):
            model._publish()
        self.assertIsNone(model._file_manager.print_attempt)
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("Print start failed" in line for line in lines))
        self.assertIn("Print start failed", model.actionStatus)
        self.assertFalse(self.follower.client._session.state.assume_print_stopped)
        # The transition itself is the success (round-2 D4: the POST
        # reply is never it): the filename match with a live state
        # clears immediately, even with zero progress — the
        # pre-extrusion window pins print_duration at 0.0.
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "benchy.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileRequestPrint("benchy.gcode")
        model.fileConfirmPrint()
        self.assertIsNotNone(model._file_manager.print_attempt)
        self.deliver(self.status(filename="benchy.gcode", state="printing", position=0))
        self.qt.events()
        self.assertIsNone(model._file_manager.print_attempt)
        # The print is live: the popup steps aside (the author's
        # live request — the monitor view returns).
        self.assertFalse(model.fileManagerOpen)
        # The host's own error verdict surfaces with its words.
        # The printer stands by first — a confirm against a stale
        # "printing" snapshot would honestly read as success.
        self.deliver(self.status(filename="other.gcode", state="standby", position=0))
        self.qt.events()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "benchy.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileRequestPrint("benchy.gcode")
        model.fileConfirmPrint()
        self.assertIsNotNone(model._file_manager.print_attempt)
        status = self.status(filename="benchy.gcode", state="error", position=0)
        status["print_stats"]["message"] = "Not homed"
        self.deliver(status)
        self.qt.events()
        self.assertIsNone(model._file_manager.print_attempt)
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("Not homed" in line for line in lines))

    def test_file_request_print_pulls_the_rows_thumbnail(self):
        # The confirmation's large thumbnail (the author's live
        # request): the page-driven cache covers visible rows only,
        # so opening the dialog for an OFF-PAGE row (the Recents
        # case) must fetch that row's thumbnail explicitly.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                files = [{"filename": f"f{i:02d}.gcode", "modified": 10.0, "size": 100}
                         for i in range(30)]
                # Oldest sorts last: page 2, outside the page cache.
                files.append({"filename": "benchy.gcode", "modified": 1.0, "size": 100,
                              "thumbnails": [{"width": 300, "height": 300,
                                              "relative_path": ".thumbs/benchy-300x300.png"}]})
                request.callback({"result": {
                    "files": files, "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        with patch.object(model._file_manager, "_fetch_thumb") as fetch:
            model.fileRequestPrint("benchy.gcode")
            fetch.assert_called_once()
            # The dialog asks for the LARGE variant (the grid cells
            # fetch the small one).
            self.assertEqual(fetch.call_args.args, (
                "benchy.gcode", "gcodes", ".thumbs/benchy-300x300.png", True))

    def test_file_delete_and_rename_round_trip_through_the_model(self):
        # Snapshot 3: selection → delete confirmation → DELETE at the
        # root-inclusive endpoint; rename request → live collision →
        # overwrite confirm → move with both parts in the body.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100},
                              {"filename": "b.gcode", "modified": 9.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.toggleFileSelection("a.gcode")
        model.fileRequestDelete()
        confirm = model.fileDeleteConfirm
        if hasattr(confirm, "value"):
            confirm = confirm.value()
        self.assertEqual(confirm["count"], 1)
        self.assertEqual(confirm["first"], "a.gcode")
        self.assertEqual(confirm["blocked"], 0)
        model.fileConfirmDelete()
        deletes = [r for r in self.transport.requests if r.method == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0].path, "http://printer-a/server/files/gcodes/a.gcode")
        model.fileRequestRename("b.gcode")
        model.filePreviewRename("a.gcode")  # collides with the resident row
        self.assertTrue(model.fileRenameConflict)
        model.fileConfirmRename()
        moves = [r for r in self.transport.requests if "server/files/move" in r.path]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].options["body"],
                         {"source": "gcodes/b.gcode", "dest": "gcodes/a.gcode"})

    def test_file_upload_collision_asks_before_overwriting(self):
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileUpload("/tmp/a.gcode")
        confirm = model.fileUploadConfirm
        if hasattr(confirm, "value"):
            confirm = confirm.value()
        self.assertEqual(confirm["filename"], "a.gcode")
        self.assertEqual(confirm["path"], "/tmp/a.gcode")
        model.fileConfirmUpload()
        self.assertEqual(model.fileUploadConfirm, "")
        # The overwrite upload ran; under the scripted transport (no
        # raw network) it refuses gracefully — the real-socket test
        # proves the multipart itself.
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("Upload refused" in line for line in lines))

    def test_file_upload_progress_flow_through_the_popup(self):
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [], "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        with patch.object(model._file_manager, "upload_file", return_value=True):
            model.fileUpload("/tmp/bench.gcode")
        progress = model.fileUploadProgress
        if hasattr(progress, "value"):
            progress = progress.value()
        self.assertEqual(progress["name"], "bench.gcode")
        self.assertEqual(progress["state"], "uploading")
        self.assertEqual(progress["percent"], 0)
        # The service signals drive the transitions (the scripted
        # transport cannot run the multipart — emit as the service
        # would; the real-socket test proves the signals' source).
        model._file_manager.uploadProgress.emit(42)
        model._file_manager.uploadFinished.emit(True, "bench.gcode")
        progress = model.fileUploadProgress
        if hasattr(progress, "value"):
            progress = progress.value()
        self.assertEqual(progress["state"], "done")
        self.assertEqual(progress["percent"], 100)
        model.fileUploadDismiss()
        self.assertEqual(model.fileUploadProgress, "")

    def test_cleared_payloads_read_as_absent(self):
        # The adversarial round's live repro: closing the popup
        # clears the payloads to "" while a surviving dialog stays
        # painted over the dashboard — its buttons used to raise
        # TypeError inside a swallowed Qt slot. Falsy payloads must
        # read as absent on every path.
        model = self.monitor()
        model.openFileManager()
        model.setFileManagerOpen(False)
        self.assertEqual(model.filePrintConfirm, "")
        with patch.object(model._file_manager, "start_print") as start_print:
            model.fileConfirmPrint()
            start_print.assert_not_called()
        self.assertEqual(model.fileUploadProgress, "")
        model._on_upload_progress(40)  # must not raise
        model._on_upload_finished(True, "bench.gcode")  # must not raise
        self.assertEqual(model.fileUploadProgress, "")

    def test_watchdog_holds_through_a_same_file_reprint(self):
        # The adversarial round's repro: Klipper never clears the
        # filename, so a re-print arms against the previous job's
        # stale terminal state. The verdict is the state CHANGE,
        # never the filename alone.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [],
                }}, None)
                break
        self.qt.events()
        self.deliver(self.status(filename="a.gcode", state="complete"))
        self.qt.events()
        model.fileRequestPrint("a.gcode")
        model.fileConfirmPrint()
        self.assertIsNotNone(model._file_manager.print_attempt)
        # The unchanged stale state holds the attempt.
        model._publish()
        self.assertIsNotNone(model._file_manager.print_attempt)
        # The transition to a live state is the success.
        self.deliver(self.status(filename="a.gcode", state="printing"))
        model._publish()
        self.assertIsNone(model._file_manager.print_attempt)

    def test_manual_reconnect_cycles_the_client(self):
        # The author's live request: a Reconnect that recovers a UI
        # stuck after a printer error — the client cycle runs even
        # when disconnected (unlike the e-stop's connected-only
        # auto-recovery).
        model = self.monitor()
        with patch.object(model._data._client, "stop") as stop, \
             patch.object(model._data._client, "start") as start:
            model.reconnect()
        stop.assert_called_once()
        start.assert_called_once()
        self.assertIn("Reconnecting", model.actionStatus)

    def test_upload_refuses_the_printing_file_and_disk_shortfall(self):
        # The host streams the printing file from disk: replacing it
        # mid-print truncates the running job, so the upload refuses
        # outright. The disk guard refuses before the far end fails.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        self.deliver(self.status(filename="a.gcode", state="printing"))
        self.qt.events()
        model.fileUpload("/tmp/a.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("currently printing" in line for line in lines))
        with patch("os.path.getsize", return_value=400 * 1048576):
            model.fileUpload("/tmp/big.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("MB free" in line for line in lines))
        # A REAL zero (full disk) refuses; only a missing report
        # (None) stands the check down (the adversarial round's
        # catch: the old guard treated the two alike).
        model._file_manager.disk_usage["free"] = 0
        with patch("os.path.getsize", return_value=1048576):
            model.fileUpload("/tmp/one-mb.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("MB free" in line for line in lines))
        model._file_manager.disk_usage.clear()
        with patch.object(model, "_start_upload") as start:
            model.fileUpload("/tmp/unknown-disk.gcode")
        start.assert_called_once()  # no report → the guard stands down

    def test_file_non_gcode_upload_refuses_without_a_prompt(self):
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [], "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileUpload("/tmp/thing.stl")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("only gcode files" in line for line in lines))

    def test_file_manager_open_flag_round_trips_through_the_model(self):
        # The author's live report: the File-manager button stopped
        # opening the popup once the flag moved into the model. The
        # flag must publish, read back, AND NOTIFY — the QML binding
        # re-evaluates on the signal, and a Python-only read passes
        # even when the notify never fires (the second report's
        # exact hole: the flag sat outside the signal group).
        model = self.monitor()
        self.assertFalse(model.fileManagerOpen)
        fired = []
        model.fileManagerChanged.connect(lambda: fired.append(True))
        model.setFileManagerOpen(True)
        self.assertTrue(model.fileManagerOpen)
        self.assertEqual(fired, [True])
        # A full publish rebuild must not lose the flag.
        model._publish()
        self.assertTrue(model.fileManagerOpen)
        model.setFileManagerOpen(False)
        self.assertFalse(model.fileManagerOpen)
        self.assertEqual(fired, [True, True])

    def test_file_manager_view_mutations_republish_immediately(self):
        # The author's live report: ticking a filter changed nothing
        # and the page carousel advanced one step then stopped — the
        # slots mutated the view dataclass without re-publishing, so
        # nothing re-rendered until an unrelated signal did. Every
        # view mutation must publish on its own.
        model = self.monitor()
        model.setFileSort("size")
        self.assertEqual(model.fileManagerSortColumn, "size")
        model.setFileSearch("benchy")
        self.assertEqual(model.fileManagerSearch, "benchy")
        model.setFilePageSize("all")
        self.assertEqual(model.fileManagerPageSize, "all")
        # No walk data in this fixture: the page index clamps to 1
        # (never an empty page), but the mutation must still publish.
        model.setFilePage(2)
        self.assertEqual(model.fileManagerPageIndex, 1)
        model.setFileFilter("slicer", ["Cura 5.9"])
        self.assertEqual(model.fileManagerFilters, {"slicer": ["Cura 5.9"]})
        self.assertEqual(model.fileManagerFilterCounts, {"slicer": 1})
        # Single-value categories publish as one-element lists (the
        # QML's checked bindings) but filter as scalars.
        model.setFileFilter("modified", ["7d"])
        self.assertEqual(model.fileManagerFilters, {"slicer": ["Cura 5.9"], "modified": ["7d"]})
        self.assertEqual(model.fileManagerFilterCounts, {"slicer": 1, "modified": 1})
        model.setFileFilter("modified", [])
        self.assertEqual(model.fileManagerFilters, {"slicer": ["Cura 5.9"]})
        model.clearFileFilters()
        self.assertEqual(model.fileManagerFilters, {})

    def test_leave_monitor_stage_chooses_preview_or_prepare(self):
        # The author's live request: Esc on the Monitor page goes to
        # the Preview stage when anything is sliced, Prepare
        # otherwise.
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        device = output._current
        device.leaveMonitorStage()
        self.assertEqual(self.app.controller.stage, "PrepareStage")
        device._has_slice = lambda: True
        device.leaveMonitorStage()
        self.assertEqual(self.app.controller.stage, "PreviewStage")

    def test_qml_public_api_is_present_without_model_subclasses(self):
        model = self.monitor()
        properties = "monitorState monitorConnected connectionDetail monitorFilename monitorProgress monitorLayer monitorElapsed monitorEta monitorFinish monitorSpeed monitorFlow monitorPosition monitorMessage printActive canPausePrint canResumePrint canCancelPrint actionBusy actionStatus temperatureItems fanItems filamentSensorItems excludeObjectItems powerDevices klippyState moonrakerVersion klipperVersion hostLoad memoryAvailable cpuTemperature mcuSummary mcuItems webcamNames activeWebcamIndex cameraName cameraRotation cameraFlipHorizontal cameraFlipVertical monitorLayerHeight macroNames hasQuadGantryLevel hasBedMesh canRunSetup temperaturePresetNames temperaturePresetItems canApplyTemperaturePreset speedFactorPercent flowFactorPercent zOffset zOffsetText fanControlItems ledItems pwmOutputItems saveConfigPending saveConfigSummary canSaveConfig emergencyStopClicks bedMeshAvailable bedMeshProfile bedMeshProfileNames bedMeshRows bedMeshColumns bedMeshValues bedMeshMinimum bedMeshMaximum bedMeshRange bedMeshXMin bedMeshXMax bedMeshYMin bedMeshYMax bedMeshRangeText bedMeshPreviewVisible jogEnabled jogDistance extrudeDistance extrudeSpeed homedAxes positionMode jogStatus controlsLocked controlsCollapsed infoCollapsed statusCollapsed consoleHeight cameraRefreshNonce cameraRecovering emergencyHoldProgress temperatureChart temperatureChartLegend consoleHistory consolePending consoleErrorBell endstopItems endstopSummary monitorEtaBasis showProbePoints fileManagerRows fileManagerRecents fileManagerDirectory fileManagerDirectories fileManagerDiskText fileManagerRefreshedAt fileManagerShown fileManagerPage fileManagerPageIndex fileManagerPageCount fileManagerPageSize fileManagerPageSelection fileManagerEmptyKind fileManagerSelected fileManagerSortColumn fileManagerSortAscending fileManagerSearch fileManagerOpen fileManagerFilters filePrintConfirm fileDeleteConfirm fileRenameTarget fileRenameConflict fileUploadConfirm fileUploadProgress fileManagerThumbs fileManagerFilterCounts fileManagerFilterOptions fileManagerHistoryLoaded fileManagerHistoryExhausted fileManagerWalkError fileManagerNote".split()
        meta = model.metaObject()
        for name in properties: self.assertGreaterEqual(meta.indexOfProperty(name), 0, name)
        for name in "pausePrint resumePrint cancelPrint reconnect refreshAll refreshWebcams selectWebcam runMacro homeAll runQuadGantryLevel calibrateBedMesh applyTemperaturePreset setSpeedFactor setFlowFactor adjustZOffset clearZOffset setFanSpeed setLedBrightness setLedColor setPwmOutput saveConfig emergencyStopClick emergencyHoldStarted emergencyHoldReleased loadBedMeshProfile clearBedMesh setBedMeshPreviewVisible macroParameterDefinitions jog setJogDistance setExtrudeDistance setExtrudeSpeed home motorsOff centerToolhead zToZero extrude heatersOff firmwareRestart klipperRestart hostRestart setControlsLocked setControlsCollapsed setInfoCollapsed setStatusCollapsed setConsoleHeight setTemperatureSensorVisible setTemperatureSensorColor setShowTemperatureTargets setShowTemperaturePower sendConsoleCommand clearConsoleHistory improveEta setShowProbePoints openFileManager refreshFileManager fileNavigateTo setFileSearch setFileSort setFileManagerOpen setPositionMode setFilePageSize setFilePage setFileFilter clearFileFilters toggleFileSelection toggleFilePageSelection clearFileSelection fileLoadAllHistory fileScanMetadata fileRequestDelete fileRequestDeleteFile fileRequestDeleteDir fileCreateDirectory fileConfirmDelete fileCancelDelete fileRequestRename fileRequestRenameDir filePreviewRename fileConfirmRename fileCancelRename fileUpload fileConfirmUpload fileCancelUpload fileUploadDismiss fileClearWalkError fileRequestVisibleThumbnails".split():
            self.assertTrue(any(bytes(meta.method(i).name()).decode() == name for i in range(meta.methodCount())), name)
        self.assertEqual(type(model).__bases__[0].__name__, "PrinterModel")


if __name__ == "__main__": unittest.main()




