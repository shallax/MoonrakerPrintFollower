from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from .MoonrakerProtocol import live_position_in_gcode_space


class PathFollowEngineMixin:
    """Map the active Moonraker G-code position onto Cura's path slider."""

    def _apply_path_progress(
        self,
        view,
        target_layer: int,
        virtual_sdcard: Dict[str, Any],
        motion_report: Optional[Dict[str, Any]] = None,
        gcode_move: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not hasattr(view, "setPath") or not hasattr(view, "getMaxPaths"):
            return "within-layer tracking unavailable in this Cura build"

        if self._path_progress_layer != target_layer:
            self._path_progress_layer = target_layer
            self._path_progress_fraction = None

        if (
            self._remote_index_filename != self._last_remote_filename
            or self._remote_index_job_key != self._remote_job_key
        ):
            try:
                view.setPath(0.0)
            except Exception:
                pass
            if (
                (self._file_reply is not None and self._file_reply.isRunning())
                or self._remote_index_build_filename == self._last_remote_filename
            ):
                return "indexing remote G-code"
            return "waiting for remote G-code index"

        if target_layer < 0 or target_layer >= len(self._remote_layer_ranges):
            return "no remote path index for this layer"

        try:
            file_position = int(virtual_sdcard.get("file_position"))
        except (TypeError, ValueError):
            return "waiting for file position"

        try:
            max_paths = int(view.getMaxPaths())
        except Exception:
            return "Cura path count unavailable"
        if max_paths <= 0:
            return "layer has no toolpaths"

        index = self._remote_index_data
        if index is None:
            return "remote path index unavailable"

        # Compact indexes hydrate only the active/next layer. Do not estimate
        # from raw byte position while hydration is still in flight: that can
        # race ahead and then visually rewind once exact motion offsets arrive.
        if getattr(index, "compact", False):
            if not (
                self._cached_gcode_filename == self._last_remote_filename
                and self._cached_gcode_path
                and self._cached_gcode_job_key == self._remote_job_key
                and os.path.isfile(self._cached_gcode_path)
            ):
                self._ensure_remote_gcode_cached(self._last_remote_filename or "")
            self._ensure_remote_layer_hydrated(target_layer)
            if target_layer not in getattr(index, "hydrated_layers", set()):
                self._path_progress_fraction = 0.0
                try:
                    if abs(float(view.getCurrentPath())) >= 0.5:
                        view.setPath(0.0)
                except Exception:
                    try:
                        view.setPath(0.0)
                    except Exception:
                        pass
                return "hydrating layer path index"

        live_position = live_position_in_gcode_space(
            motion_report or {}, gcode_move or {}
        )
        fraction, method = index.refined_fraction(
            target_layer,
            file_position,
            live_position,
            minimum_fraction=self._path_progress_fraction,
        )
        fraction = max(0.0, min(1.0, float(fraction)))
        if self._path_progress_fraction is not None:
            fraction = max(float(self._path_progress_fraction), fraction)
        self._path_progress_fraction = fraction
        target_path = fraction * max_paths

        if getattr(index, "compact", False) and target_layer + 1 < len(index.ranges):
            self._ensure_remote_layer_hydrated(target_layer + 1)

        try:
            if hasattr(view, "getMinimumPath") and hasattr(view, "setMinimumPath"):
                if int(view.getMinimumPath()) != 0:
                    view.setMinimumPath(0)
        except Exception:
            pass

        try:
            current_path = (
                float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else -1.0
            )
        except Exception:
            current_path = -1.0
        if abs(current_path - target_path) >= 0.5:
            view.setPath(target_path)

        return (
            f"path {round(target_path)}/{max_paths} "
            f"({fraction * 100:.1f}%, {method})"
        )

    def _layer_from_z(
        self, view, gcode_move: Dict[str, Any]
    ) -> Optional[int]:
        """Best-effort layer fallback when Moonraker has no current_layer.

        Only extrusion-advancing samples are considered so temporary Z-hop
        positions do not move Cura onto the wrong layer.
        """
        position = gcode_move.get("gcode_position")
        if not isinstance(position, (list, tuple)) or len(position) < 4:
            return None
        try:
            z = float(position[2])
            e = float(position[3])
        except (TypeError, ValueError):
            return None

        previous_e = self._last_extruder_position
        self._last_extruder_position = e
        if previous_e is None or e <= previous_e + 0.0001:
            return None

        try:
            max_layer = int(view.getMaxLayers())
        except Exception:
            return None
        if max_layer < 0:
            return None

        calculate_cache = getattr(view, "_calculateLayerHeightsCache", None)
        if callable(calculate_cache):
            try:
                calculate_cache()
            except Exception:
                pass
        get_height = getattr(view, "_getLayerHeight", None)
        if not callable(get_height):
            return None

        tolerance = max(0.001, self._pref_float(self.PREF_Z_TOLERANCE, 0.04))
        best: Optional[Tuple[float, int]] = None
        for layer in range(max_layer + 1):
            try:
                height = float(get_height(layer))
            except Exception:
                continue
            if height <= 0:
                continue
            delta = abs(height - z)
            if delta <= tolerance and (best is None or delta < best[0]):
                best = (delta, layer)
        return best[1] if best is not None else None
