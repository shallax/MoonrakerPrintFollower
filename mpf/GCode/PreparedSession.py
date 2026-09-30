"""The prepared store's session: its table, its writer and its coverage.

The file-backed prepared cache outlives a service instance, and one
service serves many files and machines. What this owner holds whole,
rather than the service spelling it out at each of the places that
touched it:

- the adopted table for the file being served, behind the identity
  STRENGTH gate (only a reliable modified timestamp may reuse persisted
  geometry across sessions — a uuid is Moonraker's per-extraction token,
  never content identity);
- the incremental writer, opened once per session on the owner thread
  and captured for the worker that appends to it, RETIRED before every
  checkpoint so no worker can write to it after a cutover;
- the coverage census — the layers RESOLVED, held and refused alike,
  which is the pass fraction's truth, never the RAM tier's residency;
- the completeness and published latches, and the one autonomous
  rebuild a failed final publish is allowed.

It owns no format (``PreparedStore`` does), no RAM tier (``LayerCache``,
held by the service) and no scheduling generation: the store a worker
was submitted for is captured by the service, and this owner refuses an
append to a retired writer rather than deciding who may write.
"""
from __future__ import annotations

from UM.Logger import Logger

from .IndexTasks import PreparedLayerReader
from .PreparedStore import STATE_CACHED, STATE_EMPTY, STATE_UNCACHEABLE


class PreparedSession:
    def __init__(self, store=None) -> None:
        self._store = store
        self._table = None
        self._identity = None
        self._writer = None
        self._saved = False
        self._retry_count = 0
        self._complete = False
        self._flag_complete = False
        self._coverage = set()

    # ---- what the service reads ------------------------------------

    @property
    def store(self): return self._store

    @property
    def identity(self): return self._identity

    @identity.setter
    def identity(self, identity): self._identity = identity

    @property
    def table(self): return self._table

    @table.setter
    def table(self, table): self._table = table

    @property
    def writer(self): return self._writer

    @writer.setter
    def writer(self, writer): self._writer = writer

    @property
    def saved(self): return self._saved

    @saved.setter
    def saved(self, saved): self._saved = saved

    @property
    def complete(self): return self._complete

    @complete.setter
    def complete(self, complete): self._complete = complete

    @property
    def coverage(self): return self._coverage

    @coverage.setter
    def coverage(self, coverage): self._coverage = coverage

    def has_source(self) -> bool:
        """A store and an identity: a session that can read and write."""
        return self._store is not None and self._identity is not None

    # ---- reading ---------------------------------------------------

    def served(self, layer) -> bool:
        """The table says this layer's payload is READABLE without the
        raw G-code file — a table-tuple probe, never a payload read (the
        lease decision must not cost a disk read on the owner thread)."""
        if self._store is None or self._table is None:
            return False
        if layer < 0 or layer >= len(self._table):
            return False
        state, _offset, length = self._table[layer]
        return state == STATE_CACHED and length > 0

    def uncacheable(self, layer) -> bool:
        """The codec walked this layer and refused it: resolved, and
        never retried by a later pass."""
        return (self._table is not None and 0 <= layer < len(self._table)
                and self._table[layer][0] == STATE_UNCACHEABLE)

    def reader(self):
        """The worker-side prepared source, captured at submission: the
        loaded table is immutable for its lifetime, so holding its
        reference is constant-time even for a very large print."""
        return PreparedLayerReader(self._store, self._identity, self._table).read

    def fraction(self, total):
        """The pass's honest progress over the PERSISTED store. None
        when no persistence is configured: the caller then falls back
        to the RAM tier's residency, which is the only prepared store
        there is."""
        if self._complete:
            return 1.0
        if not self.has_source():
            return None
        return min(1.0, len(self._coverage) / total)

    # ---- the session's own lifecycle -------------------------------

    def open(self, identity) -> None:
        """Open the table for the current file's prepared cache (a
        one-shot per identity). The adoption obeys the SAME strength
        gate as the index restore: only a RELIABLE modified timestamp
        may reuse persisted geometry across sessions, so a re-extracted
        file can never resurrect stale geometry. The key still targets
        the same path: the fresh pass's publish replaces the old
        representation."""
        if self._store is None or identity is None:
            return
        key = identity.stable_key() if hasattr(identity, "stable_key") else None
        if key is None or key == self._identity:
            return
        self._identity = key
        # modified > 0 is the only voucher for cross-session reuse. A
        # name + size alone cannot tell two extractions apart — prefer
        # rebuilding over stale geometry.
        if not bool(getattr(identity, "modified", 0) > 0):
            self._forget()
            return
        self._adopt_table(self._store.load_table(key))

    def adopt(self, layer_count) -> bool:
        """The reopen policy once the view's layer count is known: a
        complete clean table takes the FAST path — the pass never walks
        the file again; a complete table with holes repairs (copy the
        valid, retry the holes); anything else prepares fresh. Returns
        True for the fast path, whose caller stands its own pass
        frontier down with it."""
        table = self._table
        self._complete = False
        if not table or self._store is None:
            return False
        if len(table) == layer_count and self._flag_complete \
                and all(entry[0] != STATE_EMPTY for entry in table):
            # A valid complete cache must not read its own bytes back
            # merely to rediscover the table: the frontier and the
            # saved latch both stand down the background pass for
            # good. UNCACHEABLE layers count as complete — the pass
            # already gave them its best attempt.
            self._complete = True
            self._saved = True
            Logger.log("i", "prepared store restored: %d/%d layers",
                       layer_count, layer_count)
            return True
        if len(table) != layer_count:
            # The file's layer count no longer matches this print's
            # index: the table is unusable, and the fresh pass will
            # overwrite the file.
            self._table = None
            self._coverage = set()
            return False
        covered = sum(1 for entry in table if entry[0] != STATE_EMPTY)
        Logger.log("i", "prepared store resumed: %d/%d layers", covered, layer_count)
        return False

    def persist(self, layer, encoded, layer_count) -> None:
        """Every successfully encoded layer enters the incremental
        writer exactly once, whichever path produced it: a demand-
        prepared layer must never become a (0, 0) hole merely because
        the background pass found it already in the RAM cache.

        The demand path does not commit through here: the worker that
        produced an encoding appends it itself, because this call's
        write+flush+seek+table-write+flush sat on the UI thread once
        per demanded layer. The captured writer and its retirement flag
        carry the ownership the generation check used to."""
        self._coverage.add(layer)
        writer = self.writer_target(layer_count)
        if writer is not None:
            self._store.append(writer, layer, encoded)

    def writer_target(self, layer_count=None):
        """The incremental writer, opened ONCE per session on the owner
        thread: a lazy open inside a worker would mutate owner state
        across the thread boundary, and could resurrect a writer a
        cutover had already retired. ``layer_count`` is the view's own;
        None (no view) opens nothing.

        Opening here also keeps the self-heal a failed publish relies
        on: the next demand opens a fresh writer and the finish retries
        it."""
        if self._store is None or self._identity is None or self._saved:
            return None
        if self._writer is None and layer_count is not None:
            self._writer = self._store.open_for_write(self._identity, layer_count)
        return self._writer

    def mark_uncacheable(self, layers) -> None:
        """The codec's refusals enter the writer's table so the next
        session never retries them. A retired or absent writer drops
        them: the refusal still counts as coverage, which the caller
        records through note_coverage."""
        writer = self._writer
        if writer is None:
            return
        for layer in layers:
            self._store.append_uncacheable(writer, layer)

    def note_coverage(self, layers) -> None:
        self._coverage.update(layers)

    def publish(self, ok: bool) -> bool:
        """The finalise's commit. The published file replaces the table
        this session holds: a fresh or repair pass's offsets differ from
        the old file's, and a stale table would serve mis-aligned reads
        for the rest of the session. The saved latch closes ONLY on the
        publish itself. Returns True when the published branch ran."""
        if not ok or self._identity is None:
            return False
        self._saved = True
        self._retry_count = 0
        loaded = self._store.load_table(self._identity)
        if loaded is not None:
            self._table = loaded["table"]
            self._flag_complete = loaded["complete"]
            self._coverage = {i for i, entry in enumerate(loaded["table"])
                              if entry[0] != STATE_EMPTY}
            self._complete = (loaded["complete"]
                              and all(entry[0] != STATE_EMPTY for entry in loaded["table"]))
        return True

    def arm_retry(self) -> bool:
        """A failed final publish gets ONE autonomous rebuild from the
        prepared sources already available — never a wait for another
        user demand. Returns True when the retry was armed."""
        if self._retry_count >= 1:
            return False
        self._retry_count += 1
        self._saved = False
        self._complete = False
        table = self._table or ()
        self._coverage = {i for i, entry in enumerate(table) if entry[0] != STATE_EMPTY}
        return True

    # ---- the writer's cutover --------------------------------------

    def abort(self, store=None) -> None:
        """Abandon an unfinished writer on every exit path: the temp
        file goes, the handle closes, and an old generation's worker can
        never finalise it."""
        if self._writer is None:
            return
        store = store if store is not None else self._store
        if store is not None:
            store.abort_write(self._writer)
        self._writer = None

    def suspend(self, store=None) -> None:
        """The normal-lifecycle checkpoint (the clean-shutdown finding):
        a close or a store rebind publishes the writer's committed
        layers as an INCOMPLETE store — the next session opens it and
        resumes from the EMPTY slots. Only a genuinely failed or stale
        writer is aborted. The store is the writer's OWN store — during
        a rebind the caller passes the OLD one explicitly, so the
        checkpoint lands in the machine whose pass produced it."""
        if self._writer is None:
            return
        store = store if store is not None else self._store
        if store is not None:
            covered = sum(1 for entry in self._writer["table"] if entry is not None)
            store.suspend_write(self._writer)
            Logger.log("i", "prepared checkpoint published on shutdown: %d layers",
                       covered)
        self._writer = None

    def retire(self, store=None) -> None:
        """The writer-ownership cutover: bind, close and the machine
        rebind all RETIRE before they checkpoint. The retired flag
        freezes every later append and finish, and the store's own lock
        makes the freeze and the publication one atomic step against an
        in-flight append — once this returns no worker can ever write to
        the writer again, and the checkpoint holds exactly the committed
        layers. No blocking beyond one bounded append: the suspend only
        ever waits for a write already in progress."""
        if self._writer is None:
            return
        self._writer["retired"] = True
        self.suspend(store)

    # ---- the service's own boundaries ------------------------------

    def rebind(self, store) -> None:
        """Install the machine's store and start a fresh session. The
        caller retires the OLD session's writer through the OLD store
        before this — the cutover's ordering — so an A writer can never
        publish into B's namespace."""
        self._store = store
        self.reset()

    def reset(self) -> None:
        """Everything a new identity, or a new machine, renders
        meaningless: the adopted table, the writer, the latches and the
        coverage census all belong to the file that was being served."""
        self._table = None
        self._identity = None
        self._writer = None
        self._saved = False
        self._retry_count = 0
        self._complete = False
        self._flag_complete = False
        self._coverage = set()

    def _forget(self) -> None:
        """The identity was refused or its table could not be read: no
        table, no coverage — but the identity stands, so a later open
        for the same key is still a one-shot."""
        self._table = None
        self._flag_complete = False
        self._coverage = set()

    def _adopt_table(self, loaded) -> None:
        if loaded is None:
            self._forget()
            return
        self._table = loaded["table"]
        self._flag_complete = loaded["complete"]
        # The resolved entries (CACHED and UNCACHEABLE alike) count as
        # coverage BEFORE the pass walks: a repair session starts at the
        # old file's fraction. The coverage truth — a layer the codec
        # refused is as resolved as one it held.
        self._coverage = {i for i, entry in enumerate(loaded["table"])
                          if entry[0] != STATE_EMPTY}
