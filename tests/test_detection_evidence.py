"""The alert evidence store: frames, per-print timelines, and the bounds
that keep both finite."""

import json
from pathlib import Path
import tempfile
import unittest

from mpf.detection import EvidenceStore


class _Image:
    """A stand-in for the QImage the model hands over: this module only
    ever asks it to save itself."""

    def __init__(self, ok=True):
        self.ok = ok
        self.saved = []

    def save(self, path, fmt, quality):
        self.saved.append((path, fmt, quality))
        if not self.ok:
            return False
        Path(path).write_bytes(b"jpeg")
        return True


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(prefix="mpf-evidence-")
        self.addCleanup(self._directory.cleanup)
        self.root = self._directory.name

    def _names(self):
        return sorted(Path(EvidenceStore.evidence_directory(self.root)).iterdir())

    def test_a_frame_is_named_for_its_print_and_level(self):
        image = _Image()
        path = EvidenceStore.save_frame(self.root, image, printer="printer-a",
                                        print_key=("part.gcode", 100, 1),
                                        level="failure", score=81, at=1_700_000_000)
        self.assertTrue(path)
        name = Path(path).name
        self.assertTrue(name.startswith("frame-"))
        self.assertTrue(name.endswith(".jpg"))
        # The print key never shapes the path: it is a token.
        self.assertNotIn("part.gcode", name)
        self.assertEqual(image.saved[0][1:], ("JPG", 85))

    def test_a_frame_that_cannot_be_written_reports_nothing(self):
        self.assertEqual(EvidenceStore.save_frame(self.root, _Image(ok=False),
                                                  printer="p", print_key="j", level="warning",
                                                  score=40), "")
        # And an unwritable destination is the same refusal, not a raise.
        blocker = Path(self.root, "blocker")
        blocker.write_text("not a directory", encoding="utf-8")
        self.assertEqual(EvidenceStore.save_frame(str(blocker), _Image(), printer="p",
                                                  print_key="j", level="warning", score=40), "")
        self.assertEqual(EvidenceStore.append_sample(str(blocker), printer="p", print_key="j",
                                                     at=1.0, score=1, raw=0.1), "")

    def test_only_the_newest_frames_survive(self):
        for index in range(EvidenceStore.MAX_FRAMES + 4):
            EvidenceStore.save_frame(self.root, _Image(), printer="p", print_key="j",
                                     level="warning", score=40, at=1_700_000_000 + index)
        frames = [path for path in self._names() if path.name.startswith("frame-")]
        self.assertEqual(len(frames), EvidenceStore.MAX_FRAMES)

    def test_a_timeline_appends_and_trims_to_its_bound(self):
        path = EvidenceStore.append_sample(self.root, printer="p", print_key="j",
                                           at=1.5, score=12, raw=0.34)
        self.assertTrue(path)
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")),
                         {"at": 1.5, "score": 12, "raw": 0.34})
        for index in range(2 * EvidenceStore.MAX_SAMPLES + 2):
            EvidenceStore.append_sample(self.root, printer="p", print_key="j",
                                        at=index, score=index % 101, raw=0.5)
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 2 * EvidenceStore.MAX_SAMPLES,
                             "the timeline grows without bound")
        self.assertEqual(json.loads(lines[-1])["at"], 2 * EvidenceStore.MAX_SAMPLES + 1)

    def test_only_the_newest_prints_timelines_survive(self):
        for index in range(EvidenceStore.MAX_TIMELINES + 3):
            EvidenceStore.append_sample(self.root, printer="p", print_key=("job-%d" % index, 1),
                                        at=float(index), score=1, raw=0.1)
        timelines = [path for path in self._names() if path.name.startswith("timeline-")]
        self.assertEqual(len(timelines), EvidenceStore.MAX_TIMELINES)

    def test_a_prune_that_cannot_read_or_delete_stays_quiet(self):
        # The bounds are best-effort: an unreadable folder or a file
        # another process holds must never turn a diagnostic write into
        # an alert-path failure.
        from unittest.mock import patch
        for _ in range(EvidenceStore.MAX_FRAMES + 2):
            EvidenceStore.save_frame(self.root, _Image(), printer="p", print_key="j",
                                     level="warning", score=40, at=1_700_000_000)
        before = len(self._names())
        with patch.object(EvidenceStore.os, "listdir", side_effect=OSError("denied")):
            EvidenceStore.save_frame(self.root, _Image(), printer="p", print_key="j",
                                     level="warning", score=41, at=1_700_000_100)
        self.assertEqual(len(self._names()), before + 1,
                         "an unlistable folder skipped the write")
        with patch.object(EvidenceStore.os, "unlink", side_effect=OSError("busy")):
            EvidenceStore.save_frame(self.root, _Image(), printer="p", print_key="j",
                                     level="warning", score=42, at=1_700_000_200)
        self.assertTrue(self._names()[-1].name.startswith("frame-"))

    def test_clear_survives_a_file_that_will_not_go(self):
        from unittest.mock import patch
        EvidenceStore.save_frame(self.root, _Image(), printer="p", print_key="j",
                                 level="warning", score=40)
        with patch.object(EvidenceStore.os, "unlink", side_effect=OSError("busy")):
            EvidenceStore.clear(self.root)  # must not raise; nothing else can be done
        self.assertTrue(self._names())

    def test_clear_takes_every_file_and_tolerates_an_absent_folder(self):
        EvidenceStore.save_frame(self.root, _Image(), printer="p", print_key="j",
                                 level="warning", score=40)
        EvidenceStore.append_sample(self.root, printer="p", print_key="j", at=1.0, score=1, raw=0.1)
        self.assertTrue(self._names())
        EvidenceStore.clear(self.root)
        self.assertEqual(self._names(), [])
        EvidenceStore.clear(self.root)  # idempotent


if __name__ == "__main__":
    unittest.main()
