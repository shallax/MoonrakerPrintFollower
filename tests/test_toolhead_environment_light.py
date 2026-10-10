"""Native additive source parity; final two-pass storage is separate."""
import configparser
import re
import unittest

import numpy as np

from tests import test_toolhead_environment_shade as shade_tests


@unittest.skipIf(shade_tests.query_tests.moderngl is None, 'The capture OpenGL runtime is required')
class EnvironmentLightTests(unittest.TestCase):
    fixture = shade_tests.EnvironmentShadeTests.fixture
    render = shade_tests.EnvironmentShadeTests.render

    @staticmethod
    def extra_source():
        return (shade_tests.ROOT / 'environment-path-light.glsl').read_text()

    @staticmethod
    def main_source():
        return '''
uniform vec3 hitNormal,hitCentre;uniform int hitLine,hitStart,hitFront,outputMode;out vec4 colour;
void main(){complete=true;MPFPathHit hit=MPFPathHit(1.,hitLine,hitNormal,hitCentre,hitStart==1,hitFront==1);
 colour=outputMode==1?mpf_path_colour(hit):mpf_path_light_hit(hit);
 if(outputMode==2)colour=vec4(complete?1.:0.);
}
'''

    @staticmethod
    def extra_uniforms():
        positions = np.zeros((8, 3), np.float32); positions[0] = (0, 2, 0)
        directions = np.tile(np.array((0, -1, 0), np.float32), (8, 1))
        colours = np.zeros((8, 3), np.float32); colours[0] = (.3, .5, .8)
        return dict(mpf_pathProbe=(0, 2, 0), mpf_pathLightModels=1, mpf_pathLightCertified=1,
                    mpf_pathLightOrthographic=0, mpf_pathLightViewDirection=(0, 0, 1),
                    mpf_pathLightTop=0, mpf_pathAttachedCount=1, mpf_pathLightOpacity=.6,
                    mpf_path_showHelpers=1, mpf_pathAttachedPosition=positions,
                    mpf_pathAttachedDirection=directions, mpf_pathAttachedColour=colours,
                    mpf_pathAttachedRange=np.full(8, 10, np.float32))

    @classmethod
    def setUpClass(cls):
        shade_tests.EnvironmentShadeTests.setUpClass.__func__(cls)
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(shade_tests.ROOT / 'scene-lighting.shader')
        fragment = parser['shaders']['fragment41core']
        for kind, name in (('vec3', 'f_vertex'), ('vec3', 'f_normal'), ('vec4', 'f_color')):
            fragment = fragment.replace('in '+kind+' '+name+';', 'uniform '+kind+' '+name+';')
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        cls.reference = cls.context.program(vertex_shader=vertex, fragment_shader=fragment)
        cls.reference_vao = cls.context.vertex_array(cls.reference, [])

    @classmethod
    def tearDownClass(cls):
        cls.reference_vao.release(); cls.reference.release()
        shade_tests.EnvironmentShadeTests.tearDownClass.__func__(cls)

    def native(self, colour=(.2, .4, 2, .25), normal=(0, 1, 0), centre=(0, 0, 0), **override):
        values = self.extra_uniforms(); values.update(override)
        p = self.reference
        for key, value in {'f_color': colour, 'f_normal': normal, 'f_vertex': centre,
                           'u_depthOnly': 0, 'u_orthographic': 0, 'u_viewPosition': values['mpf_pathProbe'],
                           'u_attachedCount': values['mpf_pathAttachedCount'], 'u_lightOpacity': values['mpf_pathLightOpacity']}.items():
            p[key].value = value
        for name in ('Position', 'Direction', 'Colour', 'Range'):
            p['u_attached'+name].write(values['mpf_pathAttached'+name].tobytes())
        self.target.use()
        self.reference_vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4, dtype='f4'), np.float32).copy()

    def test_owned_light_function_expressions_are_preserved_verbatim(self):
        parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        parser.read(shade_tests.ROOT / 'scene-lighting.shader')
        source = parser['shaders']['fragment41core']
        start = source.index('vec3 lightSurface(')
        end = source.index('\nvoid main()', start)
        expected = source[start:end]
        for old, new in (('lightSurface', 'mpf_path_light_surface'), ('u_attached', 'mpf_pathAttached'),
                         ('u_lightOpacity', 'mpf_pathLightOpacity'), ('u_viewPosition', 'mpf_pathProbe'),
                         ('u_orthographic', 'mpf_pathLightOrthographic'), ('u_viewDirection', 'mpf_pathLightViewDirection')):
            expected = expected.replace(old, new)
        actual = self.extra_source()
        actual = actual[actual.index('vec3 mpf_path_light_surface('):actual.index('bool mpf_path_light_safe(')]
        self.assertEqual(re.sub(r'\s+', '', actual), re.sub(r'\s+', '', expected))

    def test_raw_additive_matches_unmodified_owned_native_fragment(self):
        cases = ({}, dict(hitNormal=(0, .7, -.3), hitCentre=(1, .3, -.4)),
                 dict(mpf_pathProbe=(3, 4, 2)), dict(mpf_pathAttachedCount=8))
        for case in cases:
            native_args = {key: value for key, value in case.items() if key.startswith('mpf_')}
            native_args['normal'] = case.get('hitNormal', (0, 1, 0))
            native_args['centre'] = case.get('hitCentre', (0, 0, 0))
            with self.subTest(case=case):
                np.testing.assert_array_equal(self.render(**case), self.native(**native_args))
        self.assertGreater(self.render()[2], 1)  # Preserve HDR before fixed-point source clamp.

    def test_history_source_alpha_and_starts_keep_their_native_palette(self):
        np.testing.assert_array_equal(self.render(mpf_path_historyEnd=1), self.native(colour=(.4, .4, .4, .9)))
        np.testing.assert_array_equal(self.render(hitStart=1), self.native(colour=(.1, .2, .3, .4)))
        np.testing.assert_array_equal(self.render(mpf_pathView=0), np.zeros(4, np.float32))  # Source-alpha discard.

    def test_culling_top_category_and_history_helper_eligibility_are_independent(self):
        self.assertTrue(np.any(self.render(mpf_pathLightTop=1)[:3] > 0))  # Older kind1 outer walls receive light.
        for case in (dict(hitFront=0), dict(mpf_pathLightModels=0), dict(mpf_pathAttachedCount=0),
                     dict(mpf_pathLightTop=1, attributes={('a_line_type', 0): 6}),
                     dict(mpf_path_historyEnd=1, mpf_path_showHelpers=0, attributes={('a_line_type', 0): 11})):
            with self.subTest(case=case):
                np.testing.assert_array_equal(self.render(**case), np.zeros(4, np.float32))
                self.assertEqual(self.render(outputMode=2, **case)[0], 1)

    def test_whole_cohort_certificate_precedes_per_hit_eligibility(self):
        for case in (dict(mpf_pathLightCertified=0), dict(mpf_pathLightCertified=0, hitFront=0),
                     dict(mpf_pathLightCertified=0, mpf_pathLightTop=1, attributes={('a_line_type', 0): 6})):
            self.assertEqual(self.render(outputMode=2, **case)[0], 0)
        self.assertEqual(self.render(mpf_pathLightCertified=0, mpf_pathLightModels=0, outputMode=2)[0], 1)

    def test_native_two_pass_storage_clamps_sources_and_accumulates_tied_paths(self):
        # Two original paths at exactly the same raster depth: base LESS picks
        # only the first, while additive LEQUAL admits BOTH. This is an actual
        # normalized framebuffer oracle, not a float base+light approximation.
        gl = shade_tests.query_tests.moderngl
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        program = self.context.program(vertex_shader=vertex,
            fragment_shader='#version 410\nuniform vec4 source;out vec4 colour;void main(){colour=source;}')
        vao = self.context.vertex_array(program, [])
        target = self.context.simple_framebuffer((1, 1), components=4, dtype='f1')
        try:
            target.use(); target.clear(0, 0, 0, 0, depth=1)
            self.context.disable(gl.BLEND | gl.CULL_FACE)
            self.context.enable(gl.DEPTH_TEST); self.context.depth_func = '<'
            target.depth_mask = True
            program['source'].value = (20/255, 40/255, 60/255, 64/255); vao.render(vertices=3)
            program['source'].value = (.9, .9, .9, .9); vao.render(vertices=3)
            self.assertEqual(tuple(target.read(components=4)), (20, 40, 60, 64))
            target.depth_mask = False; self.context.depth_func = '<='
            self.context.enable(gl.BLEND)
            self.context.blend_equation = gl.FUNC_ADD
            self.context.blend_func = (gl.SRC_ALPHA, gl.ONE, gl.ZERO, gl.ONE)
            program['source'].value = (4, 0, 1, .4); vao.render(vertices=3)
            self.assertEqual(tuple(target.read(components=4)), (122, 40, 162, 64))
            program['source'].value = (0, 4, 0, .2); vao.render(vertices=3)
            result = tuple(target.read(components=4))
            self.assertEqual(result, (122, 91, 162, 64))
            # HDR source clamping happens before alpha scaling; using a float
            # sum or only the nearest path would change these visible colours.
            self.assertNotEqual(result[0], 255)
            self.assertNotEqual(result[1], 40)
        finally:
            self.context.disable(gl.BLEND | gl.CULL_FACE | gl.DEPTH_TEST)
            self.context.depth_func = '<'
            target.depth_mask = True
            target.release(); vao.release(); program.release()

    def test_invalid_light_inputs_normalizations_and_kind_refuse_then_recover(self):
        positions = np.zeros((8, 3), np.float32); positions[0] = (float('nan'), 2, 0)
        directions = np.zeros((8, 3), np.float32)
        colours = np.zeros((8, 3), np.float32); colours[0] = (float('inf'), 1, 1)
        halfway = self.extra_uniforms()['mpf_pathAttachedPosition']; halfway[0] = (0, -2, 0)
        away = self.extra_uniforms()['mpf_pathAttachedDirection']; away[0] = (0, 1, 0)
        cases = (dict(mpf_pathLightModels=2), dict(mpf_pathAttachedCount=9), dict(mpf_pathLightTop=3),
                 dict(mpf_pathLightOpacity=float('nan')), dict(mpf_pathAttachedPosition=positions),
                 dict(mpf_pathAttachedDirection=directions), dict(mpf_pathAttachedColour=colours),
                 dict(mpf_pathAttachedRange=np.full(8, -1, np.float32)), dict(mpf_pathProbe=(0, 0, 0)),
                 dict(mpf_pathTypeStride=2), dict(attributes={('a_line_type', 0): 14}),
                 dict(mpf_pathAttachedPosition=halfway, mpf_pathAttachedDirection=away),
                 dict(hitNormal=(0, 0, 0)))
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.render(outputMode=2, **case)[0], 0)
        self.assertEqual(self.render(outputMode=2)[0], 1)


@unittest.skipIf(shade_tests.query_tests.moderngl is None, 'The capture OpenGL runtime is required')
class EnvironmentCompositionTests(unittest.TestCase):
    fixture = EnvironmentLightTests.fixture
    render = EnvironmentLightTests.render
    native = EnvironmentLightTests.native

    @staticmethod
    def extra_source():
        return EnvironmentLightTests.extra_source() + (shade_tests.ROOT / 'environment-path-compose.glsl').read_text()

    @staticmethod
    def main_source():
        return '\nuniform vec3 hitNormal,hitCentre;uniform int hitLine,hitStart,hitFront,outputMode,fakeCount;out vec4 colour;\nvoid main(){complete=true;MPFPathHit hit=MPFPathHit(1.,hitLine,hitNormal,hitCentre,hitStart==1,hitFront==1);\n mpf_pathLightHitCount=fakeCount;\n for(int i=0;i<fakeCount&&i<8;++i)mpf_pathLightHits[i]=MPFPathHit(1.,hitLine+i,hitNormal,hitCentre,hitStart==1,hitFront==1);\n colour=outputMode==1?mpf_path_colour(hit):mpf_path_light_hit(hit);\n if(outputMode==3)colour=mpf_path_shade_hit(hit);\n if(outputMode==4)colour=mpf_path_resolved_hit(hit);\n if(outputMode==2)colour=vec4(complete?1.:0.);\n}\n'

    @staticmethod
    def extra_uniforms():
        return dict(EnvironmentLightTests.extra_uniforms(), fakeCount=1, mpf_pathCollectLight=1)

    @classmethod
    def setUpClass(cls):
        EnvironmentLightTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        EnvironmentLightTests.tearDownClass.__func__(cls)

    def test_resolved_hit_matches_actual_normalized_base_then_owned_additive_pass(self):
        cases = ({}, dict(mpf_path_historyEnd=2), dict(hitStart=1),
                 dict(fakeCount=2), dict(hitNormal=(0, .7, -.3), hitCentre=(1, .3, -.4)),
                 dict(mpf_pathLightOpacity=.003), dict(mpf_pathAttachedCount=0))
        gl = shade_tests.query_tests.moderngl
        target = self.context.simple_framebuffer((1, 1), components=4, dtype='f1')
        try:
            for case in cases:
                palettes = [self.render(outputMode=1, **dict(case, hitLine=line))
                            for line in range(case.get('fakeCount', 1))]
                original = self.target
                self.target = target
                try:
                    # Store actual production raw base into a real RGBA8 target.
                    self.render(outputMode=3, **case)
                    self.context.enable(gl.BLEND)
                    self.context.blend_equation = gl.FUNC_ADD
                    self.context.blend_func = (gl.SRC_ALPHA, gl.ONE, gl.ZERO, gl.ONE)
                    native_args = {key: value for key, value in case.items() if key.startswith('mpf_')}
                    native_args['normal'] = case.get('hitNormal', (0, 1, 0))
                    native_args['centre'] = case.get('hitCentre', (0, 0, 0))
                    for palette in palettes:
                        self.native(colour=tuple(palette), **native_args)
                    native = np.frombuffer(target.read(components=4, dtype='f1'), np.uint8).astype(np.int16)
                finally:
                    self.target = original
                    self.context.disable(gl.BLEND)
                actual = np.rint(self.render(outputMode=4, **case)*255).astype(np.int16)
                with self.subTest(case=case):
                    self.assertLessEqual(int(np.abs(actual-native).max()), 1)
                    self.assertEqual(actual[3], native[3])
        finally:
            target.release()

    def test_resolution_requires_complete_ordered_light_collection(self):
        for case in (dict(mpf_pathCollectLight=0), dict(fakeCount=0), dict(fakeCount=9),
                     dict(fakeCount=2, hitLine=1)):
            with self.subTest(case=case):
                np.testing.assert_array_equal(self.render(outputMode=4, **case), np.zeros(4, np.float32))
        expected = self.render(outputMode=3)
        np.testing.assert_array_equal(self.render(outputMode=4, fakeCount=0, mpf_pathLightModels=0),
                                      np.rint(np.clip(expected, 0, 1)*255)/255)



if __name__ == '__main__':
    unittest.main()
