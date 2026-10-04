"""The per-file coverage gate judges the tree, not the json.

A coverage json only lists measured files; a plugin file no test
imports has no entry, and a json-driven gate skipped it silently. These
pins hold the tree-driven gate: unmeasured code is a failure, an empty
package marker is not, and the 95% bar still applies to measured files.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _load_gate():
    path = Path(__file__).resolve().parent.parent / "tools" / "check_per_file_coverage.py"
    spec = importlib.util.spec_from_file_location("check_per_file_coverage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PerFileCoverageGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = _load_gate()
        self.addCleanup(setattr, self.gate, "SOURCE", self.gate.SOURCE)
        self.addCleanup(setattr, self.gate, "ROOT", self.gate.ROOT)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "mpf").mkdir()
        self.gate.ROOT = self.root
        self.gate.SOURCE = self.root / "mpf"
        self.json = self.root / "coverage.json"

    def _main(self, files):
        self.json.write_text(json.dumps({"files": files}), encoding="utf-8")
        argv = sys.argv
        sys.argv = ["check_per_file_coverage.py", str(self.json)]
        try:
            return self.gate.main()
        finally:
            sys.argv = argv

    @staticmethod
    def _entry(percent, statements=10):
        return {"summary": {"percent_covered": percent, "num_statements": statements}}

    def test_a_file_no_test_imported_fails_the_gate(self):
        (self.root / "mpf" / "Orphan.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self._main({}), 1)

    def test_an_empty_package_marker_is_not_a_file_to_measure(self):
        (self.root / "mpf" / "__init__.py").write_text('"""Comments only."""\n', encoding="utf-8")
        self.assertEqual(self._main({}), 0)

    def test_the_bar_still_judges_every_measured_file(self):
        (self.root / "mpf" / "Below.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "mpf" / "Above.py").write_text("value = 1\n", encoding="utf-8")
        files = {"mpf/Below.py": self._entry(94.9), "mpf/Above.py": self._entry(95.0)}
        self.assertEqual(self._main(files), 1)
        files["mpf/Below.py"] = self._entry(95.0)
        self.assertEqual(self._main(files), 0)

    def test_a_wholly_excluded_file_still_fails_the_gate(self):
        # `# pragma: no cover` on every statement reports
        # num_statements 0 / 100% — without the has_code() cross-check
        # the file would pass both this gate and the project total.
        (self.root / "mpf" / "Blank.py").write_text(
            "value = 1  # pragma: no cover\n", encoding="utf-8")
        self.assertEqual(self._main({"mpf/Blank.py": self._entry(100.0, 0)}), 1)

    def test_a_windows_separator_key_still_finds_its_file(self):
        (self.root / "mpf" / "Back.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self._main({"mpf\\Back.py": self._entry(96.0)}), 0)

    def test_unreadable_or_unparsable_input_is_judged_not_crashed(self):
        (self.root / "mpf" / "Binary.py").write_bytes(b"\xff\xfe\x00")
        self.assertEqual(self._main({}), 1)

    def test_only_the_packages_own_entry_point_is_skipped(self):
        (self.root / "mpf" / "__main__.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self._main({}), 0)
        (self.root / "mpf" / "tools").mkdir()
        (self.root / "mpf" / "tools" / "__main__.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self._main({}), 1)


if __name__ == "__main__":
    unittest.main()
