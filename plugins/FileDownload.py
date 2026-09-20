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

from PyQt6.QtCore import QObject, QStandardPaths, pyqtSignal
from PyQt6.QtWidgets import QFileDialog

from .RemoteFileService import CANCELLED_BY_USER, FileLease


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


def _replace_saved_file(source, target):
    """Land a finished stream at `target` with deliberate replace
    semantics: the bytes are copied to a staging sibling first and the
    destination only ever changes through one atomic `os.replace`. A
    failed save therefore leaves the file the user already had exactly
    as it was — `shutil.move` cannot promise that, since a rename onto
    an existing file raises on Windows and its copy fallback truncates
    the destination in place before it knows the copy will succeed."""
    staging = _staging_path(target)
    try:
        shutil.copyfile(source, staging)
        os.replace(staging, target)
    except OSError:
        try:
            os.remove(staging)
        except OSError:
            pass
        raise


class FileDownload(QObject):
    failed = pyqtSignal(str)
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
        self._save = None  # the single in-flight SAVE stream (the popup's own)
        self._save_name = None
        self._closing = False
        # Load refusals (already loading, no printer yet, Cura never
        # confirming) surface through the same failure channel as
        # download errors — the model relays both into the popup's
        # note line.
        self._cura.loadFailed.connect(self.failed.emit)

    def progress(self):
        """The in-flight SAVE stream's progress window payload, or None
        when no save transfer runs (the popup's gate). It describes the
        save stream alone: the service's job-bound download_fraction
        tracks the FOLLOW lane, which a save download never joins, and a
        load-in-flight would otherwise lend its bytes to the save
        window's name. A response that declares no length keeps the
        window open — `indeterminate`, with the received bytes standing
        in for the fraction — because the payload is also what keeps
        the user's Cancel reachable; the determinate percentage appears
        the moment the headers declare a total."""
        download = self._save
        if download is None or getattr(download, "done", False):
            return None
        op = getattr(download, "_op", None)
        if op is None:
            return None
        size = int(getattr(op, "size", 0) or 0)
        payload = {"name": self._save_name or "", "received": int(getattr(op, "received", 0) or 0),
                   "percent": 0, "total": 0, "indeterminate": size <= 0}
        if size > 0:
            payload["percent"] = round(max(0.0, min(1.0, op.received / size)) * 100)
            payload["total"] = size
        return payload

    def cancel(self):
        """The progress window's Cancel, and the USER terminal: every
        in-flight stream retires with the user-cancel message. A user
        who presses Cancel did not lose a printer, so the
        connection-change explanation belongs to the invalidation door
        (the service's cancel_one_shots) alone."""
        self._save = None
        self._save_name = None
        for download in list(self._active):
            download.cancel(CANCELLED_BY_USER)
        self._active.clear()

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
                self.failed.emit("The printer connection changed; the download was discarded")
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
                if self._save is download:
                    self._save = None
            self._save_name = None
            if self._closing:
                return  # shutdown: the terminal retires without a report
            if error or not path:
                self.failed.emit(error or "The download failed")
                return
            directory = os.path.dirname(path)
            try:
                if machine_id != self._active_identity()[0] or generation != self._session_generation():
                    self.failed.emit("The printer connection changed; the download was discarded")
                    return
                _replace_saved_file(path, target)
            except OSError as exc:
                self.failed.emit("The download could not be saved: {}".format(exc))
            finally:
                # The streamed source is a temporary whatever happened:
                # its bytes now live at the destination or nowhere.
                shutil.rmtree(directory, ignore_errors=True)

        download = self._files.download_once(str(relpath), on_ready=on_ready)
        if not download.done:
            self._active.add(download)
            self._save = download
        return True

    def close(self):
        # Shutdown ordering (the runtime closes this BEFORE the files
        # service): retire the in-flight streams so no terminal lands
        # after the temp root is gone, and report none of them — a
        # shutting-down model has no note line to read.
        self._closing = True
        self._save = None
        self._save_name = None
        for download in list(self._active):
            download.cancel(CANCELLED_BY_USER)
        self._active.clear()
