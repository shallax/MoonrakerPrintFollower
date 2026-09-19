"""The file-manager Download capability: one stream into a temp
location, then the file loads into Cura through the follower's lease
protocol. Composition-owned (the runtime constructs it); the stream
itself belongs to RemoteFileService's one-shot lane. A download
captures the requesting printer/session identity at request time and
refuses to load a result that lands after a switch."""
from __future__ import annotations

import os
import shutil

from PyQt6.QtCore import QObject, pyqtSignal

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
        # Load refusals (already loading, no printer yet, Cura never
        # confirming) surface through the same failure channel as
        # download errors — the model relays both into the popup's
        # note line.
        self._cura.loadFailed.connect(self.failed.emit)

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

    def close(self):
        # Shutdown ordering (the runtime closes this BEFORE the files
        # service): cancel the in-flight streams so no terminal lands
        # after the temp root is gone.
        for download in list(self._active):
            download.cancel()
        self._active.clear()
