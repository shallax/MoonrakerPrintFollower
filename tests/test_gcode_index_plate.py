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

from array import array
import gzip
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from itertools import pairwise
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


_LINES_PER_PASS = 12


def _repeated_layer_gcode(passes, drift=0.0, lines=_LINES_PER_PASS, length=100.0,
                          dy=0.4):
    """One layer whose serpentine toolpath is drawn *passes* times.

    With ``drift`` 0 every XY is visited once per pass — the repeated
    infill and retraced skin the live report came from — so the
    toolhead's own coordinate cannot say which pass it is on. A small
    ``drift`` slides each pass sideways, which is what a nozzle that
    stops exactly on a stroke is then able to tell apart.
    """
    out = ["M82", "G90", "G28", "G92 E0", ";LAYER:0", ";TYPE:INFILL", "G1 Z0.200"]
    extruded = 0.0
    for printed in range(passes):
        for line in range(lines):
            y = line * dy + printed * drift
            first, last = (0.0, length) if line % 2 == 0 else (length, 0.0)
            extruded += 0.5
            out.append("G1 X%.3f Y%.3f E%.3f" % (first, y, extruded))
            extruded += 0.5
            out.append("G1 X%.3f Y%.3f E%.3f" % (last, y, extruded))
    return ("\n".join(out) + "\n").encode("ascii")


def _nozzle_at(index, layer, motion):
    """Where the toolhead physically is with *motion* (fractional) of the
    layer's motion chain behind it: the point interpolated along that
    motion, from the layer's opening position."""
    xs, ys, zs = index.motion_x[layer], index.motion_y[layer], index.motion_z[layer]
    whole = max(0, min(int(math.floor(motion)), len(xs) - 1))
    along = motion - whole
    start = index.layer_start_positions[layer] if whole == 0 \
        else (xs[whole - 1], ys[whole - 1], zs[whole - 1])
    end = (xs[whole], ys[whole], zs[whole])
    return tuple(a + along * (b - a) for a, b in zip(start, end, strict=True))


class _HeldClock:
    """A monotonic() the test moves by hand. The batch budget is
    wall-clock, and real seconds are the one input a shared runner
    cannot hold still — serving the service's own clock from here
    makes a timing contract exact instead of load-dependent."""

    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def spend(self, seconds):
        self.now += seconds


# The passive-yield pins' numbers: the production gate hands the
# interpreter back every 6 ms, and the heartbeat asks every 10 ms. A
# gap past the bound means the worker stopped yielding, and the UI
# thread lost the interpreter for several timer periods.
_YIELD_MAX_GAP_S = 0.05
_HEARTBEAT_INTERVAL_MS = 10


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


class MotionLineFastPathTests(unittest.TestCase):
    """The fast motion front (the seek profile: the three per-line
    regexes cost most of a raw hydrate's second). The front must claim
    the dominant slicer shape, and EVERY claim must agree with the
    regex front — a disagreement means the fast path reinterpreted a
    line the regexes would have read differently."""

    # The adversarial battery: shapes the regexes accept that the fast
    # path must either read identically or leave alone (None).
    BATTERY = [
        b"G1 X5 Y10 E0.2", b"G0 X5 Y0", b"G2 X5 Y0 I2 J0",
        b"G3 X5 Y0 I2 J0 R5", b"G1 X5. Y10", b"G1 X.5 Y10",
        b"G1 X-1.5 Y+2 E1e3", b"G1 X-1e-3", b"G1 E-3",
        b"G1 F600 X5", b"G1 F600", b"G1 I2 J0", b"G1",
        b"G1 ", b"G1;comment", b"G1 X5 ;comment", b"G1 X5;c",
        b"G1\tX5\tY10", b"G1  X5  Y10", b"G1 X5 Y10 E0.2 ;x",
        b"G1 x5", b"g1 x5", b"G1 X 5 Y10", b"G1 X5Y10",
        b"G1 Xe3", b"G1 X5_0", b"G1 X0x1A", b"G1 X5, Y10",
        b"G1 Xinf", b"G1 Xnan", b"G1 X5 E", b"G1 X", b"G1 S0 X5",
        b"G01 X5", b"G10 X5", b"G12 X5", b"G4 P100", b"G92 X5",
        b"M82", b"M104 S200", b"T0", b"G21", b"G20 X5",
        b"  G1 X5", b"N12 G1 X5", b"N12G1 X5", b"n12g1 x5",
        b";TYPE:WALL-OUTER", b";comment", b"", b"   ", b"N",
        b"N12", b"G", b"GX5", b"GG1 X5", b"G1X5", b"G1E0.2X5",
        b"G1 X5 Y10 E0.2 F600 Z1.5",
    ]

    def test_the_dominant_shape_takes_the_fast_path(self):
        # The structural perf guard: if the pre-check ever stops
        # claiming the dominant shape, the hydrate silently falls back
        # to the regex front and the seek win regresses.
        command, axes = gcode_index._fast_motion_line(b"G1 X5.5 Y10 E0.2")
        self.assertEqual(command, b"G1")
        self.assertEqual(axes, {"X": 5.5, "Y": 10.0, "E": 0.2})
        for line in (b"G0 X5 Y0", b"G2 X5 Y0 I2 J0", b"G3 X5 Y0 I2 J0 R5"):
            self.assertIsNotNone(gcode_index._fast_motion_line(line),
                                 "%r did not take the fast path" % line)

    def test_every_claim_agrees_with_the_regex_front(self):
        for line in self.BATTERY:
            code = line.split(b";", 1)[0]
            fast = gcode_index._fast_motion_line(code)
            if fast is None:
                continue  # the fallback is always legal
            match = gcode_index._COMMAND.search(code)
            command = match.group(1).upper() if match else b""
            axes = gcode_index._parse_axes(code)
            self.assertEqual(fast, (command, axes), "%r" % line)
            self.assertIsNotNone(gcode_index._MOTION.search(line),
                                 "%r claimed a motion the motion regex refuses" % line)

    def test_a_hydrated_layer_matches_the_full_scan_through_the_fast_path(self):
        # A layer mixing dominant fast-path lines and fallback shapes
        # hydrates into the same polylines the full scan derives.
        from tests.test_plate_progress import layer_polylines
        data = (b"M82\nG90\n;LAYER:0\n;TYPE:WALL-OUTER\n"
                + b"G2 X8 Y8 I1 J0\n"     # an arc FIRST: any code-part leak
                + b"".join(b"G1 X%.3f Y%.3f E%.4f\n" % (float(i), float(i % 50),
                                                        0.05 + i * 0.001)
                           for i in range(40))
                + b"G1 X 5 Y 10 E0.2\n"   # the spaced axis — the regex path
                + b"g1 x5 y10 e0.2\n"     # lowercase — the regex path
                + b"G1 X5. Y10\n"         # the dot-trailing number
                + b"G1 E-0.3\n")          # a retraction
        path = _write_gcode(data)
        self.addCleanup(os.remove, path)
        full = build_index_from_file(path, compact=False)
        compact = build_index_from_file(path, compact=True)
        self.assertTrue(hydrate_layer_from_file(compact, path, 0, keep_anchor=0))
        self.assertEqual(compact.motion_count(0), full.motion_count(0))
        self.assertEqual(layer_polylines(compact, 0), layer_polylines(full, 0),
                         "the fast-path hydrate diverged from the full scan")


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
        # The production cache's own path (the per-print subdirectory
        # layout) — the raw writes must land where the loader reads.
        return self.cache._path(remote)

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
                # The per-print subdirectory may exist (the path's makedirs);
        # no blob file may have been written.
        written = [name for _root, _dirs, names in os.walk(self.directory)
                   for name in names]
        self.assertEqual(written, [])

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
        # The per-print subdirectory may exist (the path's makedirs);
        # no blob file may have been written.
        written = [name for _root, _dirs, names in os.walk(self.directory)
                   for name in names]
        self.assertEqual(written, [])


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
        # The latch outranks every presentation source. In particular an
        # already-hydrated layer must not bypass it and spin forever after
        # a decode/prepare failure.
        self._bind(hydrated=(2,))
        self.service._failed_hydrate.add(2)
        self.assertEqual(self.service._presentation_source(2), "failed")
        self.service.request_hydration(2)
        # The other two layers remain useful and are still demanded.
        self.assertEqual(self.service._hydrate, {1, 3})

    def test_a_refused_layer_names_itself_in_the_payload(self):
        # The silent latch: the layer is refused, the face shows
        # "Loading layer…" for as long as it is on screen and nothing
        # says why. The payload carries the verdict so the face can
        # name it.
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service._failed_hydrate.add(5)
        payload = self.service.plate_progress(5, file_position=None)
        self.assertIsNone(payload["layers"]["current"])
        self.assertEqual(payload["refusal"], "failed",
                         "the refused layer must name itself")
        # A neighbour's refusal is not this anchor's: the face names
        # the layer it is showing.
        self.assertEqual(self.service.plate_progress(4, file_position=None)["refusal"], "")

    def test_an_anchor_outside_the_file_names_itself_too(self):
        # The other no-arrival state: a shrunken file leaves the frozen
        # anchor past the last layer. Nothing will ever arrive for it
        # either, so the face must not promise a load.
        self._bind(layers=8)
        self.service._manual_anchor = 11
        payload = self.service.plate_progress(11, file_position=None)
        self.assertIsNone(payload["layers"]["current"])
        self.assertEqual(payload["refusal"], "outside")
        # A healthy layer of the same file claims nothing.
        self.assertEqual(self.service.plate_progress(3, file_position=None)["refusal"], "")

    def test_an_explicit_seek_clears_the_refusal_latch(self):
        # The trap: the latch outranks every demand, so seeking away
        # and back to a refused layer was refused again with nothing
        # said — the user had no way to ask. A NEW anchor is a fresh
        # attempt; the poll's re-assert of the SAME anchor is not
        # (that is the per-poll re-read the latch exists to stop).
        self._bind(layers=8)
        self.service.set_manual_anchor(5)
        self.service._failed_hydrate.add(5)
        self.service._hydrate.clear()
        self.service.set_manual_anchor(5)
        self.assertIn(5, self.service._failed_hydrate,
                      "the poll's own re-assert must not clear the latch")
        self.assertNotIn(5, self.service._hydrate)
        self.service.set_manual_anchor(1)
        self.service.set_manual_anchor(5)
        self.assertNotIn(5, self.service._failed_hydrate,
                         "an explicit re-seek is a fresh attempt")
        self.assertIn(5, self.service._hydrate,
                      "the re-seek's layer was not demanded again")

    def test_the_polls_re_assert_keeps_the_frozen_demand_standing(self):
        # The coordinator re-asserts the frozen anchor every poll so a
        # demand dropped while the worker was busy is raised again
        # (its own comment says the request has to stand). The
        # idempotency guard swallowed the whole call, so a manual
        # window that lost its demand never came back.
        self._bind(layers=8)
        index = self.service._view._index
        index.followed_layer = 1
        self.service.set_manual_anchor(5)
        self.service._hydrate.clear()
        self.service.set_manual_anchor(5)
        self.assertEqual(self.service._hydrate, {4, 5, 6},
                         "the standing demand was not re-raised")
        # A ready layer is never re-demanded: decoded is readiness, and
        # re-raising it would submit the window every poll.
        self.service._decoded_lru.set(5, {}, 0)
        self.service._hydrate.clear()
        self.service.set_manual_anchor(5)
        self.assertEqual(self.service._hydrate, {4, 6})

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

    def test_a_pause_park_on_old_geometry_cannot_erase_the_boundary(self):
        offsets = self._bind()
        self.assertEqual(self.service.plate_progress(0, offsets[19], (12.0, 0.0, 0.2))["split"], 13)
        for _ in range(8):
            self.assertEqual(self.service.plate_progress(
                0, offsets[19], (1.0, 0.0, 0.2), paused=True)["split"], 13)
        # Resume can first report the macro's parked head before the
        # restored physical position lands. Those are not print moves.
        for _ in range(5):
            self.assertEqual(self.service.plate_progress(
                0, offsets[19], (1.0, 0.0, 0.2))["split"], 13)
        self.assertEqual(self.service.plate_progress(0, offsets[19], (13.0, 0.0, 0.2))["split"], 14)

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
        # starts its own, unfloored by layer 1's paint. The poll that
        # ENTERS a layer reads zero — nothing has printed on it yet —
        # and the poll after it refines from the boundary that entry
        # set, so the reset costs one poll and never inherits the
        # previous layer's paint.
        self.assertEqual(
            self.service.plate_progress(2, offsets[19], (2.0, 0.0, 0.2))["split"], 0)
        self.assertEqual(
            self.service.plate_progress(2, offsets[19], (2.0, 0.0, 0.2))["split"], 3)

    def test_entering_a_layer_never_publishes_a_future_match(self):
        # The live report: the fill jumped at the START of a new layer and
        # rewound once the truth caught up. The nozzle has just arrived and
        # has printed nothing on the layer, while the parser's read position
        # is already past it — so the entry poll's search window spans the
        # whole layer and a coincidental XY match anywhere in it won. The
        # head over x=5 reaches layer 1's own geometry, and reading that as
        # the boundary is the jump.
        offsets = self._bind(layers=3)
        self.assertEqual(
            self.service.plate_progress(0, offsets[19], (5.0, 0.0, 0.2))["split"], 6)
        entry = self.service.plate_progress(1, offsets[19], (5.0, 0.0, 0.2))
        self.assertEqual(entry["split"], 0,
                         "entering a layer published a match from its future")
        # And the entry is an INITIALISATION, not a cap: the next poll
        # refines to the head's real place with no lag behind it.
        self.assertEqual(
            self.service.plate_progress(1, offsets[19], (2.0, 0.0, 0.2))["split"], 3)

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

    def test_parser_layer_advance_waits_for_physical_height_in_both_geometry_paths(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                self.service._split_tracker.reset()
                self._bind(layers=3)
                index = self.service._view._index
                index.layer_start_positions = [(0.0, 0.0, 0.0),
                                               (0.0, 0.0, 0.2),
                                               (0.0, 0.0, 0.4)]
                index.motion_z = [array("f", [0.2] * 20),
                                  array("f", [0.4] * 20),
                                  array("f", [0.6] * 20)]
                positions = [int(offsets[-1]) for offsets in index.motion_offsets]
                if compact:
                    index.layer_motion_counts = [20] * 3
                    index.motion_offsets = [array("Q") for _ in range(3)]
                    index.motion_z = [array("f") for _ in range(3)]
                self.assertGreater(self.service.plate_progress(
                    0, positions[0], (5.0, 0.0, 0.2))["split"], 0)
                for _ in range(5):
                    self.assertEqual(self.service.plate_progress(
                        1, positions[1], (5.0, 0.0, 0.2))["split"], 0,
                        "repeated XY on the previous height seeded the new layer")
                self.assertGreater(self.service.plate_progress(
                    1, positions[1], (5.0, 0.0, 0.4))["split"], 0)

    def test_float_noise_cannot_choose_a_future_repeated_pass(self):
        best = gcode_index.better_candidate(1e-12, 5, float("inf"), None, 4)
        self.assertEqual(gcode_index.better_candidate(0.0, 500, *best, 4)[1], 5)
        reverse = gcode_index.better_candidate(0.0, 500, float("inf"), None, 4)
        self.assertEqual(gcode_index.better_candidate(1e-12, 5, *reverse, 4)[1], 5)

    @staticmethod
    def _row_payload(motions, y=0.0, x0=0.0):
        """A payload in the plate's own shape: one vertex per motion on
        a straight run at height *y*."""
        return {"classes": {"WALL-OUTER": [[[x0 + index, y, float(index)]
                                            for index in range(motions)]]},
                "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": motions}

    def _bind_payload(self, motions=20, count=None):
        """The UNHYDRATED compact layer: the motion count is known, the
        motion arrays are not (they land with the file hydration), so the
        boundary rides the payload geometry the plate already draws."""
        index = make_index(layers=1, motions=motions)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.service._decoded_lru[0] = self._row_payload(motions)
        index.layer_motion_counts = [motions if count is None else count]
        index.motion_offsets = [array("Q")]
        return index

    @staticmethod
    def _run(first, count, y, x0=0.0):
        """One drawn run: *count* vertices at height *y* stepping along x
        from *x0*, the motion index of vertex i being *first* + i — the
        payload's own triple."""
        return [[x0 + index, y, float(first + index)] for index in range(count)]

    def _bind_runs(self, runs, motions):
        """An unhydrated layer whose payload draws *runs*: the arrays are
        empty until the hydration lands, so the payload's geometry is the
        only toolpath the split can be measured against."""
        index = make_index(layers=1, motions=motions)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.service._decoded_lru[0] = {
            "classes": {"INFILL": list(runs)}, "travels": [],
            "travelStarts": [], "travelEnds": [], "motions": motions}
        index.layer_motion_counts = [motions]
        index.motion_offsets = [array("Q")]
        return index

    def test_a_late_floor_reaches_back_to_the_nozzle_after_a_wrong_match(self):
        # An off-path hop whose XY lands on a LATER pass's stroke is a
        # genuine match at distance zero, and the floor moves there while
        # the head never went anywhere. Well short of it now lies the
        # geometry the nozzle is actually on, and the search never looks
        # below the floor: every poll REFUSES. A refusal has to count as
        # an unconfirmed poll — a counter that resets on it can never
        # widen the window that would reach the nozzle, and the fill sits
        # hundreds of motions ahead of the head for the layer's life.
        self._bind_runs([self._run(0, 100, 0.0), self._run(900, 100, 40.0)],
                        motions=4000)
        # 90/100 of the layer's byte range is motion 3600 of 4000: the
        # dispatcher reading far past a nozzle that is 50 motions in.
        self.assertEqual(
            self.service.plate_progress(0, 90, (50.0, 0.0, 0.2))["split"], 50)
        for _hop in range(2):
            self.assertEqual(
                self.service.plate_progress(0, 90, (50.0, 40.0, 0.2))["split"], 950,
                "the hop was not matched where it landed")
        # Back on its own stroke, every poll now REFUSES: the window
        # hangs off the wrong floor, so the geometry under the nozzle is
        # outside it and the search finds nothing at all rather than a
        # below-floor match. The displayed boundary holds the wrong pass
        # until the expansion reaches the head, then steps back onto it.
        splits = [self.service.plate_progress(0, 90, (50.5 + poll, 0.0, 0.2))["split"]
                  for poll in range(5)]
        self.assertEqual(splits[0], 950, "the fill moved before the evidence landed")
        self.assertLess(splits[-1], 55, "three physical matches never corrected the floor")
        self.assertGreater(splits[-1], 49, "the fill lost the head's own stroke")
        self.assertEqual(self.service._split_tracker.floor, splits[-1])
        # ...and tracks the nozzle from there, on the pass it is on.
        self.assertEqual(
            self.service.plate_progress(0, 90, (60.0, 0.0, 0.2))["split"], 60)

    def test_an_unhydrated_layer_holds_the_nozzles_own_pass(self):
        # The same repeated toolpath with the arrays still empty: the
        # payload's geometry is all the search has, and the pass the
        # nozzle is on is the one the tie resolves to — the vertex order
        # the painter happened to emit is not evidence of anything. Once
        # the boundary has cleared the first pass, the fill follows the
        # nozzle into the second instead of painting the first again.
        self._bind_runs([self._run(0, 200, 0.0), self._run(200, 200, 0.0)],
                        motions=400)
        self.assertEqual(
            self.service.plate_progress(0, 90, (199.0, 0.0, 0.2))["split"], 199)
        painted = 199
        for x, expected in ((5.0, 205), (6.0, 206), (10.0, 210), (11.0, 211)):
            split = self.service.plate_progress(0, 90, (x, 0.0, 0.2))["split"]
            self.assertEqual(split, expected,
                             "the fill resolved to the pass already printed")
            self.assertGreaterEqual(split, painted)
            painted = split

    def test_the_unhydrated_layer_splits_on_its_payload_geometry(self):
        # The arrays are empty until the hydration lands, and the byte
        # fraction that stands in for them is NOT proportional to motion
        # — the live report's drifting fill. The payload's geometry is
        # what the plate displays, so the search runs over THAT.
        self._bind_payload()
        payload = self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))
        self.assertEqual(payload["split"], 5, "the byte fraction won over the geometry")
        self.assertEqual(payload["method"], "motion index")
        self.assertIsNotNone(payload["layers"]["current"])

    def test_a_payload_boundary_holds_when_the_head_leaves_the_path(self):
        self._bind_payload()
        self.assertEqual(self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))["split"], 5)
        # A park or a probe: the search finds nothing near, so the
        # boundary already painted is what the fill keeps.
        for _poll in range(2):
            self.assertEqual(
                self.service.plate_progress(0, 50, (140.0, 140.0, 10.0))["split"], 5)

    def test_the_first_poll_of_an_unhydrated_layer_paints_nothing_off_path(self):
        # The byte fraction can read ahead of the nozzle, so it is never
        # the first boundary: with telemetry and no match yet, nothing
        # has printed on the layer and nothing is painted.
        self._bind_payload()
        for _poll in range(2):
            self.assertEqual(
                self.service.plate_progress(0, 50, (140.0, 140.0, 10.0))["split"], 0)

    def test_a_machine_without_live_telemetry_paints_the_payload_coarse(self):
        # No live position anywhere: the coarse is all that machine has,
        # and the floor keeps it monotonic across polls.
        self._bind_payload()
        self.assertEqual(self.service.plate_progress(0, 50)["split"], 10)
        self.assertEqual(self.service.plate_progress(0, 20)["split"], 10,
                         "the parser's earlier position walked the fill back")

    def test_the_overshoot_lock_steps_the_floor_back_to_the_truth(self):
        # The nozzle sitting ON geometry behind an overshot floor is the
        # overshoot-lock's evidence: three polls of the same verdict step
        # the floor back to the truth instead of stalling the fill until
        # the nozzle catches up.
        self._bind_payload()
        self.assertEqual(self.service.plate_progress(0, 50, (15.0, 0.0, 0.2))["split"], 15)
        for _poll in range(2):
            self.assertEqual(self.service.plate_progress(0, 50, (6.0, 0.0, 0.2))["split"], 15)
            self.assertEqual(self.service._split_tracker.floor, 15,
                             "the floor stepped back before the third below-floor verdict")
        self.assertEqual(self.service.plate_progress(0, 50, (6.0, 0.0, 0.2))["split"], 6)
        self.assertEqual(self.service._split_tracker.floor, 6, "the overshoot lock never released")
        # Resumed: the truth is the floor now, and the fill follows the
        # nozzle forward from it again.
        self.assertEqual(self.service.plate_progress(0, 50, (12.0, 0.0, 0.2))["split"], 12)

    def test_the_observed_advance_widens_the_next_search(self):
        # The ahead side is the advance the last poll actually observed
        # (a fresh floor means the nozzle is a bounded step past it), so
        # repeated geometry cannot win the match and overshoot the fill.
        self._bind_payload(motions=6000)
        self.assertEqual(self.service.plate_progress(0, 50, (1000.0, 0.0, 0.2))["split"], 1000)
        self.assertEqual(self.service._split_tracker.advance_max, 512,
                         "the floor-less first poll measured an advance")
        self.assertEqual(self.service.plate_progress(0, 50, (2900.0, 0.0, 0.2))["split"], 2900)
        self.assertEqual(self.service._split_tracker.advance_max, 1900,
                         "the observed advance never widened the next window")

    def test_a_layer_without_a_motion_count_reads_unavailable(self):
        # Neither arrays nor a count: there is nothing to measure, so the
        # face ghosts rather than colouring a boundary it cannot know.
        self._bind_payload(count=0)
        payload = self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))
        self.assertIsNone(payload["split"])
        self.assertEqual(payload["method"], "unavailable")

    def test_the_split_without_an_index_reads_unavailable(self):
        self._bind_payload()
        self.assertEqual(len(self.service.plate_layers(0)), 3)
        self.service._view = None
        self.assertEqual(self.service.plate_layers(0), {})
        payload = self.service.plate_progress(0, 50, (5.0, 0.0, 0.2))
        self.assertIsNone(payload["split"])
        self.assertEqual(payload, {"layers": {}, "split": None, "method": "unavailable",
                                   "motionTotal": 0, "anchor": 0, "refusal": ""})


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class RepeatedGeometrySplitTests(unittest.TestCase):
    """A layer that visits the same toolpath more than once.

    Every XY the nozzle crosses is a motion of every pass, so the live
    position on its own cannot say which pass the head is on. The
    boundary must land on the stroke the nozzle is PRINTING: a later
    pass paints strokes the head has not reached and locks the fill
    ahead of it, and an earlier one clamps the fill below paint the
    plate has already drawn — the rewind the live report showed.

    The geometry is a real serpentine parsed by the real builder, walked
    by a simulated nozzle: the poll drives ``plate_progress`` exactly as
    the coordinator does, with the dispatcher's read point wherever the
    scenario puts it.
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
        self.job = ("repeated.gcode", 100, 1)
        self.service.bind(self.job)

    def _bind(self, passes=6, drift=0.0):
        """The hydrated layer: the real builder's arrays in the hot
        presentation cache, the plate's own anchor past the live layer's
        digest, exactly as the worker commits them."""
        index = build_index_from_bytes(_repeated_layer_gcode(passes, drift))
        self.service._view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        from plugins.PlateProgress import prepare_layer
        self.service._decoded_lru[0] = prepare_layer(index, 0)
        self.index = index
        self.offsets = list(index.motion_offsets[0])
        self.count = index.motion_count(0)
        return self.count

    def _poll(self, truth, lead, live=None):
        """One observe: the dispatcher reads at *truth* + *lead* motions,
        the nozzle is at *truth* unless the scenario says otherwise."""
        position = self.offsets[max(0, min(self.count - 1, int(truth) + lead))]
        if live is None:
            live = _nozzle_at(self.index, 0, truth)
        return self.service.plate_progress(0, position, live)["split"]

    def test_a_read_burst_does_not_seed_the_floor_above_the_nozzle(self):
        # A chunk read after a stutter puts the dispatcher's read point
        # many hundreds of motions past the nozzle — well beyond the
        # window the search used to keep around it. Only motions the head
        # has not reached are left in that window, and on this layer the
        # nearest of them shares the head's XY: the fill painted the
        # later pass, and because that value was then the floor, every
        # later poll agreed with it and the fill never came back.
        self._bind(passes=60)
        for truth in (10.0, 13.5, 17.0, 20.5, 24.0):
            split = self._poll(truth, lead=1200)
            self.assertLessEqual(
                split, truth + 1, "the fill painted a stroke the head has not reached")
            self.assertGreaterEqual(
                split, truth - 1, "the fill lost the head's own stroke")
            self.assertLessEqual(
                self.service._split_tracker.floor, truth, "the floor locked ahead of the head")

    def test_the_fill_never_walks_back_across_a_repeated_pass(self):
        # The nozzle finishes pass 0 and starts pass 1 on the same
        # coordinates. Resolving that tie to the earlier pass pulls the
        # boundary back under paint the plate already drew, and the
        # overshoot lock then commits the rewind: the fill visibly jumps
        # backwards every few polls, at the print's own pace. Nothing
        # may be repainted and no stroke ahead of the head may be drawn.
        self._bind(passes=6)
        painted = 0
        truth = 18.0
        while truth <= 30.0:
            split = self._poll(truth, lead=40)
            self.assertGreaterEqual(
                split, painted, "the fill walked back at motion %s" % truth)
            self.assertLessEqual(
                split, truth + 1, "the fill painted a stroke the head has not reached")
            painted = split
            truth += 1.5
        self.assertGreaterEqual(painted, 29, "the fill stopped following the head")
        # Adversarial ordering: the status feed hands the same sample
        # twice, then one from before the boundary (a re-applied G-code
        # offset moves the reported position back without the nozzle
        # moving). Repeated geometry ties either way, and no tie may walk
        # the fill back under paint the plate already drew.
        self.assertGreaterEqual(self._poll(30.0, lead=40), painted)
        stale = self._poll(18.0, lead=40)
        self.assertGreaterEqual(stale, painted, "a stale sample rewound the fill")
        self.assertLessEqual(stale, painted + 2 * _LINES_PER_PASS,
                             "a stale sample painted far ahead of the head")

    def test_a_hydration_landing_mid_layer_keeps_the_fill_on_its_own_pass(self):
        # The layer's first polls land before its arrays arrive: the
        # payload's geometry is the only toolpath the search has, so the
        # boundary the payload search accepts is the floor the hydrated
        # search inherits. The delivery is asynchronous — the arrays land
        # whenever the worker gets to them — and it is not a new layer:
        # the fill must not jump, blank or lose the pass it was on.
        index = build_index_from_bytes(_repeated_layer_gcode(passes=6))
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.job, index)
        from plugins.PlateProgress import prepare_layer
        self.service._decoded_lru[0] = prepare_layer(index, 0)
        self.index = index
        self.count = index.motion_count(0)
        self.offsets = list(index.motion_offsets[0])
        hydrated = array(index.motion_offsets[0].typecode,
                         index.motion_offsets[0])
        index.motion_offsets[0] = array("Q")
        for truth in (10.0, 14.0, 18.0):
            split = self._poll(truth, lead=40)
            self.assertLessEqual(split, truth + 1, "the payload search painted ahead")
            self.assertGreaterEqual(split, truth - 1, "the payload search lost the head")
        floor = self.service._split_tracker.floor
        # The hydration lands: the arrays are what the split rides from
        # here, and the floor is the continuity between the two halves.
        index.motion_offsets[0] = hydrated
        for truth in (19.5, 21.0, 22.5):
            position = self.offsets[int(truth) + 40]
            payload = self.service.plate_progress(
                0, position, _nozzle_at(index, 0, truth))
            self.assertIsNotNone(payload["layers"]["current"],
                                 "the hydration blanked the layer")
            split = payload["split"]
            self.assertGreaterEqual(split, floor, "the fill jumped back at the handover")
            self.assertLessEqual(split, truth + 1, "the hydrated search painted ahead")
            self.assertGreaterEqual(split, truth - 1, "the hydrated search lost the head")
            floor = split

    def test_an_off_path_travel_then_resumption_holds_the_nozzles_own_pass(self):
        # A park, a probe, a cross-plate travel: the nozzle is off the
        # extrusion geometry and nothing is printing, so the boundary
        # holds. Resumed, the head is back on its own pass — the stroke a
        # later pass also visits must not claim the fill, and the stroke
        # already passed must not take it back.
        self._bind(passes=60)
        self.assertEqual(self._poll(20.0, lead=1200), 20)
        for _park in range(2):
            self.assertEqual(
                self._poll(20.0, lead=1200, live=(140.0, 140.0, 10.0)), 20,
                "an off-path park moved the boundary")
        for truth in (21.0, 22.0, 23.0):
            split = self._poll(truth, lead=1200)
            self.assertLessEqual(split, truth + 1)
            self.assertGreaterEqual(split, truth - 1)

    def test_a_stale_live_sample_does_not_strand_the_fill(self):
        # The nozzle is reported where it is not — a stale position, a
        # re-applied homing origin — standing on a stroke a later pass
        # owns. That match is genuine, it is the nearest geometry by a
        # wide margin, and the floor moves there hundreds of motions
        # ahead of the head. What must not happen is what the live
        # report showed: the head's own stroke is then outside the
        # search's window, the refinement returns NOTHING poll after
        # poll, and a search that only widens its window on a below-floor
        # MATCH never reaches the geometry the nozzle is standing on. The
        # fill stays stranded ahead of the head for the layer's life.
        # Three reliable samples on the truth step the floor back to it.
        self._bind(passes=60, drift=4.0)
        # Line 3's stroke: motion 8 of pass 0, and the same stroke 32 mm
        # up at motion 8 of pass 8. Lines 0-9 of each pass are unique to
        # it — the drift is two and a half line spacings — so neither
        # sample is a tie.
        self.assertEqual(self._poll(8.0, lead=1200, live=(50.0, 1.2, 0.2)), 8)
        stranded = self._poll(8.0, lead=1200, live=(50.0, 33.2, 0.2))
        self.assertEqual(stranded, 8 * 2 * _LINES_PER_PASS + 8,
                         "the stale sample was not matched where it landed")
        for poll in range(3):
            split = self._poll(8.0, lead=1200, live=(50.0, 1.6 + 0.4 * poll, 0.2))
            if poll < 2:
                self.assertEqual(split, stranded,
                                 "the floor stepped back before the third sample")
        self.assertEqual(split, 14, "the fill stayed stranded on the later pass")
        self.assertEqual(self.service._split_tracker.floor, 14)
        # The head keeps printing: the fill follows it from the truth,
        # unhaunted by the pass the stale sample claimed.
        self.assertEqual(self._poll(8.0, lead=1200, live=(50.0, 2.8, 0.2)), 16)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class PayloadRefinementTests(unittest.TestCase):
    """The live-position refinement over a payload's geometry: the
    unhydrated layer's own bounded search.

    The index arrays are empty until the file hydration lands, so the
    only geometry in hand is the polylines the plate already draws. The
    search seeds on the monotonic floor (the coarse on the layer's first
    poll), contributes only the points inside each window — bisected,
    never walked — and holds rather than paints a future pass when the
    nearest travel is closer than the nearest extrusion."""

    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.service_class = self.qt.load("GCodeIndexService").GCodeIndexService

    def _refine(self, payload, coarse, live, **kwargs):
        return self.service_class._refine_over_payload(payload, coarse, live, **kwargs)

    @staticmethod
    def _row(y, first, count, x0=0.0, step=1.0):
        """One drawn run: *count* vertices stepping *step* along x at
        height *y*, motion index *first* + i — the payload's own triple."""
        return [[x0 + index * step, y, float(first + index)] for index in range(count)]

    @staticmethod
    def _payload(classes, travels=(), motions=0):
        return {"classes": classes, "travels": list(travels),
                "travelStarts": [], "travelEnds": [], "motions": motions}

    def test_a_payload_and_a_live_position_are_both_required(self):
        payload = self._payload({"W": [self._row(0.0, 0, 20)]}, motions=20)
        # No geometry, no telemetry, a position with no Z, or one that is
        # not a number at all: there is nothing to search with.
        self.assertIsNone(self._refine(None, 10, (5.0, 0.0, 0.2)))
        self.assertIsNone(self._refine(payload, 10, None))
        self.assertIsNone(self._refine(payload, 10, (5.0, 0.0)))
        self.assertIsNone(self._refine(payload, 10, (None, 0.0, 0.2)))

    def test_the_first_search_is_wide_around_the_coarse(self):
        # The layer's first poll has no floor, so the window is centred on
        # the parser's coarse position: the lookahead between the
        # dispatcher and the nozzle can be thousands of motions.
        payload = self._payload({"W": [self._row(0.0, 0, 20000)]}, motions=20000)
        self.assertEqual(self._refine(payload, 10000, (5000.0, 0.0, 0.2)), 5000)
        # Outside the window the nearest edge is hundreds of mm away: the
        # refinement refuses rather than report that as the nozzle.
        self.assertIsNone(self._refine(payload, 10000, (1000.0, 0.0, 0.2)))

    def test_the_floor_seeds_the_window_and_the_motion_count_caps_it(self):
        payload = self._payload({"W": [self._row(0.0, 0, 6000)]}, motions=6000)
        # The seed is the FLOOR, not the coarse: the parser sits at 5900
        # while the nozzle is 50 motions behind it. The window is capped
        # by the layer's own motion count, never searched past it.
        self.assertEqual(self._refine(payload, 5900, (5850.4, 0.0, 0.2), floor=5800), 5850)

    def test_the_stall_expansion_reaches_a_pass_the_narrow_windows_miss(self):
        payload = self._payload({"W": [self._row(0.0, 0, 20000)]}, motions=20000)
        # 5,000 motions past the floor: neither the 1x nor the 8x window
        # can see it. Consecutive stalled polls expand the ahead side,
        # and only then does the search reach the pass the nozzle has
        # since moved on to.
        self.assertIsNone(
            self._refine(payload, 100, (5000.0, 0.0, 0.2), floor=0, ahead=512))
        self.assertEqual(
            self._refine(payload, 100, (5000.0, 0.0, 0.2), floor=0, ahead=512, stall=2),
            5000)

    def test_an_isolated_vertex_is_a_candidate_of_its_own(self):
        # A travel split can leave a motion carrying a single vertex: it
        # is that motion's only geometry, so skipping it hides the truth
        # and the search paints a future pass instead.
        self.assertEqual(
            self._refine(self._payload({"V": [[[7.0, 0.0, 12.0]]]}, motions=20),
                         10, (7.0, 0.0, 0.2)), 12)
        # A vertex outside the window is not a candidate — and it must not
        # stop the segment beside it from being searched.
        self.assertEqual(
            self._refine(self._payload({"Far": [[[900.0, 0.0, 900.0]]],
                                        "Near": [[[7.0, 0.0, 12.0]]]}, motions=1000),
                         12, (7.0, 0.0, 0.2), floor=10), 12)

    def test_a_segment_before_the_window_is_never_walked(self):
        # The bisect skips a segment the window has already left behind:
        # its motions are all below the window, so it contributes nothing
        # and the live pass beside it is found instead.
        payload = self._payload({"old": [self._row(0.0, 0, 100)],
                                 "live": [self._row(0.0, 500, 100, x0=500.0)]},
                                motions=600)
        self.assertEqual(self._refine(payload, 550, (550.0, 0.0, 0.2), floor=550), 550)

    def test_a_zero_length_edge_is_measured_at_its_own_endpoint(self):
        # A doubled vertex has no direction to project onto: the point IS
        # the geometry, and the projection would divide by zero.
        payload = self._payload(
            {"W": [[[5.0, 0.0, 10.0], [5.0, 0.0, 11.0], [9.0, 0.0, 12.0]]]}, motions=12)
        self.assertEqual(self._refine(payload, 5, (5.0, 0.0, 0.2)), 11)

    def test_the_near_tie_prefers_the_pass_at_or_above_the_floor(self):
        # Repeated toolpaths put the nozzle's own position on an earlier
        # and a later pass as well. Both runs below are 0.5 mm from the
        # nozzle; the near one carries the earlier motions.
        near = self._row(0.5, 11, 10, x0=-5.0)
        far = self._row(-0.5, 41, 10, x0=-5.0)
        low_first = self._payload({"low": [near], "high": [far]}, motions=60)
        high_first = self._payload({"high": [far], "low": [near]}, motions=60)
        # The seam resolves to the pass AT OR ABOVE the floor: an earlier
        # pass clamps to the floor and stalls the fill for seconds.
        self.assertEqual(self._refine(low_first, 10, (0.0, 0.0, 0.2), floor=30), 46)
        self.assertEqual(self._refine(high_first, 10, (0.0, 0.0, 0.2), floor=30), 46)
        # With no floor the smallest motion wins: the nozzle is on the
        # first of the coincident passes.
        self.assertEqual(self._refine(high_first, 10, (0.0, 0.0, 0.2)), 16)
        # Both below the floor (a whole overshot stretch, not one pass):
        # the closest to the floor wins, and the result is left for the
        # caller's overshoot-lock evidence — never clamped here.
        under = self._row(0.5, 40, 10, x0=-5.0)
        over = self._row(-0.5, 90, 10, x0=-5.0)
        self.assertEqual(
            self._refine(self._payload({"low": [under], "high": [over]}, motions=900),
                         10, (0.0, 0.0, 0.2), floor=100), 95)
        self.assertEqual(
            self._refine(self._payload({"high": [over], "low": [under]}, motions=900),
                         10, (0.0, 0.0, 0.2), floor=100), 95)

    def test_the_travel_gate_holds_the_split_while_the_nozzle_travels(self):
        # Nothing prints during a travel. The only extrusion within reach
        # is a FUTURE pass, and painting it jumps the fill ahead and locks
        # the overshoot into the floor — the nearest travel's tell is what
        # makes the honest answer "hold".
        payload = self._payload({"W": [self._row(2.0, 101, 10, x0=-5.0)]},
                                [self._row(0.05, 40, 10, x0=-5.0)], motions=200)
        self.assertIsNone(self._refine(payload, 100, (0.0, 0.0, 0.2), floor=100))

    def test_a_travel_further_than_the_match_leaves_the_edge_winning(self):
        # The travel's tell is its proximity: a travel a clear margin away
        # says nothing about the nozzle's own pass. The travel channel
        # also carries a lone vertex, a stretch the window has left
        # behind, a doubled vertex and one that runs past the window's
        # far side — none of them is the tell.
        payload = self._payload(
            {"W": [self._row(0.5, 101, 10, x0=-5.0)]},
            [[[0.0, 0.0, 5.0]],
             self._row(0.6, 0, 20),
             [[-5.0, 0.5, 40.0], [-5.0, 0.5, 41.0], [4.0, 0.5, 42.0]],
             self._row(0.5, 600, 200, x0=-5.0)],
            motions=900)
        self.assertEqual(self._refine(payload, 100, (0.0, 0.0, 0.2), floor=100,
                                      ahead=512), 106)

    def test_a_head_off_the_geometry_returns_nothing(self):
        payload = self._payload({"W": [self._row(0.0, 0, 20000)]}, motions=20000)
        # A park, a Z-lift, a probe: no edge is within reach, so the
        # caller holds its floor instead of jumping to the parser.
        self.assertIsNone(self._refine(payload, 10000, (5000.0, 30.0, 0.2)))
        # The tolerance belongs to the caller: a tight one refuses a hit
        # the default would have taken.
        self.assertIsNone(self._refine(payload, 10000, (5000.5, 0.5, 0.2),
                                       max_distance_mm=0.1))


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

    def test_a_walk_without_an_index_or_a_boundary_reads_empty(self):
        # No index, no boundary, no anchor: nothing has been judged, and
        # the empty set is the truth about the edges walked so far.
        self.service._view = None
        self.assertEqual(self.service.plate_visited(0, 5, self.ROWS), frozenset())
        self._bind(b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\n")
        self.assertEqual(self.service.plate_visited(0, None, self.ROWS), frozenset())
        self.assertEqual(self.service.plate_visited(None, 5, self.ROWS), frozenset())

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
        # The opening poll with no geometry draws nothing at all: with
        # no polygon to mark, the layer is never walked. The cursor
        # advances regardless (the geometry that arrives next replays
        # from the layer's start), which the poll below proves.
        self.service.plate_visited(0, 8, [])
        self.assertEqual(walked, [], "the geometry-free poll walked the layer")
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
        # An advanced split walks the new edge alone, never the range —
        # and with every object already printed there is nothing left
        # to decide, so it draws no edge either (the all-printed fast
        # path). The cursor still advances.
        self.assertEqual(self.service.plate_visited(
            0, 9, self._rows(("Widget", self.POLYGON), ("Box", self.BOX))),
            frozenset({"Widget", "Box"}))
        self.assertEqual(walked, [], "the all-printed advance drew edges")
        self.assertEqual(self.service._visited_upto, 9,
                         "the all-printed advance did not advance the cursor")
        # A later object turns the walk back on: it is judged against
        # the consumed range — for its own geometry alone, and the
        # verdict keeps the objects already printed.
        self.assertEqual(self.service.plate_visited(
            0, 9, self._rows(("Widget", self.POLYGON), ("Box", self.BOX),
                             ("Spoon", self.SPOON))), frozenset({"Widget", "Box"}))
        self.assertEqual([edge[0] for edge in walked], list(range(9)),
                         "the new object's replay did not cover the consumed range")

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

    def test_an_extrusion_inside_overlapping_polygons_marks_both(self):
        # The release-candidate overlap question: an edge inside the
        # hulls of TWO objects has met both — the walk must not stop
        # at the first matching hull, or the later-defined object
        # never reads passed.
        data = (b"M82\n;LAYER:0\nG0 X0 Y0\nG1 X1 Y0 E1\nG0 X6 Y4\n"
                b"G1 X8 Y14 E2\n")  # motion 3: inside BOX and OVER both
        self._bind(data)
        over = [[5.0, 8.0], [15.0, 8.0], [15.0, 28.0], [5.0, 28.0]]
        rows = self._rows(("Box", self.BOX), ("Over", over))
        self.assertEqual(self.service.plate_visited(0, 4, rows),
                         frozenset({"Box", "Over"}),
                         "the overlap's second object was never marked")

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

    # The dense fixtures: `motions` straight extruding moves at y = 0,
    # x = motion - 1 -> motion, built in memory (no file), so a
    # 200 000-motion layer costs the walk and nothing else. An object
    # on that path is a narrow strip around x = centre.

    DENSE_MOTIONS = 200000

    def _bind_dense(self, motions, layers=1):
        index = make_index(layers=layers, motions=motions)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        return index

    @staticmethod
    def _strip(centre, half=25.0):
        return [[centre - half, -5.0], [centre + half, -5.0],
                [centre + half, 5.0], [centre - half, 5.0]]

    def _pin_budget(self, seconds):
        """Pin the walk's owner-thread budget. Zero cuts every poll at
        the walk's own check step, which is what makes a chunked walk
        countable; the default is the production bound."""
        original = self.service._VISITED_WALK_BUDGET_S
        self.service._VISITED_WALK_BUDGET_S = seconds
        self.addCleanup(setattr, self.service, "_VISITED_WALK_BUDGET_S", original)

    def _drive(self, anchor, split, objects, limit=4000):
        """Poll the way the live loop does — fresh rows every tick —
        until the walk has nothing left to consume. Returns ``(polls,
        verdict)`` and fails when the walk never settles."""
        service = self.service
        for polls in range(1, limit + 1):
            verdict = service.plate_visited(anchor, split, self._rows(*objects))
            if (service._visited_upto >= split
                    and service._visited_replay_upto >= service._visited_upto):
                return polls, verdict
        self.fail("the walk never settled after %d polls" % limit)

    def _count_segment_tests(self):
        """Every vertex test the walk runs, recorded."""
        module = self.qt.load("GCodeIndexService")
        real = module._segment_in_polygon
        calls = []

        def counted(*args):
            calls.append(args)
            return real(*args)

        patcher = patch.object(module, "_segment_in_polygon", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_a_dense_layer_without_geometry_is_never_walked(self):
        # The first fast path: with no polygon to mark, no edge of the
        # consumed range can change a verdict, so a dense layer is
        # never drawn at all. The cursor still advances with it — the
        # geometry that arrives later is judged against the WHOLE
        # consumed range (the late-DEFINE ruling), merely over polls.
        self._bind_dense(self.DENSE_MOTIONS)
        walked = self._counting_walk()
        self.assertEqual(self.service.plate_visited(0, 100000, []), frozenset())
        self.assertEqual(walked, [], "the geometry-free poll walked the dense layer")
        self.assertEqual(self.service._visited_upto, 100000,
                         "the cursor did not advance over the consumed range")
        polls, verdict = self._drive(0, 100000, [("Part", self._strip(5000))])
        self.assertEqual(verdict, frozenset({"Part"}),
                         "the late polygon was not replayed against the consumed range")
        self.assertGreater(polls, 1, "the dense replay was not spread over polls")

    def test_a_dense_layer_of_printed_objects_draws_no_edge(self):
        # The second fast path: every known polygon already visited —
        # nothing left to decide, so the advancing poll draws no edge
        # and still moves the cursor over the range.
        self._bind_dense(self.DENSE_MOTIONS)
        near = ("Near", self._strip(5000))
        far = ("Far", self._strip(50000))
        self._drive(0, 100000, [near, far])
        walked = self._counting_walk()
        self.assertEqual(self.service.plate_visited(0, self.DENSE_MOTIONS,
                                                    self._rows(near, far)),
                         frozenset({"Near", "Far"}),
                         "the all-printed advance changed a verdict")
        self.assertEqual(walked, [], "the all-printed advance drew the dense layer")
        self.assertEqual(self.service._visited_upto, self.DENSE_MOTIONS)

    def test_a_dense_replay_is_spread_over_bounded_polls(self):
        # The cut walk: each poll draws at most one check step, and the
        # chunks compose into one pass — no cut edge is drawn twice.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        walked = self._counting_walk()
        part = ("Part", self._strip(4000))
        counts, edges = [], []
        for _ in range(500):
            walked.clear()
            verdict = self.service.plate_visited(0, 5000, self._rows(part))
            counts.append(len(walked))
            edges.extend(edge[0] for edge in walked)
            if (self.service._visited_upto >= 5000
                    and self.service._visited_replay_upto >= self.service._visited_upto):
                break
        else:
            self.fail("the dense walk never settled")
        self.assertEqual(verdict, frozenset({"Part"}))
        self.assertGreater(len(counts), 1, "the walk was not cut")
        self.assertTrue(all(count <= self.service._VISITED_WALK_STEP for count in counts),
                        "a poll drew more than the check step: %s" % counts)
        self.assertTrue(all(edge < 5000 for edge in edges),
                        "the walk drew past its own range")
        self.assertEqual(edges, sorted(set(edges)),
                         "a cut walk drew an edge twice")

    def test_a_late_polygon_replays_a_dense_consumed_range(self):
        # The late-DEFINE ruling under the budget: the polygon arrives
        # after the layer was consumed with no geometry at all, so only
        # the frontier's replay can find it. The verdict proves the
        # replay covered the consumed range, and the cut chunks prove
        # it did so without re-walking an edge.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        self.assertEqual(self.service.plate_visited(0, 5000, []), frozenset())
        walked = self._counting_walk()
        part = ("Part", self._strip(4000))
        counts, edges = [], []
        for _ in range(500):
            walked.clear()
            verdict = self.service.plate_visited(0, 5000, self._rows(part))
            counts.append(len(walked))
            edges.extend(edge[0] for edge in walked)
            if self.service._visited_replay_upto >= self.service._visited_upto:
                break
        else:
            self.fail("the late polygon was never judged against the consumed range")
        self.assertEqual(verdict, frozenset({"Part"}),
                         "the late polygon was not marked")
        self.assertGreater(len(counts), 1, "the replay was not spread over polls")
        self.assertIn(4000, edges,
                      "the replay did not reach the extrusion it had to judge")
        self.assertEqual(edges, sorted(set(edges)),
                         "the frontier replay re-walked a consumed edge")
        self.assertEqual(self.service._visited_replay_upto, 5000,
                         "the replay did not finish on the consumed range")

    def test_a_changed_polygon_on_a_printed_object_costs_no_walk(self):
        # An object already marked printed cannot change its verdict,
        # whatever its geometry does: no walk, no vertex test, and the
        # verdict holds.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        self._drive(0, 5000, [("Part", self._strip(4000))])
        walked = self._counting_walk()
        calls = self._count_segment_tests()
        self.assertEqual(self.service.plate_visited(0, 5000,
                                                    self._rows(("Part", self._strip(1000)))),
                         frozenset({"Part"}))
        self.assertEqual(walked, [], "the printed object was judged again")
        self.assertEqual(calls, [], "the printed object's vertices were tested")

    def test_a_settled_dense_poll_tests_no_vertex(self):
        # The settled poll (unchanged geometry, unchanged split): no
        # edge and no vertex test, however dense the layer.
        self._bind_dense(self.DENSE_MOTIONS)
        objects = [("Obj%02d" % i, self._strip(2000 + 7000 * i)) for i in range(4)]
        self._drive(0, 20000, objects)
        walked = self._counting_walk()
        calls = self._count_segment_tests()
        for _ in range(3):
            self.assertEqual(self.service.plate_visited(0, 20000, self._rows(*objects)),
                             frozenset({"Obj00", "Obj01", "Obj02"}))
        self.assertEqual(walked, [], "a settled dense poll re-walked the layer")
        self.assertEqual(calls, [], "a settled dense poll tested vertices")

    def test_an_advancing_split_walks_only_the_new_range(self):
        # The cursor's own rule, with geometry still outstanding: the
        # delta draws the range since the last poll, never the layer.
        # The first range is DRIVEN to its cursor instead of assumed to
        # fit one poll: the walk's budget is wall-clock, so where a
        # platform cuts it (Windows cut it at the very first check step
        # — _VISITED_WALK_STEP — and the delta then resumed from there)
        # is a scheduling detail of the machine, never of the rule
        # under test. The edges the second range walks are counted
        # whole, however many polls they take.
        self._bind_dense(5000)
        walked = self._counting_walk()
        part = ("Part", self._strip(4800))
        self._drive(0, 1000, [part])
        walked.clear()
        self._drive(0, 2000, [part])
        self.assertEqual([edge[0] for edge in walked], list(range(1000, 2000)),
                         "the delta re-walked the consumed range")

    def test_a_bounded_walk_agrees_with_an_unbounded_one(self):
        # The cut is a scheduling detail, never a semantic one: the
        # chunked verdict equals the one-pass verdict.
        module = self.qt.load("GCodeIndexService")
        objects = [("Obj%02d" % i, self._strip(700 + 900 * i)) for i in range(4)]
        self._bind_dense(8000)
        self._pin_budget(0.0)
        _polls, bounded = self._drive(0, 8000, objects)
        other = module.GCodeIndexService(self.files, object())
        self.addCleanup(other.close)
        other.bind(self.job)
        other._view = module.IndexView(self.job, make_index(layers=1, motions=8000))
        other._VISITED_WALK_BUDGET_S = 60.0
        unbounded = other.plate_visited(0, 8000, self._rows(*objects))
        self.assertEqual(bounded, unbounded,
                         "the chunked walk read differently from the one-pass walk")
        self.assertEqual(bounded, frozenset(name for name, _polygon in objects))

    def test_rapid_anchor_changes_stay_bounded(self):
        # The anchor flips with the print (and with a manual detach):
        # every poll stays inside the budget, and a fresh anchor never
        # inherits the other layer's work.
        self._bind_dense(5000, layers=2)
        self._pin_budget(0.0)
        walked = self._counting_walk()
        part = ("Part", self._strip(4000))
        counts = []
        for anchor in (0, 1) * 6:
            walked.clear()
            self.assertEqual(self.service.plate_visited(anchor, 5000, self._rows(part)),
                             frozenset(),
                             "an anchor flip claimed a verdict it never walked")
            counts.append(len(walked))
        self.assertTrue(all(0 < count <= self.service._VISITED_WALK_STEP for count in counts),
                        "an anchor flip overspent its poll: %s" % counts)
        _polls, verdict = self._drive(0, 5000, [part])
        self.assertEqual(verdict, frozenset({"Part"}),
                         "the settled anchor never reached its own layer's verdict")

    def test_a_dense_travel_never_marks_an_object(self):
        # The E rule holds on a dense walk: the layer's travel run
        # crosses the object and deposits nothing there.
        index = self._bind_dense(5000)
        index.travel_starts[0] = [1000]
        index.travel_ends[0] = [2000]
        self._pin_budget(0.0)
        _polls, verdict = self._drive(0, 5000, [("Part", self._strip(1500))])
        self.assertEqual(verdict, frozenset(), "the travel marked the object")
        self.assertEqual(self.service._visited_upto, 5000,
                         "the travel run was not walked")
        # The control: an object where the extrusion resumes is marked.
        _polls, after = self._drive(0, 5000, [("Part", self._strip(2500))])
        self.assertEqual(after, frozenset({"Part"}),
                         "the extrusion after the travel did not mark the object")

    def test_overlapping_objects_on_a_dense_layer_both_mark(self):
        # One extruding edge visits every hull it meets: the dense walk
        # must never stop at the first match.
        self._bind_dense(5000)
        self._pin_budget(0.0)
        _polls, verdict = self._drive(0, 5000, [("Box", self._strip(4000, 200.0)),
                                                ("Over", self._strip(4000, 100.0))])
        self.assertEqual(verdict, frozenset({"Box", "Over"}),
                         "the overlap's second object was never marked")

    def test_the_owner_thread_bound_holds_on_a_dense_layer(self):
        # The reviewer's repro, measured on the owner thread: a dense
        # layer attached part-way through with every object defined.
        # Each poll is bounded (the one-pass walk costs ~839 ms), the
        # verdict is never provisional, and the walk still converges on
        # the whole truth. The bound is the walk's own budget plus an
        # order of magnitude of headroom for a loaded machine.
        self._bind_dense(self.DENSE_MOTIONS)
        objects = [("Obj%02d" % i, self._strip(5000 + 9000 * i)) for i in range(20)]
        worst, polls, verdict = 0.0, 0, frozenset()
        for _ in range(2000):
            start = time.monotonic()
            verdict = self.service.plate_visited(0, 100000, self._rows(*objects))
            worst = max(worst, time.monotonic() - start)
            polls += 1
            if (self.service._visited_upto >= 100000
                    and self.service._visited_replay_upto >= self.service._visited_upto):
                break
        else:
            self.fail("the dense walk never settled")
        self.assertLess(worst * 1000.0, 100.0,
                        "one poll spent %.1f ms on the owner thread" % (worst * 1000.0))
        self.assertLess(polls, 500, "the dense walk took %d polls to settle" % polls)
        self.assertEqual(verdict,
                         frozenset(name for name, strip in objects if strip[2][0] < 100000),
                         "the bounded walk did not reach the consumed range's truth")


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the index service suite")
class PreparedReopenPolicyTests(unittest.TestCase):
    """The reopen/repair/persist policy: the fast path, the repair copy, the
    demand-persistence, the store-census fraction, the byte budgets
    and the rebind abort."""

    class Identity:
        uuid = "u"
        modified = 1
        size = 100

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

    def test_the_public_restore_adopts_the_prepared_store(self):
        # G: the PRODUCTION lifecycle — _finish("restore") installs
        # the view, and must OPEN the prepared table BEFORE adopting
        # it (the restore path returns before _advance's open, and
        # _advance never re-adopts). A persisted complete store
        # takes the fast path through the real restore: no raw
        # download, no full-prepare rebuild, and the window's
        # payloads decode from the prepared store.
        # The harness's qt.load duplicates the plugin modules, so the
        # index must come from the HARNESS namespace's GCodeIndex —
        # the restore's isinstance guard reads the service's class
        # from that same namespace.
        index = self.qt.load("GCodeIndex").build_index_from_bytes(
            b"".join(b";LAYER:%d\nG1 X0 Y0 E0.1\n" % layer for layer in range(5)))
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        requested = []

        class Cache:
            def load(self, identity):
                return index

        self.service._cache = Cache()
        self.service._restored = False
        self.files.lease = lambda: None  # the raw lease is unavailable
        self.files.request_file = lambda: requested.append("file")
        submitted = []
        original_submit = self.service._submit
        self.service._submit = (lambda kind, fn, *args, **kwargs:
                                (submitted.append(kind),
                                 original_submit(kind, fn, *args, **kwargs))[1])
        self._pump()
        self.assertTrue(self.service._prepared_saved,
                        "the restored complete store never took the fast path")
        self.assertTrue(self.service._prepared_complete)
        self.assertEqual(self.service._full_next, 5)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.assertEqual(requested, [], "the restore demanded the raw file")
        self.assertNotIn("fullprep", submitted,
                         "the restore started a full prepared rebuild")
        # The window's payloads decode from the prepared store — the
        # presentation source reports the decoded hot state.
        window = self.service.plate_layers(2)
        self.assertIsNotNone(window)
        self.assertIn("current", window)
        # The live window's hydration follows the adoption (the fast
        # path's pump exits before the demand submits): drive it and
        # confirm the prepared store served the payload — no decode
        # from the raw file.
        from PyQt6.QtCore import QCoreApplication
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            self.service.request_hydration(2)  # the live seek's demand
            self.service._advance()
            if self.service._presentation_source(2) == "decoded":
                break
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.service._presentation_source(2), "decoded",
                         "the restored current layer never became presentation-ready")

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

    def test_a_layer_is_not_served_without_a_table(self):
        # No open table (a print whose pass never opened one, a cache
        # clear): nothing is readable without the raw file.
        self.assertFalse(self.service._prepared_served(0))
        self.service._prepared_table = []
        self.assertFalse(self.service._prepared_served(0))

    def test_a_table_of_another_length_is_dropped(self):
        # The file's layer count no longer matches this print's index: the
        # table is unusable, and the fresh pass overwrites it rather than
        # resume one print's geometry onto another's layers.
        self._view(5)
        self.store.finalise("print-key", [self._payload(i) for i in range(3)])
        self.service._prepared_open(self.files.identity)
        self.assertEqual(len(self.service._prepared_coverage), 3)
        self.service._adopt_prepared()
        self.assertIsNone(self.service._prepared_table, "a foreign-length table was adopted")
        self.assertEqual(self.service._prepared_coverage, set())

    def test_an_abandoned_writer_is_aborted(self):
        # An unfinished writer on an abandon path: its temp file goes and
        # the handle closes, so the store never publishes half a pass.
        writer = self.store.open_for_write("print-key", 3)
        self.service._prepared_writer = writer
        self.service._abort_prepared_writer()
        self.assertIsNone(self.service._prepared_writer)
        self.assertTrue(writer["retired"], "the abandoned writer stayed writable")
        self.assertFalse(os.path.exists(writer["temp"]), "the abandoned writer's file survived")

    def test_writers_drop_even_when_the_store_is_already_gone(self):
        # A cache clear or a rebind can take the store first: the writer
        # reference still drops, and no later pass can finalise it.
        self.service._abort_prepared_writer()          # nothing to abandon
        self.service._suspend_prepared_writer()        # nothing to checkpoint
        self.service._prepared = None
        self.service._prepared_writer = {"table": [None]}
        self.service._abort_prepared_writer()
        self.assertIsNone(self.service._prepared_writer)
        self.service._prepared_writer = {"table": [None]}
        self.service._suspend_prepared_writer()
        self.assertIsNone(self.service._prepared_writer)

    def test_a_weak_identity_never_opens_the_prepared_table(self):
        # The same strength gate the index restore obeys: a weak identity
        # (no reliable timestamp) must never adopt the old prepared
        # table, or a re-extracted file resurrects stale geometry.
        self._view(5)
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        self.files.identity.modified = 0.0
        self.service._prepared_open(self.files.identity)
        self.assertIsNone(self.service._prepared_table)
        # And with no table at all there is nothing to adopt.
        self.service._adopt_prepared()
        self.assertFalse(self.service._prepared_saved)

    def test_the_fraction_reads_the_ram_tier_when_nothing_persists(self):
        # No persistence configured: the RAM tier's residency is the only
        # prepared store there is — and no view is no fraction at all.
        self._view(5)
        self.service._prepared = None
        self.service._full_cache.set(0, b"x" * 10, 10)
        self.assertEqual(self.service.plate_pass_fraction(), 1 / 5)
        self.service._view = None
        self.assertIsNone(self.service.plate_pass_fraction())

    def test_an_index_with_no_layers_has_no_fraction(self):
        # No layers is no fraction: the pass bar reads empty, never a
        # division by a zero total.
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, make_index(layers=0))
        self.assertIsNone(self.service.plate_pass_fraction())

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

    def test_a_prepared_layer_decodes_without_a_lease_and_hydrates_its_arrays_later(self):
        # F3: a reopened COMPLETE compact store's geometry is the
        # prepared bytes' own work. The raw G-code lease gates only the
        # physical MOTION ARRAYS — a demand with no file in hand must
        # still decode and display, and the arrays then hydrate the
        # moment the file arrives, with no second presentation demand.
        path = _write_gcode(b"".join(
            b";LAYER:%d\n;TYPE:WALL\nG1 X0 Y0 E0.1\nG1 X1 Y1 E0.2\nG1 X2 Y2 E0.3\n" % layer
            for layer in range(3)))
        self.addCleanup(os.remove, path)
        index = self.qt.load("GCodeIndex").build_index_from_file(path, compact=True)
        self.assertTrue(index.compact)
        self.assertEqual(index.hydrated_layers, set(), "the compact scan hydrated a layer")
        self.store.finalise("print-key", [self._payload(layer) for layer in range(3)])
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_complete,
                        "the complete store never took the fast path")
        submitted = []
        original_submit = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append((kind, lease)) or original_submit(kind, work, lease))

        # Phase one: no raw file at all. The prepared payload must reach
        # the hot cache anyway — the arrays are the file's business.
        self.files.lease = lambda: None
        self.service.request_hydration(1)
        for _ in range(300):
            self.service._advance()
            self.qt.events(5)
            if 1 in self.service._decoded_lru:
                break
        self.assertIn(1, self.service._decoded_lru,
                      "the prepared compact layer never decoded without a lease")
        self.assertEqual(self.service._presentation_source(1), "decoded")
        self.assertIsNotNone(self.service.plate_layers(1)["current"],
                             "the decoded payload never reached the display bundle")
        self.assertTrue([kind for kind, _lease in submitted if kind == "hydrate"],
                        "the lease-less demand submitted no worker at all")
        self.assertTrue(all(lease is None for kind, lease in submitted if kind == "hydrate"),
                        "the presentation pass waited on a lease it does not need")
        self.assertNotIn(1, index.hydrated_layers, "an absent file hydrated arrays")

        # Phase two: the raw file arrives. The arrays hydrate off the
        # recorded demand alone — the layer is already decoded, so
        # nothing re-asks for its presentation.
        class RawLease:
            def __init__(self, source):
                self.path = source

            def close(self):
                pass

        self.files.lease = lambda: RawLease(path)
        for _ in range(300):
            self.service._advance()
            self.qt.events(5)
            if 1 in index.hydrated_layers:
                break
        self.assertIn(1, index.hydrated_layers,
                      "the motion arrays never hydrated after the raw file arrived")
        self.assertTrue(len(index.motion_offsets[1]) > 0,
                        "the hydration landed no physical motion arrays")

    def test_the_arrays_debt_prunes_and_waits_for_the_file(self):
        # The debt's own contract: it holds only layers the retention
        # window still covers and the index still lacks, and while no
        # file is in hand it submits NOTHING — one request for the file
        # is the whole poll. The file's arrival drains it once, through
        # the arrays' own worker, carrying the lease the presentation
        # never had.
        index = self._compact_view(3, hydrated=(2,), followed=1)
        self.service._hydrate_arrays = {0, 2, 9}
        self.service._full_next = 3  # the pass is done: the poll is the debt's
        self.files.lease = lambda: None
        requested = []
        self.files.request_file = lambda: requested.append(1)
        captured = self._capture_submit()
        self.service._advance()
        self.assertEqual(captured, [], "a lease-less debt submitted a worker")
        self.assertEqual(requested, [1], "the debt never asked for the file")
        self.assertEqual(self.service._hydrate_arrays, {0},
                         "the debt kept a hydrated or out-of-range layer")

        class RawLease:
            path = "/nonexistent/part.gcode"

            def close(self):
                pass

        self.files.lease = lambda: RawLease()
        captured = self._capture_submit()
        self.service._advance()
        self.assertEqual([kind for kind, _work, _lease in captured], ["hydrate"],
                         "the arriving file submitted no arrays worker")
        self.assertIsNotNone(captured[0][2], "the arrays worker took no lease")
        self.assertEqual(captured[0][1](), ([], {}),
                         "the arrays worker answers another contract than the hydrate")
        self.assertEqual(self.service._hydrate_arrays, set(),
                         "the settled debt stayed queued")
        self.assertNotIn(0, index.hydrated_layers)

    def test_a_swept_store_never_receives_an_older_generations_save(self):
        # The clear's lifecycle: a save queued BEFORE the clear must
        # never recreate the swept directory; a rebind's store is a
        # different directory and still receives its save.
        saved = []

        class Store:
            def save(self, identity, index):
                saved.append(identity)
                return "path"

        store = Store()
        service = self.service
        service._swept_store = store
        service._generation = 7
        # The old generation's queued save against the swept store.
        self.assertIsNone(service._save_index(store, 6, "identity", object()))
        self.assertEqual(saved, [], "the swept store received the stale save")
        # The same store under the CURRENT generation saves.
        self.assertEqual(service._save_index(store, 7, "identity", object()), "path")
        self.assertEqual(saved, ["identity"])
        # A different store (a rebind) under an old generation saves.
        other = Store()
        self.assertEqual(service._save_index(other, 6, "other", object()), "path")
        self.assertEqual(saved, ["identity", "other"])

    def test_a_detached_seek_never_rewinds_a_complete_fast_reopen(self):
        # The review's finding: a seek focused the pass by rewinding
        # the frontier — after a fast-path restore that rewind
        # re-read the whole saved store for nothing. The saved latch
        # now holds the frontier: the fast path stays complete.
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved)
        self.assertEqual(self.service._full_next, 5)
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service.set_manual_anchor(3)
        self.assertEqual(self.service._full_next, 5,
                         "the seek rewound the fast path's frontier")
        self._pump()  # the sought window's own demand settles
        self.assertNotIn("fullprep", submitted,
                         "the seek restarted the whole-store walk")
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

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

    def test_the_batch_loop_yields_the_interpreter_with_no_demand_pending(self):
        # The passive-yield pin. A batch's loop is tight and its layers
        # are cheap, so nothing but a wall-clock gate stops the worker
        # holding the GIL for the walk's whole duration — which is what
        # the UI thread reads as a frozen window for the pass. The
        # cadence is counted from the production yield's own calls (the
        # real yield still runs), and the count it is held to is the
        # ASKED count, never a fire count against the wall clock: a
        # descheduled worker cannot hand back a GIL it is not holding,
        # so a floor on fires per unit of time pins the machine's load
        # rather than the gate.
        module = self.qt.load("GCodeIndexService")
        self.assertTrue(hasattr(module, "passive_yield"),
                        "the background workers have no passive yield at all")
        # 20,000 layers: the deadline is what ends the walk, never an
        # exhausted frontier — a batch that ran out of layers would stop
        # asking the gate before the window closed.
        index = make_index(layers=20000, motions=20)
        self.service._view = module.IndexView(self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        captured = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: captured.append((kind, work))
        self.service._advance()
        self.service._submit = original
        self.assertEqual(captured[0][0], "fullprep",
                         "the first submission was not the pass")
        asked = []
        yields = []
        real = module.passive_yield

        def recorded(now, last):
            # The gate is asked far more often than it fires — the
            # demand check runs at the preparation's own granularity —
            # so only the calls that MOVED the watermark are hand-backs.
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                yields.append(time.monotonic())
            return updated

        started = time.monotonic()
        with patch.object(module, "passive_yield", recorded):
            frontier, _encoded, _uncacheable = captured[0][1]()
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(
            len(asked), 64,
            "the batch walked %d layers in %.0f ms and asked the gate %d times"
            % (frontier, elapsed * 1000.0, len(asked)))
        # The gate FIRING is what a descheduled worker cannot promise:
        # it hands back a GIL only while it holds one, and a run that
        # was off-CPU for the walk fires the gate once. The floor is
        # therefore that it fired at all, and the cadence claim below
        # is made only where there are two fires to measure between —
        # read as a fire count per unit of time it would pin the
        # machine's load, which is the mistake this pin exists to
        # avoid.
        self.assertGreaterEqual(
            len(yields), 1,
            "the batch walked %d layers in %.0f ms and never handed back"
            % (frontier, elapsed * 1000.0))
        # The cadence is evidence, not a bound: a median gap is still a
        # reading of the machine's scheduling, and the contract above is
        # what fails when the gate is not consulted.

    def test_the_passive_yield_sleeps_when_due_and_only_then(self):
        # The helper's own contract, with a controlled clock and a
        # RECORDED sleeper. Counting a changed watermark is not proof of
        # a hand-back: the helper returns a fresh monotonic() whether or
        # not it slept, so a version with the sleep REMOVED still "hands
        # back" by that measure — measured, both integration pins passed
        # with only time.sleep(_YIELD_SLEEP_S) deleted. This is the pin
        # that fails on that mutation.
        module = self.qt.load("GCodeIndex")
        slept = []
        stub = SimpleNamespace(monotonic=lambda: 123.0,
                               sleep=lambda seconds: slept.append(seconds))
        with patch.object(module, "time", stub):
            early = module.passive_yield(10.0, 10.0 - module._PASSIVE_YIELD_S / 2)
            self.assertEqual(slept, [], "the gate slept before it was due")
            self.assertAlmostEqual(early, 10.0 - module._PASSIVE_YIELD_S / 2,
                                   msg="an early ask moved the watermark")
            due = module.passive_yield(10.0, 10.0 - module._PASSIVE_YIELD_S)
            self.assertEqual(len(slept), 1, "a due ask never handed back")
            self.assertEqual(slept[0], module._YIELD_SLEEP_S,
                             "the gate slept for the wrong interval")
            self.assertEqual(due, 123.0, "a due ask left the watermark")

    def test_the_pass_hands_the_interpreter_back_throughout_its_walk(self):
        # The scheduling contract, asserted deterministically: while the
        # pass walks flat-out on the worker, it asks its wall-clock gate
        # throughout and every ask that fires hands the interpreter
        # back. That is what stops a worker starving the UI thread.
        #
        # The beats a real timer sees while this runs are EVIDENCE and
        # are printed, not asserted. On a shared runner a descheduled
        # process and a worker holding the GIL are indistinguishable
        # from the timer's side, so a beat count or a worst gap derived
        # from wall clock pins the runner, not the gate — measured: the
        # same tree passed on one leg and failed another with 19 beats
        # against a minimum of 25.
        module = self.qt.load("GCodeIndexService")
        index = make_index(layers=8000, motions=20)
        self.service._view = module.IndexView(self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        beats = []
        heartbeat = self.qt.QTimer()
        heartbeat.setInterval(_HEARTBEAT_INTERVAL_MS)
        heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
        self.addCleanup(heartbeat.stop)
        asked = []
        fires = []
        real = module.passive_yield

        def recorded(now, last):
            # The gate is asked far more often than it fires; only the
            # calls that MOVED the watermark are hand-backs.
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                fires.append(now)
            return updated

        heartbeat.start()
        started = time.monotonic()
        with patch.object(module, "passive_yield", recorded):
            self._pump(timeout=30.0)
        elapsed = time.monotonic() - started
        heartbeat.stop()
        self.assertGreaterEqual(
            len(asked), 64,
            "the pass walked %.0f ms flat out and asked its gate %d times"
            % (elapsed * 1000.0, len(asked)))
        self.assertGreaterEqual(
            len(fires), 1,
            "the pass never handed the interpreter back in %.0f ms"
            % (elapsed * 1000.0))
        # Timing is EVIDENCE here, never a bound: a median under 50 ms
        # still depends on a shared runner's scheduling, and the beats a
        # timer sees cannot tell a descheduled process from a worker
        # holding the GIL.
        gaps = sorted(b - a for a, b in pairwise(fires))
        print("heartbeat evidence: %d beats, %d gate asks, %d hand-backs, "
              "median hand-back gap %.1f ms over %.0f ms"
              % (len(beats), len(asked), len(fires),
                 (gaps[len(gaps) // 2] * 1000.0 if gaps else -1.0),
                 elapsed * 1000.0))

    def test_the_demands_own_encodings_are_written_by_the_worker(self):
        # The persistence move's two pins at once: a demanded layer's
        # encoded bytes must reach the incremental writer, and they must
        # reach it from the WORKER — the commit's per-layer
        # write+flush+seek+table-write+flush is exactly the UI-thread
        # cost the move removes. The store's append is recorded through
        # the class, so the identity assertion holds for every path that
        # can reach it.
        from plugins.PreparedStore import PreparedCache
        index = make_index(layers=6, motions=40)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        ui_thread = threading.get_ident()
        appends = []
        real_append = PreparedCache.append

        def recorded(store, writer, layer, payload):
            appends.append((threading.get_ident(), layer))
            return real_append(store, writer, layer, payload)

        with patch.object(PreparedCache, "append", recorded):
            self.service.request_hydration(2)
            self._pump()
        self.assertIn(2, [layer for _ident, layer in appends],
                      "the demanded layer never reached the writer")
        self.assertNotIn(ui_thread, [ident for ident, _layer in appends],
                         "a store append ran on the UI thread")
        # ...and the once-only guarantee survives the move: the demanded
        # layer's slot holds its bytes in the published store rather than
        # the (0, 0) hole the pass alone would have left behind it.
        loaded = self.store.load_table("print-key")
        self.assertEqual(loaded["table"][2][0], self.state_cached,
                         "the demanded layer published as a hole")

    def test_the_batch_loop_yields_on_the_foreground_event_alone(self):
        # The loop-top yield reads the thread-safe EVENT, never the
        # mutable hydrate set across the thread boundary. With the
        # event set (a demand's signal) and the set EMPTY, a batch
        # must stop at the first loop-top check instead of running
        # its whole deadline.
        index = make_index(layers=5, motions=2)
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        captured = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            captured.append((kind, work)))
        self.service._advance()
        self.service._submit = original
        self.assertEqual(captured[0][0], "fullprep",
                         "the first submission was not the pass")
        work = captured[0][1]
        self.assertEqual(self.service._hydrate, set())
        self.service._foreground_pending.set()  # the demand's signal alone
        frontier, _encoded, _uncacheable = work()
        self.assertEqual(frontier, 0,
                         "the batch ignored the event and walked on")
        self.service._busy = ""  # the captured batch never ran for real

    def test_the_repair_copies_an_uncacheable_entry_without_a_rewalk(self):
        # The repair copies the old file's UNCACHEABLE state into the
        # new writer WITHOUT re-walking the layer — the codec's
        # refusal stands across sessions, the entry never becomes an
        # EMPTY hole, and only the genuine hole regenerates.
        from plugins.PreparedStore import STATE_UNCACHEABLE
        writer = self.store.open_for_write("print-key", 5)
        for layer in (0, 1, 4):
            self.store.append(writer, layer, self._payload(layer))
        self.store.append_uncacheable(writer, 3)
        self.store.finish_write(writer)  # complete: 2 EMPTY, 3 refused
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        module = self.qt.load("GCodeIndexService")
        prepared_walks = []
        real_prepare = module._prepare_layer

        def spied(index_arg, layer, should_yield=None):
            prepared_walks.append(layer)
            return real_prepare(index_arg, layer, should_yield)

        with patch.object(module, "_prepare_layer", spied):
            self._pump()
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["complete"])
        self.assertEqual(loaded["table"][3], (STATE_UNCACHEABLE, 0, 0),
                         "the repair lost the uncacheable state")
        self.assertEqual(loaded["table"][2][0], self.state_cached,
                         "the genuine hole never regenerated")
        self.assertNotIn(3, prepared_walks,
                         "the repair re-walked the refused layer")
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_a_uuid_only_identity_never_restores(self):
        # The review's identity policy at the SERVICE gate: the uuid
        # alone must never make an identity "strong enough to
        # restore" — the restore's strength is the reliable modified
        # timestamp, because the lookup and the validation both
        # ignore the uuid.
        self._view(5)
        self.service._restored = False
        self.files.identity.uuid = "u1"
        self.files.identity.modified = 0.0
        self.files.identity.size = 0
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertTrue(self.service._restored)
        self.assertNotIn("restore", submitted,
                         "a uuid-only identity attempted the restore")

    def test_a_weak_size_only_identity_never_restores(self):
        # The weak case (the review's policy): a name + size with NO
        # reliable timestamp is insufficient for cross-session reuse —
        # the content may have changed between extractions, so the
        # restore is skipped and the file rebuilds.
        self._view(5)
        self.service._restored = False
        self.files.identity.uuid = ""
        self.files.identity.modified = 0.0
        self.files.identity.size = 100
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertTrue(self.service._restored)
        self.assertNotIn("restore", submitted,
                         "a weak size-only identity attempted the restore")

    def test_a_timestamped_identity_takes_the_restore_gate(self):
        # The contrasting gate: a reliable modified timestamp makes
        # the identity strong enough for the persistent restore —
        # the submission names the restore.
        self._view(5)
        self.service._restored = False
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertTrue(self.service._restored)
        self.assertIn("restore", submitted,
                      "a timestamped identity never took the restore gate")

    def test_a_rebind_checkpoints_the_old_print_writer(self):
        # The rebind CHECKPOINTS (the review's clean-shutdown finding):
        # the old print's committed layers publish as an incomplete
        # store — never a bare drop of the reference, never a .tmp
        # left behind, and the old print's progress survives for its
        # next session.
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._prepared_persist(0, self._payload(0))
        temp = self.service._prepared_writer["temp"]
        self.assertTrue(os.path.exists(temp))
        self.service.bind(("other.gcode", 100, 2))
        self.assertIsNone(self.service._prepared_writer)
        self.assertFalse(os.path.exists(temp),
                         "the rebind left the old print's temp writer")
        leftovers = [name for root, _dirs, names in os.walk(self._dir.name)
                     for name in names if ".tmp-" in name]
        self.assertEqual(leftovers, [])
        table = self.store.load_table("print-key")
        self.assertIsNotNone(table, "the checkpointed partial never published")
        self.assertFalse(table["complete"], "the partial read as complete")
        self.assertEqual(self.store.read("print-key", table["table"], 0),
                         self._payload(0),
                         "the checkpointed layer never round-tripped")

    def test_a_close_waits_for_the_worker_it_started(self):
        # The worker writes the prepared store, and on Windows a
        # directory holding a file that appears after the delete has
        # listed it cannot be removed (WinError 145 in this file's own
        # teardown). The same race lands a write after the plugin
        # believes it has shut down. close() must not return while a
        # worker is still running.
        self._view(3)
        self.service._prepared_open(self.files.identity)
        self.service._advance()
        executor = self.service._executor
        # The premise: work was actually submitted, so there IS a
        # worker to leave running (an empty pool would pass this
        # whatever close() did).
        self.assertTrue(executor._threads, "no worker was ever started")
        self.service.close()
        alive = [t for t in executor._threads if t.is_alive()]
        self.assertEqual(alive, [], "close() left its worker running")

    def test_a_clean_close_publishes_the_partial_preparation(self):
        # The review's clean-shutdown P0: session A prepares a subset,
        # then closes NORMALLY — the checkpoint publishes as an
        # incomplete store. Session B (fresh stores, fresh service,
        # same identity, NO dead pid) sees the coverage immediately,
        # reads the prepared layers from disk, resumes from the EMPTY
        # slots and reaches a complete store.
        self._view(40)
        self.service._prepared_open(self.files.identity)
        # The production append path the pass's worker uses: three
        # layers commit, the pass's remaining walk is interrupted by
        # the NORMAL close — no fake crash, no dead pid.
        for layer in range(3):
            self.service._prepared_persist(layer, self._payload(layer))
        writer = self.service._prepared_writer
        self.assertIsNotNone(writer, "the pass never opened its writer")
        self.service.close()  # the NORMAL close — no fake crash
        table = self.store.load_table("print-key")
        self.assertIsNotNone(table, "the clean close published nothing")
        self.assertFalse(table["complete"], "the partial read as complete")
        prepared = [i for i, entry in enumerate(table["table"])
                    if entry[0] == self.state_cached]
        self.assertGreater(len(prepared), 0, "no layer survived the close")
        self.assertLess(len(prepared), 40, "the partial published as complete")
        for layer in prepared[:3]:
            self.assertEqual(self.store.read("print-key", table["table"], layer),
                             self._payload(layer),
                             "a checkpointed layer never round-tripped")
        # Session B: fresh stores, fresh service, the same identity
        # (the same process — a real disk-backed restart).
        from plugins.PreparedStore import PreparedCache
        self.store = PreparedCache(self._dir.name)
        module = self.qt.load("GCodeIndexService")
        self.service = module.GCodeIndexService(self.files, object(),
                                                prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = True
        self.service._wanted = True
        self._view(40)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        fraction = self.service.plate_pass_fraction()
        self.assertGreater(fraction, 0.0,
                           "the resumed session lost the checkpoint")
        self.assertLess(fraction, 1.0,
                        "the resumed session read the partial as complete")
        # The N prepared layers are served from the store; the pass
        # resumes from the EMPTY slots and the final store completes.
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_a_weak_re_extraction_never_adopts_the_old_prepared_geometry(self):
        # F3's required E2E: session 1 persists prepared geometry for
        # a WEAK identity (same name + size, modified 0, uuid A).
        # Session 2 is the re-extraction — same key, same layer
        # count, a fresh uuid and DIFFERENT geometry — and must
        # neither attempt the index restore nor adopt the old
        # prepared table: the rebuilt geometry replaces the old
        # representation byte for byte.
        module = self.qt.load("GCodeIndexService")
        self.files.identity.uuid = "uA"
        self.files.identity.modified = 0.0
        self.files.identity.size = 100
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        old_table = self.store.load_table("print-key")
        self.assertIsNotNone(old_table, "session 1 persisted nothing")
        old_payload = self.store.read("print-key", old_table["table"], 0)

        # Session 2: fresh stores, fresh service, the re-extracted
        # identity — same name + size, still no timestamp, a NEW uuid
        # and different geometry (40 motions vs 20) over the SAME
        # layer count, so a layer-count check cannot save us.
        self.service.close()
        from plugins.PreparedStore import PreparedCache
        self.store = PreparedCache(self._dir.name)
        self.service = module.GCodeIndexService(self.files, object(),
                                                prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = False  # the gate is genuinely exercised
        self.service._wanted = True
        self.files.identity.uuid = "uB"
        index_b = make_index(layers=5, motions=40)
        self.service._view = module.IndexView(self.files.job_key, index_b)
        # The rejection is read BEFORE the pass is submitted: the old
        # file on disk holds five resolved layers, and the weak identity
        # may seed neither the table nor the coverage from them. Read
        # after _advance() the same assertion races the pass's own
        # completions, which resolve layers into that very set.
        self.service._prepared_open(self.files.identity)
        self.assertIsNone(self.service._prepared_table,
                          "the old prepared table was adopted")
        self.assertEqual(self.service._prepared_coverage, set(),
                         "the old prepared coverage leaked in")
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertNotIn("restore", submitted,
                         "the weak re-extraction attempted the index restore")
        self.assertIsNone(self.service._prepared_table,
                          "the old prepared table was adopted")
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        # The pass ran to its own completion (the settle above waits for
        # its terminal signal): every layer resolved is the FRESH view's,
        # since a set carried over from the old table never reaches the
        # count without the pass.
        self.assertEqual(self.service._prepared_coverage, set(range(5)),
                         "the fresh pass did not resolve the coverage")
        # The published store now holds the NEW geometry's exact
        # bytes — the old representation was replaced, never served.
        new_table = self.store.load_table("print-key")
        self.assertIsNotNone(new_table)
        new_payload = self.store.read("print-key", new_table["table"], 0)
        self.assertNotEqual(new_payload, old_payload,
                            "the old geometry survived the rebuild")
        expected = module._encode_layer(
            module._prepare_layer(index_b, 0, lambda: False))
        self.assertEqual(new_payload, expected,
                         "the published layer is not the new geometry's bytes")

    def test_a_regenerated_uuid_with_strong_metadata_still_reuses(self):
        # The preserved strong case: the same filename/size/mtime and
        # a REGENERATED uuid — the reliable timestamp is the voucher,
        # so the persisted prepared store still takes the fast path
        # across sessions (no rebuild, no raw re-walk).
        module = self.qt.load("GCodeIndexService")
        self.files.identity.uuid = "uA"
        self.files.identity.modified = 1.0
        self.files.identity.size = 100
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        old_payload = self.store.read(
            "print-key", self.store.load_table("print-key")["table"], 0)

        self.service.close()
        from plugins.PreparedStore import PreparedCache
        self.store = PreparedCache(self._dir.name)
        self.service = module.GCodeIndexService(self.files, object(),
                                                prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = True
        self.service._wanted = True
        self.files.identity.uuid = "uB"  # the regenerated token
        self._view(5)
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved,
                        "the strong metadata never took the fast path")
        self.assertEqual(self.service._full_next, 5)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.service._advance()
        self.assertNotIn("fullprep", submitted,
                         "the strong reuse rebuilt the whole store")
        table = self.store.load_table("print-key")
        self.assertEqual(self.store.read("print-key", table["table"], 0),
                         old_payload,
                         "the reused persistence serves different geometry")

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

    def test_the_lru_surfaces_its_keys_and_accounts_for_a_rewrite(self):
        # The inspection surface (keys, popitem) and a bulk update that
        # rewrites an entry: the old size leaves the total before the new
        # one is charged, or the budget drifts from the truth.
        module = self.qt.load("GCodeIndexService")
        lru = module._ByteBoundedLru(max_bytes=100000)
        lru.set(0, b"x" * 100, 100)
        lru.set(1, b"y" * 50, 50)
        self.assertEqual(list(lru.keys()), [0, 1])
        lru.update({0: b"z" * 300, 2: b"w" * 10})
        self.assertEqual(lru.total_bytes(), 300 + 50 + 10)
        key, value = lru.popitem()
        self.assertEqual((key, len(value)), (2, 10), "the newest entry was not the one dropped")
        self.assertEqual(lru.total_bytes(), 350)
        self.assertEqual(len(lru), 2)

    # --- the worker legs: what the demand's own workers serve and name ---

    def _capture_submit(self):
        """Hold the next submission on this thread: the worker runs where
        the test can read its (failed, stash)/frontier result, and the
        state under it can be posed exactly."""
        captured = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            captured.append((kind, work, lease)) or None)
        self.addCleanup(setattr, self.service, "_submit", original)
        return captured

    def _compact_view(self, layers, hydrated, followed=None):
        index = make_index(layers=layers, motions=20, compact=True)
        index.hydrated_layers = set(hydrated)
        index.followed_layer = followed
        self.service._view = self.qt.load("GCodeIndexService").IndexView(
            self.files.job_key, index)
        return index

    def test_the_hydrate_worker_names_the_layer_it_cannot_decode(self):
        # The worker's own contract: a packed entry the codec refuses is
        # NAMED failed — the latch is what stops every later poll
        # re-reading the same bytes — while the window's other layers
        # stay served. A decode that succeeded is presentation even when
        # the file can no longer supply that layer's motion arrays.
        self._compact_view(3, hydrated=())
        self.service._full_cache.set(0, self._payload(0), 40)
        self.service._full_cache.set(1, b"PPL1\xff", 5)  # truncated: the codec refuses it
        self.service._full_cache.set(2, self._payload(2), 40)
        self.service._hydrate = {0, 1, 2}
        captured = self._capture_submit()
        self.service._advance()
        self.assertEqual([kind for kind, _, _ in captured], ["hydrate"],
                         "the demand's window was not submitted as one task")
        failed, stash = captured[0][1]()
        self.assertEqual(failed, [1], "a refused entry was stashed or a served layer was failed")
        self.assertEqual(sorted(stash), [0, 2], "a decodable payload was dropped with the refusal")
        for layer in (0, 2):
            self.assertIsNotNone(stash[layer][1], "layer %d stashed no payload" % layer)
            self.assertNotIn(layer, failed)

    def test_a_layer_evicted_after_its_submit_is_reported_failed(self):
        # The retention window evicts a layer between the demand's own
        # submit and its worker (the live print's eviction runs on the
        # owner thread). The worker then has neither arrays nor a lease:
        # the layer is named failed, so the latch — never a silent empty
        # — decides whether the poll asks again.
        index = self._compact_view(3, hydrated=(0, 1, 2), followed=1)
        captured = self._capture_submit()
        self.service.request_hydration(1)  # the arrays all still held: no lease is asked
        self.assertEqual([kind for kind, _, _ in captured], ["hydrate"])
        self.assertIsNone(captured[0][2], "an array-less layer demanded the raw file")
        index.hydrated_layers.discard(1)
        self.assertEqual(captured[0][1](), ([1], {}))

    def test_an_evicted_layer_keeps_the_payload_the_cache_holds(self):
        # The same eviction with the packed tier holding the layer: the
        # decode is already paid for and the presentation stands — only
        # the split's arrays are missing — so the worker keeps the
        # payload instead of failing a layer it can still draw.
        index = self._compact_view(3, hydrated=(0, 1, 2), followed=1)
        self.service._full_cache.set(1, self._payload(1), 40)
        captured = self._capture_submit()
        self.service.request_hydration(1)
        self.assertIsNone(captured[0][2], "a packed payload demanded the raw file")
        index.hydrated_layers.discard(1)
        failed, stash = captured[0][1]()
        self.assertEqual(failed, [], "an unhydrated layer lost its decodable payload")
        self.assertIn(1, stash)
        self.assertIsNotNone(stash[1][1])

    def test_a_refused_encode_never_costs_the_layer_its_display(self):
        # The demand's own encode can fail (the codec's refusal): the
        # decoded payload still lands in the stash for the hot cache —
        # a refused encode is a compact-store miss, never a lost layer
        # and never a failed hydrate that would latch the window.
        self._view(3)
        self.service._hydrate = {1}
        captured = self._capture_submit()
        module = self.qt.load("GCodeIndexService")

        def refusing(payload):
            raise ValueError("refused")

        with patch.object(module, "_encode_layer", refusing):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["hydrate"])
            failed, stash = captured[0][1]()
        self.assertEqual(failed, [], "a refused encode failed the layer")
        self.assertEqual(sorted(stash), [1])
        encoded, decoded, ram_hit, size = stash[1]
        self.assertIsNone(encoded, "a refused encode was stashed as bytes")
        self.assertIsNotNone(decoded, "the refusal cost the layer its decoded payload")
        self.assertFalse(ram_hit)

    def test_a_compact_pass_reads_the_file_only_for_the_layer_with_no_source(self):
        # A compact index whose remaining layers are hydrated, RAM packed
        # or already resolved in the prepared table rebuilds its store
        # from what it holds — the walk asks for no lease at all. Only
        # the layer with no other source sends it to the file, and a
        # file that cannot serve that layer leaves the frontier ON it
        # rather than publishing a hole for it.
        #
        # The clock is held throughout: which source each layer is read
        # from is the policy under test, and it must not also need the
        # walk to fit inside 120 ms of a shared runner's real time. The
        # budget's own boundary is pinned by the two tests below.
        self._compact_view(4, hydrated=(0,))
        self.service._full_cache.set(1, self._payload(1), 40)
        writer = self.store.open_for_write("print-key", 4)
        self.store.append_uncacheable(writer, 2)
        self.store.finish_write(writer)
        self.service._prepared_open(self.files.identity)
        self.assertEqual(self.service._prepared_table[2][0], 2, "the refusal never loaded")
        captured = self._capture_submit()
        module = self.qt.load("GCodeIndexService")
        with patch.object(module, "time", _HeldClock()):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["fullprep"])
            self.assertIsNotNone(captured[0][2], "the pass walked to the file with no lease")
            frontier, encoded, uncacheable = captured[0][1]()
        self.assertEqual(frontier, 3, "the pass walked past a layer it could not read")
        self.assertEqual(uncacheable, {2}, "the resolved refusal was re-walked")
        self.assertIn(0, encoded, "the hydrated layer was never prepared")
        table = self.service._prepared_writer["table"]
        self.assertEqual(table[1][0], self.state_cached,
                         "the RAM-packed layer never rode into the rebuild")

    def test_a_queued_pass_spends_its_budget_from_its_own_execution(self):
        # The budget belongs to the WALK, not to the queue. Spent from
        # the submission, a batch the pool served late arrived with its
        # whole slice already gone and returned the frontier it was
        # handed — the load-dependent 0 != 3 on the source-selection
        # pin. The delay is injected through the clock rather than
        # slept, so the claim is about where the budget starts and
        # never about how busy the runner was.
        self._compact_view(4, hydrated=(0,))
        self.service._full_cache.set(1, self._payload(1), 40)
        writer = self.store.open_for_write("print-key", 4)
        self.store.append_uncacheable(writer, 2)
        self.store.finish_write(writer)
        self.service._prepared_open(self.files.identity)
        captured = self._capture_submit()
        module = self.qt.load("GCodeIndexService")
        clock = _HeldClock()
        with patch.object(module, "time", clock):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["fullprep"])
            clock.spend(module._FULL_PREP_BATCH_S * 1.25)
            frontier, _encoded, _uncacheable = captured[0][1]()
        self.assertEqual(frontier, 3,
                         "a queued pass spent its queue delay as its budget")

    def test_a_spent_budget_still_cuts_the_walk(self):
        # The other half of the same contract: the budget still ENDS the
        # walk. Starting it where the batch runs must not make a batch
        # unbounded, or a demand's task would queue behind a whole pass.
        # The slice is spent inside the first layer's own preparation,
        # so the cut lands on an exact frontier rather than on whatever
        # the runner's clock did meanwhile.
        module = self.qt.load("GCodeIndexService")
        self._compact_view(2000, hydrated=range(2000))
        self.service._prepared_open(self.files.identity)
        captured = self._capture_submit()
        clock = _HeldClock()
        real_prepare = module._prepare_layer

        def spend_the_slice(index, layer, should_yield=None):
            payload = real_prepare(index, layer, should_yield)
            clock.spend(module._FULL_PREP_BATCH_S)
            return payload

        with patch.object(module, "time", clock):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["fullprep"])
            with patch.object(module, "_prepare_layer", spend_the_slice):
                frontier, _encoded, _uncacheable = captured[0][1]()
        self.assertEqual(frontier, 1, "the walk ran on past a spent budget")

    def test_a_restore_reraises_the_followers_window(self):
        # A demand that races the restore is dropped at the view-None
        # guard, and the coordinator's re-assertion carries the SAME
        # anchor the idempotency guard swallows — so the follower's own
        # window is re-raised HERE, the moment the view exists (the live
        # report: a future-layer scrub after a restore rendered nothing
        # and the slider stayed disabled).
        index = self.qt.load("GCodeIndex").build_index_from_bytes(
            b"".join(b";LAYER:%d\nG1 X0 Y0 E0.1\n" % layer for layer in range(5)))
        index.followed_layer = 3

        class Cache:
            def load(self, identity):
                return index

        self.service._cache = Cache()
        self.service._restored = False
        self.files.lease = lambda: None  # the raw lease is unavailable
        submitted = []
        demanded = []
        original = self.service._submit

        def capture(kind, work, lease=None):
            submitted.append(kind)
            if kind == "hydrate":
                # The window at submission: the follower's own layer is
                # the current demand, its two ghosts stay queued.
                demanded.append((set(self.service._hydrating), set(self.service._hydrate)))
            return original(kind, work, lease)
        self.service._submit = capture
        for _ in range(400):
            self.service._advance()
            self.qt.events(5)
            if self.service._view is not None:
                break
        self.assertIn("restore", submitted)
        self.assertEqual(self.service._view._index, index, "the restore never installed the view")
        self.assertTrue(demanded, "the restore re-raised no demand at all")
        self.assertEqual(demanded[0], ({3}, {2, 4}),
                         "the restored follower's window was never re-raised")


    def _dense_layer_file(self, lines=60000):
        """ONE layer, and nothing else. The density is the point: the
        batches cover the loop BETWEEN layers, and this is the walk
        INSIDE one — a single uninterrupted interval unless the reader
        gates it, which is what a seek arriving mid-walk waits out."""
        holder = tempfile.mkdtemp(prefix="dense-layer-fixture-")
        # The name is the point. Under an mpf-* name this directory was
        # removed WHILE the test was using it: RemoteFileService's
        # stale-root sweep deletes every mpf-* root with no live pid
        # outright, and a parallel test process constructs that service.
        # It surfaced as the scan finding no file (and, before the
        # fixture asserted it, as a walk that hydrated nothing).
        self.addCleanup(shutil.rmtree, holder, ignore_errors=True)
        path = os.path.join(holder, "dense.gcode")
        with open(path, "w", encoding="ascii") as handle:
            handle.write("M82\n;LAYER:0\n;TYPE:SKIN\n")
            for step in range(lines):
                handle.write("G1 X%d.%03d Y%d.%03d E%.5f\n"
                             % (step % 180, step % 997, (step // 180) % 180,
                                step % 991, step * 0.001))
        self.assertTrue(os.path.exists(path),
                        "the fixture's file vanished before it was read")
        return path

    def test_a_dense_layer_yields_and_an_abandoned_walk_publishes_nothing(self):
        # The reader's own gate, and the guarantee that rides it. The
        # asks are counted, never timed: the gate is consulted on a line
        # counter, so how often it was reached is a structural fact
        # about the walk rather than a reading of the machine.
        path = self._dense_layer_file()
        index = build_index_from_file(path, compact=True)
        lines = 60000
        self.assertEqual(len(index.ranges), 1, "the fixture is not one layer")
        self.assertNotIn(0, index.hydrated_layers)
        module = self.qt.load("GCodeIndex")
        asked = []
        fires = []
        real = module.passive_yield

        def recorded(now, last):
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                fires.append(now)
            return updated

        stops = []

        def stop():
            stops.append(1)
            return len(stops) >= 2          # abandon on the second gate

        with patch.object(module, "passive_yield", recorded):
            with self.assertRaises(module.HydrationYield):
                module.hydrate_layer_from_file(index, path, 0, should_stop=stop)
        # A heartbeat sibling for this walk was withdrawn rather than
        # shipped: on this platform the interpreter's own 5 ms switch
        # interval releases the GIL with the walk's gate disabled
        # (measured), so it could not carry the regression here, and
        # its fixture build came back with NO layers intermittently
        # under the parallel suite — 12/12 correct alone, so the
        # condition needs contention to appear. That signature is
        # recorded as an open question about build_index_from_file
        # rather than papered over with a retry or a skip.
        # The abandoned walk stops early by design, so its count is a
        # floor; the walk that matters is the one below.
        self.assertGreaterEqual(len(asked), 8, "the dense walk never gated")
        self.assertGreaterEqual(len(fires), 2)
        # Prompt, and counted rather than timed: the walk abandons on
        # the consultation that first answers True, not several gates
        # later.
        self.assertEqual(len(stops), 2,
                         "the walk consulted the stop %d times" % len(stops))
        # Abandoned BEFORE the commit: the layer keeps every array it
        # had, which is none of them, and nothing is latched as failed.
        self.assertNotIn(0, index.hydrated_layers,
                         "an abandoned hydration marked the layer hydrated")
        if index.motion_x:
            self.assertEqual(len(index.motion_x[0]), 0,
                             "an abandoned hydration published motion arrays")
        # Non-vacuity, and the structural claim: the same layer walked
        # to the end asks its gate throughout — once per 64 lines, which
        # is a property of the walk rather than of the machine.
        before = len(asked)
        with patch.object(module, "passive_yield", recorded):
            self.assertTrue(module.hydrate_layer_from_file(index, path, 0),
                            "the fixture's layer never hydrates at all")
        self.assertIn(0, index.hydrated_layers)
        self.assertGreater(len(index.motion_x[0]), 1000,
                           "the control walk published no geometry")
        self.assertGreaterEqual(
            len(asked) - before, lines // 64 - 64,
            "the walk to the end asked its gate %d times" % (len(asked) - before))

    def test_the_scan_asks_its_gate_throughout_its_walk(self):
        # The scan's own gate, on the same structural claim the
        # hydration walk is held to. The build loop is a per-line parse
        # of a file that can be megabytes long, and it hands the
        # interpreter back only where it ASKS: a beat coarser than the
        # wall-clock period lets a whole file's worth of lines run
        # between hand-backs, and the gate cannot fire more often than
        # it is consulted. Measured on a 300k-line single layer: 26.5 ms
        # between hand-backs on the old 4096-line beat against 6.7 ms
        # with the 64-line one (p95 25.0 -> 6.4 ms), for ~1% more scan
        # time. The asks are counted rather than timed, so the claim is
        # a property of the walk; the hand-backs are printed as
        # evidence, since a descheduled process cannot hand back a GIL
        # it does not hold.
        path = self._dense_layer_file()
        lines = 60000
        module = self.qt.load("GCodeIndex")
        asked = []
        fires = []
        real = module.passive_yield

        def recorded(now, last):
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                fires.append(now)
            return updated

        with patch.object(module, "passive_yield", recorded):
            index = module.build_index_from_file(path, compact=True)
        self.assertTrue(index.ranges, "the fixture built no layers")
        self.assertGreaterEqual(
            len(asked), lines // 64 - 64,
            "the scan walked %d lines and asked its gate %d times"
            % (lines, len(asked)))
        print("scan gate evidence: %d asks, %d hand-backs over %d lines"
              % (len(asked), len(fires), lines))

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

    @unittest.skipUnless(os.path.isfile("/proc/self/status"),
                         "the RSS budget reads procfs: macOS and Windows have none")
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

    def test_an_encoding_failure_still_charges_the_decoded_tier(self):
        # A layer whose encode failed has no bytes to measure: the charge
        # comes from the payload's own motion count, so the byte budget
        # stays bounded without a second geometry walk.
        charge = self.module._decoded_charge
        self.assertEqual(charge(payload={"motions": 100}), 100 * 256)
        # A count that is absent, unusable or negative is no count at all:
        # the floor is what keeps the accounting honest.
        self.assertEqual(charge(payload={"motions": 0}), self.module._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(payload={"motions": -5}), self.module._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(payload={"motions": "many"}), self.module._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(payload=[1, 2]), self.module._DECODED_CHARGE_FLOOR)
        self.assertEqual(charge(), self.module._DECODED_CHARGE_FLOOR)
        # A packed payload is charged by its expansion, not by a walk of
        # the decoded points it stands for.
        self.assertEqual(charge(raw=b"x" * 1000),
                         1000 * self.module._DECODED_PACKED_EXPANSION)

    def test_a_value_that_refuses_its_size_leaves_the_walk(self):
        # A host object may refuse the size probe; the walk drops that one
        # value and keeps accounting the rest rather than fail the charge.
        class Unmeasurable:
            def __sizeof__(self):
                raise TypeError("no size")

        lru = self.module._ByteBoundedLru(max_bytes=100000)
        lru[0] = {"payload": {"motions": 3}, "extra": Unmeasurable()}
        self.assertGreater(lru.total_bytes(), 0)

    def test_the_combined_bound_never_evicts_a_protected_window(self):
        # The demanded windows are protected: when the pins alone put the
        # tier over budget, the reconcile yields to that floor rather than
        # evict a layer the poll is about to read.
        lru = self.service._decoded_lru
        lru.set(0, object(), 90)
        lru.set(1, object(), 90)
        lru.protected = {0, 1}
        self.service._decoded_sizes[2] = 5000
        self.service.pin_decoded(2)
        self.assertEqual(self.service.pinned_decoded_bytes(), 5000)
        self.assertEqual(len(lru), 2, "a protected window was evicted")
        self.assertIn(0, lru)
        self.assertIn(1, lru)

    def test_the_protection_set_needs_an_index(self):
        # No index names the followed layers, so there is no window to
        # protect and the set is left exactly as it was.
        self.service._decoded_lru.protected = {7}
        self.service._view = None
        self.service._update_decoded_protection()
        self.assertEqual(self.service._decoded_lru.protected, {7})
