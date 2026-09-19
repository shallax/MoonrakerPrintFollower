"""Key-carrying local republisher for authenticated camera streams.

Cura's NetworkMJPGImage cannot send the X-Api-Key header, so a camera
behind a header-auth proxy cannot render (the 2026-09-11
ruling: 4.0.0 gains a bridge). The bridge fetches the stream from the
configured printer WITH the key and republishes it on a keyless
loopback endpoint for Cura's loader. The listener binds loopback
only — the port is never reachable from the network."""
from __future__ import annotations

import time
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

from UM.Logger import Logger

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
        # The default policy re-sends custom raw headers — X-Api-Key
        # included — across cross-origin redirects. Same-origin only,
        # exactly like the shared transport (the key must never travel
        # to a redirect target off the configured host).
        self._nam.setRedirectPolicy(QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        self._upstream_base = ""
        self._api_key = ""
        # The T8 latch: the first real upstream response bytes, once
        # per bridge lifetime.
        self._first_upstream_bytes = False
        # The recovery latch: upstreamStarted means PROVEN bytes (the
        # 2026-09-19 review — issuing a request is not recovery), so
        # the emit moved to the first readyRead with data and flips
        # back on every upstream failure.
        self._stream_healthy = False
        # One entry per local connection: (upstream reply, request
        # header buffer, response-head-sent flag).
        self._relays: Dict[QTcpSocket, Tuple[Optional[QNetworkReply], bytearray, bool]] = {}
        # The per-request diagnostic meta: the request id (from the
        # process-wide sequence, so ids never collide across bridges),
        # the local peer, the accept timestamp and the per-request
        # latches/teardown reasons — the cold-start trace follows one
        # request from accept to destruction without ambiguity. The
        # reply-keyed id map keeps the finished line labelled even
        # when the socket-close handler got there first.
        self._trace_meta: Dict[QTcpSocket, dict] = {}
        self._reply_meta: Dict[QNetworkReply, int] = {}
        # Cumulative relayed bytes — the leak probe's throughput gauge
        # (diffed tick-to-tick it shows the stream is actually flowing).
        self._relayed_bytes = 0

    def _trace(self, req_id: int, event: str) -> None:
        """One diagnostic line per request event. Trace-gated with the
        T0-T9 chain; a no-op in ordinary operation. Never carries
        credentials — callers sanitise URLs before they reach here."""
        from .CameraTiming import mark
        mark("R%d" % req_id, event)

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
            Logger.log("i", "Moonraker camera bridge listening on loopback port %d", self._server.serverPort())
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
        reason = "bridge reconfigured/stopped"
        for socket in list(self._relays):
            meta = self._trace_meta.get(socket) or {}
            reply, _buffer, _sent = self._relays[socket]
            if reply is not None:
                meta["aborted"] = True
                self._trace(meta.get("req", -1), "reply aborted: %s" % reason)
                try: reply.abort()
                except Exception: pass
                try: reply.deleteLater()
                except Exception: pass
            else:
                self._trace(meta.get("req", -1), "local socket dropped before any request: %s" % reason)
            try: socket.abort()
            except Exception: pass
            try: socket.deleteLater()
            except Exception: pass
        self._relays.clear()
        self._trace_meta.clear()

    def _accept(self) -> None:
        from .CameraTiming import mark
        from .CameraTiming import next_actor_id
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            req_id = next_actor_id()
            peer = "%s:%d" % (socket.peerAddress().toString(), socket.peerPort())
            self._relays[socket] = (None, bytearray(), False)
            self._trace_meta[socket] = {
                "req": req_id, "peer": peer, "accepted": time.monotonic(),
                "aborted": False, "first_ready": False, "relayed": 0,
            }
            mark("T6", "local camera client connected (req %d from %s, relays=%d)"
                 % (req_id, peer, len(self._relays)))
            self._trace(req_id, "accepted from %s (relays=%d)" % (peer, len(self._relays)))
            socket.readyRead.connect(lambda s=socket: self._on_socket_ready(s))
            socket.disconnected.connect(lambda s=socket: self._on_socket_closed(s))
            if hasattr(socket, "errorOccurred"):
                socket.errorOccurred.connect(lambda _e, s=socket, i=req_id: self._on_socket_error(i, s))

    def _on_socket_error(self, req_id: int, socket: QTcpSocket) -> None:
        try:
            detail = "%s (state=%d)" % (socket.errorString(), int(socket.state()))
        except Exception:
            detail = "unknown"
        self._trace(req_id, "local socket error: %s" % detail)

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
        req_id = (self._trace_meta.get(socket) or {}).get("req", -1)
        # The path may carry a query; the trace strips it (credentials
        # never ride the diagnostic).
        self._trace(req_id, "GET %s" % parts[1].split("?", 1)[0])
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
        # Cap the buffered upstream read: if the local client stalls,
        # this fills and Qt applies TCP backpressure to the camera
        # instead of growing memory without bound.
        from .CameraTiming import mark
        req_id = (self._trace_meta.get(socket) or {}).get("req", -1)
        shown = str(target.url()).split("?", 1)[0]
        mark("T7", "upstream request issued (req %d)" % req_id)
        self._trace(req_id, "upstream %s" % shown)
        reply = self._nam.get(request)
        reply.setReadBufferSize(256 * 1024)
        self._reply_meta[reply] = req_id
        _old_reply, buffer, _sent = self._relays[socket]
        self._relays[socket] = (reply, buffer, False)
        if _old_reply is not None:
            self._trace(req_id, "a live upstream reply was replaced")
        self._trace(req_id, "reply created (%r)" % (reply,))
        reply.readyRead.connect(lambda r=reply, s=socket: self._on_upstream_ready(s, r))
        reply.finished.connect(lambda r=reply, s=socket: self._on_upstream_finished(s, r))
        if hasattr(reply, "metaDataChanged"):
            reply.metaDataChanged.connect(lambda r=reply, i=req_id: self._on_upstream_meta(i, r))
        if hasattr(reply, "errorOccurred"):
            reply.errorOccurred.connect(lambda _e, r=reply, i=req_id: self._on_upstream_error(i, r))
        # The loader draining the socket re-opens the write gate; drain
        # the upstream reply then, because readyRead does not fire again
        # for the bytes that sat in the full buffer while the gate was
        # closed — without this a main-thread stall froze the relay
        # permanently (the leak-soak camera freeze).
        socket.bytesWritten.connect(lambda _b=0, s=socket, r=reply: self._on_socket_written(s, r))
        Logger.log("i", "Moonraker camera bridge relaying %s", shown)

    def _on_upstream_meta(self, req_id: int, reply: QNetworkReply) -> None:
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        self._trace(req_id, "reply metadata: HTTP %s" % code)

    def _on_upstream_error(self, req_id: int, reply: QNetworkReply) -> None:
        try:
            error = int(reply.error())
        except (AttributeError, TypeError):
            error = -1
        self._trace(req_id, "reply errorOccurred code=%d: %s" % (error, reply.errorString()))

    @staticmethod
    def _header_text(value, fallback: str) -> str:
        if isinstance(value, bytes):
            return value.decode("latin-1", errors="replace")
        if isinstance(value, str):
            return value
        return fallback

    def _on_upstream_ready(self, socket: QTcpSocket, reply: QNetworkReply) -> None:
        req_id = (self._trace_meta.get(socket) or {}).get("req", -1)
        meta = self._trace_meta.get(socket)
        try:
            available = int(reply.bytesAvailable())
        except (AttributeError, TypeError):
            available = -1
        if meta is not None and not meta["first_ready"] and available > 0:
            meta["first_ready"] = True
            self._trace(req_id, "first upstream readyRead bytes=%d" % available)
        relay = self._relays.get(socket)
        if relay is None or relay[0] is not reply:
            return
        if not self._first_upstream_bytes and available > 0:
            from .CameraTiming import mark
            self._first_upstream_bytes = True
            mark("T8", "first upstream bytes (req %d)" % req_id)
        if available > 0 and not self._stream_healthy:
            # Recovery is PROVEN bytes, once per failure transition —
            # never per readyRead (the 2026-09-19 review). A stale
            # reply (its relay already popped) cannot mark a stream
            # healthy — no consumer is listening.
            self._stream_healthy = True
            self.upstreamStarted.emit()
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
            self._trace(req_id, "first bytes written to local client (HTTP %d)" % code)
        if socket.bytesToWrite() < 1 << 20:
            chunk = bytes(reply.readAll())
            if chunk:
                socket.write(chunk)
                self._relayed_bytes += len(chunk)
                if meta is not None:
                    meta["relayed"] += len(chunk)

    def _on_socket_written(self, socket: QTcpSocket, reply: QNetworkReply) -> None:
        relay = self._relays.get(socket)
        if relay is None or relay[0] is not reply:
            return
        if socket.bytesToWrite() < 1 << 20:
            self._on_upstream_ready(socket, reply)

    def _on_upstream_finished(self, socket: QTcpSocket, reply: QNetworkReply) -> None:
        meta = self._trace_meta.get(socket)
        req_id = self._reply_meta.pop(reply, (meta or {}).get("req", -1))
        relay = self._relays.get(socket)
        try:
            error_code = int(reply.error())
        except (AttributeError, TypeError):
            error_code = -1
        try:
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        except AttributeError:
            code = None
        self._trace(req_id, "reply finished error=%d (%s) HTTP=%s aborted=%s"
                    % (error_code, reply.errorString(), code, bool(meta and meta["aborted"])))
        if relay is None or relay[0] is not reply:
            # A reply whose relay is already gone: still release the
            # NAM-owned object (the live report: finished replies and
            # accepted sockets accumulated while the tracking dict
            # stayed empty).
            try: reply.deleteLater()
            except Exception: pass
            return
        # Pop on every exit: the error path previously relied on the
        # abort->disconnected signal to clean up, a fragile dependency.
        # The meta stays until the socket closes so the disconnect
        # line keeps its request id.
        self._relays.pop(socket, None)
        try: reply.deleteLater()
        except Exception: pass
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._stream_healthy = False
            Logger.log("i", "Moonraker camera bridge upstream failed: %s (HTTP %s)", reply.errorString(), code)
            self.upstreamFailed.emit(f"camera upstream failed: {reply.errorString()}")
            if code is None or int(code) >= 400:
                # An auth refusal or a dead stream: nothing useful to
                # relay — close so the loader sees the failure.
                socket.abort()
                return
        # Drain the tail: a finite (snapshot) response's last bytes can
        # still sit in the reply buffer when finished fires — without
        # this the declared Content-Length is never met and the loader
        # sees a truncated body (the reviewer's drain). A dead-core
        # reply (the NAM already gone) has nothing to drain.
        try:
            tail = bytes(reply.readAll())
            if tail:
                socket.write(tail)
                self._relayed_bytes += len(tail)
        except Exception:
            pass
        # Flush any tail and close the response so a snapshot-style
        # reply terminates cleanly.
        try: socket.flush()
        except Exception: pass
        try: socket.disconnectFromHost()
        except Exception: pass

    def _on_socket_closed(self, socket: QTcpSocket) -> None:
        meta = self._trace_meta.pop(socket, None)
        req_id = (meta or {}).get("req", -1)
        if meta and "accepted" in meta:
            duration = "%.3fs" % (time.monotonic() - meta["accepted"])
        else:
            duration = "?"
        relay = self._relays.pop(socket, None)
        relayed = (meta or {}).get("relayed", 0)
        if relay is not None and relay[0] is not None:
            if meta is not None:
                meta["aborted"] = True
            self._trace(req_id, "local disconnected after %s and %d bytes; reply aborted because no consumers remain"
                        % (duration, relayed))
            try: relay[0].abort()
            except Exception: pass
            try: relay[0].deleteLater()
            except Exception: pass
        else:
            self._trace(req_id, "local disconnected after %s and %d bytes; no live upstream" % (duration, relayed))
        # The accepted socket is parented to the server, which never
        # destroys it — every connection must release itself (the live
        # report: 100 completed requests left 100 sockets alive).
        try: socket.deleteLater()
        except Exception: pass
