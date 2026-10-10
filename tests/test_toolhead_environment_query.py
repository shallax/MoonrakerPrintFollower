"""Owned native query logic on actual GL; source/buffer ownership is separate.

Uniform arrays substitute only sampler-buffer transport, so these small tests
can run on every capture backend. The native texture-buffer fixture separately
qualifies the unmodified resource and production prepared-tree delivery.
"""
from pathlib import Path
import unittest

import numpy as np

from mpf.toolhead.ToolheadEnvironmentPaths import prepare_path_source, admit_path_inputs
from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, BufferLease
from tools.capture_toolhead import create_context

try:
    import moderngl
except ImportError:
    moderngl = None

QUERY = Path(__file__).resolve().parents[1] / 'mpf/toolhead/environment-path-query.glsl'
SOURCE = QUERY.with_name('environment-path-source.glsl')


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class EnvironmentQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        print('Environment query renderer:', cls.context.info['GL_RENDERER'], flush=True)
        text = QUERY.read_text()
        text = text.replace('uniform samplerBuffer mpf_path_nodes;', 'uniform vec4 queryNodes[64];uniform int queryNodeTexels;')
        text = text.replace('uniform usamplerBuffer mpf_path_aliasLines;', 'uniform uint queryAliases[32];uniform int queryAliasTexels;')
        text = text.replace('texelFetch(mpf_path_nodes,index*2+1)', 'queryNodes[index*2+1]')
        text = text.replace('texelFetch(mpf_path_nodes,index*2)', 'queryNodes[index*2]')
        text = text.replace('textureSize(mpf_path_nodes)', 'queryNodeTexels')
        text = text.replace('textureSize(mpf_path_aliasLines)', 'queryAliasTexels')
        text = text.replace('texelFetch(mpf_path_aliasLines,i).r', 'queryAliases[i]')
        provider = '''
uniform vec3 pathA[16],pathB[16];uniform vec2 pathDimensions[16];
uniform float pathKind,pathPrevious,pathVisibility;
MPFPathLine mpf_source_line(int id,bool history){
return MPFPathLine(pathA[id],pathDimensions[id].x,pathB[id],pathDimensions[id].y,pathKind,pathPrevious,pathVisibility);
}
uniform vec3 queryOrigin,queryDirection;uniform int outputMode;
out vec4 colour;
void main(){
 MPFPathHit hit=mpf_trace_paths(queryOrigin,queryDirection,.00001,100.);
 colour=vec4(hit.distance,float(hit.line),complete?1.:0.,float(facetWork));
 if(outputMode==1)colour=vec4(hit.normal,hit.start?1.:0.);
 if(outputMode==2)colour=vec4(hit.centre,hit.start?1.:0.);
 if(outputMode==3)colour=vec4(hit.lightFront?1.:0.,hit.start?1.:0.,hit.distance,complete?1.:0.);
 if(outputMode==4){float ids=0.;for(int i=0;i<mpf_pathLightHitCount;++i)ids+=float(mpf_pathLightHits[i].line*(i+1));
  colour=vec4(float(mpf_pathLightHitCount),ids,complete?1.:0.,float(hit.line));}
 if(outputMode==5||outputMode==6){float distance;mpf_path_lineHit(0,queryOrigin,queryDirection,mpf_path_farDistance,distance);
  colour=vec4(outputMode==5?mpf_path_candidateLow:mpf_path_candidateHigh,complete?1.:0.);}
 if(outputMode==7)colour=vec4(mpf_path_box(queryOrigin,queryDirection,vec3(-1.,-1.,1.),vec3(1.,1.,2.),1.)?1.:0.);
}
'''
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        cls.program = cls.context.program(vertex_shader=vertex, fragment_shader='#version 410\n'+text+provider)
        cls.vao = cls.context.vertex_array(cls.program, [])
        cls.target = cls.context.simple_framebuffer((1, 1), components=4, dtype='f4')

    @classmethod
    def tearDownClass(cls):
        cls.target.release(); cls.vao.release(); cls.program.release(); cls.context.release()
        if cls.dll_directory is not None:
            cls.dll_directory.close()

    def fixture(self, count=1, group=1):
        vertices = np.tile(np.array(((-2, .2, 0), (2, .2, 0)), np.float32), (count, 1))
        dims = np.tile(np.array((.4, .1), np.float32), (count*2, 1))
        ix = np.arange(count*2, dtype=np.uint32).reshape(-1, 2)
        tree = prepare_path_source(vertices, dims, ix, np.eye(4, dtype=np.float32), 1, group=group)
        a = np.zeros((16, 3), np.float32); b = a.copy(); dimensions = np.zeros((16, 2), np.float32)
        if count:
            shifted = vertices.copy(); shifted[:, 1] -= dims[:, 1]*np.float32(.5)
            a[:count], b[:count], dimensions[:count] = shifted[ix[:, 0]], shifted[ix[:, 1]], dims[ix[:, 1]]
        nodes = np.zeros((64, 4), np.float32)
        nodes[:len(tree.nodes)*2] = tree.nodes.reshape(-1, 4)
        return tree, nodes, a, b, dimensions

    def render(self, fixture=None, *, mode=0, origin=(0, .17, -1), direction=(0, 0, 1), **override):
        tree, nodes, a, b, dims = self.fixture() if fixture is None else fixture
        p = self.program
        for name, data in (('queryNodes', nodes), ('pathA', a), ('pathB', b), ('pathDimensions', dims)):
            p[name].write(data.tobytes())
        p['queryAliases'].write(np.zeros(32, np.uint32).tobytes())
        values = dict(queryOrigin=origin, queryDirection=direction, mpf_pathProbe=origin, outputMode=mode,
                      queryNodeTexels=len(tree.nodes)*2, queryAliasTexels=32,
                      mpf_path_nodeCount=len(tree.nodes), mpf_path_sourceLineCount=tree.primitives,
                      mpf_path_first=0, mpf_path_completed=tree.primitives, mpf_path_historyEnd=0,
                      mpf_path_aliasCount=0, mpf_pathCollectLight=0, mpf_path_showTravel=1, mpf_path_showHelpers=1,
                      mpf_path_showSkin=1, mpf_path_showInfill=1, mpf_path_showStarts=0,
                      pathKind=1., pathPrevious=1., pathVisibility=1.)
        values.update(override)
        for name, value in values.items():
            p[name].value = value
        self.target.use(); self.context.disable(moderngl.BLEND | moderngl.CULL_FACE | moderngl.DEPTH_TEST)
        self.vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4, dtype='f4'), np.float32).copy()

    def test_light_ties_preserve_all_original_order_and_ignore_alias_replay(self):
        fixture = self.fixture(count=3)
        result = self.render(fixture, mode=4, mpf_pathCollectLight=1)
        self.assertEqual(tuple(result), (3, 8, 1, 0))  # 0*1+1*2+2*3, not BVH order.
        # Aliases are real original draws, but discovering that same ID through
        # both its source bounds and the alias list must not add a second light.
        result = self.render(fixture, mode=4, mpf_pathCollectLight=1, mpf_path_aliasCount=1)
        self.assertEqual(tuple(result), (3, 8, 1, 0))
        result = self.render(fixture, mode=4, mpf_pathCollectLight=0)
        self.assertEqual(tuple(result), (0, 0, 1, 0))
        result = self.render(self.fixture(count=8), mode=4, mpf_pathCollectLight=1, mpf_path_aliasCount=1)
        self.assertEqual(tuple(result), (8, 168, 1, 0))

    def test_light_ties_reset_for_nearer_surface_and_refuse_overflow(self):
        fixture = self.fixture(count=3, group=3)
        fixture[2][2, 2] = -.1; fixture[3][2, 2] = -.1
        result = self.render(fixture, mode=4, mpf_pathCollectLight=1)
        self.assertEqual(tuple(result), (1, 2, 1, 2))
        overflow = self.fixture(count=9)
        result = self.render(overflow, mode=4, mpf_pathCollectLight=1)
        self.assertEqual(tuple(result), (0, 0, 0, -2))
        # A nearest-only query is not made incomplete by an unused light budget.
        self.assertEqual(tuple(self.render(overflow)[1:3]), (0, 1))
        self.assertEqual(self.render(mpf_pathCollectLight=2)[2], 0)

    def test_original_front_facet_position_normal_and_centreline(self):
        # Independent original diamond plane: sy=.06, sx=.21, upper weight
        # (.17-.15)/sy. The original front-facing normal blends up and -Z.
        result = self.render()
        a = np.float32(.2)-np.float32(.1)*np.float32(.5)
        sy = np.float32(.1)/2+np.float32(.01)
        sx = np.float32(.4)/2+np.float32(.01)
        weight = (np.float32(.17)-a)/sy
        expected = 1-sx*(1-weight)
        self.assertEqual(tuple(result[1:3]), (0., 1.))
        self.assertAlmostEqual(float(result[0]), float(expected), places=6)
        np.testing.assert_allclose(self.render(mode=1)[:3], (0, weight, -(1-weight)), atol=2e-6, rtol=0)
        np.testing.assert_allclose(self.render(mode=2)[:3], (0, a, 0), atol=2e-6, rtol=0)
        miss = self.render(origin=(0, .3, -1))
        self.assertEqual(tuple(miss[1:3]), (-1., 1.))

    def test_equal_distance_cross_leaf_winner_uses_lowest_original_id(self):
        result = self.render(self.fixture(2))
        self.assertEqual(tuple(result[1:3]), (0., 1.))
        # Traversal visits right first on equal near distance. Pruning equality
        # would preserve line1; a complete original-ID tie must recover line0.
        self.assertEqual(self.render(self.fixture(2), mpf_path_first=1)[1], 1)

    def test_prefix_filters_history_and_start_marker_semantics(self):
        self.assertEqual(self.render(mpf_path_completed=0)[1], -1)
        self.assertEqual(self.render(mpf_path_showSkin=0)[1], -1)
        self.assertEqual(self.render(pathVisibility=0)[1], -1)
        self.assertEqual(self.render(pathKind=8, mpf_path_showTravel=0)[1], -1)
        start = self.render(mode=1, origin=(-2, .17, -1), pathPrevious=8, mpf_path_showStarts=1)
        self.assertEqual(start[3], 1)
        # Native presentation history suppresses start markers.
        history = self.render(mode=1, origin=(-2, .17, -1), pathPrevious=8,
                              mpf_path_showStarts=1, mpf_path_historyEnd=1)
        self.assertEqual(history[3], 0)

    def test_malformed_buffer_counts_metadata_and_cycles_refuse_whole_query(self):
        for override in (dict(queryNodeTexels=1), dict(mpf_path_nodeCount=100),
                         dict(mpf_path_nodeCount=0),
                         dict(mpf_path_aliasCount=33), dict(queryAliasTexels=0, mpf_path_aliasCount=1),
                         dict(mpf_path_sourceLineCount=0), dict(pathKind=float('nan'))):
            with self.subTest(override=override):
                result = self.render(**override)
                self.assertEqual(tuple(result[1:3]), (-2., 0.))
        for payload in ((.5, -1), (0, -40), (0, float('nan')), (100, 0), (0, 0)):
            fixture = self.fixture(); fixture[1][0, 3], fixture[1][1, 3] = payload
            with self.subTest(payload=payload):
                result = self.render(fixture)
                self.assertEqual(tuple(result[1:3]), (-2., 0.))
        # Fresh valid source after every refusal remains usable.
        self.assertEqual(tuple(self.render()[1:3]), (0., 1.))

    def test_facet_budget_never_publishes_provisional_winner(self):
        # Eight identical start-marked tubes repeated as32aliases create more
        # than512facet evaluations, even though an early valid hit exists.
        result = self.render(self.fixture(8, group=8), mpf_path_aliasCount=32,
                             pathPrevious=8, mpf_path_showStarts=1)
        self.assertEqual(tuple(result[1:3]), (-2., 0.))
        self.assertEqual(result[3], 513)

    def test_empty_original_source_is_a_complete_miss(self):
        result = self.render(self.fixture(0))
        self.assertEqual(tuple(result[1:3]), (-1., 1.))

    def test_original_tube_and_cap_winding_distinguishes_inside_exit(self):
        self.assertEqual(self.render(mode=3)[0], 1)
        self.assertEqual(self.render(mode=3, origin=(0, .17, 0))[0], 0)
        cap = self.render(mode=3, origin=(-3, .15, 0), direction=(1, 0, 0))
        self.assertEqual(tuple(cap[[0, 3]]), (1, 1))
        exit_cap = self.render(mode=3, origin=(0, .15, 0), direction=(-1, 0, 0))
        self.assertEqual(exit_cap[0], 0)

    def test_original_travel_and_start_strip_alternating_winding(self):
        # Samples lie on both halves of the strip, away from its diagonal.
        for x in (-1.5, -.5, .5, 1.5):
            with self.subTest(x=x):
                top = self.render(mode=3, origin=(x, .3, .012), direction=(0, -1, 0), pathKind=8)
                below = self.render(mode=3, origin=(x, .1, .012), direction=(0, 1, 0), pathKind=8)
                self.assertEqual(tuple(top[[0, 3]]), (1, 1))
                self.assertEqual(tuple(below[[0, 3]]), (0, 1))
        for x in (-2.08, -1.98):
            marker = self.render(mode=3, origin=(x, .18, 1), direction=(0, 0, -1),
                                 pathPrevious=8, mpf_path_showStarts=1)
            self.assertEqual(tuple(marker[[0, 1, 3]]), (1, 1, 1))

    def test_attached_culling_uses_probe_even_when_reflection_ray_is_inside(self):
        inside = dict(mode=3, origin=(0, .17, 0), direction=(0, 0, 1))
        self.assertEqual(self.render(**inside)[0], 0)
        self.assertEqual(self.render(**inside, mpf_pathProbe=(0, .17, 1))[0], 1)
        # An outside reflected hit may simultaneously be back-facing to the
        # capture eye. Smooth interpolated normals cannot substitute for this.
        self.assertEqual(self.render(mode=3, mpf_pathProbe=(0, .17, 1))[0], 0)
        self.assertEqual(tuple(self.render(mpf_pathProbe=(float('nan'), 0, 0))[1:3]), (-2., 0.))

    def test_componentwise_query_bounds_contain_every_original_rendered_corner(self):
        # The independently expanded original geometry shader corner sets,
        # including reverse-vertical travel's a-h+up and enabled start minima.
        rng = np.random.default_rng(5530)
        cases = [(np.array(a, np.float32), np.array(b, np.float32), (.4, .05)) for a, b in (
            ((-2, .2, 0), (2, .2, 0)), ((0, .2, -2), (0, .2, 2)),
            ((0, 0, 0), (0, 2, 0)), ((0, 2, 0), (0, 0, 0)),
            ((0, 0, 0), (2, 3, 4)), ((2, 3, 4), (0, 0, 0)))]
        for index in range(12):
            raw = rng.uniform(-100, 100, (2, 3)).astype(np.float32)
            model = rng.normal(size=(3, 3)).astype(np.float32)
            translation = rng.uniform(-1000, 1000, 3).astype(np.float32)
            moved = index % 2  # Global partial alias may change either endpoint.
            raw[moved] = raw[0]*np.float32(.63)+raw[1]*np.float32(.37)
            dimensions = rng.uniform(.03, 1, (2, 2)).astype(np.float32)
            raw[:, 1] -= dimensions[:, 1]*np.float32(.5)  # Native VS, before model.
            a, b = raw @ model.T+translation
            cases.append((a, b, dimensions[1]))  # Original GS takes endpoint-B dimensions.
        for a, b, dimensions in cases:
            delta = b-a
            radial = np.array((delta[2], 0, -delta[0]), np.float32) if delta[1] == 0 else (
                np.array((1, 0, -1), np.float32) if delta[0] == delta[2] == 0 else
                np.cross(delta, np.array((delta[0], 0, delta[2]), np.float32)))
            axial = delta/np.linalg.norm(delta); radial /= np.linalg.norm(radial)
            sy = np.float32(dimensions[1])*.5+np.float32(.01)
            for kind in (1, 4, 8, 9, 12, 13):
                travel = kind in (8, 9, 12, 13)
                sx = np.float32(.05) if travel else np.float32(dimensions[0])*.5+np.float32(.01)
                h, r, up = axial*sx, radial*sx, np.array((0, sy, 0), np.float32)
                for starts, history in ((0, 0), (1, 0), (1, 1)):
                    corners = ([a-h+up, a-r+up, a+r+up, b-r+up, b+r+up, b+h+up] if travel else
                        [point+offset for point in (a, b) for offset in (-h, h, -r, r, -up, up)])
                    if starts and not history and kind in (1, 4):
                        marker = np.array((max(.05, sx), max(.05, sy), max(.05, sx)), np.float32)
                        corners += [a+np.array((x, y, z), np.float32)*marker
                            for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
                    fixture = self.fixture(); fixture[2][0], fixture[3][0] = a, b
                    fixture[4][0] = dimensions
                    options = dict(pathKind=kind, pathPrevious=8, mpf_path_showStarts=starts,
                        mpf_path_historyEnd=history)
                    low = self.render(fixture, mode=5, **options)
                    high = self.render(fixture, mode=6, **options)
                    with self.subTest(a=a, b=b, kind=kind, starts=starts, history=history):
                        self.assertEqual(low[3], 1); self.assertEqual(high[3], 1)
                        self.assertTrue(np.all(np.asarray(corners) >= low[:3]))
                        self.assertTrue(np.all(np.asarray(corners) <= high[:3]))

    def test_thin_layers_skip_nonintersecting_native_facets_without_making_a_hole(self):
        fixture = self.fixture(count=8, group=8)
        fixture[4][:8, 1] = .05
        # Seven other .05mm layers share this leaf but cannot intersect the ray.
        fixture[2][1:8, 1] += np.arange(1, 8, dtype=np.float32)*.1
        fixture[3][1:8, 1] += np.arange(1, 8, dtype=np.float32)*.1
        positions = np.stack((fixture[2][:8], fixture[3][:8]), axis=1).reshape(-1, 3).copy()
        dimensions = np.repeat(fixture[4][:8], 2, axis=0)
        positions[:, 1] += dimensions[:, 1]*np.float32(.5)
        tree = prepare_path_source(positions, dimensions, np.arange(16, dtype=np.uint32).reshape(-1, 2),
            np.eye(4, dtype=np.float32), 1, group=8)
        fixture[1][:len(tree.nodes)*2] = tree.nodes.reshape(-1, 4)
        fixture = (tree, *fixture[1:])
        hit = self.render(fixture, origin=(0, .17, -1))
        self.assertEqual(tuple(hit[1:3]), (0, 1))
        self.assertEqual(hit[3], 16)  # Every original facet of the one surviving tube.
        miss = self.render(fixture, origin=(0, .2, -1))
        self.assertEqual(tuple(miss[1:3]), (-1, 1))
        self.assertEqual(miss[3], 0)

    def test_nearer_hit_skips_farther_tubes_within_the_same_admitted_leaf(self):
        fixture = self.fixture(count=8, group=8)
        # Root/leaf intersects all tubes; original source line0 is closest.
        fixture[2][:8, 2] = np.arange(8, dtype=np.float32)
        fixture[3][:8, 2] = np.arange(8, dtype=np.float32)
        positions = np.stack((fixture[2][:8], fixture[3][:8]), axis=1).reshape(-1, 3).copy()
        dimensions = np.repeat(fixture[4][:8], 2, axis=0)
        positions[:, 1] += dimensions[:, 1]*np.float32(.5)
        tree = prepare_path_source(positions, dimensions, np.arange(16, dtype=np.uint32).reshape(-1, 2),
            np.eye(4, dtype=np.float32), 1, group=8)
        fixture[1][:len(tree.nodes)*2] = tree.nodes.reshape(-1, 4)
        fixture = (tree, *fixture[1:])
        hit = self.render(fixture)
        self.assertEqual(tuple(hit[1:3]), (0, 1)); self.assertEqual(hit[3], 16)
        np.testing.assert_array_equal(hit[:3], self.render(self.fixture())[:3])
        # Exact co-depth neighbours remain eligible; their original order and
        # lower-ID winner are separately tested across leaves and aliases.
        fixture[2][1, 2] = fixture[3][1, 2] = 0
        ties = self.render(fixture, mode=4, mpf_pathCollectLight=1)
        self.assertEqual(tuple(ties), (2, 2, 1, 0))

    def test_pruning_box_keeps_exact_limit_equality_and_nearly_parallel_components(self):
        for origin, direction in (((0, 0, 0), (0, 0, 1)), ((0, 0, 3), (0, 0, -1)),
                                  ((0, 0, 0), (1e-7, 0, 1))):
            with self.subTest(origin=origin, direction=direction):
                self.assertEqual(self.render(mode=7, origin=origin, direction=direction)[0], 1)
        self.assertEqual(self.render(mode=7, origin=(0, 0, 0), direction=(0, 0, .99))[0], 0)


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class EnvironmentSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        query, source = QUERY.read_text(), SOURCE.read_text()
        query = query[:query.index('const int mpf_path_endpoint')]
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
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        main = '''
uniform int sourceLine,sourceHistory,outputMode;out vec4 colour;
void main(){complete=true;MPFPathLine line=mpf_source_line(sourceLine,sourceHistory==1);
 colour=vec4(line.a,complete?1.:0.);
 if(outputMode==1)colour=vec4(line.b,complete?1.:0.);
 if(outputMode==2)colour=vec4(line.width,line.height,line.kind,complete?1.:0.);
 if(outputMode==3)colour=vec4(line.previous,line.visibility,0.,complete?1.:0.);
}
'''
        cls.program = cls.context.program(vertex_shader=vertex, fragment_shader='#version 410\n'+query+source+main)
        cls.vao = cls.context.vertex_array(cls.program, [])
        cls.target = cls.context.simple_framebuffer((1, 1), components=4, dtype='f4')

    @classmethod
    def tearDownClass(cls):
        cls.target.release(); cls.vao.release(); cls.program.release(); cls.context.release()
        if cls.dll_directory is not None:
            cls.dll_directory.close()

    def fixture(self):
        positions = np.array(((1, .2, 2), (3, .6, 4), (5, .4, 6), (7, .8, 8)), np.float32)
        dims = np.array(((.2, .1), (.8, .2), (.4, .2), (.6, .4)), np.float32)
        attributes = tuple((name, gl_name, kind, array) for name, gl_name, kind, array in (
            ('dimensions', 'a_line_dim', 'vector2f', dims),
            ('kind', 'a_line_type', 'float', np.array((1, 4, 6, 8), np.float32)),
            ('previous', 'a_prev_line_type', 'float', np.array((8, 1, 4, 6), np.float32)),
            ('extruder', 'a_extruder', 'float', np.array((5, 0, 0, 0), np.float32)),
            ('feedrate', 'a_feedrate', 'float', np.ones(4, np.float32)),
            ('material', 'a_material_color', 'vector4f', np.ones((4, 4), np.float32))))
        mesh = CaptureMesh(7, positions, np.array(((0, 1), (2, 3)), np.uint32),
                           None, np.ones((4, 4), np.float32), None, attributes)
        layout, size = mesh.layout()
        inputs = admit_path_inputs(mesh, BufferLease(31, size, layout), 1)
        packed = np.concatenate([array.reshape(-1) for _name, array in inputs.arrays])
        return inputs, packed

    def render(self, *, mode=0, model=None, visibility=None, indices=None, **override):
        inputs, packed = self.fixture()
        raw = np.zeros(192, np.float32); raw[:len(packed)] = packed
        ix = np.zeros(16, np.uint32); ix[:4] = inputs.indices.reshape(-1) if indices is None else indices
        self.program['rawVertices'].write(raw.tobytes()); self.program['rawIndices'].write(ix.tobytes())
        values = dict(rawVertexTexels=inputs.scalar_count, rawIndexTexels=4,
                      mpf_pathScalarCount=inputs.scalar_count, mpf_pathVertexCount=4,
                      mpf_path_sourceLineCount=2, mpf_pathClipEnabled=0,
                      mpf_pathLast=(-2., .4, -3.), mpf_pathNext=(3., .6, 4.), mpf_pathRatio=.25,
                      sourceLine=0, sourceHistory=0, outputMode=mode)
        for label, name in (('Position', 'a_vertex'), ('Dimensions', 'a_line_dim'), ('Type', 'a_line_type'),
                            ('Previous', 'a_prev_line_type'), ('Extruder', 'a_extruder')):
            _name, offset, width = next(field for field in inputs.fields if field[0] == name)
            values['mpf_path'+label], values['mpf_path'+label+'Stride'] = offset, width
        values.update(override)
        for name, value in values.items():
            self.program[name].value = value
        for name, data in (('mpf_pathModel', np.eye(4, dtype=np.float32) if model is None else model),
                           ('mpf_pathVisibility', np.ones((4, 4), np.float32) if visibility is None else visibility)):
            self.program[name].write(data.T.tobytes())
        self.target.use(); self.context.disable(moderngl.BLEND | moderngl.CULL_FACE | moderngl.DEPTH_TEST)
        self.vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4, dtype='f4'), np.float32).copy()

    def test_original_endpoint_dimensions_type_previous_and_visibility_delivery(self):
        np.testing.assert_allclose(self.render(), (1, .15, 2, 1), atol=2e-7, rtol=0)
        np.testing.assert_allclose(self.render(mode=1), (3, .5, 4, 1), atol=2e-7, rtol=0)
        np.testing.assert_allclose(self.render(mode=2), (.8, .2, 1, 1), atol=2e-7, rtol=0)
        visibility = np.ones((4, 4), np.float32); visibility[1, 1] = 0
        np.testing.assert_array_equal(self.render(mode=3, visibility=visibility), (8, 0, 0, 1))

    def test_native_partial_order_history_and_affine_model_are_preserved(self):
        model = np.eye(4, dtype=np.float32)
        model[:3, :3] = ((2, .5, 0), (0, 3, 0), (0, 0, -1))
        model[:3, 3] = (10, 20, 30)
        # Independent native mix before half-height; model is applied last.
        last, next_point = np.array((-2, .4, -3), np.float32), np.array((3, .6, 4), np.float32)
        point = last*np.float32(.75)+next_point*np.float32(.25)
        point[1] -= np.float32(.2)*np.float32(.5)
        expected = model @ np.append(point, np.float32(1))
        np.testing.assert_allclose(self.render(mode=1, model=model, mpf_pathClipEnabled=1), expected, atol=2e-6, rtol=0)
        history = model @ np.array((3, .5, 4, 1), np.float32)
        np.testing.assert_allclose(self.render(mode=1, model=model, mpf_pathClipEnabled=1, sourceHistory=1),
                                   history, atol=2e-6, rtol=0)

    def test_malformed_source_counts_strides_and_indices_refuse_before_addressing(self):
        for override in (dict(rawIndexTexels=3), dict(rawIndexTexels=2), dict(mpf_pathScalarCount=1),
                         dict(mpf_pathPosition=-1), dict(mpf_pathPosition=0x7fffffff),
                         dict(mpf_pathPositionStride=0x7fffffff), dict(mpf_pathDimensionsStride=1),
                         dict(mpf_pathVertexCount=-1), dict(sourceLine=2), dict(sourceLine=-1),
                         dict(mpf_pathClipEnabled=2), dict(mpf_pathClipEnabled=1, mpf_pathRatio=float('nan'))):
            with self.subTest(override=override):
                self.assertEqual(self.render(**override)[3], 0)
        for index in (4, 0xffffffff):
            self.assertEqual(self.render(indices=(index, 1, 2, 3))[3], 0)
        self.assertEqual(self.render()[3], 1)  # Refusal cannot poison the next publication.


if __name__ == '__main__':
    unittest.main()
