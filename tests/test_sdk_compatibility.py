import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = json.loads((ROOT / "package.json").read_text())
PLUGIN_META = json.loads((ROOT / "plugins" / "plugin.json").read_text())
PLUGIN = (ROOT / "plugins" / "MoonrakerPrintFollower.py").read_text()
CLIENT = (ROOT / "plugins" / "MoonrakerClient.py").read_text()
MACHINE_ACTION = (ROOT / "plugins" / "MoonrakerFollowerMachineAction.py").read_text()
CONFIG_QML = (ROOT / "plugins" / "MoonrakerFollowerConfiguration.qml").read_text()
ACTION_QML = (ROOT / "plugins" / "PreviewActionPanelControls.qml").read_text()
EMPTY_QML = (ROOT / "plugins" / "EmptyPreviewLoadButton.qml").read_text()
NOZZLE_FALLBACK = (ROOT / "plugins" / "NativeNozzleFallback.py").read_text()
README = (ROOT / "README.md").read_text()


class SdkCompatibilityTests(unittest.TestCase):
    def test_package_declares_sdk_8_0_floor(self):
        self.assertEqual(PACKAGE["sdk_version"], "8.0.0")
        self.assertEqual(PACKAGE["sdk_version_semver"], "8.0.0")

    def test_plugin_declares_complete_cura_5_sdk_8_line(self):
        self.assertEqual(
            PLUGIN_META["supported_sdk_versions"],
            [f"8.{minor}.0" for minor in range(13)],
        )
        self.assertEqual(PLUGIN_META["api"], 8)

    def test_readme_states_cura_5_compatibility_boundary(self):
        self.assertIn("Cura 5.0–5.13 / SDK 8.0–8.12", README)
        self.assertIn("Cura 4.x / SDK 7.x is not supported", README)
        self.assertIn("Qt 6 / PyQt6 boundary", README)

    def test_does_not_depend_on_post_8_0_machine_action_properties(self):
        # visible/shouldOpenAsDialog were added to MachineAction in SDK 8.3.
        self.assertNotIn("shouldOpenAsDialog", MACHINE_ACTION)
        self.assertNotIn("_open_as_dialog", MACHINE_ACTION)
        self.assertNotIn("visibilityChanged", MACHINE_ACTION)
        self.assertNotIn("getSupportedActionMachineList", MACHINE_ACTION)
        self.assertIn("getMachineActionManager().addSupportedAction", MACHINE_ACTION)

    def test_settings_qml_avoids_um_controls_added_in_sdk_8_3(self):
        # UM.TextField, UM.Switch and UM.Slider were added in Cura 5.3 / SDK 8.3.
        # The plugin deliberately uses the Cura controls already available in 5.0.
        for newer_control in ("UM.TextField", "UM.Switch", "UM.Slider", "UM.ComponentWithIcon"):
            self.assertNotIn(newer_control, CONFIG_QML)
        self.assertIn("Cura.TextField", CONFIG_QML)
        self.assertIn("Cura.RadioButton", CONFIG_QML)
        self.assertIn("UM.CheckBox", CONFIG_QML)
        self.assertIn("Cura.MachineAction", CONFIG_QML)

    def test_cura_5_0_preview_contracts_are_used(self):
        # These are the stable SDK-8.0-era interfaces used by the follower.
        for token in (
            "globalContainerStackChanged",
            "self._application.readLocalFile",
            'addAdditionalComponent("saveButton"',
            "currentLayerNumChanged",
            "currentPathNumChanged",
            "getCurrentLayer",
            "getCurrentPath",
        ):
            self.assertIn(token, PLUGIN)
        self.assertIn('getattr(simulation_view, "getSimulationPass", None)', NOZZLE_FALLBACK)
        self.assertIn('getattr(simulation_view, "getNozzleNode", None)', NOZZLE_FALLBACK)

    def test_optional_qt_timeout_api_is_capability_guarded(self):
        for source in (PLUGIN, CLIENT, MACHINE_ACTION):
            occurrences = [m.start() for m in re.finditer(r"\.setTransferTimeout\(", source)]
            for pos in occurrences:
                preceding = source[max(0, pos - 180):pos]
                self.assertIn('hasattr(request, "setTransferTimeout")', preceding)

    def test_no_qt5_or_websocket_dependency_is_reintroduced(self):
        combined = "\n".join((PLUGIN, CLIENT, MACHINE_ACTION, CONFIG_QML, ACTION_QML, EMPTY_QML))
        self.assertNotIn("PyQt5", combined)
        self.assertNotIn("QtWebSockets", combined)
        self.assertNotIn("QWebSocket", combined)

    def test_qml_imports_are_qt6_2_compatible(self):
        # Cura 5.0 shipped Qt 6.2 and its own QML uses QtQuick 2.15.
        for qml in (CONFIG_QML, ACTION_QML, EMPTY_QML):
            self.assertRegex(qml, r"^import QtQuick 2\.15", qml[:80])
        self.assertIn("import QtQuick.Controls 2.15", CONFIG_QML)


if __name__ == "__main__":
    unittest.main()
