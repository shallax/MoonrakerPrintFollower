from __future__ import annotations

import ast
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

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        self._macro_parameter_definitions: Dict[str, List[Dict[str, Any]]] = {}
        self._pwm_output_items: List[Dict[str, Any]] = []
        super().__init__(output_controller, number_of_extruders, follower)

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
        self.typedControlsChanged.emit()

    def _on_temperature_presets(self, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        super()._on_temperature_presets(payload, error)
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
