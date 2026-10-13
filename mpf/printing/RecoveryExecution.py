"""Sequential recovery handover with no automatic retry after ambiguity."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .RecoveryHandover import (RecoveryRefused, heating_script, prepare_return,
                               verify_command_handlers)


@dataclass(frozen=True)
class RecoveryOutcome:
    phase: str
    detail: str
    terminal: bool = False


class RecoveryExecution:
    """One attempt over injected fresh-query and script-send capabilities.

    Each operation completes before the next starts. A missing answer is an
    ambiguous outcome, never a reason to resend a potentially executed move
    or seek. The owner persists the attempt and presents every phase.
    """

    def __init__(self, query, send, changed, refresh_paused_source=None):
        self._query, self._send, self._changed = query, send, changed
        self._refresh_paused_source = refresh_paused_source
        self._active = False
        self._cancelled = False
        self._attempt = None
        self._return = None
        self._idle = False
        self._primed = False
        self._phase = "idle"

    @property
    def active(self):
        return self._active

    @property
    def phase(self):
        return self._phase

    def start(self, *, source, filename, printed_top_z,
              saved_state="PAUSE_STATE", prepare_macro="", clearance_mm=5.0,
              xy_speed_mm_s=50.0, z_speed_mm_s=5.0,
              setup_macros=(), transparent_macros=(),
              hotend_target=None, bed_target=None,
              prime_enabled=False, prime_xyz=(5.0, 5.0, 5.0),
              prime_amount_mm=20.0, prime_speed_mm_s=2.0):
        if self._active:
            return False
        self._attempt = (source, filename, printed_top_z, saved_state, prepare_macro,
                         clearance_mm, xy_speed_mm_s, z_speed_mm_s,
                         tuple(setup_macros), tuple(transparent_macros),
                         hotend_target, bed_target, prime_enabled, prime_xyz,
                         prime_amount_mm, prime_speed_mm_s)
        self._return = None
        self._active = True
        self._cancelled = False
        self._idle = False
        self._primed = False
        self._advance("checking", self._initial_status)
        return True

    def cancel(self):
        """Stop before another phase; an in-flight script may still finish."""
        if self._active:
            self._cancelled = True

    def abort_uncertain(self, detail):
        """A lost transport generation cannot deliver its pending callback."""
        if self._active:
            self._finish("uncertain", detail)

    def _note(self, phase, detail, terminal=False):
        self._phase = phase
        self._changed(RecoveryOutcome(phase, detail, terminal))

    def _finish(self, phase, detail):
        self._active = False
        self._note(phase, detail, True)

    def _advance(self, phase, callback):
        if self._cancelled:
            self._finish("cancelled", "Recovery preparation stopped; the print remains paused")
            return
        self._note(phase, phase.replace("_", " ").capitalize())
        if self._cancelled:
            self._finish("cancelled", "Recovery preparation stopped; inspect the paused print")
            return
        if not self._query(callback):
            self._finish("uncertain", "Fresh printer status could not be requested; do not retry blindly")

    def _status(self, status, error):
        if error or status is None:
            self._finish("uncertain", "Printer status was lost; check the printer before another recovery attempt")
            return False
        if self._cancelled:
            self._finish("cancelled", "Recovery preparation stopped; inspect the paused print")
            return False
        return True

    def _prepare(self, status, *, check_temperature=True):
        (source, filename, top, saved_state, prepare_macro, clearance, xy_speed,
         z_speed, setup, transparent, nozzle, bed, prime_enabled, prime_xyz,
         prime_amount, prime_speed) = self._attempt
        return prepare_return(status, source, filename=filename, printed_top_z=top,
                              saved_state=saved_state, prepare_macro=prepare_macro,
                              clearance_mm=clearance, xy_speed_mm_s=xy_speed,
                              z_speed_mm_s=z_speed, setup_macros=setup,
                              transparent_macros=transparent, idle=self._idle,
                              check_temperature=check_temperature,
                              hotend_target=nozzle, bed_target=bed,
                              prime_enabled=prime_enabled, prime_xyz=prime_xyz,
                              prime_amount_mm=prime_amount, prime_speed_mm_s=prime_speed)

    def _script(self, phase, script, callback):
        if self._cancelled:
            self._finish("cancelled", "Recovery preparation stopped; inspect the paused print")
            return
        self._note(phase, phase.replace("_", " ").capitalize())
        if self._cancelled:
            self._finish("cancelled", "Recovery preparation stopped; inspect the paused print")
            return
        if not self._send(script, callback):
            self._finish("uncertain", "A printer command could not be submitted; inspect before retrying")

    def _initial_status(self, status, error):
        if not self._status(status, error):
            return
        try:
            verify_command_handlers(status)
        except RecoveryRefused as reason:
            self._finish("refused", str(reason))
            return
        source, filename = self._attempt[:2]
        stats = status.get("print_stats") or {}
        sd = status.get("virtual_sdcard") or {}
        pause = status.get("pause_resume") or {}
        state = stats.get("state")
        if state == "printing":
            if (stats.get("filename") != filename or sd.get("file_size") != source.file_size
                    or sd.get("is_active") is not True or pause.get("is_paused") is not False):
                self._finish("refused", "The active print does not match the selected source")
                return
            self._script("pausing", "PAUSE", self._paused)
            return
        if state != "paused":
            if sd.get("is_active") is not False or pause.get("is_paused") is not False:
                self._finish("refused", "Another print or pause owns the printer")
                return
            if "/" in filename or "\\" in filename or any(c in filename for c in "\r\n\x00"):
                self._finish("refused", "Unloaded recovery currently requires a root G-code file")
                return
            self._idle = True
            self._script("loading_file", f"M23 {filename}", self._file_loaded)
            return
        self._begin_heating(status)

    def _paused(self, error):
        if error:
            self._finish("uncertain", f"PAUSE did not confirm: {error}")
            return
        self._script("waiting_for_motion", "M400", self._pause_settled)

    def _pause_settled(self, error):
        if error:
            self._finish("uncertain", f"Motion did not settle after PAUSE: {error}")
            return
        self._advance("checking_pause", self._paused_status)

    def _paused_status(self, status, error):
        if not self._status(status, error):
            return
        if ((status.get("print_stats") or {}).get("state") != "paused"
                or (status.get("pause_resume") or {}).get("is_paused") is not True):
            self._finish("uncertain", "The print did not confirm PAUSE; inspect it before retrying")
            return
        if self._refresh_paused_source is None:
            self._begin_heating(status)
            return
        self._note("rechecking_source", "Checking G-code through the final paused file position")
        if not self._refresh_paused_source(status, self._refreshed_source):
            self._finish("uncertain", "Paused G-code could not be checked; inspect the print")

    def _refreshed_source(self, source, printed_top_z, error):
        if not self._active:
            return
        if error or source is None:
            self._finish("refused", str(error or "Paused G-code could not be checked"))
            return
        if self._cancelled:
            self._finish("cancelled", "Recovery preparation stopped; inspect the paused print")
            return
        attempt = list(self._attempt)
        original = attempt[0]
        if (source.boundary_offset != original.boundary_offset
                or source.file_size != original.file_size or source.sha256 != original.sha256):
            self._finish("refused", "The paused source differs from the reviewed print")
            return
        attempt[0] = source
        attempt[2] = max(float(printed_top_z), source.printed_top_z)
        self._attempt = tuple(attempt)
        self._advance("checking_after_pause", self._begin_after_pause)

    def _begin_after_pause(self, status, error):
        if self._status(status, error):
            self._begin_heating(status)

    def _file_loaded(self, error):
        if error:
            self._finish("uncertain", f"File selection did not confirm: {error}")
            return
        self._advance("checking_loaded_file", self._loaded_status)

    def _loaded_status(self, status, error):
        if not self._status(status, error):
            return
        self._begin_heating(status)

    def _begin_heating(self, status):
        try:
            self._return = self._prepare(status, check_temperature=False)
            script, _, _ = heating_script(status, self._attempt[0],
                                          hotend_target=self._attempt[10],
                                          bed_target=self._attempt[11])
        except RecoveryRefused as reason:
            self._finish("refused", str(reason))
            return
        self._script("heating_and_waiting", script, self._heated)

    def _heated(self, error):
        if error:
            self._finish("uncertain", f"Heating did not confirm: {error}")
            return
        self._advance("checking_temperature", self._heated_status)

    def _heated_status(self, status, error):
        if not self._status(status, error):
            return
        try:
            self._return = self._prepare(status)
        except RecoveryRefused as reason:
            self._finish("refused", str(reason))
            return
        if self._idle:
            self._begin_return()
            return
        self._script("restore_state", self._return.restore_script,
                     self._state_restored)

    def _state_restored(self, error):
        if error:
            self._finish("uncertain", f"Pause-state restore did not confirm: {error}")
            return
        self._advance("checking_restored_state", self._restored_status)

    def _restored_status(self, status, error):
        if not self._status(status, error):
            return
        try:
            self._return = self._prepare(status)
        except RecoveryRefused as reason:
            self._finish("refused", str(reason))
            return
        self._begin_return()

    def _begin_return(self):
        if self._return.prime_script and not self._primed:
            self._script("priming_nozzle", self._return.prime_script, self._prime_done)
        else:
            self._script("returning", self._return.return_script, self._returned)

    def _prime_done(self, error):
        if error:
            self._finish("uncertain", f"Nozzle prime did not confirm: {error}")
            return
        self._advance("checking_prime", self._prime_status)

    def _prime_status(self, status, error):
        if not self._status(status, error):
            return
        try:
            position = status["gcode_move"]["gcode_position"]
            if any(abs(float(position[i]) - self._return.prime_target[i]) > 0.05
                   for i in range(3)):
                raise RecoveryRefused("Nozzle prime position did not confirm")
            self._return = self._prepare(status)
        except (RecoveryRefused, KeyError, TypeError, ValueError) as reason:
            self._finish("refused", str(reason))
            return
        self._primed = True
        self._begin_return()

    def _returned(self, error):
        if error:
            self._finish("uncertain", f"Return movement did not confirm: {error}")
            return
        self._advance("checking_return", self._return_status)

    def _return_status(self, status, error):
        if not self._status(status, error):
            return
        source = self._attempt[0]
        try:
            self._prepare(status)
            position = status["gcode_move"]["gcode_position"]
            if any(abs(float(position[i]) - self._return.target[i]) > 0.05 for i in range(3)):
                raise RecoveryRefused("The nozzle did not arrive at the selected point")
        except (RecoveryRefused, KeyError, TypeError, ValueError) as reason:
            self._finish("refused", str(reason))
            return
        self._script("seeking", f"M26 S{source.boundary_offset}", self._seeked)

    def _seeked(self, error):
        if error:
            self._finish("uncertain", f"File seek did not confirm: {error}")
            return
        self._advance("checking_seek", self._seek_status)

    def _seek_status(self, status, error):
        if not self._status(status, error):
            return
        source = self._attempt[0]
        try:
            self._prepare(status)
            if status["virtual_sdcard"]["file_position"] != source.boundary_offset:
                raise RecoveryRefused("Klipper did not confirm the selected file offset")
        except (RecoveryRefused, KeyError, TypeError) as reason:
            self._finish("refused", str(reason))
            return
        self._script("restoring_modal_state", self._return.modal_script, self._modal_restored)

    def _modal_restored(self, error):
        if error:
            self._finish("uncertain", f"Modal state did not confirm: {error}")
            return
        self._advance("checking_modal_state", self._modal_status)

    def _modal_status(self, status, error):
        if not self._status(status, error):
            return
        source = self._attempt[0]
        try:
            self._prepare(status)
            move = status["gcode_move"]
            if (move.get("absolute_coordinates") is not source.state.absolute_xyz
                    or move.get("absolute_extrude") is not source.state.absolute_e):
                raise RecoveryRefused("Klipper did not confirm the selected coordinate modes")
            position = move["gcode_position"]
            if (len(position) < 4 or not math.isclose(float(position[3]), source.state.e,
                                                      abs_tol=0.0001)
                    or not math.isclose(float(move["speed_factor"]),
                                        source.state.speed_percent / 100, abs_tol=0.0001)
                    or not math.isclose(float(move["extrude_factor"]),
                                        source.state.flow_percent / 100, abs_tol=0.0001)):
                raise RecoveryRefused("Klipper did not confirm the selected E or flow state")
            if source.state.acceleration is not None and not math.isclose(
                    float(status["toolhead"]["max_accel"]), source.state.acceleration,
                    abs_tol=0.1):
                raise RecoveryRefused("Klipper did not confirm the selected acceleration")
        except (RecoveryRefused, KeyError, IndexError, TypeError, ValueError) as reason:
            self._finish("refused", str(reason))
            return
        if self._return.finish_script:
            self._script("finishing_pause", self._return.finish_script, self._pause_finished)
        else:
            self._script("resuming", "M24" if self._idle else "CLEAR_PAUSE\nM24", self._resumed)

    def _pause_finished(self, error):
        if error:
            self._finish("uncertain", f"Pause cleanup did not confirm: {error}")
            return
        self._advance("checking_pause_cleanup", self._cleanup_status)

    def _cleanup_status(self, status, error):
        if not self._status(status, error):
            return
        try:
            self._prepare(status)
        except RecoveryRefused as reason:
            self._finish("refused", str(reason))
            return
        self._script("resuming", "CLEAR_PAUSE\nM24", self._resumed)

    def _resumed(self, error):
        if error:
            self._finish("uncertain", f"The recovery handover is ambiguous: {error}")
            return
        self._advance("checking_resume", self._resume_status)

    def _resume_status(self, status, error):
        if not self._status(status, error):
            return
        source, filename = self._attempt[:2]
        stats = status.get("print_stats") or {}
        sd = status.get("virtual_sdcard") or {}
        if (stats.get("state") != "printing" or stats.get("filename") != filename
                or sd.get("file_size") != source.file_size
                or sd.get("file_position", -1) < source.boundary_offset):
            self._finish("uncertain", "Printer did not confirm recovery printing; inspect it before retrying")
            return
        self._finish("complete", "Recovery is printing from the selected point")
