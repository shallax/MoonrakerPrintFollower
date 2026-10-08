"""Owned additive bed/path lighting; native shaders and meshes stay unchanged."""
from __future__ import annotations

import ast
import configparser
import math
from pathlib import Path

import numpy as np
from UM.Mesh.MeshData import MeshData
from UM.PluginRegistry import PluginRegistry
from UM.View.GL.OpenGLContext import OpenGLContext
from UM.View.GL.ShaderProgram import ShaderProgram
from UM.View.RenderBatch import RenderBatch

from ..resources.PluginPaths import plugin_path
from .ToolheadPathGeometry import ToolheadPathGeometry
from .ToolheadDepthCache import ToolheadDepthCache
from .ToolheadCamera import apply_camera_view


def layer_range(data, view):
    """Native LayerData element bounds, including only the selected path prefix."""
    current, minimum = int(view.getCurrentLayer()), int(view.getMinimumLayer())
    path = float(view.getCurrentPath())
    if current < 0 or not math.isfinite(path) or path < 0: return None
    counts = data.getElementCounts()
    start = sum(count for layer, count in counts.items() if layer < minimum)
    end = sum(count for layer, count in counts.items() if layer < current)
    selected_count = counts.get(current, 0)
    selected = min(selected_count, int(path) * 2)
    partial = None
    layer = data.getLayer(current)
    if layer is not None and path % 1 > .0001:
        index = int(path)
        for polygon in layer.polygons:
            if index >= len(polygon.data):
                index -= len(polygon.data)
                continue
            if index + 1 < len(polygon.data):
                partial = (list(map(float, polygon.data[index])), list(map(float, polygon.data[index + 1])), path % 1)
                selected = min(selected_count, selected + 2)
            break
    return start, end + selected, partial


def shader_sources(legacy, compatibility, surface=False):
    """Read the installed public shader resource, never patch native programs."""
    if surface and (legacy or compatibility):
        raise RuntimeError("Deferred surfaces require the native core geometry shader")
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    parser.optionxform = str
    source = Path(PluginRegistry.getInstance().getPluginPath("SimulationView"))
    parser.read(source / ("layers.shader" if compatibility else "layers3d.shader"), encoding="utf-8")
    variant = "" if legacy else "41core"
    vertex = parser["shaders"]["vertex" + variant]
    geometry = parser["shaders"].get("geometry" + variant)
    fragment = lighting_fragment(legacy)
    if surface:
        from .ToolheadSurfaceCache import surface_fragment
        fragment = surface_fragment()
    if geometry:
        # Reject distant lines BEFORE generating the native tubular geometry.
        # Depth still receives every visible line for correct occlusion.
        guard = """
        uniform int u_depthOnly;
        uniform int u_lightingFirstTopElement;
        uniform int u_drawElementStart;
        uniform int u_lightingShadowElements;
        uniform int u_attachedCount;
        uniform vec3 u_attachedPosition[8];
        uniform vec3 u_attachedColour[8];
        uniform float u_attachedRange[8];
        bool receivesLight(vec3 a, vec3 b, float padding) {
            vec3 segment = b - a;
            float lengthSquared = max(dot(segment, segment), 0.000001);
            for (int i = 0; i < 8; ++i) {
                if (i >= u_attachedCount) break;
                if (dot(u_attachedColour[i], u_attachedColour[i]) <= 0.0) continue;
                vec3 delta = u_attachedPosition[i] - a;
                vec3 closest = a + segment * clamp(dot(delta, segment) / lengthSquared, 0.0, 1.0);
                delta = u_attachedPosition[i] - closest;
                float reach = u_attachedRange[i] + padding;
                if (dot(delta, delta) <= reach * reach) return true;
            }
            return false;
        }
        """
        if surface:
            guard = "uniform int u_lightingFirstTopElement;\nuniform int u_drawElementStart;\nuniform int u_lightingShadowElements;\nuniform int u_surfaceDepthOnly;"
        # Match the material the native shadow pass actually displays. The
        # selected top layer keeps its category/material colour; older paths
        # are grey only when the owned pass explicitly reports shadow mode.
        # Declare uniforms before myEmitVertex, which uses this colour too.
        first_function = "void myEmitVertex(" if "void myEmitVertex(" in geometry else "void main()"
        geometry = geometry.replace(first_function, guard + "\n" + first_function, 1)
        geometry = geometry.replace("f_color = color;",
            "f_color = u_drawElementStart + gl_PrimitiveIDIn * 2 < u_lightingShadowElements"
            " ? vec4(0.4, 0.4, 0.4, 0.9) : color;")
        marker = "highp mat4 viewProjectionMatrix ="
        category_guard = "u_drawElementStart + gl_PrimitiveIDIn * 2 < u_lightingFirstTopElement && v_line_type[0] != 1"
        geometry = geometry.replace(marker, "// Inset0 (1) includes outside boundaries and hole walls.\n"
            + ("if (u_surfaceDepthOnly == 1 ? !(" + category_guard + ") : (" + category_guard + ")) return;\n" if surface else
                "if (u_depthOnly == 0 && " + category_guard + ") return;\n"
                "if (u_depthOnly == 0 && !receivesLight(v_vertex[0], v_vertex[1], "
                "2.0 * max(max(v_line_dim[0].x, v_line_dim[1].x), max(v_line_dim[0].y, v_line_dim[1].y)) + 0.1)) return;\n") + marker)
    if compatibility:
        varying = "varying" if legacy else "out"
        vertex = vertex.replace("void main()", varying + " vec3 f_vertex;\n" + varying + " vec3 f_normal;\nvoid main()")
        vertex = vertex.replace("v_line_type = a_line_type;", "v_line_type = a_line_type;\nf_vertex = (u_modelMatrix * a_vertex).xyz;\nf_normal = vec3(0.0, 1.0, 0.0);")
        # Preserve native line-category discard logic in the owned fragment.
        native = parser["shaders"]["fragment" + variant]
        body = native[native.index("void main()"):]
        output = "gl_FragColor" if legacy else "frag_color"
        body = body.replace(output + " = v_color;", output + " = vec4(lightSurface(f_vertex, f_normal, v_color.rgb), v_color.a);")
        declarations = native[:native.index("void main()")]
        declarations = declarations.replace("out vec4 frag_color;", "")
        fragment = fragment[:fragment.index("void main()")] + declarations.replace("#version 410", "") + body
    return parser, vertex, geometry, fragment


def lighting_fragment(legacy):
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    parser.read(plugin_path("toolhead", "scene-lighting.shader"), encoding="utf-8")
    return parser["shaders"]["fragment" + ("" if legacy else "41core")]


def create_path_shader(compatibility, surface=False):
    parser, vertex, geometry, fragment = shader_sources(OpenGLContext.isLegacyOpenGL(), compatibility, surface)
    shader = ShaderProgram()
    if not shader.setVertexShader(vertex) or not shader.setFragmentShader(fragment):
        raise RuntimeError("Scene lighting shader could not compile")
    if geometry and not shader.setGeometryShader(geometry):
        raise RuntimeError("Scene lighting geometry shader could not compile")
    shader.build()
    for name, value in parser["defaults"].items():
        value = ast.literal_eval(value)
        if name.startswith(("u_min_", "u_max_")): value = float(value)
        shader.setUniformValue(name, value)
    for name, value in parser["bindings"].items(): shader.addBinding(name, value)
    shader.addBinding("u_viewPosition", "view_position")
    return shader


class ToolheadSceneLighting:
    def __init__(self):
        self._mesh_shader = None
        self._frame_cache = None
        self._path_shaders = {}
        self._depth_cache = ToolheadDepthCache()
        self._prefix_depth_cache = ToolheadDepthCache()
        self._depth_cache_failed = False
        self._native_receivers = ()
        self._scene_identity = None
        self._path_geometry = {}
        self._plate_meshes = {}
        self._normal_matrices = {}
        self._surface_cache = self._surface_path_shader = self._surface_mesh_shader = None
        self._surface_failed = False
        self._surface_padding_key = None
        self._surface_padding = 0.0

    def _batch(self, shader, transform, mesh, bounds=None, lines=False):
        options = dict(type=RenderBatch.RenderType.Solid, backface_cull=True)
        if lines: options.update(mode=RenderBatch.RenderMode.Lines, range=bounds)
        batch = RenderBatch(shader, **options)
        from UM.Math.Matrix import Matrix
        key = transform.getData().tobytes()
        cached = self._normal_matrices.get(mesh)
        if cached is None or cached[0] != key:
            normal = Matrix(transform.getData())
            normal.setRow(3, [0, 0, 0, 1])
            normal.setColumn(3, [0, 0, 0, 1])
            normal.invert()
            normal.transpose()
            cached = self._normal_matrices[mesh] = (key, normal)
        batch.addItem(transform, mesh=mesh, normal_transformation=cached[1])
        return batch

    def draw(self, node, renderer, camera, view, root):
        if self._mesh_shader is None:
            from UM.View.GL.OpenGL import OpenGL
            self._mesh_shader = OpenGL.getInstance().createShaderProgram(plugin_path("toolhead", "scene-lighting.shader"))
        if self._mesh_shader is None: return
        node.apply_attached_lights(self._mesh_shader)
        apply_camera_view(self._mesh_shader, camera)
        light_bed, light_models = node.scene_lighting_effects()
        batches = []
        surface_batches = []
        paths = []
        native_depth = []
        scene_identity = (id(root), id(view), node.light_dimensions())
        if scene_identity != self._scene_identity:
            self._native_receivers = ()
            self._path_geometry.clear()
            self._plate_meshes.clear()
            self._normal_matrices.clear()
            self._scene_identity = scene_identity
        # QtRenderer.endRendering clears getBatches(). reRenderLast then runs
        # only composition, using the previous native textures. Keep the same
        # receiving geometry until a full frame supplies its replacement.
        native_frame = tuple(renderer.getBatches())
        if native_frame:
            self._native_receivers = native_frame
            live_meshes = {item["mesh"] for batch in native_frame for item in batch.items}
            self._plate_meshes = {mesh: plate for mesh, plate in self._plate_meshes.items() if mesh in live_meshes}
            retained = live_meshes | {plate for plate in self._plate_meshes.values() if plate is not None}
            self._normal_matrices = {mesh: value for mesh, value in self._normal_matrices.items() if mesh in retained}
        # Public renderer batches carry the actual plate shape and transforms.
        for native in self._native_receivers:
            if native.renderMode != RenderBatch.RenderMode.Triangles: continue
            for item in native.items:
                mesh = item["mesh"]
                vertices = mesh.getVertices()
                if vertices is None or len(vertices) < 3: continue
                if mesh not in self._plate_meshes:
                    width, depth, _height = node.light_dimensions()
                    extents = np.ptp(vertices, axis=0)
                    plate = extents[1] < .02 and extents[0] >= width * .7 and extents[2] >= depth * .7
                    self._plate_meshes[mesh] = MeshData(vertices=vertices, indices=mesh.getIndices(),
                        normals=np.tile([0, 1, 0], (len(vertices), 1)).astype(np.float32)) if plate else None
                plate = self._plate_meshes[mesh]
                if plate is not None:
                    receiver = self._batch(self._mesh_shader, item["transformation"], plate)
                    surface_batches.append(receiver)
                    if light_bed: batches.append(receiver)
                elif native.renderType == RenderBatch.RenderType.Solid:
                    native_depth.append(self._batch(self._mesh_shader, item["transformation"], mesh))
        self._mesh_shader.setUniformValue("u_hasColour", 0)
        used_data = set()
        simulation = getattr(renderer, "getRenderPass", lambda _name: None)("simulationview")
        shadow_getter = getattr(simulation, "getCompletedLayerShadowMode", None)
        # Unknown/native-only passes keep their existing receiving colours.
        # No private native shader or transition state is inspected here.
        shadow = shadow_getter() if callable(shadow_getter) else False
        path_settings = [("lightingShadow", shadow)]
        if view is not None:
            compatibility = bool(view.getCompatibilityMode())
            shader = self._path_shaders.get(compatibility)
            if shader is None:
                shader = self._path_shaders[compatibility] = create_path_shader(compatibility)
            node.apply_attached_lights(shader)
            apply_camera_view(shader, camera)
            for uniform, getter in (("u_show_travel_moves", "getShowTravelMoves"), ("u_show_helpers", "getShowHelpers"),
                    ("u_show_skin", "getShowSkin"), ("u_show_infill", "getShowInfill"), ("u_show_starts", "getShowStarts"),
                    ("u_layer_view_type", "getSimulationViewType"), ("u_extruder_opacity", "getExtruderOpacities")):
                value = getattr(view, getter)()
                shader.setUniformValue(uniform, value)
                path_settings.append((uniform, repr(value)))
            for metric, getter in (("feedrate", "Feedrate"), ("thickness", "Thickness"), ("line_width", "LineWidth"), ("flow_rate", "FlowRate")):
                for prefix in ("min", "max"):
                    value = getattr(view, "get" + prefix.title() + getter)()
                    shader.setUniformValue("u_" + prefix + "_" + metric, value)
                    path_settings.append((prefix + metric, repr(value)))
            for child in root.getAllChildren():
                if not child.isVisible(): continue
                data = child.callDecoration("getLayerData")
                if data is None: continue
                bounds = layer_range(data, view)
                if bounds is None or bounds[1] <= bounds[0]: continue
                used_data.add(data)
                partial = bounds[2]
                uniforms = {"u_last_vertex": partial[0] if partial else [math.nan] * 3,
                    "u_next_vertex": partial[1] if partial else [math.nan] * 3,
                    "u_last_line_ratio": partial[2] if partial else 1.0}
                geometry = self._path_geometry.get(data)
                if geometry is None:
                    geometry = self._path_geometry[data] = ToolheadPathGeometry(data)
                transform = child.getWorldTransformation()
                current_start = sum(count for layer, count in data.getElementCounts().items() if layer < int(view.getCurrentLayer()))
                current_start = max(bounds[0], current_start)
                # The geometry shader keeps older outer boundaries (including
                # hole walls) and all selected top-layer paths. Interior paths
                # still occlude through cached depth, without lit extrusion.
                uniforms["u_lightingFirstTopElement"] = int(current_start)
                uniforms["u_lightingShadowElements"] = int(current_start) if shadow else 0
                # Forward spatial culling is unnecessary for retained deferred
                # surfaces. Resolve it only if an invalidated image must use
                # the forward fallback, never for cached composition frames.
                paths.append((geometry, transform, bounds[:2], [], uniforms, current_start))
                # Compatibility mode's prepared top surfaces are triangle meshes.
                if compatibility and light_models:
                    for mesh in (view.getCurrentLayerMesh(), view.getCurrentLayerJumps()):
                        if mesh is not None:
                            batches.append(self._batch(self._mesh_shader, child.getWorldTransformation(), mesh))
        self._path_geometry = {data: geometry for data, geometry in self._path_geometry.items() if data in used_data}
        from UM.View.GL.OpenGL import OpenGL
        gl = OpenGL.getInstance().getBindingsObject()
        if not batches and not (light_models and paths): return
        from UM.Math.Matrix import Matrix
        from .ToolheadFrameCache import ToolheadFrameCache
        if self._frame_cache is None: self._frame_cache = ToolheadFrameCache(depth_only=True)
        copy_completed_depth = getattr(simulation, "try_copy_completed_depth", None)
        state = (scene_identity, tuple(path_settings), node.scene_lighting_signature(),
            node.render_transformation().getData().tobytes(),
            tuple((id(item["mesh"]), item["transformation"].getData().tobytes())
                for batch in native_depth + batches for item in batch.items),
            tuple((id(geometry), transform.getData().tobytes(), bounds, repr(uniforms), current_start)
                for geometry, transform, bounds, _lit, uniforms, current_start in paths))
        # Keep a fixed viewport for illumination: moving crop projections would
        # invalidate the static depth every time the head moved. Colour is
        # already additive/premultiplied, so its retained image adds once.
        self._frame_cache.draw(gl, camera, None, Matrix(), state,
            lambda cached_camera: self._draw_with_surfaces(gl, cached_camera, node, view, native_depth,
                batches, surface_batches, paths, shader if paths else None, scene_identity, path_settings,
                copy_completed_depth), additive=True)

    def _draw_with_surfaces(self, gl, camera, node, view, native_depth, batches, surface_batches,
            paths, shader, scene_identity, path_settings, copy_completed_depth=None):
        if not self._surface_failed and not OpenGLContext.isLegacyOpenGL() and view is not None and not view.getCompatibilityMode():
            try:
                self._draw_deferred(gl, camera, node, view, native_depth, surface_batches, paths,
                    shader, scene_identity, path_settings, copy_completed_depth)
                return
            except Exception as error:
                import logging
                logging.getLogger(__name__).warning("Deferred lighting unavailable; using forward lighting: %s", error)
                self._surface_failed = True
                self._surface_cache = None
                # This callback renders into our FrameCache, never the host's
                # composition target. A late failure must not double-add an
                # already shaded subset before the forward fallback redraws.
                gl.glDisable(0x0C11)
                gl.glColorMask(True, True, True, True)
                gl.glDepthMask(True)
                gl.glClearColor(0, 0, 0, 0)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        paths = self._forward_ranges(paths, node)
        if batches or any(path[3] for path in paths):
            self._draw_lighting(gl, camera, native_depth, batches, paths, shader, scene_identity, path_settings)

    @staticmethod
    def _forward_ranges(paths, node):
        """Resolve conservative receiver ranges only for an actual forward draw."""
        lights = node.scene_light_bounds() if paths and node.scene_lighting_effects()[1] else []
        selected_paths = []
        for geometry, transform, bounds, _lit, uniforms, current_start in paths:
            lit_ranges = []
            if lights:
                exterior = geometry.exterior_geometry()
                candidates = [(geometry, bounds)] if exterior is None else [
                    (exterior, geometry.exterior_range(bounds[0], current_start)),
                    (geometry, (current_start, bounds[1]))]
                for receiver, visible in candidates:
                    selected = receiver.ranges(transform, visible, lights)
                    if selected: lit_ranges.append((receiver, selected))
            selected_paths.append((geometry, transform, bounds, lit_ranges, uniforms, current_start))
        return selected_paths

    def _draw_deferred(self, gl, camera, node, view, native_depth, surface_batches, paths,
            forward_shader, scene_identity, path_settings, copy_completed_depth=None):
        from .ToolheadSurfaceCache import ToolheadSurfaceCache, surface_fragment
        if self._surface_cache is None: self._surface_cache = ToolheadSurfaceCache(lighting_fragment(False), depth_only=True)
        if self._surface_path_shader is None: self._surface_path_shader = create_path_shader(False, surface=True)
        if self._surface_mesh_shader is None:
            parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
            parser.optionxform = str
            parser.read(plugin_path("toolhead", "scene-lighting.shader"), encoding="utf-8")
            mesh_shader = ShaderProgram()
            if not mesh_shader.setVertexShader(parser["shaders"]["vertex41core"]) or not mesh_shader.setFragmentShader(surface_fragment()):
                raise RuntimeError("Deferred mesh surface shader could not compile")
            mesh_shader.build()
            for name, value in parser["bindings"].items(): mesh_shader.addBinding(name, value)
            mesh_shader.setUniformValue("u_hasColour", 0)
            self._surface_mesh_shader = mesh_shader
        surface_shader = self._surface_path_shader
        for uniform, getter in (("u_show_travel_moves", "getShowTravelMoves"), ("u_show_helpers", "getShowHelpers"),
                ("u_show_skin", "getShowSkin"), ("u_show_infill", "getShowInfill"), ("u_show_starts", "getShowStarts"),
                ("u_layer_view_type", "getSimulationViewType"), ("u_extruder_opacity", "getExtruderOpacities")):
            surface_shader.setUniformValue(uniform, getattr(view, getter)())
        for metric, getter in (("feedrate", "Feedrate"), ("thickness", "Thickness"), ("line_width", "LineWidth"), ("flow_rate", "FlowRate")):
            for prefix in ("min", "max"):
                surface_shader.setUniformValue("u_" + prefix + "_" + metric, getattr(view, "get" + prefix.title() + getter)())
        surface_shader.setUniformValue("u_surfaceKind", 2)
        surface_shader.setUniformValue("u_last_vertex", [math.nan] * 3)
        surface_shader.setUniformValue("u_next_vertex", [math.nan] * 3)
        surface_shader.setUniformValue("u_last_line_ratio", 1.0)
        completed = tuple(int(bounds[1]) - (2 if math.isfinite(uniforms["u_last_vertex"][0]) else 0)
            for _geometry, _transform, bounds, _lit, uniforms, _start in paths)
        key = (scene_identity, tuple(path_settings), camera.getInverseWorldTransformation().getData().tobytes(),
            camera.getProjectionMatrix().getData().tobytes(),
            tuple((id(item["mesh"]), item["transformation"].getData().tobytes())
                for batch in native_depth + surface_batches for item in batch.items),
            tuple((id(geometry.mesh), transform.getData().tobytes(), bounds[0], current_start)
                for geometry, transform, bounds, _lit, _uniforms, current_start in paths))

        def mesh_pass(source, kind, depth):
            self._surface_mesh_shader.setUniformValue("u_surfaceKind", kind)
            for batch in source:
                owned = RenderBatch(self._surface_mesh_shader, type=RenderBatch.RenderType.Solid,
                    mode=batch.renderMode, range=batch.renderRange, backface_cull=True,
                    state_setup_callback=lambda bindings: (bindings.glColorMask(not depth, not depth, not depth, not depth),
                        bindings.glDepthMask(depth), bindings.glDepthFunc(bindings.GL_LESS if depth else bindings.GL_LEQUAL)),
                    state_teardown_callback=lambda bindings: bindings.glColorMask(True, True, True, True))
                for item in batch.items:
                    owned.addItem(item["transformation"], item["mesh"], normal_transformation=item["normal_transformation"])
                owned.render(camera)

        def path_pass(ranges, depth):
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glEnable(gl.GL_CULL_FACE)
            gl.glDisable(gl.GL_BLEND)
            # Receivers establish their own nearest depth while retaining the
            # original LEQUAL ordering for coplanar receiver/interior ties.
            gl.glDepthMask(True)
            gl.glDepthFunc(gl.GL_LESS if depth else gl.GL_LEQUAL)
            gl.glColorMask(not depth, not depth, not depth, not depth)
            surface_shader.setUniformValue("u_surfaceDepthOnly", int(depth))
            for (geometry, transform, _bounds, _lit, uniforms, start), selected in zip(paths, ranges, strict=True):
                # Nonreceivers occlude first. Older outer/hole walls and every
                # current-layer category are then expanded just once, writing
                # both their nearest depth and receiving attributes.
                surface_shader.setUniformValue("u_lightingFirstTopElement", int(start))
                surface_shader.setUniformValue("u_lightingShadowElements", uniforms["u_lightingShadowElements"])
                if depth:
                    # Current paths are all receivers: skip their vertex work
                    # as well as their geometry expansion in this first pass.
                    receivers = [(geometry, (selected[0], min(selected[1], start)))]
                else:
                    exterior = geometry.exterior_geometry()
                    receivers = [(geometry, selected)] if exterior is None else [
                        (exterior, geometry.exterior_range(selected[0], min(selected[1], start))),
                        (geometry, (max(start, selected[0]), selected[1]))]
                for receiver, visible in receivers:
                    if visible[1] > visible[0]: receiver.render(surface_shader, camera, transform, [visible], gl)
            gl.glColorMask(True, True, True, True)

        def rebuild():
            # Occlusion always includes interior paths and native solid meshes.
            seeded = self._surface_cache.try_seed_depth(
                None if copy_completed_depth is None else
                lambda target: copy_completed_depth(gl, target, camera, paths, view))
            gl.glColorMask(False, False, False, False)
            mesh_pass(native_depth + surface_batches, 1, True)
            ranges = [(path[2][0], end) for path, end in zip(paths, completed, strict=True)]
            if not seeded: path_pass(ranges, True)
            mesh_pass(surface_batches, 1, False)
            path_pass(ranges, False)

        def append(old, new):
            ranges = list(zip(old, new, strict=True))
            # Within a stable layer every appended path is a receiver.
            path_pass(ranges, False)

        self._surface_cache.prepare(gl, key, completed, rebuild, append)
        gl.glDepthMask(True)
        gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        try:
            self._surface_cache.copy_depth(gl)
            if paths:
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glDisable(gl.GL_BLEND)
                gl.glDepthMask(True)
                gl.glDepthFunc(gl.GL_LESS)
                gl.glColorMask(False, False, False, False)
                forward_shader.setUniformValue("u_depthOnly", 1)
                for (geometry, transform, bounds, _lit, uniforms, _start), end in zip(paths, completed, strict=True):
                    for name, value in uniforms.items(): forward_shader.setUniformValue(name, value)
                    if bounds[1] > end: geometry.render(forward_shader, camera, transform, [(end, bounds[1])], gl)
                gl.glColorMask(True, True, True, True)
            padding_key = tuple((id(geometry), transform.getData().tobytes()) for geometry, transform, *_ in paths)
            if padding_key != self._surface_padding_key:
                self._surface_padding = 0.0
                for geometry, transform, *_ in paths:
                    dimensions = geometry.mesh.getAttribute("line_dimensions")
                    width = float(np.max(dimensions["value"])) if dimensions is not None else 1.0
                    self._surface_padding = max(self._surface_padding,
                        2.0 * width * float(np.linalg.norm(transform.getData()[:3, :3])) + .1)
                self._surface_padding_key = padding_key
            self._surface_cache.shade(gl, camera, node, padding=self._surface_padding)
            if paths and node.scene_lighting_effects()[1]:
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glDepthMask(False)
                gl.glDepthFunc(gl.GL_LEQUAL)
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
                forward_shader.setUniformValue("u_depthOnly", 0)
                for (geometry, transform, bounds, _lit, uniforms, _start), end in zip(paths, completed, strict=True):
                    for name, value in uniforms.items(): forward_shader.setUniformValue(name, value)
                    if bounds[1] > end: geometry.render(forward_shader, camera, transform, [(end, bounds[1])], gl)
        finally:
            gl.glColorMask(True, True, True, True)
            gl.glDepthFunc(gl.GL_LESS)
            gl.glDepthMask(True)
            gl.glDisable(gl.GL_BLEND)
            gl.glClear(gl.GL_DEPTH_BUFFER_BIT)

    def _draw_lighting(self, gl, camera, native_depth, batches, paths, shader, scene_identity, path_settings):
        # Own depth, with native composition left intact underneath.
        gl.glDepthMask(True)
        gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        try:
            def draw_static_depth():
                self._mesh_shader.setUniformValue("u_depthOnly", 1)
                for batch in native_depth + batches:
                    depth = RenderBatch(batch.shader, type=RenderBatch.RenderType.Solid, mode=batch.renderMode,
                        range=batch.renderRange, backface_cull=True,
                        state_setup_callback=lambda bindings: bindings.glColorMask(False, False, False, False),
                        state_teardown_callback=lambda bindings: bindings.glColorMask(True, True, True, True))
                    for item in batch.items:
                        depth.addItem(item["transformation"], item["mesh"], normal_transformation=item["normal_transformation"])
                    depth.render(camera)
                if paths:
                    gl.glEnable(gl.GL_DEPTH_TEST)
                    gl.glEnable(gl.GL_CULL_FACE)
                    gl.glDisable(gl.GL_BLEND)
                    gl.glColorMask(False, False, False, False)
                    shader.setUniformValue("u_depthOnly", 1)
                    for geometry, transform, bounds, _lit_ranges, _uniforms, current_start in paths:
                        shader.setUniformValue("u_last_vertex", [math.nan] * 3)
                        shader.setUniformValue("u_next_vertex", [math.nan] * 3)
                        if current_start > bounds[0]: geometry.render(shader, camera, transform, [(bounds[0], current_start)], gl)
                    gl.glColorMask(True, True, True, True)

            def draw_prefix(starts, ends):
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glDisable(gl.GL_BLEND)
                gl.glDepthMask(True)
                gl.glDepthFunc(gl.GL_LESS)
                gl.glColorMask(False, False, False, False)
                shader.setUniformValue("u_depthOnly", 1)
                shader.setUniformValue("u_last_line_ratio", 1.0)
                shader.setUniformValue("u_last_vertex", [math.nan] * 3)
                shader.setUniformValue("u_next_vertex", [math.nan] * 3)
                for (geometry, transform, _bounds, _lit, _uniforms, _start), start, end in zip(paths, starts, ends, strict=True):
                    if end > start: geometry.render(shader, camera, transform, [(start, end)], gl)
                gl.glColorMask(True, True, True, True)

            completed = tuple(int(bounds[1]) - (2 if math.isfinite(uniforms["u_last_vertex"][0]) else 0)
                for _geometry, _transform, bounds, _lit, uniforms, _start in paths)
            starts = tuple(int(path[5]) for path in paths)
            if paths and not self._depth_cache_failed:
                static_key = (scene_identity, tuple(path_settings),
                    camera.getInverseWorldTransformation().getData().tobytes(), camera.getProjectionMatrix().getData().tobytes(),
                    tuple((id(item["mesh"]), item["transformation"].getData().tobytes())
                        for batch in native_depth + batches for item in batch.items),
                    tuple((id(geometry.mesh), transform.getData().tobytes(), bounds[0], current_start)
                        for geometry, transform, bounds, _lit, _uniforms, current_start in paths))
                try:
                    def draw_base():
                        self._depth_cache.restore(gl, static_key, draw_static_depth)
                        draw_prefix(starts, completed)

                    def append_prefix(previous, current):
                        if previous[0] != current[0] or any(end < old for old, end in zip(previous[1], current[1], strict=True)):
                            return False
                        draw_prefix(previous[1], current[1])
                        return True

                    self._prefix_depth_cache.restore(gl, (static_key, completed), draw_base, append=append_prefix)
                except RuntimeError:
                    # Older GL hosts retain the correct uncached depth path.
                    self._depth_cache_failed = True
                    draw_static_depth()
                    draw_prefix(starts, completed)
            else:
                draw_static_depth()
                if paths: draw_prefix(starts, completed)
            if paths:
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glDisable(gl.GL_BLEND)
                gl.glDepthMask(True)
                gl.glColorMask(False, False, False, False)
                shader.setUniformValue("u_depthOnly", 1)
                for (geometry, transform, bounds, _lit_ranges, uniforms, _start), end in zip(paths, completed, strict=True):
                    for name, value in uniforms.items(): shader.setUniformValue(name, value)
                    if bounds[1] > end: geometry.render(shader, camera, transform, [(end, bounds[1])], gl)
                gl.glColorMask(True, True, True, True)
                gl.glDepthMask(False)
                gl.glDepthFunc(gl.GL_LEQUAL)
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
                shader.setUniformValue("u_depthOnly", 0)
                for _geometry, transform, _bounds, lit_ranges, uniforms, _current_start in paths:
                    for name, value in uniforms.items(): shader.setUniformValue(name, value)
                    for receiver, selected in lit_ranges:
                        receiver.render(shader, camera, transform, selected, gl)
            self._mesh_shader.setUniformValue("u_depthOnly", 0)
            for batch in batches:
                lit = RenderBatch(batch.shader, type=RenderBatch.RenderType.Transparent, mode=batch.renderMode,
                    range=batch.renderRange, backface_cull=True, blend_mode=RenderBatch.BlendMode.Additive,
                    state_setup_callback=lambda bindings: bindings.glDepthFunc(bindings.GL_LEQUAL),
                    state_teardown_callback=lambda bindings: bindings.glDepthFunc(bindings.GL_LESS))
                for item in batch.items:
                    lit.addItem(item["transformation"], item["mesh"], normal_transformation=item["normal_transformation"])
                lit.render(camera)
        finally:
            gl.glColorMask(True, True, True, True)
            gl.glDepthFunc(gl.GL_LESS)
            gl.glDepthMask(True)
            gl.glDisable(gl.GL_BLEND)
            gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
