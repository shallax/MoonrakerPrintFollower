# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Share bounded streaming multipart ownership across upload workflows.

"""One streaming request owns its file, reply, bounded body and terminal result."""
from dataclasses import dataclass
import json
from PyQt6.QtCore import QByteArray, QFile, QIODevice, QObject, QVariant, pyqtSignal
from PyQt6.QtNetwork import QHttpMultiPart, QHttpPart, QNetworkReply, QNetworkRequest
from .ReplyBodyReader import ReplyBodyReader

MAX_UPLOAD_REPLY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class UploadResult:
    body: bytes = b""
    network_error: str = ""
    overflow: bool = False
    cancelled: bool = False

    def payload(self):
        try:
            parsed = json.loads(self.body.decode())
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, UnicodeError):
            return {}


class MultipartUpload(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(object)

    def __init__(self, transport, parent=None):
        super().__init__(parent)
        self._transport = transport
        self._bodies = ReplyBodyReader(self)
        self._reply = None
        self._file = None
        self._multipart = None
        self._terminal = False

    def start(self, path, filename, fields):
        if self._reply is not None or self._terminal:
            raise RuntimeError("Upload operation already used")
        try:
            source = self._file = QFile(path)
            if not source.open(QIODevice.OpenModeFlag.ReadOnly):
                raise OSError(source.errorString())
            multipart = self._multipart = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType, self)
            source.setParent(multipart)
            part = QHttpPart()
            part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                           QVariant(f'form-data; name="file"; filename="{filename}"'))
            part.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, QVariant("application/octet-stream"))
            part.setBodyDevice(source)
            multipart.append(part)
            for field, value in fields.items():
                part = QHttpPart()
                part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                               QVariant(f'form-data; name="{field}"'))
                part.setBody(QByteArray(value.encode()))
                multipart.append(part)
            request = self._transport.request("server/files/upload", timeout_ms=30000)
            reply = self._transport.network.post(request, multipart)
            self._reply = reply
            multipart.setParent(reply)
            self._bodies.watch(reply, MAX_UPLOAD_REPLY_BYTES)
            reply.uploadProgress.connect(self._progress)
            reply.finished.connect(self._finished)
        except Exception:
            self.cancel(notify=False)
            raise

    def _progress(self, sent, total):
        if not self._terminal and total > 0:
            self.progress.emit(max(0, min(100, int(sent * 100 / total))))

    def _finished(self):
        reply = self.sender()
        if reply is not self._reply or self._terminal:
            return
        reply._mpf_body_finished = True
        try:
            body = self._bodies.take(reply, MAX_UPLOAD_REPLY_BYTES)
        except ValueError:
            body = b""
        result = UploadResult(body=body,
            network_error=reply.errorString() if reply.error() != QNetworkReply.NetworkError.NoError else "",
            overflow=bool(getattr(reply, "_mpf_body_overflow", False)))
        self._terminal = True
        self._dispose()
        self.finished.emit(result)

    def _dispose(self):
        source, self._file = self._file, None
        if source is not None:
            try:
                source.close()
            except RuntimeError:
                pass  # a host-destroyed reply also destroyed its file device
        reply, self._reply = self._reply, None
        multipart, self._multipart = self._multipart, None
        try:
            if reply is not None:
                reply.deleteLater()
            elif multipart is not None:
                multipart.deleteLater()
        except RuntimeError:
            pass  # host teardown may already have deleted the QObject tree

    def cancel(self, *, notify=True):
        if self._terminal:
            return
        self._terminal = True  # invalidate before abort's synchronous finished
        reply, self._reply = self._reply, None
        if reply is not None:
            try:
                reply.abort()
            except Exception:
                pass
        self._reply = reply
        self._dispose()
        if notify:
            self.finished.emit(UploadResult(cancelled=True))
