"""Native Cura Machine Action for unified per-printer Moonraker settings."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Optional

from PyQt6.QtCore import QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from cura.MachineAction import MachineAction
from UM.Logger import Logger
from UM.Settings.DefinitionContainer import DefinitionContainer

from .FollowController import FollowMode
from .MoonrakerProtocol import objects_list_endpoint, server_info_endpoint
from .PrinterConfig import PrinterConfig


class MoonrakerFollowerMachineAction(MachineAction):
    """Configure both live following and Cura-to-Moonraker output."""

    KEY = "MoonrakerPrintFollowerConfigureAction"
    LABEL = "Configure Moonraker"

    settingsChanged = pyqtSignal()
    testStatusChanged = pyqtSignal()
    testBusyChanged = pyqtSignal()

    def __init__(self, application: Any, follower: Any, output_plugin: Any = None) -> None:
        super().__init__(self.KEY, self.LABEL)
        self._application = application
        self._follower = follower
        self._output_plugin = output_plugin
        self._qml_url = "MoonrakerFollowerConfiguration.qml"

        self._probe_network = QNetworkAccessManager(self)
        self._probe_reply: Optional[QNetworkReply] = None
        self._probe_base_url = ""
        self._probe_api_key = ""
        self._probe_server_info: Dict[str, Any] = {}
        self._test_status = "Not tested"
        self._test_busy = False

        registry = application.getContainerRegistry()
        self._container_registry = registry
        registry.containerAdded.connect(self._on_container_added)

        global_stack_changed = getattr(application, "globalContainerStackChanged", None)
        if global_stack_changed is not None:
            try:
                global_stack_changed.connect(self._on_global_stack_changed)
            except Exception:
                pass

    def _on_container_added(self, container: Any) -> None:
        try:
            if not isinstance(container, DefinitionContainer):
                return
            if container.getMetaDataEntry("type") != "machine":
                return
            self._application.getMachineActionManager().addSupportedAction(
                container.getId(), self.getKey()
            )
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: unable to register machine action: %s", exc)

    def _on_global_stack_changed(self, *_args: Any) -> None:
        self.cancelTest()
        self._test_status = "Not tested"
        self.testStatusChanged.emit()
        self.settingsChanged.emit()

    def _reset(self) -> None:
        self.cancelTest()
        self._test_status = "Not tested"
        self.testStatusChanged.emit()
        self.settingsChanged.emit()

    def _config(self) -> PrinterConfig:
        return self._follower.current_printer_config()

    @pyqtProperty(str, notify=settingsChanged)
    def machineName(self) -> str:
        return self._follower.current_printer_identity()[1]

    # ------------------------------------------------------------------
    # Connection / following settings
    # ------------------------------------------------------------------

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsEnabled(self) -> bool:
        return self._config().enabled

    @pyqtProperty(str, notify=settingsChanged)
    def settingsUrl(self) -> str:
        return self._config().url

    @pyqtProperty(str, notify=settingsChanged)
    def settingsApiKey(self) -> str:
        return self._config().api_key

    @pyqtProperty(str, notify=settingsChanged)
    def settingsPollInterval(self) -> str:
        return str(self._config().poll_interval_ms)

    @pyqtProperty(str, notify=settingsChanged)
    def settingsFollowMode(self) -> str:
        return self._config().follow_mode

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsLayerOneBased(self) -> bool:
        return self._config().moonraker_layer_is_one_based

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsPathFollow(self) -> bool:
        return self._config().path_follow

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsAutoPreview(self) -> bool:
        return self._config().auto_preview

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsToolheadIndicator(self) -> bool:
        return self._config().show_toolhead_indicator

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsZFallback(self) -> bool:
        return self._config().z_fallback

    @pyqtProperty(str, notify=settingsChanged)
    def settingsZTolerance(self) -> str:
        return f"{self._config().z_tolerance:.3f}"

    # ------------------------------------------------------------------
    # Integrated Moonraker output settings
    # ------------------------------------------------------------------

    @pyqtProperty(str, notify=settingsChanged)
    def settingsFrontendUrl(self) -> str:
        return self._config().frontend_url

    @pyqtProperty(str, notify=settingsChanged)
    def settingsOutputFormat(self) -> str:
        return self._config().output_format

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadDialog(self) -> bool:
        return self._config().upload_dialog

    @pyqtProperty(str, notify=settingsChanged)
    def settingsUploadPath(self) -> str:
        return self._config().upload_path

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadStartPrint(self) -> bool:
        return self._config().upload_start_print

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadRememberState(self) -> bool:
        return self._config().upload_remember_state

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadAutohideMessage(self) -> bool:
        return self._config().upload_autohide_message

    @pyqtProperty(str, notify=settingsChanged)
    def settingsPowerDevices(self) -> str:
        return self._config().power_devices

    @pyqtProperty(str, notify=settingsChanged)
    def settingsReadyRetryInterval(self) -> str:
        return f"{self._config().ready_retry_interval_s:g}"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTranslateInput(self) -> str:
        return self._config().filename_translate_input

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTranslateOutput(self) -> str:
        return self._config().filename_translate_output

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTranslateRemove(self) -> str:
        return self._config().filename_translate_remove

    # ------------------------------------------------------------------
    # Validation / save
    # ------------------------------------------------------------------

    @pyqtProperty(str, notify=testStatusChanged)
    def testStatus(self) -> str:
        return self._test_status

    @pyqtProperty(bool, notify=testBusyChanged)
    def testBusy(self) -> bool:
        return self._test_busy

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return "http://"
        if not value.lower().startswith(("http://", "https://")):
            value = f"http://{value}"
        return value.rstrip("/")

    @staticmethod
    def _url_is_usable(value: str) -> bool:
        parsed = QUrl(value)
        return parsed.isValid() and parsed.scheme() in ("http", "https") and bool(parsed.host())

    @pyqtSlot(str, result=bool)
    def validUrl(self, value: str) -> bool:
        return self._url_is_usable(self._normalise_base_url(value))

    @pyqtSlot(str, result=bool)
    def validPollInterval(self, value: str) -> bool:
        try:
            return int(str(value).strip()) > 0
        except (TypeError, ValueError):
            return False

    @pyqtSlot(str, result=bool)
    def validZTolerance(self, value: str) -> bool:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return False
        return 0.005 <= number <= 0.250

    @pyqtSlot(str, result=bool)
    def validRetryInterval(self, value: str) -> bool:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return False
        return 0.1 <= number <= 60.0

    @pyqtSlot(str, str, result=bool)
    def validTranslation(self, source: str, target: str) -> bool:
        return len(str(source or "")) == len(str(target or ""))

    @pyqtSlot(QVariant, result=bool)
    def saveConfig(self, params: QVariant) -> bool:
        try:
            raw = params.toVariant() if hasattr(params, "toVariant") else params
            if not isinstance(raw, dict):
                return False

            interval = int(str(raw.get("poll_interval_ms", "")).strip())
            tolerance = float(str(raw.get("z_tolerance", "")).strip())
            retry_interval = float(str(raw.get("ready_retry_interval_s", "")).strip())
            url = self._normalise_base_url(str(raw.get("url", "")))
            enabled = bool(raw.get("enabled", False))
            if interval <= 0 or not (0.005 <= tolerance <= 0.250):
                return False
            if not (0.1 <= retry_interval <= 60.0):
                return False
            if enabled and not self._url_is_usable(url):
                return False

            trans_input = str(raw.get("filename_translate_input") or "")
            trans_output = str(raw.get("filename_translate_output") or "")
            if len(trans_input) != len(trans_output):
                return False

            mode = str(raw.get("follow_mode") or FollowMode.EXACT.value)
            if mode not in {item.value for item in FollowMode}:
                mode = FollowMode.EXACT.value

            current = self._config()
            data = asdict(current)
            data.update({
                "enabled": enabled,
                "url": url,
                "api_key": str(raw.get("api_key") or "").strip(),
                "poll_interval_ms": interval,
                "moonraker_layer_is_one_based": bool(raw.get("moonraker_layer_is_one_based", True)),
                "auto_preview": bool(raw.get("auto_preview", False)),
                "z_fallback": bool(raw.get("z_fallback", True)),
                "z_tolerance": tolerance,
                "path_follow": bool(raw.get("path_follow", True)),
                "show_toolhead_indicator": bool(raw.get("show_toolhead_indicator", True)),
                "follow_mode": mode,
                "frontend_url": str(raw.get("frontend_url") or "").strip(),
                "output_format": str(raw.get("output_format") or "gcode").lower(),
                "upload_dialog": bool(raw.get("upload_dialog", True)),
                "upload_path": str(raw.get("upload_path") or "").strip().strip("/"),
                "upload_start_print": bool(raw.get("upload_start_print", False)),
                "upload_remember_state": bool(raw.get("upload_remember_state", False)),
                "upload_autohide_message": bool(raw.get("upload_autohide_message", False)),
                "power_devices": str(raw.get("power_devices") or "").strip(),
                "ready_retry_interval_s": retry_interval,
                "filename_translate_input": trans_input,
                "filename_translate_output": trans_output,
                "filename_translate_remove": str(raw.get("filename_translate_remove") or ""),
            })
            config = PrinterConfig.from_dict(data)
            self._follower.apply_printer_config(config)
            if self._output_plugin is not None:
                try:
                    self._output_plugin.refresh()
                except Exception as exc:
                    Logger.log("w", "Moonraker Print Follower: output refresh after save failed: %s", exc)
            self.settingsChanged.emit()
            return True
        except Exception as exc:
            Logger.log("e", "Moonraker Print Follower: unable to save machine settings: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def _set_test_state(self, status: str, *, busy: Optional[bool] = None) -> None:
        if status != self._test_status:
            self._test_status = status
            self.testStatusChanged.emit()
        if busy is not None and bool(busy) != self._test_busy:
            self._test_busy = bool(busy)
            self.testBusyChanged.emit()

    @pyqtSlot(str, str)
    def testConnection(self, url: str, api_key: str) -> None:
        base_url = self._normalise_base_url(url)
        if not self._url_is_usable(base_url):
            self._set_test_state("Enter a valid Moonraker URL", busy=False)
            return
        if self._probe_reply is not None:
            try:
                if self._probe_reply.isRunning():
                    return
            except Exception:
                pass

        self._probe_base_url = base_url
        self._probe_api_key = str(api_key or "").strip()
        self._probe_server_info = {}
        self._set_test_state("Testing connection…", busy=True)
        self._start_probe_request(server_info_endpoint(base_url), self._handle_probe_server_info)

    def _start_probe_request(self, endpoint: str, handler: Any) -> None:
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        if self._probe_api_key:
            request.setRawHeader(b"X-Api-Key", self._probe_api_key.encode("utf-8"))
        reply = self._probe_network.get(request)
        self._probe_reply = reply
        reply.finished.connect(lambda r=reply: handler(r))

    def _handle_probe_server_info(self, reply: QNetworkReply) -> None:
        if reply is not self._probe_reply:
            reply.deleteLater()
            return
        self._probe_reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._set_test_state(f"Connection failed: {reply.errorString()}", busy=False)
                return
            import json
            payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
            self._probe_server_info = payload.get("result") or {}
        except Exception as exc:
            self._set_test_state(f"Invalid server response: {exc}", busy=False)
            return
        finally:
            reply.deleteLater()
        self._start_probe_request(objects_list_endpoint(self._probe_base_url), self._handle_probe_objects)

    def _handle_probe_objects(self, reply: QNetworkReply) -> None:
        if reply is not self._probe_reply:
            reply.deleteLater()
            return
        self._probe_reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._set_test_state(f"Printer-object test failed: {reply.errorString()}", busy=False)
                return
            import json
            payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
            objects = set(str(value) for value in ((payload.get("result") or {}).get("objects") or []))
            required = {"print_stats", "virtual_sdcard", "gcode_move"}
            missing = sorted(required - objects)
            info = self._probe_server_info
            version = str(info.get("moonraker_version") or info.get("software_version") or "unknown")
            klippy = str(info.get("klippy_state") or "unknown")
            detail = f"missing: {', '.join(missing)}" if missing else "required print objects available"
            if "motion_report" in objects:
                detail += "; live-position refinement available"
            self._set_test_state(
                f"Connected — Moonraker {version}; Klippy {klippy}; {detail}",
                busy=False,
            )
        except Exception as exc:
            self._set_test_state(f"Invalid printer-object response: {exc}", busy=False)
        finally:
            reply.deleteLater()

    @pyqtSlot()
    def cancelTest(self) -> None:
        reply = self._probe_reply
        self._probe_reply = None
        if reply is not None:
            try:
                if reply.isRunning():
                    reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass
        if self._test_busy:
            self._test_busy = False
            self.testBusyChanged.emit()
