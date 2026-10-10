"""Save the host bindings around optional owned render targets."""
from __future__ import annotations

import ctypes
import threading
from contextlib import contextmanager
from weakref import WeakKeyDictionary, ref


_procedures = WeakKeyDictionary()
_procedure_lock = threading.RLock()


class GeometryUncertain(RuntimeError):
    """Factory/read failure retains its cohort and the coordinator's VBO lease."""
    def __init__(self, owner, reason):
        super().__init__(reason)
        self.owner = owner


class SampleStateUnavailable(RuntimeError):
    """Optional sample controls refused before any graphics mutation."""


def procedure(context, name, result, *arguments):
    key = name, result, arguments
    with _procedure_lock:
        try:
            cache = _procedures.get(context)
            if cache is None:
                cache = {}
                context_ref = ref(context)
                def retired():
                    owner = context_ref()
                    if owner is not None:
                        with _procedure_lock: _procedures.pop(owner, None)
                        try: owner.aboutToBeDestroyed.disconnect(retired)
                        except (RuntimeError, TypeError): pass
                # A context wrapper may recreate its native context. Fence
                # that generation as well as ordinary Python owner retirement.
                context.aboutToBeDestroyed.connect(retired)
                _procedures[context] = cache
        except (TypeError, AttributeError):
            cache = None  # Capability-only adapters without QObject ownership.
        if cache is not None and key in cache: return cache[key]
    address = context.getProcAddress(name.encode("ascii"))
    if not address: raise RuntimeError(name + " is unavailable")
    function = ctypes.CFUNCTYPE(result, *arguments)(int(address))
    if cache is not None:
        with _procedure_lock: cache[key] = function
    return function


@contextmanager
def preserved_state(gl, context, *, exact_context=False):
    """Restore each independent state even when an optional cleanup fails."""
    def current():
        if exact_context:
            from PyQt6.QtGui import QOpenGLContext
            if QOpenGLContext.currentContext() is not context:
                raise RuntimeError('Host graphics state lost its creating context')
    current()
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
                current()
                gl.glActiveTexture(0x84C0 + unit)
                current()
                gl.glBindTexture(0x0DE1, texture)
                current()
                gl.glBindTexture(0x8513, cube)
            actions.append(restore_texture)
            if sampler: actions.append(lambda unit=unit, name=sampler_name: sampler(unit, name))
        actions.append(lambda: gl.glActiveTexture(active))
        failure = None
        for action in actions:
            current()
            try: action()
            except Exception as error:
                if failure is None: failure = error
            current()
        if failure is not None: raise RuntimeError("Host graphics state could not be restored") from failure


@contextmanager
def preserved_samples(gl, context, *, texture_units=(0,1), exact_context=False):
    """Preserve core4 sample controls and the specified owned MS texture units.

    Caller already owns the ordinary GL-state guard. Independent restoration
    continues after a failed mask/texture operation; no legacy enums are queried.
    """
    def current():
        if exact_context:
            from PyQt6.QtGui import QOpenGLContext
            if QOpenGLContext.currentContext() is not context:
                raise RuntimeError('Sample graphics state lost its creating context')
    current()
    if (type(texture_units) is not tuple or not texture_units or len(texture_units)>8
            or len(set(texture_units))!=len(texture_units)
            or any(type(unit) is not int or not 0<=unit<8 for unit in texture_units)):
        raise ValueError('Bounded distinct sample texture units required')
    if context.format().majorVersion() < 4:
        raise SampleStateUnavailable("Native sample transport requires core4 graphics")
    try:
        mask = procedure(context, "glSampleMaski", None, ctypes.c_uint, ctypes.c_uint)
        indexed = procedure(context, "glGetIntegeri_v", None, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
    except RuntimeError as error:
        raise SampleStateUnavailable(str(error)) from error
    words = int(gl.glGetIntegerv(0x8E59))
    if not 0 < words <= 64:
        raise SampleStateUnavailable("Native sample-mask storage is invalid")
    masks = []
    for index in range(words):
        value = ctypes.c_int()
        indexed(0x8E52, index, ctypes.byref(value))
        masks.append(value.value & 0xffffffff)
    enabled = {key: bool(gl.glIsEnabled(key)) for key in (0x809D, 0x8E51, 0x80A0, 0x809E, 0x809F, 0x8C36)}
    active = int(gl.glGetIntegerv(0x84E0))
    textures = []
    try:
        for unit in texture_units:
            current()
            gl.glActiveTexture(0x84C0 + unit)
            current()
            textures.append(int(gl.glGetIntegerv(0x9104)))
            current()
    finally:
        current()
        gl.glActiveTexture(active)
        current()
    if gl.glGetError():
        raise RuntimeError("Native sample-state capture failed")
    try:
        yield mask
    finally:
        actions = [lambda index=index, value=value: mask(index, value) for index, value in enumerate(masks)]
        actions.extend(lambda key=key, value=value: (gl.glEnable if value else gl.glDisable)(key)
                       for key, value in enabled.items())
        for unit, texture in zip(texture_units,textures,strict=True):
            def restore(unit=unit, texture=texture):
                gl.glActiveTexture(0x84C0 + unit)
                current()
                gl.glBindTexture(0x9100, texture)
            actions.append(restore)
        actions.append(lambda: gl.glActiveTexture(active))
        failure = None
        for action in actions:
            current()
            try: action()
            except Exception as error:
                if failure is None: failure = error
            current()
        if failure is not None:
            raise RuntimeError("Native sample state could not be restored") from failure


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
