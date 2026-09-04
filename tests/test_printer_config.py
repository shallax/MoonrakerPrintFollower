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
        store.set(PrinterConfig(enabled=True, url="http://printer-a.example.invalid:7125", follow_mode="window"))

        active[:] = ["machine-b", "Printer B"]
        self.assertFalse(store.get().enabled)
        self.assertEqual(store.get().url, "http://")
        store.set(PrinterConfig(enabled=True, url="http://printer-b.example.invalid:7125", follow_mode="completed"))

        active[:] = ["machine-a", "Printer A"]
        self.assertEqual(store.get().url, "http://printer-a.example.invalid:7125")
        self.assertEqual(store.get().follow_mode, "window")

    def test_legacy_settings_migrate_once_to_active_machine(self):
        prefs = FakePreferences()
        for field, key in PrinterConfigStore.LEGACY_MAP.items():
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
            prefs.values[key] = defaults[field]
        active = ["machine-a", "Machine A"]
        store = PrinterConfigStore(prefs, lambda: tuple(active))
        self.assertTrue(store.migrate_legacy_to_current_machine())
        migrated = store.get()
        self.assertTrue(migrated.enabled)
        self.assertEqual(migrated.url, "http://legacy.example.invalid:7125")
        self.assertEqual(migrated.poll_interval_ms, 1234)
        self.assertFalse(migrated.path_follow)

        # Changing old globals cannot overwrite a migrated printer, and a later
        # machine receives a fresh 1.1 config rather than the old global target.
        prefs.values[PrinterConfigStore.LEGACY_MAP["url"]] = "http://changed.example.invalid:7125"
        active[:] = ["machine-b", "Machine B"]
        self.assertFalse(store.migrate_legacy_to_current_machine())
        self.assertEqual(store.get().url, "http://")
        active[:] = ["machine-a", "Machine A"]
        self.assertEqual(store.get().url, "http://legacy.example.invalid:7125")

    def test_invalid_values_are_normalised(self):
        cfg = PrinterConfig.from_dict({
            "poll_interval_ms": "bad",
            "z_tolerance": "bad",
            "enabled": "yes",
            "follow_mode": "teleport",
        })
        self.assertEqual(cfg.poll_interval_ms, 750)
        self.assertEqual(cfg.z_tolerance, 0.04)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.follow_mode, "exact")


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
