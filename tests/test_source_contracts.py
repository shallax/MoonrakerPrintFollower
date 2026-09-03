import json
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = (ROOT / "plugins" / "MoonrakerPrintFollower.py").read_text()
DOWNLOAD = (ROOT / "plugins" / "DownloadStream.py").read_text()
QML_ACTION = (ROOT / "plugins" / "PreviewActionPanelControls.qml").read_text()
QML_EMPTY = (ROOT / "plugins" / "EmptyPreviewLoadButton.qml").read_text()


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
        self.assertIn("Load current print", QML_ACTION)
        self.assertIn("Load current print", QML_EMPTY)
        self.assertIn("Pause following", QML_ACTION)
        self.assertNotIn("Pause following", QML_EMPTY)

    def test_metadata_and_version(self):
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((ROOT / "plugins" / "plugin.json").read_text())
        self.assertEqual(package["package_version"], "1.0.3")
        self.assertEqual(package["sdk_version"], "8.12.0")
        self.assertEqual(package["website"], "https://github.com/shallax/MoonrakerPrintFollower")
        self.assertEqual(package["author"]["display_name"], "shallax")
        self.assertEqual(package["author"]["email"], "moonrakerprintfollower@maintain.contact")
        self.assertEqual(plugin["version"], "1.0.3")
        self.assertEqual(plugin["author"], "shallax")
        self.assertEqual(plugin["supported_sdk_versions"], ["8.12.0"])

    def test_high_risk_logic_is_split_into_modules(self):
        for name in ("Core.py", "DownloadStream.py", "GCodeIndex.py", "MoonrakerProtocol.py"):
            self.assertTrue((ROOT / "plugins" / name).is_file(), name)

    def test_source_tree_has_no_license_file(self):
        self.assertFalse(any(p.name.lower().startswith("license") for p in ROOT.rglob("*") if p.is_file()))


if __name__ == "__main__":
    unittest.main()
