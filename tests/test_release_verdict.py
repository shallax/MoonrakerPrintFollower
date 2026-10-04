"""Tag publication requires completed checks, including the Cura matrix."""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release_verdict.py"
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(
    encoding="utf-8")


def job_block(workflow: str, name: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)",
                      workflow)
    if match is None:
        raise AssertionError(f"{name} job is missing")
    return match.group(1)


class ReleaseVerdictTests(unittest.TestCase):
    def run_verdict(self, package: str, scan: str, gate: str):
        env = os.environ.copy()
        env.update(MPF_RELEASE_PACKAGE_RESULT=package,
                   MPF_RELEASE_SCAN_RESULT=scan,
                   MPF_RELEASE_GATE_RESULT=gate)
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT,
                              env=env, capture_output=True, text=True,
                              check=False)

    def test_all_release_checks_must_succeed(self):
        result = self.run_verdict("success", "success", "success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_skipped_failed_or_missing_checks_block_publication(self):
        for states in (("success", "skipped", "skipped"),
                       ("success", "failure", "skipped"),
                       ("success", "success", "failure"),
                       ("", "success", "success")):
            with self.subTest(states=states):
                result = self.run_verdict(*states)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Release checks incomplete", result.stdout)

    def test_tag_workflow_wires_a_fail_closed_verdict(self):
        scan = job_block(CI, "artifact-scan")
        self.assertIn("!cancelled()", scan)
        self.assertIn("needs.ci-package.result == 'success'", scan)
        verdict = job_block(CI, "release-verdict")
        self.assertIn("needs: [ci-package, artifact-scan, gate]", verdict)
        self.assertIn("!cancelled() && inputs.tag_release", verdict)
        self.assertIn("run: python tools/release_verdict.py", verdict)
        self.assertIn("needs: ci", job_block(RELEASE, "publish"))


if __name__ == "__main__":
    unittest.main()
