"""Coverage tests for the transport-facing quartet: the protocol
helpers, the session state core, the HTTP/socket poller and the remote
file service.

The errors, retries, cancellation and identity edges live here: a stale
reply that must not adopt a rebind, a backoff window that must not be
hammered, a download that must not survive a printer switch, and the
exactly-once terminal contract of the file-manager lane. The Qt suites
drive the real signal wiring through the harness doubles
(``ScriptedTransport``, ``ScriptedSocket`` and the real writer thread),
so the terminal deliveries are observed where production observes them
— after a queued hop to the GUI thread — rather than by calling the
private handlers directly.

Leftover uncovered line: ``RemoteFileService.py:132``, the
file-listing-size guard in ``_OneShotDownload._terminal``. Only the
job lane sizes an operation against the listing (``op.expected`` is
assigned in ``_start_download`` alone), so on the one-shot lane the
guard is inert and the line cannot execute from any real caller. The
one-shot lane's own partial-file refusal is covered against
``op.size``, the declaration it does carry.
"""
from __future__ import annotations

import importlib
import os
import sys
import time
import unittest
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, ScriptedSocket, ScriptedTransport, runtime

from plugins import MoonrakerProtocol as protocol
from plugins.MoonrakerProtocol import RemoteFileIdentity
from plugins.MoonrakerSession import (
    BindingIdentity,
    CommandTracker,
    MoonrakerSession,
    MoonrakerSessionState,
    PollPolicy,
    RequestCategory,
    RequestCoalescer,
    SessionSnapshot,
)

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtNetwork import QNetworkReply


class _QtCase(unittest.TestCase):
    """Base for the classes that build real Qt objects."""

    rt = None  # installed by setUpModule

    @classmethod
    def setUpClass(cls):
        if not QT_AVAILABLE:
            raise unittest.SkipTest("PyQt6 is required")
        if _QtCase.rt is None:
            raise unittest.SkipTest("the Qt runtime harness did not start")

    def events(self, milliseconds=0):
        self.rt.events(milliseconds)


if QT_AVAILABLE:

    class FakeReply(QObject):
        """A scriptable QNetworkReply stand-in: the test decides which
        headers exist, which chunks arrive and what the error reads."""

        readyRead = pyqtSignal()
        finished = pyqtSignal()

        def __init__(self, *, pairs=(), headers=None, chunks=()):
            super().__init__()
            self._pairs = list(pairs)
            self._pairs_available = True
            self._headers = dict(headers or {})
            self._chunks = list(chunks)
            self._error = QNetworkReply.NetworkError.NoError
            self._error_string = ""
            self.buffer_size = None
            self.buffer_size_raises = False
            self.aborted = 0
            self.deleted = 0

        def setReadBufferSize(self, size):
            if self.buffer_size_raises:
                raise RuntimeError("this reply refuses a buffer cap")
            self.buffer_size = size

        def rawHeaderPairs(self):
            if not self._pairs_available:
                raise RuntimeError("the reply exposes no header pairs")
            return list(self._pairs)

        def rawHeader(self, name):
            return self._headers.get(bytes(name), b"")

        def readAll(self):
            data = b"".join(self._chunks)
            self._chunks = []
            return data

        def error(self):
            return self._error

        def errorString(self):
            return self._error_string

        def abort(self):
            self.aborted += 1

        def deleteLater(self):
            self.deleted += 1

        def isRunning(self):
            return False

        def push(self, chunk):
            self._chunks.append(chunk)

        def fail(self, string, error=None):
            self._error_string = string
            self._error = error or QNetworkReply.NetworkError.OperationCanceledError


    class ScriptedNetwork:
        """Serves queued replies to ``transport.network.get``."""

        def __init__(self):
            self.requests = []
            self.replies = []

        def get(self, request):
            self.requests.append(request)
            if not self.replies:
                raise RuntimeError("no scripted reply")
            return self.replies.pop(0)


class _RecordingTransport(ScriptedTransport):
    """ScriptedTransport with a switchable send_json verdict and the
    network seam the download lanes need."""

    def __init__(self):
        super().__init__()
        self.network = ScriptedNetwork()
        self.started = True

    def send_json(self, *args, **kwargs):
        if not self.started:
            return False
        return super().send_json(*args, **kwargs)


def setUpModule():
    if not QT_AVAILABLE:
        return
    _QtCase._runtime_cm = runtime()
    _QtCase.rt = _QtCase._runtime_cm.__enter__()


def tearDownModule():
    if not QT_AVAILABLE:
        return
    _QtCase._runtime_cm.__exit__(None, None, None)


# --------------------------------------------------------------------------
# MoonrakerProtocol
# --------------------------------------------------------------------------

class ErrorTextTests(unittest.TestCase):
    def test_a_short_single_line_message_is_used_as_is(self):
        self.assertEqual(protocol.moonraker_error_text({"message": "Extrude refused"}), "Extrude refused")

    def test_the_unknown_placeholder_falls_through_to_the_traceback_tail(self):
        payload = {
            "message": "Unknown",
            "traceback": "Traceback (most recent call last):\n  raise ServerError: Extrude below minimum temp\n",
        }
        self.assertEqual(protocol.moonraker_error_text(payload), "Extrude below minimum temp")

    def test_the_http_marker_drops_the_transport_code(self):
        payload = {"traceback": "HTTPError: HTTP 400: Extrude below minimum temp\n"}
        self.assertEqual(protocol.moonraker_error_text(payload), "Extrude below minimum temp")

    def test_an_httperror_line_without_a_code_is_returned_whole(self):
        payload = {"traceback": "HTTPError: connection reset by peer\n"}
        self.assertEqual(protocol.moonraker_error_text(payload), "connection reset by peer")

    def test_the_longest_usable_text_wins_when_the_tail_is_blank(self):
        # The marker is present but carries no text: the message fallback
        # must still produce the exception's LAST line, never its header.
        payload = {
            "message": "Traceback (most recent call last):\nline one\nline two",
            "traceback": "ServerError: \n",
        }
        self.assertEqual(protocol.moonraker_error_text(payload), "line two")

    def test_a_multi_line_message_carries_its_own_marker(self):
        # Some builds put the exception straight into `message`; the
        # same markers live there and the tail after them is the answer.
        payload = {"message": "Traceback (most recent call last):\nraise ServerError: Short on filament"}
        self.assertEqual(protocol.moonraker_error_text(payload), "Short on filament")

    def test_an_unusable_body_yields_no_text(self):
        self.assertEqual(protocol.moonraker_error_text({}), "")
        self.assertEqual(protocol.moonraker_error_text({"message": "Unknown"}), "")
        self.assertEqual(protocol.moonraker_error_text({"message": "Unknown", "traceback": "x ServerError: \n"}), "")


class SameOriginTests(unittest.TestCase):
    def test_a_matching_origin_passes(self):
        self.assertTrue(protocol.same_origin("http://printer:7125", "http://printer:7125/server/info"))

    def test_the_default_port_matches_an_explicit_one(self):
        self.assertTrue(protocol.same_origin("https://printer", "https://printer:443/x"))
        self.assertTrue(protocol.same_origin("http://printer", "http://printer:80/x"))

    def test_a_scheme_family_never_matches_a_secure_one(self):
        self.assertFalse(protocol.same_origin("https://printer", "http://printer/x"))
        self.assertFalse(protocol.same_origin("http://printer", "https://printer/x"))

    def test_a_websocket_scheme_pairs_with_its_http_family(self):
        self.assertTrue(protocol.same_origin("ws://printer:7125", "http://printer:7125/websocket"))
        self.assertTrue(protocol.same_origin("https://printer:7125", "wss://printer:7125/websocket"))

    def test_a_foreign_host_or_port_fails_closed(self):
        self.assertFalse(protocol.same_origin("http://printer:7125", "http://webcam:7125/x"))
        self.assertFalse(protocol.same_origin("http://printer:7125", "http://printer:8080/x"))

    def test_an_empty_side_fails_closed(self):
        self.assertFalse(protocol.same_origin("", "http://printer/x"))
        self.assertFalse(protocol.same_origin("http://printer", ""))
        self.assertFalse(protocol.same_origin("http:///x", "http://printer/x"))

    def test_an_unparseable_url_fails_closed(self):
        self.assertFalse(protocol.same_origin("http://[::1", "http://printer/x"))
        self.assertFalse(protocol.same_origin("http://printer", "http://[::1"))


class EndpointTests(unittest.TestCase):
    def test_the_feed_url_maps_https_to_wss_and_http_to_ws(self):
        self.assertEqual(protocol.websocket_endpoint("https://printer:7125/"), "wss://printer:7125/websocket")
        self.assertEqual(protocol.websocket_endpoint("http://printer:7125"), "ws://printer:7125/websocket")

    def test_an_unknown_scheme_has_no_feed_url(self):
        # A silent https->ws downgrade is forbidden: anything that is not
        # plainly http(s) fails closed instead of guessing a family.
        self.assertEqual(protocol.websocket_endpoint("ftp://printer"), "")
        self.assertEqual(protocol.websocket_endpoint(""), "")

    def test_a_hostless_http_url_still_maps(self):
        self.assertTrue(protocol.websocket_endpoint("http://").startswith("ws://"))

    def test_status_and_metadata_endpoints_carry_the_core_object_set(self):
        url = protocol.status_endpoint("http://printer:7125")
        self.assertTrue(url.startswith("http://printer:7125/printer/objects/query?"))
        for name in protocol.CORE_OBJECTS:
            self.assertIn(name, url)
        self.assertEqual(
            protocol.metadata_endpoint("http://printer:7125", "dir/my part.gcode"),
            "http://printer:7125/server/files/metadata?filename=dir/my%20part.gcode",
        )

    def test_a_download_endpoint_keeps_the_path_separators(self):
        self.assertEqual(
            protocol.download_endpoint("http://printer:7125", "/sub/dir/part.gcode"),
            "http://printer:7125/server/files/gcodes//sub/dir/part.gcode",
        )

    def test_the_print_start_path_is_root_exclusive(self):
        self.assertEqual(protocol.print_start_path("  /gcodes/part.gcode "), "gcodes/part.gcode")
        self.assertEqual(
            protocol.print_start_endpoint("http://printer:7125/", "/part.gcode"),
            "http://printer:7125/printer/print/start?filename=part.gcode",
        )
        self.assertEqual(protocol.print_start_endpoint("", "/part.gcode"), "printer/print/start?filename=part.gcode")

    def test_the_delete_endpoint_keeps_the_root(self):
        self.assertEqual(
            protocol.delete_endpoint("http://printer:7125/", "/gcodes/", "/sub/part.gcode"),
            "http://printer:7125/server/files/gcodes/sub/part.gcode",
        )
        self.assertEqual(protocol.delete_endpoint("", "gcodes", "part.gcode"), "server/files/gcodes/part.gcode")

    def test_the_directory_routes_carry_the_path_query(self):
        self.assertEqual(
            protocol.directory_delete_endpoint("http://printer:7125", "gcodes", "/old dir"),
            "http://printer:7125/server/files/directory?path=gcodes/old%20dir&force=true",
        )
        self.assertEqual(
            protocol.directory_create_endpoint("", "/gcodes", "new dir"),
            "server/files/directory?path=gcodes/new%20dir",
        )

    def test_the_move_route_has_no_filename(self):
        self.assertEqual(protocol.move_endpoint(""), "server/files/move")
        self.assertEqual(protocol.move_endpoint("http://printer:7125/"), "http://printer:7125/server/files/move")

    def test_the_informational_routes_strip_a_trailing_slash(self):
        self.assertEqual(protocol.server_info_endpoint("http://printer:7125/"), "http://printer:7125/server/info")
        self.assertEqual(protocol.objects_list_endpoint("http://printer:7125/"), "http://printer:7125/printer/objects/list")
        self.assertEqual(protocol.gcode_script_endpoint("http://printer:7125/"), "http://printer:7125/printer/gcode/script")


class FileIdentityTests(unittest.TestCase):
    def test_the_stable_key_prefers_size_and_modified_over_the_uuid(self):
        # Moonraker mints a fresh uuid per extraction, so a key built on
        # it would see a restart on every fetch.
        identity = RemoteFileIdentity("part.gcode", size=120, modified=5.5, uuid="fresh")
        self.assertEqual(identity.stable_key(), "file:part.gcode|size:120|modified:5.500000")
        self.assertEqual(RemoteFileIdentity("part.gcode", modified=1.0).stable_key(),
                         "file:part.gcode|size:0|modified:1.000000")

    def test_the_stable_key_falls_back_to_the_uuid_then_the_name(self):
        self.assertEqual(RemoteFileIdentity("part.gcode", uuid="abc").stable_key(), "uuid:abc")
        self.assertEqual(RemoteFileIdentity("part.gcode").stable_key(), "file:part.gcode")

    def test_a_job_match_needs_the_name_and_tolerates_an_unknown_size(self):
        identity = RemoteFileIdentity("part.gcode", size=120)
        self.assertTrue(identity.matches_job("part.gcode", 120))
        self.assertFalse(identity.matches_job("other.gcode", 120))
        self.assertFalse(identity.matches_job("part.gcode", 121))
        self.assertTrue(identity.matches_job("part.gcode", 0))
        self.assertTrue(RemoteFileIdentity("part.gcode").matches_job("part.gcode", 120))


class ParseFileIdentityTests(unittest.TestCase):
    def test_a_metadata_result_is_unwrapped(self):
        identity = protocol.parse_file_identity("a.gcode", {"result": {
            "filename": "b.gcode", "size": "42", "modified": "7.5", "uuid": "u1"}})
        self.assertEqual(identity, RemoteFileIdentity("b.gcode", 42, 7.5, "u1"))

    def test_a_bare_payload_and_a_non_mapping_payload_are_both_tolerated(self):
        self.assertEqual(protocol.parse_file_identity("a.gcode", {"size": 9}).size, 9)
        self.assertEqual(protocol.parse_file_identity("a.gcode", ["nonsense"], 12).size, 12)
        self.assertEqual(protocol.parse_file_identity("a.gcode", None, 12).size, 12)

    def test_unparseable_numbers_fall_back_instead_of_raising(self):
        identity = protocol.parse_file_identity("a.gcode", {"size": "big", "modified": "never"}, 30)
        self.assertEqual(identity.size, 30)
        self.assertEqual(identity.modified, 0.0)
        self.assertEqual(identity.filename, "a.gcode")
        self.assertEqual(identity.uuid, "")


class MotionPositionTests(unittest.TestCase):
    def test_a_live_position_needs_four_numbers(self):
        self.assertEqual(protocol.motion_live_position({"motion_report": {"live_position": [1, 2, 3, 4]}}),
                         (1.0, 2.0, 3.0, 4.0))
        self.assertIsNone(protocol.motion_live_position({"motion_report": {"live_position": [1, 2, 3]}}))
        self.assertIsNone(protocol.motion_live_position({"motion_report": {"live_position": "1,2,3,4"}}))
        self.assertIsNone(protocol.motion_live_position({"motion_report": {"live_position": [1, 2, 3, "x"]}}))
        self.assertIsNone(protocol.motion_live_position({"motion_report": None}))
        self.assertIsNone(protocol.motion_live_position("not a status"))

    def test_the_gcode_space_conversion_subtracts_the_homing_origin(self):
        position = protocol.live_position_in_gcode_space(
            {"live_position": [10.0, 20.0, 5.0, 1.0]},
            {"homing_origin": [1.0, 2.0, 3.0]},
        )
        self.assertEqual(position, (9.0, 18.0, 2.0))

    def test_an_axis_map_reorders_the_vector(self):
        position = protocol.live_position_in_gcode_space(
            {"live_position": [1.0, 2.0, 3.0]},
            {"axis_map": {"X": 2, "Y": 0, "Z": 1}},
        )
        self.assertEqual(position, (3.0, 1.0, 2.0))

    def test_a_missing_axis_map_entry_uses_the_conventional_axis(self):
        position = protocol.live_position_in_gcode_space({"live_position": [1.0, 2.0, 3.0]}, {"axis_map": {}})
        self.assertEqual(position, (1.0, 2.0, 3.0))
        # A non-numeric mapping is ignored rather than dropped.
        position = protocol.live_position_in_gcode_space(
            {"live_position": [1.0, 2.0, 3.0]}, {"axis_map": {"X": "first"}})
        self.assertEqual(position, (1.0, 2.0, 3.0))

    def test_an_out_of_range_or_unusable_axis_refuses_the_readout(self):
        self.assertIsNone(protocol.live_position_in_gcode_space({"live_position": [1.0, 2.0]},
                                                               {"axis_map": {"Z": 5}}))
        self.assertIsNone(protocol.live_position_in_gcode_space({"live_position": [1.0, 2.0]},
                                                               {"axis_map": {"X": -1}}))
        self.assertIsNone(protocol.live_position_in_gcode_space({"live_position": [1.0, 2.0, "x"]}))
        self.assertIsNone(protocol.live_position_in_gcode_space("not a report"))
        self.assertIsNone(protocol.live_position_in_gcode_space({"live_position": "nope"}))

    def test_a_short_or_unusable_origin_contributes_no_offset(self):
        self.assertEqual(protocol.live_position_in_gcode_space({"live_position": [1.0, 2.0, 3.0]},
                                                              {"homing_origin": "nope"}),
                         (1.0, 2.0, 3.0))
        self.assertEqual(protocol.live_position_in_gcode_space({"live_position": [1.0, 2.0, 3.0]},
                                                              {"homing_origin": [1.0, "x"]}),
                         (0.0, 2.0, 3.0))


# --------------------------------------------------------------------------
# MoonrakerSession — the pure state core
# --------------------------------------------------------------------------

class PollPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = PollPolicy()

    def test_the_core_interval_follows_the_printer_state(self):
        self.assertEqual(self.policy.interval_ms(RequestCategory.CORE, 800, "printing"), 800)
        self.assertEqual(self.policy.interval_ms(RequestCategory.CORE, 800, "PAUSED "), 1500)
        self.assertEqual(self.policy.interval_ms(RequestCategory.CORE, 800, "idle"), 5000)
        self.assertEqual(self.policy.interval_ms(RequestCategory.CORE, 9000, "idle"), 9000)

    def test_urgent_wins_over_every_floor(self):
        self.assertEqual(self.policy.interval_ms(RequestCategory.CORE, 800, "paused", urgent=True), 250)

    def test_a_zero_configured_interval_still_ticks(self):
        self.assertEqual(self.policy.interval_ms(RequestCategory.CORE, 0, "printing"), 1)

    def test_the_fixed_categories_ignore_the_printer_state(self):
        self.assertEqual(self.policy.interval_ms(RequestCategory.AUXILIARY, 100), 250)
        self.assertEqual(self.policy.interval_ms(RequestCategory.POWER, 1), 5000)
        self.assertEqual(self.policy.interval_ms(RequestCategory.SYSTEM, 1), 10000)
        self.assertEqual(self.policy.interval_ms(RequestCategory.ENDSTOPS, 1), 10000)
        self.assertEqual(self.policy.interval_ms(RequestCategory.DISCOVERY, 1), 30000)

    def test_the_console_floor_relaxes_once_the_printer_is_idle(self):
        self.assertEqual(self.policy.interval_ms(RequestCategory.CONSOLE, 100, "printing"), 250)
        self.assertEqual(self.policy.interval_ms(RequestCategory.CONSOLE, 100, "paused"), 250)
        self.assertEqual(self.policy.interval_ms(RequestCategory.CONSOLE, 100, "idle"), 5000)

    def test_an_unlisted_category_passes_the_configured_value_through(self):
        self.assertEqual(self.policy.interval_ms(RequestCategory.STATIC, 4000), 4000)
        self.assertEqual(self.policy.interval_ms("command", 4000, "printing"), 4000)


class RequestCoalescerTests(unittest.TestCase):
    def test_a_busy_slot_refuses_a_plain_begin(self):
        coalescer = RequestCoalescer()
        self.assertTrue(coalescer.begin("core"))
        self.assertFalse(coalescer.begin("core"))
        self.assertTrue(coalescer.is_in_flight("core"))
        self.assertFalse(coalescer.complete("core"))

    def test_a_forced_begin_while_busy_queues_exactly_one_follow_up(self):
        coalescer = RequestCoalescer()
        coalescer.begin("core")
        coalescer.begin("core", force=True)
        coalescer.begin("core", force=True)
        self.assertTrue(coalescer.complete("core"))
        self.assertFalse(coalescer.complete("core"))

    def test_cancel_and_clear_retire_the_in_flight_mark(self):
        coalescer = RequestCoalescer()
        coalescer.begin("core")
        coalescer.cancel("core")
        self.assertFalse(coalescer.is_in_flight("core"))
        self.assertTrue(coalescer.begin("core"))
        coalescer.clear()
        self.assertFalse(coalescer.is_in_flight("core"))

    def test_completing_an_unknown_slot_is_harmless(self):
        self.assertFalse(RequestCoalescer().complete("never-started"))


class CommandTrackerTests(unittest.TestCase):
    def test_an_issue_normalises_the_expected_states(self):
        tracker = CommandTracker()
        command = tracker.issue("pause", ["Paused", " ", "printing"], timeout_s=2.0, revision=7)
        self.assertEqual(command.expected_states, {"paused", "printing"})
        self.assertEqual(command.issued_revision, 7)
        self.assertTrue(tracker.has_pending)
        self.assertIs(tracker.get("pause"), command)

    def test_an_accepted_command_without_expectations_is_immediately_confirmed(self):
        tracker = CommandTracker()
        tracker.issue("resume")
        command = tracker.accepted("resume")
        self.assertTrue(command.terminal)
        self.assertEqual(command.outcome, "confirmed")

    def test_an_accepted_command_with_expectations_waits_for_the_state(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["paused"])
        accepted = tracker.accepted("pause")
        self.assertFalse(accepted.terminal)
        self.assertEqual(accepted.outcome, "accepted")
        self.assertTrue(accepted.http_accepted)

    def test_an_unknown_or_terminal_accept_is_passed_back_unchanged(self):
        tracker = CommandTracker()
        self.assertIsNone(tracker.accepted("ghost"))
        tracker.issue("pause", ["paused"])
        tracker.failed("pause", "refused")
        self.assertTrue(tracker.accepted("pause").terminal)

    def test_observation_confirms_only_against_the_expected_state(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["paused"], timeout_s=30.0, now=0.0)
        tracker.accepted("pause")
        self.assertEqual(tracker.observe("printing", now=1.0), [])
        self.assertEqual(len(tracker.observe("paused", now=1.0)), 1)
        self.assertEqual(tracker.observe("paused", now=1.0), [])  # already terminal

    def test_an_accepted_command_times_out_with_its_own_words(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["paused"], timeout_s=5.0, now=0.0)
        tracker.accepted("pause")
        changed = tracker.observe("printing", now=6.0)
        self.assertEqual(changed[0].outcome, "timed_out")
        self.assertEqual(changed[0].detail,
                         "Moonraker accepted the command, but the expected printer state was not observed")

    def test_an_unacknowledged_command_times_out_with_the_other_words(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["paused"], timeout_s=5.0, now=0.0)
        changed = tracker.expire(now=6.0)
        self.assertEqual(changed[0].outcome, "timed_out")
        self.assertEqual(changed[0].detail, "Moonraker did not acknowledge the command in time")
        self.assertFalse(tracker.has_pending)

    def test_expiring_an_already_terminal_command_changes_nothing(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["paused"], timeout_s=5.0, now=0.0)
        tracker.failed("pause", "refused")
        self.assertEqual(tracker.expire(now=60.0), [])

    def test_a_failure_for_an_untracked_command_still_lands(self):
        # The printer can answer a command the UI never tracked (a macro
        # the operator ran elsewhere); the failure must not vanish.
        tracker = CommandTracker()
        command = tracker.failed("pause", "")
        self.assertEqual(command.name, "pause")
        self.assertTrue(command.terminal)
        self.assertEqual(command.detail, "Command failed")

    def test_settling_is_terminal_but_never_a_failure(self):
        tracker = CommandTracker()
        tracker.issue("resume")
        command = tracker.settled("resume", "")
        self.assertTrue(command.terminal)
        self.assertEqual(command.outcome, "confirmed")
        self.assertEqual(command.detail, "Nothing to do")
        self.assertIsNone(tracker.settled("ghost", "x"))
        self.assertTrue(tracker.settled("resume", "again").terminal)

    def test_expire_non_terminal_retires_the_previous_print_s_commands(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["paused"])
        tracker.issue("resume")
        tracker.failed("resume", "nope")
        changed = tracker.expire_non_terminal("superseded by a new print")
        self.assertEqual([c.name for c in changed], ["pause"])
        self.assertEqual(changed[0].outcome, "failed")
        self.assertEqual(changed[0].detail, "superseded by a new print")
        self.assertEqual(tracker.expire_non_terminal("again"), [])

    def test_as_dict_is_a_sorted_snapshot(self):
        tracker = CommandTracker()
        tracker.issue("pause", ["Paused", "printing"])
        payload = tracker.issue("pause", ["Paused", "printing"]).as_dict()
        self.assertEqual(payload["expected_states"], ["paused", "printing"])
        self.assertEqual(payload["outcome"], "pending")

    def test_clear_empties_the_tracker(self):
        tracker = CommandTracker()
        tracker.issue("pause")
        tracker.clear()
        self.assertFalse(tracker.has_pending)
        self.assertIsNone(tracker.get("pause"))


class SessionSnapshotTests(unittest.TestCase):
    def test_merging_patches_nested_objects_and_counts_revisions(self):
        snapshot = SessionSnapshot()
        snapshot.merge_status({"print_stats": {"state": "printing", "print_duration": 5}}, now=1.0)
        merged = snapshot.merge_status({"print_stats": {"print_duration": 9}}, now=2.0)
        self.assertEqual(merged["print_stats"], {"state": "printing", "print_duration": 9})
        self.assertEqual(snapshot.revision, 2)
        self.assertEqual(snapshot.updated_at, 2.0)

    def test_a_non_mapping_patch_is_refused_without_a_revision_bump(self):
        snapshot = SessionSnapshot()
        self.assertEqual(snapshot.merge_status(["nonsense"]), {})
        self.assertEqual(snapshot.revision, 0)

    def test_a_scalar_object_is_stored_and_copied_defensively(self):
        snapshot = SessionSnapshot()
        payload = {"power": {"devices": [{"name": "printer"}]}}
        snapshot.merge_status(payload)
        payload["power"]["devices"][0]["name"] = "mutated"
        self.assertEqual(snapshot.status["power"]["devices"][0]["name"], "printer")
        copy = snapshot.copy_status()
        copy["power"]["devices"][0]["name"] = "mutated again"
        self.assertEqual(snapshot.status["power"]["devices"][0]["name"], "printer")

    def test_the_printer_state_reads_the_word_or_nothing(self):
        snapshot = SessionSnapshot()
        self.assertEqual(snapshot.printer_state, "")
        snapshot.merge_status({"print_stats": "garbage"})
        self.assertEqual(snapshot.printer_state, "")
        snapshot.merge_status({"print_stats": {"state": " Printing "}})
        self.assertEqual(snapshot.printer_state, "printing")


class SessionStateTests(unittest.TestCase):
    def test_the_guards_report_a_real_transition_only(self):
        state = MoonrakerSessionState()
        self.assertTrue(state.set_pause_guard(True))
        self.assertFalse(state.set_pause_guard(True))
        self.assertTrue(state.set_pause_guard(False))
        self.assertTrue(state.set_toolhead_guard("yes"))
        self.assertFalse(state.set_toolhead_guard(True))

    def test_a_reset_clears_the_state_but_keeps_the_policy(self):
        policy = PollPolicy()
        state = MoonrakerSessionState(poll_policy=policy)
        state.connected = True
        state.feed_mode = "websocket"
        state.assume_print_stopped = True
        state.assume_print_duration = 30.0
        state.set_pause_guard(True)
        state.set_toolhead_guard(True)
        state.snapshot.merge_status({"print_stats": {"state": "printing"}})
        state.commands.issue("pause")
        state.coalescer.begin("core")
        state.reset()
        self.assertIs(state.poll_policy, policy)
        self.assertEqual(state.generation, 1)
        self.assertFalse(state.connected)
        self.assertFalse(state.pause_guard)
        self.assertFalse(state.toolhead_guard)
        self.assertEqual(state.feed_mode, "http")
        self.assertFalse(state.assume_print_stopped)
        self.assertIsNone(state.assume_print_duration)
        self.assertEqual(state.snapshot.status, {})
        self.assertFalse(state.commands.has_pending)
        self.assertFalse(state.coalescer.is_in_flight("core"))

    def test_a_merge_confirms_only_against_the_patch_s_own_state(self):
        state = MoonrakerSessionState()
        state.merge_status({"print_stats": {"state": "paused"}})
        state.commands.issue("pause", ["paused"])
        state.commands.accepted("pause")
        # The cached snapshot reads paused, but an unrelated partial patch
        # must not confirm the command with it.
        status, changed = state.merge_status({"gcode_move": {"speed": 10}}, now=0.0)
        self.assertEqual(changed, [])
        self.assertIn("gcode_move", status)
        self.assertTrue(state.commands.has_pending)
        status, changed = state.merge_status({"print_stats": {"state": "paused"}})
        self.assertEqual([command.name for command in changed], ["pause"])


class MoonrakerSessionBindingTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.socket = Mock()
        self.session = MoonrakerSession(transport=self.transport, socket=self.socket)

    def test_a_first_configure_binds_and_reconfigures_the_transport(self):
        self.assertTrue(self.session.configure("http://printer:7125/", "key"))
        self.transport.configure.assert_called_once_with("http://printer:7125", "key")
        self.socket.stop.assert_called_once()
        self.assertEqual(self.session.identity, BindingIdentity("http://printer:7125", "key", "http"))
        self.assertEqual(self.session.api_key, "key")
        self.assertEqual(self.session.base_url, "http://printer:7125")

    def test_an_unchanged_configure_is_a_no_op(self):
        self.session.configure("http://printer:7125", "key")
        self.transport.reset_mock()
        self.socket.reset_mock()
        self.assertFalse(self.session.configure("http://printer:7125/", "key"))
        self.transport.configure.assert_not_called()
        self.socket.stop.assert_not_called()

    def test_a_mode_only_change_rebinds_without_touching_the_transport(self):
        self.session.configure("http://printer:7125", "key")
        self.transport.reset_mock()
        self.socket.reset_mock()
        self.assertTrue(self.session.configure("http://printer:7125", "key", "  WebSocket "))
        self.transport.configure.assert_not_called()
        self.assertEqual(self.session.feed_mode, "websocket")
        self.socket.stop.assert_called_once()

    def test_a_blank_mode_keeps_the_current_one(self):
        self.session.configure("http://printer:7125", "key", "websocket")
        self.assertTrue(self.session.configure("http://printer:7126", "key", ""))
        self.assertEqual(self.session.feed_mode, "websocket")

    def test_the_connected_and_guard_accessors_reach_the_state(self):
        self.session.connected = True
        self.assertTrue(self.session.connected)
        self.session.set_pause_guard(True)
        self.session.set_toolhead_guard(True)
        self.assertTrue(self.session.pause_guard)
        self.assertTrue(self.session.toolhead_guard)
        self.assertEqual(self.session.generation, 0)
        self.assertIsInstance(self.session.poll_policy, PollPolicy)
        self.assertIsInstance(self.session.coalescer, RequestCoalescer)
        self.assertIsInstance(self.session.snapshot, SessionSnapshot)
        self.assertIsInstance(self.session.commands, CommandTracker)

    def test_reset_cancels_every_lane(self):
        self.session.reset()
        self.transport.cancel_all.assert_called_once()
        self.socket.stop.assert_called_once()
        self.assertEqual(self.session.generation, 1)

    def test_merge_status_delegates_to_the_state(self):
        self.session.configure("http://printer:7125", "key")
        status, changed = self.session.merge_status({"print_stats": {"state": "printing"}})
        self.assertEqual(status["print_stats"]["state"], "printing")
        self.assertEqual(changed, [])


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 is required")
class MoonrakerClientHostImportTests(unittest.TestCase):
    def test_the_module_imports_without_uranium(self):
        # The stdlib-only host suite has no UM package: the client must
        # still import, with logging off rather than an ImportError.
        module = importlib.import_module("plugins.MoonrakerClient")
        self.assertTrue(hasattr(module, "MoonrakerClient"))
        with patch.dict(sys.modules, {"UM.Logger": None}):
            module = importlib.reload(module)
        self.assertIsNone(module.Logger)
        # Drop the UM-mocked copy: a later suite must import its own.
        sys.modules.pop("plugins.MoonrakerClient", None)


class MoonrakerSessionDefaultsTests(_QtCase):
    def test_a_session_builds_its_own_transport_and_socket(self):
        module = self.rt.load("MoonrakerSession")
        session = module.MoonrakerSession()
        self.assertIsInstance(session.transport, self.rt.load("MoonrakerTransport").MoonrakerHttpTransport)
        self.assertIsInstance(session.socket, self.rt.load("MoonrakerSocket").MoonrakerSocket)
        session.transport.close()


# --------------------------------------------------------------------------
# MoonrakerClient
# --------------------------------------------------------------------------

class _ClientCase(_QtCase):
    def setUp(self):
        self.mod = self.rt.load("MoonrakerClient")
        self.state_mod = self.rt.load("MoonrakerSession")
        self.transport = _RecordingTransport()
        self.socket = ScriptedSocket()
        self.client = self.mod.MoonrakerClient(
            session=self.state_mod.MoonrakerSessionState(),
            transport=self.transport,
            socket=self.socket,
            proof_timeout_ms=40,
        )
        self.connections = []
        self.client.connectionChanged.connect(lambda ok, text: self.connections.append((ok, text)))
        self.statuses = []
        self.client.statusReceived.connect(self.statuses.append)
        self.commands = []
        self.client.commandChanged.connect(self.commands.append)
        self.invalidated = []
        self.client.sessionInvalidated.connect(lambda: self.invalidated.append(1))

    def tearDown(self):
        self.client.stop()

    def configure(self, url="http://printer:7125", key="key", interval=750, **kwargs):
        self.client.configure(url, key, interval, **kwargs)

    def last_request(self, owner=None):
        for request in reversed(self.transport.requests):
            if owner is None or request.owner == owner:
                return request
        raise AssertionError("no %r request was issued" % (owner,))

    def deliver(self, payload, error=None, owner=None):
        self.last_request(owner).callback(payload, error)

    def status(self, state, **stats):
        body = {"state": state}
        body.update(stats)
        return {"print_stats": body}


class ClientConfigurationTests(_ClientCase):
    def test_the_first_configure_binds_the_session_and_leaves_it_stopped(self):
        self.configure()
        self.assertEqual(self.transport.identity, ("http://printer:7125", "key"))
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.client.configured_feed_mode, "http")
        # The rebind point signals unconditionally: an upload can be in
        # flight even when the poller was never enabled.
        self.assertEqual(self.invalidated, [1])
        self.assertFalse(self.client.connected)

    def test_an_endpoint_change_invalidates_the_session_even_when_idle(self):
        self.configure()
        self.configure(url="http://other:7125")
        self.assertEqual(self.invalidated, [1, 1])

    def test_a_poll_interval_change_alone_does_not_rebind(self):
        self.configure(interval=500)
        self.configure(interval=900)
        self.assertEqual(self.invalidated, [1])
        self.assertEqual(self.client._poll_interval_ms, 900)
        self.assertEqual(self.client.transport.identity, ("http://printer:7125", "key"))

    def test_unparseable_and_out_of_range_intervals_are_clamped(self):
        self.configure(interval="never", aux_interval_ms=10, console_interval_ms=999999)
        self.assertEqual(self.client._poll_interval_ms, 750)
        self.assertEqual(self.client.aux_interval_ms, 250)
        self.assertEqual(self.client.console_interval_ms, 60000)
        self.configure(aux_interval_ms="nope", console_interval_ms=None)
        self.assertEqual(self.client.aux_interval_ms, 250)

    def test_a_running_poller_restarts_across_a_rebind(self):
        self.configure()
        self.client.start()
        self.transport.requests.clear()
        self.configure(url="http://other:7125")
        self.assertTrue(self.client._enabled)
        self.assertEqual(self.invalidated, [1, 1])
        self.assertIsNotNone(self.last_request("core"))

    def test_a_mode_change_rebinds_the_feed(self):
        self.configure()
        self.configure(feed_mode="websocket")
        self.assertEqual(self.client.effective_feed_mode, "websocket")


class ClientHttpPollingTests(_ClientCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.client.start()

    def test_start_is_idempotent_and_issues_the_first_poll(self):
        self.assertTrue(self.client._enabled)
        before = len(self.transport.requests)
        self.client.start()
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(self.connections[0], (False, "Connecting to Moonraker"))

    def test_a_successful_status_connects_and_applies_the_idle_floor(self):
        self.deliver({"result": {"status": self.status("idle")}})
        self.assertTrue(self.client.connected)
        self.assertEqual(self.connections[-1], (True, "Moonraker connected over HTTP polling"))
        self.assertEqual(self.client.status["print_stats"]["state"], "idle")
        # The idle floor applies only once a status has ever landed.
        self.assertEqual(self.client._poll_timer.interval(), 5000)

    def test_the_first_poll_runs_at_the_configured_cadence(self):
        self.assertEqual(self.client._poll_timer.interval(), 750)

    def test_a_transport_error_walks_the_failure_ladder(self):
        self.deliver(None, "connection refused")
        self.assertFalse(self.client.connected)
        self.assertTrue(self.connections[-1][1].startswith("Moonraker request failed: connection refused; retrying in "))
        # Never connected: the eager first-connect cadence, not the ladder.
        self.assertEqual(self.client._retry_delay_ms, 250)
        self.assertEqual(self.client._poll_timer.interval(), 250)

    def test_the_ladder_escalates_once_the_printer_has_answered(self):
        self.deliver({"result": {"status": self.status("idle")}})
        self.client._retry_not_before = 0.0
        self.client.force_refresh()
        self.deliver(None, "gone")
        self.assertEqual(self.client._retry_delay_ms, 5000)

    def test_each_failure_climbs_the_ladder_and_the_first_connect_cadence_holds(self):
        # Until the printer has ever answered, the backoff stays eager
        # whatever the ladder index; the ladder protects a PROVEN
        # endpoint's outages.
        for expected_index in (1, 2, 3, 4, 4):
            self.client._retry_not_before = 0.0
            self.client.force_refresh()
            self.deliver(None, "gone")
            self.assertEqual(self.client._retry_index, expected_index)
            self.assertEqual(self.client._retry_delay_ms, 250)

    def test_a_success_clears_the_ladder(self):
        self.deliver(None, "gone")
        self.deliver(None, "gone")
        self.assertEqual(self.client._retry_index, 2)
        self.client._retry_not_before = 0.0
        self.client.force_refresh()
        self.deliver({"result": {"status": self.status("idle")}})
        self.assertEqual(self.client._retry_index, 0)
        self.assertEqual(self.client._retry_delay_ms, 0)

    def test_a_long_reason_is_bounded(self):
        self.deliver(None, "x" * 400)
        reason, _, retry = self.connections[-1][1].partition("; retrying")
        self.assertEqual(len(reason), 161)
        self.assertTrue(reason.endswith("…"))
        self.assertEqual(retry, " in 0.25s")

    def test_an_invalid_status_body_is_rejected(self):
        self.deliver({"result": {"status": "printing"}})
        self.assertTrue(self.connections[-1][1].startswith("Moonraker returned an invalid status response"))
        self.client._retry_not_before = 0.0
        self.client.force_refresh()
        self.deliver({"result": {"status": {"print_stats": "printing"}}})
        self.assertTrue(self.connections[-1][1].startswith("Moonraker returned an invalid status response"))

    def test_a_missing_result_is_rejected(self):
        self.deliver({})
        self.assertTrue(self.connections[-1][1].startswith("Moonraker returned an invalid status response"))

    def test_a_subscriber_that_raises_is_reported_as_a_status_error(self):
        with patch.object(self.client, "admit_status", side_effect=RuntimeError("bad merge")):
            self.deliver({"result": {"status": self.status("printing")}})
        self.assertEqual(self.connections[-1][1].split(";")[0], "Moonraker status error: bad merge")

    def test_a_settings_only_change_does_not_poll(self):
        self.client.stop()
        self.transport.requests.clear()
        self.client._retry_not_before = 0.0
        self.client.force_refresh()
        self.assertEqual(self.transport.requests, [])

    def test_a_poll_inside_the_backoff_window_is_skipped(self):
        self.deliver(None, "refused")
        self.transport.requests.clear()
        self.client.force_refresh()
        self.assertEqual(self.transport.requests, [])

    def test_a_refused_transport_send_releases_the_coalescer_slot(self):
        self.deliver({"result": {"status": self.status("idle")}})
        self.transport.started = False
        self.client._retry_not_before = 0.0
        self.client.force_refresh()
        self.assertFalse(self.client._session.coalescer.is_in_flight("core"))

    def test_a_forced_poll_queues_exactly_one_follow_up(self):
        self.client.force_refresh()  # slot is in flight; the callback has not run
        self.client.force_refresh()
        self.deliver({"result": {"status": self.status("idle")}})
        self.events(20)
        # The first poll plus ONE follow-up, however many forced ticks ran.
        self.assertEqual(len(self.transport.requests), 2)

    def test_a_stale_generation_reply_is_dropped(self):
        self.client.force_refresh()
        callback = self.last_request("core").callback
        self.client.stop()
        self.transport.requests.clear()
        callback({"result": {"status": self.status("printing")}}, None)
        self.assertEqual(self.statuses, [])

    def test_stop_reports_a_connected_poller_going_down(self):
        self.deliver({"result": {"status": self.status("idle")}})
        self.client.stop()
        self.assertEqual(self.connections[-1], (False, "Moonraker polling stopped"))
        self.assertFalse(self.client.connected)
        self.assertIn(("core", None), self.transport.cancelled)
        self.assertFalse(self.client._poll_timer.isActive())

    def test_stop_without_a_session_reset_keeps_the_subscribers_quiet(self):
        self.deliver({"result": {"status": self.status("idle")}})
        before = len(self.invalidated)
        self.client.stop(reset_session=False)
        self.assertEqual(len(self.invalidated), before)
        self.assertFalse(self.client._enabled)

    def test_set_trace_http_reaches_the_transport(self):
        self.client.set_trace_http(True)
        self.assertTrue(self.transport.trace_http)

    def test_the_transport_and_session_accessors_expose_the_live_objects(self):
        self.assertIs(self.client.transport, self.transport)
        self.assertIs(self.client.session.transport, self.transport)
        self.assertIsInstance(self.client.transport_metrics, dict)


class ClientAdmissionTests(_ClientCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.client.start()

    def test_a_stale_sync_stamp_is_dropped_whole(self):
        self.client.admit_status(self.status("printing"), origin="sync", stamp=10.0,
                                 generation=self.client._generation)
        self.client.admit_status(self.status("paused"), origin="sync", stamp=5.0,
                                 generation=self.client._generation)
        self.assertEqual(self.client.status["print_stats"]["state"], "printing")

    def test_a_fragment_applies_inside_its_generation(self):
        self.client.admit_status(self.status("printing"), origin="sync", stamp=10.0,
                                 generation=self.client._generation)
        self.client.admit_status({"print_stats": {"state": "paused"}}, origin="fragment", stamp=1.0,
                                 generation=self.client._generation)
        self.assertEqual(self.client.status["print_stats"]["state"], "paused")

    def test_an_old_generation_admission_is_dropped(self):
        self.client.admit_status(self.status("printing"), origin="sync", stamp=10.0,
                                 generation=self.client._generation + 1)
        self.assertEqual(self.statuses, [])

    def test_an_unusable_stamp_falls_back_to_now(self):
        self.client.admit_status(self.status("printing"), origin="sync", stamp="never",
                                 generation=self.client._generation)
        # The status plus the connect transition's rebroadcast.
        self.assertEqual(len(self.statuses), 2)
        self.assertEqual(self.statuses[-1]["print_stats"]["state"], "printing")

    def test_the_connect_transition_rebroadcasts_the_snapshot(self):
        self.client.admit_status(self.status("idle"), origin="sync", stamp=1.0, generation=self.client._generation)
        self.assertEqual(len(self.statuses), 2)
        self.client.admit_status(self.status("idle"), origin="sync", stamp=2.0, generation=self.client._generation)
        self.assertEqual(len(self.statuses), 3)

    def test_the_capabilities_track_what_the_printer_actually_reports(self):
        self.client.admit_status({
            "print_stats": {"state": "printing", "info": {"current_layer": 4}},
            "virtual_sdcard": {"file_position": 12},
            "motion_report": {"live_position": [0, 0, 0, 0]},
        }, origin="sync", stamp=1.0, generation=self.client._generation)
        capabilities = self.client.capabilities
        self.assertTrue(capabilities["current_layer"])
        self.assertTrue(capabilities["file_position"])
        self.assertTrue(capabilities["motion_report"])
        self.assertIn("virtual_sdcard", capabilities["objects"])

    def test_a_composed_session_is_used_as_given(self):
        session = self.state_mod.MoonrakerSession(transport=_RecordingTransport(), socket=ScriptedSocket())
        client = self.mod.MoonrakerClient(session=session, transport=_RecordingTransport(), socket=ScriptedSocket())
        self.addCleanup(client.stop)
        self.assertIs(client.session, session)
        client.configure("http://printer:7125", "key", 750)
        self.assertEqual(session.identity, self.state_mod.BindingIdentity("http://printer:7125", "key", "http"))

    def test_a_subscriber_that_rebinds_during_the_connect_stops_the_admission(self):
        seen = []

        def rebind(_ok, _text):
            if self.client._enabled:
                seen.append(1)
                self.client.stop()

        self.client.connectionChanged.connect(rebind)
        self.client.admit_status(self.status("printing"), origin="sync", stamp=1.0,
                                 generation=self.client._generation)
        # The connect transition ran, then the guard stopped the rest of
        # the admission: no capabilities were published for the old session.
        self.assertEqual(len(seen), 1)
        self.assertFalse(self.client._enabled)
        self.assertEqual(self.statuses, [])

    def test_a_capabilities_handler_that_rebinds_stops_the_admission(self):
        seen = []

        def rebind(_capabilities):
            seen.append(1)
            self.client._generation += 1

        self.client.capabilitiesChanged.connect(rebind)
        self.client.admit_status(self.status("printing"), origin="fragment", stamp=1.0,
                                 generation=self.client._generation)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.statuses, [])

    def test_the_capabilities_clear_when_the_objects_go_away(self):
        self.client.admit_status({"print_stats": {"state": "idle"}}, origin="sync", stamp=1.0,
                                 generation=self.client._generation)
        capabilities = self.client.capabilities
        self.assertFalse(capabilities["current_layer"])
        self.assertFalse(capabilities["file_position"])
        self.assertFalse(capabilities["motion_report"])


class ClientEstopTests(_ClientCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.client.start()
        self.observe({"print_stats": {"state": "printing", "print_duration": 100.0}})

    def observe(self, patch):
        self.client.admit_status(patch, origin="fragment", stamp=1.0, generation=self.client._generation)

    def test_the_assumption_rewrites_the_state_and_latches_the_duration(self):
        self.client.assume_print_stopped()
        self.assertTrue(self.client.assumed_stopped)
        self.assertEqual(self.statuses[-1]["print_stats"]["state"], "cancelled")
        self.assertEqual(self.client.session.state.assume_print_duration, 100.0)

    def test_the_assumption_is_idempotent(self):
        self.client.assume_print_stopped()
        before = len(self.statuses)
        self.client.assume_print_stopped()
        self.assertEqual(len(self.statuses), before)

    def test_the_assumption_never_engages_over_an_idle_snapshot(self):
        self.client.stop()
        idle = self.mod.MoonrakerClient(session=self.state_mod.MoonrakerSessionState(),
                                        transport=_RecordingTransport(), socket=ScriptedSocket())
        self.addCleanup(idle.stop)
        idle.admit_status({"print_stats": {"state": "idle"}}, origin="sync", stamp=1.0, generation=idle._generation)
        emitted = []
        idle.statusReceived.connect(emitted.append)
        idle.assume_print_stopped()
        self.assertEqual(emitted, [])
        self.assertTrue(idle.assumed_stopped)

    def test_a_frozen_duration_keeps_the_rewrite(self):
        self.client.assume_print_stopped()
        self.statuses.clear()
        self.observe({"print_stats": {"state": "printing", "print_duration": 100.0}})
        self.assertEqual(self.statuses[-1]["print_stats"]["state"], "cancelled")
        self.assertTrue(self.client.assumed_stopped)

    def test_a_lower_duration_arms_a_restart(self):
        self.client.assume_print_stopped()
        self.observe({"print_stats": {"state": "printing", "print_duration": 5.0}})
        self.assertFalse(self.client.assumed_stopped)
        self.assertEqual(self.statuses[-1]["print_stats"]["state"], "printing")

    def test_a_real_non_printing_state_releases_the_assumption(self):
        self.client.assume_print_stopped()
        self.observe({"print_stats": {"state": "complete"}})
        self.assertFalse(self.client.assumed_stopped)

    def test_an_unparseable_duration_keeps_the_rewrite(self):
        self.client.assume_print_stopped()
        self.observe({"print_stats": {"state": "printing", "print_duration": "soon"}})
        # No comparable duration means no evidence of a new print, so the
        # assumption must survive it.
        self.assertEqual(self.statuses[-1]["print_stats"]["state"], "cancelled")
        self.assertTrue(self.client.assumed_stopped)

    def test_an_unparseable_duration_never_latches_the_assumption(self):
        client = self.mod.MoonrakerClient(session=self.state_mod.MoonrakerSessionState(),
                                          transport=_RecordingTransport(), socket=ScriptedSocket())
        self.addCleanup(client.stop)
        client.admit_status({"print_stats": {"state": "printing", "print_duration": "soon"}},
                            origin="sync", stamp=1.0, generation=client._generation)
        emitted = []
        client.statusReceived.connect(emitted.append)
        client.assume_print_stopped()
        self.assertIsNone(client.session.state.assume_print_duration)
        self.assertEqual(emitted[-1]["print_stats"]["state"], "cancelled")

    def test_a_new_print_expires_the_previous_print_s_commands(self):
        self.client.track_command("pause", ["paused"])
        self.commands.clear()
        self.observe({"print_stats": {"state": "idle"}})
        self.observe({"print_stats": {"state": "printing"}})
        self.assertEqual([command["name"] for command in self.commands], ["pause"])
        self.assertEqual(self.commands[-1]["detail"], "superseded by a new print")


class ClientCommandTests(_ClientCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.client.start()

    def test_a_tracked_command_is_announced_and_the_deadline_runs(self):
        self.client.track_command("pause", ["paused"], timeout_s=30.0)
        self.assertEqual(self.commands[-1]["name"], "pause")
        self.assertTrue(self.client._command_timer.isActive())

    def test_an_ack_of_a_command_the_snapshot_already_confirmed_is_reobserved(self):
        self.observe({"print_stats": {"state": "printing"}})
        self.client.track_command("pause", ["paused"])
        # The confirming frame landed BEFORE the ack (Klipper pushes the
        # state once): the ack must re-observe rather than time out.
        self.observe({"print_stats": {"state": "paused"}})
        self.assertTrue(self.client._session.commands.has_pending)
        self.commands.clear()
        self.client.accept_command("pause")
        self.assertEqual(self.commands[-1]["outcome"], "confirmed")

    def test_an_ack_of_a_command_with_no_new_state_waits(self):
        self.observe({"print_stats": {"state": "printing"}})
        self.client.track_command("pause", ["paused"])
        self.commands.clear()
        self.client.accept_command("pause")
        # Nothing merged since the command went out: a cached state must
        # never confirm it.
        self.assertEqual(self.commands[-1]["outcome"], "accepted")
        self.assertTrue(self.client._session.commands.has_pending)

    def test_an_acknowledgement_of_an_untracked_command_is_ignored(self):
        self.client.accept_command("ghost")
        self.assertEqual([c for c in self.commands if c["name"] == "ghost"], [])

    def test_a_failure_and_a_settlement_both_reach_the_ui(self):
        self.client.track_command("pause", ["paused"])
        self.client.fail_command("pause", "refused")
        self.assertEqual(self.commands[-1]["outcome"], "failed")
        self.client.track_command("resume")
        self.client.settle_command("resume", "Nothing to resume")
        self.assertEqual(self.commands[-1]["outcome"], "confirmed")
        self.assertEqual(self.commands[-1]["detail"], "Nothing to resume")

    def observe(self, patch):
        self.client.admit_status(patch, origin="fragment", stamp=1.0, generation=self.client._generation)

    def test_expiring_stops_the_timer_once_nothing_is_pending(self):
        self.client.track_command("pause", ["paused"], timeout_s=0.1)
        self.client._session.commands.get("pause").issued_at = 0.0
        self.client.expire_commands()
        self.assertEqual(self.commands[-1]["outcome"], "timed_out")
        self.assertFalse(self.client._command_timer.isActive())

    def test_an_expiry_across_a_rebind_does_not_emit_the_old_command(self):
        for name in ("pause", "resume"):
            self.client.track_command(name, ["paused"], timeout_s=0.1)
            self.client._session.commands.get(name).issued_at = 0.0
        self.commands.clear()
        self.client.commandChanged.connect(
            lambda command: setattr(self.client, "_generation", self.client._generation + 1))
        self.client.expire_commands()
        self.assertEqual([command["name"] for command in self.commands], ["pause"])

    def test_a_command_handler_that_rebinds_stops_the_remaining_emissions(self):
        self.observe({"print_stats": {"state": "printing"}})
        for name in ("pause", "resume"):
            self.client.track_command(name, ["paused"])
            self.client.accept_command(name)
        self.commands.clear()
        # A synchronous rebind from inside the first emission must not let
        # the rest of the old generation's commands through.
        self.client.commandChanged.connect(
            lambda command: setattr(self.client, "_generation", self.client._generation + 1))
        self.observe({"print_stats": {"state": "paused"}})
        self.assertEqual([command["name"] for command in self.commands], ["pause"])


class ClientGuardTests(_ClientCase):
    def setUp(self):
        super().setUp()
        self.configure()
        self.client.start()
        self.deliver({"result": {"status": self.status("idle")}})

    def test_a_guard_change_forces_an_immediate_refresh(self):
        self.transport.requests.clear()
        self.client.set_pause_guard(True)
        self.assertTrue(self.client.session.pause_guard)
        self.assertIsNotNone(self.last_request("core"))
        self.client.set_toolhead_guard(True)
        self.assertTrue(self.client.session.toolhead_guard)

    def test_an_unchanged_guard_triggers_no_refresh(self):
        self.client.set_pause_guard(True)
        self.transport.requests.clear()
        self.client.set_pause_guard(True)
        self.assertEqual(self.transport.requests, [])

    def test_a_guard_lowers_the_core_interval(self):
        self.client.set_pause_guard(True)
        self.assertEqual(self.client._poll_timer.interval(), 250)


class ClientSocketTests(_ClientCase):
    def setUp(self):
        super().setUp()
        self.configure(feed_mode="websocket")

    def test_start_connects_the_socket_and_subscribes_on_the_upgrade(self):
        self.client.start()
        self.assertEqual(len(self.socket.starts), 1)
        self.assertEqual(self.socket.starts[0][0], "ws://printer:7125/websocket")
        self.assertEqual(self.socket.starts[0][1], "key")
        self.assertEqual(len(self.socket.subscriptions), 1)
        self.assertIn("print_stats", self.socket.subscriptions[0])

    def test_a_second_start_cycle_does_not_stack_handlers(self):
        self.client.start()
        self.client._start_socket()
        # One tracked connection per cycle: the previous cycle's handlers
        # are disconnected, so a single emit reaches the client once.
        self.statuses.clear()
        self.socket.syncSnapshot.emit({"print_stats": {"state": "printing"}}, 5.0)
        self.assertEqual(len(self.statuses), 2)  # the snapshot plus the connect rebroadcast

    def test_a_sync_snapshot_connects_the_poller(self):
        self.client.start()
        self.socket.syncSnapshot.emit({"print_stats": {"state": "printing"}}, 5.0)
        self.assertTrue(self.client.connected)
        self.assertEqual(self.connections[-1], (True, "Moonraker connected over websocket"))
        self.assertEqual(self.client.status["print_stats"]["state"], "printing")

    def test_a_socket_failure_walks_the_ladder(self):
        self.client.start()
        self.socket.failed.emit("handshake rejected")
        self.assertTrue(self.connections[-1][1].startswith("WebSocket feed failed: handshake rejected"))
        self.assertFalse(self.client.connected)

    def test_a_rejected_key_is_reported_as_such(self):
        self.client.start()
        self.socket.subscribeRefused.emit({"message": "Unauthorized"})
        self.assertTrue(self.connections[-1][1].startswith("WebSocket feed: the API key was rejected"))

    def test_a_structured_refusal_degrades_the_feed_to_http(self):
        self.client.start()
        self.transport.requests.clear()
        self.socket.subscribeRefused.emit({"message": "Subscription not allowed"})
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.connections[-1],
                         (False, "This Moonraker refused the status subscription; using HTTP polling"))
        self.assertIsNotNone(self.last_request("core"))

    def test_a_lost_klippy_connection_fails_the_feed(self):
        self.client.start()
        self.socket.klippyLost.emit("shutdown")
        self.assertTrue(self.connections[-1][1].startswith("Klippy lost the connection (shutdown)"))

    def test_a_klippy_restart_clears_the_assumption_and_resubscribes(self):
        self.client.start()
        self.client.session.state.assume_print_stopped = True
        self.socket.subscriptions.clear()
        self.socket.klippyReady.emit()
        self.assertFalse(self.client.session.state.assume_print_stopped)
        self.assertEqual(len(self.socket.subscriptions), 1)

    def test_a_stale_generation_socket_event_is_dropped(self):
        self.client.start()
        self.client._generation += 1
        before = list(self.connections)
        self.socket.failed.emit("late")
        self.socket.subscribeRefused.emit({"message": "Unauthorized"})
        self.socket.klippyReady.emit()
        self.socket.klippyLost.emit("shutdown")
        self.assertEqual(self.connections, before)
        self.assertTrue(self.client.session.state.assume_print_stopped is False)

    def test_a_duplicate_handler_entry_does_not_abort_the_start_cycle(self):
        self.client.start()
        # A duplicated entry makes the second disconnect raise; the cycle
        # must still finish and connect exactly one fresh handler set.
        self.client._socket_handlers = list(self.client._socket_handlers) + [self.client._socket_handlers[0]]
        self.client._start_socket()
        self.assertEqual(len(self.socket.starts), 2)
        self.client._proof_handler = lambda: None  # never connected to the timer
        self.client._start_socket()
        self.assertEqual(len(self.socket.starts), 3)

    def test_the_startup_proof_degrades_a_silent_socket(self):
        self.client.start()
        self.transport.requests.clear()
        self.client._proof_failed(self.client._generation)
        self.assertEqual(self.client.effective_feed_mode, "http")
        self.assertEqual(self.connections[-1],
                         (False, "WebSocket feed stayed silent; using HTTP polling"))
        self.assertTrue(self.socket.stops > 0)
        self.assertIsNotNone(self.last_request("core"))

    def test_a_passing_proof_is_never_revoked(self):
        self.client.start()
        self.client._last_applied_stamp = 1.0
        self.client._proof_failed(self.client._generation)
        self.assertEqual(self.client.effective_feed_mode, "websocket")

    def test_a_stale_proof_never_degrades_a_rebound_session(self):
        self.client.start()
        self.client._proof_failed(self.client._generation + 1)
        self.assertEqual(self.client.effective_feed_mode, "websocket")

    def test_the_proof_timer_is_armed_and_stops_with_the_poller(self):
        self.client.start()
        self.assertTrue(self.client._proof_timer.isActive())
        self.assertEqual(self.client._proof_timer.interval(), 40)
        self.client.stop()
        self.assertFalse(self.client._proof_timer.isActive())

    def test_the_socket_drain_feeds_a_fragment_into_the_snapshot(self):
        self.client.start()
        self.socket.drain_core = lambda: ({"print_stats": {"state": "printing"}}, 3.0)
        self.client.force_refresh()
        self.assertEqual(self.client.status["print_stats"]["state"], "printing")

    def test_a_dropped_socket_is_reconnected_on_the_next_tick(self):
        self.client.start()
        self.socket.is_upgraded = False
        self.transport.requests.clear()
        self.client.force_refresh()
        self.assertEqual(len(self.socket.starts), 2)

    def test_a_dropped_socket_respects_the_retry_window(self):
        self.client.start()
        self.socket.is_upgraded = False
        self.client._retry_not_before = float("inf")
        self.client.force_refresh()
        self.assertEqual(len(self.socket.starts), 1)

    def test_resubscribe_heals_only_a_live_websocket_feed(self):
        self.client.start()
        self.socket.subscriptions.clear()
        self.client.resubscribe()
        self.assertEqual(len(self.socket.subscriptions), 1)
        self.client.stop()
        self.client.start()  # http mode now falls back only if the mode changed
        self.client.resubscribe()
        self.assertEqual(len(self.socket.subscriptions), 1)

    def test_auxiliary_objects_join_the_subscription(self):
        self.client.start()
        self.socket.subscriptions.clear()
        self.client.set_auxiliary_objects({"temperature_sensor chamber"})
        self.assertEqual(self.socket.aux_names, {"temperature_sensor chamber"})
        self.assertIn("temperature_sensor chamber", self.socket.subscriptions[-1])
        self.client.set_auxiliary_objects({"temperature_sensor chamber"})
        self.assertEqual(len(self.socket.subscriptions), 1)

    def test_auxiliary_objects_are_recorded_while_the_feed_is_down(self):
        self.client.set_auxiliary_objects({"fan"})
        self.assertEqual(self.client._aux_names, {"fan"})

    def test_the_rpc_lane_is_unavailable_until_the_socket_upgrades(self):
        self.client.start()
        self.assertTrue(self.client.rpc_available())
        self.client.session.socket.is_upgraded = False
        self.assertFalse(self.client.rpc_available())
        self.assertFalse(self.client.rpc("printer.objects.query", {}, lambda r, e: None))

    def test_an_rpc_reply_maps_both_outcomes_onto_the_transport_convention(self):
        self.client.start()
        replies = []
        self.assertTrue(self.client.rpc("printer.gcode.script", {"script": "M104 S0"},
                                        lambda reply, error: replies.append((reply, error))))
        method, params, callback = self.socket.rpcs[-1]
        self.assertEqual(method, "printer.gcode.script")
        self.assertEqual(params, {"script": "M104 S0"})
        callback({"result": {"ok": True}})
        self.assertEqual(replies[-1][1], None)
        callback({"error": {"message": "Extrude below minimum temp"}})
        self.assertEqual(replies[-1][1], "Extrude below minimum temp")
        callback({"error": {"message": "Unknown"}})
        self.assertEqual(replies[-1][1], "Moonraker refused the request")

    def test_a_refused_rpc_request_is_reported_as_unavailable(self):
        self.client.start()
        self.socket.request = lambda method, params, callback: 0
        self.assertFalse(self.client.rpc("printer.info", {}, lambda r, e: None))

    def test_the_aux_drain_is_a_socket_only_lane(self):
        self.client.start()
        self.socket.drain_aux = lambda: ({"fan": {"speed": 1.0}}, 2.0)
        self.assertEqual(self.client.drain_aux(), ({"fan": {"speed": 1.0}}, 2.0))

    def test_the_aux_drain_is_empty_while_polling_over_http(self):
        self.client.stop()
        self.configure(feed_mode="http")
        self.client.start()
        self.assertEqual(self.client.drain_aux(), (None, 0.0))


# --------------------------------------------------------------------------
# RemoteFileService
# --------------------------------------------------------------------------

class DeclaredLengthTests(_QtCase):
    def setUp(self):
        self.mod = self.rt.load("RemoteFileService")

    def test_the_header_pairs_are_read_case_insensitively(self):
        reply = FakeReply(pairs=[(b"Content-Type", b"text/plain"), (b"content-LENGTH", b"128")])
        self.assertEqual(self.mod._declared_length(reply), 128)

    def test_an_absent_length_is_zero(self):
        self.assertEqual(self.mod._declared_length(FakeReply(pairs=[(b"X-Test", b"1")])), 0)

    def test_an_unusable_pair_name_is_skipped_rather_than_ending_the_scan(self):
        reply = FakeReply(pairs=[(None, b"1"), (b"Content-Length", b"64")])
        self.assertEqual(self.mod._declared_length(reply), 64)

    def test_a_non_numeric_length_reads_as_zero(self):
        self.assertEqual(self.mod._declared_length(FakeReply(pairs=[(b"Content-Length", b"chunky")])), 0)

    def test_the_raw_header_is_the_fallback_when_no_pairs_exist(self):
        reply = FakeReply(headers={b"Content-Length": b"4096"})
        reply._pairs_available = False
        self.assertEqual(self.mod._declared_length(reply), 4096)

    def test_an_unusable_raw_header_reads_as_zero(self):
        reply = FakeReply(headers={b"Content-Length": b"chunky"})
        reply._pairs_available = False
        self.assertEqual(self.mod._declared_length(reply), 0)
        reply = FakeReply()
        reply._pairs_available = False
        self.assertEqual(self.mod._declared_length(reply), 0)


class _FileServiceCase(_QtCase):
    def setUp(self):
        self.mod = self.rt.load("RemoteFileService")
        self.transport = _RecordingTransport()
        self.transport.identity = ("http://printer:7125", "key")
        self.service = self.mod.RemoteFileService(self.transport)
        self.changed = []
        self.service.changed.connect(lambda: self.changed.append(1))
        self.failures = []
        self.service.failed.connect(self.failures.append)

    def tearDown(self):
        self.service.close()

    def deliver_json(self, payload, error=None, channel="metadata"):
        for request in reversed(self.transport.requests):
            if request.channel == channel:
                request.callback(payload, error)
                return
        raise AssertionError("no %r request was issued" % (channel,))

    def metadata(self, size=16, filename="part.gcode", modified=5.0):
        return {"result": {"filename": filename, "size": size, "modified": modified, "uuid": "u1"}}

    def start_job(self, size=16, filename="part.gcode"):
        """Bind, resolve the metadata and leave the download in flight."""
        self.service.bind((filename, size))
        reply = FakeReply(pairs=[(b"Content-Length", str(size).encode())])
        self.transport.network.replies.append(reply)
        self.service.request_file()
        self.deliver_json(self.metadata(size=size, filename=filename))
        return reply

    def finish_download(self, reply):
        reply.finished.emit()
        self.events(60)


class RemoteFileServiceMetadataTests(_FileServiceCase):
    def test_a_metadata_success_installs_the_parsed_identity(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.assertEqual(self.service.phase, "resolving")
        self.deliver_json(self.metadata(size=16, modified=7.5))
        self.assertTrue(self.service.metadata_complete)
        self.assertEqual(self.service.identity, self.mod.RemoteFileIdentity("part.gcode", 16, 7.5, "u1"))
        self.assertEqual(self.service.metadata["size"], 16)
        self.assertEqual(self.service.phase, "idle")

    def test_a_failed_metadata_installs_a_fallback_identity_and_backs_off(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.deliver_json(None, "server unreachable")
        # Downloads must be unblocked by a listing-sized fallback, but the
        # run's metadata is NOT complete.
        self.assertEqual(self.service.identity, self.mod.RemoteFileIdentity("part.gcode", 16))
        self.assertFalse(self.service.metadata_complete)
        self.assertEqual(self.service._metadata_attempts, 1)
        self.transport.requests.clear()
        self.service.request_metadata()
        self.assertEqual(self.transport.requests, [])

    def test_the_retry_window_reopens_after_the_first_delay(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.deliver_json(None, "down")
        self.service._metadata_retry_at = 0.0
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 2)
        self.deliver_json(self.metadata())
        self.assertTrue(self.service.metadata_complete)
        self.assertEqual(self.service._metadata_attempts, 0)
        self.assertEqual(self.service._metadata_retry_at, 0.0)

    def test_the_backoff_ladder_saturates_at_its_last_delay(self):
        self.service.bind(("part.gcode", 16))
        rounds = len(self.service.METADATA_RETRY_DELAYS_MS) + 2
        for _ in range(rounds):
            self.service._metadata_retry_at = 0.0
            self.service.request_metadata()
            self.deliver_json(None, "down")
        self.assertEqual(self.service._metadata_attempts, rounds)
        # The ladder saturates: past its last rung the delay stops growing.
        self.assertAlmostEqual(
            self.service._metadata_retry_at - time.monotonic(),
            self.service.METADATA_RETRY_DELAYS_MS[-1] / 1000.0,
            delta=0.5,
        )

    def test_a_metadata_request_is_refused_when_there_is_nothing_to_fetch(self):
        self.service.request_metadata()
        self.assertEqual(self.transport.requests, [])
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.assertEqual(len(self.transport.requests), 1)
        self.service.request_metadata()  # already pending
        self.assertEqual(len(self.transport.requests), 1)
        self.deliver_json(self.metadata())
        self.service.request_metadata()  # already fetched
        self.assertEqual(len(self.transport.requests), 1)

    def test_a_refused_send_releases_the_pending_flag(self):
        self.service.bind(("part.gcode", 16))
        self.transport.started = False
        self.service.request_metadata()
        self.assertFalse(self.service._metadata_pending)

    def test_a_stale_metadata_reply_is_dropped_after_a_rebind(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.service.bind(("other.gcode", 8))
        self.deliver_json(self.metadata(filename="part.gcode"))
        self.assertIsNone(self.service.identity)

    def test_a_metadata_payload_that_cannot_be_parsed_keeps_the_fallback(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        with patch.object(self.mod, "parse_file_identity", side_effect=ValueError("bad body")):
            self.deliver_json({"result": {"size": "big"}})
        self.assertEqual(self.service.identity, self.mod.RemoteFileIdentity("part.gcode", 16))
        self.assertTrue(self.service.metadata_complete)

    def test_a_non_mapping_metadata_result_caches_nothing(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.deliver_json({"result": ["garbage"]})
        self.assertEqual(self.service.metadata, {})

    def test_a_metadata_only_fetch_is_identity_neutral(self):
        self.service.bind(("part.gcode", 16))
        self.service.request_metadata()
        self.deliver_json(self.metadata())
        identity = self.service.identity
        seen = []
        self.assertTrue(self.service.request_metadata_only(lambda result, error: seen.append((result, error))))
        # The lane's own bits must not move: a failing metadata-only fetch
        # would otherwise overwrite the identity the download depends on.
        self.assertFalse(self.service._metadata_pending)
        self.deliver_json({"result": {"filename": "other.gcode", "size": 999}}, channel="metadata-only")
        self.assertEqual(seen[-1][1], None)
        self.assertEqual(seen[-1][0]["size"], 999)
        self.assertIs(self.service.identity, identity)
        self.assertEqual(self.service._metadata_only_pending, False)

    def test_a_metadata_only_failure_is_handed_back_with_its_error(self):
        self.service.bind(("part.gcode", 16))
        seen = []
        self.service.request_metadata_only(lambda result, error: seen.append((result, error)))
        self.deliver_json(None, "refused", channel="metadata-only")
        self.assertEqual(seen, [({}, "refused")])

    def test_a_metadata_only_result_that_is_not_a_mapping_becomes_empty(self):
        self.service.bind(("part.gcode", 16))
        seen = []
        self.service.request_metadata_only(lambda result, error: seen.append(result))
        self.deliver_json({"result": "garbage"}, channel="metadata-only")
        self.assertEqual(seen, [{}])

    def test_a_metadata_only_fetch_is_refused_without_a_job_or_while_pending(self):
        self.assertFalse(self.service.request_metadata_only(lambda r, e: None))
        self.service.bind(("part.gcode", 16))
        self.assertTrue(self.service.request_metadata_only(lambda r, e: None))
        self.assertFalse(self.service.request_metadata_only(lambda r, e: None))
        self.service._metadata_only_pending = False
        self.service.bind(("port.gcode", 8))
        self.transport.started = False
        self.assertFalse(self.service.request_metadata_only(lambda r, e: None))
        self.assertFalse(self.service._metadata_only_pending)

    def test_a_stale_metadata_only_reply_is_dropped(self):
        self.service.bind(("part.gcode", 16))
        seen = []
        self.service.request_metadata_only(lambda result, error: seen.append(result))
        self.service.bind(("other.gcode", 8))
        self.deliver_json({"result": {"size": 1}}, channel="metadata-only")
        self.assertEqual(seen, [])


class RemoteFileServiceDownloadTests(_FileServiceCase):
    def test_a_job_download_lands_the_file_and_clears_the_error(self):
        reply = self.start_job()
        self.assertEqual(self.service.phase, "downloading")
        self.assertEqual(self.transport.network.requests[-1].rawHeader(b"Accept-Encoding"), b"identity")
        self.assertIsNone(self.service.download_fraction)
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertEqual(self.service.download_fraction, 0.5)
        reply.push(b"90abcdef")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertEqual(self.service.error, "")
        self.assertEqual(self.service.phase, "ready")
        with open(self.service.path, "rb") as handle:
            self.assertEqual(handle.read(), b"1234567890abcdef")
        self.assertTrue(any(path.endswith("part.gcode") for path in [self.service.path]))

    def test_a_short_transfer_is_refused_against_the_declared_length(self):
        reply = self.start_job(size=16)
        reply.push(b"1234")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertEqual(self.service.error, "Downloaded G-code size mismatch; refusing partial file")
        self.assertIsNone(self.service.path)
        self.assertEqual(len(self.failures), 1)

    def test_a_transfer_that_contradicts_the_listing_is_refused(self):
        # The listing is the honest referee: a proxy that ignores
        # identity-encoding answers with a length that matches ITS own
        # declaration but not the file's real size.
        reply = FakeReply(pairs=[(b"Content-Length", b"8")])
        self.service.bind(("part.gcode", 99))
        self.transport.network.replies.append(reply)
        self.service.request_file()
        self.deliver_json(self.metadata(size=99))
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertEqual(self.service.error,
                         "Downloaded G-code does not match the file listing's size; refusing")

    def test_a_transport_error_fails_the_download(self):
        reply = self.start_job()
        reply.fail("Connection closed")
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertEqual(self.service.error, "Connection closed")
        self.assertIsNone(self.service.path)

    def test_a_writer_error_fails_the_download(self):
        reply = self.start_job()
        self.service._download.writer_error = "no space left on device"
        self.finish_download(reply)
        self.assertEqual(self.service.error, "no space left on device")

    def test_a_retired_operation_never_becomes_the_path(self):
        reply = self.start_job()
        op = self.service._download
        op.generation = self.service._generation - 1
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertIsNone(self.service.path)
        self.assertEqual(self.service.error, "")

    def test_a_compressed_response_is_refused_outright(self):
        reply = self.start_job()
        reply._headers[b"Content-Encoding"] = b"gzip"
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertEqual(self.service.error, "The download was served compressed; refusing")
        self.assertIsNone(self.service._download)

    def test_the_byte_cap_aborts_a_hostile_endpoint(self):
        reply = self.start_job()
        self.service._download.received = self.service.MAX_DOWNLOAD_BYTES
        reply.push(b"1")
        # The cap trips inside the finish drain: the operation is retired
        # mid-drain, so the drain's own tail must not stop a dead writer.
        self.finish_download(reply)
        self.assertEqual(self.service.error, "Downloaded G-code exceeds the size cap")
        self.assertIsNone(self.service._download)
        self.assertEqual(reply.aborted, 1)
        self.assertEqual(self.service._download_attempts, 1)

    def test_a_backed_up_drain_pauses_the_read(self):
        reply = self.start_job()
        op = self.service._download
        op.received = op.HIGH_WATER_BYTES
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertTrue(op.reading_paused)

    def test_a_reply_that_refuses_its_buffer_cap_is_aborted(self):
        reply = FakeReply(pairs=[(b"Content-Length", b"16")])
        reply.buffer_size_raises = True
        self.transport.network.replies.append(reply)
        self.service.bind(("part.gcode", 16))
        self.service.request_file()
        self.deliver_json(self.metadata())
        self.assertEqual(self.service.error, "this reply refuses a buffer cap")
        self.assertEqual(reply.aborted, 1)
        self.assertEqual(reply.deleted, 1)

    def test_a_read_pause_holds_the_bytes_until_the_writer_catches_up(self):
        reply = self.start_job()
        op = self.service._download
        op.reading_paused = True
        op.received = op.written + op.LOW_WATER_BYTES
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertEqual(op.received, op.LOW_WATER_BYTES)
        op.received = op.written
        reply.readyRead.emit()
        self.assertGreater(op.received, 0)
        self.assertFalse(op.reading_paused)

    def test_a_writer_drain_resumes_the_read(self):
        reply = self.start_job()
        op = self.service._download
        op.reading_paused = True
        reply.push(b"12345678")
        self.service.writerDrained.emit(op)
        self.events(20)
        op.stop()
        self.events(20)
        self.assertEqual(op.received, 8)

    def test_a_drain_from_a_retired_operation_is_ignored(self):
        self.start_job()
        op = self.service._download
        self.service._download = None
        self.service._drain(op, op.reply)
        self.assertEqual(op.received, 0)

    def test_a_late_finish_from_a_retired_reply_is_dropped(self):
        reply = self.start_job()
        op = self.service._download
        self.service._download = None
        self.service._finish_download(op, reply)
        self.assertEqual(reply.deleted, 1)

    def test_a_retired_operation_s_writer_done_is_a_no_op(self):
        self.start_job()
        op = self.service._download
        self.service._download = None
        self.service._on_writer_done(op)
        self.assertIsNone(self.service.path)

    def test_a_writer_drain_before_the_stream_ends_is_ignored(self):
        self.start_job()
        op = self.service._download
        op.finished_reading = True
        self.service._on_writer_drained(op)
        self.assertFalse(op.reading_paused)

    def test_a_download_setup_failure_retires_the_target_and_the_directory(self):
        def explode(path):
            raise OSError("the cache is full")

        service = self.mod.RemoteFileService(self.transport, target_factory=explode)
        self.addCleanup(service.close)
        failures = []
        service.failed.connect(failures.append)
        service.bind(("part.gcode", 16))
        service.request_file()
        self.deliver_json(self.metadata())
        self.assertEqual(service.error, "the cache is full")
        self.assertEqual(service.phase, "error")
        self.assertEqual(failures, ["the cache is full"])

    def test_a_request_failure_after_the_target_was_opened_closes_the_handle(self):
        opened = []
        real_factory = self.mod.DownloadTarget.open

        def record(path):
            target = real_factory(path)
            opened.append(target)
            return target

        service = self.mod.RemoteFileService(self.transport, target_factory=record)
        self.addCleanup(service.close)
        self.transport.network.replies.append(FakeReply())
        service.bind(("part.gcode", 16))
        service.request_file()
        self.transport.network.get = lambda request: (_ for _ in ()).throw(OSError("link down"))
        self.deliver_json(self.metadata())
        self.assertEqual(service.error, "link down")
        self.assertTrue(opened[-1].handle.closed)

    def test_aborting_without_an_operation_is_harmless(self):
        self.service.bind(("part.gcode", 16))
        self.service._abort_download()
        self.assertIsNone(self.service._download)

    def test_the_name_gets_a_gcode_extension_when_it_has_none(self):
        reply = self.start_job(filename="no-extension")
        reply.push(b"1234567890abcdef")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertTrue(self.service.path.endswith("no-extension.gcode"))

    def test_a_backslash_path_is_named_from_its_basename(self):
        reply = self.start_job(filename="sub\\part.gcode")
        reply.push(b"1234567890abcdef")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertTrue(self.service.path.endswith("part.gcode"))

    def test_a_download_is_not_started_twice_while_one_is_in_flight(self):
        self.start_job()
        self.transport.requests.clear()
        self.service.request_file()
        self.assertEqual([r for r in self.transport.requests if r.channel == "metadata"], [])

    def test_a_retry_request_clears_the_latched_error_and_the_ladder(self):
        reply = self.start_job()
        reply.fail("gone")
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.assertNotEqual(self.service.error, "")
        self.assertEqual(self.service._download_attempts, 1)
        self.transport.network.replies.append(FakeReply(pairs=[(b"Content-Length", b"16")]))
        self.service.request_file(retry=True)
        self.assertEqual(self.service.error, "")
        self.assertEqual(self.service._download_attempts, 0)
        self.assertIsNotNone(self.service._download)

    def test_the_latched_error_holds_inside_the_backoff_window(self):
        reply = self.start_job()
        reply.fail("gone")
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.transport.requests.clear()
        self.service.request_file()
        self.assertNotEqual(self.service.error, "")
        self.assertEqual(self.transport.requests, [])

    def test_the_download_restarts_once_the_backoff_window_passes(self):
        reply = self.start_job()
        reply.fail("gone")
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        self.service._download_retry_at = 0.0
        self.transport.network.replies.append(FakeReply(pairs=[(b"Content-Length", b"16")]))
        self.service.request_file()
        self.assertEqual(self.service.error, "")
        self.assertIsNotNone(self.service._download)

    def test_the_failure_ladder_saturates_at_its_last_delay(self):
        self.service.bind(("part.gcode", 16))
        for _ in range(len(self.service.DOWNLOAD_RETRY_DELAYS_MS) + 1):
            self.service._fail("down")
        expected = self.service.DOWNLOAD_RETRY_DELAYS_MS[-1] / 1000.0
        self.assertAlmostEqual(self.service._download_retry_at - time.monotonic(), expected, delta=0.5)


class RemoteFileServiceLeaseTests(_FileServiceCase):
    def test_a_lease_keeps_the_file_alive_across_a_rebind(self):
        reply = self.start_job(size=8)
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        path = self.service.path
        lease = self.service.lease()
        self.assertEqual(lease.path, path)
        self.service.bind(("other.gcode", 8))
        self.assertTrue(os.path.exists(path))
        lease.close()
        self.assertFalse(os.path.exists(path))

    def test_a_second_lease_defers_the_release_until_the_last_holder(self):
        reply = self.start_job(size=8)
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        path = self.service.path
        first = self.service.lease()
        second = self.service.lease()
        self.service.bind(("other.gcode", 8))
        first.close()
        self.assertTrue(os.path.exists(path))
        second.close()
        self.assertFalse(os.path.exists(path))

    def test_closing_a_lease_twice_releases_once(self):
        reply = self.start_job(size=8)
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        lease = self.service.lease()
        lease.close()
        lease.close()
        self.assertEqual(self.service._leases, {})

    def test_a_lease_is_refused_when_no_file_is_ready(self):
        self.service.bind(("part.gcode", 8))
        self.assertIsNone(self.service.lease())

    def test_close_retires_the_root_once_the_leases_are_gone(self):
        reply = self.start_job(size=8)
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_download(reply)
        lease = self.service.lease()
        root = self.service._root
        self.service.close()
        self.assertTrue(os.path.exists(root))
        self.service.close()  # idempotent
        lease.close()
        self.assertFalse(os.path.exists(root))

    def test_close_is_idempotent_without_leases(self):
        self.service.bind(("part.gcode", 8))
        self.service.close()
        self.service.close()
        self.assertTrue(self.service._closed)

    def test_a_rebind_to_the_same_job_is_a_no_op(self):
        self.service.bind(("part.gcode", 8))
        before = len(self.changed)
        self.service.bind(("part.gcode", 8))
        self.assertEqual(len(self.changed), before)
        self.service._closed = True
        self.service.bind(("part.gcode", 8))
        self.assertGreater(len(self.changed), before)


class RemoteFileServiceOneShotTests(_FileServiceCase):
    def start_one_shot(self, relpath="sub/part.gcode", declared=8):
        reply = FakeReply(pairs=[(b"Content-Length", str(declared).encode())])
        self.transport.network.replies.append(reply)
        delivered = []
        download = self.service.download_once(relpath, on_ready=lambda path, error: delivered.append((path, error)))
        return download, reply, delivered

    def finish_one_shot(self, reply):
        reply.finished.emit()
        self.events(60)

    def test_a_one_shot_download_delivers_its_path_exactly_once(self):
        download, reply, delivered = self.start_one_shot()
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_one_shot(reply)
        self.assertEqual(len(delivered), 1)
        path, error = delivered[0]
        self.assertIsNone(error)
        self.assertTrue(path.endswith("part.gcode"))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"12345678")
        self.assertEqual(self.service._one_shots, set())
        self.assertTrue(os.path.exists(path))

    def test_a_one_shot_cancel_delivers_its_terminal_and_retires_the_directory(self):
        download, reply, delivered = self.start_one_shot()
        directory = download._directory
        self.service.cancel_one_shots()
        self.assertEqual(delivered, [(None, "The printer connection changed; the download was cancelled")])
        self.assertFalse(os.path.exists(directory))
        self.assertEqual(self.service._one_shots, set())

    def test_a_cancelled_one_shot_stays_cancelled(self):
        download, reply, delivered = self.start_one_shot()
        download.cancel()
        download.cancel()
        reply.finished.emit()
        self.events(20)
        self.assertEqual(len(delivered), 1)

    def test_a_one_shot_reply_error_is_reported_and_the_directory_goes(self):
        download, reply, delivered = self.start_one_shot()
        directory = download._directory
        reply.fail("Connection closed")
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_one_shot(reply)
        self.assertEqual(delivered, [(None, "Connection closed")])
        self.assertFalse(os.path.exists(directory))

    def test_a_one_shot_rejects_a_short_transfer(self):
        download, reply, delivered = self.start_one_shot(declared=8)
        directory = download._directory
        reply.push(b"1234")
        reply.readyRead.emit()
        self.finish_one_shot(reply)
        self.assertEqual(delivered[0][1], "Downloaded G-code size mismatch; refusing partial file")
        self.assertFalse(os.path.exists(directory))

    def test_a_one_shot_writer_error_never_delivers_the_file(self):
        download, reply, delivered = self.start_one_shot()
        download._op.writer_error = "no space left on device"
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_one_shot(reply)
        self.assertEqual(delivered, [(None, "no space left on device")])

    def test_a_one_shot_that_loses_the_printer_never_delivers_the_file(self):
        download, reply, delivered = self.start_one_shot()
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.transport.identity = ("http://other:7125", "key")
        self.finish_one_shot(reply)
        self.assertEqual(delivered, [(None, "The printer connection changed during the download")])

    def test_a_one_shot_refuses_a_compressed_response(self):
        download, reply, delivered = self.start_one_shot()
        directory = download._directory
        reply._headers[b"Content-Encoding"] = b"gzip"
        reply.push(b"12345678")
        reply.readyRead.emit()
        # The failure lands mid-drain, so the reply's own finish must not
        # deliver a second terminal.
        self.finish_one_shot(reply)
        self.assertEqual(delivered, [(None, "The download was served compressed; refusing")])
        self.assertFalse(os.path.exists(directory))

    def test_a_one_shot_byte_cap_is_enforced(self):
        download, reply, delivered = self.start_one_shot()
        directory = download._directory
        download._op.received = self.service.MAX_DOWNLOAD_BYTES
        reply.push(b"1")
        # The cap trips inside the finish drain: the terminal is delivered
        # there, so the stream's tail must not signal the writer again.
        self.finish_one_shot(reply)
        self.assertEqual(delivered, [(None, "Download exceeds the size cap")])
        self.assertFalse(os.path.exists(directory))

    def test_a_late_failure_after_the_terminal_is_dropped(self):
        download, reply, delivered = self.start_one_shot()
        download._done = True
        download._finish_immediately("too late")
        download._terminal()
        download._deliver("/tmp/late", None)
        self.assertEqual(delivered, [])

    def test_a_one_shot_setup_failure_delivers_immediately_and_is_not_registered(self):
        delivered = []
        download = self.service.download_once("part.gcode", on_ready=lambda path, error: delivered.append((path, error)))
        self.assertTrue(download._done)
        self.assertEqual(len(delivered), 1)
        self.assertIsNone(delivered[0][0])
        self.assertIn("no scripted reply", delivered[0][1])
        self.assertEqual(self.service._one_shots, set())
        download.cancel()  # a dead download must not deliver a second terminal
        self.assertEqual(len(delivered), 1)

    def test_a_one_shot_name_gets_the_gcode_extension(self):
        download, reply, delivered = self.start_one_shot(relpath="sub/part")
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.finish_one_shot(reply)
        self.assertTrue(delivered[0][0].endswith("part.gcode"))

    def test_a_one_shot_encodes_the_request_as_binary_identity(self):
        download, reply, delivered = self.start_one_shot()
        request = self.transport.network.requests[-1]
        self.assertEqual(request.rawHeader(b"Accept"), b"application/octet-stream")
        self.assertEqual(request.rawHeader(b"Accept-Encoding"), b"identity")
        self.assertEqual(reply.buffer_size, 4 * 1024 * 1024)

    def test_a_one_shot_read_pause_waits_for_the_writer(self):
        download, reply, delivered = self.start_one_shot()
        op = download._op
        op.reading_paused = True
        op.received = op.written + op.LOW_WATER_BYTES
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertEqual(op.received, op.LOW_WATER_BYTES)
        op.received = op.written
        reply.readyRead.emit()
        self.assertEqual(op.received, 8)

    def test_a_backed_up_one_shot_drain_pauses_the_read(self):
        download, reply, delivered = self.start_one_shot()
        op = download._op
        op.received = op.HIGH_WATER_BYTES
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertTrue(op.reading_paused)

    def test_a_finished_reading_one_shot_ignores_the_water_mark(self):
        download, reply, delivered = self.start_one_shot()
        op = download._op
        op.finished_reading = True
        op.reading_paused = True
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertEqual(op.received, 8)

    def test_a_one_shot_drain_from_a_retired_operation_is_ignored(self):
        download, reply, delivered = self.start_one_shot()
        download._done = True
        reply.push(b"12345678")
        reply.readyRead.emit()
        self.assertEqual(download._op.received, 0)

    def test_a_late_finish_after_the_one_shot_was_done_only_deletes_the_reply(self):
        download, reply, delivered = self.start_one_shot()
        download._done = True
        reply.finished.emit()
        self.assertEqual(reply.deleted, 1)
        self.assertEqual(delivered, [])

    def test_a_finished_reading_one_shot_stops_its_writer_immediately(self):
        download, reply, delivered = self.start_one_shot()
        reply.push(b"12345678")
        reply.finished.emit()
        self.events(60)
        self.assertEqual(len(delivered), 1)
        self.assertTrue(download._op.sentinel_put)

    def test_the_one_shot_registry_finds_its_operation(self):
        download, reply, delivered = self.start_one_shot()
        op = download._op
        strays = [other for other in self.service._one_shots if other is not download]
        self.assertEqual(strays, [])
        self.service._on_one_shot_drained(op)
        self.service._on_one_shot_done(op)
        self.assertEqual(len(delivered), 1)

    def test_a_reply_that_arrives_after_the_registry_entry_is_gone_is_ignored(self):
        download, reply, delivered = self.start_one_shot()
        self.service._one_shots.discard(download)
        self.service._on_one_shot_drained(download._op)
        self.service._on_one_shot_done(download._op)
        self.assertFalse(download._done)
        self.assertEqual(delivered, [])


if __name__ == "__main__":
    unittest.main()
