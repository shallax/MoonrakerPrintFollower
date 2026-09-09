from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PyQt6.QtCore import QUrl
from UM.Logger import Logger
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin

from .MoonrakerMonitorModel import MoonrakerMonitorModel
from .MoonrakerOutputDevice import MoonrakerOutputController, MoonrakerOutputDevice


class MoonrakerOutputDevicePlugin(OutputDevicePlugin):
    """Expose one Moonraker output device for the active Cura printer."""

    def __init__(self, application: Any, follower: Any) -> None:
        super().__init__()
        self._application = application
        self._follower = follower
        self._devices: Dict[str, MoonrakerOutputDevice] = {}
        self._current: Optional[MoonrakerOutputDevice] = None
        follower.client.sessionInvalidated.connect(self._invalidate_devices)

        changed = getattr(application, "globalContainerStackChanged", None)
        if changed is not None:
            changed.connect(self.refresh)

    def start(self) -> None:
        self.refresh()

    def _invalidate_devices(self) -> None:
        """Runs before the shared transport changes credentials or session."""
        for device in self._devices.values():
            self._deactivate_device(device)

    def stop(self) -> None:
        self._invalidate_devices()
        if self._current is not None:
            try:
                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
            except Exception:
                pass
        self._current = None

    @staticmethod
    def _set_monitor_active(device: MoonrakerOutputDevice, active: bool) -> None:
        monitor = getattr(device, "activePrinter", None)
        setter = getattr(monitor, "setMonitoringActive", None)
        if callable(setter):
            try:
                setter(bool(active))
            except Exception:
                pass

    def _deactivate_device(self, device: MoonrakerOutputDevice) -> None:
        """Invalidate Monitor and upload work before an output device loses ownership."""
        self._set_monitor_active(device, False)
        deactivate = getattr(device, "deactivate", None)
        if callable(deactivate):
            try:
                deactivate()
            except Exception as error:
                Logger.logException("e", "Moonraker output deactivation failed: %s", error)

    @staticmethod
    def _usable_url(value: str) -> bool:
        url = QUrl(str(value or ""))
        return url.isValid() and url.scheme() in ("http", "https") and bool(url.host())

    def _install_monitor(self, device: MoonrakerOutputDevice, stack: Any) -> None:
        monitor = getattr(device, "activePrinter", None)
        if not isinstance(monitor, MoonrakerMonitorModel):
            try:
                extruders = int(stack.getProperty("machine_extruder_count", "value") or 1)
            except Exception:
                extruders = 1
            monitor = MoonrakerMonitorModel(
                MoonrakerOutputController(device), extruders,
                client=self._follower.client,
                print_state=lambda: self._follower.print_state,
                config=self._follower.current_printer_config,
                apply_config=self._follower.apply_printer_config,
                bed_mesh=self._follower.bed_mesh,
                request_load=self._follower.confirmForceLoadCurrentPrint,
                request_monitor_download=self._follower.confirmDownloadForMonitor,
            )
            device._printers = [monitor]

        # Refresh the display identity on every install: a cached monitor
        # reused across machine switches must not keep the old printer's
        # name or buildplate.
        try:
            monitor.updateName(stack.getName())
            monitor.updateUniqueName(stack.getId())
            monitor.updateBuildplate(stack.getProperty("machine_buildplate_type", "value"))
        except Exception:
            pass

        self._set_monitor_active(device, True)
        device.setMonitorViewQmlPath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "MoonrakerMonitorBedMesh.qml"
        ))
        try:
            monitor.refreshAll()
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: Monitor refresh failed: %s", exc)

    def refresh(self, *_args: Any) -> None:
        try:
            stack = self._application.getGlobalContainerStack()
            if stack is None:
                if self._current is not None:
                    self._deactivate_device(self._current)
                    try:
                        self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                    except Exception:
                        pass
                    self._current = None
                return
            machine_id = str(stack.getId())
            config = self._follower.current_printer_config()
            usable = self._usable_url(str(config.url or "").strip())

            if self._current is not None and self._current.getId() != MoonrakerOutputDevice.DEVICE_PREFIX + machine_id:
                self._deactivate_device(self._current)
                self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                self._current = None

            if not usable:
                if self._current is not None:
                    self._deactivate_device(self._current)
                    self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                    self._current = None
                return

            device = self._devices.get(machine_id)
            if device is None:
                device = MoonrakerOutputDevice(self._application, machine_id,
                    client=self._follower.client,
                    config=self._follower.current_printer_config,
                    apply_config=self._follower.apply_printer_config,
                    active_identity=self._follower.current_printer_identity)
                self._devices[machine_id] = device
            else:
                device.updateConfig(self._follower.current_printer_identity)

            # The transition must be computed after the incoming device is
            # resolved: at startup both _current and device are None before
            # this point, and the registration below must still fire.
            transition = self._current is not device
            if transition and self._current is not None:
                # Deactivate the outgoing instance before the incoming Monitor
                # activates: both share the "monitor" transport owner, and the
                # old instance's teardown would otherwise cancel the new
                # instance's first in-flight requests.
                self._deactivate_device(self._current)
                try:
                    self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                except Exception:
                    pass

            self._install_monitor(device, stack)
            self._current = device
            if transition:
                self.getOutputDeviceManager().addOutputDevice(device)
        except Exception as exc:
            Logger.log("e", "Moonraker Print Follower: output-device refresh failed: %s", exc)
