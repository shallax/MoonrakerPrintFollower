# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Separate model import jobs and named appearance transactions from Qt presentation.

"""One cancellable model-import job, independent of QObject and settings UI."""
from dataclasses import dataclass
import os
import threading
from .CadRuntime import install_runtime
from .ToolheadImport import read_step, read_stl


@dataclass(frozen=True)
class ImportedModel:
    mesh: object
    key: str
    name: str


class ToolheadImportJobs:
    def __init__(self, root, *, progress, completed):
        self.root = root
        self._progress = progress
        self._completed = completed
        self.generation = 0
        self.worker = None
        self.cancelled = threading.Event()
        self.closed = False

    @property
    def busy(self):
        # Occupied until owner-thread receipt, even if the worker just returned.
        return self.worker is not None

    def cancel(self):
        self.generation += 1
        self.cancelled.set()

    def close(self):
        self.closed = True
        self.cancel()

    def receive(self, generation):
        self.worker = None
        return not self.closed and generation == self.generation

    def start(self, path, step):
        if self.busy or self.closed:
            raise RuntimeError("Model import is unavailable")
        self.generation += 1
        generation = self.generation
        self.cancelled = cancel = threading.Event()

        def progress(text):
            self._progress(generation, text)

        def run():
            mesh, error = None, ""
            try:
                if step:
                    runtime = install_runtime(self.root, cancel, progress)
                    progress("Converting STEP assembly…")
                    mesh = read_step(path, runtime, cancel, progress=progress)
                else:
                    mesh = read_stl(path, cancel)
            except Exception as failure:
                error = str(failure) or type(failure).__name__
            try:
                self._completed(generation, ImportedModel(mesh, "", os.path.basename(path)), error, path)
            except RuntimeError:
                pass  # host shutdown may already have retired the signal receiver

        self.worker = threading.Thread(target=run, name="MPF toolhead import", daemon=True)
        self.worker.start()
        return generation
