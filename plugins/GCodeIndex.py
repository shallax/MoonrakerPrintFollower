from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import threading
import time

from .CachePolicy import evict_to_budget
from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import BinaryIO, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from UM.Logger import Logger as _Logger
except ImportError:
    _Logger = None  # the host stdlib suite has no UM


def _log(message, *args):
    """The persistence diagnostics, at the DECISION points only
    (never per layer): an INFO line names the reason a restore or an
    eviction happened. The host stdlib suite runs without UM — the
    log no-ops there."""
    if _Logger is not None:
        _Logger.log("i", message, *args)

from . import ArcGeometry
from .MoonrakerProtocol import RemoteFileIdentity


_LAYER_COMMENT = re.compile(rb"^\s*;LAYER:\s*-?\d+\s*$", re.IGNORECASE)
_CURA_LAYER_VALUE = re.compile(rb"^\s*;LAYER:\s*(-?\d+)\s*$", re.IGNORECASE)
_ORCA_LAYER = re.compile(rb"^\s*;\s*layer\s+num/total_layer_count:\s*\d+\s*/\s*\d+\s*$", re.IGNORECASE)
_ORCA_LAYER_VALUE = re.compile(rb"^\s*;\s*layer\s+num/total_layer_count:\s*(\d+)\s*/\s*\d+\s*$", re.IGNORECASE)
_PRUSA_LAYER_CHANGE = re.compile(rb"^\s*;LAYER_CHANGE\s*$", re.IGNORECASE)
_STATS_MARKER = re.compile(
    rb"^\s*SET_PRINT_STATS_INFO\b.*\bCURRENT_LAYER\s*=\s*(-?\d+)",
    re.IGNORECASE,
)
_MOTION = re.compile(rb"^\s*(?:N\d+\s*)?G0?[0-3](?!\d)", re.IGNORECASE)
_ELAPSED = re.compile(rb"^\s*;TIME_ELAPSED:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
_COMMAND = re.compile(rb"^\s*(?:N\d+\s*)?([GMT]\d+)(?!\d)", re.IGNORECASE)
# Only the XYZE words are read here: an arc's centre offsets are not
# positions and never move the XYZ state, so they are parsed separately
# (_ARC_WORD) and only on a G2/G3 line.
_AXIS = re.compile(rb"([XYZE])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
# An arc's I/J/K centre offsets, and the R a radius-form arc would carry
# (Klipper rejects that form; the R is what tells the two apart).
_ARC_WORD = re.compile(rb"([IJKR])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)
# The motion words that describe an arc, in every spelling the motion
# regex accepts: G2/G02 and G3/G03. Both spellings are arcs to the
# parser, so both must be arcs to the geometry as well.
_ARC_CLOCKWISE = frozenset((b"G2", b"G02"))
_ARC_COUNTER = frozenset((b"G3", b"G03"))
# The slicer's feature marker. Leading whitespace is tolerated: a
# post-processed or macro-generated file does not always write it at
# column 0, and a missed marker silently mis-colours a whole block. The
# value runs to the line's end (a marker with no value at all is not one).
# The prefix is the per-line filter: the marker is a whole-line comment,
# so nothing else on a move line can start with it, and testing that is
# several times cheaper than searching every line for the substring.
_TYPE_PREFIX = b";TYPE:"
_TYPE_COMMENT = re.compile(rb"^\s*;TYPE:\s*(\S.*?)\s*$")
# A baked end-of-layer pause: the pause command word standing alone at
# the line start (comment lines never match). The PauseAtHeight
# post-processor emits the configured pause command inside its
# ;TYPE:CUSTOM block at the END of the target layer's moves, so the
# line's offset resolves to that layer through the block ranges.
_PAUSE_COMMAND = re.compile(rb"^\s*(?:PAUSE|M0|M25)\b")

# The background workers' passive yield. Their loops are tight and their
# per-item work is small, so nothing but an explicit hand-back stops a
# worker holding the GIL for a whole pass. The gate is WALL-CLOCK, never
# an iteration count: one layer's cost varies by orders of magnitude
# across files, and a count that frees the UI thread on a sparse file
# starves it on the dense ones where it matters. The sleep is a real
# syscall on Windows, where an unscheduled hand-back does not reliably
# wake a waiting thread, and a bare yield elsewhere, where it does.
_PASSIVE_YIELD_S = 0.006
_YIELD_SLEEP_S = 0.001 if sys.platform == "win32" else 0.0


def passive_yield(now: float, last: float) -> float:
    """Hand the interpreter back once per _PASSIVE_YIELD_S of wall time.

    *last* is the caller's own watermark and the return is the updated
    one — each loop keeps it in its own frame, so the gate costs one
    comparison on the iterations that do not fire.
    """
    if now - last < _PASSIVE_YIELD_S:
        return last
    time.sleep(_YIELD_SLEEP_S)
    return time.monotonic()


class HydrationYield(Exception):
    """Cooperative interruption of a layer hydration.

    The reader walks a whole layer in one loop, so a dense layer is one
    long interval between hand-backs — the interval a foreground seek
    waits out. Nothing has been published when this is raised: the
    arrays commit under the index's lock after the walk, so an
    interrupted layer is still unhydrated and simply reached again.

    It is not PlateProgress's PreparationYield because that module
    imports this one.
    """


# The reader's own gate: the clock call and the cancellation check are
# kept out of the per-line path by a counter, and the counter is small
# enough that even a slow line cannot stretch the interval past a few
# of the yield gate's periods.
_HYDRATE_YIELD_MASK = 63

_CACHE_MAGIC = b"MPFI110\0"
# The header is a length-prefixed JSON blob inside the container, so the
# reader's bound is also the writer's: a longer header is a blob the
# loader refuses, and writing one would only spend the cache's budget on
# a file that can never be read back.
_MAX_CACHE_HEADER_BYTES = 16 * 1024 * 1024
# The feature columns' serialization budget (a run or a marker costs
# ~10 bytes of JSON): a hostile file can fragment every layer into a run
# list of its own, so the columns are bounded on top of the per-layer
# caps. Past the budget they are dropped WHOLE — a truncated run list
# would restore a layer's colours wrong, while an absent one only reads
# as "not recorded".
_MAX_CACHE_FEATURE_ENTRIES = 500_000
# The arc columns' serialization budget, in descriptors: the JSON header
# itself is the bound (one descriptor is ~25 bytes and the header may not
# exceed _MAX_CACHE_HEADER_BYTES). It is ALL OR NOTHING: a blob past it
# is not published, and one offered to the loader is refused, because a
# cache missing descriptors restores an index that draws those arcs as
# chords — geometry the file never commanded, silently, for the life of
# the cache entry. The G0/G1 path pays nothing for the budget: the
# column is sparse, one entry per arc, and a file without arcs keeps
# caching with no arc budget at all.
_MAX_CACHE_ARC_ENTRIES = 200_000
# 10: the arc columns are all-or-nothing. 9 could publish an index whose
# descriptors the entry budget had dropped, and a dropped column is
# indistinguishable from a file that never had arcs, so every 9 blob is
# refused rather than read back as an arc-free one.
# 9: the sparse per-motion arc descriptors and the layer-start arc plane.
# 8 restored feature columns but indexed G2/G3 by endpoint, so a 8 blob
# would draw every arc as its chord — the version refuses it outright.
# 11: the per-layer motion counts changed semantics (born from the
# build walk, not the hydrated arrays) — every older cache's
# counts may read zero for never-hydrated layers, which is exactly
# the resumed-session dead-slider report; refusing them rebuilds.
_CACHE_VERSION = 11
_LARGE_FILE_COMPACT_THRESHOLD = 128 * 1024 * 1024
# Hardening bounds for hostile/corrupt gcode (panel security P2-4): a
# real gcode line is well under 1 KB, real prints stay under ~100k
# layers, and no single layer carries more than a few hundred thousand
# motions. Past a bound the index DEGRADES to the coarser fallbacks
# (byte-range fraction, last known layer) instead of growing structures
# without limit — a poisoned file on the printer must not OOM Cura.
_MAX_LINE_BYTES = 64 * 1024
_MAX_LAYER_BLOCKS = 100_000
_MAX_MOTIONS_PER_LAYER = 200_000
# The feature columns have their own bounds: a hostile file can write a
# distinct ;TYPE: value (or alternate travel and print every motion) on
# every line, and neither the vocabulary nor the run list may grow with
# the line count. Past the vocabulary cap every further distinct name is
# _TYPE_OTHER; past the run cap the layer's tail is _TYPE_OTHER too —
# coarser, never wrong in a way the caller cannot see.
_MAX_TYPE_NAMES = 64
_MAX_TYPE_NAME_BYTES = 64
_MAX_TYPE_RUNS_PER_LAYER = 4096
_TYPE_NONE = 0
_TYPE_OTHER = 1
# The extrusion rule is the G-code's own: a move with a POSITIVE E step
# extrudes, anything else (no E, a flat E, a falling E) deposits nothing
# and is travel. No magnitude floor — the slicer already decided; a
# floor misread slow extrusion as travel twice (the 0.05 floor ate the
# live file's fine walls, the epsilon ate a 0.05 mm layer height's
# short skin segments: the live reports).
# The layer-format sniff window: the file head read before the scan.
_MARKER_SNIFF_BYTES = 262144
# How far below the monotonic floor the refinement search may start, in
# motions. Generous enough to cover a parser-chunk lead and any earlier
# floor overshoot; the monotonic clamp is applied to the result.
FLOOR_LOOKBACK = 256
# The fraction-to-motion-count projection's tolerance. A boundary held
# in COUNT units round-trips through the fraction as a division and can
# land a hair under its own integer (1/3 * 3 == 0.9999999999999999);
# truncating that would drop a motion the boundary already counted. Far
# below one motion's share of the layer, so it can never round a
# half-finished motion up.
_SPLIT_EPSILON = 1e-9


@dataclass
class LayerMotionIndex:
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    motion_offsets: List[array] = field(default_factory=list)
    motion_x: List[array] = field(default_factory=list)
    motion_y: List[array] = field(default_factory=list)
    motion_z: List[array] = field(default_factory=list)
    # The sparse arc descriptors, one mapping per layer: motion index ->
    # (plane, clockwise, offset-a, offset-b). A G2/G3 stays ONE motion —
    # its index, its E, its feature and its ownership are the same as any
    # G1's — and the descriptor is what says the head curved on the way.
    # Everything else (a real file's motions, overwhelmingly) is absent
    # from the mapping, which is why it is a mapping and not three more
    # per-motion arrays. The physical geometry is derived from it on
    # demand by ArcGeometry, never stored here.
    motion_arcs: List[Dict[int, tuple]] = field(default_factory=list)
    # The modal arc plane (17/18/19) at each layer's first motion. The
    # plane is modal across the whole file, so a G18 in the start g-code
    # still governs layer 50, and a compact hydration that began at that
    # layer would otherwise parse its arcs as XY.
    layer_start_arc_plane: List[int] = field(default_factory=list)
    # The per-motion feature type, one RLE run list per layer: runs of
    # [motion_count, code] in motion order, so a layer costs one run per
    # ;TYPE: block instead of a byte per motion (~3 KB against ~0.44 MB
    # on a 444k-motion file) and the cache never widens its byte body.
    # Code _TYPE_NONE is "no ;TYPE: seen yet", _TYPE_OTHER the vocabulary
    # overflow, and code n + 2 names type_names[n].
    motion_types: List[List[List[int]]] = field(default_factory=list)
    # The ;TYPE: values in first-seen order. Shared by every layer, so one
    # feature keeps one code (and one colour) for the whole print. The
    # per-motion code defaults to the last value seen — a slicer emits a
    # marker when the feature changes, not per move.
    type_names: List[str] = field(default_factory=list)
    # Travel boundaries from the E axis, as motion indices per layer: a
    # start is the motion where E left the extruding state (holding flat,
    # or falling for a retraction) and an end the motion where E resumed
    # rising. The glyphs are exactly these two lists; the travel segment
    # between a start and its end is theirs to pair (a travel that crosses
    # a layer boundary leaves its start in the previous layer, which is
    # what layer_start_extruding tells apart).
    travel_starts: List[List[int]] = field(default_factory=list)
    travel_ends: List[List[int]] = field(default_factory=list)
    layer_start_positions: List[Tuple[float, float, float]] = field(default_factory=list)
    layer_start_absolute: List[bool] = field(default_factory=list)
    layer_start_units: List[float] = field(default_factory=list)
    # The feature state at each layer's first motion, the compact
    # hydration's seed: without it a hydrated layer parses its opening
    # moves from a cold start and disagrees with the full scan it stands
    # in for (an absolute-E file reads its first move as a huge extrusion,
    # and a travel crossing the boundary loses its end marker).
    layer_start_types: List[int] = field(default_factory=list)
    layer_start_e: List[float] = field(default_factory=list)
    layer_start_e_absolute: List[bool] = field(default_factory=list)
    layer_start_extruding: List[bool] = field(default_factory=list)
    current_layer_map: Dict[int, int] = field(default_factory=dict)
    layer_elapsed_times: List[Optional[float]] = field(default_factory=list)
    # The layers whose gcode carries a baked pause command (PAUSE / M0 /
    # M25 as the line's command word) — 0-based layer indices, in
    # ascending order.
    pauses: Tuple[int, ...] = ()
    compact: bool = False
    hydrated_layers: set[int] = field(default_factory=set, repr=False)
    # One int per layer: the motion count as of the last hydration or
    # build. Eviction wipes the motion arrays (the retention bound's
    # whole point) but never this — a seek to an evicted layer still
    # resolves its FULL split and slider total instantly.
    layer_motion_counts: List[int] = field(default_factory=list)
    # The LIVE print's layer — the retention window's anchor, updated
    # by the service every poll even when that layer is already
    # hydrated. Runtime state: never saved to or restored from the
    # cache.
    followed_layer: Optional[int] = field(default=None, repr=False)
    # The follower's FROZEN layer (the pop-over's detach): a second
    # retention anchor, kept beside the live one. Runtime state, like
    # followed_layer — never saved to or restored from the cache.
    manual_anchor: Optional[int] = field(default=None, repr=False)
    cache_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __bool__(self) -> bool:
        return bool(self.ranges)

    def layer_count(self) -> int:
        return len(self.ranges)

    def motion_count(self, layer: int) -> int:
        if layer < 0:
            return 0
        if layer < len(self.layer_motion_counts):
            return self.layer_motion_counts[layer]
        if layer < len(self.motion_offsets):
            return len(self.motion_offsets[layer])
        return 0

    def file_fraction(self, layer: int, file_position: int) -> Tuple[float, str]:
        if layer < 0 or layer >= len(self.ranges):
            return 0.0, "no index"
        start, end = self.ranges[layer]
        if end <= start:
            return 0.0, "invalid range"
        position = max(start, min(int(file_position), end))
        motions = self.motion_offsets[layer]
        if motions:
            return max(0.0, min(1.0, bisect_right(motions, position) / len(motions))), "motion index"
        return max(0.0, min(1.0, (position - start) / (end - start))), "byte position"

    def refined_fraction(
        self,
        layer: int,
        file_position: int,
        live_position: Optional[Sequence[float]],
        *,
        lag_window: int = 1024,
        ahead_window: int = 8,
        max_distance_mm: float = 3.0,
        minimum_fraction: Optional[float] = None,
    ) -> Tuple[float, str]:
        """Estimate physical progress using Moonraker's live tool position.

        ``file_position`` remains the authoritative coarse locator.  We only
        search a bounded neighbourhood around that location, biased backwards
        because Klipper's parser/lookahead is normally ahead of the physical
        nozzle.  If no nearby motion segment plausibly matches the live tool
        position, the last refined value is held when one exists, so the
        monotonic floor is never inflated by the parser-position fraction;
        on the first observation of a layer there is nothing to hold and the
        parser-position fraction is the only estimate available.
        """

        base_fraction, base_method = self.file_fraction(layer, file_position)
        floor_fraction: Optional[float] = None
        if minimum_fraction is not None:
            try:
                floor_fraction = max(0.0, min(1.0, float(minimum_fraction)))
            except (TypeError, ValueError):
                floor_fraction = None

        def with_floor(fraction: float, method: str) -> Tuple[float, str]:
            if floor_fraction is not None and fraction < floor_fraction:
                return floor_fraction, f"{method} (monotonic)"
            return fraction, method

        if live_position is None or len(live_position) < 3:
            return with_floor(base_fraction, base_method)
        if layer < 0 or layer >= len(self.motion_offsets):
            return with_floor(base_fraction, base_method)

        offsets = self.motion_offsets[layer]
        xs = self.motion_x[layer] if layer < len(self.motion_x) else array("f")
        ys = self.motion_y[layer] if layer < len(self.motion_y) else array("f")
        zs = self.motion_z[layer] if layer < len(self.motion_z) else array("f")
        n = len(offsets)
        if n == 0 or len(xs) != n or len(ys) != n or len(zs) != n:
            return with_floor(base_fraction, base_method)

        try:
            px, py, pz = float(live_position[0]), float(live_position[1]), float(live_position[2])
        except (TypeError, ValueError):
            return with_floor(base_fraction, base_method)

        coarse_completed = bisect_right(offsets, int(file_position))
        lo = max(0, coarse_completed - max(1, int(lag_window)) - 1)
        if floor_fraction is not None:
            # Live XYZ can match more than one place on a closed/repeated toolpath.
            # The monotonic clamp is applied to the *result* below, which is what
            # prevents rewind; the search may dip a bounded distance below the
            # floor so an inflated floor sample can never exclude the true
            # segment (which previously cascaded into permanent coarse fallback).
            floor_completed = max(0, min(n, int(math.floor(floor_fraction * n))))
            lo = max(lo, max(0, floor_completed - FLOOR_LOOKBACK - 1))
        hi = min(n - 1, coarse_completed + max(0, int(ahead_window)))
        if hi < lo:
            return with_floor(base_fraction, base_method)

        layer_start = (
            self.layer_start_positions[layer]
            if layer < len(self.layer_start_positions)
            else (float(xs[0]), float(ys[0]), float(zs[0]))
        )
        # A motion that commanded an arc is matched against the arc it
        # commanded, not against its endpoint chord: the nozzle is on the
        # curve for the whole move, so a chord reading would call the
        # true path "off-model" and hold the coarse fraction instead.
        # The lookup is skipped entirely for a layer with no arcs.
        arcs = self.motion_arcs[layer] if layer < len(self.motion_arcs) else None

        best_distance_sq = float("inf")
        best_completed = None
        for i in range(lo, hi + 1):
            if i == 0:
                ax, ay, az = layer_start
            else:
                ax, ay, az = float(xs[i - 1]), float(ys[i - 1]), float(zs[i - 1])
            bx, by, bz = float(xs[i]), float(ys[i]), float(zs[i])
            arc = arcs.get(i) if arcs else None
            if arc is not None:
                hit = ArcGeometry.closest(arc, (ax, ay, az), (bx, by, bz), (px, py, pz))
                if hit is not None:
                    distance, t = hit
                    if distance * distance < best_distance_sq:
                        best_distance_sq = distance * distance
                        best_completed = i + t
                    continue
            dx, dy, dz = bx - ax, by - ay, bz - az
            length_sq = dx * dx + dy * dy + dz * dz
            if length_sq <= 1e-12:
                t = 1.0
                qx, qy, qz = bx, by, bz
            else:
                t = ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / length_sq
                t = max(0.0, min(1.0, t))
                qx, qy, qz = ax + t * dx, ay + t * dy, az + t * dz
            ddx, ddy, ddz = px - qx, py - qy, pz - qz
            distance_sq = ddx * ddx + ddy * ddy + ddz * ddz
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_completed = i + t

        if best_completed is None or math.sqrt(best_distance_sq) > max(0.1, max_distance_mm):
            # The live position is off-model (Z-lift, off-path movement) or
            # ambiguous. Hold the last refined value instead of jumping to the
            # parser-position fraction, which sits ahead of the nozzle and
            # inflates the monotonic floor into a cm-apart staircase. Without
            # a prior value (the first observation of a layer) the coarse
            # fraction is the only estimate available and seeds the floor.
            if floor_fraction is not None:
                return floor_fraction, "held (refined unavailable)"
            return with_floor(base_fraction, base_method)

        refined = max(0.0, min(1.0, float(best_completed) / n))
        return with_floor(refined, "live position")

    def refined_split(
        self,
        layer: int,
        file_position: int,
        live_position: Optional[Sequence[float]],
        *,
        minimum_split: Optional[int] = None,
        **kwargs,
    ) -> Tuple[Optional[int], str]:
        """The follower's printed/unprinted boundary from the live tool
        position: ``refined_fraction``'s answer, floored onto this
        layer's motion grid.

        Edge m is printed exactly when m < the returned count, so the
        boundary is the number of motions the NOZZLE has finished, not
        the number the parser has dispatched — the two differ by the
        lookahead, which is what made the painted fill run ahead of the
        head. The search itself is ``refined_fraction``'s (one
        algorithm: the window, the arc geometry, the off-model hold),
        so the plate and the Preview agree about where the head is.

        ``None`` means the live position could not refine — no
        telemetry, an off-model head with nothing yet to hold, or a
        layer the index cannot measure — and the caller keeps its own
        coarse boundary. The refinement corrects a boundary; it never
        invents one. ``minimum_split`` is the boundary already painted,
        and no result ever falls below it.
        """
        n = self.motion_count(layer)
        if n <= 0:
            return None, "no motions"
        floor_fraction = None
        if minimum_split is not None:
            try:
                floor_fraction = max(0.0, min(1.0, max(0, int(minimum_split)) / n))
            except (TypeError, ValueError):
                floor_fraction = None
        fraction, method = self.refined_fraction(
            layer, file_position, live_position, minimum_fraction=floor_fraction, **kwargs
        )
        if not (method.startswith("live position") or method.startswith("held")):
            # The coarse estimate (the parser's own position) or a
            # refusal: the caller's boundary is at least as good.
            return None, method
        # Flooring the fraction is what keeps the fill behind the head:
        # a motion counted as complete is one the nozzle has finished.
        split = int(math.floor(fraction * n + _SPLIT_EPSILON))
        if minimum_split is not None:
            split = max(split, int(minimum_split))
        return max(0, min(n, split)), method


def _parse_axes(code: bytes) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for match in _AXIS.finditer(code):
        try:
            values[match.group(1).decode("ascii").upper()] = float(match.group(2))
        except (UnicodeDecodeError, ValueError):
            continue
    return values


# The fast motion front's grammar: exactly what the three per-line
# regexes accept for the shape slicers emit for ~every motion line —
# b'G0'-b'G3' at column 0, uppercase, then space-separated axis words
# whose letter sits against its number. Any other shape returns None
# and the caller keeps the regex path (the parity contract: the fast
# path never reinterprets a line, it only claims the safe subset).
_AXIS_LETTERS = (88, 89, 90, 69)   # X Y Z E
_AXIS_LOWER = (120, 121, 122, 101)  # x y z e
_FAST_MOTIONS = (b"G0", b"G1", b"G2", b"G3")


def _fast_motion_line(stripped: bytes) -> Optional[Tuple[bytes, Dict[str, float]]]:
    """The cheap front for the dominant motion line shape (the seek
    profile: the three regexes cost most of the raw hydrate's second).
    ``stripped`` is the line's code part (the ``;`` comment already
    cut). Returns (command, axes) only when the line is exactly the
    safe shape; None always means the caller's regex path decides."""
    if len(stripped) < 3 or stripped[0] != 71:  # b'G'
        return None
    digit = stripped[1]
    if not 48 <= digit <= 51:  # b'0'..b'3' — G4/G10+ and everything else fall back
        return None
    third = stripped[2:3]
    if third not in (b"", b" ", b"\t"):
        return None  # G1X5-style crowding is the regex path's business
    tokens = stripped.split()
    axes: Dict[str, float] = {}
    for tok in tokens[1:]:
        if len(tok) < 2:
            if tok and tok[0] in _AXIS_LETTERS + _AXIS_LOWER:
                return None  # a spaced axis word — the regex reads it
            continue  # lone I/J/F/R words carry no axis value
        letter = tok[0]
        if letter in _AXIS_LOWER:
            return None  # lowercase words are the regex path's
        if letter not in _AXIS_LETTERS:
            continue
        if tok[1] not in b"+-.0123456789" or b"_" in tok:
            return None  # float() accepts shapes the regex grammar refuses
        try:
            axes[chr(letter)] = float(tok[1:])
        except ValueError:
            return None
    return b"G" + bytes((digit,)), axes


def _parse_arc_words(code: bytes) -> Dict[str, float]:
    """An arc line's I/J/K offsets (and its R, if it carries one).

    These are NOT positions: an I or a J describes where the centre sits
    relative to the move's start, so reading them as axes would move the
    head sideways and corrupt every following edge. They are parsed on
    their own, only on a G2/G3 line, and the arc plane decides which two
    of them apply (see ArcGeometry.descriptor).
    """
    values: Dict[str, float] = {}
    for match in _ARC_WORD.finditer(code):
        try:
            values[match.group(1).decode("ascii").upper()] = float(match.group(2))
        except (UnicodeDecodeError, ValueError):
            continue
    return values


class _FeatureTracker:
    """The per-motion feature state, shared by the scan and the hydrator.

    The E axis decides what a motion is: a rise above the noise floor is an
    extrusion, anything else deposits nothing and is travel — a *falling* E
    is the same rule's retraction case, not a separate class. Each change
    in that state is a travel boundary, recorded as the motion index it
    happened on: a start where the extrusion stopped, an end where it
    resumed.

    The two parse loops must classify a motion identically — a hydrated
    layer that disagreed with the full scan it stands in for would draw a
    different preview — so the rule lives here once. The modal state (E,
    its absolute/relative mode, the open travel, the feature type, the arc
    plane) survives the layer boundaries, and ``open_layer`` hands the
    layer's own seed on to the compact hydrator, which starts mid-file with
    nothing else. The arc plane belongs here for the same reason as E: G17
    /G18/G19 is modal across the whole file, so a plane chosen long before
    a layer still decides what that layer's G2/G3 means.

    The layer's type runs are built here as well, but only a ``;TYPE:``
    marker writes one: the per-motion walk ticks a counter, and
    ``payload`` turns it into a run when the layer is read out.
    """

    __slots__ = ("runs", "starts", "ends", "count", "last_type", "span",
                 "open_type", "e", "absolute_e", "extruding", "start_type",
                 "start_e", "start_e_absolute", "start_extruding", "plane",
                 "start_plane")

    def __init__(self) -> None:
        self.runs: List[List[int]] = []
        self.starts: List[int] = []
        self.ends: List[int] = []
        self.count = 0
        self.last_type = _TYPE_NONE
        # The open run, held as (count, code) slots rather than as the
        # last element of ``runs``.
        self.span = 0
        self.open_type = _TYPE_NONE
        self.e = 0.0
        self.absolute_e = True
        # Nothing has been deposited before the first motion, but no
        # travel is open either: the first rise is a motion, not a
        # boundary.
        self.extruding = True
        self.start_type = _TYPE_NONE
        self.start_e = 0.0
        self.start_e_absolute = True
        self.start_extruding = True
        # G17 is the default plane: a file that never selects one is XY.
        self.plane = ArcGeometry.PLANE_XY
        self.start_plane = ArcGeometry.PLANE_XY

    def open_layer(self) -> None:
        """Seed a new layer from the modal state and reset the counters."""
        self._flush()
        self.start_type = self.last_type
        self.start_e = self.e
        self.start_e_absolute = self.absolute_e
        self.start_extruding = self.extruding
        self.start_plane = self.plane
        self.runs = []
        self.starts = []
        self.ends = []
        self.count = 0
        self.span = 0
        self.open_type = self.last_type

    def set_type(self, code: int) -> None:
        """Adopt a ;TYPE: value, closing the run a different one opened.

        The type changes only on these markers, so this is the only place
        a run boundary can fall — the motion walk never writes the run
        list itself. A repeat of the current value (two names that both
        overflow the vocabulary share a code, and a slicer may re-state
        one) continues the open run instead of splitting it.
        """
        if code == self.last_type:
            return
        self._flush()
        self.last_type = code
        self.open_type = code

    def payload(self) -> Tuple[List[List[int]], List[int], List[int]]:
        """The layer's feature arrays, with the open run closed."""
        self._flush()
        return (self.runs, self.starts, self.ends)

    def _flush(self) -> None:
        """Materialize the open run — the count side of the RLE.

        Deferred on purpose: reaching into ``runs[-1]`` on every motion
        was the largest cost the feature walk added to the scan, while a
        slot increment is small. Only a type change or a layer boundary
        pays for the list, and those are per feature block, not per move.
        """
        span = self.span
        if not span:
            return
        self.span = 0
        runs = self.runs
        if len(runs) < _MAX_TYPE_RUNS_PER_LAYER:
            runs.append([span, self.open_type])
        else:
            # Run-capped: the tail's feature reads unknown rather than as
            # whatever block happened to be last.
            runs[-1][1] = _TYPE_OTHER
            runs[-1][0] += span

    def add(self, axes: Dict[str, float], collect: bool) -> None:
        """Advance one motion's E and feature state.

        *collect* is False when the motion is not being recorded in the
        layer's arrays — a compact scan, or past the per-layer motion cap.
        The modal state advances either way, because the next layer's seed
        is read from it; only the appends stand down.
        """
        delta = 0.0
        if "E" in axes:
            value = axes["E"] if self.absolute_e else self.e + axes["E"]
            delta = value - self.e
            self.e = value
        rolling = delta > 0.0
        if rolling != self.extruding:
            if collect:
                (self.ends if rolling else self.starts).append(self.count)
            self.extruding = rolling
        if collect:
            self.span += 1
        self.count += 1


def _emit_progress(handle: BinaryIO, progress) -> None:
    # The scanner's honest precision: the file offset against the
    # size. Every 4096 lines, so the callback stays cheap.
    try:
        size = os.fstat(handle.fileno()).st_size
        progress(min(1.0, handle.tell() / max(1, size)))
    except (OSError, ValueError):
        pass

def build_index_from_file(path: str, cancel_event=None, compact: Optional[bool] = None, progress=None, stage=None) -> LayerMotionIndex:
    # ONE pass (the ruling): a single read collects the
    # layer ranges, the marker values, the block stats, the motions
    # AND the pause offsets. The old four-pass build re-read the
    # file per concern, which restarted the progress bar per pass
    # and quadrupled the I/O on large files. The markers are checked
    # per line in the sniffed order, so the sniffed format still
    # wins cheaply and the fallbacks still catch files whose markers
    # the sniff window missed.
    if stage is not None:
        stage("Scanning layers")
    if compact is None:
        try:
            compact = os.path.getsize(path) >= _LARGE_FILE_COMPACT_THRESHOLD
        except OSError:
            compact = False

    markers = (
        (_LAYER_COMMENT, _CURA_LAYER_VALUE),
        (_ORCA_LAYER, _ORCA_LAYER_VALUE),
        (_PRUSA_LAYER_CHANGE, None),
        (_STATS_MARKER, _STATS_MARKER),
    )
    # Sniff the layer-change format from the file head so the matching
    # marker is checked first per line; the rest stay in the fallback
    # order.
    try:
        with open(path, "rb") as probe:
            # The scan matches per line (the markers are
            # line-anchored), so the sniff must too: a raw blob search
            # would miss the layer markers and hand the first check to
            # the stats marker.
            head_lines = probe.read(_MARKER_SNIFF_BYTES).splitlines()
        sniffed = next((marker for marker, _capture in markers
                        if any(marker.match(line) for line in head_lines)), None)
    except OSError:
        sniffed = None
    captures = dict(markers)
    # Which marker opens layer blocks — decided ONCE per file (the
    # critic's catch): the sniffed marker, else the earliest-ordered
    # marker that matches ANY line. A fast census pass reads only the
    # marker regexes (no block state), so the scan can react to the
    # single winner — the old per-line take-over was not retroactive
    # and mis-attributed an earlier-ordered marker's lines to a
    # later one's blocks when the file primed past the sniff window.
    winner = sniffed
    if winner is None:
        try:
            with open(path, "rb") as probe:
                for line in probe:
                    for marker, _capture in markers:
                        if marker.match(line.rstrip(b"\r\n")):
                            winner = marker
                            break
                    if winner is not None:
                        break
        except OSError:
            winner = None

    blocks: List[dict] = []
    current: Optional[dict] = None
    stats_values: List[int] = []
    marker_values: List[int] = []
    pause_offsets: List[int] = []
    absolute_xyz = True
    units_scale = 1.0
    x = y = z = 0.0
    line_number = 0
    collect_motions = not compact
    # The feature walk runs for every build, compact included: the scan
    # still sees the ;TYPE: lines and the E words, and a compact index
    # keeps only each layer's opening state for the hydrator to resume
    # from.
    features = _FeatureTracker()
    type_lookup: Dict[str, int] = {}
    type_names: List[str] = []

    yield_at = time.monotonic()
    with open(path, "rb") as handle:
        while True:
            if cancel_event is not None and (line_number & 0x3FF) == 0 and cancel_event.is_set():
                return LayerMotionIndex()
            if (line_number & 0xFFF) == 0 and line_number:
                # Release the GIL on the workers' own wall-clock gate:
                # the parse is a tight Python loop, and what starves the
                # UI thread is the TIME between hand-backs, not the line
                # count that separates them. The same beat reports the
                # byte-offset progress.
                yield_at = passive_yield(time.monotonic(), yield_at)
                if progress is not None:
                    _emit_progress(handle, progress)
            offset = handle.tell()
            line = handle.readline(_MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > _MAX_LINE_BYTES:
                # A hostile/corrupt file with no newlines would load
                # a giant "line" into RAM and regex-scan it;
                # truncated garbage chunks simply match nothing and
                # are skipped.
                line = b""
            stripped = line.rstrip(b"\r\n")

            stats_match = _STATS_MARKER.search(stripped)
            if stats_match is not None:
                try:
                    value = int(stats_match.group(1))
                    if not stats_values or stats_values[-1] != value:
                        stats_values.append(value)
                    # Record the first CURRENT_LAYER seen inside each
                    # layer block. A global consecutive-value
                    # heuristic cannot tell a leading start-gcode
                    # value (CURRENT_LAYER=0 before the first
                    # ;LAYER) from the first layer's own value.
                    if current is not None and current["end"] is None and current["stats"] is None:
                        current["stats"] = value
                except (TypeError, ValueError):
                    pass

            # The layer-marker line: the sniffed format first, the
            # fallbacks in order. Only the ACTIVE marker opens
            # blocks — the old per-pass loop broke on the first pass
            # that found ranges, so a later format's lines (e.g. the
            # stats marker) never acted once an earlier one matched
            # anywhere. Without a sniff an earlier-ordered marker
            # still takes over retroactively (its pass would have
            # found this line before any later marker's pass ran).
            boundary = False
            matched = None
            if winner is not None and winner.search(stripped) is not None:
                boundary = True
                matched = captures[winner]
            if boundary:
                if current is not None and current["end"] is None:
                    current["end"] = offset
                    # The motion total rides the close — the count is
                    # the walk's only every-motion record, and the
                    # layer's true total must survive the compact
                    # scan's empty arrays (the dead-scrub resume
                    # report). Captured only when the block actually
                    # closes here: an elapsed-closed block keeps its
                    # own total (its trailing travel belongs to no
                    # layer).
                    current["motion_total"] = features.count
                # The marker opens a layer: hand the closing one its
                # feature arrays and seed this one's opening state.
                finished = features.payload()
                features.open_layer()
                if current is not None:
                    current["features"] = finished
                if len(blocks) >= _MAX_LAYER_BLOCKS:
                    # Marker-dense hostile file: stop tracking further
                    # layers. The last tracked block already closed at
                    # the offset above; everything after degrades to
                    # the byte-range fraction and the last known
                    # layer.
                    current = None
                else:
                    current = {
                        "start": offset,
                        "end": None,
                        "elapsed": None,
                        "stats": None,
                        "motions": array("Q"),
                        "x": array("f"),
                        "y": array("f"),
                        "z": array("f"),
                        "arcs": {},
                        "start_position": (x, y, z),
                        "start_absolute": absolute_xyz,
                        "start_units": units_scale,
                        "start_type": features.start_type,
                        "start_e": features.start_e,
                        "start_e_absolute": features.start_e_absolute,
                        "start_extruding": features.start_extruding,
                        "start_arc_plane": features.start_plane,
                        "features": None,
                    }
                    blocks.append(current)
                if matched is not None:
                    capture_match = matched.search(stripped)
                    if capture_match is not None:
                        try:
                            marker_values.append(int(capture_match.group(1)))
                        except (TypeError, ValueError):
                            pass
            elif current is not None and current["end"] is None:
                elapsed_match = _ELAPSED.search(stripped)
                if elapsed_match is not None:
                    current["end"] = offset
                    # The elapsed marker closes the block as surely as the
                    # next layer marker does, so the feature arrays go with
                    # it: a layer closed here would otherwise keep the runs
                    # the tracker had not yet been handed, and a layer
                    # closed at EOF would never be given any.
                    current["features"] = features.payload()
                    current["motion_total"] = features.count
                    try:
                        current["elapsed"] = float(elapsed_match.group(1))
                    except (TypeError, ValueError):
                        current["elapsed"] = None

            # A baked end-of-layer pause command (the pause-offsets
            # concern, collected in the same read).
            if _PAUSE_COMMAND.match(stripped) is not None:
                pause_offsets.append(offset)

            # The slicer's feature marker. The prefix test is the fast
            # path — the anchored regex is the confirmation, so an
            # indented or oddly-spaced marker is still read.
            if stripped.lstrip().startswith(_TYPE_PREFIX):
                type_match = _TYPE_COMMENT.match(stripped)
                if type_match is not None:
                    name = type_match.group(1)[:_MAX_TYPE_NAME_BYTES].decode("ascii", "replace")
                    code = type_lookup.get(name)
                    if code is None:
                        if len(type_names) >= _MAX_TYPE_NAMES:
                            code = _TYPE_OTHER
                        else:
                            type_names.append(name)
                            code = len(type_names) + 1
                            type_lookup[name] = code
                    features.set_type(code)

            # Track G-code XYZ state even outside the indexed layer
            # body. This is important for Cura files that emit
            # travel/macro motion between ;TIME_ELAPSED and the
            # following ;LAYER marker.
            code = stripped.split(b";", 1)[0]
            fast = _fast_motion_line(code)
            if fast is not None:
                command, axes = fast
            else:
                command_match = _COMMAND.search(code)
                command = command_match.group(1).upper() if command_match else b""
                axes = _parse_axes(code)
            if units_scale != 1.0 and axes:
                axes = {axis: value * units_scale for axis, value in axes.items()}
            if command == b"G20":
                units_scale = 25.4
            elif command == b"G21":
                units_scale = 1.0
            elif command == b"G90":
                absolute_xyz = True
            elif command == b"G91":
                absolute_xyz = False
            elif command == b"G17":
                features.plane = ArcGeometry.PLANE_XY
            elif command == b"G18":
                features.plane = ArcGeometry.PLANE_XZ
            elif command == b"G19":
                features.plane = ArcGeometry.PLANE_YZ
            elif command == b"M82":
                features.absolute_e = True
            elif command == b"M83":
                features.absolute_e = False
            elif command == b"G92":
                if "X" in axes:
                    x = axes["X"]
                if "Y" in axes:
                    y = axes["Y"]
                if "Z" in axes:
                    z = axes["Z"]
                # An extruder reset (Cura's per-layer G92 E0) moves E
                # without extruding: it re-bases the axis, never reads as
                # a retraction.
                if "E" in axes:
                    features.e = axes["E"]
            elif command in _FAST_MOTIONS or _MOTION.search(stripped):
                # A G2/G3 is ONE motion like any other: it takes the next
                # index, its E decides extrusion, its ;TYPE: names its
                # feature. What its line adds is where the head actually
                # travelled — a circular (or helical) path the descriptor
                # records so the payload, the live-position match and the
                # printed-object walk all read the real curve instead of
                # the chord between its ends. The endpoint below is still
                # the truth the NEXT move's edge starts from.
                nx, ny, nz = x, y, z
                if "X" in axes:
                    nx = axes["X"] if absolute_xyz else x + axes["X"]
                if "Y" in axes:
                    ny = axes["Y"] if absolute_xyz else y + axes["Y"]
                if "Z" in axes:
                    nz = axes["Z"] if absolute_xyz else z + axes["Z"]
                arc = None
                if command in _ARC_CLOCKWISE or command in _ARC_COUNTER:
                    arc_words = _parse_arc_words(code)
                    if units_scale != 1.0 and arc_words:
                        arc_words = {word: value * units_scale for word, value in arc_words.items()}
                    arc = ArcGeometry.descriptor(
                        features.plane, command in _ARC_CLOCKWISE, arc_words,
                        absolute_xyz=absolute_xyz)
                x, y, z = nx, ny, nz
                collect_here = collect_motions and current is not None \
                    and current["end"] is None \
                    and len(current["motions"]) < _MAX_MOTIONS_PER_LAYER
                # The feature walk must see EVERY motion, collected or
                # not: the per-layer cap and the elapsed-marker boundary
                # both stop the arrays without stopping the E state, and
                # the next layer resumes from that state.
                features.add(axes, collect_here)
                if collect_here:
                    # Past the cap the layer's path data truncates and
                    # the byte-range fraction covers the rest — a
                    # one-layer hostile file must not grow multi-GB
                    # motion arrays.
                    if arc is not None:
                        current["arcs"][len(current["motions"])] = arc
                    current["motions"].append(offset)
                    current["x"].append(x)
                    current["y"].append(y)
                    current["z"].append(z)

            line_number += 1

        file_end = handle.tell()
        if current is not None and current["end"] is None:
            current["end"] = file_end
            current["features"] = features.payload()
            current["motion_total"] = features.count

    ranges: List[Tuple[int, int]] = []
    motions: List[array] = []
    xs: List[array] = []
    ys: List[array] = []
    zs: List[array] = []
    types: List[List[List[int]]] = []
    travel_starts: List[List[int]] = []
    travel_ends: List[List[int]] = []
    starts: List[Tuple[float, float, float]] = []
    start_absolute: List[bool] = []
    start_units: List[float] = []
    start_types: List[int] = []
    start_e: List[float] = []
    start_e_absolute: List[bool] = []
    start_extruding: List[bool] = []
    arcs: List[Dict[int, tuple]] = []
    start_arc_plane: List[int] = []
    elapsed_times: List[Optional[float]] = []
    block_stats: List[Optional[int]] = []
    layer_counts: List[int] = []
    for block in blocks:
        start = int(block["start"])
        end = int(block["end"] if block["end"] is not None else file_end)
        ranges.append((start, max(start + 1, end)))
        # The walk's per-layer motion counter, captured at the close:
        # the layer's true total — the collected arrays undercount (the
        # compact scan collects nothing; the per-layer cap truncates)
        # and a resumed session's prepared layers never hydrate, so a
        # zero count would leave the scrub slider dead forever.
        layer_counts.append(int(block.get("motion_total", 0)))
        motions.append(block["motions"])
        xs.append(block["x"])
        ys.append(block["y"])
        zs.append(block["z"])
        block_features = block["features"] or ([], [], [])
        types.append(block_features[0])
        travel_starts.append(block_features[1])
        travel_ends.append(block_features[2])
        arcs.append(block["arcs"])
        start_arc_plane.append(int(block["start_arc_plane"]))
        starts.append(tuple(float(v) for v in block["start_position"]))
        start_absolute.append(bool(block["start_absolute"]))
        start_units.append(float(block["start_units"]))
        start_types.append(int(block["start_type"]))
        start_e.append(float(block["start_e"]))
        start_e_absolute.append(bool(block["start_e_absolute"]))
        start_extruding.append(bool(block["start_extruding"]))
        elapsed = block.get("elapsed")
        elapsed_times.append(float(elapsed) if elapsed is not None else None)
        stats = block.get("stats")
        block_stats.append(int(stats) if stats is not None else None)

    # The baked pauses map to layers by block START only. A block's
    # recorded end is the ;TIME_ELAPSED line, and PauseAtHeight
    # emits its pause block AFTER that — between the elapsed marker
    # and the next layer marker (the live report: the real job's M0
    # lines sat past the recorded end and were dropped). The next
    # block's start is the true boundary, so bisect on starts alone
    # is exact. A pause before the first marker (start gcode)
    # belongs to no layer and is skipped.
    pause_layers: Tuple[int, ...] = ()
    if ranges and pause_offsets:
        baked: List[int] = []
        starts_offsets = [start for start, _end in ranges]
        for offset in pause_offsets:
            idx = bisect_right(starts_offsets, offset) - 1
            if idx < 0 or idx >= len(ranges):
                continue
            if not baked or baked[-1] != idx:
                baked.append(idx)
        pause_layers = tuple(baked)

    if cancel_event is not None and cancel_event.is_set():
        return LayerMotionIndex()
    ranges = ranges or []
    motions = motions or []
    xs = xs or []
    ys = ys or []
    zs = zs or []
    types = types or []
    travel_starts = travel_starts or []
    travel_ends = travel_ends or []
    starts = starts or []
    start_absolute = start_absolute or []
    start_units = start_units or []
    start_types = start_types or []
    start_e = start_e or []
    start_e_absolute = start_e_absolute or []
    start_extruding = start_extruding or []
    arcs = arcs or []
    start_arc_plane = start_arc_plane or []
    elapsed_times = elapsed_times or []
    stats_values = stats_values or []

    layer_map: Dict[int, int] = {}
    # Per-block values are positionally grounded: each layer's own first
    # CURRENT_LAYER, immune to leading start-gcode values and trailing extras.
    if ranges and len(block_stats) == len(ranges) and all(value is not None for value in block_stats):
        for index, value in enumerate(block_stats):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index
    if not layer_map and ranges and len(stats_values) == len(ranges):
        for index, value in enumerate(stats_values):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index
    elif not layer_map and ranges and len(marker_values) == len(ranges):
        # Cura and Orca numeric layer markers provide a useful mapping even when
        # SET_PRINT_STATS_INFO is absent. The exact values are preserved instead
        # of assuming zero/one-based numbering.
        for index, value in enumerate(marker_values):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index

    hydrated = set(range(len(ranges))) if not compact else set()
    return LayerMotionIndex(
        ranges=ranges,
        motion_offsets=motions,
        motion_x=xs,
        motion_y=ys,
        motion_z=zs,
        motion_arcs=arcs,
        motion_types=types,
        type_names=type_names,
        travel_starts=travel_starts,
        travel_ends=travel_ends,
        layer_start_positions=starts,
        layer_start_absolute=start_absolute,
        layer_start_units=start_units,
        layer_start_types=start_types,
        layer_start_e=start_e,
        layer_start_e_absolute=start_e_absolute,
        layer_start_extruding=start_extruding,
        layer_start_arc_plane=start_arc_plane,
        current_layer_map=layer_map,
        layer_elapsed_times=elapsed_times,
        pauses=pause_layers,
        compact=bool(compact),
        hydrated_layers=hydrated,
        layer_motion_counts=layer_counts,
    )


def hydrate_layer_from_file(index: LayerMotionIndex, path: str, layer: int,
                            keep_anchor: Optional[int] = None,
                            should_stop: Optional[Callable[[], bool]] = None) -> bool:
    """Populate motion data for one layer of a compact large-file index.

    Boundary indexing keeps RAM bounded for huge files. Motion commands are then
    loaded only for layers actually viewed during the live print. Byte-position
    following remains available while hydration is pending.

    keep_anchor names the FOLLOWED layer: the eviction window keeps
    [anchor-1, anchor+1] around it, so a prefetched look-ahead layer
    survives and the window follows the print rather than whichever
    layer the background worker picked last. Without keep_anchor the
    anchor is the index's followed_layer (set by the service, read
    HERE at completion so a worker finishing after an anchor change
    applies the latest policy), then the hydrated layer.

    The parse walks the layer's feature state as well as its geometry:
    the E axis for the travel boundaries and the ;TYPE: markers for the
    feature runs. Both are seeded from the scan's per-layer opening state,
    so a hydrated layer matches the full scan it stands in for.

    should_stop is consulted on the walk's own wall-clock gate, and the
    layer is abandoned with HydrationYield when it answers True. It is
    the background pass's caller that passes one: the pass runs on
    speculation and must yield to a demand, while a demand's own
    hydration is the foreground and has nothing to yield to. Callers
    that pass nothing keep the plain hand-back and no interruption.
    """
    if not index.compact or layer in index.hydrated_layers:
        return True
    if layer < 0 or layer >= len(index.ranges):
        return False
    start, end = index.ranges[layer]
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            x, y, z = index.layer_start_positions[layer] if layer < len(index.layer_start_positions) else (0.0, 0.0, 0.0)
            absolute_xyz = index.layer_start_absolute[layer] if layer < len(index.layer_start_absolute) else True
            units_scale = index.layer_start_units[layer] if layer < len(index.layer_start_units) else 1.0
            features = _FeatureTracker()
            seed_type = index.layer_start_types[layer] if layer < len(index.layer_start_types) else _TYPE_NONE
            features.last_type = seed_type
            features.open_type = seed_type
            features.e = index.layer_start_e[layer] if layer < len(index.layer_start_e) else 0.0
            features.absolute_e = index.layer_start_e_absolute[layer] if layer < len(index.layer_start_e_absolute) else True
            features.extruding = index.layer_start_extruding[layer] if layer < len(index.layer_start_extruding) else True
            # The plane at this layer's first motion, never a default XY:
            # a G18 issued before the layer decides what its arcs mean.
            features.plane = index.layer_start_arc_plane[layer] \
                if layer < len(index.layer_start_arc_plane) else ArcGeometry.PLANE_XY
            type_lookup: Dict[str, int] = {}
            offsets = array("Q")
            xs = array("f")
            ys = array("f")
            zs = array("f")
            arcs: Dict[int, tuple] = {}
            # The walk's own hand-back. Without it a dense layer is a
            # single uninterrupted hold on the interpreter, and the
            # seek that arrives while it runs waits the layer out.
            yield_at = time.monotonic()
            checked = 0
            while handle.tell() < end:
                checked += 1
                if (checked & _HYDRATE_YIELD_MASK) == 0:
                    updated = passive_yield(time.monotonic(), yield_at)
                    if updated != yield_at:
                        yield_at = updated
                        if should_stop is not None and should_stop():
                            # Before the commit below: the layer keeps
                            # every array it had, which is none of them.
                            raise HydrationYield()
                offset = handle.tell()
                line = handle.readline(_MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_LINE_BYTES:
                    line = b""
                stripped = line.rstrip(b"\r\n")
                if stripped.lstrip().startswith(_TYPE_PREFIX):
                    type_match = _TYPE_COMMENT.match(stripped)
                    if type_match is not None:
                        name = type_match.group(1)[:_MAX_TYPE_NAME_BYTES].decode("ascii", "replace")
                        code = type_lookup.get(name)
                        if code is None:
                            # The vocabulary is the index's own; a name the
                            # scan never saw there is one the scan never
                            # saw either, so it reads as unknown rather
                            # than as a code this layer cannot name.
                            try:
                                code = index.type_names.index(name) + 2
                            except ValueError:
                                code = _TYPE_OTHER
                            type_lookup[name] = code
                        features.set_type(code)
                code = stripped.split(b";", 1)[0]
                fast = _fast_motion_line(code)
                if fast is not None:
                    command, axes = fast
                else:
                    command_match = _COMMAND.search(code)
                    command = command_match.group(1).upper() if command_match else b""
                    axes = _parse_axes(code)
                if units_scale != 1.0 and axes:
                    axes = {axis: value * units_scale for axis, value in axes.items()}
                if command == b"G20":
                    units_scale = 25.4
                elif command == b"G21":
                    units_scale = 1.0
                elif command == b"G90":
                    absolute_xyz = True
                elif command == b"G91":
                    absolute_xyz = False
                elif command == b"G17":
                    features.plane = ArcGeometry.PLANE_XY
                elif command == b"G18":
                    features.plane = ArcGeometry.PLANE_XZ
                elif command == b"G19":
                    features.plane = ArcGeometry.PLANE_YZ
                elif command == b"M82":
                    features.absolute_e = True
                elif command == b"M83":
                    features.absolute_e = False
                elif command == b"G92":
                    x = axes.get("X", x); y = axes.get("Y", y); z = axes.get("Z", z)
                    if "E" in axes: features.e = axes["E"]
                elif command in _FAST_MOTIONS or _MOTION.search(stripped):
                    arc = None
                    if command in _ARC_CLOCKWISE or command in _ARC_COUNTER:
                        arc_words = _parse_arc_words(code)
                        if units_scale != 1.0 and arc_words:
                            arc_words = {word: value * units_scale for word, value in arc_words.items()}
                        arc = ArcGeometry.descriptor(
                            features.plane, command in _ARC_CLOCKWISE, arc_words,
                            absolute_xyz=absolute_xyz)
                    if "X" in axes: x = axes["X"] if absolute_xyz else x + axes["X"]
                    if "Y" in axes: y = axes["Y"] if absolute_xyz else y + axes["Y"]
                    if "Z" in axes: z = axes["Z"] if absolute_xyz else z + axes["Z"]
                    collect_here = len(offsets) < _MAX_MOTIONS_PER_LAYER
                    features.add(axes, collect_here)
                    if collect_here:
                        if arc is not None:
                            arcs[len(offsets)] = arc
                        offsets.append(offset); xs.append(x); ys.append(y); zs.append(z)
        with index.cache_lock:
            while len(index.motion_offsets) < len(index.ranges):
                index.motion_offsets.append(array("Q")); index.motion_x.append(array("f")); index.motion_y.append(array("f")); index.motion_z.append(array("f"))
                index.motion_arcs.append({})
                index.motion_types.append([]); index.travel_starts.append([]); index.travel_ends.append([])
                index.layer_start_types.append(_TYPE_NONE); index.layer_start_e.append(0.0)
                index.layer_start_e_absolute.append(True); index.layer_start_extruding.append(True)
                index.layer_start_arc_plane.append(ArcGeometry.PLANE_XY)
                if len(index.layer_motion_counts) < len(index.ranges):
                    index.layer_motion_counts.append(0)
            index.motion_offsets[layer] = offsets
            index.layer_motion_counts[layer] = len(offsets)
            index.motion_x[layer] = xs
            index.motion_y[layer] = ys
            index.motion_z[layer] = zs
            index.motion_arcs[layer] = arcs
            hydrated_runs, hydrated_starts, hydrated_ends = features.payload()
            index.motion_types[layer] = hydrated_runs
            index.travel_starts[layer] = hydrated_starts
            index.travel_ends[layer] = hydrated_ends
            index.hydrated_layers.add(layer)
            # The retention bound (the live report's progress-driven
            # growth): hydration was demand-driven as the print
            # advanced and nothing ever dropped an old layer, so a long
            # print accumulated motion arrays for every layer it
            # crossed. The window keeps the previous, current and
            # look-ahead layers around the anchor; any reader of an
            # evicted layer sees an empty array (the same degraded
            # fallback as a never-hydrated one).
            anchor = keep_anchor
            if anchor is None:
                anchor = index.followed_layer
            if anchor is None:
                anchor = layer
            # A frozen follower layer is a second anchor: its window
            # survives the live one's advance (the pop-over's detach
            # would otherwise evict the very layer it is showing).
            manual = index.manual_anchor
            # The freshly hydrated layer's own window is a THIRD
            # survivor: the background full-cache pass hydrates layers
            # far outside both anchors and must hold the arrays valid
            # until its prepare and encode complete — the next pass
            # hydrate evicts this layer's window, so the union stays
            # bounded at the live, manual and in-flight windows.
            for old in sorted(index.hydrated_layers):
                if (old < anchor - 1 or old > anchor + 1) \
                        and (manual is None or old < manual - 1 or old > manual + 1) \
                        and (old < layer - 1 or old > layer + 1):
                    index.motion_offsets[old] = array("Q")
                    index.motion_x[old] = array("f")
                    index.motion_y[old] = array("f")
                    index.motion_z[old] = array("f")
                    # The feature columns travel with the geometry, or an
                    # evicted layer would hand back a full set of runs for
                    # an empty motion list. The arc descriptors are keyed
                    # by motion index and go with them.
                    index.motion_arcs[old] = {}
                    index.motion_types[old] = []
                    index.travel_starts[old] = []
                    index.travel_ends[old] = []
                    index.hydrated_layers.remove(old)
        return True
    except OSError:
        return False


def build_index_from_bytes(data: bytes, cancel_event=None) -> LayerMotionIndex:
    with tempfile.NamedTemporaryFile(prefix="mpf-index-test-", suffix=".gcode", delete=False) as handle:
        path = handle.name
        handle.write(data)
    try:
        return build_index_from_file(path, cancel_event, compact=False)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    """Read exactly *size* bytes or raise EOFError.

    gzip streams are allowed to return short reads, so cache loading must not
    assume one ``read(n)`` call fills the requested buffer.
    """
    if size < 0:
        raise EOFError("negative cache read")
    chunks = bytearray()
    remaining = size
    while remaining:
        chunk = handle.read(remaining)
        if not chunk:
            raise EOFError("truncated index cache")
        chunks.extend(chunk)
        remaining -= len(chunk)
    return bytes(chunks)


def _feature_columns(counts: Sequence[int], type_names: Sequence[str], columns) -> Optional[Dict]:
    """Validate the per-motion feature columns, or None when they are ragged.

    The writer and the reader both come through here, because the columns
    that draw a layer's colours and its travel boundaries are only worth
    restoring if they agree with the geometry they belong to: a layer
    whose runs do not total its motion count, a marker outside its layer,
    or a code past the vocabulary would restore a *different* index than
    the one saved. Validation returning None means the blob must not be
    published — or not be trusted. An index carrying no feature data at
    all (a hand-built one, or a blob predating the columns) answers an
    empty mapping, so they are simply absent from the header.
    """
    runs, starts, ends, start_types, start_e, start_e_absolute, start_extruding = columns
    try:
        if all(len(column) == 0 for column in columns):
            return {}
    except TypeError:
        return None
    if not isinstance(type_names, list) or len(type_names) > _MAX_TYPE_NAMES:
        return None
    if not all(isinstance(name, str) for name in type_names):
        return None
    layer_count = len(counts)
    if any(not isinstance(column, list) or len(column) != layer_count for column in columns):
        return None

    def markers_or_none(values, count):
        cleaned = []
        previous = -1
        for marker in values:
            if not isinstance(marker, int) or isinstance(marker, bool) or not previous < marker < count:
                return None
            previous = marker
            cleaned.append(marker)
        return cleaned

    codes = len(type_names) + 2
    if not all(isinstance(code, int) and 0 <= code < codes for code in start_types):
        return None
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in start_e):
        return None
    if not all(isinstance(flag, bool) for flag in (*start_e_absolute, *start_extruding)):
        return None

    entries = 0
    clean_runs: List[List[List[int]]] = []
    clean_starts: List[List[int]] = []
    clean_ends: List[List[int]] = []
    for layer, count in enumerate(counts):
        layer_runs: List[List[int]] = []
        total = 0
        if len(runs[layer]) > _MAX_TYPE_RUNS_PER_LAYER:
            return None
        for run in runs[layer]:
            if not isinstance(run, list) or len(run) != 2:
                return None
            span, code = run
            if not isinstance(span, int) or isinstance(span, bool) or span < 1:
                return None
            if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code < codes:
                return None
            total += span
            layer_runs.append([span, code])
        # The runs must cover the layer's motions exactly: a layer that
        # restores a shorter path than its offsets describe is the
        # "different index" this validation exists to stop.
        if total != count:
            return None
        layer_starts = markers_or_none(starts[layer], count)
        layer_ends = markers_or_none(ends[layer], count)
        if layer_starts is None or layer_ends is None:
            return None
        entries += len(layer_runs) + len(layer_starts) + len(layer_ends)
        clean_runs.append(layer_runs)
        clean_starts.append(layer_starts)
        clean_ends.append(layer_ends)
    if entries > _MAX_CACHE_FEATURE_ENTRIES:
        return {}
    return {
        "type_names": list(type_names),
        "type_runs": clean_runs,
        "travel_starts": clean_starts,
        "travel_ends": clean_ends,
        "start_types": list(start_types),
        "start_e": [float(value) for value in start_e],
        "start_e_absolute": list(start_e_absolute),
        "start_extruding": list(start_extruding),
    }


def _arc_entries(arcs) -> List[List[list]]:
    """The in-memory descriptors as the sorted entry lists the blob holds.

    The live index keys its descriptors by motion (a sparse mapping, so
    the no-arc path allocates nothing per motion); the blob stores them
    as ordered entries, which is also the shape both sides validate.
    """
    entries: List[List[list]] = []
    for layer_arcs in arcs:
        if isinstance(layer_arcs, dict):
            entries.append([[int(motion), plane, bool(clockwise), float(offset_a), float(offset_b)]
                            for motion, (plane, clockwise, offset_a, offset_b)
                            in sorted(layer_arcs.items())])
        else:
            entries.append(list(layer_arcs))
    return entries


def _arc_columns(counts: Sequence[int], arcs, start_planes) -> Optional[Dict]:
    """Validate the arc descriptors and the layer-start planes, or None.

    The writer and the reader both come through here, for the same reason
    the feature columns do: a descriptor naming a motion its layer does
    not have, a plane that is not a plane, or offsets that draw no circle
    would restore an index whose arcs are not the arcs that were saved.
    Returning None means the blob must not be published — or not be
    trusted. An index with no arc data at all (every G0/G1 file) answers
    an empty mapping, so its header carries no arc keys and pays nothing
    for the feature.

    Past the entry budget the answer is None, never a header without the
    descriptors: the layer-start planes alone would restore an index
    whose arcs draw as chords — geometry the file never commanded —
    stored durably and indistinguishable from a file that has no arcs.
    A cache is faithful or it is not written.
    """
    try:
        if not any(arcs) and all(plane == ArcGeometry.PLANE_XY for plane in start_planes):
            return {}
    except TypeError:
        return None
    layer_count = len(counts)
    if not isinstance(arcs, (list, tuple)) or not isinstance(start_planes, (list, tuple)):
        return None
    if len(arcs) != layer_count or len(start_planes) != layer_count:
        return None
    if not all(isinstance(plane, int) and not isinstance(plane, bool)
               and plane in ArcGeometry.PLANES for plane in start_planes):
        return None
    entries = 0
    clean: List[List[list]] = []
    for layer, count in enumerate(counts):
        layer_arcs = arcs[layer]
        if not isinstance(layer_arcs, (list, tuple)):
            return None
        cleaned: List[list] = []
        previous = -1
        for entry in layer_arcs:
            if not isinstance(entry, (list, tuple)) or len(entry) != 5:
                return None
            motion, plane, clockwise, offset_a, offset_b = entry
            if not isinstance(motion, int) or isinstance(motion, bool) or not previous < motion < count:
                return None
            if not isinstance(plane, int) or isinstance(plane, bool) or plane not in ArcGeometry.PLANES:
                return None
            if not isinstance(clockwise, bool):
                return None
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for value in (offset_a, offset_b)):
                return None
            if ArcGeometry.degenerate(offset_a, offset_b):
                return None
            previous = motion
            cleaned.append([motion, plane, clockwise, float(offset_a), float(offset_b)])
        entries += len(cleaned)
        clean.append(cleaned)
    planes = [int(plane) for plane in start_planes]
    if entries > _MAX_CACHE_ARC_ENTRIES:
        return None
    return {"arcs": clean, "start_arc_plane": planes}


class PersistentIndexCache:
    def __init__(self, directory: str, *, max_bytes: int = 128 * 1024 * 1024,
                 max_entries: Optional[int] = 16) -> None:
        self.directory = directory
        self.max_bytes = max(1 * 1024 * 1024, int(max_bytes))
        # None disables the entry-count bound (the unified runtime
        # cache's choice — the configured byte budget governs the
        # whole print-folder cache; a hidden folder-count cap would
        # silently override a 4096 MiB selection for small prints).
        self.max_entries = None if max_entries is None else max(1, int(max_entries))
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, identity: RemoteFileIdentity) -> str:
        digest = hashlib.sha256(identity.stable_key().encode("utf-8")).hexdigest()
        # The per-print folder (the review's unified-persistence
        # finding): the index AND the prepared store live as siblings
        # under one print's own directory — an entire print's cache
        # is one folder to delete, and the eviction drops the whole
        # folder (never an orphaned half).
        print_dir = os.path.join(self.directory, f"p-{digest[:24]}")
        os.makedirs(print_dir, exist_ok=True)
        return os.path.join(print_dir, "index.mpfi.gz")

    def load(self, identity: Optional[RemoteFileIdentity]) -> Optional[LayerMotionIndex]:
        if identity is None:
            return None
        path = self._path(identity)
        try:
            with gzip.open(path, "rb") as handle:
                if handle.read(len(_CACHE_MAGIC)) != _CACHE_MAGIC:
                    return None
                header_len_raw = _read_exact(handle, 4)
                if len(header_len_raw) != 4:
                    return None
                header_len = struct.unpack("<I", header_len_raw)[0]
                if header_len <= 0 or header_len > _MAX_CACHE_HEADER_BYTES:
                    return None
                header = json.loads(_read_exact(handle, header_len).decode("utf-8"))
                if header.get("version") != _CACHE_VERSION:
                    return None
                if header.get("identity") != identity.stable_key():
                    return None
                # The uuid is Moonraker's per-extraction token, never a
                # content identity; the header's own fields vouch for
                # size/modified (the surviving discriminators). Validate
                # every known field.
                fields = header.get("identity_fields")
                if fields and isinstance(fields, list) and len(fields) == 4:
                    if identity.size > 0 and int(fields[1]) > 0 and int(fields[1]) != identity.size:
                        return None
                    if identity.modified > 0 and float(fields[2]) > 0 and float(fields[2]) != identity.modified:
                        return None
                    # The uuid is Moonraker's per-extraction token: with
                    # RELIABLE metadata (a real modified timestamp) a
                    # re-extraction rolls it without the gcode changing,
                    # so it must never invalidate an otherwise-valid
                    # entry (the review's UUID-policy finding — the
                    # stable key already ignores it, and the load may
                    # not contradict the key). WITHOUT a modified
                    # timestamp the weak identity (name+size alone) is
                    # the only thing standing, and a rolled uuid then
                    # marks a re-extraction whose content may have
                    # changed: the stale entry is refused rather than
                    # trusted (the review's weak-metadata rule — a
                    # field ignored by the lookup must never vouch for
                    # a restore).
                    if identity.modified <= 0 and identity.size > 0 \
                            and str(fields[3]) != str(identity.uuid):
                        return None
                    # The per-machine namespace resolves cross-printer
                    # collisions instead.
                if header.get("byteorder") != sys.byteorder:
                    return None
                ranges = [(int(a), int(b)) for a, b in header.get("ranges", [])]
                starts = [tuple(float(v) for v in xyz[:3]) for xyz in header.get("starts", [])]
                start_absolute = [bool(v) for v in header.get("start_absolute", [True] * len(ranges))]
                start_units = [float(v) for v in header.get("start_units", [1.0] * len(ranges))]
                layer_map = {int(k): int(v) for k, v in (header.get("layer_map") or {}).items()}
                elapsed_times = [float(v) if v is not None else None for v in header.get("elapsed_times", [])]
                compact = bool(header.get("compact", False))
                counts = [int(v) for v in header.get("counts", [])]
                if not (
                    len(ranges) == len(counts) == len(starts)
                    == len(start_absolute) == len(start_units) == len(elapsed_times)
                ):
                    return None
                # The feature columns are optional as a whole — a blob
                # written before they existed still restores its geometry
                # — but a present one that disagrees with the geometry is
                # refused rather than trusted. An absent run list restores
                # as one untyped run per motion: the motion is there, its
                # colour never was, and the columns must still come back a
                # per-layer entry long or the hydrator could not fill them.
                empty_markers = [[] for _ in counts]
                untyped_runs = [[[count, _TYPE_NONE]] if count else [] for count in counts]
                blank_seeds = [_TYPE_NONE] * len(counts)
                zero_seeds = [0.0] * len(counts)
                true_seeds = [True] * len(counts)
                feature_header = _feature_columns(
                    counts,
                    header.get("type_names", []),
                    (
                        header.get("type_runs", untyped_runs),
                        header.get("travel_starts", empty_markers),
                        header.get("travel_ends", empty_markers),
                        header.get("start_types", blank_seeds),
                        header.get("start_e", zero_seeds),
                        header.get("start_e_absolute", true_seeds),
                        header.get("start_extruding", true_seeds),
                    ),
                )
                if feature_header is None:
                    return None
                types = feature_header.get("type_runs", untyped_runs)
                travel_starts = feature_header.get("travel_starts", empty_markers)
                travel_ends = feature_header.get("travel_ends", empty_markers)
                start_types = feature_header.get("start_types", blank_seeds)
                start_e = feature_header.get("start_e", zero_seeds)
                start_e_absolute = feature_header.get("start_e_absolute", true_seeds)
                start_extruding = feature_header.get("start_extruding", true_seeds)
                # A budget-dropped (or absent) vocabulary leaves no code to
                # name, so the names go with the runs.
                type_names = list(feature_header.get("type_names", []))
                # The arc columns are optional as a whole: a file with
                # no arcs carries no arc keys and restores every motion
                # without one. A present column is validated against the
                # geometry it claims, and one past the entry budget is
                # refused outright — reading it back arc-free would draw
                # a chord where the file commanded a curve, for as long
                # as the entry lives.
                arc_header = _arc_columns(
                    counts,
                    header.get("arcs", [{} for _ in counts]),
                    header.get("start_arc_plane", [ArcGeometry.PLANE_XY] * len(counts)),
                )
                if arc_header is None:
                    return None
                arcs: List[Dict[int, tuple]] = []
                for layer_arcs in arc_header.get("arcs", [{} for _ in counts]):
                    arcs.append({int(entry[0]): (int(entry[1]), bool(entry[2]),
                                                 float(entry[3]), float(entry[4]))
                                 for entry in layer_arcs})
                start_arc_plane = list(arc_header.get(
                    "start_arc_plane", [ArcGeometry.PLANE_XY] * len(counts)))

                offsets: List[array] = []
                xs: List[array] = []
                ys: List[array] = []
                zs: List[array] = []
                for count in counts:
                    if count < 0 or count > 100_000_000:
                        return None
                    off = array("Q")
                    xx = array("f")
                    yy = array("f")
                    zz = array("f")
                    off.frombytes(_read_exact(handle, count * off.itemsize))
                    xx.frombytes(_read_exact(handle, count * xx.itemsize))
                    yy.frombytes(_read_exact(handle, count * yy.itemsize))
                    zz.frombytes(_read_exact(handle, count * zz.itemsize))
                    if not (len(off) == len(xx) == len(yy) == len(zz) == count):
                        return None
                    offsets.append(off)
                    xs.append(xx)
                    ys.append(yy)
                    zs.append(zz)
            try:
                os.utime(path, None)
            except OSError:
                pass
            hydrated_raw = header.get("hydrated")
            if isinstance(hydrated_raw, list):
                hydrated = {int(i) for i in hydrated_raw if 0 <= int(i) < len(ranges)}
            else:
                # The motion arrays are the evidence. The feature columns
                # deliberately are NOT: their runs must total the layer's
                # motion count, so they can only exist where those arrays
                # do and could never name a layer this misses.
                hydrated = {i for i, values in enumerate(offsets) if len(values) > 0}
            pauses = []
            for value in header.get("pauses", []):
                try:
                    layer = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= layer < len(ranges) and (not pauses or layer > pauses[-1]):
                    pauses.append(layer)
            motion_counts = header.get("motion_counts")
            if not (isinstance(motion_counts, list) and len(motion_counts) == len(ranges)
                    and all(isinstance(v, int) and 0 <= v for v in motion_counts)):
                # Legacy caches carry no eviction-proof counts; the
                # array lengths (the evicted state) are the fallback.
                motion_counts = list(counts)
            return LayerMotionIndex(
                ranges=ranges,
                motion_offsets=offsets,
                motion_x=xs,
                motion_y=ys,
                motion_z=zs,
                motion_arcs=arcs,
                motion_types=types,
                type_names=type_names,
                travel_starts=travel_starts,
                travel_ends=travel_ends,
                layer_start_positions=starts,
                layer_start_absolute=start_absolute,
                layer_start_units=start_units,
                layer_start_types=start_types,
                layer_start_e=start_e,
                layer_start_e_absolute=start_e_absolute,
                layer_start_extruding=start_extruding,
                layer_start_arc_plane=start_arc_plane,
                current_layer_map=layer_map,
                layer_elapsed_times=elapsed_times,
                pauses=tuple(pauses),
                compact=compact,
                hydrated_layers=hydrated,
                layer_motion_counts=motion_counts,
            )
        except (OSError, ValueError, json.JSONDecodeError, EOFError, struct.error):
            return None

    def save(self, identity: Optional[RemoteFileIdentity], index: LayerMotionIndex) -> None:
        if identity is None or not index:
            return
        with index.cache_lock:
            layer_count = len(index.ranges)
            if not (
                len(index.motion_offsets) == layer_count
                and len(index.motion_x) == layer_count
                and len(index.motion_y) == layer_count
                and len(index.motion_z) == layer_count
                and len(index.layer_start_positions) == layer_count
                and len(index.layer_start_absolute) == layer_count
                and len(index.layer_start_units) == layer_count
                and len(index.layer_elapsed_times) == layer_count
                and len(index.motion_arcs) == layer_count
                and len(index.layer_start_arc_plane) == layer_count
            ):
                return
            counts = [len(v) for v in index.motion_offsets]
            for i in range(layer_count):
                count = counts[i]
                if not (len(index.motion_x[i]) == len(index.motion_y[i]) == len(index.motion_z[i]) == count):
                    return
            features = _feature_columns(counts, index.type_names, (
                index.motion_types, index.travel_starts, index.travel_ends,
                index.layer_start_types, index.layer_start_e,
                index.layer_start_e_absolute, index.layer_start_extruding,
            ))
            if features is None:
                # Ragged feature columns restore a different index than the
                # one saved; publishing the blob would be worse than not
                # caching at all.
                return
            arc_columns = _arc_columns(counts, _arc_entries(index.motion_arcs),
                                       index.layer_start_arc_plane)
            if arc_columns is None:
                return
            path = self._path(identity)
            temp_path = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
            header = {
                "version": _CACHE_VERSION,
                "identity": identity.stable_key(),
                "identity_fields": [identity.filename, identity.size, identity.modified, identity.uuid],
                "byteorder": sys.byteorder,
                "ranges": index.ranges,
                "starts": index.layer_start_positions,
                "start_absolute": index.layer_start_absolute,
                "start_units": index.layer_start_units,
                "layer_map": {str(k): int(v) for k, v in index.current_layer_map.items()},
                "elapsed_times": index.layer_elapsed_times,
                "pauses": list(index.pauses),
                "compact": bool(index.compact),
                "hydrated": sorted(index.hydrated_layers),
                "counts": counts,
                # The eviction-proof counts ride beside the array lengths
                # (which reflect the evicted state for a compact save):
                # a restored index knows a far layer's total before its
                # first re-hydration.
                "motion_counts": list(index.layer_motion_counts),
            }
            header.update(features)
            header.update(arc_columns)
            raw_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
            if len(raw_header) > _MAX_CACHE_HEADER_BYTES:
                # A very fragmented (or hostile) file can still push the
                # geometry columns past the readable bound. Writing that
                # blob would only spend the byte budget on a file the
                # loader always refuses, so it is not written at all.
                return
            try:
                with gzip.open(temp_path, "wb", compresslevel=3) as handle:
                    handle.write(_CACHE_MAGIC)
                    handle.write(struct.pack("<I", len(raw_header)))
                    handle.write(raw_header)
                    for i, offsets in enumerate(index.motion_offsets):
                        handle.write(offsets.tobytes())
                        handle.write(index.motion_x[i].tobytes())
                        handle.write(index.motion_y[i].tobytes())
                        handle.write(index.motion_z[i].tobytes())
                os.replace(temp_path, path)
                self.prune(keep=path)
            except OSError:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def prune(self, keep: Optional[str] = None) -> None:
        """The print-level policy (the review's unified-lifecycle
        finding): one print folder's total cost is the index AND the
        prepared representation together, and an evicted print loses
        the WHOLE folder — never an orphaned half. The walk is the
        SHARED eviction policy (CachePolicy.evict_to_budget): true
        LRU — the least recently used unprotected folders go first,
        and the eviction never finishes over budget while an
        unprotected folder remains. The protected path (the
        just-written or currently used entry, passed explicitly —
        never an mtime guess) always survives; if it alone exceeds a
        budget, that is the only acceptable overage."""
        try:
            # The print-level totals: one entry per print folder, its
            # size the sum of every representation inside it.
            totals = {}
            for root, _dirs, names in os.walk(self.directory):
                folder = os.path.basename(root)
                if not folder.startswith("p-"):
                    continue
                try:
                    stats = [os.stat(os.path.join(root, name))
                             for name in names
                             if name.endswith((".mpfi.gz", ".mpfp"))]
                    if not stats:
                        continue  # an empty leftover folder counts nothing
                    size = sum(stat.st_size for stat in stats)
                    mtime = max(stat.st_mtime for stat in stats)
                except OSError:
                    continue
                totals[root] = (mtime, size)
            keep_dir = os.path.dirname(keep) if keep else None
            evict_to_budget(totals, self.max_bytes, self.max_entries,
                            keep_dir)
        except OSError:
            pass
