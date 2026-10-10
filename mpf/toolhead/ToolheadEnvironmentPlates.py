"""Complete distance-only platform/grid/heightmesh geometry for path recovery.

No plate colour is reconstructed here. Original plate triangles block a path
correction; tied or uncertain ordering must retain the ordinary map branch.
Only the certified public shader recipes below have model-only positions and
no fragment discard/depth replacement. Unknown recipes refuse the whole set.
"""
from dataclasses import dataclass
import hashlib
import re
import struct

import numpy as np

from .ToolheadCaptureValues import CaptureMesh, UniformValue
from .ToolheadEnvironmentPaths import _layout, _world, _tree, _cancel, _generation

CHUNK = 8192
LIMIT = 3000000
# Canonical public GLSL410 source, ignoring only comments/whitespace. Any new
# shader semantics require review instead of assuming a familiar recipe name.
RECIPES = {
 'default': ('9c9b4609f5463cf12289e6105c531ee4db60fc03a24e681d794becbee4fafe5c',
             '8c786eedda7d30ba49ef40a505d45f5592e8c37f40d457cc6b1e83cde6582658'),
 'platform': ('b0e744755467c4da1db99ba82c1577b7ac46219c05e2891c443cf667952d6dc8',
              '98ca8d74f127b5c312b84ccdbdd83db3da1aae1b45affe5eff0667543d961739'),
 'grid': ('c0d62ad02cd406f77024d7049e807a1500b2cfb455e74545ccc00598cb592fd6',
          '01d44b7ea94e5453382da20c9b4173d88a58f534a42739a3e305d84fc31a0db1'),
}
POSITION_BINDINGS = {'u_modelMatrix': 'model_matrix', 'u_viewMatrix': 'view_matrix',
                     'u_projectionMatrix': 'projection_matrix'}


@dataclass(frozen=True, eq=False)
class PreparedPlates:
    positions: np.ndarray
    triangles: np.ndarray
    models: np.ndarray
    nodes: np.ndarray
    sources: tuple
    certificate: tuple
    max_depth: int
    source_bytes: int
    retained_bytes: int
    gpu_bytes: int
    peak_bytes: int


def certify_plate_recipe(key, stages, bindings):
    if type(key) is not str or key not in RECIPES or type(stages) is not tuple or len(stages) != 2:
        raise ValueError('Unsupported complete plate geometry recipe')
    hashes = {}
    for stage, source in stages:
        if stage not in ('vertex', 'fragment') or stage in hashes or type(source) is not str or len(source) > 65536:
            raise ValueError('Bounded original plate stages required')
        body = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', source, flags=re.S)
        hashes[stage] = hashlib.sha256(re.sub(r'\s+', '', body).encode()).hexdigest()
    if tuple(hashes.get(stage) for stage in ('vertex', 'fragment')) != RECIPES[key]:
        raise ValueError('Plate position/depth shader semantics differ')
    if (type(bindings) is not tuple or len(bindings) > 16
            or any(type(entry) is not tuple or len(entry) != 2
                or any(type(value) is not str for value in entry) for entry in bindings)):
        raise ValueError('Original plate position bindings required')
    bound = dict(bindings)
    if len(bound) != len(bindings) or any(bound.get(name) != value for name, value in POSITION_BINDINGS.items()):
        raise ValueError('Original plate position bindings required')
    return key, RECIPES[key], bindings


def _model(value):
    if (type(value) is not UniformValue or value.kind != 'matrix' or type(value.value) is not tuple
            or len(value.value) != 16 or any(type(item) is not float for item in value.value)):
        raise ValueError('Frozen original plate model required')
    try: body = struct.pack('16f', *value.value)
    except (OverflowError, struct.error) as error: raise ValueError('Unrepresentable plate model') from error
    model = np.frombuffer(body, np.float32).reshape(4, 4)
    if not np.isfinite(model).all() or not np.array_equal(model[3], (0, 0, 0, 1)):
        raise ValueError('Finite affine original plate model required')
    return model, body


def prepare_plate_source(plates, recipes, generation, *, byte_budget=256*1024**2,
                         retained_bytes=0, cancel=None):
    """Pack original local vertices + indexed triples, never expanded soup.

    ``recipes`` contains plain (key, stages, bindings) from this same frozen
    frame. Transform/view/projection overrides are explicitly unsupported.
    Every original triangle is admitted, regardless of presentation alpha.
    """
    _cancel(cancel)
    generation = _generation(generation)
    if (type(plates) is not tuple or len(plates) > 2048 or type(recipes) is not tuple
            or len(recipes) > 3 or type(retained_bytes) is not int or retained_bytes < 0):
        raise ValueError('Bounded frozen plate set required')
    approved = {}
    for key, stages, bindings in recipes:
        if key in approved: raise ValueError('Duplicate plate recipe')
        approved[key] = certify_plate_recipe(key, stages, bindings)
    sources, certificates, vertex_count, triangle_count, source_bytes = [], [], 0, 0, 0
    for entry in plates:
        _cancel(cancel)
        if type(entry) is not tuple or len(entry) != 6:
            raise ValueError('Original frozen plate entry required')
        key, mesh, transform, _normal, uniforms, settings = entry
        if type(key) is not str or key not in approved or type(mesh) is not CaptureMesh or type(mesh.identity) is not int or mesh.identity <= 0:
            raise ValueError('Certified original plate publication required')
        # item uniforms update semantic bindings after the camera. Settings
        # update uniforms by name. Neither may alter the admitted projection.
        for values in (uniforms, settings):
            if (type(values) is not tuple or len(values) > 128
                    or any(type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                        or item[0] in {*POSITION_BINDINGS, *POSITION_BINDINGS.values()} for item in values)):
                raise ValueError('Plate position overrides require separate admission')
        vertices = mesh.vertices
        layout = _layout(vertices, np.dtype('float32'), 3)
        model, body = _model(transform)
        count = len(vertices)
        if count > 250000: raise ValueError('Plate publication exceeds bounded vertex count')
        indices = mesh.indices
        if indices is None:
            if count % 3: raise ValueError('Complete nonindexed plate triangles required')
            index_layout, triangles = None, count//3
        else:
            if (type(indices) is not np.ndarray or indices.dtype not in (np.dtype('int32'), np.dtype('uint32'))
                    or indices.ndim not in (1, 2) or not indices.flags.c_contiguous or indices.size % 3):
                raise ValueError('Complete original plate index triples required')
            indices = indices.view(np.uint32).reshape(-1, 3)
            index_layout, triangles = _layout(indices, np.dtype('uint32'), 3), len(indices)
        source_bytes += mesh.retained_bytes()
        sources.append((mesh, indices, model))
        certificates.append((approved[key], mesh.identity, layout, index_layout, body))
        vertex_count += count; triangle_count += triangles
    if vertex_count > LIMIT or triangle_count > 1000000:
        raise ValueError('Complete plate geometry exceeds bounded storage')
    groups = (triangle_count+7)//8
    node_count = max(0, 2*groups-1)
    payload_bytes = 12*vertex_count+16*triangle_count+64*len(sources)+32*node_count
    gpu_bytes = payload_bytes
    owned_bytes = payload_bytes+64*len(sources)
    peak = retained_bytes+source_bytes+2*payload_bytes+64*len(sources)+256*groups+16*1024**2
    if type(byte_budget) is not int or byte_budget < 0 or peak > byte_budget:
        raise MemoryError('Complete plate geometry exceeds combined memory budget')
    # Large owned allocations begin only after the complete conservative receipt.
    positions = np.empty((vertex_count, 3), np.float32)
    records = np.empty((triangle_count, 4), np.uint32)
    models = np.empty((len(sources), 4, 4), np.float32)
    low, high = np.empty((groups, 3), np.float32), np.empty((groups, 3), np.float32)
    cursor = vertex_cursor = 0
    for asset, (mesh, indices, model) in enumerate(sources):
        _cancel(cancel)
        models[asset] = model
        for first in range(0, len(mesh.vertices), CHUNK):
            _cancel(cancel)
            part = mesh.vertices[first:first+CHUNK]
            if not np.isfinite(part).all(): raise ValueError('Nonfinite original plate positions')
            positions[vertex_cursor+first:vertex_cursor+first+len(part)] = part
        count = len(mesh.vertices)//3 if indices is None else len(indices)
        for first in range(0, count, CHUNK):
            _cancel(cancel)
            size = min(CHUNK, count-first)
            part = np.arange(first*3, (first+size)*3, dtype=np.uint32).reshape(-1, 3) if indices is None else indices[first:first+size]
            if (part >= len(mesh.vertices)).any(): raise ValueError('Plate index outside original publication')
            records[cursor+first:cursor+first+size, :3] = part+vertex_cursor
            records[cursor+first:cursor+first+size, 3] = asset
        cursor += count; vertex_cursor += len(mesh.vertices)
    for first in range(0, triangle_count, CHUNK):
        _cancel(cancel)
        end = min(triangle_count, first+CHUNK)
        record = records[first:end]
        points = positions[record[:, :3]].astype(np.float64)
        # Process each admitted asset's original F32 model expression in bounded
        # gathers; grouping cannot substitute one occurrence's transform.
        lo, hi = np.empty((len(record), 3), np.float32), np.empty((len(record), 3), np.float32)
        for asset in np.unique(record[:, 3]):
            _cancel(cancel)
            mask = record[:, 3] == asset
            world, error = _world(points[mask], models[asset])
            lower, upper = (world-error).min(axis=1), (world+error).max(axis=1)
            if not np.isfinite(lower).all() or not np.isfinite(upper).all() or (np.abs(lower) >= np.finfo(np.float32).max).any() or (np.abs(upper) >= np.finfo(np.float32).max).any():
                raise ValueError('Unrepresentable original plate intervals')
            lo[mask] = np.nextafter(lower.astype(np.float32), np.float32(-np.inf))
            hi[mask] = np.nextafter(upper.astype(np.float32), np.float32(np.inf))
        full, remainder = divmod(end-first, 8); offset = first//8
        if full:
            low[offset:offset+full] = lo[:full*8].reshape(full, 8, 3).min(axis=1)
            high[offset:offset+full] = hi[:full*8].reshape(full, 8, 3).max(axis=1)
        if remainder:
            low[offset+full] = lo[full*8:].min(axis=0); high[offset+full] = hi[full*8:].max(axis=0)
    nodes, depth = _tree(low, high, triangle_count, 8, cancel)
    _cancel(cancel)
    for array in (positions, records, models, nodes): array.flags.writeable = False
    return PreparedPlates(positions, records, models, nodes, tuple(sources),
        (generation, tuple(certificates)), depth, source_bytes, owned_bytes, gpu_bytes, peak)
