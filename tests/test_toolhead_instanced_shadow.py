"""Public native buffers and GL state contracts; doubles do not prove GPU pixels."""
from pathlib import Path
import ctypes
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from mpf.toolhead.ToolheadInstancedShadow import ToolheadInstancedShadow, mesh_layout, shadow_sources
from mpf.toolhead import ToolheadInstancedShadow as module


FIXTURE = Path(__file__).parent / 'fixtures/toolhead/native_shadow.shader'


class Mesh:
    def __init__(self):
        self.vertices = np.array([[0, 0, 0], [1, 0, 0]], np.float32)
        self.indices = np.array([0, 1], np.int32)  # Real public MeshData uses signed int32.
        self.colors = np.ones((2, 4), np.float32)
        self.attributes = {
            'colors': dict(value=self.colors, opengl_type='vector4f', opengl_name='a_material_color'),
            'extruders': dict(value=np.zeros(2, np.float32), opengl_type='float', opengl_name='a_extruder'),
            'line_dimensions': dict(value=np.ones((2, 2), np.float32), opengl_type='vector2f', opengl_name='a_line_dim'),
            'line_types': dict(value=np.ones(2, np.float32), opengl_type='float', opengl_name='a_line_type'),
        }
    def getVertexCount(self): return len(self.vertices)
    def getVertices(self): return self.vertices
    def getIndices(self): return self.indices
    def hasNormals(self): return False
    def hasColors(self): return True
    def hasUVCoordinates(self): return False
    def getColors(self): return self.colors and len(self.colors) > 0  # Exact native getter contract.
    def getColorsAsByteArray(self): return self.colors.tobytes()
    def attributeNames(self): return sorted(self.attributes)
    def getAttribute(self, name): return self.attributes[name]


class GL:
    def __init__(self):
        self.program, self.vao, self.array, self.active = 17, 9, 11, 0x84C3
        self.textures = {0x84C0: 31, 0x84C1: 41}
        self.elements = {9: 27}
        self.maximum = 1000000
        self.cull, self.front = 0x0405, 0x0901
        self.calls = []
        self.glGetError = Mock(return_value=0)
        self.glIsEnabled = Mock(return_value=True)
    def glGetIntegerv(self, name):
        return {0x84E0: self.active, 0x8C2C: self.textures.get(self.active, 0),
            0x0B45: self.cull, 0x0B46: self.front, 0x8B8D: self.program, 0x85B5: self.vao, 0x8894: self.array, 0x8C2B: self.maximum}[name]
    def glActiveTexture(self, value): self.active = value
    def glBindTexture(self, target, value): self.textures[self.active] = value
    def glUseProgram(self, value): self.program = value
    def glBindVertexArray(self, value): self.vao = value
    def glBindBuffer(self, target, value):
        if target == 0x8892: self.array = value
        else: self.elements[self.vao] = value


class InstancedShadowTests(unittest.TestCase):
    def setUp(self):
        self.gl = GL()
        self.mesh = Mesh()
        self.major, self.minor, self.gles = 4, 1, False
        self.context = SimpleNamespace(isOpenGLES=lambda: self.gles,
            format=lambda: SimpleNamespace(majorVersion=lambda: self.major, minorVersion=lambda: self.minor),
            getProcAddress=Mock(return_value=1))
        self.current = self.context
        self.buffers, self.vaos, self.shaders = [], [], []
        self.next_id = 70
        owner = self
        class Buffer:
            Type = SimpleNamespace(IndexBuffer=0x8893)
            def __init__(self, kind=0x8892):
                self.kind, self.id, self.body = kind, owner.next_id, b''
                owner.next_id += 1
                self.create = Mock(return_value=True)
                self.destroy = Mock()
                owner.buffers.append(self)
            def bufferId(self): return self.id
            def bind(self): owner.gl.glBindBuffer(self.kind, self.id); return True
            def size(self):
                binding = owner.gl.array if self.kind == 0x8892 else owner.gl.elements.get(owner.gl.vao)
                if binding != self.id: raise RuntimeError('size requires a bound native buffer')
                return len(self.body)
            def allocate(self, body, size): self.body = body[:size]
        class VAO:
            def __init__(self):
                self.id = owner.next_id; owner.next_id += 1
                self.create, self.destroy = Mock(return_value=True), Mock()
                owner.vaos.append(self)
            def bind(self): owner.gl.glBindVertexArray(self.id)
        class Shader:
            def __init__(self):
                self.setVertexShader, self.setFragmentShader = Mock(return_value=True), Mock(return_value=True)
                self.build, self.addBinding, self.setUniformValue, self.updateBindings = Mock(), Mock(), Mock(), Mock()
                self.bind = Mock(side_effect=lambda: owner.gl.glUseProgram(83))
                self.release = Mock(side_effect=lambda: owner.gl.glUseProgram(0))
                owner.shaders.append(self)
        self.vertex = Buffer()
        expected = mesh_layout(self.mesh)[2]
        self.vertex.body = bytes(expected)
        self.create_vertex = Mock(return_value=self.vertex)
        self.logger = Mock()
        self.qt = {
            'PyQt6.QtGui': SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda: self.current)),
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLBuffer=Buffer, QOpenGLVertexArrayObject=VAO),
            'UM.View.GL.OpenGL': SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda: SimpleNamespace(createVertexBuffer=self.create_vertex))),
            'UM.View.GL.ShaderProgram': SimpleNamespace(ShaderProgram=Shader),
            'UM.Logger': SimpleNamespace(Logger=self.logger),
        }
        scope = patch.dict(sys.modules, self.qt)
        scope.start(); self.addCleanup(scope.stop)
        self.helper = ToolheadInstancedShadow(diagnostic=True)
        self.deleted, self.tex_buffers, self.draws = [], [], []
        def generate(count, textures):
            for index in range(count): textures[index] = 101 + index
        def delete(count, textures): self.deleted.append(tuple(textures))
        self.procs = {
            b'glGenTextures': generate, b'glDeleteTextures': delete,
            b'glTexBuffer': lambda *args: self.tex_buffers.append(args),
            b'glDrawElementsInstanced': lambda *args: self.draws.append(args),
        }
        self.helper._proc = lambda context, name, *args: self.procs[name]
        self.view = SimpleNamespace(getShowTravelMoves=lambda: False, getShowSkin=lambda: True,
            getShowInfill=lambda: False, getShowHelpers=lambda: False, getExtruderOpacities=lambda: SimpleNamespace(getData=lambda: np.ones((4, 4), np.float32)))
        self.camera = SimpleNamespace(getInverseWorldTransformation=lambda: 'view', getProjectionMatrix=lambda: 'projection',
            getCameraLightPosition=lambda: 'light')

    def render(self, start=0, end=2):
        return self.helper.render(self.mesh, FIXTURE, self.camera, 'model', start, end, self.view, self.gl)

    def assert_restored(self):
        self.assertEqual((self.gl.program, self.gl.vao, self.gl.array, self.gl.active), (17, 9, 11, 0x84C3))
        self.assertEqual(self.gl.textures, {0x84C0: 31, 0x84C1: 41})
        self.assertEqual(self.gl.elements[9], 27)

    def test_draw_retains_native_vbo_and_packed_indices_and_restores_every_touched_binding(self):
        self.assertTrue(self.render())
        storage = self.helper._storage
        self.assertIs(storage['vertex'], self.vertex)
        np.testing.assert_array_equal(np.frombuffer(storage['lines'].body, np.uint32), [0, 1])
        self.assertEqual(len(storage['template'].body), 192)
        self.assertEqual(self.tex_buffers, [(0x8C2A, 0x822E, self.vertex.id), (0x8C2A, 0x8236, storage['lines'].id)])
        self.assertEqual(self.draws[0][:3], (4, 48, 0x1405))
        self.assertIsNone(self.draws[0][3].value)
        self.assertEqual(self.draws[0][4], 1)
        self.shaders[0].setUniformValue.assert_any_call('u_show_helpers', 0)
        self.shaders[0].setUniformValue.assert_any_call('u_lineStart', 0)
        self.shaders[0].updateBindings.assert_called_once_with(model_matrix='model', view_matrix='view', projection_matrix='projection', light_0_position='light')
        self.assert_restored()
        self.assertTrue(self.render())
        self.create_vertex.assert_called_once_with(self.mesh)
        self.assertEqual(len(self.buffers), 3)
        self.assertEqual(self.logger.log.call_count, 1)

    def test_empty_and_numpy_prefix_ranges_keep_exact_absolute_element_addressing(self):
        self.assertTrue(self.render(np.int32(2), np.uint32(2)))
        self.shaders[0].setUniformValue.assert_any_call('u_lineStart', 2)
        self.assertEqual(self.draws[0][4], 0)
        self.assert_restored()

    def test_context_version_gles_travel_and_malformed_opacity_decline_without_allocations(self):
        cases = [(None, 4, 1, False), (self.context, 3, 3, False), (self.context, 4, 1, True)]
        for current, major, minor, gles in cases:
            with self.subTest(current=current, major=major, gles=gles):
                self.current, self.major, self.minor, self.gles = current, major, minor, gles
                self.assertFalse(self.render())
        self.current, self.major, self.minor, self.gles = self.context, 4, 1, False
        self.view.getShowTravelMoves = lambda: True
        self.assertFalse(self.render())
        self.view.getShowTravelMoves = lambda: False
        self.view.getExtruderOpacities = lambda: [[float('nan')] * 4 for _ in range(4)]
        self.assertFalse(self.render())
        self.view.getExtruderOpacities = lambda: [1.]
        self.assertFalse(self.render())
        self.create_vertex.assert_not_called()

    def test_unknown_shader_layout_limits_and_missing_entry_points_decline_once(self):
        for reason in ('shader', 'layout', 'maximum', 'proc', 'size'):
            with self.subTest(reason=reason):
                helper = ToolheadInstancedShadow(diagnostic=True); helper._proc = self.helper._proc
                scope = patch.object(module, 'shadow_sources', return_value=None) if reason == 'shader' else patch.object(module, 'MAX_INDEX_BYTES', 1) if reason == 'layout' else patch.object(self.gl, 'maximum', 1) if reason == 'maximum' else patch.object(self.context, 'getProcAddress', return_value=0) if reason == 'proc' else patch.object(self.vertex, 'size', return_value=1)
                with scope:
                    self.assertFalse(helper.render(self.mesh, FIXTURE, self.camera, 'model', 0, 2, self.view, self.gl))
                    self.assertFalse(helper.render(self.mesh, FIXTURE, self.camera, 'model', 0, 2, self.view, self.gl))
                self.assert_restored()
                self.assertIs(helper._blocked, self.mesh)

    def test_source_parse_errors_decline_and_diagnostic_failure_is_contained(self):
        self.logger.log.side_effect = RuntimeError('logging retired')
        self.assertFalse(self.helper.render(self.mesh, '/missing/native.shader', self.camera, 'model', 0, 2, self.view, self.gl))
        self.assert_restored()

    def test_mesh_change_retires_owned_names_only_and_new_context_does_not_delete_old_names(self):
        self.render(); old = self.helper._storage
        self.mesh = Mesh(); self.render()
        self.assertEqual(self.deleted, [(101, 102)])
        old['vao'].destroy.assert_called_once()
        self.assertEqual(self.vertex.destroy.call_count, 0)
        self.current = SimpleNamespace(**vars(self.context))
        self.render()
        self.assertEqual(len(self.deleted), 1)  # Names belong to retired context/share group.
        self.helper.close()
        self.assertEqual(len(self.deleted), 2)

    def test_cleanup_errors_do_not_abort_retirement_of_other_owned_objects(self):
        self.render(); storage = self.helper._storage
        storage['delete'] = Mock(side_effect=RuntimeError('delete'))
        storage['vao'].destroy.side_effect = RuntimeError('vao')
        self.helper.close()
        storage['lines'].destroy.assert_called_once()
        storage['template'].destroy.assert_called_once()
        self.assertIsNone(self.helper._storage)

    def test_invalid_ranges_raise_after_state_restore_and_retire_cache(self):
        for start, end in ((-2, 2), (0, 4), (1, 2), (2, 0), (0, 1)):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(RuntimeError, 'instanced draw failed'): self.render(start, end)
                self.assert_restored()
                self.assertIsNone(self.helper._storage)

    def test_draw_or_shader_cleanup_error_restores_host_state_then_requests_full_native_fallback(self):
        self.render()
        for fail in ('draw', 'release', 'bind', 'link'):
            with self.subTest(fail=fail):
                storage = self.helper._storage
                if storage is None: self.render(); storage = self.helper._storage
                if fail == 'draw': storage['draw'] = Mock(side_effect=RuntimeError('draw'))
                elif fail == 'release': storage['shader'].release.side_effect = RuntimeError('release')
                elif fail == 'bind': storage['template'].bind = Mock(return_value=False)
                else: storage['shader'].bind.side_effect = lambda: None
                with self.assertRaises(RuntimeError): self.render()
                self.assert_restored()
                self.assertIsNone(self.helper._storage)

    def test_failed_restoration_attempts_remaining_bindings_even_on_declined_admission(self):
        actual = self.gl.glBindTexture
        self.gl.glBindTexture = Mock(side_effect=lambda target, value: (_ for _ in ()).throw(RuntimeError('restore texture')) if value == 31 else actual(target, value))
        with patch.object(module, 'shadow_sources', return_value=None):
            with self.assertRaises(RuntimeError): self.render()
        self.assertEqual((self.gl.program, self.gl.vao, self.gl.array, self.gl.active), (17, 9, 11, 0x84C3))
        self.assertEqual(self.gl.textures[0x84C1], 41)

    def test_public_proc_uses_typed_integer_count_abi_and_rejects_missing_address(self):
        calls = []
        prototype = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_int)
        callback = prototype(lambda mode, count: calls.append((mode, count)))
        context = SimpleNamespace(getProcAddress=lambda name: ctypes.cast(callback, ctypes.c_void_p).value)
        raw = ToolheadInstancedShadow._proc(context, b'draw', ctypes.c_uint, ctypes.c_int)
        raw(4, 48)
        self.assertEqual(calls, [(4, 48)])
        context.getProcAddress = lambda name: 0
        with self.assertRaises(RuntimeError): ToolheadInstancedShadow._proc(context, b'draw')

    def test_content_fingerprint_accepts_known_shader_and_normalized_line_endings_only(self):
        self.assertIn('finalColor', shadow_sources(FIXTURE))
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.shader'
            path.write_bytes(FIXTURE.read_bytes().replace(b'\n', b'\r\n'))
            self.assertIsNotNone(shadow_sources(path))
            path.write_text(FIXTURE.read_text().replace('size_x = 0.05', 'size_x = 0.06'))
            self.assertIsNone(shadow_sources(path))

    def test_layout_matches_native_int32_plural_attributes_without_copying_endpoint_arrays(self):
        indices, offsets, size = mesh_layout(self.mesh)
        self.assertTrue(np.shares_memory(indices, self.mesh.indices))
        self.assertEqual(offsets, {'extruders': 22, 'line_dimensions': 24, 'line_types': 28})
        self.assertEqual(size, 120)
        self.assertEqual(self.mesh.indices.dtype, np.int32)

    def test_native_broken_color_getter_is_unused_and_byte_copy_occurs_once_per_mesh(self):
        with self.assertRaises(ValueError): self.mesh.getColors()
        self.mesh.getColors = Mock(side_effect=AssertionError('Native array getter must not be used'))
        self.mesh.getColorsAsByteArray = Mock(wraps=self.mesh.getColorsAsByteArray)
        self.assertTrue(self.render())
        self.assertTrue(self.render())
        self.mesh.getColors.assert_not_called()
        self.mesh.getColorsAsByteArray.assert_called_once()
        self.assertEqual(self.helper._storage['offsets']['extruders'], 22)

    def test_color_bytes_absent_truncated_or_wrong_scalar_size_decline_before_gpu_upload(self):
        for body in (None, b'', bytes(16), bytes(64)):
            with self.subTest(size=None if body is None else len(body)):
                mesh = Mesh(); mesh.getColorsAsByteArray = lambda body=body: body
                reasons = []
                self.assertIsNone(mesh_layout(mesh, reasons.append))
                self.assertEqual(reasons, ['native color byte count'])

    def test_index_budget_exact_boundary_and_gl_vertex_and_index_limits_remain_independent(self):
        self.assertEqual(module.MAX_INDEX_BYTES, 128 * 1024 * 1024)
        with patch.object(module, 'MAX_INDEX_BYTES', self.mesh.indices.nbytes):
            self.assertIsNotNone(mesh_layout(self.mesh))
        reasons = []
        with patch.object(module, 'MAX_INDEX_BYTES', self.mesh.indices.nbytes - 1):
            self.assertIsNone(mesh_layout(self.mesh, reasons.append))
        self.assertEqual(reasons, ['index bytes 8 exceed budget 7'])
        with patch.object(module, 'mesh_layout', return_value=(self.mesh.indices, {}, 4)):
            self.gl.maximum = 1
            self.assertFalse(self.render())  # Index count alone exceeds hardware texel limit.
        self.assertIn('index elements 2', self.logger.log.call_args.args[2])
        self.create_vertex.assert_not_called()

    def test_opt_in_rejection_records_precise_reason_once_and_default_remains_quiet(self):
        cases = [('native shader fingerprint', patch.object(module, 'shadow_sources', return_value=None)),
            ('index bytes 8 exceed budget 1', patch.object(module, 'MAX_INDEX_BYTES', 1)),
            ('texture texel limit 1; vertex floats 30; index elements 2', patch.object(self.gl, 'maximum', 1)),
            ('GL entry points', patch.object(self.context, 'getProcAddress', return_value=0)),
            ('native VBO bytes 1; expected 120', patch.object(self.vertex, 'size', return_value=1))]
        for reason, scope in cases:
            with self.subTest(reason=reason), scope:
                self.logger.log.reset_mock()
                helper = ToolheadInstancedShadow(diagnostic=True); helper._proc = self.helper._proc
                for _ in range(2):
                    self.assertFalse(helper.render(self.mesh, FIXTURE, self.camera, 'model', 0, 2, self.view, self.gl))
                self.logger.log.assert_called_once_with('i', 'toolhead shadow instancing: %s', f'native fallback ({reason})')
                self.assert_restored()
        self.logger.log.reset_mock()
        quiet = ToolheadInstancedShadow()
        self.assertFalse(quiet.render(self.mesh, '/missing.shader', self.camera, 'model', 0, 2, self.view, self.gl))
        self.logger.log.assert_not_called()

    def test_optional_normals_and_uv_storage_follow_native_soa_order(self):
        mesh = Mesh()
        mesh.hasNormals = lambda: True
        mesh.hasUVCoordinates = lambda: True
        mesh.getNormals = lambda: np.ones((2, 3), np.float32)
        mesh.getUVCoordinates = lambda: np.ones((2, 2), np.float32)
        _, offsets, size = mesh_layout(mesh)
        self.assertEqual(offsets, {'extruders': 32, 'line_dimensions': 34, 'line_types': 38})
        self.assertEqual(size, 160)
        mesh.getUVCoordinates = lambda: np.ones((2, 2), np.float64)
        reasons = []
        self.assertIsNone(mesh_layout(mesh, reasons.append))
        self.assertEqual(reasons, ['native getUVCoordinates layout'])

    def test_layout_rejects_invalid_arrays_indices_categories_and_unknown_attribute_abi(self):
        mutations = [lambda m: setattr(m, 'vertices', m.vertices.astype(np.float64)),
            lambda m: setattr(m, 'indices', np.array([-1, 1], np.int32)),
            lambda m: setattr(m, 'indices', np.array([0, 2], np.int32)),
            lambda m: setattr(m, 'indices', np.array([0], np.int32)),
            lambda m: setattr(m, 'indices', m.indices.astype(np.int64)),
            lambda m: setattr(m, 'vertices', np.full((2, 3), np.nan, np.float32)),
            lambda m: setattr(m, 'colors', m.colors.astype(np.float64)),
            lambda m: setattr(m, 'attributeNames', lambda: list(reversed(sorted(m.attributes)))),
            lambda m: m.attributes.pop('line_types'),
            lambda m: m.attributes['line_types'].update(opengl_name='wrong'),
            lambda m: m.attributes['line_types'].update(value=np.array([1.5, 1], np.float32)),
            lambda m: m.attributes['extruders'].update(value=np.array([16, 0], np.float32)),
            lambda m: m.attributes['line_dimensions'].update(value=np.full((2, 2), np.nan, np.float32)),
            lambda m: m.attributes['line_types'].update(opengl_type='vector3f'),
            lambda m: m.attributes['colors'].update(value=np.ones(3, np.float32)),
        ]
        for mutate in mutations:
            mesh = Mesh(); mutate(mesh)
            with self.subTest(mutate=mutate): self.assertIsNone(mesh_layout(mesh))


    def test_allocation_failures_destroy_every_owned_resource_and_restore_host_bindings(self):
        for failure in ('vao', 'line-create', 'line-bind', 'template-create', 'texture', 'size'):
            with self.subTest(failure=failure):
                self.helper.close()
                actual_vao = self.qt['PyQt6.QtOpenGL'].QOpenGLVertexArrayObject
                actual_buffer = self.qt['PyQt6.QtOpenGL'].QOpenGLBuffer
                def vao_factory(actual_vao=actual_vao, failure=failure):
                    value = actual_vao()
                    if failure == 'vao': value.create.return_value = False
                    return value
                sequence = []
                def buffer_factory(kind=0x8892, actual_buffer=actual_buffer, sequence=sequence, failure=failure):
                    value = actual_buffer(kind); sequence.append(value)
                    if failure == 'line-create' and len(sequence) == 1: value.create.return_value = False
                    if failure == 'line-bind' and len(sequence) == 1: value.bind = Mock(return_value=False)
                    if failure == 'template-create' and len(sequence) == 2: value.create.return_value = False
                    if failure == 'size': value.size = Mock(return_value=1)
                    return value
                buffer_factory.Type = actual_buffer.Type
                self.qt['PyQt6.QtOpenGL'].QOpenGLVertexArrayObject = vao_factory
                self.qt['PyQt6.QtOpenGL'].QOpenGLBuffer = buffer_factory
                if failure == 'texture': self.procs[b'glGenTextures'] = lambda count, textures: None
                with self.assertRaises(RuntimeError): self.render()
                self.assert_restored()
                for value in sequence: value.destroy.assert_called_once()
                self.qt['PyQt6.QtOpenGL'].QOpenGLVertexArrayObject = actual_vao
                self.qt['PyQt6.QtOpenGL'].QOpenGLBuffer = actual_buffer

    def test_missing_vbo_and_shader_compilation_fail_before_drawing_and_restore_state(self):
        for failure in ('missing', 'id', 'bind', 'compile'):
            with self.subTest(failure=failure):
                self.helper.close()
                if failure == 'missing': self.create_vertex.return_value = None
                elif failure == 'id': self.vertex.bufferId = Mock(return_value=0)
                elif failure == 'bind': self.vertex.bind = Mock(return_value=False)
                else:
                    actual = self.qt['UM.View.GL.ShaderProgram'].ShaderProgram
                    def shader_factory(actual=actual):
                        value = actual(); value.setVertexShader.return_value = False; return value
                    self.qt['UM.View.GL.ShaderProgram'].ShaderProgram = shader_factory
                with self.assertRaises(RuntimeError): self.render()
                self.assert_restored()
                self.create_vertex.return_value = self.vertex
                self.vertex.bufferId = lambda: self.vertex.id
                self.vertex.bind = lambda: (self.gl.glBindBuffer(0x8892, self.vertex.id) or True)

    def test_preexisting_host_error_declines_and_owned_gl_error_requests_full_fallback(self):
        self.gl.glGetError.return_value = 0x502
        self.assertFalse(self.render())
        self.create_vertex.assert_not_called()
        self.gl.glGetError.side_effect = [0, 0x505]
        with self.assertRaises(RuntimeError): self.render()
        self.assert_restored()
        self.assertIsNone(self.helper._storage)

    def test_cull_disabled_declines_so_normal_current_and_fractional_paths_are_never_instanced(self):
        self.gl.glIsEnabled.return_value = False
        self.assertFalse(self.render())
        self.create_vertex.assert_not_called()

    def test_front_culling_and_clockwise_winding_decline_without_geometry_changes(self):
        self.gl.cull = 0x0404
        self.assertFalse(self.render())
        self.gl.cull, self.gl.front = 0x0405, 0x0900
        self.assertFalse(self.render())
        self.create_vertex.assert_not_called()


if __name__ == '__main__': unittest.main()
