"""Complete receiver layers preserve identities, fallback and owned F32 values."""
import unittest
from unittest.mock import patch

import numpy as np

from mpf.toolhead.ToolheadEnvironmentLayers import LayerBuilder, METADATA_RESERVE, CHUNK_METADATA


class EnvironmentLayerTests(unittest.TestCase):
    def test_four_planes_keep_independent_ids_radiance_and_terminal_proof(self):
        owner=LayerBuilder(('samples',4),1,1,existing_bytes=0,byte_budget=100000,
            texel_limit=32,samples=4,sample_positions=((.375,.125),(.875,.375),(.125,.625),(.625,.875)))
        depth=np.zeros((4,1,1,2),np.float32)
        depth[...,0]=np.array((-2.5,.25,1.5,np.finfo(np.float32).max),np.float32).reshape(4,1,1)
        identity=np.arange(4,dtype=np.float32).reshape(4,1,1)
        records=np.zeros((4,1,1,4),np.float32)
        records[...,0]=depth[...,0]; records[...,1]=identity; records[...,3]=1.
        colours=np.array(((0.,0.,0.,1.),(.1,.2,.3,1.),(0.,0.,0.,0.),(.8,.7,.6,1.)),np.float32).reshape(4,1,1,4)
        expected=colours.reshape(4,4).copy()
        self.assertFalse(owner.accept(owner.key,depth,identity,records,colours))
        depth[...,0]=np.finfo(np.float32).max; depth[...,1]=1.; identity.fill(16777216.); records.fill(0.); colours.fill(0.)
        # One nonempty sample prevents whole completion, even with three empty.
        depth[3]=(np.finfo(np.float32).max,0.); identity[3]=8
        records[3]=(np.finfo(np.float32).max,8,0,1); colours[3]=(.4,.3,.2,1.)
        self.assertFalse(owner.accept(owner.key,depth,identity,records,colours))
        depth[...,0]=np.finfo(np.float32).max; depth[...,1]=1.; identity.fill(16777216.); records.fill(0.); colours.fill(0.)
        self.assertTrue(owner.accept(owner.key,depth,identity,records,colours))
        image=owner.finish(owner.key)
        self.assertEqual(image.samples,4)
        np.testing.assert_array_equal(image.ranges,((0,1),(1,1),(2,1),(3,2)))
        np.testing.assert_array_equal(image.identities,(0,1,2,3,8))
        np.testing.assert_array_equal(image.colours[:4],expected)
        self.assertEqual(image.retained_bytes,4*8+5*20)
        for samples in (2,True):
            with self.assertRaises(ValueError): self.builder(samples=samples)

    def builder(self, **options):
        values = dict(existing_bytes=100, byte_budget=100000, texel_limit=64)
        values.update(options)
        return LayerBuilder(('camera', 'pose', 4), 3, 1, **values)

    def layer(self, selected, radiance=None):
        depth = np.full((1, 3), 2., np.float32)
        ids = np.full((1, 3), 16777216., np.float32)
        records = np.zeros((1, 3, 4), np.float32)
        colours = np.zeros((1, 3, 4), np.float32)
        for pixel, z, identity in selected:
            depth[0, pixel] = z; ids[0, pixel] = identity
            records[0, pixel] = z, identity, 0., 1.
            colours[0, pixel] = (0., 0., 0., 1.) if radiance is None else radiance[pixel]
        return depth, ids, records, colours

    def accept(self, owner, layer): return owner.accept(owner.key, *layer)

    def test_dynamic_depth_order_becomes_per_pixel_id_lookup_without_changing_float_radiance(self):
        owner = self.builder()
        first = self.layer(((0, .2, 8), (1, .4, 3), (2, .1, 7)),
            {0: (2.234567, .1234567, .999999, 1.), 1: (0., 0., 0., 0.), 2: (0., 0., 0., 1.)})
        first_colour = first[3].copy()
        self.assertFalse(self.accept(owner, first))
        # A legitimate map fallback does not end this pixel's enumeration.
        second = self.layer(((0, .3, 2), (1, .5, 1)),
            {0: (.1, .7, .2, 1.), 1: (.9, .4, .8, 1.)})
        second_colour = second[3].copy()
        self.assertFalse(self.accept(owner, second))
        for array in (*first, *second): array.fill(-99.)
        self.assertTrue(self.accept(owner, self.layer(())))
        image = owner.finish(owner.key)
        np.testing.assert_array_equal(image.ranges, ((0, 2), (2, 2), (4, 1)))
        np.testing.assert_array_equal(image.identities, (2, 8, 1, 3, 7))
        np.testing.assert_array_equal(image.colours, np.stack((second_colour[0, 0], first_colour[0, 0],
            second_colour[0, 1], first_colour[0, 1], first_colour[0, 2])))
        for array in (image.ranges, image.identities, image.colours):
            self.assertTrue(array.flags.owndata); self.assertFalse(array.flags.writeable)
        self.assertEqual(image.retained_bytes, 3*8+5*20)
        self.assertEqual(image.gpu_bytes, image.retained_bytes)
        self.assertGreaterEqual(image.peak_bytes, owner.existing_bytes+image.retained_bytes+image.gpu_bytes)

    def test_empty_image_requires_explicit_terminal_selector_layer(self):
        owner = self.builder()
        with self.assertRaisesRegex(ValueError, 'incomplete'): owner.finish(owner.key)
        self.assertTrue(owner.refused)
        owner = self.builder(); self.assertTrue(self.accept(owner, self.layer(())))
        image = owner.finish(owner.key)
        self.assertEqual(image.colours.shape, (0, 4)); self.assertEqual(image.identities.shape, (0,))
        np.testing.assert_array_equal(image.ranges, np.zeros((3, 2), np.uint32))
        with self.assertRaisesRegex(ValueError, 'unavailable'): owner.finish(owner.key)

    def test_selected_valid_missing_record_or_mixed_empty_never_becomes_completion(self):
        changes = (
            lambda d, i, r, c: r.fill(0.),
            lambda d, i, r, c: i.__setitem__((0, 0), .5),
            lambda d, i, r, c: d.__setitem__((0, 0), 2.),
            lambda d, i, r, c: r.__setitem__((0, 0, 1), 4.),
            lambda d, i, r, c: r.__setitem__((0, 0, 2), 1.),
            lambda d, i, r, c: r.__setitem__((0, 0, 3), -1.),
            lambda d, i, r, c: c.__setitem__((0, 0, 3), .5),
            lambda d, i, r, c: c.__setitem__((0, 0), (1., 0., 0., 0.)),
            lambda d, i, r, c: c.__setitem__((0, 2), (0., 0., 0., 1.)),
            lambda d, i, r, c: d.__setitem__((0, 0), float('nan')),
        )
        for change in changes:
            with self.subTest(change=change):
                owner = self.builder(); layer = self.layer(((0, .2, 3),))
                change(*layer)
                with self.assertRaises(ValueError): self.accept(owner, layer)
                self.assertTrue(owner.refused); self.assertIsNone(owner.image)
                with self.assertRaises(ValueError): owner.finish(owner.key)

    def test_stalled_cursor_resurrection_and_repeated_identity_reject_whole_image(self):
        for second in (((0, .2, 3),), ((0, .1, 4),), ((1, .5, 2),)):
            owner = self.builder(); self.accept(owner, self.layer(((0, .2, 3),)))
            with self.assertRaises(ValueError): self.accept(owner, self.layer(second))
        owner = self.builder(); self.accept(owner, self.layer(((0, .2, 3),)))
        self.accept(owner, self.layer(((0, .3, 3),)))
        self.assertTrue(self.accept(owner, self.layer(())))
        with self.assertRaisesRegex(ValueError, 'repeated'): owner.finish(owner.key)
        self.assertTrue(owner.refused); self.assertIsNone(owner.image)

    def test_budget_capacity_layout_and_key_refuse_before_gathering_or_casting(self):
        with patch('mpf.toolhead.ToolheadEnvironmentLayers.np.full') as allocate:
            with self.assertRaises(MemoryError): self.builder(byte_budget=100)
            allocate.assert_not_called()
        for options in (dict(texel_limit=2), dict(texel_limit=True), dict(existing_bytes=-1)):
            with self.assertRaises((ValueError, MemoryError)): self.builder(**options)
        owner = self.builder(byte_budget=100+METADATA_RESERVE+3*(9+40+128)-1)
        with patch('mpf.toolhead.ToolheadEnvironmentLayers.np.flatnonzero') as allocate:
            with self.assertRaises(MemoryError): self.accept(owner, self.layer(((0, .2, 3),)))
            allocate.assert_not_called()
        owner = self.builder(texel_limit=3)
        self.accept(owner, self.layer(((0, .2, 3), (1, .2, 4), (2, .2, 5))))
        with self.assertRaises(MemoryError): self.accept(owner, self.layer(((0, .3, 6),)))
        owner = self.builder()
        with self.assertRaises(ValueError): owner.accept(('other',), *self.layer(()))
        owner = self.builder(); bad = list(self.layer(())); bad[0] = bad[0].astype(np.float64)
        with self.assertRaises(ValueError): self.accept(owner, bad)
        owner = self.builder(cancel=lambda: False); owner.cancel = lambda: True
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'): self.accept(owner, self.layer(()))

    def test_sparse_layer_headers_and_full_sliced_backing_are_charged(self):
        owner = self.builder(byte_budget=METADATA_RESERVE+3000)
        for layer in range(2): self.accept(owner, self.layer(((0, .1+layer*.1, layer),)))
        with self.assertRaises(MemoryError): self.accept(owner, self.layer(((0, .4, 3),)))
        self.assertIsNone(owner.image)
        self.assertGreaterEqual(owner.peak_bytes, METADATA_RESERVE+2*CHUNK_METADATA)
        owner = self.builder()
        backing = bytearray(1000000)
        depth = np.frombuffer(memoryview(backing)[128:140], np.float32).reshape(1, 3)
        depth.fill(2.)
        layer = list(self.layer(())); layer[0] = depth
        with self.assertRaises(MemoryError): self.accept(owner, layer)
        self.assertIsNone(owner.image)

    def test_cancellation_during_final_count_scan_cannot_publish(self):
        owner = self.builder(cancel=lambda: False)
        self.accept(owner, self.layer(((0, .2, 3),)))
        self.accept(owner, self.layer(()))
        original = np.cumsum
        def cancel_during_scan(*args, **kwargs):
            result = original(*args, **kwargs); owner.cancel = lambda: True; return result
        with patch('mpf.toolhead.ToolheadEnvironmentLayers.np.cumsum', side_effect=cancel_during_scan):
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'): owner.finish(owner.key)
        self.assertIsNone(owner.image); self.assertTrue(owner.refused)


if __name__ == '__main__': unittest.main()
