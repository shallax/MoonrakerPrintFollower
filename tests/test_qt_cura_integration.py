"""Executable qt cura integration contracts."""
from tests import qt_integration_support as harness

class CuraIntegrationLoadTests(harness.CuraIntegrationLoadTests):
    def test_scene_update_after_parse_keeps_the_confirmed_file_identity(self):
        path = self._make_file()
        self.cura._plugin_loaded_path = path
        self.cura._scene_changed()
        self.assertEqual(self.cura.plugin_loaded_path, path)

    def test_external_file_completion_clears_the_plugin_loaded_file_identity(self):
        self.cura._plugin_loaded_path = self._make_file()
        self.app.fileCompleted.emit(self._make_file("other.gcode"))
        self.assertIsNone(self.cura.plugin_loaded_path)

    def test_new_project_clears_file_identity_before_stale_layer_data_disappears(self):
        self.cura._plugin_loaded_path = self._make_file()
        self.app.workspaceLoaded.emit("")
        self.assertIsNone(self.cura.plugin_loaded_path)

    def test_heights_do_not_touch_cura_during_load_or_slicing_and_resume_afterwards(self):
        from unittest.mock import Mock
        view = Mock()
        view.getMaxLayers.return_value = 2
        view._getLayerHeight.side_effect = lambda layer: layer * .2
        self.cura._view = view
        self.cura._load_lease = object()
        self.assertEqual(self.cura.heights, ())
        view._calculateLayerHeightsCache.assert_not_called()
        self.cura._load_lease = None
        self.cura._slicing = True
        self.assertEqual(self.cura.heights, ())
        view._getLayerHeight.assert_not_called()
        batch = []
        self.cura._heights = batch
        self.cura._heights_built = 0
        self.cura._build_heights_step(batch)
        self.assertIsNone(self.cura._heights, "queued height work must retire when loading starts")
        self.cura._slicing = False
        self.assertEqual(self.cura.heights, (0, .2, .4))
        view._calculateLayerHeightsCache.assert_called_once()
        self.cura._view = None

    def test_load_refused_without_a_view(self):
        path = self._make_file()
        messages = []
        self.cura.loadFailed.connect(messages.append)
        self.assertFalse(self.cura.load(self._lease(path)))
        self.assertEqual(messages, ["Cura has no active printer yet"])
        self.assertEqual(self.app.loaded, [])
        self.assertEqual(self.releases, [path])  # released, never leaked

    def test_load_supersedes_a_pending_load(self):
        # The supersede ruling (the panel's fix): an explicit Load
        # while a load is pending PROCEEDS — Cura's silent-refusal
        # paths can latch `loading` for the whole watchdog window,
        # and refusing the user's retry would lock the button for
        # five minutes. The superseded lease parks as the watch
        # lease and releases; the new load holds the stage.
        path = self._make_file()
        self.cura._view = object()
        self.assertTrue(self.cura.load(self._lease(path)))
        second = self._make_file("two.gcode")
        messages = []
        self.cura.loadFailed.connect(messages.append)
        self.assertTrue(self.cura.load(self._lease(second)))
        self.assertEqual(messages, [])
        self.assertTrue(self.cura.loading)  # the new load holds the stage
        self.assertIn(path, self.releases)  # the superseded lease released, never leaked
        # QUrl spells a local file with '/' on every platform, so the
        # recorded spelling and the temp dir's own differ on Windows
        # only by separator and case: both loads reached Cura either way.
        self.assertEqual([harness.os.path.normcase(harness.os.path.normpath(entry)) for entry in self.app.loaded],
                         [harness.os.path.normcase(harness.os.path.normpath(path)),
                          harness.os.path.normcase(harness.os.path.normpath(second))])

    def test_watchdog_unsticks_loading_and_keeps_the_file(self):
        path = self._make_file()
        self.cura._view = object()
        self.cura.LOAD_WATCHDOG_MS = 50
        messages = []
        self.cura.loadFailed.connect(messages.append)
        self.assertTrue(self.cura.load(self._lease(path)))
        self.assertTrue(self.cura.loading)
        self.assertTrue(self._wait(lambda: len(messages) == 1))
        self.assertIn("did not confirm", messages[0])
        self.assertFalse(self.cura.loading)
        self.assertTrue(harness.os.path.exists(path))  # dropped, not deleted

    def test_watchdog_timed_out_load_completes_quietly(self):
        path = self._make_file()
        self.cura._view = object()
        self.cura.LOAD_WATCHDOG_MS = 50
        messages = []
        self.cura.loadFailed.connect(messages.append)
        self.assertTrue(self.cura.load(self._lease(path)))
        self.assertTrue(self._wait(lambda: not self.cura.loading))
        invalidated = []
        self.cura.invalidated.connect(invalidated.append)
        self.app.fileCompleted.emit(path)
        self.assertEqual(invalidated, [])  # absorbed, not "file replaced"
        self.assertEqual(self.cura.plugin_loaded_path, harness.os.path.abspath(path))
        self.assertIn(path, self.releases)  # released once the parse finished
        self.assertFalse(harness.os.path.exists(path))
