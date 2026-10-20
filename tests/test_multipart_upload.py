# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify shared streaming, bounded responses and reentrant cancellation.

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QFile, QObject, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from mpf.moonraker.MultipartUpload import MultipartUpload, UploadResult


class Reply(QObject):
    readyRead = pyqtSignal()
    uploadProgress = pyqtSignal(int, int)
    finished = pyqtSignal()

    def __init__(self, body=b'', *, error=False, refusal=False):
        super().__init__()
        self.body = body
        self.error_flag, self.refusal = error, refusal
        self.aborts = self.disposals = 0
        self.limits = []

    def setReadBufferSize(self, size): self.buffer_size = size
    def read(self, limit):
        self.limits.append(limit)
        data, self.body = self.body[:limit], self.body[limit:]
        return data
    def error(self):
        return QNetworkReply.NetworkError.ContentAccessDenied if self.error_flag else QNetworkReply.NetworkError.NoError
    def errorString(self): return 'forbidden'
    def abort(self):
        self.aborts += 1
        if self.refusal:
            raise OSError('socket already gone')
        self.finished.emit()
    def deleteLater(self):
        self.disposals += 1
        if self.refusal:
            raise RuntimeError('QObject already gone')


class MultipartUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'part.gcode'
        self.path.write_bytes(b'G1 X1\n')
        self.reply = Reply(b'{"print_started":true}')
        self.multipart = None
        def post(request, multipart):
            self.multipart = multipart
            self.device = multipart.findChild(QFile)
            return self.reply
        self.transport = SimpleNamespace(request=Mock(return_value=QNetworkRequest()),
                                         network=SimpleNamespace(post=post))
        self.owner = MultipartUpload(self.transport)
        self.addCleanup(self.owner.cancel, notify=False)
        self.results, self.progress = [], []
        self.owner.finished.connect(self.results.append)
        self.owner.progress.connect(self.progress.append)

    def start(self):
        self.owner.start(str(self.path), 'part.gcode', {'root': 'gcodes', 'path': 'parts'})

    def test_streamed_device_progress_terminal_body_and_disposal(self):
        self.start()
        self.assertTrue(self.device.isOpen())
        self.assertEqual(bytes(self.device.readAll()), b'G1 X1\n')
        self.device.seek(0)
        self.transport.request.assert_called_once_with('server/files/upload', timeout_ms=30000)
        for sent, total in ((0, 0), (1, 2), (100, 2), (-1, 2)):
            self.reply.uploadProgress.emit(sent, total)
        self.assertEqual(self.progress, [50, 100, 0])
        with self.assertRaisesRegex(RuntimeError, 'already used'):
            self.start()
        self.reply.readyRead.emit()
        self.reply.finished.emit()
        self.reply.finished.emit()
        self.reply.uploadProgress.emit(2, 2)
        self.assertEqual(len(self.results), 1)
        result = self.results[0]
        self.assertEqual(result.payload(), {'print_started': True})
        result.payload()['print_started'] = False
        self.assertEqual(result.payload(), {'print_started': True})
        self.assertFalse(self.device.isOpen())
        self.assertEqual((self.reply.disposals, self.reply.aborts), (1, 0))
        self.assertEqual(self.progress, [50, 100, 0])

    def test_bounded_response_aborts_without_returning_a_partial_body(self):
        self.reply = Reply(b'x' * 100)
        with patch('mpf.moonraker.MultipartUpload.MAX_UPLOAD_REPLY_BYTES', 16):
            self.start()
            self.reply.readyRead.emit()
        self.assertEqual(len(self.results), 1)
        self.assertTrue(self.results[0].overflow)
        self.assertEqual(self.results[0].body, b'')
        self.assertEqual((self.reply.aborts, self.reply.disposals), (1, 1))
        self.assertEqual(self.reply.limits, [17])
        self.assertFalse(self.device.isOpen())

    def test_cancellation_invalidates_before_synchronous_finished(self):
        self.start()
        self.owner.cancel()
        self.owner.cancel()
        self.assertEqual(len(self.results), 1)
        self.assertTrue(self.results[0].cancelled)
        self.assertEqual((self.reply.aborts, self.reply.disposals), (1, 1))
        self.assertFalse(self.device.isOpen())
        with self.assertRaises(RuntimeError): self.start()

    def test_silent_cancellation_and_already_destroyed_children(self):
        self.reply = Reply(refusal=True)
        self.start()
        sip.delete(self.device)
        self.owner.cancel(notify=False)
        self.assertEqual(self.results, [])
        self.assertEqual((self.reply.aborts, self.reply.disposals), (1, 1))
        owner = MultipartUpload(self.transport)
        owner.cancel(notify=False)
        self.assertIsNone(owner._reply)

    def test_start_failure_closes_the_file_without_a_terminal_signal(self):
        self.transport.network.post = Mock(side_effect=OSError('offline'))
        with self.assertRaisesRegex(OSError, 'offline'):
            self.start()
        self.assertIsNone(self.owner._file)
        self.assertEqual(self.results, [])
        owner = MultipartUpload(self.transport)
        with self.assertRaises(OSError): owner.start('/missing/part.gcode', 'part.gcode', {})
        self.assertIsNone(owner._file)
        owner.cancel(notify=False)

    def test_network_error_and_legacy_body_shapes(self):
        self.reply = Reply(b'legacy acknowledgement', error=True)
        self.start()
        self.reply.finished.emit()
        self.assertEqual(self.results[0].network_error, 'forbidden')
        self.assertEqual(self.results[0].payload(), {})
        self.assertEqual(UploadResult(body=b'[]').payload(), {})
        self.assertEqual(UploadResult(body=b'\xff').payload(), {})
