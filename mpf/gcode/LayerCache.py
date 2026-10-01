"""Byte-bounded packed/decoded layer caches and memory charging."""
from __future__ import annotations


from collections import OrderedDict
import sys


# The RAM tier budgets , from the
# measured real print (467 MB, 327 layers): packed layers run
# 13.5 KB - 927 KB (p50 610 KB), decoded layers 0.2 MB - 11.3 MB
# (p50 8.0 MB). 64 MB holds ~105 median packed layers — a third of
# the print — and 128 MB holds six dense decoded windows with room.
_FULL_CACHE_MAX_BYTES = 64 * 1024 * 1024
_DECODED_LRU_MAX_BYTES = 128 * 1024 * 1024
_GPU_DECODED_LRU_MAX_BYTES = 256 * 1024 * 1024
# The decoded LRU's guaranteed floor: ONE entry — the just-committed
# layer before its render wrappers pin it. The live and frozen
# windows' protection moved to the pins (the wrappers charge their
# payloads against the budget explicitly), so the old 4-entry floor's
# RAM no longer outvotes the byte bound.
_DECODED_LRU_MIN_ENTRIES = 1
# Decoded PPL1 expands into nested Python lists. The measured branch
# ratios peak around the mid-teens, so charge a conservative 16x packed
# size instead of recursively walking every decoded point a second time.
# This is accounting, not serialization: an overestimate is safe and
# keeps the byte budget bounded without adding O(points) seek latency.
_DECODED_PACKED_EXPANSION = 16
_DECODED_CHARGE_FLOOR = 4 * 1024


def _decoded_charge(raw=None, payload=None) -> int:
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return max(_DECODED_CHARGE_FLOOR, len(raw) * _DECODED_PACKED_EXPANSION)
    # Encoding failures are exceptional, but display must still work.
    # Charge from the already-known motion count without another geometry
    # traversal. 256 bytes/motion is deliberately conservative.
    motions = 0
    if isinstance(payload, dict):
        try:
            motions = max(0, int(payload.get("motions") or 0))
        except (TypeError, ValueError):
            motions = 0
    return max(_DECODED_CHARGE_FLOOR, motions * 256)


def _deep_size(obj) -> int:
    """A decoded payload's true byte footprint (its points dominate;
    a shallow getsizeof misses them)."""
    total = 0
    seen = set()

    def walk(o):
        nonlocal total
        oid = id(o)
        if oid in seen:
            return
        seen.add(oid)
        try:
            total += sys.getsizeof(o)
        except TypeError:
            return
        if isinstance(o, dict):
            for key, value in o.items():
                walk(key)
                walk(value)
        elif isinstance(o, (list, tuple)):
            for value in o:
                walk(value)

    walk(obj)
    return total


class _ByteBoundedLru:
    """A byte-budgeted access-order cache with a minimum-entry floor.
    ``peek`` reads without touching the order (the worker's path —
    only the owner mutates); ``set`` and ``get`` refresh recency.
    Sizes are explicit (the worker measures); a bare ``__setitem__``
    (the tests' fixture path) falls back to the deep walk."""

    def __init__(self, max_bytes: int, min_entries: int = 0) -> None:
        self._data = OrderedDict()
        self._sizes = {}
        self._bytes = 0
        self.max_bytes = max_bytes
        self.min_entries = min_entries
        # The demanded windows (the live print's ±1 and the detached
        # follower's frozen ±1): eviction skips these layers — the
        # owner updates the set as the anchors move.
        self.protected = set()

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        value = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        self.set(key, value, _deep_size(value))

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def peek(self, key):
        return self._data.get(key)

    def get(self, key):
        value = self._data.get(key)
        if value is not None:
            self._data.move_to_end(key)
        return value

    def touch(self, key) -> None:
        if key in self._data:
            self._data.move_to_end(key)

    def set(self, key, value, size: int) -> None:
        if key in self._data:
            self._bytes -= self._sizes.get(key, 0)
        self._data[key] = value
        self._sizes[key] = size
        self._bytes += size
        self._trim()

    def update(self, items) -> None:
        for key, value in items.items():
            if key in self._data:
                self._bytes -= self._sizes.get(key, 0)
            self._data[key] = value
            self._sizes[key] = len(value) if isinstance(value, (bytes, bytearray)) else _deep_size(value)
            self._bytes += self._sizes[key]
        self._trim()

    def pop(self, key, default=None):
        value = self._data.pop(key, default)
        if value is not default:
            self._bytes -= self._sizes.pop(key, 0)
        return value

    def popitem(self, last=True):
        key, value = self._data.popitem(last)
        self._bytes -= self._sizes.pop(key, 0)
        return key, value

    def move_to_end(self, key) -> None:
        self._data.move_to_end(key)

    def clear(self) -> None:
        self._data.clear()
        self._sizes.clear()
        self._bytes = 0

    def total_bytes(self) -> int:
        return self._bytes

    def _trim(self) -> None:
        # The floor (the decoded LRU's): below it, never evict — the
        # active windows must survive a single pathological layer.
        # The protected windows (the live ±1 and the frozen ±1) are
        # skipped: evicting a demanded layer flips the memoised
        # bundle's decoded bit and the demand re-decodes it — a
        # per-poll eviction/redemption thrash (the detached 114%
        # burn).
        while self._bytes > self.max_bytes and len(self._data) > self.min_entries:
            victim = None
            for key in self._data:
                if key not in self.protected:
                    victim = key
                    break
            if victim is None:
                break
            del self._data[victim]
            self._bytes -= self._sizes.pop(victim, 0)
