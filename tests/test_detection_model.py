"""The local model's image shape and confidence contract."""

import unittest
from types import SimpleNamespace

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


class LocalFailureModelTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
