"""Printer-control policy composed with data, command and tuning capabilities."""
from __future__ import annotations
from collections.abc import Mapping
from copy import deepcopy
import shlex
import time

from PyQt6.QtCore import QObject, pyqtSignal
from .MonitorFormatting import (
    FAN_OBJECT_PREFIXES, LED_OBJECT_PREFIXES, PWM_OBJECT_PREFIXES,
    fan_writable, friendly, infer_macro_parameters, number, mesh_profiles,
)
from .MonitorPermissions import R_UNKNOWN, Verdict, can_exclude, can_macro, can_power, can_restart, can_restore, can_z_offset

# The in-flight latch's hard ceiling: a gesture's pending state expires
# on its own after a few multiples of the status lag (the review's
# rule: never a permanent wedge, and never against the rescue direction).
# The plate that settles a gesture releases its latch sooner; this is the
# backstop for a status that never arrives.
PENDING_CEILING_SECONDS = 10.0


def _exclude_status(snapshot):
    """The volatile plate fields live on the CORE lane (the 4.6.0 move);
    the aux copy covers the lane's first landing."""
    core = snapshot.core.get("exclude_object") if snapshot.core else None
    if isinstance(core, Mapping) and core.get("objects"):
        return core
    aux = snapshot.auxiliary.get("exclude_object") if snapshot.auxiliary else None
    return aux if isinstance(aux, Mapping) else {}


def _escape_exclude_name(name):
    """The shared dispatch escaper — exclude AND restore use exactly
    this (the review's rule: one escaper, never two)."""
    return str(name).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


class MonitorControls(QObject):
    changed = pyqtSignal()

    def __init__(self, data, commands, tuning, bed_mesh, config, parent=None):
        super().__init__(parent)
        self._data, self._commands, self._tuning = data, commands, tuning
        self._mesh, self._config = bed_mesh, config
        self._remembered_colors = {}
        # The brightness slider holds the USER'S GAIN, unlinked from
        # the channel peak (the ruling): a channel nudge
        # must not move the brightness value, or the extra field
        # change triggers a second publish and rebuild that kills the
        # slider's focus.
        self._remembered_gain = {}
        # The channel sliders hold the USER'S set percentages (the
        # gain ruling): seeded once from the first-seen
        # colour, then only the user's own nudges change them — the
        # gain acts on the SEND, never on the displayed values.
        self._remembered_channels = {}
        self._macro_cache = {}
        self._config_identity = None
        self._values = {}
        self._values_copy = None
        self._pending = {}
        self._macros, self._presets = [], []
        data.changed.connect(self.observe)
        data.invalidated.connect(self.reset)
        tuning.changed.connect(self.observe)
        commands.changed.connect(self.observe)
        bed_mesh.changed.connect(self.changed.emit)
        self.observe()

    @property
    def values(self):
        # Copy-on-change (the 2026-09-19 review's I): the served
        # projection is a stable deep copy rebuilt only when the next
        # observation changes the outward values — the model consumed
        # a fresh deep copy on every heartbeat before.
        if self._values_copy is None:
            self._values_copy = deepcopy(self._values)
        return self._values_copy

    def reset(self):
        self._remembered_colors.clear()
        self._remembered_gain.clear()
        self._remembered_channels.clear()
        self._macro_cache.clear()
        self._config_identity = None
        # A pending exclusion is a claim about a gesture on THIS session
        # and THIS plate: a printer switch (the invalidated signal) makes
        # it meaningless, and it must not refuse the same gesture on the
        # printer that replaces it.
        self._pending.clear()
        self.observe()

    @staticmethod
    def section(config, name):
        return next((value for key, value in config.items() if str(key).casefold() == str(name).casefold() and isinstance(value, Mapping)), {})

    def _allowed(self, rule) -> bool:
        """The policy gate (4.2.0): one derivation for every control
        this owner dispatches. A missing observation — or a data
        provider without the record (the test stubs) — fails closed."""
        observation = getattr(self._data, "observation", None)
        if observation is None: return False
        return rule(observation).mode == "allowed"

    def _display(self, key, actual):
        self._tuning.observe(key, actual)
        return self._tuning.value(key, actual)

    def observe(self):
        snapshot = self._data.snapshot
        self._release_settled_latches(snapshot)
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
                fans.append({"object": name, "name": friendly(name), "percent": self._display("fan:" + name, actual),
                    "writable": fan_writable(name)})
            if lower.startswith(LED_OBJECT_PREFIXES):
                colors = [[max(0, min(1, number(c))) for c in raw[:4]] for raw in value.get("color_data", ()) if isinstance(raw, (tuple, list))]
                if not colors: continue
                brightness = max(max(color, default=0) for color in colors)
                if brightness > 0.001: self._remembered_colors[name] = colors
                # Seed the gain once from the first-seen peak; after
                # that the brightness slider is the user's own value.
                if name not in self._remembered_gain and brightness > 0.001:
                    self._remembered_gain[name] = brightness
                gain = self._remembered_gain.get(name, 0.0)
                # ABSOLUTE channels (a live report): the
                # chroma normalisation made every nudge re-scale all
                # four sliders — a +1 nudge of a zeroed channel jumped
                # it to 100 and dragged the others with it. The
                # sliders read the USER'S set values (the gain
                # ruling), seeded once from the first-seen colour.
                if name not in self._remembered_channels and brightness > 0.001:
                    self._remembered_channels[name] = [sum((color + [0] * 4)[i] for color in colors) / len(colors) for i in range(4)]
                channels = self._remembered_channels.get(name, [0.0, 0.0, 0.0, 0.0])
                color = self._display("led-colour:" + name, tuple(round(c * 100) for c in channels))
                section = self.section(config, name)
                leds.append({"object": name, "name": friendly(name), "percent": self._display("led-brightness:" + name, round(gain * 100)),
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
        # The setup projections read the policy row (4.2.0, the
        # adversarial round's M2): setup_allowed was the raw
        # two-valued derivation; can_restart is the one the setup
        # one-shots dispatch under.
        setup = self._allowed(can_restart)
        objects = {name.lower() for name in snapshot.objects}
        profiles = mesh_profiles(aux.get("bed_mesh"))
        new_values = {
            "macroNames": list(self._macros), "hasQuadGantryLevel": "quad_gantry_level" in objects,
            "hasBedMesh": "bed_mesh" in objects, "canRunSetup": setup,
            "temperaturePresetNames": [item["name"] for item in self._presets],
            "temperaturePresetItems": [{"index": i, "name": item["name"], "active": self.preset_active(item, aux)} for i, item in enumerate(self._presets)],
            "canApplyTemperaturePreset": setup and bool(self._presets),
            "speedFactorPercent": self._display("speed-factor", int(round(number(move.get("speed_factor")) * 100))),
            "flowFactorPercent": self._display("flow-factor", int(round(number(move.get("extrude_factor")) * 100))),
            "zOffset": number(origin[2]) if len(origin) > 2 else 0,
            "zOffsetText": f"{number(origin[2]) if len(origin) > 2 else 0:+.3f} mm",
            "fanControlItems": fans, "ledItems": leds, "pwmOutputItems": pwm,
            "saveConfigPending": bool(configfile.get("save_config_pending")),
            # The section is permanently visible (no-reflow rule), so a
            # quiet summary must actually be quiet: the fallback string
            # only appears when a pending change exists but Klipper
            # listed no items — never on a clean printer (the panel:
            # the old default rendered "Unsaved Klipper configuration
            # changes" permanently beside a dead Save button).
            "saveConfigSummary": "Unsaved: " + ", ".join(sorted(changes)) if changes else ("Unsaved Klipper configuration changes" if bool(configfile.get("save_config_pending")) else ""),
            "canSaveConfig": setup and bool(configfile.get("save_config_pending")), "bedMeshProfileNames": profiles,
        }
        # Emit only when the OUTWARD projection changed (the publish
        # storm's suppression): the model listens to this signal for
        # every heartbeat, and an unchanged projection must not
        # rebuild the whole model (the 2026-09-19 performance
        # review). The remembered tuning state still updates above.
        if new_values != self._values:
            self._values = new_values
            self._values_copy = None
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
        if name in self._macros and self._allowed(can_macro):
            arguments = str(arguments).replace("\r", " ").replace("\n", " ").strip()
            self._commands.script("Macro " + name, name + (" " + arguments if arguments else ""), rule=can_macro)

    def setup(self, name):
        scripts = {"home": ("Home", "G28", True), "qgl": ("QGL", "QUAD_GANTRY_LEVEL", self._values.get("hasQuadGantryLevel")),
            "mesh": ("Bed mesh", "BED_MESH_CALIBRATE", self._values.get("hasBedMesh")),
            "save": ("Save configuration", "SAVE_CONFIG", self._values.get("saveConfigPending"))}
        label, script, allowed = scripts[name]
        if self._commands.setup_allowed and allowed: self._commands.script(label, script, rule=can_restart)

    def firmware_restart(self):
        # The host's own endpoint (the ruled route): the FIRMWARE_RESTART
        # gcode disconnects immediately, so its ack never arrives and
        # success always read as failure. The policy gate (4.2.0): the
        # shipped guard read unknown as idle; the table fails closed.
        if not self._allowed(can_restart): return
        self._commands.request("Firmware restart", "printer/firmware_restart", {}, rule=can_restart)

    def klipper_restart(self):
        # A full Klipper restart (Moonraker's RESTART endpoint): reloads
        # the config, drops the MCU connection and clears Klipper state
        # — heavier than FIRMWARE_RESTART, lighter than a host reboot.
        if not self._allowed(can_restart): return
        self._commands.request("Klipper restart", "printer/restart", {}, rule=can_restart)

    def host_restart(self):
        if not self._allowed(can_restart): return
        self._commands.request("Host restart", "machine/reboot", {}, rule=can_restart)

    def apply_preset(self, index):
        if not self._commands.setup_allowed or not 0 <= index < len(self._presets): return
        # The rule rides the queued entry too (the phase-6 security
        # re-review, D1): the four one-shots below were the only
        # dispatch sites without one.
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
        if commands: self._commands.script(item["name"], "\n".join(commands), rule=can_restart)

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
        if commands: self._commands.script("Cooldown", "\n".join(commands), rule=can_restart)

    def factor(self, kind, percent, preview=False):
        percent = max(10 if kind == "speed" else 50, int(percent))
        key = kind + "-factor"
        if preview: self._tuning.preview(key, percent)
        else: self._tuning.queue(key, percent, key, f"{'M220' if kind == 'speed' else 'M221'} S{percent}")

    def z_offset(self, amount=None):
        if amount is not None and (abs(amount) < 0.0001 or abs(amount) > 5): return
        # The explicit per-action row (4.2.0, N3): babystepping is
        # allowed mid-print; the policy gate is the first Python
        # guard this path has ever had (the QML's !actionBusy was
        # the only click gate before).
        if not self._allowed(can_z_offset): return
        homed = str((self._data.snapshot.auxiliary.get("toolhead") or {}).get("homed_axes") or "")
        script = "SET_GCODE_OFFSET " + (f"Z_ADJUST={amount:+g}" if amount is not None else "Z=0")
        if set(homed.lower()) >= {"x", "y", "z"}: script += " MOVE=1"
        self._commands.script("Z offset", script, rule=can_z_offset)

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
            # Fail closed for the firmware-regulated fans (the
            # live report): no path may issue SET_FAN_SPEED
            # at a controller_fan/temperature_fan.
            if not fan_writable(name):
                return
            script = f"M106 S{round(percent * 255 / 100)}" if name == "fan" else f"SET_FAN_SPEED FAN={suffix} SPEED={percent / 100:.3f}"
        elif kind == "pwm-output": script = f"SET_PIN PIN={suffix} VALUE={item['scale'] * percent / 100:g}"
        else:
            # The brightness slider is the user's gain: remember it
            # (the unlink ruling) and scale the current colour.
            self._remembered_gain[name] = percent / 100.0
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
        # The sliders carry the user's SET percentages (the gain
        # ruling): the channels are remembered, and the SEND composes
        # them with the gain — the gain never changes the displayed
        # values.
        channels = [max(0, min(1, value / 100)) for value in (red, green, blue, white)]
        if not item["hasWhite"]: channels[3] = 0
        self._remembered_channels[name] = channels
        desired = tuple(round(value * 100) for value in channels)
        key = "led-colour:" + name
        if preview:
            self._tuning.preview(key, desired)
            return
        level = max(0.0, min(1.0, (float(brightness) / 100.0 if brightness >= 0 else self._remembered_gain.get(name, 1.0))))
        c = [value * level for value in channels]
        script = f"SET_LED LED={name.split(' ', 1)[-1]} RED={c[0]:.4f} GREEN={c[1]:.4f} BLUE={c[2]:.4f} WHITE={c[3]:.4f} TRANSMIT=1"
        self._tuning.queue(key, desired, key, script)

    def mesh_profile(self, name):
        if self._commands.setup_allowed and name in self._values.get("bedMeshProfileNames", ()):
            self._commands.script("Load mesh " + name, "BED_MESH_PROFILE LOAD=" + shlex.quote(name), rule=can_restart)

    def clear_mesh(self):
        if self._commands.setup_allowed and self._mesh.snapshot:
            self._commands.script("Clear bed mesh", "BED_MESH_CLEAR", rule=can_restart)

    def power_devices(self):
        # Every device the printer reports renders — the configured
        # auto-power-on list only drives the print-start power sequence
        # (UploadController), it never narrows the Monitor display (the
        # ruling: a configured 24v,Bed pair silently hid DFU).
        raw = self._data.snapshot.power
        # The per-device ruling (4.2.0, A3/F4): can_toggle is the
        # policy's per-device verdict — the shipped row field, now
        # one derivation (the old negation read unknown as idle).
        rows = []
        for item in raw:
            if not item.get("device"): continue
            observation = getattr(self._data, "observation", None)
            verdict = can_power(observation, item.get("locked_while_printing")) \
                if observation is not None else None
            rows.append({"name": item["device"], "status": str(item.get("status") or "unknown"),
                         "locked": bool(item.get("locked_while_printing")),
                         "can_toggle": bool(verdict and verdict.mode == "allowed")})
        return rows

    def set_power(self, name, on):
        item = next((item for item in self.power_devices() if item["name"] == name and item["can_toggle"]), None)
        if item:
            self._commands.send("Power " + name, "machine/device_power/device", {"device": name, "action": "on" if on else "off"})

    def exclude(self, name):
        name = str(name or "")
        # A no-op must still receipt (the no-confirm ruling: the
        # receipt IS the confirmation — silence re-triggers the
        # gesture, and a toggle applied twice is the inverse).
        refusal = self._object_refusal("exclude", name, _exclude_status(self._data.snapshot))
        if refusal:
            self._commands.report_status(f"Exclude refused: {refusal}")
            return
        observation = getattr(self._data, "observation", None)
        verdict = can_exclude(observation) if observation is not None \
            else Verdict("disabled", R_UNKNOWN)
        if verdict.mode != "allowed":
            # A refusal must SAY so (the adversarial round's H2): a
            # confirmed dialog that silently does nothing is the
            # exact class this release exists to end.
            self._commands.report_status(f"Exclude refused: {verdict.reason or 'no longer allowed'}")
            return
        key = ("exclude", name)
        if not self._arm(key):
            return
        started = self._commands.script("Exclude " + name, f'EXCLUDE_OBJECT NAME="{_escape_exclude_name(name)}"',
                                        rule=self._object_rule("exclude", name))
        if not started:
            # The lane never sent it (a dead transport, a full queue):
            # nothing is in flight, so the latch must not refuse the
            # retry for the rest of its ceiling.
            self._pending.pop(key, None)

    def restore(self, name):
        name = str(name or "")
        if not name:
            self._commands.report_status("Restore refused: no object named")
            return
        refusal = self._object_refusal("restore", name, _exclude_status(self._data.snapshot))
        if refusal:
            self._commands.report_status(f"Restore refused: {refusal}")
            return
        observation = getattr(self._data, "observation", None)
        verdict = can_restore(observation) if observation is not None \
            else Verdict("disabled", R_UNKNOWN)
        if verdict.mode != "allowed":
            self._commands.report_status(f"Restore refused: {verdict.reason or 'no longer allowed'}")
            return
        key = ("restore", name)
        if not self._arm(key):
            return
        # The name rides INSIDE the RESET line, proven non-empty above;
        # there is deliberately no no-name branch — a bare RESET=1
        # clears every exclusion on the plate (the review's blocker).
        started = self._commands.script("Restore " + name, f'EXCLUDE_OBJECT RESET=1 NAME="{_escape_exclude_name(name)}"',
                                        rule=self._object_rule("restore", name))
        if not started:
            self._pending.pop(key, None)

    @staticmethod
    def _object_refusal(direction, name, status):
        """Why this object gesture cannot land against the OBSERVED
        plate, '' when it can. One derivation behind three readers —
        the click gate, a queued entry's dispatch revalidation and the
        latch release — so no two of them can disagree about a name."""
        names = {item.get("name") for item in status.get("objects") or () if isinstance(item, Mapping)}
        excluded = set(status.get("excluded_objects") or ())
        if direction == "exclude":
            if name in excluded: return f"'{name}' is already excluded"
            return "" if name in names else f"'{name}' is not on the plate"
        return "" if name in excluded else f"'{name}' is not excluded"

    def _object_rule(self, direction, name):
        """The dispatch-time rule for one object gesture: the mid-print
        permission row re-run (the lane's queued-entry revalidation)
        PLUS the name-level predicate, which the click-time gate could
        only check before the entry queued — the plate moves while the
        lane is busy. A denial kills the entry, so its latch dies with
        it: a dead gesture must not hold the retry, nor the other
        direction, for the rest of the ceiling."""
        row = can_exclude if direction == "exclude" else can_restore
        def rule(observation):
            verdict = row(observation)
            if verdict.mode == "allowed":
                refusal = self._object_refusal(direction, name, _exclude_status(self._data.snapshot))
                if not refusal: return verdict
                verdict = Verdict("disabled", refusal)
            self._pending.pop((direction, name), None)
            return verdict
        return rule

    def _release_settled_latches(self, snapshot):
        """A latch claims a gesture is still in flight; the plate that
        settles it — the exclusion landed, the restore landed, the name
        left the plate — releases it. The ten-second ceiling is the
        backstop for a status that never came, never the release."""
        status = _exclude_status(snapshot)
        if not status: return
        for key in [key for key in self._pending if self._object_refusal(key[0], key[1], status)]:
            self._pending.pop(key, None)

    def exclude_current(self):
        """The Exclude current button's dispatch: the readout's current
        object by NAME — never CURRENT=1, which Klipper re-resolves at
        execution (the review's blocker). No current object: a refusal
        naming the empty target, never a guess."""
        status = _exclude_status(self._data.snapshot)
        current = status.get("current_object")
        if not current:
            self._commands.report_status("Exclude refused: no object is printing right now")
            return
        self.exclude(str(current))

    def _arm(self, key):
        """The in-flight latch: one gesture per (name, direction), a
        hard ceiling, and never across directions — the rescue path is
        never wedged by the exclude path."""
        now = time.monotonic()
        if self._pending.get(key, 0.0) > now:
            direction, name = key
            self._commands.report_status(f"{direction.capitalize()} already in flight: {name}")
            return False
        self._pending[key] = now + PENDING_CEILING_SECONDS
        return True

