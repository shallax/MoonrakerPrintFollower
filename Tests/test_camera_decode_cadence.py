"""Camera pipeline integration contracts, isolated by test-file process."""
import unittest
from Tests.mjpg_test_support import QT_AVAILABLE

if QT_AVAILABLE:
    from Tests.mjpg_test_support import (
        QUrl, runtime, MoonrakerMJPGImage, RENDER_INTERVAL_MS, FakeReply, FakeNam,
        _jpeg, _multipart,
    )


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class DecodeThrottleTests(unittest.TestCase):
    """The decode throttle: the render tick is the one place a JPEG
    becomes a QImage, so the rate the pane asks for IS the render
    timer's interval. The receive/parse side is deliberately left
    alone — the drain still runs at the wire's own pace and only the
    newest frame survives — so a low rate costs decodes, never
    latency or a reconnect."""

    def setUp(self):
        self._rt = runtime()
        self.qt = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        self.nam = FakeNam()
        self.item = MoonrakerMJPGImage()
        self.item._network_manager = self.nam  # the injection seam
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/webcam"))
        self.addCleanup(self.item.stop)

    def _reply(self):
        return self.nam.requests[-1]

    def _start(self):
        def _get_with_type(request):
            reply = FakeReply()
            self.nam.requests.append(reply)
            return reply
        self.nam.get = _get_with_type
        self.item.start()

    def test_the_target_round_trips_and_the_render_timer_follows(self):
        emissions = []
        self.item.targetFpsChanged.connect(lambda: emissions.append(self.item.targetFps))
        self.item.targetFps = 5.0
        self.assertEqual(self.item.getTargetFps(), 5.0)
        self.assertEqual(self.item.targetFps, 5.0)  # the property read
        self.assertEqual(self.item.renderIntervalMs, 200)
        self.assertEqual(self.item._render_timer.interval(), 200,
                         "the timer's interval is the throttle's own effect")
        self.assertEqual(emissions, [5.0], "one notify, carrying the new rate")
        # A second rate moves both again; the same rate is a no-op.
        self.item.targetFps = 60.0
        self.assertEqual((self.item.targetFps, self.item.renderIntervalMs), (60.0, 17))
        self.assertEqual(self.item._render_timer.interval(), 17)
        self.item.targetFps = 60.0
        self.assertEqual(emissions, [5.0, 60.0], "an unchanged rate never re-notifies")
        # Both the getter and the setter are the property's, not a QML-only shim.
        self.item.setTargetFps(30.0)
        self.assertEqual(self.item.getTargetFps(), 30.0)

    def test_a_non_positive_target_is_the_idle_ceiling(self):
        for value in (0, 0.0, -1.0, None, "not a number", float("nan")):
            with self.subTest(value=value):
                self.item.setTargetFps(30.0)  # a live throttle first
                self.item.setTargetFps(value)
                self.assertEqual(self.item.targetFps, 0.0,
                                 "a non-positive or unusable target releases the throttle")
                self.assertEqual(self.item.renderIntervalMs, RENDER_INTERVAL_MS)
                self.assertEqual(self.item._render_timer.interval(), RENDER_INTERVAL_MS)

    def test_a_corrupt_target_never_spins_the_timer(self):
        # The 1 ms floor: a rate the interval maths would round to
        # zero (or an infinity from a corrupt payload) still leaves a
        # timer with a positive interval.
        for value in (float("inf"), 1e9, 2000.0):
            with self.subTest(value=value):
                self.item.setTargetFps(value)
                self.assertGreaterEqual(self.item.renderIntervalMs, 1)
                self.assertEqual(self.item._render_timer.interval(),
                                 self.item.renderIntervalMs)

    def test_a_low_rate_throttles_the_decode_and_never_the_parse(self):
        # The saving is the decode. The buffer still drains at the
        # wire's pace (every frame parses), the tick is what follows
        # the rate, and the newest frame is the one that decodes. The
        # rate is read off the timer's own interval, "not yet" is read
        # with no event loop run at all, and the single tick is driven
        # through the timer's signal — a wall-clock wait would let a
        # loaded runner land the tick inside it.
        self.item.setTargetFps(2.0)  # one render tick every 500 ms
        self._start()
        self.assertEqual(self.item._render_timer.interval(), 500,
                         "the render tick follows the requested rate")
        self.assertTrue(self.item._render_timer.isActive(),
                        "and the tick runs for as long as the stream does")
        frames = [_jpeg(40, 30, shade=40 + index) for index in range(9)]
        self._reply().deliver(b"".join(_multipart(frame) for frame in frames))
        self.assertEqual(self.item.framesParsed, 9, "the parse is never throttled")
        # No event loop has run since the timer started, so no tick can
        # have landed: this zero is the parser's own doing.
        self.assertEqual(self.item.framesDisplayed, 0,
                         "a parsed frame is not a decode")
        self.assertEqual(self.item.framesDropped, 8,
                         "the superseded eight are counted as dropped")
        # The tick itself, through the timer's signal with the timer
        # stopped by its first fire: exactly one lands, however loaded
        # the machine is and however long this loop runs.
        self.item._render_timer.timeout.connect(self.item._render_timer.stop)
        self.item._render_timer.start(0)
        self.qt.events(100)
        self.assertEqual(self.item.framesDisplayed, 1, "and decodes one frame")
        self.assertFalse(self.item._render_timer.isActive(),
                         "the tick was driven, not waited out")
        self.assertEqual(self.item.framesDropped, 8, "the tick drops nothing")
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 48,
                         "the newest frame wins the display")
        self.assertEqual(self._reply()._aborted, 0, "no reconnect anywhere")

    def test_the_throttle_survives_a_restart_and_releases_with_zero(self):
        # The throttle is item state, not stream state: a source
        # restart must not silently restore the idle ceiling, and
        # zero must give it back.
        self.item.setTargetFps(10.0)
        self._start()
        self.assertEqual(self.item._render_timer.interval(), 100)
        self.item.stop()
        self.item.start()
        self.assertEqual(self.item.targetFps, 10.0)
        self.assertEqual(self.item._render_timer.interval(), 100)
        self.item.setTargetFps(0)
        self.assertEqual(self.item._render_timer.interval(), RENDER_INTERVAL_MS)



if __name__ == "__main__":
    unittest.main()
