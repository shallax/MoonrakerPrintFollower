"""The persistence re-points' integration contracts (4.5.0): the
console's shard writes and settle timer, the shard-first load, the
unknown-identity skip, the migration trigger, and the toast's two
flavours. Qt-guarded — the container runs them for real."""
import json
import os
import sys
import tempfile
import unittest

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

    def test_a_clean_install_activates_the_document_without_a_record(self):
        # The author's ruling: a first boot has nothing to migrate —
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


if __name__ == "__main__":
    unittest.main()
