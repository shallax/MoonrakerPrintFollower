"""Save the host bindings around optional owned render targets."""
from __future__ import annotations

import ctypes
import threading
from contextlib import contextmanager


def procedure(context, name, result, *arguments):
    address = context.getProcAddress(name.encode("ascii"))
    if not address: raise RuntimeError(name + " is unavailable")
    return ctypes.CFUNCTYPE(result, *arguments)(int(address))


@contextmanager
def preserved_state(gl, context):
    """Restore each independent state even when an optional cleanup fails."""
    bind = procedure(context, "glBindFramebuffer", None, ctypes.c_uint, ctypes.c_uint)
    use = procedure(context, "glUseProgram", None, ctypes.c_uint)
    vao = None
    if context.format().majorVersion() >= 3 or context.hasExtension(b"GL_ARB_vertex_array_object"):
        vao = procedure(context, "glBindVertexArray", None, ctypes.c_uint)
    elif context.hasExtension(b"GL_APPLE_vertex_array_object"):
        vao = procedure(context, "glBindVertexArrayAPPLE", None, ctypes.c_uint)
    sampler = None
    if (context.format().majorVersion() >= 4 or
            (context.format().majorVersion() == 3 and context.format().minorVersion() >= 3) or
            context.hasExtension(b"GL_ARB_sampler_objects")):
        sampler = procedure(context, "glBindSampler", None, ctypes.c_uint, ctypes.c_uint)
    integer = lambda key: int(gl.glGetIntegerv(key))
    draw, read = integer(0x8CA6), integer(0x8CAA)
    viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
    program, array, element = integer(0x8B8D), integer(0x8894), integer(0x8895)
    vertex_array = integer(0x85B5) if vao else None
    active = integer(0x84E0)
    textures = []
    for unit in range(8):
        gl.glActiveTexture(0x84C0 + unit)
        textures.append((integer(0x8069), integer(0x8514), integer(0x8919) if sampler else None))
    gl.glActiveTexture(active)
    enabled = {key: bool(gl.glIsEnabled(key)) for key in (0x0B71, 0x0BE2, 0x0B44, 0x0C11, 0x8037)}
    # FRAMEBUFFER_SRGB is core 3.0; do not query an unsupported legacy enum.
    if context.format().majorVersion() >= 3: enabled[0x8DB9] = bool(gl.glIsEnabled(0x8DB9))
    depth_mask = bool(gl.glGetBooleanv(0x0B72))
    colour_mask = tuple(map(bool, gl.glGetBooleanv(0x0C23)))
    clear_colour = tuple(map(float, gl.glGetFloatv(0x0C22)))
    depth_func, cull, front = integer(0x0B74), integer(0x0B45), integer(0x0B46)
    blend = [integer(key) for key in (0x80C9, 0x80C8, 0x80CB, 0x80CA, 0x8009, 0x883D)]
    blend_colour = tuple(map(float, gl.glGetFloatv(0x8005)))
    depth_clear = float(gl.glGetDoublev(0x0B73))
    offset = tuple(float(gl.glGetFloatv(key)) for key in (0x8038, 0x2A00))
    scissor = tuple(map(int, gl.glGetIntegerv(0x0C10)))
    try:
        yield
    finally:
        # Ordering matters for EBOs, whose binding belongs to the restored VAO.
        actions = [lambda: bind(0x8CA9, draw), lambda: bind(0x8CA8, read),
                   lambda: gl.glViewport(*viewport), lambda: use(program)]
        if vao: actions.append(lambda: vao(vertex_array))
        actions.extend([lambda: gl.glBindBuffer(0x8892, array), lambda: gl.glBindBuffer(0x8893, element),
                        lambda: gl.glDepthMask(depth_mask), lambda: gl.glColorMask(*colour_mask),
                        lambda: gl.glClearColor(*clear_colour), lambda: gl.glDepthFunc(depth_func),
                        lambda: gl.glCullFace(cull), lambda: gl.glFrontFace(front),
                        lambda: gl.glBlendFuncSeparate(*blend[:4]), lambda: gl.glBlendEquationSeparate(*blend[4:]),
                        lambda: gl.glBlendColor(*blend_colour), lambda: gl.glScissor(*scissor),
                        lambda: gl.glClearDepth(depth_clear), lambda: gl.glPolygonOffset(*offset)])
        for key, value in enabled.items():
            actions.append(lambda key=key, value=value: (gl.glEnable if value else gl.glDisable)(key))
        for unit, (texture, cube, sampler_name) in enumerate(textures):
            def restore_texture(unit=unit, texture=texture, cube=cube):
                gl.glActiveTexture(0x84C0 + unit)
                gl.glBindTexture(0x0DE1, texture)
                gl.glBindTexture(0x8513, cube)
            actions.append(restore_texture)
            if sampler: actions.append(lambda unit=unit, name=sampler_name: sampler(unit, name))
        actions.append(lambda: gl.glActiveTexture(active))
        failure = None
        for action in actions:
            try: action()
            except Exception as error:
                if failure is None: failure = error
        if failure is not None: raise RuntimeError("Host graphics state could not be restored") from failure


_retired_textures = {}
_retirement_lock = threading.RLock()


def flush_texture_deletions():
    """Delete only in a current context belonging to the owning share group."""
    from PyQt6.QtGui import QOpenGLContext
    context = QOpenGLContext.currentContext()
    if context is None: return
    key = id(context.shareGroup())
    with _retirement_lock:
        entry = _retired_textures.get(key)
        if entry is None: return
        names = tuple(entry[1])
        delete = procedure(context, "glDeleteTextures", None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))
        delete(len(names), (ctypes.c_uint * len(names))(*names))
        _retired_textures.pop(key, None)
        try: entry[0].destroyed.disconnect(entry[2])
        except (RuntimeError, TypeError): pass  # A dying share group retires its signal too.


def retire_textures(group, names, window=None):
    """GUI retirement queues names; a render job never borrows the context."""
    if not names: return
    from PyQt6 import sip
    if sip.isdeleted(group): return  # Last shared context already freed names.
    key = id(group)
    with _retirement_lock:
        if key not in _retired_textures:
            def destroyed():
                # The driver frees names when the LAST shared context dies.
                with _retirement_lock: _retired_textures.pop(key, None)
            group.destroyed.connect(destroyed)
            _retired_textures[key] = (group, set(), destroyed)
        _retired_textures[key][1].update(names)
    flush_texture_deletions()
    with _retirement_lock:
        pending = key in _retired_textures
    if pending and window is not None:
        from PyQt6.QtCore import QRunnable
        from PyQt6.QtQuick import QQuickWindow
        class Cleanup(QRunnable):
            def run(self): flush_texture_deletions()
        # Qt invokes this in its own render thread with its own context current.
        # A replacement/nonsharing context leaves old names queued safely.
        try:
            window.scheduleRenderJob(Cleanup(), QQuickWindow.RenderStage.AfterRenderingStage)
            window.update()
        except RuntimeError:
            pass  # Retired window: next shared context or group destruction owns cleanup.
