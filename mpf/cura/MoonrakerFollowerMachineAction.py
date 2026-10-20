# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-13 [54a31e5] Implement interrupted print recovery handover
# 2026-09-04 [2f1de9e] Update to v2.0.0.

"""Native Cura Machine Action for unified per-printer Moonraker settings."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, replace
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
# QHostAddress is a QtNetwork class: some bundled PyQt6 builds (Cura
# 5.13's included) do not re-export it from QtCore, and the plugin
# fails to register with "cannot import name 'QHostAddress'" when the
# import points at the wrong module.
from PyQt6.QtNetwork import QHostAddress

from cura.MachineAction import MachineAction
from UM.Logger import Logger
from UM.Resources import Resources
from UM.Settings.DefinitionContainer import DefinitionContainer

from ..application.indexing.CacheNamespaces import CACHE_DIRECTORY_NAME
from ..preview.FollowController import FollowMode
from ..moonraker.MoonrakerProtocol import objects_list_endpoint, server_info_endpoint
from ..moonraker.MoonrakerSession import RequestCategory
from ..moonraker.MoonrakerTransport import MoonrakerHttpTransport
from ..settings.PrinterConfig import PrinterConfig, normalise_url

from ..settings.MigrationPresentation import migration_banner_text, migration_diagnostics_text


class MoonrakerFollowerMachineAction(MachineAction):
    """Configure both live following and Cura-to-Moonraker output."""

    KEY = "MoonrakerPrintFollowerConfigureAction"
    LABEL = "Configure Moonraker"

    settingsChanged = pyqtSignal()
    testStatusChanged = pyqtSignal()
    testBusyChanged = pyqtSignal()
    cacheStatusChanged = pyqtSignal()
    onboardingResetStatusChanged = pyqtSignal()
    detectionResetStatusChanged = pyqtSignal()
    cadResetStatusChanged = pyqtSignal()
    detectionEvidenceStatusChanged = pyqtSignal()
    migrationChanged = pyqtSignal()
    detectionChanged = pyqtSignal()
    recoveryDiscoveryChanged = pyqtSignal()

    def __init__(self, application: Any, follower: Any, output_plugin: Any = None) -> None:
        super().__init__(self.KEY, self.LABEL)
        self._application = application
        self._follower = follower
        self._output_plugin = output_plugin
        self._toolhead_models = getattr(follower, "toolhead_models", None)
        self._detection = getattr(follower, "detection", None)
        self._detection_refusal = ""
        self._recovery_sensors = []
        self._recovery_saved_states = ["PAUSE_STATE"]
        self._recovery_prepare_macros = []
        self._recovery_sensor_pause = {}
        self._recovery_discovery_status = "Open this tab to read the printer configuration."
        if self._detection is not None:
            self._detection.stateChanged.connect(self.detectionChanged.emit)
        self._qml_url = "settings/MoonrakerFollowerConfiguration.qml"

        # Connection tests intentionally use a separate transport instance because
        # the URL/API key may be unsaved. They still use the same HTTP utility,
        # parsing, cancellation and telemetry as the live session.
        self._probe_transport = MoonrakerHttpTransport(self)
        self._probe_base_url = ""
        self._probe_api_key = ""
        self._probe_server_info: Dict[str, Any] = {}
        self._test_status = "Not tested"
        self._test_busy = False
        self._cache_status = ""
        self._onboarding_reset_status = ""
        self._detection_reset_status = ""
        self._cad_reset_status = ""
        self._detection_evidence_status = ""
        self._detection_reset_pending = False
        if self._detection is not None:
            self._detection.stateChanged.connect(self._on_detection_reset_progress)

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
        if self._toolhead_models is not None: self._toolhead_models.reset()
        self._test_status = "Not tested"
        self.testStatusChanged.emit()
        self.settingsChanged.emit()

    def _reset(self) -> None:
        self.cancelTest()
        if self._toolhead_models is not None: self._toolhead_models.reset()
        self._test_status = "Not tested"
        self.testStatusChanged.emit()
        self.settingsChanged.emit()

    def _config(self) -> PrinterConfig:
        return self._follower.current_printer_config()

    @pyqtProperty(QObject, constant=True)
    def toolheadModel(self): return self._toolhead_models

    @pyqtSlot()
    def cancelToolheadModel(self):
        if self._toolhead_models is not None: self._toolhead_models.reset()

    # ------------------------------------------------------------------
    # The migration failure surfaces (the settings page's mirror — the
    # model's values reach the Monitor; this page's manager is the
    # ACTION, so the five live here, computed from the record the
    # facade keeps. The 2026-09-18 live find: the page read these off
    # the wrong object and the broken bindings showed a dead banner.)
    # ------------------------------------------------------------------

    def _migration_record(self) -> Dict[str, Any]:
        persistence = getattr(self._follower, "persistence", None)
        record = persistence.migration_record() if persistence is not None else None
        return dict(record) if isinstance(record, dict) else {}

    @pyqtProperty(bool, notify=migrationChanged)
    def migrationBannerVisible(self) -> bool:
        record = self._migration_record()
        return bool(record.get("status") == "failed" and not record.get("bannerDismissed"))

    @pyqtProperty(str, notify=migrationChanged)
    def migrationBannerText(self) -> str:
        record = self._migration_record()
        return migration_banner_text(record) if record.get("status") == "failed" else ""

    @pyqtProperty(bool, notify=migrationChanged)
    def migrationBackupAvailable(self) -> bool:
        record = self._migration_record()
        return bool(record.get("status") == "failed" and record.get("backupWritten") and record.get("backupName"))

    @pyqtProperty(bool, notify=migrationChanged)
    def migrationDiagnosticsVisible(self) -> bool:
        record = self._migration_record()
        return bool(record.get("status") == "failed" and record.get("bannerDismissed"))

    @pyqtProperty(str, notify=migrationChanged)
    def migrationDiagnosticsText(self) -> str:
        record = self._migration_record()
        return migration_diagnostics_text(record) if record.get("status") == "failed" else ""

    @pyqtSlot()
    def dismissMigrationBanner(self) -> None:
        persistence = getattr(self._follower, "persistence", None)
        if persistence is not None:
            persistence.set_migration_record({"bannerDismissed": True})
        self.migrationChanged.emit()

    @pyqtSlot()
    def openMigrationBackupFolder(self) -> None:
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(Resources.getConfigStoragePath()))

    @pyqtProperty(str, notify=settingsChanged)
    def machineName(self) -> str:
        return self._follower.current_printer_identity()[1]

    # ------------------------------------------------------------------
    # Connection / following settings
    # ------------------------------------------------------------------

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsEnabled(self) -> bool:
        return self._config().enabled

    @pyqtProperty(QVariant, notify=settingsChanged)
    def settingsRecoverySensorNames(self):
        return list(self._config().recovery_sensor_names)

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsRecoveryAutoPause(self):
        return self._config().recovery_auto_pause

    @pyqtProperty(str, notify=settingsChanged)
    def settingsRecoverySavedState(self):
        return self._config().recovery_saved_state

    @pyqtProperty(str, notify=settingsChanged)
    def settingsRecoveryPrepareMacro(self):
        return self._config().recovery_prepare_macro

    @pyqtProperty(QVariant, notify=settingsChanged)
    def settingsRecoverySetupMacros(self):
        return list(self._config().recovery_setup_macros)

    @pyqtProperty(QVariant, notify=settingsChanged)
    def settingsRecoveryTransparentMacros(self):
        return list(self._config().recovery_transparent_macros)

    @pyqtProperty(str, notify=settingsChanged)
    def settingsRecoveryNozzleParameter(self):
        return self._config().recovery_nozzle_parameter

    @pyqtProperty(str, notify=settingsChanged)
    def settingsRecoveryBedParameter(self):
        return self._config().recovery_bed_parameter

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryLeadIn(self):
        return self._config().recovery_lead_in_mm

    @pyqtProperty(str, notify=settingsChanged)
    def settingsRecoveryLeadInMode(self):
        return self._config().recovery_lead_in_mode

    @pyqtProperty(int, notify=settingsChanged)
    def settingsRecoveryLeadInMoves(self):
        return self._config().recovery_lead_in_moves

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryClearance(self):
        return self._config().recovery_clearance_mm

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryXySpeed(self):
        return self._config().recovery_xy_speed_mm_s

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryZSpeed(self):
        return self._config().recovery_z_speed_mm_s

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsRecoveryPrimeEnabled(self):
        return self._config().recovery_prime_enabled

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryPrimeX(self):
        return self._config().recovery_prime_x

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryPrimeY(self):
        return self._config().recovery_prime_y

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryPrimeZ(self):
        return self._config().recovery_prime_z

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryPrimeAmount(self):
        return self._config().recovery_prime_amount_mm

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryPrimeSpeed(self):
        return self._config().recovery_prime_speed_mm_s

    @pyqtProperty(float, notify=settingsChanged)
    def settingsRecoveryPrimeDwell(self):
        return self._config().recovery_prime_dwell_s

    @pyqtProperty(int, notify=settingsChanged)
    def settingsRecoveryCommandTimeout(self):
        return self._config().recovery_command_timeout_s

    @pyqtProperty(int, notify=settingsChanged)
    def settingsRecoveryHeatingTimeout(self):
        return self._config().recovery_heating_timeout_s

    @pyqtProperty(int, notify=settingsChanged)
    def settingsRecoveryStatusTimeout(self):
        return self._config().recovery_status_timeout_s

    @pyqtProperty(QVariant, notify=recoveryDiscoveryChanged)
    def recoveryAvailableSensors(self):
        return [{"name": name, "label": name + (" (Klipper pauses)" if self._recovery_sensor_pause.get(name) == "klipper"
                                                   else " (follower pauses)" if self._recovery_sensor_pause.get(name) == "follower"
                                                   else " (pause owner unknown)"),
                 "pauseOwner": self._recovery_sensor_pause.get(name) or "unknown"}
                for name in self._recovery_sensors]

    @pyqtProperty(QVariant, notify=recoveryDiscoveryChanged)
    def recoverySavedStates(self):
        return list(self._recovery_saved_states)

    @pyqtProperty(QVariant, notify=recoveryDiscoveryChanged)
    def recoveryPrepareMacros(self):
        return list(self._recovery_prepare_macros)

    @pyqtProperty(str, notify=recoveryDiscoveryChanged)
    def recoveryDiscoveryStatus(self):
        return self._recovery_discovery_status

    @pyqtSlot()
    def refreshRecoveryConfiguration(self):
        self._recovery_discovery_status = "Reading Klipper configuration…"
        self.recoveryDiscoveryChanged.emit()
        transport = getattr(self._follower, "transport", None)
        if transport is None:
            self._recovery_discovery_status = "Printer configuration is unavailable."
            self.recoveryDiscoveryChanged.emit()
            return

        def received(payload, error):
            configfile = ((payload or {}).get("result") or {}).get("status") or {}
            config = (configfile.get("configfile") or {}).get("config") or {}
            if error or not isinstance(config, dict):
                self._recovery_discovery_status = "Printer configuration is unavailable."
                self.recoveryDiscoveryChanged.emit()
                return
            self._recovery_sensors = sorted(name for name in config
                if name.startswith(("filament_switch_sensor ", "filament_motion_sensor ")))
            from ..printing.RunoutEvents import pause_owner
            self._recovery_sensor_pause = {name: pause_owner(config, name) for name in self._recovery_sensors}
            pause = config.get("gcode_macro PAUSE") or config.get("gcode_macro pause") or {}
            body = str(pause.get("gcode") or "") if isinstance(pause, dict) else ""
            states = re.findall(r"\bSAVE_GCODE_STATE\s+NAME=([A-Z][A-Z0-9_]*)", body, re.I)
            self._recovery_saved_states = list(dict.fromkeys(["PAUSE_STATE"] + [s.upper() for s in states]))
            self._recovery_prepare_macros = sorted(name.removeprefix("gcode_macro ").upper()
                for name in config if name.startswith("gcode_macro "))
            self._recovery_discovery_status = (f"Found {len(self._recovery_sensors)} filament sensor(s)."
                                               if self._recovery_sensors else "No filament sensors reported by this printer.")
            self.recoveryDiscoveryChanged.emit()

        started = transport.send_json(
            "recovery-settings", "config", "POST", "printer/objects/query", received,
            body={"objects": {"configfile": ["config"]}}, replace=True,
            category="discovery")
        if not started:
            self._recovery_discovery_status = "Could not request printer configuration."
            self.recoveryDiscoveryChanged.emit()

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
    def settingsPathSmoothing(self) -> bool:
        return self._config().path_smoothing

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsEtaLearn(self) -> bool:
        return self._config().eta_learn

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsAutoPreview(self) -> bool:
        return self._config().auto_preview

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsToolheadIndicator(self) -> bool:
        return self._config().show_toolhead_indicator

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsTraceLayer(self) -> bool:
        return self._config().trace_layer

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsSeekTrace(self) -> bool:
        return self._config().seek_trace

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsMemoryDiagnosticsLog(self) -> bool:
        return self._config().memory_diagnostics_log

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsSoftwareFollowerRenderer(self) -> bool:
        return self._config().software_follower_renderer

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsCameraDisabled(self) -> bool:
        return self._config().camera_disabled

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsDetectionEnabled(self) -> bool:
        return self._config().detection_enabled

    @pyqtProperty(int, notify=settingsChanged)
    def settingsDetectionWarningThreshold(self) -> int:
        return self._config().detection_warning_threshold

    @pyqtProperty(int, notify=settingsChanged)
    def settingsDetectionFailureThreshold(self) -> int:
        return self._config().detection_failure_threshold

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionReady(self) -> bool:
        return bool(self._detection is not None and self._detection.ready)

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionGlobalEnabled(self) -> bool:
        return bool(self._detection is not None and self._detection.ready
                    and self._detection.enabled)

    @pyqtSlot(bool, result=bool)
    def setDetectionGlobalEnabled(self, enabled: bool) -> bool:
        if self._detection is None:
            self._detection_refusal = "Local detection is unavailable"
            self.detectionChanged.emit()
            return False
        return self._detection.set_enabled(enabled)

    @pyqtProperty(str, notify=detectionChanged)
    def detectionHostError(self) -> str:
        return self._detection.host_error if self._detection is not None else "Local detection is unavailable"

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionBusy(self) -> bool:
        return bool(self._detection is not None and self._detection.busy)

    @pyqtProperty(str, notify=detectionChanged)
    def detectionPhase(self) -> str:
        return self._detection.phase if self._detection is not None else ""

    @pyqtProperty(int, notify=detectionChanged)
    def detectionReceived(self) -> int:
        return self._detection.received if self._detection is not None else 0

    @pyqtProperty(int, notify=detectionChanged)
    def detectionTotal(self) -> int:
        return self._detection.total if self._detection is not None else 0

    @pyqtProperty(str, notify=detectionChanged)
    def detectionError(self) -> str:
        return self._detection.error if self._detection is not None else ""

    @pyqtProperty(int, notify=detectionChanged)
    def detectionBenchmarkMs(self) -> int:
        return self._detection.benchmark_ms if self._detection is not None else 0

    @pyqtSlot()
    def revealDetectionEvidence(self) -> None:
        """The Diagnostics tab's reveal: the alert frames and the
        per-print score timelines, in the file manager."""
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        if self._detection is None:
            self._detection_evidence_status = "Local detection is unavailable"
        else:
            root = self._detection.evidence_root()
            QDesktopServices.openUrl(QUrl.fromLocalFile(root))
            self._detection_evidence_status = "Opened " + root
        self.detectionEvidenceStatusChanged.emit()

    @pyqtProperty(str, notify=detectionEvidenceStatusChanged)
    def detectionEvidenceStatus(self) -> str:
        return self._detection_evidence_status

    @pyqtProperty(bool, notify=detectionChanged)
    def detectionCameraReady(self) -> bool:
        monitor = self._output_plugin._current_monitor() if self._output_plugin is not None else None
        return bool(monitor is not None and monitor._camera.url
                    and monitor.webcamStreamEnabled and not self._config().camera_disabled)

    @pyqtProperty(QObject, notify=settingsChanged)
    def detectionMonitor(self):
        return self._output_plugin._current_monitor() if self._output_plugin is not None else None

    @pyqtProperty(str, notify=detectionChanged)
    def detectionRefusal(self) -> str:
        return self._detection_refusal

    @pyqtProperty(int, constant=True)
    def detectionRuntimeSize(self) -> int:
        from ..detection.DetectionAssets import host_wheel
        try:
            return host_wheel().size
        except ValueError:
            return 0

    @pyqtSlot()
    def startDetectionSetup(self) -> None:
        if self._detection is not None:
            self._detection.setup()

    @pyqtSlot()
    def cancelDetectionSetup(self) -> None:
        if self._detection is not None:
            self._detection.cancel()

    @pyqtSlot()
    def declineDetectionOffer(self) -> None:
        if self._detection is not None:
            self._detection.decline_offer()

    @pyqtProperty(str, notify=onboardingResetStatusChanged)
    def onboardingResetStatus(self) -> str:
        return self._onboarding_reset_status

    @pyqtSlot()
    def resetOnboardingForNextRun(self) -> None:
        persistence = getattr(self._follower, "persistence", None)
        if persistence is None or self._detection is None:
            status = "Cannot reset onboarding: local detection or settings storage is unavailable."
        else:
            if not persistence.merge_state_global({"whatsNewSeen": ""}):
                status = "Could not reset What's New; check Cura's preferences directory."
            else:
                monitor = self._output_plugin._current_monitor() if self._output_plugin is not None else None
                if monitor is not None:
                    monitor._whats_new_seen = ""
                try:
                    self._detection.reset_offer()
                except OSError as exc:
                    status = f"What's New reset, but local detection could not be reset: {exc}"
                else:
                    status = "What's New and local detection offer will appear on the next Cura run."
        if not status.startswith("What's New and"):
            Logger.log("w", "Moonraker Print Follower: %s", status)
        self._onboarding_reset_status = status
        self.onboardingResetStatusChanged.emit()

    @pyqtProperty(str, notify=detectionResetStatusChanged)
    def detectionResetStatus(self) -> str:
        return self._detection_reset_status

    def _on_detection_reset_progress(self):
        if not self._detection_reset_pending or self._detection.busy:
            return
        self._detection_reset_pending = False
        if self._detection.error:
            self._detection_reset_status = "Could not remove local detection assets: " + self._detection.error
            Logger.log("w", "Moonraker Print Follower: %s", self._detection_reset_status)
        else:
            self._detection_reset_status = "Local detection downloads removed. Set up the model again in Detection settings."
        self.detectionResetStatusChanged.emit()

    @pyqtSlot()
    def resetDetectionAssets(self):
        persistence = getattr(self._follower, "persistence", None)
        if persistence is None or self._detection is None:
            status = "Cannot remove detection downloads: setup or settings storage is unavailable."
        elif self._detection.busy:
            status = "Wait for local detection setup to finish or cancel it first."
        elif not persistence.disable_all_detection():
            status = "Could not disable detection for all printers; no downloads were removed."
        else:
            config = self._config()
            disabled = replace(config, detection_enabled=False,
                               detection_notify_enabled=False, detection_pause_enabled=False,
                               detection_regions={})
            if self._follower.apply_printer_config(disabled) is False:
                status = "Detection disabled in storage, but this printer's live settings could not refresh."
            elif not self._detection.remove_assets():
                status = "Could not start download removal: " + self._detection.error
            else:
                self._detection_reset_pending = True
                status = "Removing local detection downloads…"
        if not status.startswith("Removing"):
            Logger.log("w", "Moonraker Print Follower: %s", status)
        self._detection_reset_status = status
        self.detectionResetStatusChanged.emit()

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsMemoryDiagnosticsTrace(self) -> bool:
        return self._config().memory_diagnostics_trace

    @pyqtProperty(str, notify=settingsChanged)
    def settingsAuxInterval(self) -> str:
        return str(self._config().aux_interval_ms)

    @pyqtProperty(str, notify=settingsChanged)
    def settingsConsoleInterval(self) -> str:
        return str(self._config().console_interval_ms)

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTransportMode(self) -> str:
        return str(getattr(self._config().feed_mode, "value", self._config().feed_mode))

    @pyqtProperty(str, notify=settingsChanged)
    def transportStatus(self) -> str:
        # The permanent reason slot under the transport radios (the UX
        # adjudication): the helper sentence until the live feed reports
        # a capability verdict.
        return (
            "WebSocket lets Moonraker push status changes to Cura. "
            "HTTP polling asks the printer for them at the interval below. "
            "Commands, uploads and the console always use HTTP."
        )

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsTraceHttp(self) -> bool:
        return self._config().trace_http

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsZFallback(self) -> bool:
        return self._config().z_fallback

    @pyqtProperty(str, notify=settingsChanged)
    def settingsZTolerance(self) -> str:
        return f"{self._config().z_tolerance:.3f}"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsCacheMaxMb(self) -> str:
        return str(self._config().cache_max_mb)

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

    @pyqtProperty(str, notify=cacheStatusChanged)
    def cacheStatus(self) -> str:
        return self._cache_status

    @pyqtProperty(str, notify=cadResetStatusChanged)
    def cadResetStatus(self) -> str:
        return self._cad_reset_status

    @pyqtSlot()
    def clearCadDownloads(self) -> None:
        """Clear optional STEP runtime files without removing saved models."""
        if self._toolhead_models is None:
            status = "CAD runtime is unavailable."
        else:
            try:
                removed = self._toolhead_models.clear_runtime_downloads()
            except (OSError, ValueError) as error:
                status = f"Could not clear CAD downloads: {error}"
            else:
                status = ("CAD downloads removed. The next STEP import will download them again."
                          if removed else "No CAD downloads were cached.")
        self._cad_reset_status = status
        self.cadResetStatusChanged.emit()

    @staticmethod
    def _url_is_usable(value: str) -> bool:
        parsed = QUrl(value)
        return parsed.isValid() and parsed.scheme() in ("http", "https") and bool(parsed.host())

    @pyqtSlot(str, result=bool)
    def validUrl(self, value: str) -> bool:
        # A malformed bracketed host makes urlsplit raise — a live
        # slot exception aborts Cura, so the validator refuses
        # instead (the 2026-09-19 coverage round's find).
        try:
            return self._url_is_usable(normalise_url(value))
        except ValueError:
            return False

    @pyqtSlot(str, str, result=bool)
    def insecureKeyWarning(self, url: str, key: str) -> bool:
        """True when an API key would be sent in cleartext: the scheme
        is plain http and the host is not loopback. The plugin never
        refuses — plain-http LAN Moonraker is the normal deployment —
        but the Connection tab must say so (panel security P2-1)."""
        if not str(key or "").strip():
            return False
        try:
            text = normalise_url(url)
        except ValueError:
            return False  # a malformed host reads as no warning, never a crash
        parsed = QUrl(text)
        if not (parsed.isValid() and parsed.scheme().lower() == "http"):
            return False
        host = str(parsed.host() or "").lower()
        if host == "localhost":
            return False
        address = QHostAddress(host)
        return not (not address.isNull() and address.isLoopback())

    @pyqtSlot(str, result=bool)
    def validPollInterval(self, value: str) -> bool:
        try:
            return 250 <= int(str(value).strip()) <= 3_600_000
        except (TypeError, ValueError):
            return False

    @pyqtSlot(str, result=bool)
    def validAuxInterval(self, value: str) -> bool:
        try:
            return 250 <= int(str(value).strip()) <= 60_000
        except (TypeError, ValueError):
            return False

    @pyqtSlot(str, result=bool)
    def validConsoleInterval(self, value: str) -> bool:
        try:
            return 250 <= int(str(value).strip()) <= 60_000
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
    def validCacheMax(self, value: str) -> bool:
        try:
            size = int(str(value).strip())
        except (TypeError, ValueError):
            return False
        return 16 <= size <= 4096

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

            # The sliders deliver JS numbers (e.g. 250.0); the legacy
            # text fields delivered digit strings. Accept both. Every
            # refusal names its reason in the log — a save that fails
            # validation must never fail silently (the toggle-revert
            # report: the dialog accepted nothing and said nothing).
            try:
                interval = int(float(str(raw.get("poll_interval_ms", "")).strip()))
                aux_interval = int(float(str(raw.get("aux_interval_ms", "")).strip()))
                console_interval = int(float(str(raw.get("console_interval_ms", "")).strip()))
                tolerance = float(str(raw.get("z_tolerance", "")).strip())
                retry_interval = float(str(raw.get("ready_retry_interval_s", "")).strip())
                raw_cache = str(raw.get("cache_max_mb") or "").strip()
                cache_max = int(raw_cache) if raw_cache \
                    else getattr(self._config(), "cache_max_mb", 2048)
            except ValueError as exc:
                Logger.log("w", "Moonraker settings save refused: unparsable field (%s)", exc)
                return False
            url = normalise_url(str(raw.get("url", "")))
            enabled = bool(raw.get("enabled", False))
            if not (250 <= interval <= 3_600_000):
                Logger.log("w", "Moonraker settings save refused: poll interval out of range")
                return False
            if not (250 <= aux_interval <= 60_000) or not (250 <= console_interval <= 60_000):
                Logger.log("w", "Moonraker settings save refused: aux/console interval out of range")
                return False
            if not (0.005 <= tolerance <= 0.250):
                Logger.log("w", "Moonraker settings save refused: z tolerance out of range")
                return False
            if not (0.1 <= retry_interval <= 60.0):
                Logger.log("w", "Moonraker settings save refused: retry interval out of range")
                return False
            if not (16 <= cache_max <= 4096):
                Logger.log("w", "Moonraker settings save refused: cache size out of range")
                return False
            if enabled and not self._url_is_usable(url):
                Logger.log("w", "Moonraker settings save refused: the URL is not usable")
                return False

            trans_input = str(raw.get("filename_translate_input") or "")
            trans_output = str(raw.get("filename_translate_output") or "")
            if len(trans_input) != len(trans_output):
                Logger.log("w", "Moonraker settings save refused: translate input/output lengths differ")
                return False

            mode = str(raw.get("follow_mode") or FollowMode.EXACT.value)
            if mode not in {item.value for item in FollowMode}:
                mode = FollowMode.EXACT.value

            current = self._config()
            recovery_sensors = raw.get("recovery_sensor_names", current.recovery_sensor_names)
            if not isinstance(recovery_sensors, list) or any(
                not isinstance(name, str) or
                not name.startswith(("filament_switch_sensor ", "filament_motion_sensor "))
                for name in recovery_sensors):
                Logger.log("w", "Moonraker settings save refused: invalid recovery sensor selection")
                return False
            permitted_sensors = set(self._recovery_sensors) | set(current.recovery_sensor_names)
            if not set(recovery_sensors) <= permitted_sensors:
                Logger.log("w", "Moonraker settings save refused: recovery sensor was not discovered")
                return False
            recovery_state = str(raw.get("recovery_saved_state", current.recovery_saved_state)).strip().upper()
            recovery_macro = str(raw.get("recovery_prepare_macro", current.recovery_prepare_macro)).strip().upper()
            nozzle_parameter = str(raw.get("recovery_nozzle_parameter", current.recovery_nozzle_parameter)).strip().upper()
            bed_parameter = str(raw.get("recovery_bed_parameter", current.recovery_bed_parameter)).strip().upper()
            if (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", nozzle_parameter)
                    or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", bed_parameter)):
                return False
            setup_macros = raw.get("recovery_setup_macros", current.recovery_setup_macros)
            transparent_macros = raw.get("recovery_transparent_macros", current.recovery_transparent_macros)
            permitted_macros = set(self._recovery_prepare_macros) | set(current.recovery_setup_macros) | set(current.recovery_transparent_macros)
            if (not isinstance(setup_macros, list) or not isinstance(transparent_macros, list)
                    or len(setup_macros) > 32 or len(transparent_macros) > 32
                    or any(not isinstance(name, str) or name not in permitted_macros
                           for name in setup_macros + transparent_macros)
                    or set(setup_macros) & set(transparent_macros)):
                Logger.log("w", "Moonraker settings save refused: invalid source macro selection")
                return False
            if (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", recovery_state)
                    or recovery_state not in set(self._recovery_saved_states) | {current.recovery_saved_state}
                    or (recovery_macro and
                        (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", recovery_macro)
                         or recovery_macro not in set(self._recovery_prepare_macros) | {current.recovery_prepare_macro}))):
                Logger.log("w", "Moonraker settings save refused: recovery macro or state was not discovered")
                return False
            recovery_numbers = {}
            recovery_mode = raw.get("recovery_lead_in_mode", current.recovery_lead_in_mode)
            if recovery_mode not in {"mm", "moves"}:
                return False
            try:
                recovery_moves = int(raw.get("recovery_lead_in_moves", current.recovery_lead_in_moves))
            except (TypeError, ValueError):
                return False
            if not 1 <= recovery_moves <= 100:
                return False
            if type(raw.get("recovery_auto_pause", current.recovery_auto_pause)) is not bool:
                Logger.log("w", "Moonraker settings save refused: recovery auto-pause must be a checkbox value")
                return False
            if type(raw.get("recovery_prime_enabled", current.recovery_prime_enabled)) is not bool:
                Logger.log("w", "Moonraker settings save refused: prime enable must be a checkbox value")
                return False
            for key, minimum, maximum in (("recovery_lead_in_mm", 0.1, 50.0),
                                          ("recovery_clearance_mm", 1.0, 30.0),
                                          ("recovery_xy_speed_mm_s", 1.0, 200.0),
                                          ("recovery_z_speed_mm_s", 0.5, 20.0),
                                          ("recovery_prime_x", 0.0, 1000.0),
                                          ("recovery_prime_y", 0.0, 1000.0),
                                          ("recovery_prime_z", 0.0, 1000.0),
                                          ("recovery_prime_amount_mm", 0.1, 200.0),
                                          ("recovery_prime_speed_mm_s", 0.1, 20.0),
                            ("recovery_prime_dwell_s", 0.0, 300.0)):
                try:
                    value = float(raw.get(key, getattr(current, key)))
                except (TypeError, ValueError):
                    return False
                if not minimum <= value <= maximum:
                    Logger.log("w", "Moonraker settings save refused: %s out of range", key)
                    return False
                recovery_numbers[key] = value
            try:
                recovery_timeout = int(raw.get("recovery_command_timeout_s", current.recovery_command_timeout_s))
            except (TypeError, ValueError):
                return False
            if not 10 <= recovery_timeout <= 600:
                Logger.log("w", "Moonraker settings save refused: recovery command timeout out of range")
                return False
            try:
                recovery_heating_timeout = int(raw.get("recovery_heating_timeout_s", current.recovery_heating_timeout_s))
            except (TypeError, ValueError, OverflowError):
                return False
            if not 60 <= recovery_heating_timeout <= 3600:
                Logger.log("w", "Moonraker settings save refused: recovery heating timeout out of range")
                return False
            try:
                recovery_status_timeout = int(raw.get("recovery_status_timeout_s", current.recovery_status_timeout_s))
            except (TypeError, ValueError, OverflowError):
                return False
            if not 3 <= recovery_status_timeout <= 60:
                Logger.log("w", "Moonraker settings save refused: recovery status timeout out of range")
                return False
            warning_threshold = raw.get("detection_warning_threshold", current.detection_warning_threshold)
            failure_threshold = raw.get("detection_failure_threshold", current.detection_failure_threshold)
            if (type(warning_threshold) is not int or type(failure_threshold) is not int
                    or not 0 <= warning_threshold < failure_threshold <= 100):
                Logger.log("w", "Moonraker settings save refused: detection thresholds must be ordered percentages")
                return False
            detection_enabled = raw.get("detection_enabled", current.detection_enabled)
            if not isinstance(detection_enabled, bool):
                Logger.log("w", "Moonraker settings save refused: detection_enabled must be a checkbox value")
                return False
            if detection_enabled and not current.detection_enabled \
                    and (not self.detectionGlobalEnabled or not self.detectionCameraReady
                         or bool(raw.get("camera_disabled", current.camera_disabled))):
                self._detection_refusal = (
                    "Enable local detection in Settings and select a working camera before enabling this printer."
                )
                self.detectionChanged.emit()
                Logger.log("w", "Moonraker settings save refused: %s", self._detection_refusal)
                return False
            self._detection_refusal = ""
            self.detectionChanged.emit()
            # The mode is a validated two-literal choice: an unknown value
            # keeps the current one — never a silent default (UX-M7).
            feed_mode = str(raw.get("feed_mode") or "").strip().lower()
            if feed_mode not in ("websocket", "http"):
                feed_mode = str(getattr(current.feed_mode, "value", current.feed_mode))
            data = asdict(current)
            data.update({
                "enabled": enabled,
                "url": url,
                "api_key": str(raw.get("api_key") or "").strip(),
                "poll_interval_ms": interval,
                "aux_interval_ms": aux_interval,
                "console_interval_ms": console_interval,
                "cache_max_mb": cache_max,
                "cache_max_mb_explicit": bool(raw_cache) or current.cache_max_mb_explicit,
                "moonraker_layer_is_one_based": bool(raw.get("moonraker_layer_is_one_based", True)),
                "auto_preview": bool(raw.get("auto_preview", False)),
                "z_fallback": bool(raw.get("z_fallback", True)),
                "z_tolerance": tolerance,
                "path_follow": bool(raw.get("path_follow", True)),
                "path_smoothing": bool(raw.get("path_smoothing", True)),
                "eta_learn": bool(raw.get("eta_learn", False)),
                "show_toolhead_indicator": bool(raw.get("show_toolhead_indicator", True)),
                "trace_layer": bool(raw.get("trace_layer", False)),
                "seek_trace": bool(raw.get("seek_trace", False)),
                "trace_http": bool(raw.get("trace_http", False)),
                "memory_diagnostics_log": bool(raw.get("memory_diagnostics_log", False)),
                "memory_diagnostics_trace": bool(raw.get("memory_diagnostics_trace", False)),
                "camera_disabled": bool(raw.get("camera_disabled", False)),
                "detection_enabled": detection_enabled,
                "recovery_sensor_names": list(dict.fromkeys(recovery_sensors)),
                "recovery_auto_pause": raw.get("recovery_auto_pause", current.recovery_auto_pause),
                "recovery_prime_enabled": raw.get("recovery_prime_enabled", current.recovery_prime_enabled),
                "recovery_saved_state": recovery_state,
                "recovery_prepare_macro": recovery_macro,
                "recovery_setup_macros": list(dict.fromkeys(setup_macros)),
                "recovery_transparent_macros": list(dict.fromkeys(transparent_macros)),
                "recovery_nozzle_parameter": nozzle_parameter,
                "recovery_bed_parameter": bed_parameter,
                "recovery_lead_in_mode": recovery_mode,
                "recovery_lead_in_moves": recovery_moves,
                **recovery_numbers,
                "recovery_command_timeout_s": recovery_timeout,
                "recovery_heating_timeout_s": recovery_heating_timeout,
                "recovery_status_timeout_s": recovery_status_timeout,
                "detection_warning_threshold": warning_threshold,
                "detection_failure_threshold": failure_threshold,
                "software_follower_renderer": bool(raw.get("software_follower_renderer", False)),
                "feed_mode": feed_mode,
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
            if self._toolhead_models is not None:
                data.update(self._toolhead_models.fields())
            config = PrinterConfig.from_dict(data)
            saved = self._follower.apply_printer_config(config)
            if saved is False:
                # A refused persistence write is not a saved setting: the
                # dialog stays open on its refusal label (saveRefused in
                # the QML) rather than closing over a lost change. `is
                # False` (not falsy): a facade that returns nothing —
                # the harness doubles, a build without persistence —
                # still counts as a save, never as a refusal.
                Logger.log("w", "Moonraker settings save refused: the settings file could not be written")
                return False
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
        base_url = normalise_url(url)
        if not self._url_is_usable(base_url):
            self._set_test_state("Enter a valid Moonraker URL", busy=False)
            return
        if self._test_busy:
            return

        self._probe_base_url = base_url
        self._probe_api_key = str(api_key or "").strip()
        self._probe_server_info = {}
        self._probe_transport.configure(self._probe_base_url, self._probe_api_key)
        self._set_test_state("Testing connection…", busy=True)
        self._start_probe_request("server-info", server_info_endpoint(base_url), self._handle_probe_server_info)

    def _start_probe_request(self, channel: str, endpoint: str, handler: Any) -> None:
        started = self._probe_transport.send_json(
            "probe",
            channel,
            "GET",
            endpoint,
            handler,
            replace=True,
            category=RequestCategory.DISCOVERY.value,
        )
        if not started:
            self._set_test_state("A connection test request is already in progress", busy=False)

    def _handle_probe_server_info(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            text = str(error)[:160] + ("…" if len(str(error)) > 160 else "")
            self._set_test_state(f"Connection failed: {text}", busy=False)
            return
        try:
            self._probe_server_info = (payload or {}).get("result") or {}
        except Exception as exc:
            self._set_test_state(f"Invalid server response: {exc}", busy=False)
            return
        self._start_probe_request(
            "objects",
            objects_list_endpoint(self._probe_base_url),
            self._handle_probe_objects,
        )

    def _handle_probe_objects(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            self._set_test_state(f"Printer-object test failed: {error}", busy=False)
            return
        try:
            objects = set(str(value) for value in (((payload or {}).get("result") or {}).get("objects") or []))
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

    def _cache_root(self) -> str:
        # Same composition as FollowerRuntime's cache directory (the
        # shared constant): the persistent index cache and the
        # diagnostics traces live under it. The session's downloaded
        # FILE is a temp directory and disappears when Cura exits.
        return os.path.join(Resources.getCacheStoragePath(), CACHE_DIRECTORY_NAME)

    @pyqtSlot()
    def clearCache(self) -> None:
        """The Diagnostics tab's cache-clear: drop the persistent index
        cache so the next Improve-ETA re-downloads and re-indexes (a
        request for a re-testable download flow). The sweep covers
        EVERY generation the plugin ever used — the legacy
        package-ID-named directory included — never only the current
        cache-v2 subtree (the live ruling)."""
        try:
            # The coordinated lifecycle (the review's cache-clear
            # finding): the index service retires its active work
            # and its writer FIRST — the invalidate cancels the
            # worker, freezes the prepared writer and bumps the
            # generation — so no worker holds a file the sweep is
            # about to delete. Then the sweep removes EVERY
            # generation the plugin ever used: the legacy
            # package-ID-named directory AND the current renamed one
            # (the live ruling — never only the cache-v2 subtree).
            # A directory that refuses to go (Windows file locks
            # from a retiring worker) is REPORTED, never silently
            # ignored.
            invalidate = getattr(self._follower, "invalidateIndex", None)
            if invalidate is not None:
                invalidate()
            refused = []
            for path in (os.path.join(Resources.getCacheStoragePath(),
                                      "MoonrakerPrintFollower"),
                         self._cache_root()):
                try:
                    shutil.rmtree(path)
                except FileNotFoundError:
                    pass  # an absent generation is a successful clear
                except OSError:
                    refused.append(path)
            if refused:
                self._cache_status = ("Cache partially cleared — some files could not "
                                      "be removed. Restart Cura to also drop the "
                                      "session's downloaded file.")
            else:
                self._cache_status = "Cache cleared. Restart Cura to also drop the session's downloaded file."
        except Exception as error:
            Logger.log("w", "Moonraker Print Follower: cache clear failed: %s", error)
            self._cache_status = "Could not clear the cache — see Cura's log."
        self.cacheStatusChanged.emit()

    @pyqtSlot()
    def cancelTest(self) -> None:
        self._probe_transport.cancel_owner("probe")
        if self._test_busy:
            self._test_busy = False
            self.testBusyChanged.emit()
