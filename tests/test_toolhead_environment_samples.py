"""Actual S4 coverage and raw planes, without sample-frequency shortcuts."""
import configparser
import ctypes
from types import SimpleNamespace
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentRecovery as recovery
from tools.capture_toolhead import create_context

try:
    import moderngl
except ImportError:
    moderngl = None


@unittest.skipIf(moderngl is None, 'The capture OpenGL runtime is required')
class EnvironmentSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.dll_directory = create_context()
        cls.guard=cls.context.simple_framebuffer((1,1))

    @classmethod
    def tearDownClass(cls):
        cls.guard.release()
        cls.context.release()
        if cls.dll_directory is not None: cls.dll_directory.close()

    def setUp(self):
        self.objects = []
        self.vertex = '''#version 410
uniform float z;in vec2 position;out vec4 v_color;
void main(){gl_Position=vec4(position,z,1.);v_color=vec4(1.);}
'''
        self.full = self.own(self.context.buffer(np.array(
            ((-1.,-1.),(3.,-1.),(-1.,3.)), np.float32).tobytes()))

    def tearDown(self):
        self.guard.use()
        for value in reversed(self.objects): value.release()
        self.assertEqual(self.context.error, 'GL_NO_ERROR')

    def own(self, value):
        self.objects.append(value); return value

    def raw(self,name,result,*arguments):
        address=self.context.mglo._context.load_opengl_function(name)
        self.assertTrue(address)
        return ctypes.CFUNCTYPE(result,*arguments)(address)

    def program(self, source, vertex=None):
        return self.own(self.context.program(vertex_shader=vertex or self.vertex, fragment_shader=source))

    def draw(self, program, buffer=None):
        vao = self.own(self.context.vertex_array(program, [(buffer or self.full, '2f', 'position')]))
        vao.render(vertices=3)

    def texture(self, size, components=4):
        return self.own(self.context.texture(size, components, dtype='f4', samples=4))

    def target(self, texture, depth=None):
        return self.own(self.context.framebuffer((texture,), depth))

    def seed(self, target, values):
        program = self.program('''#version 410
uniform int plane;uniform vec4 value;out vec4 colour;
void main(){gl_SampleMask[0]=1<<plane;colour=value;}
''')
        target.use(); self.context.viewport=(0,0,*target.size)
        self.context.disable(moderngl.DEPTH_TEST|moderngl.BLEND|moderngl.CULL_FACE)
        for plane, value in enumerate(values):
            program['plane'].value=plane; program['value'].value=value
            self.draw(program)

    def planes(self, texture):
        """Export each original plane by texelFetch, never MSAA resolve."""
        image = self.own(self.context.texture(texture.size, 4, dtype='f4'))
        target = self.target(image)
        program = self.program('''#version 410
uniform sampler2DMS source;uniform int plane;out vec4 colour;
void main(){colour=texelFetch(source,ivec2(gl_FragCoord.xy),plane);}
''')
        texture.use(0); program['source'].value=0
        target.use(); self.context.viewport=(0,0,*texture.size)
        self.context.disable(moderngl.DEPTH_TEST|moderngl.BLEND|moderngl.CULL_FACE)
        result=[]
        for plane in range(4):
            program['plane'].value=plane; self.draw(program)
            result.append(np.frombuffer(image.read(), np.float32).copy().reshape(
                texture.size[1],texture.size[0],4))
        return np.stack(result)

    def select(self, cursor_values, surfaces, size=(1,1), buffer=None, *, previous=1):
        cursor=self.texture(size); self.seed(self.target(cursor),cursor_values)
        depth=self.own(self.context.depth_texture(size,samples=4))
        scalars={stage:self.texture(size,2 if stage=='depth' else 1) for stage in ('depth','identity')}
        targets={stage:self.target(texture,depth) for stage,texture in scalars.items()}
        targets['depth'].clear(depth=.75)
        for stage, sentinel in (('depth',np.finfo(np.float32).max),('identity',16777216.)):
            # A separate seed keeps original visibility depth unchanged.
            self.seed(targets[stage],((sentinel,1. if stage=='depth' else 0.,0.,1.),)*4)
            program=self.program(recovery.layer_selection_fragment(stage,samples=4))
            cursor.use(0); scalars['depth'].use(1)
            for name,value in dict(mpf_previousCursor=0,mpf_selectedDepth=1,
                    mpf_layerSamples=4,mpf_layerPrevious=previous,mpf_layerSize=size,
                    mpf_layerOrigin=(0,0),mpf_layerCount=1).items():
                if name in program: program[name].value=value
            targets[stage].use(); targets[stage].depth_mask=False
            self.context.viewport=(0,0,*size)
            self.context.enable(moderngl.BLEND|moderngl.DEPTH_TEST)
            self.context.disable(moderngl.CULL_FACE)
            self.context.depth_func='<='; self.context.blend_equation=moderngl.MIN
            for identity,z in surfaces:
                program['mpf_layerBase'].value=identity; program['z'].value=z
                self.draw(program,buffer)
        return self.planes(scalars['depth'])[...,:2],self.planes(scalars['identity'])[...,0]

    def test_four_independent_cursors_choose_distinct_layers_without_resolve(self):
        depth,identity=self.select(((.1,0.,0.,1.),(.25,3.,0.,1.),(.4,6.,0.,1.),(0.,0.,0.,0.)),
            ((8,-.5),(2,-.5),(3,-.5),(6,.1),(9,.9)))
        np.testing.assert_array_equal(depth[:,0,0,0],np.array((.25,.25,.55,np.finfo(np.float32).max),np.float32))
        np.testing.assert_array_equal(identity[:,0,0],(2.,8.,6.,16777216.))

    def test_covered_malformed_plane_publishes_fault_instead_of_empty(self):
        depth,identity=self.select(((0.,0.,0.,1.),(0.,0.,0.,1.),(float('nan'),0.,0.,1.),(0.,0.,0.,0.)),
            ((3,-.5),))
        self.assertEqual(depth[2,0,0,1],-1.)
        self.assertEqual(identity[2,0,0],-1.)
        self.assertEqual(depth[3,0,0,1],1.)
        self.assertEqual(identity[3,0,0],16777216.)

    def test_partial_triangle_matches_original_four_plane_coverage(self):
        size=(3,2)
        buffer=self.own(self.context.buffer(np.array(((-1.,-1.),(1.,-1.),(-1.,.5)),np.float32).tobytes()))
        reference=self.texture(size); target=self.target(reference); target.clear()
        target.use(); self.context.viewport=(0,0,*size)
        self.context.disable(moderngl.DEPTH_TEST|moderngl.BLEND|moderngl.CULL_FACE)
        program=self.program('#version 410\nout vec4 colour;void main(){colour=vec4(1.);}')
        program['z'].value=-.5; self.draw(program,buffer)
        covered=self.planes(reference)[...,0]==1.
        self.assertTrue(np.any(covered)); self.assertTrue(np.any(~covered))
        self.assertTrue(np.any(np.any(covered,axis=0)&~np.all(covered,axis=0)))
        depth,identity=self.select(((0.,0.,0.,1.),)*4,((3,-.5),),size,buffer)
        np.testing.assert_array_equal(identity==3.,covered)
        np.testing.assert_array_equal(depth[...,0]==.25,covered)

    def test_near_clipped_triangle_keeps_finite_extrapolated_rank_and_original_coverage(self):
        size=(4,4)
        # Covered sample centres lie beyond the near clipping edge while the
        # original pixel-frequency interpolation location remains behind it.
        self.vertex='''#version 410
uniform float z;in vec2 position;out vec4 v_color;
void main(){gl_Position=vec4(position,z+position.x+1.,1.);v_color=vec4(1.);}
'''
        buffer=self.full
        reference=self.texture(size); visible=self.own(self.context.depth_texture(size,samples=4))
        target=self.target(reference,visible); target.use(); target.clear(depth=.75)
        self.context.viewport=(0,0,*size); self.context.enable(moderngl.DEPTH_TEST)
        self.context.disable(moderngl.BLEND|moderngl.CULL_FACE); self.context.depth_func='<='
        program=self.program('#version 410\nout vec4 colour;void main(){colour=vec4(gl_FragCoord.z,0.,0.,1.);}')
        program['z'].value=-1.3; self.draw(program,buffer)
        original=self.planes(reference); covered=original[...,3]==1.
        self.assertTrue(np.any(covered)); self.assertTrue(np.any(original[...,0][covered]<0.))
        depth,identity=self.select(((0.,0.,0.,0.),)*4,((3,-1.3),),size,buffer,previous=0)
        np.testing.assert_array_equal(identity==3.,covered)
        np.testing.assert_array_equal(depth[...,1][covered],np.zeros(np.count_nonzero(covered),np.float32))
        np.testing.assert_array_equal(depth[...,0][covered].view(np.uint32),original[...,0][covered].view(np.uint32))

    def test_original_unlit_and_material_edit_glass_blend_once_in_each_plane(self):
        parser=configparser.ConfigParser(interpolation=None,comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead','toolhead.shader'))
        vertex=recovery.layer_vertex(parser['shaders']['vertex41core']); original=parser['shaders']['fragment41core']
        programs=(self.program(original,vertex),self.program(recovery.layer_lookup_fragment(original,samples=4),vertex))
        cube=self.own(self.context.texture_cube((1,1),4,data=np.zeros((6,4),np.float32).tobytes(),dtype='f4'))
        cube.use(6); cube.use(7)
        # ModernGL does not upload samplerBuffer uniforms with the integer GL
        # entry point. Use the actual ABI, as the product's Qt adapter does.
        use=self.raw('glUseProgram',None,ctypes.c_uint)
        uniform=self.raw('glUniform1i',None,ctypes.c_int,ctypes.c_int)
        generate=self.raw('glGenTextures',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))
        delete=self.raw('glDeleteTextures',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))
        active=self.raw('glActiveTexture',None,ctypes.c_uint)
        bind=self.raw('glBindTexture',None,ctypes.c_uint,ctypes.c_uint)
        attach=self.raw('glTexBuffer',None,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint)
        for unit,internal,bytes_ in ((8,0x823C,8),(9,0x8236,4),(10,0x8814,16)):
            backing=self.own(self.context.buffer(bytes(bytes_))); name=ctypes.c_uint(); generate(1,ctypes.byref(name))
            self.own(SimpleNamespace(release=lambda name=name:delete(1,ctypes.byref(name))))
            active(0x84C0+unit); bind(0x8C2A,name.value); attach(0x8C2A,internal,backing.glo)
        fields=(('a_vertex',3),('a_normal',3),('a_color',4),('a_surface',1),('a_material',4),('a_body',1),('a_finish',2))
        data=np.array([(x,y,-.5,0.,0.,1.,.5,.3,.1,.5,1.,.47,.8,.65,1.,0.,-1.,-1.)
            for x,y in ((-1.,-1.),(3.,-1.),(-1.,3.))],np.float32)
        buffer=self.own(self.context.buffer(data.tobytes()))
        for lighting,editing,depth_only in ((0,0,0),(1,1,0),(0,0,1)):
            images=[]
            for index,program in enumerate(programs):
                image=self.texture((3,2)); target=self.target(image); target.use(); target.clear(.1,.2,.3,.4)
                self.context.viewport=(0,0,3,2); self.context.disable(moderngl.DEPTH_TEST|moderngl.CULL_FACE)
                if depth_only: self.context.disable(moderngl.BLEND)
                else:
                    self.context.enable(moderngl.BLEND)
                    self.context.blend_equation=moderngl.FUNC_ADD
                    self.context.blend_func=moderngl.SRC_ALPHA,moderngl.ONE_MINUS_SRC_ALPHA
                for name in ('u_modelMatrix','u_viewMatrix','u_projectionMatrix','u_normalMatrix','u_previewRotation'):
                    if name in program: program[name].write(np.eye(4,dtype=np.float32).tobytes())
                for name,value in dict(u_opacity=1.,u_lightingEnabled=lighting,u_materialEditEnabled=editing,
                        u_depthOnly=depth_only,mpf_layerSampleCount=4,mpf_layerBase=0,mpf_layerCount=1,
                        u_environment=6,u_sceneDepth=7).items():
                    if name in program: program[name].value=value
                use(program.glo)
                for name,unit in (('mpf_layerRanges',8),('mpf_layerIdentities',9),('mpf_layerColours',10)):
                    if name in program: uniform(program[name].location,unit)
                layout=' '.join(f'{count}f' if field in program else f'{count*4}x' for field,count in fields)
                vao=self.own(self.context.vertex_array(program,[(buffer,layout,*[f for f,_ in fields if f in program])]))
                for plane in range(4 if index and not depth_only else 1):
                    if index: program['mpf_layerLookupPlane'].value=plane
                    vao.render(vertices=3)
                images.append(self.planes(image))
            np.testing.assert_array_equal(images[0].view(np.uint32),images[1].view(np.uint32))
            if not depth_only: self.assertTrue(np.all((images[0][...,3]>0.)&(images[0][...,3]<.5)))

    def test_original_material_computation_precedes_all_four_sample_masks(self):
        parser=configparser.ConfigParser(interpolation=None,comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead','toolhead.shader'))
        source=recovery.layer_receiver_fragment(parser['shaders']['fragment41core'],samples=4)
        main=source[source.index('void main() {'):]
        self.assertLess(main.index('surfaceNormal()'),main.index('gl_SampleMask[0]'))
        self.assertLess(main.index('materialFinish('),main.index('gl_SampleMask[0]'))
        for forbidden in ('gl_SampleID','gl_SamplePosition','sample in','mpf_trace_paths('):
            self.assertNotIn(forbidden,source)
        self.assertIn('mpf_layerSamples!=4',source)
        for samples in (0,2,8,True):
            with self.assertRaises(ValueError): recovery.layer_selection_fragment('depth',samples=samples)
            with self.assertRaises(ValueError): recovery.layer_receiver_fragment(parser['shaders']['fragment41core'],samples=samples)

    def test_original_grain_roughness_and_partial_coverage_are_word_exact_per_plane(self):
        parser=configparser.ConfigParser(interpolation=None,comment_prefixes=(';',))
        parser.read(recovery.plugin_path('toolhead','toolhead.shader'))
        vertex=recovery.layer_vertex(parser['shaders']['vertex41core'])
        original=parser['shaders']['fragment41core']
        programs={name:self.program(source,vertex) for name,source in (
            ('reference',recovery.receiver_fragment(original)),
            ('layer',recovery.layer_receiver_fragment(original,samples=4)))}
        size=(7,5)
        cursor=self.texture(size); self.seed(self.target(cursor),((0.,0.,0.,0.),)*4)
        selected_depth=self.texture(size,2); self.seed(self.target(selected_depth),((.25,0.,0.,1.),)*4)
        selected_id=self.texture(size,1); self.seed(self.target(selected_id),((0.,0.,0.,1.),)*4)
        fields=(('a_vertex',3),('a_normal',3),('a_color',4),('a_surface',1),
                ('a_material',4),('a_body',1),('a_finish',2))
        # The last material word drives different grain domains in the original
        # production function; all inputs and four raw coverage planes remain.
        for material in (1.,7.,20.):
            with self.subTest(material=material):
                data=np.array([(.02*x,.02*y,-.5,.12,.18,1.,.5,.3,.1,.5,1.,.47,.8,.65,material,0.,-1.,-1.)
                    for x,y in ((-1.,-1.),(1.,-1.),(-1.,.5))],np.float32)
                buffer=self.own(self.context.buffer(data.tobytes()))
                outputs={}
                for name,program in programs.items():
                    textures=[self.texture(size) for _ in range(2 if name=='reference' else 3)]
                    target=self.own(self.context.framebuffer(textures)); target.use(); target.clear()
                    self.context.viewport=(0,0,*size)
                    self.context.disable(moderngl.DEPTH_TEST|moderngl.BLEND|moderngl.CULL_FACE)
                    for matrix in ('u_modelMatrix','u_viewMatrix','u_projectionMatrix','u_normalMatrix','u_previewRotation'):
                        value=np.diag((50.,50.,1.,1.)).astype(np.float32) if matrix=='u_projectionMatrix' else np.eye(4,dtype=np.float32)
                        if matrix in program: program[matrix].write(value.tobytes())
                    for uniform,value in dict(u_environmentEnabled=1,u_lightingEnabled=1,u_orthographic=1,
                            u_viewDirection=(0.,0.,1.),u_surfaceDetail=.71,mpf_layerSize=size,
                            mpf_layerSamples=4,mpf_layerOrigin=(0,0),mpf_layerBase=0,mpf_layerCount=1,
                            mpf_layerPrevious=0,mpf_previousCursor=0,mpf_selectedDepth=1,mpf_selectedIdentity=2).items():
                        if uniform in program: program[uniform].value=value
                    cursor.use(0); selected_depth.use(1); selected_id.use(2)
                    layout=' '.join(f'{count}f' if field in program else f'{count*4}x' for field,count in fields)
                    active=[field for field,_ in fields if field in program]
                    vao=self.own(self.context.vertex_array(program,[(buffer,layout,*active)]))
                    vao.render(vertices=3)
                    outputs[name]=[self.planes(texture) for texture in textures]
                for actual,expected in zip(outputs['layer'][:2],outputs['reference'],strict=True):
                    np.testing.assert_array_equal(actual.view(np.uint32),expected.view(np.uint32))
                covered=outputs['reference'][0][...,3]==1.
                self.assertTrue(np.any(np.any(covered,axis=0)&~np.all(covered,axis=0)))
                np.testing.assert_array_equal(outputs['layer'][2][...,3]==1.,covered)
                # Reuse the final layer target with grain disabled to prove this
                # was a real derivative/grain path rather than strength zero.
                target.use(); target.clear(); program['u_surfaceDetail'].value=0.
                vao.render(vertices=3)
                smooth=self.planes(textures[1])
                self.assertTrue(np.any(outputs['layer'][1][...,:3][covered]!=smooth[...,:3][covered]))


if __name__=='__main__': unittest.main()
