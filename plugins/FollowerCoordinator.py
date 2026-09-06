from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from UM.Logger import Logger

from .FollowerRuntime import MoonrakerPrintFollower as _FollowerRuntime
from .FollowerSession import FollowerSession
from .GCodeRepository import GCodeRepository
from .PauseScheduler import PauseScheduler
from .PreviewController import PreviewController
from .PrintTracker import PrintObservation, PrintTracker


class FollowerCoordinator(_FollowerRuntime):
    """Thin orchestration layer over the established Cura-facing runtime.

    Compatibility code may still refer to its historical private attribute names,
    but those names are properties backed by the extracted services below. This
    prevents the strangler boundary from creating two independent sources of truth.
    """

    def __init__(self, application) -> None:
        self._preview_controller = PreviewController()
        self._print_tracker = PrintTracker(self.ACTIVE_STATES)
        self._gcode_repository = GCodeRepository()
        self._follower_session = FollowerSession()
        self._pause_layers: set[int] = set()
        self._pause_scheduler = PauseScheduler(self._pause_layers)
        super().__init__(application)

        # The compatibility runtime has specialised reply lifecycles for metadata,
        # PAUSE and streamed G-code downloads. Keep those lifecycles, but put every
        # Moonraker request on the same Qt connection pool owned by the shared client.
        shared_network = self._client.transport.network
        self._network = shared_network
        self._pause_network = shared_network
        self._file_network = shared_network
        self._metadata_network = shared_network
        self._follower_session.bind_machine(self._active_machine_id, self._active_machine_name)

    # ------------------------------------------------------------------
    # Compatibility attribute bridge: extracted services are authoritative.
    # ------------------------------------------------------------------

    @property
    def _following_paused(self) -> bool:
        return bool(self._follower_session.following_paused)

    @_following_paused.setter
    def _following_paused(self, value: bool) -> None:
        self._follower_session.following_paused = bool(value)

    @property
    def _remote_job_key(self) -> Optional[Tuple[str, int, int]]:
        return self._follower_session.remote_job_key

    @_remote_job_key.setter
    def _remote_job_key(self, value: Optional[Tuple[str, int, int]]) -> None:
        self._follower_session.set_job(value)

    @property
    def _remote_job_serial(self) -> int:
        return int(self._print_tracker.serial)

    @_remote_job_serial.setter
    def _remote_job_serial(self, value: int) -> None:
        self._print_tracker.serial = max(0, int(value or 0))

    @property
    def _last_remote_file_position(self) -> Optional[int]:
        return self._print_tracker.last_file_position

    @_last_remote_file_position.setter
    def _last_remote_file_position(self, value: Optional[int]) -> None:
        self._print_tracker.last_file_position = None if value is None else int(value)

    @property
    def _last_remote_print_duration(self) -> Optional[float]:
        return self._print_tracker.last_print_duration

    @_last_remote_print_duration.setter
    def _last_remote_print_duration(self, value: Optional[float]) -> None:
        self._print_tracker.last_print_duration = None if value is None else float(value)

    @property
    def _scheduled_pause_layers(self) -> set[int]:
        return self._pause_layers

    @_scheduled_pause_layers.setter
    def _scheduled_pause_layers(self, value) -> None:
        self._pause_layers.clear()
        if value:
            self._pause_layers.update(int(item) for item in value)

    @property
    def _expected_follow_layer(self) -> Optional[int]:
        return self._preview_controller.expected.layer

    @_expected_follow_layer.setter
    def _expected_follow_layer(self, value: Optional[int]) -> None:
        self._preview_controller.expected.layer = None if value is None else int(value)

    @property
    def _expected_follow_minimum_layer(self) -> Optional[int]:
        return self._preview_controller.expected.minimum_layer

    @_expected_follow_minimum_layer.setter
    def _expected_follow_minimum_layer(self, value: Optional[int]) -> None:
        self._preview_controller.expected.minimum_layer = None if value is None else int(value)

    @property
    def _expected_follow_path(self) -> Optional[float]:
        return self._preview_controller.expected.path

    @_expected_follow_path.setter
    def _expected_follow_path(self, value: Optional[float]) -> None:
        self._preview_controller.expected.path = None if value is None else float(value)

    @property
    def _expected_follow_minimum_path(self) -> Optional[int]:
        return self._preview_controller.expected.minimum_path

    @_expected_follow_minimum_path.setter
    def _expected_follow_minimum_path(self, value: Optional[int]) -> None:
        self._preview_controller.expected.minimum_path = None if value is None else int(value)

    @property
    def _cached_gcode_filename(self) -> Optional[str]:
        return self._gcode_repository.cached.filename

    @_cached_gcode_filename.setter
    def _cached_gcode_filename(self, value: Optional[str]) -> None:
        self._gcode_repository.cached.filename = None if value is None else str(value)

    @property
    def _cached_gcode_path(self) -> Optional[str]:
        return self._gcode_repository.cached.path

    @_cached_gcode_path.setter
    def _cached_gcode_path(self, value: Optional[str]) -> None:
        self._gcode_repository.cached.path = None if value is None else str(value)

    @property
    def _cached_gcode_job_key(self) -> Optional[Tuple[str, int, int]]:
        return self._gcode_repository.cached.job_key

    @_cached_gcode_job_key.setter
    def _cached_gcode_job_key(self, value: Optional[Tuple[str, int, int]]) -> None:
        self._gcode_repository.cached.job_key = value

    @property
    def session(self):
        return self._client.session

    def _apply_timer_state(self) -> None:
        config = self._config_store.get()
        self._follow_controller.set_enabled(config.enabled)
        base_url = self._normalise_base_url(config.url)
        self._client.configure(base_url, config.api_key, config.poll_interval_ms)

        # Keep one core HTTP session alive for the configured active printer even
        # when Preview following is disabled. Monitor consumes the same stream.
        if self._url_is_usable(base_url):
            self._client.start()
            if config.enabled:
                self._update_manual_view_watch_mode()
            else:
                self._manual_view_watch_timer.stop()
                self._clear_expected_preview_position()
        else:
            self._client.stop()
            self._manual_view_watch_timer.stop()
            self._clear_expected_preview_position()
            if config.enabled:
                self._set_status("Set a Moonraker URL for this Cura printer")

    def _on_client_status(self, status) -> None:
        if not isinstance(status, dict):
            return
        # Monitor is connected directly to the shared client and still receives
        # status while Preview following is disabled. Do not move Cura in that mode.
        if not self._pref_bool(self.PREF_ENABLED):
            return
        super()._on_client_status(status)

    def _update_remote_job_identity(
        self,
        print_stats: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
    ) -> Optional[Tuple[str, int, int]]:
        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")
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

        transition = self._print_tracker.observe(
            PrintObservation(state, filename, file_size, file_position, print_duration),
            previous_state=str(self._last_remote_state or ""),
        )
        if transition.new_job:
            self._clear_remote_gcode_index()
            self._last_observed_remote_layer = None
            self._clear_scheduled_pauses(abort_request=True)
            self._remote_job_key = transition.key
            if (
                self._remote_file_identity is not None
                and transition.key is not None
                and not self._remote_file_identity.matches_job(transition.key[0], transition.key[1])
            ):
                self._remote_file_identity = None
            Logger.log(
                "i",
                "Moonraker Print Follower detected new print run #%d: %s (%d bytes)",
                transition.serial,
                filename,
                file_size,
            )
        else:
            self._remote_job_key = transition.key

        return self._remote_job_key

    def _clear_expected_preview_position(self) -> None:
        self._preview_controller.clear()

    def _remember_plugin_preview_position(self, view) -> None:
        self._preview_controller.remember(view)

    def _scheduler(self) -> PauseScheduler:
        return self._pause_scheduler

    def _toggle_pause_at_selected_layer(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return
        scheduler = self._scheduler()
        if layer in self._scheduled_pause_layers:
            scheduler.remove(layer)
            self._set_status(f"Removed end-of-layer PAUSE after layer {layer + 1}")
            self._sync_preview_button_state()
            return
        state = self._pause_at_layer_preview_state()
        if int(state.get("candidate") or 0) != layer + 1 or not bool(state.get("canToggle")):
            self._sync_preview_button_state()
            return
        scheduler.schedule(layer)
        self._set_status(f"PAUSE scheduled for end of layer {layer + 1}")
        self._sync_preview_button_state()

    def _remove_scheduled_pause(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return
        if self._scheduler().remove(layer):
            self._set_status(f"Removed end-of-layer PAUSE after layer {layer + 1}")
        self._sync_preview_button_state()

    def _clear_scheduled_pauses_from_preview(self) -> None:
        count = self._scheduler().clear()
        if count:
            suffix = "pause" if count == 1 else "pauses"
            self._set_status(f"Cleared {count} scheduled end-of-layer {suffix}")
        self._sync_preview_button_state()

    def _clear_scheduled_pauses(self, *, abort_request: bool = False) -> None:
        self._scheduler().clear()
        if abort_request:
            self._abort_pause_reply()
        self._sync_preview_button_state()

    def _maybe_trigger_scheduled_pause(self, current_layer: int) -> None:
        if not self._scheduled_pause_layers or self._remote_job_key is None:
            return
        try:
            current_layer = int(current_layer)
        except (TypeError, ValueError):
            return
        due = self._scheduler().consume_due(current_layer)
        if not due:
            return
        self._sync_preview_button_state()
        self._send_scheduled_pause(due[0], current_layer)

    def _adopt_cached_gcode_path(
        self,
        filename: str,
        path: str,
        job_key: Optional[Tuple[str, int, int]] = None,
    ) -> None:
        effective_key = job_key if job_key is not None else self._remote_job_key
        old_path = self._gcode_repository.adopt(filename, path, effective_key)
        if old_path and os.path.abspath(old_path) != os.path.abspath(path):
            self._cleanup_cached_job_dir(old_path)
        Logger.log("i", "Moonraker Print Follower streamed remote G-code %s to %s", filename, path)

    def _discard_cached_gcode(self) -> None:
        old_path = self._gcode_repository.discard()
        self._cleanup_cached_job_dir(old_path)

    def _on_active_machine_changed(self, *_args) -> None:
        before = getattr(self, "_active_machine_id", "unknown")
        super()._on_active_machine_changed(*_args)
        if self._active_machine_id != before:
            self._print_tracker.reset()
            self._follower_session.set_job(None)
        self._follower_session.bind_machine(self._active_machine_id, self._active_machine_name)

    def _invalidate_lifecycle(self, reason: str, abort_network: bool = True) -> None:
        super()._invalidate_lifecycle(reason, abort_network=abort_network)
        self._follower_session.invalidate(reason, self._lifecycle_generation)
