import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

BED_MESH = (PLUGINS / "BedMeshSceneNode.py").read_text()
TYPED = "\n".join((PLUGINS / name).read_text() for name in ("MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py", "CuraIntegration.py", "MoonrakerMonitorModel.py"))
CONTROLS = (PLUGINS / "MonitorControls.py").read_text()
FORMATTING = (PLUGINS / "MonitorFormatting.py").read_text()
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
PREVIEW_QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
EMPTY_PREVIEW_QML = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()
UPLOAD = (PLUGINS / "UploadController.py").read_text() + (PLUGINS / "MoonrakerOutputDevice.py").read_text()
UPLOAD_QML = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()
PREVIEW_ETA = (PLUGINS / "PreviewFollower.py").read_text()
PREVIEW_STATUS = (PLUGINS / "PrintCoordinator.py").read_text()

from plugins.PrinterConfig import PrinterConfig, PrinterConfigStore


class V3FollowupRegressionTests(unittest.TestCase):
    def test_bed_mesh_extends_to_bed_edges_without_disguising_extrapolation(self):
        for token in (
            "EXTRAPOLATED_ALPHA",
            "_axis_with_bed_edges",
            "_sample_matrix",
            "bed_x_min, bed_x_max",
            "bed_y_min, bed_y_max",
            "extrapolated=extrapolated",
        ):
            self.assertIn(token, BED_MESH)
        self.assertLess(
            float(BED_MESH.split("EXTRAPOLATED_ALPHA =", 1)[1].splitlines()[0].strip()),
            float(BED_MESH.split("SURFACE_ALPHA =", 1)[1].splitlines()[0].strip()),
        )

    def test_bed_mesh_draws_an_obvious_probe_bounds_outline(self):
        for token in (
            "BOUNDARY_WIDTH = 1.4",
            "BOUNDARY_LIFT = 0.09",
            "BOUNDARY_ALPHA = 0.94",
            "BOUNDARY_COLOUR = (1.0, 0.353, 0.0)",
            "append_boundary_segment",
            "boundary_vertices",
            "boundary_colours",
        ):
            self.assertIn(token, BED_MESH)

    def test_bed_mesh_visibility_forces_scene_redraw_and_rebuilds_after_file_load(self):
        self.assertIn("sceneChanged.emit(self._node)", TYPED)
        self.assertIn("fileCompleted", TYPED)
        self.assertIn("cura.changed.connect(self._render)", TYPED)
        self.assertIn("with self._cura.decorating_scene()", TYPED)
        for qml in (PREVIEW_QML, EMPTY_PREVIEW_QML):
            self.assertIn("Neon orange outline = Klipper mesh bounds; outside = extrapolated", qml)
            self.assertIn("opacity: base.bedMeshVisible ? 1.0 : 0.0", qml)
            self.assertIn("height: implicitHeight", qml)
            self.assertIn("selectedLayerEtaText", qml)
            self.assertIn("bedMeshMinimumText", qml)
            self.assertIn("bedMeshMaximumText", qml)
            self.assertIn("GradientStop", qml)

    def test_upload_root_has_a_human_readable_label(self):
        self.assertIn('if path == "<root>"', UPLOAD)
        self.assertIn('self._upload.path or "<root>"', UPLOAD)
        self.assertIn('if (path === "<root>") return true', UPLOAD_QML)
        self.assertIn("<root> is Moonraker's gcodes directory", UPLOAD_QML)

    def test_camera_selection_round_trips_per_printer(self):
        class Preferences:
            def __init__(self):
                self.values = {}
            def addPreference(self, key, default):
                self.values.setdefault(key, default)
            def getValue(self, key):
                return self.values.get(key)
            def setValue(self, key, value):
                self.values[key] = value

        active = ["printer-a", "Printer A"]
        store = PrinterConfigStore(Preferences(), lambda: tuple(active))
        store.set(PrinterConfig(camera_selected="bed-camera"))
        self.assertEqual(store.get().camera_selected, "bed-camera")
        active[:] = ["printer-b", "Printer B"]
        self.assertEqual(store.get().camera_selected, "")
        self.assertIn("def identity(camera", TYPED)
        self.assertIn("camera_selected", TYPED)
        self.assertIn("Layout.preferredWidth: 260 * screenScaleFactor", MONITOR_QML)

    def test_monitor_layer_tracks_remote_print_not_cura_slider(self):
        resolver = (PLUGINS / "PrintState.py").read_text()
        self.assertIn("class LayerResolver", resolver)
        self.assertIn("total = len(index.ranges)", resolver)
        self.assertNotIn("getCurrentLayer", resolver)
        self.assertIn("self._print_state()", MONITOR_MODEL)

    def test_eta_anchors_to_slicer_metadata_instead_of_gcode_bytes(self):
        self.assertIn("def estimate_remaining", FORMATTING)
        self.assertIn("remaining = max(0, estimate - elapsed)", FORMATTING)
        self.assertIn("0.60 * estimate <= elapsed + by_file <= 1.75 * estimate", FORMATTING)
        self.assertIn("physical.metadata_complete", FORMATTING)
        self.assertNotIn("self._metadata_estimated_time * (1.0 - progress)", MONITOR_MODEL)

    def test_preview_layer_scrub_shows_duration_and_local_clock_eta(self):
        index = (PLUGINS / "GCodeIndex.py").read_text()
        self.assertIn("layer_elapsed_times", index)
        self.assertIn("def update_eta", PREVIEW_ETA)
        self.assertIn("datetime.now().astimezone()", PREVIEW_ETA)
        self.assertIn("Selected layer", PREVIEW_ETA)
        self.assertIn('"selectedLayerEtaText": state.eta_text', PREVIEW_STATUS)

    def test_mcu_stats_are_exposed_individually(self):
        for token in (
            "parse_mcu_stats",
            "mcu_awake",
            "mcu_task_avg",
            "bytes_retransmit",
            "mcuItems",
            '"Main MCU"',
        ):
            self.assertIn(token, TYPED)
        self.assertIn("modelData.load", MONITOR_QML)
        self.assertIn("modelData.frequency", MONITOR_QML)
        self.assertIn("modelData.transport", MONITOR_QML)

    def test_addressable_led_colour_is_controllable(self):
        for token in (
            "redPercent",
            "greenPercent",
            "bluePercent",
            "whitePercent",
            "hasWhite",
            "setLedColor",
            "SET_LED LED=",
        ):
            self.assertIn(token, CONTROLS + MONITOR_MODEL)
        self.assertIn("function applyLedColour()", DASHBOARD_QML)
        self.assertGreaterEqual(DASHBOARD_QML.count("if (!pressed) applyLedColour()"), 4)
        self.assertIn("root.tuningSliderPressed = pressed", DASHBOARD_QML)
        self.assertNotIn('text: "Set colour"', DASHBOARD_QML)
        self.assertIn("root.printer.setLedColor", DASHBOARD_QML)

    def test_live_tuning_slider_ranges_expand_from_accepted_value(self):
        self.assertIn("to: Math.max(200, root.printer != null ? Math.ceil(root.printer.speedFactorPercent * 2) : 200)", DASHBOARD_QML)
        self.assertIn("to: Math.max(200, root.printer != null ? Math.ceil(root.printer.flowFactorPercent * 2) : 200)", DASHBOARD_QML)
        self.assertIn('max(10 if kind == "speed" else 50, int(percent))', CONTROLS)
        self.assertNotIn("min(200, int(percent))", CONTROLS)
        self.assertNotIn("min(150, int(percent))", CONTROLS)

    def test_z_offset_buttons_are_opposites_with_equal_click_zones(self):
        self.assertIn("id: zOffsetGrid", DASHBOARD_QML)
        self.assertIn("model: [-0.005, -0.01, -0.025, -0.05]", DASHBOARD_QML)
        self.assertIn("model: [0.005, 0.01, 0.025, 0.05]", DASHBOARD_QML)
        self.assertEqual(DASHBOARD_QML.count("width: zOffsetGrid.buttonWidth"), 2)
        self.assertGreaterEqual(DASHBOARD_QML.count("fixedWidthMode: true"), 2)

    def test_emergency_stop_is_pinned_outside_scrollable_controls(self):
        self.assertIn("anchors.bottom: emergencyDock.top", DASHBOARD_QML)
        self.assertIn("id: emergencyDock", DASHBOARD_QML)
        self.assertEqual(DASHBOARD_QML.count("id: emergencyButton"), 1)

    def test_emergency_stop_text_stays_black_during_click_sequence(self):
        self.assertIn('color: "black"', DASHBOARD_QML)
        self.assertNotIn('emergencyButton.clicks >= 2 ? "white"', DASHBOARD_QML)

    def test_monitor_sliders_only_commit_on_release(self):
        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use
        # Qt Quick Controls' deferred-value mode. onMoved only previews/holds
        # the intended value; release queues the debounced Moonraker command.
        self.assertGreaterEqual(DASHBOARD_QML.count("live: false"), 9)
        for slider_id in (
            "speedSlider", "flowSlider", "fanSlider", "ledSlider",
            "redSlider", "greenSlider", "blueSlider", "whiteSlider",
            "pwmSlider",
        ):
            marker = "id: " + slider_id
            start = DASHBOARD_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", DASHBOARD_QML[start:start + 500], slider_id)
        self.assertGreaterEqual(DASHBOARD_QML.count("onMoved:"), 9)
        self.assertIn("previewSpeedFactor", DASHBOARD_QML)
        self.assertIn("previewFlowFactor", DASHBOARD_QML)
        self.assertIn("previewFanSpeed", DASHBOARD_QML)
        self.assertIn("previewLedBrightness", DASHBOARD_QML)
        self.assertIn("previewLedColor", DASHBOARD_QML)
        self.assertIn("previewPwmOutput", DASHBOARD_QML)
        self.assertIn("function sliderSelection(slider)", DASHBOARD_QML)
        self.assertIn("slider.valueAt(slider.position)", DASHBOARD_QML)
        self.assertIn("root.sliderSelection(speedSlider) + \"%\"", DASHBOARD_QML)
        self.assertIn("setSpeedFactor(root.sliderSelection(speedSlider))", DASHBOARD_QML)
        self.assertIn("setFlowFactor(root.sliderSelection(flowSlider))", DASHBOARD_QML)

    def test_monitor_sliders_do_not_repeat_qml_properties(self):
        duplicate = "from: 0; to: 100; stepSize: 1\n                                        from: 0; to: 100; live: false"
        self.assertNotIn(duplicate, DASHBOARD_QML)


if __name__ == "__main__":
    unittest.main()
