"""Byte-only camera parsing without a Qt application, transport or decoder."""
import unittest
from unittest import mock

from mpf.monitor.camera.CameraStatistics import CameraStatistics
from mpf.monitor.camera.MJPEGParser import MJPEGParser


class CameraParserTests(unittest.TestCase):
    def setUp(self):
        self.frames = []
        self.notes = []
        self.statistics = CameraStatistics()
        self.parser = MJPEGParser(self.frames.append, self.notes.append, self.statistics)

    def test_all_multipart_split_positions_produce_the_same_complete_frames(self):
        frames = (b'\xff\xd8first\xff\xd9', b'\xff\xd8second\xff\xd9')
        body = b''.join(b'--camera\r\nContent-Length: ' + str(len(frame)).encode()
                        + b'\r\n\r\n' + frame + b'\r\n' for frame in frames)
        for split in range(len(body) + 1):
            self.parser.reset()
            self.frames.clear()
            self.parser.detect_boundary(b'multipart/x-mixed-replace; boundary="--camera"')
            self.parser.feed(body[:split])
            self.parser.feed(body[split:])
            self.assertEqual(self.frames, list(frames), split)

    def test_unframed_scan_handles_byte_at_a_time_delivery(self):
        frame = b'\xff\xd8payload\xff\xd9'
        for value in b'garbage' + frame:
            self.parser.feed(bytes([value]))
        self.assertEqual(self.frames, [frame])
        self.assertEqual(self.statistics.frames_parsed, 1)
        self.assertGreater(self.statistics.buffer_high_water, 0)

    def test_request_reset_drops_partial_framing_but_not_lifetime_counters(self):
        self.parser.detect_boundary(b'multipart/x-mixed-replace; boundary=x')
        self.parser.feed(b'--x\r\n')
        self.statistics.frames_parsed = 5
        self.parser.reset()
        self.assertEqual(self.parser.buffer, b'')
        self.assertIsNone(self.parser.boundary)
        self.assertEqual(self.statistics.frames_parsed, 5)

    def test_snapshot_limit_discards_the_partial_response(self):
        with mock.patch('mpf.monitor.camera.MJPEGParser.MAX_IN_PROGRESS_FRAME_BYTES', 4):
            self.assertTrue(self.parser.append_snapshot(b'ab'))
            self.assertTrue(self.parser.append_snapshot(b'cd'))
            self.assertEqual(self.parser.take_snapshot(), b'abcd')
            self.assertEqual(self.parser.take_snapshot(), b'')
            self.assertTrue(self.parser.append_snapshot(b'ab'))
            self.assertFalse(self.parser.append_snapshot(b'cde'))
        self.assertEqual(self.parser.take_snapshot(), b'')
        self.assertEqual(self.statistics.oversized_drops, 1)
        self.assertEqual(self.frames, [])

    def test_stats_sample_and_summary_do_not_need_qt_or_a_running_stream(self):
        emitted = []
        self.statistics.sample(1.0, 0.0, 'active', lambda: emitted.append(True))
        self.statistics.frames_parsed += 1
        self.statistics.sample(2.0, 1.5, 'active', lambda: emitted.append(True))
        text, values = self.statistics.summary(2.0, 40, 25.0)
        self.assertIn('asked 25.0 fps', text % values)
        self.assertEqual(self.statistics.recent_pending_age_ms, 500.0)
        self.assertEqual(len(emitted), 2)


if __name__ == '__main__':
    unittest.main()
