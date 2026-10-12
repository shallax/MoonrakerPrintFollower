"""Metadata diagnostics cannot bind GL or prolong owner lifetimes."""
from types import SimpleNamespace as NS
import gc
import unittest
import weakref
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from mpf.diagnostics.GraphicsCensus import GraphicsCensus, WorkerGraphicsCensus, snapshot


class Target:
    def __init__(self, name=1, textures=(2, 3, 4)):
        self.name, self.names = name, textures

    def handle(self): return self.name
    def textures(self): return self.names
    def size(self): return NS(width=lambda: 100, height=lambda: 80)
    def format(self):
        return NS(samples=lambda: 0, internalTextureFormat=lambda: 0x8814,
                  attachment=lambda: NS(value=1))


class CensusTests(unittest.TestCase):
    def setUp(self):
        self.group = object()
        self.context = NS(shareGroup=lambda: self.group)

    def test_aliases_and_texture_views_share_namespaces_without_double_counting(self):
        target = Target()
        shadow = NS(_context=self.context, _storage=dict(vertex_bytes=304, count=4,
            vertex=NS(bufferId=lambda: 8), lines=NS(bufferId=lambda: 9),
            template=NS(bufferId=lambda: 10), textures=(2, 11)))
        node = NS(_frame_cache=NS(_context=self.context, _fbo=target, _resolved=target,
                                 _pool={(100, 80, 0): (target, None)}),
                  _simulation_pass=NS(_instanced_shadow=shadow))
        rows = snapshot(node, self.context)
        self.assertEqual(len([r for r in rows if r['kind']=='fbo']), 1)
        self.assertEqual(len([r for r in rows if r['kind']=='texture']), 4)
        self.assertEqual([r['ownership'] for r in rows if r.get('name')==8], ['borrowed'])
        self.assertEqual([r['bytes'] for r in rows if r.get('name')==9], [16])
        shadow._storage['index_owner']=object()
        borrowed=snapshot(node,self.context)
        self.assertEqual([r['ownership'] for r in borrowed if r.get('name')==9],['borrowed'])
        node._simulation_pass._geometry=NS(_context=self.context,
            _index_buffer=shadow._storage['lines'],_indices=NS(nbytes=16))
        shared=snapshot(node,self.context)
        self.assertEqual([r['owner'] for r in shared if r.get('name')==9],['simulation.path.indices'])
        self.assertTrue(any(r['kind']=='incomplete' for r in rows))

    def test_foreign_context_does_not_touch_wrappers(self):
        target = Mock()
        rows = snapshot(NS(_frame_cache=NS(_context=object(), _fbo=target)), self.context)
        target.handle.assert_not_called()
        target.size.assert_not_called()
        self.assertEqual(rows[0]['kind'], 'foreign-context')

    def test_snapshot_keeps_only_values(self):
        target = Target()
        reference = weakref.ref(target)
        node = NS(_frame_cache=NS(_fbo=target))
        rows = snapshot(node, self.context)
        del node, target
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(rows[0]['width'], 100)

    def test_disabled_and_bounded_reporting_and_failure_containment(self):
        now, logger = [0.], Mock()
        census = GraphicsCensus(clock=lambda: now[0], logger=logger)
        census.report(None, False, None)
        logger.assert_not_called()
        census.report(NS(), True, self.context)
        now[0] = 9.99
        census.report(NS(), True, self.context)
        self.assertEqual(logger.call_count, 1)
        now[0] = 10.
        census.report(NS(), True, self.context)
        self.assertEqual(logger.call_count, 2)
        logger.side_effect = RuntimeError('diagnostic failure')
        now[0] = 20.
        census.report(NS(), True, self.context)

    def test_broken_target_does_not_hide_other_owners(self):
        broken = Mock()
        broken.size.side_effect = RuntimeError('retired Qt owner')
        rows = snapshot(NS(_frame_cache=NS(_fbo=broken),
                           _rotor_render=NS(_work=Target())), self.context)
        self.assertEqual(rows[0]['kind'], 'unavailable')
        self.assertTrue(any(r['kind']=='fbo' for r in rows))

    def test_published_worker_names_are_metadata_only(self):
        node = NS(_environment=NS(_storage=NS(result=NS(colour=42, depth=0))))
        rows = snapshot(node, self.context)
        published = next(row for row in rows if row.get('name') == 42)
        self.assertIn('worker-owned', published['ownership'])

    def test_mesh_uploads_use_only_cached_array_sizes_and_names(self):
        arrays = NS(nbytes=120)
        mesh = NS(_vertices=arrays, _normals=arrays, _indices=NS(nbytes=36), _attributes={})
        setattr(mesh, '__vertex_buffer', NS(bufferId=lambda: 71))
        setattr(mesh, '__index_buffer', NS(bufferId=lambda: 72))
        geometry = NS(_vertex_mesh=mesh, _indices=NS(nbytes=48),
            _index_buffer=NS(bufferId=lambda: 73), _index_chunk=None)
        node = NS(getMeshData=lambda: mesh, _rotor_meshes={'fan': (mesh,)},
            _scene_lighting=NS(_path_geometry={1:geometry}))
        rows = snapshot(node,self.context)
        buffers = {row['name']:row for row in rows if row['kind']=='buffer'}
        self.assertEqual(set(buffers), {71,72,73})
        self.assertEqual(buffers[71]['estimated_bytes'],240)
        self.assertEqual(buffers[73]['estimated_bytes'],48)

    def worker_fixture(self):
        buffer = NS(name=82,size=128)
        path = NS(index=buffer,lease=NS(name=83,size=304),arrays={1:NS(name=2)})
        scene = NS(paths={1:path},plates=NS(buffers={1:(buffer,)},arrays={}),texture=None)
        face = NS(framebuffer=5,texture=85,renderbuffer=6,retained_bytes=2097152)
        environment = NS(_storage=NS(names=(86,87),face=face))
        return scene,environment

    def test_worker_publication_is_immutable_scalar_only_throttled_and_serial_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'.profile-graphics';marker.touch()
            now = [0.]
            census = WorkerGraphicsCensus(marker,clock=lambda:now[0])
            scene,environment = self.worker_fixture()
            result = census.capture(10,self.context,scene,environment)
            def plain(value):
                if isinstance(value,tuple):return all(plain(item) for item in value)
                return value is None or type(value) in (str,int,float)
            self.assertTrue(plain(result))
            self.assertIsNone(census.capture(11,self.context,scene,environment))
            node = NS(_environment=NS(_worker=NS(graphics_census=result),
                _storage=NS(result=NS(colour=86,serial=11))))
            rows = snapshot(node,self.context)
            self.assertTrue(next(row for row in rows if row['kind']=='snapshot')['stale'])
            self.assertEqual(len([row for row in rows if row.get('name')==86]),1)
            self.assertEqual(len([row for row in rows if row.get('name')==82]),1)
            marker.unlink();now[0]=10.
            self.assertIsNone(census.capture(12,self.context,scene,environment))
            node._environment._worker.graphics_census = None
            self.assertTrue(any(row['kind']=='incomplete' for row in snapshot(node,self.context)))

    def test_absent_marker_checks_only_at_startup_and_never_reads_clock(self):
        with patch.object(Path, 'is_file', return_value=False) as exists:
            clock = Mock()
            census = GraphicsCensus(clock=clock)
            for _ in range(20): census.report(None,False,None)
            exists.assert_called_once()
            clock.assert_not_called()

    def test_standalone_marker_removal_stops_reports_without_disabling_explicit_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'.profile-graphics'
            marker.touch()
            now, logger = [0.], Mock()
            census = GraphicsCensus(marker,clock=lambda: now[0],logger=logger)
            census.report(NS(),False,self.context)
            self.assertEqual(logger.call_count,1)
            marker.unlink()
            now[0] = 10.
            census.report(NS(),False,self.context)
            self.assertEqual(logger.call_count,1)
            now[0] = 20.
            census.report(NS(),True,self.context)
            self.assertEqual(logger.call_count,2)

    def test_explicit_target_receipt_includes_guard_and_readback_without_duplicate_names(self):
        target = Target(name=21,textures=(22,))
        target.allocation_bytes = 288000
        target._names = (22,23)
        target._fbo = Target(name=21,textures=(24,))
        target._read_fbo = Target(name=25,textures=(26,))
        rows = snapshot(NS(_frame_cache=NS(_fbo=target)),self.context)
        self.assertEqual({r['name'] for r in rows if r['kind']=='texture'}, {22,23,24,26})
        targets = {r['name']:r for r in rows if r['kind']=='fbo'}
        self.assertEqual(set(targets),{21,25})
        self.assertEqual(targets[21]['estimated_bytes'],288000)

    def test_unknown_attachment_layout_is_not_given_a_false_byte_estimate(self):
        rows = snapshot(NS(_frame_cache=NS(_fbo=Target(textures=(2,3)))),self.context)
        self.assertIsNone(next(row for row in rows if row['kind']=='fbo')['estimated_bytes'])

    def test_main_capture_chunk_and_borrowed_worker_lease_are_distinguished(self):
        geometry = NS(_context=self.context,_index_buffer=NS(bufferId=lambda:91),_index_chunk=128)
        result = NS(frame=NS(paths=((None,NS(name=92,size=4096)),)))
        node = NS(_environment_scene=NS(_geometry={1:geometry}),_environment=NS(_storage=NS(result=result)))
        rows = {r['name']:r for r in snapshot(node,self.context) if r['kind']=='buffer'}
        self.assertEqual(rows[91]['estimated_bytes'],512)
        self.assertEqual(rows[91]['estimate'],'chunk upper bound')
        self.assertEqual(rows[92]['bytes'],4096)
        self.assertIn('borrowed',rows[92]['ownership'])

    def test_marker_io_failures_disable_standalone_diagnostics_and_default_logger_is_safe(self):
        with patch.object(Path,'is_file',side_effect=OSError('unavailable')):
            clock = Mock()
            census = WorkerGraphicsCensus(clock=clock)
            self.assertIsNone(census.capture(1,None,None,None))
            census.report(None,False,None)
            clock.assert_not_called()
        logger = Mock()
        with patch.dict('sys.modules',{'UM.Logger':NS(Logger=logger)}):
            GraphicsCensus(clock=lambda:0.).report(NS(),True,self.context)
            self.assertEqual(logger.log.call_args.args[0],'i')
            self.assertIsInstance(logger.log.call_args.args[2],list)
            logger.log.side_effect = RuntimeError('logger unavailable')
            GraphicsCensus(clock=lambda:0.).report(NS(),True,self.context)

    def test_worker_platform_receipt_uses_dimensions_without_copying_pixels_and_fails_closed(self):
        with patch.object(Path,'is_file',return_value=True):
            census = WorkerGraphicsCensus(clock=lambda:0.)
        scene,environment = self.worker_fixture()
        pixels = Mock()  # No conversion/length/read of the retained pixel body.
        scene.texture,scene.texture_key = NS(name=94),(256,128,pixels)
        result = census.capture(1,self.context,scene,environment)
        row = next(row for row in result[-1] if row[0]=='worker.platform')
        self.assertEqual(row[3],256*128*4)
        self.assertEqual(pixels.mock_calls,[])
        census._next = 0.
        scene.texture_key = ()
        result = census.capture(2,self.context,scene,environment)
        self.assertEqual(next(row for row in result[-1] if row[0]=='worker.platform')[3],4)
        census._next = 0.
        self.assertIsNone(census.capture(3,self.context,object(),environment))


if __name__ == '__main__': unittest.main()
