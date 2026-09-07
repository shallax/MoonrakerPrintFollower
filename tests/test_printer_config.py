import unittest

from plugins.PrinterConfig import PrinterConfig, PrinterConfigStore


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
            "camera_rotation": "45",
            "camera_mirror": "yes",
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
        self.assertEqual(cfg.camera_rotation, 0)
        self.assertTrue(cfg.camera_mirror)

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

    def test_store_has_no_legacy_migration_api(self):
        self.assertFalse(hasattr(PrinterConfigStore, "LEGACY_MAP"))
        self.assertFalse(hasattr(PrinterConfigStore, "MOONRAKER_CONNECTION_PREF_KEY"))
        self.assertFalse(hasattr(PrinterConfigStore, "migrate_legacy_to_current_machine"))
        self.assertFalse(hasattr(PrinterConfigStore, "migrate_moonraker_connection"))


if __name__ == "__main__":
    unittest.main()
