import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "plugins" / "MoonrakerMonitorRuntime.py").read_text(encoding="utf-8")


class MonitorPollPolicyTests(unittest.TestCase):
    def test_monitor_consumes_shared_session_poll_policy(self):
        self.assertIn("session = self._follower.session", RUNTIME)
        self.assertIn("policy = session.poll_policy", RUNTIME)
        self.assertIn("printer_state = session.snapshot.printer_state", RUNTIME)
        self.assertIn("policy.interval_ms(category, configured_ms, printer_state)", RUNTIME)
        for category in (
            "RequestCategory.AUXILIARY",
            "RequestCategory.POWER",
            "RequestCategory.SYSTEM",
            "RequestCategory.DISCOVERY",
        ):
            self.assertIn(category, RUNTIME)

    def test_monitor_reapplies_policy_after_each_core_status(self):
        start = RUNTIME.index("def _after_core_status")
        block = RUNTIME[start : start + 500]
        self.assertIn("super()._after_core_status(status)", block)
        self.assertIn("self._apply_adaptive_monitor_intervals()", block)


if __name__ == "__main__":
    unittest.main()
