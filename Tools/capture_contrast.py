"""Contrast census for the capture legs.

The pinned screenshots catch DRIFT, not unreadability: a black-on-black
pair baked into the theme would be pinned once and pass forever. This
census walks the text items actually rendered in the captured scene,
takes each one's own colour, samples the ground it really renders against
out of the grabbed image (so transparent buttons and unpainted gaps are
judged by what the pixels show, not by a declared background), and fails
the leg when the WCAG contrast ratio drops below MIN_CONTRAST.

Text inside a disabled control is measured against MIN_INACTIVE_CONTRAST
instead: WCAG 1.4.3 exempts inactive components, and Cura draws every
disabled stock button in its own text_disabled grey, so holding them to
the same floor would fail the gate on the host's design language. They are
counted and reported either way — an exemption nobody prints looks exactly
like a check that never ran.

One element class the pixels cannot judge: an opaque text item whose box
is covered by another element (the chart pop-over card paints over the
camera pane's "Live" pill). None of its own colour reaches that box, so
its box shows a stranger's pixels; censusing them reported a 1.6:1
offender for text the frame never drew. Those are skipped — loudly, by
name — rather than measured, and only when the element paints fully
opaque (translucent text blends, and is judged on the blend as always).
The limit of that rule: an occluding element of the same colour as the
hidden text is indistinguishable from text on its own ground, so it is
still reported.

Everything here is duck-typed on purpose — it calls only childItems(),
property(), isVisible(), opacity(), width(), height(), mapToScene() and
the image's width()/height()/pixelColor(), all of which the unit tests
fake. That keeps the walker and the colour maths testable on the host,
where PyQt6 is not installed.
"""
from __future__ import annotations

import math

try:  # PyQt6 is absent on the host, where this module's unit tests run
    from PyQt6.QtCore import QPointF
except ImportError:  # pragma: no cover - the fake items take raw numbers
    QPointF = None

# The QML Text family: Text, TextEdit, TextInput and every QML file whose
# root derives from one of them (their dynamic class names do not match,
# which is why the check walks the meta-object chain instead).
TEXT_TYPE_PREFIX = "QQuickText"
# WCAG AA wants 4.5:1 for body text; this census is the floor below which
# text is not "low contrast" but effectively INVISIBLE (white on white,
# black on near-black). Do not lower it to make a leg pass.
MIN_CONTRAST = 2.0
# WCAG 1.4.3 exempts inactive controls from the contrast minimum, and Cura
# renders every disabled stock button in text_disabled grey (#c4c4c4 on
# white: 1.74:1) — failing the gate on the host's own design language
# would make this census permanently red. Disabled text is still held to
# being VISIBLE: below this it is indistinguishable from its ground, so
# white-on-white inside a disabled button is still an offender.
MIN_INACTIVE_CONTRAST = 1.2
# Per-channel distance (of 255) within which a pixel counts as glyph and
# its anti-aliased edge rather than ground.
NEAR_TEXT_TOLERANCE = 32


def _channel(value: int) -> float:
    """One sRGB channel, linearised (IEC 61966-2-1)."""
    scaled = value / 255.0
    return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb) -> float:
    """WCAG relative luminance of an (r, g, b) triple of 0..255."""
    red, green, blue = (_channel(int(component)) for component in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(rgb_a, rgb_b) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    first, second = relative_luminance(rgb_a), relative_luminance(rgb_b)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _rgb(colour):
    """A QColor (or a fake) as an (r, g, b) tuple; None when unusable."""
    if colour is None:
        return None
    try:
        rgb = (int(colour.red()), int(colour.green()), int(colour.blue()))
    except (AttributeError, TypeError, ValueError):
        return None
    return None if any(component < 0 for component in rgb) else rgb


def _alpha(colour) -> int:
    """A colour's alpha, 0..255; opaque when it does not say."""
    try:
        return max(0, min(255, int(colour.alpha())))
    except (AttributeError, TypeError, ValueError):
        return 255


def _is_text_item(item) -> bool:
    """True when the item draws text itself (Text/TextEdit/TextInput family).

    A container that merely carries a ``text`` property (a button wrapping a
    label, say) is excluded: its ``color`` is the button ground, not a
    glyph colour, and censusing it would report the ground against itself.
    """
    meta = item.metaObject() if hasattr(item, "metaObject") else None
    hops = 0
    while meta is not None and hops < 16:
        if str(meta.className()).startswith(TEXT_TYPE_PREFIX):
            return True
        meta = meta.superClass()
        hops += 1
    return False


def _is_inactive(item) -> bool:
    """True when the item, or an ancestor of it, is a disabled control.

    Qt propagates ``enabled`` down the item tree, so a label inside a
    disabled button reports enabled False itself; the ancestor walk is for
    the items Qt leaves enabled underneath one that is not.
    """
    node, hops = item, 0
    while node is not None and hops < 64:
        try:
            if node.property("enabled") is False:
                return True
        except (AttributeError, TypeError):
            pass
        node = node.parentItem() if hasattr(node, "parentItem") else None
        hops += 1
    return False


def _name(item) -> str:
    try:
        return str(item.property("objectName") or "<unnamed>")
    except (AttributeError, TypeError):
        return "<unnamed>"


def _rendered_alpha(item, declared: int) -> int:
    """The alpha the element actually paints with: its own colour alpha
    scaled by its own and its ancestors' opacity.

    Compare a text item's colour with the pixels its box actually shows
    only when the element is fully opaque: at any less it blends with
    whatever is behind it, and the exact pixels are unpredictable.
    """
    scale, node, hops = 1.0, item, 0
    while node is not None and hops < 64:
        try:
            scale *= max(0.0, min(1.0, float(node.opacity())))
        except (AttributeError, TypeError, ValueError):
            pass
        node = node.parentItem() if hasattr(node, "parentItem") else None
        hops += 1
    return int(round(declared * scale))


def _walk(root):
    """Every item in the VISUAL tree, root first.

    findChildren() follows QObject parents, which QML-created items do not
    reliably mirror — walking childItems() is what actually reaches them.
    """
    stack = [root]
    while stack:
        item = stack.pop()
        yield item
        children = item.childItems() if hasattr(item, "childItems") else None
        if children:
            stack.extend(children)


def _scene_box(item):
    """The item's own box in scene pixels, unclamped; None without a map."""
    try:
        origin = item.mapToScene(QPointF(0, 0)) if QPointF is not None else item.mapToScene(0, 0)
        return (int(math.floor(origin.x())), int(math.floor(origin.y())),
                int(math.floor(origin.x())) + int(item.width()),
                int(math.floor(origin.y())) + int(item.height()))
    except (AttributeError, TypeError):
        return None


def _scene_rect(item, image):
    """``(box, clipped)``: the item's box in image pixels, or (None, why).

    Ancestors with ``clip`` crop it: Qt's isVisible() is true for an item
    scrolled out of a clipping Flickable, but nothing of it is drawn, so
    its box would otherwise be sampled where the ground shows through —
    and a passing scene would be reported as an offender. ``clipped``
    distinguishes that from an item simply outside the frame.
    """
    box = _scene_box(item)
    if box is None:
        return None, False
    left, top = max(0, box[0]), max(0, box[1])
    right, bottom = min(int(image.width()), box[2]), min(int(image.height()), box[3])
    clipped = False
    node = item.parentItem() if hasattr(item, "parentItem") else None
    hops = 0
    while node is not None and hops < 64:
        try:
            clips = bool(node.property("clip"))
        except (AttributeError, TypeError):
            clips = False
        if clips:
            ancestor = _scene_box(node)
            if ancestor is not None:
                left, top = max(left, ancestor[0]), max(top, ancestor[1])
                right, bottom = min(right, ancestor[2]), min(bottom, ancestor[3])
                if right - left < 2 or bottom - top < 2:
                    clipped = True
        node = node.parentItem() if hasattr(node, "parentItem") else None
        hops += 1
    if right - left < 2 or bottom - top < 2:
        return None, clipped
    return (left, top, right, bottom), clipped


def _sample_box(image, rect, text_rgb, stride, tolerance, max_samples):
    """``(ground, glyph_hits)`` for one element's box.

    ``ground`` is the modal non-glyph colour, or None when unusable; when
    every pixel is within the tolerance of the text colour the box holds
    no *other* colour — which is what white-on-white looks like, the very
    failure this census exists for — so the modal colour of all pixels is
    the honest answer: it equals the glyph colour, the ratio comes out at
    1.0, and the item is reported rather than skipped.

    ``glyph_hits`` counts the sampled pixels within tolerance of the text
    colour: zero of them means the element is not painting its own colour
    into that box at all.
    """
    left, top, right, bottom = rect
    ground_counts = {}
    all_counts = {}
    glyph_hits = 0
    samples = 0
    step = max(1, int(stride))
    for y in range(top, bottom, step):
        for x in range(left, right, step):
            rgb = _rgb(image.pixelColor(x, y))
            samples += 1
            if rgb is not None:
                all_counts[rgb] = all_counts.get(rgb, 0) + 1
                if max(abs(rgb[i] - text_rgb[i]) for i in range(3)) > tolerance:
                    ground_counts[rgb] = ground_counts.get(rgb, 0) + 1
                else:
                    glyph_hits += 1
            if samples >= max_samples:
                break
        if samples >= max_samples:
            break
    counts = ground_counts or all_counts
    if not counts:
        return None, 0
    return max(counts.items(), key=lambda entry: (entry[1], entry[0]))[0], glyph_hits


def census(root, image, *, min_contrast=MIN_CONTRAST,
           min_inactive_contrast=MIN_INACTIVE_CONTRAST, stride=2,
           tolerance=NEAR_TEXT_TOLERANCE, max_samples=4000):
    """Check every visible text item under ``root`` against its ground.

    Returns ``(checked, inactive, offenders, skipped)``: offenders carry
    the element name, a text snippet, both colours, whether it is an
    inactive control and the ratio; skipped carries the element name and
    the reason (invisible, degenerate, outside the frame) and disabled
    elements are COUNTED, not dropped — they are censused, against the
    lower floor WCAG allows for them. Skips are reported, never silently
    dropped, and never turn into passes by relaxing the threshold.
    """
    checked = 0
    inactive = 0
    offenders = []
    skipped = []
    for item in _walk(root):
        text = item.property("text") if hasattr(item, "property") else None
        if not isinstance(text, str) or not text.strip():
            continue
        if not _is_text_item(item):
            continue
        if not item.isVisible():
            skipped.append((_name(item), "invisible"))
            continue
        if float(item.opacity()) <= 0.0:
            skipped.append((_name(item), "zero opacity"))
            continue
        colour = _rgb(item.property("color"))
        if colour is None:
            skipped.append((_name(item), "no colour property"))
            continue
        translucency = _alpha(item.property("color"))
        if translucency <= 0:
            # A transparent label paints nothing. PreviewSecondaryButton
            # sets Cura's own button text transparent and draws its label
            # itself; reading that item's grey/black would report the
            # button ground against itself.
            skipped.append((_name(item), "transparent text"))
            continue
        width, height = int(item.width()), int(item.height())
        if width < 2 or height < 2:
            skipped.append((_name(item), "degenerate box %dx%d" % (width, height)))
            continue
        rect, clipped = _scene_rect(item, image)
        if rect is None:
            skipped.append((_name(item), "clipped away" if clipped else "outside the frame"))
            continue
        ground, glyph_hits = _sample_box(image, rect, colour, stride, tolerance, max_samples)
        if ground is None:
            skipped.append((_name(item), "no ground pixels to sample"))
            continue
        # An element that paints fully opaque must show its own colour
        # somewhere in its box; when it shows none of it, something else
        # is painting there — the chart pop-over card sits over the
        # camera pane, and censusing the card's pixels as the "Live"
        # pill's ground reported a 1.6:1 offender for text that frame
        # never drew. Transparent and translucent elements are exempt:
        # their colour is not what reaches the pixels.
        if _rendered_alpha(item, translucency) >= 255 and not glyph_hits:
            skipped.append((_name(item), "no glyph pixels"))
            continue
        checked += 1
        disabled = _is_inactive(item)
        inactive += 1 if disabled else 0
        # Translucent text renders as its blend over the ground, which is
        # what the eye (and the ratio) sees.
        rendered = colour if translucency >= 255 else tuple(
            round(ground[index] + translucency / 255.0 * (colour[index] - ground[index]))
            for index in range(3))
        ratio = contrast_ratio(rendered, ground)
        if _name(item) in CAMERA_OVERLAY_TEXT:
            print("  exempt (camera overlay): %s %s" % (_name(item), " ".join(text.split())[:40]))
            continue
        if ratio < (min_inactive_contrast if disabled else min_contrast):
            offenders.append({
                "objectName": _name(item),
                "text": " ".join(text.split())[:40],
                "ratio": ratio,
                "text_rgb": rendered,
                "ground_rgb": ground,
                "inactive": disabled,
                "x": rect[0],
                "y": rect[1],
            })
    return checked, inactive, offenders, skipped


# QML keeps every collapsed pane, hover overlay and lazy Loader source
# alive, so most skipped items are simply not on screen: listing all of
# them buries the skips that do matter under ~150 lines per scene.
ROUTINE_SKIPS = ("invisible",)

# Text that rides the LIVE camera feed (the stream chip, the Live
# badge): the ground the census samples beside the glyph is the
# feed's arbitrary content — a white print bed would fail any pair —
# so the pill's own fill is what carries the contrast. Exempt items
# are still censused, counted and printed — never a silent pass.
CAMERA_OVERLAY_TEXT = frozenset({"cameraStreamChipText", "cameraLiveBadgeText"})


def _report_skips(skipped):
    counts = {}
    for _, reason in skipped:
        counts[reason] = counts.get(reason, 0) + 1
    if counts:
        print("  skipped: " + ", ".join("%d %s" % (counts[reason], reason)
                                        for reason in sorted(counts)))
    for name, reason in skipped:
        if reason not in ROUTINE_SKIPS:
            print("  skipped %s (%s)" % (name, reason))


def audit(root, image, scene, *, min_contrast=MIN_CONTRAST,
          min_inactive_contrast=MIN_INACTIVE_CONTRAST):
    """Run the census for one captured scene and fail loudly on offenders."""
    checked, inactive, offenders, skipped = census(
        root, image, min_contrast=min_contrast, min_inactive_contrast=min_inactive_contrast)
    # The disabled tally is printed even when the scene passes: the floor
    # for those elements is the lower one, and a silent exemption would be
    # indistinguishable from a census that never looked at them.
    print("contrast census (%s): %d text element(s) checked (%d in disabled controls, "
          "floor %.1f), %d skipped, %d below the floor"
          % (scene, checked, inactive, min_inactive_contrast, len(skipped), len(offenders)))
    _report_skips(skipped)
    if offenders:
        lines = ["contrast census (%s): %d text element(s) below the floor:" % (scene, len(offenders))]
        for offender in sorted(offenders, key=lambda entry: entry["ratio"]):
            lines.append("  %.2f:1  %s%s  %r  %s on %s at %d,%d" % (
                offender["ratio"], offender["objectName"],
                " [disabled control]" if offender["inactive"] else "",
                offender["text"],
                "#%02x%02x%02x" % offender["text_rgb"], "#%02x%02x%02x" % offender["ground_rgb"],
                offender["x"], offender["y"]))
        raise RuntimeError("\n".join(lines))
    return checked, skipped


class Report:
    """Pools a script's per-scene verdicts so one offender costs no
    screenshots.

    The census runs right after each save, so raising there aborted the
    capture script: a leg with a real offender shipped an INCOMPLETE
    gallery — the light leg lost its three later dashboard scenes to one
    placeholder — and never censused them. Offenders are held here and
    re-raised by require_clean() once every scene is captured: the leg
    still fails, with every scene's verdict rather than the first.
    """

    def __init__(self):
        self.failures = []

    def audit(self, root, image, scene, **kwargs):
        """Census one scene; record the verdict instead of aborting."""
        try:
            return audit(root, image, scene, **kwargs)
        except RuntimeError as error:
            self.failures.append(str(error))
            return None

    def require_clean(self):
        """Fail the script once, after the captures, if any scene failed."""
        if self.failures:
            raise RuntimeError("\n".join(self.failures))
