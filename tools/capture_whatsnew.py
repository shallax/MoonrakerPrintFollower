"""Deterministic offscreen capture of the what's-new overlay.

Renders plugins/WhatsNewOverlay.qml the way production opens it: a
Popup created on Cura's engine and parented into a plain QQuickWindow
(the window stands in for Cura's main window), with the REAL content
from plugins/WhatsNew.py and the real cura-light theme through the
shared capture overlay.

Usage:  python3 tools/capture_whatsnew.py <output-directory>
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt6.QtCore import QEventLoop, QMetaObject, QObject, QTimer, QUrl, QVariant, pyqtProperty, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuick import QQuickWindow  # noqa: F401  (type-registers the QML Window wrapper)

THEME_ASSETS = os.path.join(ROOT, "tests", "theme_assets")
QML_STUBS = os.path.join(ROOT, "tests", "qml_stubs")

# The window the popup renders in; the card (520 wide, capped at 560
# tall) sits centered.
TARGET_WIDTH, TARGET_HEIGHT = 900, 700


class WhatsNewModelStub(QObject):
    """Stand-in for the monitor model's what's-new surface. The content
    is the REAL curated list — the capture documents what the user
    sees on a fresh 4.1.0 launch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from plugins.WhatsNew import entries
        self._content = entries()

    @pyqtProperty(QVariant, constant=True)
    def whatsNewContent(self):
        return self._content

    @pyqtSlot()
    def dismissWhatsNew(self):
        pass


def pixel_diversity(image):
    """Number of distinct sampled pixels, to reject blank captures."""
    sampled = set()
    for y in range(0, image.height(), 4):
        for x in range(0, image.width(), 4):
            sampled.add(image.pixelColor(x, y).rgba())
    return len(sampled)


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "screenshots")
    os.makedirs(output_dir, exist_ok=True)
    app = QGuiApplication([])

    from theme_support import install_capture_warning_filter

    install_capture_warning_filter()

    from theme_support import ThemeBackend, materialise_theme_assets as _shared_materialise, verify_capture_tree
    backend = ThemeBackend(os.path.join(THEME_ASSETS, os.environ.get("CAPTURE_THEME", "cura-light")))
    overlay = _shared_materialise(os.path.join(ROOT, "dist", ".capture-theme"), backend)

    engine = QQmlEngine()
    # The repository's capture ordering: real components first, plugins,
    # stubs last; the capture overlay is appended last of all because Qt 6
    # module resolution makes the last import path declaring a module its
    # provider (so the overlay must win for UM and Cura).
    engine.addImportPath(THEME_ASSETS)
    engine.addImportPath(os.path.join(ROOT, "plugins"))
    engine.addImportPath(QML_STUBS)
    engine.addImportPath(overlay)
    verify_capture_tree(engine, backend)
    engine.rootContext().setContextProperty("screenScaleFactor", 1.0)

    # The window stands in for Cura's main window: the popup parents
    # into its content item, exactly as production attaches it.
    window = QQuickWindow()
    window.resize(TARGET_WIDTH, TARGET_HEIGHT)
    window.show()

    component = QQmlComponent(engine)
    component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "WhatsNewOverlay.qml")))
    if component.isError():
        raise RuntimeError("\n".join(e.toString() for e in component.errors()))

    model = WhatsNewModelStub()
    popup = component.createWithInitialProperties({"model": model})
    if popup is None:
        raise RuntimeError("\n".join(e.toString() for e in component.errors()))
    root = window.contentItem()
    popup.setProperty("parent", root)
    # Center on the WINDOW's geometry: the content item's size lags
    # until its first layout pass (0 at this point — the centered
    # card once rendered at 0,0 for exactly that reason).
    popup.setProperty("x", max(0, round((window.width() - popup.property("width")) / 2)))
    popup.setProperty("y", max(0, round((window.height() - popup.property("height")) / 2)))
    QMetaObject.invokeMethod(popup, "open")
    # Let the modal dimmer's fade-in settle: the animation advances
    # only on timer events, and its END state — the theme's fixed dim
    # opacity — is what production shows. A fixed wait is
    # deterministic (the fade's end is the same every run).
    loop = QEventLoop()
    QTimer.singleShot(350, loop.quit)
    loop.exec()
    for _ in range(4):
        app.processEvents()

    image = window.grabWindow()
    path = os.path.join(output_dir, "08-whats-new.png")
    image.save(path)
    diversity = pixel_diversity(image)
    print(f"whats-new capture: {path} ({image.width()}x{image.height()}, diversity {diversity})")
    if diversity < 20:
        raise RuntimeError("whats-new capture is blank (diversity check)")


if __name__ == "__main__":
    main()
