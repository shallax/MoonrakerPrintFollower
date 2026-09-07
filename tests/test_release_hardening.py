from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
TOOLS = ROOT / "tools"
FIXTURES = ROOT / "tests" / "fixtures" / "gcode"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from plugins.GCodeIndex import build_index_from_file
from plugins.MonitorFormatting import estimate_remaining, parse_bed_mesh, parse_mcu_stats
from check_qml import check_text
from build_curapackage import build
from verify_curapackage import verify


class ReleaseHardeningTests(unittest.TestCase):
    def test_all_qml_pass_structural_checker(self):
        failures = []
        for path in PLUGINS.glob("*.qml"): failures.extend(check_text(path.read_text(), path.name))
        self.assertEqual(failures, [])

    def test_qml_checker_rejects_duplicate_property_and_unbalanced_brace(self):
        failures = check_text("import QtQuick 2.15\nItem { width: 1; width: 2\n", "bad.qml")
        self.assertTrue(any("duplicate property 'width'" in item for item in failures))
        self.assertTrue(any("unclosed '{'" in item for item in failures))

    def test_repository_has_no_tracked_python_cache_or_legacy_dashboard(self):
        tracked = subprocess.check_output(["git", "--no-pager", "ls-files"], cwd=ROOT, text=True).splitlines()
        self.assertEqual([name for name in tracked if "__pycache__" in name or name.endswith((".pyc", ".pyo"))], [])
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())
        gitignore = (ROOT / ".gitignore").read_text()
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
        self.assertEqual(build_index_from_file(str(FIXTURES / "prusa.gcode"), compact=False).layer_count(), 3)
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
        self.assertAlmostEqual(estimate_remaining(3600, 0.02, 7 * 3600, True), 6 * 3600, delta=1)
        self.assertAlmostEqual(estimate_remaining(3 * 3600, 0.10, 7 * 3600, True), 4 * 3600, delta=1)
        self.assertIsNone(estimate_remaining(120, 0.50, None, False))

    def test_malformed_bed_mesh_and_mcu_payloads_are_rejected_or_degraded(self):
        self.assertEqual(parse_bed_mesh(None), {})
        for matrix, bounds in (([[0, 1], [2]], [0, 0]), ([[0, float("nan")], [1, 2]], [0, 0]), ([[0, 1], [1, 2]], [2, 0])):
            self.assertEqual(parse_bed_mesh({"mesh_matrix": matrix, "mesh_min": bounds, "mesh_max": [1, 1]}), {})
        self.assertEqual(parse_mcu_stats("mcu_awake=0.02 nonsense bytes_write=abc bytes_read=123"), {"mcu_awake": 0.02, "bytes_read": 123.0})

    def test_full_config_is_discovered_not_polled_every_second(self):
        data = (PLUGINS / "MonitorData.py").read_text()
        self.assertIn('["save_config_pending", "save_config_pending_items"]', data)
        self.assertIn('"config-static"', data)
        self.assertIn('category="discovery"', data)

    def test_deferred_slider_and_monitor_ux_contracts(self):
        dashboard = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
        monitor = (PLUGINS / "MoonrakerMonitor.qml").read_text()
        self.assertGreaterEqual(dashboard.count("live: false"), 9)
        self.assertGreaterEqual(dashboard.count("onMoved:"), 9)
        self.assertIn("slider.valueAt(slider.position)", dashboard)
        self.assertIn("After release, the latest value is applied once it has been unchanged for 2 seconds.", dashboard)
        self.assertIn('text: "Refresh camera"', monitor)
        self.assertIn('title: "Exclude object?"', monitor)
        tuning = (PLUGINS / "MonitorTuning.py").read_text()
        self.assertIn("DEBOUNCE_MS = 2000", tuning)
        self.assertIn("current.revision != revision", tuning)


if __name__ == "__main__": unittest.main()
