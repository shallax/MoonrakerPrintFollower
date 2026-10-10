"""Reflection pair cancellation, delayed fence polls and owner retirement."""
import threading
import unittest

from mpf.toolhead.ToolheadEnvironmentMailbox import EnvironmentMailbox, PairToken, PairUse


class EnvironmentMailboxTests(unittest.TestCase):
    def test_a_pending_old_snapshot_cannot_reserve_the_new_generation(self):
        box = EnvironmentMailbox()
        old = box.generation
        box.change_generation()
        self.assertIsNone(box.reserve(old))
        current = box.reserve(box.generation)
        self.assertIsNotNone(current)
        self.assertTrue(box.building_current(current))

    def publish(self, box, payload='colour-depth-descriptor'):
        token = box.reserve()
        self.assertTrue(box.complete(token, payload, 'producer'))
        use, fence = box.begin_poll()
        self.assertEqual(fence, 'producer')
        self.assertEqual(box.end_poll(use, True), (token, payload, 'producer'))
        return token

    def test_old_complete_front_remains_until_new_producer_is_ready(self):
        box = EnvironmentMailbox()
        first = self.publish(box, 'first')
        use, payload = box.begin_read()
        self.assertEqual(payload, 'first')
        box.end_read(use, 'old-last-use')
        box.change_generation()
        second = box.reserve()
        self.assertTrue(box.building_current(second))
        box.complete(second, 'second', 'new-producer')
        poll, _fence = box.begin_poll()
        self.assertIsNone(box.end_poll(poll, False))
        use, payload = box.begin_read()
        self.assertEqual(payload, 'first')
        self.assertEqual(box.end_read(use, 'new-last-use'), 'old-last-use')
        poll, fence = box.begin_poll()
        self.assertEqual(fence, 'new-producer')
        self.assertEqual(box.end_poll(poll, True), (second, 'second', 'new-producer'))
        self.assertIsNone(box.reserve(), 'no pair may be reused before consumer acknowledgement')
        self.assertEqual(box.take_retirement(), (first, 'first', None, 'new-last-use'))
        self.assertIsNone(box.reserve())
        box.retired(first)
        third = box.reserve()
        self.assertEqual(third.index, first.index)
        self.assertNotEqual(third.serial, first.serial)

    def test_disable_during_actual_consumer_needs_no_later_frame(self):
        box = EnvironmentMailbox()
        token = self.publish(box)
        use, payload = box.begin_read()
        self.assertEqual(payload, 'colour-depth-descriptor')
        self.assertIsNone(box.begin_read(), 'a concurrent read cannot replace last-use ownership')
        box.close(); box.close()
        self.assertIsNone(box.begin_read())
        self.assertIsNone(box.begin_poll())
        self.assertIsNone(box.reserve())
        self.assertIsNone(box.take_retirement())
        box.end_read(use, 'flushed-after-close')
        self.assertEqual(box.take_retirement(), (token, payload, None, 'flushed-after-close'))
        self.assertFalse(box.drained)
        box.retired(token)
        self.assertTrue(box.drained)

    def test_cancel_during_outside_lock_producer_poll_pins_fence(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        box.complete(token, 'ready', 'producer-being-polled')
        admitted, release = threading.Event(), threading.Event()
        results, failures = [], []
        def poller():
            try:
                use, fence = box.begin_poll()
                results.append(fence)
                admitted.set()
                if not release.wait(2): raise RuntimeError('Poll test release timed out')
                results.append(box.end_poll(use, True))
            except BaseException as error: failures.append(error)
        thread = threading.Thread(target=poller)
        thread.start()
        try:
            self.assertTrue(admitted.wait(2))
            box.change_generation()
            self.assertIsNone(box.take_retirement(), 'worker must not delete a fence still being polled')
            box.close()
            self.assertIsNone(box.take_retirement())
        finally:
            release.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(results, ['producer-being-polled', None])
        self.assertEqual(box.take_retirement(), (token, 'ready', 'producer-being-polled', None))
        box.retired(token)
        self.assertTrue(box.drained)

    def test_poll_lease_is_exclusive_and_failed_poll_has_finally_return(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        box.complete(token, 'ready', 'producer')
        use, _fence = box.begin_poll()
        self.assertIsNone(box.begin_poll())
        self.assertIsNone(box.end_poll(use, False))
        with self.assertRaisesRegex(RuntimeError, 'poll is not active'): box.end_poll(use, True)
        next_use, _fence = box.begin_poll()
        box.close()
        self.assertIsNone(box.take_retirement())
        self.assertIsNone(box.end_poll(next_use, False))
        self.assertIsNotNone(box.take_retirement())

    def test_cancelled_build_aborts_and_drains_without_gui_acknowledgement(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        self.assertTrue(box.building_current(token))
        box.close()
        self.assertFalse(box.building_current(token))
        self.assertIsNone(box.take_retirement())
        box.abort(token, 'last-submitted-worker-command')
        self.assertEqual(box.take_retirement(), (token, None, 'last-submitted-worker-command', None))
        box.retired(token)
        self.assertTrue(box.drained)

    def test_aborted_source_payload_stays_paired_until_verified_retirement(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        frame = object()
        with self.assertRaisesRegex(RuntimeError, 'unverified'):
            box.abort(token, None, payload=frame)
        box.close()
        box.abort(token, 'last-source-use', payload=frame)
        self.assertIsNone(box.begin_poll())
        self.assertIsNone(box.begin_read())
        self.assertEqual(box.take_retirement(), (token, frame, 'last-source-use', None))
        self.assertFalse(box.drained)
        self.assertIsNone(box.reserve())
        box.retired(token)
        self.assertTrue(box.drained)

    def test_obsolete_complete_uses_reserved_generation_and_cannot_get_stuck_ready(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        box.change_generation()
        self.assertFalse(box.complete(token, 'obsolete', 'producer'))
        self.assertIsNone(box.begin_poll())
        self.assertEqual(box.take_retirement(), (token, 'obsolete', 'producer', None))
        box.retired(token)
        replacement = box.reserve()
        self.assertTrue(box.building_current(replacement))

    def test_unadopted_ready_pair_closes_without_future_frame(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        box.complete(token, 'never-adopted', 'producer')
        box.close()
        self.assertEqual(box.take_retirement(), (token, 'never-adopted', 'producer', None))
        box.retired(token)
        generation = box.generation
        box.change_generation()
        self.assertEqual(box.generation, generation)
        self.assertTrue(box.drained)

    def test_replacement_owner_cannot_accept_old_token_even_when_indices_match(self):
        old, replacement = EnvironmentMailbox(), EnvironmentMailbox()
        token, new = old.reserve(), replacement.reserve()
        self.assertEqual((token.index, token.serial), (new.index, new.serial))
        for operation in (
                lambda: replacement.complete(token, 'wrong', 'fence'),
                lambda: replacement.abort(token, 'fence'),
                lambda: replacement.building_current(token),
                lambda: replacement.retired(token)):
            with self.assertRaisesRegex(RuntimeError, 'another owner'): operation()

    def test_expired_token_and_invalid_owner_never_touch_reused_pair(self):
        box = EnvironmentMailbox()
        token = self.publish(box)
        self.publish(box, 'second')
        box.take_retirement(); box.retired(token)
        replacement = box.reserve()
        for operation in (
                lambda: box.complete(token, 'wrong', 'fence'),
                lambda: box.abort(token, 'fence'),
                lambda: box.end_read(PairUse(token, object()), 'fence')):
            with self.assertRaisesRegex(RuntimeError, 'expired'): operation()
        for invalid in (None, PairToken(replacement.epoch, -1, replacement.serial),
                        PairToken(replacement.epoch, 2, replacement.serial)):
            with self.assertRaisesRegex(RuntimeError, 'another owner'): box.building_current(invalid)

    def test_consumer_fence_failure_keeps_pin_until_explicit_gpu_completion(self):
        box = EnvironmentMailbox()
        token = self.publish(box)
        use, payload = box.begin_read()
        box.close()
        with self.assertRaisesRegex(RuntimeError, 'completion is unverified'): box.end_read(use, None)
        self.assertIsNone(box.take_retirement())
        box.end_read(use, None, gpu_complete=True)
        self.assertEqual(box.take_retirement(), (token, payload, None, None))
        box.retired(token)
        self.assertTrue(box.drained)

    def test_producer_fence_failure_cannot_release_pending_gpu_work(self):
        for finishing in (False, True):
            with self.subTest(finishing=finishing):
                box = EnvironmentMailbox()
                token = box.reserve()
                box.close()
                action = (lambda box=box, token=token, **kw: box.complete(token, 'finished', None, **kw)) if finishing else (
                    lambda box=box, token=token, **kw: box.abort(token, None, **kw))
                with self.assertRaisesRegex(RuntimeError, 'completion is unverified'): action()
                self.assertIsNone(box.take_retirement())
                action(gpu_complete=True)
                self.assertIsNotNone(box.take_retirement())
                box.retired(token)
                self.assertTrue(box.drained)

    def test_adoption_during_old_front_draw_retains_both_until_old_draw_returns(self):
        box = EnvironmentMailbox()
        first = self.publish(box, 'first')
        use, _payload = box.begin_read()
        second = self.publish(box, 'second')
        self.assertIsNone(box.take_retirement())
        box.end_read(use, 'old-flushed-fence')
        self.assertEqual(box.take_retirement(), (first, 'first', None, 'old-flushed-fence'))
        new_use, payload = box.begin_read()
        self.assertEqual(payload, 'second')
        box.end_read(new_use, 'second-flushed-fence')
        box.retired(first)
        box.close()
        self.assertEqual(box.take_retirement(), (second, 'second', None, 'second-flushed-fence'))

    def test_invalid_or_repeated_ownership_operations_are_rejected(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        with self.assertRaisesRegex(RuntimeError, 'retirement ticket'): box.retired(token)
        box.complete(token, 'ready', 'producer')
        with self.assertRaisesRegex(RuntimeError, 'producer ticket'): box.complete(token, 'again', 'fence')
        with self.assertRaisesRegex(RuntimeError, 'producer ticket'): box.abort(token, 'fence')
        poll, _fence = box.begin_poll()
        with self.assertRaisesRegex(RuntimeError, 'poll is not active'): box.end_poll(PairUse(token, object()), True)
        box.end_poll(poll, True)
        use, _payload = box.begin_read()
        with self.assertRaisesRegex(RuntimeError, 'consumer ticket'): box.end_read(PairUse(token, object()), 'fence')
        box.end_read(use, 'consumer')
        with self.assertRaisesRegex(RuntimeError, 'consumer ticket'): box.end_read(use, 'duplicate')

    def test_null_nonce_cannot_adopt_or_mutate_an_unpinned_pair(self):
        box = EnvironmentMailbox()
        token = box.reserve()
        box.complete(token, 'ready', 'producer')
        for invalid in (PairUse(token, None), None):
            with self.assertRaisesRegex(RuntimeError, 'poll is not active'): box.end_poll(invalid, True)
        poll, fence = box.begin_poll()
        self.assertEqual(fence, 'producer')
        box.end_poll(poll, True)
        box.close()
        retirement = box.take_retirement()
        for invalid in (PairUse(token, None), None):
            with self.assertRaisesRegex(RuntimeError, 'consumer ticket'): box.end_read(invalid, 'forged')
        self.assertEqual(retirement, (token, 'ready', None, None))
        box.retired(token)
        with self.assertRaisesRegex(RuntimeError, 'consumer ticket'): box.end_read(PairUse(token, None), 'forged-after-free')


if __name__ == '__main__': unittest.main()
