"""Host-side pins for the runner's evidence spine.

runner.py runs inside the harness container, but its imports are
host-safe, so the pure pieces — the PNG dimension check behind
shot()'s self-defence and the evidence record — are pinned here,
outside the slow loop. Run directly (like test_harness_specs.py):
`python3 tests/harness/test_harness_runner.py`.
"""

import json
import os
import shutil
import time
import unittest

import runner

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
