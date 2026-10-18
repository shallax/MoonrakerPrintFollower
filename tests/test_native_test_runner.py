# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Exercise isolated test invocation, failures and process deadlines.

"""Use real child processes to prove native tests cannot silently hang or pass."""
import contextlib
import io
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.native import dev


class NativeTestRunnerTests(unittest.TestCase):
    def test_stack_watchdog_survives_interpreter_shutdown(self):
        worker = Path(dev.__file__).with_name("test_worker.py")
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "slow_shutdown.py"
            script.write_text("import atexit, time\n"
                              "def shutdown(): time.sleep(0.5)\n"
                              "atexit.register(shutdown)\n")
            # Shorten the real watchdog, then let the child exit normally.
            probe = ("import faulthandler, runpy, sys; "
                     "dump=faulthandler.dump_traceback_later; "
                     "faulthandler.dump_traceback_later=lambda *a, **k: dump(0.1, repeat=True); "
                     "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
            result = subprocess.run([sys.executable, "-c", probe, str(worker), "file", str(script)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Timeout", result.stderr)
            self.assertIn("in shutdown", result.stderr)

    def test_deadline_kills_a_stuck_child_and_keeps_its_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = [sys.executable, "-u", "-c",
                    "import time; print('entered blocked test'); time.sleep(30)"]
            with patch.object(dev, "TEST_PROCESS_TIMEOUT", 1), contextlib.redirect_stdout(io.StringIO()) as output:
                result = dev._one_job((root, ("blocked", argv, dict(os.environ)), root))
            self.assertEqual(result[1], 124)
            self.assertEqual(result[2], 0)
            self.assertLess(result[5], 10)
            self.assertIn("entered blocked test", result[3].read_text())
            self.assertIn("process deadline", result[4][0])
            self.assertIn("START blocked", output.getvalue())
            self.assertIn("END blocked - FAIL", output.getvalue())

    def test_worker_preserves_script_and_unittest_failures_including_coverage(self):
        worker = Path(dev.__file__).with_name("test_worker.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "test_child.py"
            script.write_text("import unittest\nclass Child(unittest.TestCase):\n"
                              "    def test_child(self): self.assertEqual(1, 2)\n"
                              "if __name__ == '__main__': unittest.main()\n")
            modes = ["file", "module"]
            if importlib.util.find_spec("coverage") is not None:
                modes.append("coverage")
            for mode in modes:
                argv = [sys.executable]
                if mode == "coverage":
                    argv += ["-m", "coverage", "run"]
                argv += [str(worker)]
                if mode == "file":
                    argv += ["file", str(script)]
                else:
                    argv += ["module", "unittest", "discover", "-s", str(root)]
                env = dict(os.environ, COVERAGE_FILE=str(root / ".coverage"))
                result = subprocess.run(argv, cwd=root, env=env,
                                        capture_output=True, text=True, timeout=20)
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Ran 1 test", result.stderr)
                    self.assertIn("FAIL: test_child", result.stderr)
                    if mode == "coverage":
                        self.assertTrue((root / ".coverage").exists())
