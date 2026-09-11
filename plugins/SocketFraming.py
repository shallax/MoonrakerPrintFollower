from __future__ import annotations

"""Pure RFC 6455 client framing — no Qt, no sockets, no policy.

Client role only: outbound frames are masked, inbound frames must be
unmasked (§5.1). Everything here is a pure function over bytes and a
small reassembly state so the full validity table is testable without
a socket or an event loop (the security round's S5). Violations raise
or emit "error" events with a reason — a tolerant parser would turn a
malformed stream into wrong printer state.
"""

import base64
import codecs
from dataclasses import dataclass, field
import hashlib
import secrets
import struct
from typing import List, Optional, Tuple

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Assembled-message cap, cumulative across fragments (a fragmentation
# bomb is a legal-looking sequence of small frames). Real status
# payloads are kilobytes; exceeded -> close 1009.
MAX_MESSAGE_BYTES = 1_048_576

# The upgrade-response header stream has no HTTP parser to bound it (S7).
MAX_HANDSHAKE_HEADER_BYTES = 64 * 1024

_CLOSE_ABNORMAL = 1006
_CLOSE_NO_STATUS = 1005
_CLOSE_TLS = 1015
_CLOSE_OFF_WIRE = {_CLOSE_NO_STATUS, _CLOSE_ABNORMAL, _CLOSE_TLS}


class FramingError(Exception):
    """A protocol violation — the connection fails closed with the reason."""


@dataclass
class FrameState:
    """Reassembly state carried across parse_frames calls."""

    fragmented_opcode: Optional[int] = None  # 1=text, 2=binary while mid-message
    message: bytearray = field(default_factory=bytearray)
    decoder: Optional[codecs.IncrementalDecoder] = None  # None for binary messages
    max_bytes: int = MAX_MESSAGE_BYTES  # injectable so the cap is table-testable


def build_client_key() -> str:
    """§4.1: 16 CSPRNG bytes, base64. Never the ``random`` module (§5.3)."""
    return base64.b64encode(secrets.token_bytes(16)).decode("ascii")


def accept_value(key: str) -> str:
    """The Sec-WebSocket-Accept value a 101 response must carry for our key."""
    return base64.b64encode(
        hashlib.sha1((str(key) + GUID).encode("utf-8")).digest()
    ).decode("ascii")


def _clean_header_value(value: str) -> str:
    # Values interpolated into a hand-rolled request have no HTTP parser
    # to police them: CR/LF/NUL would inject headers (S10).
    return str(value).replace("\r", "").replace("\n", "").replace("\0", "")


def build_handshake(host_header: str, key: str, api_key: str = "") -> bytes:
    """The client upgrade request. The key rides the handshake header,
    never a URL component. No Origin header — Moonraker's check_cors
    fails closed on unconfigured cors_domains when one is present (M6)."""
    lines = [
        "GET /websocket HTTP/1.1",
        "Host: %s" % _clean_header_value(host_header),
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: %s" % _clean_header_value(key),
        "Sec-WebSocket-Version: 13",
    ]
    if api_key:
        lines.append("X-Api-Key: %s" % _clean_header_value(api_key))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def _header_tokens(headers: dict, name: str) -> List[str]:
    # Header field values are case-insensitive by RFC 7230 3.2.
    return [token.strip().lower() for token in headers.get(name, "").split(",") if token.strip()]


def verify_handshake(head: bytes, key: str) -> Tuple[bool, str]:
    """True when the 101 response is valid and bound to our key.

    Checks beyond the accept value (S7): the upgrade token pair, and no
    extension or subprotocol the client did not offer. The caller bounds
    the buffer with MAX_HANDSHAKE_HEADER_BYTES before this runs.
    """
    text = head.decode("utf-8", errors="replace")
    # Informational 1xx blocks may precede the 101 (4.1): validate each
    # interim block and judge only the final one.
    blocks = text.split("\r\n\r\n")
    if blocks and blocks[-1] == "":
        blocks = blocks[:-1]
    if not blocks:
        return False, "empty handshake response"
    for block in blocks[:-1]:
        block_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not block_lines:
            continue
        interim = block_lines[0].split(" ", 2)
        if (
            len(interim) < 2
            or interim[0] != "HTTP/1.1"
            or not interim[1].isdigit()
            or not 100 <= int(interim[1]) < 200
            or interim[1] == "101"
        ):
            return False, "unexpected interim handshake response: %s" % block_lines[0][:80]
    lines = [line.strip() for line in blocks[-1].splitlines() if line.strip()]
    if not lines:
        return False, "empty handshake response"
    status = lines[0].split(" ", 2)
    if len(status) < 2 or status[0] != "HTTP/1.1" or status[1] != "101":
        return False, "handshake refused: %s" % lines[0][:80]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    if "websocket" not in _header_tokens(headers, "upgrade"):
        return False, "handshake missing Upgrade: websocket"
    if "upgrade" not in _header_tokens(headers, "connection"):
        return False, "handshake missing Connection: Upgrade"
    if "sec-websocket-extensions" in headers or "sec-websocket-protocol" in headers:
        return False, "handshake offered an unrequested extension or subprotocol"
    if headers.get("sec-websocket-accept", "") != accept_value(key):
        return False, "Sec-WebSocket-Accept mismatch"
    return True, ""


def _encode_frame(fin: bool, opcode: int, payload: bytes) -> bytes:
    """A masked client frame, extended lengths included — the live probe
    died on the 16-bit form once, so it is table-tested here."""
    if opcode >= 0x8 and len(payload) > 125:
        raise FramingError("control frame payload exceeds 125 bytes")
    mask = secrets.token_bytes(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    if length < 126:
        return bytes([first, 0x80 | length]) + mask + masked
    if length < 65536:
        return bytes([first, 0x80 | 126]) + struct.pack(">H", length) + mask + masked
    return bytes([first, 0x80 | 127]) + struct.pack(">Q", length) + mask + masked


def encode_text_frame(payload: bytes) -> bytes:
    return _encode_frame(True, 0x1, payload)


def encode_binary_frame(payload: bytes) -> bytes:
    return _encode_frame(True, 0x2, payload)


def encode_close_frame(code: int, reason: bytes = b"") -> bytes:
    if code in _CLOSE_OFF_WIRE or not 1000 <= code <= 4999:
        raise FramingError("invalid close code %d" % code)
    return _encode_frame(True, 0x8, struct.pack(">H", code) + reason)


def encode_ping(payload: bytes = b"") -> bytes:
    return _encode_frame(True, 0x9, payload)


def encode_pong(payload: bytes) -> bytes:
    return _encode_frame(True, 0xA, payload)


def _complete_text(payload: bytes, decoder: Optional[codecs.IncrementalDecoder]) -> bytes:
    """Strict UTF-8 (§8.1). Incremental across fragments so a codepoint
    split by the frame boundary is legal; invalid sequences are not —
    replacement therapy would corrupt printer state, not fix it."""
    if decoder is not None:
        return decoder.decode(payload, final=True).encode("utf-8")
    # Binary messages pass through untouched — no UTF-8 judgement.
    return payload


def parse_frames(buffer: bytes, state: FrameState) -> Tuple[List[tuple], bytes, FrameState]:
    """Consume frames from ``buffer``; returns (events, remainder, state).

    Events: ("message", bytes) — one assembled data message;
    ("ping", bytes); ("pong", bytes); ("close", int, str);
    ("error", str) — a protocol violation, connection must fail.
    """
    events: List[tuple] = []
    buf = buffer
    while True:
        if len(buf) < 2:
            return events, buf, state
        first, second = buf[0], buf[1]
        fin = bool(first & 0x80)
        rsv = first & 0x70
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        index = 2
        if rsv:
            events.append(("error", "reserved bits set"))
            return events, buf, state
        if masked:
            events.append(("error", "masked server frame"))
            return events, buf, state
        if length == 126:
            if len(buf) < 4:
                return events, buf, state
            length = struct.unpack(">H", buf[2:4])[0]
            if length < 126:
                events.append(("error", "non-minimal length encoding"))
                return events, buf, state
            index = 4
        elif length == 127:
            if len(buf) < 10:
                return events, buf, state
            high, low = struct.unpack(">II", buf[2:10])
            if high & 0x80000000:
                events.append(("error", "64-bit length with the high bit set"))
                return events, buf, state
            length = (high << 32) | low
            if length < 65536:
                events.append(("error", "non-minimal length encoding"))
                return events, buf, state
            index = 10
        is_control = opcode >= 0x8
        if is_control and (not fin or length > 125):
            events.append(("error", "malformed control frame"))
            return events, buf, state
        if len(buf) < index + length:
            return events, buf, state
        payload = bytes(buf[index:index + length])
        buf = buf[index + length:]

        if opcode == 0x8:
            events.append(_parse_close(payload))
            return events, buf, state
        if opcode == 0x9:
            events.append(("ping", payload))
            continue
        if opcode == 0xA:
            events.append(("pong", payload))
            continue
        if opcode not in (0x0, 0x1, 0x2):
            events.append(("error", "unknown opcode 0x%x" % opcode))
            return events, buf, state

        if opcode in (0x1, 0x2):
            if state.fragmented_opcode is not None:
                events.append(("error", "new data frame during a fragmented message"))
                return events, buf, state
            if not fin:
                if len(payload) > state.max_bytes:
                    events.append(("error", "message exceeds the size cap"))
                    return events, buf, state
                state.fragmented_opcode = opcode
                state.message = bytearray(payload)
                state.decoder = (
                    codecs.getincrementaldecoder("utf-8")("strict")
                    if opcode == 0x1 else None
                )
                continue
            try:
                message = _complete_text(
                    payload, codecs.getincrementaldecoder("utf-8")("strict") if opcode == 0x1 else None
                )
            except UnicodeDecodeError:
                events.append(("error", "invalid UTF-8 in a text message"))
                return events, buf, state
            events.append(("message", message))
            continue

        # opcode 0x0: continuation
        if state.fragmented_opcode is None:
            events.append(("error", "continuation without a started message"))
            return events, buf, state
        if len(state.message) + len(payload) > state.max_bytes:
            state.fragmented_opcode = None
            state.message = bytearray()
            state.decoder = None
            events.append(("error", "message exceeds the size cap"))
            return events, buf, state
        state.message += payload
        if fin:
            try:
                message = _complete_text(bytes(state.message), state.decoder)
            except UnicodeDecodeError:
                message = None
                events.append(("error", "invalid UTF-8 in a text message"))
            state.fragmented_opcode = None
            state.message = bytearray()
            state.decoder = None
            if message is not None:
                events.append(("message", message))
        # A control frame interleaved mid-fragment reached this loop
        # without touching the message buffer: reassembly survives it.


def _parse_close(payload: bytes) -> tuple:
    if len(payload) == 1:
        return ("error", "close frame with a 1-byte payload")
    if not payload:
        return ("close", _CLOSE_NO_STATUS, "")
    code = struct.unpack(">H", payload[:2])[0]
    if code in _CLOSE_OFF_WIRE:
        return ("error", "on-wire close code %d" % code)
    if code < 1000 or 2999 < code:
        return ("error", "invalid close code %d" % code)
    try:
        reason = payload[2:].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return ("error", "invalid UTF-8 in the close reason")
    return ("close", code, reason)
