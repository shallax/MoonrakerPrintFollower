import pathlib
import types
import unittest
from plugins.MonitorFormatting import mesh_profiles, parse_bed_mesh

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

TYPED_CONTROLS = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
TYPED = "\n".join((PLUGINS / name).read_text(encoding="utf-8") for name in ("MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py", "CuraIntegration.py", "MoonrakerMonitorModel.py"))
PRESENTER = (PLUGINS / "BedMeshPresenter.py").read_text(encoding="utf-8")
SCENE_NODE = (PLUGINS / "BedMeshSceneNode.py").read_text(encoding="utf-8")
MONITOR_CONTROLS = (PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
DASHBOARD = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text(encoding="utf-8")
BED_MESH_MAP_QML = (PLUGINS / "BedMeshMap.qml").read_text(encoding="utf-8")
RANGE_SLIDER_QML = (PLUGINS / "BedMeshRangeSlider.qml").read_text(encoding="utf-8")
PRESENTATION = (PLUGINS / "PreviewPresentation.py").read_text(encoding="utf-8")
PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text(encoding="utf-8")
MAIN_DASHBOARD = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")
SETUP_SECTION_QML = (PLUGINS / "SetupSection.qml").read_text(encoding="utf-8")
PREVIEW_CONTROLS = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
EMPTY_PREVIEW = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")


class BedMeshTests(unittest.TestCase):
    @staticmethod
    def _load_typed_model():
        return types.SimpleNamespace(_parse_bed_mesh_status=parse_bed_mesh)

    def test_parses_interpolated_klipper_mesh(self):
        model = self._load_typed_model()
        snapshot = model._parse_bed_mesh_status({
            "profile_name": "default",
            "mesh_min": [40.0, 35.0],
            "mesh_max": [210.0, 215.0],
            "probed_matrix": [[0.0, 0.0], [0.0, 0.0]],
            "mesh_matrix": [
                [-0.100, 0.000, 0.100],
                [-0.050, 0.020, 0.080],
                [0.000, 0.040, 0.050],
            ],
        })
        self.assertEqual(snapshot["profile"], "default")
        self.assertEqual(snapshot["source"], "mesh_matrix")
        self.assertEqual(snapshot["rows"], 3)
        self.assertEqual(snapshot["columns"], 3)
        self.assertEqual(len(snapshot["values"]), 9)
        self.assertAlmostEqual(snapshot["minimum"], -0.1)
        self.assertAlmostEqual(snapshot["maximum"], 0.1)
        self.assertAlmostEqual(snapshot["range"], 0.2)
        self.assertEqual(snapshot["xMin"], 40.0)
        self.assertEqual(snapshot["yMax"], 215.0)

    def test_falls_back_to_probed_matrix_and_rejects_bad_bounds(self):
        model = self._load_typed_model()
        snapshot = model._parse_bed_mesh_status({
            "profile_name": "live",
            "mesh_min": [0, 0],
            "mesh_max": [100, 100],
            "mesh_matrix": [],
            "probed_matrix": [[-0.02, 0.03], [0.01, 0.04]],
        })
        self.assertEqual(snapshot["source"], "probed_matrix")
        self.assertAlmostEqual(snapshot["range"], 0.06)
        self.assertEqual(
            model._parse_bed_mesh_status({
                "mesh_min": [100, 0],
                "mesh_max": [0, 100],
                "mesh_matrix": [[0, 0], [0, 0]],
            }),
            {},
        )

    def test_preview_surface_is_non_sliceable_scene_rendering(self):
        self.assertIn("class BedMeshSceneNode(SceneNode)", SCENE_NODE)
        self.assertIn("setCalculateBoundingBox(False)", SCENE_NODE)
        self.assertIn("setSelectable(False)", SCENE_NODE)
        self.assertIn('Resources.getPath(Resources.Shaders, "default.shader")', SCENE_NODE)
        self.assertIn("transparent=True", SCENE_NODE)
        self.assertIn("backface_cull=False", SCENE_NODE)
        self.assertIn("value * exaggeration", SCENE_NODE)
        self.assertIn("DEFAULT_EXAGGERATION = 20.0", SCENE_NODE)
        self.assertIn("machine_depth / 2.0 - printer_y", SCENE_NODE)

    def test_monitor_and_preview_controls_expose_mesh(self):
        self.assertIn("bedMeshAvailable", TYPED_CONTROLS)
        self.assertIn("setBedMeshPreviewVisible", TYPED_CONTROLS)
        self.assertIn("self._node", PRESENTER)
        self.assertNotIn("self._follower", PRESENTER)
        self.assertIn("Qt.createComponent", DASHBOARD)  # the registered shell's async dashboard load
        self.assertIn("BedMeshMap {", MONITOR_QML)  # the shared mesh canvas
        self.assertIn("Canvas", BED_MESH_MAP_QML)
        self.assertIn('title: "Bed mesh — "', MONITOR_QML)  # the pop-over shell owns the title
        self.assertIn("bedMeshMinimum", MONITOR_QML)
        self.assertIn("bedMeshMaximum", MONITOR_QML)
        self.assertIn("bedMeshRange", MONITOR_QML)
        self.assertIn("The Preview's height exaggeration adjusts from its card", MONITOR_QML)
        self.assertIn("id: infoPanel", MONITOR_QML)
        self.assertIn('text: "Information"', MONITOR_QML)
        self.assertIn("bedMeshXMax - root.printer.bedMeshXMin", BED_MESH_MAP_QML)  # aspect-fitted plot
        for qml in (PREVIEW_CONTROLS, EMPTY_PREVIEW):
            self.assertIn("bedMeshVisibilityRequested", qml)
            self.assertIn('"Hide bed mesh"', qml)
            self.assertIn('"Show bed mesh"', qml)

    def test_bed_mesh_extends_to_bed_edges_without_disguising_extrapolation(self):
        for token in (
            "EXTRAPOLATED_ALPHA",
            "_axis_with_bed_edges",
            "_sample_matrix",
            "bed_x_min, bed_x_max",
            "bed_y_min, bed_y_max",
            "extrapolated=extrapolated",
        ):
            self.assertIn(token, SCENE_NODE)
        self.assertLess(
            float(SCENE_NODE.split("EXTRAPOLATED_ALPHA =", 1)[1].splitlines()[0].strip()),
            float(SCENE_NODE.split("SURFACE_ALPHA =", 1)[1].splitlines()[0].strip()),
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
            self.assertIn(token, SCENE_NODE)

    def test_bed_mesh_visibility_forces_scene_redraw_and_rebuilds_after_file_load(self):
        self.assertIn("sceneChanged.emit(self._node)", TYPED)
        self.assertIn("fileCompleted", TYPED)
        self.assertIn("cura.changed.connect(self._render)", TYPED)
        self.assertIn("with self._cura.decorating_scene()", TYPED)
        for qml in (PREVIEW_CONTROLS, EMPTY_PREVIEW):
            self.assertIn("Neon orange outline = the probed mesh bounds; outside = the boundary values, continued as Klipper clamps them", qml)
            # The legend collapses when the mesh is hidden (the
            # 2026-09-16 ruling): the card reflows instead of
            # keeping a faded gap.
            self.assertIn("visible: base.bedMeshAvailable && base.bedMeshVisible", qml)
            self.assertNotIn("opacity: base.bedMeshAvailable && base.bedMeshVisible ? 1.0 : 0.0", qml)
            self.assertIn("selectedLayerEtaText", qml)
            self.assertIn("bedMeshMinimumText", qml)
            self.assertIn("bedMeshMaximumText", qml)
            self.assertIn("BedMeshRangeSlider {", qml)

    def test_range_filter_slider_is_shared_and_synchronised(self):
        # By request: the dual-ended range filter lives on
        # BOTH the Information pop-over and the Preview legend, and
        # one shared model window drives both. The rainbow stops live
        # in the shared component so the two surfaces cannot drift.
        for stop in ("MoonrakerTheme.bandBlue", "MoonrakerTheme.bandCyan", "MoonrakerTheme.bandGreen", "MoonrakerTheme.bandYellow", "MoonrakerTheme.bandRed"):
            self.assertIn(stop, RANGE_SLIDER_QML)
        for qml in (MONITOR_QML, PREVIEW_CONTROLS):
            self.assertIn("BedMeshRangeSlider", qml)
        # The shared window: the model publishes it, the pop-over
        # slider writes it, the card's intents route through the
        # presentation into the same slot.
        self.assertIn("bedMeshThresholdLow", TYPED_CONTROLS)
        self.assertIn("setBedMeshThresholds", TYPED_CONTROLS)
        self.assertIn('root.printer.setBedMeshThresholds(low, high)', MONITOR_QML)
        self.assertIn("bedMeshThresholdsRequested", PRESENTATION)
        self.assertIn("bedMeshThresholdsRequested.connect(monitor.setBedMeshThresholds)", PLUGIN)
        # The window moves whole (the centre drag) and the bar
        # desaturates outside it (the wash, not a cover).
        for token in ("windowAdjusted", "mode = 3", "outOfWindowAlpha", "moonrakerBedMeshRangeSlider"):
            self.assertIn(token, RANGE_SLIDER_QML)
        # The cells grey out outside the window on the map, and the
        # scene node mirrors the same grey for its out-of-window
        # vertices.
        self.assertIn("outOfWindowGrey", BED_MESH_MAP_QML)
        # The grid-effect fix (a report): the cells paint
        # at full opacity with the fainter look pre-mixed toward the
        # background — partial-alpha fills that overlap double-paint
        # their shared edges into a visible grid.
        self.assertIn("blendOver", BED_MESH_MAP_QML)
        self.assertIn("measured ? 0.58 : 0.28", BED_MESH_MAP_QML)
        self.assertIn("root.inRange(sample)", BED_MESH_MAP_QML)
        self.assertIn("OUT_OF_WINDOW_GREY", SCENE_NODE)
        self.assertIn("low=thresholds[0], high=thresholds[1]", PRESENTER)
        self.assertIn("def set_thresholds", PRESENTER)
        self.assertIn("def _clamp_thresholds", PRESENTER)

    def test_scale_z_max_exaggeration_is_owned_by_the_presenter(self):
        # By request: a Preview-side slider scales the Z
        # distortion (Mainsail's "scale z-max"), 0 flattens the
        # surface, the ceiling is 1000, and the default stays the
        # historical fixed 20.
        self.assertIn("EXAGGERATION_PREF_KEY", PRESENTER)
        self.assertIn("def set_exaggeration", PRESENTER)
        self.assertIn("self._exaggeration", PRESENTER)
        self.assertIn("MAX_EXAGGERATION = 1000.0", SCENE_NODE)
        self.assertIn("MAX_EXAGGERATION = 1000.0", PRESENTER)
        self.assertIn('max(0.0, min(self.MAX_EXAGGERATION, float(exaggeration)))', SCENE_NODE)
        for token in ("Scale z-max", "bedMeshExaggerationRequested", "bedMeshExaggeration"):
            self.assertIn(token, PREVIEW_CONTROLS)
        self.assertIn("to: 1000", PREVIEW_CONTROLS)
        self.assertIn("bedMeshExaggerationRequested", PRESENTATION)
        self.assertIn("bedMeshExaggerationRequested.connect(self.set_exaggeration)", PRESENTER)

    def test_hourglass_rotation_never_carries_onto_the_download_glyph(self):
        # A live report: when the hourglass hands back to
        # the download icon, the glyph kept the angle the hourglass
        # froze at — an assignment from inside the animation cannot
        # win against the animation binding, so the idle STATE forces
        # the reset instead. The glyph rides the job section (4.3.0).
        job = (PLUGINS / "JobSection.qml").read_text(encoding="utf-8")
        self.assertIn('name: "idle"', job)
        self.assertIn("target: etaGlyph", job)

    def test_saved_profiles_are_ordered_and_loadable(self):
        self.assertEqual(mesh_profiles({"profiles": {"summer": {}, "default": {}, "winter": {}}, "profile_name": "winter"}), ["winter", "default", "summer"])
        self.assertIn("shlex.quote(name)", MONITOR_CONTROLS)
        self.assertIn("BED_MESH_PROFILE LOAD=", MONITOR_CONTROLS)
        self.assertIn('text: "Load saved mesh"', SETUP_SECTION_QML)

    def test_clearing_active_mesh_does_not_delete_saved_profiles(self):
        self.assertIn('"BED_MESH_CLEAR"', MONITOR_CONTROLS)
        self.assertNotIn("BED_MESH_PROFILE REMOVE", MONITOR_CONTROLS)
        self.assertIn('text: "Clear mesh"', SETUP_SECTION_QML)
        self.assertIn('text: "Calibrate mesh"', SETUP_SECTION_QML)


if __name__ == "__main__":
    unittest.main()
