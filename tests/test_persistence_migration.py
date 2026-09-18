"""Pure tests for the 4.5.0 one-shot migration (the panel's C1-C6/B1/E5
rulings): the strict source read, the backup-before-clean gate, the
verify-by-re-read interlock, the tri-state record and the idempotent
replay."""
import json
import os
import tempfile
import unittest

from plugins.PersistenceMigration import (
    read_source,
    run_migration,
    split_record,
    write_backup,
)


def _pretty_write(path, document):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
    return True


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = self.dir.name
        self.cura_cfg = os.path.join(base, "cura.cfg")
        self.settings_path = os.path.join(base, "moonrakerprintfollower_settings.json")
        self.state_dir = os.path.join(base, "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.old_state = os.path.join(base, "moonrakerprintfollower_sections.json")
        self.prefs = {}

    def _cfg(self, blob):
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write(
                "[general]\nversion = 1\n[moonrakerprintfollower]\nprinter_configs_v1 = %s\n" % blob
            )

    def _writers(self):
        return {
            "settings": lambda doc: _pretty_write(self.settings_path, doc),
            "state_global": lambda doc: _pretty_write(
                os.path.join(self.state_dir, "global.json"), doc
            ),
            "state_machine": lambda machine_id, doc: _pretty_write(
                os.path.join(self.state_dir, f"{machine_id}.json"), doc
            ),
        }

    def _record_write(self, update):
        # The facade's merge-write of global.migration (slice 2); the
        # fields merge INTO the record, never replacing it.
        try:
            with open(self.settings_path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            document = {"configVersion": 2, "global": {}, "machines": {}}
        record = document.setdefault("global", {}).setdefault("migration", {})
        record.update(update)
        _pretty_write(self.settings_path, document)
        return True

    def _run(self, blob_value, timestamp="2026-09-18-14-30-12", writers=None):
        writers = writers or self._writers()
        return run_migration(
            blob_value, self.cura_cfg, self.settings_path, self.state_dir,
            self.old_state, writers["settings"], self._record_write,
            writers["state_global"], writers["state_machine"],
            lambda key, value: self.prefs.__setitem__(key, value),
            timestamp,
        )

    def _settings_document(self):
        with open(self.settings_path, encoding="utf-8") as handle:
            return json.load(handle)

    # -- The strict source read -------------------------------------

    def test_read_source_distinguishes_the_states(self):
        self.assertEqual(read_source(None), ("absent", {}))
        self.assertEqual(read_source(""), ("absent", {}))
        self.assertEqual(read_source("{}"), ("empty", {}))
        self.assertEqual(read_source('{"A": {}}'), ("records", {"A": {}}))
        self.assertEqual(read_source("not json {{{"), ("corrupt", {}))
        self.assertEqual(read_source("[1, 2]"), ("corrupt", {}))

    # -- The healthy-empty path (C5) ---------------------------------

    def test_empty_blob_reaches_the_ok_path_and_activates_the_schema(self):
        outcome = self._run("{}")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.reason, "nothing-to-do")
        self.assertFalse(outcome.backup_written)
        document = self._settings_document()
        self.assertEqual(document["configVersion"], 2)
        self.assertEqual(document["machines"], {})
        self.assertEqual(document["global"]["migration"]["status"], "ok")
        with open(os.path.join(self.state_dir, "global.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["configVersion"], 2)

    def test_absent_blob_is_nothing_to_do(self):
        outcome = self._run(None)
        self.assertEqual((outcome.status, outcome.reason), ("ok", "nothing-to-do"))

    def test_the_empty_path_leaves_an_existing_document_untouched(self):
        # The first-install lost-config guard: a v2 document holding
        # live config is the source of truth — a nothing-to-do
        # migration must never replace it.
        _pretty_write(self.settings_path, {
            "configVersion": 2,
            "global": {"bedMeshVisible": True},
            "machines": {"Voron2 250": {"url": "https://voron", "api_key": "k"}},
        })
        outcome = self._run("{}")
        self.assertEqual((outcome.status, outcome.reason), ("ok", "nothing-to-do"))
        document = self._settings_document()
        self.assertEqual(document["machines"]["Voron2 250"]["api_key"], "k")
        self.assertEqual(document["global"]["bedMeshVisible"], True)
        self.assertNotIn("migration", document["global"])

    def test_a_records_rerun_keeps_the_live_machines(self):
        # A re-run against a populated document: the live records win,
        # the migrated records only fill the gaps.
        _pretty_write(self.settings_path, {
            "configVersion": 2,
            "global": {},
            "machines": {"Voron250": {"url": "https://live", "api_key": "k"}},
        })
        blob = json.dumps({
            "Voron250": {"url": "http://legacy:7125", "api_key": "old"},
            "Other": {"url": "http://o:7125", "api_key": ""},
        })
        self._cfg(blob)
        outcome = self._run(blob)
        self.assertEqual(outcome.status, "ok")
        document = self._settings_document()
        self.assertEqual(document["machines"]["Voron250"]["url"], "https://live")
        self.assertEqual(document["machines"]["Voron250"]["api_key"], "k")
        self.assertIn("Other", document["machines"])
        # The record merges flat, never nested.
        self.assertEqual(document["global"]["migration"]["status"], "ok")
        self.assertNotIn("migration", document["global"]["migration"])

    # -- The corrupt path (the ruling over C1's gate) --------

    def test_corrupt_blob_flags_and_cleans_with_a_verified_backup(self):
        self._cfg("not json {{{")
        outcome = self._run("not json {{{")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "corrupt-blob")
        self.assertTrue(outcome.backup_written)
        backup = os.path.join(self.dir.name, outcome.backup_name)
        with open(backup, "rb") as handle:
            self.assertIn(b"printer_configs_v1", handle.read())
        # The clean: the blob and the legacy mirror reset to their
        # registered defaults (the in-memory return-to-default, C4).
        from plugins.PrinterConfig import PrinterConfigStore
        self.assertEqual(self.prefs[PrinterConfigStore.PREF_KEY], "{}")
        self.assertEqual(
            self.prefs[PrinterConfigStore.LEGACY_MAP["enabled"]],
            PrinterConfigStore.LEGACY_DEFAULTS["enabled"],
        )
        # The migrated flags reset too: the [moonrakerprintfollower]
        # section leaves cura.cfg entirely.
        self.assertIs(self.prefs[PrinterConfigStore.MIGRATED_KEY], False)
        self.assertIs(self.prefs[PrinterConfigStore.MOONRAKER_CONNECTION_MIGRATED_KEY], False)
        document = self._settings_document()
        self.assertEqual(document["global"]["migration"]["status"], "failed")

    def test_corrupt_blob_without_a_backup_leaves_everything(self):
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write("[general]\nversion = 1\n")
        outcome = self._run("not json {{{")
        self.assertEqual((outcome.status, outcome.reason), ("failed", "corrupt-blob"))
        self.assertFalse(outcome.backup_written)
        self.assertEqual(self.prefs, {})
        self.assertFalse(os.path.exists(self.settings_path))

    # -- The records path ---------------------------------------------

    def test_records_migrate_and_split_across_the_two_homes(self):
        record_a = {
            "url": "http://192.168.1.50:7125",
            "api_key": "secret",
            "camera_selected": "webcam-1",
            "console_history": ["G28"],
            "console_transcript": [{"kind": "response", "text": "ok", "error": False, "success": True}],
            "console_store_time": 1234.5,
        }
        record_b = {"url": "http://192.168.1.51:7125"}
        self._cfg(json.dumps({"A": record_a, "B": record_b}))
        with open(self.old_state, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"toolhead": False}, "whatsNewSeen": "4.4.0"}, handle)
        outcome = self._run(json.dumps({"A": record_a, "B": record_b}))
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.reason, "migrated")
        self.assertEqual(outcome.records, 2)

        document = self._settings_document()
        self.assertNotIn("console_transcript", document["machines"]["A"])
        self.assertEqual(document["machines"]["A"]["url"], "http://192.168.1.50:7125")
        self.assertEqual(document["machines"]["A"]["feed_mode"], "websocket")
        with open(os.path.join(self.state_dir, "A.json"), encoding="utf-8") as handle:
            shard = json.load(handle)
        self.assertNotIn("consoleHistory", shard)
        self.assertEqual(shard["consoleTranscript"][0]["text"], "ok")
        self.assertEqual(shard["consoleStoreTime"], 1234.5)
        # The old chrome moved to the new global document, and the
        # pre-4.5.0 sections file left no trace (found in live testing).
        with open(os.path.join(self.state_dir, "global.json"), encoding="utf-8") as handle:
            chrome = json.load(handle)
        self.assertEqual(chrome["sections"], {"toolhead": False})
        self.assertFalse(os.path.exists(self.old_state))
        # The backup exists and the clean ran.
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, outcome.backup_name)))
        from plugins.PrinterConfig import PrinterConfigStore
        self.assertEqual(self.prefs[PrinterConfigStore.PREF_KEY], "{}")
        self.assertEqual(document["global"]["migration"]["backupName"], outcome.backup_name)

    def test_legacy_typed_history_folds_into_the_transcript(self):
        # A record with only the legacy typed-history key (no
        # transcript) migrates its lines as command entries — the
        # history survives the move and the dead consoleHistory shard
        # key is never written (the 4.5.0 cleanup).
        record = {"url": "http://192.168.1.50:7125", "console_history": ["G28", "M117 hi"]}
        self._cfg(json.dumps({"A": record}))
        outcome = self._run(json.dumps({"A": record}))
        self.assertEqual(outcome.status, "ok")
        with open(os.path.join(self.state_dir, "A.json"), encoding="utf-8") as handle:
            shard = json.load(handle)
        self.assertNotIn("consoleHistory", shard)
        self.assertEqual([entry["text"] for entry in shard["consoleTranscript"]], ["G28", "M117 hi"])
        self.assertTrue(all(entry["kind"] == "command" and entry["error"] is False
                            and entry["success"] is False for entry in shard["consoleTranscript"]))

    def test_backup_failed_stops_before_anything_moves(self):
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write("[general]\nversion = 1\n")
        outcome = self._run(json.dumps({"A": {"url": "http://x:7125"}}))
        self.assertEqual((outcome.status, outcome.reason), ("failed", "backup-failed"))
        self.assertEqual(self.prefs, {})
        self.assertFalse(os.path.exists(self.settings_path))

    def test_write_failed_keeps_cura_cfg_intact(self):
        record = json.dumps({"A": {"url": "http://x:7125"}})
        self._cfg(record)
        writers = self._writers()
        writers["settings"] = lambda doc: False
        outcome = self._run(record, writers=writers)
        self.assertEqual((outcome.status, outcome.reason), ("failed", "write-failed"))
        self.assertEqual(self.prefs, {})

    def test_verify_failed_keeps_cura_cfg_intact(self):
        record = json.dumps({"A": {"url": "http://x:7125"}})
        self._cfg(record)
        writers = self._writers()
        writers["state_machine"] = lambda machine_id, doc: True  # writes nothing
        outcome = self._run(record, writers=writers)
        self.assertEqual((outcome.status, outcome.reason), ("failed", "verify-failed"))
        self.assertEqual(self.prefs, {})

    # -- Replay -------------------------------------------------------

    def test_second_run_after_a_failed_first_replays(self):
        record = json.dumps({"A": {"url": "http://x:7125"}})
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write("[general]\nversion = 1\n")
        self.assertEqual(self._run(record).reason, "backup-failed")
        self._cfg(record)
        self.assertEqual(self._run(record).status, "ok")

    def test_replay_after_success_is_nothing_to_do(self):
        record = json.dumps({"A": {"url": "http://x:7125"}})
        self._cfg(record)
        self.assertEqual(self._run(record).status, "ok")
        # The clean's in-memory effect: the blob now reads as the
        # registered default, so a re-run sees a healthy empty source.
        self.assertEqual(self._run("{}").reason, "nothing-to-do")

    # -- The backup's content check (C6) ------------------------------

    def test_backup_verifies_content_not_existence(self):
        self._cfg(json.dumps({"A": {}}))
        backup = os.path.join(self.dir.name, "cura.cfg.2026-09-18-14-30-12")
        self.assertTrue(write_backup(self.cura_cfg, backup))
        with open(self.cura_cfg, "rb") as source, open(backup, "rb") as copy:
            self.assertEqual(source.read(), copy.read())
        with open(self.cura_cfg, "w", encoding="utf-8") as handle:
            handle.write("[general]\nversion = 1\n")
        self.assertFalse(write_backup(self.cura_cfg, backup))

    # -- The field split (E3) ------------------------------------------

    def test_split_record_keeps_the_field_split(self):
        settings, state = split_record({
            "url": "http://x:7125",
            "console_transcript": [{"kind": "command", "text": "M117 hi", "error": False, "success": False}],
        })
        self.assertNotIn("console_transcript", settings)
        self.assertNotIn("console_history", settings)
        self.assertNotIn("console_store_time", settings)
        self.assertEqual(settings["url"], "http://x:7125")
        self.assertEqual(state["consoleTranscript"][0]["text"], "M117 hi")


if __name__ == "__main__":
    unittest.main()
