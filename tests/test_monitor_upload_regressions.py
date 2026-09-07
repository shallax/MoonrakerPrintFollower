import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
def text(name): return (PLUGINS / name).read_text()


class MonitorUploadRegressionTests(unittest.TestCase):
    def test_settings_tab_and_upload_dialog_contract(self):
        config, upload = text("MoonrakerFollowerConfiguration.qml"), text("MoonrakerUploadDialog.qml")
        self.assertIn('UM.TabRowButton { text: "Upload" }', config)
        self.assertIn('text: "Upload format"', config)
        self.assertIn('property variant catalog: UM.I18nCatalog { name: "cura" }', upload)
        self.assertIn("manager.uploadPathOptions", upload)

    def test_cancel_is_deferred_and_not_an_error(self):
        controller = text("UploadController.py")
        adapter = text("MoonrakerOutputDevice.py")
        self.assertIn('self._later(0, lambda: self._finish(False, ""))', controller)
        self.assertIn("elif error:", adapter)
        self.assertIn("self.writeFinished.emit(self)", adapter)
        self.assertIn("self._upload.terminal_delivered()", adapter)

    def test_accept_and_folder_discovery_remain_nonblocking(self):
        source = text("UploadController.py")
        self.assertIn("self._later(0, finish)", source)
        self.assertIn("server/files/directory?", source)
        self.assertIn("MAX_DIRECTORIES", source)
        self.assertNotIn("time.sleep", source)

    def test_single_qt_model_exposes_dashboard_features(self):
        source = text("MoonrakerMonitorModel.py")
        for token in ("monitorEta", "monitorFinish", "temperatureItems", "fanItems", "filamentSensorItems",
            "excludeObjectItems", "powerDevices", "pausePrint", "resumePrint", "cancelPrint", "excludeObject",
            "setPowerDevice", "hostLoad", "memoryAvailable", "cpuTemperature", "klipperVersion", "moonrakerVersion",
            "mcuSummary", "macroNames", "runMacro", "temperaturePresetNames", "applyTemperaturePreset",
            "homeAll", "runQuadGantryLevel", "calibrateBedMesh", "macroParameterDefinitions", "temperaturePresetItems",
            "setSpeedFactor", "setFlowFactor", "adjustZOffset", "clearZOffset", "setFanSpeed", "setLedBrightness",
            "speedFactorPercent", "flowFactorPercent", "fanControlItems", "ledItems", "zOffsetText", "canSaveConfig"):
            self.assertIn(token, source)
        self.assertIn("class MoonrakerMonitorModel(PrinterOutputModel)", source)
        self.assertNotIn("_BaseMoonrakerMonitorModel", source)

    def test_same_dashboard_chain_and_power_lock_explanation(self):
        plugin = text("MoonrakerOutputDevicePlugin.py")
        dashboard = text("MoonrakerMonitorDashboard.qml")
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', plugin)
        self.assertIn("MoonrakerMonitorDashboard", text("MoonrakerMonitorBedMesh.qml"))
        self.assertIn("MoonrakerMonitor", dashboard)
        self.assertIn("Power control is locked by Moonraker while this print is active.", dashboard)

    def test_setup_and_save_commands_have_one_policy_owner(self):
        controls = text("MonitorControls.py")
        for command in ("G28", "QUAD_GANTRY_LEVEL", "BED_MESH_CALIBRATE", "SAVE_CONFIG", "SET_GCODE_OFFSET", "SET_FAN_SPEED", "SET_LED"):
            self.assertIn(command, controls)
        self.assertIn("self._commands.setup_allowed", controls)
        self.assertIn("configfile.get(\"save_config_pending\")", controls)

    def test_emergency_stop_remains_three_clicks_with_progress(self):
        source = text("MonitorCommands.py")
        dashboard = text("MoonrakerMonitorDashboard.qml")
        self.assertIn("self._clicks == 3", source)
        self.assertIn("self._reset_timer.setInterval(1000)", source)
        self.assertIn('"printer/emergency_stop"', source)
        self.assertIn("emergencyButton.clicks / 3.0", dashboard)
        self.assertIn("EMERGENCY STOP", dashboard)
        self.assertNotIn("Emergency stop?", dashboard)

    def test_runtime_sources_do_not_contain_release_nicknames(self):
        for path in PLUGINS.iterdir():
            if path.suffix in {".py", ".qml"}:
                self.assertIsNone(re.search(r"\bv3\b", path.read_text(), re.I), path.name)


if __name__ == "__main__": unittest.main()
