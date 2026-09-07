"""PrintState domain: immutable snapshots and the single LayerResolver."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from plugins.PrintState import LayerResolver, PhysicalLayer, PrintSnapshot
from plugins.PrinterConfig import PrinterConfig
from plugins.RemoteJobService import RemoteJobService


class PrintStateTests(unittest.TestCase):
    def test_physical_snapshot_is_immutable(self):
        snapshot = PrintSnapshot(layer=PhysicalLayer(5, 10))
        with self.assertRaises(FrozenInstanceError): snapshot.layer.index = 4
        with self.assertRaises(FrozenInstanceError): snapshot.job_key = ("x", 1, 1)

    def test_physical_layer_is_not_clamped_by_local_preview_geometry(self):
        resolver = LayerResolver()
        layer = resolver.resolve({"print_stats": {"info": {"current_layer": 100, "total_layer": 200}}}, PrinterConfig(), heights=(0.2, 0.4))
        self.assertEqual(layer.index, 99)
        self.assertEqual(layer.total, 200)

    def test_exact_index_map_and_ranges_precede_z_fallback(self):
        resolver = LayerResolver()
        index = SimpleNamespace(ranges=((0, 10), (10, 20)), current_layer_map={5: 1}, layer_at=lambda p: 0)
        self.assertEqual(resolver.resolve({"print_stats": {"info": {"current_layer": 5}}}, PrinterConfig(), index).index, 1)
        self.assertEqual(resolver.resolve({"virtual_sdcard": {"file_position": 5}}, PrinterConfig(), index).index, 0)

    def test_z_hop_does_not_advance_physical_layer_without_extrusion(self):
        resolver = LayerResolver()
        config = PrinterConfig()
        def observe(z, e):
            return resolver.resolve({"gcode_move": {"gcode_position": [0, 0, z, e]}}, config, heights=(0.2, 0.4, 0.6))
        self.assertIsNone(observe(0.2, 1).index)
        self.assertEqual(observe(0.2, 2).index, 0)
        self.assertEqual(observe(0.6, 2).index, 0)
        self.assertEqual(observe(0.4, 3).index, 1)

    def test_zero_current_layer_in_one_based_mode_is_pre_print_not_layer_zero(self):
        config = PrinterConfig(moonraker_layer_is_one_based=True)
        resolver = LayerResolver()
        # No index, no file position, no geometry: the pre-print report must
        # not resolve to "layer zero in progress".
        layer = resolver.resolve({"print_stats": {"info": {"current_layer": 0, "total_layer": 5}}}, config)
        self.assertIsNone(layer.index)
        # With an index, file position still resolves normally.
        index = SimpleNamespace(ranges=((0, 10), (10, 20)), current_layer_map={}, layer_at=lambda p: 1)
        layer = resolver.resolve({"print_stats": {"info": {"current_layer": 0, "total_layer": 5}},
                                  "virtual_sdcard": {"file_position": 12}}, config, index)
        self.assertEqual(layer.index, 1)

    def test_pause_z_lift_does_not_jump_layer_with_geometry(self):
        config = PrinterConfig()
        resolver = LayerResolver()
        heights = (0.2, 0.4, 0.6, 0.8)
        def observe(z, e):
            return resolver.resolve({"gcode_move": {"gcode_position": [0, 0, z, e]}}, config, heights=heights)
        self.assertIsNone(observe(0.2, 1).index)
        self.assertEqual(observe(0.2, 2).index, 0)
        self.assertEqual(observe(0.4, 3).index, 1)
        # Pause lifts Z to 2.8 while priming; extrusion advances but geometry
        # is off-model. The physical layer must stay put.
        self.assertEqual(observe(2.8, 4).index, 1)
        self.assertEqual(observe(0.6, 5).index, 2)

    def test_metadata_extrapolation_never_jumps_more_than_one_layer(self):
        config = PrinterConfig()
        resolver = LayerResolver()
        metadata = {"layer_height": 0.2, "first_layer_height": 0.2}
        def observe(z, e):
            return resolver.resolve({"gcode_move": {"gcode_position": [0, 0, z, e]}}, config, metadata=metadata)
        self.assertIsNone(observe(0.2, 1).index)
        self.assertEqual(observe(0.2, 2).index, 0)
        # A lifted Z extrapolates to ~13; the resolver advances by at most one.
        self.assertEqual(observe(2.8, 3).index, 1)

    def test_geometry_heights_drive_layer_height_and_thickness(self):
        config = SimpleNamespace(
            moonraker_layer_is_one_based=True,
            z_fallback=False,
            z_tolerance=0.05,
        )
        layer = LayerResolver().resolve(
            {"print_stats": {"info": {"current_layer": 2, "total_layer": 3}}},
            config,
            metadata={"first_layer_height": 0.2, "layer_height": 0.2},
            heights=(0.2, 0.35, 0.55),
        )
        self.assertEqual(layer.index, 1)
        self.assertAlmostEqual(layer.height, 0.35)
        self.assertAlmostEqual(layer.thickness, 0.15)

    def test_layer_thickness_falls_back_to_metadata_without_geometry(self):
        config = SimpleNamespace(
            moonraker_layer_is_one_based=True,
            z_fallback=False,
            z_tolerance=0.05,
        )
        resolver = LayerResolver()
        first = resolver.resolve(
            {"print_stats": {"info": {"current_layer": 1, "total_layer": 5}}},
            config,
            metadata={"first_layer_height": 0.24, "layer_height": 0.1},
        )
        later = resolver.resolve(
            {"print_stats": {"info": {"current_layer": 3, "total_layer": 5}}},
            config,
            metadata={"first_layer_height": 0.24, "layer_height": 0.1},
        )
        self.assertAlmostEqual(first.height, 0.24)
        self.assertAlmostEqual(first.thickness, 0.24)
        self.assertAlmostEqual(later.height, 0.44)
        self.assertAlmostEqual(later.thickness, 0.1)


class JobIdentityTests(unittest.TestCase):
    def test_same_filename_restart_gets_new_print_run_identity(self):
        jobs = RemoteJobService({"printing", "paused"})
        first = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 120},
            {"file_size": 1000, "file_position": 600},
        )
        second = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 180},
            {"file_size": 1000, "file_position": 800},
        )
        restarted = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 3},
            {"file_size": 1000, "file_position": 20},
        )
        self.assertTrue(first.new_job)
        self.assertFalse(second.new_job)
        self.assertTrue(restarted.new_job)
        self.assertNotEqual(first.key, restarted.key)

    def test_transiently_unknown_file_size_does_not_churn_identity(self):
        jobs = RemoteJobService({"printing", "paused"})
        first = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 120},
            {"file_size": 1000, "file_position": 600},
        )
        self.assertTrue(first.new_job)
        # A poll that transiently lacks virtual_sdcard must not start a new
        # run (which would reset Preview, pause schedules and downloads).
        missing = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 125},
            {},
        )
        self.assertFalse(missing.new_job)
        recovered = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 130},
            {"file_size": 1000, "file_position": 640},
        )
        self.assertFalse(recovered.new_job)
        self.assertEqual(recovered.key[0], "part.gcode")


if __name__ == "__main__":
    unittest.main()
