"""Original PBR direct terms, depth-cube visibility and actual raster position."""
import configparser
import ctypes
from pathlib import Path
import unittest

import numpy as np

from mpf.geometry.ToolheadGeometry import mesh_from_arrays, preview_buffer
from mpf.toolhead.ToolheadShadowShader import head_fragment, scene_fragment, visibility_source
from mpf.toolhead.ToolheadShadowValues import ShadowProjection
from tools.capture_toolhead import create_context, uniforms

try:
    import moderngl
except ImportError:
    moderngl = None


ROOT = Path(__file__).resolve().parents[1]


def sources(name):
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
    parser.read(ROOT / 'mpf/toolhead' / name)
    return parser['shaders']


class ShadowSourceTests(unittest.TestCase):
    def test_disabled_variants_are_original_objects_and_changed_contracts_refuse(self):
        for name, adapter in (('toolhead.shader', head_fragment), ('scene-lighting.shader', scene_fragment)):
            for variant in ('fragment', 'fragment41core'):
                original = sources(name)[variant]
                self.assertIs(adapter(original, enabled=False), original)
                legacy = variant == 'fragment'
                rewritten = adapter(original, enabled=True, legacy=legacy)
                self.assertIn('shadowVisibility(', rewritten)
                if not legacy:
                    self.assertTrue(rewritten.lstrip().startswith('#version 410\n'))
                # Every edit has a whole-source contract: neither missing nor
                # duplicated direct-light definitions may partly enable maps.
                tokens = ('float led(vec3 position, vec3 direction, vec3 normal)', 'float energy = outward * falloff*falloff;',
                          'float specular = pow(max(dot(normal, halfVector), 0.0), shininess);') if name == 'toolhead.shader' else (
                              'vec3 lightSurface(vec3 position, vec3 surfaceNormal, vec3 baseColour)', 'float energy = outward * falloff * falloff;',
                              'lightSurface(f_vertex, f_normal, f_color.rgb)')
                for token in tokens:
                    for changed in (original.replace(token, 'changed'), original+token):
                        with self.assertRaises(ValueError):
                            adapter(changed, enabled=True, legacy=legacy)

    def test_core_lod_is_explicit_and_legacy_uses_named_compatible_samplers(self):
        for count in (8, 12):
            self.assertIn('textureLod(u_shadowMap%d, direction, 0.0)' % (count-1), visibility_source(count, False))
            legacy = visibility_source(count, True)
            self.assertIn('textureCube(u_shadowMap%d, direction)' % (count-1), legacy)
            self.assertNotIn('samplerCubeShadow', legacy)
            self.assertNotIn('textureLod', legacy)
        with self.assertRaises(ValueError):
            visibility_source(9, False)


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class ShadowShaderRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        print('Shadow shader renderer:', cls.context.info['GL_RENDERER'], flush=True)
        cls.anchor = cls.context.simple_framebuffer((1, 1))
        cls.anchor.use()

    @classmethod
    def tearDownClass(cls):
        cls.anchor.release(); cls.context.release()
        if cls.dll_directory is not None:
            cls.dll_directory.close()

    def target(self, dtype='f4'):
        target = self.context.simple_framebuffer((24, 24), components=4, dtype=dtype)
        def close():
            self.anchor.use(); target.release()
        self.addCleanup(close)
        target.use(); target.clear(0, 0, 0, 0)
        self.context.disable(moderngl.BLEND | moderngl.CULL_FACE | moderngl.DEPTH_TEST)
        return target

    def cube(self, depth):
        cube = self.context.texture_cube((4, 4), 1, dtype='f4')
        self.addCleanup(cube.release)
        cube.filter = (moderngl.NEAREST, moderngl.NEAREST)
        values = np.broadcast_to(np.asarray(depth, dtype='f4'), (4, 4)).tobytes()
        for face in range(6):
            cube.write(face, values)
        return cube

    def maps(self, shader, count, *, enabled=(), depth=.2, near=.01, far=30, bias=0, depth_error=0):
        cube = self.cube(depth)
        for index in range(count):
            unit = index if count == 12 and index < 4 else index+4 if count == 12 else index+8
            cube.use(location=unit)
            shader['u_shadowMap%d' % index].value = unit
        shader['u_shadowValid'].value = tuple(int(i in enabled) for i in range(count))
        shader['u_shadowClip'].value = tuple((near, far, bias) for _ in range(count))
        shader['u_shadowProjection'].value = (ShadowProjection((0, 0, 0), near, far).depth_coefficients,)*count
        uniforms(shader, {'u_shadowDepthError': (depth_error,)*count})

    def test_both_original_head_and_scene_core_variants_compile(self):
        for name, adapter in (('toolhead.shader', head_fragment), ('scene-lighting.shader', scene_fragment)):
            resource = sources(name)
            shader = self.context.program(vertex_shader=resource['vertex41core'],
                fragment_shader=adapter(resource['fragment41core'], enabled=True))
            shader.release()
        self.assertEqual(self.context.error, 'GL_NO_ERROR')

    def head(self, *, shadows, blocked=(), attached=False, paint=False, environment=False, dtype='f4',
             normal_sign=1, map_depth=.2, expose_lookup=False, force_lookup=False):
        resource = sources('toolhead.shader')
        fragment = head_fragment(resource['fragment41core'], enabled=shadows)
        if expose_lookup:
            # Test-only observable lookup result. The production comparison
            # maps a NaN sample to finite0, which alone cannot prove a skip.
            fragment = fragment.replace('return reference <= stored + u_shadowDepthError[slot] ? 1.0 : 0.0;', 'return stored;')
        if force_lookup:
            fragment = fragment.replace('if (facing > 0.0 || broadHighlight > 0.0)', '')
        shader = self.context.program(vertex_shader=resource['vertex41core'],
            fragment_shader=fragment)
        self.addCleanup(shader.release)
        mesh = mesh_from_arrays([[[-1, -1, 0], [1, -1, 0], [1, 1, 0]],
                                 [[-1, -1, 0], [1, 1, 0], [-1, 1, 0]]], [[.6, .25, .1, .35]]*2)
        vertices = np.frombuffer(preview_buffer(mesh)[0], dtype='f4').copy().reshape(-1, 18)
        # Both triangles form one selected CAD surface, not two independently
        # classified faces. The paint fixture must exercise all its pixels.
        vertices[:, 10] = 0
        buffer = self.context.buffer(vertices.tobytes()); self.addCleanup(buffer.release)
        vao = self.context.vertex_array(shader, [(buffer, '3f 3f 4f 1f 4f 1f 2f',
            'a_vertex', 'a_normal', 'a_color', 'a_surface', 'a_material', 'a_body', 'a_finish')])
        self.addCleanup(vao.release)
        target = self.target(dtype)
        empty = self.cube(1.); empty.use(location=6); empty.use(location=7)
        identity = np.eye(4, dtype='f4')
        normal = identity.copy(); normal[2, 2] = normal_sign
        values = dict(u_modelMatrix=identity, u_normalMatrix=normal, u_viewMatrix=identity,
            u_projectionMatrix=identity, u_opacity=.8, u_lightingEnabled=1,
            u_orthographic=1, u_viewDirection=(0, 0, 1), u_viewPosition=(0, 0, 5),
            u_attachedCount=1 if attached else 0, u_environment=7, u_sceneDepth=6)
        for index in range(4):
            values['u_light%d' % index] = (0, 0, 4)
            values['u_direction%d' % index] = (0, 0, -1)
        if attached:
            values.update(u_attachedPosition=[(0, 0, 4)]*8, u_attachedDirection=[(0, 0, -1)]*8,
                u_attachedColour=[(.2, .4, .6)]*8, u_attachedRange=[20.]*8,
                u_attachedSurface=[0.]*8, u_attachedPaint=[(.15, .35, .55, float(paint))]*8)
        if environment:
            colour = self.context.texture_cube((4, 4), 3)
            self.addCleanup(colour.release)
            for face in range(6): colour.write(face, bytes((100, 180, 220))*16)
            colour.build_mipmaps(); colour.use(location=7)
            # Certify a real captured surface. Empty depth used to exercise
            # the erroneous dark miss substitute rather than a reflection.
            near, far = .01, 30.
            forward = 5.
            depth = self.cube(((far+near-2*far*near/forward)/(far-near)+1)/2)
            depth.use(location=6)
            values.update(u_environmentEnabled=1, u_environment=7, u_sceneDepth=6,
                u_probe=(0, 0, 0), u_sceneMin=(-10, -10, -10), u_sceneMax=(10, 10, 10),
                u_probeNear=.01, u_probeFar=30.)
        if shadows: self.maps(shader, 12, enabled=blocked, depth=map_depth)
        uniforms(shader, values); vao.render()
        self.assertEqual(self.context.error, 'GL_NO_ERROR')
        return np.frombuffer(target.read(components=4, dtype=dtype), dtype='f4' if dtype == 'f4' else 'u1').reshape(24, 24, 4).copy()

    def test_disabled_slots_exact_original_and_all_white_blocked_preserve_ambient_alpha(self):
        original = self.head(shadows=False, dtype='f1')
        no_maps = self.head(shadows=True, dtype='f1')
        np.testing.assert_array_equal(no_maps, original)
        blocked = self.head(shadows=True, blocked=range(4))
        original_float = self.head(shadows=False)
        np.testing.assert_array_equal(blocked[..., 3], original_float[..., 3])
        np.testing.assert_allclose(blocked[..., :3], np.broadcast_to(np.array((.6, .25, .1))*.34, (24, 24, 3)), atol=2e-8, rtol=0)
        self.assertTrue(np.all(original_float[..., :3] > blocked[..., :3]))

    def test_separate_white_light_and_attached_shadow_preserve_paint_emission_and_reflections(self):
        ambient = self.head(shadows=True, blocked=range(4))
        one_white = self.head(shadows=True, blocked=(1, 2, 3))
        three_white = self.head(shadows=True, blocked=(0,))
        self.assertGreater(float(one_white[..., :3].sum()), float(ambient[..., :3].sum()))
        self.assertGreater(float(three_white[..., :3].sum()), float(one_white[..., :3].sum()))
        painted = self.head(shadows=True, blocked=range(5), attached=True, paint=True)
        expected = np.array((.15, .35, .55))*.34 + np.array((.2, .4, .6))*.35
        np.testing.assert_allclose(painted[..., :3], np.broadcast_to(expected, (24, 24, 3)), atol=5e-8, rtol=0)
        lit = self.head(shadows=True, blocked=range(4), attached=True, paint=True)
        self.assertGreater(float(lit[..., :3].sum()), float(painted[..., :3].sum()))
        reflected = self.head(shadows=True, blocked=range(5), attached=True, paint=True, environment=True)
        self.assertGreater(float(np.abs(reflected[..., :3]-painted[..., :3]).sum()), 0)
        np.testing.assert_array_equal(reflected[..., 3], painted[..., 3])

    def test_no_direct_light_skips_lookup_while_paint_emission_remain(self):
        # Expose the lookup result so sampledNaN contaminates0*NaN. Such maps
        # cannot be published; this diagnostic includes a negative control.
        painted = self.head(shadows=True, blocked=range(5), attached=True, paint=True,
            normal_sign=-1, map_depth=float('nan'), expose_lookup=True)
        expected = np.array((.15, .35, .55))*.34 + np.array((.2, .4, .6))*.35
        np.testing.assert_allclose(painted[..., :3], np.broadcast_to(expected, (24, 24, 3)), atol=5e-8, rtol=0)
        self.assertTrue(np.isfinite(painted).all())
        broken = self.head(shadows=True, blocked=range(5), attached=True, paint=True,
            normal_sign=-1, map_depth=float('nan'), expose_lookup=True, force_lookup=True)
        self.assertFalse(np.isfinite(broken[..., :3]).all())

    def test_projected_cube_depth_uses_dominant_axis_all_faces_and_empty_texels(self):
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);}'
        fragment = '#version 410\n'+visibility_source(8, False)+'''
uniform vec3 receiver; out vec4 outputColour;
void main(){outputColour=vec4(shadowVisibility(0,vec3(0),receiver));}
'''
        shader = self.context.program(vertex_shader=vertex, fragment_shader=fragment); self.addCleanup(shader.release)
        vao = self.context.vertex_array(shader, []); self.addCleanup(vao.release)
        target = self.target()
        near, far, blocker = .1, 20., 3.
        depth = far/(far-near)-far*near/((far-near)*blocker)
        self.maps(shader, 8, enabled=(0,), depth=depth, near=near, far=far)
        for axis in range(3):
            for sign in (-1, 1):
                for distance, expected in ((2.9, 1.), (3.1, 0.)):
                    # Radial distance exceeds blocker even for visible2.9:
                    # cubemap depth is dominant-axis forward, not radius.
                    receiver = [distance*.8]*3; receiver[axis] = sign*distance
                    shader['receiver'].value = receiver; vao.render(vertices=3)
                    pixel = np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4')
                    np.testing.assert_array_equal(pixel, expected)
        self.maps(shader, 8, enabled=(0,), depth=1., near=near, far=far)
        shader['receiver'].value = (10, 10, 10); vao.render(vertices=3)
        np.testing.assert_array_equal(np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4'), 1.)
        self.assertEqual(self.context.error, 'GL_NO_ERROR')

    def test_wide_projection_actual_caster_depth_does_not_self_shadow(self):
        vertex = '''#version 410
uniform mat4 casterProjection; uniform float distanceToCaster;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);
vec4 clip=casterProjection*vec4(0,0,-distanceToCaster,1);
gl_Position=vec4((p*2.-1.)*clip.w,clip.z,clip.w);}
'''
        caster = self.context.program(vertex_shader=vertex, fragment_shader='#version 410\nvoid main(){}')
        self.addCleanup(caster.release)
        vao = self.context.vertex_array(caster, []); self.addCleanup(vao.release)
        depth = self.context.depth_texture((4, 4)); self.addCleanup(depth.release)
        framebuffer = self.context.framebuffer(depth_attachment=depth); self.addCleanup(framebuffer.release)
        projection = ShadowProjection((0, 0, 0), .01, 1000)
        a, b = projection.depth_coefficients
        matrix = np.zeros((4, 4), dtype='f4')
        matrix[0, 0] = matrix[1, 1] = 1
        matrix[2, 2] = -a; matrix[3, 2] = b; matrix[2, 3] = -1
        caster['casterProjection'].write(matrix.tobytes())
        receiver = self.context.program(vertex_shader='#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);}',
            fragment_shader='#version 410\n'+visibility_source(8, False)+'''
uniform vec3 receiver;out vec4 outputColour;
void main(){outputColour=vec4(shadowVisibility(0,vec3(0),receiver));}
''')
        self.addCleanup(receiver.release)
        receiver_vao = self.context.vertex_array(receiver, []); self.addCleanup(receiver_vao.release)
        target = self.target()
        for distance in (200., 400.):
            framebuffer.use(); framebuffer.clear(depth=1.)
            self.context.enable(moderngl.DEPTH_TEST)
            self.context.depth_func = '<'; framebuffer.depth_mask = True
            caster['distanceToCaster'].value = distance; vao.render(vertices=3)
            stored = np.frombuffer(depth.read(), dtype='f4')[0]
            # Independent old-inverse negative control exposes the observed
            # false self-shadow from real GPU-written depth, not ideal doubles.
            n, f = np.float32(.01), np.float32(1000)
            reconstructed = n*f/(f-stored*(f-n))
            self.assertLess(float(reconstructed), distance-.1)
            target.use(); self.context.disable(moderngl.DEPTH_TEST)
            error = float(np.spacing(stored))
            # Explicit diagnostic allowance, not a production admission. This
            # wide projection must refuse a .01mm physical-precision demand.
            with self.assertRaisesRegex(ValueError, 'physical precision'):
                projection.certify_precision(distance, error, .01, measurement_error=error)
            self.maps(receiver, 8, enabled=(0,), depth=stored, near=.01, far=1000, depth_error=error)
            for offset, expected in ((0., 1.), (20., 0.)):
                receiver['receiver'].value = (distance+offset, 0, 0)
                receiver_vao.render(vertices=3)
                np.testing.assert_array_equal(np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4'), expected)
        self.assertEqual(self.context.error, 'GL_NO_ERROR')

    def test_scene_shadow_uses_raster_position_preserves_centreline_lighting_and_depth_range(self):
        resource = sources('scene-lighting.shader')
        # Original lighting centreline remains fixed at the light's left.
        # Actual raster world x spans both sides of a depth-cube blocker.
        vertex = '''#version 410
out vec3 f_vertex; out vec3 f_normal; out vec4 f_color;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,.5,1);
f_vertex=vec3(-.8,0,0);f_normal=vec3(0,0,1);f_color=vec4(.6,.25,.1,.35);}
'''
        shader = self.context.program(vertex_shader=vertex,
            fragment_shader=scene_fragment(resource['fragment41core'], enabled=True))
        self.addCleanup(shader.release)
        vao = self.context.vertex_array(shader, []); self.addCleanup(vao.release)
        target = self.target()
        near, far = .01, 30.
        # Negative-Z cube: x<0 maps to its right half; x>0 to left.
        texture_depth = np.ones((4, 4), dtype='f4')
        texture_depth[:, :2] = far/(far-near)-far*near/((far-near)*1.)
        self.maps(shader, 8, enabled=(0,), depth=texture_depth, near=near, far=far)
        values = dict(u_depthOnly=0, u_lightOpacity=1., u_attachedCount=1,
            u_attachedPosition=[(0, 0, 2)]*8, u_attachedDirection=[(0, 0, -1)]*8,
            u_attachedColour=[(.2, .4, .6)]*8, u_attachedRange=[20.]*8,
            u_orthographic=1, u_viewDirection=(0, 0, 1),
            u_shadowInverseViewProjection=np.eye(4, dtype='f4'),
            u_shadowViewport=(0, 0, 24, 24), u_shadowDepthRange=(0., 1.))
        uniforms(shader, values); vao.render(vertices=3)
        first = np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4').reshape(24, 24, 4).copy()
        self.assertTrue(np.all(first[:, :10, :3] > 0))
        np.testing.assert_array_equal(first[:, 14:, :3], 0)
        np.testing.assert_array_equal(first[..., 3], np.float32(.35))
        # Expose reconstructed world position rather than a map split that is
        # insensitive to Z. Nonzero clipZ and an asymmetric binary range make
        # ignored range delivery observable without an arbitrary tolerance.
        diagnostic = scene_fragment(resource['fragment41core'], enabled=True)
        diagnostic = diagnostic.replace(
            'vec4(lightSurface(f_vertex, f_normal, f_color.rgb, shadowReceiverPosition(gl_FragCoord.xy, gl_FragCoord.z)), f_color.a)',
            'vec4(shadowReceiverPosition(gl_FragCoord.xy, gl_FragCoord.z), f_color.a)')
        position_shader = self.context.program(vertex_shader=vertex, fragment_shader=diagnostic)
        self.addCleanup(position_shader.release)
        position_vao = self.context.vertex_array(position_shader, []); self.addCleanup(position_vao.release)
        uniforms(position_shader, values); position_vao.render(vertices=3)
        before = np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4').reshape(24, 24, 4).copy()
        np.testing.assert_array_equal(before[..., 2], .5)
        depth_range = ctypes.CFUNCTYPE(None, ctypes.c_double, ctypes.c_double)(self.context.mglo._context.load('glDepthRange'))
        depth_range(.125, .625)
        position_shader['u_shadowDepthRange'].value = (.125, .625); position_vao.render(vertices=3)
        second = np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4').reshape(24, 24, 4)
        np.testing.assert_array_equal(before, second)
        position_shader['u_shadowDepthRange'].value = (0., 1.); position_vao.render(vertices=3)
        broken = np.frombuffer(target.read(components=4, dtype='f4'), dtype='f4').reshape(24, 24, 4)
        np.testing.assert_array_equal(broken[..., 2], 0.)
        depth_range(0., 1.)
        self.assertEqual(self.context.error, 'GL_NO_ERROR')
