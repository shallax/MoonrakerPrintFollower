"""Owned immutable four-sample scene-depth seed for delayed receiver work.

Copy after the normal scene seed, before drawing the head. Replays never read
the live scene again. The parent charges this owner until exact-context drain;
an uncertain drain retains the entire target instead of pretending it retired.
"""
import ctypes
from contextlib import contextmanager
from weakref import ref

from .ToolheadEnvironmentGeometry import GeometryUncertain
from .ToolheadEnvironmentPaths import _generation
from .ToolheadGLState import preserved_state, preserved_samples, procedure, sample_depth_certificate
from .ToolheadSampleTarget import ToolheadSampleTarget, sample_blit

U, I = ctypes.c_uint, ctypes.c_int
METADATA_BYTES = 64*1024
_uncertain = []


class ReceiverDepth:
    """One exact-context depth copy, including its real target allocation."""
    def __init__(self, gl, context, source, *, key, existing_bytes, byte_budget):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtCore import Qt
        key = _generation(key)
        if (type(key) is not tuple or type(source) is not ToolheadSampleTarget
                or type(existing_bytes) is not int or existing_bytes < 0
                or type(byte_budget) is not int or byte_budget <= 0):
            raise ValueError('Frozen scene-depth source and whole retained ledger required')
        if (QOpenGLContext.currentContext() is not context or source._context is not context
                or not source.isValid()):
            raise RuntimeError('Scene-depth copy requires its live original context')
        self.width, self.height = source.width(), source.height()
        receipt = self.width*self.height*36+METADATA_BYTES
        if existing_bytes+receipt > byte_budget:
            raise MemoryError('Combined frozen scene-depth budget exceeded')
        self.gl, self.context, self.key = gl, context, key
        self.positions = source.positions
        self.retained_bytes = receipt
        self._target = self._retirement = None
        self._busy = False
        self.closed = self.quarantined = False
        try:
            self._target = ToolheadSampleTarget(gl,self.width,self.height)
            # Transfer context-destruction responsibility to this lease owner:
            # the target must not drop names before our verified last-use drain.
            context.aboutToBeDestroyed.disconnect(self._target._retirement)
            self._target._retirement = None
            owner_ref = ref(self)
            def destroyed():
                owner = owner_ref()
                if owner is not None:
                    try: owner.close()
                    except Exception: pass  # close roots the whole uncertain graph.
            self._retirement = destroyed
            context.aboutToBeDestroyed.connect(destroyed,Qt.ConnectionType.DirectConnection)
            with self._operation():
                self._certify(source)
                self._certify(self._target)
                sample_blit(gl,self._target,source,buffers=gl.GL_DEPTH_BUFFER_BIT)
                self._current()
                if self._target.allocation_bytes != receipt-METADATA_BYTES:
                    raise RuntimeError('Frozen depth allocation differs from its receipt')
        except Exception as error:
            self._quarantine(error)

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Frozen depth requires its live creating context')

    def _quarantine(self, error):
        self.quarantined = True
        if not any(owner is self for owner in _uncertain): _uncertain.append(self)
        raise GeometryUncertain(self,'Frozen scene-depth lifetime is uncertain') from error

    @contextmanager
    def _operation(self):
        self._current()
        if self._busy: raise RuntimeError('Frozen depth operation is already admitted')
        self._busy = True
        try:
            with (preserved_state(self.gl,self.context,exact_context=True),
                  preserved_samples(self.gl,self.context,texture_units=(0,),exact_context=True)):
                yield
                self._current()
                if self.gl.glGetError(): raise RuntimeError('Frozen scene-depth graphics receipt failed')
            self._current()
            if self.gl.glGetError(): raise RuntimeError('Frozen depth host restoration failed')
        finally: self._busy = False

    def _positions(self):
        get = procedure(self.context,'glGetMultisamplefv',None,U,U,ctypes.POINTER(ctypes.c_float))
        result = []
        for sample in range(4):
            self._current()
            point = (ctypes.c_float*2)(); get(0x8E50,sample,point)
            self._current(); result.append(tuple(point))
        return tuple(result)

    def _certify(self, target):
        self._current()
        if (not target.bind() or not target.isValid()
                or (target.width(),target.height()) != (self.width,self.height)
                or target.positions != self.positions):
            raise RuntimeError('Frozen depth target descriptor changed')
        sample_depth_certificate(self.gl,self.context,self.width,self.height,expected_name=target.depth_texture())
        if self._positions() != self.positions:
            raise RuntimeError('Frozen depth sample order differs from the source')

    def seed(self, capture):
        """LayerCapture seed callback; copy all original planes, never resolve."""
        self._current()
        if (capture.context is not self.context or capture.samples != 4
                or (capture.width,capture.height) != (self.width,self.height)
                or capture.positions != self.positions):
            raise ValueError('Matching frozen four-sample receiver target required')
        destination = capture.targets['records'].handle()
        if int(self.gl.glGetIntegerv(0x8CA6)) != destination:
            raise RuntimeError('Receiver depth seed requires its bound records target')
        try:
            with self._operation():
                sample_depth_certificate(self.gl,self.context,self.width,self.height)
                if self._positions() != self.positions:
                    raise RuntimeError('Receiver depth sample order changed')
                self._certify(self._target)
                bind = procedure(self.context,'glBindFramebuffer',None,U,U)
                bind(0x8CA8,self._target.handle()); self._current()
                bind(0x8CA9,destination); self._current()
                self.gl.glDisable(0x0C11)
                procedure(self.context,'glBlitFramebuffer',None,I,I,I,I,I,I,I,I,U,U)(
                    0,0,self.width,self.height,0,0,self.width,self.height,self.gl.GL_DEPTH_BUFFER_BIT,0x2600)
            return True
        except Exception as error: self._quarantine(error)

    def close(self):
        if self.closed: return
        try:
            self._current()
            if self._busy: raise RuntimeError('Frozen depth still has admitted work')
            self.gl.glFinish(); self._current()
            if self.gl.glGetError(): raise RuntimeError('Frozen depth drain failed')
            if self._target is not None:
                self._target.close(); self._current()
                if self.gl.glGetError(): raise RuntimeError('Frozen depth target retirement failed')
                self._target = None
            if self._retirement is not None:
                self.context.aboutToBeDestroyed.disconnect(self._retirement)
                self._retirement = None
            self.closed = True; self.retained_bytes = 0
        except Exception as error: self._quarantine(error)

    def __del__(self):
        try: self.close()
        except Exception: pass
