"""Bounded, atomic environment capture; only complete maps become visible."""
from __future__ import annotations

import ctypes
import math
import time
from contextlib import ExitStack
from dataclasses import dataclass
from .ToolheadGLState import preserved_state, procedure, retire_textures, flush_texture_deletions

SIZE = 512
TEXTURE_UNIT = 7
DEPTH_UNIT = 6
TURN_COMMANDS = 64
TURN_SECONDS = .002  # CPU submission budget; a single GPU draw is indivisible.
CHANGED_INTERVAL = .15
PERIODIC_INTERVAL = 1.


class EnvironmentNotReady(RuntimeError):
    """Native slicing is still producing a trustworthy scene snapshot."""


@dataclass(frozen=True)
class ProbeDescriptor:
    origin: tuple
    minimum: tuple
    maximum: tuple
    near: float = .2
    far: float = 1000.

    def __post_init__(self):
        vectors = (self.origin, self.minimum, self.maximum)
        if any(len(vector) != 3 or not all(math.isfinite(value) for value in vector) for vector in vectors):
            raise ValueError("Invalid reflection probe bounds")
        if any(a >= b for a, b in zip(self.minimum, self.maximum, strict=True)) or not .01 <= self.near < self.far <= 10000:
            raise ValueError("Invalid reflection probe range")


class DepthSampler:
    def __init__(self, storage): self.storage = storage
    def bind(self, unit): self.storage._bind(unit, self.storage.front_depth)
    def release(self, unit): self.storage.release(unit)


class CubeStorage:
    """Atomic colour/depth pairs and one single-sample face target."""
    def __init__(self, gl, context, *, face_factory=None):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        self.context = context
        self.group = context.shareGroup()
        flush_texture_deletions()
        gen = procedure(context, "glGenTextures", None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))
        image = procedure(context, "glTexImage2D", None, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        self._copy = procedure(context, "glCopyTexSubImage2D", None, ctypes.c_uint, *([ctypes.c_int] * 7))
        self._mips = procedure(context, "glGenerateMipmap", None, ctypes.c_uint)
        self._sampler = procedure(context, "glBindSampler", None, ctypes.c_uint, ctypes.c_uint)
        names = (ctypes.c_uint * 4)()
        gen(4, names)
        self.names = list(names)
        if not all(self.names):
            self.close(); raise RuntimeError("Environment texture allocation failed")
        try:
            gl.glActiveTexture(0x84C0 + TEXTURE_UNIT)
            for index, name in enumerate(self.names):
                gl.glBindTexture(0x8513, name)
                depth = index >= 2
                for face in range(6):
                    image(0x8515 + face, 0, 0x81A6 if depth else 0x8058, SIZE, SIZE, 0,
                          0x1902 if depth else 0x1908, 0x1406 if depth else 0x1401, None)
                for key, value in ((0x2801, 0x2600 if depth else 0x2703), (0x2800, 0x2600 if depth else 0x2601),
                        (0x2802, 0x812F), (0x2803, 0x812F), (0x8072, 0x812F), (0x884C, 0)):
                    gl.glTexParameteri(0x8513, key, value)
            format_ = QOpenGLFramebufferObjectFormat()
            format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
            format_.setInternalTextureFormat(0x8058)
            self.face = (face_factory or QOpenGLFramebufferObject)(SIZE, SIZE, format_)
            if not self.face.isValid(): raise RuntimeError("Environment face target unavailable")
        except Exception:
            self.close(); raise
        self.front, self.back, self.front_depth, self.back_depth = self.names
        self.depth = DepthSampler(self)
        self._bindings = {}

    def begin(self, gl, clear):
        if not self.face.bind(): raise RuntimeError("Environment face target could not bind")
        gl.glViewport(0, 0, SIZE, SIZE)
        gl.glDisable(0x0C11)
        gl.glDisable(0x8037)  # Host polygon offset must not alter captured scene depth.
        gl.glColorMask(True, True, True, True)
        gl.glDepthMask(True)
        if clear:
            gl.glClearDepth(1.)
            gl.glClearColor(.08, .09, .11, 1.)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)

    def copy(self, gl, face):
        gl.glActiveTexture(0x84C0 + TEXTURE_UNIT)
        gl.glBindTexture(0x8513, self.back)
        self._copy(0x8515 + face, 0, 0, 0, 0, 0, SIZE, SIZE)
        gl.glActiveTexture(0x84C0 + DEPTH_UNIT)
        gl.glBindTexture(0x8513, self.back_depth)
        self._copy(0x8515 + face, 0, 0, 0, 0, 0, SIZE, SIZE)

    def publish(self, gl):
        gl.glActiveTexture(0x84C0 + TEXTURE_UNIT)
        gl.glBindTexture(0x8513, self.back)
        self._mips(0x8513)
        if int(gl.glGetError()): raise RuntimeError("Environment capture failed")
        self.front, self.back = self.back, self.front
        self.front_depth, self.back_depth = self.back_depth, self.front_depth

    def bind(self, unit):
        self._bind(unit, self.front)

    def _bind(self, unit, name):
        from UM.View.GL.OpenGL import OpenGL
        gl = OpenGL.getInstance().getBindingsObject()
        active = int(gl.glGetIntegerv(0x84E0))
        try:
            gl.glActiveTexture(0x84C0 + unit)
            self._bindings.setdefault(unit, (int(gl.glGetIntegerv(0x8514)), int(gl.glGetIntegerv(0x8919))))
            self._sampler(unit, 0)
            gl.glBindTexture(0x8513, name)
        except Exception:
            self.release(unit)
            raise
        finally:
            gl.glActiveTexture(active)

    def release(self, unit):
        if unit not in self._bindings: return
        from UM.View.GL.OpenGL import OpenGL
        gl = OpenGL.getInstance().getBindingsObject()
        active = int(gl.glGetIntegerv(0x84E0))
        previous, sampler = self._bindings.pop(unit)
        try:
            gl.glActiveTexture(0x84C0 + unit)
            try:
                gl.glBindTexture(0x8513, previous)
            finally:
                self._sampler(unit, sampler)
        finally:
            gl.glActiveTexture(active)

    def close(self):
        names, self.names = getattr(self, "names", []), []
        retire_textures(self.group, names, getattr(self, "window", None))
        self.face = None


class ToolheadEnvironment:
    def __init__(self, *, clock=time.monotonic, storage_factory=CubeStorage, window=None,
                 commands_per_turn=TURN_COMMANDS, turn_clock=time.perf_counter, diagnostic=False):
        self._window = window
        self._clock, self._factory = clock, storage_factory
        self._commands_per_turn = max(1, min(TURN_COMMANDS, int(commands_per_turn)))
        self._turn_clock = turn_clock
        self._context = self._storage = None
        self._hard = self._published = self._pending = None
        self._face = 0
        self._commands = None
        self._preparing = None
        self.descriptor = None
        self._next = self._retry = self._changed_next = 0.
        self.revision = 0
        self.available = False
        self.failure = ""
        self._reported_failure = ""
        self._diagnostic = diagnostic
        self._capture_start = None
        self._capture_cpu = self._capture_turns = self._report_at = 0.

    def release_bindings(self):
        # ShaderProgram stops its texture loop at the first release exception.
        # Each owned unit must still unwind independently of that host loop.
        if self._storage is None: return
        failure = None
        for unit in (TEXTURE_UNIT, DEPTH_UNIT):
            try: self._storage.release(unit)
            except Exception as error:
                if failure is None: failure = error
        if failure is not None: raise RuntimeError('Environment bindings could not be restored') from failure

    @property
    def ready(self): return self._clock() >= self._retry

    @property
    def working(self): return self._pending is not None

    @property
    def wake_delay(self):
        return 0. if self.working else max(.01, max(self._next, self._retry) - self._clock())

    def step(self, gl, context, hard, soft, snapshot):
        now = self._clock()
        if context is not self._context:
            self.close()
            self._context = context
        if hard != self._hard:
            self._hard = hard
            # Keep the last COMPLETE colour/depth/descriptor triple visible
            # during same-context replacement. Only pending faces are retired;
            # context changes/disable/failure still retire the published map.
            self._pending = self._commands = self._preparing = None
            self._retry = self._next = self._changed_next = 0
        if now < self._retry: return False
        if self._pending is None:
            if soft != self._published:
                self._next = min(self._next, self._changed_next)
            if now < self._next: return False
        started = None
        try:
            started = self._turn_clock()
            operations = 0
            # Production bounds preparation uses immutable CPU arrays only.
            # A pending CPU turn must not save/restore the entire host GL state.
            if (self._pending is not None and self._preparing is not None
                    and getattr(self._pending[1], 'cpu_preparation', False) is True):
                for operations in range(1, self._commands_per_turn + 1):
                    command = next(self._preparing, None)
                    if command is None:
                        self._preparing = None
                        operations -= 1
                        if self._turn_clock() - started >= TURN_SECONDS: return True
                        break
                    command()
                    if self._turn_clock() - started >= TURN_SECONDS: return True
                if operations == self._commands_per_turn: return True
            with preserved_state(gl, context), ExitStack() as turn_cleanup:
                if self._storage is None:
                    self._storage = self._factory(gl, context)
                    self._storage.window = self._window
                if self._pending is None:
                    # Freeze the complete scene/prefix/pose for all six faces.
                    if self._diagnostic:
                        self._capture_start = now
                        self._capture_cpu = self._capture_turns = 0.
                    self._pending = (soft, snapshot())
                    self._face, self._commands = 0, None
                    prepare = getattr(self._pending[1], "prepare", None)
                    self._preparing = iter(prepare()) if prepare else None
                data = self._pending[1]
                turn = getattr(data, 'turn', None)
                if turn is not None: turn_cleanup.enter_context(turn())
                for _operation in range(self._commands_per_turn - operations):
                    preparing = False
                    if self._preparing is not None:
                        command = next(self._preparing, None)
                        if command is not None:
                            command()
                            preparing = True
                        else: self._preparing = None
                    if not preparing:
                        first = self._commands is None
                        if first: self._commands = iter(data.commands(self._face))
                        self._storage.begin(gl, first)
                        command = next(self._commands, None)
                        if command is not None:
                            command(gl)
                        else:
                            self._storage.copy(gl, self._face)
                            self._face += 1
                            self._commands = None
                            if self._face == 6:
                                descriptor = data.descriptor
                                if not isinstance(descriptor, ProbeDescriptor): raise RuntimeError("Reflection probe descriptor unavailable")
                                self._storage.publish(gl)
                                self.descriptor = descriptor
                                self.available = True
                                self.revision += 1
                                self._published = self._pending[0]
                                self._pending = None
                                completed = self._clock()
                                self._changed_next = completed + CHANGED_INTERVAL
                                self._next = completed + (CHANGED_INTERVAL if soft != self._published else PERIODIC_INTERVAL)
                                self.failure = ""
                                self._reported_failure = ""
                    if not self.working or self._turn_clock() - started >= TURN_SECONDS: break
                return self.working
        except Exception as error:
            return self.fail(error)
        finally:
            if self._diagnostic and self._capture_start is not None and started is not None:
                self._capture_cpu += max(0., self._turn_clock() - started)
                self._capture_turns += 1
                if not self.working:
                    finished = self._clock()
                    if self.available and finished >= self._report_at:
                        try:
                            from UM.Logger import Logger
                            Logger.log("i", "Toolhead environment capture: revision %s, wall %.1f ms, CPU submission %.1f ms, %s turns",
                                self.revision, (finished-self._capture_start)*1000, self._capture_cpu*1000, int(self._capture_turns))
                        except Exception: pass
                        self._report_at = finished + 10.
                    self._capture_start = None

    def fail(self, error):
        # A map fault retires only optional reflection. No partly built
        # map, recursive render, permanent latch or timer spin is allowed.
        self.failure = str(error)[:200]
        if self.failure != self._reported_failure:
            self._reported_failure = self.failure
            try:
                from UM.Logger import Logger
                Logger.log("w", "Toolhead reflections unavailable: %s", self.failure)
            except Exception:
                pass
        self.available = False
        self._pending = self._commands = self._preparing = None
        self.descriptor = None
        self._retry = self._clock() + 5.
        return False

    def defer(self):
        # Slicing readiness is not a map fault. Keep the last complete map;
        # abandon any partial scene and retry shortly without a busy loop.
        self._pending = self._commands = self._preparing = None
        self._next = 0.
        self._capture_start = None
        self._retry = self._clock() + .05

    def apply(self, shader):
        shader.setUniformValue("u_environmentEnabled", int(self.available))
        shader.setUniformValue("u_environment", TEXTURE_UNIT)
        shader.setUniformValue("u_sceneDepth", DEPTH_UNIT)
        shader.setTexture(TEXTURE_UNIT, self._storage if self.available else None)
        shader.setTexture(DEPTH_UNIT, self._storage.depth if self.available else None)
        if self.available:
            descriptor = self.descriptor
            for name, value in (("u_probe", descriptor.origin), ("u_sceneMin", descriptor.minimum),
                    ("u_sceneMax", descriptor.maximum), ("u_probeNear", descriptor.near), ("u_probeFar", descriptor.far)):
                shader.setUniformValue(name, list(value) if isinstance(value, tuple) else value)

    def close(self):
        if self._storage is not None: self._storage.close()
        self._storage = self._pending = self._commands = self._published = self._hard = self._preparing = self.descriptor = None
        self._context = None
        self._capture_start = None
        self.available = False
