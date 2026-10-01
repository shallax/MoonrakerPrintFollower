"""Executable qt monitor integration contracts."""
from tests import qt_integration_support as harness

class MonitorDataAuxTests(harness.MonitorDataAuxTests):
    def test_auxiliary_snapshot_prunes_removed_objects(self):
        self.deliver("objects", {"result": {"objects": ["fan", "heater_bed"]}})
        self.deliver("aux", {"result": {"status": {"fan": {"speed": 0.5}, "heater_bed": {"target": 60}}}})
        self.assertEqual(set(self.data.snapshot.auxiliary), {"fan", "heater_bed"})

        # The fan disappears from printer/objects/list; the next aux response
        # must stop keeping its stale values alive.
        self.transport.requests.clear()
        self.data.refresh_discovery()
        self.deliver("objects", {"result": {"objects": ["heater_bed"]}})
        self.deliver("aux", {"result": {"status": {"heater_bed": {"target": 55}}}})
        self.assertEqual(set(self.data.snapshot.auxiliary), {"heater_bed"})
        self.assertEqual(self.data.snapshot.auxiliary["heater_bed"]["target"], 55)

    def test_aux_accepts_objects_that_appeared_after_the_first_list(self):
        # A device switched on mid-print never appears in the first
        # objects/list — its data must still render and join the
        # subscription instead of being dropped (the rule).
        self.deliver("objects", {"result": {"objects": ["fan"]}})
        self.deliver("aux", {"result": {"status": {"fan": {"speed": 0.5},
                                                    "temperature_sensor mcu": {"temperature": 32.0}}}})
        self.assertEqual(set(self.data.snapshot.auxiliary), {"fan", "temperature_sensor mcu"})
        self.assertEqual(self.data.snapshot.auxiliary["temperature_sensor mcu"]["temperature"], 32.0)

    def test_websocket_mode_subscribes_aux_objects_before_any_aux_data(self):
        # Moonraker only pushes subscribed objects, so the wanted set
        # must reach the socket as soon as the object list is known —
        # waiting for the first fragment to issue the subscription
        # deadlocked and the temperatures never appeared.
        socket = harness.ScriptedSocket()
        client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=self.transport, socket=socket)
        self.addCleanup(client.stop)
        client.configure("http://printer-a", "test-key", 750, feed_mode="websocket")
        client.start()
        data = self.qt.load("MonitorData").MonitorData(client, None)
        data.set_owner_active(True)
        self.addCleanup(data.set_owner_active, False)
        data._objects({"result": {"objects": ["fan", "heater_bed"]}}, None)
        self.assertTrue(any("fan" in subscription and "heater_bed" in subscription
                            for subscription in socket.subscriptions), socket.subscriptions)

    def test_camera_url_rewrites_through_the_bridge_when_a_key_is_set(self):
        # A key-carrying camera cannot render through Cura's loader:
        # the URL is republished on the keyless loopback bridge (the
        # the 4.0.0 ruling), key and upstream riding the bridge.
        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeData(QObject):
            changed = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.active = True
                self.snapshot = harness.SimpleNamespace(webcams=(
                    {"uid": "front-uid", "name": "Front", "stream_url": "/webcam/?action=stream"},))

        config = self.qt.load("PrinterConfig").PrinterConfig(
            camera_selected="front-uid", url="http://printer-a", api_key="test-key")
        camera_module = self.qt.load("MonitorCamera")
        camera = camera_module.MonitorCamera(FakeData(), lambda: config, lambda value: None)
        self.addCleanup(lambda: camera._camera_bridge.stop() if camera._camera_bridge else None)
        # The selection restore is scheduled for the next Qt turn.
        self.qt.events(1)
        self.assertTrue(camera.url.startswith("http://127.0.0.1:"), camera.url)
        self.assertIn("/webcam/?action=stream", camera.url)
        self.assertTrue(camera._camera_bridge.active)

    def test_camera_restore_bails_when_webcams_changed_before_turn(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeData(QObject):
            changed = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.active = True
                self.snapshot = harness.SimpleNamespace(webcams=(
                    {"uid": "front-uid", "name": "Front", "stream_url": "/front"},))

        camera_module = self.qt.load("MonitorCamera")
        config = self.qt.load("PrinterConfig").PrinterConfig(camera_selected="front-uid")
        fake = FakeData()
        camera = camera_module.MonitorCamera(fake, lambda: config, lambda value: None)
        # The constructor's observe() published the Front model and scheduled
        # its selection restore for the next Qt turn.
        self.assertTrue(camera._restore_pending)
        first_signature = camera._camera_signature

        # A second snapshot replaces the webcam set before that turn arrives.
        # The change reaches observe() exactly as in production
        # (data.changed -> observe) and re-publishes the model for Rear.
        fake.snapshot = harness.SimpleNamespace(webcams=(
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},))
        fake.changed.emit()
        self.assertNotEqual(camera._camera_signature, first_signature)
        self.assertEqual(camera.values["activeWebcamIndex"], -1)

        # The stale Front restore fires first: it was scheduled for a snapshot
        # that is no longer current, so it must bail and keep the restore
        # pending rather than publish an index against the Rear model early.
        camera._restore_after_population(first_signature)
        self.assertTrue(camera._restore_pending)
        self.assertEqual(camera.values["activeWebcamIndex"], -1)

        # The restore scheduled against the current snapshot then completes.
        self.qt.events()
        self.assertFalse(camera._restore_pending)
        self.assertEqual(camera.values["activeWebcamIndex"], 0)
        self.assertEqual(camera.values["cameraName"], "Rear")

    def test_camera_url_guard_rejects_whitespace_masked_external_hosts(self):
        # QUrl strips surrounding whitespace per RFC 3986, so a
        # " //evil.example/x" stream must be rejected on the STRIPPED
        # form — otherwise a hostile listing points Cura's loader at
        # an arbitrary host (the adversarial round's catch).
        from PyQt6.QtCore import QObject, pyqtSignal
        camera_module = self.qt.load("MonitorCamera")
        config = self.qt.load("PrinterConfig").PrinterConfig(url="http://printer-a")
        class FakeData(QObject):
            changed = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.active = True
                self.snapshot = harness.SimpleNamespace(webcams=(
                    {"uid": "front-uid", "name": "Front", "stream_url": " //evil.example/x"},))
        fake = FakeData()
        camera = camera_module.MonitorCamera(fake, lambda: config, lambda value: None)
        self.assertEqual(camera.values["webcamNames"], ["Front"])
        self.assertEqual(camera.url, "")


