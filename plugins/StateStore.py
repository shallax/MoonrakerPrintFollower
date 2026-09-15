"""The Monitor state file's explicit owner (4.2.0, F11/A6): the
file semantics — the raw read, the read-modify-write that preserves
foreign keys, the atomic replace and the rate-limited failure
reporting. Pure: no Qt, no Resources; the caller passes the path so
the migration tests run without Cura. The model keeps the VALUE
coercion (its tests pin the fallback document); the store owns what
happens to the file."""
from __future__ import annotations

import json
import os
from typing import Callable, Optional


class StateStore:
    """One owner for the sections JSON's file semantics."""

    def __init__(self, path: str, note: Optional[Callable[[str, str], None]] = None):
        self._path = path
        # The failure sink: note(failure_class, text). Classes are
        # "read"/"write"; each reports once per session (the latch
        # resets on reset_failures — the model hooks it to the
        # session boundary, round-2 A6/M6).
        self._note = note
        self._reported = set()

    def read(self):
        """The decoded document for hydration, or None. A missing
        file is the FIRST RUN, not a failure — silent. A genuine
        read failure (permissions, corrupt JSON) reports once per
        session — the old code swallowed it silently (round-2 M6)."""
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                decoded = json.load(handle)
            return decoded if isinstance(decoded, dict) else None
        except FileNotFoundError:
            return None
        except Exception:
            self._report("read", "The Monitor's panel state could not be read — the defaults were restored.")
            return None

    def write(self, update: dict, merge: bool = True) -> bool:
        """The save: by default read-modify-write — the update
        MERGES into the file's current content so foreign keys
        survive (4.3.0's UI-state store consumes this file — a
        fixed-document save would erase its keys, round-2 A6).
        merge=False is the full-document REPLACE, reserved for the
        one-time schema migrations that deliberately drop a block.
        Written atomically (.tmp + os.replace); a failure reports
        once per session."""
        try:
            if merge:
                try:
                    with open(self._path, "r", encoding="utf-8") as handle:
                        current = json.load(handle)
                    if not isinstance(current, dict):
                        current = {}
                except FileNotFoundError:
                    current = {}
                document = dict(current)
                document.update(update)
            else:
                document = dict(update)
            with open(self._path + ".tmp", "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            os.replace(self._path + ".tmp", self._path)
            return True
        except Exception:
            self._report("write", "The Monitor's panel state could not be saved — selections may not survive a restart.")
            return False

    def reset_failures(self):
        """The per-session latch boundary (A6): a NEW session may
        report its own failure."""
        self._reported.clear()

    def _report(self, kind, text):
        if self._note is None or kind in self._reported:
            return
        self._reported.add(kind)
        self._note(kind, text)
