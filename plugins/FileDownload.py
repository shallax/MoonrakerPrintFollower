"""The file-manager Download capability: one stream into a temp
location, then the file loads into Cura through the follower's lease
protocol. Composition-owned (the runtime constructs it); the stream
itself belongs to RemoteFileService's one-shot lane."""
from __future__ import annotations

import os
import shutil

from PyQt6.QtCore import QObject

from .RemoteFileService import FileLease


class FileDownload(QObject):
    def __init__(self, files, cura, parent=None):
        super().__init__(parent)
        self._files = files
        self._cura = cura

    def request(self, relpath) -> None:
        def on_ready(path, error):
            if error or not path:
                return

            def release(lease_path):
                shutil.rmtree(os.path.dirname(lease_path), ignore_errors=True)
            self._cura.load(FileLease(path, release))
        self._files.download_once(str(relpath), on_ready=on_ready)
