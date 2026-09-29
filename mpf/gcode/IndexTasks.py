"""Worker-local indexing tasks with submission-time inputs.

The service owns scheduling, generations and completion publication. Tasks
hold only their index, lease, stores and cancellation capabilities; they never
read the coordinator or mutate its lifecycle flags.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import time

from UM.Logger import Logger
from .MotionIndex import LayerMotionIndex
from .IndexHydrator import hydrate_layer_from_file
from .IndexWork import HydrationYield, passive_yield
from .LayerCache import _decoded_charge
from .PlateProgress import (
    PreparationYield, decode_layer as _decode_layer,
    encode_layer as _encode_layer, prepare_layer as _prepare_layer,
)
from .PreparedStore import STATE_UNCACHEABLE

_FULL_PREP_BATCH_S = 0.12


@dataclass(frozen=True)
class PreparedLayerReader:
    """The prepared source captured at submission, never a live service lookup.

    The loaded table is immutable for its lifetime; holding its reference is
    constant-time even for a very large print. Rebinding the service replaces
    its table/store but cannot redirect an already submitted worker.
    """
    store: Any
    identity: Any
    table: Any

    def read(self, layer):
        if self.store is None or self.table is None:
            return None
        return self.store.read(self.identity, self.table, layer)


@dataclass(frozen=True)
class LayerHydrationTask:
    index: LayerMotionIndex
    layers: tuple[int, ...]
    packed: Any
    lease: Any
    arrays_owed: frozenset[int]
    background: bool
    cancelled: Any
    immutable: bool
    store: Any
    writer: Any
    foreground: Any
    read_prepared: Callable[[int], Any]

    def run(self):
        failed = []
        stash = {}
        yield_at = time.monotonic()

        def decode_checkpoint():
            nonlocal yield_at
            if self.cancelled.is_set() or (self.background and self.foreground.is_set()):
                raise PreparationYield()
            yield_at = passive_yield(time.monotonic(), yield_at)

        for layer in self.layers:
            yield_at = passive_yield(time.monotonic(), yield_at)
            raw = self.packed.peek(layer)  # peek: the worker never reorders
            ram_hit = raw is not None
            if raw is None:
                raw = self.read_prepared(layer)
            if raw is not None:
                try:
                    decoded = _decode_layer(raw, checkpoint=decode_checkpoint,
                                            immutable=self.immutable)
                    stash[layer] = (raw, decoded, ram_hit,
                                    _decoded_charge(raw=raw, payload=decoded))
                except PreparationYield:
                    break  # A newly selected layer outranks speculative decode.
                except Exception:
                    failed.append(layer)
                    continue
                # The prepared/packed source serves the
                # PRESENTATION but never fills the index's
                # motion arrays — hydrating them here is what
                # gives the split its exact parser-anchored
                # mapping. A decode that succeeded stays
                # served even if the array hydration fails;
                # the failure only degrades the split and is
                # named, never latched.
                if layer in self.arrays_owed:
                    if self.lease is None:
                        Logger.log(
                            "w",
                            "layer %d arrays stay unhydrated: "
                            "no gcode lease — the split rides "
                            "the estimate", layer)
                    elif not hydrate_layer_from_file(self.index, self.lease.path, layer):
                        Logger.log(
                            "w",
                            "layer %d arrays failed to hydrate "
                            "from %s — the split rides the "
                            "estimate", layer, self.lease.path)
                continue
            # Hydrated arrays are a complete source in their own
            # right. Non-compact indexes always take this branch;
            # compact indexes take it while the retention window
            # still holds the layer. No raw lease is needed.
            hydrated = not self.index.compact or layer in self.index.hydrated_layers
            if not hydrated:
                if self.lease is None:
                    failed.append(layer)
                    continue
                result = hydrate_layer_from_file(self.index, self.lease.path, layer)
                if not result:
                    failed.append(layer)
                    continue
            try:
                payload = (_prepare_layer(self.index, layer, should_yield=self.foreground.is_set)
                           if self.background else _prepare_layer(self.index, layer))
            except PreparationYield:
                break
            if payload is None:
                # A hydrate that succeeded but prepared nothing
                # is not a hydrate failure: it must not latch
                # (the latch exists to stop whole-file re-reads,
                # and a re-ask here costs neither).
                continue
            encoded = None
            # An encode failure must never cost the layer its
            # display — the decoded payload still lands in the
            # hot cache (the compact store just misses it).
            try:
                encoded = _encode_layer(payload)
            except Exception:
                pass
            if encoded is not None and self.writer is not None:
                # Every successfully encoded layer enters the
                # incremental writer exactly once, whichever path
                # produced it: the demand's layer must never
                # publish as a (0, 0) hole merely because the
                # pass found it cached. The write rides the
                # worker — the commit's copy of it was on the UI
                # thread — and the store refuses a second append
                # to a filled slot, so beating the pass to the
                # layer cannot double-write it.
                self.store.append(self.writer, layer, encoded)
            stash[layer] = (encoded, payload, False,
                            _decoded_charge(raw=encoded, payload=payload))
        return failed, stash


@dataclass(frozen=True)
class LayerPreparationTask:
    index: LayerMotionIndex
    packed: Any
    start: int
    lease: Any
    read_prepared: Callable[[int], Any]
    table: Any
    store: Any
    writer: Any
    foreground: Any
    failed_layers: frozenset[int]

    def run(self):
        encoded = {}
        uncacheable = set()
        frontier = self.start
        # The budget starts HERE, where the batch actually runs.
        # Measured from the submission it also spent the pool's
        # queue delay, and a busy machine expired the slice
        # before the first layer — the walk then reported the
        # frontier it was handed, never the one it reached.
        deadline = time.monotonic() + _FULL_PREP_BATCH_S
        yield_at = time.monotonic()

        def demand_pending():
            # The demand event is read FIRST and alone decides
            # the interrupt: a foreground request must never
            # wait out the interval. Only an idle check pays
            # the passive hand-back, so the interior of a dense
            # layer keeps the UI thread fed as well.
            nonlocal yield_at
            if self.foreground.is_set():
                return True
            yield_at = passive_yield(time.monotonic(), yield_at)
            return False

        while time.monotonic() < deadline:
            # The loop-top yield reads the thread-safe EVENT,
            # never the mutable hydrate set across the thread
            # boundary — the owner records every demand in
            # both, but only the event is the worker's signal.
            if demand_pending():
                break  # a demand arrived — it outranks the pass
            layer = frontier
            if layer >= len(self.index.ranges):
                break
            if layer in self.failed_layers:
                # The latch applies to the pass too: a refused
                # layer must not retry every poll (the same
                # whole-file re-read the demand path avoids).
                # Its slot stays EMPTY: the next session
                # retries it.
                frontier = layer + 1
                continue
            packed = self.packed.peek(layer)
            if packed is not None:
                # A demand prepared this layer before the pass
                # reached it: the writer receives the bytes
                # HERE, so the pass's finish can never publish
                # a hole for a layer that WAS prepared.
                if self.writer is not None:
                    self.store.append(self.writer, layer, packed)
                frontier = layer + 1
                continue
            if self.table is not None and layer < len(self.table) \
                    and self.table[layer][0] == STATE_UNCACHEABLE:
                # The repair copy: an UNCACHEABLE layer rides
                # into the new writer WITHOUT a re-walk — the
                # codec's refusal stands across sessions.
                uncacheable.add(layer)
                frontier = layer + 1
                continue
            raw = self.read_prepared(layer) if self.table else None
            if raw is not None:
                # The repair copy :
                # the old file's valid layer rides into the
                # new writer — the rebuild never loses an
                # entry while regenerating another. The bytes
                # are read anyway for the table walk; the
                # copy costs a write, not a decode.
                if self.writer is not None:
                    self.store.append(self.writer, layer, raw)
                frontier = layer + 1
                continue
            if self.index.compact and layer not in self.index.hydrated_layers:
                try:
                    if self.lease is None or not hydrate_layer_from_file(
                            self.index, self.lease.path, layer,
                            should_stop=self.foreground.is_set):
                        break
                except HydrationYield:
                    # A demand outranks the pass, and the layer
                    # is abandoned BEFORE its arrays publish, so
                    # nothing is latched and the frontier does
                    # not advance: the foreground runs first and
                    # this layer is reached again.
                    break
            try:
                payload = _prepare_layer(self.index, layer, demand_pending)
            except PreparationYield:
                # Do not advance the frontier: this layer was
                # deliberately abandoned before memo/publication.
                # _finish() will immediately run the foreground
                # demand, then resume this layer later.
                break
            if payload is not None:
                try:
                    packed = _encode_layer(payload)
                    encoded[layer] = packed
                    if self.writer is not None:
                        self.store.append(self.writer, layer, packed)
                except Exception:
                    # A layer the codec cannot hold simply
                    # stays out of the cache; the pass must
                    # walk on, never stall. The explicit
                    # UNCACHEABLE state records the refusal —
                    # the next session never retries it.
                    uncacheable.add(layer)
            frontier = layer + 1
        return frontier, encoded, uncacheable


@dataclass(frozen=True)
class LayerArrayTask:
    index: LayerMotionIndex
    layers: tuple[int, ...]
    lease: Any
    foreground: Any

    def run(self):
        yield_at = time.monotonic()
        for layer in self.layers:
            yield_at = passive_yield(time.monotonic(), yield_at)
            if not self.index.compact or layer in self.index.hydrated_layers:
                continue
            try:
                if not hydrate_layer_from_file(
                        self.index, self.lease.path, layer,
                        should_stop=self.foreground.is_set):
                    Logger.log("w", "layer %d arrays failed to hydrate from %s — "
                               "the split rides the estimate", layer, self.lease.path)
            except HydrationYield:
                # The debt is a background errand: a demand stops it
                # here rather than latching the rest as failures.
                break
        return [], {}
