"""Pure tests for the 4.2.0 StateStore (F11/A6): the file semantics
the model's persistence now delegates to."""
import json
import os
import tempfile
import unittest

from plugins.StateStore import StateStore


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "sections.json")
        self.notes = []
        self.store = StateStore(self.path, note=lambda kind, text: self.notes.append((kind, text)))

    def test_write_merges_into_the_existing_document(self):
        # The read-modify-write (A6): a fixed-document save would
        # erase foreign keys — 4.3.0's UI-state store consumes this
        # file, and the merge is what keeps its keys alive.
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"toolhead": False}, "futureKey": 42}, handle)
        self.assertTrue(self.store.write({"sections": {"toolhead": True}, "controlsLocked": True}))
        with open(self.path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["sections"], {"toolhead": True})
        self.assertEqual(saved["futureKey"], 42)
        self.assertTrue(saved["controlsLocked"])

    def test_write_leaves_no_stale_tmp_and_survives_one(self):
        self.store.write({"sections": {}})
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        # A stale .tmp from a crash must not break the next write.
        with open(self.path + ".tmp", "w", encoding="utf-8") as handle:
            handle.write("stale")
        self.assertTrue(self.store.write({"sections": {"toolhead": True}}))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["sections"], {"toolhead": True})

    def test_read_failure_reports_once_per_session(self):
        # A path that EXISTS but cannot be opened as a file (the
        # temp dir itself) — a genuine failure, unlike the missing
        # file of a first run, which stays silent.
        store = StateStore(self.dir.name,
                           note=lambda kind, text: self.notes.append((kind, text)))
        self.assertIsNone(store.read())
        self.assertIsNone(store.read())
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.notes[0][0], "read")
        # The session boundary re-arms the latch.
        store.reset_failures()
        self.assertIsNone(store.read())
        self.assertEqual(len(self.notes), 2)

    def test_write_failure_reports_once_per_session(self):
        store = StateStore(os.path.join(self.dir.name, "missing", "sections.json"),
                           note=lambda kind, text: self.notes.append((kind, text)))
        self.assertFalse(store.write({"sections": {}}))
        self.assertFalse(store.write({"sections": {}}))
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.notes[0][0], "write")

    def test_non_dict_documents_read_as_none(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")
        self.assertIsNone(self.store.read())


if __name__ == "__main__":
    unittest.main()
