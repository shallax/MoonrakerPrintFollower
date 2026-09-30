"""The plate's Canvas drawing, executed against a recording context.

The painters are pure over a context, a payload and one style record, so
the edge rule, the two search bounds, the batching and the glyph walk are
checked here directly. The real-engine plate suites separately establish
that the painted pixels land where the geometry says.
"""
import json
import unittest
from pathlib import Path

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
except ImportError:
    QCoreApplication = QJSEngine = None

SOURCES = ("PlateViewPolicy.js", "PreviewColours.js", "PlatePainter.js")

# A 200 x 150 mm bed at 2 px/mm, the plot 10 px in from the face's corner.
PLOT = ("{sx:2.0,sy:2.0,bed:{offsetX:10.0,offsetY:10.0,bedXMin:0.0,bedYMax:150.0,"
        "plotWidth:400.0,plotHeight:300.0}}")

# A context that records the calls instead of rasterising them, so the
# assertions are about the path the painter builds.
RECORDER = """
function Recorder() {
    this.ops = [];
    this.lineWidth = 0;
    this.lineJoin = "";
    this.lineCap = "";
    this.strokeStyle = "";
    this.globalAlpha = 1.0;
    var self = this;
    this.beginPath = function () { self.ops.push(["beginPath"]); };
    this.moveTo = function (x, y) { self.ops.push(["moveTo", x, y]); };
    this.lineTo = function (x, y) { self.ops.push(["lineTo", x, y]); };
    this.stroke = function () {
        self.ops.push(["stroke", self.strokeStyle, self.globalAlpha, self.lineWidth]);
    };
}
function style(extra) {
    var s = {plot: %s, scale: 1.0, panX: 0.0, panY: 0.0, lineWidth: 2.0,
             travelWidth: 1.0, trueThickness: false, colourScheme: {mode: 1},
             baseColour: "#base", classColour: function (name) { return "#" + name; }};
    for (var key in extra)
        s[key] = extra[key];
    return s;
}
// Two horizontal runs of one class: motions 0..2 and 5..6.
function twoRuns() {
    return {classes: {"WALL-OUTER": [
        [[0, 150, 0], [10, 150, 1], [20, 150, 2]],
        [[50, 150, 5], [60, 150, 6]]
    ]}};
}
""" % PLOT


@unittest.skipUnless(QJSEngine is not None, "PyQt6 QtQml required")
class PainterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])
        root = Path(__file__).resolve().parents[1] / "mpf" / "plate"
        cls.sources = [(root / name).read_text(encoding="utf-8") for name in SOURCES]

    def setUp(self):
        self.engine = QJSEngine()
        # QJSEngine has no QML namespaces and no Qt object, so the
        # libraries load into one scope and the painter's two imported
        # names are rebound explicitly. A painter that starts using a
        # third one fails here with a ReferenceError rather than
        # silently resolving through a wildcard.
        preamble = ("var Qt = {rgba: function (r, g, b, a) "
                    "{ return 'rgba(' + [r, g, b, a].join(',') + ')'; }};")
        namespaces = "var ViewPolicy = {toScene: toScene};" \
                     "var PreviewColours = {colour: colour};"
        for source in [preamble] + self.sources + [namespaces, RECORDER]:
            body = "\n".join(line for line in source.splitlines()
                             if not line.startswith((".pragma", ".import")))
            loaded = self.engine.evaluate(body)
            self.assertFalse(loaded.isError(), loaded.toString())

    def ops(self, script):
        value = self.engine.evaluate(
            "JSON.stringify((function () { var ctx = new Recorder(); "
            + script + " return ctx.ops; })())")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def value(self, expression):
        value = self.engine.evaluate("JSON.stringify(" + expression + ")")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def strokes(self, ops):
        return [op for op in ops if op[0] == "stroke"]

    def vertices(self, ops):
        return [op for op in ops if op[0] in ("moveTo", "lineTo")]

    # --- the edge rule and its two search bounds ---

    def test_an_edge_draws_exactly_when_its_own_motion_is_below_the_split(self):
        """The split is a COUNT of printed motions and edge i belongs to
        the motion points[i][2] names, so the edge at the boundary is the
        first one NOT drawn. A negative split means the whole run."""
        points = "[[0,0,0],[1,0,1],[2,0,2],[3,0,3]]"
        self.assertEqual([self.value("edgePrinted(%s, 1, %d)" % (points, split))
                          for split in (0, 1, 2, 3, 4)],
                         [False, False, True, True, True])
        self.assertTrue(self.value("edgePrinted(%s, 3, -1)" % points))
        # Off the end of the segment nothing is printed, at any split.
        self.assertFalse(self.value("edgePrinted(%s, 4, 99)" % points))

    def test_the_first_edge_search_matches_a_linear_walk_on_every_boundary(self):
        """The binary search replaced a linear scan that re-read the whole
        printed portion each paint. It must agree with that walk for every
        boundary, including past the segment's end."""
        points = "[[0,0,0],[1,0,2],[2,0,2],[3,0,7],[4,0,9]]"
        motions = [0, 2, 2, 7, 9]
        for boundary in range(-1, 12):
            with self.subTest(boundary=boundary):
                linear = next((i for i in range(1, len(motions))
                               if motions[i] >= boundary), len(motions))
                expected = 1 if boundary <= 0 else linear
                self.assertEqual(self.value("firstEdge(%s, %d)" % (points, boundary)),
                                 expected)
        # A degenerate segment has no edge to find.
        self.assertEqual(self.value("firstEdge([[0,0,0]], 5)"), 1)

    def test_the_first_run_search_skips_runs_that_end_below_the_boundary(self):
        """The runs ascend in motion order, so a run whose LAST motion is
        below the boundary holds nothing the walk could draw. Reading
        every run was the delta paint's cost on a layer of short runs."""
        segments = ("[[[0,0,0],[1,0,1]],[[2,0,4],[3,0,5]],[[4,0,8],[5,0,9]]]")
        self.assertEqual(self.value("firstRunAt(%s, 0)" % segments), 0)
        self.assertEqual(self.value("firstRunAt(%s, -1)" % segments), 0)
        self.assertEqual(self.value("firstRunAt(%s, 2)" % segments), 1)
        self.assertEqual(self.value("firstRunAt(%s, 5)" % segments), 1)
        self.assertEqual(self.value("firstRunAt(%s, 6)" % segments), 2)
        self.assertEqual(self.value("firstRunAt(%s, 99)" % segments), 3)
        # An empty run is not a wall: the bound steps past it.
        self.assertEqual(self.value("firstRunAt([[],[[0,0,7]]], 7)"), 1)

    # --- the layer stroke ---

    def test_the_geometry_lands_through_the_plot_scale_and_pan(self):
        """The strokes are bed-space geometry: the bed's own origin maps
        to the plot offset through the camera the style names."""
        ops = self.ops("drawLayer(ctx, twoRuns(), 1.0, -1, false, -1, "
                       "style({scale: 3.0, panX: 7.0, panY: -5.0}));")
        self.assertEqual(self.vertices(ops)[0], ["moveTo", 10.0 * 3.0 + 7.0,
                                                 10.0 * 3.0 - 5.0])
        self.assertEqual(self.vertices(ops)[1], ["lineTo", (10.0 + 20.0) * 3.0 + 7.0,
                                                 10.0 * 3.0 - 5.0])

    def test_one_class_strokes_once_and_never_bridges_two_runs(self):
        """A beginPath/stroke per SEGMENT was the dominant cost, and the
        fresh path was never what kept a stroke from bridging a travel: a
        stroke never joins subpaths and moveTo opens one. So the class
        strokes ONCE, with a moveTo opening each run."""
        ops = self.ops("drawLayer(ctx, twoRuns(), 1.0, -1, false, -1, style({}));")
        self.assertEqual(len(self.strokes(ops)), 1)
        self.assertEqual([op[0] for op in self.vertices(ops)],
                         ["moveTo", "lineTo", "lineTo", "moveTo", "lineTo"])

    def test_the_split_stops_the_walk_at_the_motion_it_counted(self):
        ops = self.ops("drawLayer(ctx, twoRuns(), 1.0, 2, false, -1, style({}));")
        # Motions 0 and 1 are below 2, so the first run draws one edge and
        # the second run (motions 5, 6) draws none.
        self.assertEqual([op[0] for op in self.vertices(ops)], ["moveTo", "lineTo"])

    def test_a_delta_paint_starts_at_the_boundary_and_leaves_the_history_unread(self):
        """The accumulated tail strokes only the motions past the last
        count: the printed runs below it are never walked."""
        ops = self.ops("drawLayer(ctx, twoRuns(), 1.0, -1, false, 5, style({}));")
        self.assertEqual([op[0] for op in self.vertices(ops)], ["moveTo", "lineTo"])
        self.assertEqual(self.vertices(ops)[0][1], (10.0 + 50.0 * 2.0) * 1.0)

    def test_the_alpha_and_the_class_colour_ride_the_stroke(self):
        ops = self.ops("drawLayer(ctx, twoRuns(), 0.3, -1, false, -1, style({}));")
        self.assertEqual(self.strokes(ops)[0][1], "#WALL-OUTER")
        self.assertEqual(self.strokes(ops)[0][2], 0.3)
        self.assertEqual(self.strokes(ops)[0][3], 2.0)

    def test_the_base_draws_grey_and_never_reads_a_per_motion_colour(self):
        """The base marks the unprinted suffix: one grey for every class,
        and the colour modes do not apply to it."""
        ops = self.ops("drawLayer(ctx, twoRuns(), 0.55, -1, true, -1, "
                       "style({colourScheme: {mode: 3}}));")
        for stroke in self.strokes(ops):
            self.assertEqual(stroke[1], "#base")

    def test_a_single_motion_run_draws_its_line_and_never_a_dot(self):
        """Every segment is at least one EDGE — the payload carries the
        move's start position — and a one-vertex run is not geometry."""
        ops = self.ops("drawLayer(ctx, {classes: {FILL: [[[0,150,0],[10,150,1]]]}}, "
                       "1.0, -1, false, -1, style({}));")
        self.assertEqual([op[0] for op in self.vertices(ops)], ["moveTo", "lineTo"])
        ops = self.ops("drawLayer(ctx, {classes: {FILL: [[[0,150,0]]]}}, "
                       "1.0, -1, false, -1, style({}));")
        self.assertEqual(self.vertices(ops), [])

    def test_true_thickness_strokes_each_edge_at_its_own_payload_width(self):
        """The per-motion pen is bed millimetres through the plot, so the
        edges cannot share one batched path."""
        ops = self.ops("drawLayer(ctx, {classes: {FILL: [[[0,150,0],[10,150,1],"
                       "[20,150,2]]]}, widths: [0.4, 0.8, 1.2]}, 1.0, -1, false, -1, "
                       "style({trueThickness: true, scale: 2.0}));")
        widths = [stroke[3] for stroke in self.strokes(ops) if stroke[3] > 0]
        # Edge 1 carries motion 1 (0.8 mm) and edge 2 motion 2 (1.2 mm),
        # each through |sx * scale| = 4.
        self.assertIn(0.8 * 4.0, widths)
        self.assertIn(1.2 * 4.0, widths)

    def test_a_missing_payload_width_falls_back_rather_than_stroking_nothing(self):
        ops = self.ops("drawLayer(ctx, {classes: {FILL: [[[0,150,0],[10,150,1]]]}}, "
                       "1.0, -1, false, -1, style({trueThickness: true, scale: 1.0}));")
        self.assertIn(0.4 * 2.0, [stroke[3] for stroke in self.strokes(ops)])

    def test_a_gradient_colour_mode_reads_the_payload_per_motion(self):
        """Mode 1 is the class palette; every other mode colours each
        motion from the payload, so each edge is its own stroke."""
        ops = self.ops("drawLayer(ctx, {classes: {FILL: [[[0,150,0],[10,150,1],"
                       "[20,150,2]]]}, speeds: [10, 20, 90], "
                       "colourRanges: {speed: [10, 90]}}, 1.0, -1, false, -1, "
                       "style({colourScheme: {mode: 2}}));")
        gradients = {stroke[1] for stroke in self.strokes(ops)
                     if stroke[1].startswith("rgba")}
        self.assertGreater(len(gradients), 1, "the gradient must vary with the motion")

    # --- the travels ---

    def test_the_travels_draw_their_own_class_colour_width_and_alpha(self):
        ops = self.ops("drawTravels(ctx, [[[0,150,0],[10,150,1]]], -1, -1, "
                       "'TRAVEL_RETRACTING', style({}));")
        self.assertEqual(self.strokes(ops)[0][1], "#TRAVEL_RETRACTING")
        self.assertEqual(self.strokes(ops)[0][2], 0.8)
        self.assertEqual(self.strokes(ops)[0][3], 1.0)

    def test_an_unnamed_travel_run_falls_back_to_the_travel_class(self):
        ops = self.ops("drawTravels(ctx, [[[0,150,0],[10,150,1]]], -1, -1, '', style({}));")
        self.assertEqual(self.strokes(ops)[0][1], "#TRAVEL")

    def test_the_travel_families_each_draw_and_a_plain_payload_still_does(self):
        ops = self.ops("drawTravelClasses(ctx, {travelClasses: "
                       "{TRAVEL: [[[0,150,0],[10,150,1]]], "
                       "TRAVEL_RETRACTED: [[[20,150,2],[30,150,3]]]}}, -1, -1, style({}));")
        self.assertEqual({stroke[1] for stroke in self.strokes(ops)},
                         {"#TRAVEL", "#TRAVEL_RETRACTED"})
        ops = self.ops("drawTravelClasses(ctx, {travels: [[[0,150,0],[10,150,1]]]}, "
                       "-1, -1, style({}));")
        self.assertEqual([stroke[1] for stroke in self.strokes(ops)], ["#TRAVEL"])

    def test_an_absent_travel_payload_paints_nothing_rather_than_throwing(self):
        self.assertEqual(self.ops("drawTravels(ctx, null, -1, -1, 'TRAVEL', style({}));"), [])
        self.assertEqual(self.ops("drawTravelClasses(ctx, {}, -1, -1, style({}));"), [])

    def test_the_travels_stop_at_the_split_and_start_at_the_boundary(self):
        run = "[[[0,150,0],[10,150,1],[20,150,2],[30,150,3]]]"
        # Motion 1 is below the split of 2; motion 2 is not.
        ops = self.ops("drawTravels(ctx, %s, 2, -1, 'TRAVEL', style({}));" % run)
        self.assertEqual([op[0] for op in self.vertices(ops)], ["moveTo", "lineTo"])
        ops = self.ops("drawTravels(ctx, %s, -1, 3, 'TRAVEL', style({}));" % run)
        self.assertEqual([op[0] for op in self.vertices(ops)], ["moveTo", "lineTo"])

    # --- the glyphs ---

    def test_a_glyph_draws_only_where_playback_has_already_passed(self):
        """The retraction glyphs mark events the toolhead has reached; a
        completed layer still shows the event sitting on its last motion."""
        view = ("{scale:1.0,panX:0.0,panY:0.0,plot:%s,up:true,down:true,"
                "compact:false,completed:%%s,total:%%s}" % PLOT)
        points = "[[0,150,0,true],[50,150,5,true],[100,150,9,false]]"
        ops = self.ops("drawExtruderMarkers(ctx, %s, %s, '#ink');"
                       % (points, view % (5, 10)))
        self.assertEqual(len([op for op in ops if op[0] == "moveTo"]), 1)
        ops = self.ops("drawExtruderMarkers(ctx, %s, %s, '#ink');"
                       % (points, view % (10, 10)))
        self.assertEqual(len([op for op in ops if op[0] == "moveTo"]), 3)

    def test_the_legend_toggles_select_which_direction_draws(self):
        view = ("{scale:1.0,panX:0.0,panY:0.0,plot:%s,up:%%s,down:%%s,"
                "compact:false,completed:99,total:0}" % PLOT)
        points = "[[0,150,0,true],[100,150,1,false]]"
        for up, down, expected in (("true", "true", 2), ("true", "false", 1),
                                   ("false", "true", 1), ("false", "false", 0)):
            with self.subTest(up=up, down=down):
                ops = self.ops("drawExtruderMarkers(ctx, %s, %s, '#ink');"
                               % (points, view % (up, down)))
                self.assertEqual(len([op for op in ops if op[0] == "moveTo"]), expected)

    def test_glyphs_in_one_screen_cell_collapse_to_a_single_chevron(self):
        """A dense layer would otherwise stroke thousands of overlapping
        chevrons. Opposite directions are distinct glyphs even in one
        cell."""
        view = ("{scale:1.0,panX:0.0,panY:0.0,plot:%s,up:true,down:true,"
                "compact:false,completed:99,total:0}" % PLOT)
        near = "[[0,150,0,true],[0.5,150,1,true],[1,150,2,true]]"
        ops = self.ops("drawExtruderMarkers(ctx, %s, %s, '#ink');" % (near, view))
        self.assertEqual(len([op for op in ops if op[0] == "moveTo"]), 1)
        mixed = "[[0,150,0,true],[0.5,150,1,false]]"
        ops = self.ops("drawExtruderMarkers(ctx, %s, %s, '#ink');" % (mixed, view))
        self.assertEqual(len([op for op in ops if op[0] == "moveTo"]), 2)

    def test_the_glyph_points_up_for_a_retraction_and_down_for_its_recovery(self):
        view = ("{scale:1.0,panX:0.0,panY:0.0,plot:%s,up:true,down:true,"
                "compact:false,completed:99,total:0}" % PLOT)
        up = self.ops("drawExtruderMarkers(ctx, [[0,150,0,true]], %s, '#ink');" % view)
        down = self.ops("drawExtruderMarkers(ctx, [[0,150,0,false]], %s, '#ink');" % view)
        apex_up = [op for op in up if op[0] == "moveTo"][0]
        apex_down = [op for op in down if op[0] == "moveTo"][0]
        self.assertEqual(apex_up[1], apex_down[1])
        self.assertLess(apex_up[2], apex_down[2], "the chevrons must mirror in y")

    def test_a_face_with_no_plot_draws_no_glyphs_rather_than_throwing(self):
        view = ("{scale:1.0,panX:0.0,panY:0.0,plot:null,up:true,down:true,"
                "compact:false,completed:99,total:0}")
        self.assertEqual(self.ops("drawExtruderMarkers(ctx, [[0,150,0,true]], %s, '#ink');"
                                  % view), [])


if __name__ == "__main__":
    unittest.main()
