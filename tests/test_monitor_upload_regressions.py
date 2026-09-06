import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MONITOR_RUNTIME = (PLUGINS / "MoonrakerMonitorRuntime.py").read_text()
MONITOR_CONTROLS = (PLUGINS / "MoonrakerMonitorControls.py").read_text()
MONITOR_TYPED_CONTROLS = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
UPLOAD_QML = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
OUTPUT_LIFECYCLE = (PLUGINS / "MoonrakerOutputDeviceLifecycle.py").read_text()
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text()
CONFIG_QML = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text()


class MonitorUploadRegressionTests(unittest.TestCase):
    def test_settings_tab_is_named_upload(self):
        self.assertIn('UM.TabRowButton { text: "Upload" }', CONFIG_QML)
        self.assertNotIn('UM.TabRowButton { text: "Output" }', CONFIG_QML)
        self.assertIn('text: "Upload format"', CONFIG_QML)

    def test_cancelled_upload_finishes_without_reporting_failure(self):
        start = OUTPUT_LIFECYCLE.index("def cancelUpload")
        end = OUTPUT_LIFECYCLE.index("def _show_upload_dialog", start) if "def _show_upload_dialog" in OUTPUT_LIFECYCLE[start:] else OUTPUT_LIFECYCLE.index("def _fail", start)
        block = OUTPUT_LIFECYCLE[start:end]
        self.assertIn("QTimer.singleShot(0, self._finish_cancel_upload)", block)
        self.assertIn("self._cleanup()", block)
        self.assertIn("self._emit_write_finished_once()", block)
        self.assertNotIn("writeError.emit", block)
        self.assertIn("writeFinished.emit(self)", OUTPUT_LIFECYCLE)

    def test_upload_accept_is_deferred_until_qml_handler_returns(self):
        start = OUTPUT_LIFECYCLE.index("def acceptUpload")
        end = OUTPUT_LIFECYCLE.index("def cancelUpload", start)
        block = OUTPUT_LIFECYCLE[start:end]
        self.assertIn("QTimer.singleShot(0, self._finish_accept_upload)", block)
        self.assertIn("self._release_dialog()", block)
        self.assertIn("self._begin_upload()", block)

    def test_upload_dialog_has_cura_combobox_i18n_context(self):
        self.assertIn('property variant catalog: UM.I18nCatalog { name: "cura" }', UPLOAD_QML)
        self.assertIn("Cura.ComboBox", UPLOAD_QML)
        self.assertIn("manager.uploadPathOptions", UPLOAD_QML)

    def test_folder_dropdown_discovers_moonraker_gcodes_directories(self):
        for token in (
            "uploadPathsChanged",
            'self._folder_scan_queue = ["gcodes"]',
            '"server/files/directory?"',
            'result.get("dirs")',
            'item.get("dirname")',
            "self._folder_scan_discovered.add(relative)",
        ):
            self.assertIn(token, OUTPUT_LIFECYCLE)

    def test_all_started_upload_outcomes_finish_cura_write_lifecycle(self):
        self.assertIn("had_started_write = bool(self._busy)", OUTPUT_LIFECYCLE)
        self.assertIn("super()._fail(text)", OUTPUT_LIFECYCLE)
        self.assertIn("super()._on_upload_finished(reply)", OUTPUT_LIFECYCLE)
        self.assertGreaterEqual(OUTPUT_LIFECYCLE.count("_emit_write_finished_once()"), 3)

    def test_monitor_uses_follower_layer_interpretation(self):
        combined = MONITOR_RUNTIME + MONITOR_CONTROLS
        self.assertIn("_resolve_live_layer(status)", combined)
        self.assertIn("_remote_current_layer_map", combined)
        self.assertIn("moonraker_layer_is_one_based", combined)
        self.assertIn("_remote_layer_ranges", combined)
        self.assertIn('virtual_sdcard.get("file_position")', combined)
        self.assertIn("_layer_from_file_position", combined)
        self.assertIn("_layer_from_z", combined)
        self.assertIn("_remote_index_filename", combined)
        self.assertIn("_metadata_layer_count", MONITOR_CONTROLS)
        self.assertIn("_layer_from_metadata_z", MONITOR_CONTROLS)
        self.assertIn("monitorLayerHeight", MONITOR_CONTROLS)
        self.assertIn("MoonrakerMonitorRuntime", OUTPUT_PLUGIN)

    def test_monitor_dashboard_contains_live_controls_and_status_groups(self):
        for token in (
            "monitorEta", "monitorFinish", "temperatureItems", "fanItems",
            "filamentSensorItems", "excludeObjectItems", "powerDevices",
            "pausePrint", "resumePrint", "cancelPrint", "excludeObject",
            "setPowerDevice", "hostLoad", "memoryAvailable", "cpuTemperature",
            "klipperVersion", "moonrakerVersion", "mcuSummary",
        ):
            self.assertIn(token, MONITOR_MODEL + MONITOR_QML)

    def test_active_monitor_chain_is_packaged_and_selected(self):
        self.assertIn('"MoonrakerMonitorDashboard.qml"', OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitor.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorTypedControls", OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorControls", MONITOR_TYPED_CONTROLS)
        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)
        self.assertIn("Printer controls", DASHBOARD_QML)
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())

    def test_enhanced_monitor_exposes_requested_setup_and_macro_controls(self):
        combined_controls = MONITOR_CONTROLS + MONITOR_TYPED_CONTROLS
        for token in (
            "macroNames", "runMacro", "temperaturePresetNames", "applyTemperaturePreset",
            "homeAll", "runQuadGantryLevel", "calibrateBedMesh", "hasQuadGantryLevel",
            "hasBedMesh", "server/database/item?namespace=mainsail&key=presets",
            "macroParameterDefinitions", "temperaturePresetItems",
        ):
            self.assertIn(token, combined_controls + DASHBOARD_QML)
        self.assertIn('"G28"', MONITOR_CONTROLS)
        self.assertIn('"QUAD_GANTRY_LEVEL"', MONITOR_CONTROLS)
        self.assertIn('"BED_MESH_CALIBRATE"', MONITOR_CONTROLS)

    def test_enhanced_monitor_exposes_live_tuning_controls(self):
        for token in (
            "setSpeedFactor", "setFlowFactor", "adjustZOffset", "clearZOffset",
            "setFanSpeed", "setLedBrightness", "speedFactorPercent", "flowFactorPercent",
            "fanControlItems", "ledItems", "zOffsetText",
        ):
            self.assertIn(token, MONITOR_CONTROLS + DASHBOARD_QML)
        self.assertIn("SET_GCODE_OFFSET Z_ADJUST", MONITOR_CONTROLS)
        self.assertIn("M220 S", MONITOR_CONTROLS)
        self.assertIn("M221 S", MONITOR_CONTROLS)
        self.assertIn("SET_FAN_SPEED", MONITOR_CONTROLS)
        self.assertIn("SET_LED", MONITOR_CONTROLS)

    def test_save_config_only_appears_when_klipper_reports_pending_changes(self):
        self.assertIn('lower in {"configfile", "toolhead", "quad_gantry_level", "bed_mesh"}', MONITOR_CONTROLS)
        self.assertIn('configfile.get("save_config_pending", False)', MONITOR_CONTROLS)
        self.assertIn("saveConfigPending", MONITOR_CONTROLS)
        self.assertIn("canSaveConfig", MONITOR_CONTROLS)
        self.assertIn('"SAVE_CONFIG"', MONITOR_CONTROLS)
        self.assertIn("Save configuration", DASHBOARD_QML)

    def test_emergency_stop_requires_three_rapid_clicks_and_has_fill_progress(self):
        self.assertIn("time.monotonic()", MONITOR_CONTROLS)
        self.assertIn("now - self._estop_last_click > 1.0", MONITOR_CONTROLS)
        self.assertIn("self._estop_clicks >= 3", MONITOR_CONTROLS)
        self.assertIn('"printer/emergency_stop"', MONITOR_CONTROLS)
        self.assertIn("emergencyStopClicks", MONITOR_CONTROLS)
        self.assertIn("emergencyButton.clicks / 3.0", DASHBOARD_QML)
        self.assertIn("EMERGENCY STOP", DASHBOARD_QML)
        self.assertNotIn("Emergency stop?", DASHBOARD_QML)

    def test_power_lock_is_explained_in_enhanced_monitor(self):
        self.assertIn("locked && !printer.powerDevices[i].can_toggle", DASHBOARD_QML)
        self.assertIn("Power control is locked by Moonraker while this print is active.", DASHBOARD_QML)

    def test_monitor_controls_class_definition_smoke(self):
        class DummySignal:
            def emit(self, *_args, **_kwargs):
                pass

        def pyqt_signal(*_args, **_kwargs):
            return DummySignal()

        def pyqt_property(*_args, **_kwargs):
            def decorate(function):
                return property(function)
            return decorate

        def pyqt_slot(*_args, **_kwargs):
            def decorate(function):
                return function
            return decorate

        class QVariant:
            def __init__(self, value=None):
                self.value = value

        class QTimer:
            pass

        pyqt6 = types.ModuleType("PyQt6")
        qtcore = types.ModuleType("PyQt6.QtCore")
        qtcore.QTimer = QTimer
        qtcore.QVariant = QVariant
        qtcore.pyqtProperty = pyqt_property
        qtcore.pyqtSignal = pyqt_signal
        qtcore.pyqtSlot = pyqt_slot

        package = types.ModuleType("plugins")
        package.__path__ = []
        runtime = types.ModuleType("plugins.MoonrakerMonitorRuntime")
        runtime.MoonrakerMonitorModel = type("BaseMoonrakerMonitorModel", (), {})

        names = ["PyQt6", "PyQt6.QtCore", "plugins", "plugins.MoonrakerMonitorRuntime"]
        old = {name: sys.modules.get(name) for name in names}
        try:
            sys.modules["PyQt6"] = pyqt6
            sys.modules["PyQt6.QtCore"] = qtcore
            sys.modules["plugins"] = package
            sys.modules["plugins.MoonrakerMonitorRuntime"] = runtime
            namespace = {"__name__": "plugins.MoonrakerMonitorControls", "__package__": "plugins"}
            exec(compile(MONITOR_CONTROLS, "MoonrakerMonitorControls.py", "exec"), namespace)
            self.assertIn("MoonrakerMonitorModel", namespace)
        finally:
            for name, value in old.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

    def test_runtime_sources_do_not_contain_release_nicknames(self):
        offenders = []
        pattern = re.compile(r"\bv3\b", re.IGNORECASE)
        for path in sorted(PLUGINS.iterdir()):
            if path.suffix.lower() not in {".py", ".qml"}:
                continue
            for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
