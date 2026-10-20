# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Move shared object classification to the printer protocol domain.
# 2026-10-07 [6b3d8e9] Add Preview toolhead controls for 5.3.0
# 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
# 2026-09-07 [29b5fc3] Replace transitional runtime layers with composed components

"""Klipper printer-object families and command writability without presentation."""
import re

# One classification policy for Klipper printer objects. MonitorData uses it
# to decide what to query; the controllers and formatting use it to decide
# what to project. Adding a new object family means editing these tables only.
FAN_OBJECT_PREFIXES = ("fan_generic ", "heater_fan ", "controller_fan ", "temperature_fan ")


def fan_writable(name: str) -> bool:
    """Whether SET_FAN_SPEED actually controls this fan object. Klipper's
    controller_fan, temperature_fan and heater_fan are
    temperature-regulated — the command either never sticks or is not
    registered at all (live reports: controller_fan1 and
    hotend_fan) — so their rows render read-only."""
    lower = str(name).lower()
    if lower == "fan":
        return True
    return lower.startswith(("fan_generic ",))
LED_OBJECT_PREFIXES = ("neopixel ", "dotstar ", "led ", "pca9533 ", "pca9632 ")
PWM_OBJECT_PREFIXES = ("output_pin ",)
TEMPERATURE_OBJECT_PREFIXES = ("heater_generic ", "temperature_", "bme280 ", "htu21d ", "sht3x ", "lm75 ")
FILAMENT_OBJECT_PREFIXES = ("filament_switch_sensor ", "filament_motion_sensor ")
MCU_OBJECT_PREFIXES = ("mcu ",)

# pause_resume rides the CORE lane (4.3.0) — it must NOT appear here:
# the aux merge would overwrite the core value one slow interval later.
_SYSTEM_OBJECTS = {"heater_bed", "fan", "exclude_object", "system_stats", "webhooks", "mcu",
                   "configfile", "toolhead", "quad_gantry_level", "bed_mesh", "display_status"}


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


