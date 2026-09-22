"""The plate's progress payload: prepared polylines for the follower
face, built from the index's motion arrays.

ONE PRIMITIVE: the motion's EDGE. Motion m runs from the position the
head held when the move began — motion m-1's endpoint, or the layer's
start position for m == 0 — to motion m's own stored endpoint, which is
a position and never geometry by itself. The endpoint-only reading lost
the first edge of every run (the live report's straight-then-diagonal
skin lines arriving as single diagonals, and the fanning along a hatch)
and made a one-motion extrusion arrive as a dot.

ONE PRIMITIVE, not one line: a logical motion carries a physical path,
and a G2/G3 commanded a curve. Such a motion contributes the polyline
ArcGeometry derives from its descriptor — every subedge carrying that
one motion's index, feature and travel state — so an arc reaches the
painter, the printed-object walk and the live-position match as the
path the head took. Nothing downstream branches on the command word.

``motion_edges`` is that primitive's one implementation, and the payload
builder and the printed-object walk both read it — a seek into a layer
is the suffix of that same walk, so nothing here has to guess what a
motion means. ``refined_fraction`` searches the logical endpoints
instead (the motion index is the unit the split is counted in) and
delegates the arcs it meets to ArcGeometry.closest, so both readings
agree about the curve without sharing a walk.

A travel span always breaks the class polylines and its motions never
enter them; a feature-type change breaks them too, so a WALL -> SKIN ->
WALL sequence yields two independent WALL runs, never one run chording
across the SKIN. A feature class holds as many segments as the G-code
gives it.

Every vertex carries the motion whose edge ENDS there (a segment's
first vertex carries that first edge's motion too), so a segment's
indices never decrease and the painter can decide per edge: an edge is
printed iff its motion index is below the split count.

Below a class's point budget the vertices are the G-code's own — no
distance filter runs at all (the old 0.35 mm floor erased the live
file's short skin lines and its corner runs). Above the budget each
already-separated segment simplifies on its own with Douglas-Peucker:
endpoints and corners kept, never a stride, never across a segment
boundary, a travel, or a feature change — and the vertex it drops is
measurably within the tolerance of the chord that replaces it, so the
simplification's error is a bound rather than a hope.

The architecture contract forbids the mutable index arrays crossing the
worker boundary; only these built lists do.

The preparation — the edge walk and the simplification — is a pure
function of the index and the layer, and it is bounded twice over: the
walk is linear in the layer's motions, and the simplification's
quadratic worst case is clamped by _SIMPLIFY_WORK_LIMIT. Both are what
make it safe to call from anywhere, and `prepare_layer` is the entry
the service's own executor can own so that the UI thread's read is a
memo hit rather than a build.
"""
from __future__ import annotations

from bisect import bisect_left
from math import hypot
import threading
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple
import weakref

from . import ArcGeometry
from .GCodeIndex import LayerMotionIndex

# The per-class point budget: the G-code's own vertices are kept up to
# this ceiling, so a class draws exactly what the slicer commanded. The
# raster stack changes the economics here: the static layers rasterise
# once per anchor and the full re-raster amortises over ~20 paints.
MAX_POINTS_PER_CLASS = 200000
# The travel channel's budget: the same rule on its own channel, which
# only draws while the travels are toggled on.
MAX_TRAVEL_POINTS = 12000

# Background full-layer preparation is speculative. A foreground layer
# request may interrupt it at coarse boundaries; no partial payload is
# ever memoised or published.
_PREP_YIELD_GRANULARITY = 4096


class PreparationYield(Exception):
    """Cooperative interruption of speculative background preparation."""


def _check_yield(should_yield: Optional[Callable[[], bool]], counter: int = 0) -> None:
    if should_yield is not None and counter % _PREP_YIELD_GRANULARITY == 0 \
            and should_yield():
        raise PreparationYield()

# A span whose XY path is shorter than this never moved the head: a
# retract and prime at one position is a seam, not a repositioning. It
# is a numerical zero, not a visual floor — a 0.3 mm travel is a real
# travel and stays one.
_TRAVEL_EPSILON_MM = 1.0e-6
# The simplification floor and pass cap: the simplification engages only
# above a budget, and it must stay far below any real geometry so a
# budget miss never alters a normal layer's vertices.
_SIMPLIFY_FLOOR_MM = 1.0e-4
# ...and its ceiling: the arc tessellation's own sagitta. Above a budget
# the tolerance is path/budget, which on a very dense layer can exceed
# the error the arcs were drawn to — simplifying there would flatten a
# curve the geometry just spent vertices on. The two numbers are ONE
# budget: no vertex ever leaves with more error than a curve may carry.
_SIMPLIFY_CEILING_MM = ArcGeometry.MAX_SAGITTA_MM
_MAX_SIMPLIFY_PASSES = 6
# The simplification's work bound: the interior vertices one CHANNEL's
# simplification may inspect, shared by every segment and every
# escalation pass of one call. Douglas-Peucker's worst case is
# quadratic — a chain whose every split peels a single vertex (a
# sawtooth of near-equal teeth, a noisy run) costs one full scan per
# kept vertex, and a channel holds hundreds of thousands of them — so
# the walk is charged per inspected vertex and stops refining when the
# charge is gone. It never stops *truthfully*: an interval the bound
# could not refine keeps its own vertices, which is zero error, so the
# bound costs refinement and never accuracy.
#
# The number is a time bound, and it is calibrated as "no more than a
# well-behaved channel of your own size costs". Measured on the largest
# geometry the point budgets admit (200k vertices, one channel), the
# intrinsic cost of the exact walk is 200k inspections for a straight
# run, 1.4M for a long gentle bow and 2.7M for a dense arc; the shapes
# that exceed this bound are exactly the ones whose every split peels a
# vertex, and they exceed it by five orders of magnitude (a 200k
# sawtooth ran past 120s before it was clamped). The bound therefore
# leaves every channel that simplifies at all exactly as it was, and
# clips the quadratic shapes to the cost of an ordinary one.
_SIMPLIFY_WORK_LIMIT = 3000000
# The prepared-layer store's size: the follower's window is three layers
# and an anchor move keeps two of them, so a handful of entries covers
# the reuse plus the worker's look-ahead.
_PREPARED_LIMIT = 8

_TYPE_NONE = 0
_TYPE_OTHER = 1


def _type_name(index: LayerMotionIndex, code: int) -> str:
    if code == _TYPE_NONE:
        return "unknown"
    if code == _TYPE_OTHER:
        return "other"
    names = index.type_names
    position = code - 2
    return names[position] if 0 <= position < len(names) else "other"


def _layer_start(index: LayerMotionIndex, layer: int,
                 xs: Sequence, ys: Sequence) -> Tuple[float, float]:
    """The position the head held when the layer's first motion began —
    the first edge's start. Without a recorded start position (a
    hand-built index) the first endpoint stands in, so the run still
    opens where the G-code's first move starts."""
    if layer < len(index.layer_start_positions):
        start = index.layer_start_positions[layer]
        if len(start) >= 2:
            return float(start[0]), float(start[1])
    if len(xs):
        return float(xs[0]), float(ys[0])
    return 0.0, 0.0


def _layer_start_z(index: LayerMotionIndex, layer: int, zs: Sequence) -> float:
    """The Z the head held when the layer's first motion began."""
    if layer < len(index.layer_start_positions):
        start = index.layer_start_positions[layer]
        if len(start) >= 3:
            return float(start[2])
    return float(zs[0]) if len(zs) else 0.0


def state_before_motion(index: LayerMotionIndex, layer: int, first: int) -> bool:
    """The travel state the head held when motion *first* began.

    A layer opens in the state the file was left in — every motion of
    every earlier layer advanced it, and ``layer_start_extruding``
    records where that left each layer. Each recorded boundary SETS the
    state (a travel start is travelling, a travel end is extruding), so
    the answer is the layer's seed unless a boundary falls before
    *first*, and then the LAST such boundary wins: the same value the
    unseeked walk holds after applying them in order. Counting the
    boundaries on each side of the seek instead reads a layer that
    opened mid-travel backwards, because its boundary order is the
    mirror of a layer that opened extruding.

    A boundary AT *first* is not applied here: it belongs to that
    motion's own edge, which ``motion_edges`` applies before yielding it.
    """
    starts = index.travel_starts[layer] if layer < len(index.travel_starts) else ()
    ends = index.travel_ends[layer] if layer < len(index.travel_ends) else ()
    start_before = bisect_left(starts, first)
    end_before = bisect_left(ends, first)
    if start_before and end_before:
        # A motion recorded in both lists is a contradiction the walk
        # resolves in the travel end's favour (it checks the ends last).
        return ends[end_before - 1] >= starts[start_before - 1]
    if start_before:
        return False
    if end_before:
        return True
    extruding = True
    if layer < len(index.layer_start_extruding):
        extruding = bool(index.layer_start_extruding[layer])
    return extruding


def motion_edges(index: LayerMotionIndex, layer: int,
                 first: int = 0) -> Iterator[Tuple[int, float, float, float, float, int, bool]]:
    """Every motion of *layer* as its true edge, in order.

    Yields ``(motion, x0, y0, x1, y1, feature, extruding)``: the edge
    runs from the position the head held when motion *motion* began to
    that motion's own endpoint, *feature* is its feature code and
    *extruding* is the travel state after it. The order is the G-code's,
    one motion at a time, and a zero-XY move yields a zero-length edge
    (its geometry is nothing; its E still moves the state).

    A motion that commanded an arc yields one edge per tessellated
    subedge instead of one chord, all of them carrying that motion's own
    index, feature and travel state: the physical path crosses the
    worker boundary and no consumer needs to know the difference between
    a straight move and a curved one.

    *first* starts the walk at that motion's edge without walking the
    ones before it — the printed-object cursor's seek. The seek is
    equivalent to the suffix of the full walk: the state is
    ``state_before_motion`` and the boundary cursors start at the first
    boundary at or after *first*, so a boundary at *first* is applied to
    that motion's own edge exactly as the unseeked walk would.
    """
    if layer < 0 or layer >= index.layer_count():
        return
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    ys = index.motion_y[layer] if layer < len(index.motion_y) else ()
    count = len(xs)
    if first < 0:
        first = 0
    if first >= count:
        return
    starts = index.travel_starts[layer] if layer < len(index.travel_starts) else ()
    ends = index.travel_ends[layer] if layer < len(index.travel_ends) else ()
    runs = index.motion_types[layer] if layer < len(index.motion_types) else ()
    arcs = index.motion_arcs[layer] if layer < len(index.motion_arcs) else None
    zs: Sequence = ()
    if arcs:
        # The arc maths needs the third axis (a G17 arc's Z is its helix,
        # a G18/G19 arc's is one of its plane axes), so a layer with arcs
        # must have a Z column as long as its X and Y columns. Without
        # one the descriptors are ignored rather than guessed at.
        zs = index.motion_z[layer] if layer < len(index.motion_z) else ()
        if len(zs) != count:
            arcs = None
            zs = ()
    start_index = bisect_left(starts, first)
    end_index = bisect_left(ends, first)
    extruding = state_before_motion(index, layer, first)
    if first:
        x0 = float(xs[first - 1])
        y0 = float(ys[first - 1])
    else:
        x0, y0 = _layer_start(index, layer, xs, ys)
    # The feature RLE advances once per motion (a fresh cursor beats
    # rescanning the runs from the top for every motion of a layer).
    run_index = 0
    run_left = 0
    code = _TYPE_NONE
    skip = first
    while skip > 0 and run_index < len(runs):
        run = runs[run_index]
        run_index += 1
        try:
            span = int(run[0])
            code = int(run[1])
        except (IndexError, TypeError, ValueError):
            continue
        if span <= 0:
            continue
        if span > skip:
            run_left = span - skip
            skip = 0
        else:
            skip -= span
            run_left = 0
    for motion in range(first, count):
        if run_left <= 0:
            # Past the runs a motion's feature is unknown, never the
            # last run's value (the RLE is capped, the layer is not).
            run_left = 0
            code = _TYPE_NONE
            while run_index < len(runs):
                run = runs[run_index]
                run_index += 1
                try:
                    span = int(run[0])
                    value = int(run[1])
                except (IndexError, TypeError, ValueError):
                    continue
                if span > 0:
                    run_left = span
                    code = value
                    break
        run_left -= 1
        if start_index < len(starts) and starts[start_index] == motion:
            extruding = False
            start_index += 1
        if end_index < len(ends) and ends[end_index] == motion:
            extruding = True
            end_index += 1
        x1 = float(xs[motion])
        y1 = float(ys[motion])
        descriptor = arcs.get(motion) if arcs else None
        if descriptor is not None:
            start_z = _layer_start_z(index, layer, zs) if motion == 0 else float(zs[motion - 1])
            for px, py, _pz in ArcGeometry.tessellate(descriptor, (x0, y0, start_z),
                                                      (x1, y1, float(zs[motion]))):
                yield motion, x0, y0, px, py, code, extruding
                x0, y0 = px, py
            continue
        yield motion, x0, y0, x1, y1, code, extruding
        x0, y0 = x1, y1


def _point_segment_distance_sq(px: float, py: float, x0: float, y0: float,
                               dx: float, dy: float, span_sq: float) -> float:
    """The squared distance from (px, py) to the FINITE segment.

    The candidate the chain collapses to is the segment, not the line
    through it: the perpendicular foot of a vertex beyond the far end
    lands outside the segment, and measuring to it would call a vertex
    9 mm past the end a perfect fit and delete the corner the run has
    there. The foot is clamped into the segment, so a vertex past the
    end measures to that end.
    """
    if span_sq <= 0.0:
        return (px - x0) ** 2 + (py - y0) ** 2
    t = ((px - x0) * dx + (py - y0) * dy) / span_sq
    if t <= 0.0:
        return (px - x0) ** 2 + (py - y0) ** 2
    if t >= 1.0:
        return (px - (x0 + dx)) ** 2 + (py - (y0 + dy)) ** 2
    ox = px - (x0 + t * dx)
    oy = py - (y0 + t * dy)
    return ox * ox + oy * oy


def _douglas_peucker(points: Sequence[Sequence[float]],
                     tolerance: float) -> List[List[float]]:
    """One vertex chain reduced to its endpoints and its corners.

    Douglas-Peucker over the chain's own vertices: both endpoints are
    always kept and a vertex whose deviation from the FINITE candidate
    segment exceeds *tolerance* is kept too, so a straight run collapses
    to its two ends while a corner keeps its corner and the run's ends
    never move. The deviation is a real bound: it is the distance to the
    chord the vertex would be dropped onto, so no vertex is ever more
    than *tolerance* from the polyline that comes back. The result is a
    subset of the input vertices, so every motion index survives with
    the vertex it belongs to.

    The walk is charged against a work bound (see _simplify): a chain
    whose shape makes the exact recursion quadratic stops refining and
    keeps its own vertices instead, which is a subset of the input and
    still within the same bound.
    """
    return _simplify(points, tolerance, [_SIMPLIFY_WORK_LIMIT])


def _simplify(points: Sequence[Sequence[float]], tolerance: float,
              spent: List[int],
              should_yield: Optional[Callable[[], bool]] = None) -> List[List[float]]:
    """The charged walk behind _douglas_peucker.

    *spent* is a one-slot list — the CHANNEL's work counter, shared
    across every segment and every escalation pass of one _budgeted
    call — and the walk charges it one unit per interior vertex it
    inspects. The bound exists because the recursion's worst case is
    quadratic: a chain whose every split peels exactly one vertex (the
    sawtooth an alternating edge produces) costs a full scan per kept
    vertex, and 200k noisy vertices then cost 4e10 inspections, which
    is a frozen UI rather than a drawing.

    Running out of charge cannot break the guarantee, because the two
    things the walk can do with an interval are both bound-preserving:
    it either proves the interval's chord (every interior vertex within
    *tolerance* of the FINITE segment, so the chord may stand for them)
    or it keeps the interval's own vertices (zero error, and the arc's
    ceiling still caps the tolerance the pass is running at). What the
    bound refuses is the third option — a coarse chord no scan ever
    verified — so an exhausted walk completes the interval it was about
    to split and every interval still pending, and returns a subset
    that is denser than the exact answer and never less accurate.
    """
    count = len(points)
    if count <= 2:
        return list(points)
    tolerance_sq = tolerance * tolerance
    # A byte per vertex rather than an object: the kept-set of a
    # 200k-vertex chain is a transient the clamp must not inflate.
    keep = bytearray(count)
    keep[0] = keep[count - 1] = 1
    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        x0, y0 = points[first][0], points[first][1]
        x1, y1 = points[last][0], points[last][1]
        dx = x1 - x0
        dy = y1 - y0
        span_sq = dx * dx + dy * dy
        worst = -1.0
        worst_index = -1
        for index in range(first + 1, last):
            _check_yield(should_yield, index - first)
            point = points[index]
            distance_sq = _point_segment_distance_sq(
                point[0], point[1], x0, y0, dx, dy, span_sq)
            if distance_sq > worst:
                worst = distance_sq
                worst_index = index
        spent[0] -= last - first - 1
        if worst <= tolerance_sq:
            # A proven leaf: this chord stands for the whole interval
            # and every vertex inside it may be dropped.
            continue
        if spent[0] < 0:
            interior = last - first - 1
            keep[first + 1:last] = b"\x01" * interior
            for pending_first, pending_last in stack:
                keep[pending_first + 1:pending_last] = b"\x01" * (pending_last - pending_first - 1)
            break
        keep[worst_index] = 1
        # Only intervals with something left to decide are stacked: an
        # interval of one edge has no interior vertex, so pushing it
        # would grow the stack with work that is already decided. The
        # sawtooth case peels one vertex per split and would otherwise
        # leave one dead interval per split on the stack.
        if worst_index - first > 1:
            stack.append((first, worst_index))
        if last - worst_index > 1:
            stack.append((worst_index, last))
    return [points[index] for index in range(count) if keep[index]]


def _segments_path(segments: Sequence[Sequence[Sequence[float]]],
                   should_yield: Optional[Callable[[], bool]] = None) -> float:
    """The channels' total path length: the simplification's scale."""
    total = 0.0
    walked = 0
    for segment in segments:
        for index in range(1, len(segment)):
            walked += 1
            _check_yield(should_yield, walked)
            total += hypot(segment[index][0] - segment[index - 1][0],
                           segment[index][1] - segment[index - 1][1])
    return total


def _budgeted(segments: List[List[List[float]]],
              budget: int,
              should_yield: Optional[Callable[[], bool]] = None) -> List[List[List[float]]]:
    """A channel's segments, simplified only if they exceed *budget*.

    The exact vertices stand below the budget. Above it every segment
    simplifies ALONE — the separation the walk already made (travels,
    feature changes, disconnected runs) is never undone — with the
    tolerance doubling until the channel fits or the passes run out. A
    channel that cannot fit still holds a geometry-preserving subset —
    every dropped vertex lies within the tolerance of the chord that
    replaced it, and the ceiling is the arc sagitta, so a curve is never
    flattened past the tolerance its own subedges were drawn to. A
    simplified chord draws when its last motion is printed, so the
    printed fill lags its own chord and never runs ahead of the head.

    The passes share ONE work counter, so the whole call — every
    segment, every escalation — is bounded by _SIMPLIFY_WORK_LIMIT and
    not by the geometry's shape. A channel that spends the counter on
    its first pass is a channel whose first pass had to scan the whole
    chain per vertex kept, and no later pass at a coarser tolerance
    turns that shape into a fitting one; the counter's exhaustion ends
    the search with the last pass's subset, which is still within the
    ceiling.
    """
    total = sum(len(segment) for segment in segments)
    if total <= budget:
        return segments
    tolerance = min(max(_segments_path(segments, should_yield) / budget,
                        _SIMPLIFY_FLOOR_MM), _SIMPLIFY_CEILING_MM)
    simplified = segments
    spent = [_SIMPLIFY_WORK_LIMIT]
    for _ in range(_MAX_SIMPLIFY_PASSES):
        _check_yield(should_yield)
        simplified = [_simplify(segment, tolerance, spent, should_yield)
                      for segment in segments]
        if sum(len(segment) for segment in simplified) <= budget:
            return simplified
        if tolerance >= _SIMPLIFY_CEILING_MM:
            # The tolerance is at the fidelity ceiling: further passes
            # would weigh the same geometry against the same bound.
            break
        if spent[0] < 0:
            # The counter is gone: a later pass would return the same
            # completed subset, having nothing left to prove a chord
            # with. The search is over.
            break
        tolerance = min(tolerance * 2.0, _SIMPLIFY_CEILING_MM)
    return simplified


def _push(chain: List[List[float]], x: float, y: float, motion: int) -> None:
    """Append the edge's endpoint unless it repeats the chain's last
    vertex. A retract or prime at one position is a real state change
    with no XY geometry, and the duplicate vertex it would contribute
    is a zero-length stroke, never a line — so it is not added, and a
    chain that never moved keeps its single start vertex.
    """
    last = chain[-1]
    if x == last[0] and y == last[1]:
        return
    chain.append([x, y, float(motion)])


def _record_span(chain: List[List[float]], path: float, started: bool, closed: bool,
                 travels: List[List[List[float]]],
                 start_marks: List[List[float]],
                 end_marks: List[List[float]]) -> None:
    """A travel span reaches the payload only if the head moved.

    The glyphs mark the boundaries THIS layer actually holds: a travel
    that began in the previous layer draws its geometry here but gets no
    second start glyph, and one still open at the layer's end gets no
    end glyph — the boundary is in the next layer's file, not this one's.
    """
    if path <= _TRAVEL_EPSILON_MM:
        return
    travels.append(chain)
    if started:
        start_marks.append(list(chain[0]))
    if closed:
        end_marks.append(list(chain[-1]))


def _build(index: LayerMotionIndex, layer: int,
           should_yield: Optional[Callable[[], bool]] = None) -> tuple:
    """One edge walk over the layer: the class polylines, the travel
    channel's segments and the two glyph lists.

    Every motion's edge goes to the channel its motion belongs to: an
    extruding edge joins its feature run, a travel edge joins the open
    span. Nothing else is drawn — a pure-E retract or prime has a
    zero-length edge and disappears on its own.
    """
    classes: Dict[str, List[List[List[float]]]] = {}
    travels: List[List[List[float]]] = []
    start_marks: List[List[float]] = []
    end_marks: List[List[float]] = []
    # Whether the head was still extruding when the layer began: if it
    # was, a span opening on the layer's very first motion STARTS here
    # (its glyph is this layer's), and if it was not, that motion is the
    # continuation of a travel whose start is the previous layer's.
    opening_extruding = True
    if layer < len(index.layer_start_extruding):
        opening_extruding = bool(index.layer_start_extruding[layer])
    run_name: Optional[str] = None
    run_chain: Optional[List[List[float]]] = None
    span_chain: Optional[List[List[float]]] = None
    span_path = 0.0
    span_started = False
    for edge_number, (motion, x0, y0, x1, y1, code, extruding) in enumerate(
            motion_edges(index, layer)):
        _check_yield(should_yield, edge_number)
        if not extruding:
            # A travel breaks the class polyline; the span's first vertex
            # is the position the head already held, so the first travel
            # move draws its whole edge.
            run_name = None
            run_chain = None
            if span_chain is None:
                span_chain = [[x0, y0, float(motion)]]
                span_path = 0.0
                # A span opening on the layer's first motion while the
                # head was already travelling began in the previous
                # layer: this file holds its middle, not its start.
                span_started = motion != 0 or opening_extruding
            _push(span_chain, x1, y1, motion)
            span_path += hypot(x1 - x0, y1 - y0)
            continue
        if span_chain is not None:
            _record_span(span_chain, span_path, span_started, True,
                         travels, start_marks, end_marks)
            span_chain = None
        name = _type_name(index, code)
        if run_chain is None or name != run_name:
            # A fresh run: the edge that opens it starts where the head
            # already stood, so the first extrusion after a travel (or
            # after a feature change) draws its whole first move.
            run_chain = [[x0, y0, float(motion)]]
            classes.setdefault(name, []).append(run_chain)
            run_name = name
        _push(run_chain, x1, y1, motion)
    if span_chain is not None:
        # The layer ended inside a span: its geometry closes here, but
        # the travel itself ends in the next layer — no end glyph.
        _record_span(span_chain, span_path, span_started, False,
                     travels, start_marks, end_marks)
    return classes, travels, start_marks, end_marks


# The prepared-layer store: (index identity, layer) to the payload the
# last build returned, with the layer's motion count as the freshness
# stamp — a compact index hydrates in place, so the count is what tells
# an empty layer apart from one whose arrays have arrived. The index is
# held weakly: a live index keeps its entry valid, a collected one
# drops it rather than let a recycled id claim another index's layer.
_prepared_layers: Dict[Tuple[int, int], tuple] = {}
_prepared_lock = threading.Lock()


def _prepared_guard(index: LayerMotionIndex):
    """The index's own lock when it has one (the service holds it while
    it builds the bundle), so the store shares one lock with the index
    it is keyed on rather than adding a second one beside it."""
    return getattr(index, "cache_lock", None) or _prepared_lock


def _prepared_get(index: LayerMotionIndex, layer: int) -> Optional[dict]:
    with _prepared_guard(index):
        entry = _prepared_layers.get((id(index), layer))
    if entry is None:
        return None
    owner, count, payload = entry
    if owner() is not index or count != index.motion_count(layer):
        return None
    return payload


def _prepared_put(index: LayerMotionIndex, layer: int, payload: dict) -> None:
    with _prepared_guard(index):
        _prepared_layers[(id(index), layer)] = (
            weakref.ref(index), index.motion_count(layer), payload)
        while len(_prepared_layers) > _PREPARED_LIMIT:
            # Insertion-ordered: the oldest entry goes first, which is
            # the layer the window has moved furthest away from.
            _prepared_layers.pop(next(iter(_prepared_layers)))


def prepare_layer(index: LayerMotionIndex, layer: int,
                  should_yield: Optional[Callable[[], bool]] = None) -> Optional[dict]:
    """One layer's prepared geometry — the builder AND the memo.

    The preparation is the heavy half of this module: a full motion-edge
    walk plus the simplification. It is a PURE function of the index and
    the layer — no Qt, no UI state, nothing that must run where the
    snapshot is assembled — so the service's existing executor can own
    it: submit this per layer of the demanded window and the UI thread's
    own read is a memo hit. It is also what makes a warm read cheap, so
    a repeat (a re-read within the window, an anchor move that keeps
    two of its three layers) returns the payload the last build made.

    A layer that is not hydrated, or one the index does not have, reads
    as None — the payload must say "not loaded", never draw nothing as
    a lie.
    """
    if layer < 0 or layer >= index.layer_count():
        return None
    if index.compact and layer not in index.hydrated_layers:
        return None
    cached = _prepared_get(index, layer)
    if cached is not None:
        return cached
    payload = _prepare(index, layer, should_yield)
    _prepared_put(index, layer, payload)
    return payload


def layer_polylines(index: LayerMotionIndex, layer: int) -> Optional[dict]:
    """One hydrated layer's prepared geometry, or None when the layer
    is not hydrated (an evicted layer reads as empty — the payload
    must say so, never draw nothing as a lie)."""
    return prepare_layer(index, layer)


def _prepare(index: LayerMotionIndex, layer: int,
             should_yield: Optional[Callable[[], bool]] = None) -> dict:
    """The build itself: the edge walk, the per-class budgets and the
    travel channel, for a layer whose guards have already passed."""
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    if not len(xs):
        return {"classes": {}, "travels": [], "travelStarts": [], "travelEnds": [], "motions": 0}
    classes, travels, start_marks, end_marks = _build(index, layer, should_yield)
    # A chain of fewer than two vertices is a run whose only motions were
    # pure-E: it holds no edge and is dropped here, so the payload never
    # carries a class of it and the painter never sees a point.
    prepared = {}
    for name, segments in classes.items():
        drawn = [segment for segment in segments if len(segment) >= 2]
        if drawn:
            prepared[name] = _budgeted(drawn, MAX_POINTS_PER_CLASS, should_yield)
    return {
        "classes": prepared,
        "travels": _budgeted(travels, MAX_TRAVEL_POINTS, should_yield),
        "travelStarts": start_marks,
        "travelEnds": end_marks,
        "motions": len(xs),
    }


def encode_layer(payload: dict) -> bytes:
    """The prepared payload in the full cache's compact form.

    The painter's nested lists cost ~100+ bytes a point as Python
    objects; three f32s cost 12. Every layer of the print fits in the
    cache that way (the live request's instant-access store), and one
    decode rebuilds the QML payload on demand.
    """
    from array import array
    from struct import pack

    def pack_segments(segments):
        parts = [pack("<i", len(segments))]
        for segment in segments:
            flat = array("f")
            for x, y, motion in segment:
                flat.extend((float(x), float(y), float(motion)))
            parts.append(pack("<i", len(segment)))
            parts.append(flat.tobytes())
        return b"".join(parts)

    def pack_triples(triples):
        flat = array("f")
        for x, y, motion in triples:
            flat.extend((float(x), float(y), float(motion)))
        return pack("<i", len(triples)) + flat.tobytes()

    classes = payload.get("classes") or {}
    parts = [b"PPL1", pack("<i", int(payload.get("motions") or 0)),
             pack("<i", len(classes))]
    for name, segments in classes.items():
        raw = name.encode("utf-8")
        parts.append(pack("<B", len(raw)))
        parts.append(raw)
        parts.append(pack_segments(segments))
    parts.append(pack_segments(payload.get("travels") or ()))
    parts.append(pack_triples(payload.get("travelStarts") or ()))
    parts.append(pack_triples(payload.get("travelEnds") or ()))
    return b"".join(parts)


def decode_layer(raw: bytes) -> dict:
    """The compact form back into the painter's payload shape."""
    from array import array
    from struct import unpack_from

    if not isinstance(raw, (bytes, bytearray, memoryview)) or raw[:4] != b"PPL1":
        return {}
    offset = 4

    def read_triples():
        nonlocal offset
        count, = unpack_from("<i", raw, offset)
        offset += 4
        flat = array("f")
        flat.frombytes(raw[offset:offset + count * 12])
        offset += count * 12
        return [[flat[i * 3], flat[i * 3 + 1], int(flat[i * 3 + 2])]
                for i in range(count)]

    def read_segments():
        nonlocal offset
        seg_count, = unpack_from("<i", raw, offset)
        offset += 4
        segments = []
        for _ in range(seg_count):
            point_count, = unpack_from("<i", raw, offset)
            offset += 4
            flat = array("f")
            flat.frombytes(raw[offset:offset + point_count * 12])
            offset += point_count * 12
            segments.append([[flat[i * 3], flat[i * 3 + 1], int(flat[i * 3 + 2])]
                             for i in range(point_count)])
        return segments

    motions, = unpack_from("<i", raw, offset)
    offset += 4
    class_count, = unpack_from("<i", raw, offset)
    offset += 4
    classes = {}
    for _ in range(class_count):
        name_len = raw[offset]
        offset += 1
        name = raw[offset:offset + name_len].decode("utf-8")
        offset += name_len
        classes[name] = read_segments()
    travels = read_segments()
    starts = read_triples()
    ends = read_triples()
    return {"classes": classes, "travels": travels, "travelStarts": starts,
            "travelEnds": ends, "motions": motions}


def split_index(index: LayerMotionIndex, layer: int, file_position: int) -> Optional[int]:
    """The current layer's printed/unprinted boundary as a COUNT of
    motions: edge m is printed exactly when m < split, so split == 0
    paints nothing and split == N paints motions 0 .. N-1.

    The boundary is the number of motion lines the position has passed,
    which is what bisect_left over the offsets returns. bisect_right
    also counted a motion whose line the position had only just reached,
    so the painter — reading an inclusive index — painted the next,
    not-yet-executed edge (the review's off-by-one). One convention now:
    a count here, an exclusive test there, and the incremental delta
    from an old count F to a new one S draws the edges in [F, S).
    """
    if layer < 0 or layer >= index.layer_count():
        return None
    offsets = index.motion_offsets[layer] if layer < len(index.motion_offsets) else ()
    if not len(offsets):
        # The compact index's arrays hydrate on demand — an unhydrated
        # live layer must still resolve its coarse boundary (the live
        # report: the follow read "—" with a valid anchor and a fine
        # file position). The born-correct count times the layer's
        # byte-range fraction is the honest fallback.
        fraction, _method = index.file_fraction(layer, int(file_position))
        count = index.motion_count(layer)
        if count <= 0:
            return None
        return int(fraction * count)
    return int(bisect_left(offsets, int(file_position)))


def plate_layers(index: LayerMotionIndex, anchor: Optional[int]) -> dict:
    """The static half: the prev/current/next bundle. Immutable per
    anchor (and per hydration fill) — the service memoises it so the
    model's payload keeps its identity between splits."""
    if anchor is None:
        return {}
    layers = {}
    for slot, layer in (("prev", anchor - 1), ("current", anchor), ("next", anchor + 1)):
        layers[slot] = layer_polylines(index, layer)
    return layers


def plate_progress(index: LayerMotionIndex, anchor: Optional[int],
                   file_position: Optional[int] = None) -> dict:
    """The follower's payload: the memoised layers plus the volatile
    split and the method word (the honest degradation: a missing piece
    reads as unavailable, never as a wrong fill)."""
    if anchor is None:
        return {"layers": {}, "split": None, "method": "unavailable", "anchor": None}
    layers = plate_layers(index, anchor)
    split = None
    method = "unavailable"
    if layers.get("current") is not None and file_position is not None:
        split = split_index(index, anchor, file_position)
        method = "motion index"
    return {"layers": layers, "split": split, "method": method, "anchor": anchor}
