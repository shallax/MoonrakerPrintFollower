"""In-memory, version-checked patches for Cura's render-path allocations.

Two per-frame allocations in the Cura 5.13.0 renderer dominate the
follower's 30 fps traffic (the live report's Mac leak log caught both,
and the reviewer's renderer analysis named them):

- RenderBatch._renderItem uploads a CLIPPED copy of the toolpath's
  index array to a fresh OpenGL buffer for every ranged draw
  (force_recreate=True) — the follower's per-frame progress ranges
  drive it every tick. The mesh's FULL index buffer is already cached;
  the range can draw from it by byte offset instead.
- SimulationPass.render rebuilds the prev_line_types shift array
  (concatenate + slice) every frame, though it depends only on the
  static toolpath line types.

Each patch replaces an EXACT source block inside the installed method
and re-executes the rewritten method in its own module namespace —
Cura's files on disk are never touched, and the source match IS the
version check: a mismatch (a Cura update, a different build) skips
that patch with a log line, leaving the vanilla method in place. The
buffer and draw replacements for a ranged render are all-or-nothing:
applying one without the other would draw the wrong section.

Counters record what the patches saved; the leak probe logs them as
the evidence lines (thousands of frames must not create thousands of
index buffers).
"""
from __future__ import annotations

import ctypes
import inspect
import sys
import textwrap

COUNTERS = {
    "index_buffers_created": [0],
    "prev_line_types_recomputes": [0],
}

# The fragments are written at DEDENTED indentation: getsource returns
# a method with its class-level indent, which cannot exec at module
# level — patch_method dedents first and matches against these.
#
# RenderBatch._renderItem, the ranged-buffer branch. The comment is
# Cura's own admission of the upload-per-frame approach.
_RANGED_BUFFER_FRAGMENT = """\
    else:
        # glDrawRangeElements does not work as expected and did not get the indices field working..
        # Now we're just uploading a clipped part of the array and the start index always becomes 0.
        index_buffer = OpenGL.getInstance().createIndexBuffer(
            mesh, force_recreate=True, index_start = self._render_range[0], index_stop = self._render_range[1])"""

_RANGED_BUFFER_REPLACEMENT = """\
    else:
        # The render-path adaptation: the mesh's FULL index buffer
        # is cached; the range draws from it by byte offset instead
        # of uploading a clipped copy every frame.
        index_buffer = OpenGL.getInstance().createIndexBuffer(mesh)"""

_RANGED_DRAW_FRAGMENT = """\
        else:
            if self._render_mode == self.RenderMode.Triangles:
                self._gl.glDrawRangeElements(self._render_mode, self._render_range[0], self._render_range[1], self._render_range[1] - self._render_range[0], self._gl.GL_UNSIGNED_INT, None)
            else:
                self._gl.glDrawElements(self._render_mode, self._render_range[1] - self._render_range[0], self._gl.GL_UNSIGNED_INT, None)"""

_RANGED_DRAW_REPLACEMENT = """\
        else:
            # The render-path adaptation: PyQt's glDrawElements binding
            # models `indices` as an array (PYQT_OPENGL_ARRAY) and
            # cannot express the bound-EBO byte-offset form — call the
            # native entry point instead, so the full cached index
            # buffer serves every range (the 5.13.0 Uranium comment
            # admits the clipped-upload workaround). glDrawElements
            # covers the triangle range too; glDrawRangeElements was
            # only a hint.
            _mpf_draw_elements(self._render_mode,
                               self._render_range[1] - self._render_range[0],
                               self._gl.GL_UNSIGNED_INT,
                               self._render_range[0] * 4)"""

# SimulationPass.render, the per-frame prev_line_types build.
_PREV_LINE_TYPES_FRAGMENT = """\
                # The first line does not have a previous line: add a MoveUnretractedType in front for start detection
                # this way the first start of the layer can also be drawn
                prev_line_types = numpy.concatenate([numpy.asarray([LayerPolygon.MoveUnretractedType], dtype = numpy.float32), layer_data._attributes["line_types"]["value"]])
                # Remove the last element
                prev_line_types = prev_line_types[0:layer_data._attributes["line_types"]["value"].size]
                layer_data._attributes["prev_line_types"] =  {'opengl_type': 'float', 'value': prev_line_types, 'opengl_name': 'a_prev_line_type'}"""

_PREV_LINE_TYPES_REPLACEMENT = """\
                # The first line does not have a previous line: add a MoveUnretractedType in front for start detection
                # this way the first start of the layer can also be drawn
                # The render-path adaptation: the shifted array
                # depends only on the static line types — cache it
                # per layer-data and rebuild only when the source
                # array changes.
                _mpf_line_types = layer_data._attributes["line_types"]["value"]
                _mpf_cache = getattr(layer_data, "_mpf_prev_line_types_cache", None)
                if _mpf_cache is None or _mpf_cache[0] is not _mpf_line_types:
                    _mpf_prev = numpy.concatenate([numpy.asarray([LayerPolygon.MoveUnretractedType], dtype = numpy.float32), _mpf_line_types])
                    _mpf_prev = _mpf_prev[0:_mpf_line_types.size]
                    layer_data._mpf_prev_line_types_cache = (_mpf_line_types, _mpf_prev)
                    _mpf_recomputes[0] += 1
                prev_line_types = layer_data._mpf_prev_line_types_cache[1]
                layer_data._attributes["prev_line_types"] =  {'opengl_type': 'float', 'value': prev_line_types, 'opengl_name': 'a_prev_line_type'}"""


def _resolve_native_draw_elements():
    """The native glDrawElements for Cura's CURRENT OpenGL context.

    PyQt exposes QOpenGLFunctions.glDrawElements' indices argument as
    PYQT_OPENGL_ARRAY, so the bound-EBO byte-offset form cannot be
    expressed through the wrapper (the live-verified binding verdict:
    ctypes and sip pointer representations were all rejected). The
    native entry point resolved from Qt's own context sees the exact
    EBO QOpenGLBuffer.bind() just bound. Returns a ctypes callable, or
    None when no context is current or the symbol cannot resolve —
    the patch applies only with a working pointer.
    """
    from PyQt6.QtGui import QOpenGLContext
    context = QOpenGLContext.currentContext()
    if context is None:
        return None
    pointer = context.getProcAddress(b"glDrawElements")
    try:
        address = int(pointer)
    except (TypeError, ValueError):
        address = 0
    if not address:
        return None
    return _build_native_draw(address)


def _build_native_draw(address, factory=None):
    """Wrap a resolved glDrawElements address as a callable that
    accepts the patched code's plain-int byte offset and builds the
    pointer argument the prototype demands (the live report: the raw
    prototype rejected a plain int with 'wrong type')."""
    if factory is None:
        factory = ctypes.WINFUNCTYPE if sys.platform == "win32" else ctypes.CFUNCTYPE
    prototype = factory(None, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p)
    try:
        native = prototype(address)
    except (AttributeError, TypeError):
        return None

    def native_draw(mode, count, element_type, byte_offset):
        native(mode, count, element_type, ctypes.c_void_p(byte_offset))

    return native_draw


def patch_method(func, pairs, inject=None):
    """Replace exact source blocks inside `func` and re-execute the
    rewritten method in its own module namespace.

    All-or-nothing: when ANY fragment is absent (a version drift),
    nothing is applied and None comes back. Returns the new function,
    or None when the source was not rewritten or failed to compile.
    """
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):
        return None
    if any(fragment not in source for fragment, _replacement in pairs):
        return None
    rewritten = source
    for fragment, replacement in pairs:
        rewritten = rewritten.replace(fragment, replacement, 1)
    namespace = dict(func.__globals__)
    if inject:
        namespace.update(inject)
    try:
        exec(compile(rewritten, f"<mpf-renderer-adaptation:{func.__name__}>", "exec"), namespace)
    except Exception:
        return None
    return namespace[func.__name__]


def _log(message: str) -> None:
    try:
        from UM.Logger import Logger
        Logger.log("i", "Moonraker renderer adaptation: %s", message)
    except Exception:
        pass


def _wrap_index_creation_counter(OpenGL) -> None:
    """Count REAL index-buffer creations (cached hits excluded) — the
    probe's evidence that thousands of frames do not create thousands
    of buffers."""
    if getattr(OpenGL.createIndexBuffer, "_mpf_counted", False):
        return
    original = OpenGL.createIndexBuffer

    def counted(self, mesh, **kwargs):
        # Count only what will really create a buffer: a cached hit is
        # not a creation, and a mesh without indices returns None
        # without one (the review: attempts must not count as
        # creations).
        if (kwargs.get("force_recreate") or not hasattr(mesh, OpenGL.IndexBufferProperty)) \
                and mesh.hasIndices():
            COUNTERS["index_buffers_created"][0] += 1
        return original(self, mesh, **kwargs)

    counted._mpf_counted = True
    OpenGL.createIndexBuffer = counted


def _patch_render_batch(RenderBatch, OpenGL, draw) -> bool:
    patched = patch_method(
        RenderBatch._renderItem,
        [(_RANGED_BUFFER_FRAGMENT, _RANGED_BUFFER_REPLACEMENT),
         (_RANGED_DRAW_FRAGMENT, _RANGED_DRAW_REPLACEMENT)],
        inject={"ctypes": ctypes, "_mpf_draw_elements": draw},
    )
    if patched is None:
        return False
    RenderBatch._renderItem = patched
    _wrap_index_creation_counter(OpenGL)
    return True


def _patch_simulation_pass(module) -> bool:
    patched = patch_method(
        module.SimulationPass.render,
        [(_PREV_LINE_TYPES_FRAGMENT, _PREV_LINE_TYPES_REPLACEMENT)],
        inject={"_mpf_recomputes": COUNTERS["prev_line_types_recomputes"]},
    )
    if patched is None:
        return False
    module.SimulationPass.render = patched
    return True


def _schedule_simulation_pass(retries: int = 20) -> None:
    """SimulationView is a Cura plugin that may load after ours — find
    its module in sys.modules when it appears, then patch once."""
    from PyQt6.QtCore import QTimer

    def attempt(remaining: int) -> None:
        module = next((m for n, m in sys.modules.items() if n.endswith("SimulationPass")), None)
        if module is not None:
            try:
                if _patch_simulation_pass(module):
                    _log("applied SimulationPass: prev_line_types cached per layer-data")
                else:
                    _log("skipped SimulationPass: source mismatch (version gate)")
            except Exception as exc:
                _log(f"skipped SimulationPass: patch error {exc!r}")
            return
        if remaining:
            QTimer.singleShot(2000, lambda: attempt(remaining - 1))

    attempt(retries)


def _schedule_render_batch(RenderBatch, OpenGL, retries: int = 30) -> None:
    """Apply the ranged-buffer patch once the native glDrawElements
    resolves. The pointer comes from the CURRENT OpenGL context, which
    does not exist at plugin load — retry until Cura's renderer
    initialises it. No resolution, no patch: the patched code can
    never hit an unresolvable draw."""
    from PyQt6.QtCore import QTimer

    def attempt(remaining: int) -> None:
        draw = _resolve_native_draw_elements()
        if draw is None:
            if remaining:
                QTimer.singleShot(2000, lambda: attempt(remaining - 1))
            else:
                _log("skipped RenderBatch: the native glDrawElements never resolved "
                     "(no current OpenGL context)")
            return
        try:
            if _patch_render_batch(RenderBatch, OpenGL, draw):
                _log("applied RenderBatch: ranged draws reuse the full cached index buffer "
                     "through the native glDrawElements")
            else:
                _log("skipped RenderBatch: source mismatch (version gate)")
        except Exception as exc:
            _log(f"skipped RenderBatch: patch error {exc!r}")

    attempt(retries)


def apply_renderer_adaptations() -> None:
    """The plugin's entry: patch what the installed Cura matches, log
    each outcome, and never fail the plugin."""
    try:
        from UM.View.RenderBatch import RenderBatch
        from UM.View.GL.OpenGL import OpenGL
    except Exception as exc:
        _log(f"skipped: UM render imports unavailable: {exc!r}")
        return
    _schedule_render_batch(RenderBatch, OpenGL)
    _schedule_simulation_pass()
