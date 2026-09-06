from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urljoin

from PyQt6.QtCore import QByteArray, QTimer, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from UM.Logger import Logger

from .MoonrakerProtocol import status_endpoint


class MoonrakerMonitorModel(PrinterOutputModel):
    """Unified Cura Monitor model for Moonraker/Klipper.

    Core print state is reused from MoonrakerPrintFollower while automatic
    Preview following is enabled. If following is disabled, Monitor owns a
    lightweight one-second core-status poll so monitoring remains independent
    from the Preview preference. Peripheral Klipper objects are capability-
    discovered and queried separately because the set of heaters, fans,
    filament sensors, MCUs and exclude-object support varies by printer.
    """

    monitorChanged = pyqtSignal()
    webcamsChanged = pyqtSignal()
    cameraTransformChanged = pyqtSignal()
    peripheralsChanged = pyqtSignal()
    excludeObjectsChanged = pyqtSignal()
    powerDevicesChanged = pyqtSignal()
    systemChanged = pyqtSignal()
    actionChanged = pyqtSignal()

    CORE_POLL_MS = 1000
    AUX_POLL_MS = 1000
    POWER_POLL_MS = 5000
    SYSTEM_POLL_MS = 10000
    DISCOVERY_POLL_MS = 30000

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        super().__init__(output_controller, number_of_extruders)
        self._follower = follower
        self._network = QNetworkAccessManager(self)
        self._requests: Dict[str, QNetworkReply] = {}
        self._request_generation = 0
        self._request_identity: Optional[tuple[str, str]] = None
        self._monitoring_active = True

        self._webcams: List[Dict[str, Any]] = []
        self._active_webcam_index = -1
        self._camera_name = ""
        self._camera_rotation = 0
        self._camera_flip_horizontal = False
        self._camera_flip_vertical = False

        self._monitor_state = "Not connected"
        self._monitor_state_raw = ""
        self._monitor_filename = ""
        self._monitor_progress = 0
        self._monitor_progress_fraction = 0.0
        self._monitor_layer = "—"
        self._monitor_elapsed = "00:00:00"
        self._monitor_eta = "—"
        self._monitor_finish = "—"
        self._monitor_speed = "100%"
        self._monitor_flow = "100%"
        self._monitor_position = "—"
        self._monitor_message = ""
        self._print_duration = 0.0
        self._metadata_estimated_time: Optional[float] = None
        self._metadata_filename = ""
        self._metadata_lookup_complete = False

        self._available_objects: List[str] = []
        self._aux_objects: List[str] = []
        self._aux_status: Dict[str, Any] = {}
        self._temperature_items: List[Dict[str, Any]] = []
        self._fan_items: List[Dict[str, Any]] = []
        self._filament_items: List[Dict[str, Any]] = []
        self._exclude_items: List[Dict[str, Any]] = []

        self._power_devices_raw: List[Dict[str, Any]] = []
        self._server_info: Dict[str, Any] = {}
        self._printer_info: Dict[str, Any] = {}
        self._klippy_state = "unknown"
        self._host_load = "—"
        self._memory_available = "—"
        self._cpu_temperature = "—"
        self._mcu_summary = "—"

        self._action_busy = False
        self._action_status = ""

        self._core_timer = QTimer(self)
        self._core_timer.setInterval(self.CORE_POLL_MS)
        self._core_timer.timeout.connect(self._poll_core_fallback)

        self._aux_timer = QTimer(self)
        self._aux_timer.setInterval(self.AUX_POLL_MS)
        self._aux_timer.timeout.connect(self._poll_aux_status)

        self._power_timer = QTimer(self)
        self._power_timer.setInterval(self.POWER_POLL_MS)
        self._power_timer.timeout.connect(self.refreshPowerDevices)

        self._system_timer = QTimer(self)
        self._system_timer.setInterval(self.SYSTEM_POLL_MS)
        self._system_timer.timeout.connect(self.refreshSystemInfo)

        self._discovery_timer = QTimer(self)
        self._discovery_timer.setInterval(self.DISCOVERY_POLL_MS)
        self._discovery_timer.timeout.connect(self.refreshCapabilities)

        client = getattr(follower, "_client", None)
        status_signal = getattr(client, "statusReceived", None)
        if status_signal is not None:
            try:
                status_signal.connect(self.updateMoonrakerStatus)
            except Exception as exc:
                Logger.log("w", "Moonraker Print Follower: could not bind Monitor status: %s", exc)

        self._start_background_timers()
        self.refreshAll()

    def _start_background_timers(self) -> None:
        for timer in (self._aux_timer, self._power_timer, self._system_timer, self._discovery_timer):
            if not timer.isActive():
                timer.start()

    def _stop_background_timers(self) -> None:
        for timer in (self._core_timer, self._aux_timer, self._power_timer, self._system_timer, self._discovery_timer):
            timer.stop()

    def _current_request_identity(self) -> tuple[str, str]:
        config = self._follower.current_printer_config()
        return (str(config.url or "").strip().rstrip("/"), str(config.api_key or ""))

    def _invalidate_request_session(self) -> None:
        self._request_generation += 1
        for channel in list(self._requests):
            self._cancel_channel(channel)

    def _ensure_request_session(self) -> None:
        identity = self._current_request_identity()
        if identity != self._request_identity:
            self._request_identity = identity
            self._invalidate_request_session()

    def setMonitoringActive(self, active: bool) -> None:
        active = bool(active)
        if active == self._monitoring_active:
            if active:
                self._ensure_request_session()
            return
        self._monitoring_active = active
        if not active:
            self._stop_background_timers()
            self._invalidate_request_session()
            self._action_busy = False
            self._action_status = ""
            self.actionChanged.emit()
            return
        self._request_identity = None
        self._start_background_timers()
        self.refreshAll()

    # ------------------------------------------------------------------
    # Generic Moonraker HTTP helpers
    # ------------------------------------------------------------------

    @property
    def _base_url(self) -> str:
        config = self._follower.current_printer_config()
        return str(config.url or "").strip().rstrip("/")

    def _usable_base_url(self) -> bool:
        url = QUrl(self._base_url)
        return url.isValid() and url.scheme() in ("http", "https") and bool(url.host())

    def _request(self, path: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(self._base_url + "/" + path.lstrip("/")))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"Cura Moonraker Print Follower")
        config = self._follower.current_printer_config()
        if config.api_key:
            request.setRawHeader(b"X-Api-Key", str(config.api_key).encode("utf-8"))
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        return request

    def _cancel_channel(self, channel: str) -> None:
        reply = self._requests.pop(channel, None)
        if reply is None:
            return
        try:
            if reply.isRunning():
                reply.abort()
        except Exception:
            pass
        try:
            reply.deleteLater()
        except Exception:
            pass

    def _json_request(
        self,
        channel: str,
        method: str,
        path: str,
        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],
        *,
        body: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> bool:
        if not self._monitoring_active:
            return False
        self._ensure_request_session()
        if not self._usable_base_url():
            return False

        previous = self._requests.get(channel)
        if previous is not None:
            try:
                running = previous.isRunning()
            except Exception:
                running = False
            if running and not replace:
                return False
            self._cancel_channel(channel)

        request = self._request(path)
        method = str(method or "GET").upper()
        if body is not None:
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            data = QByteArray(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        else:
            data = QByteArray()

        if method == "POST":
            reply = self._network.post(request, data)
        else:
            reply = self._network.get(request)
        self._requests[channel] = reply
        generation = self._request_generation
        reply.finished.connect(
            lambda r=reply, c=channel, cb=callback, g=generation: self._finish_json_request(c, r, cb, g)
        )
        return True

    def _finish_json_request(
        self,
        channel: str,
        reply: QNetworkReply,
        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],
        generation: int,
    ) -> None:
        if generation != self._request_generation:
            if self._requests.get(channel) is reply:
                self._requests.pop(channel, None)
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        if self._requests.get(channel) is not reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        self._requests.pop(channel, None)

        error: Optional[str] = None
        payload: Optional[Dict[str, Any]] = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error = reply.errorString()
            else:
                raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
                if raw.strip():
                    decoded = json.loads(raw)
                    if not isinstance(decoded, dict):
                        raise ValueError("Moonraker returned a non-object JSON response")
                    if decoded.get("error"):
                        raise ValueError(str(decoded.get("error")))
                    payload = decoded
                else:
                    payload = {}
        except Exception as exc:
            error = str(exc)
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass

        try:
            callback(payload, error)
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: Monitor callback failed: %s", exc)

    @staticmethod
    def _status_object(status: Any, name: str) -> Dict[str, Any]:
        if not isinstance(status, dict):
            return {}
        value = status.get(name)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _result(payload: Optional[Dict[str, Any]]) -> Any:
        if not isinstance(payload, dict):
            return None
        return payload.get("result", payload)

    # ------------------------------------------------------------------
    # Lifecycle / refresh
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshAll(self) -> None:
        if not self._monitoring_active:
            return
        self.refreshTransport()
        self.refreshCapabilities()
        self.refreshWebcams()
        self.refreshPowerDevices()
        self.refreshSystemInfo()

    @pyqtSlot()
    def refreshTransport(self) -> None:
        if not self._monitoring_active:
            self._core_timer.stop()
            self._cancel_channel("core")
            return
        self._ensure_request_session()
        config = self._follower.current_printer_config()
        if bool(config.enabled):
            self._core_timer.stop()
            self._cancel_channel("core")
            return
        if not self._usable_base_url():
            self._core_timer.stop()
            self._cancel_channel("core")
            return
        if not self._core_timer.isActive():
            self._core_timer.start()
        self._poll_core_fallback()

    def _refresh_core_now(self) -> None:
        config = self._follower.current_printer_config()
        if bool(config.enabled):
            client = getattr(self._follower, "_client", None)
            refresh = getattr(client, "force_refresh", None)
            if callable(refresh):
                refresh()
        else:
            self._poll_core_fallback()

    def _poll_core_fallback(self) -> None:
        config = self._follower.current_printer_config()
        if bool(config.enabled):
            self.refreshTransport()
            return
        self._json_request("core", "GET", status_endpoint(self._base_url), self._on_core_finished)

    def _on_core_finished(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        if error:
            self._monitor_state = "Disconnected"
            self._monitor_state_raw = ""
            self.monitorChanged.emit()
            self.actionChanged.emit()
            return
        result = self._result(payload)
        status = result.get("status") if isinstance(result, dict) else None
        if isinstance(status, dict):
            self.updateMoonrakerStatus(status)

    # ------------------------------------------------------------------
    # Core print status, ETA and controls
    # ------------------------------------------------------------------

    @pyqtSlot(object)
    def updateMoonrakerStatus(self, status: Any) -> None:
        if not self._monitoring_active or not isinstance(status, dict):
            return

        print_stats = self._status_object(status, "print_stats")
        virtual_sdcard = self._status_object(status, "virtual_sdcard")
        gcode_move = self._status_object(status, "gcode_move")
        motion_report = self._status_object(status, "motion_report")

        state = str(print_stats.get("state") or "unknown").strip().lower()
        self._monitor_state_raw = state
        self._monitor_state = state[:1].upper() + state[1:] if state else "Unknown"
        filename = str(print_stats.get("filename") or "")
        self._monitor_filename = filename
        self._monitor_message = str(print_stats.get("message") or "")

        try:
            progress = float(virtual_sdcard.get("progress") or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        progress = max(0.0, min(1.0, progress))
        self._monitor_progress_fraction = progress
        self._monitor_progress = max(0, min(100, int(round(progress * 100.0))))

        info = print_stats.get("info") or {}
        if isinstance(info, dict):
            current_layer = info.get("current_layer")
            total_layer = info.get("total_layer")
        else:
            current_layer = None
            total_layer = None
        if current_layer is not None and total_layer is not None:
            self._monitor_layer = f"{current_layer} / {total_layer}"
        elif current_layer is not None:
            self._monitor_layer = str(current_layer)
        else:
            self._monitor_layer = "—"

        try:
            elapsed = float(print_stats.get("print_duration") or 0.0)
        except (TypeError, ValueError):
            elapsed = 0.0
        self._print_duration = max(0.0, elapsed)
        self._monitor_elapsed = self._format_duration(self._print_duration)

        try:
            speed_factor = float(gcode_move.get("speed_factor") or 1.0)
        except (TypeError, ValueError):
            speed_factor = 1.0
        try:
            flow_factor = float(gcode_move.get("extrude_factor") or 1.0)
        except (TypeError, ValueError):
            flow_factor = 1.0
        self._monitor_speed = f"{int(round(speed_factor * 100.0))}%"
        self._monitor_flow = f"{int(round(flow_factor * 100.0))}%"

        position = motion_report.get("live_position") if isinstance(motion_report, dict) else None
        if isinstance(position, (list, tuple)) and len(position) >= 3:
            try:
                self._monitor_position = (
                    f"X {float(position[0]):.1f}   "
                    f"Y {float(position[1]):.1f}   "
                    f"Z {float(position[2]):.2f}"
                )
            except (TypeError, ValueError):
                self._monitor_position = "—"
        else:
            self._monitor_position = "—"

        if filename != self._metadata_filename:
            self._metadata_filename = filename
            self._metadata_estimated_time = None
            self._metadata_lookup_complete = not bool(filename)
            if filename:
                self._fetch_metadata(filename)

        self._update_eta()
        self._after_core_status(status)
        self.monitorChanged.emit()
        self.actionChanged.emit()
        if self._power_devices_raw:
            self.powerDevicesChanged.emit()

    def _after_core_status(self, _status: Any) -> None:
        """Subclass hook run after core fields are coherent, before UI notification."""

    def _fetch_metadata(self, filename: str) -> None:
        encoded = quote(filename, safe="/")
        started = self._json_request(
            "metadata",
            "GET",
            f"server/files/metadata?filename={encoded}",
            lambda payload, error, f=filename: self._on_metadata(f, payload, error),
            replace=True,
        )
        if not started and filename == self._monitor_filename:
            self._metadata_lookup_complete = True
            self._update_eta()
            self.monitorChanged.emit()

    def _on_metadata(
        self,
        filename: str,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if filename != self._monitor_filename:
            return
        self._metadata_lookup_complete = True
        if error:
            self._update_eta()
            self.monitorChanged.emit()
            return
        data = self._result(payload)
        if not isinstance(data, dict):
            self._update_eta()
            self.monitorChanged.emit()
            return
        try:
            estimate = float(data.get("estimated_time") or 0.0)
        except (TypeError, ValueError):
            estimate = 0.0
        self._metadata_estimated_time = estimate if estimate > 0 else None
        self._update_eta()
        self.monitorChanged.emit()

    @staticmethod
    def _estimate_remaining_seconds(
        print_duration: float,
        file_progress: float,
        slicer_estimated_time: Optional[float],
        metadata_lookup_complete: bool,
    ) -> Optional[float]:
        """Estimate remaining print time without treating G-code bytes as time.

        Moonraker's virtual_sdcard.progress is a byte-position fraction. Cura can
        emit very different amounts of G-code per unit of print time, so using
        elapsed/progress as the primary ETA can turn a seven-hour job into an
        absurd multi-day estimate. File progress is retained only as a fallback
        and as a small correction when it broadly agrees with slicer metadata.
        """
        try:
            elapsed = max(0.0, float(print_duration))
        except (TypeError, ValueError):
            elapsed = 0.0
        try:
            progress = max(0.0, min(1.0, float(file_progress)))
        except (TypeError, ValueError):
            progress = 0.0

        file_remaining: Optional[float] = None
        if progress >= 0.02 and elapsed >= 60.0:
            file_total = elapsed / progress
            file_remaining = max(0.0, file_total - elapsed)

        try:
            slicer_total = float(slicer_estimated_time or 0.0)
        except (TypeError, ValueError):
            slicer_total = 0.0

        if slicer_total > 0.0:
            slicer_remaining = max(0.0, slicer_total - elapsed)
            if slicer_remaining > 0.0:
                if file_remaining is not None:
                    file_total = elapsed + file_remaining
                    if 0.60 * slicer_total <= file_total <= 1.75 * slicer_total:
                        return 0.75 * slicer_remaining + 0.25 * file_remaining
                return slicer_remaining
            return file_remaining if file_remaining is not None else 0.0

        # Avoid flashing a bad byte-based ETA while the reliable metadata request
        # is still in flight. If metadata is unavailable, fall back gracefully.
        if not metadata_lookup_complete:
            return None
        return file_remaining

    def _update_eta(self) -> None:
        if self._monitor_state_raw == "paused":
            self._monitor_eta = "Paused"
            self._monitor_finish = "—"
            return
        if self._monitor_state_raw != "printing":
            self._monitor_eta = "—"
            self._monitor_finish = "—"
            return

        remaining = self._estimate_remaining_seconds(
            self._print_duration,
            self._monitor_progress_fraction,
            self._metadata_estimated_time,
            self._metadata_lookup_complete,
        )

        if remaining is None:
            self._monitor_eta = "—"
            self._monitor_finish = "—"
            return
        self._monitor_eta = self._format_duration(remaining)
        finish = datetime.now().astimezone() + timedelta(seconds=remaining)
        if remaining >= 20 * 3600:
            self._monitor_finish = finish.strftime("%a %H:%M")
        else:
            self._monitor_finish = finish.strftime("%H:%M")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @pyqtProperty(str, notify=monitorChanged)
    def monitorState(self) -> str:
        return self._monitor_state

    @pyqtProperty(str, notify=monitorChanged)
    def monitorFilename(self) -> str:
        return self._monitor_filename

    @pyqtProperty(int, notify=monitorChanged)
    def monitorProgress(self) -> int:
        return self._monitor_progress

    @pyqtProperty(str, notify=monitorChanged)
    def monitorLayer(self) -> str:
        return self._monitor_layer

    @pyqtProperty(str, notify=monitorChanged)
    def monitorElapsed(self) -> str:
        return self._monitor_elapsed

    @pyqtProperty(str, notify=monitorChanged)
    def monitorEta(self) -> str:
        return self._monitor_eta

    @pyqtProperty(str, notify=monitorChanged)
    def monitorFinish(self) -> str:
        return self._monitor_finish

    @pyqtProperty(str, notify=monitorChanged)
    def monitorSpeed(self) -> str:
        return self._monitor_speed

    @pyqtProperty(str, notify=monitorChanged)
    def monitorFlow(self) -> str:
        return self._monitor_flow

    @pyqtProperty(str, notify=monitorChanged)
    def monitorPosition(self) -> str:
        return self._monitor_position

    @pyqtProperty(str, notify=monitorChanged)
    def monitorMessage(self) -> str:
        return self._monitor_message

    @pyqtProperty(bool, notify=actionChanged)
    def printActive(self) -> bool:
        return self._monitor_state_raw in {"printing", "paused"}

    @pyqtProperty(bool, notify=actionChanged)
    def canPausePrint(self) -> bool:
        return self._monitor_state_raw == "printing" and not self._action_busy

    @pyqtProperty(bool, notify=actionChanged)
    def canResumePrint(self) -> bool:
        return self._monitor_state_raw == "paused" and not self._action_busy

    @pyqtProperty(bool, notify=actionChanged)
    def canCancelPrint(self) -> bool:
        return self._monitor_state_raw in {"printing", "paused"} and not self._action_busy

    @pyqtProperty(bool, notify=actionChanged)
    def actionBusy(self) -> bool:
        return self._action_busy

    @pyqtProperty(str, notify=actionChanged)
    def actionStatus(self) -> str:
        return self._action_status

    @pyqtSlot()
    def pausePrint(self) -> None:
        if self.canPausePrint:
            self._send_print_action("Pause", "printer/print/pause")

    @pyqtSlot()
    def resumePrint(self) -> None:
        if self.canResumePrint:
            self._send_print_action("Resume", "printer/print/resume")

    @pyqtSlot()
    def cancelPrint(self) -> None:
        if self.canCancelPrint:
            self._send_print_action("Cancel", "printer/print/cancel")

    def _send_print_action(self, label: str, path: str) -> None:
        if self._action_busy:
            return
        self._action_busy = True
        self._action_status = f"{label} requested…"
        self.actionChanged.emit()
        started = self._json_request(
            "control",
            "POST",
            path,
            lambda payload, error, l=label: self._on_control_finished(l, payload, error),
        )
        if not started:
            self._action_busy = False
            self._action_status = "Moonraker is not available"
            self.actionChanged.emit()

    def _on_control_finished(
        self,
        label: str,
        _payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        self._action_busy = False
        if error:
            self._action_status = f"{label} failed: {error}"
        else:
            self._action_status = f"{label} accepted"
            QTimer.singleShot(150, self._refresh_core_now)
        self.actionChanged.emit()

    # ------------------------------------------------------------------
    # Dynamic Klipper object discovery and peripheral status
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshCapabilities(self) -> None:
        self._json_request("objects", "GET", "printer/objects/list", self._on_objects_list)

    def _on_objects_list(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        objects = result.get("objects") if isinstance(result, dict) else None
        if not isinstance(objects, list):
            return
        self._available_objects = sorted(str(item) for item in objects)
        self._aux_objects = [name for name in self._available_objects if self._want_aux_object(name)]
        self._poll_config_snapshot()
        self._poll_aux_status()

    @staticmethod
    def _want_aux_object(name: str) -> bool:
        lower = str(name or "").lower()
        if lower in {"heater_bed", "fan", "exclude_object", "system_stats", "webhooks", "mcu"}:
            return True
        if re.fullmatch(r"extruder\d*", lower):
            return True
        prefixes = (
            "heater_generic ",
            "temperature_sensor ",
            "temperature_fan ",
            "temperature_host ",
            "temperature_combined ",
            "bme280 ",
            "htu21d ",
            "sht3x ",
            "lm75 ",
            "fan_generic ",
            "heater_fan ",
            "controller_fan ",
            "filament_switch_sensor ",
            "filament_motion_sensor ",
            "mcu ",
        )
        return lower.startswith(prefixes)

    @staticmethod
    def _aux_query_fields(name: str):
        # configfile.config/settings can be very large. Only the two volatile
        # SAVE_CONFIG fields belong in the one-second poll; the full config is
        # refreshed with capability discovery instead.
        if str(name or "").lower() == "configfile":
            return ["save_config_pending", "save_config_pending_items"]
        return None

    @staticmethod
    def _merge_aux_status(current: Any, incoming: Any) -> Dict[str, Any]:
        merged: Dict[str, Any] = dict(current) if isinstance(current, dict) else {}
        if not isinstance(incoming, dict):
            return merged
        for name, value in incoming.items():
            previous = merged.get(name)
            if isinstance(previous, dict) and isinstance(value, dict):
                combined = dict(previous)
                combined.update(value)
                merged[name] = combined
            else:
                merged[name] = value
        return merged

    def _poll_config_snapshot(self) -> None:
        if not any(str(name).lower() == "configfile" for name in self._aux_objects):
            return
        body = {"objects": {"configfile": None}}
        self._json_request(
            "config-static",
            "POST",
            "printer/objects/query",
            self._on_config_snapshot,
            body=body,
            replace=True,
        )

    def _on_config_snapshot(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            return
        self._aux_status = self._merge_aux_status(self._aux_status, status)
        self._rebuild_peripherals()

    def _poll_aux_status(self) -> None:
        if not self._aux_objects:
            return
        body = {"objects": {name: self._aux_query_fields(name) for name in self._aux_objects}}
        self._json_request("aux", "POST", "printer/objects/query", self._on_aux_status, body=body)

    def _on_aux_status(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            return
        self._aux_status = self._merge_aux_status(self._aux_status, status)
        self._rebuild_peripherals()

    @staticmethod
    def _friendly_object_name(name: str) -> str:
        lower = name.lower()
        if lower == "extruder":
            return "Hotend"
        match = re.fullmatch(r"extruder(\d+)", lower)
        if match:
            return f"Hotend {int(match.group(1)) + 1}"
        if lower == "heater_bed":
            return "Bed"
        if lower == "fan":
            return "Part fan"
        prefixes = (
            "heater_generic ", "temperature_sensor ", "temperature_fan ",
            "temperature_host ", "temperature_combined ", "bme280 ",
            "htu21d ", "sht3x ", "lm75 ", "fan_generic ",
            "heater_fan ", "controller_fan ", "filament_switch_sensor ",
            "filament_motion_sensor ", "mcu ",
        )
        for prefix in prefixes:
            if lower.startswith(prefix):
                suffix = name[len(prefix):].replace("_", " ").strip()
                return suffix[:1].upper() + suffix[1:] if suffix else name
        return name.replace("_", " ").strip().title()

    def _rebuild_peripherals(self) -> None:
        temperatures: List[Dict[str, Any]] = []
        fans: List[Dict[str, Any]] = []
        filament: List[Dict[str, Any]] = []
        cpu_temp: Optional[float] = None
        mcu_versions: List[str] = []

        for name in sorted(self._aux_status.keys()):
            value = self._aux_status.get(name)
            if not isinstance(value, dict):
                continue
            lower = name.lower()
            friendly = self._friendly_object_name(name)

            if "temperature" in value:
                try:
                    current = float(value.get("temperature"))
                except (TypeError, ValueError):
                    current = None
                if current is not None:
                    try:
                        target = float(value.get("target")) if value.get("target") is not None else None
                    except (TypeError, ValueError):
                        target = None
                    try:
                        heater_power = float(value.get("power")) if value.get("power") is not None else None
                    except (TypeError, ValueError):
                        heater_power = None
                    detail = f"{current:.1f} °C"
                    if target is not None:
                        detail += f"  → {target:.0f} °C"
                    if heater_power is not None:
                        detail += f"  · {heater_power * 100:.0f}%"
                    temperatures.append({
                        "name": friendly,
                        "temperature": current,
                        "target": target if target is not None else -1,
                        "power": heater_power if heater_power is not None else -1,
                        "detail": detail,
                    })
                    if cpu_temp is None and (
                        lower.startswith("temperature_host ")
                        or "cpu" in lower
                        or "rpi" in lower
                    ):
                        cpu_temp = current

            if "speed" in value and (
                lower == "fan"
                or lower.startswith(("fan_generic ", "heater_fan ", "controller_fan ", "temperature_fan "))
            ):
                try:
                    speed = max(0.0, min(1.0, float(value.get("speed") or 0.0)))
                except (TypeError, ValueError):
                    speed = 0.0
                rpm = value.get("rpm")
                detail = f"{speed * 100:.0f}%"
                try:
                    if rpm is not None:
                        detail += f"  · {int(rpm):,} RPM"
                except (TypeError, ValueError):
                    pass
                fans.append({"name": friendly, "speed": speed, "detail": detail})

            if lower.startswith(("filament_switch_sensor ", "filament_motion_sensor ")):
                detected = bool(value.get("filament_detected", False))
                enabled = bool(value.get("enabled", True))
                state = "Disabled" if not enabled else ("Filament detected" if detected else "Runout / not detected")
                filament.append({
                    "name": friendly,
                    "detected": detected,
                    "enabled": enabled,
                    "state": state,
                })

            if lower == "mcu" or lower.startswith("mcu "):
                version = str(value.get("mcu_version") or "").strip()
                if version:
                    mcu_versions.append(f"{friendly}: {version}")

        exclude = self._aux_status.get("exclude_object") or {}
        exclude_items: List[Dict[str, Any]] = []
        if isinstance(exclude, dict):
            raw_objects = exclude.get("objects") or []
            excluded = {str(item) for item in (exclude.get("excluded_objects") or [])}
            current = str(exclude.get("current_object") or "")
            if isinstance(raw_objects, list):
                for item in raw_objects:
                    if not isinstance(item, dict):
                        continue
                    object_name = str(item.get("name") or "")
                    if not object_name:
                        continue
                    exclude_items.append({
                        "name": object_name,
                        "excluded": object_name in excluded,
                        "current": object_name == current,
                    })

        system_stats = self._aux_status.get("system_stats") or {}
        if isinstance(system_stats, dict):
            try:
                self._host_load = f"{float(system_stats.get('sysload')):.2f}"
            except (TypeError, ValueError):
                self._host_load = "—"
            try:
                mem_kb = float(system_stats.get("memavail") or 0.0)
                if mem_kb >= 1024 * 1024:
                    self._memory_available = f"{mem_kb / (1024 * 1024):.2f} GB"
                elif mem_kb > 0:
                    self._memory_available = f"{mem_kb / 1024:.0f} MB"
                else:
                    self._memory_available = "—"
            except (TypeError, ValueError):
                self._memory_available = "—"

        webhooks = self._aux_status.get("webhooks") or {}
        if isinstance(webhooks, dict) and webhooks.get("state"):
            self._klippy_state = str(webhooks.get("state"))

        self._temperature_items = temperatures
        self._fan_items = fans
        self._filament_items = filament
        self._exclude_items = exclude_items
        self._cpu_temperature = f"{cpu_temp:.1f} °C" if cpu_temp is not None else "—"
        self._mcu_summary = " · ".join(mcu_versions) if mcu_versions else "—"

        self.peripheralsChanged.emit()
        self.excludeObjectsChanged.emit()
        self.systemChanged.emit()

    @pyqtProperty(QVariant, notify=peripheralsChanged)
    def temperatureItems(self) -> QVariant:
        return QVariant(self._temperature_items)

    @pyqtProperty(QVariant, notify=peripheralsChanged)
    def fanItems(self) -> QVariant:
        return QVariant(self._fan_items)

    @pyqtProperty(QVariant, notify=peripheralsChanged)
    def filamentSensorItems(self) -> QVariant:
        return QVariant(self._filament_items)

    @pyqtProperty(QVariant, notify=excludeObjectsChanged)
    def excludeObjectItems(self) -> QVariant:
        return QVariant(self._exclude_items)

    @pyqtSlot(str)
    def excludeObject(self, name: str) -> None:
        name = str(name or "")
        if not name or not self.printActive or self._action_busy:
            return
        known = next((item for item in self._exclude_items if item.get("name") == name), None)
        if not known or bool(known.get("excluded")):
            return
        safe = name.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
        script = f'EXCLUDE_OBJECT NAME="{safe}"'
        self._action_busy = True
        self._action_status = f"Excluding {name}…"
        self.actionChanged.emit()
        self._json_request(
            "control",
            "POST",
            "printer/gcode/script",
            lambda payload, error, n=name: self._on_control_finished(f"Exclude {n}", payload, error),
            body={"script": script},
        )

    # ------------------------------------------------------------------
    # Moonraker power devices
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshPowerDevices(self) -> None:
        self._json_request("power-list", "GET", "machine/device_power/devices", self._on_power_devices)

    def _on_power_devices(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        devices = result.get("devices") if isinstance(result, dict) else None
        if isinstance(devices, list):
            self._power_devices_raw = [item for item in devices if isinstance(item, dict)]
            self.powerDevicesChanged.emit()

    def _visible_power_devices(self) -> List[Dict[str, Any]]:
        config = self._follower.current_printer_config()
        configured = [item.strip() for item in str(config.power_devices or "").split(",") if item.strip()]
        by_name = {str(item.get("device") or ""): item for item in self._power_devices_raw}
        source: List[Dict[str, Any]]
        if configured:
            source = [by_name.get(name, {"device": name, "status": "unknown", "locked_while_printing": False}) for name in configured]
        else:
            source = list(self._power_devices_raw)

        active = self.printActive
        result: List[Dict[str, Any]] = []
        for item in source:
            name = str(item.get("device") or "")
            if not name:
                continue
            status = str(item.get("status") or "unknown").lower()
            locked = bool(item.get("locked_while_printing", False))
            result.append({
                "name": name,
                "status": status,
                "locked": locked,
                "can_toggle": not (locked and active),
            })
        return result

    @pyqtProperty(QVariant, notify=powerDevicesChanged)
    def powerDevices(self) -> QVariant:
        return QVariant(self._visible_power_devices())

    @pyqtSlot(str, bool)
    def setPowerDevice(self, name: str, turn_on: bool) -> None:
        name = str(name or "").strip()
        if not name or self._action_busy:
            return
        visible = next((item for item in self._visible_power_devices() if item.get("name") == name), None)
        if not visible or not bool(visible.get("can_toggle")):
            return
        action = "on" if bool(turn_on) else "off"
        self._action_busy = True
        self._action_status = f"Turning {name} {action}…"
        self.actionChanged.emit()
        self._json_request(
            "power-action",
            "POST",
            "machine/device_power/device",
            lambda payload, error, n=name, a=action: self._on_power_action(n, a, payload, error),
            body={"device": name, "action": action},
            replace=True,
        )

    def _on_power_action(
        self,
        name: str,
        action: str,
        _payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        self._action_busy = False
        if error:
            self._action_status = f"Power action failed: {error}"
        else:
            self._action_status = f"{name} turned {action}"
            QTimer.singleShot(250, self.refreshPowerDevices)
            QTimer.singleShot(500, self.refreshSystemInfo)
        self.actionChanged.emit()

    # ------------------------------------------------------------------
    # System / firmware health
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshSystemInfo(self) -> None:
        self._json_request("server-info", "GET", "server/info", self._on_server_info)
        self._json_request("printer-info", "GET", "printer/info", self._on_printer_info)

    def _on_server_info(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        if isinstance(result, dict):
            self._server_info = result
            if result.get("klippy_state"):
                self._klippy_state = str(result.get("klippy_state"))
            self.systemChanged.emit()

    def _on_printer_info(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        if isinstance(result, dict):
            self._printer_info = result
            self.systemChanged.emit()

    @pyqtProperty(str, notify=systemChanged)
    def klippyState(self) -> str:
        return self._klippy_state[:1].upper() + self._klippy_state[1:] if self._klippy_state else "Unknown"

    @pyqtProperty(str, notify=systemChanged)
    def moonrakerVersion(self) -> str:
        return str(self._server_info.get("moonraker_version") or "—")

    @pyqtProperty(str, notify=systemChanged)
    def klipperVersion(self) -> str:
        return str(self._printer_info.get("software_version") or "—")

    @pyqtProperty(str, notify=systemChanged)
    def hostLoad(self) -> str:
        return self._host_load

    @pyqtProperty(str, notify=systemChanged)
    def memoryAvailable(self) -> str:
        return self._memory_available

    @pyqtProperty(str, notify=systemChanged)
    def cpuTemperature(self) -> str:
        return self._cpu_temperature

    @pyqtProperty(str, notify=systemChanged)
    def mcuSummary(self) -> str:
        return self._mcu_summary

    # ------------------------------------------------------------------
    # Moonraker webcam discovery
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshWebcams(self) -> None:
        self.refreshTransport()
        if not self._usable_base_url():
            self._use_fallback_camera()
            return
        self._json_request("webcams", "GET", "server/webcams/list", self._on_webcams_finished, replace=True)

    def _on_webcams_finished(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        cameras: List[Dict[str, Any]] = []
        if not error:
            result = self._result(payload)
            raw_cameras = result.get("webcams") if isinstance(result, dict) else None
            if isinstance(raw_cameras, list):
                cameras = [
                    item for item in raw_cameras
                    if isinstance(item, dict) and bool(item.get("enabled", True))
                ]

        self._webcams = cameras
        if self._webcams:
            self._active_webcam_index = min(max(self._active_webcam_index, 0), len(self._webcams) - 1)
            self._apply_webcam(self._active_webcam_index)
        else:
            self._active_webcam_index = -1
            self._use_fallback_camera()
        self.webcamsChanged.emit()

    def _use_fallback_camera(self) -> None:
        config = self._follower.current_printer_config()
        url = self._resolve_camera_url(str(config.camera_url or ""))
        self._camera_name = "Configured camera" if url else ""
        self._camera_rotation = int(config.camera_rotation or 0)
        self._camera_flip_horizontal = bool(config.camera_mirror)
        self._camera_flip_vertical = False
        try:
            self.setCameraUrl(QUrl(url) if url else QUrl())
        except Exception:
            pass
        self.cameraTransformChanged.emit()
        self.webcamsChanged.emit()

    def _apply_webcam(self, index: int) -> None:
        if index < 0 or index >= len(self._webcams):
            self._use_fallback_camera()
            return
        webcam = self._webcams[index]
        self._active_webcam_index = index
        self._camera_name = str(webcam.get("name") or f"Camera {index + 1}")
        stream_url = self._resolve_camera_url(str(webcam.get("stream_url") or ""))
        try:
            rotation = int(webcam.get("rotation") or 0)
        except (TypeError, ValueError):
            rotation = 0
        self._camera_rotation = rotation if rotation in {0, 90, 180, 270} else 0
        self._camera_flip_horizontal = bool(webcam.get("flip_horizontal", False))
        self._camera_flip_vertical = bool(webcam.get("flip_vertical", False))
        try:
            self.setCameraUrl(QUrl(stream_url) if stream_url else QUrl())
        except Exception:
            pass
        self.cameraTransformChanged.emit()
        self.webcamsChanged.emit()

    def _resolve_camera_url(self, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        url = QUrl(value)
        if url.isValid() and url.scheme() in ("http", "https") and url.host():
            return value
        return urljoin(self._base_url.rstrip("/") + "/", value)

    @pyqtProperty(QVariant, notify=webcamsChanged)
    def webcamNames(self) -> QVariant:
        return QVariant([
            str(camera.get("name") or f"Camera {index + 1}")
            for index, camera in enumerate(self._webcams)
        ])

    @pyqtProperty(int, notify=webcamsChanged)
    def activeWebcamIndex(self) -> int:
        return self._active_webcam_index

    @pyqtSlot(int)
    def selectWebcam(self, index: int) -> None:
        try:
            index = int(index)
        except (TypeError, ValueError):
            return
        if 0 <= index < len(self._webcams):
            self._apply_webcam(index)

    @pyqtProperty(str, notify=cameraTransformChanged)
    def cameraName(self) -> str:
        return self._camera_name

    @pyqtProperty(int, notify=cameraTransformChanged)
    def cameraRotation(self) -> int:
        return self._camera_rotation

    @pyqtProperty(bool, notify=cameraTransformChanged)
    def cameraFlipHorizontal(self) -> bool:
        return self._camera_flip_horizontal

    @pyqtProperty(bool, notify=cameraTransformChanged)
    def cameraFlipVertical(self) -> bool:
        return self._camera_flip_vertical

    @pyqtSlot()
    def openFrontend(self) -> None:
        config = self._follower.current_printer_config()
        target = str(config.frontend_url or config.url or "").strip()
        if target:
            QDesktopServices.openUrl(QUrl(target))
