"""Small real TBO publication/read/retirement proofs, never live Cura actions."""
import ctypes
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentLayerGL as storage
from mpf.toolhead.ToolheadEnvironmentLayers import LayerImage
from mpf.toolhead.ToolheadEnvironmentRecovery import layer_lookup_fragment
from mpf.toolhead.ToolheadGLState import procedure
from tests import test_toolhead_environment_gl as fixture


class LayerGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EnvironmentGLTests.setUpClass()
        cls.available = fixture.EnvironmentGLTests.available
        cls.context = fixture.EnvironmentGLTests.context
        cls.gl = getattr(fixture.EnvironmentGLTests, 'gl', None)
        cls.surface = getattr(fixture.EnvironmentGLTests, 'surface', None)

    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass()

    def setUp(self):
        if not self.available: self.skipTest('Core GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface)); self.owners = []

    def tearDown(self):
        self.context.makeCurrent(self.surface); self.gl.glFinish()
        for owner in self.owners:
            if not owner.quarantined: owner.close()
        self.assertEqual(self.gl.glGetError(), 0)

    def image(self, counts=(3,), ids=(2, 6, 9), colours=None):
        counts = np.array(counts, np.uint32)
        ranges = np.empty((len(counts), 2), np.uint32)
        ranges[:, 1] = counts; ranges[:, 0] = np.cumsum(counts)-counts
        ids = np.array(ids, np.uint32)
        values = ((0., 0., 0., 1.), (0., 0., 0., 0.), (.123, .456, .789, 1.))
        colours = np.array(values if colours is None else colours, np.float32).reshape((-1, 4)).copy()
        for array in (ranges, ids, colours): array.setflags(write=False)
        size = sum(array.nbytes for array in (ranges, ids, colours))
        return LayerImage(('image', 7), len(counts), 1, ranges, ids, colours, size, size, size*2)

    def create(self, image=None, **options):
        arguments = dict(epoch=3, fallback_key=('source', 7), existing_bytes=37,
                         byte_budget=64*1024**2); arguments.update(options)
        owner = storage.LayerStorage(self.gl, self.context, self.image() if image is None else image, **arguments)
        self.owners.append(owner); return owner

    @contextmanager
    def scope(self, key=('source', 7)):
        yield NS(key=key)

    def finish(self, owner):
        for _ in range(64):
            self.gl.glFinish()
            if owner.step(owner.key): return
        self.fail('Tiny layer upload did not complete')

    def body(self, owner, index):
        result = np.empty_like(owner._arrays[index])
        with storage._bindings(self.gl, self.context):
            self.gl.glBindBuffer(storage.TARGET, owner.buffers[index])
            procedure(self.context, 'glGetBufferSubData', None, ctypes.c_uint,
                ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p)(storage.TARGET, 0,
                    result.nbytes, result.ctypes.data)
        return result

    def test_no_partial_read_exact_upload_last_use_retirement_and_empty_dummy_storage(self):
        for image in (self.image(), self.image((0, 0), (), ())):
            owner = self.create(image)
            with owner.read(None, owner.epoch, fallback_scope=self.scope) as value: self.assertIsNone(value)
            while owner._index != 3:
                self.gl.glFinish(); self.assertFalse(owner.step(owner.key)); self.assertIsNone(owner.ready_key)
                with owner.read(owner.key, owner.epoch, fallback_scope=self.scope) as value: self.assertIsNone(value)
            self.gl.glFinish(); self.assertTrue(owner.step(owner.key))
            for index, expected in enumerate(owner._arrays):
                np.testing.assert_array_equal(self.body(owner, index), expected)
            self.assertEqual(owner.retained_bytes, sum(value.nbytes for value in owner._arrays)*2+storage.METADATA_RESERVE)
            with owner.read(owner.key, owner.epoch, fallback_scope=self.scope) as value:
                self.assertIs(value, owner); self.assertEqual(owner._readers, 1)
            self.assertIsNotNone(owner._read)
            names = tuple(owner.buffers), tuple(owner.textures)
            owner.close(); owner.close()
            self.assertTrue(owner.closed); self.assertIsNone(owner.image); self.assertEqual(owner.retained_bytes, 0)
            self.assertTrue(all(not self.gl.glIsBuffer(name) for name in names[0]))
            self.assertTrue(all(not self.gl.glIsTexture(name) for name in names[1]))

    def test_stale_owner_cannot_readopt_after_aba_and_wrong_fallback_is_never_bound(self):
        first, second = self.create(), self.create()
        self.assertNotEqual(first.key, second.key)
        charge = first.retained_bytes
        self.assertFalse(first.step(second.key)); self.finish(second)
        self.assertFalse(first.step(first.key)); self.assertIsNone(first.ready_key)
        self.assertEqual(first.retained_bytes, charge)
        with second.read(second.key, second.epoch+1, fallback_scope=self.scope) as value: self.assertIsNone(value)
        with second.read(second.key, second.epoch, fallback_scope=lambda: self.scope(('different', 7))) as value:
            self.assertIsNone(value); self.assertEqual(second._bound_reads, 0)
        with self.assertRaises(ValueError):
            with second.read(second.key, second.epoch, fallback_scope=None): self.fail('Invalid scope admitted')
        self.assertIsNone(second._read)

    def test_exact_byte_capabilities_and_value_layout_reject_before_gpu_allocation(self):
        image = self.image()
        with patch.object(storage, 'procedure') as allocate:
            for value in (replace(image, retained_bytes=0), replace(image, gpu_bytes=0),
                    replace(image, width=True), replace(image, identities=image.identities[:2]),
                    replace(image, colours=image.colours.copy())):
                with self.assertRaises(ValueError): self.create(value)
            with self.assertRaises(MemoryError): self.create(image, byte_budget=37+image.retained_bytes*2+storage.METADATA_RESERVE+storage.VALIDATION_SCRATCH-1)
            allocate.assert_not_called()
        original = self.gl.glGetIntegerv
        for parameter, limited in ((0x8C2B, 2), (0x8872, 11), (0x8B4D, 11)):
            with patch.object(self.gl, 'glGetIntegerv', side_effect=lambda name, p=parameter, v=limited:
                    v if name == p else original(name)):
                with patch.object(storage, 'procedure') as allocate:
                    with self.assertRaises(MemoryError): self.create(image)
                    allocate.assert_not_called()

    def test_csr_and_sorted_identity_continuity_across_upload_chunks(self):
        with patch.object(storage, 'UPLOAD_BYTES', 32):
            owner = self.create(self.image((8, 4), tuple(range(8))+tuple(range(4)), [(0, 0, 0, 1)]*12))
            self.finish(owner)
            np.testing.assert_array_equal(self.body(owner, 1), owner.image.identities)
            for kind in ('csr', 'coverage', 'ids', 'maxid', 'colour', 'layout'):
                image = self.image((12,), tuple(range(12)), [(0, 0, 0, 1)]*12)
                array = image.ranges if kind in ('csr', 'coverage') else image.identities if kind in ('ids', 'maxid') else image.colours
                array.setflags(write=True)
                if kind == 'csr': array[0, 0] = 1
                elif kind == 'coverage': array[0, 1] = 11
                elif kind == 'ids': array[8] = 7  # Exact upload-chunk boundary.
                elif kind == 'maxid': array[8] = 16777216
                elif kind == 'colour': array[9, 0] = np.nan
                array.setflags(write=False)
                candidate = self.create(image)
                if kind == 'layout': array.setflags(write=True)
                with self.assertRaises(storage.GeometryUncertain): self.finish(candidate)
                self.assertTrue(candidate.quarantined); self.assertIsNone(candidate.ready_key)
                self.assertIs(candidate.image, image); self.assertGreater(candidate.retained_bytes, 0)

    def test_timeout_does_not_upload_and_failed_wait_keeps_the_owned_sync(self):
        owner = self.create(); fence = owner._write
        original = storage.procedure
        def resolve(result):
            return lambda context, name, *types: (lambda *args: result) if name == 'glClientWaitSync' else original(context, name, *types)
        with patch.object(storage, 'procedure', side_effect=resolve(0x911B)):
            self.assertFalse(owner.step(owner.key)); self.assertEqual(owner._index, 0)
            self.assertEqual(owner._write, fence); self.assertIsNone(owner.ready_key)
        with patch.object(storage, 'procedure', side_effect=resolve(0x911D)):
            with self.assertRaises(storage.GeometryUncertain): owner.step(owner.key)
        self.assertEqual(owner._write, fence); self.assertTrue(owner.quarantined)
        self.assertTrue(procedure(self.context, 'glIsSync', ctypes.c_ubyte, ctypes.c_void_p)(ctypes.c_void_p(fence)))

    def test_retirement_during_wait_upload_or_fallback_cannot_delete_admitted_resources(self):
        original = storage.procedure
        for phase in ('wait', 'upload', 'fallback'):
            owner = self.create()
            if phase == 'fallback': self.finish(owner)
            def resolve(context, name, *types, owner=owner, phase=phase):
                if name == ('glClientWaitSync' if phase == 'wait' else 'glBufferSubData'):
                    return lambda *args: owner.close()
                return original(context, name, *types)
            @contextmanager
            def scope(owner=owner):
                owner.close(); yield NS(key=owner.fallback_key)
            with self.assertRaises(storage.GeometryUncertain):
                if phase == 'fallback':
                    with owner.read(owner.key, owner.epoch, fallback_scope=scope): self.fail('Retired lookup admitted')
                else:
                    self.gl.glFinish()
                    with patch.object(storage, 'procedure', side_effect=resolve): owner.step(owner.key)
            self.assertFalse(owner.closed); self.assertTrue(owner.quarantined)
            self.assertTrue(all(self.gl.glIsBuffer(name) for name in owner.buffers))

    def test_nested_step_does_not_change_demand_and_read_exit_cannot_fence_foreign_context(self):
        owner = self.create(); original = storage.procedure
        def resolve(context, name, *types):
            resolved = original(context, name, *types)
            if name != 'glBufferSubData': return resolved
            def upload(*args):
                self.assertFalse(owner.step(('other',))); self.assertFalse(owner.withdrawn)
                return resolved(*args)
            return upload
        with patch.object(storage, 'procedure', side_effect=resolve): self.finish(owner)
        try:
            with self.assertRaises(storage.GeometryUncertain):
                with owner.read(owner.key, owner.epoch, fallback_scope=self.scope) as value:
                    self.assertIs(value, owner); self.context.doneCurrent()
        finally: self.assertTrue(self.context.makeCurrent(self.surface))
        self.assertTrue(owner.quarantined); self.assertIsNone(owner._read)

    def test_failed_last_read_flush_retains_new_and_superseded_fences(self):
        owner = self.create(); self.finish(owner)
        with owner.read(owner.key, owner.epoch, fallback_scope=self.scope): pass
        old = owner._read
        with patch.object(self.gl, 'glFlush', side_effect=RuntimeError('unconfirmed flush')):
            with self.assertRaises(storage.GeometryUncertain):
                with owner.read(owner.key, owner.epoch, fallback_scope=self.scope): pass
        self.assertTrue(owner.quarantined); self.assertNotEqual(owner._read, old)
        self.assertEqual(owner._superseded, [old]); self.assertGreater(owner.retained_bytes, 0)
        for fence in (owner._read, old):
            self.assertTrue(procedure(self.context, 'glIsSync', ctypes.c_ubyte, ctypes.c_void_p)(ctypes.c_void_p(fence)))

    def test_read_exit_never_restores_old_bindings_into_a_different_live_context(self):
        from PyQt6.QtGui import QOpenGLContext, QOffscreenSurface
        from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile
        owner = self.create(); self.finish(owner)
        other = QOpenGLContext(); other.setFormat(self.context.format()); self.assertTrue(other.create())
        surface = QOffscreenSurface(); surface.setFormat(other.format()); surface.create()
        profile = QOpenGLVersionProfile(); profile.setVersion(4, 1); profile.setProfile(other.format().profile())
        names = []
        def snapshot(gl):
            active = int(gl.glGetIntegerv(0x84E0)); buffer = int(gl.glGetIntegerv(storage.TARGET))
            states = []
            for unit in (8, 9, 10, 11):
                gl.glActiveTexture(0x84C0+unit)
                states.append((int(gl.glGetIntegerv(0x8C2C)), int(gl.glGetIntegerv(0x8919))))
            gl.glActiveTexture(active); return active, buffer, tuple(states)
        try:
            with self.assertRaises(storage.GeometryUncertain):
                with owner.read(owner.key, owner.epoch, fallback_scope=self.scope):
                    self.assertTrue(other.makeCurrent(surface))
                    gl = QOpenGLVersionFunctionsFactory.get(profile, other); gl.initializeOpenGLFunctions()
                    for unit in (8, 9, 10, 11):
                        value = ctypes.c_uint()
                        procedure(other, 'glGenTextures', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(value))
                        names.append(value.value); gl.glActiveTexture(0x84C0+unit); gl.glBindTexture(storage.TARGET, value.value)
                    gl.glActiveTexture(0x84C0+3); before = snapshot(gl)
            self.assertEqual(snapshot(gl), before); self.assertEqual(gl.glGetError(), 0)
            self.assertTrue(owner.quarantined)
        finally:
            if names:
                self.assertTrue(other.makeCurrent(surface))
                values = (ctypes.c_uint*len(names))(*names)
                procedure(other, 'glDeleteTextures', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(len(names), values)
            other.doneCurrent(); self.assertTrue(self.context.makeCurrent(self.surface))

    def test_four_plane_draw_refuses_missing_context_before_sample_queries(self):
        image=replace(self.image((1,1,1,1),(0,0,0,0),[(1,0,0,1)]*4),width=1,
            samples=4,positions=((.375,.125),(.875,.375),(.125,.625),(.625,.875)))
        owner=self.create(image)
        vertices=np.zeros((3,3),np.float32)
        mesh=NS(getVertices=lambda:vertices,getVertexCount=lambda:3,hasIndices=lambda:False,
            hasNormals=lambda:False,hasColors=lambda:False,hasUVCoordinates=lambda:False,attributeNames=lambda:())
        wrapper=NS()
        adapter=storage.LayerDraw(owner,owner.key,owner.epoch,(wrapper,wrapper),((mesh,0,1),),fallback_scope=self.scope)
        self.context.doneCurrent()
        try:
            with patch.object(self.gl,'glGetIntegerv') as query:
                with patch.object(self.gl,'glIsEnabled') as enabled:
                    with self.assertRaisesRegex(RuntimeError,'creating context'):
                        with adapter.read(): self.fail('Missing context admitted')
                    query.assert_not_called(); enabled.assert_not_called()
            self.assertFalse(adapter._entered)
        finally: self.assertTrue(self.context.makeCurrent(self.surface))

    def test_four_plane_draw_requires_original_depth_precision_and_dimensions(self):
        from mpf.toolhead.ToolheadSampleTarget import ToolheadSampleTarget
        from mpf.toolhead.ToolheadGLState import preserved_state,preserved_samples,sample_depth_certificate
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject,QOpenGLFramebufferObjectFormat
        with preserved_state(self.gl,self.context),preserved_samples(self.gl,self.context):
            target=ToolheadSampleTarget(self.gl,1,1)
            names=[]
            single=None
            try:
                image=replace(self.image((1,1,1,1),(0,0,0,0),[(1,0,0,1)]*4),width=1,
                    samples=4,positions=target.positions)
                owner=self.create(image); self.finish(owner)
                vertices=np.zeros((3,3),np.float32)
                mesh=NS(getVertices=lambda:vertices,getVertexCount=lambda:3,hasIndices=lambda:False,
                    hasNormals=lambda:False,hasColors=lambda:False,hasUVCoordinates=lambda:False,attributeNames=lambda:())
                wrapper=NS()
                adapter=storage.LayerDraw(owner,owner.key,owner.epoch,(wrapper,wrapper),((mesh,0,1),),fallback_scope=self.scope)
                target.bind(); self.gl.glEnable(0x809D)
                for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36): self.gl.glDisable(flag)
                allocate=procedure(self.context,'glTexImage2DMultisample',None,ctypes.c_uint,ctypes.c_int,
                    ctypes.c_uint,ctypes.c_int,ctypes.c_int,ctypes.c_ubyte)
                attach=procedure(self.context,'glFramebufferTexture2D',None,*([ctypes.c_uint]*4),ctypes.c_int)
                for internal,width in ((0x81A6,1),(0x8CAC,2)):
                    name=ctypes.c_uint()
                    procedure(self.context,'glGenTextures',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))(1,ctypes.byref(name))
                    names.append(name.value)
                    self.gl.glActiveTexture(0x84C0); self.gl.glBindTexture(0x9100,name.value)
                    allocate(0x9100,4,internal,width,1,1); attach(0x8D40,0x8D00,0x9100,name.value,0)
                    self.assertEqual(procedure(self.context,'glCheckFramebufferStatus',ctypes.c_uint,ctypes.c_uint)(0x8D40),0x8CD5)
                    self.assertEqual(self.gl.glGetError(),0)
                    with self.assertRaisesRegex(RuntimeError,'depth'):
                        with adapter.read(): self.fail('Different original depth admitted')
                    self.assertFalse(adapter._entered)
                attach(0x8D40,0x8D00,0x9100,target._names[1],0)
                with self.assertRaises(ValueError): adapter.select_plane(0)
                with adapter.read():
                    self.assertTrue(adapter._admitted)
                    self.assertEqual(tuple(adapter.planes()),(0,1,2,3))
                    for invalid in (-1,4,True):
                        with self.assertRaises(ValueError): adapter.select_plane(invalid)
                    for plane in adapter.planes():
                        adapter.select_plane(plane); self.assertEqual(adapter._plane,plane)
                self.assertEqual(adapter._plane,0)
                # A complete S1 floating-depth target must never borrow the
                # descriptor of a previously bound matching MS depth texture.
                fmt=QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x8058)
                single=QOpenGLFramebufferObject(1,1,fmt); self.assertTrue(single.bind())
                name=ctypes.c_uint()
                procedure(self.context,'glGenTextures',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))(1,ctypes.byref(name))
                names.append(name.value)
                self.gl.glActiveTexture(0x84C0); self.gl.glBindTexture(0x0DE1,name.value)
                procedure(self.context,'glTexImage2D',None,ctypes.c_uint,ctypes.c_int,ctypes.c_int,
                    ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)(
                        0x0DE1,0,0x8CAC,1,1,0,0x1902,0x1406,None)
                attach(0x8D40,0x8D00,0x0DE1,name.value,0)
                self.assertEqual(procedure(self.context,'glCheckFramebufferStatus',ctypes.c_uint,ctypes.c_uint)(0x8D40),0x8CD5)
                self.gl.glBindTexture(0x9100,target._names[1])
                with self.assertRaisesRegex(RuntimeError,'four-sample'):
                    sample_depth_certificate(self.gl,self.context,1,1)
                self.assertEqual(self.gl.glGetIntegerv(0x9104),target._names[1])
                # Even a driver bind failure on an admitted MS target must
                # refuse before queries can certify the old bound texture.
                target.bind(); self.gl.glBindTexture(0x9100,target._names[1])
                real_bind=self.gl.glBindTexture; failed=[False]
                def wrong_kind(kind,value):
                    if not failed[0]: failed[0]=True; return real_bind(kind,name.value)
                    return real_bind(kind,value)
                with patch.object(self.gl,'glBindTexture',side_effect=wrong_kind):
                    with self.assertRaisesRegex(RuntimeError,'uncertain'):
                        sample_depth_certificate(self.gl,self.context,1,1)
                self.assertEqual(self.gl.glGetIntegerv(0x9104),target._names[1])
                self.assertEqual(self.gl.glGetError(),0)
            finally:
                target.bind()
                procedure(self.context,'glFramebufferTexture2D',None,*([ctypes.c_uint]*4),ctypes.c_int)(
                    0x8D40,0x8D00,0x9100,target._names[1],0)
                if names:
                    procedure(self.context,'glDeleteTextures',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))(
                        len(names),(ctypes.c_uint*len(names))(*names))
                if single is not None:
                    single.bind(); procedure(self.context,'glFramebufferTexture2D',None,*([ctypes.c_uint]*4),ctypes.c_int)(
                        0x8D40,0x8D00,0x0DE1,0,0)
                target.close()

    def test_context_switch_inside_restoration_stops_every_subsequent_foreign_binding(self):
        from PyQt6.QtGui import QOpenGLContext, QOffscreenSurface
        other = QOpenGLContext(); other.setFormat(self.context.format()); self.assertTrue(other.create())
        surface = QOffscreenSurface(); surface.setFormat(other.format()); surface.create()
        original = self.gl.glActiveTexture
        restoring = False
        def select(unit):
            if restoring:
                self.assertTrue(other.makeCurrent(surface)); raise RuntimeError('Context replaced during restore')
            original(unit)
        try:
            with patch.object(self.gl, 'glActiveTexture', side_effect=select):
                with patch.object(self.gl, 'glBindTexture') as bind:
                    with patch.object(storage, 'procedure', return_value=lambda *args: self.fail('Foreign sampler restore')):
                        with self.assertRaisesRegex(RuntimeError, 'lost its creating context'):
                            with storage._bindings(self.gl, self.context): restoring = True
                        bind.assert_not_called()
        finally: other.doneCurrent(); self.assertTrue(self.context.makeCurrent(self.surface))

    def test_real_query_free_shader_receives_exact_f32_black_and_map_fallback(self):
        from PyQt6.QtOpenGL import (QOpenGLShaderProgram, QOpenGLShader,
            QOpenGLVertexArrayObject, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat)
        owner = self.create(); self.finish(owner)
        shader = QOpenGLShaderProgram()
        self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, '''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}
'''), shader.log())
        fragment = '''#version 410
out vec4 colour;
vec3 environmentReflection(vec3 direction,float roughness,out float confidence){
 confidence=.125;return vec3(.2,.3,.4);}
float grainHash(vec3 cell){return cell.x;}
void main(){float confidence;vec3 value=environmentReflection(vec3(0.,0.,1.),.4,confidence);
 colour=vec4(value,confidence);}
'''
        self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            layer_lookup_fragment(fragment)), shader.log()); self.assertTrue(shader.link(), shader.log())
        vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create())
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x8814)
        target = QOpenGLFramebufferObject(1, 1, fmt); self.assertTrue(target.isValid())
        try:
            self.assertTrue(shader.bind()); vao.bind(); target.bind()
            self.gl.glViewport(0, 0, 1, 1)
            for flag in (self.gl.GL_BLEND, self.gl.GL_DEPTH_TEST, self.gl.GL_CULL_FACE, self.gl.GL_SCISSOR_TEST): self.gl.glDisable(flag)
            self.gl.glColorMask(True, True, True, True)
            with self.assertRaises(ValueError): owner.apply(shader, origin=(0, 0), base=2, count=1)
            for identity, expected in ((2, (0, 0, 0, 1)), (6, (.2, .3, .4, .125)),
                    (9, (.123, .456, .789, 1)), (10, (.2, .3, .4, .125))):
                with owner.read(owner.key, owner.epoch, fallback_scope=self.scope):
                    owner.apply(shader, origin=(0, 0), base=identity, count=1)
                    self.gl.glDrawArrays(self.gl.GL_TRIANGLES, 0, 3)
                    result = np.empty(4, np.float32)
                    procedure(self.context, 'glReadPixels', None, *([ctypes.c_int]*4), ctypes.c_uint,
                        ctypes.c_uint, ctypes.c_void_p)(0, 0, 1, 1, 0x1908, 0x1406, result.ctypes.data)
                    np.testing.assert_array_equal(result, np.array(expected, np.float32))
            class Mesh:
                def __init__(self): self.vertices = np.zeros((3,3), np.float32)
                def getVertices(self): return self.vertices
                def getVertexCount(self): return len(self.vertices)
                def hasIndices(self): return False
                def hasNormals(self): return False
                def hasColors(self): return False
                def hasUVCoordinates(self): return False
                def attributeNames(self): return ()
            meshes = tuple(Mesh() for _ in range(4))
            wrapper = NS(_shader_program=shader)
            draws = tuple((mesh, identity, 1) for mesh, identity in zip(meshes, (2, 6, 9, 10), strict=True))
            adapter = storage.LayerDraw(owner, owner.key, owner.epoch, (wrapper, wrapper), draws,
                fallback_scope=self.scope)
            self.assertIs(adapter.shader(True), wrapper)
            self.assertIs(adapter.shader(False), wrapper)
            with self.assertRaises(ValueError): adapter.shader(1)
            with self.assertRaises(RuntimeError): adapter.apply(wrapper, meshes[0])
            for enabled, selected in ((True, owner.key), (False, owner.key), (True, ('stale',))):
                adapter.enabled = enabled; adapter.selected_key = selected
                with adapter.read():
                    with self.assertRaises(RuntimeError):
                        with adapter.read(): self.fail('Nested layer draw admitted')
                    with self.assertRaises(ValueError): adapter.apply(wrapper, object())
                    for mesh, expected in zip(meshes, ((0,0,0,1),(.2,.3,.4,.125),(.123,.456,.789,1),(.2,.3,.4,.125)), strict=True):
                        adapter.apply(wrapper, mesh); self.gl.glDrawArrays(self.gl.GL_TRIANGLES, 0, 3)
                        result = np.empty(4, np.float32)
                        procedure(self.context, 'glReadPixels', None, *([ctypes.c_int]*4), ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p)(0, 0, 1, 1, 0x1908, 0x1406, result.ctypes.data)
                        wanted = expected if enabled and selected == owner.key else (.2,.3,.4,.125)
                        np.testing.assert_array_equal(result, np.array(wanted, np.float32))
                self.assertFalse(adapter._entered); self.assertFalse(adapter._admitted)
            with adapter.read():
                shader.release()
                with self.assertRaises(ValueError): adapter.apply(wrapper, meshes[0])
                self.assertTrue(shader.bind())
            for invalid in (((meshes[0],2,1),(meshes[0],6,1)), ((meshes[0],2,3),(meshes[1],3,1))):
                with self.assertRaises(ValueError): storage.LayerDraw(owner, owner.key, owner.epoch,
                    (wrapper,wrapper), invalid, fallback_scope=self.scope)
            with self.assertRaises(ValueError): storage.LayerDraw(owner, owner.key, owner.epoch,
                (), draws, fallback_scope=self.scope)
            budget = owner.byte_budget
            owner.byte_budget = owner.peak_bytes+storage.METADATA_RESERVE+32768*len(draws)-1
            with self.assertRaises(MemoryError): storage.LayerDraw(owner, owner.key, owner.epoch,
                (wrapper,wrapper), draws, fallback_scope=self.scope)
            owner.byte_budget = budget
            with self.assertRaisesRegex(ValueError, 'triangle count'):
                storage.LayerDraw(owner, owner.key, owner.epoch, (wrapper,wrapper),
                    ((meshes[0],0,2),), fallback_scope=self.scope)
            meshes[0].vertices = meshes[0].vertices.copy()
            with adapter.read():
                with self.assertRaisesRegex(ValueError, 'publication changed'):
                    adapter.apply(wrapper, meshes[0])
            indexed = Mesh(); indexed.indices = np.zeros((2,3), np.uint32)
            indexed.hasIndices = lambda: True
            indexed.getIndices = lambda: indexed.indices
            indexed.getFaceCount = lambda: len(indexed.indices)
            indexed_adapter = storage.LayerDraw(owner, owner.key, owner.epoch, (wrapper,wrapper),
                ((indexed,0,2),), fallback_scope=self.scope)
            with indexed_adapter.read(): indexed_adapter.apply(wrapper, indexed)
            indexed.indices = indexed.indices.copy()
            with indexed_adapter.read():
                with self.assertRaisesRegex(ValueError, 'publication changed'):
                    indexed_adapter.apply(wrapper, indexed)
        finally: self.gl.glFinish(); shader.release(); vao.release(); vao.destroy()


if __name__ == '__main__': unittest.main()
