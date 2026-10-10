"""Native plate-distance logic, bounded work and unambiguous path blockers."""
from pathlib import Path
import unittest

import numpy as np

from tools.capture_toolhead import create_context
from tests import test_toolhead_environment_plates as plates_support

try:
    import moderngl
except ImportError:
    moderngl = None

RESOURCE = Path(__file__).resolve().parents[1]/'mpf/toolhead/environment-plate-query.glsl'


@unittest.skipIf(moderngl is None, 'Capture OpenGL runtime required')
class EnvironmentPlateQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        source = RESOURCE.read_text()
        replacements = (('samplerBuffer mpf_plateNodes', 'vec4 rawNodes[64]'),
                        ('samplerBuffer mpf_plateVertices', 'float rawVertices[384]'),
                        ('samplerBuffer mpf_plateModels', 'vec4 rawModels[32]'),
                        ('usamplerBuffer mpf_plateTriangles', 'uvec4 rawTriangles[64]'))
        for old, new in replacements: source = source.replace('uniform '+old+';', 'uniform '+new+';')
        import re
        for buffer, array in (('Nodes', 'rawNodes'), ('Vertices', 'rawVertices'), ('Models', 'rawModels'), ('Triangles', 'rawTriangles')):
            source = source.replace('textureSize(mpf_plate'+buffer+')', 'raw'+buffer+'Count')
            source = re.sub(r'texelFetch\(mpf_plate'+buffer+r',([^\)]+)\)', array+r'[\1]', source)
        # R32F array transport is scalar, rather than the real sampler's vec4.
        source = source.replace('rawVertices[index].r', 'rawVertices[index]').replace('rawVertices[index+1].r', 'rawVertices[index+1]').replace('rawVertices[index+2].r', 'rawVertices[index+2]')
        header = '''#version 410
bool complete;bool mpf_path_finite(vec3 v){return !any(isnan(v))&&!any(isinf(v));}
uniform int rawNodesCount,rawVerticesCount,rawModelsCount,rawTrianglesCount;
'''
        main = '''
uniform vec3 origin,direction;uniform float first,last;uniform int mode;out vec4 colour;
void main(){complete=true;bool found;float distance=mpf_trace_plates(origin,direction,first,last,found);
 colour=vec4(distance,found?1.:0.,complete?1.:0.,float(mpf_plateTriangleWork));
 if(mode==1)colour=vec4(mpf_plate_point(0u,0u),complete?1.:0.);
}
'''
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        cls.program = cls.context.program(vertex_shader=vertex, fragment_shader=header+source+main)
        cls.vao = cls.context.vertex_array(cls.program, [])
        cls.target = cls.context.simple_framebuffer((1, 1), components=4, dtype='f4')

    @classmethod
    def tearDownClass(cls):
        cls.target.release(); cls.vao.release(); cls.program.release(); cls.context.release()
        if cls.dll_directory is not None: cls.dll_directory.close()

    def fixture(self, z=0, model=None):
        support = plates_support.EnvironmentPlatesTests(); support.setUp(); self.addCleanup(support.doCleanups)
        vertices = np.array(((-1, -1, z), (1, -1, z), (1, 1, z), (-1, 1, z)), np.float32)
        mesh = support.mesh()
        from dataclasses import replace
        mesh = replace(mesh, vertices=vertices, indices=np.array(((0, 1, 2), (0, 2, 3)), np.uint32), colours=None)
        return support.build((support.entry(mesh, model),))

    def render(self, source=None, **overrides):
        source = self.fixture() if source is None else source
        nodes = np.zeros((64, 4), np.float32); nodes[:len(source.nodes)*2] = source.nodes.reshape(-1, 4)
        vertices = np.zeros(384, np.float32); vertices[:source.positions.size] = source.positions.reshape(-1)
        models = np.zeros((32, 4), np.float32); models[:source.models.size//4] = source.models.reshape(-1, 4)
        triangles = np.zeros((64, 4), np.uint32); triangles[:len(source.triangles)] = source.triangles
        values = dict(rawNodes=nodes, rawVertices=vertices, rawModels=models, rawTriangles=triangles,
            rawNodesCount=len(source.nodes)*2, rawVerticesCount=source.positions.size,
            rawModelsCount=source.models.size//4, rawTrianglesCount=len(source.triangles),
            mpf_plateNodeCount=len(source.nodes), mpf_plateVertexCount=len(source.positions),
            mpf_plateTriangleCount=len(source.triangles), mpf_plateAssetCount=len(source.models),
            origin=(0, 0, -1), direction=(0, 0, 1), first=.00001, last=10., mode=0)
        values.update(overrides)
        for name, value in values.items():
            if type(value) is np.ndarray: self.program[name].write(value.tobytes())
            else: self.program[name].value = value
        self.target.use(); self.context.disable(moderngl.BLEND | moderngl.CULL_FACE | moderngl.DEPTH_TEST)
        self.vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4, dtype='f4'), np.float32).copy()

    def test_actual_two_sided_original_triangles_block_before_equal_and_after_path_distance(self):
        result = self.render()
        self.assertEqual(tuple(result[:3]), (1, 1, 1))
        self.assertEqual(tuple(self.render(last=1)[:3]), (1, 1, 1))  # Native plates precede equal paths.
        self.assertEqual(tuple(self.render(last=.5)[:3]), (0, 0, 1))
        self.assertEqual(tuple(self.render(origin=(0, 0, 1), direction=(0, 0, -1))[:3]), (1, 1, 1))
        self.assertEqual(tuple(self.render(origin=(2, 0, -1))[:3]), (0, 0, 1))
        self.assertEqual(tuple(self.render(origin=(0, 0, -1), direction=(1, 0, 0))[:3]), (0, 0, 1))

    def test_asymmetric_native_matrix_upload_is_not_transposed_twice(self):
        model = np.array(((0, 2, 0, 10), (1, 0, 0, 20), (0, 0, -1, 30), (0, 0, 0, 1)), np.float32)
        source = self.fixture(model=model)
        self.assertEqual(tuple(self.render(source, origin=(10, 20, 29))[:3]), (1, 1, 1))
        np.testing.assert_array_equal(self.render(source, origin=(10, 20, 29), mode=1), (8, 19, 30, 1))

    def test_shared_edges_and_corner_points_keep_real_plate_presence(self):
        for point in ((0, 0), (1, 1), (-1, -1), (.5, .5), (-.5, -.5)):
            with self.subTest(point=point): self.assertEqual(tuple(self.render(origin=(*point, -1))[:3]), (1, 1, 1))
        self.assertEqual(tuple(self.render(direction=(0, 0, 1e-7), last=2e7)[:3]), (1e7, 1, 1))

    def test_invalid_counts_numeric_sources_records_intervals_and_work_refuse_whole_query(self):
        cases = (dict(mpf_plateNodeCount=-1), dict(mpf_plateNodeCount=2), dict(mpf_plateVertexCount=1000),
                 dict(mpf_plateTriangleCount=65), dict(mpf_plateAssetCount=9), dict(mpf_plateNodeCount=0),
                 dict(mpf_plateAssetCount=0), dict(origin=(float('nan'), 0, 0)), dict(direction=(0, 0, 0)),
                 dict(direction=(float('inf'), 0, 1)), dict(first=-1), dict(last=float('inf')),
                 dict(last=.00001))
        for case in cases:
            with self.subTest(case=case): self.assertEqual(tuple(self.render(**case)[:3]), (0, 0, 0))
        nodes = np.zeros((64, 4), np.float32)
        nodes[0], nodes[1] = (-1, -1, -.1, 0), (1, 1, .1, -2)
        for column, value in ((3, .5), (7, -9), (0, float('nan')), (4, -2), (7, 0)):
            bad = nodes.copy().reshape(-1); bad[column] = value
            self.assertEqual(tuple(self.render(rawNodes=bad.reshape(64, 4))[:3]), (0, 0, 0))
        for lane, value in ((0, 999), (3, 999)):
            record = np.zeros((64, 4), np.uint32); record[0] = (0, 1, 2, 0); record[0, lane] = value
            self.assertEqual(tuple(self.render(rawTriangles=record)[:3]), (0, 0, 0))
        bad = np.zeros(384, np.float32); bad[0] = float('nan')
        self.assertEqual(tuple(self.render(rawVertices=bad)[:3]), (0, 0, 0))


if __name__ == '__main__': unittest.main()
