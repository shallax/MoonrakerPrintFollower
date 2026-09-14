from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass
from typing import BinaryIO


@dataclass
class DownloadTarget:
    path: str
    handle: BinaryIO
    bytes_written: int = 0

    @classmethod
    def open(cls, path: str) -> "DownloadTarget":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return cls(path=path, handle=open(path, "wb"), bytes_written=0)

    def write(self, data) -> int:
        if not data:
            return 0
        raw = bytes(data)
        self.handle.write(raw)
        self.bytes_written += len(raw)
        return len(raw)

    def flush_close(self) -> None:
        if self.handle is None:
            return
        try:
            # This file is an intra-process temporary cache, not durable user
            # data. Forcing it to stable storage can add seconds on large G-code
            # files and provides no benefit before Cura immediately reads it.
            self.handle.flush()
        finally:
            self.handle.close()

    def abort(self, remove: bool = True) -> None:
        try:
            self.handle.close()
        except Exception:
            pass
        if remove:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


class DownloadOperation:
    """One streamed download end to end: operation-local queue, target,
    byte counters and writer thread. The writer loop runs with THIS
    object bound and reads only operation fields, so a writer from a
    retired operation can neither adopt a later operation's queue or
    target nor consume its sentinel; `stop()` wakes exactly this
    operation's writer.

    `on_writer_done`/`on_writer_drained` are plain callables invoked
    from the writer thread; the service installs signal emits (the
    GCodeIndexService idiom) so delivery lands on the GUI thread.
    """

    # Bounded buffering: above the high-water mark the drain loop stops
    # reading and leaves bytes in Qt's reply buffer (whose own cap then
    # throttles the socket); the writer fires on_writer_drained once the
    # backlog crosses below the low-water mark. A blocking put() on the
    # GUI thread is forbidden by design — pausing the read is the
    # mechanism.
    HIGH_WATER_BYTES = 8 * 1024 * 1024
    LOW_WATER_BYTES = 4 * 1024 * 1024

    def __init__(self, target, reply, size, generation, job):
        self.target = target
        self.reply = reply
        self.size = size  # the declared Content-Length; 0 when absent
        self.generation = generation
        self.job = job
        self.received = 0
        self.written = 0
        self.writer_error = None
        self.aborted = False
        self.finished_reading = False
        self.reading_paused = False
        self.backed_up = False
        self.sentinel_put = False
        self.queue = queue.Queue()
        self.on_writer_done = None
        self.on_writer_drained = None
        self._writer = None

    def start(self):
        self._writer = threading.Thread(target=self._writer_main, daemon=True)
        self._writer.start()

    def stop(self):
        """Signal the writer to finish; safe from the GUI thread."""
        if not self.sentinel_put:
            self.sentinel_put = True
            self.queue.put(None)

    def abort(self):
        """Retire this operation: mark it, signal the writer, close the
        target. The reply stays the service's to abort — its signals are
        GUI-thread affairs."""
        self.aborted = True
        self.stop()
        try:
            self.target.abort(remove=False)
        except Exception:
            pass

    def _writer_main(self):
        try:
            while True:
                chunk = self.queue.get()
                if chunk is None:
                    break
                self.target.write(chunk)
                self.written += len(chunk)
                # One drain-resume per above->below crossing; the
                # service's handler hops back to the GUI thread.
                was_backed_up = self.backed_up
                self.backed_up = (self.received - self.written) > self.LOW_WATER_BYTES
                if was_backed_up and not self.backed_up and self.on_writer_drained is not None:
                    self.on_writer_drained()
        except Exception as error:
            self.writer_error = str(error)
        finally:
            if not self.aborted:
                try:
                    # The fd's own thread closes it; the GUI thread never
                    # flushes or joins (the freeze rule).
                    self.target.flush_close()
                except Exception as error:
                    self.writer_error = self.writer_error or str(error)
            if self.on_writer_done is not None:
                try:
                    self.on_writer_done()
                except RuntimeError:
                    pass  # the service is gone at shutdown
