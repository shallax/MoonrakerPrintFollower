"""The file-backed prepared store's ONE format contract (v3): the
layout, the per-layer states (EMPTY / CACHED / UNCACHEABLE), random
access, identity gating, atomic completion, the size policy, the
abort path and the startup temp cleanup. Every reader of the store's
files — the reopen, the repair and the fraction — consumes the
semantics pinned here, nowhere else."""
from __future__ import annotations

import os
import tempfile
import time
import unittest

from plugins.PlateProgress import decode_layer, encode_layer
from plugins.PreparedStore import STATE_CACHED, STATE_EMPTY, PreparedCache


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
        # : the first session appends layer by
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
        # The per-layer truth rides the STATE: a finished pass stamps
        # the completion flag, a CACHED layer carries bytes, and a
        # slot the pass never resolved stays EMPTY — never confused
        # with an uncacheable refusal.
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        # Layer 1's hydrate failed mid-pass — the finish publishes
        # it EMPTY (retried next session), not uncacheable.
        self.cache.finish_write(writer)
        loaded = self.cache.load_table("print-1")
        self.assertTrue(loaded["complete"])
        self.assertEqual(loaded["table"][0][0], 1)  # CACHED
        self.assertGreater(loaded["table"][0][2], 0)
        self.assertEqual(loaded["table"][1], (0, 0, 0))  # EMPTY
        self.assertIsNone(self.cache.read("print-1", loaded["table"], 1))

    def test_an_uncacheable_layer_round_trips_its_state(self):
        # The codec's refusal is an EXPLICIT state: it publishes with
        # no bytes and reads back as never-retry — the reopen must
        # not re-walk it, and it counts toward the coverage.
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.append_uncacheable(writer, 1)
        path = self.cache.finish_write(writer)
        self.assertIsNotNone(path)
        loaded = self.cache.load_table("print-1")
        self.assertEqual(loaded["table"][1], (2, 0, 0))  # UNCACHEABLE
        self.assertIsNone(self.cache.read("print-1", loaded["table"], 1))
        # The round trip survives a reload from disk.
        reloaded = self.cache.load_table("print-1")
        self.assertEqual(reloaded["table"][1][0], 2)

    def test_an_aborted_writer_leaves_no_temp_file(self):
        # : the abort closes the handle and
        # removes the temp, however far the append got — and it is
        # idempotent for the exit paths that reach it twice.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.abort_write(writer)
        self.cache.abort_write(writer)
        self.assertIsNone(self.cache.load_table("print-1"))
        leftovers = [name for root, _dirs, names in os.walk(self.cache.directory)
                     for name in names if ".tmp-" in name]
        self.assertEqual(leftovers, [], "the aborted writer left a temp file")

    def test_startup_removes_previous_crash_temp_files(self):
        # : crash leftovers accumulate forever
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

    def test_an_interrupted_pass_adopts_its_encoded_layers(self):
        # The review's resumable-persistence finding: a pass that died
        # mid-encode leaves its successfully prepared layers in the
        # checkpointed tmp — the next session adopts them and resumes
        # from the EMPTY slots instead of restarting from layer zero.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.append(writer, 1, encode_layer(_payload(1)))
        # The crash: the handle drops without finish_write, and the
        # owning process is gone — the tmp's pid must read dead for
        # the adoption to claim it.
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertIsNotNone(table, "the interrupted pass never adopted")
        self.assertFalse(table["complete"], "the adopted table read complete")
        states = [entry[0] for entry in table["table"]]
        self.assertEqual(states, [STATE_CACHED, STATE_CACHED, STATE_EMPTY],
                         "the adopted table lost its encoded layers")
        self.assertEqual(reloaded.read("print-1", table["table"], 0),
                         encode_layer(_payload(0)),
                         "the adopted payload never round-tripped")

    def test_a_newer_interrupted_candidate_beats_an_older_partial(self):
        # The review's repeated-crash finding: a 30% published partial
        # + a 70% interrupted tmp must keep the 70% — never fall back
        # to the older less-complete file merely because it carries
        # the published name.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(3):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.cache.suspend_write(writer)  # the published INCOMPLETE 30% partial
        # Session 2 resumes, reaches 70%, crashes: the tmp survives.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(7):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertIsNotNone(table)
        self.assertFalse(table["complete"])
        cached = sum(1 for entry in table["table"] if entry[0] == STATE_CACHED)
        self.assertEqual(cached, 7, "the arbitration kept the older partial")
        for layer in range(7):
            self.assertEqual(reloaded.read("print-1", table["table"], layer),
                             encode_layer(_payload(layer)),
                             "the newer partial never round-tripped")

    def test_a_complete_final_beats_any_interrupted_candidate(self):
        # The policy's top rule: a complete valid final outranks an
        # incomplete tmp however many layers the tmp committed.
        self.cache.finalise("print-1", [encode_layer(_payload(i)) for i in range(5)])
        writer = self.cache.open_for_write("print-1", 5)
        for layer in range(2):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertTrue(table["complete"],
                        "the complete final lost to an incomplete tmp")

    def test_a_live_process_tmp_is_never_touched(self):
        # The multi-process safety: another process's active writer
        # (its pid still exists) survives the startup adoption.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        writer["handle"].flush()
        PreparedCache(self.cache.directory)
        self.assertTrue(os.path.exists(writer["temp"]),
                        "the live writer's temp was adopted or deleted")
        writer["handle"].close()

    def test_two_machine_namespaces_never_collide(self):
        # The review's two-printer test at the cache level: the SAME
        # remote identity (the same filename, size and modified) under
        # two machine directories — each printer's cache is its own,
        # and neither overwrites the other's.
        cache_a = PreparedCache(os.path.join(self._dir.name, "a"))
        cache_b = PreparedCache(os.path.join(self._dir.name, "b"))
        cache_a.finalise("print-1", [encode_layer(_payload(0))])
        cache_b.finalise("print-1", [encode_layer(_payload(5))])
        self.assertNotEqual(os.path.dirname(cache_a._path("print-1")),
                            os.path.dirname(cache_b._path("print-1")),
                            "the two machines share one directory")
        self.assertEqual(cache_a.read("print-1",
                                      cache_a.load_table("print-1")["table"], 0),
                         encode_layer(_payload(0)),
                         "printer A's cache was overwritten")
        self.assertEqual(cache_b.read("print-1",
                                      cache_b.load_table("print-1")["table"], 0),
                         encode_layer(_payload(5)),
                         "printer B's cache was overwritten")

    def test_a_protected_oldest_entry_does_not_stop_the_eviction(self):
        # : the protected CURRENT file is the
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
        survivors = [os.path.basename(os.path.join(root, name))
                     for root, _dirs, names in os.walk(cache.directory)
                     for name in names if name.endswith(".mpfp")]
        self.assertEqual(survivors, [os.path.basename(keep_path)],
                         "the eviction stopped at the protected entry")

    def test_the_index_and_prepared_store_are_siblings(self):
        # The review's unified-lifecycle finding: the index and the
        # prepared table live as siblings under ONE print folder —
        # an entire print's cache is one folder to delete.
        from plugins.GCodeIndex import PersistentIndexCache
        index_cache = PersistentIndexCache(self._dir.name)
        identity = type("Identity", (), {"stable_key": staticmethod(lambda: "print-1")})()
        self.assertEqual(os.path.dirname(index_cache._path(identity)),
                         os.path.dirname(self.cache._path("print-1")),
                         "the two stores split the print's folder")
        self.assertEqual(os.path.basename(index_cache._path(identity)),
                         "index.mpfi.gz")
        self.assertEqual(os.path.basename(self.cache._path("print-1")),
                         "prepared.mpfp")

    def test_an_evicted_print_loses_both_halves_together(self):
        # The print-level eviction (the review's unified-lifecycle
        # finding): a print past the budget loses the WHOLE folder —
        # the index AND the prepared table — never an orphaned half,
        # and a missing half reads gracefully on the other store.
        from plugins.GCodeIndex import PersistentIndexCache
        from plugins.GCodeIndex import build_index_from_bytes
        index_cache = PersistentIndexCache(self._dir.name)

        class Identity:
            # The same stable key the prepared store uses, so the two
            # halves share one print folder.
            filename = "a.gcode"
            size = 40
            modified = 100.0
            uuid = "u1"

            @staticmethod
            def stable_key():
                return "print-1"
        identity = Identity()
        index_cache.save(identity, build_index_from_bytes(b";LAYER:0\nG1 X1 Y1 Z0.2\n"))
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        folder = os.path.dirname(self.cache._path("print-1"))
        self.assertTrue(os.path.exists(os.path.join(folder, "index.mpfi.gz")))
        self.assertTrue(os.path.exists(os.path.join(folder, "prepared.mpfp")))
        # The prepared store's own eviction: a protected current print
        # survives even past the bound.
        self.cache.max_bytes = 1
        self.cache._evict(os.path.join(folder, "prepared.mpfp"))
        self.assertTrue(os.path.exists(folder),
                        "the protected print folder was evicted")
        # An UNPROTECTED print past the bound loses the whole folder.
        self.cache.finalise("print-other", [encode_layer(_payload(0))])
        self.assertFalse(os.path.exists(folder),
                         "the evicted print kept an orphaned half")
        self.assertIsNone(self.cache.load_table("print-1"),
                          "the evicted prepared table still reads")
        self.assertIsNone(index_cache.load(identity),
                          "the evicted index still reads")

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

    def test_the_eviction_walks_recency_not_hash_order(self):
        # The review's LRU finding: the walk must follow ACCESS time,
        # never the folder hash. The keys sort aaa, mmm, zzz
        # lexically; the ACTUAL READS stamp the recency mmm (oldest),
        # aaa, zzz (newest) — the reverse pairing. With a budget for
        # exactly two folders, the least recently READ print must go
        # (mmm, the lexical middle) while the most recent survives
        # despite sorting last lexically.
        cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)
        payload = encode_layer(_payload(0))
        for key in ("aaa", "mmm", "zzz"):
            cache.finalise(key, [payload])
        # The reads re-stamp the recency in the OPPOSITE pairing of
        # the write order: mmm first (oldest), zzz last (newest).
        cache.load_table("mmm")
        time.sleep(0.02)
        cache.load_table("aaa")
        time.sleep(0.02)
        cache.load_table("zzz")
        folders = {key: os.path.dirname(cache._path(key))
                   for key in ("aaa", "mmm", "zzz")}
        sizes = {key: os.path.getsize(cache._path(key))
                 for key in ("aaa", "mmm", "zzz")}
        # The bound tightens AFTER the writes and reads: exactly two
        # folders fit — one specific old print must disappear.
        cache.max_bytes = sizes["aaa"] + sizes["zzz"]
        cache._evict(None)
        self.assertFalse(os.path.exists(folders["mmm"]),
                         "the least recently read print survived")
        self.assertIsNone(cache.load_table("mmm"),
                          "the evicted print still reads")
        self.assertTrue(os.path.exists(folders["aaa"]),
                        "the middle print was evicted")
        self.assertTrue(os.path.exists(folders["zzz"]),
                        "the most recently read print was evicted")
        self.assertIsNotNone(cache.load_table("aaa"))
        self.assertIsNotNone(cache.load_table("zzz"))
