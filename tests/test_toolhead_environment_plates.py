"""All original plate triangles block recovery, including transparent surfaces."""
from dataclasses import replace
import hashlib
import re
import unittest
from unittest.mock import patch

import numpy as np

from mpf.toolhead import ToolheadEnvironmentPlates as plates
from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, UniformValue

VERTEX = 'void main(){gl_Position=u_projectionMatrix*u_viewMatrix*u_modelMatrix*a_vertex;}'
FRAGMENT = 'void main(){frag_color=v_color;}'
STAGES = (('vertex', VERTEX), ('fragment', FRAGMENT))
BINDINGS = tuple(plates.POSITION_BINDINGS.items())
HASHES = tuple(hashlib.sha256(re.sub(r'\s+', '', source).encode()).hexdigest() for _stage, source in STAGES)


class EnvironmentPlatesTests(unittest.TestCase):
    def setUp(self):
        # Synthetic accepted recipes isolate the pure delivery contract. Actual
        # public shader fingerprints are independently checked in native proof.
        self.patch = patch.dict(plates.RECIPES, {'default': HASHES, 'platform': HASHES, 'grid': HASHES})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.recipes = (('default', STAGES, BINDINGS),)

    @staticmethod
    def mesh(indexed=True, count=10):
        vertices = np.arange(count*9, dtype=np.float32).reshape(count*3, 3)/10
        colours = np.zeros((len(vertices), 4), np.float32)  # Alpha ZERO is still depth geometry.
        indices = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3) if indexed else None
        return CaptureMesh(17, vertices, indices, None, colours, None, ())

    def entry(self, mesh=None, model=None):
        matrix = np.eye(4, dtype=np.float32) if model is None else model
        return ('default', self.mesh() if mesh is None else mesh,
                UniformValue('matrix', tuple(map(float, matrix.reshape(-1)))), None, (), ())

    def build(self, entries=None, **options):
        return plates.prepare_plate_source((self.entry(),) if entries is None else entries,
            self.recipes, ('scene', 7), **options)

    def test_all_original_indexed_and_nonindexed_triangles_and_transforms_preserved(self):
        a, b = self.mesh(), self.mesh(False, 3)
        model = np.eye(4, dtype=np.float32); model[:3, 3] = (30, -5, 9)
        result = self.build((self.entry(a), self.entry(b, model)))
        np.testing.assert_array_equal(result.positions[:len(a.vertices)], a.vertices)
        np.testing.assert_array_equal(result.positions[len(a.vertices):], b.vertices)
        np.testing.assert_array_equal(result.triangles[:10, :3], a.indices)
        np.testing.assert_array_equal(result.triangles[10:, :3], np.arange(9).reshape(-1, 3)+30)
        np.testing.assert_array_equal(result.triangles[:, 3], [0]*10+[1]*3)
        np.testing.assert_array_equal(result.models[1], model)
        self.assertEqual(result.source_bytes, a.retained_bytes()+b.retained_bytes())
        self.assertIs(result.sources[0][0], a)
        self.assertFalse(any(array.flags.writeable for array in
                             (result.positions, result.triangles, result.models, result.nodes)))
        leaves = result.nodes[result.nodes[:, 7] < 0]
        covered = sorted(line for node in leaves for line in range(int(node[3]), int(node[3]-node[7])))
        self.assertEqual(covered, list(range(13)))
        self.assertTrue(a.vertices.flags.writeable)

    def test_bounds_contain_actual_f32_models_for_every_original_triangle(self):
        model = np.array(((2, .5, -.1, 1000), (0, -1, .2, -200), (1, 0, 2, 300), (0, 0, 0, 1)), np.float32)
        mesh = self.mesh()
        result = self.build((self.entry(mesh, model),))
        for node in result.nodes[result.nodes[:, 7] < 0]:
            for triangle in range(int(node[3]), int(node[3]-node[7])):
                for index in result.triangles[triangle, :3]:
                    actual = model @ np.append(result.positions[index], np.float32(1))
                    self.assertTrue(np.all(actual[:3] >= node[:3]))
                    self.assertTrue(np.all(actual[:3] <= node[4:7]))
        repeated = self.build((self.entry(mesh, model),))
        self.assertEqual(result.certificate, repeated.certificate)
        different = self.mesh(); different.vertices[0, 0] = 90
        self.assertNotEqual(result.certificate, self.build((self.entry(different, model),)).certificate)

    def test_recipe_hash_binding_and_stage_admission_preserves_position_depth_semantics(self):
        self.assertEqual(plates.certify_plate_recipe('default', STAGES, BINDINGS)[1], HASHES)
        decorated = tuple((stage, ' // comment\n'+source.replace(';', '; \n')) for stage, source in STAGES)
        self.assertEqual(plates.certify_plate_recipe('default', decorated, BINDINGS)[1], HASHES)
        cases = (('other', STAGES, BINDINGS), ('default', STAGES+(('geometry', ''),), BINDINGS),
                 ('default', (('vertex', VERTEX), ('fragment', FRAGMENT+'discard;')), BINDINGS),
                 ('default', (('vertex', VERTEX+'gl_Position.x+=1.;'), ('fragment', FRAGMENT)), BINDINGS),
                 ('default', (('vertex', VERTEX), ('vertex', VERTEX)), BINDINGS),
                 ('default', (('vertex', 'x'*65537), ('fragment', FRAGMENT)), BINDINGS),
                 ('default', STAGES, BINDINGS+(BINDINGS[0],)), ('default', STAGES, BINDINGS[:-1]),
                 ('default', STAGES, list(BINDINGS)))
        for args in cases:
            with self.subTest(args=args[0]), self.assertRaises(ValueError): plates.certify_plate_recipe(*args)

    def test_memory_preflight_includes_cpu_original_and_new_gpu_and_old_owners(self):
        result = self.build()
        self.assertGreater(result.retained_bytes, result.gpu_bytes)
        self.assertGreater(result.peak_bytes, result.source_bytes+result.retained_bytes+result.gpu_bytes)
        with patch.object(plates.np, 'empty', side_effect=AssertionError('Allocation before admission')):
            with self.assertRaises(MemoryError): self.build(byte_budget=result.peak_bytes-1)
            with self.assertRaises(MemoryError): self.build(byte_budget=result.peak_bytes, retained_bytes=1)
        self.build(byte_budget=result.peak_bytes)
        self.assertEqual(self.build(retained_bytes=50).peak_bytes, result.peak_bytes+50)

    def test_retained_models_are_charged_separately_from_packed_model_rows(self):
        result = self.build(tuple(self.entry(self.mesh(count=1)) for _ in range(40)))
        payload = sum(array.nbytes for array in
                      (result.positions, result.triangles, result.models, result.nodes))
        self.assertEqual(result.retained_bytes, payload+40*64)
        self.assertEqual(sum(model.nbytes for _mesh, _indices, model in result.sources), 40*64)

    def test_incomplete_topology_unsafe_views_indices_numeric_models_and_overrides_refuse(self):
        mesh = self.mesh()
        invalid = (replace(mesh, vertices=mesh.vertices[:, ::-1]),
                   replace(mesh, vertices=mesh.vertices.astype(np.float64)),
                   replace(mesh, vertices=np.full(mesh.vertices.shape, np.nan, np.float32)),
                   replace(mesh, indices=mesh.indices.astype(np.int64)),
                   replace(mesh, indices=np.ones(4, np.int32)),
                   replace(mesh, indices=np.full(mesh.indices.shape, -1, np.int32)),
                   replace(mesh, indices=None, vertices=mesh.vertices[:-1]))
        for source in invalid:
            with self.subTest(shape=source.vertices.shape), self.assertRaises(ValueError):
                self.build((self.entry(source),))
        for name in ('model_matrix', 'u_viewMatrix', 'projection_matrix'):
            entry = list(self.entry()); entry[4] = ((name, UniformValue('scalar', 1)),)
            with self.assertRaises(ValueError): self.build((tuple(entry),))
            entry[4], entry[5] = (), entry[4]
            with self.assertRaises(ValueError): self.build((tuple(entry),))
        for model in (np.zeros((4, 4)), np.full((4, 4), np.inf), np.eye(4)*1e39):
            with self.assertRaises(ValueError): self.build((self.entry(model=model),))
        entry = list(self.entry()); entry[2] = UniformValue('matrix', tuple(range(16)))
        with self.assertRaises(ValueError): self.build((tuple(entry),))

    def test_empty_cancellation_metadata_and_caps_publish_no_partial_source(self):
        empty = self.build(())
        self.assertEqual(empty.positions.shape, (0, 3)); self.assertEqual(len(empty.nodes), 0)
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
            self.build(cancel=lambda: True)
        polls = [0]
        def cancel():
            polls[0] += 1
            return polls[0] == 6
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'): self.build(cancel=cancel)
        for entries in ([self.entry()], (self.entry(),)*2049, (('bad',),)):
            with self.assertRaises(ValueError): self.build(entries)
        with self.assertRaises(ValueError): self.build(retained_bytes=-1)
        with self.assertRaises(ValueError): self.build((self.entry(replace(self.mesh(), identity=0)),))
        with self.assertRaises(ValueError):
            plates.prepare_plate_source((self.entry(),), self.recipes*2, 7)
        # Metadata-only admission refuses a large vertex publication before
        # traversal of source contents or allocating destination arrays.
        huge = np.lib.stride_tricks.as_strided(np.zeros((1, 3), np.float32),
                                              shape=(250001, 3), strides=(12, 4))
        with self.assertRaises(ValueError): self.build((self.entry(replace(self.mesh(), vertices=huge)),))
