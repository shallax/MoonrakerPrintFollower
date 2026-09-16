"""The plugin-owned live-follow render pass (the review's architecture).

While following, Cura's SimulationPass is disabled and THIS pass feeds
the "simulationview" compositor layer instead. It renders Cura's own
flat-line LayerData — the same vertex/index/color arrays, referenced,
not copied — through a plugin shader whose progress is two uniforms
(u_current_layer, u_current_path). Nothing is re-uploaded per frame:
the mesh and its GL buffers are built once per loaded file, and the
33 ms ticks change only uniform values.

The cutoff matches Cura's own ranged draw exactly: Cura draws line k
when k < int(current_path) (its ranged batch ends at
start + int(path) * 2 indices), so the shader drops every vertex of a
line whose baked a_layer/a_line exceeds the current progress. Cura
truncates at whole-line granularity in the flat view — the fractional
position is the toolhead's job, not the line mesh's.

Nothing here modifies Cura or Uranium: the pass uses the public
Renderer/CompositePass APIs (addRenderPass, setLayerBindings), the
same integration SimulationView itself performs.
"""
from __future__ import annotations

import math
import os
from typing import Optional

from UM.Scene.SceneNode import SceneNode
from UM.View.RenderBatch import RenderBatch
from UM.View.RenderPass import RenderPass
from UM.View.GL.ShaderProgram import ShaderProgram

from UM.Mesh.MeshData import MeshData

from .FollowMesh import PASS_NAME, build_follow_mesh


class FollowPass(RenderPass):
    """Renders the followed toolpath with uniform-driven progress."""

    def __init__(self):
        super().__init__(PASS_NAME, 1, 1)
        self._shader: Optional[ShaderProgram] = None
        self._nozzle_shader = None
        self._batch: Optional[RenderBatch] = None
        self._mesh: Optional[MeshData] = None
        self._node: Optional[SceneNode] = None
        self._layer_data = None
        self._view = None
        self._layer = 0
        self._path = 0.0
        self._toolhead_enabled = True

    def setFollowView(self, view) -> None:
        """The SimulationView whose visibility toggles and active
        extruder the pass mirrors (the same reads as Cura's own
        pass)."""
        self._view = view

    def setFollowState(self, layer: int, path: float, toolhead: bool = True) -> None:
        """The 33 ms tick: uniforms only, no buffers change."""
        self._layer = int(layer)
        self._path = float(path)
        self._toolhead_enabled = bool(toolhead)

    def setFollowScene(self, node: Optional[SceneNode], layer_data) -> None:
        """Install (or clear) the followed toolpath. The mesh is built
        once per loaded file — a re-attach with the same layer data
        (a hydration wait) reuses it."""
        if node is None or layer_data is None:
            self._batch = None
            self._mesh = None
            self._node = None
            self._layer_data = None
            return
        if self._node is node and self._layer_data is layer_data and self._mesh is not None:
            return
        self._node = node
        self._layer_data = layer_data
        self._mesh = build_follow_mesh(layer_data)
        self._batch = None
        self._shader = None

    def _ensure_batch(self) -> Optional[RenderBatch]:
        if self._batch is not None:
            return self._batch
        if self._mesh is None or self._node is None:
            return None
        if self._shader is None:
            shader_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "follow_lines.shader")
            self._shader = ShaderProgram()
            self._shader.load(shader_path)
        batch = RenderBatch(self._shader, type=RenderBatch.RenderType.Solid,
                            mode=RenderBatch.RenderMode.Lines)
        batch.addItem(self._node.getWorldTransformation(), self._mesh)
        self._batch = batch
        return batch

    def render(self) -> None:
        batch = self._ensure_batch()
        if batch is None or self._shader is None or self._scene is None:
            return
        camera = self._scene.getActiveCamera()
        if camera is None:
            return
        self._shader.setUniformValue("u_current_layer", self._layer)
        self._shader.setUniformValue("u_current_path", self._path)
        # The visibility toggles and the active extruder, read exactly
        # as Cura's own pass reads them, so the two renders agree.
        if self._view is not None:
            try:
                self._shader.setUniformValue("u_show_travel_moves", 1 if self._view.getShowTravelMoves() else 0)
                self._shader.setUniformValue("u_show_helpers", 1 if self._view.getShowHelpers() else 0)
                self._shader.setUniformValue("u_show_skin", 1 if self._view.getShowSkin() else 0)
                self._shader.setUniformValue("u_show_infill", 1 if self._view.getShowInfill() else 0)
            except Exception:
                pass
        try:
            from cura.Settings.ExtruderManager import ExtruderManager
            self._shader.setUniformValue("u_active_extruder",
                                         float(max(0, ExtruderManager.getInstance().activeExtruderIndex)))
        except Exception:
            pass
        batch.render(camera)
        self._render_toolhead(camera)

    def _render_toolhead(self, camera) -> None:
        """The toolhead indicator — Cura's own NozzleNode and
        color.shader (all public API, the same pieces SimulationPass
        uses), positioned by the same polygon walk Cura performs, so
        the indicator survives the SimulationPass handoff."""
        if self._view is None or not self._toolhead_enabled:
            return
        head = self._head_position()
        if head is None:
            return
        from UM.Math.Vector import Vector
        try:
            nozzle = self._view.getNozzleNode()
            nozzle.setPosition(Vector(head[0], head[1], head[2]))
            if self._nozzle_shader is None:
                from UM.View.GL.OpenGL import OpenGL
                from UM.Resources import Resources
                from UM.Math.Color import Color
                from UM.Application import Application
                self._nozzle_shader = OpenGL.getInstance().createShaderProgram(
                    Resources.getPath(Resources.Shaders, "color.shader"))
                self._nozzle_shader.setUniformValue("u_color", Color(
                    *Application.getInstance().getTheme().getColor("layerview_nozzle").getRgb()))
            nozzle_batch = RenderBatch(self._nozzle_shader, type=RenderBatch.RenderType.Transparent)
            nozzle_batch.addItem(nozzle.getWorldTransformation(), mesh=nozzle.getMeshData())
            nozzle_batch.render(camera)
        except Exception:
            pass

    def _head_position(self):
        """Cura's own head derivation: the path index over the current
        layer's polygons, interpolated by the fractional ratio."""
        layer_data = self._layer_data
        if layer_data is None:
            return None
        polygons_layer = layer_data.getLayer(self._layer)
        if polygons_layer is None:
            return None
        path = float(self._path)
        if math.isnan(path):
            index = 0
        else:
            index = int(path)
        ratio = path - math.floor(path)
        from UM.Math.Vector import Vector
        for polygon in polygons_layer.polygons:
            data = polygon.data
            size = data.size // 3
            if index >= size:
                index -= size
                continue
            pos_a = Vector(float(data[index][0]), float(data[index][1]), float(data[index][2]))
            if ratio <= 0.0001 or index + 1 == len(data):
                head = pos_a
            else:
                pos_b = Vector(float(data[index + 1][0]), float(data[index + 1][1]),
                               float(data[index + 1][2]))
                head = pos_a * (1.0 - ratio) + pos_b * ratio
            if self._node is not None:
                head += self._node.getWorldPosition()
            return head
        return None
