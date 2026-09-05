from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from io import BytesIO, StringIO
from typing import Any, Callable, Dict, Optional, cast
from urllib.parse import urlencode

from PyQt6.QtCore import QByteArray, QTimer, QUrl, QVariant, pyqtProperty, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import (
    QHttpMultiPart,
    QHttpPart,
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from cura.PrinterOutput.PrinterOutputController import PrinterOutputController
from cura.PrinterOutput.PrinterOutputDevice import ConnectionType, PrinterOutputDevice
from UM.Logger import Logger
from UM.Mesh.MeshWriter import MeshWriter
from UM.Message import Message
from UM.OutputDevice import OutputDeviceError

from .PrinterConfig import PrinterConfig


class MoonrakerOutputController(PrinterOutputController):
    """Capabilities exposed to Cura for the integrated Moonraker device."""

    def __init__(self, output_device: PrinterOutputDevice) -> None:
        super().__init__(output_device)
        # Upload/start-print is implemented here. These flags are deliberately
        # conservative: Cura must not display controls that are not wired to a
        # Moonraker command yet.
        self.can_pause = False
        self.can_abort = False
        self.can_pre_heat_bed = False
        self.can_pre_heat_hotends = False
        self.can_send_raw_gcode = False
        self.can_control_manually = False
        self.can_update_firmware = False


class MoonrakerOutputDevice(PrinterOutputDevice):
    """Cura output device backed by Moonraker's file and power APIs.

    This replaces the separate Moonraker Connection plugin for the normal Cura
    workflow: write G-code/UFP, optionally choose a remote path/name, optionally
    power the printer, wait for Klippy to become ready without blocking Cura,
    upload to Moonraker and optionally start the print immediately.
    """

    DEVICE_PREFIX = "MoonrakerPrintFollower@"
    MAX_READY_ATTEMPTS = 21

    def __init__(self, application: Any, follower: Any, machine_id: str) -> None:
        super().__init__(
            device_id=self.DEVICE_PREFIX + str(machine_id),
            connection_type=ConnectionType.NetworkConnection,
        )
        self._application = application
        self._follower = follower
        self._machine_id = str(machine_id)
        self._network = QNetworkAccessManager(self)

        self._config = PrinterConfig()
        self._stream: Optional[Any] = None
        self._output_format = "gcode"
        self._file_name = ""
        self._path_name = ""
        self._start_print = False
        self._busy = False
        self._ready_attempts = 0
        self._power_devices: list[str] = []
        self._power_index = 0
        self._active_reply: Optional[QNetworkReply] = None
        self._upload_reply: Optional[QNetworkReply] = None
        self._upload_multipart: Optional[QHttpMultiPart] = None
        self._dialog = None
        self._message: Optional[Message] = None

        stack = application.getGlobalContainerStack()
        extruders = 1
        if stack is not None:
            try:
                extruders = int(stack.getProperty("machine_extruder_count", "value") or 1)
            except Exception:
                extruders = 1
        model = PrinterOutputModel(MoonrakerOutputController(self), extruders)
        if stack is not None:
            try:
                model.updateName(stack.getName())
                model.updateUniqueName(stack.getId())
                model.updateBuildplate(stack.getProperty("machine_buildplate_type", "value"))
            except Exception:
                pass
        self._printers = [model]
        self.updateConfig(follower.current_printer_config())

    # ------------------------------------------------------------------
    # Cura output-device presentation
    # ------------------------------------------------------------------

    def updateConfig(self, config: PrinterConfig) -> None:
        self._config = PrinterConfig.from_dict(asdict(config))
        stack = self._application.getGlobalContainerStack()
        name = stack.getName() if stack is not None else "Moonraker"
        self.setName(name)
        description = f"Upload to {name}"
        self.setDescription(description)
        self.setShortDescription(description)
        self.setIconName("print")
        self.setConnectionText("Connected via Moonraker")
        self.setPriority(5)

    @property
    def _base_url(self) -> str:
        return str(self._config.url or "").strip().rstrip("/")

    @pyqtProperty(str, constant=False)
    def initialUploadPath(self) -> str:
        return self._path_name

    @pyqtProperty(str, constant=False)
    def initialUploadFilename(self) -> str:
        return self._file_name

    @pyqtProperty(bool, constant=False)
    def initialStartPrint(self) -> bool:
        return bool(self._start_print)

    @pyqtProperty(QVariant, constant=False)
    def uploadPathOptions(self) -> QVariant:
        options = [""]
        for path in self._config.upload_paths:
            normalised = self._normalise_remote_path(path)
            if normalised and normalised not in options:
                options.append(normalised)
        current = self._normalise_remote_path(self._config.upload_path)
        if current and current not in options:
            options.append(current)
        return QVariant(sorted(options))

    # ------------------------------------------------------------------
    # Cura write lifecycle
    # ------------------------------------------------------------------

    def requestWrite(self, node: Any, fileName: str = None, *args: Any, **kwargs: Any) -> None:
        if self._busy:
            raise OutputDeviceError.DeviceBusyError()
        if not self._usable_url(self._base_url):
            self._fail("Configure a valid Moonraker URL in Manage Printers before uploading.")
            return

        self.updateConfig(self._follower.current_printer_config())
        self._busy = True
        self.writeStarted.emit(self)

        try:
            print_info = self._application.getPrintInformation()
            registry = self._application.getPluginRegistry()
            requested_format = self._config.output_format
            if requested_format == "ufp" and print_info is not None and not print_info.preSliced:
                self._output_format = "ufp"
                writer = cast(MeshWriter, registry.getPluginObject("UFPWriter"))
                self._stream = BytesIO()
            else:
                self._output_format = "gcode"
                writer = cast(MeshWriter, registry.getPluginObject("GCodeWriter"))
                self._stream = StringIO()

            if writer is None or not writer.write(self._stream, None):
                detail = writer.getInformation() if writer is not None else "writer unavailable"
                self._fail(f"Cura could not prepare the file for Moonraker: {detail}")
                return

            default_name = "print"
            if print_info is not None:
                default_name = str(getattr(print_info, "jobName", "") or default_name)
            raw_name = os.path.basename(str(fileName or default_name))
            for suffix in (".gcode", ".ufp"):
                if raw_name.lower().endswith(suffix):
                    raw_name = raw_name[: -len(suffix)]
                    break
            raw_name = self._translate_filename(raw_name).strip() or "print"

            self._file_name = f"{raw_name}.{self._output_format}"
            self._path_name = self._normalise_remote_path(self._config.upload_path)
            self._start_print = bool(self._config.upload_start_print)

            if self._config.upload_dialog:
                self._show_upload_dialog()
            else:
                self._begin_upload()
        except Exception as exc:
            Logger.log("e", "Moonraker Print Follower: output preparation failed: %s", exc)
            self._fail(f"Could not prepare the Moonraker upload: {exc}")

    @pyqtSlot(str, str, bool)
    def acceptUpload(self, path: str, filename: str, start_print: bool) -> None:
        if not self._busy:
            return
        path = self._normalise_remote_path(path)
        filename = self._normalise_filename(filename)
        if not filename:
            self._fail("The upload filename is empty or invalid.")
            return
        if "." not in filename:
            filename += "." + self._output_format
        self._path_name = path
        self._file_name = filename
        self._start_print = bool(start_print)
        self._remember_upload_choices(path, self._start_print)
        self._dialog = None
        self._begin_upload()

    @pyqtSlot()
    def cancelUpload(self) -> None:
        if not self._busy:
            return
        self._dialog = None
        self._cleanup()
        # Cura expects a terminal write signal once writeStarted was emitted.
        self.writeError.emit(self)

    def _show_upload_dialog(self) -> None:
        qml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MoonrakerUploadDialog.qml")
        try:
            self._dialog = self._application.createQmlComponent(qml_path, {"manager": self})
            self._dialog.show()
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: upload dialog unavailable: %s", exc)
            # A UI failure should not lose the print; use the saved defaults.
            self._dialog = None
            self._begin_upload()

    # ------------------------------------------------------------------
    # Upload orchestration
    # ------------------------------------------------------------------

    def _begin_upload(self) -> None:
        self._ready_attempts = 0
        self._power_devices = [
            item.strip() for item in str(self._config.power_devices or "").split(",") if item.strip()
        ]
        self._power_index = 0
        self._show_status("Moonraker", f"Connecting to {self._base_url}…")

        if self._start_print and self._power_devices:
            device = self._power_devices[0]
            self._json_request(
                "GET",
                "machine/device_power/device?" + urlencode({"device": device}),
                self._on_power_status,
            )
        elif self._start_print:
            self._wait_for_ready()
        else:
            # Upload-only mode does not require Klippy to be ready. Moonraker's
            # file service remains useful while the printer MCU is disconnected.
            self._upload_now()

    def _on_power_status(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        if error:
            self._fail(f"Could not query the Moonraker power device: {error}")
            return
        result = (payload or {}).get("result") or {}
        state = ""
        if isinstance(result, dict) and result:
            state = str(next(iter(result.values()))).lower()
        if state == "off":
            self._power_index = 0
            self._turn_on_next_power_device()
        else:
            self._wait_for_ready()

    def _turn_on_next_power_device(self) -> None:
        if self._power_index >= len(self._power_devices):
            self._wait_for_ready()
            return
        device = self._power_devices[self._power_index]
        self._power_index += 1
        self._json_request(
            "POST",
            "machine/device_power/device?" + urlencode({"device": device, "action": "on"}),
            lambda payload, error: self._on_power_on(device, error),
            body=b"{}",
        )

    def _on_power_on(self, device: str, error: Optional[str]) -> None:
        if error:
            self._fail(f"Could not turn on Moonraker power device '{device}': {error}")
            return
        self._turn_on_next_power_device()

    def _wait_for_ready(self) -> None:
        if not self._busy:
            return
        if self._ready_attempts >= self.MAX_READY_ATTEMPTS:
            self._fail("Moonraker is reachable, but Klippy did not become ready in time.")
            return
        self._ready_attempts += 1
        self._json_request("GET", "server/info", self._on_server_info)

    def _on_server_info(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        state = ""
        if not error:
            result = (payload or {}).get("result") or {}
            if isinstance(result, dict):
                state = str(result.get("klippy_state") or "").lower()
        if state == "ready":
            self._upload_now()
            return

        reason = error or (state if state else "Klippy not ready")
        self._show_status(
            "Moonraker",
            f"Waiting for printer readiness ({self._ready_attempts}/{self.MAX_READY_ATTEMPTS}): {reason}",
        )
        delay_ms = max(100, int(self._config.ready_retry_interval_s * 1000.0))
        QTimer.singleShot(delay_ms, self._wait_for_ready)

    def _upload_now(self) -> None:
        if not self._busy or self._stream is None:
            return
        try:
            self._stream.seek(0)
            if isinstance(self._stream, BytesIO):
                data = self._stream.getvalue()
            else:
                data = self._stream.getvalue().encode("utf-8")
        except Exception as exc:
            self._fail(f"Could not read the prepared Cura output: {exc}")
            return

        self._show_status("Moonraker - Upload", f"Uploading {self._file_name}…", progress=0)

        multipart = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
        self._upload_multipart = multipart

        file_part = QHttpPart()
        file_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            QVariant(f'form-data; name="file"; filename="{self._file_name}"'),
        )
        file_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader,
            QVariant("application/octet-stream"),
        )
        file_part.setBody(QByteArray(data))
        multipart.append(file_part)

        root_part = QHttpPart()
        root_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            QVariant('form-data; name="root"'),
        )
        root_part.setBody(QByteArray(b"gcodes"))
        multipart.append(root_part)

        if self._path_name:
            path_part = QHttpPart()
            path_part.setHeader(
                QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                QVariant('form-data; name="path"'),
            )
            path_part.setBody(QByteArray(self._path_name.encode("utf-8")))
            multipart.append(path_part)

        if self._start_print:
            print_part = QHttpPart()
            print_part.setHeader(
                QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                QVariant('form-data; name="print"'),
            )
            print_part.setBody(QByteArray(b"true"))
            multipart.append(print_part)

        request = self._request("server/files/upload")
        reply = self._network.post(request, multipart)
        multipart.setParent(reply)
        self._upload_reply = reply
        reply.uploadProgress.connect(self._on_upload_progress)
        reply.finished.connect(lambda r=reply: self._on_upload_finished(r))

    def _on_upload_progress(self, sent: int, total: int) -> None:
        if total <= 0:
            return
        progress = max(0, min(100, int(sent * 100 / total)))
        if self._message is not None:
            try:
                self._message.setProgress(progress)
            except Exception:
                pass
        self.writeProgress.emit(self, progress)

    def _on_upload_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._upload_reply:
            reply.deleteLater()
            return
        self._upload_reply = None
        self._upload_multipart = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            error = reply.errorString()
            reply.deleteLater()
            self._fail(f"Uploading to Moonraker failed: {error}")
            return

        try:
            raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("error"):
                    raise ValueError(str(payload.get("error")))
        except json.JSONDecodeError:
            # A successful HTTP response is sufficient; older Moonraker/proxy
            # combinations are not guaranteed to return JSON for every upload.
            pass
        except Exception as exc:
            reply.deleteLater()
            self._fail(f"Moonraker rejected the upload: {exc}")
            return
        reply.deleteLater()

        self._hide_message()
        suffix = " and started the print" if self._start_print else ""
        self._message = Message(
            f"Uploaded '{self._file_name}' to {self.getName()}{suffix}.",
            30 if self._config.upload_autohide_message else 0,
            True,
        )
        self._message.setTitle("Moonraker")
        self._message.addAction("open_browser", "Open Browser", "globe", "Open the configured Moonraker frontend")
        self._message.actionTriggered.connect(self._on_message_action)
        self._message.show()

        self.writeSuccess.emit(self)
        self._cleanup(keep_message=True)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(self, path: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(self._base_url + "/" + path.lstrip("/")))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"Cura Moonraker Print Follower")
        if self._config.api_key:
            request.setRawHeader(b"X-Api-Key", self._config.api_key.encode("utf-8"))
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(15000)
        return request

    def _json_request(
        self,
        method: str,
        path: str,
        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],
        *,
        body: Optional[bytes] = None,
    ) -> None:
        if not self._busy:
            return
        if self._active_reply is not None:
            try:
                if self._active_reply.isRunning():
                    self._active_reply.abort()
            except Exception:
                pass
        request = self._request(path)
        if body is not None:
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self._network.get(request) if method.upper() == "GET" else self._network.post(request, QByteArray(body or b"{}"))
        self._active_reply = reply
        reply.finished.connect(lambda r=reply, cb=callback: self._finish_json_request(r, cb))

    def _finish_json_request(
        self,
        reply: QNetworkReply,
        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],
    ) -> None:
        if reply is not self._active_reply:
            reply.deleteLater()
            return
        self._active_reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            error = reply.errorString()
            reply.deleteLater()
            callback(None, error)
            return
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
            if not isinstance(payload, dict):
                raise ValueError("Moonraker returned a non-object JSON response")
            if payload.get("error"):
                raise ValueError(str(payload.get("error")))
        except Exception as exc:
            reply.deleteLater()
            callback(None, str(exc))
            return
        reply.deleteLater()
        callback(payload, None)

    # ------------------------------------------------------------------
    # State / validation / UI helpers
    # ------------------------------------------------------------------

    def _remember_upload_choices(self, path: str, start_print: bool) -> None:
        try:
            config = self._follower.current_printer_config()
            data = asdict(config)
            paths = list(config.upload_paths)
            if path and path not in paths:
                paths.append(path)
            data["upload_paths"] = paths
            if config.upload_remember_state:
                data["upload_path"] = path
                data["upload_start_print"] = bool(start_print)
            updated = PrinterConfig.from_dict(data)
            self._follower.apply_printer_config(updated)
            self._config = updated
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: could not remember upload choices: %s", exc)

    def _translate_filename(self, name: str) -> str:
        source = self._config.filename_translate_input
        target = self._config.filename_translate_output
        remove = self._config.filename_translate_remove
        if source and len(source) == len(target):
            try:
                return name.translate(str.maketrans(source, target, remove))
            except Exception:
                pass
        return name

    @staticmethod
    def _usable_url(value: str) -> bool:
        url = QUrl(str(value or ""))
        return url.isValid() and url.scheme() in ("http", "https") and bool(url.host())

    @staticmethod
    def _normalise_remote_path(value: str) -> str:
        return re.sub(r"^[\s/]+|[\s/]+$", "", str(value or ""))

    @staticmethod
    def _normalise_filename(value: str) -> str:
        value = os.path.basename(str(value or "").strip())
        if value in (".", ".."):
            return ""
        if any(char in value for char in ':*?"<>|'):
            return ""
        return value

    def _show_status(self, title: str, text: str, progress: Optional[int] = None) -> None:
        if self._message is None:
            self._message = Message(text, 0, False, -1 if progress is not None else None)
            self._message.setTitle(title)
            self._message.show()
        else:
            try:
                self._message.setTitle(title)
                self._message.setText(text)
            except Exception:
                pass
        if progress is not None and self._message is not None:
            try:
                self._message.setProgress(progress)
            except Exception:
                pass

    def _hide_message(self) -> None:
        if self._message is not None:
            try:
                self._message.hide()
            except Exception:
                pass
            self._message = None

    def _on_message_action(self, message: Message, action: str) -> None:
        if action != "open_browser":
            return
        target = str(self._config.frontend_url or self._base_url).strip()
        if target:
            QDesktopServices.openUrl(QUrl(target))
        self._hide_message()

    def _fail(self, text: str) -> None:
        Logger.log("e", "Moonraker Print Follower: %s", text)
        self._hide_message()
        message = Message(text, 0, False)
        message.setTitle("Moonraker - Error")
        message.show()
        if self._busy:
            self.writeError.emit(self)
        self._cleanup()

    def _cleanup(self, *, keep_message: bool = False) -> None:
        self._busy = False
        self._ready_attempts = 0
        self._power_devices = []
        self._power_index = 0
        if self._active_reply is not None:
            try:
                if self._active_reply.isRunning():
                    self._active_reply.abort()
            except Exception:
                pass
            self._active_reply = None
        if self._upload_reply is not None:
            try:
                if self._upload_reply.isRunning():
                    self._upload_reply.abort()
            except Exception:
                pass
            self._upload_reply = None
        self._upload_multipart = None
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._dialog = None
        if not keep_message:
            self._hide_message()