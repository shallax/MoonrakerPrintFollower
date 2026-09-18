"""Pure tests for the 4.2.0 StateStore (F11/A6): the file semantics
the model's persistence now delegates to."""
import json
import os
import tempfile
import unittest

from plugins.StateStore import StateStore


class _Recording:
    """A lock provider's context manager that narrates its span."""

    def __init__(self, events):
        self._events = events

    def __enter__(self):
        self._events.append("acquire")
        return self

    def __exit__(self, *args):
        self._events.append("release")
        return False


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

    def test_update_holds_the_lock_across_the_read_and_the_write(self):
        # The load-bearing half of the read-modify-write: the mutator
        # runs INSIDE the acquisition, so no sibling writer can land
        # between the read and the save.
        events = []
        store = StateStore(self.path, lock=lambda: _Recording(events))
        store.update(lambda document: (events.append("mutate"), document)[1])
        self.assertEqual(events, ["acquire", "mutate", "release"])

    def test_two_stale_readers_updating_separate_keys_both_survive(self):
        # The facade's key-scoped writes are read-modify-writes; a read
        # taken before a sibling's write, followed by a replace, erases
        # the sibling (the "two facades over one file kept only the
        # last machine saved" fault). update() reads under the lock, so
        # every key lands.
        first = StateStore(self.path)
        second = StateStore(self.path)
        first.write({"machines": {"A": {"url": "http://a:7125"}}})
        stale = first.read()  # both readers now hold the same document
        second.update(lambda document: {**document, "global": {"bedMeshVisible": True}})
        first.update(lambda document: {**document, "machines": {"A": {"url": "http://a:7125"}}})
        with open(self.path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["machines"]["A"]["url"], "http://a:7125")
        self.assertTrue(saved["global"]["bedMeshVisible"])
        # The naive shape (a stale read persisted as a replace) is what
        # update() exists to prevent — pinned so the two cannot be
        # confused again.
        stale["machines"]["A"]["url"] = "http://a:7125"
        second.write(stale, merge=False)
        with open(self.path, encoding="utf-8") as handle:
            self.assertNotIn("global", json.load(handle))

    def test_update_with_no_document_writes_nothing(self):
        # The deliberate no-op (an absent machine's removal): the
        # mutator's None means "nothing changed", not a failed save.
        self.store.write({"machines": {"A": {}}})
        self.assertTrue(self.store.update(lambda document: None))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"machines": {"A": {}}})
        self.assertEqual(self.notes, [])

    def test_a_host_without_a_lock_primitive_still_persists(self):
        # E9's degraded host: the injected provider has no lock to
        # offer and reports None. `with None:` is a TypeError, so the
        # store must normalise it to "run unlocked" — otherwise every
        # save on that host fails silently.
        store = StateStore(self.path, note=lambda kind, text: self.notes.append((kind, text)),
                           lock=lambda: None)
        self.assertTrue(store.write({"sections": {"toolhead": False}}))
        self.assertTrue(store.update(lambda document: {**document, "controlsLocked": True}))
        self.assertTrue(store.write({"sections": {"toolhead": True}}, merge=False))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["sections"], {"toolhead": True})
        self.assertEqual(self.notes, [])

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
