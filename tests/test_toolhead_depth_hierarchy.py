# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead

"""Exact GPU reduction and accelerated/reference reflection lookup parity."""
import configparser
from pathlib import Path
import time
import unittest

import numpy as np
from tools.capture_toolhead import create_context, uniforms
from mpf.toolhead.ToolheadDepthHierarchy import FRAGMENT, VERTEX, RETAINED_BYTES, SIZE, TILE_SIZE
try:
    import moderngl
except ImportError:
    moderngl = None


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class DepthHierarchyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll = create_context()
        config = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
        config.read(Path(__file__).parents[1]/'mpf/toolhead/toolhead.shader')
        source = config['shaders']['fragment41core']
        helpers = source[source.index('vec3 directionalLookup'):source.index('vec3 directionalReflection')]
        cls.program = cls.context.program(vertex_shader=VERTEX, fragment_shader='''#version 410
uniform samplerCube u_sceneDepth,u_environment,u_depthHierarchy;
uniform int u_depthHierarchyEnabled,scenario;
uniform vec3 u_sceneMin,u_sceneMax;
uniform float u_probeFar,u_probeNear;
out vec4 colour;
'''+helpers+'''
void main(){
 vec2 p=(gl_FragCoord.xy-vec2(32.))/32.;
 vec3 origin=vec3(p*27.,-26.),ray=normalize(vec3(p*.4,1.));
 if(scenario==1){origin=vec3(0.);ray=normalize(vec3(p,1.));}
 if(scenario==2){origin=vec3(p*24.999999,0.);ray=normalize(vec3(-p,1e-7));}
 if(scenario==3){origin=vec3(-25.,p*25.);ray=normalize(vec3(1.,p*.000001));}
 if(scenario==4){origin=vec3(25.,p*25.);ray=normalize(vec3(-1.,-p));}
 if(scenario==5){origin=vec3(p*25.,25.);ray=normalize(vec3(-p,-1.));}
 if(scenario==6){origin=vec3(-21.875,-21.875,p.x*24.);ray=normalize(vec3(1.,1.,p.y));}
 if(scenario==7){origin=vec3(21.875,21.875,p.x*24.);ray=normalize(vec3(-1.,-1.,p.y));}
 if(scenario==8){origin=vec3(-21.875001,-21.875001,p.x*24.);ray=normalize(vec3(1.,1e-7,p.y));}
 if(scenario==9){origin=vec3(21.875001,21.875001,p.x*24.);ray=normalize(vec3(-1.,-1e-7,p.y));}
 vec3 lookup=vec3(0.);float t=directionalHit(origin,ray,lookup);
 colour=t>=0.?vec4(lookup,t):vec4(0.,0.,0.,-1.);
}''')
        cls.vao = cls.context.vertex_array(cls.program, [])
        # Instrument the exact production helper without changing its traversal.
        instrumented = 'int longestWalk=0;int skippedCells=0;\n'+helpers.replace(
            'if(any(lessThan(cell', 'longestWalk=max(longestWalk,step+1);if(any(lessThan(cell').replace(
            'if(exitTime<entry){', 'if(exitTime<entry){skippedCells++;')
        cls.counter = cls.context.program(vertex_shader=VERTEX,fragment_shader='''#version 410
uniform samplerCube u_sceneDepth,u_environment,u_depthHierarchy;
uniform int u_depthHierarchyEnabled;
uniform vec3 u_sceneMin,u_sceneMax;
uniform float u_probeFar,u_probeNear;
out vec4 colour;
'''+instrumented+'''
void main(){
 vec2 p=(gl_FragCoord.xy-vec2(32.))/32.;vec3 lookup;
 float t=directionalHit(vec3(-25.,p*25.),normalize(vec3(1.,-p)),lookup);
 colour=vec4(float(longestWalk),t,float(skippedCells),1.);
}''')
        cls.counter_vao = cls.context.vertex_array(cls.counter, [])
        cls.reducer = cls.context.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        cls.reduce_vao = cls.context.vertex_array(cls.reducer, [])

    @classmethod
    def tearDownClass(cls):
        for value in (cls.vao, cls.program, cls.reduce_vao, cls.reducer, cls.counter, cls.counter_vao): value.release()
        cls.context.release()
        if cls.dll is not None: cls.dll.close()

    def maps(self, seed):
        rng = np.random.default_rng(seed)
        x,y = np.meshgrid(np.arange(512), np.arange(512))
        faces = []
        for face in range(6):
            values = np.full((512,512), .024, np.float32)
            values += (x//64)*.0007+(y//64)*.00031+face*.00013
            values[((x//48+y//35+face)%5)==0] = 1.
            if seed:
                values = rng.choice(np.array([0.,1.,.001,.02,.025,.049,np.nextafter(np.float32(1),np.float32(0))],np.float32), (512,512))
            faces.append(values)
        return faces

    def reduce(self, faces):
        cube = self.context.texture_cube((512,512),1,dtype='f4')
        cube.filter = (moderngl.NEAREST,moderngl.NEAREST)
        ranges = self.context.texture_cube((SIZE,SIZE),2,dtype='f4')
        ranges.filter = (moderngl.NEAREST,moderngl.NEAREST)
        output = self.context.simple_framebuffer((SIZE,SIZE),components=2,dtype='f4')
        before = time.perf_counter()
        try:
            for face, values in enumerate(faces): cube.write(face,values.tobytes())
            cube.use(6);output.use()
            self.context.disable(moderngl.DEPTH_TEST|moderngl.CULL_FACE|moderngl.BLEND)
            for face, values in enumerate(faces):
                uniforms(self.reducer,dict(sourceDepth=6,face=face))
                self.reduce_vao.render(vertices=3)
                actual = np.frombuffer(output.read(components=2,dtype='f4'),np.float32).reshape(SIZE,SIZE,2)
                blocks = values.reshape(SIZE,TILE_SIZE,SIZE,TILE_SIZE).transpose(0,2,1,3).reshape(SIZE,SIZE,TILE_SIZE*TILE_SIZE)
                valid = (blocks>=0)&(blocks<1)
                expected = np.stack((np.where(valid,blocks,1).min(2),np.where(valid,blocks,0).max(2)),axis=-1)
                np.testing.assert_array_equal(actual.view('u4'),expected.view('u4'))
                ranges.write(face,actual.tobytes())
            self.context.finish()
            self.assertEqual(self.context.error,'GL_NO_ERROR')
            self.reduction_seconds = time.perf_counter()-before
        finally: output.release()
        return cube,ranges

    def test_range_reduction_exact_float_extrema_and_empty_sentinel(self):
        faces = self.maps(9)
        faces[0][:TILE_SIZE,:TILE_SIZE] = 1.
        faces[1][0,:8] = [np.nan,np.inf,-np.inf,-1.,0.,0.,np.float32(1/16777215),np.nextafter(np.float32(1),np.float32(0))]
        cube,ranges = self.reduce(faces)
        cube.release();ranges.release()
        self.assertEqual(RETAINED_BYTES,152*1024 + 2*512*768*16 + 2*256*256*16)

    def test_tile_skip_matches_fine_dda_at_edges_corners_and_grazing_rays(self):
        target = self.context.simple_framebuffer((64,64),components=4,dtype='f4')
        try:
            for seed in (0,11):
                cube,ranges = self.reduce(self.maps(seed))
                try:
                    cube.use(6);ranges.use(5);target.use()
                    uniforms(self.program,dict(u_sceneDepth=6,u_depthHierarchy=5,u_sceneMin=(-25.,)*3,
                        u_sceneMax=(25.,)*3,u_probeFar=-1000.,u_probeNear=.2))
                    for scenario in range(10):
                        results=[]
                        for enabled in (0,1):
                            uniforms(self.program,dict(scenario=scenario,u_depthHierarchyEnabled=enabled))
                            self.vao.render(vertices=3)
                            results.append(target.read(components=4,dtype='f4'))
                        self.assertEqual(results[0],results[1],f'seed={seed}, scenario={scenario}')
                    self.assertEqual(self.context.error,'GL_NO_ERROR')
                finally: cube.release();ranges.release()
        finally: target.release()

    def test_walk_bound_with_empty_tiles_and_sparse_boundary_records(self):
        faces = [np.ones((512,512),np.float32) for _ in range(6)]
        for face in range(6):
            faces[face][::TILE_SIZE,::TILE_SIZE]=.025
        cube,ranges = self.reduce(faces)
        target=self.context.simple_framebuffer((64,64),components=4,dtype='f4')
        try:
            cube.use(6);ranges.use(5);target.use()
            uniforms(self.counter,dict(u_sceneDepth=6,u_depthHierarchy=5,u_depthHierarchyEnabled=1,
                u_sceneMin=(-25.,)*3,u_sceneMax=(25.,)*3,u_probeFar=-1000.,u_probeNear=.2))
            self.counter_vao.render(vertices=3)
            output=np.frombuffer(target.read(components=4,dtype='f4'),np.float32).reshape(-1,4)
            self.assertLess(output[:,0].max(),1121.)
            self.assertGreater(output[:,2].sum(),0.,'Rewound empty intervals must bypass depth reads')
            print('Hierarchy: six-face GPU reduction/readback/upload %.3f ms; maximum sparse-boundary walk %d iterations' %
                (self.reduction_seconds*1000,output[:,0].max()))
        finally:target.release();cube.release();ranges.release()
