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

    def test_write_recreates_a_deleted_parent_directory(self):
        # A config folder deleted by hand must not strand every later
        # save as a silent no-op: the write recreates its parent.
        target = os.path.join(self.dir.name, "deleted", "settings.json")
        store = StateStore(target, note=lambda kind, text: self.notes.append((kind, text)))
        self.assertTrue(store.write({"sections": {"toolhead": True}}))
        with open(target, encoding="utf-8") as handle:
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

    def test_write_survives_a_platform_without_ono_follow(self):
        # Windows: os has no O_NOFOLLOW, and the old unconditional
        # flag turned EVERY state write into a failure — the
        # what's-new marker never persisted and the overlay offered
        # itself on every launch (a Windows run). The
        # open must degrade to the plain flags there.
        saved = getattr(os, "O_NOFOLLOW", None)
        try:
            if saved is not None:
                del os.O_NOFOLLOW  # Windows never had the flag: the degraded path is its real one
            self.assertTrue(self.store.write({"whatsNewSeen": "4.3.0"}))
            with open(self.path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["whatsNewSeen"], "4.3.0")
        finally:
            if saved is not None:
                os.O_NOFOLLOW = saved

    def test_write_failure_reports_once_per_session(self):
        # A parent path that is a FILE can never hold the document:
        # the write fails, and the failure reports exactly once.
        blocker = os.path.join(self.dir.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("not a directory")
        store = StateStore(os.path.join(blocker, "sections.json"),
                           note=lambda kind, text: self.notes.append((kind, text)))
        self.assertFalse(store.write({"sections": {}}))
        self.assertFalse(store.write({"sections": {}}))
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.notes[0][0], "write")

    def test_non_dict_documents_read_as_none(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")
        self.assertIsNone(self.store.read())

    def test_a_corrupt_file_self_heals_on_the_next_write(self):
        # The adversarial round's M1: an undecodable-but-present file
        # must not wedge every save forever — the merge falls back to
        # an empty document and the write heals the file (the old
        # replace-write self-healed the same way).
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        self.assertTrue(self.store.write({"sections": {"toolhead": True}}))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["sections"], {"toolhead": True})

    def test_replace_write_drops_foreign_keys_deliberately(self):
        # merge=False is the migration's deliberate full-document
        # replace (the one-time chart migration drops the migrated
        # block).
        self.store.write({"sections": {}, "futureKey": 42})
        self.assertTrue(self.store.write({"sections": {"toolhead": False}}, merge=False))
        with open(self.path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertNotIn("futureKey", saved)
        self.assertEqual(saved["sections"], {"toolhead": False})


if __name__ == "__main__":
    unittest.main()
