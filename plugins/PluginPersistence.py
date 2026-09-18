"""The 4.5.0 persistence facade (the panel's E2/E3/H5/M8 rulings):
the plugin's ONE owner for the settings document and the state
shards. Key-scoped operations only — components never see filenames
or JSON layouts, so the layout (one settings file; per-machine state
shards) stays an implementation detail that can change without
touching a caller (E2). One facade instance per file per process,
constructed at the composition root: the in-memory document makes
this mandatory — the never-evicted device cache guarantees several
live writers otherwise (H5). Pure: no Qt, no Resources — paths and
the save primitive are injected (the StateStore pattern); production
wires Cura's SaveFile for the fsync+flock commit (M8)."""
from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote_plus

from .PrinterConfig import PrinterConfig
from .StateStore import StateStore

# The pinned field-ownership table (E3): the union test in
# tests/test_plugin_persistence.py proves these two tuples cover the
# PrinterConfig dataclass's fields exactly once, so a field can never
# land in neither file or both. The console trio is the state side —
# written per poll, per machine; everything else is settings.
STATE_FIELDS = (
    "console_history",
    "console_transcript",
    "console_store_time",
)
SETTINGS_FIELDS = tuple(
    key for key in asdict(PrinterConfig()) if key not in STATE_FIELDS
)

# The pre-4.5.0 chrome file (L4): the migration reads it when the new
# global document is absent; the old file stays on disk.
OLD_STATE_FILE_NAME = "moonrakerprintfollower_sections.json"


class PluginPersistence:
    """The typed, key-scoped operations over the two stores."""

    def __init__(
        self,
        settings_path: str,
        state_global_path: str,
        state_machine_dir: str,
        save: Optional[Callable[[str, str], bool]] = None,
        lock: Optional[Callable[[], Any]] = None,
        note: Optional[Callable[[str, str], None]] = None,
    ):
        self._settings = StateStore(settings_path, save=save, lock=lock, note=note)
        self._state_global = StateStore(state_global_path, save=save, lock=lock, note=note)
        self._state_dir = state_machine_dir
        self._save = save
        self._lock = lock
        self._note = note
        self._shards: Dict[str, StateStore] = {}

    # -- The settings document ----------------------------------------

    @property
    def settings_path(self) -> str:
        return self._settings._path

    @property
    def state_dir(self) -> str:
        return self._state_dir

    @property
    def state_global_path(self) -> str:
        return self._state_global._path

    def set_machine_config(self, machine_id: str, config: PrinterConfig) -> bool:
        """The typed settings write: the record's settings fields,
        serialised for JSON (the enum as its persisted value)."""
        patch = {key: getattr(config, key) for key in SETTINGS_FIELDS}
        patch["feed_mode"] = config.feed_mode.value
        return self.set_machine(machine_id, patch)

    def settings_document(self) -> Dict[str, Any]:
        document = self._settings.read()
        return document if isinstance(document, dict) else {}

    def write_settings_document(self, document: Dict[str, Any]) -> bool:
        """The full-document write — the migration runner's writer."""
        return self._settings.write(document, merge=False)

    def get_machine(self, machine_id: str) -> Optional[Dict[str, Any]]:
        machines = self.settings_document().get("machines") or {}
        entry = machines.get(machine_id)
        return dict(entry) if isinstance(entry, dict) else None

    def set_machine(self, machine_id: str, patch: Dict[str, Any]) -> bool:
        """Key-scoped deep merge (E2): only this machine's record
        changes — the sibling records and the global section are read
        back and re-written untouched, never replaced."""
        document = self.settings_document()
        machines = document.get("machines")
        if not isinstance(machines, dict):
            machines = {}
        entry = machines.get(machine_id)
        merged = dict(entry) if isinstance(entry, dict) else {}
        merged.update(patch)
        machines[machine_id] = merged
        document["machines"] = machines
        return self._settings.write(document, merge=False)

    def set_global(self, patch: Dict[str, Any]) -> bool:
        document = self.settings_document()
        global_section = document.get("global")
        if not isinstance(global_section, dict):
            global_section = {}
        global_section.update(patch)
        document["global"] = global_section
        return self._settings.write(document, merge=False)

    def remove_machine(self, machine_id: str) -> bool:
        document = self.settings_document()
        machines = document.get("machines")
        if isinstance(machines, dict) and machine_id in machines:
            del machines[machine_id]
            document["machines"] = machines
            return self._settings.write(document, merge=False)
        return True

    def migration_record(self) -> Optional[Dict[str, Any]]:
        document = self.settings_document()
        global_section = document.get("global") or {}
        record = global_section.get("migration")
        return dict(record) if isinstance(record, dict) else None

    def set_migration_record(self, update: Dict[str, Any]) -> bool:
        """The runner's final-record write: merges into the record,
        never replacing it — a later run cannot erase a failure (C2)."""
        record = self.migration_record() or {}
        record.update(update)
        return self.set_global({"migration": record})

    # -- The state side -----------------------------------------------

    def _shard(self, machine_id: str) -> StateStore:
        store = self._shards.get(machine_id)
        if store is None:
            # The shard filename is the quoted id — Cura's own
            # convention for id-derived files (machine_instances/
            # <quote_plus(id)>.global.cfg, the domain panel's L2):
            # ids are name-derived and may carry spaces.
            store = StateStore(
                os.path.join(self._state_dir, f"{quote_plus(machine_id)}.json"),
                save=self._save, lock=self._lock, note=self._note,
            )
            self._shards[machine_id] = store
        return store

    def state_global_document(self) -> Dict[str, Any]:
        document = self._state_global.read()
        return document if isinstance(document, dict) else {}

    def write_state_global_document(self, document: Dict[str, Any]) -> bool:
        return self._state_global.write(document, merge=False)

    def merge_state_global(self, update: Dict[str, Any], delete: tuple = ()) -> bool:
        """The chrome's top-level merge (the StateStore semantics on the
        global document): foreign keys survive, `delete` drops the named
        keys deliberately (the chart block's removal precedent)."""
        document = self.state_global_document()
        document.update(update)
        for key in delete:
            document.pop(key, None)
        return self._state_global.write(document, merge=False)

    def reset_failures(self) -> None:
        """The per-session latch boundary, forwarded to every store."""
        self._settings.reset_failures()
        self._state_global.reset_failures()
        for store in self._shards.values():
            store.reset_failures()

    def get_machine_state(self, machine_id: str) -> Optional[Dict[str, Any]]:
        document = self._shard(machine_id).read()
        return document if isinstance(document, dict) else None

    def set_machine_state(self, machine_id: str, patch: Dict[str, Any]) -> bool:
        """The per-machine shard's top-level merge — the shard owns
        only its machine's keys, so StateStore's native merge is the
        key-scoped write here."""
        return self._shard(machine_id).write(patch, merge=True)

    def write_machine_state_document(self, machine_id: str, document: Dict[str, Any]) -> bool:
        return self._shard(machine_id).write(document, merge=False)
