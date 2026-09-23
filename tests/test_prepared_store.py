"""The file-backed prepared store's ONE format contract (v3): the
layout, the per-layer states (EMPTY / CACHED / UNCACHEABLE), random
access, identity gating, atomic completion, the size policy, the
abort path and the startup temp cleanup. Every reader of the store's
files — the reopen, the repair and the fraction — consumes the
semantics pinned here, nowhere else."""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from plugins.PlateProgress import decode_layer, encode_layer
from plugins.PreparedStore import STATE_CACHED, STATE_EMPTY, PreparedCache

# The on-disk layout the hand-built files below must match byte for
# byte (the writer's own constants, restated here so the malformed
# shapes can be laid out directly).
_HDR_FMT = "<4sIHHB"
_ENTRY_FMT = "<BQI"
_HDR_SIZE = struct.calcsize(_HDR_FMT)
_ENTRY_SIZE = struct.calcsize(_ENTRY_FMT)


def _write_raw(path, magic=b"MPFP", version=3, identity=b"print-1", count=2,
               complete=0, entries=(), table_bytes=None, payload=b""):
    """A cache file laid out byte for byte: the malformed shapes the
    writer itself never produces, so each reader's rejection reason
    can be reached on its own."""
    table = table_bytes if table_bytes is not None else b"".join(
        struct.pack(_ENTRY_FMT, state, offset, length)
        for state, offset, length in entries)
    with open(path, "wb") as handle:
        handle.write(struct.pack(_HDR_FMT, magic, version, len(identity),
                                 count, complete))
        handle.write(identity)
        handle.write(table)
        handle.write(payload)
    return path


def _park_dead_tmp(writer, complete=False):
    """Park a writer's temp as a pid-99999 leftover: the crash seam
    (the handle dropped before finish_write), or — with `complete` —
    the window between the completion flag's fsync and the rename."""
    if complete:
        writer["handle"].seek(_HDR_SIZE - 1)
        writer["handle"].write(b"\x01")
        writer["handle"].flush()
    writer["handle"].close()
    dead = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
    os.replace(writer["temp"], dead)
    return dead


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
        # them, and never touches a published file. The leftover's
        # owner is a REAL process that really died (spawned and
        # waited) — no magic pid is assumed dead, so a host where
        # pid 999 happens to be live still cleans only genuinely
        # dead owners.
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        stale = os.path.join(self.cache.directory,
                             "deadbeef.mpfp.tmp-%d-1234" % child.pid)
        with open(stale, "wb") as handle:
            handle.write(b"partial")
        reloaded = PreparedCache(self.cache.directory)
        self.assertFalse(os.path.exists(stale),
                         "the startup cleanup left a crash temp file")
        self.assertIsNotNone(reloaded.load_table("print-1"))

    def test_the_current_process_owns_its_tmp(self):
        # The liveness policy's first anchor: a tmp stamped with THIS
        # process's pid must read ALIVE — another PreparedCache
        # instance in the same process never adopts or deletes it.
        name = "print.mpfp.tmp-%d-1234" % os.getpid()
        self.assertTrue(self.cache._tmp_liveness(name),
                        "the live process's own tmp read dead")

    def test_a_live_childs_tmp_reads_alive(self):
        # A genuinely live foreign process (spawned, not yet
        # reaped): its tmp must read ALIVE on every platform.
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(self._reap, child)
        name = "print.mpfp.tmp-%d-1234" % child.pid
        self.assertTrue(self.cache._tmp_liveness(name),
                        "a live child's tmp read dead")

    def test_the_same_child_reads_dead_after_exit(self):
        # The same child, waited: the pid is provably gone — the
        # verdict flips to DEAD, and the tmp becomes adoptable. On
        # Windows the waiter still holds the process object, so the
        # probe must read the EXIT CODE, not the handle's mere
        # existence.
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        name = "print.mpfp.tmp-%d-1234" % child.pid
        self.assertFalse(self.cache._tmp_liveness(name),
                         "a waited child's tmp read alive")

    @staticmethod
    def _reap(child):
        if child.poll() is None:
            child.terminate()
            child.wait()

    def test_a_malformed_pid_reads_dead_without_raising(self):
        # No live writer ever stamps a name it cannot parse — the
        # probe reports dead and the startup never crashes.
        self.assertFalse(self.cache._tmp_liveness("print.mpfp.tmp-garbage-1234"))
        self.assertFalse(self.cache._tmp_liveness("print.mpfp.tmp--1234"))

    def test_an_impossible_pid_reads_dead_without_raising(self):
        # Zero and beyond-any-platform pids cannot own a writer: dead,
        # no exception — the OverflowError (POSIX) and the
        # ERROR_INVALID_PARAMETER (Windows) shapes both land here.
        self.assertFalse(self.cache._tmp_liveness("print.mpfp.tmp-0-1234"))
        self.assertFalse(self.cache._tmp_liveness(
            "print.mpfp.tmp-%d-1234" % (2 ** 40)))

    def test_an_unprovable_owner_keeps_its_tmp(self):
        # The conservative half of the policy, on the POSIX branch
        # EXPLICITLY (the assertions never ride the host's own
        # platform): a probe that cannot DISPROVE the owner keeps
        # the tmp — access-denied and any unexplained failure read
        # alive, so the adoption never destroys a file whose owner
        # may still hold it.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        name = "print.mpfp.tmp-12345-1234"
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill", side_effect=PermissionError()):
            self.assertTrue(self.cache._tmp_liveness(name),
                            "an access-denied owner read dead")
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill",
                             side_effect=OSError("unexplained")):
            self.assertTrue(self.cache._tmp_liveness(name),
                            "an unexplained probe failure read dead")

    def test_the_windows_probe_failure_semantics(self):
        # The native wrapper's failure verdicts (the review's
        # hardening tests): OpenProcess ERROR_INVALID_PARAMETER is
        # provably dead; access-denied or any unknown OpenProcess
        # failure and a GetExitCodeProcess failure are conservative
        # alive. The Win32 calls are MOCKED — no host process state
        # is fabricated.
        from unittest.mock import patch
        import ctypes
        import plugins.PreparedStore as store_module

        def verdict(open_handle, open_error, exit_ok, exit_code):
            closed = []

            def fake_open(*_args):
                return open_handle

            def fake_exit(handle, ref):
                if ref is not None and hasattr(ref, "_obj"):
                    ref._obj.value = exit_code
                return exit_ok

            def fake_close(handle):
                closed.append(handle)

            class FakeDll:
                # Instance attributes keep the functions PLAIN (a
                # class body would bind them, and bound methods
                # refuse the argtypes/restype assignments the
                # wrapper makes).
                def __init__(self):
                    self.OpenProcess = fake_open
                    self.GetExitCodeProcess = fake_exit
                    self.CloseHandle = fake_close

            # The names ride create=True: the host's Python 3.14
            # dropped the Windows helpers from ctypes' top level, and
            # the production branch only runs on Windows anyway.
            with patch.object(ctypes, "WinDLL", return_value=FakeDll(),
                              create=True), \
                    patch.object(ctypes, "get_last_error", return_value=open_error,
                                 create=True):
                result = store_module._windows_liveness(1234)
            return result, closed

        # OpenProcess ERROR_INVALID_PARAMETER -> provably dead.
        self.assertFalse(verdict(0, 87, True, 259)[0],
                         "a no-such-process verdict read alive")
        # Access-denied and unknown OpenProcess failures -> alive.
        self.assertTrue(verdict(0, 5, True, 259)[0],
                         "an access-denied owner read dead")
        self.assertTrue(verdict(0, 999, True, 259)[0],
                         "an unknown native error read dead")
        # A GetExitCodeProcess failure is indeterminate -> alive.
        self.assertTrue(verdict(0x1234, None, False, 259)[0],
                         "an exit-code read failure read dead")
        # The full chain: STILL_ACTIVE alive, an exit code dead, and
        # the handle always closes.
        self.assertTrue(verdict(0x1234, None, True, 259)[0])
        self.assertFalse(verdict(0x1234, None, True, 42)[0])
        self.assertEqual(verdict(0x1234, None, True, 42)[1], [0x1234],
                         "the handle never closed")

    def test_the_windows_branch_defers_to_the_native_probe(self):
        # The Windows branch's verdicts come from the NATIVE process
        # API, never os.kill: ALIVE and PROVABLY DEAD pass straight
        # through the probe. (The probe's own kernel calls run on
        # the Windows host suite; this wiring is platform-pinned.)
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        name = "print.mpfp.tmp-12345-1234"
        with patch.object(store_module.sys, "platform", "win32"), \
                patch.object(store_module, "_windows_liveness",
                             return_value=True):
            self.assertTrue(self.cache._tmp_liveness(name),
                            "the Windows alive verdict read dead")
        with patch.object(store_module.sys, "platform", "win32"), \
                patch.object(store_module, "_windows_liveness",
                             return_value=False):
            self.assertFalse(self.cache._tmp_liveness(name),
                             "the Windows dead verdict read alive")

    def test_the_posix_branch_reads_the_signal_verdicts(self):
        # The POSIX branch, pinned explicitly: alive on success,
        # dead on ProcessLookupError, conservative alive on
        # PermissionError and on any indeterminate OSError.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        name = "print.mpfp.tmp-12345-1234"
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill", return_value=None):
            self.assertTrue(self.cache._tmp_liveness(name),
                            "a live POSIX owner read dead")
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill",
                             side_effect=ProcessLookupError()):
            self.assertFalse(self.cache._tmp_liveness(name),
                             "a gone POSIX owner read alive")
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill",
                             side_effect=PermissionError()):
            self.assertTrue(self.cache._tmp_liveness(name),
                            "an access-denied POSIX owner read dead")
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill",
                             side_effect=OSError("indeterminate")):
            self.assertTrue(self.cache._tmp_liveness(name),
                            "an indeterminate POSIX failure read dead")

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

    def test_a_crashed_repair_that_resolved_more_beats_a_holey_complete_file(self):
        # The release-candidate finding: a completed pass can publish
        # EMPTY slots (a latched hydrate). A repair that resolved
        # MORE of those slots before crashing holds more real
        # coverage — the completion flag alone must never discard it.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(8):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.cache.finish_write(writer)  # complete: 8 cached, 2 EMPTY
        # Session 2: the repair resolves layer 8 too, then crashes.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(9):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertIsNotNone(table)
        self.assertFalse(table["complete"], "the adopted repair read complete")
        cached = sum(1 for entry in table["table"] if entry[0] == STATE_CACHED)
        self.assertEqual(cached, 9,
                         "the completion flag discarded the fuller repair")
        self.assertEqual(reloaded.read("print-1", table["table"], 8),
                         encode_layer(_payload(8)),
                         "the repair's extra resolution never round-tripped")

    def test_a_crashed_repair_behind_the_holey_complete_file_loses(self):
        # The companion rule: a repair that crashed EARLY holds fewer
        # resolved layers than the published complete file — the
        # published file must survive, however holey it is.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(8):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.cache.finish_write(writer)  # complete: 8 cached, 2 EMPTY
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(5):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertIsNotNone(table)
        self.assertTrue(table["complete"],
                        "the published complete file lost to a behind repair")
        cached = sum(1 for entry in table["table"] if entry[0] == STATE_CACHED)
        self.assertEqual(cached, 8)

    def test_an_uncacheable_entry_counts_toward_the_arbitration(self):
        # UNCACHEABLE is a RESOLVED state: a complete file with six
        # cached + one uncacheable entry (7 resolved) must lose to a
        # crashed repair holding eight cached entries — cached alone
        # would misread the final as merely 6.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(6):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.cache.append_uncacheable(writer, 6)
        self.cache.finish_write(writer)  # complete: 7 resolved, 3 EMPTY
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(8):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertIsNotNone(table)
        cached = sum(1 for entry in table["table"] if entry[0] == STATE_CACHED)
        self.assertEqual(cached, 8,
                         "the uncacheable entry never counted as coverage")

    def test_equal_arbitration_coverage_goes_to_the_newer_file(self):
        # Equal resolved coverage falls to the recency tie-break —
        # the policy never prefers a filename over fresher work.
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(5):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.cache.suspend_write(writer)  # the published 5/10 partial
        writer = self.cache.open_for_write("print-1", 10)
        for layer in range(5):
            self.cache.append(writer, layer, encode_layer(_payload(layer + 5)))
        writer["handle"].close()
        dead_tmp = writer["temp"].rsplit(".tmp-", 1)[0] + ".tmp-99999-1"
        os.replace(writer["temp"], dead_tmp)
        future = time.time() + 60.0
        os.utime(dead_tmp, (future, future))
        reloaded = PreparedCache(self.cache.directory)
        table = reloaded.load_table("print-1")
        self.assertIsNotNone(table)
        self.assertEqual(reloaded.read("print-1", table["table"], 0),
                         encode_layer(_payload(5)),
                         "the equal-coverage newer candidate lost the tie")

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


class UnifiedEvictionPolicyTests(unittest.TestCase):
    """The review's true-LRU semantics for the protected-entry
    eviction (item 2): the walk goes OLDEST first, the protected
    folder is never a candidate, and the eviction never finishes
    over budget while an unprotected folder remains. Every case
    deliberately arranges the hash order opposite the access
    order."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)

    def _folder(self, name, size, atime):
        """One print folder with a `size`-byte prepared file stamped
        with the given access time."""
        folder = os.path.join(self.cache.directory, "p-" + name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "prepared.mpfp")
        with open(path, "wb") as handle:
            handle.write(b"\0" * size)
        os.utime(path, (atime, atime))
        return path

    def _folder_exists(self, name):
        return os.path.exists(os.path.join(self.cache.directory, "p-" + name))

    def test_a_protected_entry_forces_reconsideration_of_the_newest(self):
        # The review's case: budget 100; A newest 60, B protected 60,
        # C oldest 20 — C goes first (oldest), and A must ALSO go
        # because the cache is still over budget; B alone survives
        # with 60 retained. The hash order (a, b, c) is the REVERSE
        # of the access order (c oldest, a newest).
        self._folder("aaa", 60, atime=3.0)   # newest
        keep = self._folder("bbb", 60, atime=2.0)  # protected
        self._folder("ccc", 20, atime=1.0)   # oldest
        self.cache.max_bytes = 100
        self.cache._evict(keep)
        self.assertTrue(self._folder_exists("bbb"),
                        "the protected entry was evicted")
        self.assertFalse(self._folder_exists("ccc"),
                         "the oldest unprotected entry survived")
        self.assertFalse(self._folder_exists("aaa"),
                         "the cache stayed over budget for the newest entry")

    def test_the_walk_is_lru_not_recency_biased_packing(self):
        # The review's case: newest 60, middle 50, oldest 40, budget
        # 100 — true LRU evicts the oldest 40 first, then the middle
        # 50; never the packing that keeps the oldest 40 because it
        # "fits better" beside the newest.
        self._folder("aaa", 60, atime=3.0)  # newest
        self._folder("bbb", 50, atime=2.0)
        self._folder("ccc", 40, atime=1.0)  # oldest
        self.cache.max_bytes = 100
        self.cache._evict(None)
        self.assertTrue(self._folder_exists("aaa"),
                        "the newest entry was evicted")
        self.assertFalse(self._folder_exists("ccc"),
                         "the oldest entry survived")
        self.assertFalse(self._folder_exists("bbb"),
                         "an older entry was kept over the middle one")

    def test_a_protected_entry_alone_may_exceed_the_budget(self):
        # The review's case: a 120-byte protected entry over a
        # 100-byte budget — every unprotected entry is evicted, and
        # the over-budget state is the only acceptable one (nothing
        # legal remains to evict).
        keep = self._folder("aaa", 120, atime=2.0)
        self._folder("bbb", 30, atime=1.0)
        self.cache.max_bytes = 100
        self.cache._evict(keep)
        self.assertTrue(self._folder_exists("aaa"),
                        "the protected entry was evicted")
        self.assertFalse(self._folder_exists("bbb"),
                         "an unprotected entry survived the overage")


class PreparedStoreRejectionTests(unittest.TestCase):
    """Every rejection reason at the file boundary: a torn header, a
    foreign identity, a half-written table, an out-of-range layer or a
    vanished file must each read as a MISS — never as a partial table
    the caller could act on."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)

    def test_a_header_cut_short_reads_as_absent(self):
        # The copy that died inside the header (or the file the volume
        # truncated): no version, no count, no identity to trust.
        path = self.cache._path("print-1")
        with open(path, "wb") as handle:
            handle.write(b"MPFP\x03")
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_a_foreign_magic_or_version_reads_as_absent(self):
        # Another tool's file, or this format's own earlier generation:
        # its offsets mean something else, so it must never parse.
        for label, kwargs in (("magic", {"magic": b"XXXX"}),
                              ("version", {"version": 2})):
            _write_raw(self.cache._path("print-1"), count=2,
                       entries=[(0, 0, 0), (0, 0, 0)], **kwargs)
            self.assertIsNone(self.cache.load_table("print-1"),
                              "the %s file parsed" % label)

    def test_a_foreign_identity_reads_as_absent(self):
        # The path digest is the ADDRESS, the stored identity is the
        # truth: a file that landed under the wrong print (a copied
        # folder) belongs to its own print only.
        _write_raw(self.cache._path("print-1"), identity=b"print-9",
                   count=2, entries=[(0, 0, 0), (0, 0, 0)])
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_a_half_written_table_reads_as_absent(self):
        # The header promises two entries; the file carries five bytes
        # of them (the pass died between the header and the table).
        _write_raw(self.cache._path("print-1"), count=2,
                   table_bytes=b"\x00" * 5)
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_a_candidate_header_that_does_not_parse_scores_nothing(self):
        # The arbitration's scoring reader refuses the same shapes: a
        # header cut short, a foreign magic, a foreign version, and an
        # empty pass (no identity, no layer count).
        refusing = {
            "torn": b"MPFP\x03\x00",
            "magic": struct.pack(_HDR_FMT, b"XXXX", 3, 7, 2, 0) + b"print-1",
            "version": struct.pack(_HDR_FMT, b"MPFP", 2, 7, 2, 0) + b"print-1",
            "no-layers": struct.pack(_HDR_FMT, b"MPFP", 3, 7, 0, 0) + b"print-1",
        }
        for label, raw in refusing.items():
            path = os.path.join(self._dir.name, label + ".mpfp.tmp-99999-1")
            with open(path, "wb") as handle:
                handle.write(raw)
            self.assertIsNone(self.cache._file_progress(path),
                              "the %s candidate scored progress" % label)

    def test_a_candidate_table_that_does_not_parse_scores_nothing(self):
        # A count the file does not carry, a CACHED entry advertising
        # bytes past the file's own end (a torn tail layer), and a state
        # byte outside this format's vocabulary (a file from a future
        # version) all read as corrupt — never as partial progress.
        short_table = os.path.join(self._dir.name, "short.mpfp.tmp-99999-1")
        _write_raw(short_table, count=2, table_bytes=b"\x00" * 5)
        past_end = os.path.join(self._dir.name, "past-end.mpfp.tmp-99999-1")
        _write_raw(past_end, count=1, entries=[(STATE_CACHED, 4096, 64)])
        unknown = os.path.join(self._dir.name, "unknown.mpfp.tmp-99999-1")
        _write_raw(unknown, count=1, entries=[(7, 0, 0)])
        for label, path in (("short-table", short_table),
                            ("past-end", past_end),
                            ("unknown-state", unknown)):
            self.assertIsNone(self.cache._file_progress(path),
                              "the %s candidate scored progress" % label)

    def test_an_unopenable_candidate_scores_nothing(self):
        # The read itself fails (an I/O error, a name a directory took
        # over): the scoring reports no progress rather than raising
        # into the startup adoption.
        path = os.path.join(self._dir.name, "unreadable.mpfp.tmp-99999-1")
        os.makedirs(path)
        self.assertIsNone(self.cache._file_progress(path))

    def test_a_corrupt_candidate_is_dropped_at_startup(self):
        # The adoption's verdict on a candidate that scores nothing and
        # has no published sibling: it dies — the store never adopts a
        # file it cannot read.
        path = os.path.join(self.cache.directory, "stray.mpfp.tmp-99999-1")
        _write_raw(path, count=1, entries=[(7, 0, 0)])
        PreparedCache(self.cache.directory)
        self.assertFalse(os.path.exists(path),
                         "the corrupt candidate survived the startup")

    def test_a_read_outside_the_table_reads_none(self):
        # The caller may hold a table across a re-index: an index below
        # zero or past the table's end is a miss, not a crash.
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        table = self.cache.load_table("print-1")["table"]
        self.assertIsNone(self.cache.read("print-1", table, -1))
        self.assertIsNone(self.cache.read("print-1", table, len(table)))

    def test_a_read_after_the_file_vanished_reads_none(self):
        # The eviction may drop the print between a table load and the
        # read: the layer reads as absent, never as an exception into
        # the render path.
        path = self.cache.finalise("print-1", [encode_layer(_payload(0))])
        table = self.cache.load_table("print-1")["table"]
        os.remove(path)
        self.assertIsNone(self.cache.read("print-1", table, 0))

    def test_finalising_no_layers_publishes_nothing(self):
        self.assertIsNone(self.cache.finalise("print-1", []))
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_a_failed_publish_leaves_no_temp_file(self):
        # The rename refused (a full or read-only volume): the temp must
        # not survive as a half-written orphan for the startup to
        # arbitrate over.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        with patch.object(store_module.os, "replace",
                          side_effect=OSError(28, "No space left on device")):
            self.assertIsNone(
                self.cache.finalise("print-1", [encode_layer(_payload(0))]))
        leftovers = [name for root, _dirs, names in os.walk(self.cache.directory)
                     for name in names if ".tmp-" in name]
        self.assertEqual(leftovers, [], "the failed publish left a temp file")

    def test_a_failed_publish_whose_cleanup_also_fails_reports_none(self):
        # The rename refused AND the temp unremovable (a read-only
        # volume): nothing legal remains to do — the publish reports
        # failure rather than raising out of the cleanup.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        with patch.object(store_module.os, "replace",
                          side_effect=OSError(28, "No space left on device")), \
                patch.object(store_module.os, "unlink",
                             side_effect=OSError(30, "Read-only file system")):
            self.assertIsNone(
                self.cache.finalise("print-1", [encode_layer(_payload(0))]))

    def test_opening_a_writer_without_layers_is_refused(self):
        # A print with no layers has no table to open.
        self.assertIsNone(self.cache.open_for_write("print-1", 0))
        self.assertIsNone(self.cache.open_for_write("print-1", -3))

    def test_a_refused_temp_creation_reads_as_no_writer(self):
        # The temp cannot be created (a full or read-only volume): the
        # caller reads "no writer" and skips the encode walk rather
        # than crashing it.
        from unittest.mock import patch
        import builtins
        real_open = builtins.open

        def refusing(name, *args, **kwargs):
            if ".tmp-" in str(name):
                raise OSError(28, "No space left on device")
            return real_open(name, *args, **kwargs)

        with patch.object(builtins, "open", refusing):
            self.assertIsNone(self.cache.open_for_write("print-1", 3))


class PreparedStoreArbitrationTests(unittest.TestCase):
    """The startup arbitration between a published file and an
    interrupted candidate, at the verdict level: a corrupt file on
    either side, a candidate that finished before the crash, and the
    tie-break that cannot read its own stamps."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)

    def test_a_corrupt_candidate_never_displaces_the_published_file(self):
        # A candidate scoring no progress loses to whatever is
        # published — never a filename preference.
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        dead = self.cache._path("print-1").rsplit(".mpfp", 1)[0] + ".mpfp.tmp-99999-1"
        with open(dead, "wb") as handle:
            handle.write(b"garbage")
        reloaded = PreparedCache(self.cache.directory)
        self.assertFalse(os.path.exists(dead), "the corrupt candidate survived")
        loaded = reloaded.load_table("print-1")
        self.assertTrue(loaded["complete"], "the published file was displaced")
        self.assertEqual(reloaded.read("print-1", loaded["table"], 0),
                         encode_layer(_payload(0)))

    def test_a_damaged_published_file_loses_to_a_valid_candidate(self):
        # A published file the reader cannot parse is no progress at
        # all: the interrupted candidate's layers are adopted over it.
        path = self.cache.finalise("print-1", [encode_layer(_payload(0))])
        with open(path, "wb") as handle:
            handle.write(b"MPFP\x03\x00")  # a header torn by the crash
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(5)))
        _park_dead_tmp(writer)
        reloaded = PreparedCache(self.cache.directory)
        loaded = reloaded.load_table("print-1")
        self.assertIsNotNone(loaded, "the valid candidate was discarded")
        self.assertFalse(loaded["complete"])
        self.assertEqual(reloaded.read("print-1", loaded["table"], 0),
                         encode_layer(_payload(5)),
                         "the damaged published file kept the table")

    def test_a_complete_candidate_wins_over_an_incomplete_published_file(self):
        # The crash window between the completion flag's fsync and the
        # rename leaves a COMPLETE, fully resolved candidate: it
        # outranks a published partial outright.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.suspend_write(writer)  # the published 1/3 checkpoint
        writer = self.cache.open_for_write("print-1", 3)
        for layer in range(3):
            self.cache.append(writer, layer, encode_layer(_payload(10 + layer)))
        _park_dead_tmp(writer, complete=True)
        reloaded = PreparedCache(self.cache.directory)
        loaded = reloaded.load_table("print-1")
        self.assertTrue(loaded["complete"], "the complete candidate lost")
        self.assertEqual(reloaded.read("print-1", loaded["table"], 0),
                         encode_layer(_payload(10)),
                         "the published partial kept the table")

    def test_a_tie_that_cannot_be_stamped_keeps_the_published_file(self):
        # Equal coverage falls to the mtime tie-break, and the stat
        # itself can fail (an I/O error on the volume): the published
        # file wins — the adoption never crashes on an unreadable
        # stamp.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        writer = self.cache.open_for_write("print-1", 4)
        for layer in range(2):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        self.cache.suspend_write(writer)  # the published 2/4 partial
        writer = self.cache.open_for_write("print-1", 4)
        for layer in range(2):
            self.cache.append(writer, layer, encode_layer(_payload(10 + layer)))
        dead = _park_dead_tmp(writer)
        final = self.cache._path("print-1")
        with patch.object(store_module.os, "stat",
                          side_effect=OSError(5, "Input/output error")):
            verdict = self.cache._arbitrate(final, dead)
        self.assertEqual(verdict, "final",
                         "the unreadable tie-break chose the candidate")
        self.assertEqual(
            self.cache.read("print-1",
                            self.cache.load_table("print-1")["table"], 0),
            encode_layer(_payload(0)),
            "the tie-break published the candidate it could not compare")

    def test_a_superseding_candidate_logs_both_coverage_counts(self):
        # The diagnostics ride the decision points: when a candidate
        # supersedes a partial the log names both counts, so a restore
        # that resumed from a crash is explained.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        calls = []

        class Recorder:
            @staticmethod
            def log(level, message, *args):
                calls.append((level, message, args))

        writer = self.cache.open_for_write("print-1", 4)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.suspend_write(writer)  # the published 1/4 partial
        writer = self.cache.open_for_write("print-1", 4)
        for layer in range(3):
            self.cache.append(writer, layer, encode_layer(_payload(layer)))
        _park_dead_tmp(writer)
        with patch.object(store_module, "_Logger", Recorder):
            reloaded = PreparedCache(self.cache.directory)
        self.assertEqual(len(calls), 1,
                         "the superseding decision was never logged")
        level, message, args = calls[0]
        self.assertEqual(level, "i")
        self.assertEqual(args, (3, 1),
                         "the log lost the coverage counts")
        self.assertIn("superseded", message)
        self.assertEqual(
            sum(1 for entry in reloaded.load_table("print-1")["table"]
                if entry[0] == STATE_CACHED), 3)


class PreparedStoreWriterFailureTests(unittest.TestCase):
    """The writer's own error paths: a refused sync, a refused publish,
    a stale append and an abort over a handle that already bit it —
    each one must leave the store consistent and the temp gone."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)

    def test_a_duplicate_append_never_rewrites_a_layer(self):
        # The first encoding of a layer is the one the pass resolved: a
        # repeat for the same slot (or an index outside the table) is
        # refused, so the table never advertises bytes since
        # overwritten.
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.append(writer, 0, encode_layer(_payload(7)))
        self.cache.append(writer, 5, encode_layer(_payload(5)))
        self.cache.finish_write(writer)
        table = self.cache.load_table("print-1")["table"]
        self.assertEqual(self.cache.read("print-1", table, 0),
                         encode_layer(_payload(0)),
                         "a duplicate append rewrote the layer")

    def test_a_retired_writer_refuses_every_later_write(self):
        # The ownership rule at the append boundary: once the owner's
        # suspend published the writer, a stale generation's append and
        # uncacheable mark are refused outright — neither can touch the
        # closed handle or the published table.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.assertTrue(self.cache.suspend_write(writer))
        self.cache.append(writer, 1, encode_layer(_payload(1)))
        self.cache.append_uncacheable(writer, 2)
        loaded = self.cache.load_table("print-1")
        self.assertEqual(loaded["table"][1], (0, 0, 0),
                         "a retired append reached the published file")
        self.assertEqual(loaded["table"][2], (0, 0, 0),
                         "a retired mark reached the published file")

    def test_an_uncacheable_mark_needs_a_live_slot(self):
        # The latched hydrate reaches the mark with no writer at all,
        # and a slot already carrying a layer — or one outside the
        # table — must never be re-marked.
        self.cache.append_uncacheable(None, 0)
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.cache.append_uncacheable(writer, 0)   # already CACHED
        self.cache.append_uncacheable(writer, 9)   # past the table
        self.cache.append_uncacheable(writer, -1)
        self.cache.append_uncacheable(writer, 1)   # the real mark
        self.cache.finish_write(writer)
        table = self.cache.load_table("print-1")["table"]
        self.assertEqual(table[0][0], STATE_CACHED,
                         "the mark overwrote a cached layer")
        self.assertEqual(self.cache.read("print-1", table, 0),
                         encode_layer(_payload(0)))
        self.assertEqual(table[1], (2, 0, 0))

    def test_a_failed_checkpoint_sync_never_loses_a_layer(self):
        # The periodic fsync (every 32 layers) may fail on a network
        # volume or a full disk: the bytes are already in the page
        # cache, the pass carries on, and every layer still round-trips
        # once the writer finishes.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        writer = self.cache.open_for_write("print-1", 40)
        with patch.object(store_module.os, "fsync",
                          side_effect=OSError(5, "Input/output error")):
            for layer in range(40):
                self.cache.append(writer, layer, encode_layer(_payload(layer)))
            path = self.cache.finish_write(writer)
        self.assertIsNotNone(path, "a refused sync cost the pass its publish")
        table = self.cache.load_table("print-1")["table"]
        for layer in range(40):
            self.assertEqual(self.cache.read("print-1", table, layer),
                             encode_layer(_payload(layer)),
                             "layer %d was lost to the sync failure" % layer)

    def test_a_retired_writer_never_finishes(self):
        # The suspend already published this writer's layers: a late
        # finish from the stale generation must not publish a second
        # time over it.
        writer = self.cache.open_for_write("print-1", 3)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        self.assertTrue(self.cache.suspend_write(writer))
        self.assertIsNone(self.cache.finish_write(writer))
        loaded = self.cache.load_table("print-1")
        self.assertFalse(loaded["complete"])
        self.assertEqual(loaded["table"][0][0], STATE_CACHED,
                         "the late finish overwrote the suspended table")

    def test_a_failed_finish_publish_aborts_the_writer(self):
        # The rename refused at the finish: no published file, no temp
        # left behind, and the writer retired by the abort.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        temp = writer["temp"]
        with patch.object(store_module.os, "replace",
                          side_effect=OSError(28, "No space left on device")):
            self.assertIsNone(self.cache.finish_write(writer))
        self.assertFalse(os.path.exists(temp),
                         "the failed finish left its temp behind")
        self.assertTrue(writer["retired"], "the failed finish kept the writer")
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_a_suspend_without_a_writer_reports_failure(self):
        self.assertFalse(self.cache.suspend_write(None))

    def test_a_suspend_survives_a_failed_sync(self):
        # The checkpoint's fsync hardens the publish but is not a
        # precondition: a refused sync still publishes the layers.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        with patch.object(store_module.os, "fsync",
                          side_effect=OSError(5, "Input/output error")):
            self.assertTrue(self.cache.suspend_write(writer),
                            "a refused sync cost the checkpoint its publish")
        loaded = self.cache.load_table("print-1")
        self.assertFalse(loaded["complete"])
        self.assertEqual(self.cache.read("print-1", loaded["table"], 0),
                         encode_layer(_payload(0)))

    def test_a_failed_suspend_publish_aborts_the_writer(self):
        # The rename refused at the checkpoint: the suspend reports
        # failure and the abort owns the cleanup — no temp is left for
        # the next startup to arbitrate over.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        temp = writer["temp"]
        with patch.object(store_module.os, "replace",
                          side_effect=OSError(28, "No space left on device")):
            self.assertFalse(self.cache.suspend_write(writer))
        self.assertFalse(os.path.exists(temp),
                         "the failed suspend left its temp behind")
        self.assertIsNone(self.cache.load_table("print-1"))

    def test_an_abort_over_a_dead_handle_still_removes_the_temp(self):
        # The abort's close can itself fail (the volume refuses the
        # final flush): the exit path must swallow it and still remove
        # the temp, so no crash on the way out leaves an orphan.
        writer = self.cache.open_for_write("print-1", 2)
        self.cache.append(writer, 0, encode_layer(_payload(0)))
        temp = writer["temp"]
        writer["handle"].close()

        class RefusingHandle:
            """The close whose final flush the volume refused."""

            def close(self):
                raise OSError(28, "No space left on device")

        writer["handle"] = RefusingHandle()
        self.cache.abort_write(writer)
        self.assertFalse(os.path.exists(temp),
                         "the failed close cost the abort its cleanup")


class PreparedStoreStartupGuardTests(unittest.TestCase):
    """The startup's own guards: the adoption of another writer's
    temp, and the eviction walk — neither may block a print or
    destroy a file it cannot account for."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cache = PreparedCache(self._dir.name, max_bytes=256 * 1024 * 1024)

    def test_a_pid_beyond_the_platform_range_reads_dead(self):
        # os.kill itself may refuse the pid (a narrow pid_t): an owner
        # that cannot exist is dead, not an error.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        with patch.object(store_module.sys, "platform", "linux"), \
                patch.object(store_module.os, "kill",
                             side_effect=OverflowError()):
            self.assertFalse(self.cache._tmp_liveness("print.mpfp.tmp-12345-1234"),
                             "an out-of-range pid read alive")

    def test_startup_never_touches_a_tmp_that_is_not_a_cache(self):
        # Only a `.mpfp.tmp-` leftover belongs to this store: any other
        # writer's temp keeps its name shape and dies by its owner's
        # rules alone.
        stray = os.path.join(self.cache.directory, "notes.txt.tmp-99999-1")
        with open(stray, "wb") as handle:
            handle.write(b"not a cache")
        PreparedCache(self.cache.directory)
        self.assertTrue(os.path.exists(stray),
                        "the startup cleanup deleted a foreign temp file")

    def test_a_temp_the_startup_could_not_remove_never_blocks_the_cache(self):
        # The unlink refused (a read-only directory): the adoption
        # keeps going — a leftover temp must never stop the print.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        stale = os.path.join(self.cache.directory, "stray.mpfp.tmp-99999-1")
        with open(stale, "wb") as handle:
            handle.write(b"partial")
        with patch.object(store_module.os, "unlink",
                          side_effect=OSError(30, "Read-only file system")) as unlink:
            reloaded = PreparedCache(self.cache.directory)
        self.assertTrue(unlink.called, "the unsound temp was never removed")
        self.assertTrue(os.path.exists(stale))
        self.assertIsNotNone(reloaded.load_table("print-1"),
                             "the startup abandoned the published prints")

    def test_an_unwalkable_directory_never_blocks_the_startup(self):
        # The volume cannot be walked (an I/O error): the adoption
        # finds nothing and the store still comes up with every
        # published print intact.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        path = self.cache.finalise("print-1", [encode_layer(_payload(0))])
        with patch.object(store_module.os, "walk",
                          side_effect=OSError(5, "Input/output error")):
            reloaded = PreparedCache(self._dir.name)
        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(reloaded.load_table("print-1"))

    def test_a_folder_holding_no_cache_files_is_never_a_candidate(self):
        # The policy accounts cache bytes: a folder with neither a
        # prepared nor an index file contributes nothing, counts toward
        # no budget and is never evicted.
        stray = os.path.join(self.cache.directory, "p-stray")
        os.makedirs(stray, exist_ok=True)
        with open(os.path.join(stray, "notes.txt"), "w") as handle:
            handle.write("x")
        keep = self.cache.finalise("print-1", [encode_layer(_payload(0))])
        self.cache.max_bytes = 1  # every ACCOUNTED folder must go
        self.cache._evict(keep)
        self.assertTrue(os.path.exists(stray),
                        "the eviction removed a folder it never accounted")
        self.assertTrue(os.path.exists(os.path.dirname(keep)))

    def test_a_folder_the_eviction_cannot_measure_is_skipped(self):
        # One print folder's files cannot be stat'ed (an I/O error on
        # the volume): the walk skips that folder and still brings the
        # rest of the directory back to budget.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        real_stat = store_module.os.stat
        broken = os.path.join(self.cache.directory, "p-broken")
        os.makedirs(broken, exist_ok=True)
        with open(os.path.join(broken, "prepared.mpfp"), "wb") as handle:
            handle.write(b"\0" * 64)
        keep = self.cache.finalise("print-1", [encode_layer(_payload(0))])
        victim = os.path.dirname(
            self.cache.finalise("print-2", [encode_layer(_payload(0))]))

        def refusing(path, *args, **kwargs):
            if os.path.dirname(str(path)) == broken:
                raise OSError(5, "Input/output error")
            return real_stat(path, *args, **kwargs)

        self.cache.max_bytes = 1
        with patch.object(store_module.os, "stat", refusing):
            self.cache._evict(keep)
        self.assertTrue(os.path.exists(broken),
                        "the unmeasurable folder was removed anyway")
        self.assertFalse(os.path.exists(victim),
                         "the eviction stopped at an unmeasurable folder")

    def test_an_unwalkable_directory_never_blocks_a_publish(self):
        # The eviction walk fails after a successful write: the print
        # is already published — the size policy never costs a print
        # its cache.
        from unittest.mock import patch
        import plugins.PreparedStore as store_module
        with patch.object(store_module.os, "walk",
                          side_effect=OSError(5, "Input/output error")):
            path = self.cache.finalise("print-1", [encode_layer(_payload(0))])
        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(self.cache.load_table("print-1"))
