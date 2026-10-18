"""Conservative Klipper return plan; command transport lives with the owner."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math
import re

from ..geometry.PhysicalMotion import UnsafeMotion, command_number, envelope, finite


class RecoveryRefused(ValueError):
    pass


COMMAND_OVERRIDES = frozenset({
    "clear_pause", "m23", "m24", "m26", "m104", "m109", "m140", "m190",
    "restore_gcode_state", "m82", "m83", "g92", "m220", "m221",
    "m204", "m106", "m107", "m400", "set_filament_sensor", "set_idle_timeout",
    "set_gcode_offset", "g4",
})


def verify_command_handlers(status):
    config = _mapping(_mapping(status, "configfile"), "config")
    names = {str(name).casefold() for name in config}
    if any(f"gcode_macro {name}" in names for name in COMMAND_OVERRIDES):
        raise RecoveryRefused("A command override prevents a verified recovery handover")


def reviewed_pause_state(status, configured="PAUSE_STATE"):
    """Resolve the stock default against an unambiguous pre-park PAUSE save.

    Never select a parked state or expand a templated name. Explicit user
    choices remain subject to the full handover validation at dispatch.
    """
    verify_command_handlers(status)
    config = _mapping(_mapping(status, "configfile"), "config")
    macro = next((value for name, value in config.items()
                  if str(name).casefold() == "gcode_macro pause"), None)
    if macro is None or configured != "PAUSE_STATE":
        return configured
    body = str(macro.get("gcode") or "") if isinstance(macro, Mapping) else ""
    commands = [line.split(";", 1)[0].strip() for line in body.splitlines()]
    saves = []
    found_pause = False
    for line in commands:
        if re.match(r"(?:BASE_PAUSE|PAUSE_BASE)\b", line, re.I):
            found_pause = True
            break
        match = re.fullmatch(r"SAVE_GCODE_STATE\s+NAME=([A-Z][A-Z0-9_]*)", line, re.I)
        if match:
            saves.append(match.group(1).upper())
    choices = set(saves)
    if found_pause and len(choices) == 1:
        return choices.pop()
    raise RecoveryRefused("Cannot identify the pre-park PAUSE state. Select the state saved before parking in Recovery settings, then check the G-code again.")


@dataclass(frozen=True)
class ReturnPlan:
    clearance_z: float
    target: tuple[float, float, float]
    prime_target: tuple[float, float, float] | None
    z_reference_script: str
    prime_script: str
    return_script: str
    modal_script: str
    restore_script: str
    finish_script: str
    resume_script: str


def heating_script(status, source_plan, *, hotend_target=None, bed_target=None):
    """Set and wait for the reviewed targets without starting the SD file."""
    config = _mapping(_mapping(status, "configfile"), "config")
    verify_command_handlers(status)
    extruder = _mapping(config, "extruder")
    nozzle = source_plan.state.hotend_target if hotend_target is None else hotend_target
    bed = source_plan.state.bed_target if bed_target is None else bed_target
    try:
        nozzle = finite(nozzle)
        minimum = finite(extruder.get("min_extrude_temp", 170))
        maximum = finite(extruder.get("max_temp"))
        if nozzle < minimum or nozzle >= maximum:
            raise UnsafeMotion("Nozzle target must be within the extruder's printable range")
        if bed is not None:
            bed = finite(bed)
            if bed < 0:
                raise UnsafeMotion("Invalid bed target")
            if bed > 0 and bed >= finite(_mapping(config, "heater_bed").get("max_temp")):
                raise UnsafeMotion("Bed target exceeds its configured maximum")
    except UnsafeMotion as error:
        raise RecoveryRefused(str(error)) from error
    lines = []
    if bed is not None and bed > 0:
        lines.append(f"M140 S{command_number(bed)}")
    lines.append(f"M104 S{command_number(nozzle)}")
    if bed is not None and bed > 0:
        lines.append(f"M190 S{command_number(bed)}")
    lines.append(f"M109 S{command_number(nozzle)}")
    return "\n".join(lines), nozzle, bed


def _mapping(parent, name):
    value = parent.get(name) if isinstance(parent, Mapping) else None
    if not isinstance(value, Mapping):
        raise RecoveryRefused(f"Fresh {name} status is unavailable")
    return value


def _temperature(status, name, target):
    heater = _mapping(status, name)
    try:
        measured = finite(heater.get("temperature"))
        setpoint = finite(heater.get("target"))
    except UnsafeMotion as error:
        raise RecoveryRefused(f"{name} temperature is unavailable") from error
    if target is not None and (abs(setpoint - target) > 1 or measured < target - 5):
        raise RecoveryRefused(f"Restore and stabilise the {name} at {target:g} °C")
    return measured, setpoint


def prepare_return(status, source_plan, *, filename, printed_top_z, clearance_mm=5.0,
                   xy_speed_mm_s=50.0, z_speed_mm_s=5.0,
                   saved_state="PAUSE_STATE", prepare_macro="",
                   setup_macros=(), transparent_macros=(), idle=False,
                   hotend_target=None, bed_target=None, check_temperature=True,
                   prime_enabled=False, prime_xyz=(0.0, 250.0, 100.0),
                   prime_amount_mm=50.0, prime_speed_mm_s=5.0, prime_dwell_s=5.0,
                   z_reference_mm=0.0):
    """Check a fresh paused snapshot and render the staged return.

    The source planner already refused unmodelled G-code. This policy supports
    stock Klipper PAUSE/RESUME on one extruder, a retained XYZ home and a
    layer-by-layer print with a known printed top. The caller re-queries after
    restoring PAUSE_STATE and again before seeking or starting the SD worker.
    """
    if not isinstance(status, Mapping):
        raise RecoveryRefused("Fresh printer status is unavailable")
    stats = _mapping(status, "print_stats")
    sd = _mapping(status, "virtual_sdcard")
    pause = _mapping(status, "pause_resume")
    if idle:
        if str(stats.get("state") or "") in {"printing", "paused"} or pause.get("is_paused") is not False:
            raise RecoveryRefused("The unloaded recovery requires an idle printer")
    elif str(stats.get("state") or "") != "paused" or pause.get("is_paused") is not True:
        raise RecoveryRefused("The original print must be paused through Klipper PAUSE")
    if str(stats.get("filename") or "") != filename or sd.get("file_size") != source_plan.file_size:
        raise RecoveryRefused("The paused file no longer matches the recovery source")
    if sd.get("is_active") is not False:
        raise RecoveryRefused("The virtual SD worker has not stopped")
    config = _mapping(_mapping(status, "configfile"), "config")
    names = {str(name).casefold() for name in config}
    for macro in (*setup_macros, *transparent_macros):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(macro)) \
                or f"gcode_macro {str(macro).casefold()}" not in names:
            raise RecoveryRefused("A configured source macro is unavailable on this printer")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", saved_state):
        raise RecoveryRefused("Invalid saved pause state name")
    if prepare_macro and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", prepare_macro):
        raise RecoveryRefused("Invalid preparation macro name")
    pause_macro = config.get("gcode_macro PAUSE") or config.get("gcode_macro pause")
    if not idle and pause_macro is None and "gcode_macro resume" in names:
        raise RecoveryRefused("A custom RESUME macro without a matching PAUSE profile is unsupported")
    if not idle and pause_macro is not None:
        body = str(_mapping(config, "gcode_macro PAUSE" if "gcode_macro PAUSE" in config
                            else "gcode_macro pause").get("gcode") or "")
        if not re.search(r"\bSAVE_GCODE_STATE\s+NAME=" + re.escape(saved_state) + r"\b", body, re.I):
            raise RecoveryRefused("The PAUSE macro does not save the configured recovery state")
        if not re.search(r"\b(?:BASE_PAUSE|PAUSE_BASE)\b", body, re.I):
            raise RecoveryRefused("The PAUSE macro does not invoke Klipper's pause handler")
    elif not idle and saved_state != "PAUSE_STATE":
        raise RecoveryRefused("The configured saved state requires a PAUSE macro")
    if prepare_macro and not idle:
        matches = [value for name, value in config.items()
                   if str(name).casefold() == f"gcode_macro {prepare_macro.casefold()}"]
        if len(matches) != 1 or not isinstance(matches[0], Mapping):
            raise RecoveryRefused("The configured preparation macro is unavailable")
        commands = [line.split(";", 1)[0].strip().upper()
                    for line in str(matches[0].get("gcode") or "").splitlines()]
        commands = [line for line in commands if line and not line.startswith("#")]
        if commands != [f"RESTORE_GCODE_STATE NAME={saved_state} MOVE=0"]:
            raise RecoveryRefused("Preparation macro must only restore the selected state without movement")
    if any(name.startswith("extruder") and name != "extruder" for name in names):
        raise RecoveryRefused("Multiple extruders are not supported by this recovery plan")
    if "extruder" not in names:
        raise RecoveryRefused("Extruder configuration is unavailable")
    extruder = _mapping(config, "extruder")
    try:
        bounds = envelope(status)
        reference = finite(z_reference_mm)
        if reference < 0 or reference > 0 and not idle:
            raise UnsafeMotion("A Z reference adjustment is only available for unloaded recovery")
        z_reference_script = ""
        if idle:
            # G92 changes the current G-code frame without changing Klipper's
            # homing origin. A later G28 restores the printer's usual frame.
            # G92 operates in gcode_move's planned frame. Toolhead Z
            # includes bed-mesh compensation and cannot define this origin.
            planned_z = bounds.gcode[2] + bounds.base[2]
            if abs(bounds.base[2] + reference) > 0.001:
                z_reference_script = f"G92 Z{command_number(planned_z + reference)}"
            bounds = replace(bounds,
                             gcode=(bounds.gcode[0], bounds.gcode[1], planned_z + reference),
                             base=(bounds.base[0], bounds.base[1], -reference))
        top = finite(printed_top_z)
        clearance = finite(clearance_mm)
        xy_speed = finite(xy_speed_mm_s)
        z_speed = finite(z_speed_mm_s)
        if top < 0 or clearance <= 0 or xy_speed <= 0 or z_speed <= 0:
            raise UnsafeMotion("Invalid printed height or clearance")
        target = tuple(finite(value) for value in source_plan.state.position)
        if idle and target[2] - reference < bounds.minimum[2] - 0.001:
            raise UnsafeMotion("Restart Z is below the homed object-top reference")
        if prime_enabled and (not isinstance(prime_xyz, (tuple, list)) or len(prime_xyz) != 3):
            raise UnsafeMotion("Prime XYZ must contain three coordinates")
        prime_target = tuple(finite(value) for value in prime_xyz) if prime_enabled else None
        maximum_z = bounds.maximum[2] - bounds.base[2]
        if idle and reference > 0:
            maximum_z = min(maximum_z, bounds.maximum[2])
        if prime_target is not None:
            prime_z = prime_target[2] if prime_target[2] > target[2] else target[2] + 5.0
            prime_target = (*prime_target[:2], min(prime_z, maximum_z))
        if prime_target is not None and idle and prime_target[2] - reference < bounds.minimum[2] - 0.001:
            raise UnsafeMotion("Prime Z is below the homed object-top reference")
        # Recovery's selected layer is the physical restart reference. A
        # parked nozzle or dry-running file height must not inflate its lift.
        clear_z = min(target[2] + clearance, maximum_z)
        prime_clear_z = min(max(bounds.gcode[2], target[2], prime_target[2]), maximum_z) if prime_target else clear_z
        if idle and reference > 0 and clear_z > bounds.maximum[2]:
            raise UnsafeMotion("Clearance exceeds the original file Z travel after object-top homing")
        bounds.check((bounds.gcode[0], bounds.gcode[1], clear_z))
        bounds.check((target[0], target[1], clear_z))
        bounds.check(target)
        if prime_target is not None:
            amount = finite(prime_amount_mm)
            speed = finite(prime_speed_mm_s)
            dwell = finite(prime_dwell_s)
            if not 0 <= dwell <= 300:
                raise UnsafeMotion("Prime dwell must be between 0 and 300 seconds")
            max_amount = finite(extruder.get("max_extrude_only_distance", 50.0))
            max_speed = extruder.get("max_extrude_only_velocity")
            if amount <= 0 or amount > min(200.0, max_amount) or speed <= 0 or speed > 20:
                raise UnsafeMotion("Prime extrusion exceeds the configured amount or speed limit")
            if max_speed is not None and speed > finite(max_speed):
                raise UnsafeMotion("Prime speed exceeds the extruder-only velocity limit")
            bounds.check((bounds.gcode[0], bounds.gcode[1], prime_clear_z))
            bounds.check((prime_target[0], prime_target[1], prime_clear_z))
            bounds.check((prime_target[0], prime_target[1], clear_z))
            bounds.check(prime_target)
    except UnsafeMotion as error:
        raise RecoveryRefused(str(error)) from error
    _, nozzle, bed = heating_script(status, source_plan,
                                     hotend_target=hotend_target, bed_target=bed_target)
    measured, setpoint = _temperature(status, "extruder", nozzle) if check_temperature else (None, None)
    try:
        min_extrude = finite(_mapping(config, "extruder").get("min_extrude_temp", 170))
    except UnsafeMotion as error:
        raise RecoveryRefused("Extruder minimum temperature is unavailable") from error
    if check_temperature and (measured < min_extrude or setpoint < min_extrude):
        raise RecoveryRefused("Heat the nozzle above its minimum extrusion temperature")
    if check_temperature and bed is not None and bed > 0:
        _temperature(status, "heater_bed", bed)
    def n(value):
        return command_number(value)
    prime_script = ""
    if prime_target is not None:
        prime_script = "\n".join((
            "M400", "G90", "M83", "M220 S100", "M221 S100",
            f"G1 Z{n(prime_clear_z)} F{n(z_speed * 120)}",
            f"G1 X{n(prime_target[0])} Y{n(prime_target[1])} F{n(xy_speed * 60)}",
            f"G1 Z{n(prime_target[2])} F{n(z_speed * 60)}",
            f"G1 E{n(amount)} F{n(speed * 60)}", "M400", f"G4 P{n(dwell * 1000)}",
        ))
    return_script = "\n".join((
        "M400", "G90", "M220 S100",
        f"G1 Z{n(clear_z)} F{n(z_speed * 120)}",
        f"G1 X{n(target[0])} Y{n(target[1])} F{n(xy_speed * 60)}",
        "M400",
    ))
    state = source_plan.state
    modal = [f"M220 S{n(state.speed_percent)}", f"M221 S{n(state.flow_percent)}",
             f"G92 E{n(state.e)}"]
    if state.acceleration is not None:
        modal.append(f"M204 S{n(state.acceleration)}")
    if state.fan_speed is not None:
        modal.append(f"M106 S{state.fan_speed}" if state.fan_speed else "M107")
    modal.extend(("G90" if state.absolute_xyz else "G91",
                  "M83" if state.force_relative_e else "M82"))
    if state.feedrate is not None:
        modal.append(f"G1 F{n(state.feedrate)}")
    modal.append("M400")
    if not math.isfinite(clear_z):
        raise RecoveryRefused("Invalid clearance")
    restore_script = "" if idle else prepare_macro or f"RESTORE_GCODE_STATE NAME={saved_state} MOVE=0"
    finish = []
    if pause_macro is not None and not idle:
        # Only undo literal sensor disables. Macro expressions cannot be
        # safely expanded client-side; those need an explicit printer macro.
        disables = re.findall(r"\bSET_FILAMENT_SENSOR\s+SENSOR=([A-Za-z0-9_]+)\s+ENABLE=0\b", body, re.I)
        if len(disables) != len(re.findall(r"\bSET_FILAMENT_SENSOR\b", body, re.I)):
            raise RecoveryRefused("The PAUSE macro's sensor changes need a verified cleanup macro")
        for sensor in dict.fromkeys(disables):
            finish.append(f"SET_FILAMENT_SENSOR SENSOR={sensor} ENABLE=1")
        if "SET_IDLE_TIMEOUT" in body.upper():
            timeout = _mapping(config, "idle_timeout").get("timeout")
            try:
                timeout = int(finite(timeout))
            except UnsafeMotion as error:
                raise RecoveryRefused("The normal idle timeout is unavailable") from error
            if timeout <= 0:
                raise RecoveryRefused("The normal idle timeout is invalid")
            finish.append(f"SET_IDLE_TIMEOUT TIMEOUT={timeout}")
    # Keep network acknowledgements above the object. The final descent and
    # SD start share one script, without a dwell or a round trip at the surface.
    resume = ["G90", "M220 S100", f"G1 Z{n(target[2])} F{n(z_speed * 60)}",
              *modal[:-1]]
    if not idle:
        resume.append("CLEAR_PAUSE")
    resume.append("M24")
    return ReturnPlan(clear_z, target, prime_target, z_reference_script, prime_script,
                      return_script, "\n".join(modal),
                      restore_script, "\n".join(finish), "\n".join(resume))
