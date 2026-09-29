"""Pure polygon intersection primitives shared by indexing and presentation."""

def point_in_polygon(x, y, polygon):
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_bounds(polygon):
    """The polygon's bounding box, as (min_x, min_y, max_x, max_y) —
    the print walk's cheap rejection before any vertex test."""
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _orientation(ax, ay, bx, by, cx, cy):
    """The turn sign of a -> b -> c: 1 left, -1 right, 0 collinear."""
    value = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0


def _on_span(ax, ay, bx, by, px, py):
    """The collinear point *p* lies within the a -> b box."""
    return (min(ax, bx) <= px <= max(ax, bx)
            and min(ay, by) <= py <= max(ay, by))


def _segments_cross(ax, ay, bx, by, cx, cy, dx, dy):
    """True when segment a-b meets segment c-d, endpoints included."""
    first = _orientation(ax, ay, bx, by, cx, cy)
    second = _orientation(ax, ay, bx, by, dx, dy)
    third = _orientation(cx, cy, dx, dy, ax, ay)
    fourth = _orientation(cx, cy, dx, dy, bx, by)
    if first != second and third != fourth:
        return True
    # The collinear cases: a vertex standing on the other segment's span
    # still means the two meet.
    return ((first == 0 and _on_span(ax, ay, bx, by, cx, cy))
            or (second == 0 and _on_span(ax, ay, bx, by, dx, dy))
            or (third == 0 and _on_span(cx, cy, dx, dy, ax, ay))
            or (fourth == 0 and _on_span(cx, cy, dx, dy, bx, by)))


def segment_in_polygon(x0, y0, x1, y1, polygon):
    """True when the segment meets the polygon at all.

    An extrusion edge deposits inside the object whether it ends there
    or passes through: a corner clipping whose two ends both sit outside
    still prints material in the object, so the crossing test runs when
    the ends alone say nothing. The walk pre-rejects with the polygon's
    bounds, so this only sees segments that could meet it.
    """
    if point_in_polygon(x0, y0, polygon) or point_in_polygon(x1, y1, polygon):
        return True
    j = len(polygon) - 1
    for i in range(len(polygon)):
        if _segments_cross(x0, y0, x1, y1, polygon[j][0], polygon[j][1],
                           polygon[i][0], polygon[i][1]):
            return True
        j = i
    return False
