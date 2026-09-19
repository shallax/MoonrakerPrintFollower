#!/usr/bin/env python3
"""The per-file coverage gate: every plugin file clears 95% on its own.

The project-total bar alone let whole files hide inside a green
average (the 2026-09-19 tightening). Files whose coverage provably
lives elsewhere — harness scenario legs, the live run, unreachable
branches — carry a justified exclusion in the scenario map's schema:
reason / evidence / date / recheck. `coverage json` feeds this; the
run_tests.sh coverage leg generates the json and calls this check
after the project-total bar.
"""
from __future__ import annotations

import json
import sys

BAR = 95.0

EXCLUSIONS = {
    "plugins/__init__.py": {
        "reason": "the registration guard's ImportError branch is unreachable in the pinned container (PyQt6 is present)",
        "evidence": "the container's PyQt6 pins; the guard exists for host-suite imports",
        "date": "2026-09-19",
        "recheck": "the container's Qt stack changes",
    },
}


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mpf/coverage.json"
    for name, entry in EXCLUSIONS.items():
        for field in ("reason", "evidence", "date", "recheck"):
            if not str(entry.get(field, "")).strip():
                print("coverage exclusion %s lacks %r" % (name, field))
                return 1
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    failures = []
    for file_path, file_data in sorted(data.get("files", {}).items()):
        if not file_path.startswith("plugins/") or file_path.endswith("__main__.py"):
            continue
        summary = file_data.get("summary", {})
        statements = summary.get("num_statements", 0)
        if statements <= 0:
            continue
        percent = summary.get("percent_covered", 0.0)
        if percent >= BAR or file_path in EXCLUSIONS:
            continue
        failures.append("%s %.1f%% (%d statements)" % (file_path, percent, statements))
    if failures:
        print("per-file coverage failures (below %.1f%%):" % BAR)
        for failure in failures:
            print("  " + failure)
        return 1
    print("per-file coverage gate passed (bar %.1f%%)" % BAR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
