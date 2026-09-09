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

    def test_geometry_heights_resolve_mid_layer_positions(self):
        # The match tolerance is tighter than the layer thickness, so
        # most of a layer matches NO layer-start height; the layer is
        # the last start below the nozzle.
        config = PrinterConfig()
        resolver = LayerResolver()
        heights = (0.2, 0.4, 0.6)
        def observe(z, e):
            return resolver.resolve({"gcode_move": {"gcode_position": [0, 0, z, e]}}, config, heights=heights)
        self.assertIsNone(observe(0.2, 1).index)
        self.assertEqual(observe(0.2, 2).index, 0)
        self.assertEqual(observe(0.35, 3).index, 0)  # mid-layer
        self.assertEqual(observe(0.46, 4).index, 1)

    def test_first_z_increment_seeds_a_provisional_layer_immediately(self):
        # The author attaches mid-print and cannot wait for a full
        # layer (some layers are huge): the FIRST Z increment seeds a
        # layer immediately; a z-hop misread self-heals on its descent.
        config = PrinterConfig()
        resolver = LayerResolver()
        status = {"virtual_sdcard": {"progress": 0.5}}
        def observe(z, e):
            status["gcode_move"] = {"gcode_position": [0, 0, z, e]}
            return resolver.resolve(status, config, metadata={})
        self.assertIsNone(observe(9.8, 1.0).index)
        self.assertEqual(observe(9.9, 2.0).index, 98)  # (9.9 - 0.1) / 0.1
        self.assertIsNone(observe(9.8, 3.0).index)  # the z-hop's return leg cancels it
        self.assertEqual(observe(9.9, 4.0).index, 98)

    def test_mid_layer_attach_seeds_the_floor_not_the_round_up(self):
        # Panel DOM-P1: round() read the layer one AHEAD from ~60%
        # through the layer and the one-at-a-time extrapolation could
        # never pull it back. The seed must use floor semantics.
        config = PrinterConfig()
        resolver = LayerResolver()
        status = {"virtual_sdcard": {"progress": 0.5}}
        metadata = {"first_layer_height": 0.24, "layer_height": 0.1}
        def observe(z, e):
            status["gcode_move"] = {"gcode_position": [0, 0, z, e]}
            return resolver.resolve(status, config, metadata=metadata)
        self.assertIsNone(observe(0.69, 1.0).index)
        # 0.79 sits inside layer 5's span; round() would claim 6.
        self.assertEqual(observe(0.79, 2.0).index, 5)

    def test_provisional_seed_uses_metadata_first_and_step(self):
        # Panel ARCH-P2-1: the provisional seed used the measured
        # ascent as BOTH thickness and subtractor while the canonical
        # branch one screen below uses (z - first)/step — they disagree
        # exactly on 150%-first-layer profiles. The seed now prefers
        # the slicer header.
        config = PrinterConfig()
        resolver = LayerResolver()
        status = {"virtual_sdcard": {"progress": 0.5}}
        metadata = {"first_layer_height": 0.3, "layer_height": 0.2}
        def observe(z, e):
            status["gcode_move"] = {"gcode_position": [0, 0, z, e]}
            return resolver.resolve(status, config, metadata=metadata)
        self.assertIsNone(observe(0.6, 1.0).index)
        # (0.7 - 0.3) / 0.2 = 2; the old ascent-based seed claimed 6.
        self.assertEqual(observe(0.7, 2.0).index, 2)

    def test_continuous_rise_without_a_step_retires_the_seed(self):
        # Panel DOM-P2-1 (the seed-guard half): a vase-mode rise never
        # plateaus, so the measured-ascent seed would keep an absurd
        # layer number forever. Six consecutive ascent observations
        # without a plateau retire the guess — "—" is honest; an
        # inflated number is worse.
        config = PrinterConfig()
        resolver = LayerResolver()
        status = {"virtual_sdcard": {"progress": 0.5}}
        def observe(z, e):
            status["gcode_move"] = {"gcode_position": [0, 0, z, e]}
            return resolver.resolve(status, config, metadata={})
        self.assertIsNone(observe(10.0, 1.0).index)
        # The first rise seeds from the fragment ascent (absurd)…
        seeded = observe(10.03125, 2.0)
        self.assertIsNotNone(seeded.index)
        # …but the continuing rise disproves it and retires the guess.
        for step in range(3, 8):
            observe(10.0 + 0.03125 * step, float(step))
        self.assertIsNone(observe(10.0 + 0.03125 * 8, 8.0).index)
        # The retirement suppresses re-seeding while the rise continues.
        self.assertIsNone(observe(10.0 + 0.03125 * 9, 9.0).index)

    def test_continuous_rise_with_metadata_tracks_the_rise(self):
        # Vase mode WITH a slicer header: the seed uses the header step
        # from the start, and the rise-disproof re-anchors on it instead
        # of retiring — the readout tracks the continuous rise.
        config = PrinterConfig()
        resolver = LayerResolver()
        status = {"virtual_sdcard": {"progress": 0.5}}
        metadata = {"first_layer_height": 0.2, "layer_height": 0.2}
        def observe(z, e):
            status["gcode_move"] = {"gcode_position": [0, 0, z, e]}
            return resolver.resolve(status, config, metadata=metadata)
        self.assertIsNone(observe(10.0, 1.0).index)
        last = None
        for step in range(2, 8):
            last = observe(10.0 + 0.08 * step, float(step))
        self.assertEqual(last.index, 51)

    def test_measured_step_anchors_height_and_thickness_for_the_bar(self):
        # The layer progress bar needs the layer's start height and
        # thickness even for never-downloaded prints: the measured step
        # must feed the height/thickness anchors.
        config = SimpleNamespace(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.05)
        resolver = LayerResolver()
        status = {"print_stats": {"state": "printing", "info": {"current_layer": 0, "total_layer": 12}},
                  "virtual_sdcard": {"progress": 0.5}}
        for z, e in ((0.3, 1.0), (0.3, 2.0), (0.5, 3.0), (0.5, 4.0), (0.7, 5.0), (0.7, 6.0)):
            status["gcode_move"] = {"gcode_position": [100, 100, z, e]}
            resolver.resolve(status, config, metadata={})
        layer = resolver.resolve(status, config, metadata={})
        self.assertEqual(layer.index, 2)
        self.assertAlmostEqual(layer.height, 0.6, places=6)   # 0.2 first + 2 x 0.2 step
        self.assertAlmostEqual(layer.thickness, 0.2, places=6)

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
