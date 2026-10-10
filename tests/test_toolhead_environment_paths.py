"""Complete original path containment and bounded preparation, no graphics."""
import math
import tracemalloc
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from mpf.toolhead import ToolheadEnvironmentPaths as paths
from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, BufferLease


def source(count):
    positions = np.zeros((count*2, 3), np.float32)
    positions[:, 0] = np.arange(count*2, dtype=np.float32)
    positions[:, 1] = .1
    dimensions = np.tile(np.array((.4, .05), np.float32), (count*2, 1))
    indices = np.arange(count*2, dtype=np.uint32).reshape(-1, 2)
    return positions, dimensions, indices, np.eye(4, dtype=np.float32)


def original_corners(a, b, ad, bd, model):
    # Original native endpoint half-height and FLOAT32 affine decoder. Expand
    # actual GS corner sets, not the implementation's envelope formula.
    raw_points = [(model @ np.append(p, np.float32(1)))[:3] for p in (a, b)]
    points = []
    for p, d in ((a, ad), (b, bd)):
        p = p.copy()
        p[1] -= d[1]*np.float32(.5)
        points.append((model @ np.append(p, np.float32(1)))[:3])
    a, b = points
    delta = b-a
    if delta[1] == 0:
        radial = np.array((delta[2], 0, -delta[0]), np.float32)
    elif delta[0] == delta[2] == 0:
        radial = np.array((1, 0, -1), np.float32)
    else:
        radial = np.cross(delta, np.array((delta[0], 0, delta[2]), np.float32))
    axis = delta/np.linalg.norm(delta)
    radial /= np.linalg.norm(radial)
    corners = []
    sy = bd[1]*np.float32(.5)+np.float32(.01)
    up = np.array((0, sy, 0), np.float32)
    for sx in (bd[0]*np.float32(.5)+np.float32(.01), np.float32(.05)):
        h, r = axis*sx, radial*sx
        for endpoint in (a, b):
            corners.extend((endpoint-h, endpoint+h, endpoint-r, endpoint+r, endpoint-up, endpoint+up))
        corners.extend((a-h+up, a-r+up, a+r+up, b-r+up, b+r+up, b+h+up))
    marker = np.array((max(.05, bd[0]*.5+.01), max(.05, sy), max(.05, bd[0]*.5+.01)), np.float32)
    for x in (-1, 1):
        for y in (-1, 1):
            for z in (-1, 1):
                corners.append(a+np.array((x, y, z), np.float32)*marker)
    # Raw endpoints must also be discoverable for global moved aliases.
    for p in raw_points:
        corners.append(p)
    return np.asarray(corners)


def publication(count=3):
    positions, dimensions, indices, _model = source(count)
    scalars = np.ones(len(positions), np.float32)
    colours = np.tile(np.array((.2, .4, 2., .9), np.float32), (len(positions), 1))
    attrs = (('dims', 'a_line_dim', 'vector2f', dimensions),
             ('type', 'a_line_type', 'float', scalars.copy()),
             ('previous', 'a_prev_line_type', 'float', scalars.copy()),
             ('extruder', 'a_extruder', 'float', np.zeros_like(scalars)),
             ('feedrate', 'a_feedrate', 'float', scalars.copy()),
             ('material', 'a_material_color', 'vector4f', colours.copy()))
    mesh = CaptureMesh(31, positions, indices.view(np.int32), None, colours, None, attrs)
    layout, size = mesh.layout()
    return mesh, BufferLease(52, size, layout)


class EnvironmentPathPublicationTests(unittest.TestCase):
    def admit(self, count=3, **options):
        return paths.admit_path_inputs(*publication(count), ('slice', 19), **options)

    def test_exact_planar_offsets_all_fields_and_unsigned_original_index_borrows(self):
        mesh, lease = publication()
        result = paths.admit_path_inputs(mesh, lease, ('slice', 19))
        self.assertIs(result.mesh, mesh)
        self.assertIs(result.vertex, lease)
        self.assertTrue(np.shares_memory(result.indices, mesh.indices))
        self.assertTrue(mesh.indices.flags.writeable)
        self.assertFalse(result.indices.flags.writeable)
        self.assertEqual(result.scalar_count*4, lease.size)
        self.assertEqual([(name, offset*4) for name, offset, _width in result.fields],
                         [(name, offset) for name, _kind, offset in lease.layout])
        for _name, array in result.arrays:
            self.assertTrue(array.flags.writeable)  # The admission never edits host flags.
            self.assertEqual(array.dtype, np.float32)
        self.assertEqual(dict(result.arrays)['a_color'][0, 2], 2)  # Preserve HDR, not clamped paint.
        changed = paths.admit_path_inputs(mesh, lease, ('slice', 20))
        self.assertNotEqual(changed.certificate, result.certificate)
        new_colour = replace(mesh, colours=mesh.colours.copy())
        self.assertNotEqual(paths.admit_path_inputs(new_colour, lease, ('slice', 19)).certificate,
                            result.certificate)
        new_lease = replace(lease, name=53)
        self.assertNotEqual(paths.admit_path_inputs(mesh, new_lease, ('slice', 19)).certificate,
                            result.certificate)

    def test_invalid_layouts_fail_before_existing_size_only_lease_validation(self):
        mesh, lease = publication()
        invalid = (replace(mesh, colours=mesh.colours.astype(np.float64)),
                   replace(mesh, colours=np.ones((len(mesh.vertices), 4), dtype=object)),
                   replace(mesh, vertices=mesh.vertices[:, ::-1]),
                   replace(mesh, vertices=np.lib.stride_tricks.as_strided(mesh.vertices,
                           shape=(len(mesh.vertices), 3), strides=mesh.vertices.strides)),
                   replace(mesh, attributes=mesh.attributes+(('other', 'a_line_type', 'float',
                           np.ones(len(mesh.vertices), np.float32)),)),
                   replace(mesh, attributes=mesh.attributes[1:]),
                   replace(mesh, attributes=((object(), 'a_unused', 'float',
                           np.zeros(len(mesh.vertices), np.float32)),)+mesh.attributes),
                   replace(mesh, attributes=mesh.attributes*11),
                   replace(mesh, attributes=(('bad', 'a_line_dim', 'int', mesh.attributes[0][3]),)+mesh.attributes[1:]))
        with patch.object(BufferLease, 'validate', side_effect=AssertionError('Size-only check before readable validation')):
            for item in invalid:
                with self.subTest(item=item.identity), self.assertRaises(ValueError):
                    paths.admit_path_inputs(item, lease, 1)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            paths.admit_path_inputs(mesh, replace(lease, size=lease.size-4), 1)
        for item, buffer in ((object(), lease), (mesh, object()), (replace(mesh, identity=0), lease),
                             (replace(mesh, vertices=None), lease), (mesh, replace(lease, name=True))):
            with self.assertRaises(ValueError):
                paths.admit_path_inputs(item, buffer, 1)

    def test_complete_validation_includes_unreferenced_vertex_fields_and_indices(self):
        mesh, lease = publication()
        # Only line0 referenced, yet bad data in the last unpublished vertex is refused.
        mesh = replace(mesh, indices=mesh.indices[:1])
        for name, bad in (('a_feedrate', math.nan), ('a_line_dim', -1), ('a_line_type', 14),
                          ('a_prev_line_type', .5), ('a_extruder', 16)):
            attrs = []
            for key, gl_name, kind, array in mesh.attributes:
                array = array.copy()
                if gl_name == name:
                    array[-1] = bad
                attrs.append((key, gl_name, kind, array))
            with self.subTest(name=name), self.assertRaises(ValueError):
                paths.admit_path_inputs(replace(mesh, attributes=tuple(attrs)), lease, 1)
        for indices in (np.array(((0, -1),), np.int32), np.array(((0, 99),), np.uint32),
                        np.array((0, 1, 2), np.uint32), np.array(((0, 1),), np.int64), None):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                paths.admit_path_inputs(replace(mesh, indices=indices), lease, 1)
        with self.assertRaises(ValueError):
            paths.admit_path_inputs(mesh, lease, [])

    def test_global_partial_aliases_use_either_local_endpoint_in_exact_nonhistory_prefix(self):
        mesh, lease = publication(7)
        next_point = np.array((50, .1, 0), np.float32)
        # A-only, B-only, both-endpoints, hidden categories and outside prefix/history.
        mesh.vertices[[0, 2, 5, 6, 7, 8, 13]] = next_point
        mesh.attributes[1][3][8:10] = 6  # Infill is still retained in alias discovery.
        inputs = paths.admit_path_inputs(mesh, lease, 1)
        last = np.array((-50, .1, 0), np.float32)
        prefix = paths.freeze_path_prefix(inputs, 2, 4, 12, (last, next_point, .25))
        self.assertEqual((prefix.first, prefix.history, prefix.completed), (1, 2, 6))
        np.testing.assert_array_equal(prefix.aliases, (2, 3, 4))
        self.assertFalse(prefix.aliases.flags.writeable)
        next_point[:] = 900
        self.assertEqual(prefix.partial[1], (50., float(np.float32(.1)), 0.))
        # A first element above history is legal; no invented first<=history rule.
        late = paths.freeze_path_prefix(inputs, 6, 2, 12, (last, (50, .1, 0), .25))
        np.testing.assert_array_equal(late.aliases, (3, 4))
        self.assertNotEqual(prefix.certificate, late.certificate)
        none = paths.freeze_path_prefix(inputs, 0, 0, 14)
        self.assertIsNone(none.partial)
        self.assertEqual(none.aliases.size, 0)

    def test_boundary_partial_and_alias_overflow_refuse_whole_snapshot(self):
        inputs = self.admit(40)
        for bounds in ((1, 0, 2), (0, 1, 2), (0, 0, 1), (4, 0, 2), (0, 4, 2),
                       (0, 0, 82), (False, 0, 2), (-2, 0, 2)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                paths.freeze_path_prefix(inputs, *bounds)
        for partial in ((), (0, 0, 0), ((0, 0, 0), (1, 2), .5),
                        ((0, 0, 0), (1, 2, math.nan), .5), ((0, 0, 0), (1, 2, 3), 1.1),
                        ((0, 0, 0), (1, 2, 3), [0, 1])):
            with self.subTest(partial=partial), self.assertRaises((ValueError, TypeError)):
                paths.freeze_path_prefix(inputs, 0, 0, 80, partial)
        positions = dict(inputs.arrays)['a_vertex']
        positions[::2] = (50, .1, 0)
        # Test data mutation occurs before discovery; a real owner must publish
        # a new generation for such a change. The certificate is not a hash.
        with self.assertRaisesRegex(ValueError, 'aliases exceed'):
            paths.freeze_path_prefix(inputs, 0, 0, 80, ((-50, .1, 0), (50, .1, 0), .5))
        allowed = paths.freeze_path_prefix(inputs, 0, 0, 64, ((-50, .1, 0), (50, .1, 0), .5))
        np.testing.assert_array_equal(allowed.aliases, np.arange(32, dtype=np.uint32))

    def test_invalid_large_partial_shapes_refuse_before_numeric_conversion(self):
        inputs = self.admit()
        giant = np.broadcast_to(np.float64(1), (10_000_000,))
        with patch.object(paths.np, 'asarray', side_effect=AssertionError('Conversion before state preflight')):
            for partial in ((giant, (1, 2, 3), .5), ((0, 0, 0), giant, .5),
                            ((0, 0, 0), (1, 2, 3), giant),
                            (([1, 2, 3], 0, 0), (1, 2, 3), .5)):
                with self.assertRaises(ValueError):
                    paths.freeze_path_prefix(inputs, 0, 0, 6, partial)
        invalid = np.lib.stride_tricks.as_strided(np.ones(1, np.float64), shape=(3,), strides=(8,))
        with patch.object(paths.np, 'asarray', side_effect=AssertionError('Read before allocation certificate')):
            with self.assertRaisesRegex(ValueError, 'allocation'):
                paths.freeze_path_prefix(inputs, 0, 0, 6, (invalid, (1, 2, 3), .5))
        # A real retained FLOAT64 endpoint still freezes only three F32 values.
        valid = np.array((1, 2, 3), np.float64)
        self.assertEqual(paths.freeze_path_prefix(inputs, 0, 0, 6, (valid, valid, .5)).partial[0], (1., 2., 3.))

    def test_cancellation_is_atomic_in_channel_validation_and_alias_scanning(self):
        mesh, lease = publication(8193)
        visits = 0
        def count():
            nonlocal visits
            visits += 1
            return False
        inputs = paths.admit_path_inputs(mesh, lease, 1, cancel=count)
        for stop in range(1, visits+1):
            at = 0
            def cancelled(stop=stop):
                nonlocal at
                at += 1
                return at == stop
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
                paths.admit_path_inputs(mesh, lease, 1, cancel=cancelled)
        visits = 0
        paths.freeze_path_prefix(inputs, 0, 0, inputs.indices.size, ((0, 0, 0), (-1, -1, -1), .5), cancel=count)
        for stop in range(1, visits+1):
            at = 0
            def cancelled(stop=stop):
                nonlocal at
                at += 1
                return at == stop
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
                paths.freeze_path_prefix(inputs, 0, 0, inputs.indices.size,
                                         ((0, 0, 0), (-1, -1, -1), .5), cancel=cancelled)


class EnvironmentPathLightCertificateTests(unittest.TestCase):
    def fixture(self, count=4, partial=None, history=4):
        mesh, lease = publication(count)
        inputs = paths.admit_path_inputs(mesh, lease, ('light', 1))
        prefix = paths.freeze_path_prefix(inputs, 0, history, count*2, partial)
        return inputs, prefix

    def test_complete_history_and_current_keys_include_top_and_starts(self):
        inputs, prefix = self.fixture()
        a = paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=True)
        self.assertEqual(a, (prefix.certificate, 4, True, True))
        for top, enabled, starts in ((0, True, True), (4, False, True), (4, True, False)):
            self.assertNotEqual(a, paths.certify_path_light(inputs, prefix, top, enabled=enabled, show_starts=starts))
        # No lower-history marker means the base hit is sufficient; current
        # starts share the base GS and do not require refusal.
        dict(inputs.arrays)['a_prev_line_type'][4] = 0
        self.assertEqual(paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=True), a)

    def test_all_historical_endpoints_refuse_before_any_winning_line_selection(self):
        for endpoint in (0, 3):
            inputs, prefix = self.fixture(partial=((-5, .1, 0), (99, .1, 0), .5))
            dict(inputs.arrays)['a_vertex'][endpoint] = (99, .1, 0)
            # Source mutation here is synthetic fixture setup. Runtime sources
            # are frozen, and their owner retains the corresponding generation.
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, 'Historical partial'):
                paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=False)
            paths.certify_path_light(inputs, prefix, 4, enabled=False, show_starts=False)
        inputs, prefix = self.fixture(partial=((-5, .1, 0), (99, .1, 0), 1))
        dict(inputs.arrays)['a_vertex'][0] = (99, .1, 0)
        paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=False)

    def test_historical_starts_use_first_endpoint_and_independent_top_exception(self):
        inputs, prefix = self.fixture()
        arrays = dict(inputs.arrays)
        arrays['a_prev_line_type'][0] = 0
        arrays['a_line_type'][1] = 6  # END palette must not hide FIRST kind1.
        with self.assertRaisesRegex(ValueError, 'lighting starts'):
            paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=True)
        paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=False)
        arrays['a_line_type'][0] = 4
        # Older helper markers are omitted by the light's top/category guard.
        paths.certify_path_light(inputs, prefix, 4, enabled=True, show_starts=True)
        with self.assertRaisesRegex(ValueError, 'lighting starts'):
            paths.certify_path_light(inputs, prefix, 0, enabled=True, show_starts=True)
        arrays['a_prev_line_type'][0] = 4
        paths.certify_path_light(inputs, prefix, 0, enabled=True, show_starts=True)

    def test_first_above_history_has_no_historical_geometry(self):
        inputs, _ = self.fixture()
        prefix = paths.freeze_path_prefix(inputs, 6, 4, 8)
        dict(inputs.arrays)['a_prev_line_type'][0] = 0
        paths.certify_path_light(inputs, prefix, 6, enabled=True, show_starts=True)

    def test_invalid_certificates_bounds_and_flags_refuse(self):
        inputs, prefix = self.fixture()
        for top in (-2, 1, 10, False):
            with self.subTest(top=top), self.assertRaises(ValueError):
                paths.certify_path_light(inputs, prefix, top, enabled=True, show_starts=True)
        for wrong in (object(), replace(prefix, first=-1), replace(prefix, history=9),
                      replace(prefix, certificate=()), replace(prefix, completed=True)):
            with self.assertRaises(ValueError):
                paths.certify_path_light(inputs, wrong, 4, enabled=True, show_starts=True)
        for enabled, starts in ((1, True), (True, 0)):
            with self.assertRaises(ValueError):
                paths.certify_path_light(inputs, prefix, 4, enabled=enabled, show_starts=starts)
        with self.assertRaises(ValueError):
            paths.certify_path_light(object(), prefix, 4, enabled=True, show_starts=True)

    def test_forged_partial_certificate_cannot_allocate_or_read_large_endpoints(self):
        inputs, prefix = self.fixture()
        huge = np.broadcast_to(np.float64(1), (10_000_000,))
        overrun = np.lib.stride_tricks.as_strided(np.ones(1, np.float32), shape=(3,), strides=(4,))
        partials = (((0., 0., 0.), (0., 0., 0.), math.nan),
                    ((0., 0., 0.), (0., 0., 0.), 2.),
                    ((0., 0., 0.), huge, .5), ((0., 0., 0.), overrun, .5),
                    ((0., 0., 0.), (1.e100, 0., 0.), .5),
                    ([0., 0., 0.], (0., 0., 0.), .5),
                    ((0., 0., 0.), (0., 0., 0.), False))
        with patch.object(paths.np, 'asarray', side_effect=AssertionError('Conversion before bounded preflight')):
            for partial in partials:
                forged = replace(prefix, partial=partial, certificate=(inputs.certificate, prefix.first,
                    prefix.history, prefix.completed, partial))
                with self.subTest(ratio=partial[2]), self.assertRaisesRegex(ValueError, 'Canonical bounded'):
                    paths.certify_path_light(inputs, forged, 4, enabled=True, show_starts=False)

    def test_cancellation_between_bounded_full_history_chunks(self):
        inputs, prefix = self.fixture(count=paths.CHUNK+1, history=(paths.CHUNK+1)*2)
        calls = 0
        def count():
            nonlocal calls
            calls += 1
            return False
        paths.certify_path_light(inputs, prefix, 0, enabled=True, show_starts=True, cancel=count)
        self.assertEqual(calls, 4)  # Entry, two chunks, publication.
        for stop in range(1, calls+1):
            current = 0
            def cancelled(stop=stop):
                nonlocal current
                current += 1
                return current == stop
            with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
                paths.certify_path_light(inputs, prefix, 0, enabled=True, show_starts=True, cancel=cancelled)


class EnvironmentPathsTests(unittest.TestCase):
    def prepare(self, count=1, **options):
        return paths.prepare_path_source(*source(count), ('slice', 7), **options)

    def certify_tree(self, result):
        if not result.primitives:
            self.assertEqual(result.nodes.shape, (0, 8))
            self.assertEqual(result.max_depth, 0)
            return
        visited, ids, stack, depth = set(), [], [(0, 1)], 0
        while stack:
            index, d = stack.pop()
            self.assertNotIn(index, visited)
            visited.add(index)
            depth = max(depth, d)
            row = result.nodes[index]
            self.assertTrue(np.isfinite(row).all())
            self.assertTrue(np.all(row[:3] <= row[4:7]))
            self.assertEqual(float(row[3]), int(row[3]))
            self.assertEqual(float(row[7]), int(row[7]))
            if row[7] < 0:
                ids.extend(range(int(row[3]), int(row[3]-row[7])))
            else:
                for child in (int(row[3]), int(row[7])):
                    self.assertTrue(np.all(result.nodes[child, :3] >= row[:3]))
                    self.assertTrue(np.all(result.nodes[child, 4:7] <= row[4:7]))
                    stack.append((child, d+1))
        self.assertEqual(len(visited), len(result.nodes))
        self.assertEqual(sorted(ids), list(range(result.primitives)))
        self.assertEqual(depth, result.max_depth)

    def test_empty_one_odd_groups_and_final_short_group_keep_all_original_ids(self):
        for count in (0, 1, 8, 9, 17, 49, 81, 8193):
            with self.subTest(count=count):
                result = self.prepare(count)
                self.certify_tree(result)
                self.assertLess(result.max_depth, 64)
                self.assertEqual(result.retained_bytes, sum(a.nbytes for a in
                                 (result.nodes, result.low, result.high, result.model)))
                self.assertEqual(result.gpu_bytes, result.nodes.nbytes)

    def test_exact_generation_dimensions_and_model_certificate_retains_borrows(self):
        arrays = source(10)
        result = paths.prepare_path_source(*arrays, ('slice', 7))
        self.assertEqual(result.certificate, paths.source_certificate(*arrays, ('slice', 7)))
        for original, retained in zip(arrays[:3], result.sources, strict=True):
            self.assertIs(original, retained)
            self.assertTrue(original.flags.writeable)
        for output in (result.nodes, result.low, result.high, result.model):
            self.assertFalse(output.flags.writeable)
        replaced = list(arrays)
        replaced[1] = arrays[1].copy()
        self.assertNotEqual(result.certificate, paths.source_certificate(*replaced, ('slice', 7)))
        model = arrays[3].copy()
        model[0, 3] = 2
        self.assertNotEqual(result.certificate, paths.source_certificate(*arrays[:3], model, ('slice', 7)))
        self.assertNotEqual(result.certificate, paths.source_certificate(*arrays, ('slice', 8)))
        arrays[3][0, 3] = 900
        self.assertEqual(result.model[0, 3], 0)  # Frozen before caller changes it.

    def test_all_source_validation_includes_unreferenced_vertices(self):
        p, d, ix, model = source(2)
        for target, value in ((p, math.nan), (d, math.inf), (d, -1)):
            broken = target.copy()
            broken[3, 0] = value
            inputs = (broken, d) if target is p else (p, broken)
            with self.subTest(value=value), self.assertRaises(ValueError):
                paths.prepare_path_source(*inputs, ix[:1], model, 1)
        ix[1, 1] = len(p)
        with patch.object(paths, '_boxes', side_effect=AssertionError('Gather before index validation')):
            with self.assertRaisesRegex(ValueError, 'index outside'):
                paths.prepare_path_source(p, d, ix, model, 1)

    def test_layout_model_and_generation_refuse_without_conversion(self):
        arrays = source(2)
        invalid = [(arrays[0].astype(np.float64), *arrays[1:]),
                   (arrays[0], arrays[1][:, ::-1], *arrays[2:]),
                   (*arrays[:2], arrays[2].astype(np.int64), arrays[3]),
                   (*arrays[:3], arrays[3].astype(np.float64))]
        for row in invalid:
            with self.subTest(layout=tuple(a.dtype for a in row)), self.assertRaises(ValueError):
                paths.prepare_path_source(*row, 1)
        nested = 1
        for _ in range(10):
            nested = (nested,)
        for generation in ([], {}, object(), (1, []), True, (0,)*9, 'x'*257, 1 << 65,
                           nested, ((0,)*8,)*8):
            with self.assertRaises(ValueError):
                paths.prepare_path_source(*arrays, generation)
        for value in (math.nan, math.inf, .5):
            model = arrays[3].copy()
            model[3, 1] = value
            with self.assertRaises(ValueError):
                paths.prepare_path_source(*arrays[:3], model, 1)

    def test_oversized_strided_allocation_refused_before_read_and_real_buffer_views_accepted(self):
        p, d, ix, model = source(1)
        fake = np.lib.stride_tricks.as_strided(p, shape=(10000, 3), strides=p.strides)
        with self.assertRaisesRegex(ValueError, 'allocation'):
            paths.prepare_path_source(fake, np.zeros((10000, 2), np.float32), ix, model, 1)
        # A proper retained bytes allocation is readable without ownership of a
        # second source copy in the builder. Layout validation does not write it.
        buffers = tuple(np.frombuffer(a.tobytes(), dtype=a.dtype).reshape(a.shape) for a in (p, d, ix))
        result = paths.prepare_path_source(*buffers, model, 1)
        self.assertEqual(result.primitives, 1)

    def test_raw_high_endpoint_for_moved_alias_is_retained_outside_shifted_geometry(self):
        p = np.array(((0, 100, 0), (20, 0, 0)), np.float32)
        d = np.array(((.1, 80), (.1, .05)), np.float32)
        result = paths.prepare_path_source(p, d, np.array(((0, 1),), np.uint32), np.eye(4, dtype=np.float32), 1)
        self.assertLess(result.low[0, 1], 60)
        self.assertGreater(result.high[0, 1], 100)
        # Tube dimensions come from B, so its entire ordinary geometry is near
        # shifted A=60. Raw A=100 is solely required by alias discovery.
        self.assertGreater(p[0, 1], 60 + 2*.06)

    def test_original_tube_cap_travel_marker_corners_contained_under_affine_models(self):
        rng = np.random.default_rng(725)
        cases = [((0, 1, 0), (0, 0, 0), (0, .05), (0, .05)),
                 ((0, 1, 0), (1, 1, 0), (.4, .05), (.4, .05)),
                 ((0, 1, 0), (2, 3, 4), (.4, .05), (.1, .6))]
        for _ in range(100):
            cases.append((*rng.uniform(-100, 100, (2, 3)), *rng.uniform(.03, 1, (2, 2))))
        for index, (a, b, ad, bd) in enumerate(cases):
            p, d = np.array((a, b), np.float32), np.array((ad, bd), np.float32)
            model = np.eye(4, dtype=np.float32)
            if index >= 3:
                model[:3, :3] = rng.normal(size=(3, 3))
                model[:3, 3] = rng.uniform(-1000, 1000, 3)
            result = paths.prepare_path_source(p, d, np.array(((0, 1),), np.uint32), model, index)
            corners = original_corners(p[0], p[1], d[0], d[1], model)
            with self.subTest(index=index):
                self.assertTrue(np.all(corners >= result.low[0]), (corners.min(axis=0), result.low[0]))
                self.assertTrue(np.all(corners <= result.high[0]), (corners.max(axis=0), result.high[0]))
        # Reverse-vertical travel's a-h+up lies above a; the old single radius
        # envelope was below this actual corner, even including raw endpoints.
        p, d = np.array(((0, 1, 0), (0, 0, 0)), np.float32), np.array(((0, .05),)*2, np.float32)
        corners = original_corners(p[0], p[1], d[0], d[1], np.eye(4, dtype=np.float32))
        self.assertGreater(corners[:, 1].max(), 1.025)

    def test_tiny_delta_unsafe_radial_and_overflow_refuse_entire_source(self):
        for endpoint in ((1e-9, 0, 0), (1, 1e-9, 0), (1e20, 1, 0), (0, 0, 0)):
            p = np.array(((0, 0, 0), endpoint), np.float32)
            d = np.tile(np.array((.4, .05), np.float32), (2, 1))
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                paths.prepare_path_source(p, d, np.array(((0, 1),), np.uint32), np.eye(4, dtype=np.float32), 1)
        p, d, ix, model = source(1)
        d[:, 0] = 1e30
        with self.assertRaisesRegex(ValueError, 'offset normalization'):
            paths.prepare_path_source(p, d, ix, model, 1)

    def test_budget_charges_build_gpu_and_retained_replacement_before_allocation(self):
        groups, owned, gpu, peak = paths.memory_plan(100, retained_bytes=12345)
        self.assertEqual(groups, 13)
        self.assertEqual(owned, 24*groups + 32*(groups*2-1) + 64)
        self.assertGreaterEqual(peak, owned+gpu+12345)
        with patch.object(paths, '_validate_sources', side_effect=AssertionError('Admission too late')):
            with self.assertRaises(MemoryError):
                self.prepare(100, retained_bytes=12345, byte_budget=peak-1)
        self.prepare(100, retained_bytes=12345, byte_budget=peak)
        for count, group, retained in ((-1, 8, 0), (paths.MAX_ID+1, 8, 0), (1, 0, 0),
                                       (1, 33, 0), (1, 8, -1), (True, 8, 0)):
            with self.assertRaises(ValueError):
                paths.memory_plan(count, group, retained)

    def test_all_gathers_bounded_and_actual_owned_peak_below_declared_plan(self):
        arrays = source(100001)
        sizes = []
        original = paths._boxes
        def bounded(p, d, ix, model):
            sizes.append(len(ix))
            return original(p, d, ix, model)
        tracemalloc.start()
        try:
            with patch.object(paths, '_boxes', bounded):
                result = paths.prepare_path_source(*arrays, 1)
            _, actual_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLessEqual(max(sizes), paths.CHUNK)
        self.assertEqual(sum(sizes), len(arrays[2]))
        self.assertLess(actual_peak, result.peak_bytes-result.gpu_bytes)
        self.certify_tree(result)

    def test_cancellation_at_every_checkpoint_publishes_no_partial_tree(self):
        arrays = source(8301)
        calls = []
        paths.prepare_path_source(*arrays, 1, cancel=lambda: calls.append(1) and False)
        self.assertGreater(len(calls), 10)
        for stop in range(1, len(calls)+1):
            count = 0
            def cancel(stop=stop):
                nonlocal count
                count += 1
                return count == stop
            with self.subTest(checkpoint=stop), self.assertRaises(RuntimeError):
                paths.prepare_path_source(*arrays, 1, cancel=cancel)


if __name__ == '__main__':
    unittest.main()
