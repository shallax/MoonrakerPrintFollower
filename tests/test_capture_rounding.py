"""Native 3D rounding never masks alpha, geometry or UI capture regressions."""
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtGui import QColor, QImage

from tools.image_diff import native_3d_rounding


class CaptureRoundingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "a").mkdir()
        (self.root / "b").mkdir()
        self.image = QImage(32, 24, QImage.Format.Format_RGBA8888)
        self.image.fill(QColor(50, 60, 70, 255))

    def matches(self, altered, name="14-toolhead-printing.png"):
        a, b = self.root / "a" / name, self.root / "b" / name
        self.assertTrue(self.image.save(str(a)))
        self.assertTrue(altered.save(str(b)))
        return native_3d_rounding(a, b)

    def test_sparse_one_level_rgb_rounding_is_accepted(self):
        other = self.image.copy()
        for x in range(16):
            other.setPixelColor(x, 0, QColor(51, 59, 71, 255))
        self.assertTrue(self.matches(other))

    def test_seventeen_changed_pixels_are_rejected(self):
        other = self.image.copy()
        for x in range(17):
            other.setPixelColor(x, 0, QColor(51, 60, 70, 255))
        self.assertFalse(self.matches(other))

    def test_two_level_colour_change_is_rejected(self):
        other = self.image.copy()
        other.setPixelColor(0, 0, QColor(52, 60, 70, 255))
        self.assertFalse(self.matches(other))

    def test_alpha_change_or_transparent_hole_is_rejected(self):
        for alpha in (254, 0):
            with self.subTest(alpha=alpha):
                other = self.image.copy()
                other.setPixelColor(0, 0, QColor(50, 60, 70, alpha))
                self.assertFalse(self.matches(other))

    def test_ui_capture_is_never_given_rounding_tolerance(self):
        self.assertFalse(self.matches(self.image.copy(), "11-print-follower.png"))

    def test_missing_or_different_sized_images_are_rejected(self):
        self.assertFalse(native_3d_rounding(self.root / "13-toolhead-lighting.png",
                                          self.root / "missing" / "13-toolhead-lighting.png"))
        self.assertFalse(self.matches(QImage(31, 24, QImage.Format.Format_RGBA8888)))


if __name__ == "__main__":
    unittest.main()
