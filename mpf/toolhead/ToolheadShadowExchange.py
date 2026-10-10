"""Pure shadow-key admission around the existing two-pair fence mailbox.

Worker storage must select the reserved physical index, not its private front
swap. Only an exact selected source/light/pose key can be read. The caller owns
all GL fences and drains them before acknowledging retirement or closing maps.
"""
from dataclasses import dataclass
from threading import RLock

from .ToolheadEnvironmentMailbox import EnvironmentMailbox
from .ToolheadShadowValues import ShadowMapPlan, ShadowProjection, certify_key


@dataclass(frozen=True)
class ShadowSet:
    key: object
    plan: ShadowMapPlan
    maps: tuple

    def __post_init__(self):
        if self.key is None:
            raise ValueError('Completed shadow set needs a source key')
        certify_key(self.key)
        if not isinstance(self.plan, ShadowMapPlan) or not self.plan.lights:
            raise ValueError('Completed shadow set needs a complete admitted light plan')
        values = tuple(tuple(entry) for entry in self.maps)
        if len(values) != len(self.plan.lights) or any(len(entry) != 3 for entry in values):
            raise ValueError('Completed shadow map set is incomplete')
        for entry, light in zip(values, self.plan.lights, strict=True):
            identity, texture, projection = entry
            identity = tuple(identity)
            if (identity != (light.kind, light.index) or type(texture) is not int or texture <= 0 or
                    not isinstance(projection, ShadowProjection) or projection.origin != light.position):
                raise ValueError('Completed shadow map does not match its light plan')
        if len({entry[1] for entry in values}) != len(values):
            raise ValueError('Completed shadow maps alias distinct lights')
        # Freeze each nested identity too; mutable caller lists cannot change
        # either sampler delivery or the source key after completion.
        object.__setattr__(self, 'maps', tuple((tuple(identity), texture, projection)
                                             for identity, texture, projection in values))


@dataclass(frozen=True)
class ShadowWrite:
    token: object
    key: object
    generation: int


class ShadowExchange:
    def __init__(self):
        self._lock = RLock()
        self._mailbox = EnvironmentMailbox()
        self._selected = self._front = None
        self._writers = {}
        self._inflight = {}

    def select(self, key):
        if key is None:
            raise ValueError('Shadow demand needs a source key; disable closes the owner')
        certify_key(key)
        with self._lock:
            if self._mailbox.closed:
                return None
            if key != self._selected:
                self._selected = key
                self._mailbox.change_generation()
            return self._mailbox.generation

    def reserve(self, key, generation):
        """No duplicate capture for a completed or in-flight exact key."""
        certify_key(key)
        with self._lock:
            if (self._mailbox.closed or key != self._selected or generation != self._mailbox.generation or
                    (self._front is not None and self._front.key == key) or
                    any(write.key == key and write.generation == generation for write in self._inflight.values())):
                return None
            token = self._mailbox.reserve(generation)
            if token is None:
                return None
            write = ShadowWrite(token, key, generation)
            self._writers[token] = self._inflight[token] = write
            return write

    def _writer(self, write):
        if not isinstance(write, ShadowWrite) or self._writers.get(write.token) is not write:
            raise RuntimeError('Shadow producer ticket is not active')

    def building_current(self, write):
        with self._lock:
            self._writer(write)
            return self._mailbox.building_current(write.token)

    def complete(self, write, result, producer_fence, *, gpu_complete=False):
        with self._lock:
            self._writer(write)
            if not isinstance(result, ShadowSet) or result.key != write.key:
                raise ValueError('Completed shadow result belongs to another source key')
            ready = self._mailbox.complete(write.token, result, producer_fence, gpu_complete=gpu_complete)
            del self._writers[write.token]
            return ready

    def abort(self, write, producer_fence, *, gpu_complete=False):
        with self._lock:
            self._writer(write)
            self._mailbox.abort(write.token, producer_fence, gpu_complete=gpu_complete)
            del self._writers[write.token]

    def begin_poll(self):
        with self._lock:
            return self._mailbox.begin_poll()

    def end_poll(self, use, producer_complete):
        with self._lock:
            adopted = self._mailbox.end_poll(use, producer_complete)
            if adopted is not None:
                token, result, fence = adopted
                self._inflight.pop(token)
                self._front = result
            return adopted

    def begin_read(self, key):
        """Retained old storage never shadows a different source generation."""
        certify_key(key)
        with self._lock:
            if (key != self._selected or self._front is None or self._front.key != key):
                return None
            return self._mailbox.begin_read()

    def end_read(self, use, flushed_fence, *, gpu_complete=False):
        with self._lock:
            return self._mailbox.end_read(use, flushed_fence, gpu_complete=gpu_complete)

    def take_retirement(self):
        with self._lock:
            return self._mailbox.take_retirement()

    def retired(self, token):
        with self._lock:
            self._mailbox.retired(token)
            self._inflight.pop(token, None)

    def close(self):
        with self._lock:
            self._mailbox.close()
            self._front = self._selected = None

    @property
    def drained(self):
        with self._lock:
            return self._mailbox.drained
