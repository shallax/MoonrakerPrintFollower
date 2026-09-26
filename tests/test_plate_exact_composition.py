"""The exact compositor's policy is executed without a QML scene graph.

Keep these pure checks independent of render timing: the Qt engine tests
separately establish that the QML adapter wires the real signals correctly.
"""
import json
import unittest
from pathlib import Path

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
except ImportError:
    QCoreApplication = QJSEngine = None


@unittest.skipUnless(QJSEngine is not None, "PyQt6 QtQml required")
class ExactCompositionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.engine = QJSEngine()
        source = (Path(__file__).resolve().parents[1] / "plugins" /
                  "PlateExactComposition.js").read_text(encoding="utf-8")
        loaded = self.engine.evaluate(source.replace(".pragma library", ""))
        self.assertFalse(loaded.isError(), loaded.toString())

    def evaluate(self, expression):
        value = self.engine.evaluate("JSON.stringify(" + expression + ")")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def test_one_transaction_coalesces_and_rejects_old_world_receipts(self):
        actual = self.evaluate("""(function () {
            var t = empty(1, 'print-A/layer-3');
            t = request(t).state;
            var queued = request(t);
            t = painted(queued.state, {epoch:1,world:'print-A/layer-3',
                                      valid:true,from:10,split:18});
            t = newWorld(t, 2, 'print-B/layer-3');
            var result = delivered(t, 2, 'print-B/layer-3');
            return {queued:queued.state.pending, accepted:result.accepted,
                    retry:needsPaint(result.state)};
        })()""")
        self.assertEqual(actual, {"queued": True, "accepted": False,
                                  "retry": True})

    def test_equivalent_implicit_paints_can_share_one_delivery(self):
        actual = self.evaluate("""(function () {
            var r = {epoch:1,world:'A',valid:true,from:10,split:18};
            var t = request(empty(1,'A')).state;
            t = painted(painted(t,r),r);
            var good = delivered(t,1,'A');
            var mixed = painted(painted(request(empty(1,'A')).state,r),
                {epoch:1,world:'A',valid:true,from:15,split:18});
            return {equivalent:good.accepted,mixed:delivered(mixed,1,'A').accepted,
                    delivered:good.receipt.from};
        })()""")
        self.assertEqual(actual, {"equivalent": True, "mixed": False,
                                  "delivered": 10})

    def test_scene_change_retargets_a_request_that_has_not_painted(self):
        self.assertEqual(self.evaluate("""(function () {
            var t=request(empty(1,'A')).state;
            var next=newWorld(t,2,'B');
            return {waiting:next.inFlight,start:request(next).start};
        })()"""), {"waiting": False, "start": True})

    def test_scene_change_keeps_the_real_upload_accounted_until_delivery(self):
        self.assertEqual(self.evaluate("""(function () {
            var t=painted(request(empty(1,'A')).state,
                {epoch:1,world:'A',valid:true,from:0,split:18});
            var next=newWorld(t,2,'B');
            return {count:next.count,waiting:next.inFlight,
                    accepted:delivered(next,2,'B').accepted};
        })()"""), {"count": 1, "waiting": True, "accepted": False})

    def test_scrubs_hold_the_last_complete_picture_until_the_target_is_ready(self):
        self.assertEqual(self.evaluate("""(function () {
            var p={full:false,fullReady:false,epoch:1,world:'A',split:20,
                receipt:{valid:true,epoch:1,world:'A',from:0,split:18},
                splitOk:false,currentPrefix:{ready:false},retainedPrefix:{ready:false},
                heldFull:false,inkless:false};
            var forward=presentation(p);p.split=10;
            var backward=presentation(p);p.heldFull=true;
            return [forward.kind,forward.ready,backward.kind,presentation(p).kind];
        })()"""), ["canvas", False, "canvas", "canvas"])

    def test_no_op_paint_cannot_retire_an_actual_upload(self):
        self.assertEqual(self.evaluate("""(function () {
            var t=request(empty(1,'A')).state;
            var noOp=unchanged(t);
            t=painted(t,{epoch:1,world:'A',valid:true,from:0,split:18});
            return {noOpWaiting:noOp.inFlight,uploadWaiting:unchanged(t).inFlight};
        })()"""), {"noOpWaiting": False, "uploadWaiting": True})

    def test_attached_monotonic_only_in_same_world(self):
        self.assertEqual(self.evaluate("""[
            splitGate(true,1,1,18,20,true),
            splitGate(true,1,1,18,20,false),
            splitGate(true,1,2,18,20,true),
            splitGate(true,1,1,18,17,true)
        ]"""), [True, False, False, False])

    def test_first_show_never_doubles_a_full_vector_bitmap(self):
        self.assertEqual(self.evaluate("""(function () {
            var layer = {prefixSplit:10,prefixData:"prefix-A"};
            return [prefixReady(layer,true,true,0,18,18,false,false,0,"prefix-A"),
                    prefixReady(layer,true,true,10,18,18,false,false,10,"prefix-A"),
                    prefixReady(layer,true,true,0,18,18,true,false,0,"prefix-A")];
        })()"""), [False, True, False])

    def test_exact_scene_waits_only_for_required_components(self):
        self.assertEqual(self.evaluate("""(function () {
            var s = {available:true,hasCurrent:true,full:false,partial:true,
                     prefixUsable:true,prefixReady:true,fullCanvasReady:false,
                     baseShown:false,basePending:true,previousPending:false,
                     nextPending:false};
            var ready = exactReady(s);
            s.previousPending = true;
            var ghost = exactReady(s);
            s.previousPending = false;
            s.prefixReady = false;
            var missing = exactReady(s);
            return [ready,ghost,missing];
        })()"""), [True, False, False])

    def test_interval_ownership_requires_the_actual_prefix_asset(self):
        self.assertEqual(self.evaluate("""(function () {
            var layer={prefixSplit:10,prefixData:'new-prefix'};
            return [
                partialComposition(layer,18,true,true,0,true,'old-prefix'),
                partialComposition(layer,18,true,true,10,true,'old-prefix'),
                partialComposition(layer,18,true,true,10,true,'new-prefix'),
                partialComposition(layer,18,true,true,10,false,'new-prefix'),
                partialComposition(layer,18,true,false,10,true,'new-prefix')
            ];
        })()"""), [
            {"ready": True, "vector": True, "prefix": False},
            {"ready": False, "vector": False, "prefix": False},
            {"ready": True, "vector": False, "prefix": True},
            {"ready": False, "vector": False, "prefix": False},
            {"ready": False, "vector": False, "prefix": False},
        ])

    def test_same_boundary_different_prefix_assets_are_not_consensus(self):
        self.assertFalse(self.evaluate("""(function () {
            var t=request(empty(1,'A')).state;
            t=painted(t,{epoch:1,world:'A',valid:true,from:10,split:18,
                         prefixSource:'old-prefix'});
            t=painted(t,{epoch:1,world:'A',valid:true,from:10,split:18,
                         prefixSource:'new-prefix'});
            return delivered(t,1,'A').accepted;
        })()"""))

    def test_full_canvas_releases_exact_readiness_during_prefix_decode(self):
        self.assertTrue(self.evaluate("""exactReady({
            available:true,hasCurrent:true,partial:true,full:false,
            prefixUsable:true,prefixReady:false,fullCanvasReady:true,
            baseShown:false,previousPending:false,nextPending:false
        })"""))

    def test_presentation_keeps_one_interval_owner_through_asset_replacement(self):
        self.assertEqual(self.evaluate("""(function () {
            var p={full:false,fullReady:false,epoch:2,world:'scene',splitOk:true,split:18,
                   inkless:false,heldFull:true,
                   receipt:{valid:true,epoch:2,world:'scene',from:10,split:18,
                            prefixSource:'old'},
                   currentPrefix:{ready:false,source:'new',from:15},
                   retainedPrefix:{ready:true,source:'old',from:10}};
            var waiting=presentation(p);
            p.currentPrefix.ready=true;
            var imageFirst=presentation(p);
            p.receipt.prefixSource='new';p.receipt.from=15;
            var joint=presentation(p);
            p.receipt.from=0;
            var fallback=presentation(p);
            p.full=true;p.fullReady=false;
            var fullLoading=presentation(p);
            p.fullReady=true;
            var full=presentation(p);
            return [waiting,imageFirst,joint,fallback,fullLoading,full];
        })()"""), [
            {"kind": "prefix", "prefix": "retained", "ready": True},
            {"kind": "prefix", "prefix": "retained", "ready": True},
            {"kind": "prefix", "prefix": "current", "ready": True},
            {"kind": "canvas", "prefix": "", "ready": True},
            {"kind": "canvas", "prefix": "", "ready": True},
            {"kind": "full", "prefix": "", "ready": True},
        ])

    def test_an_old_world_receipt_cannot_expose_any_prefix(self):
        self.assertEqual(self.evaluate("""presentation({
            full:false,fullReady:false,epoch:2,world:'B',splitOk:true,
            inkless:false,heldFull:false,
            receipt:{valid:true,epoch:1,world:'A',from:10,split:18,prefixSource:'old'},
            currentPrefix:{ready:true,source:'old',from:10},
            retainedPrefix:{ready:true,source:'old',from:10}
        })"""), {"kind": "preparing", "prefix": "", "ready": False})

    def test_partial_canvas_cannot_release_a_full_demand(self):
        self.assertEqual(self.evaluate("""presentation({
            full:true,fullReady:false,epoch:2,world:'scene',split:21,
            splitOk:true,inkless:false,heldFull:false,
            receipt:{valid:true,epoch:2,world:'scene',from:0,split:18},
            currentPrefix:{ready:false},retainedPrefix:{ready:false}
        })"""), {"kind": "canvas", "prefix": "", "ready": False})

    def test_full_requires_a_ready_pair_or_a_complete_fallback(self):
        self.assertEqual(self.evaluate("""(function () {
            var p={available:true,hasCurrent:true,full:true,
                   fullImagesReady:false,fullCanvasReady:false};
            var missing=exactReady(p);
            p.fullCanvasReady=true;
            var fallback=exactReady(p);
            p.fullCanvasReady=false;p.fullImagesReady=true;
            return [missing,fallback,exactReady(p)];
        })()"""), [False, True, True])

    def test_an_empty_demand_waits_for_the_presentation_to_retire_old_pixels(self):
        self.assertEqual(self.evaluate("""(function () {
            var p={available:true,hasCurrent:true,full:false,partial:false,
                   presentationReady:false};
            var pending=exactReady(p);
            p.presentationReady=true;
            return [pending,exactReady(p)];
        })()"""), [False, True])

    def test_tail_plan_preserves_deltas_and_rebuilds_only_changed_inputs(self):
        self.assertEqual(self.evaluate("""(function () {
            var s={dirty:false,split:18,paints:2,source:'geometry',view:'view',
                   from:10,prefixSource:'prefix'};
            var d={split:20,source:'geometry',view:'view',cadence:200,
                   prefix:{from:10,source:'prefix'}};
            var delta=tailPlan(s,d);
            d.split=15;
            var reverse=tailPlan(s,d);
            d.split=20;d.prefix={from:15,source:'next'};
            var boundary=tailPlan(s,d);
            d.prefix={from:0,source:''};
            return [delta,reverse,boundary,tailPlan(s,d)];
        })()"""), [
            {"reset": False, "from": 18, "coverage": 10},
            {"reset": True, "from": 10, "coverage": 10},
            {"reset": True, "from": 15, "coverage": 15},
            {"reset": True, "from": -1, "coverage": 0},
        ])


if __name__ == "__main__":
    unittest.main()
