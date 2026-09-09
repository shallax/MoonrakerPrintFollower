"""Pure Monitor projections/parsers. No Qt, networking, timers or mutable owners."""
from __future__ import annotations
import ast
from collections.abc import Mapping
from datetime import datetime, timedelta
import math
import re


def result(payload):
    """The 'result' field of a Moonraker reply, falling back to the
    payload itself; {} when there is no mapping at all."""
    return payload.get("result", payload) if isinstance(payload, Mapping) else {}


def factor_percent(value) -> str:
    """A speed/flow factor as a percentage, or '—' when the printer did
    not report one (empty snapshot, reconnect, unsupported Klipper)."""
    factor = number(value, None)
    return "—" if factor is None else f"{round(factor * 100)}%"


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError): return default


def friendly(name):
    if name == "extruder": return "Hotend"
    match = re.fullmatch(r"extruder(\d+)", name)
    if match: return f"Hotend {int(match.group(1)) + 1}"
    if name == "heater_bed": return "Bed"
    if name == "fan": return "Part fan"
    return name.split(" ", 1)[-1].replace("_", " ").strip().capitalize()


def chart_label(name):
    """The chart/pane label: friendly(), with the family kept for fan
    objects so a temperature_fan's reading cannot be mistaken for a
    heater of the same suffix."""
    label = friendly(name)
    return label + " (fan)" if object_kind(name) == "fan" else label


# One classification policy for Klipper printer objects. MonitorData uses it
# to decide what to query; the controllers and formatting use it to decide
# what to project. Adding a new object family means editing these tables only.
FAN_OBJECT_PREFIXES = ("fan_generic ", "heater_fan ", "controller_fan ", "temperature_fan ")
LED_OBJECT_PREFIXES = ("neopixel ", "dotstar ", "led ", "pca9533 ", "pca9632 ")
PWM_OBJECT_PREFIXES = ("output_pin ",)
TEMPERATURE_OBJECT_PREFIXES = ("heater_generic ", "temperature_", "bme280 ", "htu21d ", "sht3x ", "lm75 ")
FILAMENT_OBJECT_PREFIXES = ("filament_switch_sensor ", "filament_motion_sensor ")
MCU_OBJECT_PREFIXES = ("mcu ",)

_SYSTEM_OBJECTS = {"heater_bed", "fan", "exclude_object", "system_stats", "webhooks", "mcu",
                   "configfile", "toolhead", "quad_gantry_level", "bed_mesh"}


def object_kind(name):
    """Classify a Klipper printer object into one shared projection kind."""
    lower = str(name or "").lower()
    if lower in _SYSTEM_OBJECTS or re.fullmatch(r"extruder\d*", lower):
        return "system"
    if lower.startswith("gcode_macro "):
        return "macro"
    if lower.startswith(FAN_OBJECT_PREFIXES):
        return "fan"
    if lower.startswith(LED_OBJECT_PREFIXES):
        return "led"
    if lower.startswith(PWM_OBJECT_PREFIXES):
        return "pwm"
    if lower.startswith(TEMPERATURE_OBJECT_PREFIXES):
        return "temperature"
    if lower.startswith(FILAMENT_OBJECT_PREFIXES):
        return "filament"
    if lower.startswith(MCU_OBJECT_PREFIXES):
        return "mcu"
    return ""


def wanted_object(name):
    """Whether MonitorData should query this object's auxiliary state."""
    return object_kind(name) in {"system", "fan", "led", "pwm", "temperature", "filament", "mcu"}


def chart_temperature_objects(auxiliary):
    """Temperature-bearing objects for the chart and the pane's list.

    Heaters and temperature sensors chart unconditionally. Fan objects
    (``temperature_fan``) chart only when no other charted object reads
    the same sensor — equal readings at the same tick — because the
    fan's temperature is worth plotting only when it is the only window
    onto that sensor. The pane's temperature list and the chart share
    this predicate so the two can never disagree.
    """
    readings = {}
    for name, value in auxiliary.items():
        if not isinstance(value, Mapping):
            continue
        kind = object_kind(name)
        if kind == "system":
            lower = str(name).lower()
            if lower != "heater_bed" and not re.fullmatch(r"extruder\d*", lower):
                continue
        elif kind not in ("temperature", "fan"):
            continue
        temperature = number(value.get("temperature"), None)
        if temperature is None:
            continue
        readings[str(name)] = temperature
    for name, reading in list(readings.items()):
        if object_kind(name) != "fan":
            continue
        if any(object_kind(other) != "fan" and abs(reading - other_reading) <= 0.01
               for other, other_reading in readings.items() if other != name):
            del readings[name]
    return readings


def duration(seconds):
    hours, rest = divmod(max(0, int(round(number(seconds)))), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def estimate_remaining(elapsed, progress, estimate, complete):
    elapsed, progress, estimate = max(0, number(elapsed)), max(0, min(1, number(progress))), number(estimate)
    # The by-file blend needs only a little progress signal and must
    # appear promptly: the author expects the unoptimised values as
    # soon as Moonraker reports them on connect, not a minute into the
    # print (the old 60 s / 2% floor left the readout empty at start).
    by_file = max(0, elapsed / progress - elapsed) if progress >= 0.005 and elapsed >= 10 else None
    if estimate > 0:
        remaining = max(0, estimate - elapsed)
        if remaining > 0:
            if by_file is not None and 0.60 * estimate <= elapsed + by_file <= 1.75 * estimate:
                return 0.75 * remaining + 0.25 * by_file
            return remaining
        return by_file if by_file is not None else 0.0
    return by_file if complete else None


def core_values(snapshot, physical, connected):
    stats = snapshot.core.get("print_stats") or {}
    sd = snapshot.core.get("virtual_sdcard") or {}
    move = snapshot.core.get("gcode_move") or {}
    motion = snapshot.core.get("motion_report") or {}
    info = stats.get("info") or {}
    state = str(stats.get("state") or "unknown")
    layer = physical.layer
    current, total = layer.index, layer.total
    layer_text = f"{current + 1} / {total}" if current is not None and total is not None else str(current + 1) if current is not None else f"— / {total}" if total else "—"
    eta, finish, basis = "—", "—", ""
    if state == "paused": eta = "Paused"
    elif state == "printing":
        # The layer-anchored estimate (index timing × observed speed)
        # wins when the coordinator computed one; the plain blend stays
        # the fallback and the UI shows which basis is active.
        remaining = getattr(physical, "layer_eta", None)
        basis = "index"
        if remaining is None:
            remaining = estimate_remaining(stats.get("print_duration"), sd.get("progress"), physical.estimated_time, physical.metadata_complete)
            basis = "blend"
        if remaining is not None:
            eta = duration(remaining)
            finish = (datetime.now().astimezone() + timedelta(seconds=remaining)).strftime("%a %H:%M" if remaining >= 72000 else "%H:%M")
    # How far through the CURRENT layer the file position is, from the
    # index's byte ranges (the nozzle's Z never moves within a layer,
    # so Z cannot express this). -1 without an index: the bar hides.
    layer_progress = getattr(physical, "layer_progress", None)
    if layer_progress is None:
        layer_progress = -1.0
    position = motion.get("live_position") or ()
    # Filament accounting (the author's request): Moonraker reports the
    # used length as a TOP-LEVEL print_stats field; Klipper's info dict
    # only ever holds the layer counters a slicer's SET_PRINT_STATS_INFO
    # wrote (current_layer/total_layer), never filament_used — the old
    # info-dict read matched no real poll. The info dict stays a
    # fallback for robustness. The slicer's total comes from the file
    # metadata in the coordinator snapshot (physical.filament_total);
    # the monitor snapshot is the second home for legacy callers.
    # Remaining is honest "—" without both.
    used_mm = number(stats.get("filament_used"), None)
    if used_mm is None:
        used_mm = number(info.get("filament_used"), None)
    total_mm = getattr(physical, "filament_total", None)
    if total_mm is None:
        total_mm = getattr(snapshot, "filament_total", None)
    filament_used = f"{used_mm / 1000.0:.2f} m" if used_mm is not None else "—"
    filament_remaining = f"{max(0.0, total_mm - used_mm) / 1000.0:.2f} m" \
        if used_mm is not None and total_mm is not None and total_mm >= 0 else "—"
    return {
        "monitorState": state.capitalize() if connected else "Disconnected",
        "monitorFilename": str(stats.get("filename") or ""),
        "monitorProgress": max(0, min(100, round(number(sd.get("progress")) * 100, 2))),
        "monitorLayer": layer_text, "monitorLayerHeight": f"{layer.thickness:.3f} mm" if layer.thickness is not None else "—",
        "monitorLayerSource": getattr(layer, "source", ""),
        "monitorLayerProgress": layer_progress,
        "monitorElapsed": duration(stats.get("print_duration")), "monitorEta": eta, "monitorFinish": finish,
        "monitorEtaBasis": basis,
        "monitorSpeed": factor_percent(move.get("speed_factor")),
        "monitorFlow": factor_percent(move.get("extrude_factor")),
        "monitorPosition": f"X {number(position[0]):.1f}   Y {number(position[1]):.1f}   Z {number(position[2]):.2f}" if len(position) >= 3 else "—",
        "monitorMessage": str(stats.get("message") or ""),
        "filamentUsed": filament_used,
        "filamentRemaining": filament_remaining,
    }


def endstop_values(snapshot, connected=True):
    """The endstop readout: per-axis pin states, or an explicit
    "not homed yet" summary — Klipper's endstop values are meaningless
    before the first homing of a session, and an empty list must not
    read as a bug."""
    states = snapshot.endstops or {}
    items = []
    for axis in sorted(states):
        raw = str(states[axis]).strip()
        if raw:
            items.append({"name": axis.upper(), "state": raw, "triggered": raw.lower() == "triggered"})
    # An empty set means two different things: never homed this
    # session, or disconnected (the snapshot cleared). Only claim
    # homing is missing while actually connected.
    summary = "" if items or not connected else "Not homed yet — home an axis to populate the readout."
    return {"endstopItems": items, "endstopSummary": summary}


def parse_mcu_stats(value):
    source = value if isinstance(value, Mapping) else dict(token.split("=", 1) for token in str(value or "").replace(",", " ").split() if "=" in token)
    return {str(key): number(raw, None) for key, raw in source.items() if number(raw, None) is not None}


def format_bytes(value):
    if value is None or value < 0: return "—"
    if value >= 1000000: return f"{value / 1000000:.2f} MB"
    if value >= 1000: return f"{value / 1000:.1f} kB"
    return f"{value:.0f} B"


def peripheral_values(snapshot):
    temperatures, fans, filament, mcus = [], [], [], []
    cpu, versions = None, []
    chartable = chart_temperature_objects(snapshot.auxiliary)
    for name, value in sorted(snapshot.auxiliary.items()):
        if not isinstance(value, Mapping): continue
        lower, label = name.lower(), chart_label(name)
        if name in chartable:
            temperature = chartable[name]
            target, power = number(value.get("target"), None), number(value.get("power"), None)
            detail = f"{temperature:.1f} °C" + (f"  → {target:.0f} °C" if target is not None else "") + (f"  · {power * 100:.0f}%" if power is not None else "")
            temperatures.append({"name": label, "temperature": temperature, "target": target if target is not None else -1, "power": power if power is not None else -1, "detail": detail})
            if cpu is None and (lower.startswith("temperature_host ") or "cpu" in lower or "rpi" in lower): cpu = temperature
        if "speed" in value and (lower == "fan" or lower.startswith(FAN_OBJECT_PREFIXES)):
            speed = max(0, min(1, number(value.get("speed"))))
            detail = f"{speed * 100:.0f}%" + (f"  · {int(number(value['rpm'])):,} RPM" if value.get("rpm") is not None else "")
            fans.append({"name": label, "speed": speed, "detail": detail})
        if lower.startswith(FILAMENT_OBJECT_PREFIXES):
            detected, enabled = bool(value.get("filament_detected")), bool(value.get("enabled", True))
            filament.append({"name": label, "detected": detected, "enabled": enabled, "state": "Disabled" if not enabled else "Filament detected" if detected else "Runout / not detected"})
        if lower == "mcu" or lower.startswith(MCU_OBJECT_PREFIXES):
            stats = parse_mcu_stats(value.get("last_stats"))
            frequency = stats.get("freq") or number((value.get("mcu_constants") or {}).get("CLOCK_FREQ"), None)
            memory = next((number(value[key], None) for key in ("memory_free", "memavail", "free_memory", "memory") if value.get(key) is not None), None)
            tasks = [f"{label} {stats[key] * 1000000:.1f} µs" for key, label in (("mcu_task_avg", "avg"), ("mcu_task_stddev", "σ")) if key in stats]
            traffic = [label + " " + format_bytes(stats[key]) for key, label in (("bytes_write", "TX"), ("bytes_read", "RX"), ("bytes_retransmit", "retry")) if key in stats]
            version = str(value.get("mcu_version") or "—")
            versions.append(f"{label}: {version}")
            mcus.append({"name": "Main MCU" if lower == "mcu" else label, "version": version,
                "load": f"{max(0, stats['mcu_awake'] * 100):.1f}%" if "mcu_awake" in stats else "—",
                "task": " · ".join(tasks) or "—", "frequency": f"{frequency / 1000000:.3f} MHz" if frequency else "—",
                "memory": format_bytes(memory), "transport": " · ".join(traffic) or "—"})
    exclude = snapshot.auxiliary.get("exclude_object") or {}
    excluded = exclude.get("excluded_objects") or ()
    objects = [{"name": str(item["name"]), "excluded": item["name"] in excluded, "current": item["name"] == exclude.get("current_object")}
        for item in exclude.get("objects", ()) if isinstance(item, Mapping) and item.get("name")]
    system = snapshot.auxiliary.get("system_stats") or {}
    memory = number(system.get("memavail"))
    klippy = str((snapshot.auxiliary.get("webhooks") or {}).get("state") or snapshot.server.get("klippy_state") or "unknown")
    return {"temperatureItems": temperatures, "fanItems": fans, "filamentSensorItems": filament,
        "excludeObjectItems": objects, "mcuItems": mcus, "mcuSummary": " · ".join(versions) or "—",
        "hostLoad": f"{number(system.get('sysload')):.2f}" if system.get("sysload") is not None else "—",
        "memoryAvailable": f"{memory / 1048576:.2f} GB" if memory >= 1048576 else f"{memory / 1024:.0f} MB" if memory > 0 else "—",
        "cpuTemperature": f"{cpu:.1f} °C" if cpu is not None else "—", "klippyState": klippy.capitalize(),
        "moonrakerVersion": str(snapshot.server.get("moonraker_version") or "—"),
        "klipperVersion": str(snapshot.printer.get("software_version") or "—")}


def normalise_mesh(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) < 2: return None
    matrix, columns = [], None
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2: return None
        if columns is None: columns = len(row)
        if len(row) != columns: return None
        converted = [number(value, None) for value in row]
        if any(value is None for value in converted): return None
        matrix.append(converted)
    return matrix


def parse_bed_mesh(status):
    if not isinstance(status, Mapping): return {}
    matrix, source = normalise_mesh(status.get("mesh_matrix")), "mesh_matrix"
    if matrix is None: matrix, source = normalise_mesh(status.get("probed_matrix")), "probed_matrix"
    if matrix is None: return {}
    minimum, maximum = status.get("mesh_min"), status.get("mesh_max")
    if not isinstance(minimum, (list, tuple)) or not isinstance(maximum, (list, tuple)) or min(len(minimum), len(maximum)) < 2: return {}
    bounds = [number(value, None) for value in (minimum[0], minimum[1], maximum[0], maximum[1])]
    if any(value is None for value in bounds) or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]: return {}
    values = [value for row in matrix for value in row]
    return {"profile": str(status.get("profile_name") or "").strip(), "source": source,
        "rows": len(matrix), "columns": len(matrix[0]), "values": values,
        "xMin": bounds[0], "yMin": bounds[1], "xMax": bounds[2], "yMax": bounds[3],
        "minimum": min(values), "maximum": max(values), "range": max(values) - min(values)}


def mesh_profiles(status):
    if not isinstance(status, Mapping): return []
    raw = status.get("profiles") or ()
    if isinstance(raw, Mapping): names = list(raw)
    else: names = [item if isinstance(item, str) else item.get("name") or item.get("profile") for item in raw if isinstance(item, (str, Mapping))]
    names = sorted({str(name).strip() for name in names if name}, key=str.casefold)
    active = str(status.get("profile_name") or "")
    first = active if active in names else "default" if "default" in names else None
    if first: names.remove(first); names.insert(0, first)
    return names


def literal_default(expression):
    if expression is None: return None, False
    text = expression.strip()
    if text.casefold() in {"true", "false"}: return text.casefold() == "true", True
    if text.casefold() in {"none", "null"}: return "", True
    try: return ast.literal_eval(text), True
    except (SyntaxError, ValueError): return None, False


def infer_macro_parameters(gcode):
    found = {}
    pattern = re.compile(r"params(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([^'\"]+)['\"]\s*\])")
    defaults = re.compile(r"\|\s*default\s*\(\s*([^,\)]+)", re.I)
    for line in str(gcode or "").splitlines():
        for match in pattern.finditer(line):
            name = (match.group(1) or match.group(2)).strip().upper()
            tail = line[match.end():]
            default = defaults.search(tail)
            expression = default.group(1).strip() if default else None
            value, literal = literal_default(expression)
            if re.search(r"\|\s*int\b", tail, re.I): kind = "int"
            elif re.search(r"\|\s*float\b", tail, re.I): kind = "float"
            elif isinstance(value, bool): kind = "bool"
            elif isinstance(value, int): kind = "int"
            elif isinstance(value, float): kind = "float"
            elif isinstance(value, str) and value.lower() in {"true", "false"} and re.search(r"\|\s*lower\b", tail, re.I):
                kind, value = "bool", value.lower() == "true"
            else: kind = "string"
            item = {"name": name, "type": kind, "default": str(value) if literal else "", "required": expression is None, "hasDefault": expression is not None}
            if name not in found: found[name] = item
            else:
                old = found[name]
                if old["type"] == "string" and kind != "string": old["type"] = kind
                if not old["hasDefault"] and item["hasDefault"]: old.update(default=item["default"], hasDefault=True, required=False)
    return list(found.values())

