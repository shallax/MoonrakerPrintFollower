# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-18 [53afbd7] 4.5.0: the reviewer-attribution phrasing goes from the codebase
# 2026-09-15 [ff4666f] 4.2.0 snapshot rounds: the bed-mesh range filter and the live-verdict fixes
# 2026-09-06 [2f17f60] Render Klipper bed mesh in Cura Preview
# 2026-10-20 — 6.0.0: Delegate sampling and geometry arrays to the portable geometry owner.

from __future__ import annotations

from ..geometry.BedMeshGeometry import BedMeshGeometry

from UM.Mesh.MeshData import MeshData
from UM.Resources import Resources
from UM.Scene.SceneNode import SceneNode
from UM.View.GL.OpenGL import OpenGL


class BedMeshSceneNode(SceneNode):
    """Non-selectable coloured Klipper bed-mesh surface for Cura's 3D scene.

    Klipper only knows the configured mesh bounds. Cura, however, renders the
    complete build plate. The measured/interpolated Klipper surface therefore
    remains exactly where Klipper says it is, while one additional outer ring
    extends to the physical Cura bed edges, sampled with KLIPPER'S OWN CLAMP
    (bed_mesh._get_linear_index constrains both the index and the fraction):
    outside the probed bounds the compensation continues the boundary edge's
    interpolated value — no made-up slope, no drop to zero (the accuracy
    ruling, 2026-09-15). Extended vertices are intentionally more
    transparent so the unprobed region is not presented as measured data. A
    neon-orange raised ribbon follows the exact Klipper mesh bounds so the
    measured/interpolated area remains obvious even when the alpha change at
    the extended perimeter is visually subtle.
    """

    def __init__(self) -> None:
        super().__init__(name="Moonraker bed mesh", node_id="MoonrakerPrintFollowerBedMesh")
        self.setSelectable(False)
        self.setCalculateBoundingBox(False)
        self._shader = None

    def clear(self) -> None:
        self.setMeshData(None)
        self.setVisible(False)

    def updateMesh(self, snapshot, machine_width, machine_depth, center_is_zero,
                   exaggeration=BedMeshGeometry.DEFAULT_EXAGGERATION, low=None, high=None) -> bool:
        arrays = BedMeshGeometry.build(snapshot, machine_width, machine_depth,
                                       center_is_zero, exaggeration, low, high)
        if arrays is None:
            self.clear()
            return False
        self.setMeshData(MeshData(vertices=arrays.vertices, indices=arrays.indices, colors=arrays.colours))
        return True

    def render(self, renderer) -> bool:
        mesh = self.getMeshData()
        if mesh is None or not self.isVisible():
            return True
        if self._shader is None:
            self._shader = OpenGL.getInstance().createShaderProgram(
                Resources.getPath(Resources.Shaders, "default.shader")
            )
        renderer.queueNode(
            self,
            shader=self._shader,
            transparent=True,
            backface_cull=False,
            sort=-4,
        )
        return True
