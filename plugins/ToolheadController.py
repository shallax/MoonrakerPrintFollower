"""Monitor toolhead-control owner: the jog queue and pause-first sequencing.

All G-code text, safety classification and queue coalescing rules are pure
policy in ToolheadPolicy; this object only owns mutable state (the pending
queue, the pause wait and its deadline) and sends commands through the
shared Monitor command lane.

Safety: moves run while the printer is idle or paused. While printing,
the Monitor disables the jog controls — the user must pause explicitly
first. The pause-first queue below remains as the safety net for a tap
that lands in the transition window (e.g. right after a resume, before
the fresh ``printing`` state reaches the UI): such a tap sends the
tracked Pause command (the same request as the Monitor pause button),
the queue drains only once a fresh ``paused`` state is observed, and is
dropped if the pause is not confirmed in time or the print resumes
mid-drain.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .ToolheadPolicy import (
    EXTRUDE_DISTANCE_DEFAULT,
    EXTRUDE_SPEED_DEFAULT,
    JOG_DISTANCE_DEFAULT,
    PAUSE_WAIT_TIMEOUT_S,
    clamp_relative_move,
    make_center_op,
    make_z0_op,
    STATUS_NOT_READY,
    STATUS_PAUSE_TIMED_OUT,
    STATUS_PAUSE_WAITING,
    STATUS_PAUSED_MOVING,
    STATUS_RESUMED_DROP,
    axis_ok,
    extrude_distance_ok,
    extrude_speed_ok,
    jog_distance_ok,
    jog_gate,
    make_extrude_op,
    make_home_op,
    make_jog_op,
    make_motors_off_op,
    position_mode_text,
    push_op,
)


class ToolheadController(QObject):
    changed = pyqtSignal()

    def __init__(self, data, commands, parent=None):
        super().__init__(parent)
        self._data = data
        self._commands = commands
        self._pending = ()
        self._jog_distance = JOG_DISTANCE_DEFAULT
        self._extrude_distance = EXTRUDE_DISTANCE_DEFAULT
        self._extrude_speed = EXTRUDE_SPEED_DEFAULT
        self._absolute_coordinates = True
        self._pause_waiting = False
        self._pause_in_flight = False
        self._draining = False
        self._pumping = False
        self._guard_latched = False
        self._status = ""
        self._values = {}
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(int(PAUSE_WAIT_TIMEOUT_S * 1000))
        self._deadline.timeout.connect(self._pause_failed)
        # While moves are queued or have just run, the core poll floor drops
        # to the urgent rate so the position readout tracks the head.
        self._guard_cooldown = QTimer(self)
        self._guard_cooldown.setSingleShot(True)
        self._guard_cooldown.setInterval(3000)
        self._guard_cooldown.timeout.connect(lambda: self._set_guard(False))
        data.changed.connect(self.observe)
        data.changed.connect(self._pump)
        data.invalidated.connect(self._reset)
        data.commandChanged.connect(self._command_changed)
        commands.changed.connect(self._pump)
        commands.emergencyStopped.connect(self._reset)
        self.observe()

    @property
    def values(self):
        return dict(self._values)

    def observe(self):
        core = self._data.snapshot.core
        auxiliary = self._data.snapshot.auxiliary
        state = str((core.get("print_stats") or {}).get("state") or "")
        toolhead = auxiliary.get("toolhead") or {}
        gcode_move = core.get("gcode_move") or {}
        absolute = bool(gcode_move.get("absolute_coordinates", True))
        self._absolute_coordinates = absolute
        self._values = {
            # Motion controls are exposed only when moves are immediately
            # allowed: while printing the user must pause explicitly first.
            # The pause-first queue stays as the safety net for anything
            # that slips through (and drops on resume).
            "jogEnabled": bool(self._data.active and self._data.connected and jog_gate(state) == "allowed"),
            "jogDistance": self._jog_distance,
            "extrudeDistance": self._extrude_distance,
            "extrudeSpeed": self._extrude_speed,
            "homedAxes": str(toolhead.get("homed_axes") or ""),
            "positionMode": position_mode_text(absolute),
            "jogStatus": self._status,
        }
        self.changed.emit()

    def set_distance(self, distance):
        # The free-text field is a magnitude; the buttons carry direction,
        # so a negative entry must not silently invert every arrow.
        if not jog_distance_ok(distance) or float(distance) <= 0:
            return
        self._jog_distance = float(distance)
        self._values["jogDistance"] = self._jog_distance
        self.changed.emit()

    def set_extrude_distance(self, distance):
        if not extrude_distance_ok(distance) or float(distance) <= 0:
            return
        self._extrude_distance = float(distance)
        self._values["extrudeDistance"] = self._extrude_distance
        self.changed.emit()

    def set_extrude_speed(self, speed):
        if not extrude_speed_ok(speed):
            return
        self._extrude_speed = float(speed)
        self._values["extrudeSpeed"] = self._extrude_speed
        self.changed.emit()

    def jog(self, axis, direction):
        try:
            direction = int(direction)
        except (TypeError, ValueError):
            return
        if direction not in (-1, 1) or not axis_ok(str(axis)):
            return
        axis = str(axis)
        distance = self._clamp_jog(axis, self._jog_distance * direction)
        if distance == 0.0:
            return  # already at the limit: nothing to move
        try:
            op = make_jog_op(axis, distance, self._absolute_coordinates)
        except ValueError:
            return
        self._push(op)

    def _clamp_jog(self, axis, distance):
        """Keep relative jogs inside the toolhead's axis limits.

        The live position comes from the core motion report; the limits
        from the toolhead auxiliary object. Without position data, any
        move toward the axis minimum is forbidden outright — the head may
        already be at zero.
        """
        index = {"x": 0, "y": 1, "z": 2}[axis]
        live = (self._data.snapshot.core.get("motion_report") or {}).get("live_position") or ()
        toolhead = self._data.snapshot.auxiliary.get("toolhead") or {}
        try:
            current = float(live[index])
            minimum = toolhead.get("axis_minimum") or ()
            maximum = toolhead.get("axis_maximum") or ()
            minimum = float(minimum[index]) if len(minimum) > index else None
            maximum = float(maximum[index]) if len(maximum) > index else None
        except (TypeError, ValueError, IndexError):
            return distance if distance > 0 else 0.0
        return clamp_relative_move(distance, current, minimum, maximum)

    def home(self, axis=""):
        try:
            op = make_home_op(str(axis))
        except ValueError:
            return
        self._push(op)

    def motors_off(self):
        self._push(make_motors_off_op())

    def center_toolhead(self):
        """Absolute move to the plate centre at the park height."""
        toolhead = self._data.snapshot.auxiliary.get("toolhead") or {}
        minimum = toolhead.get("axis_minimum") or ()
        maximum = toolhead.get("axis_maximum") or ()
        if len(minimum) < 2 or len(maximum) < 2:
            return
        try:
            x = (float(minimum[0]) + float(maximum[0])) / 2.0
            y = (float(minimum[1]) + float(maximum[1])) / 2.0
        except (TypeError, ValueError):
            return
        self._push(make_center_op(x, y, self._absolute_coordinates))

    def z_to_zero(self):
        """Absolute Z move down to 0."""
        self._push(make_z0_op(self._absolute_coordinates))

    def extrude(self, direction):
        try:
            direction = int(direction)
        except (TypeError, ValueError):
            return
        if direction not in (-1, 1):
            return
        try:
            op = make_extrude_op(self._extrude_distance * direction, self._extrude_speed,
                                 self._absolute_coordinates)
        except ValueError:
            return
        self._push(op)

    def _push(self, op):
        self._pending, status = push_op(self._pending, op, absolute_coordinates=self._absolute_coordinates)
        if status:
            self._set_status(status)
        self._pending = self._clamp_tail(self._pending)
        if self._pending:
            self._guard_cooldown.stop()
            self._guard_latched = True
            self._set_guard(True)
        self._pump()

    def _clamp_tail(self, pending):
        """Re-clamp a merged jog tail against the (possibly stale) position.

        Each tap is clamped before it queues, but consecutive taps merge
        against the same polled position: two -25 taps at z=10 each clamp
        to -10 and the merged move would overshoot to -10. The tail gets
        clamped again after merging, so the executed move never leaves the
        axis limits.
        """
        if not pending:
            return pending
        tail = pending[-1]
        if tail.kind != "jog":
            return pending
        clamped = self._clamp_jog(tail.axis, tail.distance)
        if clamped == 0.0:
            return pending[:-1]
        if clamped != tail.distance:
            return pending[:-1] + (make_jog_op(tail.axis, clamped, self._absolute_coordinates),)
        return pending

    def _pump(self):
        # Sending a command can synchronously deliver its own terminal
        # event (a refused Pause completes with "failed" inside send()).
        # The nested dispatch must not re-run the pump before this frame's
        # handlers have finished; the commandChanged handler drops the
        # queue on the failure, which is the only correct follow-up.
        if self._pumping:
            return
        self._pumping = True
        try:
            self._pump_dispatch()
        finally:
            self._pumping = False

    def _pump_dispatch(self):
        gate = jog_gate(self._state())
        if not self._pending:
            self._pause_waiting = self._pause_in_flight = self._draining = False
            self._deadline.stop()
            self._set_status("")
            # The queue just went empty (drained or dropped): keep the fast
            # poll floor briefly so the readout settles on the final
            # position, then release the guard. Only the transition arms
            # the timer — every poll pumps here, and re-arming on each
            # would make the cooldown impossible to reach at poll cadence.
            if self._guard_latched:
                self._guard_latched = False
                self._guard_cooldown.start()
            return
        if gate == "disabled":
            self._pending = ()
            self._pause_waiting = self._pause_in_flight = self._draining = False
            self._deadline.stop()
            self._set_status(STATUS_NOT_READY)
            return
        if gate == "pause-first":
            if self._draining and not self._pause_waiting:
                # The pause had been granted and the print resumed mid-drain;
                # remaining moves must never run while printing.
                self._pending = ()
                self._draining = False
                self._set_status(STATUS_RESUMED_DROP)
                return
            if not self._pause_waiting:
                self._pause_waiting = True
                self._deadline.start()
                self._set_status(STATUS_PAUSE_WAITING)
            if not self._pause_in_flight and not self._commands.busy:
                # The shared tracked Pause; when it is confirmed the fresh
                # paused state lets the drain below run.
                if self._commands.send("Pause", "printer/print/pause"):
                    self._pause_in_flight = True
            return
        # Idle or paused: moves are allowed.
        if self._pause_waiting or self._pause_in_flight:
            self._pause_waiting = self._pause_in_flight = False
            self._deadline.stop()
            self._set_status(STATUS_PAUSED_MOVING)
        while self._pending and not self._commands.busy:
            op = self._pending[0]
            if self._commands.send(op.label, "printer/gcode/script", {"script": op.script}):
                self._pending = self._pending[1:]
                self._draining = True
            else:
                break
        if not self._pending:
            self._draining = False
            self._set_status("")

    def _command_changed(self, event):
        if event.get("name") != "Pause" or not (self._pause_waiting or self._pause_in_flight):
            return
        if not event.get("terminal"):
            return
        if event.get("outcome") == "confirmed":
            # Stop the deadline but keep the pause flags armed until the
            # fresh paused state reaches the snapshot: the state update can
            # arrive after this event, and an early clear would re-send a
            # redundant Pause against a stale "printing" state.
            self._deadline.stop()
            self._set_status(STATUS_PAUSED_MOVING)
            return
        self._pause_waiting = self._pause_in_flight = False
        self._deadline.stop()
        self._pending = ()
        self._draining = False
        self._set_status(STATUS_PAUSE_TIMED_OUT)

    def _pause_failed(self):
        if not self._pause_waiting:
            return
        self._pause_waiting = False
        self._pending = ()
        self._draining = False
        self._set_status(STATUS_PAUSE_TIMED_OUT)
        # _pause_in_flight is left alone: if the pause does land later, a
        # later _pump simply finds an empty queue.

    def _reset(self):
        self._pending = ()
        self._pause_waiting = self._pause_in_flight = self._draining = False
        self._deadline.stop()
        self._guard_cooldown.stop()
        self._guard_latched = False
        self._set_guard(False)
        self._set_status("")
        self.observe()

    def _set_guard(self, active):
        setter = getattr(self._data, "set_toolhead_guard", None)
        if setter is not None:
            setter(active)

    def _state(self):
        if not self._data.active or not self._data.connected:
            return ""
        return str((self._data.snapshot.core.get("print_stats") or {}).get("state") or "")

    def _set_status(self, status):
        if self._status == status:
            return
        self._status = status
        self._values["jogStatus"] = status
        self.changed.emit()

    def close(self):
        self._deadline.stop()
        self._guard_cooldown.stop()
