from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QTimer, QVariant, pyqtProperty, pyqtSignal, pyqtSlot

from .MoonrakerMonitorRuntime import MoonrakerMonitorModel as _BaseMoonrakerMonitorModel


class MoonrakerMonitorModel(_BaseMoonrakerMonitorModel):
    """Advanced Monitor controls layered on the follower-aware Monitor model."""

    controlsChanged = pyqtSignal()
    emergencyStopChanged = pyqtSignal()

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        self._runtime_metadata_filename = ""
        self._metadata_layer_count: Optional[int] = None
        self._metadata_layer_height: Optional[float] = None
        self._metadata_first_layer_height: Optional[float] = None
        self._monitor_layer_height = "—"
        self._speed_factor_percent = 100
        self._flow_factor_percent = 100
        self._z_offset = 0.0
        self._homed_axes = ""
        self._macros: List[str] = []
        self._temperature_presets: List[Dict[str, Any]] = []
        self._fan_control_items: List[Dict[str, Any]] = []
        self._led_items: List[Dict[str, Any]] = []
        self._led_last_nonzero: Dict[str, List[List[float]]] = {}
        self._has_qgl = False
        self._has_bed_mesh = False
        self._save_config_pending = False
        self._save_config_items: Dict[str, Any] = {}
        self._estop_clicks = 0
        self._estop_last_click = 0.0
        self._estop_reset_timer: Optional[QTimer] = None
        super().__init__(output_controller, number_of_extruders, follower)

        self._estop_reset_timer = QTimer(self)
        self._estop_reset_timer.setSingleShot(True)
        self._estop_reset_timer.setInterval(1000)
        self._estop_reset_timer.timeout.connect(self._reset_emergency_stop)

    @staticmethod
    def _same_print_file(indexed_filename: Any, live_filename: Any) -> bool:
        indexed = str(indexed_filename or "").replace("\\", "/").strip("/")
        live = str(live_filename or "").replace("\\", "/").strip("/")
        if not indexed or not live:
            return False
        if indexed == live:
            return True
        return os.path.basename(indexed) == os.path.basename(live)

    @pyqtSlot(object)
    def updateMoonrakerStatus(self, status: Any) -> None:
        if isinstance(status, dict):
            print_stats = status.get("print_stats") or {}
            filename = str(print_stats.get("filename") or "")
            if filename != self._runtime_metadata_filename:
                self._runtime_metadata_filename = filename
                self._metadata_layer_count = None
                self._metadata_layer_height = None
                self._metadata_first_layer_height = None
                self._monitor_layer_height = "—"

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

        self._monitor_layer_height = self._resolve_layer_height_text(current_layer)

        if isinstance(status, dict):
            gcode_move = status.get("gcode_move") or {}
            try:
                self._speed_factor_percent = max(1, int(round(float(gcode_move.get("speed_factor") or 1.0) * 100.0)))
            except (TypeError, ValueError):
                self._speed_factor_percent = 100
            try:
                self._flow_factor_percent = max(1, int(round(float(gcode_move.get("extrude_factor") or 1.0) * 100.0)))
            except (TypeError, ValueError):
                self._flow_factor_percent = 100
            origin = gcode_move.get("homing_origin")
            if isinstance(origin, (list, tuple)) and len(origin) >= 3:
                try:
                    self._z_offset = float(origin[2])
                except (TypeError, ValueError):
                    pass

        self.monitorChanged.emit()
        self.controlsChanged.emit()

    def _on_metadata(self, filename: str, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        super()._on_metadata(filename, payload, error)
        if filename != self._monitor_filename or error:
            return
        data = self._result(payload)
        if not isinstance(data, dict):
            return
        self._metadata_layer_count = self._as_positive_int(data.get("layer_count"))
        self._metadata_layer_height = self._as_positive_float(data.get("layer_height"))
        self._metadata_first_layer_height = self._as_positive_float(data.get("first_layer_height"))
        self.controlsChanged.emit()
        self._refresh_core_now()

    def _resolve_live_layer(self, status: Any) -> Tuple[Optional[int], Optional[int]]:
        if not isinstance(status, dict):
            return None, self._metadata_layer_count

        print_stats = status.get("print_stats") or {}
        gcode_move = status.get("gcode_move") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        info = print_stats.get("info") or {}
        if not isinstance(info, dict):
            info = {}

        filename = str(print_stats.get("filename") or "")
        raw_remote_layer = info.get("current_layer")
        total_layer = self._as_positive_int(info.get("total_layer")) or self._metadata_layer_count
        target_layer: Optional[int] = None

        indexed_filename = getattr(self._follower, "_remote_index_filename", None)
        same_index_file = self._same_print_file(indexed_filename, filename)
        ranges = list(getattr(self._follower, "_remote_layer_ranges", []) or [])
        if total_layer is None and same_index_file and ranges:
            total_layer = len(ranges)

        # When live Preview following is active, Cura's current layer is the
        # authoritative result of the follower's CURRENT_LAYER mapping, one/zero
        # based handling and Z fallback. Reusing that settled value keeps Monitor
        # layer progress from independently choosing a different layer.
        try:
            config = self._follower.current_printer_config()
            following_live = bool(config.enabled) and not bool(getattr(self._follower, "_following_paused", False))
            if following_live and self._same_print_file(getattr(self._follower, "_last_remote_filename", ""), filename):
                view = self._follower._simulation_view()
                if view is not None and bool(getattr(self._follower, "_cura_has_toolpath")()):
                    authoritative_layer = max(0, int(view.getCurrentLayer()))
                    authoritative_total = total_layer
                    if hasattr(view, "getMaxLayers"):
                        local_total = max(1, int(view.getMaxLayers()) + 1)
                        if authoritative_total is None:
                            authoritative_total = local_total
                    if authoritative_total is not None:
                        authoritative_layer = min(authoritative_layer, authoritative_total - 1)
                    return authoritative_layer + 1, authoritative_total
        except Exception:
            pass

        if raw_remote_layer is not None:
            try:
                raw = int(raw_remote_layer)
                layer_map = getattr(self._follower, "_remote_current_layer_map", {})
                if same_index_file and raw in layer_map:
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
                if same_index_file and ranges:
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

        if target_layer is None and total_layer:
            z_layer = self._layer_from_metadata_z(gcode_move, total_layer)
            if z_layer is not None:
                target_layer = z_layer
            else:
                try:
                    progress = float(virtual_sdcard.get("progress"))
                except (TypeError, ValueError):
                    progress = -1.0
                if 0.0 <= progress <= 1.0:
                    target_layer = min(total_layer - 1, max(0, int(progress * total_layer)))

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

        if total_layer is not None:
            target_layer = min(target_layer, max(0, total_layer - 1))
        return target_layer + 1, total_layer

    def _layer_from_metadata_z(self, gcode_move: Any, total_layer: int) -> Optional[int]:
        layer_height = self._metadata_layer_height
        if layer_height is None or layer_height <= 0:
            return None
        first_height = self._metadata_first_layer_height or layer_height
        if not isinstance(gcode_move, dict):
            return None
        raw_position = gcode_move.get("gcode_position")
        if not isinstance(raw_position, (list, tuple)) or len(raw_position) < 3:
            raw_position = gcode_move.get("position")
        if not isinstance(raw_position, (list, tuple)) or len(raw_position) < 3:
            return None
        try:
            z = float(raw_position[2])
        except (TypeError, ValueError):
            return None
        if z <= 0:
            return None
        if z <= first_height + max(0.02, layer_height * 0.25):
            return 0
        raw_index = (z - first_height) / layer_height
        nearest = int(round(raw_index))
        expected = first_height + nearest * layer_height
        if abs(z - expected) > max(0.025, layer_height * 0.30):
            return None
        return min(total_layer - 1, max(0, nearest))

    def _resolve_layer_height_text(self, current_layer: Optional[int]) -> str:
        height = self._metadata_layer_height
        if current_layer == 1 and self._metadata_first_layer_height is not None:
            height = self._metadata_first_layer_height
        if height is None:
            return "—"
        formatted = f"{height:.3f}".rstrip("0").rstrip(".")
        return f"{formatted} mm"

    @staticmethod
    def _as_positive_float(value: Any) -> Optional[float]:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None

    @pyqtProperty(str, notify=controlsChanged)
    def monitorLayerHeight(self) -> str:
        return self._monitor_layer_height

    @staticmethod
    def _want_aux_object(name: str) -> bool:
        lower = str(name or "").lower()
        if _BaseMoonrakerMonitorModel._want_aux_object(name):
            return True
        if lower in {"configfile", "toolhead", "quad_gantry_level", "bed_mesh"}:
            return True
        return lower.startswith(("neopixel ", "dotstar ", "led ", "pca9533 ", "pca9632 "))

    def _on_objects_list(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        super()._on_objects_list(payload, error)
        if error:
            return
        objects = list(getattr(self, "_available_objects", []) or [])
        macros: List[str] = []
        for object_name in objects:
            lower = object_name.lower()
            if lower.startswith("gcode_macro "):
                macro = object_name[len("gcode_macro "):].strip()
                if macro and not macro.startswith("_"):
                    macros.append(macro)
        self._macros = sorted(macros, key=str.casefold)
        lower_objects = {item.lower() for item in objects}
        self._has_qgl = "quad_gantry_level" in lower_objects
        self._has_bed_mesh = "bed_mesh" in lower_objects
        self.controlsChanged.emit()
        self.refreshTemperaturePresets()

    def _rebuild_peripherals(self) -> None:
        super()._rebuild_peripherals()
        toolhead = self._aux_status.get("toolhead") or {}
        if isinstance(toolhead, dict):
            self._homed_axes = str(toolhead.get("homed_axes") or "")

        configfile = self._aux_status.get("configfile") or {}
        if isinstance(configfile, dict):
            self._save_config_pending = bool(configfile.get("save_config_pending", False))
            items = configfile.get("save_config_pending_items")
            self._save_config_items = items if isinstance(items, dict) else {}
        else:
            self._save_config_pending = False
            self._save_config_items = {}

        self._fan_control_items = self._build_fan_controls()
        self._led_items = self._build_led_controls()
        self.controlsChanged.emit()

    def _build_fan_controls(self) -> List[Dict[str, Any]]:
        controls: List[Dict[str, Any]] = []
        for object_name in sorted(self._aux_status.keys()):
            value = self._aux_status.get(object_name)
            if not isinstance(value, dict) or "speed" not in value:
                continue
            lower = object_name.lower()
            if lower != "fan" and not lower.startswith("fan_generic "):
                continue
            try:
                speed = max(0.0, min(1.0, float(value.get("speed") or 0.0)))
            except (TypeError, ValueError):
                speed = 0.0
            controls.append({
                "object": object_name,
                "name": self._friendly_object_name(object_name),
                "percent": int(round(speed * 100.0)),
            })
        return controls

    def _led_supports_white(self, object_name: str) -> bool:
        configfile = self._aux_status.get("configfile") or {}
        raw_config = configfile.get("config") if isinstance(configfile, dict) else None
        if not isinstance(raw_config, dict):
            return False
        wanted = str(object_name or "").casefold()
        section = next((value for key, value in raw_config.items() if str(key).casefold() == wanted and isinstance(value, dict)), None)
        if not isinstance(section, dict):
            return False
        if "white_pin" in section:
            return True
        return "W" in str(section.get("color_order") or "").upper()

    def _build_led_controls(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for object_name in sorted(self._aux_status.keys()):
            lower = object_name.lower()
            if not lower.startswith(("neopixel ", "dotstar ", "led ", "pca9533 ", "pca9632 ")):
                continue
            value = self._aux_status.get(object_name)
            if not isinstance(value, dict):
                continue
            raw_colors = value.get("color_data")
            colors: List[List[float]] = []
            if isinstance(raw_colors, (list, tuple)):
                for raw in raw_colors:
                    if not isinstance(raw, (list, tuple)):
                        continue
                    converted: List[float] = []
                    for channel in list(raw)[:4]:
                        try:
                            converted.append(max(0.0, min(1.0, float(channel))))
                        except (TypeError, ValueError):
                            converted.append(0.0)
                    if converted:
                        colors.append(converted)
            if not colors:
                continue
            brightness = max((max(color) if color else 0.0) for color in colors)
            if brightness > 0.001:
                self._led_last_nonzero[object_name] = [list(color) for color in colors]
            representative = [0.0, 0.0, 0.0, 0.0]
            for color in colors:
                padded = list(color) + [0.0] * (4 - len(color))
                for index in range(4):
                    representative[index] += padded[index]
            representative = [value / len(colors) for value in representative]
            chroma_scale = max(representative)
            if chroma_scale <= 0.001:
                remembered = self._led_last_nonzero.get(object_name) or [[1.0, 1.0, 1.0, 0.0]]
                representative = [0.0, 0.0, 0.0, 0.0]
                for color in remembered:
                    padded = list(color) + [0.0] * (4 - len(color))
                    for index in range(4):
                        representative[index] += padded[index]
                representative = [value / len(remembered) for value in representative]
                chroma_scale = max(representative)
            if chroma_scale > 0.001:
                representative = [value / chroma_scale for value in representative]
            result.append({
                "object": object_name,
                "name": self._friendly_led_name(object_name),
                "percent": int(round(brightness * 100.0)),
                "redPercent": int(round(representative[0] * 100.0)),
                "greenPercent": int(round(representative[1] * 100.0)),
                "bluePercent": int(round(representative[2] * 100.0)),
                "whitePercent": int(round(representative[3] * 100.0)),
                "hasWhite": self._led_supports_white(object_name),
            })
        return result

    @staticmethod
    def _friendly_led_name(object_name: str) -> str:
        parts = object_name.split(" ", 1)
        if len(parts) == 2:
            name = parts[1].replace("_", " ").strip()
            return name[:1].upper() + name[1:] if name else object_name
        return object_name.replace("_", " ").title()

    @pyqtProperty(QVariant, notify=controlsChanged)
    def macroNames(self) -> QVariant:
        return QVariant(self._macros)

    @pyqtProperty(bool, notify=controlsChanged)
    def hasQuadGantryLevel(self) -> bool:
        return self._has_qgl

    @pyqtProperty(bool, notify=controlsChanged)
    def hasBedMesh(self) -> bool:
        return self._has_bed_mesh

    @pyqtProperty(bool, notify=controlsChanged)
    def canRunSetup(self) -> bool:
        return not self.printActive and not self.actionBusy

    @pyqtSlot(str, str)
    def runMacro(self, name: str, arguments: str = "") -> None:
        name = str(name or "").strip()
        if name not in self._macros or self._action_busy:
            return
        arguments = str(arguments or "").replace("\r", " ").replace("\n", " ").strip()
        script = name if not arguments else f"{name} {arguments}"
        self._send_gcode_action(f"Macro {name}", script)

    @pyqtSlot()
    def homeAll(self) -> None:
        if self.canRunSetup:
            self._send_gcode_action("Home", "G28")

    @pyqtSlot()
    def runQuadGantryLevel(self) -> None:
        if self.canRunSetup and self._has_qgl:
            self._send_gcode_action("QGL", "QUAD_GANTRY_LEVEL")

    @pyqtSlot()
    def calibrateBedMesh(self) -> None:
        if self.canRunSetup and self._has_bed_mesh:
            self._send_gcode_action("Bed mesh", "BED_MESH_CALIBRATE")

    @pyqtSlot()
    def refreshTemperaturePresets(self) -> None:
        self._json_request(
            "temperature-presets",
            "GET",
            "server/database/item?namespace=mainsail&key=presets",
            self._on_temperature_presets,
            replace=True,
        )

    def _on_temperature_presets(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        if error:
            self._temperature_presets = []
            self.controlsChanged.emit()
            return
        result = self._result(payload)
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            self._temperature_presets = []
            self.controlsChanged.emit()
            return

        items: List[Dict[str, Any]] = []
        presets = value.get("presets")
        if isinstance(presets, dict):
            for preset_id, preset in presets.items():
                if not isinstance(preset, dict):
                    continue
                name = str(preset.get("name") or preset_id).strip()
                if name:
                    items.append({"id": str(preset_id), "name": name, "preset": preset})
        items.sort(key=lambda item: item["name"].casefold())

        cooldown = str(value.get("cooldownGcode") or "").strip()
        if cooldown:
            items.append({"id": "__cooldown__", "name": "Cooldown", "preset": {"gcode": cooldown, "values": {}}})
        self._temperature_presets = items
        self.controlsChanged.emit()

    @pyqtProperty(QVariant, notify=controlsChanged)
    def temperaturePresetNames(self) -> QVariant:
        return QVariant([item["name"] for item in self._temperature_presets])

    @pyqtProperty(bool, notify=controlsChanged)
    def canApplyTemperaturePreset(self) -> bool:
        return bool(self._temperature_presets) and not self.printActive and not self.actionBusy

    @pyqtSlot(int)
    def applyTemperaturePreset(self, index: int) -> None:
        if not self.canApplyTemperaturePreset:
            return
        try:
            item = self._temperature_presets[int(index)]
        except (TypeError, ValueError, IndexError):
            return
        preset = item.get("preset") or {}
        commands: List[str] = []
        values = preset.get("values")
        if isinstance(values, dict):
            for object_name, attributes in values.items():
                if not isinstance(attributes, dict) or not bool(attributes.get("bool", False)):
                    continue
                try:
                    target = float(attributes.get("value"))
                except (TypeError, ValueError):
                    continue
                parts = str(object_name).split(" ", 1)
                object_type = parts[0]
                command_name = parts[1] if len(parts) > 1 else parts[0]
                if object_type == "temperature_fan":
                    commands.append(f"SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN={command_name} TARGET={target:g}")
                else:
                    commands.append(f"SET_HEATER_TEMPERATURE HEATER={command_name} TARGET={target:g}")
        extra = str(preset.get("gcode") or "").strip()
        if extra:
            commands.append(extra)
        if commands:
            self._send_gcode_action(str(item.get("name") or "Temperature preset"), "\n".join(commands))

    @pyqtProperty(int, notify=controlsChanged)
    def speedFactorPercent(self) -> int:
        return self._speed_factor_percent

    @pyqtProperty(int, notify=controlsChanged)
    def flowFactorPercent(self) -> int:
        return self._flow_factor_percent

    @pyqtProperty(float, notify=controlsChanged)
    def zOffset(self) -> float:
        return self._z_offset

    @pyqtProperty(str, notify=controlsChanged)
    def zOffsetText(self) -> str:
        return f"{self._z_offset:+.3f} mm"

    @pyqtProperty(QVariant, notify=controlsChanged)
    def fanControlItems(self) -> QVariant:
        return QVariant(self._fan_control_items)

    @pyqtProperty(QVariant, notify=controlsChanged)
    def ledItems(self) -> QVariant:
        return QVariant(self._led_items)

    @pyqtSlot(int)
    def setSpeedFactor(self, percent: int) -> None:
        try:
            value = max(10, min(200, int(percent)))
        except (TypeError, ValueError):
            return
        self._send_quick_gcode("speed-factor", f"M220 S{value}")

    @pyqtSlot(int)
    def setFlowFactor(self, percent: int) -> None:
        try:
            value = max(50, min(150, int(percent)))
        except (TypeError, ValueError):
            return
        self._send_quick_gcode("flow-factor", f"M221 S{value}")

    @pyqtSlot(float)
    def adjustZOffset(self, amount: float) -> None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return
        if abs(amount) < 0.0001 or abs(amount) > 5.0:
            return
        move = " MOVE=1" if set(self._homed_axes.lower()) >= {"x", "y", "z"} else ""
        self._send_gcode_action("Z offset", f"SET_GCODE_OFFSET Z_ADJUST={amount:+g}{move}")

    @pyqtSlot()
    def clearZOffset(self) -> None:
        move = " MOVE=1" if set(self._homed_axes.lower()) >= {"x", "y", "z"} else ""
        self._send_gcode_action("Clear Z offset", f"SET_GCODE_OFFSET Z=0{move}")

    @pyqtSlot(str, int)
    def setFanSpeed(self, object_name: str, percent: int) -> None:
        object_name = str(object_name or "")
        known = next((item for item in self._fan_control_items if item.get("object") == object_name), None)
        if not known:
            return
        try:
            value = max(0, min(100, int(percent)))
        except (TypeError, ValueError):
            return
        if object_name.lower() == "fan":
            script = f"M106 S{int(round(value * 255.0 / 100.0))}"
        else:
            fan_name = object_name.split(" ", 1)[1] if " " in object_name else object_name
            script = f"SET_FAN_SPEED FAN={fan_name} SPEED={value / 100.0:.3f}"
        self._send_quick_gcode("fan-" + object_name, script)

    @pyqtSlot(str, int)
    def setLedBrightness(self, object_name: str, percent: int) -> None:
        object_name = str(object_name or "")
        if not any(item.get("object") == object_name for item in self._led_items):
            return
        try:
            target = max(0.0, min(1.0, int(percent) / 100.0))
        except (TypeError, ValueError):
            return

        value = self._aux_status.get(object_name) or {}
        raw_colors = value.get("color_data") if isinstance(value, dict) else None
        colors: List[List[float]] = []
        if isinstance(raw_colors, (list, tuple)):
            for raw in raw_colors:
                if isinstance(raw, (list, tuple)):
                    try:
                        colors.append([max(0.0, min(1.0, float(v))) for v in list(raw)[:4]])
                    except (TypeError, ValueError):
                        continue
        current_brightness = max((max(color) if color else 0.0) for color in colors) if colors else 0.0
        if current_brightness <= 0.001:
            colors = [list(color) for color in self._led_last_nonzero.get(object_name, [[1.0, 1.0, 1.0, 0.0]])]
            current_brightness = max((max(color) if color else 0.0) for color in colors) if colors else 1.0
        scale = target / current_brightness if current_brightness > 0 else 0.0
        led_name = object_name.split(" ", 1)[1] if " " in object_name else object_name
        commands: List[str] = []
        for index, color in enumerate(colors, 1):
            scaled = [max(0.0, min(1.0, channel * scale)) for channel in color]
            while len(scaled) < 4:
                scaled.append(0.0)
            commands.append(
                f"SET_LED LED={led_name} INDEX={index} RED={scaled[0]:.4f} GREEN={scaled[1]:.4f} "
                f"BLUE={scaled[2]:.4f} WHITE={scaled[3]:.4f} TRANSMIT={1 if index == len(colors) else 0}"
            )
        if commands:
            self._send_quick_gcode("led-" + object_name, "\n".join(commands))

    @pyqtSlot(str, int, int, int, int, int)
    def setLedColor(self, object_name: str, red: int, green: int, blue: int, white: int = 0, brightness_percent: int = -1) -> None:
        object_name = str(object_name or "")
        item = next((entry for entry in self._led_items if entry.get("object") == object_name), None)
        if item is None:
            return
        try:
            channels = [max(0.0, min(1.0, int(value) / 100.0)) for value in (red, green, blue, white)]
        except (TypeError, ValueError):
            return
        if not bool(item.get("hasWhite")):
            channels[3] = 0.0
        peak = max(channels)
        if peak <= 0.001:
            channels = [0.0, 0.0, 0.0, 0.0]
        else:
            channels = [value / peak for value in channels]

        try:
            if int(brightness_percent) >= 0:
                brightness = max(0.0, min(1.0, int(brightness_percent) / 100.0))
            else:
                brightness = max(0.0, min(1.0, float(item.get("percent") or 0.0) / 100.0))
        except (TypeError, ValueError):
            brightness = 0.0
        if brightness <= 0.001:
            remembered = self._led_last_nonzero.get(object_name) or []
            brightness = max((max(color) if color else 0.0) for color in remembered) if remembered else 1.0
        scaled = [value * brightness for value in channels]
        led_name = object_name.split(" ", 1)[1] if " " in object_name else object_name
        script = (
            f"SET_LED LED={led_name} RED={scaled[0]:.4f} GREEN={scaled[1]:.4f} "
            f"BLUE={scaled[2]:.4f} WHITE={scaled[3]:.4f} TRANSMIT=1"
        )
        self._send_quick_gcode("led-colour-" + object_name, script)

    @pyqtProperty(bool, notify=controlsChanged)
    def saveConfigPending(self) -> bool:
        return self._save_config_pending

    @pyqtProperty(str, notify=controlsChanged)
    def saveConfigSummary(self) -> str:
        if not self._save_config_items:
            return "Unsaved Klipper configuration changes"
        return "Unsaved: " + ", ".join(sorted(str(key) for key in self._save_config_items.keys()))

    @pyqtProperty(bool, notify=controlsChanged)
    def canSaveConfig(self) -> bool:
        return self._save_config_pending and not self.printActive and not self.actionBusy

    @pyqtSlot()
    def saveConfig(self) -> None:
        if self.canSaveConfig:
            self._send_gcode_action("Save configuration", "SAVE_CONFIG")

    @pyqtProperty(int, notify=emergencyStopChanged)
    def emergencyStopClicks(self) -> int:
        return self._estop_clicks

    @pyqtSlot()
    def emergencyStopClick(self) -> None:
        now = time.monotonic()
        if self._estop_clicks > 0 and now - self._estop_last_click > 1.0:
            self._estop_clicks = 0
        self._estop_last_click = now
        self._estop_clicks = min(3, self._estop_clicks + 1)
        self.emergencyStopChanged.emit()
        if self._estop_reset_timer is not None:
            self._estop_reset_timer.start(1000)

        if self._estop_clicks >= 3:
            self._json_request(
                "emergency-stop",
                "POST",
                "printer/emergency_stop",
                self._on_emergency_stop_finished,
                replace=True,
            )

    def _on_emergency_stop_finished(self, _payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        self._action_status = f"Emergency stop failed: {error}" if error else "Emergency stop issued"
        self.actionChanged.emit()
        self._refresh_core_now()

    def _reset_emergency_stop(self) -> None:
        if self._estop_clicks == 0:
            return
        self._estop_clicks = 0
        self._estop_last_click = 0.0
        self.emergencyStopChanged.emit()

    def _send_gcode_action(self, label: str, script: str) -> None:
        if self._action_busy:
            return
        self._action_busy = True
        self._action_status = f"{label} requested…"
        self.actionChanged.emit()
        started = self._json_request(
            "control",
            "POST",
            "printer/gcode/script",
            lambda payload, error, l=label: self._on_control_finished(l, payload, error),
            body={"script": str(script)},
        )
        if not started:
            self._action_busy = False
            self._action_status = "Moonraker is not available"
            self.actionChanged.emit()

    def _send_quick_gcode(self, channel: str, script: str) -> None:
        self._json_request(
            "quick-" + str(channel),
            "POST",
            "printer/gcode/script",
            lambda _payload, _error: None,
            body={"script": str(script)},
            replace=True,
        )
