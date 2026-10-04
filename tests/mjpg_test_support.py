"""The plugin-owned MJPEG renderer (the fork of Cura's
NetworkMJPGImage): the receive/parse/latest-wins/render-scheduler
contracts. Qt-guarded — the container runs them for real against the
production class with a fake network manager and a fake reply."""

import os
import sys

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
            from mpf.monitor.camera.FrameDecoder import FrameDecoder
            from mpf.monitor.camera.MJPEGParser import MAX_HEADER_BYTES, MAX_IN_PROGRESS_FRAME_BYTES, RETAINED_GARBAGE_LIMIT
            from mpf.monitor.camera.MoonrakerMJPGImage import MoonrakerMJPGImage, RENDER_INTERVAL_MS
            # The defining module itself, captured while it is still in
            # sys.modules: the fixture's rollback drops the entry, and a
            # later import would execute a second copy whose patched
            # globals the class under test does not read.
            mjpg_module = sys.modules["mpf.monitor.camera.MoonrakerMJPGImage"]
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
            self._finished = False

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
            return self._finished

        def complete(self, tail=b"") -> None:
            if tail:
                self._chunks.append(tail)
            self._finished = True
            self.finished.emit()

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



# Explicit exports shared by camera test files; this module contains no tests.
__all__ = ["QT_AVAILABLE", "runtime", "QByteArray", "QObject", "QThread", "QUrl",
           "pyqtSignal", "QColor", "QImage", "QBuffer", "QIODevice", "QNetworkRequest",
           "FrameDecoder", "MAX_HEADER_BYTES", "MAX_IN_PROGRESS_FRAME_BYTES",
           "RETAINED_GARBAGE_LIMIT", "MoonrakerMJPGImage", "RENDER_INTERVAL_MS",
           "FakeReply", "FakeNam", "_jpeg", "_multipart", "_chunked"]
