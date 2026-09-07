from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from .FollowController import decide_layers


class PreviewFollowEngineMixin:
    """Translate shared Moonraker status into Cura Preview layer state.

    This mixin owns orchestration only. Print-run identity, Preview expectations,
    pause schedules, file/index state and transport are owned by their dedicated
    services/adapters and are reached through the coordinator.
    """

    def _on_client_status(self, status) -> None:
        if not isinstance(status, dict):
            return
        print_stats = status.get("print_stats") or {}
        gcode_move = status.get("gcode_move") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        motion_report = status.get("motion_report") or {}
        self._follow_controller.set_connection(True)
        self._apply_remote_status(print_stats, gcode_move, virtual_sdcard, motion_report)

    def _resolve_remote_layer_index(
        self,
        print_stats: Dict[str, Any],
        gcode_move: Dict[str, Any],
        filename: str,
        view=None,
    ) -> Tuple[Optional[int], str]:
        info = print_stats.get("info") or {}
        remote_layer = info.get("current_layer")
        target_layer: Optional[int] = None
        source = ""

        if remote_layer is not None:
            try:
                raw_remote_layer = int(remote_layer)
                if (
                    self._gcode_index_service.filename == filename
                    and raw_remote_layer in self._gcode_index_service.current_layer_map
                ):
                    target_layer = self._gcode_index_service.current_layer_map[raw_remote_layer]
                    source = "Moonraker current_layer (G-code mapped)"
                else:
                    target_layer = raw_remote_layer
                    if self.current_printer_config().moonraker_layer_is_one_based:
                        target_layer -= 1
                    source = "Moonraker current_layer"
            except (TypeError, ValueError):
                target_layer = None

        if target_layer is None and self.current_printer_config().z_fallback and view is not None:
            target_layer = self._layer_from_z(view, gcode_move)
            if target_layer is not None:
                source = "Z-height fallback"

        return target_layer, source

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

        previous_observation = self._remote_job_service.observation
        previous_filename = previous_observation.filename if previous_observation is not None else ""
        previous_state = previous_observation.state if previous_observation is not None else ""
        self._update_remote_job_identity(print_stats, virtual_sdcard)

        tracking = self._preview_follower_service.tracking
        runtime = self._preview_follower_service.runtime
        try:
            tracking.speed_factor = max(
                0.05, float(gcode_move.get("speed_factor") or 1.0)
            )
        except (TypeError, ValueError):
            tracking.speed_factor = 1.0
        try:
            tracking.eta_current_print_duration = max(
                0.0, float(print_stats.get("print_duration") or 0.0)
            )
        except (TypeError, ValueError):
            tracking.eta_current_print_duration = None
        try:
            reported_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            reported_size = 0
        if filename and state in self.ACTIVE_STATES:
            self._ensure_remote_metadata(filename, reported_size)

        if filename != previous_filename:
            runtime.toolhead_path_valid = False
            self._hide_toolhead_indicator()
            runtime.last_extruder_position = None
            runtime.preview_switched_for_job = False

        if state != previous_state and state not in self.ACTIVE_STATES:
            runtime.last_extruder_position = None
            runtime.preview_switched_for_job = False

        if state not in self.ACTIVE_STATES:
            self._preview_follower_service.reset_print_state()
            if self._pause_schedule_service.layers:
                self._clear_scheduled_pauses(abort_request=True)
            self._hide_toolhead_indicator()
            label = state or "unknown"
            suffix = f" — {filename}" if filename else ""
            self._set_status(f"Moonraker connected; printer is {label}{suffix}")
            return

        # Physical layer observation deliberately continues while Preview is
        # detached. Scheduled end-of-layer PAUSE and ETA state are printer state,
        # not Cura-slider state.
        view = self._simulation_view()
        observed_layer, observed_source = self._resolve_remote_layer_index(
            print_stats, gcode_move, filename, view
        )
        if observed_layer is not None:
            observed_layer = max(0, int(observed_layer))
            self._preview_follower_service.observe_remote_layer(observed_layer)
            self._maybe_trigger_scheduled_pause(observed_layer)
            if view is not None and hasattr(view, "getMaxLayers"):
                try:
                    observed_max = max(0, int(view.getMaxLayers()))
                    tracking.resolved_remote_layer = min(observed_layer, observed_max)
                except Exception:
                    pass

        if self._preview_follower_service.following_paused:
            runtime.toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._update_selected_layer_eta(view)
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=(runtime.observed_remote_layer + 1)
                    if runtime.observed_remote_layer is not None
                    else None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="following paused; Moonraker polling continues",
                )
            )
            return

        if self.current_printer_config().path_follow and filename:
            if self._cura_has_toolpath() and not self._slicing_in_progress:
                self._ensure_remote_gcode_index(filename)
            else:
                self._ensure_remote_gcode_cached(filename)

        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            runtime.toolhead_path_valid = False
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

        if view is None or not hasattr(view, "setLayer"):
            runtime.toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status("Connected, but Cura's SimulationView is unavailable")
            return

        self._maybe_switch_to_preview()

        info = print_stats.get("info") or {}
        total_layer = info.get("total_layer")
        target_layer = observed_layer
        source = observed_source
        if target_layer is None:
            runtime.toolhead_path_valid = False
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
            max_layer = (
                int(view.getMaxLayers()) if hasattr(view, "getMaxLayers") else target_layer
            )
        except Exception:
            max_layer = target_layer

        remote_target_layer = max(0, min(target_layer, max(0, max_layer)))
        tracking.resolved_remote_layer = remote_target_layer
        decision = decide_layers(
            remote_target_layer, max_layer, self._config_store.get().follow_mode
        )
        target_layer = decision.current_layer
        minimum_layer = decision.minimum_layer

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
            current_minimum = (
                int(view.getMinimumLayer()) if hasattr(view, "getMinimumLayer") else None
            )
        except Exception:
            current_minimum = None

        path_detail = ""
        runtime.toolhead_path_valid = False
        self._applying_follow_update += 1
        try:
            if current != target_layer or (
                minimum_layer is not None and current_minimum != minimum_layer
            ):
                # PreviewFollowerService is the authoritative Preview-write
                # boundary. Do not call CuraAdapter directly from orchestration.
                self._preview_follower_service.apply_layer_decision(
                    view, target_layer, minimum_layer
                )

            if self.current_printer_config().path_follow and decision.follow_path:
                path_detail = self._apply_path_progress(
                    view,
                    remote_target_layer,
                    virtual_sdcard,
                    motion_report or {},
                    gcode_move,
                )
                runtime.toolhead_path_valid = path_detail.startswith("path ")
        finally:
            self._applying_follow_update = max(0, self._applying_follow_update - 1)

        self._remember_plugin_preview_position(view)
        self._update_selected_layer_eta(view)
        self._update_toolhead_indicator(view)

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

    def _maybe_switch_to_preview(self) -> None:
        runtime = self._preview_follower_service.runtime
        if runtime.preview_switched_for_job or not self.current_printer_config().auto_preview:
            return
        try:
            self._controller.setActiveStage("PreviewStage")
            runtime.preview_switched_for_job = True
        except Exception:
            # Following remains usable if Cura refuses a stage switch.
            pass
