"""Live correction ownership and invalidation contracts without printer access."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from mpf.toolhead import ToolheadEnvironmentFrame as module


class EnvironmentFrameTests(unittest.TestCase):
    def setUp(self):
        self.owner=module.EnvironmentFrame(Mock(),Mock(),object())
        self.owner._current=Mock()
        self.addCleanup(module._retiring.discard,self.owner)

    def test_uncertain_constructor_allocation_stays_in_parent_receipt(self):
        orphan=NS(retained_bytes=123)
        with self.assertRaises(module.GeometryUncertain):
            self.owner._failed(module.GeometryUncertain(orphan,'allocation failed'))
        self.assertTrue(self.owner.quarantined)
        self.assertIn(self.owner,module._retiring)
        self.assertEqual(self.owner.retained_bytes,123)
        self.assertIs(self.owner.orphans[0],orphan)

    def test_view_change_drains_before_reusing_a_complete_image(self):
        self.owner.key=('old',);self.owner.frame=Mock(identity='old pixels')
        self.owner._drain=Mock(side_effect=lambda:setattr(self.owner,'frame',None))
        self.assertIsNone(self.owner.step(('new',)))
        self.owner._drain.assert_called_once()
        self.assertEqual(self.owner.key,('new',))

    def test_source_change_refuses_completed_draw(self):
        self.owner.frame=Mock(identity='complete');self.owner._valid=lambda:False
        self.assertFalse(self.owner.draw())
        self.owner.frame.draw.assert_not_called()

    def test_matching_complete_draw_uses_frozen_plan_and_read_scope(self):
        from contextlib import nullcontext
        self.owner.frame=Mock(identity='complete');self.owner._valid=lambda:True
        batch=Mock();self.owner.static=NS(plan=(batch,))
        self.owner.programs=NS(read=lambda:nullcontext())
        self.assertTrue(self.owner.draw())
        batch.draw.assert_called_once_with(self.owner.node,self.owner.values,
            self.owner.mapping,layer_draw=self.owner.frame.draw.return_value)

    def test_retirement_failure_keeps_source_hold(self):
        self.owner.frame=Mock();self.owner.frame.close.side_effect=RuntimeError('reader pending')
        source=Mock();token=object();self.owner.hold=source,token
        with self.assertRaises(RuntimeError):self.owner._drain()
        self.assertIsNotNone(self.owner.frame)
        source.release_recovery.assert_not_called()


class EnvironmentFrameLifecycleTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.context=object();self.gl=Mock();self.gl.glGetError.return_value=0
        self.source=Mock(recovery_ready=True)
        self.node=Mock(_environment=self.source,_lighting_enabled=True,_reflections_enabled=True,
                       _translucent_mesh=None,_static_transparent=(),_rotor_meshes={})
        self.node._ordinary_retained_graphics.return_value=123
        self.node.getMeshData.return_value=None
        self.owner=module.EnvironmentFrame(self.node,self.gl,self.context)
        self.owner.key=('view',)
        self.current=patch('PyQt6.QtGui.QOpenGLContext.currentContext',return_value=self.context)
        self.current.start();self.addCleanup(self.current.stop)
        self.addCleanup(module._retiring.discard,self.owner)
        self.patch=patch
        scope=patch.dict('sys.modules',{'UM.View.GL.OpenGL':NS(OpenGL=NS(VertexBufferProperty='vertex',IndexBufferProperty='index'))})
        scope.start();self.addCleanup(scope.stop)

    def test_prepare_retains_complete_request_and_exact_source_until_drain(self):
        from contextlib import ExitStack
        output=object.__new__(module.ToolheadSampleTarget)
        output._fbo=Mock();output._fbo.width.return_value=19;output._fbo.height.return_value=13
        output.positions=((.375,.125),(.875,.375),(.125,.625),(.625,.875))
        mapping=Mock(owner=self.source,source_key=('source',))
        mapping.result.frame.retained_source_bytes.return_value=100
        mapping.result.frame.capture_storage_bytes.return_value=200
        mapping.result.geometry.retained_bytes=300
        mapping.result.descriptor.far=25.
        mapping.current.return_value=True
        programs=Mock(retained_bytes=65536)
        programs.programs={('lookup',False):object(),('lookup',True):object()}
        depth=Mock(retained_bytes=65536)
        batch=Mock(opaque=NS(mesh=object(),count=3),translucent=None)
        static=Mock(retained_bytes=65536,plan=(batch,),meshes=())
        frame=Mock(identity=None,failure='')
        with ExitStack() as stack:
            for target,name,result in ((module.ReceiverMap,'freeze',mapping),(module,'ReceiverPrograms',programs),
                    (module.ReceiverValues,'freeze',object()),(module.ReceiverStatic,'freeze',static),
                    (module,'ReceiverDepth',depth),(module,'LayerFrame',frame)):
                stack.enter_context(self.patch.object(target,name,return_value=result))
            self.owner.prepare(object(),output,('view',),True,None,None)
        request=frame.select.call_args.args[0]
        self.assertEqual((request.width,request.height,request.samples),(19,13,4))
        self.assertEqual(request.positions,output.positions)
        self.assertEqual(request.lease_bytes,3*65536)
        self.assertEqual(request.poses[0].draws,((0,3,True),))
        self.assertTrue(request.verify())
        self.assertEqual(self.owner.hold,(self.source,self.source.retain_recovery.return_value))
        pose=request.poses[0];pose.seed('target');pose.draw('stage','target');pose.query('source','textures')
        programs.seed.assert_called_once_with('target',depth,static.plan,self.owner.values,mapping)
        programs.query.assert_called_once_with('source','textures',25.)
        frame.step.return_value=False
        with self.patch.object(module.time,'monotonic',side_effect=[0,1]):
            self.assertIsNone(self.owner.step(('view',)))
        self.source._window.update.assert_called()
        frame.identity=('complete',);frame.step.return_value=True
        self.assertEqual(self.owner.step(('view',)),('complete',))
        static.batch_for.return_value=batch
        programs.read.return_value=__import__('contextlib').nullcontext()
        self.assertTrue(self.owner.draw(batch.opaque.mesh))
        frame.draw.return_value=None
        self.assertFalse(self.owner.draw())
        frame.draw.return_value=Mock();static.batch_for.return_value=None
        self.assertFalse(self.owner.draw(object()))
        self.assertIn('geometry correction',self.owner.status)
        self.owner.close()
        self.assertTrue(self.owner.closed)
        frame.close.assert_called_once();depth.close.assert_called_once();programs.close.assert_called_once()
        self.source.release_recovery.assert_called_once()
        self.owner.close()

    def test_invalid_sources_and_failed_steps_do_not_publish(self):
        self.owner.frame=Mock(identity=None,failure='GPU failed')
        self.owner.frame.step.return_value=False
        self.owner.mapping=Mock(owner=self.source)
        self.owner.static=NS(meshes=())
        self.owner._drain=Mock()
        self.assertIsNone(self.owner.step(('view',)))
        self.assertIn('GPU failed',self.owner.failure)
        self.assertIn('unavailable',self.owner.status)
        self.owner.mapping.current.return_value=False
        self.assertIsNone(self.owner.step(('view',)))
        self.assertIn('source changed',self.owner.failure)
        self.owner.closing=True
        self.assertIsNone(self.owner.step(('another',)))
        self.owner.closing=False;self.owner._drain.side_effect=RuntimeError('drain')
        with self.assertRaises(module.GeometryUncertain):self.owner.step(('another',))
        self.assertTrue(self.owner.quarantined)

    def test_prepare_admission_and_constructor_failure_release_source(self):
        self.owner.prepare(None,None,('wrong',),True,None,None)
        self.assertEqual(self.owner.failure,'')
        self.owner.prepare(None,None,('view',),False,None,None)
        self.assertIn('four-sample',self.owner.failure)
        self.owner.failed_key=None
        output=object.__new__(module.ToolheadSampleTarget)
        self.source.recovery_ready=False
        self.owner.prepare(None,output,('view',),True,None,None)
        self.assertIn('Waiting',self.owner.failure)
        self.source.recovery_ready=True
        with self.patch.object(module.ReceiverMap,'freeze',return_value=None):
            self.owner.prepare(None,output,('view',),True,None,None)
        with self.patch.object(module.ReceiverMap,'freeze',side_effect=RuntimeError('source refused')):
            self.owner.prepare(None,output,('view',),True,None,None)
        self.assertEqual(self.owner.failure,'source refused')
        self.assertIsNone(self.owner.hold)

    def test_context_and_buffer_retirement_fail_closed(self):
        self.owner.closed=True
        with self.assertRaisesRegex(RuntimeError,'original context'):self.owner._current()
        self.owner.closed=False
        self.assertIn('preparing',self.owner.status)
        buffer=Mock();buffer.isCreated.return_value=False
        mesh=NS(vertex=buffer,index=None)
        self.owner.static=NS(meshes=(NS(mesh=mesh),),retained_bytes=7)
        self.owner.programs=Mock(retained_bytes=11)
        self.assertEqual(self.owner.retained_bytes,18)
        with self.patch.dict('sys.modules',{'UM.View.GL.OpenGL':NS(OpenGL=NS(VertexBufferProperty='vertex',IndexBufferProperty='index'))}):
            self.owner._drain()
        buffer.destroy.assert_called_once();self.assertFalse(hasattr(mesh,'vertex'))
        self.gl.glGetError.return_value=1282
        with self.assertRaisesRegex(RuntimeError,'drain failed'):self.owner._drain()
        with self.assertRaises(module.GeometryUncertain):self.owner.close()
        self.assertIn(self.owner,module._retiring)

    def test_off_context_close_schedules_original_context_and_keeps_failed_graph(self):
        with self.patch('PyQt6.QtGui.QOpenGLContext.currentContext',return_value=None):
            self.owner.mapping=NS(owner=self.source)
            self.owner.close();self.owner.close()
            self.source._window.scheduleRenderJob.assert_called_once()
            job=self.source._window.scheduleRenderJob.call_args.args[0]
            job.run()
            self.assertTrue(self.owner.quarantined)
        self.owner.quarantined=False
        job.run()
        self.assertTrue(self.owner.closed)
        other=module.EnvironmentFrame(self.node,self.gl,self.context)
        self.addCleanup(module._retiring.discard,other)
        with self.patch('PyQt6.QtGui.QOpenGLContext.currentContext',return_value=None):
            other.close()
        self.assertTrue(other.quarantined)
