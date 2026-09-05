from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PyQt6.QtCore import QUrl
from UM.Logger import Logger
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin

# MoonrakerMonitorRuntime remains the proven layer-following base implementation.
# MoonrakerMonitorControls remains the tested control base beneath the typed layer.
# MoonrakerMonitorEnhanced.qml is retained in the package as the previous dashboard.
from .MoonrakerMonitorTypedControls import MoonrakerMonitorModel
from .MoonrakerOutputDevice import MoonrakerOutputController
from .MoonrakerOutputDeviceLifecycle import MoonrakerOutputDevice


class MoonrakerOutputDevicePlugin(OutputDevicePlugin):
    """Expose one Moonraker output device for the active Cura printer."""

    def __init__(self, application: Any, follower: Any) -> None:
        super().__init__()
        self._application = application
        self._follower = follower
        self._devices: Dict[str, MoonrakerOutputDevice] = {}
        self._current: Optional[MoonrakerOutputDevice] = None

        changed = getattr(application, "globalContainerStackChanged", None)
        if changed is not None:
            changed.connect(self.refresh)

    def start(self) -> None:
        self.refresh()

    def stop(self) -> None:
        if self._current is not None:
            try:
                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
            except Exception:
                pass
        self._current = None

    @staticmethod
    def _usable_url(value: str) -> bool:
        url = QUrl(str(value or ""))
        return url.isValid() and url.scheme() in ("http", "https") and bool(url.host())

    def _install_monitor(self, device: MoonrakerOutputDevice, stack: Any) -> None:
        """Attach the unified Moonraker model and Cura Monitor QML to a device."""
        monitor = getattr(device, "activePrinter", None)
        if not isinstance(monitor, MoonrakerMonitorModel):
            try:
                extruders = int(stack.getProperty("machine_extruder_count", "value") or 1)
            except Exception:
                extruders = 1
            monitor = MoonrakerMonitorModel(
                MoonrakerOutputController(device), extruders, self._follower
            )
            try:
                monitor.updateName(stack.getName())
                monitor.updateUniqueName(stack.getId())
                monitor.updateBuildplate(stack.getProperty("machine_buildplate_type", "value"))
            except Exception:
                pass
            # PrinterOutputDevice exposes activePrinter from this model list.
            device._printers = [monitor]

        # "MoonrakerMonitorDashboard.qml" composes the established
        # "MoonrakerMonitor.qml" view, preserving its camera/status/power UI
        # while adding typed controls.
        device._monitor_view_qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "MoonrakerMonitorDashboard.qml"
        )
        try:
            monitor.refreshAll()
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: Monitor refresh failed: %s", exc)

    def refresh(self, *_args: Any) -> None:
        try:
            stack = self._application.getGlobalContainerStack()
            if stack is None:
                return
            machine_id = str(stack.getId())
            config = self._follower.current_printer_config()
            usable = self._usable_url(str(config.url or "").strip())

            if self._current is not None and self._current.getId() != MoonrakerOutputDevice.DEVICE_PREFIX + machine_id:
                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                self._current = None

            if not usable:
                if self._current is not None:
                    self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                    self._current = None
                return

            device = self._devices.get(machine_id)
            if device is None:
                device = MoonrakerOutputDevice(self._application, self._follower, machine_id)
                self._install_monitor(device, stack)
                self._devices[machine_id] = device
            else:
                device.updateConfig(config)
                self._install_monitor(device, stack)

            if self._current is not device:
                if self._current is not None:
                    try:
                        self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                    except Exception:
                        pass
                self._current = device
                self.getOutputDeviceManager().addOutputDevice(device)
            else:
                device.updateConfig(config)
        except Exception as exc:
            Logger.log("e", "Moonraker Print Follower: output-device refresh failed: %s", exc)
