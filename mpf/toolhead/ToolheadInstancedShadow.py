"""Bounded instancing for fingerprint-matched native shadow lower layers.

The 26-vertex template follows Cura's LGPL stock tube geometry. Native current,
fractional and visible travel paths continue using the original geometry shader.
"""
from __future__ import annotations

import configparser
import ctypes
import hashlib

import numpy as np

SHADOW_DIGEST = "3f6df3da984abb98926b24da6bf83f43d7fcdea264406d7bd826c7d25a0d4dcd"
MAX_INDEX_BYTES = 128 * 1024 * 1024
VERTEX_SOURCE = """#version 410
uniform samplerBuffer u_vertices;
uniform usamplerBuffer u_lineIndices;
uniform int u_dimensionOffset;
uniform int u_typeOffset;
uniform int u_extruderOffset;
uniform mat4 u_extruder_opacity;
uniform int u_show_travel_moves;
uniform int u_show_helpers;
uniform int u_lineStart;
uniform int u_show_skin;
uniform int u_show_infill;
uniform mat4 u_modelMatrix;
uniform mat4 u_viewMatrix;
uniform mat4 u_projectionMatrix;
out vec4 f_color;
out vec3 f_vertex;
out vec3 f_normal;
float scalar(int index) { return texelFetch(u_vertices,index).r; }
vec3 point(int index) { return vec3(scalar(index*3),scalar(index*3+1),scalar(index*3+2)); }
vec2 dimension(int index) { return vec2(scalar(u_dimensionOffset+index*2),scalar(u_dimensionOffset+index*2+1)); }
const int endpoint[26]=int[26](0,1,0,1,0,1,0,1,0,1,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1);
const int side[26]=int[26](0,0,1,1,2,2,3,3,0,0,0,1,4,2,2,3,4,0,2,1,5,0,0,3,5,2);
void main() {
 int line=u_lineStart+gl_InstanceID*2;
 int a=int(texelFetch(u_lineIndices,line).r), b=int(texelFetch(u_lineIndices,line+1).r);
 vec2 da=dimension(a), db=dimension(b);
 vec4 va=vec4(point(a),1.0), vb=vec4(point(b),1.0);
 va.y -= da.y / 2.0; vb.y -= db.y / 2.0;
 va=u_modelMatrix*va; vb=u_modelMatrix*vb;
 vec3 delta=(vb-va).xyz;
 int kind=int(scalar(u_typeOffset+a));
 int extruder=int(scalar(u_extruderOffset+a));
 bool travel=kind==8 || kind==9 || kind==12 || kind==13;
 if (delta == vec3(0.0) || (u_extruder_opacity[extruder%4][extruder/4]==0.0 && !travel) || (travel && u_show_travel_moves==0) || (u_show_helpers==0 && (kind==4 || kind==5 || kind==7 || kind==10)) || (u_show_skin==0 && (kind==1 || kind==2 || kind==3)) || (kind==6 && u_show_infill==0)) {
  gl_Position=vec4(0.0,0.0,2.0,1.0);f_color=vec4(0.0);f_vertex=vec3(0.0);f_normal=vec3(0.0,1.0,0.0);return;
 }
 vec3 radial;
 if (delta.y==0.0) radial=vec3(delta.z,0.0,-delta.x);
 else if (delta.x==0.0 && delta.z==0.0) radial=vec3(1.0,0.0,-1.0);
 else radial=cross(delta,vec3(delta.x,0.0,delta.z));
 vec3 head=normalize(delta), horizontal=normalize(radial), vertical=vec3(0.0,1.0,0.0);
 float sx=db.x / 2.0 + 0.01, sy=db.y / 2.0 + 0.01;
 vec3 normals[6]=vec3[6](-horizontal,vertical,horizontal,-vertical,-head,head);
 vec4 offsets[6]=vec4[6](-vec4(horizontal*sx,0.0),vec4(vertical*sy,0.0),vec4(horizontal*sx,0.0),-vec4(vertical*sy,0.0),-vec4(head*sx,0.0),vec4(head*sx,0.0));
 vec4 centre=endpoint[gl_VertexID]==0 ? va : vb;
 mat4 viewProjectionMatrix=u_projectionMatrix*u_viewMatrix;
 gl_Position=viewProjectionMatrix*(centre+offsets[side[gl_VertexID]]);
 // Native shading position is the centreline, separately from tube position.
 f_vertex=centre.xyz;f_normal=normals[side[gl_VertexID]];f_color=vec4(0.4,0.4,0.4,0.9);
}
"""


def shadow_sources(path):
    """Accept the tested native program by content, independent of install path."""
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
    parser.optionxform = str
    with open(path, encoding='utf-8') as stream: parser.read_file(stream)
    source = '\n\0\n'.join(parser['shaders'][name] for name in
        ('vertex41core', 'geometry41core', 'fragment41core'))
    if hashlib.sha256(source.encode()).hexdigest() != SHADOW_DIGEST: return None
    return parser['shaders']['fragment41core']


def mesh_layout(mesh, reject=None):
    """Validate native SOA storage once without converting or expanding arrays."""
    def decline(reason):
        if reject is not None: reject(reason)
        return None

    count = int(mesh.getVertexCount())
    vertices = np.asarray(mesh.getVertices())
    indices = np.asarray(mesh.getIndices())
    if (count <= 0 or vertices.shape != (count, 3) or vertices.dtype != np.float32
            or not vertices.flags.c_contiguous or indices.dtype not in (np.dtype(np.uint32), np.dtype(np.int32))
            or indices.ndim != 1 or not indices.flags.c_contiguous
            or len(indices) % 2): return decline("native vertex/index layout")
    if indices.nbytes > MAX_INDEX_BYTES:
        return decline(f"index bytes {indices.nbytes} exceed budget {MAX_INDEX_BYTES}")
    for first in range(0, len(indices), 65536):
        part = indices[first:first + 65536]
        if np.any(part < 0) or np.any(part >= count): return decline("index outside vertex range")
    for first in range(0, count, 65536):
        if not np.isfinite(vertices[first:first + 65536]).all(): return decline("nonfinite vertices")
    offset = count * 3
    for present, getter, size in ((mesh.hasNormals(), 'getNormals', 3),
            (mesh.hasColors(), 'getColorsAsByteArray', 4),
            (mesh.hasUVCoordinates(), 'getUVCoordinates', 2)):
        if present:
            if getter == 'getColorsAsByteArray':
                # Native getColors() raises on ndarray truth evaluation. Shadow
                # shading never reads colors; match the public native upload's
                # byte count and release this one-time temporary immediately.
                body = mesh.getColorsAsByteArray()
                valid = isinstance(body, (bytes, bytearray, memoryview)) and len(body) == count * size * 4
                del body
                if not valid: return decline("native color byte count")
            else:
                values = np.asarray(getattr(mesh, getter)())
                if values.dtype != np.float32 or values.size != count * size or not values.flags.c_contiguous:
                    return decline(f"native {getter} layout")
            offset += count * size
    names = list(mesh.attributeNames())
    if names != sorted(names): return decline("native attribute ordering")
    sizes = {'float': 1, 'int': 1, 'vector2f': 2, 'vector4f': 4}
    required = {'line_dimensions': ('vector2f', 'a_line_dim'),
                'line_types': ('float', 'a_line_type'), 'extruders': ('float', 'a_extruder')}
    offsets = {}
    for name in names:
        attribute = mesh.getAttribute(name)
        kind = attribute['opengl_type']
        values = np.asarray(attribute['value'])
        if (kind not in sizes or values.dtype.itemsize != 4 or values.size != count * sizes[kind]
                or not values.flags.c_contiguous): return decline(f"attribute {name} layout")
        if name in required:
            if (kind, attribute['opengl_name']) != required[name] or values.dtype != np.float32:
                return decline(f"attribute {name} ABI")
            flat = values.reshape(-1)
            for first in range(0, len(flat), 65536):
                part = flat[first:first + 65536]
                if not np.isfinite(part).all(): return decline(f"attribute {name} nonfinite")
                if name != 'line_dimensions' and (np.any(part != np.floor(part)) or np.any(part < 0)
                        or np.any(part > (15 if name == 'extruders' else 13))): return decline(f"attribute {name} category")
            offsets[name] = offset
        offset += count * sizes[kind]
    if offsets.keys() != required.keys(): return decline("required attributes missing")
    return indices.view(np.uint32), offsets, offset * 4


class _Unsupported(Exception):
    pass


class ToolheadInstancedShadow:
    """Return False for unsupported admission; raise after a failed owned draw."""
    TARGET = 0x8C2A  # GL_TEXTURE_BUFFER

    def __init__(self, *, diagnostic=False):
        self._diagnostic = diagnostic
        self._context = self._mesh = self._storage = self._blocked = None
        self._reported = None

    def _notice(self, text):
        if self._diagnostic and self._reported is not self._context:
            self._reported = self._context
            try:
                from UM.Logger import Logger
                Logger.log('i', 'toolhead shadow instancing: %s', text)
            except Exception: pass

    @staticmethod
    def _proc(context, name, *arguments):
        address = context.getProcAddress(name)
        if not address: raise RuntimeError('Shadow instancing entry point unavailable')
        return ctypes.CFUNCTYPE(None, *arguments)(int(address))

    def close(self):
        from PyQt6.QtGui import QOpenGLContext
        storage, self._storage = self._storage, None
        if storage is not None and QOpenGLContext.currentContext() is self._context:
            try: storage['delete'](2, storage['textures'])
            except Exception: pass
            for item in (storage['vao'], storage['lines'], storage['template']):
                try: item.destroy()
                except Exception: pass
        self._mesh = self._blocked = None

    @staticmethod
    def _state(gl):
        active = int(gl.glGetIntegerv(0x84E0))
        textures = []
        try:
            for unit in (0, 1):
                gl.glActiveTexture(0x84C0 + unit)
                textures.append(int(gl.glGetIntegerv(0x8C2C)))
        finally: gl.glActiveTexture(active)
        return (int(gl.glGetIntegerv(0x8B8D)), int(gl.glGetIntegerv(0x85B5)),
                int(gl.glGetIntegerv(0x8894)), active, textures)

    @staticmethod
    def _restore(gl, state):
        program, vao, array, active, textures = state
        failure = None
        operations = []
        for unit, texture in enumerate(textures):
            operations.extend((lambda unit=unit: gl.glActiveTexture(0x84C0 + unit),
                               lambda texture=texture: gl.glBindTexture(0x8C2A, texture)))
        operations.extend((lambda: gl.glActiveTexture(active), lambda: gl.glUseProgram(program),
                           lambda: gl.glBindVertexArray(vao), lambda: gl.glBindBuffer(0x8892, array)))
        for operation in operations:
            try: operation()
            except Exception as error:
                if failure is None: failure = error
        if failure is not None: raise RuntimeError('Shadow GL state restoration failed') from failure

    def _create(self, mesh, path, context, gl):
        from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject
        from UM.View.GL.OpenGL import OpenGL
        from UM.View.GL.ShaderProgram import ShaderProgram
        try:
            fragment = shadow_sources(path)
            if fragment is None:
                self._notice('native fallback (native shader fingerprint)')
                return None
            layout = mesh_layout(mesh, lambda reason: self._notice(f'native fallback ({reason})'))
        except (OSError, configparser.Error, KeyError, TypeError, ValueError, AttributeError) as error:
            self._notice(f'native fallback (source/layout API: {type(error).__name__})')
            return None
        if layout is None: return None
        indices, offsets, expected = layout
        maximum = int(gl.glGetIntegerv(0x8C2B))
        if expected // 4 > maximum or len(indices) > maximum:
            self._notice(f'native fallback (texture texel limit {maximum}; vertex floats {expected // 4}; index elements {len(indices)})')
            return None
        proc = lambda name, *args: self._proc(context, name, *args)
        if any(not context.getProcAddress(name) for name in (b'glGenTextures', b'glDeleteTextures', b'glTexBuffer', b'glDrawElementsInstanced')):
            self._notice('native fallback (GL entry points)')
            return None
        gen = proc(b'glGenTextures', ctypes.c_int, ctypes.POINTER(ctypes.c_uint))
        delete = proc(b'glDeleteTextures', ctypes.c_int, ctypes.POINTER(ctypes.c_uint))
        texture_buffer = proc(b'glTexBuffer', ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
        draw = proc(b'glDrawElementsInstanced', ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
        vertex = OpenGL.getInstance().createVertexBuffer(mesh)
        if vertex is None or int(vertex.bufferId()) <= 0 or not vertex.bind(): raise RuntimeError('Shadow VBO unavailable')
        if vertex.size() != expected:
            self._notice(f'native fallback (native VBO bytes {vertex.size()}; expected {expected})')
            return None
        shader = ShaderProgram()
        if not shader.setVertexShader(VERTEX_SOURCE) or not shader.setFragmentShader(fragment): raise RuntimeError('Shadow shader compilation failed')
        shader.build()
        for name, binding in (('u_modelMatrix', 'model_matrix'), ('u_viewMatrix', 'view_matrix'),
                ('u_projectionMatrix', 'projection_matrix'), ('u_lightPosition', 'light_0_position')):
            shader.addBinding(name, binding)
        vao = QOpenGLVertexArrayObject()
        lines = QOpenGLBuffer()
        template = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        textures = (ctypes.c_uint * 2)()
        try:
            if not vao.create(): raise RuntimeError('Shadow VAO unavailable')
            vao.bind()
            for buffer, body in ((lines, indices.tobytes()), (template, self._pattern().tobytes())):
                if not buffer.create() or not buffer.bind(): raise RuntimeError('Shadow EBO unavailable')
                buffer.allocate(body, len(body))
                if buffer.size() != len(body): raise RuntimeError('Shadow EBO storage incomplete')
            gen(2, textures)
            if not all(textures): raise RuntimeError('Shadow texture allocation failed')
            for texture, kind, buffer in ((textures[0], 0x822E, vertex), (textures[1], 0x8236, lines)):
                gl.glActiveTexture(0x84C0)
                gl.glBindTexture(self.TARGET, int(texture))
                texture_buffer(self.TARGET, kind, int(buffer.bufferId()))
            return dict(vertex=vertex, offsets=offsets, shader=shader, vao=vao, lines=lines,
                        template=template, textures=textures, delete=delete, draw=draw, count=len(indices), vertex_bytes=expected)
        except Exception:
            try: delete(2, textures)
            except Exception: pass
            for item in (vao, lines, template):
                try: item.destroy()
                except Exception: pass
            raise

    @staticmethod
    def _pattern():
        pattern = []
        for first, size in ((0, 10), (10, 4), (14, 4), (18, 4), (22, 4)):
            for point in range(2, size):
                pattern.extend((first + point - 2, first + point - 1, first + point) if point % 2 == 0
                    else (first + point - 1, first + point - 2, first + point))
        return np.asarray(pattern, dtype=np.uint32)

    def render(self, mesh, native_path, camera, transform, start, end, view, gl):
        from PyQt6.QtGui import QOpenGLContext
        context = QOpenGLContext.currentContext()
        if context is None or context.isOpenGLES() or (context.format().majorVersion(), context.format().minorVersion()) < (4, 1): return False
        if context is not self._context:
            self.close()
            self._context, self._reported = context, None
        if (view.getShowTravelMoves() or not gl.glIsEnabled(0x0B44)
                or int(gl.glGetIntegerv(0x0B45)) != 0x0405
                or int(gl.glGetIntegerv(0x0B46)) != 0x0901): return False
        opacity_value = view.getExtruderOpacities()
        opacity = np.asarray(opacity_value.getData() if hasattr(opacity_value, "getData") else opacity_value)
        if opacity.shape != (4, 4) or not np.isfinite(opacity).all(): return False
        if self._blocked is mesh: return False
        # Check before our commands so a host error is never misattributed to
        # instancing. A new error after the owned draw requires full fallback.
        if gl.glGetError():
            self._notice('native fallback (preexisting GL error)')
            return False
        state = self._state(gl)
        shader = None
        failure = None
        result = False
        try:
            if mesh is not self._mesh:
                self.close()
                self._storage = self._create(mesh, native_path, context, gl)
                self._mesh = mesh
                if self._storage is None:
                    self._blocked = mesh
                    self._notice('native fallback (shader/layout/capability)')
                    raise _Unsupported
            storage = self._storage
            start, end = int(start), int(end)
            if start < 0 or end < start or end > storage['count'] or start % 2 or end % 2:
                raise RuntimeError('Shadow instanced range invalid')
            if end - start > 0x7fffffff: raise RuntimeError('Shadow instance count exceeds GL ABI')
            shader = storage['shader']
            for unit, texture in enumerate(storage['textures']):
                gl.glActiveTexture(0x84C0 + unit)
                gl.glBindTexture(self.TARGET, int(texture))
            for name, value in (('u_vertices', 0), ('u_lineIndices', 1), ('u_dimensionOffset', storage['offsets']['line_dimensions']),
                    ('u_typeOffset', storage['offsets']['line_types']), ('u_extruderOffset', storage['offsets']['extruders']),
                    ('u_lineStart', start), ('u_show_travel_moves', 0), ('u_show_helpers', int(view.getShowHelpers())),
                    ('u_show_skin', int(view.getShowSkin())), ('u_show_infill', int(view.getShowInfill())),
                    ('u_extruder_opacity', view.getExtruderOpacities())):
                shader.setUniformValue(name, value)
            storage['vao'].bind()
            if not storage['template'].bind(): raise RuntimeError('Shadow template bind failed')
            gl.glUseProgram(0)
            shader.bind()
            if not int(gl.glGetIntegerv(0x8B8D)): raise RuntimeError('Shadow shader link unavailable')
            shader.updateBindings(model_matrix=transform, view_matrix=camera.getInverseWorldTransformation(),
                projection_matrix=camera.getProjectionMatrix(), light_0_position=camera.getCameraLightPosition())
            storage['draw'](0x0004, 48, 0x1405, ctypes.c_void_p(0), (end - start) // 2)
            if gl.glGetError(): raise RuntimeError('Shadow instancing GL operation failed')
            result = True
            self._notice(f"lower layers admitted (vertices {mesh.getVertexCount()}; VBO bytes {storage['vertex_bytes']}; index bytes {storage['count'] * 4})")
        except _Unsupported: pass
        except Exception as error: failure = error
        finally:
            try:
                if shader is not None: shader.release()
            except Exception as error: failure = failure or error
            try: self._restore(gl, state)
            except Exception as error: failure = failure or error
        if failure is not None:
            self.close()
            raise RuntimeError('Shadow instanced draw failed') from failure
        return result
