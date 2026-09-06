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
from PauseScheduleService import PauseScheduleService
from PrintTracker import PrintObservation, PrintTracker


class V31ArchitectureTests(unittest.TestCase):
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

    def test_scheduled_pause_flow_tightens_polling_and_confirms_from_status(self):
        fake = FakeMoonraker([
            {"print_stats": {"state": "printing", "info": {"current_layer": 9}}},
            {"print_stats": {"state": "printing", "info": {"current_layer": 10}}},
            {"print_stats": {"state": "printing", "info": {"current_layer": 11}}},
            {"print_stats": {"state": "paused", "info": {"current_layer": 11}}},
        ])
        session = MoonrakerSessionState()
        scheduler = PauseScheduleService()

        fake.poll_session(session, now=0)
        self.assertTrue(scheduler.schedule(10))
        self.assertTrue(scheduler.is_imminent(9, lookahead_layers=1))
        session.set_pause_guard(True)
        self.assertEqual(
            session.poll_policy.interval_ms(
                RequestCategory.CORE,
                750,
                session.snapshot.printer_state,
                urgent=session.pause_guard,
            ),
            250,
        )

        fake.poll_session(session, now=1)
        self.assertEqual(scheduler.consume_due(10), [])
        fake.poll_session(session, now=2)
        self.assertEqual(scheduler.consume_due(11), [10])
        session.set_pause_guard(False)

        command = session.commands.issue("ScheduledPause", {"paused"}, timeout_s=10, now=2)
        fake.request("POST", "/printer/gcode/script", {"script": "PAUSE"})
        session.commands.accepted("ScheduledPause")
        self.assertFalse(command.terminal)
        _, changes = fake.poll_session(session, now=3)
        self.assertEqual([item.outcome for item in changes], ["confirmed"])
        self.assertEqual(fake.commands[-1].name, "PAUSE")

    def test_same_filename_restart_gets_new_print_run_identity(self):
        tracker = PrintTracker({"printing", "paused"})
        first = tracker.observe(
            PrintObservation("printing", "part.gcode", 1000, 600, 120),
            previous_state="standby",
        )
        self.assertTrue(first.new_job)
        second = tracker.observe(
            PrintObservation("printing", "part.gcode", 1000, 800, 180),
            previous_state="printing",
        )
        self.assertFalse(second.new_job)
        restarted = tracker.observe(
            PrintObservation("printing", "part.gcode", 1000, 20, 3),
            previous_state="printing",
        )
        self.assertTrue(restarted.new_job)
        self.assertNotEqual(first.key, restarted.key)

    def test_active_architecture_has_one_service_per_domain(self):
        coordinator = (PLUGINS / "FollowerCoordinator.py").read_text(encoding="utf-8")
        for token in (
            "RemoteJobService",
            "RemoteFileService",
            "GCodeIndexService",
            "PreviewFollowerService",
            "PauseScheduleService",
            "CuraLifecycleBridge",
            "FollowerTransportMixin",
        ):
            self.assertIn(token, coordinator)
        for obsolete in ("PauseScheduler.py", "PreviewController.py", "FollowerStateBridge.py"):
            self.assertFalse((PLUGINS / obsolete).exists(), obsolete)

    def test_public_follower_is_thin_and_runtime_is_compatibility_boundary(self):
        facade = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")
        coordinator = (PLUGINS / "FollowerCoordinator.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
        runtime = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
        self.assertLess(len(facade.splitlines()), 20)
        self.assertIn("class FollowerCoordinator(FollowerTransportMixin, _FollowerRuntime)", coordinator)
        self.assertIn("_ensure_remote_metadata", transport)
        self.assertIn("_begin_gcode_download", transport)
        self.assertIn("_send_scheduled_pause", transport)
        self.assertIn("_update_selected_layer_eta", runtime)
        self.assertIn("_load_cached_remote_gcode_forced", runtime)

    def test_monitor_core_and_peripheral_json_use_shared_transport(self):
        client = (PLUGINS / "MoonrakerClient.py").read_text(encoding="utf-8")
        session = (PLUGINS / "MoonrakerSession.py").read_text(encoding="utf-8")
        monitor_session = (PLUGINS / "MoonrakerMonitorSession.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "MoonrakerTransport.py").read_text(encoding="utf-8")
        self.assertIn("MoonrakerSession", client)
        self.assertIn("self._session.transport.send_json", client)
        self.assertIn("MoonrakerHttpTransport", session)
        self.assertIn("transport.send_json", monitor_session)
        self.assertIn("client.force_refresh", monitor_session)
        self.assertNotIn("status_endpoint", monitor_session)
        self.assertIn("QNetworkAccessManager", transport)
        self.assertNotIn("QNetworkAccessManager", client)
        self.assertNotIn("QWebSocket", client)
        self.assertNotIn("websocket", client.lower())

    def test_output_and_follower_reuse_shared_transport(self):
        output = (PLUGINS / "MoonrakerOutputSession.py").read_text(encoding="utf-8")
        follower_transport = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
        self.assertIn("transport.send_json", output)
        self.assertIn("self._network = transport.network", output)
        self.assertIn("self._client.transport.send_json", follower_transport)
        self.assertIn("self._client.transport.request", follower_transport)
        self.assertIn("self._client.transport.network.get", follower_transport)

    def test_connection_probe_reuses_transport_implementation_but_is_isolated(self):
        action = (PLUGINS / "MoonrakerFollowerMachineAction.py").read_text(encoding="utf-8")
        self.assertIn("MoonrakerHttpTransport", action)
        self.assertIn("self._probe_transport", action)
        self.assertIn("self._probe_transport.send_json", action)
        self.assertNotIn("QNetworkAccessManager", action)

    def test_transport_centralizes_cancellation_generation_and_observability(self):
        source = (PLUGINS / "MoonrakerTransport.py").read_text(encoding="utf-8")
        for token in (
            "self._generation",
            "cancel_owner",
            "cancel_all",
            "request_id",
            "category=",
            "elapsed_ms=",
            "TransportMetrics",
            "average_elapsed_ms",
        ):
            self.assertIn(token, source)

    def test_output_reuses_shared_readiness(self):
        source = (PLUGINS / "MoonrakerOutputSession.py").read_text(encoding="utf-8")
        self.assertIn("_shared_client_ready", source)
        self.assertIn("self._upload_now()", source)
        self.assertIn("super()._wait_for_ready()", source)


if __name__ == "__main__":
    unittest.main()
