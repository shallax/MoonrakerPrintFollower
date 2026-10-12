"""Deterministic Preview Toolhead captures with synthetic values only.

No printer model or transport is created. CAPTURE_THEME selects one real
Cura theme for repeatability checks; a normal refresh emits both themes.
python tools/capture_preview_toolhead.py /tmp/mpf/toolhead-captures.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.parse import unquote

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSize, QUrl
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuick import QQuickImageProvider, QQuickWindow

from tools import capture_contrast, theme_support


class IconProvider(QQuickImageProvider):
    """Apply Uranium's alpha-mask tint in software for this capture only."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, identifier, requested_size):
        source, _, tint = identifier.partition("?")
        image = QImage(QUrl(unquote(source)).toLocalFile())
        if image.isNull():
            return image, QSize()
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), QColor(unquote(tint)))
        painter.end()
        return image, image.size()


class ToolheadScene:
    """Real QML/theme assets; all signals terminate in local presentation."""

    def __init__(self, app, theme="cura-light", scale=1.0, available_height=620):
        self.app = app
        Path("/tmp/mpf").mkdir(exist_ok=True)
        self.scratch = tempfile.mkdtemp(prefix="toolhead-theme-", dir="/tmp/mpf")
        self.theme = theme_support.ThemeBackend(str(ROOT / "tests/theme_assets" / theme))
        overlay = theme_support.materialise_theme_assets(self.scratch, self.theme)
        # The generic offscreen ColorImage stub ignores tint. These new
        # light/dark illustrations must show the actual header/tab colours.
        (Path(overlay) / "UM/ColorImage.qml").write_text('''import QtQuick 2.15
Item {
    id: root
    property url source: ""
    property color color: "#000000"
    Image {
        anchors.fill: parent
        source: root.source == "" ? "" : "image://toolhead-tint/" + encodeURIComponent(root.source) + "?" + encodeURIComponent(root.color)
        fillMode: Image.PreserveAspectFit
    }
}
''')
        self.engine = QQmlEngine()
        self.icons = IconProvider()
        self.engine.addImageProvider("toolhead-tint", self.icons)
        self.warnings = []
        self.engine.warnings.connect(lambda errors: self.warnings.extend(e.toString() for e in errors))
        for path in (ROOT / "tests/qml_stubs", ROOT / "mpf", Path(overlay)):
            self.engine.addImportPath(str(path))
        theme_support.verify_capture_tree(self.engine, self.theme)
        self.engine.rootContext().setContextProperty("screenScaleFactor", scale)
        self.component = QQmlComponent(self.engine)
        self.component.loadUrl(QUrl.fromLocalFile(str(ROOT / "mpf/preview/PreviewToolheadPane.qml")))
        if self.component.isError():
            raise RuntimeError("\n".join(e.toString() for e in self.component.errors()))
        self.pane = self.component.create()
        if self.pane is None:
            raise RuntimeError("Toolhead pane failed to instantiate")
        self.pane.setProperty("availableHeight", available_height * scale)
        self.pane.collapseRequested.connect(lambda value: self.pane.setProperty("collapsed", value))
        self.pane.jogDistanceRequested.connect(lambda value: self.pane.setProperty("jogDistance", value))
        self.window = QQuickWindow()
        self.window.setColor(self.theme.getColor("main_background"))
        self.window.resize(round(352 * scale), round((available_height + 48) * scale))
        self.pane.setParentItem(self.window.contentItem())
        self.pane.setX(24 * scale)
        self.pane.setY(24 * scale)
        self.window.show()
        self.pump()

    def pump(self, seconds=0.08):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)

    def find(self, name):
        # Repeater delegates belong to the visual tree, which need not match
        # QObject ownership. Follow the actual scene rather than parentage.
        pending = [self.pane]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        raise AssertionError("missing " + name)

    def ready(self):
        for name, value in {
            "connected": True, "jogAllowed": True, "homeAllowed": True,
            "moveToAllowed": True, "offsetAllowed": True,
            "positionX": "125.00", "positionY": "125.00", "positionZ": "10.00",
            "offsetText": "−0.075", "statusText": "Ready · synthetic printer",
            "actionRows": [{"key": "quad_gantry_level", "label": "Quad gantry level", "allowed": True},
                           {"key": "bed_mesh", "label": "Calibrate bed mesh", "allowed": True},
                           {"key": "motors", "label": "Disable motors", "allowed": True}],
        }.items():
            self.pane.setProperty(name, value)
        self.pump()

    def capture(self, path):
        self.window.resize(round(self.pane.width() + 48 * self.pane.property("scale")),
                           round(self.pane.height() + 48 * self.pane.property("scale")))
        self.pump()
        image = self.window.grabWindow()
        if image.isNull() or not image.save(str(path)):
            raise RuntimeError("failed capture " + str(path))
        capture_contrast.audit(self.window.contentItem(), image, Path(path).name)
        if self.warnings:
            raise RuntimeError("\n".join(self.warnings))
        return image

    def close(self):
        self.pane.setParentItem(None)
        self.pane.deleteLater()
        self.window.close()
        self.pump()
        self.component = None
        self.engine.deleteLater()
        self.pump()
        shutil.rmtree(self.scratch)


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])
    explicit_theme = os.environ.get("CAPTURE_THEME")
    for theme in ((explicit_theme,) if explicit_theme else ("cura-light", "cura-dark")):
        suffix = "-dark" if theme == "cura-dark" and not explicit_theme else ""
        def path(name, suffix=suffix):
            return output / (name + suffix + ".png")
        scene = ToolheadScene(app, theme)
        try:
            scene.capture(path("18-preview-toolhead-disconnected"))
            scene.ready()
            scene.capture(path("15-preview-toolhead"))
            scene.pane.setProperty("collapsed", True)
            scene.capture(path("16-preview-toolhead-collapsed"))
            scene.pane.setProperty("collapsed", False)
            for name in ("jogAllowed", "homeAllowed", "moveToAllowed"):
                scene.pane.setProperty(name, False)
            scene.pane.setProperty("statusText", "Printing · Z-offset available")
            scene.pane.setProperty("actionRows", [
                {"key": "quad_gantry_level", "label": "Quad gantry level", "allowed": False},
                {"key": "bed_mesh", "label": "Calibrate bed mesh", "allowed": False},
                {"key": "motors", "label": "Disable motors", "allowed": False},
            ])
            scene.capture(path("17-preview-toolhead-printing"))
            print("captured", theme, "disconnected, expanded, collapsed and printing")
        finally:
            scene.close()


if __name__ == "__main__":
    main()
