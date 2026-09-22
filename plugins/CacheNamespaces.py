"""The per-machine cache namespace owner (the review's machine-
namespace finding): the runtime constructs ONE owner over ONE index
service, and the owner follows the binding's changed signal — a
machine switch rebinds the service's stores into the new machine's
prints root. The service survives (its rebind API cancels the old
generation and suspends its prepared writer into the OLD machine's
namespace), and a worker from the old machine can never commit into
the new one's stores."""
from __future__ import annotations

import hashlib
import os

from UM.Logger import Logger

from .GCodeIndex import PersistentIndexCache
from .PreparedStore import PreparedCache


class CacheNamespaces:
    def __init__(self, cache_root: str, identity_source, service, parent=None,
                 cache_bytes_source=None):
        self._cache_root = cache_root
        self._identity_source = identity_source
        self._service = service
        # The per-machine cache bound (the author's setting): the
        # ACTIVE machine's configured MiB limit — one half of the
        # binding KEY, read fresh at every bind so a machine switch
        # binds its own budget.
        self._cache_bytes_source = cache_bytes_source
        self._machine_hash = self._hash(self._identity_source())
        self._cache_bytes = None
        if self._cache_bytes_source is not None:
            self._cache_bytes = self._cache_bytes_source()
        self._bind(initial=True)
        Logger.log("i", "Moonraker cache namespace %s", self._machine_hash)

    @staticmethod
    def _hash(machine_id: str) -> str:
        """The machine identity's cache namespace token: the Cura
        machine id alone (never the display name, never a mutable
        URL, never a file uuid). An unresolved identity hashes like
        any other, but nothing is persisted under it while it stays
        unresolved."""
        return hashlib.sha256(str(machine_id).encode("utf-8")).hexdigest()[:24]

    def _bind(self, initial=False) -> None:
        """The unified per-print lifecycle (the review's finding):
        BOTH stores root at the machine's prints folder — one print's
        index and prepared table are siblings under one directory —
        and BOTH obey the SAME machine byte budget (the author's
        per-printer setting): one configured limit governs the whole
        unified cache, never a smaller index-side default that could
        evict whole folders early. The index's entry-count bound is
        disabled here — the byte budget is the user-facing limit,
        and a hidden 16-print cap would silently override it."""
        print_root = os.path.join(self._cache_root, "cache-v2", self._machine_hash, "prints")
        cache_bytes = self._cache_bytes
        prepared = PreparedCache(print_root) if cache_bytes is None \
            else PreparedCache(print_root, max_bytes=cache_bytes)
        index = PersistentIndexCache(print_root) if cache_bytes is None \
            else PersistentIndexCache(print_root, max_bytes=cache_bytes,
                                      max_entries=None)
        self._service.rebind_stores(index, prepared, initial=initial)

    def follow(self) -> None:
        """The effective binding KEY (the review's finding): the
        machine identity AND the configured byte budget together —
        either change rebinds the stores through the ordinary
        writer-retirement lifecycle, and anything else is a no-op,
        so the broad binding.changed signal costs nothing on an
        unrelated settings save. Idempotent by construction."""
        machine_hash = self._hash(self._identity_source())
        cache_bytes = None
        if self._cache_bytes_source is not None:
            cache_bytes = self._cache_bytes_source()
        if machine_hash == self._machine_hash and cache_bytes == self._cache_bytes:
            return
        previous_hash = self._machine_hash
        self._machine_hash = machine_hash
        self._cache_bytes = cache_bytes
        self._bind()
        if previous_hash != machine_hash:
            Logger.log("i", "Moonraker cache namespace switched to machine %s", machine_hash)
        else:
            Logger.log("i", "Moonraker cache budget switched to %d bytes",
                       cache_bytes or 0)

    @property
    def machine_hash(self) -> str:
        return self._machine_hash
