import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

TYPED_CONTROLS = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text()
SCENE_NODE = (PLUGINS / "BedMeshSceneNode.py").read_text()
DASHBOARD = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text()
PREVIEW_CONTROLS = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
EMPTY_PREVIEW = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()


class BedMeshTests(unittest.TestCase):
    @staticmethod
    def _load_typed_model():
        class DummySignal:
            def emit(self, *_args, **_kwargs):
                pass

            def connect(self, *_args, **_kwargs):
                pass

        def pyqt_signal(*_args, **_kwargs):
            return DummySignal()

        def pyqt_property(*_args, **_kwargs):
            def decorate(function):
                return property(function)
            return decorate

        def pyqt_slot(*_args, **_kwargs):
            def decorate(function):
                return function
            return decorate

        class QVariant:
            def __init__(self, value=None):
                self.value = value

        pyqt6 = types.ModuleType("PyQt6")
        qtcore = types.ModuleType("PyQt6.QtCore")
        qtcore.QVariant = QVariant
        qtcore.pyqtProperty = pyqt_property
        qtcore.pyqtSignal = pyqt_signal
        qtcore.pyqtSlot = pyqt_slot

        package = types.ModuleType("plugins")
        package.__path__ = []
        base_module = types.ModuleType("plugins.MoonrakerMonitorControls")
        base_module.MoonrakerMonitorModel = type(
            "BaseMoonrakerMonitorModel",
            (),
            {"_want_aux_object": staticmethod(lambda _name: False)},
        )

        names = ["PyQt6", "PyQt6.QtCore", "plugins", "plugins.MoonrakerMonitorControls"]
        old = {name: sys.modules.get(name) for name in names}
        try:
            sys.modules["PyQt6"] = pyqt6
            sys.modules["PyQt6.QtCore"] = qtcore
            sys.modules["plugins"] = package
            sys.modules["plugins.MoonrakerMonitorControls"] = base_module
            namespace = {"__name__": "plugins.MoonrakerMonitorTypedControls", "__package__": "plugins"}
            exec(compile(TYPED_CONTROLS, "MoonrakerMonitorTypedControls.py", "exec"), namespace)
            return namespace["MoonrakerMonitorModel"]
        finally:
            for name, value in old.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

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
        self.assertIn("_bed_mesh_scene_node", TYPED_CONTROLS)
        self.assertIn("Canvas", DASHBOARD)
        self.assertIn('text: "Bed mesh — "', DASHBOARD)
        self.assertIn("bedMeshMinimum", DASHBOARD)
        self.assertIn("bedMeshMaximum", DASHBOARD)
        self.assertIn("bedMeshRange", DASHBOARD)
        self.assertIn("20× vertical exaggeration", DASHBOARD)
        for qml in (PREVIEW_CONTROLS, EMPTY_PREVIEW):
            self.assertIn("bedMeshVisibilityRequested", qml)
            self.assertIn('"Hide bed mesh"', qml)
            self.assertIn('"Show bed mesh"', qml)


if __name__ == "__main__":
    unittest.main()
