"""Render-thread facade for worker captures and fenced main-context reads."""
from __future__ import annotations

import ctypes
from weakref import WeakSet
from PyQt6.QtCore import QCoreApplication, QThread, QTimer, Qt, QRunnable
from PyQt6.QtGui import QOpenGLContext, QOffscreenSurface
from PyQt6 import sip
from .ToolheadEnvironment import ToolheadEnvironment, TEXTURE_UNIT, DEPTH_UNIT, CHANGED_INTERVAL, PERIODIC_INTERVAL
from .ToolheadEnvironmentWorker import EnvironmentWorker, CaptureJob
from .ToolheadCaptureRecipe import CaptureFreezer
from .ToolheadGLState import preserved_state, procedure

_owners = set()  # Retain running threads through nonblocking retirement.
_quarantined = set()  # Failed main activation cannot prove shared resources safe to free.


class _MainFinish(QRunnable):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def run(self):
        try:
            if self.owner._is_main_current(): self.owner._finish_main()
        except Exception:
            self.owner._abandon()
        finally: self.owner._failure_scheduled = False


class _Depth:
    def __init__(self, bindings): self.bindings = bindings
    def bind(self, unit): self.bindings.bind_depth(unit)
    def release(self, unit): self.bindings.release(unit)


class _ConsumerBindings:
    def __init__(self, owner, result):
        self.owner, self.result = owner, result
        self.depth = _Depth(self)
        self._bindings, self._use = {}, None

    def bind(self, unit): self._bind(unit, self.result.colour)
    def bind_depth(self, unit): self._bind(unit, self.result.depth)

    def _bind(self, unit, name):
        owner, gl = self.owner, self.owner._gl
        if owner._main_retired or owner._quarantine:
            raise RuntimeError('Reflection consumer context was retired')
        if self._use is None:
            admission = owner._worker.mailbox.begin_read()
            if admission is None: raise RuntimeError('Reflection consumer was retired')
            self._use, payload = admission
            owner._reads.add(self)
            if payload is not self.result:
                self._finish(); raise RuntimeError('Reflection consumer descriptor changed')
        active = int(gl.glGetIntegerv(0x84E0))
        try:
            gl.glActiveTexture(0x84C0+unit)
            self._bindings.setdefault(unit, (int(gl.glGetIntegerv(0x8514)), int(gl.glGetIntegerv(0x8919))))
            owner._sampler(unit, 0)
            gl.glBindTexture(0x8513, name)
        except Exception:
            self.release(unit); raise
        finally: gl.glActiveTexture(active)

    def release(self, unit):
        owner, gl = self.owner, self.owner._gl
        if owner._main_retired or owner._quarantine:
            self._bindings.pop(unit, None)
            return
        binding = self._bindings.pop(unit, None)
        if binding is not None:
            active = int(gl.glGetIntegerv(0x84E0))
            try:
                gl.glActiveTexture(0x84C0+unit)
                try: gl.glBindTexture(0x8513, binding[0])
                finally: owner._sampler(unit, binding[1])
            finally: gl.glActiveTexture(active)
        if not self._bindings: self._finish()

    def _finish(self):
        if self._use is None: return
        owner = self.owner
        if owner._main_retired or owner._quarantine: return
        fence = None
        try:
            fence = owner._fence(0x9117, 0)
            if fence: owner._gl.glFlush()
            else: owner._gl.glFinish()
        except Exception:
            owner._gl.glFinish()
            fence = None
        previous = owner._worker.mailbox.end_read(self._use, int(fence) if fence else None, gpu_complete=not fence)
        self._use = None
        owner._reads.discard(self)
        if previous: owner._delete(ctypes.c_void_p(previous))
        owner._worker.wake()

    def close(self):
        # Registered cached shaders are withdrawn by the facade. Actual read
        # completion remains in shader finally, not a settings callback.
        pass


def native_depth_format(gl, context):
    """Match the negotiated native Qt face precision, including Mac depth32."""
    from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
    with preserved_state(gl, context):
        format_ = QOpenGLFramebufferObjectFormat()
        format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        format_.setInternalTextureFormat(0x8058)
        face = QOpenGLFramebufferObject(1, 1, format_)
        if not face.isValid() or not face.bind(): raise RuntimeError('Reflection face format unavailable')
        value = ctypes.c_int()
        procedure(context, 'glGetFramebufferAttachmentParameteriv', None, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_uint, ctypes.POINTER(ctypes.c_int))(0x8D40, 0x8D00, 0x8CD0, ctypes.byref(value))
        if value.value != 0x8D41: raise RuntimeError('Reflection face depth attachment unsupported')
        procedure(context, 'glGetFramebufferAttachmentParameteriv', None, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_uint, ctypes.POINTER(ctypes.c_int))(0x8D40, 0x8D00, 0x8CD1, ctypes.byref(value))
        previous = int(gl.glGetIntegerv(0x8CA7))
        try:
            procedure(context, 'glBindRenderbuffer', None, ctypes.c_uint, ctypes.c_uint)(0x8D41, value.value)
            procedure(context, 'glGetRenderbufferParameteriv', None, ctypes.c_uint, ctypes.c_uint,
                ctypes.POINTER(ctypes.c_int))(0x8D41, 0x8D44, ctypes.byref(value))
            result = value.value
        finally: procedure(context, 'glBindRenderbuffer', None, ctypes.c_uint, ctypes.c_uint)(0x8D41, previous)
        face.release()
        return result


class AsyncEnvironment(ToolheadEnvironment):
    def __init__(self, gl, context, window, *, diagnostic=False):
        super().__init__(window=window, diagnostic=diagnostic)
        self._gl, self._main_context, self._window = gl, context, window
        self._main_pointer = sip.unwrapinstance(context)
        self._main_format = context.format()
        self._main_finish = procedure(context, 'glFinish', None)
        self._main_retired = False
        self._quarantine = False
        self._group = context.shareGroup()
        self._context = context
        self._sampler = procedure(context, 'glBindSampler', None, ctypes.c_uint, ctypes.c_uint)
        self._fence = procedure(context, 'glFenceSync', ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
        self._poll = procedure(context, 'glClientWaitSync', ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64)
        self._delete = procedure(context, 'glDeleteSync', None, ctypes.c_void_p)
        depth = native_depth_format(gl, context)
        self._surface = QOffscreenSurface(); self._surface.setFormat(context.format()); self._surface.create()
        if not self._surface.isValid(): raise RuntimeError('Reflection worker surface unavailable')
        worker_context = QOpenGLContext(); worker_context.setFormat(context.format()); worker_context.setShareContext(context)
        if not worker_context.create() or not QOpenGLContext.areSharing(context, worker_context):
            self._surface.destroy(); raise RuntimeError('Reflection context sharing unavailable')
        constants = {name: int(getattr(gl, name)) for name in dir(gl) if name.startswith('GL_')}
        self._worker = EnvironmentWorker(worker_context, self._surface, QThread.currentThread(), constants, depth)
        worker_context.moveToThread(self._worker)
        self._worker.ready.connect(self._arrived, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._freezer, self._leases = CaptureFreezer(), {}
        self._serial, self._submitted = 0, None
        self._busy = self._closed = False
        self.requires_replacement = False
        self._disposed = False
        self._failure_scheduled = False
        self._shaders = WeakSet()
        self._reads = set()
        self._deletions = []
        self._fallback = ToolheadEnvironment(window=window, diagnostic=diagnostic)
        self._fallback_published = None
        self._retry_timer = QTimer()
        self._retry_timer.setSingleShot(True); self._retry_timer.setInterval(5)
        self._retry_timer.timeout.connect(self._arrived)
        _owners.add(self)
        QCoreApplication.instance().aboutToQuit.connect(self._shutdown)
        context.aboutToBeDestroyed.connect(self._context_destroyed, Qt.ConnectionType.DirectConnection)
        self._worker.start()

    @property
    def working(self): return self._busy

    @property
    def wake_delay(self):
        if self._closed:
            return max(.01, self._retry-self._clock()) if self.requires_replacement else None
        if self._busy: return None
        return max(.01, max(self._next, self._retry)-self._clock())

    def _collect(self):
        if self._quarantine: return
        for serial, fence in self._worker.completed():
            self._leases.pop(serial, None)
            if fence: self._deletions.append(fence)
        if self._is_main_current():
            while self._deletions: self._delete(ctypes.c_void_p(self._deletions.pop()))

    def _is_main_current(self):
        if self._main_retired or self._quarantine: return False
        context = QOpenGLContext.currentContext()
        return context is not None and not sip.isdeleted(context) and sip.unwrapinstance(context) == self._main_pointer

    def _arrived(self):
        self._collect()
        if self._worker.failure:
            self.failure = self._worker.failure
            self.requires_replacement = True
            self._retry = self._clock()+5
            self.close()
            self._schedule_cleanup()
        if not self._closed and self._window is not None: self._window.update()

    def _schedule_cleanup(self):
        if self._main_retired or self._quarantine: return
        if self._window is not None and not self._failure_scheduled:
            self._failure_scheduled = True
            self._window.scheduleRenderJob(_MainFinish(self), self._window.RenderStage.BeforeRenderingStage)
            self._window.update()

    def _finish_main(self):
        if not self._is_main_current():
            raise RuntimeError('Reflection consumer finish requires its original context')
        self._main_finish()
        for binding in tuple(self._reads):
            previous = self._worker.mailbox.end_read(binding._use, None, gpu_complete=True)
            binding._use = None
            self._reads.discard(binding)
            if previous: self._delete(ctypes.c_void_p(previous))
        self._worker.host_drained.set()
        self._worker.wake()
        self._collect()

    def _adopt(self):
        admission = self._worker.mailbox.begin_poll()
        if admission is None: return
        use, fence = admission
        complete = False
        try:
            status = self._poll(ctypes.c_void_p(fence), 0, 0) if fence else 0x911A
            if status == 0x911D: raise RuntimeError('Reflection producer fence failed')
            complete = status in (0x911A, 0x911C)
        finally:
            result = self._worker.mailbox.end_poll(use, complete)
            self._worker.wake()
        if result is None:
            if not self._closed: self._retry_timer.start()
            return
        _token, payload, consumed = result
        if consumed: self._delete(ctypes.c_void_p(consumed))
        self._fallback.close()
        self._storage = _ConsumerBindings(self, payload)
        self.descriptor, self.available = payload.descriptor, True
        self.revision += 1
        self._published, self._busy = payload.soft, payload.serial != self._serial
        completed = self._clock()
        self._changed_next, self._next = completed+CHANGED_INTERVAL, completed+PERIODIC_INTERVAL
        if self._diagnostic and self._worker.timings:
            from UM.Logger import Logger
            serial, elapsed, turns = self._worker.timings
            Logger.log('i', 'Toolhead worker capture: job %s, wall %.1f ms, %s worker turns', serial, elapsed, turns)

    def step(self, gl, context, hard, soft, snapshot):
        if self._closed: return False
        self._collect()
        if self._deletions: self._schedule_cleanup()
        now = self._clock()
        if hard != self._hard:
            self._worker.mailbox.change_generation()
            self._hard, self._submitted, self._busy = hard, None, False
            self._next = self._retry = self._changed_next = 0
        self._adopt()
        key = hard, soft
        if key == self._submitted and self._busy: return True
        if soft != self._published: self._next = min(self._next, self._changed_next)
        if now < max(self._next, self._retry): return self._busy
        data = snapshot()
        frozen = self._freezer.freeze(data, context)
        if frozen is None:
            self._fallback.step(gl, context, hard, soft, lambda: data)
            if self._fallback.available:
                self._storage, self.descriptor, self.available = self._fallback._storage, self._fallback.descriptor, True
                published = self._fallback._storage, self._fallback.revision
                if published != self._fallback_published:
                    self._fallback_published = published
                    self.revision += 1
            self._next = now+.05
            return True
        frame, wrappers = frozen
        ready = self._fence(0x9117, 0)
        if not ready: raise RuntimeError('Reflection geometry readiness fence unavailable')
        gl.glFlush()
        self._serial += 1
        job = CaptureJob(self._serial, soft, frame, int(ready), self._worker.mailbox.generation)
        self._leases[job.serial] = wrappers
        previous = self._worker.submit(job)
        if previous is not None:
            self._leases.pop(previous.serial, None)
            if previous.ready_fence: self._delete(ctypes.c_void_p(previous.ready_fence))
        if previous is job: return False
        self._submitted, self._busy = key, True
        return True

    def apply(self, shader):
        self._shaders.add(shader)
        super().apply(shader)

    def defer(self):
        if self._closed: return
        self._worker.mailbox.change_generation()
        self._submitted, self._busy = None, False
        self._retry = self._clock()+.05

    def fail(self, error):
        self.failure = str(error)[:200]
        self.requires_replacement = True
        self._retry = self._clock()+5
        self.close()
        return False

    def close(self):
        if self._closed: return
        self._closed = True
        self._retry_timer.stop()
        self.available, self._busy = False, False
        for shader in self._shaders:
            for unit in (TEXTURE_UNIT, DEPTH_UNIT):
                try: shader.setTexture(unit, None)
                except Exception:
                    pass  # Withdraw each independent binding and always stop the producer.
        self._worker.stop()
        self._fallback.close()

    def _finished(self):
        if self._disposed: return
        self._disposed = True
        if not self._quarantine:
            self._collect()
            if self._deletions: self._schedule_cleanup()
            self._surface.destroy()
            self._worker.context.deleteLater()
        _owners.discard(self)
        try: QCoreApplication.instance().aboutToQuit.disconnect(self._shutdown)
        except (RuntimeError, TypeError): pass
        try: self._main_context.aboutToBeDestroyed.disconnect(self._context_destroyed)
        except (RuntimeError, TypeError): pass

    def _context_destroyed(self):
        if self._main_retired: return
        self.close()
        surface = None
        # Qt emits this direct signal before destroying the native context,
        # although SIP may already have invalidated the original wrapper.
        context = None
        try:
            context = sip.wrapinstance(self._main_pointer, QOpenGLContext)
            if not self._is_main_current():
                surface = QOffscreenSurface()
                surface.setFormat(self._main_format); surface.create()
                if not context.makeCurrent(surface):
                    self._abandon()
                    return
            self._finish_main()
            # Teardown is the last chance to return outstanding input fences
            # in this exact context; ordinary refresh remains asynchronous.
            self._worker.wait()
            self._collect()
        except Exception:
            self._abandon()
        finally:
            try:
                if surface is not None:
                    try:
                        if context is not None: context.doneCurrent()
                    finally: surface.destroy()
            except Exception:
                self._abandon()
            finally: self._main_retired = True

    def _abandon(self):
        self._quarantine = True
        self.failure = 'Reflection context activation failed; resources quarantined'
        self.requires_replacement = True
        _quarantined.add(self)
        self._worker.abandon()

    def _shutdown(self):
        self.close()
        if self._worker.isRunning():
            # Shutdown alone may synchronously wait. Give a failed shared-fence
            # drain independent confirmation from the host context first.
            if self._main_retired or sip.isdeleted(self._main_context):
                if not self._main_retired: self._abandon()
                self._worker.wait()
                self._finished()
                return
            surface = QOffscreenSurface(); surface.setFormat(self._main_format); surface.create()
            activated = False
            try:
                activated = self._main_context.makeCurrent(surface)
                if activated: self._finish_main()
                else: self._abandon()
            except Exception:
                self._abandon()
            finally:
                self._worker.wait()
                try:
                    if activated:
                        self._collect()
                        self._main_context.doneCurrent()
                finally: surface.destroy()
        self._finished()


def create_environment(gl, context, window, *, diagnostic=False):
    app = QCoreApplication.instance()
    supported = (app is not None and QThread.currentThread() is app.thread()
        and context.format().majorVersion() >= 4 and window is not None)
    if supported and not any(owner._group is context.shareGroup() for owner in _owners | _quarantined):
        try: return AsyncEnvironment(gl, context, window, diagnostic=diagnostic)
        except Exception:
            pass  # Unsupported optional context capabilities keep the exact sync backend.
    return ToolheadEnvironment(window=window, diagnostic=diagnostic)
