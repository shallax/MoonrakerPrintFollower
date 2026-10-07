"""Opt-in asynchronous timings for this plugin's GPU commands only.

Create the untracked ``mpf/toolhead/.profile-rendering`` marker before creating
the node, or pass a marker path explicitly. No application settings are changed.
The sampler never waits for unfinished GPU work or calls glFinish.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import time

class _GLQueries:
    TARGET = 0x88BF  # GL_TIME_ELAPSED

    def __init__(self):
        from PyQt6.QtGui import QOpenGLContext
        self._current = QOpenGLContext.currentContext
        self._context = self._current()
        if self._context is None: raise RuntimeError("No timing context")
        format_ = self._context.format()
        if ((format_.majorVersion(), format_.minorVersion()) < (3, 3)
                and not self._context.hasExtension(b"GL_ARB_timer_query")):
            raise RuntimeError("GPU timer query unsupported")
        def proc(name, *arguments):
            address = self._context.getProcAddress(name)
            if not address: raise RuntimeError("GPU timer query unavailable")
            return ctypes.CFUNCTYPE(None, *arguments)(int(address))
        pointer = ctypes.POINTER
        self._gen = proc(b"glGenQueries", ctypes.c_int, pointer(ctypes.c_uint))
        self._delete = proc(b"glDeleteQueries", ctypes.c_int, pointer(ctypes.c_uint))
        self._begin = proc(b"glBeginQuery", ctypes.c_uint, ctypes.c_uint)
        self._end = proc(b"glEndQuery", ctypes.c_uint)
        self._query = proc(b"glGetQueryiv", ctypes.c_uint, ctypes.c_uint, pointer(ctypes.c_int))
        self._available = proc(b"glGetQueryObjectuiv", ctypes.c_uint, ctypes.c_uint, pointer(ctypes.c_uint))
        self._result = proc(b"glGetQueryObjectui64v", ctypes.c_uint, ctypes.c_uint, pointer(ctypes.c_uint64))

    def _check(self):
        if self._current() is not self._context: raise RuntimeError("GPU timing context retired")

    def create(self):
        self._check()
        value = ctypes.c_uint()
        self._gen(1, ctypes.byref(value))
        if not value.value: raise RuntimeError("GPU timer allocation failed")
        return value.value

    def begin(self, query):
        self._check()
        current = ctypes.c_int()
        self._query(self.TARGET, 0x8865, ctypes.byref(current))  # GL_CURRENT_QUERY
        if current.value: raise RuntimeError("A host GPU timer is active")
        self._begin(self.TARGET, int(query))
        self._query(self.TARGET, 0x8865, ctypes.byref(current))
        if (current.value & 0xffffffff) != query: raise RuntimeError("GPU timer did not begin")

    def end(self):
        self._check()
        self._end(self.TARGET)

    def available(self, query):
        self._check()
        value = ctypes.c_uint()
        self._available(int(query), 0x8867, ctypes.byref(value))  # GL_QUERY_RESULT_AVAILABLE
        return bool(value.value)

    def result(self, query):
        self._check()
        value = ctypes.c_uint64()
        self._result(int(query), 0x8866, ctypes.byref(value))  # GL_QUERY_RESULT
        return value.value

    def delete(self, query):
        self._check()
        value = ctypes.c_uint(int(query))
        self._delete(1, ctypes.byref(value))


class RenderTiming:
    """At most two pending queries, one new sample per second, no GL when off."""
    def __init__(self, marker_path=None, *, clock=time.monotonic, logger=None, backend_factory=_GLQueries):
        try:
            marker = marker_path or Path(__file__).resolve().parents[1] / "toolhead" / ".profile-rendering"
            self.enabled = Path(marker).is_file()
        except Exception: self.enabled = False
        self._clock, self._logger, self._factory = clock, logger, backend_factory
        self._backend = None
        self._pending = []
        self._names = []
        self._next = 0
        self._last_sample = float("-inf")
        try: self._last_report = clock() if self.enabled else 0
        except Exception:
            self.enabled = False
            self._last_report = 0
        self._totals = {}

    def _disable(self):
        self.enabled = False
        if self._backend is not None:
            for _name, query in self._pending:
                try: self._backend.delete(query)
                except Exception: pass
        self._pending.clear()
        self._totals.clear()

    def _poll(self, now):
        for name, query in self._pending[:]:
            if not self._backend.available(query): continue
            milliseconds = self._backend.result(query) / 1e6
            self._backend.delete(query)
            self._pending.remove((name, query))
            count, total, maximum = self._totals.get(name, (0, 0., 0.))
            self._totals[name] = count + 1, total + milliseconds, max(maximum, milliseconds)
        if now - self._last_report >= 10 and self._totals:
            message = "; ".join(f"{name}: {count} samples, mean {total/count:.3f} ms, max {maximum:.3f} ms"
                                for name, (count, total, maximum) in sorted(self._totals.items()))
            if self._logger is None:
                from UM.Logger import Logger
                Logger.log("i", "toolhead GPU timing (own commands): %s", message)
            else: self._logger(message)
            self._last_report = now
            self._totals.clear()

    def measure(self, name, callback):
        if not self.enabled: return callback()
        query = None
        try:
            now = self._clock()
            if self._backend is None: self._backend = self._factory()
            self._poll(now)
            name = str(name)[:32]
            if name not in self._names and len(self._names) < 2: self._names.append(name)
            if (len(self._pending) < 2 and now - self._last_sample >= 1
                    and name == self._names[self._next % len(self._names)]):
                query = self._backend.create()
                try: self._backend.begin(query)
                except Exception:
                    self._backend.delete(query)
                    query = None
                    raise
                self._last_sample = now
                self._next = (self._names.index(name) + 1) % len(self._names)
        except Exception:
            self._disable()
        try:
            return callback()
        finally:
            if query is not None:
                try:
                    self._backend.end()
                    if self.enabled: self._pending.append((name, query))
                    else: self._backend.delete(query)
                except Exception:
                    try: self._backend.delete(query)
                    except Exception: pass
                    self._disable()
