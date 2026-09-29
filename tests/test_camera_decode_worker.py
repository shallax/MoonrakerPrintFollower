"""Camera pipeline integration contracts, isolated by test-file process."""
import time
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from tests.mjpg_test_support import QT_AVAILABLE

if QT_AVAILABLE:
    from tests.mjpg_test_support import (
        QThread, QUrl, QImage,
        runtime, FrameDecoder,
        MoonrakerMJPGImage, FakeReply, FakeNam,
        _jpeg, _multipart,
    )


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class DecodeOffTheQtThreadTests(unittest.TestCase):
    """The decode worker: the Qt thread keeps the hand-over and the
    install, and the JPEG decode — the one large piece of per-frame work
    — happens on a thread of the renderer's own. What these pin is that
    the split holds: which thread decodes, that at most one decode is in
    flight (the rate cap and the memory budget), that a superseded or
    post-shutdown result is never installed, and that the Qt thread's
    share per frame is the hand-over rather than the decode."""

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

    def _drain(self, milliseconds):
        self.qt.events(milliseconds)

    def _drain_until(self, predicate, milliseconds=4000):
        deadline = time.monotonic() + milliseconds / 1000.0
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.qt.events(25)
        return predicate()

    def _start(self, content_type=b"multipart/x-mixed-replace; boundary=mpfboundary"):
        def _get_with_type(request):
            reply = FakeReply(content_type)
            self.nam.requests.append(reply)
            return reply
        self.nam.get = _get_with_type
        self.item.start()

    def _patch_decode(self, replacement):
        """Patch the module-level decode seam the worker resolves. The
        patch targets the function's own globals — the binding the
        worker actually calls — because the unittest runtime's double
        entry can alias the module."""
        globals_dict = FrameDecoder.run.__globals__
        old = globals_dict.get("_decode_jpeg")
        globals_dict["_decode_jpeg"] = replacement
        self.addCleanup(globals_dict.__setitem__, "_decode_jpeg", old)

    def _tick_and_install(self):
        """Drive one render tick by hand and wait for its frame to reach
        the screen: the tick hands the frame over, and the install lands
        on a later Qt turn."""
        rendered = self.item._stats.decodes_rendered
        self.item._render()
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.decodes_rendered > rendered),
            "the decoded frame never reached the screen")

    def test_the_decode_runs_off_the_qt_thread(self):
        # The whole job: the JPEG decode must not run on the Qt thread.
        # The seam is module-level for exactly this — the worker's call
        # is observable, and what it observes is which thread ran it.
        threads = []

        def recording(frame):
            threads.append((threading.get_ident(), QThread.currentThread()))
            return QImage.fromData(frame)

        self._patch_decode(recording)
        main_ident = threading.get_ident()
        main_thread = QThread.currentThread()
        self._start()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertTrue(
            self._drain_until(lambda: self.item.framesDisplayed == 1),
            "the frame never reached the screen")
        self.assertTrue(threads, "the worker never called the decode seam")
        self.assertNotIn(main_ident, [ident for ident, _ in threads],
                         "the decode ran on the thread the test runs on")
        self.assertEqual(len({ident for ident, _ in threads}), 1,
                         "the decodes did not all run on the one worker")
        for _ident, thread in threads:
            self.assertIsNot(thread, main_thread,
                             "the decode ran on the Qt thread the item was made on")
            self.assertIsNot(thread, self.item.thread(),
                             "the decode ran on the thread the item lives on")
        self.assertEqual(self.item.imageWidth, 40,
                         "the frame on screen is the one the worker decoded")

    def test_native_decode_allows_python_callbacks_during_the_decode(self):
        decode = FrameDecoder.run.__globals__["_decode_jpeg"]
        frame = _jpeg(4096, 4096)
        intervals = []
        callbacks = []
        images = []

        def work():
            for _ in range(3):
                started = time.perf_counter()
                images.append(decode(frame))
                intervals.append((started, time.perf_counter()))

        worker = threading.Thread(target=work)
        worker.start()
        while worker.is_alive():
            callbacks.append(time.perf_counter())
            self.qt.events(1)
        worker.join()
        self.assertEqual(len(images), 3)
        self.assertTrue(all(image.width() == 4096 for image in images))
        # Observe progress INSIDE native work, excluding hand-over edges.
        # A worker calling QImage.fromData holds the GIL and permits none.
        self.assertTrue(any(start + 0.003 < callback < stop - 0.003
                            for start, stop in intervals for callback in callbacks),
                        "native JPEG work monopolised the Python execution lock")

    def test_native_decode_preserves_pixels_and_rejects_invalid_data(self):
        decode = FrameDecoder.run.__globals__["_decode_jpeg"]
        frame = _jpeg(40, 30, shade=83)
        self.assertEqual(decode(frame), QImage.fromData(frame))
        self.assertTrue(decode(b"not an image").isNull())

    def test_a_burst_keeps_one_decode_in_flight_and_installs_the_newest(self):
        # The rate cap survives the move: the tick hands over at most one
        # frame per decode, so a fast source can neither pile decodes up
        # nor hold more than one decoded image outside the Qt thread.
        # What arrives during a decode stays pending — the picture that
        # lands is the one the decode started on, and the newest arrival
        # takes the very next tick.
        gate = threading.Event()
        entered = threading.Event()
        calls = []

        def blocking(frame):
            calls.append(frame)
            if not entered.is_set():
                entered.set()
                gate.wait(2.0)
            return QImage.fromData(frame)

        self._patch_decode(blocking)
        self.item.setTargetFps(50.0)  # a 20 ms cap
        self._start()
        self.item._render_timer.stop()  # every tick from here is by hand
        first = [_jpeg(40, 30, shade=40 + index) for index in range(5)]
        self._reply().deliver(b"".join(_multipart(frame) for frame in first))
        self.item._render()  # one tick: one frame handed over, held there
        self.assertTrue(self._drain_until(entered.is_set),
                        "the worker never picked the frame up")
        # The stream keeps delivering while that decode is out — the
        # case the cap exists for: the newer frames must wait, not
        # queue another decode (or another decoded image) behind it.
        later = [_jpeg(40, 30, shade=45 + index) for index in range(4)]
        self._reply().deliver(b"".join(_multipart(frame) for frame in later))
        self.assertEqual(self.item.framesParsed, 9)
        for _ in range(4):
            self.item._render()  # ticks while that decode is still out
            self._drain(5)
        self.assertEqual(self.item._stats.decodes_rendered, 0,
                         "a frame reached the screen before its decode returned")
        # Nothing queued behind it: one frame in the worker plus the
        # newest arrival is the whole memory, and the decodes stay at
        # the cap's rate rather than the stream's.
        self.assertEqual(self.item._decoder._queue.qsize(), 0,
                         "a second decode was queued behind the one in flight")
        # A decode that has outlasted the cap: the completion is allowed
        # to hand the newest arrival over itself, which is what stops
        # the display costing two ticks per frame.
        time.sleep(0.06)
        gate.set()
        self.assertTrue(
            self._drain_until(lambda: self.item.framesDisplayed == 2),
            "the held decode and the frame behind it did not both reach "
            "the screen")
        self.assertEqual(len(calls), 2,
                         "the completion handed over more than the newest frame")
        # Nine parsed: two through the worker, the other seven superseded
        # in the buffer (four before the first hand-over, three after).
        self.assertEqual(self.item.framesDropped, 7)
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 48,
                         "the installed frame is not the newest arrival")
        self.assertEqual(self.item._decode_in_flight, 0,
                         "the in-flight slot never came back")
        self._drain(50)  # anything further queued would land in here
        self.assertEqual(self.item.framesDisplayed, 2)
        self.assertEqual(len(calls), 2,
                         "a frame was handed over with nothing asking for it")

    def test_a_decode_in_flight_at_shutdown_is_never_installed(self):
        # Cancellation and teardown together: a decode that started
        # before the stream stopped belongs to a stream that no longer
        # exists, so its result is discarded on arrival — and the worker
        # is joined rather than left running behind the item.
        gate = threading.Event()
        entered = threading.Event()
        decoded = threading.Event()

        def blocking(frame):
            if not entered.is_set():
                entered.set()
                gate.wait(2.0)
            image = QImage.fromData(frame)
            decoded.set()
            return image

        self._patch_decode(blocking)
        self._start()
        self.item._render_timer.stop()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.item._render()
        self.assertTrue(self._drain_until(entered.is_set),
                        "the worker never picked the frame up")
        gate.set()
        # Wait for the worker without running the event loop: the
        # result must still be in flight when the stream stops, which is
        # the case this pins.
        self.assertTrue(decoded.wait(4.0), "the worker never finished the decode")
        self.item.stop()
        self.assertIsNotNone(self.item._decoder,
                             "the worker was orphaned rather than joined")
        self.assertFalse(self.item._decoder.isRunning(),
                         "a worker outlived the item's stop()")
        self._drain(50)  # the queued result arrives, and is dropped
        self.assertEqual(self.item._stats.decodes_rendered, 0)
        self.assertEqual(self.item.framesDisplayed, 0)
        self.assertTrue(self.item._image.isNull(),
                        "a decode superseded by stop() reached the screen")
        self.assertEqual(self.item._decode_in_flight, 0)

    def test_the_tick_hands_the_frame_over_instead_of_decoding_it(self):
        # What the Qt thread pays per frame is the hand-over plus the
        # install; the decode's own milliseconds are the worker's, and
        # the two are accounted separately. A span of wall time carries
        # the machine with it, so the cost is the minimum of four: the
        # code is in every sample, and so is a tick that decoded.
        self._start()
        self.item._render_timer.stop()
        hand_overs = []
        for _ in range(4):
            before = self.item._stats.tick_ms_total
            self._reply().deliver(_multipart(_jpeg(400, 300)))
            self._tick_and_install()
            hand_overs.append(self.item._stats.tick_ms_total - before)
        self.assertEqual(self.item._stats.decodes_rendered, len(hand_overs))
        self.assertGreater(self.item._stats.decode_ms_total, 0.0)
        self.assertGreater(min(hand_overs), 0.0,
                           "a tick that handed nothing over was measured")
        self.assertLess(min(hand_overs),
                        self.item._stats.decode_ms_total / len(hand_overs) / 4.0,
                        "the Qt-thread tick cost as much as the decode")

    def test_a_tick_without_a_worker_leaves_the_frame_pending(self):
        # The worker is built with the stream and joined with it, so a
        # tick that finds none is the shutdown race's own shape: the
        # frame must stay where it is, not be consumed by nothing.
        item = MoonrakerMJPGImage()
        self.addCleanup(item.stop)
        item._network_manager = self.nam
        item.setSourceURL(QUrl("http://127.0.0.1:1/webcam"))
        item._pending_frame = _jpeg(40, 30)
        # The arrival meter is a perf_counter span; seed it on its own clock.
        item._pending_arrival = time.perf_counter()
        item._render()
        self.assertIsNotNone(item._pending_frame,
                             "a tick with no worker consumed the frame")
        self.assertEqual(item._decode_in_flight, 0)
        self.assertEqual(item._stats.decodes_rendered, 0)

    def test_a_decode_slower_than_the_tick_displays_once_per_decode(self):
        # A decode that outlasts its tick must not cost the NEXT tick too.
        # Dispatch quantised to the tick is one display per two ticks at a
        # 20 ms tick and a 22 ms decode, and a frame that arrived just after
        # the wasted tick then waits out another whole period, which is the
        # lag the live report names. The display rate belongs to the decode,
        # bounded by the cap, not to a multiple of the tick.
        #
        # The pin is a pair of counts rather than a rate, since a rate read
        # off a fixed window measures the machine as much as the scheduler:
        # a starved run reaches the same counts later, and cannot reach
        # different ones. Each decode is held open until the frame the next
        # hand-over needs is in hand, so the interleaving is the test's to
        # fix rather than the machine's.
        frames = 6
        real = QImage.fromData
        # One slot per decode, claimed under the lock: the worker settles the
        # order, and each release is held until the test is ready for it.
        entered = [threading.Event() for _ in range(frames + 2)]
        release = [threading.Event() for _ in range(frames + 2)]
        claim = threading.Lock()
        claimed = []

        def slow(frame):
            with claim:
                index = len(claimed)
                claimed.append(index)
            time.sleep(0.022)  # a decode a hair longer than one tick
            entered[index].set()
            # The timeout only stops a broken run from hanging the suite:
            # every release the test reaches is set within a tick of it.
            release[index].wait(2.0)
            return real(frame)

        self._patch_decode(slow)
        self.item.setTargetFps(50.0)  # a 20 ms tick
        self._start()
        self.item._render_timer.stop()  # every tick from here is by hand
        self.assertEqual(self.item._render_timer.interval(), 20)
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=40)))
        self.item._render()  # the run's only tick: it hands the first frame over
        for index in range(frames):
            self.assertTrue(
                self._drain_until(entered[index].is_set),
                "frame %d never reached the worker: the run's one tick is "
                "spent, so the hand-over had to come from the decode" % index)
            if index + 1 < frames:
                # The source stays a frame ahead of the decode, so every
                # completion has something to hand over.
                self._reply().deliver(
                    _multipart(_jpeg(40, 30, shade=41 + index)))
            release[index].set()
            self.assertTrue(
                self._drain_until(
                    lambda shown=index: self.item.framesDisplayed > shown),
                "frame %d never reached the screen" % index)
        self.assertEqual(len(claimed), frames,
                         "a frame was decoded more than once")
        self.assertEqual(self.item._stats.decodes_rendered, frames,
                         "the run cost a tick per frame: a decode's successor "
                         "waited for a tick that never came")
        self.assertEqual(self.item.framesDropped, 0,
                         "a frame was superseded rather than displayed")
        self.assertGreater(self.item._stats.decode_ms_max, 20.0,
                           "the decode did not outlast the tick")
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 45,
                         "the frame on screen is not the last one decoded")

    def test_the_completion_dispatch_never_shortens_the_cap(self):
        # The hand-over a completion makes must clear the same interval
        # the tick does, or the cap stops being a cap: a fast source
        # against a fast decode would display as fast as the source
        # delivers instead of at the rate the pane asked for.
        #
        # The completion only gets a say when a successor is already
        # pending as a decode returns — otherwise the tick hands every
        # frame over and this measures the tick, not the hand-over it
        # names. So the source must outrun the decode: 15 ms against a
        # 200 fps source, still well inside the 50 ms cap, and the
        # displayed rate is the tick's 20/s. Take the interval test out
        # of the hand-over and the same window runs at the decode's own
        # 63/s.
        def slow(frame):
            time.sleep(0.015)
            return QImage.fromData(frame)

        self._patch_decode(slow)
        self.item.setTargetFps(20.0)  # a 50 ms cap: 20 hand-overs a second
        self._start()
        self.assertEqual(self.item._render_timer.interval(), 50)
        frame = _jpeg(40, 30)
        # The window is the item's own count of displays and the bound
        # is the cap's allowance for the time that window took, so a
        # starved runner reaches the same count later and its allowance
        # grows with it: a fixed second read the machine instead, and
        # at a two-percent quota it went red having tested nothing.
        required = 12
        delivered = 0
        started = time.perf_counter()
        next_frame = started
        while self.item.framesDisplayed < required and \
                time.perf_counter() - started < 10.0:
            if time.perf_counter() >= next_frame:
                self._reply().deliver(_multipart(frame))
                delivered += 1
                next_frame += 0.005  # a 200 fps source, 10x the cap
            self.qt.events(5)
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(
            self.item.framesDisplayed, required,
            "the cap's own rate was not reached: the display never ran")
        # The premise, and it is about the SOURCE rather than the
        # runner: a window that delivered no more frames than it
        # displayed would pass the bound below having tested nothing.
        # Counted against the displays actually made, a starved loop
        # shrinks both sides together.
        self.assertGreaterEqual(
            delivered, 2 * self.item.framesDisplayed,
            "the source did not outrun the display: the bound below was "
            "vacuous")
        # The cap's own arithmetic: hand-overs are a whole interval
        # apart, so a window of this length holds at most its rate plus
        # the one that straddles the start.
        self.assertLessEqual(
            self.item.framesDisplayed, elapsed * 20.0 + 1,
            "the cap was exceeded: a completion hand-over outran the tick")

    def test_a_completion_inside_the_cap_leaves_the_frame_for_the_tick(self):
        # The completion hands over AT the cap, not merely after its
        # decode: a decode that finished inside the interval must leave
        # the frame behind it for the tick, or a fast decode would set
        # the display rate instead of the rate the pane asked for.
        def fast(frame):
            return QImage.fromData(frame)

        self._patch_decode(fast)
        # The cap is wide on purpose: "inside the interval" has to hold
        # by construction. A loaded runner stretches a decode past a
        # narrow cap, and the hand-over that follows is legal by the
        # cap's own rule — the pin would fail having tested nothing.
        self.item.setTargetFps(1.0)  # a 1000 ms cap
        self._start()
        self.assertEqual(self.item._render_timer.interval(), 1000)
        self.item._render_timer.stop()  # every tick from here is by hand
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=40)))
        self.item._render()  # handed over; this decode is a few ms
        # The next frame lands well inside the interval, so it is pending
        # when the decode returns.
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=41)))
        # A wait, not a sample: an install is all this waits for, and the
        # count is asserted below where a second one is the failure.
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.decodes_rendered >= 1),
            "the first decode never reached the screen")
        # The premise, checked rather than assumed: the decode returned
        # with the interval still running, which is the case this pin is
        # about and the case a stalled runner can silently turn into its
        # opposite.
        self.assertLess(
            time.perf_counter() - self.item._last_dispatch_at,
            self.item._render_timer.interval() / 1000.0,
            "the completion landed outside the cap: the pin's premise "
            "(a completion inside the interval) never held")
        # 30 ms of draining is long past a decode of a few ms, and the
        # frame must still be pending.
        self._drain(30)
        self.assertEqual(self.item._stats.decodes_rendered, 1,
                         "the completion handed a frame over inside the cap")
        self.assertIsNotNone(self.item._pending_frame,
                             "the held frame was consumed by nothing")
        # Held, not lost: the next tick displays it.
        self._tick_and_install()
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 41)
        self.assertEqual(self.item.framesDisplayed, 2)

    def test_the_tick_does_not_stack_a_hand_over_on_a_completion(self):
        # A completion's hand-over is this period's extra one, not a
        # second cadence: the tick behind it must not hand another frame
        # over inside the interval — even when the decode that followed
        # the completion was quick enough to have freed the worker again.
        # A delivery that lands long after its own decode is the shape a
        # busy Qt thread produces, and it is where the interval test
        # alone would let the tick stack.
        submitted = []
        self.item._decoder = SimpleNamespace(submit=lambda *args: submitted.append(args),
                                             stop=lambda: True)
        self.item.setTargetFps(50.0)
        clock = SimpleNamespace(perf_counter=lambda: now[0])
        now = [100.0]
        with mock.patch.dict(self.item._render.__globals__, time=clock):
            self.item._pending_frame = b"first"
            self.item._render()
            self.item._decode_in_flight = 0
            self.item._pending_frame = b"second"
            now[0] += 0.031  # the completion is later than the cap
            self.item._dispatch_from_completion()
            self.item._decode_in_flight = 0
            self.item._pending_frame = b"third"
            now[0] += 0.001
            self.item._render()
            self.assertEqual(len(submitted), 2, "the tick shortened the cap")
            self.assertEqual(self.item._pending_frame, b"third")
            now[0] += 0.020
            self.item._render()
            self.assertEqual(len(submitted), 3, "an eligible tick was wasted")
            self.assertEqual(submitted[-1][1], b"third")

    def test_a_frame_arriving_after_completion_does_not_wait_for_a_tick(self):
        self._start()
        self.item._render_timer.stop()
        submitted = []
        self.item._decoder.stop()
        self.item._decoder = SimpleNamespace(submit=lambda *args: submitted.append(args),
                                             stop=lambda: True)
        first = _jpeg(40, 30, shade=40)
        self._reply().deliver(_multipart(first))
        self.assertEqual(submitted[-1][1], first)
        self.item._decode_in_flight = 0
        # No frame was pending at completion. The next arrival alone
        # must wake the idle decoder after the cap has elapsed.
        self.item._last_dispatch_at = time.perf_counter() - 1.0
        second = _jpeg(40, 30, shade=41)
        self._reply().deliver(_multipart(second))
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[-1][1], second)
        self.assertIsNone(self.item._pending_frame)

    def test_a_pending_successor_wakes_at_the_deadline_without_another_tick(self):
        self.item.setTargetFps(10.0)
        self._start()
        self.item._render_timer.stop()
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=40)))
        self.item._render()
        # The queued completion cannot run until events are pumped,
        # so the successor is guaranteed to be waiting when it lands.
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=41)))
        self.assertTrue(self._drain_until(lambda: self.item.framesDisplayed == 2),
                        "the successor waited for a periodic tick that never came")
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 41)
        self.assertFalse(self.item._dispatch_timer.isActive())

    def test_the_round_trip_is_reported_split_by_where_it_stalls(self):
        # The submit-to-install trip is the number the display rate is
        # a symptom of, so it is reported per frame and split into the
        # shares that stall independently: the wait for the worker, the
        # decode, and the trip back to a Qt thread that may be busy.
        real = QImage.fromData

        def slow(frame):
            time.sleep(0.03)
            return real(frame)

        self._patch_decode(slow)
        self._start()
        self.item._render_timer.stop()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._tick_and_install()
        self.assertEqual(self.item._stats.decodes_rendered, 1)
        self.item._emit_stats()
        round_trip = self.item._stats.recent_round_trip_ms
        # The injected decode is the floor: the trip carries it plus
        # whatever the hand-over and the return cost.
        self.assertGreaterEqual(round_trip, 30.0)
        # The three shares are the trip, and each is accounted where it
        # happens: a split that dropped a share would not sum back. The
        # four readings share one precision, so their sums differ by
        # rounding alone — 0.02 at the worst, against the 0.05 here.
        self.assertAlmostEqual(
            round_trip,
            self.item._stats.recent_queue_wait_ms + self.item._stats.recent_decode_ms_per_frame
            + self.item._stats.recent_delivery_ms,
            places=1,
            msg="the round trip's shares do not add up to the round trip")
        # Both meters are accumulated from perf_counter: the trip is the
        # queue wait, the decode and the Qt thread's own return, and the
        # lag is that trip plus the parse-to-dispatch gap in front of it,
        # every term a span of the one clock. Only the reporting
        # separates them — a decimal for the lag, a hundredth for the
        # trip — so 0.055, half of each, is all rounding can show; the
        # 1.0 it replaces was wider than the real inversion it hid.
        self.assertGreaterEqual(
            self.item._stats.recent_display_lag_ms,
            round_trip - 0.055,
            "the frame's age at the screen is younger than the trip "
            "that put it there")
        # The interval's own maximum, which the stats tick consumes: the
        # trip carries the decode, so it cannot be the shorter of the two.
        # Both sides at ONE precision: the reported maximum is rounded to
        # a decimal and the raw decode maximum is not, and rounding only
        # one side of a comparison breaks its monotonicity — 30.6 against
        # 30.6279 is a red leg that measured nothing. Rounding both keeps
        # the ordering exact, since a >= b implies round(a) >= round(b),
        # so this costs the assertion none of its power.
        self.assertGreaterEqual(self.item._stats.recent_round_trip_ms_max,
                                round(self.item._stats.decode_ms_max, 1))



if __name__ == "__main__":
    unittest.main()
