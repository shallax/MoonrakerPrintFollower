"""Live complete static-receiver correction for the ordinary environment path.

Own the full request graph until its last reader drains. Camera movement never
reuses an old pixel lookup; moving rotors retain their ordinary reflection path.
"""
from itertools import count
import time

from .ToolheadEnvironmentLayerFrame import LayerFrame, LayerFrameRequest, LayerPose
from .ToolheadEnvironmentReceiver import ReceiverValues, ReceiverStatic, ReceiverMap
from .ToolheadEnvironmentDepth import ReceiverDepth
from .ToolheadEnvironmentPrograms import ReceiverPrograms
from .ToolheadEnvironmentGeometry import GeometryUncertain
from .ToolheadCaptureValues import CaptureMesh
from .ToolheadSampleTarget import ToolheadSampleTarget

BUDGET = 768*1024**2
_tokens = count(1)
_retiring = set()


class EnvironmentFrame:
    def __init__(self,node,gl,context):
        self.node,self.gl,self.context=node,gl,context
        self.key=self.failed_key=None
        self.frame=self.programs=self.depth=self.static=self.values=self.mapping=None
        self.hold=None;self.external_bytes=0;self.orphans=[]
        self.closed=self.closing=self.quarantined=False
        self.failure='';self.started=0.

    @property
    def retained_bytes(self):
        # LayerFrame's request ledger owns static/depth/program/source charges.
        orphans=sum(getattr(owner,'retained_bytes',0) for owner in self.orphans)
        if self.frame is not None:return self.frame.retained_bytes+orphans
        return orphans+sum(getattr(owner,'retained_bytes',0) for owner in (self.programs,self.depth,self.static))

    def _current(self):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined or QOpenGLContext.currentContext() is not self.context:
            raise RuntimeError('Environment correction requires its original context')

    def _valid(self):
        return (not self.closing and self.key is not None and self.mapping is not None
            and self.node._environment is self.mapping.owner and self.mapping.current()
            and self.node._lighting_enabled and self.node._reflections_enabled
            and self.static is not None and all(mesh.source_current() for mesh in self.static.meshes))

    def _drain(self, *, programs=False):
        self._current()
        # Storage readers retire before any borrowed shader/mesh/depth/source.
        if self.frame is not None:self.frame.close();self.frame=None
        if self.depth is not None:self.depth.close();self.depth=None
        self.gl.glFinish()
        if self.gl.glGetError():raise RuntimeError('Environment receiver drain failed')
        if self.static is not None:
            from UM.View.GL.OpenGL import OpenGL
            for frozen in self.static.meshes:
                for name in (OpenGL.VertexBufferProperty,OpenGL.IndexBufferProperty):
                    buffer=getattr(frozen.mesh,name,None)
                    if buffer is not None:
                        buffer.destroy();self._current()
                        if buffer.isCreated():raise RuntimeError('Owned receiver upload did not retire')
                        delattr(frozen.mesh,name)
            self.static=None
        if programs and self.programs is not None:self.programs.close();self.programs=None
        if self.hold is not None:
            owner,token=self.hold;owner.release_recovery(token);self.hold=None
        self.mapping=self.values=None

    def _failed(self,error):
        self.failure=str(error)[:160];self.failed_key=self.key
        if isinstance(error,GeometryUncertain):
            if error.owner is not self and all(error.owner is not owner for owner in (self.frame,self.programs,self.depth,self.static,*self.orphans)):
                self.orphans.append(error.owner)
            self.quarantined=True;_retiring.add(self);raise error
        try:self._drain()
        except Exception as failure:
            self.quarantined=True;_retiring.add(self)
            raise GeometryUncertain(self,'Environment request retirement uncertain') from failure

    def step(self,key):
        self._current()
        if self.closing:return None
        if key!=self.key:
            self.key=key;self.failed_key=None;self.failure=''
            try:self._drain()
            except Exception as error:self._failed(error);return None
        if self.frame is None:return None
        if not self._valid():
            self._failed(RuntimeError('Environment source changed'));return None
        try:
            deadline=time.monotonic()+.002
            while True:
                if self.frame.step(self.gl,self.context,existing_bytes=self.external_bytes):break
                if self.frame.failure:raise RuntimeError(self.frame.failure)
                if time.monotonic()>=deadline:break
            if self.frame.identity is None:self.wake()
            return self.frame.identity
        except Exception as error:self._failed(error);return None

    def prepare(self,camera,output,key,seeded,viewport,crop):
        self._current()
        if self.closing or key!=self.key or key==self.failed_key or self.frame is not None:return
        if not seeded or type(output) is not ToolheadSampleTarget:
            self.failure='Original four-sample scene depth unavailable';self.failed_key=key;return
        owner=self.node._environment
        if owner is None or not getattr(owner,'recovery_ready',False):
            self.failure='Waiting for a supported completed reflection source';return
        try:
            mapping=ReceiverMap.freeze(owner)
            if mapping is None:return
            token=owner.retain_recovery(mapping.bindings)
            self.hold=(owner,token);self.mapping=mapping
            width,height=output.width(),output.height()
            # Conservatively include current/old ordinary graphics, retained
            # cubemaps/source inputs and original receiver CPU+GPU publications.
            external=width*height*228+self.node._ordinary_retained_graphics(width,height,4)
            external+=sum(old.retained_bytes+old.external_bytes for old in _retiring if old is not self)
            source=mapping.result.frame
            external+=source.retained_source_bytes()+source.capture_storage_bytes()+64*1024**2
            external+=mapping.result.geometry.retained_bytes
            originals=[self.node.getMeshData(),self.node._translucent_mesh,*self.node._static_transparent]
            originals.extend(mesh for pair in self.node._rotor_meshes.values() for mesh in pair)
            unique={id(mesh):mesh for mesh in originals if mesh is not None}
            external+=sum(CaptureMesh.freeze(mesh).retained_bytes()*2 for mesh in unique.values())
            self.external_bytes=external
            if self.programs is None:self.programs=ReceiverPrograms(self.gl,self.context,existing_bytes=external,byte_budget=BUDGET)
            self.values=ReceiverValues.freeze(self.node,camera)
            self.static=ReceiverStatic.freeze(self.node,self.values,
                existing_bytes=external+self.programs.retained_bytes,byte_budget=BUDGET)
            request_key=(next(_tokens),)
            self.depth=ReceiverDepth(self.gl,self.context,output,key=request_key,
                existing_bytes=external+self.programs.retained_bytes+self.static.retained_bytes,byte_budget=BUDGET)
            plan=self.static.plan;draws=[];meshes=[];base=0
            for batch in plan:
                for frozen in (batch.opaque,batch.translucent):
                    if frozen is None:continue
                    draws.append((base,frozen.count,frozen is batch.opaque))
                    meshes.append((frozen.mesh,base,frozen.count));base+=frozen.count
            pose=LayerPose(mapping.source_key,tuple(draws),tuple(meshes),
                (self.programs.programs['lookup',False],self.programs.programs['lookup',True]),
                lambda target:self.programs.seed(target,self.depth,plan,self.values,mapping),
                lambda stage,target:self.programs.collect(stage,target,plan,self.values,mapping),
                mapping.query_scope,
                lambda source,textures:self.programs.query(source,textures,mapping.result.descriptor.far))
            leases=(self.programs,self.depth,self.static,self.values,mapping)
            receipt=sum(owner.retained_bytes for owner in (self.programs,self.depth,self.static))
            request=LayerFrameRequest(request_key,width,height,4,output.positions,(pose,),leases,receipt,self._valid)
            self.frame=LayerFrame(byte_budget=BUDGET);self.frame.select(request)
            self.started=time.monotonic();self.failure='';self.wake()
        except Exception as error:self._failed(error)

    def draw(self,original=None):
        if self.frame is None or not self._valid() or self.frame.identity is None:return False
        draw=self.frame.draw(0)
        if draw is None:return False
        plan=self.static.plan if original is None else (self.static.batch_for(original),)
        if not plan or any(batch is None for batch in plan):return False
        with self.programs.read():
            for batch in plan:batch.draw(self.node,self.values,self.mapping,layer_draw=draw)
        return True

    @property
    def status(self):
        if self.failure:return 'Environment map · correction unavailable: '+self.failure
        if self.frame is not None and self.frame.identity is not None:return 'Environment map + geometry correction'
        return 'Environment map · preparing complete correction'

    def wake(self):
        owner=self.node._environment
        window=getattr(owner,'_window',None)
        if window is not None:window.update()

    def close(self):
        if self.closed:return
        self.closing=True
        from PyQt6.QtGui import QOpenGLContext
        if QOpenGLContext.currentContext() is self.context:
            try:self._drain(programs=True);self.closed=True;_retiring.discard(self)
            except Exception as error:
                self.quarantined=True;_retiring.add(self)
                raise GeometryUncertain(self,'Environment frame retirement uncertain') from error
            return
        if self in _retiring:return
        _retiring.add(self)
        from PyQt6.QtCore import QRunnable
        owner=self
        class Retire(QRunnable):
            def run(self):
                if QOpenGLContext.currentContext() is not owner.context:
                    owner.quarantined=True;return
                try:owner.close()
                except Exception:pass  # Whole graph remains rooted.
        window=getattr(getattr(self.mapping,'owner',None),'_window',None)
        if window is not None:window.scheduleRenderJob(Retire(),window.RenderStage.BeforeRenderingStage);window.update()
        else:self.quarantined=True
