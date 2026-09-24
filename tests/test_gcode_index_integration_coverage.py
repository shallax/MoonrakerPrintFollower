"""Integration coverage for the gcode index and the Cura lifecycle adapter.

Two surfaces share this file because they are the pair that answers "what
is printing" to Cura: GCodeIndex turns a job file into layer ranges and
motion geometry, CuraIntegration keeps the plugin's view of Cura's scene,
view, lease and slicing state coherent across swaps.

The Cura half runs against the real plugin code with the harness's minimal
Cura host doubles (tests/qt_runtime_support.runtime), so every branch here
exercises production behaviour — no plugin method is copied or mocked.

Since there is no coverage figure for a test that asserts a constant, several
cases here pin *which* fallback answered rather than only that one did: the
returned method name ("motion index" / "byte position" / "held ..." /
"(monotonic)") is the pair of the value and the evidence.

Leftover lines, and why no test reaches them:

GCodeIndex.py
- 227-228 (_parse_axes): the guard covers a non-numeric axis value, but the
  scan regex only matches digits and signs, so float() cannot fail.
- 349-350, 395-396, 403-404 (stats / marker / elapsed coercions): the same
  shape — each value has already matched a numeric regex, so int()/float()
  cannot raise on it.
- 667-668 (build_index_from_bytes): os.remove on a temp file this process
  just created; the failure needs the file to be taken away mid-call.
- 711 (cache header length): _read_exact either returns exactly four bytes
  or raises, so the length test is dead behind it.
- 764 (post-frombytes length): frombytes fills exactly count items from
  count * itemsize bytes, so the ragged-column check cannot fire there.

CuraIntegration.py
- 367: the release callback disconnects itself after its own lease closes;
  Qt never invokes a disconnected slot, so no reachable order of events
  makes that disconnect raise.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
from array import array
from types import SimpleNamespace
from unittest.mock import patch

import plugins.GCodeIndex as gcode_index
from plugins.GCodeIndex import (
    FLOOR_LOOKBACK,
    LayerMotionIndex,
    PersistentIndexCache,
    _CACHE_MAGIC,
    _CACHE_VERSION,
    _emit_progress,
    _read_exact,
    build_index_from_bytes,
    build_index_from_file,
    hydrate_layer_from_file,
)
from plugins.MoonrakerProtocol import RemoteFileIdentity
from tests.qt_runtime_support import QT_AVAILABLE, runtime


def _write_temp(data, prefix="index-", suffix=".gcode"):
    handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=False)
    handle.write(data)
    handle.close()
    return handle.name


def _one_layer(points, offsets=None, **overrides):
    """A single-layer index whose motion arrays follow *points* exactly.

    Hand-built arrays (rather than slicing a real file) are the only way to
    pin the geometry the refinement search reasons about: repeated points,
    ragged arrays and missing layer starts are shapes the scanner cannot be
    steered into producing.
    """
    if offsets is None:
        offsets = [10 * (index + 1) for index in range(len(points))]
    fields = dict(
        ranges=[(0, offsets[-1] + 10 if offsets else 10)],
        motion_offsets=[array("Q", offsets)],
        motion_x=[array("f", [point[0] for point in points])],
        motion_y=[array("f", [point[1] for point in points])],
        motion_z=[array("f", [point[2] for point in points])],
        layer_start_positions=[points[0]] if points else [],
        layer_start_absolute=[True],
        layer_start_units=[1.0],
    )
    fields.update(overrides)
    index = LayerMotionIndex(**fields)
    index.hydrated_layers = {0}
    return index


class LayerEstimatorTests(unittest.TestCase):
    """LayerMotionIndex's own arithmetic: the fallbacks every consumer
    leans on when a file is degraded, hostile or only partly hydrated."""

    def test_index_surface_guards_out_of_range_layers(self):
        index = build_index_from_bytes(b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n")
        self.assertTrue(index)
        self.assertEqual(index.layer_count(), 2)
        self.assertEqual(index.motion_count(0), 1)
        # Out-of-range and negative layers answer 0 rather than raising:
        # the service polls the followed layer before the index is bound.
        self.assertEqual(index.motion_count(-1), 0)
        self.assertEqual(index.motion_count(9), 0)
        self.assertFalse(LayerMotionIndex())

    def test_file_fraction_names_the_basis_it_used(self):
        self.assertEqual(LayerMotionIndex().file_fraction(0, 0), (0.0, "no index"))
        # A zero-width range (one marker immediately after another) cannot
        # yield a fraction.
        self.assertEqual(LayerMotionIndex(ranges=[(10, 10)]).file_fraction(0, 0),
                         (0.0, "invalid range"))
        # No motion data (compact, not yet hydrated): byte position.
        self.assertEqual(LayerMotionIndex(ranges=[(0, 100)],
                                          motion_offsets=[array("Q")]).file_fraction(0, 50),
                         (0.5, "byte position"))
        index = build_index_from_bytes(b";LAYER:0\n" + b"G1 X1\n" * 4)
        position = int(index.motion_offsets[0][1])
        self.assertEqual(index.file_fraction(0, position), (0.5, "motion index"))
        # The position is clamped into the range, so a stale offset from
        # the previous job can never report more than complete.
        self.assertEqual(index.file_fraction(0, 10 ** 9), (1.0, "motion index"))
        self.assertEqual(index.file_fraction(0, -50)[0], 0.0)

    def test_refinement_falls_back_without_usable_motion_data(self):
        points = [(0.0, 0.0, 0.2), (10.0, 0.0, 0.2), (20.0, 0.0, 0.2)]
        index = _one_layer(points)
        live = (10.0, 0.0, 0.2)
        # No live position at all, and a position too short to be XYZ.
        self.assertEqual(index.refined_fraction(0, 30, None)[1], "motion index")
        self.assertEqual(index.refined_fraction(0, 30, (1.0, 2.0))[1], "motion index")
        # A layer with no motion column of its own: the coarse fraction
        # survives, the search does not run.
        ragged = _one_layer(points, motion_x=[])
        self.assertEqual(ragged.refined_fraction(0, 30, live)[1], "motion index")
        # A layer the index does not cover at all.
        self.assertEqual(index.refined_fraction(4, 30, live), (0.0, "no index"))
        # A motion-free layer keeps its byte position.
        empty = LayerMotionIndex(ranges=[(0, 100)], motion_offsets=[array("Q")],
                                 motion_x=[array("f")], motion_y=[array("f")],
                                 motion_z=[array("f")])
        self.assertEqual(empty.refined_fraction(0, 30, live), (0.3, "byte position"))
        # Live coordinates that are not numbers.
        self.assertEqual(index.refined_fraction(0, 30, ("x", "y", "z"))[1], "motion index")

    def test_refinement_rejects_an_off_model_live_position(self):
        index = _one_layer([(0.0, 0.0, 0.2), (10.0, 0.0, 0.2), (20.0, 0.0, 0.2)])
        # A Z-lift or an off-path move is no evidence of progress: with a
        # floor the last refined value is held, never the parser fraction.
        self.assertEqual(index.refined_fraction(0, 30, (500.0, 500.0, 500.0), minimum_fraction=0.4),
                         (0.4, "held (refined unavailable)"))
        # Without a prior value the coarse estimate is all there is.
        self.assertEqual(index.refined_fraction(0, 30, (500.0, 500.0, 500.0)),
                         (1.0, "motion index"))

    def test_refinement_holds_the_floor_when_the_window_is_exhausted(self):
        # An inflated floor sample must not raise or rewind: when the whole
        # bounded search window sits below the floor, the coarse fraction
        # is clamped up to it.
        index = _one_layer([(float(value), 0.0, 0.2) for value in range(300)])
        self.assertGreater(len(index.motion_offsets[0]), FLOOR_LOOKBACK)
        self.assertEqual(index.refined_fraction(0, 0, (0.0, 0.0, 0.2), minimum_fraction=1.0),
                         (1.0, "motion index (monotonic)"))

    def test_refinement_uses_the_first_motion_when_starts_are_missing(self):
        # Older caches carry no per-layer start position; the first motion
        # is then the segment origin rather than a crash.
        index = _one_layer([(0.0, 0.0, 0.2), (10.0, 0.0, 0.2), (20.0, 0.0, 0.2)],
                           layer_start_positions=[])
        fraction, method = index.refined_fraction(0, 30, (15.0, 0.0, 0.2))
        self.assertEqual(method, "live position")
        self.assertAlmostEqual(fraction, 2.5 / 3, places=3)

    def test_refinement_matches_a_zero_length_segment(self):
        # A repeated point makes the projection degenerate; the segment end
        # stands in, so the search still lands on the right motion.
        index = _one_layer([(0.0, 0.0, 0.2)] * 3)
        fraction, method = index.refined_fraction(0, 20, (0.0, 0.0, 0.2))
        self.assertEqual(method, "live position")
        self.assertAlmostEqual(fraction, 1 / 3, places=3)

    def test_refinement_tracks_the_live_position_behind_the_parser(self):
        index = _one_layer([(0.0, 0.0, 0.2), (10.0, 0.0, 0.2),
                            (20.0, 0.0, 0.2), (30.0, 0.0, 0.2)])
        # The nozzle sits halfway along the third segment while the file
        # position has already reached the fourth motion.
        fraction, method = index.refined_fraction(0, 40, (25.0, 0.0, 0.2))
        self.assertEqual(method, "live position")
        self.assertAlmostEqual(fraction, 0.875, places=3)

    def test_refinement_applies_the_monotonic_clamp_to_its_result(self):
        index = _one_layer([(0.0, 0.0, 0.2), (10.0, 0.0, 0.2),
                            (20.0, 0.0, 0.2), (30.0, 0.0, 0.2)])
        self.assertEqual(index.refined_fraction(0, 20, (5.0, 0.0, 0.2), minimum_fraction=0.9),
                         (0.9, "live position (monotonic)"))
        # A floor that cannot be read as a number is dropped, not fatal.
        fraction, method = index.refined_fraction(0, 20, (5.0, 0.0, 0.2),
                                                  minimum_fraction="nonsense")
        self.assertEqual(method, "live position")
        self.assertLess(fraction, 0.9)

    def test_progress_emission_survives_a_closed_handle(self):
        # The every-4096-lines progress beat must never take the scan down:
        # a handle that has gone away simply stops reporting.
        seen = []
        path = _write_temp(b"G1 X1\n", prefix="progress-")
        self.addCleanup(os.remove, path)
        with open(path, "rb") as live:
            _emit_progress(live, seen.append)
        self.assertEqual(seen, [0.0])
        closed = open(path, "rb")
        closed.close()
        _emit_progress(closed, seen.append)
        self.assertEqual(seen, [0.0])


class IndexScanTests(unittest.TestCase):
    """The single-pass scanner: marker sniffing, the G-code state machine,
    the hardening caps and the layer-map fallback chain."""

    def setUp(self):
        self._paths = []

    def _write(self, data):
        path = _write_temp(data, prefix="scan-")
        self.addCleanup(os.remove, path)
        return path

    def test_an_unreadable_path_falls_back_then_raises(self):
        # The size probe, the marker sniff and the winner census all
        # tolerate a file they cannot open; the scan itself is where the
        # caller has to hear about it.
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                build_index_from_file(os.path.join(directory, "gone.gcode"))

    def test_scan_reports_its_stage_and_byte_progress(self):
        stages = []
        progress = []
        path = self._write(b";LAYER:0\n" + b"G1 X1 Y1 Z0.2\n" * 4200)
        index = build_index_from_file(path, stage=stages.append, progress=progress.append)
        self.assertEqual(stages, ["Scanning layers"])
        # The 4096-line beat is the only report for a file this size, and
        # it must stay inside [0, 1].
        self.assertTrue(progress)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in progress))
        self.assertEqual(index.motion_count(0), 4200)

    def test_a_late_marker_still_wins_the_census(self):
        # No marker inside the sniff window: the census pass reads on until
        # it finds the format the file actually uses.
        head = b"; generated by a slicer\n" * 12000
        self.assertGreater(len(head), 262144)
        index = build_index_from_file(self._write(head + b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n"))
        self.assertEqual(index.layer_count(), 2)

    def test_a_file_without_markers_indexes_empty(self):
        index = build_index_from_bytes(b"G1 X1\nG1 X2\nM104 S200\n")
        self.assertFalse(index)
        self.assertEqual(index.layer_count(), 0)

    def test_cancellation_before_and_after_the_scan(self):
        class Cancel:
            def __init__(self, true_after):
                self.calls = 0
                self.true_after = true_after

            def is_set(self):
                self.calls += 1
                return self.calls > self.true_after

        data = b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n"
        # Cancelled before the first line: nothing is built at all.
        self.assertFalse(build_index_from_file(self._write(data), Cancel(0)))
        # Cancelled between the last line and the return: the finished
        # index is discarded rather than handed to a dead generation.
        late = Cancel(1)
        self.assertFalse(build_index_from_file(self._write(data), late))
        self.assertGreaterEqual(late.calls, 2)

    def test_oversized_lines_are_skipped(self):
        giant = b"G1 X1" + b" " * 70000 + b"\n"
        index = build_index_from_bytes(b";LAYER:0\n" + giant + b"G1 X5\n;LAYER:1\nG1 X6\n")
        self.assertEqual(index.layer_count(), 2)
        self.assertEqual(index.motion_count(0), 1)

    def test_the_layer_block_cap_degrades_to_coarse_ranges(self):
        # Past the cap the marker stream is ignored: the last tracked block
        # already closed, and later markers open nothing.
        data = b"".join(b";LAYER:%d\nG1 X1\n" % layer for layer in range(6))
        with patch.object(gcode_index, "_MAX_LAYER_BLOCKS", 2):
            index = build_index_from_bytes(data)
        self.assertEqual(index.layer_count(), 2)

    def test_the_per_layer_motion_cap_truncates_path_data(self):
        data = b";LAYER:0\n" + b"".join(b"G1 X%d\n" % value for value in range(10))
        with patch.object(gcode_index, "_MAX_MOTIONS_PER_LAYER", 3):
            index = build_index_from_bytes(data)
        # The PATH DATA truncates at the cap; the count stays the
        # walk's honest total.
        self.assertEqual(len(index.motion_offsets[0]), 3)
        self.assertEqual(index.motion_count(0), 10)
        # The byte range still spans the whole layer, so following degrades
        # to the coarse fraction rather than stalling.
        self.assertGreater(index.ranges[0][1] - index.ranges[0][0], 10)

    def test_inches_and_relative_motion_are_tracked_through_the_scan(self):
        data = b"""G20
G90
G92 X1 Y2 Z0.5
;LAYER:0
G1 X2 Y2 Z0.5
G91
G1 X1 Y1
G21
G90
G1 X10 Y10 Z1
"""
        index = build_index_from_bytes(data)
        self.assertEqual(index.motion_count(0), 3)
        # G92 seeds the offset (already in inches), G20 scales the axes,
        # G91 accumulates and G21 returns to millimetres before the final
        # absolute move.
        self.assertAlmostEqual(index.motion_x[0][0], 2 * 25.4, places=3)
        self.assertAlmostEqual(index.motion_x[0][1], 3 * 25.4, places=3)
        self.assertAlmostEqual(index.motion_y[0][1], 3 * 25.4, places=3)
        self.assertAlmostEqual(index.motion_x[0][2], 10.0, places=3)

    def test_baked_pauses_map_to_the_layer_that_owns_them(self):
        data = b"""PAUSE
;LAYER:0
G1 X1
;TIME_ELAPSED:10
M0
M25
;LAYER:1
G1 X2
;TIME_ELAPSED:20
PAUSE
;LAYER:2
G1 X3
"""
        # The leading PAUSE precedes the first marker and belongs to no
        # layer; layer 0's two commands are one layer; layer 1's is its own.
        self.assertEqual(build_index_from_bytes(data).pauses, (0, 1))

    def test_the_layer_map_falls_back_through_its_sources(self):
        # Block stats win, but a repeated value makes the map unusable — a
        # shifted map is worse than none, so it is dropped.
        duplicated = build_index_from_bytes(
            b";LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X1\n"
            b";LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\nG1 X2\n")
        self.assertEqual(duplicated.current_layer_map, {})
        # A block with no stats line of its own falls back to the global
        # stats stream, which is positionally aligned here.
        global_stats = build_index_from_bytes(
            b"SET_PRINT_STATS_INFO CURRENT_LAYER=3\n;LAYER_CHANGE\nG1 X1\n"
            b";LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=4\nG1 X2\n")
        self.assertEqual(global_stats.current_layer_map, {3: 0, 4: 1})
        # A repeated value in that stream is just as unusable.
        repeated = build_index_from_bytes(
            b"SET_PRINT_STATS_INFO CURRENT_LAYER=3\n;LAYER_CHANGE\nG1 X1\n"
            b";LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=4\nG1 X2\n"
            b";LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=3\nG1 X3\n")
        self.assertEqual(repeated.current_layer_map, {})
        # Cura/Orca numeric markers supply the map when no stats exist.
        markers = build_index_from_bytes(b";LAYER:1\nG1 X1\n;LAYER:2\nG1 X2\n")
        self.assertEqual(markers.current_layer_map, {1: 0, 2: 1})
        # Repeated markers are as unusable as repeated stats values: the
        # numbering restarted, so no layer can be trusted to a marker.
        restarted = build_index_from_bytes(b";LAYER:1\nG1 X1\n;LAYER:1\nG1 X2\n")
        self.assertEqual(restarted.current_layer_map, {})
        self.assertEqual(restarted.layer_count(), 2)

    def test_time_elapsed_closes_a_layer_at_its_own_marker(self):
        index = build_index_from_bytes(b";LAYER:0\nG1 X1\n;TIME_ELAPSED:12.5\nG1 X9\n")
        self.assertEqual(index.layer_elapsed_times, [12.5])
        # Travel between the elapsed marker and the next layer marker does
        # not reopen the closed block.
        self.assertEqual(index.motion_count(0), 1)

    def test_compact_builds_defer_hydration_to_the_reader(self):
        path = self._write(b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n")
        compact = build_index_from_file(path, compact=True)
        self.assertTrue(compact.compact)
        self.assertEqual(compact.hydrated_layers, set())
        # The count is the walk's metadata, born correct: only the
        # GEOMETRY defers to the reader.
        self.assertEqual(compact.motion_count(0), 1)
        self.assertFalse(build_index_from_file(path, compact=False).compact)

    def test_compact_builds_carry_the_true_per_layer_motion_counts(self):
        # The resumed-session report: a compact scan collects no
        # motion arrays, so the counts must come from the walk's
        # every-motion counter — otherwise a prepared-store resume
        # serves every layer without hydration and the scrub slider
        # stays dead (zero counts) forever.
        path = self._write(
            b";LAYER:0\nG1 X1\nG1 X2\n;LAYER:1\nG1 X3\n;LAYER:2\n"
            b"G1 X4\nG1 X5\nG1 X6\n")
        compact = build_index_from_file(path, compact=True)
        full = build_index_from_file(path, compact=False)
        self.assertEqual([compact.motion_count(i) for i in range(3)],
                         [2, 1, 3],
                         "the compact build's counts are not the "
                         "walk's true totals")
        self.assertEqual([full.motion_count(i) for i in range(3)],
                         [2, 1, 3],
                         "the non-compact build disagrees")
        # The counts stand alone: the geometry still waits for the
        # reader.
        self.assertEqual(compact.hydrated_layers, set())
        self.assertEqual(len(compact.motion_offsets[0]), 0)


class HydrationTests(unittest.TestCase):
    """Compact indexes load motion data per layer, inside a retention
    window anchored to the live print."""

    def _compact(self, layers=5):
        lines = []
        for layer in range(layers):
            lines.append(b";LAYER:%d\n" % layer)
            lines.append(b"G1 X%d Y1 Z0.2\n" % (layer * 10 + 5))
            lines.append(b";TIME_ELAPSED:%d\n" % (layer * 10))
        path = _write_temp(b"".join(lines), prefix="hydrate-")
        self.addCleanup(os.remove, path)
        return build_index_from_file(path, compact=True), path

    def test_non_compact_and_already_hydrated_layers_are_no_ops(self):
        full = build_index_from_bytes(b";LAYER:0\nG1 X1\n")
        self.assertTrue(hydrate_layer_from_file(full, "irrelevant", 0))
        index, path = self._compact(2)
        self.assertTrue(hydrate_layer_from_file(index, path, 0))
        self.assertIn(0, index.hydrated_layers)
        self.assertTrue(hydrate_layer_from_file(index, path, 0))

    def test_out_of_range_and_unreadable_sources_report_failure(self):
        index, path = self._compact(2)
        self.assertFalse(hydrate_layer_from_file(index, path, 9))
        self.assertFalse(hydrate_layer_from_file(index, path, -1))
        self.assertFalse(hydrate_layer_from_file(
            index, os.path.join(os.path.dirname(path), "gone"), 0))
        self.assertEqual(index.hydrated_layers, set())

    def test_hydration_reads_the_layer_geometry(self):
        index, path = self._compact(3)
        self.assertTrue(hydrate_layer_from_file(index, path, 1))
        self.assertEqual(index.motion_count(1), 1)
        self.assertAlmostEqual(index.motion_x[1][0], 15.0, places=3)
        self.assertEqual(len(index.motion_offsets), 3)

    def test_hydration_grows_columns_that_are_missing(self):
        # An index whose motion columns were lost or never written (an
        # older cache, a hand-built index) must still hydrate rather than
        # raising IndexError on the layer's own column.
        index, path = self._compact(3)
        index.motion_offsets, index.motion_x, index.motion_y, index.motion_z = [], [], [], []
        self.assertTrue(hydrate_layer_from_file(index, path, 1))
        self.assertEqual([len(column) for column in (index.motion_offsets, index.motion_x,
                                                     index.motion_y, index.motion_z)],
                         [3, 3, 3, 3])
        self.assertEqual(index.motion_count(1), 1)

    def test_hydration_needs_no_stored_layer_starts(self):
        # A cache written before per-layer start positions existed must
        # still hydrate: the parse starts from the origin.
        index, path = self._compact(2)
        index.layer_start_positions = []
        index.layer_start_absolute = []
        index.layer_start_units = []
        self.assertTrue(hydrate_layer_from_file(index, path, 0))
        self.assertAlmostEqual(index.motion_x[0][0], 5.0, places=3)

    def test_hydration_tracks_inches_and_relative_motion(self):
        # The unit and distance-mode commands sit inside the layer block,
        # where hydration has to replay them itself.
        path = _write_temp(b";LAYER:0\nG20\nG90\nG1 X1 Y1 Z0.5\nG91\nG1 X1 Y1\n"
                           b"G92 X0 Y0\nG21\nG90\nG1 X2 Y2 Z1\n", prefix="hydrate-units-")
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        self.assertTrue(hydrate_layer_from_file(index, path, 0))
        self.assertEqual(index.motion_count(0), 3)
        self.assertAlmostEqual(index.motion_x[0][0], 25.4, places=3)
        self.assertAlmostEqual(index.motion_z[0][0], 12.7, places=3)
        # Relative move on top of the inch-scaled position.
        self.assertAlmostEqual(index.motion_x[0][1], 50.8, places=3)
        # G92 zeroes the modal position without emitting a move.
        self.assertAlmostEqual(index.motion_y[0][2], 2.0, places=3)
        self.assertAlmostEqual(index.motion_x[0][2], 2.0, places=3)
        # Hydration must agree with the full scan it stands in for.
        full = build_index_from_file(path)
        self.assertEqual([tuple(round(v, 3) for v in axis) for axis in full.motion_x],
                         [tuple(round(v, 3) for v in axis) for axis in index.motion_x])

    def test_hydration_stops_when_the_source_shrank(self):
        # The file was truncated or replaced between indexing and
        # hydration, so the layer range now points past the new end.
        path = _write_temp(b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n", prefix="hydrate-shrunk-")
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        with open(path, "wb") as handle:
            handle.write(b";LAYER:0\n")
        self.assertTrue(hydrate_layer_from_file(index, path, 1))
        self.assertEqual(index.motion_count(1), 0)

    def test_the_retention_window_follows_the_anchor(self):
        index, path = self._compact(6)
        for layer in (0, 1, 2):
            hydrate_layer_from_file(index, path, layer)
        # The newest hydration sets the anchor, so the window keeps [1, 3]
        # and drops layer 0 rather than growing without bound.
        self.assertEqual(index.hydrated_layers, {1, 2})
        index.followed_layer = 4
        hydrate_layer_from_file(index, path, 3)
        # The live print's layer wins over the worker's pick: the window
        # is [3, 5] around layer 4, not around the hydrated layer 3.
        # The freshly hydrated layer keeps its own ±1 window too (the
        # background pass's prepare window), so the walk's survivor 2
        # stays until the next hydrate evicts it.
        self.assertEqual(index.hydrated_layers, {2, 3})
        hydrate_layer_from_file(index, path, 5)
        self.assertEqual(index.hydrated_layers, {3, 5})
        hydrate_layer_from_file(index, path, 0, keep_anchor=0)
        self.assertEqual(index.hydrated_layers, {0})

    def test_hydration_ignores_pathological_lines(self):
        giant = b"G1 X1" + b" " * 70000 + b"\n"
        path = _write_temp(b";LAYER:0\n" + giant + b"G1 X5\n", prefix="hydrate-giant-")
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        self.assertTrue(hydrate_layer_from_file(index, path, 0))
        self.assertEqual(index.motion_count(0), 1)


class CacheTests(unittest.TestCase):
    """The persistent index cache: a content-addressed, self-validating
    blob that has to reject anything it cannot vouch for."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(prefix="mpfi-cache-")
        self.addCleanup(self._directory.cleanup)
        self.directory = self._directory.name

    def _identity(self, name="part.gcode", size=100, modified=1.0, uuid="u1"):
        return RemoteFileIdentity(name, size, modified, uuid)

    def _path(self, remote):
        # The production cache's own path (the per-print subdirectory
        # layout) — the raw writes must land where the loader reads.
        return PersistentIndexCache(self.directory)._path(remote)

    def _write_raw(self, remote, payload, magic=_CACHE_MAGIC, header=None):
        """Place a cache file at the identity's own path, bypassing save()."""
        raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
        with gzip.open(self._path(remote), "wb") as handle:
            handle.write(magic)
            handle.write(struct.pack("<I", len(raw)))
            handle.write(raw)
            handle.write(payload)
        return self._path(remote)

    def _header(self, remote, **overrides):
        # The remote is positional: "identity" is also a header field, and
        # an override of it must not collide with the parameter name.
        header = {
            "version": _CACHE_VERSION,
            "identity": remote.stable_key(),
            "identity_fields": [remote.filename, remote.size, remote.modified, remote.uuid],
            "byteorder": sys.byteorder,
            "ranges": [[0, 40]],
            "starts": [[0.0, 0.0, 0.2]],
            "start_absolute": [True],
            "start_units": [1.0],
            "layer_map": {"1": 0},
            "elapsed_times": [10.0],
            "pauses": [0],
            "compact": False,
            "hydrated": [0],
            "counts": [1],
        }
        header.update(overrides)
        return header

    def _body(self, count=1):
        # One motion: offset, x, y, z.
        return (array("Q", [8]) * count).tobytes() + (array("f", [1.0]) * count).tobytes() \
            + (array("f", [2.0]) * count).tobytes() + (array("f", [0.2]) * count).tobytes()

    def test_round_trip_preserves_every_restorable_field(self):
        index = build_index_from_bytes(b";LAYER:0\nG1 X5 Y2 Z0.2\n;TIME_ELAPSED:7\n")
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        cache.save(identity, index)
        restored = cache.load(identity)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.ranges, index.ranges)
        self.assertEqual(restored.layer_start_positions, index.layer_start_positions)
        self.assertEqual(restored.layer_elapsed_times, index.layer_elapsed_times)
        self.assertEqual(restored.current_layer_map, index.current_layer_map)
        self.assertEqual(restored.pauses, index.pauses)
        self.assertEqual(list(restored.motion_offsets[0]), list(index.motion_offsets[0]))
        self.assertAlmostEqual(restored.motion_x[0][0], 5.0, places=3)

    def test_load_refuses_what_it_cannot_identify(self):
        index = build_index_from_bytes(b";LAYER:0\nG1 X1\n")
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        self.assertIsNone(cache.load(None))
        cache.save(identity, index)
        self.assertIsNone(cache.load(self._identity(size=200)))
        self.assertIsNone(cache.load(self._identity(modified=2.0)))
        # The uuid is Moonraker's per-extraction token (the review's
        # UUID-policy finding): any uuid with the same filename, size
        # and modified is the same content.
        self.assertIsNotNone(cache.load(self._identity(uuid="u2")))
        # A different filename is a different cache slot entirely.
        self.assertIsNone(cache.load(self._identity(name="other.gcode")))

    def test_load_refuses_damaged_headers(self):
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        self._write_raw(identity, b"", magic=b"NOTMAGIC")
        self.assertIsNone(cache.load(identity))
        self._write_raw(identity, self._body(), header=self._header(identity, version=_CACHE_VERSION + 1))
        self.assertIsNone(cache.load(identity))
        self._write_raw(identity, self._body(),
                        header=self._header(identity, identity="file:elsewhere|size:1|modified:1.000000"))
        self.assertIsNone(cache.load(identity))
        other_endian = "big" if sys.byteorder == "little" else "little"
        self._write_raw(identity, self._body(), header=self._header(identity, byteorder=other_endian))
        self.assertIsNone(cache.load(identity))
        # Ragged column lengths, and counts outside the sane bound.
        self._write_raw(identity, self._body(), header=self._header(identity, counts=[1, 1]))
        self.assertIsNone(cache.load(identity))
        self._write_raw(identity, self._body(), header=self._header(identity, counts=[-1]))
        self.assertIsNone(cache.load(identity))
        self._write_raw(identity, self._body(), header=self._header(identity, counts=[100_000_001]))
        self.assertIsNone(cache.load(identity))
        # The identity key matches but the header's own size/modified
        # disagree with the file: the blob is stale for this remote.
        self._write_raw(identity, self._body(),
                        header=self._header(identity,
                                            identity_fields=["part.gcode", 999, 1.0, "u1"]))
        self.assertIsNone(cache.load(identity))
        self._write_raw(identity, self._body(),
                        header=self._header(identity,
                                            identity_fields=["part.gcode", 100, 5.0, "u1"]))
        self.assertIsNone(cache.load(identity))

    def test_load_refuses_an_out_of_range_header_length(self):
        identity = self._identity()
        path = self._write_raw(identity, self._body(), header=self._header(identity))
        cache = PersistentIndexCache(self.directory)
        for length in (0, 17 * 1024 * 1024):
            with gzip.open(path, "wb") as handle:
                handle.write(_CACHE_MAGIC)
                handle.write(struct.pack("<I", length))
            self.assertIsNone(cache.load(identity))

    def test_load_refuses_unparseable_and_truncated_streams(self):
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        path = self._write_raw(identity, self._body(), header=self._header(identity))
        with gzip.open(path, "wb") as handle:
            handle.write(_CACHE_MAGIC)
            handle.write(struct.pack("<I", 8))
            handle.write(b"{ not js")
        self.assertIsNone(cache.load(identity))
        # A body shorter than the counts promise.
        self._write_raw(identity, b"\x00" * 4, header=self._header(identity))
        self.assertIsNone(cache.load(identity))
        # A slot that is not a gzip container at all.
        with open(self._path(identity), "wb") as handle:
            handle.write(b"not gzip at all")
        self.assertIsNone(cache.load(identity))

    def test_identity_fields_are_advisory_when_absent(self):
        # A header without the field list (or with one that is not a list)
        # still loads on its own key; a mismatched uuid loads too (the
        # review's UUID-policy finding — only size/modified vouch for
        # the content), and an unknown size vouches for nothing.
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        self._write_raw(identity, self._body(), header=self._header(identity, identity_fields=None))
        self.assertIsNotNone(cache.load(identity))
        self._write_raw(identity, self._body(), header=self._header(identity, identity_fields="opaque"))
        self.assertIsNotNone(cache.load(identity))
        self._write_raw(identity, self._body(),
                        header=self._header(identity, identity_fields=["part.gcode", 100, 1.0, "stale"]))
        self.assertIsNotNone(cache.load(identity))
        self._write_raw(identity, self._body(),
                        header=self._header(identity, identity_fields=["part.gcode", 0, 1.0, "u1"]))
        self.assertIsNotNone(cache.load(identity))

    def test_load_reads_the_hydration_and_pause_columns(self):
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        # No hydrated column: the loaded arrays are the evidence instead.
        self._write_raw(identity, self._body(), header=self._header(identity, hydrated=None, pauses=[]))
        restored = cache.load(identity)
        self.assertEqual(restored.hydrated_layers, {0})
        # Unparseable, out-of-range and out-of-order pause entries are
        # dropped rather than trusted.
        self._write_raw(identity, self._body(count=2), header=self._header(
            identity, ranges=[[0, 40], [40, 80]], starts=[[0.0, 0.0, 0.2], [0.0, 0.0, 0.4]],
            start_absolute=[True, True], start_units=[1.0, 1.0], elapsed_times=[10.0, 20.0],
            counts=[1, 1], hydrated=[], pauses=["0", None, "junk", "1", "0", "5"]))
        restored = cache.load(identity)
        self.assertEqual(restored.pauses, (0, 1))
        self.assertEqual(restored.hydrated_layers, set())
        self.assertEqual(restored.current_layer_map, {1: 0})

    def test_load_ignores_utime_failure(self):
        # A read-only cache mount makes the touch fail; the data is still
        # good, so the load must not be discarded.
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        cache.save(identity, build_index_from_bytes(b";LAYER:0\nG1 X1\n"))
        with patch("os.utime", side_effect=OSError("read-only")):
            self.assertIsNotNone(cache.load(identity))

    def test_save_refuses_indexes_it_cannot_serialize(self):
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        cache.save(None, build_index_from_bytes(b";LAYER:0\nG1 X1\n"))
        cache.save(identity, LayerMotionIndex())
        # Ragged per-layer arrays would restore as a different index.
        cache.save(identity, LayerMotionIndex(ranges=[(0, 10), (10, 20)],
                                              motion_offsets=[array("Q")]))
        cache.save(identity, LayerMotionIndex(
            ranges=[(0, 10)], motion_offsets=[array("Q", [4])], motion_x=[array("f")],
            motion_y=[array("f", [0])], motion_z=[array("f", [0])],
            layer_start_positions=[(0.0, 0.0, 0.0)], layer_start_absolute=[True],
            layer_start_units=[1.0], layer_elapsed_times=[None]))
        self.assertEqual(os.listdir(self.directory), [])

    def test_a_failed_write_leaves_no_temporary_behind(self):
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        with patch("os.replace", side_effect=OSError("disk full")):
            cache.save(identity, build_index_from_bytes(b";LAYER:0\nG1 X1\n"))
        leftovers = [name for _root, _dirs, names in os.walk(self.directory)
                     for name in names]
        self.assertEqual(leftovers, [], "the failed write left a file behind")
        self.assertIsNone(cache.load(identity))
        # Even a cleanup that fails must not propagate: the cache is
        # best-effort, and a partial blob is never published either way.
        index = build_index_from_bytes(b";LAYER:0\nG1 X1\n")
        with patch("os.replace", side_effect=OSError("disk full")), \
                patch("os.remove", side_effect=OSError("locked")):
            cache.save(identity, index)
        leftovers = [name for _root, _dirs, names in os.walk(self.directory)
                     for name in names]
        self.assertEqual(len(leftovers), 1)
        self.assertIn(".mpfi.gz.tmp-", leftovers[0])
        self.assertIsNone(cache.load(identity))

    def test_prune_keeps_unrelated_files_and_survives_a_vanished_entry(self):
        cache = PersistentIndexCache(self.directory)
        notes = os.path.join(self.directory, "notes.txt")
        with open(notes, "w") as handle:
            handle.write("keep me")
        dead = os.path.join(self.directory, "dead.mpfi.gz")
        os.symlink(os.path.join(self.directory, "nowhere"), dead)
        cache.prune()
        self.assertTrue(os.path.exists(notes))
        self.assertTrue(os.path.lexists(dead))
        with patch("os.listdir", side_effect=OSError("gone")):
            cache.prune()

    def test_prune_enforces_the_entry_and_byte_budgets(self):
        index = build_index_from_bytes(b";LAYER:0\nG1 X1\n")
        cache = PersistentIndexCache(self.directory, max_entries=1)
        for value in range(3):
            cache.save(self._identity(name=f"{value}.gcode"), index)
        blobs = [name for _root, _dirs, names in os.walk(self.directory)
                 for name in names if name.endswith(".mpfi.gz")]
        self.assertLessEqual(len(blobs), 1)

        with tempfile.TemporaryDirectory(prefix="mpfi-bytes-") as directory:
            cache = PersistentIndexCache(directory, max_bytes=1024 * 1024)
            # The print-level layout: each filler print folder carries
            # its index (and, in production, its prepared sibling —
            # the policy's total covers both).
            for value in range(3):
                folder = os.path.join(directory, f"p-filler-{value}")
                os.makedirs(folder, exist_ok=True)
                blob = os.path.join(folder, "index.mpfi.gz")
                with open(blob, "wb") as handle:
                    handle.write(b"\x00" * 700000)
                os.utime(blob, (1000.0, 1000.0 + value))
            cache.prune()
            remaining = sorted(folder for folder in os.listdir(directory)
                               if folder.startswith("p-"))
            self.assertEqual(remaining, ["p-filler-2"])  # newest survives
            # Another Cura instance pruning the same directory at the same
            # moment can take the folder first; the sweep still completes.
            late_dir = os.path.join(directory, "p-late")
            os.makedirs(late_dir, exist_ok=True)
            late = os.path.join(late_dir, "index.mpfi.gz")
            with open(late, "wb") as handle:
                handle.write(b"\x00" * 700000)
            with patch("shutil.rmtree", side_effect=OSError("vanished")):
                cache.prune()
            self.assertTrue(os.path.lexists(late))
            self.assertTrue(os.path.lexists(os.path.join(
                directory, "p-filler-2", "index.mpfi.gz")))

    def test_the_constructor_clamps_its_budgets(self):
        cache = PersistentIndexCache(self.directory, max_bytes=1, max_entries=0)
        self.assertEqual(cache.max_entries, 1)
        self.assertGreaterEqual(cache.max_bytes, 1024 * 1024)

    def test_read_exact_tolerates_short_reads(self):
        class Dribble:
            def __init__(self, data, chunk):
                self._data = data
                self._chunk = chunk
                self._position = 0

            def read(self, size):
                take = min(size, self._chunk, len(self._data) - self._position)
                chunk = self._data[self._position:self._position + take]
                self._position += take
                return chunk

        # A gzip stream may hand back one byte at a time.
        self.assertEqual(_read_exact(Dribble(b"abcdefgh", 1), 8), b"abcdefgh")
        self.assertEqual(_read_exact(Dribble(b"abcdefgh", 3), 5), b"abcde")
        with self.assertRaises(EOFError):
            _read_exact(Dribble(b"abc", 2), 8)
        with self.assertRaises(EOFError):
            _read_exact(Dribble(b"abc", 3), -1)

    def test_cache_files_are_content_addressed(self):
        identity = self._identity()
        cache = PersistentIndexCache(self.directory)
        cache.save(identity, build_index_from_bytes(b";LAYER:0\nG1 X1\n"))
        digest = hashlib.sha256(identity.stable_key().encode("utf-8")).hexdigest()
        # The per-print folder (the unified lifecycle): one folder per
        # print, keyed by the content digest, the index inside named
        # by its role.
        self.assertEqual(os.listdir(self.directory), [f"p-{digest[:24]}"])
        self.assertEqual(os.listdir(os.path.join(self.directory, f"p-{digest[:24]}")),
                         ["index.mpfi.gz"])


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class CuraIntegrationTests(unittest.TestCase):
    """CuraIntegration against the harness's Cura host doubles: the scene,
    view, lease, slicing and watchdog contracts it owes the rest of the
    plugin."""

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.logger = sys.modules["UM.Logger"].Logger
        # Hosts stay referenced until every cleanup has run: close()
        # disconnects signals by handle, and a host collected first would
        # leave those handles dangling.
        self._hosts = []
        import plugins.CuraIntegration as module
        self.module = module

        class SliceBackend(QObject):
            slicingStarted = pyqtSignal()
            slicingCancelled = pyqtSignal()
            backendStateChange = pyqtSignal(int)

        class SimulationView(QObject):
            currentLayerNumChanged = pyqtSignal()
            currentPathNumChanged = pyqtSignal()
            activityChanged = pyqtSignal()
            maxLayersChanged = pyqtSignal()

            def __init__(self, layers=3):
                super().__init__()
                self._layer = 0
                self._max = max(0, layers - 1)
                self.layer_sets = []
                self.recalculated = 0
                self.layer_data = None
                self.broken = False
                self.bad_height = False
                self.bad_data = False
                self.activity = False

            def getCurrentLayer(self):
                if self.broken:
                    raise AttributeError("no layer slider")
                return self._layer

            def setLayer(self, value):
                if self.broken:
                    raise AttributeError("no layer slider")
                self.layer_sets.append(int(value))
                self._layer = int(value)

            def getMaxLayers(self):
                if self.broken:
                    raise AttributeError("no layer slider")
                return self._max

            def getLayerData(self):
                if self.bad_data:
                    raise AttributeError("no mesh data")
                return self.layer_data

            def _calculateLayerHeightsCache(self):
                self.recalculated += 1

            def _getLayerHeight(self, layer):
                if self.bad_height:
                    raise AttributeError("no layer height")
                return 0.2

            def getActivity(self):
                return self.activity

            def setActivity(self, value):
                self.activity = bool(value)

        def host(backend=None, activity=True):
            class Host(self.qt.Application):
                def __init__(self):
                    super().__init__()
                    self.backend = backend
                    self.loaded = []
                    self.read_error = None
                    self.platform_calls = 0

                def getBackend(self):
                    return self.backend

                def readLocalFile(self, url, add_to_recent_files=False):
                    if self.read_error is not None:
                        raise self.read_error
                    # normpath: QUrl spells a local file with '/' even on
                    # Windows, and the loaded paths are compared against the
                    # native ones the lease was built from.
                    self.loaded.append(os.path.normpath(url.toLocalFile()))

            if activity:
                def updatePlatformActivity(self):
                    self.platform_calls += 1

                Host.updatePlatformActivity = updatePlatformActivity
            return Host()

        self.Host = host
        self.SimulationView = SimulationView
        self.backend = SliceBackend()

    def build(self, backend=None, activity=True, view=None):
        app = self.Host(backend=self.backend if backend is None else backend, activity=activity)
        self._hosts.append(app)
        if view is not None:
            app.controller.view = view
        integration = self.module.CuraIntegration(app, None)
        self.addCleanup(integration.close)
        self.qt.events()
        return app, integration

    def _pump(self, seconds):
        """Run the event loop for a bounded stretch (timer-driven paths)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.qt.events(5)

    def _accept(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.qt.events(5)
            if predicate():
                return True
        return predicate()

    def _lease(self, name="part.gcode"):
        directory = tempfile.mkdtemp(prefix="cura-load-")
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(b";LAYER:0\nG1 X1\n")

        def release(leased_path):
            released.append(leased_path)
            shutil.rmtree(os.path.dirname(leased_path), ignore_errors=True)

        released = []
        return self.qt.load("RemoteFileService").FileLease(path, release), released

    # -- lifecycle wiring -------------------------------------------------

    def test_construction_binds_every_host_signal_it_owns(self):
        app, integration = self.build()
        self.assertEqual(integration.generation, 0)
        self.assertFalse(integration.loading)
        self.assertFalse(integration.suspended)
        self.assertIsNone(integration.view)
        self.assertEqual(len(integration._connections), 7)

    def test_an_unbindable_host_signal_is_logged_not_fatal(self):
        # A Cura host that renamed a signal must degrade to a lost refresh,
        # never to a failed plugin startup.
        class Hostile:
            def connect(self, callback):
                raise RuntimeError("no such signal")

        app, _ = self.build()
        app.mainWindowChanged = Hostile()
        broken = self.module.CuraIntegration(app, None)
        self.addCleanup(broken.close)
        self.assertEqual(self.logger.log.call_args_list[-1].args[0], "w")

    def test_slicing_suspends_and_backend_done_releases_it(self):
        _, integration = self.build()
        invalidated = []
        integration.invalidated.connect(invalidated.append)
        integration._slicing_started()
        self.assertTrue(integration.suspended)
        self.assertEqual(invalidated, ["Cura slicing started"])
        # The guard is on the load, not on idempotence: Cura reports one
        # start per slice, and each one is a new generation.
        integration._slicing_started()
        self.assertEqual(len(invalidated), 2)
        integration._backend_changed(1)  # BackendState.Done
        self.assertFalse(integration._slicing)
        self.assertTrue(integration.suspended)  # the settle window still holds
        integration._settle_until = 0.0
        self.assertFalse(integration.suspended)
        # A state that is not Done leaves the slice running.
        integration._slicing_started()
        integration._backend_changed(0)
        self.assertTrue(integration._slicing)
        integration._slicing_finished()
        self.assertFalse(integration._slicing)

    def test_slicing_does_not_interrupt_a_pending_load(self):
        app, integration = self.build()
        lease, _released = self._lease()
        integration._view = object()
        self.assertTrue(integration.load(lease))
        integration._slicing_started()
        self.assertTrue(integration.loading)
        self.assertFalse(integration._slicing)

    def test_scene_changes_invalidate_unless_the_plugin_caused_them(self):
        app, integration = self.build()
        invalidated = []
        integration.invalidated.connect(invalidated.append)
        app.controller.getScene().getRoot().childrenChanged.emit()
        self.assertEqual(invalidated, ["Cura scene structure changed"])
        self.assertTrue(integration.suspended)  # the settle window
        # The plugin's own decorating writes are not user edits.
        with integration.decorating_scene() as root:
            root.childrenChanged.emit()
        self.assertEqual(len(invalidated), 1)
        # Nor is a load's own scene churn.
        lease, _released = self._lease()
        integration._view = object()
        integration.load(lease)
        app.controller.getScene().getRoot().childrenChanged.emit()
        self.assertEqual(len(invalidated), 1)
        # And a closed adapter ignores the scene entirely.
        integration.close()
        app.controller.getScene().getRoot().childrenChanged.emit()
        self.assertEqual(len(invalidated), 1)

    def test_refresh_rebinds_the_scene_root_and_the_view(self):
        app, integration = self.build()
        root = app.controller.getScene().getRoot()
        app.controller.getScene().root = type(root)()
        swapped = []
        integration.viewSwapped.connect(lambda: swapped.append(1))
        integration._refresh()
        self.assertIs(integration._root, app.controller.getScene().getRoot())
        self.assertEqual(swapped, [])  # the view itself did not change
        # A view swap re-binds, emits and opens the settle window that
        # absorbs Cura's late layer/path echoes.
        view = self.SimulationView()
        app.controller.view = view
        integration._refresh()
        self.assertEqual(swapped, [1])
        self.assertIs(integration.view, view)
        self.assertTrue(integration.suspended)
        positions = []
        integration.positionChanged.connect(lambda: positions.append(1))
        view.currentLayerNumChanged.emit()
        view.currentPathNumChanged.emit()
        self.assertEqual(len(positions), 2)
        changed = []
        integration.changed.connect(lambda: changed.append(1))
        view.maxLayersChanged.emit()
        view.activityChanged.emit()
        self.assertEqual(len(changed), 2)
        self.assertIsNone(integration._heights)
        # Swapping the view away disconnects the old one's signals.
        app.controller.view = None
        integration._refresh()
        view.currentLayerNumChanged.emit()
        self.assertEqual(len(positions), 2)

    def test_a_second_load_supersedes_the_pending_one(self):
        # Once a load is pending but unconfirmed, an explicit Load must not
        # be refused for the whole watchdog window: the old lease is parked
        # for the next load to release.
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        first, first_released = self._lease("first.gcode")
        second, second_released = self._lease("second.gcode")
        self.assertTrue(integration.load(first))
        self.assertTrue(integration.load(second))
        self.assertEqual(app.loaded, [first.path, second.path])
        self.assertEqual(first_released, [first.path])
        self.assertTrue(integration.loading)
        app.fileCompleted.emit(second.path)
        self.assertEqual(second_released, [second.path])
        self.assertFalse(integration.loading)

    def test_an_unrelated_completion_leaves_the_pending_lease_alone(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        lease, released = self._lease()
        invalidated = []
        integration.invalidated.connect(invalidated.append)
        self.assertTrue(integration.load(lease))
        # Another file finishing is not our parse: release nothing, and
        # keep waiting for our own completion.
        app.fileCompleted.emit("/tmp/somebody-elses.gcode")
        self.assertEqual(released, [])
        self.assertTrue(integration.loading)
        self.assertEqual(invalidated, ["Cura file replaced"])

    def test_a_view_that_cannot_build_its_height_cache_still_yields_one(self):
        app, integration = self.build()
        view = self.SimulationView(layers=3)

        def broken_cache():
            raise AttributeError("no cache builder")

        view._calculateLayerHeightsCache = broken_cache
        app.controller.view = view
        integration._refresh()
        # The per-layer fallback is slower but still answers.
        self.assertEqual(integration.heights, (0.2, 0.2, 0.2))

    def test_close_and_swap_survive_signals_that_refuse_to_disconnect(self):
        # A host whose signal objects cannot be disconnected must still
        # shut down cleanly rather than throwing out of a Qt teardown.
        class Stubborn:
            def connect(self, callback): pass
            def disconnect(self, callback): raise RuntimeError("not mine")

        def view_with(signal):
            return SimpleNamespace(currentLayerNumChanged=signal,
                                   currentPathNumChanged=signal,
                                   activityChanged=signal,
                                   maxLayersChanged=signal)

        app, integration = self.build()
        app.controller.view = view_with(Stubborn())
        integration._refresh()
        self.assertIsNotNone(integration.view)
        # The next swap has to drop the first view's connections.
        app.controller.view = view_with(Stubborn())
        integration._refresh()
        integration._root = SimpleNamespace(childrenChanged=Stubborn())
        integration.close()
        self.assertTrue(integration._closed)
        self.assertEqual(integration.generation, 1)

    def test_refresh_survives_a_host_without_a_scene_or_view(self):
        class Bare:
            def getScene(self):
                raise AttributeError("no scene")

            def getView(self, name):
                raise AttributeError("no view")

        app, integration = self.build()
        integration.controller = Bare()
        integration._refresh()
        self.assertIsNone(integration.view)

    def test_the_scene_change_guard_reads_the_plugin_state(self):
        app, integration = self.build()
        integration._own_scene_changes = 1
        integration._scene_changed()
        self.assertEqual(integration._settle_until, 0.0)
        integration._own_scene_changes = 0
        integration._slicing = True
        integration._scene_changed()
        self.assertEqual(integration._settle_until, 0.0)
        integration._slicing = False
        integration._scene_changed()
        self.assertGreater(integration._settle_until, 0.0)

    def test_deferred_refresh_is_dropped_once_the_generation_moves(self):
        app, integration = self.build()
        ran = []
        integration.queue(lambda: ran.append(1))
        integration.invalidate("swap")
        self._pump(0.1)
        self.assertEqual(ran, [])
        integration.queue(lambda: ran.append(1))
        self.assertTrue(self._accept(lambda: bool(ran)))
        self.assertEqual(len(ran), 1)

    # -- Cura activity and preview ----------------------------------------

    def test_platform_activity_nudge_is_optional_and_guarded(self):
        app, integration = self.build()
        integration.nudge_cura_activity()
        self.assertEqual(app.platform_calls, 1)
        quiet, quiet_integration = self.build(activity=False)
        self.assertFalse(hasattr(type(quiet), "updatePlatformActivity"))
        quiet_integration.nudge_cura_activity()

        def explode():
            raise RuntimeError("host blew up")

        app.updatePlatformActivity = explode
        integration.nudge_cura_activity()  # swallowed: the nudge is best-effort

    def test_layer_nudge_reannounces_the_current_layer(self):
        app, integration = self.build()
        integration.nudge_layer_view()  # no view yet
        view = self.SimulationView()
        view._layer = 4
        app.controller.view = view
        integration._refresh()
        integration.nudge_layer_view()
        # Stepping back then forward wakes Cura's own chrome; the writes are
        # guarded so the follower's own bump is not read as a user drag.
        self.assertEqual(view.layer_sets, [3, 4])
        self.assertEqual(integration._writing, 0)
        view.broken = True
        integration.nudge_layer_view()  # a broken view is not fatal
        self.assertEqual(integration._writing, 0)

    def test_preview_and_toolpath_gates_read_the_live_view(self):
        app, integration = self.build()
        self.assertFalse(integration.preview_active)
        self.assertFalse(integration.has_toolpath)
        app.controller.stage = SimpleNamespace(getId=lambda: "PreviewStage")
        self.assertTrue(integration.preview_active)
        app.controller.stage = SimpleNamespace(getId=lambda: "PrepareStage")
        self.assertFalse(integration.preview_active)
        view = self.SimulationView(layers=0)
        app.controller.view = view
        integration._refresh()
        self.assertFalse(integration.has_toolpath)  # no layers yet
        view.layer_data = [object()]
        self.assertTrue(integration.has_toolpath)
        view.layer_data = None
        view._max = 12
        self.assertTrue(integration.has_toolpath)
        # A layer-data accessor that throws says nothing about the layer
        # count, so the gate falls through to it rather than reporting no
        # toolpath for a sliced file.
        view.bad_data = True
        self.assertTrue(integration.has_toolpath)
        view.bad_data = False
        view.broken = True
        self.assertFalse(integration.has_toolpath)
        # A view with no layer-data accessor and a broken layer count.
        integration._view = SimpleNamespace()
        self.assertFalse(integration.has_toolpath)

    def test_preview_gate_degrades_when_the_stage_query_fails(self):
        class Bare:
            def getActiveStage(self):
                raise AttributeError("no stage")

        app, integration = self.build()
        integration.controller = Bare()
        self.assertFalse(integration.preview_active)

    def test_switch_to_preview_reports_failure(self):
        app, integration = self.build()
        self.assertTrue(integration.switch_to_preview())
        self.assertEqual(app.controller.stage, "PreviewStage")

        class Refusing:
            def setActiveStage(self, stage):
                raise RuntimeError("stage not available")

        integration.controller = Refusing()
        self.assertFalse(integration.switch_to_preview())

    def test_scene_has_objects_needs_a_selectable_mesh(self):
        app, integration = self.build()
        root = type(app.controller.getScene().getRoot())()
        app.controller.getScene().root = root
        self.assertFalse(integration.scene_has_objects)  # no children at all
        mesh = object()
        empty = SimpleNamespace(isSelectable=lambda: False, getMeshData=lambda: mesh)
        root.getAllChildren = lambda: [empty]
        self.assertFalse(integration.scene_has_objects)
        root.getAllChildren = lambda: [empty, SimpleNamespace(isSelectable=lambda: True,
                                                              getMeshData=lambda: mesh)]
        self.assertTrue(integration.scene_has_objects)
        root.getAllChildren = lambda: [SimpleNamespace(isSelectable=lambda: True,
                                                       getMeshData=lambda: None)]
        self.assertFalse(integration.scene_has_objects)

    def test_scene_has_objects_degrades_without_a_scene(self):
        class Bare:
            def getScene(self):
                raise AttributeError("no scene")

        app, integration = self.build()
        integration.controller = Bare()
        self.assertFalse(integration.scene_has_objects)

    def test_show_nozzle_repairs_the_native_pass_in_preview(self):
        app, integration = self.build()
        view = self.SimulationView()
        app.controller.view = view
        integration._refresh()
        integration.show_nozzle()  # not in preview: nothing to repair
        app.controller.stage = SimpleNamespace(getId=lambda: "PreviewStage")
        scene_root = app.controller.getScene().getRoot()
        nozzle = SimpleNamespace(parent=None, visible=True)
        nozzle.getParent = lambda: nozzle.parent
        nozzle.setParent = lambda node: setattr(nozzle, "parent", node)
        nozzle.setVisible = lambda value: setattr(nozzle, "visible", value)
        simulation_pass = SimpleNamespace(_switching_layers=True, _old_current_layer=9)
        simulation_pass.setEnabled = lambda value: setattr(simulation_pass, "enabled", value)
        view.getController = lambda: app.controller
        view.getNozzleNode = lambda: nozzle
        view.getSimulationPass = lambda: simulation_pass
        view._layer = 5
        integration.show_nozzle()
        self.assertIs(nozzle.parent, scene_root)
        self.assertFalse(nozzle.visible)
        self.assertTrue(simulation_pass.enabled)
        self.assertFalse(simulation_pass._switching_layers)
        self.assertEqual(simulation_pass._old_current_layer, 5)
        self.assertTrue(view.activity)

    # -- layer tables -----------------------------------------------------

    def test_layer_heights_are_built_once_and_cached(self):
        app, integration = self.build()
        self.assertEqual(integration.heights, ())
        view = self.SimulationView(layers=3)
        app.controller.view = view
        integration._refresh()
        heights = integration.heights
        self.assertEqual(heights, (0.2, 0.2, 0.2))
        self.assertEqual(view.recalculated, 1)
        self.assertEqual(integration.heights, heights)

    def test_layer_heights_are_served_in_ticks_for_a_large_file(self):
        app, integration = self.build()
        view = self.SimulationView(layers=205)
        app.controller.view = view
        integration._refresh()
        # The table is spread over event-loop ticks so a big file cannot
        # freeze the UI; callers degrade gracefully on a partial table.
        self.assertLess(len(integration.heights), 205)
        self.assertTrue(self._accept(lambda: len(integration.heights) == 205))
        self.assertEqual(set(integration.heights), {0.2})

    def test_layer_height_failures_read_as_zero(self):
        app, integration = self.build()
        view = self.SimulationView(layers=3)
        view.bad_height = True
        app.controller.view = view
        integration._refresh()
        # An unreadable height is a zero in the table, not a lost table.
        self.assertEqual(integration.heights, (0.0, 0.0, 0.0))
        # A view without Cura's height accessor still yields a table.
        integration._view = SimpleNamespace()
        integration._heights = None
        self.assertEqual(integration.heights, (0.0,))

    def test_a_stale_or_closed_height_tick_appends_nothing(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView(layers=3)
        integration._refresh()
        _ = integration.heights  # establishes the batch and the build counter
        stale = []
        integration._build_heights_step(stale)
        self.assertEqual(stale, [])
        batch = []
        integration._heights = batch
        integration._heights_built = 0
        integration.close()
        integration._build_heights_step(batch)
        self.assertEqual(batch, [])

    def test_view_positions_degrade_when_the_view_cannot_answer(self):
        app, integration = self.build()
        self.assertIsNone(integration.selected_layer)
        self.assertIsNone(integration.max_layer)
        view = self.SimulationView()
        view._layer = 2
        app.controller.view = view
        integration._refresh()
        self.assertEqual(integration.selected_layer, 2)
        self.assertEqual(integration.max_layer, 2)
        view.broken = True
        self.assertIsNone(integration.selected_layer)
        self.assertIsNone(integration.max_layer)

    def test_watch_drives_the_position_timer(self):
        app, integration = self.build()
        integration.watch(True)
        self.assertTrue(integration._watch.isActive())
        integration.watch(False)
        self.assertFalse(integration._watch.isActive())
        integration.close()
        integration.watch(True)
        self.assertFalse(integration._watch.isActive())

    def test_position_changes_are_suppressed_during_preview_writes(self):
        app, integration = self.build()
        positions = []
        integration.positionChanged.connect(lambda: positions.append(1))
        integration._watch.setInterval(20)
        integration.watch(True)
        self.assertTrue(self._accept(lambda: bool(positions)))
        seen = len(positions)
        # The follower's own preview writes must not read as user drags.
        with integration.writing_preview() as view:
            self.assertIsNone(view)
            self._pump(0.15)
            self.assertEqual(len(positions), seen)
        self.assertTrue(self._accept(lambda: len(positions) > seen))
        integration.watch(False)

    # -- the load lease ---------------------------------------------------

    def test_load_hands_the_file_to_cura_and_releases_on_completion(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        lease, released = self._lease()
        loaded = []
        integration.fileLoaded.connect(loaded.append)
        self.assertTrue(integration.load(lease))
        self.assertTrue(integration.loading)
        self.assertEqual(app.loaded, [lease.path])
        app.fileCompleted.emit(lease.path)
        self.assertFalse(integration.loading)
        self.assertEqual(released, [lease.path])
        self.assertEqual(loaded, [lease.path])
        self.assertTrue(integration.suspended)

    def test_load_refuses_without_a_view_and_releases_the_lease(self):
        app, integration = self.build()
        lease, released = self._lease()
        failures = []
        integration.loadFailed.connect(failures.append)
        self.assertFalse(integration.load(lease))
        self.assertEqual(failures, ["Cura has no active printer yet"])
        self.assertEqual(released, [lease.path])
        self.assertEqual(app.loaded, [])

    def test_load_reports_a_host_that_refuses_the_file(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        app.read_error = RuntimeError("readLocalFile refused")
        lease, released = self._lease()
        failures = []
        integration.loadFailed.connect(failures.append)
        self.assertFalse(integration.load(lease))
        self.assertEqual(failures, ["readLocalFile refused"])
        self.assertFalse(integration.loading)
        self.assertEqual(released, [lease.path])

    def test_a_new_load_closes_the_timed_out_lease(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        integration.LOAD_WATCHDOG_MS = 20
        stale, stale_released = self._lease("stale.gcode")
        self.assertTrue(integration.load(stale))
        self.assertTrue(self._accept(lambda: not integration.loading))
        fresh, fresh_released = self._lease("fresh.gcode")
        self.assertTrue(integration.load(fresh))
        # The timed-out lease is parked and released by the next load, so a
        # silently-refused file's temp directory does not outlive it.
        self.assertEqual(stale_released, [stale.path])
        self.assertTrue(integration.loading)
        app.fileCompleted.emit(fresh.path)
        self.assertEqual(fresh_released, [fresh.path])

    def test_a_late_completion_after_the_watchdog_is_absorbed(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        integration.LOAD_WATCHDOG_MS = 20
        lease, released = self._lease()
        failures = []
        integration.loadFailed.connect(failures.append)
        self.assertTrue(integration.load(lease))
        self.assertTrue(self._accept(lambda: bool(failures)))
        self.assertIn("did not confirm the load in time", failures[0])
        self.assertTrue(os.path.exists(lease.path))  # dropped, never deleted
        invalidated = []
        integration.invalidated.connect(invalidated.append)
        app.fileCompleted.emit(lease.path)
        # The late parse finishes quietly: it is the load we asked for.
        self.assertEqual(invalidated, [])
        self.assertEqual(released, [lease.path])
        self.assertFalse(os.path.exists(lease.path))

    def test_an_unexpected_completion_invalidates_the_generation(self):
        app, integration = self.build()
        invalidated = []
        integration.invalidated.connect(invalidated.append)
        app.fileCompleted.emit("/tmp/somebody-elses.gcode")
        self.assertEqual(invalidated, ["Cura file replaced"])
        self.assertTrue(integration.suspended)

    def test_load_is_refused_once_the_adapter_is_closed(self):
        app, integration = self.build()
        lease, released = self._lease()
        integration.close()
        self.assertFalse(integration.load(lease))
        self.assertEqual(released, [lease.path])

    # -- replace confirmation ---------------------------------------------

    def test_confirm_replace_only_loads_on_yes(self):
        app, integration = self.build()
        answers = []
        asked = []

        class MessageBox:
            # The buttons are OR-ed into one mask, so they have to be ints.
            StandardButton = SimpleNamespace(Yes=1, No=2)

            @classmethod
            def question(cls, *args):
                asked.append(args)
                return answers[-1]

        with patch.object(self.module, "QMessageBox", MessageBox):
            answers.append(MessageBox.StandardButton.No)
            ran = []
            integration.confirm_replace(lambda: ran.append(1))
            self.assertTrue(self._accept(lambda: bool(asked)))
            self._pump(0.1)
            self.assertEqual(ran, [])
            answers.append(MessageBox.StandardButton.Yes)
            integration.confirm_replace(lambda: ran.append(1))
            self.assertTrue(self._accept(lambda: bool(ran)))
        self.assertEqual(len(asked), 2)
        self.assertEqual(app.controller.stage, "PreviewStage")

    def test_a_confirmed_replace_survives_a_scene_change_in_the_same_turn(self):
        """The user's answer outranks a Cura scene generation.

        A scene change processed on the turn after the answer (the stage
        switch this prompt makes is one source of them) used to reach
        queue()'s stale-token guard first and drop the load: the user
        answered Yes and nothing happened, with nothing in the log. The
        mac 5.12 leg's missing load is this shape.
        """
        from PyQt6.QtCore import QTimer
        app, integration = self.build()

        class MessageBox:
            StandardButton = SimpleNamespace(Yes=1, No=2)

            @classmethod
            def question(cls, *args):
                # The scene change lands while the box is up, so it is
                # processed before the callback queued after it.
                QTimer.singleShot(
                    0, lambda: integration.invalidate("Cura scene structure changed"))
                return MessageBox.StandardButton.Yes

        with patch.object(self.module, "QMessageBox", MessageBox):
            ran = []
            integration.confirm_replace(lambda: ran.append(1))
            self.assertTrue(self._accept(lambda: bool(ran)),
                            "the user's answer was dropped, not run")
        self.assertEqual(ran, [1])

    def test_a_confirmed_replace_does_not_run_the_callback_after_close(self):
        """The answer is deferred one turn, and shutdown wins that race.

        Dropping the token guard for the confirmed load must not mean
        running a callback into a closed adapter: the shutdown that
        lands while the box is up is the one case that still refuses.
        """
        app, integration = self.build()

        class MessageBox:
            StandardButton = SimpleNamespace(Yes=1, No=2)

            @classmethod
            def question(cls, *args):
                integration.close()
                return MessageBox.StandardButton.Yes

        with patch.object(self.module, "QMessageBox", MessageBox):
            ran = []
            integration.confirm_replace(lambda: ran.append(1))
            self._pump(0.1)
        self.assertEqual(ran, [])

    # -- shutdown ---------------------------------------------------------

    def test_close_releases_everything_it_owns(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        integration.watch(True)
        lease, released = self._lease()
        self.assertTrue(integration.load(lease))
        integration.close()
        self.assertFalse(integration.loading)
        self.assertEqual(released, [lease.path])
        self.assertFalse(integration._watch.isActive())
        self.assertEqual(integration._connections, [])
        self.assertEqual(integration._view_connections, [])
        self.assertEqual(integration.generation, 1)
        integration.close()  # idempotent
        self.assertEqual(integration.generation, 1)

    def test_close_releases_a_timed_out_lease_too(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        integration.LOAD_WATCHDOG_MS = 20
        lease, released = self._lease()
        self.assertTrue(integration.load(lease))
        self.assertTrue(self._accept(lambda: not integration.loading))
        integration.close()
        self.assertEqual(released, [lease.path])

    def test_invalidate_bumps_the_generation_and_drops_the_table(self):
        app, integration = self.build()
        app.controller.view = self.SimulationView()
        integration._refresh()
        _ = integration.heights
        reasons = []
        integration.invalidated.connect(reasons.append)
        integration.invalidate("user reload")
        self.assertEqual(integration.generation, 1)
        self.assertEqual(reasons, ["user reload"])
        self.assertIsNone(integration._heights)


if __name__ == "__main__":
    unittest.main()
