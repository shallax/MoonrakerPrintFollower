"""Preview-family coverage: the follower, motion, framing, toolhead and data seams.

Targets the branch-heavy error and fallback paths of the six modules the domain
suites reach mainly through their happy paths. Every double is local and
minimal: the Cura port/view, the index, the client and the snapshot are faked
at exactly the seam the module under test calls.

Qt-bound paths run against the real PyQt6 in the container (offscreen).

Lines no test can reach, with the reason:

* PreviewMotion.py:153  — ``write()``'s ramp-from-fraction fallback for a
  missing previous observation. The jump branch and ``reset()`` set
  ``_obs_time`` and ``_displayed`` together, so the non-jump path this
  guards always has a previous observation.
* PrintState.py:267-269 — the "past the last known start" arm of the
  height ladder. It needs the highest start at or below ``z`` to be the
  final entry, but that same entry is then within ``z_tolerance`` of
  ``z`` and the exact-match arm above has already claimed it.
* ToolheadPolicy.py:85  — ``clamp_relative_move``'s inverted-range
  (``minimum > maximum``) refusal. No target satisfies both clamps at
  once, so the floor or the maximum clamp returns first.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from qt_runtime_support import QT_AVAILABLE

from plugins import SocketFraming
from plugins.SocketFraming import (
    FrameState,
    FramingError,
    MAX_MESSAGE_BYTES,
    accept_value,
    build_client_key,
    build_handshake,
    encode_binary_frame,
    encode_close_frame,
    encode_ping,
    encode_pong,
    encode_text_frame,
    parse_frames,
    verify_handshake,
)
from plugins.ToolheadPolicy import (
    CENTER_Z_MM,
    EXTRUDE_SPEED_MAX,
    JOG_DISTANCE_DEFAULT,
    JOG_DISTANCE_MAX,
    JOG_DISTANCE_MIN,
    MAX_PENDING_OPS,
    STATUS_QUEUE_FULL,
    axis_ok,
    center_script,
    clamp_relative_move,
    extrude_distance_ok,
    extrude_script,
    extrude_speed_ok,
    home_script,
    jog_distance_ok,
    jog_gate,
    jog_script,
    make_center_op,
    make_extrude_op,
    make_home_op,
    make_jog_op,
    make_motors_off_op,
    make_z0_op,
    motors_off_script,
    position_mode_text,
    push_op,
    z0_script,
)
from plugins.PrintState import LayerResolver, PhysicalLayer, PrintSnapshot
from plugins.PreviewFollower import PreviewFollower, preview_override_kind

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal
    from plugins.MonitorData import MonitorData, freeze
    from plugins.PreviewMotion import PreviewMotion


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class FakeView:
    """The Cura SimulationView subset the Preview family reads and writes."""

    def __init__(self, layer=0, minimum=0, path=0.0, maximum=100, minimum_path=0):
        self.layer, self.minimum, self.path = int(layer), int(minimum), float(path)
        self.maximum, self.minimum_path = maximum, int(minimum_path)
        self.writes = []          # (handle, value) from apply_preview_decision
        self.paths = []           # setPath calls
        self.minimum_paths = []   # setMinimumPath calls
        self.resets = 0           # resetLayerData calls

    def getCurrentLayer(self): return self.layer
    def getMinimumLayer(self): return self.minimum
    def getCurrentPath(self): return self.path
    def getMinimumPath(self): return self.minimum_path
    def getMaxPaths(self): return self.maximum

    def setLayer(self, value): self.layer = int(value); self.writes.append(("layer", int(value)))
    def setMinimumLayer(self, value): self.minimum = int(value); self.writes.append(("minimum", int(value)))
    def setPath(self, value): self.path = float(value); self.paths.append(float(value))
    def setMinimumPath(self, value): self.minimum_path = int(value); self.minimum_paths.append(int(value))
    def resetLayerData(self): self.resets += 1


@contextmanager
def writing_preview():
    yield


class FakeCura:
    """The Cura port PreviewFollower drives."""

    def __init__(self, view=None):
        self.view = view
        self.suspended = False
        self.has_toolpath = True
        self.max_layer = 10
        self.selected_layer = None
        self.switches = 0
        self.nozzles = 0
        self.switch_result = True
        self.writes = 0

    def switch_to_preview(self):
        self.switches += 1
        return self.switch_result

    def show_nozzle(self): self.nozzles += 1

    def writing_preview(self):
        self.writes += 1
        return writing_preview()


class FakeIndex:
    """The MultiIndex subset PreviewFollower reads."""

    def __init__(self, layers=5, elapsed=None, hydrated=None, fraction=0.5,
                 method="byte-range", mapping=None, at=None):
        self.ranges = tuple((0, 100) for _ in range(layers))
        self.elapsed_times = [10.0 * i for i in range(layers)] if elapsed is None else list(elapsed)
        self._hydrated = set(range(layers)) if hydrated is None else set(hydrated)
        self._fraction, self.method = fraction, method
        self.current_layer_map = dict(mapping or {})
        self._at = dict(at or {})

    def hydrated(self, layer): return layer in self._hydrated
    def fraction(self, layer, position, live, previous): return (self._fraction, self.method)
    def layer_at(self, position): return self._at.get(position)


def preview_config(**overrides):
    base = dict(enabled=True, follow_mode="exact", auto_preview=False, path_follow=False,
                eta_learn=False, path_smoothing=True, show_toolhead_indicator=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def snapshot(layer=None, active=True, state="printing"):
    return SimpleNamespace(layer=SimpleNamespace(index=layer), active=active,
                           observation=SimpleNamespace(state=state))


def status(**overrides):
    base = {"print_stats": {"print_duration": 60.0}, "gcode_move": {"speed_factor": 1.0}}
    base.update(overrides)
    return base


class MotionTrace:
    """PreviewMotion's `remember` hook plus the writes a follower delegates."""

    def __init__(self):
        self.calls = 0
        self.reset_calls = 0
        self.writes = []

    def __call__(self):
        self.calls += 1

    def reset(self):
        self.reset_calls += 1

    def write(self, layer, fraction, method=""):
        self.writes.append((layer, fraction, method))


# --------------------------------------------------------------------------
# SocketFraming — pure RFC 6455 client framing
# --------------------------------------------------------------------------


def client_frame(opcode, payload=b"", *, fin=True, rsv=0):
    """A well-formed, unmasked server frame (the client role expects no mask)."""
    first = (0x80 if fin else 0x00) | rsv | opcode
    length = len(payload)
    if length < 126:
        head = bytes([first, length])
    elif length < 65536:
        head = bytes([first, 126]) + struct.pack(">H", length)
    else:
        head = bytes([first, 127]) + struct.pack(">Q", length)
    return head + payload


def payload_of(frame):
    """The masked payload of a client frame, un-XORed with its own mask."""
    length = frame[1] & 0x7F
    index = 2
    if length == 126:
        length, index = struct.unpack(">H", frame[2:4])[0], 4
    elif length == 127:
        length, index = struct.unpack(">Q", frame[2:10])[0], 10
    mask = frame[index:index + 4]
    body = frame[index + 4:index + 4 + length]
    return bytes(byte ^ mask[n % 4] for n, byte in enumerate(body))


class HandshakeTests(unittest.TestCase):
    def test_client_key_is_sixteen_csprng_bytes_in_base64(self):
        keys = {build_client_key() for _ in range(5)}
        self.assertEqual(5, len(keys), "the CSPRNG must not repeat")
        for key in keys:
            self.assertEqual(16, len(base64.b64decode(key)))

    def test_accept_value_matches_the_rfc_6455_vector(self):
        # §1.3's worked example, so the SHA-1+GUID construction is pinned.
        self.assertEqual("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
                         accept_value("dGhlIHNhbXBsZSBub25jZQ=="))
        expected = base64.b64encode(hashlib.sha1(b"k" + SocketFraming.GUID.encode()).digest()).decode()
        self.assertEqual(expected, accept_value("k"))

    def test_upgrade_request_omits_origin_and_cleans_injected_values(self):
        request = build_handshake("printer.local\r\nX-Evil: 1", "key\nvalue",
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
        headers = build_handshake("host:7125", "key").decode().split("\r\n")
        self.assertFalse([line for line in headers if line.lower().startswith("x-api-key")])


def upgrade_response(key, *, status="HTTP/1.1 101 Switching Protocols",
                     upgrade="websocket", connection="Upgrade", extra=()):
    lines = [status]
    if upgrade is not None: lines.append("Upgrade: %s" % upgrade)
    if connection is not None: lines.append("Connection: %s" % connection)
    lines.append("Sec-WebSocket-Accept: %s" % accept_value(key))
    lines.extend(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class VerifyHandshakeTests(unittest.TestCase):
    KEY = "dGhlIHNhbXBsZSBub25jZQ=="

    def test_a_bound_response_is_accepted(self):
        self.assertEqual((True, ""), verify_handshake(upgrade_response(self.KEY), self.KEY))

    def test_header_tokens_are_case_insensitive_and_comma_separated(self):
        head = upgrade_response(self.KEY, upgrade="WebSocket, h2c",
                                connection="keep-alive, UPGRADE")
        self.assertEqual((True, ""), verify_handshake(head, self.KEY))

    def test_an_empty_response_is_refused(self):
        for head in (b"", b"\r\n\r\n", b"  \r\n\r\n"):
            self.assertEqual("empty handshake response", verify_handshake(head, self.KEY)[1])

    def test_interim_blocks_are_validated_and_the_last_block_judged(self):
        head = b"HTTP/1.1 100 Continue\r\n\r\n" + upgrade_response(self.KEY)
        self.assertEqual((True, ""), verify_handshake(head, self.KEY))

    def test_an_empty_interim_block_is_skipped(self):
        head = b"\r\n\r\n" + upgrade_response(self.KEY)
        self.assertEqual((True, ""), verify_handshake(head, self.KEY))

    def test_a_malformed_interim_block_is_refused(self):
        for line in ("HTTP/1.0 100 Continue", "HTTP/1.1 two", "HTTP/1.1 200 OK",
                     "HTTP/1.1 101 Switching Protocols", "HTTP/1.1 1000 Continue"):
            head = line.encode() + b"\r\n\r\n" + upgrade_response(self.KEY)
            ok, reason = verify_handshake(head, self.KEY)
            self.assertFalse(ok)
            self.assertIn("unexpected interim", reason)

    def test_a_refused_status_line_names_the_response(self):
        ok, reason = verify_handshake(upgrade_response(self.KEY, status="HTTP/1.1 403 Forbidden"),
                                      self.KEY)
        self.assertFalse(ok)
        self.assertIn("handshake refused: HTTP/1.1 403 Forbidden", reason)

    def test_missing_upgrade_and_connection_tokens_are_refused(self):
        self.assertIn("Upgrade", verify_handshake(upgrade_response(self.KEY, upgrade="h2c"), self.KEY)[1])
        self.assertIn("Connection", verify_handshake(upgrade_response(self.KEY, connection="close"), self.KEY)[1])
        self.assertIn("Upgrade", verify_handshake(upgrade_response(self.KEY, upgrade=None), self.KEY)[1])

    def test_an_unrequested_extension_or_subprotocol_is_refused(self):
        for header in ("Sec-WebSocket-Extensions: permessage-deflate", "Sec-WebSocket-Protocol: mqtt"):
            ok, reason = verify_handshake(upgrade_response(self.KEY, extra=[header]), self.KEY)
            self.assertFalse(ok)
            self.assertIn("unrequested extension or subprotocol", reason)

    def test_an_accept_value_for_another_key_is_refused(self):
        head = upgrade_response(self.KEY).replace(accept_value(self.KEY).encode(), b"wrong")
        self.assertEqual("Sec-WebSocket-Accept mismatch", verify_handshake(head, self.KEY)[1])

    def test_a_headerless_line_is_ignored_rather_than_parsed(self):
        head = b"HTTP/1.1 101 Switching Protocols\r\nnot a header\r\nSec-WebSocket-Accept: %s\r\n\r\n" \
               % accept_value(self.KEY).encode()
        # The unparsable line drops out, so the missing Upgrade token decides.
        self.assertIn("Upgrade", verify_handshake(head, self.KEY)[1])


class ClientFrameEncodingTests(unittest.TestCase):
    def test_short_payloads_use_the_inline_length_and_set_the_mask_bit(self):
        frame = encode_text_frame(b"hello")
        self.assertEqual(0x81, frame[0])
        self.assertEqual(0x80 | 5, frame[1])
        self.assertEqual(b"hello", payload_of(frame))
        self.assertEqual(0xA, encode_pong(b"x")[0] & 0x0F)
        self.assertEqual(0x9, encode_ping()[0] & 0x0F)
        self.assertEqual(0x2, encode_binary_frame(b"")[0] & 0x0F)

    def test_extended_lengths_cover_both_wire_forms(self):
        for size, marker in ((125, 125), (126, 126), (65535, 126), (65536, 127)):
            frame = encode_binary_frame(b"z" * size)
            self.assertEqual(marker, frame[1] & 0x7F, "wire form for %d bytes" % size)
            self.assertEqual(size, len(payload_of(frame)), "size %d survived the wire form" % size)
            if marker == 126:
                self.assertEqual(size, struct.unpack(">H", frame[2:4])[0])
            elif marker == 127:
                self.assertEqual(size, struct.unpack(">Q", frame[2:10])[0])

    def test_a_control_frame_payload_over_125_bytes_is_refused(self):
        with self.assertRaises(FramingError):
            encode_ping(b"x" * 126)
        with self.assertRaises(FramingError):
            encode_close_frame(1000, b"r" * 124)

    def test_close_codes_off_the_wire_or_out_of_range_are_refused(self):
        for code in (999, 1005, 1006, 1015, 5000):
            with self.assertRaises(FramingError):
                encode_close_frame(code)
        self.assertEqual(0x88, encode_close_frame(1000, b"bye")[0])
        self.assertEqual(0x88, encode_close_frame(4999)[0])


class ParseFramesTests(unittest.TestCase):
    def test_a_complete_text_message_round_trips(self):
        events, rest, state = parse_frames(client_frame(0x1, b"hello"), FrameState())
        self.assertEqual([("message", b"hello")], events)
        self.assertEqual(b"", rest)
        self.assertIsNone(state.fragmented_opcode)

    def test_a_partial_frame_is_held_for_the_next_read(self):
        whole = client_frame(0x1, b"hello")
        state = FrameState()
        events, rest, state = parse_frames(whole[:4], state)
        self.assertEqual([], events)
        self.assertEqual(whole[:4], rest)
        events, rest, state = parse_frames(rest + whole[4:], state)
        self.assertEqual([("message", b"hello")], events)
        self.assertEqual(b"", rest)

    def test_an_incomplete_extended_length_header_waits(self):
        long = client_frame(0x2, b"z" * 300)
        state = FrameState()
        events, rest, _ = parse_frames(long[:3], state)   # 16-bit form, header cut
        self.assertEqual([], events)
        events, rest, _ = parse_frames(rest + long[3:], state)
        self.assertEqual([("message", b"z" * 300)], events)
        huge = client_frame(0x2, b"z" * 70000)
        state = FrameState()
        events, rest, _ = parse_frames(huge[:5], state)   # 64-bit form, header cut
        self.assertEqual([], events)
        events, rest, _ = parse_frames(rest + huge[5:], state)
        self.assertEqual([("message", b"z" * 70000)], events)

    def test_control_frames_report_their_payload(self):
        events, _, _ = parse_frames(client_frame(0x9, b"hi") + client_frame(0xA, b"yo"),
                                    FrameState())
        self.assertEqual([("ping", b"hi"), ("pong", b"yo")], events)

    def test_reserved_bits_and_a_masked_server_frame_fail_closed(self):
        events, _, _ = parse_frames(client_frame(0x1, b"x", rsv=0x40), FrameState())
        self.assertEqual([("error", "reserved bits set")], events)
        events, _, _ = parse_frames(bytes([0x81, 0x80 | 1]) + b"\x00abcd", FrameState())
        self.assertEqual([("error", "masked server frame")], events)

    def test_non_minimal_and_oversized_length_encodings_fail_closed(self):
        events, _, _ = parse_frames(bytes([0x81, 126]) + struct.pack(">H", 5) + b"hello", FrameState())
        self.assertEqual([("error", "non-minimal length encoding")], events)
        events, _, _ = parse_frames(bytes([0x81, 127]) + struct.pack(">Q", 5) + b"hello", FrameState())
        self.assertEqual([("error", "non-minimal length encoding")], events)
        events, _, _ = parse_frames(bytes([0x81, 127]) + struct.pack(">Q", 1 << 63) + b"x", FrameState())
        self.assertEqual([("error", "64-bit length with the high bit set")], events)

    def test_a_fragmented_or_oversized_control_frame_fails_closed(self):
        events, _, _ = parse_frames(client_frame(0x9, b"x", fin=False), FrameState())
        self.assertEqual([("error", "malformed control frame")], events)
        events, _, _ = parse_frames(client_frame(0x8, b"x" * 126), FrameState())
        self.assertEqual([("error", "malformed control frame")], events)

    def test_an_unknown_opcode_fails_closed(self):
        events, _, _ = parse_frames(client_frame(0x3, b"x"), FrameState())
        self.assertEqual([("error", "unknown opcode 0x3")], events)

    def test_a_text_message_with_invalid_utf8_fails_closed(self):
        events, _, _ = parse_frames(client_frame(0x1, b"\xff\xfe"), FrameState())
        self.assertEqual([("error", "invalid UTF-8 in a text message")], events)

    def test_a_codepoint_split_across_fragments_is_legal(self):
        state = FrameState()
        encoded = "é".encode()
        events, _, state = parse_frames(client_frame(0x1, encoded[:1], fin=False), state)
        self.assertEqual([], events)
        events, _, state = parse_frames(client_frame(0x0, encoded[1:]), state)
        self.assertEqual([("message", "é".encode())], events)
        self.assertIsNone(state.decoder)

    def test_fragments_reassemble_and_control_frames_may_interleave(self):
        state = FrameState()
        events, _, state = parse_frames(client_frame(0x2, b"one", fin=False), state)
        self.assertEqual([0x2], [state.fragmented_opcode])
        events, _, state = parse_frames(client_frame(0x9, b"mid") + client_frame(0x0, b"two"), state)
        self.assertEqual([("ping", b"mid"), ("message", b"onetwo")], events)

    def test_a_new_data_frame_mid_fragment_fails_closed(self):
        state = FrameState()
        parse_frames(client_frame(0x1, b"a", fin=False), state)
        events, _, _ = parse_frames(client_frame(0x1, b"b"), state)
        self.assertEqual([("error", "new data frame during a fragmented message")], events)

    def test_a_continuation_without_a_start_fails_closed(self):
        events, _, _ = parse_frames(client_frame(0x0, b"b"), FrameState())
        self.assertEqual([("error", "continuation without a started message")], events)

    def test_a_fragmented_message_beyond_the_cap_fails_closed_and_resets(self):
        state = FrameState(max_bytes=8)
        events, _, state = parse_frames(client_frame(0x1, b"123456", fin=False), state)
        self.assertEqual([], events)
        events, _, state = parse_frames(client_frame(0x0, b"789", fin=True), state)
        self.assertEqual([("error", "message exceeds the size cap")], events)
        self.assertIsNone(state.fragmented_opcode, "the bomb must not leave a half message behind")

    def test_a_single_frame_beyond_the_cap_fails_closed(self):
        state = FrameState(max_bytes=4)
        events, _, _ = parse_frames(client_frame(0x1, b"12345", fin=False), state)
        self.assertEqual([("error", "message exceeds the size cap")], events)

    def test_a_final_fragment_with_invalid_utf8_fails_closed(self):
        state = FrameState()
        parse_frames(client_frame(0x1, b"ok", fin=False), state)
        events, _, state = parse_frames(client_frame(0x0, b"\xff"), state)
        self.assertEqual([("error", "invalid UTF-8 in a text message")], events)
        self.assertIsNone(state.decoder)

    def test_the_default_cap_is_one_mebibyte(self):
        self.assertEqual(1_048_576, MAX_MESSAGE_BYTES)
        self.assertEqual(MAX_MESSAGE_BYTES, FrameState().max_bytes)


class CloseFrameTests(unittest.TestCase):
    def test_a_close_frame_yields_code_and_reason(self):
        events, rest, _ = parse_frames(client_frame(0x8, struct.pack(">H", 1000) + b"bye"),
                                       FrameState())
        self.assertEqual([("close", 1000, "bye")], events)
        self.assertEqual(b"", rest)

    def test_a_bare_close_frame_reports_no_status(self):
        events, _, _ = parse_frames(client_frame(0x8), FrameState())
        self.assertEqual([("close", 1005, "")], events)

    def test_a_one_byte_close_payload_fails_closed(self):
        events, _, _ = parse_frames(client_frame(0x8, b"\x03"), FrameState())
        self.assertEqual([("error", "close frame with a 1-byte payload")], events)

    def test_on_wire_and_out_of_range_close_codes_fail_closed(self):
        for code in (1005, 1006, 1015, 999, 3000):
            events, _, _ = parse_frames(client_frame(0x8, struct.pack(">H", code)), FrameState())
            self.assertEqual(1, len(events))
            self.assertEqual("error", events[0][0])
            self.assertIn(str(code), events[0][1])

    def test_a_close_reason_that_is_not_utf8_fails_closed(self):
        events, _, _ = parse_frames(client_frame(0x8, struct.pack(">H", 1000) + b"\xff"), FrameState())
        self.assertEqual([("error", "invalid UTF-8 in the close reason")], events)


# --------------------------------------------------------------------------
# ToolheadPolicy — scripts, gates and the jog queue
# --------------------------------------------------------------------------


class ToolheadScriptTests(unittest.TestCase):
    def test_a_jog_sandwiches_a_relative_move_and_restores_absolute(self):
        self.assertEqual("G91\nG1 X25 F3000\nG90", jog_script("X", JOG_DISTANCE_DEFAULT))
        self.assertEqual("G91\nG1 Z1 F600", jog_script("z", 1.0, absolute_coordinates=False))
        self.assertEqual("G91\nG1 Y-5 F3000\nG90", jog_script(" Y ", -5.0))

    def test_a_jog_outside_the_bounds_or_off_axis_is_refused(self):
        with self.assertRaises(ValueError):
            jog_script("e", 5.0)
        for distance in (0.0, JOG_DISTANCE_MIN / 2, JOG_DISTANCE_MAX + 1, float("nan")):
            with self.assertRaises(ValueError):
                jog_script("x", distance)

    def test_home_scripts_cover_all_axes_and_one(self):
        self.assertEqual("G28", home_script())
        self.assertEqual("G28 X", home_script(" x "))
        with self.assertRaises(ValueError):
            home_script("e")
        self.assertEqual("M18", motors_off_script())

    def test_absolute_moves_wrap_to_leave_a_relative_printer_relative(self):
        self.assertEqual("G1 X5 Y6 Z50 F3000", center_script(5, 6))
        self.assertEqual("G90\nG1 X5 Y6 Z50 F3000\nG91", center_script(5, 6, absolute_coordinates=False))
        self.assertEqual("G90\nG1 Z0 F600\nG91", z0_script(absolute_coordinates=False))
        self.assertEqual("G1 Z0 F600", z0_script())
        self.assertIn("Z%g" % CENTER_Z_MM, center_script(1, 1))

    def test_extrusion_scripts_cover_both_modes_and_bounds(self):
        self.assertEqual("G91\nG1 E5 F300\nG90", extrude_script(5.0))
        self.assertEqual("G91\nG1 E-5 F1500", extrude_script(-5.0, speed_mm_per_min=1500,
                                                            absolute_coordinates=False))
        with self.assertRaises(ValueError):
            extrude_script(0.05)
        with self.assertRaises(ValueError):
            extrude_script(5.0, speed_mm_per_min=EXTRUDE_SPEED_MAX + 1)

    def test_axis_and_range_validators_reject_junk(self):
        self.assertTrue(axis_ok(" Z "))
        self.assertFalse(axis_ok("e"))
        self.assertFalse(axis_ok(None))
        self.assertTrue(jog_distance_ok(-JOG_DISTANCE_MAX))
        self.assertFalse(jog_distance_ok("far"))
        self.assertFalse(jog_distance_ok(None))
        self.assertTrue(extrude_distance_ok(100.0))
        self.assertFalse(extrude_distance_ok(""))
        self.assertTrue(extrude_speed_ok(30))
        self.assertFalse(extrude_speed_ok("fast"))
        self.assertFalse(extrude_speed_ok(EXTRUDE_SPEED_MAX + 0.5))

    def test_position_mode_text_names_the_mode(self):
        self.assertEqual("Absolute", position_mode_text(True))
        self.assertEqual("Relative", position_mode_text(0))

    def test_the_jog_gate_unlocks_terminated_states_only(self):
        self.assertEqual("pause-first", jog_gate(" PRINTING "))
        for state in ("standby", "paused", "complete", "cancelled", "error"):
            self.assertEqual("allowed", jog_gate(state))
        for state in ("", None, "homing", "offline"):
            self.assertEqual("disabled", jog_gate(state))


class ClampRelativeMoveTests(unittest.TestCase):
    def test_a_move_into_negative_territory_is_forbidden_entirely(self):
        self.assertEqual(0.0, clamp_relative_move(-5.0, 2.0, minimum=0.0))
        self.assertEqual(0.0, clamp_relative_move(-1.0, 0.5), "unknown floor still floors at zero")
        self.assertEqual(0.0, clamp_relative_move(0.0, 12.0))

    def test_a_move_past_the_maximum_lands_on_the_boundary(self):
        self.assertEqual(3.0, clamp_relative_move(10.0, 7.0, maximum=10.0))
        self.assertEqual(0.0, clamp_relative_move(10.0, 12.0, maximum=10.0), "already past: no move")

    def test_a_move_inside_the_range_passes_through_unchanged(self):
        self.assertEqual(2.5, clamp_relative_move(2.5, 1.0, minimum=0.0, maximum=10.0))
        self.assertEqual(-2.5, clamp_relative_move(-2.5, 9.0, minimum=0.0, maximum=10.0))

    def test_an_inverted_range_is_decided_by_the_floor(self):
        # minimum > maximum never satisfies both clamps at once, so the
        # inverted-range guard at the end of the function cannot return; the
        # floor check decides first and the move is refused.
        self.assertEqual(0.0, clamp_relative_move(1.0, 5.0, minimum=10.0, maximum=None))
        self.assertEqual(0.0, clamp_relative_move(1.0, 5.0, minimum=10.0, maximum=3.0))


class JogQueueTests(unittest.TestCase):
    def test_operation_builders_pre_render_their_scripts(self):
        jog = make_jog_op("Z", -1.0, True)
        self.assertEqual(("jog", "z", -1.0, "Jog Z", "G91\nG1 Z-1 F600\nG90"),
                         (jog.kind, jog.axis, jog.distance, jog.label, jog.script))
        self.assertEqual("Home all", make_home_op().label)
        self.assertEqual("G28 Y", make_home_op("y").script)
        self.assertEqual("Home Y", make_home_op("y").label)
        self.assertEqual(("motors-off", "Motors off", "M18"),
                         (make_motors_off_op().kind, make_motors_off_op().label,
                          make_motors_off_op().script))
        center = make_center_op(5.0, 6.0, False)
        self.assertEqual(("center", CENTER_Z_MM, "G90\nG1 X5 Y6 Z50 F3000\nG91"),
                         (center.kind, center.distance, center.script))
        self.assertEqual(("z0", "Z to 0", "G1 Z0 F600"),
                         (make_z0_op(True).kind, make_z0_op(True).label, make_z0_op(True).script))
        self.assertEqual("Retract", make_extrude_op(-5.0, 300, True).label)
        extrude = make_extrude_op(5.0, 300, True)
        self.assertEqual(("extrude", "e", 5.0, 300.0, "Extrude"),
                         (extrude.kind, extrude.axis, extrude.distance, extrude.speed, extrude.label))

    def test_operation_builders_refuse_a_bad_axis(self):
        with self.assertRaises(ValueError):
            make_jog_op("e", 5.0, True)
        with self.assertRaises(ValueError):
            make_home_op("e")

    def test_the_queue_rejects_the_newest_tap_at_the_depth_cap(self):
        op = make_home_op()
        pending = ()
        for _ in range(MAX_PENDING_OPS):
            pending, why = push_op(pending, op)
            self.assertIsNone(why)
        self.assertEqual(MAX_PENDING_OPS, len(pending))
        kept, why = push_op(pending, op)
        self.assertEqual(STATUS_QUEUE_FULL, why)
        self.assertEqual(pending, kept, "already-queued intent is kept")

    def test_equal_and_opposite_moves_are_never_coalesced(self):
        merged, _ = push_op((make_extrude_op(-5.0, 300, True),), make_extrude_op(-5.0, 300, True))
        self.assertEqual(2, len(merged))


# --------------------------------------------------------------------------
# PrintState.LayerResolver — the physical-layer estimator
# --------------------------------------------------------------------------


def layer_config(**overrides):
    base = dict(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.02)
    base.update(overrides)
    return SimpleNamespace(**base)


def z_status(z, e, *, progress=0.5, current_layer=None, total_layer=None, state="printing",
             objects=True):
    """A status carrying a G-code position pair; `objects=False` drops the mappings."""
    if not objects:
        return {"print_stats": [], "virtual_sdcard": [], "gcode_move": []}
    info = {}
    if current_layer is not None: info["current_layer"] = current_layer
    if total_layer is not None: info["total_layer"] = total_layer
    sdcard = {} if progress is None else {"progress": progress}
    return {"print_stats": {"state": state, "info": info},
            "virtual_sdcard": sdcard,
            "gcode_move": {"gcode_position": [0.0, 0.0, z, e]}}


class LayerResolverBasicsTests(unittest.TestCase):
    def test_the_layer_total_prefers_the_index_then_the_object_then_metadata(self):
        resolver = LayerResolver()
        index = FakeIndex(layers=7)
        self.assertEqual(7, resolver.resolve({}, layer_config(), index=index).total)
        self.assertEqual(12, resolver.resolve(z_status(1, 1, total_layer=12), layer_config()).total)
        self.assertEqual(9, resolver.resolve({}, layer_config(), metadata={"layer_count": 9}).total)
        self.assertEqual(4, resolver.resolve({}, layer_config(), heights=(0.2, 0.4, 0.6, 0.8)).total)
        self.assertIsNone(resolver.resolve({}, layer_config()).total)
        # A nonsensical total falls through to the next source, never clamping.
        self.assertEqual(9, resolver.resolve(z_status(1, 1, total_layer=0), layer_config(),
                                             metadata={"layer_count": 9}).total)

    def test_current_layer_is_mapped_then_one_based_adjusted(self):
        resolver = LayerResolver()
        resolver.resolve(z_status(1, 1, current_layer=5, progress=None), layer_config())
        mapped = resolver.resolve(z_status(1, 1, current_layer=5, progress=None), layer_config(),
                                  index=FakeIndex(mapping={5: 2}))
        self.assertEqual((2, "G-code mapped current_layer"), (mapped.index, mapped.source))
        self.assertEqual((4, "Moonraker current_layer"),
                         (LayerResolver().resolve(z_status(1, 1, current_layer=5, progress=None),
                                                  layer_config()).index, "Moonraker current_layer"))
        zero_based = LayerResolver().resolve(z_status(1, 1, current_layer=5, progress=None),
                                             layer_config(moonraker_layer_is_one_based=False))
        self.assertEqual(5, zero_based.index)

    def test_a_pre_print_zero_layer_falls_through_to_the_file_position(self):
        resolver = LayerResolver()
        index = FakeIndex(at={500: 3})
        status = z_status(1, 1, current_layer=0, progress=None)
        status["virtual_sdcard"] = {"file_position": 500}
        self.assertEqual(("Moonraker current_layer",), (resolver.resolve(status, layer_config(moonraker_layer_is_one_based=False)).source,))
        self.assertEqual((3, "G-code file position"),
                         (resolver.resolve(status, layer_config(), index=index).index, "G-code file position"))

    def test_a_non_mapping_status_is_tolerated(self):
        layer = LayerResolver().resolve(z_status(0, 0, objects=False), layer_config())
        self.assertIsNone(layer.index)
        self.assertIsNone(layer.total)

    def test_a_non_finite_or_overflowing_number_reads_as_absent(self):
        resolver = LayerResolver()
        self.assertIsNone(resolver.resolve(z_status(1, 1, total_layer=float("inf")),
                                           layer_config()).total)
        self.assertIsNone(resolver._number("abc"))
        self.assertIsNone(resolver._number(10 ** 400))
        self.assertIsNone(resolver._number(None, int))


class LayerResolverZEstimateTests(unittest.TestCase):
    """The extrusion-guarded Z estimator: seeds, plateau commits and its retires."""

    def drive(self, samples, *, config=None, metadata=None, index=None, heights=(),
              progress=0.5):
        resolver = LayerResolver()
        config = config or layer_config()
        layers = [resolver.resolve(z_status(z, e, progress=progress), config, index=index,
                                   metadata=metadata, heights=heights)
                  for z, e in samples]
        return resolver, layers

    def test_the_first_increment_seeds_a_layer_and_the_baseline_records_no_extrusion(self):
        _, layers = self.drive([(0.2, 0.0), (0.4, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertIsNone(layers[0].index, "the baseline observation seeds nothing")
        self.assertEqual((1, "extrusion-guarded Z height"), (layers[-1].index, layers[-1].source))
        self.assertEqual(0.2, layers[-1].thickness)

    def test_a_progress_of_zero_blocks_the_estimate_before_the_file_starts(self):
        _, layers = self.drive([(0.2, 0.0), (0.4, 1.0)], progress=0.0,
                               metadata={"layer_height": 0.2})
        self.assertIsNone(layers[-1].index)

    def test_a_continuing_rise_re_anchors_the_provisional_guess(self):
        samples = [(0.2, 0.0), (0.4, 0.1), (0.5, 0.1), (0.6, 0.1), (0.7, 0.1), (0.8, 0.1),
                   (0.9, 0.2)]
        _, layers = self.drive(samples, metadata={"layer_height": 0.2,
                                                  "first_layer_height": 0.2})
        self.assertEqual(3, layers[-1].index, "the re-anchored step is the declared layer height")

    def test_a_rise_without_a_declared_step_retires_the_guess(self):
        samples = [(0.2, 0.0), (0.4, 0.1), (0.5, 0.1), (0.6, 0.1), (0.7, 0.1), (0.8, 0.1),
                   (0.9, 0.2)]
        _, layers = self.drive(samples, metadata={})
        self.assertIsNone(layers[-1].index, "an inflated layer number is worse than none")

    def test_a_rise_re_anchors_on_the_measured_median_without_a_header(self):
        # One plateau measures a 0.05 step; the sixth rise then trips the
        # re-anchor, which re-seeds from that measured median.
        samples = [(0.2, 0.0), (0.25, 1.0), (0.25, 1.0)]
        samples += [(0.30, 1.0), (0.35, 1.0), (0.40, 1.0), (0.45, 1.0), (0.50, 1.0)]
        samples += [(0.55, 1.1)]
        _, layers = self.drive(samples, metadata={})
        self.assertEqual(10, layers[-1].index)

    def test_a_poll_too_small_to_seed_still_enters_the_estimate(self):
        # A rise under the 0.02 provisional-seed floor: the estimate's own
        # mid-print seed rule decides instead, so the layer is not lost.
        _, layers = self.drive([(0.2, 0.0), (0.205, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual((0, 0.2), (layers[-1].index, layers[-1].height))

    def test_an_early_tiny_rise_seeds_only_near_the_bed(self):
        # Before ~3% progress a quiet rise is start-gcode creep, but a height
        # still inside the first couple of layers is honestly layer zero.
        _, layers = self.drive([(0.2, 0.0), (0.205, 1.0)], progress=0.01,
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(0, layers[-1].index)

    def test_a_z_hop_descent_cancels_the_provisional_seed(self):
        _, layers = self.drive([(0.2, 0.0), (0.4, 1.0), (0.2, 1.0)],
                               metadata={"layer_height": 0.2})
        self.assertIsNone(layers[-1].index, "the hop was not a layer change")

    def test_a_descent_that_keeps_ascent_does_not_read_as_a_hop(self):
        _, layers = self.drive([(0.0, 0.0), (1.0, 1.0), (1.05, 1.0), (0.5, 1.0), (0.1, 1.0)],
                               metadata={"layer_height": 0.2})
        self.assertIsNotNone(layers[-1].index, "the ascent never cancelled out")

    def test_plateaus_record_measured_layer_deltas_and_bound_their_history(self):
        resolver = LayerResolver()
        config = layer_config()
        resolver.resolve(z_status(0.0, 0.0), config, metadata={})
        resolver.resolve(z_status(0.2, 1.0), config, metadata={})
        for step in range(30):
            resolver.resolve(z_status(0.2 + step * 0.2, 1.0), config, metadata={})
            resolver.resolve(z_status(0.2 + step * 0.2 + 0.002, 1.0), config, metadata={})
        self.assertTrue(resolver._z_deltas)
        self.assertLessEqual(len(resolver._z_deltas), 24, "the delta history is bounded")

    def test_late_progress_seeds_the_estimate_from_the_current_height(self):
        _, layers = self.drive([(0.2, 0.0), (5.0, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(24, layers[-1].index)

    def test_an_early_wipe_height_is_not_taken_for_a_layer(self):
        _, layers = self.drive([(0.2, 0.0), (10.0, 1.0)], progress=0.01,
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertIsNone(layers[-1].index)

    def test_the_extrapolation_advances_one_layer_and_never_jumps(self):
        _, layers = self.drive([(0.4, 1.0), (0.6, 2.0), (20.0, 3.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(2, layers[1].index)
        self.assertEqual(3, layers[-1].index, "a lift must not jump the physical layer")

    def test_a_lower_candidate_corrects_only_after_three_observations(self):
        samples = [(0.4, 1.0), (0.6, 2.0), (0.6, 3.0)]
        samples += [(2.0, 4.0), (2.0, 5.0), (2.0, 6.0), (2.0, 7.0), (2.0, 8.0), (2.0, 9.0),
                    (2.0, 10.0)]
        _, layers = self.drive(samples, metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(9, layers[-1].index, "the estimate climbed one layer per poll")
        _, corrected = self.drive(samples + [(1.0, 11.0), (1.0, 12.0), (1.0, 13.0)],
                                  metadata={"layer_height": 0.2, "first_layer_height": 0.2})
        self.assertEqual(9, corrected[-3].index, "one correction must not move the layer")
        self.assertEqual(4, corrected[-1].index, "three consecutive corrections settle it")

    def test_an_object_height_bounds_the_estimate(self):
        _, layers = self.drive([(0.2, 0.0), (9.0, 1.0)],
                               metadata={"layer_height": 0.2, "first_layer_height": 0.2,
                                         "object_height": 1.0})
        self.assertEqual(5, layers[-1].index)

    def test_a_measured_median_step_replaces_an_absent_header(self):
        resolver = LayerResolver()
        config = layer_config()
        resolver.resolve(z_status(0.0, 0.0), config, metadata={})
        # Two plateaus measure a 0.2 step; the next resolve consults the median.
        for z in (0.2, 0.4):
            resolver.resolve(z_status(z, 1.0), config, metadata={})
            resolver.resolve(z_status(z + 0.002, 1.0), config, metadata={})
        layer = resolver.resolve(z_status(1.0, 2.0), config, metadata={})
        self.assertIsNotNone(layer.index)


class LayerResolverGeometryTests(unittest.TestCase):
    HEIGHTS = (0.2, 0.4, 0.6, 0.8, 1.0)
    METADATA = {"layer_height": 0.2, "first_layer_height": 0.2}

    def resolve_at(self, z, *, heights, metadata=None, config=None):
        # The geometry paths live inside the extrusion-guarded estimate, so
        # every probe needs a baseline poll before the Z sample.
        resolver = LayerResolver()
        config = config or layer_config()
        resolver.resolve(z_status(max(0.2, z - 0.2), 0.0, progress=0.5), config,
                         metadata=metadata or self.METADATA, heights=heights)
        return resolver.resolve(z_status(z, 1.0, progress=0.5), config,
                                metadata=metadata or self.METADATA, heights=heights)

    def test_heights_pin_the_layer_at_an_exact_match(self):
        layer = self.resolve_at(0.6, heights=self.HEIGHTS)
        self.assertEqual(2, layer.index)
        self.assertEqual(0.6, layer.height)
        self.assertAlmostEqual(0.2, layer.thickness)

    def test_a_mid_layer_position_maps_to_the_last_start_below_it(self):
        layer = self.resolve_at(0.55, heights=self.HEIGHTS)
        self.assertEqual(1, layer.index)
        self.assertEqual(0.4, layer.height)

    def test_a_position_past_the_last_start_reads_as_the_top_layer(self):
        layer = self.resolve_at(1.01, heights=self.HEIGHTS)
        self.assertEqual(4, layer.index)
        self.assertEqual(1.0, layer.height)
        self.assertAlmostEqual(0.2, layer.thickness)

    def test_geometry_from_another_scene_does_not_clamp_the_observation(self):
        layer = self.resolve_at(5.0, heights=(0.1, 0.11, 0.12))
        self.assertIsNotNone(layer.index, "the heights were not this file's")

    def test_the_layer_zero_thickness_uses_the_first_layer_height(self):
        layer = self.resolve_at(0.3, heights=(),
                                metadata={"layer_height": 0.2, "first_layer_height": 0.3})
        self.assertEqual(0, layer.index)
        self.assertEqual(0.3, layer.height)
        self.assertEqual(0.3, layer.thickness)

    def test_a_derived_height_uses_the_resolved_step_without_geometry(self):
        layer = self.resolve_at(0.6, heights=())
        self.assertAlmostEqual(0.6, layer.height)
        self.assertEqual(0.2, layer.thickness)


class LayerResolverStartTests(unittest.TestCase):
    def test_an_active_print_at_layer_zero_reports_the_start(self):
        status = z_status(0.2, 0.0, current_layer=0, progress=0.01)
        layer = LayerResolver().resolve(status, layer_config())
        self.assertEqual((0, "print start"), (layer.index, layer.source))

    def test_past_three_percent_the_same_signal_is_not_a_stuck_layer_one(self):
        status = z_status(0.2, 0.0, current_layer=0, progress=0.5)
        self.assertIsNone(LayerResolver().resolve(status, layer_config()).index)

    def test_a_standby_printer_at_layer_zero_gets_no_layer(self):
        status = z_status(0.2, 0.0, current_layer=0, progress=0.01, state="standby")
        self.assertIsNone(LayerResolver().resolve(status, layer_config()).index)

    def test_a_negative_layer_is_floored(self):
        status = z_status(0.2, 0.0, current_layer=-3, progress=None)
        layer = LayerResolver().resolve(status, layer_config(moonraker_layer_is_one_based=False))
        self.assertEqual(0, layer.index)

    def test_a_zero_based_scene_reads_layer_zero_as_reported(self):
        status = z_status(0.2, 0.0, current_layer=0, progress=0.01)
        layer = LayerResolver().resolve(status, layer_config(moonraker_layer_is_one_based=False))
        self.assertEqual((0, "Moonraker current_layer"), (layer.index, layer.source))

    def test_reset_clears_the_z_estimate(self):
        resolver = LayerResolver()
        config = layer_config()
        resolver.resolve(z_status(0.2, 1.0, progress=0.5), config, metadata={"layer_height": 0.2})
        resolver.reset()
        self.assertIsNone(resolver._prev_z)
        self.assertEqual([], resolver._z_deltas)


class PrintSnapshotTests(unittest.TestCase):
    def test_active_covers_printing_and_paused_only(self):
        for state, expected in (("printing", True), ("paused", True), ("standby", False),
                                ("cancelled", False)):
            snap = PrintSnapshot(observation=SimpleNamespace(state=state))
            self.assertEqual(expected, snap.active, state)
        self.assertFalse(PrintSnapshot().active)

    def test_a_snapshot_is_frozen_with_defaults(self):
        snap = replace(PrintSnapshot(), layer_eta=12.0, index_ready=True)
        self.assertEqual(PhysicalLayer(), snap.layer)
        self.assertEqual((12.0, True), (snap.layer_eta, snap.index_ready))


# --------------------------------------------------------------------------
# PreviewFollower — policy, tracking and the ETA projections
# --------------------------------------------------------------------------


class DeferredView(FakeView):
    """A view whose writes land an observation late (Cura's hung restore)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pending = []

    def setLayer(self, value): self.pending.append(("setLayer", int(value)))
    def setMinimumLayer(self, value): self.pending.append(("setMinimumLayer", int(value)))

    def commit(self):
        for name, value in self.pending:
            getattr(FakeView, name)(self, value)
        self.pending = []


class PathBlockedView(FakeView):
    """A view with no path API: hasattr() must see it as tracking-unavailable."""

    @property
    def setPath(self):
        raise AttributeError("setPath")


def follower_of(view=None, **port_overrides):
    port = FakeCura(view)
    for name, value in port_overrides.items():
        setattr(port, name, value)
    return PreviewFollower(port), port


class OverrideKindTests(unittest.TestCase):
    def test_an_unarmed_follower_cannot_infer_intent(self):
        self.assertIsNone(preview_override_kind(expected_layer=None, current_layer=3))

    def test_either_layer_handle_moving_is_a_layer_override(self):
        self.assertEqual("layer", preview_override_kind(expected_layer=3, current_layer=4))
        self.assertEqual("layer", preview_override_kind(expected_layer=3, current_layer=3,
                                                        expected_minimum_layer=1, current_minimum_layer=2))
        self.assertIsNone(preview_override_kind(expected_layer=3, current_layer=3,
                                                expected_minimum_layer=1, current_minimum_layer=None),
                          "an unreadable minimum cannot attest a change")

    def test_a_path_handle_move_outside_the_tolerance_is_a_path_override(self):
        self.assertEqual("path", preview_override_kind(expected_layer=3, current_layer=3,
                                                       expected_path=10.0, current_path=11.0))
        self.assertIsNone(preview_override_kind(expected_layer=3, current_layer=3,
                                                expected_path=10.0, current_path=10.5))
        self.assertEqual("path", preview_override_kind(expected_layer=3, current_layer=3,
                                                       expected_path=10.0, current_path=10.75))
        self.assertEqual("path", preview_override_kind(expected_layer=3, current_layer=3,
                                                       expected_minimum_path=2, current_minimum_path=3))

    def test_matching_handles_report_no_override(self):
        self.assertIsNone(preview_override_kind(
            expected_layer=3, current_layer=3, expected_minimum_layer=1, current_minimum_layer=1,
            expected_path=5.0, current_path=5.2, expected_minimum_path=1, current_minimum_path=1))


class FollowerDetachTests(unittest.TestCase):
    def test_a_manual_layer_move_detaches_the_follower(self):
        view = FakeView(layer=3)
        follower, _ = follower_of(view)
        follower.remember()
        view.layer = 4
        self.assertEqual("layer", follower.detect_override())
        self.assertFalse(follower.state.attached)

    def test_a_manual_path_scroll_detaches_the_follower(self):
        view = FakeView(layer=3, path=10.0, minimum_path=2)
        follower, _ = follower_of(view)
        follower.remember()
        view.path = 20.0
        self.assertEqual("path", follower.detect_override())
        self.assertFalse(follower.state.attached)

    def test_a_still_view_reports_no_override(self):
        follower, _ = follower_of(FakeView(layer=3))
        follower.remember()
        self.assertIsNone(follower.detect_override())

    def test_a_detached_suspended_or_viewless_follower_never_reads_intent(self):
        follower, port = follower_of(FakeView(layer=3))
        follower.remember()
        follower.attach(False)
        self.assertIsNone(follower.detect_override())
        follower.attach(True)
        port.suspended = True
        self.assertIsNone(follower.detect_override())
        port.suspended = False
        port.view = None
        self.assertIsNone(follower.detect_override())

    def test_an_unreadable_layer_handle_reads_as_no_change(self):
        follower, port = follower_of(FakeView(layer=3))
        follower.remember()

        def explode():
            raise RuntimeError("view torn down")
        port.view.getCurrentLayer = explode
        self.assertIsNone(follower.detect_override())

    def test_the_first_change_after_an_unarmed_window_detaches(self):
        follower, _ = follower_of(FakeView(layer=3))
        self.assertIsNone(follower.detect_override(), "unarmed: adopt the view's position")
        self.assertEqual(3, follower.state.expected_layer, "the baseline was adopted")
        follower._cura.view.layer = 5
        self.assertEqual("layer", follower.detect_override())


class FollowerViewTests(unittest.TestCase):
    def test_attach_records_the_view_and_a_detach_drops_the_motion(self):
        view = FakeView(layer=2, minimum=1, path=3.0, minimum_path=1)
        follower, _ = follower_of(view)
        motion = MotionTrace()
        follower.bind_motion(motion)
        follower.attach()
        self.assertEqual((2, 1, 3.0), (follower.state.expected_layer, follower.state.expected_minimum,
                                       follower.state.expected_path))
        follower.attach(False)
        self.assertFalse(follower.state.attached)
        self.assertEqual(1, motion.reset_calls, "a detach drops the glide")

    def test_invalidate_view_clears_only_the_view_handles(self):
        follower, _ = follower_of(FakeView(layer=2))
        follower.remember()
        follower.invalidate_view()
        self.assertIsNone(follower.state.expected_layer)
        self.assertIsNone(follower.state.expected_minimum)
        self.assertTrue(follower.state.attached)

    def test_reset_print_keeps_the_armed_baseline(self):
        follower, _ = follower_of(FakeView(layer=2))
        follower.remember()
        follower.reset_print()
        self.assertEqual(2, follower.state.expected_layer, "the view survives a print-end reset")

    def test_reset_tracking_clears_the_estimate_not_the_baseline(self):
        follower, _ = follower_of(FakeView(layer=2))
        follower.observe(snapshot(4), status(), preview_config(), FakeIndex())
        follower.reset_tracking()
        self.assertIsNone(follower.state.anchor_layer)
        self.assertIsNone(follower.state.path_layer)
        self.assertEqual("", follower.state.eta_text)
        self.assertEqual(4, follower.state.expected_layer, "the armed baseline survives")


class FollowerObserveTests(unittest.TestCase):
    def setUp(self):
        self.view = FakeView(layer=3)
        self.follower, self.port = follower_of(self.view)

    def observe(self, snap=None, st=None, config=None, index=None):
        return self.follower.observe(snap or snapshot(3), st or status(),
                                     config or preview_config(), index or FakeIndex())

    def test_an_inactive_snapshot_reports_connected_and_resets_the_print(self):
        self.follower.remember()
        self.assertEqual(("Connected", ()), self.observe(snapshot(3, active=False)))
        self.assertIsNone(self.follower.state.observed_layer)
        self.assertEqual(3, self.follower.state.expected_layer)

    def test_a_disabled_follower_observes_but_drives_nothing(self):
        self.assertEqual(("Print active", ()), self.observe(config=preview_config(enabled=False)))
        self.assertEqual([], self.view.paths)
        self.assertEqual(0, self.port.writes)

    def test_a_detached_follower_reports_detached(self):
        self.follower.attach(False)
        self.assertEqual(("Detached", ()), self.observe())

    def test_a_busy_cura_is_reported_rather_than_driven(self):
        self.port.suspended = True
        self.assertEqual(("Cura busy", ()), self.observe())
        self.assertEqual([], self.view.paths)

    def test_a_missing_view_or_toolpath_falls_back_to_print_active(self):
        self.port.view = None
        self.assertEqual(("Print active", ()), self.observe())
        self.port.view = FakeView(layer=3)
        self.port.has_toolpath = False
        self.assertEqual(("Print active", ()), self.observe())

    def test_a_missing_layer_waits_for_layer_data(self):
        self.assertEqual(("Waiting for layer data", ()), self.observe(snapshot(None)))

    def test_missing_cura_layer_data_is_named(self):
        self.port.max_layer = None
        self.assertEqual(("Cura layer data unavailable", ()), self.observe())

    def test_a_non_numeric_speed_and_duration_fall_back(self):
        st = status(print_stats={"print_duration": "soon"}, gcode_move={"speed_factor": "fast"})
        self.observe(st=st)
        self.assertEqual((1.0, None), (self.follower.state.speed, self.follower.state.duration))

    def test_a_zero_speed_factor_reads_as_one_and_a_negative_one_is_floored(self):
        self.observe(st=status(gcode_move={"speed_factor": 0}))
        self.assertEqual(1.0, self.follower.state.speed)
        self.observe(st=status(gcode_move={"speed_factor": -2}))
        self.assertEqual(0.05, self.follower.state.speed)

    def test_the_view_is_driven_and_the_baseline_re_armed(self):
        self.view.layer = 2                     # a view trailing the printer
        self.observe()
        self.assertEqual([("minimum", 0), ("layer", 3)], self.view.writes)
        self.assertEqual(3, self.follower.state.expected_layer)

    def test_an_agreeing_view_is_not_written_again(self):
        self.view.layer, self.view.minimum = 3, 0
        self.observe()
        self.assertEqual([], self.view.writes, "the decision was already satisfied")

    def test_the_auto_preview_switch_fires_once(self):
        config = preview_config(auto_preview=True)
        self.observe(config=config)
        self.assertTrue(self.follower.state.switched)
        self.observe(config=config)
        self.assertEqual(1, self.port.switches)

    def test_a_refused_preview_switch_is_retried(self):
        self.port.switch_result = False
        self.observe(config=preview_config(auto_preview=True))
        self.observe(config=preview_config(auto_preview=True))
        self.assertEqual(2, self.port.switches)
        self.assertFalse(self.follower.state.switched)

    def test_a_deferred_view_write_is_issued_but_not_re_armed(self):
        view = DeferredView(layer=0)
        follower, _ = follower_of(view)
        follower.observe(snapshot(3), status(), preview_config(), FakeIndex())
        self.assertEqual([("setMinimumLayer", 0), ("setLayer", 3)], view.pending)
        self.assertEqual(3, follower.state.observed_layer)

    def test_a_deferred_view_write_keeps_the_previous_baseline(self):
        view = DeferredView(layer=3)
        follower, _ = follower_of(view)
        follower.remember()
        follower.observe(snapshot(5), status(), preview_config(), FakeIndex())
        self.assertEqual(3, follower.state.expected_layer,
                         "a not-yet-applied write must not re-arm the baseline")

    def test_the_nozzle_is_left_alone_without_a_followed_path(self):
        config = preview_config(show_toolhead_indicator=True)
        self.observe(config=config)
        self.assertFalse(self.follower.state.nozzle_valid)
        self.assertEqual(0, self.port.nozzles)

    def test_a_paused_observation_is_named(self):
        detail, _ = self.observe(snapshot(3, state="paused"))
        self.assertEqual("Printer paused", detail)

    def test_the_drift_learner_clamps_and_ignores_an_empty_index(self):
        config = preview_config(eta_learn=True)
        index = FakeIndex(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        # duration/boundary = 600/60 clamps to the 2.0 ceiling
        self.observe(st=status(print_stats={"print_duration": 600.0}), config=config, index=index)
        self.assertEqual(2.0, self.follower.state.drift)
        follower, _ = follower_of(FakeView(layer=3))
        follower.observe(snapshot(3), status(print_stats={"print_duration": 30.0}), config,
                         FakeIndex(elapsed=[]))
        self.assertIsNone(follower.state.drift)
        follower2, _ = follower_of(FakeView(layer=3))
        follower2.observe(snapshot(3), status(print_stats={"print_duration": 30.0}), config,
                          FakeIndex(elapsed=[None, None, None, None, None]))
        self.assertIsNone(follower2.state.drift)

    def test_a_short_first_boundary_is_not_turned_into_a_drift(self):
        config = preview_config(eta_learn=True)
        index = FakeIndex(elapsed=[0.0, 30.0, 60.0, 90.0, 120.0])
        self.observe(st=status(print_stats={"print_duration": 600.0}), config=config, index=index)
        self.assertIsNone(self.follower.state.drift)   # boundary <= 60 s is refused

    def test_the_learned_drift_is_clamped_upwards(self):
        config = preview_config(eta_learn=True)
        index = FakeIndex(elapsed=[0.0, 600.0, 1200.0, 1800.0, 2400.0])
        self.observe(st=status(print_stats={"print_duration": 60.0}), config=config, index=index)
        self.assertEqual(0.5, self.follower.state.drift)

    def test_an_index_without_the_layer_leaves_the_drift_alone(self):
        config = preview_config(eta_learn=True)
        index = FakeIndex(elapsed=[0.0, 600.0, 1200.0], layers=2)   # layer 3 is out of range
        self.observe(st=status(print_stats={"print_duration": 60.0}), config=config, index=index)
        self.assertIsNone(self.follower.state.drift)

    def test_the_duration_shortfall_is_ignored_without_a_duration(self):
        config = preview_config(eta_learn=True)
        self.observe(st=status(print_stats={}), config=config,
                     index=FakeIndex(elapsed=[0.0, 600.0, 1200.0, 1800.0, 2400.0]))
        self.assertIsNone(self.follower.state.drift)


class FollowerPathTests(unittest.TestCase):
    def setUp(self):
        self.view = FakeView(layer=3, maximum=1000)
        self.follower, self.port = follower_of(self.view)

    def run_path(self, *, index=..., st=None, config=None, view=None, motion=None, vsc=...):
        if view is not None:
            self.port.view = view
        if motion is not None:
            self.follower.bind_motion(motion)
        if st is None:
            st = status()
            if vsc is ...:
                st["virtual_sdcard"] = {"file_position": 400}
            elif vsc is not None:
                st["virtual_sdcard"] = vsc
        index = FakeIndex() if index is ... else index
        return self.follower.observe(snapshot(3), st,
                                     config or preview_config(path_follow=True), index)

    def path_detail(self, *, index=..., st=None, view=None, vsc=..., smooth=True):
        """_follow_path directly: observe() reports only its own status word."""
        if view is not None:
            self.port.view = view
        if st is None:
            st = status()
            if vsc is ...:
                st["virtual_sdcard"] = {"file_position": 400}
            elif vsc is not None:
                st["virtual_sdcard"] = vsc
        index = FakeIndex() if index is ... else index
        return self.follower._follow_path(self.port.view, 3, st, index, smooth=smooth)

    def test_a_view_without_the_path_api_reports_unavailable(self):
        self.assertEqual(("Path tracking unavailable", ()),
                         self.path_detail(view=PathBlockedView(layer=3)))

    def test_a_layer_past_the_index_waits(self):
        detail, hydration = self.path_detail(index=FakeIndex(layers=2, elapsed=[0.0, 10.0]))
        self.assertEqual(("Waiting for index", ()), (detail, hydration))
        self.assertEqual([0.0], self.view.paths, "the stale path is parked at the layer start")

    def test_a_missing_index_waits_and_resets_the_motion(self):
        motion = MotionTrace()
        self.follower.bind_motion(motion)
        detail, _ = self.path_detail(index=None)
        self.assertEqual("Waiting for index", detail)
        self.assertEqual(1, motion.reset_calls)

    def test_an_unhydrated_layer_requests_hydration(self):
        detail, hydration = self.path_detail(index=FakeIndex(hydrated=()))
        self.assertEqual(("Hydrating layer", (3,)), (detail, hydration))
        self.assertEqual(0.0, self.follower.state.path_fraction)
        self.assertEqual([0.0], self.view.paths, "a stale target must not fight the hydration")

    def test_an_unhydrated_layer_stops_a_running_animation(self):
        motion = MotionTrace()
        self.follower.bind_motion(motion)
        self.path_detail(index=FakeIndex(hydrated=()))
        self.assertEqual(1, motion.reset_calls)

    def test_a_missing_or_unparsable_file_position_waits(self):
        self.assertEqual("Waiting for file position", self.path_detail(st={"gcode_move": {}})[0])
        self.assertEqual("Waiting for file position",
                         self.path_detail(vsc={"file_position": "far"})[0])

    def test_a_maximum_path_count_that_is_missing_or_empty(self):
        self.view.maximum = None
        self.assertEqual("Waiting for file position", self.path_detail()[0])
        self.view.maximum = 0
        self.assertEqual("Layer has no paths", self.path_detail()[0])

    def test_the_minimum_path_handle_is_parked_at_zero(self):
        self.view.minimum_path = 7
        detail, hydration = self.path_detail()
        self.assertEqual(0, self.view.minimum_path)
        self.assertEqual((4,), hydration)
        self.assertTrue(detail.startswith("path 500/1000 "), detail)
        self.assertEqual([0], self.view.minimum_paths)

    def test_the_smoothed_path_is_delegated_to_the_motion_driver(self):
        motion = MotionTrace()
        self.run_path(motion=motion)
        self.assertEqual([(3, 0.5, "byte-range")], motion.writes)
        self.assertEqual([], self.view.paths, "the driver owns the view while smoothing")

    def test_an_unsmoothed_path_writes_the_view_directly(self):
        motion = MotionTrace()
        self.follower.bind_motion(motion)
        config = preview_config(path_follow=True, path_smoothing=False)
        self.run_path(config=config, motion=motion)
        self.assertEqual(1, motion.reset_calls, "the glide is dropped, not animated")
        self.assertEqual([500.0], self.view.paths)
        self.view.path = 499.9
        self.run_path(config=config, motion=motion)
        self.assertEqual([500.0], self.view.paths, "a sub-half-path move is not rewritten")

    def test_a_real_path_write_arms_the_nozzle_indicator(self):
        self.run_path(config=preview_config(path_follow=True, show_toolhead_indicator=True))
        self.assertTrue(self.follower.state.nozzle_valid)
        self.assertEqual(1, self.port.nozzles)

    def test_a_layer_change_drops_the_stale_fraction(self):
        self.run_path()
        self.assertEqual(3, self.follower.state.path_layer)
        self.assertEqual(0.5, self.follower.state.path_fraction)


class FollowerEtaTests(unittest.TestCase):
    def make(self, elapsed=None, duration=60.0, elapsed_layer=3):
        view = FakeView(layer=elapsed_layer)
        follower, port = follower_of(view)
        index = FakeIndex(elapsed=elapsed)
        follower.observe(snapshot(elapsed_layer),
                         status(print_stats={"print_duration": duration}), preview_config(), index)
        return follower, port, index

    def test_format_duration_pads_and_floors(self):
        self.assertEqual("00:00:00", PreviewFollower.format_duration(-4))
        self.assertEqual("01:02:03", PreviewFollower.format_duration(3723.4))
        self.assertEqual("10:00:00", PreviewFollower.format_duration(36000))

    def test_remaining_needs_a_position_and_an_index(self):
        follower, _, index = self.make()
        self.assertIsNone(follower.remaining(4, None), "no index")
        follower.reset_print()
        self.assertIsNone(follower.remaining(4, index), "no observed layer")

    def test_remaining_reports_the_distance_to_the_selected_layer(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        self.assertAlmostEqual(120.0, follower.remaining(5, index))

    def test_remaining_yields_none_when_a_boundary_is_unknown(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, None, 240.0])
        self.assertIsNone(follower.remaining(4, index))

    def test_remaining_uses_the_within_layer_fraction_as_a_floor(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        follower.observe(snapshot(3),
                         status(print_stats={"print_duration": 60.0},
                                virtual_sdcard={"file_position": 400}),
                         preview_config(path_follow=True),
                         FakeIndex(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0], fraction=0.75))
        self.assertAlmostEqual(0.75, follower.state.path_fraction)
        self.assertAlmostEqual(15.0, follower.remaining(4, index),
                               msg="three quarters through the layer is not the layer start")

    def test_remaining_end_anchors_on_the_last_layer_and_the_estimate(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        self.assertAlmostEqual(180.0, follower.remaining_end(index, None))
        self.assertAlmostEqual(480.0, follower.remaining_end(index, 600.0),
                               msg="a later slicer estimate replaces the index end")

    def test_remaining_end_blends_the_mean_layer_duration_without_an_estimate(self):
        follower, _, _ = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        trailing = [0.0, 60.0, 120.0, 180.0, 240.0, None, None]
        self.assertAlmostEqual(180.0, follower.remaining_end(FakeIndex(elapsed=trailing), None))

    def test_remaining_end_gives_up_on_a_position_or_a_timing_it_cannot_read(self):
        follower, _, index = self.make()
        self.assertIsNone(follower.remaining_end(None, 100.0), "no index")
        follower.reset_print()
        self.assertIsNone(follower.remaining_end(index, 100.0), "no observed layer")
        follower2, _, blank = self.make(elapsed=[None, None, None, None, None])
        self.assertIsNone(follower2.remaining_end(blank, 100.0), "no timing at all")
        follower3, _, partial = self.make(elapsed=[60.0, None, None, None, None],
                                          elapsed_layer=2)
        self.assertIsNone(follower3.remaining_end(partial, 100.0), "the layer start is unreadable")

    def test_remaining_end_applies_the_learned_drift(self):
        follower, _, index = self.make(elapsed=[0.0, 600.0, 1200.0, 1800.0, 2400.0], duration=60.0)
        baseline = follower.remaining_end(index, None)
        follower._state = replace(follower.state, eta_learn=True, drift=2.0)
        self.assertAlmostEqual(baseline * 2.0, follower.remaining_end(index, None))
        follower._state = replace(follower.state, eta_learn=True, drift=None)
        self.assertAlmostEqual(baseline, follower.remaining_end(index, None))

    def test_the_eta_text_covers_every_selection_relation(self):
        follower, port, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, 240.0])
        cases = ((2, "already printed"), (3, "current print layer"))
        for selected, phrase in cases:
            port.selected_layer = selected
            follower.update_eta(snapshot(3), index)
            self.assertIn(phrase, follower.state.eta_text)
        port.selected_layer = 5
        follower.update_eta(snapshot(3), index)
        self.assertIn("in 00:02:00", follower.state.eta_text)
        self.assertIn("≈", follower.state.eta_text)

    def test_a_far_future_layer_names_the_weekday_clock(self):
        follower, port, index = self.make(elapsed=[0.0, 100000.0, 200000.0, 300000.0, 400000.0])
        port.selected_layer = 4
        follower.update_eta(snapshot(3), index)
        self.assertRegex(follower.state.eta_text, r"^Selected layer 5 — in \d\d:\d\d:\d\d · ≈[A-Z][a-z]{2} \d\d:\d\d$")

    def test_a_missing_last_boundary_does_not_affect_a_mid_print_eta(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, 180.0, None])
        self.assertAlmostEqual(60.0, follower.remaining(4, index))

    def test_an_unreadable_finish_boundary_falls_back_to_the_layer_start(self):
        follower, _, index = self.make(elapsed=[0.0, 60.0, 120.0, None, 240.0])
        self.assertAlmostEqual(120.0, follower.remaining(5, index))

    def test_an_unreadable_timing_is_named_in_the_eta_text(self):
        follower, port, index = self.make(elapsed=[0.0, None, None, None, None])
        port.selected_layer = 4
        follower.update_eta(snapshot(3), index)
        self.assertIn("ETA unavailable", follower.state.eta_text)

    def test_the_eta_text_is_blank_when_nothing_is_selected_or_observed(self):
        follower, _, index = self.make()
        follower.update_eta(snapshot(3, active=False), index)
        self.assertEqual("", follower.state.eta_text)
        follower.update_eta(snapshot(3), index)
        self.assertEqual("", follower.state.eta_text, "nothing is selected")
        follower.reset_print()
        follower._cura.selected_layer = 5
        follower.update_eta(snapshot(3), index)
        self.assertEqual("", follower.state.eta_text, "no layer has been observed yet")


# --------------------------------------------------------------------------
# PreviewMotion — the Qt tick driver
# --------------------------------------------------------------------------


class FakeClock:
    """Replaces the module's time reference so the ticks are deterministic."""

    def __init__(self): self.now = 1000.0
    def monotonic(self): return self.now
    def advance(self, seconds): self.now += seconds


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class PreviewMotionTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from PyQt6.QtCore import QCoreApplication
        # QTimer needs an application object even when it never fires here.
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.patch = patch("plugins.PreviewMotion.time", FakeClock())
        # PreviewMotion resolves `time.monotonic` through its own module global.
        self.clock = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.view = FakeView(layer=1, maximum=1000)
        self.cura = FakeCura(self.view)
        self.remember = MotionTrace()
        self.motion = PreviewMotion(self.cura, self.remember)

    def test_the_first_observation_jumps_and_clears_the_old_layer(self):
        self.motion.write(1, 0.25)
        self.assertEqual((1, 0.25, 0.25), (self.motion._layer, self.motion._target,
                                           self.motion._displayed))
        self.assertFalse(self.motion._timer.isActive(), "no glide is owed yet")
        self.assertEqual(1, self.view.resets)
        self.assertEqual(250.0, self.view.paths[-1])
        self.assertEqual([0], self.view.minimum_paths, "the minimum is set once per view")
        self.assertEqual(1, self.remember.calls)

    def test_a_layer_transition_jumps_rather_than_gliding(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        rate = self.motion._velocity
        self.motion.write(2, 0.05)
        self.assertEqual((2, 0.05), (self.motion._layer, self.motion._displayed))
        self.assertEqual(2, self.view.resets, "each transition drops the old layer's cache")
        self.assertAlmostEqual(rate * 0.8, self.motion._velocity, "the rate is warm-started")

    def test_a_second_observation_in_a_layer_starts_the_glide(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.assertTrue(self.motion._timer.isActive())
        self.assertGreater(self.motion._velocity, 0.0)
        self.assertAlmostEqual(1.0, self.motion._inter_poll)

    def test_a_flat_stretch_keeps_the_previous_rate(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        rate = self.motion._velocity
        self.assertGreater(rate, 0.0)
        # A repeat of the newest value past the window carries no rate at all.
        self.clock.advance(5.0)
        self.motion.write(1, 0.4)
        self.assertEqual(rate, self.motion._velocity)

    def test_an_unchanged_window_never_invents_a_rate(self):
        flat = PreviewMotion(self.cura, self.remember)
        flat.write(1, 0.4)
        self.clock.advance(1.0)
        flat.write(1, 0.4)
        self.assertEqual(0.0, flat._velocity)

    def test_a_travel_spike_is_clipped_at_the_velocity_cap(self):
        self.motion.write(1, 0.0)
        self.clock.advance(1.0)
        self.motion.write(1, 1.0)
        self.assertLessEqual(self.motion._velocity, 0.5)

    def test_a_late_poll_still_reconstructs_a_trajectory(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.clock.advance(0.5)
        target = self.motion._current_target(self.clock.now)
        self.assertGreaterEqual(target, 0.2)
        self.assertLessEqual(target, 0.4)

    def test_the_ramp_saturates_at_the_newest_observation(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.clock.advance(120.0)
        self.assertEqual(0.4, self.motion._current_target(self.clock.now))

    def test_the_tick_advances_the_display_and_stops_at_the_target(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.clock.advance(0.5)
        self.motion._tick()
        self.assertGreater(self.motion._displayed, 0.2)
        self.assertLessEqual(self.motion._displayed, self.motion._target)
        for _ in range(200):
            self.clock.advance(0.033)
            self.motion._tick()
        self.assertEqual(self.motion._target, self.motion._displayed)
        self.assertFalse(self.motion._timer.isActive())

    def test_a_tick_without_a_target_stops_the_timer(self):
        self.motion._timer.start()
        self.motion._tick()
        self.assertFalse(self.motion._timer.isActive())

    def test_reset_stops_the_glide_and_keeps_the_poll_estimate(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.motion.reset()
        self.assertFalse(self.motion._timer.isActive())
        self.assertIsNone(self.motion._displayed)
        self.assertEqual(0.0, self.motion._velocity)
        self.assertEqual(1.0, self.motion._inter_poll)
        self.motion.write(2, 0.5)
        self.assertEqual(0.5, self.motion._displayed)

    def test_a_write_without_a_view_or_paths_writes_nothing(self):
        self.cura.view = None
        self.motion.write(1, 0.5)
        self.assertEqual(0, self.remember.calls)
        self.cura.view = self.view
        self.view.maximum = None
        self.motion._write(0.5)
        self.assertEqual(0, self.remember.calls)
        self.view.maximum = 0
        self.motion._write(0.5)

    def test_a_replacement_view_receives_its_minimum_again(self):
        self.motion.write(1, 0.5)
        self.assertEqual([0], self.view.minimum_paths)
        self.motion._write(0.6)
        self.assertEqual([0], self.view.minimum_paths, "the minimum is not rewritten per tick")
        self.cura.view = FakeView(layer=1, maximum=1000)
        self.motion._write(0.6)
        self.assertEqual([0], self.cura.view.minimum_paths, "a new view gets its own minimum")

    def test_the_trace_dumps_samples_and_rotates_a_large_file(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "trace.csv")
            motion = PreviewMotion(self.cura, self.remember, trace_path=path)
            motion.write(1, 0.2)
            self.clock.advance(1.0)          # the sample window opens after the obs row
            motion._tick()
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("obs", text)
            self.assertIn("tick", text)
            motion._trace("tick", self.clock.now, 1, 0.3)   # inside the 2 Hz sample window
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(text, handle.read(), "tick samples are rate limited")
            with open(path, "wb") as handle:
                handle.write(b"x" * (512 * 1024 + 1))
            self.clock.advance(1.0)
            motion._trace("obs", self.clock.now, 1, 0.5)
            with open(path, encoding="utf-8") as handle:
                rotated = handle.read()
            self.assertTrue(rotated.startswith("time,event,layer,fraction,displayed,velocity,method"),
                            "an oversized trace is truncated and re-headered")

    def test_an_unwritable_trace_path_is_swallowed(self):
        motion = PreviewMotion(self.cura, self.remember, trace_path="/nonexistent-dir/t.csv")
        motion.write(1, 0.5)
        motion._trace("obs", 1.0, 1, 0.5)

    def test_a_sub_epsilon_shortfall_snaps_to_the_target(self):
        # A rounding leftover must not leave the timer running forever a
        # millionth of a path short of the observation.
        self.motion.write(1, 0.5)
        self.motion._displayed = 0.5 - 1e-7
        self.motion._timer.start()
        self.motion._tick()
        self.assertEqual(0.5, self.motion._displayed)
        self.assertFalse(self.motion._timer.isActive(), "the glide is finished")

    def test_close_stops_the_timer(self):
        self.motion.write(1, 0.2)
        self.clock.advance(1.0)
        self.motion.write(1, 0.4)
        self.motion.close()
        self.assertFalse(self.motion._timer.isActive())


# --------------------------------------------------------------------------
# MonitorData — the request/poll lifecycle and its projections
# --------------------------------------------------------------------------


class FakeSession:
    """The session record MonitorData reads for intervals and generations."""

    def __init__(self):
        self.base_url = "http://printer:7125/"
        self.generation = 1
        self.snapshot = SimpleNamespace(printer_state="ready")
        self.forced = {}
        self.calls = []
        self.poll_policy = SimpleNamespace(interval_ms=self.interval_ms)

    def interval_ms(self, category, default, printer_state):
        self.calls.append((category, default, printer_state))
        return int(self.forced.get(category, default))


class FakeTransport:
    """The wire the monitor sends through; every request stays inspectable."""

    def __init__(self):
        self.sent = []
        self.cancelled = []

    def send_json(self, owner, channel, method, path, callback, *, body=None, replace=False,
                  category=None, timeout_ms=None):
        self.sent.append(SimpleNamespace(owner=owner, channel=channel, method=method, path=path,
                                         callback=callback, body=body, replace=replace,
                                         category=category, timeout_ms=timeout_ms))
        return True

    def cancel_owner(self, name): self.cancelled.append(name)


class FakeDataClient(QObject if QT_AVAILABLE else object):
    """The MoonrakerClient seam MonitorData binds to."""

    if QT_AVAILABLE:
        statusReceived = pyqtSignal(object)
        commandChanged = pyqtSignal(object)
        sessionInvalidated = pyqtSignal()
        connectionChanged = pyqtSignal(bool, str)

    def __init__(self):
        if QT_AVAILABLE:
            super().__init__()
        self.connected = False
        self.status = {"print_stats": {"state": "standby"}}
        self.aux_interval_ms = 2500
        self.console_interval_ms = 1000
        self.effective_feed_mode = "http"
        self.session = FakeSession()
        self.transport = FakeTransport()
        self.assumed_stopped = False
        self.aux_patch = None
        self.rpc_ok = False
        self.rpcs = []
        self.aux_sets = []
        self.drains = 0
        self.refreshes = 0
        self.stopped = 0
        self.started = 0
        self.resubscribes = 0
        self.tracked = []
        self.accepted = []
        self.failed = []
        self.settled = []
        self.assumed_stops = 0
        self.guards = []

    def rpc(self, method, params, callback):
        self.rpcs.append((method, params, callback))
        return self.rpc_ok

    def drain_aux(self):
        self.drains += 1
        return (self.aux_patch, 1.0)

    def set_auxiliary_objects(self, names): self.aux_sets.append(set(names))
    def force_refresh(self): self.refreshes += 1
    def resubscribe(self): self.resubscribes += 1
    def assume_print_stopped(self): self.assumed_stops += 1
    def set_toolhead_guard(self, active): self.guards.append(active)
    def stop(self): self.stopped += 1
    def start(self): self.started += 1

    def track_command(self, name, expected_states=(), *, timeout_s=10.0):
        self.tracked.append((name, tuple(expected_states), timeout_s))

    def accept_command(self, name): self.accepted.append(name)
    def fail_command(self, name, detail): self.failed.append((name, detail))
    def settle_command(self, name, detail): self.settled.append((name, detail))


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MonitorDataTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.client = FakeDataClient()
        self.data = MonitorData(self.client)
        for timer in self.data._timers.values(): timer.stop()
        self.data._console_watch.stop()
        self.data._watchdog.stop()
        self.changes = []
        self.invalidations = []
        self.blocks = []
        self.states = []
        self.aux_changes = []
        self.console_changes = []
        self.data.changed.connect(lambda: self.changes.append(1))
        self.data.invalidated.connect(lambda: self.invalidations.append(1))
        self.data.previewBlockChanged.connect(self.blocks.append)
        self.data.connectionStateChanged.connect(self.states.append)
        self.data.auxiliaryChanged.connect(lambda: self.aux_changes.append(1))
        self.data.consoleStoreChanged.connect(lambda: self.console_changes.append(1))
        self.addCleanup(self.data.set_active, False)

    def pump(self, ms=20):
        # A real nested event loop: processEvents() alone never delivers the
        # later(0) pushes these tests wait on.
        from PyQt6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def request_to(self, channel, position=-1):
        sent = [item for item in self.client.transport.sent if item.channel == channel]
        return sent[position]

    def activate(self):
        self.client.connected = True
        self.data.set_active(True)


class MonitorConnectionTests(MonitorDataTests):
    def test_a_connect_arms_the_monitor_and_rearms_a_live_one(self):
        self.client.connected = True
        self.client.connectionChanged.emit(True, "handshake ok")
        self.assertTrue(self.data.active)
        self.assertEqual(("yes", "handshake ok"), (self.data.connection_state,
                                                   self.data.connection_detail))
        self.assertEqual(1, self.client.refreshes)
        before = len(self.client.transport.sent)
        self.client.connectionChanged.emit(True, "reconnected")
        self.assertEqual(2, self.client.refreshes, "a live reconnect re-fires the lanes")
        self.assertGreater(len(self.client.transport.sent), before)

    def test_a_disconnect_reads_no_once_a_connection_has_been_seen(self):
        self.client.connectionChanged.emit(True, "")
        self.client.connected = False
        self.client.connectionChanged.emit(False, "dropped")
        self.assertEqual("no", self.data.connection_state)
        self.assertEqual("no", self.states[-1])

    def test_a_never_connected_session_reads_unknown(self):
        self.assertEqual("unknown", self.data.connection_state)
        self.assertFalse(self.data.connected)
        self.client.connectionChanged.emit(False, "")
        self.assertEqual("unknown", self.data.connection_state)

    def test_a_bare_connection_event_falls_back_to_the_client_flag(self):
        self.client.connected = True
        self.data._connection_changed()
        self.assertTrue(self.data.active)
        self.assertEqual("", self.data.connection_detail)

    def test_a_session_invalidation_deactivates_the_monitor(self):
        self.activate()
        self.client.connected = False        # the transport went with the session
        self.client.sessionInvalidated.emit()
        self.assertFalse(self.data.active)
        self.assertEqual("unknown", self.data.connection_state)

    def test_the_watchdog_re_subscribes_a_dead_discovery_chain_once(self):
        self.data.refresh_discovery = Mock()
        self.activate()
        armed = self.data.refresh_discovery.call_count
        live = self.client.resubscribes
        self.data._watch_discovery()
        self.assertEqual(live + 1, self.client.resubscribes)
        self.assertEqual(armed + 1, self.data.refresh_discovery.call_count)
        self.data._update(objects=("extruder",), auxiliary={"extruder": {"temperature": 20}})
        self.data._watch_discovery()
        self.assertEqual(live + 1, self.client.resubscribes, "a live chain is left alone")

    def test_the_watchdog_does_nothing_while_inactive(self):
        self.data._watch_discovery()
        self.assertEqual(0, self.client.resubscribes)

    def test_deactivation_clears_the_session_and_cancels_its_owner(self):
        self.activate()
        self.data.set_console_expanded(True, 5.0)
        self.data._console_entries = [{"text": "x"}]
        self.data.set_active(False)
        self.assertEqual(["monitor"], self.client.transport.cancelled)
        self.assertFalse(self.data._console_expanded)
        self.assertIsNone(self.data._console_seed)
        self.assertEqual([], self.data.console_entries)
        self.assertEqual(1, len(self.invalidations))
        self.assertTrue(self.blocks[-1]["inactive"],
                        "the cleared monitor publishes the absent shape")

    def test_the_poll_intervals_follow_the_session_policy(self):
        from plugins.MoonrakerSession import RequestCategory
        self.client.session.forced = {RequestCategory.AUXILIARY: 4321}
        self.activate()
        self.assertEqual(4321, self.data._timers[RequestCategory.AUXILIARY].interval())
        self.assertTrue([call for call in self.client.session.calls
                         if call[0] == RequestCategory.AUXILIARY])

    def test_reconnect_cycles_the_client_and_rearms(self):
        self.data.reconnect()
        self.assertEqual((1, 1), (self.client.stopped, self.client.started))
        self.assertTrue(self.data.active)

    def test_an_emergency_reconnect_needs_an_armed_connected_client(self):
        self.data.reconnect_after_emergency()
        self.assertEqual(0, self.client.stopped, "nothing to cycle while inactive")
        self.activate()
        self.client.connected = False
        self.data.reconnect_after_emergency()
        self.assertEqual(0, self.client.stopped, "nothing to cycle while disconnected")
        self.client.connected = True
        self.data.reconnect_after_emergency()
        self.assertEqual(1, self.client.stopped)
        self.assertTrue(self.data.active)


class MonitorRequestTests(MonitorDataTests):
    def test_a_request_needs_an_active_monitor_and_a_session_url(self):
        self.assertFalse(self.data.request("x", "GET", "p", lambda payload, error: None))
        self.activate()
        self.client.session.base_url = ""
        self.assertFalse(self.data.request("x", "GET", "p", lambda payload, error: None))
        self.client.session.base_url = "http://printer/"
        self.assertTrue(self.data.request("x", "GET", "p", lambda payload, error: None))

    def test_a_reply_from_a_dead_generation_is_dropped(self):
        answers = []
        self.activate()
        self.data.request("probe", "GET", "p", lambda payload, error: answers.append(payload))
        stale = self.request_to("probe")
        self.data.set_active(False)
        stale.callback({"result": 1}, None)
        self.assertEqual([], answers, "a stale session must not reach the callback")
        self.data.set_active(True)
        self.data.request("probe", "GET", "p", lambda payload, error: answers.append(payload))
        self.request_to("probe").callback({"result": 2}, None)
        self.assertEqual([{"result": 2}], answers)

    def test_a_reply_from_the_live_generation_is_delivered(self):
        answers = []
        self.activate()
        self.data.request("probe", "GET", "p", lambda payload, error: answers.append(payload))
        self.request_to("probe").callback({"result": 1}, None)
        self.assertEqual([{"result": 1}], answers)

    def test_the_rpc_lane_answers_over_the_socket_without_touching_the_wire(self):
        self.activate()
        self.client.rpc_ok = True
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("aux", "POST", "printer/objects/query",
                                          lambda payload, error: None,
                                          rpc=("printer.objects.query", {"objects": {}})))
        self.assertEqual("printer.objects.query", self.client.rpcs[-1][0])
        self.assertEqual(before, len(self.client.transport.sent))

    def test_a_boot_window_rpc_failure_skips_the_wire_but_retries_discovery(self):
        self.activate()
        self.client.effective_feed_mode = "websocket"
        self.data.later = Mock()
        from plugins.MoonrakerSession import RequestCategory
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("objects", "GET", "printer/objects/list",
                                          lambda payload, error: None,
                                          category=RequestCategory.DISCOVERY,
                                          rpc=("printer.objects.list", {})))
        self.assertEqual(before, len(self.client.transport.sent), "the wire is skipped")
        self.assertEqual((1000, self.data.refresh_discovery), self.data.later.call_args[0])

    def test_a_boot_window_rpc_failure_without_discovery_just_waits(self):
        self.activate()
        self.client.effective_feed_mode = "websocket"
        self.data.later = Mock()
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("aux", "POST", "printer/objects/query",
                                          lambda payload, error: None,
                                          rpc=("printer.objects.query", {})))
        self.assertEqual(before, len(self.client.transport.sent))
        self.assertEqual(0, self.data.later.call_count)

    def test_an_http_session_falls_through_to_the_wire(self):
        self.activate()
        before = len(self.client.transport.sent)
        self.assertTrue(self.data.request("aux", "POST", "printer/objects/query",
                                          lambda payload, error: None,
                                          rpc=("printer.objects.query", {})))
        self.assertEqual(before + 1, len(self.client.transport.sent))

    def test_a_deferred_callback_is_bound_to_its_generation(self):
        calls = []
        self.activate()
        self.data.later(0, lambda: calls.append("fired"))
        self.pump()
        self.assertEqual(["fired"], calls)
        self.data.set_active(False)
        self.data.later(0, lambda: calls.append("stale"))
        self.pump()
        self.assertEqual(["fired"], calls)


class MonitorProjectionTests(MonitorDataTests):
    def test_the_observation_record_assembles_from_both_lanes(self):
        self.activate()
        self.client.assumed_stopped = True
        self.data._update(core={"print_stats": {"state": "printing"},
                                "pause_resume": {"is_paused": True}},
                          auxiliary={"configfile": {"save_config_pending": True},
                                     "toolhead": {"homed_axes": "xyz"}},
                          objects=("pause_resume", "extruder"))
        observed = self.data.observation
        self.assertEqual(("printing", "xyz", True, True), (observed.state, observed.homed_axes,
                                                           observed.assumed_stopped,
                                                           observed.save_config_pending))
        self.assertTrue(observed.is_paused)
        self.assertTrue(observed.pause_resume_supported)
        self.assertEqual("yes", observed.connection)
        self.assertTrue(observed.active)

    def test_an_unobserved_object_list_reads_as_no_capability_signal(self):
        self.data._update(objects=(), auxiliary={"toolhead": {}})
        self.assertIsNone(self.data.observation.pause_resume_supported)
        self.assertIsNone(self.data.observation.is_paused)

    def test_an_observed_list_without_the_module_fails_the_row_closed(self):
        self.data._update(objects=("stepper_enable",))
        self.assertFalse(self.data.observation.pause_resume_supported)

    def test_the_chrome_and_lane_pushes_update_without_a_stale_rebuild(self):
        self.activate()          # a deferred push is dropped while inactive
        self.pump()
        self.changes.clear()
        self.data.set_controls_locked(True)
        self.assertTrue(self.data.observation.controls_locked)
        self.pump()
        self.assertEqual(1, len(self.changes), "the locked push publishes once")
        self.data.set_controls_locked(True)
        self.pump()
        self.assertEqual(1, len(self.changes), "an unchanged push is a no-op")
        self.data.set_commands_busy(True)
        self.assertTrue(self.data.observation.busy)
        self.assertEqual(1, len(self.changes), "the busy push never re-publishes")
        self.data.set_commands_busy(False)
        self.assertFalse(self.data.observation.busy)

    def test_freeze_deep_freezes_a_nested_payload(self):
        frozen = freeze({"a": [1, {"b": 2}], "c": (3,), "d": "s"})
        self.assertEqual({"a": (1, {"b": 2}), "c": (3,), "d": "s"}, dict(frozen))
        with self.assertRaises(TypeError):
            frozen["a"] = ()
        with self.assertRaises(TypeError):
            frozen["a"][1]["b"] = 9

    def test_the_client_passthroughs_reach_the_client(self):
        self.data.track_command("Resume", ("paused",), timeout_s=0.5)
        self.data.accept_command("Resume")
        self.data.fail_command("Resume", "no")
        self.data.force_refresh()
        self.data.assume_print_stopped()
        self.data.set_toolhead_guard(True)
        self.assertEqual(("Resume", ("paused",), 0.5), self.client.tracked[-1])
        self.assertEqual(["Resume"], self.client.accepted)
        self.assertEqual([("Resume", "no")], self.client.failed)
        self.assertEqual(1, self.client.refreshes)
        self.assertEqual(1, self.client.assumed_stops)
        self.assertEqual([True], self.client.guards)

    def test_the_read_only_properties_project_the_snapshot(self):
        self.assertEqual(self.client.status, self.data.status)
        self.assertTrue(self.data.wants_object("extruder"))
        self.assertFalse(self.data.wants_object("print_stats"))
        self.assertIs(self.data.snapshot, self.data._snapshot)
        self.assertFalse(self.data.active)

    def test_observe_filters_non_mappings_and_ignores_a_dead_monitor(self):
        self.data.observe({"print_stats": {"state": "printing"}, "junk": 5})
        self.assertEqual({}, dict(self.data.snapshot.core), "an inactive monitor stores nothing")
        self.data.observe("not a mapping")
        self.activate()
        self.data.observe({"print_stats": {"state": "printing"}, "junk": 5})
        self.assertEqual({"print_stats": {"state": "printing"}}, dict(self.data.snapshot.core))


class MonitorAuxTests(MonitorDataTests):
    def test_the_aux_lane_heals_the_discovery_chain_before_objects_arrive(self):
        self.data.refresh_discovery = Mock()
        self.activate()
        armed = self.data.refresh_discovery.call_count
        self.data.refresh_aux()
        self.assertEqual(armed + 1, self.data.refresh_discovery.call_count)

    def test_the_socket_is_a_source_and_its_fragments_merge(self):
        self.activate()
        self.data._update(objects=("extruder",))
        self.client.effective_feed_mode = "websocket"
        self.client.aux_patch = {"extruder": {"temperature": 200.0}}
        self.data.refresh_aux()
        self.assertEqual(1, self.client.drains)
        self.assertEqual(200.0, self.data.snapshot.auxiliary["extruder"]["temperature"])

    def test_an_empty_socket_drain_re_issues_the_subscription(self):
        self.activate()
        self.data._update(objects=("extruder", "stepper_enable"))
        self.client.effective_feed_mode = "websocket"
        self.data.refresh_aux()
        self.assertEqual({"extruder"}, self.client.aux_sets[-1])

    def test_an_http_aux_poll_queries_only_the_wanted_objects(self):
        self.activate()
        self.data._update(objects=("extruder", "stepper_enable"))
        self.data.refresh_aux()
        item = self.request_to("aux")
        self.assertEqual(["extruder"], list(item.body["objects"]))
        self.assertIsNone(item.body["objects"]["extruder"])

    def test_an_http_aux_poll_with_nothing_wanted_sends_nothing(self):
        self.activate()
        self.data._update(objects=("stepper_enable",))
        before = len(self.client.transport.sent)
        self.data.refresh_aux()
        self.assertEqual(before, len(self.client.transport.sent))

    def test_the_configfile_aux_query_asks_for_the_pending_flags(self):
        self.activate()
        self.data._update(objects=("configfile",))
        self.data.refresh_aux()
        self.assertEqual(["save_config_pending", "save_config_pending_items"],
                         self.request_to("aux").body["objects"]["configfile"])

    def test_a_reconcile_only_rides_a_socket_session(self):
        self.activate()
        self.data._update(objects=("extruder",))
        self.data._reconcile_aux_subscription()
        self.assertEqual([], self.client.aux_sets)
        self.client.effective_feed_mode = "websocket"
        self.data._reconcile_aux_subscription()
        self.assertEqual([{"extruder"}], self.client.aux_sets)

    def test_the_aux_reply_merges_into_the_wanted_set_and_rebuilds(self):
        self.activate()
        self.data._update(objects=("extruder", "heater_bed"))
        self.data._merge_aux({"extruder": {"temperature": 200.0}})
        self.data._merge_aux({"extruder": {"target": 210.0}, "ghost": {"x": 1}})
        self.assertEqual({"temperature": 200.0, "target": 210.0},
                         dict(self.data.snapshot.auxiliary["extruder"]))
        self.assertNotIn("ghost", self.data.snapshot.auxiliary)
        self.assertGreaterEqual(len(self.aux_changes), 2)
        self.assertIsInstance(self.blocks[-1], dict)

    def test_a_newly_seen_device_joins_the_subscription(self):
        self.activate()
        self.data._update(objects=())
        self.data._merge_aux({"extruder": {"temperature": 20.0}})
        self.assertEqual({"extruder"}, self.client.aux_sets[-1])
        self.assertIn("extruder", self.data.snapshot.auxiliary)

    def test_a_non_mapping_aux_reply_is_dropped(self):
        self.activate()
        before = dict(self.data.snapshot.auxiliary)
        self.data._aux("nonsense", None)
        self.data._aux({"result": {"status": {"extruder": {"temperature": 1.0}}}}, "error")
        self.assertEqual(before, dict(self.data.snapshot.auxiliary))
        self.data._aux({"result": {"status": {"extruder": {"temperature": 1.0}}}}, None)
        self.assertEqual({"temperature": 1.0}, dict(self.data.snapshot.auxiliary["extruder"]))

    def test_the_discovery_chain_feeds_objects_presets_and_webcams(self):
        self.activate()
        before = len(self.client.transport.sent)
        self.data.refresh_discovery()
        fresh = self.client.transport.sent[before:]
        self.assertEqual(3, len(fresh))
        self.assertEqual({"objects", "presets", "webcams"}, {item.channel for item in fresh})
        self.request_to("objects").callback({"result": {"objects": ["configfile", "extruder"]}}, None)
        self.assertEqual(("configfile", "extruder"), self.data.snapshot.objects)
        self.assertTrue([item for item in self.client.transport.sent
                         if item.channel == "config-static"])
        self.request_to("presets").callback({"result": {"value": {"pla": {"temp": 200}}}}, None)
        self.assertEqual({"pla": {"temp": 200}}, dict(self.data.snapshot.presets))
        self.request_to("presets").callback({"result": {}}, "error")
        self.assertEqual({"pla": {"temp": 200}}, dict(self.data.snapshot.presets))

    def test_a_broken_or_empty_objects_reply_leaves_the_lists_alone(self):
        self.activate()
        self.data._objects({"result": {"objects": "extruder"}}, None)
        self.assertEqual((), self.data.snapshot.objects)
        self.data._objects({"result": {"objects": ["extruder"]}}, "error")
        self.assertEqual((), self.data.snapshot.objects, "a failed poll never erases the list")


class MonitorLaneTests(MonitorDataTests):
    def test_the_endstop_poll_waits_for_a_ready_klippy_and_an_idle_printer(self):
        self.activate()
        self.data._update(server={"klippy_state": "shutdown"})
        before = len(self.client.transport.sent)
        self.data.refresh_endstops()
        self.assertEqual(before, len(self.client.transport.sent), "klippy is not ready")
        self.data._update(server={"klippy_state": "ready"},
                          core={"print_stats": {"state": "printing"}})
        self.data.refresh_endstops()
        self.assertEqual(before, len(self.client.transport.sent), "the toolhead must not dwell")
        self.data._update(core={"print_stats": {"state": "standby"}})
        self.data.refresh_endstops()
        item = self.request_to("endstops")
        item.callback({"result": {"x": "open"}}, None)
        self.assertEqual({"x": "open"}, dict(self.data.snapshot.endstops))

    def test_a_failed_endstop_poll_keeps_the_last_known_states(self):
        self.activate()
        self.data._update(server={"klippy_state": "ready"},
                          core={"print_stats": {"state": "standby"}},
                          endstops={"x": "TRIGGERED"})
        self.data.refresh_endstops()
        self.request_to("endstops").callback({"result": {}}, "timeout")
        self.assertEqual({"x": "TRIGGERED"}, dict(self.data.snapshot.endstops))

    def test_power_system_and_webcam_replies_project_into_the_snapshot(self):
        self.activate()
        self.data.refresh_power()
        self.request_to("power-list").callback({"result": {"devices": [{"device": "psu"}]}}, None)
        self.assertEqual(({"device": "psu"},), self.data.snapshot.power)
        self.data.refresh_system()
        self.request_to("server-info").callback({"result": {"klippy_state": "ready"}}, None)
        self.request_to("printer-info").callback({"result": {"state": "ready"}}, None)
        self.assertEqual({"klippy_state": "ready"}, dict(self.data.snapshot.server))
        self.assertEqual({"state": "ready"}, dict(self.data.snapshot.printer))
        self.data.refresh_webcams()
        self.request_to("webcams").callback({"result": {"webcams": [
            {"name": "cam", "enabled": True}, {"name": "off", "enabled": False}, "junk"]}}, None)
        self.assertEqual(({"name": "cam", "enabled": True},), self.data.snapshot.webcams)

    def test_a_failed_power_or_webcam_poll_keeps_the_last_known_values(self):
        self.activate()
        self.data._update(power=({"device": "psu"},), webcams=({"name": "cam"},))
        self.data.refresh_power()
        self.request_to("power-list").callback({"result": {}}, "timeout")
        self.assertEqual(({"device": "psu"},), self.data.snapshot.power)
        self.data.refresh_webcams()
        self.request_to("webcams").callback({"result": {}}, "timeout")
        self.assertEqual(({"name": "cam"},), self.data.snapshot.webcams)

    def test_refresh_all_is_a_no_op_while_inactive(self):
        self.client.force_refresh = Mock()
        self.data.refresh_all()
        self.assertEqual(0, self.client.force_refresh.call_count)
        self.activate()
        before = self.client.force_refresh.call_count
        self.data.refresh_all()
        self.assertEqual(before + 1, self.client.force_refresh.call_count)


class MonitorConsoleTests(MonitorDataTests):
    def store(self, entries):
        return {"result": {"gcode_store": entries}}

    def test_an_expand_seeds_the_skip_and_polls_immediately(self):
        self.activate()
        self.data.set_console_expanded(True, 10.0)
        self.assertTrue(self.data._console_expanded)
        self.assertEqual(10.0, self.data._console_seed)
        self.assertFalse(self.data._console_watch.isActive())
        self.request_to("console-store")

    def test_the_first_fetch_skips_the_seeded_history_permanently(self):
        self.activate()
        self.data.set_console_expanded(True, 10.0)
        item = self.request_to("console-store")
        item.callback(self.store([
            {"type": "response", "time": 5.0, "message": "stale"},
            {"type": "response", "time": 20.0, "message": "ok"},
            {"type": "response", "time": 20.0, "message": "ok"},
            {"type": "command", "time": 21.0, "message": "G28"},
            {"type": "response", "time": 22.0, "message": ""},
            {"type": "response", "time": 23.0, "message": "!! fail"},
            {"type": "response", "time": 24.0, "message": "// echo"},
        ]), None)
        entries = self.data.console_entries
        self.assertEqual(["ok", "!! fail", "// echo"], [entry["text"] for entry in entries])
        self.assertEqual([False, True, False], [entry["error"] for entry in entries])
        self.assertEqual([True, False, False], [entry["success"] for entry in entries])
        self.assertEqual(1, len(self.console_changes))
        # The skipped history stays skipped on the next poll.
        self.data.refresh_console_store()
        self.request_to("console-store").callback(self.store(
            [{"type": "response", "time": 5.0, "message": "stale"},
             {"type": "response", "time": 25.0, "message": "fresh"}]), None)
        self.assertEqual(["fresh"], [entry["text"] for entry in self.data.console_entries])

    def test_a_no_op_resume_verdict_settles_the_tracked_command(self):
        self.activate()
        self.data.set_console_expanded(True, 0.0)
        self.request_to("console-store").callback(self.store(
            [{"type": "response", "time": 5.0, "message": "Resume aborted: no paused print"}]), None)
        self.assertEqual([("Resume", "Nothing to resume")], self.client.settled)

    def test_an_empty_or_broken_store_reply_publishes_nothing(self):
        self.activate()
        self.data.set_console_expanded(True, 0.0)
        self.request_to("console-store").callback(self.store([]), None)
        self.assertEqual([], self.data.console_entries)
        self.request_to("console-store").callback("nonsense", None)
        self.request_to("console-store").callback({"result": {"gcode_store": "nope"}}, None)
        self.request_to("console-store").callback(self.store([{"type": "response"}]), "error")
        self.assertEqual(0, len(self.console_changes))

    def test_a_collapsed_console_polls_slowly_and_only_while_active(self):
        self.data.set_console_expanded(False)
        self.assertTrue(self.data._console_watch.isActive(), "the bell's feed arms on collapse")
        self.data.refresh_console_store = Mock()
        self.data._refresh_console_watch()
        self.assertEqual(0, self.data.refresh_console_store.call_count, "inactive: no poll")
        self.activate()
        self.data._refresh_console_watch()
        self.assertEqual(1, self.data.refresh_console_store.call_count)
        self.data.set_console_expanded(True, 0.0)     # the expand polls once itself
        expanded = self.data.refresh_console_store.call_count
        self.data._refresh_console_watch()
        self.assertEqual(expanded, self.data.refresh_console_store.call_count,
                         "expanded: the fast poll owns it")

    def test_a_collapsed_store_poll_is_forced_past_the_expansion_gate(self):
        self.activate()
        before = len(self.client.transport.sent)
        self.data.refresh_console_store()
        self.assertEqual(before, len(self.client.transport.sent))
        self.data.refresh_console_store(force=True)
        self.request_to("console-store")

    def test_a_re_expand_re_seeds_from_the_persisted_stamp(self):
        self.activate()
        self.data.set_console_expanded(True, 10.0)
        self.data.set_console_expanded(False)
        self.assertTrue(self.data._console_watch.isActive())
        self.data.set_console_expanded(True, 50.0)
        self.assertEqual(50.0, self.data._console_seed)


if __name__ == "__main__":
    unittest.main()
