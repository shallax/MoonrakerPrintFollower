import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

LIFECYCLE = (PLUGINS / "MoonrakerOutputDeviceLifecycle.py").read_text()
UPLOAD_QML = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
TYPED_CONTROLS = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text()
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text()


class HotfixRegressionTests(unittest.TestCase):
    def test_upload_dialog_teardown_is_queued_out_of_qml_callbacks(self):
        self.assertIn("QTimer.singleShot(0, self._finish_accept_upload)", LIFECYCLE)
        self.assertIn("QTimer.singleShot(0, self._finish_cancel_upload)", LIFECYCLE)
        self.assertIn("def _release_dialog", LIFECYCLE)
        self.assertIn("dialog.deleteLater()", LIFECYCLE)
        self.assertNotIn("self._dialog = None\n        self._cleanup()\n        self._emit_write_finished_once()", LIFECYCLE)

    def test_upload_folders_are_discovered_from_moonraker(self):
        self.assertIn("uploadPathsChanged = pyqtSignal()", LIFECYCLE)
        self.assertIn("server/files/directory?", LIFECYCLE)
        self.assertIn('"gcodes"', LIFECYCLE)
        self.assertIn('item.get("dirname")', LIFECYCLE)
        self.assertIn("manager.uploadPathOptions", UPLOAD_QML)
        self.assertIn("UM.I18nCatalog", UPLOAD_QML)

    def test_hidden_upload_folders_are_not_presented(self):
        self.assertIn("def _is_hidden_remote_path", LIFECYCLE)
        self.assertIn('part.startswith(".")', LIFECYCLE)
        self.assertIn('dirname.startswith(".")', LIFECYCLE)
        self.assertIn("not self._is_hidden_remote_path(normalised)", LIFECYCLE)
        self.assertIn('return "" if self._is_hidden_remote_path(path) else path', LIFECYCLE)

    def test_dashboard_shows_current_z_offset_beside_nudges(self):
        self.assertIn('text: "Current Z offset"', DASHBOARD_QML)
        self.assertIn('"Current " + root.printer.zOffsetText', DASHBOARD_QML)
        self.assertIn("adjustZOffset", DASHBOARD_QML)

    def test_temperature_presets_are_buttons_not_an_implied_selection(self):
        self.assertIn("temperaturePresetItems", DASHBOARD_QML)
        self.assertIn('text: modelData.active ? "Active — " + modelData.name : modelData.name', DASHBOARD_QML)
        self.assertIn("applyTemperaturePreset(modelData.index)", DASHBOARD_QML)
        self.assertNotIn("temperaturePresetSelector", DASHBOARD_QML)
        self.assertIn("def _preset_is_active", TYPED_CONTROLS)

    def test_output_plugin_selects_typed_dashboard(self):
        self.assertIn("MoonrakerMonitorTypedControls", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorDashboard.qml"', OUTPUT_PLUGIN)

    def _load_typed_model(self):
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

        pyqt6 = types.ModuleType("PyQt6")
        qtcore = types.ModuleType("PyQt6.QtCore")
        qtcore.QVariant = QVariant
        qtcore.pyqtProperty = pyqt_property
        qtcore.pyqtSignal = pyqt_signal
        qtcore.pyqtSlot = pyqt_slot

        package = types.ModuleType("plugins")
        package.__path__ = []
        base_module = types.ModuleType("plugins.MoonrakerMonitorControls")
        base_module.MoonrakerMonitorModel = type(
            "BaseMoonrakerMonitorModel",
            (),
            {"_want_aux_object": staticmethod(lambda _name: False)},
        )

        names = ["PyQt6", "PyQt6.QtCore", "plugins", "plugins.MoonrakerMonitorControls"]
        old = {name: sys.modules.get(name) for name in names}
        try:
            sys.modules["PyQt6"] = pyqt6
            sys.modules["PyQt6.QtCore"] = qtcore
            sys.modules["plugins"] = package
            sys.modules["plugins.MoonrakerMonitorControls"] = base_module
            namespace = {"__name__": "plugins.MoonrakerMonitorTypedControls", "__package__": "plugins"}
            exec(compile(TYPED_CONTROLS, "MoonrakerMonitorTypedControls.py", "exec"), namespace)
            return namespace["MoonrakerMonitorModel"]
        finally:
            for name, value in old.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

    def test_macro_parameter_inference_types_defaults(self):
        model = self._load_typed_model()
        definitions = model._infer_macro_parameters(
            """
            {% set enabled = params.ENABLED|default(True) %}
            {% set count = params.COUNT|default(5)|int %}
            {% set scale = params.SCALE|default(0.25)|float %}
            {% set label = params.LABEL|default('test') %}
            {% set required = params.REQUIRED|int %}
            """
        )
        by_name = {item["name"]: item for item in definitions}
        self.assertEqual(by_name["ENABLED"]["type"], "bool")
        self.assertEqual(by_name["ENABLED"]["default"], "True")
        self.assertEqual(by_name["COUNT"]["type"], "int")
        self.assertEqual(by_name["COUNT"]["default"], "5")
        self.assertEqual(by_name["SCALE"]["type"], "float")
        self.assertEqual(by_name["LABEL"]["type"], "string")
        self.assertTrue(by_name["REQUIRED"]["required"])

    def test_pwm_output_pin_discovery_and_scaled_set_pin(self):
        self.assertIn('lower.startswith("output_pin ")', TYPED_CONTROLS)
        self.assertIn('section.get("pwm")', TYPED_CONTROLS)
        self.assertIn("pwmOutputItems", DASHBOARD_QML)
        self.assertIn("setPwmOutput", DASHBOARD_QML)
        self.assertIn('text: "PWM outputs"', DASHBOARD_QML)

        model = self._load_typed_model()
        instance = model.__new__(model)
        instance._aux_status = {
            "configfile": {
                "config": {
                    "output_pin case_light": {"pwm": "True", "scale": "2.0"},
                    "output_pin relay": {"pwm": "False"},
                }
            },
            "output_pin case_light": {"value": 1.0},
            "output_pin relay": {"value": 1.0},
        }
        items = instance._build_pwm_output_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["pin"], "case_light")
        self.assertEqual(items[0]["percent"], 50)
        self.assertEqual(items[0]["scale"], 2.0)

        instance._pwm_output_items = items
        sent = []
        instance._send_quick_gcode = lambda channel, script: sent.append((channel, script))
        instance.setPwmOutput("output_pin case_light", 75)
        self.assertEqual(sent[-1][1], "SET_PIN PIN=case_light VALUE=1.5")


if __name__ == "__main__":
    unittest.main()
