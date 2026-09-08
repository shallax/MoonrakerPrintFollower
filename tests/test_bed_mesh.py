import pathlib
import types
import unittest
from plugins.MonitorFormatting import mesh_profiles, parse_bed_mesh

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

TYPED_CONTROLS = (PLUGINS / "MoonrakerMonitorModel.py").read_text()
TYPED = "\n".join((PLUGINS / name).read_text() for name in ("MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py", "CuraIntegration.py", "MoonrakerMonitorModel.py"))
PRESENTER = (PLUGINS / "BedMeshPresenter.py").read_text()
SCENE_NODE = (PLUGINS / "BedMeshSceneNode.py").read_text()
MONITOR_CONTROLS = (PLUGINS / "MonitorControls.py").read_text()
DASHBOARD = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text()
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text()
MAIN_DASHBOARD = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
PREVIEW_CONTROLS = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
EMPTY_PREVIEW = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()


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
        self.assertIn("MoonrakerMonitorDashboard", DASHBOARD)  # the registered shell
        self.assertIn("Canvas", MONITOR_QML)
        self.assertIn('text: "Bed mesh — "', MONITOR_QML)
        self.assertIn("bedMeshMinimum", MONITOR_QML)
        self.assertIn("bedMeshMaximum", MONITOR_QML)
        self.assertIn("bedMeshRange", MONITOR_QML)
        self.assertIn("20× vertical exaggeration", MONITOR_QML)
        self.assertIn("id: infoPanel", MONITOR_QML)
        self.assertIn('text: "Information"', MONITOR_QML)
        self.assertIn("bedMeshXMax - root.printer.bedMeshXMin", MONITOR_QML)  # aspect-fitted plot
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
            self.assertIn("Neon orange outline = Klipper mesh bounds; outside = extrapolated", qml)
            self.assertIn("opacity: base.bedMeshVisible ? 1.0 : 0.0", qml)
            self.assertIn("height: implicitHeight", qml)
            self.assertIn("selectedLayerEtaText", qml)
            self.assertIn("bedMeshMinimumText", qml)
            self.assertIn("bedMeshMaximumText", qml)
            self.assertIn("GradientStop", qml)

    def test_saved_profiles_are_ordered_and_loadable(self):
        self.assertEqual(mesh_profiles({"profiles": {"summer": {}, "default": {}, "winter": {}}, "profile_name": "winter"}), ["winter", "default", "summer"])
        self.assertIn("shlex.quote(name)", MONITOR_CONTROLS)
        self.assertIn("BED_MESH_PROFILE LOAD=", MONITOR_CONTROLS)
        self.assertIn('text: "Load saved mesh"', MAIN_DASHBOARD)

    def test_clearing_active_mesh_does_not_delete_saved_profiles(self):
        self.assertIn('"BED_MESH_CLEAR"', MONITOR_CONTROLS)
        self.assertNotIn("BED_MESH_PROFILE REMOVE", MONITOR_CONTROLS)
        self.assertIn('text: "Clear mesh"', MAIN_DASHBOARD)
        self.assertIn('text: "Calibrate mesh"', MAIN_DASHBOARD)


if __name__ == "__main__":
    unittest.main()
