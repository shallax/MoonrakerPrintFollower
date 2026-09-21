"""The file-backed prepared cold store (the review's round 3, steps
1-4): every layer's compact PPL1 encoding, random-accessible on disk
so the complete print never sits in the Python heap.

Layout:
    magic b"MPFP" + format version u32
    identity (u16 length + utf8 — the RemoteFileIdentity stable key)
    layer count u32
    layer table: count x (u64 offset, u32 length)
    payload area: the packed layers

Random access reads one layer by seeking the table — no preceding
layers are ever decoded. Writes go to a temporary file and finalise
atomically (the header and the table are written LAST, then the
rename), so a partially written cache is never treated as complete.
The directory's total usage is bounded by a size policy with
print-level recency eviction.
"""
from __future__ import annotations

import os
import struct
import time
from typing import Optional

_MAGIC = b"MPFP"
_FORMAT_VERSION = 1
_HEADER_FMT = "<4sIHH"
_TABLE_ENTRY_FMT = "<QI"
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class PreparedCache:
    """Per-print prepared-layer files under one directory."""

    def __init__(self, directory: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self.directory = directory
        self.max_bytes = max(16 * 1024 * 1024, int(max_bytes))
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, identity: str) -> str:
        import hashlib
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.mpfp")

    def load_table(self, identity: str) -> Optional[list]:
        """The layer table for a completed cache, or None when the
        file is absent, partial, or belongs to another identity."""
        path = self._path(identity)
        # The explicit recency (the review's finding 12): atime is
        # unreliable under relatime/noatime — a successful open
        # stamps the entry itself.
        try:
            os.utime(path, None)
        except OSError:
            pass
        try:
            with open(path, "rb") as handle:
                header = handle.read(struct.calcsize(_HEADER_FMT))
                if len(header) < struct.calcsize(_HEADER_FMT):
                    return None
                magic, version, id_len, count = struct.unpack(_HEADER_FMT, header)
                if magic != _MAGIC or version != _FORMAT_VERSION:
                    return None
                stored = handle.read(id_len).decode("utf-8", "replace")
                if stored != identity:
                    return None
                raw = handle.read(count * struct.calcsize(_TABLE_ENTRY_FMT))
                if len(raw) < count * struct.calcsize(_TABLE_ENTRY_FMT):
                    return None
                table = [struct.unpack_from(_TABLE_ENTRY_FMT, raw, i * struct.calcsize(_TABLE_ENTRY_FMT))
                         for i in range(count)]
                # A truncated file must never read as complete: the
                # payload area's extent must fit the file's own size.
                size = os.fstat(handle.fileno()).st_size
                for offset, length in table:
                    if offset + length > size:
                        return None
                return table
        except OSError:
            return None

    def read(self, identity: str, table: list, layer: int) -> Optional[bytes]:
        """One layer's packed payload, read by offset — never through
        its predecessors."""
        if layer < 0 or layer >= len(table):
            return None
        offset, length = table[layer]
        if length <= 0:
            return None
        path = self._path(identity)
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                raw = handle.read(length)
            return raw if len(raw) == length else None
        except OSError:
            return None

    def finalise(self, identity: str, payloads: list) -> Optional[str]:
        """Write every layer's packed payload and atomically publish
        the completed cache. Returns the path on success."""
        if not payloads:
            return None
        path = self._path(identity)
        temp = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
        try:
            with open(temp, "wb") as handle:
                header = struct.pack(_HEADER_FMT, _MAGIC, _FORMAT_VERSION,
                                     len(identity.encode("utf-8")), len(payloads))
                handle.write(header)
                handle.write(identity.encode("utf-8"))
                table_start = handle.tell()
                handle.seek(table_start + len(payloads) * struct.calcsize(_TABLE_ENTRY_FMT))
                table = []
                for payload in payloads:
                    table.append((handle.tell(), len(payload)))
                    handle.write(payload)
                handle.seek(table_start)
                for offset, length in table:
                    handle.write(struct.pack(_TABLE_ENTRY_FMT, offset, length))
            os.replace(temp, path)
        except OSError:
            try:
                os.unlink(temp)
            except OSError:
                pass
            return None
        self._evict(path)
        return path

    def _evict(self, keep: str) -> None:
        """The size policy: drop the least-recently-accessed print
        files while the directory's total exceeds the bound (the
        current file and the live session's are protected)."""
        try:
            entries = []
            total = 0
            for name in os.listdir(self.directory):
                if not name.endswith(".mpfp"):
                    continue
                path = os.path.join(self.directory, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                total += stat.st_size
                entries.append((stat.st_atime, path, stat.st_size))
            # The policy (the review's finding 13): under budget,
            # stop; a protected entry is SKIPPED, never a stopper —
            # the eviction continues with the next candidate.
            for _atime, path, size in sorted(entries):
                if total <= self.max_bytes:
                    break
                if path == keep:
                    continue
                try:
                    os.unlink(path)
                    total -= size
                except OSError:
                    pass
        except OSError:
            pass
