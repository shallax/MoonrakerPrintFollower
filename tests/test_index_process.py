# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Cover helper bootstrap, interrupted publication and invalid child results.

"""Real isolated scanner parity, cancellation and failed-child containment."""
from dataclasses import fields
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mpf.application.indexing import IndexProcess
from mpf.gcode.GCodeIndex import build_index_from_file
from mpf.gcode.MotionIndex import LayerMotionIndex
from mpf.gcode.SourceIdentity import CacheIdentity
from tests.qt_runtime_support import QT_AVAILABLE, runtime


class IndexProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mpf process test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builder = IndexProcess.IndexProcessBuilder(self.root, executable=sys.executable)
        self.source = self.root / "part with spaces.gcode"
        self.source.write_bytes(b";LAYER:0\nG90\nM83\nG1 X10 Y10 E1\nG1 X20 E1\n")

    def test_real_worker_matches_every_persisted_index_field_for_slicer_fixtures(self):
        for path in sorted(Path(__file__).with_name("fixtures").joinpath("gcode").glob("*.gcode")):
            with self.subTest(path=path.name):
                progress = []
                for compact in (True, False):
                    self.builder._compact = compact
                    actual = self.builder(path, threading.Event(), progress=progress.append)
                    expected = build_index_from_file(path, compact=compact)
                    for field in fields(LayerMotionIndex):
                        if field.name == "cache_lock":
                            continue
                        self.assertEqual(getattr(actual, field.name), getattr(expected, field.name), field.name)
                    if expected:
                        self.assertEqual(progress[-1], 1.0)

    def test_compact_result_hydrates_the_same_motions_as_full_scan(self):
        from mpf.gcode.IndexHydrator import hydrate_layer_from_file
        for path in sorted(Path(__file__).with_name("fixtures").joinpath("gcode").glob("*.gcode")):
            with self.subTest(path=path.name):
                actual = self.builder(path, threading.Event())
                expected = build_index_from_file(path, compact=False)
                for layer in range(len(actual.ranges)):
                    self.assertTrue(hydrate_layer_from_file(actual, path, layer))
                    for name in ("motion_offsets", "motion_x", "motion_y", "motion_z", "motion_arcs",
                                 "motion_extrusion", "motion_speeds", "motion_tools", "motion_types"):
                        self.assertEqual(getattr(actual, name)[layer], getattr(expected, name)[layer], name)

    def test_cancellation_before_launch_never_starts_a_child(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(IndexProcess.subprocess, "Popen") as launch:
            self.assertFalse(self.builder(self.source, cancelled))
        launch.assert_not_called()

    def test_progress_cancellation_terminates_joins_and_removes_private_output(self):
        self.source.write_bytes(b";LAYER:0\n" + b"G1 X1 Y2 E0.1\n" * 1_000_000)
        cancelled = threading.Event()
        children, directories, fractions = [], [], []
        launch = subprocess.Popen

        def recorded(*args, **kwargs):
            process = launch(*args, **kwargs)
            children.append(process)
            directories.append(Path(kwargs["cwd"]))
            return process

        def report(fraction):
            fractions.append(fraction)
            cancelled.set()

        with patch.object(IndexProcess.subprocess, "Popen", recorded):
            self.assertFalse(self.builder(self.source, cancelled, progress=report))
        self.assertTrue(fractions, "the running scan must publish byte progress")
        self.assertLess(fractions[0], 1.0)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertFalse(directories[0].exists())

    def test_worker_failure_surfaces_without_in_process_retry(self):
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "No such file|cannot find|not find"):
            self.builder(self.source, threading.Event())

    def test_missing_result_is_an_error(self):
        with patch.object(IndexProcess.PersistentIndexCache, "load", return_value=None):
            with self.assertRaisesRegex(ValueError, "invalid index"):
                self.builder(self.source, threading.Event())

    def test_no_layer_markers_returns_empty_index(self):
        self.source.write_bytes(b"G90\nG1 X1 Y2\n")
        self.assertFalse(self.builder(self.source, threading.Event()))

    def test_cooperative_yields_are_disabled_only_for_the_child(self):
        from mpf.gcode import GCodeIndex
        with patch.object(GCodeIndex, "passive_yield") as hand_back:
            self.source.write_bytes(b";LAYER:0\n" + b"G1 X1 Y2\n" * 200)
            build_index_from_file(self.source, cooperative=False)
            hand_back.assert_not_called()
            build_index_from_file(self.source)
            self.assertGreater(hand_back.call_count, 0)

    def test_startup_failure_removes_private_output(self):
        directories = []
        def fail(*args, **kwargs):
            directories.append(Path(kwargs["cwd"]))
            raise OSError("cannot start helper")
        with patch.object(IndexProcess.subprocess, "Popen", fail):
            with self.assertRaisesRegex(OSError, "cannot start"):
                self.builder(self.source, threading.Event())
        self.assertFalse(directories[0].exists())

    def test_verified_python_only_install_is_reused_and_cancelled_bootstrap_is_not_retried(self):
        builder = IndexProcess.IndexProcessBuilder(self.root)
        assets = [("cad.whl", {}), ("python.zip", {})]
        with patch.object(IndexProcess, "runtime_assets", return_value=assets), \
                patch.object(IndexProcess, "runtime_directory", return_value=str(self.root / "cad")), \
                patch.object(IndexProcess, "install_runtime", return_value=str(self.root / "helper")) as install:
            expected = str(self.root / "helper/python/python.exe")
            self.assertEqual(builder._python(threading.Event()), expected)
            self.assertEqual(builder._python(threading.Event()), expected)
            install.assert_called_once()
            self.assertEqual(install.call_args.kwargs["assets"], assets[-1:])
            self.assertEqual(install.call_args.kwargs["archive_directory"], self.root / "cad/.archives")
            install.call_args.args[2]("installing")
        cancelled = threading.Event()
        def interrupted(_cancel):
            cancelled.set()
            raise ValueError("cancelled install")
        with patch.object(builder, "_python", side_effect=interrupted):
            self.assertFalse(builder(self.source, cancelled))
        with patch.object(builder, "_python", side_effect=ValueError("invalid runtime")):
            with self.assertRaisesRegex(ValueError, "invalid runtime"):
                builder(self.source, threading.Event())

    def test_cancellation_during_result_restore_refuses_the_completed_child(self):
        cancelled = threading.Event()
        def restored(_identity):
            cancelled.set()
            return LayerMotionIndex()
        with patch.object(self.builder, "_run", return_value=True), \
                patch.object(IndexProcess.PersistentIndexCache, "load", side_effect=restored):
            self.assertFalse(self.builder(self.source, cancelled))

    def test_worker_diagnostics_are_bounded_and_a_running_child_is_reaped(self):
        child = Mock(returncode=0)
        child.poll.return_value = None
        def launch(*args, **kwargs):
            kwargs["stdout"].write(b"x" * (64*1024 + 1))
            kwargs["stdout"].flush()
            return child
        with patch.object(IndexProcess.subprocess, "Popen", side_effect=launch):
            with self.assertRaisesRegex(ValueError, "excessive diagnostics"):
                self.builder._run(sys.executable, self.source, self.root, "compact", threading.Event())
        child.kill.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)
        child = Mock(returncode=1)
        child.poll.return_value = 1
        with patch.object(IndexProcess.subprocess, "Popen", return_value=child):
            with self.assertRaisesRegex(ValueError, "Index worker failed"):
                self.builder._run(sys.executable, self.source, self.root, "compact", threading.Event())
        child.kill.assert_not_called()

    def test_prelaunch_and_postexit_cancellation_and_invalid_progress_are_contained(self):
        for postexit in (False, True):
            cancelled = threading.Event()
            child = Mock(returncode=0)
            def launch(*args, cancelled=cancelled, child=child, **kwargs):
                cancelled.set()
                return child
            if not postexit:
                cancelled.set()
            child.poll.return_value = 0
            with patch.object(IndexProcess.subprocess, "Popen", side_effect=launch) as start:
                self.assertFalse(self.builder._run(sys.executable, self.source, self.root, "compact", cancelled))
            self.assertEqual(start.call_count, int(postexit))
        seen = []
        for raw in ("bad", "nan", "-1", "2", "0.5"):
            (self.root / "progress").write_text(raw, encoding="ascii")
            self.builder._progress(self.root, seen.append)
        self.builder._progress(self.root, None)
        self.assertEqual(seen, [.5])


class IndexProcessPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mpf prepare test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builder = IndexProcess.IndexProcessBuilder(self.root, executable=sys.executable)

    def task(self, path, *, compact=True):
        index = build_index_from_file(path, compact=compact)
        return SimpleNamespace(index=index, start=0, packed=SimpleNamespace(peek=lambda layer: None),
            table=None, read_prepared=lambda layer: None, failed_layers=frozenset(),
            lease=SimpleNamespace(path=path), writer=None, store=None, foreground=threading.Event())

    def test_isolated_preparation_matches_inline_payloads_without_hydrating_parent(self):
        from mpf.gcode.PlateProgress import prepare_layer, encode_layer
        for path in sorted(Path(__file__).with_name("fixtures").joinpath("gcode").glob("*.gcode")):
            for compact in (True, False):
                with self.subTest(file=path.name, compact=compact):
                    task = self.task(path, compact=compact)
                    original_hydrated = set(task.index.hydrated_layers)
                    expected = build_index_from_file(path, compact=False)
                    received = {}
                    reports = []
                    while task.start < len(task.index.ranges):
                        frontier, encoded, refused = self.builder.prepare(
                            task, threading.Event(), progress=lambda encoded, refused, reports=reports: reports.append(dict(encoded)))
                        self.assertGreater(frontier, task.start)
                        self.assertFalse(refused)
                        received.update(encoded)
                        task.start = frontier
                    for layer in range(len(expected.ranges)):
                        payload = prepare_layer(expected, layer)
                        if payload is not None:
                            self.assertEqual(received[layer], encode_layer(payload))
                    self.assertEqual({layer: raw for batch in reports for layer, raw in batch.items()}, received)
                    self.assertEqual(task.index.hydrated_layers, original_hydrated)

    def test_existing_prepared_and_uncacheable_slots_need_no_source_or_child(self):
        from mpf.gcode.PreparedStore import STATE_CACHED, STATE_UNCACHEABLE
        path = self.root / "part.gcode"
        path.write_bytes(b";LAYER:0\nG1 X1 Y1 E1\n;LAYER:1\nG1 X2 Y2 E2\n")
        task = self.task(path)
        task.lease = None
        task.table = [(STATE_CACHED, 1, 4), (STATE_UNCACHEABLE, 0, 0)]
        task.read_prepared = lambda layer: b"PPL1" if layer == 0 else None
        with patch.object(self.builder, "_run") as run:
            frontier, encoded, refused = self.builder.prepare(task, threading.Event())
        run.assert_not_called()
        self.assertEqual((frontier, encoded, refused), (2, {0:b"PPL1"}, {1}))

    def test_foreground_demand_kills_and_joins_speculative_child(self):
        path = self.root / "dense.gcode"
        path.write_bytes(b";LAYER:0\n" + b"G1 X1 Y2 E0.1\n" * 100_000)
        task = self.task(path)
        launch = subprocess.Popen
        processes, directories, timers = [], [], []
        def recorded(*args, **kwargs):
            process = launch(*args, **kwargs)
            processes.append(process)
            directories.append(Path(kwargs["cwd"]))
            timer = threading.Timer(.04, task.foreground.set)
            timer.start()
            timers.append(timer)
            return process
        with patch.object(IndexProcess.subprocess, "Popen", recorded):
            self.assertEqual(self.builder.prepare(task, threading.Event()), (0, {}, set()))
        for timer in timers:
            timer.join()
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertFalse(directories[0].exists())

    def test_cancelled_batch_does_not_start(self):
        path = self.root / "part.gcode"
        path.write_bytes(b";LAYER:0\nG1 X1 Y1 E1\n")
        task = self.task(path)
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(self.builder, "_run") as run:
            self.assertEqual(self.builder.prepare(task, cancelled), (0, {}, set()))
        run.assert_not_called()

    def test_small_result_reads_only_its_file_size_and_oversize_is_rejected(self):
        output = self.root
        (output / "result.json").write_text(json.dumps({
            "encoded": [0], "uncacheable": [], "processed": [0]}), encoding="utf-8")
        payload = b"PPL1" + b"x" * 100
        (output / "layer-0.ppl").write_bytes(payload)
        reads = []
        original_open = Path.open
        class Tracked:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def read(self, size):
                reads.append(size)
                return self.handle.read(size)
        def opened(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            return Tracked(handle) if path.suffix == ".ppl" else handle
        with patch.object(Path, "open", opened):
            self.assertEqual(self.builder._prepared_result(output, [0]), ({0: payload}, [], [0]))
        self.assertEqual(reads, [len(payload)+1])
        with patch.object(IndexProcess.os, "fstat", return_value=SimpleNamespace(st_size=64*1024*1024+1)):
            with self.assertRaisesRegex(ValueError, "Invalid prepared layer"):
                self.builder._prepared_result(output, [0])

    def test_preparation_reuses_cached_layers_and_requires_a_lease_only_for_missing_data(self):
        path = self.root / "part.gcode"
        path.write_bytes(b";LAYER:0\nG1 X1 Y1 E1\n;LAYER:1\nG1 X2 Y2 E2\n")
        task = self.task(path)
        task.failed_layers = frozenset({1})
        task.packed = SimpleNamespace(peek=lambda layer: b"PPL1")
        task.store = Mock()
        task.writer = object()
        self.assertEqual(self.builder.prepare(task, threading.Event()), (2, {0: b"PPL1"}, set()))
        task.store.append.assert_called_once_with(task.writer, 0, b"PPL1")
        task.packed = SimpleNamespace(peek=lambda layer: None)
        task.lease = None
        with self.assertRaisesRegex(ValueError, "source lease"):
            self.builder.prepare(task, threading.Event())

    def test_corrupt_or_inconsistent_prepared_results_are_refused(self):
        for result in (
                {"encoded": [0], "uncacheable": [0], "processed": [0]},
                {"encoded": [0], "uncacheable": [], "processed": []},
                {"encoded": [], "uncacheable": [], "processed": [1]},
                {"encoded": "0", "uncacheable": [], "processed": [0]}):
            with self.subTest(result=result):
                (self.root / "result.json").write_text(json.dumps(result), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "worker result"):
                    self.builder._prepared_result(self.root, [0])
        (self.root / "result.json").write_text(json.dumps({
            "encoded": [0], "uncacheable": [], "processed": [0]}), encoding="utf-8")
        (self.root / "layer-0.ppl").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "prepared layer result"):
            self.builder._prepared_result(self.root, [0])

    def test_failed_child_or_foreground_arrival_never_publishes_speculative_results(self):
        path = self.root / "part.gcode"
        path.write_bytes(b";LAYER:0\nG1 X1 Y1 E1\n")
        for outcome in (False, ValueError("broken child"), "foreground"):
            with self.subTest(outcome=outcome):
                task = self.task(path)
                task.store = Mock()
                task.writer = object()
                def run(*args, outcome=outcome, **kwargs):
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome is not False
                def result(*args, task=task):
                    task.foreground.set()
                    return {0: b"PPL1"}, [], [0]
                with patch.object(self.builder, "_run", side_effect=run), \
                        patch.object(self.builder, "_prepared_result", side_effect=result):
                    if isinstance(outcome, Exception):
                        with self.assertRaisesRegex(ValueError, "broken child"):
                            self.builder.prepare(task, threading.Event())
                    else:
                        self.assertEqual(self.builder.prepare(task, threading.Event()), (0, {}, set()))
                task.store.append.assert_not_called()


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class IndexProcessLifecycleTests(unittest.TestCase):
    def test_isolated_background_pass_publishes_a_complete_reopenable_store(self):
        import time
        from PyQt6.QtCore import QObject, pyqtSignal
        from mpf.gcode.PreparedStore import PreparedCache
        from mpf.gcode.PlateProgress import prepare_layer, encode_layer
        class Signals(QObject):
            changed = pyqtSignal()
        with tempfile.TemporaryDirectory() as private, runtime(isolated_index=True) as qt:
            path = Path(private, "part.gcode")
            path.write_bytes(b"M83\n" + b"".join(
                b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\n" % layer for layer in range(40)))
            index = build_index_from_file(path, compact=True)
            expected = build_index_from_file(path, compact=False)
            signals = Signals()
            identity = CacheIdentity("part", filename="part.gcode", modified=1)
            files = SimpleNamespace(changed=signals.changed, identity=identity, job_key=("part",),
                lease=lambda: SimpleNamespace(path=path, close=lambda: None))
            store = PreparedCache(str(Path(private, "prepared")))
            builder = qt.load("IndexProcess").IndexProcessBuilder(private)
            service = qt.load("GCodeIndexService").GCodeIndexService(
                files, None, prepared=store, index_builder=builder)
            try:
                service._job, service._wanted, service._restored = files.job_key, True, True
                service._view = qt.load("IndexView").IndexView(files.job_key, index)
                service._prepared.open(identity)
                service._advance()
                deadline = time.monotonic() + 15
                while not service._prepared.saved and not service._pass_error and time.monotonic() < deadline:
                    qt.events(10)
                self.assertEqual(service._pass_error, "")
                self.assertTrue(service._prepared.saved)
                self.assertEqual(service.plate_pass_fraction(), 1.0)
                self.assertEqual(service._full_next, 40)
                table = store.load_table("part")
                self.assertTrue(table["complete"])
                for layer in (0, 31, 32, 39):
                    self.assertEqual(store.read("part", table["table"], layer),
                                     encode_layer(prepare_layer(expected, layer)))
                self.assertFalse(index.hydrated_layers, "background preparation must not hydrate host arrays")
            finally:
                service.close()

    def test_late_progress_cannot_overwrite_a_new_generation(self):
        with runtime() as qt:
            from PyQt6.QtCore import QObject, pyqtSignal
            class Signals(QObject):
                changed = pyqtSignal()
            signals = Signals()
            lease = SimpleNamespace(path="source.gcode", close=lambda: None)
            files = SimpleNamespace(changed=signals.changed, identity=CacheIdentity("source"),
                                    job_key=("source",), lease=lambda: lease)
            reports, queued = [], []
            def build(path, cancel, *, progress):
                reports.append((path, cancel))
                progress(.75)
                return LayerMotionIndex()
            service = qt.load("GCodeIndexService").GCodeIndexService(files, None, index_builder=build)
            try:
                service._job, service._wanted, service._restored = files.job_key, True, True
                service._submit = lambda kind, work, lease: queued.append((kind, work, lease))
                service._advance()
                self.assertEqual(queued[0][0], "build")
                self.assertIs(queued[0][2], lease)
                service._generation += 1
                service._progress = None
                queued[0][1]()
                self.assertIsNone(service.progress)
                self.assertEqual(reports[0][0], "source.gcode")
                service._on_preparation_progress(service._generation-1, {0: b"stale"}, ())
                self.assertIsNone(service._full_cache.peek(0))
            finally:
                service.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows composition")
    def test_windows_runtime_selects_process_builder(self):
        with runtime(isolated_index=True) as qt:
            app = qt.Application()
            follower = qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
            try:
                builder = follower._runtime.index._index_builder
                self.assertEqual(type(builder).__name__, "IndexProcessBuilder")
                self.assertEqual(builder._executable, sys.executable)
                self.assertTrue(builder._compact)
            finally:
                follower.deinitialize()


if __name__ == "__main__":
    unittest.main()
