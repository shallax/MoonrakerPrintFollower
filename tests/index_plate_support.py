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

import mpf.gcode.GCodeIndex as gcode_index
from mpf.gcode.MotionIndex import LayerMotionIndex
from mpf.gcode.IndexCache import PersistentIndexCache
from mpf.gcode.IndexCodec import _CACHE_MAGIC, _CACHE_VERSION
from mpf.gcode.IndexLimits import _MAX_TYPE_NAMES, _MAX_TYPE_RUNS_PER_LAYER
from mpf.gcode.FeatureTracker import _TYPE_NONE, _TYPE_OTHER
from mpf.gcode.GCodeIndex import build_index_from_bytes, build_index_from_file
from mpf.gcode.IndexHydrator import hydrate_layer_from_file
from mpf.moonraker.MoonrakerProtocol import RemoteFileIdentity
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


class TravelBoundaryTests(unittest.TestCase):
    """The E-axis rule: which motions are travel, and where a travel starts."""


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


class FeatureRetentionTests(unittest.TestCase):
    """The feature columns follow the geometry in and out of the window."""

    DATA = (b"M82\n"
            b";LAYER:0\n;TYPE:WALL\nG1 X1 E1.0\nG1 E0.0\n"
            b";LAYER:1\nG1 X2 E1.0\nG1 X3 E1.1\n"
            b";LAYER:2\n;TYPE:SKIN\nG1 X4 E1.2\nG0 X0 Y0\nG1 X6 E1.25\n"
            b";LAYER:3\nG1 X5 E1.3\n")


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
        index.motion_offsets = [array("Q") for _ in range(layers)]
        index.compact = True
        index.hydrated_layers = set(hydrated)
        view = self.qt.load("IndexView").IndexView(self.job, index)
        self.service._view = view
        return index


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
        view = self.qt.load("IndexView").IndexView(self.job, index)
        self.service._view = view
        # The worker's commit: the decoded payloads land in the hot
        # presentation cache — the bundle reads no other store (the
        # UI thread never prepares or decodes).
        from mpf.gcode.PlateProgress import prepare_layer
        for layer in range(layers):
            self.service._decoded_lru[layer] = prepare_layer(index, layer)
        return list(index.motion_offsets[0])


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
        self.service._view = self.qt.load("IndexView").IndexView(self.job, index)
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
        self.service._view = self.qt.load("IndexView").IndexView(self.job, index)
        self.service._decoded_lru[0] = {
            "classes": {"INFILL": list(runs)}, "travels": [],
            "travelStarts": [], "travelEnds": [], "motions": motions}
        index.layer_motion_counts = [motions]
        index.motion_offsets = [array("Q")]
        return index


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
        self.service._view = self.qt.load("IndexView").IndexView(self.job, index)
        from mpf.gcode.PlateProgress import prepare_layer
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
        return self.qt.load("MotionRefinement").refine_payload(payload, coarse, live, **kwargs)

    @staticmethod
    def _row(y, first, count, x0=0.0, step=1.0):
        """One drawn run: *count* vertices stepping *step* along x at
        height *y*, motion index *first* + i — the payload's own triple."""
        return [[x0 + index * step, y, float(first + index)] for index in range(count)]

    @staticmethod
    def _payload(classes, travels=(), motions=0):
        return {"classes": classes, "travels": list(travels),
                "travelStarts": [], "travelEnds": [], "motions": motions}


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
        view = self.qt.load("IndexView").IndexView(self.job, index)
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
        module = self.qt.load("ObjectVisitTracker")
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


    # The dense fixtures: `motions` straight extruding moves at y = 0,
    # x = motion - 1 -> motion, built in memory (no file), so a
    # 200 000-motion layer costs the walk and nothing else. An object
    # on that path is a narrow strip around x = centre.

    DENSE_MOTIONS = 200000

    def _bind_dense(self, motions, layers=1):
        index = make_index(layers=layers, motions=motions)
        self.service._view = self.qt.load("IndexView").IndexView(self.job, index)
        return index

    @staticmethod
    def _strip(centre, half=25.0):
        return [[centre - half, -5.0], [centre + half, -5.0],
                [centre + half, 5.0], [centre - half, 5.0]]

    def _pin_budget(self, seconds):
        """Pin the walk's owner-thread budget. Zero cuts every poll at
        the walk's own check step, which is what makes a chunked walk
        countable; the default is the production bound."""
        original = self.service._objects._VISITED_WALK_BUDGET_S
        self.service._objects._VISITED_WALK_BUDGET_S = seconds
        self.addCleanup(setattr, self.service._objects, "_VISITED_WALK_BUDGET_S", original)

    def _drive(self, anchor, split, objects, limit=4000):
        """Poll the way the live loop does — fresh rows every tick —
        until the walk has nothing left to consume. Returns ``(polls,
        verdict)`` and fails when the walk never settles."""
        service = self.service
        for polls in range(1, limit + 1):
            verdict = service.plate_visited(anchor, split, self._rows(*objects))
            if (service._objects._visited_upto >= split
                    and service._objects._visited_replay_upto >= service._objects._visited_upto):
                return polls, verdict
        self.fail("the walk never settled after %d polls" % limit)

    def _count_segment_tests(self):
        """Every vertex test the walk runs, recorded."""
        module = self.qt.load("ObjectVisitTracker")
        real = module.segment_in_polygon
        calls = []

        def counted(*args):
            calls.append(args)
            return real(*args)

        patcher = patch.object(module, "segment_in_polygon", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


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
        from mpf.gcode.PreparedStore import PreparedCache, STATE_CACHED
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
        self.service._view = self.qt.load("IndexView").IndexView(
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
            if self.service._prepared.saved and not self.service._busy:
                return
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.fail("the prepared pass did not settle")

    @staticmethod
    def _payload(layer):
        from mpf.gcode.PlateProgress import encode_layer
        return encode_layer({"classes": {"SKIN": [[[0.0, 0.0, 0.0], [1.0, float(layer), 1.0]]]},
                             "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2})


        # The cadence is evidence, not a bound: a median gap is still a
        # reading of the machine's scheduling, and the contract above is
        # what fails when the gate is not consulted.


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
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, index)
        return index


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


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['DecodedBudgetTests', 'FeatureCacheTests', 'FeatureRetentionTests', 'FeatureTypeTests', 'HydrationWindowTests', 'LayerMotionIndex', 'MotionLineFastPathTests', 'PayloadRefinementTests', 'PersistentIndexCache', 'PlateSplitRefinementTests', 'PlateVisitedTests', 'PreparedReopenPolicyTests', 'QT_AVAILABLE', 'RemoteFileIdentity', 'RepeatedGeometrySplitTests', 'SimpleNamespace', 'TravelBoundaryTests', '_CACHE_MAGIC', '_CACHE_VERSION', '_HEARTBEAT_INTERVAL_MS', '_HeldClock', '_LINES_PER_PASS', '_MAX_TYPE_NAMES', '_MAX_TYPE_RUNS_PER_LAYER', '_TYPE_NONE', '_TYPE_OTHER', '_YIELD_MAX_GAP_S', '_codes', '_nozzle_at', '_repeated_layer_gcode', '_write_gcode', 'annotations', 'array', 'build_index_from_bytes', 'build_index_from_file', 'gcode_index', 'gzip', 'hydrate_layer_from_file', 'json', 'make_index', 'math', 'os', 'pairwise', 'patch', 'runtime', 'shutil', 'struct', 'sys', 'tempfile', 'threading', 'time', 'unittest']
