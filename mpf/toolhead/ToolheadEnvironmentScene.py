"""Owned bed/platform and public LayerData rendering for reflection probes."""
from __future__ import annotations

import ast
import configparser
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from .ToolheadPathGeometry import ToolheadPathGeometry
from .ToolheadSceneLighting import layer_range, create_path_shader
from .ToolheadEnvironment import EnvironmentNotReady, ProbeDescriptor

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


def directional_camera(descriptor, face, light):
    """One exterior orthographic view, with the original cube face orientation.

    Each face is a separate spatial depth image; its origin is outside the
    scene on the opposite side of its forward axis. No receiver pose is used.
    """
    from UM.Math.Matrix import Matrix
    low,high=np.asarray(descriptor.minimum),np.asarray(descriptor.maximum)
    centre,extent=(low+high)*.5,(high-low)*.5
    axis=face//2;direction=np.zeros(3);direction[axis]=1. if face%2==0 else -1.
    origin=centre-direction*(extent[axis]+descriptor.near+.01)
    camera=probe_camera(origin,face,light,descriptor.near,descriptor.far)
    view=camera.getInverseWorldTransformation().getData()
    width=np.dot(np.abs(view[0,:3]),extent);height=np.dot(np.abs(view[1,:3]),extent)
    near,far=descriptor.near,descriptor.far
    projection=Matrix(np.array(((1/width,0,0,0),(0,1/height,0,0),
        (0,0,-2/(far-near),-(far+near)/(far-near)),(0,0,0,1))))
    camera.getProjectionMatrix=lambda:projection
    return camera


class EnvironmentSnapshot:
    cpu_preparation = True  # prepare() never binds, creates or calls Qt/GL resources.
    def __init__(self, owner, origin, light, plates, paths, uniforms, compatibility, *, plate_renderer=None, projection=0):
        self.owner, self.origin, self.light = owner, tuple(origin), light
        self.plates, self.paths = plates, paths
        self.uniforms, self.compatibility = uniforms, compatibility
        self.descriptor = None
        self.projection = projection
        self.lighting = {}
        self.light_effects = (False, False)
        self.path_top = {}
        self.path_bounds = {}
        self._visibility = {}
        self._turn = self._draw_key = self._draw = None
        self._plate_renderer = plate_renderer

    def _render_plate(self, shader, item, camera, gl, phase):
        if self._plate_renderer is not None:
            self._plate_renderer(shader, item, camera, gl, phase)
            return
        from UM.View.RenderBatch import RenderBatch
        transparent = phase == 'colour'
        setup = (lambda bindings: (bindings.glDepthMask(False), bindings.glDepthFunc(bindings.GL_LESS),
            bindings.glBlendEquation(bindings.GL_FUNC_ADD))) if transparent else (
            lambda bindings: (bindings.glColorMask(False, False, False, False), bindings.glDepthMask(True),
                bindings.glDepthFunc(bindings.GL_LESS))) if phase == 'depth' else (
            lambda bindings: self._light_state(bindings, shader, camera=camera))
        batch = RenderBatch(shader, type=RenderBatch.RenderType.Transparent if transparent else RenderBatch.RenderType.Solid,
            backface_cull=phase == 'light', state_setup_callback=setup)
        batch.addItem(item['transformation'], mesh=item['mesh'], uniforms=item.get('uniforms') if phase != 'light' else None,
            normal_transformation=item.get('normal_transformation'))
        try: batch.render(camera)
        finally:
            try: shader.release()
            finally:
                if phase == 'depth': gl.glColorMask(True, True, True, True)

    def _close_draw(self):
        turn, self._turn = self._turn, None
        self._draw_key = self._draw = None
        if turn is not None: turn.close()

    def _break_draw(self):
        if self._turn is not None:
            self._close_draw()
            self._turn = ExitStack()

    @contextmanager
    def turn(self):
        """No shader, VAO or buffer binding survives a host render turn."""
        self._turn = ExitStack()
        try: yield
        finally: self._close_draw()

    def _path_draw(self, key, geometry, shader, camera, transform, start, end, gl, setup):
        if self._turn is None:
            setup()
            geometry.render(shader, camera, transform, [(start, end)], gl)
            return
        if key != self._draw_key:
            self._close_draw()
            self._turn = ExitStack()
            setup()
            self._draw = self._turn.enter_context(geometry.draw_session(shader, camera, transform, gl))
            self._draw_key = key
        self._draw([(start, end)])

    def prepare(self):
        """Reduce immutable mesh bounds once, in bounded render-thread turns."""
        entries = [(item["mesh"], item["transformation"]) for _shader, item, _settings in self.plates]
        if not self.projection:
            entries += [(geometry.mesh, transform) for geometry, transform, *_rest in self.paths]
        world = []
        cached_work = 0
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
            cached_work += 1
            if cached_work == 32:
                cached_work = 0
                yield lambda: None
        # Reduce only admitted prefix chunks. Future layers must not force all
        # six faces to draw the currently visible prefix. Cache immutable local
        # bounds; transformed tube expansion is accounted for per snapshot.
        for geometry, transform, bounds, _partial, _shadow in self.paths:
            mesh = geometry.mesh
            cached = self.owner._path_bounds.setdefault(mesh, {})
            edges = self.owner._path_edges.setdefault(mesh, {})
            admitted_edges = set()
            matrix = np.asarray(transform.getData())
            expansion = max(1., float(np.max(np.abs(matrix[:3, :3]).sum(axis=1))))
            for start in range(bounds[0] // PATH_CHUNK * PATH_CHUNK, bounds[1], PATH_CHUNK):
                first, last = max(start, bounds[0]), min(start + PATH_CHUNK, bounds[1])
                complete = first == start and last == start + PATH_CHUNK
                cache, key = (cached, start) if complete else (edges, (first, last))
                if not complete: admitted_edges.add(key)
                if key not in cache:
                    def reduce_path(first=first, last=last, cache=cache, key=key, mesh=mesh):
                        selected = np.asarray(mesh.getIndices()).reshape(-1)[first:last]
                        points = mesh.getVertices()[selected]
                        dimensions = mesh.getAttribute("line_dimensions")
                        dimensions = dimensions["value"] if dimensions is not None else None
                        if not len(points) or not np.isfinite(points).all():
                            raise RuntimeError("Invalid reflection path vertices")
                        padding = 2.
                        if dimensions is not None:
                            sizes = np.asarray(dimensions)[selected]
                            if not np.isfinite(sizes).all(): raise RuntimeError("Invalid reflection path dimensions")
                            padding = max(padding, float(np.max(np.abs(sizes))) * 2)
                        cache[key] = points.min(axis=0), points.max(axis=0), padding
                    yield reduce_path
                low, high, padding = cache[key]
                centre, extent = (low + high) / 2, (high - low) / 2
                centre = matrix[:3, :3] @ centre + matrix[:3, 3]
                extent = np.abs(matrix[:3, :3]) @ extent + padding * expansion
                key = id(geometry), start
                previous = self.path_bounds.get(key)
                low, high = centre - extent, centre + extent
                if previous is not None:
                    # One immutable LayerData may occur under multiple scene
                    # transforms. Its shared geometry must retain every copy.
                    low, high = np.minimum(low, previous[0]), np.maximum(high, previous[1])
                self.path_bounds[key] = low, high
                cached_work += 1
                if cached_work == 32:
                    cached_work = 0
                    yield lambda: None
            # Keep only the current first/last partial blocks, not every scrub
            # prefix. Unchanged captures reuse these two bounded edge entries.
            self.owner._path_edges[mesh] = {key: value for key, value in edges.items() if key in admitted_edges}
        padding = max(2., float(self.uniforms.get("u_max_thickness", 0)) * 2,
                      float(self.uniforms.get("u_max_line_width", 0)) * 2)
        low = np.min([entry[0] for entry in world], axis=0) - padding if world else np.asarray(self.origin) - 1
        high = np.max([entry[1] for entry in world], axis=0) + padding if world else np.asarray(self.origin) + 1
        if self.path_bounds:
            low = np.minimum(low, np.min([box[0] for box in self.path_bounds.values()], axis=0))
            high = np.maximum(high, np.max([box[1] for box in self.path_bounds.values()], axis=0))
        reach = float(np.linalg.norm(np.maximum(abs(low - self.origin), abs(high - self.origin)))) + 1
        self.descriptor = ProbeDescriptor(self.origin, tuple(low), tuple(high),
            far=max(2., reach, float(np.max(high-low))+.22) if self.projection else max(2.,reach),
            projection=self.projection)

    def visible_chunks(self, geometry, camera, first, last, *, lights=None):
        """Reject only whole padded chunks outside one homogeneous clip plane."""
        clip = None if self.projection else camera.getProjectionMatrix().getData() @ camera.getInverseWorldTransformation().getData()
        camera_key = None if clip is None else (clip.dtype.str, clip.tobytes())
        rejected = 0
        for start in range(first, last, PATH_CHUNK):
            end = min(start + PATH_CHUNK, last)
            # A shadow boundary can straddle two aligned cached chunks.
            visible = False
            for offset in range(start // PATH_CHUNK * PATH_CHUNK, end, PATH_CHUNK):
                box = self.path_bounds.get((id(geometry), offset))
                if box is None:
                    visible = True; break  # Unprepared synthetic snapshots fail open.
                key = camera_key, id(geometry), offset
                cached = self._visibility.get(key)
                if self.projection:
                    # Exterior directional cameras enclose the complete prepared
                    # scene bounds. Let raster clipping handle these ranges;
                    # repeating per-box frustum tests cannot remove useful work.
                    verdict = True
                elif cached is None or cached[0] is not box:
                    low, high = box
                    corners = np.array([[x, y, z, 1.] for x in (low[0], high[0])
                                        for y in (low[1], high[1]) for z in (low[2], high[2])])
                    points = corners @ clip.T
                    verdict = not any(np.all(sign * points[:, axis] > points[:, 3])
                                      for axis in range(3) for sign in (-1, 1))
                    self._visibility[key] = box, verdict
                else: verdict = cached[1]
                if verdict:
                    visible = True; break
            if visible and lights is not None:
                visible = False
                for offset in range(start // PATH_CHUNK * PATH_CHUNK, end, PATH_CHUNK):
                    box = self.path_bounds.get((id(geometry), offset))
                    if box is None:
                        visible = True; break
                    low, high = box
                    for position, reach in lights:
                        delta = np.maximum(np.maximum(low - position, position - high), 0)
                        if float(np.sum(delta * delta)) <= reach * reach:
                            visible = True; break
                    if visible: break
            if visible:
                rejected = 0
                yield start, end
            else:
                rejected += 1
                if rejected == 32:
                    # Cooperative checkpoint: next(commands) must not scan an
                    # arbitrarily large invisible print in one GUI turn.
                    rejected = 0
                    yield None, None

    def lit_chunks(self, geometry, camera, first, last):
        """Cull only additive energy; native colour and depth keep all geometry."""
        lights = []
        try:
            for index in range(int(self.lighting['u_attachedCount'])):
                position = np.asarray(self.lighting[f'u_attachedPosition[{index}]'], dtype=float)
                reach = float(self.lighting[f'u_attachedRange[{index}]'])
                colour = np.asarray(self.lighting[f'u_attachedColour[{index}]'], dtype=float)
                if position.shape != (3,) or not np.isfinite(position).all() or not math.isfinite(reach):
                    raise ValueError('Unclassified light range')
                if reach > 0 and np.any(colour != 0): lights.append((position, reach))
        except (KeyError, TypeError, ValueError):
            lights = None  # Incomplete synthetic/custom descriptors fail open.
        # A single rejection budget covers both tests. Nested generators
        # otherwise multiply 31 frustum rejects by 31 light-range rejects.
        yield from self.visible_chunks(geometry, camera, first, last, lights=lights)

    def commands(self, face):
        descriptor = self.descriptor
        camera = (directional_camera(descriptor,face,self.light) if descriptor.projection else
            probe_camera(self.origin, face, self.light, descriptor.near, descriptor.far))
        for shader, item, settings in self.plates:
            def plate(gl, shader=shader, item=item, settings=settings):
                self._break_draw()
                for name, value in settings.items(): shader.setUniformValue(name, value)
                self._render_plate(shader, item, camera, gl, 'colour')
            yield plate
        # Native transparent plate surfaces do not occlude each other while
        # their colours blend: a negative heightmap still shows beneath the
        # grid. Populate paired depth only AFTER that complete colour phase.
        for shader, item, settings in self.plates:
            def plate_depth(gl, shader=shader, item=item, settings=settings):
                self._break_draw()
                for name, value in settings.items(): shader.setUniformValue(name, value)
                self._render_plate(shader, item, camera, gl, 'depth')
            yield plate_depth
        for geometry, transform, bounds, partial, shadow in self.paths:
            # Native shadow geometry has distinct helper/start-marker rules,
            # not merely grey paint. Keep its shader and range separate.
            spans = ((bounds[0], min(shadow, bounds[1]), True),
                     (max(bounds[0], shadow), bounds[1], False))
            for first, last, is_shadow in spans:
                if last <= first: continue
                shader = self.owner.path_shader(self.compatibility, is_shadow)
                fragment = None if is_shadow else partial
                for start, end in self.visible_chunks(geometry, camera, first, last):
                    if start is None:
                        yield lambda gl: None
                        continue
                    def path(gl, geometry=geometry, transform=transform, start=start, end=end, partial=fragment, shader=shader):
                        def setup():
                            for name, value in self.uniforms.items(): shader.setUniformValue(name, value)
                            shader.setUniformValue("u_last_vertex", partial[0] if partial else [math.nan] * 3)
                            shader.setUniformValue("u_next_vertex", partial[1] if partial else [math.nan] * 3)
                            shader.setUniformValue("u_last_line_ratio", partial[2] if partial else 1.)
                        gl.glEnable(gl.GL_DEPTH_TEST)
                        gl.glDepthMask(True)
                        gl.glDepthFunc(gl.GL_LESS)
                        gl.glDisable(gl.GL_BLEND)
                        gl.glDisable(gl.GL_CULL_FACE)
                        key = id(geometry), shader, id(camera), id(transform), id(partial)
                        self._path_draw(key, geometry, shader, camera, transform, start, end, gl, setup)
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
                    self._break_draw()
                    self._light_state(gl, shader, camera=camera)
                    shader.setUniformValue('u_hasColour', 0)
                    self._render_plate(shader, dict(item, mesh=mesh), camera, gl, 'light')
                yield lit_plate
        if light_models and self.paths:
            shader = self.owner.light_path_shader(self.compatibility)
            for geometry, transform, bounds, partial, shadow in self.paths:
                for start, end in self.lit_chunks(geometry, camera, bounds[0], bounds[1]):
                    if start is None:
                        yield lambda gl: None
                        continue
                    def lit_path(gl, geometry=geometry, transform=transform, start=start, end=end, partial=partial, shadow=shadow):
                        # CubeStorage.begin restores depth writes for each
                        # command. Reapply additive state even in a bound batch.
                        self._light_state(gl, shader, uniforms=False)
                        def setup():
                            self._light_uniforms(shader,camera)
                            for name, value in self.uniforms.items(): shader.setUniformValue(name, value)
                            shader.setUniformValue('u_lightingFirstTopElement', self.path_top[id(geometry)])
                            shader.setUniformValue('u_lightingShadowElements', shadow)
                            shader.setUniformValue('u_last_vertex', partial[0] if partial else [math.nan]*3)
                            shader.setUniformValue('u_next_vertex', partial[1] if partial else [math.nan]*3)
                            shader.setUniformValue('u_last_line_ratio', partial[2] if partial else 1.)
                        key = id(geometry), shader, id(camera), id(transform), id(partial), shadow
                        self._path_draw(key, geometry, shader, camera, transform, start, end, gl, setup)
                    yield lit_path

    def _light_uniforms(self, shader, camera=None):
        for name, value in self.lighting.items(): shader.setUniformValue(name, value)
        shader.setUniformValue('u_depthOnly', 0)
        shader.setUniformValue('u_orthographic', int(bool(self.projection)))
        if self.projection and camera is not None:
            shader.setUniformValue('u_viewDirection', list(map(float,camera.getInverseWorldTransformation().getData()[2,:3])))

    def _light_state(self, gl, shader, *, uniforms=True, camera=None):
        if uniforms: self._light_uniforms(shader,camera)
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
        self._path_bounds = {}
        self._path_edges = {}
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

    @staticmethod
    def heightmap_meshes(root, children=None):
        from ..bedmesh.BedMeshSceneNode import BedMeshSceneNode
        return {child.getMeshData() for child in (root.getAllChildren() if children is None else children)
                if isinstance(child, BedMeshSceneNode) and child.isVisible() and child.getMeshData() is not None}

    def signature(self, renderer, view, root):
        if view.getCompatibilityMode():
            # Legacy top-layer mesh and flat-line modes differ from tubes.
            # Until exact scene parity is available, retain ordinary shading.
            raise RuntimeError("Reflections require the normal Preview renderer")
        live = tuple(renderer.getBatches())
        if live: self._batches = live
        uniforms = view_uniforms(view)
        children = tuple(root.getAllChildren())
        data = []
        for child in children:
            mesh = child.callDecoration("getLayerData")
            if mesh is not None and not getattr(child, "isOutsideBuildArea", lambda: False)() and not child.callDecoration("isAssignedToDisabledExtruder"):
                data.append((mesh, np.asarray(child.getWorldTransformation().getData(), dtype=np.float64).tobytes(), child.isVisible()))
        data = tuple(data)
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
        if not data and not getattr(provider, "getReflectionSceneReady", lambda: True)():
            raise EnvironmentNotReady("Native slice replacement is still processing")
        if shadow is None and any(visible for _mesh, _transform, visible in data):
            raise EnvironmentNotReady("Native Preview path shadow state unavailable")
        from .ToolheadCaptureValues import freeze_uniform
        plate_publication=tuple((id(item['mesh']),freeze_uniform(item['transformation']),
            freeze_uniform(item['normal_transformation']) if item.get('normal_transformation') is not None else None,
            tuple((name,freeze_uniform(value)) for name,value in (item.get('uniforms') or {}).items()))
            for batch in self._batches if batch.renderMode==4 for item in batch.items)
        hard = (root, view, self._scrub_epoch, bool(shadow), tuple((id(mesh), transform, visible) for mesh, transform, visible in data),
                repr(uniforms), bool(view.getCompatibilityMode()), int(view.getMinimumLayer()), colours, texture,
                tuple((id(item["mesh"]), item["transformation"].getData().tobytes())
                      for batch in self._batches for item in batch.items if batch.renderMode == 4),
                frozenset(map(id, self.heightmap_meshes(root, children))),plate_publication,
                bool(stack.getMetaDataEntry('has_textured_buildplate',False)) if stack is not None else False)
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
        heightmaps = self.heightmap_meshes(root)
        plates, heightmap_plates = [], []
        for batch in self._batches:
            if batch.renderMode != 4: continue
            for item in batch.items:
                mesh = item["mesh"]
                if mesh in heightmaps and self._plate_kinds.get(mesh) is None:
                    vertices = mesh.getVertices()
                    if vertices is not None and 3 <= len(vertices) <= MAX_BED_VERTICES:
                        self._plate_kinds[mesh] = "heightmap"
                if mesh not in self._plate_kinds:
                    kind = None
                    # Bound classification before any whole-array operation.
                    if mesh in platforms or mesh in heightmaps or mesh.hasUVCoordinates():
                        vertices = mesh.getVertices()
                        if vertices is not None and 3 <= len(vertices) <= MAX_BED_VERTICES:
                            extent = np.ptp(vertices, axis=0)
                            if mesh in heightmaps: kind = "heightmap"
                            elif mesh in platforms: kind = "platform"
                            elif mesh.hasUVCoordinates() and extent[1] < .02 and extent[0] > node.light_dimensions()[0]*.7 and extent[2] > node.light_dimensions()[1]*.7:
                                kind = "grid"
                    self._plate_kinds[mesh] = kind
                kind = self._plate_kinds[mesh]
                if kind is None: continue
                if kind == "heightmap" and mesh not in heightmaps: continue
                is_grid = kind == "grid"
                shader = self.mesh_shader("default" if kind == "heightmap" else kind)
                settings = {"u_plateColor": [value/255 for value in colours[0]],
                            "u_gridColor0": [value/255 for value in colours[1]],
                            "u_gridColor1": [value/255 for value in colours[2]]} if is_grid else {}
                if is_grid:
                    # Match native textured-bed blending over the platform.
                    settings["u_plateColor"][3] = .5 if Application.getInstance().getGlobalContainerStack().getMetaDataEntry("has_textured_buildplate", False) else 1.
                elif kind == "platform": shader.setTexture(0, self._texture)
                copied = dict(item, transformation=Matrix(item["transformation"].getData()))
                (heightmap_plates if kind == "heightmap" else plates).append((shader, copied, settings))
        # Heightmap alpha must blend over the same bed base; it is not a flat
        # attached-light receiver and retains its actual vertex colour/height.
        plates.extend(heightmap_plates)
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
        self._path_bounds = {key: value for key, value in self._path_bounds.items() if key in used}
        self._path_edges = {key: value for key, value in self._path_edges.items() if key in used}
        meshes = {item["mesh"] for batch in self._batches for item in batch.items}
        self._plate_kinds = {mesh: kind for mesh, kind in self._plate_kinds.items() if mesh in meshes}
        self._light_receivers = {mesh: receiver for mesh, receiver in self._light_receivers.items() if mesh in meshes}
        self._mesh_bounds = {mesh: bounds for mesh, bounds in self._mesh_bounds.items() if mesh in meshes or mesh in used}
        point = node.render_position()
        origin = np.array((point.x, point.y, point.z)) + np.mean(node._model_bounds, axis=0)
        snapshot = EnvironmentSnapshot(self, origin, camera.getCameraLightPosition(), plates, paths,
                                   uniforms, bool(view.getCompatibilityMode()), projection=1)
        if getattr(node, '_lighting_enabled', False) and getattr(node, '_attached_lights', ()):
            node.apply_attached_lights(SimpleNamespace(setUniformValue=lambda name, value: snapshot.lighting.__setitem__(name, deepcopy(value))))
            snapshot.light_effects = node.scene_lighting_effects()
            for geometry, _transform, bounds, _partial, _shadow in paths:
                # Native LayerData counts are NumPy integers. Qt's scalar
                # uniform overload accepts Python int only, including after
                # ShaderProgram defers this upload until the next bind.
                snapshot.path_top[id(geometry)] = int(max(bounds[0], sum(count for layer, count in geometry.mesh.getElementCounts().items() if layer < int(view.getCurrentLayer()))))
        return snapshot
