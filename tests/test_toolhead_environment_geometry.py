"""Real core GL complete-source storage, state restoration and refusal."""
import ctypes
import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PyQt6.QtGui import QOpenGLContext

from mpf.toolhead import ToolheadEnvironmentGeometry as geometry
from mpf.toolhead.ToolheadEnvironmentPaths import admit_path_inputs, freeze_path_prefix
from mpf.toolhead.ToolheadCaptureValues import BufferLease
from mpf.toolhead.ToolheadGLState import procedure
from tests import test_toolhead_environment_gl as fixture
from tests.test_toolhead_environment_paths import publication


class EnvironmentGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EnvironmentGLTests.setUpClass()
        cls.context = fixture.EnvironmentGLTests.context
        cls.surface, cls.gl = fixture.EnvironmentGLTests.surface, fixture.EnvironmentGLTests.gl
        cls.available = fixture.EnvironmentGLTests.available

    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass()

    def setUp(self):
        if not self.available: self.skipTest('Core GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface))
        self.owners, self.buffers, self.fences = [], [], []

    def fn(self, name, result, *args): return procedure(self.context, name, result, *args)

    def tearDown(self):
        self.context.makeCurrent(self.surface); self.gl.glFinish()
        for owner in self.owners:
            if not owner.quarantined: owner.close()
        for value in self.fences: self.fn('glDeleteSync', None, ctypes.c_void_p)(ctypes.c_void_p(value))
        if self.buffers:
            body = (ctypes.c_uint*len(self.buffers))(*self.buffers)
            self.fn('glDeleteBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(len(body), body)

    def source(self, count=3, backing=False):
        mesh, lease = publication(count)
        if backing:
            allocation = np.zeros((200000, 3), np.float32)
            allocation[:len(mesh.vertices)] = mesh.vertices
            mesh = replace(mesh, vertices=allocation[:len(mesh.vertices)])
        if count > 1000:
            # Dense repeated paths exercise upload size without manufacturing
            # huge FLOAT32 coordinates whose one-unit delta is uncertifiable.
            mesh.vertices[:, 0] %= 128
        value = ctypes.c_uint()
        self.fn('glGenBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(value))
        self.buffers.append(value.value); self.gl.glBindBuffer(geometry.TARGET, value.value)
        self.fn('glBufferData', None, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint)(
            geometry.TARGET, lease.size, None, 0x88E4)
        offset = 0
        for array in mesh.upload_parts():
            self.fn('glBufferSubData', None, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p)(
                geometry.TARGET, offset, array.nbytes, ctypes.c_void_p(array.ctypes.data))
            offset += array.nbytes
        self.gl.glBindBuffer(geometry.TARGET, 0)
        fence = self.fn('glFenceSync', ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)(0x9117, 0)
        self.fences.append(int(fence)); self.gl.glFlush()
        inputs = admit_path_inputs(mesh, BufferLease(value.value, lease.size, lease.layout), ('test', 2))
        return inputs, freeze_path_prefix(inputs, 0, 0, inputs.indices.size), int(fence)

    def create(self, source=None, **kwargs):
        inputs, prefix, fence = source or self.source()
        options = dict(epoch=2, source_ready=fence, byte_budget=128*1024**2); options.update(kwargs)
        owner = geometry.PathGeometry(self.gl, self.context, inputs, prefix, np.eye(4, dtype=np.float32), **options)
        self.owners.append(owner); return owner

    def read_buffer(self, name, dtype, shape):
        result = np.empty(shape, dtype)
        with geometry._bindings(self.gl, self.context):
            self.gl.glBindBuffer(geometry.TARGET, name)
            self.fn('glGetBufferSubData', None, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p)(
                geometry.TARGET, 0, result.nbytes, ctypes.c_void_p(result.ctypes.data))
        return result

    def state(self):
        active = int(self.gl.glGetIntegerv(geometry.ACTIVE))
        result = [active, int(self.gl.glGetIntegerv(geometry.TARGET))]
        for unit in geometry.UNITS:
            self.gl.glActiveTexture(0x84C0+unit)
            result.append((int(self.gl.glGetIntegerv(geometry.BINDING)), int(self.gl.glGetIntegerv(0x8919))))
        self.gl.glActiveTexture(active); return result

    def test_complete_original_indices_tree_and_borrowed_vbo_retirement(self):
        source = self.source(101); owner = self.create(source)
        self.assertTrue(owner.published); self.assertEqual(len(owner.buffers), 3)
        np.testing.assert_array_equal(self.read_buffer(owner.buffers[0], np.float32, owner.prepared.nodes.shape), owner.prepared.nodes)
        np.testing.assert_array_equal(self.read_buffer(owner.buffers[1], np.uint32, source[0].indices.shape), source[0].indices)
        self.assertNotIn(source[0].vertex.name, owner.buffers)
        self.assertTrue(self.fn('glIsSync', ctypes.c_ubyte, ctypes.c_void_p)(ctypes.c_void_p(source[2])))
        owner.close(); owner.close()
        self.assertTrue(self.fn('glIsBuffer', ctypes.c_ubyte, ctypes.c_uint)(source[0].vertex.name))
        self.assertIsNone(owner.inputs); self.assertEqual(self.gl.glGetError(), 0)

    def test_global_aliases_and_full_indices_survive_narrow_prefix(self):
        inputs, _prefix, fence = self.source(10); next_ = inputs.mesh.vertices[15].copy()
        prefix = freeze_path_prefix(inputs, 4, 6, 16, (next_-np.array((.2, 0, 0), np.float32), next_, .5))
        owner = self.create((inputs, prefix, fence))
        np.testing.assert_array_equal(self.read_buffer(owner.buffers[2], np.uint32, prefix.aliases.shape), prefix.aliases)
        np.testing.assert_array_equal(self.read_buffer(owner.buffers[1], np.uint32, inputs.indices.shape), inputs.indices)
        self.assertEqual(owner.prefix.completed, 8)

    def test_single_line_leaves_keep_original_ids_and_charge_complete_storage(self):
        source = self.source(33)
        old = self.create(source)
        owner = self.create(source, group=1, existing_bytes=old.retained_bytes)
        self.assertEqual(owner.prepared.group, 1)
        self.assertNotEqual(owner.key, old.key)
        nodes = self.read_buffer(owner.buffers[0], np.float32, owner.prepared.nodes.shape)
        leaves = nodes[nodes[:, 7] < 0]
        np.testing.assert_array_equal(np.sort(leaves[:, 3]), np.arange(33))
        np.testing.assert_array_equal(leaves[:, 7], -np.ones(33))
        np.testing.assert_array_equal(self.read_buffer(owner.buffers[1], np.uint32, source[0].indices.shape), source[0].indices)
        self.assertEqual(owner.prepared.gpu_bytes, nodes.nbytes)
        self.assertGreater(owner.peak_bytes, old.peak_bytes+old.retained_bytes)
        self.assertGreater(owner.retained_bytes, old.retained_bytes)
        with patch.object(geometry, 'procedure', wraps=geometry.procedure) as calls:
            with self.assertRaisesRegex(MemoryError, 'combined'):
                self.create(source, group=1, byte_budget=old.peak_bytes)
        self.assertNotIn('glGenBuffers', [call.args[1] for call in calls.call_args_list])

    def test_selected_leaf_size_is_checked_against_actual_texture_capacity(self):
        original, _prefix, fence = self.source(3)
        mesh = replace(original.mesh, indices=np.tile(original.indices, (100, 1)))
        inputs = admit_path_inputs(mesh, original.vertex, ('test', 3))
        source = inputs, freeze_path_prefix(inputs, 0, 0, inputs.indices.size), fence
        class Limited:
            def glGetIntegerv(_self, key):
                return 650 if key == 0x8C2B else self.gl.glGetIntegerv(key)
        self.assertLess(inputs.scalar_count, 650)
        self.assertLess(inputs.indices.size, 650)
        with patch.object(geometry, 'prepare_path_source', wraps=geometry.prepare_path_source) as prepare:
            with self.assertRaisesRegex(RuntimeError, 'capabilities'):
                geometry.PathGeometry(Limited(), self.context, source[0], source[1], np.eye(4, dtype=np.float32),
                    epoch=2, source_ready=source[2], byte_budget=128*1024**2, group=1)
        prepare.assert_not_called()
        for group in (0, 33, True, 1.0):
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, 'grouping'):
                self.create(source, group=group)

    def test_nested_shared_context_read_restores_all_buffer_texture_state(self):
        old, new = self.create(), self.create(); before = self.state()
        with old.read(self.gl, self.context, 2):
            leased = self.state()
            with new.read(self.gl, self.context, 2):
                self.assertEqual([entry[0] for entry in self.state()[2:]], new.textures)
                self.assertEqual([entry[1] for entry in self.state()[2:]], [0]*4)
            self.assertEqual(self.state(), leased)
        self.assertEqual(self.state(), before)
        shared = QOpenGLContext(); shared.setFormat(self.context.format()); shared.setShareContext(self.context)
        self.assertTrue(shared.create()); self.assertTrue(shared.makeCurrent(self.surface))
        with old.read(self.gl, shared, 2): self.assertEqual([entry[0] for entry in self.state()[2:]], old.textures)
        self.context.makeCurrent(self.surface); self.assertEqual(self.gl.glGetError(), 0)

    def test_wrong_epoch_noncurrent_and_closed_owner_refuse(self):
        owner = self.create()
        with self.assertRaisesRegex(RuntimeError, 'epoch'):
            with owner.read(self.gl, self.context, 3): pass
        self.context.doneCurrent()
        with self.assertRaisesRegex(RuntimeError, 'epoch'):
            with owner.read(self.gl, self.context, 2): pass
        with self.assertRaisesRegex(RuntimeError, 'live creating'): owner.close()
        self.context.makeCurrent(self.surface); owner.close()
        with self.assertRaisesRegex(RuntimeError, 'epoch'):
            with owner.read(self.gl, self.context, 2): pass

    def test_budget_capability_prefix_and_fence_refusal(self):
        source = self.source()
        for kwargs in (dict(byte_budget=1), dict(existing_bytes=128*1024**2)):
            with self.assertRaisesRegex(MemoryError, 'combined'): self.create(source, **kwargs)
        with self.assertRaisesRegex(ValueError, 'source and prefix'):
            self.create((source[0], replace(source[1], certificate=(('wrong',),)), source[2]))
        with self.assertRaisesRegex(ValueError, 'aliases'):
            self.create((source[0], replace(source[1], aliases=np.array((0,), np.uint32)), source[2]))
        with self.assertRaisesRegex(ValueError, 'epoch'): self.create(source, epoch=0)
        with self.assertRaisesRegex(RuntimeError, 'ready fence'): self.create(source, source_ready=12345)
        class Limited:
            def glGetIntegerv(_self, key): return 1 if key == 0x8C2B else self.gl.glGetIntegerv(key)
        with self.assertRaisesRegex(RuntimeError, 'capabilities'):
            geometry.PathGeometry(Limited(), self.context, source[0], source[1], np.eye(4, dtype=np.float32),
                epoch=2, source_ready=source[2], byte_budget=128*1024**2)
        self.assertEqual(self.gl.glGetError(), 0)

    def test_source_subview_backing_is_charged_before_query_allocation(self):
        source = self.source(backing=True)
        with patch.object(geometry, 'procedure', wraps=geometry.procedure) as calls:
            with self.assertRaisesRegex(MemoryError, 'combined'): self.create(source, byte_budget=18*1024**2)
        self.assertNotIn('glGenBuffers', [call.args[1] for call in calls.call_args_list])
        owner = self.create(source)
        self.assertGreaterEqual(owner.source_bytes, 200000*3*4+source[0].vertex.size)
        self.assertEqual(owner.source_bytes, source[0].vertex.size+source[0].mesh.retained_bytes())
        self.assertEqual(self.gl.glGetError(), 0)

    def test_cancelled_chunk_upload_preserves_host_state(self):
        source = self.source(140000); before = self.state(); original = geometry.procedure; uploaded = []
        def resolver(context, name, *args):
            function = original(context, name, *args)
            if name != 'glBufferSubData': return function
            def write(target, offset, size, pointer):
                uploaded.append(size); return function(target, offset, size, pointer)
            return write
        with patch.object(geometry, 'procedure', side_effect=resolver):
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
                self.create(source, cancel=lambda: bool(uploaded))
        self.assertEqual(self.state(), before); self.assertTrue(uploaded)
        self.assertLessEqual(max(uploaded), geometry.UPLOAD_BYTES); self.assertEqual(self.gl.glGetError(), 0)

    def test_allocation_faults_preserve_state(self):
        source = self.source(); original = geometry.procedure; before = self.state()
        for fault in ('glGetBufferParameteri64v', 'glGenTextures', 'glGenBuffers'):
            def resolver(context, name, *args, fault=fault):
                return (lambda *args: None) if name == fault else original(context, name, *args)
            with patch.object(geometry, 'procedure', side_effect=resolver):
                with self.assertRaisesRegex(RuntimeError, 'size|allocation'): self.create(source)
            self.assertEqual(self.state(), before)
        self.assertEqual(self.gl.glGetError(), 0)

    def test_uncertain_constructor_returns_owner_instead_of_losing_live_handles(self):
        source = self.source(); before = self.state(); original = geometry.procedure
        for restore_fault in (False, True):
            with self.subTest(restore_fault=restore_fault):
                if restore_fault:
                    original_bindings = geometry._bindings
                    @contextmanager
                    def uncertain_restore(gl, context, original_bindings=original_bindings):
                        with original_bindings(gl, context): yield
                        raise geometry._RestoreFailed('uncertain restoration')
                    injected = patch.object(geometry, '_bindings', uncertain_restore)
                    close_patch = patch.object(geometry.PathGeometry, 'close', wraps=geometry.PathGeometry.close)
                else:
                    def resolve(context, name, *args):
                        return (lambda *_: None) if name == 'glGenTextures' else original(context, name, *args)
                    injected = patch.object(geometry, 'procedure', side_effect=resolve)
                    close_patch = patch.object(geometry.PathGeometry, 'close', side_effect=RuntimeError('uncertain cleanup'))
                with injected, close_patch as close:
                    with self.assertRaises(geometry.GeometryUncertain) as failed: self.create(source)
                    if restore_fault: close.assert_not_called()
                    else: close.assert_called_once()
                owner = failed.exception.owner
                self.owners.append(owner)
                self.assertTrue(owner.quarantined)
                self.assertIn(owner, geometry._quarantined)
                self.assertIs(owner.inputs, source[0])
                self.assertTrue(owner.buffers)
                self.assertTrue(all(self.fn('glIsBuffer', ctypes.c_ubyte, ctypes.c_uint)(name) for name in owner.buffers))
                self.assertTrue(self.fn('glIsBuffer', ctypes.c_ubyte, ctypes.c_uint)(source[0].vertex.name))
                self.assertEqual(self.state(), before)
                self.assertEqual(self.gl.glGetError(), 0)

    def test_consumer_error_restores_state_uncertain_deletion_quarantines(self):
        owner = self.create(); before = self.state(); original = geometry.procedure
        with self.assertRaisesRegex(RuntimeError, 'consumer'):
            with owner.read(self.gl, self.context, 2): raise RuntimeError('consumer')
        self.assertEqual(self.state(), before)
        def resolver(context, name, *args):
            if name == 'glDeleteTextures':
                def fail(*args): raise RuntimeError('uncertain deletion')
                return fail
            return original(context, name, *args)
        with patch.object(geometry, 'procedure', side_effect=resolver):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'): owner.close()
        self.assertTrue(owner.quarantined); self.assertFalse(owner.published)
        self.assertIn(owner, geometry._quarantined); self.assertEqual(len(owner.textures), 4)
        with self.assertRaisesRegex(RuntimeError, 'quarantined'): owner.close()
        # Injected fault occurred BEFORE deletion; only test cleanup certifies it.
        geometry._quarantined.remove(owner); owner.quarantined = False
        self.assertEqual(self.state(), before)

    def test_stale_descriptor_arrays_indices_and_empty_sources_refuse(self):
        source = self.source(); other = self.source()
        for inputs in (replace(source[0], vertex=other[0].vertex),
                replace(source[0], indices=source[0].indices.copy()),
                replace(source[0], scalar_count=1),
                replace(source[0], arrays=(('a_vertex', source[0].mesh.vertices.copy()),)+source[0].arrays[1:])):
            with self.assertRaisesRegex(ValueError, 'certificate'):
                self.create((inputs, source[1], source[2]))
        with self.assertRaisesRegex(ValueError, 'Empty paths'): self.create(self.source(0))
        baseline = self.context.receivers(self.context.aboutToBeDestroyed)
        for _ in range(4):
            with self.assertRaises(MemoryError): self.create(source, byte_budget=1)
        owner = self.create(source)
        self.assertGreater(self.context.receivers(self.context.aboutToBeDestroyed), baseline)
        self.assertGreaterEqual(owner.retained_bytes, owner.source_bytes+owner.incremental_bytes)
        owner.close()
        self.assertEqual(self.context.receivers(self.context.aboutToBeDestroyed), baseline)

    def test_restore_failure_attempts_later_units_and_quarantines_without_cleanup_retry(self):
        owner = self.create(); before = self.state(); events = []; body_done = [False]
        class Faulty:
            def glGetIntegerv(_self, key): return self.gl.glGetIntegerv(key)
            def glActiveTexture(_self, unit):
                if body_done[0]: events.append(('active', unit))
                self.gl.glActiveTexture(unit)
            def glBindTexture(_self, target, name):
                if body_done[0] and int(self.gl.glGetIntegerv(geometry.ACTIVE)) == 0x84C8:
                    raise RuntimeError('first unit restore failed')
                self.gl.glBindTexture(target, name)
            def glBindBuffer(_self, target, name):
                events.append(('buffer', name)); self.gl.glBindBuffer(target, name)
        with self.assertRaisesRegex(RuntimeError, 'restoration failed'):
            with owner.read(Faulty(), self.context, 2): body_done[0] = True
        for unit in (0x84C9, 0x84CA, 0x84CB, before[0]): self.assertIn(('active', unit), events)
        self.assertIn(('buffer', before[1]), events)
        self.assertTrue(owner.quarantined); self.assertFalse(owner.published)
        self.assertEqual(len(owner.textures), 4); self.assertIsNotNone(owner.inputs)
        # Deliberately failed texture restoration alone is repaired by the test;
        # production must retain this cohort and refuse future graphics work.
        self.gl.glActiveTexture(0x84C8); self.gl.glBindTexture(geometry.TARGET, before[2][0])
        self.gl.glActiveTexture(before[0])
        geometry._quarantined.remove(owner); owner.quarantined = False
        self.assertEqual(self.state(), before)


if __name__ == '__main__': unittest.main()
