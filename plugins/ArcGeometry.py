"""The physical geometry of ONE logical motion.

A motion is an edge: it runs from the position the head held when the
move began to the position the move commanded. For G0/G1 that edge is a
straight line, and the index's endpoint arrays are all the geometry
there is. For G2/G3 the commanded move is a circular (or helical) path
whose endpoint is only where it finishes, so the physical geometry is a
polyline the consumers need in place of the endpoint pair.

This module is the one place that turns a parsed arc into that
polyline, and the one place that measures a live position against it.
``PlateProgress.motion_edges`` walks the layer through ``tessellate``
for the payload and the printed-object walk, and ``refined_fraction``
matches the live toolhead through ``closest`` — the same circle, the
same parameterisation, one tolerance budget. A consumer that only needs
"where did the head go" asks ``tessellate``; none of them re-derive the
maths.

The semantics are Klipper's ``gcode_arcs``: G2 clockwise, G3
counter-clockwise, G17/G18/G19 selecting the plane, the centre given as
an OFFSET from the start (I/J in XY, I/K in XZ, J/K in YZ), and the
remaining axis interpolated linearly along the sweep (the helix).

A descriptor is the sparse per-motion record the index carries: it is
deliberately small (plane, direction, the two plane offsets) because
almost every motion of a real file is a G0/G1 and must not pay for the
few that are arcs. It is never a second geometry model: everything the
descriptor means is read back through the functions below.

Malformed arcs degrade to the endpoint chord by carrying NO descriptor
rather than by a special case in the consumers, so an unusable arc can
never crash a parse, a payload build or a live-position sample.
"""
from __future__ import annotations

from math import acos, atan2, ceil, cos, hypot, isfinite, pi, sin
from typing import Dict, List, Optional, Sequence, Tuple

# The modal plane codes. XY is the default: a file that never selects a
# plane is entirely G17, which is also what the index assumes.
PLANE_XY = 17
PLANE_XZ = 18
PLANE_YZ = 19
PLANES = (PLANE_XY, PLANE_XZ, PLANE_YZ)

# A descriptor: (plane, clockwise, offset along the plane's first axis,
# offset along its second).
ArcDescriptor = Tuple[int, bool, float, float]

# plane -> (first axis, second axis, helical axis) as XYZ indices, and
# the two centre-offset words that belong to those axes.
_PLANE_AXES = {PLANE_XY: (0, 1, 2), PLANE_XZ: (0, 2, 1), PLANE_YZ: (1, 2, 0)}
_PLANE_WORDS = {PLANE_XY: ("I", "J"), PLANE_XZ: ("I", "K"), PLANE_YZ: ("J", "K")}

# The tessellation budget, one number shared with the payload's own
# simplification ceiling (see PlateProgress): the chord error of a
# generated subedge stays under this, so the two never disagree about
# how far a drawn curve may sit from the commanded one.
MAX_SAGITTA_MM = 0.03
# The subedge length cap. The sagitta rule alone spends segments in
# proportion to the radius; a long arc also needs a bound in millimetres
# or one motion could hand the painter a stroke longer than a feature.
MAX_SEGMENT_MM = 1.0
# A defensive bound on one motion's subdivision count. A hostile or
# absurd arc (a 10-metre radius, a sweep of many turns) would otherwise
# ask for an unbounded list; past this the subedges grow longer than
# MAX_SEGMENT_MM but stay on the commanded path, and the count stays
# bounded for the payload, the cache and the live-position search.
MAX_SEGMENTS = 4096
# Below this radius the two offsets are numerically zero: Klipper
# rejects such an arc outright, and the file is rendered as the chord.
_MIN_RADIUS_MM = 1.0e-6

_TAU = 2.0 * pi


def degenerate(offset_a: float, offset_b: float) -> bool:
    """The two plane offsets are numerically zero: nothing to turn about."""
    return abs(offset_a) < _MIN_RADIUS_MM and abs(offset_b) < _MIN_RADIUS_MM


def descriptor(plane: int, clockwise: bool, words: Dict[str, float], *,
               absolute_xyz: bool = True, scale: float = 1.0) -> Optional[ArcDescriptor]:
    """The arc's sparse descriptor, or None when Klipper would reject it.

    *words* holds the line's I/J/K (and R) numbers as parsed. The
    rejected forms are Klipper's own: an arc under G91 (no relative arc
    moves), a radius-form arc (R), and an arc whose two applicable
    centre offsets are both zero (no centre to turn about). Each is
    rejected by answering None, which the index reads as "no arc here":
    the motion keeps its endpoint, its E and its feature, and draws as
    the straight edge it would have been without the offsets.
    """
    if not absolute_xyz or plane not in _PLANE_AXES or "R" in words:
        return None
    word_a, word_b = _PLANE_WORDS[plane]
    offset_a = float(words.get(word_a, 0.0)) * scale
    offset_b = float(words.get(word_b, 0.0)) * scale
    if not (isfinite(offset_a) and isfinite(offset_b)):
        return None
    if degenerate(offset_a, offset_b):
        return None
    return (plane, bool(clockwise), offset_a, offset_b)


def _frame(start: Sequence[float], target: Sequence[float], desc: ArcDescriptor):
    """The arc's plane frame: centre, radius, start angle and signed sweep.

    The sweep carries the direction: positive counter-clockwise,
    negative clockwise, |sweep| the angle actually travelled. A target
    equal to the start is a full turn in the commanded direction — an
    arc is never zero-length — and the wrap-around is resolved from the
    direction rather than from the shortest angle between the two
    radius vectors, so a start at 350 degrees and a target at 10 degrees
    sweeps 20 degrees counter-clockwise and 340 clockwise.
    """
    plane, clockwise, offset_a, offset_b = desc
    axis_a, axis_b, axis_h = _PLANE_AXES[plane]
    start_a, start_b = float(start[axis_a]), float(start[axis_b])
    target_a, target_b = float(target[axis_a]), float(target[axis_b])
    centre_a = start_a + offset_a
    centre_b = start_b + offset_b
    radius = hypot(start_a - centre_a, start_b - centre_b)
    start_angle = atan2(start_b - centre_b, start_a - centre_a)
    target_angle = atan2(target_b - centre_b, target_a - centre_a)
    if clockwise:
        sweep = -((start_angle - target_angle) % _TAU)
        if sweep == 0.0:
            sweep = -_TAU
    else:
        sweep = (target_angle - start_angle) % _TAU
        if sweep == 0.0:
            sweep = _TAU
    return (centre_a, centre_b, radius, start_angle, sweep, axis_a, axis_b, axis_h)


def _point_at(frame, start: Sequence[float], target: Sequence[float], t: float) -> Tuple[float, float, float]:
    """The commanded position at *t* along the sweep (0 start, 1 end).

    The helical axis interpolates linearly, so a G17 arc that also
    changes Z is a true helix rather than a circle with a Z jump, and
    the G18/G19 planes come out the same way.
    """
    centre_a, centre_b, radius, start_angle, sweep, axis_a, axis_b, axis_h = frame
    angle = start_angle + sweep * t
    values = [0.0, 0.0, 0.0]
    values[axis_a] = centre_a + radius * cos(angle)
    values[axis_b] = centre_b + radius * sin(angle)
    values[axis_h] = float(start[axis_h]) + (float(target[axis_h]) - float(start[axis_h])) * t
    return (values[0], values[1], values[2])


def _ceil_bound(value: float, ceiling: int) -> int:
    """ceil(*value*) clamped into [1, *ceiling*], never raising on inf."""
    if not isfinite(value) or value >= ceiling:
        return ceiling
    if value <= 1.0:
        return 1
    return int(ceil(value))


def subdivisions(desc: ArcDescriptor, start: Sequence[float], target: Sequence[float], *,
                 max_sagitta: float = MAX_SAGITTA_MM, max_segment: float = MAX_SEGMENT_MM,
                 max_segments: int = MAX_SEGMENTS) -> int:
    """How many subedges one arc needs, from geometry rather than taste.

    Two criteria, the stricter winning: the chord error of a subedge
    stays under *max_sagitta* (the visible tolerance — the angle whose
    sagitta on this radius equals it), and no subedge's full 3D length
    (circular arc plus helical travel) exceeds *max_segment*. A tiny arc
    answers one, so a nearly-straight commanded arc never explodes into
    points.
    """
    _centre_a, _centre_b, radius, _start_angle, sweep, _axis_a, _axis_b, axis_h = _frame(start, target, desc)
    sweep = abs(sweep)
    by_sagitta = 1.0
    if radius > max_sagitta:
        ratio = 1.0 - max_sagitta / radius
        step = 2.0 * acos(max(-1.0, min(1.0, ratio)))
        if step > 0.0:
            by_sagitta = sweep / step
    helical = abs(float(target[axis_h]) - float(start[axis_h]))
    by_length = hypot(sweep * radius, helical) / max_segment if max_segment > 0.0 else 1.0
    return max(_ceil_bound(by_sagitta, max_segments), _ceil_bound(by_length, max_segments))


def tessellate(desc: ArcDescriptor, start: Sequence[float], target: Sequence[float], *,
               max_sagitta: float = MAX_SAGITTA_MM, max_segment: float = MAX_SEGMENT_MM,
               max_segments: int = MAX_SEGMENTS) -> List[Tuple[float, float, float]]:
    """The arc as points after its start, ending at the exact target.

    The endpoint is the index's own stored position rather than the
    last trig step, so the next motion's edge starts exactly where this
    one ended and the endpoint the split and the toolhead follow is the
    one the file commanded.
    """
    frame = _frame(start, target, desc)
    count = subdivisions(desc, start, target, max_sagitta=max_sagitta,
                         max_segment=max_segment, max_segments=max_segments)
    if count <= 1:
        return [(float(target[0]), float(target[1]), float(target[2]))]
    points = [_point_at(frame, start, target, index / count) for index in range(1, count)]
    points.append((float(target[0]), float(target[1]), float(target[2])))
    return points


def point_at(desc: ArcDescriptor, start: Sequence[float], target: Sequence[float],
             t: float) -> Tuple[float, float, float]:
    """The arc's position at fraction *t*, endpoints exact."""
    if t <= 0.0:
        return (float(start[0]), float(start[1]), float(start[2]))
    if t >= 1.0:
        return (float(target[0]), float(target[1]), float(target[2]))
    return _point_at(_frame(start, target, desc), start, target, t)


def closest(desc: ArcDescriptor, start: Sequence[float], target: Sequence[float],
            position: Sequence[float]) -> Optional[Tuple[float, float]]:
    """The live position's distance to the arc, and where along it that is.

    Answers ``(distance_mm, t)`` for the nearest point of the commanded
    path, or None for an arc with no radius to speak of. The angular
    position is clamped into the sweep, so a sample beside the arc but
    past either end measures to that end — the same answer the
    tessellated polyline would give, without tessellating.

    The candidates are the angular position (when it is on the sweep)
    and the two ends, and they are compared by their REAL 3D distance:
    the angle alone cannot separate them on a full-circle helix, whose
    start and target share their planar offset while the seam between
    them is exactly the helical travel. The angular candidate is tried
    first and ties keep it, so a planar full circle — where both ends
    are the same point — still answers t = 0 deterministically.
    """
    frame = _frame(start, target, desc)
    centre_a, centre_b, radius, start_angle, sweep, axis_a, axis_b, _axis_h = frame
    if radius <= 0.0:
        return None
    point_a = float(position[axis_a]) - centre_a
    point_b = float(position[axis_b]) - centre_b
    candidates: List[float] = []
    if point_a != 0.0 or point_b != 0.0:
        angle = atan2(point_b, point_a)
        direction = -1.0 if sweep < 0.0 else 1.0
        travelled = ((angle - start_angle) * direction) % _TAU
        if travelled <= abs(sweep):
            candidates.append(travelled / abs(sweep) if sweep != 0.0 else 0.0)
    candidates.append(0.0)
    candidates.append(1.0)
    px = float(position[0])
    py = float(position[1])
    pz = float(position[2])
    best_distance = float("inf")
    best_t = 0.0
    for t in candidates:
        q = point_at(desc, start, target, t)
        distance = hypot(hypot(px - q[0], py - q[1]), pz - q[2])
        if distance < best_distance:
            best_distance = distance
            best_t = t
    return (best_distance, best_t)
