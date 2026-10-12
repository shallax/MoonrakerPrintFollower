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

    def test_opt_in_exact_context_rejects_foreign_entry_and_stops_restore_on_switch(self):
        context = self.context(4); current = [None]
        with patch('PyQt6.QtGui.QOpenGLContext.currentContext', side_effect=lambda: current[0]):
            with self.assertRaisesRegex(RuntimeError, 'creating context'):
                with module.preserved_state(self.gl, context, exact_context=True): self.fail('Foreign entry')
            self.gl.glGetIntegerv.assert_not_called()
            current[0] = context
            with self.assertRaisesRegex(RuntimeError, 'creating context'):
                with module.preserved_state(self.gl, context, exact_context=True): current[0] = None
            self.procedures['glBindFramebuffer'].assert_not_called()
            current[0] = context
            def replace(*_args): current[0] = None; raise RuntimeError('Replacement during restore')
            self.procedures['glBindFramebuffer'].side_effect = replace
            with self.assertRaisesRegex(RuntimeError, 'creating context'):
                with module.preserved_state(self.gl, context, exact_context=True): pass
            self.procedures['glUseProgram'].assert_not_called()

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

    def test_procedure_cache_is_context_generation_owned_and_does_not_retain_owner(self):
        from PyQt6.QtCore import pyqtSignal
        import gc, weakref
        self.scope.stop()
        called=[]
        function=ctypes.CFUNCTYPE(None,ctypes.c_uint)(called.append)
        class Context(QObject):
            aboutToBeDestroyed=pyqtSignal()
            def __init__(self):
                super().__init__();self.lookups=0
            def getProcAddress(self,name):
                self.lookups+=1
                return ctypes.cast(function,ctypes.c_void_p).value
        context=Context();other=Context()
        first=module.procedure(context,'test',None,ctypes.c_uint)
        self.assertIs(module.procedure(context,'test',None,ctypes.c_uint),first)
        self.assertEqual(context.lookups,1)
        self.assertIsNot(module.procedure(other,'test',None,ctypes.c_uint),first)
        first(9);self.assertEqual(called,[9])
        context.aboutToBeDestroyed.emit()
        self.assertIsNot(module.procedure(context,'test',None,ctypes.c_uint),first)
        self.assertEqual(context.lookups,2)
        reference=weakref.ref(context)
        del context;gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(len(module._procedures),1)
        other.aboutToBeDestroyed.emit()
        self.assertEqual(len(module._procedures),0)


    def sample_state(self):
        self.gl.glGetError.return_value = 0
        self.active = 0x84C5
        self.bindings = {0: 71, 1: 72}
        self.masks = [0x89abcdef, 0x76543210]
        self.flags = {0x809D: True, 0x8E51: True, 0x80A0: False,
                      0x809E: False, 0x809F: True, 0x8C36: True}
        self.gl.glActiveTexture.side_effect = lambda value: setattr(self, 'active', value)
        self.gl.glGetIntegerv.side_effect = lambda key: (2 if key == 0x8E59 else
            self.active if key == 0x84E0 else self.bindings[self.active-0x84C0])
        self.gl.glIsEnabled.side_effect = self.flags.__getitem__
        self.gl.glEnable.side_effect = lambda key: self.flags.__setitem__(key, True)
        self.gl.glDisable.side_effect = lambda key: self.flags.__setitem__(key, False)
        self.gl.glBindTexture.side_effect = lambda kind, value: self.bindings.__setitem__(self.active-0x84C0, value)
        def integer(key, index, pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = self.masks[index]
        def mask(index, value): self.masks[index] = value
        self.procedures['glGetIntegeri_v'] = Mock(side_effect=integer)
        self.procedures['glSampleMaski'] = Mock(side_effect=mask)

    def test_sample_guard_restores_every_word_flag_binding_and_active_unit(self):
        self.sample_state()
        original_flags = self.flags.copy()
        with module.preserved_samples(self.gl, self.context(4)) as mask:
            mask(0, 1); mask(1, 0)
            for key in self.flags: self.flags[key] = not self.flags[key]
            self.gl.glActiveTexture(0x84C0); self.gl.glBindTexture(0x9100, 99)
        self.assertEqual(self.masks, [0x89abcdef, 0x76543210])
        self.assertEqual(self.flags, original_flags)
        self.assertEqual(self.bindings, {0: 71, 1: 72}); self.assertEqual(self.active, 0x84C5)

    def test_exact_sample_guard_stops_before_foreign_capture_or_restore_binding(self):
        for phase in ('capture', 'restore'):
            self.sample_state(); context=self.context(4); current=[context]; restoring=[False]
            def select(value,phase=phase,current=current,restoring=restoring):
                self.active=value
                if (phase=='capture' and value==0x84C0) or restoring[0]: current[0]=None
            self.gl.glActiveTexture.side_effect=select
            self.gl.glBindTexture.reset_mock()
            self.gl.glGetIntegerv.reset_mock()
            with patch('PyQt6.QtGui.QOpenGLContext.currentContext',side_effect=lambda current=current:current[0]):
                with self.assertRaisesRegex(RuntimeError,'creating context'):
                    with module.preserved_samples(self.gl,context,exact_context=True): restoring[0]=True
            self.gl.glBindTexture.assert_not_called()
            if phase=='capture':
                self.assertNotIn(0x9104,[call.args[0] for call in self.gl.glGetIntegerv.call_args_list])
                self.assertEqual(self.gl.glActiveTexture.call_args_list[-1].args,(0x84C0,))

    def test_sample_cleanup_fault_still_restores_other_words_flags_and_textures(self):
        self.sample_state()
        original = self.procedures['glSampleMaski'].side_effect
        def fault(index, value):
            if index == 0: raise RuntimeError('word fault')
            original(index, value)
        with self.assertRaisesRegex(RuntimeError, 'sample state could not be restored'):
            with module.preserved_samples(self.gl, self.context(4)):
                self.masks[:] = [1, 0]
                self.procedures['glSampleMaski'].side_effect = fault
                self.bindings[0] = 99
        self.assertEqual(self.masks[1], 0x76543210)
        self.assertEqual(self.bindings, {0: 71, 1: 72}); self.assertEqual(self.active, 0x84C5)
        self.gl.glEnable.assert_any_call(0x8C36)
        self.sample_state()
        self.gl.glBindTexture.side_effect = RuntimeError('texture restore fault')
        with self.assertRaisesRegex(RuntimeError, 'sample state could not be restored'):
            with module.preserved_samples(self.gl, self.context(4)): self.masks[:] = [0, 0]
        self.assertEqual(self.masks, [0x89abcdef, 0x76543210]); self.assertEqual(self.active, 0x84C5)

    def test_sample_capture_rejects_legacy_invalid_storage_or_driver_error(self):
        with self.assertRaisesRegex(RuntimeError, 'core4'):
            with module.preserved_samples(self.gl, self.context(2)): self.fail('entered')
        self.assertFalse(self.procedures)
        with patch.object(module,'procedure',side_effect=RuntimeError('missing glSampleMaski')):
            with self.assertRaises(module.SampleStateUnavailable):
                with module.preserved_samples(self.gl,self.context(4)):self.fail('entered')
        self.sample_state()
        original = self.gl.glGetIntegerv.side_effect
        for words in (0, 65):
            self.gl.glGetIntegerv.side_effect = lambda key, words=words: words if key == 0x8E59 else original(key)
            with self.assertRaisesRegex(RuntimeError, 'storage'):
                with module.preserved_samples(self.gl, self.context(4)): self.fail('entered')
        self.gl.glGetIntegerv.side_effect = original
        self.gl.glGetError.return_value = 0x0502
        with self.assertRaisesRegex(RuntimeError, 'capture failed'):
            with module.preserved_samples(self.gl, self.context(4)): self.fail('entered')
        self.assertEqual(self.active, 0x84C5)
        self.assertEqual(self.masks, [0x89abcdef, 0x76543210])
