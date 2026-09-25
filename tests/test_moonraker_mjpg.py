"""The plugin-owned MJPEG renderer (the fork of Cura's
NetworkMJPGImage): the receive/parse/latest-wins/render-scheduler
contracts. Qt-guarded — the container runs them for real against the
production class with a fake network manager and a fake reply."""

import os
import threading
import time
import unittest

# The window paint test needs a screen: the offscreen platform,
# set before any Qt import (the real-engine file's pattern).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QByteArray, QObject, QThread, QUrl, pyqtSignal
    from PyQt6.QtGui import QColor, QImage
    from qt_runtime_support import QT_AVAILABLE, runtime
    if QT_AVAILABLE:
        _started = runtime()
        _started.__enter__()
        try:
            from plugins.MoonrakerMJPGImage import (
                MAX_HEADER_BYTES,
                MAX_IN_PROGRESS_FRAME_BYTES,
                MoonrakerMJPGImage,
                RENDER_INTERVAL_MS,
                RETAINED_GARBAGE_LIMIT,
            )
        finally:
            _started.__exit__(None, None, None)
except ImportError:
    QT_AVAILABLE = False


if QT_AVAILABLE:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtNetwork import QNetworkRequest

    def _jpeg(width: int, height: int, shade: int = 120) -> bytes:
        """A small real JPEG (the decode path must accept it). The
        fill takes a QColor: QImage.fill(int) packs the value into a
        single channel, which would make the shade assertions read the
        wrong channel."""
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(QColor(shade, shade, shade))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPG", 85)
        return bytes(buffer.data())

    def _multipart(frame: bytes, boundary: bytes = b"mpfboundary") -> bytes:
        return (b"--" + boundary + b"\r\nContent-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                + frame + b"\r\n")

    class FakeReply(QObject):
        readyRead = pyqtSignal()
        finished = pyqtSignal()
        errorOccurred = pyqtSignal(int)

        def __init__(self, content_type=b""):
            super().__init__()
            self._chunks = []
            self._aborted = 0
            self._deleted_later = False
            self._content_type = content_type

        def readAll(self) -> QByteArray:
            data = b"".join(self._chunks)
            self._chunks = []
            return QByteArray(data)

        def deliver(self, data: bytes) -> None:
            self._chunks.append(data)
            self.readyRead.emit()

        def abort(self) -> None:
            self._aborted += 1

        def isFinished(self) -> bool:
            return False

        def deleteLater(self) -> None:
            self._deleted_later = True

        def rawHeader(self, name) -> QByteArray:
            if name == b"Content-Type":
                return QByteArray(self._content_type)
            return QByteArray()

    class FakeNam(QObject):
        def __init__(self):
            super().__init__()
            self.requests = []

        def get(self, request: QNetworkRequest) -> FakeReply:
            content_type = b""
            # The reply the test wants: default to multipart so the
            # framing path runs; a test may swap the type after.
            reply = FakeReply(content_type)
            self.requests.append(reply)
            return reply

    def _chunked(raw: bytes, size: int):
        for offset in range(0, len(raw), size):
            yield raw[offset:offset + size]


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

    def _tick_and_install(self):
        """Drive one render tick by hand and wait for its frame to reach
        the screen. The decode runs off the Qt thread, so the tick only
        hands the frame over — the install lands on a later Qt turn."""
        rendered = self.item._decodes_rendered
        self.item._render()
        self.assertTrue(
            self._drain_until(lambda: self.item._decodes_rendered > rendered),
            "the decoded frame never reached the screen")

    def _start(self, content_type=b"multipart/x-mixed-replace; boundary=mpfboundary"):
        def _get_with_type(request):
            reply = FakeReply(content_type)
            self.nam.requests.append(reply)
            return reply
        self.nam.get = _get_with_type
        self.item.start()

    def test_a_one_frame_fragmented_across_many_chunks_reconstructs(self):
        self._start()
        frame = _jpeg(40, 30)
        body = _multipart(frame)
        for chunk in _chunked(body, 3):  # fragment hard
            self._reply().deliver(chunk)
        self.assertEqual(self.item.framesParsed, 1)
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)
        self.assertEqual(self.item.imageWidth, 40)
        self.assertEqual(self.item.imageHeight, 30)

    def test_b_one_chunk_with_many_frames_displays_only_the_newest(self):
        self._start()
        frames = [_jpeg(40, 30, shade=40 + index) for index in range(6)]
        body = b"".join(_multipart(frame) for frame in frames)
        self._reply().deliver(body)
        self.assertEqual(self.item.framesParsed, 6)
        self._drain(80)
        # One render tick decodes the newest only; the five older
        # frames count as intentionally dropped.
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
        self.assertLess(len(self.item._stream_buffer), len(frame) + 4096)
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)
        self.assertEqual(self.item.imageWidth, 256)

    def test_d_unframed_garbage_stays_bounded_and_never_thrashes(self):
        # The parsers trim markerless garbage EAGERLY — a garbage feed
        # stays bounded without ever reaching the resync backstop or
        # the connection.
        self._start()
        for _ in range(int(RETAINED_GARBAGE_LIMIT / 100000) + 2):
            self._reply().deliver(b"x" * 100000)
        self.assertEqual(self.item.parserResyncs, 0)
        self.assertLess(len(self.item._stream_buffer), 100)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)

    def test_d2_the_garbage_backstop_resyncs_the_pathological_state(self):
        # The RETAINED_GARBAGE_LIMIT backstop itself: an unframed
        # buffer beyond the bound (a state the eager trims normally
        # prevent) is discarded with a counted resync — the connection
        # is never the response.
        self._start(content_type=b"application/octet-stream")
        self.item._stream_buffer = bytearray(b"z" * (RETAINED_GARBAGE_LIMIT + 1))
        self.item._apply_limits()
        self.assertGreater(self.item.parserResyncs, 0)
        self.assertEqual(len(self.item._stream_buffer), 0)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)

    def test_e_constant_resolution_fires_image_size_changed_once(self):
        self._start()
        emissions = []
        self.item.imageSizeChanged.connect(lambda: emissions.append(1))
        frame = _jpeg(40, 30)
        for _ in range(5):
            self._reply().deliver(_multipart(frame))
            self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 5)
        self.assertEqual(len(emissions), 1)

    def test_f_resolution_changes_fire_once_per_change(self):
        self._start()
        emissions = []
        self.item.imageSizeChanged.connect(lambda: emissions.append(1))
        for _ in range(3):
            self._reply().deliver(_multipart(_jpeg(40, 30)))
            self._drain(80)
        for _ in range(3):
            self._reply().deliver(_multipart(_jpeg(64, 48)))
            self._drain(80)
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
        self._drain(500)
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
        # The newest frame wins the display: once the burst settles,
        # one more render tick decodes the pending newest — the last
        # frame's shade (63) — and never an older one.
        self._drain(80)
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
        self._drain(80)
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
        self._drain(80)
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
        self._drain(80)
        self.assertEqual(seen, [(64, 48)])

        self.item.clearFrame()
        self.assertEqual((self.item.imageWidth, self.item.imageHeight), (0, 0),
                         "a blanked frame reports no size")
        self.assertEqual(seen, [(64, 48), (0, 0)], "the blank is announced")

        # The resume: the SAME resolution on the same reply.
        self._reply().deliver(_multipart(_jpeg(64, 48)))
        self._drain(80)
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
        self.assertLess(len(self.item._stream_buffer), 1000)
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
        self.assertEqual(len(self.item._stream_buffer), 0)
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_a_stream_transition_resets_the_parser_state(self):
        # A genuinely new stream must not inherit the old source's
        # partial frame, buffer or boundary.
        self._start()
        self._reply().deliver(b"--mpfboundary\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8partial")
        self.assertGreater(len(self.item._stream_buffer), 0)
        self.item.setSourceURL(QUrl("http://127.0.0.1:1/other"))  # running: one transition
        self.assertEqual(len(self.item._stream_buffer), 0)
        self.assertIsNone(self.item._pending_frame)
        self.assertIsNone(self.item._multipart_boundary)

    def test_the_stats_snapshot_emits_when_the_counters_move(self):
        # The diagnostics properties mutate and ride one low-frequency
        # statsChanged signal — never per-frame notifications.
        self._start()
        emissions = []
        self.item.statsChanged.connect(lambda: emissions.append(1))
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertEqual(emissions, [])  # no per-frame emission
        self._drain(1200)
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
        self.assertLessEqual(len(self.item._stream_buffer), len(b"--mpfboundary"))
        self.assertEqual(self._reply()._aborted, 0)
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_nonsensical_content_lengths_fall_back_to_the_scan(self):
        # Content-Length <= 0 or non-numeric is not usable framing:
        # the SOI/EOI scan within the part carries the frame.
        self._start()
        frame = _jpeg(40, 30)
        for declared in (b"0", b"-5", b"abc"):
            body = (b"--mpfboundary\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + declared + b"\r\n\r\n" + frame + b"\r\n")
            self._reply().deliver(body)
            self._drain(40)
        self.assertEqual(self.item.framesParsed, 3)
        self.assertEqual(self.item.framesDisplayed, 3)

    def test_a_double_dash_boundary_normalises(self):
        # boundary=--foo is the non-compliant real-world form: the
        # delimiter must be --foo, never ----foo.
        self._start(content_type=b"multipart/x-mixed-replace; boundary=--mpfboundary")
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertEqual(self.item.framesParsed, 1)
        self._drain(80)
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
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_a_complete_oversized_jpeg_is_rejected_in_every_path(self):
        # The central choke point: a COMPLETE frame beyond the
        # maximum is rejected before any decode, in the raw scan and
        # the multipart-no-Content-Length paths alike — never by
        # reconnecting.
        huge = b"\xff\xd8" + b"x" * (MAX_IN_PROGRESS_FRAME_BYTES + 10) + b"\xff\xd9"
        # The raw scan path.
        self._start(content_type=b"application/octet-stream")
        before = self.item._oversized_drops
        self._reply().deliver(huge)
        self.assertEqual(self.item.framesParsed, 0)
        self.assertEqual(self.item._oversized_drops, before + 1)
        self.assertEqual(len(self.nam.requests), 1)
        self.assertEqual(self._reply()._aborted, 0)
        self.item.stop()
        # The multipart path without Content-Length.
        self._start()
        before = self.item._oversized_drops
        self._reply().deliver(b"--mpfboundary\r\nContent-Type: image/jpeg\r\n\r\n" + huge)
        self.assertEqual(self.item.framesParsed, 0)
        self.assertEqual(self.item._oversized_drops, before + 1)
        self.assertEqual(len(self.nam.requests), 2)  # the two starts, no extra
        self.assertEqual(self._reply()._aborted, 0)
        # A healthy frame still parses afterwards.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)

    def test_an_oversized_header_with_a_terminator_is_rejected(self):
        # MAX_HEADER_BYTES applies even when the oversized header
        # block eventually terminates.
        self._start()
        self._reply().deliver(
            b"--mpfboundary" + b"X" * (MAX_HEADER_BYTES + 100) + b"\r\n\r\n"
            + _jpeg(40, 30))
        self.assertGreater(self.item.parserResyncs, 0)
        self.assertLessEqual(len(self.item._stream_buffer), len(b"--mpfboundary"))
        self.assertEqual(self._reply()._aborted, 0)
        # The stream stays healthy for the next part.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._drain(80)
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
        self._drain(1100)  # a full stats interval with traffic in it
        self.assertEqual(self.item.framesParsed, 10)
        self.assertGreater(self.item.recentIncomingFPS, 0)
        # A quiet interval: the recent rates drop to zero while the
        # lifetime averages stay.
        self._drain(1300)
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
        self.assertLessEqual(len(self.item._stream_buffer), len(b"--mpfboundary"))
        self.assertEqual(self._reply()._aborted, 0)
        # A later valid frame still parses.
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._drain(80)
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
        self._drain(1300)  # a quiet interval: the rates fall to zero
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
        self._drain(6500)  # no frames at all; past the 5 s gate
        self.assertGreater(self.item._trace_summary_at, before)
        self.assertGreater(self.item._last_stats_at, 0)

    def test_recent_throughput_tracks_delta_bytes(self):
        self._start()
        frame = _jpeg(40, 30)
        body = _multipart(frame)
        for _ in range(8):
            self._reply().deliver(body)
            self._drain(200)
        self._drain(1100)  # a full interval with traffic in it
        self.assertGreater(self.item.recentBytesPerSec, 0)

    def test_stop_disconnects_the_stored_callbacks(self):
        # _stop_request disconnects the ACTUAL stored lambdas: after
        # the stop, the reply's finished cannot reach the handler at
        # all (the identity guard is the second line of defence).
        self._start()
        reply = self._reply()
        self.item.stop()
        self.assertIsNotNone(self.item._reply_finished_cb)
        self.item._transport_errors = 0
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
        self._drain(1100)
        self.assertGreater(self.item.recentIncomingFPS, 0)

    def test_the_drain_records_its_cost_and_the_gap_between_drains(self):
        # The drain is the receive path's share of the Qt thread, and
        # the gap between two drains is the starvation the camera's own
        # send sees. Neither is readable from a frame rate, and a
        # source that cannot be drained is the "frames backing up"
        # complaint's other explanation.
        self._start()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertGreater(self.item._drain_ms_total, 0)
        self.assertGreater(self.item._drain_ms_max, 0)
        started = time.monotonic()
        self._drain(300)  # nothing arrives: the Qt thread is elsewhere
        waited = (time.monotonic() - started) * 1000.0
        self.assertGreaterEqual(waited, 250)
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertGreaterEqual(self.item._drain_gap_ms_max, waited * 0.8)

    def test_the_display_lag_is_the_age_of_the_frame_that_reached_the_screen(
            self):
        # The latency the viewer actually sees. A frame that arrived
        # long before it was decoded is late by its AGE, not by its
        # decode time — which is what "behind reality" means.
        self._start()
        self.item._render_timer.stop()  # hold the frame in the buffer
        frame = _jpeg(40, 30)
        self._reply().deliver(_multipart(frame))
        started = time.monotonic()
        self._drain(250)  # the frame waits while the Qt thread is busy
        waited = (time.monotonic() - started) * 1000.0
        self._tick_and_install()
        held = self.item._display_lag_ms_total
        self.assertGreaterEqual(held, waited * 0.8)
        # A frame rendered at once is a small lag: the measurement is
        # THIS frame's age, not a constant and not a stale stamp.
        self._reply().deliver(_multipart(frame))
        self._tick_and_install()
        self.assertLess(self.item._display_lag_ms_total - held, waited * 0.5)

    def test_the_stats_interval_separates_parsed_from_displayed(self):
        # The phase diagnosis needs both counts over the SAME interval:
        # parsed-but-not-displayed is the backlog, and the Qt-thread
        # share says whether the plugin or the source owns it.
        self._start()
        frames = [_jpeg(40, 30, shade=50 + index) for index in range(4)]
        self._reply().deliver(b"".join(_multipart(item) for item in frames))
        self.assertTrue(
            self._drain_until(lambda: self.item._recent_parsed_count == 4),
            "the stats tick never closed an interval over the burst")
        self.assertEqual(self.item._recent_displayed_count, 1)
        # The stats timer is a coarse QTimer: 1 s of interval with Qt's
        # 5% tolerance, not a lifetime and not a guess.
        self.assertGreaterEqual(self.item._recent_interval_s, 0.9)
        self.assertLessEqual(self.item._recent_interval_s, 1.5)
        self.assertGreater(self.item._recent_decode_ms, 0)
        self.assertGreater(self.item._recent_decode_ms_per_frame, 0)
        self.assertGreater(self.item._recent_drain_ms_per_frame, 0)

    def test_the_oldest_buffered_frame_reports_its_age(self):
        # A frame that parsed but never rendered is invisible to both
        # rates; its age at the tick is the backlog the user feels.
        self._start()
        self.item._render_timer.stop()  # the render never happens
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self.assertTrue(
            self._drain_until(lambda: self.item._recent_pending_age_ms > 0),
            "the stats tick never reported the buffered frame")
        self.assertEqual(self.item.framesParsed, 1)
        self.assertEqual(self.item.framesDisplayed, 0)
        self.assertGreaterEqual(self.item._recent_pending_age_ms, 200)

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
        self.assertGreaterEqual(self.item._drain_gap_ms_max, 250)
        self.assertTrue(
            self._drain_until(lambda: self.item._recent_drain_gap_ms >= 250),
            "the tick never harvested the interval's maxima")
        self.assertEqual(self.item._drain_gap_ms_max, 0.0)

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
            self._drain_until(lambda: self.item._recent_displayed_count >= 1),
            "the stats tick never reported the displayed frame")
        fmt, values = self.item._summary_line()
        line = fmt % values
        self.assertIn(self.item._app_state,
                      ("active", "inactive", "hidden", "suspended", "unknown"))
        self.assertIn("[%s]" % self.item._app_state, line)
        self.assertIn("parsed %d frames" % self.item._recent_parsed_count, line)
        self.assertIn("displayed %d frames" % self.item._recent_displayed_count, line)
        # The cadence the pane asked for, beside what came out: the pair
        # is what tells a rate capped by the request from a rate the Qt
        # thread could not keep up with.
        self.assertEqual(self.item._render_timer.interval(), 40)
        self.assertIn("render tick 40 ms", line)
        self.assertIn("asked 25.0 fps", line)
        self.assertIn("pending frame age %.1f ms" % self.item._recent_pending_age_ms,
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
        self._drain(80)
        self.assertEqual(self.item.framesDisplayed, 1)
        self.assertEqual(self.item.imageWidth, 40)


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
        globals_dict = MoonrakerMJPGImage._render.__globals__
        old = globals_dict.get("_decode_jpeg")
        globals_dict["_decode_jpeg"] = replacement
        self.addCleanup(globals_dict.__setitem__, "_decode_jpeg", old)

    def _tick_and_install(self):
        """Drive one render tick by hand and wait for its frame to reach
        the screen: the tick hands the frame over, and the install lands
        on a later Qt turn."""
        rendered = self.item._decodes_rendered
        self.item._render()
        self.assertTrue(
            self._drain_until(lambda: self.item._decodes_rendered > rendered),
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
        self.assertEqual(self.item._decodes_rendered, 0,
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
        deadline = time.monotonic() + 4.0
        while not decoded.is_set() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(decoded.is_set(), "the worker never finished the decode")
        self.item.stop()
        self.assertIsNotNone(self.item._decoder,
                             "the worker was orphaned rather than joined")
        self.assertFalse(self.item._decoder.isRunning(),
                         "a worker outlived the item's stop()")
        self._drain(50)  # the queued result arrives, and is dropped
        self.assertEqual(self.item._decodes_rendered, 0)
        self.assertEqual(self.item.framesDisplayed, 0)
        self.assertTrue(self.item._image.isNull(),
                        "a decode superseded by stop() reached the screen")
        self.assertEqual(self.item._decode_in_flight, 0)

    def test_the_tick_hands_the_frame_over_instead_of_decoding_it(self):
        # What the Qt thread pays per frame is the hand-over plus the
        # install; the decode's own milliseconds are the worker's. The
        # two are accounted separately, so the split is measurable
        # rather than asserted.
        self._start()
        self.item._render_timer.stop()
        self._reply().deliver(_multipart(_jpeg(400, 300)))
        self._tick_and_install()
        self.assertGreater(self.item._decode_ms_total, 0.0)
        self.assertLess(self.item._tick_ms_total,
                        self.item._decode_ms_total / 4.0,
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
        item._pending_arrival = time.monotonic()
        item._render()
        self.assertIsNotNone(item._pending_frame,
                             "a tick with no worker consumed the frame")
        self.assertEqual(item._decode_in_flight, 0)
        self.assertEqual(item._decodes_rendered, 0)

    def test_a_decode_slower_than_the_tick_displays_once_per_decode(self):
        # A decode that outlasts its tick must not cost the NEXT tick too.
        # Dispatch quantised to the tick is one display per two ticks at a
        # 20 ms tick and a 22 ms decode -- and a frame that arrived just
        # after the wasted tick then waits out another whole period, which
        # is the lag the live report names. The display rate belongs to the
        # decode, bounded by the cap, not to a multiple of the tick.
        real = QImage.fromData

        def slow(frame):
            time.sleep(0.022)  # a decode a hair longer than one tick
            return real(frame)

        self._patch_decode(slow)
        self.item.setTargetFps(50.0)  # a 20 ms tick
        self._start()
        self.assertEqual(self.item._render_timer.interval(), 20)
        frame = _jpeg(40, 30)
        delivered = 0
        started = time.monotonic()
        next_frame = started
        while time.monotonic() - started < 1.0:
            if time.monotonic() >= next_frame:
                self._reply().deliver(_multipart(frame))
                delivered += 1
                next_frame += 0.010  # a 100 fps source: always one in hand
            self.qt.events(5)
        self.assertGreaterEqual(delivered, 80)
        # ~45 displays in the window at the decode's own rate; one per two
        # ticks is ~25.
        self.assertGreaterEqual(
            self.item.framesDisplayed, 35,
            "the decode's own rate was not reached: dispatch is quantised "
            "to the tick")

    def test_the_completion_dispatch_never_shortens_the_cap(self):
        # The hand-over a completion makes must clear the same interval
        # the tick does, or the cap stops being a cap: a fast source
        # against a fast decode would display as fast as the source
        # delivers instead of at the rate the pane asked for.
        self.item.setTargetFps(20.0)  # a 50 ms cap
        self._start()
        self.assertEqual(self.item._render_timer.interval(), 50)
        frame = _jpeg(40, 30)
        delivered = 0
        started = time.monotonic()
        next_frame = started
        while time.monotonic() - started < 1.0:
            if time.monotonic() >= next_frame:
                self._reply().deliver(_multipart(frame))
                delivered += 1
                next_frame += 0.010  # a 100 fps source, 5x the cap
            self.qt.events(5)
        self.assertGreaterEqual(delivered, 80)
        self.assertLessEqual(
            self.item.framesDisplayed, 24,
            "the cap was exceeded: a completion hand-over outran the tick")
        self.assertGreaterEqual(self.item.framesDisplayed, 15,
                                "the cap's own rate was not reached")

    def test_a_completion_inside_the_cap_leaves_the_frame_for_the_tick(self):
        # The completion hands over AT the cap, not merely after its
        # decode: a decode that finished inside the interval must leave
        # the frame behind it for the tick, or a fast decode would set
        # the display rate instead of the rate the pane asked for.
        def ten_ms(frame):
            time.sleep(0.01)
            return QImage.fromData(frame)

        self._patch_decode(ten_ms)
        self.item.setTargetFps(20.0)  # a 50 ms cap
        self._start()
        self.item._render_timer.stop()  # every tick from here is by hand
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=40)))
        self.item._render()  # handed over; this decode takes 10 ms
        # The next frame lands well inside the interval, so it is pending
        # when the decode returns.
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=41)))
        self.assertTrue(
            self._drain_until(lambda: self.item._decodes_rendered == 1),
            "the first decode never reached the screen")
        # 10 ms of the 50 ms cap have passed: 30 ms of draining is long
        # past a second decode, and the frame must still be pending.
        self._drain(30)
        self.assertEqual(self.item._decodes_rendered, 1,
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
        slow_once = []

        def first_slow(frame):
            if not slow_once:
                slow_once.append(True)
                time.sleep(0.03)  # longer than the 20 ms cap
            return QImage.fromData(frame)

        self._patch_decode(first_slow)
        self.item.setTargetFps(50.0)  # a 20 ms cap
        self._start()
        self.item._render_timer.stop()  # every tick from here is by hand
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=40)))
        self.item._render()  # handed over; this decode is held for 30 ms
        # A frame in hand when that decode returns: the completion hands
        # it over itself (the cap allows it by then), and the decode that
        # follows is instant, so the worker is free again immediately.
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=41)))
        self.assertTrue(
            self._drain_until(lambda: self.item._decodes_rendered == 2),
            "the completion did not hand the pending frame over")
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 41)
        # A third arrival, inside the interval the completion just spent.
        self._reply().deliver(_multipart(_jpeg(40, 30, shade=42)))
        self.item._render()
        self._drain(5)
        self.assertEqual(self.item._decodes_rendered, 2,
                         "the tick stacked a hand-over on the completion's")
        self.assertIsNotNone(self.item._pending_frame,
                             "the held frame was consumed by nothing")
        # Held, not lost: once the interval has passed, the next tick
        # displays it.
        self._drain(30)
        self._tick_and_install()
        self.assertEqual(self.item._image.pixelColor(0, 0).red(), 42)
        self.assertEqual(self.item.framesDisplayed, 3)

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
        self.assertEqual(self.item._decodes_rendered, 1)
        self.item._emit_stats()
        round_trip = self.item._recent_round_trip_ms
        # The injected decode is the floor: the trip carries it plus
        # whatever the hand-over and the return cost.
        self.assertGreaterEqual(round_trip, 30.0)
        # The three shares are the trip, and each is accounted where it
        # happens: a split that dropped a share would not sum back. The
        # four readings share one precision, so their sums differ by
        # rounding alone — 0.02 at the worst, against the 0.05 here.
        self.assertAlmostEqual(
            round_trip,
            self.item._recent_queue_wait_ms + self.item._recent_decode_ms_per_frame
            + self.item._recent_delivery_ms,
            places=1,
            msg="the round trip's shares do not add up to the round trip")
        # Two meters, accumulated independently: the frame's age at the
        # screen (from its parse) cannot be younger than the trip that
        # put it there.
        self.assertGreaterEqual(self.item._recent_display_lag_ms,
                                round_trip - 1.0)
        # The interval's own maximum, which the stats tick consumes: the
        # trip carries the decode, so it cannot be the shorter of the two.
        self.assertGreaterEqual(self.item._recent_round_trip_ms_max,
                                self.item._decode_ms_max)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class RendererPathCoverageTests(unittest.TestCase):
    """The renderer's QPainter/sink/teardown paths (the 2026-09-19
    per-file coverage tightening): paint, the diagnostic getters, the
    disconnect/teardown except branches, the finished/error handlers,
    the boundary-detection guards, the scan-mode cleanup, the decode
    failure, the trace line and the destructor."""

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

    def _start(self, content_type=b"multipart/x-mixed-replace; boundary=mpfboundary"):
        def _get_with_type(request):
            reply = FakeReply(content_type)
            self.nam.requests.append(reply)
            return reply
        self.nam.get = _get_with_type
        self.item.start()

    def test_the_mirror_and_source_accessors_answer(self):
        # The QML-facing accessors (paint itself is the scene
        # graph's callback and cannot run under this file's
        # QCoreApplication runtime).
        self.item.setMirror(True)
        self.item.setMirror(True)  # the no-op path
        self.assertTrue(self.item.getMirror())
        self.assertEqual(self.item.getSourceURL().toString(), "http://127.0.0.1:1/webcam")

    def test_the_diagnostic_getters_read_before_and_after_traffic(self):
        self.assertEqual(self.item.incomingFPS, 0.0)  # the epoch-0 branch
        self.assertEqual(self.item.displayedFPS, 0.0)
        self._start()
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._drain(80)
        self.assertGreaterEqual(self.item.bufferHighWaterMark, 0)
        self.assertGreaterEqual(self.item.maximumFrameSize, 0)
        self.assertGreaterEqual(self.item.incomingFPS, 0)
        self.assertGreaterEqual(self.item.displayedFPS, 0)
        self.assertGreaterEqual(self.item.averageFrameSize, 0)
        self.assertGreaterEqual(self.item.averageDecodeMs, 0)
        self.assertGreaterEqual(self.item.maximumDecodeMs, 0)

    def test_stop_survives_raising_disconnects_and_a_raising_finished_check(self):
        class Broken:
            def disconnect(self, *args):
                raise RuntimeError("gone")

        self._start()
        reply = self._reply()
        reply.readyRead = Broken()
        reply.finished = Broken()
        reply.errorOccurred = Broken()
        reply.isFinished = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
        self.item.stop()  # every except branch runs; nothing escapes
        self.assertTrue(reply._deleted_later)

    def test_a_current_finished_retires_the_stream_and_a_stale_one_is_ignored(self):
        self._start()
        stale = self._reply()
        self.item.stop()
        self._start()
        current = self._reply()
        stale.finished.emit()  # the stale guard returns first
        self.assertTrue(self.item._started)
        current.finished.emit()
        self.assertFalse(self.item._started)
        self.assertGreaterEqual(current._aborted, 1)

    def test_a_current_error_counts_and_a_stale_error_is_ignored(self):
        self._start()
        stale = self._reply()
        self.item.stop()
        self._start()
        before = self.item.transportErrors
        stale.errorOccurred.emit(1)
        self.assertEqual(self.item.transportErrors, before)
        self._reply().errorOccurred.emit(1)
        self.assertEqual(self.item.transportErrors, before + 1)

    def test_detect_boundary_survives_a_raising_header_and_an_empty_type(self):
        self._start()
        reply = self._reply()
        reply.rawHeader = lambda name: (_ for _ in ()).throw(RuntimeError("gone"))
        reply.deliver(b"garbage")
        self._drain(1)
        self._start(b"")  # an empty content type falls to the scan
        self._reply().deliver(_multipart(_jpeg(40, 30)))
        self._drain(80)
        self.assertGreaterEqual(self.item.framesParsed, 1)

    def test_drain_is_inert_without_a_reply_or_data(self):
        self.item._image_reply = None
        self.item._drain()
        self._start()
        self._reply().deliver(b"")
        self.item._drain()
        self.assertEqual(self.item.bytesReceived, 0)

    def test_a_boundary_with_no_frame_inside_is_dropped(self):
        self._start()
        self._reply().deliver(b"--mpfboundary\r\n--mpfboundary")
        self.assertEqual(self.item.framesParsed, 0)
        self.assertEqual(self.item.parserResyncs, 0)

    def test_scan_mode_trims_leading_and_trailing_garbage(self):
        self._start(b"")
        self._reply().deliver(b"abc")  # no SOI: keep only the tail pair
        self._drain(1)
        self._reply().deliver(b"junk" + _jpeg(40, 30))  # a leading-garbage SOI
        self._drain(80)
        self.assertGreaterEqual(self.item.framesParsed, 1)

    def test_an_undecodable_frame_counts_a_decode_failure(self):
        self._start()
        self.item._pending_frame = b"\xff\xd8\xff\xd9"
        self.item._pending_arrival = time.monotonic()
        before = self.item._decode_failures
        self.item._render()
        # The decode runs off the Qt thread now: the failure is counted
        # when the worker's empty result is delivered back.
        deadline = time.monotonic() + 4.0
        while self.item._decode_failures == before and time.monotonic() < deadline:
            self._drain(25)
        self.assertEqual(self.item._decode_failures, before + 1)

    def test_the_trace_line_logs_when_enabled(self):
        # The unittest runtime's double entry can alias the module, so
        # the patch targets the function's own globals — the binding
        # _trace actually resolves.
        calls = []

        class Logger:
            @staticmethod
            def log(level, message, *args):
                calls.append(message % args if args else message)

        globals_dict = self.item._trace.__globals__
        old = globals_dict.get("Logger")
        globals_dict["Logger"] = Logger
        try:
            self.item._trace_enabled = True
            self.item._trace("probe")
        finally:
            globals_dict["Logger"] = old
        self.assertEqual(len(calls), 1)

    def test_destruction_survives_a_raising_stop(self):
        item = MoonrakerMJPGImage()
        item.stop = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
        del item  # must not raise

    def test_begin_request_creates_a_manager_when_none_is_injected(self):
        item = MoonrakerMJPGImage()
        self.addCleanup(item.stop)
        item.setSourceURL(QUrl("http://127.0.0.1:1/webcam"))
        item._begin_request()
        self.assertIsNotNone(item._image_reply)
