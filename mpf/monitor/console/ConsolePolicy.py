# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-13 [a5234d9] docs: drop verbatim quotes and author attributions from the repo text
# 2026-09-10 [2f5ce3b] Round-5 review fixes: verdicts, feed voices, filament totals, store-seed fix and docs
# 2026-09-10 [4990f07] Monitor readouts: layer-anchored ETA, endstops and the first live-pass fixes

"""Pure console policy: history bounds and input guards.

Deliberately NO command-safety table: the console is unrestricted
(typing a command is real intent; it must not be babied). The policy
owns only what is mechanical —
history length, the empty-input guard — so the controller stays thin
and everything here is unit-testable without Qt.
"""
from __future__ import annotations

from typing import List

# The persisted history ring bound: long enough for a full session of
# tuning commands, short enough that the per-printer config record
# never grows unbounded.
MAX_HISTORY = 200

# The persisted console TRANSCRIPT bound (commands + Klipper's output
# from the gcode store): the ruling is the last ~50 lines
# survive across sessions, with restored lines greyed in the pane.
MAX_TRANSCRIPT = 50

# The console's own in-flight bound: its sends ride their own request
# lane, never the shared one-shot lane. The real transport serialises
# one request per channel, so this is headroom, not a queue depth.
MAX_PENDING = 16

# A pasted megabyte "line" must not be sent verbatim — the console's
# semantic is one line, as displayed. 8 KiB is far beyond any real
# gcode command (panel security P3).
MAX_LINE = 8 * 1024


def normalise_line(text) -> str:
    """The command text or "" when the input is empty/whitespace, over
    the length cap, or carries line breaks (a multiline paste must not
    smuggle extra commands past the single-line UI)."""
    line = str(text or "")
    if "\r" in line or "\n" in line:
        line = line.replace("\r", "").replace("\n", "")
    line = line.strip()
    if len(line) > MAX_LINE:
        return ""
    return line


def trim_history(lines, limit: int = MAX_HISTORY) -> List[str]:
    """The newest `limit` entries of a history list."""
    source = [str(line) for line in lines] if isinstance(lines, (list, tuple)) else []
    return source[-limit:]
