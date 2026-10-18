# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
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
