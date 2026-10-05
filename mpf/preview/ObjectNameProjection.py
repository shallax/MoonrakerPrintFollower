"""Screen-space placement for Preview object-name banners."""
from __future__ import annotations

import math

from ..geometry.Polygons import point_in_polygon


def footprint_hits(objects, origin, direction):
    """Return every footprint intersected by a camera ray, in row order.

    Each footprint lives on the displayed layer plane for its object.
    Overlapping XY regions deliberately return multiple names: a G-code
    Preview ray has no per-object depth ID with which to choose one.
    """
    if abs(direction[1]) < 1e-9:
        return []
    names = []
    for item in objects:
        polygon = item.get("footprint") or ()
        if len(polygon) < 3:
            continue
        distance = (item["footprintHeight"] - origin[1]) / direction[1]
        if not math.isfinite(distance) or distance < 0:
            continue
        x = origin[0] + direction[0] * distance
        z = origin[2] + direction[2] * distance
        if point_in_polygon(x, z, polygon) and item["name"] not in names:
            names.append(item["name"])
    return names


def place_banners(objects, project, width, height, *, hover_only=False, hovered_node=0, hovered_names=()):
    """Project every finite anchor and place its name plate in the viewport.

    ``project`` returns logical window coordinates or None.  The leaders
    keep the actual object centre as their endpoint, even if that centre
    projects just beyond an edge. A crowded plate may bend or overlap, but
    it must never disappear because the search ran out of room.
    """
    if width <= 8 or height <= 8:
        return []
    candidates = []
    hovered_names = set(hovered_names)
    for item in objects:
        if hover_only and not ((hovered_node and item.get("nodeId") == hovered_node)
                               or item["name"] in hovered_names):
            continue
        point = project(item["position"])
        if point is None:
            continue
        x, y = point
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        candidates.append((float(y), float(x), item))
    candidates.sort(key=lambda value: (value[0], value[1], value[2]["name"]))

    placed = []
    occupied = []
    for y, x, item in candidates:
        label_width = min(width - 8.0, 184.0, max(76.0, 22.0 + len(item["name"]) * 7.0))
        label_height = min(height - 8.0, 44.0 if item.get("deadline") is not None else
                           (30.0 if item.get("progress") is not None else 24.0))
        max_left = width - label_width - 4.0
        max_top = height - label_height - 4.0
        center_left = min(max(x - label_width / 2.0, 4.0), max_left)
        above = y - label_height - 14.0
        above_start = min(max(above, 4.0), max_top)
        below_start = min(max(y + 14.0, 4.0), max_top)
        steps = range(int(height / 38.0) + 3)
        above_tops = list(dict.fromkeys(max(4.0, above_start - step * 38.0) for step in steps))
        below_tops = list(dict.fromkeys(min(max_top, below_start + step * 38.0) for step in steps))
        # A short sideways fan is clearer than a leader climbing through
        # several rows of other plates. Still keep the first unobstructed
        # plate centred above its own object.
        top_groups = (above_tops, below_tops) if above >= 4.0 else (below_tops, above_tops)
        lefts = [center_left]
        for step in range(1, int(width / 36.0) + 3):
            lefts.extend((min(max(center_left - step * 36.0, 4.0), max_left),
                          min(max(center_left + step * 36.0, 4.0), max_left)))
        lefts = list(dict.fromkeys(lefts))
        chosen = None
        fallback = None
        fallback_overlap = float("inf")
        primary_start = above_start if above >= 4.0 else below_start
        primary_tops = list(dict.fromkeys(
            min(max(primary_start + (step * 19.0 if above < 4.0 else -step * 19.0), 4.0), max_top)
            for step in range(min(16, int(height / 19.0) + 2))))
        nearby = []
        for top in primary_tops:
            lift = abs(top - primary_start)
            for left in lefts[:17]:
                bend = abs(left + label_width / 2.0 - x) > 0.5
                if bend and lift < 19.0 and above >= 4.0:
                    continue
                offset = abs(left - center_left)
                nearby.append((lift + offset * 0.3 + (10.0 if bend else 0.0),
                               offset, top, left))
        for _, _, top, left in sorted(nearby):
            rect = (left, top, left + label_width, top + label_height)
            overlap = sum(max(0.0, min(rect[2], other[2] + 5.0) - max(rect[0], other[0] - 5.0))
                          * max(0.0, min(rect[3], other[3] + 5.0) - max(rect[1], other[1] - 5.0))
                          for other in occupied)
            if overlap == 0:
                chosen = rect
                break
        # The full search handles edges and unusually dense viewports. It
        # always leaves a least-overlapping position as a final fallback.
        if chosen is None:
            for tops in top_groups:
                for left in lefts:
                    for top in tops:
                        rect = (left, top, left + label_width, top + label_height)
                        overlap = sum(max(0.0, min(rect[2], other[2] + 5.0) - max(rect[0], other[0] - 5.0))
                                      * max(0.0, min(rect[3], other[3] + 5.0) - max(rect[1], other[1] - 5.0))
                                      for other in occupied)
                        if overlap == 0:
                            chosen = rect
                            break
                        if overlap < fallback_overlap:
                            fallback, fallback_overlap = rect, overlap
                    if chosen is not None:
                        break
                if chosen is not None:
                    break
        if chosen is None:
            chosen = fallback
        occupied.append(chosen)
        placed.append({
            "name": item["name"], "nodeId": item.get("nodeId", 0),
            "anchorX": x, "anchorY": y,
            "labelX": chosen[0], "labelY": chosen[1],
            "labelWidth": label_width, "labelHeight": label_height,
            "progress": item.get("progress"),
            "deadline": item.get("deadline"),
        })
    return placed
