"""Owned bed/platform and public LayerData rendering for reflection probes."""
from __future__ import annotations

import ast
import configparser
from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from .ToolheadPathGeometry import ToolheadPathGeometry
from .ToolheadSceneLighting import layer_range, create_path_shader
from .ToolheadEnvironment import ProbeDescriptor

PATH_CHUNK = 32768  # index elements; native tube emission is bounded per turn
MAX_BED_VERTICES = 250000
BOUNDS_CHUNK = 65536
VIEW_FIELDS = (("u_show_travel_moves", "getShowTravelMoves"), ("u_show_helpers", "getShowHelpers"),
               ("u_show_skin", "getShowSkin"), ("u_show_infill", "getShowInfill"),
               ("u_show_starts", "getShowStarts"), ("u_layer_view_type", "getSimulationViewType"),
               ("u_extruder_opacity", "getExtruderOpacities"))


def view_uniforms(view):
    values = {name: getattr(view, getter)() for name, getter in VIEW_FIELDS}
    for metric, getter in (("feedrate", "Feedrate"), ("thickness", "Thickness"),
                           ("line_width", "LineWidth"), ("flow_rate", "FlowRate")):
        for prefix in ("min", "max"):
            values["u_" + prefix + "_" + metric] = getattr(view, "get" + prefix.title() + getter)()
    return values


def owned_shader(path):
    """Compile from public resources; never mutate a native shader instance."""
    from UM.View.GL.OpenGLContext import OpenGLContext
    from UM.View.GL.ShaderProgram import ShaderProgram
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    parser.optionxform = str
    if not parser.read(path, encoding="utf-8"): raise RuntimeError("Environment shader resource unavailable")
    variant = "" if OpenGLContext.isLegacyOpenGL() else "41core"
    shader = ShaderProgram()
    vertex, fragment = (parser["shaders"][stage + variant] for stage in ("vertex", "fragment"))
    geometry = parser["shaders"].get("geometry" + variant)
    if not shader.setVertexShader(vertex) or not shader.setFragmentShader(fragment):
        raise RuntimeError("Environment scene shader could not compile")
    if geometry and not shader.setGeometryShader(geometry): raise RuntimeError("Environment path shader could not compile")
    shader.build()
    for name, value in parser.items("defaults") if parser.has_section("defaults") else []:
        shader.setUniformValue(name, ast.literal_eval(value))
    for name, value in parser["bindings"].items(): shader.addBinding(name, value)
    return shader


def probe_camera(origin, face, light, near=.2, far=10000.):
    from UM.Math.Matrix import Matrix
    from UM.Math.Vector import Vector
    directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    ups = ((0, -1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0), (0, -1, 0))
    forward, up = np.asarray(directions[face]), np.asarray(ups[face])
    right = np.cross(forward, up)
    view = np.eye(4)
    view[:3, :3] = np.stack((right, np.cross(right, forward), -forward))
    view[:3, 3] = -view[:3, :3] @ origin
    # UM's historical setPerspective uses another matrix convention. Build
    # the standard column-vector 90-degree cube projection explicitly.
    projection = Matrix(np.array(((1, 0, 0, 0), (0, 1, 0, 0),
        (0, 0, (far+near)/(near-far), 2*far*near/(near-far)), (0, 0, -1, 0))))
    return SimpleNamespace(getInverseWorldTransformation=lambda: Matrix(view),
        getProjectionMatrix=lambda: projection, getWorldPosition=lambda: Vector(*origin),
        getCameraLightPosition=lambda: light)


class EnvironmentSnapshot:
    def __init__(self, owner, origin, light, plates, paths, uniforms, compatibility):
        self.owner, self.origin, self.light = owner, tuple(origin), light
        self.plates, self.paths = plates, paths
        self.uniforms, self.compatibility = uniforms, compatibility
        self.descriptor = None
        self.lighting = {}
        self.light_effects = (False, False)
        self.path_top = {}

    def prepare(self):
        """Reduce immutable mesh bounds once, in bounded render-thread turns."""
        entries = [(item["mesh"], item["transformation"]) for _shader, item, _settings in self.plates]
        entries += [(geometry.mesh, transform) for geometry, transform, *_rest in self.paths]
        world = []
        for mesh, transform in entries:
            if mesh not in self.owner._mesh_bounds:
                vertices = mesh.getVertices()
                low, high = np.full(3, np.inf), np.full(3, -np.inf)
                for start in range(0, len(vertices), BOUNDS_CHUNK):
                    def reduce(start=start, vertices=vertices, low=low, high=high):
                        points = vertices[start:start + BOUNDS_CHUNK]
                        if not np.isfinite(points).all(): raise RuntimeError("Invalid reflection scene vertices")
                        low[:] = np.minimum(low, points.min(axis=0))
                        high[:] = np.maximum(high, points.max(axis=0))
                    yield reduce
                self.owner._mesh_bounds[mesh] = (low, high)
            low, high = self.owner._mesh_bounds[mesh]
            matrix = np.asarray(transform.getData())
            centre, extent = (low + high) / 2, (high - low) / 2
            centre = matrix[:3, :3] @ centre + matrix[:3, 3]
            extent = np.abs(matrix[:3, :3]) @ extent
            world.append((centre - extent, centre + extent))
        padding = max(2., float(self.uniforms.get("u_max_thickness", 0)) * 2,
                      float(self.uniforms.get("u_max_line_width", 0)) * 2)
        low = np.min([entry[0] for entry in world], axis=0) - padding if world else np.asarray(self.origin) - 1
        high = np.max([entry[1] for entry in world], axis=0) + padding if world else np.asarray(self.origin) + 1
        reach = float(np.linalg.norm(np.maximum(abs(low - self.origin), abs(high - self.origin)))) + 1
        self.descriptor = ProbeDescriptor(self.origin, tuple(low), tuple(high), far=max(2., reach))

    def commands(self, face):
        from UM.View.RenderBatch import RenderBatch
        descriptor = self.descriptor
        camera = probe_camera(self.origin, face, self.light, descriptor.near, descriptor.far)
        for shader, item, settings in self.plates:
            def plate(gl, shader=shader, item=item, settings=settings):
                for name, value in settings.items(): shader.setUniformValue(name, value)
                batch = RenderBatch(shader, type=RenderBatch.RenderType.Transparent, backface_cull=False,
                                    state_setup_callback=lambda bindings: (
                                        bindings.glDepthMask(True), bindings.glDepthFunc(bindings.GL_LESS),
                                        bindings.glBlendEquation(bindings.GL_FUNC_ADD)))
                batch.addItem(item["transformation"], mesh=item["mesh"], uniforms=item.get("uniforms"),
                              normal_transformation=item.get("normal_transformation"))
                try: batch.render(camera)
                finally: shader.release()
            yield plate
        for geometry, transform, bounds, partial, shadow in self.paths:
            # Native shadow geometry has distinct helper/start-marker rules,
            # not merely grey paint. Keep its shader and range separate.
            spans = ((bounds[0], min(shadow, bounds[1]), True),
                     (max(bounds[0], shadow), bounds[1], False))
            for first, last, is_shadow in spans:
                if last <= first: continue
                shader = self.owner.path_shader(self.compatibility, is_shadow)
                fragment = None if is_shadow else partial
                for start in range(first, last, PATH_CHUNK):
                    end = min(start + PATH_CHUNK, last)
                    def path(gl, geometry=geometry, transform=transform, start=start, end=end, partial=fragment, shader=shader):
                        for name, value in self.uniforms.items(): shader.setUniformValue(name, value)
                        shader.setUniformValue("u_last_vertex", partial[0] if partial else [math.nan] * 3)
                        shader.setUniformValue("u_next_vertex", partial[1] if partial else [math.nan] * 3)
                        shader.setUniformValue("u_last_line_ratio", partial[2] if partial else 1.)
                        gl.glEnable(gl.GL_DEPTH_TEST)
                        gl.glDepthMask(True)
                        gl.glDepthFunc(gl.GL_LESS)
                        gl.glDisable(gl.GL_BLEND)
                        gl.glDisable(gl.GL_CULL_FACE)
                        geometry.render(shader, camera, transform, [(start, end)], gl)
                    yield path


        # The same attached-light composition as the visible scene, frozen for
        # all six faces. It changes colour only; the paired depth stays native.
        if not self.lighting or not any(self.light_effects): return
        light_bed, light_models = self.light_effects
        if light_bed:
            shader = self.owner.light_mesh_shader()
            for _native, item, settings in self.plates:
                if not settings: continue  # Native platform volume is not the bed receiver.
                mesh = self.owner.light_receiver(item['mesh'])
                def lit_plate(gl, mesh=mesh, item=item, shader=shader):
                    self._light_state(gl, shader)
                    shader.setUniformValue('u_hasColour', 0)
                    batch = RenderBatch(shader, type=RenderBatch.RenderType.Solid, backface_cull=True,
                        state_setup_callback=lambda bindings: self._light_state(bindings, shader))
                    batch.addItem(item['transformation'], mesh=mesh, normal_transformation=item.get('normal_transformation'))
                    try: batch.render(camera)
                    finally: shader.release()
                yield lit_plate
        if light_models:
            shader = self.owner.light_path_shader(self.compatibility)
            for geometry, transform, bounds, partial, shadow in self.paths:
                for start in range(bounds[0], bounds[1], PATH_CHUNK):
                    end = min(start + PATH_CHUNK, bounds[1])
                    def lit_path(gl, geometry=geometry, transform=transform, start=start, end=end, partial=partial, shadow=shadow):
                        self._light_state(gl, shader)
                        for name, value in self.uniforms.items(): shader.setUniformValue(name, value)
                        shader.setUniformValue('u_lightingFirstTopElement', self.path_top[id(geometry)])
                        shader.setUniformValue('u_lightingShadowElements', shadow)
                        shader.setUniformValue('u_last_vertex', partial[0] if partial else [math.nan]*3)
                        shader.setUniformValue('u_next_vertex', partial[1] if partial else [math.nan]*3)
                        shader.setUniformValue('u_last_line_ratio', partial[2] if partial else 1.)
                        geometry.render(shader, camera, transform, [(start, end)], gl)
                    yield lit_path

    def _light_state(self, gl, shader):
        for name, value in self.lighting.items(): shader.setUniformValue(name, value)
        shader.setUniformValue('u_depthOnly', 0)
        shader.setUniformValue('u_orthographic', 0)  # Cube faces always use perspective rays.
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glEnable(gl.GL_CULL_FACE)
        gl.glCullFace(gl.GL_BACK)
        gl.glFrontFace(gl.GL_CCW)
        gl.glDepthFunc(gl.GL_LEQUAL)
        gl.glDepthMask(False)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendEquation(gl.GL_FUNC_ADD)
        gl.glBlendFuncSeparate(gl.GL_SRC_ALPHA, gl.GL_ONE, gl.GL_ZERO, gl.GL_ONE)


class ToolheadEnvironmentScene:
    def __init__(self):
        self._batches = ()
        self._geometry = {}
        self._shaders = {}
        self._texture = None
        self._platform_key = None
        self._prefix = None
        self._scrub_epoch = 0
        self._plate_kinds = {}
        self._mesh_bounds = {}
        self._light_receivers = {}

    def path_shader(self, compatibility, shadow=False):
        from UM.PluginRegistry import PluginRegistry
        key = ("paths", compatibility, shadow)
        if key not in self._shaders:
            directory = Path(PluginRegistry.getInstance().getPluginPath("SimulationView"))
            name = "layers" if compatibility else "layers3d"
            self._shaders[key] = owned_shader(directory / (name + ("_shadow" if shadow else "") + ".shader"))
        return self._shaders[key]

    def mesh_shader(self, kind):
        from UM.Resources import Resources
        if kind not in self._shaders:
            self._shaders[kind] = owned_shader(Resources.getPath(Resources.Shaders, kind + ".shader"))
        return self._shaders[kind]

    def light_mesh_shader(self):
        from ..resources.PluginPaths import plugin_path
        if 'light-mesh' not in self._shaders:
            self._shaders['light-mesh'] = owned_shader(plugin_path('toolhead', 'scene-lighting.shader'))
        return self._shaders['light-mesh']

    def light_path_shader(self, compatibility):
        key = ('light-path', compatibility)
        if key not in self._shaders: self._shaders[key] = create_path_shader(compatibility)
        return self._shaders[key]

    def light_receiver(self, mesh):
        from UM.Mesh.MeshData import MeshData
        if mesh not in self._light_receivers:
            vertices = mesh.getVertices()
            self._light_receivers[mesh] = MeshData(vertices=vertices, indices=mesh.getIndices(),
                normals=np.tile([0, 1, 0], (len(vertices), 1)).astype(np.float32))
        return self._light_receivers[mesh]

    def signature(self, renderer, view, root):
        if view.getCompatibilityMode():
            # Legacy top-layer mesh and flat-line modes differ from tubes.
            # Until exact scene parity is available, retain ordinary shading.
            raise RuntimeError("Reflections require the normal Preview renderer")
        live = tuple(renderer.getBatches())
        if live: self._batches = live
        uniforms = view_uniforms(view)
        data = tuple((child.callDecoration("getLayerData"), np.asarray(child.getWorldTransformation().getData(), dtype=np.float64).tobytes(), child.isVisible())
                     for child in root.getAllChildren() if child.callDecoration("getLayerData") is not None and not getattr(child, "isOutsideBuildArea", lambda: False)() and not child.callDecoration("isAssignedToDisabledExtruder"))
        from UM.Application import Application
        app = Application.getInstance()
        theme = app.getTheme()
        uniforms["u_starts_color"] = [value/255 for value in theme.getColor("layerview_starts").getRgb()]
        colours = tuple(tuple(theme.getColor(name).getRgb()) for name in ("buildplate", "buildplate_grid", "buildplate_grid_minor"))
        stack = app.getGlobalContainerStack()
        container = stack.findContainer({"platform_texture": "*"}) if stack is not None else None
        texture = container.getMetaDataEntry("platform_texture") if container is not None else ""
        prefix = int(view.getCurrentLayer()), float(view.getCurrentPath())
        if self._prefix is not None and prefix < self._prefix: self._scrub_epoch += 1
        self._prefix = prefix
        provider = renderer.getRenderPass("simulationview")
        shadow = getattr(provider, "getCompletedLayerShadowMode", lambda: None)()
        if shadow is None and any(visible for _mesh, _transform, visible in data):
            raise RuntimeError("Native Preview path shadow state unavailable")
        hard = (root, view, self._scrub_epoch, bool(shadow), tuple((id(mesh), transform, visible) for mesh, transform, visible in data),
                repr(uniforms), bool(view.getCompatibilityMode()), int(view.getMinimumLayer()), colours, texture,
                tuple((id(item["mesh"]), item["transformation"].getData().tobytes())
                      for batch in self._batches for item in batch.items if batch.renderMode == 4))
        return hard, data, uniforms, colours, texture

    def snapshot(self, node, renderer, camera, signature):
        from UM.Math.Matrix import Matrix
        from UM.Application import Application
        from UM.Scene.Platform import Platform
        from UM.Resources import Resources
        from UM.View.GL.OpenGL import OpenGL
        from PyQt6.QtGui import QImageReader
        _hard, data, uniforms, colours, texture = signature
        view, root = node._view, node._root
        if texture != self._platform_key:
            # Load Cura's public platform resource into our own texture.
            owned_texture = OpenGL.getInstance().createTexture()
            if texture:
                path = Resources.getPath(Resources.Images, texture)
                if Path(path).stat().st_size > 16 * 1024 * 1024: raise RuntimeError("Platform texture exceeds reflection budget")
                reader = QImageReader(path)
                size = reader.size()
                if not size.isValid() or size.width()*size.height() > 16*1024*1024:
                    raise RuntimeError("Platform texture exceeds reflection pixel budget")
                image = reader.read()
                if image.isNull() or image.width()*image.height() > 16*1024*1024: raise RuntimeError("Platform texture unavailable")
                owned_texture.setImage(image.mirrored())
            self._platform_key, self._texture = texture, owned_texture
        platforms = {child.getMeshData() for child in root.getAllChildren() if isinstance(child, Platform)}
        plates = []
        for batch in self._batches:
            if batch.renderMode != 4: continue
            for item in batch.items:
                mesh = item["mesh"]
                if mesh not in self._plate_kinds:
                    kind = None
                    # Bound classification before any whole-array operation.
                    if mesh in platforms or mesh.hasUVCoordinates():
                        vertices = mesh.getVertices()
                        if vertices is not None and 3 <= len(vertices) <= MAX_BED_VERTICES:
                            extent = np.ptp(vertices, axis=0)
                            if mesh in platforms: kind = "platform"
                            elif mesh.hasUVCoordinates() and extent[1] < .02 and extent[0] > node.light_dimensions()[0]*.7 and extent[2] > node.light_dimensions()[1]*.7:
                                kind = "grid"
                    self._plate_kinds[mesh] = kind
                kind = self._plate_kinds[mesh]
                if kind is None: continue
                is_grid = kind == "grid"
                shader = self.mesh_shader(kind)
                settings = {"u_plateColor": [value/255 for value in colours[0]],
                            "u_gridColor0": [value/255 for value in colours[1]],
                            "u_gridColor1": [value/255 for value in colours[2]]} if is_grid else {}
                if is_grid:
                    # Match native textured-bed blending over the platform.
                    settings["u_plateColor"][3] = .5 if Application.getInstance().getGlobalContainerStack().getMetaDataEntry("has_textured_buildplate", False) else 1.
                else: shader.setTexture(0, self._texture)
                copied = dict(item, transformation=Matrix(item["transformation"].getData()))
                plates.append((shader, copied, settings))
        shadow = _hard[3]
        used, paths = set(), []
        for mesh, raw_transform, visible in data:
            if not visible: continue
            bounds = layer_range(mesh, view)
            if bounds is None or bounds[1] <= bounds[0]: continue
            used.add(mesh)
            if mesh not in self._geometry: self._geometry[mesh] = ToolheadPathGeometry(mesh, build_bounds=False, index_chunk=PATH_CHUNK)
            transform = Matrix(np.frombuffer(raw_transform, dtype=np.float64).reshape(4, 4))
            before = sum(count for layer, count in mesh.getElementCounts().items() if layer < int(view.getCurrentLayer())) if shadow else 0
            paths.append((self._geometry[mesh], transform, bounds[:2], bounds[2], int(before)))
        self._geometry = {key: value for key, value in self._geometry.items() if key in used}
        meshes = {item["mesh"] for batch in self._batches for item in batch.items}
        self._plate_kinds = {mesh: kind for mesh, kind in self._plate_kinds.items() if mesh in meshes}
        self._light_receivers = {mesh: receiver for mesh, receiver in self._light_receivers.items() if mesh in meshes}
        self._mesh_bounds = {mesh: bounds for mesh, bounds in self._mesh_bounds.items() if mesh in meshes or mesh in used}
        point = node.render_position()
        origin = np.array((point.x, point.y, point.z)) + np.mean(node._model_bounds, axis=0)
        snapshot = EnvironmentSnapshot(self, origin, camera.getCameraLightPosition(), plates, paths,
                                   uniforms, bool(view.getCompatibilityMode()))
        if getattr(node, '_lighting_enabled', False) and getattr(node, '_attached_lights', ()):
            node.apply_attached_lights(SimpleNamespace(setUniformValue=lambda name, value: snapshot.lighting.__setitem__(name, deepcopy(value))))
            snapshot.light_effects = node.scene_lighting_effects()
            for geometry, _transform, bounds, _partial, _shadow in paths:
                snapshot.path_top[id(geometry)] = max(bounds[0], sum(count for layer, count in geometry.mesh.getElementCounts().items() if layer < int(view.getCurrentLayer())))
        return snapshot
