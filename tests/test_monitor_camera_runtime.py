"""Executable monitor camera runtime contracts."""
from tests import monitor_test_support as harness

class MonitorQtTests(harness.MonitorQtTests):
    def test_snapshot_endpoint_replaces_stream_only_at_one_fps_or_below(self):
        model = self.monitor()
        self.deliver()
        model._data._update(webcams=[{
            "uid": "cam", "name": "Cam", "stream_url": "/stream",
            "snapshot_url": "/snapshot", "target_fps": 30,
        }])
        self.qt.events()
        self.assertTrue(model.cameraSnapshotAvailable)
        self.assertFalse(model.cameraSnapshotMode)
        self.assertEqual(model._camera.url, "http://printer-a/stream")
        self.assertIn("http://printer-a/stream", model.cameraUrl)

        model.setCameraFps(1.0)
        self.qt.events()
        self.assertTrue(model.cameraSnapshotMode)
        self.assertEqual(model._camera.url, "http://printer-a/snapshot")
        self.assertIn("http://printer-a/snapshot", model.cameraUrl)
        model.setCameraFps(0.5)
        self.qt.events()
        self.assertTrue(model.cameraSnapshotMode)
        self.assertEqual(model._camera.url, "http://printer-a/snapshot")
        model.setCameraFps(1.5)
        self.qt.events()
        self.assertFalse(model.cameraSnapshotMode)
        self.assertEqual(model._camera.url, "http://printer-a/stream")

    def test_missing_or_unsafe_snapshot_keeps_the_stream_at_low_rate(self):
        model = self.monitor()
        self.deliver()
        for snapshot in ("", "//foreign/snapshot", "file:///etc/passwd"):
            with self.subTest(snapshot=snapshot):
                model._data._update(webcams=[{
                    "uid": "cam", "name": "Cam", "stream_url": "/stream",
                    "snapshot_url": snapshot, "target_fps": 30,
                }])
                self.qt.events()
                model.setCameraFps(0.5)
                self.qt.events()
                self.assertFalse(model.cameraSnapshotAvailable)
                self.assertFalse(model.cameraSnapshotMode)
                self.assertEqual(model._camera.url, "http://printer-a/stream")

    def test_webcam_list_survives_a_failed_poll(self):
        # Panel ARCH-P3-1: endstops retain last-known states on error;
        # webcams used to blank on ANY failed poll ("no camera" during a
        # printer reboot). Now they follow the same retention principle.
        model = self.monitor()
        self.qt.events(1)
        webcams = [r for r in self.transport.requests if r.channel == "webcams"]
        self.assertEqual(1, len(webcams), "one in-flight webcam RPC at a time (the coalescer)")
        webcams[0].callback({"result": {"webcams": [{"name": "Front", "stream_url": "/webcam", "enabled": True}]}}, None)
        self.qt.events(1)
        self.assertEqual(model.webcamNames, ["Front"])
        # The landed reply reopens the gate: the next poll issues again.
        model.refreshWebcams()
        self.qt.events(1)
        later = [r for r in self.transport.requests if r.channel == "webcams"][1:]
        self.assertTrue(later)
        later[-1].callback(None, "boom")
        self.qt.events(1)
        self.assertEqual(model.webcamNames, ["Front"])

    def test_controls_lock_and_camera_refresh_nonce(self):
        model = self.monitor()
        self.assertFalse(model.controlsLocked)
        model.setControlsLocked(True)
        self.assertTrue(model.controlsLocked)
        model.setControlsLocked(False)
        self.assertFalse(model.controlsLocked)
        before = model.cameraRefreshNonce
        model.refreshWebcams()
        self.assertEqual(model.cameraRefreshNonce, before + 1)

    def test_webcam_watchdog_veils_and_bumps_the_refresh_nonce(self):
        # A dead bridge relay bumps the refresh nonce (a URL change is
        # the only thing that restarts Cura's loader), throttled so a
        # dead stream cannot spin the loader, with the veil until the
        # stream restarts.
        model = self.monitor()
        model._camera_last_refresh_at = 0.0
        before = model.cameraRefreshNonce
        model._on_stream_failed()
        self.assertTrue(model.cameraRecovering)
        self.assertEqual(model.cameraRefreshNonce, before + 1)
        # A second failure inside the throttle window does not bump.
        model._on_stream_failed()
        self.assertEqual(model.cameraRefreshNonce, before + 1)
        model._on_stream_recovered()
        self.assertFalse(model.cameraRecovering)

    def test_camera_render_stall_rides_the_same_recovery(self):
        # The render watchdog (the live report): a stream
        # that connected but never painted a frame reports the stall
        # through the same nonce-bump recovery as a stream failure,
        # with the same 10 s throttle.
        model = self.monitor()
        model._camera_last_refresh_at = 0.0
        before = model.cameraRefreshNonce
        model.cameraRenderStalled()
        self.assertTrue(model.cameraRecovering)
        self.assertEqual(model.cameraRefreshNonce, before + 1)
        model.cameraRenderStalled()
        self.assertEqual(model.cameraRefreshNonce, before + 1)

    def test_the_rate_is_capped_by_the_selected_cameras_own_target(self):
        # The ceiling is the camera's configured target_fps, never a
        # product constant: a 15 FPS camera cannot be asked for 30, and
        # the commit stores what was actually applied — while the
        # OBSERVE path only lowers the effective rate, leaving the
        # preference alone for the faster camera it came from.
        model = self.monitor()
        model._data._update(webcams=[
            {"uid": "fast", "name": "Fast", "stream_url": "/fast", "target_fps": 60},
            {"uid": "slow", "name": "Slow", "stream_url": "/slow", "target_fps": 15},
        ])
        self.qt.events()
        self.assertEqual(model.cameraFpsMax, 60.0, "the selected camera's own ceiling")
        model.setCameraFps(45.0)
        self.qt.events()
        self.assertEqual(model.cameraFps, 45.0)
        self.assertEqual(self.follower.current_printer_config().camera_fps, 45.0)

        model.selectWebcam(1)  # the slower camera
        self.qt.events()
        self.assertEqual(model.cameraFpsMax, 15.0, "the new camera publishes its own ceiling")
        self.assertEqual(model.cameraFps, 15.0, "and the effective rate follows it down")
        self.assertEqual(self.follower.current_printer_config().camera_fps, 45.0,
                         "the stored preference is not rewritten by the cap")
        model.setCameraFps(30.0)  # past this camera's ceiling
        self.qt.events()
        self.assertEqual(model.cameraFps, 15.0, "the commit is capped at the camera")
        self.assertEqual(self.follower.current_printer_config().camera_fps, 45.0,
                         "a commit that cannot move the rate rewrites nothing")
        model.setCameraFps(10.0)  # inside this camera's ceiling
        self.qt.events()
        self.assertEqual(model.cameraFps, 10.0)
        self.assertEqual(self.follower.current_printer_config().camera_fps, 10.0,
                         "and a commit that does move it stores what was applied")
        model.selectWebcam(0)  # back to the fast camera
        self.qt.events()
        self.assertEqual(model.cameraFpsMax, 60.0)
        self.assertEqual(model.cameraFps, 10.0, "the user's own rate, uncapped by the slower camera")

    def test_a_camera_without_a_usable_target_fps_falls_back_to_the_render_ceiling(self):
        # An older Moonraker, or a front-end-written entry, carries no
        # target_fps: the ceiling is the renderer's own idle cadence
        # rather than a camera that cannot be read as one.
        model = self.monitor()
        for entry in (
                {"uid": "none", "name": "None", "stream_url": "/none"},
                {"uid": "zero", "name": "Zero", "stream_url": "/zero", "target_fps": 0},
                {"uid": "junk", "name": "Junk", "stream_url": "/junk", "target_fps": "nonsense"},
                {"uid": "huge", "name": "Huge", "stream_url": "/huge", "target_fps": 5000},
                {"uid": "tiny", "name": "Tiny", "stream_url": "/tiny", "target_fps": 0.01},
        ):
            with self.subTest(entry=entry["uid"]):
                model._data._update(webcams=[entry])
                self.qt.events()
                expected = {"huge": 120.0, "tiny": 0.5}.get(entry["uid"], 30.0)
                self.assertEqual(model.cameraFpsMax, expected,
                                 "a corrupt or absent target_fps lands on a usable ceiling")
                self.assertLessEqual(model.cameraFps, model.cameraFpsMax)
                self.assertGreaterEqual(model.cameraFps, model.cameraFpsMin)
                model.setCameraFps(120.0)
                self.qt.events()
                self.assertEqual(model.cameraFps, min(expected, 120.0),
                                 "the control can never ask the renderer for more")

    def test_a_reconfigured_camera_re_reads_its_own_ceiling(self):
        # The ceiling rides the camera SIGNATURE: an installation that
        # changes its stream's target_fps while the pane is open must
        # re-read it rather than keep offering the old range.
        model = self.monitor()
        cameras = [{"uid": "cam", "name": "Cam", "stream_url": "/cam", "target_fps": 30}]
        model._data._update(webcams=cameras)
        self.qt.events()
        self.assertEqual(model.cameraFpsMax, 30.0)
        model.setCameraFps(20.0)
        self.qt.events()

        reconfigured = [{"uid": "cam", "name": "Cam", "stream_url": "/cam", "target_fps": 10}]
        model._data._update(webcams=reconfigured)
        self.qt.events()
        self.assertEqual(model.cameraFpsMax, 10.0, "the re-configured camera's new ceiling")
        self.assertEqual(model.cameraFps, 10.0, "and the rate with it")
        # The stored preference is the user's, uncapped: a stream put
        # back to 30 restores the rate the user chose.
        model._data._update(webcams=cameras)
        self.qt.events()
        self.assertEqual(model.cameraFpsMax, 30.0)
        self.assertEqual(model.cameraFps, 20.0, "the user's own rate comes back with the room")

    def test_selected_camera_persists_through_the_settings_document_and_restores_after_webcams(self):
        model = self.monitor()
        cameras = [
            {"uid": "front-uid", "name": "Front", "stream_url": "/front"},
            {"uid": "rear-uid", "name": "Rear", "stream_url": "/rear"},
        ]

        # The first camera publication deliberately populates the ComboBox model
        # without trying to restore a currentIndex into an empty model.
        model._data._update(webcams=cameras)
        self.assertEqual(model._camera.values["webcamNames"], ["Front", "Rear"])
        self.assertEqual(model.activeWebcamIndex, -1)
        self.qt.events()
        self.assertEqual(model.activeWebcamIndex, 0)

        model.selectWebcam(1)
        self.qt.events()  # the publish coalescer flushes on the next turn

        self.assertEqual(self.follower.current_printer_config().camera_selected, "rear-uid")
        self.assertEqual(model.activeWebcamIndex, 1)
        self.assertEqual(model.cameraName, "Rear")
        # The 4.5.0 world: the camera selection persists through the
        # facade's settings document (SaveFile's fsync makes the save
        # durable) — the preference-flush pin retired with the
        # transcript.
        document = self.follower.persistence.settings_document()
        machine_id = self.follower.current_printer_identity()[0]
        self.assertEqual(document["machines"][machine_id]["camera_selected"], "rear-uid")

        # Recreate the complete follower against the same Cura preference store,
        # rather than merely constructing another camera helper around the same
        # live configuration object. This exercises the persisted config path.
        app2 = self.qt.Application(preferences=self.app.preferences)
        transport2 = harness.ScriptedTransport()
        runtime_module = self.qt.load("FollowerRuntime")
        real_client = runtime_module.MoonrakerClient
        with harness.patch.object(runtime_module, "MoonrakerClient", lambda parent: real_client(parent, transport=transport2, socket=harness.ScriptedSocket())):
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
        follower2.apply_printer_config(harness.replace(follower2.current_printer_config(), camera_selected="Rear"))
        restored_model._camera._key = None
        restored_model._camera.observe()
        self.assertEqual(restored_model.activeWebcamIndex, 1)
        self.assertEqual(restored_model.cameraName, "Rear")
