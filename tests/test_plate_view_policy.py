"""The plate camera's arithmetic, executed without a scene graph.

The bed transform, the pan clamp, the zoom's focal/eased terms, the
scope's two directions and the physical stroke width are pure, so they
are checked directly here. The Qt engine tests separately establish that
the face's camera state and its Items read these results.
"""
import json
import unittest
from pathlib import Path

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
except ImportError:
    QCoreApplication = QJSEngine = None

# A 200 x 150 mm bed plotted into a 400 x 300 face at 2 px/mm, with the
# plot offset 10 px in from the face's own top-left corner.
PLOT = ("{sx:2.0,sy:2.0,bed:{offsetX:10.0,offsetY:10.0,bedXMin:0.0,bedYMax:150.0,"
        "plotWidth:400.0,plotHeight:300.0}}")


@unittest.skipUnless(QJSEngine is not None, "PyQt6 QtQml required")
class ViewPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.engine = QJSEngine()
        source = (Path(__file__).resolve().parents[1] / "mpf" / "plate" /
                  "PlateViewPolicy.js").read_text(encoding="utf-8")
        loaded = self.engine.evaluate(source.replace(".pragma library", ""))
        self.assertFalse(loaded.isError(), loaded.toString())

    def evaluate(self, expression):
        value = self.engine.evaluate("JSON.stringify(" + expression + ")")
        self.assertFalse(value.isError(), value.toString())
        return json.loads(value.toString())

    def number(self, expression):
        value = self.engine.evaluate(expression)
        self.assertFalse(value.isError(), value.toString())
        return value.toNumber()

    def test_the_bed_transform_is_the_plot_mapping_and_yields_on_no_plot(self):
        """One transform serves the dot, the glyphs and the travels: the
        bed's own origin maps to the plot's offset, y counts down from
        bedYMax, and a face with no plot yet gets null rather than a
        point at the origin."""
        self.assertEqual(self.evaluate("toScene(%s, 0.0, 150.0)" % PLOT),
                         {"x": 10.0, "y": 10.0})
        self.assertEqual(self.evaluate("toScene(%s, 100.0, 50.0)" % PLOT),
                         {"x": 210.0, "y": 210.0})
        self.assertIsNone(self.evaluate("toScene(null, 100.0, 50.0)"))

    def test_the_plotted_bed_box_scales_from_the_plot_offset(self):
        self.assertEqual(self.evaluate("bedBox(%s.bed, 1.0)" % PLOT),
                         {"left": 10.0, "right": 410.0, "top": 10.0, "bottom": 310.0})
        self.assertEqual(self.evaluate("bedBox(%s.bed, 2.0)" % PLOT),
                         {"left": 20.0, "right": 820.0, "top": 20.0, "bottom": 620.0})

    def test_the_soft_clamp_keeps_the_margin_visible_on_every_side(self):
        """The camera moves freely while the bed shows, but never past
        the point where fewer than `margin` pixels of bed remain: a pan
        far off to one side lands exactly on that boundary, and the
        opposite direction lands on the other."""
        # At 2x the bed box runs 20..820 across a 400-wide face, so the
        # x pan may not exceed 400 - 100 - 20 = 280, nor fall below
        # 100 - 820 = -720; y runs 20..620 in a 300-tall face.
        self.assertEqual(self.evaluate(
            "softClampPan(%s, 2.0, 400.0, 300.0, 100.0, 5000.0, 5000.0)" % PLOT),
            {"x": 280.0, "y": 180.0})
        self.assertEqual(self.evaluate(
            "softClampPan(%s, 2.0, 400.0, 300.0, 100.0, -5000.0, -5000.0)" % PLOT),
            {"x": -720.0, "y": -520.0})
        self.assertEqual(self.evaluate(
            "softClampPan(%s, 2.0, 400.0, 300.0, 100.0, 12.0, -34.0)" % PLOT),
            {"x": 12.0, "y": -34.0})

    def test_an_empty_clamp_interval_and_a_missing_plot_keep_the_request(self):
        """A margin wider than the plotted bed leaves no interval to
        clamp into, and a face whose plot has not been fitted yet has no
        bed at all. Neither may snap the camera to zero."""
        self.assertEqual(self.evaluate(
            "softClampPan(%s, 1.0, 400.0, 300.0, 5000.0, 77.0, 88.0)" % PLOT),
            {"x": 77.0, "y": 88.0})
        self.assertEqual(self.evaluate(
            "softClampPan(null, 1.0, 400.0, 300.0, 100.0, 77.0, 88.0)"),
            {"x": 77.0, "y": 88.0})

    def test_the_centre_pan_puts_the_named_bed_point_at_the_middle(self):
        """The jump is unclamped on purpose — the plate may show empty
        space beyond the bed edge rather than pin the bed to an edge."""
        pan = self.evaluate("centrePan(%s, 100.0, 50.0, 1.0, 400.0, 300.0)" % PLOT)
        scene = self.evaluate("toScene(%s, 100.0, 50.0)" % PLOT)
        self.assertEqual(scene["x"] * 1.0 + pan["x"], 200.0)
        self.assertEqual(scene["y"] * 1.0 + pan["y"], 150.0)
        pan = self.evaluate("centrePan(%s, 0.0, 150.0, 3.0, 400.0, 300.0)" % PLOT)
        self.assertEqual(scene := self.evaluate("toScene(%s, 0.0, 150.0)" % PLOT),
                         {"x": 10.0, "y": 10.0})
        self.assertEqual(scene["x"] * 3.0 + pan["x"], 200.0)
        self.assertIsNone(self.evaluate("centrePan(null, 0.0, 0.0, 1.0, 400.0, 300.0)"))

    def test_the_focal_pan_holds_the_point_under_the_pointer(self):
        """The scale change is anchored: the scene point beneath the
        cursor occupies the same viewport pixel before and after."""
        for pointer, pan, from_scale, to_scale in ((120.0, -30.0, 1.5, 4.0),
                                                   (0.0, 0.0, 1.0, 20.0),
                                                   (399.0, 250.0, 6.0, 1.0)):
            with self.subTest(pointer=pointer, to=to_scale):
                bed = self.number("focalBed(%r, %r, %r)" % (pointer, pan, from_scale))
                moved = self.number("focalPan(%r, %r, %r, %r)"
                                    % (pointer, pan, from_scale, to_scale))
                self.assertAlmostEqual(bed * to_scale + moved, pointer, places=9)

    def test_the_glide_pan_carries_the_drags_taken_since_the_wheel(self):
        """Scale and pan are one transform: the focal derivation runs
        every tick, and a held drag's accumulated delta rides on top of
        it rather than fighting it."""
        self.assertEqual(self.number("glidePan(120.0, 40.0, 2.0, 0.0)"), 40.0)
        self.assertEqual(self.number("glidePan(120.0, 40.0, 2.0, 15.0)"), 55.0)
        self.assertEqual(self.number("glidePan(120.0, 40.0, 3.0, 15.0)"), 15.0)

    def test_the_ease_converges_monotonically_and_snaps_at_the_target(self):
        """The glide must terminate rather than creep: the final tick
        lands exactly on the target, and no tick overshoots it."""
        scale, ticks = 1.0, 0
        while scale != 20.0 and ticks < 500:
            nxt = self.number("easeScale(%r, 20.0, 0.30)" % scale)
            self.assertGreater(nxt, scale)
            self.assertLessEqual(nxt, 20.0)
            scale, ticks = nxt, ticks + 1
        self.assertEqual(scale, 20.0)
        self.assertLess(ticks, 40, "the ease should land in a quarter second of ticks")
        # Downward, and already-there.
        self.assertLess(self.number("easeScale(8.0, 1.0, 0.30)"), 8.0)
        self.assertEqual(self.number("easeScale(4.0, 4.0, 0.30)"), 4.0)

    def test_a_touchpad_scales_by_distance_and_a_wheel_keeps_its_notch(self):
        """Treating every touchpad pixel event as a whole notch raced
        from bed fit to the limit, so the notch keeps its 25% step while
        pixel movement scales continuously — and a small pixel delta is
        a small change."""
        self.assertAlmostEqual(self.number("wheelScale(0, 120, 1.0, 20.0)"), 1.25)
        self.assertAlmostEqual(self.number("wheelScale(0, -120, 4.0, 20.0)"), 3.2)
        self.assertAlmostEqual(self.number("wheelScale(180, 0, 1.0, 20.0)"), 1.25)
        notch = self.number("wheelScale(0, 120, 1.0, 20.0)")
        self.assertLess(self.number("wheelScale(10, 0, 1.0, 20.0)"), notch)

    def test_the_wheel_stays_inside_the_fit_and_the_cap(self):
        self.assertEqual(self.number("wheelScale(0, -120, 1.0, 20.0)"), 1.0)
        self.assertEqual(self.number("wheelScale(0, 120, 20.0, 20.0)"), 20.0)
        self.assertEqual(self.number("wheelScale(3600, 0, 5.0, 20.0)"), 20.0)

    def test_the_scope_track_and_the_scale_are_inverses_with_the_fit_at_the_bottom(self):
        """The bar's bottom is the 100% fit and its top is the cap; a
        position read as a scale and put back as a fraction returns the
        same place on the track."""
        self.assertEqual(self.number("scaleFromTrack(300.0, 300.0, 20.0)"), 1.0)
        self.assertAlmostEqual(self.number("scaleFromTrack(0.0, 300.0, 20.0)"), 20.0)
        self.assertEqual(self.number("trackFraction(1.0, 20.0)"), 0.0)
        self.assertAlmostEqual(self.number("trackFraction(20.0, 20.0)"), 1.0)
        for y in (0.0, 37.0, 150.0, 299.0, 300.0):
            with self.subTest(y=y):
                scale = self.number("scaleFromTrack(%r, 300.0, 20.0)" % y)
                fraction = self.number("trackFraction(%r, 20.0)" % scale)
                self.assertAlmostEqual(300.0 - fraction * 300.0, y, places=9)
        # Off the track's ends the fraction is held, never extrapolated.
        self.assertEqual(self.number("scaleFromTrack(600.0, 300.0, 20.0)"), 1.0)
        self.assertAlmostEqual(self.number("scaleFromTrack(-600.0, 300.0, 20.0)"), 20.0)

    def test_the_graduations_run_from_the_fit_to_the_cap_in_steps(self):
        steps = self.evaluate("graduations(20.0, 0.25)")
        self.assertEqual(steps[0], 1.0)
        self.assertAlmostEqual(steps[-1], 20.0)
        self.assertEqual(len(steps), 77)
        majors = [v for v in steps if round(v * 100) % 100 == 0]
        self.assertEqual(len(majors), 20)
        self.assertEqual(self.evaluate("graduations(2.0, 0.5)"), [1.0, 1.5, 2.0])

    def test_the_physical_stroke_scales_with_the_plot_the_zoom_and_the_line_scale(self):
        """The ink is bed geometry, not screen pixels: doubling the
        zoom doubles the stroke, and so does doubling the line scale."""
        params = ("{pixelLineWidth:false,lineScale:0.7,plot:%s,nominalMm:0.2,"
                  "compactBoost:7.0,view:{scale:%%r,lineScale:%%r,compact:%%s,dpr:1.0}}"
                  % PLOT)
        base = self.number("toolpathWidth(%s)" % (params % (5.0, 1.0, "false")))
        self.assertAlmostEqual(base, 0.2 * 2.0 * 5.0)
        self.assertAlmostEqual(
            self.number("toolpathWidth(%s)" % (params % (10.0, 1.0, "false"))), base * 2)
        self.assertAlmostEqual(
            self.number("toolpathWidth(%s)" % (params % (5.0, 2.0, "false"))), base * 2)
        # The mini is a fixed-size thumbnail: honest physical strokes
        # there are invisible, so the compact face boosts the weight.
        self.assertAlmostEqual(
            self.number("toolpathWidth(%s)" % (params % (5.0, 1.0, "true"))), base * 7.0)

    def test_the_device_coverage_floor_matches_the_native_pen_and_follows_dpr(self):
        """A sub-floor stroke must present at the same full-intensity
        footprint on both renderers: without the floor the native
        raster's backed downscale faded thin strokes while the canvas
        stroked them raw, and the two halves of one layer read as
        different inks. An absent dpr reads as 1."""
        params = ("{pixelLineWidth:false,lineScale:0.7,plot:%s,nominalMm:0.2,"
                  "compactBoost:7.0,view:{scale:0.01,lineScale:1.0,compact:false%%s}}"
                  % PLOT)
        self.assertEqual(self.number("toolpathWidth(%s)" % (params % ",dpr:1.0")), 1.0)
        self.assertEqual(self.number("toolpathWidth(%s)" % (params % ",dpr:2.0")), 1.0)
        self.assertEqual(self.number("toolpathWidth(%s)" % (params % ",dpr:4.0")), 0.5)
        self.assertEqual(self.number("toolpathWidth(%s)" % (params % "")), 1.0)
        # A dpr below 1 can never raise the floor above one logical pixel.
        self.assertEqual(self.number("toolpathWidth(%s)" % (params % ",dpr:0.5")), 1.0)

    def test_the_pixel_width_request_is_a_clamped_integer_count(self):
        """The debug/legacy request is a screen count, so it ignores the
        plot entirely — including a face with no plot at all."""
        for asked, expected in ((0.4, 1), (1.0, 1), (3.4, 3), (3.6, 4), (99.0, 8)):
            with self.subTest(asked=asked):
                self.assertEqual(self.number(
                    "toolpathWidth({pixelLineWidth:true,lineScale:%r,plot:null})" % asked),
                    expected)

    def test_a_face_with_no_plot_has_no_geometry_stroke_to_report(self):
        self.assertEqual(self.number(
            "toolpathWidth({pixelLineWidth:false,lineScale:0.7,plot:null,nominalMm:0.2,"
            "compactBoost:7.0,view:{scale:1.0,lineScale:1.0,compact:false,dpr:1.0}})"), 0)

    def test_the_travel_width_is_a_visual_ratio_over_the_same_basis(self):
        self.assertAlmostEqual(self.number("travelWidth(2.0, 0.7, false)"), 1.4)
        self.assertEqual(self.number("travelWidth(2.0, 0.7, true)"), 1.0)
        self.assertEqual(self.number("travelWidth(0.0, 0.7, false)"), 0.0)

    def test_the_zoom_cap_is_one_constant_for_the_wheel_and_the_scope(self):
        """The scope's graduations, its track and the wheel's ceiling
        must not drift apart: they read the same cap."""
        self.assertEqual(self.number("MAX_SCALE"), 20.0)
        self.assertAlmostEqual(self.evaluate("graduations(MAX_SCALE, 0.25)")[-1],
                               self.number("MAX_SCALE"))
        self.assertAlmostEqual(self.number("scaleFromTrack(0.0, 300.0, MAX_SCALE)"),
                               self.number("wheelScale(3600, 0, 5.0, MAX_SCALE)"))


if __name__ == "__main__":
    unittest.main()
