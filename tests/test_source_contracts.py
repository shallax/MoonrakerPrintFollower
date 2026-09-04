import json
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = (ROOT / "plugins" / "MoonrakerPrintFollower.py").read_text()
DOWNLOAD = (ROOT / "plugins" / "DownloadStream.py").read_text()
CLIENT = (ROOT / "plugins" / "MoonrakerClient.py").read_text()
INDEX = (ROOT / "plugins" / "GCodeIndex.py").read_text()
QML_ACTION = (ROOT / "plugins" / "PreviewActionPanelControls.qml").read_text()
QML_EMPTY = (ROOT / "plugins" / "EmptyPreviewLoadButton.qml").read_text()
CURA_ADAPTER = (ROOT / "plugins" / "CuraAdapter.py").read_text()


class SourceContractTests(unittest.TestCase):
    def test_known_good_confirmation_and_public_cura_loader_are_retained(self):
        self.assertIn("QMessageBox.question", PLUGIN)
        self.assertIn("self._application.readLocalFile", PLUGIN)
        self.assertIn("add_to_recent_files=False", PLUGIN)
        self.assertNotIn("_readMeshFinished", PLUGIN)

    def test_streaming_download_contract(self):
        self.assertIn("readyRead.connect", PLUGIN)
        self.assertIn("setReadBufferSize(4 * 1024 * 1024)", PLUGIN)
        self.assertIn("DownloadTarget.open", PLUGIN)
        self.assertNotIn("data = bytes(reply.readAll())", PLUGIN)
        self.assertNotIn("fsync", DOWNLOAD)

    def test_structural_scene_and_backend_done_contract(self):
        self.assertIn("childrenChanged.connect", PLUGIN)
        self.assertIn("backendStateChange", PLUGIN)
        self.assertIn("BackendState.Done", PLUGIN)
        self.assertNotIn("DepthFirstIterator", PLUGIN)
        self.assertNotIn("_scene_structure_signature", PLUGIN)
        self.assertNotIn("printDurationMessage", PLUGIN)

    def test_motion_report_refinement_contract(self):
        self.assertIn("motion_report", PLUGIN)
        self.assertIn("live_position_in_gcode_space", PLUGIN)
        self.assertIn("refined_fraction", PLUGIN)

    def test_manual_load_is_only_explicit_cura_load(self):
        # There should be one call site that opens the remote G-code in Cura,
        # under the explicit forced-load routine.
        self.assertEqual(PLUGIN.count("self._application.readLocalFile"), 1)
        self.assertIn("def _load_cached_remote_gcode_forced", PLUGIN)

    def test_manual_preview_override_cannot_be_masked_by_fast_polling(self):
        # Manual slider changes must not be hidden behind a rolling time-based
        # suppression window. Plugin-originated writes are identified explicitly
        # by _applying_follow_update, while the watcher remains active even when
        # Cura's layer/path signals are connected.
        self.assertNotIn("_manual_view_ignore_until", PLUGIN)
        self.assertIn("if self._applying_follow_update:", PLUGIN)
        self.assertIn("self._manual_view_watch_timer.start()", PLUGIN)
        self.assertNotIn("and not self._manual_view_signals_connected", PLUGIN)
        self.assertIn("currentLayerNumChanged", PLUGIN)
        self.assertIn("currentPathNumChanged", PLUGIN)
        self.assertIn("getMinimumLayer", PLUGIN)
        self.assertIn("getMinimumPath", PLUGIN)
        self.assertIn("preview_override_kind", PLUGIN)
        # Never absorb an arbitrary user position as a new baseline. Only the
        # follower write/resume path may arm the expected Preview position.
        self.assertNotIn("self._expected_follow_layer = current_layer", PLUGIN)

    def test_preview_only_controls(self):
        self.assertIn("previewStageActive", QML_ACTION)
        self.assertIn("previewStageActive", QML_EMPTY)
        self.assertIn('text: "Load print"', QML_ACTION)
        self.assertIn('text: "Load print"', QML_EMPTY)
        self.assertIn('base.followingPaused ? "Resume" : "Pause"', QML_ACTION)
        self.assertNotIn('base.followingPaused ? "Resume" : "Pause"', QML_EMPTY)
        self.assertNotIn("HTTP fallback", QML_ACTION)
        self.assertNotIn("Following live print", QML_ACTION)

    def test_metadata_and_version(self):
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((ROOT / "plugins" / "plugin.json").read_text())
        self.assertEqual(package["package_version"], "1.1.0")
        self.assertEqual(package["sdk_version"], "8.12.0")
        self.assertEqual(package["website"], "https://github.com/shallax/MoonrakerPrintFollower")
        self.assertEqual(package["author"]["display_name"], "shallax")
        self.assertEqual(package["author"]["email"], "moonrakerprintfollower@maintain.contact")
        self.assertEqual(plugin["version"], "1.1.0")
        self.assertEqual(plugin["author"], "shallax")
        self.assertEqual(plugin["supported_sdk_versions"], ["8.12.0"])

    def test_high_risk_logic_is_split_into_modules(self):
        for name in (
            "Core.py", "DownloadStream.py", "GCodeIndex.py", "MoonrakerProtocol.py",
            "PrinterConfig.py", "MoonrakerClient.py", "FollowController.py",
            "CuraAdapter.py",
        ):
            self.assertTrue((ROOT / "plugins" / name).is_file(), name)

    def test_1_1_http_polling_and_capability_contract(self):
        self.assertNotIn("QWebSocket", CLIENT)
        self.assertNotIn("websocket", CLIENT.lower())
        self.assertIn("RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)", CLIENT)
        self.assertIn("QNetworkAccessManager", CLIENT)
        self.assertIn("self._poll_timer.timeout.connect(self.force_refresh)", CLIENT)
        self.assertIn("capabilitiesChanged", CLIENT)
        self.assertIn("_on_client_capabilities_changed", PLUGIN)
        self.assertIn("objects_list_endpoint", PLUGIN)
        self.assertNotIn("HTTP fallback", PLUGIN)

    def test_1_1_per_printer_and_follow_mode_contract(self):
        self.assertIn("PrinterConfigStore", PLUGIN)
        self.assertIn("globalContainerStackChanged", PLUGIN)
        self.assertIn("FollowController", PLUGIN)
        self.assertIn("FollowMode.WINDOW", PLUGIN)
        self.assertIn("Resume", QML_ACTION)


    def test_1_1_preview_controls_stay_compact(self):
        self.assertNotIn("Load current print", QML_ACTION)
        self.assertNotIn("Pause following", QML_ACTION)
        self.assertIn('return "Following"', PLUGIN)
        self.assertIn('return "Paused"', PLUGIN)
        self.assertIn('property real contentWidth:', QML_ACTION)
        self.assertIn('property real followButtonWidth:', QML_ACTION)
        self.assertIn('property real loadButtonWidth:', QML_ACTION)
        self.assertIn('Column\n        {\n            id: contentColumn', QML_ACTION)
        self.assertIn('id: followerTitle', QML_ACTION)
        self.assertIn('id: followerStatus', QML_ACTION)
        self.assertIn('id: buttons', QML_ACTION)

    def test_1_1_preview_card_has_native_title_status_icons_and_bottom_alignment(self):
        for qml in (QML_ACTION, QML_EMPTY):
            self.assertIn('text: "Moonraker Print Follower"', qml)
            self.assertIn('source: UM.Theme.getIcon("Nozzle")', qml)
            self.assertIn('property string statusIconName: "Information"', qml)
            self.assertIn('source: UM.Theme.getIcon(base.statusIconName)', qml)
            self.assertIn('font: UM.Theme.getFont("medium_bold")', qml)
        self.assertIn('anchors.verticalCenterOffset: (2 * base.verticalPadding) - (height / 2)', QML_ACTION)
        self.assertIn('return "CheckCircle"', PLUGIN)
        self.assertIn('return "Clock"', PLUGIN)
        self.assertIn('return "CancelCircle"', PLUGIN)
        self.assertIn('controls.setProperty("statusIconName", status_icon_name)', PLUGIN)

    def test_1_1_preview_card_has_title_headroom(self):
        self.assertIn("Math.max(260 * screenScaleFactor", QML_ACTION)
        self.assertIn("property real contentWidth: 260 * screenScaleFactor", QML_EMPTY)

    def test_1_1_preview_card_has_explicit_left_gutter(self):
        self.assertIn('property real externalGap: UM.Theme.getSize("default_margin").width', QML_ACTION)
        self.assertIn('width: visible ? externalGap + followerPanel.width : 0', QML_ACTION)
        self.assertIn('anchors.right: parent.right', QML_ACTION)

    def test_1_1_preview_controls_use_cura_action_panel_card_styling(self):
        # Keep the follower visually separated from other saveButton extensions
        # (notably Cura's Post Processing </> button) using the same theme
        # primitives as Cura's own ActionPanelWidget.
        for qml in (QML_ACTION, QML_EMPTY):
            self.assertIn('UM.Theme.getColor("main_background")', qml)
            self.assertIn('UM.Theme.getColor("lining")', qml)
            self.assertIn('UM.Theme.getSize("default_lining")', qml)
            self.assertIn('UM.Theme.getSize("default_radius")', qml)
            self.assertIn('UM.Theme.getSize("thick_margin")', qml)
        self.assertTrue(QML_ACTION.lstrip().startswith("import QtQuick"))
        self.assertIn("Item\n{\n    id: base", QML_ACTION)
        self.assertIn("Rectangle\n    {\n        id: followerPanel", QML_ACTION)

    def test_1_1_startup_does_not_force_lazy_machine_manager(self):
        # Cura 5.13 creates MachineManager lazily. Extension construction happens
        # before Cura initializes its i18n catalog, so getMachineManager() from a
        # plugin constructor can schedule active-machine restoration too early and
        # crash Cura in setGlobalContainerStack(). Printer identity must therefore
        # use only the already-established global container stack.
        self.assertNotIn("application.getMachineManager(", CURA_ADAPTER)
        self.assertNotIn("self._application.getMachineManager(", PLUGIN)
        self.assertIn("getGlobalContainerStack", CURA_ADAPTER)

    def test_1_1_has_no_printer_discovery_runtime(self):
        self.assertFalse((ROOT / "plugins" / "Discovery.py").exists())
        self.assertNotIn("discover_moonraker", PLUGIN)
        self.assertNotIn("_moonraker._tcp.local.", PLUGIN)
        self.assertNotIn("Discovered", PLUGIN)
        self.assertNotIn("Rescan", PLUGIN)
        package = json.loads((ROOT / "package.json").read_text())
        self.assertNotIn("discovery", package["description"].lower())

    def test_1_1_large_file_contract(self):
        self.assertIn("_LARGE_FILE_COMPACT_THRESHOLD", INDEX)
        self.assertIn("hydrate_layer_from_file", INDEX)
        self.assertIn(";LAYER_CHANGE", INDEX)
        self.assertIn("layer\\s+num/total_layer_count", INDEX)

    def test_source_tree_has_no_license_file(self):
        self.assertFalse(any(p.name.lower().startswith("license") for p in ROOT.rglob("*") if p.is_file()))


if __name__ == "__main__":
    unittest.main()
