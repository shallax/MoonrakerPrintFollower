"""Executable preview socket frames contracts."""
from tests import preview_family_support as harness

class HandshakeTests(harness.HandshakeTests):
    def test_client_key_is_sixteen_csprng_bytes_in_base64(self):
        keys = {harness.build_client_key() for _ in range(5)}
        self.assertEqual(5, len(keys), "the CSPRNG must not repeat")
        for key in keys:
            self.assertEqual(16, len(harness.base64.b64decode(key)))

    def test_accept_value_matches_the_rfc_6455_vector(self):
        # §1.3's worked example, so the SHA-1+GUID construction is pinned.
        self.assertEqual("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
                         harness.accept_value("dGhlIHNhbXBsZSBub25jZQ=="))
        expected = harness.base64.b64encode(harness.hashlib.sha1(b"k" + harness.SocketFraming.GUID.encode()).digest()).decode()
        self.assertEqual(expected, harness.accept_value("k"))

    def test_upgrade_request_omits_origin_and_cleans_injected_values(self):
        request = harness.build_handshake("printer.local\r\nX-Evil: 1", "key\nvalue",
                                  api_key="secret\0\r\nX-Evil: 2").decode()
        lines = request.strip().split("\r\n")
        headers = [line.split(":", 1)[0].strip().lower() for line in lines]
        self.assertNotIn("x-evil", headers, "CR/LF must not start a header line")
        self.assertEqual("GET /websocket HTTP/1.1", lines[0])
        self.assertIn("host", headers)
        self.assertIn("sec-websocket-key", headers)
        self.assertIn("x-api-key", headers)
        self.assertTrue(request.endswith("\r\n\r\n"))
        self.assertNotIn("origin", headers, "check_cors fails closed on an unconfigured domain")

    def test_upgrade_request_without_an_api_key_carries_no_key_header(self):
        headers = harness.build_handshake("host:7125", "key").decode().split("\r\n")
        self.assertFalse([line for line in headers if line.lower().startswith("x-api-key")])


class VerifyHandshakeTests(harness.VerifyHandshakeTests):
    def test_a_bound_response_is_accepted(self):
        self.assertEqual((True, ""), harness.verify_handshake(harness.upgrade_response(self.KEY), self.KEY))

    def test_header_tokens_are_case_insensitive_and_comma_separated(self):
        head = harness.upgrade_response(self.KEY, upgrade="WebSocket, h2c",
                                connection="keep-alive, UPGRADE")
        self.assertEqual((True, ""), harness.verify_handshake(head, self.KEY))

    def test_an_empty_response_is_refused(self):
        for head in (b"", b"\r\n\r\n", b"  \r\n\r\n"):
            self.assertEqual("empty handshake response", harness.verify_handshake(head, self.KEY)[1])

    def test_interim_blocks_are_validated_and_the_last_block_judged(self):
        head = b"HTTP/1.1 100 Continue\r\n\r\n" + harness.upgrade_response(self.KEY)
        self.assertEqual((True, ""), harness.verify_handshake(head, self.KEY))

    def test_an_empty_interim_block_is_skipped(self):
        head = b"\r\n\r\n" + harness.upgrade_response(self.KEY)
        self.assertEqual((True, ""), harness.verify_handshake(head, self.KEY))

    def test_a_malformed_interim_block_is_refused(self):
        for line in ("HTTP/1.0 100 Continue", "HTTP/1.1 two", "HTTP/1.1 200 OK",
                     "HTTP/1.1 101 Switching Protocols", "HTTP/1.1 1000 Continue"):
            head = line.encode() + b"\r\n\r\n" + harness.upgrade_response(self.KEY)
            ok, reason = harness.verify_handshake(head, self.KEY)
            self.assertFalse(ok)
            self.assertIn("unexpected interim", reason)

    def test_a_refused_status_line_names_the_response(self):
        ok, reason = harness.verify_handshake(harness.upgrade_response(self.KEY, status="HTTP/1.1 403 Forbidden"),
                                      self.KEY)
        self.assertFalse(ok)
        self.assertIn("handshake refused: HTTP/1.1 403 Forbidden", reason)

    def test_missing_upgrade_and_connection_tokens_are_refused(self):
        self.assertIn("Upgrade", harness.verify_handshake(harness.upgrade_response(self.KEY, upgrade="h2c"), self.KEY)[1])
        self.assertIn("Connection", harness.verify_handshake(harness.upgrade_response(self.KEY, connection="close"), self.KEY)[1])
        self.assertIn("Upgrade", harness.verify_handshake(harness.upgrade_response(self.KEY, upgrade=None), self.KEY)[1])

    def test_an_unrequested_extension_or_subprotocol_is_refused(self):
        for header in ("Sec-WebSocket-Extensions: permessage-deflate", "Sec-WebSocket-Protocol: mqtt"):
            ok, reason = harness.verify_handshake(harness.upgrade_response(self.KEY, extra=[header]), self.KEY)
            self.assertFalse(ok)
            self.assertIn("unrequested extension or subprotocol", reason)

    def test_an_accept_value_for_another_key_is_refused(self):
        head = harness.upgrade_response(self.KEY).replace(harness.accept_value(self.KEY).encode(), b"wrong")
        self.assertEqual("Sec-WebSocket-Accept mismatch", harness.verify_handshake(head, self.KEY)[1])

    def test_a_headerless_line_is_ignored_rather_than_parsed(self):
        head = b"HTTP/1.1 101 Switching Protocols\r\nnot a header\r\nSec-WebSocket-Accept: %s\r\n\r\n" \
               % harness.accept_value(self.KEY).encode()
        # The unparsable line drops out, so the missing Upgrade token decides.
        self.assertIn("Upgrade", harness.verify_handshake(head, self.KEY)[1])


class ClientFrameEncodingTests(harness.ClientFrameEncodingTests):
    def test_short_payloads_use_the_inline_length_and_set_the_mask_bit(self):
        frame = harness.encode_text_frame(b"hello")
        self.assertEqual(0x81, frame[0])
        self.assertEqual(0x80 | 5, frame[1])
        self.assertEqual(b"hello", harness.payload_of(frame))
        self.assertEqual(0xA, harness.encode_pong(b"x")[0] & 0x0F)
        self.assertEqual(0x9, harness.encode_ping()[0] & 0x0F)
        self.assertEqual(0x2, harness.encode_binary_frame(b"")[0] & 0x0F)

    def test_extended_lengths_cover_both_wire_forms(self):
        for size, marker in ((125, 125), (126, 126), (65535, 126), (65536, 127)):
            frame = harness.encode_binary_frame(b"z" * size)
            self.assertEqual(marker, frame[1] & 0x7F, "wire form for %d bytes" % size)
            self.assertEqual(size, len(harness.payload_of(frame)), "size %d survived the wire form" % size)
            if marker == 126:
                self.assertEqual(size, harness.struct.unpack(">H", frame[2:4])[0])
            elif marker == 127:
                self.assertEqual(size, harness.struct.unpack(">Q", frame[2:10])[0])

    def test_a_control_frame_payload_over_125_bytes_is_refused(self):
        with self.assertRaises(harness.FramingError):
            harness.encode_ping(b"x" * 126)
        with self.assertRaises(harness.FramingError):
            harness.encode_close_frame(1000, b"r" * 124)

    def test_close_codes_off_the_wire_or_out_of_range_are_refused(self):
        for code in (999, 1005, 1006, 1015, 5000):
            with self.assertRaises(harness.FramingError):
                harness.encode_close_frame(code)
        self.assertEqual(0x88, harness.encode_close_frame(1000, b"bye")[0])
        self.assertEqual(0x88, harness.encode_close_frame(4999)[0])


class ParseFramesTests(harness.ParseFramesTests):
    def test_a_complete_text_message_round_trips(self):
        events, rest, state = harness.parse_frames(harness.client_frame(0x1, b"hello"), harness.FrameState())
        self.assertEqual([("message", b"hello")], events)
        self.assertEqual(b"", rest)
        self.assertIsNone(state.fragmented_opcode)

    def test_a_partial_frame_is_held_for_the_next_read(self):
        whole = harness.client_frame(0x1, b"hello")
        state = harness.FrameState()
        events, rest, state = harness.parse_frames(whole[:4], state)
        self.assertEqual([], events)
        self.assertEqual(whole[:4], rest)
        events, rest, state = harness.parse_frames(rest + whole[4:], state)
        self.assertEqual([("message", b"hello")], events)
        self.assertEqual(b"", rest)

    def test_an_incomplete_extended_length_header_waits(self):
        long = harness.client_frame(0x2, b"z" * 300)
        state = harness.FrameState()
        events, rest, _ = harness.parse_frames(long[:3], state)   # 16-bit form, header cut
        self.assertEqual([], events)
        events, rest, _ = harness.parse_frames(rest + long[3:], state)
        self.assertEqual([("message", b"z" * 300)], events)
        huge = harness.client_frame(0x2, b"z" * 70000)
        state = harness.FrameState()
        events, rest, _ = harness.parse_frames(huge[:5], state)   # 64-bit form, header cut
        self.assertEqual([], events)
        events, rest, _ = harness.parse_frames(rest + huge[5:], state)
        self.assertEqual([("message", b"z" * 70000)], events)

    def test_control_frames_report_their_payload(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x9, b"hi") + harness.client_frame(0xA, b"yo"),
                                    harness.FrameState())
        self.assertEqual([("ping", b"hi"), ("pong", b"yo")], events)

    def test_reserved_bits_and_a_masked_server_frame_fail_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x1, b"x", rsv=0x40), harness.FrameState())
        self.assertEqual([("error", "reserved bits set")], events)
        events, _, _ = harness.parse_frames(bytes([0x81, 0x80 | 1]) + b"\x00abcd", harness.FrameState())
        self.assertEqual([("error", "masked server frame")], events)

    def test_non_minimal_and_oversized_length_encodings_fail_closed(self):
        events, _, _ = harness.parse_frames(bytes([0x81, 126]) + harness.struct.pack(">H", 5) + b"hello", harness.FrameState())
        self.assertEqual([("error", "non-minimal length encoding")], events)
        events, _, _ = harness.parse_frames(bytes([0x81, 127]) + harness.struct.pack(">Q", 5) + b"hello", harness.FrameState())
        self.assertEqual([("error", "non-minimal length encoding")], events)
        events, _, _ = harness.parse_frames(bytes([0x81, 127]) + harness.struct.pack(">Q", 1 << 63) + b"x", harness.FrameState())
        self.assertEqual([("error", "64-bit length with the high bit set")], events)

    def test_a_fragmented_or_oversized_control_frame_fails_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x9, b"x", fin=False), harness.FrameState())
        self.assertEqual([("error", "malformed control frame")], events)
        events, _, _ = harness.parse_frames(harness.client_frame(0x8, b"x" * 126), harness.FrameState())
        self.assertEqual([("error", "malformed control frame")], events)

    def test_an_unknown_opcode_fails_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x3, b"x"), harness.FrameState())
        self.assertEqual([("error", "unknown opcode 0x3")], events)

    def test_a_text_message_with_invalid_utf8_fails_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x1, b"\xff\xfe"), harness.FrameState())
        self.assertEqual([("error", "invalid UTF-8 in a text message")], events)

    def test_a_codepoint_split_across_fragments_is_legal(self):
        state = harness.FrameState()
        encoded = "é".encode()
        events, _, state = harness.parse_frames(harness.client_frame(0x1, encoded[:1], fin=False), state)
        self.assertEqual([], events)
        events, _, state = harness.parse_frames(harness.client_frame(0x0, encoded[1:]), state)
        self.assertEqual([("message", "é".encode())], events)
        self.assertIsNone(state.decoder)

    def test_fragments_reassemble_and_control_frames_may_interleave(self):
        state = harness.FrameState()
        events, _, state = harness.parse_frames(harness.client_frame(0x2, b"one", fin=False), state)
        self.assertEqual([0x2], [state.fragmented_opcode])
        events, _, state = harness.parse_frames(harness.client_frame(0x9, b"mid") + harness.client_frame(0x0, b"two"), state)
        self.assertEqual([("ping", b"mid"), ("message", b"onetwo")], events)

    def test_a_new_data_frame_mid_fragment_fails_closed(self):
        state = harness.FrameState()
        harness.parse_frames(harness.client_frame(0x1, b"a", fin=False), state)
        events, _, _ = harness.parse_frames(harness.client_frame(0x1, b"b"), state)
        self.assertEqual([("error", "new data frame during a fragmented message")], events)

    def test_a_continuation_without_a_start_fails_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x0, b"b"), harness.FrameState())
        self.assertEqual([("error", "continuation without a started message")], events)

    def test_a_fragmented_message_beyond_the_cap_fails_closed_and_resets(self):
        state = harness.FrameState(max_bytes=8)
        events, _, state = harness.parse_frames(harness.client_frame(0x1, b"123456", fin=False), state)
        self.assertEqual([], events)
        events, _, state = harness.parse_frames(harness.client_frame(0x0, b"789", fin=True), state)
        self.assertEqual([("error", "message exceeds the size cap")], events)
        self.assertIsNone(state.fragmented_opcode, "the bomb must not leave a half message behind")

    def test_a_single_frame_beyond_the_cap_fails_closed(self):
        state = harness.FrameState(max_bytes=4)
        events, _, _ = harness.parse_frames(harness.client_frame(0x1, b"12345", fin=False), state)
        self.assertEqual([("error", "message exceeds the size cap")], events)

    def test_a_final_fragment_with_invalid_utf8_fails_closed(self):
        state = harness.FrameState()
        harness.parse_frames(harness.client_frame(0x1, b"ok", fin=False), state)
        events, _, state = harness.parse_frames(harness.client_frame(0x0, b"\xff"), state)
        self.assertEqual([("error", "invalid UTF-8 in a text message")], events)
        self.assertIsNone(state.decoder)

    def test_the_default_cap_is_one_mebibyte(self):
        self.assertEqual(1_048_576, harness.MAX_MESSAGE_BYTES)
        self.assertEqual(harness.MAX_MESSAGE_BYTES, harness.FrameState().max_bytes)


class CloseFrameTests(harness.CloseFrameTests):
    def test_a_close_frame_yields_code_and_reason(self):
        events, rest, _ = harness.parse_frames(harness.client_frame(0x8, harness.struct.pack(">H", 1000) + b"bye"),
                                       harness.FrameState())
        self.assertEqual([("close", 1000, "bye")], events)
        self.assertEqual(b"", rest)

    def test_a_bare_close_frame_reports_no_status(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x8), harness.FrameState())
        self.assertEqual([("close", 1005, "")], events)

    def test_a_one_byte_close_payload_fails_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x8, b"\x03"), harness.FrameState())
        self.assertEqual([("error", "close frame with a 1-byte payload")], events)

    def test_on_wire_and_out_of_range_close_codes_fail_closed(self):
        for code in (1005, 1006, 1015, 999, 3000):
            events, _, _ = harness.parse_frames(harness.client_frame(0x8, harness.struct.pack(">H", code)), harness.FrameState())
            self.assertEqual(1, len(events))
            self.assertEqual("error", events[0][0])
            self.assertIn(str(code), events[0][1])

    def test_a_close_reason_that_is_not_utf8_fails_closed(self):
        events, _, _ = harness.parse_frames(harness.client_frame(0x8, harness.struct.pack(">H", 1000) + b"\xff"), harness.FrameState())
        self.assertEqual([("error", "invalid UTF-8 in the close reason")], events)


