"""Per-object filament progress and finish estimates survive index caching."""
from __future__ import annotations

import tempfile
import unittest
import os
from array import array
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from mpf.gcode.GCodeIndex import build_index_from_bytes, build_index_from_file
from mpf.gcode.IndexCache import PersistentIndexCache
from mpf.gcode.IndexView import IndexView
from mpf.moonraker.MoonrakerProtocol import RemoteFileIdentity
from mpf.printing.PrintState import MotionProgress
from mpf.gcode.ObjectWork import (ObjectWorkTracker, object_remaining,
                                   object_work_fraction, valid_object_work)


SOURCE = (b"M82\n;LAYER:0\nEXCLUDE_OBJECT_START NAME=Alpha\n"
          b"G1 X10 Y0 Z0.2 E1\nEXCLUDE_OBJECT_END NAME=Alpha\n"
          b"EXCLUDE_OBJECT_START NAME=Beta\nG1 X20 Y0 Z0.2 E2\n"
          b"EXCLUDE_OBJECT_END NAME=Beta\n;TIME_ELAPSED:60\n;LAYER:1\n"
          b"EXCLUDE_OBJECT_START NAME=Alpha\nG1 X30 Y0 Z0.4 E3\n"
          b"EXCLUDE_OBJECT_END NAME=Alpha\n;TIME_ELAPSED:120\n")


class ObjectWorkTests(unittest.TestCase):
    def test_file_offsets_track_object_filament_and_finish_order(self):
        index = build_index_from_bytes(SOURCE)
        view = IndexView(("job",), index)
        offset = SOURCE.index(b";LAYER:1")
        metrics = view.object_metrics(offset, 120)
        self.assertEqual(index.object_work["Alpha"]["filament"], 2)
        self.assertEqual(index.object_work["Alpha"]["top"], 0.4)
        self.assertEqual(metrics["Alpha"]["progress"], 0.5)
        self.assertEqual(metrics["Beta"]["progress"], 1.0)
        self.assertEqual(metrics["Alpha"]["center"], [20.0, 0.0])
        self.assertIsNone(view.object_metrics(None)["Alpha"]["progress"])
        self.assertGreater(metrics["Alpha"]["remaining"], 0)
        self.assertEqual(metrics["Beta"]["remaining"], 0)
        boundary = MotionProgress(layer=0, split=1, motion_total=2)
        self.assertEqual(view.physical_file_offset(boundary), SOURCE.index(b"G1 X10"))

    def test_object_summaries_round_trip_through_cache(self):
        index = build_index_from_bytes(SOURCE)
        identity = RemoteFileIdentity("objects.gcode", len(SOURCE), 1.0, "objects")
        with tempfile.TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory)
            cache.save(identity, index)
            restored = cache.load(identity)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.object_work, index.object_work)

    def test_mesh_markers_supply_names_without_exclude_commands(self):
        index = build_index_from_bytes(b"M82\n;LAYER:0\n;MESH:part.stl\nG1 X1 Z0.2 E1\n"
                                       b";MESH:NONMESH\nG1 X2 E2\n")
        self.assertEqual(list(index.object_work), ["part.stl"])
        self.assertEqual(index.object_work["part.stl"]["filament"], 1)

    def test_compact_scan_keeps_object_summaries(self):
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as handle:
            handle.write(SOURCE)
            path = handle.name
        try:
            index = build_index_from_file(path, compact=True)
        finally:
            os.remove(path)
        self.assertEqual(index.object_work["Alpha"]["filament"], 2)

    def test_file_offset_falls_back_to_layer_fraction_when_motion_offsets_are_absent(self):
        index = build_index_from_bytes(SOURCE)
        view = IndexView(("job",), index)
        start, end = index.ranges[0]
        self.assertIsNone(view.physical_file_offset(None))
        self.assertIsNone(view.physical_file_offset(MotionProgress(-1, 1, 2)))
        self.assertEqual(view.physical_file_offset(MotionProgress(0, 0, 2)), start)
        index.motion_offsets[0] = array("I")
        self.assertEqual(view.physical_file_offset(MotionProgress(0, 2, 4, partial=0.5)),
                         int(start + (end - start) * 0.625))
        self.assertIsNone(view.physical_file_offset(MotionProgress(0, 2, 0)))
        self.assertEqual(IndexView(("empty",), SimpleNamespace(ranges=[])).object_metrics(0), {})

    def test_cached_object_summary_rejects_corruption_and_oversized_records(self):
        row = deepcopy(build_index_from_bytes(SOURCE).object_work["Alpha"])
        self.assertTrue(valid_object_work({"Alpha": row}, len(SOURCE)))
        self.assertFalse(valid_object_work([], len(SOURCE)))
        self.assertFalse(valid_object_work({"": row}, len(SOURCE)))
        damaged = deepcopy(row)
        damaged["filament"] = float("nan")
        self.assertFalse(valid_object_work({"Alpha": damaged}, len(SOURCE)))
        damaged = deepcopy(row)
        damaged["layers"][0] = ["bad", 10, 0, 1]
        self.assertFalse(valid_object_work({"Alpha": damaged}, len(SOURCE)))
        damaged = deepcopy(row)
        damaged["layers"][0][0] = -1
        self.assertFalse(valid_object_work({"Alpha": damaged}, len(SOURCE)))
        damaged = deepcopy(row)
        damaged["last"] -= 1
        self.assertFalse(valid_object_work({"Alpha": damaged}, len(SOURCE)))
        with patch("mpf.gcode.ObjectWork.MAX_CHECKPOINTS", 1):
            self.assertFalse(valid_object_work({"Alpha": row}, len(SOURCE)))

    def test_object_scan_discards_unbounded_names_and_checkpoints(self):
        tracker = ObjectWorkTracker()
        tracker.open_layer()
        with patch("mpf.gcode.ObjectWork.MAX_OBJECTS", 1):
            tracker.marker(b"EXCLUDE_OBJECT_START NAME=first")
            tracker.add(10, 0, 0, 0.2, 1)
            tracker.marker(b"EXCLUDE_OBJECT_START NAME=second")
            tracker.add(20, 1, 0, 0.2, 1)
        self.assertEqual(tracker.finish(), {})
        tracker.open_layer()  # overflow stays terminal
        self.assertEqual(tracker.finish(), {})

        tracker = ObjectWorkTracker()
        with patch("mpf.gcode.ObjectWork.MAX_CHECKPOINTS", 1):
            tracker.open_layer()
            tracker.marker(b"EXCLUDE_OBJECT_START NAME=first")
            tracker.add(10, 0, 0, 0.2, 1)
            tracker.open_layer()
            tracker.add(20, 1, 0, 0.4, 1)
            self.assertEqual(tracker.finish(), {})

    def test_object_progress_and_eta_handle_missing_or_invalid_timing(self):
        row = deepcopy(build_index_from_bytes(SOURCE).object_work["Alpha"])
        self.assertIsNone(object_work_fraction({"filament": 0}, 10))
        self.assertEqual(object_work_fraction(row, len(SOURCE) + 1), 1.0)
        self.assertIsNone(object_remaining(row, 0, len(SOURCE), None, [(0, len(SOURCE))], [100]))
        self.assertIsNone(object_remaining(row, 0, len(SOURCE), 50, [], []))
        self.assertIsNone(object_remaining(row, 0, len(SOURCE), 50,
                                           [(0, len(SOURCE))], [None]))
