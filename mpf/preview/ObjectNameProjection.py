"""Screen-space placement for Preview object-name banners."""
from __future__ import annotations

import math


def place_banners(objects, project, width, height):
    """Project world anchors and keep name plates from covering each other.

    ``project`` returns logical window coordinates or None.  The leaders
    stay vertical near each object; only their upper part can fan out.
    """
    candidates = []
    for item in objects:
        point = project(item["position"])
        if point is None:
            continue
        x, y = point
        if not (math.isfinite(x) and math.isfinite(y)) or not (-12 <= x <= width + 12 and -12 <= y <= height + 12):
            continue
        candidates.append((float(y), float(x), item))
    candidates.sort(key=lambda value: (value[0], value[1], value[2]["name"]))

    placed = []
    occupied = []
    for y, x, item in candidates:
        label_width = min(184.0, max(76.0, 22.0 + len(item["name"]) * 7.0))
        label_height = 44.0 if item.get("deadline") is not None else (30.0 if item.get("progress") is not None else 24.0)
        chosen = None
        for lift in range(0, 7):
            top = y - label_height - 14.0 - lift * 38.0
            if top < 4.0:
                continue
            for shift in (0.0, -36.0, 36.0, -72.0, 72.0, -108.0, 108.0):
                left = min(max(x - label_width / 2.0 + shift, 4.0), max(4.0, width - label_width - 4.0))
                rect = (left, top, left + label_width, top + label_height)
                if all(rect[2] + 5 < other[0] or other[2] + 5 < rect[0]
                       or rect[3] + 5 < other[1] or other[3] + 5 < rect[1]
                       for other in occupied):
                    chosen = rect
                    break
            if chosen is not None:
                break
        if chosen is None:
            continue
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
