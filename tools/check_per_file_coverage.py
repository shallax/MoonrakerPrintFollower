#!/usr/bin/env python3
"""The per-file coverage gate: every plugin file clears 95% on its own.

The project-total bar alone let whole files hide inside a green
average (the 2026-09-19 tightening), and a json-only gate let a file
no test imports hide from judgement entirely: a coverage json lists
measured files, so a never-imported file was silently skipped. The
gate therefore enumerates the tree and treats a missing entry as a
failure. A file whose coverage provably lives elsewhere — the live
run, an unreachable platform branch — carries a justified entry in
EXCLUSIONS below: reason / evidence / date / recheck, validated by the
shared schema before anything is judged. `coverage json` feeds the
bar; the tree decides what must be judged.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

# The shared schema is a sibling: this script runs both as a file
# (sys.path[0] is tools/) and as a module (the gate's own tests), so
# the directory is named explicitly rather than assumed.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from exclusion_schema import failures as exclusion_failures  # noqa: E402

BAR = 95.0
ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "mpf"

# Per-file excuses: path -> {reason, evidence, date, recheck}. The
# validation below runs before anything is judged, so an excuse
# without its justification fails the gate itself.
EXCLUSIONS = {}


def has_code(path: pathlib.Path) -> bool:
    """True when the file holds anything but comments and docstrings —
    an empty package marker has nothing to measure. An unreadable or
    unparsable file is judged, never skipped: UnicodeDecodeError and
    the NUL-byte ValueError are ValueError subclasses, so the except
    covers binary and malformed input, not just syntax errors."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and not (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return True
    return False


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mpf/coverage.json"
    problems = exclusion_failures(EXCLUSIONS, where="coverage exclusion ")
    if problems:
        for problem in problems:
            print(problem)
        return 1
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    # Coverage writes the platform's separator, and the tree, the
    # exclusion table and the bar's own report are all posix-spelled:
    # on Windows the raw key ("mpf\\Foo.py") matched no path in the
    # tree, so every file was judged missing.
    measured = {key.replace("\\", "/"): value for key, value in data.get("files", {}).items()}
    failures = []
    for source_path in sorted(SOURCE.rglob("*.py")):
        file_path = source_path.relative_to(ROOT).as_posix()
        # Only the package's own entry point is skipped — a subpackage
        # __main__.py carries code and is judged like any other file.
        if file_path == "mpf/__main__.py" or file_path in EXCLUSIONS:
            continue
        entry = measured.get(file_path)
        if entry is None:
            if has_code(source_path):
                failures.append("%s never measured — no test imports it" % file_path)
            continue
        summary = entry.get("summary", {})
        statements = summary.get("num_statements", 0)
        if statements <= 0:
            # Zero measured statements is only legitimate for a file
            # with no code. A file whose statements were all excluded
            # (`# pragma: no cover`) reports 0/100% and would otherwise
            # slip past both this gate and the project total.
            if has_code(source_path):
                failures.append("%s has code but no measured statements — "
                                "coverage excluded it entirely" % file_path)
            continue
        percent = summary.get("percent_covered", 0.0)
        if percent >= BAR:
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
