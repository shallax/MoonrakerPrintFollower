"""Fail-closed physical travel checks over one fresh Klipper query.

G-code position is not physical position: G92/SET_GCODE_OFFSET change the
base, and bed_mesh transforms Z. Use Klipper's planned endpoint (not a
mid-flight live_position) and bound compensation over the whole mesh. The
global bound deliberately avoids guessing unreported mesh offsets/ZFADE.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math


class UnsafeMotion(ValueError):
    pass


def telemetry_object(value):
    if not isinstance(value, Mapping):
        raise UnsafeMotion("Malformed printer telemetry")
    return value


def finite(value):
    if isinstance(value, bool):
        raise UnsafeMotion("Invalid motion value")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise UnsafeMotion("Missing motion value") from error
    if not math.isfinite(result):
        raise UnsafeMotion("Non-finite motion value")
    return result


def xyz(value):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise UnsafeMotion("Position or travel limits are unavailable")
    return tuple(finite(v) for v in value[:3])


def command_number(value):
    # Python's shortest round-tripping decimal preserves the exact float
    # we checked, without the six-significant-digit rounding of ':g'.
    return repr(finite(value)).removesuffix(".0")


@dataclass(frozen=True)
class Envelope:
    position: tuple
    gcode: tuple
    base: tuple
    origin: tuple
    minimum: tuple
    maximum: tuple
    compensation_min: float
    compensation_max: float
    bed: float

    def check(self, target):
        target = xyz(target)
        start = tuple(a + b for a, b in zip(self.gcode, self.base, strict=False))
        end = tuple(a + b for a, b in zip(target, self.base, strict=False))
        # XY and pre-transform Z are linear between endpoints. Every mesh
        # interpolation/fade value is inside the global compensation bound.
        for point in (start, end):
            for i in (0, 1):
                if not self.minimum[i] <= point[i] <= self.maximum[i]:
                    raise UnsafeMotion("Target is outside the printer's travel limits")
            if point[2] < 0:
                raise UnsafeMotion("Move would cross the calibrated bed plane")
            lower = point[2] + self.compensation_min
            upper = point[2] + self.compensation_max
            if lower < max(self.minimum[2], self.bed) or upper > self.maximum[2]:
                raise UnsafeMotion("Move lacks safe bed clearance or exceeds Z travel")
        return target


def envelope(status):
    if not isinstance(status, Mapping):
        raise UnsafeMotion("Fresh printer state is unavailable")
    toolhead = telemetry_object(status.get("toolhead"))
    move = telemetry_object(status.get("gcode_move"))
    axis_map = move.get("axis_map")
    if axis_map is not None and (not isinstance(axis_map, Mapping) or
                                any(axis_map.get(a) != i for i, a in enumerate("XYZ"))):
        raise UnsafeMotion("An unsupported axis mapping prevents safe manual movement")
    if not set(str(toolhead.get("homed_axes", "")).lower()) >= set("xyz"):
        raise UnsafeMotion("Home XYZ before manual movement")
    config = telemetry_object(status.get("configfile")).get("config")
    if not isinstance(config, Mapping):
        raise UnsafeMotion("Printer configuration is unavailable")
    config = {str(k).casefold(): v for k, v in config.items()}
    protected = {"g0", "g1", "g90", "g91", "save_gcode_state", "restore_gcode_state", "set_gcode_offset"}
    if any(key.startswith("gcode_macro ") and key.split(" ", 1)[1] in protected for key in config):
        raise UnsafeMotion("A movement command override prevents safe manual movement")
    kinematics = str(telemetry_object(config.get("printer")).get("kinematics", "")).casefold()
    if kinematics not in {"cartesian", "corexy", "corexz", "hybrid_corexy", "hybrid_corexz",
                           "limited_cartesian", "limited_corexy", "limited_corexz"}:
        raise UnsafeMotion("Manual travel guard does not support this kinematics")
    # These transforms do not publish enough runtime information to prove
    # a physical path. Firmware homing/calibration remains available.
    if any(key in config for key in ("bed_tilt", "skew_correction", "z_thermal_adjust",
                                     "axis_twist_compensation", "dual_carriage")):
        raise UnsafeMotion("An unreported coordinate transform prevents safe manual movement")
    position = xyz(toolhead.get("position"))
    planned = xyz(move.get("position"))
    gcode, origin = xyz(move.get("gcode_position")), xyz(move.get("homing_origin"))
    base = tuple(a - b for a, b in zip(planned, gcode, strict=False))
    minimum, maximum = xyz(toolhead.get("axis_minimum")), xyz(toolhead.get("axis_maximum"))
    if any(lo >= hi for lo, hi in zip(minimum, maximum, strict=False)):
        raise UnsafeMotion("Invalid printer travel limits")
    lo = hi = bed = 0.0
    if "bed_mesh" in config:
        mesh = status.get("bed_mesh")
        if not isinstance(mesh, Mapping) or "mesh_matrix" not in mesh:
            raise UnsafeMotion("Active bed compensation is unavailable")
        rows = mesh["mesh_matrix"]
        if not isinstance(rows, (list, tuple)) or not rows:
            raise UnsafeMotion("Invalid bed mesh")
        if not (len(rows) == 1 and isinstance(rows[0], (list, tuple)) and not rows[0]):
            if len(rows) < 2 or not all(isinstance(r, (list, tuple)) and len(r) == len(rows[0])
                                       and len(r) >= 2 for r in rows):
                raise UnsafeMotion("Invalid bed mesh")
            values = [finite(v) for row in rows for v in row]
            # Klipper rounds the published mesh to six decimals. Widen the
            # bound, rather than admit a positive tolerance below the bed.
            lo, hi = min(0.0, min(values) - 0.000001), max(0.0, max(values) + 0.000001)
            bed = hi
            mesh_config = telemetry_object(config['bed_mesh'])
            fade_distance = finite(mesh_config.get('fade_end', 0)) - finite(mesh_config.get('fade_start', 1))
            if fade_distance > 0:
                # Explicit fade targets lie within the mesh range or are
                # zero, but Klipper's default average is rounded to .01 mm.
                # Include that rounding outside the published mesh extrema.
                target_min = lo - 0.005
                target_max = hi + 0.005
                lo, hi = target_min, target_max
    # Reject inconsistent telemetry instead of mistaking another transform
    # for a bed mesh. A query is one eventtime, not mixed polling lanes.
    if any(abs(position[i] - planned[i]) > 0.00001 for i in (0, 1)) \
            or not lo - 0.00001 <= position[2] - planned[2] <= hi + 0.00001:
        raise UnsafeMotion("Physical and G-code coordinates do not agree")
    if any(not minimum[i] <= position[i] <= maximum[i] for i in (0, 1)) \
            or not max(minimum[2], bed) <= position[2] <= maximum[2]:
        raise UnsafeMotion("Reported physical position is outside safe travel")
    return Envelope(position, gcode, base, origin, minimum, maximum, lo, hi, bed)


def target_fields(x, y, z):
    """Blank axes stay unchanged; accept numbers, never G-code fragments."""
    result = tuple(None if str(v).strip() == "" else finite(v) for v in (x, y, z))
    if all(v is None for v in result):
        raise UnsafeMotion("Enter at least one target coordinate")
    return result


def prepare(op, status):
    """Validate then render exactly the intent that will be dispatched."""
    if op.kind == "offset":
        # Z-offset is an operator calibration adjustment, not a position
        # target. No inferred nozzle geometry or bed floor governs it.
        delta = finite(op.distance)
        return "SET_GCODE_OFFSET " + ("Z=0" if op.reset else "Z_ADJUST=" +
                                      ("+" if delta >= 0 else "") + command_number(delta)) + " MOVE=1"
    if op.kind not in {"jog", "move-to", "center", "z0"}:
        return op.script
    bounds = envelope(status)
    target = list(bounds.gcode)
    if op.kind == "jog":
        target["xyz".index(op.axis)] += finite(op.distance)
    elif op.kind == "move-to":
        target = [old if new is None else finite(new) for old, new in zip(target, op.targets, strict=False)]
    elif op.kind == "z0":
        target[2] = 0.0
    elif op.kind == "center":
        target = [(bounds.minimum[i] + bounds.maximum[i]) / 2 - bounds.base[i] for i in (0, 1)] \
            + [50.0 - bounds.base[2]]
    bounds.check(target)
    if op.kind == "jog":
        line = f"G91\nG1 {op.axis.upper()}{command_number(op.distance)} F{600 if op.axis == 'z' else 3000}"
    else:
        axes = range(3) if op.kind == "center" else (2,) if op.kind == "z0" else \
            [i for i, v in enumerate(op.targets) if v is not None]
        coordinates = " ".join(f"{'XYZ'[i]}{command_number(target[i])}" for i in axes)
        line = f"G90\nG1 {coordinates} F600"
    # Save the ACTUAL mode/feedrate inside the script, not a polling guess.
    return "SAVE_GCODE_STATE NAME=MPF_MANUAL_MOVE\n" + line \
        + "\nRESTORE_GCODE_STATE NAME=MPF_MANUAL_MOVE MOVE=0"
