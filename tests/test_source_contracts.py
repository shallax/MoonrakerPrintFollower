import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

FACADE = (PLUGINS / "MoonrakerPrintFollower.py").read_text()
COORDINATOR = (PLUGINS / "FollowerCoordinator.py").read_text()
RUNTIME = (PLUGINS / "FollowerRuntime.py").read_text()
FOLLOWER_RUNTIME_FILES = (
    "FollowerBootstrap.py",
    "FollowerConfiguration.py",
    "CuraLifecycleRuntime.py",
    "CuraViewBridge.py",
    "CuraFileLifecycle.py",
    "PreviewFollowerRuntime.py",
    "PreviewStatus.py",
    "PreviewEta.py",
    "PreviewControls.py",
    "PreviewLoad.py",
    "PreviewFollowEngine.py",
    "PathFollowEngine.py",
    "GCodeIndexRuntime.py",
    "RemoteFileTransfer.py",
)
FOLLOWER_IMPLEMENTATION = "\n".join(
    [FACADE, COORDINATOR, RUNTIME]
    + [(PLUGINS / name).read_text() for name in FOLLOWER_RUNTIME_FILES]
    + [(PLUGINS / "FollowerTransport.py").read_text()]
)
CLIENT = (PLUGINS / "MoonrakerClient.py").read_text()
SESSION = (PLUGINS / "MoonrakerSession.py").read_text()
TRANSPORT = (PLUGINS / "MoonrakerTransport.py").read_text()
MONITOR = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
OUTPUT = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
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
        ) + FOLLOWER_RUNTIME_FILES:
            self.assertTrue((PLUGINS / name).is_file(), name)
        for obsolete in (
            "PauseScheduler.py",
            "PreviewController.py",
            "PrintTracker.py",
            "GCodeRepository.py",
            "FollowerSession.py",
            "FollowerStateBridge.py",
            "MoonrakerMonitorSession.py",
            "MoonrakerOutputSession.py",
            "NativeNozzleFallback.py",
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
            "minimum_fraction=tracking.path_fraction",
            "currentLayerNumChanged",
            "currentPathNumChanged",
            "keep_native_nozzle_visible(view)",
        ):
            self.assertIn(token, FOLLOWER_IMPLEMENTATION, token)
        self.assertNotIn("_readMeshFinished", FOLLOWER_IMPLEMENTATION)
        self.assertNotIn("DepthFirstIterator", FOLLOWER_IMPLEMENTATION)

    def test_focused_runtime_has_no_shadow_http_stack(self):
        runtime_layer = "\n".join(
            [RUNTIME] + [(PLUGINS / name).read_text() for name in FOLLOWER_RUNTIME_FILES]
        )
        self.assertNotIn("QNetworkAccessManager", runtime_layer)
        self.assertNotIn("QNetworkRequest", runtime_layer)
        self.assertNotIn("status_endpoint", runtime_layer)
        self.assertNotIn("metadata_endpoint", runtime_layer)
        self.assertNotIn("gcode_script_endpoint", runtime_layer)
        self.assertNotIn("download_endpoint", runtime_layer)

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

    def test_monitor_is_natively_shared_session_and_transport(self):
        self.assertIn("self._request_generation", MONITOR)
        self.assertIn("generation != self._request_generation", MONITOR)
        self.assertIn("client.force_refresh", MONITOR)
        self.assertIn("transport.send_json", MONITOR)
        self.assertNotIn("QNetworkAccessManager", MONITOR)
        self.assertNotIn("status_endpoint", MONITOR)
        self.assertIn("RequestCategory.AUXILIARY", MONITOR)
        self.assertIn("waiting for printer confirmation", MONITOR)
        self.assertFalse((PLUGINS / "MoonrakerMonitorSession.py").exists())

    def test_output_reuses_shared_transport_readiness_and_upload_lifecycle(self):
        self.assertIn('registry.getPluginObject("GCodeWriter")', OUTPUT)
        self.assertIn('registry.getPluginObject("UFPWriter")', OUTPUT)
        self.assertIn('self._request("server/files/upload")', OUTPUT)
        self.assertIn("QHttpMultiPart", OUTPUT)
        self.assertIn("transport.network.post", OUTPUT)
        self.assertIn("transport.send_json", OUTPUT)
        self.assertIn("_shared_client_ready", OUTPUT)
        self.assertIn("QTimer.singleShot", OUTPUT)
        self.assertNotIn("QNetworkAccessManager", OUTPUT)
        self.assertNotIn("sleep(", OUTPUT)
        self.assertIn("MoonrakerOutputDeviceLifecycle", OUTPUT_PLUGIN)
        self.assertFalse((PLUGINS / "MoonrakerOutputSession.py").exists())

    def test_production_modules_are_compatibility_free(self):
        production_sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in PLUGINS.glob("*.py")
        }
        for name, source in production_sources.items():
            with self.subTest(module=name):
                self.assertNotIn("except ImportError", source)
                self.assertNotIn("sys.path.insert(", source)
                self.assertNotIn("importlib.util.spec_from_file_location", source)

        follower_sources = {
            name: production_sources[name]
            for name in (
                "FollowerCoordinator.py",
                "FollowerTransport.py",
                "FollowerBootstrap.py",
            ) + FOLLOWER_RUNTIME_FILES
        }
        for legacy_alias in (
            "self._remote_job_key",
            "self._remote_file_identity",
            "self._metadata_job_key",
            "self._remote_index_data",
            "self._remote_index_filename",
            "self._remote_index_job_key",
            "self._remote_layer_ranges",
            "self._remote_motion_offsets",
            "self._remote_current_layer_map",
            "self._remote_index_build_filename",
            "self._remote_index_build_job_key",
            "self._cached_gcode_filename",
            "self._cached_gcode_path",
            "self._cached_gcode_job_key",
            "self._following_paused",
            "self._lifecycle_generation",
            "self._force_load_requested",
            "self._force_load_pending_filename",
            "self._cura_load_in_progress",
            "self._cura_load_path",
            "self._pause_network",
            "self._file_network",
            "self._metadata_network",
            "self._last_remote_filename",
            "self._last_remote_state",
            "self._last_observed_remote_layer",
            "self._last_extruder_position",
            "self._preview_switched_for_job",
            "self._toolhead_path_valid",
            "self._last_source",
        ):
            offenders = [
                name for name, source in follower_sources.items()
                if legacy_alias in source
            ]
            self.assertEqual(offenders, [], f"{legacy_alias}: {offenders}")

    def test_follower_transport_owns_transient_request_state(self):
        bootstrap = (PLUGINS / "FollowerBootstrap.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
        index_runtime = (PLUGINS / "GCodeIndexRuntime.py").read_text(encoding="utf-8")

        for token in (
            "_file_reply",
            "_file_reply_filename",
            "_file_reply_job_key",
            "_file_download_target",
            "_metadata_filename",
        ):
            self.assertNotIn(token, bootstrap, token)

        self.assertIn("def __init__(self, application) -> None:", transport)
        self.assertIn("super().__init__(application)", transport)
        self.assertIn("self._metadata_filename: Optional[str] = None", transport)
        self.assertIn("self._file_reply = None", transport)
        self.assertIn("self._metadata_filename == filename", index_runtime)
        self.assertNotIn("_init_follower_transport", COORDINATOR)

        for removed in (
            "_PendingMarker",
            "self._metadata_reply",
            "self._metadata_reply_generation",
            "self._metadata_reply_job_key",
            "self._file_reply_generation",
            "_init_follower_transport",
        ):
            self.assertNotIn(removed, FOLLOWER_IMPLEMENTATION, removed)

    def test_preview_service_owns_live_tracking_and_print_runtime_state(self):
        preview_service = (PLUGINS / "PreviewFollowerService.py").read_text(encoding="utf-8")
        remote_job_service = (PLUGINS / "RemoteJobService.py").read_text(encoding="utf-8")
        follower_sources = {
            name: (PLUGINS / name).read_text(encoding="utf-8")
            for name in ("FollowerCoordinator.py", "FollowerBootstrap.py") + FOLLOWER_RUNTIME_FILES
        }

        self.assertIn("class PreviewTrackingState", preview_service)
        self.assertIn("class PreviewRuntimeState", preview_service)
        self.assertIn("self.tracking = PreviewTrackingState()", preview_service)
        self.assertIn("self.runtime = PreviewRuntimeState()", preview_service)
        self.assertIn("def reset_tracking(self) -> None:", preview_service)
        self.assertIn("def reset_print_state(self) -> None:", preview_service)
        self.assertIn("def update_path_fraction(self, fraction: float) -> float:", preview_service)
        self.assertIn("def observe_remote_layer(self, layer: int) -> None:", preview_service)
        self.assertIn("self._preview_follower_service.reset_tracking()", COORDINATOR)
        self.assertIn("self._preview_follower_service.reset_print_state()", COORDINATOR)

        self.assertIn("observation: Optional[PrintObservation]", remote_job_service)
        self.assertIn("def printer_state(self) -> str:", remote_job_service)
        self.assertIn("def filename(self) -> str:", remote_job_service)
        self.assertNotIn("previous_state:", remote_job_service)

        for removed_field in (
            "self._path_progress_layer",
            "self._path_progress_fraction",
            "self._last_resolved_remote_layer",
            "self._selected_layer_eta_text",
            "self._last_speed_factor",
            "self._eta_anchor_layer",
            "self._eta_anchor_print_duration",
            "self._eta_current_print_duration",
            "self._last_remote_filename",
            "self._last_remote_state",
            "self._last_observed_remote_layer",
            "self._last_extruder_position",
            "self._preview_switched_for_job",
            "self._toolhead_path_valid",
            "self._last_source",
        ):
            offenders = [
                name for name, source in follower_sources.items()
                if removed_field in source
            ]
            self.assertEqual(offenders, [], f"{removed_field}: {offenders}")

    def test_only_shared_transport_constructs_a_network_manager(self):
        production_sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in PLUGINS.glob("*.py")
        }
        constructors = [
            name for name, source in production_sources.items()
            if re.search(r"\bQNetworkAccessManager\s*\(", source)
        ]
        self.assertEqual(constructors, ["MoonrakerTransport.py"])

    def test_production_has_no_websocket_path_or_removed_wrapper_references(self):
        production_sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in PLUGINS.glob("*.py")
        }
        for name, source in production_sources.items():
            with self.subTest(module=name):
                self.assertNotIn("QWebSocket", source)
                self.assertNotIn("QtWebSockets", source)
                self.assertNotIn("websocket", source.lower())

        removed_names = (
            "PauseScheduler",
            "PreviewController",
            "PrintTracker",
            "GCodeRepository",
            "FollowerSession",
            "FollowerStateBridge",
            "MoonrakerMonitorSession",
            "MoonrakerOutputSession",
            "NativeNozzleFallback",
        )
        for removed in removed_names:
            offenders = [
                name for name, source in production_sources.items()
                if removed in source
            ]
            self.assertEqual(offenders, [], f"{removed}: {offenders}")

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
