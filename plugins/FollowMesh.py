"""The follow pass's mesh baking — UM-free so the logic is testable
on any host with numpy (the pass module itself imports UM).

Cura's flat-line LayerData shares vertices between adjacent lines
(A B C D with index pairs A-B, B-C, C-D), so no single vertex can
carry a line identity. The follow representation is de-indexed: the
vertices and the consumed attributes are expanded through the
flattened index array into two unique endpoints per line, and the
mesh carries NO indices — the pass draws plain GL_LINES by array
(the per-frame ranged EBO path is avoided entirely). The expansion
is one-time, bounded memory per loaded file.

The progress attributes are float32 (opengl_type "float"): Uranium
passes attributes through setAttributeBuffer, which uses normalized
attributes — integer GLSL attributes need different treatment, and
the layer/line ranges are far below float32's integer-precision
limit.
"""
from __future__ import annotations

PASS_NAME = "moonraker_follow"


def build_follow_mesh(layer_data, mesh_factory=None):
    """Bake the progress attributes into a de-indexed MeshData over
    the source arrays. mesh_factory is injectable for tests (the real
    UM MeshData comes from the pass module)."""
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
    index_count = len(indices)
    if index_count == 0:
        return None
    # The de-index: every index pair becomes two unique endpoints.
    expanded_vertices = vertices[indices]
    attributes = {}
    try:
        for name in layer_data.attributeNames():
            if name in ("prev_line_types",):
                continue
            attribute = layer_data.getAttribute(name)
            value = attribute.get("value")
            if value is None or len(value) != len(vertices):
                continue
            expanded = value[indices]
            entry = dict(attribute)
            entry["value"] = expanded
            attributes[name] = entry
    except (AttributeError, TypeError):
        attributes = {}
    # The progress metadata, float32 (the reviewer's attribute typing):
    # layer L owns indices [start, start + counts[L]) — counts are
    # index counts, two per line — and each expanded vertex carries
    # the layer and the within-layer line id (both endpoints of a
    # line share the same id).
    layers = numpy.empty(index_count, numpy.float32)
    lines = numpy.empty(index_count, numpy.float32)
    cursor = 0
    for layer in sorted(counts):
        size = int(counts[layer])
        if size <= 0 or cursor + size > index_count:
            continue
        end = cursor + size
        layers[cursor:end] = float(layer)
        lines[cursor:end] = numpy.arange(size, dtype=numpy.float32) // 2.0
        cursor = end
    if cursor < index_count:
        # Indices beyond the element-count table: mark them past the
        # end of the last layer so they never render.
        layers[cursor:] = float(1 << 30)
        lines[cursor:] = 0.0
    attributes["layer"] = {"opengl_type": "float", "value": layers, "opengl_name": "a_layer"}
    attributes["line"] = {"opengl_type": "float", "value": lines, "opengl_name": "a_line"}
    return mesh_factory(vertices=expanded_vertices, indices=None, colors=colors[indices] if colors is not None else None,
                        attributes=attributes)
