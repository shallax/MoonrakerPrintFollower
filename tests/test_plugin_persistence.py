"""Pure tests for the 4.5.0 persistence facade (the panel's E2/E3/H5
rulings): the pinned field table, the key-scoped merge, the shard
semantics, the pretty-print contract and the injected save
primitive."""
import json
import os
import tempfile
import unittest
from dataclasses import asdict

from plugins.PluginPersistence import (
    SETTINGS_FIELDS,
    STATE_FIELDS,
    PluginPersistence,
)
from plugins.PrinterConfig import PrinterConfig


class FieldTableTests(unittest.TestCase):
    def test_the_table_covers_the_dataclass_exactly_once(self):
        fields = set(asdict(PrinterConfig()))
        self.assertEqual(fields, set(SETTINGS_FIELDS) | set(STATE_FIELDS))
        self.assertEqual(len(fields), len(SETTINGS_FIELDS) + len(STATE_FIELDS))

    def test_the_console_trio_is_the_state_side(self):
        self.assertEqual(
            set(STATE_FIELDS),
            {"console_history", "console_transcript", "console_store_time"},
        )


class PluginPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = self.dir.name
        self.saves = []

        def _save(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.saves.append((path, text))
            return True

        self.facade = PluginPersistence(
            os.path.join(base, "moonrakerprintfollower_settings.json"),
            os.path.join(base, "state", "global.json"),
            os.path.join(base, "state", "machines"),
            save=_save,
        )

    def _seed(self, machines):
        self.facade.write_settings_document({
            "configVersion": 2,
            "global": {"activeMachineId": None, "migration": {"status": "ok"}},
            "machines": machines,
        })

    def test_set_machine_merges_only_that_record(self):
        self._seed({
            "A": {"url": "http://a:7125", "api_key": "key-a"},
            "B": {"url": "http://b:7125"},
        })
        self.assertTrue(self.facade.set_machine("A", {"url": "http://a2:7125"}))
        document = self.facade.settings_document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a2:7125")
        # The sibling record and the API key survive the key-scoped
        # write untouched (the E2 shallow-merge trap).
        self.assertEqual(document["machines"]["A"]["api_key"], "key-a")
        self.assertEqual(document["machines"]["B"], {"url": "http://b:7125"})
        self.assertEqual(document["global"]["migration"], {"status": "ok"})

    def test_remove_machine_drops_only_that_record(self):
        self._seed({"A": {"url": "http://a:7125"}, "B": {"url": "http://b:7125"}})
        self.assertTrue(self.facade.remove_machine("A"))
        document = self.facade.settings_document()
        self.assertNotIn("A", document["machines"])
        self.assertIn("B", document["machines"])

    def test_set_global_merges(self):
        self._seed({})
        self.assertTrue(self.facade.set_global({"activeMachineId": "A"}))
        self.assertTrue(self.facade.set_global({"migration": {"status": "failed"}}))
        document = self.facade.settings_document()
        self.assertEqual(document["global"]["activeMachineId"], "A")
        self.assertEqual(document["global"]["migration"]["status"], "failed")

    def test_migration_record_merges_never_replaces(self):
        self._seed({})
        self.assertTrue(self.facade.set_migration_record({"status": "failed", "reason": "backup-failed"}))
        self.assertTrue(self.facade.set_migration_record({"toastShown": True}))
        record = self.facade.migration_record()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["reason"], "backup-failed")
        self.assertEqual(record["toastShown"], True)

    def test_machine_state_shard_merges_top_level(self):
        self.assertTrue(self.facade.set_machine_state("A", {"consoleTranscript": [{"kind": "command", "text": "G28"}]}))
        self.assertTrue(self.facade.set_machine_state("A", {"consoleStoreTime": 1.5}))
        state = self.facade.get_machine_state("A")
        self.assertEqual(state["consoleTranscript"][0]["text"], "G28")
        self.assertEqual(state["consoleStoreTime"], 1.5)

    def test_the_retired_console_history_key_is_shed(self):
        # The dead key cannot enter a shard: the facade drops it after
        # the merge, and a shard that already carries it loses it on
        # its next write (the 4.5.0 cleanup).
        self.assertTrue(self.facade.set_machine_state("A", {"consoleHistory": ["G28"]}))
        state = self.facade.get_machine_state("A")
        self.assertNotIn("consoleHistory", state)
        self.assertTrue(self.facade.write_machine_state_document("A", {"consoleHistory": ["G28"], "consoleStoreTime": 1.5}))
        self.assertTrue(self.facade.set_machine_state("A", {"consoleTranscript": []}))
        state = self.facade.get_machine_state("A")
        self.assertNotIn("consoleHistory", state)
        self.assertEqual(state["consoleStoreTime"], 1.5)

    def test_the_documents_are_pretty_printed(self):
        self._seed({"A": {"url": "http://a:7125", "api_key": "k"}})
        path, text = self.saves[-1]
        self.assertIn("\n  ", text)
        self.assertIn("\n", text)
        # Sorted keys: "api_key" must precede "url" in the serialised
        # text (the stable-diff contract, L2).
        self.assertLess(text.index('"api_key"'), text.index('"url"'))

    def test_the_injected_save_primitive_carries_the_write(self):
        self._seed({})
        self.assertTrue(self.saves)
        path, text = self.saves[0]
        self.assertEqual(path, self.facade._settings._path)
        self.assertEqual(json.loads(text)["configVersion"], 2)


if __name__ == "__main__":
    unittest.main()
