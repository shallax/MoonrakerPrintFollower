from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional


class PreviewEtaMixin:
    @staticmethod
    def _format_preview_duration(seconds: float) -> str:
        total = max(0, int(round(float(seconds))))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _estimate_layer_boundary_remaining(
        self, target_layer: int, *, end_of_layer: bool
    ) -> Optional[float]:
        try:
            target_layer = max(0, int(target_layer))
        except (TypeError, ValueError):
            return None
        index = self._remote_index_data
        times = list(getattr(index, "layer_elapsed_times", []) or []) if index is not None else []
        current_layer = (
            self._last_observed_remote_layer
            if self._last_observed_remote_layer is not None
            else self._last_resolved_remote_layer
        )
        if current_layer is None or not times:
            return None
        current_layer = max(0, int(current_layer))

        def layer_start_elapsed(layer: int) -> Optional[float]:
            if layer <= 0:
                return 0.0
            boundary = layer - 1
            if 0 <= boundary < len(times) and times[boundary] is not None:
                try:
                    return float(times[boundary])
                except (TypeError, ValueError):
                    return None
            return None

        target_boundary = target_layer if end_of_layer else target_layer - 1
        if target_boundary < 0:
            target_elapsed = 0.0
        elif target_boundary < len(times) and times[target_boundary] is not None:
            try:
                target_elapsed = float(times[target_boundary])
            except (TypeError, ValueError):
                return None
        else:
            return None
        current_start = layer_start_elapsed(current_layer)
        if current_start is None:
            return None
        current_end = None
        if 0 <= current_layer < len(times) and times[current_layer] is not None:
            try:
                current_end = float(times[current_layer])
            except (TypeError, ValueError):
                current_end = None

        speed = max(0.05, float(self._last_speed_factor or 1.0))
        fractions: List[float] = []
        if self._path_progress_layer == current_layer and self._path_progress_fraction is not None:
            try:
                fractions.append(max(0.0, min(1.0, float(self._path_progress_fraction))))
            except (TypeError, ValueError):
                pass
        if (
            self._eta_anchor_layer == current_layer
            and self._eta_anchor_print_duration is not None
            and self._eta_current_print_duration is not None
            and current_end is not None
            and current_end > current_start
        ):
            actual_into_layer = max(
                0.0,
                float(self._eta_current_print_duration) - float(self._eta_anchor_print_duration),
            )
            planned_layer_duration = current_end - current_start
            fractions.append(
                max(0.0, min(1.0, actual_into_layer * speed / planned_layer_duration))
            )
        fraction = max(fractions) if fractions else 0.0
        planned_now = current_start
        if current_end is not None and current_end >= current_start:
            planned_now += (current_end - current_start) * fraction
        remaining_planned = max(0.0, target_elapsed - planned_now)
        return remaining_planned / speed

    def _update_selected_layer_eta(self, view=None) -> None:
        text = ""
        if self._last_remote_state in self.ACTIVE_STATES:
            if view is None:
                view = self._simulation_view()
            current_layer = (
                self._last_observed_remote_layer
                if self._last_observed_remote_layer is not None
                else self._last_resolved_remote_layer
            )
            if view is not None and current_layer is not None:
                try:
                    selected_layer = max(0, int(view.getCurrentLayer()))
                except Exception:
                    selected_layer = None
                if selected_layer is not None:
                    human_layer = selected_layer + 1
                    if selected_layer < current_layer:
                        text = f"Selected layer {human_layer} — already printed"
                    elif selected_layer == current_layer:
                        if self._following_paused:
                            text = f"Selected layer {human_layer} — current print layer"
                    else:
                        remaining = self._estimate_layer_boundary_remaining(
                            selected_layer, end_of_layer=False
                        )
                        if remaining is None:
                            text = f"Selected layer {human_layer} — ETA unavailable (no layer timing)"
                        else:
                            finish = datetime.now().astimezone() + timedelta(seconds=remaining)
                            clock = finish.strftime("%a %H:%M") if remaining >= 20 * 3600 else finish.strftime("%H:%M")
                            text = (
                                f"Selected layer {human_layer} — in {self._format_preview_duration(remaining)} "
                                f"· ~{clock}"
                            )
        if text == self._selected_layer_eta_text:
            return
        self._selected_layer_eta_text = text
        self._sync_preview_button_state()
