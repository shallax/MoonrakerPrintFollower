import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
LIFECYCLE = (PLUGINS / "MoonrakerOutputDeviceLifecycle.py").read_text(encoding="utf-8")
BASE = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")


class OutputRebindLifecycleTests(unittest.TestCase):
    def test_output_device_is_deactivated_before_losing_active_ownership(self):
        self.assertIn("def _deactivate_device", PLUGIN)
        self.assertIn("self._set_monitor_active(self._current, False)", PLUGIN)
        self.assertIn("self._set_monitor_active(device, False)", PLUGIN)
        self.assertIn("deactivate()", PLUGIN)
        self.assertGreaterEqual(PLUGIN.count("self._deactivate_device(self._current)"), 3)

    def test_deactivation_cancels_transport_and_streaming_upload_work(self):
        start = LIFECYCLE.index("def deactivate")
        block = LIFECYCLE[start : start + 700]
        self.assertIn("had_started_write = bool(self._busy)", block)
        self.assertIn("self._release_dialog()", block)
        self.assertIn("self._cleanup()", block)
        self.assertIn("self._emit_write_finished_once()", block)
        self.assertIn("transport.cancel_owner(self._transport_owner())", BASE)
        self.assertIn("self._upload_reply.abort()", BASE)


if __name__ == "__main__":
    unittest.main()
