"""Main-thread freeze and worker-local reconstruction of scene recipes."""
from __future__ import annotations

import ast
import configparser
import ctypes
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import numpy as np

from .ToolheadCaptureValues import CaptureMesh, BufferLease, freeze_uniform, thaw_uniform
from .ToolheadCaptureGL import RawShaderProgram
from .ToolheadCaptureDraw import CapturePaths, CapturePlates
from .ToolheadEnvironmentScene import EnvironmentSnapshot
from .ToolheadGLState import procedure


@dataclass(frozen=True)
class ShaderRecipe:
    stages: tuple
    defaults: tuple
    bindings: tuple

    @classmethod
    def load(cls, path):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.optionxform = str
        if not parser.read(path, encoding='utf-8'): raise RuntimeError('Reflection shader source unavailable')
        return cls(tuple((stage, parser['shaders'][stage+'41core']) for stage in ('vertex', 'fragment', 'geometry')
            if stage+'41core' in parser['shaders']), tuple((name, freeze_uniform(ast.literal_eval(value)))
            for name, value in (parser.items('defaults') if parser.has_section('defaults') else ())),
            tuple(parser['bindings'].items()))

    def build(self):
        shader = RawShaderProgram()
        try:
            for stage, source in self.stages:
                setter = getattr(shader, 'set'+stage.title()+'Shader')
                if not setter(source): raise RuntimeError('Reflection shader did not compile')
            shader.build()
            for name, value in self.defaults: shader.setUniformValue(name, thaw_uniform(value))
            for name, value in self.bindings: shader.addBinding(name, value)
            return shader
        except Exception:
            shader._shader_program.close(); raise


def light_recipe():
    from .ToolheadSceneLighting import shader_sources
    parser, vertex, geometry, fragment = shader_sources(False, False)
    defaults = []
    for name, value in parser['defaults'].items():
        value = ast.literal_eval(value)
        if name.startswith(('u_min_', 'u_max_')): value = float(value)
        defaults.append((name, freeze_uniform(value)))
    return ShaderRecipe(tuple((name, source) for name, source in (
        ('vertex', vertex), ('geometry', geometry), ('fragment', fragment)) if source is not None),
        tuple(defaults), tuple(parser['bindings'].items()) + (('u_viewPosition', 'view_position'),))


def recipe_for(key):
    from UM.Resources import Resources
    from UM.PluginRegistry import PluginRegistry
    from ..resources.PluginPaths import plugin_path
    if key == 'light-mesh': return ShaderRecipe.load(plugin_path('toolhead', 'scene-lighting.shader'))
    if isinstance(key, tuple):
        if key[0] == 'light-path': return light_recipe()
        if key[0] == 'paths':
            if key[1]: raise RuntimeError('Reflection workers require normal Preview')
            root = Path(PluginRegistry.getInstance().getPluginPath('SimulationView'))
            return ShaderRecipe.load(root / ('layers3d_shadow.shader' if key[2] else 'layers3d.shader'))
        raise RuntimeError('Unknown reflection shader recipe')
    return ShaderRecipe.load(Resources.getPath(Resources.Shaders, key+'.shader'))


@dataclass(frozen=True)
class CaptureFrame:
    origin: tuple
    light: object
    plates: tuple
    paths: tuple
    uniforms: tuple
    lighting: tuple
    light_effects: tuple
    recipes: tuple
    texture: object


class CaptureFreezer:
    """Retain native wrappers on main; worker receives only immutable values."""
    def __init__(self):
        self.meshes, self.recipes = {}, {}
        self._serial = 0
        self._texture_source = object()
        self._texture_value = None

    def mesh(self, source):
        mesh = self.meshes.get(source)
        if mesh is None:
            self._serial += 1
            mesh = self.meshes[source] = CaptureMesh.freeze(source, identity=self._serial)
        return mesh

    def freeze(self, snapshot, context):
        from UM.View.GL.OpenGL import OpenGL
        owner, leases, used, paths, plates = snapshot.owner, [], set(), [], []
        for geometry, transform, bounds, partial, shadow in snapshot.paths:
            upload = geometry.vertex_mesh(context)
            buffer = getattr(upload, OpenGL.VertexBufferProperty, None)
            if buffer is None: return None  # Correct sync capture establishes a missing first upload.
            mesh = self.mesh(upload)
            layout, size = mesh.layout()
            gl = OpenGL.getInstance().getBindingsObject()
            previous = int(gl.glGetIntegerv(0x8894))
            name, recorded = int(buffer.bufferId()), ctypes.c_int()
            try:
                gl.glBindBuffer(0x8892, name)
                procedure(context, 'glGetBufferParameteriv', None, ctypes.c_uint, ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_int))(0x8892, 0x8764, ctypes.byref(recorded))
            finally: gl.glBindBuffer(0x8892, previous)
            lease = BufferLease(name, int(recorded.value), layout)
            lease.validate(mesh)
            leases.extend((buffer, upload)); used.add(upload)
            paths.append((mesh, lease, freeze_uniform(transform), tuple(map(int, bounds)),
                freeze_uniform(partial) if partial else None, int(shadow), int(snapshot.path_top.get(id(geometry), bounds[0]))))
        shader_keys = {id(shader): key for key, shader in owner._shaders.items()}
        for shader, item, settings in snapshot.plates:
            key = shader_keys[id(shader)]
            mesh = self.mesh(item['mesh']); used.add(item['mesh'])
            plates.append((key, mesh, freeze_uniform(item['transformation']),
                freeze_uniform(item['normal_transformation']) if item.get('normal_transformation') is not None else None,
                tuple((name, freeze_uniform(value)) for name, value in (item.get('uniforms') or {}).items()),
                tuple((name, freeze_uniform(value)) for name, value in settings.items())))
        keys = {entry[0] for entry in plates}
        if paths: keys.update((('paths', False, False), ('paths', False, True)))
        if snapshot.lighting:
            if snapshot.light_effects[0]: keys.add('light-mesh')
            if snapshot.light_effects[1] and paths: keys.add(('light-path', False))
        for key in keys:
            if key not in self.recipes: self.recipes[key] = recipe_for(key)
        texture_source = owner._texture
        image = texture_source.getImage() if texture_source is not None and hasattr(texture_source, 'getImage') else None
        texture_key = texture_source, image.cacheKey() if image is not None else None
        texture = self._texture_value if texture_key == self._texture_source else None
        if texture_key != self._texture_source and image is not None:
            from PyQt6.QtGui import QImage
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            if image.isNull() or image.width()*image.height() > 16*1024*1024:
                raise RuntimeError('Reflection platform texture exceeds budget')
            texture = image.width(), image.height(), image.constBits().asstring(image.sizeInBytes())
        self._texture_source, self._texture_value = texture_key, texture
        self.meshes = {key: value for key, value in self.meshes.items() if key in used}
        frame = CaptureFrame(snapshot.origin, freeze_uniform(snapshot.light), tuple(plates), tuple(paths),
            tuple((name, freeze_uniform(value)) for name, value in snapshot.uniforms.items()),
            tuple((name, freeze_uniform(value)) for name, value in snapshot.lighting.items()), snapshot.light_effects,
            tuple((key, self.recipes[key]) for key in keys), texture)
        return frame, tuple(leases)


class CaptureScene:
    """Worker-owned reconstruction; CPU algorithms match synchronous capture."""
    def __init__(self, gl, context, texture_factory):
        self.gl, self.context, self.texture_factory = gl, context, texture_factory
        self._mesh_bounds, self._path_bounds, self._path_edges = {}, {}, {}
        self.shaders, self.paths, self.receivers = {}, {}, {}
        self.plates = CapturePlates(gl, context)
        self.texture = None
        self.texture_key = None

    def path_shader(self, compatibility, shadow=False): return self.shaders[('paths', compatibility, shadow)]
    def light_path_shader(self, compatibility): return self.shaders[('light-path', compatibility)]
    def light_mesh_shader(self): return self.shaders['light-mesh']

    def light_receiver(self, mesh):
        receiver = self.receivers.get(mesh.identity)
        if receiver is None:
            normals = np.tile([0, 1, 0], (mesh.getVertexCount(), 1)).astype(np.float32)
            normals.flags.writeable = False
            receiver = self.receivers[mesh.identity] = CaptureMesh(-mesh.identity, mesh.vertices, mesh.indices,
                normals, None, None, ())
        return receiver

    def snapshot(self, frame):
        for key, recipe in frame.recipes:
            if key not in self.shaders: self.shaders[key] = recipe.build()
        if self.texture is None or frame.texture != self.texture_key:
            if self.texture is not None: self.texture.close()
            self.texture = self.texture_factory(frame.texture)
            self.texture_key = frame.texture
        if 'platform' in self.shaders: self.shaders['platform'].setTexture(0, self.texture)
        plates, paths, tops, used = [], [], {}, set()
        for key, mesh, transform, normal, uniforms, settings in frame.plates:
            plates.append((self.shaders[key], dict(mesh=mesh, transformation=thaw_uniform(transform),
                normal_transformation=thaw_uniform(normal) if normal else None,
                uniforms={name: thaw_uniform(value) for name, value in uniforms}),
                {name: thaw_uniform(value) for name, value in settings}))
            used.add(mesh)
        live_paths = set()
        for mesh, lease, transform, bounds, partial, shadow, top in frame.paths:
            key = mesh.identity, lease.name, lease.size
            geometry = self.paths.get(key)
            if geometry is None: geometry = self.paths[key] = CapturePaths(mesh, lease, self.gl, self.context)
            live_paths.add(key); used.add(mesh)
            paths.append((geometry, thaw_uniform(transform), bounds, thaw_uniform(partial) if partial else None, shadow))
            tops[id(geometry)] = top
        for key in set(self.paths)-live_paths: self.paths.pop(key).close()
        plate_ids = {entry[1].identity for entry in frame.plates}
        self.receivers = {key: value for key, value in self.receivers.items() if key in plate_ids}
        self.plates.retain(plate_ids | {-key for key in plate_ids})
        for cache in (self._mesh_bounds, self._path_bounds, self._path_edges):
            for mesh in set(cache)-used: cache.pop(mesh)
        snapshot = EnvironmentSnapshot(self, frame.origin, thaw_uniform(frame.light), plates, paths,
            {name: thaw_uniform(value) for name, value in frame.uniforms}, False, plate_renderer=self.plates.draw)
        snapshot.lighting = {name: thaw_uniform(value) for name, value in frame.lighting}
        snapshot.light_effects, snapshot.path_top = frame.light_effects, tops
        return snapshot

    def close(self):
        for geometry in self.paths.values(): geometry.close()
        self.paths.clear(); self.plates.close()
        for shader in self.shaders.values():
            shader.release(); shader._shader_program.close()
        self.shaders.clear()
        if self.texture is not None: self.texture.close()
        self.texture = None


def uniform_sink():
    values = {}
    return values, SimpleNamespace(setUniformValue=lambda name, value: values.__setitem__(name, value))
