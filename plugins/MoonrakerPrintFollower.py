"""Cura extension that makes Preview follow a remote Klipper/Moonraker print.

Moonraker status is read through bounded HTTP polling with automatic retry
backoff. No third-party Python packages are required; the plugin uses Cura's
bundled Qt networking facilities.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QMessageBox

from UM.Backend.Backend import BackendState
from UM.Extension import Extension
from UM.Logger import Logger
from UM.Resources import Resources

from .Core import OperationContext, OperationPhase, RemoteFileIdentity, preview_override_kind
from .CuraAdapter import active_machine_identity, apply_preview_decision, preview_head_position
from .FollowController import FollowController, FollowMode, FollowState, decide_layers
from .MoonrakerClient import MoonrakerClient
from .PrinterConfig import PrinterConfig, PrinterConfigStore
from .ToolheadIndicator import ToolheadIndicatorNode
from .DownloadStream import DownloadTarget
from .GCodeIndex import (
    LayerMotionIndex,
    PersistentIndexCache,
    build_index_from_file,
    hydrate_layer_from_file,
)
from .MoonrakerProtocol import (
    download_endpoint,
    live_position_in_gcode_space,
    metadata_endpoint,
    parse_file_identity,
    status_endpoint,
)



class MoonrakerPrintFollower(QObject, Extension):
    """Synchronise Cura's SimulationView layer with a Moonraker print."""

    _remoteIndexReady = pyqtSignal(int, str, object, int, int)
    _remoteLayerHydrated = pyqtSignal(int, int, bool)

    PLUGIN_ID = "Moonraker_Print_Follower"
    PREF_ROOT = "moonraker_print_follower"

    PREF_ENABLED = f"{PREF_ROOT}/enabled"
    PREF_URL = f"{PREF_ROOT}/url"
    PREF_API_KEY = f"{PREF_ROOT}/api_key"
    PREF_INTERVAL = f"{PREF_ROOT}/poll_interval_ms"
    PREF_ONE_BASED = f"{PREF_ROOT}/moonraker_layer_is_one_based"
    PREF_AUTO_PREVIEW = f"{PREF_ROOT}/auto_preview"
    PREF_Z_FALLBACK = f"{PREF_ROOT}/z_fallback"
    PREF_Z_TOLERANCE = f"{PREF_ROOT}/z_tolerance"
    PREF_PATH_FOLLOW = f"{PREF_ROOT}/path_follow"

    ACTIVE_STATES = {"printing", "paused"}

    @property
    def _force_load_requested(self) -> bool:
        return bool(self._operation.force_load)

    @_force_load_requested.setter
    def _force_load_requested(self, value: bool) -> None:
        self._operation.force_load = bool(value)
        if value and self._operation.phase == OperationPhase.IDLE:
            self._operation.phase = OperationPhase.RESOLVING

    @property
    def _force_load_pending_filename(self) -> Optional[str]:
        return self._operation.filename if self._operation.force_load else None

    @_force_load_pending_filename.setter
    def _force_load_pending_filename(self, value: Optional[str]) -> None:
        if value is not None:
            self._operation.filename = value
            self._operation.force_load = True
        elif self._operation.phase != OperationPhase.CURA_LOADING:
            self._operation.filename = None

    @property
    def _cura_load_in_progress(self) -> bool:
        return self._operation.phase == OperationPhase.CURA_LOADING

    @_cura_load_in_progress.setter
    def _cura_load_in_progress(self, value: bool) -> None:
        if value:
            self._operation.phase = OperationPhase.CURA_LOADING
        elif self._operation.phase == OperationPhase.CURA_LOADING:
            self._operation.phase = OperationPhase.READY

    @property
    def _cura_load_started_at(self) -> Optional[float]:
        return self._operation.started_at

    @_cura_load_started_at.setter
    def _cura_load_started_at(self, value: Optional[float]) -> None:
        self._operation.started_at = value

    def _set_operation_phase(
        self, phase: OperationPhase, *, filename: Optional[str] = None, message: str = ""
    ) -> None:
        self._operation.transition(
            phase,
            filename=filename,
            job_key=self._remote_job_key if hasattr(self, "_remote_job_key") else None,
            message=message,
        )
        self._sync_preview_button_state()

    def __init__(self, application) -> None:
        QObject.__init__(self)
        Extension.__init__(self)
        self._application = application
        self._preferences = application.getPreferences()
        self._controller = application.getController()
        self._operation = OperationContext()

        self._register_preferences()
        self._config_store = PrinterConfigStore(
            self._preferences, lambda: active_machine_identity(self._application)
        )
        self._config_store.migrate_legacy_to_current_machine()
        self._active_machine_id, self._active_machine_name = self._config_store.identity()
        self._follow_controller = FollowController()
        self._follow_controller.set_enabled(self._config_store.get().enabled)
        self._last_capabilities: Dict[str, Any] = {}

        self._client = MoonrakerClient(self)
        self._client.statusReceived.connect(self._on_client_status)
        self._client.connectionChanged.connect(self._on_client_connection_changed)
        self._client.capabilitiesChanged.connect(self._on_client_capabilities_changed)

        self._network = QNetworkAccessManager()
        self._reply: Optional[QNetworkReply] = None
        self._reply_purpose: Optional[str] = None

        # A separate network manager is used for the one-time G-code download so
        # regular status polling can continue while a large file is being indexed.
        self._file_network = QNetworkAccessManager()
        self._file_reply: Optional[QNetworkReply] = None
        self._file_reply_filename: Optional[str] = None
        self._file_reply_generation = 0
        self._file_reply_job_key: Optional[Tuple[str, int, int]] = None
        self._file_download_target: Optional[DownloadTarget] = None

        self._metadata_network = QNetworkAccessManager()
        self._metadata_reply: Optional[QNetworkReply] = None
        self._metadata_filename: Optional[str] = None
        self._metadata_job_key: Optional[Tuple[str, int, int]] = None
        self._metadata_reply_generation = 0
        self._metadata_reply_job_key: Optional[Tuple[str, int, int]] = None
        self._remote_file_identity: Optional[RemoteFileIdentity] = None

        cache_dir = os.path.join(Resources.getCacheStoragePath(), self.PLUGIN_ID, "indexes")
        self._persistent_index_cache = PersistentIndexCache(cache_dir)
        self._cache_save_threads: set[threading.Thread] = set()


        self._last_status_text = "Not connected"
        self._last_remote_filename: Optional[str] = None
        self._last_remote_state: Optional[str] = None
        self._last_extruder_position: Optional[float] = None
        self._preview_switched_for_job = False
        self._last_source: Optional[str] = None
        self._force_load_requested = False
        self._force_load_pending_filename: Optional[str] = None
        self._following_paused = False
        self._follow_controller.resume()

        # Detect direct user interaction with Cura's Preview layer/path controls.
        # SimulationView does not expose a stable public "user changed slider"
        # signal across Cura 5.x, so watch the public current-value API instead.
        # Values written by this plugin establish the expected position; any later
        # deviation while following is active is a manual override and pauses the
        # session without changing the saved enabled preference.
        self._manual_view_watch_timer = QTimer()
        self._manual_view_watch_timer.setInterval(75)
        self._manual_view_watch_timer.timeout.connect(self._watch_for_manual_preview_change)
        self._manual_view_signals_connected = False
        self._applying_follow_update = 0
        self._expected_follow_layer: Optional[int] = None
        self._expected_follow_minimum_layer: Optional[int] = None
        self._expected_follow_path: Optional[float] = None
        self._expected_follow_minimum_path: Optional[int] = None

        self._preview_overlay = None
        self._action_panel_controls = None
        self._connected_simulation_view = None
        self._toolhead_indicator = ToolheadIndicatorNode()
        self._toolhead_path_valid = False
        self._scene = None
        self._scene_root = None
        self._lifecycle_generation = 0
        self._destroyed = False
        self._slicing_in_progress = False
        self._scene_settle_until = 0.0

        # Identify a print independently from its filename. Moonraker retains
        # print_stats after completion and users often reprint the same file.
        # A monotonically increasing serial prevents stale cache/index reuse
        # when file_position or print_duration resets for a new run.
        self._remote_job_serial = 0
        self._remote_job_key: Optional[Tuple[str, int, int]] = None
        self._last_remote_file_position: Optional[int] = None
        self._last_remote_print_duration: Optional[float] = None

        # Keep the downloaded G-code available for the duration of the Cura
        # session. Cura reads G-code asynchronously, so the file must outlive
        # the call to readLocalFile(). TemporaryDirectory cleans it up when
        # Cura exits.
        self._temp_gcode_dir = tempfile.TemporaryDirectory(
            prefix="cura-moonraker-print-follower-"
        )
        self._cached_gcode_filename: Optional[str] = None
        self._cached_gcode_path: Optional[str] = None
        self._cached_gcode_job_key: Optional[Tuple[str, int, int]] = None
        # Keep Cura's in-flight file identity separate from the reusable cache.
        # A new Moonraker job may invalidate the cache while Cura is still
        # asynchronously finishing a read of the previous file.
        self._cura_load_path: Optional[str] = None
        self._cura_load_filename: Optional[str] = None
        self._cura_load_job_key: Optional[Tuple[str, int, int]] = None
        self._deferred_cache_dirs: set[str] = set()

        # Remote G-code index used for within-layer progress. Each entry stores
        # the layer byte range plus byte offsets of G0/G1/G2/G3 motion commands.
        # Arrays keep memory usage reasonable even for very large files.
        self._remote_index_filename: Optional[str] = None
        self._remote_index_job_key: Optional[Tuple[str, int, int]] = None
        self._remote_layer_ranges: List[Tuple[int, int]] = []
        self._remote_motion_offsets: List[Any] = []
        self._remote_current_layer_map: Dict[int, int] = {}
        self._remote_index_data: Optional[LayerMotionIndex] = None
        self._remote_index_build_filename: Optional[str] = None
        self._remote_index_build_job_key: Optional[Tuple[str, int, int]] = None
        self._remote_index_generation = 0
        self._remote_index_cancel_event: Optional[threading.Event] = None
        self._remote_index_thread: Optional[threading.Thread] = None
        self._cura_load_in_progress = False
        self._cura_load_started_at: Optional[float] = None
        self._remoteIndexReady.connect(self._on_remote_index_ready)
        self._remoteLayerHydrated.connect(self._on_remote_layer_hydrated)
        self._hydrating_layers: set[int] = set()
        self._hydration_threads: set[threading.Thread] = set()

        file_completed = getattr(self._application, "fileCompleted", None)
        if file_completed is not None:
            try:
                file_completed.connect(self._on_cura_file_completed)
            except Exception:
                pass

        main_window_changed = getattr(self._application, "mainWindowChanged", None)
        if main_window_changed is not None:
            try:
                main_window_changed.connect(self._on_main_window_changed)
            except Exception:
                pass

        # SceneNode.childrenChanged propagates descendant additions/removals to
        # the root. It is the structural signal we actually need, unlike Cura's
        # broad sceneChanged notification which also fires for transforms/redraws.
        self._scene = None
        self._scene_root = None
        self._bind_scene_structure_signal()

        # Suspend Preview writes while CuraEngine is replacing layer data.
        # BackendState.Done is the authoritative completion point in Cura 5.13.
        try:
            backend = self._application.getBackend()
        except Exception:
            backend = None
        self._backend = backend
        if backend is not None:
            for signal_name, handler in (
                ("slicingStarted", self._on_slicing_started),
                ("slicingCancelled", self._on_slicing_cancelled),
                ("backendStateChange", self._on_backend_state_changed),
            ):
                signal = getattr(backend, signal_name, None)
                if signal is not None:
                    try:
                        signal.connect(handler)
                    except Exception:
                        pass

        global_stack_changed = getattr(self._application, "globalContainerStackChanged", None)
        if global_stack_changed is not None:
            try:
                global_stack_changed.connect(self._on_active_machine_changed)
            except Exception:
                pass
        active_view_changed = getattr(self._controller, "activeViewChanged", None)
        if active_view_changed is not None:
            try:
                active_view_changed.connect(self._on_active_view_changed)
            except Exception:
                pass

        # The controls have two placements: an empty-Preview overlay and Cura's
        # official action-panel extension row. Both remain strictly Preview-only.
        active_stage_changed = getattr(self._controller, "activeStageChanged", None)
        if active_stage_changed is not None:
            try:
                active_stage_changed.connect(self._sync_preview_controls_visibility)
            except Exception:
                pass

        try:
            if self._application.getMainWindow() is not None:
                self._create_preview_controls()
        except Exception:
            pass

        self._apply_timer_state()

    # ------------------------------------------------------------------
    # Preferences and UI
    # ------------------------------------------------------------------

    def _register_preferences(self) -> None:
        self._preferences.addPreference(self.PREF_ENABLED, False)
        self._preferences.addPreference(self.PREF_URL, "http://")
        self._preferences.addPreference(self.PREF_API_KEY, "")
        self._preferences.addPreference(self.PREF_INTERVAL, 750)
        self._preferences.addPreference(self.PREF_ONE_BASED, True)
        self._preferences.addPreference(self.PREF_AUTO_PREVIEW, False)
        self._preferences.addPreference(self.PREF_Z_FALLBACK, True)
        self._preferences.addPreference(self.PREF_Z_TOLERANCE, 0.04)
        self._preferences.addPreference(self.PREF_PATH_FOLLOW, True)

    def current_printer_config(self) -> PrinterConfig:
        """Return the active Cura printer's follower configuration."""
        return self._config_store.get()

    def current_printer_identity(self) -> Tuple[str, str]:
        """Return the active Cura machine id and human-readable name."""
        return self._config_store.identity()

    def apply_printer_config(self, config: PrinterConfig) -> None:
        """Persist and immediately apply configuration from the Machine Action QML."""
        self._config_store.set(config)
        self._follow_controller.set_enabled(config.enabled)
        if not config.enabled:
            self._following_paused = False
            self._follow_controller.resume()
            self._clear_expected_preview_position()
        self._apply_timer_state()
        self._sync_preview_button_state()
        self._update_toolhead_indicator()
        if config.enabled and self._url_is_usable(self._normalise_base_url(config.url)):
            self._client.force_refresh()

    def _toggle_following_pause(self) -> None:
        """Pause or resume Preview movement without changing saved preferences."""
        self._following_paused = not self._following_paused
        if self._following_paused:
            self._follow_controller.pause_by_user("pause button")
        else:
            self._follow_controller.resume()
        self._sync_preview_button_state()

        if self._following_paused:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status("Following paused; Moonraker connection remains active")
        else:
            view = self._simulation_view()
            if view is not None:
                self._remember_plugin_preview_position(view)
            else:
                self._clear_expected_preview_position()
            self._set_status("Following resumed; catching up to the current print")
            self._client.force_refresh()

    def _on_client_status(self, status) -> None:
        if not isinstance(status, dict):
            return
        print_stats = status.get("print_stats") or {}
        gcode_move = status.get("gcode_move") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        motion_report = status.get("motion_report") or {}
        self._follow_controller.set_connection(True)
        self._apply_remote_status(print_stats, gcode_move, virtual_sdcard, motion_report)

    def _on_client_connection_changed(self, connected: bool, detail: str) -> None:
        self._follow_controller.set_connection(bool(connected), connecting=not connected and self._pref_bool(self.PREF_ENABLED))
        if not connected:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
        if not connected and self._last_remote_state not in self.ACTIVE_STATES:
            self._set_status(detail)
        self._sync_preview_button_state()

    def _on_client_capabilities_changed(self, capabilities) -> None:
        self._last_capabilities = dict(capabilities or {})

    def _on_active_machine_changed(self, *_args) -> None:
        """Transfer the single follower session to Cura's newly active printer.

        There is intentionally only one MoonrakerClient in the plugin. A printer
        switch first stops that client and invalidates every in-flight operation
        owned by the old machine. Only after that teardown is complete do we bind
        the configuration for the new active Cura printer and optionally restart
        polling. Two printers can therefore never drive Preview concurrently.
        """
        machine_id, machine_name = self._config_store.identity()
        if machine_id == self._active_machine_id:
            # Cura may emit global-container notifications more than once while
            # a printer switch settles. Treat the machine id as the transaction
            # key so we reconnect only once.
            self._active_machine_name = machine_name
            self._sync_preview_button_state()
            return

        # Tear down the old printer *before* changing the owner identity. This
        # aborts its status request, metadata/download work and index workers, so
        # a late result cannot move Preview after another printer becomes active.
        self._client.stop()
        self._invalidate_lifecycle("active Cura printer changed")

        self._active_machine_id = machine_id
        self._active_machine_name = machine_name
        self._following_paused = False
        self._follow_controller.resume()
        self._last_remote_filename = None
        self._last_remote_state = None
        self._last_extruder_position = None
        self._last_capabilities = {}
        self._remote_job_key = None
        self._remote_file_identity = None
        self._discard_cached_gcode()
        self._clear_remote_gcode_index()
        self._config_store.migrate_legacy_to_current_machine()
        self._apply_timer_state()
        self._sync_preview_controls_visibility()

    def _active_printer_is_configured_for_following(self) -> bool:
        """Return True only when the active Cura printer owns a usable session."""
        if self._active_machine_id == "unknown":
            return False
        config = self._config_store.get()
        return bool(
            config.enabled
            and self._url_is_usable(self._normalise_base_url(config.url))
        )

    def _is_preview_stage_active(self) -> bool:
        try:
            stage = self._controller.getActiveStage()
            if stage is None:
                return False
            get_id = getattr(stage, "getId", None)
            stage_id = get_id() if callable(get_id) else getattr(stage, "stageId", None)
            return stage_id == "PreviewStage"
        except Exception:
            return False

    def _hide_toolhead_indicator(self) -> None:
        indicator = getattr(self, "_toolhead_indicator", None)
        if indicator is None:
            return
        try:
            indicator.setVisible(False)
        except Exception:
            pass

    def _update_toolhead_indicator(self, view=None) -> None:
        """Keep the plugin-owned printhead marker aligned with Cura Preview."""
        indicator = getattr(self, "_toolhead_indicator", None)
        config = self._config_store.get()
        if (
            indicator is None
            or not config.show_toolhead_indicator
            or not config.enabled
            or self._following_paused
            or self._last_remote_state not in self.ACTIVE_STATES
            or not self._toolhead_path_valid
            or not config.path_follow
            or config.follow_mode != FollowMode.EXACT.value
            or self._slicing_in_progress
            or not self._is_preview_stage_active()
        ):
            self._hide_toolhead_indicator()
            return

        if view is None:
            view = self._simulation_view()
        if view is None:
            self._hide_toolhead_indicator()
            return

        position = preview_head_position(self._controller, view)
        if position is None:
            self._hide_toolhead_indicator()
            return
        try:
            if not indicator.ensureNativeNozzleMesh(view):
                self._hide_toolhead_indicator()
                return
            indicator.setIndicatorPosition(position)
            indicator.setVisible(True)
        except Exception as error:
            self._hide_toolhead_indicator()
            Logger.log("w", "Moonraker Print Follower could not update printhead indicator: %s", error)

    def _watch_for_manual_preview_change(self) -> None:
        """Fallback watcher for Cura builds without layer/path change signals."""
        self._check_for_manual_preview_change()

    def _on_preview_position_changed(self, *_args) -> None:
        """Event-driven manual override detection for normal Cura 5.x builds."""
        self._check_for_manual_preview_change()

    def _check_for_manual_preview_change(self) -> None:
        """Pause following when Cura Preview moves outside our own write.

        Cura emits the same layer/path change signals for both the upper
        (current) and lower (minimum) slider handles.  Track all four values so
        a user adjustment cannot be missed merely because the current layer or
        path stayed unchanged.  Crucially, an unarmed follower never adopts an
        arbitrary Preview position as its baseline; only a follower write (or
        an explicit Resume) arms the expected position.
        """
        if not self._pref_bool(self.PREF_ENABLED) or self._following_paused:
            return
        if self._applying_follow_update:
            return
        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            return

        view = self._simulation_view()
        if view is None:
            return

        # If no follower-originated position has been recorded yet there is no
        # trustworthy reference point.  Do *not* baseline from the current view
        # here: doing so can swallow the very first manual change after a Cura
        # lifecycle event.
        if self._expected_follow_layer is None:
            return

        try:
            current_layer = int(view.getCurrentLayer())
        except Exception:
            return

        try:
            current_minimum_layer = (
                int(view.getMinimumLayer()) if hasattr(view, "getMinimumLayer") else None
            )
        except Exception:
            current_minimum_layer = None

        try:
            current_path = float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else None
        except Exception:
            current_path = None

        try:
            current_minimum_path = (
                int(view.getMinimumPath()) if hasattr(view, "getMinimumPath") else None
            )
        except Exception:
            current_minimum_path = None

        override_kind = preview_override_kind(
            expected_layer=self._expected_follow_layer,
            current_layer=current_layer,
            expected_minimum_layer=self._expected_follow_minimum_layer,
            current_minimum_layer=current_minimum_layer,
            expected_path=self._expected_follow_path,
            current_path=current_path,
            expected_minimum_path=self._expected_follow_minimum_path,
            current_minimum_path=current_minimum_path,
        )
        if override_kind is None:
            return

        self._following_paused = True
        self._toolhead_path_valid = False
        self._hide_toolhead_indicator()
        self._follow_controller.pause_by_user(f"manual {override_kind} change")
        # Retain the user's position for diagnostics/UI state.  Resume will
        # explicitly re-arm from this position before requesting a catch-up.
        self._remember_plugin_preview_position(view)
        self._sync_preview_button_state()
        if override_kind == "layer":
            self._set_status("Following paused because the Preview layer range was changed manually")
        else:
            self._set_status("Following paused because the Preview path position was changed manually")

    def _update_manual_view_watch_mode(self) -> None:
        """Keep a lightweight watcher running as a backstop for Cura signals.

        Cura 5.13 exposes currentLayerNumChanged/currentPathNumChanged and those
        signals provide the immediate manual-override response.  The 75 ms
        watcher deliberately remains enabled as a fallback in case a view is
        rebuilt or a signal connection is temporarily unavailable.  Plugin
        writes are distinguished by _applying_follow_update, so no time-based
        suppression window is required.
        """
        should_run = (
            self._pref_bool(self.PREF_ENABLED)
            and self._valid_configured_url()
        )
        if should_run:
            self._manual_view_watch_timer.start()
        else:
            self._manual_view_watch_timer.stop()

    def _clear_expected_preview_position(self) -> None:
        self._expected_follow_layer = None
        self._expected_follow_minimum_layer = None
        self._expected_follow_path = None
        self._expected_follow_minimum_path = None

    def _remember_plugin_preview_position(self, view) -> None:
        """Record Cura's settled position after a follower-controlled update."""
        try:
            self._expected_follow_layer = int(view.getCurrentLayer())
        except Exception:
            self._expected_follow_layer = None
        try:
            self._expected_follow_minimum_layer = (
                int(view.getMinimumLayer()) if hasattr(view, "getMinimumLayer") else None
            )
        except Exception:
            self._expected_follow_minimum_layer = None
        try:
            self._expected_follow_path = (
                float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else None
            )
        except Exception:
            self._expected_follow_path = None
        try:
            self._expected_follow_minimum_path = (
                int(view.getMinimumPath()) if hasattr(view, "getMinimumPath") else None
            )
        except Exception:
            self._expected_follow_minimum_path = None

    def _queue_lifecycle_callback(self, callback, delay_ms: int = 0) -> None:
        generation = self._lifecycle_generation
        def run() -> None:
            if self._destroyed or generation != self._lifecycle_generation:
                return
            try:
                callback()
            except Exception as error:
                Logger.logException(
                    "e",
                    "Moonraker Print Follower delayed callback failed: %s",
                    error,
                )
        QTimer.singleShot(delay_ms, run)

    def _bind_scene_structure_signal(self) -> None:
        old_root = getattr(self, "_scene_root", None)
        if old_root is not None:
            try:
                old_root.childrenChanged.disconnect(self._on_scene_children_changed)
            except Exception:
                pass
        indicator = getattr(self, "_toolhead_indicator", None)
        if indicator is not None:
            try:
                indicator.setParent(None)
            except Exception:
                pass
        try:
            self._scene = self._controller.getScene()
            self._scene_root = self._scene.getRoot()
            # Parent our marker before listening for structural changes so adding
            # the plugin-owned node cannot recursively invalidate the lifecycle.
            if indicator is not None:
                indicator.setParent(self._scene_root)
            self._scene_root.childrenChanged.connect(self._on_scene_children_changed)
        except Exception:
            self._scene = None
            self._scene_root = None

    def _abort_status_reply(self) -> None:
        reply = self._reply
        self._reply = None
        self._reply_purpose = None
        if reply is None:
            return
        try:
            reply.finished.disconnect()
        except Exception:
            pass
        try:
            if reply.isRunning():
                reply.abort()
        except Exception:
            pass
        try:
            reply.deleteLater()
        except Exception:
            pass

    def _abort_metadata_reply(self) -> None:
        reply = self._metadata_reply
        self._metadata_reply = None
        self._metadata_filename = None
        self._metadata_reply_job_key = None
        if reply is None:
            return
        try:
            reply.finished.disconnect()
        except Exception:
            pass
        try:
            if reply.isRunning():
                reply.abort()
        except Exception:
            pass
        try:
            reply.deleteLater()
        except Exception:
            pass

    def _ensure_remote_metadata(self, filename: str, fallback_size: int = 0) -> None:
        if not filename:
            return
        identity = self._remote_file_identity
        if (
            identity is not None
            and identity.matches_job(filename, fallback_size)
            and self._metadata_job_key == self._remote_job_key
        ):
            return
        if self._metadata_reply is not None and self._metadata_reply.isRunning():
            if self._metadata_filename == filename:
                return
            self._abort_metadata_reply()

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            return
        request = QNetworkRequest(QUrl(metadata_endpoint(base_url, filename)))
        request.setRawHeader(b"Accept", b"application/json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        api_key = self._pref_str(self.PREF_API_KEY)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))
        self._metadata_filename = filename
        self._metadata_reply_generation = self._lifecycle_generation
        self._metadata_reply_job_key = self._remote_job_key
        reply = self._metadata_network.get(request)
        self._metadata_reply = reply
        generation = self._metadata_reply_generation
        job_key = self._metadata_reply_job_key
        reply.finished.connect(
            lambda r=reply, f=filename, sz=int(fallback_size or 0), g=generation, j=job_key: self._handle_metadata_reply(r, f, sz, g, j)
        )

    def _handle_metadata_reply(
        self,
        reply: QNetworkReply,
        filename: str,
        fallback_size: int,
        reply_generation: int,
        reply_job_key: Optional[Tuple[str, int, int]],
    ) -> None:
        if reply is not self._metadata_reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        self._metadata_reply = None
        self._metadata_filename = None
        self._metadata_reply_job_key = None
        try:
            if (
                reply_generation != self._lifecycle_generation
                or reply_job_key != self._remote_job_key
            ):
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                # Older/minimal Moonraker installations may not expose metadata.
                # Mark this job resolved with a non-persistable fallback identity
                # so live following can still build an in-memory index.
                self._remote_file_identity = RemoteFileIdentity(filename, int(fallback_size or 0))
                self._metadata_job_key = self._remote_job_key
                if self._pref_bool(self.PREF_PATH_FOLLOW) and self._cura_has_toolpath():
                    self._ensure_remote_gcode_index(filename)
                return
            payload = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
            identity = parse_file_identity(filename, payload, fallback_size)
            if self._last_remote_filename != filename:
                return
            if self._remote_job_key is not None and not identity.matches_job(
                self._remote_job_key[0], self._remote_job_key[1]
            ):
                return
            self._remote_file_identity = identity
            self._metadata_job_key = self._remote_job_key
            if (
                self._remote_index_data is not None
                and self._remote_index_filename == filename
                and (identity.uuid or identity.modified > 0)
            ):
                self._persist_index_async(identity, self._remote_index_data)
            elif self._pref_bool(self.PREF_PATH_FOLLOW) and self._cura_has_toolpath():
                if self._try_load_persistent_index(filename):
                    if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                        self._queue_lifecycle_callback(lambda: self._poll(force=True))
                else:
                    self._ensure_remote_gcode_index(filename)
        except Exception as error:
            Logger.log("w", "Moonraker Print Follower metadata lookup failed for %s: %s", filename, error)
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass

    def _abort_file_reply(self) -> None:
        reply = self._file_reply
        self._file_reply = None
        self._file_reply_filename = None
        self._file_reply_job_key = None
        target = self._file_download_target
        self._file_download_target = None
        if reply is not None:
            try:
                reply.readyRead.disconnect()
            except Exception:
                pass
            try:
                reply.finished.disconnect()
            except Exception:
                pass
            try:
                if reply.isRunning():
                    reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass
        if target is not None:
            target.abort(remove=True)
            self._cleanup_cached_job_dir(target.path)
        if self._operation.phase == OperationPhase.DOWNLOADING:
            self._set_operation_phase(OperationPhase.IDLE)

    def _invalidate_lifecycle(self, reason: str, abort_network: bool = True) -> None:
        self._lifecycle_generation += 1
        self._toolhead_path_valid = False
        self._hide_toolhead_indicator()
        self._clear_expected_preview_position()
        self._cancel_remote_index_build()

        if abort_network:
            self._abort_status_reply()
            self._abort_metadata_reply()
            self._abort_file_reply()
            self._file_reply_generation = self._lifecycle_generation

        was_loading = self._cura_load_in_progress
        self._cura_load_in_progress = False
        self._cura_load_started_at = None
        self._cura_load_path = None
        self._cura_load_filename = None
        self._cura_load_job_key = None
        # If Cura was still parsing a remote file, do not delete a deferred
        # cache directory underneath its asynchronous reader. TemporaryDirectory
        # will remove it at shutdown if no later fileCompleted event owns it.
        if not was_loading:
            self._cleanup_deferred_cache_dirs()
        self._force_load_requested = False
        self._force_load_pending_filename = None
        self._operation.reset(OperationPhase.IDLE)
        Logger.log("d", "Moonraker Print Follower invalidated scene lifecycle: %s", reason)

    def _on_scene_children_changed(self, source=None) -> None:
        # Cura's own remote G-code reader and CuraEngine both restructure the
        # scene as part of expected work. Their dedicated lifecycle handlers own
        # those transitions, so do not recursively invalidate them here.
        if self._cura_load_in_progress or self._slicing_in_progress:
            return
        if time.monotonic() < self._scene_settle_until:
            return

        self._follow_controller.set_cura_suspended(True)
        self._scene_settle_until = time.monotonic() + 0.35
        self._invalidate_lifecycle("Cura scene structure changed")
        self._queue_lifecycle_callback(self._finish_scene_settle, 100)

    def _finish_scene_settle(self) -> None:
        self._follow_controller.set_cura_suspended(False)
        self._bind_scene_structure_signal()
        self._refresh_simulation_view_connection()
        self._sync_preview_button_state()
        if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
            self._poll(force=True)

    def _on_slicing_started(self, *_args) -> None:
        if self._cura_load_in_progress:
            return
        self._slicing_in_progress = True
        self._follow_controller.set_cura_suspended(True)
        self._scene_settle_until = time.monotonic() + 0.5
        self._invalidate_lifecycle("Cura slicing started")

    def _on_slicing_cancelled(self, *_args) -> None:
        self._finish_slicing_lifecycle("Cura slicing cancelled")

    def _on_backend_state_changed(self, state, *_args) -> None:
        try:
            done = state == BackendState.Done
        except Exception:
            done = str(state).lower().endswith("done")
        if done:
            self._finish_slicing_lifecycle("Cura slicing finished")

    def _finish_slicing_lifecycle(self, reason: str) -> None:
        if not self._slicing_in_progress:
            return
        self._slicing_in_progress = False
        self._follow_controller.set_cura_suspended(False)
        self._scene_settle_until = time.monotonic() + 0.35
        self._bind_scene_structure_signal()
        self._refresh_simulation_view_connection()
        self._sync_preview_button_state()
        Logger.log("d", "Moonraker Print Follower: %s", reason)
        if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
            self._queue_lifecycle_callback(lambda: self._poll(force=True), 100)

    def _on_active_view_changed(self, *_args) -> None:
        # Rebind SimulationView if Cura replaced the QObject underneath us.
        self._refresh_simulation_view_connection()
        self._sync_preview_controls_visibility()

    def _on_main_window_changed(self, *_args) -> None:
        # A main-window/QML rebuild does not imply a new model or remote job.
        # Rebind UI objects without cancelling network/index work.
        self._refresh_simulation_view_connection()
        # Do not append another saveButton component on every mainWindowChanged.
        # Cura keeps additional components at application scope, so the existing
        # action control can be reparented by the new ActionPanel. Only the empty
        # Preview overlay needs its visual parent updated.
        self._reparent_preview_overlay()
        if self._preview_overlay is None or self._action_panel_controls is None:
            self._create_preview_controls()
        try:
            self._application.additionalComponentsChanged.emit("saveButton")
        except Exception:
            pass
        self._sync_preview_controls_visibility()

    def _on_preview_overlay_destroyed(self, obj=None) -> None:
        self._preview_overlay = None

    def _on_action_panel_controls_destroyed(self, obj=None) -> None:
        self._remove_additional_component_reference(
            self._action_panel_controls if self._action_panel_controls is not None else obj
        )
        self._action_panel_controls = None

    def _on_simulation_view_destroyed(self, obj=None) -> None:
        # This handler is connected only to the currently tracked view. Avoid
        # comparing a destroyed Qt proxy: depending on PyQt lifetime timing the
        # object passed by destroyed() may not compare identically to our proxy.
        self._connected_simulation_view = None
        self._manual_view_signals_connected = False
        self._update_manual_view_watch_mode()

    def _remove_additional_component_reference(self, component) -> None:
        """Best-effort cleanup for Cura's add-only additional-component API.

        Cura 5.13 exposes ``addAdditionalComponent`` but no corresponding public
        remove call. On normal main-window rebuilds we reuse the same component,
        so this is only needed when the component is actually destroyed or the
        plugin shuts down. The private-map fallback is capability-checked and
        isolated here so a future Cura version can simply skip it safely.
        """
        if component is None:
            return

        remover = getattr(self._application, "removeAdditionalComponent", None)
        if callable(remover):
            try:
                remover("saveButton", component)
                return
            except Exception:
                pass

        components = getattr(self._application, "_additional_components", None)
        if not isinstance(components, dict):
            return
        row = components.get("saveButton")
        if not isinstance(row, list):
            return
        filtered = [item for item in row if item is not component]
        if len(filtered) == len(row):
            return
        components["saveButton"] = filtered
        try:
            self._application.additionalComponentsChanged.emit("saveButton")
        except Exception:
            pass

    def _disconnect_simulation_view_connection(self) -> None:
        view = self._connected_simulation_view
        self._connected_simulation_view = None
        self._manual_view_signals_connected = False
        if view is None:
            return
        try:
            activity_changed = getattr(view, "activityChanged", None)
            if activity_changed is not None:
                activity_changed.disconnect(self._on_simulation_activity_changed)
        except Exception:
            pass
        for signal_name in ("currentLayerNumChanged", "currentPathNumChanged"):
            try:
                signal = getattr(view, signal_name, None)
                if signal is not None:
                    signal.disconnect(self._on_preview_position_changed)
            except Exception:
                pass

    def _refresh_simulation_view_connection(self):
        view = None
        try:
            view = self._controller.getView("SimulationView")
        except Exception:
            pass

        if view is self._connected_simulation_view:
            return view

        old = self._connected_simulation_view
        if old is not None:
            try:
                destroyed = getattr(old, "destroyed", None)
                if destroyed is not None:
                    destroyed.disconnect(self._on_simulation_view_destroyed)
            except Exception:
                pass
            try:
                activity_changed = getattr(old, "activityChanged", None)
                if activity_changed is not None:
                    activity_changed.disconnect(self._on_simulation_activity_changed)
            except Exception:
                pass
            for signal_name in ("currentLayerNumChanged", "currentPathNumChanged"):
                try:
                    signal = getattr(old, signal_name, None)
                    if signal is not None:
                        signal.disconnect(self._on_preview_position_changed)
                except Exception:
                    pass

        self._connected_simulation_view = view
        self._manual_view_signals_connected = False
        if view is None:
            self._update_manual_view_watch_mode()
            return None

        try:
            destroyed = getattr(view, "destroyed", None)
            if destroyed is not None:
                destroyed.connect(self._on_simulation_view_destroyed)
        except Exception:
            pass

        try:
            activity_changed = getattr(view, "activityChanged", None)
            if activity_changed is not None:
                activity_changed.connect(self._on_simulation_activity_changed)
        except Exception:
            pass

        layer_signal = getattr(view, "currentLayerNumChanged", None)
        path_signal = getattr(view, "currentPathNumChanged", None)
        if layer_signal is not None and path_signal is not None:
            try:
                layer_signal.connect(self._on_preview_position_changed)
                path_signal.connect(self._on_preview_position_changed)
                self._manual_view_signals_connected = True
            except Exception:
                self._manual_view_signals_connected = False
        self._update_manual_view_watch_mode()
        return view

    def _compact_preview_status(self, has_toolpath: Optional[bool] = None) -> str:
        phase = self._operation.phase
        if phase == OperationPhase.RESOLVING:
            return "Resolving…"
        if phase == OperationPhase.DOWNLOADING:
            return "Downloading…"
        if phase == OperationPhase.CURA_LOADING:
            return "Loading print…"
        if phase == OperationPhase.INDEXING:
            return "Indexing…"
        if phase == OperationPhase.ERROR:
            return "Error"

        state = self._follow_controller.state
        if state == FollowState.USER_OVERRIDE or self._following_paused:
            return "Paused"
        if state == FollowState.CURA_SUSPENDED:
            return "Cura busy"
        if state == FollowState.CONNECTING:
            return "Connecting…"
        if state == FollowState.DISCONNECTED and self._pref_bool(self.PREF_ENABLED):
            return "Disconnected"
        if state == FollowState.ERROR:
            return "Connection error"
        if state == FollowState.REMOTE_PAUSED:
            return "Printer paused"
        if has_toolpath is None:
            has_toolpath = self._cura_has_toolpath()
        if (
            has_toolpath
            and self._pref_bool(self.PREF_ENABLED)
            and self._last_remote_state in self.ACTIVE_STATES
        ):
            return "Following"
        if self._last_remote_state in self.ACTIVE_STATES:
            return "Print active"
        if state == FollowState.IDLE:
            return "Connected"
        return ""

    @staticmethod
    def _compact_preview_status_icon(status: str) -> str:
        if status in ("Following", "Connected"):
            return "CheckCircle"
        if status in ("Connecting…", "Loading print…", "Indexing…"):
            return "Clock"
        if status in ("Disconnected", "Connection error", "Error"):
            return "CancelCircle"
        if status == "Print active":
            return "Printer"
        return "Information"

    def _sync_preview_button_state(self, *_args) -> None:
        has_toolpath = self._cura_has_toolpath()
        compact_status = self._compact_preview_status(has_toolpath)
        status_icon_name = self._compact_preview_status_icon(compact_status)
        configured = self._active_printer_is_configured_for_following()
        machine_id, machine_name = self._config_store.identity()
        if machine_id == self._active_machine_id:
            self._active_machine_name = machine_name
        active_printer_name = self._active_machine_name or machine_name
        for controls in (self._preview_overlay, self._action_panel_controls):
            if controls is None:
                continue
            try:
                controls.setProperty("followingPaused", self._following_paused)
                controls.setProperty(
                    "followingEnabled", self._pref_bool(self.PREF_ENABLED)
                )
                controls.setProperty("configuredForFollowing", configured)
                controls.setProperty("activePrinterName", active_printer_name)
                controls.setProperty("hasToolpath", has_toolpath)
                controls.setProperty("statusText", compact_status)
                controls.setProperty("statusIconName", status_icon_name)
            except Exception:
                pass

    def _on_simulation_activity_changed(self, *_args) -> None:
        """Refresh controls and catch up as soon as Cura finishes loading layer data."""

        self._sync_preview_button_state()
        self._clear_expected_preview_position()
        if (
            self._cura_has_toolpath()
            and self._pref_bool(self.PREF_ENABLED)
            and not self._following_paused
        ):
            # readLocalFile() parses G-code asynchronously. A normal timer tick can
            # arrive while SimulationView still reports zero layers and be clamped
            # to layer 0. Force a fresh poll the moment Cura announces real layer
            # data so following resumes immediately after a manual remote load.
            self._queue_lifecycle_callback(lambda: self._poll(force=True))

    def _cura_has_toolpath(self) -> bool:
        """Return True only when Cura's SimulationView actually has layer data."""

        view = self._simulation_view()
        if view is None:
            return False

        get_activity = getattr(view, "getActivity", None)
        if callable(get_activity):
            try:
                return bool(get_activity())
            except Exception:
                pass

        # Fallback for unusual Cura builds: actual layer data is stronger evidence
        # than getMaxLayers(), because a valid one-layer print also has max layer 0.
        get_layer_data = getattr(view, "getLayerData", None)
        if callable(get_layer_data):
            try:
                return get_layer_data() is not None
            except Exception:
                pass
        return False

    def _sync_preview_controls_visibility(self, *_args) -> None:
        """Expose both control placements only while Preview is active.

        The QML components themselves decide which placement is visible based on
        ``CuraApplication.platformActivity``: the empty-Preview overlay is used
        when Cura has no action panel, and the registered ``saveButton`` component
        is used once Cura's Slice/Save/Upload panel exists.
        """

        if self._preview_overlay is None and self._action_panel_controls is None:
            self._update_toolhead_indicator()
            return

        is_preview = False
        try:
            stage = self._controller.getActiveStage()
            if stage is not None:
                stage_id = None
                get_id = getattr(stage, "getId", None)
                if callable(get_id):
                    stage_id = get_id()
                if not stage_id:
                    stage_id = getattr(stage, "stageId", None)
                is_preview = stage_id == "PreviewStage"
        except Exception as error:
            Logger.log(
                "w",
                "Moonraker Print Follower could not determine Cura's active stage: %s",
                error,
            )

        for controls in (self._preview_overlay, self._action_panel_controls):
            if controls is None:
                continue
            try:
                controls.setProperty("previewStageActive", is_preview)
            except Exception as error:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not update Preview control visibility: %s",
                    error,
                )
        self._sync_preview_button_state()
        self._update_toolhead_indicator()

    def _apply_timer_state(self) -> None:
        """Configure the per-printer Moonraker client.

        2.0.0 keeps polling isolated in MoonrakerClient so per-printer
        connection state and retry backoff do not leak into Cura lifecycle code.
        """
        config = self._config_store.get()
        self._follow_controller.set_enabled(config.enabled)
        base_url = self._normalise_base_url(config.url)
        self._client.configure(base_url, config.api_key, config.poll_interval_ms)

        if config.enabled and self._url_is_usable(base_url):
            self._client.start()
            self._update_manual_view_watch_mode()
        else:
            self._client.stop()
            self._manual_view_watch_timer.stop()
            self._clear_expected_preview_position()
            if config.enabled:
                self._set_status("Set a Moonraker URL for this Cura printer")

    def _reparent_preview_overlay(self) -> None:
        overlay = self._preview_overlay
        if overlay is None:
            return
        try:
            main_window = self._application.getMainWindow()
            if main_window is None or not hasattr(main_window, "contentItem"):
                return
            window_content = main_window.contentItem()
            if window_content is None:
                return
            set_parent_item = getattr(overlay, "setParentItem", None)
            if callable(set_parent_item):
                set_parent_item(window_content)
            try:
                overlay.setParent(window_content)
            except Exception:
                pass
        except Exception as error:
            Logger.log(
                "w",
                "Moonraker Print Follower could not reparent empty Preview control: %s",
                error,
            )

    def _create_preview_controls(self, *_args) -> None:
        """Create adaptive Preview controls using Cura's native action-panel slot.

        When Cura has no platform activity, its entire bottom-right action panel is
        absent, so a small direct overlay keeps ``Load current print`` reachable.
        As soon as the action panel exists, the overlay hides itself and a second
        component registered in Cura's official ``saveButton`` extension area takes
        over. Cura then lays our controls out alongside other plugins (for example
        Post Processing) instead of us guessing the panel's size or position.
        """

        if self._preview_overlay is not None and self._action_panel_controls is not None:
            return
        try:
            from UM.PluginRegistry import PluginRegistry

            main_window = self._application.getMainWindow()
            if main_window is None or not hasattr(main_window, "contentItem"):
                return
            window_content = main_window.contentItem()
            if window_content is None:
                return

            plugin_path = PluginRegistry.getInstance().getPluginPath(self.PLUGIN_ID)
            if not plugin_path:
                return

            if self._preview_overlay is None:
                overlay_path = os.path.join(plugin_path, "EmptyPreviewLoadButton.qml")
                overlay = self._application.createQmlComponent(overlay_path)
                if overlay is None:
                    Logger.log("e", "Moonraker Print Follower could not create empty-Preview control")
                    return

                try:
                    overlay.loadClicked.connect(self._confirm_force_load_current_print)
                except Exception as error:
                    Logger.logException(
                        "e",
                        "Moonraker Print Follower could not connect empty-Preview Load button: %s",
                        error,
                    )
                    try:
                        overlay.deleteLater()
                    except Exception:
                        pass
                    return

                self._preview_overlay = overlay
                try:
                    overlay.destroyed.connect(self._on_preview_overlay_destroyed)
                except Exception:
                    pass
            self._reparent_preview_overlay()

            if self._action_panel_controls is None:
                action_path = os.path.join(plugin_path, "PreviewActionPanelControls.qml")
                action_controls = self._application.createQmlComponent(action_path)
                if action_controls is None:
                    Logger.log("e", "Moonraker Print Follower could not create action-panel controls")
                else:
                    try:
                        action_controls.loadClicked.connect(self._confirm_force_load_current_print)
                        action_controls.pauseClicked.connect(self._toggle_following_pause)
                    except Exception as error:
                        Logger.logException(
                            "e",
                            "Moonraker Print Follower could not connect action-panel controls: %s",
                            error,
                        )
                        try:
                            action_controls.deleteLater()
                        except Exception:
                            pass
                        action_controls = None

                    if action_controls is not None:
                        self._application.addAdditionalComponent("saveButton", action_controls)
                        self._action_panel_controls = action_controls
                        try:
                            action_controls.destroyed.connect(self._on_action_panel_controls_destroyed)
                        except Exception:
                            pass

                # Rebind to Cura's current SimulationView instance. Cura can replace
            # this QObject during model/slice transitions.
            self._refresh_simulation_view_connection()

            self._sync_preview_button_state()
            self._sync_preview_controls_visibility()
        except Exception as error:
            Logger.logException(
                "w",
                "Moonraker Print Follower could not add Preview controls: %s",
                error,
            )

    @pyqtSlot()
    def confirmForceLoadCurrentPrint(self) -> None:
        """QML entry point for the Preview Load current print button."""

        self._confirm_force_load_current_print()

    @pyqtSlot()
    def toggleFollowingPause(self) -> None:
        """QML entry point for the Preview Pause/Resume following button."""

        self._toggle_following_pause()

    def _confirm_force_load_current_print(self) -> None:
        """Ask for confirmation using Cura's own reliable Qt Widgets path.

        Cura itself uses ``QMessageBox.question(None, ...)`` for destructive
        confirmations. Using that exact synchronous API keeps destructive load
        confirmation in Cura's established UI path.
        """

        try:
            # Keep the manual action Preview-oriented. Runtime load requests can
            # originate outside Preview, so switch stages before asking.
            stage = self._controller.getActiveStage()
            stage_id = None
            if stage is not None:
                get_id = getattr(stage, "getId", None)
                if callable(get_id):
                    stage_id = get_id()
                if not stage_id:
                    stage_id = getattr(stage, "stageId", None)
            if stage_id != "PreviewStage":
                self._controller.setActiveStage("PreviewStage")
        except Exception as error:
            Logger.log(
                "w",
                "Moonraker Print Follower could not switch to Preview before confirmation: %s",
                error,
            )

        # Defer one event-loop turn so a requested stage change can complete.
        self._queue_lifecycle_callback(self._ask_force_load_question)

    def _ask_force_load_question(self) -> None:
        """Synchronously ask Yes/No, then act on the returned button value."""

        try:
            answer = QMessageBox.question(
                None,
                "Moonraker Print Follower",
                "Replace Cura contents?\n\n"
                "This will discard everything currently loaded in Cura and replace it "
                "with the G-code currently printing in Moonraker.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        except Exception as error:
            Logger.logException(
                "e",
                "Moonraker Print Follower could not show load confirmation: %s",
                error,
            )
            self._set_status(f"Could not show load confirmation: {error}")
            return

        if answer != QMessageBox.StandardButton.Yes:
            self._set_status("Load current print cancelled")
            return

        # The QMessageBox has already closed at this point. Queue the scene/network
        # change onto the next event-loop turn rather than doing it inside the
        # nested QMessageBox event loop.
        self._queue_lifecycle_callback(self._force_load_current_print)

    def _force_load_current_print(self) -> None:
        """Resolve the active Moonraker job, re-download it and replace Cura's scene."""

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL before loading the current print")
            return

        self._operation.reset(OperationPhase.RESOLVING)
        self._force_load_requested = True
        self._force_load_pending_filename = None
        self._set_status("Finding the current Moonraker print…")
        self._issue_status_request(
            base_url,
            self._pref_str(self.PREF_API_KEY),
            purpose="force_load",
        )

    def _start_forced_gcode_download(self, filename: str) -> None:
        if not filename:
            self._force_load_requested = False
            self._set_status("Moonraker did not report a current G-code filename")
            return

        self._force_load_pending_filename = filename

        # The follower normally downloads the active G-code shortly after a print
        # starts so it can map Moonraker's file_position to Cura's path slider.
        # If that exact job is already cached, loading it should be immediate: do
        # not throw away a perfectly good multi-megabyte file and download/index
        # it all over again just because the user clicked the button.
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            self._set_status(f"{filename}: loading cached current print into Cura Preview…")
            self._queue_lifecycle_callback(lambda f=filename: self._load_cached_remote_gcode_forced(f))
            return

        # If the same file is already downloading for path indexing, reuse that
        # request. The reply handler will cache it, start Cura loading immediately,
        # and build the path index separately.
        if (
            self._file_reply is not None
            and self._file_reply.isRunning()
            and self._file_reply_filename == filename
            and self._file_reply_job_key == self._remote_job_key
        ):
            self._set_status(f"{filename}: downloading current print…")
            return

        if self._file_reply is not None:
            self._abort_file_reply()

        if self._begin_gcode_download(filename):
            self._set_status(f"{filename}: downloading current print…")
        else:
            self._force_load_requested = False
            self._force_load_pending_filename = None
            self._set_operation_phase(OperationPhase.ERROR, filename=filename)
            self._set_status(f"Could not start download of current print: {filename}")

    def _load_cached_remote_gcode_forced(self, filename: str) -> None:
        if (
            self._cached_gcode_filename != filename
            or not self._cached_gcode_path
            or self._cached_gcode_job_key != self._remote_job_key
            or not os.path.isfile(self._cached_gcode_path)
        ):
            self._force_load_requested = False
            self._force_load_pending_filename = None
            self._set_status("Current print was downloaded but could not be cached for Cura")
            return

        path = self._cached_gcode_path

        # Keep our Python-heavy path indexer out of Cura's way while Cura parses
        # the G-code. The index is rebuilt after Cura emits fileCompleted.
        self._cancel_remote_index_build()
        self._cura_load_in_progress = True
        self._follow_controller.set_cura_suspended(True)
        self._cura_load_started_at = time.perf_counter()
        self._cura_load_path = path
        self._cura_load_filename = filename
        self._cura_load_job_key = self._cached_gcode_job_key

        try:
            Logger.log(
                "i",
                "Moonraker Print Follower forcibly replacing Cura contents with remote G-code: %s",
                filename,
            )
            # Use Cura's public, supported loader exactly as the last known-good
            # v0.9.8 implementation did. Do not bypass CuraApplication's normal
            # ReadFileJob / GCodeReader lifecycle with private APIs.
            self._application.readLocalFile(
                QUrl.fromLocalFile(path),
                add_to_recent_files=False,
            )
            self._set_status(f"{filename}: loading into Cura Preview…")
        except Exception as error:
            self._cura_load_in_progress = False
            self._follow_controller.set_cura_suspended(False)
            self._cura_load_started_at = None
            self._cura_load_path = None
            self._cura_load_filename = None
            self._cura_load_job_key = None
            self._cleanup_deferred_cache_dirs()
            self._force_load_requested = False
            self._force_load_pending_filename = None
            Logger.logException(
                "e",
                "Moonraker Print Follower could not force-load remote G-code into Cura: %s",
                error,
            )
            self._set_status(f"Could not load current print into Cura: {error}")

    # ------------------------------------------------------------------
    # Moonraker HTTP
    # ------------------------------------------------------------------

    def _poll(self, force: bool = False) -> None:
        if not force and not self._pref_bool(self.PREF_ENABLED):
            return
        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL for this Cura printer")
            return
        self._client.force_refresh()

    def _issue_status_request(self, base_url: str, api_key: str, purpose: str) -> None:
        if self._reply is not None and self._reply.isRunning():
            if purpose == "force_load":
                # A normal poll queries the same print_stats object we need here.
                # Promote the in-flight reply instead of dropping the explicit
                # user action or creating a second request path.
                self._reply_purpose = "force_load"
                self._set_status("Finding the current Moonraker print…")
            elif purpose == "test":
                self._set_status("A Moonraker request is already in progress")
            return

        endpoint = status_endpoint(base_url)
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))

        self._reply_purpose = purpose
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda r=reply: self._handle_status_reply(r))

    def _handle_status_reply(self, reply: QNetworkReply) -> None:
        # Ignore a late completion from an aborted/replaced request. Capturing
        # the actual reply object prevents an old ``finished`` signal from ever
        # processing the state belonging to a newer request.
        if reply is not self._reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return

        purpose = self._reply_purpose or "poll"
        self._reply = None
        self._reply_purpose = None

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._set_status(f"Moonraker error: {reply.errorString()}")
                return

            raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
            payload = json.loads(raw)
            result = payload.get("result") or {}
            status = result.get("status") or {}
            print_stats = status.get("print_stats") or {}
            gcode_move = status.get("gcode_move") or {}
            virtual_sdcard = status.get("virtual_sdcard") or {}
            motion_report = status.get("motion_report") or {}

            if purpose == "test":
                state = str(print_stats.get("state") or "unknown")
                filename = str(print_stats.get("filename") or "")
                suffix = f" — {filename}" if filename else ""
                self._set_status(f"Connected to Moonraker; printer state: {state}{suffix}")
                return

            if purpose == "force_load":
                state = str(print_stats.get("state") or "")
                filename = str(print_stats.get("filename") or "")
                if state not in self.ACTIVE_STATES:
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                    self._set_status(
                        f"Moonraker is {state or 'not printing'}; there is no active print to load"
                    )
                    return
                if not filename:
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                    self._set_status("Moonraker did not report a current G-code filename")
                    return
                self._update_remote_job_identity(print_stats, virtual_sdcard)
                self._last_remote_filename = filename
                self._last_remote_state = state
                try:
                    reported_size = int(virtual_sdcard.get("file_size") or 0)
                except (TypeError, ValueError):
                    reported_size = 0
                self._ensure_remote_metadata(filename, reported_size)
                self._start_forced_gcode_download(filename)
                return

            self._apply_remote_status(print_stats, gcode_move, virtual_sdcard, motion_report)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            Logger.logException("w", "Moonraker Print Follower could not parse Moonraker status: %s", error)
            self._set_status(f"Invalid Moonraker response: {error}")
        except Exception as error:  # Keep an extension failure from destabilising Cura.
            Logger.logException("e", "Moonraker Print Follower status handling failed: %s", error)
            self._set_status(f"Print follower error: {error}")
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Layer synchronisation
    # ------------------------------------------------------------------

    def _update_remote_job_identity(
        self,
        print_stats: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
    ) -> Optional[Tuple[str, int, int]]:
        """Track the actual print run, not merely the G-code filename.

        Moonraker retains print_stats after completion and users commonly print
        the same file repeatedly. Filename-only caching can therefore reuse an
        index from the previous run. File size plus reset detection for
        file_position / print_duration gives us a stable per-run identity without
        another network request.
        """
        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")
        active = state in self.ACTIVE_STATES and bool(filename)

        try:
            file_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        try:
            file_position = int(virtual_sdcard.get("file_position") or 0)
        except (TypeError, ValueError):
            file_position = 0
        try:
            print_duration = float(print_stats.get("print_duration") or 0.0)
        except (TypeError, ValueError):
            print_duration = 0.0

        new_job = False
        current = self._remote_job_key
        if active:
            if current is None:
                new_job = True
            elif current[0] != filename or current[1] != file_size:
                new_job = True
            elif self._last_remote_state not in self.ACTIVE_STATES:
                new_job = True
            elif (
                self._last_remote_file_position is not None
                and file_position < self._last_remote_file_position
            ):
                # virtual_sdcard.file_position is monotonic within a run. Any
                # backwards movement therefore identifies a restarted/replaced
                # job, including very small G-code files.
                new_job = True
            elif (
                self._last_remote_print_duration is not None
                and print_duration + 0.05 < self._last_remote_print_duration
            ):
                new_job = True

            if new_job:
                self._remote_job_serial += 1
                self._clear_remote_gcode_index()
                self._remote_job_key = (filename, file_size, self._remote_job_serial)
                if (
                    self._remote_file_identity is not None
                    and not self._remote_file_identity.matches_job(filename, file_size)
                ):
                    self._remote_file_identity = None
                Logger.log(
                    "i",
                    "Moonraker Print Follower detected new print run #%d: %s (%d bytes)",
                    self._remote_job_serial,
                    filename,
                    file_size,
                )

            self._last_remote_file_position = file_position
            self._last_remote_print_duration = print_duration
        else:
            self._last_remote_file_position = None
            self._last_remote_print_duration = None

        return self._remote_job_key

    def _apply_remote_status(
        self,
        print_stats: Dict[str, Any],
        gcode_move: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
        motion_report: Optional[Dict[str, Any]] = None,
    ) -> None:
        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")
        self._follow_controller.set_connection(True)
        self._follow_controller.set_remote_state(state)

        previous_filename = self._last_remote_filename
        self._update_remote_job_identity(print_stats, virtual_sdcard)
        try:
            reported_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            reported_size = 0
        if filename and state in self.ACTIVE_STATES:
            self._ensure_remote_metadata(filename, reported_size)

        if filename != previous_filename:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._last_extruder_position = None
            self._preview_switched_for_job = False
            self._last_source = None
        self._last_remote_filename = filename

        if state != self._last_remote_state:
            if state not in self.ACTIVE_STATES:
                self._last_extruder_position = None
                self._preview_switched_for_job = False
            self._last_remote_state = state

        if state not in self.ACTIVE_STATES:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            label = state or "unknown"
            suffix = f" — {filename}" if filename else ""
            self._set_status(f"Moonraker connected; printer is {label}{suffix}")
            return

        if self._following_paused:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="following paused; Moonraker polling continues",
                )
            )
            return

        # Keep the active G-code cached so an explicit Load current print can be
        # nearly immediate.  Do not burn CPU indexing it while Cura has no local
        # toolpath to follow: that work is useless until Preview contains layer
        # data and, worse, can contend with Cura's own G-code parser.
        if self._pref_bool(self.PREF_PATH_FOLLOW) and filename:
            if self._cura_has_toolpath() and not self._slicing_in_progress:
                self._ensure_remote_gcode_index(filename)
            else:
                self._ensure_remote_gcode_cached(filename)

        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="Cura is rebuilding local layer data; following temporarily suspended",
                )
            )
            return

        view = self._simulation_view()
        if view is None or not hasattr(view, "setLayer"):
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status("Connected, but Cura's SimulationView is unavailable")
            return

        self._maybe_switch_to_preview()

        info = print_stats.get("info") or {}
        remote_layer = info.get("current_layer")
        total_layer = info.get("total_layer")

        target_layer: Optional[int] = None
        source = ""

        if remote_layer is not None:
            try:
                raw_remote_layer = int(remote_layer)
                if (
                    self._remote_index_filename == filename
                    and raw_remote_layer in self._remote_current_layer_map
                ):
                    # Prefer the CURRENT_LAYER values embedded in the actual G-code.
                    # This makes layer numbering self-describing and avoids an
                    # off-by-one mismatch if the preference namespace was reset or
                    # a slicer/macro reports zero-based layers instead of one-based.
                    target_layer = self._remote_current_layer_map[raw_remote_layer]
                    source = "Moonraker current_layer (G-code mapped)"
                else:
                    target_layer = raw_remote_layer
                    if self._pref_bool(self.PREF_ONE_BASED):
                        target_layer -= 1
                    source = "Moonraker current_layer"
            except (TypeError, ValueError):
                target_layer = None

        if target_layer is None and self._pref_bool(self.PREF_Z_FALLBACK):
            target_layer = self._layer_from_z(view, gcode_move)
            if target_layer is not None:
                source = "Z-height fallback"

        if target_layer is None:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=total_layer,
                    detail="waiting for layer data",
                )
            )
            return

        try:
            max_layer = int(view.getMaxLayers()) if hasattr(view, "getMaxLayers") else target_layer
        except Exception:
            max_layer = target_layer

        # Cura's SimulationView layer is zero-based and its max is the largest
        # valid zero-based index. First clamp the actual printer layer, then let
        # the selected follow mode decide what Cura should display.
        remote_target_layer = max(0, min(target_layer, max(0, max_layer)))
        decision = decide_layers(
            remote_target_layer, max_layer, self._config_store.get().follow_mode
        )
        target_layer = decision.current_layer
        minimum_layer = decision.minimum_layer

        # If the local preview is clearly not the same sliced job, avoid
        # silently claiming perfect synchronisation.  We still follow (clamped)
        # because a user may intentionally be inspecting a similar G-code file.
        mismatch = ""
        try:
            if total_layer is not None:
                total = int(total_layer)
                local_total = max_layer + 1
                if total > 0 and local_total > 0 and abs(total - local_total) > 2:
                    mismatch = f"; layer-count mismatch remote {total} / local {local_total}"
        except (TypeError, ValueError):
            pass

        try:
            current = int(view.getCurrentLayer()) if hasattr(view, "getCurrentLayer") else -1
        except Exception:
            current = -1
        try:
            current_minimum = int(view.getMinimumLayer()) if hasattr(view, "getMinimumLayer") else None
        except Exception:
            current_minimum = None

        path_detail = ""
        self._toolhead_path_valid = False
        self._applying_follow_update += 1
        try:
            if current != target_layer or (minimum_layer is not None and current_minimum != minimum_layer):
                apply_preview_decision(view, target_layer, minimum_layer)

            if self._pref_bool(self.PREF_PATH_FOLLOW) and decision.follow_path:
                path_detail = self._apply_path_progress(
                    view, remote_target_layer, virtual_sdcard, motion_report or {}, gcode_move
                )
                self._toolhead_path_valid = path_detail.startswith("path ")
        finally:
            self._applying_follow_update = max(0, self._applying_follow_update - 1)

        self._remember_plugin_preview_position(view)
        self._update_toolhead_indicator(view)

        self._last_source = source
        mode = self._config_store.get().follow_mode
        detail = f"following via {source}; mode {mode}"
        if state == "paused":
            detail += "; printer paused"
        if path_detail:
            detail += f"; {path_detail}"
        detail += mismatch
        self._set_status(
            self._active_status_text(
                filename,
                remote_layer=remote_target_layer + 1,
                total_layer=total_layer,
                detail=detail,
            )
        )

    def _apply_path_progress(
        self,
        view,
        target_layer: int,
        virtual_sdcard: Dict[str, Any],
        motion_report: Optional[Dict[str, Any]] = None,
        gcode_move: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Drive Cura's horizontal path slider using Moonraker file position.

        The remote G-code is indexed once per print. For each Cura layer we
        retain the byte offsets of motion commands. ``virtual_sdcard.file_position``
        then tells us approximately how many of those commands Klipper has
        consumed. Scaling that count to Cura's path count gives a much closer
        visual match than using whole-file percentage or elapsed time.
        """

        if not hasattr(view, "setPath") or not hasattr(view, "getMaxPaths"):
            return "within-layer tracking unavailable in this Cura build"

        if (
            self._remote_index_filename != self._last_remote_filename
            or self._remote_index_job_key != self._remote_job_key
        ):
            # setLayer() normally resets the horizontal slider to the end of the
            # layer. While the remote file is still being indexed, show the start
            # instead so we do not misleadingly display a completed layer.
            try:
                view.setPath(0.0)
            except Exception:
                pass
            if (
                (self._file_reply is not None and self._file_reply.isRunning())
                or self._remote_index_build_filename == self._last_remote_filename
            ):
                return "indexing remote G-code"
            return "waiting for remote G-code index"

        if target_layer < 0 or target_layer >= len(self._remote_layer_ranges):
            return "no remote path index for this layer"

        try:
            file_position = int(virtual_sdcard.get("file_position"))
        except (TypeError, ValueError):
            return "waiting for file position"

        try:
            max_paths = int(view.getMaxPaths())
        except Exception:
            return "Cura path count unavailable"

        if max_paths <= 0:
            return "layer has no toolpaths"

        index = self._remote_index_data
        if index is None:
            return "remote path index unavailable"

        # Large G-code files use a compact persistent index containing only
        # layer byte ranges.  Hydrate motion/live-position data for the layer
        # currently being viewed on demand.  Byte-position following remains
        # available immediately while the small layer-only scan runs.
        if getattr(index, "compact", False):
            if not (
                self._cached_gcode_filename == self._last_remote_filename
                and self._cached_gcode_path
                and self._cached_gcode_job_key == self._remote_job_key
                and os.path.isfile(self._cached_gcode_path)
            ):
                self._ensure_remote_gcode_cached(self._last_remote_filename or "")
            self._ensure_remote_layer_hydrated(target_layer)

        live_position = live_position_in_gcode_space(
            motion_report or {},
            gcode_move or {},
        )

        fraction, method = index.refined_fraction(
            target_layer,
            file_position,
            live_position,
        )
        fraction = max(0.0, min(1.0, float(fraction)))
        target_path = fraction * max_paths

        # Exact path following owns the full horizontal slider range. Reset
        # Cura's lower path handle as well as the current path so a previous
        # manual/window-style inspection cannot leave part of the layer hidden.
        try:
            if hasattr(view, "getMinimumPath") and hasattr(view, "setMinimumPath"):
                if int(view.getMinimumPath()) != 0:
                    view.setMinimumPath(0)
        except Exception:
            pass

        try:
            current_path = float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else -1.0
        except Exception:
            current_path = -1.0

        # Avoid needlessly forcing a redraw for tiny changes. A half path is
        # smaller than the slider can usefully communicate visually.
        if abs(current_path - target_path) >= 0.5:
            view.setPath(target_path)

        return f"path {round(target_path)}/{max_paths} ({fraction * 100:.1f}%, {method})"

    def _ensure_remote_layer_hydrated(self, layer: int) -> None:
        index = self._remote_index_data
        if index is None or not getattr(index, "compact", False):
            return
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            return
        if layer < 0 or layer >= len(index.ranges):
            return
        if layer in getattr(index, "hydrated_layers", set()) or layer in self._hydrating_layers:
            return
        path = self._cached_gcode_path
        if not (
            path
            and self._cached_gcode_filename == self._last_remote_filename
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(path)
        ):
            return

        generation = self._remote_index_generation
        job_key = self._remote_job_key
        self._hydrating_layers.add(layer)
        self._hydration_threads = {t for t in self._hydration_threads if t.is_alive()}

        def worker() -> None:
            ok = False
            try:
                ok = bool(hydrate_layer_from_file(index, path, layer))
            except Exception as error:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not hydrate layer %d: %s",
                    layer,
                    error,
                )
            if not self._destroyed:
                self._remoteLayerHydrated.emit(generation, layer, ok and job_key == self._remote_job_key)

        thread = threading.Thread(
            target=worker,
            name=f"MoonrakerPrintFollowerLayer{layer}",
            daemon=True,
        )
        self._hydration_threads.add(thread)
        thread.start()

    @pyqtSlot(int, int, bool)
    def _on_remote_layer_hydrated(self, generation: int, layer: int, ok: bool) -> None:
        self._hydrating_layers.discard(layer)
        self._hydration_threads = {t for t in self._hydration_threads if t.is_alive()}
        if generation != self._remote_index_generation or not ok:
            return
        index = self._remote_index_data
        if index is None or layer not in getattr(index, "hydrated_layers", set()):
            return
        self._remote_motion_offsets = list(index.motion_offsets)
        # Save the newly hydrated layer opportunistically. Cache writes happen
        # off the GUI thread and remain bounded by PersistentIndexCache.
        self._persist_index_async(self._remote_file_identity, index)
        if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
            self._client.force_refresh()

    def _cancel_remote_index_build(self, wait: bool = False, timeout: float = 1.0) -> None:
        """Ask the current index worker to stop and invalidate its result.

        Normal scene changes cancel cooperatively without blocking Cura's GUI.
        Shutdown and replacement of a worker may wait briefly so a daemon thread
        is not left running against plugin state that is being torn down.
        """

        event = self._remote_index_cancel_event
        if event is not None:
            event.set()
        thread = self._remote_index_thread
        self._remote_index_cancel_event = None
        self._remote_index_generation += 1
        self._remote_index_build_filename = None
        self._remote_index_build_job_key = None
        if (
            wait
            and thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                Logger.log(
                    "w",
                    "Moonraker Print Follower index worker did not stop within %.2fs",
                    timeout,
                )
        if thread is not None and not thread.is_alive():
            self._remote_index_thread = None
        if self._operation.phase == OperationPhase.INDEXING:
            next_phase = (
                OperationPhase.READY
                if self._remote_index_data is not None
                else OperationPhase.IDLE
            )
            self._set_operation_phase(next_phase)

    def _cleanup_cached_job_dir(self, path: Optional[str]) -> None:
        """Remove a per-job cache directory when Cura no longer needs it."""
        if not path:
            return
        try:
            job_dir = os.path.dirname(os.path.abspath(path))
            temp_root = os.path.abspath(self._temp_gcode_dir.name)
            if os.path.commonpath((job_dir, temp_root)) != temp_root:
                return
            if self._cura_load_in_progress and self._cura_load_path:
                if os.path.abspath(path) == os.path.abspath(self._cura_load_path):
                    self._deferred_cache_dirs.add(job_dir)
                    return
            shutil.rmtree(job_dir, ignore_errors=True)
            self._deferred_cache_dirs.discard(job_dir)
        except Exception as error:
            Logger.log(
                "w",
                "Moonraker Print Follower could not clean cached G-code directory: %s",
                error,
            )

    def _cleanup_deferred_cache_dirs(self) -> None:
        if not self._deferred_cache_dirs:
            return
        active_dir = None
        if self._cura_load_in_progress and self._cura_load_path:
            try:
                active_dir = os.path.dirname(os.path.abspath(self._cura_load_path))
            except Exception:
                active_dir = None
        for job_dir in tuple(self._deferred_cache_dirs):
            if active_dir is not None and job_dir == active_dir:
                continue
            shutil.rmtree(job_dir, ignore_errors=True)
            self._deferred_cache_dirs.discard(job_dir)

    def _discard_cached_gcode(self) -> None:
        old_path = self._cached_gcode_path
        self._cached_gcode_filename = None
        self._cached_gcode_path = None
        self._cached_gcode_job_key = None
        self._cleanup_cached_job_dir(old_path)

    def _clear_remote_gcode_index(self) -> None:
        self._cancel_remote_index_build()
        self._hydrating_layers.clear()
        self._remote_index_filename = None
        self._remote_index_job_key = None
        self._remote_layer_ranges = []
        self._remote_motion_offsets = []
        self._remote_current_layer_map = {}
        self._remote_index_data = None
        self._discard_cached_gcode()

        self._abort_file_reply()

    def _install_remote_index(
        self, filename: str, index: LayerMotionIndex, job_key=None, *, source: str = "built"
    ) -> bool:
        if not index:
            return False
        if filename != self._last_remote_filename:
            return False
        if job_key is not None and self._remote_job_key is not None and job_key != self._remote_job_key:
            return False
        self._remote_index_data = index
        self._remote_layer_ranges = list(index.ranges)
        self._remote_motion_offsets = list(index.motion_offsets)
        self._remote_current_layer_map = dict(index.current_layer_map)
        self._remote_index_filename = filename
        self._remote_index_job_key = self._remote_job_key
        self._set_operation_phase(OperationPhase.READY, filename=filename)
        Logger.log(
            "i",
            "Moonraker Print Follower %s %d-layer path index for %s",
            source,
            len(index.ranges),
            filename,
        )
        return True

    def _persist_index_async(
        self, identity: Optional[RemoteFileIdentity], index: Optional[LayerMotionIndex]
    ) -> None:
        """Persist a completed index without gzip work on Cura's GUI thread."""
        if identity is None or index is None or not index:
            return
        if not identity.uuid and identity.modified <= 0:
            return

        # Reap references to completed writers before starting another one.
        self._cache_save_threads = {t for t in self._cache_save_threads if t.is_alive()}

        def worker() -> None:
            try:
                self._persistent_index_cache.save(identity, index)
            except Exception as error:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not persist path index: %s",
                    error,
                )

        thread = threading.Thread(
            target=worker,
            name="MoonrakerPrintFollowerCache",
            daemon=True,
        )
        self._cache_save_threads.add(thread)
        thread.start()

    def _try_load_persistent_index(self, filename: str) -> bool:
        if not filename or self._remote_file_identity is None:
            return False
        if self._metadata_job_key != self._remote_job_key:
            return False
        if not self._remote_file_identity.uuid and self._remote_file_identity.modified <= 0:
            return False
        if not self._remote_file_identity.matches_job(
            filename, self._remote_job_key[1] if self._remote_job_key else 0
        ):
            return False
        if (
            self._remote_index_filename == filename
            and self._remote_index_job_key == self._remote_job_key
            and self._remote_index_data is not None
        ):
            return True
        index = self._persistent_index_cache.load(self._remote_file_identity)
        if index is None:
            return False
        return self._install_remote_index(filename, index, self._remote_job_key, source="restored cached")

    def _ensure_remote_gcode_cached(self, filename: str) -> None:
        """Download the active job once without starting a path-index build."""

        if not filename:
            return
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            return
        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_key
            ):
                return
            self._abort_file_reply()

        self._begin_gcode_download(filename)

    def _ensure_remote_gcode_index(self, filename: str) -> None:
        if not filename or self._cura_load_in_progress or self._slicing_in_progress:
            return
        if (
            self._metadata_job_key != self._remote_job_key
            and self._metadata_reply is not None
            and self._metadata_reply.isRunning()
            and self._metadata_filename == filename
        ):
            return

        if self._try_load_persistent_index(filename):
            if self._remote_index_data is not None and getattr(self._remote_index_data, "compact", False):
                self._ensure_remote_gcode_cached(filename)
            return

        if (
            self._remote_index_filename == filename
            and self._remote_index_job_key == self._remote_job_key
        ):
            return

        if (
            self._remote_index_build_filename == filename
            and self._remote_index_build_job_key == self._remote_job_key
        ):
            return

        # If we already have the current job cached (for example because the user
        # just loaded it into Cura), build the path index from that local copy in a
        # worker thread instead of downloading it again.
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            self._start_remote_gcode_index_build_from_file(filename, self._cached_gcode_path)
            return

        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_key
            ):
                return
            self._abort_file_reply()

        self._begin_gcode_download(filename)

    def _begin_gcode_download(self, filename: str) -> bool:
        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url) or not filename:
            return False

        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_key
            ):
                return True
            self._abort_file_reply()

        try:
            job_dir = tempfile.mkdtemp(prefix="job-", dir=self._temp_gcode_dir.name)
            base_name = os.path.basename(filename.replace("\\", "/")) or "moonraker.gcode"
            if os.path.splitext(base_name)[1].lower() not in (".g", ".gcode"):
                base_name += ".gcode"
            target = DownloadTarget.open(os.path.join(job_dir, base_name))
        except Exception as error:
            Logger.logException("w", "Moonraker Print Follower could not create download target: %s", error)
            return False

        request = QNetworkRequest(QUrl(download_endpoint(base_url, filename)))
        request.setRawHeader(b"Accept", b"application/octet-stream")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(30000)
        api_key = self._pref_str(self.PREF_API_KEY)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))

        self._file_reply_filename = filename
        self._file_reply_generation = self._lifecycle_generation
        self._file_reply_job_key = self._remote_job_key
        self._file_download_target = target
        self._set_operation_phase(OperationPhase.DOWNLOADING, filename=filename)
        reply = self._file_network.get(request)
        try:
            reply.setReadBufferSize(4 * 1024 * 1024)
        except Exception:
            pass
        self._file_reply = reply
        reply_generation = self._file_reply_generation
        reply_job_key = self._file_reply_job_key
        reply.readyRead.connect(lambda r=reply: self._drain_gcode_reply(r))
        reply.finished.connect(
            lambda r=reply, f=filename, g=reply_generation, j=reply_job_key: self._handle_gcode_reply(r, f, g, j)
        )
        return True

    def _drain_gcode_reply(self, reply: QNetworkReply) -> None:
        if reply is not self._file_reply:
            return
        target = self._file_download_target
        if target is None:
            return
        try:
            chunk = reply.readAll()
            if chunk:
                target.write(chunk)
        except Exception as error:
            filename = self._file_reply_filename or self._force_load_pending_filename or "current print"
            forced = bool(
                self._force_load_requested
                and self._force_load_pending_filename == self._file_reply_filename
            )
            Logger.log("w", "Moonraker Print Follower streaming download write failed: %s", error)
            self._abort_file_reply()
            if forced:
                self._force_load_requested = False
                self._force_load_pending_filename = None
                self._set_operation_phase(OperationPhase.ERROR, filename=str(filename))
                self._set_status(f"Could not write downloaded G-code: {error}")

    def _start_remote_gcode_index_build_from_file(self, filename: str, path: str) -> None:
        if not filename or not path or not os.path.isfile(path):
            return
        job_key = self._remote_job_key
        if self._remote_index_filename == filename and self._remote_index_job_key == job_key:
            return
        if self._remote_index_build_filename == filename and self._remote_index_build_job_key == job_key:
            return
        if self._try_load_persistent_index(filename):
            return

        if self._remote_index_thread is not None and self._remote_index_thread.is_alive():
            self._cancel_remote_index_build(wait=True, timeout=0.5)
            if self._remote_index_thread is not None and self._remote_index_thread.is_alive():
                self._queue_lifecycle_callback(
                    lambda f=filename, p=path: self._start_remote_gcode_index_build_from_file(f, p),
                    100,
                )
                return

        generation = self._remote_index_generation
        lifecycle_generation = self._lifecycle_generation
        job_serial = job_key[2] if job_key is not None else 0
        cancel_event = threading.Event()
        self._remote_index_cancel_event = cancel_event
        self._remote_index_build_filename = filename
        self._remote_index_build_job_key = job_key
        self._set_operation_phase(OperationPhase.INDEXING, filename=filename)

        def worker() -> None:
            try:
                index = build_index_from_file(path, cancel_event)
            except Exception as error:
                Logger.logException(
                    "w",
                    "Moonraker Print Follower failed to index cached G-code %s: %s",
                    filename,
                    error,
                )
                index = LayerMotionIndex()
            if not self._destroyed and not cancel_event.is_set():
                self._remoteIndexReady.emit(
                    generation,
                    filename,
                    index,
                    lifecycle_generation,
                    job_serial,
                )

        thread = threading.Thread(
            target=worker,
            name="MoonrakerPrintFollowerIndex",
            daemon=True,
        )
        self._remote_index_thread = thread
        thread.start()

    @pyqtSlot(int, str, object, int, int)
    def _on_remote_index_ready(
        self,
        generation: int,
        filename: str,
        index,
        lifecycle_generation: int,
        job_serial: int,
    ) -> None:
        if generation != self._remote_index_generation:
            return
        if lifecycle_generation != self._lifecycle_generation:
            return
        if filename != self._last_remote_filename:
            return
        if self._remote_job_key is not None and job_serial != self._remote_job_key[2]:
            return

        self._remote_index_build_filename = None
        self._remote_index_build_job_key = None
        self._remote_index_cancel_event = None
        self._remote_index_thread = None
        if isinstance(index, LayerMotionIndex) and self._install_remote_index(
            filename, index, self._remote_job_key, source="built"
        ):
            if (
                self._remote_file_identity is not None
                and (self._remote_file_identity.uuid or self._remote_file_identity.modified > 0)
            ):
                self._persist_index_async(self._remote_file_identity, index)
            if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                self._queue_lifecycle_callback(lambda: self._poll(force=True))
        else:
            self._set_operation_phase(OperationPhase.READY, filename=filename)
            Logger.log(
                "w",
                "Moonraker Print Follower found no layer markers in remote G-code %s",
                filename,
            )

    def _handle_gcode_reply(
        self,
        reply: QNetworkReply,
        filename: str,
        reply_generation: int,
        reply_job_key: Optional[Tuple[str, int, int]],
    ) -> None:
        if reply is not self._file_reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return

        # Drain the final bytes before detaching the reply. readyRead is not
        # guaranteed to fire once more immediately before finished.
        self._drain_gcode_reply(reply)
        target = self._file_download_target
        self._file_reply = None
        self._file_reply_filename = None
        self._file_reply_job_key = None
        self._file_download_target = None

        try:
            if not filename or target is None:
                if target is not None:
                    target.abort(remove=True)
                return
            if reply_generation != self._lifecycle_generation or reply_job_key != self._remote_job_key:
                target.abort(remove=True)
                self._cleanup_cached_job_dir(target.path)
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                target.abort(remove=True)
                self._cleanup_cached_job_dir(target.path)
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not download %s: %s",
                    filename,
                    reply.errorString(),
                )
                if self._force_load_pending_filename == filename:
                    self._set_operation_phase(OperationPhase.ERROR, filename=filename)
                    self._set_status(
                        f"Could not download current print from Moonraker: {reply.errorString()}"
                    )
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                return

            target.flush_close()
            self._adopt_cached_gcode_path(filename, target.path, reply_job_key)
            identity = self._remote_file_identity
            if identity is not None and identity.size > 0 and target.bytes_written != identity.size:
                Logger.log(
                    "w",
                    "Moonraker Print Follower downloaded %d bytes for %s; metadata reported %d",
                    target.bytes_written,
                    filename,
                    identity.size,
                )
                bad_path = target.path
                self._discard_cached_gcode()
                self._cleanup_cached_job_dir(bad_path)
                if self._force_load_pending_filename == filename:
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                    self._set_operation_phase(OperationPhase.ERROR, filename=filename)
                    self._set_status(
                        f"Downloaded G-code size mismatch for {filename}; refusing to load a partial file"
                    )
                return

            forced_load = self._force_load_pending_filename == filename
            if forced_load:
                self._load_cached_remote_gcode_forced(filename)
            elif (
                self._pref_bool(self.PREF_PATH_FOLLOW)
                and self._cura_has_toolpath()
                and not self._cura_load_in_progress
            ):
                self._start_remote_gcode_index_build_from_file(filename, target.path)
            else:
                self._set_operation_phase(OperationPhase.READY, filename=filename)
        except Exception as error:
            Logger.logException(
                "w",
                "Moonraker Print Follower failed to process remote G-code %s: %s",
                filename,
                error,
            )
            if target is not None:
                target.abort(remove=True)
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass

    def _adopt_cached_gcode_path(
        self,
        filename: str,
        path: str,
        job_key: Optional[Tuple[str, int, int]] = None,
    ) -> None:
        old_path = self._cached_gcode_path
        self._cached_gcode_filename = filename
        self._cached_gcode_path = path
        self._cached_gcode_job_key = job_key if job_key is not None else self._remote_job_key
        if old_path and os.path.abspath(old_path) != os.path.abspath(path):
            self._cleanup_cached_job_dir(old_path)
        Logger.log(
            "i",
            "Moonraker Print Follower streamed remote G-code %s to %s",
            filename,
            path,
        )

    def _on_cura_file_completed(self, file_name: str) -> None:
        """Finish an explicit remote load without altering Cura's Prepare stage."""

        is_remote_file = False
        load_path = self._cura_load_path
        if load_path:
            try:
                is_remote_file = (
                    os.path.abspath(str(file_name)) == os.path.abspath(load_path)
                )
            except Exception:
                is_remote_file = False

        if not is_remote_file:
            if self._cura_load_in_progress:
                self._invalidate_lifecycle("another Cura file completed during remote load")
            self._bind_scene_structure_signal()
            self._scene_settle_until = time.monotonic() + 0.25
            self._refresh_simulation_view_connection()
            self._sync_preview_button_state()
            if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                self._queue_lifecycle_callback(lambda: self._poll(force=True), 100)
            return

        filename = self._cura_load_filename or self._cached_gcode_filename
        load_job_key = self._cura_load_job_key
        was_forced = bool(
            self._force_load_requested
            and filename
            and self._force_load_pending_filename == filename
        )

        load_seconds = None
        if self._cura_load_started_at is not None:
            load_seconds = max(0.0, time.perf_counter() - self._cura_load_started_at)
        self._cura_load_in_progress = False
        self._follow_controller.set_cura_suspended(False)
        self._cura_load_started_at = None
        self._cura_load_path = None
        self._cura_load_filename = None
        self._cura_load_job_key = None
        self._cleanup_deferred_cache_dirs()
        self._force_load_requested = False
        self._force_load_pending_filename = None
        self._preview_switched_for_job = True  # Cura's G-code loader switches to Preview itself.
        self._bind_scene_structure_signal()
        self._scene_settle_until = time.monotonic() + 0.25
        self._refresh_simulation_view_connection()

        # Layer data is populated asynchronously after readLocalFile(). Activity
        # changes normally update the UI, but this immediate refresh is harmless.
        self._sync_preview_button_state()

        if filename:
            Logger.log(
                "i",
                "Moonraker Print Follower loaded active remote G-code into Cura Preview: %s",
                filename,
            )
            if was_forced:
                suffix = f" in {load_seconds:.1f}s" if load_seconds is not None else ""
                self._set_status(f"{filename}: loaded current print into Cura Preview{suffix}")
                Logger.log(
                    "i",
                    "Moonraker Print Follower Cura G-code parse completed%s",
                    suffix,
                )
                # Build the motion index only after Cura has finished parsing, so
                # following work cannot slow the import itself.
                if (
                    self._pref_bool(self.PREF_PATH_FOLLOW)
                    and load_job_key == self._remote_job_key
                ):
                    self._queue_lifecycle_callback(lambda f=filename: self._ensure_remote_gcode_index(f))

                # The fileCompleted signal can precede SimulationView activity on
                # large G-code files. Poll now and again shortly afterwards; the
                # activityChanged hook will also force a catch-up when layers land.
                if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                    self._queue_lifecycle_callback(lambda: self._poll(force=True))
                    self._queue_lifecycle_callback(lambda: self._poll(force=True), 250)

    def _simulation_view(self):
        return self._refresh_simulation_view_connection()

    def _maybe_switch_to_preview(self) -> None:
        if self._preview_switched_for_job or not self._pref_bool(self.PREF_AUTO_PREVIEW):
            return
        try:
            self._controller.setActiveStage("PreviewStage")
            self._preview_switched_for_job = True
        except Exception as error:
            # Following can still work without forcing the Preview stage.
            Logger.log("w", "Moonraker Print Follower could not activate Preview: %s", error)

    def _layer_from_z(self, view, gcode_move: Dict[str, Any]) -> Optional[int]:
        """Best-effort fallback when print_stats.info.current_layer is absent.

        Z-hop can briefly place the nozzle at a different height.  To avoid
        chasing those moves, a candidate layer is accepted only on a poll where
        the reported G-code extruder position has advanced.
        """

        position = gcode_move.get("gcode_position")
        if not isinstance(position, (list, tuple)) or len(position) < 4:
            return None

        try:
            z = float(position[2])
            e = float(position[3])
        except (TypeError, ValueError):
            return None

        previous_e = self._last_extruder_position
        self._last_extruder_position = e
        if previous_e is None or e <= previous_e + 0.0001:
            return None

        try:
            max_layer = int(view.getMaxLayers())
        except Exception:
            return None
        if max_layer < 0:
            return None

        # SimulationView builds this cache when activated.  Calculate it on
        # demand if Cura has sliced data but the user has not opened Preview yet.
        calculate_cache = getattr(view, "_calculateLayerHeightsCache", None)
        if callable(calculate_cache):
            try:
                calculate_cache()
            except Exception:
                pass

        get_height = getattr(view, "_getLayerHeight", None)
        if not callable(get_height):
            return None

        tolerance = max(0.001, self._pref_float(self.PREF_Z_TOLERANCE, 0.04))
        best: Optional[Tuple[float, int]] = None
        for layer in range(max_layer + 1):
            try:
                height = float(get_height(layer))
            except Exception:
                continue
            if height <= 0:
                continue
            delta = abs(height - z)
            if delta <= tolerance and (best is None or delta < best[0]):
                best = (delta, layer)

        return best[1] if best is not None else None

    def _shutdown(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._timer.stop()
        self._manual_view_watch_timer.stop()
        self._invalidate_lifecycle("plugin shutdown")
        self._cancel_remote_index_build(wait=True, timeout=2.0)
        hydration_deadline = time.monotonic() + 1.0
        for thread in tuple(self._hydration_threads):
            remaining = max(0.0, hydration_deadline - time.monotonic())
            if remaining <= 0:
                break
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=remaining)
        self._hydration_threads.clear()
        self._hydrating_layers.clear()
        try:
            self._client.stop()
        except Exception:
            pass
        deadline = time.monotonic() + 1.5
        for thread in tuple(self._cache_save_threads):
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=remaining)
        self._cache_save_threads.clear()
        self._disconnect_simulation_view_connection()
        self._abort_metadata_reply()

        try:
            if self._scene_root is not None:
                self._scene_root.childrenChanged.disconnect(self._on_scene_children_changed)
        except Exception:
            pass
        self._hide_toolhead_indicator()
        try:
            if self._toolhead_indicator is not None:
                self._toolhead_indicator.setParent(None)
        except Exception:
            pass
        self._scene_root = None

        try:
            file_completed = getattr(self._application, "fileCompleted", None)
            if file_completed is not None:
                file_completed.disconnect(self._on_cura_file_completed)
        except Exception:
            pass
        try:
            main_window_changed = getattr(self._application, "mainWindowChanged", None)
            if main_window_changed is not None:
                main_window_changed.disconnect(self._on_main_window_changed)
        except Exception:
            pass
        try:
            global_stack_changed = getattr(self._application, "globalContainerStackChanged", None)
            if global_stack_changed is not None:
                global_stack_changed.disconnect(self._on_active_machine_changed)
        except Exception:
            pass
        try:
            active_view_changed = getattr(self._controller, "activeViewChanged", None)
            if active_view_changed is not None:
                active_view_changed.disconnect(self._on_active_view_changed)
        except Exception:
            pass
        try:
            active_stage_changed = getattr(self._controller, "activeStageChanged", None)
            if active_stage_changed is not None:
                active_stage_changed.disconnect(self._sync_preview_controls_visibility)
        except Exception:
            pass

        backend = getattr(self, "_backend", None)
        if backend is not None:
            for signal_name, handler in (
                ("slicingStarted", self._on_slicing_started),
                ("slicingCancelled", self._on_slicing_cancelled),
                ("backendStateChange", self._on_backend_state_changed),
            ):
                try:
                    signal = getattr(backend, signal_name, None)
                    if signal is not None:
                        signal.disconnect(handler)
                except Exception:
                    pass

        overlay = self._preview_overlay
        self._preview_overlay = None
        if overlay is not None:
            try:
                overlay.loadClicked.disconnect(self._confirm_force_load_current_print)
            except Exception:
                pass
            try:
                overlay.setProperty("visible", False)
                overlay.deleteLater()
            except Exception:
                pass

        controls = self._action_panel_controls
        self._action_panel_controls = None
        if controls is not None:
            self._remove_additional_component_reference(controls)
            try:
                controls.loadClicked.disconnect(self._confirm_force_load_current_print)
            except Exception:
                pass
            try:
                controls.pauseClicked.disconnect(self._toggle_following_pause)
            except Exception:
                pass
            try:
                controls.setProperty("visible", False)
                controls.deleteLater()
            except Exception:
                pass

        self._cura_load_in_progress = False
        self._cura_load_path = None
        self._cura_load_filename = None
        self._cura_load_job_key = None
        self._cleanup_deferred_cache_dirs()
        try:
            self._temp_gcode_dir.cleanup()
        except Exception:
            pass

    def deinitialize(self) -> None:
        """Stop timers, workers and Qt references before Cura unloads the plugin."""
        self._shutdown()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_status_text(
        self,
        filename: str,
        remote_layer: Optional[int],
        total_layer: Any,
        detail: str,
    ) -> str:
        name = filename or "remote print"
        if remote_layer is None:
            layer_text = "layer ?"
        else:
            try:
                total = int(total_layer) if total_layer is not None else 0
            except (TypeError, ValueError):
                total = 0
            layer_text = f"layer {remote_layer}/{total}" if total > 0 else f"layer {remote_layer}"
        return f"{name}: {layer_text} — {detail}"

    def _set_status(self, text: str) -> None:
        self._last_status_text = text
        self._sync_preview_button_state()

    def _valid_configured_url(self) -> bool:
        return self._url_is_usable(self._normalise_base_url(self._pref_str(self.PREF_URL)))

    @staticmethod
    def _url_is_usable(url: str) -> bool:
        parsed = QUrl(url)
        return parsed.isValid() and parsed.scheme() in ("http", "https") and bool(parsed.host())

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        value = (value or "").strip().rstrip("/")
        if not value:
            return "http://"
        if not value.lower().startswith(("http://", "https://")):
            value = f"http://{value}"
        return value

    def _pref_str(self, key: str) -> str:
        value = self._per_printer_pref_value(key)
        return "" if value is None else str(value)

    def _pref_bool(self, key: str) -> bool:
        value = self._per_printer_pref_value(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _pref_int(self, key: str, default: int) -> int:
        try:
            return int(self._per_printer_pref_value(key))
        except (TypeError, ValueError):
            return default

    def _pref_float(self, key: str, default: float) -> float:
        try:
            return float(self._per_printer_pref_value(key))
        except (TypeError, ValueError):
            return default

    def _per_printer_pref_value(self, key: str):
        """Bridge legacy call sites onto the active Cura printer's 1.1 config."""
        config = self._config_store.get()
        mapping = {
            self.PREF_ENABLED: config.enabled,
            self.PREF_URL: config.url,
            self.PREF_API_KEY: config.api_key,
            self.PREF_INTERVAL: config.poll_interval_ms,
            self.PREF_ONE_BASED: config.moonraker_layer_is_one_based,
            self.PREF_AUTO_PREVIEW: config.auto_preview,
            self.PREF_Z_FALLBACK: config.z_fallback,
            self.PREF_Z_TOLERANCE: config.z_tolerance,
            self.PREF_PATH_FOLLOW: config.path_follow,
        }
        if key in mapping:
            return mapping[key]
        return self._preferences.getValue(key)
