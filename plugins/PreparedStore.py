"""The file-backed prepared cold store: every layer's compact PPL1 encoding, random-accessible on disk
so the complete print never sits in the Python heap.

Layout (format version 3):
    magic b"MPFP" + format version u32
    identity (u16 length + utf8 — the RemoteFileIdentity stable key)
    layer count u32
    completion flag u8 — 1 once the pass has WALKED EVERY layer
    layer table: count x (u8 state, u64 offset, u32 length)
        state 0 EMPTY       — the pass never resolved it (a latched
                              hydrate): retry on the next session
        state 1 CACHED      — the packed payload follows at the offset
        state 2 UNCACHEABLE — the pass walked it and the codec refused:
                              never retry, the layer reads as resolved
    payload area: the packed layers

The per-layer truth rides the STATE — a (0, 0) entry is EMPTY, not an
overloaded uncacheable marker: the completion flag no longer has to
disambiguate it. Random access reads one layer by seeking the table —
no preceding layers are ever decoded. Writes go to a temporary file
and finalise atomically (the header and the table are written LAST,
then the rename), so a partially written cache is never treated as
complete. The directory's total usage is bounded by a size policy
with print-level recency eviction.
"""
from __future__ import annotations

import os
import struct
import time
from typing import Optional

_MAGIC = b"MPFP"
_FORMAT_VERSION = 3
_HEADER_FMT = "<4sIHHB"
_TABLE_ENTRY_FMT = "<BQI"
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024

# The per-layer states: a published file's truth rides the table.
STATE_EMPTY = 0        # never resolved — retry on the next session
STATE_CACHED = 1       # the packed payload follows
STATE_UNCACHEABLE = 2  # walked and refused — never retry


class PreparedCache:
    """Per-print prepared-layer files under one directory."""

    def __init__(self, directory: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self.directory = directory
        self.max_bytes = max(16 * 1024 * 1024, int(max_bytes))
        os.makedirs(self.directory, exist_ok=True)
        # Crash leftovers: a temp writer
        # from a previous run is never valid, and startup has no
        # active writer to protect — remove them all.
        try:
            for name in os.listdir(self.directory):
                if ".tmp-" in name:
                    try:
                        os.unlink(os.path.join(self.directory, name))
                    except OSError:
                        pass
        except OSError:
            pass

    def _path(self, identity: str) -> str:
        import hashlib
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.mpfp")

    def load_table(self, identity: str) -> Optional[dict]:
        """The layer table for a published cache as
        ``{"table": [(state, offset, length), ...], "complete": bool}``,
        or None when the file is absent, partial, or belongs to
        another identity."""
        path = self._path(identity)
        # The explicit recency: atime is
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
                magic, version, id_len, count, complete = struct.unpack(_HEADER_FMT, header)
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
                for state, offset, length in table:
                    if state == STATE_CACHED and offset + length > size:
                        return None
                return {"table": table, "complete": bool(complete)}
        except OSError:
            return None

    def read(self, identity: str, table: list, layer: int) -> Optional[bytes]:
        """One layer's packed payload, read by offset — never through
        its predecessors. EMPTY and UNCACHEABLE entries read None."""
        if layer < 0 or layer >= len(table):
            return None
        state, offset, length = table[layer]
        if state != STATE_CACHED or length <= 0:
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
                                     len(identity.encode("utf-8")), len(payloads), 1)
                handle.write(header)
                handle.write(identity.encode("utf-8"))
                table_start = handle.tell()
                handle.seek(table_start + len(payloads) * struct.calcsize(_TABLE_ENTRY_FMT))
                table = []
                for payload in payloads:
                    table.append((STATE_CACHED, handle.tell(), len(payload)))
                    handle.write(payload)
                handle.seek(table_start)
                for state, offset, length in table:
                    handle.write(struct.pack(_TABLE_ENTRY_FMT, state, offset, length))
            os.replace(temp, path)
        except OSError:
            try:
                os.unlink(temp)
            except OSError:
                pass
            return None
        self._evict(path)
        return path

    def open_for_write(self, identity: str, layer_count: int) -> Optional[dict]:
        """The incremental writer: a temp
        file accumulates the pass's encodings layer by layer, so the
        first session never retains the whole cold store in RAM. The
        header and the table are written at `finish_write`; a partial
        temp file never reads as complete."""
        if layer_count <= 0:
            return None
        path = self._path(identity)
        temp = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
        try:
            handle = open(temp, "wb")
        except OSError:
            return None
        # Reserve the header and the table (the payloads append
        # behind them; the finalise back-fills).
        handle.write(b"\0" * (struct.calcsize(_HEADER_FMT) + len(identity.encode("utf-8"))
                              + layer_count * struct.calcsize(_TABLE_ENTRY_FMT)))
        return {"identity": identity, "temp": temp, "handle": handle,
                "layer_count": layer_count, "table": [None] * layer_count}

    def append(self, writer: dict, layer: int, payload: bytes) -> None:
        if layer < 0 or layer >= writer["layer_count"] or writer["table"][layer] is not None:
            return
        handle = writer["handle"]
        writer["table"][layer] = (STATE_CACHED, handle.tell(), len(payload))
        handle.write(payload)

    def append_uncacheable(self, writer: dict, layer: int) -> None:
        """Record a layer the pass walked but the codec refused: no
        payload follows, and the state says never retry."""
        if writer is None or layer < 0 or layer >= writer["layer_count"] \
                or writer["table"][layer] is not None:
            return
        writer["table"][layer] = (STATE_UNCACHEABLE, 0, 0)

    def finish_write(self, writer: dict) -> Optional[str]:
        """Write the header and the table, then atomically publish.
        The completion flag is set unconditionally: `finish_write`
        only runs once the pass has walked every layer, so a
        remaining slot means the pass never resolved it — EMPTY
        (a latched hydrate), retried on the next session, never
        confused with an uncacheable layer (which was marked
        explicitly)."""
        identity = writer["identity"]
        handle = writer["handle"]
        try:
            handle.seek(0)
            handle.write(struct.pack(_HEADER_FMT, _MAGIC, _FORMAT_VERSION,
                                     len(identity.encode("utf-8")), writer["layer_count"], 1))
            handle.write(identity.encode("utf-8"))
            for entry in writer["table"]:
                if entry is None:
                    entry = (STATE_EMPTY, 0, 0)
                handle.write(struct.pack(_TABLE_ENTRY_FMT, entry[0], entry[1], entry[2]))
            handle.close()
            os.replace(writer["temp"], self._path(identity))
        except OSError:
            self.abort_write(writer)
            return None
        self._evict(self._path(identity))
        return self._path(identity)

    def abort_write(self, writer: dict) -> None:
        """Abandon an unfinished writer: close the handle and remove
        the temp file, however far the append got . Idempotent — the caller's exit paths all reach
        it."""
        try:
            writer["handle"].close()
        except (OSError, ValueError):
            pass
        try:
            os.unlink(writer["temp"])
        except OSError:
            pass

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
            # The policy: under budget,
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
