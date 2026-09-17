"""The UI-state store (4.3.0): the state file's second consumer.

The Monitor's save payload rewrites eight whole top-level keys per
save, so the UI state lives as a top-level SIBLING of those eight —
the sections map — never nested, or a merge would erase it. The
shared StateStore's merge semantics do the heavy lifting; this owner
adds the schema validation and the boundary guard: a value that
cannot survive the JSON round-trip fails HERE, with these words —
never inside the Monitor's save with the Monitor's failure text.
(The pane-size schema was removed in the re-review: no producer, no
consumer. The pane-sizes promise was corrected out of the docs in
4.4.0 — the console stays the only resizable pane.)
"""
from __future__ import annotations

import json

from UM.Logger import Logger


class UiStateStore:
    """Owns the sections map and section-layout persistence."""

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

    def set_section_layout(self, layout: dict) -> bool:
        """Persist the section-layout document under its key (the
        literal matches SectionLayoutPolicy.SECTION_LAYOUT_KEY — this
        owner imports nothing). The caller normalises; this owner only
        enforces the round-trip guard."""
        if not self._survives(layout):
            Logger.log("w", "Moonraker UI state: the section layout did not survive validation — the save was skipped.")
            return False
        return self._store.write({"sectionLayout": layout})

    def _survives(self, payload: dict) -> bool:
        """The boundary guard: a value that cannot round-trip
        through JSON would fail the SHARED save inside the Monitor's
        own sentence — the failure is attributed here instead."""
        try:
            return json.loads(json.dumps(payload, allow_nan=False)) == payload
        except (TypeError, ValueError):
            return False
