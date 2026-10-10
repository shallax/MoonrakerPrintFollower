"""Complete light sets, exact source selection and fence-pinned retirement."""
from dataclasses import replace
import unittest

from mpf.toolhead.ToolheadShadowExchange import ShadowExchange, ShadowSet
from mpf.toolhead.ToolheadShadowValues import ShadowLight, ShadowMapPlan, ShadowProjection


class ShadowExchangeTests(unittest.TestCase):
    def result(self, key, count=2):
        lights = tuple(ShadowLight('path' if index == 0 else 'platform', 0,
            (index, 2, 3), None, (1, 1, 1)) for index in range(count))
        plan = ShadowMapPlan(lights)
        return ShadowSet(key, plan, tuple(((light.kind, light.index), 10+index,
            ShadowProjection(light.position, .2, 500)) for index, light in enumerate(lights)))

    def publish(self, exchange, key):
        generation = exchange.select(key)
        write = exchange.reserve(key, generation)
        self.assertTrue(exchange.building_current(write))
        self.assertIsNone(exchange.reserve(key, generation))
        result = self.result(key)
        exchange.complete(write, result, 'producer')
        poll, fence = exchange.begin_poll()
        self.assertEqual(fence, 'producer')
        self.assertIsNone(exchange.end_poll(poll, False))
        poll, _ = exchange.begin_poll()
        self.assertEqual(exchange.end_poll(poll, True), (write.token, result, 'producer'))
        return write, result

    def test_source_change_cannot_use_retained_shadows_and_a_b_a_restores_exact_front(self):
        exchange = ShadowExchange()
        write, first = self.publish(exchange, ('A',))
        self.assertIsNone(exchange.reserve(('A',), exchange.select(('A',))))
        use, delivered = exchange.begin_read(('A',))
        self.assertIs(delivered, first)
        exchange.end_read(use, 'consumer')
        generation = exchange.select(('B',))
        self.assertIsNone(exchange.begin_read(('A',)))
        self.assertIsNone(exchange.begin_read(('B',)))
        pending = exchange.reserve(('B',), generation)
        self.assertIsNone(exchange.reserve(('A',), generation))
        self.assertIsNone(exchange.reserve(('B',), generation-1))
        exchange.select(('A',))
        self.assertFalse(exchange.building_current(pending))
        exchange.abort(pending, 'cancelled-producer')
        use, restored = exchange.begin_read(('A',))
        self.assertIs(restored, first)
        exchange.end_read(use, None, gpu_complete=True)
        retirement = exchange.take_retirement()
        self.assertEqual(retirement[0], pending.token)
        exchange.retired(pending.token)
        exchange.close()
        self.assertEqual(exchange.take_retirement()[0], write.token)
        exchange.retired(write.token)
        self.assertTrue(exchange.drained)

    def test_close_with_live_consumer_pins_all_maps_until_last_use_returns(self):
        exchange = ShadowExchange()
        write, result = self.publish(exchange, ('scene',))
        use, _ = exchange.begin_read(('scene',))
        exchange.close()
        self.assertFalse(exchange.drained)
        self.assertIsNone(exchange.take_retirement())
        self.assertIsNone(exchange.select(('replacement',)))
        self.assertIsNone(exchange.reserve(('scene',), write.generation))
        self.assertIsNone(exchange.begin_read(('scene',)))
        exchange.end_read(use, 'last-use')
        self.assertEqual(exchange.take_retirement(), (write.token, result, None, 'last-use'))
        exchange.retired(write.token)
        self.assertTrue(exchange.drained)

    def test_obsolete_completion_and_wrong_owner_cannot_publish_new_generation(self):
        exchange, replacement = ShadowExchange(), ShadowExchange()
        generation = exchange.select(('old',))
        write = exchange.reserve(('old',), generation)
        with self.assertRaisesRegex(RuntimeError, 'ticket is not active'):
            replacement.building_current(write)
        with self.assertRaisesRegex(ValueError, 'another source'):
            exchange.complete(write, self.result(('wrong',)), 'producer')
        with self.assertRaisesRegex(ValueError, 'another source'):
            exchange.complete(write, None, 'producer')
        exchange.select(('new',))
        self.assertFalse(exchange.complete(write, self.result(('old',)), 'producer'))
        self.assertIsNone(exchange.begin_poll())
        exchange.retired(exchange.take_retirement()[0])
        with self.assertRaisesRegex(RuntimeError, 'ticket is not active'):
            exchange.abort(write, None, gpu_complete=True)
        fresh = exchange.reserve(('new',), exchange.select(('new',)))
        exchange.abort(fresh, None, gpu_complete=True)
        exchange.retired(exchange.take_retirement()[0])
        exchange.close(); self.assertTrue(exchange.drained)

    def test_complete_set_rejects_missing_aliasing_mismatched_or_mutable_inputs(self):
        result = self.result(('scene',))
        for changes in (dict(key=None), dict(key=['mutable']), dict(plan=None),
                        dict(plan=ShadowMapPlan(())), dict(maps=result.maps[:1]),
                        dict(maps=(result.maps[0], result.maps[0])),
                        dict(maps=((('wrong', 0), 1, result.maps[0][2]), result.maps[1])),
                        dict(maps=((('path', 0), 0, result.maps[0][2]), result.maps[1])),
                        dict(maps=((('path', 0), 10, ShadowProjection((9, 9, 9), .2, 500)), result.maps[1])),
                        dict(maps=(result.maps[0], (result.maps[1][0], 10, result.maps[1][2])))):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(result, **changes)
        identity = ['path', 0]
        frozen = replace(result, maps=((identity, 10, result.maps[0][2]), result.maps[1]))
        identity[0] = 'mutated'
        self.assertEqual(frozen.maps[0][0], ('path', 0))
        with self.assertRaises(ValueError): ShadowExchange().select(None)


if __name__ == '__main__': unittest.main()
