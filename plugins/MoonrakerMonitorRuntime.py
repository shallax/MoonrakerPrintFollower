from __future__ import annotations

from typing import Any, Optional, Tuple

from .MoonrakerMonitorModel import MoonrakerMonitorModel as _BaseMoonrakerMonitorModel
from .MoonrakerSession import RequestCategory


class MoonrakerMonitorModel(_BaseMoonrakerMonitorModel):
    """Monitor model additions that depend on follower layer interpretation."""

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        self._resolved_current_layer: Optional[int] = None
        self._resolved_total_layer: Optional[int] = None
        super().__init__(output_controller, number_of_extruders, follower)
        self._apply_adaptive_monitor_intervals()

    def _apply_adaptive_monitor_intervals(self) -> None:
        """Apply the active session's category-aware polling policy to Monitor timers."""
        try:
            session = self._follower.session
            policy = session.poll_policy
            printer_state = session.snapshot.printer_state
        except Exception:
            return

        for timer, category, configured_ms in (
            (self._aux_timer, RequestCategory.AUXILIARY, self.AUX_POLL_MS),
            (self._power_timer, RequestCategory.POWER, self.POWER_POLL_MS),
            (self._system_timer, RequestCategory.SYSTEM, self.SYSTEM_POLL_MS),
            (self._discovery_timer, RequestCategory.DISCOVERY, self.DISCOVERY_POLL_MS),
        ):
            try:
                timer.setInterval(
                    policy.interval_ms(category, configured_ms, printer_state)
                )
            except Exception:
                pass

    def _after_core_status(self, status: Any) -> None:
        super()._after_core_status(status)
        self._apply_adaptive_monitor_intervals()
        current_layer, total_layer = self._resolve_live_layer(status)
        self._resolved_current_layer = current_layer
        self._resolved_total_layer = total_layer
        if current_layer is not None and total_layer is not None:
            self._monitor_layer = f"{current_layer} / {total_layer}"
        elif current_layer is not None:
            self._monitor_layer = str(current_layer)
        elif total_layer is not None:
            self._monitor_layer = f"— / {total_layer}"
        else:
            self._monitor_layer = "—"

    def _resolve_live_layer(self, status: Any) -> Tuple[Optional[int], Optional[int]]:
        if not isinstance(status, dict):
            return None, None

        print_stats = self._status_object(status, "print_stats")
        gcode_move = self._status_object(status, "gcode_move")
        virtual_sdcard = self._status_object(status, "virtual_sdcard")
        info = print_stats.get("info") or {}
        if not isinstance(info, dict):
            info = {}

        filename = str(print_stats.get("filename") or "")
        raw_remote_layer = info.get("current_layer")
        total_layer = self._as_positive_int(info.get("total_layer"))
        target_layer: Optional[int] = None
        index = self._follower.gcode_index

        if raw_remote_layer is not None:
            try:
                raw = int(raw_remote_layer)
                if index.filename == filename and raw in index.current_layer_map:
                    target_layer = int(index.current_layer_map[raw])
                else:
                    target_layer = raw
                    config = self._follower.current_printer_config()
                    if bool(config.moonraker_layer_is_one_based):
                        target_layer -= 1
            except (TypeError, ValueError, AttributeError):
                target_layer = None

        if target_layer is None:
            try:
                ranges = list(index.ranges or [])
                if index.filename == filename and ranges:
                    if total_layer is None:
                        total_layer = len(ranges)
                    try:
                        file_position = int(virtual_sdcard.get("file_position"))
                    except (TypeError, ValueError):
                        file_position = None
                    if file_position is not None:
                        target_layer = self._layer_from_file_position(ranges, file_position)
            except Exception:
                target_layer = None

        if target_layer is None:
            try:
                config = self._follower.current_printer_config()
                if bool(config.z_fallback):
                    view = self._follower._simulation_view()
                    if view is not None:
                        target_layer = self._follower._layer_from_z(view, gcode_move)
            except Exception:
                target_layer = None

        if target_layer is None:
            return None, total_layer

        target_layer = max(0, int(target_layer))
        try:
            view = self._follower._simulation_view()
            if view is not None and hasattr(view, "getMaxLayers"):
                max_layer = max(0, int(view.getMaxLayers()))
                target_layer = min(target_layer, max_layer)
                if total_layer is None:
                    total_layer = max_layer + 1
        except Exception:
            pass

        return target_layer + 1, total_layer

    @staticmethod
    def _layer_from_file_position(ranges: Any, file_position: int) -> Optional[int]:
        if not ranges:
            return None
        try:
            position = int(file_position)
        except (TypeError, ValueError):
            return None

        low = 0
        high = len(ranges) - 1
        while low <= high:
            middle = (low + high) // 2
            try:
                start, end = ranges[middle]
                start = int(start)
                end = int(end)
            except (TypeError, ValueError, IndexError):
                return None

            if position < start:
                high = middle - 1
            elif position >= end:
                low = middle + 1
            else:
                return middle

        if low > 0:
            return min(low - 1, len(ranges) - 1)
        return None

    @staticmethod
    def _as_positive_int(value: Any) -> Optional[int]:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None
