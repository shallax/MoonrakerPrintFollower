import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MONITOR_RUNTIME = (PLUGINS / "MoonrakerMonitorRuntime.py").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
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
        self.assertIn("_resolve_live_layer(status)", MONITOR_RUNTIME)
        self.assertIn("_remote_current_layer_map", MONITOR_RUNTIME)
        self.assertIn("moonraker_layer_is_one_based", MONITOR_RUNTIME)
        self.assertIn("_remote_layer_ranges", MONITOR_RUNTIME)
        self.assertIn('virtual_sdcard.get("file_position")', MONITOR_RUNTIME)
        self.assertIn("_layer_from_file_position", MONITOR_RUNTIME)
        self.assertIn("_layer_from_z", MONITOR_RUNTIME)
        self.assertIn("_remote_index_filename", MONITOR_RUNTIME)
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
