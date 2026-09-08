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

# Distance presets offered by the Monitor UI, in millimetres.
JOG_DISTANCES = (0.1, 1.0, 10.0, 100.0)
# Feedrates in mm/min: XY jog 50 mm/s, Z 10 mm/s (Klipper-safe defaults),
# extrusion 5 mm/s.
XY_FEEDRATE_MM_PER_MIN = 3000
Z_FEEDRATE_MM_PER_MIN = 600
E_FEEDRATE_MM_PER_MIN = 300
# Fixed extrude/retract step shown by the UI buttons.
EXTRUDE_STEP_MM = 5.0
# Safety bounds: queue depth and how long a requested pause may take.
MAX_PENDING_OPS = 16
PAUSE_WAIT_TIMEOUT_S = 10.0
MAX_EXTRUDE_MM = 10.0

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


def extrude_script(distance: float, *, absolute_coordinates: bool = True) -> str:
    """Relative extrusion move; same sandwich semantics as jog_script."""
    distance = float(distance)
    if abs(distance) > MAX_EXTRUDE_MM:
        raise ValueError(f"extrusion amount {distance:g} mm is not a safe single move")
    parts = ["G91", f"G1 E{distance:g} F{E_FEEDRATE_MM_PER_MIN}"]
    if absolute_coordinates:
        parts.append("G90")
    return "\n".join(parts)


def homed_text(value) -> str:
    """'xyz' -> 'X Y Z'; anything falsy -> '—'."""
    text = str(value or "").strip().lower()
    return " ".join(axis.upper() for axis in text if axis in _AXES) or "—"


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


def make_extrude_op(distance: float, absolute_coordinates: bool) -> JogOp:
    distance = float(distance)
    return JogOp(kind="extrude", axis="e", distance=distance,
        label="Extrude" if distance >= 0 else "Retract",
        script=extrude_script(distance, absolute_coordinates=absolute_coordinates))


def _regenerate(op: JogOp, distance: float, absolute_coordinates: bool) -> JogOp:
    if op.kind == "jog":
        return make_jog_op(op.axis, distance, absolute_coordinates)
    return make_extrude_op(distance, absolute_coordinates)


def push_op(pending: Sequence[JogOp], op: JogOp,
            *, absolute_coordinates: bool = True) -> Tuple[Tuple[JogOp, ...], Optional[str]]:
    """Append an operation with adjacent same-axis coalescing.

    Consecutive relative moves on one axis are additive, so five X+1 taps
    merge into one ``G1 X5``; merging across a home or another axis would
    not be equivalent, so only the tail op may merge. The depth cap rejects
    the newest tap (already-queued intent is kept) and reports why.
    """
    pending = tuple(pending)
    if not pending:
        return (op,), None
    tail = pending[-1]
    if (tail.kind == op.kind in {"jog", "extrude"} and tail.axis == op.axis):
        merged = _regenerate(tail, tail.distance + op.distance, absolute_coordinates)
        return pending[:-1] + (merged,), None
    if len(pending) >= MAX_PENDING_OPS:
        return pending, STATUS_QUEUE_FULL
    return pending + (op,), None
