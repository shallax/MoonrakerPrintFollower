"""The local model's image shape and confidence contract."""

import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt6.QtGui import QImage

from mpf.detection.LocalFailureModel import LocalFailureModel


class FakeSession:
    def __init__(self, outputs=None):
        self.outputs = outputs or [np.array([[[[0., 0., .2, .2]]]]), np.array([[[.54]]])]
        self.feed = None

    def get_inputs(self):
        return [SimpleNamespace(name="camera", shape=[1, 3, 416, 416])]

    def run(self, _requested, feed):
        self.feed = feed
        return self.outputs


class ShapedSession(FakeSession):
    def __init__(self, shape, outputs=None):
        super().__init__(outputs)
        self.shape = shape

    def get_inputs(self):
        return [SimpleNamespace(name="camera", shape=self.shape)]


# A stand-in runtime whose import is real: load() inserts a directory on
# sys.path and imports from it, so a real file is the only honest fixture.
RUNTIME_MODULE = '''\
from types import SimpleNamespace

__version__ = "{version}"
calls = {{}}


class RunOptions:
    terminate = False


class SessionOptions:
    def __init__(self):
        self.intra_op_num_threads = 0
        self.inter_op_num_threads = 0


class InferenceSession:
    def __init__(self, path, sess_options=None, providers=None):
        calls["session"] = (path, sess_options, providers)

    def get_inputs(self):
        return [SimpleNamespace(name="camera", shape=[1, 3, 416, 416])]
'''


class LocalFailureModelTests(unittest.TestCase):
    def install_runtime(self, directory, version="1.23.2"):
        """Write a stand-in onnxruntime package that load() genuinely imports."""
        package = Path(directory, "runtime", "onnxruntime")
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(RUNTIME_MODULE.format(version=version),
                                             encoding="utf-8")
        installed = package.parent.resolve()
        self.addCleanup(sys.modules.pop, "onnxruntime", None)
        self.addCleanup(self._forget_path, str(installed))
        return installed

    @staticmethod
    def _forget_path(entry):
        while entry in sys.path:
            sys.path.remove(entry)

    def test_regions_exclude_pixels_and_outside_boxes_before_aggregation(self):
        boxes = np.array([[[[.1, .1, .4, .4]], [[.6, .6, .9, .9]]]])
        scores = np.array([[[.9], [.8]]])
        session = FakeSession([boxes, scores])
        image = QImage(100, 100, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        regions = (((0, 0), (1, 0), (0, 1)),)
        result = LocalFailureModel(session).detect(image, regions)
        self.assertAlmostEqual(result.score, .9)
        self.assertEqual(len(result.boxes), 1)
        self.assertEqual(result.boxes[0].as_dict()["x"], .1)
        self.assertEqual(float(session.feed["camera"][0, :, 350, 350].max()), 0)
        self.assertEqual(float(session.feed["camera"][0, :, 200, 50].min()), 1)

    def test_crop_fills_input_and_maps_boxes_back_to_source_pixels(self):
        image = QImage(200, 100, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        regions = (((.25, .2), (.75, .2), (.75, .8), (.25, .8)),)
        boxes = np.array([[[[.1, .25, .9, .75]], [[.1, .25, .9, .75]]]])
        session = FakeSession([boxes, np.array([[[.9], [.8]]])])
        result = LocalFailureModel(session).detect(image, regions)
        self.assertEqual(float(session.feed["camera"].min()), 1)
        self.assertAlmostEqual(result.score, .9)
        self.assertEqual(len(result.boxes), 1, "NMS still removes duplicates in the crop")
        box = result.boxes[0]
        np.testing.assert_allclose([box.x, box.y, box.width, box.height], [.3, .35, .4, .3])

    def test_disjoint_regions_keep_gap_masked_and_filter_boxes_in_source_space(self):
        image = QImage(200, 100, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        regions = (((.2, .2), (.4, .2), (.4, .8), (.2, .8)),
                   ((.6, .2), (.8, .2), (.8, .8), (.6, .8)))
        boxes = np.array([[[[0, 0, .2, 1]], [[.4, .2, .6, .8]], [[.8, 0, 1, 1]]]])
        session = FakeSession([boxes, np.array([[[.9], [.99], [.8]]])])
        result = LocalFailureModel(session).detect(image, regions)
        self.assertEqual(float(session.feed["camera"][0, :, 200, 208].max()), 0)
        self.assertEqual(float(session.feed["camera"][0, :, 200, 25].min()), 1)
        self.assertAlmostEqual(result.score, 1.7)
        self.assertEqual(len(result.boxes), 2)
        np.testing.assert_allclose([result.boxes[0].x, result.boxes[1].x], [.2, .68])

    def test_run_options_can_be_cancelled_without_reusing_a_terminated_run(self):
        import threading
        import time
        entered, release = threading.Event(), threading.Event()
        class RunOptions:
            terminate = False
        class Session(FakeSession):
            def run(self, names, feed, options):
                entered.set()
                while not options.terminate and not release.wait(.005):
                    pass
                if options.terminate:
                    raise RuntimeError("run terminated")
                return self.outputs
        model = LocalFailureModel(Session(), RunOptions)
        image = QImage(12, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        errors = []
        def detect():
            try:
                model.detect(image)
            except RuntimeError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=detect)
        worker.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        model.cancel_current()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(errors, ["run terminated"])
        release.set()
        self.assertAlmostEqual(model.detect(image).score, .54)


    def test_rgb_image_is_resized_and_normalised_before_inference(self):
        session = FakeSession()
        image = QImage(16, 8, QImage.Format.Format_RGB888)
        image.fill(0xFF804020)
        self.assertAlmostEqual(LocalFailureModel(session).score(image), .54, places=5)
        tensor = session.feed["camera"]
        self.assertEqual(tensor.shape, (1, 3, 416, 416))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(float(tensor[0, 0, 200, 200]), 128 / 255, delta=.01)
        self.assertAlmostEqual(float(tensor[0, 1, 200, 200]), 64 / 255, delta=.01)

    def test_bad_output_fails_rather_than_becoming_healthy(self):
        session = FakeSession([np.zeros((1, 1, 1, 4)), np.array([[[float("nan")]]])])
        image = QImage(16, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        with self.assertRaisesRegex(ValueError, "confidence"):
            LocalFailureModel(session).score(image)

    def test_obico_filters_low_scores_and_overlapping_boxes_then_sums_remaining(self):
        boxes = np.array([[[[0., 0., .3, .3]], [[0., 0., .3, .3]],
                           [[.6, .6, .9, .9]], [[.1, .6, .3, .8]]]])
        scores = np.array([[[.9], [.8], [.7], [.07]]])
        image = QImage(16, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        self.assertAlmostEqual(LocalFailureModel(FakeSession([boxes, scores])).score(image), 1.6)

    def test_foreign_input_contracts_are_rejected_before_any_frame_is_scored(self):
        for shapes in ((), ([1, 3, 416],), ([1, 3, 416, 416], [1, 3, 416, 416])):
            session = SimpleNamespace(get_inputs=lambda shapes=shapes: [
                SimpleNamespace(name="camera", shape=shape) for shape in shapes])
            with self.subTest(shapes=shapes), self.assertRaisesRegex(ValueError, "model input"):
                LocalFailureModel(session)
        for shape in ([2, 3, 416, 416], [1, 4, 416, 416], [1, 3, 0, 416],
                      [1, 3, 416, 1025], [1, 3, 416.0, 416], [1, 3, "416", 416]):
            with self.subTest(shape=shape), self.assertRaisesRegex(ValueError, "dimensions"):
                LocalFailureModel(ShapedSession(shape))
        session = ShapedSession([1, 3, 100, 200])
        image = QImage(16, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        LocalFailureModel(session).score(image)
        self.assertEqual(session.feed["camera"].shape, (1, 3, 100, 200))

    def test_load_imports_the_pinned_runtime_and_binds_its_session(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = self.install_runtime(directory)
            model = LocalFailureModel.load("model-weights.onnx", str(installed))
            runtime = sys.modules["onnxruntime"]
            self.assertEqual(Path(runtime.__file__).parent, installed / "onnxruntime")
            path, options, providers = runtime.calls["session"]
        self.assertEqual(sys.path[0], str(installed))
        self.assertEqual((path, providers), ("model-weights.onnx", ["CPUExecutionProvider"]))
        self.assertEqual((options.intra_op_num_threads, options.inter_op_num_threads), (1, 1))
        self.assertIsInstance(model, LocalFailureModel)

    def test_load_reuses_a_pinned_runtime_that_is_already_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = self.install_runtime(directory)
            sys.path.insert(0, str(installed))
            imported = importlib.import_module("onnxruntime")
            model = LocalFailureModel.load("model-weights.onnx", str(installed))
        self.assertIs(sys.modules["onnxruntime"], imported)
        self.assertEqual(sys.path.count(str(installed)), 1)
        self.assertIsInstance(model, LocalFailureModel)

    def test_load_refuses_a_runtime_that_did_not_pin_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = self.install_runtime(directory, version="1.24.0")
            with self.assertRaisesRegex(RuntimeError, "did not load"):
                LocalFailureModel.load("model-weights.onnx", str(installed))

    def test_load_refuses_a_foreign_runtime_already_imported_in_cura(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = self.install_runtime(directory)
            foreign = SimpleNamespace(
                __file__=str(Path(directory, "site-packages", "onnxruntime", "__init__.py")),
                __version__="1.23.2")
            with patch.dict(sys.modules, {"onnxruntime": foreign}):
                with self.assertRaisesRegex(RuntimeError, "Another inference runtime"):
                    LocalFailureModel.load("model-weights.onnx", str(installed))
            self.assertNotIn(str(installed), sys.path)

    def test_score_rejects_an_empty_frame_before_touching_the_session(self):
        session = FakeSession()
        with self.assertRaisesRegex(ValueError, "empty camera frame"):
            LocalFailureModel(session).score(QImage())
        self.assertIsNone(session.feed)

    def test_score_rejects_foreign_output_arities_and_shapes(self):
        image = QImage(16, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        boxes = np.array([[[[0., 0., .2, .2]]]])
        for outputs in ([boxes], [boxes, np.array([[[.5]]]), np.array([[[.5]]])]):
            with self.subTest(outputs=len(outputs)), \
                    self.assertRaisesRegex(ValueError, "Unexpected failure model outputs"):
                LocalFailureModel(FakeSession(outputs)).score(image)
        for confidences in (np.array([[.5]]), np.array([[[.5]], [[.5]]]), np.array([[[.5, .5]]])):
            with self.subTest(shape=confidences.shape), \
                    self.assertRaisesRegex(ValueError, "output shapes"):
                LocalFailureModel(FakeSession([boxes, confidences])).score(image)
        doubled = np.array([[[[0., 0., .2, .2]]], [[[0., 0., .2, .2]]]])
        with self.assertRaisesRegex(ValueError, "output shapes"):
            LocalFailureModel(FakeSession([doubled, np.array([[[.5]]])])).score(image)

    def test_score_rejects_non_finite_coordinates_from_a_confident_box(self):
        image = QImage(16, 8, QImage.Format.Format_RGB888)
        image.fill(0)
        for value in (float("inf"), float("-inf"), float("nan")):
            boxes = np.array([[[[0., 0., value, .3]]]])
            with self.subTest(value=value), \
                    self.assertRaisesRegex(ValueError, "coordinates"):
                LocalFailureModel(FakeSession([boxes, np.array([[[.9]]])])).score(image)
        filtered = np.array([[[[0., 0., float("inf"), .3]]]])
        self.assertEqual(LocalFailureModel(FakeSession([filtered, np.array([[[.05]]])])).score(image), 0.0)

    def test_load_requires_the_pinned_runtime_directory(self):
        # Fail-closed: a caller that omits the directory must refuse,
        # never import whatever onnxruntime the process carries.
        with self.assertRaisesRegex(RuntimeError, "runtime directory is required"):
            LocalFailureModel.load("model-weights.onnx", "")

    def test_a_nameless_imported_runtime_is_refused(self):
        with patch.dict(sys.modules, {"onnxruntime": SimpleNamespace(__file__=None)}):
            with self.assertRaisesRegex(RuntimeError, "already loaded"):
                LocalFailureModel.load("model-weights.onnx", "/nonexistent/installed")


if __name__ == "__main__":
    unittest.main()
