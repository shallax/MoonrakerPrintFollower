"""Pure physical Toolhead readouts; never permission to move the printer."""
from __future__ import annotations

from collections.abc import Mapping
import math


def _finite(value, digits):
    try:
        if isinstance(value, bool):
            return "—"
        value = float(value)
        return f"{value:.{digits}f}" if math.isfinite(value) else "—"
    except (TypeError, ValueError, OverflowError):
        return "—"


def readout(snapshot, connected, state):
    """Use the existing observed lanes, with absent readings left absent."""
    values = {"connected": bool(connected), "positionX": "—", "positionY": "—",
              "positionZ": "—", "offsetText": "—", "actionRows": [],
              "statusText": "Disconnected"}
    if not connected:
        return values
    core, auxiliary = snapshot.core, getattr(snapshot, "auxiliary", {}) or {}
    motion = core.get("motion_report") or {}
    position = motion.get("live_position") or ()
    if isinstance(position, (list, tuple)):
        for index, axis in enumerate("XYZ"):
            if index < len(position):
                values["position" + axis] = _finite(position[index], 2)
    origin = (core.get("gcode_move") or {}).get("homing_origin") or ()
    if isinstance(origin, (list, tuple)) and len(origin) > 2:
        offset = _finite(origin[2], 3)
        if offset != "—":
            values["offsetText"] = ("+" if float(origin[2]) >= 0 else "") + offset
    config = (auxiliary.get("configfile") or {}).get("config") or {}
    names = {str(name).casefold() for name in (getattr(snapshot, "objects", ()) or ())}
    if isinstance(config, Mapping):
        names.update(str(name).casefold() for name in config)
    for key, label in (("quad_gantry_level", "Quad gantry level"),
                       ("z_tilt", "Adjust Z tilt"),
                       ("bed_mesh", "Calibrate bed mesh"),
                       ("screws_tilt_adjust", "Calculate screw adjustments"),
                       ("bed_screws", "Adjust bed screws"),
                       ("delta_calibrate", "Calibrate delta")):
        if key in names:
            values["actionRows"].append({"key": key, "label": label, "allowed": False})
    values["actionRows"].append({"key": "motors", "label": "Disable motors", "allowed": False})
    values["statusText"] = str(state or "Unknown").capitalize()
    return values
