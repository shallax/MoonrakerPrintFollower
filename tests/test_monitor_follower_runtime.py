"""Executable monitor follower runtime contracts."""
from tests import monitor_test_support as harness

class MonitorQtTests(harness.MonitorQtTests):
    def test_the_follower_keys_publish_their_defaults_and_the_served_layer(self):
        model = self.monitor()
        self.assertTrue(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, -1)
        self.assertEqual(model.plateLayerCount, 0)
        self._with_layers(model, anchor=7, count=12)
        model.setFollowerPopoverOpen(True)
        self.assertEqual(model.plateProgressAnchor, 7)
        self.assertEqual(model.plateLayerCount, 12)

    def test_the_progress_scrub_is_itself_a_detach_and_clamps_to_the_layer(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=7, count=12)
        model.setFollowerPopoverOpen(True)
        # A scrub from the LIVE layer freezes it where it stood: the
        # within-layer seek cannot follow the print and play at once.
        model.setFollowerLayerProgress(41)
        self.assertFalse(model.followerAttached)
        self.assertEqual(model.followerLayerAnchor, 7)
        self.assertEqual(coordinator._plate_split, 41)
        # The request clamps to the layer's motion count (the fixture's
        # payload carries motionTotal 100). The scrub's own refresh
        # reaps the injected payload, so it is re-armed first.
        self._with_layers(model, anchor=7, count=12)
        model.setFollowerLayerProgress(9999)
        self.assertEqual(coordinator._plate_split, 100)

    def test_the_progress_scrub_from_attached_layer_zero_detaches(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=0, count=12)
        model.setFollowerPopoverOpen(True)
        model.setFollowerLayerProgress(41)
        self.assertFalse(model.followerAttached,
                         "the scrub on layer zero never detached")
        self.assertEqual(model.followerLayerAnchor, 0)
        self.assertEqual(coordinator._plate_anchor, 0)
        self.assertEqual(coordinator._plate_split, 41)
        # The detached state survives the refresh.
        self._with_layers(model, anchor=0, count=12)
        self.assertFalse(model.followerAttached,
                         "the refresh re-attached the layer-zero scrub")
        self.assertEqual(model.followerLayerAnchor, 0)

    def test_a_closed_popover_freezes_the_follower_payload_and_reopening_resumes(self):
        model = self.monitor()
        self._with_layers(model, anchor=7, count=12)
        model.setFollowerPopoverOpen(True)
        self._with_layers(model, anchor=8, count=12)
        self.assertEqual(model.plateProgressAnchor, 8)
        model.setFollowerPopoverOpen(False)
        self._with_layers(model, anchor=9, count=12)
        self.assertEqual(model.plateProgressAnchor, 8,
                         "the closed popover took the fresh payload")
        model.setFollowerPopoverOpen(True)
        self.assertEqual(model.plateProgressAnchor, 9,
                         "reopening never resumed the live payload")

    def test_a_new_print_reattaches_the_follower(self):
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self._with_layers(model, anchor=7, count=12)
        model.setFollowerPopoverOpen(True)
        model.setFollowerAttached(False)
        self.assertEqual(coordinator._plate_anchor, 7)
        # The frozen layer belonged to the file that was printing.
        coordinator._snapshot = harness.replace(coordinator._snapshot, job_key=("next.gcode", 1))
        model._publish()
        self.assertTrue(model.followerAttached, "the frozen layer survived into the next print")
        self.assertEqual(model.followerLayerAnchor, -1)
        self.assertIsNone(coordinator._plate_anchor)


