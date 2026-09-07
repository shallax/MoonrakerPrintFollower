"""Pure MoonrakerSession behaviour: poll policy, coalescing, rebind and commands."""
from __future__ import annotations

import unittest

from tests.fake_moonraker import FakeMoonraker
from plugins.MoonrakerSession import (
    MoonrakerSession,
    MoonrakerSessionState,
    PollPolicy,
    RequestCategory,
    RequestCoalescer,
)


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
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "paused"), 1500)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "standby"), 5000)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 750, "printing"), 1000)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 750, "standby"), 2500)
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
        session = MoonrakerSession(transport=transport)
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

    def test_session_reset_invalidates_old_generation_and_shared_state(self):
        # Drive the production identity path (MoonrakerSession.configure) and
        # then reset, as a rebind does, rather than a test-only state mutation.
        transport = _FakeTransport()
        session = MoonrakerSession(transport=transport)
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
        self.assertEqual(transport.cancel_calls, 1)

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
