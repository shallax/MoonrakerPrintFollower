"""Pure policy for manual toolhead control: scripts, gates and the jog queue.

The Monitor's toolhead controls move the physical printer. Every G-code
string, every safety decision and every queue rule lives here so the whole
domain is testable without Qt or a printer. The controller (ToolheadController)
owns timers, the drain loop and command sending; it never formats scripts.

Safety model: moves run while the printer is idle or paused. While
printing, a jog request pauses the print first — the queue drains only once
a fresh ``paused`` state is observed — so a move is never force-executed
mid-print.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

# Movement distance presets offered by the Monitor UI, in millimetres.
JOG_DISTANCES = (0.1, 0.5, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 125.0)
JOG_DISTANCE_DEFAULT = 25.0
# Free-text bounds for movement distances.
JOG_DISTANCE_MIN = 0.01
JOG_DISTANCE_MAX = 300.0
# The centre-toolhead move parks the nozzle this far above the plate.
CENTER_Z_MM = 50.0
# Feedrates in mm/min: XY jog 50 mm/s, Z 10 mm/s (Klipper-safe defaults),
# extrusion default 5 mm/s.
XY_FEEDRATE_MM_PER_MIN = 3000
Z_FEEDRATE_MM_PER_MIN = 600
E_FEEDRATE_MM_PER_MIN = 300
# Extrusion controls: distance presets, speed presets (1/2/5/25 mm/s) and
# the free-text bounds.
EXTRUDE_DISTANCES = (5.0, 10.0, 15.0, 25.0, 75.0, 100.0)
EXTRUDE_DISTANCE_DEFAULT = 5.0
EXTRUDE_DISTANCE_MIN = 0.1
EXTRUDE_DISTANCE_MAX = 100.0
EXTRUDE_SPEEDS_MM_PER_MIN = (60, 120, 300, 1500)
EXTRUDE_SPEED_DEFAULT = E_FEEDRATE_MM_PER_MIN
EXTRUDE_SPEED_MIN = 30
EXTRUDE_SPEED_MAX = 1800
# Safety bounds: queue depth and how long a requested pause may take.
MAX_PENDING_OPS = 16
PAUSE_WAIT_TIMEOUT_S = 10.0

# Status strings shared with the UI; tests pin the prose to these tokens.
STATUS_PAUSE_WAITING = "Waiting for the printer to pause…"
STATUS_PAUSE_TIMED_OUT = "The printer did not pause — queued moves were cancelled."
STATUS_RESUMED_DROP = "The print resumed — queued moves were cancelled."
STATUS_QUEUE_FULL = "Too many queued moves — wait for the printer to catch up."
STATUS_PAUSED_MOVING = "Printer paused — moving now."
STATUS_NOT_READY = "Printer is not ready for toolhead moves."

_AXES = ("x", "y", "z")


def axis_ok(axis: str) -> bool:
    """True for a single lowercase jog/home axis."""
    return str(axis).strip().lower() in _AXES


def clamp_relative_move(signed_distance: float, current: float,
                        minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    """Limit a relative move to the axis range.

    A move that would cross the axis minimum is forbidden entirely (zero):
    the toolhead must never be allowed into negative territory. A move that
    would cross the maximum is clamped so it lands exactly on the boundary.
    """
    signed_distance = float(signed_distance)
    current = float(current)
    if signed_distance == 0.0:
        return 0.0
    target = current + signed_distance
    if minimum is not None and target < minimum:
        return 0.0
    if maximum is not None and target > maximum:
        result = maximum - current
        return result if result > 0 else 0.0  # already out: no move
    if minimum is not None and maximum is not None and minimum > maximum:
        return 0.0
    return signed_distance


def jog_distance_ok(distance) -> bool:
    try:
        distance = float(distance)
    except (TypeError, ValueError):
        return False
    return JOG_DISTANCE_MIN <= abs(distance) <= JOG_DISTANCE_MAX


def extrude_distance_ok(distance) -> bool:
    try:
        distance = float(distance)
    except (TypeError, ValueError):
        return False
    return EXTRUDE_DISTANCE_MIN <= abs(distance) <= EXTRUDE_DISTANCE_MAX


def extrude_speed_ok(speed) -> bool:
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return False
    return EXTRUDE_SPEED_MIN <= speed <= EXTRUDE_SPEED_MAX


def _feedrate(axis: str) -> int:
    return Z_FEEDRATE_MM_PER_MIN if axis == "z" else XY_FEEDRATE_MM_PER_MIN


def jog_script(axis: str, distance: float, *, absolute_coordinates: bool = True) -> str:
    """Relative G1 move sandwich; G90 restored only if the printer was absolute.

    The sandwich leaves a printer already in relative mode alone, and the
    relative inner move means the script is correct regardless of where the
    toolhead currently is.
    """
    axis = str(axis).strip().lower()
    distance = float(distance)
    if not axis_ok(axis):
        raise ValueError(f"bad jog axis {axis!r}")
    if not jog_distance_ok(distance):
        raise ValueError(f"jog distance {distance:g} mm is out of range")
    parts = ["G91", f"G1 {axis.upper()}{distance:g} F{_feedrate(axis)}"]
    if absolute_coordinates:
        parts.append("G90")
    return "\n".join(parts)


def home_script(axis: str = "") -> str:
    """G28, or G28 with a single axis for per-axis homing."""
    axis = str(axis).strip().lower()
    if axis and not axis_ok(axis):
        raise ValueError(f"bad home axis {axis!r}")
    return f"G28 {axis.upper()}".strip() if axis else "G28"


def motors_off_script() -> str:
    return "M18"


def _absolute_sandwich(parts, absolute_coordinates: bool) -> str:
    """Wrap an absolute move so a relative-mode printer ends relative again."""
    lines = []
    if not absolute_coordinates:
        lines.append("G90")
    lines.extend(parts)
    if not absolute_coordinates:
        lines.append("G91")
    return "\n".join(lines)


def center_script(x: float, y: float, *, z: float = CENTER_Z_MM,
                  absolute_coordinates: bool = True) -> str:
    """Absolute move to the plate centre at the park height."""
    return _absolute_sandwich(
        [f"G1 X{float(x):g} Y{float(y):g} Z{float(z):g} F{XY_FEEDRATE_MM_PER_MIN}"],
        absolute_coordinates)


def z0_script(*, absolute_coordinates: bool = True) -> str:
    """Absolute Z move down to 0 (the bed level after homing)."""
    return _absolute_sandwich([f"G1 Z0 F{Z_FEEDRATE_MM_PER_MIN}"], absolute_coordinates)


def extrude_script(distance: float, *, speed_mm_per_min: float = E_FEEDRATE_MM_PER_MIN,
                   absolute_coordinates: bool = True) -> str:
    """Relative extrusion move; same sandwich semantics as jog_script."""
    distance = float(distance)
    speed_mm_per_min = float(speed_mm_per_min)
    if not extrude_distance_ok(distance):
        raise ValueError(f"extrusion distance {distance:g} mm is out of range")
    if not extrude_speed_ok(speed_mm_per_min):
        raise ValueError(f"extrusion speed {speed_mm_per_min:g} mm/min is out of range")
    parts = ["G91", f"G1 E{distance:g} F{speed_mm_per_min:g}"]
    if absolute_coordinates:
        parts.append("G90")
    return "\n".join(parts)


def position_mode_text(absolute) -> str:
    """The coordinate mode readout, e.g. 'Absolute moves'."""
    return "Absolute" if bool(absolute) else "Relative"


def jog_gate(state: str) -> str:
    """Classify print state for toolhead moves: disabled | pause-first | allowed."""
    state = str(state or "").strip().lower()
    if state == "printing":
        return "pause-first"
    if state in {"standby", "paused", "complete", "cancelled"}:
        return "allowed"
    return "disabled"


@dataclass(frozen=True)
class JogOp:
    """One queued toolhead operation, pre-rendered for the controller."""
    kind: str
    axis: str = ""
    distance: float = 0.0
    speed: float = 0.0
    label: str = ""
    script: str = ""


def make_jog_op(axis: str, signed_distance: float, absolute_coordinates: bool) -> JogOp:
    axis = str(axis).strip().lower()
    signed_distance = float(signed_distance)
    if not axis_ok(axis):
        raise ValueError(f"bad jog axis {axis!r}")
    return JogOp(kind="jog", axis=axis, distance=signed_distance,
        label=f"Jog {axis.upper()}", script=jog_script(axis, signed_distance, absolute_coordinates=absolute_coordinates))


def make_home_op(axis: str = "") -> JogOp:
    axis = str(axis).strip().lower()
    if axis and not axis_ok(axis):
        raise ValueError(f"bad home axis {axis!r}")
    return JogOp(kind="home", axis=axis, label="Home all" if not axis else f"Home {axis.upper()}",
        script=home_script(axis))


def make_motors_off_op() -> JogOp:
    return JogOp(kind="motors-off", label="Motors off", script=motors_off_script())


def make_center_op(x: float, y: float, absolute_coordinates: bool) -> JogOp:
    return JogOp(kind="center", distance=CENTER_Z_MM, label="Centre toolhead",
        script=center_script(x, y, absolute_coordinates=absolute_coordinates))


def make_z0_op(absolute_coordinates: bool) -> JogOp:
    return JogOp(kind="z0", label="Z to 0", script=z0_script(absolute_coordinates=absolute_coordinates))


def make_extrude_op(distance: float, speed_mm_per_min: float, absolute_coordinates: bool) -> JogOp:
    distance = float(distance)
    speed_mm_per_min = float(speed_mm_per_min)
    return JogOp(kind="extrude", axis="e", distance=distance, speed=speed_mm_per_min,
        label="Extrude" if distance >= 0 else "Retract",
        script=extrude_script(distance, speed_mm_per_min=speed_mm_per_min,
                              absolute_coordinates=absolute_coordinates))


def _regenerate(op: JogOp, distance: float, absolute_coordinates: bool) -> JogOp:
    if op.kind == "jog":
        return make_jog_op(op.axis, distance, absolute_coordinates)
    return make_extrude_op(distance, op.speed, absolute_coordinates)


def push_op(pending: Sequence[JogOp], op: JogOp,
            *, absolute_coordinates: bool = True) -> Tuple[Tuple[JogOp, ...], Optional[str]]:
    """Append an operation with adjacent same-axis coalescing.

    Consecutive relative moves on one axis are additive, so five X+1 taps
    merge into one ``G1 X5``; merging across a home or another axis would
    not be equivalent, so only the tail op may merge. Extrusion moves merge
    only at the same feedrate. The depth cap rejects the newest tap
    (already-queued intent is kept) and reports why.
    """
    pending = tuple(pending)
    if not pending:
        return (op,), None
    tail = pending[-1]
    if (tail.kind == op.kind in {"jog", "extrude"} and tail.axis == op.axis
            and (tail.kind != "extrude" or tail.speed == op.speed)):
        merged_distance = tail.distance + op.distance
        if merged_distance == 0.0:
            # Equal-and-opposite moves cancel: the user's tap undoes the
            # queued tail (regenerating a zero-length move would raise).
            return pending[:-1], None
        merged = _regenerate(tail, merged_distance, absolute_coordinates)
        return pending[:-1] + (merged,), None
    if len(pending) >= MAX_PENDING_OPS:
        return pending, STATUS_QUEUE_FULL
    return pending + (op,), None
