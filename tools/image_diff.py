#!/usr/bin/env python3
"""The determinism gate's mismatch diagnostics: when two captures
differ, print the differing-pixel count, the changed bounding box and
the image dimensions, and write a visual diff beside the first image
(changed pixels red) so the differing region is identifiable at a
glance."""
import sys
from pathlib import Path

import numpy as np
from PyQt6.QtGui import QColor, QImage

NATIVE_3D_CAPTURES = {"13-toolhead-lighting.png", "14-toolhead-printing.png"}
MAX_ROUNDING_PIXELS = 16


def native_3d_rounding(first, second):
    """Accept sparse one-level RGB rounding only in opaque 3D showcase PNGs.

    Native software GL can straddle an 8-bit rounding boundary. This is not
    an antialias tolerance: altered alpha, two-level changes, geometry holes
    and changes spanning more than 16 pixels all fail.
    """
    first, second = Path(first), Path(second)
    if first.name != second.name or first.name not in NATIVE_3D_CAPTURES:
        return False
    a, b = QImage(str(first)), QImage(str(second))
    if a.isNull() or b.isNull() or a.size() != b.size():
        return False
    def pixels(image):
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        return np.frombuffer(image.constBits().asstring(image.sizeInBytes()),
                             dtype=np.uint8).reshape(-1, 4)
    left, right = pixels(a), pixels(b)
    if np.any(left[:, 3] != 255) or np.any(right[:, 3] != 255):
        return False
    delta = np.abs(left.astype(np.int16) - right.astype(np.int16))
    count = int(np.count_nonzero(np.any(delta, axis=1)))
    safe = count <= MAX_ROUNDING_PIXELS and int(delta.max()) <= 1
    if safe:
        print("%s: %d pixels differ by at most one RGB level (native GL rounding)"
              % (first.name, count))
    return safe


def main():
    first = Path(sys.argv[1])
    second = Path(sys.argv[2])
    if sys.argv[3:] == ["--native-3d-rounding"]:
        return 0 if native_3d_rounding(first, second) else 1
    a = QImage(str(first))
    b = QImage(str(second))
    if a.size() != b.size():
        print("sizes differ:", a.width(), "x", a.height(),
              "vs", b.width(), "x", b.height())
        return 1
    min_x, min_y, max_x, max_y = a.width(), a.height(), -1, -1
    count = 0
    for y in range(a.height()):
        for x in range(a.width()):
            if a.pixel(x, y) != b.pixel(x, y):
                count += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    print("%s: %d differing pixels, bbox x=%d..%d y=%d..%d of %dx%d" % (
        first.name, count, min_x, max_x, min_y, max_y, a.width(), a.height()))
    if count:
        out = first.parent / ("diff-" + first.name)
        diff = a.copy()
        red = QColor(255, 0, 0).rgba()
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                if a.pixel(x, y) != b.pixel(x, y):
                    diff.setPixel(x, y, red)
        diff.save(str(out))
        print("visual diff written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
