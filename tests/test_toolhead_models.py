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

    def test_successful_profile_or_restore_actions_clear_old_refusal(self):
        for action in (lambda:self.model.setMaterialFinish('abs','roughness',.4),
                       lambda:self.model.resetMaterialFinish('abs','roughness'),
                       self.model.clearMaterialPaint):
            self.model._status='Too many selected faces; nothing changed.'
            action()
            self.assertEqual(self.model.status,'')

    def test_invisible_bodies_do_not_use_the_translucent_rotor_budget(self):
        from mpf.geometry.ToolheadGeometry import mesh_metadata
        metadata=mesh_metadata(self.saved)
        metadata['bodies']=[dict(name='Body',source='unknown',centre=None,axis=None) for _ in range(66)]
        mesh=mesh_from_arrays(np.repeat(self.saved.triangles,66,axis=0),[(1,0,0,0)]*66,body_ids=np.arange(66),metadata=metadata)
        self.model._mesh=mesh
        self.model.setRotor(dict(body=0,centre=[0,0,0],axis=[0,0,1],rpm=100,direction=1))
        self.assertEqual(len(self.model.rotors),1)
        self.model._body_opacity={str(index):.5 for index in range(65)}
        self.model.setRotor(dict(body=1,centre=[0,0,0],axis=[0,0,1],rpm=100,direction=1))
        self.assertEqual(len(self.model.rotors),1)
        self.assertIn('64 translucent',self.model.status)

    def test_opacity_body_face_precedence_reset_and_cancel_preserve_source(self):
        source = self.model.mesh.colours.copy()
        self.model.beginEdit()
        self.model.toggleOpacitySelection(0)
        self.model.setSelectedOpacity(.2)
        self.assertEqual(self.model.bodyOpacity, {'0': .2})
        self.model.clearOpacitySelection()
        self.model.selectOpacityKind('face')
        self.model.toggleOpacitySelection(0)
        self.model.setSelectedOpacity(0)
        self.assertEqual(self.model.faceOpacity, {'0': 0.})
        self.assertEqual(self.model.selectedOpacity, 0)
        self.model.resetSelectedOpacity()
        self.assertEqual(self.model.faceOpacity, {'0': 'imported'})
        self.assertEqual(self.model.selectedOpacity, 1)
        self.model.selectOpacityKind('body')
        self.model.toggleOpacitySelection(0)
        self.model.setSelectedOpacity(.7)
        # Whole-body edits clear descendant face edits; explicitly selected
        # mixed faces then receive the same value, without material changes.
        self.assertEqual(self.model.faceOpacity, {'0': .7})
        self.model.resetSelectedOpacity()
        self.assertEqual((self.model.bodyOpacity, self.model.faceOpacity), ({}, {}))
        self.model.setSelectedOpacity(.3)
        self.assertEqual(self.model.fields()['toolhead_body_opacity'], {'0': .3})
        self.model.endEdit(False)
        self.assertEqual((self.model.bodyOpacity, self.model.faceOpacity, self.model.opacitySelectionCount), ({}, {}, 0))
        np.testing.assert_array_equal(source, self.model.mesh.colours)

    def test_local_material_finishes_reset_independently_and_cancel_all_properties(self):
        self.model.beginEdit()
        self.model.toggleOpacitySelection(0)
        self.model.setSelectedMaterial('petg')
        self.model.setSelectedFinish('roughness', .8)
        self.model.setSelectedFinish('reflectivity', .7)
        self.model.setSelectedOpacity(.4)
        self.assertEqual(self.model.bodyMaterials, {'0': 'petg'})
        self.assertAlmostEqual(self.model.selectedRoughness, .8, places=6)
        self.model.clearOpacitySelection()
        self.model.selectOpacityKind('face')
        self.model.toggleOpacitySelection(0)
        self.model.setSelectedFinish('roughness', .1)
        self.model.resetSelectedFinish('roughness')
        self.assertEqual(self.model.faceFinishes, {'0': {'roughness': 'automatic'}})
        self.assertAlmostEqual(self.model.selectedRoughness, .25, places=6)
        self.assertAlmostEqual(self.model.selectedReflectivity, .7, places=6)
        self.model.setSelectedMaterial('automatic')
        self.assertEqual(self.model.selectedMaterial, 'unknown')
        self.assertEqual(self.model.bodyOpacity, {'0': .4})
        self.assertEqual(self.model.bodyFinishes, {'0': {'roughness': .8, 'reflectivity': .7}})
        self.assertEqual(self.model.fields()['toolhead_body_materials'], {'0': 'petg'})
        self.model.endEdit(False)
        self.assertEqual((self.model.bodyMaterials, self.model.surfaceMaterials, self.model.bodyFinishes, self.model.faceFinishes, self.model.bodyOpacity), ({}, {}, {}, {}, {}))

    def test_body_material_paint_preserves_other_properties_and_clears_descendant_type_only(self):
        self.model.selectMaterialPaint('glass')
        self.model.paintSurface(0)
        self.model.toggleOpacitySelection(0)
        self.model.setSelectedFinish('reflectivity', .3)
        self.model.setSelectedOpacity(.6)
        self.model.selectMaterialPaint('abs')
        self.model.paintBody(0)
        self.assertEqual(self.model.surfaceMaterials, {})
        self.assertEqual(self.model.bodyMaterials, {'0': 'abs'})
        self.assertEqual(self.model.bodyFinishes, {'0': {'reflectivity': .3}})
        self.assertEqual(self.model.bodyOpacity, {'0': .6})

    def test_opacity_overrides_reload_and_new_asset_clears_selection(self):
        self.config.toolhead_body_opacity = {'0': .4, '1000': .5}
        self.config.toolhead_face_opacity = {'0': .6}
        self.model.reset()
        self.assertEqual(self.model.bodyOpacity, {'0': .4})
        self.assertEqual(self.model.faceOpacity, {'0': .6})
        self.model.toggleOpacitySelection(0)
        self.model.useDefault()
        self.assertEqual((self.model.bodyOpacity, self.model.faceOpacity, self.model.opacitySelectionCount), ({}, {}, 0))

    def test_rotor_proposal_confirmation_telemetry_and_cancel_are_separate(self):
        self.model.beginEdit()
        self.model.pickedBody(0)
        proposal = self.model.rotorCandidate
        self.assertEqual(self.model.rotors, [])
        self.assertIn("Proposed axis", self.model.status)
        self.model.previewRotor(dict(proposal, rpm=60, fan="fan", direction=-1))
        self.assertEqual(self.model.rotors, [])
        self.assertEqual(self.model.rotorReadout, "Fan unavailable")
        self.model.setFanReadings({'fan': dict(available=True, speed=.5)})
        self.assertIn("Estimated from power · 30 RPM", self.model.rotorReadout)
        self.model.setFanReadings({'fan': dict(available=True, speed=1, rpm=0)})
        self.assertIn("Measured RPM · 0 RPM", self.model.rotorReadout)
        self.model.setRotor(self.model.rotorCandidate)
        self.assertEqual(self.model.rotors[0]['direction'], -1)
        self.assertFalse(self.model.rotorReadout.startswith("Proposal"))
        self.model.setRotor(dict(proposal, axis=[0,0,0]))
        self.assertIn("nonzero axis", self.model.status)
        self.assertEqual(len(self.model.rotors), 1)
        self.model.endEdit(False)
        self.assertEqual(self.model.fields()['toolhead_rotors'], [])
        self.assertEqual(self.model.rotorCandidate, {})
        self.model.pickedBody(0)
        self.model.setRotor(proposal)
        self.model.removeRotor(0)
        self.assertEqual(self.model.rotors, [])
        self.assertEqual(self.model.rotorCandidate, {})
        self.model.pickedBody(99999)
        self.assertEqual(self.model.rotorCandidate, {})

    def test_empty_occurrences_keep_stable_ids_but_cannot_be_rotors(self):
        from mpf.geometry.ToolheadRotors import rotors
        metadata = dict(materials=[dict(name='ABS', description='', source='step-material')],
            bodies=[dict(name=name, source='unknown', centre=None, axis=None) for name in ('First', 'Hidden', 'Last')])
        self.model._mesh = mesh_from_arrays(self.saved.triangles, body_ids=[2], metadata=metadata)
        self.assertEqual(self.model.bodies, [dict(label='Last', body=2)])
        self.model.pickedBody(1)
        self.assertEqual(self.model.rotorCandidate, {})
        self.assertIn('no visible triangles', self.model.status)
        row=dict(body=1, centre=[0,0,0], axis=[0,0,1], rpm=1000)
        self.assertEqual(rotors([row], self.model.mesh), [])
        self.model.pickedBody(2)
        self.assertEqual(self.model.rotorCandidate['body'],2)

    def test_multiple_bodies_keep_the_same_printer_fan_binding_in_saved_fields(self):
        metadata=dict(materials=[dict(name='ABS',description='',source='step-material')],
            bodies=[dict(name=name,source='unknown',centre=None,axis=None) for name in ('Left fan','Right fan')])
        self.model._mesh=mesh_from_arrays(np.concatenate((self.saved.triangles,self.saved.triangles+[10,0,0])),
            body_ids=[0,1],metadata=metadata)
        for body,direction,rpm in ((0,1,6000.),(1,-1,4000.)):
            self.model.pickedBody(body)
            self.model.setRotor(dict(self.model.rotorCandidate,fan='fan_generic cooling',direction=direction,rpm=rpm))
        saved=self.model.fields()['toolhead_rotors']
        self.assertEqual([row['fan'] for row in saved],['fan_generic cooling']*2)
        self.assertEqual([row['body'] for row in saved],[0,1])
        self.assertEqual([row['direction'] for row in saved],[1,-1])
        self.assertEqual([row['rpm'] for row in saved],[6000.,4000.])
        self.model.previewRotor(dict(self.model.rotorCandidate,rpm=1000.))
        self.assertTrue(self.model.rotorReadout=='Fan unavailable')
        self.model.setFanReadings({'fan_generic cooling':dict(available=True,speed=.5)})
        self.assertTrue(self.model.rotorReadout.startswith('Proposal'))

    def test_surface_detail_is_transactional_without_geometry_rebuild_or_publication_during_drag(self):
        mesh = self.model.mesh
        changed, preview = [], []
        self.model.changed.connect(lambda: changed.append(True))
        self.model.lightingPreviewChanged.connect(lambda: preview.append(True))
        self.model.beginEdit()
        self.model.previewSurfaceDetail(.8)
        self.assertEqual(self.model.surfaceDetail, .8)
        self.assertEqual((changed, preview), ([], [True]))
        self.assertIs(self.model.mesh, mesh)
        self.model.endEdit(False)
        self.assertEqual(self.model.surfaceDetail, .35)
        self.model.beginEdit()
        self.model.setSurfaceDetail(0)
        self.model.endEdit(True)
        self.assertEqual(self.model.fields()["toolhead_surface_detail"], 0)
        self.assertEqual(self.model.materials[0]["kind"], "unknown")
        self.config.toolhead_surface_detail = .7
        self.model.reset()
        self.assertEqual(self.model.surfaceDetail, .7)

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
                                              "toolhead_tip": [51, 61, 1], "toolhead_lights": [], "toolhead_surface_detail": .35, "toolhead_rotors": [], "toolhead_material_overrides": {}, "toolhead_surface_materials": {}, "toolhead_body_opacity": {}, "toolhead_face_opacity": {}, "toolhead_body_materials": {}, "toolhead_body_finishes": {}, "toolhead_face_finishes": {}})
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
        self.assertEqual(self.model.fields(), {"toolhead_model": "", "toolhead_model_name": "", "toolhead_tip": [], "toolhead_lights": [], "toolhead_surface_detail": .35, "toolhead_rotors": [], "toolhead_material_overrides": {}, "toolhead_surface_materials": {}, "toolhead_body_opacity": {}, "toolhead_face_opacity": {}, "toolhead_body_materials": {}, "toolhead_body_finishes": {}, "toolhead_face_finishes": {}})
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
        self.config.toolhead_rotors = [dict(body=0, centre=[1,2,3], axis=[0,0,1], rpm=3000, direction=1, fan='', blur=True)]
        self.model.reset()
        self.assertIn("Saved model unavailable", self.model.status)
        self.assertEqual(self.model.fields(), {"toolhead_model": "", "toolhead_model_name": "", "toolhead_tip": [], "toolhead_lights": [], "toolhead_surface_detail": .35, "toolhead_rotors": [], "toolhead_material_overrides": {}, "toolhead_surface_materials": {}, "toolhead_body_opacity": {}, "toolhead_face_opacity": {}, "toolhead_body_materials": {}, "toolhead_body_finishes": {}, "toolhead_face_finishes": {}})
        self.assertEqual(self.model.tip, (0, 0, 0))
        self.assertEqual(self.model.rotors, [])
        self.assertEqual(self.model.rotorCandidate, {})

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
        # Exact clock values avoid flooring a rounded 184.999999-second delta.
        # Patch the owner's module reference, never the process-wide clock.
        with patch('mpf.toolhead.ToolheadModels.time', SimpleNamespace(monotonic=lambda: 1000.0)):
            self.blocked_import()
        self.assertTrue(self.model._elapsed_timer.isActive())
        with patch('mpf.toolhead.ToolheadModels.time', SimpleNamespace(monotonic=lambda: 4661.0)):
            self.assertEqual(self.model.elapsedText, "Elapsed: 1:01:01")
        with patch('mpf.toolhead.ToolheadModels.time', SimpleNamespace(monotonic=lambda: 1185.0)):
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
