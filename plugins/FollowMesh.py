"""The follow pass's mesh baking — UM-free so the logic is testable
on any host with numpy (the pass module itself imports UM).

A plugin MeshData over Cura's flat-line LayerData, referencing its
arrays (no copy) and adding the two progress attributes:

a_layer: the layer index of every vertex.
a_line: the within-layer line index of every vertex (both vertices of
        a line carry the same value, so a culled line loses both
        endpoints together).
"""
from __future__ import annotations

PASS_NAME = "moonraker_follow"


def build_follow_mesh(layer_data, mesh_factory=None):
    """Bake the progress attributes into a MeshData over the source
    arrays. mesh_factory is injectable for tests (the real UM
    MeshData comes from the pass module)."""
    if mesh_factory is None:
        from UM.Mesh.MeshData import MeshData
        mesh_factory = MeshData
    # numpy is imported lazily: Cura's runtime always ships it, but
    # the module must still import on hosts without it.
    import numpy
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
    return mesh_factory(vertices=vertices, indices=indices, colors=colors,
                        attributes=attributes)
