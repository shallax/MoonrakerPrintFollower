import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MONITOR_RUNTIME = (PLUGINS / "MoonrakerMonitorRuntime.py").read_text()
MONITOR_CONTROLS = (PLUGINS / "MoonrakerMonitorControls.py").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
ENHANCED_QML = (PLUGINS / "MoonrakerMonitorEnhanced.qml").read_text()
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
        self.assertIn("self._cleanup()", block)
        self.assertIn("self._emit_write_finished_once()", block)
        self.assertNotIn("writeError.emit", block)
        self.assertIn("writeFinished.emit(self)", OUTPUT_LIFECYCLE)

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

    def test_enhanced_monitor_is_packaged_and_selected(self):
        self.assertIn('"MoonrakerMonitorEnhanced.qml"', OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitor.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorControls", OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitor", ENHANCED_QML)
        self.assertIn("Printer controls", ENHANCED_QML)

    def test_enhanced_monitor_exposes_requested_setup_and_macro_controls(self):
        for token in (
            "macroNames", "runMacro", "temperaturePresetNames", "applyTemperaturePreset",
            "homeAll", "runQuadGantryLevel", "calibrateBedMesh", "hasQuadGantryLevel",
            "hasBedMesh", "server/database/item?namespace=mainsail&key=presets",
        ):
            self.assertIn(token, MONITOR_CONTROLS + ENHANCED_QML)
        self.assertIn('"G28"', MONITOR_CONTROLS)
        self.assertIn('"QUAD_GANTRY_LEVEL"', MONITOR_CONTROLS)
        self.assertIn('"BED_MESH_CALIBRATE"', MONITOR_CONTROLS)

    def test_enhanced_monitor_exposes_live_tuning_controls(self):
        for token in (
            "setSpeedFactor", "setFlowFactor", "adjustZOffset", "clearZOffset",
            "setFanSpeed", "setLedBrightness", "speedFactorPercent", "flowFactorPercent",
            "fanControlItems", "ledItems", "zOffsetText",
        ):
            self.assertIn(token, MONITOR_CONTROLS + ENHANCED_QML)
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
        self.assertIn("Save configuration", ENHANCED_QML)

    def test_emergency_stop_requires_three_rapid_clicks_and_has_fill_progress(self):
        self.assertIn("time.monotonic()", MONITOR_CONTROLS)
        self.assertIn("now - self._estop_last_click > 1.0", MONITOR_CONTROLS)
        self.assertIn("self._estop_clicks >= 3", MONITOR_CONTROLS)
        self.assertIn('"printer/emergency_stop"', MONITOR_CONTROLS)
        self.assertIn("emergencyStopClicks", MONITOR_CONTROLS)
        self.assertIn("emergencyButton.clicks / 3.0", ENHANCED_QML)
        self.assertIn("EMERGENCY STOP", ENHANCED_QML)
        self.assertNotIn("Emergency stop?", ENHANCED_QML)

    def test_power_lock_is_explained_in_enhanced_monitor(self):
        self.assertIn("locked && !printer.powerDevices[i].can_toggle", ENHANCED_QML)
        self.assertIn("Power control is locked by Moonraker while this print is active.", ENHANCED_QML)

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
