# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead

"""Production single-fetch lookup: detail, continuous bed motion and safe fallback."""
import configparser
from pathlib import Path
import unittest
import numpy as np
from tools.capture_toolhead import create_context, uniforms


class CentralReflectionGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx, cls.dll = create_context()
        parser=configparser.ConfigParser(interpolation=None,comment_prefixes=(';',))
        parser.read(Path(__file__).resolve().parents[1]/'mpf/toolhead/toolhead.shader')
        source=parser['shaders']['fragment41core']
        cls.helper=source[source.index('vec3 centralReflectionDirection'):source.index('float grainHash')]
        cls.programs={}
        prefix='''#version 410
uniform vec3 v_position,u_probe,ray;
uniform float u_probeFar,roughness,u_environmentGroundMaxZ;
uniform int u_environmentGroundEnabled;
uniform vec4 u_environmentGround;
uniform samplerCube u_environment,u_environmentPrevious;
uniform int u_environmentPreviousEnabled,u_previousGroundEnabled;
uniform float u_environmentBlend,u_previousFar,u_previousGroundMaxZ;
uniform vec3 u_previousProbe;
uniform vec4 u_previousGround;
out vec4 colour;
'''
        vertex='#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        for name,body in [('direction','colour=vec4(centralReflectionDirection(ray),centralReflectionLevel(roughness));'),
                          ('colour','float confidence;colour=vec4(environmentReflection(ray,roughness,confidence),confidence);')]:
            program=cls.ctx.program(vertex_shader=vertex,fragment_shader=prefix+cls.helper+'\nvoid main(){'+body+'}')
            cls.programs[name]=(program,cls.ctx.vertex_array(program,[]))
        cls.target=cls.ctx.simple_framebuffer((1,1),components=4,dtype='f4')
        cls.target.use()

    @classmethod
    def tearDownClass(cls):
        for program,vao in cls.programs.values():vao.release();program.release()
        cls.target.release();cls.ctx.release()
        if cls.dll is not None:cls.dll.close()

    def sample(self,mode='direction',**changes):
        program,vao=self.programs[mode]
        values=dict(v_position=(0,6,0),u_probe=(0,10,0),ray=(0,-1,0),roughness=.5,
            u_probeFar=500.,u_environmentGroundEnabled=1,u_environmentGround=(0,-90,-90,90),
            u_environmentGroundMaxZ=90.,u_environment=7,u_environmentPreviousEnabled=0)
        values.update(changes);uniforms(program,values)
        self.target.use();vao.render(vertices=3)
        return np.frombuffer(self.target.read(components=4,dtype='f4'),np.float32).copy()

    def plane_cube(self,probe):
        import moderngl
        size=256
        texture=self.ctx.texture_cube((size,size),3,dtype='f4')
        self.addCleanup(texture.release)
        background=np.full((size,size,3),.1,np.float32)
        for face in range(6):texture.write(face,background.tobytes())
        coordinates=2*(np.arange(size,dtype=np.float32)+.5)/size-1
        s,t=np.meshgrid(coordinates,coordinates)
        x=probe[0]+probe[1]*s;z=probe[2]-probe[1]*t
        rgb=np.stack(((x+100)/200,(z+100)/200,.5+.4*np.sin(x*2)*np.cos(z*2)),axis=-1).astype('f4')
        texture.write(3,rgb.tobytes());texture.build_mipmaps()
        texture.filter=(moderngl.LINEAR_MIPMAP_LINEAR,moderngl.LINEAR)
        return texture

    def test_temporal_blend_endpoints_and_intermediate_use_each_probe_descriptor(self):
        old=self.plane_cube((0,10,0));new=self.plane_cube((4,10,0))
        old.use(7);before=self.sample('colour',roughness=.05,u_environmentGround=(-2,-90,-90,90),ray=(.2,-1,.1))
        new.use(7);after=self.sample('colour',u_probe=(4,10,0),roughness=.05,ray=(.2,-1,.1))
        old.use(3)
        for weight in (0.,.25,.5,1.):
            actual=self.sample('colour',u_probe=(4,10,0),roughness=.05,ray=(.2,-1,.1),
                u_environmentPreviousEnabled=1,u_environmentPrevious=3,u_environmentBlend=weight,
                u_previousProbe=(0,10,0),u_previousFar=500.,u_previousGroundEnabled=1,
                u_previousGround=(-2,-90,-90,90),u_previousGroundMaxZ=90.)
            np.testing.assert_allclose(actual,before*(1-weight)+after*weight,atol=1e-6)
            if weight == 0.: np.testing.assert_array_equal(actual,before)
            if weight == 1.: np.testing.assert_array_equal(actual,after)
        self.assertEqual(self.ctx.error,'GL_NO_ERROR')

    def test_roughness_curve_preserves_detail_without_changing_sharp_endpoint(self):
        for roughness,level in ((0,0),(.2,.24),(.5,1.5),(.8,3.84),(1,6)):
            with self.subTest(roughness=roughness):
                self.assertAlmostEqual(float(self.sample(roughness=roughness)[3]),level,places=5)
        self.assertGreater(512/2**float(self.sample(roughness=.5)[3]),180)

    def test_bed_motion_is_continuous_and_capture_adoption_preserves_world_detail(self):
        direction=np.array((.1,-1,.15));direction/=np.linalg.norm(direction)
        old=self.plane_cube((0,10,0));new=self.plane_cube((4,10,0))
        before=[];after=[];raw=[]
        for x in np.linspace(-2,6,30):
            changes=dict(v_position=(float(x),6,0),ray=tuple(direction),roughness=.05)
            old.use(7);before.append(self.sample('colour',**changes))
            raw.append(self.sample('colour',u_environmentGroundEnabled=0,**changes))
            new.use(7);after.append(self.sample('colour',u_probe=(4,10,0),**changes))
        before,after,raw=map(np.asarray,(before,after,raw))
        expected=(np.linspace(-2,6,30)+.6+100)/200
        np.testing.assert_allclose(before[:,0],expected,atol=2e-5)
        np.testing.assert_allclose(after[:,0],expected,atol=2e-5)
        np.testing.assert_allclose(before[:,:2],after[:,:2],atol=2e-5)
        self.assertLess(float(np.max(abs(before[:,2]-after[:,2]))),.015)
        self.assertLess(float(np.ptp(raw[:,0])),1e-6)
        self.assertGreater(float(np.ptp(before[:,0])),.039)
        np.testing.assert_array_equal(before[:,3],np.ones(30))
        self.assertEqual(self.ctx.error,'GL_NO_ERROR')

    def test_receiver_distance_does_not_clip_a_certified_captured_ground_point(self):
        # Far bounds the capture camera; it is not a distance from this receiver.
        results=[self.sample(v_position=(4,y,0),u_probe=(0,2,0),u_probeFar=5.,roughness=0.)[:3]
            for y in (4.99,5.,5.01,100.)]
        expected=np.array((4.,-2.,0.));expected/=np.linalg.norm(expected)
        for result in results:np.testing.assert_allclose(result,expected,atol=1e-6)

    def test_grazing_edges_upward_and_missing_plane_have_defined_complete_fallback(self):
        for ray,position,enabled in (((0,1,0),(0,6,0),1),((1,0,0),(0,6,0),1),
                ((0,0,0),(0,6,0),1),((0,-1,0),(100,6,0),1),((0,-1,0),(0,-1,0),1),
                ((0,-1,0),(0,6,0),0)):
            actual=self.sample(ray=ray,v_position=position,u_environmentGroundEnabled=enabled)
            np.testing.assert_allclose(actual[:3],ray,atol=1e-6)
            self.assertTrue(np.isfinite(actual).all())
        # Footprint blending is continuous at its edge, not a binary branch.
        rays=[self.sample(v_position=(x,6,0))[:3] for x in (89.99,90.,90.01)]
        self.assertLess(float(np.max(abs(rays[0]-rays[1]))),.0001)
        np.testing.assert_array_equal(rays[1],rays[2])
