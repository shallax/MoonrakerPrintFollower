"""Qt owner for reviewed recovery plans and a single guarded handover."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import math
import os

from PyQt6.QtCore import QObject, pyqtSignal

from ..moonraker.MoonrakerProtocol import parse_file_identity
from ..printing.RecoveryExecution import RecoveryExecution
from .RecoveryPlanning import RecoveryPlanError, scan_source


@dataclass(frozen=True)
class RecoveryContext:
    job: tuple
    filename: str
    path: str
    identity: object
    boundary: object
    printed_top_z: float
    saved_state: str
    prepare_macro: str
    clearance_mm: float
    xy_speed_mm_s: float
    z_speed_mm_s: float
    command_timeout_s: int
    observed_offset: int = 0
    setup_macros: tuple[str, ...] = ()
    transparent_macros: tuple[str, ...] = ()
    machine_id: str = ""
    history_job_id: str = ""
    status_timeout_s: int = 10
    prime_enabled: bool = False
    prime_xyz: tuple[float, float, float] = (5.0, 5.0, 5.0)
    prime_amount_mm: float = 20.0
    prime_speed_mm_s: float = 2.0
    nozzle_parameter: str = "EXTRUDER"
    bed_parameter: str = "BED"
    unloaded: bool = False
    heating_timeout_s: int = 1800


def _source_still_matches(path, source):
    try:
        before = os.stat(path)
        if before.st_size != source.file_size:
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.stat(path)
        return (before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
                and digest.hexdigest() == source.sha256)
    except OSError:
        return False


class RecoveryController(QObject):
    changed = pyqtSignal()
    _planned = pyqtSignal(int, object, object)
    _verified = pyqtSignal(int, bool)
    _replanned = pyqtSignal(int, object, object, object)

    OBJECTS = {name: None for name in (
        "print_stats", "virtual_sdcard", "pause_resume", "toolhead", "gcode_move",
        "configfile", "extruder", "heater_bed")}

    def __init__(self, client, context, parent=None, record=None):
        super().__init__(parent)
        self._client = client
        self._context = context
        self._record = record
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MoonrakerRecovery")
        self._generation = 0
        self._closed = False
        self._planned_context = None
        self._source = None
        self._execution = None
        self._manual_targets = (None, None)
        self._manual_printed_top_z = None
        self._paused_replan_callback = None
        self._phase = "idle"
        self._detail = "Select a point in the follower to plan recovery."
        self._planned.connect(self._plan_done)
        self._verified.connect(self._verification_done)
        self._replanned.connect(self._replan_done)
        client.sessionInvalidated.connect(self.invalidate)

    @property
    def state(self):
        source = self._source
        context = self._planned_context
        return {"phase": self._phase, "detail": self._detail,
                "ready": self._phase == "ready" and source is not None,
                "active": self._execution is not None and self._execution.active,
                "offset": source.boundary_offset if source is not None else -1,
                "filename": context.filename if context is not None else "",
                "unloaded": context.unloaded if context is not None else False,
                "e": source.state.e if source is not None else 0.0,
                "absoluteE": source.state.absolute_e if source is not None else False,
                "hotendTarget": (source.state.hotend_target
                                 if source is not None and source.state.hotend_target is not None
                                 and source.state.hotend_target > 0 else None),
                "bedTarget": source.state.bed_target if source is not None else None,
                "suggestedTopZ": (max(context.printed_top_z, source.printed_top_z)
                                  if source is not None and context is not None else 0.0),
                "primeEnabled": context.prime_enabled if context is not None else False,
                "primeX": context.prime_xyz[0] if context is not None else 0.0,
                "primeY": context.prime_xyz[1] if context is not None else 0.0,
                "primeZ": context.prime_xyz[2] if context is not None else 0.0,
                "primeAmount": context.prime_amount_mm if context is not None else 0.0,
                "primeSpeed": context.prime_speed_mm_s if context is not None else 0.0,
                "x": source.state.position[0] if source is not None else 0.0,
                "y": source.state.position[1] if source is not None else 0.0,
                "z": source.state.position[2] if source is not None else 0.0}

    def _set(self, phase, detail):
        self._phase, self._detail = phase, detail
        if self._record is not None and self._planned_context is not None:
            self._record(self._planned_context, phase, detail)
        self.changed.emit()

    def plan(self):
        if self._closed or self._execution is not None and self._execution.active:
            return False
        context = self._context()
        if context is None:
            self._set("refused", "Select an exact indexed point in a downloaded print.")
            return False
        self._generation += 1
        generation = self._generation
        self._planned_context = context
        self._source = None
        self._set("planning", "Checking the G-code and reconstructing extrusion state…")

        def work():
            try:
                source = scan_source(context.path, context.boundary,
                                     context.observed_offset or None,
                                     setup_macros=context.setup_macros,
                                     transparent_macros=context.transparent_macros,
                                     nozzle_parameter=context.nozzle_parameter,
                                     bed_parameter=context.bed_parameter)
                self._planned.emit(generation, source, None)
            except (RecoveryPlanError, OSError, ValueError) as error:
                self._planned.emit(generation, None, str(error))

        self._executor.submit(work)
        return True

    def _plan_done(self, generation, source, error):
        if self._closed or generation != self._generation:
            return
        if source is None:
            self._source = None
            self._set("refused", str(error or "The G-code could not be planned."))
            return
        self._source = source
        self._set("ready", "G-code state checked. Review the shown XYZ and E before starting recovery.")

    def start(self, temperatures=None):
        if self._closed or self._phase != "ready" or self._source is None:
            return False
        context = self._context()
        if not self._same_context(context):
            self._source = None
            self._set("refused", "The selected print, point or recovery settings changed. Prepare again.")
            return False
        supplied = temperatures if isinstance(temperatures, dict) else {}
        try:
            nozzle = (None if self._source.state.hotend_target is not None
                      and self._source.state.hotend_target > 0 else
                      float(supplied["hotend"]))
            bed = (None if self._source.state.bed_target is not None else
                   float(supplied["bed"]))
            if ((nozzle is not None and (not math.isfinite(nozzle) or nozzle <= 0))
                    or (bed is not None and (not math.isfinite(bed) or bed < 0))):
                raise ValueError("Invalid recovery temperature")
        except (KeyError, TypeError, ValueError, OverflowError):
            self._set("refused", "Enter the nozzle and bed targets missing from the G-code.")
            return False
        if context.unloaded:
            try:
                top = float(supplied["printedTopZ"])
                if not math.isfinite(top) or top < self._source.state.position[2]:
                    raise ValueError(top)
            except (KeyError, TypeError, ValueError, OverflowError):
                self._set("refused", "Enter the existing object's highest Z at or above the restart layer.")
                return False
            self._manual_printed_top_z = top
        else:
            self._manual_printed_top_z = None
        self._manual_targets = (nozzle, bed)
        self._generation += 1
        generation = self._generation
        source = self._source
        self._set("verifying_source", "Checking that the downloaded G-code is unchanged…")
        self._executor.submit(lambda: self._verified.emit(generation,
                              _source_still_matches(context.path, source)))
        return True

    def _verification_done(self, generation, matches):
        if self._closed or generation != self._generation:
            return
        if not matches:
            self._set("refused", "The downloaded G-code changed. Prepare a fresh recovery plan.")
            return
        context = self._planned_context
        if not self._same_context(self._context()):
            self._set("refused", "The selected print or point changed before handover.")
            return
        self._set("checking_remote_file", "Checking the printer's file metadata…")
        from urllib.parse import quote
        path = "server/files/metadata?filename=" + quote(context.filename, safe="/")

        def checked(payload, error):
            if self._closed or generation != self._generation:
                return
            identity = parse_file_identity(context.filename, payload or {}, 0)
            if (error or not self._same_context(self._context()) or context.identity.modified <= 0
                    or identity.stable_key() != context.identity.stable_key()):
                self._set("refused", "The printer's G-code identity changed or cannot be verified.")
                return
            self._execute(generation)

        if not self._client.transport.send_json("recovery", "file-identity", "GET", path,
                                                checked, category="metadata"):
            self._set("refused", "Fresh printer file metadata could not be requested.")

    def _same_context(self, current):
        planned = self._planned_context
        if current is None or planned is None:
            return False
        return replace(current, observed_offset=planned.observed_offset,
                       printed_top_z=planned.printed_top_z) == planned

    def _replan_done(self, generation, source, top, error):
        callback = self._paused_replan_callback
        self._paused_replan_callback = None
        if (callback is not None and not self._closed and generation == self._generation):
            callback(source, top, error)

    def _execute(self, generation):
        context, source = self._planned_context, self._source

        def replan_paused(status, callback):
            stats = status.get("print_stats") or {}
            sd = status.get("virtual_sdcard") or {}
            try:
                position = int(sd.get("file_position"))
                if (stats.get("filename") != context.filename
                        or sd.get("file_size") != source.file_size
                        or sd.get("is_active") is not False
                        or position < source.boundary_offset or position > source.file_size):
                    return False
            except (TypeError, ValueError):
                return False
            self._paused_replan_callback = callback

            def work():
                try:
                    updated = scan_source(context.path, context.boundary, position,
                                          setup_macros=context.setup_macros,
                                          transparent_macros=context.transparent_macros,
                                          nozzle_parameter=context.nozzle_parameter,
                                          bed_parameter=context.bed_parameter)
                    self._replanned.emit(generation, updated,
                                         max(context.printed_top_z, updated.printed_top_z), None)
                except (RecoveryPlanError, OSError, ValueError) as error:
                    self._replanned.emit(generation, None, None, str(error))
            self._executor.submit(work)
            return True

        def query(callback):
            def received(payload, error):
                if self._closed or generation != self._generation:
                    return
                status = ((payload or {}).get("result") or {}).get("status")
                callback(status, error)
            return self._client.transport.send_json(
                "recovery", "status", "POST", "printer/objects/query", received,
                body={"objects": self.OBJECTS}, timeout_ms=context.status_timeout_s * 1000,
                category="command")

        def send(script, callback):
            def received(_payload, error):
                if self._closed or generation != self._generation:
                    return
                callback(error)
            return self._client.transport.send_json(
                "recovery", "script", "POST", "printer/gcode/script", received,
                body={"script": script},
                timeout_ms=(context.heating_timeout_s if script.startswith(("M140 S", "M104 S"))
                            else context.command_timeout_s) * 1000,
                category="command")

        def changed(outcome):
            self._set(outcome.phase, outcome.detail)

        self._execution = RecoveryExecution(query, send, changed,
                                            refresh_paused_source=replan_paused)
        self._execution.start(source=source, filename=context.filename,
                              printed_top_z=max(context.printed_top_z, source.printed_top_z,
                                                self._manual_printed_top_z or 0.0),
                              saved_state=context.saved_state,
                              prepare_macro=context.prepare_macro,
                              setup_macros=context.setup_macros,
                              transparent_macros=context.transparent_macros,
                              clearance_mm=context.clearance_mm,
                              xy_speed_mm_s=context.xy_speed_mm_s,
                              z_speed_mm_s=context.z_speed_mm_s,
                              hotend_target=self._manual_targets[0],
                              bed_target=self._manual_targets[1],
                              prime_enabled=context.prime_enabled,
                              prime_xyz=context.prime_xyz,
                              prime_amount_mm=context.prime_amount_mm,
                              prime_speed_mm_s=context.prime_speed_mm_s)

    def cancel(self):
        if self._phase == "planning":
            self._generation += 1
            self._set("cancelled", "Recovery planning cancelled.")
        elif self._execution is not None and self._execution.active:
            self._execution.cancel()
            self._set("cancelling", "Stopping before the next recovery stage…")

    def invalidate(self, *_args):
        if self._closed:
            return
        was_active = self._phase not in {"idle", "refused", "complete", "cancelled", "uncertain"}
        executing = self._execution is not None and self._execution.active
        self._generation += 1
        self._source = None
        if was_active:
            detail = "Connection changed during recovery. Inspect the printer before trying again."
            if executing:
                self._execution.abort_uncertain(detail)
            else:
                self._set("refused", "The print connection changed. Prepare a fresh recovery plan.")

    def close(self):
        self._closed = True
        self._generation += 1
        self._executor.shutdown(wait=False, cancel_futures=True)
