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
# report). The CLASS polylines stride per class instead (below): a
# class of tiny moves must keep its fidelity.
MAX_POINTS_PER_LAYER = 12000
# The per-class point budget: each class strides to ITS OWN path —
# the layer-wide stride starved a class of tiny moves (the skin's
# 0.027 mm lines vanished entirely: the live report). The raster
# stack changes the economics: the layers rasterise once per anchor
# and the full re-raster amortises over ~20 paints. The raise to
# 200k puts the dense classes at the 0.35 mm floor — the skin's
# lines draw at their true pitch, never strided (the live
# quantisation reports).
MAX_POINTS_PER_CLASS = 200000
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


def _decimate(index: LayerMotionIndex, layer: int) -> Dict[str, List[List[List[float]]]]:
    """One pass over the motions: per feature class, distance-
    filtered polylines in bed mm. Each class strides to ITS OWN path
    (the live ruling: a class of tiny moves — the skin's 0.027 mm
    lines — must draw as lines, not vanish under a layer-wide
    stride). A MOVING travel between kept points breaks the segment
    (the phantom-line guard); a stationary retract-prime pulse does
    NOT — nothing moved, so the polyline continues invisibly across
    it. The travel motions never enter the class polylines (they
    belong to the travel channel). Each kept point carries its motion
    index — the printed/unprinted split lands on these points."""
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    ys = index.motion_y[layer] if layer < len(index.motion_y) else ()
    count = len(xs)
    if count == 0:
        return {}
    starts = index.travel_starts[layer] if layer < len(index.travel_starts) else ()
    ends = index.travel_ends[layer] if layer < len(index.travel_ends) else ()
    # Pre-pass: each class's own path length over the non-travel
    # motions — the per-class stride's denominator.
    class_paths: Dict[str, float] = {}
    previous: Dict[str, tuple] = {}
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
            continue
        name = _type_name(index, _code_at(index, layer, motion))
        x, y = float(xs[motion]), float(ys[motion])
        if name in previous:
            class_paths[name] += hypot(x - previous[name][0], y - previous[name][1])
        else:
            class_paths[name] = 0.0
        previous[name] = (x, y)
    thresholds = {name: max(MIN_SEGMENT_MM, path / MAX_POINTS_PER_CLASS)
                  for name, path in class_paths.items()}
    classes: Dict[str, List[List[List[float]]]] = {}
    last_kept: Dict[str, List[float]] = {}
    last_seen: Dict[str, List[float]] = {}
    span_seen: Dict[str, bool] = {}
    in_travel = not bool(index.layer_start_extruding[layer]
                         if layer < len(index.layer_start_extruding) else True)
    start_i = end_i = 0
    span_first: Optional[int] = None
    pending_join: Dict[str, Optional[List[float]]] = {}
    for motion in range(count):
        if start_i < len(starts) and motion == starts[start_i]:
            in_travel = True
            start_i += 1
            span_first = motion
        if end_i < len(ends) and motion == ends[end_i]:
            in_travel = False
            end_i += 1
            # EVERY span breaks the class polyline: a stationary
            # retract-prime pulse is a real seam, and merging across
            # it chords the corner where the next line starts (the
            # live report: straight lines read as slight diagonals).
            # A STATIONARY seam hands the next segment its true start
            # — the point before the pulse IS the line's beginning,
            # so a one-motion line draws as its real line, never a
            # dot (the live report: orange blobs at the skin's line
            # ends).
            stationary = False
            if span_first is not None:
                dx = float(xs[motion - 1]) - float(xs[span_first])
                dy = float(ys[motion - 1]) - float(ys[span_first])
                stationary = hypot(dx, dy) < MIN_TRAVEL_MM
            for name in span_seen:
                span_seen[name] = True
                pending_join[name] = last_seen.get(name) if stationary else None
            span_first = None
        if in_travel:
            continue
        name = _type_name(index, _code_at(index, layer, motion))
        point = [float(xs[motion]), float(ys[motion]), float(motion)]
        threshold = thresholds[name]
        previous = last_kept.get(name)
        if previous is None or span_seen.get(name, False):
            # A fresh segment: the class's first point, or the first
            # point after a travel. The distance never breaks a
            # segment — a long straight move is ONE sparse raw
            # step, and breaking on distance erased every straight
            # line (the live report); continuous extrusion joins.
            if previous is not None and span_seen.get(name, False):
                # The closing segment keeps its TRUE END — the last
                # extrusion before the travel — so the strided
                # polyline never overshoots or falls short of the
                # real path (the quantisation report).
                segment = classes.get(name, [])
                if segment and segment[-1] and last_seen.get(name) is not None \
                        and last_seen[name] != segment[-1][-1]:
                    segment[-1].append(last_seen[name])
            segment = classes.setdefault(name, [])
            join = pending_join.get(name)
            if join is not None and join != point:
                segment.append([join, point])
            else:
                segment.append([point])
            pending_join[name] = None
            span_seen[name] = False
            last_kept[name] = point
            last_seen[name] = point
        elif hypot(point[0] - previous[0], point[1] - previous[1]) >= threshold:
            classes[name][-1].append(point)
            last_kept[name] = point
            last_seen[name] = point
        else:
            last_seen[name] = point
        # Closer than the stride: decimated away, and not the
        # distance reference for the next kept point.
    # The layer's last segments keep their true ends the same way.
    for name in classes:
        segment = classes[name]
        if segment and segment[-1] and last_seen.get(name) is not None \
                and last_seen[name] != segment[-1][-1]:
            segment[-1].append(last_seen[name])
    return classes


# A travel span's minimum repositioning, in bed mm: below it the span
# is a retract-prime pulse with no movement (the live file's skin
# seams — E pauses at every patch boundary without the toolhead going
# anywhere) and reads as a travel only by mistake (the live report).
MIN_TRAVEL_MM = 0.5


def _travel_geometry(index: LayerMotionIndex, layer: int,
                     threshold: float) -> tuple:
    """The travel channel: per-span decimated segments plus the span
    boundary glyph markers, BUILT FROM ONE SPAN WALK. Stationary
    retract-prime pulses contribute nothing — a travel is the
    repositioning between where extrusion stopped and where it
    resumed (the live report: every skin seam drew a start/stop
    glyph pair). Each point carries its motion index — the face
    clips them to the printed portion (the live ruling: travels show
    only after they have been passed). A layer that opened mid-travel
    starts in the span."""
    xs = index.motion_x[layer] if layer < len(index.motion_x) else ()
    ys = index.motion_y[layer] if layer < len(index.motion_y) else ()
    count = len(xs)
    if count == 0:
        return [], [], []
    starts = list(index.travel_starts[layer] if layer < len(index.travel_starts) else ())
    ends = list(index.travel_ends[layer] if layer < len(index.travel_ends) else ())
    start_index = 0
    end_index = 0
    in_travel = not bool(index.layer_start_extruding[layer]
                         if layer < len(index.layer_start_extruding) else True)
    segments: List[List[List[float]]] = []
    start_marks: List[List[float]] = []
    end_marks: List[List[float]] = []
    segment: Optional[List[List[float]]] = None
    span_first: Optional[int] = None
    last: Optional[List[float]] = None
    for motion in range(count):
        if start_index < len(starts) and motion == starts[start_index]:
            # A new span starts its own segment.
            in_travel = True
            start_index += 1
            segment = None
            span_first = motion
            last = None
        if end_index < len(ends) and motion == ends[end_index]:
            in_travel = False
            end_index += 1
            # The span's verdict: a real repositioning keeps its
            # segment and both glyphs; a stationary pulse vanishes.
            if span_first is not None:
                dx = float(xs[motion - 1]) - float(xs[span_first])
                dy = float(ys[motion - 1]) - float(ys[span_first])
                if hypot(dx, dy) >= MIN_TRAVEL_MM:
                    # A one-point segment keeps the span's last point
                    # — the QML drops sub-2-point segments and a short
                    # span would never draw (the skin-report twin).
                    # A one-motion span has no interior point; its
                    # segment stays single and the painter draws the
                    # dot.
                    if segment is not None and len(segment) == 1 and motion - 1 > span_first:
                        segment[-1].append([float(xs[motion - 1]), float(ys[motion - 1]), float(motion - 1)])
                    if segment is not None:
                        segments.append(segment)
                    start_marks.append([float(xs[span_first]), float(ys[span_first]), float(span_first)])
                    end_marks.append([float(xs[motion - 1]), float(ys[motion - 1]), float(motion - 1)])
                elif segment is not None:
                    # The stationary pulse's segment never lands.
                    segment = None
                    last = None
            span_first = None
        if not in_travel:
            continue
        if span_first is None:
            span_first = motion
        point = [float(xs[motion]), float(ys[motion]), float(motion)]
        if segment is None or last is None \
                or hypot(point[0] - last[0], point[1] - last[1]) >= threshold:
            if segment is None:
                segment = []
            segment.append(point)
            last = point
    # The layer's last span crossing into the next layer: the walk
    # ends inside it — close it with the same verdict.
    if in_travel and segment is not None and span_first is not None:
        dx = float(xs[count - 1]) - float(xs[span_first])
        dy = float(ys[count - 1]) - float(ys[span_first])
        if hypot(dx, dy) >= MIN_TRAVEL_MM:
            if len(segment) == 1 and count - 1 > span_first:
                segment[-1].append([float(xs[count - 1]), float(ys[count - 1]), float(count - 1)])
            segments.append(segment)
            start_marks.append([float(xs[span_first]), float(ys[span_first]), float(span_first)])
            end_marks.append([float(xs[count - 1]), float(ys[count - 1]), float(count - 1)])
    return segments, start_marks, end_marks


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
    threshold = _path_threshold(index, layer)
    travels, travel_starts, travel_ends = _travel_geometry(index, layer, threshold)
    return {
        "classes": _decimate(index, layer),
        "travels": travels,
        "travelStarts": travel_starts,
        "travelEnds": travel_ends,
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
