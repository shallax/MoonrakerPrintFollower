"""Executable detection owner contracts for run identity, actions and exact observations."""
from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage
from mpf.detection.DetectionObservation import DetectionBox, DetectionSample, DetectionResult, ModelDetections
from mpf.settings.PrinterConfig import PrinterConfig
from tests.qt_runtime_support import runtime


class Detector(QObject):
    stateChanged = pyqtSignal()
    observationReady = pyqtSignal(object)
    ready = True
    enabled = True

    def __init__(self):
        super().__init__()
        self.samples = []
        self.resets = 0

    def reset(self): self.resets += 1
    def sample(self, *args): self.samples.append(args)
    def enqueue_io(self, key, callback):
        callback()
        return True


class Store:
    def __init__(self): self.records, self.fail = {}, False
    def get_machine_state(self, printer): return deepcopy(self.records.get(printer, {}))
    def set_machine_state(self, printer, patch):
        if self.fail: return False
        self.records.setdefault(printer, {}).update(deepcopy(patch))
        return True


class MonitorDetectionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime()
        self.runtime.__enter__()
        self.addCleanup(self.runtime.__exit__, None, None, None)
        from mpf.monitor.MonitorDetection import MonitorDetection
        from mpf.monitor.MonitorPermissions import Observation
        self.Owner = MonitorDetection
        self.config = PrinterConfig(url="http://printer", camera_url="http://printer/camera",
            detection_enabled=True, detection_notify_enabled=True, detection_pause_enabled=True,
            detection_safe_seconds=0)
        self.store = Store()
        self.detector = Detector()
        self.client = SimpleNamespace(session=SimpleNamespace(generation=1))
        self.job = SimpleNamespace(job_key=("part.gcode", 100, 1))
        self.camera = SimpleNamespace(url="http://printer/camera", values={"activeWebcamIndex": None,
            "cameraName": "Configured camera", "cameraSnapshotMode": False})
        self.recovery = SimpleNamespace(nonce=1, stream_enabled=True)
        self.requests = []
        self.data = SimpleNamespace(active=True, connection_state="yes",
            snapshot=SimpleNamespace(core={"print_stats": {"state": "printing", "filename": "part.gcode", "print_duration": 600}}, webcams=[]),
            observation=Observation(True, "yes", "printing", "xyz", False, False, False, False,
                                    pause_resume_supported=True), request=self.request)
        self.commands = SimpleNamespace(report_status=Mock(), send=Mock(return_value=True))
        self.owner = self.make_owner()
        self.owner._detection_values()

    def request(self, channel, method, path, callback):
        self.requests.append(callback)
        return True

    def apply(self, config):
        self.config = config
        return True

    def make_owner(self):
        result = self.Owner(detection=self.detector, data=self.data, client=self.client,
            camera=self.camera, recovery=self.recovery, config=lambda: self.config,
            apply_config=self.apply, identity=lambda: ("printer-one", "Printer"),
            print_state=lambda: self.job, commands=self.commands, store=self.store,
            publish=lambda: None, schedule_publish=lambda: None, published_values=lambda: {},
            printer_name=lambda: "Printer")
        result._notify_detection = Mock()
        return result

    def attest(self, job_id="server-run-one", callback=None):
        (callback or self.requests[-1])({"result": {"jobs": [dict(job_id=job_id,
            start_time=1000.0, filename="part.gcode", status="in_progress", end_time=None)]}}, "")

    def observation(self, score=.1, captured_at=None, context=None):
        image = QImage(12, 8, QImage.Format.Format_RGB888)
        image.fill(0x123456)
        return DetectionResult(DetectionSample(image, context or self.owner._detection_context(),
            time.monotonic() if captured_at is None else captured_at),
            ModelDetections(score, (DetectionBox(.1, .2, .3, .4, .8),)))

    def test_mute_persists_same_server_run_across_changed_local_serial_and_clears_for_next_run(self):
        self.attest()
        self.owner._on_detection_observation(self.observation())
        boxes = self.owner.detectionBoxes
        frames = self.owner._detection_policy.baseline["frames"]
        self.owner.setDetectionMuted(True)
        self.assertEqual(self.owner.detectionBoxes, boxes)
        self.owner._on_detection_observation(self.observation(4))
        self.assertGreater(self.owner._detection_policy.baseline["frames"], frames)
        self.commands.send.assert_not_called()
        self.owner._notify_detection.assert_not_called()
        self.client.session.generation += 1
        self.job.job_key = ("part.gcode", 100, 9)
        restored = self.make_owner()
        restored._detection_values()
        self.attest()
        self.assertTrue(restored.detectionMuted)
        self.job.job_key = ("part.gcode", 100, 10)
        restored._detection_values()
        self.attest("server-run-two")
        self.assertFalse(restored.detectionMuted)

    def test_pending_guard_is_durable_before_dispatch_and_unknown_reply_remains_guarded_after_restart(self):
        self.attest()
        def sent(*args, **kwargs):
            record = self.store.records["printer-one"]["detectionActions"]
            self.assertTrue(record["pausePending"])
            self.assertEqual(record["pauseAttempt"], kwargs["command_id"])
            return True
        self.commands.send.side_effect = sent
        context = self.owner._detection_context()
        self.owner._handle_detection_action(context, "failure", time.time())
        attempt = self.owner._detection_pause_attempt
        self.owner._on_detection_pause_command(dict(name="Pause", commandId=attempt,
            terminal=True, outcome="failed", detail="outcome unknown: connection lost"))
        self.assertTrue(self.store.records["printer-one"]["detectionActions"]["pauseUncertain"])
        restored = self.make_owner()
        restored._detection_values()
        self.attest()
        self.commands.send.reset_mock()
        restored._handle_detection_action(restored._detection_context(), "failure", time.time()+100)
        self.commands.send.assert_not_called()
        self.assertTrue(restored.detectionPauseRearmable)

    def test_failed_guard_save_never_dispatches_and_failed_rearm_does_not_change_epoch(self):
        self.attest()
        self.store.fail = True
        self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time())
        self.commands.send.assert_not_called()
        self.store.fail = False
        self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time()+100)
        self.owner._on_detection_pause_command(dict(name="Pause", commandId=self.owner._detection_pause_attempt,
            terminal=True, outcome="confirmed"))
        epoch = self.owner._detection_epoch
        self.store.fail = True
        self.owner.rearmDetectionPause()
        self.assertEqual(self.owner._detection_epoch, epoch)
        self.assertTrue(self.owner.detectionPauseRearmable)

    def test_aba_tuning_rearm_and_old_pause_tokens_cannot_adopt_retired_results(self):
        self.attest()
        old = self.observation(4)
        self.owner.setDetectionThresholds(20, 60)
        self.owner.setDetectionThresholds(38, 78)
        self.assertNotEqual(self.owner._detection_context(), old.sample.context)
        self.owner._on_detection_observation(old)
        self.assertIsNone(self.owner._detection_policy.state(now=time.monotonic(),
            context=self.owner._detection_context(), active=True).score)
        self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time())
        self.owner._on_detection_pause_command(dict(name="Pause", commandId="old-attempt",
            terminal=True, outcome="confirmed"))
        self.assertFalse(self.owner._detection_paused_for_print)
        self.assertTrue(self.owner._detection_pause_pending)

    def test_stale_out_of_order_and_wrong_source_samples_are_dropped(self):
        self.owner._on_detection_observation(self.observation(captured_at=time.monotonic()-31))
        self.assertEqual(self.owner.detectionBoxes, [])
        first = self.observation()
        self.owner._on_detection_observation(first)
        frames = self.owner._detection_policy.baseline["frames"]
        self.owner._on_detection_observation(self.observation(4, captured_at=first.sample.captured_at-.01))
        self.assertEqual(self.owner._detection_policy.baseline["frames"], frames)
        self.owner.acceptDetectionFrame(first.sample.image, time.monotonic(), "http://other/camera")
        self.owner.acceptDetectionFrame(first.sample.image, time.monotonic(), "http://printer/camera?mpf_reload=0")
        self.assertEqual(self.detector.samples, [])
        self.owner.acceptDetectionFrame(first.sample.image, time.monotonic(), "http://printer/camera?mpf_reload=1")
        self.assertEqual(len(self.detector.samples), 1)

    def test_region_editor_is_transactional_and_region_changes_get_separate_baselines(self):
        before = self.owner._detection_baseline_camera
        self.owner._detection_policy.restore_baseline({"mean": .2, "frames": 100})
        self.owner.setDetectionEditingRegions(True)
        self.assertIsNone(self.owner._detection_context())
        self.assertFalse(self.owner.saveDetectionRegions([[[0,0], [1,1], [0,1], [1,0]]]))
        self.assertTrue(self.owner.detectionEditingRegions)
        self.owner.setDetectionEditingRegions(False)
        self.assertEqual(self.config.detection_regions, {})
        self.assertEqual(self.owner._detection_policy.baseline["frames"], 100)
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.saveDetectionRegions([[[0,0], [.5,0], [.5,1], [0,1]]]))
        self.assertNotEqual(self.owner._detection_baseline_camera, before)
        self.assertEqual(self.owner._detection_policy.baseline["frames"], 0)

    def test_missing_history_allows_notifications_but_no_pause_or_mute(self):
        self.owner._handle_detection_action(self.owner._detection_context(), "warning", time.time())
        self.owner._notify_detection.assert_called_once()
        self.commands.send.assert_not_called()
        self.assertFalse(self.owner.detectionMuteAvailable)
        self.assertIn("identity", self.owner._detection_values()["detectionStatus"])
        self.assertNotIn("detectionActions", self.store.records.get("printer-one", {}))

    def test_failure_upgrades_an_unacknowledged_warning_immediately_and_repeat_count_is_bounded(self):
        self.attest()
        context = self.owner._detection_context()
        now = time.time()
        self.owner._handle_detection_action(context, "warning", now)
        self.owner._handle_detection_action(context, "failure", now+1)
        self.assertEqual(self.owner._notify_detection.call_count, 2)
        self.assertEqual(self.owner.detectionAlertLevel, "failure")
        for i in range(20):
            self.owner._handle_detection_action(context, "warning", now+300*(i+1))
        self.assertEqual(self.owner._notify_detection.call_count, 3)
        self.assertEqual(self.owner.detectionAlertLevel, "failure")

    def test_acknowledged_warning_does_not_delay_failure_notification_or_pause(self):
        self.attest()
        context = self.owner._detection_context()
        now = time.time()
        self.owner._handle_detection_action(context, "warning", now - 1)
        self.owner.acknowledgeDetectionAlert()
        self.owner._handle_detection_action(context, "warning", now + 1)
        self.owner._notify_detection.assert_called_once()
        self.commands.send.assert_not_called()
        self.owner._handle_detection_action(context, "failure", now + 2)
        self.assertEqual(self.owner._notify_detection.call_count, 2)
        self.commands.send.assert_called_once()
        self.assertEqual(self.owner.detectionAlertLevel, "failure")
        self.owner.acknowledgeDetectionAlert()
        self.owner._handle_detection_action(context, "failure", now + 3)
        self.assertEqual(self.owner._notify_detection.call_count, 2)

    def test_stale_history_reply_cannot_attest_next_local_print(self):
        old = self.requests[-1]
        self.job.job_key = ("next.gcode", 200, 2)
        self.attest(callback=old)
        self.assertIsNone(self.owner._detection_run_identity)

    def test_periodic_history_reattests_same_local_job_and_retires_previous_print(self):
        self.attest()
        self.owner.setDetectionMuted(True)
        old = self.observation(4)
        self.owner._detection_history_next_at = 0
        self.owner._detection_values()
        self.assertEqual(len(self.requests), 2)
        self.attest("server-run-two")
        self.assertFalse(self.owner.detectionMuted)
        self.assertNotEqual(old.sample.context, self.owner._detection_context())
        self.owner._on_detection_observation(old)
        self.assertEqual(self.owner.detectionBoxes, [])

    def test_new_pause_requires_fresh_history_even_while_local_identity_is_unchanged(self):
        self.attest()
        self.owner._detection_attested_at -= 3
        self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time())
        self.commands.send.assert_not_called()
        self.assertEqual(len(self.requests), 2)
        self.requests[-1](None, "history unavailable")
        self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time())
        self.commands.send.assert_not_called()
        self.assertEqual(len(self.requests), 2, "failed history must respect retry backoff")

    def test_source_selection_retires_inference_and_baseline_even_with_unchanged_webcam_uid(self):
        self.data.snapshot.webcams = [{"uid": "same-camera", "name": "Camera", "stream_url": "/stream?camera=1"}]
        self.camera.values["activeWebcamIndex"] = 0
        self.camera.url = "http://printer/stream?camera=1"
        self.owner._detection_values()
        old = self.observation(4)
        baseline = self.owner._detection_baseline_camera
        self.data.snapshot.webcams[0]["stream_url"] = "/stream?camera=2"
        self.camera.url = "http://printer/stream?camera=2"
        self.owner._detection_values()
        self.assertNotEqual(self.owner._detection_baseline_camera, baseline)
        self.owner._on_detection_observation(old)
        self.assertEqual(self.owner.detectionBoxes, [])

    def test_unknown_feed_selectors_retire_regions_and_reject_old_frames(self):
        for selector in ("src", "device", "camera"):
            with self.subTest(selector=selector):
                self.data.snapshot.webcams = [{"uid": "same-camera", "stream_url": "/stream?" + selector + "=bed"}]
                self.camera.values["activeWebcamIndex"] = 0
                self.camera.url = "http://printer/stream?" + selector + "=bed"
                self.owner._detection_values()
                before = self.owner._detection_camera_id()
                previous_context = self.owner._detection_context()
                old_url = self.camera.url
                self.data.snapshot.webcams[0]["stream_url"] = "/stream?" + selector + "=toolhead"
                self.camera.url = "http://printer/stream?" + selector + "=toolhead"
                self.owner._detection_values()
                self.assertNotEqual(before, self.owner._detection_camera_id())
                self.assertNotEqual(previous_context, self.owner._detection_context())
                self.detector.samples.clear()
                self.owner.acceptDetectionFrame(QImage(20, 20, QImage.Format.Format_RGB32), source_url=old_url)
                self.assertEqual(self.detector.samples, [])
                self.owner.acceptDetectionFrame(QImage(20, 20, QImage.Format.Format_RGB32), source_url=self.camera.url)
                self.assertEqual(len(self.detector.samples), 1)

    def test_long_upstream_identifiers_cannot_corrupt_persisted_regions(self):
        self.data.snapshot.webcams = [{"uid": "camera-" * 100, "stream_url": "/stream?src=bed"}]
        self.camera.values["activeWebcamIndex"] = 0
        self.camera.url = "http://printer/stream?src=bed"
        self.owner.setDetectionEditingRegions(True)
        valid = [[[.2, .2], [.8, .2], [.8, .8], [.2, .8]]]
        self.assertTrue(self.owner.saveDetectionRegions(valid))
        from dataclasses import asdict
        restored = PrinterConfig.from_dict(asdict(self.config))
        camera = self.owner.detectionRegionCamera
        self.assertLess(len(camera), 256)
        self.assertEqual(len(restored.detection_regions[camera]), 1)
        self.assertNotIn("invalid", restored.detection_regions)

    def test_regions_persist_independently_per_printer_and_camera_after_restart(self):
        import os
        import tempfile
        from mpf.settings.PluginPersistence import PluginPersistence
        with tempfile.TemporaryDirectory() as directory:
            paths = (os.path.join(directory, "settings.json"),
                     os.path.join(directory, "state.json"), os.path.join(directory, "machines"))
            persistence = PluginPersistence(*paths)
            self.data.snapshot.webcams = [
                {"uid": "bed", "stream_url": "/stream?camera=bed"},
                {"uid": "top", "stream_url": "/stream?camera=top"}]
            expected = {}
            for printer in ("printer-one", "printer-two"):
                self.config = PrinterConfig(url="http://printer", camera_url="http://printer/camera")
                self.owner._identity = lambda printer=printer: (printer, printer)
                def save(config, printer=printer):
                    if not persistence.set_machine_config(printer, config):
                        return False
                    return self.apply(PrinterConfig.from_dict(persistence.get_machine(printer)))
                self.owner._apply_config = save
                for index in (0, 1):
                    self.camera.values["activeWebcamIndex"] = index
                    inset = round(.1 + .1 * index + (.2 if printer == "printer-two" else 0), 2)
                    region = [[[inset, inset], [.8, inset], [.8, .8], [inset, .8]]]
                    self.owner.setDetectionEditingRegions(True)
                    self.assertTrue(self.owner.saveDetectionRegions(region))
                    expected[printer, index] = region
            # A new persistence facade and owner read the saved disk records.
            persistence = PluginPersistence(*paths)
            for printer in ("printer-one", "printer-two"):
                self.config = PrinterConfig.from_dict(persistence.get_machine(printer))
                restored = self.make_owner()
                restored._identity = lambda printer=printer: (printer, printer)
                for index in (1, 0):
                    self.camera.values["activeWebcamIndex"] = index
                    self.assertEqual(restored.detectionRegions, expected[printer, index])

    def test_tuning_and_overlay_preferences_round_trip_and_reset(self):
        self.assertTrue(self.owner.detectionNotifyEnabled)
        self.owner.setDetectionSensitivity(.9)
        self.owner.setDetectionThresholds(30, 70)
        self.owner.setDetectionSafeSeconds(120)
        self.owner.setDetectionShowBoxes(False)
        self.assertEqual((self.owner.detectionSensitivity, self.owner.detectionWarningThreshold,
                          self.owner.detectionFailureThreshold, self.owner.detectionSafeSeconds), (.9, 30, 70, 120))
        self.assertFalse(self.owner.detectionShowBoxes)
        self.owner.resetDetectionTuning()
        self.assertEqual((self.owner.detectionSensitivity, self.owner.detectionWarningThreshold,
                          self.owner.detectionFailureThreshold), (1, 38, 78))
        self.assertEqual(self.owner.detectionSafeSeconds, 120)
        self.assertTrue(self.owner.detectionRegionsValid)

    def test_invalid_control_values_and_failed_saves_cannot_change_settings(self):
        for callback, arguments in ((self.owner.setDetectionEnabled, (1,)),
                                    (self.owner.setDetectionThresholds, (80, 20)),
                                    (self.owner.setDetectionSensitivity, (float("nan"),)),
                                    (self.owner.setDetectionPauseEnabled, ("yes",))):
            with self.assertRaises(ValueError): callback(*arguments)
        before = self.config
        self.owner._apply_config = lambda value: False
        self.owner.setDetectionSensitivity(1.1)
        self.assertEqual(self.config, before)
        self.commands.report_status.assert_called()
        self.detector.ready = False
        self.owner.setDetectionSensitivity(1.1)
        self.assertEqual(self.config, before)
        self.detector.ready = True
        self.camera.url = ""
        self.owner.setDetectionEnabled(True)
        self.assertEqual(self.config, before)
        self.owner.rearmDetectionPause()
        self.commands.send.assert_not_called()

    def test_mute_save_failure_rolls_back_and_unmute_retires_existing_analysis(self):
        self.owner.setDetectionMuted(True)
        self.assertFalse(self.owner.detectionMuted, "mute needs a durable attested run")
        self.attest()
        self.owner._on_detection_observation(self.observation())
        old = self.observation()
        self.owner.setDetectionMuted(True)
        self.assertTrue(self.owner.detectionMuted)
        self.store.fail = True
        self.owner.setDetectionMuted(False)
        self.assertTrue(self.owner.detectionMuted)
        self.store.fail = False
        self.owner.setDetectionMuted(False)
        self.assertFalse(self.owner.detectionMuted)
        self.owner._on_detection_observation(old)
        self.assertEqual(self.owner.detectionBoxes, [])
        self.assertFalse(self.owner.detectionAlertPending)

    def test_region_save_errors_leave_the_edit_transaction_open(self):
        self.owner.setDetectionEditingRegions(True)
        invalid = [[[0, 0], [1, 1], [0, 1], [1, 0]]]
        valid = [[[.2, .2], [.8, .2], [.8, .8], [.2, .8]]]
        self.assertNotEqual(self.owner.validateDetectionRegions(invalid), "")
        self.assertFalse(self.owner.saveDetectionRegions(invalid))
        self.owner._apply_config = lambda value: False
        self.assertFalse(self.owner.saveDetectionRegions(valid))
        self.assertTrue(self.owner.detectionEditingRegions)
        self.assertEqual(self.owner.detectionRegions, [])
        self.owner._apply_config = self.apply
        from dataclasses import replace
        self.config = replace(self.config, detection_regions={"other-" + str(i): () for i in range(32)})
        self.assertFalse(self.owner.saveDetectionRegions(valid))
        self.assertTrue(self.owner.detectionEditingRegions)
        self.config = replace(self.config, detection_regions={})
        self.assertTrue(self.owner.saveDetectionRegions(valid))
        self.assertFalse(self.owner.detectionEditingRegions)
        self.assertEqual(len(self.owner.detectionRegions), 1)

    def test_invalid_observations_and_bounded_baselines_do_not_fabricate_alerts(self):
        context = self.owner._detection_context()
        self.owner._on_detection_result(context, float("nan"))
        self.assertEqual(self.owner.detectionBoxes, [])
        self.commands.send.assert_not_called()
        self.owner._detection_baselines = {"old-" + str(i): {"mean": 0, "frames": 1} for i in range(34)}
        self.owner._on_detection_observation(self.observation())
        self.assertEqual(len(self.owner._detection_baselines), 32)
        self.assertIn(self.owner._detection_baseline_camera, self.owner._detection_baselines)
        self.detector.storage_root = Mock(side_effect=OSError("unavailable directory"))
        self.assertIsNone(self.owner._detection_evidence_root())
        self.detector.enqueue_io = None
        self.assertFalse(self.owner._queue_detection_io("evidence", lambda: None))

    def test_retired_camera_disables_controls_editing_analysis_and_owned_alert(self):
        self.attest()
        self.owner.setDetectionEditingRegions(True)
        message = Mock()
        self.owner._detection_message = message
        self.recovery.stream_enabled = False
        self.owner._detection_values()
        message.hide.assert_called_once()
        self.assertFalse(self.owner.detectionCameraReady)
        self.assertFalse(self.owner.detectionEditingRegions)
        self.assertFalse(self.owner.detectionMuteAvailable)
        self.assertIsNone(self.owner._detection_context())
        self.owner.setDetectionSensitivity(1.1)
        self.assertEqual(self.config.detection_sensitivity, 1)
        self.recovery.stream_enabled = True
        self.owner._detection_values()
        self.assertEqual(self.owner.detectionBoxes, [])
        self.assertEqual(self.owner._detection_values()["detectionState"], "waiting")

    def test_model_removal_closes_the_region_edit_transaction(self):
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.detectionEditingRegions)
        self.detector.ready = False
        self.owner._detection_values()
        self.assertFalse(self.owner.detectionEditingRegions)

    def test_disabling_detection_closes_the_region_edit_transaction(self):
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.detectionEditingRegions)
        self.owner.setDetectionEnabled(False)
        self.assertFalse(self.owner.detectionEditingRegions)

    def test_failed_acknowledgement_rolls_back_and_retired_toast_cannot_acknowledge(self):
        self.attest()
        self.owner._handle_detection_action(self.owner._detection_context(), "warning", time.time())
        self.store.fail = True
        self.owner.acknowledgeDetectionAlert()
        self.assertTrue(self.owner.detectionAlertPending)
        self.assertIn("acknowledgement", self.commands.report_status.call_args.args[0])
        self.store.fail = False
        message = Mock()
        self.owner._detection_message = message
        self.owner._detection_message_token = self.owner._detection_action_context
        self.data.active = False
        self.owner._invalidate_detection()
        self.owner._on_detection_alert_action(message, "detectionAcknowledge")
        self.assertTrue(self.owner.detectionAlertPending)

    def test_tiny_region_sample_is_unavailable_without_fabricating_normal_or_alerting(self):
        result = self.observation(4)
        from dataclasses import replace
        self.owner._on_detection_observation(replace(result, unavailable_reason="Enlarge monitored regions"))
        self.assertEqual(self.owner.detectionBoxes, [])
        self.assertIn("Enlarge", self.owner._detection_values()["detectionStatus"])
        self.commands.send.assert_not_called()
        self.owner._notify_detection.assert_not_called()

    def test_negative_and_failed_latest_attestation_revoke_pause_freshness_immediately(self):
        self.attest()
        old = self.observation(4)
        self.owner._ensure_detection_run(force=True)
        self.assertFalse(self.owner._attestation_fresh(2))
        self.requests[-1]({"result": {"jobs": [dict(job_id="server-run-one", start_time=1000.,
            filename="part.gcode", status="completed", end_time=2000.)]}}, "")
        self.assertIsNone(self.owner._detection_run_identity)
        self.owner._on_detection_observation(old)
        self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time())
        self.commands.send.assert_not_called()

    def test_saved_regions_survive_bridge_restart_and_snapshot_mode_switch(self):
        self.data.snapshot.webcams = [{"uid": "same-camera", "name": "Camera", "stream_url": "/stream?camera=1", "snapshot_url": "/snapshot?camera=1"}]
        self.camera.values["activeWebcamIndex"] = 0
        self.camera.url = "http://127.0.0.1:40001/stream?camera=1"
        self.owner._detection_values()
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.saveDetectionRegions([[[.2,.2],[.6,.2],[.6,.6],[.2,.6]]]))
        regions = self.owner.detectionRegions
        camera = self.owner.detectionRegionCamera
        self.camera.url = "http://127.0.0.1:40002/stream?camera=1"
        self.owner._detection_values()
        self.assertEqual(self.owner.detectionRegionCamera, camera)
        self.assertEqual(self.owner.detectionRegions, regions)
        self.camera.url = "http://127.0.0.1:40002/snapshot?camera=1"
        self.camera.values["cameraSnapshotMode"] = True
        self.owner._detection_values()
        self.assertEqual(self.owner.detectionRegionCamera, camera)
        self.assertEqual(self.owner.detectionRegions, regions)

    def test_cropped_regions_do_not_restore_old_full_frame_mask_baseline(self):
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.saveDetectionRegions([[[.2,.2],[.6,.2],[.6,.6],[.2,.6]]]))
        camera = self.owner.detectionRegionCamera
        fingerprint = self.owner._regions_fingerprint()
        old_key = camera + ":mask-v1:" + fingerprint
        new_key = camera + ":crop-v1:" + fingerprint
        self.store.records["printer-one"] = {"detectionBaselines": {
            old_key: {"mean": 4, "frames": 1000}}}
        restored = self.make_owner()
        restored._detection_values()
        self.assertEqual(restored._detection_baseline_camera, new_key)
        self.assertEqual(restored._detection_policy.baseline, {"mean": 0, "frames": 0})
        self.store.records["printer-one"]["detectionBaselines"][new_key] = {"mean": .2, "frames": 50}
        restarted = self.make_owner()
        restarted._detection_values()
        self.assertEqual(restarted._detection_policy.baseline, {"mean": .2, "frames": 50})

    def test_baseline_reset_keeps_regions_other_cameras_and_survives_restart(self):
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.saveDetectionRegions([[[.2,.2],[.6,.2],[.6,.6],[.2,.6]]]))
        self.owner._detection_values()
        self.attest()
        self.owner._detection_policy.restore_baseline({"mean": 1.5, "frames": 194})
        self.owner._detection_baselines["other-camera"] = {"mean": .3, "frames": 30}
        self.store.records["other-printer"] = {"detectionBaselines": {"camera": {"mean": .4, "frames": 40}}}
        original_config = self.config
        other_printer = deepcopy(self.store.records["other-printer"])
        pending = {}
        def enqueue(key, callback):
            pending[key] = callback
            return True
        self.detector.enqueue_io = enqueue
        self.owner._on_detection_observation(self.observation(1.5))
        old_result = self.observation(8)
        self.assertGreater(self.owner.detectionBaseline, 1)
        self.owner.resetDetectionBaseline()
        self.assertEqual(self.owner.detectionBaseline, 0)
        self.assertEqual(self.config, original_config, "reset must not edit zones or settings")
        self.owner._on_detection_observation(old_result)
        self.assertEqual(self.owner._detection_policy.baseline, {"mean": 0, "frames": 0})
        pending["baseline:printer-one"]()
        self.assertEqual(self.store.records["other-printer"], other_printer)
        saved = self.store.records["printer-one"]["detectionBaselines"]
        self.assertEqual(saved["other-camera"], {"mean": .3, "frames": 30})
        restarted = self.make_owner()
        restarted._detection_values()
        self.assertEqual(restarted.detectionBaseline, 0)
        self.assertEqual(restarted.detectionRegions, self.owner.detectionRegions)

    def test_baseline_reset_refuses_when_its_persistence_queue_is_unavailable(self):
        self.owner._detection_policy.restore_baseline({"mean": 1.5, "frames": 194})
        self.detector.enqueue_io = lambda key, callback: False
        self.owner.resetDetectionBaseline()
        self.assertEqual(self.owner.detectionBaseline, 1.5)
        self.assertIn("could not be reset", self.commands.report_status.call_args.args[0])

    def test_first_camera_use_and_training_reset_publish_learning_without_a_green_score(self):
        self.owner.setDetectionThresholds(1, 9)
        self.attest()
        for index in range(6):
            self.owner._on_detection_observation(self.observation(0))
            values = self.owner._detection_values()
            self.assertEqual(values["detectionState"], "learning")
            self.assertEqual(values["detectionScore"], -1)
            self.assertIn("%d/6" % (index + 1), values["detectionStatus"])
        self.commands.send.assert_not_called()
        self.owner._notify_detection.assert_not_called()
        for _ in range(3):
            self.owner._on_detection_observation(self.observation(1))
        self.assertEqual(self.owner._detection_values()["detectionState"], "failure")
        self.assertLess(self.owner.detectionBaseline, .001)
        self.owner.resetDetectionBaseline()
        self.owner._detection_values()
        self.owner._on_detection_observation(self.observation(0))
        self.assertEqual(self.owner._detection_values()["detectionState"], "learning")
        self.assertEqual(self.owner._detection_policy.baseline["frames"], 1)

    def test_printer_training_reset_clears_all_cameras_but_keeps_zones_and_other_printers(self):
        config = self.config
        self.owner._detection_baselines["other-camera"] = {"mean": 2, "frames": 50}
        self.store.records["other-printer"] = {"detectionBaselines": {"camera": {"mean": 3, "frames": 100}}}
        other = deepcopy(self.store.records["other-printer"])
        self.owner.resetPrinterDetectionTraining()
        saved = self.store.records["printer-one"]["detectionBaselines"]
        self.assertNotIn("other-camera", saved)
        self.assertTrue(all(value == {"mean": 0, "frames": 0} for value in saved.values()))
        self.assertEqual(self.config, config)
        self.assertEqual(self.store.records["other-printer"], other)

    def test_delayed_history_response_cannot_become_a_fresh_pause_authorisation(self):
        from unittest.mock import patch
        with patch("mpf.monitor.MonitorDetection.time.monotonic", return_value=100):
            self.owner._detection_history_pending = False
            self.owner._detection_history_next_at = self.owner._detection_history_retry_at = 0
            self.owner._ensure_detection_run(force=True)
        with patch("mpf.monitor.MonitorDetection.time.monotonic", return_value=110):
            self.attest()
            self.assertFalse(self.owner._attestation_fresh(2))
            self.owner._handle_detection_action(self.owner._detection_context(), "failure", time.time())
            self.commands.send.assert_not_called()

    def test_qml_javascript_arrays_are_accepted_at_the_real_python_slot_boundary(self):
        from PyQt6.QtQml import QJSEngine
        engine = QJSEngine()
        value = engine.evaluate("[[[.2,.2],[.6,.2],[.6,.6],[.2,.6]]]")
        self.assertEqual(self.owner.validateDetectionRegions(value), "")
        self.owner.setDetectionEditingRegions(True)
        self.assertTrue(self.owner.saveDetectionRegions(value))
        self.assertEqual(len(self.owner.detectionRegions), 1)
