from __future__ import annotations

import os
import shutil
import tempfile
import time
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply

from .MoonrakerProtocol import RemoteFileIdentity
from .DownloadStream import DownloadOperation, DownloadTarget
from .MoonrakerProtocol import download_endpoint, metadata_endpoint, parse_file_identity


class FileLease:
    """A consumer's explicit ownership of a cached file, released on the UI thread."""
    def __init__(self, path, release):
        self.path = path
        self._release = release

    def close(self):
        release, self._release = self._release, None
        if release is not None:
            release(self.path)


class _OneShotDownload:
    """The file-manager Download lane: one file, one stream, into a
    fresh temp directory. Independent of the job-bound state machine
    (the file-manager ruling: Download loads the file into
    Cura), with the same byte cap and lane discipline."""

    def __init__(self, transport, relpath, root, on_ready):
        self._transport = transport
        self._relpath = str(relpath)
        self._on_ready = on_ready
        self._received = 0
        self._done = False
        try:
            self._directory = tempfile.mkdtemp(prefix="file-", dir=root)
            name = os.path.basename(self._relpath.replace("\\", "/")) or "download.gcode"
            if os.path.splitext(name)[1].lower() not in {".g", ".gcode"}:
                name += ".gcode"
            self._path = os.path.join(self._directory, name)
            self._target = DownloadTarget.open(self._path)
            request = transport.request(download_endpoint(transport.identity[0], self._relpath), timeout_ms=30000)
            request.setRawHeader(b"Accept", b"application/octet-stream")
            self._reply = transport.network.get(request)
            self._reply.setReadBufferSize(4 * 1024 * 1024)
            self._reply.readyRead.connect(self._drain)
            self._reply.finished.connect(self._finish)
        except Exception as error:
            self._abort()
            self._finish_immediately(str(error))

    def _drain(self):
        if self._done:
            return
        try:
            chunk = bytes(self._reply.readAll())
            self._received += len(chunk)
            if self._received > RemoteFileService.MAX_DOWNLOAD_BYTES:
                self._abort()
                self._finish_immediately("Download exceeds the size cap")
                return
            if chunk:
                self._target.write(chunk)
        except Exception as error:
            self._abort()
            self._finish_immediately(str(error))

    def _finish(self):
        if self._done:
            return
        self._done = True
        error = None
        try:
            if self._reply.error() != QNetworkReply.NetworkError.NoError:
                error = self._reply.errorString()
            else:
                self._target.flush_close()
                self._on_ready(self._path, None)
                return
        except Exception as exc:
            error = str(exc)
        self._abort()
        self._on_ready(None, error)

    def _finish_immediately(self, error):
        if self._done:
            return
        self._done = True
        self._abort()
        self._on_ready(None, error)

    def _abort(self):
        try:
            self._target.abort(remove=False)
        except Exception:
            pass
        shutil.rmtree(self._directory, ignore_errors=True)


class RemoteFileService(QObject):
    """Own metadata, streamed downloads and leased files; no Cura/index knowledge.

    Download identity and metadata completeness are separate: a failed metadata
    request installs a fallback identity so downloads can proceed, but retries
    with backoff until a real response arrives. Only a successful response
    marks the run's metadata complete. A failed download retries on its own
    backoff ladder, driven by consumers re-requesting the file.
    """
    changed = pyqtSignal()
    failed = pyqtSignal(str)
    # The writer thread emits these; queued delivery lands them on the
    # GUI thread (the GCodeIndexService idiom). The op payload is how a
    # stale writer's terminal is told apart from the current one.
    writerDone = pyqtSignal(object)
    writerDrained = pyqtSignal(object)

    METADATA_RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)
    DOWNLOAD_RETRY_DELAYS_MS = (2000, 5000, 15000, 60000)
    # Download byte cap (panel security P2-3): the equality check against
    # the server-declared size is the only other guard, and a hostile or
    # stale endpoint simply lies about it. Real prints are well under a
    # gigabyte; 2 GiB is headroom beyond generous.
    MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

    def __init__(self, transport, parent=None, *, target_factory=DownloadTarget.open):
        super().__init__(parent)
        self._transport = transport
        self._root = tempfile.mkdtemp(prefix="cura-moonraker-files-")
        # Injected so the gated-writer regressions need no private-field
        # patching.
        self._target_factory = target_factory
        self._generation = 0
        self._job = None
        self._identity = None
        self._metadata = {}
        self._metadata_pending = False
        self._metadata_fetched = False
        self._metadata_attempts = 0
        self._metadata_retry_at = 0.0
        self._path = None
        self._download = None
        self._want_file = False
        self._leases = {}
        self._retired = set()
        self._closed = False
        self._error = ""
        self._download_attempts = 0
        self._download_retry_at = 0.0
        self._lifetime_received = 0
        self.writerDone.connect(self._on_writer_done)
        self.writerDrained.connect(self._on_writer_drained)

    @property
    def download_fraction(self):
        """0..1 of the in-flight download, or None when nothing is
        downloading. The denominator is the response's declared
        Content-Length — the transfer authority; a transfer without
        one renders indeterminate."""
        op = self._download
        if op is None or op.size <= 0:
            return None
        return max(0.0, min(1.0, op.received / op.size))

    @property
    def job_key(self): return self._job
    @property
    def identity(self): return self._identity
    @property
    def metadata(self): return MappingProxyType(self._metadata)
    @property
    def metadata_complete(self): return self._metadata_fetched
    @property
    def path(self): return self._path
    @property
    def error(self): return self._error
    @property
    def phase(self):
        if self._error: return "error"
        if self._download is not None: return "downloading"
        if self._metadata_pending: return "resolving"
        return "ready" if self._path else "idle"

    def download_once(self, relpath, *, on_ready):
        """The file-manager Download capability: stream one file into
        a fresh temp location and report `on_ready(path, error)` once.
        The job-bound state machine is untouched."""
        if not hasattr(self, "_one_shots"):
            self._one_shots = set()
        download = None
        def done(path, error):
            if download is not None and download in self._one_shots:
                self._one_shots.discard(download)
            on_ready(path, error)
        download = _OneShotDownload(self._transport, relpath, self._root, done)
        # KEEP THE REFERENCE: the reply's signals hold bound methods
        # of this object, and a garbage-collected downloader dies
        # silently mid-stream.
        self._one_shots.add(download)
        return download

    def bind(self, job_key):
        if self._job == job_key and not self._closed:
            return
        self._generation += 1
        self._transport.cancel_owner("files")
        self._abort_download()
        if self._path: self._retire(self._path)
        self._job = job_key
        self._identity = self._path = None
        self._metadata = {}
        self._metadata_pending = self._want_file = False
        self._metadata_fetched = False
        self._metadata_attempts = 0
        self._metadata_retry_at = 0.0
        self._error = ""
        self._download_attempts = 0
        self._download_retry_at = 0.0
        self.changed.emit()

    def request_metadata(self):
        if self._closed or not self._job or self._metadata_pending or self._metadata_fetched:
            return
        if self._identity is not None and time.monotonic() < self._metadata_retry_at:
            return  # inside the backoff window; the fallback identity already unblocks downloads
        self._metadata_pending = True
        generation, job = self._generation, self._job
        def finished(payload, error):
            if generation != self._generation or job != self._job or self._closed:
                return
            self._metadata_pending = False
            result = (payload or {}).get("result", {})
            self._metadata = dict(result) if isinstance(result, dict) else {}
            if error:
                self._metadata_attempts += 1
                delay = self.METADATA_RETRY_DELAYS_MS[min(self._metadata_attempts - 1, len(self.METADATA_RETRY_DELAYS_MS) - 1)]
                self._metadata_retry_at = time.monotonic() + delay / 1000.0
                if self._identity is None:
                    self._identity = RemoteFileIdentity(job[0], job[1])
                self.changed.emit()
                self._advance()
                return
            self._metadata_fetched = True
            self._metadata_attempts = 0
            self._metadata_retry_at = 0.0
            try:
                self._identity = parse_file_identity(job[0], payload or {}, job[1])
            except (TypeError, ValueError):
                self._identity = RemoteFileIdentity(job[0], job[1])
            self.changed.emit()
            self._advance()
        started = self._transport.send_json("files", "metadata", "GET",
            metadata_endpoint(self._transport.identity[0], job[0]), finished, category="static")
        if not started: self._metadata_pending = False

    def request_file(self, *, retry=False):
        self._want_file = True
        if retry:
            self._error = ""
            self._download_attempts = 0
            self._download_retry_at = 0.0
        self._advance()

    def _advance(self):
        if self._closed or not self._job: return
        if self._error and self._want_file and time.monotonic() >= self._download_retry_at:
            # Inside the download backoff window the error stays latched so
            # every consumer re-request does not hammer the network; once the
            # window passes, the next request restarts the download.
            self._error = ""
            self.changed.emit()
        if self._error: return
        if self._identity is None:
            self.request_metadata()
        elif self._want_file and not self._path and self._download is None:
            self._start_download()

    def _start_download(self):
        generation, job = self._generation, self._job
        directory = tempfile.mkdtemp(prefix="job-", dir=self._root)
        name = os.path.basename(job[0].replace("\\", "/")) or "moonraker.gcode"
        if os.path.splitext(name)[1].lower() not in {".g", ".gcode"}: name += ".gcode"
        reply = None
        try:
            target = self._target_factory(os.path.join(directory, name))
            request = self._transport.request(download_endpoint(self._transport.identity[0], job[0]), timeout_ms=30000)
            request.setRawHeader(b"Accept", b"application/octet-stream")
            reply = self._transport.network.get(request)
            reply.setReadBufferSize(4 * 1024 * 1024)
            declared = reply.rawHeader(b"Content-Length")
            size = int(bytes(declared)) if declared else 0
        except Exception as error:
            if reply is not None:
                reply.abort()
                reply.deleteLater()
            shutil.rmtree(directory, ignore_errors=True)
            self._fail(str(error))
            return
        op = DownloadOperation(target, reply, size, generation, job)
        op.on_writer_done = lambda o=op: self.writerDone.emit(o)
        op.on_writer_drained = lambda o=op: self.writerDrained.emit(o)
        self._download = op
        op.start()
        reply.readyRead.connect(lambda r=reply, o=op: self._drain(o, r))
        reply.finished.connect(lambda r=reply, o=op: self._finish_download(o, r))
        self.changed.emit()

    def _drain(self, op, reply):
        if op is not self._download or op.aborted:
            return
        if op.reading_paused and not op.finished_reading:
            if (op.received - op.written) >= op.LOW_WATER_BYTES:
                return  # still backed up: bytes stay in the reply's buffer
            op.reading_paused = False
        try:
            chunk = bytes(reply.readAll())
            if chunk:
                op.received += len(chunk)
                self._lifetime_received += len(chunk)
                if op.received > self.MAX_DOWNLOAD_BYTES:
                    # Byte cap (panel security P2-3): the only guard was
                    # equality against the SERVER-DECLARED size, which a
                    # hostile or stale endpoint simply lies about. Past
                    # the cap the download aborts and the retry ladder
                    # takes over — unbounded disk fill under /tmp is off.
                    self._abort_download()
                    self._fail("Downloaded G-code exceeds the size cap")
                    return
                op.queue.put(chunk)
                if not op.finished_reading and (op.received - op.written) >= op.HIGH_WATER_BYTES:
                    op.reading_paused = True
        except Exception as error:
            self._abort_download()
            self._fail(str(error))

    def _finish_download(self, op, reply):
        if op is not self._download:
            reply.deleteLater()
            return
        op.finished_reading = True
        self._drain(op, reply)
        if op is not self._download:
            return
        # The writer drains the tail and flush-closes the target, then
        # its done signal completes the operation on the GUI thread.
        # The GUI thread never joins a writer: a stale worker stealing
        # the sentinel left join() blocked forever.
        op.stop()

    def _on_writer_drained(self, op):
        if op is not self._download or op.aborted or op.finished_reading:
            return
        op.reading_paused = False
        self._drain(op, op.reply)

    def _on_writer_done(self, op):
        if op is not self._download:
            return  # a retired operation's writer; everything was cleaned at abort
        self._download = None
        try:
            if op.writer_error is not None:
                raise OSError(op.writer_error)
            if op.generation != self._generation or op.job != self._job:
                self._retire(op.target.path)
                return
            if op.reply.error() != QNetworkReply.NetworkError.NoError:
                raise OSError(op.reply.errorString())
            if op.size > 0 and op.target.bytes_written != op.size:
                raise OSError("Downloaded G-code size mismatch; refusing partial file")
            self._path = op.target.path
            self._download_attempts = 0
            self._download_retry_at = 0.0
            self.changed.emit()
        except Exception as error:
            self._retire(op.target.path)
            self._fail(str(error))
        finally:
            op.reply.deleteLater()

    def _abort_download(self):
        op = self._download
        self._download = None
        if op is None:
            return
        # Retire the operation and signal its writer before aborting the
        # reply: abort() can emit finished synchronously, and the stale
        # reply must find no active operation when it lands.
        op.abort()
        if op.reply is not None:
            op.reply.abort()
            op.reply.deleteLater()
        self._retire(op.target.path)

    def _fail(self, message):
        self._error = str(message)
        self._download_attempts += 1
        delay = self.DOWNLOAD_RETRY_DELAYS_MS[min(self._download_attempts - 1, len(self.DOWNLOAD_RETRY_DELAYS_MS) - 1)]
        self._download_retry_at = time.monotonic() + delay / 1000.0
        self.failed.emit(self._error)
        self.changed.emit()

    def lease(self):
        if self._path is None: return None
        path = self._path
        self._leases[path] = self._leases.get(path, 0) + 1
        return FileLease(path, self._release)

    def _release(self, path):
        count = self._leases.get(path, 0) - 1
        if count > 0: self._leases[path] = count
        else: self._leases.pop(path, None)
        if path in self._retired and path not in self._leases:
            self._retired.remove(path)
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        if self._closed and not self._leases:
            shutil.rmtree(self._root, ignore_errors=True)

    def _retire(self, path):
        self._retired.add(path)
        if path not in self._leases: self._release(path)

    def close(self):
        if self._closed: return
        self.bind(None)
        self._closed = True
        if not self._leases: shutil.rmtree(self._root, ignore_errors=True)
