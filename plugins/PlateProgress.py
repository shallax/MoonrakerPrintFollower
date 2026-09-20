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
"""
from __future__ import annotations

from bisect import bisect_left
from math import hypot
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

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
    """
    count = len(points)
    if count <= 2:
        return list(points)
    tolerance_sq = tolerance * tolerance
    keep = [False] * count
    keep[0] = keep[count - 1] = True
    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        x0, y0 = points[first][0], points[first][1]
        x1, y1 = points[last][0], points[last][1]
        dx = x1 - x0
        dy = y1 - y0
        span_sq = dx * dx + dy * dy
        worst = -1.0
        worst_index = -1
        for index in range(first + 1, last):
            distance_sq = _point_segment_distance_sq(
                points[index][0], points[index][1], x0, y0, dx, dy, span_sq)
            if distance_sq > worst:
                worst = distance_sq
                worst_index = index
        if worst > tolerance_sq:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))
    return [points[index] for index in range(count) if keep[index]]


def _segments_path(segments: Sequence[Sequence[Sequence[float]]]) -> float:
    """The channels' total path length: the simplification's scale."""
    total = 0.0
    for segment in segments:
        for index in range(1, len(segment)):
            total += hypot(segment[index][0] - segment[index - 1][0],
                           segment[index][1] - segment[index - 1][1])
    return total


def _budgeted(segments: List[List[List[float]]],
              budget: int) -> List[List[List[float]]]:
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
    """
    total = sum(len(segment) for segment in segments)
    if total <= budget:
        return segments
    tolerance = min(max(_segments_path(segments) / budget, _SIMPLIFY_FLOOR_MM), _SIMPLIFY_CEILING_MM)
    simplified = segments
    for _ in range(_MAX_SIMPLIFY_PASSES):
        simplified = [_douglas_peucker(segment, tolerance) for segment in segments]
        if sum(len(segment) for segment in simplified) <= budget:
            return simplified
        if tolerance >= _SIMPLIFY_CEILING_MM:
            # The tolerance is at the fidelity ceiling: further passes
            # would weigh the same geometry against the same bound.
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


def _build(index: LayerMotionIndex, layer: int) -> tuple:
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
    for motion, x0, y0, x1, y1, code, extruding in motion_edges(index, layer):
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


def layer_polylines(index: LayerMotionIndex, layer: int) -> Optional[dict]:
    """One hydrated layer's prepared geometry, or None when the layer
    is not hydrated (an evicted layer reads as empty — the payload
    must say so, never draw nothing as a lie)."""
    if layer < 0 or layer >= index.layer_count():
        return None
    if index.compact and layer not in index.hydrated_layers:
        return None
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    if not len(xs):
        return {"classes": {}, "travels": [], "travelStarts": [], "travelEnds": [], "motions": 0}
    classes, travels, start_marks, end_marks = _build(index, layer)
    # A chain of fewer than two vertices is a run whose only motions were
    # pure-E: it holds no edge and is dropped here, so the payload never
    # carries a class of it and the painter never sees a point.
    prepared = {}
    for name, segments in classes.items():
        drawn = [segment for segment in segments if len(segment) >= 2]
        if drawn:
            prepared[name] = _budgeted(drawn, MAX_POINTS_PER_CLASS)
    return {
        "classes": prepared,
        "travels": _budgeted(travels, MAX_TRAVEL_POINTS),
        "travelStarts": start_marks,
        "travelEnds": end_marks,
        "motions": len(xs),
    }


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
        return None
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
