"""The nozzle lifecycle repair's full guard chain (the coverage bolster
target — the module stood at 4%): every capability gate, every missing
piece, the compatibility-mode early-out, the activity/parent/visible
repairs, and the happy path."""
import unittest

from plugins.NativeNozzleLifecycle import keep_native_nozzle_visible


class FakePass:
    def __init__(self):
        self._compatibility_mode = False
        self._switching_layers = True
        self._old_current_layer = -1
        self.enabled = False

    def setEnabled(self, value):
        self.enabled = value


class FakeScene:
    def __init__(self, root=None):
        self._root = root

    def getRoot(self):
        return self._root


class FakeController:
    def __init__(self, scene=None):
        self._scene = scene

    def getScene(self):
        return self._scene


class FakeNozzle:
    def __init__(self, parent=None):
        self._parent = parent
        self.visible = True

    def getParent(self):
        return self._parent

    def setParent(self, parent):
        self._parent = parent

    def setVisible(self, value):
        self.visible = value


class FakeView:
    """A simulation_view double: every capability is optional so the
    guard chain's absence cases stay one attribute deletion away."""

    def __init__(self):
        self.root = object()
        self.pass_obj = FakePass()
        self.nozzle = FakeNozzle(parent=None)
        self.controller = FakeController(scene=FakeScene(root=self.root))
        self.activity = False
        self.layer = 3
        self.pass_enabled = False
        self.nozzle_visible = True
        self.nozzle_parented = False
        self.activity_set = False

    def getSimulationPass(self):
        return self.pass_obj

    def getCurrentLayer(self):
        return self.layer

    def getNozzleNode(self):
        return self.nozzle

    def getController(self):
        return self.controller

    def getActivity(self):
        return self.activity

    def setActivity(self, value):
        self.activity = value
        self.activity_set = True


def make_view():
    view = FakeView()
    return view


class NozzleLifecycleTests(unittest.TestCase):
    def test_happy_path_repairs_and_returns_true(self):
        view = make_view()
        self.assertTrue(keep_native_nozzle_visible(view))
        self.assertIs(view.nozzle.getParent(), view.root)
        self.assertFalse(view.nozzle.visible)  # the scene copy stays hidden
        self.assertTrue(view.pass_obj.enabled)
        self.assertTrue(view.activity)
        self.assertFalse(view.pass_obj._switching_layers)
        self.assertEqual(view.pass_obj._old_current_layer, 3)

    def test_activity_is_not_set_when_already_active(self):
        view = make_view()
        view.activity = True
        keep_native_nozzle_visible(view)
        self.assertTrue(view.activity)

    def test_nozzle_already_parented_is_not_reparented(self):
        view = make_view()
        view.nozzle = FakeNozzle(parent=view.root)
        keep_native_nozzle_visible(view)
        self.assertIs(view.nozzle.getParent(), view.root)

    def test_missing_capabilities_return_false(self):
        # The lookup uses getattr-with-default, so absent and None hit
        # the same callable guard — the guard itself is the branch.
        for attr in ("getSimulationPass", "getCurrentLayer", "getNozzleNode", "getController"):
            view = make_view()
            setattr(view, attr, None)
            self.assertFalse(keep_native_nozzle_visible(view), attr)

    def test_non_callable_capability_returns_false(self):
        view = make_view()
        view.getSimulationPass = "not callable"
        self.assertFalse(keep_native_nozzle_visible(view))

    def test_none_pieces_return_false(self):
        cases = (
            lambda v: setattr(v, "pass_obj", None),
            lambda v: setattr(v, "controller", FakeController(scene=None)),
            lambda v: setattr(v, "controller", FakeController(scene=FakeScene(root=None))),
            lambda v: setattr(v, "nozzle", None),
        )
        for mutate in cases:
            view = make_view()
            mutate(view)
            self.assertFalse(keep_native_nozzle_visible(view))

    def test_compatibility_mode_returns_false(self):
        view = make_view()
        view.pass_obj._compatibility_mode = True
        self.assertFalse(keep_native_nozzle_visible(view))

    def test_pass_without_the_layer_switch_markers_returns_false(self):
        view = make_view()
        view.pass_obj = FakePass()
        del view.pass_obj._switching_layers
        self.assertFalse(keep_native_nozzle_visible(view))

    def test_controller_without_get_scene_returns_false(self):
        view = make_view()
        view.controller = object()  # no getScene at all
        self.assertFalse(keep_native_nozzle_visible(view))

    def test_scene_without_get_root_returns_false(self):
        view = make_view()
        view.controller = FakeController(scene=object())
        self.assertFalse(keep_native_nozzle_visible(view))

    def test_nozzle_without_set_parent_returns_false(self):
        view = make_view()
        view.nozzle = FakeNozzle()
        view.nozzle.setParent = None
        self.assertFalse(keep_native_nozzle_visible(view))

    def test_missing_optional_repairs_still_succeed(self):
        # setVisible / setEnabled / setActivity are capability-checked
        # individually: their absence must not fail the repair.
        view = make_view()
        view.nozzle.setVisible = None
        self.assertTrue(keep_native_nozzle_visible(view))

        view = make_view()
        view.pass_obj.setEnabled = None
        self.assertTrue(keep_native_nozzle_visible(view))

        view = make_view()
        view.setActivity = None
        self.assertTrue(keep_native_nozzle_visible(view))
        self.assertFalse(view.activity)

    def test_exceptions_return_false(self):
        view = make_view()

        def boom():
            raise RuntimeError("probe")
        view.getSimulationPass = boom
        self.assertFalse(keep_native_nozzle_visible(view))
