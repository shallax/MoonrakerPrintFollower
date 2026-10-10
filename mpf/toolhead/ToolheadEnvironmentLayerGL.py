"""Unwired bounded completed-layer TBO owner, exact creating context only.

A LayerImage is a plain completed value, not a GL lease. The caller also holds
the matching cubemap/source read ticket through every fallback/lookup draw.
"""
import ctypes
from contextlib import contextmanager
from itertools import count
from weakref import ref

import numpy as np

from .ToolheadCaptureValues import retained_array_bytes
from .ToolheadEnvironmentLayers import LayerImage, METADATA_RESERVE
from .ToolheadEnvironmentPaths import MAX_ID, _generation
from .ToolheadEnvironmentGeometry import _bindings, GeometryUncertain
from .ToolheadGLState import procedure, sample_depth_certificate

U, I, P = ctypes.c_uint, ctypes.c_int, ctypes.c_void_p
TARGET = 0x8C2A
UNITS = (8, 9, 10)
UPLOAD_BYTES = 1024*1024
VALIDATION_SCRATCH = 16*1024**2
_requests = count(1)
_uncertain = []


def _mesh_certificate(mesh, count_):
    """Original complete RenderBatch triangles and publication descriptors."""
    vertices = mesh.getVertices()
    if (not isinstance(vertices, np.ndarray) or vertices.ndim != 2 or vertices.shape[1] != 3
            or mesh.getVertexCount() != len(vertices)):
        raise ValueError('Original triangle vertex publication required')
    indices = mesh.getIndices() if mesh.hasIndices() else None
    if indices is not None:
        if (not isinstance(indices, np.ndarray) or indices.ndim != 2 or indices.shape[1] != 3
                or indices.dtype not in (np.dtype('int32'), np.dtype('uint32'))
                or mesh.getFaceCount() != len(indices) or count_ != len(indices)):
            raise ValueError('Complete original indexed triangle count required')
    elif len(vertices) % 3 or count_ != len(vertices)//3:
        raise ValueError('Complete original triangle count required')
    attributes = tuple(mesh.attributeNames())
    if len(attributes) > 32: raise ValueError('Bounded original vertex attributes required')
    arrays = (vertices, indices, mesh.getNormals() if mesh.hasNormals() else None,
        getattr(getattr(mesh, 'source', mesh), '_colors', None) if mesh.hasColors() else None,
        mesh.getUVCoordinates() if mesh.hasUVCoordinates() else None,
        *(mesh.getAttribute(name)['value'] for name in attributes))
    if mesh.hasColors() and arrays[3] is None:
        raise ValueError('Original colour array publication required')
    descriptions = []
    for array in arrays:
        if array is None: descriptions.append(None); continue
        if not isinstance(array, np.ndarray): raise ValueError('Original array publication required')
        descriptions.append((id(array), array.ctypes.data, array.shape, array.strides,
                             array.dtype.str, array.flags.writeable))
    return tuple(descriptions), tuple((name, mesh.getAttribute(name)['opengl_name'],
                                      mesh.getAttribute(name)['opengl_type']) for name in attributes)


class LayerDraw:
    """Borrow a completed layer owner through original mesh draws.

    The frame coordinator supplies the currently selected storage request and
    exact frozen mesh ranges. This adapter neither selects a view nor owns the
    shader cache. Its programs must be the query-free original PBR variants.
    """
    def __init__(self, storage, selected_key, epoch, programs, draws, *, fallback_scope, enabled=True):
        if (type(storage) is not LayerStorage or type(epoch) is not int
                or type(programs) is not tuple or len(programs) != 2
                or type(draws) is not tuple or not 0 < len(draws) <= 256
                or not callable(fallback_scope) or type(enabled) is not bool):
            raise ValueError('Completed original draw adapter required')
        # Up to32 attributes per draw retain Python descriptors/identities.
        # Source arrays and shader programs stay in the caller's frozen ledger.
        metadata = METADATA_RESERVE+32768*len(draws)
        if storage.peak_bytes+metadata > storage.byte_budget:
            raise MemoryError('Combined original layer draw metadata budget exceeded')
        end = 0; seen = set(); certificates = []
        for mesh, base, count_ in draws:
            if (mesh is None or id(mesh) in seen or type(base) is not int or type(count_) is not int
                    or base < end or count_ <= 0 or base > MAX_ID+1-count_):
                raise ValueError('Unique frozen meshes and original primitive ranges required')
            seen.add(id(mesh)); end = base+count_
            certificates.append(_mesh_certificate(mesh, count_))
        self.storage, self.selected_key, self.epoch = storage, selected_key, epoch
        self.programs, self.draws, self.fallback_scope = programs, draws, fallback_scope
        self.certificates = tuple(certificates)
        self.retained_bytes = metadata
        self.enabled = enabled; self._entered = self._admitted = False
        self._plane=0

    def planes(self): return range(self.storage.image.samples)

    def select_plane(self, plane):
        if not self._entered or type(plane) is not int or not 0<=plane<self.storage.image.samples:
            raise ValueError('Admitted original colour plane required')
        self._plane=plane

    def shader(self, single_pass):
        if type(single_pass) is not bool: raise ValueError('Original shader family required')
        return self.programs[int(single_pass)]

    @contextmanager
    def read(self):
        if self._entered: raise RuntimeError('Original layer draw is already admitted')
        self.storage._current()
        self._entered = True
        try:
            if self.storage.image.samples==4:
                gl=self.storage.gl
                if (int(gl.glGetIntegerv(0x80A9))!=4 or not gl.glIsEnabled(0x809D)
                        or any(gl.glIsEnabled(flag) for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36))):
                    raise RuntimeError('Original four-plane coverage controls required')
                sample_depth_certificate(gl,self.storage.context,self.storage.image.width,self.storage.image.height)
                query=procedure(self.storage.context,'glGetMultisamplefv',None,U,U,ctypes.POINTER(ctypes.c_float))
                positions=[]
                for plane in range(4):
                    point=(ctypes.c_float*2)(); query(0x8E50,plane,point); positions.append(tuple(point))
                if tuple(positions)!=self.storage.image.positions:
                    raise RuntimeError('Original output sample positions differ from the receiver')
            with self.storage.read(self.selected_key, self.epoch, fallback_scope=self.fallback_scope) as admitted:
                self._admitted = admitted is self.storage
                yield self
        finally: self._entered = self._admitted = False; self._plane=0

    def apply(self, shader, mesh):
        if not self._entered: raise RuntimeError('Original layer draw requires its read scope')
        self.storage._current()
        if (not any(shader is candidate for candidate in self.programs)
                or int(self.storage.gl.glGetIntegerv(0x8B8D)) != shader._shader_program.programId()):
            raise ValueError('Bound original layer program required')
        matches = [(base, count, certificate) for (original, base, count), certificate
                   in zip(self.draws, self.certificates, strict=True) if original is mesh]
        if len(matches) != 1: raise ValueError('Original layer mesh was not frozen in this frame')
        program = shader._shader_program
        program.setUniformValue('mpf_layerLookupEnabled', 0)
        base, count, certificate = matches[0]
        if _mesh_certificate(mesh, count) != certificate:
            raise ValueError('Original layer mesh publication changed')
        if self._admitted:
            self.storage.apply(program,origin=(0,0),base=base,count=count,plane=self._plane)
            if not self.enabled: program.setUniformValue('mpf_layerLookupEnabled', 0)


class LayerStorage:
    """Complete private ranges/ID/F32 colour buffers; no partial readiness.

    ``existing_bytes`` includes every other live owner, even withdrawn ones.
    Initial whole-buffer driver allocation is not bounded by upload chunk size.
    """
    def __init__(self, gl, context, image, *, epoch, fallback_key, existing_bytes, byte_budget):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtCore import Qt
        if (type(image) is not LayerImage or any(type(value) is not int for value in (
                epoch, existing_bytes, byte_budget, image.width, image.height,image.samples))
                or epoch <= 0 or existing_bytes < 0 or byte_budget < 0
                or not 0 < image.width <= 8192 or not 0 < image.height <= 8192 or image.samples not in (1,4)):
            raise ValueError('Exact completed layer value and bounded ledger required')
        if image.samples==4:
            positions=image.positions
            if (type(positions) is not tuple or len(positions)!=4
                    or any(type(point) is not tuple or len(point)!=2
                           or any(type(v) is not float or not np.isfinite(v) or not 0<=v<=1 for v in point)
                           for point in positions) or len(set(positions))!=4):
                raise ValueError('Completed original sample pattern required')
        elif image.positions is not None: raise ValueError('S1 values have no sample pattern')
        pixels, matches = image.width*image.height*image.samples, len(image.identities)
        key, fallback_key = _generation(image.key), _generation(fallback_key)
        if type(key) is not tuple or type(fallback_key) is not tuple:
            raise ValueError('Immutable image and fallback cohort keys required')
        arrays = image.ranges, image.identities, image.colours
        for array, dtype, shape in zip(arrays, (np.uint32, np.uint32, np.float32),
                ((pixels, 2), (matches,), (matches, 4)), strict=True):
            if (type(array) is not np.ndarray or array.dtype != dtype or array.shape != shape
                    or not array.flags.c_contiguous or not array.flags.aligned
                    or not array.flags.owndata or array.flags.writeable):
                raise ValueError('Owned readonly layer arrays required')
        cpu = sum(retained_array_bytes(array) for array in arrays)
        if image.retained_bytes != cpu or image.gpu_bytes != cpu:
            raise ValueError('Layer byte certificate differs from its arrays')
        gpu = pixels*8+max(4, matches*4)+max(16, matches*16)
        cpu += 20 if not matches else 0
        peak = existing_bytes+cpu+gpu+METADATA_RESERVE+VALIDATION_SCRATCH
        if peak > byte_budget: raise MemoryError('Combined layer upload budget exceeded')
        if (QOpenGLContext.currentContext() is not context or context.format().majorVersion() < 4
                or context.format().profile() != context.format().OpenGLContextProfile.CoreProfile):
            raise RuntimeError('Layer upload requires its exact creating core4 context')
        limit = min((1 << 31)-1, int(gl.glGetIntegerv(0x8C2B)))
        if (int(gl.glGetIntegerv(0x8872)) < 12 or int(gl.glGetIntegerv(0x8B4D)) < 12
                or pixels > limit or max(1, matches) > limit):
            raise MemoryError('Complete layer buffers exceed actual texture capability')
        self.gl, self.context, self.epoch = gl, context, epoch
        self.image, self.fallback_key = image, fallback_key
        self.key = key, epoch, next(_requests)
        self.retained_bytes, self.peak_bytes = cpu+gpu+METADATA_RESERVE, peak
        self.byte_budget = byte_budget
        self.buffers, self.textures = [], []
        self._arrays = (image.ranges, image.identities if matches else np.zeros(1, np.uint32),
                        image.colours if matches else np.zeros((1, 4), np.float32))
        for array in self._arrays: array.setflags(write=False)
        self._layouts = tuple((array.shape, array.dtype, array.ctypes.data, array.nbytes)
                              for array in self._arrays)
        self._index = self._offset = self._prefix = 0
        self._write = self._read = None
        self._superseded = []
        self._readers = self._submissions = 0
        self._bound_reads = 0
        self.closed = self.quarantined = self.withdrawn = False; self.ready_key = None
        self._retirement = None
        owner_ref = ref(self)
        def destroyed():
            owner = owner_ref()
            if owner is not None:
                try: owner.close()
                except Exception: pass  # close roots uncertain names and image.
        try:
            context.aboutToBeDestroyed.connect(destroyed, Qt.ConnectionType.DirectConnection)
            self._retirement = destroyed
            with self._submission(), _bindings(gl, context):
                for array, unit, format_, texels in zip(self._arrays, UNITS,
                        (0x823C, 0x8236, 0x8814), (pixels, max(1, matches), max(1, matches)), strict=True):
                    self._current()
                    value = U(); procedure(context, 'glGenBuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
                    if not value.value: raise RuntimeError('Layer buffer allocation failed')
                    self.buffers.append(value.value); gl.glBindBuffer(TARGET, value.value)
                    procedure(context, 'glBufferData', None, U, ctypes.c_ssize_t, P, U)(
                        TARGET, array.nbytes, None, 0x88E4)
                    actual = ctypes.c_int64()
                    procedure(context, 'glGetBufferParameteri64v', None, U, U, ctypes.POINTER(ctypes.c_int64))(
                        TARGET, 0x8764, ctypes.byref(actual))
                    if actual.value != array.nbytes: raise RuntimeError('Layer buffer storage differs')
                    texture = U(); procedure(context, 'glGenTextures', None, I, ctypes.POINTER(U))(1, ctypes.byref(texture))
                    if not texture.value: raise RuntimeError('Layer texture allocation failed')
                    self.textures.append(texture.value)
                    gl.glActiveTexture(0x84C0+unit); gl.glBindTexture(TARGET, texture.value)
                    procedure(context, 'glTexBuffer', None, U, U, U)(TARGET, format_, value.value)
                    query = procedure(context, 'glGetTexLevelParameteriv', None, U, I, U, ctypes.POINTER(I))
                    for parameter, expected in ((0x1000, texels), (0x1003, format_), (0x8C2D, value.value)):
                        result = I(); query(TARGET, 0, parameter, ctypes.byref(result))
                        if result.value != expected: raise RuntimeError('Layer texture descriptor differs')
                if gl.glGetError(): raise RuntimeError('Layer allocation failed')
                self._fence('_write')
        except Exception as error: self._quarantine(error)

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Layer storage requires its live creating context')

    def _quarantine(self, error):
        self.quarantined = self.withdrawn = True; self.ready_key = None
        if not any(owner is self for owner in _uncertain): _uncertain.append(self)
        raise GeometryUncertain(self, str(error)) from error

    @contextmanager
    def _submission(self):
        self._submissions += 1
        try: yield
        finally: self._submissions -= 1

    def _fence(self, field):
        self._current()
        previous = getattr(self, field)
        name = procedure(self.context, 'glFenceSync', P, U, U)(0x9117, 0)
        if not name: raise RuntimeError('Layer completion fence unavailable')
        if previous is not None: self._superseded.append(previous)
        setattr(self, field, int(name)); self.gl.glFlush()
        if self.gl.glGetError(): raise RuntimeError('Layer fence flush failed')
        if previous is not None:
            procedure(self.context, 'glDeleteSync', None, P)(P(previous))
            if self.gl.glGetError(): raise RuntimeError('Prior layer fence cleanup failed')
            self._superseded.remove(previous)

    def _poll(self, field):
        self._current(); name = getattr(self, field)
        if name is None: return True
        result = procedure(self.context, 'glClientWaitSync', U, P, U, ctypes.c_ulonglong)(P(name), 0, 0)
        if self.gl.glGetError(): raise RuntimeError('Layer fence wait failed')
        if result == 0x911B: return False
        if result not in (0x911A, 0x911C): raise RuntimeError('Layer GPU completion is uncertain')
        procedure(self.context, 'glDeleteSync', None, P)(P(name))
        if self.gl.glGetError(): raise RuntimeError('Layer completed fence cleanup failed')
        setattr(self, field, None); return True

    def _layout(self):
        for array, layout in zip(self._arrays, self._layouts, strict=True):
            if (array.flags.writeable or not array.flags.c_contiguous or not array.flags.aligned
                    or not array.flags.owndata
                    or (array.shape, array.dtype, array.ctypes.data, array.nbytes) != layout):
                raise ValueError('Layer publication layout changed')

    def _boundaries(self, indices):
        # A strided CSR starts column can make np.searchsorted copy the whole
        # image. This vector lower-bound uses only upload-chunk-sized scratch.
        low = np.zeros(indices.size, np.uint32)
        high = np.full(indices.size, len(self.image.ranges), np.uint32)
        while np.any(low < high):
            middle = low+(high-low)//2
            valid = middle < len(self.image.ranges)
            starts = self.image.ranges[np.minimum(middle, len(self.image.ranges)-1), 0]
            less = valid & (starts < indices)
            low = np.where(less, middle+1, low)
            high = np.where(less, high, middle)
        return (low < len(self.image.ranges)) & (self.image.ranges[
            np.minimum(low, len(self.image.ranges)-1), 0] == indices)

    def _validate(self, start, size):
        array = self._arrays[self._index]
        if self._index == 0:
            values = array[start//8:(start+size)//8].astype(np.uint64)
            counts = values[:, 1]; ends = self._prefix+np.cumsum(counts, dtype=np.uint64)
            if np.any(values[:, 0] != ends-counts) or np.any(ends > len(self.image.identities)):
                raise ValueError('Layer CSR coverage is not contiguous')
            prefix = int(ends[-1])
            if start+size == array.nbytes and prefix != len(self.image.identities):
                raise ValueError('Layer CSR does not cover every record')
            return prefix
        if self._index == 1 and self.image.identities.size:
            first, last = start//4, (start+size)//4
            ids = array[first:last]
            if np.any(ids > MAX_ID): raise ValueError('Layer identity exceeds exact source range')
            indices = np.arange(max(1, first), last, dtype=np.uint32)
            if np.any(~self._boundaries(indices) & (array[indices] <= array[indices-1])):
                raise ValueError('Layer identities are not strictly sorted per pixel')
        if self._index == 2:
            colours = array[start//16:(start+size)//16]
            if (not np.isfinite(colours).all()
                    or np.any(~((colours[:, 3] == 1.) | np.all(colours == 0., axis=1)))):
                raise ValueError('Layer radiance is incomplete')
        return self._prefix

    def step(self, selected_key):
        """One validated upload chunk and at most one pending producer fence."""
        self._current()
        if self._submissions or self._readers: return False
        if selected_key != self.key: self.withdrawn = True; self.ready_key = None
        if self.withdrawn: return False
        try:
            with self._submission():
                self._layout()
                if not self._poll('_write'): return False
                if self._index == 3: self.ready_key = self.key; return True
                array = self._arrays[self._index]
                size = min(UPLOAD_BYTES, array.nbytes-self._offset)
                with _bindings(self.gl, self.context):
                    prefix = self._validate(self._offset, size)
                    self.gl.glBindBuffer(TARGET, self.buffers[self._index])
                    procedure(self.context, 'glBufferSubData', None, U, ctypes.c_ssize_t, ctypes.c_ssize_t, P)(
                        TARGET, self._offset, size, P(array.ctypes.data+self._offset))
                    if self.gl.glGetError(): raise RuntimeError('Layer upload failed')
                    self._fence('_write')
                self._current()
                self._prefix = prefix; self._offset += size
                if self._offset == array.nbytes: self._index += 1; self._offset = 0
            return False
        except Exception as error: self._quarantine(error)

    @contextmanager
    def read(self, selected_key, epoch, *, fallback_scope):
        """Parent scope must retain the exact old-map fallback through drawing."""
        self._current()
        if (selected_key is None or self.ready_key is None or self.withdrawn
                or self.ready_key != selected_key or epoch != self.epoch):
            yield None; return
        if not callable(fallback_scope): raise ValueError('Matching fallback read scope required')
        self._readers += 1
        try:
            with fallback_scope() as fallback:
                self._current(); self._layout()
                if fallback is None or fallback.key != self.fallback_key:
                    yield None; return
                with _bindings(self.gl, self.context):
                    sampler = procedure(self.context, 'glBindSampler', None, U, U)
                    for unit, texture in zip(UNITS, self.textures, strict=True):
                        self.gl.glActiveTexture(0x84C0+unit); self.gl.glBindTexture(TARGET, texture); sampler(unit, 0)
                    self._bound_reads += 1
                    try: yield self
                    finally:
                        try: self._fence('_read')
                        finally: self._bound_reads -= 1
        except Exception as error: self._quarantine(error)
        finally: self._readers -= 1

    def apply(self, shader, *, origin, base, count, plane=0):
        """Caller has bound the real Qt program inside the admitted read."""
        self._current()
        if (not self._bound_reads or int(self.gl.glGetIntegerv(0x8B8D)) != shader.programId()
                or type(base) is not int or type(count) is not int or count <= 0
                or base < 0 or count > MAX_ID+1 or base > MAX_ID+1-count
                or type(plane) is not int or not 0<=plane<self.image.samples
                or type(origin) is not tuple or len(origin) != 2
                or any(type(value) is not int or not -(1 << 31) <= value < 1 << 31 for value in origin)):
            raise ValueError('Bound original primitive range and integer crop required')
        shader.setUniformValue('mpf_layerLookupEnabled', 0)
        for name, value in dict(mpf_layerRanges=8, mpf_layerIdentities=9, mpf_layerColours=10,
                mpf_layerPixelCount=self.image.width*self.image.height*self.image.samples,
                mpf_layerSampleCount=self.image.samples,mpf_layerLookupPlane=plane,
                mpf_layerRecordCount=len(self.image.identities), mpf_layerDrawBase=base,
                mpf_layerDrawCount=count).items(): shader.setUniformValue(name, value)
        uniform = procedure(self.context, 'glUniform2i', None, I, I, I)
        uniform(shader.uniformLocation('mpf_layerLookupOrigin'), *origin)
        uniform(shader.uniformLocation('mpf_layerLookupSize'), self.image.width, self.image.height)
        if self.gl.glGetError(): raise RuntimeError('Layer lookup delivery failed')
        shader.setUniformValue('mpf_layerLookupEnabled', int(bool(len(self.image.identities))))

    def close(self):
        if self.closed: return
        try:
            self._current()
            if self._readers or self._submissions: raise RuntimeError('Layer storage still has admitted work')
            self.withdrawn = True; self.ready_key = None
            with self._submission():
                self.gl.glFinish()
                if self.gl.glGetError() or not self._poll('_write') or not self._poll('_read'):
                    raise RuntimeError('Layer teardown GPU completion is uncertain')
                with _bindings(self.gl, self.context):
                    for names, function in ((self.textures, 'glDeleteTextures'), (self.buffers, 'glDeleteBuffers')):
                        while names:
                            value = U(names[-1]); procedure(self.context, function, None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
                            if self.gl.glGetError(): raise RuntimeError('Layer resource cleanup failed')
                            names.pop()
            if self._retirement is not None:
                self.context.aboutToBeDestroyed.disconnect(self._retirement); self._retirement = None
            self.image = self._arrays = None; self.retained_bytes = 0; self.closed = True
        except Exception as error: self._quarantine(error)
