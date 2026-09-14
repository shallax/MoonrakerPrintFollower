"""Host-side pins for the runner's evidence spine.

runner.py runs inside the harness container, but its imports are
host-safe, so the pure pieces — the PNG dimension check behind
shot()'s self-defence and the evidence record — are pinned here,
outside the slow loop. Run directly (like test_harness_specs.py):
`python3 tests/harness/test_harness_runner.py`.
"""

import json
import os
import re
import shutil
import time
import unittest
from pathlib import Path

import runner

ROOT = Path(__file__).resolve().parent.parent.parent
SCRATCH = "/tmp/mpf/test-harness-runner"


def _png_bytes(width, height):
    # A structurally valid PNG header: signature + IHDR chunk carrying
    # the declared dimensions. shot() reads only these fields, so the
    # file body is irrelevant.
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = (b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00")  # bit depth 8, RGBA, no interlace
    length = len(ihdr).to_bytes(4, "big")
    return signature + length + ihdr + b"\x00" * 4  # CRC not read by the parser


class PngSizeTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH, exist_ok=True)

    def test_declared_dimensions_parse(self):
        path = os.path.join(SCRATCH, "full.png")
        with open(path, "wb") as handle:
            handle.write(_png_bytes(1600, 1000))
        self.assertEqual(runner._png_size(path), (1600, 1000))

    def test_truncated_frame_is_not_a_png(self):
        path = os.path.join(SCRATCH, "truncated.png")
        with open(path, "wb") as handle:
            handle.write(_png_bytes(1600, 1000)[:20])
        self.assertIsNone(runner._png_size(path))

    def test_non_png_is_none(self):
        path = os.path.join(SCRATCH, "text.png")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not a png")
        self.assertIsNone(runner._png_size(path))


class ClassificationRatchetTests(unittest.TestCase):
    def test_real_input_can_only_grow(self):
        # The F08 acceptance, pinned statically: real-input steps may
        # only increase as the conversion proceeds. Today's census:
        # 41 click_stage + 2 deliver_click + 2 click_text.
        text = (ROOT / "tests/harness/scenarios.py").read_text()
        real = len(re.findall(r'"op": "(deliver_click|click_stage|click_text)"', text))
        self.assertGreaterEqual(real, 45)

    def test_direct_invocation_can_only_shrink(self):
        # And the other half: direct-invocation steps may only
        # shrink. Today's census: 34 exec_slot + 15 exec_file_slot +
        # 6 emit_click + 6 exec_mode + 3 click_jog + 3 exec_validator
        # + 2 exec_console + 1 exec_extrude + 1 exec_test_connection.
        text = (ROOT / "tests/harness/scenarios.py").read_text()
        direct = len(re.findall(
            r'"op": "(exec_slot|exec_file_slot|emit_click|click_jog|exec_mode'
            r'|exec_validator|exec_console|exec_extrude|exec_test_connection)"', text))
        self.assertLessEqual(direct, 71)

    def test_classification_derives_from_the_mechanism(self):
        # A step's class comes from its op and the delivery record —
        # never from a declared label.
        self.assertEqual(
            runner._classify("deliver_click", {"accepted": True}), "ui-interaction")
        self.assertEqual(
            runner._classify("deliver_click", {"accepted": False}),
            "application-integration")
        self.assertEqual(runner._classify("exec_slot", None),
                         "application-integration")
        self.assertEqual(runner._classify("wait_model", None),
                         "diagnostic-probe")


class RealModeAllowlistTests(unittest.TestCase):
    def test_real_mode_allowlists_are_pinned_exactly(self):
        # The ratcheting shape: these allowlists are the only thing
        # between a scenario and a live printer. They may only
        # shrink; any change moves this pin AND the decision record
        # in the same commit.
        self.assertEqual(sorted(runner.REAL_SAFE_SLOTS), sorted([
            "reconnect", "refreshAll", "refreshWebcams", "selectWebcam",
            "setShowProbePoints", "setBedMeshPreviewVisible",
            "setSectionExpanded", "setConsoleExpanded", "setStatusCollapsed",
            "setInfoCollapsed", "setControlsCollapsed", "setConsoleHeight",
            "clearConsoleHistory"]))
        self.assertEqual(runner.REAL_SAFE_OPS, {
            "click_stage", "click_text", "model_read",
            "wait_model", "assert_model", "exec_slot", "dwell",
            "rect_of", "assert_aligned", "assert_rendered",
            "wait_rendered", "wait_rect", "dump_visible",
            "resize_window", "census"})

    def test_input_verbs_are_denied_in_real_mode(self):
        # Deny-by-default: every input verb is refused in real mode
        # unless deliberately allowlisted (none are).
        for op in ("deliver_click", "key_press", "click_jog", "emit_click",
                   "exec_file_slot", "exec_code"):
            self.assertNotIn(op, runner.REAL_SAFE_OPS)


class EvidenceRecordTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        os.makedirs(SCRATCH, exist_ok=True)
        runner.EVIDENCE[:] = []
        self._old_run_dir = runner.RUN_DIR
        runner.RUN_DIR = SCRATCH

    def tearDown(self):
        runner.RUN_DIR = self._old_run_dir
        runner.EVIDENCE[:] = []

    def test_entry_shape_matches_schema_1(self):
        entry = runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "click_text", "text": "Pause"}, "f1-00",
            True, "a real click", "it landed", (SCRATCH + "/f1-00.png", None),
            time.monotonic())
        self.assertEqual(entry["schema"], 1)
        self.assertEqual(entry["scenario"], "f1")
        self.assertEqual(entry["step"], 0)
        self.assertEqual(entry["op"], "click_text")
        self.assertEqual(entry["spec"], {"op": "click_text", "text": "Pause"})
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["capture"], "f1-00.png")
        self.assertIsNone(entry["capture_error"])
        self.assertIsNone(entry["delivery"])
        self.assertGreaterEqual(entry["duration_ms"], 0)

    def test_capture_error_splits_from_the_path(self):
        entry = runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "sim_set"}, "f1-00", False, "state", "refused",
            ("/none/f1-00.png", "capture failed: empty frame"), time.monotonic())
        self.assertEqual(entry["capture"], "f1-00.png")
        self.assertEqual(entry["capture_error"], "capture failed: empty frame")
        self.assertFalse(entry["ok"])

    def test_write_evidence_lands_parseable_json(self):
        runner.EVIDENCE.append(runner._evidence_entry(
            {"id": "f1"}, 0, {"op": "sim_arm"}, "f1-00", True, "armed", "applied",
            ("/none/f1-00.png", None), time.monotonic()))
        runner.write_evidence("test run")
        with open(os.path.join(SCRATCH, "evidence.json"), encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["schema"], 1)
        self.assertEqual(record["title"], "test run")
        self.assertEqual(record["steps"][0]["scenario"], "f1")


if __name__ == "__main__":
    unittest.main()
