"""Unwired exact-context layer coordinator; no final-image or AA shortcut.

The caller freezes the complete camera/pose/material/seed/draw identity in key,
retains all scene leases in existing_bytes, and supplies the original draws.
Queries hold an actual matching completed-cohort ticket, not just a value key.
"""
import ctypes
from contextlib import contextmanager, nullcontext
from itertools import count
from weakref import ref

import numpy as np

from .ToolheadEnvironmentLayers import LayerBuilder, METADATA_RESERVE, CHUNK_METADATA
from .ToolheadEnvironmentPaths import _generation, MAX_ID
from .ToolheadEnvironmentGeometry import GeometryUncertain
from .ToolheadGLState import preserved_state, preserved_samples, procedure, sample_depth_certificate

U, I, P = ctypes.c_uint, ctypes.c_int, ctypes.c_void_p
READ_BYTES = 1024*1024
_requests = count(1)
_uncertain = []


class _SampleFrame:
    """Qt owns its FBO/detached dummy; the capture owns all MS attachments."""
    def __init__(self, frame, textures): self.frame, self.names = frame, textures
    def bind(self): return self.frame.bind()
    def textures(self): return self.names
    def texture(self): return self.names[0]
    def handle(self): return self.frame.handle()
    def width(self): return self.frame.width()
    def height(self): return self.frame.height()


class LayerCapture:
    """Seed -> scalar depth/ID -> original receiver -> tiled query -> compaction.

    Only an explicit canonical entirely-empty selector ends enumeration. The
    final LayerImage is plain data; LayerStorage and its read leases are separate.
    Initial allocation/CPU compaction are not claimed as bounded render turns.
    """
    def __init__(self, gl, context, width, height, *, key, source_key, draws,
                 existing_bytes, byte_budget, samples=1, sample_positions=None, packed_queries=True):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtCore import Qt
        if (any(type(v) is not int for v in (width, height, existing_bytes, byte_budget))
                or not 0 < width <= 8192 or not 0 < height <= 8192
                or existing_bytes < 0 or byte_budget < 0
                or type(samples) is not int or samples not in (1,4)):
            raise ValueError('Bounded exact layer capture ledger required')
        if samples==4:
            if (type(sample_positions) is not tuple or len(sample_positions)!=4
                    or any(type(point) is not tuple or len(point)!=2
                           or any(type(v) is not float or not np.isfinite(v) or not 0<=v<=1 for v in point)
                           for point in sample_positions) or len(set(sample_positions))!=4):
                raise ValueError('Actual original ordered four sample positions required')
        elif sample_positions is not None:
            raise ValueError('Single-sample capture has no MS pattern')
        key, source_key = _generation(key), _generation(source_key)
        if type(key) is not tuple or type(source_key) is not tuple:
            raise ValueError('Frozen view and completed source-ticket identities required')
        if type(draws) is not tuple or not 0 < len(draws) <= 256:
            raise ValueError('Complete original draw descriptors required')
        end = 0
        for descriptor in draws:
            if (type(descriptor) is not tuple or len(descriptor) != 3
                    or any(type(v) is not int for v in descriptor[:2]) or type(descriptor[2]) is not bool
                    or descriptor[0] < end or descriptor[1] <= 0
                    or descriptor[0] > MAX_ID+1-descriptor[1]):
                raise ValueError('Disjoint original primitive ranges and visibility family required')
            end = descriptor[0]+descriptor[1]
        if (QOpenGLContext.currentContext() is not context or context.format().majorVersion() < 4
                or context.format().profile() != context.format().OpenGLContextProfile.CoreProfile):
            raise RuntimeError('Layer capture requires its exact core4 context')
        if int(gl.glGetIntegerv(0x8824)) < 3 or int(gl.glGetIntegerv(0x8CDF)) < 3:
            raise RuntimeError('Three original receiver attachments required')
        self.gl, self.context, self.width, self.height = gl, context, width, height
        self.key, self.source_key, self.draws = (key, next(_requests)), source_key, draws
        if type(packed_queries) is not bool: raise ValueError('Explicit packed query mode required')
        self.packed_queries = packed_queries
        self.samples, self.positions = samples, sample_positions
        if samples==4:
            self.key += (samples,tuple(int(v) for v in np.array(sample_positions,np.float32).view(np.uint32).flat))
        # S4: records208/selectors48/cursor64/query64/export60 + Qt dummies16.
        self.graphics_bytes = width*height*((76 if samples==1 else 396) if packed_queries else (92 if samples==1 else 460))
        if packed_queries: self.graphics_bytes += 16*16*52 + 1024*1024  # tiny inputs/output + program/scratch reserve
        self.metadata_bytes = METADATA_RESERVE+256*len(draws)
        self.builder = LayerBuilder(self.key, width, height,
            existing_bytes=existing_bytes+self.graphics_bytes+self.metadata_bytes,
            byte_budget=byte_budget, texel_limit=min((1 << 31)-1, int(gl.glGetIntegerv(0x8C2B))),
            samples=samples,sample_positions=sample_positions)
        self.builder._room((49 if samples==1 else 53)*width*height*samples)  # Cursor9B + complete readbacks40B per plane.
        self.targets = {}; self.image = None
        self.phase = 'seed'; self.layer = self.tile = self._read_row = self._read_plane = 0
        self._readbacks = {}; self._sync = None; self._busy = 0; self._stage = None; self._seen = set()
        self._read_sample=self._cursor_sample=0; self._export_read=self._export_query=None
        self._gather_program = self._gather_vao = None
        self._batch_indices = None; self._scan_offset = 0; self._query_pending = False
        self.query_batches = self.query_records = 0
        self._packed_result = None; self._packed_offset = 0
        self._sample_names=[]; self._export_program=self._copy_program=self._export_vao=None
        self.closed = self.quarantined = self.withdrawn = False; self._retirement = None
        try:
            with self._admission(), preserved_state(gl, context, exact_context=True):
                if samples==4:
                    with preserved_samples(gl,context,texture_units=(0,1,2),exact_context=True):
                        self._allocate_samples()
                else:
                    self._allocate_single()
                if self.packed_queries:
                    with preserved_samples(gl,context,texture_units=(0,1,2),exact_context=True):self._allocate_packed()
                if gl.glGetError(): raise RuntimeError('Layer allocation failed')
                self._fence()
            owner_ref = ref(self)
            def destroyed():
                owner = owner_ref()
                if owner is not None:
                    try: owner.close()
                    except Exception: pass  # Whole uncertain graph remains rooted.
            context.aboutToBeDestroyed.connect(destroyed, Qt.ConnectionType.DirectConnection)
            self._retirement = destroyed
        except Exception as error: self._quarantine(error)

    def _allocate_single(self):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        gl,context,width,height=self.gl,self.context,self.width,self.height
        for name, internal, attachments in (
                        ('records', 0x8814, 3), ('depth', 0x822E, 1), ('identity', 0x822E, 1),
                        ('query', 0x8814, 1), ('cursor', 0x8814, 1)):
                    if self.packed_queries and name == 'query': continue
                    self._current()
                    fmt = QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(internal)
                    if name == 'records': fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
                    target = QOpenGLFramebufferObject(width, height, fmt); self.targets[name] = target
                    for _ in range(attachments-1): target.addColorAttachment(width, height, internal)
                    if not target.isValid() or not target.bind(): raise RuntimeError('Layer attachment allocation failed')
                    if int(gl.glGetIntegerv(0x80A9)) != 0: raise RuntimeError('This layer coordinator requires S1 storage')
                    self._certify(target, internal, attachments)
        records = self.targets['records']; records.bind()
        query = procedure(context, 'glGetFramebufferAttachmentParameteriv', None, U, U, U, ctypes.POINTER(I))
        name, kind, bits = I(), I(), I()
        for parameter, value in ((0x8CD0, kind), (0x8CD1, name), (0x8216, bits)):
            query(0x8D40, 0x8D00, parameter, ctypes.byref(value))
        if kind.value != 0x8D41 or not name.value or bits.value not in (16, 24, 32):
            raise RuntimeError('Original visibility depth storage is uncertified')
        for selector in ('depth', 'identity'):
            self.targets[selector].bind()
            actual = I(); query(0x8D40, 0x8D00, 0x8CD0, ctypes.byref(actual))
            if actual.value != 0: raise RuntimeError('Selector unexpectedly owns private depth storage')
            procedure(context, 'glFramebufferRenderbuffer', None, U, U, U, U)(0x8D40, 0x8D00, 0x8D41, name.value)
            actual = I(); query(0x8D40, 0x8D00, 0x8CD1, ctypes.byref(actual))
            if (actual.value != name.value or procedure(context, 'glCheckFramebufferStatus', U, U)(0x8D40) != 0x8CD5):
                raise RuntimeError('Selectors do not share the original visibility depth')

    def _allocate_samples(self):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        gl,context=self.gl,self.context
        image=procedure(context,'glTexImage2DMultisample',None,U,I,U,I,I,ctypes.c_ubyte)
        attach=procedure(context,'glFramebufferTexture2D',None,U,U,U,U,I)
        check=procedure(context,'glCheckFramebufferStatus',U,U)
        names=(U*7)(); procedure(context,'glGenTextures',None,I,ctypes.POINTER(U))(7,names)
        self._sample_names=list(names)
        if not all(names): raise RuntimeError('Layer MS texture allocation failed')
        depth=names[6]
        gl.glActiveTexture(0x84C0)
        gl.glBindTexture(0x9100,depth); image(0x9100,4,0x8CAC,self.width,self.height,1)
        fields=(('records',0x8814,tuple(names[:3])),('depth',0x8230,(names[3],)),
                ('identity',0x822E,(names[4],)),('cursor',0x8814,(names[5],)))
        for name,internal,textures in fields:
            fmt=QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x8058)
            frame=QOpenGLFramebufferObject(self.width,self.height,fmt)
            target=_SampleFrame(frame,textures); self.targets[name]=target
            if not frame.isValid() or not frame.bind(): raise RuntimeError('Layer MS framebuffer unavailable')
            for slot,texture in enumerate(textures):
                gl.glBindTexture(0x9100,texture); image(0x9100,4,internal,self.width,self.height,1)
                attach(0x8D40,0x8CE0+slot,0x9100,texture,0)
            if name!='cursor': attach(0x8D40,0x8D00,0x9100,depth,0)
            if check(0x8D40)!=0x8CD5 or int(gl.glGetIntegerv(0x80A9))!=4:
                raise RuntimeError('Layer MS storage is incomplete')
            self._certify(target,internal,len(textures))
            if name!='cursor':
                sample_depth_certificate(gl,context,self.width,self.height,expected_name=depth)
            query=procedure(context,'glGetMultisamplefv',None,U,U,ctypes.POINTER(ctypes.c_float))
            positions=[]
            for plane in range(4):
                point=(ctypes.c_float*2)(); query(0x8E50,plane,point); positions.append(tuple(point))
            if tuple(positions)!=self.positions:
                raise RuntimeError('Layer MS sample positions differ from original coverage')
        gl.glBindTexture(0x9100,depth)
        query=procedure(context,'glGetTexLevelParameteriv',None,U,I,U,ctypes.POINTER(I))
        for parameter,expected in ((0x1003,0x8CAC),(0x9106,4),(0x1000,self.width),(0x1001,self.height),(0x9107,1)):
            actual=I(); query(0x9100,0,parameter,ctypes.byref(actual))
            if actual.value!=expected: raise RuntimeError('Layer MS visibility depth is uncertified')
        for name,internal,count_ in (('query',0x8814,1),('query1',0x8814,1),
                ('query2',0x8814,1),('query3',0x8814,1),('export-records',0x8814,3),
                ('export-depth',0x8230,1),('export-identity',0x822E,1)):
            if self.packed_queries and name.startswith('query'): continue
            fmt=QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(internal)
            target=QOpenGLFramebufferObject(self.width,self.height,fmt); self.targets[name]=target
            for _ in range(count_-1): target.addColorAttachment(self.width,self.height,internal)
            if not target.isValid() or not target.bind(): raise RuntimeError('Layer plane staging unavailable')
            self._certify(target,internal,count_)
            if int(gl.glGetIntegerv(0x80A9))!=0: raise RuntimeError('Raw export requires real S1 staging')

    def _allocate_packed(self):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        for name, internal, count_ in (('gather',0x8814,2),('query',0x8814,1)):
            fmt=QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(internal)
            target=QOpenGLFramebufferObject(16,16,fmt); self.targets[name]=target
            for _ in range(count_-1): target.addColorAttachment(16,16,internal)
            if not target.isValid() or not target.bind(): raise RuntimeError('Packed query storage unavailable')
            self._certify(target,internal,count_)

        value=U();procedure(self.context,'glGenTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
        self._indices=value.value;self._sample_names.append(value.value)
        self._texture(2,self._indices)
        unpack=int(self.gl.glGetIntegerv(0x88EF))
        try:
            self.gl.glBindBuffer(0x88EC,0)
            procedure(self.context,'glTexImage2D',None,U,I,I,I,I,I,U,U,P)(0x0DE1,0,0x8236,16,16,0,0x8D94,0x1405,None)
        finally:self._current();self.gl.glBindBuffer(0x88EC,unpack)
        self.gl.glTexParameteri(0x0DE1,0x2801,0x2600);self.gl.glTexParameteri(0x0DE1,0x2800,0x2600)

    def _restore_transfer(self, actions):
        errors=[]
        for function,args in actions:
            self._current()
            try:function(*args)
            except Exception as error:errors.append(error)
            self._current()
        if errors:raise RuntimeError('Packed transfer host restoration failed') from errors[0]

    def _gather(self, plane, indices):
        if (type(plane) is not int or not 0<=plane<self.samples or indices.dtype!=np.uint32
                or indices.ndim!=1 or not 0<len(indices)<=256
                or np.any(indices>=self.width*self.height) or np.any(indices[1:]<=indices[:-1])):
            raise ValueError('Exact ordered active sample indices required')
        from PyQt6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
        if self._gather_program is None:
            shader=QOpenGLShaderProgram(); self._gather_program=shader
            vertex='#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
            sampler='sampler2DMS' if self.samples==4 else 'sampler2D'
            sample='plane' if self.samples==4 else '0'
            fragment=('#version 410\nuniform '+sampler+' origin,ray; uniform usampler2D indices; uniform int plane,width,count_;'
                'layout(location=0)out vec4 o;layout(location=1)out vec4 r;'
                'void main(){ivec2 p=ivec2(gl_FragCoord.xy);o=vec4(0.);r=vec4(0.);'
                'if(p.y*16+p.x>=count_)return;uint i=texelFetch(indices,p,0).r;'
                'ivec2 q=ivec2(int(i)%width,int(i)/width);'
                'o=texelFetch(origin,q,'+sample+');r=texelFetch(ray,q,'+sample+');}')
            if (not shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,vertex)
                    or not shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment)
                    or not shader.link()): raise RuntimeError('Packed receiver gather unavailable: '+shader.log())
            self._gather_vao=QOpenGLVertexArrayObject()
            if not self._gather_vao.create(): raise RuntimeError('Packed receiver VAO unavailable')
        # Upload state belongs to the host, including any unpack PBO/strides.
        packed={key:int(self.gl.glGetIntegerv(key)) for key in (0x0CF0,0x0CF2,0x0CF3,0x0CF4,0x0CF5)}
        buffer=int(self.gl.glGetIntegerv(0x88EF))
        data=np.zeros(256,np.uint32); data[:len(indices)]=indices
        try:
            self.gl.glBindBuffer(0x88EC,0)
            for key in packed:self.gl.glPixelStorei(key,1 if key==0x0CF5 else 0)
            self._texture(2,self._indices)
            procedure(self.context,'glTexSubImage2D',None,U,I,I,I,I,I,U,U,P)(0x0DE1,0,0,0,16,16,0x8D94,0x1405,P(data.ctypes.data))
        finally:
            self._restore_transfer([(self.gl.glPixelStorei,(key,value)) for key,value in packed.items()]
                +[(self.gl.glBindBuffer,(0x88EC,buffer))])
        self._bind('gather')
        for flag in (self.gl.GL_BLEND,self.gl.GL_DEPTH_TEST,self.gl.GL_CULL_FACE):self.gl.glDisable(flag)
        shader=self._gather_program;self._gather_vao.bind()
        if not shader.bind():raise RuntimeError('Packed receiver gather bind failed')
        try:
            for unit,name in enumerate(('origin','ray')):
                self._texture(unit,self.targets['records'].textures()[unit],multisample=self.samples==4)
                shader.setUniformValue(name,unit)
            shader.setUniformValue('indices',2);shader.setUniformValue('width',self.width)
            shader.setUniformValue('plane',plane);shader.setUniformValue('count_',len(indices))
            self.gl.glDrawArrays(self.gl.GL_TRIANGLES,0,3)
        finally:shader.release();self._gather_vao.release()

    def _packed_query(self, query_scope, query):
        pixels=self.width*self.height
        if 'query' not in self._readbacks:
            self.builder._room((49 if self.samples==1 else 53)*pixels*self.samples+24*self.builder.matches)
            shape=(self.height,self.width,4) if self.samples==1 else (4,self.height,self.width,4)
            self._readbacks['query']=np.zeros(shape,np.float32)
        if self._query_pending:
            self._bind('query')
            if self._packed_result is None:self._packed_result=np.empty((256,4),np.float32)
            result=self._packed_result
            offset=self._packed_offset
            x,y=offset%16,offset//16
            width=min(16-x,max(1,READ_BYTES//16))
            rows=min(16-y,max(1,READ_BYTES//(width*16))) if width==16 else 1
            count_=width*rows
            packed={key:int(self.gl.glGetIntegerv(key)) for key in (0x0D00,0x0D02,0x0D03,0x0D04,0x0D05)}
            buffer=int(self.gl.glGetIntegerv(0x88ED));clamped=int(self.gl.glGetIntegerv(0x891C))
            clamp=procedure(self.context,'glClampColor',None,U,U)
            try:
                self.gl.glBindBuffer(0x88EB,0);clamp(0x891C,0)
                for key in packed:self.gl.glPixelStorei(key,1 if key==0x0D05 else 0)
                procedure(self.context,'glReadBuffer',None,U)(0x8CE0)
                procedure(self.context,'glReadPixels',None,I,I,I,I,U,U,P)(x,y,width,rows,0x1908,0x1406,P(result[offset:offset+count_].ctypes.data))
                if self.gl.glGetError():raise RuntimeError('Packed query readback failed')
            finally:
                self._restore_transfer([(self.gl.glPixelStorei,(key,value)) for key,value in packed.items()]
                    +[(self.gl.glBindBuffer,(0x88EB,buffer)),(clamp,(0x891C,clamped))])
            self._packed_offset+=count_
            if self._packed_offset<256:return
            self._packed_offset=0;self._packed_result=None
            plane,indices=self._batch_indices
            if np.any(result[len(indices):]!=0):raise RuntimeError('Packed query padding was not empty')
            self._readbacks['query'].reshape(self.samples,pixels,4)[plane,indices]=result[:len(indices)]
            self._batch_indices=None;self._query_pending=False
            return
        # Scan bounded windows; never allocate a crop-sized int64 index list.
        identities=self._readbacks['identity'].reshape(-1)
        while self._scan_offset<len(identities):
            start=self._scan_offset;plane=start//pixels
            end=min(start+65536,(plane+1)*pixels)
            local=np.flatnonzero(identities[start:end]<=MAX_ID)[:256]
            self._scan_offset=(start+int(local[-1])+1) if len(local)==256 else end
            if not len(local):return  # bounded scan work; the parent pump may continue
            indices=(local+start-plane*pixels).astype(np.uint32)
            self._batch_indices=(plane,indices)
            self._gather(plane,indices)
            with query_scope() as source:
                self._current()
                if source is None or source.key!=self.source_key:self.withdrawn=True;return
                self._clear('query');self.gl.glEnable(self.gl.GL_SCISSOR_TEST);self.gl.glScissor(0,0,16,16)
                query(source,tuple(self.targets['gather'].textures()))
                self._current();self._fence()
            self.query_batches+=1;self.query_records+=len(indices);self._query_pending=True
            return
        self._readbacks['query'].setflags(write=False);self.phase='payload'

    def _sample_program(self, copy=False):
        from PyQt6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
        attr='_copy_program' if copy else '_export_program'
        shader=getattr(self,attr)
        if shader is None:
            shader=QOpenGLShaderProgram(); setattr(self,attr,shader)
            vertex='''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}
'''
            fragment='''#version 410
uniform sampler2DMS source0,source1,source2;uniform int plane;
layout(location=0)out vec4 colour0;layout(location=1)out vec4 colour1;layout(location=2)out vec4 colour2;
void main(){ivec2 p=ivec2(gl_FragCoord.xy);
 colour0=texelFetch(source0,p,plane);colour1=texelFetch(source1,p,plane);colour2=texelFetch(source2,p,plane);}
''' if not copy else '''#version 410
uniform sampler2DMS source0;uniform int plane;out vec4 colour0;
void main(){gl_SampleMask[0]=1<<plane;colour0=texelFetch(source0,ivec2(gl_FragCoord.xy),plane);}
'''
            if (not shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,vertex)
                    or not shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment)
                    or not shader.link()): raise RuntimeError('Raw sample transport program unavailable: '+shader.log())
        if self._export_vao is None:
            self._export_vao=QOpenGLVertexArrayObject()
            if not self._export_vao.create(): raise RuntimeError('Raw sample transport VAO unavailable')
        return shader

    def _export(self, name, plane, *, copy=False):
        self._current()
        if type(plane) is not int or not 0<=plane<4: raise ValueError('Exact raw sample plane required')
        self._bind('cursor' if copy else 'export-records' if name=='record' else 'export-'+name)
        for flag in (self.gl.GL_BLEND,self.gl.GL_DEPTH_TEST,self.gl.GL_CULL_FACE,
                     0x8E51,0x80A0,0x809E,0x809F,0x8C36): self.gl.glDisable(flag)
        self.gl.glEnable(0x809D)
        source=self.targets['records'] if name=='record' else self.targets[name]
        shader=self._sample_program(copy); self._export_vao.bind()
        if not shader.bind(): raise RuntimeError('Raw sample transport bind failed')
        try:
            textures=source.textures()
            for unit in range(1 if copy else 3):
                self._texture(unit,textures[min(unit,len(textures)-1)],multisample=True)
                shader.setUniformValue('source'+str(unit),unit)
            if copy:
                self._texture(0,source.textures()[2],multisample=True)
            shader.setUniformValue('plane',plane); self.gl.glDrawArrays(self.gl.GL_TRIANGLES,0,3)
        finally: shader.release(); self._export_vao.release()
        if self.gl.glGetError(): raise RuntimeError('Raw sample transport failed')

    @property
    def retained_bytes(self):
        if self.closed: return 0
        image = self.builder.image.retained_bytes if self.builder.image is not None else 0
        return (self.graphics_bytes+self.metadata_bytes+METADATA_RESERVE+9*self.width*self.height*self.samples
                +24*self.builder.matches+CHUNK_METADATA*len(self.builder.chunks)
                +sum(v.nbytes for v in self._readbacks.values())+image)

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Layer capture requires its live creating context')

    def _quarantine(self, error):
        self.quarantined = self.withdrawn = True; self.image = None
        if not any(owner is self for owner in _uncertain): _uncertain.append(self)
        raise GeometryUncertain(self, str(error)) from error

    @contextmanager
    def _admission(self):
        self._busy += 1
        try: yield
        finally: self._busy -= 1

    def _fence(self):
        self._current()
        if self._sync is not None: raise RuntimeError('Layer producer fence is still owned')
        sync = procedure(self.context, 'glFenceSync', P, U, U)(0x9117, 0)
        if not sync: raise RuntimeError('Layer producer fence unavailable')
        self._sync = int(sync); self.gl.glFlush()
        if self.gl.glGetError(): raise RuntimeError('Layer producer flush failed')

    def _poll(self):
        self._current()
        if self._sync is None: return True
        status = procedure(self.context, 'glClientWaitSync', U, P, U, ctypes.c_ulonglong)(P(self._sync), 0, 0)
        if self.gl.glGetError(): raise RuntimeError('Layer producer wait failed')
        if status == 0x911B: return False
        if status not in (0x911A, 0x911C): raise RuntimeError('Layer producer completion uncertain')
        procedure(self.context, 'glDeleteSync', None, P)(P(self._sync))
        if self.gl.glGetError(): raise RuntimeError('Layer producer cleanup failed')
        self._sync = None; return True

    def _certify(self, target, internal, count_):
        if len(target.textures()) != count_: raise RuntimeError('Layer attachment count differs')
        query = procedure(self.context, 'glGetTexLevelParameteriv', None, U, I, U, ctypes.POINTER(I))
        for texture in target.textures():
            kind=0x9100 if isinstance(target,_SampleFrame) else 0x0DE1
            self.gl.glActiveTexture(0x84C0); self.gl.glBindTexture(kind, texture)
            checks=((0x1003, internal), (0x1000, target.width()), (0x1001, target.height()))
            if kind==0x9100: checks+=((0x9106,4),(0x9107,1))
            for parameter, expected in checks:
                result = I(); query(kind, 0, parameter, ctypes.byref(result))
                if result.value != expected: raise RuntimeError('Layer attachment descriptor differs')

    def _bind(self, name):
        self._current(); target = self.targets[name]
        if not target.bind(): raise RuntimeError('Layer target bind failed')
        count_ = len(target.textures())
        procedure(self.context, 'glDrawBuffers', None, I, ctypes.POINTER(U))(count_, (U*count_)(*(0x8CE0+i for i in range(count_))))
        self.gl.glViewport(0, 0, target.width(), target.height())
        self.gl.glDisable(self.gl.GL_SCISSOR_TEST); self.gl.glColorMask(True, True, True, True)
        return target

    def _clear(self, name, sentinel=0.):
        self._bind(name)
        clear = procedure(self.context, 'glClearBufferfv', None, U, I, ctypes.POINTER(ctypes.c_float))
        values=(np.finfo(np.float32).max,1.,0.,0.) if name=='depth' and self.samples==4 else (sentinel,0.,0.,0.)
        for slot in range(len(self.targets[name].textures())): clear(0x1800,slot,(ctypes.c_float*4)(*values))

    def _texture(self, unit, texture, *, multisample=False):
        self.gl.glActiveTexture(0x84C0+unit); self.gl.glBindTexture(0x9100 if multisample else 0x0DE1, texture)
        procedure(self.context, 'glBindSampler', None, U, U)(unit, 0)

    def deliver(self, shader, index):
        """Immediately before each original primitive draw in the callback."""
        self._current()
        if (self._stage not in ('depth', 'identity', 'record') or type(index) is not int
                or index not in range(len(self.draws)) or index in self._seen
                or int(self.gl.glGetIntegerv(0x8B8D)) != shader.programId()):
            raise ValueError('One bound original program/draw delivery required')
        base, count_, opaque = self.draws[index]
        self.gl.glEnable(self.gl.GL_DEPTH_TEST); self.gl.glDepthMask(False)
        self.gl.glDepthFunc(self.gl.GL_LEQUAL if opaque else self.gl.GL_LESS)
        if self._stage == 'record': self.gl.glDisable(self.gl.GL_BLEND)
        else:
            self.gl.glEnable(self.gl.GL_BLEND); self.gl.glBlendEquationSeparate(0x8007, 0x8007)
        for name, value in dict(mpf_layerBase=base, mpf_layerCount=count_, mpf_layerPrevious=int(self.layer > 0),
                mpf_previousCursor=0, mpf_selectedDepth=1, mpf_selectedIdentity=2,
                mpf_layerSamples=self.samples).items(): shader.setUniformValue(name, value)
        uniform = procedure(self.context, 'glUniform2i', None, I, I, I)
        uniform(shader.uniformLocation('mpf_layerOrigin'), 0, 0)
        uniform(shader.uniformLocation('mpf_layerSize'), self.width, self.height)
        self._seen.add(index)

    def _read(self, names):
        name = names[self._read_plane]
        if name == 'query' and self.packed_queries:
            self._read_plane += 1
            return self._read_plane == len(names)
        components = 2 if name=='depth' and self.samples==4 else 1 if name in ('depth', 'identity') else 4
        pixels = self.width*self.height
        if name not in self._readbacks:
            self.builder._room((49 if self.samples==1 else 53)*pixels*self.samples+24*self.builder.matches)
            shape=(self.height,self.width,components) if self.samples==1 else (4,self.height,self.width,components)
            self._readbacks[name] = np.empty(shape,np.float32)
        rows = min(self.height-self._read_row, max(1, READ_BYTES//(self.width*components*4)))
        if self.samples==4:
            if name=='query': self._bind('query'+(str(self._read_sample) if self._read_sample else ''))
            else:
                signature=name,self._read_sample
                if self._export_read!=signature:
                    self._export(name,self._read_sample); self._export_read=signature; self._fence(); return False
                self._bind('export-records' if name=='record' else 'export-'+name)
        else: self._bind('records' if name == 'record' else name)
        procedure(self.context, 'glReadBuffer', None, U)(0x8CE2 if name == 'record' else 0x8CE0)
        packed = {key: int(self.gl.glGetIntegerv(key)) for key in (0x0D00, 0x0D02, 0x0D03, 0x0D04, 0x0D05)}
        buffer = int(self.gl.glGetIntegerv(0x88ED))
        read_clamp = int(self.gl.glGetIntegerv(0x891C))
        clamp = procedure(self.context, 'glClampColor', None, U, U)
        try:
            clamp(0x891C, 0)
            self.gl.glBindBuffer(0x88EB, 0)
            for key in packed: self.gl.glPixelStorei(key, 1 if key == 0x0D05 else 0)
            array=self._readbacks[name] if self.samples==1 else self._readbacks[name][self._read_sample]
            result = array[self._read_row:self._read_row+rows]
            procedure(self.context, 'glReadPixels', None, I, I, I, I, U, U, P)(0, self._read_row,
                self.width, rows, 0x1903 if components==1 else 0x8227 if components==2 else 0x1908, 0x1406, P(result.ctypes.data))
            if self.gl.glGetError(): raise RuntimeError('Completed layer readback failed')
        finally:
            errors = []
            for function, args in ([(self.gl.glPixelStorei, (key, value)) for key, value in packed.items()]
                    +[(self.gl.glBindBuffer, (0x88EB, buffer)), (clamp, (0x891C, read_clamp))]):
                self._current()
                try: function(*args)
                except Exception as error: errors.append(error)
                self._current()
            if errors: raise RuntimeError('Layer pixel-pack state could not be restored') from errors[0]
        self._read_row += rows
        if self._read_row == self.height:
            self._read_row=0; self._read_sample+=1
            if self._read_sample==self.samples:
                self._readbacks[name].setflags(write=False); self._read_sample=0; self._read_plane+=1
        return self._read_plane == len(names)

    def step(self, selected_key, *, seed, draw, query_scope, query):
        """One phase, query tile or <=1MiB completed row copy; no partial image."""
        self._current()
        if self._busy: return False
        if selected_key != self.key: self.withdrawn = True; self.image = None
        if self.withdrawn: return False
        if any(not callable(callback) for callback in (seed, draw, query_scope, query)):
            raise ValueError('Original seed/draw and completed source-query callbacks required')
        try:
            with self._admission():
                if not self._poll(): return False
                if self.phase == 'complete': self.image = self.builder.image; return True
                with (preserved_state(self.gl, self.context, exact_context=True),
                      preserved_samples(self.gl,self.context,texture_units=(0,1,2),exact_context=True)
                      if self.samples==4 or self.packed_queries else nullcontext()):
                    if (tuple(map(int, self.gl.glGetIntegerv(0x0B40))) != (0x1B02, 0x1B02)
                            or any(self.gl.glIsEnabled(flag) for flag in (0x8C89, 0x0B90, *range(0x3000, 0x3008)))):
                        raise RuntimeError('Layer passes require original unrestricted fill coverage')
                    if self.samples==4:
                        self.gl.glEnable(0x809D)
                        for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36): self.gl.glDisable(flag)
                    if self.phase == 'seed':
                        self._clear('cursor'); self._clear('records'); self.gl.glDepthMask(True)
                        self.gl.glClearDepth(1.); self.gl.glClear(self.gl.GL_DEPTH_BUFFER_BIT)
                        if seed(self) is not True: self.withdrawn = True; return False
                        self._current(); self._fence(); self.phase = 'depth'
                    elif self.phase in ('depth', 'identity', 'record'):
                        stage = self.phase
                        self._clear('records' if stage == 'record' else stage,
                                    2. if stage == 'depth' else MAX_ID+1 if stage == 'identity' else 0.)
                        for unit, name in enumerate(('cursor', 'depth', 'identity')):
                            if name != stage: self._texture(unit,self.targets[name].texture(),multisample=self.samples==4)
                        self._stage = stage; self._seen.clear()
                        # The collector owns immutable seeded depth. Establish
                        # that contract BEFORE a draw callback saves/restores
                        # its independent native RenderBatch state.
                        self.gl.glDepthMask(False)
                        try: draw(stage, self)
                        finally: self._stage = None
                        if len(self._seen) != len(self.draws): raise ValueError('Original draw set was incomplete')
                        if self.gl.glGetBooleanv(self.gl.GL_DEPTH_WRITEMASK): raise RuntimeError('Layer draw changed static depth')
                        self._fence()
                        self.phase = dict(depth='identity', identity='selectors', record='query')[stage]
                    elif self.phase == 'selectors':
                        if self._read(('depth', 'identity')):
                            self._read_plane = 0
                            depth = self._readbacks['depth'][...,0]; identity = self._readbacks['identity'][...,0]
                            state=self._readbacks['depth'][...,1] if self.samples==4 else None
                            empty = ((depth==2.) if state is None else
                                     ((depth==np.finfo(np.float32).max)&(state==1.))) & (identity==MAX_ID+1)
                            valid = np.isfinite(depth) & (((depth>=0.)&(depth<=1.)) if state is None else (state==0.))
                            valid &= np.isfinite(identity) & (identity >= 0.) & (identity <= MAX_ID) & (identity == np.floor(identity))
                            if not np.all(empty | valid): raise ValueError('Malformed original receiver selector')
                            self.phase = 'terminal' if np.all(empty) else 'record'
                    elif self.phase == 'query' and self.packed_queries:
                        self._packed_query(query_scope, query)
                    elif self.phase == 'query':
                        per_plane=((self.width+15)//16)*((self.height+15)//16)
                        plane,tile=divmod(self.tile,per_plane)
                        target='query'+(str(plane) if plane else '')
                        if self.samples==4 and self._export_query!=plane:
                            self._export('record',plane); self._export_query=plane; self._fence(); return False
                        if tile == 0: self._clear(target)
                        with query_scope() as source:
                            self._current()
                            if source is None or source.key != self.source_key:
                                self.withdrawn = True; return False
                            self._bind(target)
                            for flag in (self.gl.GL_BLEND, self.gl.GL_DEPTH_TEST, self.gl.GL_CULL_FACE): self.gl.glDisable(flag)
                            row, column = divmod(tile, (self.width+15)//16)
                            x, y = column*16, row*16
                            self.gl.glEnable(self.gl.GL_SCISSOR_TEST)
                            self.gl.glScissor(x, y, min(16, self.width-x), min(16, self.height-y))
                            records=self.targets['records' if self.samples==1 else 'export-records']
                            query(source, tuple(records.textures())[:2])
                            self._current(); self._fence()
                        self.tile += 1
                        if self.tile == per_plane*self.samples: self.phase = 'payload'
                    elif self.phase == 'payload':
                        if self._read(('record', 'query')): self._read_plane = 0; self.phase = 'compact'
                    elif self.phase in ('compact', 'terminal'):
                        if self.phase == 'terminal':
                            self._clear('records')
                            if not self.packed_queries:
                                for plane in range(self.samples): self._clear('query'+(str(plane) if plane else ''))
                            self.builder._room((49 if self.samples==1 else 53)*self.width*self.height*self.samples+24*self.builder.matches)
                            shape=(self.height,self.width,4) if self.samples==1 else (4,self.height,self.width,4)
                            for name in ('record', 'query'): self._readbacks[name] = np.zeros(shape,np.float32)
                        ended = self.builder.accept(self.key,self._readbacks['depth'][...,0] if self.samples==1 else self._readbacks['depth'],
                            self._readbacks['identity'][...,0], self._readbacks['record'], self._readbacks['query'])
                        if ended:
                            self.builder.finish(self.key); self._readbacks.clear(); self._fence(); self.phase = 'complete'
                        else: self.phase = 'cursor'
                    elif self.phase == 'cursor':
                        if self.samples==4:
                            self._export('record',self._cursor_sample,copy=True); self._cursor_sample+=1
                        else:
                            self._bind('records'); procedure(self.context, 'glReadBuffer', None, U)(0x8CE2)
                            self._texture(0, self.targets['cursor'].texture())
                            procedure(self.context, 'glCopyTexSubImage2D', None, U, I, I, I, I, I, I, I)(
                                0x0DE1, 0, 0, 0, 0, 0, self.width, self.height)
                            self._cursor_sample=1
                        self._fence()
                        if self._cursor_sample==self.samples:
                            self._cursor_sample=0; self._readbacks.clear(); self.layer+=1; self.tile=0
                            self._scan_offset=0; self._batch_indices=None; self._query_pending=False
                            self._export_query=self._export_read=None; self.phase='depth'
                    if self.gl.glGetError(): raise RuntimeError('Layer phase failed')
                self._current()
            return False
        except Exception as error: self._quarantine(error)

    def close(self):
        if self.closed: return
        try:
            self._current()
            if self._busy: raise RuntimeError('Layer capture still has admitted work')
            self.withdrawn = True
            with self._admission():
                self.gl.glFinish()
                if self.gl.glGetError() or not self._poll(): raise RuntimeError('Layer retirement is uncertain')
                # Selector FBOs borrowing depth die before its records owner.
                for name in tuple(self.targets):
                    if name!='records': self.targets.pop(name,None)
                self.targets.pop('records',None)
                while self._sample_names:
                    value=U(self._sample_names[-1])
                    procedure(self.context,'glDeleteTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
                    if self.gl.glGetError(): raise RuntimeError('Layer MS texture retirement failed')
                    self._sample_names.pop()
                if self._export_vao is not None: self._export_vao.destroy(); self._export_vao=None
                if self._gather_vao is not None: self._gather_vao.destroy(); self._gather_vao=None
                self._gather_program=None; self._batch_indices=None; self._packed_result=None
                self._export_program=self._copy_program=None
                if self._retirement is not None:
                    self.context.aboutToBeDestroyed.disconnect(self._retirement); self._retirement = None
                self._readbacks.clear(); self.image = self.builder = None
                self.closed = True
        except Exception as error: self._quarantine(error)
