"""Coverage suite for the websocket owner and the HTTP transport.

``tests/test_socket_owner.py`` walks the happy paths against a scripted
loopback server; this file targets the branches that suite leaves dark —
handshake rejection and the header cap, client-side socket and TLS
errors, exact-id correlation, the transport's refusal-body parsing, its
size caps and lane cancellation.

Fidelity policy: whatever a real peer can produce is produced by one (a
real TCP or TLS listener, a real ``http.server``, a real QNAM). The two
drops in ``_finish_json`` need a pending entry whose reply object or
generation no longer matches, which no live socket can arrange, so those
alone drive the method with a scripted reply surface.

Lines that stay uncovered, and why:

- ``MoonrakerTransport.py:309`` — ``payload = {}``, the empty-body
  answer. PyQt hands back ``None`` for the null QByteArray that
  ``QNetworkReply.read()`` returns when nothing is buffered; the
  module now guards that conversion (an empty 200 arrives as the
  empty object — the agent's live find, fixed in the module and
  pinned by ``test_an_empty_body_is_the_empty_object``).

Every other statement in both modules is covered in this container. The
whole file skips without Qt, as does every other Qt suite here.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from tests.qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, runtime
from tests.ws_loopback import WSServer

if QT_AVAILABLE:
    from PyQt6.QtNetwork import (
        QAbstractSocket,
        QNetworkAccessManager,
        QNetworkReply,
        QSslError,
        QSslSocket,
    )

    import plugins.MoonrakerSocket as socket_module
    from plugins.MoonrakerSocket import MoonrakerSocket
    from plugins.SocketFraming import (
        MAX_HANDSHAKE_HEADER_BYTES,
        accept_value,
    )


# A throwaway self-signed 127.0.0.1 certificate, embedded so the TLS test
# needs no openssl at runtime and adds no fixture file. It exists only to
# be REJECTED — no peer is ever expected to trust it.
_TLS_CERT = b"""-----BEGIN CERTIFICATE-----
MIIDHDCCAgSgAwIBAgIUYd5F2TyzkwQD1bA1JSKv5GKrnMEwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJMTI3LjAuMC4xMCAXDTI2MDkxODE1MTUxMFoYDzIxMjYw
ODI1MTUxNTEwWjAUMRIwEAYDVQQDDAkxMjcuMC4wLjEwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQDCneRL7Lp2JOCzxl1z2D6jtVF5zF/hURZ+MEhDtVYi
6rQ4kC6MkobL4uvK0xoJYava3w0he480kDdSCALM5sBVUoCs6syZh4yza4HtlU6m
YRPNWgJ1VLP88g3lrZ+RPOtJojEj4C6M/EctGfCPcxajaQcAW88JgrtGUY09CKg3
GkQ2ruuG66hPfYSC43VBH/pRErjmR/2CoTOa7tx2imVQwC2guXT+I1HIYxUfmoYj
MdaN/wrdkABTCDzsCABnCBxY00ogTt3y7vd0n0xcA5lsoxl2KZ0nxFGH9JjGNQuS
kUmoqsMeB/C/0bXfhfb/WobEvYUqhhoM+zHB7m8mFIZLAgMBAAGjZDBiMB0GA1Ud
DgQWBBQmsaPFHogejYjmPR1duBg+fbLCXDAfBgNVHSMEGDAWgBQmsaPFHogejYjm
PR1duBg+fbLCXDAPBgNVHRMBAf8EBTADAQH/MA8GA1UdEQQIMAaHBH8AAAEwDQYJ
KoZIhvcNAQELBQADggEBAGNUUrjy3s+pYqebG7hxqok4G+MqYWAioG+8sVgIX5dj
6Jni2u07SHb3Ao9KOq7d/DqUkctKpbJXv/BraMuh0mGTywIRC30cU25yMrk599dO
IcirBWA/hYmPIXvwj5cLvwlcmJCbIZ/tvvQc2z1eQfPodishhuQufZQyimNdMS6l
Khw+Vpmgwra7gHy82VFX7zXPiS+4NNCrNhKzyNlIPC0R0NwbXa1UPe50AzgyGVhq
0hHNKHMWwprLquL/2Flfx4KLtKEl9Gxr+JeF2GL++dqivXbfp5wZdNKpgIHpBC/s
3RGywui+wjDeO3ZHi1hIby7xF7dG7po7VyaZn0F/2MI=
-----END CERTIFICATE-----
"""

_TLS_KEY = b"""-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDCneRL7Lp2JOCz
xl1z2D6jtVF5zF/hURZ+MEhDtVYi6rQ4kC6MkobL4uvK0xoJYava3w0he480kDdS
CALM5sBVUoCs6syZh4yza4HtlU6mYRPNWgJ1VLP88g3lrZ+RPOtJojEj4C6M/Ect
GfCPcxajaQcAW88JgrtGUY09CKg3GkQ2ruuG66hPfYSC43VBH/pRErjmR/2CoTOa
7tx2imVQwC2guXT+I1HIYxUfmoYjMdaN/wrdkABTCDzsCABnCBxY00ogTt3y7vd0
n0xcA5lsoxl2KZ0nxFGH9JjGNQuSkUmoqsMeB/C/0bXfhfb/WobEvYUqhhoM+zHB
7m8mFIZLAgMBAAECggEAP8anLDxZBGNkYXVlZZOdP1+pYg0X9InyoYqn+8w+fHXG
dmzFXFqeIs1rsM1KEH355+FFd836aWLDRjYK2OPbbnp5YXEWeE+Dy74010sg+YQB
jb9r49BMpV0bMcOacWlxv3EPwm5yQtBfcMe53gB8kDNYUIVFjRvSzCHoc3JVUazO
dPbqALRIXRQ+P7gibbnvuwPYvlAtovEnPGnlloz8s1uYrRc+j6hfnJHOFR60lio4
asvjxPpsYZoQoRsmUBwPXl+AEeo7+htt1Nl4uBeu5YPCDhL1ny5IELEmyvTPmXZU
1bd5GZ1ittfus+ugKREtX20PCHoyuCBMmyyufS1OmQKBgQD1A+A7gB6MH727zyeD
bMQ97Cpm+JGcZnUQWR1JKIBp6fg6rkGfKrrNk7FKhsawmkqule0M3sqRAiB1lk51
D5jmaa7cuvTAeXVHLaijV60drrOmyPR1TiFdKjnmtQhDPdo36H+edmDu2DUUXBZa
e52kPHWhNeih/2mjij9rcqdBwwKBgQDLV5N/y/wISy6I6aHBL4Yu/mHy3VdaiOQu
NG/WWXs2j/SGAgrj+2OEy+ssKDvPU3S5HN6gyc1pNnMXSUhuLRiYHrqAOX0dEjUe
lmstbffWsfc/r0+0hDaFc2c51azL+aWoGNyTHE7EA2nbaqZ+/5UphU04HD8SdHY7
a+VTdZaY2QKBgQDamiKt85lNm+REdIbkk15jgRoz1QLVb/RyKZAffatU1BNNB7w0
roMMeAFuZOFRH9gR/GILYZuJ1UPwpbJKbygUK6Z/+a31LGieoPYdBlTGbuCKpjC1
mIfb/5i5ZjuBAuZ3i1CRqTzC3NQ+3gpzoGb96ZNpyhdMsf0mlGoRC7J+0QKBgBfp
nZvqxiHHjZpmNzJ7v0Dpg2VHKE2qSYMxgXFopne9KBlWRieXWZW1UVupA5eXDePz
BC9ObAySbahwYscqIHlLK82GdTMlAAAv2jzGcN3boeLoX+wvnCXHc302ppJ6MkP5
YH8gVhbw+9Lk0N2ges8eMP2HPNeTwI+uWZKeAm7JAoGBALZlEuf5qxXswzYmeFea
5JXfU0vuKjUNLo82S+zHjyhokCuy1rCOhyd1yqPxQcRaoqMoikF1yQPqsPRPJiEQ
vNKixNaEA2gbT3fhASlUW7JqzybFbPZbAQRbXJDGq5RaFyOf36AzQMrAVwt7re4D
gteEEdoamKteJ7qWDn1zXjOZ
-----END PRIVATE KEY-----
"""


def server_frame(opcode: int, payload: bytes) -> bytes:
    """An unmasked server frame — §5.1 forbids a server from masking."""
    if len(payload) < 126:
        return bytes([0x80 | opcode, len(payload)]) + payload
    return bytes([0x80 | opcode, 126]) + struct.pack(">H", len(payload)) + payload


def server_text(message: dict) -> bytes:
    return server_frame(0x1, json.dumps(message).encode("utf-8"))


def payload_bytes(message: dict) -> bytes:
    """The JSON-RPC payload alone, which is what ``_on_message`` sees."""
    return json.dumps(message).encode("utf-8")


def _remove_tree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class _UpgradeResponder:
    """A one-shot TCP (or TLS) listener answering one upgrade request.

    Its response is the real parse's input, never a stub: the 401 text is
    what the settings dialog shows the user.
    """

    def __init__(self, response: bytes, tls_context=None) -> None:
        self._response = response
        self._tls = tls_context
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        try:
            if self._tls is not None:
                conn = self._tls.wrap_socket(conn, server_side=True)
            conn.settimeout(2.0)
            conn.recv(65536)
            conn.sendall(self._response)
            # Hold the socket until the client tears it down, so the
            # response is never lost to a close race.
            while conn.recv(4096):
                pass
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


class _DeadSocket:
    """A socket surface for the states Qt will not stage on demand."""

    def __init__(self, *, fail: bool = False, unconnected: bool = False,
                 delete_raises: bool = False) -> None:
        self._fail = fail
        self._unconnected = unconnected
        self._delete_raises = delete_raises
        self.written = []

    def state(self):
        if self._unconnected:
            return QAbstractSocket.SocketState.UnconnectedState
        return QAbstractSocket.SocketState.ConnectedState

    def write(self, payload) -> int:
        if self._fail:
            raise OSError("device not open")
        self.written.append(payload)
        return len(payload)

    def flush(self) -> None:
        pass

    def disconnectFromHost(self) -> None:
        pass

    def deleteLater(self) -> None:
        if self._delete_raises:
            raise RuntimeError("wrapped C/C++ object has been deleted")


class _TransportHandler(PipeSafeHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def _write(self, status, body=b"", content_type="application/json"):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._send(body)

    def _send(self, body):
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _chunked(self, body):
        # No Content-Length: the client can only learn the size by
        # reading, which is what the streamed cap has to catch.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for start in range(0, len(body), 512):
            chunk = body[start:start + 512]
            self._send(b"%X\r\n%s\r\n" % (len(chunk), chunk))
        self._send(b"0\r\n\r\n")

    def _route(self):
        path = str(self.path).split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        self.server.record(path, self.command, self.rfile.read(length) if length else b"")
        if path == "/ok":
            self._write(200, b'{"result": "fine"}')
        elif path == "/empty":
            self._write(200, b"")
        elif path == "/nonobject":
            self._write(200, b"[1, 2, 3]")
        elif path == "/servererror":
            self._write(200, b'{"error": {"code": 400, "message": "Extrude below minimum temp"}}')
        elif path == "/flat_error":
            self._write(200, b'{"error": "printer said no"}')
        elif path == "/forbidden":
            self._write(403, b'{"error": {"code": 403, "message": "File currently in use"}}')
        elif path == "/plain_refusal":
            # A refusal body with no "error" key at all.
            self._write(403, b'{"message": "Not authorised"}')
        elif path == "/html401":
            # An auth gateway's page, not Moonraker's JSON.
            self._write(401, b"<html><body>denied</body></html>", content_type="text/html")
        elif path == "/big":
            self._write(200, b'{"pad": "' + b"x" * 4096 + b'"}')
        elif path == "/chunked_big":
            self._chunked(b'{"pad": "' + b"x" * 4096 + b'"}')
        elif path == "/gate":
            # Holds the lane open until the test releases it, so "still
            # running" never depends on a sleep.
            self.server.gate.wait(5.0)
            self._write(200, b'{"result": "gate"}')
        else:
            self._write(404, b'{"error": "not found"}')


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address=("127.0.0.1", 0)) -> None:
        super().__init__(address, _TransportHandler)
        self.gate = threading.Event()
        self.requests = []
        self.lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d" % self.server_address[1]

    def record(self, path, command, body) -> None:
        with self.lock:
            self.requests.append((path, command, body))


class _Vanished:
    """A reply whose C++ object is gone: every touch raises, as PyQt does."""

    def isRunning(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    def abort(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    def deleteLater(self):
        raise RuntimeError("wrapped C/C++ object has been deleted")


class _ScriptedReply:
    """The QNetworkReply surface ``_finish_json`` reads, scripted."""

    def __init__(self, body: bytes = b"", *, delete_raises: bool = False) -> None:
        self._body = body
        self._delete_raises = delete_raises
        self.deleted = False

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def errorString(self) -> str:
        return ""

    def read(self, _count: int) -> bytes:
        return self._body

    def bytesAvailable(self) -> int:
        return 0

    def header(self, _name):
        return None

    def attribute(self, _name):
        return None

    def isRunning(self) -> bool:
        return True

    def abort(self) -> None:
        pass

    def deleteLater(self) -> None:
        if self._delete_raises:
            raise RuntimeError("wrapped C/C++ object has been deleted")
        self.deleted = True


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class SocketCase(unittest.TestCase):
    """Shared pump-and-observe harness for the socket owner."""

    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self._owners = []
        self.addCleanup(self._stop_owners)

    def _stop_owners(self):
        for owner in self._owners:
            owner.stop()

    def owner(self, **kwargs):
        instance = MoonrakerSocket(**kwargs)
        self._owners.append(instance)
        self.failures = []
        instance.failed.connect(self.failures.append)
        return instance

    def wait_until(self, predicate, timeout=5.0):
        """Pump the Qt loop while polling — socket traffic only moves
        inside an event dispatch."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.app.processEvents()
            time.sleep(0.005)
        return False

    def loopback(self, address=("127.0.0.1", 0)):
        server = WSServer(address)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def upgraded(self, server, core=None, aux=None, **kwargs):
        """A connected, upgraded owner against the loopback server."""
        instance = self.owner(**kwargs)
        instance.start(self.ws_url(server), "", set(core or ()), set(aux or ()))
        self.assertTrue(self.wait_until(lambda: instance.is_upgraded), "never upgraded")
        return instance

    def ws_url(self, server, scheme="ws"):
        return "%s://127.0.0.1:%d/websocket" % (scheme, server.server_address[1])

    def raw_upgrade(self, response, tls_context=None):
        responder = _UpgradeResponder(response, tls_context)
        self.addCleanup(responder.close)
        return responder


class HandshakeCornerTests(SocketCase):
    """The 101 stage: what a hostile or broken peer makes it do."""

    def test_header_cap_fails_before_any_frame_parsing(self):
        instance = self.owner()
        instance._buffer = b"x" * (MAX_HANDSHAKE_HEADER_BYTES + 1)
        instance._process_handshake()
        self.assertTrue(self.failures)
        self.assertIn("header cap", self.failures[0])
        self.assertFalse(instance.is_upgraded)

    def test_a_split_handshake_is_held_until_it_is_complete(self):
        # A partial read is not a verdict: no failure, no upgrade, and the
        # bytes stay buffered for the next readyRead.
        instance = self.owner()
        instance._buffer = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: webs"
        instance._process_handshake()
        self.assertEqual(self.failures, [])
        self.assertFalse(instance.is_upgraded)
        self.assertTrue(instance._buffer.startswith(b"HTTP/1.1 101"))

    def test_a_rejected_api_key_is_named_as_such(self):
        responder = self.raw_upgrade(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
        instance = self.owner()
        instance.start("ws://127.0.0.1:%d/websocket" % responder.port, "bad", set(), set())
        self.assertTrue(self.wait_until(lambda: self.failures), "no failure reported")
        self.assertEqual(self.failures[0], "the API key was rejected (HTTP 401)")
        self.assertFalse(instance.is_upgraded)

    def test_a_non_101_response_is_refused_with_its_status(self):
        responder = self.raw_upgrade(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        self.owner().start("ws://127.0.0.1:%d/websocket" % responder.port, "", set(), set())
        self.assertTrue(self.wait_until(lambda: self.failures), "no failure reported")
        self.assertIn("handshake refused", self.failures[0])
        self.assertIn("200", self.failures[0])

    def test_an_accept_value_for_another_key_is_refused(self):
        response = (
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
            + accept_value("another-key").encode() + b"\r\n\r\n"
        )
        responder = self.raw_upgrade(response)
        self.owner().start("ws://127.0.0.1:%d/websocket" % responder.port, "", set(), set())
        self.assertTrue(self.wait_until(lambda: self.failures), "no failure reported")
        self.assertEqual(self.failures[0], "Sec-WebSocket-Accept mismatch")

    def test_a_default_port_is_left_out_of_the_host_header(self):
        try:
            server = WSServer(("127.0.0.1", 80))
        except OSError:
            self.skipTest("port 80 is not bindable in this runtime")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        instance = self.owner()
        instance.start("ws://127.0.0.1/websocket", "", set(), set())
        self.assertTrue(self.wait_until(lambda: instance.is_upgraded), "never upgraded")
        self.assertTrue(server.wait_for(lambda s: s.handshakes))
        self.assertEqual(server.handshakes[0].get("host"), "127.0.0.1")

    def test_a_non_default_port_rides_the_host_header(self):
        server = self.loopback()
        self.upgraded(server)
        self.assertTrue(server.wait_for(lambda s: s.handshakes))
        self.assertEqual(
            server.handshakes[0].get("host"),
            "127.0.0.1:%d" % server.server_address[1],
        )

    def test_frames_pipelined_behind_the_101_are_not_dropped(self):
        # Moonraker may push state in the same TCP segment as the upgrade;
        # anything already buffered must be parsed, not lost.
        instance = self.owner()
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        instance._key = key
        head = (
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept_value(key)
        ).encode("ascii")
        ready = []
        instance.klippyReady.connect(lambda: ready.append(True))
        instance._buffer = head + server_text({"jsonrpc": "2.0", "method": "notify_klippy_ready"})
        instance._process_handshake()
        self.assertTrue(instance.is_upgraded)
        self.assertEqual(ready, [True])


class FrameRoutingTests(SocketCase):
    """Per-frame outcomes on a live feed."""

    def test_a_server_close_frame_ends_the_feed(self):
        server = self.loopback()
        instance = self.upgraded(server)
        instance._buffer = server_frame(0x8, struct.pack(">H", 1000))
        instance._process_buffer()
        self.assertFalse(instance.is_upgraded)
        self.assertTrue(
            self.wait_until(lambda: any(entry[0] == "close" for entry in server.inbound)),
            "the close was never acknowledged on the wire",
        )

    def test_a_protocol_violation_event_fails_the_connection(self):
        instance = self.owner()
        instance._buffer = bytes([0xC1, 0x00])  # reserved bits set
        instance._process_buffer()
        self.assertEqual(self.failures, ["reserved bits set"])
        self.assertFalse(instance.is_upgraded)

    def test_a_parser_that_raises_fails_closed(self):
        # The parser contract is "raise or emit"; the raise half must tear
        # the feed down rather than escape the callback.
        from plugins.SocketFraming import FramingError
        server = self.loopback()
        instance = self.upgraded(server)
        original = socket_module.parse_frames

        def explode(_buffer, _state):
            raise FramingError("parser gave up")

        socket_module.parse_frames = explode
        self.addCleanup(setattr, socket_module, "parse_frames", original)
        instance._buffer = server_text({"jsonrpc": "2.0"})
        instance._process_buffer()
        self.assertEqual(self.failures, ["parser gave up"])
        self.assertFalse(instance.is_upgraded)

    def test_an_undecodable_frame_fails_the_connection(self):
        instance = self.owner()
        instance._on_message(b"\xff\xfe\xfd")
        self.assertEqual(self.failures, ["undecodable JSON-RPC frame"])

    def test_a_non_object_frame_fails_the_connection(self):
        instance = self.owner()
        instance._on_message(b"[1, 2, 3]")
        self.assertEqual(self.failures, ["non-object JSON-RPC frame"])

    def test_klippy_lifecycle_notifications_reach_their_signals(self):
        instance = self.owner()
        ready, lost = [], []
        instance.klippyReady.connect(lambda: ready.append(True))
        instance.klippyLost.connect(lost.append)
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "method": "notify_klippy_ready"}))
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "method": "notify_klippy_shutdown"}))
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "method": "notify_klippy_disconnected"}))
        self.assertEqual(ready, [True])
        self.assertEqual(lost, ["notify_klippy_shutdown", "notify_klippy_disconnected"])

    def test_an_unsubscribed_notification_is_ignored(self):
        instance = self.owner()
        instance._on_message(payload_bytes(
            {"jsonrpc": "2.0", "method": "notify_gcode_response", "params": ["ok"]}))
        self.assertEqual(self.failures, [])
        self.assertEqual(instance.drain_core(), (None, 0.0))

    def test_a_status_notification_without_a_patch_is_ignored(self):
        # A malformed notify must not touch accumulated state.
        instance = self.owner()
        instance._core_names = {"print_stats"}
        instance._on_message(payload_bytes(
            {"jsonrpc": "2.0", "method": "notify_status_update", "params": [7]}))
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "method": "notify_status_update"}))
        self.assertIsNone(instance.drain_core()[0])

    def test_a_message_carrying_both_id_and_method_is_a_notification(self):
        # Only a message with an id and no method is a reply; anything else
        # must not be allowed to satisfy a pending request.
        instance = self.owner()
        instance._core_names = {"print_stats"}
        instance._pending[3] = (lambda _reply: self.failures.append("consumed"), instance._generation)
        instance._on_message(payload_bytes({
            "jsonrpc": "2.0", "id": 3, "method": "notify_status_update",
            "params": [{"print_stats": {"state": "printing"}}],
        }))
        self.assertEqual(self.failures, [])
        self.assertIn(3, instance._pending)
        self.assertEqual(instance.drain_core()[0], {"print_stats": {"state": "printing"}})

    def test_a_reply_is_matched_by_exact_id_only(self):
        instance = self.owner()
        seen = []
        instance._pending[7] = (seen.append, instance._generation)
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "result": {"wrong": True}, "id": 4242}))
        self.assertEqual(seen, [], "an unknown id satisfied a live request")
        self.assertIn(7, instance._pending)
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "result": {"right": True}, "id": 7}))
        self.assertEqual(seen[0]["result"], {"right": True})
        self.assertNotIn(7, instance._pending)

    def test_a_reply_from_a_previous_generation_is_dropped(self):
        # Teardown bumps the generation; a late reply from the old
        # connection must not run the old consumer's callback.
        instance = self.owner()
        seen = []
        instance._pending[9] = (seen.append, instance._generation)
        instance._generation += 1
        instance._on_message(payload_bytes({"jsonrpc": "2.0", "result": {}, "id": 9}))
        self.assertEqual(seen, [])
        self.assertNotIn(9, instance._pending)

    def test_a_ping_from_the_printer_is_answered_with_a_matching_pong(self):
        server = self.loopback()
        instance = self.upgraded(server)
        instance._buffer = server_frame(0x9, b"probe")
        instance._process_buffer()
        self.assertTrue(
            self.wait_until(lambda: any(
                entry[0] == "pong" and entry[1] == b"probe" for entry in server.inbound)),
            "the ping was not answered",
        )

    def test_aux_fragments_merge_instead_of_replacing(self):
        # Moonraker pushes only the fields that changed; a one-shot field
        # must survive the next progress-only fragment in the auxiliary
        # accumulator just as it does in the core one.
        instance = self.owner()
        instance._aux_names = {"print_stats"}
        instance._apply_patch({"print_stats": {"message": "M117 hello"}})
        instance._apply_patch({"print_stats": {"progress": 0.4}})
        aux, stamp = instance.drain_aux()
        self.assertEqual(aux["print_stats"], {"message": "M117 hello", "progress": 0.4})
        self.assertGreater(stamp, 0.0)
        self.assertIsNone(instance.drain_aux()[0])

    def test_a_reply_refreshes_the_keepalive_clock(self):
        # The clock is fed by the reply that comes back, not the request
        # that went out — otherwise a dead peer looks live.
        server = self.loopback()
        instance = self.upgraded(server)
        instance._last_auth_reply_at = 0.0
        self.assertGreater(instance.request("server.info", {}, lambda _reply: None), 0)
        self.assertTrue(
            self.wait_until(lambda: instance.last_auth_reply_at > 0.0),
            "the reply never stamped the auth clock",
        )


class SubscribeCornerTests(SocketCase):
    """The subscribe reply is the one place a capability failure and a
    status policy are told apart."""

    def test_a_structured_refusal_is_a_capability_failure(self):
        server = self.loopback()
        instance = self.upgraded(server)
        refused, snapshots = [], []
        instance.subscribeRefused.connect(refused.append)
        instance.syncSnapshot.connect(lambda status, stamp: snapshots.append(status))
        server.queue(("reply", {"error": {"code": -32602, "message": "Invalid params"}}))
        instance.subscribe({"print_stats": None})
        self.assertTrue(self.wait_until(lambda: refused), "no refusal surfaced")
        self.assertEqual(refused[0]["message"], "Invalid params")
        self.assertEqual(snapshots, [], "a refusal must not be rendered as a snapshot")

    def test_a_reply_without_a_status_object_publishes_nothing(self):
        server = self.loopback()
        instance = self.upgraded(server)
        snapshots = []
        instance.syncSnapshot.connect(lambda status, stamp: snapshots.append(status))
        for result in ({}, "not a status"):
            before = instance.last_auth_reply_at
            server.queue(("reply", {"result": result}))
            instance.subscribe({"print_stats": None})
            self.assertTrue(
                self.wait_until(lambda b=before: instance.last_auth_reply_at > b),
                "the reply never landed",
            )
        self.assertEqual(snapshots, [])
        self.assertEqual(self.failures, [])

    def test_a_re_subscribe_replaces_the_aux_subset_and_seeds_it(self):
        # The wanted set grows mid-print: routing follows the latest
        # subscribe, and the sync answering it seeds the accumulator so an
        # object that never changes still reaches the next drain.
        server = self.loopback()
        instance = self.upgraded(server, aux={"bed_mesh"})
        self.assertEqual(instance.subscribed_names, ["bed_mesh"])
        landed = []
        instance.syncSnapshot.connect(lambda status, stamp: landed.append(status))
        server.queue(("reply", {"result": {"status": {"heater_bed": {"temperature": 61}}}}))
        instance.subscribe({"heater_bed": None}, aux_names={"heater_bed"})
        self.assertEqual(instance.subscribed_names, ["heater_bed"])
        self.assertTrue(self.wait_until(lambda: landed), "the sync never arrived")
        self.assertTrue(self.wait_until(lambda: server.requests), "the subscribe never reached the printer")
        self.assertEqual(list(server.requests[-1]["params"]["objects"]), ["heater_bed"])
        aux, stamp = instance.drain_aux()
        self.assertEqual(aux["heater_bed"], {"temperature": 61})
        self.assertGreater(stamp, 0.0)
        self.assertIsNone(instance.drain_aux()[0])


class SocketFailureTests(SocketCase):
    """Client-side failures: nothing here may be silent."""

    def test_a_refused_connect_reports_the_socket_error(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        instance = self.owner()
        instance.start("ws://127.0.0.1:%d/websocket" % port, "", set(), set())
        self.assertTrue(self.wait_until(lambda: self.failures), "a refused connect was silent")
        self.assertTrue(self.failures[0], "the failure carried no reason")
        self.assertFalse(instance.is_upgraded)

    def test_wss_never_trusts_an_untrusted_certificate(self):
        cert_path, key_path = self._tls_files()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        responder = self.raw_upgrade(b"HTTP/1.1 101 Switching Protocols\r\n\r\n", tls_context=context)
        instance = self.owner()
        instance.start("wss://127.0.0.1:%d/websocket" % responder.port, "key", set(), set())
        self.assertIsInstance(instance._socket, QSslSocket)
        self.assertTrue(self.wait_until(lambda: self.failures), "a self-signed peer was not refused")
        self.assertFalse(instance.is_upgraded)

    def test_ssl_errors_are_surfaced_rather_than_ignored(self):
        instance = self.owner()
        instance.start("wss://127.0.0.1:1/websocket", "", set(), set())
        ssl_socket = instance._socket
        self.assertIsInstance(ssl_socket, QSslSocket)
        problems = [
            QSslError(QSslError.SslError.SelfSignedCertificate),
            QSslError(QSslError.SslError.HostNameMismatch),
        ]
        ssl_socket.sslErrors.emit(problems)
        expected = "; ".join(str(problem.errorString()) for problem in problems)
        self.assertEqual(self.failures, [expected])

    def test_ssl_errors_with_no_text_still_report_a_failure(self):
        instance = self.owner()
        instance.start("wss://127.0.0.1:1/websocket", "", set(), set())
        instance._socket.sslErrors.emit([])
        self.assertEqual(self.failures, ["TLS verification failed"])

    def test_a_torn_down_connection_ignores_its_late_signals(self):
        # Teardown detaches the socket, but it can still deliver a connect
        # that completed in flight, buffered bytes or a socket error. None
        # of it may reach the connection that replaced it.
        server = self.loopback()
        instance = self.upgraded(server)
        detached = instance._socket
        instance.stop()
        detached.connected.emit()
        detached.errorOccurred.emit(QAbstractSocket.SocketError.ConnectionRefusedError)
        detached.readyRead.emit()
        self.assertEqual(self.failures, [])
        self.assertFalse(instance.is_upgraded)

    def test_a_stale_ssl_error_after_teardown_is_ignored(self):
        instance = self.owner()
        instance.start("wss://127.0.0.1:1/websocket", "", set(), set())
        ssl_socket = instance._socket
        instance.stop()
        ssl_socket.sslErrors.emit([QSslError(QSslError.SslError.SelfSignedCertificate)])
        self.assertEqual(self.failures, [], "a torn-down connection still reported its old errors")

    def _tls_files(self):
        directory = tempfile.mkdtemp(prefix="moonraker-tls-")
        self.addCleanup(_remove_tree, directory)
        cert_path = os.path.join(directory, "cert.pem")
        key_path = os.path.join(directory, "key.pem")
        for path, payload in ((cert_path, _TLS_CERT), (key_path, _TLS_KEY)):
            with open(path, "wb") as handle:
                handle.write(payload)
        return cert_path, key_path


class SocketWriteTests(SocketCase):
    """Writes that cannot land must leave no state behind."""

    def test_a_request_before_the_upgrade_is_not_written(self):
        # A frame written ahead of the handshake would corrupt the wire.
        fresh = self.owner()
        self.assertEqual(fresh.request("server.info", {}, lambda _reply: None), 0)
        pending = self.owner()
        pending._socket = _DeadSocket()
        self.assertEqual(pending.request("server.info", {}, lambda _reply: None), 0)
        self.assertEqual(pending._pending, {})

    def test_a_failed_request_write_drops_its_pending_entry(self):
        instance = self.owner()
        instance._socket = _DeadSocket(fail=True)
        instance._upgraded = True
        self.assertEqual(instance.request("server.info", {}, lambda _reply: None), 0)
        self.assertEqual(instance._pending, {})

    def test_control_writes_are_skipped_without_a_socket(self):
        instance = self.owner()
        instance._write_control(b"\x88\x00")
        instance._send_keepalive()
        self.assertEqual(instance._pending, {})

    def test_a_failed_control_write_is_swallowed(self):
        instance = self.owner()
        instance._socket = _DeadSocket(fail=True)
        instance._write_control(b"\x88\x00")

    def test_the_keepalive_pings_before_any_reply_has_landed(self):
        # Zero is "no reply yet", not "overdue": a fresh feed must not be
        # failed by its own first timer tick.
        server = self.loopback()
        instance = self.upgraded(server)
        instance._last_auth_reply_at = 0.0
        instance._send_keepalive()
        self.assertEqual(sorted(instance._pending), [1])

    def test_stop_keeps_the_close_frame_off_an_unconnected_socket(self):
        instance = self.owner()
        stub = _DeadSocket(unconnected=True)
        instance._socket = stub
        instance.stop()
        self.assertEqual(stub.written, [])
        self.assertIsNone(instance._socket)

    def test_stop_before_start_is_harmless(self):
        instance = self.owner()
        instance.stop()
        self.assertEqual(instance.generation, 1)
        self.assertFalse(instance.is_upgraded)
        self.assertEqual(instance.subscribed_names, [])

    def test_teardown_survives_a_socket_that_cannot_be_deleted(self):
        instance = self.owner()
        instance._socket = _DeadSocket(unconnected=True, delete_raises=True)
        instance.stop()
        self.assertEqual(instance.generation, 1)

    def test_an_overdue_keepalive_fails_the_connection(self):
        # An idle printer legitimately pushes nothing, so the application
        # round trip is the only liveness evidence there is.
        server = self.loopback()
        instance = self.upgraded(server, keepalive_interval_ms=50, keepalive_deadline_ms=150)
        instance._last_auth_reply_at = time.monotonic() - 1.0
        instance._send_keepalive()
        self.assertEqual(self.failures, ["keepalive reply deadline exceeded"])
        self.assertFalse(instance.is_upgraded)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class TransportCase(unittest.TestCase):
    """Shared harness for the HTTP transport: a real server, a real QNAM."""

    def setUp(self):
        self.server = _HttpServer()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self._cm = runtime()
        self.harness = self._cm.__enter__()
        self.addCleanup(self._cm.__exit__, None, None, None)
        self.app = self.harness.app
        self.module = self.harness.load("MoonrakerTransport")
        self.transport = self.module.MoonrakerHttpTransport()
        self.addCleanup(self.transport.close)
        self.addCleanup(self.server.gate.set)
        self.transport.configure(self.server.base_url, "test-key")
        self.calls = []
        self.log = self.module.Logger.log

    def callback(self):
        """A consumer callback recording its (payload, error) verdict."""
        def record(payload, error):
            self.calls.append((payload, error))
        return record

    def deliver(self, owner="o", channel="c", method="GET", path="/ok", **kwargs):
        """Send one request and wait for THIS consumer's verdict."""
        before = len(self.calls)
        self.assertTrue(self.transport.send_json(owner, channel, method, path, self.callback(), **kwargs))
        self.assertTrue(self.poll(lambda: len(self.calls) > before), "the reply never reached the consumer")
        return self.calls[-1]

    def poll(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.app.processEvents()
            time.sleep(0.005)
        return False

    def logged(self, level, fragment):
        return [call for call in self.log.call_args_list
                if call.args and call.args[0] == level and fragment in str(call.args[1])]


class TransportRequestTests(TransportCase):
    """Request building: the key's origin rule and the timeout."""

    def test_the_request_carries_its_headers_and_transfer_timeout(self):
        request = self.transport.request("/server/info", timeout_ms=1234)
        self.assertEqual(request.url().toString(), self.server.base_url + "/server/info")
        self.assertEqual(bytes(request.rawHeader(b"Accept")), b"application/json")
        self.assertEqual(bytes(request.rawHeader(b"User-Agent")), b"Cura Moonraker Print Follower")
        self.assertEqual(request.transferTimeout(), 1234)

    def test_an_absolute_url_is_used_verbatim(self):
        request = self.transport.request("http://127.0.0.1:9/elsewhere")
        self.assertEqual(request.url().host(), "127.0.0.1")
        self.assertEqual(request.url().port(), 9)

    def test_a_target_off_the_printers_origin_carries_no_key(self):
        # Fail-closed: a webcam host or tunnel alias must never see the
        # printer's key.
        request = self.transport.request("http://127.0.0.1:9/elsewhere")
        self.assertEqual(bytes(request.rawHeader(b"X-Api-Key")), b"")

    def test_a_stray_scheme_cannot_redirect_the_key_off_origin(self):
        # A pasted URL with a scheme the transport does not honour is
        # treated as a path, so the key still only reaches the configured
        # printer origin — never the host the string named.
        request = self.transport.request("ftp://elsewhere/x")
        self.assertEqual(request.url().host(), "127.0.0.1")
        self.assertEqual(request.url().port(), self.server.server_address[1])

    def test_the_shared_manager_and_the_generation_stay_reachable(self):
        # Streaming download and upload run their own reply lifecycles on
        # the same manager; the generation is what invalidates a stale
        # reply after a reconfigure.
        self.assertIsInstance(self.transport.network, QNetworkAccessManager)
        before = self.transport.generation
        self.assertFalse(self.transport.configure(self.server.base_url, "test-key"))
        self.assertEqual(self.transport.generation, before)
        self.assertTrue(self.transport.configure(self.server.base_url, "another-key"))
        self.assertEqual(self.transport.generation, before + 1)

    def test_configure_reports_whether_anything_changed(self):
        self.assertFalse(self.transport.configure(self.server.base_url, "test-key"))
        self.assertTrue(self.transport.configure(self.server.base_url, "another-key"))
        self.assertEqual(self.transport.identity, (self.server.base_url, "another-key"))


class TransportReplyTests(TransportCase):
    """What the consumer is handed for each reply shape."""

    def test_a_plain_object_body_is_delivered_without_error(self):
        payload, error = self.deliver()
        self.assertEqual(payload, {"result": "fine"})
        self.assertIsNone(error)

    def test_an_empty_body_is_the_empty_object(self):
        # A zero-length 200 is a legitimate reply ("{}" endpoints) and
        # must arrive as the empty object, not a failure — the None
        # QByteArray conversion used to raise (the coverage agent's
        # live find, fixed in the module).
        payload, error = self.deliver(path="/empty")
        self.assertEqual(payload, {})
        self.assertIsNone(error)

    def test_a_refusal_body_without_an_error_key_still_names_the_server(self):
        payload, error = self.deliver(path="/plain_refusal")
        self.assertEqual(error, "Not authorised")
        self.assertEqual(payload, {"message": "Not authorised"})

    def test_a_body_that_is_not_an_object_is_a_failure(self):
        payload, error = self.deliver(path="/nonobject")
        self.assertIn("non-object JSON", error)
        self.assertIsNone(payload)

    def test_a_200_with_an_error_object_keeps_the_servers_words(self):
        # The server's ANSWER stays in the payload alongside the error:
        # consumers need "the printer refused this" told apart from a
        # transport-level failure.
        payload, error = self.deliver(path="/servererror")
        self.assertEqual(error, "Extrude below minimum temp")
        self.assertEqual(payload["error"]["code"], 400)

    def test_a_200_with_a_flat_error_string_is_surfaced(self):
        payload, error = self.deliver(path="/flat_error")
        self.assertEqual(error, "printer said no")
        self.assertIsNotNone(payload)

    def test_a_json_refusal_body_is_read_and_named(self):
        payload, error = self.deliver(path="/forbidden")
        self.assertEqual(error, "File currently in use")
        self.assertEqual(payload["error"]["code"], 403)

    def test_an_html_refusal_names_the_status_instead(self):
        payload, error = self.deliver(path="/html401")
        self.assertEqual(error, "the API key was rejected (HTTP 401)")
        self.assertIsNone(payload)

    def test_the_size_cap_rejects_a_body_ahead_of_reading_it(self):
        with mock.patch.object(self.module, "MAX_REPLY_BYTES", 512):
            payload, error = self.deliver(path="/big")
        self.assertIn("size cap", error)
        self.assertIsNone(payload)

    def test_the_size_cap_catches_a_body_with_no_declared_length(self):
        # Streamed replies declare nothing; only the bytes read can reveal
        # that the cap was blown.
        with mock.patch.object(self.module, "MAX_REPLY_BYTES", 512):
            payload, error = self.deliver(path="/chunked_big")
        self.assertIn("size cap", error)
        self.assertIsNone(payload)

    def test_a_post_body_is_json_encoded_and_typed(self):
        _payload, error = self.deliver(method="POST", path="/ok", body={"a": 1, "b": [2]})
        self.assertIsNone(error)
        path, command, body = self.server.requests[-1]
        self.assertEqual(command, "POST")
        self.assertEqual(json.loads(body.decode()), {"a": 1, "b": [2]})

    def test_a_raw_body_is_sent_untouched(self):
        self.deliver(method="POST", path="/ok", body=b"raw-bytes")
        self.assertEqual(self.server.requests[-1][2], b"raw-bytes")

    def test_a_delete_really_deletes(self):
        # The old else-branch silently downgraded unknown verbs to GET.
        self.deliver(method="DELETE", path="/ok")
        self.assertEqual(self.server.requests[-1][1], "DELETE")

    def test_an_unknown_verb_is_refused_rather_than_downgraded(self):
        self.assertRaises(
            ValueError,
            self.transport.send_json, "o", "c", "PATCH", "/ok", self.callback(),
        )

    def test_the_diagnostics_toggle_adds_a_debug_line(self):
        self.transport.set_trace_http(True)
        self.deliver()
        self.assertTrue(self.logged("d", "outcome=ok"), "the trace line never appeared")

    def test_tracing_stays_off_by_default(self):
        self.deliver()
        self.assertEqual(self.logged("d", "outcome=ok"), [])

    def test_a_failure_is_logged_at_warning_level(self):
        self.deliver(path="/forbidden")
        self.assertTrue(self.logged("w", "failed:"), "a failure went unlogged")


class TransportLaneTests(TransportCase):
    """One lane per owner/channel pair, replaced or retired under control."""

    def test_a_busy_lane_refuses_a_second_request(self):
        # Without this the poll cadence would stack requests on a slow
        # printer until the connection pool is exhausted.
        self.assertTrue(self.transport.send_json("o", "c", "GET", "/gate", self.callback()))
        self.assertFalse(self.transport.send_json("o", "c", "GET", "/ok", self.callback()))

    def test_replace_retires_the_request_it_supersedes(self):
        self.assertTrue(self.transport.send_json("o", "c", "GET", "/gate", self.callback()))
        self.assertTrue(self.transport.send_json("o", "c", "GET", "/ok", self.callback(), replace=True))
        self.assertTrue(self.poll(lambda: self.calls), "the replacement never reported")
        self.assertEqual(self.calls[-1], ({"result": "fine"}, None))
        self.assertEqual(len(self.calls), 1, "the superseded request still reached the consumer")

    def test_cancelling_an_unknown_lane_is_a_no_op(self):
        self.transport.cancel("nobody", "nothing")
        self.assertEqual(self.calls, [])

    def test_cancelling_a_vanished_reply_does_not_raise(self):
        self.transport._pending["drop::c"] = self.module._PendingRequest(
            _Vanished(), 1, "GET", "auxiliary", time.monotonic())
        self.transport.cancel_owner("drop")
        self.assertEqual(self.transport._pending, {})

    def test_a_lane_whose_reply_vanished_is_replaced_not_reported_busy(self):
        # Touching the surface of a deleted QNetworkReply raises; the lane
        # must then be reclaimable instead of blocking every future poll.
        self.transport._pending["o::c"] = self.module._PendingRequest(
            _Vanished(), 1, "GET", "auxiliary", time.monotonic())
        self.assertTrue(self.transport.send_json("o", "c", "GET", "/ok", self.callback()))
        self.assertTrue(self.poll(lambda: self.calls), "the lane stayed wedged")

    def test_cancelling_an_owner_leaves_other_owners_alone(self):
        self.assertTrue(self.transport.send_json("keep", "c", "GET", "/gate", self.callback()))
        self.assertTrue(self.transport.send_json("drop", "c", "GET", "/gate", self.callback()))
        self.transport.cancel_owner("drop")
        self.assertEqual(sorted(self.transport._pending), ["keep::c"])

    def test_cancel_all_retires_every_lane(self):
        self.assertTrue(self.transport.send_json("a", "c", "GET", "/gate", self.callback()))
        self.assertTrue(self.transport.send_json("b", "c", "GET", "/gate", self.callback()))
        self.transport.cancel_all()
        self.assertEqual(self.transport._pending, {})

    def test_a_cancelled_reply_is_teardown_noise_not_printer_trouble(self):
        # A reply QNAM cancels on its own still reaches the consumer, but
        # teardown must not read as printer trouble in the log.
        self.assertTrue(self.transport.send_json("o", "c", "GET", "/gate", self.callback()))
        self.transport._pending["o::c"].reply.abort()
        self.assertTrue(self.poll(lambda: self.calls), "the consumer was never told")
        self.assertIsNotNone(self.calls[0][1])
        self.assertTrue(self.logged("d", "canceled"), "a cancellation was logged as a failure")
        self.assertEqual(self.logged("w", "failed:"), [])

    def test_close_retires_every_lane(self):
        self.assertTrue(self.transport.send_json("o", "c", "GET", "/gate", self.callback()))
        self.transport.close()
        self.assertEqual(self.transport._pending, {})


class TransportStaleFinishTests(TransportCase):
    """Finishes no live socket can stage: a reply whose object or
    generation no longer matches the lane, and replies whose C++ object is
    already gone when the transport touches it."""

    def _pending(self, reply):
        entry = self.module._PendingRequest(reply, 1, "GET", "auxiliary", time.monotonic())
        self.transport._pending[self.transport._key("o", "c")] = entry
        return entry

    def test_a_finish_from_a_previous_generation_is_dropped(self):
        reply = _ScriptedReply()
        self._pending(reply)
        self.transport._generation += 1
        self.transport._finish_json(
            self.transport._key("o", "c"), reply, self.callback(), self.transport._generation - 1)
        self.assertEqual(self.calls, [], "a stale generation reached the consumer")
        self.assertTrue(reply.deleted)

    def test_a_finish_for_a_superseded_reply_is_dropped(self):
        self._pending(_ScriptedReply())
        late = _ScriptedReply()
        self.transport._finish_json(
            self.transport._key("o", "c"), late, self.callback(), self.transport._generation)
        self.assertEqual(self.calls, [], "a superseded reply reached the consumer")
        self.assertTrue(late.deleted)

    def test_a_dropped_reply_that_cannot_be_deleted_is_still_dropped(self):
        # A reply whose C++ object is already gone raises on contact; the
        # drop must stay silent all the same.
        reply = _ScriptedReply(delete_raises=True)
        self._pending(reply)
        self.transport._generation += 1
        self.transport._finish_json(
            self.transport._key("o", "c"), reply, self.callback(), self.transport._generation - 1)
        self.assertEqual(self.calls, [])

    def test_a_reply_that_cannot_be_deleted_still_delivers_its_verdict(self):
        # The consumer's verdict is what a lane exists for; cleanup
        # failing afterwards must not swallow it.
        reply = _ScriptedReply(b'{"result": 1}', delete_raises=True)
        self._pending(reply)
        self.transport._finish_json(
            self.transport._key("o", "c"), reply, self.callback(), self.transport._generation)
        self.assertEqual(self.calls, [({"result": 1}, None)])
        self.assertNotIn("o::c", self.transport._pending)


class TransportMetricsTests(TransportCase):
    """The diagnostics surface the settings dialog reads."""

    def test_metrics_count_what_the_transport_did(self):
        self.deliver(path="/ok")
        self.deliver(path="/forbidden")
        metrics = self.transport.metrics["auxiliary"]
        self.assertEqual(metrics["started"], 2)
        self.assertEqual(metrics["completed"], 2)
        self.assertEqual(metrics["failed"], 1)
        self.assertGreater(metrics["average_elapsed_ms"], 0.0)

    def test_an_idle_transport_reports_nothing_and_averages_zero(self):
        self.assertEqual(self.transport.metrics, {})
        self.assertEqual(self.module.TransportMetrics().average_elapsed_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
