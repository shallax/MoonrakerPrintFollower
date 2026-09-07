import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
LIFECYCLE = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
BASE = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")


class OutputRebindLifecycleTests(unittest.TestCase):
    def test_output_device_is_deactivated_before_losing_active_ownership(self):
        self.assertIn("def _deactivate_device", PLUGIN)
        self.assertIn("sessionInvalidated.connect(self._invalidate_devices)", PLUGIN)
        self.assertIn("self._set_monitor_active(device, False)", PLUGIN)
        self.assertIn("deactivate()", PLUGIN)
        self.assertGreaterEqual(PLUGIN.count("self._deactivate_device(self._current)"), 3)

    def test_deactivation_cancels_transport_and_streaming_upload_work(self):
        cleanup = LIFECYCLE[LIFECYCLE.index("def _finish"):]
        self.assertIn("transport.cancel_owner(self.owner)", cleanup)
        self.assertLess(cleanup.index("reply, self._reply = self._reply, None"), cleanup.index("reply.abort()"))
        self.assertIn("self._generation += 1", cleanup)
        self.assertIn("QTimer.singleShot(0, terminal)", cleanup)
        self.assertIn("self._upload.terminal_delivered()", BASE)


if __name__ == "__main__":
    unittest.main()
