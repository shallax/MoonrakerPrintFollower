from __future__ import annotations

from UM.Logger import Logger
from .FollowController import FollowMode
from .NativeNozzleFallback import keep_native_nozzle_visible


class PreviewFollowerRuntimeMixin:
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
        return

    def _update_toolhead_indicator(self, view=None) -> None:
        """Keep Cura's native SimulationView nozzle visible while following."""
        config = self._config_store.get()
        if (
            not config.show_toolhead_indicator
            or not config.enabled
            or self._following_paused
            or self._last_remote_state not in self.ACTIVE_STATES
            or not self._toolhead_path_valid
            or not config.path_follow
            or config.follow_mode != FollowMode.EXACT.value
            or self._slicing_in_progress
            or not self._is_preview_stage_active()
        ):
            return
        if view is None:
            view = self._simulation_view()
        if view is None:
            return
        try:
            if not keep_native_nozzle_visible(view):
                Logger.log("d", "Moonraker Print Follower could not enable Cura's native nozzle fallback")
        except Exception as error:
            Logger.log("w", "Moonraker Print Follower could not update printhead indicator: %s", error)

    def _watch_for_manual_preview_change(self) -> None:
        self._check_for_manual_preview_change()
        self._update_selected_layer_eta()

    def _on_preview_position_changed(self, *_args) -> None:
        self._check_for_manual_preview_change()
        self._update_selected_layer_eta()

    def _update_manual_view_watch_mode(self) -> None:
        should_run = self._pref_bool(self.PREF_ENABLED) and self._valid_configured_url()
        if should_run:
            self._manual_view_watch_timer.start()
        else:
            self._manual_view_watch_timer.stop()

    def _cura_has_toolpath(self) -> bool:
        view = self._simulation_view()
        if view is None:
            return False
        get_activity = getattr(view, "getActivity", None)
        if callable(get_activity):
            try:
                return bool(get_activity())
            except Exception:
                pass
        get_layer_data = getattr(view, "getLayerData", None)
        if callable(get_layer_data):
            try:
                return get_layer_data() is not None
            except Exception:
                pass
        return False
