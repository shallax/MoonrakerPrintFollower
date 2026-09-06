from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .MoonrakerProtocol import status_endpoint
from .MoonrakerSession import MoonrakerSessionState, RequestCategory


class MoonrakerClient(QObject):
    """Shared resilient HTTP-only Moonraker core-status session."""

    statusReceived = pyqtSignal(object)
    connectionChanged = pyqtSignal(bool, str)
    capabilitiesChanged = pyqtSignal(object)
    commandChanged = pyqtSignal(object)

    RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)

    def __init__(self, parent=None, session: Optional[MoonrakerSessionState] = None) -> None:
        super().__init__(parent)
        self._base_url = ""
        self._api_key = ""
        self._poll_interval_ms = 750
        self._enabled = False
        self._connected = False
        self._retry_index = 0
        self._generation = 0
        self._session = session or MoonrakerSessionState()
        self._network = QNetworkAccessManager(self)
        self._http_reply: Optional[QNetworkReply] = None
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
    def session(self) -> MoonrakerSessionState:
        return self._session

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
        endpoint_changed = new_base_url != self._base_url or new_api_key != self._api_key
        interval_changed = new_interval != self._poll_interval_ms
        self._base_url = new_base_url
        self._api_key = new_api_key
        self._poll_interval_ms = new_interval
        if endpoint_changed:
            # URL and credentials together define request identity. Never retain a
            # snapshot obtained with the previous identity, even when only the API
            # key changed and the URL text itself stayed the same.
            self._session.reset()
            self._session.base_url = new_base_url
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
        if self._http_reply is not None:
            try:
                self._http_reply.abort()
                self._http_reply.deleteLater()
            except Exception:
                pass
            self._http_reply = None
        was_connected = self._connected
        self._connected = False
        self._session.connected = False
        if reset_session:
            self._session.reset()
        if was_connected:
            self.connectionChanged.emit(False, "Moonraker polling stopped")

    def force_refresh(self) -> None:
        if not self._enabled or not self._base_url:
            return
        key = RequestCategory.CORE.value
        if not self._session.coalescer.begin(key, force=True):
            return
        if self._http_reply is not None:
            try:
                if self._http_reply.isRunning():
                    self._session.coalescer.complete(key)
                    return
            except Exception:
                pass
        request = QNetworkRequest(QUrl(status_endpoint(self._base_url)))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"Cura Moonraker Print Follower")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        if self._api_key:
            request.setRawHeader(b"X-Api-Key", self._api_key.encode("utf-8"))
        reply = self._network.get(request)
        self._http_reply = reply
        generation = self._generation
        reply.finished.connect(lambda r=reply, g=generation: self._handle_http_status(r, g))

    def _handle_http_status(self, reply: QNetworkReply, generation: int) -> None:
        key = RequestCategory.CORE.value
        if generation != self._generation or reply is not self._http_reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        self._http_reply = None
        follow_up = False
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._handle_failure(f"Moonraker request failed: {reply.errorString()}")
                return
            payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
            result = payload.get("result") or {}
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
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._handle_failure(f"Invalid Moonraker response: {error}")
        except Exception as error:
            self._handle_failure(f"Moonraker status error: {error}")
        finally:
            follow_up = self._session.coalescer.complete(key)
            try:
                reply.deleteLater()
            except Exception:
                pass
            if follow_up and self._enabled:
                QTimer.singleShot(0, self.force_refresh)

    def _apply_adaptive_interval(self) -> None:
        interval = self._session.poll_policy.interval_ms(
            RequestCategory.CORE,
            self._poll_interval_ms,
            self._session.snapshot.printer_state,
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
