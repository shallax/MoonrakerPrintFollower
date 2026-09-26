"""The printer-state permission policy (4.2.0): ONE pure module
projecting the polled observation into named action permissions, with
the rulings as an explicit table. Pure by contract — no Qt, no
networking, no mutable owners (the architecture review's F10 shape:
pure functions over a frozen record, reason strings as constants)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Observation:
    """The frozen policy input, assembled ONCE in MonitorData from
    everything its consumers read (the round-1 H1 contract).

    connection is tri-state: 'unknown' until this session has
    observed a connection, then 'yes'/'no'. unknown is NOT idle —
    every ruling must distinguish them (F10). assumed_stopped carries
    the e-stop assumption — the ONE case where the plugin must not
    trust the last poll (the client rewrites the emitted state to
    'cancelled'; the table sees the assumption itself, not just the
    rewrite)."""
    active: bool
    connection: str
    state: str
    homed_axes: str
    assumed_stopped: bool
    save_config_pending: bool
    controls_locked: bool
    busy: bool
    # The authoritative paused bit from the pause_resume object; None
    # when the object has not been observed — the rows fall back to
    # the state word with that caveat stated.
    is_paused: Optional[bool] = None
    # The capability signal (4.3.0): True once the printer's observed
    # object list CONTAINS pause_resume, False once the list was
    # observed WITHOUT it, None while the list itself is unseen. On a
    # printer without the module the pause endpoint does not exist —
    # absence must fail closed, never fall through to the state word.
    pause_resume_supported: Optional[bool] = None


# The concise disabled reasons (F10): module-level constants — the
# model's value_property caches on value identity, and a reason
# rebuilt each publish would miss that cache every poll (round-2 A3).
# The prelude below checks in this order, so a disconnected printer
# says "not connected", never a state it cannot attest.
R_UNKNOWN = "Printer state unknown"
R_DISCONNECTED = "Printer not connected"
R_ESTOPPED = "Emergency stop issued"
R_LOCKED = "Controls locked"
R_PRINTING = "A print is running"
R_NOT_PRINTING = "No print is running"
R_NOT_PRINTING_RESTORE = "No print is running"
R_BUSY = "A command is running"
# The toolhead caption's non-denial states: pause-first is a MODE,
# not a refusal — but the caption still names it (the shipped
# sentence), and the paused note preserves the shipped readout.
R_PAUSE_FIRST = "Moves disabled — pause first"
R_PAUSED_NOTE = "Paused — moves run immediately"
# The pause/resume row vocabulary (4.3.0): distinct constants — the
# exclude-flavoured R_NOT_PRINTING detail must never ride a Pause
# button (M6).
R_NOTHING_TO_PAUSE = "Nothing is printing"
R_ALREADY_PAUSED = "Print is already paused"
R_ALREADY_PRINTING = "Print is not paused"
# The capability refusals (4.3.0, the domain re-review): a printer
# without the pause_resume module has no pause endpoint, and a
# CLEAR_PAUSE leaves the state word "paused" while RESUME can never
# succeed — each gets its own words instead of reusing a false one.
R_UNSUPPORTED = "Pause is not supported on this printer"
R_CLEARED_PAUSE = "Paused — this print can't be resumed"
R_NO_PAUSED_PRINT = "No paused print to resume"


# The long tooltip sentences (the UX adjudication: the short form
# rides the row, the full sentence the tooltip — both from the
# policy, the QML builds no sentences).
REASON_DETAIL = {
    R_UNKNOWN: "The printer's state has not been observed yet — check the connection.",
    R_DISCONNECTED: "The printer is not connected — reconnect before using the controls.",
    R_ESTOPPED: "An emergency stop was issued — commands stay refused until the connection is cycled.",
    R_LOCKED: "The controls are locked — unlock them with the padlock.",
    R_PAUSE_FIRST: "Toolhead moves are disabled during a print — pause first.",
    R_PAUSED_NOTE: "Printer is paused — moves run immediately; a print resumes from Klipper's recorded position.",
    R_PRINTING: "A print is running — pause or finish it first.",
    R_BUSY: "A command is running — wait for it to finish.",
    R_NOT_PRINTING: "Nothing to exclude — this fires only while a print runs.",
    R_NOT_PRINTING_RESTORE: "Nothing to restore — this fires only while a print runs.",
    R_NOTHING_TO_PAUSE: "Pause applies to a running print — nothing is printing right now.",
    R_ALREADY_PAUSED: "The print is already paused — the button reads Resume while paused.",
    R_ALREADY_PRINTING: "Resume applies to a paused print — this print is still running.",
    R_UNSUPPORTED: "This printer has no pause_resume module — Klipper has no pause command to run.",
    R_CLEARED_PAUSE: "The pause queue was cleared (CLEAR_PAUSE) — Klipper will refuse RESUME for this print.",
    R_NO_PAUSED_PRINT: "The paused flag is stale — the print state was reset without clearing it.",
}


@dataclass(frozen=True)
class Verdict:
    """One action ruling: a mode and, for a non-allowed mode, the
    concise reason. Modes follow the shipped jog_gate vocabulary:
    'allowed' | 'pause-first' | 'disabled' — pause-first is NOT a
    denial (the pause-first jog path deliberately dispatches work
    that failed the click-time gate, round-2 H2)."""
    mode: str
    reason: str = ""


def _prelude(obs: Observation):
    """The shared fail-closed prelude — the positive allow-list's
    first gate, checked by every action. Returns a reason constant
    or None. Unknown is never idle (F10).

    The e-stop assumption deliberately does NOT block here: the
    shipped ruling releases the print guards immediately (the state
    reads cancelled and jog unlocks for recovery), and the command
    REFUSAL rides the lane's own e-stop lifecycle plus the follow-up
    disconnect — the record CARRIES assumed_stopped for visibility,
    not so every click-time gate changes. The pause/resume rows ARE
    the first consumers of the assumption (4.3.0): their refusal
    WORDS come from the policy, the enforcement stays the lane's."""
    if obs.connection == "unknown": return R_UNKNOWN
    if obs.connection != "yes": return R_DISCONNECTED
    if not obs.active: return R_UNKNOWN
    if obs.controls_locked: return R_LOCKED
    return None


def _print_active(state: str) -> bool:
    return state in {"printing", "paused"}


def can_jog(obs: Observation) -> Verdict:
    """The toolhead block: jog, extrude and the absolute/relative
    switch share this gate — no control distinguishes them today
    (round-1 L1). The state mapping mirrors the shipped jog_gate
    exactly: printing is pause-first; standby/paused/complete/
    cancelled/error allow (error unlocks recovery moves); anything
    else — an unobserved state included — disables. Not-homed is
    ALLOWED (the shipped decision, now an explicit row, round-2
    H3)."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    state = obs.state
    if state == "printing": return Verdict("pause-first", R_PAUSE_FIRST)
    if state in {"standby", "paused", "complete", "cancelled", "error"}:
        return Verdict("allowed", "")
    return Verdict("disabled", R_UNKNOWN)


def section_reason(obs: Observation) -> str:
    """The shared SECTION-level denial (4.2.0, the phase-6 re-review):
    the states that grey WHOLE panes — unknown/disconnected/locked —
    as one short form every section's Status row prefixes. Per-section
    state text (Setup's print-time refusal and the like) stays where
    it is; this closes the fail-closed leak where a locked or dead
    pane read "—". The prelude IS this list — one derivation."""
    return _prelude(obs) or ""


def jog_caption(obs: Observation) -> str:
    """The toolhead section's Status-row caption: the short form for
    every state — the reason when disabled, the pause-first warning,
    the paused note — and '' when there is nothing to say."""
    verdict = can_jog(obs)
    if verdict.mode == "disabled": return verdict.reason
    if verdict.mode == "pause-first": return R_PAUSE_FIRST
    if obs.state == "paused": return R_PAUSED_NOTE
    return ""


def can_set_absolute(obs: Observation) -> Verdict:
    """G90/G91 changes how every subsequent move is interpreted —
    the explicit per-action row (round-2 N3) rules it follows the
    toolhead gate."""
    return can_jog(obs)


def can_restart(obs: Observation) -> Verdict:
    """Firmware/Klipper/host restarts: refused while a print runs,
    otherwise the shipped queueing behaviour stands (busy is NOT a
    click-time gate — one-shots line up deliberately). Unknown
    fails closed (the shipped guard read unknown as idle, round-2
    H5/S1)."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if _print_active(obs.state): return Verdict("disabled", R_PRINTING)
    if not obs.state: return Verdict("disabled", R_UNKNOWN)
    return Verdict("allowed", "")


def can_apply_temperature_preset(obs: Observation) -> Verdict:
    """Temperature presets and the cooldown: refused while the print
    RUNS, allowed while it is PAUSED. A paused print holds no moving
    toolhead, and setting a heater target is what a pause is for; the
    restart row's blanket refusal covered both states because a
    firmware restart is unsafe in either. The rest of the map follows
    can_restart: unknown fails closed, and busy is not a click-time
    gate."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.state == "printing": return Verdict("disabled", R_PRINTING)
    if not obs.state: return Verdict("disabled", R_UNKNOWN)
    return Verdict("allowed", "")


def can_power(obs: Observation, locked_while_printing) -> Verdict:
    """Per power device (the shipped per-row can_toggle, round-2
    A3/F4): a locked device refuses while a print runs; an unlocked
    device stays toggleable. Unknown fails closed (was the masked
    unknown-as-idle leak, round-2 H5)."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if locked_while_printing and _print_active(obs.state):
        return Verdict("disabled", R_PRINTING)
    if not obs.state: return Verdict("disabled", R_UNKNOWN)
    return Verdict("allowed", "")


def can_start_print(obs: Observation) -> Verdict:
    """Print-start click/dispatch time: not-homed AND not-ready
    states stay allowed — the shipped decision, now an explicit row
    (the confirmation warns and the watchdog explains a start that
    never happens). The CONNECTION clause stays: unknown or
    disconnected refuses (the shipped printStartAllowed required
    monitorConnected — the QML term this row replaces). Only an
    OBSERVED running print refuses otherwise."""
    if obs.connection == "unknown": return Verdict("disabled", R_UNKNOWN)
    if obs.connection != "yes": return Verdict("disabled", R_DISCONNECTED)
    if _print_active(obs.state): return Verdict("disabled", R_PRINTING)
    return Verdict("allowed", "")


def can_macro(obs: Observation) -> Verdict:
    """Macros follow the one-shot shape: refused while a print
    runs, queued behind the lane otherwise."""
    return can_restart(obs)


def can_exclude(obs: Observation) -> Verdict:
    """Object exclusion fires only mid-print — the positive shape of
    the shipped gate (a print must be running to exclude)."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.state not in {"printing", "paused"}:
        return Verdict("disabled", R_NOT_PRINTING)
    return Verdict("allowed", "")


def can_restore(obs: Observation) -> Verdict:
    """The restore twin: the same mid-print gate with its own words —
    the exclude-flavoured reason must never ride the other direction's
    refusals. The name-level predicates (is it excluded? is it on the
    plate?) live with the command owner, not here."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.state not in {"printing", "paused"}:
        return Verdict("disabled", R_NOT_PRINTING_RESTORE)
    return Verdict("allowed", "")


def can_z_offset(obs: Observation) -> Verdict:
    """Babystepping mid-print is legitimate Klipper practice — the
    explicit per-action row (round-2 N3) allows it while printing;
    the click gate is the busy flag (the shipped QML term)."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.busy: return Verdict("disabled", R_BUSY)
    if not obs.state: return Verdict("disabled", R_UNKNOWN)
    return Verdict("allowed", "")


def can_pause(obs: Observation) -> Verdict:
    """Pause the live print: allowed only while PRINTING with the
    lane idle — busy is a ROW term (the can_z_offset shape), so the
    reason says what happened instead of a dead button. Never
    pause-first: the mode has no legitimate pause meaning (a
    copy-paste from can_jog would ship "Moves disabled — pause
    first" on a Pause button). The e-stop assumption refuses
    outright — the first consumer of the carried assumption. The
    paused bit is pause_resume.is_paused where observed, the state
    word otherwise (the fallback caveat). A missing observation
    DENIES at the lane with R_UNKNOWN — the pump's fail-closed
    polarity; the upload path's None-allows is a composition-
    boundary artefact, not a precedent. The ToolheadController's
    autonomous pause (a pause-first fulfilment) is a LANE
    behaviour, not a click gate — it deliberately does not route
    through this row."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.assumed_stopped: return Verdict("disabled", R_ESTOPPED)
    if obs.busy: return Verdict("disabled", R_BUSY)
    # The capability gate: on a printer without the pause_resume
    # module the pause endpoint does not exist — absence fails closed
    # (R_UNSUPPORTED), and an unobserved object list fails closed too
    # (R_UNKNOWN) until the capability is known.
    if obs.pause_resume_supported is False: return Verdict("disabled", R_UNSUPPORTED)
    if obs.pause_resume_supported is None: return Verdict("disabled", R_UNKNOWN)
    paused = obs.is_paused
    if paused is None:
        paused = obs.state == "paused"
    if paused or obs.state == "paused": return Verdict("disabled", R_ALREADY_PAUSED)
    if obs.state != "printing": return Verdict("disabled", R_NOTHING_TO_PAUSE)
    return Verdict("allowed", "")


def can_resume(obs: Observation) -> Verdict:
    """Resume the paused print: allowed only while PAUSED with the
    lane idle. The authoritative bit is pause_resume.is_paused —
    the state proxy alone ships a live Resume on a print that can
    never resume (CLEAR_PAUSE leaves the virtual_sdcard halted with
    print_stats "paused" forever and Klipper aborts the RESUME);
    the state word is the fallback with that caveat. Same lane
    polarity and e-stop consumption as can_pause."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.assumed_stopped: return Verdict("disabled", R_ESTOPPED)
    if obs.busy: return Verdict("disabled", R_BUSY)
    # The capability gate, as in can_pause: no module, no resume —
    # and an unobserved list refuses until it is known.
    if obs.pause_resume_supported is False: return Verdict("disabled", R_UNSUPPORTED)
    if obs.pause_resume_supported is None: return Verdict("disabled", R_UNKNOWN)
    paused = obs.is_paused
    if paused is None:
        paused = obs.state == "paused"
    if not paused:
        if obs.state == "paused":
            # CLEAR_PAUSE (or a filament-runout pause mid-window): the
            # state word says paused while the authoritative bit says
            # RESUME can never succeed — say that, not "not paused".
            return Verdict("disabled", R_CLEARED_PAUSE)
        return Verdict("disabled", R_ALREADY_PRINTING)
    # The two-sided gate (the domain round's finding): a stale
    # is_paused survives SDCARD_RESET_FILE — print_stats resets to
    # standby without touching pause_resume — so the bit alone is
    # not enough. A terminal or standby state with the bit up is the
    # stale trap: RESUME would restore a saved toolhead state and
    # MOVE on a print that no longer exists (while the file-manager's
    # print gate stays locked). The lag carve-out stays: "printing"
    # with the bit up is a fresh pause mid-transition, still
    # resumable.
    if obs.state not in ("paused", "printing"):
        return Verdict("disabled", R_NO_PAUSED_PRINT)
    return Verdict("allowed", "")
