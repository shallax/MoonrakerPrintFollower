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
from .ToolheadEnvironmentGeometry import GeometryUncertain


class _Abandoned(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptureJob:
    serial: int
    soft: object
    frame: object
    ready_fence: int
    generation: int | None = None
    key: object = None


@dataclass(frozen=True)
class CaptureResult:
    colour: int
    depth: int
    descriptor: object
    soft: object
    serial: int = 0
    # Plain published source values travel with the completed pair. Their
    # actual main-context wrappers remain in AsyncEnvironment._leases until
    # BOTH producer and last-consumer fences have drained for this pair.
    frame: object = None
    input_fence: int = 0
    key: object = None
    geometry: object = None


@dataclass
class _SourceReservation:
    source_bytes: int
    geometry: object = None
    capture_bytes: int = 0

    @property
    def retained_bytes(self):
        return self.source_bytes+self.capture_bytes+(self.geometry.incremental_bytes if self.geometry is not None else 0)


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

    def __init__(self, context, surface, main_thread, constants, depth_format, *, query_factory=None):
        super().__init__()
        self.context, self.surface, self.main_thread = context, surface, main_thread
        self.constants, self.depth_format = constants, depth_format
        self.mailbox = EnvironmentMailbox()
        self._condition = Condition()
        self._wake_revision = 0
        self._pending = None
        self._completed = deque()
        self._released = deque()
        self.failure = ''
        self.timings = None
        self.stopping = False
        self._accepting = True
        self.host_drained = Event()
        self.abandoned = Event()
        self.quarantine = None
        # Disabled in Cura until hit-local radiance and scene ordering are
        # qualified. Factory reserves future capture storage + old cohorts and
        # returns one newly owned source cohort, never a reused front owner.
        self.query_factory = query_factory

    def submit(self, job):
        with self._condition:
            if not self._accepting: return job
            previous, self._pending = self._pending, job
            self._wake_revision += 1
            self._condition.notify()
            return previous

    def completed(self):
        with self._condition:
            result = tuple(self._completed)
            self._completed.clear()
            return result

    def sources_released(self, serial):
        """Main has actually dropped this acknowledged publication's wrappers."""
        with self._condition:
            self._released.append(serial)
            self._wake_revision += 1
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._accepting = False
            self.stopping = True
            self._wake_revision += 1
            self._condition.notify()
        self.mailbox.close()

    def abandon(self):
        # No main-context finish exists: retain shared handles and input leases.
        self.abandoned.set()
        self.stop()

    def _checkpoint(self):
        if self.abandoned.is_set():
            raise _Abandoned(self.failure or 'Reflection context activation failed; resources quarantined')

    def wake(self):
        with self._condition:
            self._wake_revision += 1
            self._condition.notify()

    def _acknowledge(self, serial, undeleted_fence=0):
        self._checkpoint()
        with self._condition: self._completed.append((serial, undeleted_fence))
        self.ready.emit()

    def run(self):
        gl = scene = environment = None
        pending_gpu = deque()
        active = token = retiring = None
        geometry = None
        geometries = {}
        source_owners = {}
        scene_receipt = None
        input_fence_deleted = False
        active_transferred = False
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
            def retire_pair(retirement):
                nonlocal retiring
                retired, payload, producer, consumer = retirement
                retiring = retirement
                try:
                    drain(producer)
                    retiring = retired, payload, None, consumer
                    drain(consumer)
                    retiring = retired, payload, None, None
                    if payload is not None and payload.geometry is not None:
                        payload.geometry.close()
                        # Private handles are gone, but main source wrappers
                        # can outlive the queued acknowledgement. Keep the
                        # conservative receipt charged until main collection.
                    self.mailbox.retired(retired)
                    if payload is not None:
                        # Publication transfers the source lease to this pair.
                        # Capture end does not finish later consumer queries.
                        self._acknowledge(payload.serial, payload.input_fence)
                    retiring = None
                except _Abandoned:
                    raise
                except Exception:
                    # Never retry a driver deletion of uncertain outcome, or
                    # replay an already-deleted producer after consumer fault.
                    self.failure = 'Reflection pair retirement failed; resources quarantined'
                    self.abandon()
                    self.ready.emit()
                    self._checkpoint()
            scene = CaptureScene(gl, self.context, lambda image: CaptureTexture(gl, self.context, image))
            environment = ToolheadEnvironment(storage_factory=lambda bindings, context:
                WorkerStorage(bindings, context, self.depth_format, token.index))
            while True:
                self._checkpoint()
                with self._condition:
                    observed_wake = self._wake_revision
                    while self._released:
                        released = source_owners.pop(self._released.popleft(), None)
                        if released is not None and released.geometry is not None:
                            geometries.pop(id(released.geometry), None)
                retirement = self.mailbox.take_retirement()
                if retirement is not None:
                    retiring = retirement
                    retire_pair(retirement)
                    retiring = retirement = None
                    continue
                if self.stopping and self.mailbox.drained: break
                with self._condition:
                    if self._pending is None:
                        # Notifications may arrive between retirement inspection
                        # and acquiring this lock. Never sleep past such a wake.
                        if observed_wake == self._wake_revision:
                            self._condition.wait(.01 if self.stopping else None)
                        continue
                    if (self._pending.generation is not None
                            and self._pending.generation != self.mailbox.generation):
                        stale, self._pending = self._pending, None
                        self._completed.append((stale.serial, stale.ready_fence))
                        self.ready.emit()
                        stale = None
                        continue
                    token = self.mailbox.reserve(self._pending.generation)
                    if token is None:
                        self._condition.wait(.01)
                        continue
                    active, self._pending = self._pending, None
                    geometry = None
                    input_fence_deleted = False
                    active_transferred = False
                try:
                    if active.ready_fence:
                        wait(ctypes.c_void_p(active.ready_fence), 0, 0xffffffffffffffff)
                    if self.query_factory is not None:
                        # Include even clean-cancelled/None factory jobs until
                        # main confirms release, not merely queued completion.
                        reservation = _SourceReservation(active.frame.retained_source_bytes(),
                            capture_bytes=getattr(active.frame, 'capture_storage_bytes', lambda: 0)())
                        # Main acknowledgement releases a source lease, not
                        # CaptureScene's private cached uploads/publications.
                        # Keep their receipt until snapshot actually replaces
                        # those caches, including a cancelled prior capture.
                        previous_sources = tuple(source_owners.values())
                        if scene_receipt is not None:
                            previous_sources += (scene_receipt,)
                        source_owners[active.serial] = reservation
                        try:
                            geometry = self.query_factory(gl, self.context, active, previous_sources,
                                lambda token=token: self.stopping or not self.mailbox.building_current(token))
                        except GeometryUncertain as error:
                            geometry = error.owner
                            self.failure = str(error)[:200]
                            self.abandon()
                            self._checkpoint()
                        except Exception:
                            # A cleanly cancelled unpublished allocation owns no
                            # reader. Its source still transfers to abort/drain;
                            # cancellation must not kill the producer for newer work.
                            if self.stopping or not self.mailbox.building_current(token): continue
                            raise
                        if geometry is not None:
                            if id(geometry) in geometries:
                                self.failure = 'Reflection query factory reused a live cohort'
                                self.abandon(); self._checkpoint()
                            geometries[id(geometry)] = geometry
                            reservation.geometry = geometry
                    if active.ready_fence:
                        delete(ctypes.c_void_p(active.ready_fence))
                        input_fence_deleted = True
                    started, turns = time.perf_counter(), 0
                    if not self.mailbox.building_current(token): continue
                    snapshot = scene.snapshot(active.frame)
                    if self.query_factory is not None:
                        scene_receipt = _SourceReservation(reservation.source_bytes,
                            capture_bytes=reservation.capture_bytes)
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
                    # Capture geometry uses have completed. The published pair
                    # retains its source for possible later consumer queries.
                    if self.mailbox.building_current(token) and environment.revision != previous_revision:
                        result = CaptureResult(environment._storage.front, environment._storage.front_depth,
                            environment.descriptor, active.soft, active.serial, active.frame,
                            key=active.key, geometry=geometry)
                        producer = seal_gpu()
                        self.mailbox.complete(token, result, producer, gpu_complete=producer is None)
                        active_transferred = True
                        geometry = None
                        result = None
                        token = None
                        self.timings = (active.serial, (time.perf_counter()-started)*1000, turns)
                        self.ready.emit()
                finally:
                    self._checkpoint()
                    while pending_gpu:
                        drain(pending_gpu[0]); pending_gpu.popleft()
                    if token is not None:
                        last = seal_gpu()
                        self.mailbox.abort(token, last, gpu_complete=last is None,
                            payload=CaptureResult(0, 0, None, active.soft, active.serial, active.frame,
                                active.ready_fence if not input_fence_deleted else 0, active.key, geometry))
                        active_transferred = True
                        geometry = None
                        token = None
                    active = snapshot = None
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
                    except Exception:
                        self.failure = 'Reflection producer finish failed; resources quarantined'
                        self.abandon()
                        self.ready.emit()
                        self._checkpoint()
                self._checkpoint()
                if token is not None:
                    self.mailbox.abort(token, None, gpu_complete=True,
                        payload=CaptureResult(0, 0, None, active.soft, active.serial, active.frame,
                            active.ready_fence if not input_fence_deleted else 0, active.key, geometry))
                    active_transferred = True
                    geometry = None
                    token = None
                if retiring is not None:
                    retire_pair(retiring)
                    retiring = None
                if active is not None and not active_transferred:
                    self._acknowledge(active.serial, active.ready_fence if not input_fence_deleted else 0)
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
                    retire_pair(retirement)
                    retirement = None
                if environment is not None:
                    try: environment.close()
                    except Exception: pass
                if scene is not None:
                    try: scene.close()
                    except Exception: pass
            except _Abandoned:
                self.quarantine = (scene, environment, active, token, retiring, pending, tuple(pending_gpu),
                    geometry, tuple(geometries.values()))
            self.context.doneCurrent()
            self.context.moveToThread(self.main_thread)
