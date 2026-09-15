"""The UI-state store (4.3.0): the state file's second consumer.

The Monitor's save payload rewrites nine whole top-level keys per
save, so the UI state lives as top-level SIBLINGS of those nine —
the sections map's persistence and the sizes schema ("sectionSizes",
the tenth key) — never nested, or a merge would erase it. The shared
StateStore's merge semantics do the heavy lifting; this owner adds
the schema validation and the boundary guard: a value that cannot
survive the JSON round-trip fails HERE, with these words — never
inside the Monitor's save with the Monitor's failure text.
"""
from __future__ import annotations

import json
import math

from UM.Logger import Logger


class UiStateStore:
    """Owns the sections map's persistence and the sizes schema."""

    def __init__(self, store):
        # The shared StateStore (the model's own instance) — one
        # store, one file, two consumers' features.
        self._store = store

    def set_sections(self, sections: dict) -> bool:
        """Persist the collapse map under its existing key (booleans
        only — a shape change or a renamed id silently re-expands
        every existing user's sections)."""
        payload = {str(key): bool(value) for key, value in dict(sections or {}).items()}
        if not self._survives(payload):
            Logger.log("w", "Moonraker UI state: the sections map did not survive validation — the save was skipped.")
            return False
        return self._store.write({"sections": payload})

    def set_sizes(self, sizes: dict) -> bool:
        """Persist the pane sizes under the tenth top-level key. The
        schema: string pane ids to finite numbers — anything else is
        dropped at the boundary, never written."""
        payload = {}
        for key, value in dict(sizes or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                payload[str(key)] = number
        if not self._survives(payload):
            Logger.log("w", "Moonraker UI state: the sizes map did not survive validation — the save was skipped.")
            return False
        return self._store.write({"sectionSizes": payload})

    def _survives(self, payload: dict) -> bool:
        """The boundary guard: a value that cannot round-trip
        through JSON would fail the SHARED save inside the Monitor's
        own sentence — the failure is attributed here instead."""
        try:
            return json.loads(json.dumps(payload, allow_nan=False)) == payload
        except (TypeError, ValueError):
            return False
