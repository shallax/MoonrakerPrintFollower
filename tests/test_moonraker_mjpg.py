"""Camera pipeline integration contracts, isolated by test-file process."""
import time
import unittest
from tests.mjpg_test_support import QT_AVAILABLE

if QT_AVAILABLE:
    from tests.mjpg_test_support import (
        QUrl, QColor, QImage,
        QBuffer, QIODevice, runtime, MAX_HEADER_BYTES, MAX_IN_PROGRESS_FRAME_BYTES, RETAINED_GARBAGE_LIMIT,
        MoonrakerMJPGImage, FakeReply, FakeNam,
        _jpeg, _multipart, _chunked,
    )


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MoonrakerMJPGImageTests(unittest.TestCase):
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

    def test_camera_requests_the_framebuffer_render_target(self):
        from PyQt6.QtQuick import QQuickPaintedItem
        self.assertEqual(self.item.renderTarget(),
                         QQuickPaintedItem.RenderTarget.FramebufferObject)

    def _drain(self, milliseconds):
        """Run the event loop so the render timer fires its ticks."""
        self.qt.events(milliseconds)

    def _drain_until(self, predicate, milliseconds=4000):
        """Pump the event loop until the predicate holds. The stats
        cadence is real time, so a fixed sleep would make the pin a
        race against it — this waits for the tick it wants."""
        deadline = time.monotonic() + milliseconds / 1000.0
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.qt.events(25)
        return predicate()

    def _await(self, predicate, message, milliseconds=4000):
        """Wait for a record the code sets, then let one more land.

        The render timer is real time, so a fixed drain in front of an
        exact count reads the machine when the tick is late; the
        settle behind the wait keeps the nothing-more-happened pins
        measuring the renderer rather than the length of the wait.
        """
        self.assertTrue(self._drain_until(predicate, milliseconds), message)
        self._drain(50)

    def _tick_and_install(self):
        """Drive one render tick by hand and wait for its frame to reach
        the screen. The decode runs off the Qt thread, so the tick only
        hands the frame over — the install lands on a later Qt turn."""
        rendered = self.item._stats.decodes_rendered
        self.item._render()
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.decodes_rendered > rendered),
            "the decoded frame never reached the screen")

    def _start(self, content_type=b"multipart/x-mixed-replace; boundary=mpfboundary"):
        def _get_with_type(request):
            reply = FakeReply(content_type)
            self.nam.requests.append(reply)
            return reply
        self.nam.get = _get_with_type
        self.item.start()

    def test_snapshot_polling_closes_each_reply_and_never_overlaps_requests(self):
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/snapshot"))
        self.item.setTargetFps(1.0)
        self.item.setSnapshotMode(True)
        self.item.start()
        first = self._reply()
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self.item._image_request.url().path(), "/snapshot")
        self.assertEqual(bytes(self.item._image_request.rawHeader(b"Cache-Control")), b"no-cache")
        self.item._begin_snapshot_request()
        self.assertEqual(len(self.nam.requests), 1, "a slow response blocks another GET")
        first.complete(_jpeg(40, 30))
        self.assertEqual(self.item.framesParsed, 1)
        self.assertIsNone(self.item._image_reply)
        self.assertEqual(first._aborted, 0)
        self.assertTrue(self.item._snapshot_timer.isActive())
        self.assertEqual(self.item._snapshot_timer.interval(), 1000)
        self.item._snapshot_timer.stop()
        self.item._begin_snapshot_request()
        self.assertEqual(len(self.nam.requests), 2)
        self.item.setTargetFps(0.5)
        self._reply().complete(_jpeg(40, 30))
        self.assertEqual(self.item._snapshot_timer.interval(), 2000)
        self.item.stop()
        self.item._begin_snapshot_request()
        self.assertEqual(len(self.nam.requests), 2, "stop prevents later polling")

    def test_stale_reply_callbacks_leave_the_current_snapshot_request_alone(self):
        self.item.setSnapshotMode(True)
        self.assertTrue(self.item.getSnapshotMode())
        self.item.setSnapshotMode(True)  # Reapplying the mode cannot reconnect.
        self.item.start()
        active = self._reply()
        stale = FakeReply()
        self.item._on_finished(stale)
        self.item._on_snapshot_finished(stale)
        self.item._on_error(stale)
        self.assertIs(self.item._image_reply, active)
        self.assertTrue(self.item._started)
        self.assertEqual(self.item.transportErrors, 0)
        self.assertEqual(len(self.nam.requests), 1)

    def test_switching_to_snapshot_mode_aborts_the_mjpeg_connection(self):
        self._start()
        stream = self._reply()
        self.item.setTargetFps(1.0)
        self.item.setSnapshotMode(True)
        self.assertEqual(stream._aborted, 1)
        self.assertTrue(stream._deleted_later)
        self.assertIsNot(self._reply(), stream)
        self._reply().complete(_jpeg(40, 30))
        self.assertTrue(self.item._snapshot_timer.isActive())
        self.item.setSnapshotMode(False)
        self.assertFalse(self.item._snapshot_timer.isActive())
        self.assertEqual(len(self.nam.requests), 3)

    def test_stalled_snapshot_is_aborted_before_retrying(self):
        self.item.setTargetFps(0.5)
        self.item.setSnapshotMode(True)
        self.item.start()
        first = self._reply()
        self.assertTrue(self.item._snapshot_timeout_timer.isActive())
        self.item._on_snapshot_timeout()
        self.assertEqual(first._aborted, 1)
        self.assertIsNone(self.item._image_reply)
        self.assertEqual(self.item.transportErrors, 1)
        self.assertTrue(self.item._snapshot_timer.isActive())
        self.assertEqual(self.item._snapshot_timer.interval(), 2000)
        self.item._snapshot_timer.stop()
        self.item._begin_snapshot_request()
        self.assertEqual(len(self.nam.requests), 2)

    def test_snapshot_response_can_be_png_and_is_decoded_once(self):
        image = QImage(32, 24, QImage.Format.Format_RGB888)
        image.fill(QColor(20, 120, 60))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.item.setSnapshotMode(True)
        self.item.setTargetFps(1.0)
        self.item.start()
        self._reply().complete(bytes(buffer.data()))
        self.assertEqual(self.item.framesParsed, 1)
        self._await(lambda: self.item.framesDisplayed == 1,
                    "the one-shot PNG never reached the screen")
        self.assertEqual((self.item.imageWidth, self.item.imageHeight), (32, 24))

    def test_a_one_frame_fragmented_across_many_chunks_reconstructs(self):
        self._start()
        frame = _jpeg(40, 30)
        body = _multipart(frame)
        for chunk in _chunked(body, 3):  # fragment hard
            self._reply().deliver(chunk)
        self.assertEqual(self.item.framesParsed, 1)
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the reassembled frame never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)
        self.assertEqual(self.item.imageWidth, 40)
        self.assertEqual(self.item.imageHeight, 30)

    def test_b_one_chunk_with_many_frames_displays_only_the_newest(self):
        self._start()
        frames = [_jpeg(40, 30, shade=40 + index) for index in range(6)]
        body = b"".join(_multipart(frame) for frame in frames)
        self._reply().deliver(body)
        self.assertEqual(self.item.framesParsed, 6)
        # One render tick decodes the newest only; the five older
        # frames count as intentionally dropped.
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the newest frame never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)
        self.assertEqual(self.item.framesDropped, 5)

    def test_c_more_than_2mb_of_valid_frames_never_reconnects(self):
        # The Cura regression: 2 MB of VALID concatenated frames used
        # to restart the stream. Here: no abort, no new request, the
        # buffer stays bounded, the newest frame survives.
        self._start()
        frame = _jpeg(256, 256)
        body = _multipart(frame)
        total = 0
        while total < 2 * 1000 * 1000 + 500 * 1000:
            self._reply().deliver(body)
            total += len(body)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)
        self.assertLess(len(self.item._parser.buffer), len(frame) + 4096)
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the surviving frame never reached the screen")
        self.assertLessEqual(self.item.framesDisplayed, 2,
                             "the burst queued intermediate frames")
        self.assertEqual(self.item.imageWidth, 256)

    def test_d_unframed_garbage_stays_bounded_and_never_thrashes(self):
        # The parsers trim markerless garbage EAGERLY — a garbage feed
        # stays bounded without ever reaching the resync backstop or
        # the connection.
        self._start()
        for _ in range(int(RETAINED_GARBAGE_LIMIT / 100000) + 2):
            self._reply().deliver(b"x" * 100000)
        self.assertEqual(self.item.parserResyncs, 0)
        self.assertLess(len(self.item._parser.buffer), 100)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)

    def test_d2_the_garbage_backstop_resyncs_the_pathological_state(self):
        # The RETAINED_GARBAGE_LIMIT backstop itself: an unframed
        # buffer beyond the bound (a state the eager trims normally
        # prevent) is discarded with a counted resync — the connection
        # is never the response.
        self._start(content_type=b"application/octet-stream")
        self.item._parser.buffer = bytearray(b"z" * (RETAINED_GARBAGE_LIMIT + 1))
        self.item._parser.apply_limits()
        self.assertGreater(self.item.parserResyncs, 0)
        self.assertEqual(len(self.item._parser.buffer), 0)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)

    def test_e_constant_resolution_fires_image_size_changed_once(self):
        self._start()
        emissions = []
        self.item.imageSizeChanged.connect(lambda: emissions.append(1))
        frame = _jpeg(40, 30)
        for index in range(5):
            self._reply().deliver(_multipart(frame))
            self._await(
                lambda shown=index + 1: self.item.framesDisplayed >= shown,
                "a frame of the run never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 5)
        self.assertEqual(len(emissions), 1)

    def test_f_resolution_changes_fire_once_per_change(self):
        self._start()
        emissions = []
        self.item.imageSizeChanged.connect(lambda: emissions.append(1))
        for index in range(3):
            self._reply().deliver(_multipart(_jpeg(40, 30)))
            self._await(
                lambda shown=index + 1: self.item.framesDisplayed >= shown,
                "a frame of the first resolution never reached the screen")
        for index in range(3, 6):
            self._reply().deliver(_multipart(_jpeg(64, 48)))
            self._await(
                lambda shown=index + 1: self.item.framesDisplayed >= shown,
                "a frame of the second resolution never reached the screen")
        # Once for the initial resolution, once for the change.
        self.assertEqual(len(emissions), 2)
        self.assertEqual(self.item.imageWidth, 64)
        self.assertEqual(self.item.imageHeight, 48)

    def test_g_source_change_while_running_is_one_transition(self):
        self._start()
        first = self._reply()
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/other"))
        self.assertEqual(len(self.nam.requests), 2)
        self.assertEqual(first._aborted, 1)
        self.assertIsNot(self._reply(), first)

    def test_h_reapplying_the_identical_state_is_a_no_op(self):
        self._start()
        self.item.start()  # the duplicate application
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/webcam"))  # identical
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)

    def test_i_a_bursty_30fps_feed_paces_the_display_without_latency(self):
        # A burstier feed than the render ceiling: every frame parses,
        # the display paces to the timer, the superseded frames drop,
        # and the last displayed frame is the newest delivered.
        self._start()
        frames = [_jpeg(40, 30, shade=40 + index) for index in range(24)]
        for frame in frames:
            self._reply().deliver(_multipart(frame))
        # A wait, not a window: the burst is parsed by the deliveries
        # themselves, and all this waits for is the one display the
        # newest of them wins.
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the burst's newest frame never reached the screen")
        self.assertEqual(self.item.framesParsed, 24)
        self.assertLess(self.item.framesDisplayed, 24)
        self.assertGreater(self.item.framesDropped, 0)
        # Every frame is either displayed, dropped or the newest
        # pending — the mid-burst render ticks may display some, and
        # the accounting must close.
        self.assertLessEqual(self.item.framesDisplayed + self.item.framesDropped,
                             self.item.framesParsed)
        self.assertIn(self.item.framesParsed - (self.item.framesDisplayed
                                                + self.item.framesDropped), (0, 1))
        # The newest frame wins the display: the one displayed above
        # is the last frame's shade (63) and never an older one.
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 63)
        # No reconnect anywhere in the burst.
        self.assertEqual(self._reply()._aborted, 0)

    def test_an_overlimit_declared_frame_is_dropped_without_reconnecting(self):
        # A single declared frame beyond the documented maximum:
        # pathological — dropped with a counted resync, the stream
        # continues.
        self._start()
        header = (b"--mpfboundary\r\nContent-Type: image/jpeg\r\n"
                  b"Content-Length: " + str(MAX_IN_PROGRESS_FRAME_BYTES + 1).encode()
                  + b"\r\n\r\n")
        self._reply().deliver(header)
        self.assertGreater(self.item.parserResyncs, 0)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)
        # The stream stays healthy: a normal frame still displays.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame after the resync never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_stop_releases_the_reply_and_disconnects_its_signals(self):
        # The QNAM survives start/stop cycles, so every stopped reply
        # must be deleteLater'd — and its late signals must never
        # reach the new state.
        self._start()
        reply = self._reply()
        self.item.stop()
        self.assertTrue(reply._deleted_later)
        self.assertEqual(self.item._image_reply, None)
        reply.deliver(b"late bytes after stop")  # a stale readyRead
        self.assertEqual(self.item.bytesReceived, 0)

    def test_image_size_changed_fires_after_the_image_lands(self):
        # The notify must expose the NEW dimensions: imageWidth and
        # imageHeight read the new image at emit time.
        self._start()
        seen = []
        self.item.imageSizeChanged.connect(
            lambda: seen.append((self.item.imageWidth, self.item.imageHeight)))
        self._reply().deliver(_multipart(_jpeg(64, 48)))
        self._await(lambda: len(seen) >= 1,
                    "the size change was never announced")
        self.assertEqual(seen, [(64, 48)])

    def test_clearing_the_frame_re_announces_the_size(self):
        # The blank is a real size change (NxM -> 0x0) and the
        # remembered rect goes with it: the stream off/on left zoom and
        # FPS dead because the resumed stream's first frame repeated
        # the old resolution, announced nothing, and the consumer that
        # gated on imageWidth kept the value it latched before the
        # blank.
        self._start()
        seen = []
        self.item.imageSizeChanged.connect(
            lambda: seen.append((self.item.imageWidth, self.item.imageHeight)))
        self._reply().deliver(_multipart(_jpeg(64, 48)))
        self._await(lambda: len(seen) >= 1,
                    "the first frame never announced its size")
        self.assertEqual(seen, [(64, 48)])

        self.item.clearFrame()
        self.assertEqual((self.item.imageWidth, self.item.imageHeight), (0, 0),
                         "a blanked frame reports no size")
        self.assertEqual(seen, [(64, 48), (0, 0)], "the blank is announced")

        # The resume: the SAME resolution on the same reply.
        self._reply().deliver(_multipart(_jpeg(64, 48)))
        self._await(lambda: len(seen) >= 3,
                    "the resume never re-announced the size")
        self.assertEqual(seen, [(64, 48), (0, 0), (64, 48)],
                         "the first frame after a blank re-announces its size")

        # Nothing changed means nothing announced: the notify stays a
        # real-change signal, however often the pane blanks.
        self.item.clearFrame()
        self.item.clearFrame()
        self.assertEqual(len(seen), 4)

    def test_an_unterminated_oversized_part_is_discarded_not_reconnected(self):
        # A part that never terminates (no declared length, no next
        # marker) must not grow without bound: once the in-progress
        # span passes the documented maximum, it is dropped with a
        # counted resync and the connection survives.
        self._start()
        header = b"--mpfboundary\r\nContent-Type: image/jpeg\r\n\r\n"
        self._reply().deliver(header)
        for _ in range(int(MAX_IN_PROGRESS_FRAME_BYTES / 1000000) + 2):
            self._reply().deliver(b"x" * 1000000)
        self.assertGreater(self.item.parserResyncs, 0)
        self.assertLess(len(self.item._parser.buffer), 1000)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)

    def test_the_boundary_comes_from_the_raw_content_type_header(self):
        # The multipart parser activates from rawHeader(b"Content-Type")
        # — the parsed-header accessor is the wrong type. A quoted
        # boundary and the closing marker exercise the raw parse.
        self._start(content_type=b'multipart/x-mixed-replace; boundary="mpfboundary"')
        frame = _jpeg(40, 30)
        self._reply().deliver(_multipart(frame))
        self._reply().deliver(b"--mpfboundary--")
        self.assertEqual(self.item.framesParsed, 1)
        self.assertEqual(len(self.item._parser.buffer), 0)
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame of the quoted-boundary stream never reached "
                    "the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_a_stream_transition_resets_the_parser_state(self):
        # A genuinely new stream must not inherit the old source's
        # partial frame, buffer or boundary.
        self._start()
        self._reply().deliver(b"--mpfboundary\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8partial")
        self.assertGreater(len(self.item._parser.buffer), 0)
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/other"))  # running: one transition
        self.assertEqual(len(self.item._parser.buffer), 0)
        self.assertIsNone(self.item._pending_frame)
        self.assertIsNone(self.item._parser.boundary)

    def test_the_stats_snapshot_emits_when_the_counters_move(self):
        # The diagnostics properties mutate and ride one low-frequency
        # statsChanged signal — never per-frame notifications.
        self._start()
        emissions = []
        self.item.statsChanged.connect(lambda: emissions.append(1))
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertEqual(emissions, [])  # no per-frame emission
        # The wait is on the emission itself: a window the cadence had
        # to land inside would have read the machine when it did not.
        self._await(lambda: len(emissions) >= 1,
                    "the stats never reported the moving counters")
        self.assertGreater(len(emissions), 0)
        self.assertEqual(self.item.framesParsed, 1)

    def test_trace_enabled_is_a_real_property(self):
        changed = []
        self.item.traceEnabledChanged.connect(lambda: changed.append(1))
        self.item.setTraceEnabled(True)
        self.assertTrue(self.item.getTraceEnabled())
        self.assertEqual(changed, [1])
        self.item.setTraceEnabled(True)  # idempotent
        self.assertEqual(changed, [1])

    def test_the_explicit_counters_name_their_events(self):
        self._start()
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/other"))  # running: transition
        self.assertEqual(self.item.requestsStarted, 2)
        # Two changes: the setUp's initial assignment plus this one.
        self.assertEqual(self.item.sourceChanges, 2)
        self.assertEqual(self.item.transportErrors, 0)
        self._reply().errorOccurred.emit(99)
        self.assertEqual(self.item.transportErrors, 1)

    def test_the_render_timer_uses_the_precise_timer_type(self):
        from PyQt6.QtCore import Qt
        self.assertEqual(self.item._render_timer.timerType(),
                         Qt.TimerType.PreciseTimer)

    def test_an_oversized_header_block_resynchronises(self):
        # MAX_HEADER_BYTES is enforced: a header block that outgrows
        # its bound without a terminator is dropped at the marker,
        # and a healthy part still parses afterwards.
        self._start()
        self._reply().deliver(b"--mpfboundary" + b"X" * (MAX_HEADER_BYTES + 100))
        self.assertGreater(self.item.parserResyncs, 0)
        # Only the marker-straddle tail may remain (bounded).
        self.assertLessEqual(len(self.item._parser.buffer), len(b"--mpfboundary"))
        self.assertEqual(self._reply()._aborted, 0)
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame after the oversized header never reached "
                    "the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_nonsensical_content_lengths_fall_back_to_the_scan(self):
        # Content-Length <= 0 or non-numeric is not usable framing:
        # the SOI/EOI scan within the part carries the frame.
        self._start()
        frame = _jpeg(40, 30)
        for index, declared in enumerate((b"0", b"-5", b"abc"), start=1):
            body = (b"--mpfboundary\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + declared + b"\r\n\r\n" + frame + b"\r\n")
            self._reply().deliver(body)
            # Each frame is displayed before the next is delivered: a
            # fixed drain lets the next delivery supersede a frame a
            # late tick had not reached yet, and the count below then
            # reads the machine.
            self._await(
                lambda shown=index: self.item.framesDisplayed >= shown,
                "the frame the scan path carried never reached the screen")
        self.assertEqual(self.item.framesParsed, 3)
        self.assertEqual(self.item.framesDisplayed, 3)

    def test_a_double_dash_boundary_normalises(self):
        # boundary=--foo is the non-compliant real-world form: the
        # delimiter must be --foo, never ----foo.
        self._start(content_type=b"multipart/x-mixed-replace; boundary=--mpfboundary")
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertEqual(self.item.framesParsed, 1)
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame of the normalised boundary never reached "
                    "the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_a_fragmented_boundary_reconstructs(self):
        # The boundary itself split across read chunks must still
        # frame the part.
        self._start()
        body = _multipart(_jpeg(40, 30))
        split = body.index(b"boundary") + 3
        self._reply().deliver(body[:split])
        self._reply().deliver(body[split:])
        self.assertEqual(self.item.framesParsed, 1)
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame of the split boundary never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_a_complete_oversized_jpeg_is_rejected_in_every_path(self):
        # The central choke point: a COMPLETE frame beyond the
        # maximum is rejected before any decode, in the raw scan and
        # the multipart-no-Content-Length paths alike — never by
        # reconnecting.
        huge = b"\xff\xd8" + b"x" * (MAX_IN_PROGRESS_FRAME_BYTES + 10) + b"\xff\xd9"
        # The raw scan path.
        self._start(content_type=b"application/octet-stream")
        before = self.item._stats.oversized_drops
        self._reply().deliver(huge)
        self.assertEqual(self.item.framesParsed, 0)
        self.assertEqual(self.item._stats.oversized_drops, before + 1)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)
        self.item.stop()
        # The multipart path without Content-Length.
        self._start()
        before = self.item._stats.oversized_drops
        self._reply().deliver(b"--mpfboundary\r\nContent-Type: image/jpeg\r\n\r\n" + huge)
        self.assertEqual(self.item.framesParsed, 0)
        self.assertEqual(self.item._stats.oversized_drops, before + 1)
        self.assertEqual(len(self.nam.requests), 2)  # the two starts, no extra
        self.assertEqual(self._reply()._aborted, 0)
        # A healthy frame still parses afterwards.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame after the rejected one never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_an_oversized_header_with_a_terminator_is_rejected(self):
        # MAX_HEADER_BYTES applies even when the oversized header
        # block eventually terminates.
        self._start()
        self._reply().deliver(
            b"--mpfboundary" + b"X" * (MAX_HEADER_BYTES + 100) + b"\r\n\r\n"
            + _jpeg(40, 30))
        self.assertGreater(self.item.parserResyncs, 0)
        self.assertLessEqual(len(self.item._parser.buffer), len(b"--mpfboundary"))
        self.assertEqual(self._reply()._aborted, 0)
        # The stream stays healthy for the next part.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame after the terminated header never reached "
                    "the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_recent_fps_reflects_the_current_interval(self):
        # The recent FPS come from delta counters over the last stats
        # interval — traffic spread ACROSS an interval counts; a
        # quiet interval reads zero while the lifetime averages
        # persist.
        self._start()
        for _ in range(10):
            self._reply().deliver(_multipart(_jpeg(40, 30)))
            self._drain(120)  # ~10 frames across ~1.2 s
        self.assertEqual(self.item.framesParsed, 10)
        # The rate is the interval's own record, published for the
        # interval that just closed: a fixed window ends on whichever
        # interval falls inside it, and a starved run ends on a quiet
        # one. The traffic runs until the rate it published is not zero.
        deadline = time.monotonic() + 4.0
        while (self.item.recentIncomingFPS <= 0
               and time.monotonic() < deadline):
            self._reply().deliver(_multipart(_jpeg(40, 30)))
            self._drain(120)
        self.assertGreater(self.item.recentIncomingFPS, 0)
        # A quiet interval: the recent rates drop to zero while the
        # lifetime averages stay. The wait is on the drop, which the
        # code makes at its own cadence, not on an interval of wall
        # time that would have to contain that tick.
        self._await(
            lambda: self.item.recentIncomingFPS == 0
            and self.item.recentDisplayedFPS == 0,
            "the recent rates never fell back to zero")
        self.assertEqual(self.item.recentIncomingFPS, 0)
        self.assertEqual(self.item.recentDisplayedFPS, 0)
        self.assertGreater(self.item.incomingFPS, 0)

    def test_a_stale_finished_callback_cannot_touch_the_new_reply(self):
        # A late finished from request A, queued before the source
        # change, must not stop or mutate request B.
        self._start()
        old_reply = self._reply()
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/other"))
        current = self._reply()
        self.assertEqual(len(self.nam.requests), 2)
        old_reply.finished.emit()
        self.assertIs(self.item._image_reply, current)
        self.assertEqual(current._aborted, 0)
        self.assertTrue(self.item._started)

    def test_a_stale_error_callback_cannot_count_on_the_new_reply(self):
        self._start()
        old_reply = self._reply()
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/other"))
        before = self.item.transportErrors
        old_reply.errorOccurred.emit(99)
        self.assertEqual(self.item.transportErrors, before)

    def test_an_oversized_part_is_bounded_immediately_without_more_input(self):
        # The offending remainder is discarded AT THE RESYNC — the
        # buffer is already small before any further chunk arrives.
        self._start()
        header = b"--mpfboundary\r\nContent-Type: image/jpeg\r\n\r\n"
        self._reply().deliver(header)
        self._reply().deliver(b"x" * (MAX_IN_PROGRESS_FRAME_BYTES + 100))
        self.assertLessEqual(len(self.item._parser.buffer), len(b"--mpfboundary"))
        self.assertEqual(self._reply()._aborted, 0)
        # A later valid frame still parses.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the frame after the bounded part never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_a_recent_rate_transition_to_zero_notifies(self):
        # The 30 -> 0 transition in the recent rates must reach the
        # QML even when the raw counters stop moving.
        self._start()
        # Traffic spread so a frame lands inside EVERY stats interval
        # until it stops: 12 frames at 250 ms across ~3 s.
        for _ in range(12):
            self._reply().deliver(_multipart(_jpeg(40, 30)))
            self._drain(250)
        self.assertGreater(self.item.recentDisplayedFPS, 0)
        emissions = []
        self.item.statsChanged.connect(lambda: emissions.append(1))
        # A quiet interval: the rates fall to zero. The wait is on the
        # fall — the transition this test is named for — so a tick that
        # is merely late still lands inside it.
        self._await(
            lambda: self.item.recentDisplayedFPS == 0
            and self.item.recentIncomingFPS == 0,
            "the recent rates never fell back to zero")
        self.assertEqual(self.item.recentDisplayedFPS, 0)
        self.assertEqual(self.item.recentIncomingFPS, 0)
        self.assertGreater(len(emissions), 0)

    def test_the_summary_rides_the_stats_cadence_during_a_render_stall(self):
        # The periodic summary must keep reporting when no frames
        # render — a stalled render cannot silence its own telemetry.
        self._start()
        self.item.traceEnabled = True
        # The observable contract (the mock patch is unreliable
        # under the unittest runtime's double entry): the summary's
        # bookkeeping and the stats cadence advance WITHOUT any frame
        # being rendered.
        before = self.item._trace_summary_at
        # No frames at all, and the wait is longer than the 5 s gate:
        # the summary's own stamp is the record this reads, so a slow
        # run reaches it later rather than not at all.
        self._await(lambda: self.item._trace_summary_at > before,
                    "the summary never rode the stats cadence",
                    milliseconds=8000)
        self.assertGreater(self.item._trace_summary_at, before)
        self.assertGreater(self.item._stats.last_stats_at, 0)

    def test_recent_throughput_tracks_delta_bytes(self):
        self._start()
        frame = _jpeg(40, 30)
        body = _multipart(frame)
        for _ in range(8):
            self._reply().deliver(body)
            self._drain(200)
        # The throughput is published for the interval that just closed,
        # so the traffic runs until one closes with the burst inside it:
        # a fixed window ends on the quiet interval behind the burst,
        # whose zero is the empty interval's and not the meter's.
        deadline = time.monotonic() + 4.0
        while (self.item.recentBytesPerSec <= 0
               and time.monotonic() < deadline):
            self._reply().deliver(body)
            self._drain(200)
        self.assertGreater(self.item.recentBytesPerSec, 0)

    def test_stop_disconnects_the_stored_callbacks(self):
        # _stop_request disconnects the ACTUAL stored lambdas: after
        # the stop, the reply's finished cannot reach the handler at
        # all (the identity guard is the second line of defence).
        self._start()
        reply = self._reply()
        self.item.stop()
        self.assertIsNotNone(self.item._reply_finished_cb)
        self.item._stats.transport_errors = 0
        reply.finished.emit()
        self.assertEqual(self.item._started, False)

    def test_a_new_request_reseeds_the_recent_baseline(self):
        # After a restart, the first interval reports its OWN rate —
        # a long stopped stretch must not dilute it.
        self._start()
        frame = _jpeg(40, 30)
        body = _multipart(frame)
        for _ in range(8):
            self._reply().deliver(body)
            self._drain(150)
        self.item.stop()
        self.item.start()  # a genuinely new request
        self._drain(1100)  # the first emit seeds the new baseline
        self._reply().deliver(body)  # the frame lands in the NEXT interval
        emitted = self.item._stats.last_stats_at
        # The emission that reports the frame is the record, and the
        # stamp moves for every emit: waiting on the interval instead
        # would need a tick to land inside it.
        self._await(lambda: self.item._stats.last_stats_at > emitted,
                    "the interval after the frame never emitted")
        self.assertGreater(self.item.recentIncomingFPS, 0)

    def test_the_drain_records_its_cost_and_the_gap_between_drains(self):
        # The drain is the receive path's share of the Qt thread, and
        # the gap between two drains is the starvation the camera's own
        # send sees. Neither is readable from a frame rate, and a
        # source that cannot be drained is the "frames backing up"
        # complaint's other explanation.
        self._start()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertGreater(self.item._stats.drain_ms_total, 0)
        self.assertGreater(self.item._stats.drain_ms_max, 0)
        started = time.monotonic()
        self._drain(300)  # nothing arrives: the Qt thread is elsewhere
        waited = (time.monotonic() - started) * 1000.0
        self.assertGreaterEqual(waited, 250)
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertGreaterEqual(self.item._stats.drain_gap_ms_max, waited * 0.8)

    def test_the_display_lag_is_the_age_of_the_frame_that_reached_the_screen(
            self):
        # The latency the viewer actually sees. A frame that arrived
        # long before it was decoded is late by its AGE, not by its
        # decode time — which is what "behind reality" means.
        self._start()
        self.item._render_timer.stop()
        self.item._decode_in_flight = -1  # simulate an occupied decoder
        frame = _jpeg(40, 30)
        self._reply().deliver(_multipart(frame))
        started = time.monotonic()
        self._drain(250)  # the frame waits while the Qt thread is busy
        waited = (time.monotonic() - started) * 1000.0
        self.item._decode_in_flight = 0
        self._tick_and_install()
        held = self.item._stats.display_lag_ms_total
        self.assertGreaterEqual(held, waited * 0.8)
        # A frame rendered at once is a small lag: the measurement is
        # THIS frame's age, not a constant and not a stale stamp.
        self._reply().deliver(_multipart(frame))
        self._tick_and_install()
        self.assertLess(self.item._stats.display_lag_ms_total - held, waited * 0.5)

    def test_the_stats_interval_separates_parsed_from_displayed(self):
        # The phase diagnosis needs both counts over the SAME interval:
        # parsed-but-not-displayed is the backlog, and the Qt-thread
        # share says whether the plugin or the source owns it.
        self._start()
        # The workload is SIZED to the metric's own resolution. The
        # published shares are display-rounded (1 and 2 decimals), so
        # four 40x30 frames decoded faster than the rounding and the
        # shares read 0.0 on a fast runner — the live failure was
        # exactly that at one decimal. Real frames decode measurably.
        frames = [_jpeg(640, 480, shade=50 + index) for index in range(4)]
        self._reply().deliver(b"".join(_multipart(item) for item in frames))
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.recent_parsed_count == 4),
            "the stats tick never closed an interval over the burst")
        self.assertEqual(self.item._stats.recent_displayed_count, 1)
        # The stats timer is a coarse QTimer: 1 s of interval with Qt's
        # 5% tolerance, not a lifetime and not a guess.
        self.assertGreaterEqual(self.item._stats.recent_interval_s, 0.9)
        self.assertLessEqual(self.item._stats.recent_interval_s, 1.5)
        # A JPEG can decode in less than 0.05 ms on a fast
        # runner. The diagnostic deliberately rounds to tenths, so
        # zero is a valid displayed rate. Check the measured work and
        # the per-frame arithmetic instead of requiring a slow CPU.
        self.assertGreater(self.item._stats.decode_ms_total, 0)
        self.assertGreater(self.item._stats.drain_ms_total, 0)
        self.assertEqual(self.item._stats.recent_decodes, 1)
        self.assertGreaterEqual(self.item._stats.recent_decode_ms, 0)
        self.assertEqual(self.item._stats.recent_decode_ms_per_frame,
                         round(self.item._stats.decode_ms_total, 2))
        self.assertEqual(self.item._stats.recent_drain_ms_per_frame,
                         round(self.item._stats.drain_ms_total / 4, 2))

    def test_the_oldest_buffered_frame_reports_its_age(self):
        # A frame that parsed but never rendered is invisible to both
        # rates; its age at the tick is the backlog the user feels.
        self._start()
        self.item._render_timer.stop()
        self.item._decode_in_flight = -1  # the decoder cannot accept the frame
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.recent_pending_age_ms > 0),
            "the stats tick never reported the buffered frame")
        self.assertEqual(self.item.framesParsed, 1)
        self.assertEqual(self.item.framesDisplayed, 0)
        self.assertGreaterEqual(self.item._stats.recent_pending_age_ms, 200)

    def test_the_interval_maxima_are_reset_by_the_tick(self):
        # A maximum that never reset would report an old spike as the
        # current interval's worst: the maxima belong to the interval
        # they were measured in, so the fresh interval reads low.
        self._start()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        started = time.monotonic()
        self._drain(300)
        self.assertGreaterEqual((time.monotonic() - started) * 1000.0, 250)
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertGreaterEqual(self.item._stats.drain_gap_ms_max, 250)
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.recent_drain_gap_ms >= 250),
            "the tick never harvested the interval's maxima")
        self.assertEqual(self.item._stats.drain_gap_ms_max, 0.0)

    def test_the_summary_line_renders_every_number_it_promises(self):
        # The line the owner pastes: the format and its values must
        # stay in step (a mismatch raises), and the interval's own
        # parsed/displayed counts, the app state and the oldest
        # buffered frame's age must all be on it.
        self._start()
        # A rate the pane actually asked for: at the default 0 the two
        # fields below would read 0 ms/0.0 fps and a hardcoded constant
        # would satisfy them, which is the pin measuring nothing.
        self.item.setTargetFps(25.0)
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertTrue(
            self._drain_until(lambda: self.item._stats.recent_displayed_count >= 1),
            "the stats tick never reported the displayed frame")
        fmt, values = self.item._stats.summary(time.monotonic(), self.item.getRenderIntervalMs(), self.item.getTargetFps())
        line = fmt % values
        self.assertIn(self.item._stats.app_state,
                      ("active", "inactive", "hidden", "suspended", "unknown"))
        self.assertIn("[%s]" % self.item._stats.app_state, line)
        self.assertIn("parsed %d frames" % self.item._stats.recent_parsed_count, line)
        self.assertIn("displayed %d frames" % self.item._stats.recent_displayed_count, line)
        # The cadence the pane asked for, beside what came out: the pair
        # is what tells a rate capped by the request from a rate the Qt
        # thread could not keep up with.
        self.assertEqual(self.item._render_timer.interval(), 40)
        self.assertIn("render tick 40 ms", line)
        self.assertIn("asked 25.0 fps", line)
        self.assertIn("pending frame age %.1f ms" % self.item._stats.recent_pending_age_ms,
                      line)
        self.assertIn("Qt thread", line)

    def test_the_soi_eoi_fallback_covers_streams_without_multipart(self):
        # No usable Content-Type: the raw concatenated-JPEG scan must
        # carry the stream.
        self._start(content_type=b"application/octet-stream")
        frame = _jpeg(40, 30)
        self._reply().deliver(frame[:7])
        self._reply().deliver(frame[7:])
        self.assertEqual(self.item.framesParsed, 1)
        self._await(lambda: self.item.framesDisplayed >= 1,
                    "the scanned frame never reached the screen")
        self.assertEqual(self.item.framesDisplayed, 1)
        self.assertEqual(self.item.imageWidth, 40)



if __name__ == "__main__":
    unittest.main()
