import pathlib
import unittest
from plugins.MonitorFormatting import infer_macro_parameters

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
        self.assertIn('self._later(0, lambda: self._finish(False, ""))', UPLOAD)
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

    def test_pwm_controls_remain_in_dashboard(self):
        self.assertIn("pwmOutputItems", DASHBOARD_QML)
        self.assertIn("setPwmOutput", DASHBOARD_QML)
        self.assertIn('text: "PWM outputs"', DASHBOARD_QML)


if __name__ == "__main__": unittest.main()
