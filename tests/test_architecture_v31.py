from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

from tests.fake_moonraker import FakeMoonraker
from plugins.MoonrakerSession import (
    MoonrakerSession,
    MoonrakerSessionState,
    PollPolicy,
    RequestCategory,
    RequestCoalescer,
)
from plugins.PauseScheduleService import PauseScheduleService
from plugins.RemoteJobService import RemoteJobService


RUNTIME_COMPONENTS = (
    "FollowerBootstrap.py",
    "FollowerConfiguration.py",
    "CuraLifecycleRuntime.py",
    "CuraViewBridge.py",
    "CuraFileLifecycle.py",
    "PreviewFollowerRuntime.py",
    "PreviewStatus.py",
    "PreviewEta.py",
    "PreviewControls.py",
    "PreviewLoad.py",
    "PreviewFollowEngine.py",
    "PathFollowEngine.py",
    "GCodeIndexRuntime.py",
    "RemoteFileTransfer.py",
)


class _FakeTransport:
    def __init__(self) -> None:
        self.identity = ("", "")
        self.configure_calls = []

    def configure(self, base_url: str, api_key: str) -> bool:
        target = (str(base_url or "").rstrip("/"), str(api_key or ""))
        changed = target != self.identity
        self.identity = target
        self.configure_calls.append(target)
        return changed


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

    def test_scheduled_pause_tightens_polling_and_confirms_from_status(self):
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
            session.poll_policy.interval_ms(RequestCategory.CORE, 750, session.snapshot.printer_state, urgent=session.pause_guard),
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
        jobs = RemoteJobService({"printing", "paused"})
        first = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 120},
            {"file_size": 1000, "file_position": 600},
        )
        second = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 180},
            {"file_size": 1000, "file_position": 800},
        )
        restarted = jobs.observe(
            {"state": "printing", "filename": "part.gcode", "print_duration": 3},
            {"file_size": 1000, "file_position": 20},
        )
        self.assertTrue(first.new_job)
        self.assertFalse(second.new_job)
        self.assertTrue(restarted.new_job)
        self.assertNotEqual(first.key, restarted.key)

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

    def test_exact_service_boundaries_exist_without_duplicate_wrappers(self):
        required = (
            "RemoteJobService.py", "RemoteFileService.py", "GCodeIndexService.py",
            "PreviewFollowerService.py", "PauseScheduleService.py", "CuraLifecycleBridge.py",
            "MoonrakerSession.py",
        )
        for name in required:
            self.assertTrue((PLUGINS / name).is_file(), name)
        redundant = (
            "PrintTracker.py", "GCodeRepository.py", "PreviewController.py", "PauseScheduler.py",
            "FollowerSession.py", "FollowerStateBridge.py", "MoonrakerMonitorSession.py",
            "MoonrakerOutputSession.py", "NativeNozzleFallback.py",
        )
        for name in redundant:
            self.assertFalse((PLUGINS / name).exists(), name)

    def test_runtime_composition_is_complete_and_has_no_private_http_stack(self):
        runtime = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
        tree = ast.parse(runtime)
        relative_modules = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
        }
        for name in RUNTIME_COMPONENTS:
            module = name[:-3]
            self.assertIn(module, relative_modules, module)
            self.assertTrue((PLUGINS / name).is_file(), name)
        implementation = "\n".join(
            (PLUGINS / name).read_text(encoding="utf-8")
            for name in ("FollowerRuntime.py",) + RUNTIME_COMPONENTS
        )
        self.assertNotIn("QNetworkAccessManager", implementation)
        self.assertNotIn("QNetworkRequest", implementation)

    def test_public_follower_is_thin_and_coordinator_uses_exact_services(self):
        facade = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")
        coordinator = (PLUGINS / "FollowerCoordinator.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
        eta = (PLUGINS / "PreviewEta.py").read_text(encoding="utf-8")
        load = (PLUGINS / "PreviewLoad.py").read_text(encoding="utf-8")
        follow = (PLUGINS / "PreviewFollowEngine.py").read_text(encoding="utf-8")
        self.assertLess(len(facade.splitlines()), 20)
        self.assertIn("class FollowerCoordinator(FollowerTransportMixin, _FollowerRuntime)", coordinator)
        for token in (
            "RemoteJobService", "RemoteFileService", "GCodeIndexService",
            "PreviewFollowerService", "PauseScheduleService", "CuraLifecycleBridge",
        ):
            self.assertIn(token, coordinator)
        self.assertIn("_ensure_remote_metadata", transport)
        self.assertIn("_begin_gcode_download", transport)
        self.assertIn("_send_scheduled_pause", transport)
        self.assertIn("_update_selected_layer_eta", eta)
        self.assertIn("_load_cached_remote_gcode_forced", load)
        self.assertIn("self._preview_follower_service.apply_layer_decision", follow)

    def test_monitor_core_and_peripheral_json_use_shared_transport(self):
        client = (PLUGINS / "MoonrakerClient.py").read_text(encoding="utf-8")
        session = (PLUGINS / "MoonrakerSession.py").read_text(encoding="utf-8")
        monitor = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "MoonrakerTransport.py").read_text(encoding="utf-8")
        self.assertIn("MoonrakerSession", client)
        self.assertIn("self._session.transport.send_json", client)
        self.assertIn("MoonrakerHttpTransport", session)
        self.assertIn("transport.send_json", monitor)
        self.assertIn("client.force_refresh", monitor)
        self.assertNotIn("status_endpoint", monitor)
        self.assertNotIn("QNetworkAccessManager", monitor)
        self.assertIn("QNetworkAccessManager", transport)
        self.assertNotIn("QNetworkAccessManager", client)
        self.assertNotIn("QWebSocket", client)
        self.assertNotIn("websocket", client.lower())

    def test_output_and_follower_reuse_shared_transport(self):
        output = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        follower_transport = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
        self.assertIn("transport.send_json", output)
        self.assertIn("transport.network.post", output)
        self.assertNotIn("QNetworkAccessManager", output)
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
            "self._generation", "cancel_owner", "cancel_all", "request_id",
            "category=", "elapsed_ms=", "TransportMetrics", "average_elapsed_ms",
        ):
            self.assertIn(token, source)

    def test_output_reuses_shared_readiness(self):
        source = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        self.assertIn("_shared_client_ready", source)
        self.assertIn("self._upload_now()", source)
        self.assertNotIn("super()._wait_for_ready()", source)


if __name__ == "__main__":
    unittest.main()
