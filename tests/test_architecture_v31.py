from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fake_moonraker import FakeMoonraker
from MoonrakerSession import MoonrakerSessionState, PollPolicy, RequestCategory, RequestCoalescer
from PrintTracker import PrintObservation, PrintTracker


class V31ArchitectureTests(unittest.TestCase):
    def test_polling_is_category_and_state_aware(self):
        policy = PollPolicy()
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "printing"), 750)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "paused"), 1500)
        self.assertEqual(policy.interval_ms(RequestCategory.CORE, 750, "standby"), 5000)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 750, "printing"), 1000)
        self.assertEqual(policy.interval_ms(RequestCategory.AUXILIARY, 750, "standby"), 2500)
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

    def test_command_ack_requires_observed_printer_state(self):
        fake = FakeMoonraker([
            {"print_stats": {"state": "printing"}},
            {"print_stats": {"state": "paused"}},
        ])
        session = MoonrakerSessionState()
        session.merge_status(fake.next_status(), now=0)
        command = session.commands.issue("Pause", {"paused"}, timeout_s=10, now=1)
        session.commands.accepted("Pause")
        self.assertFalse(command.terminal)
        _, changes = session.merge_status(fake.next_status(), now=2)
        self.assertEqual([item.outcome for item in changes], ["confirmed"])
        self.assertTrue(command.terminal)

    def test_command_ack_times_out_deterministically(self):
        session = MoonrakerSessionState()
        session.commands.issue("Resume", {"printing"}, timeout_s=5, now=10)
        session.commands.accepted("Resume")
        changes = session.commands.observe("paused", now=15.1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].outcome, "timed_out")

    def test_same_filename_restart_gets_new_print_run_identity(self):
        tracker = PrintTracker({"printing", "paused"})
        first = tracker.observe(PrintObservation("printing", "part.gcode", 1000, 600, 120), previous_state="standby")
        self.assertTrue(first.new_job)
        second = tracker.observe(PrintObservation("printing", "part.gcode", 1000, 800, 180), previous_state="printing")
        self.assertFalse(second.new_job)
        restarted = tracker.observe(PrintObservation("printing", "part.gcode", 1000, 20, 3), previous_state="printing")
        self.assertTrue(restarted.new_job)
        self.assertNotEqual(first.key, restarted.key)

    def test_public_follower_is_thin_and_runtime_behaviour_is_preserved(self):
        facade = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")
        coordinator = (PLUGINS / "FollowerCoordinator.py").read_text(encoding="utf-8")
        runtime = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
        self.assertLess(len(facade.splitlines()), 20)
        self.assertIn("class FollowerCoordinator", coordinator)
        self.assertIn("PrintTracker", coordinator)
        self.assertIn("PauseScheduler", coordinator)
        self.assertIn("GCodeRepository", coordinator)
        self.assertIn("_update_selected_layer_eta", runtime)
        self.assertIn("_send_scheduled_pause", runtime)
        self.assertIn("_load_cached_remote_gcode_forced", runtime)

    def test_monitor_core_is_shared_and_http_only(self):
        client = (PLUGINS / "MoonrakerClient.py").read_text(encoding="utf-8")
        monitor_session = (PLUGINS / "MoonrakerMonitorSession.py").read_text(encoding="utf-8")
        self.assertIn("RequestCoalescer", (PLUGINS / "MoonrakerSession.py").read_text(encoding="utf-8"))
        self.assertIn("self._core_timer.stop()", monitor_session)
        self.assertIn("client.force_refresh", monitor_session)
        self.assertNotIn("status_endpoint", monitor_session)
        self.assertNotIn("QWebSocket", client)
        self.assertNotIn("websocket", client.lower())

    def test_output_reuses_shared_readiness(self):
        source = (PLUGINS / "MoonrakerOutputSession.py").read_text(encoding="utf-8")
        self.assertIn("_shared_client_ready", source)
        self.assertIn("self._upload_now()", source)
        self.assertIn("super()._wait_for_ready()", source)


if __name__ == "__main__":
    unittest.main()
