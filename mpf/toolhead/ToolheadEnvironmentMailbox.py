"""Two reflection pairs with explicit producer and consumer admission.

This owner contains no Qt or GL calls. Callers acquire tickets under its lock,
perform driver work outside it, then return the tickets even on cancellation.
Fence deletion and pair reuse belong exclusively to the retirement owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class PairToken:
    epoch: object
    index: int
    serial: int


@dataclass(frozen=True)
class PairUse:
    token: PairToken
    nonce: object


@dataclass
class _Pair:
    state: str = 'free'
    serial: int = 0
    generation: int = -1
    payload: object = None
    producer_fence: object = None
    consumer_fence: object = None
    poller: object = None
    reader: object = None


class EnvironmentMailbox:
    """Keep the completed front visible while its replacement is built.

    A token names one allocation cycle in one mailbox/context epoch. An
    admitted poll or draw pins both that cycle and its relevant GL resources.
    Close seals demand immediately, without requiring any later GUI frame.
    """
    def __init__(self):
        self._lock = RLock()
        self._epoch = object()
        self._pairs = [_Pair(), _Pair()]
        self._front = None
        self.generation = 0
        self.closed = False

    def _pair(self, token):
        if (not isinstance(token, PairToken) or token.epoch is not self._epoch
                or not 0 <= token.index < len(self._pairs)):
            raise RuntimeError('Reflection pair belongs to another owner')
        pair = self._pairs[token.index]
        if pair.serial != token.serial:
            raise RuntimeError('Reflection pair ticket has expired')
        return pair

    def reserve(self, generation=None):
        with self._lock:
            if self.closed or (generation is not None and generation != self.generation): return None
            for index, pair in enumerate(self._pairs):
                if pair.state == 'free':
                    pair.serial += 1
                    pair.generation = self.generation
                    pair.state = 'building'
                    return PairToken(self._epoch, index, pair.serial)
            return None

    def building_current(self, token):
        with self._lock:
            pair = self._pair(token)
            return pair.state == 'building' and pair.generation == self.generation and not self.closed

    def complete(self, token, payload, producer_fence, *, gpu_complete=False):
        """Publish only the generation actually reserved, never caller state.

        A failed fence creation requires a completed producer glFinish before
        the caller may pass gpu_complete=True. The same rule applies to abort.
        """
        with self._lock:
            pair = self._pair(token)
            if pair.state != 'building': raise RuntimeError('Reflection producer ticket is not active')
            if producer_fence is None and not gpu_complete:
                raise RuntimeError('Reflection producer completion is unverified')
            pair.payload, pair.producer_fence = payload, producer_fence
            pair.state = 'ready' if pair.generation == self.generation and not self.closed else 'sealed'
            return pair.state == 'ready'

    def abort(self, token, producer_fence, *, gpu_complete=False, payload=None):
        """Seal work and its retained sources with the last GPU command."""
        with self._lock:
            pair = self._pair(token)
            if pair.state != 'building': raise RuntimeError('Reflection producer ticket is not active')
            if producer_fence is None and not gpu_complete:
                raise RuntimeError('Reflection producer completion is unverified')
            pair.payload, pair.producer_fence = payload, producer_fence
            pair.state = 'sealed'

    def begin_poll(self):
        with self._lock:
            if self.closed: return None
            for index, pair in enumerate(self._pairs):
                if pair.state == 'ready' and pair.poller is None:
                    pair.poller = object()
                    return PairUse(PairToken(self._epoch, index, pair.serial), pair.poller), pair.producer_fence
            return None

    def end_poll(self, use, producer_complete):
        """Return the poll pin and atomically recheck cancellation/adoption.

        On adoption the main owner receives the producer fence for deletion.
        Otherwise the worker retains it for a later poll or sealed retirement.
        A false result is also the mandatory finally path after a failed poll.
        """
        with self._lock:
            if not isinstance(use, PairUse): raise RuntimeError('Reflection producer poll is not active')
            pair = self._pair(use.token)
            if pair.poller is None or pair.poller is not use.nonce: raise RuntimeError('Reflection producer poll is not active')
            pair.poller = None
            if pair.state != 'ready' or self.closed or pair.generation != self.generation or not producer_complete:
                return None
            if self._front is not None: self._pair(self._front).state = 'sealed'
            pair.state = 'displayed'
            fence, pair.producer_fence = pair.producer_fence, None
            self._front = use.token
            return use.token, pair.payload, fence

    def begin_read(self):
        """Admit before retaining texture names or descriptor for binding."""
        with self._lock:
            if self.closed or self._front is None: return None
            pair = self._pair(self._front)
            if pair.state != 'displayed' or pair.reader is not None: return None
            pair.reader = object()
            return PairUse(self._front, pair.reader), pair.payload

    def end_read(self, use, flushed_fence, *, gpu_complete=False):
        """Record last use, including a draw during which close intervened.

        The previous consumer fence is returned to its main-thread owner for
        deletion. Failure to create a fence must first complete that context's
        submitted draws; it must never silently unpin still-running GPU work.
        """
        with self._lock:
            if not isinstance(use, PairUse): raise RuntimeError('Reflection consumer ticket is not active')
            pair = self._pair(use.token)
            if pair.reader is None or pair.reader is not use.nonce: raise RuntimeError('Reflection consumer ticket is not active')
            if flushed_fence is None and not gpu_complete:
                raise RuntimeError('Reflection consumer completion is unverified')
            previous, pair.consumer_fence = pair.consumer_fence, flushed_fence
            pair.reader = None
            return previous

    def change_generation(self):
        with self._lock:
            if self.closed: return
            self.generation += 1
            for pair in self._pairs:
                if pair.state == 'ready': pair.state = 'sealed'
            # Completed front is intentionally retained until replacement.

    def close(self):
        with self._lock:
            if self.closed: return
            self.closed = True
            self.generation += 1
            self._front = None
            for pair in self._pairs:
                if pair.state in ('ready', 'displayed'): pair.state = 'sealed'
            # Active producer must abort/complete in its worker finally path.

    def take_retirement(self):
        """Claim fences only when no outside-lock poll or draw can use them."""
        with self._lock:
            for index, pair in enumerate(self._pairs):
                if pair.state == 'sealed' and pair.poller is None and pair.reader is None:
                    pair.state = 'draining'
                    return PairToken(self._epoch, index, pair.serial), pair.payload, pair.producer_fence, pair.consumer_fence
            return None

    def retired(self, token):
        """Acknowledge only after both fence drains and resource cleanup."""
        with self._lock:
            pair = self._pair(token)
            if pair.state != 'draining': raise RuntimeError('Reflection retirement ticket is not active')
            pair.payload = pair.producer_fence = pair.consumer_fence = None
            pair.state = 'free'

    @property
    def drained(self):
        with self._lock:
            return self.closed and all(pair.state == 'free' for pair in self._pairs)
