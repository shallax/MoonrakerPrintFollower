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
from plugins.PrinterConfig import PrinterConfigStore


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

    def _run(self, blob_value, timestamp="2026-09-18-14-30-12", writers=None,
             record_write=None, set_pref=None):
        writers = writers or self._writers()
        return run_migration(
            blob_value, self.cura_cfg, self.settings_path, self.state_dir,
            self.old_state, writers["settings"], record_write or self._record_write,
            writers["state_global"], writers["state_machine"],
            set_pref or (lambda key, value: self.prefs.__setitem__(key, value)),
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

    def test_empty_blob_is_a_side_effect_free_no_op(self):
        # Schema activation is the BINDING's job (the reviewer's
        # first-install invariant): the transformer writes nothing for
        # absent source — no document, no record, no state files.
        outcome = self._run("{}")
        self.assertEqual((outcome.status, outcome.reason), ("ok", "nothing-to-do"))
        self.assertFalse(outcome.backup_written)
        self.assertFalse(os.path.exists(self.settings_path))
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "global.json")))

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

    def test_a_corrupt_recovery_state_write_failure_leaves_the_source_for_replay(self):
        # The 4.5.0 ordering: the clean runs only after the recovery
        # documents have landed — a failed recovery leaves the corrupt
        # source in place and the next boot replays it.
        self._cfg("not json {{{")
        writers = self._writers()
        writers["state_global"] = lambda doc: False
        outcome = self._run("not json {{{", writers=writers)
        self.assertEqual((outcome.status, outcome.reason), ("failed", "write-failed"))
        self.assertTrue(outcome.backup_written)
        self.assertEqual(self.prefs, {})  # no clean, no preference writes
        self.assertTrue(os.path.exists(self.cura_cfg))
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "global.json")))

        # The next boot with functioning writers retries from the same
        # source: the recovery lands, the failed record is durable, and
        # ONLY THEN the legacy state is cleaned.
        outcome = self._run("not json {{{")
        self.assertEqual((outcome.status, outcome.reason), ("failed", "corrupt-blob"))
        document = self._settings_document()
        self.assertEqual(document["global"]["migration"]["status"], "failed")
        from plugins.PrinterConfig import PrinterConfigStore
        self.assertEqual(self.prefs[PrinterConfigStore.PREF_KEY], "{}")
        self.assertIs(self.prefs[PrinterConfigStore.MIGRATED_KEY], False)

    def test_a_corrupt_recovery_settings_write_failure_leaves_the_source_for_replay(self):
        self._cfg("not json {{{")
        writers = self._writers()
        writers["settings"] = lambda doc: False
        outcome = self._run("not json {{{", writers=writers)
        self.assertEqual((outcome.status, outcome.reason), ("failed", "write-failed"))
        self.assertTrue(outcome.backup_written)
        self.assertEqual(self.prefs, {})
        self.assertFalse(os.path.exists(self.settings_path))

        outcome = self._run("not json {{{")
        self.assertEqual((outcome.status, outcome.reason), ("failed", "corrupt-blob"))
        document = self._settings_document()
        self.assertEqual(document["global"]["migration"]["status"], "failed")
        from plugins.PrinterConfig import PrinterConfigStore
        self.assertEqual(self.prefs[PrinterConfigStore.PREF_KEY], "{}")
        self.assertIs(self.prefs[PrinterConfigStore.MIGRATED_KEY], False)

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

    # -- The commit step (the Q1 ordering fix) ------------------------

    def test_a_failed_verify_leaves_no_record_and_the_next_boot_retries(self):
        """The two-boot repro of the early-record fault: the candidate
        write used to persist an ok record BEFORE the verify, so a boot
        that failed verification left a success on disk and the boot
        after that ran the legacy cleanup over a migration that never
        landed. The observable failure is the SECOND boot."""
        blob = json.dumps({"A": {"url": "http://x:7125"}})
        self._cfg(blob)
        events = []
        writers = self._writers()
        writers["state_machine"] = lambda machine_id, document: True  # the shard never lands

        def record_write(update):
            events.append(("record", dict(update)))
            return self._record_write(update)

        def set_pref(key, value):
            events.append(("pref", key))
            self.prefs[key] = value

        boot1 = self._run(blob, writers=writers, record_write=record_write, set_pref=set_pref)
        self.assertEqual((boot1.status, boot1.reason), ("failed", "verify-failed"))
        # Uncommitted: nothing cleaned, nothing recorded, cura.cfg intact.
        self.assertEqual(events, [])
        self.assertEqual(self.prefs, {})
        with open(self.settings_path, encoding="utf-8") as handle:
            self.assertNotIn("migration", json.load(handle).get("global", {}))

        # Boot 2: the source is still intact, so the attempt replays and
        # this time the verify passes — the clean runs and the ok record
        # lands as the LAST step, after everything it claims.
        events.clear()
        boot2 = self._run(blob, record_write=record_write, set_pref=set_pref)
        self.assertEqual((boot2.status, boot2.reason), ("ok", "migrated"))
        self.assertTrue(boot2.record_persisted)
        kinds = [kind for kind, _ in events]
        self.assertIn("pref", kinds)
        self.assertEqual(kinds[-1], "record")
        self.assertLess(max(i for i, kind in enumerate(kinds) if kind == "pref"),
                        kinds.index("record"))
        self.assertEqual(events[-1][1]["status"], "ok")
        self.assertEqual(self._settings_document()["global"]["migration"]["status"], "ok")
        self.assertEqual(self.prefs[PrinterConfigStore.PREF_KEY], "{}")

    def test_a_crash_between_the_candidate_writes_and_the_verify_replays(self):
        """The crash-equivalent: the candidate files landed, then the
        process died before the verify/clean/commit. Nothing on disk
        may read as a finished migration — an absent record is exactly
        "not committed yet" — and the next boot replays from the intact
        source."""
        blob = json.dumps({"A": {"url": "http://x:7125"}})
        self._cfg(blob)
        writers = self._writers()

        class Crash(Exception):
            pass

        def crash_after_the_write(document):
            writers["settings"](document)  # the candidate file DID land
            raise Crash("the process died before the verify")

        with self.assertRaises(Crash):
            self._run(blob, writers={**writers, "settings": crash_after_the_write})
        with open(self.settings_path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["machines"]["A"]["url"], "http://x:7125")
        self.assertNotIn("migration", document.get("global", {}))
        self.assertEqual(self.prefs, {})  # the clean never ran
        with open(self.cura_cfg, "rb") as handle:
            self.assertIn(b"printer_configs_v1", handle.read())

        boot2 = self._run(blob)
        self.assertEqual((boot2.status, boot2.reason), ("ok", "migrated"))
        self.assertEqual(self._settings_document()["global"]["migration"]["status"], "ok")

    def test_the_final_record_write_is_checked_and_reported(self):
        # The commit's own write can fail after the migration itself
        # succeeded: the verdict must not claim a persisted record the
        # next boot will not find (it replays against an empty source).
        blob = json.dumps({"A": {"url": "http://x:7125"}})
        self._cfg(blob)
        outcome = self._run(blob, record_write=lambda update: False)
        self.assertEqual((outcome.status, outcome.reason), ("ok", "migrated"))
        self.assertFalse(outcome.record_persisted)
        self.assertEqual(self.prefs[PrinterConfigStore.PREF_KEY], "{}")  # the clean ran
        with open(self.settings_path, encoding="utf-8") as handle:
            self.assertNotIn("migration", json.load(handle).get("global", {}))

    def test_the_empty_path_is_side_effect_free(self):
        # Schema activation is the BINDING's job: absent/empty source
        # returns the no-op outcome WITHOUT touching the writers — a
        # fresh install must never manufacture a record, a backup or
        # a failure (the reviewer's first-install invariant).
        calls = []
        writers = self._writers()
        for name in ("settings", "state_global", "state_machine"):
            original = writers[name]

            def spy(document, _original=original, _name=name):
                calls.append(_name)
                return _original(document)

            writers[name] = spy
        outcome = self._run("{}", writers=writers)
        self.assertEqual((outcome.status, outcome.reason), ("ok", "nothing-to-do"))
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(self.settings_path))

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
