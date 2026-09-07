from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
if str(PLUGINS) not in sys.path:
    sys.path.insert(0, str(PLUGINS))

from CuraLifecycleBridge import CuraLifecycleBridge
from MoonrakerSession import MoonrakerSessionState


class V31RaceGuardTests(unittest.TestCase):
    def test_stale_cura_lifecycle_callback_is_rejected(self):
        bridge = CuraLifecycleBridge()
        token = bridge.token()
        observed: list[str] = []

        bridge.invalidate("scene replaced")

        self.assertFalse(bridge.guarded(token, lambda: observed.append("stale")))
        self.assertEqual(observed, [])
        current = bridge.token()
        self.assertTrue(bridge.guarded(current, lambda: observed.append("current")))
        self.assertEqual(observed, ["current"])

    def test_session_reset_invalidates_old_generation_and_shared_state(self):
        session = MoonrakerSessionState()
        session.rebind("http://printer-a")
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

    def test_active_machine_switch_resets_session_before_new_binding(self):
        source = (PLUGINS / "FollowerConfiguration.py").read_text(encoding="utf-8")
        start = source.index("def _on_active_machine_changed")
        end = source.index("def _active_printer_is_configured_for_following", start)
        switch = source[start:end]

        stop_pos = switch.index("self._client.stop()")
        invalidate_pos = switch.index('self._invalidate_lifecycle("active Cura printer changed")')
        assign_pos = switch.index("self._active_machine_id = machine_id")
        apply_pos = switch.index("self._apply_timer_state()")

        self.assertLess(stop_pos, invalidate_pos)
        self.assertLess(invalidate_pos, assign_pos)
        self.assertLess(assign_pos, apply_pos)

        client = (PLUGINS / "MoonrakerClient.py").read_text(encoding="utf-8")
        self.assertIn("def stop(self, *, reset_session: bool = True)", client)
        self.assertIn("if reset_session:\n            self._session.reset()", client)

    def test_specialised_follower_replies_validate_lifecycle_and_job_identity(self):
        transport = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
        transfer = (PLUGINS / "RemoteFileTransfer.py").read_text(encoding="utf-8")

        self.assertIn(
            "reply_generation != self._lifecycle_generation or reply_job_key != self._remote_job_key",
            transport,
        )
        self.assertIn(
            "reply_generation != self._lifecycle_generation\n                or reply_job_key != self._remote_job_key",
            transfer,
        )
        self.assertIn(
            "request_generation != self._scheduled_pause_request_generation",
            transport,
        )
        self.assertIn("lifecycle_generation != self._lifecycle_generation", transport)
        self.assertIn("job_key != self._remote_job_key", transport)


if __name__ == "__main__":
    unittest.main()
