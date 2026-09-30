"""The file-manager Download capability: one stream into a temp
location, then either the file loads into Cura through the follower's
lease protocol, or — the live ruling — the user's save picker chooses
a local path and the stream lands there. The save flow is STRICTLY a
file transfer: no Cura load, no index build, no parsing, no print
state changes. Composition-owned (the runtime constructs it); the
stream itself belongs to RemoteFileService's one-shot lane. A download
captures the requesting printer/session identity at request time and
refuses to land a result that arrives after a switch."""
from __future__ import annotations

import os
import shutil
import threading

from PyQt6.QtCore import QObject, QStandardPaths, pyqtSignal
from PyQt6.QtWidgets import QFileDialog

from .RemoteFileService import CANCELLED_BY_USER, FileLease

# Top of the save lane's lifecycle: the stream is running, or its
# finished bytes are being copied beside the destination and published.
_STREAMING = "streaming"
_PUBLISHING = "publishing"

# The publication copy's step: small enough that a cancel is honoured
# promptly, large enough that a 2 GiB print is not a million writes.
_COPY_CHUNK_BYTES = 4 * 1024 * 1024

# The connection-change outcome, spelled once: the stream's own identity
# gates and the publication-time one report the same words.
_STALE_MESSAGE = "The printer connection changed; the download was discarded"


class _SaveCancelled(Exception):
    """The user's Cancel arrived while the finished stream was being
    published beside its destination: the staging file goes, the
    destination the user already had stays."""


class _SaveStale(Exception):
    """The requesting printer or session was switched away from while
    the finished stream was being copied beside its destination — the
    copy deliberately runs for minutes, so the switch can land long
    after the stream's own identity gate ran. The staging file goes and
    the destination keeps exactly what it had."""


def _staging_path(target):
    """A free sibling name beside `target` — the picker's own
    directory, so the staging file and the destination share a
    volume and the final swap below can be an atomic replace."""
    directory = os.path.dirname(target) or "."
    base = os.path.basename(target) or "download.gcode"
    for attempt in range(1, 100):
        candidate = os.path.join(directory, ".{}.mpf-part-{}".format(base, attempt))
        if not os.path.exists(candidate):
            return candidate
    raise OSError("no free staging name beside {}".format(target))


def _replace_saved_file(source, target, cancelled=None, verify=None):
    """Land a finished stream at `target` with deliberate replace
    semantics: the bytes are copied to a staging sibling first and the
    destination only ever changes through one atomic `os.replace`. A
    failed save therefore leaves the file the user already had exactly
    as it was — `shutil.move` cannot promise that, since a rename onto
    an existing file raises on Windows and its copy fallback truncates
    the destination in place before it knows the copy will succeed.

    The copy is CHUNKED and honours `cancelled` between chunks: the
    callers run this off the owner thread, and this is what lets the
    window's Cancel abandon a copy already in flight instead of
    waiting the whole volume out.

    `verify` is the last gate before the swap, re-read here rather than
    when the copy started: the copy can outlast the printer/session it
    belongs to, and a switch landing mid-copy must leave the retired
    printer's file out of the user's destination."""
    staging = _staging_path(target)
    try:
        with open(source, "rb") as stream, open(staging, "wb") as staging_stream:
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise _SaveCancelled()
                chunk = stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                staging_stream.write(chunk)
        # Re-checked immediately before the swap: a cancel that landed
        # while the last chunk was written must not publish, and neither
        # may a transfer whose printer or session was switched away from
        # during the copy.
        if cancelled is not None and cancelled.is_set():
            raise _SaveCancelled()
        if verify is not None and not verify():
            raise _SaveStale()
        os.replace(staging, target)
    except BaseException:
        try:
            os.remove(staging)
        except OSError:
            pass
        raise


class FileDownload(QObject):
    failed = pyqtSignal(str)
    # The publication terminal, emitted by the copy worker: the
    # connection is queued, so the slot runs on the owner (GUI) thread
    # that built this object.
    publicationDone = pyqtSignal(object, object)
    # The save lane carries ONE user transfer at a time (the lifecycle
    # finding): the progress window is a single name, a single bar and
    # a single Cancel, so rather than let one name describe several
    # streams, a second request while one runs is refused outright.
    BUSY_MESSAGE = "A download is already in progress"

    def __init__(self, files, cura, parent=None, *, active_identity=None, session_generation=None):
        super().__init__(parent)
        self._files = files
        self._cura = cura
        self._active_identity = active_identity or (lambda: (None, None))
        self._session_generation = session_generation or (lambda: None)
        self._active = set()
        self._save = None  # the SAVE transfer the window describes (_STREAMING or _PUBLISHING)
        self._save_name = None
        self._save_stage = None
        self._save_cancel = None  # the publication copy's cancel event
        self._closing = False
        self.publicationDone.connect(self._on_publication_done)
        # Load refusals (already loading, no printer yet, Cura never
        # confirming) surface through the same failure channel as
        # download errors — the model relays both into the popup's
        # note line.
        self._cura.loadFailed.connect(self.failed.emit)

    def progress(self):
        """The in-flight SAVE transfer's progress window payload, or None
        when no save transfer runs (the popup's gate). It describes the
        save stream alone: the service's job-bound download_fraction
        tracks the FOLLOW lane, which a save download never joins, and a
        load-in-flight would otherwise lend its bytes to the save
        window's name. A response that declares no length keeps the
        window open — `indeterminate`, with the received bytes standing
        in for the fraction — because the payload is also what keeps
        the user's Cancel reachable; the determinate percentage appears
        the moment the headers declare a total.

        The finished stream keeps its window while it is being
        published: the bytes are complete but not yet at the destination,
        so the bar holds full instead of vanishing into the copy (which
        is where it used to disappear)."""
        download = self._save
        if download is None:
            return None
        op = getattr(download, "_op", None)
        if self._save_stage == _PUBLISHING:
            received = int(getattr(op, "received", 0) or 0) if op is not None else 0
            size = int(getattr(op, "size", 0) or 0) if op is not None else 0
            return {"name": self._save_name or "", "received": received, "percent": 100,
                    "total": size or received, "indeterminate": False}
        if getattr(download, "done", False) or op is None:
            return None
        size = int(getattr(op, "size", 0) or 0)
        payload = {"name": self._save_name or "", "received": int(getattr(op, "received", 0) or 0),
                   "percent": 0, "total": 0, "indeterminate": size <= 0}
        if size > 0:
            payload["percent"] = round(max(0.0, min(1.0, op.received / size)) * 100)
            payload["total"] = size
        return payload

    def cancel(self):
        """The progress window's Cancel, and the USER terminal for the
        transfer that window describes: the SAVE stream. A load opened
        alongside into Cura carries its own UI and its own terminal, so
        this Cancel leaves it streaming — retiring it silently killed a
        transfer the user never cancelled. Shutdown (`close`) and the
        session-invalidation door still retire everything.

        A cancel that lands while the finished stream is being published
        is honoured as well: the copy abandons and the destination the
        user already had stays. A user who pressed Cancel did not lose a
        printer, so the connection-change explanation belongs to the
        invalidation door (the service's cancel_one_shots) alone."""
        save, self._save = self._save, None
        stage, self._save_stage = self._save_stage, None
        cancelled, self._save_cancel = self._save_cancel, None
        self._save_name = None
        if cancelled is not None:
            cancelled.set()
        if save is None:
            return
        self._active.discard(save)
        if stage != _PUBLISHING:
            save.cancel(CANCELLED_BY_USER)

    def _clear_save(self, download):
        """Retire the save lane's own latch once `download` is the
        transfer the window describes."""
        if self._save is not download:
            return
        self._save = None
        self._save_name = None
        self._save_stage = None
        self._save_cancel = None

    def _publish_saved_file(self, download, source, target, cancelled, verify):
        """The publication worker: the whole-file copy of the finished
        stream onto the destination's volume, OFF the owner thread. The
        copy used to run on it, so a large file or a slow volume froze
        Cura's repaints, the popup's Cancel and the window's close for
        as long as the copy took. The staged source is retired here
        too: its bytes now live at the destination or nowhere."""
        try:
            _replace_saved_file(source, target, cancelled, verify)
            error = None
        except _SaveCancelled:
            error = CANCELLED_BY_USER
        except _SaveStale:
            error = _STALE_MESSAGE
        except Exception as exc:
            error = "The download could not be saved: {}".format(exc)
        finally:
            shutil.rmtree(os.path.dirname(source), ignore_errors=True)
        self.publicationDone.emit(download, error)

    def _on_publication_done(self, download, error):
        """The publication's terminal, on the owner thread."""
        self._clear_save(download)
        if self._closing:
            return  # shutdown: the terminal retires without a report
        if error:
            self.failed.emit(error)

    def request(self, relpath) -> bool:
        machine_id = self._active_identity()[0]
        generation = self._session_generation()
        intent = "load"
        download = None

        def on_ready(path, error):
            if download is not None:
                self._active.discard(download)
            if self._closing:
                return  # shutdown: the terminal retires without a report
            if error or not path:
                self.failed.emit(error or "The download failed")
                return
            # A stale completion must never load into a different
            # Cura session: the captured identity has to match the
            # live one, or the file is discarded.
            if (intent != "load" or machine_id != self._active_identity()[0]
                    or generation != self._session_generation()):
                self.failed.emit(_STALE_MESSAGE)
                return

            def release(lease_path):
                shutil.rmtree(os.path.dirname(lease_path), ignore_errors=True)
            self._cura.load(FileLease(path, release))

        download = self._files.download_once(str(relpath), on_ready=on_ready)
        if not download.done:
            # A synchronous constructor failure delivered its terminal
            # before `download` existed here, so the dead download must
            # not accumulate in the active set (the hardening pass).
            self._active.add(download)
        return True

    def request_save(self, relpath) -> bool:
        """The save-picker flow: the user chooses a local path and the
        stream lands there. Nothing else happens — no load, no index,
        no print state. A cancelled picker starts nothing, and a second
        request while one save transfer runs is refused explicitly
        rather than queued behind it (one popup, one transfer)."""
        if self._save is not None:
            self.failed.emit(self.BUSY_MESSAGE)
            return False
        machine_id = self._active_identity()[0]
        generation = self._session_generation()
        name = os.path.basename(str(relpath)) or "download.gcode"
        # The picker opens in the OS's Downloads directory when it
        # exposes one, otherwise the home directory (the live request).
        default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        if not default_dir:
            default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation) \
                or os.path.expanduser("~")
        target, _selected = QFileDialog.getSaveFileName(
            None, "Save file", os.path.join(default_dir, name))
        if not target:
            return False
        self._save_name = name
        download = None

        def on_ready(path, error):
            if download is not None:
                self._active.discard(download)
            if self._closing:
                self._clear_save(download)
                return  # shutdown: the terminal retires without a report
            if error or not path:
                self._clear_save(download)
                self.failed.emit(error or "The download failed")
                return
            if machine_id != self._active_identity()[0] or generation != self._session_generation():
                self._clear_save(download)
                # The streamed source is a temporary whatever happened.
                shutil.rmtree(os.path.dirname(path), ignore_errors=True)
                self.failed.emit(_STALE_MESSAGE)
                return

            def still_current():
                # The publication-time identity gate: the copy below runs
                # for as long as the volume takes, so the check made here
                # is only the first one — the copy re-reads it immediately
                # before the swap. The accessors are plain identity reads,
                # which is what lets the copy thread make them.
                return (machine_id == self._active_identity()[0]
                        and generation == self._session_generation())

            # The transfer is done, the FILE is not there yet: it still
            # has to be copied to the destination's volume. That whole-
            # file copy runs on a worker (the owner thread cannot afford
            # it), and the save latch stays SET until the atomic replace
            # publishes — the window keeps its name, its bar and its
            # Cancel the whole way instead of clearing into the stall.
            self._save = download
            self._save_stage = _PUBLISHING
            self._save_cancel = threading.Event()
            threading.Thread(target=self._publish_saved_file,
                             args=(download, path, target, self._save_cancel, still_current),
                             name="mpf-save-publish", daemon=True).start()

        download = self._files.download_once(str(relpath), on_ready=on_ready)
        if not download.done:
            self._active.add(download)
            self._save = download
            self._save_stage = _STREAMING
        return True

    def close(self):
        # Shutdown ordering (the runtime closes this BEFORE the files
        # service): retire the in-flight streams — a publication in
        # flight included, so no copy keeps writing to a destination
        # while the interpreter goes down — and report none of them: a
        # shutting-down model has no note line to read.
        self._closing = True
        cancelled, self._save_cancel = self._save_cancel, None
        self._save = None
        self._save_name = None
        self._save_stage = None
        if cancelled is not None:
            cancelled.set()
        for download in list(self._active):
            download.cancel(CANCELLED_BY_USER)
        self._active.clear()
