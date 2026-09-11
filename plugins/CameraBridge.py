"""Key-carrying local republisher for authenticated camera streams.

Cura's NetworkMJPGImage cannot send the X-Api-Key header, so a camera
behind a header-auth proxy cannot render (the author's 2026-09-11
ruling: 4.0.0 gains a bridge). The bridge fetches the stream from the
configured printer WITH the key and republishes it on a keyless
loopback endpoint for Cura's loader. The listener binds loopback
only — the port is never reachable from the network."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import (
    QHostAddress,
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
    QTcpServer,
    QTcpSocket,
)

# The request header arrives in one packet from a local client, but a
# hostile local process could stream forever: cap the buffered header.
MAX_REQUEST_HEADER_BYTES = 8192


class CameraBridge(QObject):
    upstreamStarted = pyqtSignal()
    upstreamFailed = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        self._nam = QNetworkAccessManager(self)
        self._upstream_base = ""
        self._api_key = ""
        # One entry per local connection: (upstream reply, request
        # header buffer, response-head-sent flag).
        self._relays: Dict[QTcpSocket, Tuple[Optional[QNetworkReply], bytearray, bool]] = {}

    @property
    def port(self) -> int:
        return self._server.serverPort() if self._server.isListening() else 0

    @property
    def active(self) -> bool:
        return self.port > 0

    def configure(self, upstream_base: str, api_key: str) -> bool:
        """Point the bridge at the printer; listen on an ephemeral
        loopback port while configured. Returns whether the listener
        is live."""
        base = str(upstream_base or "").rstrip("/")
        key = str(api_key or "")
        if (base, key) != (self._upstream_base, self._api_key):
            self._close_all()
        self._upstream_base, self._api_key = base, key
        if base and not self._server.isListening():
            self._server.listen(QHostAddress.SpecialAddress.LocalHost, 0)
        elif not base and self._server.isListening():
            self._server.close()
        return self.port > 0

    def local_url(self, path: str) -> str:
        """The keyless loopback URL Cura's loader should use. The path
        (including any query) is echoed to the upstream verbatim."""
        if not self.port:
            return ""
        path = str(path or "").lstrip("/")
        host = QHostAddress(QHostAddress.SpecialAddress.LocalHost).toString()
        return f"http://{host}:{self.port}/{path}"

    def stop(self) -> None:
        self._close_all()
        if self._server.isListening():
            self._server.close()
        self._upstream_base = ""
        self._api_key = ""

    def _close_all(self) -> None:
        for socket in list(self._relays):
            reply, _buffer, _sent = self._relays[socket]
            if reply is not None:
                try: reply.abort()
                except Exception: pass
            try: socket.abort()
            except Exception: pass
        self._relays.clear()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            self._relays[socket] = (None, bytearray(), False)
            socket.readyRead.connect(lambda s=socket: self._on_socket_ready(s))
            socket.disconnected.connect(lambda s=socket: self._on_socket_closed(s))

    def _on_socket_ready(self, socket: QTcpSocket) -> None:
        relay = self._relays.get(socket)
        if relay is None:
            return
        reply, buffer, sent = relay
        if reply is not None:
            # The request has been dispatched; any further client bytes
            # are body traffic (there is none for GET) — drain them.
            socket.readAll()
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_REQUEST_HEADER_BYTES:
            socket.abort()
            return
        if b"\r\n" not in buffer:
            return
        line, _rest = bytes(buffer).split(b"\r\n", 1)
        parts = line.decode("latin-1", errors="replace").split(" ")
        if len(parts) < 2 or parts[0].upper() != "GET":
            socket.abort()
            return
        self._start_upstream(socket, parts[1])

    def _start_upstream(self, socket: QTcpSocket, path: str) -> None:
        if not self._upstream_base:
            socket.abort()
            return
        target = QUrl(self._upstream_base + path)
        if not target.isValid() or target.scheme() not in ("http", "https"):
            socket.abort()
            return
        request = QNetworkRequest(target)
        request.setRawHeader(b"X-Api-Key", self._api_key.encode("utf-8"))
        reply = self._nam.get(request)
        _old_reply, buffer, _sent = self._relays[socket]
        self._relays[socket] = (reply, buffer, False)
        reply.readyRead.connect(lambda r=reply, s=socket: self._on_upstream_ready(s, r))
        reply.finished.connect(lambda r=reply, s=socket: self._on_upstream_finished(s, r))
        self.upstreamStarted.emit()

    @staticmethod
    def _header_text(value, fallback: str) -> str:
        if isinstance(value, bytes):
            return value.decode("latin-1", errors="replace")
        if isinstance(value, str):
            return value
        return fallback

    def _on_upstream_ready(self, socket: QTcpSocket, reply: QNetworkReply) -> None:
        relay = self._relays.get(socket)
        if relay is None or relay[0] is not reply:
            return
        _reply, buffer, sent = relay
        if not sent:
            self._relays[socket] = (reply, buffer, True)
            code = int(reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 200)
            reason = self._header_text(
                reply.attribute(QNetworkRequest.Attribute.HttpReasonPhraseAttribute), "OK")
            content_type = self._header_text(
                reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader), "application/octet-stream")
            declared = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
            head = f"HTTP/1.1 {code} {reason}\r\nContent-Type: {content_type}\r\n"
            if declared is not None and int(declared) > 0:
                head += f"Content-Length: {int(declared)}\r\n"
            head += "Connection: close\r\n\r\n"
            socket.write(head.encode("latin-1"))
        chunk = bytes(reply.readAll())
        if chunk:
            socket.write(chunk)

    def _on_upstream_finished(self, socket: QTcpSocket, reply: QNetworkReply) -> None:
        relay = self._relays.get(socket)
        if relay is None or relay[0] is not reply:
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            self.upstreamFailed.emit(f"camera upstream failed: {reply.errorString()}")
            if code is None or int(code) >= 400:
                # An auth refusal or a dead stream: nothing useful to
                # relay — close so the loader sees the failure and
                # retries on its own schedule.
                socket.abort()
                return
        # Flush any tail and close the response so a snapshot-style
        # reply terminates cleanly.
        try: socket.flush()
        except Exception: pass
        try: socket.disconnectFromHost()
        except Exception: pass
        self._relays.pop(socket, None)

    def _on_socket_closed(self, socket: QTcpSocket) -> None:
        relay = self._relays.pop(socket, None)
        if relay is not None and relay[0] is not None:
            try: relay[0].abort()
            except Exception: pass
