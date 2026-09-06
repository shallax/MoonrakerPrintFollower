from __future__ import annotations

import ast
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QVariant, pyqtProperty, pyqtSignal, pyqtSlot

from .MoonrakerMonitorControls import MoonrakerMonitorModel as _BaseMoonrakerMonitorModel


class MoonrakerMonitorModel(_BaseMoonrakerMonitorModel):
    """Typed macro arguments, truthful presets, and PWM output controls."""

    typedControlsChanged = pyqtSignal()

    _PARAM_RE = re.compile(
        r"params(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([^'\"]+)['\"]\s*\])"
    )
    _DEFAULT_RE = re.compile(r"\|\s*default\s*\(\s*([^,\)]+)", re.IGNORECASE)

    BED_MESH_VISIBLE_PREF = "moonraker_print_follower/bed_mesh_visible"
    BED_MESH_EXAGGERATION = 20.0

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        self._macro_parameter_definitions: Dict[str, List[Dict[str, Any]]] = {}
        self._pwm_output_items: List[Dict[str, Any]] = []
        self._mcu_items: List[Dict[str, Any]] = []
        self._restoring_webcam = False
        self._bed_mesh_snapshot: Dict[str, Any] = {}
        self._bed_mesh_fingerprint: Optional[Tuple[Any, ...]] = None
        self._bound_preview_control_ids: set[int] = set()
        super().__init__(output_controller, number_of_extruders, follower)

        preferences = getattr(follower, "_preferences", None)
        if preferences is not None:
            try:
                preferences.addPreference(self.BED_MESH_VISIBLE_PREF, True)
            except Exception:
                pass
        if not hasattr(follower, "_bed_mesh_preview_visible"):
            setattr(follower, "_bed_mesh_preview_visible", self._read_bed_mesh_visibility_preference())
        if not hasattr(follower, "_bed_mesh_snapshot"):
            setattr(follower, "_bed_mesh_snapshot", {})
        if not hasattr(follower, "_bed_mesh_fingerprint"):
            setattr(follower, "_bed_mesh_fingerprint", None)
        if not hasattr(follower, "_bed_mesh_scene_node"):
            setattr(follower, "_bed_mesh_scene_node", None)

        controller = getattr(follower, "_controller", None)
        stage_changed = getattr(controller, "activeStageChanged", None)
        if stage_changed is not None:
            try:
                stage_changed.connect(self._sync_bed_mesh_scene_visibility)
            except Exception:
                pass
        application = getattr(follower, "_application", None)
        machine_changed = getattr(application, "globalContainerStackChanged", None)
        if machine_changed is not None:
            try:
                machine_changed.connect(self._on_bed_mesh_machine_changed)
            except Exception:
                pass
        file_completed = getattr(application, "fileCompleted", None) if application is not None else None
        if file_completed is not None:
            try:
                file_completed.connect(self._on_cura_bed_mesh_scene_changed)
            except Exception:
                pass
        self._bind_preview_bed_mesh_controls()

    @staticmethod
    def _want_aux_object(name: str) -> bool:
        lower = str(name or "").lower()
        if lower.startswith("output_pin "):
            return True
        return _BaseMoonrakerMonitorModel._want_aux_object(name)

    def _rebuild_peripherals(self) -> None:
        super()._rebuild_peripherals()
        self._rebuild_macro_parameter_definitions()
        self._pwm_output_items = self._build_pwm_output_items()
        self._mcu_items = self._build_mcu_items()
        self._update_bed_mesh_snapshot(self._aux_status.get("bed_mesh"))
        self._bind_preview_bed_mesh_controls()
        self._sync_preview_bed_mesh_controls()
        self.typedControlsChanged.emit()

    def _on_temperature_presets(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        super()._on_temperature_presets(payload, error)
        self.typedControlsChanged.emit()


    # ------------------------------------------------------------------
    # Bed mesh data, Monitor heatmap and Preview scene overlay
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_bed_mesh_matrix(raw: Any) -> Optional[List[List[float]]]:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return None
        matrix: List[List[float]] = []
        columns: Optional[int] = None
        for raw_row in raw:
            if not isinstance(raw_row, (list, tuple)) or len(raw_row) < 2:
                return None
            if columns is None:
                columns = len(raw_row)
            elif len(raw_row) != columns:
                return None
            row: List[float] = []
            for raw_value in raw_row:
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(value):
                    return None
                row.append(value)
            matrix.append(row)
        return matrix

    @classmethod
    def _parse_bed_mesh_status(cls, status: Any) -> Dict[str, Any]:
        if not isinstance(status, dict):
            return {}

        source = "mesh_matrix"
        matrix = cls._normalise_bed_mesh_matrix(status.get("mesh_matrix"))
        if matrix is None:
            source = "probed_matrix"
            matrix = cls._normalise_bed_mesh_matrix(status.get("probed_matrix"))
        if matrix is None:
            return {}

        mesh_min = status.get("mesh_min")
        mesh_max = status.get("mesh_max")
        if not isinstance(mesh_min, (list, tuple)) or len(mesh_min) < 2:
            return {}
        if not isinstance(mesh_max, (list, tuple)) or len(mesh_max) < 2:
            return {}
        try:
            x_min = float(mesh_min[0])
            y_min = float(mesh_min[1])
            x_max = float(mesh_max[0])
            y_max = float(mesh_max[1])
        except (TypeError, ValueError):
            return {}
        if not all(math.isfinite(value) for value in (x_min, y_min, x_max, y_max)):
            return {}
        if x_max <= x_min or y_max <= y_min:
            return {}

        rows = len(matrix)
        columns = len(matrix[0])
        values = [value for row in matrix for value in row]
        minimum = min(values)
        maximum = max(values)
        profile = str(status.get("profile_name") or "").strip()
        return {
            "profile": profile,
            "source": source,
            "rows": rows,
            "columns": columns,
            "values": values,
            "xMin": x_min,
            "xMax": x_max,
            "yMin": y_min,
            "yMax": y_max,
            "minimum": minimum,
            "maximum": maximum,
            "range": maximum - minimum,
        }

    @staticmethod
    def _bed_mesh_snapshot_fingerprint(snapshot: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
        if not snapshot:
            return None
        return (
            snapshot.get("profile"),
            snapshot.get("source"),
            int(snapshot.get("rows") or 0),
            int(snapshot.get("columns") or 0),
            round(float(snapshot.get("xMin") or 0.0), 5),
            round(float(snapshot.get("xMax") or 0.0), 5),
            round(float(snapshot.get("yMin") or 0.0), 5),
            round(float(snapshot.get("yMax") or 0.0), 5),
            tuple(round(float(value), 6) for value in (snapshot.get("values") or [])),
        )

    def _read_bed_mesh_visibility_preference(self) -> bool:
        preferences = getattr(self._follower, "_preferences", None)
        if preferences is None:
            return True
        try:
            value = preferences.getValue(self.BED_MESH_VISIBLE_PREF)
        except Exception:
            return True
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() not in {"0", "false", "no", "off"}

    def _preview_stage_active(self) -> bool:
        controller = getattr(self._follower, "_controller", None)
        if controller is None:
            return False
        try:
            stage = controller.getActiveStage()
            if stage is None:
                return False
            getter = getattr(stage, "getId", None)
            stage_id = getter() if callable(getter) else getattr(stage, "stageId", None)
            return stage_id == "PreviewStage"
        except Exception:
            return False

    def _ensure_bed_mesh_scene_node(self):
        follower = self._follower
        controller = getattr(follower, "_controller", None)
        if controller is None:
            return None
        try:
            scene = controller.getScene()
            root = scene.getRoot()
        except Exception:
            return None

        node = getattr(follower, "_bed_mesh_scene_node", None)
        if node is None:
            try:
                from .BedMeshSceneNode import BedMeshSceneNode
                node = BedMeshSceneNode()
                setattr(follower, "_bed_mesh_scene_node", node)
            except Exception:
                return None

        try:
            current_parent = node.getParent()
        except Exception:
            current_parent = None
        if current_parent is root:
            return node

        # The follower intentionally treats arbitrary root children changes as a
        # Cura scene lifecycle reset.  Adding our own non-sliceable visual node
        # must not look like the user replaced the model, so momentarily detach
        # only that one follower callback while parenting the overlay.
        disconnected = False
        handler = getattr(follower, "_on_scene_children_changed", None)
        tracked_root = getattr(follower, "_scene_root", None)
        if handler is not None and tracked_root is root:
            try:
                root.childrenChanged.disconnect(handler)
                disconnected = True
            except Exception:
                disconnected = False
        try:
            node.setParent(root)
        except Exception:
            return None
        finally:
            if disconnected:
                try:
                    root.childrenChanged.connect(handler)
                except Exception:
                    pass
        return node

    def _rebuild_bed_mesh_scene(self) -> None:
        snapshot = dict(getattr(self._follower, "_bed_mesh_snapshot", {}) or {})
        node = self._ensure_bed_mesh_scene_node() if snapshot else getattr(self._follower, "_bed_mesh_scene_node", None)
        if node is None:
            return
        if not snapshot:
            try:
                node.clear()
            except Exception:
                pass
            return

        application = getattr(self._follower, "_application", None)
        try:
            stack = application.getGlobalContainerStack() if application is not None else None
            if stack is None:
                return
            width = float(stack.getProperty("machine_width", "value"))
            depth = float(stack.getProperty("machine_depth", "value"))
            center_is_zero = bool(stack.getProperty("machine_center_is_zero", "value"))
            node.updateMesh(
                snapshot,
                width,
                depth,
                center_is_zero,
                self.BED_MESH_EXAGGERATION,
            )
        except Exception:
            return
        self._sync_bed_mesh_scene_visibility()

    def _update_bed_mesh_snapshot(self, status: Any) -> None:
        snapshot = self._parse_bed_mesh_status(status)
        fingerprint = self._bed_mesh_snapshot_fingerprint(snapshot)
        self._bed_mesh_snapshot = snapshot
        if snapshot:
            minimum = float(snapshot.get("minimum") or 0.0)
            maximum = float(snapshot.get("maximum") or 0.0)
            range_value = float(snapshot.get("range") or 0.0)
            setattr(self._follower, "_bed_mesh_range_text", f"{range_value:.3f} mm range")
            setattr(self._follower, "_bed_mesh_minimum_text", f"{minimum:+.3f} mm")
            setattr(self._follower, "_bed_mesh_maximum_text", f"{maximum:+.3f} mm")
        else:
            setattr(self._follower, "_bed_mesh_range_text", "")
            setattr(self._follower, "_bed_mesh_minimum_text", "")
            setattr(self._follower, "_bed_mesh_maximum_text", "")
        follower_fingerprint = getattr(self._follower, "_bed_mesh_fingerprint", None)
        if fingerprint != follower_fingerprint:
            setattr(self._follower, "_bed_mesh_snapshot", dict(snapshot))
            setattr(self._follower, "_bed_mesh_fingerprint", fingerprint)
            self._rebuild_bed_mesh_scene()
        else:
            self._sync_bed_mesh_scene_visibility()

    def _sync_bed_mesh_scene_visibility(self, *_args: Any) -> None:
        snapshot = dict(getattr(self._follower, "_bed_mesh_snapshot", {}) or {})
        node = self._ensure_bed_mesh_scene_node() if snapshot else getattr(self._follower, "_bed_mesh_scene_node", None)
        if node is None:
            self._sync_preview_bed_mesh_controls()
            return
        visible = bool(
            getattr(self._follower, "_bed_mesh_preview_visible", True)
            and snapshot
            and self._preview_stage_active()
        )
        try:
            node.setVisible(visible)
            controller = getattr(self._follower, "_controller", None)
            if controller is not None:
                scene = controller.getScene()
                scene.sceneChanged.emit(node)
        except Exception:
            pass
        self._sync_preview_bed_mesh_controls()

    def _on_cura_bed_mesh_scene_changed(self, *_args: Any) -> None:
        # Cura replaces/removes scene children while loading G-code or models.
        # Reattach the non-sliceable overlay after that lifecycle has settled.
        if getattr(self._follower, "_bed_mesh_snapshot", {}):
            try:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(100, self._rebuild_bed_mesh_scene)
            except Exception:
                self._rebuild_bed_mesh_scene()

    def _on_bed_mesh_machine_changed(self, *_args: Any) -> None:
        self._bed_mesh_snapshot = {}
        self._bed_mesh_fingerprint = None
        setattr(self._follower, "_bed_mesh_snapshot", {})
        setattr(self._follower, "_bed_mesh_fingerprint", None)
        node = getattr(self._follower, "_bed_mesh_scene_node", None)
        if node is not None:
            try:
                node.clear()
            except Exception:
                pass
        self._sync_preview_bed_mesh_controls()
        self.typedControlsChanged.emit()

    def _bind_preview_bed_mesh_controls(self) -> None:
        for attribute in ("_preview_overlay", "_action_panel_controls"):
            controls = getattr(self._follower, attribute, None)
            if controls is None:
                continue
            control_id = id(controls)
            if control_id in self._bound_preview_control_ids:
                continue
            signal = getattr(controls, "bedMeshVisibilityRequested", None)
            if signal is None:
                continue
            try:
                signal.connect(self.setBedMeshPreviewVisible)
                self._bound_preview_control_ids.add(control_id)
            except Exception:
                pass
        self._sync_preview_bed_mesh_controls()

    def _sync_preview_bed_mesh_controls(self) -> None:
        available = bool(self._bed_mesh_snapshot)
        visible = bool(getattr(self._follower, "_bed_mesh_preview_visible", True))
        range_text = self.bedMeshRangeText
        for attribute in ("_preview_overlay", "_action_panel_controls"):
            controls = getattr(self._follower, attribute, None)
            if controls is None:
                continue
            try:
                controls.setProperty("bedMeshAvailable", available)
                controls.setProperty("bedMeshVisible", visible)
                controls.setProperty("bedMeshRangeText", range_text)
                controls.setProperty("bedMeshMinimumText", str(getattr(self._follower, "_bed_mesh_minimum_text", "")))
                controls.setProperty("bedMeshMaximumText", str(getattr(self._follower, "_bed_mesh_maximum_text", "")))
            except Exception:
                pass

    @pyqtProperty(bool, notify=typedControlsChanged)
    def bedMeshAvailable(self) -> bool:
        return bool(self._bed_mesh_snapshot)

    @pyqtProperty(str, notify=typedControlsChanged)
    def bedMeshProfile(self) -> str:
        if not self._bed_mesh_snapshot:
            return ""
        return str(self._bed_mesh_snapshot.get("profile") or "Current mesh")

    @pyqtProperty(int, notify=typedControlsChanged)
    def bedMeshRows(self) -> int:
        return int(self._bed_mesh_snapshot.get("rows") or 0)

    @pyqtProperty(int, notify=typedControlsChanged)
    def bedMeshColumns(self) -> int:
        return int(self._bed_mesh_snapshot.get("columns") or 0)

    @pyqtProperty(QVariant, notify=typedControlsChanged)
    def bedMeshValues(self) -> QVariant:
        return QVariant(list(self._bed_mesh_snapshot.get("values") or []))

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshMinimum(self) -> float:
        return float(self._bed_mesh_snapshot.get("minimum") or 0.0)

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshMaximum(self) -> float:
        return float(self._bed_mesh_snapshot.get("maximum") or 0.0)

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshRange(self) -> float:
        return float(self._bed_mesh_snapshot.get("range") or 0.0)

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshXMin(self) -> float:
        return float(self._bed_mesh_snapshot.get("xMin") or 0.0)

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshXMax(self) -> float:
        return float(self._bed_mesh_snapshot.get("xMax") or 0.0)

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshYMin(self) -> float:
        return float(self._bed_mesh_snapshot.get("yMin") or 0.0)

    @pyqtProperty(float, notify=typedControlsChanged)
    def bedMeshYMax(self) -> float:
        return float(self._bed_mesh_snapshot.get("yMax") or 0.0)

    @pyqtProperty(str, notify=typedControlsChanged)
    def bedMeshRangeText(self) -> str:
        if not self._bed_mesh_snapshot:
            return ""
        return f"{self.bedMeshRange:.3f} mm range"

    @pyqtProperty(bool, notify=typedControlsChanged)
    def bedMeshPreviewVisible(self) -> bool:
        return bool(getattr(self._follower, "_bed_mesh_preview_visible", True))

    @pyqtSlot(bool)
    def setBedMeshPreviewVisible(self, visible: bool) -> None:
        setter = getattr(self._follower, "setBedMeshPreviewVisible", None)
        if callable(setter):
            setter(bool(visible))
        else:
            setattr(self._follower, "_bed_mesh_preview_visible", bool(visible))
        self._sync_bed_mesh_scene_visibility()
        self._sync_preview_bed_mesh_controls()
        self.typedControlsChanged.emit()

    # ------------------------------------------------------------------
    # PWM output_pin discovery and control
    # ------------------------------------------------------------------

    @staticmethod
    def _config_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _find_config_section(raw_config: Dict[str, Any], section_name: str) -> Optional[Dict[str, Any]]:
        wanted = str(section_name or "").casefold()
        for key, value in raw_config.items():
            if str(key).casefold() == wanted and isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _friendly_pwm_name(object_name: str) -> str:
        suffix = str(object_name or "").split(" ", 1)[1] if " " in str(object_name or "") else str(object_name or "")
        suffix = suffix.replace("_", " ").strip()
        return suffix[:1].upper() + suffix[1:] if suffix else str(object_name or "")

    def _build_pwm_output_items(self) -> List[Dict[str, Any]]:
        configfile = self._aux_status.get("configfile") or {}
        raw_config = configfile.get("config") if isinstance(configfile, dict) else None
        if not isinstance(raw_config, dict):
            return []

        items: List[Dict[str, Any]] = []
        for object_name in sorted(self._aux_status.keys(), key=str.casefold):
            if not str(object_name).lower().startswith("output_pin "):
                continue
            status = self._aux_status.get(object_name)
            if not isinstance(status, dict) or status.get("value") is None:
                continue
            section = self._find_config_section(raw_config, object_name)
            if not isinstance(section, dict) or not self._config_truthy(section.get("pwm")):
                continue
            try:
                scale = float(section.get("scale") or 1.0)
            except (TypeError, ValueError):
                scale = 1.0
            if scale <= 0:
                scale = 1.0
            try:
                current = float(status.get("value") or 0.0)
            except (TypeError, ValueError):
                current = 0.0
            current = max(0.0, min(scale, current))
            pin_name = str(object_name).split(" ", 1)[1].strip()
            items.append({
                "object": str(object_name),
                "pin": pin_name,
                "name": self._friendly_pwm_name(object_name),
                "scale": scale,
                "percent": int(round(current * 100.0 / scale)),
            })
        return items

    @pyqtProperty(QVariant, notify=typedControlsChanged)
    def pwmOutputItems(self) -> QVariant:
        return QVariant(list(self._pwm_output_items))

    @pyqtSlot(str, int)
    def setPwmOutput(self, object_name: str, percent: int) -> None:
        object_name = str(object_name or "")
        item = next((entry for entry in self._pwm_output_items if entry.get("object") == object_name), None)
        if item is None:
            return
        try:
            percent_value = max(0, min(100, int(percent)))
            scale = float(item.get("scale") or 1.0)
        except (TypeError, ValueError):
            return
        pin_name = str(item.get("pin") or "").strip()
        if not pin_name:
            return
        target = scale * percent_value / 100.0
        self._send_quick_gcode(
            "pwm-output-" + object_name,
            f"SET_PIN PIN={pin_name} VALUE={target:g}",
        )

    # ------------------------------------------------------------------
    # Remembered webcam and per-MCU runtime statistics
    # ------------------------------------------------------------------

    @staticmethod
    def _webcam_identity(webcam: Dict[str, Any], index: int = 0) -> str:
        return str(
            webcam.get("uid")
            or webcam.get("id")
            or webcam.get("name")
            or f"camera-{index}"
        ).strip()

    def _apply_webcam(self, index: int) -> None:
        super()._apply_webcam(index)
        if self._restoring_webcam or index < 0 or index >= len(self._webcams):
            return
        identity = self._webcam_identity(self._webcams[index], index)
        if not identity:
            return
        try:
            store = getattr(self._follower, "_config_store", None)
            if store is not None:
                store.update(camera_selected=identity)
        except Exception:
            pass

    def _on_webcams_finished(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        remembered = str(getattr(self._follower.current_printer_config(), "camera_selected", "") or "").strip()
        self._restoring_webcam = True
        try:
            super()._on_webcams_finished(payload, error)
        finally:
            self._restoring_webcam = False
        if not remembered or not self._webcams:
            return
        index = next((
            i for i, webcam in enumerate(self._webcams)
            if self._webcam_identity(webcam, i) == remembered
        ), -1)
        if index >= 0 and index != self._active_webcam_index:
            self._restoring_webcam = True
            try:
                super()._apply_webcam(index)
            finally:
                self._restoring_webcam = False

    @staticmethod
    def _parse_mcu_last_stats(value: Any) -> Dict[str, float]:
        if isinstance(value, dict):
            source = value
        else:
            source: Dict[str, Any] = {}
            for token in str(value or "").replace(",", " ").split():
                if "=" not in token:
                    continue
                key, raw = token.split("=", 1)
                source[key.strip()] = raw.strip()
        parsed: Dict[str, float] = {}
        for key, raw in source.items():
            try:
                parsed[str(key)] = float(raw)
            except (TypeError, ValueError):
                continue
        return parsed

    @staticmethod
    def _format_mcu_bytes(value: Optional[float]) -> str:
        if value is None or value < 0:
            return "—"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f} MB"
        if value >= 1000:
            return f"{value / 1000:.1f} kB"
        return f"{value:.0f} B"

    def _build_mcu_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for object_name in sorted(self._aux_status.keys(), key=str.casefold):
            lower = str(object_name).lower()
            if lower != "mcu" and not lower.startswith("mcu "):
                continue
            value = self._aux_status.get(object_name)
            if not isinstance(value, dict):
                continue
            friendly = "Main MCU" if lower == "mcu" else self._friendly_object_name(object_name)
            stats = self._parse_mcu_last_stats(value.get("last_stats"))
            awake = stats.get("mcu_awake")
            load = max(0.0, awake * 100.0) if awake is not None else None
            task_avg = stats.get("mcu_task_avg")
            task_stddev = stats.get("mcu_task_stddev")
            frequency = stats.get("freq")
            if frequency is None:
                constants = value.get("mcu_constants")
                if isinstance(constants, dict):
                    try:
                        frequency = float(constants.get("CLOCK_FREQ"))
                    except (TypeError, ValueError):
                        frequency = None
            memory = "—"
            for key in ("memory_free", "memavail", "free_memory", "memory"):
                if value.get(key) is None:
                    continue
                try:
                    memory = self._format_mcu_bytes(float(value.get(key)))
                    break
                except (TypeError, ValueError):
                    continue
            tasks: List[str] = []
            if task_avg is not None:
                tasks.append(f"avg {task_avg * 1_000_000:.1f} µs")
            if task_stddev is not None:
                tasks.append(f"σ {task_stddev * 1_000_000:.1f} µs")
            transport: List[str] = []
            if stats.get("bytes_write") is not None:
                transport.append("TX " + self._format_mcu_bytes(stats.get("bytes_write")))
            if stats.get("bytes_read") is not None:
                transport.append("RX " + self._format_mcu_bytes(stats.get("bytes_read")))
            if stats.get("bytes_retransmit") is not None:
                transport.append("retry " + self._format_mcu_bytes(stats.get("bytes_retransmit")))
            items.append({
                "name": friendly,
                "version": str(value.get("mcu_version") or "—"),
                "load": f"{load:.1f}%" if load is not None else "—",
                "task": " · ".join(tasks) if tasks else "—",
                "frequency": f"{frequency / 1_000_000:.3f} MHz" if frequency else "—",
                "memory": memory,
                "transport": " · ".join(transport) if transport else "—",
            })
        return items

    @pyqtProperty(QVariant, notify=typedControlsChanged)
    def mcuItems(self) -> QVariant:
        return QVariant(list(self._mcu_items))

    # ------------------------------------------------------------------
    # Macro argument discovery
    # ------------------------------------------------------------------

    def _rebuild_macro_parameter_definitions(self) -> None:
        configfile = self._aux_status.get("configfile") or {}
        raw_config = configfile.get("config") if isinstance(configfile, dict) else None
        if not isinstance(raw_config, dict):
            self._macro_parameter_definitions = {}
            return

        definitions: Dict[str, List[Dict[str, Any]]] = {}
        for macro in self._macros:
            section = self._find_macro_section(raw_config, macro)
            if not isinstance(section, dict):
                definitions[macro] = []
                continue
            gcode = str(section.get("gcode") or "")
            definitions[macro] = self._infer_macro_parameters(gcode)
        self._macro_parameter_definitions = definitions

    @classmethod
    def _find_macro_section(cls, raw_config: Dict[str, Any], macro: str) -> Optional[Dict[str, Any]]:
        return cls._find_config_section(raw_config, f"gcode_macro {macro}")

    @classmethod
    def _infer_macro_parameters(cls, gcode: str) -> List[Dict[str, Any]]:
        found: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for line in str(gcode or "").splitlines():
            for match in cls._PARAM_RE.finditer(line):
                raw_name = match.group(1) or match.group(2) or ""
                name = raw_name.strip().upper()
                if not name:
                    continue

                tail = line[match.end():]
                default_match = cls._DEFAULT_RE.search(tail)
                default_expr = default_match.group(1).strip() if default_match else None
                literal, has_literal = cls._parse_literal_default(default_expr)

                lower_tail = tail.lower()
                if re.search(r"\|\s*int\b", lower_tail):
                    kind = "int"
                elif re.search(r"\|\s*float\b", lower_tail):
                    kind = "float"
                elif isinstance(literal, bool):
                    kind = "bool"
                elif isinstance(literal, int) and not isinstance(literal, bool):
                    kind = "int"
                elif isinstance(literal, float):
                    kind = "float"
                elif (
                    isinstance(literal, str)
                    and literal.strip().casefold() in {"true", "false"}
                    and re.search(r"\|\s*lower\b", lower_tail)
                ):
                    kind = "bool"
                    literal = literal.strip().casefold() == "true"
                    has_literal = True
                else:
                    kind = "string"

                if has_literal:
                    if isinstance(literal, bool):
                        default_text = "True" if literal else "False"
                    else:
                        default_text = str(literal)
                else:
                    default_text = ""

                item = {
                    "name": name,
                    "type": kind,
                    "default": default_text,
                    "required": default_expr is None,
                    "hasDefault": default_expr is not None,
                }

                existing = found.get(name)
                if existing is None:
                    found[name] = item
                    order.append(name)
                else:
                    # Prefer an occurrence that carries an explicit type/default.
                    if existing["type"] == "string" and kind != "string":
                        existing["type"] = kind
                    if not existing["hasDefault"] and item["hasDefault"]:
                        existing["default"] = item["default"]
                        existing["hasDefault"] = True
                        existing["required"] = False

        return [found[name] for name in order]

    @staticmethod
    def _parse_literal_default(expression: Optional[str]) -> Tuple[Any, bool]:
        if expression is None:
            return None, False
        text = expression.strip()
        lower = text.casefold()
        if lower == "true":
            return True, True
        if lower == "false":
            return False, True
        if lower in {"none", "null"}:
            return "", True
        try:
            return ast.literal_eval(text), True
        except (ValueError, SyntaxError):
            pass
        if re.fullmatch(r"[-+]?\d+", text):
            try:
                return int(text), True
            except ValueError:
                pass
        if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", text):
            try:
                return float(text), True
            except ValueError:
                pass
        return None, False

    @pyqtSlot(str, result=QVariant)
    def macroParameterDefinitions(self, macro: str) -> QVariant:
        name = str(macro or "").strip()
        return QVariant(list(self._macro_parameter_definitions.get(name, [])))

    # ------------------------------------------------------------------
    # Temperature preset presentation
    # ------------------------------------------------------------------

    def _preset_is_active(self, item: Dict[str, Any]) -> bool:
        preset = item.get("preset") or {}
        values = preset.get("values") if isinstance(preset, dict) else None
        if not isinstance(values, dict):
            return False

        status_by_name = {str(name).casefold(): value for name, value in self._aux_status.items()}
        compared = 0
        for object_name, attributes in values.items():
            if not isinstance(attributes, dict) or not bool(attributes.get("bool", False)):
                continue
            try:
                wanted = float(attributes.get("value"))
            except (TypeError, ValueError):
                return False
            status = status_by_name.get(str(object_name).casefold())
            if not isinstance(status, dict) or status.get("target") is None:
                return False
            try:
                actual = float(status.get("target"))
            except (TypeError, ValueError):
                return False
            if abs(actual - wanted) > 0.5:
                return False
            compared += 1
        return compared > 0

    @pyqtProperty(QVariant, notify=typedControlsChanged)
    def temperaturePresetItems(self) -> QVariant:
        items: List[Dict[str, Any]] = []
        for index, item in enumerate(self._temperature_presets):
            items.append({
                "index": index,
                "name": str(item.get("name") or "Temperature preset"),
                "active": self._preset_is_active(item),
            })
        return QVariant(items)
