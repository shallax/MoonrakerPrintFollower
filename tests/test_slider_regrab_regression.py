import unittest
from unittest.mock import patch

from qt_runtime_support import QT_AVAILABLE, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SliderRegrabRegressionTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False))

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def deliver(self):
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation)

    def test_regrabbing_slider_keeps_last_released_value_until_next_release(self):
        model = self.monitor()
        self.deliver()
        model._tuning.DEBOUNCE_MS = 1000

        # First release is published immediately, while its G-code remains
        # debounced. This is the position the next gesture must start from.
        model.setSpeedFactor(137)
        self.assertEqual(model.speedFactorPercent, 137)

        # Re-grab before the command has been sent. A status poll still reports
        # the printer's old 100% value, but must not push the bound Slider back.
        model.previewSpeedFactor(145)
        self.assertEqual(model.speedFactorPercent, 137)
        self.deliver()
        self.assertEqual(model.speedFactorPercent, 137)

        # Releasing the second gesture replaces the cancelled 137% command and
        # sends only the new value after the debounce interval.
        model._tuning.DEBOUNCE_MS = 10
        model.setSpeedFactor(145)
        self.assertEqual(model.speedFactorPercent, 145)
        self.qt.events(25)
        commands = [request for request in self.transport.requests if request.channel == "quick-speed-factor"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S145"})


if __name__ == "__main__":
    unittest.main()
