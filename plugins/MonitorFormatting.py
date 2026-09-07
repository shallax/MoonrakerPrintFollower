"""Pure Monitor projections/parsers. No Qt, networking, timers or mutable owners."""
from __future__ import annotations
import ast
from collections.abc import Mapping
from datetime import datetime, timedelta
import math
import re


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


def duration(seconds):
    hours, rest = divmod(max(0, int(round(number(seconds)))), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def estimate_remaining(elapsed, progress, estimate, complete):
    elapsed, progress, estimate = max(0, number(elapsed)), max(0, min(1, number(progress))), number(estimate)
    by_file = max(0, elapsed / progress - elapsed) if progress >= 0.02 and elapsed >= 60 else None
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
    state = str(stats.get("state") or "unknown")
    layer = physical.layer
    current, total = layer.index, layer.total
    layer_text = f"{current + 1} / {total}" if current is not None and total is not None else str(current + 1) if current is not None else f"— / {total}" if total else "—"
    eta, finish = "—", "—"
    if state == "paused": eta = "Paused"
    elif state == "printing":
        remaining = estimate_remaining(stats.get("print_duration"), sd.get("progress"), physical.estimated_time, physical.metadata_complete)
        if remaining is not None:
            eta = duration(remaining)
            finish = (datetime.now().astimezone() + timedelta(seconds=remaining)).strftime("%a %H:%M" if remaining >= 72000 else "%H:%M")
    position = motion.get("live_position") or ()
    return {
        "monitorState": state.capitalize() if connected else "Disconnected",
        "monitorFilename": str(stats.get("filename") or ""),
        "monitorProgress": max(0, min(100, round(number(sd.get("progress")) * 100))),
        "monitorLayer": layer_text, "monitorLayerHeight": f"{layer.thickness:.3f} mm" if layer.thickness is not None else "—",
        "monitorElapsed": duration(stats.get("print_duration")), "monitorEta": eta, "monitorFinish": finish,
        "monitorSpeed": f"{round(number(move.get('speed_factor'), 1) * 100)}%",
        "monitorFlow": f"{round(number(move.get('extrude_factor'), 1) * 100)}%",
        "monitorPosition": f"X {number(position[0]):.1f}   Y {number(position[1]):.1f}   Z {number(position[2]):.2f}" if len(position) >= 3 else "—",
        "monitorMessage": str(stats.get("message") or ""),
    }


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
    for name, value in sorted(snapshot.auxiliary.items()):
        if not isinstance(value, Mapping): continue
        lower, label = name.lower(), friendly(name)
        if "temperature" in value:
            temperature = number(value["temperature"], None)
            if temperature is not None:
                target, power = number(value.get("target"), None), number(value.get("power"), None)
                detail = f"{temperature:.1f} °C" + (f"  → {target:.0f} °C" if target is not None else "") + (f"  · {power * 100:.0f}%" if power is not None else "")
                temperatures.append({"name": label, "temperature": temperature, "target": target if target is not None else -1, "power": power if power is not None else -1, "detail": detail})
                if cpu is None and (lower.startswith("temperature_host ") or "cpu" in lower or "rpi" in lower): cpu = temperature
        if "speed" in value and (lower == "fan" or lower.startswith(("fan_generic ", "heater_fan ", "controller_fan ", "temperature_fan "))):
            speed = max(0, min(1, number(value.get("speed"))))
            detail = f"{speed * 100:.0f}%" + (f"  · {int(number(value['rpm'])):,} RPM" if value.get("rpm") is not None else "")
            fans.append({"name": label, "speed": speed, "detail": detail})
        if lower.startswith(("filament_switch_sensor ", "filament_motion_sensor ")):
            detected, enabled = bool(value.get("filament_detected")), bool(value.get("enabled", True))
            filament.append({"name": label, "detected": detected, "enabled": enabled, "state": "Disabled" if not enabled else "Filament detected" if detected else "Runout / not detected"})
        if lower == "mcu" or lower.startswith("mcu "):
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

