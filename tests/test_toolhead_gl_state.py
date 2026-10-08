"""Legacy state restoration and share-group retirement under cleanup faults."""
import ctypes
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PyQt6 import sip
from PyQt6.QtCore import QObject, QCoreApplication, QEvent
from PyQt6.QtQuick import QQuickWindow  # Register QtGui before replacing currentContext in CPU contracts.
from mpf.toolhead import ToolheadGLState as module


class GLStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.gl=Mock()
        self.gl.glGetIntegerv.side_effect=lambda key: (1,2,30,40) if key in (0x0BA2,0x0C10) else key
        self.gl.glGetBooleanv.side_effect=lambda key: (True,False,True,False) if key==0x0C23 else True
        self.gl.glGetFloatv.side_effect=lambda key: (.1,.2,.3,.4) if key in (0x0C22,0x8005) else .5
        self.gl.glGetDoublev.return_value=.3
        self.gl.glIsEnabled.side_effect=lambda key:key%2==0
        self.procedures={}
        self.scope=patch.object(module,'procedure',side_effect=lambda ctx,name,*types:self.procedures.setdefault(name,Mock()))
        self.scope.start();self.addCleanup(self.scope.stop)
        module._retired_textures.clear()
        self.addCleanup(module._retired_textures.clear)

    def context(self, version, extensions=()):
        return NS(format=lambda:NS(majorVersion=lambda:version),hasExtension=lambda name:name in extensions)

    def test_legacy_apple_vao_and_without_vao_restore_independent_states(self):
        for extensions, expected in (([b'GL_APPLE_vertex_array_object'],'glBindVertexArrayAPPLE'),([],None),([b'GL_ARB_vertex_array_object'],'glBindVertexArray')):
            with self.subTest(extensions=extensions):
                self.procedures.clear();self.gl.reset_mock()
                with module.preserved_state(self.gl,self.context(2,extensions)): pass
                if expected: self.procedures[expected].assert_called_once_with(0x85B5)
                else: self.assertNotIn('glBindVertexArray',self.procedures)
                self.assertNotIn(0x8DB9,[call.args[0] for call in self.gl.glIsEnabled.call_args_list])
                self.gl.glActiveTexture.assert_called_with(0x84E0)
                self.gl.glBindBuffer.assert_any_call(0x8893,0x8895)

    def test_a_failed_restore_does_not_prevent_fbo_program_texture_depth_cleanup(self):
        self.gl.glColorMask.side_effect=RuntimeError('driver fault')
        with self.assertRaisesRegex(RuntimeError,'Host graphics state'):
            with module.preserved_state(self.gl,self.context(4)): pass
        self.procedures['glBindFramebuffer'].assert_any_call(0x8CA9,0x8CA6)
        self.procedures['glUseProgram'].assert_called_with(0x8B8D)
        self.gl.glClearDepth.assert_called_with(.3)
        self.gl.glActiveTexture.assert_called_with(0x84E0)
        self.assertEqual(self.gl.glBindTexture.call_count,16)

    def test_sampler_cleanup_is_independent_of_failed_texture_restore(self):
        self.gl.glBindTexture.side_effect = RuntimeError('texture fault')
        with self.assertRaisesRegex(RuntimeError, 'Host graphics state'):
            with module.preserved_state(self.gl, self.context(4)): pass
        self.assertEqual(self.procedures['glBindSampler'].call_count, 8)
        self.procedures['glBindSampler'].assert_any_call(6, 0x8919)
        self.gl.glActiveTexture.assert_called_with(0x84E0)

    def test_missing_required_entrypoint_fails_before_render_callback(self):
        self.scope.stop()
        with self.assertRaisesRegex(RuntimeError,'glBindFramebuffer is unavailable'):
            with module.preserved_state(self.gl,NS(getProcAddress=lambda name:None)): self.fail('entered render')
        self.gl.glGetIntegerv.assert_not_called()

    def test_retirement_disconnects_each_completed_callback_and_preserves_failed_delete(self):
        group=QObject();current=[None]
        context=NS(shareGroup=lambda:group)
        gui=NS(QOpenGLContext=NS(currentContext=lambda:current[0]))
        with patch.dict('sys.modules',{'PyQt6.QtGui':gui}):
            for number in (1,2,3):
                module.retire_textures(group,[number])
                self.assertEqual(group.receivers(group.destroyed),2)  # Qt's Python slot proxy owns a second hook.
                current[0]=context;module.flush_texture_deletions()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                self.assertEqual(group.receivers(group.destroyed),0)
                current[0]=None
            module.retire_textures(group,[9])
            delete=self.procedures['glDeleteTextures'];delete.side_effect=RuntimeError('lost driver')
            current[0]=context
            with self.assertRaisesRegex(RuntimeError,'lost driver'): module.flush_texture_deletions()
            self.assertEqual(module._retired_textures[id(group)][1],{9})
            delete.side_effect=None;module.flush_texture_deletions()
            self.assertEqual(module._retired_textures,{})
        # Empty retirement never borrows a dead QObject or context.
        sip.delete(group);module.retire_textures(group,[]);module.retire_textures(group,[10])

    def test_destroyed_group_and_retired_window_cleanup_are_safe_without_current_context(self):
        group=QObject();window=Mock();window.scheduleRenderJob.side_effect=RuntimeError('window destroyed')
        with patch.dict('sys.modules',{'PyQt6.QtGui':NS(QOpenGLContext=NS(currentContext=lambda:None))}):
            module.retire_textures(group,[2,3],window)
            self.assertEqual(window.scheduleRenderJob.call_args.args[1],QQuickWindow.RenderStage.AfterRenderingStage)
            self.assertEqual(module._retired_textures[id(group)][1],{2,3})
            window.update.assert_not_called()
            sip.delete(group)
            self.assertEqual(module._retired_textures,{})

    def test_procedure_uses_actual_address_with_declared_abi(self):
        self.scope.stop()
        called=[]
        function=ctypes.CFUNCTYPE(None,ctypes.c_uint)(called.append)
        context=NS(getProcAddress=lambda name:ctypes.cast(function,ctypes.c_void_p).value)
        module.procedure(context,'test',None,ctypes.c_uint)(7)
        self.assertEqual(called,[7])
