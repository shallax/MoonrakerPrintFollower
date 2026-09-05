from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from PyQt6.QtCore import QTimer, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from UM.Logger import Logger

from .MoonrakerProtocol import status_endpoint


class MoonrakerMonitorModel(PrinterOutputModel):
    """Live Cura Monitor model for the unified Moonraker integration.

    When Preview following is enabled, Monitor consumes the follower's existing
    Moonraker status signal and adds no second status request stream. When
    automatic following is disabled, Monitor owns a lightweight 1-second polling
    fallback so upload/Monitor remain connected independently of Preview-follow
    preference state. Webcam configuration is discovered through Moonraker's
    webcam API because it changes rarely and is not a Klipper printer object.
    """

    monitorChanged = pyqtSignal()
    webcamsChanged = pyqtSignal()
    cameraTransformChanged = pyqtSignal()

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        super().__init__(output_controller, number_of_extruders)
        self._follower = follower
        self._network = QNetworkAccessManager(self)
        self._status_network = QNetworkAccessManager(self)
        self._webcam_reply: Optional[QNetworkReply] = None
        self._status_reply: Optional[QNetworkReply] = None
        self._webcams: List[Dict[str, Any]] = []
        self._active_webcam_index = -1

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._poll_status_fallback)

        self._camera_name = ""
        self._camera_rotation = 0
        self._camera_flip_horizontal = False
        self._camera_flip_vertical = False

        self._monitor_state = "Not connected"
        self._monitor_filename = ""
        self._monitor_progress = 0
        self._monitor_layer = "—"
        self._monitor_elapsed = "00:00:00"
        self._monitor_speed = "100%"
        self._monitor_flow = "100%"
        self._monitor_position = "—"

        client = getattr(follower, "_client", None)
        status_signal = getattr(client, "statusReceived", None)
        if status_signal is not None:
            try:
                status_signal.connect(self.updateMoonrakerStatus)
            except Exception as exc:
                Logger.log("w", "Moonraker Print Follower: could not bind Monitor status: %s", exc)

        self.refreshTransport()
        self.refreshWebcams()

    # ------------------------------------------------------------------
    # Print status
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshTransport(self) -> None:
        """Use exactly one status stream for the active printer.

        The follower already polls when automatic following is enabled. If the
        user disables following, Monitor takes over status polling so the unified
        connection remains useful without causing duplicate Moonraker traffic.
        """
        config = self._follower.current_printer_config()
        if bool(config.enabled):
            self._status_timer.stop()
            self._abort_status_reply()
            return

        base_url = str(config.url or "").strip().rstrip("/")
        parsed = QUrl(base_url)
        if not parsed.isValid() or parsed.scheme() not in ("http", "https") or not parsed.host():
            self._status_timer.stop()
            self._abort_status_reply()
            return

        if not self._status_timer.isActive():
            self._status_timer.start()
        self._poll_status_fallback()

    def _abort_status_reply(self) -> None:
        reply = self._status_reply
        self._status_reply = None
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

    def _poll_status_fallback(self) -> None:
        config = self._follower.current_printer_config()
        if bool(config.enabled):
            self.refreshTransport()
            return
        if self._status_reply is not None:
            try:
                if self._status_reply.isRunning():
                    return
            except Exception:
                pass

        base_url = str(config.url or "").strip().rstrip("/")
        parsed = QUrl(base_url)
        if not parsed.isValid() or parsed.scheme() not in ("http", "https") or not parsed.host():
            return

        request = QNetworkRequest(QUrl(status_endpoint(base_url)))
        request.setRawHeader(b"Accept", b"application/json")
        if config.api_key:
            request.setRawHeader(b"X-Api-Key", str(config.api_key).encode("utf-8"))
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        reply = self._status_network.get(request)
        self._status_reply = reply
        reply.finished.connect(lambda r=reply: self._on_status_fallback_finished(r))

    def _on_status_fallback_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._status_reply:
            reply.deleteLater()
            return
        self._status_reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._monitor_state = "Disconnected"
                self.monitorChanged.emit()
                return
            payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
            result = payload.get("result") or {}
            status = result.get("status") if isinstance(result, dict) else None
            if isinstance(status, dict):
                self.updateMoonrakerStatus(status)
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: Monitor status polling failed: %s", exc)
        finally:
            reply.deleteLater()

    @pyqtSlot(object)
    def updateMoonrakerStatus(self, status: Any) -> None:
        if not isinstance(status, dict):
            return

        print_stats = status.get("print_stats") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        gcode_move = status.get("gcode_move") or {}
        motion_report = status.get("motion_report") or {}

        state = str(print_stats.get("state") or "unknown").strip()
        self._monitor_state = state[:1].upper() + state[1:] if state else "Unknown"
        self._monitor_filename = str(print_stats.get("filename") or "")

        try:
            progress = float(virtual_sdcard.get("progress") or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
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
        self._monitor_elapsed = self._format_duration(elapsed)

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

        self.monitorChanged.emit()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
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
    def monitorSpeed(self) -> str:
        return self._monitor_speed

    @pyqtProperty(str, notify=monitorChanged)
    def monitorFlow(self) -> str:
        return self._monitor_flow

    @pyqtProperty(str, notify=monitorChanged)
    def monitorPosition(self) -> str:
        return self._monitor_position

    # ------------------------------------------------------------------
    # Moonraker webcam discovery
    # ------------------------------------------------------------------

    @pyqtSlot()
    def refreshWebcams(self) -> None:
        self.refreshTransport()
        config = self._follower.current_printer_config()
        base_url = str(config.url or "").strip().rstrip("/")
        parsed = QUrl(base_url)
        if not parsed.isValid() or parsed.scheme() not in ("http", "https") or not parsed.host():
            self._use_fallback_camera()
            return

        if self._webcam_reply is not None:
            try:
                if self._webcam_reply.isRunning():
                    self._webcam_reply.abort()
            except Exception:
                pass
            try:
                self._webcam_reply.deleteLater()
            except Exception:
                pass
            self._webcam_reply = None

        request = QNetworkRequest(QUrl(base_url + "/server/webcams/list"))
        request.setRawHeader(b"Accept", b"application/json")
        if config.api_key:
            request.setRawHeader(b"X-Api-Key", str(config.api_key).encode("utf-8"))
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        reply = self._network.get(request)
        self._webcam_reply = reply
        reply.finished.connect(lambda r=reply: self._on_webcams_finished(r))

    def _on_webcams_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._webcam_reply:
            reply.deleteLater()
            return
        self._webcam_reply = None

        cameras: List[Dict[str, Any]] = []
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
                result = payload.get("result") or {}
                raw_cameras = result.get("webcams") if isinstance(result, dict) else None
                if isinstance(raw_cameras, list):
                    cameras = [
                        item for item in raw_cameras
                        if isinstance(item, dict) and bool(item.get("enabled", True))
                    ]
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: webcam discovery failed: %s", exc)
        finally:
            reply.deleteLater()

        self._webcams = cameras
        if self._webcams:
            self._active_webcam_index = min(
                max(self._active_webcam_index, 0), len(self._webcams) - 1
            )
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
        config = self._follower.current_printer_config()
        base = str(config.url or "").strip().rstrip("/") + "/"
        return urljoin(base, value)

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
