from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal

from .MoonrakerProtocol import status_endpoint
from .MoonrakerSession import MoonrakerSession, MoonrakerSessionState, RequestCategory


class MoonrakerClient(QObject):
    """Shared resilient HTTP-only core poller over one MoonrakerSession."""

    statusReceived = pyqtSignal(object)
    connectionChanged = pyqtSignal(bool, str)
    capabilitiesChanged = pyqtSignal(object)
    commandChanged = pyqtSignal(object)
    sessionInvalidated = pyqtSignal()

    RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)

    def __init__(self, parent=None, session=None, transport=None) -> None:
        super().__init__(parent)
        self._base_url = ""
        self._api_key = ""
        self._poll_interval_ms = 750
        self._enabled = False
        self._connected = False
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        self._generation = 0
        self._assume_print_stopped = False
        if isinstance(session, MoonrakerSession):
            self._session = session
        else:
            state = session if isinstance(session, MoonrakerSessionState) else None
            self._session = MoonrakerSession(self, state=state, transport=transport)
        self._capabilities: Dict[str, Any] = {
            "objects": [],
            "current_layer": False,
            "file_position": False,
            "motion_report": False,
        }
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setSingleShot(False)
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(lambda: self._refresh(force=False))
        self._command_timer = QTimer(self)
        self._command_timer.setInterval(100)
        self._command_timer.timeout.connect(self.expire_commands)

    @property
    def session(self) -> MoonrakerSession:
        return self._session

    def set_trace_http(self, enabled) -> None:
        self._session.transport.set_trace_http(enabled)

    @property
    def transport(self):
        return self._session.transport

    @property
    def transport_metrics(self) -> Dict[str, Dict[str, float | int]]:
        return self._session.transport.metrics

    @property
    def connected(self) -> bool:
        return bool(self._connected)

    @property
    def capabilities(self) -> Dict[str, Any]:
        return dict(self._capabilities)

    @property
    def status(self) -> Dict[str, Any]:
        return self._session.snapshot.copy_status()

    def configure(self, base_url: str, api_key: str, poll_interval_ms: int) -> None:
        new_base_url = str(base_url or "").rstrip("/")
        new_api_key = str(api_key or "")
        try:
            new_interval = max(1, int(poll_interval_ms))
        except (TypeError, ValueError):
            new_interval = 750
        endpoint_changed = (new_base_url, new_api_key) != (self._base_url, self._api_key)
        was_enabled = self._enabled
        if endpoint_changed:
            # Synchronous UI-thread subscribers cancel streaming uploads and
            # deferred work while the transport still has the OLD identity.
            # The emit is unconditional: configure is the rebind point, and
            # subscribers must tear down even when the poller was not
            # enabled (an upload can be in flight without polling). Callers
            # that want no second wave (PrinterBinding) stop with
            # reset_session=False beforehand.
            self.stop(reset_session=False)
            self.sessionInvalidated.emit()
        self._base_url = new_base_url
        self._api_key = new_api_key
        self._poll_interval_ms = new_interval
        if endpoint_changed:
            self._session.configure(new_base_url, new_api_key)
        self._apply_adaptive_interval()
        if endpoint_changed and was_enabled:
            self.start()

    def start(self) -> None:
        if self._enabled:
            return
        self._generation += 1
        self._enabled = True
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        self._capabilities.update({
            "objects": [],
            "current_layer": False,
            "file_position": False,
            "motion_report": False,
        })
        self.capabilitiesChanged.emit(dict(self._capabilities))
        self._apply_adaptive_interval()
        self._poll_timer.start()
        self.connectionChanged.emit(False, "Connecting to Moonraker")
        self.force_refresh()

    def stop(self, *, reset_session: bool = True) -> None:
        self._generation += 1
        self._enabled = False
        self._poll_timer.stop()
        self._command_timer.stop()
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        if reset_session:
            self.sessionInvalidated.emit()
        self._session.coalescer.cancel(RequestCategory.CORE.value)
        self._session.transport.cancel_owner("core")
        was_connected = self._connected
        self._connected = False
        self._session.connected = False
        if reset_session:
            self._session.reset()
        if was_connected:
            self.connectionChanged.emit(False, "Moonraker polling stopped")

    def set_toolhead_guard(self, active: bool) -> None:
        changed = self._session.set_toolhead_guard(active)
        self._apply_adaptive_interval()
        if changed and active and self._enabled:
            self.force_refresh()

    def set_pause_guard(self, active: bool) -> None:
        changed = self._session.set_pause_guard(active)
        self._apply_adaptive_interval()
        if changed and active and self._enabled:
            self.force_refresh()

    def force_refresh(self) -> None:
        self._refresh(force=True)

    def _refresh(self, *, force: bool) -> None:
        if not self._enabled or not self._base_url:
            return
        if time.monotonic() < self._retry_not_before:
            return
        key = RequestCategory.CORE.value
        if not self._session.coalescer.begin(key, force=force):
            return
        generation = self._generation
        started = self._session.transport.send_json(
            "core",
            "status",
            "GET",
            status_endpoint(self._base_url),
            lambda payload, error, g=generation: self._handle_http_status(payload, error, g),
            category=RequestCategory.CORE.value,
        )
        if not started:
            self._session.coalescer.complete(key)

    def _queue_refresh(self, generation: int) -> None:
        def refresh() -> None:
            if generation == self._generation and self._enabled:
                self.force_refresh()
        QTimer.singleShot(0, refresh)

    def assume_print_stopped(self) -> None:
        """The e-stop's assumption (the author's ruling): the print is
        over until the printer reports otherwise. The CURRENT snapshot
        re-emits immediately with the assumed state so every consumer
        re-evaluates now, not at the next poll — which may never come
        if the stop wedged Moonraker."""
        if self._assume_print_stopped:
            return
        self._assume_print_stopped = True
        merged = self._session.snapshot.copy_status()
        if merged and str((merged.get("print_stats") or {}).get("state") or "").lower() in {"printing", "paused"}:
            merged["print_stats"]["state"] = "cancelled"
            self.statusReceived.emit(merged)

    def _handle_http_status(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
        generation: int,
    ) -> None:
        key = RequestCategory.CORE.value
        if generation != self._generation:
            return
        try:
            if error:
                self._handle_failure(f"Moonraker request failed: {error}")
                return
            result = (payload or {}).get("result")
            status = result.get("status") if isinstance(result, dict) else None
            if not isinstance(status, dict) or any(not isinstance(value, dict) for value in status.values()):
                self._handle_failure("Moonraker returned an invalid status response")
                return
            self._handle_success()
            if generation != self._generation:
                return
            merged, changed_commands = self._session.merge_status(status)
            self._apply_adaptive_interval()
            self._update_status_capabilities(merged)
            if generation != self._generation:
                return
            if self._assume_print_stopped:
                # The e-stop's assumption (the author's ruling): until
                # the printer reports a real non-printing state, every
                # emitted status reads as cancelled — the monitor's
                # guards, the jog gate AND the follower's coordinator
                # all consume this one observation and need no
                # per-consumer conditionals.
                state = str((merged.get("print_stats") or {}).get("state") or "").lower()
                if state in {"printing", "paused"}:
                    merged["print_stats"]["state"] = "cancelled"
                else:
                    self._assume_print_stopped = False
            self.statusReceived.emit(merged)
            for command in changed_commands:
                if generation != self._generation:
                    break
                self.commandChanged.emit(command.as_dict())
        except Exception as exc:
            self._handle_failure(f"Moonraker status error: {exc}")
        finally:
            # Signal subscribers may rebind synchronously. Never complete the
            # coalescer slot belonging to their new generation.
            if generation == self._generation:
                follow_up = self._session.coalescer.complete(key)
                if follow_up and self._enabled and not self._retry_delay_ms:
                    self._queue_refresh(generation)

    def _apply_adaptive_interval(self) -> None:
        interval = self._session.poll_policy.interval_ms(
            RequestCategory.CORE,
            self._poll_interval_ms,
            self._session.snapshot.printer_state,
            urgent=self._session.pause_guard or self._session.toolhead_guard,
        )
        interval = max(interval, self._retry_delay_ms)
        if self._poll_timer.interval() != interval:
            self._poll_timer.setInterval(interval)

    def _handle_success(self) -> None:
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        self._session.connected = True
        if not self._connected:
            self._connected = True
            self.connectionChanged.emit(True, "Moonraker connected")

    def _handle_failure(self, reason: str) -> None:
        delay = self.RETRY_DELAYS_MS[min(self._retry_index, len(self.RETRY_DELAYS_MS) - 1)]
        self._retry_index = min(self._retry_index + 1, len(self.RETRY_DELAYS_MS) - 1)
        adaptive = self._session.poll_policy.interval_ms(
            RequestCategory.CORE,
            self._poll_interval_ms,
            self._session.snapshot.printer_state,
            urgent=self._session.pause_guard or self._session.toolhead_guard,
        )
        retry_interval = max(adaptive, delay)
        self._retry_delay_ms = retry_interval
        self._retry_not_before = time.monotonic() + retry_interval / 1000.0
        if self._poll_timer.interval() != retry_interval:
            self._poll_timer.setInterval(retry_interval)
        if self._enabled:
            # Backoff starts at failure, not at the previous request's start.
            # A repeating/coarse timer could otherwise fire before the deadline
            # and postpone the next retry by a whole extra polling interval.
            self._poll_timer.start()
        self._connected = False
        self._session.connected = False
        self.connectionChanged.emit(False, f"{reason}; retrying in {retry_interval / 1000:g}s")

    def track_command(self, name: str, expected_states: Iterable[str] = (), *, timeout_s: float = 10.0) -> None:
        command = self._session.commands.issue(name, expected_states, timeout_s=timeout_s)
        self._command_timer.start()
        self.commandChanged.emit(command.as_dict())

    def accept_command(self, name: str) -> None:
        command = self._session.commands.accepted(name)
        if command is not None:
            self.commandChanged.emit(command.as_dict())
        self.force_refresh()

    def fail_command(self, name: str, detail: str) -> None:
        command = self._session.commands.failed(name, detail)
        if command is not None:
            self.commandChanged.emit(command.as_dict())

    def expire_commands(self) -> None:
        generation = self._generation
        changed = self._session.commands.expire()
        for command in changed:
            if generation != self._generation:
                return
            self.commandChanged.emit(command.as_dict())
        if not self._session.commands.has_pending:
            self._command_timer.stop()

    def _update_status_capabilities(self, status: Optional[Dict[str, Any]] = None) -> None:
        status = status if isinstance(status, dict) else self._session.snapshot.copy_status()
        objects = set(self._capabilities.get("objects") or [])
        objects.update(str(key) for key in status.keys())
        self._capabilities["objects"] = sorted(objects)
        print_stats = status.get("print_stats") or {}
        info = print_stats.get("info") if isinstance(print_stats, dict) else {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        self._capabilities["current_layer"] = isinstance(info, dict) and info.get("current_layer") is not None
        self._capabilities["file_position"] = isinstance(virtual_sdcard, dict) and virtual_sdcard.get("file_position") is not None
        self._capabilities["motion_report"] = "motion_report" in status
        self.capabilitiesChanged.emit(dict(self._capabilities))
