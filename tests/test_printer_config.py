import json
import os
import sys
import unittest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from PrinterConfig import PrinterConfig, PrinterConfigStore


class FakePreferences:
    def __init__(self):
        self.values = {}

    def addPreference(self, key, default):
        self.values.setdefault(key, default)

    def getValue(self, key):
        return self.values.get(key)

    def setValue(self, key, value):
        self.values[key] = value


class PrinterConfigTests(unittest.TestCase):
    def test_configs_are_isolated_per_cura_machine(self):
        prefs = FakePreferences()
        active = ["machine-a", "Printer A"]
        store = PrinterConfigStore(prefs, lambda: tuple(active))
        store.set(PrinterConfig(
            enabled=True,
            url="http://printer-a.example.invalid:7125",
            follow_mode="window",
            upload_path="projects/a",
            power_devices="printer, lights",
        ))

        active[:] = ["machine-b", "Printer B"]
        self.assertFalse(store.get().enabled)
        self.assertEqual(store.get().url, "http://")
        self.assertEqual(store.get().upload_path, "")
        store.set(PrinterConfig(
            enabled=True,
            url="http://printer-b.example.invalid:7125",
            follow_mode="completed",
            upload_start_print=True,
        ))

        active[:] = ["machine-a", "Printer A"]
        self.assertEqual(store.get().url, "http://printer-a.example.invalid:7125")
        self.assertEqual(store.get().follow_mode, "window")
        self.assertEqual(store.get().upload_path, "projects/a")
        self.assertEqual(store.get().power_devices, "printer, lights")
        self.assertFalse(store.get().upload_start_print)

    def test_legacy_follower_settings_migrate_once_to_active_machine(self):
        prefs = FakePreferences()
        defaults = {
            "enabled": True,
            "url": "http://legacy.example.invalid:7125",
            "api_key": "",
            "poll_interval_ms": 1234,
            "moonraker_layer_is_one_based": False,
            "auto_preview": True,
            "z_fallback": False,
            "z_tolerance": 0.08,
            "path_follow": False,
        }
        for field, key in PrinterConfigStore.LEGACY_MAP.items():
            prefs.values[key] = defaults[field]

        active = ["machine-a", "Machine A"]
        store = PrinterConfigStore(prefs, lambda: tuple(active))
        self.assertTrue(store.migrate_legacy_to_current_machine())
        migrated = store.get()
        self.assertTrue(migrated.enabled)
        self.assertEqual(migrated.url, "http://legacy.example.invalid:7125")
        self.assertEqual(migrated.poll_interval_ms, 1234)
        self.assertFalse(migrated.path_follow)

        prefs.values[PrinterConfigStore.LEGACY_MAP["url"]] = "http://changed.example.invalid:7125"
        active[:] = ["machine-b", "Machine B"]
        self.assertFalse(store.migrate_legacy_to_current_machine())
        self.assertEqual(store.get().url, "http://")
        active[:] = ["machine-a", "Machine A"]
        self.assertEqual(store.get().url, "http://legacy.example.invalid:7125")

    def test_standalone_moonraker_connection_settings_migrate_for_all_printers(self):
        prefs = FakePreferences()
        prefs.values[PrinterConfigStore.MOONRAKER_CONNECTION_PREF_KEY] = json.dumps({
            "machine-a": {
                "url": "http://old-a.example.invalid:7125/",
                "api_key": "",
                "frontend_url": "https://ui-a.example.invalid/",
                "output_format": "ufp",
                "upload_dialog": False,
                "upload_path": "/jobs/a/",
                "upload_pathes": ["jobs/a", "/archive/a/"],
                "upload_start_print_job": True,
                "upload_remember_state": True,
                "upload_autohide_messagebox": True,
                "power_device": "printer, lights",
                "retry_interval": "1.25",
                "trans_input": " _",
                "trans_output": "--",
                "trans_remove": "[]",
            },
            "machine-b": {
                "url": "http://old-b.example.invalid:7125/",
                "upload_path": "jobs/b",
            },
        })
        active = ["machine-a", "Printer A"]
        store = PrinterConfigStore(prefs, lambda: tuple(active))

        self.assertEqual(store.migrate_moonraker_connection(), 2)
        cfg_a = store.get("machine-a")
        self.assertEqual(cfg_a.url, "http://old-a.example.invalid:7125")
        self.assertEqual(cfg_a.frontend_url, "https://ui-a.example.invalid/")
        self.assertEqual(cfg_a.output_format, "ufp")
        self.assertFalse(cfg_a.upload_dialog)
        self.assertEqual(cfg_a.upload_path, "jobs/a")
        self.assertEqual(cfg_a.upload_paths, ["jobs/a", "archive/a"])
        self.assertTrue(cfg_a.upload_start_print)
        self.assertTrue(cfg_a.upload_remember_state)
        self.assertTrue(cfg_a.upload_autohide_message)
        self.assertEqual(cfg_a.power_devices, "printer, lights")
        self.assertEqual(cfg_a.ready_retry_interval_s, 1.25)
        self.assertEqual(cfg_a.filename_translate_input, " _")
        self.assertEqual(cfg_a.filename_translate_output, "--")
        self.assertEqual(cfg_a.filename_translate_remove, "[]")

        cfg_b = store.get("machine-b")
        self.assertEqual(cfg_b.url, "http://old-b.example.invalid:7125")
        self.assertEqual(cfg_b.upload_path, "jobs/b")

        # Migration is deliberately one-shot and leaves the old preference data
        # untouched so rollback to the standalone plugin remains possible.
        self.assertEqual(store.migrate_moonraker_connection(), 0)
        self.assertIn("machine-a", json.loads(prefs.values[PrinterConfigStore.MOONRAKER_CONNECTION_PREF_KEY]))

    def test_existing_follower_connection_wins_during_standalone_migration(self):
        prefs = FakePreferences()
        store = PrinterConfigStore(prefs, lambda: ("machine-a", "Printer A"))
        store.set(PrinterConfig(
            url="https://new.example.invalid",
            api_key="",
            follow_mode="window",
        ), "machine-a")
        prefs.values[PrinterConfigStore.MOONRAKER_CONNECTION_PREF_KEY] = json.dumps({
            "machine-a": {
                "url": "http://old.example.invalid:7125/",
                "api_key": "",
                "upload_start_print_job": True,
            }
        })

        self.assertEqual(store.migrate_moonraker_connection(), 1)
        cfg = store.get("machine-a")
        self.assertEqual(cfg.url, "https://new.example.invalid")
        self.assertEqual(cfg.follow_mode, "window")
        self.assertTrue(cfg.upload_start_print)

    def test_invalid_values_are_normalised(self):
        cfg = PrinterConfig.from_dict({
            "poll_interval_ms": "bad",
            "z_tolerance": "bad",
            "ready_retry_interval_s": "bad",
            "enabled": "yes",
            "upload_dialog": "off",
            "follow_mode": "teleport",
            "output_format": "stl",
            "upload_path": "/folder/sub/",
            "upload_paths": ["/one/", "", " two/"],
        })
        self.assertEqual(cfg.poll_interval_ms, 750)
        self.assertEqual(cfg.z_tolerance, 0.04)
        self.assertEqual(cfg.ready_retry_interval_s, 0.5)
        self.assertTrue(cfg.enabled)
        self.assertFalse(cfg.upload_dialog)
        self.assertEqual(cfg.follow_mode, "exact")
        self.assertEqual(cfg.output_format, "gcode")
        self.assertEqual(cfg.upload_path, "folder/sub")
        self.assertEqual(cfg.upload_paths, ["one", "two"])

    def test_retry_interval_is_bounded(self):
        self.assertEqual(PrinterConfig.from_dict({"ready_retry_interval_s": 0}).ready_retry_interval_s, 0.1)
        self.assertEqual(PrinterConfig.from_dict({"ready_retry_interval_s": 999}).ready_retry_interval_s, 60.0)

    def test_toolhead_indicator_defaults_on_and_round_trips(self):
        self.assertTrue(PrinterConfig().show_toolhead_indicator)
        cfg = PrinterConfig.from_dict({"show_toolhead_indicator": False})
        self.assertFalse(cfg.show_toolhead_indicator)

        prefs = FakePreferences()
        store = PrinterConfigStore(prefs, lambda: ("machine-a", "Printer A"))
        store.set(cfg)
        self.assertFalse(store.get().show_toolhead_indicator)


if __name__ == "__main__":
    unittest.main()
