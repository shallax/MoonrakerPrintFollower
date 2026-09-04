from __future__ import annotations

import json
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .MoonrakerProtocol import status_endpoint


class MoonrakerClient(QObject):
    """Resilient HTTP-polling Moonraker status client.

    The configured polling interval is used while Moonraker is healthy. Network
    failures temporarily back off through 1/2/5/10/30 seconds so an offline
    printer or proxy cannot be hammered by a sub-second polling interval. A
    successful response immediately restores the configured interval.
    """

    statusReceived = pyqtSignal(object)
    connectionChanged = pyqtSignal(bool, str)
    capabilitiesChanged = pyqtSignal(object)

    RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._base_url = ""
        self._api_key = ""
        self._poll_interval_ms = 750
        self._enabled = False
        self._connected = False
        self._retry_index = 0
        self._generation = 0

        self._network = QNetworkAccessManager(self)
        self._http_reply: Optional[QNetworkReply] = None
        self._status: Dict[str, Dict[str, Any]] = {}
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
    def capabilities(self) -> Dict[str, Any]:
        return dict(self._capabilities)

    def configure(self, base_url: str, api_key: str, poll_interval_ms: int) -> None:
        new_base_url = str(base_url or "").rstrip("/")
        new_api_key = str(api_key or "")
        try:
            new_interval = max(1, int(poll_interval_ms))
        except (TypeError, ValueError):
            new_interval = 750

        changed = (
            new_base_url != self._base_url
            or new_api_key != self._api_key
            or new_interval != self._poll_interval_ms
        )
        self._base_url = new_base_url
        self._api_key = new_api_key
        self._poll_interval_ms = new_interval
        self._poll_timer.setInterval(self._poll_interval_ms)

        if changed and self._enabled:
            self.stop()
            self.start()

    def start(self) -> None:
        if self._enabled:
            return
        self._generation += 1
        self._enabled = True
        self._retry_index = 0
        self._status.clear()
        self._capabilities.update({
            "objects": [],
            "current_layer": False,
            "file_position": False,
            "motion_report": False,
        })
        self.capabilitiesChanged.emit(dict(self._capabilities))
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.start()
        self.connectionChanged.emit(False, "Connecting to Moonraker")
        self.force_refresh()

    def stop(self) -> None:
        # Invalidate any completion already queued for the previous session.
        # This matters when Cura switches printers: a late HTTP completion from
        # the old target must never publish status into the new printer session.
        self._generation += 1
        self._enabled = False
        self._poll_timer.stop()
        self._retry_index = 0
        if self._http_reply is not None:
            try:
                self._http_reply.abort()
                self._http_reply.deleteLater()
            except Exception:
                pass
            self._http_reply = None
        was_connected = self._connected
        self._connected = False
        if was_connected:
            self.connectionChanged.emit(False, "Moonraker polling stopped")

    def force_refresh(self) -> None:
        if not self._enabled or not self._base_url:
            return
        if self._http_reply is not None:
            try:
                if self._http_reply.isRunning():
                    return
            except Exception:
                pass

        request = QNetworkRequest(QUrl(status_endpoint(self._base_url)))
        request.setRawHeader(b"Accept", b"application/json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        if self._api_key:
            request.setRawHeader(b"X-Api-Key", self._api_key.encode("utf-8"))

        reply = self._network.get(request)
        self._http_reply = reply
        generation = self._generation
        reply.finished.connect(lambda r=reply, g=generation: self._handle_http_status(r, g))

    def _handle_http_status(self, reply: QNetworkReply, generation: int) -> None:
        if generation != self._generation or reply is not self._http_reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        self._http_reply = None

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
            self._merge_status(status)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._handle_failure(f"Invalid Moonraker response: {error}")
        except Exception as error:
            self._handle_failure(f"Moonraker status error: {error}")
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass

    def _handle_success(self) -> None:
        self._retry_index = 0
        if self._poll_timer.interval() != self._poll_interval_ms:
            self._poll_timer.setInterval(self._poll_interval_ms)
        if not self._connected:
            self._connected = True
            self.connectionChanged.emit(True, "Moonraker connected")

    def _handle_failure(self, reason: str) -> None:
        delay = self.RETRY_DELAYS_MS[
            min(self._retry_index, len(self.RETRY_DELAYS_MS) - 1)
        ]
        self._retry_index = min(
            self._retry_index + 1, len(self.RETRY_DELAYS_MS) - 1
        )
        retry_interval = max(self._poll_interval_ms, delay)
        if self._poll_timer.interval() != retry_interval:
            self._poll_timer.setInterval(retry_interval)

        self._connected = False
        self.connectionChanged.emit(
            False,
            f"{reason}; retrying in {retry_interval / 1000:g}s",
        )

    def _merge_status(self, patch: Dict[str, Any]) -> None:
        for object_name, value in patch.items():
            if isinstance(value, dict):
                existing = self._status.get(object_name)
                if not isinstance(existing, dict):
                    existing = {}
                    self._status[object_name] = existing
                existing.update(value)
            else:
                self._status[object_name] = value

        self._update_status_capabilities()
        self.statusReceived.emit(dict(self._status))

    def _update_status_capabilities(self) -> None:
        objects = set(self._capabilities.get("objects") or [])
        objects.update(str(k) for k in self._status.keys())
        self._capabilities["objects"] = sorted(objects)

        print_stats = self._status.get("print_stats") or {}
        info = print_stats.get("info") if isinstance(print_stats, dict) else {}
        virtual_sdcard = self._status.get("virtual_sdcard") or {}
        self._capabilities["current_layer"] = (
            isinstance(info, dict) and info.get("current_layer") is not None
        )
        self._capabilities["file_position"] = (
            isinstance(virtual_sdcard, dict)
            and virtual_sdcard.get("file_position") is not None
        )
        self._capabilities["motion_report"] = "motion_report" in self._status
        self.capabilitiesChanged.emit(dict(self._capabilities))
