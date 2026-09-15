"""The print-start operation owner (the 4.3.0 extraction).

One owned operation across every start path: pending (the armed
attempt), confirmed live (a filename match with a live state), failed
(the timeout verdict — the POST reply is never the success) and moot
(a terminal state that differs from the armed one). The upload path
reads its own queued verdict from its POST body, so the false-watchdog
case exists only while both paths share the attempt — this owner is
the model's half of that share.

Capabilities only: the file manager's attempt accessor and clear, the
console's note sink and the commands' status sink arrive through the
constructor — the owner never sees the model, the follower or any
shared context.
"""
from __future__ import annotations

import time


class PrintStartOwner:
    """Supervises the armed print-start attempt on every publish tick."""

    FILE_PRINT_START_TIMEOUT_S = 15.0

    def __init__(self, *, file_manager, console, commands):
        self._file_manager = file_manager
        self._console = console
        self._commands = commands
        self._armed_state = ""
        self._start_error = ""

    def arm(self, state: str) -> None:
        # The state the snapshot held at confirm time: the matched
        # branch in tick() holds while it stays unchanged, so a stale
        # terminal state from the SAME file's previous job can never
        # wipe the fresh attempt (the adversarial round's repro).
        self._armed_state = state
        self._start_error = ""

    def tick(self, core) -> None:
        """The awaited print transition (round-2 D4: success is NEVER
        the POST reply). A start that never transitions — OR that
        matches the filename but never makes PROGRESS (Klipper can
        accept the start and freeze before the first motion — the
        author's live report: the UI stayed "printing" on a failed
        start) — explains itself and drops the assumed-active state."""
        attempt = self._file_manager.print_attempt
        if attempt is None:
            return
        stats = core.get("print_stats") or {}
        filename = str(stats.get("filename") or "")
        state = str(stats.get("state") or "")
        if filename == attempt[0]:
            if state in ("printing", "paused"):
                # The job is live with the right file: the success. No
                # progress test — print_duration sits at exactly 0.0
                # and file_position freezes until the first extrusion,
                # so a heat soak alone must never read as a failed
                # start.
                self._file_manager.clear_print_attempt()
                self._armed_state = ""
                self._start_error = ""
            elif state == "error":
                # A cold start raises a transient Klipper error (the
                # extruder refuses to move below min temp) that the
                # print itself outlives once heated: a failure verdict
                # here lies while the job carries on. Hold and remember
                # the words — the timeout below is the only failure
                # verdict, and it keeps the message.
                self._start_error = str(stats.get("message") or "").strip()
            elif state and self._armed_state and state == self._armed_state:
                # Unchanged since the confirm: hold. Klipper never
                # clears the filename, so a re-print of the same file
                # starts from a stale terminal state — that must not
                # wipe the fresh attempt (the adversarial round's
                # repro).
                pass
            elif state and self._armed_state:
                # A terminal state that DIFFERS from the armed one:
                # the printer moved and ended; the verdict is moot. A
                # mismatched filename never clears — a poll in the
                # window between the confirm and the printer's state
                # change must not wipe the attempt.
                self._file_manager.clear_print_attempt()
                self._armed_state = ""
                self._start_error = ""
        if self._file_manager.print_attempt is not None and time.time() - attempt[1] > self.FILE_PRINT_START_TIMEOUT_S:
            if self._start_error:
                self._fail(f"The printer reported an error: {self._start_error}")
            else:
                self._fail("The printer did not begin printing.")

    def _fail(self, reason: str) -> None:
        """The start's failure verdict: the console note and the
        action status. The observed printer state is NEVER rewritten —
        a live print must not read "cancelled", and the pause-first
        jog gate must not lift beside a live nozzle (that assumption
        belongs to the e-stop alone)."""
        self._file_manager.clear_print_attempt()
        self._armed_state = ""
        self._start_error = ""
        self._console.note(f"Print start failed — {reason}")
        self._commands.report_status(f"Print start failed — {reason}")
