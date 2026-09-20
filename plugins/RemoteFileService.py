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


# The one-shot lane's two cancel terminals, kept apart on purpose (the
# lifecycle finding): a user who pressed Cancel did not lose a printer,
# and an invalidated session must still say why the file went away.
CANCELLED_BY_USER = "The download was cancelled"
CANCELLED_BY_SESSION = "The printer connection changed; the download was cancelled"


def _declared_length(reply) -> int:
    """The response's Content-Length, 0 when absent. Read through the
    header PAIRS with a case-insensitive scan — the typed lookup
    would pull the network-request class into this module against
    the architecture rule, and the raw bytes-key lookup returns
    empty on the Cura PyQt6 (probed against the server).
    The raw lookup stays as the fallback for replies whose pairs
    are unavailable (the late-header test's fake)."""
    try:
        pairs = reply.rawHeaderPairs()
    except Exception:
        pairs = None
    if pairs is not None:
        for name, value in pairs:
            try:
                key = bytes(name).decode("latin-1", "replace").lower()
            except Exception:
                continue
            if key == "content-length":
                try:
                    return int(bytes(value))
                except (TypeError, ValueError):
                    return 0
        return 0
    try:
        declared = reply.rawHeader(b"Content-Length")
        return int(bytes(declared)) if declared else 0
    except Exception:
        return 0


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
    fresh temp directory, built on `DownloadOperation` — the same
    bounded buffering, writer thread and operation-local state as the
    job lane. Independent of the job-bound state machine (the
    file-manager ruling: Download loads the file into Cura); its
    terminal differs: exactly one `on_ready(path, error)` delivery,
    the transport identity captured at request time, and the temp
    directory retired on every error path (success keeps the file for
    the lease protocol)."""

    def __init__(self, service, relpath, root, on_ready):
        self._service = service
        self._transport = service._transport
        self._relpath = str(relpath)
        self._on_ready = on_ready
        self._transport_identity = tuple(self._transport.identity)
        self._done = False
        self._directory = None
        self._path = None
        self._op = None
        reply = None
        try:
            self._directory = tempfile.mkdtemp(prefix="file-", dir=root)
            name = os.path.basename(self._relpath.replace("\\", "/")) or "download.gcode"
            if os.path.splitext(name)[1].lower() not in {".g", ".gcode"}:
                name += ".gcode"
            self._path = os.path.join(self._directory, name)
            request = self._transport.request(download_endpoint(self._transport.identity[0], self._relpath), timeout_ms=30000)
            request.setRawHeader(b"Accept", b"application/octet-stream")
            request.setRawHeader(b"Accept-Encoding", b"identity")
            reply = self._transport.network.get(request)
            reply.setReadBufferSize(4 * 1024 * 1024)
            # The target opens LAST so a raise above leaves no open
            # handle behind (the coverage agent's live find — the
            # orphaned fd also resisted file deletion on Windows).
            target = service._target_factory(self._path)
            # Same as the job lane: the declared length is read lazily
            # in the drain once the response headers have arrived.
            size = 0
        except Exception as error:
            # No file handle exists here (the target opens last), but a
            # failure AFTER the reply was created — the target factory
            # raising — must not leave that reply live: abort and
            # dispose it exactly as the job lane's setup path does, or
            # the transfer keeps running with nothing reading it.
            if reply is not None:
                reply.abort()
                reply.deleteLater()
            # The one-shot retires with its directory removed by
            # _finish_immediately.
            self._finish_immediately(str(error))
            return
        self._op = DownloadOperation(target, reply, size, None, None)
        self._op.on_writer_done = lambda o=self._op: service.oneShotDone.emit(o)
        self._op.on_writer_drained = lambda o=self._op: service.oneShotDrained.emit(o)
        self._op.start()
        reply.readyRead.connect(lambda r=reply, o=self._op: service._drain_one_shot(self, o, r))
        reply.finished.connect(lambda r=reply, o=self._op: self._finish_stream(o, r))

    @property
    def done(self) -> bool:
        """The read-only completion flag (the hardening pass): a
        synchronous constructor failure delivers its terminal BEFORE
        `download_once` returns, so the caller must be able to see the
        completion without reaching into the private flag."""
        return self._done

    def _finish_stream(self, op, reply):
        if self._done:
            reply.deleteLater()
            return
        op.finished_reading = True
        self._service._drain_one_shot(self, op, reply)
        if self._done:
            return
        op.stop()  # the writer flushes and closes, then its done signal lands the terminal

    def _terminal(self):
        if self._done:
            return
        op = self._op
        try:
            # A transport/session switch mid-stream must never load the
            # old printer's file into the current session.
            if tuple(self._transport.identity) != self._transport_identity:
                raise OSError("The printer connection changed during the download")
            if op.writer_error is not None:
                raise OSError(op.writer_error)
            if op.reply.error() != QNetworkReply.NetworkError.NoError:
                raise OSError(op.reply.errorString())
            if op.size > 0 and op.target.bytes_written != op.size:
                raise OSError("Downloaded G-code size mismatch; refusing partial file")
            if op.expected > 0 and op.target.bytes_written != op.expected:
                raise OSError("Downloaded G-code does not match the file listing's size; refusing")
            self._deliver(self._path, None)
        except Exception as error:
            self._abort()
            self._deliver(None, str(error))
        finally:
            op.reply.deleteLater()

    def _finish_immediately(self, error):
        if self._done:
            return
        self._abort()
        self._deliver(None, error)

    def cancel(self, reason=CANCELLED_BY_USER):
        """Abort and deliver the terminal error. Exactly-once holds
        through every path, constructor failure included. The reason is
        the caller's to name: the user's Cancel and an invalidated
        session are different terminals (`cancel_one_shots` passes the
        connection-change one), and the quiet shutdown flavour differs
        only in whether the consumer reports it."""
        if self._done:
            return
        self._abort()
        self._deliver(None, reason)

    def _abort(self):
        op = self._op
        if op is not None:
            op.abort()
            if op.reply is not None:
                op.reply.abort()
                op.reply.deleteLater()
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    def _deliver(self, path, error):
        if self._done:
            return
        self._done = True
        self._on_ready(path, error)


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
    oneShotDone = pyqtSignal(object)
    oneShotDrained = pyqtSignal(object)

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
        self._metadata_only_pending = False
        self._metadata_fetched = False
        self._metadata_attempts = 0
        self._metadata_retry_at = 0.0
        self._path = None
        self._download = None
        self._one_shots = set()
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
        self.oneShotDone.connect(self._on_one_shot_done)
        self.oneShotDrained.connect(self._on_one_shot_drained)

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
        The job-bound state machine is untouched. The returned handle
        supports `cancel()`; the caller re-validates the requesting
        printer/session identity at delivery."""
        download = None
        def done(path, error):
            if download is not None and download in self._one_shots:
                self._one_shots.discard(download)
            on_ready(path, error)
        download = _OneShotDownload(self, relpath, self._root, done)
        if download.done:
            # A constructor failure delivered its terminal before the
            # registry add — the dead download must not accumulate.
            return download
        # KEEP THE REFERENCE: the reply's signals hold bound methods
        # of this object, and a garbage-collected downloader dies
        # silently mid-stream.
        self._one_shots.add(download)
        return download

    def cancel_one_shots(self):
        """The session-invalidation hook (wired by the runtime): every
        in-flight one-shot aborts and delivers its terminal error —
        the connection-change terminal, never the user-cancel one."""
        for download in list(self._one_shots):
            download.cancel(CANCELLED_BY_SESSION)

    def _drain_one_shot(self, download, op, reply):
        if download._done or op.aborted:
            return
        if op.reading_paused and not op.finished_reading:
            if (op.received - op.written) >= op.LOW_WATER_BYTES:
                return
            op.reading_paused = False
        try:
            if op.size <= 0:
                op.size = _declared_length(reply)
            # The identity-encoding contract (the critic's catch): a
            # proxy that ignores the request serves compressed bytes
            # whose length matches ITS declaration — the reply's own
            # Content-Encoding header is the tell.
            encoding = bytes(reply.rawHeader(b"Content-Encoding"))
            if encoding and encoding.lower() != b"identity":
                raise OSError("The download was served compressed; refusing")
            chunk = bytes(reply.readAll())
            if chunk:
                op.received += len(chunk)
                self._lifetime_received += len(chunk)
                if op.received > self.MAX_DOWNLOAD_BYTES:
                    download._finish_immediately("Download exceeds the size cap")
                    return
                op.queue.put(chunk)
                if not op.finished_reading and (op.received - op.written) >= op.HIGH_WATER_BYTES:
                    op.reading_paused = True
        except Exception as error:
            download._finish_immediately(str(error))

    def _on_one_shot_drained(self, op):
        for download in list(self._one_shots):
            if download._op is op:
                download._service._drain_one_shot(download, op, op.reply)
                return

    def _on_one_shot_done(self, op):
        for download in list(self._one_shots):
            if download._op is op:
                download._terminal()
                return

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
        self._metadata_pending = self._metadata_only_pending = self._want_file = False
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

    def request_metadata_only(self, callback) -> bool:
        """A metadata-only fetch that is IDENTITY-NEUTRAL (4.2.0,
        A5/H7): the job lane's identity, its fetched/pending/
        attempts/retry bits and its metadata cache stay untouched —
        a failing metadata-only request must never overwrite the
        identity the download path depends on (the 4.0.2 hazard),
        and a successful one must not mark the job lane complete
        (a silent download hang otherwise). The caller owns the
        callback's lifetime; a stale reply is dropped by the
        generation guard."""
        if self._closed or not self._job or self._metadata_only_pending:
            return False
        self._metadata_only_pending = True
        generation, job = self._generation, self._job
        def finished(payload, error):
            if generation != self._generation or job != self._job or self._closed:
                return
            self._metadata_only_pending = False
            result = (payload or {}).get("result", {})
            callback(dict(result) if isinstance(result, dict) else {}, error)
        started = self._transport.send_json("files", "metadata-only", "GET",
            metadata_endpoint(self._transport.identity[0], job[0]), finished, category="static")
        if not started:
            self._metadata_only_pending = False
        return started

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
        target = None
        try:
            target = self._target_factory(os.path.join(directory, name))
            request = self._transport.request(download_endpoint(self._transport.identity[0], job[0]), timeout_ms=30000)
            request.setRawHeader(b"Accept", b"application/octet-stream")
            # IDENTITY encoding: Qt's default Accept-Encoding made the
            # server answer gzip + CHUNKED, which carries no
            # Content-Length — the transfer's declared size stayed 0
            # and the bar swept for the whole download (the live
            # report, nginx probed). Identity also keeps
            # the bytes on disk identical to the printer's file, which
            # the size-mismatch guard wants.
            request.setRawHeader(b"Accept-Encoding", b"identity")
            reply = self._transport.network.get(request)
            reply.setReadBufferSize(4 * 1024 * 1024)
            # The response headers have not arrived yet: the declared
            # length is read lazily in the drain (a creation-time
            # rawHeader read was empty and froze the progress as
            # indeterminate for the whole transfer).
            size = 0
        except Exception as error:
            if reply is not None:
                reply.abort()
                reply.deleteLater()
            if target is not None:
                # The open target must never leak its handle when the
                # request setup fails after the file was created.
                target.abort(remove=False)
            shutil.rmtree(directory, ignore_errors=True)
            self._fail(str(error))
            return
        op = DownloadOperation(target, reply, size, generation, job)
        # The file listing's size is the honest referee (the
        # critic's catch) — a proxy that ignores identity-encoding
        # delivers bytes whose length matches its own declaration.
        if self._identity is not None and self._identity.size > 0:
            op.expected = int(self._identity.size)
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
            if op.size <= 0:
                op.size = _declared_length(reply)
            # The identity-encoding contract (the critic's catch): a
            # proxy that ignores the request serves compressed bytes
            # whose length matches ITS declaration — the reply's own
            # Content-Encoding header is the tell.
            encoding = bytes(reply.rawHeader(b"Content-Encoding"))
            if encoding and encoding.lower() != b"identity":
                raise OSError("The download was served compressed; refusing")
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
            if op.expected > 0 and op.target.bytes_written != op.expected:
                raise OSError("Downloaded G-code does not match the file listing's size; refusing")
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
        self.cancel_one_shots()  # every one-shot retires BEFORE the root rmtree below
        self._closed = True
        if not self._leases: shutil.rmtree(self._root, ignore_errors=True)
