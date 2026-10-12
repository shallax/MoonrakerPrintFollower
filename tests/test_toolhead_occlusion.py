"""Native depth and transparent inventory remain live on composite-only ticks."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np
from mpf.toolhead.ToolheadOcclusion import ToolheadOcclusion


class OcclusionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.owner=ToolheadOcclusion()
        self.provider=NS(get_visible_depth_revision=Mock(return_value='depth1'),
            get_sample_depth_revision=Mock(return_value='samples1'),copy_visible_depth=Mock(return_value=True))
        self.batches=[]
        self.renderer=NS(getBatches=lambda:self.batches,getRenderPass=lambda name:self.provider)
        self.gl=Mock();self.gl.glGetIntegerv.side_effect=lambda name:(13,17,800,600) if name==0x0BA2 else 0
        batch=NS(RenderMode=NS(Triangles=4),RenderType=NS(Solid=1,Transparent=2))
        context=patch.dict('sys.modules',{'UM.View.RenderBatch':NS(RenderBatch=batch)})
        context.start();self.addCleanup(context.stop)
        self.camera=object()

    def item(self, vertices=3):
        data=np.eye(4)
        return dict(mesh=NS(getVertices=Mock(return_value=None if vertices is None else np.zeros((vertices,3)))),
            transformation=NS(getData=lambda:data),uniforms={'alpha':[.5]}),data

    def batch(self, kind, items, mode=4):
        return NS(renderType=kind,renderMode=mode,items=items,shader=object())

    def test_single_partition_keeps_order_and_checks_only_solid_triangle_vertices(self):
        solid,_=self.item();short,_=self.item(2);empty,_=self.item(None)
        front,_=self.item();other,_=self.item();overlay,_=self.item()
        first=self.batch(2,[front]);last=self.batch(2,[])
        self.batches=[first,self.batch(1,[solid,short,empty]),self.batch(1,[other],mode=1),self.batch(3,[overlay]),last]
        key,seed=self.owner.prepare(self.renderer,self.camera,self.gl)
        self.assertEqual(self.owner._transparent,(first,last))
        self.assertEqual(key[1],((id(solid['mesh']),solid['transformation'].getData().tobytes()),))
        self.assertEqual([entry[0] for entry in key[2]],[id(first.shader),id(last.shader)])
        for item in (solid,short,empty):item['mesh'].getVertices.assert_called_once()
        for item in (front,other,overlay):item['mesh'].getVertices.assert_not_called()
        self.owner._draw_bed=Mock()
        self.assertTrue(seed(self.gl,object(),self.camera,(13,17,800,600),(0,0,800,600)))
        self.assertEqual(self.owner._draw_bed.call_args.args[4],[solid])

    def test_composite_only_ticks_observe_mutable_transforms_uniforms_and_revisions(self):
        solid,position=self.item();glass,glass_position=self.item()
        opaque=self.batch(1,[solid]);transparent=self.batch(2,[glass])
        self.batches=[opaque,transparent]
        first,_=self.owner.prepare(self.renderer,self.camera,self.gl)
        self.batches=[]
        position[0,3]=10;glass_position[1,3]=20;glass['uniforms']['alpha'][0]=.8
        self.provider.get_visible_depth_revision.return_value='depth2'
        self.provider.get_sample_depth_revision.return_value='samples2'
        second,_=self.owner.prepare(self.renderer,self.camera,self.gl)
        for index in range(4):self.assertNotEqual(first[index],second[index])
        self.assertEqual(self.provider.get_visible_depth_revision.call_count,2)
        self.assertEqual(self.provider.get_sample_depth_revision.call_count,2)
        replacement,_=self.item();transparent.items[:]=[replacement,glass]
        third,_=self.owner.prepare(self.renderer,self.camera,self.gl)
        self.assertEqual([entry[0] for entry in third[2][0][1]],[id(replacement['mesh']),id(glass['mesh'])])
        self.batches=[self.batch(2,[replacement])]
        final,_=self.owner.prepare(self.renderer,self.camera,self.gl)
        self.assertEqual(final[1],())
        self.assertEqual(self.owner._transparent,tuple(self.batches))
