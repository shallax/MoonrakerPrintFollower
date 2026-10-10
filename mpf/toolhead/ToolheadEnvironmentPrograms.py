"""Original receiver draws and separate bounded queries for one frozen frame.

No query code enters the CAD material shader. The caller retains this owner,
meshes, depth and map lease until all collection/lookup reads have drained.
"""
import ctypes
from contextlib import contextmanager
import math
from weakref import ref

from .ToolheadEnvironmentGeometry import GeometryUncertain
from .ToolheadEnvironmentRecovery import (layer_vertex, layer_selection_fragment,
    layer_receiver_fragment, layer_lookup_fragment, recovery_query_fragment)
from .ToolheadGLState import preserved_state, preserved_samples, procedure
from .ToolheadCaptureValues import thaw_uniform
from ..resources.PluginPaths import plugin_path

PROGRAM_BYTES = 16*1024**2
_uncertain = []


def create_program(stage, solid, retain):
    if stage not in ('depth','identity','record','lookup','seed','query') or type(solid) is not bool:
        raise ValueError('Owned receiver program stage required')
    from UM.View.GL.ShaderProgram import ShaderProgram
    class Program(ShaderProgram):
        def setVertexShader(self, source):
            value=('#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);'
                   'gl_Position=vec4(p*2.-1.,0.,1.);}') if stage=='query' else layer_vertex(source)
            return super().setVertexShader(value)
        def setFragmentShader(self, source):
            if stage in ('depth','identity'): value=layer_selection_fragment(stage,single_pass=solid,samples=4)
            elif stage=='record': value=layer_receiver_fragment(source,single_pass=solid,samples=4)
            elif stage=='lookup': value=layer_lookup_fragment(source.replace('if (v_color.a <= 0.0) discard;','') if solid else source,samples=4)
            elif stage=='seed': value=source
            elif stage=='query': value=recovery_query_fragment()
            else: raise ValueError('Owned receiver program stage required')
            return super().setFragmentShader(value)
    program=Program()
    # Pin the partially created Qt graph BEFORE load/compile can fail or invoke
    # context retirement. The owner must not lose an uncertain program name.
    retain(program)
    program.load(plugin_path('toolhead','toolhead.shader'),version='41core')
    if not program._shader_program.isLinked(): raise RuntimeError('Receiver program did not link')
    return program


class ReceiverPrograms:
    """Exact creating context, independent program/VAO and drain receipt."""
    def __init__(self,gl,context,*,existing_bytes,byte_budget):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLVertexArrayObject
        from PyQt6.QtCore import Qt
        if (type(existing_bytes) is not int or existing_bytes<0 or type(byte_budget) is not int
                or byte_budget<=0): raise ValueError('Whole receiver program ledger required')
        if existing_bytes+PROGRAM_BYTES>byte_budget: raise MemoryError('Receiver programs exceed retained budget')
        if QOpenGLContext.currentContext() is not context: raise RuntimeError('Receiver programs require their creating context')
        self.gl,self.context=gl,context
        self.retained_bytes=PROGRAM_BYTES
        self.programs={}; self.vao=None; self._retirement=None; self._busy=False
        self.closed=self.quarantined=False
        owner_ref=ref(self)
        def destroyed():
            owner=owner_ref()
            if owner is not None:
                try:owner.close()
                except Exception:pass  # Retain uncertain programs rather than claim drain.
        try:
            self._retirement=destroyed
            context.aboutToBeDestroyed.connect(destroyed,Qt.ConnectionType.DirectConnection)
            with self._operation():
                for stage in ('depth','identity','record','lookup'):
                    for solid in (False,True):self._create(stage,solid)
                self._create('seed',False)
                self._create('query',False)
                self.vao=QOpenGLVertexArrayObject()
                if not self.vao.create(): raise RuntimeError('Receiver query vertex array unavailable')
            self._current()
            if gl.glGetError():raise RuntimeError('Receiver program preparation failed')
        except Exception as error:self._quarantine(error)

    def _create(self,stage,solid):
        return create_program(stage,solid,lambda shader:self.programs.__setitem__((stage,solid),shader))

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Receiver programs require their live creating context')

    def _quarantine(self,error):
        self.quarantined=True
        if not any(owner is self for owner in _uncertain):_uncertain.append(self)
        raise GeometryUncertain(self,'Receiver program lifetime is uncertain') from error

    @contextmanager
    def _operation(self):
        self._current()
        if self._busy:raise RuntimeError('Receiver program work is already admitted')
        self._busy=True
        try:
            with (preserved_state(self.gl,self.context,exact_context=True),
                  preserved_samples(self.gl,self.context,texture_units=(0,1),exact_context=True)):
                yield
                self._current()
                if self.gl.glGetError():raise RuntimeError('Receiver program graphics receipt failed')
            self._current()
            if self.gl.glGetError():raise RuntimeError('Receiver program host restoration failed')
        except Exception as error:self._quarantine(error)
        finally:self._busy=False

    def _draw(self, shader, values, mapping, mesh, model, normal, setup):
        from UM.View.RenderBatch import RenderBatch
        from UM.View.GL.OpenGL import OpenGL
        self._current()
        # RenderBatch always activates Uranium's singleton context before its
        # first GL call. Refuse a foreign owner before it can switch contexts.
        if OpenGL.getInstance()._context is not self.context:
            raise RuntimeError('Original receiver requires the native render context')
        try:
            values.apply(shader);self._current()
            mapping.apply(shader);self._current()
            def checked(gl):
                self._bound(shader)
                setup(gl)
                self._current()
            batch=RenderBatch(shader,type=RenderBatch.RenderType.Solid,backface_cull=True,state_setup_callback=checked)
            batch.addItem(thaw_uniform(model),mesh=mesh,**({'normal_transformation':thaw_uniform(normal)} if normal is not None else {}))
            batch.render(values.camera);self._current()
        finally:
            try:
                self._current();shader.release();self._current()
            finally:
                self._current();mapping.release_bindings();self._current()

    def _bound(self,shader):
        self._current()
        if int(self.gl.glGetIntegerv(0x8B8D))!=shader._shader_program.programId():
            raise RuntimeError('Original receiver program was not bound')

    @contextmanager
    def read(self):
        """Pin borrowed lookup programs through the original final CAD draw."""
        with self._operation():
            yield self.programs['lookup',False],self.programs['lookup',True]

    def collect(self,stage,capture,plan,values,mapping):
        """Original meshes, stable original ranges and per-draw visibility."""
        if stage not in ('depth','identity','record'):raise ValueError('Receiver collection stage required')
        self._plan(plan,values)
        with self._operation():
            index=0
            for batch in plan:
                solid=batch.translucent is None
                shader=self.programs[stage,solid]
                shader.setUniformValue('u_depthOnly',0)
                for frozen in (batch.opaque,batch.translucent):
                    if frozen is None:continue
                    self._draw(shader,values,mapping,frozen.mesh,batch.model,batch.normal,
                        lambda gl,index=index,shader=shader:capture.deliver(shader._shader_program,index))
                    index+=1

    @staticmethod
    def _plan(plan,values):
        from .ToolheadEnvironmentReceiver import ReceiverBatch,ReceiverValues
        if (type(values) is not ReceiverValues or type(plan) is not tuple or not 0<len(plan)<=256
                or any(type(batch) is not ReceiverBatch or batch.values is not values for batch in plan)):
            raise ValueError('Complete original receiver plan and values required')

    def seed(self,capture,depth,plan,values,mapping):
        """Copied scene depth plus the same alpha-aware opaque head prepass."""
        self._plan(plan,values)
        with self._operation():
            if depth.seed(capture) is not True:return False
            shader=self.programs['seed',False]
            shader.setUniformValue('u_depthOnly',1)
            def setup(gl):
                gl.glColorMask(False,False,False,False);gl.glDepthMask(True)
                gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LESS);gl.glDisable(gl.GL_BLEND)
            try:
                for batch in plan:
                    if batch.opaque is not None:
                        self._draw(shader,values,mapping,batch.opaque.mesh,batch.model,batch.normal,setup)
                return True
            finally:
                try:
                    self._current();shader.setUniformValue('u_depthOnly',0);self._current()
                finally:
                    self._current();self.gl.glColorMask(True,True,True,True);self._current()

    def query(self,source,textures,far):
        """LayerCapture limits the scissor to one16x16 tile, never a CAD draw."""
        if (type(textures) is not tuple or len(textures)!=2 or
                any(type(texture) is not int or texture<=0 for texture in textures)
                or type(far) not in (int,float) or not math.isfinite(far) or far<=0):
            raise ValueError('Two owned query textures and finite probe distance required')
        self._current()
        box=tuple(map(int,self.gl.glGetIntegerv(0x0C10)))
        if (not self.gl.glIsEnabled(0x0C11) or len(box)!=4 or min(box[:2])<0
                or any(not 0<value<=16 for value in box[2:])):
            raise ValueError('Separate receiver queries require a bounded sixteen-pixel tile')
        with self._operation():
            shader=self.programs['query',False]
            bind_sampler=procedure(self.context,'glBindSampler',None,ctypes.c_uint,ctypes.c_uint)
            for unit,texture in enumerate(textures):
                self.gl.glActiveTexture(0x84C0+unit);self.gl.glBindTexture(0x0DE1,texture);bind_sampler(unit,0)
            try:
                shader.setUniformValue('mpf_receiverOriginTexture',0);shader.setUniformValue('mpf_receiverRayTexture',1)
                shader.setUniformValue('u_probeFar',far);source.geometry.apply(shader);self._current()
                self.vao.bind();shader.bind();self._bound(shader);self.gl.glDrawArrays(self.gl.GL_TRIANGLES,0,3)
            finally:
                try:
                    self._current();shader.release();self._current()
                finally:
                    self._current();self.vao.release();self._current()

    def close(self):
        if self.closed:return
        if self._busy:self._quarantine(RuntimeError('Receiver program work is admitted'))
        try:
            self._current()
            self._busy=True
            with preserved_state(self.gl,self.context,exact_context=True):
                self.gl.glFinish();self._current()
                if self.gl.glGetError():raise RuntimeError('Receiver program drain failed')
                for shader in self.programs.values():
                    shader.setTexture(7,None);self._current()
                    shader.setTexture(6,None);self._current()
                    shader.release();self._current()
                if self.vao is not None:self.vao.destroy()
            self._current()
            if self.gl.glGetError():raise RuntimeError('Receiver program retirement failed')
            if self._retirement is not None:self.context.aboutToBeDestroyed.disconnect(self._retirement)
            self.programs={};self.vao=self._retirement=None;self.closed=True;self.retained_bytes=0
        except Exception as error:self._quarantine(error)
        finally:self._busy=False
