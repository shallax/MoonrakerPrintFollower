from __future__ import annotations

from typing import Any, Optional, Tuple

from .MoonrakerMonitorModel import MoonrakerMonitorModel as _BaseMoonrakerMonitorModel


class MoonrakerMonitorModel(_BaseMoonrakerMonitorModel):
    """Monitor model additions that depend on follower layer interpretation."""

    def updateMoonrakerStatus(self, status: Any) -> None:
        super().updateMoonrakerStatus(status)
        current_layer, total_layer = self._resolve_live_layer(status)
        if current_layer is not None and total_layer is not None:
            self._monitor_layer = f"{current_layer} / {total_layer}"
        elif current_layer is not None:
            self._monitor_layer = str(current_layer)
        elif total_layer is not None:
            self._monitor_layer = f"— / {total_layer}"
        else:
            self._monitor_layer = "—"
        self.monitorChanged.emit()

    def _resolve_live_layer(self, status: Any) -> Tuple[Optional[int], Optional[int]]:
        if not isinstance(status, dict):
            return None, None

        print_stats = status.get("print_stats") or {}
        gcode_move = status.get("gcode_move") or {}
        info = print_stats.get("info") or {}
        if not isinstance(info, dict):
            info = {}

        filename = str(print_stats.get("filename") or "")
        raw_remote_layer = info.get("current_layer")
        total_layer = self._as_positive_int(info.get("total_layer"))
        target_layer: Optional[int] = None

        if raw_remote_layer is not None:
            try:
                raw = int(raw_remote_layer)
                layer_map = getattr(self._follower, "_remote_current_layer_map", {})
                indexed_filename = getattr(self._follower, "_remote_index_filename", None)
                if indexed_filename == filename and raw in layer_map:
                    target_layer = int(layer_map[raw])
                else:
                    target_layer = raw
                    config = self._follower.current_printer_config()
                    if bool(config.moonraker_layer_is_one_based):
                        target_layer -= 1
            except (TypeError, ValueError, AttributeError):
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
    def _as_positive_int(value: Any) -> Optional[int]:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None
