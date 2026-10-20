# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Retain the native debugger's independent crash log.
# 2026-10-18 — 6.0.0: Verify crash evidence survives missing files and copy failures.

"""Exercise diagnostic collection without a running Cura or macOS host."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import collect_native_diagnostics as diagnostics


class NativeDiagnosticsTests(unittest.TestCase):
    def test_matrix_and_dispatched_leg_both_collect_after_a_failed_or_interrupted_step(self):
        workflows = Path(__file__).resolve().parents[1] / ".github/workflows"
        for name in ("ci.yml", "gate-leg.yml"):
            with self.subTest(workflow=name):
                source = (workflows / name).read_text()
                block = source.split("- name: Preserve native crash diagnostics (macOS)", 1)[1]
                block = block.split("- name:", 1)[0]
                self.assertIn("if: always() && runner.os == 'macOS'", block)
                self.assertIn("python tools/collect_native_diagnostics.py", block)
                self.assertIn('--output "$GALLERY/native-diagnostics"', block)
                self.assertLess(source.index("Preserve native crash diagnostics"),
                                source.index("Upload the unit's gallery"))

    def test_collects_native_logs_and_only_cura_crashes_including_retired_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("work/cura_run.log", "work/native-backtrace.log", "work/native_harness.log", "work/simulator.log",
                         "config/cura.log", "config/cura.log.1", "reports/UltiMaker-Cura.ips",
                         "reports/Retired/Cura.crash", "reports/Finder.ips",
                         "cura/stdout.log", "cura/stderr.log"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            copied = diagnostics.collect(root / "work", root / "config", root / "reports", root / "out")
            self.assertEqual(len(copied), 10)
            for path in copied:
                self.assertTrue(path.read_text())
            self.assertEqual((root / "out/crashes/Retired/Cura.crash").read_text(),
                             "reports/Retired/Cura.crash")
            self.assertFalse((root / "out/crashes/Finder.ips").exists())

    def test_missing_logs_and_one_unreadable_file_do_not_discard_other_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(diagnostics.collect(root / "missing", None, root / "missing", root / "out"), [])
            work = root / "work"
            work.mkdir()
            (work / "cura_run.log").write_text("crash")
            (work / "simulator.log").write_text("simulator")
            copy = diagnostics.shutil.copy2

            def restricted(source, destination):
                if source.name == "cura_run.log":
                    raise PermissionError("unreadable")
                return copy(source, destination)

            with patch.object(diagnostics.shutil, "copy2", side_effect=restricted):
                copied = diagnostics.collect(work, None, root / "missing", root / "out")
            self.assertEqual([path.name for path in copied], ["simulator.log"])
