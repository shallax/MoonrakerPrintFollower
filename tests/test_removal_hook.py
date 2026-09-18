"""The machine-removal hook's contract (the panel's E1/B2/E6 rulings):
the wipe targets the REMOVED id — never the active identity, because
removeMachine activates a replacement first — the filters tolerate
Cura's own unsolicited removals, and the legacy mirror survives on
the settings write. Qt-guarded like the other runtime suites."""
import unittest
from unittest.mock import patch

try:
    from PyQt6.QtCore import QTimer
    from qt_runtime_support import QT_AVAILABLE, Preferences, runtime
    if QT_AVAILABLE:
        # runtime() is a generator: its body registers the UM stubs
        # under patch.dict(sys.modules), active only while it runs —
        # the plugin imports happen inside one pass.
        _started = runtime()
        _started.__enter__()
        try:
            from plugins.PrinterBinding import PrinterBinding, _REMOVAL_WIPE_FIELDS
            from plugins.PrinterConfig import PrinterConfig, PrinterConfigStore
        finally:
            _started.__exit__(None, None, None)
except ImportError:
    QT_AVAILABLE = False
    runtime = None


class _FakeSignal:
    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self, *args):
        for handler in list(self._handlers):
            handler(*args)


class _FakeRegistry:
    def __init__(self):
        self.containerRemoved = _FakeSignal()
        self.known = []

    def findContainerStacksMetadata(self, **kwargs):
        return self.known


class _FakeContainer:
    def __init__(self, container_id, type_name):
        self._id = container_id
        self._type = type_name

    def getId(self):
        return self._id

    def getMetaData(self):
        return {"type": self._type}


class _FakePersistence:
    def __init__(self):
        self.records = {}
        self.writes = []

    def get_machine(self, machine_id):
        entry = self.records.get(machine_id)
        return dict(entry) if isinstance(entry, dict) else None

    def set_machine(self, machine_id, patch):
        self.writes.append((machine_id, dict(patch)))
        self.records.setdefault(machine_id, {}).update(patch)
        return True

    def set_machine_config(self, machine_id, config):
        return self.set_machine(machine_id, {"url": config.url})

    def migration_record(self):
        return None

    def set_migration_record(self, update):
        return True


class _FakeClient:
    def __init__(self):
        self.stops = 0

    def stop(self, reset_session=False):
        self.stops += 1

    def set_trace_http(self, value):
        pass

    def configure(self, *args, **kwargs):
        pass

    def start(self):
        pass


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class RemovalHookTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.prefs = Preferences({})
        self.app = self.qt.Application(self.prefs)
        self.app.stack = self.qt.Machine("B")
        self.registry = _FakeRegistry()
        self.app.getContainerRegistry = lambda: self.registry
        self.persistence = _FakePersistence()
        self.client = _FakeClient()
        self.binding = PrinterBinding(
            self.app, self.client, self.persistence,
            cura_cfg_path="/nonexistent/cura.cfg", old_state_path=None,
        )
        # The wipe defers via singleShot(0): run it inline so the
        # assertions see the write (the deferral itself is the E8
        # sequencing contract, not the target).
        patcher = patch.object(QTimer, "singleShot", lambda *args: args[1]() if callable(args[1]) else None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seed(self, machine_id, url):
        self.persistence.records[machine_id] = {"url": url, "api_key": "secret",
                                                "camera_url": "http://cam", "follow_mode": "exact"}

    def test_non_machine_containers_are_ignored(self):
        self._seed("A", "http://a:7125")
        self.registry.containerRemoved.emit(_FakeContainer("A", "quality_changes"))
        self.assertEqual(self.persistence.writes, [])

    def test_unknown_machines_are_ignored(self):
        self.registry.containerRemoved.emit(_FakeContainer("NOPE", "machine"))
        self.assertEqual(self.persistence.writes, [])

    def test_the_rename_emission_is_filtered_by_the_registry(self):
        # renameContainer emits containerRemoved too: the registry
        # still knows the id, so nothing is wiped (E8/B2).
        self._seed("A", "http://a:7125")
        self.registry.known = [{"id": "A"}]
        self.registry.containerRemoved.emit(_FakeContainer("A", "machine"))
        self.assertEqual(self.persistence.writes, [])

    def test_the_wipe_targets_the_removed_machine_not_the_active_one(self):
        # The E1 trap: removeMachine activates a replacement FIRST, so
        # an active-identity default blanks the wrong printer. Here
        # the ACTIVE machine is B; A is removed — A's credentials
        # wipe, B's record is untouched.
        self._seed("A", "http://a:7125")
        self._seed("B", "http://b:7125")
        self.registry.containerRemoved.emit(_FakeContainer("A", "machine"))
        self.assertEqual(len(self.persistence.writes), 1)
        machine_id, patch = self.persistence.writes[0]
        self.assertEqual(machine_id, "A")
        for field in _REMOVAL_WIPE_FIELDS:
            self.assertIn(field, patch)
        self.assertEqual(patch["url"], "http://")
        self.assertIn("removed_at", patch)
        # The harmless rest survives for a same-named re-add.
        self.assertEqual(self.persistence.records["A"]["follow_mode"], "exact")
        self.assertEqual(self.persistence.records["B"]["api_key"], "secret")

    def test_removing_the_active_machine_stops_the_live_session(self):
        self._seed("B", "http://b:7125")
        self.registry.containerRemoved.emit(_FakeContainer("B", "machine"))
        self.assertEqual(self.client.stops, 1)
        self.assertEqual(self.persistence.writes[0][0], "B")

    def test_the_legacy_chain_skips_once_the_migration_record_exists(self):
        # The clean reset the migrated flags: a re-run of the legacy
        # chain would resurrect the blob into cura.cfg. The record
        # guards it.
        self.persistence.migration_record = lambda: {"status": "ok"}
        with patch.object(type(self.binding._store), "migrate_legacy_to_current_machine",
                          side_effect=AssertionError("the legacy chain must not run post-migration")), \
             patch.object(type(self.binding._store), "migrate_moonraker_connection",
                          side_effect=AssertionError("the legacy chain must not run post-migration")):
            self.binding._migrate()

    def test_apply_mirrors_the_legacy_toggles_for_the_leak_probe(self):
        # E6: the leak instrument reads the legacy flat keys; the new
        # settings write preserves the mirror.
        self.persistence.records["B"] = {"url": "http://b:7125"}
        config = PrinterConfig(url="http://b:7125", memory_diagnostics_log=True,
                               memory_diagnostics_trace=True)
        self.binding.apply(config)
        self.assertTrue(self.prefs.getValue(PrinterConfigStore.LEGACY_MAP["memory_diagnostics_log"]))
        self.assertTrue(self.prefs.getValue(PrinterConfigStore.LEGACY_MAP["memory_diagnostics_trace"]))
        self.assertTrue(self.prefs.getValue(PrinterConfigStore.LEGACY_MAP["enabled"]))


if __name__ == "__main__":
    unittest.main()
