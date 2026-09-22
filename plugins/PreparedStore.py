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
import threading
import time
from typing import Optional

from plugins.CachePolicy import evict_to_budget

try:
    from UM.Logger import Logger as _Logger
except ImportError:
    _Logger = None  # the host stdlib suite has no UM


def _log(message, *args):
    """The persistence diagnostics, at the DECISION points only
    (never per layer): an INFO line names the reason a restore or an
    eviction happened. The host stdlib suite runs without UM — the
    log no-ops there."""
    if _Logger is not None:
        _Logger.log("i", message, *args)

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
        # The writer-ownership lock (the review's ownership finding):
        # every writer touch and the publish boundaries (suspend,
        # abort, finish) serialize through it, so an owner's suspend
        # can never close a handle mid-append and a retired writer
        # refuses every later write atomically. Re-entrant — the
        # failure paths abort from inside.
        self._lock = threading.RLock()
        os.makedirs(self.directory, exist_ok=True)
        # Crash leftovers: a tmp writer from a previous run now
        # ADOPTS its successfully prepared layers (the review's
        # resumable-persistence finding) — see _adopt_interrupted.
        self._adopt_interrupted()

    def _path(self, identity: str) -> str:
        import hashlib
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        # The per-print folder (the review's unified-persistence
        # finding): the index AND the prepared table live as siblings
        # under one print's own directory — an entire print's cache
        # is one folder to delete, and the eviction drops the whole
        # folder (never an orphaned half).
        print_dir = os.path.join(self.directory, f"p-{digest[:24]}")
        os.makedirs(print_dir, exist_ok=True)
        return os.path.join(print_dir, "prepared.mpfp")

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
                            # The arbitration (the review's repeated-crash
                            # finding): a published file AND a candidate
                            # both exist — keep whichever holds more valid
                            # progress, never prefer a filename.
                            verdict = self._arbitrate(final_path, path)
                            if verdict == "tmp":
                                os.replace(path, final_path)
                            else:
                                os.unlink(path)
                        elif self._tmp_sound(path):
                            os.replace(path, final_path)
                        else:
                            os.unlink(path)
                    except OSError:
                        pass
        except OSError:
            return

    def _tmp_liveness(self, name: str) -> bool:
        """The owner-liveness probe (the review's Windows-portability
        finding): True when the tmp's owning process may still be
        alive, False when the owner is provably gone or never
        existed. The probe is deliberately CONSERVATIVE — an owner
        that cannot be disproved keeps its tmp; only a provably dead
        or impossible owner releases it for adoption. Windows reads
        OpenProcess's own verdict (ERROR_INVALID_PARAMETER names no
        live process), never POSIX signal-0 semantics."""
        try:
            pid = int(name.split(".tmp-", 1)[1].split("-", 1)[0])
        except (IndexError, ValueError):
            return False  # no live writer ever stamped this name
        if pid <= 0:
            return False  # impossible: writers stamp their real pid
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False  # POSIX: no such process
        except PermissionError:
            return True  # cannot disprove the owner — keep the tmp
        except OverflowError:
            return False  # beyond any platform's pid space
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 87:
                # ERROR_INVALID_PARAMETER: the pid names no live
                # process (Windows has no ESRCH mapping for it).
                return False
            return True  # any other failure keeps the tmp

    def _tmp_sound(self, path: str) -> bool:
        """The checkpointed tmp's own validity: the header parses and
        every CACHED entry's extent fits the file (a torn tail layer
        reads EMPTY later, never a corrupt offset)."""
        return self._file_progress(path) is not None

    def _file_progress(self, path: str) -> Optional[dict]:
        """One file's valid progress, whoever wrote it: the completion
        flag and the CACHED entry count — the arbitration's common
        measure for a published final and an interrupted tmp alike.
        A corrupt or truncated file reports None."""
        try:
            with open(path, "rb") as handle:
                header = handle.read(struct.calcsize(_HEADER_FMT))
                if len(header) < struct.calcsize(_HEADER_FMT):
                    return None
                magic, version, id_len, count, complete = struct.unpack(_HEADER_FMT, header)
                if magic != _MAGIC or version != _FORMAT_VERSION or id_len <= 0 or count <= 0:
                    return None
                handle.read(id_len)
                raw = handle.read(count * struct.calcsize(_TABLE_ENTRY_FMT))
                if len(raw) < count * struct.calcsize(_TABLE_ENTRY_FMT):
                    return None
                table = [struct.unpack_from(_TABLE_ENTRY_FMT, raw,
                                            i * struct.calcsize(_TABLE_ENTRY_FMT))
                         for i in range(count)]
                size = os.fstat(handle.fileno()).st_size
                cached = 0
                for state, offset, length in table:
                    if state == STATE_CACHED:
                        if offset <= 0 or offset + length > size:
                            return None
                        cached += 1
                return {"complete": bool(complete), "cached": cached,
                        "count": count}
        except OSError:
            return None

    def _arbitrate(self, final_path: str, tmp_path: str) -> str:
        """The deterministic policy between an existing published file
        and an interrupted candidate (the review's repeated-crash
        finding — 30% published + a 70% tmp must keep the 70%): a
        complete valid final beats an incomplete tmp; a more-complete
        valid partial beats a less-complete one; equal progress goes
        to the newer file. A corrupt or unsound candidate loses."""
        final = self._file_progress(final_path)
        tmp = self._file_progress(tmp_path)
        if tmp is None:
            return "final"
        if final is None:
            return "tmp"
        if final["complete"]:
            return "final"
        if tmp["complete"]:
            return "tmp"
        if tmp["cached"] > final["cached"]:
            _log("prepared interrupted candidate superseded the older partial (%d > %d layers)",
                       tmp["cached"], final["cached"])
            return "tmp"
        if tmp["cached"] < final["cached"]:
            return "final"
        try:
            if os.stat(tmp_path).st_mtime >= os.stat(final_path).st_mtime:
                return "tmp"
        except OSError:
            pass
        return "final"

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
                "layer_count": layer_count, "table": [None] * layer_count,
                "checkpoint_layers": 0, "checkpoint_bytes": 0,
                # The generation-owned retirement flag (the review's
                # writer-ownership finding): the OWNER flips it when a
                # suspend/close/rebind begins publishing — every write
                # and the finish honour it, so a stale worker can
                # never touch the file after retirement.
                "retired": False}

    def _table_offset(self, writer: dict) -> int:
        """The table region's byte offset: the header plus the
        identity."""
        return struct.calcsize(_HEADER_FMT) + len(writer["identity"].encode("utf-8"))

    def append(self, writer: dict, layer: int, payload: bytes) -> None:
        # The whole append holds the lock: the retired check and the
        # write are one atomic step, so an owner's suspend can never
        # interleave — it either precedes this append (the flag
        # refuses it) or follows it (the bytes are checkpointed).
        with self._lock:
            if writer.get("retired"):
                # The owner began publishing this writer: a stale
                # generation must never write to it again (the
                # review's writer-ownership finding).
                return
            if layer < 0 or layer >= writer["layer_count"] or writer["table"][layer] is not None:
                return
            handle = writer["handle"]
            writer["table"][layer] = (STATE_CACHED, handle.tell(), len(payload))
            handle.write(payload)
            # The durability ordering (the review's checkpoint
            # finding): a table entry advertised CACHED after a
            # process crash must refer to bytes the page cache
            # already holds — the payload flushes first, only then
            # does the slot's write advertise it.
            handle.flush()
            # The in-place checkpoint (the review's
            # resumable-persistence finding): the table slot writes
            # NOW, so an interrupted pass keeps this layer's entry
            # and the adoption resumes from the EMPTY slots.
            end = handle.tell()
            handle.seek(self._table_offset(writer) + layer * struct.calcsize(_TABLE_ENTRY_FMT))
            handle.write(struct.pack(_TABLE_ENTRY_FMT, STATE_CACHED,
                                     writer["table"][layer][1], len(payload)))
            handle.flush()
            handle.seek(end)
            # The periodic durability checkpoint: an fsync every 32
            # layers or 4 MiB of payload — the periodic durability
            # guarantee at a cadence whose cost is invisible next to
            # the encode walk (a per-layer fsync would). The
            # suspension and the finish always sync (their callers).
            writer["checkpoint_layers"] += 1
            writer["checkpoint_bytes"] += len(payload)
            if writer["checkpoint_layers"] >= 32 or writer["checkpoint_bytes"] >= 4 * 1024 * 1024:
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                writer["checkpoint_layers"] = 0
                writer["checkpoint_bytes"] = 0

    def append_uncacheable(self, writer: dict, layer: int) -> None:
        """Record a layer the pass walked but the codec refused: no
        payload follows, and the state says never retry."""
        if writer is None:
            return
        with self._lock:
            if writer.get("retired") or layer < 0 \
                    or layer >= writer["layer_count"] or writer["table"][layer] is not None:
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
        explicitly). A RETIRED writer never finishes: the owner's
        suspend owns its publication."""
        with self._lock:
            if writer.get("retired"):
                return None
            identity = writer["identity"]
            handle = writer["handle"]
            try:
                # The completion flag is the header's final byte.
                handle.seek(struct.calcsize(_HEADER_FMT) - 1)
                handle.write(b"\x01")
                handle.flush()
                # The publish's durability boundary: the complete
                # store's bytes sync before the rename.
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                handle.close()
                os.replace(writer["temp"], self._path(identity))
            except OSError:
                self.abort_write(writer)
                return None
        self._evict(self._path(identity))
        return self._path(identity)

    def suspend_write(self, writer: dict) -> bool:
        """The normal-lifecycle CHECKPOINT (the review's clean-shutdown
        finding): a close, a rebind or a machine switch publishes the
        pass's committed layers as a valid INCOMPLETE store — the
        header already carries completion 0 and every layer's table
        slot was checkpointed in place — so the next session opens it
        and resumes from the EMPTY slots. Only a genuinely failed or
        stale writer is aborted (abort_write). The retirement flag
        flips FIRST, under the lock: from the first moment of the
        publication no worker can ever write to the writer again —
        an in-flight append completes before the freeze, and every
        later one is refused."""
        if writer is None:
            return False
        with self._lock:
            writer["retired"] = True
            try:
                writer["handle"].flush()
                # The publish's durability boundary: the checkpointed
                # bytes and the table slots sync before the rename.
                try:
                    os.fsync(writer["handle"].fileno())
                except OSError:
                    pass
                writer["handle"].close()
                os.replace(writer["temp"], self._path(writer["identity"]))
                return True
            except OSError:
                self.abort_write(writer)
                return False

    def abort_write(self, writer: dict) -> None:
        """Abandon an unfinished writer: close the handle and remove
        the temp file, however far the append got. Idempotent — the
        caller's exit paths all reach it. The retirement flag flips
        under the lock too, so an abandoned writer is exactly as
        untouchable as a suspended one."""
        with self._lock:
            writer["retired"] = True
            try:
                writer["handle"].close()
            except (OSError, ValueError):
                pass
            try:
                os.unlink(writer["temp"])
            except OSError:
                pass

    def _evict(self, keep: str) -> None:
        """The print-level size policy (the review's unified-lifecycle
        finding): one print folder's total cost is the index AND the
        prepared representation together, and an evicted print loses
        the WHOLE folder — never an orphaned half. The walk is the
        SHARED eviction policy (CachePolicy.evict_to_budget): true
        LRU — the least recently read unprotected folders go first,
        and the eviction never finishes over budget while an
        unprotected folder remains. The protected path (the
        current/just-written entry) always survives; if it alone
        exceeds the budget, that is the only acceptable overage."""
        try:
            totals = {}
            for root, _dirs, names in os.walk(self.directory):
                folder = os.path.basename(root)
                if not folder.startswith("p-"):
                    continue
                try:
                    stats = [os.stat(os.path.join(root, name)) for name in names
                             if name.endswith((".mpfp", ".mpfi.gz"))]
                    if not stats:
                        continue
                    totals[root] = (max(stat.st_atime for stat in stats),
                                    sum(stat.st_size for stat in stats))
                except OSError:
                    continue
            keep_dir = os.path.dirname(keep) if keep else None
            retained, _entries = evict_to_budget(
                totals, self.max_bytes, None, keep_dir)
            if retained > self.max_bytes:
                # The only survivor is the protected entry (or an
                # unremovable folder) — the one acceptable overage.
                _log("cache print budget unreachable: %d bytes retained "
                     "with the protected entry", retained)
        except OSError:
            pass
