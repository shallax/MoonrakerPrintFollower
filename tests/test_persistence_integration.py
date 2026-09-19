"""The persistence re-points' integration contracts (4.5.0): the
console's shard writes and settle timer, the shard-first load, the
unknown-identity skip, the migration trigger, and the toast's two
flavours. Qt-guarded — the container runs them for real."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    from qt_runtime_support import QT_AVAILABLE, Preferences, runtime
    if QT_AVAILABLE:
        _started = runtime()
        _started.__enter__()
        try:
            from plugins.ConsoleController import ConsoleController
            from plugins.PluginPersistence import PluginPersistence
            from plugins.PrinterBinding import PrinterBinding
            from plugins.PrinterConfig import PrinterConfig, PrinterConfigStore
        finally:
            _started.__exit__(None, None, None)
except ImportError:
    QT_AVAILABLE = False


def _pretty_save(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


if QT_AVAILABLE:
    class _Data(QObject):
        connectionStateChanged = pyqtSignal(str)
        invalidated = pyqtSignal()

    class _Commands(QObject):
        emergencyStopped = pyqtSignal()
else:
    _Data = _Commands = None


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ConsoleShardTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = self.dir.name
        self.persistence = PluginPersistence(
            os.path.join(base, "settings.json"),
            os.path.join(base, "state", "global.json"),
            os.path.join(base, "state", "machines"),
            save=_pretty_save,
        )
        self.data = _Data()
        self.commands = _Commands()
        self.applied = []

        def apply_config(config):
            self.applied.append(config)
        self.console = ConsoleController(
            self.data, self.commands,
            config=lambda: PrinterConfig(), apply_config=apply_config,
            identity=lambda: ("A", "A"), persistence=self.persistence,
        )

    def _shard(self):
        return self.persistence.get_machine_state("A") or {}

    def test_persist_writes_the_per_machine_shard(self):
        self.console.append_responses([
            {"text": "ok", "error": False, "success": True, "time": 5.0},
        ])
        shard = self._shard()
        self.assertEqual(shard["consoleTranscript"][0]["text"], "ok")
        self.assertEqual(shard["consoleStoreTime"], 5.0)

    def test_mark_saved_settles_the_persisted_window(self):
        # A typed command flips blue until the 2 s settle fires
        # mark_saved (the verdict colouring's UX ruling).
        entry = {"kind": "command", "text": "G28", "error": False, "success": False,
                 "restored": False, "saved": False}
        self.console._append_entries([entry])
        self.console._persist()
        self.assertFalse(entry["saved"])
        self.console.mark_saved()
        self.assertTrue(entry["saved"])

    def test_clear_writes_an_empty_shard(self):
        self.console.append_responses([{"text": "x", "error": False, "success": True, "time": 1.0}])
        self.console.clear()
        shard = self._shard()
        self.assertEqual(shard["consoleTranscript"], [])
        self.assertNotIn("consoleHistory", shard)

    def test_the_shard_wins_over_the_config_fallback(self):
        # Pre-migration fallback: the config record's transcript only
        # loads when the shard is absent; a present shard (even an
        # empty one — a genuine Clear) is authoritative.
        self.persistence.set_machine_state("A", {
            "consoleTranscript": [{"kind": "command", "text": "from-shard",
                                   "error": False, "success": False}],
            "consoleStoreTime": 9.0,
        })
        fallback = PrinterConfig(console_transcript=[
            {"kind": "command", "text": "from-config", "error": False, "success": False}])
        self.console._config = lambda: fallback
        self.console.reload_if_empty()
        texts = [entry["text"] for entry in self.console._transcript]
        self.assertIn("from-shard", texts)
        self.assertNotIn("from-config", texts)

    def test_persist_skips_while_the_identity_is_unknown(self):
        console = ConsoleController(
            self.data, self.commands,
            config=lambda: PrinterConfig(), apply_config=lambda config: None,
            identity=lambda: ("unknown", "Unknown Cura printer"),
            persistence=self.persistence,
        )
        console.append_responses([{"text": "x", "error": False, "success": True, "time": 1.0}])
        self.assertEqual(self.persistence.get_machine_state("unknown"), None)
        self.assertFalse(os.path.exists(
            os.path.join(self.persistence.state_dir, "unknown.json")))

    def test_a_failed_shard_write_notes_the_console(self):
        persistence = PluginPersistence(
            os.path.join(self.dir.name, "s2.json"),
            os.path.join(self.dir.name, "g2.json"),
            os.path.join(self.dir.name, "m2"),
            save=lambda path, text: False,
        )
        console = ConsoleController(
            self.data, self.commands,
            config=lambda: PrinterConfig(), apply_config=lambda config: None,
            identity=lambda: ("A", "A"), persistence=persistence,
        )
        console.append_responses([{"text": "x", "error": False, "success": True, "time": 1.0}])
        notes = [entry["text"] for entry in console._transcript if entry["kind"] == "note"]
        self.assertTrue(any("could not be saved" in note for note in notes))


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MigrationTriggerTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = self.dir.name
        self.settings_path = os.path.join(base, "settings.json")
        self.persistence = PluginPersistence(
            self.settings_path,
            os.path.join(base, "state", "global.json"),
            os.path.join(base, "state", "machines"),
            save=_pretty_save,
        )
        self.cura_cfg = os.path.join(base, "cura.cfg")
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write("[general]\nversion = 1\n[moonrakerprintfollower]\nprinter_configs_v1 = %s\n"
                         % json.dumps({"A": {"url": "http://a:7125"}}))
        prefs = Preferences({})
        self.prefs = prefs
        self.app = self.qt.Application(prefs)
        self.app.stack = self.qt.Machine("A")

        class Client:
            def stop(self, reset_session=False): pass
            def set_trace_http(self, value): pass
            def configure(self, *args, **kwargs): pass
            def start(self): pass
        self.client = Client()
        self.binding = PrinterBinding(self.app, self.client, self.persistence,
                                      cura_cfg_path=self.cura_cfg, old_state_path=None)

    def test_a_clean_install_boots_twice_without_any_migration_artefacts(self):
        # The reviewer's first-install invariant, ASSEMBLED: the legacy
        # chain, the activation, a real save and the second boot — the
        # interaction that fabricated the phantom migration.
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, "{}")
        # Boot 1 through the REAL entry point (_migrate): the legacy
        # chain and the Moonraker Connection check both run, then the
        # migration — the interaction the bug lived in.
        self.binding._migrate()
        document = self.persistence.settings_document()
        self.assertEqual(document["configVersion"], 2)
        self.assertEqual(document.get("machines"), {})
        self.assertNotIn("migration", document.get("global", {}))
        self.assertIsNone(self.persistence.migration_record())
        self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY), "{}")
        self.assertFalse(self.binding._store._truthy(
            self.prefs.getValue(PrinterConfigStore.MIGRATED_KEY)))
        self.assertFalse(self.binding._store._truthy(
            self.prefs.getValue(PrinterConfigStore.MOONRAKER_CONNECTION_MIGRATED_KEY)))
        self.assertEqual([n for n in os.listdir(self.dir.name)
                          if n.startswith("cura.cfg.")], [])
        # The production save path (the settings action's verb, via
        # the binding's apply).
        config = PrinterConfig()
        config.url = "http://a:7125"
        config.api_key = "k"
        self.assertTrue(self.binding.apply(config))
        with open(self.settings_path, encoding="utf-8") as handle:
            before = json.load(handle)

        # Boot 2: a fresh binding over the same state (the process
        # boundary in-process — the MODE=firstinstall harness is the
        # two-process proof) — the document must be EQUIVALENT and
        # the machinery completely silent.
        second = PrinterBinding(self.app, self.client, self.persistence,
                                cura_cfg_path=self.cura_cfg, old_state_path=None)
        second._migrate()
        with open(self.settings_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), before)
        self.assertIsNone(self.persistence.migration_record())
        self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY), "{}")

    def test_the_one_shot_migrates_after_the_legacy_chain(self):
        key = PrinterConfigStore.PREF_KEY
        self.prefs.addPreference(key, "{}")
        self.prefs.setValue(key, json.dumps({"A": {"url": "http://a:7125"}}))
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        self.binding.run_persistence_migration()
        document = self.persistence.settings_document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(self.prefs.getValue(key), "{}")
        # The backup landed beside cura.cfg with the ruled name form.
        record = self.persistence.migration_record()
        self.assertTrue(record["backupWritten"])
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, record["backupName"])))

    def test_a_failed_bed_mesh_carry_holds_the_migration_back_and_retries(self):
        # The 4.5.0 transactional fix: a REQUIRED carry that fails must
        # hold the migration back — the clean that would destroy the
        # legacy bed-mesh values never runs, and the next boot retries
        # from the intact source.
        key = PrinterConfigStore.PREF_KEY
        self.prefs.addPreference(key, "{}")
        self.prefs.setValue(key, json.dumps({"A": {"url": "http://a:7125"}}))
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        self.prefs.setValue("moonrakerprintfollower/bed_mesh_visible", False)
        self.prefs.setValue("moonrakerprintfollower/bed_mesh_exaggeration", 7.5)

        original_set_global = self.persistence.set_global
        self.persistence.set_global = lambda patch: False
        self.binding.run_persistence_migration()
        # The hold-back surfaces the session-scoped write-failed
        # verdict, with nothing persisted and nothing cleaned.
        record = self.persistence.migration_record()
        self.assertEqual((record["status"], record["reason"]), ("failed", "write-failed"))
        self.assertFalse(record["backupWritten"])
        self.assertNotIn("migration", self.persistence.settings_document().get("global", {}))
        self.assertEqual(self.prefs.getValue(key), json.dumps({"A": {"url": "http://a:7125"}}))
        self.assertEqual(self.prefs.getValue("moonrakerprintfollower/bed_mesh_visible"), False)
        self.assertEqual(self.prefs.getValue("moonrakerprintfollower/bed_mesh_exaggeration"), 7.5)

        # The next boot with a working store: the carry lands, the
        # migration runs, and ONLY THEN the legacy keys reset.
        self.persistence.set_global = original_set_global
        self.binding.run_persistence_migration()
        document = self.persistence.settings_document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(document["global"]["bedMeshVisible"], False)
        self.assertEqual(document["global"]["bedMeshExaggeration"], 7.5)
        self.assertEqual(self.prefs.getValue(key), "{}")
        self.assertIs(self.prefs.getValue("moonrakerprintfollower/bed_mesh_visible"), True)
        self.assertEqual(self.prefs.getValue("moonrakerprintfollower/bed_mesh_exaggeration"), 20.0)

    def test_the_post_migration_tidy_holds_the_clean_on_a_failed_carry(self):
        # The ok-record tidy path: a failed carry must leave the old
        # values available for another retry, not destroy them in the
        # clean that follows.
        self.persistence.write_settings_document({
            "configVersion": 2,
            "global": {"migration": {
                "status": "ok", "reason": "migrated", "backupWritten": True,
                "backupName": "cura.cfg.2026-09-18-14-30-12", "records": 1,
                "toastShown": False, "bannerDismissed": False,
            }},
            "machines": {},
        })
        self.prefs.setValue("moonrakerprintfollower/bed_mesh_visible", False)
        self.prefs.setValue("moonrakerprintfollower/bed_mesh_exaggeration", 7.5)

        original_set_global = self.persistence.set_global
        self.persistence.set_global = lambda patch: False
        self.binding.run_persistence_migration()
        self.assertEqual(self.prefs.getValue("moonrakerprintfollower/bed_mesh_visible"), False)
        self.assertEqual(self.prefs.getValue("moonrakerprintfollower/bed_mesh_exaggeration"), 7.5)

        self.persistence.set_global = original_set_global
        self.binding.run_persistence_migration()
        document = self.persistence.settings_document()
        self.assertEqual(document["global"]["bedMeshVisible"], False)
        self.assertEqual(document["global"]["bedMeshExaggeration"], 7.5)
        self.assertIs(self.prefs.getValue("moonrakerprintfollower/bed_mesh_visible"), True)
        self.assertEqual(self.prefs.getValue("moonrakerprintfollower/bed_mesh_exaggeration"), 20.0)

    def test_a_direct_flat_upgrade_migrates_on_the_first_boot(self):
        # The one-boot direct upgrade: cura.cfg carries the flat-era
        # values but printer_configs_v1 has never been flushed to disk.
        # The legacy chain synthesises the blob in memory THIS boot,
        # and the backup must accept the actual pre-migration source —
        # the original flat settings — not the transformed key.
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write(
                "[general]\nversion = 1\n[moonrakerprintfollower]\n"
                "enabled = False\nurl = http://old:7125\napi_key = oldkey\n"
            )
        self.prefs.setValue(PrinterConfigStore.LEGACY_MAP["enabled"], False)
        self.prefs.setValue(PrinterConfigStore.LEGACY_MAP["url"], "http://old:7125")
        self.prefs.setValue(PrinterConfigStore.LEGACY_MAP["api_key"], "oldkey")
        self.binding._migrate()

        document = self.persistence.settings_document()
        self.assertEqual(document["machines"]["A"]["url"], "http://old:7125")
        self.assertEqual(document["machines"]["A"]["api_key"], "oldkey")
        record = self.persistence.migration_record()
        self.assertEqual(record["status"], "ok")
        self.assertTrue(record["backupWritten"])
        with open(os.path.join(self.dir.name, record["backupName"]), "rb") as handle:
            raw = handle.read()
        self.assertIn(b"moonrakerprintfollower", raw)
        self.assertIn(b"http://old:7125", raw)  # the original source, not the blob
        self.assertNotIn(b"printer_configs_v1", raw)
        # The legacy source is cleaned only after success.
        self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY), "{}")
        self.assertFalse(self.binding._store._truthy(
            self.prefs.getValue(PrinterConfigStore.MIGRATED_KEY)))

    def test_a_direct_moonraker_connection_upgrade_migrates_on_the_first_boot(self):
        # A pure Connection upgrade: no flat follower values at all —
        # the import's own marker releases the one-shot gate, and the
        # backup accepts the real moonraker/instances source.
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write(
                "[general]\nversion = 1\n[moonraker]\n"
                'instances = {"Old": {"url": "http://mc:7125", "api_key": "mckey"}}\n'
            )
        self.prefs.setValue(PrinterConfigStore.MOONRAKER_CONNECTION_PREF_KEY,
                            json.dumps({"Old": {"url": "http://mc:7125", "api_key": "mckey"}}))
        self.binding._migrate()

        document = self.persistence.settings_document()
        self.assertEqual(document["machines"]["Old"]["url"], "http://mc:7125")
        self.assertEqual(document["machines"]["Old"]["api_key"], "mckey")
        record = self.persistence.migration_record()
        self.assertEqual(record["status"], "ok")
        self.assertTrue(record["backupWritten"])
        with open(os.path.join(self.dir.name, record["backupName"]), "rb") as handle:
            raw = handle.read()
        self.assertIn(b"moonraker", raw)
        self.assertIn(b"mckey", raw)
        self.assertNotIn(b"printer_configs_v1", raw)
        self.assertFalse(self.binding._store._truthy(
            self.prefs.getValue(PrinterConfigStore.MOONRAKER_CONNECTION_MIGRATED_KEY)))

    def test_a_replayed_migration_keeps_the_live_v2_state(self):
        # The hardening pass reproducer, at the production seam: a
        # retry after a partially completed migration must not roll
        # back the live v2 state documents — existing values win, the
        # migration fills only the gaps.
        key = PrinterConfigStore.PREF_KEY
        self.prefs.addPreference(key, "{}")
        self.prefs.setValue(key, json.dumps({"A": {
            "url": "http://a:7125",
            "console_transcript": [{"kind": "command", "text": "legacy", "error": False, "success": False}],
            "console_store_time": "legacy-stamp",
        }}))
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        # The legacy chrome the first attempt had moved, and the live
        # v2 state the session wrote after that attempt.
        old_state_path = os.path.join(self.dir.name, "old_sections.json")
        _pretty_save(old_state_path, json.dumps({"someLegacyKey": "legacy"}))
        self.persistence.write_state_global_document({
            "configVersion": 2,
            "whatsNewSeen": "current",
            "currentOnlyKey": "keep",
            "someLegacyKey": "newer-value",
        })
        self.persistence.write_machine_state_document("A", {
            "consoleTranscript": [{"kind": "command", "text": "newer", "error": False, "success": False}],
            "consoleStoreTime": "newer-stamp",
            "liveOnlyKey": "keep",
        })
        binding = PrinterBinding(self.app, self.client, self.persistence,
                                 cura_cfg_path=self.cura_cfg, old_state_path=old_state_path)
        binding.run_persistence_migration()

        global_doc = self.persistence.state_global_document()
        self.assertEqual(global_doc["whatsNewSeen"], "current")
        self.assertEqual(global_doc["currentOnlyKey"], "keep")
        self.assertEqual(global_doc["someLegacyKey"], "newer-value")
        shard = self.persistence.get_machine_state("A")
        self.assertEqual(shard["consoleStoreTime"], "newer-stamp")
        self.assertEqual(shard["consoleTranscript"][0]["text"], "newer")
        self.assertEqual(shard["liveOnlyKey"], "keep")
        self.assertNotIn("consoleHistory", shard)

    def test_a_corrupt_recovery_retry_keeps_the_live_v2_global_state(self):
        # The hardening pass's last hole, at the production seam: the
        # corrupt-source recovery writes its global state through the
        # SAME merge semantics as the records path — a retry after a
        # failed recovery write must fill gaps, never roll back the
        # live v2 state the session wrote in between.
        key = PrinterConfigStore.PREF_KEY
        self.prefs.addPreference(key, "{}")
        self.prefs.setValue(key, "not json {{{")
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        old_state_path = os.path.join(self.dir.name, "old_sections.json")
        _pretty_save(old_state_path, json.dumps({"legacyOnly": "legacy", "conflict": "old"}))
        binding = PrinterBinding(self.app, self.client, self.persistence,
                                 cura_cfg_path=self.cura_cfg, old_state_path=old_state_path)

        # Attempt 1: the global-state recovery lands, the settings
        # recovery write fails — the corrupt source stays intact for
        # the retry, and state.json exists.
        original_settings_write = self.persistence.write_settings_document
        self.persistence.write_settings_document = lambda document: False
        try:
            binding.run_persistence_migration()
        finally:
            self.persistence.write_settings_document = original_settings_write
        self.assertEqual(self.prefs.getValue(key), "not json {{{")  # no clean
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, "state", "global.json")))
        # The failure is surfaced session-scoped (nothing persisted):
        # the retry stays required.
        record = self.persistence.migration_record()
        self.assertEqual((record["status"], record["reason"]), ("failed", "write-failed"))
        self.assertNotIn("migration", self.persistence.settings_document().get("global", {}))

        # Between attempts the live 4.5 session writes its own state
        # through the production chrome API.
        self.assertTrue(self.persistence.merge_state_global({
            "whatsNewSeen": "current",
            "currentOnly": "keep",
            "conflict": "newer",
        }))

        # Attempt 2: the recovery settings write succeeds and the
        # retry runs from the same corrupt source.
        binding.run_persistence_migration()
        global_doc = self.persistence.state_global_document()
        self.assertEqual(global_doc["whatsNewSeen"], "current")
        self.assertEqual(global_doc["currentOnly"], "keep")
        self.assertEqual(global_doc["conflict"], "newer")
        self.assertEqual(global_doc["legacyOnly"], "legacy")  # the gap filled
        self.assertEqual(global_doc["configVersion"], 2)
        # The terminal corrupt-blob record landed, the backup remains,
        # and ONLY NOW the corrupt preferences were cleaned.
        record = self.persistence.migration_record()
        self.assertEqual((record["status"], record["reason"]), ("failed", "corrupt-blob"))
        self.assertTrue(record["backupWritten"])
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, record["backupName"])))
        self.assertEqual(self.prefs.getValue(key), "{}")

    def test_a_deleted_folder_reconfigure_survives_the_next_boot(self):
        # The Windows lost-config sequence: the folder was deleted by
        # hand (no settings, no record). Boot A activates the v2
        # document directly — a clean install has nothing to migrate,
        # so no record is fabricated. Boot B sees the document and an
        # empty blob: nothing to do, no writes at all.
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, "{}")
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        self.binding.run_persistence_migration()  # boot A: the activation
        config = PrinterConfig()
        config.url = "http://a:7125"
        config.api_key = "k"
        self.persistence.set_machine_config("A", config)

        base = self.dir.name
        second = PluginPersistence(
            os.path.join(base, "settings.json"),
            os.path.join(base, "state", "global.json"),
            os.path.join(base, "state", "machines"),
            save=_pretty_save,
        )
        binding2 = PrinterBinding(self.app, self.client, second,
                                  cura_cfg_path=self.cura_cfg, old_state_path=None)
        binding2.run_persistence_migration()  # boot B: nothing to do
        document = second.settings_document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(document["machines"]["A"]["api_key"], "k")

    def test_a_failed_one_shot_reaches_the_notice_without_being_persisted(self):
        # The failure must not be persisted (the next boot retries from
        # the intact source) and must still own its surfaces: the record
        # rides in the facade's session copy until a run commits one.
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, json.dumps({"A": {"url": "http://a:7125"}}))
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        os.remove(self.cura_cfg)  # the backup's source is gone
        self.binding.run_persistence_migration()
        record = self.persistence.migration_record()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["reason"], "backup-failed")
        self.assertFalse(record["backupWritten"])
        # Nothing claims the attempt on disk: no record, the blob intact.
        self.assertNotIn("migration", self.persistence.settings_document()["global"])
        self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY),
                         json.dumps({"A": {"url": "http://a:7125"}}))
        # The notice's own read finds it — the toast and the banner are
        # what the user sees.
        toasts = []
        notice = self.qt.load("MigrationNotice").MigrationNotice(
            self.persistence, whats_new_gate=lambda: False, raise_toast=toasts.append)
        self.addCleanup(notice.deleteLater)
        notice.announce()
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0]["reason"], "backup-failed")

    def test_a_failure_with_an_unwritable_store_still_reaches_the_notice(self):
        # The storage that would hold the record can be exactly what
        # failed (a read-only config folder): the session copy is the
        # notice's only source, so it must survive a dead store. The
        # bed-mesh carry fails first and holds the migration back —
        # the surfaced outcome is the carry's write-failed verdict,
        # with no backup (nothing moved, so nothing was copied).
        base = self.dir.name
        dead = PluginPersistence(
            self.settings_path,
            os.path.join(base, "state", "global.json"),
            os.path.join(base, "state", "machines"),
            save=lambda path, text: False,
        )
        binding = PrinterBinding(self.app, self.client, dead,
                                 cura_cfg_path=self.cura_cfg, old_state_path=None)
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, json.dumps({"A": {"url": "http://a:7125"}}))
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        self.assertFalse(os.path.exists(self.settings_path))
        binding.run_persistence_migration()
        record = dead.migration_record()
        self.assertEqual((record["status"], record["reason"]), ("failed", "write-failed"))
        self.assertFalse(record["backupWritten"])
        self.assertFalse(os.path.exists(self.settings_path))
        self.assertEqual([n for n in os.listdir(base) if n.startswith("cura.cfg.")], [])

    def test_a_clean_install_activates_the_document_without_a_record(self):
        # The ruling: a first boot has nothing to migrate —
        # the v2 document activates directly, no migration record.
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, "{}")
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, False)
        self.binding.run_persistence_migration()
        document = self.persistence.settings_document()
        self.assertEqual(document["configVersion"], 2)
        self.assertEqual(document["machines"], {})
        self.assertNotIn("migration", document["global"])

    def test_an_existing_document_with_nothing_to_migrate_is_untouched(self):
        # The lost-config repro's second boot: the document holds live
        # config and the blob is empty — zero writes.
        self.persistence.write_settings_document({
            "configVersion": 2,
            "global": {},
            "machines": {"Voron2 250": {"url": "https://voron", "api_key": "k"}},
        })
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, "{}")
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        self.binding.run_persistence_migration()
        document = self.persistence.settings_document()
        self.assertEqual(document["machines"]["Voron2 250"]["api_key"], "k")
        self.assertNotIn("migration", document["global"])

    def test_the_ui_state_stores_boundary_guard_and_the_facade_branch(self):
        from plugins.UiStateStore import UiStateStore

        # NaN cannot survive the JSON round-trip (allow_nan=False) —
        # the boundary guard skips the save with the owner's wording.
        store = UiStateStore(store=None)
        self.assertFalse(store.set_section_layout({"row": float("nan")}))

        class FakeFacade:
            def __init__(self):
                self.updates = []

            def merge_state_global(self, update, delete=()):
                self.updates.append((dict(update), delete))
                return True

        facade = FakeFacade()
        store = UiStateStore(store=facade)
        self.assertTrue(store.set_sections({"toolhead": False}))
        self.assertTrue(store.set_section_layout({"ids": ["toolhead"]}))
        self.assertEqual(facade.updates[0][0], {"sections": {"toolhead": False}})
        self.assertEqual(facade.updates[1][0], {"sectionLayout": {"ids": ["toolhead"]}})

    def test_the_banner_copy_has_two_flavours_and_the_diagnostics_row_demotes(self):
        from plugins.MoonrakerMonitorModel import (
            _migration_banner_text,
            _migration_diagnostics_text,
        )
        backed = {"backupWritten": True, "backupName": "cura.cfg.2026-09-18-14-30-12"}
        banner = _migration_banner_text(backed)
        self.assertIn("cura.cfg.2026-09-18-14-30-12", banner)
        self.assertIn("Help > Show Configuration Folder", banner)
        self.assertIn("reinstall the previous version", banner)
        bare = _migration_banner_text({"backupWritten": False})
        self.assertIn("Nothing was removed", bare)
        self.assertNotIn("cura.cfg", bare)
        row = _migration_diagnostics_text(backed)
        self.assertIn("cura.cfg.2026-09-18-14-30-12", row)
        self.assertIn("Nothing was removed", _migration_diagnostics_text({"backupWritten": False}))

    def test_the_toast_has_two_flavours(self):
        calls = []

        class RecordingMessage(sys.modules["UM.Message"].Message):
            def __init__(self, *args):
                super().__init__(*args)
                calls.append({"args": args, "actions": []})

            def addAction(self, *args):
                calls[-1]["actions"].append(args)

        sys.modules["UM.Message"].Message = RecordingMessage
        try:
            from plugins.FollowerRuntime import _raise_migration_toast
            _raise_migration_toast({"backupWritten": True, "backupName": "cura.cfg.2026-09-18-14-30-12"})
            _raise_migration_toast({"backupWritten": False})
        finally:
            sys.modules["UM.Message"].Message = RecordingMessage.__mro__[1]
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0]["actions"]), 1)  # flavour A: the folder action
        self.assertIn("Show backup folder", calls[0]["actions"][0])
        self.assertEqual(calls[1]["actions"], [])  # flavour B: no backup to open
        self.assertIn("cura.cfg.2026-09-18-14-30-12", calls[0]["args"][0])

    def test_the_toast_action_survives_garbage_collection(self):
        # UM.Signal holds plain functions WEAKLY: the action handler
        # must be rooted by the module, not by the frame that raised
        # the toast — a local closure dies with it and the
        # Show-backup-folder action silently stops working (the
        # 4.5.0 review's finding). The double replicates the weak
        # storage, so the forced collection genuinely discriminates.
        import gc
        import inspect
        import weakref
        from PyQt6.QtGui import QDesktopServices
        from UM.Resources import Resources

        class WeakSignal:
            def __init__(self):
                self.slots = []

            def connect(self, slot):
                if inspect.ismethod(slot):
                    self.slots.append(weakref.WeakMethod(slot))
                else:
                    self.slots.append(weakref.ref(slot))

            def emit(self, *args):
                for ref in list(self.slots):
                    slot = ref()
                    if slot is not None:
                        slot(*args)

        raised = []

        class RecordingMessage(sys.modules["UM.Message"].Message):
            def __init__(self, *args):
                super().__init__(*args)
                self.actionTriggered = WeakSignal()
                raised.append(self)

        sys.modules["UM.Message"].Message = RecordingMessage
        try:
            from plugins.FollowerRuntime import _raise_migration_toast
            with patch.object(Resources, "getConfigStoragePath", return_value="/tmp/config"), \
                 patch.object(QDesktopServices, "openUrl") as open_mock:
                _raise_migration_toast({"backupWritten": True, "backupName": "cura.cfg.2026-09-18-14-30-12"})
                gc.collect()
                raised[0].actionTriggered.emit("show_backup_folder", None)
                self.assertTrue(open_mock.called)
        finally:
            sys.modules["UM.Message"].Message = RecordingMessage.__mro__[1]


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class LateActivationTests(unittest.TestCase):
    """The boot-ready parity (the hardening pass): a late/hot
    construction and a host without initializationFinished behave
    like PrinterBinding's ready predicate — the migration notice must
    not wait for a signal that has already fired or will never
    exist."""

    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        self.base = base.name
        self.prefs = Preferences({})
        from UM.Resources import Resources
        self.resources_patcher = patch.object(
            Resources, "getStoragePath", side_effect=lambda *args, **kwargs: base.name)
        self.config_patcher = patch.object(
            Resources, "getConfigStoragePath", return_value=base.name)
        self.resources_patcher.start()
        self.config_patcher.start()
        self.addCleanup(self.resources_patcher.stop)
        self.addCleanup(self.config_patcher.stop)
        from plugins.FollowerRuntime import FollowerRuntime
        self.FollowerRuntime = FollowerRuntime

    def _app(self, started, with_signal):
        app = self.qt.Application(self.prefs)
        app.stack = self.qt.Machine("A")
        app.started = started
        if with_signal:
            class FakeSignal:
                def __init__(self):
                    self.handlers = []

                def connect(self, handler):
                    self.handlers.append(handler)

                def emit(self, *args):
                    for handler in list(self.handlers):
                        handler(*args)

            app.initializationFinished = FakeSignal()
        return app

    def test_normal_startup_waits_for_the_boot_edge(self):
        app = self._app(started=False, with_signal=True)
        from plugins.MigrationNotice import MigrationNotice
        # The class patch must be active BEFORE the runtime connects:
        # the signal captures the bound method at connect time, so an
        # instance patch after construction would spy on nothing.
        with patch.object(MigrationNotice, "announce") as announce:
            owner = self.FollowerRuntime(app, None)
            self.assertEqual(announce.call_count, 0)  # nothing runs early
            # The three boot-deferred owners: the preview hosts, the
            # binding's readiness and the migration notice.
            self.assertEqual(len(app.initializationFinished.handlers), 3)
            app.initializationFinished.emit()
            self.assertEqual(announce.call_count, 1)
        self.addCleanup(owner.close)

    def test_a_late_construction_announces_immediately(self):
        app = self._app(started=True, with_signal=True)
        from plugins.MigrationNotice import MigrationNotice
        with patch.object(MigrationNotice, "announce") as announce:
            owner = self.FollowerRuntime(app, None)
        self.addCleanup(owner.close)
        self.assertEqual(app.initializationFinished.handlers, [])
        self.assertEqual(announce.call_count, 1)

    def test_a_host_without_the_boot_signal_announces_immediately(self):
        app = self._app(started=False, with_signal=False)
        from plugins.MigrationNotice import MigrationNotice
        with patch.object(MigrationNotice, "announce") as announce:
            owner = self.FollowerRuntime(app, None)
        self.addCleanup(owner.close)
        self.assertEqual(announce.call_count, 1)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class BindingReadinessTests(unittest.TestCase):
    """The boot-complete latch (B1): Cura re-reads the preference file
    after plugins load and drops every preference write until it has
    started, so a construction-time clean is resurrected. The
    destructive half — the legacy chain's blob push, the one-shot's
    clean — waits for initializationFinished; reads and the connection
    do not."""

    class Client:
        def __init__(self):
            self.configures = []
            self.starts = 0
            self.stops = 0

        def stop(self, reset_session=False):
            self.stops += 1

        def set_trace_http(self, value):
            pass

        def configure(self, url, api_key, poll_interval_ms, **kwargs):
            self.configures.append(url)

        def start(self):
            self.starts += 1

    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = self.dir.name
        self.settings_path = os.path.join(base, "settings.json")
        self.blob = {"A": {"url": "http://a:7125"}, "B": {"url": "http://b:7125"}}
        self.save_fails = False

        def save(path, text):
            if self.save_fails and path == self.settings_path:
                return False
            return _pretty_save(path, text)

        self.persistence = PluginPersistence(
            self.settings_path,
            os.path.join(base, "state", "global.json"),
            os.path.join(base, "state", "machines"),
            save=save,
        )
        self.cura_cfg = os.path.join(base, "cura.cfg")
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write("[general]\nversion = 1\n[moonrakerprintfollower]\nprinter_configs_v1 = %s\n"
                         % json.dumps(self.blob))
        self.prefs = Preferences({})
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, json.dumps(self.blob))
        self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
        Application = self.qt.Application

        class BootApplication(Application):
            initializationFinished = pyqtSignal()

        self.app = BootApplication(self.prefs)
        self.app.stack = self.qt.Machine("A")
        self.client = self.Client()
        self.binding = PrinterBinding(self.app, self.client, self.persistence,
                                      cura_cfg_path=self.cura_cfg, old_state_path=None)
        self.app.initializationFinished.connect(self.binding.mark_ready)

    def _document(self):
        return self.persistence.settings_document()

    def test_construction_and_start_cannot_run_the_destructive_half(self):
        self.binding.start()
        # Nothing destructive: no document, no clean, cura.cfg intact.
        self.assertFalse(os.path.exists(self.settings_path))
        self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY), json.dumps(self.blob))
        with open(self.cura_cfg, "rb") as handle:
            self.assertIn(b"printer_configs_v1", handle.read())
        # Reads and the connection stay allowed before readiness.
        self.assertEqual(self.binding.identity[0], "A")
        self.assertEqual(self.binding.config.url, "http://a:7125")

    def test_readiness_migrates_the_legacy_install_and_cleans(self):
        self.binding.start()
        self.app.initializationFinished.emit()
        document = self._document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(document["machines"]["B"]["url"], "http://b:7125")
        self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY), "{}")
        record = self.persistence.migration_record()
        self.assertEqual(record["status"], "ok")
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, record["backupName"])))

    def test_a_clean_install_still_activates_once_ready(self):
        self.prefs.setValue(PrinterConfigStore.PREF_KEY, "{}")
        self.binding.start()
        self.assertFalse(os.path.exists(self.settings_path))
        self.app.initializationFinished.emit()
        document = self._document()
        self.assertEqual(document["configVersion"], 2)
        self.assertEqual(document["machines"], {})
        self.assertNotIn("migration", document["global"])

    def test_a_machine_change_before_readiness_resolves_once_ready(self):
        self.binding.start()
        self.app.stack = self.qt.Machine("B")
        self.app.globalContainerStackChanged.emit()
        # The switch is honoured (reads and the connection), but the
        # destructive half is still deferred.
        self.assertEqual(self.binding.identity[0], "B")
        self.assertFalse(os.path.exists(self.settings_path))
        self.app.initializationFinished.emit()
        document = self._document()
        self.assertIn("A", document["machines"])
        self.assertIn("B", document["machines"])
        # The resolved identity's migrated record is what the client
        # ends up connected to.
        self.assertEqual(self.client.configures[-1], "http://b:7125")

    def test_a_plugin_loaded_after_the_boot_does_not_wait_forever(self):
        # The re-enable path: Cura sets `started` immediately before it
        # emits initializationFinished, so a plugin constructed after
        # the boot has no signal left to wait for. The latch must be
        # open from construction — otherwise the migration would never
        # run for that install.
        self.app.started = True
        binding = PrinterBinding(self.app, self.client, self.persistence,
                                 cura_cfg_path=self.cura_cfg, old_state_path=None)
        binding.start()
        self.assertEqual(self.persistence.settings_document()["machines"]["A"]["url"], "http://a:7125")

    def test_repeated_readiness_notifications_are_idempotent(self):
        original = PrinterBinding.run_persistence_migration
        calls = []

        def counted(self_):
            calls.append(1)
            return original(self_)

        with patch.object(PrinterBinding, "run_persistence_migration", counted):
            self.binding.start()
            self.assertEqual(calls, [])
            self.app.initializationFinished.emit()
            self.assertEqual(len(calls), 1)
            self.app.initializationFinished.emit()
            self.app.initializationFinished.emit()
        self.assertEqual(len(calls), 1)

    def test_a_refused_save_reports_failure_and_keeps_the_live_connection(self):
        self.binding.start()
        self.app.initializationFinished.emit()
        self.assertEqual(self.client.configures[-1], "http://a:7125")
        self.save_fails = True
        self.assertFalse(self.binding.apply(
            PrinterConfig(url="http://moved:7125", api_key="k")))
        # The refused write left the document alone: the live connection
        # stays on the last usable configuration, and nothing claims the
        # new one was saved.
        self.assertEqual(self.persistence.get_machine("A")["url"], "http://a:7125")
        self.assertEqual(self.client.configures[-1], "http://a:7125")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class FacadeStaleWriterTests(unittest.TestCase):
    """The facade's key-scoped writes are read-modify-writes: two
    writers each holding a stale document must not erase each other
    (the "only the last machine saved" fault, H5's several live
    writers over one file)."""

    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = self.dir.name
        self.paths = (os.path.join(base, "settings.json"),
                      os.path.join(base, "state", "global.json"),
                      os.path.join(base, "state", "machines"))

    def _facade(self):
        return PluginPersistence(*self.paths, save=_pretty_save)

    def test_two_stale_writers_updating_separate_keys_both_survive(self):
        first, second = self._facade(), self._facade()
        first.set_machine("A", {"url": "http://a:7125"})  # both now hold a document
        second.set_machine("B", {"url": "http://b:7125"})  # a sibling write lands
        first.set_global({"bedMeshVisible": True})         # the stale writer's own key
        document = self._facade().settings_document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(document["machines"]["B"]["url"], "http://b:7125")
        self.assertTrue(document["global"]["bedMeshVisible"])

    def test_the_global_chrome_merge_survives_a_sibling_write(self):
        first, second = self._facade(), self._facade()
        first.merge_state_global({"sections": {"toolhead": False}})
        second.set_machine("A", {"url": "http://a:7125"})
        first.merge_state_global({"sectionLayout": {"ids": ["toolhead"]}})
        self.assertEqual(second.state_global_document()["sections"], {"toolhead": False})
        self.assertEqual(second.state_global_document()["sectionLayout"], {"ids": ["toolhead"]})
        self.assertEqual(second.get_machine("A")["url"], "http://a:7125")


if __name__ == "__main__":
    unittest.main()
