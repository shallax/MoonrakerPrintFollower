"""Standalone EGL/native alpha-pair proof, isolated from Qt GLX fixtures.

The test runner gives each module its own process. Mesa EGL and Qt GLX must
not successively own the same test thread: releasing ModernGL resources does
not establish a portable EGL-to-GLX context handoff.
"""
import unittest

from mpf.toolhead.ToolheadTransparency import ToolheadTransparency


class OrdinaryTransparencyBlendGLTests(unittest.TestCase):
    def test_direct_blend_matches_original_rgba_for_every_alpha_pair(self):
        import ast
        import inspect
        import numpy as np
        try:
            import moderngl
        except ImportError:
            self.skipTest('The capture OpenGL runtime is required')
        from tools.capture_toolhead import create_context
        context, dll = create_context()
        resources = []
        try:
            width = height = 512
            rng = np.random.default_rng(7531)
            head = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
            front = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
            head[:, :, 3] = np.tile(np.arange(256, dtype=np.uint8), 1024).reshape(height, width)
            front[:, :, 3] = np.repeat(np.arange(256, dtype=np.uint8), 1024).reshape(height, width)
            for values in (head, front):
                values[:, :, :3] = np.rint(values[:, :, :3].astype(float) * values[:, :, 3:4] / 255).astype(np.uint8)
            tree = ast.parse(inspect.getsource(inspect.getmodule(ToolheadTransparency)))
            fragment = next(call.args[0].value for call in ast.walk(tree)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'setFragmentShader')
            vertex = next(call.args[0].value for call in ast.walk(tree)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'setVertexShader')
            reference = '''#version 410
uniform sampler2D u_head, u_front;
out vec4 frag_color;
void main(){ivec2 p=ivec2(gl_FragCoord.xy);vec4 h=texelFetch(u_head,p,0),f=texelFetch(u_front,p,0);
frag_color=vec4(h.rgb*(1.-f.a)+f.rgb*h.a,h.a);}'''
            # Dithering may deliberately perturb normalized integer storage.
            # This isolated context is destroyed below, so no host flag changes.
            context.disable_direct(0x0BD0)
            for dtype in ('f1','f4'):
                values = (head,front) if dtype=='f1' else tuple(v.astype(np.float32)/255 for v in (head,front))
                inputs=[]
                for value in values:
                    texture=context.texture((width,height),4,value.tobytes(),dtype=dtype)
                    resources.append(texture);inputs.append(texture)
                outputs=[]
                for source in (reference,fragment):
                    program=context.program(vertex_shader=vertex,fragment_shader=source)
                    resources.append(program);program['u_front']=1
                    if 'u_head' in program:program['u_head']=0
                    texture=context.texture((width,height),4,values[0].tobytes(),dtype=dtype);resources.append(texture)
                    target=context.framebuffer([texture]);resources.append(target)
                    target.use();context.viewport=(0,0,width,height)
                    context.disable(moderngl.DEPTH_TEST|moderngl.CULL_FACE|moderngl.BLEND)
                    for unit,texture in enumerate(inputs):texture.use(unit)
                    if source==fragment:
                        context.enable(moderngl.BLEND);context.blend_equation=moderngl.FUNC_ADD
                        context.blend_func=(moderngl.DST_ALPHA,moderngl.ONE_MINUS_SRC_ALPHA,moderngl.ZERO,moderngl.ONE)
                    vao=context.vertex_array(program,[]);resources.append(vao);vao.render(vertices=3)
                    outputs.append(np.frombuffer(target.read(components=4,alignment=1,dtype=dtype),
                        np.uint8 if dtype=='f1' else np.float32).reshape(height,width,4).copy())
                np.testing.assert_array_equal(outputs[0][...,3],outputs[1][...,3])
                np.testing.assert_array_equal(outputs[1][...,3],values[0][...,3])
                if dtype=='f1':
                    delta=np.abs(outputs[1][...,:3].astype(int)-outputs[0][...,:3].astype(int))
                    self.assertLessEqual(int(delta.max()),1,'RGBA8 RGB may differ by only one quantization step')
                    np.testing.assert_array_equal(outputs[1][head[...,3]==0],0)
                    print('Direct blend RGB8 max LSB:',int(delta.max()))
                else:
                    h,f=values;expected=h[...,:3].astype(float)*(1-f[...,3:4])+f[...,:3].astype(float)*h[...,3:4]
                    for output in outputs:np.testing.assert_allclose(output[...,:3],expected,rtol=0,atol=1e-6)
                    np.testing.assert_allclose(outputs[0],outputs[1],rtol=0,atol=1e-6)
                    print('Direct blend RGBA32F max:',float(np.max(np.abs(outputs[0]-outputs[1]))))
            # Odd dimensions and diagonal-crossing clips exercise shared-edge
            # ownership and the software driver's formerly missing corner blocks.
            shape=(19,13)
            front_texture=context.texture(shape,4,bytes((32,64,16,128))*(19*13))
            resources.append(front_texture)
            program=context.program(vertex_shader=vertex,fragment_shader=fragment)
            resources.append(program);program['u_front']=1
            vao=context.vertex_array(program,[]);resources.append(vao)
            for clip in ((0,0,7,5),(12,8,7,5),(3,2,13,9)):
                initial=np.full((13,19,4),(77,51,26,179),dtype=np.uint8)
                texture=context.texture(shape,4,initial.tobytes());resources.append(texture)
                target=context.framebuffer([texture]);resources.append(target)
                target.use();context.viewport=(0,0,*shape);context.scissor=None
                x,y,w,h=clip;program["u_clipEnabled"]=1;program["u_clipRect"]=(x,y,x+w,y+h)
                context.enable(moderngl.BLEND);context.blend_equation=moderngl.FUNC_ADD
                context.blend_func=(moderngl.DST_ALPHA,moderngl.ONE_MINUS_SRC_ALPHA,moderngl.ZERO,moderngl.ONE)
                front_texture.use(1);vao.render(vertices=3)
                actual=np.frombuffer(target.read(components=4,alignment=1),np.uint8).reshape(13,19,4)
                x,y,w,h=clip;outside=np.ones((13,19),bool);outside[y:y+h,x:x+w]=False
                np.testing.assert_array_equal(actual[outside],initial[outside])
                np.testing.assert_array_equal(actual[...,3],initial[...,3])
                expected=np.array((77,51,26))*(1-128/255)+np.array((32,64,16))*179/255
                self.assertLessEqual(float(np.abs(actual[y:y+h,x:x+w,:3]-expected).max()),1)
            context.scissor=None
        finally:
            for resource in reversed(resources): resource.release()
            context.release()
            if dll is not None: dll.close()
