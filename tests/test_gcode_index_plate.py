"""The per-motion feature columns, and the demand that fills them.

Two surfaces share this file because they are one feature seen from both
ends. The index derives, for every motion, the slicer's feature type (the
plate payload's colours) and whether the motion is extruding (its travel
glyphs), and the index service's hydration window is what makes those
columns exist for the layers the live print sits on. That window was the
review's F2 finding: the request stood down whenever the current layer was
already hydrated, so the layer behind the print never filled and the face
degraded to an empty ghost.

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
        self.assertEqual(index.motion_count(0), 0)
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
        # The frozen layer and the neighbour below it hold; the layer
        # just hydrated sits in NEITHER window and goes straight out.
        self.assertEqual(index.hydrated_layers, {4, 5})
        self.assertEqual(index.motion_count(5), 1)
        # The control: with no frozen anchor the same walk throws
        # everything away as it goes — the detached face's layer would
        # be evicted by the print's own advance.
        control = build_index_from_file(path, compact=True)
        for layer in (5, 4, 3, 2):
            self.assertTrue(hydrate_layer_from_file(control, path, layer, keep_anchor=0))
        self.assertEqual(control.hydrated_layers, set())


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

    def test_a_request_for_a_hydrated_layer_still_asks_for_its_neighbours(self):
        self._bind(hydrated=(2,))
        self.service.request_hydration(2)
        # The review repro: the current layer was already hydrated, so the
        # request did nothing and the ghost never filled.
        self.assertEqual(self.service._hydrate, {1, 3})

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
        self.assertEqual(self.service._hydrate, {2, 3})

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

    def test_a_request_outside_the_followed_window_is_not_queued(self):
        index = self._bind()
        index.followed_layer = 0
        self.service.request_hydration(4)
        self.assertEqual(self.service._hydrate, set())
        index.followed_layer = None
        self.service.request_hydration(4)
        self.assertEqual(self.service._hydrate, {3, 4})


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
