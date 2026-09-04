import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "NativeNozzleFallback", ROOT / "plugins" / "NativeNozzleFallback.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
keep_native_nozzle_visible = MODULE.keep_native_nozzle_visible


class _Pass:
    def __init__(self, *, compatibility=False):
        self._compatibility_mode = compatibility
        self._switching_layers = True
        self._old_current_layer = 2
        self.enabled = False

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class _Root:
    pass


class _Scene:
    def __init__(self, root):
        self.root = root

    def getRoot(self):
        return self.root


class _Controller:
    def __init__(self, scene):
        self.scene = scene

    def getScene(self):
        return self.scene


class _Nozzle:
    def __init__(self, parent=None):
        self.parent = parent
        self.visible = True
        self.parent_calls = 0

    def getParent(self):
        return self.parent

    def setParent(self, parent):
        self.parent = parent
        self.parent_calls += 1

    def setVisible(self, visible):
        self.visible = bool(visible)


class _View:
    def __init__(self, render_pass, layer=17, *, nozzle=None, activity=True):
        self.render_pass = render_pass
        self.layer = layer
        self.root = _Root()
        self.controller = _Controller(_Scene(self.root))
        self.nozzle = nozzle if nozzle is not None else _Nozzle()
        self.activity = activity
        self.activity_calls = 0

    def getSimulationPass(self):
        return self.render_pass

    def getCurrentLayer(self):
        return self.layer

    def getNozzleNode(self):
        return self.nozzle

    def getController(self):
        return self.controller

    def getActivity(self):
        return self.activity

    def setActivity(self, activity):
        self.activity = bool(activity)
        self.activity_calls += 1


class NativeNozzleFallbackTests(unittest.TestCase):
    def test_repairs_nozzle_lifecycle_and_clears_layer_switch_gate(self):
        render_pass = _Pass()
        view = _View(render_pass, 17, activity=False)

        self.assertTrue(keep_native_nozzle_visible(view))
        self.assertIs(view.nozzle.parent, view.root)
        self.assertEqual(view.nozzle.parent_calls, 1)
        self.assertFalse(view.nozzle.visible)
        self.assertTrue(render_pass.enabled)
        self.assertTrue(view.activity)
        self.assertEqual(view.activity_calls, 1)
        self.assertFalse(render_pass._switching_layers)
        self.assertEqual(render_pass._old_current_layer, 17)

    def test_does_not_reparent_nozzle_already_attached_to_current_scene(self):
        render_pass = _Pass()
        view = _View(render_pass)
        view.nozzle.parent = view.root

        self.assertTrue(keep_native_nozzle_visible(view))
        self.assertEqual(view.nozzle.parent_calls, 0)

    def test_does_not_override_compatibility_mode(self):
        render_pass = _Pass(compatibility=True)
        view = _View(render_pass, 9)
        self.assertFalse(keep_native_nozzle_visible(view))
        self.assertIsNone(view.nozzle.parent)
        self.assertTrue(render_pass._switching_layers)
        self.assertEqual(render_pass._old_current_layer, 2)

    def test_missing_simulation_pass_is_safe(self):
        self.assertFalse(keep_native_nozzle_visible(_View(None)))

    def test_missing_private_contract_is_safe(self):
        class IncompletePass:
            _compatibility_mode = False
            def setEnabled(self, enabled):
                pass
        self.assertFalse(keep_native_nozzle_visible(_View(IncompletePass())))


if __name__ == "__main__":
    unittest.main()
