"""Ordinary plugin scene node, independent of Cura's estimated NozzleNode."""
import numpy as np
from UM.Mesh.MeshData import MeshData
from UM.Scene.SceneNode import SceneNode
from UM.View.GL.OpenGL import OpenGL
from UM.View.RenderBatch import RenderBatch
from ..resources.PluginPaths import plugin_path
from ..geometry.ToolheadMaterials import material_parameters, surface_detail, finish_uniforms, material_overrides, painted_materials, local_finish_overrides, local_finish_parameters
from ..geometry.ToolheadOpacity import opacity_overrides, opacity_colours, colour_overrides
from ..geometry.ToolheadRotors import rotors, RotorMotion, rotation, speed
from ..geometry.ToolheadLighting import light_values, validated_lights


from .ToolheadCamera import camera_view

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
            self.node.prepare_environment(self.renderer, camera, gl)
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
        self._surface_detail = .35
        self._material_overrides, self._surface_materials = {}, {}
        self._attached_lights = []
        self._scene_lighting = None
        self._light_bed = self._light_models = True
        self._lighting_enabled = True
        self._reflections_enabled = True
        self._view = self._root = None
        self._render_position = self._render_transform = self._render_normal = None
        self._render_failure = ""
        self._lighting_failure = ""
        self._frame_cache = None
        self._render_context = None
        self._environment = self._environment_scene = None
        self._rotor_render = None
        self._rotor_meshes = {}
        self._rotor_key = None
        self._rotor_motion = RotorMotion()
        self._source_model = None
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
            if self._environment is not None: self._environment.close()
            self._environment = self._environment_scene = None
            self._rotor_render = None
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

    def prepare_environment(self, renderer, camera, gl):
        if self._native_model or self._view is None or self._root is None or not self._lighting_enabled or not self._reflections_enabled: return
        from .ToolheadEnvironment import ToolheadEnvironment
        from .ToolheadEnvironmentScene import ToolheadEnvironmentScene
        if self._environment is None:
            from UM.Application import Application
            window = getattr(Application.getInstance(), "getMainWindow", lambda: None)()
            self._environment = ToolheadEnvironment(window=window)
        if self._environment_scene is None: self._environment_scene = ToolheadEnvironmentScene()
        if not self._environment.ready:
            self._schedule_environment()
            return
        try:
            signature = self._environment_scene.signature(renderer, self._view, self._root)
            point = self.render_position()
            soft = (int(self._view.getCurrentLayer()), float(self._view.getCurrentPath()),
                    round(point.x, 1), round(point.y, 1), round(point.z, 1), self.scene_lighting_signature())
            self._environment.step(gl, self._render_context, signature[0], soft,
                lambda: self._environment_scene.snapshot(self, renderer, camera, signature))
            self._schedule_environment()
        except Exception as error:
            # Unsupported optional capture keeps ordinary shading available.
            self._environment.fail(error)

    def _schedule_environment(self):
        owner = self._environment
        pending = getattr(self, "_environment_wake", None)
        if pending is not None and pending[0] is owner: return
        from UM.Application import Application
        from PyQt6.QtCore import QTimer
        app = Application.getInstance()
        delay = int(owner.wake_delay * 1000)
        token = owner, object()
        self._environment_wake = token
        def request():
            if self._environment_wake is token: self._environment_wake = None
            if self._environment is owner and self.visible_for_render() and self._lighting_enabled and self._reflections_enabled:
                window = app.getMainWindow()
                if window is not None: window.update()
        app.callLater(lambda: QTimer.singleShot(max(1, delay), request) if delay else request())

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

    def set_material_finishes(self, value):
        self._material_overrides = material_overrides(value)

    def set_surface_materials(self, value):
        checked = painted_materials(value, self._source_model) if self._source_model is not None else {}
        if checked != self._surface_materials:
            self._surface_materials = checked
            if self._source_model is not None:
                self._build_meshes(self._rotor_motion.rows)

    def set_local_appearance(self, materials, body_finishes, face_finishes):
        if self._source_model is None: return
        values = (painted_materials(materials, self._source_model, bodies=True),
            local_finish_overrides(body_finishes, self._source_model, bodies=True),
            local_finish_overrides(face_finishes, self._source_model))
        if values == (self._body_materials, self._body_finishes, self._face_finishes): return
        self._body_materials, self._body_finishes, self._face_finishes = values
        self._build_meshes(self._rotor_motion.rows)

    def set_opacity_overrides(self, bodies, faces):
        if self._source_model is None: return
        bodies = opacity_overrides(bodies, self._source_model, bodies=True)
        faces = opacity_overrides(faces, self._source_model)
        if (bodies, faces) == (self._body_opacity, self._face_opacity): return
        self._body_opacity, self._face_opacity = bodies, faces
        colours = opacity_colours(self._source_model, bodies, faces)
        self._transparent_body_count = len(np.unique(self._source_model.body_ids[(colours[:, 3] > 0) & (colours[:, 3] < .999)]))
        rows = self._rotor_motion.rows if self._transparent_body_count <= 64 else []
        self._rotor_key = None
        self._build_meshes(rows)

    def set_colour_overrides(self, bodies, faces):
        if self._source_model is None: return
        bodies = colour_overrides(bodies, self._source_model, bodies=True)
        faces = colour_overrides(faces, self._source_model)
        if (bodies, faces) == (self._body_colours, self._face_colours): return
        self._body_colours, self._face_colours = bodies, faces
        self._build_meshes(self._rotor_motion.rows)

    def set_surface_detail(self, value):
        self._surface_detail = surface_detail(value)

    def set_attached_lights(self, lights):
        self._attached_lights = validated_lights(lights)

    def set_scene(self, view, root):
        if view is not self._view or root is not self._root:
            self._restore_simulation()
            if self._environment is not None: self._environment.close()
            self._environment = self._environment_scene = None
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

    def set_reflections_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._reflections_enabled: return
        self._reflections_enabled = enabled
        if not enabled:
            if self._environment is not None: self._environment.close()
            self._environment = self._environment_scene = None
            self._environment_wake = None

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
        if self._environment is not None: self._environment.close()
        self._environment = self._environment_scene = None
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
        self._depth_seed = self._depth_revision = self._rotor_render = None
        self._opaque_shader_cache = None

    def set_model(self, model, tip):
        if self._environment is not None: self._environment.close()
        self._environment = self._environment_scene = None
        self._native_model = False
        self._shader = None
        self._render_failure = ""
        self._lighting_failure = ""
        self._scene_lighting = None
        self._tip = np.asarray(tip)
        self._source_model = model
        self._body_materials, self._body_finishes, self._face_finishes = {}, {}, {}
        self._body_opacity, self._face_opacity = {}, {}
        self._body_colours, self._face_colours = {}, {}
        self._surface_materials = {}
        self._transparent_body_count = len(np.unique(model.body_ids[(model.colours[:,3] > 0) & (model.colours[:,3] < .999)]))
        self._rotor_key = None
        self._rotor_meshes = {}
        self._rotor_motion = RotorMotion()
        self._build_meshes([])

    def _build_meshes(self, rows):
        model, tip = self._source_model, self._tip
        points = model.triangles.reshape(-1, 3)
        # Source Z-up mm to Cura Y-up; winding is preserved by this rotation.
        anchor = np.asarray(tip, dtype=np.float32)
        vertices = np.empty_like(points)
        vertices[:, 0] = points[:, 0] - anchor[0]
        vertices[:, 1] = points[:, 2] - anchor[2]
        vertices[:, 2] = anchor[1] - points[:, 1]
        triangles = vertices.reshape(-1, 3, 3)
        self._model_bounds = vertices.min(axis=0), vertices.max(axis=0)
        # Use the same CAD-face normals as the editor, rotated into Cura's
        # Y-up frame without welding across face/material/body boundaries.
        normals = model.vertex_normals[:, :, [0, 2, 1]] * np.array([1, 1, -1], dtype=np.float32)
        materials = material_parameters(model, self._surface_materials, self._body_materials)
        finishes = local_finish_parameters(model, self._body_finishes, self._face_finishes)
        colours = opacity_colours(model, self._body_opacity, self._face_opacity, body_colours=self._body_colours, face_colours=self._face_colours)
        opaque = colours[:, 3] >= .999
        visible = colours[:, 3] > 0
        self._mesh_centres = {}
        def batch(mask):
            mask = mask & visible
            if not np.any(mask): return None
            result = MeshData(vertices=triangles[mask].reshape(-1, 3), normals=normals[mask].reshape(-1, 3),
                colors=np.repeat(colours[mask], 3, axis=0),
                            attributes={"toolhead_finish": dict(value=np.repeat(finishes[mask], 3, axis=0),
                                opengl_type="vector2f", opengl_name="a_finish"),
                                "toolhead_surface": dict(value=np.repeat(model.surfaces[mask], 3).astype(np.float32),
                                opengl_type="float", opengl_name="a_surface"),
                                "toolhead_material": dict(value=np.repeat(materials[mask], 3, axis=0),
                                    opengl_type="vector4f", opengl_name="a_material")})
            self._mesh_centres[id(result)] = np.mean(triangles[mask].reshape(-1,3),axis=0)
            return result
        static = np.ones(len(opaque), dtype=bool)
        self._rotor_meshes = {}
        for row in rows:
            mask = model.body_ids == row['body']
            static &= ~mask
            self._rotor_meshes[row['body']] = batch(mask & opaque), batch(mask & ~opaque)
            selected = triangles[mask].reshape(-1,3)
            if len(selected):
                centre = np.array(row['centre'])-self._tip
                centre = centre[[0,2,1]] * [1,1,-1]
                radius = np.linalg.norm(selected-centre,axis=1).max()
                low,high = self._model_bounds
                self._model_bounds = np.minimum(low,centre-radius),np.maximum(high,centre+radius)
        self.setMeshData(batch(opaque & static))
        self._translucent_mesh = batch(~opaque & static)
        self._static_transparent = [batch(~opaque & static & (model.body_ids == body))
            for body in np.unique(model.body_ids[~opaque & static & visible])] if rows else []
        self._rotor_retry = 0.
        self._rotor_status = ""
        self._frame_cache = self._rotor_render = None

    def set_rotors(self, values, readings):
        if self._source_model is None: return
        rows = rotors(values, self._source_model)
        # Bound per-pose transparent body ordering/draw count too.
        limited = bool(rows and self._transparent_body_count > 64)
        if limited: rows = []
        key = repr(rows)
        if key != self._rotor_key:
            self._rotor_key = key
            self._build_meshes(rows)
        self._rotor_motion.configure(rows, readings)
        if limited: self._rotor_status = "Fan animation unavailable for more than 64 translucent bodies."

    def animation_status(self): return getattr(self, "_rotor_status", "")

    def rotors_moving(self):
        return any(any(mesh is not None for mesh in self._rotor_meshes.get(row['body'], ()))
                   and speed(row, self._rotor_motion.readings)[0] > 0
                   for row in self._rotor_motion.rows)

    def _animate_rotors(self, gl, static, camera, size):
        from .ToolheadRotorRender import ToolheadRotorRender
        from UM.Math.Matrix import Matrix
        if self._rotor_render is None: self._rotor_render = ToolheadRotorRender()
        poses = self._rotor_motion.sample()
        current_target = [None]
        base = self.render_transformation().getData()
        convert = np.array(((1,0,0),(0,0,1),(0,-1,0)))
        def render(cropped_camera, fraction):
            if current_target[0] is None: current_target[0] = self._rotor_render._work
            transparent = []
            for row, phase, blur, _label in poses:
                centre = convert @ (np.array(row['centre'])-self._tip)
                axis = convert @ row['axis']
                local = rotation(centre,axis,phase+row['direction']*blur*fraction)
                transform = Matrix(base @ local)
                normal = Matrix(transform.getData()); normal.setRow(3,[0,0,0,1]); normal.setColumn(3,[0,0,0,1]); normal.invert(); normal.transpose()
                opaque, translucent = self._rotor_meshes[row['body']]
                self._draw_mesh(cropped_camera,opaque,None,transform,normal)
                if translucent is not None: transparent.append((translucent,transform,normal))
            # Translucent housing must composite after the moving opaque fan.
            # It cannot be baked into colour before the rotor overwrites it.
            for mesh in self._static_transparent:
                transparent.append((mesh,self.render_transformation(),self._render_normal))
            eye = cropped_camera.getWorldPosition()
            position = np.array((eye.x,eye.y,eye.z))
            orthographic, direction = self._camera_view(cropped_camera)
            def distance(item):
                mesh, transform, _normal = item
                centre = self._mesh_centres[id(mesh)]
                world = transform.getData() @ np.append(centre,1.)
                if orthographic: return -float(world[:3] @ direction)
                return float(np.sum((world[:3]-position)**2))
            for mesh, transform, normal in sorted(transparent,key=distance,reverse=True):
                self._draw_mesh(cropped_camera,None,mesh,transform,normal)
            if self._depth_seed is not None:
                self._occlusion.overlay(gl, current_target[0], cropped_camera)
        import time
        if time.monotonic() >= self._rotor_retry:
            try:
                result = self._rotor_render.combine(gl,static,camera,size,render,
                    blurred=any(blur>.02 for _row,_phase,blur,_label in poses))
                self._rotor_status = ""
                return result
            except Exception:
                self._rotor_status = "Fan blur unavailable; sharp rotation shown."
                self._rotor_retry = time.monotonic()+5.
        # Optional shutter storage must not hide an otherwise valid head.
        # Single-pose fallback uses freshly rebuilt static colour/depth.
        self._frame_cache._key = None
        static.bind()
        current_target[0] = static
        render(camera,0.)
        return static

    def set_native_model(self, mesh):
        """Use Cura's already Y-up nozzle mesh without CAD transforms/effects."""
        if self._environment is not None: self._environment.close()
        self._environment = self._environment_scene = self._rotor_render = None
        self._static_transparent = []
        self._mesh_centres = {}
        self._occlusion = self._depth_seed = self._depth_revision = None
        self._opaque_shader_cache = None
        self._rotor_status = ""
        self.set_simulation_active(False)
        self._native_model = True
        self._shader = self._scene_lighting = self._frame_cache = None
        self._render_failure = self._lighting_failure = ""
        self._translucent_mesh = None
        self._rotor_meshes = {}
        self._source_model = None
        self._rotor_motion = RotorMotion()
        self._opacity = 1.0
        self._attached_lights = []
        self.setMeshData(mesh)

    def render(self, renderer):
        if self.isVisible() and (self.getMeshData() is not None or self._translucent_mesh is not None or self._rotor_meshes):
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
        state = (id(self.getMeshData()), id(self._translucent_mesh), self._lighting_enabled, self._reflections_enabled,
            self._lights, repr(self._attached_lights), self._surface_detail, repr(self._material_overrides),
            (self._environment.available, self._environment.revision) if self._environment else None)
        self._frame_cache.draw(OpenGL.getInstance().getBindingsObject(), camera, self._model_bounds,
            self.render_transformation(), state, self._draw, opacity=self._opacity,
            depth_seed=self._depth_seed, depth_revision=self._depth_revision,
            post_render=self._occlusion.overlay if self._depth_seed is not None and not self._rotor_meshes else None,
            animate=self._animate_rotors if self._rotor_meshes else None)

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
        self._draw_mesh(camera,self.getMeshData(),None if self._rotor_meshes else self._translucent_mesh,self.render_transformation(),self._render_normal)

    @staticmethod
    def _camera_view(camera):
        return camera_view(camera)

    def _draw_mesh(self, camera, opaque, translucent, transform, normal_matrix):
        normal = {"normal_transformation": normal_matrix} if normal_matrix is not None else {}
        if opaque is not None or translucent is not None:
            single_pass = translucent is None
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
            orthographic, direction = self._camera_view(camera)
            shader.setUniformValue("u_orthographic", int(orthographic))
            shader.setUniformValue("u_viewDirection", direction.tolist())
            shader.setUniformValue("u_opacity", 1.0)
            shader.setUniformValue("u_lightingEnabled", int(self._lighting_enabled))
            shader.setUniformValue("u_surfaceDetail", self._surface_detail)
            for name, value in finish_uniforms(self._material_overrides).items(): shader.setUniformValue(name, value)
            shader.setUniformValue("u_materialEditEnabled", 0)
            if self._environment is not None: self._environment.apply(shader)
            else:
                from .ToolheadEnvironment import TEXTURE_UNIT, DEPTH_UNIT
                shader.setUniformValue("u_environmentEnabled", 0)
                # ShaderProgram binds registered textures even when sampling is disabled.
                try:
                    shader.setTexture(TEXTURE_UNIT, None)
                finally:
                    shader.setTexture(DEPTH_UNIT, None)
            self.apply_attached_lights(shader)
            width, depth, height = self._lights
            for index, (position, direction) in enumerate((
                    ([-width/2, height, 0], [1, -1, 0]),
                    ([width/2, height, 0], [-1, -1, 0]),
                    ([0, height, -depth/2], [0, -1, 1]),
                    ([0, height, depth/2], [0, -1, -1]))):
                shader.setUniformValue("u_light" + str(index), position)
                shader.setUniformValue("u_direction" + str(index), direction)
            try:
                if opaque is not None:
                    # Opaque CAD uses early depth rejection in one lighting draw.
                    # Intrinsically translucent models retain the depth prepass.
                    # Global opacity belongs to the completed image composition.
                    if not single_pass:
                        depth = RenderBatch(shader, type=RenderBatch.RenderType.Solid, backface_cull=True,
                            state_setup_callback=lambda gl: gl.glColorMask(False, False, False, False),
                            state_teardown_callback=lambda gl: gl.glColorMask(True, True, True, True))
                        depth.addItem(transform, mesh=opaque, **normal)
                        shader.setUniformValue("u_depthOnly", 1)
                        try:
                            depth.render(camera)
                        finally:
                            shader.setUniformValue("u_depthOnly", 0)
                    options = dict(state_setup_callback=lambda gl: (gl.glDepthFunc(gl.GL_LEQUAL),
                        gl.glBlendFuncSeparate(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA, gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)),
                        state_teardown_callback=lambda gl: gl.glDepthFunc(gl.GL_LESS))
                    batch = RenderBatch(shader, type=RenderBatch.RenderType.Solid, backface_cull=True, **options)
                    batch.addItem(transform, mesh=opaque, **normal)
                    batch.render(camera)
                if translucent is not None:
                    batch = RenderBatch(shader, type=RenderBatch.RenderType.Transparent, backface_cull=True,
                        state_setup_callback=lambda gl: gl.glBlendFuncSeparate(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA,
                            gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA))
                    batch.addItem(transform, mesh=translucent, **normal)
                    batch.render(camera)
            finally:
                try:
                    shader.release()  # Uranium RenderBatch does not unwind failed binds/draws.
                finally:
                    if self._environment is not None: self._environment.release_bindings()
