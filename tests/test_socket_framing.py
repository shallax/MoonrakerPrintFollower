"""Byte-level acceptance tests for the pure RFC 6455 framing module.

The table below is the security panel's S5/S7/S10/S11 list plus the
engineering panel's E4 additions — including the ENCODE side of the
extended lengths, which the live probe caught (a >125-byte payload with
a short-form header is a corrupt frame the peer drops silently).
"""
import struct
import unittest

from plugins.SocketFraming import (
    FrameState,
    FramingError,
    MAX_HANDSHAKE_HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    accept_value,
    build_client_key,
    build_handshake,
    encode_binary_frame,
    encode_close_frame,
    encode_ping,
    encode_text_frame,
    parse_frames,
    verify_handshake,
)


class HandshakeRequestTests(unittest.TestCase):
    def test_request_carries_the_required_headers_and_no_origin(self):
        request = build_handshake("printer:7125", "sample-key").decode()
        self.assertIn("GET /websocket HTTP/1.1\r\n", request)
        self.assertIn("Host: printer:7125\r\n", request)
        self.assertIn("Upgrade: websocket\r\n", request)
        self.assertIn("Connection: Upgrade\r\n", request)
        self.assertIn("Sec-WebSocket-Key: sample-key\r\n", request)
        self.assertIn("Sec-WebSocket-Version: 13\r\n", request)
        self.assertNotIn("Origin", request)

    def test_api_key_rides_the_handshake_when_given(self):
        request = build_handshake("printer", "k", api_key="secret").decode()
        self.assertIn("X-Api-Key: secret\r\n", request)
        self.assertNotIn("X-Api-Key", build_handshake("printer", "k").decode())

    def test_header_values_are_sanitised_of_cr_lf_and_nul(self):
        # The hand-rolled writer has no HTTP parser to police values (S10):
        # CR/LF/NUL are stripped, so the injection can never become a
        # separate header line — the residue dies inside the Host value.
        request = build_handshake("evil\r\nX-Evil: 1", "k").decode()
        self.assertNotIn("\r\nX-Evil", request)
        self.assertEqual(request.count("Host:"), 1)

    def test_build_client_key_is_16_csprng_bytes_base64(self):
        import base64
        first = build_client_key()
        second = build_client_key()
        self.assertNotEqual(first, second)
        self.assertEqual(len(base64.b64decode(first)), 16)

    def test_accept_value_matches_the_rfc_example(self):
        # RFC 6455 1.3: the sample nonce's expected accept.
        self.assertEqual(
            accept_value("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        )


class HandshakeVerifyTests(unittest.TestCase):
    def _head(self, key, extra_lines=(), status="101 Switching Protocols"):
        lines = [f"HTTP/1.1 {status}"] + list(extra_lines)
        if not any(line.lower().startswith("upgrade:") for line in lines):
            lines.append("Upgrade: websocket")
        if not any(line.lower().startswith("connection:") for line in lines):
            lines.append("Connection: Upgrade")
        if not any(line.lower().startswith("sec-websocket-accept:") for line in lines):
            lines.append(f"Sec-WebSocket-Accept: {accept_value(key)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def test_valid_101_passes(self):
        ok, reason = verify_handshake(self._head("sample-key"), "sample-key")
        self.assertTrue(ok, reason)

    def test_wrong_accept_fails(self):
        ok, _ = verify_handshake(self._head("sample-key"), "other-key")
        self.assertFalse(ok)

    def test_non_101_status_fails(self):
        ok, reason = verify_handshake(self._head("k", status="200 OK"), "k")
        self.assertFalse(ok)
        self.assertIn("200", reason)

    def test_redirect_fails_closed(self):
        head = (
            "HTTP/1.1 302 Found\r\n"
            "Location: https://evil.example/websocket\r\n\r\n"
        ).encode()
        ok, reason = verify_handshake(head, "k")
        self.assertFalse(ok)
        self.assertIn("302", reason)

    def test_missing_upgrade_or_connection_fails(self):
        self.assertFalse(verify_handshake(self._head("k", ("Upgrade: h2c",)), "k")[0])
        self.assertFalse(verify_handshake(self._head("k", ("Connection: close",)), "k")[0])

    def test_connection_token_list_is_parsed(self):
        ok, _ = verify_handshake(
            self._head("k", ("Connection: keep-alive, Upgrade",)), "k"
        )
        self.assertTrue(ok)

    def test_unoffered_extension_or_subprotocol_fails(self):
        self.assertFalse(verify_handshake(
            self._head("k", ("Sec-WebSocket-Extensions: permessage-deflate",)), "k")[0])
        self.assertFalse(verify_handshake(
            self._head("k", ("Sec-WebSocket-Protocol: chat",)), "k")[0])

    def test_informational_1xx_before_the_101_is_tolerated(self):
        head = (
            "HTTP/1.1 100 Continue\r\n\r\n"
        ).encode() + self._head("k")
        ok, reason = verify_handshake(head, "k")
        self.assertTrue(ok, reason)

    def test_handshake_header_cap_is_a_stated_constant(self):
        # The caller bounds the read loop with this before parsing (S7);
        # the constant is the contract the socket owner must honour.
        self.assertGreater(MAX_HANDSHAKE_HEADER_BYTES, 0)


class FrameParseTests(unittest.TestCase):
    @staticmethod
    def server_frame(opcode, payload=b"", fin=True):
        # Extended lengths use the marker form — the same encode-side
        # rule the live probe caught on the wire.
        head = bytes([(0x80 if fin else 0) | opcode])
        length = len(payload)
        if length < 126:
            head += bytes([length])
        elif length < 65536:
            head += bytes([126]) + struct.pack(">H", length)
        else:
            head += bytes([127]) + struct.pack(">Q", length)
        return head + payload

    @staticmethod
    def events(buffer, state=None):
        events, rest, state = parse_frames(buffer, state or FrameState())
        return events, rest, state

    def test_text_frame_round_trip(self):
        events, rest, _ = self.events(self.server_frame(1, b"hello"))
        self.assertEqual(events, [("message", b"hello")])
        self.assertEqual(rest, b"")

    def test_incomplete_frame_is_returned_as_rest(self):
        data = self.server_frame(1, b"hello")
        events, rest, _ = self.events(data[:3])
        self.assertEqual(events, [])
        self.assertEqual(rest, data[:3])

    def test_multiple_frames_in_one_buffer(self):
        data = self.server_frame(1, b"a") + self.server_frame(9, b"ping")
        events, rest, _ = self.events(data)
        self.assertEqual(events, [("message", b"a"), ("ping", b"ping")])
        self.assertEqual(rest, b"")

    def test_masked_server_frame_fails(self):
        payload = b"x"
        head = bytes([0x81, 0x80 | len(payload)]) + b"mask" + payload
        events, _, _ = self.events(head)
        self.assertEqual(events, [("error", "masked server frame")])

    def test_each_rsv_bit_fails(self):
        for rsv in (0x40, 0x20, 0x10):
            events, _, _ = self.events(bytes([0x81 | rsv, 0]))
            self.assertEqual(events[0][0], "error")

    def test_unknown_opcodes_fail(self):
        for opcode in (0x3, 0x4, 0x5, 0x6, 0x7, 0xB, 0xC, 0xD, 0xE, 0xF):
            events, _, _ = self.events(self.server_frame(opcode))
            self.assertEqual(events[0][0], "error")

    def test_control_frame_length_126_fails(self):
        events, _, _ = self.events(
            bytes([0x89, 126]) + struct.pack(">H", 126) + b"x" * 126)
        self.assertEqual(events[0][0], "error")

    def test_fragmented_control_frame_fails(self):
        events, _, _ = self.events(self.server_frame(9, b"p", fin=False))
        self.assertEqual(events[0][0], "error")

    def test_64_bit_length_with_high_bit_set_fails(self):
        events, _, _ = self.events(bytes([0x81, 127]) + struct.pack(">Q", 1 << 63))
        self.assertEqual(events[0][0], "error")

    def test_non_minimal_length_encodings_fail(self):
        events, _, _ = self.events(bytes([0x81, 126]) + struct.pack(">H", 125) + b"x" * 125)
        self.assertEqual(events[0][0], "error")
        events, _, _ = self.events(bytes([0x81, 127]) + struct.pack(">Q", 65535) + b"x" * 65535)
        self.assertEqual(events[0][0], "error")

    def test_close_with_1_byte_payload_fails(self):
        events, _, _ = self.events(self.server_frame(8, b"\x03"))
        self.assertEqual(events[0][0], "error")

    def test_reserved_close_codes_fail(self):
        for code in (1005, 1006, 1015):
            events, _, _ = self.events(self.server_frame(8, struct.pack(">H", code)))
            self.assertEqual(events[0][0], "error")

    def test_close_with_a_reason_parses(self):
        events, _, _ = self.events(
            self.server_frame(8, struct.pack(">H", 1000) + b"bye"))
        self.assertEqual(events, [("close", 1000, "bye")])

    def test_16_and_64_bit_server_lengths_parse(self):
        for size in (126, 2000, 65536):
            events, rest, _ = self.events(self.server_frame(2, b"x" * size))
            self.assertEqual(events[0][0], "message")
            self.assertEqual(len(events[0][1]), size)
            self.assertEqual(rest, b"")


class FrameEncodeTests(unittest.TestCase):
    @staticmethod
    def decode_client_frame(frame):
        """Undo the client's masking to recover the payload + length form."""
        first, second = frame[0], frame[1]
        opcode = first & 0x0F
        length = second & 0x7F
        index = 2
        if length == 126:
            length = struct.unpack(">H", frame[2:4])[0]
            index = 4
        elif length == 127:
            length = struct.unpack(">Q", frame[2:10])[0]
            index = 10
        mask = frame[index:index + 4]
        payload = frame[index + 4:index + 4 + length]
        return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def test_short_length_frame(self):
        opcode, payload = self.decode_client_frame(encode_text_frame(b"probe-ping"))
        self.assertEqual((opcode, payload), (1, b"probe-ping"))

    def test_16_bit_length_frame(self):
        frame = encode_text_frame(b"x" * 160)
        self.assertEqual(frame[1], 0x80 | 126)
        self.assertEqual(struct.unpack(">H", frame[2:4])[0], 160)
        _, payload = self.decode_client_frame(frame)
        self.assertEqual(len(payload), 160)

    def test_64_bit_length_frame(self):
        frame = encode_binary_frame(b"x" * 65536)
        self.assertEqual(frame[1], 0x80 | 127)
        self.assertEqual(struct.unpack(">Q", frame[2:10])[0], 65536)

    def test_control_frame_over_125_bytes_fails(self):
        with self.assertRaises(FramingError):
            encode_ping(b"x" * 126)

    def test_close_codes_off_wire_are_refused(self):
        for code in (1005, 1006, 1015, 0, 5000):
            with self.assertRaises(FramingError):
                encode_close_frame(code)


class MessageAssemblyTests(unittest.TestCase):
    @staticmethod
    def frame(opcode, payload=b"", fin=True):
        return FrameParseTests.server_frame(opcode, payload, fin)

    def test_single_text_frame_returns_the_payload(self):
        events, _, _ = FrameParseTests.events(self.frame(1, b"hello"))
        self.assertEqual(events, [("message", b"hello")])

    def test_fragmented_text_across_a_utf8_codepoint_is_legal(self):
        state = FrameState()
        events, _, state = FrameParseTests.events(self.frame(1, b"\xe2\x82", fin=False), state)
        self.assertEqual(events, [])
        events, _, state = FrameParseTests.events(self.frame(0, b"\xac", fin=False), state)
        self.assertEqual(events, [])
        events, _, state = FrameParseTests.events(self.frame(0, b"", fin=True), state)
        self.assertEqual(events, [("message", "€".encode())])

    def test_invalid_utf8_fails(self):
        events, _, _ = FrameParseTests.events(self.frame(1, b"\xff\xfe"))
        self.assertEqual(events[0][0], "error")

    def test_invalid_utf8_across_fragments_fails(self):
        state = FrameState()
        events, _, state = FrameParseTests.events(self.frame(1, b"\xe2\x28", fin=False), state)
        self.assertEqual(events, [])
        events, _, state = FrameParseTests.events(self.frame(0, b"\xa1", fin=True), state)
        self.assertEqual(events[0][0], "error")

    def test_orphan_continuation_fails(self):
        events, _, _ = FrameParseTests.events(self.frame(0, b"x", fin=True))
        self.assertEqual(events[0][0], "error")

    def test_new_data_frame_mid_fragmentation_fails(self):
        state = FrameState()
        FrameParseTests.events(self.frame(1, b"abc", fin=False), state)
        events, _, _ = FrameParseTests.events(self.frame(1, b"def", fin=True), state)
        self.assertEqual(events[0][0], "error")

    def test_cumulative_cap_across_fragments_fails(self):
        state = FrameState(max_bytes=10)
        FrameParseTests.events(self.frame(1, b"12345678", fin=False), state)
        events, _, _ = FrameParseTests.events(self.frame(0, b"901", fin=False), state)
        self.assertEqual(events[0][0], "error")
        self.assertEqual(state.fragmented_opcode, None)

    def test_binary_fragments_reassemble_without_utf8(self):
        state = FrameState()
        FrameParseTests.events(self.frame(2, b"\xff\xfe", fin=False), state)
        events, _, _ = FrameParseTests.events(self.frame(0, b"\xfd", fin=True), state)
        self.assertEqual(events, [("message", b"\xff\xfe\xfd")])

    def test_control_frame_interleaved_mid_fragmentation_survives(self):
        state = FrameState()
        FrameParseTests.events(self.frame(1, b"ab", fin=False), state)
        events, _, state = FrameParseTests.events(self.frame(9, b"ping"), state)
        self.assertEqual(events, [("ping", b"ping")])
        events, _, state = FrameParseTests.events(self.frame(0, b"cd", fin=True), state)
        self.assertEqual(events, [("message", b"abcd")])

    def test_cap_constant_is_the_documented_bound(self):
        self.assertEqual(MAX_MESSAGE_BYTES, 1_048_576)


if __name__ == "__main__":
    unittest.main()
