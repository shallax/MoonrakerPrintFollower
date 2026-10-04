#!/usr/bin/env python3
"""The one exclusion schema shared by every audited exclusion table.

Two tables carry justified exclusions — the harness surface map
(tests/harness/scenario_map.py) and the per-file coverage gate
(tools/check_per_file_coverage.py) — and the schema is what keeps them
honest: reason, evidence, date and a re-check trigger, so an entry
cannot be a one-line prose excuse that never expires. The rule lives
here so both tables are held to the same one, judged by the same code.
"""
from __future__ import annotations

import datetime

FIELDS = ("reason", "evidence", "date", "recheck")


def failures(entries, where: str = "") -> list:
    """Every entry's schema violation, as printable strings.

    A missing field, an empty one, or a date that is not a real
    ISO-8601 day (an exclusion's date is when it was granted, and it
    has to be readable a year later).
    """
    problems = []
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            problems.append("%s%s: entry is not a mapping" % (where, name))
            continue
        for field in FIELDS:
            if not str(entry.get(field, "")).strip():
                problems.append("%s%s lacks %r" % (where, name, field))
        try:
            datetime.date.fromisoformat(str(entry.get("date", "")))
        except ValueError:
            problems.append("%s%s: date %r is not an ISO-8601 day" % (where, name, entry.get("date")))
    return problems
