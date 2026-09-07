import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL = (ROOT / "plugins" / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
DATA = (ROOT / "plugins" / "MonitorData.py").read_text(encoding="utf-8")


class MonitorPollPolicyTests(unittest.TestCase):
    def test_monitor_consumes_shared_session_poll_policy(self):
        self.assertIn("self.client.session.snapshot.printer_state", DATA)
        self.assertIn("poll_policy.interval_ms(category, 1000", DATA)
        self.assertIn("if timer.interval() != interval:", DATA)
        for category in (
            "RequestCategory.AUXILIARY",
            "RequestCategory.POWER",
            "RequestCategory.SYSTEM",
            "RequestCategory.DISCOVERY",
        ):
            self.assertIn(category, DATA)

    def test_monitor_has_one_timer_policy_owner(self):
        owners = [source for source in (MODEL, DATA) if any(
            isinstance(node, ast.FunctionDef) and node.name == "_intervals"
            for node in ast.walk(ast.parse(source))
        )]
        self.assertEqual(len(owners), 1)
        self.assertIs(owners[0], DATA)


if __name__ == "__main__":
    unittest.main()
