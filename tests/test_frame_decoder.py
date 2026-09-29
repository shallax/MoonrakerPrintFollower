"""Decode queue semantics without timing-dependent worker-thread coverage.

The real-thread integration tests separately assert that native decoding leaves
Qt responsive. These tests drive the same queue loop synchronously with a
terminal sentinel, so its generation, failure and timing contract is directly
observable (including when coverage cannot trace a Qt-created native thread).
"""
import unittest
from unittest import mock

from tests.mjpg_test_support import QT_AVAILABLE

if QT_AVAILABLE:
    from tests.mjpg_test_support import FrameDecoder, QImage, _jpeg, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class FrameDecoderQueueTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.decoder = FrameDecoder()
        self.results = []
        self.decoder.decoded.connect(lambda generation, result: self.results.append((generation, result)))

    def test_queue_preserves_generation_pixels_and_timing_for_each_frame(self):
        first, second = _jpeg(17, 13, 53), _jpeg(11, 7, 172)
        self.decoder.submit(3, first)
        self.decoder.submit(8, second)
        self.decoder._queue.put(None)
        self.decoder.run()
        self.assertEqual([generation for generation, _ in self.results], [3, 8])
        for (_, result), frame in zip(self.results, (first, second), strict=True):
            image, decode_ms, queue_ms, completed_at = result
            self.assertEqual(image, QImage.fromData(frame))
            self.assertGreaterEqual(decode_ms, 0)
            self.assertGreaterEqual(queue_ms, 0)
            self.assertGreater(completed_at, 0)
        self.assertTrue(self.decoder._queue.empty())
        self.assertTrue(self.decoder.stop())

    def test_invalid_frame_reports_failure_and_does_not_drop_next_frame(self):
        self.decoder.submit(4, b"not a JPEG")
        self.decoder.submit(5, _jpeg(9, 6))
        self.decoder._queue.put(None)
        self.decoder.run()
        self.assertEqual([generation for generation, _ in self.results], [4, 5])
        self.assertIsNone(self.results[0][1][0])
        self.assertEqual(self.results[1][1][0].size().width(), 9)
        self.assertEqual(self.results[1][1][0].size().height(), 6)

    def test_shutdown_sentinel_prevents_decode_of_later_queue_entries(self):
        self.decoder._queue.put(None)
        self.decoder.submit(6, _jpeg(4, 3))
        with mock.patch.dict(FrameDecoder.run.__globals__, {"_decode_jpeg": mock.Mock()}) as namespace:
            self.decoder.run()
            namespace["_decode_jpeg"].assert_not_called()
        self.assertEqual(self.results, [])
        self.assertEqual(self.decoder._queue.qsize(), 1)

    def test_stop_queues_one_sentinel_and_reports_a_successful_join(self):
        with mock.patch.object(self.decoder, "isRunning", return_value=True), \
                mock.patch.object(self.decoder, "wait", return_value=True) as join:
            self.assertTrue(self.decoder.stop())
        join.assert_called_once_with(2000)
        self.assertIsNone(self.decoder._queue.get_nowait())
        self.assertTrue(self.decoder._queue.empty())

    def test_failed_join_preserves_the_worker_until_it_can_exit(self):
        orphaned = []
        with mock.patch.object(self.decoder, "isRunning", return_value=True), \
                mock.patch.object(self.decoder, "wait", return_value=False), \
                mock.patch.dict(FrameDecoder.stop.__globals__, {"_ORPHANED_DECODERS": orphaned}):
            self.assertFalse(self.decoder.stop())
        self.assertEqual(orphaned, [self.decoder])
        self.assertIsNone(self.decoder.parent())
        self.assertIsNone(self.decoder._queue.get_nowait())


if __name__ == "__main__":
    unittest.main()
