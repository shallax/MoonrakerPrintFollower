"""The printer-state permission policy (4.2.0): ONE pure module
projecting the polled observation into named action permissions, with
the rulings as an explicit table. Pure by contract — no Qt, no
networking, no mutable owners (the architecture review's F10 shape:
pure functions over a frozen record, reason strings as constants)."""
from __future__ import annotations
from dataclasses import dataclass


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
R_BUSY = "A command is running"
# The toolhead caption's non-denial states: pause-first is a MODE,
# not a refusal — but the caption still names it (the shipped
# sentence), and the paused note preserves the shipped readout.
R_PAUSE_FIRST = "Moves disabled — pause first"
R_PAUSED_NOTE = "Paused — moves run immediately"


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
    disconnect — the record CARRIES assumed_stopped for visibility
    (the phase-6 re-review's D9: no ruling consumes it today, the
    lane enforces the refusal), not so the click-time gates
    change."""
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


def can_z_offset(obs: Observation) -> Verdict:
    """Babystepping mid-print is legitimate Klipper practice — the
    explicit per-action row (round-2 N3) allows it while printing;
    the click gate is the busy flag (the shipped QML term)."""
    blocked = _prelude(obs)
    if blocked: return Verdict("disabled", blocked)
    if obs.busy: return Verdict("disabled", R_BUSY)
    if not obs.state: return Verdict("disabled", R_UNKNOWN)
    return Verdict("allowed", "")
