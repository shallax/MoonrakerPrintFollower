"""Public Qt/GL contract doubles; these tests do not certify native GPU pixels."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np


class Size:
    def __init__(self, width, height):
        self.width, self.height = width, height

    def __eq__(self, other):
        return isinstance(other, Size) and (self.width, self.height) == (other.width, other.height)


class Matrix:
    def __init__(self, *values):
        self.values = values


class QuickItem:
    class Renderer:
        pass

    def __init__(self, owner):
        self.parent = owner

    def setZ(self, value): self.z = value
    def setMirrorVertically(self, value): self.mirror = value
    def setTextureFollowsItemSize(self, value): self.follows_size = value


class PreviewGLContracts(unittest.TestCase):
    def setUp(self):
        self.gl = Mock()
        self.viewport = (8, 12, 480, 400)
        self.gl.glGetIntegerv.side_effect = lambda name: 71 if name == 0x8CA6 else self.viewport
        self.context = Mock()
        self.context.format.return_value.profile.return_value = 1
        self.context.getProcAddress.return_value = 101
        self.current_context = Mock(return_value=self.context)
        self.functions = Mock(return_value=self.gl)
        self.program = Mock()
        self.program.log.return_value = 'shader diagnostic'
        self.vao, self.buffer, self.blitter = Mock(), Mock(), Mock()
        self.program_factory = Mock(return_value=self.program)
        self.vao_factory = Mock(return_value=self.vao)
        self.buffer_factory = Mock(return_value=self.buffer)
        self.buffer_factory.UsagePattern = SimpleNamespace(StaticDraw=1)
        self.blitter_factory = Mock(return_value=self.blitter)
        self.blitter_factory.Origin = SimpleNamespace(OriginBottomLeft=0)
        self.fbos = []

        def fbo(size, format_):
            result = Mock()
            result.size.return_value = size
            result.texture.return_value = 83
            self.fbos.append(result)
            return result

        self.fbo_factory = Mock(side_effect=fbo)
        self.fbo_factory.Attachment = SimpleNamespace(CombinedDepthStencil=1)
        self.format_factory = Mock(side_effect=Mock)
        self.profile_factory = Mock(side_effect=Mock)
        modules = {
            'PyQt6.QtCore': SimpleNamespace(QSize=Size, pyqtSignal=lambda *args: Mock()),
            'PyQt6.QtGui': SimpleNamespace(QMatrix4x4=Matrix,
                QOpenGLContext=SimpleNamespace(currentContext=self.current_context),
                QSurfaceFormat=SimpleNamespace(OpenGLContextProfile=SimpleNamespace(CoreProfile=1)),
                QVector3D=lambda *args: tuple(args), QVector4D=lambda *args: tuple(args)),
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLBuffer=self.buffer_factory,
                QOpenGLFramebufferObject=self.fbo_factory, QOpenGLFramebufferObjectFormat=self.format_factory,
                QOpenGLShader=SimpleNamespace(ShaderTypeBit=SimpleNamespace(Vertex=1, Fragment=2)),
                QOpenGLShaderProgram=self.program_factory,
                QOpenGLVersionFunctionsFactory=SimpleNamespace(get=self.functions),
                QOpenGLVersionProfile=self.profile_factory,
                QOpenGLVertexArrayObject=self.vao_factory, QOpenGLTextureBlitter=self.blitter_factory),
            'PyQt6.QtQuick': SimpleNamespace(QQuickFramebufferObject=QuickItem),
        }
        self.module_name = 'mpf.toolhead._preview_gl_contract'
        source = Path(__file__).resolve().parents[1] / 'mpf/toolhead/ToolheadPreviewGL.py'
        spec = importlib.util.spec_from_file_location(self.module_name, source)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            sys.modules[self.module_name] = self.module
            spec.loader.exec_module(self.module)
        self.addCleanup(sys.modules.pop, self.module_name, None)
        self.bind_framebuffer = Mock()
        self.proc_constructor = Mock(return_value=self.bind_framebuffer)
        self.proc_type = patch.object(self.module.ctypes, 'CFUNCTYPE', return_value=self.proc_constructor)
        self.proc_type.start()
        self.addCleanup(self.proc_type.stop)
        self.window = Mock()
        self.failure = Mock()
        self.light = dict(position=[1, 2, 3], direction=[0, 0, 1], colour='#804020',
                          brightness=2, range=30, surface=7, paint=True)
        self.owner = SimpleNamespace(_packed=(b'packed mesh bytes', None, 3., np.int64(6)),
            _camera=(np.array([1., 2., 3.]), np.eye(3), 40.), _pan=(12., -7.),
            _model=SimpleNamespace(lights=[self.light]), width=lambda: 240., height=lambda: 200.)
        self.item = SimpleNamespace(owner=self.owner, window=lambda: self.window,
                                    failed=SimpleNamespace(emit=self.failure))
        self.renderer = self.module.PreviewRenderer()
        self.renderer.synchronize(self.item)

    def uniforms(self):
        return {entry.args[0]: entry.args[1] for entry in self.program.setUniformValue.call_args_list}

    def assert_finished(self, failed=False):
        self.window.beginExternalCommands.assert_called_once()
        self.window.endExternalCommands.assert_called_once()
        self.assertEqual(self.renderer._failed, failed)
        self.assertEqual(self.failure.call_count, int(failed))

    def test_item_keeps_renderer_wrapper_and_native_texture_configuration(self):
        item = self.module.ToolheadPreviewGL(self.owner)
        self.assertIs(item.parent, self.owner)
        self.assertIs(item.owner, self.owner)
        self.assertEqual((item.z, item.mirror, item.follows_size), (-1, True, True))
        renderer = item.createRenderer()
        self.assertIs(renderer, item._renderer)
        self.assertIsInstance(renderer, self.module.PreviewRenderer)

    def test_synchronize_retains_packed_bytes_and_snapshots_mutable_lights(self):
        self.assertIs(self.renderer._packed, self.owner._packed)
        self.assertIs(self.renderer._camera, self.owner._camera)
        self.assertEqual(self.renderer._pan, (12., -7.))
        self.light['position'][0] = 99
        self.owner._model.lights.clear()
        self.assertEqual(self.renderer._lights[0]['position'], [1, 2, 3])
        for model in (None, SimpleNamespace()):
            self.owner._model = model
            self.renderer.synchronize(self.item)
            self.assertEqual(self.renderer._lights, [])

    def test_core_shader_initialization_and_physical_viewport(self):
        self.renderer.render()
        self.assert_finished()
        profile = self.functions.call_args.args[0]
        profile.setVersion.assert_called_once_with(4, 1)
        profile.setProfile.assert_called_once_with(1)
        sources = self.program.addShaderFromSourceCode.call_args_list
        self.assertEqual([entry.args[0] for entry in sources], [1, 2])
        self.assertTrue(all('#version 410' in entry.args[1] for entry in sources))
        self.buffer.setUsagePattern.assert_called_once_with(1)
        self.assertEqual(self.fbos[0].size(), Size(480, 400))
        self.gl.glViewport.assert_has_calls([call(0, 0, 480, 400), call(8, 12, 480, 400)])

    def test_compatibility_shader_and_ext_framebuffer_entry_point(self):
        self.context.format.return_value.profile.return_value = 0
        self.context.getProcAddress.side_effect = [None, 102]
        self.renderer.render()
        self.assert_finished()
        self.functions.call_args.args[0].setVersion.assert_called_once_with(2, 0)
        self.assertTrue(all('#version 410' not in entry.args[1]
                            for entry in self.program.addShaderFromSourceCode.call_args_list))
        self.context.getProcAddress.assert_has_calls([call(b'glBindFramebuffer'), call(b'glBindFramebufferEXT')])
        self.proc_constructor.assert_called_once_with(102)

    def test_mesh_upload_is_retained_for_orbit_pan_and_lighting_only_redraws(self):
        self.renderer.render()
        packed = self.owner._packed
        self.owner._camera = (np.zeros(3), np.eye(3), 60.)
        self.owner._pan = (20., 8.)
        self.renderer.synchronize(self.item)
        self.renderer.render()
        self.assertIs(self.renderer._uploaded, packed)
        self.buffer.allocate.assert_called_once_with(packed[0], len(packed[0]))
        self.owner._packed = (b'new mesh', None, 4., 3)
        self.renderer.synchronize(self.item)
        self.renderer.render()
        self.assertEqual(self.buffer.allocate.call_count, 2)
        self.assertEqual(self.fbo_factory.call_count, 1)
        self.viewport = (0, 0, 960, 800)
        self.renderer.render()
        self.assertEqual(self.fbo_factory.call_count, 2)
        self.assertEqual(self.buffer.allocate.call_count, 2)

    def test_light_and_camera_uniforms_keep_source_coordinates_and_paint_values(self):
        self.renderer.render()
        values = self.uniforms()
        self.assertEqual(values['u_attachedCount'], 1)
        self.assertEqual(values['u_attachedPosition[0]'], (1., 2., 3.15))
        self.assertEqual(values['u_attachedDirection[0]'], (0, 0, 1))
        np.testing.assert_allclose(values['u_attachedColour[0]'], np.array([128, 64, 32]) / 255 * 2)
        self.assertEqual(values['u_attachedRange[0]'], 30.)
        self.assertEqual(values['u_attachedSurface[0]'], 7.)
        np.testing.assert_allclose(values['u_attachedPaint[0]'], [128/255, 64/255, 32/255, 1])
        self.assertEqual(values['u_viewPosition'], (1., 2., 15.))
        self.assertEqual(len(values['u_projectionMatrix'].values), 16)
        np.testing.assert_allclose(values['u_projectionMatrix'].values,
            [1/3, 0, 0, -7/30, 0, -.4, 0, .87, 0, 0, -1/6, .5, 0, 0, 0, 1])
        self.assertTrue(all('u_light'+str(index) in values and 'u_direction'+str(index) in values for index in range(4)))

    def test_draws_depth_then_premultiplied_colour_and_restores_qt_state(self):
        self.renderer.render()
        self.assert_finished()
        self.program.setAttributeBuffer.assert_has_calls([
            call('a_vertex', 0x1406, 0, 3, 44), call('a_normal', 0x1406, 12, 3, 44),
            call('a_color', 0x1406, 24, 4, 44), call('a_surface', 0x1406, 40, 1, 44)])
        self.program.setUniformValue.assert_has_calls([call('u_depthOnly', 1), call('u_depthOnly', 0)])
        self.gl.glDrawArrays.assert_has_calls([call(4, 0, 6), call(4, 0, 6)])
        self.assertTrue(all(type(entry.args[2]) is int for entry in self.gl.glDrawArrays.call_args_list))
        self.gl.glBlendFuncSeparate.assert_called_once_with(0x0302, 0x0303, 1, 0x0303)
        self.gl.glDepthFunc.assert_has_calls([call(0x0201), call(0x0203), call(0x0201)])
        self.gl.glColorMask.assert_called_with(True, True, True, True)
        self.gl.glDepthMask.assert_called_with(True)
        self.gl.glDisable.assert_has_calls([call(0x0B71), call(0x0BE2)])
        for resource in (self.program, self.buffer, self.vao, self.blitter): resource.release.assert_called_once()
        self.bind_framebuffer.assert_called_once_with(0x8D40, 71)
        self.assertEqual(self.blitter.blit.call_args.args[0], 83)
        self.assertEqual(self.blitter.blit.call_args.args[2], 0)

    def test_context_replacement_recreates_resources_and_reuploads_mesh(self):
        self.renderer.render()
        old_fbo = self.renderer._depth_fbo
        old_vao, old_buffer = self.vao, self.buffer
        replacement_program, replacement_vao, replacement_buffer, replacement_blitter = Mock(), Mock(), Mock(), Mock()
        self.program_factory.return_value = replacement_program
        self.vao_factory.return_value = replacement_vao
        self.buffer_factory.return_value = replacement_buffer
        self.blitter_factory.return_value = replacement_blitter
        replacement = Mock()
        replacement.format.return_value.profile.return_value = 1
        replacement.getProcAddress.return_value = 201
        self.current_context.return_value = replacement
        self.renderer.render()
        self.assertIs(self.renderer._context, replacement)
        self.assertIsNot(self.renderer._depth_fbo, old_fbo)
        self.assertEqual(self.functions.call_count, 2)
        self.assertEqual(self.program_factory.call_count, 2)
        self.assertEqual(self.vao_factory.call_count, 2)
        old_buffer.allocate.assert_called_once()
        replacement_buffer.allocate.assert_called_once_with(self.owner._packed[0], len(self.owner._packed[0]))
        old_vao.bind.assert_called_once()
        old_buffer.bind.assert_called_once()
        old_vao.destroy.assert_not_called()
        old_buffer.destroy.assert_not_called()
        replacement_vao.bind.assert_called_once()
        self.assertIs(self.functions.call_args.args[1], replacement)
        self.assertEqual(self.failure.call_count, 0)

    def test_missing_window_and_already_failed_renderer_do_not_touch_gl(self):
        self.renderer._window = None
        self.renderer.render()
        self.renderer._window = self.window
        self.renderer._failed = True
        self.renderer.render()
        self.window.beginExternalCommands.assert_not_called()
        self.functions.assert_not_called()

    def test_empty_model_or_logical_size_copies_only_clear_texture(self):
        for missing in ('_packed', '_camera', '_width', '_height'):
            with self.subTest(missing=missing):
                self.renderer._failed = False
                self.renderer.synchronize(self.item)
                setattr(self.renderer, missing, 0 if missing in ('_width', '_height') else None)
                self.gl.reset_mock()
                self.window.reset_mock()
                self.blitter.reset_mock()
                self.renderer.render()
                self.assert_finished()
                self.gl.glDrawArrays.assert_not_called()
                self.blitter.blit.assert_called_once()

    def test_zero_physical_viewport_balances_commands_without_allocating_fbo(self):
        self.viewport = (0, 0, 0, 400)
        self.renderer.render()
        self.assert_finished()
        self.fbo_factory.assert_not_called()
        self.blitter.blit.assert_not_called()

    def test_initialization_errors_are_reported_once_and_external_commands_end(self):
        cases = (
            (self.current_context, None, 'No current'),
            (self.functions, None, 'functions unavailable'),
            (self.gl.initializeOpenGLFunctions, False, 'functions could not initialize'),
            (self.program.addShaderFromSourceCode, False, 'shader diagnostic'),
            (self.program.link, False, 'shader diagnostic'),
            (self.vao.create, False, 'VAO unavailable'),
            (self.buffer.create, False, 'vertex buffer unavailable'),
            (self.context.getProcAddress, None, 'framebuffer binding unavailable'),
            (self.blitter.create, False, 'texture copy unavailable'),
        )
        for method, result, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                self.renderer = self.module.PreviewRenderer()
                self.renderer.synchronize(self.item)
                self.window.reset_mock()
                self.failure.reset_mock()
                previous = method.return_value
                method.return_value = result
                try:
                    self.renderer.render()
                    self.assert_finished(failed=True)
                    self.assertIn(diagnostic, self.failure.call_args.args[0])
                    self.renderer.render()
                    self.assertEqual(self.failure.call_count, 1)
                    self.assertEqual(self.window.endExternalCommands.call_count, 1)
                finally:
                    method.return_value = previous

    def test_failed_fbo_or_shader_or_buffer_binding_never_presents_partial_frame(self):
        cases = ('invalid_fbo', 'fbo_bind', 'buffer_bind', 'program_bind', 'allocation', 'draw')
        for case in cases:
            with self.subTest(case=case):
                self.renderer = self.module.PreviewRenderer()
                self.renderer.synchronize(self.item)
                self.window.reset_mock()
                self.failure.reset_mock()
                self.blitter.reset_mock()
                self.buffer.bind.return_value = self.program.bind.return_value = True
                self.buffer.allocate.side_effect = self.gl.glDrawArrays.side_effect = None
                original = self.fbo_factory.side_effect
                def fbo(size, format_, case=case, original=original):
                    result = original(size, format_)
                    result.isValid.return_value = case != 'invalid_fbo'
                    result.bind.return_value = case != 'fbo_bind'
                    return result
                self.fbo_factory.side_effect = fbo
                if case == 'buffer_bind': self.buffer.bind.return_value = False
                if case == 'program_bind': self.program.bind.return_value = False
                if case == 'allocation': self.buffer.allocate.side_effect = RuntimeError('allocation failed')
                if case == 'draw': self.gl.glDrawArrays.side_effect = RuntimeError('draw failed')
                try:
                    self.renderer.render()
                    self.assert_finished(failed=True)
                    self.blitter.blit.assert_not_called()
                    self.bind_framebuffer.assert_called_with(0x8D40, 71)
                finally:
                    self.fbo_factory.side_effect = original

    def test_cleanup_errors_are_contained_and_other_releases_still_run(self):
        self.program.release.side_effect = RuntimeError('program teardown failed')
        self.buffer.release.side_effect = RuntimeError('buffer teardown failed')
        self.failure.side_effect = RuntimeError('GUI item deleted')
        self.renderer.render()
        self.assert_finished(failed=True)
        self.vao.release.assert_called_once()
        self.bind_framebuffer.assert_called_once_with(0x8D40, 71)
        self.blitter.blit.assert_not_called()

    def test_blit_and_blitter_release_failures_still_end_external_commands(self):
        self.blitter.blit.side_effect = RuntimeError('texture copy failed')
        self.blitter.release.side_effect = RuntimeError('blitter release failed')
        self.renderer.render()
        self.assert_finished(failed=True)
        self.blitter.release.assert_called_once()
        self.assertIn('texture copy failed', self.failure.call_args.args[0])

    def test_gl_state_cleanup_failure_does_not_skip_remaining_cleanup(self):
        self.gl.glColorMask.side_effect = [None, None, None, RuntimeError('mask reset failed')]
        self.renderer.render()
        self.assert_finished(failed=True)
        self.gl.glDepthMask.assert_called_with(True)
        self.gl.glDepthFunc.assert_called_with(0x0201)
        self.vao.release.assert_called_once()
        self.bind_framebuffer.assert_called_once_with(0x8D40, 71)
        self.blitter.blit.assert_not_called()

    def test_blitter_bind_failure_releases_blitter_and_balances_commands(self):
        self.blitter.bind.side_effect = RuntimeError('blitter bind failed')
        self.renderer.render()
        self.assert_finished(failed=True)
        self.blitter.blit.assert_not_called()
        self.blitter.release.assert_called_once()

    def test_begin_failure_with_existing_resources_avoids_gl_outside_boundary(self):
        self.renderer.render()
        self.window.reset_mock()
        self.gl.reset_mock()
        self.program.reset_mock()
        self.buffer.reset_mock()
        self.vao.reset_mock()
        self.window.beginExternalCommands.side_effect = RuntimeError('window no longer rendering')
        self.renderer.render()
        self.assertTrue(self.renderer._failed)
        self.window.endExternalCommands.assert_not_called()
        self.assertEqual(self.gl.mock_calls, [])
        for resource in (self.program, self.buffer, self.vao): resource.release.assert_not_called()

    def test_external_commands_failures_and_framebuffer_restore_are_contained(self):
        self.window.beginExternalCommands.side_effect = RuntimeError('begin failed')
        self.renderer.render()
        self.assertTrue(self.renderer._failed)
        self.window.endExternalCommands.assert_not_called()
        self.functions.assert_not_called()
        self.renderer._failed = False
        self.window.reset_mock(side_effect=True)
        self.failure.reset_mock()
        self.window.endExternalCommands.side_effect = RuntimeError('end failed')
        self.bind_framebuffer.side_effect = RuntimeError('restore failed')
        self.renderer.render()
        self.assert_finished(failed=True)
        self.blitter.blit.assert_not_called()
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_failure_without_a_gui_reporter_is_contained(self):
        renderer = self.module.PreviewRenderer()
        renderer._fail(RuntimeError('destroyed item'))
        renderer._fail(RuntimeError('second error'))
        self.assertTrue(renderer._failed)


if __name__ == '__main__':
    unittest.main()
