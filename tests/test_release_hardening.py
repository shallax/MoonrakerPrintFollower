from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
TOOLS = ROOT / "tools"
FIXTURES = ROOT / "tests" / "fixtures" / "gcode"
for path in (PLUGINS, TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from GCodeIndex import build_index_from_file
from check_qml import check_text
from build_curapackage import build
from verify_curapackage import verify

MONITOR_SOURCE = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
RUNTIME_SOURCE = (PLUGINS / "MoonrakerMonitorRuntime.py").read_text(encoding="utf-8")
CONTROLS_SOURCE = (PLUGINS / "MoonrakerMonitorControls.py").read_text(encoding="utf-8")
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text(encoding="utf-8")
CONFIG_QML = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text(encoding="utf-8")
OUTPUT_PLUGIN_SOURCE = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
TYPED_SOURCE = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text(encoding="utf-8")
DASHBOARD_SOURCE = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")


class DummySignal:
    def connect(self, *_args, **_kwargs):
        pass
    def emit(self, *_args, **_kwargs):
        pass


class DummyTimer:
    def __init__(self, *_args, **_kwargs):
        self.active = False
        self.timeout = DummySignal()
        self.interval = 0
    def setInterval(self, value):
        self.interval = value
    def setSingleShot(self, *_args):
        pass
    def start(self):
        self.active = True
    def stop(self):
        self.active = False
    def isActive(self):
        return self.active
    @staticmethod
    def singleShot(_delay, callback):
        callback()


class DummyReply:
    def __init__(self):
        self.deleted = False
        self.aborted = False
    def isRunning(self):
        return True
    def abort(self):
        self.aborted = True
    def deleteLater(self):
        self.deleted = True


def _pyqt_signal(*_args, **_kwargs):
    return DummySignal()


def _pyqt_property(*_args, **_kwargs):
    def decorate(function):
        return property(function)
    return decorate


def _pyqt_slot(*_args, **_kwargs):
    def decorate(function):
        return function
    return decorate


def load_monitor_model():
    class QByteArray(bytes):
        pass
    class QUrl:
        def __init__(self, value=""):
            self.value = value
        def isValid(self):
            return True
        def scheme(self):
            return "http"
        def host(self):
            return "host"
    class QVariant:
        def __init__(self, value=None):
            self.value = value
    class QNetworkReply:
        class NetworkError:
            NoError = 0
    class QNetworkRequest:
        class KnownHeaders:
            ContentTypeHeader = 0
    class QNetworkAccessManager:
        def __init__(self, *_args):
            pass
    class QDesktopServices:
        @staticmethod
        def openUrl(*_args):
            pass
    class PrinterOutputModel:
        def __init__(self, *_args, **_kwargs):
            pass
    class Logger:
        @staticmethod
        def log(*_args, **_kwargs):
            pass

    modules = {
        "PyQt6": types.ModuleType("PyQt6"),
        "PyQt6.QtCore": types.ModuleType("PyQt6.QtCore"),
        "PyQt6.QtGui": types.ModuleType("PyQt6.QtGui"),
        "PyQt6.QtNetwork": types.ModuleType("PyQt6.QtNetwork"),
        "cura": types.ModuleType("cura"),
        "cura.PrinterOutput": types.ModuleType("cura.PrinterOutput"),
        "cura.PrinterOutput.Models": types.ModuleType("cura.PrinterOutput.Models"),
        "cura.PrinterOutput.Models.PrinterOutputModel": types.ModuleType("cura.PrinterOutput.Models.PrinterOutputModel"),
        "UM": types.ModuleType("UM"),
        "UM.Logger": types.ModuleType("UM.Logger"),
        "plugins": types.ModuleType("plugins"),
        "plugins.MoonrakerProtocol": types.ModuleType("plugins.MoonrakerProtocol"),
    }
    modules["plugins"].__path__ = []
    core = modules["PyQt6.QtCore"]
    core.QByteArray = QByteArray; core.QTimer = DummyTimer; core.QUrl = QUrl; core.QVariant = QVariant
    core.pyqtProperty = _pyqt_property; core.pyqtSignal = _pyqt_signal; core.pyqtSlot = _pyqt_slot
    modules["PyQt6.QtGui"].QDesktopServices = QDesktopServices
    network = modules["PyQt6.QtNetwork"]
    network.QNetworkAccessManager = QNetworkAccessManager; network.QNetworkReply = QNetworkReply; network.QNetworkRequest = QNetworkRequest
    modules["cura.PrinterOutput.Models.PrinterOutputModel"].PrinterOutputModel = PrinterOutputModel
    modules["UM.Logger"].Logger = Logger
    modules["plugins.MoonrakerProtocol"].status_endpoint = lambda base: base + "/printer/objects/query"

    old = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        namespace = {"__name__": "plugins.MoonrakerMonitorModel", "__package__": "plugins"}
        exec(compile(MONITOR_SOURCE, "MoonrakerMonitorModel.py", "exec"), namespace)
        return namespace["MoonrakerMonitorModel"]
    finally:
        for name, value in old.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def load_typed_model():
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")
    class QVariant:
        def __init__(self, value=None): self.value = value
    qtcore.QVariant = QVariant
    qtcore.pyqtProperty = _pyqt_property; qtcore.pyqtSignal = _pyqt_signal; qtcore.pyqtSlot = _pyqt_slot
    package = types.ModuleType("plugins"); package.__path__ = []
    base = types.ModuleType("plugins.MoonrakerMonitorControls")
    base.MoonrakerMonitorModel = type("Base", (), {"_want_aux_object": staticmethod(lambda _name: False)})
    modules = {"PyQt6": pyqt6, "PyQt6.QtCore": qtcore, "plugins": package, "plugins.MoonrakerMonitorControls": base}
    old = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        namespace = {"__name__": "plugins.MoonrakerMonitorTypedControls", "__package__": "plugins"}
        exec(compile(TYPED_SOURCE, "MoonrakerMonitorTypedControls.py", "exec"), namespace)
        return namespace["MoonrakerMonitorModel"]
    finally:
        for name, value in old.items():
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value


class ReleaseHardeningTests(unittest.TestCase):
    def test_all_qml_pass_structural_checker(self):
        failures = []
        for path in sorted(PLUGINS.glob("*.qml")):
            failures.extend(check_text(path.read_text(encoding="utf-8"), path.name))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_qml_checker_rejects_duplicate_property_and_unbalanced_brace(self):
        bad = "import QtQuick 2.15\nItem { width: 1; width: 2\n"
        failures = check_text(bad, "bad.qml")
        self.assertTrue(any("duplicate property 'width'" in item for item in failures))
        self.assertTrue(any("unclosed '{'" in item for item in failures))

    def test_repository_has_no_tracked_python_cache_or_legacy_dashboard(self):
        tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        debris = [name for name in tracked if "__pycache__" in name or name.endswith((".pyc", ".pyo"))]
        self.assertEqual(debris, [])
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("__pycache__/", gitignore)
        self.assertIn("*.py[cod]", gitignore)

    def test_package_is_exact_byte_for_byte_source_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "candidate.curapackage"
            build(package)
            verify(package)

    def test_cura_orca_prusa_and_variable_layer_fixtures(self):
        cura = build_index_from_file(str(FIXTURES / "cura.gcode"), compact=False)
        self.assertEqual(cura.layer_count(), 3)
        self.assertEqual(cura.current_layer_map, {1: 0, 2: 1, 3: 2})
        self.assertEqual(cura.layer_elapsed_times, [120.0, 420.0, 900.0])
        orca = build_index_from_file(str(FIXTURES / "orca.gcode"), compact=False)
        self.assertEqual(orca.current_layer_map, {1: 0, 2: 1, 3: 2})
        prusa = build_index_from_file(str(FIXTURES / "prusa.gcode"), compact=False)
        self.assertEqual(prusa.layer_count(), 3)
        variable = build_index_from_file(str(FIXTURES / "variable_layers.gcode"), compact=False)
        self.assertEqual(variable.layer_elapsed_times, [10.0, 22.0, 45.0])

    def test_pause_missing_time_and_resume_fixtures_remain_indexable(self):
        paused = build_index_from_file(str(FIXTURES / "pause.gcode"), compact=False)
        self.assertEqual(paused.layer_count(), 3)
        self.assertEqual(paused.motion_count(1), 2)
        missing = build_index_from_file(str(FIXTURES / "missing_time.gcode"), compact=False)
        self.assertEqual(missing.layer_elapsed_times, [None, None])
        resumed = build_index_from_file(str(FIXTURES / "resume.gcode"), compact=False)
        self.assertEqual(resumed.current_layer_map, {1: 0, 2: 1, 3: 2, 4: 3})

    def test_eta_prefers_slicer_time_for_early_and_resumed_prints(self):
        model = load_monitor_model()
        early = model._estimate_remaining_seconds(3600, 0.02, 7 * 3600, True)
        self.assertAlmostEqual(early, 6 * 3600, delta=1)
        resumed = model._estimate_remaining_seconds(3 * 3600, 0.10, 7 * 3600, True)
        self.assertAlmostEqual(resumed, 4 * 3600, delta=1)
        waiting = model._estimate_remaining_seconds(120, 0.50, None, False)
        self.assertIsNone(waiting)

    def test_malformed_core_status_degrades_without_throwing(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        instance._monitoring_active = True
        instance._monitor_state = "Not connected"; instance._monitor_state_raw = ""
        instance._monitor_filename = ""; instance._monitor_message = ""; instance._monitor_progress = 0
        instance._monitor_progress_fraction = 0.0; instance._monitor_layer = "—"; instance._monitor_elapsed = "00:00:00"
        instance._monitor_eta = "—"; instance._monitor_finish = "—"; instance._monitor_speed = "100%"; instance._monitor_flow = "100%"
        instance._monitor_position = "—"; instance._print_duration = 0.0; instance._metadata_estimated_time = None
        instance._metadata_filename = ""; instance._metadata_lookup_complete = True; instance._power_devices_raw = []
        instance.updateMoonrakerStatus({"print_stats": "bad", "virtual_sdcard": [], "gcode_move": 7, "motion_report": None})
        self.assertEqual(instance.monitorPosition, "—")
        self.assertEqual(instance.monitorProgress, 0)

    def test_malformed_bed_mesh_and_mcu_payloads_are_rejected_or_degraded(self):
        model = load_typed_model()
        self.assertEqual(model._parse_bed_mesh_status(None), {})
        self.assertEqual(model._parse_bed_mesh_status({"mesh_matrix": [[0, 1], [2]], "mesh_min": [0, 0], "mesh_max": [1, 1]}), {})
        self.assertEqual(model._parse_bed_mesh_status({"mesh_matrix": [[0, float("nan")], [1, 2]], "mesh_min": [0, 0], "mesh_max": [1, 1]}), {})
        self.assertEqual(model._parse_bed_mesh_status({"mesh_matrix": [[0, 1], [1, 2]], "mesh_min": [2, 0], "mesh_max": [1, 1]}), {})
        stats = model._parse_mcu_last_stats("mcu_awake=0.02 nonsense bytes_write=abc bytes_read=123")
        self.assertEqual(stats, {"mcu_awake": 0.02, "bytes_read": 123.0})

    def test_stale_monitor_request_generation_is_ignored(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        reply = DummyReply()
        instance._requests = {"aux": reply}
        instance._request_generation = 4
        called = []
        instance._finish_json_request("aux", reply, lambda *_args: called.append(True), 3)
        self.assertEqual(called, [])
        self.assertTrue(reply.deleted)
        self.assertNotIn("aux", instance._requests)

    def test_inactive_monitor_ignores_shared_follower_status(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        instance._monitoring_active = False
        instance.updateMoonrakerStatus({"print_stats": {"state": "printing"}})
        self.assertFalse(hasattr(instance, "_monitor_state_raw"))

    def test_monitor_lifecycle_deactivates_old_printer_and_invalidates_requests(self):
        self.assertIn("self._request_generation", MONITOR_SOURCE)
        self.assertIn("generation != self._request_generation", MONITOR_SOURCE)
        self.assertIn("def setMonitoringActive", MONITOR_SOURCE)
        self.assertIn("if not self._monitoring_active", MONITOR_SOURCE)
        self.assertIn("self._set_monitor_active(self._current, False)", OUTPUT_PLUGIN_SOURCE)
        self.assertIn("self._set_monitor_active(device, True)", OUTPUT_PLUGIN_SOURCE)

    def test_deferred_slider_labels_follow_thumb_and_debounce_before_apply(self):
        self.assertGreaterEqual(DASHBOARD_SOURCE.count("live: false"), 9)
        self.assertIn("slider.valueAt(slider.position)", DASHBOARD_SOURCE)
        self.assertGreaterEqual(DASHBOARD_SOURCE.count("onMoved:"), 9)
        self.assertIn("previewSpeedFactor", DASHBOARD_SOURCE)
        self.assertIn("previewFlowFactor", DASHBOARD_SOURCE)
        self.assertIn("previewFanSpeed", DASHBOARD_SOURCE)
        self.assertIn("previewLedBrightness", DASHBOARD_SOURCE)
        self.assertIn("previewLedColor", DASHBOARD_SOURCE)
        self.assertIn("previewPwmOutput", DASHBOARD_SOURCE)
        self.assertIn("SLIDER_DEBOUNCE_MS = 2000", CONTROLS_SOURCE)
        self.assertIn("timer.start()  # restarting an active single-shot timer resets the full 2 s debounce", CONTROLS_SOURCE)
        self.assertIn("def _slider_value_from_poll", CONTROLS_SOURCE)
        self.assertIn('self._slider_value_from_poll("speed-factor", actual_speed)', CONTROLS_SOURCE)
        self.assertIn('self._slider_value_from_poll("flow-factor", actual_flow)', CONTROLS_SOURCE)
        self.assertIn('self._slider_value_from_poll("fan:" + object_name, actual_percent)', CONTROLS_SOURCE)
        self.assertIn('self._slider_value_from_poll("led-brightness:" + object_name, actual_brightness)', CONTROLS_SOURCE)
        self.assertIn('self._slider_value_from_poll("led-colour:" + object_name, actual_colour)', CONTROLS_SOURCE)
        self.assertIn('self._slider_value_from_poll("pwm-output:" + str(object_name), actual_percent)', TYPED_SOURCE)
        self.assertIn("self._clear_all_slider_pending(emit=False)", CONTROLS_SOURCE)
        for slider in ("speedSlider", "flowSlider", "fanSlider", "ledSlider", "redSlider", "greenSlider", "blueSlider", "whiteSlider", "pwmSlider"):
            self.assertIn(f"root.sliderSelection({slider})", DASHBOARD_SOURCE)


def test_core_status_uses_one_subclass_hook_instead_of_reprocessing_three_times(self):
    self.assertIn("self._after_core_status(status)", MONITOR_SOURCE)
    self.assertIn("def _after_core_status", RUNTIME_SOURCE)
    self.assertIn("def _after_core_status", CONTROLS_SOURCE)
    self.assertNotIn("def updateMoonrakerStatus", RUNTIME_SOURCE)
    self.assertNotIn("def updateMoonrakerStatus", CONTROLS_SOURCE)
    self.assertIn("self._resolved_current_layer", RUNTIME_SOURCE)

def test_full_klipper_config_is_not_polled_every_second(self):
    self.assertIn('["save_config_pending", "save_config_pending_items"]', MONITOR_SOURCE)
    self.assertIn('"config-static"', MONITOR_SOURCE)
    self.assertIn('name: self._aux_query_fields(name)', MONITOR_SOURCE)
    model = load_monitor_model()
    merged = model._merge_aux_status(
        {"configfile": {"config": {"gcode_macro TEST": {"gcode": "G28"}}, "save_config_pending": False}},
        {"configfile": {"save_config_pending": True, "save_config_pending_items": {"bed_mesh": {}}}},
    )
    self.assertIn("config", merged["configfile"])
    self.assertTrue(merged["configfile"]["save_config_pending"])

def test_request_identity_change_aborts_old_replies(self):
    model = load_monitor_model()
    instance = model.__new__(model)
    reply = DummyReply()
    instance._requests = {"aux": reply}
    instance._request_generation = 7
    instance._request_identity = ("http://old.invalid", "old-key")
    class Config:
        url = "http://new.invalid"
        api_key = str("new-key")
    class Follower:
        @staticmethod
        def current_printer_config():
            return Config()
    instance._follower = Follower()
    instance._ensure_request_session()
    self.assertEqual(instance._request_generation, 8)
    self.assertEqual(instance._request_identity, ("http://new.invalid", "new-key"))
    self.assertTrue(reply.aborted)
    self.assertTrue(reply.deleted)
    self.assertEqual(instance._requests, {})

def test_monitor_ux_release_polish_is_explicit(self):
    self.assertIn("After release, the latest value is applied once it has been unchanged for 2 seconds.", DASHBOARD_SOURCE)
    self.assertIn('text: "Refresh camera"', MONITOR_QML)
    self.assertIn("root.printer.refreshWebcams()", MONITOR_QML)
    self.assertIn('title: "Exclude object?"', MONITOR_QML)
    self.assertIn("excludeObjectDialog.open()", MONITOR_QML)
    self.assertIn('placeholderText: "<root>"', CONFIG_QML)
    self.assertIn("Leave blank to use Moonraker's gcodes root.", CONFIG_QML)
    self.assertIn("stack is None", OUTPUT_PLUGIN_SOURCE)
    self.assertIn("self._set_monitor_active(self._current, False)", OUTPUT_PLUGIN_SOURCE)

if __name__ == "__main__":
    unittest.main()
