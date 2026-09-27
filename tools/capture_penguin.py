"""Deterministic three-material penguin toolpaths for the README capture.

This is illustration G-code, not a printer job: no heating, homing or macros.
The capture indexes it with the production parser and renders its real motions.
Coordinates are sampled from an original stylised silhouette, with exclusive
material regions so no overlapping extrusion is needed to draw the face.
"""
from __future__ import annotations

import math


def _ellipse(x, y, cx, cy, rx, ry):
    return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1


def _material(x, y):
    # Feet and beak sit in front of the black silhouette.
    if _ellipse(x, y, 101, 49, 26, 11) or _ellipse(x, y, 149, 49, 26, 11):
        return 2
    if _ellipse(x, y, 125, 149, 27, 12):
        # A shallow curved smile, cut into the yellow material.
        if 104 < x < 146 and abs(y - (141 + 0.012 * (x - 125) ** 2)) < 0.8:
            return 0
        return 2
    body = _ellipse(x, y, 125, 105, 48, 63)
    head = _ellipse(x, y, 125, 164, 37, 40)
    wing = (_ellipse(x, y, 80, 100, 15, 35)
            or _ellipse(x, y, 170, 100, 15, 35))
    if not (body or head or wing):
        return None
    if _ellipse(x, y, 125, 98, 34, 43):
        return 1
    for eye in (113, 137):
        if _ellipse(x, y, eye, 165, 8, 15):
            return 0 if _ellipse(x, y, eye + 0.8, 161, 5, 7) else 1
    return 0


def make_gcode(bed_size=200.0):
    """Three identical layers of non-overlapping horizontal skin strokes."""
    lines = [";FLAVOR:Marlin", ";LAYER_COUNT:3", ";Generated capture illustration",
             "G90", "M82", "G92 E0"]
    extrusion = [0.0, 0.0, 0.0]
    for layer in range(3):
        lines.extend([f";LAYER:{layer}", f"G0 Z{(layer + 1) * 0.2:.3f} F6000",
                      ";TYPE:SKIN"])
        for tool in range(3):
            lines.extend([f"T{tool}", f"G92 E{extrusion[tool]:.5f}",
                          ";TYPE:" + ("WALL-OUTER", "WALL-INNER", "SKIN")[tool]])
            for row in range(240):
                y = 38 + row * 0.7
                start = None
                for col in range(602):
                    x = 65 + col * 0.2
                    inside = col < 601 and _material(x, y) == tool
                    if inside and start is None:
                        start = x
                    elif not inside and start is not None:
                        end = x - 0.2
                        if end > start:
                            first, last = (start, end) if row % 2 == 0 else (end, start)
                            # The README model defaults to 200 mm; the native
                            # harness uses 250 mm. Centre the same geometry
                            # on its actual bed without changing extrusion.
                            first = (first - 125) * 0.8 + bed_size / 2
                            last = (last - 125) * 0.8 + bed_size / 2
                            bed_y = (y - 121) * 0.8 + bed_size / 2
                            lines.append(f"G0 X{first:.3f} Y{bed_y:.3f} F6000")
                            # 0.56 mm width, 0.2 mm height, 1.75 mm filament.
                            extrusion[tool] += abs(last - first) * 0.56 * 0.2 / (math.pi * 0.875 ** 2)
                            lines.append(f"G1 X{last:.3f} Y{bed_y:.3f} E{extrusion[tool]:.5f} F1800")
                        start = None
        lines.append(f";TIME_ELAPSED:{(layer + 1) * 300}")
    return "\n".join(lines) + "\n"
