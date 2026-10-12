"""Real GL parity of freshly rendered crops backed by recycled private FBOs."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject)
from tests import test_toolhead_environment_gl as fixture
from mpf.toolhead.ToolheadFrameCache import ToolheadFrameCache
from mpf.toolhead.ToolheadGLState import procedure


class FramePoolGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.EnvironmentGLTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass.__func__(cls)

    def test_alternating_crops_reuse_names_with_exact_fresh_colour_and_depth(self):
        self._alternating_crops(0)

    def test_multisample_pairs_preserve_sample_positions_resolved_colour_and_depth(self):
        self._alternating_crops(4)

    def test_additive_composition_rectangle_matches_full_quad_exactly(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        host=QOpenGLFramebufferObject(128,96,fmt)
        matrix=lambda data:SimpleNamespace(getData=lambda:data)
        identity=matrix(np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:identity,getProjectionMatrix=lambda:identity,
            getWorldPosition=lambda:(0,0,0),getCameraLightPosition=lambda:(0,1,0))
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        cache=ToolheadFrameCache(depth_only=True)
        def render(_camera):
            gl.glEnable(0x0C11);gl.glScissor(20,20,20,12)
            gl.glClearColor(.17,.31,.43,0.);gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        def deliver(rect,enabled):
            host.bind();gl.glViewport(20,10,80,60);gl.glDisable(0x0C11)
            gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
            gl.glClearColor(.11,.23,.37,.61);gl.glClearDepth(.73)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
            gl.glScissor(45,25,30,25)
            (gl.glEnable if enabled else gl.glDisable)(0x0C11)
            cache.draw(gl,camera,None,identity,'same',render,additive=True,composition_rect=rect)
            self.assertEqual(tuple(gl.glGetIntegerv(0x0C10)),(45,25,30,25))
            self.assertEqual(bool(gl.glIsEnabled(0x0C11)),enabled)
            host.bind()
            colour=(ctypes.c_ubyte*(128*96*4))();depth=(ctypes.c_float*(128*96))()
            read(0,0,128,96,0x1908,0x1401,colour);read(0,0,128,96,0x1902,0x1406,depth)
            return bytes(colour),bytes(depth)
        try:
            with patch.dict('sys.modules',{'UM.Math.Matrix':SimpleNamespace(Matrix=matrix)}):
                for enabled in (False,True):
                    self.assertEqual(deliver((0,0,80,60),enabled),deliver((16,16,32,24),enabled))
                self.assertEqual(cache._size[:2],(80,60))
                self.assertEqual(int(gl.glGetError()),0)
        finally:
            host.bind();cache.release_targets();gl.glDisable(0x0C11)

    def test_hemisphere_scissor_preserves_actual_light_shader_rgb(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        import configparser
        from pathlib import Path
        from PyQt6.QtGui import QVector3D
        from mpf.toolhead.ToolheadSurfaceCache import light_scissor
        gl=self.gl; width,height=256,192
        software='Apple Software Renderer' in str(gl.glGetString(0x1F01))
        fmt=QOpenGLFramebufferObjectFormat();fmt.setInternalTextureFormat(0x8814)
        hosts=(QOpenGLFramebufferObject(width,height),QOpenGLFramebufferObject(width,height,fmt))
        dither=bool(gl.glIsEnabled(0x0BD0));gl.glDisable(0x0BD0)
        maxima=[0,0.];illuminated=False
        config=configparser.ConfigParser(interpolation=None)
        config.read(Path(__file__).parents[1]/'mpf/toolhead/scene-lighting.shader')
        source='#version 410\n'+config['shaders']['fragment41core']
        if software:
            source=source.replace('void main()', 'uniform float plane;\nvoid main()').replace(
                'lightSurface(f_vertex, f_normal, f_color.rgb)',
                'lightSurface(vec3(gl_FragCoord.xy/vec2(256,192)*2.-1.,plane),vec3(0,0,1),vec3(.5,.6,.7))')
        program=QOpenGLShaderProgram();vao=QOpenGLVertexArrayObject()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float plane;out vec3 f_vertex;out vec3 f_normal;out vec4 f_color;'
            'void main(){vec2 p=gl_VertexID==0?vec2(-1,-1):gl_VertexID==1?vec2(3,-1):vec2(-1,3);'
            'gl_Position=vec4(p,0,1);f_vertex=vec3(p,plane);f_normal=vec3(0,0,1);f_color=vec4(.5,.6,.7,1);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,source))
        self.assertTrue(program.link());self.assertTrue(vao.create())
        matrix=SimpleNamespace(getData=lambda:np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:matrix,getProjectionMatrix=lambda:matrix)
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        from mpf.toolhead.ToolheadGLState import preserved_state
        try:
            with preserved_state(gl,self.context):
                hosts[0].bind();gl.glViewport(0,0,width,height);gl.glDisable(gl.GL_DEPTH_TEST)
                gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE);gl.glColorMask(True,True,True,True)
                program.bind();vao.bind()
                for name,value in (('u_depthOnly',0),('u_attachedCount',1),('u_orthographic',1)):
                    program.setUniformValue(name,value)
                program.setUniformValue('u_lightOpacity',1.)
                program.setUniformValue('u_viewDirection',QVector3D(0,0,1))
                program.setUniformValue('u_attachedColour[0]',QVector3D(1,.8,.6))
                program.setUniformValue('u_attachedRange[0]',.65)
                for position,direction in (((.1,.2,.2),(0,-1,0)),((-.2,.1,.2),(.6,-.8,0)),((.3,-.2,.2),(0,0,-1))):
                    program.setUniformValue('u_attachedPosition[0]',QVector3D(*position))
                    program.setUniformValue('u_attachedDirection[0]',QVector3D(*direction))
                    rect=light_scissor(camera,[(position,.65)],width,height,directions=[direction])
                    self.assertLess(rect[2]*rect[3],width*height)
                    for plane in (-.3,0.,.19):
                        program.setUniformValue('plane',plane)
                        for index,host in enumerate(hosts):
                            host.bind();images=[]
                            dtype=np.uint8 if index==0 else np.float32
                            values=np.empty((height,width,4),dtype=dtype)
                            for cropped in (False,True):
                                gl.glViewport(0,0,width,height);gl.glDisable(gl.GL_SCISSOR_TEST);gl.glClearColor(0,0,0,0);gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                                if cropped:
                                    if software:gl.glViewport(*rect)
                                    else:gl.glEnable(gl.GL_SCISSOR_TEST);gl.glScissor(*rect)
                                gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                                read(0,0,width,height,0x1908,0x1401 if index==0 else 0x1406,values.ctypes.data)
                                images.append(values.copy())
                            outside=np.ones((height,width),bool);x,y,w,h=rect;outside[y:y+h,x:x+w]=False
                            # Additive composition ignores alpha; the unclipped shader writes
                            # geometry alpha even where its illumination is exactly zero.
                            np.testing.assert_array_equal(images[0][~outside,3],images[1][~outside,3])
                            np.testing.assert_array_equal(images[1][outside,3],0)
                            np.testing.assert_array_equal(images[0][outside,:3],0)
                            np.testing.assert_array_equal(images[1][outside,:3],0)
                            delta=np.abs(images[0][...,:3].astype(float)-images[1][...,:3].astype(float))
                            maxima[index]=max(maxima[index],float(delta.max()))
                            self.assertTrue(np.isfinite(images).all())
                            if index==0:self.assertLessEqual(float(delta.max()),1)
                            else:
                                illuminated=illuminated or bool(np.any(images[0][...,:3]>0))
                                np.testing.assert_array_equal(np.any(images[0][...,:3]>0,axis=-1),np.any(images[1][...,:3]>0,axis=-1))
                                self.assertLessEqual(float(delta.max()),1e-6)
                vao.release();program.release()
                self.assertEqual(int(gl.glGetError()),0)
            self.assertTrue(illuminated,'Hemisphere proof must contain illuminated pixels')
            print('Hemisphere RGB8 max LSB / RGB32F max:',maxima)
        finally:
            (gl.glEnable if dither else gl.glDisable)(0x0BD0)
            vao.destroy();program.removeAllShaders()

    def test_native_projection_output_matches_full_nested_surface_colour_and_depth(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache, surface_fragment
        from mpf.toolhead.ToolheadFrameCache import lighting_region
        from mpf.toolhead.ToolheadGLState import preserved_state
        gl=self.gl;width,height=1024,768
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        host=QOpenGLFramebufferObject(1040,800,fmt)
        programs=[]
        for fragment in (surface_fragment(),
                '#version 410\nuniform sampler2D ptex;uniform sampler2D ctex;uniform vec2 origin;out vec4 colour;'
                'void main(){ivec2 q=ivec2(gl_FragCoord.xy-origin);vec4 p=texelFetch(ptex,q,0);'
                'gl_FragDepth=p.w;colour=vec4(texelFetch(ctex,q,0).rgb*.125,0);}'):
            program=QOpenGLShaderProgram()
            vertex=('#version 410\nout vec3 f_vertex;out vec3 f_normal;out vec4 f_color;'
                'void main(){vec2 p=gl_VertexID==0?vec2(-1,-1):gl_VertexID==1?vec2(3,-1):vec2(-1,3);'
                'f_vertex=vec3(p*123.,.2);f_normal=vec3(.2,.3,1);f_color=vec4(p*.12+.4,.7,1);'
                'gl_Position=vec4(p,.3+.1*p.x,1);}')
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,vertex))
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment))
            self.assertTrue(program.link());programs.append(program)
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        identity=SimpleNamespace(getData=lambda:np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:identity,getProjectionMatrix=lambda:identity,
            getWorldPosition=lambda:(0,0,0),getCameraLightPosition=lambda:(0,1,0))
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        cache=ToolheadFrameCache(depth_only=True);surface=ToolheadSurfaceCache(depth_only=True)
        def capture():
            gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LESS);gl.glDisable(gl.GL_CULL_FACE)
            programs[0].bind();programs[0].setUniformValue('u_surfaceKind',2)
            vao.bind();gl.glDrawArrays(gl.GL_TRIANGLES,0,3);vao.release();programs[0].release()
        def deliver(rect,crop):
            host.bind();gl.glViewport(8,12,width,height);gl.glDisable(gl.GL_SCISSOR_TEST)
            gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
            gl.glClearColor(.1,.2,.3,.4);gl.glClearDepth(.8);gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
            def render(delivered_camera):
                self.assertIs(delivered_camera,camera)
                surface.prepare(gl,'same',(),capture,lambda *_:None,rect=rect)
                surface.copy_depth(gl,rect=rect)
                ox,oy,_,_=gl.glGetIntegerv(0x0BA2);sx,sy,_,_=surface._region
                gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LEQUAL);gl.glDepthMask(False)
                gl.glDisable(gl.GL_BLEND);gl.glEnable(gl.GL_SCISSOR_TEST)
                gl.glScissor(int(ox)+rect[0],int(oy)+rect[1],rect[2],rect[3])
                if cache._software_scissor:
                    gl.glDisable(gl.GL_SCISSOR_TEST);gl.glViewport(int(ox)+rect[0],int(oy)+rect[1],rect[2],rect[3])
                programs[1].bind();programs[1].setUniformValue('origin',float(ox+sx),float(oy+sy))
                for unit,index,name in ((0,0,'ptex'),(1,2,'ctex')):
                    gl.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x0DE1,surface._fbo.textures()[index])
                    programs[1].setUniformValue(name,unit)
                vao.bind();gl.glDrawArrays(gl.GL_TRIANGLES,0,3);vao.release();programs[1].release()
                for unit in (0,1):gl.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x0DE1,0)
                gl.glActiveTexture(0x84C0);gl.glDisable(gl.GL_SCISSOR_TEST)
                gl.glViewport(int(ox),int(oy),width,height)
            cache.draw(gl,camera,None,identity,object(),render,additive=True,composition_rect=rect,
                _crop=crop,preserve_projection=True)
            self.assertEqual(tuple(gl.glGetIntegerv(0x0BA2)),(8,12,width,height))
            host.bind();rgba=(ctypes.c_ubyte*(1040*800*4))();depth=(ctypes.c_float*(1040*800))()
            read(0,0,1040,800,0x1908,0x1401,rgba);read(0,0,1040,800,0x1902,0x1406,depth)
            return bytes(rgba),bytes(depth)
        try:
            with preserved_state(gl,self.context),patch.dict('sys.modules',{'UM.Math.Matrix':SimpleNamespace(Matrix=lambda data:SimpleNamespace(getData=lambda:data))}):
                previous=None
                for rect in ((200,240,180,150),(240,250,180,150),(640,250,180,150)):
                    full=deliver(rect,(0,0,width,height));previous=lighting_region(rect,width,height,previous)
                    self.assertEqual(deliver(rect,previous),full)
                    self.assertLess(cache._size[0]*cache._size[1],width*height//2)
                    self.assertEqual(cache._pool_bytes,0)
                self.assertEqual(int(gl.glGetError()),0)
        finally:
            cache.release_targets();vao.destroy()
            for program in programs:program.removeAllShaders()

    def test_software_composition_keeps_asymmetric_native_pixels_and_depth(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        with patch.object(self.gl,'glGetString',return_value='Apple Software Renderer'):
            self.test_native_projection_output_matches_full_nested_surface_colour_and_depth()
            self.test_additive_composition_rectangle_matches_full_quad_exactly()

    def test_surface_region_preserves_full_resolution_attributes_and_depth_on_escape(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache, surface_fragment
        import configparser
        from pathlib import Path
        from PyQt6.QtGui import QVector3D
        gl = self.gl
        software='Apple Software Renderer' in str(gl.glGetString(0x1F01))
        dither=bool(gl.glIsEnabled(0x0BD0));gl.glDisable(0x0BD0)
        config=configparser.ConfigParser(interpolation=None)
        config.read(Path(__file__).parents[1]/'mpf/toolhead/scene-lighting.shader')
        light_source=config['shaders']['fragment41core'].split('void main()')[0]
        light=QOpenGLShaderProgram()
        self.assertTrue(light.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);}'))
        self.assertTrue(light.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\n'+light_source+\
            'uniform sampler2D posTex,normTex,colTex;uniform vec2 origin;'
            'void main(){ivec2 q=ivec2(gl_FragCoord.xy-origin);vec4 p=texelFetch(posTex,q,0),n=texelFetch(normTex,q,0),c=texelFetch(colTex,q,0);'
            'if(n.w<.5)discard;frag_color=vec4(lightSurface(p.xyz,n.xyz,c.rgb),c.a);}'))
        self.assertTrue(light.link())
        diagnostics=[];final_max=0;half_max=0
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        host = QOpenGLFramebufferObject(1040,800,fmt)
        full, cropped = ToolheadSurfaceCache(depth_only=True), ToolheadSurfaceCache(depth_only=True)
        program = QOpenGLShaderProgram()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float phase;uniform int exactFixture;out vec3 f_vertex;out vec3 f_normal;out vec4 f_color;'
            'void main(){vec2 p=gl_VertexID==0?vec2(-1.,-1.):gl_VertexID==1?vec2(3.,-1.):vec2(-1.,3.);'
            'f_vertex=vec3(p*(exactFixture==1?vec2(128,96):vec2(123)),phase);f_normal=exactFixture==1?vec3(0,0,1):vec3(.2,.3,1.);'
            'f_color=exactFixture==1?vec4(p*vec2(.125,.09375)+.5,.75,1):vec4(p*.12+.4,.7,1.);'
            'gl_Position=vec4(p, .5-phase*.1,1.);}'))
        fragment=surface_fragment()
        if software:
            # Exact pixel mapping/storage proof, independent of this driver's
            # native varying interpolation. The arbitrary-gradient leg stays native.
            fragment=fragment.replace('void main()', 'uniform int exactFixture;uniform float phase;uniform vec2 fixtureOrigin;\nvoid main()')
            fragment=fragment.replace('surface_colour = f_color;', '''surface_colour = f_color;
    if (exactFixture == 1) {
        vec2 pixel=gl_FragCoord.xy+fixtureOrigin;
        surface_position=vec4(pixel*.25-vec2(128,96),phase,gl_FragCoord.z);
        surface_normal=vec4(0,0,1,float(u_surfaceKind));
        surface_colour=vec4(pixel/4096.+vec2(.375,.40625),.75,1);
    }''')
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment))
        self.assertTrue(program.link())
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        read_buffer=procedure(self.context,'glReadBuffer',None,ctypes.c_uint)
        calls={id(full):0,id(cropped):0}
        def draw(owner, phase):
            calls[id(owner)]+=1
            gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LESS);gl.glDisable(gl.GL_CULL_FACE)
            program.bind();program.setUniformValue('exactFixture',int(dyadic));program.setUniformValue('phase',float(phase));program.setUniformValue('u_surfaceKind',2)
            if software:program.setUniformValue('fixtureOrigin',float(owner._region[0]),float(owner._region[1]))
            vao.bind();gl.glDrawArrays(gl.GL_TRIANGLES,0,3);vao.release();program.release()
        def image(owner,rect,attachment):
            x,y,w,h=rect;rx,ry,_,_=owner._region
            owner._fbo.bind()
            channels=1 if attachment is None else 4
            values=np.empty((h,w,channels),dtype=np.float32)
            if attachment is not None: read_buffer(0x8CE0+attachment)
            read(x-rx,y-ry,w,h,0x1902 if attachment is None else 0x1908,0x1406,values.ctypes.data)
            return values
        def lit(owner,rect):
            host.bind();gl.glViewport(8,12,1024,768);gl.glDisable(0x0C11)
            gl.glDisable(gl.GL_DEPTH_TEST);gl.glDisable(gl.GL_BLEND);gl.glColorMask(True,True,True,True)
            gl.glClearColor(0,0,0,0);gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            light.bind();light.setUniformValue('u_attachedCount',1);light.setUniformValue('u_orthographic',1)
            light.setUniformValue('u_lightOpacity',1.);light.setUniformValue('u_viewDirection',QVector3D(0,0,1))
            light.setUniformValue('u_attachedPosition[0]',QVector3D(0,0,40))
            light.setUniformValue('u_attachedDirection[0]',QVector3D(0,0,-1))
            light.setUniformValue('u_attachedColour[0]',QVector3D(.1,.12,.14));light.setUniformValue('u_attachedRange[0]',400.)
            rx,ry,_,_=owner._region;light.setUniformValue('origin',float(rx+8),float(ry+12))
            for unit,name in enumerate(('posTex','normTex','colTex')):
                gl.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x0DE1,owner._fbo.textures()[unit]);light.setUniformValue(name,unit)
            x,y,w,h=rect;gl.glEnable(0x0C11);gl.glScissor(x+8,y+12,w,h)
            if 'Apple Software Renderer' in str(gl.glGetString(0x1F01)):
                gl.glDisable(0x0C11);gl.glViewport(x+8,y+12,w,h)
            vao.bind();gl.glDrawArrays(4,0,3);vao.release();light.release()
            result=np.empty((h,w,4),np.uint8);read(x+8,y+12,w,h,0x1908,0x1401,result.ctypes.data)
            for unit in range(3):gl.glActiveTexture(0x84C0+unit);gl.glBindTexture(0x0DE1,0)
            gl.glActiveTexture(0x84C0);gl.glDisable(0x0C11)
            return result
        try:
            for dyadic in (False,True):
                for phase,rect in enumerate(((350,300,80,90),(370,310,80,90),(800,550,100,100),(50,40,100,100))):
                    for owner in (full,cropped):
                        host.bind();gl.glViewport(8,12,1024,768)
                        owner.prepare(gl,('camera',dyadic),(phase,),lambda owner=owner,phase=phase:draw(owner,phase),
                            lambda _old,_new,owner=owner,phase=phase:draw(owner,phase),rect=rect if owner is cropped else None)
                        self.assertEqual(tuple(map(int,gl.glGetIntegerv(0x0BA2))),(8,12,1024,768))
                    if phase==0:self.assertLess(cropped._size[0]*cropped._size[1],1024*768//2)
                    for attachment in (0,1,2,None):
                        a,b=image(full,rect,attachment),image(cropped,rect,attachment)
                        self.assertTrue(np.isfinite(a).all() and np.isfinite(b).all())
                        if dyadic or attachment is None:np.testing.assert_array_equal(a,b)
                        elif attachment==0:
                            np.testing.assert_array_equal(a[...,2:],b[...,2:])
                            # Explicit empirical acceptance: <1/64 pixel in this affine
                            # world gradient. GL_SUBPIXEL_BITS does not specify varying precision.
                            error=np.abs(a[...,:2]-b[...,:2]);bounds=np.array([246/1024,246/768])/64
                            diagnostics.append((float(error.max()),float(np.sqrt(np.mean(error**2)))))
                            self.assertTrue(np.all(error<=bounds),f'position max/RMS={diagnostics[-1]}, bounds={bounds}')
                        else:
                            np.testing.assert_array_equal(a[...,3],b[...,3])
                            steps=np.abs(a.astype(np.float16).view(np.uint16).astype(int)-b.astype(np.float16).view(np.uint16).astype(int))
                            half_max=max(half_max,int(steps.max()))
                            self.assertLessEqual(int(steps.max()),1,'At most one FP16 storage step per attribute')
                    first,second=lit(full,rect),lit(cropped,rect)
                    self.assertTrue(np.any(first[...,:3]>0),f'Production lighting proof must contain illuminated pixels: dyadic={dyadic}, phase={phase}, rect={rect}')
                    np.testing.assert_array_equal(first[...,3],second[...,3])
                    final_max=max(final_max,int(np.abs(first[...,:3].astype(int)-second[...,:3].astype(int)).max()))
                    self.assertLessEqual(final_max,1)
                    # Translation of the cropped source must preserve host depth.
                    host.bind();gl.glViewport(8,12,1024,768);gl.glDisable(0x0C11);gl.glDepthMask(True)
                    gl.glClearDepth(1.);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                    cropped.copy_depth(gl,rect=rect)
                    x,y,w,h=rect;actual=np.empty((h,w,1),dtype=np.float32)
                    read(x+8,y+12,w,h,0x1902,0x1406,actual.ctypes.data)
                    np.testing.assert_array_equal(actual,image(full,rect,None))
            self.assertEqual(calls[id(full)],8)
            self.assertEqual(calls[id(cropped)],8)
            self.assertEqual(int(gl.glGetError()),0)
            print('Surface arbitrary-gradient world max/RMS:',diagnostics)
            print('Surface half-attribute max steps / final light RGB8 max LSB:',half_max,final_max)
        finally:
            (gl.glEnable if dither else gl.glDisable)(0x0BD0)
            light.removeAllShaders()
            full._fbo=cropped._fbo=None;vao.destroy();program.removeAllShaders();host.release()

    def test_surface_depth_region_matches_full_copy_with_offset_viewport(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        source=QOpenGLFramebufferObject(64,48,fmt);host=QOpenGLFramebufferObject(128,96,fmt)
        cache=ToolheadSurfaceCache(depth_only=True);cache._fbo=source
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        source.bind();gl.glDisable(0x0C11);gl.glDepthMask(True);gl.glClearDepth(.23);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        gl.glEnable(0x0C11);gl.glScissor(20,20,10,10);gl.glClearDepth(.67);gl.glClear(gl.GL_DEPTH_BUFFER_BIT);gl.glDisable(0x0C11)
        def deliver(rect):
            host.bind();gl.glViewport(13,17,64,48);gl.glClearDepth(1.);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
            cache.copy_depth(gl,rect=rect)
            data=(ctypes.c_float*(128*96))();read(0,0,128,96,0x1902,0x1406,data)
            return np.ctypeslib.as_array(data).reshape(96,128).copy()
        full=deliver(None);clipped=deliver((16,16,24,20))
        np.testing.assert_array_equal(full[33:53,29:53],clipped[33:53,29:53])
        outside=np.ones((96,128),bool);outside[33:53,29:53]=False
        self.assertTrue(np.all(clipped[outside]==1.))
        self.assertEqual(int(gl.glGetError()),0)
        host.bind();cache._fbo=None

    def _alternating_crops(self,samples):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil if samples else QOpenGLFramebufferObject.Attachment.Depth)
        fmt.setSamples(samples)
        host=QOpenGLFramebufferObject(128,96,fmt)
        if samples and host.format().samples()!=samples:self.skipTest("Requested host multisampling unavailable")
        program=QOpenGLShaderProgram()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float phase;void main(){vec2 p=gl_VertexID==0?vec2(-.9,-.7):gl_VertexID==1?vec2(.8,-.6):vec2(-.2,.8);gl_Position=vec4(p+vec2(phase*.03,0.),phase*.05,1.);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nuniform float phase;out vec4 colour;void main(){colour=vec4(gl_FragCoord.xy/128.,.1+phase*.07,1.);}'))
        self.assertTrue(program.link())
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        matrix=lambda data:SimpleNamespace(getData=lambda:data)
        identity=matrix(np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:identity,
            getProjectionMatrix=lambda:identity,getWorldPosition=lambda:(0,0,0),getCameraLightPosition=lambda:(0,1,0))
        cache=ToolheadFrameCache(depth_only=True);handles={};reference=None
        sample_position=procedure(self.context,'glGetMultisamplefv',None,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p) if samples else None
        try:
            with patch.dict('sys.modules',{'UM.Math.Matrix':SimpleNamespace(Matrix=matrix)}):
                for phase,crop in enumerate(((0,0,64,48),(16,8,96,64),(32,16,64,48),(0,0,96,64),(0,0,64,48))):
                    def render(_camera, phase=phase):
                        gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
                        gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LEQUAL)
                        program.bind();program.setUniformValue('phase',float(phase));vao.bind()
                        try:gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                        finally:vao.release();program.release()
                    def deliver(owner, phase=phase, crop=crop, render=render):
                        host.bind();gl.glViewport(0,0,128,96);gl.glClearDepth(1.)
                        owner.draw(gl,camera,None,identity,phase,render,_crop=crop,_compose=False)
                        owner._fbo.bind();w,h=crop[2:]
                        positions=[]
                        if samples:
                            for index in range(samples):
                                position=(ctypes.c_float*2)();sample_position(0x8E50,index,position)
                                positions.append(tuple(position))
                        colour=(ctypes.c_ubyte*(w*h*4))();depth=(ctypes.c_float*(w*h))()
                        (owner._resolved or owner._fbo).bind()
                        read(0,0,w,h,0x1908,0x1401,colour)
                        if samples:
                            depth_format=QOpenGLFramebufferObjectFormat()
                            depth_format.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                            depth_resolve=QOpenGLFramebufferObject(w,h,depth_format)
                            QOpenGLFramebufferObject.blitFramebuffer(depth_resolve,owner._fbo,buffers=gl.GL_DEPTH_BUFFER_BIT)
                            depth_resolve.bind()
                        else:owner._fbo.bind()
                        read(0,0,w,h,0x1902,0x1406,depth)
                        return bytes(colour),bytes(depth),positions

                    actual=deliver(cache)
                    handle=(cache._fbo.handle(),cache._resolved.handle() if cache._resolved else None)
                    if crop[2:] in handles:self.assertEqual(handle,handles[crop[2:]])
                    else:handles[crop[2:]]=handle
                    reference=ToolheadFrameCache(depth_only=True)
                    self.assertEqual(actual,deliver(reference))
                    reference.release_targets();reference=None
                self.assertEqual(cache._pool_hits,3)
                self.assertEqual(len(cache._pool),1)
                self.assertEqual(int(gl.glGetError()),0)
        finally:
            host.bind();cache.release_targets()
            if reference is not None:reference.release_targets()
            vao.destroy();program.removeAllShaders()
