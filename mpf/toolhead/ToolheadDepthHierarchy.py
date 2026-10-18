# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead

"""Conservative coarse cubes and full depth range atlases for reflection traversal."""
import ctypes

from .ToolheadCaptureBuffers import CaptureVertexArray
from .ToolheadGLState import GeometryUncertain, preserved_state, procedure

TILE_SIZE = 32
SIZE = 512//TILE_SIZE
UNIT = 5
SCRATCH_SIZE = 128
FULL_WIDTH = 512
FULL_HEIGHT = 768
FULL_SCRATCH_SIZE = 256
RETAINED_BYTES = (2*6*SIZE*SIZE*8 + SCRATCH_SIZE*SCRATCH_SIZE*8
                  + 2*FULL_WIDTH*FULL_HEIGHT*16 + 2*FULL_SCRATCH_SIZE**2*16)
_uncertain = []

VERTEX = '''#version 410
void main() {
    vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);
    gl_Position=vec4(p*2.-1.,0.,1.);
}'''
FRAGMENT = '''#version 410
uniform samplerCube sourceDepth;
uniform int face;
out vec2 rangeDepth;
vec3 direction(vec2 uv) {
    if(face==0)return vec3(1.,-uv.y,-uv.x);
    if(face==1)return vec3(-1.,-uv.y,uv.x);
    if(face==2)return vec3(uv.x,1.,uv.y);
    if(face==3)return vec3(uv.x,-1.,-uv.y);
    if(face==4)return vec3(uv.x,-uv.y,1.);
    return vec3(-uv.x,-uv.y,-1.);
}
void main() {
    vec2 pixel=floor(gl_FragCoord.xy)*32.;
    vec2 bounds=vec2(1.,0.);
    for(int y=0;y<32;y++)for(int x=0;x<32;x++) {
        vec2 uv=(pixel+vec2(x,y)+.5)/256.-1.;
        float d=textureLod(sourceDepth,direction(uv),0.).r;
        if(d>=0.&&d<1.)bounds=vec2(min(bounds.x,d),max(bounds.y,d));
    }
    rangeDepth=bounds;
}'''

# Keep the direct reducer above as an independent test oracle. Partitioning
# each 32x32 tile into 4x4 blocks gives the GPU substantially more parallel work.
FIRST_FRAGMENT = FRAGMENT.replace('*32.', '*4.').replace('y<32', 'y<4').replace('x<32', 'x<4')
SECOND_FRAGMENT = """#version 410
uniform sampler2D sourceRanges;
out vec2 rangeDepth;
void main() {
    ivec2 pixel=ivec2(gl_FragCoord.xy)*8;
    vec2 bounds=vec2(1.,0.);
    for(int y=0;y<8;y++)for(int x=0;x<8;x++) {
        vec2 block=texelFetch(sourceRanges,pixel+ivec2(x,y),0).rg;
        bounds=vec2(min(bounds.x,block.x),max(bounds.y,block.y));
    }
    rangeDepth=bounds;
}"""


# Canonical axis grids pair opposite depth faces before reducing all nine levels.
FULL_FIRST_FRAGMENT = """#version 410
uniform samplerCube sourceDepth;uniform int axis;out vec4 ranges;
void main(){ivec2 pixel=ivec2(gl_FragCoord.xy)*2;int u=axis+1;if(u==3)u=0;int v=u+1;if(v==3)v=0;
vec4 r=vec4(1.,0.,1.,0.);
for(int y=0;y<2;y++)for(int x=0;x<2;x++){
vec2 cell=vec2(pixel+ivec2(x,y));vec3 address=vec3(0.);address[axis]=1.;address[u]=(cell.x+.5)/256.-1.;address[v]=(cell.y+.5)/256.-1.;
float f=textureLod(sourceDepth,address,0.).r;address[axis]=-1.;float b=textureLod(sourceDepth,address,0.).r;
if(f>=0.&&f<1.)r.xy=vec2(min(r.x,f),max(r.y,f));if(b>=0.&&b<1.)r.zw=vec2(min(r.z,b),max(r.w,b));}
ranges=r;}"""
FULL_SECOND_FRAGMENT = """#version 410
uniform sampler2D sourceRanges;out vec4 ranges;
void main(){ivec2 pixel=ivec2(gl_FragCoord.xy)*2;vec4 r=vec4(1.,0.,1.,0.);
for(int y=0;y<2;y++)for(int x=0;x<2;x++){vec4 p=texelFetch(sourceRanges,pixel+ivec2(x,y),0);r=vec4(min(r.x,p.x),max(r.y,p.y),min(r.z,p.z),max(r.w,p.w));}ranges=r;}"""


class DepthHierarchy:
    """Range cubes and atlases follow the existing physical colour/depth pairs."""
    @classmethod
    def optional(cls, gl, context):
        # The original fine DDA remains available on legacy or unsupported GL.
        if context.format().majorVersion() < 4: return None
        owner = cls.__new__(cls)
        try:
            owner.__init__(gl, context)
            return owner
        except GeometryUncertain: raise
        except Exception:
            # __init__ verified its own cleanup and host-state restoration.
            return None

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if getattr(self, 'quarantined', False) or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Depth hierarchy requires its creating context')

    def _quarantine(self, error):
        self.quarantined = True
        if self not in _uncertain: _uncertain.append(self)
        raise GeometryUncertain(self, 'Depth hierarchy lifetime or host state is uncertain') from error

    def __init__(self, gl, context):
        self.gl = gl
        self.context = context
        self.retained_bytes = RETAINED_BYTES
        self.enabled = True
        self.names = []; self.fbo = self.scratch = 0
        self.program = self.second_program = self.vao = None
        self.full_names = []; self.full_scratch = []
        self.full_first_program = self.full_second_program = None
        self.full_ready = set()
        self._current()
        failure = None
        try:
            with preserved_state(gl, context, exact_context=True):
                try:
                    self._allocate(gl, context)
                    if gl.glGetError(): raise RuntimeError('Depth hierarchy allocation failed')
                except Exception as error:
                    # Consume allocation errors before checking cleanup itself.
                    for _ in range(16):
                        if not gl.glGetError(): break
                    self.close()
                    failure = error
            if gl.glGetError(): raise RuntimeError('Depth hierarchy host restoration failed')
        except Exception as error: self._quarantine(error)
        if failure is not None: raise failure

    def _allocate(self, gl, context):
        from .ToolheadCaptureGL import RawProgram
        from PyQt6.QtOpenGL import QOpenGLShader
        self.context = context
        self.names = []
        self.fbo = self.scratch = 0
        self.program = self.second_program = self.vao = None
        self.full_names = []; self.full_scratch = []
        self.full_first_program = self.full_second_program = None
        self.full_ready = set()
        U, I = ctypes.c_uint, ctypes.c_int
        try:
            body = (U*2)()
            procedure(context,'glGenTextures',None,I,ctypes.POINTER(U))(2,body)
            self.names = list(body)
            if not all(self.names): raise RuntimeError('Reflection depth range allocation failed')
            gl.glActiveTexture(0x84C0+UNIT)
            image = procedure(context,'glTexImage2D',None,U,I,I,I,I,I,U,U,ctypes.c_void_p)
            unpack = int(gl.glGetIntegerv(0x88EF))
            try:
                gl.glBindBuffer(0x88EC, 0)
                value=U()
                procedure(context,'glGenTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
                self.scratch=value.value
                if not self.scratch: raise RuntimeError('Reflection depth scratch allocation failed')
                gl.glBindTexture(0x0DE1,self.scratch)
                image(0x0DE1,0,0x8230,SCRATCH_SIZE,SCRATCH_SIZE,0,0x8227,0x1406,None)
                for key,value in ((0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F)):
                    gl.glTexParameteri(0x0DE1,key,value)
                for field, width, height in (('full_names', FULL_WIDTH, FULL_HEIGHT),
                                              ('full_scratch', FULL_SCRATCH_SIZE, FULL_SCRATCH_SIZE)):
                    textures = (U*2)()
                    procedure(context,'glGenTextures',None,I,ctypes.POINTER(U))(2,textures)
                    setattr(self, field, list(textures))
                    if not all(textures): raise RuntimeError('Full depth hierarchy allocation failed')
                    for name in textures:
                        gl.glBindTexture(0x0DE1,name)
                        image(0x0DE1,0,0x8814,width,height,0,0x1908,0x1406,None)
                        for key,value in ((0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F)):
                            gl.glTexParameteri(0x0DE1,key,value)
                for name in self.names:
                    gl.glBindTexture(0x8513,name)
                    for face in range(6): image(0x8515+face,0,0x8230,SIZE,SIZE,0,0x8227,0x1406,None)
                    for key,value in ((0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F),(0x8072,0x812F)):
                        gl.glTexParameteri(0x8513,key,value)
            finally:
                try:
                    self._current(); gl.glBindBuffer(0x88EC, unpack)
                except Exception as error: self._quarantine(error)
            value=U()
            procedure(context,'glGenFramebuffers',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
            self.fbo=value.value
            if not self.fbo: raise RuntimeError('Reflection depth range framebuffer unavailable')
            self.program=RawProgram()
            self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,VERTEX)
            self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,FIRST_FRAGMENT)
            self.program.link()
            self.second_program=RawProgram()
            self.second_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,VERTEX)
            self.second_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,SECOND_FRAGMENT)
            self.second_program.link()
            for field, fragment in (('full_first_program', FULL_FIRST_FRAGMENT),
                                    ('full_second_program', FULL_SECOND_FRAGMENT)):
                program = RawProgram()
                setattr(self, field, program)
                program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,VERTEX)
                program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment)
                program.link()
            self.vao=CaptureVertexArray(context);self.vao.create()
        except Exception:
            raise

    def full_target(self, target):
        """Return only a successfully built atlas belonging to this physical pair."""
        if target not in getattr(self, 'full_ready', ()) or getattr(self, 'quarantined', False):
            return 0
        return self.full_names[self.names.index(target)]

    def build(self, gl, source, target):
        self._current()
        if not self.enabled: return False
        if type(source) is not int or source <= 0 or target not in self.names:
            raise ValueError('Owned target and matching completed depth required')
        try:
            with preserved_state(gl, self.context, exact_context=True):
                try:
                    if hasattr(self, 'full_ready'): self.full_ready.discard(target)
                    self._build(gl, source, target)
                except Exception:
                    # Preserve already-published ranges for the other pair.
                    # Disable future reductions, but do not retire a main read.
                    gl.glFinish()
                    if gl.glGetError(): raise RuntimeError('Depth hierarchy failed build did not drain') from None
                    self.enabled = False
            if gl.glGetError(): raise RuntimeError('Depth hierarchy host restoration failed')
            return self.enabled
        except Exception as error: self._quarantine(error)

    def _build(self, gl, source, target):
        U,I=ctypes.c_uint,ctypes.c_int
        bind=procedure(self.context,'glBindFramebuffer',None,U,U)
        attach=procedure(self.context,'glFramebufferTexture2D',None,U,U,U,U,I)
        status=procedure(self.context,'glCheckFramebufferStatus',U,U)
        draw=procedure(self.context,'glDrawArrays',None,U,I,I)
        gl.glActiveTexture(0x84C0+UNIT)
        procedure(self.context,'glBindSampler',None,U,U)(UNIT,0)
        gl.glBindTexture(0x8513,source)
        gl.glBindTexture(0x0DE1,self.scratch)
        bind(0x8D40,self.fbo)
        gl.glViewport(0,0,SIZE,SIZE)
        for flag in (0x0B71,0x0BE2,0x0B44,0x0C11):gl.glDisable(flag)
        gl.glColorMask(True,True,True,True)
        procedure(self.context,'glDrawBuffer',None,U)(0x8CE0)
        self.vao.bind()
        for face in range(6):
            attach(0x8D40,0x8CE0,0x0DE1,self.scratch,0)
            if status(0x8D40)!=0x8CD5:raise RuntimeError('Reflection depth scratch target incomplete')
            gl.glViewport(0,0,SCRATCH_SIZE,SCRATCH_SIZE)
            self.program.bind()
            self.program.setUniformValue(self.program.uniformLocation('sourceDepth'),UNIT)
            self.program.setUniformValue(self.program.uniformLocation('face'),face)
            draw(0x0004,0,3)
            # Replace the draw attachment before sampling scratch. Ordered GL
            # draws make these writes visible without a CPU wait or readback.
            attach(0x8D40,0x8CE0,0x8515+face,target,0)
            if status(0x8D40)!=0x8CD5:raise RuntimeError('Reflection depth range target incomplete')
            gl.glViewport(0,0,SIZE,SIZE)
            self.second_program.bind()
            self.second_program.setUniformValue(self.second_program.uniformLocation('sourceRanges'),UNIT)
            draw(0x0004,0,3)
        self.second_program.release()
        self._build_full(gl, source, target)
        procedure(self.context,'glBindVertexArray',None,U)(0)
        if gl.glGetError():raise RuntimeError('Reflection depth range reduction failed')

    def _build_full(self, gl, source, target):
        U,I=ctypes.c_uint,ctypes.c_int
        attach=procedure(self.context,'glFramebufferTexture2D',None,U,U,U,U,I)
        status=procedure(self.context,'glCheckFramebufferStatus',U,U)
        draw=procedure(self.context,'glDrawArrays',None,U,I,I)
        copy=procedure(self.context,'glCopyTexSubImage2D',None,U,I,I,I,I,I,I,I)
        atlas=self.full_names[self.names.index(target)]
        attach(0x8D40,0x8CE0,0x0DE1,atlas,0)
        if status(0x8D40)!=0x8CD5: raise RuntimeError('Full depth atlas incomplete')
        gl.glClearColor(1.,0.,1.,0.)
        gl.glClear(0x4000)
        procedure(self.context,'glReadBuffer',None,U)(0x8CE0)
        gl.glBindTexture(0x8513,source)
        for axis in range(3):
            for level in range(1,10):
                side=512>>level
                slot=(level-1)%2
                attach(0x8D40,0x8CE0,0x0DE1,self.full_scratch[slot],0)
                if status(0x8D40)!=0x8CD5: raise RuntimeError('Full depth scratch incomplete')
                gl.glViewport(0,0,side,side)
                if level==1:
                    program=self.full_first_program
                    program.bind()
                    program.setUniformValue(program.uniformLocation('sourceDepth'),UNIT)
                    program.setUniformValue(program.uniformLocation('axis'),axis)
                else:
                    gl.glBindTexture(0x0DE1,self.full_scratch[1-slot])
                    program=self.full_second_program
                    program.bind()
                    program.setUniformValue(program.uniformLocation('sourceRanges'),UNIT)
                draw(0x0004,0,3)
                # Copy only from the separate draw attachment; the published
                # atlas is never sampled or attached while it is populated.
                gl.glBindTexture(0x0DE1,atlas)
                copy(0x0DE1,0,512-(512>>(level-1)),axis*256,0,0,side,side)
        program.release()
        if gl.glGetError(): raise RuntimeError('Full depth hierarchy reduction failed')
        self.full_ready.add(target)

    def close(self):
        U,I=ctypes.c_uint,ctypes.c_int
        try:
            self._current()
            if self.vao is not None:
                self.vao.close()
                if self.gl.glGetError(): raise RuntimeError('Depth hierarchy VAO retirement failed')
                self.vao=None
            for field in ('full_second_program', 'full_first_program'):
                program=getattr(self, field, None)
                if program is not None:
                    program.close()
                    if self.gl.glGetError(): raise RuntimeError('Full depth program retirement failed')
                    setattr(self, field, None)
            if self.second_program is not None:
                self.second_program.close()
                if self.gl.glGetError(): raise RuntimeError('Depth hierarchy second program retirement failed')
                self.second_program=None
            if self.program is not None:
                self.program.close()
                if self.gl.glGetError(): raise RuntimeError('Depth hierarchy program retirement failed')
                self.program=None
            if self.fbo:
                value=U(self.fbo)
                procedure(self.context,'glDeleteFramebuffers',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
                if self.gl.glGetError(): raise RuntimeError('Depth hierarchy FBO retirement failed')
                self.fbo=0
            if self.scratch:
                value=U(self.scratch)
                procedure(self.context,'glDeleteTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
                if self.gl.glGetError(): raise RuntimeError('Depth hierarchy scratch retirement failed')
                self.scratch=0
            if self.names:
                body=(U*len(self.names))(*self.names)
                procedure(self.context,'glDeleteTextures',None,I,ctypes.POINTER(U))(len(self.names),body)
                if self.gl.glGetError(): raise RuntimeError('Depth hierarchy texture retirement failed')
                self.names=[]
            for field in ('full_scratch', 'full_names'):
                names=getattr(self, field, [])
                if names:
                    body=(U*len(names))(*names)
                    procedure(self.context,'glDeleteTextures',None,I,ctypes.POINTER(U))(len(names),body)
                    if self.gl.glGetError(): raise RuntimeError('Full depth texture retirement failed')
                    setattr(self, field, [])
            self.full_ready=set()
            self.retained_bytes = 0
        except Exception as error: self._quarantine(error)
