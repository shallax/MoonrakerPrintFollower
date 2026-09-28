"""Pins for the gate's verdict panel.

The panel is the first thing on a job's page, so its verdict has to be
readable at a glance and has to be RIGHT: a summary that reported PASSED
over a failed leg would be worse than no summary at all. These tests
build evidence by hand and assert the rendered Markdown.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tools.gate_summary import render


def _step(name, ok, op="assert", spec=None, assertion=""):
    return {"schema": 1, "scenario": "group", "name": name, "op": op,
            "spec": spec or {}, "ok": ok, "assertion": assertion}


def _evidence(steps, capture=None):
    return {"schema": 1, "cura": "5.13.0", "plugin": "4.6.0",
            "capture": capture or {"mode": "on", "reason": "", "judged": True},
            "steps": steps}


class GateSummaryTests(unittest.TestCase):
    def _render(self, evidence):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "evidence.json").write_text(
                json.dumps(evidence), encoding="utf-8")
            return render(directory, "ubuntu-26.04 group-x")

    def test_a_clean_unit_leads_with_passed(self):
        out = self._render(_evidence([_step("g1-00", True), _step("g1-01", True)]))
        self.assertTrue(out.startswith("# PASSED ✅"), out.splitlines()[0])
        self.assertIn("| g1 | **PASSED** ✅ | 2 |", out)
        self.assertNotIn("Failed steps", out)

    def test_a_failed_unit_states_the_count_and_the_names(self):
        out = self._render(_evidence([
            _step("g1-00", True), _step("g1-01", False),
            _step("g2-00", False)]))
        self.assertTrue(out.startswith("# FAILED ❌ — 2 of 3 steps failed"),
                        out.splitlines()[0])
        # Both scenarios carry their own verdict, so the scan stops at
        # the table rather than at the log.
        self.assertIn("| g1 | **FAILED** ❌ g1-01 | 2 |", out)
        self.assertIn("| g2 | **FAILED** ❌ g2-00 | 1 |", out)

    def test_the_scenario_comes_from_the_step_name(self):
        # The evidence's own `scenario` field is the GROUP in some
        # writers, so the step name is the authority.
        out = self._render(_evidence([_step("h2-06", False)]))
        self.assertIn("| h2 |", out)
        self.assertNotIn("| group |", out)

    def test_a_failed_step_says_what_it_wanted_and_what_it_saw(self):
        out = self._render(_evidence([
            _step("h2-06", False, op="wait_rect",
                  spec={"op": "wait_rect", "objectName": "moonrakerReplacePrompt"},
                  assertion="now absent (never observed in this scenario)")]))
        self.assertIn("**h2-06** `wait_rect` moonrakerReplacePrompt", out)
        self.assertIn("wanted it to appear", out)
        self.assertIn("saw now absent", out)

    def test_an_absent_wait_reads_as_wanting_it_gone(self):
        out = self._render(_evidence([
            _step("h2-08", False, op="wait_rect",
                  spec={"op": "wait_rect", "objectName": "x", "absent": True})]))
        self.assertIn("wanted it to be gone", out)

    def test_a_long_probe_dump_is_clipped(self):
        # A wait_exec failure carries the whole probe's output; the
        # panel must not become the log.
        out = self._render(_evidence([
            _step("h2-13", False, op="wait_exec", spec={"op": "wait_exec", "code": "X"},
                  assertion="{" + "z" * 2000 + "}")]))
        self.assertIn("…", out)
        self.assertLess(max(len(line) for line in out.splitlines()), 400)

    def test_capture_off_is_stated_where_the_verdict_is(self):
        out = self._render(_evidence(
            [_step("h2-00", True)],
            capture={"mode": "off", "judged": False,
                     "reason": "macOS runs without capture by design. More here."}))
        self.assertIn("capture off", out)
        self.assertIn("macOS runs without capture by design", out)

    def test_an_unbuilt_summary_never_raises(self):
        # The step runs with `always()`: it must survive a leg that
        # died before writing anything, and say so rather than 404.
        with tempfile.TemporaryDirectory() as directory:
            out = render(directory, "unit")
        self.assertIn("NO EVIDENCE", out)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "evidence.json").write_text("{not json", encoding="utf-8")
            out = render(directory, "unit")
        self.assertIn("UNREADABLE EVIDENCE", out)

    def test_the_jobs_render_it_into_the_step_summary(self):
        # The wiring is half the feature: a summariser nothing calls
        # leaves the job page exactly as unreadable as before.
        root = Path(__file__).resolve().parent.parent
        for name, os_expr in (("gate-leg.yml", "inputs.os"),
                              ("release.yml", "matrix.os")):
            text = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("tools/gate_summary.py", text, name)
            self.assertIn('>> "$GITHUB_STEP_SUMMARY"', text, name)
            self.assertIn("if: always()", text, name)
            self.assertIn(os_expr, text, name)


if __name__ == "__main__":
    unittest.main()
