"""The file-backed prepared store's contract: random access, identity
gating, atomic completion, the size policy, the abort path and the
startup temp cleanup (the review's findings 20/21)."""
from __future__ import annotations

import os
import tempfile
import unittest

from plugins.PlateProgress import decode_layer, encode_layer
from plugins.PreparedStore import PreparedCache


def _payload(layer):
    return {"classes": {"SKIN": [[[0.0, 0.0, 0.0], [1.0, float(layer), 1.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2}


def _dense(layer):
    """A ~20k-motion payload (~120 KB encoded): the eviction test's
    files must genuinely exceed the size floor."""
    segments = [[[i * 0.8 % 246.0, 2.0 + float(layer), float(i)] for i in range(20000)]]
    return {"classes": {"FILL": segments}, "travels": [], "travelStarts": [],
            "travelEnds": [], "motions": 20000}


class PreparedStoreTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)

    def _table(self, identity):
        loaded = self.cache.load_table(identity)
        return loaded["table"] if loaded is not None else None

    def test_finalise_round_trips_through_random_access(self):
        encodings = [encode_layer(_payload(layer)) for layer in range(6)]
        self.cache.finalise("print-1", encodings)
        table = self._table("print-1")
        self.assertIsNotNone(table)
        self.assertEqual(len(table), 6)
        for layer in range(6):
            raw = self.cache.read("print-1", table, layer)
            self.assertEqual(decode_layer(raw)["motions"], 2)
            self.assertEqual(decode_layer(raw)["classes"]["SKIN"][0][1][1], float(layer))

    def test_a_truncated_cache_is_never_complete(self):
        encodings = [encode_layer(_payload(layer)) for layer in range(3)]
        path = self.cache.finalise("print-1", encodings)
        with open(path, "r+b") as handle:
            handle.truncate(os.path.getsize(path) - 10)
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_the_incremental_writer_publishes_atomically(self):
        # The review's finding 9: the first session appends layer by
        # layer (no full-RAM finalisation), and an unfinished writer
        # never reads as complete.
        writer = self.cache.open_for_write("print-1", 3)
        self.assertIsNotNone(writer)
        for layer in range(3):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.assertIsNone(self.cache.load_table("print-1"),
                          "an unfinished writer read as complete")
        path = self.cache.finish_write(writer)
        self.assertIsNotNone(path)
        table = self._table("print-1")
        self.assertEqual(len(table), 3)
        for layer in range(3):
            raw = self.cache.read("print-1", table, layer)
            self.assertEqual(decode_layer(raw)["classes"]["SKIN"][0][1][1], float(layer))

    def test_a_different_identity_never_reads(self):
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        self.assertIsNone(self.cache.load_table("print-2"))

    def test_a_finished_file_carries_the_completion_flag(self):
        # The review's finding 16: the flag is what distinguishes a
        # genuinely uncacheable (0, 0) entry from a not-prepared-yet
        # hole — a finished pass stamps it, a published file always
        # carries it.
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        # Layer 1's encode "fails" — the pass walks on; the finish
        # still publishes complete (the attempt is recorded).
        self.cache.finish_write(writer)
        loaded = self.cache.load_table("print-1")
        self.assertTrue(loaded["complete"])
        self.assertEqual(loaded["table"][0][1] > 0, True)
        self.assertEqual(loaded["table"][1], (0, 0))

    def test_an_aborted_writer_leaves_no_temp_file(self):
        # The review's finding 20: the abort closes the handle and
        # removes the temp, however far the append got — and it is
        # idempotent for the exit paths that reach it twice.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.abort_write(writer)
        self.cache.abort_write(writer)
        self.assertIsNone(self.cache.load_table("print-1"))
        leftovers = [name for name in os.listdir(self.cache.directory) if ".tmp-" in name]
        self.assertEqual(leftovers, [], "the aborted writer left a temp file")

    def test_startup_removes_previous_crash_temp_files(self):
        # The review's finding 21: crash leftovers accumulate forever
        # (eviction only sees .mpfp) — a fresh cache instance cleans
        # them, and never touches a published file.
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        stale = os.path.join(self.cache.directory, "deadbeef.mpfp.tmp-999-1234")
        with open(stale, "wb") as handle:
            handle.write(b"partial")
        reloaded = PreparedCache(self.cache.directory)
        self.assertFalse(os.path.exists(stale),
                         "the startup cleanup left a crash temp file")
        self.assertIsNotNone(reloaded.load_table("print-1"))

    def test_a_protected_oldest_entry_does_not_stop_the_eviction(self):
        # The review's finding 13: the protected CURRENT file is the
        # oldest — the policy must skip it and evict the next
        # candidates until the directory fits.
        cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)
        keep_path = cache.finalise("print-1", [encode_layer(_dense(i)) for i in range(200)])
        cache.finalise("print-2", [encode_layer(_dense(i)) for i in range(200)])
        cache.finalise("print-3", [encode_layer(_dense(i)) for i in range(200)])
        # The bound tightens AFTER the writes (the finalise's own
        # eviction must not pre-empt the protected file), so the
        # protected-oldest case runs cleanly.
        cache.max_bytes = 16 * 1024 * 1024
        cache._evict(keep_path)
        self.assertTrue(os.path.exists(keep_path),
                        "the protected file was evicted")
        survivors = [name for name in os.listdir(cache.directory) if name.endswith(".mpfp")]
        self.assertEqual(survivors, [os.path.basename(keep_path)],
                         "the eviction stopped at the protected entry")

    def test_the_size_policy_evicts_the_oldest_file(self):
        cache = PreparedCache(self._dir.name, max_bytes=16 * 1024 * 1024)
        cache.finalise("print-1", [encode_layer(_dense(i)) for i in range(200)])
        path1 = cache._path("print-1")
        self.assertTrue(os.path.exists(path1))
        cache.finalise("print-2", [encode_layer(_dense(i)) for i in range(200)])
        cache.finalise("print-3", [encode_layer(_dense(i)) for i in range(200)])
        cache._evict(cache._path("print-3"))
        self.assertFalse(os.path.exists(path1),
                         "the oldest print's file survived the bound")
