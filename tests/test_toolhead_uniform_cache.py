"""Private delivery proofs retain mutable and transient host-uniform semantics."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from mpf.toolhead.ToolheadUniformCache import DeliveredUniformCache, delivered_key

class Matrix:
    def __init__(self): self.data = np.eye(4, dtype='f4')
    def getData(self): return self.data
class Vector:
    x, y, z = 1., 2., 3.
class Color:
    r, g, b, a = .1, .2, .3, 1.
class Host:
    def __init__(self):
        self._shader_program = object()
        self.calls = []; self.pending = {}; self.bound = False; self.fail = False
        self.texture_binds = self.texture_releases = 0
    def build(self): return 'linked'
    def _setUniformValueDirect(self, name, value):
        self.calls.append((name, delivered_key(value)))
        if self.fail: raise RuntimeError('delivery failed')
    def _setUniformValueArrayDirect(self, name, value): self.calls.append(('array', name))
    def setUniformValue(self, name, value, cache=True):
        if cache: self.pending[name] = value
        if self.bound: self._setUniformValueDirect(name, value)
    def bind(self):
        self.bound = True; self.texture_binds += 1
        for name, value in self.pending.items(): self._setUniformValueDirect(name, value)
    def release(self): self.bound = False; self.texture_releases += 1
class Cached(DeliveredUniformCache, Host): pass

class UniformDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict('sys.modules', {f'UM.Math.{c.__name__}': SimpleNamespace(**{c.__name__: c}) for c in (Matrix, Vector, Color)})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.shader = Cached()
    def test_rebind_skips_deliveries_but_preserves_texture_calls(self):
        s = self.shader
        for i, v in enumerate((True, 1, 1., [1., 2.], [1., 2., 3.], [1., 2., 3., 4.], Matrix(), Vector(), Color())):
            s.setUniformValue(i, v)
        s.bind(); s.release(); count = len(s.calls); s.bind(); s.release()
        self.assertEqual(len(s.calls), count)
        self.assertEqual((s.texture_binds, s.texture_releases), (2, 2))
    def test_transient_value_is_replaced_by_cached_value_on_bind(self):
        s = self.shader; s.setUniformValue(1, 1.); s.bind()
        s.setUniformValue(1, 2., cache=False); s.release(); s.bind()
        self.assertEqual([key[1] for _, key in s.calls], [delivered_key(v)[1] for v in (1., 2., 1.)])
    def test_mutable_host_values_are_snapshots(self):
        s = self.shader; values = [[1., 2.], Matrix(), Vector(), Color()]
        for i, v in enumerate(values): s.setUniformValue(i, v)
        s.bind(); s.release()
        values[0][0] = 3.; values[1].data[0, 0] = 3.; values[2].x = 3.; values[3].r = .7
        s.bind(); self.assertEqual(len(s.calls), 8)
    def test_scalar_overloads_signed_zero_and_nan_payload(self):
        s = self.shader
        for v in (True, 1, 1., 0., -0.): s._setUniformValueDirect(0, v)
        self.assertEqual(len(s.calls), 5)
        self.assertNotEqual(delivered_key(0.), delivered_key(-0.))
        self.assertEqual(delivered_key(float('nan')), delivered_key(float('nan')))
    def test_failed_upload_cannot_retain_previous_proof(self):
        s = self.shader; s._setUniformValueDirect(0, 1.); s.fail = True
        with self.assertRaises(RuntimeError): s._setUniformValueDirect(0, 2.)
        s.fail = False; s._setUniformValueDirect(0, 1.)
        self.assertEqual(len(s.calls), 3)
    def test_relink_and_program_replacement_invalidate(self):
        s = self.shader; s._setUniformValueDirect(0, 1.); self.assertEqual(s.build(), 'linked')
        s._setUniformValueDirect(0, 1.); s._shader_program = object(); s._setUniformValueDirect(0, 1.)
        self.assertEqual(len(s.calls), 3)
    def test_array_and_unknown_writes_invalidate_all_locations(self):
        s = self.shader
        for unknown in ([[1., 2.], [3., 4.]], [], object()):
            s._setUniformValueDirect(7, 1.)
            before = len(s.calls); s._setUniformValueDirect(6, unknown); s._setUniformValueDirect(7, 1.)
            self.assertEqual(len(s.calls), before + 2)
        before = len(s.calls); s._setUniformValueArrayDirect(6, [[1., 2., 3.]]); s._setUniformValueDirect(7, 1.)
        self.assertEqual(len(s.calls), before + 2)
