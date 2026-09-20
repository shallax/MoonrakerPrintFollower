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
        # The routed monitor (the 4.5.0 ownership fix): the monitor
        # OUT-signals that feed the shared Preview presentation attach
        # ONLY to the current machine's monitor — a cached, deposed
        # monitor never publishes into the live presentation.
        self._routed_monitor: Optional[Any] = None
        self._routed_action = None
        self._routed_preview = None
        follower.client.sessionInvalidated.connect(self._invalidate_devices)
        # The presentation IN-signals connect once and dispatch to
        # the current monitor only — broadcasting to every cached
        # monitor and letting stale ones refuse was the ownership
        # violation (the 4.5.0 review).
        follower.presentation.bedMeshThresholdsRequested.connect(self._route_bed_mesh_thresholds)
        follower.presentation.printPauseRequested.connect(self._route_pause_request)

        changed = getattr(application, "globalContainerStackChanged", None)
        self._stack_signal = changed
        self._running = False
        if changed is not None:
            changed.connect(self.refresh)

    def start(self) -> None:
        self._running = True
        self.refresh()

    def _current_monitor(self) -> Optional[Any]:
        device = self._current
        return getattr(device, "activePrinter", None) if device is not None else None

    def _route_bed_mesh_thresholds(self, low: float, high: float) -> None:
        monitor = self._current_monitor()
        if monitor is not None:
            monitor.setBedMeshThresholds(low, high)

    def _route_pause_request(self) -> None:
        monitor = self._current_monitor()
        if monitor is not None:
            monitor.stripPausePrint()

    def _grant_monitor_routing(self, monitor: Any) -> None:
        """The current monitor's sole Preview routing: the verdicts
        and the preview block publish only from the selected machine's
        monitor, re-granted on every switch and revoked on every
        deactivation."""
        if self._routed_monitor is monitor:
            return
        self._revoke_monitor_routing()
        self._routed_monitor = monitor
        self._routed_action = monitor.actionChanged.connect(lambda: self._follower.presentation.publish_pause_verdicts(
            monitor.canPausePrint, monitor.canResumePrint,
            monitor.pauseReason, monitor.resumeReason,
            monitor.pauseReasonDetail, monitor.resumeReasonDetail))
        self._routed_preview = monitor.previewBlockChanged.connect(self._follower.receive_preview_block)
        # The migration notice's overlay owner (the UX ruling): the
        # toast waits for the CURRENT model's What's-New dismissal —
        # a cached monitor that lost the selection must not hold it.
        if getattr(self._follower, "notice", None) is not None:
            self._follower.notice().attach_model(monitor)

    def _revoke_monitor_routing(self) -> None:
        monitor = self._routed_monitor
        if monitor is None:
            return
        if self._routed_action is not None:
            try:
                monitor.actionChanged.disconnect(self._routed_action)
            except Exception:
                pass
        if self._routed_preview is not None:
            try:
                monitor.previewBlockChanged.disconnect(self._routed_preview)
            except Exception:
                pass
        self._routed_monitor = None
        self._routed_action = None
        self._routed_preview = None

    def _invalidate_devices(self) -> None:
        """Runs before the shared transport changes credentials or
        session. The monitors suspend their OWN runtime lanes on the
        session signal — ownership is untouched here, so the selected
        machine's monitor re-arms on the reconnect. Only the upload
        sides need the explicit teardown."""
        for device in self._devices.values():
            deactivate = getattr(device, "deactivate", None)
            if callable(deactivate):
                try:
                    deactivate()
                except Exception as error:
                    Logger.logException("e", "Moonraker output deactivation failed: %s", error)

    def stop(self) -> None:
        self._running = False
        for device in self._devices.values():
            self._deactivate_device(device)
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
        if getattr(device, "activePrinter", None) is self._routed_monitor:
            self._revoke_monitor_routing()
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
                request_file_download=self._follower.request_file_download,
                download_failed=self._follower.download_failed,
                request_download_progress=self._follower.download_progress,
                cancel_file_download=self._follower.cancel_file_download,
                request_monitor_download=self._follower.confirmDownloadForMonitor,
                request_plate_anchor=self._follower.setPlateAnchor,
                request_plate_split=self._follower.setPlateSplit,
                persistence=self._follower.persistence,
                identity=self._follower.current_printer_identity,
            )
            # The Preview wirings are NOT made here: the grant below
            # attaches them to the current monitor only, and a machine
            # switch revokes them from the deposed one (the 4.5.0
            # ownership fix — cached monitors used to stay wired to
            # the shared presentation for their whole lives).
            device._printers = [monitor]

        # Refresh the display identity on every install: a cached monitor
        # reused across machine switches must not keep the old printer's
        # name or buildplate.
        try:
            monitor.updateName(stack.getName())
            monitor.updateUniqueName(stack.getId())
            monitor.updateBuildplate(stack.getProperty("machine_buildplate_type", "value"))
            # The bed-mesh map's bed-space geometry (4.2.0, a
            # request): the physical dimensions the expanded
            # map draws the probed bounds within.
            monitor.setMachineGeometry(
                stack.getProperty("machine_width", "value"),
                stack.getProperty("machine_depth", "value"),
                stack.getProperty("machine_center_is_zero", "value"))
        except Exception:
            pass

        self._set_monitor_active(device, True)
        self._grant_monitor_routing(monitor)
        # The stage's Loader reads the CONSTANT monitorItem property
        # once — the shell document this device serves compiles in
        # milliseconds, so that read cannot land mid-compile; the
        # dashboard compiles asynchronously inside the shell, off the
        # startup path.
        device.setMonitorViewQmlPath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "MoonrakerMonitorBedMesh.qml"
        ))
        try:
            monitor.refreshAll()
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower: Monitor refresh failed: %s", exc)

    def refresh(self, *_args: Any) -> None:
        if not self._running:
            # Stopped (the 2026-09-19 review's E): a stack change
            # after stop() must not reinstall a device.
            return
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
                    active_identity=self._follower.current_printer_identity,
                    has_slice=self._follower.has_toolpath)
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
