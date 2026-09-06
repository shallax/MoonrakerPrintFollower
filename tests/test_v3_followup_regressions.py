import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

BED_MESH = (PLUGINS / "BedMeshSceneNode.py").read_text()
TYPED = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text()
CONTROLS = (PLUGINS / "MoonrakerMonitorControls.py").read_text()
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
PREVIEW_QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
EMPTY_PREVIEW_QML = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()
UPLOAD = (PLUGINS / "MoonrakerOutputDeviceLifecycle.py").read_text()
UPLOAD_QML = (PLUGINS / "MoonrakerUploadDialog.qml").read_text()

from PrinterConfig import PrinterConfig, PrinterConfigStore


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
        self.assertIn("scene.sceneChanged.emit(node)", TYPED)
        self.assertIn("fileCompleted", TYPED)
        self.assertIn("_on_cura_bed_mesh_scene_changed", TYPED)
        self.assertIn("_ensure_bed_mesh_scene_node() if snapshot", TYPED)
        for qml in (PREVIEW_QML, EMPTY_PREVIEW_QML):
            self.assertIn("Neon orange outline = Klipper mesh bounds; outside = extrapolated", qml)
            self.assertIn("opacity: base.bedMeshVisible ? 1.0 : 0.0", qml)
            self.assertIn("height: implicitHeight", qml)
            self.assertIn("selectedLayerEtaText", qml)
            self.assertIn("bedMeshMinimumText", qml)
            self.assertIn("bedMeshMaximumText", qml)
            self.assertIn("GradientStop", qml)

    def test_upload_root_has_a_human_readable_label(self):
        self.assertIn('ROOT_UPLOAD_LABEL = "<root>"', UPLOAD)
        self.assertIn("if path == self.ROOT_UPLOAD_LABEL", UPLOAD)
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
        self.assertIn("_webcam_identity", TYPED)
        self.assertIn("camera_selected", TYPED)
        self.assertIn("Layout.preferredWidth: 260 * screenScaleFactor", MONITOR_QML)

    def test_monitor_layer_tracks_remote_print_not_cura_slider(self):
        self.assertIn("Do not derive the physical printer layer from SimulationView", CONTROLS)
        self.assertIn("if same_index_file and ranges:", CONTROLS)
        self.assertIn("total_layer = len(ranges)", CONTROLS)
        self.assertIn("_remote_layer_ranges", CONTROLS)
        self.assertNotIn("authoritative result of the follower", CONTROLS)

    def test_eta_anchors_to_slicer_metadata_instead_of_gcode_bytes(self):
        self.assertIn("_estimate_remaining_seconds", MONITOR_MODEL)
        self.assertIn("slicer_remaining = max(0.0, slicer_total - elapsed)", MONITOR_MODEL)
        self.assertIn("0.60 * slicer_total <= file_total <= 1.75 * slicer_total", MONITOR_MODEL)
        self.assertIn("_metadata_lookup_complete", MONITOR_MODEL)
        self.assertNotIn("self._metadata_estimated_time * (1.0 - progress)", MONITOR_MODEL)

    def test_preview_layer_scrub_shows_duration_and_local_clock_eta(self):
        follower = (PLUGINS / "MoonrakerPrintFollower.py").read_text()
        index = (PLUGINS / "GCodeIndex.py").read_text()
        self.assertIn("layer_elapsed_times", index)
        self.assertIn("_update_selected_layer_eta", follower)
        self.assertIn("datetime.now().astimezone()", follower)
        self.assertIn("Selected layer", follower)
        self.assertIn('controls.setProperty("selectedLayerEtaText"', follower)

    def test_mcu_stats_are_exposed_individually(self):
        for token in (
            "_parse_mcu_last_stats",
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
            self.assertIn(token, CONTROLS)
        self.assertIn("function applyLedColour()", DASHBOARD_QML)
        self.assertIn("onPressedChanged: if (!pressed) applyLedColour()", DASHBOARD_QML)
        self.assertNotIn('text: "Set colour"', DASHBOARD_QML)
        self.assertIn("root.printer.setLedColor", DASHBOARD_QML)

    def test_live_tuning_slider_ranges_expand_from_accepted_value(self):
        self.assertIn("to: Math.max(200, root.printer != null ? Math.ceil(root.printer.speedFactorPercent * 2) : 200)", DASHBOARD_QML)
        self.assertIn("to: Math.max(200, root.printer != null ? Math.ceil(root.printer.flowFactorPercent * 2) : 200)", DASHBOARD_QML)

    def test_emergency_stop_text_stays_black_during_click_sequence(self):
        self.assertIn('color: "black"', DASHBOARD_QML)
        self.assertNotIn('emergencyButton.clicks >= 2 ? "white"', DASHBOARD_QML)


if __name__ == "__main__":
    unittest.main()
