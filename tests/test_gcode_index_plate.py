"""The per-motion feature columns, and the demand that fills them.

Two surfaces share this file because they are one feature seen from both
ends. The index derives, for every motion, the slicer's feature type (the
plate payload's colours) and whether the motion is extruding (its travel
glyphs), and the index service's hydration window is what makes those
columns exist for the layers the live print sits on. The request stood
down whenever the current layer was
already hydrated, so the layer behind the print never filled and the face
degraded to an empty ghost.

The service's split is the third surface here: the follower paints the
boundary the LIVE position puts the nozzle at, refined through the same
search the Preview runs (the index's ``refined_fraction``, floored onto
the layer's motion grid), and that boundary is monotonic per print and
per layer.

The classification is the E-axis rule, stated once in the index: a
positive E step extrudes, anything else (no E, flat, or falling for a
retraction) does not, and every change of that state is a travel boundary.
Retractions are therefore not a separate class of marker — with retraction
disabled the same rule still finds the travel — and the boundary lists are
what the payload draws a glyph from, one per boundary.

Leftover lines, and why no test reaches them:

GCodeIndex.py
- _feature_columns' ``not isinstance(run, list)`` guard covers a run that
  is not a pair, but a run past that shape fails the pair length first on
  every shape a JSON header can produce; the two are kept apart so a
  future encoder cannot smuggle one past.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import plugins.GCodeIndex as gcode_index
from plugins.GCodeIndex import (
    LayerMotionIndex,
    PersistentIndexCache,
    _CACHE_MAGIC,
    _CACHE_VERSION,
    _MAX_TYPE_NAMES,
    _MAX_TYPE_RUNS_PER_LAYER,
    _TYPE_NONE,
    _TYPE_OTHER,
    build_index_from_bytes,
    build_index_from_file,
    hydrate_layer_from_file,
)
from plugins.MoonrakerProtocol import RemoteFileIdentity
from tests.qt_runtime_support import QT_AVAILABLE, runtime
from tests.test_plate_progress import make_index


def _write_gcode(data):
    handle = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False)
    handle.write(data)
    handle.close()
    return handle.name


def _codes(names):
    """The code each vocabulary entry takes: _TYPE_NONE, _TYPE_OTHER, then names."""
    return {name: code + 2 for code, name in enumerate(names)}


class FeatureTypeTests(unittest.TestCase):
    """The ;TYPE: marker walk: what a motion's type is, and what it costs."""

    def test_a_marker_types_every_motion_that_follows_it(self):
        index = build_index_from_bytes(
            b";LAYER:0\n"
            b";TYPE:WALL-OUTER\n"
            b"G1 X1 Y1 E0.5\n"
            b"G1 X2 Y2 E0.6\n"
            b";TYPE:SKIN\n"
            b"G1 X3 Y3 E0.7\n")
        names = index.type_names
        self.assertEqual(names, ["WALL-OUTER", "SKIN"])
        codes = _codes(names)
        # A slicer writes a marker when the feature CHANGES, so a motion
        # with no marker of its own keeps the last one seen (the run).
        self.assertEqual(index.motion_types[0],
                         [[2, codes["WALL-OUTER"]], [1, codes["SKIN"]]])
        self.assertEqual(sum(run[0] for run in index.motion_types[0]), 3)

    def test_a_marker_tolerates_indentation_and_keeps_a_multi_word_name(self):
        index = build_index_from_bytes(b";LAYER:0\n  \t;TYPE:Internal perimeter\nG1 X1 E1\n")
        self.assertEqual(index.type_names, ["Internal perimeter"])
        self.assertEqual(index.motion_types[0], [[1, 2]])

    def test_a_valueless_or_absent_marker_is_not_a_marker(self):
        # ";TYPE:" with nothing after it (and no marker at all) leaves the
        # motion untyped: the code word alone is not a feature name.
        index = build_index_from_bytes(b";LAYER:0\n;TYPE:\nG1 X1 E1\nG0 X2\n")
        self.assertEqual(index.type_names, [])
        self.assertEqual(index.motion_types[0], [[2, _TYPE_NONE]])

    def test_a_layer_closed_by_its_elapsed_marker_keeps_its_features(self):
        # ;TIME_ELAPSED closes a block as surely as the next ;LAYER does
        # (PauseAtHeight emits its pause after it), so the feature arrays
        # must be handed over there too — the last layer of a real file is
        # exactly this shape.
        index = build_index_from_bytes(
            b";LAYER:0\n;TYPE:WALL\nG1 X1 E1.0\nG1 E0.0\n;TIME_ELAPSED:7\n")
        self.assertEqual(index.motion_types, [[[2, 2]]])
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.layer_elapsed_times, [7.0])

    def test_the_vocabulary_is_capped_and_its_overflow_reads_unknown(self):
        names = ["T%d" % value for value in range(_MAX_TYPE_NAMES + 6)]
        data = b";LAYER:0\n" + b"".join(
            b";TYPE:%s\nG1 X1 E1\n" % name.encode("ascii") for name in names)
        index = build_index_from_bytes(data)
        self.assertEqual(len(index.type_names), _MAX_TYPE_NAMES)
        # The vocabulary cap is a bound on the index, not an error: past it
        # every name shares _TYPE_OTHER, so a hostile file cannot grow the
        # table with the line count (and the shared code merges their runs).
        self.assertEqual(index.motion_types[0][-1], [len(names) - _MAX_TYPE_NAMES, _TYPE_OTHER])
        self.assertEqual(sum(run[0] for run in index.motion_types[0]), len(names))

    def test_a_hydrated_layer_names_an_off_vocabulary_type_as_unknown(self):
        # The compact hydrator rebuilds the runs from the file's bytes; a
        # name the SCAN never registered (it fell past the vocabulary cap)
        # has no code of its own, so the layer's tail reads unknown rather
        # than being named by a code that belongs to another feature.
        names = ["T%d" % value for value in range(_MAX_TYPE_NAMES + 1)]
        data = b";LAYER:0\n" + b"".join(
            b";TYPE:%s\nG1 X1 E1\n" % name.encode("ascii") for name in names)
        path = _write_gcode(data)
        self.addCleanup(os.remove, path)
        full = build_index_from_file(path, compact=False)
        compact = build_index_from_file(path, compact=True)
        self.assertTrue(hydrate_layer_from_file(compact, path, 0))
        self.assertEqual(compact.motion_types, full.motion_types)
        self.assertEqual(compact.motion_types[0][-1], [1, _TYPE_OTHER])

    def test_the_run_list_is_capped_and_its_tail_reads_unknown(self):
        # Every marker followed by one motion: past the run cap the layer's
        # tail collapses into the last run instead of growing a list per
        # motion. The runs must still total the motion count, or save()
        # would refuse the index as ragged.
        count = _MAX_TYPE_RUNS_PER_LAYER + 500
        data = b";LAYER:0\n" + b"".join(
            b";TYPE:%s\nG1 X1 E1\n" % (b"A" if value % 2 else b"B")
            for value in range(count))
        index = build_index_from_bytes(data)
        runs = index.motion_types[0]
        self.assertEqual(len(runs), _MAX_TYPE_RUNS_PER_LAYER)
        self.assertEqual(sum(run[0] for run in runs), count)
        self.assertEqual(runs[-1][1], _TYPE_OTHER)


class TravelBoundaryTests(unittest.TestCase):
    """The E-axis rule: which motions are travel, and where a travel starts."""

    def test_a_retraction_starts_the_travel_and_the_prime_ends_it(self):
        index = build_index_from_bytes(
            b"M82\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 Y1 E1.0\n"
            b"G1 X2 Y2 E1.1\n"
            b"G1 E0.1\n"
            b"G0 X9 Y9\n"
            b"G1 E1.1\n"
            b"G1 X3 Y3 E1.2\n")
        # E stops rising at the retraction (falling is the same rule's
        # decreasing case) and resumes at the prime.
        self.assertEqual(index.travel_starts, [[2]])
        self.assertEqual(index.travel_ends, [[4]])
        self.assertEqual(index.layer_start_extruding, [True])

    def test_flat_e_classifies_travel_without_retractions(self):
        index = build_index_from_bytes(
            b"M82\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 Y1 E1.0\n"
            b"G0 X9 Y9\n"
            b"G0 X8 Y8\n"
            b"G1 X3 Y3 E1.1\n")
        # Retraction disabled: E merely holds across the travel, which is
        # the same "not extruding" state the retraction reaches.
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.travel_ends, [[3]])

    def test_any_positive_e_step_extrudes(self):
        # The rule is the G-code's own: a positive step extrudes, no
        # matter how small. The live file's fine walls step E by
        # ~0.014 mm per move, and a 0.05 mm layer height's short
        # segments step it by ~1e-4 — both were misread as travel by
        # the old magnitude floors (the live reports).
        index = build_index_from_bytes(
            b"M82\n;LAYER:0\nG1 X1 E1.0\n"
            b"G1 X2 E1.00008\n"
            b"G1 X3 E1.00016\n"
            b"G1 X4 E1.01402\n"
            b"G0 X5\n"
            b"G1 X6 E1.09\n")
        # One travel: the E-less move, and only that one.
        self.assertEqual(index.travel_starts, [[4]])
        self.assertEqual(index.travel_ends, [[5]])

    def test_flat_and_falling_e_do_not_extrude(self):
        # No E at all, an E that merely holds, and a retraction all
        # read as travel: the sign, not the magnitude, decides.
        index = build_index_from_bytes(
            b"M82\n;LAYER:0\nG1 X1 E1.0\n"
            b"G1 X2\n"
            b"G1 X3 E1.0\n"
            b"G1 X4 E0.25\n"
            b"G1 X5 E1.2\n")
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.travel_ends, [[4]])

    def test_relative_e_and_a_g92_rebase_keep_the_rule(self):
        index = build_index_from_bytes(
            b"M83\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 E1.0\n"
            b"G1 E-1.0\n"
            b"G0 X9\n"
            b"G1 E1.0\n"
            b"G92 E0\n"
            b"G1 X2 E0.2\n")
        # A G92 re-bases the axis without moving it: Cura's per-layer G92
        # E0 is not a retraction, and the rise that follows it is not a
        # travel end (no travel was open).
        self.assertEqual(index.travel_starts, [[1]])
        self.assertEqual(index.travel_ends, [[3]])
        self.assertEqual(index.layer_start_e_absolute, [False])

    def test_a_travel_that_crosses_a_layer_boundary_leaves_its_two_halves(self):
        path = _write_gcode(
            b"M82\n;LAYER:0\n;TYPE:WALL\n"
            b"G1 X1 E1.0\n"
            b"G1 E0.0\n"
            b";LAYER:1\n"
            b"G1 X5 E1.0\n"
            b"G1 X6 E1.2\n")
        self.addCleanup(os.remove, path)
        full = build_index_from_file(path, compact=False)
        self.assertEqual(full.travel_starts, [[1], []])
        self.assertEqual(full.travel_ends, [[], [0]])
        # The next layer opens mid-travel, so it must be told: the seed is
        # the pair the payload reads to know the glyph it drew in the
        # previous layer is the one this layer closes.
        self.assertEqual(full.layer_start_extruding, [True, False])
        self.assertEqual(full.layer_start_types, [_TYPE_NONE, _codes(full.type_names)["WALL"]])

    def test_hydration_reproduces_the_full_scans_features(self):
        data = (b"M82\n;LAYER:0\n;TYPE:WALL-OUTER\n"
                b"G1 X1 Y1 E1.0\nG1 X2 Y2 E1.1\nG1 E0.1\nG0 X9 Y9\nG1 E1.1\n"
                b";LAYER:1\n;TYPE:SKIN\n"
                b"G1 X5 Y5 E1.2\nG0 X1 Y1\nG1 X6 Y6 E1.3\n"
                b";LAYER:2\nG1 X7 Y7 E1.4\n")
        path = _write_gcode(data)
        self.addCleanup(os.remove, path)
        full = build_index_from_file(path, compact=False)
        # The compact hydrator runs its own parse loop over one layer's
        # bytes with no history; it must agree with the scan it stands in
        # for or the preview changes colour with the hydration state.
        for layer in range(len(full.ranges)):
            compact = build_index_from_file(path, compact=True)
            self.assertTrue(hydrate_layer_from_file(compact, path, layer, keep_anchor=layer))
            self.assertEqual(compact.motion_types[layer], full.motion_types[layer])
            self.assertEqual(compact.travel_starts[layer], full.travel_starts[layer])
            self.assertEqual(compact.travel_ends[layer], full.travel_ends[layer])
            self.assertEqual(compact.type_names, full.type_names)
            self.assertEqual(compact.motion_x[layer], full.motion_x[layer])


class FeatureCacheTests(unittest.TestCase):
    """The feature columns through the persistent cache: restored, or refused."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(prefix="mpfi-plate-")
        self.addCleanup(self._directory.cleanup)
        self.directory = self._directory.name
        self.cache = PersistentIndexCache(self.directory)

    def _identity(self, name="part.gcode"):
        return RemoteFileIdentity(name, 100, 1.0, "u1")

    def _path(self, remote):
        digest = hashlib.sha256(remote.stable_key().encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.mpfi.gz")

    def _header(self, remote, **overrides):
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
            "pauses": [],
            "compact": False,
            "hydrated": [0],
            "counts": [1],
        }
        header.update(overrides)
        return header

    def _write_raw(self, remote, header, count=1):
        # One motion: offset, x, y, z.
        body = (struct.pack("<Q", 8) * count + struct.pack("<f", 1.0) * count
                + struct.pack("<f", 2.0) * count + struct.pack("<f", 0.2) * count)
        raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
        with gzip.open(self._path(remote), "wb") as handle:
            handle.write(_CACHE_MAGIC)
            handle.write(struct.pack("<I", len(raw)))
            handle.write(raw)
            handle.write(body)
        return self._path(remote)

    def _featured_index(self):
        """A one-layer index carrying every feature column."""
        return build_index_from_bytes(
            b"M82\n;LAYER:0\n;TYPE:WALL-OUTER\n"
            b"G1 X1 Y1 E1.0\nG1 E0.0\nG0 X9 Y9\nG1 E1.0\n")

    def test_the_feature_columns_survive_the_round_trip(self):
        index = self._featured_index()
        identity = self._identity()
        self.cache.save(identity, index)
        restored = self.cache.load(identity)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.motion_types, index.motion_types)
        self.assertEqual(restored.travel_starts, index.travel_starts)
        self.assertEqual(restored.travel_ends, index.travel_ends)
        self.assertEqual(restored.type_names, index.type_names)
        self.assertEqual(restored.layer_start_types, index.layer_start_types)
        self.assertEqual(restored.layer_start_e, index.layer_start_e)
        self.assertEqual(restored.layer_start_e_absolute, index.layer_start_e_absolute)
        self.assertEqual(restored.layer_start_extruding, index.layer_start_extruding)
        self.assertEqual(restored.hydrated_layers, index.hydrated_layers)

    def test_a_compact_index_round_trips_its_hydrated_layers_features(self):
        data = (b"M82\n;LAYER:0\n;TYPE:WALL\nG1 X1 E1.0\n;LAYER:1\n;TYPE:SKIN\nG1 X2 E1.1\n")
        path = _write_gcode(data)
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        hydrate_layer_from_file(index, path, 1)
        identity = self._identity()
        self.cache.save(identity, index)
        restored = self.cache.load(identity)
        self.assertEqual(restored.hydrated_layers, {1})
        # The unhydrated layer keeps empty columns; the hydrated one keeps
        # the colours it was hydrated for, with its seed intact.
        self.assertEqual(restored.motion_types, [[], index.motion_types[1]])
        self.assertEqual(restored.type_names, index.type_names)
        self.assertEqual(restored.layer_start_types, index.layer_start_types)

    def test_a_blob_from_the_previous_version_is_refused(self):
        identity = self._identity()
        # A reader that accepted the older format would restore it with
        # empty feature columns and draw a colourless, travel-less plate.
        self._write_raw(identity, self._header(identity, version=_CACHE_VERSION - 1))
        self.assertIsNone(self.cache.load(identity))

    def test_a_header_without_the_feature_columns_still_loads(self):
        identity = self._identity()
        self._write_raw(identity, self._header(identity))
        restored = self.cache.load(identity)
        self.assertIsNotNone(restored)
        # The motion is restored and reads untyped: the columns must still
        # come back one entry per layer, or the hydrator could not fill the
        # layer it is asked for.
        self.assertEqual(restored.motion_types, [[[1, _TYPE_NONE]]])
        self.assertEqual(restored.travel_starts, [[]])
        self.assertEqual(restored.travel_ends, [[]])
        self.assertEqual(restored.type_names, [])
        self.assertEqual(restored.layer_start_types, [_TYPE_NONE])
        self.assertEqual(restored.layer_start_e, [0.0])
        self.assertEqual(restored.layer_start_e_absolute, [True])
        self.assertEqual(restored.layer_start_extruding, [True])

    def test_a_ragged_feature_column_is_never_written(self):
        index = self._featured_index()
        identity = self._identity()
        for label, column in (
            ("runs short of the motions", [[[1, 2]]]),
            ("runs past the motions", [[[3, 2]]]),
            ("a code off the vocabulary", [[[2, 9]]]),
            ("a zero-length run", [[[0, 2]]]),
            ("a marker outside the layer", [[[2, 2]]]),
            ("a shorter column", []),
        ):
            with self.subTest(label):
                index.motion_types = [[[2, 2]]]
                index.travel_starts = [[1]]
                if label == "a marker outside the layer":
                    index.travel_starts = [[4]]
                elif label == "a shorter column":
                    index.travel_starts = []
                else:
                    index.motion_types = column
                self.cache.save(identity, index)
                self.assertEqual(os.listdir(self.directory), [])

    def test_a_ragged_feature_column_in_a_blob_is_refused(self):
        identity = self._identity()
        for label, overrides in (
            ("runs short of the motions", {"counts": [2], "type_runs": [[[1, 0]]]}),
            ("runs that are not pairs", {"type_runs": [[[1]]]}),
            ("a run that is not a list", {"type_runs": [[1]]}),
            ("a marker outside the layer", {"travel_starts": [[1]]}),
            ("markers out of order", {"counts": [2], "travel_ends": [[1, 0]]}),
            ("a duplicated marker", {"counts": [2], "travel_ends": [[1, 1]]}),
            ("a code off the vocabulary", {"type_runs": [[[1, 7]]]}),
            ("a seed type off the vocabulary", {"start_types": [7]}),
            ("an unusable seed E", {"start_e": [None]}),
            ("a seed flag that is not a flag", {"start_extruding": [1]}),
            ("a vocabulary that is not a list", {"type_names": "junk"}),
            ("a vocabulary entry that is not a name", {"type_names": [7]}),
            ("a vocabulary past the cap", {"type_names": ["T%d" % v for v in range(_MAX_TYPE_NAMES + 1)]}),
            ("columns that are not columns", {"type_runs": 0}),
        ):
            with self.subTest(label):
                self._write_raw(identity, self._header(identity, **overrides))
                self.assertIsNone(self.cache.load(identity))

    def test_the_feature_columns_are_dropped_rather_than_refused_past_the_budget(self):
        index = self._featured_index()
        identity = self._identity()
        with patch.object(gcode_index, "_MAX_CACHE_FEATURE_ENTRIES", 0):
            self.cache.save(identity, index)
            restored = self.cache.load(identity)
        # The geometry is what the cache is for: a fragmented file keeps
        # its index and loses only the colours it could not afford.
        self.assertEqual(restored.motion_types, [[[4, _TYPE_NONE]]])
        self.assertEqual(restored.travel_starts, [[]])
        self.assertEqual(list(restored.motion_offsets[0]), list(index.motion_offsets[0]))
        self._write_raw(identity, self._header(identity, type_runs=[[[1, 0]]]))
        with patch.object(gcode_index, "_MAX_CACHE_FEATURE_ENTRIES", 0):
            # Budget measured across the whole blob: a layer's runs that
            # arrived over it are dropped here too, never half-restored.
            self.assertEqual(self.cache.load(identity).motion_types, [[[1, _TYPE_NONE]]])

    def test_an_over_long_header_is_not_written_at_all(self):
        identity = self._identity()
        with patch.object(gcode_index, "_MAX_CACHE_HEADER_BYTES", 8):
            # The reader would refuse this blob, so publishing it would
            # only spend the cache's byte budget on a dead file.
            self.cache.save(identity, self._featured_index())
        self.assertEqual(os.listdir(self.directory), [])


class FeatureRetentionTests(unittest.TestCase):
    """The feature columns follow the geometry in and out of the window."""

    DATA = (b"M82\n"
            b";LAYER:0\n;TYPE:WALL\nG1 X1 E1.0\nG1 E0.0\n"
            b";LAYER:1\nG1 X2 E1.0\nG1 X3 E1.1\n"
            b";LAYER:2\n;TYPE:SKIN\nG1 X4 E1.2\nG0 X0 Y0\nG1 X6 E1.25\n"
            b";LAYER:3\nG1 X5 E1.3\n")

    def test_the_padding_loop_covers_the_feature_columns(self):
        path = _write_gcode(self.DATA)
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        # A compact index carries a column per layer from the start; drop
        # them so the hydration's grow loop is the only thing filling
        # them, which is what a restored blob of an older shape needs.
        index.motion_offsets, index.motion_x, index.motion_y, index.motion_z = [], [], [], []
        index.motion_types, index.travel_starts, index.travel_ends = [], [], []
        index.layer_start_types, index.layer_start_e = [], []
        index.layer_start_e_absolute, index.layer_start_extruding = [], []
        self.assertTrue(hydrate_layer_from_file(index, path, 1, keep_anchor=1))
        self.assertEqual(len(index.motion_types), len(index.ranges))
        self.assertEqual(index.motion_types[0], [])
        self.assertEqual(index.travel_starts[2], [])
        self.assertEqual(index.layer_start_types, [_TYPE_NONE] * len(index.ranges))
        self.assertEqual(index.layer_start_extruding, [True] * len(index.ranges))
        self.assertEqual(index.motion_count(1), 2)

    def test_the_retention_window_evicts_the_feature_columns(self):
        path = _write_gcode(self.DATA)
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        for layer in range(4):
            self.assertTrue(hydrate_layer_from_file(index, path, layer, keep_anchor=3))
        # The window is [anchor-1, anchor+1]: layers 0 and 1 are behind it,
        # and an evicted layer must not keep runs for a motion list it no
        # longer has.
        self.assertEqual(index.hydrated_layers, {2, 3})
        self.assertEqual(index.motion_types[0], [])
        self.assertEqual(index.travel_starts[0], [])
        self.assertEqual(index.travel_ends[0], [])
        # The arrays went, but the count it recorded persists.
        self.assertEqual(index.motion_count(0), 2)
        self.assertEqual(index.motion_types[3], [[1, 3]])
        self.assertEqual(index.travel_starts[2], [1])

    def test_the_frozen_layer_holds_its_own_window_beside_the_live_print(self):
        data = b"M82\n" + b"".join(b";LAYER:%d\nG1 X%d E1.0\n" % (n, n + 1) for n in range(6))
        path = _write_gcode(data)
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=True)
        # The live print stands at layer 0; the follower is frozen on 5
        # (the pop-over's detach). Every hydration here evicts against
        # the live window alone — the frozen layer and its own
        # neighbours survive on the second anchor.
        index.manual_anchor = 5
        for layer in (5, 4, 3, 2):
            self.assertTrue(hydrate_layer_from_file(index, path, layer, keep_anchor=0))
        # The frozen layer and the neighbour below it hold, and each
        # freshly hydrated layer keeps its own ±1 window until the
        # NEXT hydrate evicts it (the background pass's guarantee:
        # the arrays stay valid through the prepare and encode). The
        # walk's newest layer is always the survivor — memory stays
        # bounded at the three windows.
        self.assertEqual(index.hydrated_layers, {2, 3, 4, 5})
        self.assertEqual(index.motion_count(5), 1)
        # The control: with no frozen anchor the same walk keeps the
        # LAST freshly hydrated layer's window alone — still bounded,
        # and the pass's prepare window is always the most recent.
        control = build_index_from_file(path, compact=True)
        for layer in (5, 4, 3, 2):
            self.assertTrue(hydrate_layer_from_file(control, path, layer, keep_anchor=0))
        self.assertEqual(control.hydrated_layers, {2, 3})


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class HydrationWindowTests(unittest.TestCase):
    """The service demand: the anchor's own three layers, or none."""

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Files(QObject):
            changed = pyqtSignal()

        self.files = Files()
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        module = self.qt.load("GCodeIndexService")
        self.service = module.GCodeIndexService(self.files, object())
        self.addCleanup(self.service.close)
        # Nothing is ever submitted: _advance stands down without a wanted
        # request, so these cases read the pure demand state.
        self.job = ("part.gcode", 100, 1)
        self.service.bind(self.job)

    def _bind(self, layers=5, hydrated=()):
        index = LayerMotionIndex(ranges=[(value * 10, value * 10 + 10) for value in range(layers)])
        index.compact = True
        index.hydrated_layers = set(hydrated)
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.service._view = view
        return index

    def test_a_hydration_request_covers_the_layers_around_the_layer(self):
        self._bind()
        self.service.request_hydration(2)
        # The face reads the previous layer's ghost, the current layer and
        # the look-ahead; a request that asked for the current layer alone
        # left the ghost empty for the whole print.
        self.assertEqual(self.service._hydrate, {1, 2, 3})
        self.service._hydrate.clear()
        self.service.request_hydration(0)
        self.assertEqual(self.service._hydrate, {0, 1})

    def test_a_request_for_a_hydrated_layer_still_demands_its_presentation(self):
        self._bind(hydrated=(2,))
        self.service.request_hydration(2)
        # Hydration is a SOURCE state, not presentation readiness. The
        # current layer remains demanded until the decoded cache holds it,
        # alongside the two ghosts.
        self.assertEqual(self.service._hydrate, {1, 2, 3})

    def test_a_request_outside_the_index_is_ignored(self):
        self._bind()
        self.service.request_hydration(9)
        self.service.request_hydration(-1)
        self.assertEqual(self.service._hydrate, set())

    def test_a_refused_layer_is_not_asked_for_again(self):
        self._bind()
        self.service._failed_hydrate.add(2)
        self.service.request_hydration(2)
        # The latch spares the next poll a whole-file re-read; the other
        # two layers are still worth asking for.
        self.assertEqual(self.service._hydrate, {1, 3})

    def test_a_moved_anchor_retops_the_window(self):
        index = self._bind(hydrated=(4,))
        self.service._hydrate = {0, 1, 2}
        self.service.set_followed_layer(3)
        self.assertEqual(index.followed_layer, 3)
        # The anchor is pruned to its own window, then topped back up:
        # the layer the print has just left is exactly the ghost the face
        # wants, and it is not in the request any more.
        self.assertEqual(self.service._hydrate, {2, 3, 4})

    def test_a_manual_anchor_demands_its_window_beside_the_live_one(self):
        index = self._bind(layers=8)
        index.followed_layer = 1
        self.service.set_manual_anchor(5)
        # The frozen layer's window is asked for on its own demand path:
        # re-anchoring the live window would evict the live layer the
        # dot, the split and the printed fill all read.
        self.assertEqual(self.service._hydrate, {4, 5, 6})
        self.assertEqual(index.manual_anchor, 5)
        self.service._hydrate.clear()
        self.service.request_hydration(1)
        self.assertEqual(self.service._hydrate, {0, 1, 2})
        # Rejoining the print drops the frozen demand.
        self.service.set_manual_anchor(None)
        self.assertIsNone(index.manual_anchor)
        self.assertEqual(self.service._hydrate, {0, 1, 2})

    def test_a_manual_anchor_outside_the_index_is_never_asked_for(self):
        index = self._bind(layers=8)
        self.service.set_manual_anchor(11)
        # A layer outside the file is no anchor: nothing is demanded and
        # the retention bound keeps the live window alone.
        self.assertEqual(self.service._hydrate, set())
        self.assertIsNone(index.manual_anchor)

    def test_the_frozen_anchor_reaches_an_index_built_after_the_detach(self):
        self._bind(layers=8)
        # The detach can land before the build, with no index to carry
        # the anchor yet.
        self.service._view = None
        self.service.set_manual_anchor(6)
        index = self._bind(layers=8)
        # The poll's advance hands the held anchor to the new index.
        self.service._apply_manual_anchor()
        self.assertEqual(index.manual_anchor, 6)

    def test_the_full_marker_draws_every_motion_of_the_frozen_layer(self):
        # A seek lands the layer at 100% (the live request): the FULL
        # marker resolves to the layer's own motion count, whatever
        # layer it points at.
        index = self._bind(layers=8)
        index.motion_offsets = [list(range(3)) for _ in range(8)]
        index.motion_offsets[5] = list(range(11))
        self.service.set_manual_anchor(5)
        self.service.set_manual_split(-1)
        payload = self.service.plate_progress(5, file_position=None)
        self.assertEqual(payload["split"], 11)

    def test_the_manual_split_is_the_frozen_layers_boundary(self):
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service.set_manual_split(37)
        # A frozen layer carries no file position; the scrub is what
        # the payload reads for the boundary.
        payload = self.service.plate_progress(5, file_position=None)
        self.assertEqual(payload["split"], 37)
        # A live poll of the print's own layer is untouched.
        payload = self.service.plate_progress(2, file_position=42)
        self.assertIsNone(payload["split"])

    def test_rejoining_the_print_abandons_the_scrub(self):
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service.set_manual_split(37)
        self.service.set_manual_anchor(None)
        payload = self.service.plate_progress(5, file_position=None)
        self.assertIsNone(payload["split"])

    def test_the_progress_payload_carries_the_layers_motion_count(self):
        # motionTotal is the slider's range: the layer's own edge count.
        index = self._bind(layers=8)
        index.motion_offsets = [list(range(10)) for _ in range(8)]
        payload = self.service.plate_progress(3, file_position=None)
        self.assertEqual(payload["motionTotal"], 10)

    def test_the_full_cache_answers_after_the_window_evicts(self):
        # The full prepared cache's promise: a layer the window's
        # store no longer holds is served from the compact form —
        # decoded by the WORKER into the hot cache (never on the UI
        # thread), and the bundle's first display
        # reuses the worker's own payload instead of decoding a second
        # object .
        index = self._bind(layers=8, hydrated=(5,))
        from plugins.PlateProgress import encode_layer, prepare_layer
        payload = prepare_layer(index, 5)
        self.assertIsNotNone(payload)
        self.service._full_cache[5] = encode_layer(payload)
        from plugins.PlateProgress import _prepared_layers
        _prepared_layers.pop((id(index), 5), None)
        # The worker's commit lands the decoded payload in the hot
        # presentation cache.
        self.service._decoded_lru[5] = payload
        bundle = self.service.plate_layers(5)
        self.assertIs(bundle["current"], payload,
                      "the worker's payload was decoded again for the first display")
        self.assertEqual(bundle["current"]["motions"], payload["motions"])

    def test_alternating_anchors_keep_both_bundles(self):
        # The live payload and the frozen one alternate every poll
        # while detached; one memo slot thrashed — each ask evicted
        # the other's bundle and the identities churned per poll (the
        # live report: the whole plugin went awful while detached).
        self._bind(layers=8, hydrated=(3, 4, 5))
        live = self.service.plate_layers(4)
        frozen = self.service.plate_layers(9)
        self.assertIsNot(frozen, live)
        self.assertIs(self.service.plate_layers(4), live,
                      "the live bundle rebuilt on the alternating ask")
        self.assertIs(self.service.plate_layers(9), frozen,
                      "the frozen bundle rebuilt on the alternating ask")

    def test_the_layers_memo_rebuilds_when_the_hydration_fill_lands(self):
        # The live report: a far seek's current stayed blank forever —
        # the bundle was memoised while the anchor's layer was still
        # loading, and the fill state never entered the key. The fill
        # is the worker's decoded store now: hydration alone serves
        # nothing (the UI thread never prepares or decodes).
        index = self._bind(layers=8, hydrated=(4, 6))
        self.service.set_manual_anchor(5)
        first = self.service.plate_layers(5)
        self.assertIsNone(first["current"])
        from plugins.PlateProgress import prepare_layer
        index.hydrated_layers.add(5)
        self.service._decoded_lru[5] = prepare_layer(index, 5)
        second = self.service.plate_layers(5)
        self.assertIsNotNone(second["current"])

    def test_a_request_outside_the_followed_window_is_not_queued(self):
        index = self._bind()
        index.followed_layer = 0
        self.service.request_hydration(4)
        self.assertEqual(self.service._hydrate, set())
        index.followed_layer = None
        self.service.request_hydration(4)
        self.assertEqual(self.service._hydrate, {3, 4})


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class PlateSplitRefinementTests(unittest.TestCase):
    """The worker-side boundary as the coordinator asks for it: the
    coarse file position refined by the live tool position, monotonic
    across polls and honest when the refinement is unavailable.

    The geometry is the synthetic row (``tests.test_plate_progress``'s
    ``make_index``): motion m runs from x = m - 1 to x = m at z = 0.2,
    with the dispatcher's byte offsets far ahead of any one motion. The
    live position is what tells the boundary where the NOZZLE is.
    """

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Files(QObject):
            changed = pyqtSignal()

        self.files = Files()
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        module = self.qt.load("GCodeIndexService")
        self.service = module.GCodeIndexService(self.files, object())
        self.addCleanup(self.service.close)
        self.job = ("part.gcode", 100, 1)
        self.service.bind(self.job)

    def _bind(self, layers=1, motions=20):
        index = make_index(layers=layers, motions=motions)
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.service._view = view
        # The worker's commit: the decoded payloads land in the hot
        # presentation cache — the bundle reads no other store (the
        # UI thread never prepares or decodes).
        from plugins.PlateProgress import prepare_layer
        for layer in range(layers):
            self.service._decoded_lru[layer] = prepare_layer(index, layer)
        return list(index.motion_offsets[0])

    def test_a_machine_without_live_telemetry_keeps_the_coarse_boundary(self):
        offsets = self._bind()
        self.assertEqual(self.service.plate_progress(0, offsets[9])["split"], 9)
        # The floor never latches onto a coarse value, so a printer that
        # reports no live position paints exactly what it always did.
        self.assertEqual(self.service.plate_progress(0, offsets[19])["split"], 19)

    def test_the_live_position_refines_the_split_and_holds_it(self):
        offsets = self._bind()
        # The dispatcher is at the layer's end; the head is at x = 5,
        # which is 6 finished motions.
        payload = self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))
        self.assertEqual(payload["split"], 6)
        self.assertEqual(payload["method"], "motion index")
        self.assertIsNotNone(payload["layers"]["current"])
        # Telemetry gone: the painted boundary is held, never replaced
        # by the dispatcher's position.
        self.assertEqual(self.service.plate_progress(0, offsets[19])["split"], 6)
        # Telemetry present but off-path (a park, a probe): the same.
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (-40.0, -40.0, 0.2))["split"], 6)

    def test_the_boundary_never_walks_backwards_across_polls(self):
        offsets = self._bind()
        painted = 0
        for x in (4.0, 9.0, 9.0, 6.0, 12.0, 11.0, 18.5):
            split = self.service.plate_progress(0, offsets[19], (x, 0.0, 0.2))["split"]
            self.assertGreaterEqual(split, painted, "the fill rewound at x=%s" % x)
            painted = split
        self.assertEqual(painted, 19)

    def test_a_paused_park_holds_the_boundary(self):
        offsets = self._bind()
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        # Paused: the dispatcher is frozen where it stopped and the
        # pause macro parks the head off the path. Nothing is being
        # printed, so nothing new is painted — the stalled-ahead parser
        # position is never the answer, and repetition changes nothing.
        for _poll in range(2):
            self.assertEqual(
                self.service.plate_progress(0, offsets[19], (140.0, 140.0, 10.0))["split"], 6)
        # Resumed: the head is back on the path ahead, and the paint
        # follows it again.
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (12.0, 0.0, 0.2))["split"], 13)

    def test_an_anchor_off_the_index_reads_unavailable(self):
        # No layer is no boundary: the face ghosts rather than colouring
        # to another layer's count, whatever the telemetry says.
        offsets = self._bind()
        payload = self.service.plate_progress(9, offsets[19], (5.0, 0.0, 0.2))
        self.assertIsNone(payload["split"])
        self.assertEqual(payload["method"], "unavailable")

    def test_the_boundary_resets_with_the_layer(self):
        offsets = self._bind(layers=3)
        self.assertEqual(
            self.service.plate_progress(1, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        # Another layer's count is another layer's boundary: layer 2
        # starts its own, unfloored by layer 1's paint.
        self.assertEqual(
            self.service.plate_progress(2, offsets[19], (2.0, 0.0, 0.2))["split"], 3)

    def test_the_boundary_resets_with_the_print(self):
        offsets = self._bind()
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        # The next print's file: the same anchor, a different job, so
        # the previous print's boundary is not this one's floor.
        self.job = ("next.gcode", 200, 1)
        self.service.bind(self.job)
        self._bind()
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (2.0, 0.0, 0.2))["split"], 3)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class PlateVisitedTests(unittest.TestCase):
    """The printed-object verdict: which polygons the executed
    EXTRUSION geometry has reached. The walk reads the same motion
    edges the payload draws, so a travel that crosses or ends inside a
    polygon deposits nothing there, while an extrusion that only clips
    a corner marks it."""

    # A 40 x 30 mm object, well inside the bed.
    POLYGON = [[20.0, 10.0], [60.0, 10.0], [60.0, 40.0], [20.0, 40.0]]
    ROWS = [{"name": "Widget", "polygon": POLYGON}]

    # The late-arriving geometry. Motions: 0 the opening travel, 1 a
    # prime, 2 a travel, 3 the extrusion that crosses Widget and starts
    # inside Box, 4 a travel back, 5 a vertical extrusion inside Box,
    # 6 a travel away, 7 an extrusion inside Fork, 8 one that touches
    # nothing. Spoon sits where nothing ever prints.
    LATE = (b"M82\n;LAYER:0\n"
            b"G0 X0 Y0\n"
            b"G1 X5 Y0 E1\n"
            b"G0 X5 Y5\n"
            b"G1 X75 Y45 E2\n"
            b"G0 X5 Y5\n"
            b"G1 X5 Y45 E3\n"
            b"G0 X150 Y150\n"
            b"G1 X155 Y150 E4\n"
            b"G1 X160 Y150 E5\n")
    BOX = [[0.0, 2.0], [10.0, 2.0], [10.0, 20.0], [0.0, 20.0]]
    SPOON = [[100.0, 100.0], [120.0, 100.0], [120.0, 120.0], [100.0, 120.0]]
    FORK = [[145.0, 140.0], [165.0, 140.0], [165.0, 160.0], [145.0, 160.0]]

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Files(QObject):
            changed = pyqtSignal()

        self.files = Files()
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.service = self.qt.load("GCodeIndexService").GCodeIndexService(self.files, object())
        self.addCleanup(self.service.close)
        self.job = ("part.gcode", 100, 1)
        self.service.bind(self.job)

    def _bind(self, data, hydration=None):
        """Index the literal G-code and point the service at it. A
        compact index is what the live follower carries, and
        *hydration* fills a layer the way the worker's job does."""
        path = _write_gcode(data)
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path, compact=hydration is not None)
        for layer in hydration or ():
            self.assertTrue(hydrate_layer_from_file(index, path, layer, keep_anchor=layer))
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.service._view = view
        return index

    def _rows(self, *objects):
        """Rows as the live path builds them: a fresh dict and a fresh
        polygon list per tick, so only the CONTENT can identify the
        geometry."""
        return [{"name": name, "polygon": [list(point) for point in polygon]}
                for name, polygon in objects]

    def _counting_walk(self):
        """The edges the walker actually walks — the module's own seek,
        wrapped. A poll that re-scans consumed motion reads high here,
        which is the cost the cache exists to avoid. The recording lags
        one edge: a walk breaks ON the edge past its stop, so the lag
        counts what it drew without the seek's look-ahead."""
        module = self.qt.load("GCodeIndexService")
        real = module._motion_edges
        walked = []

        def counted(index, anchor, first=0):
            drawn = None
            for edge in real(index, anchor, first):
                if drawn is not None:
                    walked.append(drawn)
                drawn = edge
                yield edge
            if drawn is not None:
                # Exhausted rather than broken out of: that last edge was
                # drawn. A break leaves the iterator suspended, so the
                # look-ahead is never counted.
                walked.append(drawn)

        patcher = patch.object(module, "_motion_edges", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        return walked

    def test_a_travel_across_a_polygon_deposits_nothing(self):
        # Motions: 1 the prime that starts the print, 2 and 3 the travels
        # (the second crossing the polygon), 4 the extrusion that finally
        # reaches it.
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n"
                   b"G0 X0 Y25\nG0 X80 Y25\nG1 X40 Y25 E2\n")
        self.assertEqual(self.service.plate_visited(0, 4, self.ROWS), frozenset(),
                         "a travel crossing the polygon marked it printed")
        self.assertEqual(self.service.plate_visited(0, 5, self.ROWS), frozenset({"Widget"}),
                         "the extrusion that reached the polygon did not mark it")

    def test_an_extrusion_that_only_clips_a_corner_marks_it(self):
        # Both endpoints outside, the segment crossing the rectangle: the
        # endpoint-only reading would call this a miss.
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\nG0 X5 Y5\nG1 X75 Y45 E2\n")
        self.assertEqual(self.service.plate_visited(0, 4, self.ROWS), frozenset({"Widget"}))

    def test_a_later_travel_never_unmarks_a_printed_object(self):
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n"
                   b"G0 X5 Y5\nG1 X75 Y45 E2\nG0 X0 Y0\nG1 X80 Y25 E3\n")
        self.assertEqual(self.service.plate_visited(0, 4, self.ROWS), frozenset({"Widget"}))
        # The next poll's travel crosses everything and changes nothing;
        # a backwards split (a restart) keeps the verdict too, because
        # the walk's cursor only ever advances.
        self.assertEqual(self.service.plate_visited(0, 6, self.ROWS), frozenset({"Widget"}))
        self.assertEqual(self.service.plate_visited(0, 2, self.ROWS), frozenset({"Widget"}))

    def test_a_compact_layer_is_walked_like_a_full_one(self):
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n;LAYER:1\n"
                b"G0 X5 Y5\nG1 X75 Y45 E2\n")
        # Layer 1 is the one that prints the object, and a compact index
        # carries its motions only once the worker has hydrated it.
        self._bind(data, hydration=(1,))
        self.assertEqual(self.service.plate_visited(1, 2, self.ROWS), frozenset({"Widget"}))

    def test_a_moved_anchor_restarts_the_walk(self):
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n;LAYER:1\n"
                b"G0 X5 Y5\nG1 X75 Y45 E2\n")
        self._bind(data, hydration=(0, 1))
        self.assertEqual(self.service.plate_visited(0, 2, self.ROWS), frozenset())
        self.assertEqual(self.service.plate_visited(1, 2, self.ROWS), frozenset({"Widget"}),
                         "the anchor change did not restart the walk at the layer's start")

    def test_rows_without_a_usable_polygon_are_ignored(self):
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X75 Y45 E1\n")
        rows = [{"name": "Widget", "polygon": None},
                {"name": "", "polygon": self.POLYGON},
                {"name": "Box", "polygon": [[70.0, 40.0], [80.0, 40.0], [80.0, 50.0], [70.0, 50.0]]}]
        self.assertEqual(self.service.plate_visited(0, 2, rows), frozenset({"Box"}))

    def test_a_polygon_arriving_after_its_extrusion_marks_it(self):
        # The late-DEFINE sequence: the walk advances with no geometry
        # at all, then EXCLUDE_OBJECT_DEFINE executes and the polygon
        # arrives covering an extrusion already consumed. A bare cursor
        # never looks back, so the object stayed grey until a later
        # layer.
        self._bind(self.LATE)
        self.assertEqual(self.service.plate_visited(0, 8, []), frozenset(),
                         "the geometry-free poll marked something")
        self.assertEqual(
            self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX))),
            frozenset({"Widget", "Box"}),
            "the late polygon was not replayed against the consumed extrusion")

    def test_a_settled_poll_never_rescans_prior_motion(self):
        self._bind(self.LATE)
        walked = self._counting_walk()
        # The opening poll with no geometry walks the layer once.
        self.service.plate_visited(0, 8, [])
        self.assertEqual(len(walked), 8, "the opening poll did not walk the layer")
        walked.clear()
        # The geometry arriving costs ONE replay of the consumed range.
        self.assertEqual(self.service.plate_visited(
            0, 8, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
            frozenset({"Widget", "Box"}))
        self.assertEqual(len(walked), 8, "the replay did not cover the consumed range")
        walked.clear()
        # Rebuilt-but-equal rows are the same geometry: no walk at all.
        for _ in range(3):
            self.assertEqual(self.service.plate_visited(
                0, 8, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
                frozenset({"Widget", "Box"}))
        self.assertEqual(walked, [], "a settled poll re-walked the consumed motion")
        # An advanced split walks the new edge alone, never the range.
        self.assertEqual(self.service.plate_visited(
            0, 9, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
            frozenset({"Widget", "Box"}))
        self.assertEqual([edge[0] for edge in walked], [8],
                         "the advanced poll re-walked the layer")

    def test_another_object_arriving_later_is_judged_on_the_consumed_range(self):
        self._bind(self.LATE)
        self.assertEqual(self.service.plate_visited(0, 4, self._rows(("Widget", self.POLYGON))),
                         frozenset({"Widget"}))
        # Box arrives at the SAME split: only the consumed extrusion 3
        # covers it, so the verdict turns on the replay alone.
        self.assertEqual(
            self.service.plate_visited(0, 4, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX),
                                                        ("Spoon", self.SPOON))),
            frozenset({"Widget", "Box"}),
            "the incrementally-defined object was not judged on the consumed range")
        # Fork arrives with new motion instead: the two halves compose
        # into one verdict, and the untouched object stays unvisited.
        self.assertEqual(
            self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX),
                                                        ("Spoon", self.SPOON),
                                                        ("Fork", self.FORK))),
            frozenset({"Widget", "Box", "Fork"}),
            "the same poll's replay and delta did not compose")
        self.assertEqual(
            self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON),
                                                        ("Box", self.BOX),
                                                        ("Spoon", self.SPOON),
                                                        ("Fork", self.FORK))),
            frozenset({"Widget", "Box", "Fork"}),
            "a later poll changed the verdicts")

    def test_a_changed_polygon_replays_the_consumed_range(self):
        # The same name with moved vertices is new geometry: the cache
        # keys on content, so the consumed range is judged again.
        self._bind(self.LATE)
        away = [[100.0, 130.0], [120.0, 130.0], [120.0, 140.0], [100.0, 140.0]]
        self.assertEqual(self.service.plate_visited(0, 8, self._rows(("Widget", away))),
                         frozenset(), "the far polygon marked something")
        self.assertEqual(self.service.plate_visited(0, 8, self._rows(("Widget", self.POLYGON))),
                         frozenset({"Widget"}),
                         "the moved polygon did not replay the consumed extrusion")

    def test_a_new_layer_rewalks_its_own_motion(self):
        # Layer 0 settles the geometry without printing the object; the
        # anchor move must not read that settled geometry as covering
        # layer 1's own motions, and must reanchor in both directions.
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n"
                b";LAYER:1\nG0 X5 Y5\nG1 X75 Y45 E2\n")
        self._bind(data, hydration=(0, 1))
        rows = self._rows(("Widget", self.POLYGON))
        self.assertEqual(self.service.plate_visited(0, 2, rows), frozenset(),
                         "layer 0 printed the object")
        self.assertEqual(self.service.plate_visited(1, 2, rows), frozenset({"Widget"}),
                         "the settled geometry suppressed layer 1's own walk")
        self.assertEqual(self.service.plate_visited(0, 2, rows), frozenset(),
                         "layer 0's verdict survived the anchor move")


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class PreparedReopenPolicyTests(unittest.TestCase):
    """The reopen/repair/persist policy: the fast path, the repair copy, the
    demand-persistence, the store-census fraction, the byte budgets
    and the rebind abort."""

    class Identity:
        uuid = "u"
        modified = 1

        def __init__(self, key):
            self._key = key

        def stable_key(self):
            return self._key

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Files(QObject):
            changed = pyqtSignal()

            def __init__(self):
                super().__init__()
                self.job_key = ("part.gcode", 100, 1)
                self.identity = PreparedReopenPolicyTests.Identity("print-key")

            def lease(self):
                class Lease:
                    path = ""

                    def close(self):
                        pass
                return Lease()

            def request_metadata(self):
                pass

            def request_file(self):
                pass

        self.files = Files()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        from plugins.PreparedStore import PreparedCache, STATE_CACHED
        self.store = PreparedCache(self._dir.name)
        self.state_cached = STATE_CACHED
        module = self.qt.load("GCodeIndexService")
        self.service = module.GCodeIndexService(self.files, object(), prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = True
        self.service._wanted = True

    def _view(self, layers=5):
        index = make_index(layers=layers, motions=20)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        return index

    def _pump(self, timeout=5.0):
        """Drive _advance until the pass and its save settle. The
        worker's completion rides a QUEUED signal — the loop must
        process events, not just sleep."""
        from PyQt6.QtCore import QCoreApplication
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.service._advance()
            if self.service._prepared_saved and not self.service._busy:
                return
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.fail("the prepared pass did not settle")

    @staticmethod
    def _payload(layer):
        from plugins.PlateProgress import encode_layer
        return encode_layer({"classes": {"SKIN": [[[0.0, 0.0, 0.0], [1.0, float(layer), 1.0]]]},
                             "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2})

    def test_a_complete_reopen_takes_the_fast_path(self):
        # : a valid complete cache must not
        # read its own bytes back — the table says complete, the
        # pass stands down, and the fraction reads 100% with zero
        # RAM residency.
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        reads = []
        original_read = self.store.read
        self.store.read = lambda identity, table, layer: (
            reads.append(layer) or original_read(identity, table, layer))
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved)
        self.assertEqual(self.service._full_next, 5)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.service._advance()
        self.assertEqual(self.service._busy, "", "the pass ran after a fast-path reopen")
        self.assertIsNone(self.service._prepared_writer)
        self.assertEqual(reads, [], "the reopen replayed the store")

    def test_a_holey_reopen_repairs_without_losing_valid_entries(self):
        # The sparse-repair regression: 0,1,3,4 valid and
        # 2 missing — the repair regenerates 2 and COPIES the valid
        # entries into the new file; nothing complementary-holes.
        from plugins.PlateProgress import decode_layer
        writer = self.store.open_for_write("print-key", 5)
        for layer in (0, 1, 3, 4):
            self.store.append(writer, layer, self._payload(layer))
        self.store.finish_write(writer)
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertFalse(self.service._prepared_saved,
                         "a holey table took the fast path")
        self._pump()
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(all(entry[0] == self.state_cached for entry in loaded["table"]),
                        "the repair published a complementary hole")
        for layer in (0, 1, 3, 4):
            # The COPIED entries keep their exact payloads.
            raw = self.store.read("print-key", loaded["table"], layer)
            self.assertEqual(decode_layer(raw)["classes"]["SKIN"][0][1][1],
                             float(layer), "layer %d reads another layer's payload" % layer)
        # The REGENERATED hole is the synthetic index's own geometry.
        regrown = decode_layer(self.store.read("print-key", loaded["table"], 2))
        self.assertIn("WALL-OUTER", regrown["classes"])
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_a_demand_prepared_layer_persists_into_the_writer(self):
        # : a manual/live demand prepares the
        # layer BEFORE the pass reaches it — the encoded bytes must
        # enter the writer anyway, or the finish publishes a (0,0)
        # hole for a layer that WAS prepared.
        self._view(5)
        self.service._prepared_open(self.files.identity)
        encoded = self._payload(2)
        self.service._prepared_persist(2, encoded)
        self.assertIsNotNone(self.service._prepared_writer,
                             "the persist opened no writer")
        self.assertEqual(self.service._prepared_writer["table"][2][0],
                         self.state_cached)
        self.assertGreater(self.service._prepared_writer["table"][2][2], 0)
        self.assertIn(2, self.service._prepared_coverage)
        # The pass reaching the same layer copies the cached bytes
        # instead of skipping it (the worker's branch).
        self.service._full_cache.set(2, encoded, len(encoded))
        self._pump()
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(all(entry[0] == self.state_cached for entry in loaded["table"]),
                        "a prepared layer published as a hole")

    def test_the_pass_fraction_counts_stores_not_residency(self):
        # : a 1,000-layer print with a bounded
        # RAM tier must report the store's coverage, not the cache's
        # residency (a 64-entry cache must not cap the band at 6%).
        self._view(layers=1000)
        self.service._prepared_open(self.files.identity)
        for layer in range(900):
            self.service._prepared_coverage.add(layer)
        self.assertEqual(self.service.plate_pass_fraction(), 0.9)
        for layer in range(64):
            self.service._full_cache.set(layer, b"x" * 1000, 1000)
        self.assertEqual(self.service.plate_pass_fraction(), 0.9,
                         "the fraction followed the RAM tier's residency")

    def test_the_fraction_reaches_100_with_uncacheable_layers(self):
        # The coverage truth: a layer the codec refused is as
        # RESOLVED as one it held — the fraction must reach 100%
        # once the pass has given every layer its attempt.
        self._view(3)
        module = self.qt.load("GCodeIndexService")
        real_encode = module._encode_layer
        calls = []

        def refusing(payload):
            # The synthetic index's geometry carries no layer marker:
            # the pass walks 0, 1, 2 in order, so the SECOND encode
            # is layer 1's.
            calls.append(payload)
            if len(calls) == 2:
                raise ValueError("refused")
            return real_encode(payload)
        with patch.object(module, "_encode_layer", refusing):
            self.service._prepared_open(self.files.identity)
            self._pump()
        self.assertEqual(self.service.plate_pass_fraction(), 1.0,
                         "the refused layer capped the fraction")
        loaded = self.store.load_table("print-key")
        self.assertEqual(loaded["table"][1][0], 2,
                         "the refusal did not publish as UNCACHEABLE")
        self.assertTrue(loaded["complete"])

    def test_the_reopen_never_retries_an_uncacheable_layer(self):
        # An UNCACHEABLE layer rides the reopen AS-IS: the fast path
        # stands the pass down, the coverage counts it, and no
        # writer opens to re-walk the refusal.
        writer = self.store.open_for_write("print-key", 3)
        for layer in (0, 2):
            self.store.append(writer, layer, self._payload(layer))
        self.store.append_uncacheable(writer, 1)
        self.store.finish_write(writer)
        self._view(3)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved,
                        "the complete table (refusal included) took the repair path")
        self.assertTrue(self.service._prepared_complete)
        self.assertIsNone(self.service._prepared_writer)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.assertIn(1, self.service._prepared_coverage,
                      "the uncacheable layer left the coverage")
        self.assertFalse(self.service._prepared_served(1),
                         "an uncacheable layer read as served")
        self.service._advance()
        self.assertEqual(self.service._busy, "",
                         "the reopen re-walked the uncacheable layer")

    def test_noncompact_hydrated_current_is_presented_first_without_a_file_lease(self):
        # The live regression: non-compact indexes report every layer as
        # hydrated immediately, while the decoded presentation cache is
        # initially empty. CURRENT must still be prepared, and it must
        # land before either ghost without reacquiring the G-code.
        index = self._view(5)
        index.followed_layer = 2
        self.service._last_save_at = time.monotonic()  # keep index-save out of this ordering test
        self.service._prepared_open(self.files.identity)
        requests = []
        self.files.lease = lambda: None
        self.files.request_file = lambda: requests.append(set(self.service._decoded_lru))

        module = self.qt.load("GCodeIndexService")
        real_prepare = module._prepare_layer
        prepared = []

        def recording_prepare(index_arg, layer):
            prepared.append(layer)
            return real_prepare(index_arg, layer)

        with patch.object(module, "_prepare_layer", recording_prepare):
            self.service.request_hydration(2)
            for _ in range(200):
                self.qt.events(5)
                if 2 in self.service._decoded_lru:
                    break

        self.assertIn(2, self.service._decoded_lru,
                      "the hydrated live current never became presentation-ready")
        self.assertEqual(prepared[0], 2,
                         "a ghost was prepared before the visible current")
        self.assertEqual(requests, [],
                         "hydrated index arrays incorrectly requested the raw G-code")
        self.assertEqual(self.service._presentation_source(2), "decoded")

    def test_a_prepared_window_seeks_without_the_file_lease(self):
        # The prepared store serves the demanded window: the seek
        # must never wait on the raw G-code lease — the lease exists
        # only for the hydrate-from-file fallback.
        self._view(3)
        writer = self.store.open_for_write("print-key", 3)
        for layer in range(3):
            self.store.append(writer, layer, self._payload(layer))
        self.store.finish_write(writer)
        self.service._prepared_open(self.files.identity)
        requests = []

        def request():
            # Record the LRU state at request time: the seek itself
            # must be DONE before any file request (the pass's later
            # request is the file's legitimate user).
            requests.append(set(self.service._decoded_lru))
        self.files.request_file = request
        self.files.lease = lambda: None  # the G-code file is ABSENT
        # The manual seek demands its window (the live demand path
        # fires for the manual window regardless of hydration).
        self.service.set_manual_anchor(1)
        for _ in range(200):
            if all(layer in self.service._decoded_lru for layer in (0, 1, 2)):
                break
            self.service._advance()
            self.qt.events(5)
        self.assertEqual(requests, [],
                         "prepared/hydrated presentation work requested the raw G-code")
        for layer in range(3):
            self.assertIn(layer, self.service._decoded_lru,
                          "layer %d never decoded from the store" % layer)

    def test_the_saved_latch_autonomously_recovers_after_one_failed_publish(self):
        # A failed publish must NOT read as saved, and recovery must not
        # depend on a later user demand or _prepared_persist call.
        self._view(3)
        self.service._prepared_open(self.files.identity)
        self.service._prepared_persist(0, self._payload(0))
        self.service._full_next = len(self.service._view.ranges)
        self.service._prepared_saved = False
        from plugins.PreparedStore import PreparedCache
        real_finish = PreparedCache.finish_write
        attempts = []

        def fail_once(store, writer):
            attempts.append(writer["identity"])
            if len(attempts) == 1:
                store.abort_write(writer)
                return None
            return real_finish(store, writer)

        with patch.object(PreparedCache, "finish_write", fail_once):
            for _ in range(400):
                self.service._advance()
                self.qt.events(5)
                if self.service._prepared_saved and not self.service._busy:
                    break

        self.assertGreaterEqual(len(attempts), 2,
                                "the failed publish never retried autonomously")
        self.assertTrue(self.service._prepared_saved,
                        "the autonomous retry never latched")
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["complete"])

    def test_a_foreground_seek_interrupts_the_dense_layer_already_in_progress(self):
        # Between-layer checks are insufficient: the request deliberately
        # arrives AFTER one speculative layer has entered preparation.
        # The worker-visible callback must interrupt that same layer and
        # let CURRENT commit before background work resumes.
        index = make_index(layers=40, motions=20000)
        index.followed_layer = 0
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        module = self.qt.load("GCodeIndexService")
        entered = threading.Event()
        interrupted = threading.Event()
        real_prepare = module._prepare_layer

        def controlled_prepare(index_arg, layer, should_yield=None):
            if should_yield is not None and not interrupted.is_set():
                entered.set()
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if should_yield():
                        interrupted.set()
                        raise module.PreparationYield()
                    time.sleep(0.001)
            return real_prepare(index_arg, layer)

        with patch.object(module, "_prepare_layer", controlled_prepare):
            self.service._advance()
            self.assertEqual(self.service._busy, "fullprep")
            self.assertTrue(entered.wait(1.0),
                            "the speculative layer never entered preparation")

            started = time.monotonic()
            self.service.set_manual_anchor(30)
            for _ in range(400):
                self.qt.events(2)
                if 30 in self.service._decoded_lru:
                    break
            elapsed = (time.monotonic() - started) * 1000.0

        self.assertTrue(interrupted.is_set(),
                        "the in-progress speculative layer never yielded")
        self.assertIn(30, self.service._decoded_lru,
                      "foreground CURRENT did not commit after the yield")
        self.assertLess(elapsed, 500.0,
                        "foreground CURRENT waited behind the speculative layer")

    def test_the_pass_batch_yields_to_a_demand(self):
        # A seek mid-pass cuts in: the single worker releases the
        # batch (the yield check between layers), the demand's task
        # runs next, and the pass resumes behind it — the manual
        # window's decoded layers prove the demand committed while
        # the pass was still walking. The dense index stretches the
        # pass across several batches so the seek lands mid-walk.
        index = make_index(layers=60, motions=20000)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        self.service._advance()
        self.assertEqual(self.service._busy, "fullprep",
                         "the pass never submitted")
        self.service.set_manual_anchor(30)
        full_at_demand = 60
        for _ in range(400):
            self.service._advance()
            self.qt.events(5)
            if {29, 30, 31} <= set(self.service._decoded_lru):
                full_at_demand = self.service._full_next
                break
        self.assertLess(full_at_demand, 60,
                        "the pass finished before the demand cut in")

    def test_a_rebind_aborts_the_old_print_writer(self):
        # : bind() must close and delete the
        # previous print's unfinished temp writer — never leave a
        # handle or a .tmp file behind.
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._prepared_persist(0, self._payload(0))
        temp = self.service._prepared_writer["temp"]
        self.assertTrue(os.path.exists(temp))
        self.service.bind(("other.gcode", 100, 2))
        self.assertIsNone(self.service._prepared_writer)
        self.assertFalse(os.path.exists(temp),
                         "the rebind left the old print's temp writer")
        leftovers = [name for name in os.listdir(self._dir.name) if ".tmp-" in name]
        self.assertEqual(leftovers, [])

    def test_the_byte_budgets_bind_the_ram_tiers(self):
        # : the packed tier is pure bytes,
        # the decoded tier holds its slot floor under pressure, and a
        # read refreshes recency.
        module = self.qt.load("GCodeIndexService")
        packed = module._ByteBoundedLru(max_bytes=200)
        packed.set(0, b"x" * 100, 100)
        packed.set(1, b"y" * 50, 50)
        self.assertIsNotNone(packed.get(0))  # the read refreshes recency
        packed.set(2, b"z" * 70, 70)  # 220 > 200: the least recent (1) goes
        self.assertIn(0, packed)
        self.assertNotIn(1, packed)
        decoded = module._ByteBoundedLru(max_bytes=100, min_entries=2)
        decoded.set("a", object(), 90)
        decoded.set("b", object(), 90)
        self.assertEqual(len(decoded), 2, "the floor evicted under pressure")
        decoded.set("c", object(), 90)
        self.assertEqual(len(decoded), 2)
        self.assertNotIn("a", decoded)
        self.assertIn("b", decoded)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class DecodedBudgetTests(unittest.TestCase):
    """The decoded tier's pin accounting: a render wrapper's
    payload stays charged against the budget after the LRU evicts
    it, and the combined bound yields the LRU to the pins."""

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Files(QObject):
            changed = pyqtSignal()

        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        module = self.qt.load("GCodeIndexService")
        self.module = module
        self.service = module.GCodeIndexService(Files(), object())
        self.addCleanup(self.service.close)
        self.service.bind(("part.gcode", 100, 1))
        # The real 128 MB bound is unreachable in a unit test: the
        # tests shrink it and drive the same trim paths.
        self.service._decoded_lru.max_bytes = 200

    def test_a_pin_keeps_evicted_bytes_charged(self):
        # The mirror seeds the way the commit does in production:
        # every charged layer's size stays known past its eviction.
        def charge(layer, size):
            self.service._decoded_lru.set(layer, object(), size)
            self.service._decoded_sizes[layer] = size

        lru = self.service._decoded_lru
        charge(1, 100)
        self.service.pin_decoded(1)
        charge(2, 100)
        charge(3, 100)  # 300 > 200: the LRU evicts 1 on its own
        self.assertNotIn(1, lru)
        self.assertEqual(self.service.pinned_decoded_bytes(), 100,
                         "the evicted pin's bytes were uncharged")
        self.service._reconcile_decoded()
        self.assertEqual(self.service.decoded_resident_bytes(), 200,
                         "the combined bound did not yield to the pin")
        self.service.unpin_decoded(1)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)
        self.assertEqual(self.service.decoded_resident_bytes(), 100,
                         "the unpin never released the charge")
        # An unknown layer pins nothing.
        self.service.pin_decoded(99)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)

    def test_an_evicted_memo_payload_still_pins(self):
        # The frozen window's memoised payload can outlive its LRU
        # entry: the pin must charge it from the retained size, not
        # from the LRU's live table.
        self.service._decoded_sizes[9] = 700
        self.service._decoded_lru.set(9, object(), 700)
        self.service._decoded_lru.clear()  # the LRU dropped it
        self.service.pin_decoded(9)
        self.assertEqual(self.service.pinned_decoded_bytes(), 700,
                         "the memoised payload's pin never charged")
        self.service.unpin_decoded(9)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)

    def test_the_service_floor_is_one_entry(self):
        # The windows' protection moved to the pins: the floor keeps
        # only the just-committed layer alive under a pathological
        # byte bound.
        self.assertEqual(self.service._decoded_lru.min_entries, 1)
        lru = self.service._decoded_lru
        lru.set(7, object(), 100)
        lru.max_bytes = 1
        lru.set(8, object(), 100)
        self.assertEqual(len(lru), 1, "the one-entry floor did not hold")

    def test_the_pin_reads_the_charged_size_without_rewalking(self):
        # The worker measured the payload once; the pin must reuse
        # that size (O(1)), never re-walk the geometry on the UI
        # thread — the exact bytes must survive the eviction through
        # the mirror.
        payload = {"classes": {"FILL": [
            [[i * 0.5 % 240.0, 2.0, float(i)] for i in range(20000)]]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 20000}
        start = time.monotonic()
        size = self.module._deep_size(payload)
        walked = time.monotonic() - start
        print("deep_size: %d bytes walked in %.1f ms" % (size, walked * 1000.0))
        lru = self.service._decoded_lru
        lru.set(4, payload, size)
        self.service._decoded_sizes[4] = size
        self.service.pin_decoded(4)
        lru.set(5, object(), 120)
        lru.set(6, object(), 120)
        self.assertNotIn(4, lru)
        self.assertEqual(self.service.pinned_decoded_bytes(), size,
                         "the pin did not retain the worker's measured size")
        self.service.unpin_decoded(4)
        self.assertEqual(self.service.pinned_decoded_bytes(), 0)

    def test_the_decoded_tier_plateaus_under_churn(self):
        # Seek-style churn must not climb: fresh payloads per cycle,
        # the byte bound evicting under pressure, and the process
        # RSS within a slack of the early cycle (a leak would climb
        # past it — the live-print confirmation rides the next pass).
        import gc

        def rss_kb():
            with open("/proc/self/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
            return 0

        lru = self.service._decoded_lru
        lru.max_bytes = 8 * 1024 * 1024

        def cycle_payloads():
            for _layer in range(12):
                yield {"classes": {"FILL": [
                    [[i * 0.5 % 240.0, 2.0, float(i)] for i in range(8000)]]},
                    "travels": [], "travelStarts": [], "travelEnds": [],
                    "motions": 8000}

        for cycle in range(6):
            for layer, payload in enumerate(cycle_payloads()):
                lru.set(cycle * 100 + layer, payload,
                        self.module._deep_size(payload))
            lru.clear()
            gc.collect()
            if cycle == 1:
                early = rss_kb()
        late = rss_kb()
        print("decoded-tier RSS: early %d KB, late %d KB" % (early, late))
        self.assertLess(late, early + 30000,
                        "the decoded tier's RSS climbed across the cycles")
