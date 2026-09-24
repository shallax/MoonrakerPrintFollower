#!/usr/bin/env python3
"""A unit's verdict as a Markdown step summary.

The Actions UI shows each job as a name and a dot, and the leg's own log
buries the verdict under a hundred lines of progress. This renders the
evidence as one panel at the top of the job page: PASS or FAIL in the
heading, the count, each scenario's own verdict, and the failed steps
with what they were asserting.

Never fails the job — a summary that could not be built says so.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import OrderedDict

STEP = re.compile(r"^([A-Za-z0-9]+)-\d+$")


def _scenario_of(entry):
    # The step's own name carries its scenario (h2-06 belongs to h2). The
    # entry's `scenario` field is the GROUP in some writers, so it cannot
    # be trusted for this.
    match = STEP.match(str(entry.get("name") or ""))
    return match.group(1) if match else str(entry.get("scenario") or "?")


def _clip(text, limit=200):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _expectation(spec):
    if not isinstance(spec, dict):
        return ""
    if spec.get("absent"):
        return "it to be gone"
    for key in ("contains", "value", "expect"):
        if spec.get(key):
            return f"{key} {spec[key]!r}"
    # A wait with no declared value is a wait for the thing to exist.
    return "it to appear" if spec.get("op", "").startswith("wait_") else ""


def _target(spec):
    if not isinstance(spec, dict):
        return ""
    for key in ("objectName", "text", "slot", "prop", "stage", "code"):
        if spec.get(key):
            value = str(spec[key])
            return value if key != "code" else "(inline probe)"
    return ""


def render(run_dir, label=""):
    path = os.path.join(run_dir, "evidence.json")
    if not os.path.exists(path):
        return f"# ⚠️ NO EVIDENCE — {label}\n\n`{path}` was never written.\n"
    try:
        with open(path, encoding="utf-8") as handle:
            evidence = json.load(handle)
    except (OSError, ValueError) as error:
        return f"# ⚠️ UNREADABLE EVIDENCE — {label}\n\n`{error}`\n"

    steps = evidence.get("steps") or []
    failed = [entry for entry in steps if not entry.get("ok")]
    scenarios = OrderedDict()
    for entry in steps:
        scenarios.setdefault(_scenario_of(entry), []).append(entry)

    cura = evidence.get("cura") or "?"
    head = ("PASSED ✅" if not failed
            else f"FAILED ❌ — {len(failed)} of {len(steps)} steps failed")
    lines = [f"# {head}", ""]
    lines.append(f"**{label}** · {len(scenarios)} scenarios · {len(steps)} steps · Cura {cura}")
    lines.append("")
    lines.append("---")
    lines.append("")

    capture = evidence.get("capture") or {}
    if capture.get("mode") == "off":
        reason = str(capture.get("reason") or "").split(". ")[0]
        lines.append(f"> 🎥 **capture off** — the step assertions are judged, the pictures "
                     f"are not. {reason}.")
        lines.append("")

    lines.append("| scenario | verdict | steps |")
    lines.append("|---|---|---|")
    for name, entries in scenarios.items():
        bad = [entry["name"] for entry in entries if not entry.get("ok")]
        verdict = f"**FAILED** ❌ {', '.join(bad)}" if bad else "**PASSED** ✅"
        lines.append(f"| {name} | {verdict} | {len(entries)} |")
    lines.append("")

    if failed:
        lines.append("### Failed steps")
        lines.append("")
        for entry in failed:
            spec = entry.get("spec") or {}
            what = f"`{spec.get('op')}`" if isinstance(spec, dict) else ""
            target = _target(spec)
            lines.append(f"- **{entry.get('name')}** {what} {target}".rstrip())
            expected = _expectation(spec)
            if expected:
                lines.append(f"  - wanted {expected}")
            detail = entry.get("assertion") or entry.get("capture_error")
            if detail:
                lines.append(f"  - saw {_clip(detail)}")
    return "\n".join(lines) + "\n"


def main(argv):
    run_dir = argv[1] if len(argv) > 1 else "."
    label = argv[2] if len(argv) > 2 else os.path.basename(os.path.abspath(run_dir))
    try:
        sys.stdout.write(render(run_dir, label))
    except Exception as error:  # never fail the job for a summary
        sys.stdout.write(f"# ⚠️ SUMMARY UNAVAILABLE — {label}\n\n`{error!r}`\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
