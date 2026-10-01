"""The popover's pause block, projected from the coordinator's rows.

The projection exists so the popover's button is armed exactly when the
coordinator would accept the request, at the layer the popover is
standing on rather than the live one. These pin the two gates, the
anchor's precedence and the total's fallback; the facade's own file
drives the same values out of a real schedule.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from mpf.Monitor.PauseAtLayerPresentation import PauseAtLayerPresentation

MANUAL_LAYER = 4   # the rows are 1-based; the projection reads 0-based
BAKED_LAYER = 6


def snapshot(index=None, total=None):
    return SimpleNamespace(layer=SimpleNamespace(index=index, total=total))


def item(layer, state="manual"):
    return {"layer": layer, "state": state}


BLOCK = {
    "pauseAtLayerActive": True,
    "pauseAtLayerSummary": "3 pauses",
    "pauseAtLayerItems": [item(MANUAL_LAYER), item(BAKED_LAYER, "baked")],
    "pauseAtLayerHasBaked": True,
    "pauseAtLayerHasClearable": True,
}


def presentation(block=BLOCK, anchor=-1):
    return PauseAtLayerPresentation(block=lambda: block, anchor=lambda: anchor)


class PauseAtLayerPresentationTests(unittest.TestCase):
    def test_no_seam_and_no_block_both_publish_nothing(self):
        self.assertEqual({}, PauseAtLayerPresentation(block=None, anchor=lambda: -1).values(
            snapshot(), -1, 100))
        for published in (None, [], "", 0):
            with self.subTest(published=published):
                self.assertEqual({}, presentation(block=published).values(snapshot(), -1, 100))

    def test_the_candidate_is_the_layer_the_popover_stands_on(self):
        values = presentation(anchor=MANUAL_LAYER - 1).values(snapshot(), 0, 100)
        self.assertEqual(values["pauseAtLayerCandidate"], MANUAL_LAYER)
        self.assertTrue(values["pauseAtLayerScheduled"], "that layer is one of the manual rows")

    def test_a_never_slid_popover_falls_back_to_the_plate_anchor(self):
        values = presentation(anchor=-1).values(snapshot(), MANUAL_LAYER - 1, 100)
        self.assertEqual(values["pauseAtLayerCandidate"], MANUAL_LAYER)
        self.assertTrue(values["pauseAtLayerScheduled"])

    def test_an_unknown_layer_publishes_no_candidate(self):
        values = presentation(anchor=-1).values(snapshot(), -1, 100)
        self.assertEqual(values["pauseAtLayerCandidate"], 0)
        self.assertFalse(values["pauseAtLayerCanToggle"])
        self.assertFalse(values["pauseAtLayerScheduled"])

    def test_the_rows_own_facts_cross_unchanged(self):
        values = presentation().values(snapshot(), 0, 100)
        self.assertIs(values["pauseAtLayerItems"], BLOCK["pauseAtLayerItems"])
        self.assertEqual(values["pauseAtLayerSummary"], "3 pauses")
        self.assertTrue(values["pauseAtLayerHasBaked"])
        self.assertTrue(values["pauseAtLayerHasClearable"])
        self.assertTrue(values["pauseAtLayerActive"])

    def test_a_baked_layer_greys_the_button_with_its_own_reason(self):
        values = presentation(anchor=BAKED_LAYER - 1).values(snapshot(index=0, total=100), 0, 100)
        self.assertFalse(values["pauseAtLayerCanToggle"],
                         "the coordinator refuses a layer that is already baked")
        self.assertIn("baked into the gcode", values["pauseAtLayerUnavailableText"])

    def test_an_unindexed_print_says_so_instead_of_the_card_reason(self):
        values = presentation().values(snapshot(), 0, 0)
        self.assertFalse(values["pauseAtLayerCanToggle"])
        self.assertEqual(values["pauseAtLayerUnavailableText"], "Print not indexed")

    def test_the_button_is_armed_exactly_when_the_coordinator_would_accept(self):
        values = presentation(anchor=MANUAL_LAYER - 1).values(snapshot(index=0, total=100), 0, 100)
        self.assertTrue(values["pauseAtLayerCanToggle"])
        self.assertEqual(values["pauseAtLayerUnavailableText"], "")

    def test_the_cards_own_reason_text_passes_through_when_it_is_neither_gate(self):
        values = presentation(anchor=2).values(snapshot(index=9, total=100), 0, 100)
        self.assertFalse(values["pauseAtLayerCanToggle"])
        self.assertEqual(values["pauseAtLayerUnavailableText"], "Layer 3 already printed")

    def test_the_total_falls_back_to_the_plate_layer_count(self):
        # The snapshot's own total is absent mid-print: the plate's count
        # is what keeps the final-layer gate honest.
        values = presentation(anchor=99).values(snapshot(index=0, total=None), 0, 100)
        self.assertFalse(values["pauseAtLayerCanToggle"])
        self.assertEqual(values["pauseAtLayerUnavailableText"], "Final layer ends the print")
        self.assertTrue(presentation(anchor=99).values(snapshot(index=0, total=None), 0, 200)
                        ["pauseAtLayerCanToggle"], "the plate's count is what fed the gate")

    def test_the_snapshots_own_total_wins_over_the_plate_count(self):
        values = presentation(anchor=99).values(snapshot(index=0, total=120), 0, 100)
        self.assertTrue(values["pauseAtLayerCanToggle"])

    def test_an_inactive_print_greys_the_button(self):
        values = presentation(block=dict(BLOCK, pauseAtLayerActive=False)).values(
            snapshot(index=0, total=100), 0, 100)
        self.assertFalse(values["pauseAtLayerCanToggle"])
        self.assertEqual(values["pauseAtLayerUnavailableText"], "")


if __name__ == "__main__":
    unittest.main()
