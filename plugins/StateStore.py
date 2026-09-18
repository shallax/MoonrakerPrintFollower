"""The plugin JSON's file-semantics owner (4.2.0, F11/A6; the 4.5.0
M8/E9 extensions): the raw read, the read-modify-write that preserves
foreign keys, the atomic replace and the rate-limited failure
reporting. Pure: no Qt, no Resources; the caller passes the path so
the migration tests run without Cura. The model keeps the VALUE
coercion (its tests pin the fallback document); the store owns what
happens to the file.

4.5.0: the documents are pretty-printed (indent + sorted keys, the
author's ruling) and fsynced; the atomic-write primitive is injected
(production passes Cura's SaveFile for its fsync+flock commit — M8)
and an optional lock guards the whole read-modify-write cycle
(E9/H2)."""
from __future__ import annotations

import contextlib
import json
import os
from typing import Any, Callable, Optional


def _no_lock():
    return contextlib.nullcontext()


class StateStore:
    """One owner for a plugin JSON document's file semantics."""

    def __init__(
        self,
        path: str,
        note: Optional[Callable[[str, str], None]] = None,
        save: Optional[Callable[[str, str], bool]] = None,
        lock: Optional[Callable[[], Any]] = None,
    ):
        self._path = path
        # The failure sink: note(failure_class, text). Classes are
        # "read"/"write"; each reports once per session (the latch
        # resets on reset_failures — the model hooks it to the
        # session boundary, round-2 A6/M6).
        self._note = note
        self._reported = set()
        # The injected atomic-write primitive (M8): production passes
        # Cura's SaveFile; the default is the pure tmp+replace below.
        self._save = save
        # The lock around the read-modify-write (E9/H2): production
        # guards the shared documents; per-machine shards have a
        # single writer by construction and need none.
        self._lock = lock or _no_lock

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

    def write(self, update: dict, merge: bool = True, delete: tuple = ()) -> bool:
        """The save: by default read-modify-write — the update
        MERGES into the file's current content so foreign keys
        survive (4.3.0's UI-state store consumes this file — a
        fixed-document save would erase its keys, round-2 A6).
        `delete` names the keys the merge deliberately drops (the
        chart migration removes ONLY its own legacy block — never a
        full-document rewrite, which was the sibling rule's single
        exception and is now gone outright). merge=False is the
        full-document REPLACE. Pretty-printed (indent + sorted keys)
        and written atomically with an fsync before the swap (C6); a
        failure reports once per session."""
        try:
            with self._lock():
                if merge:
                    document = self._merged(update, delete)
                else:
                    document = dict(update)
                text = json.dumps(document, indent=2, sort_keys=True, allow_nan=False)
                if self._save is not None:
                    return self._save(self._path, text)
                # O_NOFOLLOW: a pre-existing symlink at the .tmp path must
                # not be written through (truncating whatever it points
                # at, as this user). The flag is POSIX-only — Windows has
                # no such risk at .tmp and its os lacks the constant, so
                # the open must degrade there (the author's Windows run:
                # every state write failed and the what's-new marker never
                # persisted); 0o600: the file now carries two features'
                # state.
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                # Same guard as the production SaveFile wrapper: a
                # deleted parent folder must not strand the writes.
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                fd = os.open(self._path + ".tmp", flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    # allow_nan=False: NaN/Infinity round-trip through
                    # Python's own loader but are non-standard JSON for
                    # any other reader — a file with two owners must stay
                    # standard.
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(self._path + ".tmp", self._path)
                return True
        except Exception:
            self._report("write", "The Monitor's panel state could not be saved — selections may not survive a restart.")
            return False

    def _merged(self, update: dict, delete: tuple) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                current = json.load(handle)
            if not isinstance(current, dict):
                current = {}
        except Exception:
            # Any undecodable-but-present file (a BOM, a partial sync, a
            # hand-edit) must not wedge the save forever — the old
            # replace-write self-healed by overwriting; the merge falls
            # back to an empty document and heals on this write (the
            # adversarial round's M1). NOTE: this self-heal is a
            # cross-consumer key-loss event — the next merge write emits
            # only the writing consumer's keys.
            current = {}
        document = dict(current)
        document.update(update)
        for key in delete:
            document.pop(key, None)
        return document

    def reset_failures(self):
        """The per-session latch boundary (A6): a NEW session may
        report its own failure."""
        self._reported.clear()

    def _report(self, kind, text):
        if self._note is None or kind in self._reported:
            return
        self._reported.add(kind)
        self._note(kind, text)
