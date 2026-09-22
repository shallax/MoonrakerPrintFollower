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
    def __init__(self, cache_root: str, identity_source, service, parent=None):
        self._cache_root = cache_root
        self._identity_source = identity_source
        self._service = service
        self._machine_hash = self._hash(self._identity_source())
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
        index and prepared table are siblings under one directory."""
        print_root = os.path.join(self._cache_root, "cache-v2", self._machine_hash, "prints")
        self._service.rebind_stores(
            PersistentIndexCache(print_root), PreparedCache(print_root),
            initial=initial)

    def follow(self) -> None:
        """The binding's changed signal: rebind only when the durable
        machine identity actually changed. The first resolution moves
        the stores off the unknown hash, so a printer that appeared
        after construction never strands data there."""
        machine_hash = self._hash(self._identity_source())
        if machine_hash == self._machine_hash:
            return
        self._machine_hash = machine_hash
        self._bind()
        Logger.log("i", "Moonraker cache namespace switched to machine %s", machine_hash)

    @property
    def machine_hash(self) -> str:
        return self._machine_hash
