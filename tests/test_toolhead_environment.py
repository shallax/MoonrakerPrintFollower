"""Atomic capture, frozen snapshots, coalescing and optional failure recovery."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mpf.toolhead.ToolheadEnvironment import ToolheadEnvironment, ProbeDescriptor


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.clock = [10.]
        self.storage = Mock()
        self.context = object()
        self.factory = Mock(return_value=self.storage)
        self.environment = ToolheadEnvironment(clock=lambda: self.clock[0], storage_factory=self.factory)
        scope = patch('mpf.toolhead.ToolheadEnvironment.preserved_state', return_value=nullcontext())
        scope.start(); self.addCleanup(scope.stop)
        self.drawn = []

    def snapshot(self, value):
        def commands(face):
            yield lambda gl: self.drawn.append((value, face))
        return SimpleNamespace(commands=commands, descriptor=ProbeDescriptor((0.,0.,0.),(-100.,-100.,-100.),(100.,100.,100.)))

    def step(self, hard='file', soft='pose'):
        return self.environment.step(None, self.context, hard, soft, lambda: self.snapshot(soft))

    def finish(self, hard='file', soft='pose'):
        for _ in range(12): self.step(hard, soft)

    def test_only_six_complete_faces_publish_and_snapshot_stays_frozen(self):
        self.step(soft='old')
        for _ in range(10): self.step(soft='new')
        self.assertFalse(self.environment.available)
        self.assertEqual(self.storage.publish.call_count, 0)
        self.assertFalse(self.step(soft='new'), 'a completed capture must wait for its cadence')
        self.assertEqual(self.drawn, [('old', face) for face in range(6)])
        self.assertTrue(self.environment.available)
        self.assertEqual(self.environment.revision, 1)
        self.assertEqual(self.storage.copy.call_count, 6)
        self.assertFalse(self.environment.working)
        for _ in range(20): self.assertFalse(self.step(soft='new'))
        self.assertEqual(self.storage.begin.call_count, 12)
        self.clock[0] += 1.1
        self.finish(soft='new')
        self.assertEqual(self.drawn[6:], [('new', face) for face in range(6)])
        self.assertEqual(self.environment.revision, 2)

    def test_repeated_unchanged_frames_reuse_the_complete_map_until_periodic_refresh(self):
        self.finish()
        for _ in range(20): self.assertFalse(self.step())
        self.assertEqual(self.storage.publish.call_count, 1)
        self.clock[0] += 1.1
        self.assertTrue(self.step())
        self.assertTrue(self.environment.available)

    def test_hard_identity_change_retires_front_and_pending_without_cross_scene_faces(self):
        self.finish()
        self.step(soft='moving')
        self.assertTrue(self.environment.available)
        self.step(hard='new file', soft='new pose')
        self.assertFalse(self.environment.available)
        self.assertEqual(self.environment._face, 0)
        self.assertEqual(self.drawn[-1], ('new pose', 0))
        self.environment.close()
        self.assertFalse(self.environment.working)
        self.storage.close.assert_called_once()

    def test_fault_never_publishes_partial_map_and_backoff_does_not_spin(self):
        self.storage.copy.side_effect = RuntimeError('copy failed')
        self.step(); self.assertFalse(self.step())
        self.assertEqual(self.environment.failure, 'copy failed')
        self.assertFalse(self.environment.available)
        self.storage.publish.assert_not_called()
        count = self.storage.begin.call_count
        for _ in range(40): self.assertFalse(self.step())
        self.assertEqual(self.storage.begin.call_count, count)
        self.clock[0] += 6
        self.storage.copy.side_effect = None
        self.finish()
        self.assertTrue(self.environment.available)
        self.assertEqual(self.environment.failure, '')

    def test_context_change_retires_resources_before_reallocating(self):
        self.finish()
        self.context = object()
        self.step()
        self.storage.close.assert_called_once()
        self.assertEqual(self.factory.call_count, 2)
        self.assertFalse(self.environment.available)

    def test_shader_only_receives_published_storage(self):
        shader = Mock()
        self.environment.apply(shader)
        shader.setTexture.assert_any_call(7, None)
        shader.setTexture.assert_any_call(6, None)
        self.finish()
        self.environment.apply(shader)
        shader.setUniformValue.assert_any_call('u_environmentEnabled', 1)
        shader.setTexture.assert_any_call(7, self.storage)
        shader.setTexture.assert_any_call(6, self.storage.depth)
        shader.setUniformValue.assert_any_call('u_probeFar', 1000.)

    def test_descriptor_stays_with_its_complete_map_and_missing_descriptor_cannot_publish(self):
        self.finish()
        first = self.environment.descriptor
        self.clock[0] += 1.1
        snapshot = self.snapshot('new')
        snapshot.descriptor = ProbeDescriptor((3,4,5),(-10,-20,-30),(20,30,40),far=200.)
        for _ in range(11):
            self.environment.step(None,self.context,'file','new',lambda:snapshot)
        self.assertIs(self.environment.descriptor,first)
        self.environment.step(None,self.context,'file','new',lambda:snapshot)
        self.assertIs(self.environment.descriptor,snapshot.descriptor)
        self.assertEqual(self.environment.revision,2)
        self.clock[0] += 1.1
        snapshot.descriptor = None
        for _ in range(12): self.environment.step(None,self.context,'file','broken',lambda:snapshot)
        self.assertFalse(self.environment.available)
        self.assertIsNone(self.environment.descriptor)
        self.assertEqual(self.storage.publish.call_count,2)

    def test_invalid_probe_bounds_range_and_nonfinite_values_are_rejected(self):
        for values in (((0,0,0),(1,1,1),(0,0,0),.2,100.),
                ((float('nan'),0,0),(-1,-1,-1),(1,1,1),.2,100.),
                ((0,0,0),(-1,-1,-1),(1,1,1),1.,1.),
                ((0,0,0),(-1,-1,-1),(1,1,1),.2,10001.)):
            with self.subTest(values=values), self.assertRaises(ValueError): ProbeDescriptor(*values)


class StorageFaultTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        from mpf.toolhead import ToolheadEnvironment as module
        self.module=module
        self.gl=Mock(GL_COLOR_BUFFER_BIT=1,GL_DEPTH_BUFFER_BIT=2)
        self.gl.glGetIntegerv.side_effect=lambda key:9 if key==0x84E0 else 23
        self.gl.glGetError.return_value=0
        self.context=SimpleNamespace(shareGroup=lambda:'group')
        self.fbo=Mock();self.format=Mock()
        factory=Mock(return_value=self.fbo);factory.Attachment=SimpleNamespace(Depth=1)
        def procedure(context,name,*signature):
            if name=='glGenTextures':
                return lambda count,names:[names.__setitem__(i,i+10) for i in range(count)]
            return Mock()
        self.native=patch.object(module,'procedure',side_effect=procedure);self.native.start();self.addCleanup(self.native.stop)
        self.qt=patch.dict('sys.modules',{'PyQt6.QtOpenGL':SimpleNamespace(QOpenGLFramebufferObject=factory,QOpenGLFramebufferObjectFormat=lambda:self.format),
            'UM.View.GL.OpenGL':SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda:SimpleNamespace(getBindingsObject=lambda:self.gl)))})
        self.qt.start();self.addCleanup(self.qt.stop)
        self.retire=patch.object(module,'retire_textures');self.retired=self.retire.start();self.addCleanup(self.retire.stop)
        self.flush=patch.object(module,'flush_texture_deletions');self.flush.start();self.addCleanup(self.flush.stop)
        self.storage=module.CubeStorage(self.gl,self.context)
        self.addCleanup(self.storage.close)
        self.ctypes=ctypes

    def test_failed_first_sampler_restore_cannot_skip_the_second_owned_unit(self):
        self.storage.bind(7);self.storage.depth.bind(6)
        original=self.gl.glBindTexture.side_effect
        def fail_once(target,name):
            self.gl.glBindTexture.side_effect=original
            raise RuntimeError('injected cube restore')
        self.gl.glBindTexture.side_effect=fail_once
        environment=ToolheadEnvironment();environment._storage=self.storage
        with self.assertRaisesRegex(RuntimeError,'Environment bindings'): environment.release_bindings()
        self.assertEqual(self.storage._bindings,{})
        self.storage._sampler.assert_any_call(6,23)
        self.storage._sampler.assert_any_call(7,23)

    def test_sampler_binding_returns_the_exact_prior_unit_and_cube_binding(self):
        self.storage.bind(7)
        self.gl.glBindTexture.assert_called_with(0x8513,10)
        self.storage.release(7)
        self.gl.glBindTexture.assert_called_with(0x8513,23)
        self.gl.glActiveTexture.assert_called_with(9)

    def test_face_or_publish_failure_never_exchanges_front_and_back(self):
        self.fbo.bind.return_value=False
        with self.assertRaisesRegex(RuntimeError,'could not bind'):self.storage.begin(self.gl,True)
        self.gl.glGetError.return_value=1282
        with self.assertRaisesRegex(RuntimeError,'capture failed'):self.storage.publish(self.gl)
        self.assertEqual((self.storage.front,self.storage.back),(10,11))
        self.assertEqual((self.storage.front_depth,self.storage.back_depth),(12,13))

    def test_depth_and_colour_bindings_restore_independently_in_forward_release_order(self):
        self.storage.bind(7)
        self.storage.depth.bind(6)
        self.gl.glBindTexture.assert_called_with(0x8513,12)
        self.storage.release(7)
        self.storage.depth.release(6)
        self.assertEqual(self.storage._bindings,{})
        self.gl.glActiveTexture.assert_called_with(9)

    def test_allocation_failure_retires_any_partial_raw_names(self):
        with patch.object(self.module,'procedure',return_value=Mock()):
            with self.assertRaisesRegex(RuntimeError,'allocation failed'):self.module.CubeStorage(self.gl,self.context)
        self.retired.assert_called_with('group',[0,0,0,0],None)
        self.fbo.isValid.return_value=False
        with self.assertRaisesRegex(RuntimeError,'face target unavailable'):self.module.CubeStorage(self.gl,self.context)
        self.retired.assert_called_with('group',[10,11,12,13],None)
