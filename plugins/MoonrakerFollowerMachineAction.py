"""Native Cura Machine Action for per-printer Moonraker follower settings."""

from __future__ import annotations

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
    """Expose follower configuration through Cura's Manage Printers UI."""

    KEY = "MoonrakerPrintFollowerConfigureAction"
    LABEL = "Configure Moonraker Follower"

    settingsChanged = pyqtSignal()
    testStatusChanged = pyqtSignal()
    testBusyChanged = pyqtSignal()

    def __init__(self, application: Any, follower: Any) -> None:
        super().__init__(self.KEY, self.LABEL)
        self._application = application
        self._follower = follower
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
        """Advertise the action for every local Cura machine definition."""
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

    @pyqtSlot(QVariant, result=bool)
    def saveConfig(self, params: QVariant) -> bool:
        try:
            raw = params.toVariant() if hasattr(params, "toVariant") else params
            if not isinstance(raw, dict):
                return False
            interval = int(str(raw.get("poll_interval_ms", "")).strip())
            tolerance = float(str(raw.get("z_tolerance", "")).strip())
            url = self._normalise_base_url(str(raw.get("url", "")))
            enabled = bool(raw.get("enabled", False))
            if interval <= 0 or not (0.005 <= tolerance <= 0.250):
                return False
            if enabled and not self._url_is_usable(url):
                return False

            mode = str(raw.get("follow_mode") or FollowMode.EXACT.value)
            if mode not in {item.value for item in FollowMode}:
                mode = FollowMode.EXACT.value

            config = PrinterConfig(
                enabled=enabled,
                url=url,
                api_key=str(raw.get("api_key") or "").strip(),
                poll_interval_ms=interval,
                moonraker_layer_is_one_based=bool(raw.get("moonraker_layer_is_one_based", True)),
                auto_preview=bool(raw.get("auto_preview", False)),
                z_fallback=bool(raw.get("z_fallback", True)),
                z_tolerance=tolerance,
                path_follow=bool(raw.get("path_follow", True)),
                show_toolhead_indicator=bool(raw.get("show_toolhead_indicator", True)),
                follow_mode=mode,
            )
            self._follower.apply_printer_config(config)
            self.settingsChanged.emit()
            return True
        except Exception as exc:
            Logger.log("e", "Moonraker Print Follower: unable to save machine settings: %s", exc)
            return False

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
            if missing:
                detail = f"missing: {', '.join(missing)}"
            else:
                detail = "required print objects available"
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
