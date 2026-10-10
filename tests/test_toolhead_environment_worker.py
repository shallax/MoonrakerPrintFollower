"""Producer cancellation and fence retirement with an injected graphics driver."""
import sys
import unittest
import weakref
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from mpf.toolhead import ToolheadEnvironmentGeometry as query_geometry

with patch.dict(sys.modules, {
    'mpf.toolhead.ToolheadCaptureGL': NS(RawBindings=object),
    'mpf.toolhead.ToolheadCaptureRecipe': NS(CaptureScene=object),
    'mpf.toolhead.ToolheadEnvironmentGeometry': query_geometry,
}):
    from mpf.toolhead import ToolheadEnvironmentWorker as module


class EnvironmentWorkerTests(unittest.TestCase):
    def run_worker(self, *, failure='', cancelled=False, no_fence=False, bad_poll=False, unavailable=False, stale=False,
                   idle=False, reserved=False, queued=False, close_fault=False, held_reader=False, sealed_publication=False,
                   query=False, query_cancelled=False):
        context = Mock()
        context.makeCurrent.return_value = not unavailable
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        frame = NS(retained_source_bytes=lambda: 512) if query else 'immutable scene'
        job = module.CaptureJob(1, 'pose', frame, 91, worker.mailbox.generation, ('file', 'pose'))
        if not idle: worker.submit(job)
        else:
            worker._condition.wait = Mock(side_effect=lambda *_: worker.submit(job))
        if reserved:
            reserve = worker.mailbox.reserve
            first = True
            def delayed_reserve(*args):
                nonlocal first
                if first: first = False; return None
                return reserve(*args)
            worker.mailbox.reserve = delayed_reserve
        if stale:
            worker.mailbox.change_generation()
            worker.ready.connect(worker.stop)
        driver = Mock()
        deleted, submitted, publications = [], [], []
        geometry = Mock()
        if query:
            def prepare(_gl, _context, active, retained, cancel):
                self.assertIs(_gl, driver)
                self.assertIs(_context, context)
                self.assertIs(active, job)
                self.assertEqual(retained, ())
                self.assertNotIn(91, deleted, 'input readiness was deleted before source admission')
                self.assertFalse(cancel())
                if query_cancelled:
                    worker.stop()
                    self.assertTrue(cancel())
                    raise RuntimeError('cancelled clean allocation')
                return geometry
            worker.query_factory = prepare
            def close_geometry():
                self.assertEqual(tuple(worker._completed), ())
                self.assertTrue(set(submitted) - {0} <= set(deleted))
                if publications and not sealed_publication: self.assertIn(999, deleted)
            geometry.close.side_effect = close_geometry
        sequence = iter(range(100, 200))
        def fence(*_):
            value = 0 if no_fence else next(sequence)
            submitted.append(value)
            return value
        statuses = iter([0x911D, 0x911A] if bad_poll else [0x911B, 0x911A])
        def poll(value, *_):
            self.assertNotIn(1, [serial for serial, _fence in worker._completed],
                'source was acknowledged before its final pair fence drained')
            if value.value == 999:
                # Last consumer GPU use is still outstanding. Its original
                # main VBO wrapper must not have been returned at capture end.
                self.assertEqual(tuple(worker._completed), ())
            return next(statuses, 0x911A)
        def resolve(_context, name, *_signature):
            return {'glFenceSync': fence, 'glClientWaitSync': poll,
                    'glDeleteSync': lambda value: deleted.append(value.value), 'glWaitSync': Mock()}[name]
        scene = Mock()
        environment = Mock(revision=0, failure='', descriptor='paired bounds')
        environment._storage = NS(front=11, front_depth=12, select=Mock())
        count = 0
        def step(*args):
            nonlocal count
            count += 1
            self.assertIs(args[-1](), scene.snapshot.return_value)
            if failure:
                if queued: worker.submit(module.CaptureJob(2, 'next', 'immutable next scene', 92))
                environment.failure = failure
            if cancelled: worker.stop()
            if count == 4: environment.revision = 1
        environment.step.side_effect = step
        if close_fault:
            environment.close.side_effect = RuntimeError('environment close fault')
            scene.close.side_effect = RuntimeError('scene close fault')
        complete = worker.mailbox.complete
        def publish(token, result, producer, **kwargs):
            if sealed_publication: worker.mailbox.change_generation()
            accepted = complete(token, result, producer, **kwargs)
            publications.append(result)
            if sealed_publication:
                self.assertFalse(accepted)
                worker.stop()
                return accepted
            use, _fence = worker.mailbox.begin_poll()
            _token, _payload, consumed = worker.mailbox.end_poll(use, True)
            if consumed: deleted.append(consumed)  # Main owns an adopted producer fence.
            read, _result = worker.mailbox.begin_read()
            if held_reader:
                def finish_read(*_args):
                    self.assertEqual(tuple(worker._completed), ())
                    self.assertIs(_result.frame, job.frame)
                    self.assertIsNone(worker.mailbox.take_retirement())
                    worker.mailbox.end_read(read, 999)
                worker._condition.wait = Mock(side_effect=finish_read)
            else:
                worker.mailbox.end_read(read, 999)
            worker.stop()
            return accepted
        worker.mailbox.complete = publish
        # The exact-main-context finish is independently proved by native Qt tests.
        worker.host_drained.set()
        with patch.object(module, 'procedure', side_effect=resolve), \
                patch.object(module, 'RawBindings', return_value=driver), \
                patch.object(module, 'CaptureScene', return_value=scene), \
                patch.object(module, 'ToolheadEnvironment', return_value=environment):
            worker.run()
        self.assertTrue(worker.mailbox.drained)
        expected = ((1, 91 if unavailable or stale or query_cancelled else 0),)
        if queued: expected = ((2, 0),) + expected
        self.assertEqual(worker.completed(), expected)
        self.assertEqual(worker.completed(), ())
        context.doneCurrent.assert_called_once()
        context.moveToThread.assert_called_once_with(worker.main_thread)
        self.assertIs(worker.submit(job), job)
        if unavailable:
            self.assertIn('context unavailable', worker.failure)
            scene.close.assert_not_called()
        else:
            scene.close.assert_called_once(); environment.close.assert_called_once()
            if not stale and not query_cancelled: self.assertIn(91, deleted)
            self.assertEqual(len(deleted), len(set(deleted)))
            if not failure and not cancelled and not stale and not query_cancelled:
                self.assertEqual(publications[0], module.CaptureResult(11, 12, 'paired bounds', 'pose', 1, job.frame,
                    key=job.key, geometry=geometry if query else None))
                self.assertEqual(worker.timings[2], 4)
                if not sealed_publication: self.assertIn(999, deleted)
                self.assertEqual(set(submitted) - {0}, set(deleted) - {91, 999})
            else: self.assertFalse(publications)
        if query and not query_cancelled: geometry.close.assert_called_once()
        if query_cancelled:
            geometry.close.assert_not_called()
            self.assertEqual(worker.failure, '')
        return worker

    def test_idle_ready_pair_generation_wake_retires_without_new_submission(self):
        context=Mock();worker=module.EnvironmentWorker(context,object(),object(),{},0x81A6)
        token=worker.mailbox.reserve()
        payload=module.CaptureResult(11,12,'descriptor','pose',7,'retained source')
        worker.mailbox.complete(token,payload,91)
        def defer(*_):
            worker.mailbox.change_generation()
            worker.wake()
        worker._condition.wait=Mock(side_effect=defer)
        worker.ready.connect(worker.stop)
        deleted=[]
        def procedure(_context,name,*_types):
            return {'glFenceSync':Mock(),'glWaitSync':Mock(),
                'glClientWaitSync':lambda *_:0x911A,
                'glDeleteSync':lambda value:deleted.append(value.value)}[name]
        with patch.object(module,'procedure',side_effect=procedure), \
                patch.object(module,'RawBindings',return_value=Mock()), \
                patch.object(module,'CaptureScene',return_value=Mock()), \
                patch.object(module,'ToolheadEnvironment',return_value=Mock()):
            worker.run()
        self.assertEqual(worker.failure,'')
        self.assertEqual(worker.completed(),((7,0),))
        self.assertEqual(deleted,[91])
        worker._condition.wait.assert_called_once_with(None)
        self.assertTrue(worker.mailbox.drained)

    def test_wake_between_retirement_scan_and_wait_is_not_lost(self):
        context=Mock();worker=module.EnvironmentWorker(context,object(),object(),{},0x81A6)
        original=worker.mailbox.take_retirement;scans=[]
        def scan():
            scans.append(None)
            if len(scans)==1:worker.wake()
            else:worker.stop()
            return original()
        worker.mailbox.take_retirement=scan
        worker._condition.wait=Mock(side_effect=AssertionError('lost wake'))
        with patch.object(module,'procedure',return_value=Mock()), \
                patch.object(module,'RawBindings',return_value=Mock()), \
                patch.object(module,'CaptureScene',return_value=Mock()), \
                patch.object(module,'ToolheadEnvironment',return_value=Mock()):
            worker.run()
        self.assertEqual(worker.failure,'')
        worker._condition.wait.assert_not_called()
        self.assertEqual(len(scans),2)

    def test_idle_worker_waits_for_notification_instead_of_polling(self):
        worker = self.run_worker(idle=True)
        self.assertIn(((None,), {}), [(call.args, call.kwargs) for call in worker._condition.wait.call_args_list])

    def test_query_cohort_transfers_with_pair_and_closes_after_last_consumer(self):
        self.run_worker(query=True, held_reader=True)

    def test_query_cohort_of_cancelled_publication_drains_before_source_ack(self):
        self.run_worker(query=True, sealed_publication=True)

    def test_query_cohort_of_failed_or_aborted_capture_is_retired_once(self):
        self.run_worker(query=True, failure='injected capture failure')
        self.run_worker(query=True, cancelled=True)

    def test_cleanly_cancelled_factory_does_not_kill_worker_or_leak_input(self):
        self.run_worker(query=True, query_cancelled=True)

    def test_closed_cohort_stays_budgeted_until_main_actually_releases_source(self):
        for collected in (False, True):
            with self.subTest(collected=collected): self.check_retained_source_budget(collected)

    def test_none_or_clean_cancelled_factory_keeps_uncollected_source_budget(self):
        self.check_retained_source_budget(False, first_none=True)
        self.check_retained_source_budget(False, first_cancel=True)

    def test_cancelled_capture_cache_is_budgeted_after_main_source_ack(self):
        self.check_retained_source_budget(True, second_cancel=True)

    def check_retained_source_budget(self, collected, first_none=False, first_cancel=False, second_cancel=False):
        context, driver, scene, environment = Mock(), Mock(), Mock(), Mock()
        cohorts = [Mock() for _ in range(3)]
        for cohort in cohorts:
            cohort.closed, cohort.incremental_bytes = False, 64
            cohort.close.side_effect = lambda cohort=cohort: setattr(cohort, 'closed', True)
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        jobs = [module.CaptureJob(i+1, f'pose{i}', NS(retained_source_bytes=lambda i=i: 512+i,
            capture_storage_bytes=lambda i=i: 2048+i), 91+i) for i in range(3)]
        seen = []
        def prepare(_gl, _context, job, retained, _cancel):
            seen.append(retained)
            if job.serial == 1 and first_cancel:
                worker.mailbox.change_generation()
                worker.submit(jobs[1])
                raise RuntimeError('clean factory cancellation')
            if job.serial == 1 and first_none: return None
            if job.serial == 3:
                if second_cancel:
                    self.assertFalse(cohorts[0].closed)
                    self.assertTrue(cohorts[1].closed)
                    expected = (512, 513)
                    owners = (cohorts[0], None)
                else:
                    if not first_none and not first_cancel: self.assertTrue(cohorts[0].closed)
                    self.assertFalse(cohorts[1].closed)
                    expected = (513, 513) if collected else (512, 513, 513)
                    owners = (cohorts[1], None) if collected else (
                        None if first_none or first_cancel else cohorts[0], cohorts[1], None)
                self.assertEqual(tuple(entry.source_bytes for entry in retained), expected)
                self.assertEqual(tuple(entry.geometry for entry in retained), owners)
                # The last entry is the actual CaptureScene cache, distinct
                # from released pair leases, with no retired query allocation.
                self.assertEqual(retained[-1].capture_bytes, 2049)
                self.assertEqual(retained[-1].retained_bytes, 513+2049)
            return cohorts[job.serial-1]
        worker.query_factory = prepare
        acknowledge = worker._acknowledge
        def acknowledge_source(serial, fence=0):
            acknowledge(serial, fence)
            if collected: worker.sources_released(serial)
        worker._acknowledge = acknowledge_source
        sequence = iter(range(100, 200)); deleted = []
        procedures = {'glFenceSync': lambda *_: next(sequence), 'glClientWaitSync': Mock(return_value=0x911A),
            'glDeleteSync': lambda value: deleted.append(value.value), 'glWaitSync': Mock()}
        environment._storage = NS(front=11, front_depth=12, select=Mock())
        environment.revision, environment.failure = 0, ''
        def step(*_):
            if second_cancel and scene.snapshot.call_args.args[0] is jobs[1].frame:
                worker.mailbox.change_generation()
                worker.submit(jobs[2])
            else: environment.revision += 1
        environment.step.side_effect = step
        complete = worker.mailbox.complete
        def publish(token, result, fence, **kwargs):
            accepted = complete(token, result, fence, **kwargs)
            use, _ = worker.mailbox.begin_poll()
            _, payload, consumed = worker.mailbox.end_poll(use, True)
            self.assertIs(payload, result)
            if consumed: deleted.append(consumed)
            if result.serial == 3: worker.stop()
            else: worker.submit(jobs[result.serial])
            return accepted
        worker.mailbox.complete = publish
        worker.submit(jobs[0])
        with patch.object(module, 'procedure', side_effect=lambda _context, name, *_: procedures[name]), \
                patch.object(module, 'RawBindings', return_value=driver), \
                patch.object(module, 'CaptureScene', return_value=scene), \
                patch.object(module, 'ToolheadEnvironment', return_value=environment):
            worker.run()
        self.assertEqual(worker.failure, '')
        self.assertTrue(worker.mailbox.drained)
        self.assertEqual(len(seen), 3)
        self.assertEqual(sorted(worker.completed()), [(1, 91 if first_cancel else 0), (2, 0), (3, 0)])
        if first_none or first_cancel: cohorts[0].close.assert_not_called()
        else: cohorts[0].close.assert_called_once()
        for cohort in cohorts[1:]: cohort.close.assert_called_once()

    def test_uncertain_factory_or_query_close_retains_source_without_ack(self):
        for during_prepare in (True, False):
            with self.subTest(during_prepare=during_prepare):
                context, driver, scene, environment, geometry = Mock(), Mock(), Mock(), Mock(), Mock()
                worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6,
                    query_factory=Mock(return_value=geometry))
                source = NS(retained_source_bytes=lambda: 512)
                job = module.CaptureJob(1, 'pose', source, 91)
                worker.submit(job)
                if during_prepare:
                    worker.query_factory.side_effect = module.GeometryUncertain(geometry, 'uncertain upload restoration')
                else:
                    geometry.close.side_effect = RuntimeError('uncertain private buffer deletion')
                environment._storage = NS(front=11, front_depth=12, select=Mock())
                environment.revision, environment.failure = 0, ''
                environment.step.side_effect = lambda *_, environment=environment: setattr(environment, 'revision', 1)
                sequence = iter(range(100, 200))
                deleted = []
                procedures = {'glFenceSync': lambda *_, sequence=sequence: next(sequence), 'glClientWaitSync': Mock(return_value=0x911A),
                    'glDeleteSync': lambda value, deleted=deleted: deleted.append(value.value), 'glWaitSync': Mock()}
                complete = worker.mailbox.complete
                def stop_after_complete(*args, complete=complete, worker=worker, **kwargs):
                    accepted = complete(*args, **kwargs)
                    worker.stop()
                    return accepted
                worker.mailbox.complete = stop_after_complete
                with patch.object(module, 'procedure', side_effect=lambda _context, name, *_, procedures=procedures: procedures[name]), \
                        patch.object(module, 'RawBindings', return_value=driver), \
                        patch.object(module, 'CaptureScene', return_value=scene), \
                        patch.object(module, 'ToolheadEnvironment', return_value=environment):
                    worker.run()
                self.assertTrue(worker.abandoned.is_set())
                self.assertEqual(worker.completed(), ())
                self.assertFalse(worker.mailbox.drained)
                if during_prepare:
                    self.assertIn(job, worker.quarantine)
                    self.assertIs(worker.quarantine[7], geometry)
                    self.assertNotIn(91, deleted)
                    geometry.close.assert_not_called()
                else:
                    self.assertIs(worker.quarantine[4][1].frame, source)
                    self.assertIn(geometry, worker.quarantine[8])
                    geometry.close.assert_called_once()
                scene.close.assert_not_called(); environment.close.assert_not_called()

    def test_complete_capture_drains_bounded_submissions_and_last_consumer(self):
        self.assertEqual(self.run_worker().failure, '')

    def test_published_source_lease_outlives_active_read_and_last_consumer_fence(self):
        self.assertEqual(self.run_worker(held_reader=True).failure, '')

    def test_cancelled_publication_returns_source_once_only_after_pair_drain(self):
        self.assertEqual(self.run_worker(sealed_publication=True).failure, '')

    def test_cancelled_capture_never_publishes_but_returns_its_input_lease(self):
        self.run_worker(cancelled=True)

    def test_failed_capture_retires_both_pairs_and_seals_new_submissions(self):
        self.assertEqual(self.run_worker(failure='injected draw failure').failure, 'injected draw failure')

    def test_unavailable_context_returns_unconsumed_input_fence_to_main(self):
        self.run_worker(unavailable=True)

    def test_failed_fence_allocation_uses_verified_finish(self):
        self.run_worker(no_fence=True)

    def test_failed_wait_requires_main_drain_confirmation(self):
        self.assertIn('fence wait failed', self.run_worker(bad_poll=True).failure)

    def test_stale_pending_generation_returns_input_fence_without_drawing(self):
        self.assertEqual(self.run_worker(stale=True).failure, '')

    def test_abandon_during_idle_wait_retains_pending_job_without_acknowledgement(self):
        context = Mock()
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        job = module.CaptureJob(1, 'pose', 'immutable scene', 91)
        def abandon(*_):
            worker.submit(job); worker.abandon()
        worker._condition.wait = Mock(side_effect=abandon)
        scene, environment, driver = Mock(), Mock(), Mock()
        with patch.object(module, 'procedure', return_value=Mock()), \
                patch.object(module, 'RawBindings', return_value=driver), \
                patch.object(module, 'CaptureScene', return_value=scene), \
                patch.object(module, 'ToolheadEnvironment', return_value=environment):
            worker.run()
        self.assertIn(job, worker.quarantine)
        self.assertEqual(worker.completed(), ())
        self.assertFalse(worker.host_drained.is_set())
        driver.glFinish.assert_not_called()
        scene.close.assert_not_called(); environment.close.assert_not_called()
        context.doneCurrent.assert_called_once()
        context.moveToThread.assert_called_once()

    def test_failed_producer_finish_quarantines_active_source_without_fake_completion(self):
        context, driver, scene, environment = Mock(), Mock(), Mock(), Mock()
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        frame = object()
        job = module.CaptureJob(1, 'pose', frame, 91)
        worker.submit(job)
        driver.glFlush.side_effect = RuntimeError('submission fault')
        driver.glFinish.side_effect = RuntimeError('unverified producer finish')
        environment._storage = None
        environment.revision, environment.failure = 0, ''
        procedures = {'glFenceSync': Mock(return_value=101), 'glClientWaitSync': Mock(),
                      'glDeleteSync': Mock(), 'glWaitSync': Mock()}
        abort = Mock(wraps=worker.mailbox.abort)
        worker.mailbox.abort = abort
        with patch.object(module, 'procedure', side_effect=lambda _context, name, *_: procedures[name]), \
                patch.object(module, 'RawBindings', return_value=driver), \
                patch.object(module, 'CaptureScene', return_value=scene), \
                patch.object(module, 'ToolheadEnvironment', return_value=environment):
            worker.run()
        self.assertIn('producer finish failed', worker.failure)
        self.assertTrue(worker.abandoned.is_set())
        self.assertIn(job, worker.quarantine)
        self.assertIs(job.frame, frame)
        self.assertEqual(worker.completed(), ())
        self.assertFalse(worker.mailbox.drained)
        abort.assert_not_called()
        scene.close.assert_not_called(); environment.close.assert_not_called()
        context.doneCurrent.assert_called_once()

    def test_stale_unconsumed_source_is_not_kept_by_idle_worker_locals(self):
        class Frame: pass
        context = Mock()
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        source = Frame(); source_ref = weakref.ref(source)
        worker.submit(module.CaptureJob(1, 'pose', source, 91, worker.mailbox.generation))
        del source
        worker.mailbox.change_generation()
        def stop_idle(*_args):
            self.assertIsNone(source_ref(), 'idle worker retained an acknowledged stale frame')
            worker.stop()
        worker._condition.wait = Mock(side_effect=stop_idle)
        with patch.object(module, 'procedure', return_value=Mock()), \
                patch.object(module, 'RawBindings', return_value=Mock()), \
                patch.object(module, 'CaptureScene', return_value=Mock()), \
                patch.object(module, 'ToolheadEnvironment', return_value=Mock()):
            worker.run()
        self.assertEqual(worker.completed(), ((1, 91),))
        self.assertEqual(worker.failure, '')

    def test_consumer_retirement_fault_never_replays_deleted_producer_or_returns_source(self):
        context, driver, scene, environment = Mock(), Mock(), Mock(), Mock()
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        source = object()
        worker.submit(module.CaptureJob(1, 'pose', source, 91))
        environment._storage = NS(front=11, front_depth=12, select=Mock())
        environment.revision, environment.failure = 0, ''
        environment.step.side_effect = lambda *_: setattr(environment, 'revision', 1)
        deleted = []
        def poll(value, *_):
            if value.value == 999: raise RuntimeError('consumer fence transport fault')
            return 0x911A
        sequence = iter(range(100, 200))
        procedures = {'glFenceSync': lambda *_: next(sequence), 'glClientWaitSync': poll,
            'glDeleteSync': lambda value: deleted.append(value.value), 'glWaitSync': Mock()}
        complete = worker.mailbox.complete
        def seal_unadopted(token, payload, fence, **kwargs):
            accepted = complete(token, payload, fence, **kwargs)
            # Keep producer + consumer drains in the same unadopted pair. This
            # injects the retirement fault after a producer deletion succeeds.
            worker.mailbox._pair(token).consumer_fence = 999
            worker.stop()
            return accepted
        worker.mailbox.complete = seal_unadopted
        with patch.object(module, 'procedure', side_effect=lambda _context, name, *_: procedures[name]), \
                patch.object(module, 'RawBindings', return_value=driver), \
                patch.object(module, 'CaptureScene', return_value=scene), \
                patch.object(module, 'ToolheadEnvironment', return_value=environment):
            worker.run()
        self.assertEqual(deleted, [91, 100, 101])
        self.assertEqual(worker.completed(), ())
        self.assertTrue(worker.abandoned.is_set())
        self.assertFalse(worker.mailbox.drained)
        retirement = worker.quarantine[4]
        self.assertIs(retirement[1].frame, source)
        self.assertIsNone(retirement[2])
        self.assertEqual(retirement[3], 999)
        self.assertIn('retirement failed', worker.failure)
        scene.close.assert_not_called(); environment.close.assert_not_called()

    def test_replaced_pending_input_is_returned_without_consumption(self):
        worker = module.EnvironmentWorker(Mock(), object(), object(), {}, 0x81A6)
        first = module.CaptureJob(1, 'old', 'frame', 91)
        second = module.CaptureJob(2, 'new', 'frame', 92)
        self.assertIsNone(worker.submit(first))
        self.assertIs(worker.submit(second), first)
        worker.wake()
        worker.stop()
        self.assertIs(worker.submit(first), first)

    def test_idle_worker_and_temporarily_busy_pair_resume_without_losing_input(self):
        self.run_worker(idle=True)
        self.run_worker(reserved=True)

    def test_pending_input_and_independent_close_faults_cannot_skip_retirement(self):
        worker = self.run_worker(failure='injected draw failure', queued=True, close_fault=True)
        self.assertEqual(worker.failure, 'injected draw failure')


if __name__ == '__main__': unittest.main()
