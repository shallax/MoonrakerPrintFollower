# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Share and verify canonical recovery actions across confirmation and execution.
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation

"""Real recovery screens with synthetic G-code; no network or printer commands."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QObject, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PyQt6.QtQuick import QQuickWindow

from mpf.application.RecoveryPlanning import scan_source
from mpf.printing.RecoveryReview import review_recovery
from tools import capture_contrast, theme_support
from tools.capture_preview_toolhead import IconProvider


class Printer(QObject):
    changed = pyqtSignal()

    def __init__(self, source):
        super().__init__()
        self.plan = dict(ready=True, active=False, phase="ready", detail="G-code and printer profile checked.",
            unloaded=False, filename="example-bracket.gcode", offset=source.boundary_offset, resumeLayer=50, resumeMove=8,
            x=source.state.position[0], y=source.state.position[1], z=source.state.position[2], currentX=0, currentY=250, currentZ=20,
            e=source.state.e, absoluteE=source.state.absolute_e, hotendTarget=210, bedTarget=60, clearance=5,
            xySpeed=50, zSpeed=5, primeEnabled=True, primeX=0, primeY=250, primeZ=100,
            primeAmount=20, primeSpeed=5, primeDwell=5, maximumZ=250,
            bedBounds=[0, 0, 0, 250, 250], previewSegments=[list(segment) for segment in source.preview_segments],
            previewCaption="Recorded G-code progress; actual material may be missing")

    @pyqtSlot(QVariant, result=QVariant)
    def reviewRecovery(self, supplied):
        supplied = supplied.toVariant() if hasattr(supplied, 'toVariant') else supplied
        return dict(review_recovery(self.plan, supplied).document(), token="capture-only")

    @pyqtProperty(int, constant=True)
    def plateLayerCount(self): return 150
    @pyqtProperty(int, constant=True)
    def plateLayerMotionCount(self): return 8
    @pyqtProperty(QVariant, notify=changed)
    def recoveryPlan(self): return self.plan
    @pyqtProperty(QVariant, constant=True)
    def recoverySelection(self):
        return dict(layer=50, completedMotions=7, offset=self.plan["offset"], x=self.plan["x"], y=self.plan["y"], z=self.plan["z"])
    @pyqtProperty(QVariant, constant=True)
    def recoveryEvents(self):
        return [dict(kind="manual", layer=49, split=7, observed_at=123)]
    @pyqtProperty(QVariant, constant=True)
    def recoveryPreviousAttempt(self): return {}
    @pyqtProperty(str, constant=True)
    def recoveryPauseNote(self): return ""


def pump(app):
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)


def geometry(folder):
    lines = ["G90", "M83", "G1 X80 Y90 Z0 F1800"]
    for layer in range(1, 51):
        lines += [f";LAYER:{layer - 1}", f"G1 Z{layer * .2:.1f}", "G1 X160 Y90 E2", "G1 X160 Y110 E2",
                  "G1 X120 Y110 E2", "G1 X120 Y150 E2", "G1 X80 Y150 E2", "G1 X80 Y90 E2"]
    prefix = ("\n".join(lines) + "\n").encode()
    path = Path(folder) / "example.gcode"
    path.write_bytes(prefix + b"G1 X120 E2\n")
    plan = scan_source(path, SimpleNamespace(next_motion_offset=len(prefix), position=(80, 90, 10)))
    return plan


def capture(app, output, theme_name, suffix):
    with tempfile.TemporaryDirectory(prefix="mpf-recovery-capture-") as folder:
        theme = theme_support.ThemeBackend(str(ROOT / "tests/theme_assets" / theme_name))
        overlay = theme_support.materialise_theme_assets(folder, theme)
        engine = QQmlEngine()
        engine.addImageProvider("toolhead-tint", IconProvider())
        warnings = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        for path in (ROOT / "tests/qml_stubs", ROOT / "mpf", Path(overlay)):
            engine.addImportPath(str(path))
        theme_support.verify_capture_tree(engine, theme)
        engine.rootContext().setContextProperty("screenScaleFactor", 1.0)
        printer = Printer(geometry(folder))
        components, items = [], []
        window = QQuickWindow()
        window.setColor(theme.getColor("main_background"))
        window.resize(600, 780)
        window.show()

        def create(filename):
            component = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT / "mpf/plate" / filename)))
            if component.isError():
                raise RuntimeError("\n".join(e.toString() for e in component.errors()))
            item = component.create()
            components.append(component)
            items.append(item)
            item.setParentItem(window.contentItem())
            item.setX(20)
            item.setY(20)
            item.setWidth(560)
            item.setHeight(740)
            return item

        def save(name, popup=None):
            pump(app)
            image = window.grabWindow()
            if image.isNull():
                raise RuntimeError("capture failed: " + name)
            capture_contrast.audit(popup.property("contentItem") if popup else window.contentItem(), image, name)
            if popup:
                image = image.copy(round(controls.x() + popup.property("x")),
                                   round(controls.y() + popup.property("y")),
                                   round(popup.property("width")), round(popup.property("height")))
            if not image.save(str(output / (name + suffix + ".png"))):
                raise RuntimeError("capture save failed: " + name)
            if warnings:
                raise RuntimeError("\n".join(warnings))

        controls = create("RecoveryControls.qml")
        controls.setProperty("printerModel", printer)
        save("19-recovery-selection")
        expression = QQmlExpression(engine.rootContext(), controls, "recoveryActions()")
        actions, undefined = expression.evaluate()
        if undefined or expression.hasError():
            raise RuntimeError(expression.error().toString())
        actions = actions.toVariant() if hasattr(actions, "toVariant") else actions
        controls.setVisible(False)
        preview = create("RecoveryStepPreview.qml")
        preview.setProperty("actions", actions)
        preview.setProperty("segments", printer.plan["previewSegments"])
        preview.setProperty("bedBounds", printer.plan["bedBounds"])
        preview.setProperty("restartPosition", [printer.plan[axis] for axis in "xyz"])
        preview.setProperty("caption", printer.plan["previewCaption"])
        preview.findChild(QObject, "recoveryPreviewStep").setProperty("value", len(actions) - 1)
        save("20-recovery-preview")
        preview.setVisible(False)
        controls.setVisible(True)
        popup = controls.findChild(QObject, "moonrakerRecoveryConfirmation")
        popup.setProperty("visible", True)
        pump(app)
        scroll = controls.findChild(QObject, "moonrakerRecoveryConfirmationScroll").property("contentItem")
        scroll.setProperty("contentY", scroll.property("contentHeight") - scroll.height())
        save("21-recovery-confirmation", popup)
        for item in items:
            item.setParentItem(None)
            item.deleteLater()
        window.close()
        pump(app)
        engine.deleteLater()
        pump(app)


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])
    explicit = os.environ.get("CAPTURE_THEME")
    for theme in ((explicit,) if explicit else ("cura-light", "cura-dark")):
        capture(app, output, theme, "-dark" if theme == "cura-dark" and not explicit else "")
        print("captured recovery selection, geometry and confirmation:", theme)


if __name__ == "__main__":
    main()
