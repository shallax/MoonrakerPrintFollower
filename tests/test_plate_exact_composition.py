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

    def test_attached_monotonic_only_in_same_world(self):
        self.assertEqual(self.evaluate("""[
            splitGate(true,1,1,18,20,true),
            splitGate(true,1,1,18,20,false),
            splitGate(true,1,2,18,20,true),
            splitGate(true,1,1,18,17,true)
        ]"""), [True, False, False, False])

    def test_first_show_never_doubles_a_full_vector_bitmap(self):
        self.assertEqual(self.evaluate("""(function () {
            var layer = {prefixSplit:10};
            return [prefixReady(layer,true,true,0,18,18,false,false,0),
                    prefixReady(layer,true,true,10,18,18,false,false,10),
                    prefixReady(layer,true,true,0,18,18,true,false,0)];
        })()"""), [False, True, True])

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


if __name__ == "__main__":
    unittest.main()
