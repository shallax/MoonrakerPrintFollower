"""Executable contracts for the completed component boundaries."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import pathlib
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, ScriptedTransport, runtime


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
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.parts = self.follower._runtime
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False))

    def status(self, *, layer=10, filename="part.gcode", duration=30, position=40, state="printing"):
        return {"print_stats": {"filename": filename, "state": state, "print_duration": duration,
                "info": {"current_layer": layer, "total_layer": 50}},
            "virtual_sdcard": {"file_size": 100, "file_position": position},
            "gcode_move": {"gcode_position": [1, 1, 2, 10], "speed_factor": 1, "extrude_factor": 1}}

    def deliver(self, status):
        client = self.follower.client
        client._handle_http_status({"result": {"status": status}}, None, client._generation)

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def test_facade_has_no_legacy_private_state_or_mixin_bases(self):
        for name in ("_remote_job_service", "_preview_follower_service", "_simulation_view", "_apply_path_progress", "_config_store"):
            self.assertFalse(hasattr(self.follower, name), name)
        self.assertFalse(any("Mixin" in cls.__name__ for cls in type(self.follower).__mro__))

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
        files = self.parts.files
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
        files._identity = self.qt.load("Core").RemoteFileIdentity("part.gcode", 100, modified=1)
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

    def test_emergency_stop_bypasses_busy_command_but_requires_three_clicks(self):
        model = self.monitor()
        model._commands._busy = True
        model.emergencyStopClick()
        model.emergencyStopClick()
        self.assertFalse(any(r.channel == "emergency-stop" for r in self.transport.requests))
        model.emergencyStopClick()
        self.assertEqual(sum(r.channel == "emergency-stop" for r in self.transport.requests), 1)

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
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                body = b'{"result":{"item":{"path":"part.gcode"}}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
        class Handler(BaseHTTPRequestHandler):
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
        follower.apply_printer_config(self.config_type(url="http://127.0.0.1:" + str(server.server_port), enabled=True, path_follow=True))
        parts = follower._runtime
        for _ in range(300):
            if parts.index.view is not None: break
            self.qt.events(10)
        self.assertIsNotNone(parts.index.view)
        self.assertEqual(len(parts.index.view.ranges), 3)
        self.assertEqual(pathlib.Path(parts.files.path).read_bytes(), content)
        parts.coordinator.request_load()
        for _ in range(100):
            if app.loaded_paths: break
            self.qt.events(10)
        self.assertEqual(app.loaded_paths, [parts.files.path])
        self.assertTrue(parts.cura.loading)
        app.fileCompleted.emit(parts.files.path)
        self.assertFalse(parts.cura.loading)

    def test_only_active_monitor_can_update_shared_mesh(self):
        model = self.monitor()
        self.deliver(self.status())
        model._data._update(auxiliary={"bed_mesh": {"mesh_matrix": [[0, 1], [2, 3]], "mesh_min": [0, 0], "mesh_max": [10, 10]}})
        self.assertTrue(self.follower.bed_mesh.snapshot)
        model.setMonitoringActive(False)
        model._controls.observe()
        self.assertTrue(self.follower.bed_mesh.snapshot)
        self.follower.client.stop()
        self.assertFalse(self.follower.bed_mesh.snapshot)

    def test_qml_public_api_is_present_without_model_subclasses(self):
        model = self.monitor()
        properties = "monitorState monitorFilename monitorProgress monitorLayer monitorElapsed monitorEta monitorFinish monitorSpeed monitorFlow monitorPosition monitorMessage printActive canPausePrint canResumePrint canCancelPrint actionBusy actionStatus temperatureItems fanItems filamentSensorItems excludeObjectItems powerDevices klippyState moonrakerVersion klipperVersion hostLoad memoryAvailable cpuTemperature mcuSummary mcuItems webcamNames activeWebcamIndex cameraName cameraRotation cameraFlipHorizontal cameraFlipVertical monitorLayerHeight macroNames hasQuadGantryLevel hasBedMesh canRunSetup temperaturePresetNames temperaturePresetItems canApplyTemperaturePreset speedFactorPercent flowFactorPercent zOffset zOffsetText fanControlItems ledItems pwmOutputItems saveConfigPending saveConfigSummary canSaveConfig emergencyStopClicks bedMeshAvailable bedMeshProfile bedMeshProfileNames bedMeshRows bedMeshColumns bedMeshValues bedMeshMinimum bedMeshMaximum bedMeshRange bedMeshXMin bedMeshXMax bedMeshYMin bedMeshYMax bedMeshRangeText bedMeshPreviewVisible".split()
        meta = model.metaObject()
        for name in properties: self.assertGreaterEqual(meta.indexOfProperty(name), 0, name)
        for name in "pausePrint resumePrint cancelPrint refreshAll refreshWebcams selectWebcam runMacro homeAll runQuadGantryLevel calibrateBedMesh applyTemperaturePreset setSpeedFactor setFlowFactor adjustZOffset clearZOffset setFanSpeed setLedBrightness setLedColor setPwmOutput saveConfig emergencyStopClick loadBedMeshProfile clearBedMesh setBedMeshPreviewVisible macroParameterDefinitions".split():
            self.assertTrue(any(bytes(meta.method(i).name()).decode() == name for i in range(meta.methodCount())), name)
        self.assertEqual(type(model).__bases__[0].__name__, "PrinterModel")


if __name__ == "__main__": unittest.main()




