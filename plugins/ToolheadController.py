"""Monitor toolhead-control owner: the jog queue and pause-first sequencing.

All G-code text, safety classification and queue coalescing rules are pure
policy in ToolheadPolicy; this object only owns mutable state (the pending
queue, the pause wait and its deadline) and sends commands through the
shared Monitor command lane.

Safety: moves run while the printer is idle or paused. A jog tap while
printing first sends the tracked Pause command (the same request as the
Monitor pause button); the queue drains only once a fresh ``paused`` state
is observed, and is dropped if the pause is not confirmed in time or the
print resumes mid-drain. Taps arriving during the pause wait coalesce into
the queue, so the controls stay enabled while the printer pauses.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .ToolheadPolicy import (
    JOG_DISTANCES,
    PAUSE_WAIT_TIMEOUT_S,
    STATUS_NOT_READY,
    STATUS_PAUSE_TIMED_OUT,
    STATUS_PAUSE_WAITING,
    STATUS_PAUSED_MOVING,
    STATUS_RESUMED_DROP,
    axis_ok,
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
        self._jog_distance = 1.0
        self._absolute_coordinates = True
        self._pause_waiting = False
        self._pause_in_flight = False
        self._draining = False
        self._pumping = False
        self._status = ""
        self._values = {}
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(int(PAUSE_WAIT_TIMEOUT_S * 1000))
        self._deadline.timeout.connect(self._pause_failed)
        data.changed.connect(self.observe)
        data.changed.connect(self._pump)
        data.invalidated.connect(self._reset)
        data.commandChanged.connect(self._command_changed)
        commands.changed.connect(self._pump)
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
            "jogEnabled": bool(self._data.active and self._data.connected and jog_gate(state) != "disabled"),
            "jogDistance": self._jog_distance,
            "homedAxes": str(toolhead.get("homed_axes") or ""),
            "positionMode": position_mode_text(absolute),
            "jogStatus": self._status,
        }
        self.changed.emit()

    def set_distance(self, distance):
        try:
            distance = float(distance)
        except (TypeError, ValueError):
            return
        if distance not in JOG_DISTANCES:
            return
        self._jog_distance = distance
        self._values["jogDistance"] = distance
        self.changed.emit()

    def jog(self, axis, direction):
        try:
            direction = int(direction)
        except (TypeError, ValueError):
            return
        if direction not in (-1, 1) or not axis_ok(str(axis)):
            return
        try:
            op = make_jog_op(str(axis), self._jog_distance * direction, self._absolute_coordinates)
        except ValueError:
            return
        self._push(op)

    def home(self, axis=""):
        try:
            op = make_home_op(str(axis))
        except ValueError:
            return
        self._push(op)

    def motors_off(self):
        self._push(make_motors_off_op())

    def extrude(self, amount):
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return
        try:
            op = make_extrude_op(amount, self._absolute_coordinates)
        except ValueError:
            return
        self._push(op)

    def _push(self, op):
        self._pending, status = push_op(self._pending, op, absolute_coordinates=self._absolute_coordinates)
        if status:
            self._set_status(status)
        self._pump()

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
        self._set_status("")
        self.observe()

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
