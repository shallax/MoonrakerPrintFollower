# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Verify reusable geometry cancellation, cache eviction and stdlib fallback.

"""Deterministic work-boundary tests for the Qt-free geometry owner."""
from array import array
from itertools import count
import threading
import unittest
from unittest.mock import Mock, patch
from mpf.plate import ToolpathGeometry as geometry


class ToolpathGeometryTests(unittest.TestCase):
    def setUp(self):
        geometry._CACHE.clear()
        self.addCleanup(geometry._CACHE.clear)

    def payload(self, edges=1):
        return {'motions': edges, 'classes': {'WALL-OUTER': [tuple((i, 0, i) for i in range(edges+1))]}}

    def test_cancellation_at_each_preparation_checkpoint_publishes_and_caches_nothing(self):
        for edges, times in ((300, count()), (1, count()), (1, iter((0, 0, 1)))):
            with self.subTest(edges=edges, times=type(times).__name__):
                payload = self.payload(edges)
                cancel = Mock()
                cancel.is_set.side_effect = [False, True]
                with patch.object(geometry.time, 'perf_counter', side_effect=lambda times=times: next(times)):
                    self.assertEqual(geometry.prepare((('current', payload),), cancel), ())
                self.assertEqual(len(geometry._CACHE), 0)
                self.assertEqual(len(geometry.prepare((('current', payload),))[0][2]), edges)
                geometry._CACHE.clear()

    def test_long_work_yields_without_losing_edges_or_metrics(self):
        payload = self.payload(300)
        baseline = geometry.prepare((('current', payload),))
        geometry._CACHE.clear()
        ticks = count()
        with patch.object(geometry.time, 'perf_counter', side_effect=lambda: next(ticks)), \
                patch.object(geometry.time, 'sleep') as pause:
            actual = geometry.prepare((('current', payload),))
        self.assertEqual(actual, baseline)
        self.assertGreater(pause.call_count, 1)

    def test_cache_evicts_old_payload_without_changing_live_result(self):
        first, second = self.payload(), self.payload()
        result = geometry.prepare((('current', first),))
        charge = next(iter(geometry._CACHE.values()))[2]
        with patch.object(geometry, '_CACHE_BYTES', charge):
            self.assertEqual(geometry.prepare((('current', second),)), result)
        self.assertEqual(len(geometry._CACHE), 1)
        self.assertEqual(next(iter(geometry._CACHE.values()))[0], (('current', second),))
        self.assertIsNot(next(iter(geometry._CACHE.values()))[0][0][1], first)

    def test_stroke_work_yields_and_cancellation_does_not_publish_partial_triangles(self):
        prepared = geometry.prepare((('current', self.payload(300)),))
        baseline = geometry.stroke_geometry(prepared, 2, 2, 2)
        cancel = threading.Event()
        cancel.set()
        ticks = count()
        with patch.object(geometry.time, 'perf_counter', side_effect=lambda: next(ticks)):
            self.assertEqual(geometry.stroke_geometry(prepared, 2, 2, 2, cancel=cancel), ())
        ticks = count()
        with patch.object(geometry.time, 'perf_counter', side_effect=lambda: next(ticks)), \
                patch.object(geometry.time, 'sleep') as pause:
            self.assertEqual(geometry.stroke_geometry(prepared, 2, 2, 2), baseline)
        self.assertGreater(pause.call_count, 1)

    def test_numpy_absence_preserves_strokes_events_and_completion_counts(self):
        prepared = geometry.prepare((('current', {**self.payload(), 'retractions': [(3, 4, 1)]}),))
        accelerated = geometry.pack_shader(prepared)
        with patch.dict('sys.modules', {'numpy': None}):
            fallback = geometry.pack_shader(prepared)
        self.assertEqual(accelerated, fallback)
        self.assertEqual(geometry.marker_counts(fallback, 2), (('RETRACTION', 1),))
        marks = array('f')
        marks.frombytes(geometry.marker_geometry(fallback, 1, 1, 1, False, True, False))
        self.assertGreater(len(marks), 0)
