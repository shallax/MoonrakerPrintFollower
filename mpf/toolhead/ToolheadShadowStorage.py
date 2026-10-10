"""Private depth-cube sets; only an entirely completed light set is readable.

The creating worker keeps its context current through allocation, capture and
close. Scene freezing, draw budgets, caster eligibility and shader binding are
owned by the future shadow coordinator, not this resource adapter.
"""
import ctypes
from weakref import ref

from .ToolheadGLState import procedure, preserved_state, preserved_samples, retire_textures
from .ToolheadShadowValues import MAP_SIZE, ShadowMapPlan, ShadowProjection, certify_key

U, I = ctypes.c_uint, ctypes.c_int


class ShadowStorage:
    def __init__(self, gl, context, plan):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtCore import Qt
        if not isinstance(plan, ShadowMapPlan) or not plan.lights:
            raise ValueError('Point-shadow storage requires an admitted nonempty plan')
        if QOpenGLContext.currentContext() is not context or context.format().majorVersion() < 4:
            raise RuntimeError('Point-shadow storage requires its creating core4 context')
        self.gl, self.context, self.group = gl, context, context.shareGroup()
        self.plan = plan
        self.names, self.framebuffer = [], 0
        self._retired = False
        self.retirement_failure = ''
        self._pending = self._published = None
        self._projections = self._front_projections = ()
        self._completed = set()
        self._capturing = False
        self._ids = tuple((light.kind, light.index) for light in plan.lights)
        self._front = self._back = ()
        self._bind = procedure(context, 'glBindFramebuffer', None, U, U)
        self._attach = procedure(context, 'glFramebufferTexture2D', None, U, U, U, U, I)
        self._check = procedure(context, 'glCheckFramebufferStatus', U, U)
        self._depth_range = procedure(context, 'glDepthRange', None, ctypes.c_double, ctypes.c_double)
        owner_ref = ref(self)
        def retired():
            owner = owner_ref()
            if owner is not None:
                owner._retired = True
                owner._pending = owner._published = None
                # FBOs are context-local; the dying native context owns it.
                owner.framebuffer = 0
                try:
                    retire_textures(owner.group, owner.names)
                except Exception as error:
                    # Keep the lease if queueing or flushing failed. A later
                    # close/current shared context can retry independent cleanup.
                    owner.retirement_failure = str(error)
                else:
                    owner.names = []
        self._on_retired = retired
        context.aboutToBeDestroyed.connect(retired, Qt.ConnectionType.DirectConnection)
        try:
            with preserved_state(gl, context):
                if int(gl.glGetIntegerv(0x851C)) < MAP_SIZE:
                    raise RuntimeError('Point-shadow cube size is unavailable')
                count = len(plan.lights) * 2
                names = (U * count)()
                procedure(context, 'glGenTextures', None, I, ctypes.POINTER(U))(count, names)
                self.names = list(names)
                if not all(self.names):
                    raise RuntimeError('Point-shadow texture allocation failed')
                gl.glActiveTexture(0x84C0)
                image = procedure(context, 'glTexImage2D', None, U, I, I, I, I, I, U, U, ctypes.c_void_p)
                query = procedure(context, 'glGetTexLevelParameteriv', None, U, I, U, ctypes.POINTER(I))
                unpack = int(gl.glGetIntegerv(0x88EF))
                try:
                    gl.glBindBuffer(0x88EC, 0)
                    for name in self.names:
                        gl.glBindTexture(0x8513, name)
                        for face in range(6):
                            image(0x8515+face, 0, 0x8CAC, MAP_SIZE, MAP_SIZE, 0, 0x1902, 0x1406, None)
                            for parameter, expected in ((0x1000, MAP_SIZE), (0x1001, MAP_SIZE), (0x1003, 0x8CAC)):
                                value = I()
                                query(0x8515+face, 0, parameter, ctypes.byref(value))
                                if value.value != expected:
                                    raise RuntimeError('Point-shadow depth storage differs from admission')
                        for key, value in ((0x2801, 0x2600), (0x2800, 0x2600), (0x2802, 0x812F),
                                           (0x2803, 0x812F), (0x8072, 0x812F), (0x884C, 0)):
                            gl.glTexParameteri(0x8513, key, value)
                finally:
                    gl.glBindBuffer(0x88EC, unpack)
                value = U()
                procedure(context, 'glGenFramebuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
                self.framebuffer = value.value
                if not self.framebuffer:
                    raise RuntimeError('Point-shadow framebuffer allocation failed')
                self._bind(0x8D40, self.framebuffer)
                procedure(context, 'glDrawBuffer', None, U)(0)
                procedure(context, 'glReadBuffer', None, U)(0)
                self._front, self._back = tuple(self.names[:count//2]), tuple(self.names[count//2:])
                for name in self._back:
                    self._attach(0x8D40, 0x8D00, 0x8515, name, 0)
                    if self._check(0x8D40) != 0x8CD5:
                        raise RuntimeError('Point-shadow framebuffer is incomplete')
                if gl.glGetError():
                    raise RuntimeError('Point-shadow allocation failed')
        except Exception:
            self.close()
            raise

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self._retired or not self.framebuffer or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Point-shadow storage needs its live creating context')

    def begin(self, key, projections, *, target=None):
        self._current()
        if self._capturing:
            raise RuntimeError('Point-shadow capture is already active')
        if key is None:
            raise ValueError('Point-shadow generation key is missing')
        certify_key(key)
        values = tuple(projections)
        if len(values) != len(self._ids) or any(not isinstance(value, ShadowProjection) for value in values):
            raise ValueError('Point-shadow projection set is incomplete')
        if any(value.origin != light.position for value, light in zip(values, self.plan.lights, strict=True)):
            raise ValueError('Point-shadow projection does not match its delivered light')
        if target is not None:
            if type(target) is not int or target not in (0, 1):
                raise ValueError('Point-shadow physical target is invalid')
            # The exchange owns physical admission. A private cancelled
            # publish must not choose the next writer over a displayed set.
            count = len(self._ids)
            self._back = tuple(self.names[target*count:(target+1)*count])
        # All six faces and all emitters belong to this one frozen generation.
        self._pending, self._projections = key, values
        self._completed.clear()

    def capture(self, identity, face, draw):
        self._current()
        if self._capturing:
            raise RuntimeError('Point-shadow capture is already active')
        self._capturing = True
        try:
            self._capture_face(identity, face, draw)
        finally:
            self._capturing = False

    def _capture_face(self, identity, face, draw):
        self._current()
        if self._pending is None or identity not in self._ids or type(face) is not int or not 0 <= face < 6:
            raise ValueError('Point-shadow capture ticket is invalid')
        gl, context = self.gl, self.context
        # Refuse host coverage we cannot independently reproduce/restore.
        if (tuple(map(int, gl.glGetIntegerv(0x0B40))) != (0x1B02, 0x1B02) or
                any(gl.glIsEnabled(flag) for flag in (0x8C89, 0x0B90, *range(0x3000, 0x3008)))):
            raise RuntimeError('Point-shadow raster coverage is unsupported')
        depth_range = tuple(map(float, gl.glGetDoublev(0x0B70)))
        ticket = identity, face
        self._completed.discard(ticket)
        try:
            with preserved_state(gl, context), preserved_samples(gl, context):
                self._bind(0x8D40, self.framebuffer)
                self._attach(0x8D40, 0x8D00, 0x8515+face, self._back[self._ids.index(identity)], 0)
                if self._check(0x8D40) != 0x8CD5:
                    raise RuntimeError('Point-shadow face is incomplete')
                gl.glViewport(0, 0, MAP_SIZE, MAP_SIZE)
                gl.glDisable(0x0B44)
                gl.glFrontFace(0x0901)
                gl.glCullFace(0x0405)
                for flag in (0x0BE2, 0x0C11, 0x8037, 0x864F, 0x809E, 0x809F, 0x8E51):
                    # Depth clamp is restored separately; sample controls by
                    # their own guard. Other states belong to ordinary guard.
                    if flag != 0x864F:
                        gl.glDisable(flag)
                clamp = bool(gl.glIsEnabled(0x864F))
                try:
                    gl.glDisable(0x864F)
                    self._depth_range(0., 1.)
                    gl.glEnable(0x0B71)
                    gl.glDepthMask(True)
                    gl.glDepthFunc(0x0201)
                    gl.glClearDepth(1.)
                    gl.glClear(0x0100)
                    draw(gl, self._projections[self._ids.index(identity)], face)
                    if gl.glGetError():
                        raise RuntimeError('Point-shadow face draw failed')
                finally:
                    try:
                        self._depth_range(*depth_range)
                    finally:
                        (gl.glEnable if clamp else gl.glDisable)(0x864F)
        except Exception:
            # A failed draw or restoration never certifies even an old ticket.
            self._completed.discard(ticket)
            raise
        self._current()
        self._completed.add(ticket)

    def publish(self):
        self._current()
        if self._capturing:
            raise RuntimeError('Point-shadow capture is already active')
        required = {(identity, face) for identity in self._ids for face in range(6)}
        if self._pending is None or self._completed != required:
            raise RuntimeError('Point-shadow set has incomplete faces')
        self._front = self._back
        count = len(self._ids)
        other = count if self._front == tuple(self.names[:count]) else 0
        self._back = tuple(self.names[other:other+count])
        self._published, self._pending = self._pending, None
        self._front_projections = self._projections
        self._completed.clear()

    def completed(self, key):
        self._current()
        certify_key(key)
        if self._published is None or self._published != key:
            return None
        return tuple(zip(self._ids, self._front, self._front_projections, strict=True))

    def close(self):
        from PyQt6.QtGui import QOpenGLContext
        if self._capturing:
            raise RuntimeError('Point-shadow capture is already active')
        if self.framebuffer and QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Point-shadow worker must close in its creating context')
        name, self.framebuffer = self.framebuffer, 0
        self._pending = self._published = None
        self._completed.clear()
        try:
            if name:
                value = U(name)
                procedure(self.context, 'glDeleteFramebuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
        finally:
            retire_textures(self.group, self.names)
            self.names = []
            self._retired = True
            try:
                self.context.aboutToBeDestroyed.disconnect(self._on_retired)
            except (RuntimeError, TypeError):
                pass
