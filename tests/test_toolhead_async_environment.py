"""Main-context ownership, cancellation and recovery without printer access."""
import sys
import unittest
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from mpf.toolhead.ToolheadEnvironment import ToolheadEnvironment
from mpf.toolhead.ToolheadEnvironmentMailbox import EnvironmentMailbox

# Native GL/scene parity is qualified separately; these tests inject the
# producer boundary to exercise failures that a healthy driver will not emit.
with patch.dict(sys.modules, {
    'mpf.toolhead.ToolheadEnvironmentWorker': NS(EnvironmentWorker=object, CaptureJob=lambda *args: NS(
        serial=args[0], soft=args[1], frame=args[2], ready_fence=args[3], generation=args[4], key=args[5])),
    'mpf.toolhead.ToolheadCaptureRecipe': NS(CaptureFreezer=object),
}):
    from mpf.toolhead import ToolheadAsyncEnvironment as module


class AsyncEnvironmentTests(unittest.TestCase):
    def setUp(self):
        owner = self.owner = object.__new__(module.AsyncEnvironment)
        ToolheadEnvironment.__init__(owner, clock=lambda: 10.)
        owner._gl = Mock()
        owner._main_context = object()
        owner._main_pointer = id(owner._main_context)
        owner._main_retired = owner._quarantine = False
        owner._main_format, owner._main_finish = 'cached format', Mock()
        sip = patch.object(module, 'sip', NS(unwrapinstance=id, isdeleted=lambda *_: False,
            wrapinstance=lambda *_: owner._main_context))
        sip.start(); self.addCleanup(sip.stop)
        owner._worker = Mock(mailbox=EnvironmentMailbox(), failure='', timings=None)
        owner._worker.abandoned = Event()
        owner._worker.completed.return_value = ()
        owner._worker.submit.return_value = None
        owner._window = Mock()
        owner._closed = owner._disposed = owner._failure_scheduled = owner._busy = False
        owner.requires_replacement = False
        owner._leases, owner._deletions, owner._reads, owner._shaders = {}, [], set(), set()
        owner._serial, owner._submitted = 0, None
        owner._sampler, owner._delete = Mock(), Mock()
        owner._fence, owner._poll = Mock(return_value=71), Mock(return_value=0x911A)
        owner._retry_timer, owner._fallback = Mock(), Mock()
        owner._surface = Mock()
        owner._fallback_published = None
        owner._freezer = Mock()
        owner._freezer.freeze.return_value = ('frozen', ('retained-wrapper',))

    def test_held_source_stops_adoption_and_submission_until_key_changes(self):
        owner=self.owner
        owner._selected_key=('file','pose');owner._hard='file'
        owner._storage=object();token=object()
        owner._recovery_hold=(token,owner._storage,owner._selected_key)
        snapshot=Mock(return_value='snapshot')
        owner.step(owner._gl,owner._main_context,'file','pose',snapshot)
        snapshot.assert_not_called();owner._worker.submit.assert_not_called()
        owner.release_recovery(object())
        self.assertTrue(owner._held_recovery())
        owner.step(owner._gl,owner._main_context,'file','new pose',snapshot)
        self.assertIsNone(owner._recovery_hold)
        snapshot.assert_called_once();owner._worker.submit.assert_called_once()

    def test_generation_changes_wake_retirement_without_a_successor_job(self):
        for deferred in (True, False):
            payload=self.publish()
            retired=[]
            def wake(retired=retired):
                result=self.owner._worker.mailbox.take_retirement()
                if result is not None:
                    retired.append(result[1])
                    self.owner._worker.mailbox.retired(result[0])
            self.owner._worker.wake.side_effect=wake
            if deferred:
                self.owner.defer()
            else:
                self.owner._freezer.freeze.return_value=None
                self.owner._fallback.available=False
                self.owner.step(self.owner._gl,self.owner._main_context,'replacement','pose',lambda:'scene')
            self.assertEqual(retired,[payload])
            self.owner._worker.submit.assert_not_called()

    def test_unchanged_directional_map_sleeps_until_scene_changes(self):
        owner = self.owner
        owner.available = True
        owner.descriptor = NS(projection=1)
        owner._hard, owner._published = 'file', 'pose'
        owner._selected_key = owner._submitted = ('file', 'pose')
        owner._next = 0  # Even long after the old periodic refresh deadline.
        snapshot = Mock(return_value='snapshot')
        self.assertFalse(owner.step(owner._gl, owner._main_context, 'file', 'pose', snapshot))
        snapshot.assert_not_called()
        self.assertIsNone(owner.wake_delay)
        self.assertTrue(owner.step(owner._gl, owner._main_context, 'file', 'changed pose', snapshot))
        snapshot.assert_called_once()
        owner._worker.submit.assert_called_once()

    def test_new_geometry_generation_rebuilds_unchanged_directional_pose(self):
        owner = self.owner
        owner.available = True
        owner.descriptor = NS(projection=1)
        owner._hard, owner._published = 'file', 'pose'
        owner._selected_key = owner._submitted = ('file', 'pose')
        snapshot = Mock(return_value='snapshot')
        self.assertTrue(owner.step(owner._gl, owner._main_context, 'other file', 'pose', snapshot))
        snapshot.assert_called_once()

    def publish(self, serial=1, soft='pose'):
        payload = NS(colour=3, depth=4, descriptor='exact descriptor', soft=soft, serial=serial)
        box = self.owner._worker.mailbox
        token = box.reserve()
        box.complete(token, payload, 99)
        return payload

    def query_binding(self, failure=False):
        payload = self.publish()
        payload.key, payload.frame = ('file', 'captured pose'), 'frozen source and uniforms'
        geometry = payload.geometry = NS(key=('source publication', 'model', 'prefix'), epoch=7, reads=0)
        @contextmanager
        def read(gl, context, epoch):
            self.assertIs(gl, self.owner._gl)
            self.assertIs(context, self.owner._main_context)
            self.assertEqual(epoch, 7)
            geometry.reads += 1
            try: yield geometry
            finally:
                geometry.reads -= 1
                if failure: raise module.GeometryUncertain(geometry, 'uncertain query state restoration')
        geometry.read = read
        self.owner._adopt()
        self.owner._worker.wake.reset_mock()
        return self.owner._storage, payload, geometry

    def test_runtime_scope_requires_selected_completed_cohort_and_always_withdraws_enabled_flag(self):
        owner = self.owner
        binding, payload, geometry = self.query_binding()
        geometry.apply = Mock()
        shader = Mock()
        owner._selected_key = payload.key
        self.assertTrue(owner.recovery_ready)
        self.assertEqual(owner.recovery_identity, (payload.serial, geometry.key))
        with owner.query_bindings(shader) as admitted:
            self.assertIs(admitted, geometry)
            geometry.apply.assert_called_once_with(shader)
            self.assertIsNotNone(binding._use)
        self.assertEqual(shader.setUniformValue.call_args.args, ('mpf_recoveryEnabled', 0))
        self.assertIsNone(binding._use)
        geometry.apply.reset_mock()
        owner._selected_key = ('file', 'new pending pose')
        self.assertFalse(owner.recovery_ready)
        self.assertIsNone(owner.recovery_identity)
        with owner.query_bindings(shader) as admitted: self.assertIsNone(admitted)
        geometry.apply.assert_not_called()
        self.assertIs(owner._storage, binding)
        owner._selected_key = payload.key
        geometry.apply.side_effect = RuntimeError('uniform publication failed')
        with self.assertRaisesRegex(RuntimeError, 'publication'):
            with owner.query_bindings(shader): pass
        self.assertEqual(shader.setUniformValue.call_args.args, ('mpf_recoveryEnabled', 0))
        self.assertIsNone(binding._use)
        owner.defer(); self.assertIsNone(owner._selected_key)

    def test_query_only_and_nested_queries_share_one_last_use_ticket(self):
        owner = self.owner
        binding, payload, geometry = self.query_binding()
        with binding.query(payload.key, geometry.key) as admitted:
            self.assertIs(admitted, geometry)
            self.assertEqual(owner._reads, {binding})
            with binding.query(payload.key, geometry.key):
                self.assertEqual(geometry.reads, 2)
                self.assertEqual(binding._queries, 2)
            self.assertEqual(binding._queries, 1)
            self.assertIsNotNone(binding._use)
            owner._fence.assert_not_called()
        self.assertEqual(owner._reads, set())
        self.assertIsNone(binding._use)
        owner._fence.assert_called_once()
        owner._worker.wake.assert_called_once()

    def test_cube_release_cannot_end_ticket_while_query_is_active(self):
        owner = self.owner
        binding, payload, geometry = self.query_binding()
        owner._gl.glGetIntegerv.return_value = 12
        binding.bind(7); binding.depth.bind(6)
        with binding.query(payload.key, geometry.key):
            binding.release(7); binding.depth.release(6)
            self.assertEqual(binding._bindings, {})
            self.assertIsNotNone(binding._use)
            self.assertIsNone(owner._worker.mailbox.take_retirement())
            owner._fence.assert_not_called()
        owner._fence.assert_called_once()

    def test_retained_old_map_cannot_query_new_scene_source_or_prefix(self):
        owner = self.owner
        binding, payload, geometry = self.query_binding()
        for selected, source in ((None, geometry.key), (('file', 'new pose'), geometry.key),
                                 (payload.key, ('source publication', 'model', 'new prefix'))):
            with binding.query(selected, source) as admitted: self.assertIsNone(admitted)
        self.assertEqual(geometry.reads, 0)
        self.assertEqual(owner._reads, set())
        owner._fence.assert_not_called()
        payload.geometry = None
        with binding.query(payload.key, geometry.key) as admitted: self.assertIsNone(admitted)
        owner._fence.assert_not_called()

    def test_query_body_failure_still_restores_and_fences_read(self):
        owner = self.owner
        binding, payload, geometry = self.query_binding()
        with self.assertRaisesRegex(RuntimeError, 'draw failed'):
            with binding.query(payload.key, geometry.key): raise RuntimeError('draw failed')
        self.assertEqual(geometry.reads, 0)
        self.assertEqual(owner._reads, set())
        owner._fence.assert_called_once()
        self.assertFalse(owner._quarantine)

    def test_query_restoration_failure_quarantines_pair_and_main_source(self):
        owner = self.owner
        owner._leases[1] = 'original main VBO wrapper'
        binding, payload, geometry = self.query_binding(failure=True)
        self.addCleanup(module._quarantined.discard, owner)
        with self.assertRaisesRegex(module.GeometryUncertain, 'uncertain query state'):
            with binding.query(payload.key, geometry.key): pass
        self.assertTrue(owner._quarantine)
        self.assertIn(owner, module._quarantined)
        self.assertEqual(owner._leases, {1: 'original main VBO wrapper'})
        self.assertIsNotNone(binding._use)
        self.assertIsNone(owner._worker.mailbox.take_retirement())
        owner._fence.assert_not_called()
        owner._worker.abandon.assert_called_once()

    def test_exact_main_finish_cannot_return_still_active_query_scope(self):
        owner = self.owner
        binding, payload, geometry = self.query_binding()
        with binding.query(payload.key, geometry.key):
            owner._worker.mailbox.close()
            self.assertIsNone(owner._worker.mailbox.take_retirement())
            with patch.object(module.QOpenGLContext, 'currentContext', return_value=owner._main_context):
                with self.assertRaisesRegex(RuntimeError, 'query scope'): owner._finish_main()
            self.assertIsNotNone(binding._use)
            self.assertIsNone(owner._worker.mailbox.take_retirement())
            # More work is permitted until lexical exit; the retained ticket
            # must still protect its source regardless of the finish request.
            owner._gl.glDrawArrays(4, 0, 3)
        self.assertEqual(owner._reads, set())
        owner._main_finish.assert_not_called()
        owner._fence.assert_called_once()
        self.assertIsNotNone(owner._worker.mailbox.take_retirement())

    def test_context_destruction_with_active_query_quarantines_future_reads(self):
        owner = self.owner
        owner._main_context = Mock(); owner._main_pointer = id(owner._main_context)
        owner._leases[1] = 'main source wrapper'
        binding, payload, geometry = self.query_binding()
        self.addCleanup(module._quarantined.discard, owner)
        with binding.query(payload.key, geometry.key):
            with patch.object(module.QOpenGLContext, 'currentContext', return_value=owner._main_context):
                owner._context_destroyed()
            self.assertTrue(owner._main_retired)
            self.assertTrue(owner._quarantine)
            self.assertIsNotNone(binding._use)
            self.assertIsNone(owner._worker.mailbox.take_retirement())
        self.assertEqual(owner._leases, {1: 'main source wrapper'})
        self.assertIsNotNone(binding._use)
        owner._main_finish.assert_not_called()
        owner._fence.assert_not_called()
        owner._worker.abandon.assert_called_once()

    def test_old_result_does_not_clear_newer_submission_and_duplicate_it(self):
        owner = self.owner
        owner._serial, owner._busy = 2, True
        payload = self.publish(serial=1)
        owner._adopt()
        self.assertTrue(owner._busy)
        self.assertIs(owner._storage.result, payload)
        owner._submitted, owner._hard = ('file', 'latest'), 'file'
        self.assertTrue(owner.step(owner._gl, owner._main_context, 'file', 'latest', Mock()))
        owner._freezer.freeze.assert_not_called()

    def test_hard_change_rejects_ready_old_frame_before_adoption(self):
        owner = self.owner
        owner._hard = 'old file'
        self.publish()
        owner.step(owner._gl, owner._main_context, 'new file', 'new pose', lambda: 'new snapshot')
        self.assertFalse(owner.available)
        job = owner._worker.submit.call_args.args[0]
        self.assertEqual(job.generation, owner._worker.mailbox.generation)
        self.assertEqual(job.frame, 'frozen')
        self.assertEqual(owner._leases[job.serial], ('retained-wrapper',))

    def test_main_finish_rejects_another_context_even_if_it_shares_resources(self):
        owner = self.owner
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=object()), \
                patch.object(module, 'procedure'):
            module._MainFinish(owner).run()
            owner._main_finish.assert_not_called()
            owner._worker.host_drained.set.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'original context'): owner._finish_main()

    def test_source_replacement_keeps_first_upload_fallback_waking(self):
        owner = self.owner
        owner._hard, owner._busy = 'old file', True
        owner._freezer.freeze.return_value = None
        owner._fallback.available = False
        owner.step(owner._gl, owner._main_context, 'new file', 'pose', lambda: 'not uploaded')
        owner._fallback.step.assert_called_once()
        self.assertIsNotNone(owner.wake_delay)
        self.assertAlmostEqual(owner.wake_delay, .05)

    def test_fallback_publications_keep_facade_revision_monotonic(self):
        owner = self.owner
        owner.revision = 9
        owner._freezer.freeze.return_value = None
        owner._fallback.available, owner._fallback.revision = True, 2
        owner.step(owner._gl, owner._main_context, 'file', 'pose', lambda: 'snapshot')
        self.assertEqual(owner.revision, 10)
        owner._next = 0
        owner.step(owner._gl, owner._main_context, 'file', 'pose', lambda: 'snapshot')
        self.assertEqual(owner.revision, 10)
        owner._next, owner._fallback.revision = 0, 3
        owner.step(owner._gl, owner._main_context, 'file', 'pose', lambda: 'snapshot')
        self.assertEqual(owner.revision, 11)

    def test_verified_finish_returns_abandoned_read_and_undeleted_input_fences(self):
        owner = self.owner
        self.publish(); owner._adopt()
        binding = owner._storage
        binding._use, _ = owner._worker.mailbox.begin_read()
        owner._reads.add(binding)
        owner._worker.mailbox.close()
        owner._worker.completed.return_value = ((7, 82),)
        owner._leases[7] = 'wrapper'
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=owner._main_context), \
                patch.object(module, 'procedure', return_value=Mock()):
            owner._finish_main()
        owner._main_finish.assert_called_once_with()
        self.assertIsNone(binding._use)
        self.assertEqual(owner._reads, set())
        self.assertEqual(owner._leases, {})
        self.assertEqual(owner._deletions, [])
        owner._worker.host_drained.set.assert_called_once()
        self.assertIsNotNone(owner._worker.mailbox.take_retirement())
        self.assertIn(82, [call.args[0].value for call in owner._delete.call_args_list])

    def test_failed_idle_worker_requests_its_own_cleanup_frame_once(self):
        owner = self.owner
        owner._worker.failure = 'injected fence failure'
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=None):
            owner._arrived(); owner._arrived()
        self.assertTrue(owner._closed)
        self.assertTrue(owner.requires_replacement)
        owner._window.scheduleRenderJob.assert_called_once()
        owner._window.update.assert_called_once()
        owner._worker.stop.assert_called_once()

    def test_shader_withdrawal_fault_cannot_skip_other_unit_or_worker_stop(self):
        owner = self.owner
        shader = Mock()
        shader.setTexture.side_effect = [RuntimeError('first slot retired'), None]
        owner._shaders.add(shader)
        owner.close(); owner.close()
        self.assertEqual([call.args for call in shader.setTexture.call_args_list], [(7, None), (6, None)])
        owner._worker.stop.assert_called_once()
        owner._fallback.close.assert_called_once()

    def test_frozen_source_frames_drop_only_after_normal_worker_retirement(self):
        owner = self.owner
        owner._main_context = Mock()
        owner._main_pointer = id(owner._main_context)
        storage, freezer = object(), owner._freezer
        owner._storage = storage
        owner._leases[1] = 'main VBO wrapper'
        owner.close()
        self.assertIs(owner._storage, storage)
        self.assertIs(owner._freezer, freezer)
        self.assertEqual(owner._leases, {1: 'main VBO wrapper'})
        owner._worker.completed.return_value = ((1, 0),)
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=owner._main_context), \
                patch.object(module.QCoreApplication, 'instance', return_value=Mock()):
            owner._finished()
        self.assertEqual(owner._leases, {})
        self.assertIsNone(owner._storage)
        self.assertIsNone(owner._freezer)

    def test_quarantine_keeps_frozen_source_frames_and_main_wrappers(self):
        owner = self.owner
        owner._main_context = Mock()
        storage, freezer = object(), owner._freezer
        owner._storage = storage
        owner._leases[1] = 'unverified main VBO wrapper'
        owner._quarantine = True
        with patch.object(module.QCoreApplication, 'instance', return_value=Mock()):
            owner._finished()
        self.assertIs(owner._storage, storage)
        self.assertIs(owner._freezer, freezer)
        self.assertEqual(owner._leases, {1: 'unverified main VBO wrapper'})
        owner._worker.completed.assert_not_called()

    def test_worker_local_quarantine_roots_sources_without_ready_or_later_frame(self):
        owner = self.owner
        owner._main_context = Mock()
        owner._group = object()
        storage, freezer = object(), owner._freezer
        owner._storage = storage
        owner._leases[1] = 'unverified main VBO wrapper'
        owner._worker.failure = 'Reflection producer finish failed; resources quarantined'
        owner._worker.abandoned.set()
        module._owners.add(owner)
        self.addCleanup(module._owners.discard, owner)
        self.addCleanup(module._quarantined.discard, owner)
        with patch.object(module.QCoreApplication, 'instance', return_value=Mock()):
            owner._finished()
        self.assertTrue(owner._quarantine)
        self.assertIn(owner, module._quarantined)
        self.assertIs(owner._storage, storage)
        self.assertIs(owner._freezer, freezer)
        self.assertEqual(owner._leases, {1: 'unverified main VBO wrapper'})
        self.assertIn('producer finish failed', owner.failure)
        owner._worker.completed.assert_not_called()
        owner._surface.destroy.assert_not_called()
        owner._worker.context.deleteLater.assert_not_called()
        context = Mock()
        context.format().majorVersion.return_value = 4
        context.shareGroup.return_value = owner._group
        thread = object()
        app = Mock(); app.thread.return_value = thread
        with patch.object(module.QCoreApplication, 'instance', return_value=app), \
                patch.object(module.QThread, 'currentThread', return_value=thread), \
                patch.object(module, 'AsyncEnvironment') as replacement:
            self.assertIsInstance(module.create_environment(Mock(), context, Mock()), ToolheadEnvironment)
        replacement.assert_not_called()

    def test_worker_quarantine_precedes_completed_source_collection(self):
        owner = self.owner
        owner._leases[1] = 'unverified source'
        owner._worker.abandoned.set()
        owner._worker.completed.return_value = ((1, 0),)
        self.addCleanup(module._quarantined.discard, owner)
        owner._collect()
        self.assertTrue(owner._quarantine)
        self.assertEqual(owner._leases, {1: 'unverified source'})
        owner._worker.completed.assert_not_called()

    def test_busy_worker_schedules_no_repeated_foreground_capture_frames(self):
        self.owner._busy = True
        self.assertIsNone(self.owner.wake_delay)
        self.owner.defer()
        self.assertFalse(self.owner.working)
        self.assertAlmostEqual(self.owner.wake_delay, .05)

    def test_consumer_restores_both_units_and_ends_one_shared_read(self):
        owner = self.owner
        self.publish(); owner._adopt()
        binding = owner._storage
        owner._gl.glGetIntegerv.return_value = 12
        binding.bind(7); binding.depth.bind(6)
        self.assertEqual(len(owner._reads), 1)
        binding.release(7)
        self.assertIsNotNone(binding._use)
        binding.depth.release(6)
        self.assertIsNone(binding._use)
        self.assertEqual(owner._reads, set())
        self.assertEqual(owner._sampler.call_args_list[-1].args, (6, 12))
        self.assertEqual(owner._gl.glActiveTexture.call_args.args, (12,))
        owner._gl.glFlush.assert_called_once()
        binding.close()

    def test_consumer_bind_fault_unwinds_then_fence_fault_confirms_finish(self):
        owner = self.owner
        self.publish(); owner._adopt()
        owner._gl.glGetIntegerv.return_value = 9
        owner._gl.glBindTexture.side_effect = [RuntimeError('texture bind'), None]
        owner._fence.side_effect = RuntimeError('sync unsupported')
        with self.assertRaisesRegex(RuntimeError, 'texture bind'): owner._storage.bind(7)
        self.assertEqual(owner._reads, set())
        self.assertEqual(owner._storage._bindings, {})
        owner._gl.glFinish.assert_called_once()
        owner._gl.glActiveTexture.assert_called_with(9)
        owner._worker.mailbox.close()
        with self.assertRaisesRegex(RuntimeError, 'retired'): owner._storage.bind(7)

    def test_descriptor_replacement_rejects_stale_cached_consumer(self):
        owner = self.owner
        payload = self.publish(); owner._adopt()
        stale = module._ConsumerBindings(owner, NS(colour=5, depth=6))
        with self.assertRaisesRegex(RuntimeError, 'descriptor changed'): stale.bind(7)
        self.assertEqual(owner._reads, set())
        self.assertIs(owner._storage.result, payload)

    def test_poll_wait_retry_and_failed_poll_do_not_publish(self):
        owner = self.owner
        self.publish()
        owner._poll.return_value = 0x911B
        owner._adopt()
        self.assertFalse(owner.available)
        owner._retry_timer.start.assert_called_once()
        owner._poll.return_value = 0x911D
        with self.assertRaisesRegex(RuntimeError, 'producer fence'): owner._adopt()
        self.assertFalse(owner.available)
        owner._poll.return_value = 0x911A
        owner._adopt()
        self.assertTrue(owner.available)

    def test_collect_defers_input_fence_deletion_until_original_context(self):
        owner = self.owner
        owner._leases[4] = 'retained input'
        owner._worker.completed.side_effect = [((4, 92),), ()]
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=object()): owner._collect()
        self.assertEqual(owner._leases, {})
        self.assertEqual(owner._deletions, [92]); owner._delete.assert_not_called()
        owner._worker.sources_released.assert_called_once_with(4)
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=owner._main_context): owner._collect()
        self.assertEqual(owner._deletions, [])
        self.assertEqual(owner._delete.call_args.args[0].value, 92)

    def test_busy_capture_defers_freeze_and_adopts_before_freezing_latest_pose(self):
        owner = self.owner
        owner._hard = 'file'
        owner._submitted = ('file', 'first')
        owner._busy = True
        snapshot = Mock(return_value='latest scene')
        for pose in range(20):
            self.assertTrue(owner.step(owner._gl, owner._main_context, 'file', pose, snapshot))
        snapshot.assert_not_called()
        owner._freezer.freeze.assert_not_called()
        owner._fence.assert_not_called()
        owner._worker.submit.assert_not_called()
        def adopted():
            owner._published, owner._busy = 'first', False
            owner._next = owner._changed_next = 10.15
        with patch.object(owner, '_adopt', side_effect=adopted):
            self.assertTrue(owner.step(owner._gl, owner._main_context, 'file', 'latest', snapshot))
        snapshot.assert_called_once()
        owner._freezer.freeze.assert_called_once_with('latest scene', owner._main_context)
        job = owner._worker.submit.call_args.args[0]
        self.assertEqual(job.key, ('file', 'latest'))

    def test_early_completion_preserves_changed_scene_deadline(self):
        owner = self.owner
        owner._hard, owner._busy = 'file', True
        owner._submitted = ('file', 'first')
        owner._next = owner._changed_next = 11.
        def adopted():
            owner._published, owner._busy = 'first', False
            owner._next = owner._changed_next = 10.15
        with patch.object(owner, '_adopt', side_effect=adopted):
            self.assertFalse(owner.step(owner._gl, owner._main_context, 'file', 'latest', Mock()))
        owner._freezer.freeze.assert_not_called()
        self.assertEqual(owner._next, 10.15)

    def test_pose_returning_to_inflight_capture_needs_no_successor(self):
        owner = self.owner
        owner._hard, owner._busy = 'file', True
        owner._submitted = ('file', 'first')
        snapshot = Mock()
        owner.step(owner._gl, owner._main_context, 'file', 'changed', snapshot)
        def adopted():
            owner._published, owner._busy = 'first', False
            owner.available = True
            owner.descriptor = NS(projection=1)
        with patch.object(owner, '_adopt', side_effect=adopted):
            self.assertFalse(owner.step(owner._gl, owner._main_context, 'file', 'first', snapshot))
        snapshot.assert_not_called()
        self.assertIsNone(owner.wake_delay)

    def test_submit_replacement_retires_input_and_missing_ready_fence_rejects(self):
        owner = self.owner
        owner._worker.submit.return_value = NS(serial=9, ready_fence=81)
        owner._leases[9] = 'previous input'
        owner.step(owner._gl, owner._main_context, 'file', 'pose', lambda: 'scene')
        self.assertNotIn(9, owner._leases)
        self.assertEqual(owner._delete.call_args.args[0].value, 81)
        owner._next = owner._retry = 0
        owner._fence.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'readiness fence'):
            owner.step(owner._gl, owner._main_context, 'new file', 'new pose', lambda: 'scene')
        self.assertEqual(owner._serial, 1)

    def test_sealed_submission_and_due_time_do_not_duplicate_work(self):
        owner = self.owner
        owner._next = 11
        self.assertFalse(owner.step(owner._gl, owner._main_context, None, None, Mock()))
        owner._freezer.freeze.assert_not_called()
        owner._next = 0
        owner._worker.submit.side_effect = lambda job: job
        self.assertFalse(owner.step(owner._gl, owner._main_context, 'file', 'pose', lambda: 'scene'))
        self.assertEqual(owner._leases, {})
        owner.close()
        self.assertFalse(owner.step(owner._gl, owner._main_context, 'file', 'pose', Mock()))
        self.assertIsNone(owner.wake_delay)
        owner.requires_replacement = True
        owner._retry = 12
        self.assertEqual(owner.wake_delay, 2.)
        owner.defer()

    def test_failure_message_is_bounded_and_owner_finishes_once(self):
        owner = self.owner
        owner._main_context = Mock()
        self.assertFalse(owner.fail('x' * 400))
        self.assertEqual(len(owner.failure), 200)
        owner._deletions = [8]
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=None), \
                patch.object(module.QCoreApplication, 'instance', return_value=Mock()):
            owner._finished(); owner._finished()
        owner._surface.destroy.assert_called_once()
        owner._worker.context.deleteLater.assert_called_once()
        owner._window.scheduleRenderJob.assert_called_once()

    def test_context_destruction_and_shutdown_finish_on_exact_main_context(self):
        owner = self.owner
        context = owner._main_context = Mock()
        context.makeCurrent.return_value = True
        surface = Mock()
        with patch.object(module, 'QOffscreenSurface', return_value=surface), \
                patch.object(module.QOpenGLContext, 'currentContext', return_value=None), \
                patch.object(owner, '_finish_main') as finish:
            owner._context_destroyed()
        finish.assert_called_once(); context.doneCurrent.assert_called_once()
        surface.destroy.assert_called_once()
        owner._closed = False
        owner._worker.isRunning.return_value = True
        with patch.object(module, 'QOffscreenSurface', return_value=surface), \
                patch.object(owner, '_finish_main') as finish, patch.object(owner, '_finished'):
            owner._shutdown()
        finish.assert_not_called(); self.assertEqual(owner._worker.wait.call_count, 2)
        self.assertEqual(context.doneCurrent.call_count, 1)

    def test_retired_context_address_cannot_admit_a_replacement_context(self):
        owner = self.owner
        owner._main_retired = True
        owner._deletions = [81]
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=object()), \
                patch.object(module.sip, 'unwrapinstance', return_value=owner._main_pointer):
            module._MainFinish(owner).run(); owner._collect(); owner._context_destroyed()
        owner._main_finish.assert_not_called(); owner._delete.assert_not_called()
        self.assertEqual(owner._deletions, [81])

    def test_live_shutdown_confirms_main_finish_before_waiting_and_collecting(self):
        owner = self.owner
        owner._main_context = Mock()
        owner._main_context.makeCurrent.return_value = True
        owner._worker.isRunning.return_value = True
        surface = Mock()
        with patch.object(module, 'QOffscreenSurface', return_value=surface), \
                patch.object(owner, '_finish_main') as finish, patch.object(owner, '_finished'):
            owner._shutdown()
        finish.assert_called_once(); owner._worker.wait.assert_called_once()
        self.assertEqual(owner._main_context.doneCurrent.call_count, 1)
        surface.destroy.assert_called_once()

    def test_failed_activation_quarantines_pinned_read_and_never_calls_dead_context(self):
        owner = self.owner
        self.addCleanup(module._quarantined.discard, owner)
        self.publish(); owner._adopt()
        binding = owner._storage
        binding._use, _ = owner._worker.mailbox.begin_read()
        binding._bindings = {7: (0, 0), 6: (0, 0)}
        owner._reads.add(binding)
        owner._leases[8] = 'retained input'
        context = owner._main_context = Mock()
        context.makeCurrent.return_value = False
        with patch.object(module, 'QOffscreenSurface', return_value=Mock()), \
                patch.object(module.QOpenGLContext, 'currentContext', return_value=None):
            owner._context_destroyed()
        self.assertTrue(owner._quarantine); self.assertTrue(owner._main_retired)
        owner._worker.abandon.assert_called_once()
        owner._worker.host_drained.set.assert_not_called()
        owner._arrived()
        owner._window.scheduleRenderJob.assert_not_called()
        owner._gl.reset_mock(); owner._fence.reset_mock()
        binding.release(7); binding.depth.release(6); binding._finish()
        self.assertIsNotNone(binding._use)
        self.assertEqual(owner._reads, {binding})
        self.assertEqual(owner._leases, {8: 'retained input'})
        self.assertFalse(owner._worker.mailbox.drained)
        self.assertEqual(owner._gl.mock_calls, [])
        owner._fence.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'context was retired'): binding.bind(7)
        with patch.object(module.QCoreApplication, 'instance', return_value=Mock()):
            owner._shutdown()
        owner._surface.destroy.assert_not_called()
        owner._worker.context.deleteLater.assert_not_called()
        self.assertIn(owner, module._quarantined)

    def test_retired_verified_consumer_late_release_only_discards_python_bindings(self):
        owner = self.owner
        self.publish(); owner._adopt()
        binding = owner._storage
        binding._use, _ = owner._worker.mailbox.begin_read()
        binding._bindings = {7: (0, 0), 6: (0, 0)}
        owner._reads.add(binding)
        with patch.object(module.QOpenGLContext, 'currentContext', return_value=owner._main_context):
            owner._finish_main()
        owner._main_retired = True
        owner._gl.reset_mock()
        binding.release(7); binding.depth.release(6)
        self.assertIsNone(binding._use); self.assertEqual(binding._bindings, {})
        self.assertEqual(owner._gl.mock_calls, [])

    def test_deleted_wrapper_and_failed_finish_are_owned_shutdown_failures(self):
        owner = self.owner
        self.addCleanup(module._quarantined.discard, owner)
        with patch.object(module.sip, 'wrapinstance', side_effect=RuntimeError('wrapper retired')):
            owner._context_destroyed()
        self.assertTrue(owner._quarantine)
        owner._main_retired = False
        owner._worker.isRunning.return_value = True
        with patch.object(module.sip, 'isdeleted', return_value=True), \
                patch.object(owner, '_finished'):
            owner._shutdown()
        owner._worker.wait.assert_called_once()
        owner._worker.host_drained.set.assert_not_called()

    def test_failed_finish_in_render_job_and_shutdown_quarantines_without_escaping(self):
        owner = self.owner
        self.addCleanup(module._quarantined.discard, owner)
        owner._failure_scheduled = True
        with patch.object(owner, '_is_main_current', return_value=True), \
                patch.object(owner, '_finish_main', side_effect=RuntimeError('finish failed')):
            module._MainFinish(owner).run()
        self.assertFalse(owner._failure_scheduled)
        self.assertTrue(owner._quarantine)
        owner._worker.isRunning.return_value = True
        owner._main_context = Mock()
        owner._main_context.makeCurrent.return_value = True
        surface = Mock()
        with patch.object(module, 'QOffscreenSurface', return_value=surface), \
                patch.object(owner, '_finish_main', side_effect=RuntimeError('finish failed')), \
                patch.object(owner, '_finished'):
            owner._shutdown()
        owner._worker.wait.assert_called_once()
        surface.destroy.assert_called_once()
        owner._worker.host_drained.set.assert_not_called()

    def test_constructor_rejects_invalid_capabilities_and_factory_falls_back(self):
        context, surface, worker, app = Mock(), Mock(), Mock(), Mock()
        context.shareGroup.return_value = 'shared group'
        surface.isValid.return_value = True
        context.create.return_value = True
        ctx_class = Mock(return_value=context)
        ctx_class.areSharing.return_value = True
        with patch.object(module, 'QOpenGLContext', ctx_class), \
                patch.object(module, 'QOffscreenSurface', return_value=surface), \
                patch.object(module, 'QTimer', return_value=Mock()), \
                patch.object(module, 'native_depth_format', return_value=0x81A6), \
                patch.object(module, 'procedure', return_value=Mock()), \
                patch.object(module, 'EnvironmentWorker', return_value=worker) as factory, \
                patch.object(module, 'CaptureFreezer', return_value=Mock()), \
                patch.object(module.QCoreApplication, 'instance', return_value=app), \
                patch.object(module, '_owners', set()):
            owner = module.AsyncEnvironment(Mock(), context, Mock())
            self.assertEqual(owner._group, 'shared group')
            self.assertNotIn('query_factory',factory.call_args.kwargs)
            worker.start.assert_called_once()
            context.moveToThread.assert_called_once_with(worker)
            surface.isValid.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'surface unavailable'):
                module.AsyncEnvironment(Mock(), context, Mock())
            surface.isValid.return_value = True
            ctx_class.areSharing.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'context sharing'):
                module.AsyncEnvironment(Mock(), context, Mock())
            surface.destroy.assert_called_once()
        context.format.return_value.majorVersion.return_value = 4
        app.thread.return_value = 'GUI thread'
        with patch.object(module.QCoreApplication, 'instance', return_value=app), \
                patch.object(module.QThread, 'currentThread', return_value='GUI thread'), \
                patch.object(module, '_owners', set()), \
                patch.object(module, 'AsyncEnvironment', side_effect=RuntimeError('optional unavailable')):
            self.assertIsInstance(module.create_environment(Mock(), context, Mock()), ToolheadEnvironment)
        with patch.object(module.QCoreApplication, 'instance', return_value=None):
            self.assertIsInstance(module.create_environment(Mock(), context, None), ToolheadEnvironment)


if __name__ == '__main__': unittest.main()
