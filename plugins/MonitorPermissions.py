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
