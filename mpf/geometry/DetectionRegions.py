"""Bounded simple polygons in normalized, untransformed camera coordinates."""

from hashlib import sha256
import json
from math import isfinite

MAX_REGIONS = 4
MAX_VERTICES = 32
MIN_AREA = 0.0001


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersects(a, b, c, d):
    values = (_cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b))
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    for point, start, end, value in ((c, a, b, values[0]), (d, a, b, values[1]),
                                      (a, c, d, values[2]), (b, c, d, values[3])):
        if abs(value) <= 1e-12 and (min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
                                   and min(start[1], end[1]) <= point[1] <= max(start[1], end[1])):
            return True
    return False


def validate_regions(value):
    if not isinstance(value, (list, tuple)) or len(value) > MAX_REGIONS:
        raise ValueError(f"Use at most {MAX_REGIONS} monitored regions")
    result = []
    for region in value:
        if not isinstance(region, (list, tuple)) or not 3 <= len(region) <= MAX_VERTICES:
            raise ValueError(f"Each polygon needs 3–{MAX_VERTICES} points")
        points = []
        for point in region:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Region points must contain two coordinates")
            if any(type(n) not in (int, float) or not 0 <= n <= 1 or not isfinite(n) for n in point):
                raise ValueError("Region points must lie inside the camera image")
            points.append(tuple(round(float(n), 8) for n in point))
        if len(set(points)) != len(points):
            raise ValueError("Polygon points must be distinct")
        count = len(points)
        for i in range(count):
            a, b, c = points[i - 1], points[i], points[(i + 1) % count]
            if abs(_cross(a, b, c)) <= 1e-12 and (a[0] - b[0]) * (c[0] - b[0]) + (a[1] - b[1]) * (c[1] - b[1]) > 0:
                raise ValueError("Polygon edges must not double back")
        for i in range(count):
            a, b = points[i], points[(i + 1) % count]
            for j in range(i + 1, count):
                if j == i + 1 or (i == 0 and j == count - 1):
                    continue
                if _intersects(a, b, points[j], points[(j + 1) % count]):
                    raise ValueError("Polygon edges must not cross or touch")
        area = sum(points[i][0] * points[(i + 1) % count][1]
                   - points[(i + 1) % count][0] * points[i][1] for i in range(count)) / 2
        if abs(area) < MIN_AREA:
            raise ValueError("The monitored region is too small")
        # Direction and starting vertex do not change the monitored pixels.
        if area < 0:
            points.reverse()
        first = min(range(count), key=lambda i: points[i])
        result.append(tuple(points[first:] + points[:first]))
    return tuple(sorted(set(result)))


def fingerprint(regions):
    return sha256(json.dumps(validate_regions(regions), separators=(",", ":")).encode()).hexdigest()[:20]


def persisted_regions(value):
    """Preserve invalid cameras as None: corruption must not enable full-frame AI."""
    if not isinstance(value, dict):
        return {"invalid": None}
    if len(value) > 32 or any(not isinstance(camera, str) or len(camera) > 256 for camera in value):
        return {"invalid": None}
    result = {}
    for camera, regions in value.items():
        if not isinstance(camera, str) or len(camera) > 256:
            continue
        try:
            result[camera] = validate_regions(regions)
        except (TypeError, ValueError, OverflowError):
            result[camera] = None
    return result
