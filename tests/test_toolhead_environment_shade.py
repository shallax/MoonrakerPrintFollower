"""Recovered native path appearance, independently of ray intersection error."""
from pathlib import Path
import unittest

import numpy as np

from tests import test_toolhead_environment_query as query_tests
from tools.capture_toolhead import create_context

ROOT = Path(__file__).resolve().parents[1] / 'mpf/toolhead'


@unittest.skipIf(query_tests.moderngl is None, 'The capture OpenGL runtime is required')
class EnvironmentShadeTests(unittest.TestCase):
    fixture = query_tests.EnvironmentSourceTests.fixture

    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        print('Environment shade renderer:', cls.context.info['GL_RENDERER'], flush=True)
        query = (ROOT / 'environment-path-query.glsl').read_text()
        query = query[:query.index('const int mpf_path_endpoint')]
        source = (ROOT / 'environment-path-source.glsl').read_text()
        source = source.replace('uniform samplerBuffer mpf_pathVertices;',
                                'uniform float rawVertices[192];uniform int rawVertexTexels;')
        source = source.replace('uniform usamplerBuffer mpf_pathIndices;',
                                'uniform uint rawIndices[16];uniform int rawIndexTexels;')
        source = source.replace('textureSize(mpf_pathVertices)', 'rawVertexTexels')
        source = source.replace('textureSize(mpf_pathIndices)', 'rawIndexTexels')
        source = source.replace('texelFetch(mpf_pathVertices,offset+vertex*stride+component).r',
                                'rawVertices[offset+vertex*stride+component]')
        source = source.replace('texelFetch(mpf_pathIndices,line*2).r', 'rawIndices[line*2]')
        source = source.replace('texelFetch(mpf_pathIndices,line*2+1).r', 'rawIndices[line*2+1]')
        shade = (ROOT / 'environment-path-shade.glsl').read_text()
        if hasattr(cls, 'extra_source'):
            shade += cls.extra_source()
        shade = shade.replace('textureSize(mpf_pathIndices)', 'rawIndexTexels')
        shade = shade.replace('texelFetch(mpf_pathIndices,hit.line*2+1).r', 'rawIndices[hit.line*2+1]')
        shade = shade.replace('texelFetch(mpf_pathIndices,hit.line*2).r', 'rawIndices[hit.line*2]')
        main = '''
uniform vec3 hitNormal,hitCentre;uniform int hitLine,hitStart,hitFront,outputMode;out vec4 colour;
void main(){complete=true;MPFPathHit hit=MPFPathHit(1.,hitLine,hitNormal,hitCentre,hitStart==1,hitFront==1);
 colour=outputMode==1?mpf_path_colour(hit):mpf_path_shade_hit(hit);
 if(outputMode==2)colour=vec4(complete?1.:0.);
}
'''
        if hasattr(cls, 'main_source'):
            main = cls.main_source()
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        cls.program = cls.context.program(vertex_shader=vertex, fragment_shader='#version 410\n'+query+source+shade+main)
        cls.vao = cls.context.vertex_array(cls.program, [])
        cls.target = cls.context.simple_framebuffer((1, 1), components=4, dtype='f4')

    @classmethod
    def tearDownClass(cls):
        cls.target.release(); cls.vao.release(); cls.program.release(); cls.context.release()
        if cls.dll_directory is not None:
            cls.dll_directory.close()

    def render(self, *, attributes=None, indices=None, **override):
        inputs, packed = self.fixture()
        fields = {name: (offset, width) for name, offset, width in inputs.fields}
        # Start and END palettes differ; the GS always emits the END palette.
        for name, colours in (('a_color', ((.9, .8, .7, 1), (.2, .4, 2, .25))),
                              ('a_material_color', ((.8, .7, .6, 1), (.6, .1, .3, 0)))):
            offset, width = fields[name]
            for vertex, colour in enumerate(colours):
                packed[offset+vertex*width:offset+(vertex+1)*width] = colour
        for (name, vertex), data in (attributes or {}).items():
            offset, width = fields[name]
            packed[offset+vertex*width:offset+(vertex+1)*width] = data
        raw = np.zeros(192, np.float32); raw[:len(packed)] = packed
        ix = np.zeros(16, np.uint32); ix[:4] = inputs.indices.reshape(-1) if indices is None else indices
        p = self.program
        p['rawVertices'].write(raw.tobytes()); p['rawIndices'].write(ix.tobytes())
        values = dict(rawVertexTexels=inputs.scalar_count, rawIndexTexels=4,
                      mpf_pathScalarCount=inputs.scalar_count, mpf_pathVertexCount=4,
                      mpf_path_sourceLineCount=2, mpf_path_first=0, mpf_path_completed=2, mpf_path_historyEnd=0,
                      mpf_pathView=1, mpf_pathMinimum=(0, 0, 0, 0), mpf_pathMaximum=(2, 1, 2, 2),
                      mpf_pathStartsColour=(.1, .2, .3, .4), mpf_pathCameraLight=(0, 2, 0),
                      mpf_pathMinimumAlbedo=(.1, .1, .1), hitNormal=(0, 1, 0), hitCentre=(0, 0, 0),
                      hitLine=0, hitStart=0, hitFront=1, outputMode=0)
        for label, name in (('Dimensions', 'a_line_dim'), ('Colour', 'a_color'),
                            ('MaterialColour', 'a_material_color'), ('Feedrate', 'a_feedrate'), ('Type', 'a_line_type')):
            offset, width = fields[name]
            suffix = '' if label in ('Dimensions', 'Type') else 'Offset'
            values['mpf_path'+label+suffix], values['mpf_path'+label+'Stride'] = offset, width
        if hasattr(self, 'extra_uniforms'):
            values.update(self.extra_uniforms())
        values.update(override)
        for name, value in values.items():
            if name in p:
                if type(value) is np.ndarray:
                    p[name].write(value.tobytes())
                else:
                    p[name].value = value
        gl = query_tests.moderngl
        self.target.use(); self.context.disable(gl.BLEND | gl.CULL_FACE | gl.DEPTH_TEST)
        self.vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4, dtype='f4'), np.float32).copy()

    def test_end_palette_hdr_and_zero_alpha_do_not_attenuate_base_rgb(self):
        np.testing.assert_array_equal(self.render(outputMode=1), np.array((.2, .4, 2, .25), np.float32))
        np.testing.assert_allclose(self.render(), (.34, .58, 2.5, .25), atol=1e-7, rtol=0)
        np.testing.assert_array_equal(self.render(mpf_pathView=0, outputMode=1), np.array((.6, .1, .3, 0), np.float32))
        np.testing.assert_allclose(self.render(mpf_pathView=0), (.82, .22, .46, 0), atol=1e-7, rtol=0)

    def test_history_and_actual_start_theme_have_distinct_native_ambient(self):
        np.testing.assert_allclose(self.render(mpf_path_historyEnd=1, hitStart=1, mpf_pathView=99),
                                   (.52, .52, .52, .9), atol=1e-7, rtol=0)
        np.testing.assert_allclose(self.render(hitStart=1), (.22, .34, .46, .4), atol=1e-7, rtol=0)

    def test_native_metric_colour_modes_and_degenerate_ranges(self):
        # Independent installed VS gradients at END: feedrate1, height.2,
        # width.8, flow.16. Flow's equal-range centre differs from other modes.
        expected = {2: (.5, .5, 0, 1), 3: (0, .3, .7, 1), 4: (.4, .5, 0, 1),
                    5: (0, 0, .82, 1)}
        for mode, colour in expected.items():
            with self.subTest(mode=mode):
                np.testing.assert_allclose(self.render(mpf_pathView=mode, outputMode=1), colour, atol=2e-7, rtol=0)
        for mode in (2, 3, 4, 5):
            colour = self.render(mpf_pathView=mode, mpf_pathMinimum=(1, 1, 1, 1),
                                 mpf_pathMaximum=(1, 1, 1, 1), outputMode=1)
            np.testing.assert_allclose(colour, (.5, 1, .5, 1) if mode == 5 else
                                       (0, .75, .5, 1) if mode == 3 else (.5, .5, 0, 1), atol=1e-7, rtol=0)

    def test_light_uses_hit_centre_and_normalized_interpolated_normal(self):
        normal = np.array((0, .7, -.3), np.float32)
        centre = np.array((3, .4, -2), np.float32)
        delta = np.array((0, 2, 0), np.float32)-centre
        diffuse = np.clip(np.dot(normal/np.linalg.norm(normal), delta/np.linalg.norm(delta)), 0, 1)
        colour = np.array((.2, .4, 2, .25), np.float32)
        expected = colour.copy(); expected[:3] = colour[:3]*np.float32(.2)+np.float32(.1)+diffuse*colour[:3]
        np.testing.assert_allclose(self.render(hitNormal=tuple(normal), hitCentre=tuple(centre)), expected, atol=2e-7, rtol=0)

    def test_invalid_palette_layout_original_index_and_nonfinite_shading_refuse(self):
        cases = (dict(hitLine=-1), dict(hitLine=2), dict(rawIndexTexels=3), dict(mpf_path_sourceLineCount=1),
                 dict(mpf_path_first=-1), dict(mpf_path_completed=3), dict(mpf_path_historyEnd=3),
                 dict(mpf_pathColourStride=3), dict(mpf_pathColourOffset=0x7fffffff), dict(mpf_pathView=6),
                 dict(mpf_pathView=2, mpf_pathFeedrateStride=2), dict(mpf_pathView=3, mpf_pathDimensionsStride=1),
                 dict(mpf_pathView=5, mpf_pathMaximum=(2, 1, 2, float('inf'))),
                 dict(mpf_pathView=2, mpf_pathMinimum=(float('nan'), 0, 0, 0)),
                 dict(hitNormal=(0, 0, 0)), dict(hitNormal=(float('nan'), 1, 0)),
                 dict(hitCentre=(0, 2, 0)), dict(hitCentre=(float('inf'), 0, 0)),
                 dict(hitStart=1, mpf_pathStartsColour=(1, 1, 1, float('nan'))))
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.render(outputMode=2, **case)[0], 0)
        for index in (4, 0xffffffff):
            self.assertEqual(self.render(indices=(0, index, 2, 3), outputMode=2)[0], 0)
        self.assertEqual(self.render(attributes={('a_color', 1): (float('nan'), 1, 1, 1)}, outputMode=2)[0], 0)
        self.assertEqual(self.render(outputMode=2)[0], 1)


if __name__ == '__main__':
    unittest.main()
