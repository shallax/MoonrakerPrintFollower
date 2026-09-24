#!/usr/bin/env python3
"""Run the suite per file and write JUnit-style XML for Codecov's
test-results analytics — the CI leg uploads the file through
codecov/test-results-action.

ONE PROCESS PER FILE, which is the unit the parallel gate uses and the
only one the suite actually supports. A single-process discovery
imports every module before running any test, and `test_leak_probe_
coverage` creates a module-level ``QCoreApplication`` at import — so the
real-engine file's setup finds the process already owned and raises
SkipTest, and 23 of its classes silently skipped in this report while
the gate ran them. A report of a different suite than the gate's is
worse than no report."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

# The suite files import plugins.* — discovery alone puts tests/ on
# the path, not the repo root (the parallel legs get the root from
# run_tests.sh's environment; this standalone pass sets it itself).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DISCOVER_DIR = "tests"
PATTERN = "test_*.py"
OUTPUT = "tests.junit.xml"


class _JUnitResult(unittest.TestResult):
    """Collects one record per test: classname, name, verdict and the
    failure/error/skip payload."""

    def __init__(self):
        super().__init__()
        self.cases = []
        self.started = None
        self.current = None

    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()
        self.current = {
            "classname": test.__class__.__module__ + "." + test.__class__.__qualname__,
            "name": str(test),
            "time": 0.0,
            "kind": None,  # None | "failure" | "error" | "skipped"
            "message": "",
        }

    def _record(self, test):
        if self.current is None:
            # A class-level skip/error arrives without a startTest.
            self.started = time.monotonic()
            self.current = {
                "classname": test.__class__.__module__ + "." + test.__class__.__qualname__,
                "name": str(test),
                "time": 0.0,
                "kind": None,
                "message": "",
            }

    def _finish(self, test, kind, message):
        self._record(test)
        self.current["time"] = time.monotonic() - self.started
        self.current["kind"] = kind
        self.current["message"] = message

    def addSuccess(self, test):
        super().addSuccess(test)
        self._finish(test, None, "")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._finish(test, "failure", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._finish(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._finish(test, "skipped", str(reason))

    def stopTest(self, test):
        super().stopTest(test)
        self._record(test)
        if self.current["kind"] is None:
            self.current["time"] = time.monotonic() - self.started
        self.cases.append(self.current)
        self.current = None


def _write(cases, started, elapsed):
    suite = ET.Element("testsuite", {
        "name": "MoonrakerPrintFollower",
        "tests": str(len(cases)),
        "failures": str(sum(1 for case in cases if case["kind"] == "failure")),
        "errors": str(sum(1 for case in cases if case["kind"] == "error")),
        "skipped": str(sum(1 for case in cases if case["kind"] == "skipped")),
        "time": "%.3f" % elapsed,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
    })
    for case in cases:
        testcase = ET.SubElement(suite, "testcase", {
            "classname": case["classname"],
            "name": case["name"],
            "time": "%.3f" % case["time"],
        })
        if case["kind"] in ("failure", "error"):
            ET.SubElement(testcase, case["kind"], {
                "message": case["message"].splitlines()[0] if case["message"] else "",
            }).text = case["message"]
        elif case["kind"] == "skipped":
            ET.SubElement(testcase, "skipped", {"message": case["message"]})
    tree = ET.ElementTree(ET.Element("testsuites"))
    tree.getroot().append(suite)
    ET.indent(tree, space="  ")
    tree.write(OUTPUT, encoding="utf-8", xml_declaration=True)


def _run_one(pattern, out_path):
    """One file, in this process, dumped as JSON to *out_path* (the
    child mode).

    A FILE, not stdout: the suite's own tests print to stdout (`publish
    -> picture: 266 ms`, the reverse-scrub diffs), so a JSON appended
    after them does not parse and the whole file's records are lost —
    which is how the first version of this reported six "unparsable
    report" errors and 3103 tests instead of 3829."""
    import json
    suite = unittest.defaultTestLoader.discover(DISCOVER_DIR, pattern=pattern)
    result = _JUnitResult()
    suite.run(result)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result.cases, handle)
    return 0


def _discover_files():
    return sorted(name for name in os.listdir(DISCOVER_DIR)
                  if name.startswith("test_") and name.endswith(".py"))


def _run_all():
    """Every file in its own process, the records merged in order."""
    import json
    import subprocess
    cases = []
    for name in _discover_files():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            out_path = handle.name
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--one", name, out_path],
                capture_output=True, text=True)
            with open(out_path, encoding="utf-8") as handle:
                cases.extend(json.load(handle))
        except (OSError, ValueError):
            # The file died before its result could be dumped: record it
            # rather than lose it from the report.
            cases.append({"classname": name, "name": name, "time": 0.0,
                          "kind": "error",
                          "message": ((proc.stderr if proc else "") or "")[-2000:]
                                     or "the file produced no report"})
        finally:
            try: os.unlink(out_path)
            except OSError: pass
    return cases


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--one":
        return _run_one(sys.argv[2], sys.argv[3])
    started = time.time()
    cases = _run_all()
    result = _JUnitResult()
    result.cases = cases
    _write(result.cases, started, time.time() - started)
    for case in result.cases:
        if case["kind"] in ("failure", "error"):
            # The count alone left a red leg unnamed in CI: the 3.11
            # pass reported "1 failures" and no test name, and the
            # XML it wrote is not uploaded. Name it here, with the
            # tail of the traceback — the exception line is the part
            # a reader of the leg needs.
            print("%s %s.%s" % (case["kind"].upper(), case["classname"],
                                case["name"].split(" ", 1)[0]))
            tail = [line for line in case["message"].splitlines() if line.strip()][-3:]
            for line in tail:
                print("    " + line)
    failures = [case for case in result.cases if case["kind"] == "failure"]
    errors = [case for case in result.cases if case["kind"] == "error"]
    skipped = [case for case in result.cases if case["kind"] == "skipped"]
    print("wrote %s: %d tests, %d failures, %d errors, %d skipped" % (
        OUTPUT, len(result.cases), len(failures), len(errors), len(skipped)))
    return 1 if (failures or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
