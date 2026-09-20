"""The plate's progress payload: prepared polylines for the follower
face, built from the index's motion arrays.

The payload is prepared HERE (pure, worker-friendly), never in QML:
each layer's motions decimate into a bed-space distance-filtered
polyline per feature class (the review's rule: a distance filter,
never head-truncation — a truncated layer reads as a lie about the
print), with the travel boundaries as scene-ready markers and the
printed/unprinted split index for the current layer. The architecture
contract forbids the mutable index arrays crossing the worker
boundary; only these built lists do.
"""
from __future__ import annotations

from bisect import bisect_right
from math import hypot
from typing import Dict, List, Optional

from .GCodeIndex import LayerMotionIndex

# The per-layer point budget: a dense layer's motions can number in
# the hundreds of thousands; the distance filter decimates toward
# this ceiling. The threshold keys off the layer's TOTAL PATH LENGTH
# (the span was wrong — a dense infill's path dwarfs its span, and
# the paint lagged seconds behind the live toolhead: the live
# report).
MAX_POINTS_PER_LAYER = 12000
# The bed-space minimum segment length (mm): shorter runs collapse.
MIN_SEGMENT_MM = 0.35

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


def _code_at(index: LayerMotionIndex, layer: int, motion: int) -> int:
    runs = index.motion_types[layer] if layer < len(index.motion_types) else []
    walked = 0
    for count, code in runs:
        if motion < walked + count:
            return code
        walked += count
    return _TYPE_NONE


def _path_threshold(index: LayerMotionIndex, layer: int) -> float:
    """The layer's decimation threshold: the total non-travel PATH
    length over the point budget (a dense infill's path dwarfs its
    span — keying on the span left the threshold at the floor and
    the paint lagged seconds behind the toolhead: the live report),
    with the bed-space floor."""
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    ys = index.motion_y[layer] if layer < len(index.motion_y) else ()
    count = len(xs)
    if count == 0:
        return MIN_SEGMENT_MM
    starts = index.travel_starts[layer] if layer < len(index.travel_starts) else ()
    ends = index.travel_ends[layer] if layer < len(index.travel_ends) else ()
    total_path = 0.0
    in_travel = not bool(index.layer_start_extruding[layer]
                         if layer < len(index.layer_start_extruding) else True)
    start_i = end_i = 0
    last_x, last_y = float(xs[0]), float(ys[0])
    for motion in range(1, count):
        if start_i < len(starts) and motion == starts[start_i]:
            in_travel = True
            start_i += 1
        if end_i < len(ends) and motion == ends[end_i]:
            in_travel = False
            end_i += 1
        if in_travel:
            continue
        total_path += hypot(float(xs[motion]) - last_x, float(ys[motion]) - last_y)
        last_x, last_y = float(xs[motion]), float(ys[motion])
    return max(MIN_SEGMENT_MM, total_path / MAX_POINTS_PER_LAYER)


def _decimate(index: LayerMotionIndex, layer: int,
              threshold: float) -> Dict[str, List[List[List[float]]]]:
    """One pass over the motions: per feature class, distance-
    filtered polylines in bed mm, BROKEN INTO SEGMENTS at the travel
    spans. The travel motions never enter the class polylines (they
    belong to the travel channel), and a travel between two kept
    points starts a new segment — without the break the stroke
    bridges the gap and paints phantom lines between objects (the
    live report). Each kept point carries its motion index — the
    printed/unprinted split lands on these points, not on the raw
    motions."""
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    ys = index.motion_y[layer] if layer < len(index.motion_y) else ()
    count = len(xs)
    if count == 0:
        return {}
    starts = index.travel_starts[layer] if layer < len(index.travel_starts) else ()
    ends = index.travel_ends[layer] if layer < len(index.travel_ends) else ()
    classes: Dict[str, List[List[List[float]]]] = {}
    last: Dict[str, List[float]] = {}
    travel_seen: Dict[str, bool] = {}
    in_travel = not bool(index.layer_start_extruding[layer]
                         if layer < len(index.layer_start_extruding) else True)
    start_i = end_i = 0
    for motion in range(count):
        if start_i < len(starts) and motion == starts[start_i]:
            in_travel = True
            start_i += 1
        if end_i < len(ends) and motion == ends[end_i]:
            in_travel = False
            end_i += 1
        if in_travel:
            # A travel intervenes: every class with a kept point must
            # start a fresh segment on its next kept point.
            for name in travel_seen:
                travel_seen[name] = True
            continue
        name = _type_name(index, _code_at(index, layer, motion))
        point = [float(xs[motion]), float(ys[motion]), float(motion)]
        previous = last.get(name)
        if previous is None or travel_seen.get(name, False):
            # A fresh segment: the class's first point, or the first
            # point after a travel. The distance never breaks a
            # segment — continuous extrusion joins, whatever the
            # stride.
            classes.setdefault(name, []).append([point])
            travel_seen[name] = False
            last[name] = point
        elif hypot(point[0] - previous[0], point[1] - previous[1]) >= threshold:
            classes[name][-1].append(point)
            last[name] = point
        # Closer than the stride: decimated away, and not the
        # distance reference for the next kept point.
    return classes


def _travel_points(index: LayerMotionIndex, layer: int, threshold: float) -> List[List[float]]:
    """The travel spans as one distance-decimated polyline, each point
    carrying its motion index — the face clips them to the printed
    portion (the live ruling: travels show only after they have been
    passed). A layer that opened mid-travel starts in the span."""
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    ys = index.motion_y[layer] if layer < len(index.motion_y) else ()
    count = len(xs)
    if count == 0:
        return []
    starts = list(index.travel_starts[layer] if layer < len(index.travel_starts) else ())
    ends = list(index.travel_ends[layer] if layer < len(index.travel_ends) else ())
    start_index = 0
    end_index = 0
    # A layer that opened not extruding (a cross-layer travel's tail)
    # begins the span at motion zero.
    in_travel = not bool(index.layer_start_extruding[layer]
                         if layer < len(index.layer_start_extruding) else True)
    points: List[List[float]] = []
    last: Optional[List[float]] = None
    for motion in range(count):
        if start_index < len(starts) and motion == starts[start_index]:
            in_travel = True
            start_index += 1
        if end_index < len(ends) and motion == ends[end_index]:
            in_travel = False
            end_index += 1
        if not in_travel:
            continue
        point = [float(xs[motion]), float(ys[motion]), float(motion)]
        if last is None or hypot(point[0] - last[0], point[1] - last[1]) >= threshold:
            points.append(point)
            last = point
    return points


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
    starts = index.travel_starts[layer] if layer < len(index.travel_starts) else ()
    ends = index.travel_ends[layer] if layer < len(index.travel_ends) else ()
    ys = index.motion_y[layer]
    threshold = _path_threshold(index, layer)
    return {
        "classes": _decimate(index, layer, threshold),
        "travels": _travel_points(index, layer, threshold),
        "travelStarts": [[float(xs[m]), float(ys[m]), float(m)] for m in starts if m < len(xs)],
        "travelEnds": [[float(xs[m]), float(ys[m]), float(m)] for m in ends if m < len(xs)],
        "motions": len(xs),
    }


def split_index(index: LayerMotionIndex, layer: int, file_position: int) -> Optional[int]:
    """The current layer's printed/unprinted boundary: the motion whose
    byte offset last covers the live file position (the review's H3 —
    bytes quantise to motions, and the dot derives from the same
    index)."""
    if layer < 0 or layer >= index.layer_count():
        return None
    offsets = index.motion_offsets[layer] if layer < len(index.motion_offsets) else ()
    if not len(offsets):
        return None
    return int(bisect_right(offsets, int(file_position)))


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
