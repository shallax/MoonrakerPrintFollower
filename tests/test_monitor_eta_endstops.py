"""Executable monitor eta endstops contracts."""
from tests import monitor_test_support as harness

class EndstopAndEtaBasisTests(harness.EndstopAndEtaBasisTests):
    def test_endstop_values_projects_axes_and_the_not_homed_state(self):
        from plugins.MonitorFormatting import endstop_values
        snapshot = harness.SimpleNamespace(endstops={"x": "TRIGGERED", "y": "open", "z": "open"})
        items, summary = endstop_values(snapshot)["endstopItems"], endstop_values(snapshot)["endstopSummary"]
        self.assertEqual([(item["name"], item["state"], item["triggered"]) for item in items],
                         [("X", "TRIGGERED", True), ("Y", "open", False), ("Z", "open", False)])
        self.assertEqual(summary, "")
        empty = harness.SimpleNamespace(endstops={})
        self.assertEqual(endstop_values(empty)["endstopItems"], [])
        self.assertIn("No endstop states reported", endstop_values(empty)["endstopSummary"])

    def test_core_values_prefers_the_layer_anchored_eta_and_names_the_basis(self):
        from plugins.MonitorFormatting import core_values
        snapshot = harness.SimpleNamespace(core={"print_stats": {"state": "printing", "print_duration": 30},
                                        "virtual_sdcard": {"progress": 0.1}},
                                   auxiliary={}, server={})
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=1, total=20, thickness=None),
                                   estimated_time=600.0, metadata_complete=True, layer_eta=420.0)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorEtaBasis"], "index")
        self.assertEqual(values["monitorEta"], "00:07:00")
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=1, total=20, thickness=None),
                                   estimated_time=600.0, metadata_complete=True, layer_eta=None)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorEtaBasis"], "blend")

    def test_core_values_reports_filament_used_and_remaining(self):
        from plugins.MonitorFormatting import core_values
        # Real Klipper shape (Status_Reference + klippy/print_stats.py):
        # filament_used is a TOP-LEVEL print_stats field, always present
        # while printing; the info dict only ever carries the layer
        # counters a slicer's SET_PRINT_STATS_INFO wrote. The slicer
        # total rides the COORDINATOR snapshot (the physical arg); the
        # monitor snapshot has no such field.
        snapshot = harness.SimpleNamespace(core={"print_stats": {"state": "printing", "print_duration": 30,
                                                         "filament_used": 3500.0,
                                                         "info": {"current_layer": 1, "total_layer": 20}},
                                        "virtual_sdcard": {}},
                                   auxiliary={}, server={})
        physical = harness.SimpleNamespace(layer=harness.SimpleNamespace(index=1, total=20, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=None,
                                   filament_total=42000.0)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "3.50 m")
        self.assertEqual(values["filamentRemaining"], "38.50 m")
        # Without the metadata total the remaining length is honest "—".
        physical.filament_total = None
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentRemaining"], "—")
        # Without the polled used length both read "—".
        snapshot.core = {"print_stats": {"state": "printing"}}
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "—")
        # The legacy shapes still parse (info-dict used, monitor-
        # snapshot total) — they are fallbacks, not the live path.
        snapshot.core = {"print_stats": {"state": "printing",
                                         "info": {"filament_used": 1200.0}}}
        snapshot.filament_total = 42000.0
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "1.20 m")
        self.assertEqual(values["filamentRemaining"], "40.80 m")


