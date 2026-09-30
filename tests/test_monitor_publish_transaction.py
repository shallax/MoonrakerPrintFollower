"""The facade's publication transaction, driven through the real model.

`_publish()` is one transaction: the snapshot and the transitions that
precede the values built from it, the value build, ONE commit of the
whole map, the camera transition amending that frame, then the
notification pass. These tests pin the phase boundaries the restructure
made explicit: a notify handler must see the frame its signal announces,
the camera's nonce must ride the publish that bumped it, and a poll that
changed nothing stays quiet and cheap.
"""
from __future__ import annotations

from types import SimpleNamespace

from mpf.monitor.MonitorPublication import SIGNAL_GROUPS
from tests import monitor_test_support as harness


class MonitorPublishTransactionTests(harness.MonitorQtTests):
    def announced(self, model, names=None):
        """Record which named signals fire, in the order they fire."""
        fired = []
        for name, _keys in SIGNAL_GROUPS:
            if names is not None and name not in names:
                continue
            getattr(model, name).connect(lambda name=name: fired.append(name))
        return fired

    def test_a_handler_reads_the_frame_its_signal_announces(self):
        # The commit precedes the notification: a handler that reads a
        # sibling property must see the NEW frame. The follower's attach
        # flip is the case that matters — QML reads the anchor in the
        # same turn it re-frames the plate.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events()
        seen = []
        model.followerViewChanged.connect(lambda: seen.append(model.followerLayerAnchor))
        model._follower_layer_anchor = 17
        model._publish()
        self.assertEqual(seen, [17],
                         "the notification fired before the frame committed")

    def test_the_camera_nonce_rides_the_publish_that_bumped_it(self):
        # The camera-delay fix: the bump reaches QML in the SAME frame as
        # the URL it belongs to, or the coalescer applies the stream
        # twice. Reading the property from the notify handler is the
        # contract QML depends on.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events()
        model._camera = SimpleNamespace(url="", values={})
        model._publish()
        seen = []
        model.cameraRefreshChanged.connect(lambda: seen.append(model.cameraRefreshNonce))
        model._camera.url = "http://cam/stream"
        model._publish()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], model.cameraRefreshNonce,
                         "the nonce the handler read was not the published one")
        self.assertGreater(seen[0], 0)

    def test_a_quiet_poll_broadcasts_nothing(self):
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events()
        fired = self.announced(model)
        model._publish()
        self.assertEqual(fired, [],
                         "a poll that changed no key still notified: %s" % fired)

    def test_only_the_groups_whose_keys_moved_are_notified(self):
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events()
        fired = self.announced(model)
        self.deliver_state("paused")
        self.qt.events()
        self.assertIn("monitorChanged", fired)
        # The print's state moved; nothing about the camera or the
        # console transcript did.
        self.assertNotIn("cameraTransformChanged", fired)
        self.assertNotIn("consoleChanged", fired)
        self.assertNotIn("fileManagerThumbsChanged", fired)

    def test_a_quiet_poll_issues_no_request_and_writes_no_state(self):
        # Acceptance: the unchanged poll stays cheap. The build may
        # re-derive every projection, but no request leaves for the
        # printer and no state document is rewritten.
        model = self.monitor()
        self.deliver_state("printing")
        self.qt.events()
        model._publish()
        issued = len(self.transport.requests)
        writes = []
        original = type(model)._save_state
        type(model)._save_state = lambda self: writes.append(1)
        try:
            model._publish()
            model._publish()
        finally:
            type(model)._save_state = original
        self.assertEqual(writes, [], "a quiet poll wrote the state document")
        self.assertEqual(len(self.transport.requests), issued,
                         "a quiet poll issued a printer request")
