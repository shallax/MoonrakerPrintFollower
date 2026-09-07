from __future__ import annotations

import os
import tempfile
import threading
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer
from UM.Extension import Extension
from UM.Resources import Resources

from .Core import OperationContext, OperationPhase
from .CuraAdapter import active_machine_identity
from .FollowController import FollowController
from .GCodeIndex import PersistentIndexCache
from .MoonrakerClient import MoonrakerClient
from .PrinterConfig import PrinterConfigStore


class FollowerBootstrapMixin:
    def _set_operation_phase(
        self,
        phase: OperationPhase,
        *,
        filename: Optional[str] = None,
        message: str = "",
    ) -> None:
        self._operation.transition(
            phase,
            filename=filename,
            job_key=self._remote_job_service.key,
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

        self._config_store = PrinterConfigStore(
            self._preferences, lambda: active_machine_identity(self._application)
        )
        self._active_machine_id, self._active_machine_name = self._config_store.identity()
        self._follow_controller = FollowController()
        self._follow_controller.set_enabled(self._config_store.get().enabled)
        self._last_capabilities: Dict[str, Any] = {}

        self._client = MoonrakerClient(self)
        self._client.statusReceived.connect(self._on_client_status)
        self._client.connectionChanged.connect(self._on_client_connection_changed)
        self._client.capabilitiesChanged.connect(self._on_client_capabilities_changed)

        # Explicit status probes use a separate request lane only when testing an
        # identity other than the active shared session.
        self._reply = None
        self._reply_purpose: Optional[str] = None

        # Scheduled PAUSE request lifecycle. The schedule itself is owned by
        # PauseScheduleService and is intentionally print-local.
        self._pause_reply = None
        self._pause_reply_generation = 0
        self._pause_reply_job_key: Optional[Tuple[str, int, int]] = None
        self._last_observed_remote_layer: Optional[int] = None

        # Large G-code downloads use the shared transport connection pool while
        # retaining their own streaming QNetworkReply lifecycle.
        self._file_reply = None
        self._file_reply_filename: Optional[str] = None
        self._file_reply_generation = 0
        self._file_reply_job_key: Optional[Tuple[str, int, int]] = None
        self._file_download_target = None

        self._metadata_reply = None
        self._metadata_filename: Optional[str] = None
        self._metadata_reply_generation = 0
        self._metadata_reply_job_key: Optional[Tuple[str, int, int]] = None

        cache_dir = os.path.join(Resources.getCacheStoragePath(), self.PLUGIN_ID, "indexes")
        self._persistent_index_cache = PersistentIndexCache(cache_dir)
        self._cache_save_threads: set[threading.Thread] = set()

        self._last_status_text = "Not connected"
        self._last_remote_filename: Optional[str] = None
        self._last_remote_state: Optional[str] = None
        self._last_extruder_position: Optional[float] = None
        self._preview_switched_for_job = False
        self._last_source: Optional[str] = None
        self._follow_controller.resume()

        # Detect direct user interaction with Cura's Preview layer/path controls.
        # Follower-written values are remembered by PreviewFollowerService; a
        # later deviation is classified as a manual detach.
        self._manual_view_watch_timer = QTimer()
        self._manual_view_watch_timer.setInterval(75)
        self._manual_view_watch_timer.timeout.connect(self._watch_for_manual_preview_change)
        self._manual_view_signals_connected = False
        self._applying_follow_update = 0

        self._preview_overlay = None
        self._action_panel_controls = None
        self._connected_simulation_view = None
        self._toolhead_path_valid = False

        # Within a running layer, displayed path progress is monotonic.
        self._path_progress_layer: Optional[int] = None
        self._path_progress_fraction: Optional[float] = None
        self._last_resolved_remote_layer: Optional[int] = None
        self._selected_layer_eta_text = ""
        self._last_speed_factor = 1.0
        self._eta_anchor_layer: Optional[int] = None
        self._eta_anchor_print_duration: Optional[float] = None
        self._eta_current_print_duration: Optional[float] = None

        self._scene = None
        self._scene_root = None
        self._destroyed = False
        self._slicing_in_progress = False
        self._scene_settle_until = 0.0

        # Cached G-code must outlive readLocalFile(), which parses asynchronously.
        self._temp_gcode_dir = tempfile.TemporaryDirectory(
            prefix="cura-moonraker-print-follower-"
        )
        self._deferred_cache_dirs: set[str] = set()

        self._remoteIndexReady.connect(self._on_remote_index_ready)
        self._remoteLayerHydrated.connect(self._on_remote_layer_hydrated)

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

        # SceneNode.childrenChanged is structural; broad sceneChanged signals also
        # fire for redraw/transform activity and are intentionally not used here.
        self._bind_scene_structure_signal()

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

        global_stack_changed = getattr(
            self._application, "globalContainerStackChanged", None
        )
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
