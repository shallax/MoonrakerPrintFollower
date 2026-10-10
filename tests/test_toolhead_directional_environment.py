"""Production directional lookup: complete texel traversal and true empty space."""
import configparser
from pathlib import Path
import unittest
import numpy as np
from tools.capture_toolhead import create_context, uniforms
import moderngl


class DirectionalEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context,cls.dll=create_context()
        config=configparser.ConfigParser(interpolation=None,comment_prefixes=(';',))
        config.read(Path(__file__).parents[1]/'mpf/toolhead/toolhead.shader')
        source=config['shaders']['fragment41core']
        helpers=source[source.index('vec3 directionalLookup'):source.index('vec3 directionalReflection')]
        cls.shader=cls.context.program(vertex_shader='''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}''',fragment_shader='''#version 410
uniform samplerCube u_sceneDepth,u_environment;
uniform vec3 u_sceneMin,u_sceneMax,origin,ray;
uniform float u_probeFar,u_probeNear;
out vec4 colour;
'''+helpers+'''
void main(){vec3 lookup;float t=directionalHit(origin,normalize(ray),lookup);colour=t>=0.?vec4(origin+normalize(ray)*t,1.):vec4(0.);}''')
        cls.vao=cls.context.vertex_array(cls.shader,[])
        cls.target=cls.context.simple_framebuffer((1,1),components=4,dtype='f4')

    @classmethod
    def tearDownClass(cls):
        cls.vao.release();cls.shader.release();cls.target.release();cls.context.release()
        if cls.dll is not None:cls.dll.close()

    def trace(self,bounds,origin,ray,surfaces):
        low,high=map(np.asarray,bounds);centre=(low+high)*.5;extent=(high-low)*.5
        uv=(np.arange(512)+.5)/512*2-1;x,y=np.meshgrid(uv,uv)
        axes=((np.ones_like(x),-y,-x),(-np.ones_like(x),-y,x),(x,np.ones_like(x),y),
              (x,-np.ones_like(x),-y),(x,-y,np.ones_like(x)),(-x,-y,-np.ones_like(x)))
        cube=self.context.texture_cube((512,512),1,dtype='f4');cube.filter=(moderngl.NEAREST,moderngl.NEAREST)
        try:
            for face,q in enumerate(axes):
                points=np.stack(q,axis=-1)*extent+centre
                plane=surfaces(face,points)
                axis=face//2
                depth=(plane-low[axis]+.01 if face%2==0 else high[axis]+.01-plane)/(1000.-.2)
                cube.write(face,np.where(np.isfinite(plane),depth,1.).astype('f4').tobytes())
            cube.use(location=6);self.target.use();self.context.disable(moderngl.DEPTH_TEST|moderngl.CULL_FACE|moderngl.BLEND)
            uniforms(self.shader,dict(u_sceneDepth=6,u_sceneMin=tuple(low),u_sceneMax=tuple(high),
                u_probeFar=-1000.,u_probeNear=.2,origin=origin,ray=ray))
            self.vao.render(vertices=3)
            result=np.frombuffer(self.target.read(components=4,dtype='f4'),np.float32).copy()
            self.assertEqual(self.context.error,'GL_NO_ERROR');return result
        finally:cube.release()

    def test_opposite_capture_finds_wall_from_between_two_surfaces(self):
        for axis in range(3):
            def walls(face,p,axis=axis):
                return np.full(p.shape[:2],-2. if face==axis*2 else 2. if face==axis*2+1 else np.nan)
            for side in (-1.,1.):
                ray=[0.,0.,0.];ray[axis]=side
                actual=self.trace(((-10.,)*3,(10.,)*3),(0,0,0),tuple(ray),walls)
                self.assertEqual(actual[3],1.);self.assertAlmostEqual(actual[axis],side*2.,places=4)

    def test_thin_oblique_feature_cannot_fall_between_uniform_steps(self):
        def box(face,p):
            axis=face//2;other=[i for i in range(3) if i!=axis]
            present=np.all((p[:,:,other]>=3.)&(p[:,:,other]<=3.4),axis=2)
            return np.where(present,3. if face%2==0 else 3.4,np.nan)
        actual=self.trace(((-100.,)*3,(100.,)*3),(0,0,0),(1,1,1),box)
        self.assertEqual(actual[3],1.)
        self.assertTrue(np.all(actual[:3]>=2.9999));self.assertTrue(np.all(actual[:3]<=3.4001))

    def test_silhouette_step_does_not_create_a_bridge_between_depths(self):
        def step(face,p):
            return np.where(p[:,:,0]<0.,4.,12.) if face==4 else np.full(p.shape[:2],np.nan)
        actual=self.trace(((-20.,)*3,(20.,)*3),(-2,0,0),(1,0,1),step)
        self.assertEqual(actual[3],1.);self.assertAlmostEqual(actual[2],12.,places=4)

    def test_empty_views_remain_a_real_miss(self):
        actual=self.trace(((-10.,)*3,(10.,)*3),(0,0,0),(1,1,1),lambda face,p:np.full(p.shape[:2],np.nan))
        np.testing.assert_array_equal(actual,np.zeros(4))
