import json
import pathlib
import unittest

from plugins.PrinterConfig import PrinterConfig, PrinterConfigStore, normalise_url

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins"


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
            "trace_layer": False,
            "trace_http": False,
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

    def test_legacy_migration_defers_when_cura_machine_is_unknown(self):
        prefs = FakePreferences()
        prefs.values[PrinterConfigStore.LEGACY_MAP["url"]] = "http://legacy.example.invalid:7125"
        active = ["unknown", "Unknown Cura printer"]
        store = PrinterConfigStore(prefs, lambda: tuple(active))

        self.assertFalse(store.migrate_legacy_to_current_machine())
        self.assertFalse(store._truthy(prefs.values[PrinterConfigStore.MIGRATED_KEY]))

        active[:] = ["machine-a", "Printer A"]
        self.assertTrue(store.migrate_legacy_to_current_machine())
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
                "camera_url": "/webcam/?action=stream",
                "camera_image_rotation": "270",
                "camera_image_mirror": True,
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
        self.assertEqual(cfg_a.camera_url, "/webcam/?action=stream")
        self.assertEqual(cfg_a.camera_rotation, 270)
        self.assertTrue(cfg_a.camera_mirror)

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

    def test_path_smoothing_defaults_on_and_round_trips(self):
        self.assertTrue(PrinterConfig().path_smoothing)
        cfg = PrinterConfig.from_dict({"path_smoothing": False})
        self.assertFalse(cfg.path_smoothing)

        prefs = FakePreferences()
        store = PrinterConfigStore(prefs, lambda: ("machine-a", "Printer A"))
        store.set(cfg)
        self.assertFalse(store.get().path_smoothing)

    def test_toolhead_indicator_defaults_on_and_round_trips(self):
        self.assertTrue(PrinterConfig().show_toolhead_indicator)
        cfg = PrinterConfig.from_dict({"show_toolhead_indicator": False})
        self.assertFalse(cfg.show_toolhead_indicator)

        prefs = FakePreferences()
        store = PrinterConfigStore(prefs, lambda: ("machine-a", "Printer A"))
        store.set(cfg)
        self.assertFalse(store.get().show_toolhead_indicator)

    def test_camera_selection_round_trips_per_printer(self):
        active = ["printer-a", "Printer A"]
        store = PrinterConfigStore(FakePreferences(), lambda: tuple(active))
        store.set(PrinterConfig(camera_selected="bed-camera"))
        self.assertEqual(store.get().camera_selected, "bed-camera")
        active[:] = ["printer-b", "Printer B"]
        self.assertEqual(store.get().camera_selected, "")

    def test_diagnostics_settings_save_and_list_in_a_tab(self):
        config = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text()
        self.assertIn('text: "Diagnostics"', config)
        self.assertIn('"trace_layer": layerTraceBox.checked', config)
        self.assertIn('"trace_http": httpTraceBox.checked', config)
        action = (PLUGINS / "MoonrakerFollowerMachineAction.py").read_text()
        self.assertIn('"trace_layer": bool(raw.get("trace_layer", False))', action)
        self.assertIn('"trace_http": bool(raw.get("trace_http", False))', action)

    def test_settings_tab_lists_diagnostic_traces(self):
        config = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text()
        self.assertIn('text: "Log layer resolution (diagnostics)"', config)
        self.assertIn('text: "Log HTTP requests (diagnostics)"', config)

    def test_settings_tab_lists_upload(self):
        config = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text()
        self.assertIn('text: "Upload"', config)
        self.assertIn('text: "Upload format"', config)

    def test_diagnostics_tab_carries_the_cache_clear(self):
        config = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text()
        self.assertIn('text: "Clear cached downloads and indexes"', config)
        self.assertIn("manager.clearCache()", config)
        self.assertIn("manager.cacheStatus", config)
        self.assertIn('text: "Log layer resolution (diagnostics)"', config)
        action = (PLUGINS / "MoonrakerFollowerMachineAction.py").read_text()
        self.assertIn("def clearCache(self)", action)
        self.assertIn('shutil.rmtree(self._cache_root(), ignore_errors=True)', action)
        self.assertIn('"Moonraker_Print_Follower"', action)

    def test_normalise_url_is_the_single_url_rule(self):
        self.assertEqual(normalise_url(""), "http://")
        self.assertEqual(normalise_url(None), "http://")
        self.assertEqual(normalise_url(" 192.168.1.5 "), "http://192.168.1.5")
        self.assertEqual(normalise_url("https://printer/"), "https://printer")
        self.assertEqual(normalise_url("HTTP://Printer:7125//"), "HTTP://Printer:7125")
        for scheme_only in ("http:", "https:", "http://", "https://", "HTTP://"):
            self.assertEqual(normalise_url(scheme_only), "http://")

    def test_normalise_url_strips_userinfo_and_control_characters(self):
        # Panel security P3: Qt logs the full request URL on errors, so
        # embedded credentials would leak into Cura's log; control
        # characters never belong in a host.
        self.assertEqual(normalise_url("http://user:pass@printer.lan"), "http://printer.lan")
        self.assertEqual(normalise_url("https://user@printer.lan:7125/"), "https://printer.lan:7125")
        self.assertEqual(normalise_url("http://printer.l\x00an"), "http://")
        self.assertEqual(normalise_url("http://printer.l\nan"), "http://")

    def test_upload_paths_refuse_traversal_segments_at_config_time(self):
        # Panel security P3: the dialog applies UploadController.valid_path,
        # but a hand-edited or migrated config must not carry ".." to the
        # upload API either.
        from plugins.PrinterConfig import upload_path_safe
        self.assertEqual(upload_path_safe("PLA"), "PLA")
        self.assertEqual(upload_path_safe("PLA/parts"), "PLA/parts")
        self.assertEqual(upload_path_safe("<root>"), "")
        self.assertEqual(upload_path_safe("../gcodes"), "")
        self.assertEqual(upload_path_safe("PLA/../gcodes"), "")
        self.assertEqual(upload_path_safe(".hidden"), "")
        config = PrinterConfig.from_dict({"upload_path": "../gcodes",
                                          "upload_paths": ["PLA", "../steal"]})
        self.assertEqual(config.upload_path, "")
        self.assertEqual(config.upload_paths, ["PLA"])

    def test_from_dict_normalises_url(self):
        self.assertEqual(PrinterConfig.from_dict({"url": "printer.lan"}).url, "http://printer.lan")
        self.assertEqual(PrinterConfig.from_dict({"url": "https://printer.lan/"}).url, "https://printer.lan")
        self.assertEqual(PrinterConfig.from_dict({}).url, "http://")

    def test_from_dict_rejects_nonfinite_or_out_of_range_tolerance(self):
        self.assertEqual(PrinterConfig.from_dict({"z_tolerance": float("nan")}).z_tolerance, 0.04)
        self.assertEqual(PrinterConfig.from_dict({"z_tolerance": float("inf")}).z_tolerance, 0.04)
        self.assertEqual(PrinterConfig.from_dict({"z_tolerance": 0.0}).z_tolerance, 0.04)
        self.assertEqual(PrinterConfig.from_dict({"z_tolerance": 9.0}).z_tolerance, 0.04)
        self.assertEqual(PrinterConfig.from_dict({"z_tolerance": 0.1}).z_tolerance, 0.1)

    def test_from_dict_caps_poll_interval(self):
        self.assertEqual(PrinterConfig.from_dict({"poll_interval_ms": 10 ** 20}).poll_interval_ms, 3_600_000)
        self.assertEqual(PrinterConfig.from_dict({"poll_interval_ms": 0}).poll_interval_ms, 1)


if __name__ == "__main__":
    unittest.main()
