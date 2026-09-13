"""The Qt owner of one Moonraker websocket connection.

The wire counterpart of ``MoonrakerTransport``: a peer capability on the
session, never reachable as ``client.transport``. Owns the socket
lifecycle, the handshake, the subscription, the per-class raw-fragment
accumulators, the keepalive round-trip and its own generation. Knows
nothing about status policy, timers beyond the keepalive, or the UI.

Discipline carried from the HTTP transport:
- every signal callback validates (socket, client, session) generations;
- teardown bumps the socket generation BEFORE closing, then drains the
  pending-request map and the accumulators;
- sslErrors are never ignored (wss parity with QNAM's https defaults);
- nothing here logs credentials — handshake bytes are never logged.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QSslSocket, QTcpSocket, QAbstractSocket

from .SocketFraming import (
    MAX_HANDSHAKE_HEADER_BYTES,
    FrameState,
    FramingError,
    build_client_key,
    build_handshake,
    encode_close_frame,
    encode_pong,
    encode_text_frame,
    parse_frames,
    verify_handshake,
)

# The keepalive round-trip cadence (Moonraker itself pings every 10 s;
# ours is the authenticated application-level check the liveness ruling
# requires — an idle printer legitimately pushes nothing).
KEEPALIVE_INTERVAL_MS = 10_000
KEEPALIVE_DEADLINE_MS = 15_000


class MoonrakerSocket(QObject):
    """One websocket feed connection; see the module docstring."""

    upgraded = pyqtSignal()  # 101 received and validated
    syncSnapshot = pyqtSignal(object, float)  # subscribe reply: full status, issue stamp
    subscribeRefused = pyqtSignal(object)  # the subscribe reply's error dict (F3)
    klippyReady = pyqtSignal()
    klippyLost = pyqtSignal(str)  # notify_klippy_shutdown / _disconnected
    failed = pyqtSignal(str)  # terminal failures with a reason, never silent

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        keepalive_interval_ms: int = KEEPALIVE_INTERVAL_MS,
        keepalive_deadline_ms: int = KEEPALIVE_DEADLINE_MS,
    ) -> None:
        super().__init__(parent)
        self._socket: Optional[QTcpSocket] = None
        self._generation = 0
        self._buffer = b""
        self._frame_state = FrameState()
        self._core: Dict[str, Any] = {}
        self._aux: Dict[str, Any] = {}
        self._core_stamp = 0.0
        self._aux_stamp = 0.0
        self._core_names: set = set()
        self._aux_names: set = set()
        self._request_serial = 0
        self._pending: Dict[int, Tuple[Callable, int]] = {}
        self._last_auth_reply_at = 0.0
        self._key = ""
        self._upgraded = False
        self._keepalive_interval_ms = max(10, int(keepalive_interval_ms))
        self._keepalive_deadline_ms = max(10, int(keepalive_deadline_ms))
        self._keepalive_timer = QTimer(self)
        self._keepalive_timer.setInterval(self._keepalive_interval_ms)
        self._keepalive_timer.timeout.connect(self._send_keepalive)

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def last_auth_reply_at(self) -> float:
        return self._last_auth_reply_at

    @property
    def is_upgraded(self) -> bool:
        return self._upgraded

    @property
    def subscribed_names(self) -> List[str]:
        return sorted(self._core_names | self._aux_names)

    def start(self, url: str, api_key: str, core_names: set, aux_names: set) -> None:
        """Connect (ws or wss by scheme), upgrade, subscribe and feed."""
        self.stop()
        parsed = QUrl(url)
        use_tls = parsed.scheme().lower() == "wss"
        self._core_names = set(core_names)
        self._aux_names = set(aux_names)
        socket = QSslSocket(self) if use_tls else QTcpSocket(self)
        self._socket = socket
        generation = self._generation

        def stale() -> bool:
            return generation != self._generation

        def on_ready() -> None:
            if stale() or self._socket is not socket:
                return
            host = parsed.host() or ""
            if parsed.port(80 if not use_tls else 443) not in (80, 443):
                host = f"{host}:{parsed.port(80)}"
            self._key = build_client_key()
            request = build_handshake(host, self._key, api_key)
            socket.write(request)

        def on_error(error: QAbstractSocket.SocketError) -> None:
            if stale():
                return
            self.failed.emit(socket.errorString() or f"socket error {int(error)}")

        def on_ssl_errors(errors: list) -> None:
            # Never ignoreSslErrors: wss must fail exactly as https does
            # today, with the certificate text surfaced.
            if stale():
                return
            text = "; ".join(str(error.errorString()) for error in errors) or "TLS verification failed"
            self.failed.emit(text)

        def on_data() -> None:
            if stale() or self._socket is not socket:
                return
            self._buffer += bytes(socket.readAll())
            if not self._upgraded:
                self._process_handshake()
                return
            self._process_buffer()

        socket.errorOccurred.connect(on_error)
        socket.readyRead.connect(on_data)
        if isinstance(socket, QSslSocket):
            # TLS: the upgrade request may only be written once the
            # encrypted channel exists (a plaintext write would hit a
            # TLS port and die as a remote close — the live proxy run).
            socket.sslErrors.connect(on_ssl_errors)
            socket.encrypted.connect(on_ready)
            socket.connectToHostEncrypted(parsed.host(), parsed.port(443))
        else:
            socket.connected.connect(on_ready)
            socket.connectToHost(parsed.host(), parsed.port(80))

    def stop(self) -> None:
        self._generation += 1
        self._keepalive_timer.stop()
        socket = self._socket
        self._socket = None
        if socket is not None:
            try:
                if socket.state() != QAbstractSocket.SocketState.UnconnectedState:
                    socket.write(encode_close_frame(1000))
                    socket.flush()
                    socket.disconnectFromHost()
            except Exception:
                pass
            try:
                socket.deleteLater()
            except Exception:
                pass
        self._pending.clear()
        self._buffer = b""
        self._frame_state = FrameState()
        self._core.clear()
        self._aux.clear()
        self._core_stamp = 0.0
        self._aux_stamp = 0.0
        self._core_names = set()
        self._aux_names = set()
        self._last_auth_reply_at = 0.0
        self._key = ""
        self._upgraded = False

    def request(self, method: str, params: Dict[str, Any], callback: Callable[[Dict[str, Any]], None]) -> int:
        """One JSON-RPC over the socket, exact-id correlated (S8).

        Before the upgrade a buffered write could land ahead of the
        handshake on the wire — requests only exist on a live feed."""
        socket = self._socket
        if socket is None or not self._upgraded:
            return 0
        self._request_serial += 1
        request_id = self._request_serial
        self._pending[request_id] = (callback, self._generation)
        message = {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
        try:
            socket.write(encode_text_frame(json.dumps(message).encode("utf-8")))
        except (FramingError, OSError):
            self._pending.pop(request_id, None)
            return 0
        return request_id

    def subscribe(self, objects: Dict[str, Any], *, aux_names=None) -> None:
        """The merged subscription set, one call per connection (a second
        subscribe replaces the first wholesale — F4). The aux subset
        travels with it: fragment routing AND the sync seeding both
        depend on knowing which names are auxiliary, and the wanted set
        can grow mid-print (a device switched on later)."""

        if aux_names is not None:
            self._aux_names = set(aux_names)

        def on_reply(reply: Dict[str, Any]) -> None:
            self._last_auth_reply_at = time.monotonic()
            error = reply.get("error")
            if isinstance(error, dict):
                # A structured refusal is a capability failure, not a
                # link failure — never rendered as "Invalid params" (F3).
                self.subscribeRefused.emit(error)
                return
            result = reply.get("result")
            status = result.get("status") if isinstance(result, dict) else None
            if isinstance(status, dict):
                # The subscribe response carries the FULL current state;
                # Moonraker then pushes only CHANGES. Seed the aux
                # accumulator from the sync so objects that never change
                # (a steady temperature) still reach the Monitor on the
                # next drain (the author's live report).
                for name in self._aux_names:
                    if name in status:
                        self._aux[name] = status[name]
                if self._aux:
                    self._aux_stamp = self._issue_stamp
                self.syncSnapshot.emit(status, self._issue_stamp)

        self._issue_stamp = time.monotonic()
        self.request("printer.objects.subscribe", {"objects": objects}, on_reply)

    def drain_core(self) -> Tuple[Optional[Dict[str, Any]], float]:
        patch, stamp = None, self._core_stamp
        if self._core:
            patch = self._core
            self._core = {}
            self._core_stamp = 0.0
        return patch, stamp

    def drain_aux(self) -> Tuple[Optional[Dict[str, Any]], float]:
        patch, stamp = None, self._aux_stamp
        if self._aux:
            patch = self._aux
            self._aux = {}
            self._aux_stamp = 0.0
        return patch, stamp

    def _process_handshake(self) -> None:
        """The 101 stage: validate the upgrade before any frame parsing,
        with the caller-bound header cap enforced here (S7)."""
        if b"\r\n\r\n" not in self._buffer:
            if len(self._buffer) > MAX_HANDSHAKE_HEADER_BYTES:
                self.failed.emit("handshake response exceeds the header cap")
                self.stop()
            return
        head, rest = self._buffer.split(b"\r\n\r\n", 1)
        ok, reason = verify_handshake(head + b"\r\n\r\n", self._key)
        if not ok:
            if "HTTP/1.1 401" in reason or "HTTP/1.0 401" in reason:
                reason = "the API key was rejected (HTTP 401)"
            self.failed.emit(reason)
            self.stop()
            return
        self._upgraded = True
        self._buffer = rest
        self._keepalive_timer.start()
        self.upgraded.emit()
        if rest:
            self._process_buffer()

    def _process_buffer(self) -> None:
        try:
            events, remainder, self._frame_state = parse_frames(self._buffer, self._frame_state)
        except FramingError as exc:
            self.failed.emit(str(exc))
            self.stop()
            return
        self._buffer = remainder
        for event in events:
            kind = event[0]
            if kind == "ping":
                self._write_control(encode_pong(event[1]))
            elif kind == "close":
                self._write_control(encode_close_frame(1000))
                self.stop()
                return
            elif kind == "error":
                self.failed.emit(event[1])
                self.stop()
                return
            elif kind == "message":
                self._on_message(event[1])

    def _on_message(self, payload: bytes) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.failed.emit("undecodable JSON-RPC frame")
            self.stop()
            return
        if not isinstance(message, dict):
            self.failed.emit("non-object JSON-RPC frame")
            self.stop()
            return
        if "id" in message and "method" not in message:
            # A reply: exact-id correlation only (S8). Unknown ids are
            # dropped, never used to satisfy another request.
            entry = self._pending.pop(message.get("id"), None)
            if entry is not None:
                callback, generation = entry
                if generation == self._generation:
                    self._last_auth_reply_at = time.monotonic()
                    callback(message)
            return
        method = message.get("method")
        if method == "notify_status_update":
            params = message.get("params") or []
            patch = params[0] if params else {}
            if isinstance(patch, dict):
                self._apply_patch(patch)
            return
        if method == "notify_klippy_ready":
            self.klippyReady.emit()
            return
        if method in ("notify_klippy_shutdown", "notify_klippy_disconnected"):
            self.klippyLost.emit(str(method))
            return
        # notify_gcode_response and anything else unsubscribed: ignore.

    def _apply_patch(self, patch: Dict[str, Any]) -> None:
        """Raw-fragment accumulation: plain dict update, no deepcopy and
        no consumer work at push rate (F6). One object may route to BOTH
        classes (bed_mesh: core geometry + aux mesh profiles — A8)."""
        stamp = time.monotonic()
        for name, value in patch.items():
            if name in self._core_names:
                self._core[name] = value
                self._core_stamp = stamp
            if name in self._aux_names:
                self._aux[name] = value
                self._aux_stamp = stamp

    def _send_keepalive(self) -> None:
        if self._socket is None:
            return
        if self._last_auth_reply_at and time.monotonic() - self._last_auth_reply_at > self._keepalive_deadline_ms / 1000.0:
            self.failed.emit("keepalive reply deadline exceeded")
            self.stop()
            return
        self.request("server.info", {}, lambda reply: None)

    def _write_control(self, frame: bytes) -> None:
        if self._socket is None:
            return
        try:
            self._socket.write(frame)
        except OSError:
            pass
