# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history:
# 2026-10-20 [326436d] Speed up Windows indexing and parallel layer preparation.

"""Disposable Windows scan/preparation workers with bounded result handoff."""
from __future__ import annotations

from array import array
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from ...gcode.IndexCache import PersistentIndexCache
from ...gcode.MotionIndex import LayerMotionIndex
from ...gcode.SourceIdentity import CacheIdentity
from ...gcode.PreparedStore import STATE_UNCACHEABLE
from ...toolhead.CadRuntime import install_runtime, runtime_assets, runtime_directory


class IndexProcessBuilder:
    def __init__(self, storage_root, *, executable=None, compact=True, workers=None):
        self._root = Path(storage_root)
        self._executable = executable
        self._compact = compact
        self._verified_executable = None
        self._workers = max(1, min(8, (os.cpu_count() or 2)//4)) if workers is None else max(1, min(8, workers))

    def _python(self, cancelled):
        if self._executable is not None:
            return str(self._executable)
        if self._verified_executable is not None:
            return self._verified_executable
        assets = runtime_assets()
        # A STEP installation already retains the pinned helper archive. Reuse
        # those verified bytes, without making indexing depend on CAD packages.
        archives = Path(runtime_directory(self._root / "cad-runtime", assets), ".archives")
        runtime = install_runtime(self._root / "index-runtime", cancelled, lambda _text: None,
                                  assets=assets[-1:], archive_directory=archives)
        self._verified_executable = str(Path(runtime, "python", "python.exe"))
        return self._verified_executable

    def __call__(self, path, cancel_event, *, progress=None):
        if cancel_event.is_set():
            return LayerMotionIndex()
        try:
            executable = self._python(cancel_event)
        except Exception:
            if cancel_event.is_set():
                return LayerMotionIndex()
            raise
        # The raw source lease is held by GCodeIndexService until this returns.
        # No persistent cache is written by the child: stale/cancelled results
        # disappear with this private directory and cannot resurrect a clear.
        with tempfile.TemporaryDirectory(prefix="mpf-index-") as private:
            output = Path(private)
            if not self._run(executable, path, output, "compact" if self._compact else "full",
                             cancel_event, progress=progress):
                return LayerMotionIndex()
            if (output / "empty").is_file():
                return LayerMotionIndex()
            index = PersistentIndexCache(private).load(CacheIdentity("worker"))
            if index is None:
                raise ValueError("Index worker returned an invalid index")
            if cancel_event.is_set():
                return LayerMotionIndex()
            if progress is not None:
                progress(1.0)
            return index

    def prepare(self, task, cancelled, *, progress=None):
        """Run one background batch; only packed bytes return to the host.

        The existing session still owns all persistent writes and coverage.
        Foreground demand cancels this speculative batch before the service
        schedules the demand, with no child capable of publishing afterward.
        """
        encoded, uncacheable, layers, resolved = {}, set(), [], set()
        end = min(len(task.index.ranges), task.start + 32*self._workers)
        for layer in range(task.start, end):
            if cancelled.is_set() or task.foreground.is_set():
                return task.start, encoded, uncacheable
            if layer in task.failed_layers:
                resolved.add(layer)
                continue
            raw = task.packed.peek(layer)
            if raw is None and task.table is not None:
                if layer < len(task.table) and task.table[layer][0] == STATE_UNCACHEABLE:
                    uncacheable.add(layer)
                    resolved.add(layer)
                    continue
                raw = task.read_prepared(layer)
            if raw is None:
                layers.append(layer)
            else:
                encoded[layer] = raw
                resolved.add(layer)
                if task.writer is not None:
                    task.store.append(task.writer, layer, raw)
        if not layers:
            return end, encoded, uncacheable
        if task.lease is None:
            raise ValueError("Isolated preparation needs a G-code source lease")
        executable = self._python(cancelled)
        with tempfile.TemporaryDirectory(prefix="mpf-prepare-") as private:
            output = Path(private)
            # Strip resident arrays even for an old non-compact cache. The
            # child rehydrates only its own batch, using identical modal seeds.
            snapshot = self._boundaries(task.index)
            PersistentIndexCache(private).save(CacheIdentity("worker"), snapshot)
            count = min(self._workers, (len(layers)+31)//32)
            requests = []
            for number in range(count):
                destination = output / str(number)
                destination.mkdir()
                requested = layers[number::count]
                (destination / "request.json").write_text(json.dumps({
                    "layers": requested, "start": task.start, "end": end,
                    "index_root": private}), encoding="utf-8")
                requests.append((destination, requested))
            stopped = threading.Event()
            class Demand:
                def is_set(self):
                    return task.foreground.is_set() or stopped.is_set()
            # Waiting threads do no geometry work; each child has its own GIL.
            # Interleave adjacent layers across workers so the completed prefix
            # advances even when the work budget cuts several partitions short.
            with ThreadPoolExecutor(max_workers=count, thread_name_prefix="IndexProcessWait") as pool:
                futures = {pool.submit(self._run, executable, task.lease.path, destination,
                                       "prepare", cancelled, foreground=Demand()): (destination, requested)
                           for destination, requested in requests}
                try:
                    for future in as_completed(futures):
                        if not future.result():
                            stopped.set()
                            return task.start, encoded, uncacheable
                        destination, requested = futures[future]
                        completed, refused, processed = self._prepared_result(destination, requested)
                        resolved.update(processed)
                        uncacheable.update(refused)
                        for layer, raw in completed.items():
                            if cancelled.is_set() or task.foreground.is_set():
                                stopped.set()
                                return task.start, encoded, uncacheable
                            encoded[layer] = raw
                            if task.writer is not None:
                                task.store.append(task.writer, layer, raw)
                        if progress is not None:
                            progress(completed, tuple(refused))
                except Exception:
                    stopped.set()
                    raise
            frontier = task.start
            while frontier < end and frontier in resolved:
                frontier += 1
            return frontier, encoded, uncacheable

    @staticmethod
    def _prepared_result(output, requested):
        with (output / "result.json").open("r", encoding="utf-8") as result_file:
            result = json.loads(result_file.read(4096))
        completed, refused, processed = result["encoded"], result["uncacheable"], result["processed"]
        if (not all(isinstance(value, list) for value in (completed, refused, processed))
                or not set(processed).issubset(requested)
                or not set(completed + refused).issubset(processed)
                or set(completed) & set(refused)):
            raise ValueError("Invalid preparation worker result")
        encoded = {}
        for layer in completed:
            with (output / f"layer-{layer}.ppl").open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                if size > 64*1024*1024:
                    raise ValueError("Invalid prepared layer result")
                raw = source.read(size + 1)
            if len(raw) > 64*1024*1024 or not raw.startswith(b"PPL1"):
                raise ValueError("Invalid prepared layer result")
            encoded[layer] = raw
        return encoded, refused, processed

    @staticmethod
    def _boundaries(index):
        count = len(index.ranges)
        columns = {name: [array(code) for _ in range(count)] for name, code in (
            ("motion_offsets", "Q"), ("motion_x", "f"), ("motion_y", "f"),
            ("motion_z", "f"), ("motion_extrusion", "f"), ("motion_speeds", "f"), ("motion_tools", "H"))}
        columns.update({name: [[] for _ in range(count)] for name in (
            "motion_types", "travel_starts", "travel_ends", "extruder_events", "firmware_retractions")})
        return replace(index, **columns, motion_arcs=[{} for _ in range(count)],
                       compact=True, hydrated_layers=set(),
                       layer_motion_counts=[index.motion_count(layer) for layer in range(count)])

    def _run(self, executable, path, output, mode, cancelled, *, progress=None, foreground=None):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("PYTHON", "DYLD_", "LD_", "QT_"))}
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name == "nt" else {}
        process = None
        with (output / "worker.log").open("wb") as log:
            try:
                if cancelled.is_set() or (foreground is not None and foreground.is_set()):
                    return False
                process = subprocess.Popen(
                    [executable, "-I", "-B", str(Path(__file__).with_name("IndexWorker.py")),
                     os.path.abspath(path), str(output), mode], stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, cwd=str(output), env=env, **options)
                while process.poll() is None:
                    if cancelled.wait(.025) or (foreground is not None and foreground.is_set()):
                        return False
                    self._progress(output, progress)
                    if (output / "worker.log").stat().st_size > 64*1024:
                        raise ValueError("Index worker produced excessive diagnostics")
                if cancelled.is_set() or (foreground is not None and foreground.is_set()):
                    return False
                if process.returncode:
                    with (output / "worker.log").open("rb") as diagnostics:
                        detail = diagnostics.read(2048).decode("utf-8", "replace").strip()
                    raise ValueError(detail or "Index worker failed")
                return True
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    @staticmethod
    def _progress(output, callback):
        if callback is None:
            return
        try:
            with (output / "progress").open("r", encoding="ascii") as handle:
                fraction = float(handle.read(64))
            if math.isfinite(fraction) and 0 <= fraction <= 1:
                callback(fraction)
        except (OSError, ValueError):
            pass
