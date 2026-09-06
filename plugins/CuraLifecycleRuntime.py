from __future__ import annotations

import time
from UM.Backend.Backend import BackendState
from UM.Logger import Logger
from .Core import OperationPhase


class CuraLifecycleRuntimeMixin:
    def _bind_scene_structure_signal(self) -> None:
        old_root = getattr(self, "_scene_root", None)
        if old_root is not None:
            try:
                old_root.childrenChanged.disconnect(self._on_scene_children_changed)
            except Exception:
                pass
        try:
            self._scene = self._controller.getScene()
            self._scene_root = self._scene.getRoot()
            self._scene_root.childrenChanged.connect(self._on_scene_children_changed)
        except Exception:
            self._scene = None
            self._scene_root = None

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
        if not was_loading:
            self._cleanup_deferred_cache_dirs()
        self._force_load_requested = False
        self._force_load_pending_filename = None
        self._operation.reset(OperationPhase.IDLE)
        Logger.log("d", "Moonraker Print Follower invalidated scene lifecycle: %s", reason)

    def _on_scene_children_changed(self, source=None) -> None:
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
        self._refresh_simulation_view_connection()
        self._sync_preview_controls_visibility()

    def _on_main_window_changed(self, *_args) -> None:
        self._refresh_simulation_view_connection()
        self._reparent_preview_overlay()
        if self._preview_overlay is None or self._action_panel_controls is None:
            self._create_preview_controls()
        try:
            self._application.additionalComponentsChanged.emit("saveButton")
        except Exception:
            pass
        self._sync_preview_controls_visibility()
