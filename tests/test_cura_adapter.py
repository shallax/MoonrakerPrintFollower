import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mpf_cura_adapter", ROOT / "plugins" / "CuraAdapter.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
active_machine_identity = MODULE.active_machine_identity
preview_head_position = MODULE.preview_head_position


class _Stack:
    def getId(self):
        return "machine-123"

    def getName(self):
        return "Printer A"


class _AppBeforeMachineSelection:
    def __init__(self):
        self.machine_manager_touched = False

    def getGlobalContainerStack(self):
        return None

    def getMachineManager(self):
        self.machine_manager_touched = True
        raise AssertionError("MachineManager must not be forced during plugin bootstrap")


class _AppWithMachine:
    def getGlobalContainerStack(self):
        return _Stack()

    def getMachineManager(self):
        raise AssertionError("Global stack is authoritative; MachineManager is unnecessary")


class _WorldPosition:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class _Polygon:
    def __init__(self, points):
        self.data = points


class _Layer:
    def __init__(self, polygons):
        self.polygons = polygons


class _LayerData:
    def __init__(self, layers):
        self._layers = layers

    def getLayer(self, number):
        return self._layers[number]


class _SceneNode:
    def __init__(self, layer_data=None, children=None, world=(0.0, 0.0, 0.0)):
        self._layer_data = layer_data
        self._children = list(children or [])
        self._world = _WorldPosition(*world)

    def getChildren(self):
        return self._children

    def callDecoration(self, name):
        return self._layer_data if name == "getLayerData" else None

    def getWorldPosition(self):
        return self._world


class _Scene:
    def __init__(self, root):
        self._root = root

    def getRoot(self):
        return self._root


class _Controller:
    def __init__(self, root):
        self._scene = _Scene(root)

    def getScene(self):
        return self._scene


class _PreviewView:
    def __init__(self, layer, path):
        self._layer = layer
        self._path = path

    def getCurrentLayer(self):
        return self._layer

    def getCurrentPath(self):
        return self._path


class CuraAdapterTests(unittest.TestCase):
    def test_bootstrap_identity_does_not_force_machine_manager(self):
        app = _AppBeforeMachineSelection()
        self.assertEqual(active_machine_identity(app), ("unknown", "Unknown Cura printer"))
        self.assertFalse(app.machine_manager_touched)

    def test_established_global_stack_supplies_identity(self):
        self.assertEqual(active_machine_identity(_AppWithMachine()), ("machine-123", "Printer A"))


    def test_preview_head_position_matches_current_polygon_point_and_world_offset(self):
        layer_data = _LayerData({0: _Layer([_Polygon([[1, 2, 3], [4, 5, 6]])])})
        toolpath = _SceneNode(layer_data=layer_data, world=(10, 20, 30))
        root = _SceneNode(children=[toolpath])
        self.assertEqual(
            preview_head_position(_Controller(root), _PreviewView(0, 1.0)),
            (14.0, 25.0, 36.0),
        )

    def test_preview_head_position_interpolates_fractional_path(self):
        layer_data = _LayerData({0: _Layer([_Polygon([[0, 0, 0], [10, 20, 30]])])})
        root = _SceneNode(children=[_SceneNode(layer_data=layer_data)])
        self.assertEqual(
            preview_head_position(_Controller(root), _PreviewView(0, 0.25)),
            (2.5, 5.0, 7.5),
        )

    def test_preview_head_position_walks_across_polygons(self):
        layer_data = _LayerData({0: _Layer([
            _Polygon([[0, 0, 0], [1, 1, 1]]),
            _Polygon([[5, 6, 7], [8, 9, 10]]),
        ])})
        root = _SceneNode(children=[_SceneNode(layer_data=layer_data)])
        self.assertEqual(
            preview_head_position(_Controller(root), _PreviewView(0, 2.0)),
            (5.0, 6.0, 7.0),
        )

    def test_preview_head_position_returns_none_without_layer_geometry(self):
        root = _SceneNode()
        self.assertIsNone(preview_head_position(_Controller(root), _PreviewView(0, 0.0)))


if __name__ == "__main__":
    unittest.main()
