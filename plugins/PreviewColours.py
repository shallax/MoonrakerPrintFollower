"""Print-wide Preview colour projections, matching Cura's gradient equations.

The gradients match Cura 5.13's SimulationView/layers3d.shader. Static colours
come from the host's Preview theme and active extruder material model.
"""
from __future__ import annotations

import math

from .TravelStates import is_travel

MODE_KEYS = {2: "speed", 3: "height", 4: "width", 5: "flow"}
CLASS_THEME_KEYS = {
    "WALL-OUTER": "layerview_inset_0", "WALL-INNER": "layerview_inset_x",
    "SKIN": "layerview_skin", "FILL": "layerview_infill",
    "SUPPORT": "layerview_support", "SUPPORT-INTERFACE": "layerview_support_interface",
    "SKIRT": "layerview_skirt", "PRIME-TOWER": "layerview_prime_tower",
    "TRAVEL": "layerview_move_combing",
    "TRAVEL_RETRACTING": "layerview_move_while_retracting",
    "TRAVEL_RETRACTED": "layerview_move_retraction",
    "TRAVEL_PRIMING": "layerview_move_while_unretracting",
}
DEFAULT_CLASSES = {
    "WALL-OUTER": "#d32f2f", "WALL-INNER": "#388e3c", "SKIN": "#e65100",
    "FILL": "#1976d2", "SUPPORT": "#00838f", "SKIRT": "#00897b", "TRAVEL": "#b085e8",
}
DEFAULT_CLASSES.update(TRAVEL_RETRACTING="#7fffff", TRAVEL_RETRACTED="#807fff", TRAVEL_PRIMING="#ff7fff")


def gradient(mode, value, limits):
    """RGBA in [0,1]; a constant range uses Cura's middle colour."""
    lo, hi = limits
    equal = abs(hi - lo) < .0001
    v = .5 if equal else min(1.0, max(0.0, (value - lo) / (hi - lo)))
    if mode == 3:
        rgb = (min(1, max(0, 4*v - 2)), v if v > .75 else min(1.5*v, .75), .75 - abs(.25 - v))
    elif mode == 5:
        t = 0 if equal else 2*v - 1
        rgb = tuple(min(1, max(0, 1.5 - abs(2*t + shift))) for shift in (-1, 0, 1))
    else:
        rgb = (v, .5 if v > .375 else 1 - abs(1 - 4*v), max(1 - 4*v, 0))
    return (*rgb, 1.0)


def valid_ranges(value):
    return (isinstance(value, dict) and set(value) <= set(MODE_KEYS.values())
            and all(isinstance(bounds, (list, tuple)) and len(bounds) == 2
                    and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1e9 for v in bounds)
                    and bounds[0] <= bounds[1] for bounds in value.values()))


def motion_value(payload, motion, mode):
    speeds, widths = payload.get("speeds") or (), payload.get("widths") or ()
    speed = speeds[motion] if 0 <= motion < len(speeds) else 0.0
    width = widths[motion] if 0 <= motion < len(widths) and widths[motion] > 0 else .4
    height = payload.get("layerHeight", .2)
    return {2: speed, 3: height, 4: width, 5: width * height * speed}.get(mode, 0.0)


def rgba_hex(rgba):
    r, g, b, a = (round(min(1, max(0, v)) * 255) for v in rgba)
    return f"#{a:02x}{r:02x}{g:02x}{b:02x}"


def motion_colour(payload, name, motion, scheme):
    mode = int(scheme.get("mode", 1))
    classes = scheme.get("classes") or DEFAULT_CLASSES
    if is_travel(name) or mode == 1:
        return classes.get(name, "#888888")
    if mode == 0:
        tools = payload.get("tools") or ()
        tool = int(tools[motion]) if 0 <= motion < len(tools) else 0
        materials = scheme.get("materials") or ["#888888"]
        return materials[tool] if tool < len(materials) else materials[0]
    bounds = (payload.get("colourRanges") or {}).get(MODE_KEYS.get(mode), (0, 0))
    return rgba_hex(gradient(mode, motion_value(payload, motion, mode), bounds))
