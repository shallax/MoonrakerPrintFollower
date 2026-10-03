"""Global detection service lifecycle, without network or native imports."""

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QImage

from mpf.detection import AssetInstaller
from mpf.detection.DetectionAssets import ASSET_VERSION, RuntimeWheel, installed_paths
from mpf.detection.LocalDetectionService import LocalDetectionService, _lane


class Persistence:
    def __init__(self, record=None):
        self.record = dict(record or {})
        self.fail = False

    def settings_document(self):
        return {"global": {"localDetection": dict(self.record)}}

    def set_global(self, patch):
        if self.fail:
            return False
        self.record = dict(patch["localDetection"])
        return True


class Model:
    def __init__(self):
        self.calls = []
        self.block = None

    def score(self, image):
        self.calls.append((threading.get_ident(), image.pixelColor(0, 0).red()))
        if self.block:
            self.block.wait(2)
        return .42


class LocalDetectionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.persistence = Persistence()
        self.model = Model()
        self.wheel = RuntimeWheel("pinned.whl", "a" * 64, 128)
        self.patches = [
            patch("mpf.detection.LocalDetectionService.host_wheel", return_value=self.wheel),
            patch("mpf.detection.LocalDetectionService.LocalFailureModel.load",
                  return_value=self.model),
            patch("mpf.detection.LocalDetectionService.LocalDetectionService._verified",
                  return_value=True),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.services = []

    def service(self):
        result = LocalDetectionService(self.directory.name, self.persistence)
        self.services.append(result)
        self.addCleanup(result.close)
        return result

    def until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail("Timed out waiting for detection worker")

    def test_offer_decline_and_failed_persistence_are_visible(self):
        service = self.service()
        self.assertTrue(service.should_offer)
        self.persistence.fail = True
        service.decline_offer()
        self.assertTrue(service.should_offer)
        self.assertIn("Could not save", service.error)
        self.persistence.fail = False
        service.decline_offer()
        self.assertFalse(service.should_offer)
        self.assertTrue(self.persistence.record["offer_seen"])

    def test_reset_offer_replays_wizard_without_removing_ready_assets(self):
        self.persistence.record = {"offer_seen": True, "consent": True,
                                   "ready_version": ASSET_VERSION}
        service = self.service()
        self.until(lambda: service.ready)
        self.assertFalse(service.should_offer)
        self.persistence.fail = True
        with self.assertRaises(OSError):
            service.reset_offer()
        self.assertFalse(service.should_offer)
        self.persistence.fail = False
        service.reset_offer()
        self.assertTrue(service.ready)
        self.assertTrue(service.should_offer)
        self.assertEqual(self.persistence.record["ready_version"], ASSET_VERSION)
        service.decline_offer()
        self.assertFalse(service.should_offer)

    def test_qml_exposes_state_properties_and_invokable_action_slots(self):
        service = self.service()
        meta = service.metaObject()
        for name in ("ready", "host_error", "busy", "phase", "received",
                     "total", "error", "should_offer", "enabled"):
            prop = meta.property(meta.indexOfProperty(name))
            self.assertEqual(prop.name(), name)
            self.assertTrue(prop.hasNotifySignal())
        runtime_size = meta.property(meta.indexOfProperty("runtime_size"))
        self.assertEqual(runtime_size.name(), "runtime_size")
        self.assertTrue(runtime_size.isConstant())
        self.assertEqual(runtime_size.read(service), self.wheel.size)
        for signature in (b"setup()", b"cancel()", b"decline_offer()", b"remove_assets()",
                          b"set_enabled(bool)"):
            self.assertGreaterEqual(meta.indexOfMethod(signature), 0)

    def test_setup_benchmarks_off_gui_thread_persists_success_and_reuses_assets(self):
        service = self.service()
        paths = installed_paths(self.directory.name)
        installs = []

        def install(_root, _wheel, _cancel, progress):
            installs.append(True)
            progress("model", 50, 100)
            return paths

        with patch.object(AssetInstaller, "install", side_effect=install):
            service.setup()
            self.until(lambda: service.ready)
        self.assertEqual(len(installs), 1)
        self.assertFalse(service.busy)
        self.assertEqual(service.phase, "ready")
        self.assertEqual((service.received, service.total), (50, 100))
        self.assertEqual(self.persistence.record["ready_version"], ASSET_VERSION)
        self.assertTrue(service.enabled)
        self.assertTrue(self.persistence.record["consent"])
        self.assertFalse(service.should_offer)
        self.assertEqual(len(self.model.calls), 2)
        self.assertTrue(all(call[0] != threading.get_ident() for call in self.model.calls))
        service.close()

        other = self.service()
        self.until(lambda: other.ready)
        self.assertEqual(other.phase, "ready")
        self.assertTrue(other.enabled)
        self.assertEqual(len(installs), 1)

    def test_global_switch_persists_and_retires_inflight_results(self):
        service = self.service()
        with patch.object(AssetInstaller, "install",
                          return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.ready)
        image = QImage(8, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        results = []
        service.resultReady.connect(lambda context, score: results.append((context, score)))
        self.model.block = threading.Event()
        service.sample(image, ("old",))
        self.until(lambda: len(self.model.calls) >= 3)
        self.assertTrue(service.set_enabled(False))
        self.assertFalse(service.enabled)
        self.assertIs(self.persistence.record["enabled"], False)
        self.model.block.set()
        self.app.processEvents()
        service.sample(image, ("ignored",))
        self.app.processEvents()
        self.assertEqual(results, [])
        other = self.service()
        self.until(lambda: other.ready)
        self.assertFalse(other.enabled)
        self.assertTrue(other.set_enabled(True))
        self.assertTrue(other.enabled)
        self.assertIs(self.persistence.record["enabled"], True)

    def test_global_switch_refuses_unsaved_changes(self):
        service = self.service()
        self.assertFalse(service.set_enabled(False))
        self.assertIn("Set up", service.error)
        with patch.object(AssetInstaller, "install",
                          return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.ready)
        self.persistence.fail = True
        self.assertFalse(service.set_enabled(False))
        self.assertTrue(service.enabled)
        self.assertIn("Could not save", service.error)

    def test_reset_during_setup_only_invalidates_frames_not_global_setup(self):
        service = self.service()
        with patch.object(AssetInstaller, "install",
                          return_value=installed_paths(self.directory.name)):
            service.setup()
            service.reset()
            self.until(lambda: service.ready)
        self.assertFalse(service.busy)

    def test_cancel_waiting_download_cleans_up_and_never_sets_success(self):
        service = self.service()
        started = threading.Event()
        paths = installed_paths(self.directory.name)

        def install(root, wheel, cancel, progress):
            os.makedirs(os.path.dirname(paths[1]), exist_ok=True)
            with open(paths[1], "wb") as handle:
                handle.write(b"incomplete")
            started.set()
            while not cancel.wait(.01):
                progress("model", 1, 100)
            raise AssetInstaller.DownloadCancelled("cancelled")

        with patch.object(service, "_verified", return_value=False), \
                patch.object(AssetInstaller, "install", side_effect=install):
            service.setup()
            self.assertTrue(started.wait(2))
            service.cancel()
            self.until(lambda: service.phase == "cancelled")
        self.assertFalse(service.ready)
        self.assertFalse(service.busy)
        self.assertFalse(os.path.exists(paths[1]))
        self.assertNotEqual(self.persistence.record.get("ready_version"), ASSET_VERSION)

    def test_setup_write_failure_prevents_download(self):
        service = self.service()
        self.persistence.fail = True
        with patch.object(AssetInstaller, "install", side_effect=AssertionError("downloaded")):
            service.setup()
            self.until(lambda: service.phase == "error")
        self.assertIn("Could not save", service.error)
        self.assertFalse(service.ready)

    def test_success_write_failure_does_not_claim_ready(self):
        service = self.service()

        def install(*_args):
            self.persistence.fail = True
            return installed_paths(self.directory.name)

        with patch.object(AssetInstaller, "install", side_effect=install):
            service.setup()
            self.until(lambda: service.phase == "error")
        self.assertFalse(service.ready)
        self.assertNotEqual(self.persistence.record.get("ready_version"), ASSET_VERSION)
        self.assertIn("Could not save", service.error)

    def test_existing_corrupt_assets_fail_startup_without_native_import(self):
        self.persistence.record = {"ready_version": ASSET_VERSION, "offer_seen": True}
        with patch("mpf.detection.LocalDetectionService.LocalDetectionService._verified",
                   return_value=False):
            service = self.service()
            self.until(lambda: service.phase == "error")
        self.assertIn("integrity", service.error)
        self.assertFalse(service.ready)
        self.assertEqual(self.model.calls, [])

    def test_slow_real_inference_benchmark_fails_without_success_marker(self):
        service = self.service()
        with patch("mpf.detection.LocalDetectionService._BENCHMARK_LIMIT", -.01), \
                patch.object(AssetInstaller, "install",
                             return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.phase == "error")
        self.assertIn("limit: 5s", service.error)
        self.assertNotEqual(self.persistence.record.get("ready_version"), ASSET_VERSION)

    def test_missing_native_numpy_reports_actionable_setup_failure(self):
        service = self.service()
        with patch("mpf.detection.LocalDetectionService.LocalFailureModel.load",
                   side_effect=ImportError("NumPy unavailable in Cura")), \
                patch.object(AssetInstaller, "install",
                             return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.phase == "error")
        self.assertIn("NumPy unavailable", service.error)
        self.assertFalse(service.ready)
        self.assertNotEqual(self.persistence.record.get("ready_version"), ASSET_VERSION)

    def test_unsupported_host_never_starts_worker_or_offers(self):
        with patch("mpf.detection.LocalDetectionService.host_wheel",
                   side_effect=ValueError("Unsupported Python")):
            service = self.service()
        self.assertEqual(service.host_error, "Unsupported Python")
        self.assertEqual(service.runtime_size, 0)
        self.assertFalse(service.should_offer)
        self.assertEqual(service.phase, "unsupported")
        changed = []
        service.stateChanged.connect(lambda: changed.append(service.error))
        service.setup()
        self.assertFalse(service.busy)
        self.assertEqual(service.error, "Unsupported Python")
        self.assertEqual(changed, ["Unsupported Python"])
        service.close()

    def test_latest_frame_wins_and_reset_discards_inflight_results(self):
        service = self.service()
        with patch.object(AssetInstaller, "install",
                          return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.ready)
        image = QImage(8, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        received = []
        service.resultReady.connect(lambda context, score: received.append((context, score)))
        with patch("mpf.detection.LocalDetectionService._CADENCE", .1):
            service.sample(image, ("old",))
            self.until(lambda: len(received) == 1)
            image.fill(0xFF0000)
            service.sample(image, ("replace",))
            image.fill(0x00FF00)
            service.sample(image, ("latest",))
            self.until(lambda: len(received) == 2)
            self.assertEqual(received[-1], (("latest",), .42))
            self.assertEqual(self.model.calls[-1][1], 0)
            self.model.block = threading.Event()
            service.sample(image, ("retired",))
            self.until(lambda: len(self.model.calls) >= 5)
            service.reset()
            self.model.block.set()
            time.sleep(.02)
            self.app.processEvents()
            self.assertEqual(len(received), 2)
        service.close()
        self.assertFalse(service._worker.is_alive())

    def test_sample_inference_failure_demotes_ready_and_notifies_qml(self):
        service = self.service()
        with patch.object(AssetInstaller, "install",
                          return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.ready)
        changes = []
        results = []
        service.stateChanged.connect(lambda: changes.append((service.ready, service.error)))
        service.resultReady.connect(lambda context, score: results.append((context, score)))
        image = QImage(8, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        with patch.object(self.model, "score", side_effect=RuntimeError("ONNX execution failed")), \
                patch("mpf.detection.LocalDetectionService._CADENCE", .01):
            service.sample(image, ("print-1",))
            self.until(lambda: service.phase == "error")
        self.assertFalse(service.ready)
        self.assertIn("ONNX execution failed", service.error)
        self.assertEqual(results, [])
        self.assertIn((False, "ONNX execution failed"), changes)
        service.sample(image, ("print-2",))
        self.app.processEvents()
        self.assertEqual(results, [])

    def test_remove_assets_runs_on_worker_and_requires_fresh_download(self):
        self.persistence.record = {"ready_version": ASSET_VERSION, "consent": True,
                                   "offer_seen": True, "foreign": "kept"}
        service = self.service()
        self.until(lambda: service.ready)
        runtime, model = installed_paths(self.directory.name)
        archive = os.path.join(os.path.dirname(model), self.wheel.filename)
        os.makedirs(runtime)
        for path in (model, archive, os.path.join(runtime, "runtime.bin")):
            with open(path, "wb") as handle:
                handle.write(b"asset")
        sibling = os.path.join(os.path.dirname(model), "leave-me.txt")
        with open(sibling, "wb") as handle:
            handle.write(b"unrelated")
        changes = []
        service.stateChanged.connect(
            lambda: changes.append((service.phase, service.busy, service.ready, service.error)))
        worker = []
        original_unlink = os.unlink

        def observed_unlink(path, *args, **kwargs):
            worker.append(threading.get_ident())
            return original_unlink(path, *args, **kwargs)

        with patch("mpf.detection.LocalDetectionService.os.unlink", side_effect=observed_unlink):
            self.assertTrue(service.remove_assets())
            self.assertTrue(service.busy)
            self.assertFalse(service.ready)
            self.until(lambda: not service.busy)
        self.assertTrue(worker)
        self.assertTrue(all(ident != threading.get_ident() for ident in worker))
        self.assertEqual(service.phase, "uninstalled")
        self.assertEqual(service.error, "")
        self.assertEqual(changes[0], ("removing", True, False, ""))
        self.assertEqual(changes[-1], ("uninstalled", False, False, ""))
        for path in (model, archive, runtime):
            self.assertFalse(os.path.lexists(path))
        self.assertTrue(os.path.isfile(sibling))
        self.assertEqual(self.persistence.record,
                         {"ready_version": "", "consent": False,
                          "offer_seen": False, "foreign": "kept"})
        self.assertTrue(service.should_offer)

    def test_remove_rejects_concurrent_setup_without_deleting_assets(self):
        service = self.service()
        runtime, model = installed_paths(self.directory.name)
        os.makedirs(os.path.dirname(model))
        with open(model, "wb") as handle:
            handle.write(b"model")
        started = threading.Event()
        release = threading.Event()

        def install(*_args):
            started.set()
            release.wait(3)
            return runtime, model

        with patch.object(AssetInstaller, "install", side_effect=install):
            service.setup()
            self.assertTrue(started.wait(2))
            self.assertFalse(service.remove_assets())
            self.assertIn("during setup", service.error)
            self.assertEqual(service.phase, "error")
            self.assertTrue(os.path.isfile(model))
            release.set()
            self.until(lambda: not service.busy)
        self.assertTrue(os.path.isfile(model))

    def test_remove_invalidates_inflight_result_and_rejects_second_request(self):
        service = self.service()
        with patch.object(AssetInstaller, "install",
                          return_value=installed_paths(self.directory.name)):
            service.setup()
            self.until(lambda: service.ready)
        _, model_path = installed_paths(self.directory.name)
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        with open(model_path, "wb") as handle:
            handle.write(b"asset")
        image = QImage(8, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        received = []
        service.resultReady.connect(lambda *_: received.append(True))
        self.model.block = threading.Event()
        with patch("mpf.detection.LocalDetectionService._CADENCE", .01):
            service.sample(image, ("old",))
            self.until(lambda: len(self.model.calls) >= 3)
            self.assertTrue(service.remove_assets())
            self.assertEqual(service.phase, "removing")
            self.assertFalse(service.ready)
            self.assertTrue(os.path.isfile(model_path))
            service.setup()
            self.assertEqual(service.phase, "error")
            self.assertIn("while asset removal", service.error)
            self.assertFalse(service.remove_assets())
            self.assertIn("during setup or removal", service.error)
            self.model.block.set()
            self.until(lambda: not service.busy)
        self.assertEqual(received, [])
        self.assertFalse(service.ready)
        self.assertEqual(service.phase, "uninstalled")
        self.assertFalse(os.path.exists(model_path))

    def test_removal_failure_is_visible_and_does_not_reset_consent(self):
        self.persistence.record = {"ready_version": ASSET_VERSION, "consent": True,
                                   "offer_seen": True}
        service = self.service()
        self.until(lambda: service.ready)
        runtime, model = installed_paths(self.directory.name)
        os.makedirs(os.path.dirname(model))
        with open(model, "wb") as handle:
            handle.write(b"asset")
        changes = []
        service.stateChanged.connect(
            lambda: changes.append((service.phase, service.busy, service.error)))
        with patch("mpf.detection.LocalDetectionService.os.unlink",
                   side_effect=PermissionError("access denied")):
            self.assertTrue(service.remove_assets())
            self.until(lambda: not service.busy)
        self.assertEqual(service.phase, "error")
        self.assertIn("access denied", service.error)
        self.assertEqual(changes[0], ("removing", True, ""))
        self.assertEqual(changes[-1], ("error", False, service.error))
        self.assertTrue(os.path.isfile(model))
        self.assertEqual(self.persistence.record["ready_version"], ASSET_VERSION)
        self.assertFalse(service.ready)

    def test_removal_settings_failure_reports_error_after_deleting_assets(self):
        service = self.service()
        self.persistence.fail = True
        self.assertTrue(service.remove_assets())
        self.until(lambda: not service.busy)
        self.assertEqual(service.phase, "error")
        self.assertIn("Could not save", service.error)

    def test_close_aborts_removal_queued_behind_another_worker(self):
        self.persistence.record = {"ready_version": ASSET_VERSION, "consent": True,
                                   "offer_seen": True}
        service = self.service()
        self.until(lambda: service.ready)
        runtime, model = installed_paths(self.directory.name)
        os.makedirs(os.path.dirname(model))
        with open(model, "wb") as handle:
            handle.write(b"asset")
        states = []
        service.stateChanged.connect(
            lambda: states.append((service.phase, service.busy, service.error)))
        self.assertTrue(_lane.acquire(timeout=1))
        try:
            self.assertTrue(service.remove_assets())
            started = time.monotonic()
            service.close()
            self.assertLess(time.monotonic() - started, 3)
        finally:
            _lane.release()
        self.assertTrue(os.path.isfile(model))
        self.assertEqual(self.persistence.record["ready_version"], ASSET_VERSION)
        self.assertTrue(self.persistence.record["consent"])
        self.assertEqual(states[0], ("removing", True, ""))
        self.assertEqual(states[-1][0:2], ("error", False))
        self.assertIn("closed before the worker lane", states[-1][2])
        self.assertFalse(service._worker.is_alive())

    def test_close_reports_completed_removal_after_worker_acquires_lane(self):
        self.persistence.record = {"ready_version": ASSET_VERSION, "consent": True,
                                   "offer_seen": True}
        service = self.service()
        self.until(lambda: service.ready)
        _, model = installed_paths(self.directory.name)
        os.makedirs(os.path.dirname(model))
        with open(model, "wb") as handle:
            handle.write(b"asset")
        started = threading.Event()
        release = threading.Event()
        unlink = os.unlink

        def wait_before_unlink(path, *args, **kwargs):
            started.set()
            self.assertTrue(release.wait(2))
            return unlink(path, *args, **kwargs)

        with patch("mpf.detection.LocalDetectionService.os.unlink",
                   side_effect=wait_before_unlink):
            self.assertTrue(service.remove_assets())
            self.assertTrue(started.wait(2))
            timer = threading.Timer(.05, release.set)
            timer.start()
            try:
                service.close()
            finally:
                release.set()
                timer.join()
        self.assertFalse(service.busy)
        self.assertEqual(service.phase, "uninstalled")
        self.assertEqual(service.error, "")
        self.assertFalse(os.path.exists(model))
        self.assertEqual(self.persistence.record["ready_version"], "")

    def test_removal_refuses_symlinked_asset_parent(self):
        service = self.service()
        destination = os.path.join(self.directory.name, "elsewhere")
        os.makedirs(destination)
        link = os.path.join(self.directory.name, "detection")
        try:
            os.symlink(destination, link)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links unavailable")
        self.assertTrue(service.remove_assets())
        self.until(lambda: not service.busy)
        self.assertEqual(service.phase, "error")
        self.assertIn("symbolic link", service.error)
        self.assertTrue(os.path.islink(link))


if __name__ == "__main__":
    unittest.main()
