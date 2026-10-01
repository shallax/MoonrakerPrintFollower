"""Synthesized input, and the proof that it arrived where it was aimed.

The click walks' window union, the control a name resolves to, the aim
point on a rotated body, the delivery filter that records which events the
target saw, and the QTest import with its fallbacks. The server owns the
GUI thread these run on; this owns what they do.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt6.QtGui import QGuiApplication
from .qt_test import _import_qtest
from .scene import _WALK_STATS, _main_window, _walk


def _click_windows():
    # The click walks' window union: EVERY QQuickWindow, not the
    # visibility-filtered list — a popup's window reports isVisible
    # False under the WM-less Xvfb while its content is genuinely on
    # screen (the rename dialog's verbs were unreachable through the
    # filtered walk). The main window first, then the rest by size;
    # zero-area windows are skipped. The FM dialogs render deep in
    # whichever topology this Cura uses, and the probes proved reach
    # at depth 96. A click is a deliberate per-step operation, so its
    # walk may take seconds — the stall warning applies to the
    # observation dumps, not here.
    from PyQt6.QtQuick import QQuickWindow
    windows = [w for w in QGuiApplication.topLevelWindows()
               if isinstance(w, QQuickWindow)]
    windows.sort(key=lambda w: (w is _main_window(), w.width() * w.height()),
                 reverse=True)
    for window in windows:
        if window.width() * window.height() <= 0:
            continue
        _WALK_STATS["windows"] += 1
        try:
            yield window, _walk(window.contentItem(), depth=96)
        except AttributeError:
            continue


class _DeliveryFilter(QObject):
    # The delivery-introspection filter: which mouse events the
    # target window saw during a press/release, with positions — the
    # proof that the synthesized input arrived where it was aimed.
    def __init__(self, events, parent=None):
        super().__init__(parent)
        self.events = events

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseMove):
            name = str(event.type()).split(".")[-1]
            try:
                pos = (round(event.position().x()), round(event.position().y()))
            except Exception:
                pos = None
            self.events.append((name, pos))
        return False


def _nearest_control(item):
    # The custom-button quirk: Cura's components layer labels over
    # the clickable region, so a text match resolves to the label —
    # and a press at the label's centre grabs the background, never
    # the handler. Aim at the nearest Button/MenuItem-class ANCESTOR,
    # whose region owns the click; then the nearest MouseArea
    # ancestor. An inert label whose row owns clicks in a SIBLING
    # surface behind it (the configure row's fill) resolves by a hit
    # test at the label's centre. That last tier never runs for the
    # overlay-fails-the-step proofs: their targets have interactive
    # ancestors and promote before the hit test.
    area = None
    node = item
    while node is not None:
        klass = node.metaObject().className()
        if "Button" in klass or "MenuItem" in klass:
            return node
        if area is None and "MouseArea" in klass:
            area = node
        try:
            node = node.parentItem()
        except Exception:
            break
    if area is not None:
        return area
    try:
        window = item.window()
        if window is not None:
            scene = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            hit = window.contentItem().childAt(scene.x(), scene.y())
            if hit is not None and "MouseArea" in hit.metaObject().className():
                return hit
    except Exception:
        pass
    return item


def _accepted_by(grabber, target):
    # The press counts as accepted only when the grabber IS the
    # target or a descendant of it — a scroll container (a
    # QQuickFlickable) grabs presses on disabled children for
    # flicking, and that is a refusal, not a delivery.
    if grabber is None or target is None:
        return False
    node = grabber
    while node is not None:
        if node is target:
            return True
        try:
            node = node.parentItem()
        except Exception:
            return False
    # An inert label's owner can be a covering SIBLING surface — the
    # configure row's fill sits BEHIND its title label, and a press
    # on the label grabs the fill. Accept when the grabber is a
    # MouseArea covering the target, and only when the target is
    # truly inert (no interactive ancestor — the overlay-fails-the
    # -step proofs promote to a Button before this runs, so a scrim
    # over a control still refuses).
    try:
        if "MouseArea" in grabber.metaObject().className():
            probe = target
            while probe is not None:
                klass = probe.metaObject().className()
                if "Button" in klass or "MenuItem" in klass or "MouseArea" in klass:
                    return False
                try:
                    probe = probe.parentItem()
                except Exception:
                    break
            grab = grabber.mapToScene(QPointF(0, 0))
            aim = target.mapToScene(QPointF(0, 0))
            if (grab.x() <= aim.x() and grab.y() <= aim.y()
                    and grab.x() + grabber.width() >= aim.x() + target.width()
                    and grab.y() + grabber.height() >= aim.y() + target.height()):
                return True
    except Exception:
        pass
    return False


def _identify(item):
    # An identified item for the delivery record: the objectName (or
    # None), its class, and the parent chain. The class alone said
    # "QQuickItem" for every press — the name is what discriminates.
    if item is None:
        return None
    try:
        name = item.objectName()
    except Exception:
        name = None
    chain = [item.metaObject().className()]
    node = item
    for _ in range(4):
        try:
            node = node.parentItem()
        except Exception:
            break
        if node is None:
            break
        chain.append(node.metaObject().className())
    return {"objectName": name or None, "class": chain[0], "chain": chain}


def _viewport_blocker(window, target, x, y):
    # Why a press at (x, y) cannot land on the target — None when it
    # can. mapToScene ignores clipping: an item scrolled out of its
    # pane's Flickable still reports a plausible rect while rendering
    # nowhere, and a press aimed there is a click on EMPTY SPACE that
    # surfaces only as hit=None, accepted=False (the s7 jog leg: the
    # aim sat 28px below the window's bottom edge, and the harness
    # could not tell that from a swallowed click). The check names the
    # first blocker so the step fails with the reason instead.
    try:
        root = window.contentItem()
        rw, rh = float(root.width()), float(root.height())
    except Exception:
        return None
    if not (0 <= x < rw and 0 <= y < rh):
        return (f"the aim {[x, y]} is outside the window content "
                f"({round(rw)}x{round(rh)}) — nothing can receive the press")
    node = target
    while node is not None:
        try:
            node = node.parentItem()
        except Exception:
            return None
        if node is None:
            break
        try:
            if not bool(node.property("clip")):
                continue
            local = node.mapFromItem(root, QPointF(x, y))
            w, h = float(node.width()), float(node.height())
        except Exception:
            continue
        if not (-0.5 <= local.x() <= w + 0.5 and -0.5 <= local.y() <= h + 0.5):
            try:
                origin = node.mapToScene(QPointF(0, 0))
                place = [round(origin.x()), round(origin.y())]
            except Exception:
                place = None
            label = node.property("objectName") or node.metaObject().className()
            return (f"the aim {[x, y]} is outside the clipped viewport of {label} "
                    f"({round(w)}x{round(h)} at scene {place}) — scroll the target into view first")
    return None


def _aim_point(item):
    # The item's own centre, in the window content's coordinates — the
    # space _viewport_blocker() judges and _deliver_press() presses in.
    # mapToScene(w/2, h/2) is the centre of the body AS DRAWN: adding
    # w/2, h/2 to the mapped ORIGIN mixed item axes into scene axes, so
    # a rotated control (the collapsed rails run at -90°) was aimed off
    # its own body — every press at a rail landed beside it, and every
    # rail rect was reported "NOT in view" while the rail rendered.
    try:
        centre = item.mapToScene(QPointF(float(item.width()) / 2.0,
                                         float(item.height()) / 2.0))
    except Exception:
        return None
    return (round(centre.x()), round(centre.y()))


def _aim_in_view(item):
    # Whether a press at the item's centre could actually land — the
    # observation half of _viewport_blocker, reported with every rect
    # so a "rendered" item that is scrolled out of sight reads as such
    # in the evidence instead of passing as visible.
    try:
        window = item.window()
    except Exception:
        return None
    aim = _aim_point(item)
    if aim is None:
        return None
    return _viewport_blocker(window, item, aim[0], aim[1]) is None


def _deliver_press(window, x, y, button, target=None):
    # A QTest press/release with delivery introspection. The hit is
    # the item geometrically under the aim point BEFORE the press;
    # the grabber is QQuickWindow.mouseGrabberItem() right after it —
    # and accepted means the grabber is the target's own chain. An
    # overlay (a scrim, a menu) covering the aim changes the hit and
    # the grabber, which is exactly what the overlay-fails-the-step
    # proof asserts.
    try:
        hit = window.contentItem().childAt(x, y)
    except Exception:
        hit = None
    events = []
    filt = _DeliveryFilter(events)
    window.installEventFilter(filt)
    qtest = _import_qtest()
    try:
        qtest.QTest.mousePress(window, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
        qtest.QTest.qWait(60)
        try:
            grabber = window.mouseGrabberItem()
        except Exception:
            grabber = None
        qtest.QTest.mouseRelease(window, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
        qtest.QTest.qWait(60)
    finally:
        window.removeEventFilter(filt)
    return {
        "accepted": _accepted_by(grabber, target),
        "grabber": _identify(grabber),
        "hit": _identify(hit),
        "events": events[:12],
    }


def _sample_button_state(row, label):
    # Pointer-state snapshot of one mounted fixture button.
    if row is None:
        return None
    for child in row.childItems():
        try:
            if child.property("text") != label:
                continue
        except Exception:
            continue
        state = {}
        for prop in ("pressed", "down", "hovered", "enabled", "visible"):
            try:
                state[prop] = bool(child.property(prop))
            except Exception:
                pass
        return state
    return None
