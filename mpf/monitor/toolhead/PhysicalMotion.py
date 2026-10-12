"""Fail-closed physical travel checks over one fresh Klipper query.

Use the planned G-code endpoint, an inclusive zero floor and reported axis
limits. Coordinate bases translate upper limits; firmware alone owns bed-mesh
compensation. Never infer a clearance floor from compensated physical Z.
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

    def check(self, target, axes=range(3)):
        target = xyz(target)
        # The operator's calibrated G-code zero is an inclusive boundary on
        # every axis. Mesh compensation and coordinate offsets do not define
        # a second, inferred minimum clearance above that zero.
        if any(target[i] < 0 for i in axes):
            raise UnsafeMotion("Target would go below zero")
        start = tuple(a + b for a, b in zip(self.gcode, self.base, strict=False))
        end = tuple(a + b for a, b in zip(target, self.base, strict=False))
        for point in (start, end):
            if any(point[i] > self.maximum[i] for i in range(3)):
                raise UnsafeMotion("Target is outside the printer's travel limits")
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
    # Toolhead.position Z is firmware-compensated. It is neither the user's
    # zero nor a source for a second mesh/clearance calculation in this client.
    if any(abs(position[i] - planned[i]) > 0.00001 for i in (0, 1)):
        raise UnsafeMotion("Physical and G-code coordinates do not agree")
    if any(planned[i] > maximum[i] for i in range(3)):
        raise UnsafeMotion("Reported planned position exceeds travel limits")
    return Envelope(position, gcode, base, origin, minimum, maximum)


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
    axes = ("xyz".index(op.axis),) if op.kind == "jog" else (2,) if op.kind == "z0" else \
        [i for i, v in enumerate(op.targets) if v is not None] if op.kind == "move-to" else range(3)
    bounds.check(target, axes)
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
