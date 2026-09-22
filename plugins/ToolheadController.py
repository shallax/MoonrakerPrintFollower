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

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .MonitorPermissions import R_UNKNOWN, Verdict, can_jog
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
    make_extrude_op,
    make_home_op,
    make_jog_op,
    make_motors_off_op,
    position_mode_text,
    push_op,
)


class ToolheadController(QObject):
    changed = pyqtSignal()
    # A move was rejected by the client-side clamp: the model routes
    # this into the console as a local note (the live
    # request — a rejected nudge must explain itself in BOTH the
    # jog status and the console feed).
    rejectedNote = pyqtSignal(str)

    def __init__(self, data, commands, parent=None):
        super().__init__(parent)
        self._data = data
        self._commands = commands
        self._pending = ()
        self._z_rejection_noted = False
        self._jog_distance = JOG_DISTANCE_DEFAULT
        self._extrude_distance = EXTRUDE_DISTANCE_DEFAULT
        self._extrude_speed = EXTRUDE_SPEED_DEFAULT
        self._absolute_coordinates = True
        self._mode_latch = None
        # The client-side axis projections (the live report's stale-
        # poll safety, generalised): rapid nudge taps outrun the
        # poll, and each tap clamped against the STALE position let
        # the merged queue walk the head past a limit — Z went below
        # zero (the 2026-09-19 review's B), and X/Y can overshoot
        # their maxima the same way. The projection advances with
        # every queued move and re-syncs from the poll once the
        # queue drains.
        self._axis_estimate = {"x": None, "y": None, "z": None}
        # The dispatched-but-unreflected DOWNWARD distance per axis:
        # while it is nonzero, a poll between the pre-command level
        # and the projection is mid-flight, not truth.
        self._axis_down_pending = {"x": 0.0, "y": 0.0, "z": 0.0}
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
        # The assumed-cancelled latch flips through commands.changed:
        # the jog gate must refresh with it (the live
        # report — the jog pad stayed locked behind a print that no
        # longer existed).
        commands.changed.connect(self.observe)
        commands.emergencyStopped.connect(self._reset)
        self.observe()

    @property
    def values(self):
        return dict(self._values)

    def observe(self):
        core = self._data.snapshot.core
        auxiliary = self._data.snapshot.auxiliary
        toolhead = auxiliary.get("toolhead") or {}
        gcode_move = core.get("gcode_move") or {}
        absolute = bool(gcode_move.get("absolute_coordinates", True))
        if self._mode_latch is not None and time.monotonic() < self._mode_latch:
            if absolute == self._absolute_coordinates:
                # The printer adopted the toggle already.
                self._mode_latch = None
            # else: HOLD the user's choice while the G90/G91 rides
            # the lane — an eager poll revert would bounce the mode
            # display back and forth (the live report:
            # hysteresis on clicking).
        else:
            self._mode_latch = None
            self._absolute_coordinates = absolute
        for axis in ("x", "y", "z"):
            if any(getattr(op, "axis", None) == axis for op in self._pending):
                continue
            # The queue has no moves of this axis left: the poll's
            # position is the truth again — but only once it has
            # actually CAUGHT UP. An op leaves the queue at dispatch,
            # so a poll that still reads HIGHER than the projection
            # is mid-flight while downward distance is unreflected;
            # adopting it would re-arm the estimate and let repeated
            # stale polls accept downward distance beyond the real
            # headroom (the 2026-09-19 review's B). A poll ABOVE the
            # pre-command level is a genuine upward move (external
            # G-code, a home) and adopts. No position data clears
            # the estimate, as before (the no-data clamp fails closed
            # anyway).
            polled = self._polled_axis(axis)
            if self._axis_estimate[axis] is None or polled is None:
                self._axis_estimate[axis] = polled
                self._axis_down_pending[axis] = 0.0
            elif polled <= self._axis_estimate[axis] + 1e-9:
                # The head reached the projection: adopt the truth.
                self._axis_estimate[axis] = polled
                self._axis_down_pending[axis] = 0.0
            elif polled > self._axis_estimate[axis] + self._axis_down_pending[axis] + 1e-9:
                # Higher than the level our downward move started
                # from: the head genuinely moved up.
                self._axis_estimate[axis] = polled
                self._axis_down_pending[axis] = 0.0
            # else: mid-flight — the projection stays conservative.
        # The policy projection (4.2.0, A4): jogEnabled stays the
        # published property, now fed by the permissions table's
        # can_jog — the same state mapping as before (the shipped
        # jog_gate), plus the fail-closed prelude. Motion controls
        # are exposed only when moves are immediately allowed: while
        # printing the user must pause explicitly first. The
        # pause-first queue stays as the safety net for anything
        # that slips through (and drops on resume).
        observation = getattr(self._data, "observation", None)
        jog_verdict = can_jog(observation) if observation is not None \
            else Verdict("disabled", R_UNKNOWN)
        new_values = {
            "jogEnabled": jog_verdict.mode == "allowed",
            "jogDistance": self._jog_distance,
            "extrudeDistance": self._extrude_distance,
            "extrudeSpeed": self._extrude_speed,
            "homedAxes": str(toolhead.get("homed_axes") or ""),
            "positionMode": position_mode_text(self._absolute_coordinates),
            "jogStatus": self._status,
        }
        # The Z projection, the mode latch and the clamp state update
        # above regardless; the OUTWARD signal fires only when the
        # projection visibly changed — an unchanged heartbeat must
        # not rebuild the whole model (the publish storm's
        # suppression, the 2026-09-19 performance review).
        if new_values != self._values:
            self._values = new_values
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
            if axis == "z" and direction == -1:
                # The clamp rejected the move (the live
                # request): the jog status says so, and the console
                # gets a local note — once per burst, so a flurry of
                # taps cannot flood the feed.
                self._set_status("Z nudge rejected — the head would go below 0.00 Z")
                if not self._z_rejection_noted:
                    self._z_rejection_noted = True
                    self.rejectedNote.emit("Z nudge rejected — the head would go below 0.00 Z.")
            return  # already at the limit: nothing to move
        if axis == "z":
            self._z_rejection_noted = False
        try:
            op = make_jog_op(axis, distance, self._absolute_coordinates)
        except ValueError:
            return
        self._push(op)

    def set_absolute(self, absolute: bool) -> None:
        """The abs/rel toggle (a live request): subsequent
        jogs, extrudes and parks encode against this mode, and the
        PRINTER adopts it too — the actual G90/G91 rides the command
        lane so the next poll's gcode_move agrees instead of
        reverting a local-only flag."""
        self._absolute_coordinates = bool(absolute)
        # Hold the toggle against poll reverts until the printer
        # reports the new mode (or the window lapses — the latch's
        # fallback for a lane that never answers).
        self._mode_latch = time.monotonic() + 5.0
        self._values["positionMode"] = position_mode_text(self._absolute_coordinates)
        self.changed.emit()
        self._commands.send("Absolute mode" if absolute else "Relative mode",
                            "printer/gcode/script", {"script": "G90" if absolute else "G91"})

    def _polled_axis(self, axis):
        """The freshest axis value the poll knows: the live motion
        report when present, else the gcode position (the Position
        readout's own source — the live report: the plugin KNOWS
        the position and must guard with it)."""
        index = {"x": 0, "y": 1, "z": 2}[axis]
        core = self._data.snapshot.core
        live = (core.get("motion_report") or {}).get("live_position") or ()
        if len(live) > index:
            try:
                return float(live[index])
            except (TypeError, ValueError):
                pass
        position = (core.get("gcode_move") or {}).get("gcode_position") or ()
        if len(position) > index:
            try:
                return float(position[index])
            except (TypeError, ValueError):
                pass
        return None

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
            if self._axis_estimate[axis] is not None:
                # The client-side axis projection (the live
                # report: rapid nudge taps outrun the poll, and each
                # tap clamped against the STALE position let the
                # merged queue walk the head past a limit — Z below
                # zero, X/Y beyond their maxima). The estimate
                # advances with every queued move of the axis and
                # re-syncs from the poll once the queue drains.
                current = self._axis_estimate[axis]
            else:
                current = float(live[index])
            minimum = toolhead.get("axis_minimum") or ()
            maximum = toolhead.get("axis_maximum") or ()
            minimum = float(minimum[index]) if len(minimum) > index else None
            maximum = float(maximum[index]) if len(maximum) > index else None
            if axis == "z" and (minimum is None or minimum < 0.0):
                # The Z floor is ZERO, whatever the configured
                # position_min says (the live ruling:
                # "you know what clicking nudge would move to. Why
                # are you allowing it?" — many printers configure a
                # negative Z minimum for probe travel, but the jog
                # pad must never send the head below 0.00).
                minimum = 0.0
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
        self._pending, status = push_op(self._pending, op)
        if status:
            self._set_status(status)
        # The tail re-clamps BEFORE the estimate advances, so
        # the re-clamp sees the pre-tap position.
        self._pending = self._clamp_tail(self._pending)
        # The axis projection advances the moment the move is ACCEPTED
        # into the queue — a later tap clamps against the move its
        # predecessor already covers, never the stale polled value
        # (the live report: the head could still be nudged past a
        # limit).
        op_axis = getattr(op, "axis", None)
        if op_axis in ("x", "y", "z"):
            base = self._axis_estimate[op_axis]
            if base is None:
                base = self._polled_axis(op_axis)
            if base is not None:
                distance = getattr(op, "distance", 0.0)
                self._axis_estimate[op_axis] = base + distance
                if distance < 0:
                    # A downward command's reflection is now owed;
                    # an upward command supersedes any owed one.
                    self._axis_down_pending[op_axis] += abs(distance)
                else:
                    self._axis_down_pending[op_axis] = 0.0
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
        # The dispatch gate is the SAME row the click gate publishes
        # (the adversarial round's H1): the old jog_gate derivation
        # was lock-blind — a move queued before the padlock went down
        # dispatched after it. One derivation, both edges.
        observation = getattr(self._data, "observation", None)
        verdict = can_jog(observation) if observation is not None \
            else Verdict("disabled", R_UNKNOWN)
        gate = verdict.mode
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
        self._axis_estimate = {"x": None, "y": None, "z": None}
        self._axis_down_pending = {"x": 0.0, "y": 0.0, "z": 0.0}
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
