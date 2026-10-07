"""Ordinary plugin scene node, independent of Cura's estimated NozzleNode."""
import numpy as np
from UM.Mesh.MeshData import MeshData
from UM.Scene.SceneNode import SceneNode
from UM.View.GL.OpenGL import OpenGL
from UM.View.RenderBatch import RenderBatch
from ..resources.PluginPaths import plugin_path
from ..geometry.ToolheadLighting import light_values, validated_lights


class ToolheadCompositePass:
    """Delegate Cura's final composition, then draw the toolhead on top.

    Keeping this as the final composite pass also covers QtRenderer's cached
    reRenderLast path. All view-owned shader/binding APIs remain on the original
    pass; no host shader, view or renderer class is patched.
    """
    def __init__(self, composite, renderer, node):
        self.composite, self.renderer, self.node = composite, renderer, node

    def getName(self): return self.composite.getName()
    def getSize(self): return self.composite.getSize()
    def setSize(self, width, height): self.composite.setSize(width, height)
    def getPriority(self): return self.composite.getPriority()
    def isEnabled(self): return self.composite.isEnabled()
    def setEnabled(self, value): self.composite.setEnabled(value)
    def getTextureId(self): return self.composite.getTextureId()
    def getOutput(self): return self.composite.getOutput()
    def bind(self): self.composite.bind()
    def release(self): self.composite.release()
    def getCompositeShader(self): return self.composite.getCompositeShader()
    def setCompositeShader(self, shader): self.composite.setCompositeShader(shader)
    def getLayerBindings(self): return self.composite.getLayerBindings()
    def setLayerBindings(self, bindings): self.composite.setLayerBindings(bindings)

    def render(self):
        self.composite.render()
        if not self.node.visible_for_render(): return
        gl = None
        try:
            camera = self.node.getSceneCamera()
            if camera is None: return
            self.node.prepare_render_context()
            if self.node.render_failure(): return
            gl = OpenGL.getInstance().getBindingsObject()
            self.node.prepare_occlusion(self.renderer, camera, gl)
            self.node._render_timing.measure("scene lighting", lambda: self.node.illuminate_scene(self.renderer, camera))
            # Scene depth belongs to other FBOs. Preserve composed pixels.
            gl.glDepthMask(gl.GL_TRUE)
            gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
            self.node._render_timing.measure("head", lambda: self.node.draw(camera))
        except Exception as error:
            # A plugin graphics failure must never abort Qt's render callback.
            self.node.fail_rendering(error)
        finally:
            if gl is not None:
                try:
                    gl.glDisable(gl.GL_DEPTH_TEST)
                    gl.glDisable(gl.GL_BLEND)
                    gl.glDisable(gl.GL_CULL_FACE)
                    gl.glColorMask(True, True, True, True)
                    gl.glDepthFunc(gl.GL_LESS)
                    gl.glDepthMask(gl.GL_TRUE)
                    # Qt controls start with clean depth after our image quads.
                    gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                except Exception as error:
                    if not self.node.render_failure(): self.node.fail_rendering(error)



class ToolheadSceneNode(SceneNode):
    def __init__(self):
        super().__init__(name="Moonraker toolhead", node_id="MoonrakerToolhead")
        self.setSelectable(False)
        self.setCalculateBoundingBox(False)
        self.setVisible(False)
        self._shader = None
        self._native_model = False
        self._translucent_mesh = None
        self._composite_pass = None
        self._simulation_pass = None
        self._simulation_active = False
        self._opacity = 1.0
        self._lights = (250.0, 250.0, 250.0)
        self._attached_lights = []
        self._scene_lighting = None
        self._light_bed = self._light_models = True
        self._lighting_enabled = True
        self._view = self._root = None
        self._render_position = self._render_transform = self._render_normal = None
        self._render_failure = ""
        self._lighting_failure = ""
        self._frame_cache = None
        self._render_context = None
        self._occlusion = None
        self._depth_seed = self._depth_revision = None
        from ..diagnostics.RenderTiming import RenderTiming
        self._render_timing = RenderTiming()

    def visible_for_render(self): return self.isVisible() and self._opacity > 0

    def render_failure(self): return self._render_failure

    def prepare_render_context(self):
        from PyQt6.QtGui import QOpenGLContext
        context = QOpenGLContext.currentContext()
        if context is None: raise RuntimeError("Toolhead graphics context unavailable")
        if self._render_context is not context:
            self._shader = self._scene_lighting = self._frame_cache = None
            self._occlusion = None
            self._depth_seed = self._depth_revision = None
            self._render_failure = ""
            self._lighting_failure = ""
            self._render_context = context

    def fail_rendering(self, error):
        from UM.Logger import Logger
        self._render_failure = "Toolhead rendering unavailable: " + str(error)[:200]
        Logger.logException("e", self._render_failure)

    def prepare_occlusion(self, renderer, camera, gl):
        self._depth_seed = self._depth_revision = None
        if self._native_model: return
        if self._occlusion is None:
            from .ToolheadOcclusion import ToolheadOcclusion
            self._occlusion = ToolheadOcclusion()
        try:
            self._depth_revision, self._depth_seed = self._occlusion.prepare(renderer, camera, gl)
        except Exception:
            # Occlusion is opportunistic on host fallback/warm frames. Never
            # hide a valid head because optional depth acquisition failed.
            self._depth_seed = self._depth_revision = None

    def set_render_position(self, position):
        # This decoration is composed after native rendering. Its movement
        # must not bubble transformationChanged up the scene and force Cura
        # to rebuild every G-code layer. Request cached composition instead.
        from UM.Math.Matrix import Matrix
        self._render_position = position
        self._render_transform = Matrix()
        self._render_transform.setByTranslation(position)
        if self._render_normal is None: self._render_normal = Matrix()

    def render_position(self):
        return self._render_position if self._render_position is not None else self.getWorldPosition()

    def render_transformation(self):
        return self._render_transform if self._render_transform is not None else self.getWorldTransformation()

    def requires_full_render(self): return self._composite_pass is None

    def set_opacity(self, value):
        self._opacity = max(0.0, min(1.0, float(value)))

    def set_lights(self, width, depth, height):
        self._lights = (width, depth, height)

    def set_attached_lights(self, lights):
        self._attached_lights = validated_lights(lights)

    def set_scene(self, view, root):
        if view is not self._view or root is not self._root:
            self._restore_simulation()
            self._scene_lighting = None
            self._occlusion = None
            self._depth_seed = self._depth_revision = None
            self._lighting_failure = ""
        self._view, self._root = view, root

    def set_simulation_active(self, active):
        changed = self._simulation_active != bool(active)
        self._simulation_active = bool(active)
        if not active: self._restore_simulation()
        return changed

    def _restore_simulation(self):
        owned = self._simulation_pass
        if owned is None: return
        owned.close()
        self._simulation_pass = None

    def set_scene_lighting(self, bed, models):
        if (bool(bed), bool(models)) != (self._light_bed, self._light_models):
            self._lighting_failure = ""
        self._light_bed, self._light_models = bool(bed), bool(models)

    def scene_lighting_effects(self): return self._light_bed, self._light_models

    def set_lighting_enabled(self, enabled):
        self._lighting_enabled = bool(enabled)

    def scene_lighting_signature(self):
        return self._opacity, self._lighting_enabled, self._light_bed, self._light_models, repr(self._attached_lights)

    def scene_light_bounds(self):
        world = self.render_position()
        origin = np.array([world.x, world.y, world.z])
        result = []
        for light in self._attached_lights:
            position, _direction, colour, reach = light_values(light)
            if max(colour) <= 0: continue
            local = np.asarray(position) - self._tip
            result.append((origin + [local[0], local[2], -local[1]], reach))
        return result

    def light_dimensions(self): return self._lights

    def apply_attached_lights(self, shader):
        shader.setUniformValue("u_lightOpacity", self._opacity)
        shader.setUniformValue("u_attachedCount", len(self._attached_lights))
        if not self._attached_lights: return
        world = self.render_position()
        for index, light in enumerate(self._attached_lights):
            position, direction, colour, reach = light_values(light)
            position = np.asarray(position) - self._tip
            position = [float(position[0]+world.x), float(position[2]+world.y), float(-position[1]+world.z)]
            direction = [direction[0], direction[2], -direction[1]]
            for name, vector in (("Position", position), ("Direction", direction), ("Colour", list(colour))):
                shader.setUniformValue("u_attached"+name+"["+str(index)+"]", vector)
            shader.setUniformValue("u_attachedRange["+str(index)+"]", float(reach))
            shader.setUniformValue("u_attachedSurface["+str(index)+"]", float(light['surface']))
            rgb = [int(light['colour'][j:j+2], 16)/255 for j in (1, 3, 5)]
            shader.setUniformValue("u_attachedPaint["+str(index)+"]", rgb + [float(light['paint'])])

    def illuminate_scene(self, renderer, camera):
        if self._native_model: return
        if not self._lighting_enabled: return
        if not self._attached_lights or self._root is None or self._opacity <= 0 or not (self._light_bed or self._light_models): return
        if not self.scene_light_bounds(): return
        if self._lighting_failure: return
        try:
            if self._scene_lighting is None:
                from .ToolheadSceneLighting import ToolheadSceneLighting
                self._scene_lighting = ToolheadSceneLighting()
            self._scene_lighting.draw(self, renderer, camera, self._view, self._root)
        except Exception as error:
            # Scene effects are optional. Their failure must never hide the head.
            from UM.Logger import Logger
            self._lighting_failure = str(error)[:200]
            Logger.logException("e", "Toolhead scene lighting unavailable: " + self._lighting_failure)

    def getSceneCamera(self):
        from UM.Application import Application
        return Application.getInstance().getController().getScene().getActiveCamera()

    def close(self):
        self._restore_simulation()
        owned = self._composite_pass
        if owned is not None:
            if owned.renderer.getRenderPass("composite") is owned:
                owned.renderer.removeRenderPass(owned)
                owned.renderer.addRenderPass(owned.composite)
            self._composite_pass = None
        self._scene_lighting = None
        self._frame_cache = None
        self._occlusion = None

    def set_model(self, model, tip):
        self._native_model = False
        self._shader = None
        self._render_failure = ""
        self._lighting_failure = ""
        self._scene_lighting = None
        self._tip = np.asarray(tip)
        points = model.triangles.reshape(-1, 3)
        # Source Z-up mm to Cura Y-up; winding is preserved by this rotation.
        anchor = np.asarray(tip, dtype=np.float32)
        vertices = np.empty_like(points)
        vertices[:, 0] = points[:, 0] - anchor[0]
        vertices[:, 1] = points[:, 2] - anchor[2]
        vertices[:, 2] = anchor[1] - points[:, 1]
        triangles = vertices.reshape(-1, 3, 3)
        self._model_bounds = vertices.min(axis=0), vertices.max(axis=0)
        normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        normals /= np.maximum(lengths, 1e-9)[:, None]
        opaque = model.colours[:, 3] >= .999
        def batch(mask):
            if not np.any(mask): return None
            return MeshData(vertices=triangles[mask].reshape(-1, 3), normals=np.repeat(normals[mask], 3, axis=0),
                colors=np.repeat(model.colours[mask], 3, axis=0),
                            attributes={"toolhead_surface": dict(value=np.repeat(model.surfaces[mask], 3).astype(np.float32),
                                opengl_type="float", opengl_name="a_surface")})
        self.setMeshData(batch(opaque))
        self._translucent_mesh = batch(~opaque)

    def set_native_model(self, mesh):
        """Use Cura's already Y-up nozzle mesh without CAD transforms/effects."""
        self.set_simulation_active(False)
        self._native_model = True
        self._shader = self._scene_lighting = self._frame_cache = None
        self._render_failure = self._lighting_failure = ""
        self._translucent_mesh = None
        self._opacity = 1.0
        self._attached_lights = []
        self.setMeshData(mesh)

    def render(self, renderer):
        if self.isVisible() and (self.getMeshData() is not None or self._translucent_mesh is not None):
            composite = renderer.getRenderPass("composite")
            if composite is not self._composite_pass and composite is not None:
                self.close()
                self._composite_pass = ToolheadCompositePass(composite, renderer, self)
                renderer.removeRenderPass(composite)
                renderer.addRenderPass(self._composite_pass)
            if not self._native_model and self._simulation_active and self._view is not None and self._root is not None:
                original = renderer.getRenderPass("simulationview")
                if original is not None and original is not self._simulation_pass:
                    self._restore_simulation()
                    from ..cura.ToolheadSimulationPass import ToolheadSimulationPass
                    self._simulation_pass = ToolheadSimulationPass(original, renderer, self._view, self._root,
                        lambda: self._simulation_active and self.isVisible())
                    renderer.removeRenderPass(original)
                    renderer.addRenderPass(self._simulation_pass)
        return True

    def draw(self, camera):
        if self._native_model:
            self._draw_native(camera)
            return
        if self._opacity <= 0: return
        if self._frame_cache is None:
            from .ToolheadFrameCache import ToolheadFrameCache
            self._frame_cache = ToolheadFrameCache(depth_only=True)
        state = (id(self.getMeshData()), id(self._translucent_mesh), self._lighting_enabled,
            self._lights, repr(self._attached_lights))
        self._frame_cache.draw(OpenGL.getInstance().getBindingsObject(), camera, self._model_bounds,
            self.render_transformation(), state, self._draw, opacity=self._opacity,
            depth_seed=self._depth_seed, depth_revision=self._depth_revision,
            post_render=self._occlusion.overlay if self._depth_seed is not None else None)

    def _draw_native(self, camera):
        mesh = self.getMeshData()
        if mesh is None: return
        if self._shader is None:
            from UM.Application import Application
            from UM.Math.Color import Color
            from UM.Resources import Resources
            self._shader = OpenGL.getInstance().createShaderProgram(Resources.getPath(Resources.Shaders, "color.shader"))
            self._shader.setUniformValue("u_color", Color(*Application.getInstance().getTheme().getColor("layerview_nozzle").getRgb()))
        batch = RenderBatch(self._shader, type=RenderBatch.RenderType.Transparent)
        batch.addItem(self.render_transformation(), mesh=mesh)
        batch.render(camera)

    def _draw(self, camera):
        normal = {"normal_transformation": self._render_normal} if self._render_normal is not None else {}
        if self.getMeshData() is not None or self._translucent_mesh is not None:
            single_pass = self._translucent_mesh is None
            if single_pass:
                cached = getattr(self, "_opaque_shader_cache", None)
                if cached is None or cached[0] is not self._render_context:
                    from .ToolheadOpaqueShader import create_opaque_shader
                    cached = self._render_context, create_opaque_shader()
                    self._opaque_shader_cache = cached
                shader = cached[1]
            else:
                if self._shader is None:
                    self._shader = OpenGL.getInstance().createShaderProgram(plugin_path("toolhead", "toolhead.shader"))
                shader = self._shader
            shader.setUniformValue("u_opacity", 1.0)
            shader.setUniformValue("u_lightingEnabled", int(self._lighting_enabled))
            self.apply_attached_lights(shader)
            width, depth, height = self._lights
            for index, (position, direction) in enumerate((
                    ([-width/2, height, 0], [1, -1, 0]),
                    ([width/2, height, 0], [-1, -1, 0]),
                    ([0, height, -depth/2], [0, -1, 1]),
                    ([0, height, depth/2], [0, -1, -1]))):
                shader.setUniformValue("u_light" + str(index), position)
                shader.setUniformValue("u_direction" + str(index), direction)
            if self.getMeshData() is not None:
                # Opaque CAD uses early depth rejection in one lighting draw.
                # Intrinsically translucent models retain the depth prepass.
                # Global opacity belongs to the completed image composition.
                if not single_pass:
                    depth = RenderBatch(shader, type=RenderBatch.RenderType.Solid, backface_cull=True,
                        state_setup_callback=lambda gl: gl.glColorMask(False, False, False, False),
                        state_teardown_callback=lambda gl: gl.glColorMask(True, True, True, True))
                    depth.addItem(self.render_transformation(), mesh=self.getMeshData(), **normal)
                    shader.setUniformValue("u_depthOnly", 1)
                    try:
                        depth.render(camera)
                    finally:
                        shader.setUniformValue("u_depthOnly", 0)
                options = dict(state_setup_callback=lambda gl: (gl.glDepthFunc(gl.GL_LEQUAL),
                    gl.glBlendFuncSeparate(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA, gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)),
                    state_teardown_callback=lambda gl: gl.glDepthFunc(gl.GL_LESS))
                batch = RenderBatch(shader, type=RenderBatch.RenderType.Solid, backface_cull=True, **options)
                batch.addItem(self.render_transformation(), mesh=self.getMeshData(), **normal)
                batch.render(camera)
            if self._translucent_mesh is not None:
                batch = RenderBatch(shader, type=RenderBatch.RenderType.Transparent, backface_cull=True,
                    state_setup_callback=lambda gl: gl.glBlendFuncSeparate(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA,
                        gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA))
                batch.addItem(self.render_transformation(), mesh=self._translucent_mesh, **normal)
                batch.render(camera)
