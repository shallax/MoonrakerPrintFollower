"""Pure console policy: history bounds and input guards.

Deliberately NO command-safety table: the author ruled the console
unrestricted ("writing something in there requires real intent and we
shouldn't baby the user"). The policy owns only what is mechanical —
history length, the empty-input guard — so the controller stays thin
and everything here is unit-testable without Qt.
"""
from __future__ import annotations

from typing import List

# The persisted history ring bound: long enough for a full session of
# tuning commands, short enough that the per-printer config record
# never grows unbounded.
MAX_HISTORY = 200

# Mirror MonitorCommands.MAX_QUEUED_COMMANDS: the console shares the
# one-shot lane with macros and setup scripts.
MAX_PENDING = 16


def normalise_line(text) -> str:
    """The command text or "" when the input is empty/whitespace."""
    return str(text or "").strip()


def trim_history(lines, limit: int = MAX_HISTORY) -> List[str]:
    """The newest `limit` entries of a history list."""
    source = [str(line) for line in lines] if isinstance(lines, (list, tuple)) else []
    return source[-limit:]
