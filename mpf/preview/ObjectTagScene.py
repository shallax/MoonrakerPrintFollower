# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Separate scene reading from portable object-tag projection and progress fallback.

"""Read only selectable scene bounds; do not infer object names or print progress."""
from ..printing.ObjectTagPresentation import SceneTagObject


def read_scene_objects(root):
    rows = []
    for node in root.getAllChildren():
        try:
            if not node.isSelectable() or node.getMeshData() is None or not node.isVisible():
                continue
            box = node.getBoundingBox()
            if box is None or not box.isValid():
                continue
            center = box.center
            rows.append(SceneTagObject(id(node), str(node.getName() or ""),
                                       (center.x, getattr(center, "y", 0), center.z),
                                       box.top, box.bottom, box.height))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return rows
