import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

FACADE = (PLUGINS / "MoonrakerPrintFollower.py").read_text()
COORDINATOR = (PLUGINS / "FollowerCoordinator.py").read_text()
RUNTIME = (PLUGINS / "FollowerRuntime.py").read_text()
CLIENT = (PLUGINS / "MoonrakerClient.py").read_text()
SESSION = (PLUGINS / "MoonrakerSession.py").read_text()
TRANSPORT = (PLUGINS / "MoonrakerTransport.py").read_text()
MONITOR = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MONITOR_SESSION = (PLUGINS / "MoonrakerMonitorSession.py").read_text()
OUTPUT = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
OUTPUT_SESSION = (PLUGINS / "MoonrakerOutputSession.py").read_text()
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text()
QML_ACTION = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
QML_EMPTY = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()


class SourceContractTests(unittest.TestCase):
    def test_release_metadata_is_v31_and_package_id_is_canonical(self):
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((PLUGINS / "plugin.json").read_text())
        self.assertEqual(package["package_version"], "3.1.0")
        self.assertEqual(plugin["version"], "3.1.0")
        self.assertEqual(package["package_id"], "Moonraker_Print_Follower")
        self.assertEqual(package["website"], "https://github.com/shallax/MoonrakerPrintFollower")
        self.assertEqual(package["author"]["display_name"], "shallax")
        self.assertEqual(package["author"]["email"], "moonrakerprintfollower@maintain.contact")
        self.assertEqual(plugin["supported_sdk_versions"], [f"8.{minor}.0" for minor in range(13)])

    def test_public_follower_is_thin_and_domains_are_extracted_once(self):
        self.assertLess(len(FACADE.splitlines()), 20)
        self.assertIn("FollowerCoordinator", FACADE)
        for name in (
            "FollowerRuntime.py",
            "FollowerCoordinator.py",
            "RemoteJobService.py",
            "RemoteFileService.py",
            "GCodeIndexService.py",
            "PauseScheduleService.py",
            "PreviewFollowerService.py",
            "CuraLifecycleBridge.py",
            "FollowerTransport.py",
            "MoonrakerSession.py",
            "MoonrakerTransport.py",
        ):
            self.assertTrue((PLUGINS / name).is_file(), name)
        for obsolete in (
            "PauseScheduler.py",
            "PreviewController.py",
            "PrintTracker.py",
            "GCodeRepository.py",
            "FollowerSession.py",
            "FollowerStateBridge.py",
        ):
            self.assertFalse((PLUGINS / obsolete).exists(), obsolete)
        for token in (
            "RemoteJobService",
            "RemoteFileService",
            "GCodeIndexService",
            "PauseScheduleService",
            "PreviewFollowerService",
            "CuraLifecycleBridge",
        ):
            self.assertIn(token, COORDINATOR)

    def test_established_follower_safety_contracts_remain_available(self):
        combined = RUNTIME + "\n" + (PLUGINS / "FollowerTransport.py").read_text()
        for token in (
            "QMessageBox.question",
            "self._application.readLocalFile",
            "add_to_recent_files=False",
            "def _load_cached_remote_gcode_forced",
            "readyRead.connect",
            "setReadBufferSize(4 * 1024 * 1024)",
            "childrenChanged.connect",
            "BackendState.Done",
            "motion_report",
            "minimum_fraction=self._path_progress_fraction",
            "currentLayerNumChanged",
            "currentPathNumChanged",
            "keep_native_nozzle_visible(view)",
        ):
            self.assertIn(token, combined, token)
        self.assertNotIn("_readMeshFinished", RUNTIME)
        self.assertNotIn("DepthFirstIterator", RUNTIME)

    def test_shared_session_is_http_only_generation_guarded_coalesced_and_observable(self):
        self.assertNotIn("QWebSocket", CLIENT)
        self.assertNotIn("websocket", CLIENT.lower())
        self.assertIn("MoonrakerSession", CLIENT)
        self.assertIn("RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)", CLIENT)
        self.assertIn("generation != self._generation", CLIENT)
        self.assertIn("self._session.coalescer.begin", CLIENT)
        self.assertIn("class MoonrakerSession", SESSION)
        self.assertIn("RequestCoalescer", SESSION)
        self.assertIn("PollPolicy", SESSION)
        self.assertIn("CommandTracker", SESSION)
        self.assertIn("SessionSnapshot", SESSION)
        self.assertIn("TransportMetrics", TRANSPORT)
        self.assertIn("request_id", TRANSPORT)
        self.assertIn("elapsed_ms", TRANSPORT)

    def test_monitor_uses_shared_core_and_shared_transport(self):
        self.assertIn("self._request_generation", MONITOR)
        self.assertIn("generation != self._request_generation", MONITOR)
        self.assertIn("self._core_timer.stop()", MONITOR_SESSION)
        self.assertIn("client.force_refresh", MONITOR_SESSION)
        self.assertIn("transport.send_json", MONITOR_SESSION)
        self.assertNotIn("status_endpoint", MONITOR_SESSION)
        self.assertIn("RequestCategory.AUXILIARY", MONITOR_SESSION)
        self.assertIn("waiting for printer confirmation", MONITOR_SESSION)

    def test_output_reuses_shared_transport_readiness_and_upload_lifecycle(self):
        self.assertIn('registry.getPluginObject("GCodeWriter")', OUTPUT)
        self.assertIn('registry.getPluginObject("UFPWriter")', OUTPUT)
        self.assertIn('self._request("server/files/upload")', OUTPUT)
        self.assertIn("QHttpMultiPart", OUTPUT)
        self.assertIn("QTimer.singleShot", OUTPUT)
        self.assertNotIn("sleep(", OUTPUT)
        self.assertIn("_shared_client_ready", OUTPUT_SESSION)
        self.assertIn("transport.send_json", OUTPUT_SESSION)
        self.assertIn("MoonrakerOutputSession", OUTPUT_PLUGIN)

    def test_preview_controls_remain_preview_only(self):
        self.assertIn("previewStageActive", QML_ACTION)
        self.assertIn("previewStageActive", QML_EMPTY)
        self.assertIn('text: "Load current print"', QML_ACTION)
        self.assertIn('base.followingPaused ? "Attach" : "Detach"', QML_ACTION)
        self.assertNotIn('base.followingPaused ? "Attach" : "Detach"', QML_EMPTY)
        self.assertNotIn("HTTP fallback", QML_ACTION)

    def test_startup_and_packaging_invariants(self):
        init_source = (PLUGINS / "__init__.py").read_text()
        self.assertIn("MoonrakerOutputDevicePlugin", init_source)
        self.assertIn("output_plugin = MoonrakerOutputDevicePlugin(app, follower)", init_source)
        self.assertTrue((ROOT / "LICENSE").is_file())
        license_text = (ROOT / "LICENSE").read_text()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)

    def test_source_contains_no_private_network_examples_or_literal_api_key(self):
        text_files = [
            path for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"}
        ]
        combined = "\n".join(path.read_text(errors="replace") for path in text_files)
        self.assertIsNone(re.search(r"\b(?:10|127)\.\d+\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"\b192\.168\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"api_key\s*[=:]\s*[\"'][^\"']+[\"']", combined, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
