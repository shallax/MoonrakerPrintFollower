import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

PLUGIN = (PLUGINS / "MoonrakerPrintFollower.py").read_text()
DOWNLOAD = (PLUGINS / "DownloadStream.py").read_text()
CLIENT = (PLUGINS / "MoonrakerClient.py").read_text()
INDEX = (PLUGINS / "GCodeIndex.py").read_text()
QML_ACTION = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
QML_EMPTY = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()
CURA_ADAPTER = (PLUGINS / "CuraAdapter.py").read_text()
NOZZLE_FALLBACK = (PLUGINS / "NativeNozzleFallback.py").read_text()
MACHINE_ACTION = (PLUGINS / "MoonrakerFollowerMachineAction.py").read_text()
QML_CONFIG = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text()
PRINTER_CONFIG = (PLUGINS / "PrinterConfig.py").read_text()
PLUGIN_INIT = (PLUGINS / "__init__.py").read_text()
OUTPUT_DEVICE = (PLUGINS / "MoonrakerOutputDevice.py").read_text()
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text()
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
QML_MONITOR = (PLUGINS / "MoonrakerMonitor.qml").read_text()
QML_UPLOAD = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
README = (ROOT / "README.md").read_text()


class SourceContractTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # Established follower behaviour that must not regress.
    # ------------------------------------------------------------------

    def test_known_good_confirmation_and_public_cura_loader_are_retained(self):
        self.assertIn("QMessageBox.question", PLUGIN)
        self.assertEqual(PLUGIN.count("self._application.readLocalFile"), 1)
        self.assertIn("add_to_recent_files=False", PLUGIN)
        self.assertIn("def _load_cached_remote_gcode_forced", PLUGIN)
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

    def test_motion_report_refinement_and_monotonic_path_contract(self):
        self.assertIn("motion_report", PLUGIN)
        self.assertIn("live_position_in_gcode_space", PLUGIN)
        self.assertIn("refined_fraction", PLUGIN)
        self.assertIn("self._path_progress_layer", PLUGIN)
        self.assertIn("self._path_progress_fraction", PLUGIN)
        self.assertIn("minimum_fraction=self._path_progress_fraction", PLUGIN)
        self.assertIn('return "hydrating layer path index"', PLUGIN)
        self.assertIn("self._ensure_remote_layer_hydrated(target_layer + 1)", PLUGIN)
        self.assertIn("minimum_fraction", INDEX)
        self.assertIn("monotonic", INDEX)

    def test_manual_preview_override_cannot_be_masked_by_polling(self):
        self.assertNotIn("_manual_view_ignore_until", PLUGIN)
        self.assertIn("if self._applying_follow_update:", PLUGIN)
        self.assertIn("self._manual_view_watch_timer.start()", PLUGIN)
        self.assertNotIn("and not self._manual_view_signals_connected", PLUGIN)
        self.assertIn("currentLayerNumChanged", PLUGIN)
        self.assertIn("currentPathNumChanged", PLUGIN)
        self.assertIn("getMinimumLayer", PLUGIN)
        self.assertIn("getMinimumPath", PLUGIN)
        self.assertIn("preview_override_kind", PLUGIN)
        self.assertNotIn("self._expected_follow_layer = current_layer", PLUGIN)

    def test_preview_controls_remain_preview_only_and_compact(self):
        self.assertIn("previewStageActive", QML_ACTION)
        self.assertIn("previewStageActive", QML_EMPTY)
        self.assertIn('text: "Load print"', QML_ACTION)
        self.assertIn('text: "Load print"', QML_EMPTY)
        self.assertIn('base.followingPaused ? "Resume" : "Pause"', QML_ACTION)
        self.assertNotIn('base.followingPaused ? "Resume" : "Pause"', QML_EMPTY)
        self.assertNotIn("HTTP fallback", QML_ACTION)
        self.assertNotIn("Following live print", QML_ACTION)
        self.assertIn('return "Following"', PLUGIN)
        self.assertIn('return "Paused"', PLUGIN)
        self.assertIn('property real contentWidth:', QML_ACTION)

    def test_native_printhead_fallback_is_retained(self):
        self.assertIn("keep_native_nozzle_visible", NOZZLE_FALLBACK)
        self.assertIn('getattr(simulation_view, "getSimulationPass", None)', NOZZLE_FALLBACK)
        self.assertIn('getattr(simulation_view, "getNozzleNode", None)', NOZZLE_FALLBACK)
        self.assertNotIn("renderer.queueNode", NOZZLE_FALLBACK)
        self.assertNotIn("RenderBatch", NOZZLE_FALLBACK)
        self.assertIn("keep_native_nozzle_visible(view)", PLUGIN)
        self.assertIn('text: "Show live printhead indicator"', QML_CONFIG)
        self.assertIn("show_toolhead_indicator", PRINTER_CONFIG)

    def test_single_active_printer_session_is_generation_guarded(self):
        start = PLUGIN.index("def _on_active_machine_changed")
        end = PLUGIN.index("def _active_printer_is_configured_for_following", start)
        block = PLUGIN[start:end]
        self.assertLess(block.index("self._client.stop()"), block.index("self._active_machine_id = machine_id"))
        self.assertLess(
            block.index('self._invalidate_lifecycle("active Cura printer changed")'),
            block.index("self._active_machine_id = machine_id"),
        )
        self.assertIn("self._generation += 1", CLIENT)
        self.assertIn("generation != self._generation", CLIENT)

    def test_http_polling_and_large_file_contracts_are_retained(self):
        self.assertNotIn("QWebSocket", CLIENT)
        self.assertNotIn("websocket", CLIENT.lower())
        self.assertIn("RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)", CLIENT)
        self.assertIn("self._poll_timer.timeout.connect(self.force_refresh)", CLIENT)
        self.assertIn("capabilitiesChanged", CLIENT)
        self.assertIn("_LARGE_FILE_COMPACT_THRESHOLD", INDEX)
        self.assertIn("hydrate_layer_from_file", INDEX)
        self.assertIn(";LAYER_CHANGE", INDEX)
        self.assertIn("layer\\s+num/total_layer_count", INDEX)

    # ------------------------------------------------------------------
    # Unified configuration and Moonraker integration.
    # ------------------------------------------------------------------

    def test_metadata_and_release_version_are_current(self):
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((PLUGINS / "plugin.json").read_text())
        self.assertEqual(package["package_version"], "3.0.0")
        self.assertEqual(package["package_id"], "Moonraker_Print_Follower")
        self.assertEqual(package["sdk_version"], "8.0.0")
        self.assertEqual(package["sdk_version_semver"], "8.0.0")
        self.assertEqual(package["website"], "https://github.com/shallax/MoonrakerPrintFollower")
        self.assertEqual(package["author"]["display_name"], "shallax")
        self.assertEqual(package["author"]["email"], "moonrakerprintfollower@maintain.contact")
        self.assertEqual(plugin["version"], "3.0.0")
        self.assertEqual(plugin["author"], "shallax")
        self.assertEqual(plugin["supported_sdk_versions"], [f"8.{minor}.0" for minor in range(13)])

    def test_unified_manage_printers_action_has_three_tabs(self):
        self.assertIn('class MoonrakerFollowerMachineAction(MachineAction)', MACHINE_ACTION)
        self.assertIn('LABEL = "Configure Moonraker"', MACHINE_ACTION)
        self.assertIn('self._qml_url = "MoonrakerFollowerConfiguration.qml"', MACHINE_ACTION)
        self.assertIn('containerAdded.connect(self._on_container_added)', MACHINE_ACTION)
        self.assertIn('getMachineActionManager().addSupportedAction', MACHINE_ACTION)
        self.assertIn('self._follower.apply_printer_config(config)', MACHINE_ACTION)
        self.assertIn('self._output_plugin.refresh()', MACHINE_ACTION)
        self.assertTrue(QML_CONFIG.lstrip().startswith('import QtQuick'))
        self.assertIn('Cura.MachineAction', QML_CONFIG)
        self.assertIn('text: "Connection"', QML_CONFIG)
        self.assertIn('text: "Following"', QML_CONFIG)
        self.assertIn('text: "Upload"', QML_CONFIG)
        self.assertIn('manager.saveConfig', QML_CONFIG)
        self.assertIn('manager.testConnection', QML_CONFIG)
        self.assertIn('settingsPowerDevices', MACHINE_ACTION)
        self.assertIn('settingsOutputFormat', MACHINE_ACTION)
        self.assertIn('settingsFrontendUrl', MACHINE_ACTION)

    def test_output_device_is_registered_with_same_follower_instance(self):
        self.assertIn("MoonrakerOutputDevicePlugin", PLUGIN_INIT)
        self.assertIn("output_plugin = MoonrakerOutputDevicePlugin(app, follower)", PLUGIN_INIT)
        self.assertIn('"output_device": output_plugin', PLUGIN_INIT)
        self.assertIn("MoonrakerFollowerMachineAction(app, follower, output_plugin)", PLUGIN_INIT)
        self.assertIn("class MoonrakerOutputDevicePlugin(OutputDevicePlugin)", OUTPUT_PLUGIN)
        self.assertIn("follower.current_printer_config()", OUTPUT_PLUGIN)
        self.assertIn("globalContainerStackChanged", OUTPUT_PLUGIN)

    def test_output_device_supports_gcode_ufp_upload_and_start_print(self):
        self.assertIn("class MoonrakerOutputDevice(PrinterOutputDevice)", OUTPUT_DEVICE)
        self.assertIn('registry.getPluginObject("GCodeWriter")', OUTPUT_DEVICE)
        self.assertIn('registry.getPluginObject("UFPWriter")', OUTPUT_DEVICE)
        self.assertIn('self._request("server/files/upload")', OUTPUT_DEVICE)
        self.assertIn("QHttpMultiPart", OUTPUT_DEVICE)
        self.assertIn('form-data; name="root"', OUTPUT_DEVICE)
        self.assertIn('form-data; name="path"', OUTPUT_DEVICE)
        self.assertIn('form-data; name="print"', OUTPUT_DEVICE)
        self.assertIn("reply.uploadProgress.connect", OUTPUT_DEVICE)
        self.assertIn("self.writeSuccess.emit(self)", OUTPUT_DEVICE)
        self.assertIn("self.writeError.emit(self)", OUTPUT_DEVICE)
        self.assertIn("QDesktopServices.openUrl", OUTPUT_DEVICE)

    def test_power_startup_and_readiness_wait_are_nonblocking(self):
        self.assertIn("machine/device_power/device?", OUTPUT_DEVICE)
        self.assertIn('self._json_request("GET", "server/info"', OUTPUT_DEVICE)
        self.assertIn("QTimer.singleShot", OUTPUT_DEVICE)
        self.assertNotIn("from time import sleep", OUTPUT_DEVICE)
        self.assertNotIn("sleep(", OUTPUT_DEVICE)
        self.assertIn("MAX_READY_ATTEMPTS", OUTPUT_DEVICE)

    def test_filename_translation_and_upload_dialog_are_integrated(self):
        self.assertIn("filename_translate_input", PRINTER_CONFIG)
        self.assertIn("filename_translate_output", PRINTER_CONFIG)
        self.assertIn("filename_translate_remove", PRINTER_CONFIG)
        self.assertIn("str.maketrans", OUTPUT_DEVICE)
        self.assertIn('title: "Upload to Moonraker"', QML_UPLOAD)
        self.assertIn("manager.uploadPathOptions", QML_UPLOAD)
        self.assertIn("manager.initialUploadFilename", QML_UPLOAD)
        self.assertIn("manager.initialStartPrint", QML_UPLOAD)
        self.assertIn("manager.acceptUpload", QML_UPLOAD)
        self.assertIn("manager.cancelUpload", QML_UPLOAD)

    def test_standalone_moonraker_connection_settings_are_migrated(self):
        self.assertIn('MOONRAKER_CONNECTION_PREF_KEY = "moonraker/instances"', PRINTER_CONFIG)
        self.assertIn("def migrate_moonraker_connection", PRINTER_CONFIG)
        for legacy_key in (
            "frontend_url", "output_format", "upload_dialog", "upload_path",
            "upload_pathes", "upload_start_print_job", "upload_remember_state",
            "upload_autohide_messagebox", "power_device", "retry_interval",
            "trans_input", "trans_output", "trans_remove", "camera_url",
            "camera_image_rotation", "camera_image_mirror",
        ):
            self.assertIn(legacy_key, PRINTER_CONFIG)
        self.assertIn("migrate_moonraker_connection()", PLUGIN_INIT)

    def test_monitor_reuses_follower_status_and_discovers_moonraker_webcams(self):
        self.assertIn("class MoonrakerMonitorModel(PrinterOutputModel)", MONITOR_MODEL)
        self.assertIn('getattr(follower, "_client", None)', MONITOR_MODEL)
        self.assertIn("status_signal.connect(self.updateMoonrakerStatus)", MONITOR_MODEL)
        self.assertIn('"server/webcams/list"', MONITOR_MODEL)
        self.assertIn("camera_rotation", PRINTER_CONFIG)
        self.assertIn("camera_mirror", PRINTER_CONFIG)
        self.assertIn("MoonrakerMonitorModel", OUTPUT_PLUGIN)
        self.assertIn("_monitor_view_qml_path", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitor.qml"', OUTPUT_PLUGIN)
        self.assertIn("Cura.NetworkMJPGImage", QML_MONITOR)
        self.assertIn("webcamNames", QML_MONITOR)
        self.assertIn("selectWebcam", QML_MONITOR)
        self.assertIn("monitorProgress", QML_MONITOR)
        self.assertIn("monitorLayer", QML_MONITOR)
        self.assertIn("monitorElapsed", QML_MONITOR)
        self.assertIn("monitorSpeed", QML_MONITOR)
        self.assertIn("monitorFlow", QML_MONITOR)
        self.assertIn("monitorPosition", QML_MONITOR)
        self.assertIn("openFrontend", QML_MONITOR)

    def test_output_controller_does_not_advertise_unimplemented_controls(self):
        for flag in (
            "can_pause", "can_abort", "can_pre_heat_bed", "can_pre_heat_hotends",
            "can_send_raw_gcode", "can_control_manually", "can_update_firmware",
        ):
            self.assertIn(f"self.{flag} = False", OUTPUT_DEVICE)

    # ------------------------------------------------------------------
    # Packaging / privacy / startup safety.
    # ------------------------------------------------------------------

    def test_high_risk_logic_is_split_into_modules(self):
        for name in (
            "Core.py", "DownloadStream.py", "GCodeIndex.py", "MoonrakerProtocol.py",
            "PrinterConfig.py", "MoonrakerClient.py", "FollowController.py",
            "CuraAdapter.py", "MoonrakerFollowerMachineAction.py",
            "MoonrakerOutputDevice.py", "MoonrakerOutputDeviceLifecycle.py",
            "MoonrakerOutputDevicePlugin.py", "MoonrakerMonitorModel.py",
            "MoonrakerMonitorRuntime.py", "MoonrakerMonitorControls.py",
            "MoonrakerMonitorTypedControls.py", "MoonrakerMonitor.qml",
            "MoonrakerMonitorDashboard.qml",
        ):
            self.assertTrue((PLUGINS / name).is_file(), name)

    def test_no_extensions_menu_or_settings_qdialog(self):
        self.assertNotIn('setMenuName(', PLUGIN)
        self.assertNotIn('addMenuItem(', PLUGIN)
        self.assertNotIn('show_configuration_dialog', PLUGIN)
        self.assertNotIn('QDialog', PLUGIN)
        self.assertNotIn('QLineEdit', PLUGIN)
        self.assertNotIn('QCheckBox', PLUGIN)

    def test_startup_does_not_force_lazy_machine_manager(self):
        self.assertNotIn("application.getMachineManager(", CURA_ADAPTER)
        self.assertNotIn("self._application.getMachineManager(", PLUGIN)
        self.assertIn("getGlobalContainerStack", CURA_ADAPTER)

    def test_no_printer_discovery_runtime(self):
        self.assertFalse((PLUGINS / "Discovery.py").exists())
        self.assertNotIn("discover_moonraker", PLUGIN)
        self.assertNotIn("_moonraker._tcp." + "local.", PLUGIN)
        package = json.loads((ROOT / "package.json").read_text())
        self.assertNotIn("discovery", package["description"].lower())

    def test_source_uses_only_generic_printer_examples(self):
        text_files = [
            path for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"}
        ]
        combined = "\n".join(path.read_text(errors="replace") for path in text_files)
        self.assertNotIn("vo" + "ron", combined.lower())
        self.assertIsNone(re.search(r"\b(?:10|127)\.\d+\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"\b192\.168\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b", combined))
        urls = re.findall(r"https?://[^\s\"'<>`)]+", combined)
        allowed_public = "https://github.com/shallax/MoonrakerPrintFollower"
        for url in urls:
            if url == allowed_public or url in {"http://", "https://"} or "{" in url:
                continue
            self.assertIn(".invalid", url, url)

    def test_source_has_no_literal_sample_api_key(self):
        text_files = [
            path for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"}
        ]
        combined = "\n".join(path.read_text(errors="replace") for path in text_files)
        self.assertIsNone(re.search(r"api_key\s*[=:]\s*[\"'][^\"']+[\"']", combined, re.IGNORECASE))

    def test_marketplace_package_id_is_canonical(self):
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(package["package_id"], "Moonraker_Print_Follower")
        self.assertIn('PLUGIN_ID = "Moonraker_Print_Follower"', PLUGIN)
        self.assertNotIn("getPluginPath(self.PLUGIN_ID)", PLUGIN)
        self.assertIn("os.path.dirname(os.path.abspath(__file__))", PLUGIN)

    def test_source_tree_has_no_license_file(self):
        self.assertFalse(any(
            path.name.lower().startswith("license")
            for path in ROOT.rglob("*") if path.is_file()
        ))


if __name__ == "__main__":
    unittest.main()
