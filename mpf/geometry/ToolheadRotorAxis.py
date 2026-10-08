"""Conservative analytic cylinder consensus in already placed CAD coordinates."""
from __future__ import annotations
import math


def rotation_axis(candidates, low, high):
    """Return an axis only when every cylinder agrees on the same line."""
    if not candidates or low is None or len(candidates) > 64: return None, None
    point, direction = candidates[0]
    length = math.sqrt(sum(value*value for value in direction))
    if length < 1e-9: return None, None
    direction = tuple(value/length for value in direction)
    for other, axis in candidates[1:]:
        dot = sum(a*b for a, b in zip(direction, axis, strict=True))
        if abs(dot) < .9999: return None, None
        delta = tuple(a-b for a, b in zip(other, point, strict=True))
        along = sum(a*b for a, b in zip(delta, direction, strict=True))
        perpendicular = sum((delta[i]-direction[i]*along)**2 for i in range(3))
        if perpendicular > .0001: return None, None
    centre = [(a+b)/2 for a, b in zip(low, high, strict=True)]
    along = sum((centre[i]-point[i])*direction[i] for i in range(3))
    centre = [point[i] + direction[i]*along for i in range(3)]
    dominant = max(range(3), key=lambda i: abs(direction[i]))
    if direction[dominant] < 0: direction = tuple(-value for value in direction)
    return centre, list(direction)
