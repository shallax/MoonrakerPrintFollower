"""Pure MoonrakerSession behaviour: poll policy, coalescing, rebind and commands."""
from __future__ import annotations

import unittest

from tests.fake_moonraker import FakeMoonraker
from plugins.MoonrakerSession import (
    BindingIdentity,
    MoonrakerSession,
    MoonrakerSessionState,
    PollPolicy,
    RequestCategory,
    RequestCoalescer,
)


class _FakeSocket:
    def __init__(self) -> None:
        self.stops = 0
        self.starts = []

    def stop(self) -> None:
        self.stops += 1

    def start(self, url, api_key, core_names, aux_names) -> None:
        self.starts.append((url, api_key))


class _FakeTransport:
    def __init__(self) -> None:
        self.identity = ("", "")
        self.configure_calls = []
        self.cancel_calls = 0

    def configure(self, base_url: str, api_key: str) -> bool:
        target = (str(base_url or "").rstrip("/"), str(api_key or ""))
        changed = target != self.identity
        self.identity = target
        self.configure_calls.append(target)
        return changed

    def cancel_all(self) -> None:
        self.cancel_calls += 1


class SessionStateTests(unittest.TestCase):
    def test_polling_is_category_state_and_pause_guard_aware(self):
        policy = PollPolicy()
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "printing"), 750)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "printing", urgent=True), 250)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 100, "printing", urgent=True), 100)
        # The toolhead guard must beat the idle/paused floors too: moves
        # execute in those states, and the readout has to track the head.
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "paused", urgent=True), 250)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "standby", urgent=True), 250)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "paused"), 1500)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "standby"), 5000)
        # The auxiliary cadence is the user's delivery interval (the
        # sliders ruling) with the printer's own 250 ms cadence as the
        # floor; 2500 is the shipped default.
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 2500, "printing"), 2500)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 2500, "standby"), 2500)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 5000, "printing"), 5000)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 100, "printing"), 250)
        self.assertEqual(policy.interval_ms(RequestCategory.POWER, 750, "printing"), 5000)
        self.assertEqual(policy.interval_ms(RequestCategory.SYSTEM, 750, "printing"), 10000)
        self.assertEqual(policy.interval_ms(RequestCategory.DISCOVERY, 750, "printing"), 30000)

    def test_overlapping_refreshes_coalesce_to_one_follow_up(self):
        coalescer = RequestCoalescer()
        self.assertTrue(coalescer.begin("core"))
        self.assertFalse(coalescer.begin("core", force=True))
        self.assertFalse(coalescer.begin("core", force=True))
        self.assertTrue(coalescer.complete("core"))
        self.assertTrue(coalescer.begin("core"))
        self.assertFalse(coalescer.complete("core"))

    def test_endpoint_or_credential_rebind_invalidates_shared_state(self):
        transport = _FakeTransport()
        session = MoonrakerSession(transport=transport, socket=_FakeSocket())
        self.assertTrue(session.configure("http://printer", "first-key"))
        session.merge_status({"print_stats": {"state": "printing"}}, now=1)
        self.assertTrue(session.coalescer.begin("core"))
        session.commands.issue("Pause", {"paused"}, now=1)
        generation = session.generation

        self.assertTrue(session.configure("http://printer", "second-key"))
        self.assertGreater(session.generation, generation)
        self.assertEqual(session.base_url, "http://printer")
        self.assertEqual(session.api_key, "second-key")
        self.assertEqual(session.snapshot.revision, 0)
        self.assertEqual(session.snapshot.copy_status(), {})
        self.assertFalse(session.coalescer.is_in_flight("core"))
        self.assertIsNone(session.commands.get("Pause"))
        self.assertFalse(session.connected)
        self.assertFalse(session.pause_guard)

        generation = session.generation
        self.assertTrue(session.configure("http://other-printer", "second-key"))
        self.assertGreater(session.generation, generation)
        self.assertEqual(transport.identity, ("http://other-printer", "second-key"))

    def test_mode_only_change_rebinds_without_touching_the_transport(self):
        transport = _FakeTransport()
        socket = _FakeSocket()
        session = MoonrakerSession(transport=transport, socket=socket)
        session.configure("http://printer", "key")
        # The session's sentinel is the pre-4.0.0 behaviour; the product
        # default lives in PrinterConfig and is passed in explicitly.
        self.assertEqual(session.feed_mode, "http")
        generation = session.generation

        self.assertTrue(session.configure("http://printer", "key", "websocket"))
        self.assertGreater(session.generation, generation)
        self.assertEqual(session.feed_mode, "websocket")
        # The mode change rebinds the session but never reconfigures the
        # HTTP transport: its lanes have no reason to be invalidated.
        self.assertEqual(transport.configure_calls, [("http://printer", "key")])
        self.assertEqual(socket.stops, 2)  # the first bind's reset + the mode change

    def test_feed_mode_none_sentinel_keeps_the_current_mode(self):
        transport = _FakeTransport()
        socket = _FakeSocket()
        session = MoonrakerSession(transport=transport, socket=socket)
        session.configure("http://printer", "key", "http")
        generation = session.generation
        self.assertFalse(session.configure("http://printer", "key"))
        self.assertEqual(session.generation, generation)
        self.assertEqual(session.feed_mode, "http")
        self.assertEqual(session.identity, BindingIdentity("http://printer", "key", "http"))

    def test_session_reset_invalidates_old_generation_and_shared_state(self):
        # Drive the production identity path (MoonrakerSession.configure) and
        # then reset, as a rebind does, rather than a test-only state mutation.
        transport = _FakeTransport()
        session = MoonrakerSession(transport=transport, socket=_FakeSocket())
        self.assertTrue(session.configure("http://printer-a", "key"))
        session.merge_status(
            {
                "print_stats": {"state": "printing", "filename": "old.gcode"},
                "virtual_sdcard": {"file_position": 1234},
            },
            now=1,
        )
        session.coalescer.begin("core")
        session.commands.issue("Pause", {"paused"}, now=1)
        old_generation = session.generation

        session.reset()

        self.assertGreater(session.generation, old_generation)
        self.assertEqual(session.snapshot.copy_status(), {})
        self.assertEqual(session.snapshot.revision, 0)
        self.assertFalse(session.coalescer.is_in_flight("core"))
        self.assertIsNone(session.commands.get("Pause"))
        self.assertFalse(session.connected)
        self.assertFalse(session.pause_guard)
        self.assertFalse(session.toolhead_guard)
        self.assertEqual(transport.cancel_calls, 1)

    def test_qt_wrapper_delegates_toolhead_guard_to_the_shared_state(self):
        # The client reads session.toolhead_guard at configure time and
        # ToolheadController arms it through set_toolhead_guard; both must
        # delegate to the state the configure path resets.
        session = MoonrakerSession(transport=_FakeTransport(), socket=_FakeSocket())
        self.assertFalse(session.toolhead_guard)
        self.assertTrue(session.set_toolhead_guard(True))
        self.assertTrue(session.toolhead_guard)
        self.assertTrue(session.state.toolhead_guard)
        self.assertFalse(session.set_toolhead_guard(True))  # idempotent
        session.configure("http://printer", "key")
        self.assertFalse(session.toolhead_guard)

    def test_command_ack_requires_observed_printer_state(self):
        fake = FakeMoonraker([
            {"print_stats": {"state": "printing"}},
            {"print_stats": {"state": "paused"}},
        ])
        session = MoonrakerSessionState()
        fake.poll_session(session, now=0)
        command = session.commands.issue("Pause", {"paused"}, timeout_s=10, now=1)
        session.commands.accepted("Pause")
        self.assertFalse(command.terminal)
        _, changes = fake.poll_session(session, now=2)
        self.assertEqual([item.outcome for item in changes], ["confirmed"])
        self.assertTrue(command.terminal)

    def test_command_ack_times_out_deterministically(self):
        session = MoonrakerSessionState()
        session.commands.issue("Resume", {"printing"}, timeout_s=5, now=10)
        session.commands.accepted("Resume")
        changes = session.commands.observe("paused", now=15.1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].outcome, "timed_out")

    def test_published_snapshot_is_detached_from_session_internals(self):
        session = MoonrakerSessionState()
        session.merge_status({
            "print_stats": {"state": "printing", "info": {"current_layer": 3}},
            "gcode_move": {"gcode_position": [0, 0, 0.4, 10]},
        }, now=1)

        published = session.snapshot.copy_status()
        published["print_stats"]["info"]["current_layer"] = 999
        published["gcode_move"]["gcode_position"][2] = 50.0
        published["print_stats"]["state"] = "paused"

        internal = session.snapshot.status
        self.assertEqual(internal["print_stats"]["state"], "printing")
        self.assertEqual(internal["print_stats"]["info"]["current_layer"], 3)
        self.assertEqual(internal["gcode_move"]["gcode_position"], [0, 0, 0.4, 10])

    def test_merge_stores_defensive_copies_of_patch_values(self):
        session = MoonrakerSessionState()
        patch = {
            "print_stats": {"info": {"current_layer": 1}},
            "gcode_move": {"gcode_position": [1, 2, 3, 4]},
        }
        session.merge_status(patch, now=1)

        patch["print_stats"]["info"]["current_layer"] = 42
        patch["gcode_move"]["gcode_position"][0] = 99

        internal = session.snapshot.status
        self.assertEqual(internal["print_stats"]["info"]["current_layer"], 1)
        self.assertEqual(internal["gcode_move"]["gcode_position"], [1, 2, 3, 4])

    def test_long_print_simulation_keeps_one_monotonic_shared_snapshot(self):
        statuses = [
            {
                "print_stats": {
                    "state": "printing",
                    "filename": "long-print.gcode",
                    "print_duration": float(layer * 10),
                    "info": {"current_layer": layer},
                },
                "virtual_sdcard": {"file_size": 20_000_000, "file_position": layer * 9000},
            }
            for layer in range(1, 2001)
        ]
        fake = FakeMoonraker(statuses)
        session = MoonrakerSessionState()
        for tick in range(2000):
            status, changes = fake.poll_session(session, now=float(tick))
            self.assertFalse(changes)
            self.assertEqual(status["print_stats"]["info"]["current_layer"], tick + 1)
        self.assertEqual(session.snapshot.revision, 2000)
        self.assertEqual(fake.remaining, 0)
        self.assertEqual(len(fake.requests), 2000)


if __name__ == "__main__":
    unittest.main()
