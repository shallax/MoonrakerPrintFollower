"""Follower view DPR transport through the production Qt model."""
from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    class FollowerViewDprTests(harness.FollowerViewDprTests):
        def test_the_popovers_nine_argument_call_lands_at_dpr_two(self):
            # The popover feeds the DPR ninth; a slot registered for
            # eight arguments silently drops it (the engine's "Too many
            # arguments" warning), and the workers then paint at DPR 1.
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runPopover")
            view = self._surface("popover").view
            self.assertEqual(view.get("dpr"), 2.0,
                             "the nine-argument view call dropped its DPR")
            self.assertEqual(view.get("width"), 400)
            self.assertFalse(view.get("compact"))

        def test_the_minis_eight_argument_call_still_lands(self):
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runMini")
            view = self._surface("mini").view
            self.assertEqual(view.get("width"), 120)
            self.assertTrue(view.get("compact"))
            self.assertEqual(view.get("dpr"), 1.0,
                             "the eight-argument call invented a DPR")

        def test_the_production_popover_call_reaches_the_slot_intact(self):
            # The monitor's own face feeds the slot through its
            # viewSettled connection: the arity mismatch is visible in
            # the engine's diagnostics even where the dropped value
            # (1.0 offscreen) is indistinguishable.
            monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
            monitor.setProperty("printer", self.model)
            monitor.setProperty("openPopOver", "plateprogress")
            self.pump(30)
            faces = [face for face in monitor.findChildren(harness.QQuickItem, "moonrakerPlateProgressFace")
                     if not face.property("compact")]
            self.assertEqual(len(faces), 1)
            face = faces[0]
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(face, "viewSettled")
            self.pump(20)
            view = self._surface("popover").view
            self.assertEqual(view.get("width"), int(face.width()),
                             "the popover's view never reached the model")
            self.assertFalse(view.get("compact"))
            truncated = [message for message in
                         harness._APPLICATION["messages"][self._message_start:]
                         if "Too many arguments" in message]
            self.assertEqual(truncated, [],
                             "the popover's nine-argument view call was truncated")

        def test_a_qml_dpr_change_rebakes_the_navigation_raster(self):
            surface = self._surface("popover")
            before = self._navigation_demand(surface, 1.0)
            self.assertIsNotNone(before, "the navigation demand never built")
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runPopover")
            self.assertEqual(surface.view.get("dpr"), 2.0)
            after = self.model._navigation_key(surface)
            self.assertNotEqual(before, after,
                                "a DPR change never invalidated the navigation raster")
            self.assertNotEqual(self.model._nav_key_hard(before),
                                self.model._nav_key_hard(after),
                                "the DPR change is not hard — the follow throttle "
                                "would swallow the re-bake")

        def test_a_dpr_stale_navigation_raster_leaves_the_face(self):
            surface = self._surface("popover")
            baked = self._navigation_demand(surface, 1.0)
            surface.nav["url"] = "file:///tmp/mpf/raster-probe/nav-stub.png"
            surface.nav["key"] = baked
            self.assertEqual(self.model._navigation_data_value(surface),
                             surface.nav["url"],
                             "the warm raster never reached the face")
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runPopover")
            # The raster in hand was baked at the old ratio: at the new
            # demand it is a stale picture of the same scene.
            surface.nav["url"] = "file:///tmp/mpf/raster-probe/nav-stub.png"
            surface.nav["key"] = baked
            self.assertEqual(self.model._navigation_data_value(surface), "",
                             "a DPR-stale navigation raster still reached the face")

