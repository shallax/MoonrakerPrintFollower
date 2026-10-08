"""Offscreen production GLSL: authored finishes, alpha and model-anchored grain."""
import configparser
import unittest

import numpy as np

from mpf.geometry.ToolheadGeometry import mesh_from_arrays, preview_buffer
from mpf.geometry.ToolheadOpacity import opacity_colours
from tools.capture_toolhead import create_context, program, uniforms

try:
    import moderngl
except ImportError:
    moderngl = None


@unittest.skipIf(moderngl is None, "The capture OpenGL runtime is required")
class MaterialRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        print("Material test renderer:", cls.context.info["GL_RENDERER"], flush=True)
        cls.anchor = cls.context.simple_framebuffer((1,1))
        cls.anchor.use()

    @classmethod
    def tearDownClass(cls):
        cls.anchor.release()
        cls.context.release()
        if cls.dll_directory is not None:
            cls.dll_directory.close()

    def release_target(self, target):
        self.anchor.use()
        target.release()

    def render(self, name, detail, *, alpha=.35, translation=0, orthographic=False, eye_offset=0, environment=False, local_finish=None, colour=None):
        metadata = {"materials": [dict(name=name, description="", source="step-material")],
                    "bodies": [dict(name="Part", source="unknown", centre=None, axis=None)]}
        mesh = mesh_from_arrays([[[-1, -1, 0], [1, -1, 0], [1, 1, 0]],
                                 [[-1, -1, 0], [1, 1, 0], [-1, 1, 0]]],
                                [[.6, .25, .1, alpha]] * 2, metadata=metadata)
        shader = program(self.context, "toolhead.shader")
        self.addCleanup(shader.release)
        buffer = self.context.buffer(preview_buffer(mesh, colours=opacity_colours(mesh, body_colours={"0": colour} if colour else None), face_finishes={"0": local_finish, "1": local_finish} if local_finish else None)[0])
        self.addCleanup(buffer.release)
        vao = self.context.vertex_array(shader, [(buffer, "3f 3f 4f 1f 4f 1f 2f",
                                        "a_vertex", "a_normal", "a_color", "a_surface", "a_material", "a_body", "a_finish")])
        self.addCleanup(vao.release)
        target = self.context.simple_framebuffer((128, 128), components=4, dtype="f4")
        self.addCleanup(self.release_target, target)
        target.use()
        target.clear(0, 0, 0, 0)
        self.context.disable(moderngl.BLEND | moderngl.CULL_FACE | moderngl.DEPTH_TEST)
        identity = np.eye(4, dtype=np.float32)
        model = identity.copy(); model[3, 0] = translation
        projection = identity.copy(); projection[3, 0] = -translation
        values = dict(u_modelMatrix=model, u_normalMatrix=identity, u_viewMatrix=identity,
                      u_projectionMatrix=projection, u_surfaceDetail=detail, u_opacity=1.,
                      u_lightingEnabled=1, u_depthOnly=0, u_attachedCount=0,
                      u_viewPosition=(translation+2+eye_offset, 2, 5),
                      u_orthographic=int(orthographic), u_viewDirection=(0, 0, 1))
        if environment:
            cube = self.context.texture_cube((4, 4), 3)
            self.addCleanup(cube.release)
            for face, colour in enumerate(((255,0,0), (0,255,0), (0,0,255), (255,255,0), (255,0,255), (0,255,255))):
                cube.write(face, bytes(colour)*16)
            cube.build_mipmaps()
            cube.use(location=7)
            depth = self.context.texture_cube((4,4), 1, dtype='f4')
            self.addCleanup(depth.release)
            depth.filter = (moderngl.NEAREST, moderngl.NEAREST)
            near,far = .2,30.
            hardware = ((far+near-2*far*near/10.)/(far-near)+1)/2
            for face in range(6): depth.write(face, np.full(16,hardware,np.float32).tobytes())
            depth.use(location=6)
            values.update(u_environmentEnabled=1, u_environment=7, u_sceneDepth=6, u_probe=(0,0,0),
                u_sceneMin=(-11,-11,-11),u_sceneMax=(11,11,11),u_probeNear=near,u_probeFar=far)
        for index in range(4):
            values["u_light" + str(index)] = (translation-1, 1, 4)
            values["u_direction" + str(index)] = (0, 0, -1)
        uniforms(shader, values)
        vao.render()
        return np.frombuffer(target.read(components=4, dtype="f4"), dtype=np.float32).reshape(128, 128, 4).copy()

    def test_body_rgb_override_changes_actual_pixels_and_retains_alpha(self):
        original = self.render("ABS", 0)
        green = self.render("ABS", 0, colour="#00ff00")
        np.testing.assert_array_equal(original[:, :, 3], green[:, :, 3])
        self.assertGreater(float(green[:, :, 1].mean()), float(green[:, :, 0].mean()) * 2)
        self.assertGreater(np.count_nonzero(np.any(abs(original[:, :, :3]-green[:, :, :3]) > .01, axis=2)), 1000)

    def test_plastic_grain_changes_light_only_and_zero_is_smooth(self):
        smooth = self.render("ABS", 0)
        detailed = self.render("ABS", 1)
        self.assertGreater(np.count_nonzero(np.any(np.abs(smooth[:, :, :3]-detailed[:, :, :3]) > .0005, axis=2)), 1000)
        np.testing.assert_array_equal(smooth[:, :, 3], detailed[:, :, 3])
        np.testing.assert_allclose(detailed[:, :, 3], .35, atol=1e-6)
        np.testing.assert_array_equal(self.render("ABS", 0), smooth)

    def test_glass_and_metal_do_not_gain_generated_plastic_grain(self):
        for name in ("Glass", "Aluminium"):
            with self.subTest(name=name):
                np.testing.assert_array_equal(self.render(name, 0), self.render(name, 1))

    def test_authored_roughness_changes_highlights_without_changing_alpha(self):
        polished = self.render("Polished steel", 0)
        blasted = self.render("Blasted steel", 0)
        self.assertGreater(np.count_nonzero(np.abs(polished[:, :, :3]-blasted[:, :, :3]) > .001), 1000)
        np.testing.assert_array_equal(polished[:, :, 3], blasted[:, :, 3])

    def test_grain_follows_the_model_instead_of_sliding_in_world_space(self):
        original = self.render("ABS", 1)
        moved = self.render("ABS", 1, translation=3)
        np.testing.assert_allclose(original, moved, atol=2e-6)

    def test_receiver_forward_and_deferred_light_function_uses_parallel_ortho_eye(self):
        shader = program(self.context, 'scene-lighting.shader')
        vertices = np.array([[-.8,-.8,0],[.8,-.8,0],[0,.8,0]],np.float32)
        packed = np.concatenate((vertices,np.tile([0,0,1],(3,1)),np.tile([.2,.2,.2,.7],(3,1))),axis=1).astype('f4')
        buffer = self.context.buffer(packed.tobytes())
        vao = self.context.vertex_array(shader, [(buffer,'3f 3f 4f','a_vertex','a_normal','a_color')])
        target = self.context.simple_framebuffer((64,64),components=4,dtype='f4')
        identity = np.eye(4,dtype='f4')
        uniforms(shader,dict(u_modelMatrix=identity,u_normalMatrix=identity,u_projectionMatrix=identity,u_viewMatrix=identity,u_hasColour=1,u_depthOnly=0,u_lightOpacity=1,u_attachedCount=1,u_viewDirection=(0,0,1)))
        shader['u_attachedPosition'].value=[(0,0,2)]*8
        shader['u_attachedDirection'].value=[(0,0,-1)]*8
        shader['u_attachedColour'].value=[(.2,.2,.2)]*8
        shader['u_attachedRange'].value=[5.]*8
        def pixels(ortho, x):
            target.use();target.clear(0,0,0,0)
            uniforms(shader,dict(u_orthographic=ortho,u_viewPosition=(x,0,3)))
            self.context.disable(moderngl.BLEND|moderngl.CULL_FACE|moderngl.DEPTH_TEST)
            vao.render()
            return np.frombuffer(target.read(components=4,dtype='f4'),np.float32).copy()
        try:
            np.testing.assert_array_equal(pixels(1,0),pixels(1,20))
            self.assertGreater(np.count_nonzero(pixels(0,0)!=pixels(0,20)),100)
            self.assertEqual(self.context.error,'GL_NO_ERROR')
        finally:
            self.release_target(target);vao.release();buffer.release();shader.release()

    def test_orthographic_reflections_and_highlights_use_parallel_view_rays(self):
        for environment in (False, True):
            with self.subTest(environment=environment):
                original = self.render("Polished steel", 0, orthographic=True, environment=environment)
                # Panning changes camera position, not orthographic ray direction.
                moved = self.render("Polished steel", 0, orthographic=True, eye_offset=20, environment=environment)
                np.testing.assert_array_equal(original, moved)
                perspective = self.render("Polished steel", 0, environment=environment)
                shifted = self.render("Polished steel", 0, eye_offset=20, environment=environment)
                self.assertGreater(np.count_nonzero(np.abs(perspective[:, :, :3]-shifted[:, :, :3]) > .001), 1000)
                self.assertGreater(np.count_nonzero(np.abs(original[:, :, :3]-perspective[:, :, :3]) > .001), 1000)

    def test_local_roughness_and_reflectivity_reach_real_pixels_without_alpha_changes(self):
        original = self.render("ABS", 0, environment=True)
        polished = self.render("ABS", 0, environment=True, local_finish={'roughness': .05, 'reflectivity': .9})
        self.assertGreater(np.count_nonzero(np.abs(original[:, :, :3]-polished[:, :, :3]) > .01), 1000)
        np.testing.assert_array_equal(original[:, :, 3], polished[:, :, 3])

    def depth_direction(self, position, direction, *, hole=False, enclosure=False, jump=False, second_hit=False):
        from pathlib import Path
        source = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        source.read(Path(__file__).parents[1] / 'mpf/toolhead/toolhead.shader')
        fragment = source['shaders']['fragment41core']
        trace = fragment[fragment.index('float radialResidual'):fragment.index('vec3 environmentReflection')]
        shader = self.context.program(vertex_shader='''#version 410
            void main() { vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.); }''',
            fragment_shader='''#version 410
            uniform samplerCube u_sceneDepth;
            uniform vec3 u_probe, u_sceneMin, u_sceneMax, v_position, ray;
            uniform float u_probeNear, u_probeFar;
            out vec4 colour;
            ''' + trace + '\nvoid main(){colour=vec4(localReflectionDirection(normalize(ray)),1.);}')
        self.addCleanup(shader.release)
        cube = self.context.texture_cube((512,512), 1, dtype='f4')
        cube.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.addCleanup(cube.release)
        uv = (np.arange(512)+.5)/512*2-1
        x,y = np.meshgrid(uv,uv)
        faces = ((np.ones_like(x),-y,-x),(-np.ones_like(x),-y,x),(x,np.ones_like(x),y),
                 (x,-np.ones_like(x),-y),(x,-y,np.ones_like(x)),(-x,-y,-np.ones_like(x)))
        near,far = .2,30.
        for face, axes in enumerate(faces):
            rays = np.stack(axes,axis=-1)
            if enclosure: forward = np.full(x.shape,10.)
            else:
                with np.errstate(divide='ignore',invalid='ignore'):
                    forward = 10. / rays[:,:,2]
                if jump: forward = np.where(rays[:,:,0] < .3, 5., 15.) / np.maximum(rays[:,:,2], .000001)
                if second_hit:
                    ratio = rays[:,:,0] / np.maximum(rays[:,:,2], .000001)
                    plane = np.where((ratio>.4286)&(ratio<.6),3.,12.)
                    forward = plane / np.maximum(rays[:,:,2], .000001)
            depth = ((far+near-2*far*near/np.maximum(forward,.000001))/(far-near)+1)/2
            depth = np.where((forward>near)&(forward<far),depth,1.)
            if hole: depth[:]=1.
            # The production cube uses24-bit hardware depth, not infinite precision.
            depth = np.round(depth*((1<<24)-1))/((1<<24)-1)
            cube.write(face,np.asarray(depth,dtype='f4').tobytes())
        cube.use(location=6)
        limit = 16 if second_hit else 11
        uniforms(shader,dict(u_sceneDepth=6,u_probe=(0,0,0),u_sceneMin=(-limit,-limit,-limit),u_sceneMax=(limit,limit,limit),
            u_probeNear=near,u_probeFar=far,v_position=position,ray=direction))
        target = self.context.simple_framebuffer((1,1),components=4,dtype='f4')
        self.addCleanup(self.release_target, target)
        target.use(); self.context.disable(moderngl.DEPTH_TEST|moderngl.CULL_FACE|moderngl.BLEND)
        vao=self.context.vertex_array(shader,[]); self.addCleanup(vao.release); vao.render(vertices=3)
        self.assertEqual(self.context.error,'GL_NO_ERROR')
        return np.frombuffer(target.read(components=4,dtype='f4'),np.float32)[:3].copy()

    def test_depth_parallax_hits_the_actual_plane_from_an_offset_surface(self):
        actual = self.depth_direction((3,0,0),(0,0,1))
        expected = np.array([3,0,10])/np.linalg.norm([3,0,10])
        np.testing.assert_allclose(actual,expected,atol=.004)
        self.assertGreater(actual[0],.28, 'a direction-only map would return zero X')

    def test_depth_background_and_silhouette_jumps_do_not_invent_a_hit(self):
        np.testing.assert_array_equal(self.depth_direction((3,0,0),(0,0,1),hole=True),[0,0,0])
        np.testing.assert_array_equal(self.depth_direction((3,0,0),(0,0,1),jump=True),[0,0,0])

    def test_six_face_depth_reconstruction_and_cube_seams(self):
        for direction in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1),(1,1,0),(1,0,1)):
            with self.subTest(direction=direction):
                actual=self.depth_direction((0,0,0),direction,enclosure=True)
                np.testing.assert_allclose(actual,np.array(direction)/np.linalg.norm(direction),atol=.003)

    def test_false_silhouette_bracket_does_not_hide_a_later_visible_plane(self):
        actual=self.depth_direction((3,0,0),(0,0,1),second_hit=True)
        np.testing.assert_allclose(actual,np.array([3,0,12])/np.linalg.norm([3,0,12]),atol=.004)
