from __future__ import annotations

import os
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer
from UM.Extension import Extension
from UM.Resources import Resources

from .Core import OperationContext, OperationPhase, RemoteFileIdentity
from .CuraAdapter import active_machine_identity
from .FollowController import FollowController
from .MoonrakerClient import MoonrakerClient
from .PrinterConfig import PrinterConfigStore
from .GCodeIndex import PersistentIndexCache


class FollowerBootstrapMixin:
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

        self._reply = None
        self._reply_purpose: Optional[str] = None

        # Preview-scheduled PAUSE commands are intentionally print-local.
        # They are never persisted into PrinterConfig because carrying a layer
        # number into a different G-code file would be unsafe and surprising.
        self._pause_reply = None
        self._pause_reply_generation = 0
        self._pause_reply_job_key: Optional[Tuple[str, int, int]] = None
        self._scheduled_pause_layers: set[int] = set()
        self._last_observed_remote_layer: Optional[int] = None

        # Large G-code downloads use the shared transport connection pool.
        self._file_reply = None
        self._file_reply_filename: Optional[str] = None
        self._file_reply_generation = 0
        self._file_reply_job_key: Optional[Tuple[str, int, int]] = None
        self._file_download_target = None

        self._metadata_reply = None
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
        self._toolhead_path_valid = False
        # Within a running layer, displayed path progress is monotonic. Live XYZ
        # refinement can otherwise snap to an earlier occurrence of the same XY
        # on closed/repeated geometry and visibly rewind Cura's path slider.
        self._path_progress_layer: Optional[int] = None
        self._path_progress_fraction: Optional[float] = None
        self._last_resolved_remote_layer: Optional[int] = None
        self._selected_layer_eta_text = ""
        self._last_speed_factor = 1.0
        # ETA estimates use the slicer's cumulative layer timings, but keep an
        # independent Moonraker print-duration anchor for the current layer.
        # This lets ETAs continue counting down while Cura Preview is detached
        # and its own path slider is no longer being advanced by the follower.
        self._eta_anchor_layer: Optional[int] = None
        self._eta_anchor_print_duration: Optional[float] = None
        self._eta_current_print_duration: Optional[float] = None
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
        self._remote_index_data = None
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
        # BackendState.Done is the authoritative slicing-completion point across Cura 5.x.
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
