#!/usr/bin/env python3
"""Run the plain unittest discovery and write JUnit-style XML for
Codecov's test-results analytics — the CI leg uploads the file through
codecov/test-results-action. No pytest dependency: the standard
library's discovery runs in one process and this result gathers each
test's verdict. The parallel host legs stay as they are; this is a
serial pass solely for the report."""
from __future__ import annotations

import os
import sys
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


def main():
    started = time.time()
    suite = unittest.defaultTestLoader.discover(DISCOVER_DIR, pattern=PATTERN)
    result = _JUnitResult()
    suite.run(result)
    _write(result.cases, started, time.time() - started)
    print("wrote %s: %d tests, %d failures, %d errors, %d skipped" % (
        OUTPUT, len(result.cases), len(result.failures), len(result.errors),
        len(result.skipped)))
    return 1 if (result.failures or result.errors) else 0


if __name__ == "__main__":
    sys.exit(main())
