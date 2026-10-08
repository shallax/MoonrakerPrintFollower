"""Atomic capture, frozen snapshots, coalescing and optional failure recovery."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import sys

from mpf.toolhead.ToolheadEnvironment import ToolheadEnvironment, ProbeDescriptor


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.clock = [10.]
        self.storage = Mock()
        self.context = object()
        self.factory = Mock(return_value=self.storage)
        self.environment = ToolheadEnvironment(clock=lambda: self.clock[0], storage_factory=self.factory, commands_per_turn=1)
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

    def test_idle_deadline_does_not_touch_graphics_but_changed_pose_and_context_do(self):
        self.finish()
        with patch('mpf.toolhead.ToolheadEnvironment.preserved_state',return_value=nullcontext()) as guard:
            for _ in range(100):self.assertFalse(self.step())
            guard.assert_not_called()
            self.clock[0]+=.16
            self.assertTrue(self.step(soft='changed'))
            guard.assert_called_once()
            guard.reset_mock()
            self.environment.step(None,object(),'file','changed',lambda:self.snapshot('changed'))
            guard.assert_called_once()

    def test_declared_cpu_prepare_turns_keep_budget_without_touching_gl(self):
        timer=[0.];work=[]
        owner=ToolheadEnvironment(clock=lambda:self.clock[0],storage_factory=self.factory,
            turn_clock=lambda:timer[0],commands_per_turn=4)
        snap=self.snapshot('pose');snap.cpu_preparation=True
        def prepare():
            for index in range(6):
                def reduce(index=index):
                    work.append(index);timer[0]+=.0021
                yield reduce
        snap.prepare=prepare
        step=lambda:owner.step(None,self.context,'file','pose',lambda:snap)
        with patch('mpf.toolhead.ToolheadEnvironment.preserved_state',return_value=nullcontext()) as guard:
            self.assertTrue(step());guard.assert_called_once()
            guard.reset_mock()
            for _ in range(5):self.assertTrue(step())
            guard.assert_not_called()
            self.assertEqual(work,list(range(6)))
            while step():pass
            self.assertTrue(owner.available)
            self.storage.publish.assert_called_once()
            guard.assert_called()

    def test_cpu_prepare_count_cap_and_exhaustion_fall_through_to_draw(self):
        work=[]
        owner=ToolheadEnvironment(clock=lambda:self.clock[0],storage_factory=self.factory,
            turn_clock=lambda:0.,commands_per_turn=4)
        snap=self.snapshot('pose');snap.cpu_preparation=True
        snap.prepare=lambda:(lambda index=index:work.append(index) for index in range(9))
        step=lambda:owner.step(None,self.context,'file','pose',lambda:snap)
        self.assertTrue(step());self.assertEqual(work,list(range(4)))
        with patch('mpf.toolhead.ToolheadEnvironment.preserved_state',return_value=nullcontext()) as guard:
            self.assertTrue(step());self.assertEqual(work,list(range(8)));guard.assert_not_called()
            self.assertTrue(step());self.assertEqual(work,list(range(9)));guard.assert_called_once()

    def test_explicit_small_capture_batches_never_publish_partial_faces(self):
        owner=ToolheadEnvironment(clock=lambda:self.clock[0],storage_factory=self.factory,turn_clock=lambda:0.,commands_per_turn=4)
        for frame in range(3):
            owner.step(None,self.context,'file','pose',lambda:self.snapshot('pose'))
            self.assertEqual(self.storage.begin.call_count,(frame+1)*4)
            self.assertEqual(owner.available,frame==2)
        self.assertEqual(self.drawn,[('pose',face) for face in range(6)])
        self.storage.publish.assert_called_once()

    def test_changed_pose_refreshes_promptly_without_recapturing_unchanged_idle_frames(self):
        self.finish()
        old = self.environment.descriptor
        self.assertGreater(self.environment.wake_delay, .9)
        self.clock[0] += .1
        self.assertFalse(self.step(soft='moved'))
        self.assertAlmostEqual(self.environment.wake_delay, .05)
        self.assertIs(self.environment.descriptor, old)
        self.clock[0] += .06
        self.assertTrue(self.step(soft='moved'))
        for _ in range(11): self.step(soft='newer')
        self.assertEqual(self.drawn[-6:], [('moved', face) for face in range(6)])
        self.assertEqual(self.environment.revision, 2)
        self.assertAlmostEqual(self.environment.wake_delay, .15)
        self.clock[0] += .16
        self.finish(soft='newer')
        self.assertEqual(self.environment.revision, 3)
        self.assertGreater(self.environment.wake_delay, .9)
        self.clock[0] += .3
        self.assertFalse(self.step(soft='newer'))
        self.assertEqual(self.environment.revision, 3)

    def test_zero_cost_preparation_still_has_a_bounded_operation_ceiling(self):
        prepared = []
        owner = ToolheadEnvironment(clock=lambda:self.clock[0], storage_factory=self.factory,
                                   turn_clock=lambda:0., commands_per_turn=1000000)
        snap = self.snapshot('pose')
        snap.prepare = lambda: (lambda index=index: prepared.append(index) for index in range(130))
        owner.step(None, self.context, 'file', 'pose', lambda:snap)
        self.assertEqual(prepared, list(range(64)))
        self.storage.begin.assert_not_called()
        owner.step(None, self.context, 'file', 'pose', lambda:snap)
        self.assertEqual(prepared, list(range(128)))
        self.assertFalse(owner.available)
        owner.step(None, self.context, 'file', 'pose', lambda:snap)
        self.assertEqual(prepared, list(range(130)))
        self.assertEqual(self.drawn, [('pose',face) for face in range(6)])
        self.assertTrue(owner.available)

    def test_turn_budget_counts_prepare_draw_and_copy_and_always_makes_progress(self):
        timer=[0.]
        owner=ToolheadEnvironment(clock=lambda:self.clock[0],storage_factory=self.factory,turn_clock=lambda:timer[0])
        def commands(face):
            yield lambda gl:timer.__setitem__(0,timer[0]+.003)
        def prepare():
            yield lambda:timer.__setitem__(0,timer[0]+.003)
        snap=self.snapshot('pose');snap.commands=commands;snap.prepare=prepare
        self.storage.copy.side_effect=lambda *args:timer.__setitem__(0,timer[0]+.003)
        owner.step(None,self.context,'file','pose',lambda:snap)
        self.storage.begin.assert_not_called()
        for count in range(12):
            owner.step(None,self.context,'file','pose',lambda:snap)
            self.assertEqual(self.storage.begin.call_count,count+1)
            self.assertEqual(owner.available,count==11)
        self.storage.publish.assert_called_once()

    def test_hard_identity_change_keeps_complete_front_and_retires_pending_faces(self):
        self.finish()
        self.clock[0] += 1.1
        self.step(soft='moving')
        self.assertTrue(self.environment.available)
        self.assertTrue(self.environment.working)
        self.step(hard='new file', soft='new pose')
        self.assertTrue(self.environment.available)
        self.assertEqual(self.environment._face, 0)
        self.assertEqual(self.drawn[-1], ('new pose', 0))
        old_descriptor=self.environment.descriptor
        for _ in range(10):self.step(hard='new file',soft='new pose')
        self.assertIs(self.environment.descriptor,old_descriptor)
        self.assertEqual(self.environment.revision,1)
        self.step(hard='new file',soft='new pose')
        self.assertEqual(self.environment.revision,2)
        self.assertEqual(self.drawn[-6:],[('new pose',face) for face in range(6)])
        self.environment.close()
        self.assertFalse(self.environment.working)
        self.storage.close.assert_called_once()

    def test_slice_readiness_keeps_complete_map_abandons_partial_and_retries_without_fault_backoff(self):
        self.finish()
        old = self.environment.descriptor
        self.clock[0] += 1.1
        self.step(soft='new slice')
        self.assertTrue(self.environment.working)
        self.environment.defer()
        self.assertTrue(self.environment.available)
        self.assertIs(self.environment.descriptor, old)
        self.assertFalse(self.environment.working)
        self.assertFalse(self.environment.ready)
        self.assertEqual(self.environment.failure, '')
        self.assertAlmostEqual(self.environment.wake_delay, .05)
        self.clock[0] += .051
        self.assertTrue(self.environment.ready)
        self.finish()
        self.assertEqual(self.environment.revision, 2)

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

    def test_capture_failure_diagnostic_is_bounded_deduplicated_and_recovers(self):
        logger=Mock()
        with patch.dict(sys.modules,{'UM.Logger':SimpleNamespace(Logger=logger)}):
            self.environment.fail(RuntimeError('fault'))
            self.environment.fail(RuntimeError('fault'))
            logger.log.assert_called_once_with('w','Toolhead reflections unavailable: %s','fault')
            self.environment.fail(RuntimeError('different'))
            self.assertEqual(logger.log.call_count,2)
            logger.log.side_effect=RuntimeError('logger fault')
            self.environment.fail(RuntimeError('x'*300))
            self.assertEqual(len(self.environment.failure),200)
            logger.log.side_effect=None
            self.clock[0]+=6
            self.finish()
            self.assertTrue(self.environment.available)
            self.environment.fail(RuntimeError('x'*300))
            self.assertEqual(logger.log.call_count,4)

    def test_capture_timing_separates_submission_and_wall_delay_and_cannot_break_rendering(self):
        logger = Mock(); timer=[0.]
        owner = ToolheadEnvironment(clock=lambda:self.clock[0], storage_factory=self.factory,
            commands_per_turn=1, turn_clock=lambda:timer[0], diagnostic=True)
        snap = self.snapshot('pose')
        def commands(face):
            yield lambda gl:timer.__setitem__(0,timer[0]+.001)
        snap.commands = commands
        with patch.dict(sys.modules, {'UM.Logger':SimpleNamespace(Logger=logger)}):
            for _ in range(12):
                owner.step(None,self.context,'file','pose',lambda:snap)
                self.clock[0]+=.02
            args = logger.log.call_args.args
            self.assertEqual(args[2],1)
            self.assertAlmostEqual(args[3],220.)
            self.assertAlmostEqual(args[4],6.)
            self.assertEqual(args[5],12)
            for _ in range(12): owner.step(None,self.context,'file','changed',lambda:snap)
            logger.log.assert_called_once()
            self.clock[0]+=11.
            logger.log.side_effect=RuntimeError('diagnostic logger unavailable')
            for _ in range(12): owner.step(None,self.context,'file','changed',lambda:snap)
            self.assertTrue(owner.available)
            self.assertEqual(owner.failure,'')
            self.assertEqual(logger.log.call_count,2)
            owner.step(None,self.context,'new file','changed',lambda:snap)
            owner.defer()
            self.assertIsNone(owner._capture_start)
            owner.close()
            self.assertIsNone(owner._capture_start)

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
