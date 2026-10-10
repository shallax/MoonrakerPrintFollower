"""Real tiny capture/upload groups: cancellation, whole publication and drain."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentLayerFrame as frame
from tests import test_toolhead_environment_layer_capture as fixture


class _OriginalMesh:
    def __init__(self):
        self.vertices=np.zeros((6,3),np.float32); self.vertices.setflags(write=False)
    def getVertices(self): return self.vertices
    def getVertexCount(self): return len(self.vertices)
    def hasIndices(self): return False
    def hasNormals(self): return False
    def hasColors(self): return False
    def hasUVCoordinates(self): return False
    def attributeNames(self): return ()


class LayerFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.LayerCaptureTests.setUpClass()
        cls.available=fixture.LayerCaptureTests.available
        cls.gl=fixture.LayerCaptureTests.gl; cls.context=fixture.LayerCaptureTests.context

    @classmethod
    def tearDownClass(cls): fixture.LayerCaptureTests.tearDownClass()

    def setUp(self):
        if not self.available: self.skipTest('Core GL unavailable')
        self.fixture=fixture.LayerCaptureTests('test_context_retirement_during_admission_keeps_the_entire_capture_rooted')
        self.fixture.setUp(); self.fixture.prepare()
        self.owner=frame.LayerFrame(byte_budget=64*1024**2)
        self.source=('cohort',7); self.current=True
        self.mesh=_OriginalMesh()

    def tearDown(self):
        self.context.makeCurrent(self.fixture.surface); self.gl.glFinish()
        if not self.owner.quarantined: self.owner.close()
        self.fixture.tearDown()

    @contextmanager
    def scope(self): yield NS(key=self.source)

    def request(self, key='A', *, pose_count=1, **changes):
        wrapper=NS(_shader_program=self.fixture.programs['record'])
        pose=frame.LayerPose(('cohort',7),((0,2,False),),((self.mesh,0,2),),(wrapper,wrapper),
            self.fixture.seed,self.fixture.draw,self.scope,self.fixture.query)
        value=frame.LayerFrameRequest((key,'view','crop','depth','head','materials','poses','source'),
            8,8,1,None,(pose,)*pose_count,(self.mesh,self.fixture.programs),128*1024,lambda:self.current)
        return replace(value,**changes)

    def step(self, **options):
        return self.owner.step(self.gl,self.context,existing_bytes=options.get('existing_bytes',37))

    def finish(self):
        for _ in range(512):
            if self.step(): return
            self.gl.glFinish()
        self.fail('Complete original group never published')

    def test_all_shutters_publish_together_and_keep_the_exact_request(self):
        request=self.request(pose_count=3); self.owner.select(request)
        saw_partial=False
        for _ in range(512):
            complete=self.step(); self.gl.glFinish()
            if self.owner.storages:
                saw_partial=True; self.assertIsNone(self.owner.identity); self.assertIsNone(self.owner.draw(0))
            if complete: break
        else: self.fail('Shutter group did not complete')
        self.assertTrue(saw_partial); self.assertEqual(len(self.owner.front[1]),3)
        self.assertEqual(self.owner.identity[0],request.key)
        self.assertTrue(all(self.owner.draw(pose) is not None for pose in range(3)))
        self.assertIsNone(self.owner.draw(-1)); self.assertIsNone(self.owner.draw(True))
        self.assertGreater(self.owner.retained_bytes,request.lease_bytes)
        self.assertFalse(self.step()); revision=self.owner.revision
        self.current=False; self.assertIsNone(self.owner.identity)
        self.current=True; self.assertEqual(self.owner.revision,revision)
        with self.assertRaises(ValueError): self.owner.select(object())

    def test_cancelled_A_B_A_never_adopts_the_old_pending_handle(self):
        a,b=self.request('A'),self.request('B')
        self.owner.select(a); self.step(); old=self.owner.capture; epoch=self.owner.epoch
        self.owner.select(b); self.assertTrue(old.closed); self.assertGreater(self.owner.epoch,epoch)
        self.step(); middle=self.owner.capture
        self.owner.select(a); self.assertTrue(middle.closed); self.assertIsNone(self.owner.identity)
        self.finish(); self.assertNotEqual(self.owner.front[3],epoch)
        self.owner.select(b); self.step(); self.owner.select(a)
        self.assertIs(self.owner.front[0],a); self.assertIsNotNone(self.owner.identity)
        self.assertFalse(self.step())

    def test_source_replacement_during_query_withdraws_the_whole_group(self):
        request=self.request()
        def query(source,textures):
            self.fixture.query(source,textures); self.source=('cohort',8)
        request=replace(request,poses=(replace(request.poses[0],query=query),))
        self.owner.select(request)
        for _ in range(128):
            self.step(); self.gl.glFinish()
            if self.owner.selected is None: break
        else: self.fail('Replaced source was retained')
        self.assertIsNone(self.owner.identity); self.assertIsNone(self.owner.capture)
        self.assertIsNone(self.owner.pending)

    def test_selection_inside_a_draw_is_deferred_until_the_capture_unwinds(self):
        a,b=self.request('A'),self.request('B')
        def draw(stage,target):
            self.fixture.draw(stage,target); self.owner.select(b)
            self.assertFalse(target.closed)
        a=replace(a,poses=(replace(a.poses[0],draw=draw),))
        self.owner.select(a); self.step(); capture=self.owner.capture; self.gl.glFinish()
        self.step(); self.assertTrue(capture.closed)
        self.assertIs(self.owner.selected,b); self.assertIsNone(self.owner.identity)
        self.finish(); self.assertIs(self.owner.front[0],b)

    def test_seed_refusal_and_combined_old_new_budget_keep_original_fallback(self):
        a=self.request('A'); self.owner.select(a); self.finish()
        old=self.owner.front
        b=self.request('B'); b=replace(b,poses=(replace(b.poses[0],seed=lambda target:False),))
        self.owner.select(b); self.assertFalse(self.step())
        self.assertIsNone(self.owner.pending); self.assertIsNone(self.owner.identity)
        self.assertIn('withdrew',self.owner.failure)
        self.assertIs(self.owner.front,old)
        self.owner.select(self.request('C')); self.step(existing_bytes=self.owner.byte_budget)
        self.assertIsNone(self.owner.identity); self.assertIsNone(self.owner.capture)
        self.assertIn('budget',self.owner.failure)
        self.owner.select(a); self.assertIsNotNone(self.owner.identity)

    def test_context_retirement_roots_the_pending_callbacks_without_future_frames(self):
        request=self.request(); self.owner.select(request); self.step()
        capture=self.owner.capture; held=self.owner.retained_bytes
        self.owner._retirement()
        self.assertTrue(self.owner.quarantined); self.assertIn(self.owner,frame._uncertain)
        self.assertIs(self.owner.pending,request); self.assertIs(self.owner.capture,capture)
        self.assertEqual(self.owner.retained_bytes,held)
        with self.assertRaises(frame.GeometryUncertain): self.owner.close()
        with self.assertRaises(RuntimeError): self.step()
        # Fault injection did not destroy this actual context. Explicitly finish
        # and drain the test's real names; production never assumes this receipt.
        self.gl.glFinish(); self.owner.quarantined=False; self.owner.close()
        frame._uncertain.remove(self.owner)

    def test_completed_front_is_unavailable_after_context_retirement(self):
        self.owner.select(self.request()); self.finish()
        old=self.owner.front; held=self.owner.retained_bytes
        self.owner._retirement()
        self.assertIsNone(self.owner.identity); self.assertIsNone(self.owner.draw(0))
        self.assertIs(self.owner.front,old); self.assertEqual(self.owner.retained_bytes,held)
        self.gl.glFinish(); self.owner.quarantined=False; self.owner.close(); frame._uncertain.remove(self.owner)

    def test_verify_reselection_cannot_return_an_old_front(self):
        armed=[False]; b=self.request('B')
        def verify():
            if armed[0]: self.owner.select(b)
            return True
        a=self.request('A',verify=verify); self.owner.select(a); self.finish()
        armed[0]=True
        self.assertIsNone(self.owner.identity); self.assertIsNone(self.owner.draw(0))
        self.assertIs(self.owner.selected,b)

    def test_verify_retirement_cannot_report_a_ready_front_on_that_call(self):
        armed=[False]
        def verify():
            if armed[0]: self.owner._retirement()
            return True
        self.owner.select(self.request(verify=verify)); self.finish(); held=self.owner.retained_bytes
        armed[0]=True
        self.assertIsNone(self.owner.identity); self.assertIsNone(self.owner.draw(0))
        self.assertEqual(self.owner.retained_bytes,held)
        self.gl.glFinish(); self.owner.quarantined=False; self.owner.close(); frame._uncertain.remove(self.owner)

    def test_old_front_retirement_ABA_drains_the_cancelled_epoch_before_retry(self):
        old_request=self.request('old'); self.owner.select(old_request); self.finish()
        old=self.owner.front[1][0]
        a,b=self.request('A'),self.request('B'); self.owner.select(a)
        close=old.close
        def retirement():
            close(); self.owner.select(b); self.owner.select(a)
        with patch.object(old,'close',side_effect=retirement):
            for _ in range(256):
                self.step(); self.gl.glFinish()
                if old.closed: break
            else: self.fail('Old front retirement not reached')
        self.assertIs(self.owner.selected,a); self.assertIsNone(self.owner.pending)
        self.assertFalse(self.owner.storages); self.assertIsNone(self.owner.identity)
        self.finish(); self.assertIs(self.owner.front[0],a)

    def test_source_scope_exit_reselection_prevents_final_group_publication(self):
        armed=[False]; b=self.request('B')
        @contextmanager
        def scope():
            yield NS(key=self.source)
            if armed[0]: self.owner.select(b)
        a=self.request('A'); a=replace(a,poses=(replace(a.poses[0],query_scope=scope),))
        self.owner.select(a)
        for _ in range(256):
            self.step(); self.gl.glFinish()
            if self.owner.upload is not None and self.owner.upload._index==3:
                armed[0]=True; break
        else: self.fail('Ready upload not reached')
        upload=self.owner.upload
        self.assertFalse(self.step()); self.assertIs(self.owner.selected,b)
        self.assertIsNone(self.owner.identity); self.assertTrue(upload.closed)

    def test_failed_upload_constructor_remains_in_the_combined_ledger(self):
        self.owner.select(self.request()); held=[]; original=frame.LayerStorage
        def failed(*args,**kwargs):
            storage=original(*args,**kwargs); held.append(storage)
            raise frame.GeometryUncertain(storage,'allocated constructor completion uncertain')
        with patch.object(frame,'LayerStorage',side_effect=failed):
            for _ in range(256):
                try: self.step()
                except frame.GeometryUncertain: break
                self.gl.glFinish()
            else: self.fail('Allocated failure never reached')
        storage=held[0]; self.assertIn(storage,self.owner.orphans)
        self.assertTrue(storage.buffers); self.assertTrue(storage.textures)
        self.assertGreaterEqual(self.owner.retained_bytes,storage.retained_bytes+self.owner.pending.lease_bytes)
        self.gl.glFinish(); storage.close(); self.owner.orphans=[]
        self.owner.quarantined=False; self.owner.close(); frame._uncertain.remove(self.owner)

    def test_external_growth_during_upload_refuses_before_another_submission(self):
        self.owner.select(self.request())
        for _ in range(256):
            self.step(); self.gl.glFinish()
            if self.owner.upload is not None: break
        else: self.fail('Upload not reached')
        upload=self.owner.upload
        external=self.owner.byte_budget-self.owner.retained_bytes-frame.VALIDATION_SCRATCH+1
        self.assertFalse(self.step(existing_bytes=external))
        self.assertTrue(upload.closed); self.assertIsNone(self.owner.identity)
        self.assertIn('upload ledger',self.owner.failure)

    def test_earlier_shutter_source_loss_before_group_adoption_refuses_all_poses(self):
        alive=[True]
        @contextmanager
        def first_scope(): yield NS(key=self.source) if alive[0] else None
        request=self.request(pose_count=2)
        request=replace(request,poses=(replace(request.poses[0],query_scope=first_scope),request.poses[1]))
        self.owner.select(request)
        for _ in range(512):
            self.step(); self.gl.glFinish()
            if len(self.owner.storages)==1 and self.owner.upload is not None and self.owner.upload._index==3: break
        else: self.fail('Final shutter upload not reached')
        first=self.owner.storages[0]; alive[0]=False
        self.assertFalse(self.step()); self.assertIsNone(self.owner.identity)
        self.assertTrue(first.closed); self.assertFalse(self.owner.storages)

    def test_close_failure_roots_a_completed_front_until_verified_manual_fault_cleanup(self):
        self.owner.select(self.request()); self.finish(); storage=self.owner.front[1][0]
        held=self.owner.retained_bytes
        with patch.object(storage,'close',side_effect=RuntimeError('completed read still live')):
            with self.assertRaises(frame.GeometryUncertain): self.owner.close()
        self.assertIsNone(self.owner.identity); self.assertEqual(self.owner.retained_bytes,held)
        self.assertIs(self.owner.front[1][0],storage)
        self.gl.glFinish(); self.owner.quarantined=False; self.owner.close(); frame._uncertain.remove(self.owner)

    def test_faulted_drain_retains_all_source_and_receiver_leases(self):
        request=self.request(); self.owner.select(request); self.step(); capture=self.owner.capture
        with patch.object(capture,'close',side_effect=RuntimeError('completion uncertain')):
            with self.assertRaises(frame.GeometryUncertain): self.owner.select(None)
        self.assertTrue(self.owner.quarantined); self.assertIs(self.owner.pending,request)
        self.assertIs(self.owner.capture,capture); self.assertIn(self.owner,frame._uncertain)
        self.gl.glFinish(); self.owner.quarantined=False; self.owner.close(); frame._uncertain.remove(self.owner)

    def test_invalid_receipts_reentrancy_and_wrong_context_fail_before_work(self):
        for change in (dict(width=0),dict(samples=2),dict(lease_bytes=0),dict(leases=()),dict(poses=()),
                dict(verify=None),dict(positions=()),dict(poses=(object(),))):
            with self.subTest(change=change),self.assertRaises(ValueError): self.request(**change)
        pose=self.request().poses[0]
        with self.assertRaisesRegex(ValueError,'same original primitive ranges'):
            self.request(poses=(replace(pose,meshes=((self.mesh,5,2),)),))
        with self.assertRaises(ValueError): frame.LayerFrame(byte_budget=True)
        with self.assertRaises(ValueError): self.step(existing_bytes=-1)
        with self.assertRaises(RuntimeError): self.owner.step(self.gl,object(),existing_bytes=0)
        self.owner._stepping=True
        with self.assertRaises(RuntimeError): self.step()
        self.owner._stepping=False
        self.owner.close(); self.owner.close()
        with self.assertRaises(RuntimeError): self.owner.select(self.request())
