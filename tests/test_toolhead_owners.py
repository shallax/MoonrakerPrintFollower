# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify import lifetime and complete appearance checkpoints without Qt.

from dataclasses import asdict, fields
import threading
import unittest
from unittest.mock import patch

from mpf.toolhead.AppearanceDraft import AppearanceDraft
from mpf.toolhead.ToolheadImportJobs import ToolheadImportJobs


class AppearanceDraftTests(unittest.TestCase):
    def test_all_fields_round_trip_through_an_independent_checkpoint(self):
        draft = AppearanceDraft(texts=['1', '2', '3'], manual=True, surface_detail=.7,
                                lights=[{'brightness': .8}], rotors=[{'rpm': 500}])
        for member in fields(draft):
            if member.type is dict:
                setattr(draft, member.name, {'0': {'value': .4}})
        mesh = object()
        checkpoint = draft.checkpoint(('printer-a', 'url-a'), mesh)
        expected = asdict(draft)
        draft.texts[0] = '99'
        draft.lights[0]['brightness'] = 0
        draft.body_colours['0']['value'] = 1
        restored = checkpoint.restore(('printer-a', 'url-a'), mesh)
        self.assertEqual(asdict(restored), expected)
        restored.rotors[0]['rpm'] = 0
        self.assertEqual(checkpoint.restore(('printer-a', 'url-a'), mesh).rotors[0]['rpm'], 500)
        self.assertIsNone(checkpoint.restore(('printer-b', 'url-a'), mesh))
        self.assertIsNone(checkpoint.restore(('printer-a', 'url-a'), object()))

    def test_model_reset_preserves_global_preferences_and_independent_defaults(self):
        draft = AppearanceDraft(surface_detail=.7, material_overrides={'plastic': {'roughness': .6}},
                                body_colours={'0': '#abcdef'}, lights=[{}], rotors=[{}])
        draft.reset_model()
        self.assertEqual(draft.surface_detail, .7)
        self.assertEqual(draft.material_overrides, {'plastic': {'roughness': .6}})
        self.assertEqual((draft.body_colours, draft.lights, draft.rotors), ({}, [], []))
        a, b = AppearanceDraft(), AppearanceDraft()
        a.texts[0] = '1'
        a.lights.append({})
        self.assertEqual((b.texts, b.lights), (['0', '0', '0'], []))


class ImportJobTests(unittest.TestCase):
    def setUp(self):
        self.completed, self.progress = [], []
        self.owner = ToolheadImportJobs('runtime-root',
            progress=lambda *args: self.progress.append(args),
            completed=lambda *args: self.completed.append(args))
        self.addCleanup(self.owner.close)

    def finish(self):
        self.owner.worker.join(3)
        self.assertFalse(self.owner.worker.is_alive())
        self.assertTrue(self.owner.busy, 'worker occupancy ends on owner-thread receipt')
        generation, model, error, path = self.completed[-1]
        return self.owner.receive(generation), model, error, path

    def test_stl_named_result_and_retirement_without_publication(self):
        mesh = object()
        with patch('mpf.toolhead.ToolheadImportJobs.read_stl', return_value=mesh):
            generation = self.owner.start('/tmp/model.stl', False)
            accepted, result, error, path = self.finish()
        self.assertTrue(accepted)
        self.assertFalse(self.owner.busy)
        self.assertEqual((result.mesh, result.key, result.name, error, path),
                         (mesh, '', 'model.stl', '', '/tmp/model.stl'))
        self.assertEqual(generation, 1)

    def test_step_progress_error_cancel_and_closed_admission(self):
        def install(root, cancelled, progress):
            self.assertEqual(root, 'runtime-root')
            progress('reader ready')
            return 'reader'
        with patch('mpf.toolhead.ToolheadImportJobs.install_runtime', side_effect=install), \
                patch('mpf.toolhead.ToolheadImportJobs.read_step', side_effect=ValueError('invalid STEP')):
            self.owner.start('/tmp/model.step', True)
            self.owner.cancel()
            accepted, result, error, _path = self.finish()
        self.assertFalse(accepted)
        self.assertIsNone(result.mesh)
        self.assertEqual(error, 'invalid STEP')
        self.assertTrue(self.owner.cancelled.is_set())
        self.assertEqual([text for _generation, text in self.progress],
                         ['reader ready', 'Converting STEP assembly…'])
        self.owner.close()
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            self.owner.start('/tmp/model.stl', False)

    def test_busy_service_refuses_a_second_reader_and_survives_retired_receiver(self):
        started, release = threading.Event(), threading.Event()
        def read(_path, _cancel):
            started.set()
            release.wait(3)
            return object()
        with patch('mpf.toolhead.ToolheadImportJobs.read_stl', side_effect=read):
            self.owner.start('/tmp/model.stl', False)
            self.assertTrue(started.wait(3))
            with self.assertRaises(RuntimeError):
                self.owner.start('/tmp/other.stl', False)
            self.owner.close()
            release.set()
            accepted, _result, _error, _path = self.finish()
            self.assertFalse(accepted)
        def retired(*_args):
            raise RuntimeError('QObject retired')
        owner = ToolheadImportJobs('runtime-root', progress=retired, completed=retired)
        with patch('mpf.toolhead.ToolheadImportJobs.read_stl', side_effect=RuntimeError()):
            owner.start('/tmp/model.stl', False)
            owner.worker.join(3)
            self.assertFalse(owner.worker.is_alive())
            owner.close()
