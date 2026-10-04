"""Abort HTTPS receipt actively, including headers and chunk framing."""

from contextlib import contextmanager
from http.client import HTTPSConnection
import socket
import threading
import time
from urllib.request import HTTPSHandler

_local = threading.local()


class TransferAbort:
    def __init__(self, cancel):
        self.cancel = cancel
        self.deadline = time.monotonic() + 15.0
        self.expired = False
        self._sockets = []
        self._owned_sockets = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._worker = threading.Thread(target=self._watch, name="mpf-detection-transfer", daemon=True)
        self._worker.start()

    def register_socket(self, connection, *, owned=False):
        with self._lock:
            self._sockets.append(connection)
            if owned:
                self._owned_sockets.append(connection)
        if self.expired or (self.cancel is not None and self.cancel.is_set()):
            self._abort()

    def body(self, response):
        # Test transports and already-open responses also get active abort.
        raw = getattr(getattr(response, "fp", None), "raw", None)
        connection = getattr(raw, "_sock", None)
        if connection is not None:
            self.register_socket(connection)
        self.deadline = time.monotonic() + 1800.0

    def _abort(self):
        with self._lock:
            sockets = tuple(self._sockets)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _watch(self):
        while not self._done.wait(.05):
            if (self.cancel is not None and self.cancel.is_set()) or time.monotonic() > self.deadline:
                self.expired = time.monotonic() > self.deadline
                self._abort()
                return

    def close(self):
        self._done.set()
        self._worker.join(timeout=.1)
        for connection in self._owned_sockets:
            connection.close()


class _Connection(HTTPSConnection):
    def __init__(self, host, *, guard, **kwargs):
        super().__init__(host, **kwargs)
        self._guard = guard
        create_connection = self._create_connection
        def guarded_connection(*args, **options):
            connection = create_connection(*args, **options)
            # Register before HTTPConnection reads proxy CONNECT headers.
            # The duplicate survives TLS wrapping, which detaches the raw fd.
            try:
                duplicate = connection.dup()
            except OSError:
                connection.close()
                raise
            guard.register_socket(duplicate, owned=True)
            return connection
        self._create_connection = guarded_connection

    def connect(self):
        super().connect()
        self._guard.register_socket(self.sock)


class AbortHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        guard = getattr(_local, "guard", None)
        if guard is None:
            return super().https_open(request)
        def connection(host, **kwargs):
            return _Connection(host, guard=guard, **kwargs)
        options = {"context": self._context}
        # Python 3.12 removed check_hostname from HTTPSConnection and its
        # handler; older Cura runtimes still expose the explicit override.
        if hasattr(self, "_check_hostname"):
            options["check_hostname"] = self._check_hostname
        return self.do_open(connection, request, **options)


@contextmanager
def transfer_abort(cancel):
    previous = getattr(_local, "guard", None)
    guard = TransferAbort(cancel)
    _local.guard = guard
    try:
        yield guard
    finally:
        _local.guard = previous
        guard.close()
