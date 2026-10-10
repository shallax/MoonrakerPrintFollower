"""Owned receiver values for delayed layers; no live scene lookup during draws.

The parent admits the full source/old/new ledger before calling these helpers.
Meshes are copied once into owned immutable native MeshData; RenderBatch can
use its real upload ABI without borrowing a mutable host GPU cache. Camera and
uniform delivery reconstruct fresh native values from plain frozen tuples.
"""
from dataclasses import dataclass
from contextlib import contextmanager
import numpy as np

from .ToolheadCaptureValues import CaptureMesh, freeze_uniform, thaw_uniform
from .ToolheadEnvironmentLayerGL import _mesh_certificate
from .ToolheadEnvironment import TEXTURE_UNIT, DEPTH_UNIT
from .ToolheadEnvironmentPaths import _layout

METADATA_BYTES = 64*1024


class _UploadArray(np.ndarray):
    # Cura's existing attribute upload ABI still calls ndarray.tostring.
    # This explicit immutable array owns no host-cache forwarding mechanism.
    def tostring(self): return self.tobytes()


@dataclass(frozen=True)
class ReceiverCamera:
    view: object
    projection: object
    eye: object
    light: object

    @classmethod
    def freeze(cls, camera):
        return cls(*(freeze_uniform(value) for value in (
            camera.getInverseWorldTransformation(), camera.getProjectionMatrix(),
            camera.getWorldPosition(), camera.getCameraLightPosition())))

    def getInverseWorldTransformation(self): return thaw_uniform(self.view)
    def getProjectionMatrix(self): return thaw_uniform(self.projection)
    def getWorldPosition(self): return thaw_uniform(self.eye)
    def getCameraLightPosition(self): return thaw_uniform(self.light)


@dataclass(frozen=True)
class ReceiverValues:
    """Original PBR inputs, excluding independently pinned map textures."""
    camera: ReceiverCamera
    uniforms: tuple

    @classmethod
    def freeze(cls, node, camera):
        frozen = ReceiverCamera.freeze(camera)
        # Read values against the SAME camera publication, not a camera that
        # can change between RenderBatch bindings and view-ray calculation.
        values = node.receiver_uniforms(frozen)
        return cls(frozen, tuple((name, freeze_uniform(value)) for name,value in values.items()))

    def apply(self, shader):
        for name,value in self.uniforms: shader.setUniformValue(name,thaw_uniform(value))


@dataclass(frozen=True)
class ReceiverBatch:
    """Original solid/glass grouping and frozen matrices, not a new order."""
    opaque: object
    translucent: object
    model: object
    normal: object
    values: ReceiverValues

    def draw(self,node,values,mapping,*,layer_draw=None):
        if values is not self.values:
            raise ValueError('Original shutter camera and receiver values required')
        node._draw_mesh(values.camera,self.opaque.mesh if self.opaque is not None else None,
            self.translucent.mesh if self.translucent is not None else None,
            thaw_uniform(self.model),thaw_uniform(self.normal) if self.normal is not None else None,layer_draw=layer_draw,
            receiver_values=values,receiver_map=mapping)


@dataclass(frozen=True)
class ReceiverStatic:
    """Static primitive radiance, collected independently of moving fans.

    Static glass is deliberately NOT precomposited into the animated seed.
    The normal rotor draw plan determines its current colour order, using the
    original mesh identity; only that selected mesh is replaced by its frozen
    publication. Moving rotor receivers keep their ordinary reflection path.
    """
    plan: tuple
    meshes: tuple
    retained_bytes: int

    @classmethod
    def freeze(cls,node,values,*,existing_bytes,byte_budget):
        if (type(values) is not ReceiverValues or type(existing_bytes) is not int or existing_bytes<0
                or type(byte_budget) is not int or byte_budget<=0):
            raise ValueError('Frozen static receiver values and whole ledger required')
        if not node._rotor_meshes:
            shutter=ReceiverShutter.freeze(node,values,(),existing_bytes=existing_bytes,byte_budget=byte_budget)
            return cls(shutter.plans[0],shutter.meshes,shutter.retained_bytes)
        solid=node.getMeshData()
        glass=tuple(mesh for mesh in node._static_transparent if mesh is not None)
        if len(glass)>64 or len(set(map(id,glass)))!=len(glass) or any(mesh is solid for mesh in glass):
            raise ValueError('Complete unique static receiver partition required')
        raw=(((solid,None),) if solid is not None else ())+tuple((None,mesh) for mesh in glass)
        if not raw:raise ValueError('Visible static receiver partition required')
        model=_receiver_matrix(node.render_transformation())
        normal=_receiver_matrix(node._render_normal,True)
        metadata=METADATA_BYTES+4096*len(raw)
        if existing_bytes+metadata>byte_budget:raise MemoryError('Frozen static metadata exceeds budget')
        owned={};retained=metadata
        for opaque,translucent in raw:
            source=opaque if opaque is not None else translucent
            frozen=ReceiverMesh.freeze(source,existing_bytes=existing_bytes+retained,byte_budget=byte_budget)
            owned[id(source)]=frozen;retained+=frozen.retained_bytes
        if not all(mesh.source_current() for mesh in owned.values()):
            raise ValueError('Static receiver source changed during freeze')
        plan=tuple(ReceiverBatch(owned.get(id(opaque)),owned.get(id(translucent)),model,normal,values)
                   for opaque,translucent in raw)
        return cls(plan,tuple(owned.values()),retained)

    def batch_for(self,original):
        """Match AFTER the original native plan has sorted the actual mesh."""
        for batch in self.plan:
            if any(mesh is not None and mesh.source is original for mesh in (batch.opaque,batch.translucent)):
                return batch
        return None


def _receiver_matrix(value,optional=False):
    if optional and value is None:return None
    frozen=freeze_uniform(value)
    if frozen.kind!='matrix' or len(frozen.value)!=16 or not np.isfinite(frozen.value).all():
        raise ValueError('Finite original receiver matrix required')
    return frozen


@dataclass(frozen=True)
class ReceiverShutter:
    """One complete original shutter plan; never resample motion during work.

    The caller supplies the same already-sampled rotor poses used for the
    selected frame. All original plans/matrices are copied before mesh copies;
    repeated meshes across poses share a single owned receiver publication.
    This value owns CPU data only; its parent owns uploads and verified drain.
    """
    phase_key: tuple
    fractions: tuple
    plans: tuple
    meshes: tuple
    retained_bytes: int

    @classmethod
    def freeze(cls,node,values,poses,*,existing_bytes,byte_budget):
        if (type(values) is not ReceiverValues or type(poses) is not tuple or len(poses)>64
                or type(existing_bytes) is not int or existing_bytes<0
                or type(byte_budget) is not int or byte_budget<=0):
            raise ValueError('Frozen receiver values, sampled poses and retained ledger required')
        for pose in poses:
            if type(pose) is not tuple or len(pose)!=4 or type(pose[0]) is not dict:
                raise ValueError('Original bounded sampled rotor row required')
            row=pose[0]
            if any(type(row.get(name)) not in (tuple,list) or len(row[name])!=3 for name in ('centre','axis')):
                raise ValueError('Three bounded rotor centre and axis values required')
            if any(type(value) not in (int,float) for value in (*row['centre'],*row['axis'],pose[1],pose[2])):
                raise ValueError('Plain numeric rotor pose scalars required')
        phase_key=tuple((row['body'],tuple(row['centre']),tuple(row['axis']),row['direction'],phase,blur)
                        for row,phase,blur,_label in poses)
        if any(type(body) is not int or not 0<=body<4096 or type(direction) is not int or direction not in (-1,1)
               or not np.isfinite((*centre,*axis,direction,phase,blur)).all() or blur<0
               for body,centre,axis,direction,phase,blur in phase_key):
            raise ValueError('Finite original rotor pose required')
        sampled=tuple((dict(body=body,centre=centre,axis=axis,direction=direction),phase,blur,'')
                      for body,centre,axis,direction,phase,blur in phase_key)
        bodies=tuple(body for body,*_rest in phase_key)
        if len(set(bodies))!=len(bodies) or set(bodies)!=set(node._rotor_meshes):
            raise ValueError('Sampled poses must cover every original rotor body once')
        fractions=(-.5,0.,.5) if any(blur>.02 for _row,_phase,blur,_label in sampled) else (0.,)
        rotating=bool(node._rotor_meshes)
        solid=node.getMeshData(); glass=None if rotating else node._translucent_mesh
        model=_receiver_matrix(node.render_transformation()); normal=_receiver_matrix(node._render_normal,True)
        raw=[]
        for fraction in fractions:
            plan=[(solid,glass,model,normal)] if solid is not None or glass is not None else []
            if rotating:
                for mesh,transparent,transform,normal_matrix in node.rotor_draw_plan(values.camera,sampled,fraction):
                    plan.append((None if transparent else mesh,mesh if transparent else None,
                                 _receiver_matrix(transform),_receiver_matrix(normal_matrix,True)))
            if not plan: raise ValueError('Complete visible receiver shutter required')
            raw.append(tuple(plan))
        metadata=METADATA_BYTES+4096*sum(len(plan) for plan in raw)
        if existing_bytes+metadata>byte_budget: raise MemoryError('Frozen shutter metadata exceeds budget')
        owned={}; retained=metadata
        for plan in raw:
            for opaque,translucent,_model,_normal in plan:
                for mesh in (opaque,translucent):
                    if mesh is not None and id(mesh) not in owned:
                        frozen=ReceiverMesh.freeze(mesh,existing_bytes=existing_bytes+retained,byte_budget=byte_budget)
                        owned[id(mesh)]=frozen; retained+=frozen.retained_bytes
        plans=tuple(tuple(ReceiverBatch(owned.get(id(opaque)),owned.get(id(translucent)),model,normal,values)
                          for opaque,translucent,model,normal in plan) for plan in raw)
        if not all(mesh.source_current() for mesh in owned.values()):
            raise ValueError('Receiver source changed during shutter freeze')
        return cls(phase_key,fractions,plans,tuple(owned.values()),retained)


@dataclass(frozen=True, eq=False)
class ReceiverMesh:
    """Owned native mesh, original-range certificate and conservative receipt.

    ``retained_bytes`` includes the new CPU arrays, complete prospective
    VBO/EBO and the native uploader's temporary byte copies. This conservative
    reservation stays charged until the parent's verified retirement.
    Original publications remain the parent's independently charged leases.
    """
    mesh: object
    source: object
    source_certificate: tuple
    count: int
    retained_bytes: int

    @classmethod
    def freeze(cls, mesh, *, existing_bytes, byte_budget):
        if (type(existing_bytes) is not int or existing_bytes<0
                or type(byte_budget) is not int or byte_budget<=0):
            raise ValueError('Whole retained receiver ledger required')
        count = mesh.getFaceCount() if mesh.hasIndices() else mesh.getVertexCount()//3
        certificate = _mesh_certificate(mesh,count)
        if mesh.hasColors():
            colours=getattr(getattr(mesh,'source',mesh),'_colors',None)
            # CaptureMesh's general small-mesh byte fallback is inappropriate
            # before this boundary's span/copy admission.
            if (not isinstance(colours,np.ndarray) or colours.dtype!=np.float32
                    or colours.shape!=(mesh.getVertexCount(),4)):
                raise ValueError('Original native float32 receiver colours required')
        attributes=tuple((name,mesh.getAttribute(name)['opengl_name'],mesh.getAttribute(name)['opengl_type'],
                          mesh.getAttribute(name)['value']) for name in mesh.attributeNames())
        source_parts=(mesh.getVertices(),mesh.getIndices() if mesh.hasIndices() else None,
            mesh.getNormals() if mesh.hasNormals() else None,
            colours if mesh.hasColors() else None,
            mesh.getUVCoordinates() if mesh.hasUVCoordinates() else None,
            *(array for _name,_gl_name,_kind,array in attributes))
        for array in source_parts:
            if array is not None:
                if array.dtype.hasobject or not array.flags.aligned:
                    raise ValueError('Aligned original receiver arrays required')
                if not array.flags.c_contiguous:
                    raise ValueError('Contiguous original receiver allocation required')
                # No layout normalization or byte fallback precedes this
                # readable allocation certificate.
                _layout(np.asarray(array).reshape(-1,1),array.dtype,1)
        # Do not ask the live mesh for its arrays again after admission. A
        # replacement getter publication must not bypass the span proof.
        frozen = CaptureMesh(id(mesh),*source_parts[:5],attributes)
        frozen.layout()
        parts = (frozen.vertices,frozen.indices,frozen.normals,frozen.colours,frozen.uvs,
                 *(array for _name,_gl_name,_kind,array in frozen.attributes))
        if any(array is not None and array.dtype!=np.float32 for array in (
                frozen.vertices,frozen.normals,frozen.colours,frozen.uvs)):
            raise ValueError('Original native float32 receiver channels required')
        if any(kind not in ('float','int','vector2f','vector4f') or array.dtype!=
                (np.dtype('int32') if kind=='int' else np.dtype('float32'))
                for _name,_gl_name,kind,array in frozen.attributes):
            raise ValueError('Original native receiver attribute ABI required')
        cpu = sum(array.nbytes for array in parts if array is not None)
        retained = 3*cpu+METADATA_BYTES
        if existing_bytes+retained>byte_budget:
            raise MemoryError('Combined frozen receiver copy exceeds budget')
        def owned(array):
            if array is None: return None
            result = np.array(array,copy=True,order='C',subok=False).view(_UploadArray)
            result.setflags(write=False)
            result.base.setflags(write=False)
            return result
        from UM.Mesh.MeshData import MeshData
        result = MeshData(vertices=owned(frozen.vertices),indices=owned(frozen.indices),
            normals=owned(frozen.normals),colors=owned(frozen.colours),uvs=owned(frozen.uvs),
            attributes={name:dict(opengl_name=gl_name,opengl_type=kind,value=owned(array))
                        for name,gl_name,kind,array in frozen.attributes})
        if _mesh_certificate(mesh,count)!=certificate:
            raise ValueError('Receiver source changed during freeze')
        clone = CaptureMesh.freeze(result)
        if clone.retained_bytes()!=cpu:
            raise ValueError('Native receiver owns unexpected backing allocations')
        return cls(result,mesh,certificate,count,retained)

    def source_current(self):
        # This certifies publication descriptors, not arbitrary writes through
        # a host's hidden mutable aliases. The parent must also verify its
        # authoritative model generation; draws use ONLY our owned arrays.
        return _mesh_certificate(self.source,self.count)==self.source_certificate


@dataclass(frozen=True)
class ReceiverSource:
    """Compact admitted identity plus the actual leased geometry object."""
    key: tuple
    geometry: object


@dataclass(frozen=True, eq=False)
class ReceiverMap:
    """Pin one completed map/source payload; never re-resolve today's owner."""
    owner: object
    bindings: object
    result: object
    selected_key: tuple
    source_key: tuple
    geometry_key: tuple
    uniforms: tuple

    @classmethod
    def freeze(cls, owner):
        if not owner.recovery_ready: return None
        bindings=owner._storage
        result=bindings.result
        descriptor=result.descriptor
        values={'u_environmentEnabled':1,'u_environment':TEXTURE_UNIT,'u_sceneDepth':DEPTH_UNIT,
            'u_probe':list(descriptor.origin),'u_sceneMin':list(descriptor.minimum),
            'u_sceneMax':list(descriptor.maximum),'u_probeNear':descriptor.near,'u_probeFar':descriptor.far}
        # The full geometry certificate can contain many matrices/palettes.
        # LayerFrame uses a compact identity while this pin retains and checks
        # the complete certificate inside the actual consumer admission.
        source_key=(id(owner),id(bindings),result.serial,id(result.geometry),result.geometry.epoch)
        frozen=cls(owner,bindings,result,owner._selected_key,source_key,result.geometry.key,
                   tuple((name,freeze_uniform(value)) for name,value in values.items()))
        return frozen if frozen.current() else None

    def current(self):
        owner=self.owner
        return (not owner._closed and not owner._main_retired and not owner._quarantine
            and owner.available and owner._storage is self.bindings
            and self.bindings.result is self.result and self.result.key==self.selected_key
            and owner._selected_key==self.selected_key and self.result.geometry.key==self.geometry_key
            and id(self.result.geometry)==self.source_key[3] and self.result.geometry.epoch==self.source_key[4])

    @contextmanager
    def query_scope(self):
        if not self.current(): yield None; return
        with self.bindings.query(self.selected_key,self.geometry_key) as geometry:
            yield ReceiverSource(self.source_key,geometry) if geometry is self.result.geometry and self.current() else None

    def apply(self, shader):
        if not self.current(): raise RuntimeError('Frozen environment publication changed')
        self.owner._shaders.add(shader)
        for name,value in self.uniforms: shader.setUniformValue(name,thaw_uniform(value))
        shader.setTexture(TEXTURE_UNIT,self.bindings)
        shader.setTexture(DEPTH_UNIT,self.bindings.depth)

    def release_bindings(self):
        # Never release a newer owner's binding after a completed-map swap.
        try:self.bindings.release(TEXTURE_UNIT)
        finally:self.bindings.release(DEPTH_UNIT)
