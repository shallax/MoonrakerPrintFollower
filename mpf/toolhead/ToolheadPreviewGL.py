"""Qt Quick OpenGL preview with render-thread-owned, persistent mesh buffers."""
from __future__ import annotations

import configparser
import copy
import ctypes
from PyQt6.QtCore import QSize, pyqtSignal

from PyQt6.QtGui import QMatrix4x4, QOpenGLContext, QSurfaceFormat, QVector3D, QVector4D
from PyQt6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat, QOpenGLShader, QOpenGLShaderProgram,
    QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile, QOpenGLVertexArrayObject, QOpenGLTextureBlitter)
from PyQt6.QtQuick import QQuickFramebufferObject

from ..resources.PluginPaths import plugin_path
from ..geometry.ToolheadLighting import light_values


class ToolheadPreviewGL(QQuickFramebufferObject):
    failed = pyqtSignal(str)
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setZ(-1)
        self.setMirrorVertically(True)
        self.setTextureFollowsItemSize(True)

    def createRenderer(self):
        # PyQt must retain the Python override wrapper while C++ owns the
        # renderer; otherwise its virtual callbacks can outlive that wrapper.
        self._renderer = PreviewRenderer()
        return self._renderer


class PreviewRenderer(QQuickFramebufferObject.Renderer):
    def __init__(self):
        super().__init__()
        self._packed = self._uploaded = None
        self._camera = None
        self._program = self._buffer = self._vao = self._gl = None
        self._depth_fbo = self._blitter = None
        self._bind_framebuffer = None
        self._window = None
        self._context = None
        self._report_failure = None
        self._failed = False

    def synchronize(self, item):
        # Qt blocks the GUI thread here. Retain immutable bytes/value snapshots;
        # render() never reads a settings QObject or writes its draft.
        owner = item.owner
        self._packed = owner._packed
        self._camera = owner._camera
        self._pan = owner._pan
        self._width, self._height = owner.width(), owner.height()
        self._window = item.window()
        self._report_failure = item.failed.emit
        self._lights = copy.deepcopy(owner._model.lights) if owner._model is not None and hasattr(owner._model, "lights") else []

    def _discard_resources(self):
        # Qt owns the context/share-group bookkeeping for these objects. Never
        # bind or manually destroy an old context's VAO/FBO in its replacement.
        self._uploaded = None
        self._program = self._buffer = self._vao = self._gl = None
        self._depth_fbo = self._blitter = self._bind_framebuffer = None

    def _fail(self, error):
        if self._failed: return
        self._failed = True
        try:
            if self._report_failure is not None:
                self._report_failure("OpenGL preview unavailable: " + str(error)[:240])
        except Exception:
            # The GUI item may already have been destroyed during teardown.
            pass

    def _safe(self, action):
        try:
            action()
            return True
        except Exception as error:
            self._fail(error)
            return False

    def _initialize(self):
        context = QOpenGLContext.currentContext()
        if context is None: raise RuntimeError("No current OpenGL preview context")
        core = context.format().profile() == QSurfaceFormat.OpenGLContextProfile.CoreProfile
        profile = QOpenGLVersionProfile()
        profile.setVersion(4, 1) if core else profile.setVersion(2, 0)
        profile.setProfile(context.format().profile())
        self._gl = QOpenGLVersionFunctionsFactory.get(profile, context)
        if self._gl is None: raise RuntimeError("OpenGL preview functions unavailable")
        if self._gl.initializeOpenGLFunctions() is False: raise RuntimeError("OpenGL preview functions could not initialize")
        source = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        source.read(plugin_path("toolhead", "toolhead.shader"))
        self._program = QOpenGLShaderProgram()
        for kind, key in ((QOpenGLShader.ShaderTypeBit.Vertex, "vertex"),
                          (QOpenGLShader.ShaderTypeBit.Fragment, "fragment")):
            if not self._program.addShaderFromSourceCode(kind, source["shaders"][key + ("41core" if core else "")]):
                raise RuntimeError(self._program.log())
        if not self._program.link(): raise RuntimeError(self._program.log())
        self._vao = QOpenGLVertexArrayObject()
        if not self._vao.create(): raise RuntimeError("OpenGL preview VAO unavailable")
        self._buffer = QOpenGLBuffer()
        if not self._buffer.create(): raise RuntimeError("OpenGL preview vertex buffer unavailable")
        self._buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        # Qt 6.6's external-type SIP callback can crash returning an FBO to
        # Qt Quick. Let Qt allocate its presentation FBO in C++; our private
        # depth FBO stays Python-owned and its colour texture is copied on GPU.
        address = context.getProcAddress(b"glBindFramebuffer") or context.getProcAddress(b"glBindFramebufferEXT")
        if not address: raise RuntimeError("OpenGL framebuffer binding unavailable")
        self._bind_framebuffer = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint)(int(address))
        self._blitter = QOpenGLTextureBlitter()
        if not self._blitter.create(): raise RuntimeError("OpenGL texture copy unavailable")

    def render(self):
        if self._window is None or self._failed: return
        target = viewport = None
        begun = present = False
        try:
            self._window.beginExternalCommands()
            begun = True
            context = QOpenGLContext.currentContext()
            if context is not self._context:
                self._discard_resources()
                self._context = context
            if self._gl is None: self._initialize()
            gl = self._gl
            target = int(gl.glGetIntegerv(0x8CA6))  # current Qt draw FBO
            viewport = gl.glGetIntegerv(0x0BA2)
            size = QSize(int(viewport[2]), int(viewport[3]))
            if viewport[2] <= 0 or viewport[3] <= 0: return
            if self._depth_fbo is None or self._depth_fbo.size() != size:
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                self._depth_fbo = QOpenGLFramebufferObject(size, format_)
                if not self._depth_fbo.isValid(): raise RuntimeError("OpenGL depth framebuffer unavailable")
            if not self._depth_fbo.bind(): raise RuntimeError("OpenGL depth framebuffer could not bind")
            gl.glViewport(0, 0, int(viewport[2]), int(viewport[3]))
            gl.glDisable(0x0C11)  # scissor
            gl.glDisable(0x0B44)  # culling: picking includes both face sides
            gl.glColorMask(True, True, True, True)
            gl.glDepthMask(True)
            gl.glClearColor(0, 0, 0, 0)
            gl.glClear(0x4000 | 0x0100)  # colour + private depth
            present = True
            if self._width <= 0 or self._height <= 0: return
            if self._packed is None or self._camera is None: return
            self._vao.bind()
            if not self._buffer.bind(): raise RuntimeError("OpenGL preview vertex buffer could not bind")
            if self._uploaded is not self._packed:
                data = self._packed[0]
                self._buffer.allocate(data, len(data))
                self._uploaded = self._packed
            program = self._program
            if not program.bind(): raise RuntimeError("OpenGL preview shader could not bind")
            for name, offset, count in (("a_vertex", 0, 3), ("a_normal", 12, 3), ("a_color", 24, 4), ("a_surface", 40, 1)):
                program.enableAttributeArray(name)
                program.setAttributeBuffer(name, 0x1406, offset, count, 44)
            centre, matrix, scale = self._camera
            radius = self._packed[2]
            # Match the logical-pixel camera/picker; physical FBO dimensions
            # are supplied by Qt and need no second DPR multiplication.
            right, negative_up, depth = matrix.T
            projection = QMatrix4x4(
                *(list(right * 2*scale/self._width) + [(-float(right @ centre)*scale+self._pan[0])*2/self._width] +
                  list(-negative_up * 2*scale/self._height) + [(float(negative_up @ centre)*scale-self._pan[1])*2/self._height] +
                  list(-depth / (radius*2)) + [float(depth @ centre)/(radius*2)] + [0, 0, 0, 1]))
            identity = QMatrix4x4()
            for name in ("u_modelMatrix", "u_viewMatrix", "u_normalMatrix"):
                program.setUniformValue(name, identity)
            program.setUniformValue("u_projectionMatrix", projection)
            program.setUniformValue("u_opacity", 1.0)
            program.setUniformValue("u_lightingEnabled", 1)
            program.setUniformValue("u_attachedCount", len(self._lights))
            for index, light in enumerate(self._lights):
                position, direction, colour, reach = light_values(light)
                for name, vector in (("Position", position), ("Direction", direction), ("Colour", colour)):
                    program.setUniformValue("u_attached"+name+"["+str(index)+"]", QVector3D(*vector))
                program.setUniformValue("u_attachedRange["+str(index)+"]", float(reach))
                program.setUniformValue("u_attachedSurface["+str(index)+"]", float(light['surface']))
                rgb = tuple(int(light['colour'][j:j+2], 16)/255 for j in (1, 3, 5))
                program.setUniformValue("u_attachedPaint["+str(index)+"]", QVector4D(*rgb, float(light['paint'])))
            program.setUniformValue("u_viewPosition", QVector3D(*(centre+depth*radius*4)))
            for index, (position, direction) in enumerate((
                    (centre+(-radius, 0, radius), (1, 0, -1)),
                    (centre+(radius, 0, radius), (-1, 0, -1)),
                    (centre+(0, -radius, radius), (0, 1, -1)),
                    (centre+(0, radius, radius), (0, -1, -1)))):
                program.setUniformValue("u_light"+str(index), QVector3D(*position))
                program.setUniformValue("u_direction"+str(index), QVector3D(*direction))
            gl.glEnable(0x0B71)  # depth test
            gl.glDepthFunc(0x0201)  # LESS
            gl.glDisable(0x0BE2)  # blend
            # Depth prepass makes translucent CAD faces independent of source
            # triangle order. Compose the nearest surface with premultiplied
            # framebuffer alpha, as Qt Quick expects for this texture.
            gl.glColorMask(False, False, False, False)
            program.setUniformValue("u_depthOnly", 1)
            gl.glDrawArrays(0x0004, 0, int(self._packed[3]))
            program.setUniformValue("u_depthOnly", 0)
            gl.glColorMask(True, True, True, True)
            gl.glDepthFunc(0x0203)  # LEQUAL
            gl.glDepthMask(False)
            gl.glEnable(0x0BE2)
            gl.glBlendFuncSeparate(0x0302, 0x0303, 1, 0x0303)
            gl.glDrawArrays(0x0004, 0, int(self._packed[3]))
        except Exception as error:
            present = False
            self._fail(error)
        finally:
            # Every cleanup action is independent: a release failure must not
            # leave Qt's framebuffer bound incorrectly or skip the external
            # commands boundary. Failed frames are never copied to Qt's texture.
            if begun and self._gl is not None:
                self._safe(lambda: self._gl.glColorMask(True, True, True, True))
                self._safe(lambda: self._gl.glDepthMask(True))
                self._safe(lambda: self._gl.glDepthFunc(0x0201))
                self._safe(lambda: self._gl.glDisable(0x0B71))
                self._safe(lambda: self._gl.glDisable(0x0BE2))
            if begun:
                for resource in (self._program, self._buffer, self._vao):
                    if resource is not None: self._safe(resource.release)
            if target is not None and self._bind_framebuffer is not None:
                self._safe(lambda: self._bind_framebuffer(0x8D40, target))
                if self._gl is not None and viewport is not None:
                    self._safe(lambda: self._gl.glViewport(*viewport))
                if present and not self._failed and self._depth_fbo is not None and self._blitter is not None:
                    try:
                        self._blitter.bind()
                        self._blitter.blit(self._depth_fbo.texture(), QMatrix4x4(),
                                           QOpenGLTextureBlitter.Origin.OriginBottomLeft)
                    except Exception as error:
                        self._fail(error)
                    finally:
                        self._safe(self._blitter.release)
            if begun: self._safe(self._window.endExternalCommands)
