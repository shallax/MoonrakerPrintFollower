# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Measure the worker entry point and qualify preparation refusals and transfer budgets.

"""Exercise the shipped worker in-process as well as its isolated CLI bootstrap."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from mpf.application.indexing import IndexWorker
from mpf.gcode import GCodeIndex, IndexCache, IndexHydrator, PlateProgress
from mpf.gcode.SourceIdentity import CacheIdentity


class IndexWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "part.gcode"
        self.source.write_bytes(b"M83\n;LAYER:0\nG1 X1 Y1 E1\n;LAYER:1\nG1 X2 Y2 E1\n")
        self.output = self.root / "output"
        self.output.mkdir()
        self.index_root = self.root / "index"
        self.index_root.mkdir()
        self.index = GCodeIndex.build_index_from_file(self.source, compact=True)
        IndexCache.PersistentIndexCache(str(self.index_root)).save(CacheIdentity("worker"), self.index)

    def request(self, layers=None):
        (self.output / "request.json").write_text(json.dumps({
            "layers": [0, 1] if layers is None else layers, "start": 0, "end": 2,
            "index_root": str(self.index_root)}), encoding="utf-8")

    def result(self):
        return json.loads((self.output / "result.json").read_text(encoding="utf-8"))

    def test_cli_bootstrap_scans_and_persists_exact_boundaries(self):
        aliases = {key: value for key, value in sys.modules.items() if key.startswith("_mpf_index")}
        def restore_aliases():
            for key in list(sys.modules):
                if key.startswith("_mpf_index"):
                    del sys.modules[key]
            sys.modules.update(aliases)
        self.addCleanup(restore_aliases)
        with patch.object(sys, "argv", [str(IndexWorker.__file__), str(self.source), str(self.output), "compact"]):
            runpy.run_path(IndexWorker.__file__, run_name="__main__")
        actual = IndexCache.PersistentIndexCache(str(self.output)).load(CacheIdentity("worker"))
        self.assertIsNotNone(actual)
        self.assertEqual(actual.ranges, self.index.ranges)
        self.assertEqual(actual.layer_motion_counts, self.index.layer_motion_counts)
        self.assertTrue(actual.compact)
        self.source.unlink()
        error = io.StringIO()
        with patch.object(sys, "argv", [str(IndexWorker.__file__), str(self.source), str(self.output), "compact"]), redirect_stderr(error):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(IndexWorker.__file__, run_name="__main__")
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("part.gcode", error.getvalue())

    def test_scan_progress_is_throttled_and_empty_scans_are_explicit(self):
        def scan(_path, *, progress, **kwargs):
            progress(.25)
            progress(.5)
            return self.index
        with patch.object(GCodeIndex, "build_index_from_file", side_effect=scan), patch.object(IndexWorker.time, "monotonic", return_value=1):
            IndexWorker.main([str(self.source), str(self.output), "compact"])
        self.assertEqual((self.output / "progress").read_text(), "0.25")
        self.assertFalse((self.output / "progress.tmp").exists())
        self.source.write_bytes(b"G90\n")
        IndexWorker.main([str(self.source), str(self.output), "compact"])
        self.assertTrue((self.output / "empty").is_file())

    def test_prepare_cli_matches_inline_geometry_and_preserves_order(self):
        self.request()
        IndexWorker.main([str(self.source), str(self.output), "prepare"])
        result = self.result()
        self.assertEqual(result, {"frontier": 2, "encoded": [0, 1], "uncacheable": [], "processed": [0, 1]})
        full = GCodeIndex.build_index_from_file(self.source)
        for layer in range(2):
            self.assertEqual((self.output / f"layer-{layer}.ppl").read_bytes(),
                             PlateProgress.encode_layer(PlateProgress.prepare_layer(full, layer)))

    def test_invalid_index_batch_and_hydration_fail_before_publication(self):
        self.request()
        with patch.object(IndexCache.PersistentIndexCache, "load", return_value=None):
            with self.assertRaisesRegex(ValueError, "boundary index"):
                IndexWorker.prepare(str(self.source), str(self.output))
        for layers in ([1, 0], [0, 0], [-1], [2], ["zero"], "layers", list(range(33))):
            with self.subTest(layers=layers):
                self.request(layers)
                with self.assertRaisesRegex(ValueError, "layer batch"):
                    IndexWorker.prepare(str(self.source), str(self.output))
        self.request()
        with patch.object(IndexHydrator, "hydrate_layer_from_file", return_value=False):
            with self.assertRaisesRegex(ValueError, "hydrate"):
                IndexWorker.prepare(str(self.source), str(self.output))
        self.assertFalse((self.output / "result.json").exists())

    def test_uncacheable_and_empty_payloads_still_advance_processed_frontier(self):
        self.request()
        class Oversized:
            def __len__(self):
                return 64*1024*1024 + 1
        with patch.object(PlateProgress, "encode_layer", return_value=Oversized()):
            IndexWorker.prepare(str(self.source), str(self.output))
        self.assertEqual(self.result(), {"frontier": 2, "encoded": [], "uncacheable": [0, 1], "processed": [0, 1]})
        self.assertFalse(list(self.output.glob("*.ppl")))
        with patch.object(PlateProgress, "prepare_layer", return_value=None):
            IndexWorker.prepare(str(self.source), str(self.output))
        self.assertEqual(self.result(), {"frontier": 2, "encoded": [], "uncacheable": [], "processed": [0, 1]})

    def test_transfer_budget_stops_at_a_complete_layer_and_reports_partial_frontier(self):
        self.request()
        payload = b"PPL1" + b"x" * (8*1024*1024)
        with patch.object(PlateProgress, "encode_layer", return_value=payload):
            IndexWorker.prepare(str(self.source), str(self.output))
        self.assertEqual(self.result(), {"frontier": 1, "encoded": [0], "uncacheable": [], "processed": [0]})
        self.assertEqual((self.output / "layer-0.ppl").stat().st_size, len(payload))
        self.assertFalse((self.output / "layer-1.ppl").exists())
