#!/usr/bin/env python3
"""Allow only tiny CPU rounding differences in committed capture comparisons.

Repeated captures on the same host are still checked byte-for-byte separately.
"""
import sys
from pathlib import Path

from PyQt6.QtGui import QImage


def matches(reference, generated):
    reference, generated = Path(reference), Path(generated)
    if not reference.is_file() or not generated.is_file():
        return False
    if reference.read_bytes() == generated.read_bytes():
        return True
    a, b = QImage(str(reference)), QImage(str(generated))
    if a.isNull() or b.isNull() or a.size() != b.size():
        return False
    a, b = (im.convertToFormat(QImage.Format.Format_RGBA8888) for im in (a, b))
    pa, pb = bytes(a.constBits().asstring(a.sizeInBytes())), bytes(b.constBits().asstring(b.sizeInBytes()))
    changed = 0
    for i in range(0, len(pa), 4):
        if pa[i:i + 4] == pb[i:i + 4]:
            continue
        changed += 1
        if changed > 32 or any(abs(pa[j] - pb[j]) > 2 for j in range(i, i + 4)):
            return False
    return True


def main():
    reference, generated = map(Path, sys.argv[1:])
    failed = False
    for image in sorted(generated.glob("*.png")):
        if not matches(reference / image.name, image):
            print("stale screenshot: " + image.name)
            failed = True
    return int(failed or not list(generated.glob("*.png")))


if __name__ == "__main__":
    sys.exit(main())
