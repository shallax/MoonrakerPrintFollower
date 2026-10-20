# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Verify independent consumer retention, release and generation-scoped worker demands.

"""Residency belongs to consumers, never the parsed file or its serialised content."""
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mpf.gcode.cache import IndexResidency
from mpf.gcode import LayerMotionIndex, build_index_from_file, hydrate_layer_from_file


class IndexResidencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'layers.gcode'
        self.path.write_bytes(b''.join(b';LAYER:%d\nG1 X%d\n' % (n,n+1) for n in range(10)))
        self.index = build_index_from_file(self.path, compact=True)

    def hydrate(self, layer, retention):
        self.assertTrue(hydrate_layer_from_file(self.index, self.path, layer, retention=retention))

    def test_two_readers_keep_independent_windows_and_release_only_their_own(self):
        demands = IndexResidency()
        first, second = object(), object()
        demands.set_window(first, 1)
        demands.set_window(second, 8)
        for layer in (1, 8, 4): self.hydrate(layer, demands.layers)
        self.assertEqual(self.index.hydrated_layers, {1, 4, 8})
        demands.set_window(first, None)
        self.hydrate(5, demands.layers)
        self.assertEqual(self.index.hydrated_layers, {4, 5, 8})
        self.assertEqual(self.index.motion_count(1), 1)
        self.assertEqual(demands.anchor(second), 8)
        self.assertIsNone(demands.anchor(first))
        self.assertTrue(isinstance(demands.layers(), frozenset))
        self.assertFalse({'followed_layer','manual_anchor'} & {field.name for field in fields(LayerMotionIndex)})

    def test_a_worker_reads_latest_demands_at_completion(self):
        demands = IndexResidency()
        demands.set_window('reader', 1)
        self.hydrate(1, demands.layers)
        consulted = []
        def completion_demands():
            consulted.append(True)
            demands.set_window('reader', 8)
            return demands.layers()
        self.hydrate(8, completion_demands)
        self.assertEqual(consulted, [True])
        self.assertEqual(self.index.hydrated_layers, {8})

    def test_a_new_generation_does_not_redirect_the_old_workers_capability(self):
        previous = IndexResidency()
        previous.set_window('reader', 1)
        captured = previous.layers
        current = IndexResidency()
        current.set_window('reader', 8)
        self.hydrate(1, captured)
        self.hydrate(5, captured)
        self.assertEqual(self.index.hydrated_layers, {1,5})
        self.assertEqual(current.layers(), frozenset({7,8,9}))

    def test_live_matching_without_a_file_position_keeps_progress_unavailable(self):
        from mpf.application.indexing.IndexMotionTracking import IndexMotionTracking
        from mpf.gcode import IndexView
        tracking = IndexMotionTracking()
        view = IndexView(('layers.gcode', self.path.stat().st_size, 1), self.index)
        progress = tracking.observe(view, None, 0, None, (0,0,0))
        self.assertIsNone(progress.split)
        self.assertEqual(progress.method, 'unavailable')
        self.assertIsNone(tracking.tracker.key)

    def test_invalid_demands_leave_existing_windows_unchanged(self):
        demands = IndexResidency()
        demands.set_window('reader', 0, radius=2)
        self.assertEqual(demands.layers(), frozenset({0,1,2}))
        for anchor, radius in ((True,1), (-1,1), (1.5,1), (0,True), (0,-1), (0,1.5)):
            with self.subTest(anchor=anchor,radius=radius), self.assertRaises(ValueError):
                demands.set_window('reader', anchor, radius)
        self.assertEqual(demands.layers(), frozenset({0,1,2}))
