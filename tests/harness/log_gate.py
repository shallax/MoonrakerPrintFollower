"""The same plugin-log verdict for native and container Cura journeys."""
from pathlib import Path
from collections import Counter
from datetime import datetime
import json
import math
import re
import sys


def plugin_log_noise(text):
    qml_names = {path.name for path in
                 (Path(__file__).resolve().parents[2] / "mpf").glob("*.qml")}
    noisy = re.compile(r"\b(?:WARNING|ERROR|CRITICAL|TypeError|ReferenceError)\b|(?i:(?:polish|binding) loop)")
    owned = re.compile(r"MoonrakerPrintFollower")
    return [(number, line) for number, line in enumerate(text.splitlines(), 1)
            if noisy.search(line) and (owned.search(line)
                or any(name in line for name in qml_names))]


def completed_fault_messages(spec, steps):
    """Only a fully verified injected-fault scenario can explain its warning."""
    if not steps or not all(step[3] for step in steps):
        return []
    return list(spec.get("expected_log_messages", ()))


def check_logs(paths, evidence=None):
    failed = False
    expected = Counter()
    windows = []
    if evidence is not None and Path(evidence).is_file():
        try:
            record = json.loads(Path(evidence).read_text(encoding="utf-8"))
            messages = record.get("expected_log_messages", [])
            if not isinstance(messages, list) or not all(isinstance(m, str) and m for m in messages):
                raise ValueError("invalid expected fault messages")
            expected.update(messages)
            for window in record.get("expected_log_windows", []):
                start, end = float(window["start"]), float(window["end"])
                if not 0 < start <= end < float("inf"):
                    raise ValueError("invalid fault window")
                # Cura records milliseconds, while the runner records full
                # precision. Include the boundary millisecond: Qt can round
                # a warning at .204754 to .205 without leaving the scenario.
                start = math.floor(start * 1000) / 1000
                end = math.ceil(end * 1000) / 1000
                windows.append((start, end, re.compile(window["pattern"])))
        except (OSError, ValueError, AttributeError, TypeError, KeyError, re.error) as exc:
            print(f"ui_test: invalid log-gate evidence: {exc}", file=sys.stderr)
            return 1
    paths = list(paths)
    if not paths:
        print("ui_test: no Cura logs available for validation", file=sys.stderr)
        return 1
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"ui_test: cannot read Cura log {path}: {exc}", file=sys.stderr)
            failed = True
            continue
        for line, message in plugin_log_noise(text):
            # Exact Logger message and bounded count; never a blanket match
            # for 'refused', an HTTP status, or a scenario's whole log.
            body = message.rsplit("]: ", 1)[-1]
            try:
                stamp = datetime.strptime(message[:23], "%Y-%m-%d %H:%M:%S,%f").timestamp()
            except ValueError:
                stamp = 0
            injected = any(start <= stamp <= end and pattern.fullmatch(body)
                           for start, end, pattern in windows)
            if re.search(r"\bWARNING\b", message) and injected:
                print(f"ui_test: expected fault-window warning: {path}:{line}: {message}")
                continue
            if re.search(r"\bWARNING\b", message) and expected[body] > 0:
                expected[body] -= 1
                print(f"ui_test: expected injected fault: {path}:{line}: {message}")
                continue
            print(f"ui_test: CURA LOG NOISE: {path}:{line}: {message}", file=sys.stderr)
            failed = True
    if not failed:
        print("ui_test: cura.log scan clean")
    return int(failed)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    sys.exit(check_logs(args.paths, args.evidence))
