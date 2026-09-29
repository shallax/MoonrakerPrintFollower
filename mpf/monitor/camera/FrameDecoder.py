# Copyright (c) 2018 Aldo Hoeben / fieldOfView
# NetworkMJPGImage is released under the terms of the LGPLv3 or higher.
# Camera pipeline component extracted for Moonraker Print Follower.

from __future__ import annotations

import queue
import time
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader
from UM.Logger import Logger

# The decode threads that outlived their items (a decode that will not
# return inside the shutdown wait). A QThread destroyed while running
# aborts the process, so an item that could not join its worker hands
# it here instead: the thread still exits at its own next loop turn,
# and nothing it emits has a receiver left to reach.
_ORPHANED_DECODERS: list = []


def _decode_jpeg(frame: bytes) -> QImage:
    """The one place a JPEG becomes a QImage.

    Module-level so the thread it runs on is a testable fact rather
    than an implementation detail: the worker calls this, and nothing
    Qt-affine is in it — a bytes payload in, a value type out."""
    # PyQt's QImage.fromData holds the GIL throughout the native decode.
    # QImageReader.read releases it, so this worker cannot block Python
    # callbacks on the GUI thread for the duration of every camera frame.
    buffer = QBuffer()
    buffer.setData(QByteArray(frame))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    return reader.read()


class FrameDecoder(QThread):
    """The JPEG decode, off the Qt thread.

    What crosses the boundary is a value and nothing else: the frame's
    bytes go in, a QImage comes back through a queued signal (Qt's
    implicit sharing makes the hand-over a pointer swap), and the item
    that installs and paints it never leaves its own thread. The item,
    its QML bindings and the network reply are all still touched from
    the Qt thread only.

    One decode is in flight at a time, and the tick that submits is the
    rate cap's: a fast source therefore cannot pile work up here, and a
    superseded decode is dropped by generation rather than displayed.
    """

    # generation, (the decoded image or None for a failed decode, the
    # decode's own milliseconds, the wait for the worker, the emit's
    # timestamp). The last two travel WITH the result so the Qt thread
    # can split the round trip without a shared clock: how long the
    # frame waited in the queue is the worker's to measure, and how long
    # the result then waited for the Qt thread is the Qt thread's.
    #
    # Every stamp here and beside it is perf_counter. The round trip is
    # read as the SUM of its shares, and a share measured on a clock of
    # its own resolution cannot be added to one that is not: monotonic
    # on Windows is GetTickCount64, quantised to the 15.625 ms tick, so
    # a queue wait or a return read there is wrong by up to a tick
    # against a decode that is not.
    decoded = pyqtSignal(int, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: queue.Queue = queue.Queue()

    def submit(self, generation: int, frame: bytes) -> None:
        self._queue.put((generation, frame, time.perf_counter()))

    def stop(self) -> bool:
        """Join the worker: an item must not be destroyed under a live
        thread, and a decode is bounded by one frame's own size. False
        means the join failed and the thread was orphaned instead."""
        if not self.isRunning():
            return True
        self._queue.put(None)
        if self.wait(2000):
            return True
        # A decode that outlives the wait: the item is about to die and
        # this thread must not die with it.
        Logger.log("w", "Moonraker MJPEG: the frame decoder did not stop in time")
        _ORPHANED_DECODERS.append(self)
        self.setParent(None)
        return False

    def run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            generation, frame, submitted = item
            picked = time.perf_counter()
            started = time.perf_counter()
            image = _decode_jpeg(frame)
            elapsed = (time.perf_counter() - started) * 1000.0
            self.decoded.emit(
                generation,
                (None if image.isNull() else image, elapsed,
                 (picked - submitted) * 1000.0, time.perf_counter()))
