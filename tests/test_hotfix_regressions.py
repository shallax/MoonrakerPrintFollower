import pathlib
from types import SimpleNamespace
import unittest

from plugins.MonitorFormatting import core_values, infer_macro_parameters
from plugins.PrintState import LayerResolver

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
UPLOAD = (PLUGINS / "UploadController.py").read_text()
ADAPTER = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
UPLOAD_QML = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
BED_MESH_QML = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text()
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text()


class HotfixRegressionTests(unittest.TestCase):
    def test_upload_dialog_teardown_is_queued_out_of_qml_callbacks(self):
        self.assertIn("self._later(0, finish)", UPLOAD)
        self.assertIn('self._later_owned(0, lambda: self._finish(False, ""))', UPLOAD)
        self.assertIn("dialog.deleteLater()", ADAPTER)

    def test_upload_folders_are_discovered_and_hidden_paths_excluded(self):
        self.assertIn("server/files/directory?", UPLOAD)
        self.assertIn('part.startswith(".")', UPLOAD)
        self.assertIn("MAX_DIRECTORIES", UPLOAD)
        self.assertIn("manager.uploadPathOptions", UPLOAD_QML)
        self.assertIn("UM.I18nCatalog", UPLOAD_QML)

    def test_dashboard_shows_current_z_offset_beside_nudges(self):
        self.assertIn('text: "Current Z offset"', DASHBOARD_QML)
        self.assertIn('"Current " + root.printer.zOffsetText', DASHBOARD_QML)
        self.assertIn("adjustZOffset", DASHBOARD_QML)

    def test_temperature_presets_are_buttons_not_an_implied_selection(self):
        self.assertIn("temperaturePresetItems", DASHBOARD_QML)
        self.assertIn('modelData.active ? "Active — "', DASHBOARD_QML)
        self.assertIn("applyTemperaturePreset(modelData.index)", DASHBOARD_QML)
        self.assertNotIn("temperaturePresetSelector", DASHBOARD_QML)

    def test_output_plugin_selects_the_same_dashboard_through_one_model(self):
        self.assertIn("from .MoonrakerMonitorModel import MoonrakerMonitorModel", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorDashboard", BED_MESH_QML)

    def test_macro_parameter_inference_types_defaults(self):
        definitions = infer_macro_parameters("""
            {% set enabled = params.ENABLED|default(True) %}
            {% set count = params.COUNT|default(5)|int %}
            {% set scale = params.SCALE|default(0.25)|float %}
            {% set label = params.LABEL|default('test') %}
            {% set required = params.REQUIRED|int %}
        """)
        by_name = {item["name"]: item for item in definitions}
        self.assertEqual(by_name["ENABLED"]["type"], "bool")
        self.assertEqual(by_name["ENABLED"]["default"], "True")
        self.assertEqual(by_name["COUNT"]["type"], "int")
        self.assertEqual(by_name["COUNT"]["default"], "5")
        self.assertEqual(by_name["SCALE"]["type"], "float")
        self.assertEqual(by_name["LABEL"]["type"], "string")
        self.assertTrue(by_name["REQUIRED"]["required"])

    def test_monitor_layer_height_reports_current_thickness(self):
        config = SimpleNamespace(
            moonraker_layer_is_one_based=True,
            z_fallback=False,
            z_tolerance=0.05,
        )
        layer = LayerResolver().resolve(
            {"print_stats": {"info": {"current_layer": 2, "total_layer": 3}}},
            config,
            metadata={"first_layer_height": 0.2, "layer_height": 0.2},
            heights=(0.2, 0.35, 0.55),
        )
        self.assertEqual(layer.index, 1)
        self.assertAlmostEqual(layer.height, 0.35)
        self.assertAlmostEqual(layer.thickness, 0.15)

        snapshot = SimpleNamespace(core={
            "print_stats": {"state": "printing", "print_duration": 0},
            "virtual_sdcard": {"progress": 0},
            "gcode_move": {},
            "motion_report": {},
        })
        physical = SimpleNamespace(
            layer=layer,
            estimated_time=None,
            metadata_complete=False,
        )
        self.assertEqual(core_values(snapshot, physical, True)["monitorLayerHeight"], "0.150 mm")

    def test_layer_thickness_falls_back_to_metadata_without_geometry(self):
        config = SimpleNamespace(
            moonraker_layer_is_one_based=True,
            z_fallback=False,
            z_tolerance=0.05,
        )
        resolver = LayerResolver()
        first = resolver.resolve(
            {"print_stats": {"info": {"current_layer": 1, "total_layer": 5}}},
            config,
            metadata={"first_layer_height": 0.24, "layer_height": 0.1},
        )
        later = resolver.resolve(
            {"print_stats": {"info": {"current_layer": 3, "total_layer": 5}}},
            config,
            metadata={"first_layer_height": 0.24, "layer_height": 0.1},
        )
        self.assertAlmostEqual(first.height, 0.24)
        self.assertAlmostEqual(first.thickness, 0.24)
        self.assertAlmostEqual(later.height, 0.44)
        self.assertAlmostEqual(later.thickness, 0.1)

    def test_pwm_controls_remain_in_dashboard(self):
        self.assertIn("pwmOutputItems", DASHBOARD_QML)
        self.assertIn("setPwmOutput", DASHBOARD_QML)
        self.assertIn('text: "PWM outputs"', DASHBOARD_QML)


if __name__ == "__main__": unittest.main()
