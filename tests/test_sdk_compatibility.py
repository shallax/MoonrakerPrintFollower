import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
PACKAGE = json.loads((ROOT / "package.json").read_text())
PLUGIN_META = json.loads((PLUGINS / "plugin.json").read_text())
FOLLOWER_SOURCES = (
    "MoonrakerPrintFollower.py",
    "FollowerRuntime.py",
    "PrintCoordinator.py",
    "PrinterBinding.py",
    "CuraIntegration.py",
    "PreviewPresentation.py",
    "PreviewFollower.py",
    "RemoteFileService.py",
    "GCodeIndexService.py",
    "MoonrakerTransport.py",
    "MoonrakerSocket.py",
    "SocketFraming.py",
)
PLUGIN = "\n".join((PLUGINS / name).read_text() for name in FOLLOWER_SOURCES)
CLIENT = (PLUGINS / "MoonrakerClient.py").read_text()
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MACHINE_ACTION = (PLUGINS / "MoonrakerFollowerMachineAction.py").read_text()
# Every shipped QML file is audited; a newly added file must not silently
# bypass the Qt6.2 import checks.
QML_FILES = sorted(path.name for path in PLUGINS.glob("*.qml"))
QML_SOURCES = {path.name: path.read_text() for path in PLUGINS.glob("*.qml")}
CONFIG_QML = QML_SOURCES["MoonrakerFollowerConfiguration.qml"]
MONITOR_QML = QML_SOURCES["MoonrakerMonitor.qml"]
CAMERA_PANE_QML = QML_SOURCES["CameraPane.qml"]
UPLOAD_QML = QML_SOURCES["MoonrakerUploadDialog.qml"]
ACTION_QML = QML_SOURCES["MoonrakerPreviewCard.qml"]
EMPTY_QML = QML_SOURCES["MoonrakerPreviewCard.qml"]
FOLLOW_PASS = (PLUGINS / "FollowPass.py").read_text()
FOLLOW_CONTROLLER = (PLUGINS / "FollowPassController.py").read_text()
README = (ROOT / "README.md").read_text()


class SdkCompatibilityTests(unittest.TestCase):
    def test_package_declares_sdk_8_0_floor(self):
        self.assertEqual(PACKAGE["sdk_version"], "8.11.0")
        self.assertEqual(PACKAGE["sdk_version_semver"], "8.11.0")

    def test_plugin_declares_complete_cura_5_sdk_8_line(self):
        self.assertEqual(
            PLUGIN_META["supported_sdk_versions"],
            ["8.11.0", "8.12.0"],
        )
        self.assertEqual(PLUGIN_META["api"], 8)

    def test_readme_states_cura_5_compatibility_boundary(self):
        self.assertIn("Cura 5.11–5.13 / SDK 8.11–8.12", README)
        self.assertIn("Cura 4.x / SDK 7.x is not supported", README)
        self.assertIn("5.11 floor", README)

    def test_does_not_depend_on_post_8_0_machine_action_properties(self):
        self.assertNotIn("shouldOpenAsDialog", MACHINE_ACTION)
        self.assertNotIn("_open_as_dialog", MACHINE_ACTION)
        self.assertNotIn("visibilityChanged", MACHINE_ACTION)
        self.assertNotIn("getSupportedActionMachineList", MACHINE_ACTION)
        self.assertIn("getMachineActionManager().addSupportedAction", MACHINE_ACTION)

    def test_settings_qml_avoids_um_controls_added_in_sdk_8_3(self):
        for newer_control in (
            "UM.TextField",
            "UM.Switch",
            "UM.Slider",
            "UM.ComponentWithIcon",
        ):
            self.assertNotIn(newer_control, CONFIG_QML)
        self.assertIn("Cura.TextField", CONFIG_QML)
        self.assertIn("Cura.RadioButton", CONFIG_QML)
        self.assertIn("UM.CheckBox", CONFIG_QML)
        self.assertIn("Cura.MachineAction", CONFIG_QML)

    def test_cura_5_0_preview_contracts_are_used(self):
        for token in (
            "globalContainerStackChanged",
            "self.application.readLocalFile",
            'addAdditionalComponent("saveButton"',
            "currentLayerNumChanged",
            "currentPathNumChanged",
            "getCurrentLayer",
            "getCurrentPath",
        ):
            self.assertIn(token, PLUGIN)
        # The follow-pass architecture exercises the capability-guarded
        # SimulationView accessors through the public APIs (the nozzle
        # lifecycle hack is gone).
        self.assertIn("view.getSimulationPass()", FOLLOW_CONTROLLER)
        self.assertIn("self._view.getNozzleNode()", FOLLOW_PASS)

    def test_optional_qt_timeout_api_is_capability_guarded(self):
        for source in (PLUGIN, CLIENT, MACHINE_ACTION, MONITOR_MODEL):
            occurrences = [
                match.start()
                for match in re.finditer(r"\.setTransferTimeout\(", source)
            ]
            for pos in occurrences:
                preceding = source[max(0, pos - 180) : pos]
                self.assertIn('hasattr(request, "setTransferTimeout")', preceding)

    def test_no_qt5_or_websocket_dependency_is_reintroduced(self):
        combined = "\n".join(
            (
                PLUGIN,
                CLIENT,
                MONITOR_MODEL,
                MACHINE_ACTION,
                CONFIG_QML,
                MONITOR_QML,
                UPLOAD_QML,
                ACTION_QML,
                EMPTY_QML,
            )
        )
        self.assertNotIn("PyQt5", combined)
        self.assertNotIn("QtWebSockets", combined)
        self.assertNotIn("QWebSocket", combined)

    def test_qml_imports_are_qt6_2_compatible(self):
        for name in QML_FILES:
            self.assertRegex(QML_SOURCES[name], r"^import QtQuick 2\.15", name)
        for qml in (CONFIG_QML, MONITOR_QML, UPLOAD_QML):
            self.assertIn("import QtQuick.Controls 2.15", qml)

    def test_monitor_uses_cura_network_mjpeg_component(self):
        # The image lives in the camera card (CameraPane.qml): the
        # negative guards follow it there, or they pass vacuously.
        self.assertIn("Cura.NetworkMJPGImage", CAMERA_PANE_QML)
        self.assertNotIn("WebEngine", CAMERA_PANE_QML)
        self.assertNotIn("VideoOutput", CAMERA_PANE_QML)


if __name__ == "__main__":
    unittest.main()
