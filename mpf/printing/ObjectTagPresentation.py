# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Separate scene reading from portable object-tag projection and progress fallback.

"""Object-name matching and fallback tags over plain scene/print values."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SceneTagObject:
    node_id: int
    name: str
    center: tuple
    top: float
    bottom: float
    height: float


def definition_center(row):
    center = row.get("center")
    if center is not None:
        return center[:2]
    polygon = row.get("polygon") or ()
    if not polygon:
        return None
    return ((min(point[0] for point in polygon) + max(point[0] for point in polygon)) / 2.0,
            (min(point[1] for point in polygon) + max(point[1] for point in polygon)) / 2.0)



def object_tag_rows(objects, definitions, metrics, *, width, depth, centred,
                    current_height, current_object, selected_height=None):
    centers = [(row, center) for row in definitions if (center := definition_center(row)) is not None]
    rows = []
    for obj in objects:
        try:
            cx, _cy, cz = obj.center
            px = cx if centred else cx + width / 2.0
            py = -cz if centred else depth / 2.0 - cz
            nearest = min(centers, key=lambda pair: math.hypot(px - pair[1][0], py - pair[1][1]), default=None)
            matched = nearest is not None and math.hypot(px - nearest[1][0], py - nearest[1][1]) < 12.0
            if matched:
                name = nearest[0]["name"]
                status_name = nearest[0].get("statusName", name)
            else:
                name = obj.name
                status_name = name
            if not name:
                continue
            metric = metrics.get(name) or {}
            progress = metric.get("progress")
            if (progress is None and current_height is not None and obj.top > obj.bottom
                    and status_name == current_object):
                progress = min(1.0, max(0.0, (float(current_height) - obj.bottom) / obj.height))
            if matched and nearest[0].get("excluded"):
                progress = None
            rows.append({"name": name, "nodeId": obj.node_id, "position": (cx, obj.top, cz),
                         "progress": progress, "deadline": metric.get("deadline") if progress is not None else None})
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    if rows:
        return rows, True
    if selected_height is not None and math.isfinite(selected_height):
        current_height = selected_height
    z = max(0.0, float(current_height or 0.0))
    for row, center in centers:
        x, y = center[:2]
        scene_x = x if centred else x - width / 2.0
        scene_z = -y if centred else depth / 2.0 - y
        metric = metrics.get(row["name"]) or {}
        progress = None if row.get("excluded") else metric.get("progress")
        top = metric.get("top")
        anchor_z = min(z, float(top)) if top is not None else z
        polygon = row.get("polygon") or ()
        bounds = row.get("bounds")
        if len(polygon) < 3 and bounds is not None:
            xmin, ymin, xmax, ymax = bounds
            polygon = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))
        footprint = [(px if centred else px - width / 2.0,
                      -py if centred else depth / 2.0 - py) for px, py in polygon]
        rows.append({"name": row["name"], "nodeId": 0,
                     "position": (scene_x, anchor_z, scene_z), "progress": progress,
                     "deadline": metric.get("deadline") if progress is not None else None,
                     "footprint": footprint, "footprintHeight": anchor_z})
    return rows, False
