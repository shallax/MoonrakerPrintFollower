"""Executable qml camera lifecycle contracts."""
from tests import qml_engine_support as harness

class CameraTitleRowTests(harness.CameraTitleRowTests):
    def test_the_refresh_button_stays_inside_the_pane(self):
        pane, window = self._mount_pane(self.FOLDED_WIDTHS[0])
        button = self._refresh_button(pane)
        self.assertTrue(button.isVisible(), "the controls did not build")
        for width in self.FOLDED_WIDTHS:
            self.resize_window(pane, window, width, 420)
            pane_rect = self.rect(pane, pane)
            button_rect = self.rect(button, pane)
            self.assertGreater(button.width(), 0.0, width)
            self.assertGreater(button_rect.left(), pane_rect.left(), width)
            self.assertLessEqual(button_rect.right(), pane_rect.right() + 0.5,
                                 "the refresh button left the pane at %d" % width)


class CameraOwnershipTests(harness.CameraOwnershipTests):
    def test_first_application_starts_the_consumer_exactly_once(self):
        # A: the initial attach applies the settled final state once —
        # exactly one start, one stop, one source assignment. The old
        # churn (URL publish then a later nonce publish) drove two
        # applications of two URL strings; the coalescer now delivers
        # ONE application of the final URL.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        self.assertEqual((1, 1, 1), self._counts(image))

    def test_duplicate_desired_state_is_a_no_op(self):
        # B: applying URL X + running twice — the second application
        # must not stop, re-assign the source or start again (each
        # would kill a healthy stream through Cura's destructive
        # start()).
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        self.assertEqual(first, self._counts(image))

    def test_query_only_republish_is_a_no_op(self):
        # C: a rotated nonce in the query is the same desired stream —
        # the data-snapshot reaction must not kill a healthy
        # connection for it (the 2026-09-19 live-run ruling).
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?nonce=1", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?nonce=2", True)
        self.pump()
        self.assertEqual(first, self._counts(image))

    def test_a_camera_selection_change_restarts_exactly_once(self):
        # D: two selections can share one PATH and differ only in the
        # camera= query. That is a different stream, so the pane must
        # re-resolve — the whole query is not rotation noise.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?camera=front", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?camera=back", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2] + 1), self._counts(image))
        self.assertEqual(image.property("source").toString(),
                         "http://127.0.0.1:59999/webcam2/?camera=back",
                         "the second selection resolved to the first source")

    def test_a_rotating_nonce_never_thrashes_a_selection(self):
        # E: the same selection with a rotated per-poll nonce (the
        # token/timestamp a camera service rewrites) is the SAME
        # stream — no stop, no re-assignment, no restart — however the
        # query is spelled.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?camera=front&token=aaa", True)
        self.pump()
        first = self._counts(image)
        for url in ("http://127.0.0.1:59999/webcam2/?camera=front&token=bbb",
                    "http://127.0.0.1:59999/webcam2/?token=ccc&camera=front"):
            self._apply(pane, url, True)
            self.pump()
            self.assertEqual(first, self._counts(image), url)
        # A nonce rotation on ANOTHER selection is a real change: the
        # rotation must not hide the camera it rides with.
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?camera=back&token=ddd", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2] + 1), self._counts(image))

    def test_reload_marker_restarts_exactly_once(self):
        # The mpf_reload marker is the model's EXPLICIT reload request
        # (manual refresh, watchdog recovery, reconnect): a bump still
        # restarts — exactly one replacement start, not zero and not
        # two.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?mpf_reload=1", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?mpf_reload=2", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2] + 1), self._counts(image))

    def test_genuine_path_change_restarts_exactly_once(self):
        # A different stream is a different desired state: exactly one
        # replacement start, not zero and not two.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam/", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2] + 1), self._counts(image))

    def test_visibility_lifecycle_stops_and_restarts_once(self):
        # D: hide -> one stop; show -> one start. The lifecycle
        # handler adopts the new visible state into the applied
        # latches, so a later identical application stays a no-op —
        # never a second start path racing applyCamera.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        first = self._counts(image)
        image.setProperty("visible", False)
        self.pump()
        self.assertEqual((first[0], first[1] + 1, first[2]), self._counts(image))
        image.setProperty("visible", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2]), self._counts(image))
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2]), self._counts(image))

    def test_the_stream_chip_stays_hidden_until_genuinely_live(self):
        # The chip's liveness contract (the offline-veil ruling): no
        # model, or a hidden frame-less image, hides the chip — the
        # stats must never float over the veil. The SHOWING case
        # renders for real in the capture leg, whose census reads the
        # chip's actual text.
        pane, window = self.mount_window("CameraPane.qml", 400, 420)
        pane.setProperty("configured", True)
        self.pump(30)
        chip = self.find(pane, "cameraStreamChip")
        image = self.find(pane, "cameraImage")
        image.setProperty("visible", True)
        image.setProperty("imageWidth", 640)
        image.setProperty("recentBytesPerSec", 11000000)
        self.pump()
        self.assertFalse(chip.property("visible"),
                         "a model-less pane must not show the chip")
        image.setProperty("visible", False)
        self.pump()
        self.assertFalse(chip.property("visible"))
        self.addCleanup(window.deleteLater)


