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
TOOLHEAD = (ROOT / "plugins" / "ToolheadIndicator.py").read_text()
MACHINE_ACTION = (ROOT / "plugins" / "MoonrakerFollowerMachineAction.py").read_text()
QML_CONFIG = (ROOT / "plugins" / "MoonrakerFollowerConfiguration.qml").read_text()
PRINTER_CONFIG = (ROOT / "plugins" / "PrinterConfig.py").read_text()
PLUGIN_INIT = (ROOT / "plugins" / "__init__.py").read_text()
README = (ROOT / "README.md").read_text()


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
        self.assertEqual(package["package_version"], "2.0.0")
        self.assertEqual(package["sdk_version"], "8.12.0")
        self.assertEqual(package["website"], "https://github.com/shallax/MoonrakerPrintFollower")
        self.assertEqual(package["author"]["display_name"], "shallax")
        self.assertEqual(package["author"]["email"], "moonrakerprintfollower@maintain.contact")
        self.assertEqual(plugin["version"], "2.0.0")
        self.assertEqual(plugin["author"], "shallax")
        self.assertEqual(plugin["supported_sdk_versions"], ["8.12.0"])

    def test_2_0_configuration_is_a_native_qml_machine_action(self):
        self.assertIn('class MoonrakerFollowerMachineAction(MachineAction)', MACHINE_ACTION)
        self.assertIn('LABEL = "Configure Moonraker Follower"', MACHINE_ACTION)
        self.assertIn('self._qml_url = "MoonrakerFollowerConfiguration.qml"', MACHINE_ACTION)
        self.assertNotIn('self._open_as_dialog = False', MACHINE_ACTION)
        self.assertIn('containerAdded.connect(self._on_container_added)', MACHINE_ACTION)
        self.assertIn('getMachineActionManager().addSupportedAction', MACHINE_ACTION)
        self.assertIn('container.getMetaDataEntry("type") != "machine"', MACHINE_ACTION)
        self.assertIn('self._follower.apply_printer_config(config)', MACHINE_ACTION)
        self.assertIn('"machine_action": MoonrakerFollowerMachineAction(app, follower)', PLUGIN_INIT)
        self.assertTrue(QML_CONFIG.lstrip().startswith('import QtQuick'))
        self.assertIn('Cura.MachineAction', QML_CONFIG)
        self.assertIn('text: "Connection"', QML_CONFIG)
        self.assertIn('text: "Following"', QML_CONFIG)
        self.assertIn('manager.saveConfig', QML_CONFIG)
        self.assertIn('manager.testConnection', QML_CONFIG)
        self.assertIn('actionDialog.close()', QML_CONFIG)
        self.assertNotIn('self._application.getMachineManager(', MACHINE_ACTION)

    def test_2_0_has_no_extensions_menu_or_settings_qdialog(self):
        self.assertNotIn('setMenuName(', PLUGIN)
        self.assertNotIn('addMenuItem(', PLUGIN)
        self.assertNotIn('show_configuration_dialog', PLUGIN)
        self.assertNotIn('QDialog', PLUGIN)
        self.assertNotIn('QLineEdit', PLUGIN)
        self.assertNotIn('QCheckBox', PLUGIN)
        self.assertIn('from PyQt6.QtWidgets import QMessageBox', PLUGIN)

    def test_high_risk_logic_is_split_into_modules(self):
        for name in (
            "Core.py", "DownloadStream.py", "GCodeIndex.py", "MoonrakerProtocol.py",
            "PrinterConfig.py", "MoonrakerClient.py", "FollowController.py",
            "CuraAdapter.py", "MoonrakerFollowerMachineAction.py",
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
        self.assertIn("objects_list_endpoint", MACHINE_ACTION)
        self.assertNotIn("HTTP fallback", PLUGIN)

    def test_1_1_per_printer_and_follow_mode_contract(self):
        self.assertIn("PrinterConfigStore", PLUGIN)
        self.assertIn("globalContainerStackChanged", PLUGIN)
        self.assertIn("FollowController", PLUGIN)
        self.assertIn('text: "Window around current layer (±2)"', QML_CONFIG)
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
        self.assertNotIn("_moonraker._tcp." + "local.", PLUGIN)
        self.assertNotIn("Discovered", PLUGIN)
        self.assertNotIn("Rescan", PLUGIN)
        package = json.loads((ROOT / "package.json").read_text())
        self.assertNotIn("discovery", package["description"].lower())

    def test_1_1_large_file_contract(self):
        self.assertIn("_LARGE_FILE_COMPACT_THRESHOLD", INDEX)
        self.assertIn("hydrate_layer_from_file", INDEX)
        self.assertIn(";LAYER_CHANGE", INDEX)
        self.assertIn("layer\\s+num/total_layer_count", INDEX)

    def test_2_0_source_uses_only_generic_printer_examples(self):
        import re
        text_files = [
            path for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"}
        ]
        combined = "\n".join(path.read_text(errors="replace") for path in text_files)
        # Never embed personal/local printer identities or local-network addresses.
        self.assertNotIn("vo" + "ron", combined.lower())
        local_suffix = "." + "local"
        local_host_literal = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+" + re.escape(local_suffix.lstrip(".")) + r"(?::\d+)?\b", re.IGNORECASE)
        self.assertIsNone(local_host_literal.search(combined))
        self.assertIsNone(re.search(r"\b(?:10|127)\.\d+\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"\b192\.168\.\d+\.\d+\b", combined))
        self.assertIsNone(re.search(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b", combined))
        # URL examples must use the reserved .invalid domain. The project's own
        # public repository URL is metadata, not a printer endpoint.
        urls = re.findall(r"https?://[^\s\"'<>`)]+", combined)
        allowed_public = "https://github.com/shallax/MoonrakerPrintFollower"
        for url in urls:
            if url == allowed_public or url in {"http://", "https://"} or "{" in url:
                continue
            self.assertIn(".invalid", url, url)

    def test_2_0_source_has_no_literal_sample_api_key(self):
        import re
        text_files = [
            path for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"}
        ]
        combined = "\n".join(path.read_text(errors="replace") for path in text_files)
        # Non-empty API-key literals are forbidden; tests and docs should leave
        # the value empty rather than publishing a realistic-looking example.
        self.assertIsNone(re.search(r"api_key\s*[=:]\s*[\"'][^\"']+[\"']", combined, re.IGNORECASE))

    def test_2_0_source_bundle_includes_installable_curapackage_and_instructions(self):
        import zipfile
        package_path = ROOT / "MoonrakerPrintFollower-v2.0.0.curapackage"
        self.assertTrue(package_path.is_file())
        self.assertIn("MoonrakerPrintFollower-v2.0.0.curapackage", README)
        self.assertIn("drag `moonrakerprintfollower-v2.0.0.curapackage` onto the cura window", README.lower())
        with zipfile.ZipFile(package_path) as archive:
            names = set(archive.namelist())
        self.assertIn("files/plugins/Moonraker_Print_Follower/MoonrakerFollowerConfiguration.qml", names)
        self.assertIn("files/plugins/Moonraker_Print_Follower/MoonrakerFollowerMachineAction.py", names)
        self.assertIn("files/plugins/Moonraker_Print_Follower/ToolheadIndicator.py", names)

    def test_2_0_has_plugin_owned_printhead_fallback(self):
        self.assertTrue((ROOT / "plugins" / "ToolheadIndicator.py").is_file())
        self.assertIn("class ToolheadIndicatorNode(SceneNode)", TOOLHEAD)
        self.assertNotIn("self.setPosition(", TOOLHEAD)
        self.assertIn("preview_head_position", CURA_ADAPTER)
        self.assertIn("ToolheadIndicatorNode", PLUGIN)
        self.assertIn("_update_toolhead_indicator", PLUGIN)
        self.assertIn('text: "Show live printhead indicator"', QML_CONFIG)
        self.assertIn("settingsToolheadIndicator", MACHINE_ACTION)
        self.assertIn("show_toolhead_indicator", PRINTER_CONFIG)


    def test_2_0_single_active_printer_session_and_preview_identity(self):
        # The follower is a single-owner runtime. Switching Cura machines must
        # stop the old HTTP client and invalidate every old-machine operation
        # before assigning the new machine id.
        start = PLUGIN.index("def _on_active_machine_changed")
        end = PLUGIN.index("def _active_printer_is_configured_for_following", start)
        block = PLUGIN[start:end]
        self.assertLess(block.index("self._client.stop()"), block.index("self._active_machine_id = machine_id"))
        self.assertLess(block.index('self._invalidate_lifecycle("active Cura printer changed")'), block.index("self._active_machine_id = machine_id"))
        self.assertIn("self._active_machine_name = machine_name", block)

        # The HTTP client also generation-guards late completions from a stopped
        # session, so an old printer cannot publish after the new one starts.
        self.assertIn("self._generation += 1", CLIENT)
        self.assertIn("generation != self._generation", CLIENT)

        # Preview controls identify the active Cura printer and are absent when
        # that printer has no enabled, usable follower configuration.
        self.assertIn('controls.setProperty("activePrinterName", active_printer_name)', PLUGIN)
        self.assertIn('controls.setProperty("configuredForFollowing", configured)', PLUGIN)
        for qml in (QML_ACTION, QML_EMPTY):
            self.assertIn("property bool configuredForFollowing: false", qml)
            self.assertIn('property string activePrinterName: ""', qml)
            self.assertIn("configuredForFollowing", qml.split("visible:", 1)[1].split("\n", 1)[0])
            self.assertIn('base.activePrinterName + (base.statusText.length > 0 ? " — " + base.statusText : "")', qml)

    def test_2_0_printhead_fallback_reuses_cura_native_nozzle_model(self):
        # Do not ship a substitute halo/pin design. Reuse Cura's actual nozzle
        # mesh and theme colour, with the same nozzle.stl as a fallback source.
        self.assertIn('getattr(simulation_view, "getNozzleNode", None)', TOOLHEAD)
        self.assertIn('getPluginPath("SimulationView")', TOOLHEAD)
        self.assertIn('"resources", "nozzle.stl"', TOOLHEAD)
        self.assertIn('getColor("layerview_nozzle")', TOOLHEAD)
        self.assertNotIn("MeshBuilder", TOOLHEAD)
        self.assertNotIn("addDonut", TOOLHEAD)
        self.assertNotIn("addCube", TOOLHEAD)
        self.assertIn("indicator.ensureNativeNozzleMesh(view)", PLUGIN)

    def test_source_tree_has_no_license_file(self):
        self.assertFalse(any(p.name.lower().startswith("license") for p in ROOT.rglob("*") if p.is_file()))


if __name__ == "__main__":
    unittest.main()
