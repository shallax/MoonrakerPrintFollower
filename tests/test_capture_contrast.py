"""The capture contrast census, tested without a Qt install.

The census runs inside the capture legs (the dev container), but its maths
and its walker are pure Python over duck-typed items, so the rules that
decide pass/fail are pinned here on the host: the WCAG ratio, which items
count as text, what counts as the ground, and what is skipped instead of
silently passing.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import capture_contrast  # noqa: E402


class FakeMeta:
    """A meta-object chain: className() + superClass(), like QMetaObject."""

    def __init__(self, name, parent=None):
        self._name = name
        self._parent = parent

    def className(self):
        return self._name

    def superClass(self):
        return self._parent


class FakeColour:
    def __init__(self, rgb, alpha=255):
        self._rgb = tuple(rgb)
        self._alpha = alpha

    def alpha(self):
        return self._alpha

    def red(self):
        return self._rgb[0]

    def green(self):
        return self._rgb[1]

    def blue(self):
        return self._rgb[2]

    def name(self):
        return "#%02x%02x%02x" % self._rgb


class FakePoint:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class FakeItem:
    def __init__(self, text=None, colour=(0, 0, 0), alpha=255, x=0, y=0, width=20, height=10,
                 visible=True, opacity=1.0, object_name=None, children=(),
                 enabled=True, parent=None, clip=False,
                 meta_chain=("Label_QMLTYPE_11", "QQuickText", "QQuickItem", "QObject")):
        self._text = text
        self._colour = FakeColour(colour, alpha) if colour is not None else None
        self._x, self._y = x, y
        self._width, self._height = width, height
        self._visible, self._opacity = visible, opacity
        self._object_name = object_name
        self._children = list(children)
        self._enabled, self._parent, self._clip = enabled, parent, clip
        chain = None
        for name in reversed(meta_chain):
            chain = FakeMeta(name, chain)
        self._meta = chain

    def property(self, name):
        if name == "text":
            return self._text
        if name == "color":
            return self._colour
        if name == "objectName":
            return self._object_name
        if name == "enabled":
            return self._enabled
        if name == "clip":
            return self._clip
        return None

    def childItems(self):
        return list(self._children)

    def parentItem(self):
        return self._parent

    def isVisible(self):
        return self._visible

    def opacity(self):
        return self._opacity

    def width(self):
        return self._width

    def height(self):
        return self._height

    def mapToScene(self, *args):
        return FakePoint(self._x, self._y)

    def metaObject(self):
        return self._meta


class FakeImage:
    """A frame of one colour, optionally striped with others.

    ``stripe`` paints a band of a foreign colour across the frame (an
    occluding card, a neighbour's fill); ``glyphs`` paints bands in the
    elements' own colours where their glyphs would be. A frame carrying
    none of an element's own colour is a frame that element does not
    paint into — the census reads that as covered and skips it — so every
    element a test expects to be MEASURED needs its colour in the frame.
    """

    def __init__(self, width, height, fill=(255, 255, 255), stripe=None,
                 stripe_rows=(), stripe_cols=(), glyphs=()):
        self._width, self._height = width, height
        self._fill = tuple(fill)
        self._stripe = tuple(stripe) if stripe is not None else None
        self._rows, self._cols = set(stripe_rows), set(stripe_cols)
        self._glyphs = [(tuple(rgb), set(rows)) for rgb, rows in glyphs]

    def width(self):
        return self._width

    def height(self):
        return self._height

    def pixelColor(self, x, y):
        for rgb, rows in self._glyphs:
            if y in rows:
                return FakeColour(rgb)
        if self._stripe is not None and (y in self._rows or x in self._cols):
            return FakeColour(self._stripe)
        return FakeColour(self._fill)


class ContrastMathTests(unittest.TestCase):
    def test_luminance_endpoints(self):
        self.assertAlmostEqual(capture_contrast.relative_luminance((0, 0, 0)), 0.0, places=6)
        self.assertAlmostEqual(capture_contrast.relative_luminance((255, 255, 255)), 1.0, places=6)

    def test_luminance_is_channel_weighted(self):
        green = capture_contrast.relative_luminance((0, 255, 0))
        red = capture_contrast.relative_luminance((255, 0, 0))
        blue = capture_contrast.relative_luminance((0, 0, 255))
        self.assertGreater(green, red)
        self.assertGreater(red, blue)

    def test_contrast_ratio_endpoints(self):
        self.assertAlmostEqual(capture_contrast.contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, places=4)
        self.assertAlmostEqual(capture_contrast.contrast_ratio((255, 255, 255), (0, 0, 0)), 21.0, places=4)

    def test_contrast_ratio_of_identical_colours_is_one(self):
        for rgb in ((0, 0, 0), (255, 255, 255), (25, 25, 25), (39, 44, 48)):
            self.assertAlmostEqual(capture_contrast.contrast_ratio(rgb, rgb), 1.0, places=6)

    def test_contrast_ratio_is_symmetric(self):
        first = capture_contrast.contrast_ratio((25, 25, 25), (174, 174, 174))
        second = capture_contrast.contrast_ratio((174, 174, 174), (25, 25, 25))
        self.assertAlmostEqual(first, second, places=9)


class CensusWalkerTests(unittest.TestCase):
    def census(self, root, image, **kwargs):
        # The walker also returns how many censused elements sat in
        # disabled controls; these tests are about pass/fail, so they read
        # the three-way verdict.
        checked, _, offenders, skipped = capture_contrast.census(root, image, **kwargs)
        return checked, offenders, skipped

    def test_dark_text_on_white_ground_passes(self):
        item = FakeItem(text="Printer status", colour=(25, 25, 25), width=100, height=16)
        image = FakeImage(200, 50, fill=(255, 255, 255), glyphs=[((25, 25, 25), range(6, 10))])
        checked, offenders, skipped = self.census(item, image)
        self.assertEqual(checked, 1)
        self.assertEqual(offenders, [])
        self.assertEqual(skipped, [])

    def test_white_text_on_white_ground_is_an_offender(self):
        item = FakeItem(text="EMERGENCY STOP", colour=(255, 255, 255), width=100, height=16,
                        object_name="moonrakerEmergencyButton")
        checked, offenders, skipped = self.census(item, FakeImage(200, 50, fill=(255, 255, 255)))
        self.assertEqual(checked, 1)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0]["objectName"], "moonrakerEmergencyButton")
        self.assertEqual(offenders[0]["text"], "EMERGENCY STOP")
        self.assertAlmostEqual(offenders[0]["ratio"], 1.0, places=6)
        self.assertEqual(offenders[0]["ground_rgb"], (255, 255, 255))

    def test_unnamed_offender_is_reported_as_unnamed(self):
        item = FakeItem(text="x", colour=(255, 255, 255), width=40, height=20)
        _, offenders, _ = self.census(item, FakeImage(60, 40, fill=(250, 250, 250)))
        self.assertEqual(offenders[0]["objectName"], "<unnamed>")

    def test_glyph_pixels_are_excluded_from_the_ground(self):
        # A white ground with a black glyph band through the text's own
        # colour: the ground must be read as white, not as the glyph.
        image = FakeImage(200, 40, fill=(255, 255, 255), stripe=(0, 0, 0), stripe_rows=range(10, 16))
        item = FakeItem(text="Console", colour=(0, 0, 0), width=100, height=20, y=8)
        checked, offenders, _ = self.census(item, image)
        self.assertEqual(checked, 1)
        self.assertEqual(offenders, [])

    def test_anti_aliased_edge_between_text_and_ground_is_excluded(self):
        # A near-text grey (inside the tolerance) must not become the ground.
        near = (225, 225, 225)  # within the tolerance of the 205-grey text
        image = FakeImage(200, 40, fill=(255, 255, 255), stripe=near, stripe_rows=range(4, 8))
        item = FakeItem(text="Close", colour=(205, 205, 205), width=80, height=16)
        _, offenders, _ = self.census(item, image)
        self.assertEqual(len(offenders), 1)  # 205-grey on white is below the floor
        self.assertEqual(offenders[0]["ground_rgb"], (255, 255, 255))

    def test_invisible_items_are_skipped_and_noted(self):
        item = FakeItem(text="Hidden", colour=(255, 255, 255), visible=False, width=40, height=16,
                        object_name="hiddenLabel")
        checked, offenders, skipped = self.census(item, FakeImage(60, 40, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("hiddenLabel", "invisible")])

    def test_zero_opacity_items_are_skipped_and_noted(self):
        item = FakeItem(text="Faded", colour=(255, 255, 255), opacity=0.0, width=40, height=16)
        checked, offenders, skipped = self.census(item, FakeImage(60, 40, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("<unnamed>", "zero opacity")])

    def test_degenerate_box_is_skipped_and_noted(self):
        item = FakeItem(text="I", colour=(255, 255, 255), width=1, height=16, object_name="caret")
        checked, offenders, skipped = self.census(item, FakeImage(60, 40, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("caret", "degenerate box 1x16")])

    def test_item_outside_the_frame_is_skipped(self):
        item = FakeItem(text="Off-screen", colour=(255, 255, 255), x=500, y=500, width=40, height=16)
        checked, offenders, skipped = self.census(item, FakeImage(60, 40, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("<unnamed>", "outside the frame")])

    def test_item_clipped_away_by_a_clipping_ancestor_is_skipped(self):
        # Qt's isVisible() is true for an item scrolled out of a clipping
        # Flickable, but nothing of it is drawn: sampling its box would
        # read the ground showing through and report a false offender.
        viewport = FakeItem(text=None, colour=None, x=0, y=0, width=100, height=40, clip=True)
        scrolled = FakeItem(text="Console output interval", colour=(255, 255, 255),
                            x=0, y=200, width=80, height=16, parent=viewport,
                            object_name="scrolledLabel")
        checked, offenders, skipped = self.census(scrolled, FakeImage(200, 300, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("scrolledLabel", "clipped away")])

    def test_item_inside_its_clipping_ancestor_is_still_censused(self):
        viewport = FakeItem(text=None, colour=None, x=0, y=0, width=200, height=100, clip=True)
        visible = FakeItem(text="Readable", colour=(25, 25, 25), x=10, y=10, width=80, height=16,
                           parent=viewport, object_name="visibleLabel")
        image = FakeImage(200, 100, fill=(255, 255, 255), glyphs=[((25, 25, 25), range(16, 20))])
        checked, offenders, skipped = self.census(visible, image)
        self.assertEqual((checked, offenders, skipped), (1, [], []))

    def test_transparent_text_is_skipped_and_noted(self):
        # PreviewSecondaryButton draws its own label and sets Cura's
        # button text transparent; the item is still visible to Qt, and
        # reading its RGB would report the button ground against itself.
        item = FakeItem(text="Detach", colour=(0, 0, 0), alpha=0, width=80, height=16,
                        object_name="nativeButtonLabel")
        image = FakeImage(200, 40, fill=(45, 45, 46))
        checked, offenders, skipped = self.census(item, image)
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("nativeButtonLabel", "transparent text")])

    def test_translucent_text_is_judged_on_its_blend_over_the_ground(self):
        # The same black text at 16% opacity is nearly invisible on white;
        # opaque it is the best contrast there is.
        image = FakeImage(200, 40, fill=(255, 255, 255))
        faint = FakeItem(text="Faint", colour=(0, 0, 0), alpha=40, width=80, height=16)
        opaque = FakeItem(text="Solid", colour=(0, 0, 0), width=80, height=16)
        _, offenders_faint, _ = self.census(faint, image)
        _, offenders_opaque, _ = self.census(opaque, image)
        self.assertEqual(len(offenders_faint), 1)
        self.assertEqual(offenders_opaque, [])

    def test_item_without_a_colour_property_is_skipped(self):
        item = FakeItem(text="No colour", colour=None, width=40, height=16)
        checked, offenders, skipped = self.census(item, FakeImage(60, 40, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("<unnamed>", "no colour property")])

    def test_container_carrying_a_text_property_is_not_censused(self):
        # PrimaryButton-style: a Rectangle whose own colour is the button
        # ground, wrapping the label that actually draws the text.
        label = FakeItem(text="Upload", colour=(25, 25, 25), width=60, height=16)
        button = FakeItem(text="Upload", colour=(25, 110, 240), width=80, height=28,
                          children=[label], object_name="uploadButton",
                          meta_chain=("PrimaryButton_QMLTYPE_68", "QQuickRectangle",
                                      "QQuickItem", "QObject"))
        image = FakeImage(120, 60, fill=(255, 255, 255), glyphs=[((25, 25, 25), range(6, 10))])
        checked, offenders, _ = self.census(button, image)
        self.assertEqual(checked, 1)          # only the label
        self.assertEqual(offenders, [])

    def test_blank_text_is_not_censused(self):
        item = FakeItem(text="   ", colour=(255, 255, 255), width=40, height=16)
        checked, offenders, skipped = self.census(item, FakeImage(60, 40, fill=(255, 255, 255)))
        self.assertEqual((checked, offenders, skipped), (0, [], []))

    def test_nested_items_are_visited(self):
        inner = FakeItem(text="Inner", colour=(255, 255, 255), width=40, height=16, object_name="inner")
        outer = FakeItem(text=None, colour=None, width=100, height=40, children=[inner])
        checked, offenders, _ = self.census(outer, FakeImage(120, 60, fill=(255, 255, 255)))
        self.assertEqual(checked, 1)
        self.assertEqual(offenders[0]["objectName"], "inner")

    def test_intraline_whitespace_is_collapsed_in_the_snippet(self):
        item = FakeItem(text="EMERGENCY   STOP\n  — hold", colour=(255, 255, 255),
                        width=120, height=16)
        _, offenders, _ = self.census(item, FakeImage(160, 40, fill=(255, 255, 255)))
        self.assertEqual(offenders[0]["text"], "EMERGENCY STOP — hold")

    def test_audit_passes_a_readable_scene_silently(self):
        item = FakeItem(text="Readable", colour=(25, 25, 25), width=80, height=16)
        image = FakeImage(120, 40, fill=(255, 255, 255), glyphs=[((25, 25, 25), range(6, 10))])
        capture_contrast.audit(item, image, "unit")

    def test_audit_raises_with_the_offender_list(self):
        item = FakeItem(text="Invisible", colour=(255, 255, 255), width=80, height=16,
                        object_name="ghostLabel")
        with self.assertRaises(RuntimeError) as caught:
            capture_contrast.audit(item, FakeImage(120, 40, fill=(255, 255, 255)), "unit")
        message = str(caught.exception)
        self.assertIn("ghostLabel", message)
        self.assertIn("Invisible", message)
        self.assertIn("unit", message)

    def test_threshold_is_the_documented_floor(self):
        self.assertEqual(capture_contrast.MIN_CONTRAST, 2.0)

    def test_disabled_control_text_is_held_to_the_inactive_floor(self):
        # Cura's own disabled stock-button grey (196,196,196) on white is
        # 1.74:1 — below the active floor, above the inactive one. The
        # only difference between these two items is `enabled`.
        image = FakeImage(120, 40, fill=(255, 255, 255),
                          glyphs=[((196, 196, 196), range(6, 10))])
        live = FakeItem(text="Home X", colour=(196, 196, 196), width=80, height=16)
        disabled = FakeItem(text="Home X", colour=(196, 196, 196), width=80, height=16,
                            enabled=False)
        _, offenders_live, _ = self.census(live, image)
        _, offenders_disabled, _ = self.census(disabled, image)
        self.assertEqual(len(offenders_live), 1)
        self.assertEqual(offenders_disabled, [])

    def test_white_on_white_inside_a_disabled_control_still_offends(self):
        item = FakeItem(text="EMERGENCY STOP", colour=(255, 255, 255), width=100, height=16,
                        object_name="moonrakerEmergencyButton", enabled=False)
        _, offenders, _ = self.census(item, FakeImage(200, 50, fill=(255, 255, 255)))
        self.assertEqual(len(offenders), 1)
        self.assertTrue(offenders[0]["inactive"])

    def test_a_disabled_ancestor_lowers_the_floor_for_its_label(self):
        # Qt propagates `enabled`, so the label usually reports it itself;
        # this covers the items it leaves enabled underneath a disabled
        # parent (the label's own property is untouched here).
        button = FakeItem(text=None, colour=None, width=100, height=30, enabled=False)
        label = FakeItem(text="Home X", colour=(196, 196, 196), width=80, height=16, parent=button)
        image = FakeImage(200, 50, fill=(255, 255, 255),
                          glyphs=[((196, 196, 196), range(6, 10))])
        checked, offenders, _ = self.census(label, image)
        self.assertEqual(checked, 1)          # measured, not skipped as covered
        self.assertEqual(offenders, [])

    def test_inactive_floor_is_the_documented_value(self):
        self.assertEqual(capture_contrast.MIN_INACTIVE_CONTRAST, 1.2)

    def test_census_counts_the_elements_in_disabled_controls(self):
        image = FakeImage(200, 60, fill=(255, 255, 255), glyphs=[
            ((25, 25, 25), range(6, 10)), ((196, 196, 196), range(36, 40))])
        root = FakeItem(text=None, colour=None, width=200, height=60, children=[
            FakeItem(text="Live", colour=(25, 25, 25), width=80, height=16),
            FakeItem(text="Disabled", colour=(196, 196, 196), width=80, height=16, y=30,
                     enabled=False),
        ])
        checked, inactive, offenders, _ = capture_contrast.census(root, image)
        self.assertEqual((checked, inactive, offenders), (2, 1, []))

    def test_audit_prints_the_disabled_tally_even_when_it_passes(self):
        # The exemption must be visible in the leg's log: a silent one is
        # indistinguishable from text the census never looked at.
        image = FakeImage(120, 40, fill=(255, 255, 255),
                          glyphs=[((196, 196, 196), range(6, 10))])
        item = FakeItem(text="Home X", colour=(196, 196, 196), width=80, height=16, enabled=False)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            capture_contrast.audit(item, image, "unit")
        self.assertIn("1 in disabled controls", printed.getvalue())

    def test_occluded_text_is_skipped_instead_of_offending(self):
        # The chart pop-over card covers the camera pane: the "Live" pill's
        # box shows the CARD's pixels (a pale chart fill), none of its own
        # white. Measuring them reported white-on-pink as an offender for
        # text this frame never drew.
        image = FakeImage(200, 60, fill=(241, 193, 193))
        item = FakeItem(text="Live", colour=(255, 255, 255), width=23, height=13,
                        object_name="cameraLiveLabel")
        checked, offenders, skipped = self.census(item, image)
        self.assertEqual((checked, offenders), (0, []))
        self.assertEqual(skipped, [("cameraLiveLabel", "no glyph pixels")])

    def test_white_glyphs_on_a_pale_ground_still_offend(self):
        # The same colours, actually drawn: the box carries the white glyph
        # pixels, so the element is measured — and it is unreadable.
        image = FakeImage(200, 60, fill=(241, 193, 193), stripe=(255, 255, 255),
                          stripe_rows=range(2, 6))
        item = FakeItem(text="Live", colour=(255, 255, 255), width=23, height=13)
        checked, offenders, _ = self.census(item, image)
        self.assertEqual(checked, 1)
        self.assertLess(offenders[0]["ratio"], 2.0)
        self.assertEqual(offenders[0]["ground_rgb"], (241, 193, 193))

    def test_an_element_transparent_to_its_ancestor_is_not_held_to_the_glyph_test(self):
        # An ancestor at half opacity means the pixels are the alpha blend,
        # so "no pixel matches my colour" says nothing about whether the
        # element is drawn.
        parent = FakeItem(text=None, colour=None, width=200, height=60, opacity=0.5)
        item = FakeItem(text="Live", colour=(255, 255, 255), width=23, height=13, parent=parent)
        checked, offenders, _ = self.census(item, FakeImage(200, 60, fill=(241, 193, 193)))
        self.assertEqual(checked, 1)
        self.assertEqual(len(offenders), 1)

    def test_ratio_just_below_the_floor_offends_and_just_above_passes(self):
        # 183/184-grey on white sits either side of 2.0: the walker must
        # not round a borderline element into a pass.
        below = FakeItem(text="Borderline", colour=(184, 184, 184), width=80, height=16)
        above = FakeItem(text="Borderline", colour=(183, 183, 183), width=80, height=16)
        image_below = FakeImage(120, 40, fill=(255, 255, 255),
                                glyphs=[((184, 184, 184), range(6, 10))])
        image_above = FakeImage(120, 40, fill=(255, 255, 255),
                                glyphs=[((183, 183, 183), range(6, 10))])
        _, offenders_below, _ = self.census(below, image_below)
        _, offenders_above, _ = self.census(above, image_above)
        self.assertEqual(len(offenders_below), 1)
        self.assertEqual(offenders_above, [])


class ReportTests(unittest.TestCase):
    """The per-scene report: one offender must not cost the other scenes
    their screenshots, and the leg must still fail."""

    def setUp(self):
        self.image = FakeImage(120, 40, fill=(255, 255, 255))
        self.offending = FakeItem(text="Invisible", colour=(255, 255, 255), width=80, height=16,
                                  object_name="ghostLabel")
        self.readable = FakeItem(text="Readable", colour=(25, 25, 25), width=80, height=16)

    def test_a_clean_report_passes_silently(self):
        report = capture_contrast.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            report.audit(self.readable, self.image, "unit")
        report.require_clean()

    def test_an_offending_scene_does_not_stop_the_leg(self):
        # The capture script keeps going: the census must not raise here,
        # or the scenes after the offender are never captured.
        report = capture_contrast.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(report.audit(self.offending, self.image, "unit-1"))
            report.audit(self.readable, self.image, "unit-2")
        self.assertEqual(len(report.failures), 1)

    def test_require_clean_reports_every_offending_scene(self):
        report = capture_contrast.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            report.audit(self.offending, self.image, "unit-1")
            report.audit(self.offending, self.image, "unit-2")
        with self.assertRaises(RuntimeError) as caught:
            report.require_clean()
        message = str(caught.exception)
        self.assertIn("unit-1", message)
        self.assertIn("unit-2", message)
        self.assertIn("ghostLabel", message)

    def test_a_failing_scene_is_still_censused_after_a_clean_one(self):
        report = capture_contrast.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            report.audit(self.readable, self.image, "unit-1")
            report.audit(self.offending, self.image, "unit-2")
        with self.assertRaises(RuntimeError) as caught:
            report.require_clean()
        self.assertIn("unit-2", str(caught.exception))

    def test_the_report_returns_the_census_verdicts(self):
        report = capture_contrast.Report()
        image = FakeImage(120, 40, fill=(255, 255, 255), glyphs=[((25, 25, 25), range(6, 10))])
        with contextlib.redirect_stdout(io.StringIO()):
            checked, skipped = report.audit(self.readable, image, "unit")
        self.assertEqual((checked, skipped), (1, []))


if __name__ == "__main__":
    unittest.main()
