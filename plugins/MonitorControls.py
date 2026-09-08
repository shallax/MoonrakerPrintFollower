"""Printer-control policy composed with data, command and tuning capabilities."""
from __future__ import annotations
from collections.abc import Mapping
from copy import deepcopy
import shlex

from PyQt6.QtCore import QObject, pyqtSignal
from .MonitorFormatting import (
    FAN_OBJECT_PREFIXES, LED_OBJECT_PREFIXES, PWM_OBJECT_PREFIXES,
    friendly, infer_macro_parameters, number, mesh_profiles,
)


class MonitorControls(QObject):
    changed = pyqtSignal()

    def __init__(self, data, commands, tuning, bed_mesh, config, parent=None):
        super().__init__(parent)
        self._data, self._commands, self._tuning = data, commands, tuning
        self._mesh, self._config = bed_mesh, config
        self._remembered_colors = {}
        self._macro_cache = {}
        self._config_identity = None
        self._values = {}
        self._macros, self._presets = [], []
        data.changed.connect(self.observe)
        data.invalidated.connect(self.reset)
        tuning.changed.connect(self.observe)
        commands.changed.connect(self.observe)
        bed_mesh.changed.connect(self.changed.emit)
        self.observe()

    @property
    def values(self): return deepcopy(self._values)

    def reset(self):
        self._remembered_colors.clear()
        self._macro_cache.clear()
        self._config_identity = None
        self.observe()

    @staticmethod
    def section(config, name):
        return next((value for key, value in config.items() if str(key).casefold() == str(name).casefold() and isinstance(value, Mapping)), {})

    def _display(self, key, actual):
        self._tuning.observe(key, actual)
        return self._tuning.value(key, actual)

    def observe(self):
        snapshot = self._data.snapshot
        aux = snapshot.auxiliary
        configfile = aux.get("configfile") or {}
        config = configfile.get("config") or {}
        if config is not self._config_identity:
            self._config_identity = config
            self._macro_cache.clear()
        self._macros = sorted([name.split(" ", 1)[1] for name in snapshot.objects
            if name.lower().startswith("gcode_macro ") and not name.split(" ", 1)[1].startswith("_")], key=str.casefold)
        fans, leds, pwm = [], [], []
        for name, value in sorted(aux.items()):
            if not isinstance(value, Mapping): continue
            lower = name.lower()
            if lower == "fan" or lower.startswith(FAN_OBJECT_PREFIXES):
                actual = round(max(0, min(1, number(value.get("speed")))) * 100)
                fans.append({"object": name, "name": friendly(name), "percent": self._display("fan:" + name, actual)})
            if lower.startswith(LED_OBJECT_PREFIXES):
                colors = [[max(0, min(1, number(c))) for c in raw[:4]] for raw in value.get("color_data", ()) if isinstance(raw, (tuple, list))]
                if not colors: continue
                brightness = max(max(color, default=0) for color in colors)
                if brightness > 0.001: self._remembered_colors[name] = colors
                source = colors if brightness > 0.001 else self._remembered_colors.get(name, [[1, 1, 1, 0]])
                chroma = [sum((color + [0] * 4)[i] for color in source) / len(source) for i in range(4)]
                peak = max(chroma)
                chroma = tuple(round(c * 100 / peak) if peak > 0.001 else 0 for c in chroma)
                color = self._display("led-colour:" + name, chroma)
                section = self.section(config, name)
                leds.append({"object": name, "name": friendly(name), "percent": self._display("led-brightness:" + name, round(brightness * 100)),
                    "redPercent": color[0], "greenPercent": color[1], "bluePercent": color[2], "whitePercent": color[3],
                    "hasWhite": "white_pin" in section or "W" in str(section.get("color_order") or "").upper()})
            if lower.startswith(PWM_OBJECT_PREFIXES):
                section = self.section(config, name)
                if str(section.get("pwm") or "").lower() not in {"1", "true", "yes", "on"} or value.get("value") is None: continue
                scale = number(section.get("scale"), 1)
                if scale <= 0: scale = 1
                actual = round(max(0, min(scale, number(value.get("value")))) * 100 / scale)
                pwm.append({"object": name, "pin": name.split(" ", 1)[1], "name": friendly(name), "scale": scale,
                    "percent": self._display("pwm-output:" + name, actual)})
        presets = snapshot.presets.get("presets") or {}
        self._presets = sorted([{"id": key, "name": str(value.get("name") or key), "preset": value}
            for key, value in presets.items() if isinstance(value, Mapping)], key=lambda item: item["name"].casefold()) if isinstance(presets, Mapping) else []
        if snapshot.presets.get("cooldownGcode"):
            self._presets.append({"id": "__cooldown__", "name": "Cooldown", "preset": {"gcode": snapshot.presets["cooldownGcode"], "values": {}}})
        move = snapshot.core.get("gcode_move") or {}
        origin = move.get("homing_origin") or ()
        changes = configfile.get("save_config_pending_items") or {}
        setup = self._commands.setup_allowed
        objects = {name.lower() for name in snapshot.objects}
        profiles = mesh_profiles(aux.get("bed_mesh"))
        self._values = {
            "macroNames": list(self._macros), "hasQuadGantryLevel": "quad_gantry_level" in objects,
            "hasBedMesh": "bed_mesh" in objects, "canRunSetup": setup,
            "temperaturePresetNames": [item["name"] for item in self._presets],
            "temperaturePresetItems": [{"index": i, "name": item["name"], "active": self.preset_active(item, aux)} for i, item in enumerate(self._presets)],
            "canApplyTemperaturePreset": setup and bool(self._presets),
            "speedFactorPercent": self._display("speed-factor", round(number(move.get("speed_factor"), 1) * 100)),
            "flowFactorPercent": self._display("flow-factor", round(number(move.get("extrude_factor"), 1) * 100)),
            "zOffset": number(origin[2]) if len(origin) > 2 else 0,
            "zOffsetText": f"{number(origin[2]) if len(origin) > 2 else 0:+.3f} mm",
            "fanControlItems": fans, "ledItems": leds, "pwmOutputItems": pwm,
            "saveConfigPending": bool(configfile.get("save_config_pending")),
            "saveConfigSummary": "Unsaved: " + ", ".join(sorted(changes)) if changes else "Unsaved Klipper configuration changes",
            "canSaveConfig": setup and bool(configfile.get("save_config_pending")), "bedMeshProfileNames": profiles,
        }
        self.changed.emit()

    @staticmethod
    def preset_active(item, auxiliary):
        values = (item.get("preset") or {}).get("values")
        if not isinstance(values, Mapping): return False
        status = {name.casefold(): value for name, value in auxiliary.items()}
        compared = 0
        for name, attributes in values.items():
            if not isinstance(attributes, Mapping) or not attributes.get("bool"): continue
            target = number(attributes.get("value"), None)
            actual = number((status.get(name.casefold()) or {}).get("target"), None)
            if target is None or actual is None or abs(target - actual) > 0.5: return False
            compared += 1
        return compared > 0

    def macro_parameters(self, name):
        if name not in self._macros: return []
        if name not in self._macro_cache:
            config = (self._data.snapshot.auxiliary.get("configfile") or {}).get("config") or {}
            self._macro_cache[name] = infer_macro_parameters(self.section(config, "gcode_macro " + name).get("gcode", ""))
        return deepcopy(self._macro_cache[name])

    def run_macro(self, name, arguments=""):
        if name in self._macros and not self._commands.print_active:
            arguments = str(arguments).replace("\r", " ").replace("\n", " ").strip()
            self._commands.script("Macro " + name, name + (" " + arguments if arguments else ""))

    def setup(self, name):
        scripts = {"home": ("Home", "G28", True), "qgl": ("QGL", "QUAD_GANTRY_LEVEL", self._values.get("hasQuadGantryLevel")),
            "mesh": ("Bed mesh", "BED_MESH_CALIBRATE", self._values.get("hasBedMesh")),
            "save": ("Save configuration", "SAVE_CONFIG", self._values.get("saveConfigPending"))}
        label, script, allowed = scripts[name]
        if self._commands.setup_allowed and allowed: self._commands.script(label, script)

    def firmware_restart(self):
        if not self._data.active or self._commands.print_active: return
        self._commands.script("Firmware restart", "FIRMWARE_RESTART")

    def host_restart(self):
        if not self._data.active or self._commands.print_active: return
        self._commands.request("Host restart", "machine/reboot", {})

    def apply_preset(self, index):
        if not self._commands.setup_allowed or not 0 <= index < len(self._presets): return
        item = self._presets[index]
        preset, commands = item["preset"], []
        for name, attributes in (preset.get("values") or {}).items():
            if not isinstance(attributes, Mapping) or not attributes.get("bool"): continue
            target = number(attributes.get("value"), None)
            if target is None: continue
            parts = name.split(" ", 1)
            heater = parts[-1]
            command = f"SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN={heater}" if parts[0] == "temperature_fan" else f"SET_HEATER_TEMPERATURE HEATER={heater}"
            commands.append(command + f" TARGET={target:g}")
        if preset.get("gcode"): commands.append(str(preset["gcode"]))
        if commands: self._commands.script(item["name"], "\n".join(commands))

    def heaters_off(self):
        """Cooldown: set every heater appearing in the profiles to 0 target.

        The union of profile heater entries is the safe heater inventory —
        auxiliary objects include temperature *sensors*, which take no
        target and must not receive a heater command.
        """
        if not self._commands.setup_allowed: return
        commands, seen = [], set()
        for item in self._presets:
            for name, attributes in (item.get("preset") or {}).get("values", {}).items():
                if not isinstance(attributes, Mapping) or not attributes.get("bool"): continue
                if name in seen: continue
                seen.add(name)
                parts = name.split(" ", 1)
                heater = parts[-1]
                command = (f"SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN={heater}"
                           if parts[0] == "temperature_fan" else f"SET_HEATER_TEMPERATURE HEATER={heater}")
                commands.append(command + " TARGET=0")
        if commands: self._commands.script("Cooldown", "\n".join(commands))

    def factor(self, kind, percent, preview=False):
        percent = max(10 if kind == "speed" else 50, int(percent))
        key = kind + "-factor"
        if preview: self._tuning.preview(key, percent)
        else: self._tuning.queue(key, percent, key, f"{'M220' if kind == 'speed' else 'M221'} S{percent}")

    def z_offset(self, amount=None):
        if amount is not None and (abs(amount) < 0.0001 or abs(amount) > 5): return
        homed = str((self._data.snapshot.auxiliary.get("toolhead") or {}).get("homed_axes") or "")
        script = "SET_GCODE_OFFSET " + (f"Z_ADJUST={amount:+g}" if amount is not None else "Z=0")
        if set(homed.lower()) >= {"x", "y", "z"}: script += " MOVE=1"
        self._commands.script("Z offset", script)

    def output(self, kind, name, percent, preview=False):
        list_name = {"fan": "fanControlItems", "pwm-output": "pwmOutputItems", "led-brightness": "ledItems"}[kind]
        item = next((item for item in self._values.get(list_name, ()) if item["object"] == name), None)
        if item is None: return
        percent = max(0, min(100, int(percent)))
        key = kind + ":" + name
        if preview:
            self._tuning.preview(key, percent)
            return
        suffix = name.split(" ", 1)[-1]
        if kind == "fan":
            script = f"M106 S{round(percent * 255 / 100)}" if name == "fan" else f"SET_FAN_SPEED FAN={suffix} SPEED={percent / 100:.3f}"
        elif kind == "pwm-output": script = f"SET_PIN PIN={suffix} VALUE={item['scale'] * percent / 100:g}"
        else:
            raw = (self._data.snapshot.auxiliary.get(name) or {}).get("color_data") or ()
            colors = [[number(c) for c in color[:4]] for color in raw if isinstance(color, (list, tuple))]
            peak = max((max(color, default=0) for color in colors), default=0)
            if peak <= 0.001:
                colors = self._remembered_colors.get(name, [[1, 1, 1, 0]])
                peak = max(max(color) for color in colors)
            factor = percent / (peak * 100) if peak else 0
            commands = []
            for i, color in enumerate(colors, 1):
                c = [max(0, min(1, value * factor)) for value in color] + [0] * 4
                commands.append(f"SET_LED LED={suffix} INDEX={i} RED={c[0]:.4f} GREEN={c[1]:.4f} BLUE={c[2]:.4f} WHITE={c[3]:.4f} TRANSMIT={int(i == len(colors))}")
            script = "\n".join(commands)
        self._tuning.queue(key, percent, key, script)

    def led_color(self, name, red, green, blue, white=0, brightness=-1, preview=False):
        item = next((item for item in self._values.get("ledItems", ()) if item["object"] == name), None)
        if item is None: return
        channels = [max(0, min(1, value / 100)) for value in (red, green, blue, white)]
        if not item["hasWhite"]: channels[3] = 0
        peak = max(channels)
        chroma = [value / peak if peak > 0.001 else 0 for value in channels]
        desired = tuple(round(value * 100) for value in chroma)
        key = "led-colour:" + name
        if preview:
            self._tuning.preview(key, desired)
            return
        level = max(0, min(1, (brightness if brightness >= 0 else item["percent"]) / 100))
        if level <= 0.001: level = max((max(c) for c in self._remembered_colors.get(name, [[1, 1, 1, 0]])), default=1)
        c = [value * level for value in chroma]
        script = f"SET_LED LED={name.split(' ', 1)[-1]} RED={c[0]:.4f} GREEN={c[1]:.4f} BLUE={c[2]:.4f} WHITE={c[3]:.4f} TRANSMIT=1"
        self._tuning.queue(key, desired, key, script)

    def mesh_profile(self, name):
        if self._commands.setup_allowed and name in self._values.get("bedMeshProfileNames", ()):
            self._commands.script("Load mesh " + name, "BED_MESH_PROFILE LOAD=" + shlex.quote(name))

    def clear_mesh(self):
        if self._commands.setup_allowed and self._mesh.snapshot:
            self._commands.script("Clear bed mesh", "BED_MESH_CLEAR")

    def power_devices(self):
        configured = [item.strip() for item in self._config().power_devices.split(",") if item.strip()]
        raw = self._data.snapshot.power
        by_name = {item.get("device"): item for item in raw}
        source = [by_name.get(name, {"device": name}) for name in configured] if configured else raw
        return [{"name": item["device"], "status": str(item.get("status") or "unknown"),
            "locked": bool(item.get("locked_while_printing")),
            "can_toggle": not (item.get("locked_while_printing") and self._commands.print_active)} for item in source if item.get("device")]

    def set_power(self, name, on):
        item = next((item for item in self.power_devices() if item["name"] == name and item["can_toggle"]), None)
        if item:
            self._commands.send("Power " + name, "machine/device_power/device", {"device": name, "action": "on" if on else "off"})

    def exclude(self, name):
        status = self._data.snapshot.auxiliary.get("exclude_object") or {}
        names = {item.get("name") for item in status.get("objects", ())}
        if name in names and name not in status.get("excluded_objects", ()) and self._commands.print_active:
            safe = name.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
            self._commands.script("Exclude " + name, f'EXCLUDE_OBJECT NAME="{safe}"')

