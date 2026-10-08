"""Private shared-context reflection producer with bounded GPU submission."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from collections import deque
from threading import Condition, Event
import time

from PyQt6.QtCore import QThread, pyqtSignal
from .ToolheadCaptureGL import RawBindings
from .ToolheadCaptureBuffers import CaptureFace, CaptureTexture
from .ToolheadCaptureRecipe import CaptureScene
from .ToolheadEnvironment import CubeStorage, ToolheadEnvironment
from .ToolheadEnvironmentMailbox import EnvironmentMailbox
from .ToolheadGLState import procedure


class _Abandoned(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptureJob:
    serial: int
    soft: object
    frame: object
    ready_fence: int
    generation: int | None = None


@dataclass(frozen=True)
class CaptureResult:
    colour: int
    depth: int
    descriptor: object
    soft: object
    serial: int = 0


class WorkerStorage(CubeStorage):
    def __init__(self, gl, context, depth_format, target=0):
        super().__init__(gl, context, face_factory=lambda width, height, _format:
            CaptureFace(gl, context, width, height, depth_format))
        self.select(target)

    def select(self, target):
        if target not in (0, 1): raise RuntimeError('Reflection pair target is invalid')
        # Mailbox owns physical pair admission. A cancelled capture may already
        # have called CubeStorage.publish; its private swap must never select
        # the next writer or overwrite the main's still-displayed front.
        self.back, self.back_depth = self.names[target], self.names[target+2]

    def close(self):
        names, self.names = getattr(self, 'names', []), []
        if names:
            body = (ctypes.c_uint*len(names))(*names)
            procedure(self.context, 'glDeleteTextures', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(len(names), body)
        face, self.face = getattr(self, 'face', None), None
        if face is not None: face.close()


class EnvironmentWorker(QThread):
    ready = pyqtSignal()

    def __init__(self, context, surface, main_thread, constants, depth_format):
        super().__init__()
        self.context, self.surface, self.main_thread = context, surface, main_thread
        self.constants, self.depth_format = constants, depth_format
        self.mailbox = EnvironmentMailbox()
        self._condition = Condition()
        self._pending = None
        self._completed = deque()
        self.failure = ''
        self.timings = None
        self.stopping = False
        self._accepting = True
        self.host_drained = Event()
        self.abandoned = Event()
        self.quarantine = None

    def submit(self, job):
        with self._condition:
            if not self._accepting: return job
            previous, self._pending = self._pending, job
            self._condition.notify()
            return previous

    def completed(self):
        with self._condition:
            result = tuple(self._completed)
            self._completed.clear()
            return result

    def stop(self):
        with self._condition:
            self._accepting = False
            self.stopping = True
            self._condition.notify()
        self.mailbox.close()

    def abandon(self):
        # No main-context finish exists: retain shared handles and input leases.
        self.abandoned.set()
        self.stop()

    def _checkpoint(self):
        if self.abandoned.is_set(): raise _Abandoned('Reflection context activation failed; resources quarantined')

    def wake(self):
        with self._condition: self._condition.notify()

    def _acknowledge(self, serial, undeleted_fence=0):
        self._checkpoint()
        with self._condition: self._completed.append((serial, undeleted_fence))
        self.ready.emit()

    def run(self):
        gl = scene = environment = None
        pending_gpu = deque()
        active = token = retiring = None
        input_fence_deleted = False
        try:
            if not self.context.makeCurrent(self.surface): raise RuntimeError('Reflection worker context unavailable')
            gl = RawBindings(self.context, self.constants)
            fence = procedure(self.context, 'glFenceSync', ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
            poll = procedure(self.context, 'glClientWaitSync', ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64)
            delete = procedure(self.context, 'glDeleteSync', None, ctypes.c_void_p)
            wait = procedure(self.context, 'glWaitSync', None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64)
            def seal_gpu():
                self._checkpoint()
                value = fence(0x9117, 0)
                if value: gl.glFlush(); return int(value)
                gl.glFinish(); return None
            def drain(value):
                if value is None: return
                while True:
                    self._checkpoint()
                    result = poll(ctypes.c_void_p(value), 0, 0)
                    if result in (0x911A, 0x911C): break
                    if result == 0x911D:
                        # A bad shared fence cannot justify deleting textures
                        # still used by the main context. Ask that owner for a
                        # verified finish and retain the handle until it replies.
                        self.failure = 'Reflection worker fence wait failed'
                        self.ready.emit()
                        while not self.host_drained.wait(.01): self._checkpoint()
                        self._checkpoint()
                        gl.glFinish()
                        break
                    time.sleep(.001)
                self._checkpoint()
                delete(ctypes.c_void_p(value))
            scene = CaptureScene(gl, self.context, lambda image: CaptureTexture(gl, self.context, image))
            environment = ToolheadEnvironment(storage_factory=lambda bindings, context:
                WorkerStorage(bindings, context, self.depth_format, token.index))
            while True:
                self._checkpoint()
                retirement = self.mailbox.take_retirement()
                if retirement is not None:
                    retiring = retirement
                    retired, _payload, producer, consumer = retirement
                    drain(producer); drain(consumer)
                    self.mailbox.retired(retired)
                    retiring = None
                    continue
                if self.stopping and self.mailbox.drained: break
                with self._condition:
                    if self._pending is None:
                        self._condition.wait(.01)
                        continue
                    if (self._pending.generation is not None
                            and self._pending.generation != self.mailbox.generation):
                        stale, self._pending = self._pending, None
                        self._completed.append((stale.serial, stale.ready_fence))
                        self.ready.emit()
                        continue
                    token = self.mailbox.reserve(self._pending.generation)
                    if token is None:
                        self._condition.wait(.01)
                        continue
                    active, self._pending = self._pending, None
                    input_fence_deleted = False
                try:
                    if active.ready_fence:
                        wait(ctypes.c_void_p(active.ready_fence), 0, 0xffffffffffffffff)
                        delete(ctypes.c_void_p(active.ready_fence))
                        input_fence_deleted = True
                    started, turns = time.perf_counter(), 0
                    if not self.mailbox.building_current(token): continue
                    snapshot = scene.snapshot(active.frame)
                    if environment._storage is not None: environment._storage.select(token.index)
                    environment._next = environment._retry = 0
                    previous_revision = environment.revision
                    while self.mailbox.building_current(token):
                        self._checkpoint()
                        if len(pending_gpu) == 2:
                            drain(pending_gpu[0]); pending_gpu.popleft()
                        environment.step(gl, self.context, active.serial, active.soft, lambda snapshot=snapshot: snapshot)
                        turns += 1
                        pending_gpu.append(seal_gpu())
                        if environment.failure: raise RuntimeError(environment.failure)
                        if environment.revision != previous_revision: break
                    while pending_gpu:
                        drain(pending_gpu[0]); pending_gpu.popleft()
                    # All geometry uses are complete before main releases its
                    # VBO wrapper lease. Publication still carries its own fence.
                    if self.mailbox.building_current(token) and environment.revision != previous_revision:
                        result = CaptureResult(environment._storage.front, environment._storage.front_depth,
                            environment.descriptor, active.soft, active.serial)
                        producer = seal_gpu()
                        self.mailbox.complete(token, result, producer, gpu_complete=producer is None)
                        token = None
                        self.timings = (active.serial, (time.perf_counter()-started)*1000, turns)
                        self.ready.emit()
                finally:
                    self._checkpoint()
                    while pending_gpu:
                        drain(pending_gpu[0]); pending_gpu.popleft()
                    if token is not None:
                        last = seal_gpu()
                        self.mailbox.abort(token, last, gpu_complete=last is None)
                        token = None
                    self._acknowledge(active.serial, active.ready_fence if not input_fence_deleted else 0)
                    active = None
            self._checkpoint()
            gl.glFinish()
        except Exception as error:
            self.failure = str(error)[:200]
            with self._condition: self._accepting = False
            self.mailbox.close()
            self.ready.emit()
        finally:
            with self._condition:
                self._accepting = False
                pending, self._pending = self._pending, None
            try:
                # A context failure cannot be repaired by returning unsafe leases.
                # Finish submitted work before dropping job wrappers on the main.
                self._checkpoint()
                if gl is not None:
                    try: gl.glFinish()
                    except Exception: pass
                self._checkpoint()
                if token is not None:
                    try: self.mailbox.abort(token, None, gpu_complete=True)
                    except Exception: pass
                if retiring is not None:
                    retired, _payload, producer, consumer = retiring
                    drain(producer); drain(consumer)
                    self.mailbox.retired(retired)
                if active is not None: self._acknowledge(active.serial, active.ready_fence if not input_fence_deleted else 0)
                if pending is not None:
                    remaining_fence = pending.ready_fence
                    if gl is not None and pending.ready_fence:
                        try:
                            delete(ctypes.c_void_p(pending.ready_fence))
                            remaining_fence = 0
                        except Exception: pass
                    self._acknowledge(pending.serial, remaining_fence)
                # Close/error also seals the main consumer. Its last-use fence
                # must drain before deleting shared textures, even when no later
                # window frame is scheduled. Poll/read tickets return in finally.
                while environment is not None and not self.mailbox.drained:
                    self._checkpoint()
                    retirement = self.mailbox.take_retirement()
                    if retirement is None:
                        with self._condition: self._condition.wait(.002)
                        continue
                    self._checkpoint()
                    retired, _payload, producer, consumer = retirement
                    drain(producer); drain(consumer)
                    self.mailbox.retired(retired)
                if environment is not None:
                    try: environment.close()
                    except Exception: pass
                if scene is not None:
                    try: scene.close()
                    except Exception: pass
            except _Abandoned:
                self.quarantine = (scene, environment, active, token, retiring, pending, tuple(pending_gpu))
            self.context.doneCurrent()
            self.context.moveToThread(self.main_thread)
