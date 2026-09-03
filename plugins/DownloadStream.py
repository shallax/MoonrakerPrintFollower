from __future__ import annotations

import os
from dataclasses import dataclass
from typing import BinaryIO, Optional


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
