#!/usr/bin/env python3
"""The determinism gate's mismatch diagnostics: when two captures
differ, print the differing-pixel count, the changed bounding box and
the image dimensions, and write a visual diff beside the first image
(changed pixels red) so the differing region is identifiable at a
glance."""
import sys
from pathlib import Path

from PyQt6.QtGui import QColor, QImage


def main():
    first = Path(sys.argv[1])
    second = Path(sys.argv[2])
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
