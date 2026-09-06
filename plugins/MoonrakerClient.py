from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .MoonrakerProtocol import status_endpoint
from .MoonrakerSession import MoonrakerSession, MoonrakerSessionState, RequestCategory


class MoonrakerClient(QObject):
    """Shared resilient HTTP-only core poller over one MoonrakerSession."""

    statusReceived = pyqtSignal(object)
    connectionChanged = pyqtSignal(bool, str)
    capabilitiesChanged = pyqtSignal(object)
    commandChanged = pyqtSignal(object)

    RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)

    def __init__(self, parent=None, session=None, transport=None) -> None:
        super().__init__(parent)
        self._base_url = ""
        self._api_key = ""
        self._poll_interval_ms = 750
        self._enabled = False
        self._connected = False
        self._retry_index = 0
        self._generation = 0
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
        self._poll_timer.setSingleShot(False)
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(self.force_refresh)

    @property
    def session(self) -> MoonrakerSession:
        return self._session

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
        interval_changed = new_interval != self._poll_interval_ms
        self._base_url = new_base_url
        self._api_key = new_api_key
        self._poll_interval_ms = new_interval
        if endpoint_changed:
            self._session.configure(new_base_url, new_api_key)
        self._apply_adaptive_interval()
        if endpoint_changed and self._enabled:
            self.stop(reset_session=False)
            self.start()
        elif interval_changed and self._enabled:
            self._apply_adaptive_interval()

    def start(self) -> None:
        if self._enabled:
            return
        self._generation += 1
        self._enabled = True
        self._retry_index = 0
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
        self._retry_index = 0
        self._session.coalescer.cancel(RequestCategory.CORE.value)
        self._session.transport.cancel_owner("core")
        was_connected = self._connected
        self._connected = False
        self._session.connected = False
        if reset_session:
            self._session.reset()
        if was_connected:
            self.connectionChanged.emit(False, "Moonraker polling stopped")

    def set_pause_guard(self, active: bool) -> None:
        changed = self._session.set_pause_guard(active)
        self._apply_adaptive_interval()
        if changed and active and self._enabled:
            self.force_refresh()

    def force_refresh(self) -> None:
        if not self._enabled or not self._base_url:
            return
        key = RequestCategory.CORE.value
        if not self._session.coalescer.begin(key, force=True):
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
            follow_up = self._session.coalescer.complete(key)
            if follow_up and self._enabled:
                QTimer.singleShot(0, self.force_refresh)

    def _handle_http_status(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
        generation: int,
    ) -> None:
        key = RequestCategory.CORE.value
        if generation != self._generation:
            self._session.coalescer.complete(key)
            return
        try:
            if error:
                self._handle_failure(f"Moonraker request failed: {error}")
                return
            result = (payload or {}).get("result") or {}
            status = result.get("status") or {}
            if not isinstance(status, dict):
                self._handle_failure("Moonraker returned an invalid status response")
                return
            self._handle_success()
            merged, changed_commands = self._session.merge_status(status)
            self._apply_adaptive_interval()
            self._update_status_capabilities(merged)
            self.statusReceived.emit(merged)
            for command in changed_commands:
                self.commandChanged.emit(command.as_dict())
        except Exception as exc:
            self._handle_failure(f"Moonraker status error: {exc}")
        finally:
            follow_up = self._session.coalescer.complete(key)
            if follow_up and self._enabled:
                QTimer.singleShot(0, self.force_refresh)

    def _apply_adaptive_interval(self) -> None:
        interval = self._session.poll_policy.interval_ms(
            RequestCategory.CORE,
            self._poll_interval_ms,
            self._session.snapshot.printer_state,
            urgent=self._session.pause_guard,
        )
        if self._poll_timer.interval() != interval:
            self._poll_timer.setInterval(interval)

    def _handle_success(self) -> None:
        self._retry_index = 0
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
            urgent=self._session.pause_guard,
        )
        retry_interval = max(adaptive, delay)
        if self._poll_timer.interval() != retry_interval:
            self._poll_timer.setInterval(retry_interval)
        self._connected = False
        self._session.connected = False
        self.connectionChanged.emit(False, f"{reason}; retrying in {retry_interval / 1000:g}s")

    def track_command(self, name: str, expected_states: Iterable[str] = (), *, timeout_s: float = 10.0) -> None:
        command = self._session.commands.issue(name, expected_states, timeout_s=timeout_s)
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
        changed = self._session.commands.observe(self._session.snapshot.printer_state)
        for command in changed:
            self.commandChanged.emit(command.as_dict())

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
