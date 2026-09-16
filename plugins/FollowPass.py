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

import os
from typing import Optional

import numpy

from UM.Scene.SceneNode import SceneNode
from UM.View.RenderBatch import RenderBatch
from UM.View.RenderPass import RenderPass
from UM.View.GL.ShaderProgram import ShaderProgram

from UM.Mesh.MeshData import MeshData

PASS_NAME = "moonraker_follow"


def build_follow_mesh(layer_data) -> Optional[MeshData]:
    """A plugin MeshData over Cura's flat-line LayerData, referencing
    its arrays (no copy) and adding the two progress attributes.

    a_layer: the layer index of every vertex.
    a_line: the within-layer line index of every vertex (both vertices
            of a line carry the same value, so a culled line loses
            both endpoints together).
    """
    try:
        vertices = layer_data.getVertices()
        indices = layer_data.getIndices()
        colors = layer_data.getColors()
        counts = layer_data.getElementCounts()
    except (AttributeError, KeyError):
        return None
    if vertices is None or indices is None or not counts:
        return None
    vertex_count = len(vertices)
    if vertex_count == 0:
        return None
    layers = numpy.empty(vertex_count, numpy.int32)
    lines = numpy.empty(vertex_count, numpy.int32)
    cursor = 0
    for layer in sorted(counts):
        size = int(counts[layer])
        if size <= 0 or cursor + size > vertex_count:
            continue
        end = cursor + size
        layers[cursor:end] = layer
        lines[cursor:end] = numpy.arange(size, dtype=numpy.int32) // 2
        cursor = end
    if cursor < vertex_count:
        # Vertices beyond the element-count table: mark them past the
        # end of the last layer so they never render.
        layers[cursor:] = 1 << 30
        lines[cursor:] = 0
    attributes = {
        "layer": {"opengl_type": "int", "value": layers, "opengl_name": "a_layer"},
        "line": {"opengl_type": "int", "value": lines, "opengl_name": "a_line"},
    }
    # Carry the source layer-data's per-vertex attributes by reference
    # (extruder, line_type, material_color) so the shader's colouring
    # and visibility rules see the same values as Cura's own pass.
    try:
        for name in layer_data.attributeNames():
            if name in ("layer", "line", "prev_line_types"):
                continue
            attribute = layer_data.getAttribute(name)
            value = attribute.get("value")
            if value is None or len(value) != vertex_count:
                continue
            attributes[name] = dict(attribute)
    except (AttributeError, TypeError):
        pass
    return MeshData(vertices=vertices, indices=indices, colors=colors, attributes=attributes)


class FollowPass(RenderPass):
    """Renders the followed toolpath with uniform-driven progress."""

    def __init__(self):
        super().__init__(PASS_NAME, 1, 1)
        self._shader: Optional[ShaderProgram] = None
        self._batch: Optional[RenderBatch] = None
        self._mesh: Optional[MeshData] = None
        self._node: Optional[SceneNode] = None
        self._layer_data = None
        self._layer = 0
        self._path = 0.0

    def setFollowState(self, layer: int, path: float) -> None:
        """The 33 ms tick: uniforms only, no buffers change."""
        self._layer = int(layer)
        self._path = float(path)

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
        batch.render(camera)
