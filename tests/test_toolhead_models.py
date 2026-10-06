"""Printer-bound toolhead drafts across real queued Qt import completions."""
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

try:
    from PyQt6.QtCore import QCoreApplication, QUrl
    from mpf.toolhead.ToolheadModels import ToolheadModels
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

from mpf.geometry.ToolheadGeometry import mesh_from_arrays
from mpf.toolhead.ToolheadAssetStore import ToolheadAssetStore
from tests.test_toolhead_geometry import binary_stl


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 is required for toolhead draft lifecycle tests")
class ToolheadModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ToolheadAssetStore(os.path.join(self.temporary.name, "models"))
        self.saved = mesh_from_arrays([((1, 2, -3), (5, 2, -3), (1, 6, 7))])
        self.replacement = mesh_from_arrays([((20, 30, -8), (24, 30, -8), (20, 34, 2))])
        self.saved_key = self.store.publish(self.saved)
        self.config = SimpleNamespace(toolhead_model=self.saved_key,
                                      toolhead_model_name="saved.stl", toolhead_tip=[])
        self.identity = ("printer-a", "http://printer-a.local:7125", "")
        self.model = ToolheadModels(self.store, os.path.join(self.temporary.name, "runtime"),
                                   lambda: self.config, lambda: self.identity)
        self.addCleanup(self.model.close)
        self.addCleanup(self.retire_worker)
        self.release = threading.Event()

    def until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertTrue(condition(), "queued toolhead completion missed its deadline")

    def retire_worker(self):
        self.release.set()
        self.model.close()
        self.until(lambda: not self.model.busy)

    def import_replacement(self):
        filename = os.path.join(self.temporary.name, "replacement.stl")
        with open(filename, "wb") as target:
            target.write(binary_stl(self.replacement.triangles))
        self.model.choose(QUrl.fromLocalFile(filename).toString())
        self.until(lambda: not self.model.busy)
        self.assertIn("Ready", self.model.status)

    def blocked_import(self):
        entered = threading.Event()

        def reader(*_args):
            entered.set()
            if not self.release.wait(3):
                raise AssertionError("test did not release import")
            return self.replacement

        patched = patch("mpf.toolhead.ToolheadModels.read_stl", side_effect=reader)
        patched.start()
        self.addCleanup(patched.stop)
        self.model.choose(QUrl.fromLocalFile(os.path.join(self.temporary.name, "blocked.stl")).toString())
        self.assertTrue(entered.wait(1), "import worker did not enter the controlled reader")
        self.assertTrue(self.model.busy)

    def test_saved_model_and_manual_tip_rehydrate_when_the_editor_opens(self):
        self.config.toolhead_tip = [2.25, 3.5, -2]
        self.model.reset()
        np.testing.assert_array_equal(self.model.mesh.triangles, self.saved.triangles)
        self.assertEqual(self.model.name, "saved.stl")
        self.assertEqual(self.model.tip, (2.25, 3.5, -2))
        self.assertTrue(self.model.manual)
        self.assertEqual(self.model.fields()["toolhead_tip"], [2.25, 3.5, -2])

    def test_successful_import_stays_a_draft_until_explicit_save_fields(self):
        self.import_replacement()
        self.assertEqual(self.config.toolhead_model, self.saved_key)
        self.assertEqual(os.listdir(self.store.root), [self.saved_key + ".mesh"])
        fields = self.model.fields()
        self.assertNotEqual(fields["toolhead_model"], self.saved_key)
        self.assertEqual(fields["toolhead_model_name"], "replacement.stl")
        self.assertEqual(fields["toolhead_tip"], [])
        restored = self.store.load(fields["toolhead_model"])
        np.testing.assert_array_equal(restored.triangles, self.replacement.triangles)
        self.assertEqual(self.model.fields(), fields)

    def test_reset_discards_import_and_xyz_draft_without_creating_an_asset(self):
        self.import_replacement()
        self.model.setTip(2, "-7.5")
        self.model.reset()
        self.assertEqual(self.model.fields()["toolhead_model"], self.saved_key)
        self.assertEqual(self.model.tip, self.saved.automatic_tip)
        self.assertFalse(self.model.manual)
        self.assertEqual(os.listdir(self.store.root), [self.saved_key + ".mesh"])

    def test_failed_replacement_keeps_the_previous_unsaved_working_draft(self):
        self.import_replacement()
        self.model.setTip(0, "21.5")
        previous = self.model.mesh
        filename = os.path.join(self.temporary.name, "broken.stl")
        with open(filename, "wb") as target:
            target.write(b"this is not an STL")
        self.model.choose(QUrl.fromLocalFile(filename).toString())
        self.until(lambda: not self.model.busy)
        self.assertIn("Import failed", self.model.status)
        self.assertIs(self.model.mesh, previous)
        self.assertEqual(self.model.tip[0], 21.5)
        fields = self.model.fields()
        self.assertEqual(fields["toolhead_model_name"], "replacement.stl")
        self.assertEqual(fields["toolhead_tip"][0], 21.5)

    def test_cancel_import_preserves_prior_draft_and_rejects_late_result(self):
        self.import_replacement()
        previous = self.model.mesh
        self.model.setTip(0, "21.5")
        self.blocked_import()
        self.model.cancel()
        self.assertIs(self.model.mesh, previous)
        self.assertEqual(self.model.tip[0], 21.5)
        self.release.set()
        self.until(lambda: not self.model.busy)
        self.assertIs(self.model.mesh, previous)
        self.assertEqual(self.model.tip[0], 21.5)
        self.assertEqual(self.model.fields()["toolhead_model_name"], "replacement.stl")

    def test_printer_switch_retires_old_worker_before_it_can_replace_new_draft(self):
        self.blocked_import()
        second = mesh_from_arrays([((50, 60, 0), (54, 60, 0), (50, 64, 10))])
        second_key = self.store.publish(second)
        self.identity = ("printer-b", "http://printer-b.local:7125", "")
        self.config = SimpleNamespace(toolhead_model=second_key,
                                      toolhead_model_name="printer-b.stl", toolhead_tip=[51, 61, 1])
        self.model.reset()
        self.release.set()
        self.until(lambda: not self.model.busy)
        self.assertEqual(self.model.fields(), {"toolhead_model": second_key,
                                              "toolhead_model_name": "printer-b.stl",
                                              "toolhead_tip": [51, 61, 1], "toolhead_lights": []})
        np.testing.assert_array_equal(self.model.mesh.triangles, second.triangles)

    def test_changed_printer_identity_refuses_save_even_without_reset_signal(self):
        self.identity = ("printer-b", "http://printer-b.local:7125", "")
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.model.fields()

    def test_invalid_xyz_cannot_be_saved_then_reset_restores_automatic_anchor(self):
        for invalid in ("", "nan", "inf", "10001", "not a number"):
            with self.subTest(invalid=invalid):
                self.model.setTip(1, invalid)
                self.assertFalse(self.model.valid)
                with self.assertRaises(ValueError):
                    self.model.fields()
                self.model.automatic()
                self.assertTrue(self.model.valid)
                self.assertEqual(self.model.fields()["toolhead_tip"], [])

    def test_surface_pick_and_xyz_override_are_persisted_as_manual_coordinates(self):
        self.model.picked((2.5, 3.75, -1.25))
        self.assertEqual(self.model.fields()["toolhead_tip"], [2.5, 3.75, -1.25])
        self.model.setTip(2, "-2.125")
        self.assertEqual(self.model.fields()["toolhead_tip"], [2.5, 3.75, -2.125])
        self.model.automatic()
        self.assertEqual(self.model.tip, self.saved.automatic_tip)
        self.assertEqual(self.model.fields()["toolhead_tip"], [])

    def test_use_default_is_a_draft_until_config_adopts_its_fields(self):
        self.model.useDefault()
        self.assertEqual(self.model.name, "Default indicator")
        self.assertEqual(self.model.fields(), {"toolhead_model": "", "toolhead_model_name": "", "toolhead_tip": [], "toolhead_lights": []})
        self.assertEqual(self.config.toolhead_model, self.saved_key)
        self.model.reset()
        self.assertEqual(self.model.name, "saved.stl")

    def test_asset_write_failure_preserves_the_draft_for_retry(self):
        self.import_replacement()
        with patch.object(self.store, "publish", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.model.fields()
        self.assertEqual(self.config.toolhead_model, self.saved_key)
        self.assertEqual(self.model.name, "replacement.stl")
        fields = self.model.fields()
        np.testing.assert_array_equal(self.store.load(fields["toolhead_model"]).triangles,
                                      self.replacement.triangles)

    def test_missing_saved_asset_has_visible_refusal_and_usable_default_draft(self):
        os.unlink(os.path.join(self.store.root, self.saved_key + ".mesh"))
        self.config.toolhead_tip = [5, 5, 5]
        self.model.reset()
        self.assertIn("Saved model unavailable", self.model.status)
        self.assertEqual(self.model.fields(), {"toolhead_model": "", "toolhead_model_name": "", "toolhead_tip": [], "toolhead_lights": []})
        self.assertEqual(self.model.tip, (0, 0, 0))

    def test_remote_url_or_unsupported_format_never_start_an_import(self):
        for url in ("https://example.invalid/nozzle.stl",
                    QUrl.fromLocalFile(os.path.join(self.temporary.name, "model.obj")).toString()):
            with self.subTest(url=url):
                self.model.choose(url)
                self.assertFalse(self.model.busy)
                self.assertEqual(self.model.fields()["toolhead_model"], self.saved_key)
                self.assertIn("Choose", self.model.status)

    def test_cancelling_step_consent_preserves_previous_unsaved_model_and_tip(self):
        self.import_replacement()
        previous = self.model.mesh
        self.model.setTip(0, "21.5")
        pins = [("reader.whl", {"size": 100})]
        with patch("mpf.toolhead.ToolheadModels.runtime_assets", return_value=pins), \
                patch("mpf.toolhead.ToolheadModels.runtime_directory", return_value=os.path.join(self.temporary.name, "absent-reader")), \
                patch("mpf.toolhead.ToolheadModels.install_runtime") as installer:
            self.model.choose(QUrl.fromLocalFile(os.path.join(self.temporary.name, "head.step")).toString())
            self.assertTrue(self.model.needsDownload)
            self.assertFalse(self.model.valid)
            self.assertFalse(self.model.busy)
            installer.assert_not_called()
            self.model.cancel()
            self.assertFalse(self.model.needsDownload)
            self.assertTrue(self.model.valid)
            self.assertIs(self.model.mesh, previous)
            self.assertEqual(self.model.tip[0], 21.5)

    def test_close_retires_worker_without_join_or_late_draft_publication(self):
        self.blocked_import()
        previous = self.model.mesh
        changed = []
        self.model.changed.connect(lambda: changed.append(True))
        self.model.close()
        self.assertTrue(self.model.busy, "close must not wait for native conversion")
        self.release.set()
        self.until(lambda: not self.model.busy)
        self.assertIs(self.model.mesh, previous)
        self.assertEqual(changed, [])
        self.assertEqual(os.listdir(self.store.root), [self.saved_key + ".mesh"])

    def test_step_consent_runs_reader_worker_and_publishes_progress_before_completion(self):
        pins = [("reader.whl", {"size": 100})]
        runtime = os.path.join(self.temporary.name, "absent-reader")
        entered = threading.Event()
        seen = []
        self.model.changed.connect(lambda: seen.append(self.model.status))

        def install(_root, _cancelled, progress):
            progress("Downloading CAD reader: 1 / 2 MiB")
            return runtime

        def convert(_path, supplied_runtime, cancelled, *, progress):
            self.assertEqual(supplied_runtime, runtime)
            self.assertFalse(cancelled.is_set())
            progress("Building CAD geometry…")
            entered.set()
            if not self.release.wait(3):
                raise AssertionError("test did not release STEP conversion")
            return self.replacement

        with patch("mpf.toolhead.ToolheadModels.runtime_assets", return_value=pins), \
                patch("mpf.toolhead.ToolheadModels.runtime_directory", return_value=runtime), \
                patch("mpf.toolhead.ToolheadModels.install_runtime", side_effect=install) as installer, \
                patch("mpf.toolhead.ToolheadModels.read_step", side_effect=convert) as converter:
            path = os.path.join(self.temporary.name, "assembly.STP")
            self.model.choose(QUrl.fromLocalFile(path).toString())
            self.assertTrue(self.model.needsDownload)
            installer.assert_not_called()
            self.model.downloadAndImport()
            self.assertTrue(entered.wait(1))
            self.until(lambda: "Converting STEP assembly…" in seen)
            self.until(lambda: "Building CAD geometry…" in seen)
            self.assertIn("Downloading CAD reader: 1 / 2 MiB", seen)
            self.assertFalse(self.model.valid)
            with self.assertRaises(ValueError):
                self.model.fields()
            self.release.set()
            self.until(lambda: not self.model.busy)
            converter.assert_called_once()
            self.assertEqual(self.model.name, "assembly.STP")
            self.assertFalse(self.model.needsDownload)
            self.assertEqual(self.model.fields()["toolhead_model_name"], "assembly.STP")

    def test_verified_reader_starts_step_immediately_without_new_consent(self):
        runtime = os.path.join(self.temporary.name, "ready-reader")
        os.makedirs(runtime)
        with open(os.path.join(runtime, "verified.json"), "w") as marker:
            marker.write("{}")
        with patch("mpf.toolhead.ToolheadModels.runtime_assets", return_value=[("reader.whl", {"size": 100})]), \
                patch("mpf.toolhead.ToolheadModels.runtime_directory", return_value=runtime), \
                patch("mpf.toolhead.ToolheadModels.install_runtime", return_value=runtime), \
                patch("mpf.toolhead.ToolheadModels.read_step", return_value=self.replacement):
            self.model.choose(QUrl.fromLocalFile(os.path.join(self.temporary.name, "head.step")).toString())
            self.assertFalse(self.model.needsDownload)
            self.until(lambda: not self.model.busy)
            self.assertIn("Ready", self.model.status)

    def test_unsupported_step_runtime_refuses_before_worker_or_draft_change(self):
        previous = self.model.mesh
        with patch("mpf.toolhead.ToolheadModels.runtime_assets", side_effect=ValueError("No verified CAD reader")):
            self.model.choose(QUrl.fromLocalFile(os.path.join(self.temporary.name, "head.step")).toString())
        self.assertEqual(self.model.status, "No verified CAD reader")
        self.assertFalse(self.model.busy)
        self.assertFalse(self.model.needsDownload)
        self.assertIs(self.model.mesh, previous)
        self.assertEqual(self.model.fields()["toolhead_model"], self.saved_key)

    def test_busy_import_refuses_concurrent_selection_default_and_xyz_edits(self):
        self.blocked_import()
        previous = self.model.mesh
        original_tip = self.model.tip
        self.model.choose(QUrl.fromLocalFile(os.path.join(self.temporary.name, "other.stl")).toString())
        self.model.useDefault()
        self.model.setTip(0, "999")
        self.model.picked((999, 999, 999))
        self.assertIs(self.model.mesh, previous)
        self.assertEqual(self.model.tip, original_tip)
        self.release.set()
        self.until(lambda: not self.model.busy)

    def test_progress_for_retired_generation_or_closed_draft_cannot_replace_status(self):
        previous = self.model.status
        self.model._progress(self.model._generation - 1, "stale progress")
        self.assertEqual(self.model.status, previous)
        self.model.close()
        self.model._progress(self.model._generation, "closed progress")
        self.assertEqual(self.model.status, previous)

    def test_import_elapsed_timer_updates_without_rebuilding_draft_and_stops_on_completion(self):
        self.assertEqual(self.model.elapsedText, "")
        self.blocked_import()
        self.assertTrue(self.model._elapsed_timer.isActive())
        with patch('mpf.toolhead.ToolheadModels.time.monotonic', return_value=self.model._started + 3661):
            self.assertEqual(self.model.elapsedText, "Elapsed: 1:01:01")
        with patch('mpf.toolhead.ToolheadModels.time.monotonic', return_value=self.model._started + 185):
            self.assertEqual(self.model.elapsedText, "Elapsed: 3:05")
        changed, elapsed = [], []
        self.model.changed.connect(lambda: changed.append(True))
        self.model.elapsedChanged.connect(lambda: elapsed.append(True))
        self.model._elapsed_timer.timeout.emit()
        self.assertEqual(changed, [])
        self.assertEqual(elapsed, [True])
        self.model.cancel()
        self.assertTrue(self.model._elapsed_timer.isActive())
        self.release.set()
        self.until(lambda: not self.model.busy)
        self.assertFalse(self.model._elapsed_timer.isActive())
        self.assertEqual(self.model.elapsedText, "")

    def test_tip_properties_and_invalid_axis_keep_exact_draft_text(self):
        self.model.setTip(0, "2.125")
        self.model.setTip(1, "3.25")
        self.model.setTip(2, "-1.5")
        self.assertEqual((self.model.tipX, self.model.tipY, self.model.tipZ), ("2.125", "3.25", "-1.5"))
        self.model.setTip(3, "999")
        self.model.picked(None)
        self.assertEqual(self.model.tip, (2.125, 3.25, -1.5))
