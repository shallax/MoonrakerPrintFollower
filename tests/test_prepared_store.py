"""The file-backed prepared store's contract: random access, identity
gating, atomic completion and the size policy."""
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
        self.cache = PreparedCache(self._dir.name, max_bytes=64 * 1024 * 1024)

    def test_finalise_round_trips_through_random_access(self):
        encodings = [encode_layer(_payload(layer)) for layer in range(6)]
        self.cache.finalise("print-1", encodings)
        table = self.cache.load_table("print-1")
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

    def test_a_different_identity_never_reads(self):
        self.cache.finalise("print-1", [encode_layer(_payload(0))])
        self.assertIsNone(self.cache.load_table("print-2"))

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
