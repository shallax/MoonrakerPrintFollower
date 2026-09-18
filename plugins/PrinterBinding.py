"""Active printer/configuration ownership, independent of following and UI features.

4.5.0: the settings live in the persistence facade's document; the
legacy preference chain (the flat keys, the Moonraker Connection
import) still runs first; the one-shot migration into the facade's
files runs from Cura's initializationFinished — never construction
(Cura re-reads the preference file after plugins load and resurrects
a construction-time clean, the panel's B1) — with the machine-switch
path as the deferred-until-identity case. The console's
preference-flush debounce is gone: the transcript writes its own
per-machine shard (ConsoleController), so the binding no longer
flushes cura.cfg for it. The removal hook wipes a removed machine's
credentials via the facade — the explicit removed id, never the
active identity (E1's wrong-target trap: removeMachine activates a
replacement first)."""
from __future__ import annotations

import time
from dataclasses import replace

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from UM.Logger import Logger

from .CuraAdapter import active_machine_identity
from .PersistenceMigration import read_source, run_migration
from .PrinterConfig import PrinterConfig, PrinterConfigStore, normalise_url

# The host-identifying fields the removal wipe clears (the author's
# ruling): the harmless rest survives for a same-named re-add.
_REMOVAL_WIPE_FIELDS = ("url", "api_key", "camera_url", "frontend_url", "upload_path")
# The one-shot re-runs while the failure is retryable: a failed
# backup, a failed write or a failed verify all leave cura.cfg intact
# and the next launch replays. A terminal failure (or success) is the
# persisted record's word.
_RETRYABLE_REASONS = {"backup-failed", "write-failed", "verify-failed"}


class PrinterBinding(QObject):
    changed = pyqtSignal()

    def __init__(self, application, client, persistence, cura_cfg_path, old_state_path, parent=None):
        super().__init__(parent)
        self._application = application
        self._client = client
        self._persistence = persistence
        self._cura_cfg_path = cura_cfg_path
        self._old_state_path = old_state_path
        # The legacy chain's owner until the 5.0.0 teardown: the flat
        # keys, the rename marker and the Moonraker Connection import
        # still live in preferences.
        self._store = PrinterConfigStore(application.getPreferences(), lambda: active_machine_identity(application))
        self._machine_id, self._machine_name = self._store.identity()
        self._closed = False
        signal = getattr(application, "globalContainerStackChanged", None)
        self._machine_signal = signal
        if signal is not None:
            signal.connect(self._machine_changed)
        self._registry = application.getContainerRegistry() if hasattr(application, "getContainerRegistry") else None
        if self._registry is not None and hasattr(self._registry, "containerRemoved"):
            self._registry.containerRemoved.connect(self._container_removed)

    @property
    def config(self):
        # LIVE identity, never the construction-time cache: the binding
        # constructs before Cura's active machine exists (identity
        # "unknown"), and every cached read saw the unknown machine's
        # EMPTY record — the console's restored transcript never loaded
        # (the "console starts completely empty" report).
        machine_id, _ = self.identity
        if self._persistence is not None:
            entry = self._persistence.get_machine(machine_id)
            if entry is not None:
                return PrinterConfig.from_dict(entry)
        # Pre-migration fallback: the preference blob is still the
        # source until the one-shot lands.
        return self._store.get(machine_id)

    @property
    def identity(self):
        return self._machine_id, self._machine_name

    @property
    def configured(self):
        return self._machine_id != "unknown" and self.usable(normalise_url(self.config.url))

    @staticmethod
    def usable(url):
        parsed = QUrl(url)
        return parsed.isValid() and parsed.scheme() in {"http", "https"} and bool(parsed.host())

    def _carry_bed_mesh_preferences(self, preferences) -> None:
        """The bed-mesh keys' move (the no-trace ruling): the values
        carry into the settings document's global section once, then
        the clean resets the preferences. Idempotent — the keys'
        presence in the global section is the guard, so a later run
        can never overwrite the user's live values with defaults."""
        global_section = self._persistence.settings_document().get("global") or {}
        if "bedMeshVisible" in global_section and "bedMeshExaggeration" in global_section:
            return
        patch = {}
        if "bedMeshVisible" not in global_section:
            patch["bedMeshVisible"] = bool(preferences.getValue("moonrakerprintfollower/bed_mesh_visible"))
        if "bedMeshExaggeration" not in global_section:
            try:
                patch["bedMeshExaggeration"] = float(preferences.getValue("moonrakerprintfollower/bed_mesh_exaggeration"))
            except (TypeError, ValueError):
                patch["bedMeshExaggeration"] = 20.0
        if patch:
            self._persistence.set_global(patch)

    def _migrate(self):
        record = self._persistence.migration_record() if self._persistence is not None else None
        if record is None and not self._persistence.settings_document():
            # The pre-migration window only: the legacy chain runs
            # while the blob is still the source and the v2 document
            # does not exist yet. Once the document exists the chain
            # must NOT run — its mirror keys fabricate a migration
            # record on installs that never had legacy data (the
            # first-install report), and the one-shot's own checks
            # cover whatever the chain used to.
            for migrate in (self._store.migrate_legacy_to_current_machine, self._store.migrate_moonraker_connection):
                try:
                    migrate()
                except Exception as error:
                    Logger.log("w", "Moonraker settings migration failed: %s", error)
        self.run_persistence_migration()

    def run_persistence_migration(self):
        """The v2 activation and the one-shot (B1): called from
        Cura's initializationFinished (wired by the runtime) and from
        the machine-switch path. A clean install activates the v2
        document directly — no migration record, because nothing was
        migrated (the author's ruling). The one-shot runs only while
        the legacy blob still holds records; an absent or empty blob
        is nothing to do, and the existing document is never replaced
        on that path."""
        preferences = self._application.getPreferences()
        document = self._persistence.settings_document()
        if not document:
            # The clean-install activation: the v2 skeleton, no record.
            self._persistence.write_settings_document({
                "configVersion": 2,
                "global": {},
                "machines": {},
            })
            return
        record = self._persistence.migration_record()
        if record is not None:
            status = record.get("status")
            if status == "ok":
                # The post-conditions are idempotent: an install that
                # migrated on an earlier snapshot still sheds the old
                # file and the cura.cfg flags (the legacy chain is
                # record-guarded, so nothing resurrects the blob).
                from .PersistenceMigration import _clean_preferences, _remove_old_state_file
                self._carry_bed_mesh_preferences(preferences)
                _remove_old_state_file(self._old_state_path)
                _clean_preferences(preferences.setValue)
                return
            if status == "failed" and not (
                record.get("backupWritten") is False
                or record.get("reason") in _RETRYABLE_REASONS
            ):
                return
        blob = preferences.getValue(PrinterConfigStore.PREF_KEY)
        source_state, _ = read_source(blob)
        if source_state in ("absent", "empty"):
            # Nothing to migrate: the existing v2 document is the
            # source of truth. The record's absence must never re-arm
            # a rewrite (the first-install lost-config report).
            return
        if not self._store._truthy(preferences.getValue(PrinterConfigStore.MIGRATED_KEY)):
            # The legacy chain has not finished pushing the records;
            # the one-shot must wait (they would re-write the blob
            # after the clean).
            return
        self._carry_bed_mesh_preferences(preferences)
        outcome = run_migration(
            blob,
            self._cura_cfg_path,
            settings_path=self._persistence.settings_path,
            state_dir=self._persistence.state_dir,
            old_state_path=self._old_state_path,
            settings_write=self._persistence.write_settings_document,
            settings_record_write=self._persistence.set_migration_record,
            state_global_write=self._persistence.write_state_global_document,
            state_machine_write=self._persistence.write_machine_state_document,
            set_pref=preferences.setValue,
            timestamp=time.strftime("%Y-%m-%d-%H-%M-%S"),
        )
        if outcome.status == "failed":
            Logger.log("w", "Moonraker persistence migration failed: %s", outcome.reason)

    def start(self):
        self._migrate()
        self._apply()

    def apply(self, config):
        if self._closed:
            return
        previous = self.config
        self._client.set_trace_http(config.trace_http)
        endpoint_changed = (normalise_url(previous.url), previous.api_key) != (normalise_url(config.url), config.api_key)
        camera_changed = previous.camera_selected != config.camera_selected
        camera_only = camera_changed and replace(previous, camera_selected=config.camera_selected) == config

        if endpoint_changed:
            # Tear the poller down before persistence/rebind without
            # emitting: the client's configure below emits exactly one
            # invalidation wave on the old identity.
            self._client.stop(reset_session=False)

        # The facade's typed settings write (the file is the source of
        # truth now — SaveFile's fsync makes the save durable, so the
        # synchronous preference flush retires with the transcript).
        machine_id, _ = self.identity
        self._persistence.set_machine_config(machine_id, config)

        # Camera selection is UI state, not connection state: persisted
        # above, but never a reconfigure/restart of the client.
        if camera_only:
            self.changed.emit()
            return
        self._apply()

    def _machine_changed(self, *_args):
        machine_id, name = self._store.identity()
        if machine_id == self._machine_id:
            self._machine_name = name
            self.changed.emit()
            return
        # A machine switch must invalidate the session state even when both
        # Cura profiles use the same endpoint: subscribers tear down and
        # the generation bump is what stale-callback guards rely on.
        self._client.stop()
        self._machine_id, self._machine_name = machine_id, name
        self._migrate()
        self._apply()

    def _container_removed(self, container, *args):
        """Cura removed a container: the machine-stack removal is the
        last emission of removeMachine, and the rename path emits here
        too — the filter is the metadata type plus a registry check
        (E8/B2). Cura itself can remove machines without the user (the
        quality-changes name collision), so the wipe records why."""
        try:
            metadata = container.getMetaData() if hasattr(container, "getMetaData") else {}
            if str(metadata.get("type") or "") != "machine":
                return
            machine_id = str(container.getId() or "")
        except Exception:
            return
        if self._persistence is None or self._persistence.get_machine(machine_id) is None:
            return
        if self._registry is not None and self._registry.findContainerStacksMetadata(id=machine_id):
            return  # still known: the rename emission, not a removal
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._wipe_removed_machine(machine_id))

    def _wipe_removed_machine(self, machine_id):
        # The explicit REMOVED id, never the active identity (E1):
        # removeMachine activates a replacement first, so any
        # active-identity default blanks the wrong printer.
        if machine_id == self._machine_id:
            self._client.stop()
        patch = {field: ("http://" if field == "url" else "") for field in _REMOVAL_WIPE_FIELDS}
        patch["removed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._persistence.set_machine(machine_id, patch)
        Logger.log("i", "Moonraker machine %s removed — its credentials were wiped.", machine_id)

    def _apply(self):
        config = self.config
        url = normalise_url(config.url)
        # The product default (websocket) lives in PrinterConfig and is
        # passed here explicitly — never a client-side code default.
        self._client.configure(
            url, config.api_key, config.poll_interval_ms,
            feed_mode=config.feed_mode.value,
            aux_interval_ms=config.aux_interval_ms,
            console_interval_ms=config.console_interval_ms,
        )
        if self.configured:
            self._client.start()
        else:
            self._client.stop()
        self.changed.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._machine_signal is not None:
            try:
                self._machine_signal.disconnect(self._machine_changed)
            except Exception:
                pass
        self._client.stop()
