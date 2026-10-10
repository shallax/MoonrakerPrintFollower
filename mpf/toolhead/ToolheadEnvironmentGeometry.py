"""Private complete-path query storage, not a renderer or source lease owner.

The coordinator retains the original main-context VBO wrappers and immutable
publication until this cohort's producer AND last consumer fences retire. This
owner waits the source-ready fence but never deletes it. No array certificate
substitutes for that graphics lease. Allocation is currently unwired in Cura.
"""
import ctypes
from contextlib import contextmanager
from weakref import ref

import numpy as np

from .ToolheadGLState import procedure
from .ToolheadEnvironmentPaths import (PathInputs, PathPrefix, admit_path_inputs,
    freeze_path_prefix, prepare_path_source, memory_plan, _layout)

U, I, P = ctypes.c_uint, ctypes.c_int, ctypes.c_void_p
TARGET, BINDING, ACTIVE = 0x8C2A, 0x8C2C, 0x84E0
UNITS = (8, 9, 10, 11)
UPLOAD_BYTES = 1024 * 1024
_quarantined = []


class _RestoreFailed(RuntimeError):
    pass


class GeometryUncertain(RuntimeError):
    """Factory/read failure retains its cohort and the coordinator's VBO lease."""
    def __init__(self, owner, reason):
        super().__init__(reason)
        self.owner = owner


@contextmanager
def _bindings(gl, context):
    """Buffer textures are outside the ordinary renderer's units0..7 guard."""
    from PyQt6.QtGui import QOpenGLContext
    if QOpenGLContext.currentContext() is not context:
        raise _RestoreFailed('Path query state requires its exact creating context')
    active, buffer = int(gl.glGetIntegerv(ACTIVE)), int(gl.glGetIntegerv(TARGET))
    states = []
    bind_sampler = procedure(context, 'glBindSampler', None, U, U)
    try:
        for unit in UNITS:
            gl.glActiveTexture(0x84C0+unit)
            states.append((unit, int(gl.glGetIntegerv(BINDING)), int(gl.glGetIntegerv(0x8919))))
        yield
    finally:
        if QOpenGLContext.currentContext() is not context:
            # A body callback can switch contexts. Restoring saved names into
            # that foreign context would damage an unrelated renderer.
            raise _RestoreFailed('Path query restoration lost its creating context')
        # Restoration failures propagate; the caller must quarantine its cohort
        # instead of continuing ordinary rendering with uncertain host state.
        errors = []
        def restore(function, *args):
            if QOpenGLContext.currentContext() is not context:
                raise _RestoreFailed('Path query restoration lost its creating context')
            succeeded = True
            try: function(*args)
            except Exception as error: errors.append(error); succeeded = False
            if QOpenGLContext.currentContext() is not context:
                raise _RestoreFailed('Path query restoration lost its creating context')
            return succeeded
        for unit, texture, sampling in states:
            # If selecting a unit fails, binding its texture on some OTHER
            # active unit is unsafe. Indexed sampler restoration is independent.
            if restore(gl.glActiveTexture, 0x84C0+unit):
                restore(gl.glBindTexture, TARGET, texture)
            restore(bind_sampler, unit, sampling)
        restore(gl.glBindBuffer, TARGET, buffer)
        restore(gl.glActiveTexture, active)
        if errors: raise _RestoreFailed('Path query graphics-state restoration failed') from errors[0]


class PathGeometry:
    """One frozen source/prefix, private EBO/tree/alias storage and VBO view.

    ``existing_bytes`` charges all OTHER retained owners (including an older
    query cohort and its source publications, cube pairs and native work).
    This owner additionally charges its full borrowed CPU publication/VBO,
    new original EBO, tree and alias storage. Shared-source overcount is safe;
    silently omitting a prior source is not. ``source_ready`` is an actual GL
    sync created/flushed by the retained source owner in this share group.
    """
    def __init__(self, gl, context, inputs, prefix, model, *, epoch, source_ready,
                 byte_budget, existing_bytes=0, group=8, cancel=None):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtCore import Qt
        if (type(inputs) is not PathInputs or type(prefix) is not PathPrefix
                or type(inputs.certificate) is not tuple or len(inputs.certificate) != 6
                or type(prefix.certificate) is not tuple or len(prefix.certificate) != 5
                or prefix.certificate[0] != inputs.certificate):
            raise ValueError('Matching frozen original source and prefix required')
        admitted = admit_path_inputs(inputs.mesh, inputs.vertex, inputs.certificate[1], cancel=cancel)
        if (admitted.certificate != inputs.certificate or admitted.fields != inputs.fields
                or admitted.scalar_count != inputs.scalar_count or len(admitted.arrays) != len(inputs.arrays)
                or any(name != other or array is not data for (name, array), (other, data)
                    in zip(admitted.arrays, inputs.arrays, strict=True))
                or _layout(inputs.indices, np.dtype('uint32'), 2) != _layout(admitted.indices, np.dtype('uint32'), 2)):
            raise ValueError('Path source descriptor differs from its certificate')
        if not inputs.indices.size:
            raise ValueError('Empty paths require no geometry query owner')
        if type(prefix.aliases) is not np.ndarray or prefix.aliases.ndim != 1 or prefix.aliases.size > 32:
            raise ValueError('Complete original moved aliases required')
        _layout(prefix.aliases.reshape(-1, 1), np.dtype('uint32'), 1)
        verified = freeze_path_prefix(inputs, prefix.first*2, prefix.history*2,
            prefix.completed*2, prefix.partial, cancel=cancel)
        if verified.certificate != prefix.certificate or not np.array_equal(verified.aliases, prefix.aliases):
            raise ValueError('Complete original moved aliases required')
        if (type(epoch) is not int or epoch <= 0 or type(source_ready) is not int
                or not 0 < source_ready < 1 << (ctypes.sizeof(P)*8)
                or type(existing_bytes) is not int or existing_bytes < 0):
            raise ValueError('Retained source epoch, ready fence and memory charge required')
        if (QOpenGLContext.currentContext() is not context or context.format().majorVersion() < 4
                or context.format().profile() != context.format().OpenGLContextProfile.CoreProfile):
            raise RuntimeError('Path query allocation requires its exact core4 context')
        self.gl, self.context, self.group = gl, context, context.shareGroup()
        self.inputs, self.prefix, self.epoch = inputs, prefix, epoch
        self.buffers, self.textures = [], []
        self.prepared = None
        self.published = self.retired = False
        self.quarantined = False
        self.failure = ''
        owner_ref = ref(self)
        def destroyed():
            owner = owner_ref()
            if owner is not None: owner.retired = True
        self._on_destroyed = None
        fields = dict(inputs.arrays)
        indices_bytes = max(4, inputs.indices.nbytes)
        alias_bytes = max(4, prefix.aliases.nbytes)
        self.source_bytes = inputs.vertex.size + inputs.mesh.retained_bytes()
        groups = memory_plan(len(inputs.indices), group)[0]
        texels = ((2*groups-1)*2, inputs.scalar_count, inputs.indices.size, max(1, prefix.aliases.size))
        capacity = int(gl.glGetIntegerv(0x8C2B))
        if (int(gl.glGetIntegerv(0x8872)) < 12 or int(gl.glGetIntegerv(0x8B4D)) < 12
                or any(count > capacity for count in texels)):
            raise RuntimeError('Complete path query exceeds buffer-texture capabilities')
        # Preflight BEFORE any acceleration allocation. The builder's peak also
        # includes its new GPU tree reservation and bounded CPU work scratch.
        self.prepared = prepare_path_source(fields['a_vertex'], fields['a_line_dim'],
            inputs.indices, model, inputs.certificate[1], group=group, byte_budget=byte_budget,
            retained_bytes=existing_bytes+self.source_bytes+indices_bytes+alias_bytes, cancel=cancel)
        self.peak_bytes = self.prepared.peak_bytes
        self.incremental_bytes = self.prepared.retained_bytes + max(32, self.prepared.gpu_bytes) + indices_bytes + alias_bytes
        self.retained_bytes = self.source_bytes + self.incremental_bytes
        self.key = inputs.certificate, self.prepared.certificate, self.prepared.group, prefix.certificate
        try:
            with _bindings(gl, context):
                self._cancel(cancel)
                if not procedure(context, 'glIsSync', ctypes.c_ubyte, P)(P(source_ready)):
                    raise RuntimeError('Path query source-ready fence is unavailable')
                procedure(context, 'glWaitSync', None, P, U, ctypes.c_uint64)(P(source_ready), 0, 0xffffffffffffffff)
                gl.glBindBuffer(TARGET, inputs.vertex.name)
                self._size(inputs.vertex.size)
                nodes = self.prepared.nodes if self.prepared.nodes.size else np.zeros((1, 8), np.float32)
                indices = inputs.indices if inputs.indices.size else np.zeros(1, np.uint32)
                aliases = prefix.aliases if prefix.aliases.size else np.zeros(1, np.uint32)
                handles = (self._upload(nodes, cancel), inputs.vertex.name,
                           self._upload(indices, cancel), self._upload(aliases, cancel))
                for unit, handle, format_, count in zip(UNITS, handles, (0x8814, 0x822E, 0x8236, 0x8236), texels, strict=True):
                    self._cancel(cancel)
                    value = U()
                    procedure(context, 'glGenTextures', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
                    if not value.value: raise RuntimeError('Path query texture allocation failed')
                    self.textures.append(value.value)
                    gl.glActiveTexture(0x84C0+unit); gl.glBindTexture(TARGET, value.value)
                    procedure(context, 'glTexBuffer', None, U, U, U)(TARGET, format_, handle)
                    query = procedure(context, 'glGetTexLevelParameteriv', None, U, I, U, ctypes.POINTER(I))
                    for parameter, expected in ((0x1000, count), (0x1003, format_), (0x8C2D, handle)):
                        actual = I(); query(TARGET, 0, parameter, ctypes.byref(actual))
                        if actual.value != expected: raise RuntimeError('Path query texture storage incomplete')
                self._cancel(cancel)
                if gl.glGetError(): raise RuntimeError('Path query allocation failed')
            context.aboutToBeDestroyed.connect(destroyed, Qt.ConnectionType.DirectConnection)
            self._on_destroyed = destroyed
            self.published = True
        except _RestoreFailed as error:
            self._quarantine('Path query graphics-state restoration failed; resources quarantined')
            raise GeometryUncertain(self, self.failure) from error
        except Exception:
            try: self.close()
            except Exception as error:
                self._quarantine('Path query cleanup failed')
                raise GeometryUncertain(self, self.failure) from error
            raise

    @staticmethod
    def _cancel(cancel):
        if cancel is not None and cancel(): raise RuntimeError('Cancelled path query allocation')

    def _size(self, expected):
        size = ctypes.c_int64()
        procedure(self.context, 'glGetBufferParameteri64v', None, U, U, ctypes.POINTER(ctypes.c_int64))(
            TARGET, 0x8764, ctypes.byref(size))
        if size.value != expected: raise RuntimeError('Path query source/storage size differs from admission')

    def _upload(self, array, cancel):
        value = U()
        procedure(self.context, 'glGenBuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
        if not value.value: raise RuntimeError('Path query buffer allocation failed')
        self.buffers.append(value.value)
        self.gl.glBindBuffer(TARGET, value.value)
        procedure(self.context, 'glBufferData', None, U, ctypes.c_ssize_t, P, U)(TARGET, array.nbytes, None, 0x88E4)
        self._size(array.nbytes)
        write = procedure(self.context, 'glBufferSubData', None, U, ctypes.c_ssize_t, ctypes.c_ssize_t, P)
        for offset in range(0, array.nbytes, UPLOAD_BYTES):
            self._cancel(cancel)
            write(TARGET, offset, min(UPLOAD_BYTES, array.nbytes-offset), P(array.ctypes.data+offset))
            if self.gl.glGetError(): raise RuntimeError('Path query upload failed')
        return value.value

    def _current(self, context, epoch):
        from PyQt6.QtGui import QOpenGLContext
        if (self.retired or self.quarantined or not self.published or epoch != self.epoch
                or QOpenGLContext.currentContext() is not context or context.shareGroup() != self.group):
            raise RuntimeError('Path query cohort is not readable in this context epoch')

    @contextmanager
    def read(self, gl, context, epoch):
        """Caller already holds the map's consumer ticket through its draw fence."""
        self._current(context, epoch)
        try:
            with _bindings(gl, context):
                sampler = procedure(context, 'glBindSampler', None, U, U)
                for unit, name in zip(UNITS, self.textures, strict=True):
                    gl.glActiveTexture(0x84C0+unit); gl.glBindTexture(TARGET, name); sampler(unit, 0)
                yield self
        except _RestoreFailed as error:
            self._quarantine('Path query graphics-state restoration failed; resources quarantined')
            raise GeometryUncertain(self, self.failure) from error

    def _quarantine(self, reason):
        self.published = False
        self.failure = reason
        if not self.quarantined: _quarantined.append(self)
        self.quarantined = True

    def close(self):
        """Only after verified producer + last-consumer retirement, no destructor."""
        from PyQt6.QtGui import QOpenGLContext
        if self.quarantined: raise RuntimeError(self.failure)
        if QOpenGLContext.currentContext() is not self.context or self.retired:
            raise RuntimeError('Path query retirement needs its live creating context')
        self.published = False
        try:
            with _bindings(self.gl, self.context):
                for names, function in ((self.textures, 'glDeleteTextures'), (self.buffers, 'glDeleteBuffers')):
                    while names:
                        value = U(names[-1])
                        procedure(self.context, function, None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
                        if self.gl.glGetError(): raise RuntimeError('Path query deletion failed')
                        names.pop()
        except Exception:
            self._quarantine('Path query retirement failed; resources quarantined')
            raise
        self.inputs = self.prefix = self.prepared = None
        if self._on_destroyed is not None:
            self.context.aboutToBeDestroyed.disconnect(self._on_destroyed)
            self._on_destroyed = None
