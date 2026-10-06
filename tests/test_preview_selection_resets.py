"""Selection-tool topology echoes must not impersonate path-slider input."""
import sys
from types import ModuleType
from unittest.mock import patch

from tests import qt_integration_support as harness
from tests.preview_family_support import FakeView


class SelectionResetTests(harness.QtRuntimeTests):
    def fixture(self):
        integration = self.qt.load("CuraIntegration").CuraIntegration(self.qt.Application())
        self.addCleanup(integration.close)
        follower = self.qt.load("PreviewFollower").PreviewFollower(integration)
        view = FakeView(layer=3, path=25, maximum=100)
        view.layer_data = object()
        view.getLayerData = lambda: view.layer_data
        integration._view = view
        integration._settle_until = 0
        follower.remember()
        outcomes = []
        integration.positionChanged.connect(lambda: outcomes.append(follower.detect_override()))
        module = ModuleType("UM.Scene.ToolHandle")
        module.ToolHandle = type("ToolHandle", (), {})
        modules = patch.dict(sys.modules, {"UM.Scene.ToolHandle": module})
        modules.start()
        self.addCleanup(modules.stop)
        return integration, follower, view, outcomes, module.ToolHandle

    def scene_stack(self, integration, view, child, *, removal=True):
        # Real native public callback order: childrenChanged invokes the view
        # before our integration's root callback, with the root as its argument.
        def calculateMaxPathsOnLayer(self, _layer):
            self.setPath(self.getMaxPaths())
            integration._position_changed()
        def _onSceneChanged(self):
            calculateMaxPathsOnLayer(self, self.getCurrentLayer())
        def removeChild(self, child):
            _onSceneChanged(view)
            integration._scene_changed(self)
        def addChild(self, scene_node):
            _onSceneChanged(view)
            integration._scene_changed(self)
        (removeChild if removal else addChild)(integration._root, child)

    def test_handle_removal_and_readdition_restore_path_without_invalidation(self):
        for removal in (True, False):
            integration, follower, view, outcomes, handle = self.fixture()
            generation = integration.generation
            self.scene_stack(integration, view, handle(), removal=removal)
            self.assertEqual(outcomes, [None])
            self.assertEqual(view.path, 25)
            self.assertTrue(follower.state.attached)
            self.assertEqual(integration.generation, generation)
            self.assertFalse(integration.incidental_path_reset)
            # A genuine drag to the exact same endpoint detaches immediately.
            view.setPath(100)
            integration._position_changed()
            self.assertEqual(outcomes[-1], "path")
            self.assertFalse(follower.state.attached)

    def test_real_scene_child_reset_detaches_and_invalidates(self):
        integration, follower, view, outcomes, _ = self.fixture()
        generation = integration.generation
        view.layer_data = object()
        self.scene_stack(integration, view, object())
        self.assertEqual(outcomes, ["path"])
        self.assertFalse(follower.state.attached)
        self.assertGreater(integration.generation, generation)

    def test_unchanged_toolpath_native_scene_refresh_restores_without_handle_assumption(self):
        integration, follower, view, outcomes, _ = self.fixture()
        self.scene_stack(integration, view, object())
        self.assertEqual(outcomes, [None])
        self.assertEqual(view.path, 25)
        self.assertTrue(follower.state.attached)

    def test_missing_native_type_is_fail_closed(self):
        integration, _, _, _, _ = self.fixture()
        with patch.dict(sys.modules, {"UM.Scene.ToolHandle": None}):
            self.assertFalse(integration._tool_handle_change())

    def test_minimum_path_change_is_not_absorbed(self):
        integration, follower, view, outcomes, handle = self.fixture()
        view.minimum_path = 1
        self.scene_stack(integration, view, handle())
        self.assertEqual(outcomes, ["path"])
        self.assertFalse(follower.state.attached)

    def test_layer_change_during_decorative_notification_still_detaches(self):
        integration, follower, view, outcomes, handle = self.fixture()
        view.layer = 4
        self.scene_stack(integration, view, handle())
        self.assertEqual(outcomes, ["layer"])
        self.assertFalse(follower.state.attached)

    def test_failed_public_path_restore_detaches(self):
        integration, follower, view, outcomes, handle = self.fixture()
        original = view.setPath
        view.setPath = lambda value: original(value) if value == 100 else None
        self.scene_stack(integration, view, handle())
        self.assertEqual(outcomes, ["path"])
        self.assertFalse(follower.state.attached)

    def test_native_layer_slider_recalculation_is_not_a_scene_refresh(self):
        integration, follower, view, outcomes, _ = self.fixture()
        def calculateMaxPathsOnLayer(self, _layer):
            self.setPath(self.getMaxPaths())
            integration._position_changed()
        def _onCurrentLayerNumChanged(self):
            calculateMaxPathsOnLayer(self, self.getCurrentLayer())
        _onCurrentLayerNumChanged(view)
        self.assertEqual(outcomes, ["path"])
        self.assertFalse(follower.state.attached)

    def test_endpoint_diagnostics_are_bounded(self):
        integration, _, _, _, _ = self.fixture()
        module = self.qt.load("CuraIntegration")
        with patch.object(module.Logger, "log") as log:
            for _ in range(10):
                integration.report_path_reset(25, True)
        self.assertEqual(log.call_count, 3)
        self.assertIn("test_endpoint_diagnostics_are_bounded", log.call_args.args[-1])

    def test_successful_restore_diagnostic_is_once_per_integration(self):
        integration, follower, view, outcomes, handle = self.fixture()
        module = self.qt.load("CuraIntegration")
        with patch.object(module.Logger, "log") as log:
            for _ in range(3):
                self.scene_stack(integration, view, handle())
        self.assertEqual(outcomes, [None, None, None])
        self.assertTrue(follower.state.attached)
        self.assertEqual(log.call_count, 1)
        self.assertIn("restored unchanged toolpath", log.call_args.args[1])
