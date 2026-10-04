"""Cancellation aborts blocked header and chunk-header receipt, not just body reads."""
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mpf.detection.DetectionDownloadTransport import TransferAbort, _Connection, _Response, AbortHTTPSHandler, transfer_abort


class TransferAbortTests(unittest.TestCase):
    def test_body_registers_live_socket_and_abort_tolerates_an_already_closed_socket(self):
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        connection = Mock()
        connection.shutdown.side_effect = OSError("already closed")
        guard.body(SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=connection))))
        self.assertGreater(guard.deadline, time.monotonic() + 1700)
        guard._abort()
        connection.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    def test_connection_closes_the_raw_socket_if_duplicate_allocation_fails(self):
        raw = Mock()
        raw.dup.side_effect = OSError("descriptor limit")
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        with patch("socket.create_connection", return_value=raw):
            connection = _Connection("model.example", guard=guard)
        with self.assertRaisesRegex(OSError, "descriptor limit"):
            connection._create_connection(("model.example", 443))
        raw.close.assert_called_once()

    def test_handler_scopes_guard_and_registers_the_completed_tls_socket(self):
        handler = AbortHTTPSHandler()
        request = Mock()
        with patch("urllib.request.HTTPSHandler.https_open", return_value="normal"):
            self.assertEqual(handler.https_open(request), "normal")
        with transfer_abort(None) as outer:
            with transfer_abort(None) as inner, patch.object(handler, "do_open", return_value="guarded") as opening:
                self.assertEqual(handler.https_open(request), "guarded")
                factory = opening.call_args.args[0]
                connection = factory("model.example")
                connection.sock = Mock()
                connection._context = Mock()
                connection._context.wrap_socket.return_value = connection.sock
                with patch("http.client.HTTPConnection.connect"):
                    connection.connect()
                self.assertIn(connection.sock, inner._sockets)
            with patch.object(handler, "do_open", return_value="outer") as opening:
                handler.https_open(request)
                self.assertIs(opening.call_args.args[0]("model.example")._guard, outer)

    def test_cancel_interrupts_a_trickling_incomplete_header(self):
        self._blocked_receipt(b"HTTP/1.1 200 OK\r\nX-Long: abc", header=True)

    def test_cancel_interrupts_chunk_size_framing(self):
        self._blocked_receipt(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n123", header=False)

    def test_slow_healthy_headers_and_body_resume_after_poll_timeouts(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        client.settimeout(2)
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        response = _Response(client, guard=guard)
        self.addCleanup(response.close)
        server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Slow:")
        finished = threading.Event()
        result = []
        def read():
            try:
                response.begin()
                result.append(response.read())
            except Exception as exc:
                result.append(exc)
            finally:
                finished.set()
        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        self.assertFalse(finished.wait(.25))
        server.sendall(b" yes\r\n\r\na")
        self.assertFalse(finished.wait(.25))
        server.sendall(b"b")
        self.assertTrue(finished.wait(1))
        worker.join(1)
        self.assertEqual(result, [b"ab"])
        self.assertEqual(client.gettimeout(), 2)

    def test_socket_file_reference_survives_connection_close(self):
        client, server = socket.socketpair()
        self.addCleanup(server.close)
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        response = _Response(client, guard=guard)
        self.addCleanup(response.close)
        client.close()
        server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        response.begin()
        self.assertEqual(response.read(), b"ok")
        self.assertEqual(client.fileno(), -1)

    def test_response_factory_accepts_http_client_positional_debuglevel(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        connection = _Connection("model.example", guard=guard)
        response = connection.response_class(client, 1, method="GET")
        self.addCleanup(response.close)
        self.assertEqual(response.debuglevel, 1)
        self.assertEqual(response._method, "GET")

    def test_deadline_interrupts_blocked_headers_without_shutdown(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        response = _Response(client, guard=guard)
        self.addCleanup(response.close)
        # The polling reader must work even when Winsock shutdown does
        # not wake an in-progress receive operation.
        guard._abort = Mock()
        guard.deadline = time.monotonic() + .15
        server.sendall(b"HTTP/1.1 200 OK\r\nX-Slow:")
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            response.begin()
        self.assertLess(time.monotonic() - started, .5)

    def test_tls_handshake_retries_poll_timeout_and_restores_socket_timeout(self):
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        connection = _Connection("model.example", guard=guard)
        connection._context = Mock()
        tls = connection._context.wrap_socket.return_value
        tls.gettimeout.return_value = 3
        tls.do_handshake.side_effect = [socket.timeout(), None]
        with patch("http.client.HTTPConnection.connect"):
            connection.connect()
        self.assertEqual(tls.do_handshake.call_count, 2)
        tls.settimeout.assert_called_with(3)
        self.assertIn(tls, guard._sockets)
        connection._context.wrap_socket.assert_called_once_with(
            unittest.mock.ANY, server_hostname="model.example", do_handshake_on_connect=False)

    def test_tls_handshake_cancellation_does_not_depend_on_socket_shutdown(self):
        cancel = threading.Event()
        guard = TransferAbort(cancel)
        self.addCleanup(guard.close)
        connection = _Connection("proxy.example", guard=guard)
        connection.set_tunnel("model.example", 443)
        connection._context = Mock()
        tls = connection._context.wrap_socket.return_value
        tls.gettimeout.return_value = 3
        def stalled_handshake():
            cancel.set()
            raise socket.timeout()
        tls.do_handshake.side_effect = stalled_handshake
        with patch("http.client.HTTPConnection.connect"), self.assertRaisesRegex(OSError, "cancelled"):
            connection.connect()
        tls.settimeout.assert_called_with(3)
        self.assertEqual(tls.do_handshake.call_count, 1)
        connection._context.wrap_socket.assert_called_once_with(
            unittest.mock.ANY, server_hostname="model.example", do_handshake_on_connect=False)

    def test_proxy_connect_headers_are_abortable_before_tls_wrapping(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(1)
        cancel = threading.Event()
        guard = TransferAbort(cancel)
        self.addCleanup(guard.close)
        connection = _Connection("127.0.0.1", port=listener.getsockname()[1], guard=guard)
        connection.set_tunnel("model.example", 443)
        finished = threading.Event()
        def connect():
            try:
                connection.connect()
            except Exception:
                pass
            finally:
                connection.close()
                finished.set()
        worker = threading.Thread(target=connect, daemon=True)
        worker.start()
        server, _ = listener.accept()
        self.addCleanup(server.close)
        server.settimeout(1)
        self.assertIn(b"CONNECT", server.recv(4096))
        server.sendall(b"HTTP/1.1 200 Connection established\r\nX-Long: abc")
        self.assertFalse(finished.wait(.1))
        cancel.set()
        self.assertTrue(finished.wait(.5), "proxy CONNECT must be interrupted by cancellation")
        worker.join(.5)

    def test_socket_registered_after_deadline_is_immediately_aborted(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        guard = TransferAbort(None)
        self.addCleanup(guard.close)
        guard.expired = True
        guard.register_socket(client)
        server.settimeout(.2)
        self.assertEqual(server.recv(1), b"")

    def _blocked_receipt(self, prefix, *, header):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        cancel = threading.Event()
        guard = TransferAbort(cancel)
        self.addCleanup(guard.close)
        guard.register_socket(client)
        response = _Response(client, guard=guard)
        server.sendall(prefix)
        finished = threading.Event()
        errors = []
        def read():
            try:
                response.begin()
                if not header:
                    response.read1(8192)
            except Exception as exc:
                errors.append(exc)
            finally:
                response.close()
                finished.set()
        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        self.assertFalse(finished.wait(.1), "fixture must be blocked in receipt")
        started = time.monotonic()
        cancel.set()
        self.assertTrue(finished.wait(.5), "cancellation must shut down the live socket")
        self.assertLess(time.monotonic() - started, .5)
        worker.join(.5)
        self.assertTrue(errors or header)
