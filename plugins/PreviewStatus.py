from __future__ import annotations

from typing import Any, Dict, Optional
from .Core import OperationPhase
from .FollowController import FollowState


class PreviewStatusMixin:
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
        if has_toolpath and self._pref_bool(self.PREF_ENABLED) and self._last_remote_state in self.ACTIVE_STATES:
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
        pause_state = self._pause_at_layer_preview_state()
        for controls in (self._preview_overlay, self._action_panel_controls):
            if controls is None:
                continue
            try:
                controls.setProperty("followingPaused", self._following_paused)
                controls.setProperty("followingEnabled", self._pref_bool(self.PREF_ENABLED))
                controls.setProperty("configuredForFollowing", configured)
                controls.setProperty("activePrinterName", active_printer_name)
                controls.setProperty("hasToolpath", has_toolpath)
                controls.setProperty("statusText", compact_status)
                controls.setProperty("statusIconName", status_icon_name)
                controls.setProperty("selectedLayerEtaText", self._selected_layer_eta_text)
                controls.setProperty("pauseAtLayerActive", pause_state["active"])
                controls.setProperty("pauseAtLayerCandidate", pause_state["candidate"])
                controls.setProperty("pauseAtLayerCanToggle", pause_state["canToggle"])
                controls.setProperty("pauseAtLayerScheduled", pause_state["scheduled"])
                controls.setProperty("pauseAtLayerSummary", pause_state["summary"])
                controls.setProperty("pauseAtLayerItems", pause_state["items"])
                controls.setProperty("pauseAtLayerUnavailableText", pause_state["unavailable"])
            except Exception:
                pass

    def _pause_at_layer_preview_state(self) -> Dict[str, Any]:
        active = bool(self._last_remote_state in self.ACTIVE_STATES and self._remote_job_key is not None)
        candidate = 0
        selected_layer: Optional[int] = None
        max_layer: Optional[int] = None
        view = self._simulation_view() if active else None
        if view is not None:
            try:
                selected_layer = max(0, int(view.getCurrentLayer()))
                candidate = selected_layer + 1
            except Exception:
                selected_layer = None
            try:
                if hasattr(view, "getMaxLayers"):
                    max_layer = max(0, int(view.getMaxLayers()))
            except Exception:
                max_layer = None
        current = self._last_observed_remote_layer
        scheduled = bool(selected_layer is not None and selected_layer in self._scheduled_pause_layers)
        is_final_layer = bool(selected_layer is not None and max_layer is not None and selected_layer >= max_layer)
        can_toggle = bool(active and selected_layer is not None and current is not None and selected_layer >= current and not is_final_layer)
        unavailable = ""
        if active and selected_layer is not None and not scheduled and not can_toggle:
            if current is None:
                unavailable = "Waiting for current print layer"
            elif selected_layer < current:
                unavailable = f"Layer {selected_layer + 1} already printed"
            elif is_final_layer:
                unavailable = "Final layer ends the print"
            else:
                unavailable = "Select a future layer"
        items = []
        for layer in sorted(self._scheduled_pause_layers):
            remaining = self._estimate_layer_boundary_remaining(layer, end_of_layer=True)
            eta = f"in {self._format_preview_duration(remaining)}" if remaining is not None else "ETA unavailable"
            items.append({"layer": layer + 1, "eta": eta})
        layers = ", ".join(str(item["layer"]) for item in items)
        return {
            "active": active,
            "candidate": candidate,
            "canToggle": can_toggle,
            "scheduled": scheduled,
            "summary": f"End-of-layer PAUSE: {layers}" if layers else "",
            "items": items,
            "unavailable": unavailable,
        }
