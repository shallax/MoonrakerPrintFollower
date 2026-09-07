from dataclasses import replace
import json
import unittest
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, ROOT, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class TuningCameraRegressionTests(unittest.TestCase):
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

    def test_slider_preview_does_not_publish_during_drag_and_commit_still_sends(self):
        model = self.monitor()
        self.deliver()
        tuning_changes = []
        control_changes = []
        model._tuning.changed.connect(lambda: tuning_changes.append(True))
        model.controlsChanged.connect(lambda: control_changes.append(True))

        for value in range(110, 121):
            model.previewSpeedFactor(value)
        self.assertEqual(tuning_changes, [])
        self.assertEqual(model._tuning.value("speed-factor", 100), 100)
        self.assertEqual(model.speedFactorPercent, 100)

        # A normal Moonraker poll during the active gesture must not publish the
        # preview value back through the bound Qt property and reset the Slider.
        self.deliver()
        self.assertEqual(model.speedFactorPercent, 100)
        self.assertEqual(control_changes, [])

        model._tuning.DEBOUNCE_MS = 10
        model.setSpeedFactor(137)
        self.assertEqual(len(tuning_changes), 1)
        self.assertEqual(model.speedFactorPercent, 137)
        self.assertEqual(len(control_changes), 1)
        self.qt.events(25)
        commands = [request for request in self.transport.requests if request.channel == "quick-speed-factor"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S137"})

    def test_monitor_does_not_broadcast_unrelated_ui_signals_on_every_poll(self):
        model = self.monitor()
        self.deliver()
        webcam_changes = []
        control_changes = []
        model.webcamsChanged.connect(lambda: webcam_changes.append(True))
        model.controlsChanged.connect(lambda: control_changes.append(True))

        self.deliver()
        self.assertEqual(webcam_changes, [])
        self.assertEqual(control_changes, [])

        model._data._update(webcams=[{"uid": "front", "name": "Front", "stream_url": "/front"}])
        self.assertEqual(len(webcam_changes), 1)
        self.assertEqual(control_changes, [])

    def test_slider_qml_prevents_parent_flickable_from_stealing_drag(self):
        qml = (ROOT / "plugins" / "MoonrakerMonitorDashboard.qml").read_text()
        self.assertIn("property bool tuningSliderPressed: false", qml)
        self.assertIn("interactive: !root.tuningSliderPressed", qml)
        for slider_id in ("speedSlider", "flowSlider", "fanSlider", "ledSlider", "redSlider",
                          "greenSlider", "blueSlider", "whiteSlider", "pwmSlider"):
            self.assertIn("id: " + slider_id, qml)
        self.assertGreaterEqual(qml.count("root.tuningSliderPressed = pressed"), 9)

    def test_selected_camera_is_flushed_immediately_and_restored_after_webcams_are_populated(self):
        model = self.monitor()
        cameras = [
            {"uid": "front-uid", "name": "Front", "stream_url": "/front"},
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},
        ]
        self.app.savePreferences = Mock()

        # The first camera publication deliberately populates the ComboBox model
        # without trying to restore a currentIndex into an empty model.
        model._data._update(webcams=cameras)
        self.assertEqual(model._camera.values["webcamNames"], ["Front", "Rear"])
        self.assertEqual(model.activeWebcamIndex, -1)
        self.qt.events()
        self.assertEqual(model.activeWebcamIndex, 0)

        model.selectWebcam(1)

        self.assertEqual(self.follower.current_printer_config().camera_selected, "rear-uid")
        self.assertEqual(model.activeWebcamIndex, 1)
        self.assertEqual(model.cameraName, "Rear")
        self.app.savePreferences.assert_called_once_with()

        config_module = self.qt.load("PrinterConfig")
        stored = json.loads(self.app.preferences.values[config_module.PrinterConfigStore.PREF_KEY])
        machine_id = self.follower.current_printer_identity()[0]
        self.assertEqual(stored[machine_id]["camera_selected"], "rear-uid")

        # Recreate the complete follower against the same Cura preference store,
        # rather than merely constructing another camera helper around the same
        # live configuration object. This exercises the persisted config path.
        app2 = self.qt.Application(preferences=self.app.preferences)
        transport2 = ScriptedTransport()
        runtime_module = self.qt.load("FollowerRuntime")
        real_client = runtime_module.MoonrakerClient
        with patch.object(runtime_module, "MoonrakerClient", lambda parent: real_client(parent, transport=transport2)):
            follower2 = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app2)
        self.addCleanup(follower2.deinitialize)
        self.assertEqual(follower2.current_printer_config().camera_selected, "rear-uid")

        output2 = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app2, follower2)
        output2.start()
        self.addCleanup(output2.stop)
        restored_model = output2._current.activePrinter

        # On Monitor startup/load, publish the dropdown contents first. Only on
        # the next Qt event turn should the saved UID be resolved and selected.
        restored_model._data._update(webcams=cameras)
        self.assertEqual(restored_model._camera.values["webcamNames"], ["Front", "Rear"])
        self.assertEqual(restored_model.activeWebcamIndex, -1)
        self.qt.events()
        self.assertEqual(restored_model.activeWebcamIndex, 1)
        self.assertEqual(restored_model.cameraName, "Rear")

        # Older configurations/frontends may have persisted the unique camera
        # name rather than Moonraker's UID. That must continue to restore the
        # same webcam instead of silently falling back to the first entry.
        follower2.apply_printer_config(replace(follower2.current_printer_config(), camera_selected="Rear"))
        restored_model._camera._key = None
        restored_model._camera.observe()
        self.assertEqual(restored_model.activeWebcamIndex, 1)
        self.assertEqual(restored_model.cameraName, "Rear")

    def test_camera_qml_uses_the_activated_signal_index_not_bound_current_index(self):
        qml = (ROOT / "plugins" / "MoonrakerMonitor.qml").read_text()
        self.assertIn("onActivated: function(index)", qml)
        self.assertIn("selectWebcam(index)", qml)
        self.assertNotIn("selectWebcam(cameraSelector.currentIndex)", qml)


if __name__ == "__main__":
    unittest.main()
