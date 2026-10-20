# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-20 — 6.0.0: Separate raster painting, Qt assets and worker publication.
# 2026-10-18 — 6.0.0: Convert empty immutable geometry and event markers to Qt-compatible values.
# 2026-10-01 [02e4bd5] Draw switchable bed-axis arrows across plate views
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-09-21 [f130718] 4.6.0: the native render path — dense toolpath geometry rasterises off the QML JavaScript
#   path (QPainterPath per segment on a worker, the measured winner at ~120 ms for 500k motions), the face
#   composes blitted QImages with role-free reuse, the scrub keeps its vector delta, and the whole pipeline is
#   generation-gated and latest-wins coalesced


"""Atomic publication of immutable raster images for QML consumers."""
import os
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QImage

def png_file(image: QImage, directory: str, name: str) -> str:
    """The raster transport (engine-proven): a QImage variant
    segfaults the Qt6 Canvas and its QML reads see nothing inside
    it, and a data: URL loads but never renders in a QQuickImage —
    a file:// PNG is the one source the scene-graph Image draws.
    Written on the WORKER, never the owner thread. ATOMIC: the PNG
    encodes to a temporary sibling and only renames into the final
    immutable name once the save returned True — a URL is never
    published for a failed write, and a reader never sees a
    half-written file."""
    if image is None or image.width() <= 0:
        return ""
    path = os.path.join(directory, f"{name}.png")
    temp = f"{path}.tmp-{os.getpid()}"
    try:
        os.makedirs(directory, exist_ok=True)
        if not image.save(temp, "PNG"):
            try:
                os.unlink(temp)
            except OSError:
                pass
            return ""
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        return ""
    return QUrl.fromLocalFile(path).toString()


