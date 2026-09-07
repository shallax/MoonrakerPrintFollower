from __future__ import annotations

import os
import threading
import time
from UM.Logger import Logger


class CuraFileLifecycleMixin:
    def _on_cura_file_completed(self, file_name: str) -> None:
        """Finish an explicit remote load without altering Cura's Prepare stage."""
        is_remote_file = False
        load_path = self._cura_load_path
        if load_path:
            try:
                is_remote_file = os.path.abspath(str(file_name)) == os.path.abspath(load_path)
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
        self._preview_switched_for_job = True
        self._bind_scene_structure_signal()
        self._scene_settle_until = time.monotonic() + 0.25
        self._refresh_simulation_view_connection()
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
                Logger.log("i", "Moonraker Print Follower Cura G-code parse completed%s", suffix)
                if self._pref_bool(self.PREF_PATH_FOLLOW) and load_job_key == self._remote_job_key:
                    self._queue_lifecycle_callback(lambda f=filename: self._ensure_remote_gcode_index(f))
                if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                    self._queue_lifecycle_callback(lambda: self._poll(force=True))
                    self._queue_lifecycle_callback(lambda: self._poll(force=True), 250)

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
        self._scene_root = None
        for owner, signal_name, handler in (
            (self._application, "fileCompleted", self._on_cura_file_completed),
            (self._application, "mainWindowChanged", self._on_main_window_changed),
            (self._application, "globalContainerStackChanged", self._on_active_machine_changed),
            (self._controller, "activeViewChanged", self._on_active_view_changed),
            (self._controller, "activeStageChanged", self._sync_preview_controls_visibility),
        ):
            try:
                signal = getattr(owner, signal_name, None)
                if signal is not None:
                    signal.disconnect(handler)
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
