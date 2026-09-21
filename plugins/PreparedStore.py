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
        # Crash leftovers: a tmp writer from a previous run now
        # ADOPTS its successfully prepared layers (the review's
        # resumable-persistence finding) — see _adopt_interrupted.
        self._adopt_interrupted()

    def _path(self, identity: str) -> str:
        import hashlib
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        # The per-print subdirectory (the review's persistence
        # finding): the index and the prepared table live as siblings
        # under one print's own directory.
        print_dir = os.path.join(self.directory, f"p-{digest[:24]}")
        os.makedirs(print_dir, exist_ok=True)
        return os.path.join(print_dir, f"{digest}.mpfp")

    def _adopt_interrupted(self) -> None:
        """The interrupted-pass adoption: a tmp whose header was
        written (the checkpointed writer) carries the layers the pass
        already encoded — adopt it as the incomplete table, so the
        next session resumes from the EMPTY slots instead of
        restarting from layer zero. A LIVE process's writer (its pid
        still exists) is never touched; an unsound tmp dies."""
        try:
            for root, _dirs, names in os.walk(self.directory):
                for name in names:
                    if ".tmp-" not in name:
                        continue
                    final_path = os.path.join(root, name.split(".tmp-", 1)[0])
                    if not final_path.endswith(".mpfp"):
                        continue
                    path = os.path.join(root, name)
                    if self._tmp_liveness(name):
                        continue  # another process's active writer
                    try:
                        if os.path.exists(final_path):
                            os.unlink(path)  # the published final wins
                        elif self._tmp_sound(path):
                            os.replace(path, final_path)
                        else:
                            os.unlink(path)
                    except OSError:
                        pass
        except OSError:
            return

    def _tmp_liveness(self, name: str) -> bool:
        """True when the tmp's owning process is still alive (the
        pid rides the name)."""
        try:
            pid = int(name.split(".tmp-", 1)[1].split("-", 1)[0])
        except (IndexError, ValueError):
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _tmp_sound(self, path: str) -> bool:
        """The checkpointed tmp's own validity: the header parses and
        every CACHED entry's extent fits the file (a torn tail layer
        reads EMPTY later, never a corrupt offset)."""
        try:
            with open(path, "rb") as handle:
                header = handle.read(struct.calcsize(_HEADER_FMT))
                if len(header) < struct.calcsize(_HEADER_FMT):
                    return False
                magic, version, id_len, count, _complete = struct.unpack(_HEADER_FMT, header)
                if magic != _MAGIC or version != _FORMAT_VERSION or id_len <= 0 or count <= 0:
                    return False
                handle.read(id_len)
                raw = handle.read(count * struct.calcsize(_TABLE_ENTRY_FMT))
                if len(raw) < count * struct.calcsize(_TABLE_ENTRY_FMT):
                    return False
                table = [struct.unpack_from(_TABLE_ENTRY_FMT, raw,
                                            i * struct.calcsize(_TABLE_ENTRY_FMT))
                         for i in range(count)]
                size = os.fstat(handle.fileno()).st_size
                for state, offset, length in table:
                    if state == STATE_CACHED and (offset <= 0 or offset + length > size):
                        return False
                return True
        except OSError:
            return False

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
        # The checkpointed header (the review's resumable-persistence
        # finding): the magic, the version, the identity and the
        # completion flag are written NOW — completion 0 — so an
        # interrupted pass leaves a structurally valid table the next
        # session can adopt and resume. The table slots follow as
        # reserved zeros; the payloads append behind them.
        handle.write(struct.pack(_HEADER_FMT, _MAGIC, _FORMAT_VERSION,
                                 len(identity.encode("utf-8")), layer_count, 0))
        handle.write(identity.encode("utf-8"))
        handle.write(b"\0" * (layer_count * struct.calcsize(_TABLE_ENTRY_FMT)))
        return {"identity": identity, "temp": temp, "handle": handle,
                "layer_count": layer_count, "table": [None] * layer_count}

    def _table_offset(self, writer: dict) -> int:
        """The table region's byte offset: the header plus the
        identity."""
        return struct.calcsize(_HEADER_FMT) + len(writer["identity"].encode("utf-8"))

    def append(self, writer: dict, layer: int, payload: bytes) -> None:
        if layer < 0 or layer >= writer["layer_count"] or writer["table"][layer] is not None:
            return
        handle = writer["handle"]
        writer["table"][layer] = (STATE_CACHED, handle.tell(), len(payload))
        handle.write(payload)
        # The in-place checkpoint (the review's resumable-persistence
        # finding): the table slot writes NOW, so an interrupted pass
        # keeps this layer's entry and the adoption resumes from the
        # EMPTY slots.
        end = handle.tell()
        handle.seek(self._table_offset(writer) + layer * struct.calcsize(_TABLE_ENTRY_FMT))
        handle.write(struct.pack(_TABLE_ENTRY_FMT, STATE_CACHED,
                                 writer["table"][layer][1], len(payload)))
        handle.seek(end)

    def append_uncacheable(self, writer: dict, layer: int) -> None:
        """Record a layer the pass walked but the codec refused: no
        payload follows, and the state says never retry."""
        if writer is None or layer < 0 or layer >= writer["layer_count"] \
                or writer["table"][layer] is not None:
            return
        writer["table"][layer] = (STATE_UNCACHEABLE, 0, 0)
        handle = writer["handle"]
        end = handle.tell()
        handle.seek(self._table_offset(writer) + layer * struct.calcsize(_TABLE_ENTRY_FMT))
        handle.write(struct.pack(_TABLE_ENTRY_FMT, STATE_UNCACHEABLE, 0, 0))
        handle.seek(end)

    def finish_write(self, writer: dict) -> Optional[str]:
        """Flip the completion flag, then atomically publish. The
        header and the per-layer table entries were already written
        in place (the checkpoints); `finish_write` only marks the
        pass complete. A remaining slot means the pass never resolved
        it — EMPTY (a latched hydrate), retried on the next session,
        never confused with an uncacheable layer (which was marked
        explicitly)."""
        identity = writer["identity"]
        handle = writer["handle"]
        try:
            # The completion flag is the header's final byte.
            handle.seek(struct.calcsize(_HEADER_FMT) - 1)
            handle.write(b"\x01")
            handle.flush()
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
            for root, _dirs, names in os.walk(self.directory):
                for name in names:
                    if not name.endswith(".mpfp"):
                        continue
                    path = os.path.join(root, name)
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
