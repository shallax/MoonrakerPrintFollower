"""Exact full-hierarchy lookup, including numerical and budget fallbacks."""
import configparser
from pathlib import Path
import unittest

import numpy as np
from tools.capture_toolhead import create_context, uniforms
try:
    import moderngl
except ImportError:
    moderngl = None


def _atlas(faces):
    """Independent CPU min/max oracle in canonical paired-axis coordinates."""
    x,y=np.meshgrid(np.arange(512),np.arange(512));canonical=[]
    for axis in range(3):
        front=((511-y,511-x),(y,x),(x,511-y))[axis]
        back=((y,511-x),(y,511-x),(511-x,511-y))[axis]
        canonical.append(np.stack((faces[2*axis][front[1],front[0]],
            faces[2*axis+1][back[1],back[0]]),-1))
    atlas=np.zeros((768,512,4),dtype='f4');atlas[:,:,(0,2)]=1.
    for level in range(1,10):
        side=512>>level;tile=1<<level;offset=512-(512>>(level-1))
        for axis,values in enumerate(canonical):
            cells=values.reshape(side,tile,side,tile,2).transpose(0,2,1,3,4).reshape(side,side,tile*tile,2)
            valid=(cells>=0)&(cells<1)
            low=np.where(valid,cells,1.).min(2);high=np.where(valid,cells,0.).max(2)
            atlas[axis*256:axis*256+side,offset:offset+side]=np.stack((low[:,:,0],high[:,:,0],low[:,:,1],high[:,:,1]),-1)
    return atlas


@unittest.skipIf(moderngl is None,"The capture OpenGL runtime is required")
class FullHierarchyLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context,cls.dll=create_context()
        config=configparser.ConfigParser(interpolation=None,comment_prefixes=(';',))
        config.read(Path(__file__).parents[1]/'mpf/toolhead/toolhead.shader')
        fragment=config['shaders']['fragment41core']
        cls.helpers=fragment[fragment.index('vec3 directionalLookup'):fragment.index('vec3 directionalSample')]

    @classmethod
    def tearDownClass(cls):
        cls.context.release()
        if cls.dll is not None:cls.dll.close()

    def test_full_and_forced_fine_continuation_match_original_closed_cell_walk(self):
        ctx=self.context;owned=[]
        try:
            header='''#version 410
#define MPF_USE_FULL_HIERARCHY %d
uniform samplerCube u_sceneDepth,u_depthHierarchy;
uniform int u_depthHierarchyEnabled;
uniform sampler2D origins,rays;
uniform vec3 u_sceneMin,u_sceneMax;
uniform float u_probeFar,u_probeNear;
out vec4 result;
'''
            footer='''
void main(){ivec2 p=ivec2(gl_FragCoord.xy);vec3 lookup;
float hit=directionalHit(texelFetch(origins,p,0).xyz,texelFetch(rays,p,0).xyz,lookup);
result=hit<0.?vec4(0.,0.,0.,-1.):vec4(lookup,hit);}
'''
            vertex='''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'''
            programs=[];vaos=[]
            for enabled,forced in ((0,False),(1,False),(1,True)):
                helpers=self.helpers.replace('17410','1034').replace('16384','8') if forced else self.helpers
                program=ctx.program(vertex_shader=vertex,fragment_shader=header%enabled+helpers+footer)
                vao=ctx.vertex_array(program,[]);owned.extend((program,vao));programs.append(program);vaos.append(vao)
            output=ctx.simple_framebuffer((64,64),components=4,dtype='f4');owned.append(output)
            rng=np.random.default_rng(47191)
            for translation,span in (((0.,0.,0.),(50.,50.,50.)),((1e6,-1e6,1e6),(50.,50.,50.)),((-1e5,1e5,-1e5),(180.,12.,75.))):
                centre=np.array(translation,dtype='f4');span=np.array(span,dtype='f4');low=centre-span/2;high=centre+span/2
                origin=(centre+rng.uniform(-.7,.7,(64,64,3))*span).astype('f4')
                ray=rng.normal(size=(64,64,3)).astype('f4');ray/=np.linalg.norm(ray,axis=2)[:,:,None]
                ray[0,:6]=np.array(((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)),dtype='f4')
                ray[1,:3]=np.array(((1,1,1),(1,-1,1),(-1,1,-1)),dtype='f4')/np.sqrt(np.float32(3))
                ray[2,:,0]=np.float32(1e-7)
                faces=rng.uniform(.005,.06,(6,512,512)).astype('f4')
                # Sparse holes, invalid sentinels, tile/fine edges and one-texel extrema.
                faces[:,::3,:]=1.;faces[:,:,::7]=1.;faces[:,100:104,200:232]=np.nan
                faces[:,20:52,20:52]=.025;faces[:,32,32]=.001;faces[:,300:320,20:52]=-1.
                cube=ctx.texture_cube((512,512),1,dtype='f4');cube.filter=(moderngl.NEAREST,moderngl.NEAREST)
                atlas=ctx.texture((512,768),4,_atlas(faces).tobytes(),dtype='f4')
                origins=ctx.texture((64,64),3,origin.tobytes(),dtype='f4');rays=ctx.texture((64,64),3,ray.tobytes(),dtype='f4')
                try:
                    for face in range(6):cube.write(face,faces[face].tobytes())
                    cube.use(6);atlas.use(4);origins.use(0);rays.use(1)
                    output.use();ctx.viewport=(0,0,64,64);ctx.disable(moderngl.DEPTH_TEST|moderngl.BLEND|moderngl.CULL_FACE)
                    images=[]
                    for program,vao in zip(programs,vaos,strict=True):
                        uniforms(program,dict(u_sceneDepth=6,u_depthHierarchy=5,u_depthHierarchyEnabled=0,
                            u_fullHierarchy=4,u_fullHierarchyEnabled=1,origins=0,rays=1,
                            u_sceneMin=tuple(low),u_sceneMax=tuple(high),u_probeFar=-1000.,u_probeNear=.2))
                        vao.render(vertices=3);images.append(np.frombuffer(output.read(components=4,dtype='f4'),np.float32).copy())
                    np.testing.assert_array_equal(images[1],images[0],err_msg=f'full hierarchy {translation}')
                    np.testing.assert_array_equal(images[2],images[0],err_msg=f'budget fallback {translation}')
                    programs[1]['u_fullHierarchyEnabled'].value=0;vaos[1].render(vertices=3)
                    np.testing.assert_array_equal(np.frombuffer(output.read(components=4,dtype='f4'),np.float32),images[0])
                finally:
                    for resource in (cube,atlas,origins,rays):resource.release()
            self.assertEqual(ctx.error,'GL_NO_ERROR')
        finally:
            for resource in reversed(owned):resource.release()
