"""Portable native-buffer admission/transaction ownership tests, without Cura."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from mpf.toolhead import ToolheadPathGeometry as module


class Mesh:
    def __init__(self):
        self.vertices=np.arange(9,dtype=np.float32).reshape(3,3);self.vertices.flags.writeable=False
        self.indices=np.array([0,1,1,2],np.int32);self.indices.flags.writeable=False
        self.types=np.array([1,8,2],np.float32);self.types.flags.writeable=False
        self.attributes={'line_types':dict(value=self.types,opengl_type='float',opengl_name='a_line_type')}
        self.previous()
    def previous(self):
        self.attributes['prev_line_types']=dict(value=np.array([8,1,8],np.float32),
            opengl_type='float',opengl_name='a_prev_line_type')
    def getVertexCount(self):return 3
    def getVertices(self):return self.vertices
    def getIndices(self):return self.indices
    def getNormals(self):return None
    def getUVCoordinates(self):return None
    def attributeNames(self):return sorted(self.attributes)
    def getAttribute(self,name):return self.attributes[name]


class NativeLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.group=object();self.context=SimpleNamespace(shareGroup=lambda:self.group)
        self.current=self.context;self.bound=987;self.errors=[];self.allocations=[]
        owner=self
        class Buffer:
            def __init__(self,mesh):
                self.group=owner.group;self.valid=True
                self.data=mesh.vertices.tobytes()+b''.join(mesh.getAttribute(n)['value'].tobytes() for n in mesh.attributeNames())
                self.name=len(owner.allocations)+1;owner.allocations.append(self)
            def bind(self):
                if not self.valid or owner.current.shareGroup() is not self.group:return False
                owner.bound=self.name;return True
            def size(self):return len(self.data)
            def bufferId(self):return self.name
        class API:
            VertexBufferProperty='_test_native_buffer'
            def createVertexBuffer(self,mesh,force_recreate=False):
                cached=getattr(mesh,self.VertexBufferProperty,None)
                if cached is not None and not force_recreate:return cached
                result=Buffer(mesh);setattr(mesh,self.VertexBufferProperty,result);return result
            def getBindingsObject(self):return owner.gl
        self.api=API();API.getInstance=staticmethod(lambda:self.api)
        self.API=API;self.Buffer=Buffer
        self.gl=SimpleNamespace(glGetIntegerv=lambda _:self.bound,
            glBindBuffer=lambda _,name:setattr(self,'bound',name),
            glGetError=lambda:self.errors.pop(0) if self.errors else 0)
        self.logger=Mock()
        self.modules=patch.dict('sys.modules',{'UM.View.GL.OpenGL':SimpleNamespace(OpenGL=API),
            'PyQt6.QtOpenGL':SimpleNamespace(QOpenGLBuffer=Buffer),
            'PyQt6.QtGui':SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda:self.current)),
            'UM.Logger':SimpleNamespace(Logger=self.logger)})
        self.modules.start();self.addCleanup(self.modules.stop)
        for registry in (module._native_uploads,module._native_candidates,module._native_logged):registry.clear()
        self.addCleanup(module._native_uploads.clear);self.addCleanup(module._native_candidates.clear)
    def observe(self,mesh):
        return module.observe_native_upload(mesh,self.context,self.API.createVertexBuffer,source_type=Mesh)
    def existing(self):
        mesh=Mesh();old=self.api.createVertexBuffer(mesh);observation=self.observe(mesh)
        self.assertIsNotNone(observation)
        self.assertFalse(module.admit_native_upload(mesh,self.context,observation))
        return mesh,old,observation

    def test_new_upload_retains_exact_generation_and_restores_binding(self):
        mesh=Mesh();observation=self.observe(mesh);old=self.api.createVertexBuffer(mesh)
        self.logger.log.side_effect=RuntimeError('optional logger failed')
        self.assertTrue(module.admit_native_upload(mesh,self.context,observation))
        self.assertEqual(self.bound,987)
        proof=module._native_upload(mesh,self.context);proxy=module.PathVertexMesh(mesh,native=proof)
        self.assertIs(proxy._borrowed_native,old)
        self.assertFalse(proxy.getAttribute('prev_line_types')['value'].flags.writeable)
        expected=old.data
        observation=self.observe(mesh);new=self.api.createVertexBuffer(mesh,force_recreate=True)
        self.assertTrue(module.admit_native_upload(mesh,self.context,observation))
        self.assertIs(module._native_upload(mesh,self.context)[1],new)
        self.assertEqual(proxy._borrowed_native.data,expected)
        self.assertIsNone(module._native_upload(mesh,SimpleNamespace(shareGroup=lambda:object())))
        mesh.vertices=mesh.vertices.copy();mesh.vertices.flags.writeable=False
        self.assertIsNone(module._native_upload(mesh,self.context))
        self.assertNotIn(mesh,module._native_uploads)

    def test_first_upload_replaces_existing_once_and_restores_caller_binding(self):
        mesh,old,_=self.existing();self.bound=789
        proof=module._first_native_upload(mesh,self.context)
        self.assertIsNotNone(proof);self.assertIsNot(proof[1],old)
        self.assertEqual(proof[1].data,old.data);self.assertEqual(len(self.allocations),2)
        self.assertEqual(self.bound,789)
        self.assertIsNone(module._first_native_upload(mesh,self.context))
        self.assertNotIn(mesh,module._native_candidates)

    def test_failed_replacement_restores_cache_owner_and_drains_gl_errors(self):
        mesh,old,_=self.existing();self.errors=[0,0x505,0];self.bound=123
        self.assertIsNone(module._first_native_upload(mesh,self.context))
        self.assertIs(getattr(mesh,self.API.VertexBufferProperty),old)
        self.assertEqual(self.bound,123);self.assertEqual(self.errors,[])
        self.assertNotIn(mesh,module._native_uploads);self.assertNotIn(mesh,module._native_candidates)

    def test_retained_private_proxy_blocks_replacement_and_unknown_writer_declines(self):
        mesh,old,_=self.existing();proxy=module.PathVertexMesh(mesh)
        setattr(proxy,self.API.VertexBufferProperty,old)
        self.assertIsNone(module._first_native_upload(mesh,self.context))
        self.assertEqual(len(self.allocations),1)
        del proxy
        mesh,old,_=self.existing()
        with patch.object(self.api,'createVertexBuffer',Mock(side_effect=AssertionError('must not upload'))):
            self.assertIsNone(self.observe(mesh))
            self.assertIsNone(module._first_native_upload(mesh,self.context))
        self.assertIs(getattr(mesh,self.API.VertexBufferProperty),old)

    def test_observation_and_admission_reject_mutation_layout_and_foreign_context(self):
        mesh=Mesh();observation=self.observe(mesh)
        self.assertIs(observation[2][0],mesh.vertices)
        self.assertIsNone(module.observe_native_upload(mesh,None,self.API.createVertexBuffer,source_type=Mesh))
        self.assertIsNone(module.observe_native_upload(mesh,self.context,lambda:None,source_type=Mesh))
        self.api.createVertexBuffer(mesh)
        original=mesh.attributes['prev_line_types']
        for field,value in (('opengl_type','int'),('opengl_name','wrong'),('value',np.zeros(3,np.float64)),
                ('value',np.array([1,1,8],np.float32)),('value',np.array([8,2,8],np.float32))):
            mesh.attributes['prev_line_types']={**original,field:value}
            self.assertFalse(module.admit_native_upload(mesh,self.context,observation))
        mesh.attributes['prev_line_types']=original
        self.current=SimpleNamespace(shareGroup=lambda:object())
        self.assertIsNone(self.observe(mesh));self.assertFalse(module.admit_native_upload(mesh,self.context,observation))
        self.current=self.context
        mesh.attributes['line_types']['opengl_type']='unknown'
        self.assertIsNone(self.observe(mesh))
        mesh.attributes['line_types']['opengl_type']='float';mesh.vertices.flags.writeable=True
        self.assertIsNone(self.observe(mesh));self.assertFalse(module.admit_native_upload(mesh,self.context,observation))
        mesh.vertices.flags.writeable=False;mesh.indices.flags.writeable=True
        self.assertIsNone(self.observe(mesh))

    def test_stale_candidate_and_invalid_buffer_cannot_publish(self):
        mesh,old,_=self.existing();old.valid=False
        self.assertIsNone(module._first_native_upload(mesh,self.context))
        self.assertIs(getattr(mesh,self.API.VertexBufferProperty),old)
        mesh,old,observation=self.existing();old.data=b'wrong size'
        self.assertFalse(module.admit_native_upload(mesh,self.context,observation))
        self.assertFalse(module.admit_native_upload(mesh,self.context,None))
        setattr(mesh,self.API.VertexBufferProperty,object())
        self.assertFalse(module.admit_native_upload(mesh,self.context,observation))
        self.assertIsNone(module._native_upload([],self.context))
        self.assertIsNone(module._first_native_upload([],self.context))
