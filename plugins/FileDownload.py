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

from .RemoteFileService import FileLease


class FileDownload(QObject):
    failed = pyqtSignal(str)

    def __init__(self, files, cura, parent=None, *, active_identity=None, session_generation=None):
        super().__init__(parent)
        self._files = files
        self._cura = cura
        self._active_identity = active_identity or (lambda: (None, None))
        self._session_generation = session_generation or (lambda: None)
        self._active = set()
        self._save_name = None
        # Load refusals (already loading, no printer yet, Cura never
        # confirming) surface through the same failure channel as
        # download errors — the model relays both into the popup's
        # note line.
        self._cura.loadFailed.connect(self.failed.emit)

    def progress(self):
        """The in-flight save download's progress window payload:
        {name, percent}, or None when nothing is streaming. The model
        polls this each publish — the popup opens while a value is
        present and closes when the stream ends. The fraction comes
        from the one-shot handle's own operation: the service's
        job-bound download_fraction tracks the FOLLOW lane, which a
        save download never joins."""
        fraction = None
        for download in self._active:
            op = getattr(download, "_op", None)
            if op is not None and getattr(op, "size", 0) > 0:
                fraction = max(0.0, min(1.0, op.received / op.size))
                break
        if fraction is None:
            return None
        return {"name": self._save_name or "", "percent": round(fraction * 100),
                "received": op.received, "total": op.size}

    def cancel(self):
        """The progress window's Cancel: retire every in-flight stream
        (their terminals already deliver the cancel errors)."""
        self._save_name = None
        for download in list(self._active):
            download.cancel()
        self._active.clear()

    def request(self, relpath) -> bool:
        machine_id = self._active_identity()[0]
        generation = self._session_generation()
        intent = "load"
        download = None

        def on_ready(path, error):
            if download is not None:
                self._active.discard(download)
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
        no print state. A cancelled picker starts nothing."""
        machine_id = self._active_identity()[0]
        generation = self._session_generation()
        self._save_name = os.path.basename(str(relpath)) or "download.gcode"
        # The picker opens in the OS's Downloads directory when it
        # exposes one, otherwise the home directory (the live request).
        default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        if not default_dir:
            default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation) \
                or os.path.expanduser("~")
        target, _selected = QFileDialog.getSaveFileName(
            None, "Save file", os.path.join(default_dir, self._save_name))
        if not target:
            self._save_name = None
            return False
        download = None

        def on_ready(path, error):
            if download is not None:
                self._active.discard(download)
            self._save_name = None
            if error or not path:
                self.failed.emit(error or "The download failed")
                return
            directory = os.path.dirname(path)
            try:
                if machine_id != self._active_identity()[0] or generation != self._session_generation():
                    self.failed.emit("The printer connection changed; the download was discarded")
                    return
                shutil.move(path, target)
            except OSError as exc:
                self.failed.emit("The download could not be saved: {}".format(exc))
            finally:
                shutil.rmtree(directory, ignore_errors=True)

        download = self._files.download_once(str(relpath), on_ready=on_ready)
        if not download.done:
            self._active.add(download)
        return True

    def close(self):
        # Shutdown ordering (the runtime closes this BEFORE the files
        # service): cancel the in-flight streams so no terminal lands
        # after the temp root is gone.
        for download in list(self._active):
            download.cancel()
        self._active.clear()
