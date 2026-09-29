"""Camera pipeline integration contracts, isolated by test-file process."""
import time
import unittest
from tests.mjpg_test_support import QT_AVAILABLE

if QT_AVAILABLE:
    from tests.mjpg_test_support import (
        QUrl, runtime, MoonrakerMJPGImage, FakeReply, FakeNam,
        _jpeg, _multipart,
    )


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
        self.item._pending_arrival = time.perf_counter()
        before = self.item._stats.decode_failures
        self.item._render()
        # The decode runs off the Qt thread now: the failure is counted
        # when the worker's empty result is delivered back.
        deadline = time.monotonic() + 4.0
        while self.item._stats.decode_failures == before and time.monotonic() < deadline:
            self._drain(25)
        self.assertEqual(self.item._stats.decode_failures, before + 1)

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



if __name__ == "__main__":
    unittest.main()
