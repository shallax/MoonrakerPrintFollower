# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Validate settings drafts independently of Cura and return field-specific refusals.

"""Strict interactive validation; persisted configuration retains tolerant loading."""

from dataclasses import asdict, dataclass
import re
from .ConfigConstraints import RECOVERY_NUMBER_LIMITS
from .PrinterConfig import PrinterConfig, normalise_url
from ..printing.FollowMode import FollowMode


@dataclass(frozen=True)
class ConfigDiscovery:
    sensors: tuple = ()
    saved_states: tuple = ()
    macros: tuple = ()
    detection_global_enabled: bool = False
    detection_camera_ready: bool = False


@dataclass
class ConfigValidationError(ValueError):
    field: str
    message: str

    def __str__(self):
        return self.message


def _connection_fields(raw, current, url_is_usable):
    try:
        interval = int(float(str(raw.get("poll_interval_ms", "")).strip()))
        aux_interval = int(float(str(raw.get("aux_interval_ms", "")).strip()))
        console_interval = int(float(str(raw.get("console_interval_ms", "")).strip()))
        tolerance = float(str(raw.get("z_tolerance", "")).strip())
        retry_interval = float(str(raw.get("ready_retry_interval_s", "")).strip())
        raw_cache = str(raw.get("cache_max_mb") or "").strip()
        cache_max = int(raw_cache) if raw_cache else getattr(current, "cache_max_mb", 2048)
    except ValueError as exc:
        raise ConfigValidationError("settings", "unparsable field (%s)" % (exc,)) from None
    url = normalise_url(str(raw.get("url", "")))
    enabled = bool(raw.get("enabled", False))
    if not 250 <= interval <= 3600000:
        raise ConfigValidationError("poll_interval_ms", "poll interval out of range")
    if not 250 <= aux_interval <= 60000 or not 250 <= console_interval <= 60000:
        raise ConfigValidationError("aux_interval_ms", "aux/console interval out of range")
    if not 0.005 <= tolerance <= 0.25:
        raise ConfigValidationError("z_tolerance", "z tolerance out of range")
    if not 0.1 <= retry_interval <= 60.0:
        raise ConfigValidationError("ready_retry_interval_s", "retry interval out of range")
    if not 16 <= cache_max <= 4096:
        raise ConfigValidationError("cache_max_mb", "cache size out of range")
    if enabled and (not url_is_usable(url)):
        raise ConfigValidationError("url", "the URL is not usable")
    trans_input = str(raw.get("filename_translate_input") or "")
    trans_output = str(raw.get("filename_translate_output") or "")
    if len(trans_input) != len(trans_output):
        raise ConfigValidationError("filename_translate_input", "translate input/output lengths differ")
    mode = str(raw.get("follow_mode") or FollowMode.EXACT.value)
    if mode not in {item.value for item in FollowMode}:
        mode = FollowMode.EXACT.value
    feed_mode = str(raw.get("feed_mode") or "").strip().lower()
    if feed_mode not in ("websocket", "http"):
        feed_mode = str(getattr(current.feed_mode, "value", current.feed_mode))
    return {
        "enabled": enabled,
        "url": url,
        "api_key": str(raw.get("api_key") or "").strip(),
        "poll_interval_ms": interval,
        "aux_interval_ms": aux_interval,
        "console_interval_ms": console_interval,
        "cache_max_mb": cache_max,
        "cache_max_mb_explicit": bool(raw_cache) or current.cache_max_mb_explicit,
        "moonraker_layer_is_one_based": bool(raw.get("moonraker_layer_is_one_based", True)),
        "auto_preview": bool(raw.get("auto_preview", False)),
        "z_fallback": bool(raw.get("z_fallback", True)),
        "z_tolerance": tolerance,
        "path_follow": bool(raw.get("path_follow", True)),
        "path_smoothing": bool(raw.get("path_smoothing", True)),
        "eta_learn": bool(raw.get("eta_learn", False)),
        "show_toolhead_indicator": bool(raw.get("show_toolhead_indicator", True)),
        "trace_layer": bool(raw.get("trace_layer", False)),
        "seek_trace": bool(raw.get("seek_trace", False)),
        "trace_http": bool(raw.get("trace_http", False)),
        "memory_diagnostics_log": bool(raw.get("memory_diagnostics_log", False)),
        "memory_diagnostics_trace": bool(raw.get("memory_diagnostics_trace", False)),
        "camera_disabled": bool(raw.get("camera_disabled", False)),
        "software_follower_renderer": bool(raw.get("software_follower_renderer", False)),
        "feed_mode": feed_mode,
        "follow_mode": mode,
        "frontend_url": str(raw.get("frontend_url") or "").strip(),
        "output_format": str(raw.get("output_format") or "gcode").lower(),
        "upload_dialog": bool(raw.get("upload_dialog", True)),
        "upload_path": str(raw.get("upload_path") or "").strip().strip("/"),
        "upload_start_print": bool(raw.get("upload_start_print", False)),
        "upload_remember_state": bool(raw.get("upload_remember_state", False)),
        "upload_autohide_message": bool(raw.get("upload_autohide_message", False)),
        "power_devices": str(raw.get("power_devices") or "").strip(),
        "ready_retry_interval_s": retry_interval,
        "filename_translate_input": trans_input,
        "filename_translate_output": trans_output,
        "filename_translate_remove": str(raw.get("filename_translate_remove") or ""),
    }


def _recovery_fields(raw, current, discovery):
    recovery_sensors = raw.get("recovery_sensor_names", current.recovery_sensor_names)
    if not isinstance(recovery_sensors, list) or any(
        (
            not isinstance(name, str) or not name.startswith(("filament_switch_sensor ", "filament_motion_sensor "))
            for name in recovery_sensors
        )
    ):
        raise ConfigValidationError("recovery_sensor_names", "invalid recovery sensor selection")
    permitted_sensors = set(discovery.sensors) | set(current.recovery_sensor_names)
    if not set(recovery_sensors) <= permitted_sensors:
        raise ConfigValidationError("recovery_sensor_names", "recovery sensor was not discovered")
    recovery_state = str(raw.get("recovery_saved_state", current.recovery_saved_state)).strip().upper()
    recovery_macro = str(raw.get("recovery_prepare_macro", current.recovery_prepare_macro)).strip().upper()
    nozzle_parameter = str(raw.get("recovery_nozzle_parameter", current.recovery_nozzle_parameter)).strip().upper()
    bed_parameter = str(raw.get("recovery_bed_parameter", current.recovery_bed_parameter)).strip().upper()
    if not re.fullmatch("[A-Z][A-Z0-9_]{0,63}", nozzle_parameter) or not re.fullmatch(
        "[A-Z][A-Z0-9_]{0,63}", bed_parameter
    ):
        raise ConfigValidationError("recovery_nozzle_parameter", "Invalid setting value")
    setup_macros = raw.get("recovery_setup_macros", current.recovery_setup_macros)
    transparent_macros = raw.get("recovery_transparent_macros", current.recovery_transparent_macros)
    permitted_macros = (
        set(discovery.macros) | set(current.recovery_setup_macros) | set(current.recovery_transparent_macros)
    )
    if (
        not isinstance(setup_macros, list)
        or not isinstance(transparent_macros, list)
        or len(setup_macros) > 32
        or (len(transparent_macros) > 32)
        or any(
            (not isinstance(name, str) or name not in permitted_macros for name in setup_macros + transparent_macros)
        )
        or set(setup_macros) & set(transparent_macros)
    ):
        raise ConfigValidationError("recovery_setup_macros", "invalid source macro selection")
    if (
        not re.fullmatch("[A-Z][A-Z0-9_]{0,63}", recovery_state)
        or recovery_state not in set(discovery.saved_states) | {current.recovery_saved_state}
        or (
            recovery_macro
            and (
                not re.fullmatch("[A-Z][A-Z0-9_]{0,63}", recovery_macro)
                or recovery_macro not in set(discovery.macros) | {current.recovery_prepare_macro}
            )
        )
    ):
        raise ConfigValidationError("recovery_saved_state", "recovery macro or state was not discovered")
    recovery_numbers = {}
    recovery_mode = raw.get("recovery_lead_in_mode", current.recovery_lead_in_mode)
    if recovery_mode not in {"mm", "moves"}:
        raise ConfigValidationError("recovery_lead_in_mode", "Invalid setting value")
    try:
        recovery_moves = int(raw.get("recovery_lead_in_moves", current.recovery_lead_in_moves))
    except (TypeError, ValueError):
        raise ConfigValidationError("settings", "Invalid setting value") from None
    if not 1 <= recovery_moves <= 100:
        raise ConfigValidationError("recovery_lead_in_moves", "Invalid setting value")
    if type(raw.get("recovery_auto_pause", current.recovery_auto_pause)) is not bool:
        raise ConfigValidationError("settings", "recovery auto-pause must be a checkbox value")
    if type(raw.get("recovery_prime_enabled", current.recovery_prime_enabled)) is not bool:
        raise ConfigValidationError("settings", "prime enable must be a checkbox value")
    for key, minimum, maximum in RECOVERY_NUMBER_LIMITS:
        try:
            value = float(raw.get(key, getattr(current, key)))
        except (TypeError, ValueError):
            raise ConfigValidationError(key, "Invalid setting value") from None
        if not minimum <= value <= maximum:
            raise ConfigValidationError(key, "%s out of range" % (key,))
        recovery_numbers[key] = value
    try:
        recovery_timeout = int(raw.get("recovery_command_timeout_s", current.recovery_command_timeout_s))
    except (TypeError, ValueError):
        raise ConfigValidationError("settings", "Invalid setting value") from None
    if not 10 <= recovery_timeout <= 600:
        raise ConfigValidationError("recovery_command_timeout_s", "recovery command timeout out of range")
    try:
        recovery_heating_timeout = int(raw.get("recovery_heating_timeout_s", current.recovery_heating_timeout_s))
    except (TypeError, ValueError, OverflowError):
        raise ConfigValidationError("settings", "Invalid setting value") from None
    if not 60 <= recovery_heating_timeout <= 3600:
        raise ConfigValidationError("recovery_heating_timeout_s", "recovery heating timeout out of range")
    try:
        recovery_status_timeout = int(raw.get("recovery_status_timeout_s", current.recovery_status_timeout_s))
    except (TypeError, ValueError, OverflowError):
        raise ConfigValidationError("settings", "Invalid setting value") from None
    if not 3 <= recovery_status_timeout <= 60:
        raise ConfigValidationError("recovery_status_timeout_s", "recovery status timeout out of range")
    return {
        "recovery_sensor_names": list(dict.fromkeys(recovery_sensors)),
        "recovery_auto_pause": raw.get("recovery_auto_pause", current.recovery_auto_pause),
        "recovery_prime_enabled": raw.get("recovery_prime_enabled", current.recovery_prime_enabled),
        "recovery_saved_state": recovery_state,
        "recovery_prepare_macro": recovery_macro,
        "recovery_setup_macros": list(dict.fromkeys(setup_macros)),
        "recovery_transparent_macros": list(dict.fromkeys(transparent_macros)),
        "recovery_nozzle_parameter": nozzle_parameter,
        "recovery_bed_parameter": bed_parameter,
        "recovery_lead_in_mode": recovery_mode,
        "recovery_lead_in_moves": recovery_moves,
        **recovery_numbers,
        "recovery_command_timeout_s": recovery_timeout,
        "recovery_heating_timeout_s": recovery_heating_timeout,
        "recovery_status_timeout_s": recovery_status_timeout,
    }


def _detection_fields(raw, current, discovery):
    warning_threshold = raw.get("detection_warning_threshold", current.detection_warning_threshold)
    failure_threshold = raw.get("detection_failure_threshold", current.detection_failure_threshold)
    if (
        type(warning_threshold) is not int
        or type(failure_threshold) is not int
        or (not 0 <= warning_threshold < failure_threshold <= 100)
    ):
        raise ConfigValidationError("detection_warning_threshold", "detection thresholds must be ordered percentages")
    detection_enabled = raw.get("detection_enabled", current.detection_enabled)
    if not isinstance(detection_enabled, bool):
        raise ConfigValidationError("detection_enabled", "detection_enabled must be a checkbox value")
    if (
        detection_enabled
        and (not current.detection_enabled)
        and (
            not discovery.detection_global_enabled
            or not discovery.detection_camera_ready
            or bool(raw.get("camera_disabled", current.camera_disabled))
        )
    ):
        detection_refusal = (
            "Enable local detection in Settings and select a working camera before enabling this printer."
        )
        raise ConfigValidationError("detection_enabled", "%s" % (detection_refusal,))
    return {
        "detection_enabled": detection_enabled,
        "detection_warning_threshold": warning_threshold,
        "detection_failure_threshold": failure_threshold,
    }


def validate_config(raw, current, discovery, *, url_is_usable):
    """Validate a draft without persisting settings, publishing assets or touching UI.

    URL syntax is an injected, deterministic value-parser capability so the host
    can retain its established URL grammar without importing Qt into the policy.
    """
    if not isinstance(raw, dict):
        raise ConfigValidationError("settings", "Settings must be an object")
    data = asdict(current)
    data.update(_connection_fields(raw, current, url_is_usable))
    data.update(_recovery_fields(raw, current, discovery))
    data.update(_detection_fields(raw, current, discovery))
    return PrinterConfig.from_dict(data)
