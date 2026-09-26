"""Finding B, the Python half: a layer transition must not jump ahead of
the nozzle, and one poll must be one telemetry snapshot.

Klipper executes SET_PRINT_STATS_INFO as it READS the file, and
``virtual_sdcard.file_position`` is a read offset: both run ahead of the
physical nozzle by the move queue's lookahead, so at a layer change the
printer reports the NEXT layer while the nozzle is still finishing this
one. ``motion_report.live_position`` is the only signal that cannot lead.

The plate's boundary is why this matters beyond the readout: the
coordinator anchors it to the resolved layer and asks the index service
to refine that layer's boundary against the nozzle's live position. An
anchor one layer early searches the NEW layer's geometry from the OLD
layer's point in the toolpath, publishes a fraction of a layer with
nothing printed, and the service's monotonic floor then holds it.

Layer selection is here (``PrintState.LayerResolver``); the anchor's
consequence is asserted against the real ``GCodeIndexService`` in the
Qt-gated class below, through the same seams ``test_gcode_index_plate``
uses. The QML half of the finding — world/generation identity in the
compositor, old-layer textures, retained prefixes, the pending grey base
— is out of this file's scope: it needs the real engine and belongs to
the render lane. Its Python analogue, a self-consistent
(anchor, layers, split) delivery when a later frame arrives mid-refresh,
is covered in test_coordinator_toolhead_console_coverage.
"""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from plugins.PrintState import LayerResolver
from plugins.PrinterConfig import PrinterConfig
from plugins.RemoteJobService import RemoteJobService
from tests.qt_runtime_support import QT_AVAILABLE

# The print's geometry: 0.2 mm layers, a first layer of the same height.
HEIGHTS = (0.2, 0.4, 0.6, 0.8, 1.0, 1.2)
METADATA = {"layer_height": 0.2, "first_layer_height": 0.2, "layer_count": 6}


def transition_status(*, claimed, live_z, commanded_z, position=600, state="printing"):
    """The status at a layer change: the parser has read the layer-change
    marker and the byte offset that follows it, while the nozzle's own
    position is still on the layer being finished. ``commanded_z`` is
    gcode_move's, which the parser updates as it reads — it leads too,
    which is why the resolver may not trust it either."""
    return {
        "print_stats": {"state": state, "filename": "part.gcode",
                        "info": {"current_layer": claimed, "total_layer": len(HEIGHTS)}},
        "virtual_sdcard": {"file_position": position, "file_size": 100000, "progress": 0.4},
        "gcode_move": {"gcode_position": [5.0, 5.0, commanded_z, 12.0]},
        "motion_report": {"live_position": [5.0, 5.0, live_z, 12.0]},
    }


def nozzle(z):
    return (5.0, 5.0, z)


class LayerClaimHeldAtTheNozzleTests(unittest.TestCase):
    """A parser claim is held at the layer the nozzle has reached."""

    def setUp(self):
        self.resolver = LayerResolver()
        self.config = PrinterConfig()

    def resolve(self, status, **kwargs):
        return self.resolver.resolve(status, self.config, metadata=METADATA,
                                     heights=HEIGHTS, **kwargs)

    def test_the_transition_claim_does_not_advance_before_the_nozzle(self):
        # The live transition: layer 0 is being finished, the parser has
        # read layer 1's marker (current_layer=2, one-based) and the byte
        # offset past it. Every parser-side signal says layer 1; the
        # nozzle is still on layer 0. The nozzle wins.
        status = transition_status(claimed=2, live_z=0.2, commanded_z=0.4)
        layer = self.resolve(status, nozzle=nozzle(0.2))
        self.assertEqual(layer.index, 0, "resolved %r from %r" % (layer.index, layer.source))
        # The Z estimate cannot have answered here: extrusion is flat, and
        # the extrusion-guarded branch is skipped while a claim exists.
        self.assertNotEqual(layer.source, "extrusion-guarded Z height")

    def test_the_commanded_z_is_not_evidence_of_physical_progress(self):
        # gcode_move.gcode_position is the parser's own leak — it is
        # already at the new layer's plane. Had the hold read it, it would
        # agree with the claim and change nothing at all.
        status = transition_status(claimed=2, live_z=0.2, commanded_z=0.4)
        self.assertEqual(self.resolve(status, nozzle=nozzle(0.2)).index, 0)
        # The same poll with no live position corroborates nothing, and
        # keeps the claim: an unverifiable frame degrades to the old
        # behaviour instead of inventing a hold.
        self.assertEqual(self.resolve(status, nozzle=None).index, 1)

    def test_the_advance_follows_the_nozzle_and_leaves_no_lag(self):
        # The nozzle physically rises to the new layer's plane: the same
        # claim now resolves. No timer, no extra poll, no per-layer latch.
        status = transition_status(claimed=2, live_z=0.2, commanded_z=0.4)
        self.assertEqual(self.resolve(status, nozzle=nozzle(0.2)).index, 0)
        self.assertEqual(self.resolve(status, nozzle=nozzle(0.4)).index, 1)
        # The height and thickness readouts follow the resolved layer.
        arrived = self.resolve(status, nozzle=nozzle(0.4))
        self.assertAlmostEqual(arrived.height, 0.4)
        self.assertAlmostEqual(arrived.thickness, 0.2)

    def test_a_settled_layer_is_never_held_low(self):
        # Half a layer of systematic drift (a raft, a squished first
        # layer) must not read one layer low: a layer's Z is commanded
        # once, at its start, so the nozzle holds that height for the
        # layer's whole life. A floor rule without the half-span would
        # hold layer 1's claim at layer 0 for the entire print.
        for offset in (0.0, 0.09, -0.09):
            with self.subTest(offset=offset):
                resolver = LayerResolver()
                status = transition_status(claimed=3, live_z=0.4 + offset, commanded_z=0.6)
                self.assertEqual(
                    resolver.resolve(status, PrinterConfig(), metadata=METADATA,
                                     heights=HEIGHTS, nozzle=nozzle(0.4 + offset)).index,
                    1)

    def test_a_pause_lift_above_the_model_is_not_a_layer(self):
        # A nozzle parked above the print is not evidence about a layer:
        # the heights cannot answer, so the claim stands.
        status = transition_status(claimed=3, live_z=40.0, commanded_z=40.0)
        self.assertEqual(self.resolve(status, nozzle=nozzle(40.0)).index, 2)

    def test_a_height_table_from_another_file_does_not_hold(self):
        # Past the skew bound the height table is the suspect one (the
        # Cura scene holds a different file), so the claim keeps it rather
        # than dragging the readout down to another print's layer.
        status = transition_status(claimed=6, live_z=0.2, commanded_z=1.0)
        self.assertEqual(self.resolve(status, nozzle=nozzle(0.2)).index, 5)

    def test_a_missing_live_position_keeps_the_parser_claim(self):
        # The reviewer's coarse-only poll: no live telemetry at all. The
        # resolver cannot corroborate, and the index service is then
        # handed no live position either — so with a claim already
        # advanced, the split falls back to the parser's own byte
        # fraction. See PlateAnchorAtTheTransitionTests.
        status = transition_status(claimed=2, live_z=0.2, commanded_z=0.4)
        status.pop("motion_report")
        self.assertEqual(self.resolve(status, nozzle=None).index, 1)

    def test_the_nozzle_never_advances_the_layer_beyond_the_claim(self):
        # The hold lowers a claim; it never raises one. A claim at or
        # behind the nozzle passes through untouched, and a nozzle far
        # ahead of the claim does not pull the readout forward — no
        # speculative advance, and none to lock into the plate's floor.
        behind = transition_status(claimed=1, live_z=0.4, commanded_z=0.4)
        self.assertEqual(self.resolve(behind, nozzle=nozzle(0.4)).index, 0)
        self.assertEqual(self.resolve(behind, nozzle=None).index, 0)
        parked_high = transition_status(claimed=1, live_z=1.0, commanded_z=1.0)
        self.assertEqual(self.resolve(parked_high, nozzle=nozzle(1.0)).index, 0)

    def test_a_restarted_print_does_not_inherit_the_old_file_position(self):
        # A same-file restart keeps virtual_sdcard's byte offset until the
        # printer reads its own file, so the finished print's offset is
        # still reported — and the resolver, before the start-gcode sets
        # current_layer, has nothing but that offset to go on. The
        # coordinator's boundary refusal squares the carried offset to
        # zero; without it the new print opens on the old print's layer,
        # which is the anchor the old fraction was painted from.
        jobs = RemoteJobService({"printing", "paused"})
        jobs.observe({"state": "printing", "filename": "part.gcode", "print_duration": 600},
                     {"file_size": 100000, "file_position": 90000})
        restarted = jobs.observe({"state": "printing", "filename": "part.gcode",
                                  "print_duration": 1},
                                 {"file_size": 100000, "file_position": 90000})
        self.assertTrue(restarted.new_job)
        self.assertFalse(jobs.position_attributed(90000), "the carried offset is the old print's")
        index = restart_index()
        # What the coordinator hands the resolver once it has refused the
        # carried offset: the new print's own layer, not the old print's.
        self.assertEqual(resolve_at(index, 0).index, 0)
        # The offset would otherwise have read as the finished print's layer.
        self.assertEqual(resolve_at(index, 90000).index, 4)


def restart_index():
    """current_layer is still the pre-print zero, so the offset below is
    the resolver's only evidence — the restart's own first poll."""
    return SimpleNamespace(ranges=((0, 100), (100, 200)),
                           current_layer_map={},
                           layer_at=lambda position: 4 if position == 90000 else 0)


def resolve_at(index, position, claimed=0):
    status = {"print_stats": {"state": "printing", "filename": "part.gcode",
                              "info": {"current_layer": claimed, "total_layer": 6}},
              "virtual_sdcard": {"file_position": position, "file_size": 100000}}
    return LayerResolver().resolve(status, PrinterConfig(), index=index,
                                   metadata=METADATA, heights=HEIGHTS)


def _transition_index():
    """Two layers, and a layer change to trip over: layer 0 is a 10-motion
    row ending at x = 9, layer 1 a 20-motion row that passes back through
    (9, 0) at its own motion 9. The nozzle finishing layer 0 therefore
    sits exactly on a point of layer 1's toolpath — the coincidence that
    turned an early anchor into a published half-layer."""
    from array import array
    from plugins.GCodeIndex import LayerMotionIndex

    def offsets(count):
        return array("Q", [m * 10 for m in range(count)])

    return LayerMotionIndex(
        ranges=[(0, 100), (100, 200)],
        motion_offsets=[offsets(10), offsets(20)],
        motion_x=[array("f", [float(m) for m in range(10)]),
                  array("f", [float(m) for m in range(20)])],
        motion_y=[array("f", [0.0] * 10), array("f", [0.0] * 20)],
        motion_z=[array("f", [0.2] * 10), array("f", [0.4] * 20)],
        motion_types=[[[10, 2]], [[20, 2]]],
        type_names=["WALL-OUTER"],
        travel_starts=[[], []],
        travel_ends=[[], []],
        layer_start_positions=[(0.0, 0.0, 0.0), (0.0, 0.0, 0.2)],
        layer_start_absolute=[True, True],
        layer_start_units=[1.0, 1.0],
        layer_start_types=[1, 1],
        layer_start_e=[0.0, 0.0],
        layer_start_e_absolute=[True, True],
        layer_start_extruding=[True, True],
        current_layer_map={},
        layer_elapsed_times=[1.0, 1.0],
        compact=True,
        hydrated_layers={0, 1},
    )


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class PlateAnchorAtTheTransitionTests(unittest.TestCase):
    """What the wrong anchor publishes, and what the held one publishes.

    Drives the real ``GCodeIndexService`` through the coordinator's own
    arguments: the resolved layer, the parser's byte offset and the
    nozzle's live position. Each poll needs its own service — the split
    is monotonic per anchor, which is exactly why an early anchor matters:
    its value is latched before it is corrected.
    """

    def setUp(self):
        from tests.qt_runtime_support import runtime

        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def service(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Files(QObject):
            changed = pyqtSignal()

        service = self.qt.load("GCodeIndexService").GCodeIndexService(Files(), object())
        self.addCleanup(service.close)
        self.job = ("part.gcode", 200, 1)
        service.bind(self.job)
        index = _transition_index()
        service._view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        from plugins.PlateProgress import prepare_layer
        for layer in range(2):
            service._decoded_lru[layer] = prepare_layer(index, layer)
        return service

    def test_the_held_anchor_publishes_the_finished_layer(self):
        # The parser is at the first byte of layer 1; the nozzle is on
        # layer 0's last motion. Anchored to the layer the nozzle is on,
        # all of layer 0 is printed and none of layer 1 is: the payload
        # cannot show an advanced boundary for a layer that has not
        # started.
        payload = self.service().plate_progress(0, 100, (9.0, 0.0, 0.2))
        self.assertEqual(payload["anchor"], 0)
        self.assertEqual(payload["split"], payload["motionTotal"])

    def test_the_early_anchor_publishes_a_fraction_of_an_unprinted_layer(self):
        # The anchor the unfixed resolver published: layer 1's refinement
        # searches the new layer's geometry from the nozzle's place on the
        # OLD layer's toolpath and matches a motion in a layer with
        # nothing printed (measured: 10 of 20 motions at the first poll,
        # then held by the service's floor before it rewinds).
        payload = self.service().plate_progress(1, 100, (9.0, 0.0, 0.2))
        self.assertEqual(payload["anchor"], 1)
        self.assertGreater(payload["split"], 0)
        self.assertLess(payload["split"], payload["motionTotal"])

    def test_a_coarse_only_poll_publishes_the_parser_fraction(self):
        # With no live telemetry the split falls back to the parser's byte
        # fraction INSIDE the anchored layer — a nonzero boundary at the
        # layer's very first byte. The anchor is therefore the only thing
        # keeping such a poll honest, which is why the hold happens in the
        # resolver and not downstream, and why the two paths that hand the
        # service a live position are one snapshot (the coordinator's
        # pinned frame).
        first = self.service().plate_progress(1, 100, None)
        self.assertGreater(first["split"], 0)
        later = self.service().plate_progress(1, 160, None)
        self.assertGreater(later["split"], first["split"])

    def test_the_transition_is_not_re_searched_when_the_asset_lands_late(self):
        # The poll order the render lane's pending-Canvas case is the QML
        # analogue of: the layer's decoded asset is not in the hot cache
        # yet (the worker's callback is still in flight). The anchor is
        # the held one, so a missing asset changes nothing — the payload
        # is the finished layer either way, and nothing is re-anchored.
        service = self.service()
        service._decoded_lru.pop(1)
        payload = service.plate_progress(0, 100, (9.0, 0.0, 0.2))
        self.assertEqual(payload["split"], payload["motionTotal"])
        self.assertEqual(payload["anchor"], 0)


if __name__ == "__main__":
    unittest.main()
