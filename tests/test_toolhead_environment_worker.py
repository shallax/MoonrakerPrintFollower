"""Producer cancellation and fence retirement with an injected graphics driver."""
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

with patch.dict(sys.modules, {
    'mpf.toolhead.ToolheadCaptureGL': NS(RawBindings=object),
    'mpf.toolhead.ToolheadCaptureRecipe': NS(CaptureScene=object),
}):
    from mpf.toolhead import ToolheadEnvironmentWorker as module


class EnvironmentWorkerTests(unittest.TestCase):
    def run_worker(self, *, failure='', cancelled=False, no_fence=False, bad_poll=False, unavailable=False, stale=False,
                   idle=False, reserved=False, queued=False, close_fault=False):
        context = Mock()
        context.makeCurrent.return_value = not unavailable
        worker = module.EnvironmentWorker(context, object(), object(), {}, 0x81A6)
        job = module.CaptureJob(1, 'pose', 'immutable scene', 91, worker.mailbox.generation)
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
        sequence = iter(range(100, 200))
        def fence(*_):
            value = 0 if no_fence else next(sequence)
            submitted.append(value)
            return value
        statuses = iter([0x911D, 0x911A] if bad_poll else [0x911B, 0x911A])
        def poll(*_): return next(statuses, 0x911A)
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
            accepted = complete(token, result, producer, **kwargs)
            publications.append(result)
            use, _fence = worker.mailbox.begin_poll()
            _token, _payload, consumed = worker.mailbox.end_poll(use, True)
            if consumed: deleted.append(consumed)  # Main owns an adopted producer fence.
            read, _result = worker.mailbox.begin_read()
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
        expected = ((1, 91 if unavailable or stale else 0),)
        if queued: expected += ((2, 0),)
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
            if not stale: self.assertIn(91, deleted)
            self.assertEqual(len(deleted), len(set(deleted)))
            if not failure and not cancelled and not stale:
                self.assertEqual(publications[0], module.CaptureResult(11, 12, 'paired bounds', 'pose', 1))
                self.assertEqual(worker.timings[2], 4)
                self.assertIn(999, deleted)
                self.assertEqual(set(submitted) - {0}, set(deleted) - {91, 999})
            else: self.assertFalse(publications)
        return worker

    def test_complete_capture_drains_bounded_submissions_and_last_consumer(self):
        self.assertEqual(self.run_worker().failure, '')

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
