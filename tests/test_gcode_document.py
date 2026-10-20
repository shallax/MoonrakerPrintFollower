# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Prove immutable standalone documents, source independence and incomplete-index refusal.

"""Consumer outcomes using real parsing, geometry preparation and exact boundaries."""
from collections.abc import Mapping
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import patch

from mpf.gcode.GCodeDocument import GCodeDocument
from mpf.gcode import GCodeIndex, PlateProgress
from mpf.plate import prepare, pack_shader
from tools.check_library import SAMPLE


class DocumentTests(unittest.TestCase):
    def test_file_is_not_needed_after_loading_and_documents_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'part.gcode'
            path.write_bytes(SAMPLE)
            document = GCodeDocument.from_file(path)
        other = GCodeDocument.from_bytes(SAMPLE.replace(b'X40', b'X50'))
        self.assertNotEqual(document, other)
        self.assertEqual(document.layer_count, 1)
        info = document.layer_info(0)
        self.assertEqual((info.layer, info.motion_count, info.indexed_motion_count), (0, 5, 5))
        self.assertTrue(info.complete)
        self.assertAlmostEqual(info.layer_height_mm, .2)
        self.assertEqual(info.byte_range[1], len(SAMPLE))
        self.assertAlmostEqual(document.boundary(0, 2).position[0], 40)
        self.assertAlmostEqual(other.boundary(0, 2).position[0], 50)
        self.assertTrue(pack_shader(prepare((('current', document.geometry(0)),))))
        with self.assertRaises(FrozenInstanceError):
            info.layer = 9

    def test_nested_snapshots_are_immutable_and_survive_cache_eviction(self):
        data = SAMPLE + b';LAYER:1\nG0 Z0.4\nG1 X40 E5\n'
        document = GCodeDocument.from_bytes(data)
        before = document.geometry(0)
        vertices = pack_shader(prepare((('current', before),)))

        def immutable(value):
            if isinstance(value, Mapping):
                with self.assertRaises(TypeError):
                    value['illegal'] = 1
                for child in value.values():
                    immutable(child)
            elif isinstance(value, tuple):
                for child in value:
                    immutable(child)
            else:
                self.assertIsInstance(value, (float, int, str))
        immutable(before)
        with patch.object(PlateProgress, '_PREPARED_LIMIT', 1):
            later = document.geometry(1)
        self.assertGreater(later['motions'], 0)
        self.assertEqual(pack_shader(prepare((('current', before),))), vertices)
        self.assertEqual(document.geometry(0), before)

    def test_parallel_readers_observe_consistent_snapshots(self):
        document = GCodeDocument.from_bytes(SAMPLE)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(document.geometry, [0] * 8))
        self.assertTrue(all(result == results[0] for result in results))

    def test_independent_documents_can_evict_shared_cache_entries_concurrently(self):
        documents = [GCodeDocument.from_bytes(SAMPLE.replace(b'X40', ('X%d' % (40 + i)).encode()))
                     for i in range(24)]
        previous = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, previous)
        sys.setswitchinterval(.000001)

        def read(document):
            for _ in range(200):
                self.assertEqual(document.geometry(0)['motions'], 5)
            return document.boundary(0, 2).position[0]

        with ThreadPoolExecutor(max_workers=12) as executor:
            self.assertEqual(list(executor.map(read, documents)), list(range(40, 64)))

    def test_boundary_crosses_layers_and_end_of_document_is_explicit(self):
        document = GCodeDocument.from_bytes(SAMPLE + b';LAYER:1\nG0 Z0.4\nG1 X40 E5\n')
        boundary = document.boundary(0, 5)
        self.assertEqual((boundary.selected_layer, boundary.next_layer, boundary.next_motion), (0, 1, 0))
        self.assertIsNone(document.boundary(1, 2))

    def test_invalid_layer_and_completed_motion_inputs_do_not_silently_clamp(self):
        document = GCodeDocument.from_bytes(SAMPLE)
        for layer in (True, False, -1, 1, .5, '0', None):
            for query in (document.layer_info, document.geometry, lambda layer: document.boundary(layer, 0)):
                with self.subTest(layer=layer), self.assertRaises(IndexError):
                    query(layer)
        for completed in (True, False, -1, 6, .5, '0', None):
            with self.subTest(completed=completed), self.assertRaises(ValueError):
                document.boundary(0, completed)
        self.assertEqual(GCodeDocument.from_bytes(b'').layer_count, 0)

    def test_cancelled_loading_never_returns_an_empty_or_partial_document(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            GCodeDocument.from_bytes(SAMPLE, cancel_event=cancel)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'part.gcode'
            path.write_bytes(SAMPLE)
            with self.assertRaises(CancelledError):
                GCodeDocument.from_file(path, cancel_event=cancel)
            cancel.clear()
            self.assertEqual(GCodeDocument.from_file(path, cancel_event=cancel).layer_count, 1)
            self.assertEqual(GCodeDocument.from_bytes(SAMPLE, cancel_event=cancel).layer_count, 1)

    def test_retained_motion_limit_does_not_masquerade_as_complete_geometry(self):
        with patch.object(GCodeIndex, '_MAX_MOTIONS_PER_LAYER', 2):
            document = GCodeDocument.from_bytes(SAMPLE)
        info = document.layer_info(0)
        self.assertEqual((info.motion_count, info.indexed_motion_count), (5, 2))
        self.assertFalse(info.complete)
        self.assertIsNone(document.boundary(0, 0))
        with self.assertRaisesRegex(ValueError, 'retained-motion limit'):
            document.geometry(0)

    def test_bad_optional_diameter_comment_does_not_prevent_loading(self):
        baseline = GCodeDocument.from_bytes(SAMPLE).geometry(0)
        for comment in (b';EXTRUDER_TRAIN.0.MATERIAL.DIAMETER:1..75\n',
                        b';EXTRUDER_TRAIN.0.MATERIAL.DIAMETER:.\n',
                        b';EXTRUDER_TRAIN.' + b'9' * 5000 + b'.MATERIAL.DIAMETER:1.75\n'):
            with self.subTest(comment=comment[:80]):
                document = GCodeDocument.from_bytes(comment + SAMPLE)
                self.assertEqual(document.geometry(0), baseline)
