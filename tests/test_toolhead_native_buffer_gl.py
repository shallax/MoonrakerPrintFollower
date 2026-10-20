# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead

"""Tracked native uploads reuse exact bytes and preserve leased generations."""
import ast
import ctypes
import types
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PyQt6.QtGui import QOpenGLContext, QOffscreenSurface
from PyQt6.QtOpenGL import QOpenGLBuffer
from tests import test_toolhead_environment_gl as gl_fixture
from mpf.toolhead import ToolheadPathGeometry as module
from mpf.cura import ToolheadSimulationPass as adapter
from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, BufferLease


class LayerData:
    def __init__(self):
        self.vertices=np.array([[0,0,0],[1,2,3],[4,5,6]],np.float32)
        self.vertices.flags.writeable=False
        self.indices=np.array([0,1,1,2],np.int32);self.indices.flags.writeable=False
        self.attributes={'line_types':dict(value=np.array([1,8,2],np.float32).view(module.UploadArray),
            opengl_type='float',opengl_name='a_line_type')}
        self.attributes['line_types']['value'].flags.writeable=False
    def getVertexCount(self):return len(self.vertices)
    def getVertices(self):return self.vertices
    def getIndices(self):return self.indices
    def getNormals(self):return None
    def getUVCoordinates(self):return None
    def hasNormals(self):return False
    def hasColors(self):return False
    def hasUVCoordinates(self):return False
    def getVerticesAsByteArray(self):return self.vertices.tobytes()
    def attributeNames(self):return sorted(self.attributes)
    def getAttribute(self,name):return self.attributes[name]
    def previous(self,wrong=False):
        self.attributes['prev_line_types']=dict(value=np.array([8,1,0 if wrong else 8],np.float32).view(module.UploadArray),
            opengl_type='float',opengl_name='a_prev_line_type')


class NativeBufferGLTests(unittest.TestCase):
    setUpClass=classmethod(gl_fixture.EnvironmentGLTests.setUpClass.__func__)
    tearDownClass=classmethod(gl_fixture.EnvironmentGLTests.tearDownClass.__func__)

    def setUp(self):
        if not self.available:self.skipTest('Offscreen Qt OpenGL unavailable')
        root=Path('/Applications/UltiMaker Cura.app/Contents/Resources')
        if not root.exists():self.skipTest('Installed native uploader fixture unavailable')
        def function(path,owner,name):
            filename=str(root/path);tree=ast.parse(Path(filename).read_text())
            cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name==owner)
            method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==name)
            body=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),method],type_ignores=[])
            ast.fix_missing_locations(body)
            scope={'QOpenGLBuffer':QOpenGLBuffer,'ctypes':ctypes,'Logger':Mock()}
            exec(compile(body,filename,'exec'),scope)
            return scope[name],scope
        create,scope=function('UM/View/GL/OpenGL.py','OpenGL','createVertexBuffer')
        render,_=function('share/cura/plugins/SimulationView/SimulationPass.py','SimulationPass','render')
        self.render=types.MethodType(render,object());self.upload_scope=scope
        api=type('OpenGL',(),{'VertexBufferProperty':'__vertex_buffer','createVertexBuffer':create})
        scope['OpenGL']=api
        self.api=api();self.api.getBindingsObject=lambda:self.gl
        api.getInstance=staticmethod(lambda:self.api)
        self.patch=patch.dict(sys.modules,{'UM.View.GL.OpenGL':types.SimpleNamespace(OpenGL=api),
            'cura.LayerData':types.SimpleNamespace(LayerData=LayerData),
            'UM.Logger':types.SimpleNamespace(Logger=Mock())})
        self.patch.start();self.addCleanup(self.patch.stop)
        module._native_uploads.clear();module._native_candidates.clear()

    def upload(self,mesh,wrong=False):
        observation=adapter.observe_native_upload(mesh,self.context,self.render)
        self.assertIsNotNone(observation)
        mesh.previous(wrong)
        buffer=self.api.createVertexBuffer(mesh,force_recreate=True)
        admitted=module.admit_native_upload(mesh,self.context,observation)
        return buffer,admitted

    def bytes(self,buffer):
        self.assertTrue(buffer.bind());data=(ctypes.c_ubyte*buffer.size())()
        from mpf.toolhead.ToolheadGLState import procedure
        procedure(self.context,'glGetBufferSubData',None,ctypes.c_uint,ctypes.c_ssize_t,ctypes.c_ssize_t,ctypes.c_void_p)(0x8892,0,len(data),data)
        return bytes(data)

    def test_known_native_upload_borrow_has_identical_bytes_and_no_second_allocation(self):
        mesh=LayerData();buffer,admitted=self.upload(mesh);self.assertTrue(admitted)
        geometry=module.ToolheadPathGeometry(mesh)
        upload=geometry.vertex_mesh(self.context)
        self.assertIs(upload._borrowed_native,buffer)
        self.assertIs(self.api.createVertexBuffer(upload),buffer)
        self.assertTrue(np.shares_memory(upload.getAttribute('prev_line_types')['value'],mesh.getAttribute('prev_line_types')['value']))
        expected=mesh.vertices.tobytes()+b''.join(mesh.getAttribute(name)['value'].tobytes() for name in mesh.attributeNames())
        self.assertEqual(self.bytes(buffer),expected)
        frozen=CaptureMesh.freeze(upload);layout,size=frozen.layout()
        lease=BufferLease(buffer.bufferId(),size,layout);lease.validate(frozen)
        old=upload
        replacement,admitted=self.upload(mesh);self.assertTrue(admitted)
        newer=geometry.vertex_mesh(self.context)
        self.assertIsNot(newer,old);self.assertIs(newer._borrowed_native,replacement)
        self.assertIs(getattr(old,'__vertex_buffer'),buffer)
        self.assertEqual(self.bytes(buffer),expected)
        self.assertEqual(self.bytes(replacement),expected)
        lease.validate(frozen)  # Old source/buffer remain valid while the worker retains them.

    def test_untracked_wrong_previous_and_same_size_metadata_replacement_decline(self):
        mesh=LayerData();mesh.previous();self.api.createVertexBuffer(mesh)
        observed=adapter.observe_native_upload(mesh,self.context,self.render)
        self.assertFalse(module.admit_native_upload(mesh,self.context,observed))
        _buffer,admitted=self.upload(mesh,wrong=True);self.assertFalse(admitted)
        _buffer,admitted=self.upload(mesh);self.assertTrue(admitted)
        before=module.ToolheadPathGeometry(mesh).vertex_mesh(self.context)
        replacement=mesh.vertices.copy();replacement.flags.writeable=False;mesh.vertices=replacement
        after=module.ToolheadPathGeometry(mesh).vertex_mesh(self.context)
        self.assertIsNone(after._borrowed_native);self.assertIsNot(before,after)
        self.assertFalse(adapter._known_native_writer(lambda:None,lambda:None))

    def prepare_existing(self):
        mesh=LayerData();mesh.previous();old=self.api.createVertexBuffer(mesh)
        observed=adapter.observe_native_upload(mesh,self.context,self.render)
        self.assertFalse(module.admit_native_upload(mesh,self.context,observed))
        return mesh,old

    def test_first_upload_replaces_existing_native_once_without_private_allocation(self):
        mesh,old=self.prepare_existing();expected=self.bytes(old)
        factory=Mock(side_effect=QOpenGLBuffer);factory.Type=QOpenGLBuffer.Type
        with patch.dict(self.upload_scope,{'QOpenGLBuffer':factory}):
            geometry=module.ToolheadPathGeometry(mesh);upload=geometry.vertex_mesh(self.context)
            self.assertIsNot(upload._borrowed_native,old)
            self.assertIs(self.api.createVertexBuffer(upload),upload._borrowed_native)
            self.assertIs(geometry.vertex_mesh(self.context),upload)
        self.assertEqual(factory.call_count,1)
        self.assertEqual(self.bytes(upload._borrowed_native),expected)
        self.assertEqual(self.bytes(old),expected)

    def test_failed_first_upload_restores_native_cache_and_caller_binding(self):
        mesh,old=self.prepare_existing();caller=QOpenGLBuffer();caller.create();caller.bind();caller.allocate(b'caller',6)
        gl=Mock(wraps=self.gl);gl.glGetError.side_effect=[0,0x505,0]
        self.api.getBindingsObject=lambda:gl
        upload=module.ToolheadPathGeometry(mesh).vertex_mesh(self.context)
        self.assertEqual(gl.glGetError.call_count,3)
        self.assertIsNone(upload._borrowed_native)
        self.assertIs(getattr(mesh,'__vertex_buffer'),old)
        self.assertEqual(int(self.gl.glGetIntegerv(0x8894)),caller.bufferId())
        self.assertNotIn(mesh,module._native_uploads)
        caller.destroy()

    def test_any_retained_private_proxy_blocks_first_upload_transaction(self):
        mesh,old=self.prepare_existing()
        private=module.PathVertexMesh(mesh);private_buffer=self.api.createVertexBuffer(private)
        worker_retention=(private_buffer,private)  # Same strong owners as CaptureFreezer leases.
        upload=module.ToolheadPathGeometry(mesh).vertex_mesh(self.context)
        self.assertIsNone(upload._borrowed_native)
        self.assertIs(getattr(mesh,'__vertex_buffer'),old)
        self.assertIs(worker_retention[1],private)

    def test_unknown_instance_uploader_declines_without_calling_it(self):
        mesh,old=self.prepare_existing()
        with patch.object(self.api,'createVertexBuffer',side_effect=AssertionError('unknown uploader')) as create:
            upload=module.ToolheadPathGeometry(mesh).vertex_mesh(self.context)
        create.assert_not_called()
        self.assertIsNone(upload._borrowed_native)
        self.assertIs(getattr(mesh,'__vertex_buffer'),old)

    def test_observation_retains_arrays_and_logging_failure_does_not_fail_admission(self):
        import weakref
        mesh=LayerData();observed=adapter.observe_native_upload(mesh,self.context,self.render)
        original=weakref.ref(mesh.vertices)
        replacement=mesh.vertices.copy();replacement.flags.writeable=False;mesh.vertices=replacement
        self.assertIsNotNone(original())
        mesh.previous();self.api.createVertexBuffer(mesh,force_recreate=True)
        self.assertFalse(module.admit_native_upload(mesh,self.context,observed))
        with patch.object(sys.modules['UM.Logger'].Logger,'log',side_effect=RuntimeError('logger unavailable')):
            buffer,admitted=self.upload(mesh)
        self.assertTrue(admitted)
        self.assertIs(module.ToolheadPathGeometry(mesh).vertex_mesh(self.context)._borrowed_native,buffer)

    def test_qt_wrapper_rejects_unrelated_context_and_accepts_shared_context(self):
        mesh=LayerData();buffer,admitted=self.upload(mesh);self.assertTrue(admitted)
        contexts=[]
        try:
            for shared in (False,True):
                context=QOpenGLContext();context.setFormat(self.context.format())
                if shared:context.setShareContext(self.context)
                self.assertTrue(context.create())
                surface=QOffscreenSurface();surface.setFormat(context.format());surface.create()
                contexts.append((context,surface));self.assertTrue(context.makeCurrent(surface))
                others=[]
                for _ in range(1 if shared else buffer.bufferId()+1):
                    other=QOpenGLBuffer();self.assertTrue(other.create());self.assertTrue(other.bind());other.allocate(b'wrong',5)
                    others.append(other)
                    if other.bufferId()==buffer.bufferId():break
                if not shared:self.assertEqual(other.bufferId(),buffer.bufferId())
                self.assertEqual(buffer.bind(),shared)
                selected=module.ToolheadPathGeometry(mesh).vertex_mesh(context)
                self.assertIs(selected._borrowed_native,buffer if shared else None)
                for other in others:other.destroy()
                context.doneCurrent()
            self.assertTrue(self.context.makeCurrent(self.surface))
            self.assertEqual(len(self.bytes(buffer)),60)
        finally:self.context.makeCurrent(self.surface)
