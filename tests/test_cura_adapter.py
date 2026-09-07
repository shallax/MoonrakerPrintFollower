import importlib.util
import pathlib
import unittest

from plugins.CuraLifecycleBridge import CuraLifecycleBridge

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mpf_cura_adapter", ROOT / "plugins" / "CuraAdapter.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
active_machine_identity = MODULE.active_machine_identity
preview_current_layer = MODULE.preview_current_layer
preview_minimum_layer = MODULE.preview_minimum_layer
preview_current_path = MODULE.preview_current_path
preview_minimum_path = MODULE.preview_minimum_path
preview_max_paths = MODULE.preview_max_paths
set_preview_path = MODULE.set_preview_path
set_preview_minimum_path = MODULE.set_preview_minimum_path


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


class CuraAdapterTests(unittest.TestCase):
    def test_bootstrap_identity_does_not_force_machine_manager(self):
        app = _AppBeforeMachineSelection()
        self.assertEqual(active_machine_identity(app), ("unknown", "Unknown Cura printer"))
        self.assertFalse(app.machine_manager_touched)

    def test_established_global_stack_supplies_identity(self):
        self.assertEqual(active_machine_identity(_AppWithMachine()), ("machine-123", "Printer A"))

    def test_stale_cura_lifecycle_callback_is_rejected(self):
        bridge = CuraLifecycleBridge()
        token = bridge.token()
        observed: list[str] = []

        bridge.invalidate("scene replaced")

        self.assertFalse(bridge.guarded(token, lambda: observed.append("stale")))
        self.assertEqual(observed, [])
        current = bridge.token()
        self.assertTrue(bridge.guarded(current, lambda: observed.append("current")))
        self.assertEqual(observed, ["current"])


class PreviewViewAccessorTests(unittest.TestCase):
    class FakeView:
        def __init__(self):
            self.layer = 3
            self.minimum = 1
            self.path = 2.5
            self.paths = []
        def getCurrentLayer(self): return self.layer
        def getMinimumLayer(self): return self.minimum
        def getCurrentPath(self): return self.path
        def getMinimumPath(self): return 0
        def getMaxPaths(self): return 100
        def setPath(self, value): self.paths.append(value)
        def setMinimumPath(self, value): self.minimum = value

    def test_typed_reads_expose_view_values(self):
        view = self.FakeView()
        self.assertEqual(preview_current_layer(view), 3)
        self.assertEqual(preview_minimum_layer(view), 1)
        self.assertAlmostEqual(preview_current_path(view), 2.5)
        self.assertEqual(preview_max_paths(view), 100)

    def test_missing_or_broken_views_read_as_none(self):
        self.assertIsNone(preview_current_layer(None))
        self.assertIsNone(preview_current_layer(object()))
        view = self.FakeView()
        view.path = "not-a-float"
        self.assertIsNone(preview_current_path(view))

    def test_typed_writes_guard_missing_methods(self):
        view = self.FakeView()
        set_preview_path(view, 12.0)
        set_preview_minimum_path(view, 0)
        self.assertEqual(view.paths, [12.0])
        self.assertEqual(view.minimum, 0)
        set_preview_path(object(), 1.0)  # no setPath: no-op


if __name__ == "__main__":
    unittest.main()
