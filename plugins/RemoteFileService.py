from __future__ import annotations

import os
import shutil
import tempfile
import time
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply

from .MoonrakerProtocol import RemoteFileIdentity
from .DownloadStream import DownloadTarget
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

    METADATA_RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)
    DOWNLOAD_RETRY_DELAYS_MS = (2000, 5000, 15000, 60000)

    def __init__(self, transport, parent=None):
        super().__init__(parent)
        self._transport = transport
        self._root = tempfile.mkdtemp(prefix="cura-moonraker-files-")
        self._generation = 0
        self._job = None
        self._identity = None
        self._metadata = {}
        self._metadata_pending = False
        self._metadata_fetched = False
        self._metadata_attempts = 0
        self._metadata_retry_at = 0.0
        self._path = None
        self._reply = self._target = None
        self._want_file = False
        self._leases = {}
        self._retired = set()
        self._closed = False
        self._error = ""
        self._download_attempts = 0
        self._download_retry_at = 0.0

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
        if self._reply is not None: return "downloading"
        if self._metadata_pending: return "resolving"
        return "ready" if self._path else "idle"

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
        elif self._want_file and not self._path and self._reply is None:
            self._start_download()

    def _start_download(self):
        generation, job = self._generation, self._job
        directory = tempfile.mkdtemp(prefix="job-", dir=self._root)
        name = os.path.basename(job[0].replace("\\", "/")) or "moonraker.gcode"
        if os.path.splitext(name)[1].lower() not in {".g", ".gcode"}: name += ".gcode"
        try:
            self._target = DownloadTarget.open(os.path.join(directory, name))
            request = self._transport.request(download_endpoint(self._transport.identity[0], job[0]), timeout_ms=30000)
            request.setRawHeader(b"Accept", b"application/octet-stream")
            reply = self._transport.network.get(request)
            reply.setReadBufferSize(4 * 1024 * 1024)
            self._reply = reply
            reply.readyRead.connect(lambda: self._drain(reply))
            reply.finished.connect(lambda: self._finish_download(reply, generation, job))
            self.changed.emit()
        except Exception as error:
            self._abort_download()
            shutil.rmtree(directory, ignore_errors=True)
            self._fail(str(error))

    def _drain(self, reply):
        if reply is not self._reply or self._target is None: return
        try:
            self._target.write(reply.readAll())
        except Exception as error:
            self._abort_download()
            self._fail(str(error))

    def _finish_download(self, reply, generation, job):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._drain(reply)
        if reply is not self._reply: return
        target = self._target
        self._reply = self._target = None
        try:
            if generation != self._generation or job != self._job:
                target.abort()
                self._retire(target.path)
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise OSError(reply.errorString())
            target.flush_close()
            size = self._identity.size if self._identity is not None else job[1]
            if size > 0 and target.bytes_written != size:
                raise OSError("Downloaded G-code size mismatch; refusing partial file")
            self._path = target.path
            self._download_attempts = 0
            self._download_retry_at = 0.0
            self.changed.emit()
        except Exception as error:
            target.abort()
            self._retire(target.path)
            self._fail(str(error))
        finally:
            reply.deleteLater()

    def _abort_download(self):
        reply, target = self._reply, self._target
        self._reply = self._target = None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if target is not None:
            target.abort()
            self._retire(target.path)

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
