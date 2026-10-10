"""Bounded CPU preparation of complete native path sources for reflections.

This is not a graphics owner. The caller retains an immutable LayerData
publication until every reader retires. Array identity is a layout certificate,
not a content hash; in-place mutation requires a new source generation.
"""
from dataclasses import dataclass
import math

import numpy as np

from .ToolheadCaptureValues import CaptureMesh, BufferLease


CHUNK = 8192
MAX_ID = (1 << 24) - 1
_EPS = np.finfo(np.float32).eps
_SCRATCH = 16 * 1024**2
_WIDTHS = {'float': 1, 'vector2f': 2, 'vector3f': 3, 'vector4f': 4}
_FIELDS = (('a_vertex', 3), ('a_line_dim', 2), ('a_line_type', 1),
           ('a_prev_line_type', 1), ('a_extruder', 1), ('a_feedrate', 1),
           ('a_color', 4), ('a_material_color', 4))


@dataclass(frozen=True, eq=False)
class PathInputs:
    """CPU publication/decoder certificate, NOT a graphics read lease.

    The capture owner must separately retain its actual VBO and original EBO
    owners, context epoch and ready fence until every reader retires. Borrowed
    arrays, buffer names and generation tokens cannot establish GPU readiness.
    """
    mesh: CaptureMesh
    vertex: BufferLease
    indices: np.ndarray
    arrays: tuple
    fields: tuple
    certificate: tuple
    scalar_count: int


@dataclass(frozen=True, eq=False)
class PathPrefix:
    first: int
    history: int
    completed: int
    partial: tuple | None
    aliases: np.ndarray
    certificate: tuple


@dataclass(frozen=True, eq=False)
class PreparedPaths:
    nodes: np.ndarray
    low: np.ndarray
    high: np.ndarray
    sources: tuple
    model: np.ndarray
    certificate: tuple
    primitives: int
    group: int
    max_depth: int
    retained_bytes: int
    borrowed_bytes: int
    gpu_bytes: int
    peak_bytes: int


def _cancel(cancel):
    if cancel is not None and cancel():
        raise RuntimeError('Cancelled environment path preparation')


def _generation(value, depth=0, remaining=None):
    remaining = [64] if remaining is None else remaining
    remaining[0] -= 1
    if depth > 8 or remaining[0] < 0:
        raise ValueError('Source generation exceeds bounded certificate')
    if type(value) in (int, str, bytes):
        if type(value) is int and value.bit_length() > 64 or type(value) in (str, bytes) and len(value) > 256:
            raise ValueError('Source generation exceeds bounded certificate')
        return value
    if type(value) is tuple:
        if len(value) > 8:
            raise ValueError('Source generation exceeds bounded certificate')
        return tuple(_generation(part, depth+1, remaining) for part in value)
    raise ValueError('Immutable source generation required')


def _layout(array, dtype, columns):
    if (type(array) is not np.ndarray or array.dtype != dtype or array.ndim != 2
            or array.shape[1] != columns or not array.flags.c_contiguous or not array.flags.aligned):
        raise ValueError('Unsupported native path array layout')
    owner = array
    while type(owner.base) is np.ndarray:
        owner = owner.base
    if owner.flags.owndata:
        start, size = owner.__array_interface__['data'][0], owner.nbytes
    else:
        try:
            allocation = memoryview(owner.base)
            if not allocation.contiguous:
                raise ValueError('Noncontiguous source allocation')
            byte_view = np.frombuffer(allocation, dtype=np.uint8)
            start, size = byte_view.__array_interface__['data'][0], byte_view.nbytes
        except (TypeError, BufferError) as error:
            raise ValueError('Uncertified native source allocation') from error
    pointer = array.__array_interface__['data'][0]
    if pointer < start or pointer+array.nbytes > start+size:
        raise ValueError('Native array exceeds readable allocation')
    return (array.__array_interface__['data'][0], array.shape, array.strides, array.dtype.str)


def source_certificate(positions, dimensions, indices, model, source_generation):
    """Capture all decoder layouts and exact model bytes without source copies."""
    layouts = (_layout(positions, np.dtype('float32'), 3),
               _layout(dimensions, np.dtype('float32'), 2),
               _layout(indices, np.dtype('uint32'), 2))
    if len(dimensions) != len(positions):
        raise ValueError('Dimensions must match all native vertices')
    if (type(model) is not np.ndarray or model.dtype != np.float32 or model.shape != (4, 4)
            or not model.flags.c_contiguous or not model.flags.aligned):
        raise ValueError('Exact finite FLOAT32 affine model required')
    _layout(model, np.dtype('float32'), 4)
    model_bytes = model.tobytes()
    frozen = np.frombuffer(model_bytes, dtype=np.float32).reshape(4, 4)
    if not np.isfinite(frozen).all() or not np.array_equal(frozen[3], (0, 0, 0, 1)):
        raise ValueError('Exact finite FLOAT32 affine model required')
    return layouts, model_bytes, _generation(source_generation)


def admit_path_inputs(mesh, vertex, source_generation, *, cancel=None):
    """Certify the actual planar upload and full original index publication.

    No packing, numeric conversion or guessed generic attribute values. Missing
    consumed fields explicitly refuse. Native signed32 indices retain their
    exact unsigned GPU bytes; negative/outside indices fail before any gather.
    Caller guarantees this immutable source generation matches the leased VBO.
    """
    _cancel(cancel)
    if type(mesh) is not CaptureMesh or type(vertex) is not BufferLease:
        raise ValueError('Frozen original path publication required')
    if (type(mesh.vertices) is not np.ndarray or mesh.vertices.ndim != 2
            or type(vertex.name) is not int or not 0 < vertex.name <= 0xffffffff
            or type(vertex.size) is not int or vertex.size < 0):
        raise ValueError('Original path buffer descriptor required')
    count = len(mesh.vertices)
    if type(mesh.identity) is not int or mesh.identity <= 0:
        raise ValueError('Original path publication identity required')
    if (type(mesh.attributes) is not tuple or len(mesh.attributes) > 64
            or any(type(entry) is not tuple or len(entry) != 4
                or any(type(value) is not str or not value or len(value) > 256 for value in entry[:3])
                for entry in mesh.attributes)):
        raise ValueError('Bounded plain path attribute metadata required')
    channels = [('a_vertex', 'vector3f', mesh.vertices),
                ('a_normal', 'vector3f', mesh.normals), ('a_color', 'vector4f', mesh.colours),
                ('a_uvs', 'vector2f', mesh.uvs)]
    channels += [(name, kind, array) for _key, name, kind, array in mesh.attributes]
    arrays, fields, layouts, offset = [], [], [], 0
    for name, kind, array in channels:
        if array is None:
            continue
        width = _WIDTHS.get(kind)
        if (not isinstance(name, str) or not name or width is None or type(array) is not np.ndarray
                or array.dtype != np.float32 or (array.shape != (count, width) and not (width == 1 and array.shape == (count,)))
                or not array.flags.c_contiguous or not array.flags.aligned):
            raise ValueError('Unsupported original path channel')
        if name in {entry[0] for entry in fields}:
            raise ValueError('Duplicate original path shader semantic')
        layout = _layout(array.reshape(count, width), np.dtype('float32'), width)
        arrays.append((name, array)); fields.append((name, offset, width))
        layouts.append((name, kind, offset*4, layout))
        offset += count*width
    by_name = dict(arrays)
    delivered = {name: width for name, _offset, width in fields}
    if any(delivered.get(name) != width for name, width in _FIELDS):
        raise ValueError('Missing or unsupported consumed path channel')
    # Readable spans are certified BEFORE the existing size-only lease check.
    if offset > 0x7fffffff:
        raise ValueError('Path scalar offsets exceed signed GLSL addressing')
    vertex.validate(mesh)
    original = mesh.indices
    if (type(original) is not np.ndarray or original.dtype not in (np.dtype('int32'), np.dtype('uint32'))
            or original.ndim not in (1, 2) or original.ndim == 2 and original.shape[1] != 2
            or original.size % 2 or not original.flags.c_contiguous or not original.flags.aligned):
        raise ValueError('Complete original line EBO publication required')
    indices = original.view(np.uint32).reshape(-1, 2)
    index_layout = _layout(indices, np.dtype('uint32'), 2)
    if len(indices) > MAX_ID:
        raise ValueError('Path line IDs exceed exact FLOAT32 addressing')
    generation = _generation(source_generation)
    for _name, array in arrays:
        for start in range(0, count, CHUNK):
            _cancel(cancel)
            part = array[start:start+CHUNK]
            if not np.isfinite(part).all():
                raise ValueError('Nonfinite original path channel')
    dimensions = by_name['a_line_dim']
    for start in range(0, count, CHUNK):
        _cancel(cancel)
        if (dimensions[start:start+CHUNK] < 0).any():
            raise ValueError('Negative original path dimensions')
        for name, maximum in (('a_line_type', 13), ('a_prev_line_type', 13), ('a_extruder', 15)):
            part = by_name[name][start:start+CHUNK]
            if ((part < 0).any() or (part > maximum).any() or (part != np.floor(part)).any()):
                raise ValueError('Unsafe original path scalar index')
    for start in range(0, len(indices), CHUNK):
        _cancel(cancel)
        if (indices[start:start+CHUNK] >= count).any():
            raise ValueError('Original path EBO index outside source')
    _cancel(cancel)
    # This new view is read-only; the native host publication's flag is intact.
    indices.flags.writeable = False
    certificate = (mesh.identity, generation, vertex.name, vertex.size, tuple(layouts), index_layout)
    return PathInputs(mesh, vertex, indices, tuple(arrays), tuple(fields), certificate, offset)


def freeze_path_prefix(inputs, first_element, history_element, completed_element, partial=None, *, cancel=None):
    """Convert native ELEMENT bounds and retain all global moved aliases.

    Native VS clips every raw endpoint exactly equal to next, including other
    lines sharing that coordinate. History never clips. Source IDs and equality
    stay local FLOAT32, before half-height, model and mix. No category filtering
    may remove an alias from this certificate.
    """
    _cancel(cancel)
    elements = (first_element, history_element, completed_element)
    if (any(type(value) is not int or value < 0 or value % 2 for value in elements)
            or first_element > completed_element or history_element > completed_element
            or completed_element > inputs.indices.size):
        raise ValueError('Exact original path element boundaries required')
    first, history, completed = (value//2 for value in elements)
    frozen, ids = None, []
    if partial is not None:
        if type(partial) not in (tuple, list) or len(partial) != 3:
            raise ValueError('Finite native partial-path values required')
        # Preflight BEFORE conversion: arbitrary array-shaped state must not
        # allocate a print-sized FLOAT32 copy merely to fail the shape check.
        points = partial[:2]
        for point in points:
            if type(point) is np.ndarray:
                valid = (point.shape == (3,) and point.dtype.kind in 'fiu'
                         and point.flags.c_contiguous and point.flags.aligned)
                if valid:
                    _layout(point.reshape(1, 3), point.dtype, 3)
            elif type(point) in (tuple, list):
                valid = len(point) == 3 and all(type(value) in (int, float)
                    or isinstance(value, (np.integer, np.floating)) for value in point)
            else:
                valid = False
            if not valid:
                raise ValueError('Finite native partial-path values required')
        if not (type(partial[2]) in (int, float) or isinstance(partial[2], (np.integer, np.floating))):
            raise ValueError('Finite native partial-path values required')
        with np.errstate(over='ignore', invalid='ignore'):
            last, next_point = (np.asarray(point, dtype=np.float32) for point in partial[:2])
            ratio = np.float32(partial[2])
        if (last.shape != (3,) or next_point.shape != (3,) or not np.isfinite(last).all()
                or not np.isfinite(next_point).all() or np.ndim(ratio) != 0 or not np.isfinite(ratio)
                or ratio < 0 or ratio > 1):
            raise ValueError('Finite native partial-path values required')
        frozen = (tuple(map(float, last)), tuple(map(float, next_point)), float(ratio))
        # Only these frozen values, rather than caller-owned partial arrays,
        # participate in discovery and subsequent decoder uniforms.
        next_point = np.asarray(frozen[1], dtype=np.float32)
        positions = dict(inputs.arrays)['a_vertex']
        for start in range(max(first, history), completed, CHUNK):
            _cancel(cancel)
            points = positions[inputs.indices[start:min(start+CHUNK, completed)]]
            matches = np.all(points == next_point, axis=2).any(axis=1)
            selected = np.flatnonzero(matches)
            if len(ids)+len(selected) > 32:
                raise ValueError('Complete moved-path aliases exceed query budget')
            ids.extend((selected+start).tolist())
    _cancel(cancel)
    aliases = np.asarray(ids, dtype=np.uint32)
    aliases.flags.writeable = False
    certificate = inputs.certificate, first, history, completed, frozen
    return PathPrefix(first, history, completed, frozen, aliases, certificate)


def certify_path_light(inputs, prefix, top_element, *, enabled, show_starts, cancel=None):
    """Certify that additive geometry can reuse ALL base-history hits.

    The native additive pass uses the current vertex/geometry shaders even for
    grey history. That can move historical endpoints which the base shadow
    shader never clips, or add historical start markers absent from the base.
    Either case requires a separate lighting-geometry query; it cannot be
    inferred from the winning base line. Refuse the WHOLE cohort before use.
    Sources must remain the same immutable publication as their read lease.
    """
    _cancel(cancel)
    partial = prefix.partial if type(prefix) is PathPrefix else None
    if partial is not None:
        # Only freeze_path_prefix's canonical six scalars and F32 ratio are
        # admitted. A self-consistent forged certificate is not a shape or
        # readable-allocation certificate and must not trigger a large copy.
        if (type(partial) is not tuple or len(partial) != 3
                or any(type(point) is not tuple or len(point) != 3
                    or any(type(value) is not float or not math.isfinite(value)
                        or abs(value) > np.finfo(np.float32).max for value in point)
                    for point in partial[:2])
                or type(partial[2]) is not float or not math.isfinite(partial[2])
                or not 0. <= partial[2] <= 1.):
            raise ValueError('Canonical bounded frozen partial light state required')
    if (type(inputs) is not PathInputs or type(prefix) is not PathPrefix
            or prefix.certificate != (inputs.certificate, prefix.first,
                prefix.history, prefix.completed, prefix.partial)
            or any(type(value) is not int for value in (prefix.first, prefix.history, prefix.completed))
            or min(prefix.first, prefix.history) < 0 or prefix.first > prefix.completed
            or prefix.history > prefix.completed or prefix.completed > len(inputs.indices)
            or type(top_element) is not int or top_element % 2
            or not prefix.first*2 <= top_element <= prefix.completed*2
            or type(enabled) is not bool or type(show_starts) is not bool):
        raise ValueError('Matching bounded native light geometry required')
    certificate = prefix.certificate, top_element, enabled, show_starts
    if not enabled:
        return certificate
    arrays = dict(inputs.arrays)
    historical_end = min(prefix.history, prefix.completed)
    moved = prefix.partial is not None and prefix.partial[2] < 1.
    next_point = np.asarray(prefix.partial[1], dtype=np.float32) if moved else None
    for start in range(prefix.first, historical_end, CHUNK):
        _cancel(cancel)
        original = inputs.indices[start:min(start+CHUNK, historical_end)]
        if moved and np.all(arrays['a_vertex'][original] == next_point, axis=2).any():
            raise ValueError('Historical partial lighting requires separate geometry')
        if show_starts:
            # Current GS markers use FIRST endpoint categories; END palette
            # colours and draw visibility cannot certify that no marker exists.
            first = original[:, 0]
            kind, previous = arrays['a_line_type'][first], arrays['a_prev_line_type'][first]
            markers = ((kind == 1) & (previous != 1)) | ((kind == 4) & (previous != 4))
            admitted = (np.arange(start, start+len(first)) >= top_element//2) | (kind == 1)
            if (markers & admitted).any():
                raise ValueError('Historical lighting starts require separate geometry')
    _cancel(cancel)
    return certificate


def memory_plan(count, group=8, retained_bytes=0):
    """Combined owned CPU build, new GPU upload and previous-owner admission.

    Sources are borrowed, counted separately and never charged as new copies.
    256 bytes/group covers bounds, nodes, Morton/sort overlaps and topology
    frontiers. A fixed 16 MiB covers bounded gather/transform validation scratch.
    The new GPU upload and old owners are reserved even during CPU preparation.
    """
    if (type(count) is not int or not 0 <= count <= MAX_ID or type(group) is not int
            or not 1 <= group <= 32 or type(retained_bytes) is not int or retained_bytes < 0):
        raise ValueError('Unsupported path count, grouping or retained storage')
    groups = (count + group - 1) // group
    nodes = max(0, 2 * groups - 1)
    if nodes > MAX_ID:
        raise ValueError('Path node IDs exceed exact FLOAT32 storage')
    gpu = 32 * nodes
    owned = 24 * groups + gpu + 64  # Frozen model, including empty sources.
    peak = retained_bytes + gpu + 256 * groups + 64 + _SCRATCH
    return groups, owned, gpu, peak


def _validate_sources(positions, dimensions, indices, cancel):
    for first in range(0, len(positions), CHUNK):
        _cancel(cancel)
        p, d = positions[first:first+CHUNK], dimensions[first:first+CHUNK]
        if not np.isfinite(p).all() or not np.isfinite(d).all() or (d < 0).any():
            raise ValueError('Nonfinite native vertex or invalid dimensions')
    for first in range(0, len(indices), CHUNK):
        _cancel(cancel)
        if (indices[first:first+CHUNK] >= len(positions)).any():
            raise ValueError('Native path index outside retained allocation')


def _world(points, model):
    # Float64 interval centre avoids overflowing intermediate host arithmetic.
    # Margin below covers the original FLOAT32 decoder, including FMA choices.
    result = np.empty(points.shape, dtype=np.float64)
    magnitude = np.empty_like(result)
    for axis in range(3):
        terms = points * model[axis, :3]
        result[..., axis] = terms[..., 0] + terms[..., 1] + terms[..., 2] + model[axis, 3]
        magnitude[..., axis] = np.abs(terms).sum(axis=-1) + abs(float(model[axis, 3]))
    return result, magnitude * (16 * _EPS)


def _boxes(positions, dimensions, indices, model):
    raw = positions[indices].astype(np.float64)
    dims = dimensions[indices].astype(np.float64)
    shifted = raw.copy()
    shifted[..., 1] -= dims[..., 1] * .5
    world, error = _world(shifted, model)
    raw_world, raw_error = _world(raw, model)
    # Original FLOAT32 local half-height subtraction also rounds before model.
    error += ((np.abs(raw[..., 1]) + dims[..., 1] * .5) * (4 * _EPS))[..., None] * np.abs(model[:3, 1])
    delta = world[:, 1] - world[:, 0]
    uncertainty = error.sum(axis=1)
    length = np.sqrt(np.square(delta).sum(axis=1))
    uncertain = np.sqrt(np.square(uncertainty).sum(axis=1))
    if (not np.isfinite(length).all() or (length <= uncertain + 1e-6).any()
            or (np.square(delta).sum(axis=1) > np.finfo(np.float32).max).any()):
        raise ValueError('Uncertified native path normalization')
    horizontal = ((raw[:, 0, 1] == raw[:, 1, 1]) & (dims[:, 0, 1] == dims[:, 1, 1])
                  & (model[1, 0] == 0) & (model[1, 2] == 0))
    vertical = ((raw[:, 0, 0] == raw[:, 1, 0]) & (raw[:, 0, 2] == raw[:, 1, 2])
                & (model[0, 1] == 0) & (model[2, 1] == 0))
    # The general shader branch normalizes cross(delta, delta.xz). Even a
    # perfectly safe delta length can leave that cross product underflowed.
    # Structural horizontal/vertical cases use the original safe branches.
    general = ~(horizontal | vertical)
    xz = np.sqrt(np.square(delta[:, (0, 2)]).sum(axis=1))
    xz_error = np.sqrt(np.square(uncertainty[:, (0, 2)]).sum(axis=1))
    radial_min = np.maximum(0, np.abs(delta[:, 1])-uncertainty[:, 1]) * np.maximum(0, xz-xz_error)
    radial_max = (np.abs(delta[:, 1])+uncertainty[:, 1]) * (xz+xz_error)
    if ((general & (radial_min <= 1e-6 + radial_max * (16*_EPS))).any()
            or (general & (radial_max > math.sqrt(np.finfo(np.float32).max))).any()):
        raise ValueError('Uncertified native radial normalization')
    width = np.maximum(.05, dims[:, 1, 0] * .5 + .01)
    height = np.maximum(.05, dims[:, 1, 1] * .5 + .01)
    if (np.maximum(width, height) > math.sqrt(np.finfo(np.float32).max)/2).any():
        raise ValueError('Uncertified native offset normalization')
    radius = 2 * np.maximum(width, height) * 1.00001
    extent = np.repeat(radius[:, None], 3, axis=1)
    # This certificate follows native/ray/path_bounds.h. No near-horizontal
    # epsilon, transformed thickness or omission of travel/start geometry.
    extent[horizontal, 0] = width[horizontal] * 1.00001
    extent[horizontal, 2] = width[horizontal] * 1.00001
    extent[horizontal, 1] = height[horizontal] * 1.00001
    low = np.minimum((world-error).min(axis=1)-extent, (raw_world-raw_error).min(axis=1))
    high = np.maximum((world+error).max(axis=1)+extent, (raw_world+raw_error).max(axis=1))
    # Outward conversion, including subnormal boundaries. Nonfinite output is
    # a whole-source refusal, never a dropped primitive or clamped geometry.
    limit = np.finfo(np.float32).max
    if (not np.isfinite(low).all() or not np.isfinite(high).all()
            or (np.abs(low) >= limit).any() or (np.abs(high) >= limit).any()):
        raise ValueError('Unrepresentable native path bounds')
    low = np.nextafter(low.astype(np.float32), np.float32(-np.inf))
    high = np.nextafter(high.astype(np.float32), np.float32(np.inf))
    if not np.isfinite(low).all() or not np.isfinite(high).all():
        raise ValueError('Overflowed native path bounds')
    return low, high


def _spread(values):
    values = values.copy()
    for shift, mask in ((16, 0x030000ff), (8, 0x0300f00f), (4, 0x030c30c3), (2, 0x09249249)):
        values = (values | (values << shift)) & mask
    return values


def _tree(low, high, count, group, cancel):
    groups = len(low)
    if not groups:
        return np.empty((0, 8), np.float32), 0
    _cancel(cancel)
    centre = (low.astype(np.float64) + high.astype(np.float64)) * .5
    minimum = centre.min(axis=0)
    span = np.maximum(centre.max(axis=0) - minimum, 1e-30)
    keys = np.zeros(groups, np.uint32)
    for axis in range(3):
        _cancel(cancel)
        scaled = np.clip((centre[:, axis]-minimum[axis]) / span[axis] * 1023, 0, 1023).astype(np.uint32)
        keys |= _spread(scaled) << axis
    del centre, scaled
    sort = np.argsort(keys, kind='stable')
    _cancel(cancel)  # In particular, before allocating nodes after argsort.
    order = sort.astype(np.uint32)
    del sort, keys
    nodes = np.empty((2*groups-1, 8), np.float32)
    leaf_start = groups-1
    nodes[leaf_start:, :3] = low[order]
    nodes[leaf_start:, 4:7] = high[order]
    nodes[leaf_start:, 3] = order * group
    nodes[leaf_start:, 7] = -np.minimum(group, count-order.astype(np.int64)*group)
    del order
    level = np.arange(leaf_start, len(nodes), dtype=np.uint32)
    allocated, depth = 0, 1
    while len(level) > 1:
        _cancel(cancel)
        pairs = len(level)//2
        left, right = level[:pairs*2:2], level[1:pairs*2:2]
        parent = np.arange(allocated, allocated+pairs, dtype=np.uint32)
        nodes[parent, :3] = np.minimum(nodes[left, :3], nodes[right, :3])
        nodes[parent, 4:7] = np.maximum(nodes[left, 4:7], nodes[right, 4:7])
        nodes[parent, 3], nodes[parent, 7] = left, right
        allocated += pairs
        level = np.concatenate((parent, level[-1:])) if len(level) % 2 else parent
        depth += 1
    root = int(level[0])
    if allocated != leaf_start or depth > math.ceil(math.log2(groups))+1:
        raise ValueError('Incomplete environment path topology')
    if root:
        nodes[[0, root]] = nodes[[root, 0]]
        for first in range(0, leaf_start, CHUNK):
            _cancel(cancel)
            for column in (3, 7):
                child = nodes[first:min(leaf_start, first+CHUNK), column]
                zero, moved = child == 0, child == root
                child[zero], child[moved] = root, 0
    # Construction gives every original group exactly once, joins each subtree
    # once, and never adds an uninitialized parent. Check delivered intervals
    # and parent containment in bounded slices before publication.
    for first in range(0, len(nodes), CHUNK):
        _cancel(cancel)
        part = nodes[first:first+CHUNK]
        if not np.isfinite(part).all() or (part[:, :3] > part[:, 4:7]).any():
            raise ValueError('Invalid delivered path intervals')
        internal = part[part[:, 7] >= 0]
        for column in (3, 7):
            child = nodes[internal[:, column].astype(np.uint32)]
            if (child[:, :3] < internal[:, :3]).any() or (child[:, 4:7] > internal[:, 4:7]).any():
                raise ValueError('Path parent does not contain child')
    return nodes, depth


def prepare_path_source(positions, dimensions, indices, model_f32, source_generation, *,
                        group=8, byte_budget=256*1024**2, retained_bytes=0, cancel=None):
    """Prepare one complete publication; cancellation/refusal publishes nothing."""
    _cancel(cancel)
    certificate = source_certificate(positions, dimensions, indices, model_f32, source_generation)
    groups, owned, gpu, peak = memory_plan(len(indices), group, retained_bytes)
    if type(byte_budget) is not int or byte_budget < 0 or peak > byte_budget:
        raise MemoryError('Environment path preparation exceeds combined memory budget')
    # Decode the bytes already certified, rather than re-reading a caller's
    # mutable model between certificate construction and the worker snapshot.
    model = np.frombuffer(certificate[1], dtype=np.float32).reshape(4, 4)
    _validate_sources(positions, dimensions, indices, cancel)
    low, high = np.empty((groups, 3), np.float32), np.empty((groups, 3), np.float32)
    step = CHUNK//group*group
    for first in range(0, len(indices), step):
        _cancel(cancel)
        end = min(len(indices), first+step)
        lo, hi = _boxes(positions, dimensions, indices[first:end], model)
        full, remainder = divmod(end-first, group)
        offset = first//group
        if full:
            low[offset:offset+full] = lo[:full*group].reshape(full, group, 3).min(axis=1)
            high[offset:offset+full] = hi[:full*group].reshape(full, group, 3).max(axis=1)
        if remainder:
            low[offset+full] = lo[full*group:].min(axis=0)
            high[offset+full] = hi[full*group:].max(axis=0)
        del lo, hi
    nodes, depth = _tree(low, high, len(indices), group, cancel)
    _cancel(cancel)
    for output in (nodes, low, high, model):
        output.flags.writeable = False
    return PreparedPaths(nodes, low, high, (positions, dimensions, indices), model, certificate,
                         len(indices), group, depth, owned,
                         positions.nbytes+dimensions.nbytes+indices.nbytes, gpu, peak)
