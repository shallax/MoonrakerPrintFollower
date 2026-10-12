"""Optional reducer fallback and physical pair publication/retirement."""
from contextlib import nullcontext
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from mpf.toolhead import ToolheadDepthHierarchy as hierarchy
with patch.dict(sys.modules, {
    'mpf.toolhead.ToolheadCaptureGL': NS(RawBindings=object),
    'mpf.toolhead.ToolheadCaptureRecipe': NS(CaptureScene=object),
}):
    from mpf.toolhead import ToolheadEnvironmentWorker as worker


class HierarchyLifetimeTests(unittest.TestCase):
    def test_legacy_never_allocates_and_clean_capability_failure_is_optional(self):
        context=Mock();context.format().majorVersion.return_value=2
        gl=Mock();gl.glGetError.return_value=0
        self.assertIsNone(hierarchy.DepthHierarchy.optional(gl,context))
        context.format().majorVersion.return_value=4
        with patch.object(hierarchy.DepthHierarchy,'_current'), \
             patch.object(hierarchy,'preserved_state',side_effect=lambda *a,**k:nullcontext()), \
             patch.object(hierarchy.DepthHierarchy,'_allocate',side_effect=RuntimeError('unsupported')):
            self.assertIsNone(hierarchy.DepthHierarchy.optional(gl,context))
        gl.glGetError.assert_called()

    def test_host_restoration_failure_is_quarantined_not_optional(self):
        context=Mock();context.format().majorVersion.return_value=4
        guard=Mock();guard.__enter__=Mock();guard.__exit__=Mock(side_effect=RuntimeError('restoration'))
        gl=Mock();gl.glGetError.return_value=0
        try:
            with patch.object(hierarchy.DepthHierarchy,'_current'), \
                 patch.object(hierarchy,'preserved_state',return_value=guard), \
                 patch.object(hierarchy.DepthHierarchy,'_allocate'):
                with self.assertRaises(hierarchy.GeometryUncertain) as error:
                    hierarchy.DepthHierarchy.optional(gl,context)
            self.assertIn(error.exception.owner,hierarchy._uncertain)
        finally: hierarchy._uncertain.clear()

    def owner(self):
        owner=hierarchy.DepthHierarchy.__new__(hierarchy.DepthHierarchy)
        owner.context=object();owner.names=[51,52];owner.enabled=True
        owner.full_names=[61,62];owner.full_scratch=[71,72]
        owner.full_first_program=owner.full_second_program=None
        owner.full_ready={51,52}
        owner.vao=owner.program=owner.second_program=None
        owner.fbo=owner.scratch=0
        owner.retained_bytes=hierarchy.RETAINED_BYTES
        owner.gl=Mock();owner.gl.glGetError.return_value=0
        owner._current=Mock();owner._build=Mock()
        return owner

    def test_clean_build_failure_disables_future_reduction_without_freeing_front(self):
        owner=self.owner();owner._build.side_effect=RuntimeError('unsupported target')
        with patch.object(hierarchy,'preserved_state',side_effect=lambda *a,**k:nullcontext()):
            self.assertFalse(owner.build(owner.gl,31,51))
            self.assertFalse(owner.build(owner.gl,32,52))
        owner.gl.glFinish.assert_called_once()
        owner._build.assert_called_once()
        self.assertEqual(owner.names,[51,52])
        self.assertEqual(owner.full_ready,{52})
        self.assertEqual(owner.full_target(51),0)
        self.assertEqual(owner.full_target(52),62)
        self.assertEqual(owner.full_names,[61,62])
        self.assertEqual(owner.full_scratch,[71,72])
        self.assertEqual(owner.retained_bytes,hierarchy.RETAINED_BYTES)

    def test_full_texture_retirement_failure_retains_failed_and_later_resources(self):
        for field in ('full_scratch','full_names'):
            with self.subTest(field=field):
                owner=self.owner();owner.names=[]
                original=list(getattr(owner,field))
                # Scratch precedes atlas retirement; fail the chosen operation.
                owner.gl.glGetError.side_effect=([0,0x0502] if field=='full_names' else [0x0502])
                delete=Mock()
                try:
                    with patch.object(hierarchy,'procedure',return_value=delete):
                        with self.assertRaises(hierarchy.GeometryUncertain) as raised:owner.close()
                    self.assertIs(raised.exception.owner,owner)
                    self.assertIn(owner,hierarchy._uncertain)
                    self.assertEqual(getattr(owner,field),original)
                    self.assertEqual(owner.full_names,[61,62])
                    self.assertEqual(owner.retained_bytes,hierarchy.RETAINED_BYTES)
                    self.assertEqual(owner.full_target(52),0)
                    self.assertEqual(delete.call_count,2 if field=='full_names' else 1)
                    if field=='full_names':self.assertEqual(owner.full_scratch,[])
                finally:hierarchy._uncertain.clear()

    def test_full_program_retirement_failure_preserves_program_and_textures(self):
        for field in ('full_second_program','full_first_program'):
            with self.subTest(field=field):
                owner=self.owner();program=Mock();setattr(owner,field,program)
                owner.gl.glGetError.return_value=0x0502
                try:
                    with patch.object(hierarchy,'procedure') as procedure:
                        with self.assertRaises(hierarchy.GeometryUncertain) as raised:owner.close()
                    self.assertIs(raised.exception.owner,owner)
                    self.assertIs(getattr(owner,field),program)
                    program.close.assert_called_once()
                    procedure.assert_not_called()
                    self.assertEqual(owner.names,[51,52])
                    self.assertEqual(owner.full_names,[61,62])
                    self.assertEqual(owner.full_scratch,[71,72])
                    self.assertEqual(owner.retained_bytes,hierarchy.RETAINED_BYTES)
                    self.assertIn(owner,hierarchy._uncertain)
                finally:hierarchy._uncertain.clear()

    def test_scratch_retirement_failure_quarantines_entire_owner(self):
        owner=self.owner()
        owner.vao=owner.program=owner.second_program=None
        owner.fbo=0;owner.scratch=71
        owner.retained_bytes=hierarchy.RETAINED_BYTES
        owner.gl.glGetError.return_value=0x0502
        delete=Mock()
        try:
            with patch.object(hierarchy,'procedure',return_value=delete):
                with self.assertRaises(hierarchy.GeometryUncertain):owner.close()
            self.assertIn(owner,hierarchy._uncertain)
            self.assertEqual(owner.names,[51,52])
            self.assertEqual(owner.scratch,71)
            self.assertEqual(owner.retained_bytes,hierarchy.RETAINED_BYTES)
            delete.assert_called_once()
        finally:hierarchy._uncertain.clear()

    def test_selected_physical_pair_owns_ranges_even_after_cancelled_private_swap(self):
        storage=worker.WorkerStorage.__new__(worker.WorkerStorage)
        storage.names=[11,12,21,22];storage._hierarchy=Mock(names=[51,52],full_names=[61,62])
        storage._hierarchy.build.return_value=True
        storage.front,storage.front_depth=12,22
        storage.front_hierarchy=52
        def publish(_gl):
            storage.front,storage.back=storage.back,storage.front
            storage.front_depth,storage.back_depth=storage.back_depth,storage.front_depth
        with patch.object(worker.CubeStorage,'publish',side_effect=publish):
            storage.select(0);storage.publish('gl')
            storage._hierarchy.build.assert_called_with('gl',21,51)
            self.assertEqual((storage.front,storage.front_depth,storage.front_hierarchy),(11,21,51))
            self.assertEqual(storage.front_full_hierarchy,61)
            # Cancelled publication does not own target choice; mailbox does.
            storage.select(0);storage.publish('gl')
            self.assertEqual((storage.front,storage.front_depth,storage.front_hierarchy),(11,21,51))
            storage._hierarchy.build.return_value=False
            storage.select(1);storage.publish('gl')
            self.assertEqual((storage.front,storage.front_depth,storage.front_hierarchy),(12,22,0))
            self.assertEqual(storage.front_full_hierarchy,0)

    def test_wrong_pair_and_invalid_source_refuse_before_drawing(self):
        owner=self.owner()
        for source,target in ((0,51),(31,53),('31',51)):
            with self.assertRaises(ValueError):owner.build(owner.gl,source,target)
        owner._build.assert_not_called()

    def test_uncertain_range_retirement_retains_the_ordinary_pair(self):
        storage=worker.WorkerStorage.__new__(worker.WorkerStorage)
        storage.names=[11,12,21,22];storage._hierarchy=Mock()
        fault=hierarchy.GeometryUncertain(storage._hierarchy,'uncertain retirement')
        storage._hierarchy.close.side_effect=fault
        try:
            with self.assertRaises(hierarchy.GeometryUncertain):storage.close()
            self.assertIn(storage,worker._uncertain_storage)
            self.assertEqual(storage.names,[11,12,21,22])
        finally:worker._uncertain_storage.remove(storage)

    def test_uncertain_range_constructor_retains_already_allocated_ordinary_pair(self):
        allocated=[]
        def ordinary(storage,*args,**kwargs):
            storage.names=[11,12,21,22]
            allocated.append(storage)
        fault=hierarchy.GeometryUncertain(object(),'uncertain range allocation')
        try:
            with patch.object(worker.CubeStorage,'__init__',ordinary), \
                 patch.object(worker.DepthHierarchy,'optional',side_effect=fault), \
                 patch.object(worker.WorkerStorage,'select') as select:
                with self.assertRaises(hierarchy.GeometryUncertain) as raised:
                    worker.WorkerStorage(Mock(),Mock(),0x81A6)
            self.assertIs(raised.exception,fault)
            self.assertEqual(allocated[0].names,[11,12,21,22])
            self.assertIn(allocated[0],worker._uncertain_storage)
            select.assert_not_called()
        finally:
            for storage in allocated:
                if storage in worker._uncertain_storage:worker._uncertain_storage.remove(storage)
