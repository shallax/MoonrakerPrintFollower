"""Ownership boundaries of the concrete index worker collaborators."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from Tests.qt_runtime_support import QT_AVAILABLE, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class IndexTaskOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.tasks = self.qt.load("IndexTasks")

    def test_prepared_reader_keeps_its_submitted_store_identity_and_table(self):
        calls = []
        table = [(1, 12, 24)]
        old = SimpleNamespace(read=lambda identity, entries, layer:
                              calls.append((identity, entries, layer)) or b"old")
        current = SimpleNamespace(store=old, identity="A", table=table)
        reader = self.tasks.PreparedLayerReader(current.store, current.identity, current.table)
        current.store = SimpleNamespace(read=lambda *args: b"wrong printer")
        current.identity = "B"
        current.table = []
        self.assertEqual(reader.read(0), b"old")
        self.assertEqual(calls, [("A", table, 0)])
        self.assertIs(reader.table, table, "submission must not copy a print-wide table")
        with self.assertRaises(FrozenInstanceError):
            reader.identity = "B"

    def test_missing_prepared_source_is_a_cache_miss(self):
        self.assertIsNone(self.tasks.PreparedLayerReader(None, "A", []).read(0))
        store = SimpleNamespace(read=lambda *args: self.fail("absent table was read"))
        self.assertIsNone(self.tasks.PreparedLayerReader(store, "A", None).read(0))

    def test_worker_records_have_no_service_or_coordinator_capability(self):
        for name in ("LayerHydrationTask", "LayerPreparationTask", "LayerArrayTask"):
            cls = getattr(self.tasks, name)
            self.assertTrue(cls.__dataclass_params__.frozen)
            self.assertTrue(set(cls.__dataclass_fields__).isdisjoint(
                {"service", "owner", "parent", "coordinator", "model"}))
