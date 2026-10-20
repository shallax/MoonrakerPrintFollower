# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-20 — 6.0.0: Separate raster painting, Qt assets and worker publication.
# 2026-10-18 — 6.0.0: Convert empty immutable geometry and event markers to Qt-compatible values.
# 2026-10-01 [02e4bd5] Draw switchable bed-axis arrows across plate views
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-09-21 [f130718] 4.6.0: the native render path — dense toolpath geometry rasterises off the QML JavaScript
#   path (QPainterPath per segment on a worker, the measured winner at ~120 ms for 500k motions), the face
#   composes blitted QImages with role-free reuse, the scrub keeps its vector delta, and the whole pipeline is
#   generation-gated and latest-wins coalesced


"""Worker execution and guarded owner-thread handoff for raster requests."""
import time
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

class _CheckpointBudget:
    """Give interactive rendering time between background geometry chunks."""
    def __init__(self, cancel):
        self.cancel = cancel
        self.deadline = time.monotonic() + 0.008

    def is_set(self):
        if self.cancel.is_set():
            return True
        if time.monotonic() >= self.deadline:
            self.cancel.wait(0.008)
            self.deadline = time.monotonic() + 0.008
        return self.cancel.is_set()


class _RasterJob(QRunnable):
    """A one-shot raster build on Qt's shared pool."""

    def __init__(self, work) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        self._work()


class RasterBridge(QObject):
    """The worker -> owner handoff: the
    worker emits its START and the COMPLETED images through this
    bridge, and the queued cross-thread delivery runs the commit on
    the model's owning thread — workers never touch the model's
    QObjects. The start lets the scheduler count a job superseded
    before it ever ran ."""

    started = pyqtSignal(object)
    done = pyqtSignal(object, object)

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)


def _bridge_emit(bridge, signal, *args):
    """The worker -> owner handoff with a teardown guard: a late
    worker's emit after the bridge's C++ side died (the owner's
    deleteLater) would raise and abort the pool thread — the job is
    dropped instead; no owner remains to commit it. True when the
    emit landed."""
    try:
        getattr(bridge, signal).emit(*args)
        return True
    except RuntimeError:
        return False


