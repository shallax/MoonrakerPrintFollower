"""Complete receiver groups joined to one frozen view and source publication.

The scene adapter supplies immutable receiver/program/source leases, original
draw callbacks and their complete byte receipt. No callback may consult a newer
scene publication. This owner selects before pumping and never exposes an
unfinished pose or a completed image from a different camera/depth/pose key.
"""
from dataclasses import dataclass
from itertools import count
from weakref import ref

from .ToolheadEnvironmentPaths import _generation
from .ToolheadEnvironmentLayers import METADATA_RESERVE
from .ToolheadEnvironmentLayerCapture import LayerCapture
from .ToolheadEnvironmentLayerGL import LayerStorage, LayerDraw, VALIDATION_SCRATCH
from .ToolheadEnvironmentGeometry import GeometryUncertain

_epochs = count(1)
_uncertain = []


@dataclass(frozen=True, eq=False)
class LayerPose:
    """One original shutter pose; all source reads take the exact pinned ticket."""
    source_key: tuple
    draws: tuple
    meshes: tuple
    programs: tuple
    seed: object
    draw: object
    query_scope: object
    query: object


@dataclass(frozen=True, eq=False)
class LayerFrameRequest:
    """Key includes view/crop/depth/head/material/light and the FULL pose group.

    ``leases`` roots immutable camera/mesh/source/program DTOs through teardown;
    ``lease_bytes`` includes their backings, GPU owners and callback metadata.
    ``verify`` checks the selected scene publication, not GPU completion. The
    pose's query scope independently grants the actual completed source lease.
    """
    key: tuple
    width: int
    height: int
    samples: int
    positions: tuple | None
    poses: tuple
    leases: tuple
    lease_bytes: int
    verify: object

    def __post_init__(self):
        if (type(_generation(self.key)) is not tuple
                or any(type(v) is not int or not 0<v<=8192 for v in (self.width,self.height))
                or type(self.samples) is not int or self.samples not in (1,4)
                or type(self.poses) is not tuple or not 0<len(self.poses)<=32
                or type(self.leases) is not tuple or not self.leases
                or type(self.lease_bytes) is not int or self.lease_bytes<METADATA_RESERVE
                or not callable(self.verify)):
            raise ValueError('Complete frozen receiver group and lease receipt required')
        if ((self.samples==1 and self.positions is not None)
                or (self.samples==4 and (type(self.positions) is not tuple or len(self.positions)!=4
                    or len(set(self.positions))!=4))):
            raise ValueError('Original sample pattern required')
        for pose in self.poses:
            if (type(pose) is not LayerPose or type(_generation(pose.source_key)) is not tuple
                    or type(pose.draws) is not tuple or type(pose.meshes) is not tuple
                    or len(pose.draws)!=len(pose.meshes) or not pose.meshes
                    or type(pose.programs) is not tuple or len(pose.programs)!=2
                    or any(not callable(callback) for callback in
                           (pose.seed,pose.draw,pose.query_scope,pose.query))):
                raise ValueError('Original pose callbacks, meshes and programs required')
            for descriptor,mesh in zip(pose.draws,pose.meshes,strict=True):
                if (type(descriptor) is not tuple or len(descriptor)!=3
                        or type(mesh) is not tuple or len(mesh)!=3 or mesh[1:]!=descriptor[:2]):
                    raise ValueError('Capture and lookup require the same original primitive ranges')


class LayerFrame:
    """Pump separately from cached fallback pixels; publish all poses atomically."""
    def __init__(self, *, byte_budget):
        if type(byte_budget) is not int or byte_budget<=0: raise ValueError('Layer frame budget required')
        self.byte_budget=byte_budget
        self.selected=self.pending=self.front=None
        self._pending_epoch=None
        self.capture=self.upload=None
        self.storages=[]; self.draws=[]
        self.orphans=[]
        self.epoch=self.revision=0
        self.context=None; self._retirement=None
        self.closed=self.quarantined=False
        self._stepping=False
        self.failure=None

    @property
    def retained_bytes(self):
        requests={id(request):request for request in (self.selected,self.pending,
                  self.front[0] if self.front is not None else None) if request is not None}
        total=sum(request.lease_bytes for request in requests.values())+METADATA_RESERVE
        owners=[self.capture,self.upload,*self.storages,*self.draws,*self.orphans]
        if self.front is not None: owners.extend((*self.front[1],*self.front[2]))
        return total+sum(owner.retained_bytes for owner in owners if owner is not None)

    @property
    def identity(self):
        front,selected,epoch=self.front,self.selected,self.epoch
        if (self.closed or self.quarantined or front is None or selected is not front[0]
                or not selected.verify() or self.front is not front
                or self.selected is not selected or self.epoch!=epoch
                or self.closed or self.quarantined): return None
        return front[0].key,front[3],self.revision

    def _uncertain(self, error):
        if isinstance(error,GeometryUncertain) and error.owner is not self:
            owners=[self.capture,self.upload,*self.storages,*self.orphans]
            if self.front is not None: owners.extend(self.front[1])
            if not any(owner is error.owner for owner in owners): self.orphans.append(error.owner)
        self.quarantined=True; self.failure=str(error)
        if not any(owner is self for owner in _uncertain): _uncertain.append(self)
        raise GeometryUncertain(self,str(error)) from error

    def _drain_pending(self):
        # Clear fields only AFTER every close succeeds; uncertainty retains the
        # request, callbacks and all owners, including already closed handles.
        for owner in (self.capture,self.upload,*self.storages):
            if owner is not None: owner.close()
        self.capture=self.upload=self.pending=None
        self._pending_epoch=None
        self.storages=[]; self.draws=[]

    def select(self, request):
        if self.closed or self.quarantined: raise RuntimeError('Layer frame owner unavailable')
        if request is not None and type(request) is not LayerFrameRequest:
            raise ValueError('Frozen layer request required')
        if self.selected is request: return
        # Selection/epoch precedes retirement, source callbacks and adoption.
        self.selected=request; self.epoch=next(_epochs)
        if self._stepping: return
        try: self._drain_pending()
        except Exception as error: self._uncertain(error)
        self.failure=None

    def _valid(self, request, epoch, pose):
        if self.selected is not request or self.epoch!=epoch or not request.verify(): return False
        with pose.query_scope() as source:
            valid=(source is not None and source.key==pose.source_key)
        return (valid and self.selected is request and self.epoch==epoch and request.verify()
                and self.selected is request and self.epoch==epoch and not self.quarantined)

    def step(self, gl, context, *, existing_bytes):
        if self._stepping: raise RuntimeError('Receiver group already has admitted work')
        self._stepping=True
        try: return self._step(gl,context,existing_bytes=existing_bytes)
        finally:
            self._stepping=False
            if self.pending is not None and (self.pending is not self.selected or self._pending_epoch!=self.epoch):
                try: self._drain_pending()
                except Exception as error: self._uncertain(error)

    def _withdraw(self, request, epoch):
        if self.selected is request and self.epoch==epoch: self.select(None)

    def _step(self, gl, context, *, existing_bytes):
        from PyQt6.QtGui import QOpenGLContext
        if self.closed or self.quarantined: raise RuntimeError('Layer frame owner unavailable')
        if type(existing_bytes) is not int or existing_bytes<0: raise ValueError('Whole retained frame ledger required')
        if QOpenGLContext.currentContext() is not context or self.context not in (None,context):
            raise RuntimeError('Layer frame creating context required')
        if self.context is None:
            self.context=context; owner=ref(self)
            def destroyed():
                instance=owner()
                if instance is not None and not instance.closed:
                    instance.quarantined=True; instance.failure='Layer frame context retired before verified drain'
                    if not any(value is instance for value in _uncertain): _uncertain.append(instance)
            self._retirement=destroyed
            from PyQt6.QtCore import Qt
            context.aboutToBeDestroyed.connect(destroyed,Qt.ConnectionType.DirectConnection)
        request=self.selected; epoch=self.epoch
        if request is None or self.identity is not None: return False
        pose=request.poses[len(self.storages)]
        try:
            if not self._valid(request,epoch,pose):
                self.failure='Receiver source or view publication changed'; self._withdraw(request,epoch); return False
            if existing_bytes+self.retained_bytes>self.byte_budget:
                raise MemoryError('Combined retained receiver group exceeds budget')
            self.pending=request
            self._pending_epoch=epoch
            if self.capture is None and self.upload is None:
                self.capture=LayerCapture(gl,context,request.width,request.height,
                    key=(epoch,len(self.storages)),source_key=pose.source_key,draws=pose.draws,
                    existing_bytes=existing_bytes+self.retained_bytes,byte_budget=self.byte_budget,
                    samples=request.samples,sample_positions=request.positions)
            if self.capture is not None:
                # Ordinary targets may grow while capture is pending. Re-charge
                # the CURRENT external ledger before any dynamic array growth.
                self.capture.builder.existing_bytes=(existing_bytes+self.retained_bytes-self.capture.retained_bytes
                    +self.capture.graphics_bytes+self.capture.metadata_bytes)
                complete=self.capture.step(self.capture.key,seed=pose.seed,draw=pose.draw,
                    query_scope=pose.query_scope,query=pose.query)
                if not self._valid(request,epoch,pose): self._withdraw(request,epoch); return False
                if self.capture.withdrawn:
                    raise RuntimeError('Receiver capture withdrew its incomplete frame')
                if not complete: return False
                self.upload=LayerStorage(gl,context,self.capture.image,epoch=epoch,fallback_key=pose.source_key,
                    existing_bytes=existing_bytes+self.retained_bytes,byte_budget=self.byte_budget)
                self.capture.close(); self.capture=None
            if existing_bytes+self.retained_bytes+VALIDATION_SCRATCH>self.byte_budget:
                raise MemoryError('Current receiver upload ledger exceeds budget')
            if not self.upload.step(self.upload.key): return False
            if not self._valid(request,epoch,pose): self._withdraw(request,epoch); return False
            draw=LayerDraw(self.upload,self.upload.key,epoch,pose.programs,pose.meshes,fallback_scope=pose.query_scope)
            if existing_bytes+self.retained_bytes+draw.retained_bytes>self.byte_budget:
                raise MemoryError('Complete receiver draw group exceeds budget')
            self.storages.append(self.upload); self.draws.append(draw); self.upload=None
            if len(self.storages)!=len(request.poses): return False
            # Every pose/source is still admitted, including earlier shutters.
            if not all(self._valid(request,epoch,value) for value in request.poses): self._withdraw(request,epoch); return False
            if self.selected is not request or self.epoch!=epoch: return False
            if self.front is not None:
                for storage in self.front[1]: storage.close()
                self.front=None
            if self.selected is not request or self.epoch!=epoch: return False
            self.front=(request,tuple(self.storages),tuple(self.draws),epoch)
            self.storages=[]; self.draws=[]; self.pending=None; self._pending_epoch=None
            self.revision+=1; return True
        except GeometryUncertain as error: self._uncertain(error)
        except Exception as error:
            reason=str(error)
            try: self._withdraw(request,epoch)
            except GeometryUncertain: raise
            self.failure=reason; return False

    def draw(self, pose):
        if type(pose) is not int or self.identity is None or not 0<=pose<len(self.front[2]): return None
        return self.front[2][pose]

    def close(self):
        if self.closed: return
        if self.quarantined: raise GeometryUncertain(self,self.failure)
        if self._stepping:
            self.selected=None; self.epoch=next(_epochs)
            self._uncertain(RuntimeError('Receiver group retired during admitted work'))
        try:
            self.select(None)
            if self.front is not None:
                for storage in self.front[1]: storage.close()
            if self._retirement is not None:
                self.context.aboutToBeDestroyed.disconnect(self._retirement)
            self.front=self.context=self._retirement=None; self.closed=True
        except Exception as error: self._uncertain(error)
