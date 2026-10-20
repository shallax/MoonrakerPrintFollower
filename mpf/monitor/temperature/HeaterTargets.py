# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-18 — 6.0.0: Discover writable heater commands independently of slider limits.
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation

"""Writable heater inventory and limits from Moonraker's Klipper config."""
import math
import re
from collections.abc import Mapping

from ..MonitorFormatting import friendly


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def target_command(name):
    """Only writable Klipper object names can become target commands."""
    if not isinstance(name, str):
        return None
    if re.fullmatch(r"extruder\d*|heater_bed|heater_generic [A-Za-z0-9_.-]+", name):
        return f"SET_HEATER_TEMPERATURE HEATER={name.split()[-1]}"
    if re.fullmatch(r"temperature_fan [A-Za-z0-9_.-]+", name):
        return f"SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN={name.split()[-1]}"
    return None


def heater_names(auxiliary):
    """Cooldown needs live writable targets, even without configured limits."""
    return [name for name, status in auxiliary.items()
            if target_command(name) and isinstance(status, Mapping) and "target" in status]


def heater_targets(auxiliary):
    configfile = auxiliary.get("configfile") or {}
    if not isinstance(configfile, Mapping):
        return []
    config = configfile.get("config") or {}
    settings = configfile.get("settings") or {}
    if not isinstance(config, Mapping) or not isinstance(settings, Mapping):
        return []
    rows = []
    for name, status in sorted(auxiliary.items()):
        if not re.fullmatch(r"extruder\d*|heater_bed|heater_generic [A-Za-z0-9_.-]+", name):
            continue
        if not isinstance(status, Mapping) or "target" not in status:
            continue
        section = settings.get(name, config.get(name, {}))
        if not isinstance(section, Mapping):
            continue
        low, high = finite(section.get("min_temp")), finite(section.get("max_temp"))
        target = finite(status.get("target"))
        if low is None or high is None or high <= max(0, low) or target is None:
            continue
        minimum, maximum = max(1, math.ceil(low)), math.floor(high)
        if minimum > maximum:
            continue
        rows.append(dict(object=name, name=friendly(name), minimum=minimum,
                         maximum=maximum, target=target))
    return rows
