"""A scripted RFC 6455 loopback server for the socket-owner tests.

Stdlib only, thread-based like ``PipeSafeHandler``. Records the
handshake headers and every inbound/outbound frame, and plays a script
of actions: replies to JSON-RPC requests, pushed notifications, pings
and closes. Pushes ride ahead of replies — a queued notify is sent
before the next request is answered.
"""
from __future__ import annotations

import json
import socket
import socketserver
import struct
import threading
from typing import Any, Dict, List, Optional, Tuple

from plugins.SocketFraming import accept_value


class WSHandler(socketserver.BaseRequestHandler):
    server: "WSServer"

    def _recv_exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self.request.recv(count - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def _read_frame(self) -> Optional[Tuple[int, bytes]]:
        """Server side: client frames are masked."""
        header = self._recv_exact(2)
        if len(header) < 2:
            return None
        first, second = header
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4)
        payload = self._recv_exact(length)
        return opcode, bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            head += bytes([length])
        elif length < 65536:
            head += bytes([126]) + struct.pack(">H", length)
        else:
            head += bytes([127]) + struct.pack(">Q", length)
        try:
            self.request.sendall(head + payload)
        except (BrokenPipeError, ConnectionResetError):
            return
        self.server.record_outbound((opcode, payload))

    def _drain_pushes(self) -> None:
        while True:
            action = self.server.pop_push()
            if action is None:
                return
            kind = action[0]
            if kind == "notify":
                self._send_frame(0x1, json.dumps({
                    "jsonrpc": "2.0", "method": action[1], "params": action[2]}).encode())
            elif kind == "ping":
                self._send_frame(0x9, action[1])
            elif kind == "close":
                self._send_frame(0x8, struct.pack(">H", action[1]) + action[2])

    def handle(self) -> None:
        request = b""
        while b"\r\n\r\n" not in request and len(request) < 65536:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            request += chunk
        head, _ = request.split(b"\r\n\r\n", 1)
        headers: Dict[str, str] = {}
        key = ""
        for line in head.decode("utf-8", "replace").splitlines()[1:]:
            if ":" in line:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
                if name.strip().lower() == "sec-websocket-key":
                    key = value.strip()
        self.server.record_handshake(headers)
        # The 101 must be the FIRST bytes on the wire.
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_value(key)
        ).encode()
        try:
            self.request.sendall(response)
        except (BrokenPipeError, ConnectionResetError):
            return
        # A short read timeout lets queued pushes drain without waiting
        # for the client to send something first.
        self.request.settimeout(0.05)
        while True:
            self._drain_pushes()
            try:
                frame = self._read_frame()
            except socket.timeout:
                continue
            if frame is None:
                return
            opcode, payload = frame
            if opcode == 0x8:
                (code,) = struct.unpack(">H", payload[:2]) if len(payload) >= 2 else (1000,)
                self.server.record_inbound(("close", code, payload[2:]))
                self._send_frame(0x8, payload[:2])
                return
            if opcode == 0x9:
                self.server.record_inbound(("ping", payload))
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                self.server.record_inbound(("pong", payload))
                continue
            self.server.record_inbound(("text", payload))
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(message, dict) or "id" not in message:
                continue
            self.server.record_request(message)
            self._drain_pushes()
            reply = self.server.pop_reply()
            if reply is None:
                self._send_frame(0x1, json.dumps({
                    "jsonrpc": "2.0", "result": {}, "id": message["id"]}).encode())
            else:
                body = dict(reply[1])
                body["id"] = message["id"]
                body.setdefault("jsonrpc", "2.0")
                self._send_frame(0x1, json.dumps(body).encode())


class WSServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, WSHandler)
        self.lock = threading.Lock()
        self.handshakes: List[Dict[str, str]] = []
        self.inbound: List[Tuple] = []
        self.outbound: List[Tuple] = []
        self.requests: List[Dict[str, Any]] = []
        self.script: List[Tuple] = []

    def record_handshake(self, headers) -> None:
        with self.lock:
            self.handshakes.append(headers)

    def record_inbound(self, entry) -> None:
        with self.lock:
            self.inbound.append(entry)

    def record_outbound(self, entry) -> None:
        with self.lock:
            self.outbound.append(entry)

    def record_request(self, message) -> None:
        with self.lock:
            self.requests.append(message)

    def queue(self, *actions) -> None:
        with self.lock:
            self.script.extend(actions)

    def pop_push(self) -> Optional[Tuple]:
        with self.lock:
            if self.script and self.script[0][0] != "reply":
                return self.script.pop(0)
        return None

    def pop_reply(self) -> Optional[Tuple]:
        with self.lock:
            if self.script and self.script[0][0] == "reply":
                return self.script.pop(0)
        return None

    def wait_for(self, predicate, timeout: float = 5.0) -> bool:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if predicate(self):
                    return True
            time.sleep(0.01)
        return False
