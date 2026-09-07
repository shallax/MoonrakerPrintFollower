from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QTimer
from UM.Logger import Logger

from .Core import OperationPhase
from .CuraLifecycleBridge import CuraLifecycleBridge
from .FollowerRuntime import MoonrakerPrintFollower as _FollowerRuntime
from .FollowerTransport import FollowerTransportMixin
from .GCodeIndex import LayerMotionIndex
from .GCodeIndexService import GCodeIndexService
from .PauseScheduleService import PauseScheduleService
from .PreviewFollowerService import PreviewFollowerService
from .RemoteFileService import RemoteFileService
from .RemoteJobService import RemoteJobService


class FollowerCoordinator(FollowerTransportMixin, _FollowerRuntime):
    """Compose the follower's domain services and coordinate cross-domain events."""

    def __init__(self, application) -> None:
        self._remote_job_service = RemoteJobService(self.ACTIVE_STATES)
        self._remote_file_service = RemoteFileService()
        self._gcode_index_service = GCodeIndexService()
        self._preview_follower_service = PreviewFollowerService()
        self._pause_schedule_service = PauseScheduleService()
        self._cura_lifecycle_bridge = CuraLifecycleBridge()
        super().__init__(application)

    @property
    def client(self):
        """The active shared Moonraker client consumed by Monitor/output integrations."""
        return self._client

    @property
    def session(self):
        """The active shared Moonraker session consumed by plugin integrations."""
        return self._client.session

    @property
    def transport(self):
        """The active shared HTTP transport/connection pool."""
        return self._client.transport

    @property
    def gcode_index(self) -> GCodeIndexService:
        """Read-only service boundary used by Monitor for physical-layer mapping."""
        return self._gcode_index_service

    # ------------------------------------------------------------------
    # Shared session and lifecycle coordination
    # ------------------------------------------------------------------

    def _apply_timer_state(self) -> None:
        config = self.current_printer_config()
        self._follow_controller.set_enabled(config.enabled)
        base_url = self._normalise_base_url(config.url)
        self._client.configure(base_url, config.api_key, config.poll_interval_ms)
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
        self._consume_pending_shared_status(status)
        if not self.current_printer_config().enabled:
            return
        super()._on_client_status(status)

    def _queue_lifecycle_callback(self, callback, delay_ms: int = 0) -> None:
        token = self._cura_lifecycle_bridge.token()

        def run() -> None:
            if self._destroyed or not self._cura_lifecycle_bridge.is_current(token):
                return
            try:
                callback()
            except Exception as error:
                Logger.logException(
                    "e", "Moonraker Print Follower delayed callback failed: %s", error
                )

        QTimer.singleShot(delay_ms, run)

    def _invalidate_lifecycle(self, reason: str, abort_network: bool = True) -> None:
        super()._invalidate_lifecycle(reason, abort_network=abort_network)
        self._client.set_pause_guard(False)

    # ------------------------------------------------------------------
    # Remote print, file and index coordination
    # ------------------------------------------------------------------

    def _update_remote_job_identity(
        self,
        print_stats: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
    ) -> Optional[Tuple[str, int, int]]:
        filename = str(print_stats.get("filename") or "")
        try:
            file_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0

        transition = self._remote_job_service.observe(
            print_stats,
            virtual_sdcard,
            previous_state=str(self._last_remote_state or ""),
        )
        if transition.new_job:
            self._clear_remote_gcode_index()
            self._last_observed_remote_layer = None
            self._clear_scheduled_pauses(abort_request=True)
            identity = self._remote_file_service.identity
            if (
                identity is not None
                and transition.key is not None
                and not identity.matches_job(transition.key[0], transition.key[1])
            ):
                self._remote_file_service.clear_identity()
            Logger.log(
                "i",
                "Moonraker Print Follower detected new print run #%d: %s (%d bytes)",
                transition.serial,
                filename,
                file_size,
            )
        return transition.key

    def _adopt_cached_gcode_path(
        self,
        filename: str,
        path: str,
        job_key: Optional[Tuple[str, int, int]] = None,
    ) -> None:
        effective_key = job_key if job_key is not None else self._remote_job_service.key
        old_path = self._remote_file_service.adopt(filename, path, effective_key)
        if old_path and os.path.abspath(old_path) != os.path.abspath(path):
            self._cleanup_cached_job_dir(old_path)
        Logger.log(
            "i",
            "Moonraker Print Follower streamed remote G-code %s to %s",
            filename,
            path,
        )

    def _discard_cached_gcode(self) -> None:
        old_path = self._remote_file_service.discard_cache()
        self._cleanup_cached_job_dir(old_path)

    def _clear_remote_gcode_index(self) -> None:
        self._cancel_remote_index_build()
        self._gcode_index_service.clear_hydrations()
        self._path_progress_layer = None
        self._path_progress_fraction = None
        self._last_resolved_remote_layer = None
        self._eta_anchor_layer = None
        self._eta_anchor_print_duration = None
        self._eta_current_print_duration = None
        self._selected_layer_eta_text = ""
        self._gcode_index_service.clear_index()
        self._discard_cached_gcode()
        self._abort_file_reply()

    def _install_remote_index(
        self,
        filename: str,
        index: LayerMotionIndex,
        job_key=None,
        *,
        source: str = "built",
    ) -> bool:
        current_job = self._remote_job_service.key
        if not index or filename != self._last_remote_filename:
            return False
        if job_key is not None and current_job is not None and job_key != current_job:
            return False
        if not self._gcode_index_service.install(filename, index, current_job):
            return False
        self._set_operation_phase(OperationPhase.READY, filename=filename)
        Logger.log(
            "i",
            "Moonraker Print Follower %s %d-layer path index for %s",
            source,
            len(index.ranges),
            filename,
        )
        return True

    # ------------------------------------------------------------------
    # Preview attachment
    # ------------------------------------------------------------------

    def _clear_expected_preview_position(self) -> None:
        self._preview_follower_service.clear()

    def _remember_plugin_preview_position(self, view) -> None:
        self._preview_follower_service.remember(view)

    def _check_for_manual_preview_change(self) -> None:
        if (
            not self.current_printer_config().enabled
            or self._preview_follower_service.following_paused
        ):
            return
        if self._applying_follow_update:
            return
        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            return
        view = self._simulation_view()
        if view is None:
            return
        override_kind = self._preview_follower_service.classify_manual_override(view)
        if override_kind is None:
            return

        self._preview_follower_service.set_paused(True)
        self._toolhead_path_valid = False
        self._hide_toolhead_indicator()
        self._follow_controller.pause_by_user(f"manual {override_kind} change")
        self._preview_follower_service.remember(view)
        self._sync_preview_button_state()
        if override_kind == "layer":
            self._set_status(
                "Following paused because the Preview layer range was changed manually"
            )
        else:
            self._set_status(
                "Following paused because the Preview path position was changed manually"
            )

    # ------------------------------------------------------------------
    # Scheduled PAUSE and precision polling
    # ------------------------------------------------------------------

    def _update_pause_poll_guard(self, current_layer: Optional[int] = None) -> None:
        if current_layer is None:
            current_layer = self._last_observed_remote_layer
        active = False
        if current_layer is not None and self._remote_job_service.key is not None:
            active = self._pause_schedule_service.is_imminent(
                current_layer, lookahead_layers=1
            )
        self._client.set_pause_guard(active)

    def _toggle_pause_at_selected_layer(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return
        if layer in self._pause_schedule_service.layers:
            self._pause_schedule_service.remove(layer)
            self._update_pause_poll_guard()
            self._set_status(f"Removed end-of-layer PAUSE after layer {layer + 1}")
            self._sync_preview_button_state()
            return
        state = self._pause_at_layer_preview_state()
        if int(state.get("candidate") or 0) != layer + 1 or not bool(
            state.get("canToggle")
        ):
            self._sync_preview_button_state()
            return
        self._pause_schedule_service.schedule(layer)
        self._update_pause_poll_guard()
        self._set_status(f"PAUSE scheduled for end of layer {layer + 1}")
        self._sync_preview_button_state()

    def _remove_scheduled_pause(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return
        if self._pause_schedule_service.remove(layer):
            self._set_status(f"Removed end-of-layer PAUSE after layer {layer + 1}")
        self._update_pause_poll_guard()
        self._sync_preview_button_state()

    def _clear_scheduled_pauses_from_preview(self) -> None:
        count = self._pause_schedule_service.clear()
        self._client.set_pause_guard(False)
        if count:
            suffix = "pause" if count == 1 else "pauses"
            self._set_status(f"Cleared {count} scheduled end-of-layer {suffix}")
        self._sync_preview_button_state()

    def _clear_scheduled_pauses(self, *, abort_request: bool = False) -> None:
        self._pause_schedule_service.clear()
        self._client.set_pause_guard(False)
        if abort_request:
            self._abort_pause_reply()
        self._sync_preview_button_state()

    def _maybe_trigger_scheduled_pause(self, current_layer: int) -> None:
        if not self._pause_schedule_service.layers or self._remote_job_service.key is None:
            self._client.set_pause_guard(False)
            return
        try:
            current_layer = int(current_layer)
        except (TypeError, ValueError):
            self._client.set_pause_guard(False)
            return
        due = self._pause_schedule_service.consume_due(current_layer)
        self._update_pause_poll_guard(current_layer)
        if not due:
            return
        self._sync_preview_button_state()
        self._send_scheduled_pause(due[0], current_layer)
