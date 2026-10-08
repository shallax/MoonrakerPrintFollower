"""Lighting range selection preserves crossing lines, transforms and prefix limits."""
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
import numpy as np
from mpf.toolhead.ToolheadPathGeometry import ToolheadPathGeometry


class PathGeometryTests(unittest.TestCase):
    def test_empty_mesh_missing_categories_and_dimensions_keep_conservative_ranges(self):
        empty = ToolheadPathGeometry(self.mesh([]))
        self.assertEqual(empty.ranges(None, (0, 0), [(np.zeros(3), 1)]), [])
        mesh = self.mesh([[0, 0, 0], [1, 0, 0]])
        geometry = ToolheadPathGeometry(mesh)
        self.assertIsNone(geometry.exterior_geometry())
        mesh.getAttribute = lambda name: {'value': np.array([[10., 1.]])} if name == 'line_dimensions' else None
        wide = ToolheadPathGeometry(mesh)
        self.assertEqual(wide.ranges(SimpleNamespace(getData=lambda: np.eye(4)), (0, 2),
                                     [(np.array([0., 15., 0.]), 1)]), [(0, 2)])
        self.assertEqual(wide.ranges(SimpleNamespace(getData=lambda: np.eye(4)), (1, 1),
                                     [(np.zeros(3), 1)]), [])

    def test_compact_boundary_indices_keep_hole_walls_and_map_scrub_prefixes(self):
        mesh = self.mesh([[float(x), 0, 0] for x in range(10)])
        kinds = np.repeat([1, 2, 6, 1, 3], 2)
        mesh.getAttribute = lambda name: {"value": kinds} if name == "line_types" else None
        geometry = ToolheadPathGeometry(mesh)
        boundary = geometry.exterior_geometry()
        self.assertIs(boundary, geometry.exterior_geometry())
        self.assertIs(boundary.mesh, mesh)
        np.testing.assert_array_equal(boundary._indices, [0, 1, 6, 7])
        self.assertEqual(geometry.exterior_range(0, 6), (0, 2))
        self.assertEqual(geometry.exterior_range(2, 10), (2, 4))
        self.assertEqual(geometry.exterior_range(4, 6), (2, 2))

    def test_native_missing_attribute_raises_key_error_without_hiding_head(self):
        mesh = self.mesh([[0, 0, 0], [1, 0, 0]])
        attributes = {}
        mesh.getAttribute = attributes.__getitem__
        geometry = ToolheadPathGeometry(mesh)
        self.assertIsNone(geometry.exterior_geometry())
        attributes["line_types"] = {"value": np.array([1, 1]), "opengl_name": "a_line_type"}
        boundary = geometry.exterior_geometry()
        np.testing.assert_array_equal(boundary._indices, [0, 1])

    def test_native_numpy_counts_become_integer_ebo_offsets(self):
        import ctypes
        geometry = ToolheadPathGeometry(self.mesh([[0, 0, 0], [1, 0, 0]]))
        calls = []
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_int,
            ctypes.c_uint, ctypes.c_void_p)
        geometry._draw_elements = callback_type(lambda mode, count, kind, offset: calls.append((mode, count, kind, offset)))
        gl = SimpleNamespace(GL_LINES=1, GL_UNSIGNED_INT=5125)
        for kind in (int, np.int32, np.int64, np.uint32):
            geometry._draw_range(gl, kind(12), kind(20))
        self.assertEqual(calls, [(1, 8, 5125, 48)] * 4)

    def mesh(self, points):
        vertices = np.asarray(points, dtype=np.float32)
        return SimpleNamespace(getVertices=lambda: vertices,
            getIndices=lambda: np.arange(len(vertices), dtype=np.uint32), getAttribute=lambda _: None)

    def test_far_blocks_are_skipped_and_visible_prefix_is_preserved(self):
        points = [[0, 0, 0], [1, 0, 0]] * 512 + [[1000, 0, 0], [1001, 0, 0]] * 512
        geometry = ToolheadPathGeometry(self.mesh(points))
        transform = SimpleNamespace(getData=lambda: np.eye(4))
        self.assertEqual(geometry.ranges(transform, (0, 2048), [(np.zeros(3), 10)]), [(0, 1024)])
        self.assertEqual(geometry.ranges(transform, (20, 800), [(np.zeros(3), 10)]), [(20, 800)])
        self.assertEqual(geometry.ranges(transform, (1024, 2048), [(np.zeros(3), 10)]), [])
        self.assertEqual(geometry.ranges(transform, (0, 2048), []), [])

    def test_long_crossing_segment_and_rotated_transformed_geometry_are_retained(self):
        geometry = ToolheadPathGeometry(self.mesh([[-100, 0, 0], [100, 0, 0]]))
        matrix = np.array([[0, -1, 0, 30], [1, 0, 0, 40], [0, 0, 1, 50], [0, 0, 0, 1]], dtype=float)
        transform = SimpleNamespace(getData=lambda: matrix)
        self.assertEqual(geometry.ranges(transform, (0, 2), [(np.array([30, 40, 50]), 1)]), [(0, 2)])
        self.assertEqual(geometry.ranges(transform, (0, 2), [(np.array([0, 0, 0]), 1)]), [])
        matrix[:3, 3] = 0
        self.assertEqual(geometry.ranges(transform, (0, 2), [(np.zeros(3), 1)]), [(0, 2)])

    def test_adjacent_blocks_merge_and_multiple_lights_retain_disjoint_regions(self):
        points = [[float(x), 0, 0] for x in range(4096)]
        geometry = ToolheadPathGeometry(self.mesh(points))
        transform = SimpleNamespace(getData=lambda: np.eye(4))
        self.assertEqual(geometry.ranges(transform, (0, 4096), [(np.array([512, 0, 0]), 800)]), [(0, 2048)])
        self.assertEqual(geometry.ranges(transform, (0, 4096),
            [(np.zeros(3), 1), (np.array([4095, 0, 0]), 1)]), [(0, 1024), (3072, 4096)])

    def test_float32_world_bounds_use_direct_contraction_and_are_cached(self):
        geometry = ToolheadPathGeometry(self.mesh([[-100, 0, 0], [100, 0, 0]]))
        matrix = np.array([[0, -2, 0, 30], [3, 0, 0, 40], [0, 0, 4, 50], [0, 0, 0, 1]], np.float32)
        transform = SimpleNamespace(getData=lambda: matrix)
        with patch('mpf.toolhead.ToolheadPathGeometry.np.einsum', wraps=np.einsum) as contract:
            for _ in range(2):
                self.assertEqual(geometry.ranges(transform, (0, 2), [(np.array([30, 340, 50]), 1)]), [(0, 2)])
        transforms = [args for args in contract.call_args_list if args.args[0] == 'ij,kj->ik']
        self.assertEqual(len(transforms), 2)  # Centre/extent once, then reuse unchanged bounds.
        self.assertTrue(all(args.kwargs['optimize'] is False for args in transforms))
        low, high = geometry._world_bounds
        np.testing.assert_allclose(low, [[26, -266, 42]])
        np.testing.assert_allclose(high, [[34, 346, 58]])

    def test_only_prefix_blocks_are_distance_tested_including_partial_boundary_blocks(self):
        from unittest.mock import patch
        geometry = ToolheadPathGeometry(self.mesh([[0, 0, 0], [1, 0, 0]] * 5120))
        transform = SimpleNamespace(getData=lambda: np.eye(4))
        actual = np.einsum
        tested = []
        def count_blocks(expression, left, right, **kwargs):
            if expression == 'ij,ij->i': tested.append(len(left))
            return actual(expression, left, right, **kwargs)
        with patch("mpf.toolhead.ToolheadPathGeometry.np.einsum", side_effect=count_blocks):
            self.assertEqual(geometry.ranges(transform, (1030, 2050), [(np.zeros(3), 10)]), [(1030, 2050)])
            self.assertEqual(tested, [2])  # Blocks 1 and 2, not all ten.
            tested.clear()
            self.assertEqual(geometry.ranges(transform, (2048, 3072), [(np.zeros(3), 10)]), [(2048, 3072)])
            self.assertEqual(tested, [1])
            tested.clear()
            self.assertEqual(geometry.ranges(transform, (10240, 10242), [(np.zeros(3), 10)]), [])
            self.assertEqual(tested, [])

    def test_numpy_prefix_counts_preserve_disjoint_block_offsets_and_mesh_end(self):
        points = [[0, 0, 0], [1, 0, 0]] * 512 + [[1000, 0, 0], [1001, 0, 0]] * 512 + [[0, 0, 0], [1, 0, 0]] * 25
        geometry = ToolheadPathGeometry(self.mesh(points))
        transform = SimpleNamespace(getData=lambda: np.eye(4))
        self.assertEqual(geometry.ranges(transform, (np.int64(1000), np.int64(9999)), [(np.zeros(3), 10)]),
                         [(1000, 1024), (2048, 2098)])

    def test_vao_attributes_are_cached_per_shader_and_vertex_buffer_with_one_owned_ebo(self):
        import sys
        from unittest.mock import Mock, patch
        geometry = ToolheadPathGeometry(self.mesh([[0, 0, 0], [1, 0, 0]] * 2))
        geometry.mesh.getVertexCount = lambda: 4
        geometry.mesh.hasNormals = lambda: False
        geometry.mesh.hasColors = lambda: True
        geometry.mesh.hasUVCoordinates = lambda: False
        geometry.mesh.attributeNames = lambda: ["line_types"]
        geometry.mesh.getAttribute = lambda _: dict(opengl_type="float", opengl_name="a_line_type")
        geometry._draw_elements = Mock()
        vao_factory = Mock(side_effect=lambda: Mock(create=Mock(return_value=True)))
        owned_indices = Mock(create=Mock(return_value=True), bufferId=Mock(return_value=11), size=Mock(return_value=16))
        buffer_factory = Mock(return_value=owned_indices)
        buffer_factory.Type = SimpleNamespace(IndexBuffer=1)
        vertex = Mock(bufferId=Mock(return_value=101), size=Mock(return_value=128))
        vertex_lookup = Mock(return_value=vertex)
        matrix = Mock(getData=Mock(return_value=np.eye(4)))
        context = Mock()
        geometry._context = context
        modules = {
            "PyQt6.QtOpenGL": SimpleNamespace(QOpenGLBuffer=buffer_factory, QOpenGLVertexArrayObject=vao_factory),
            "PyQt6.QtGui": SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda: context)),
            "UM.View.GL.OpenGL": SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda: SimpleNamespace(createVertexBuffer=vertex_lookup))),
            "UM.Math.Matrix": SimpleNamespace(Matrix=Mock(return_value=matrix)),
        }
        gl = SimpleNamespace(GL_LINES=1, GL_UNSIGNED_INT=5125)
        camera = Mock()
        a, b = Mock(), Mock()
        with patch.dict(sys.modules, modules):
            geometry.render(a, camera, matrix, [(np.int64(0), np.int64(2))], gl)
            geometry.render(a, camera, matrix, [(2, 4)], gl)
            self.assertEqual(vao_factory.call_count, 1)
            self.assertEqual(a.enableAttribute.call_count, 3)
            a.enableAttribute.assert_any_call("a_vertex", "vector3f", 0)
            a.enableAttribute.assert_any_call("a_color", "vector4f", 48)
            a.enableAttribute.assert_any_call("a_line_type", "float", 112)
            a.setUniformValue.assert_any_call("u_drawElementStart", 0)
            a.setUniformValue.assert_any_call("u_drawElementStart", 2)
            self.assertEqual(owned_indices.allocate.call_count, 1)
            geometry.render(b, camera, matrix, [(0, 2)], gl)
            self.assertEqual(vao_factory.call_count, 2)
            self.assertEqual(b.enableAttribute.call_count, 3)
            # Even a recycled GL name must configure a new buffer wrapper.
            replacement = Mock(bufferId=Mock(return_value=101), size=Mock(return_value=128))
            vertex_lookup.return_value = replacement
            geometry.render(a, camera, matrix, [(0, 2)], gl)
            self.assertEqual(vao_factory.call_count, 3)
            self.assertEqual(a.enableAttribute.call_count, 6)
            self.assertEqual(owned_indices.allocate.call_count, 1)
            self.assertEqual(len(geometry._vaos), 3)
            for vao, kept_buffer in geometry._vaos.values():
                vao.release.assert_called()
                self.assertIn(kept_buffer, (vertex, replacement))

    def test_empty_draw_ranges_do_not_construct_gpu_resources(self):
        geometry = ToolheadPathGeometry(self.mesh([[0, 0, 0], [1, 0, 0]]))
        geometry.render(None, None, None, [], None)
        self.assertEqual(geometry._vaos, {})
        self.assertIsNone(geometry._index_buffer)


class PathRenderContracts(unittest.TestCase):
    def setUp(self):
        self.attributes = {
            'scalar': dict(opengl_type='float', opengl_name='a_scalar'),
            'integer': dict(opengl_type='int', opengl_name='a_integer'),
            'pair': dict(opengl_type='vector2f', opengl_name='a_pair'),
            'triple': dict(opengl_type='vector3f', opengl_name='a_triple'),
            'quad': dict(opengl_type='vector4f', opengl_name='a_quad'),
        }
        mesh = SimpleNamespace(getVertices=lambda: np.zeros((4, 3), dtype=np.float32),
            getIndices=lambda: np.arange(4, dtype=np.uint32), getVertexCount=lambda: 4,
            getAttribute=lambda name: self.attributes.get(name), attributeNames=lambda: list(self.attributes),
            hasNormals=lambda: True, hasColors=lambda: True, hasUVCoordinates=lambda: True)
        self.geometry = ToolheadPathGeometry(mesh)
        self.context = Mock(getProcAddress=Mock(return_value=101))
        self.current_context = Mock(return_value=self.context)
        self.vertex = Mock(bufferId=Mock(return_value=10), size=Mock(return_value=368))
        self.vertex_lookup = Mock(return_value=self.vertex)
        self.indices = Mock(bufferId=Mock(return_value=20), size=Mock(return_value=16))
        self.index_factory = Mock(return_value=self.indices)
        self.index_factory.Type = SimpleNamespace(IndexBuffer=1)
        self.vao = Mock()
        self.vao_factory = Mock(return_value=self.vao)
        self.normal = Mock()
        self.normal_factory = Mock(return_value=self.normal)
        self.shader = Mock()
        self.camera = Mock()
        self.matrix_data = np.eye(4)
        self.transform = SimpleNamespace(getData=lambda: self.matrix_data)
        self.gl = SimpleNamespace(GL_LINES=1, GL_UNSIGNED_INT=5125)
        self.draw = Mock()
        self.proc_constructor = Mock(return_value=self.draw)
        modules = {
            'PyQt6.QtGui': SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=self.current_context)),
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLBuffer=self.index_factory, QOpenGLVertexArrayObject=self.vao_factory),
            'UM.View.GL.OpenGL': SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda:
                SimpleNamespace(createVertexBuffer=self.vertex_lookup))),
            'UM.Math.Matrix': SimpleNamespace(Matrix=self.normal_factory),
        }
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.proc_patch = patch('mpf.toolhead.ToolheadPathGeometry.ctypes.CFUNCTYPE', return_value=self.proc_constructor)
        self.proc_patch.start()
        self.addCleanup(self.proc_patch.stop)

    def render(self, ranges=None):
        self.geometry.render(self.shader, self.camera, self.transform,
                             [(np.int64(0), np.int64(2)), (2, 4)] if ranges is None else ranges, self.gl)

    def test_reflection_upload_is_bounded_and_preserves_original_primitive_offsets(self):
        self.geometry._index_chunk = 2
        self.indices.size.return_value = 8
        self.render([(2, 4)])
        self.indices.allocate.assert_called_once_with(np.array([2, 3], dtype=np.uint32).tobytes(), 8)
        self.assertIsNone(self.draw.call_args.args[3].value)
        self.shader.setUniformValue.assert_called_with('u_drawElementStart', 2)
        with self.assertRaisesRegex(RuntimeError, 'chunk exceeds budget'): self.render([(0, 4)])
        with self.assertRaisesRegex(RuntimeError, 'chunk exceeds budget'): self.render([(0, 2), (2, 4)])

    def test_public_buffer_layout_draw_offsets_and_normal_matrix_are_reused(self):
        self.render()
        self.render()
        self.shader.enableAttribute.assert_has_calls([
            call('a_vertex', 'vector3f', 0), call('a_normal', 'vector3f', 48),
            call('a_color', 'vector4f', 96), call('a_uvs', 'vector2f', 160),
            call('a_scalar', 'float', 192), call('a_integer', 'int', 208),
            call('a_pair', 'vector2f', 224), call('a_triple', 'vector3f', 256),
            call('a_quad', 'vector4f', 304)])
        self.assertEqual(self.shader.enableAttribute.call_count, 9)
        self.vertex.size.assert_called_once()
        self.indices.size.assert_called_once()
        self.indices.allocate.assert_called_once_with(self.geometry._indices.tobytes(), 16)
        self.normal_factory.assert_called_once()
        self.normal.setRow.assert_called_once_with(3, [0, 0, 0, 1])
        self.normal.setColumn.assert_called_once_with(3, [0, 0, 0, 1])
        self.normal.invert.assert_called_once()
        self.normal.transpose.assert_called_once()
        self.shader.updateBindings.assert_called_with(model_matrix=self.transform, normal_matrix=self.normal,
            view_matrix=self.camera.getInverseWorldTransformation.return_value,
            projection_matrix=self.camera.getProjectionMatrix.return_value,
            view_position=self.camera.getWorldPosition.return_value,
            light_0_position=self.camera.getCameraLightPosition.return_value)
        self.assertEqual([entry.args[1] for entry in self.draw.call_args_list], [2, 2, 2, 2])
        self.assertEqual([entry.args[3].value for entry in self.draw.call_args_list], [None, 8, None, 8])
        self.assertTrue(all(type(entry.args[1]) is int for entry in self.draw.call_args_list))
        for resource in (self.shader, self.indices, self.vao, self.vertex):
            self.assertEqual(resource.release.call_count, 2)
        self.matrix_data[0, 0] = 2
        self.render()
        self.assertEqual(self.normal_factory.call_count, 2)

    def test_context_generation_retires_owned_buffers_vaos_and_proc_without_rebinding_old_names(self):
        self.render()
        replacement_context = Mock(getProcAddress=Mock(return_value=201))
        replacement_indices = Mock(bufferId=Mock(return_value=21), size=Mock(return_value=16))
        replacement_vao = Mock()
        self.current_context.return_value = replacement_context
        self.index_factory.return_value = replacement_indices
        self.vao_factory.return_value = replacement_vao
        self.render()
        self.assertIs(self.geometry._context, replacement_context)
        self.assertIs(self.geometry._index_buffer, replacement_indices)
        self.assertEqual(self.proc_constructor.call_args_list, [call(101), call(201)])
        self.assertEqual(len(self.geometry._vaos), 1)
        self.indices.bind.assert_called_once()
        self.vao.bind.assert_called_once()
        replacement_indices.allocate.assert_called_once()
        replacement_vao.bind.assert_called_once()
        self.indices.destroy.assert_not_called()
        self.vao.destroy.assert_not_called()

    def test_missing_context_entrypoint_vertex_wrapper_or_raw_buffer_name_prevents_draw(self):
        cases = (
            (self.current_context, None, 'context unavailable'),
            (self.context.getProcAddress, None, 'index draw unavailable'),
            (self.vertex_lookup, None, 'vertex buffer unavailable'),
            (self.vertex.bufferId, 0, 'vertex buffer unavailable'),
            (self.indices.bufferId, 0, 'index buffer unavailable'),
        )
        for method, value, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                self.geometry._context = None
                self.geometry._draw_elements = self.geometry._index_buffer = None
                self.geometry._vaos.clear()
                self.draw.reset_mock()
                previous = method.return_value
                method.return_value = value
                try:
                    with self.assertRaisesRegex(RuntimeError, diagnostic): self.render()
                    self.draw.assert_not_called()
                    self.assertEqual(self.geometry._vaos, {})
                    self.assertIsNone(self.geometry._index_buffer)
                finally:
                    method.return_value = previous

    def test_failed_acquisitions_release_every_attempted_binding_and_allow_retry(self):
        cases = (
            (self.vao.create, False, 'VAO could not', ()),
            (self.vao.bind, RuntimeError('vao bind failed'), 'vao bind failed', ('vao',)),
            (self.vertex.bind, False, 'vertex buffer could not', ('vao', 'vertex')),
            (self.indices.create, False, 'index buffer could not be created', ('vao', 'vertex')),
            (self.indices.bind, False, 'index buffer could not be bound', ('vao', 'vertex', 'indices')),
            (self.indices.allocate, RuntimeError('allocation failed'), 'allocation failed', ('vao', 'vertex', 'indices')),
            (self.indices.size, 0, 'index storage incomplete', ('vao', 'vertex', 'indices')),
            (self.shader.bind, False, 'shader could not', ('vao', 'vertex', 'indices', 'shader')),
            (self.vertex.size, 367, 'vertex buffer storage incomplete', ('vao', 'vertex', 'indices', 'shader')),
        )
        for method, value, diagnostic, released in cases:
            with self.subTest(diagnostic=diagnostic):
                self.geometry._vaos.clear()
                self.geometry._index_buffer = None
                for resource in (self.vao, self.vertex, self.indices, self.shader): resource.reset_mock()
                self.draw.reset_mock()
                previous = method.return_value
                if isinstance(value, Exception): method.side_effect = value
                else: method.return_value = value
                try:
                    with self.assertRaisesRegex(RuntimeError, diagnostic): self.render()
                    for name in ('vao', 'vertex', 'indices', 'shader'):
                        release = getattr(self, name).release
                        if name in released: release.assert_called_once()
                        else: release.assert_not_called()
                    self.draw.assert_not_called()
                    self.assertIsNone(self.geometry._index_buffer)
                finally:
                    method.return_value = previous
                    method.side_effect = None
                self.render()
                self.assertEqual(self.draw.call_count, 2)

    def test_existing_index_bind_failure_is_checked_and_releases_other_bindings(self):
        self.render()
        self.indices.bind.return_value = False
        self.draw.reset_mock()
        for resource in (self.shader, self.indices, self.vao, self.vertex): resource.release.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'index buffer could not be bound'): self.render()
        self.shader.release.assert_not_called()
        for resource in (self.indices, self.vao, self.vertex): resource.release.assert_called_once()
        self.draw.assert_not_called()

    def test_draw_and_cleanup_failures_preserve_first_error_and_release_independently(self):
        self.draw.side_effect = ValueError('draw failed first')
        self.shader.release.side_effect = RuntimeError('shader release failed')
        self.indices.release.side_effect = RuntimeError('index release failed')
        with self.assertRaisesRegex(RuntimeError, 'draw failed first') as error: self.render()
        self.assertIsInstance(error.exception.__cause__, ValueError)
        self.vao.release.assert_called_once()
        self.vertex.release.assert_called_once()
        self.assertEqual(self.geometry._vaos, {})
        self.assertIsNone(self.geometry._index_buffer)

    def test_cleanup_only_failure_propagates_after_all_resources_are_released(self):
        self.indices.release.side_effect = RuntimeError('index cleanup failed')
        with self.assertRaisesRegex(RuntimeError, 'index cleanup failed'): self.render()
        for resource in (self.shader, self.indices, self.vao, self.vertex): resource.release.assert_called_once()

    def test_ranges_outside_owned_ebo_are_rejected_before_gpu_acquisition(self):
        for bounds in ((-1, 2), (2, 1), (0, 6)):
            with self.subTest(bounds=bounds):
                with self.assertRaisesRegex(RuntimeError, 'range exceeds owned buffer'): self.render([bounds])
        self.vertex_lookup.assert_not_called()
        self.draw.assert_not_called()

    def test_public_shader_void_bind_contract_is_accepted(self):
        # Uranium's ShaderProgram.bind() returns None, unlike Qt's bool API.
        self.shader.bind.return_value = None
        self.render()
        self.assertEqual(self.draw.call_count, 2)
