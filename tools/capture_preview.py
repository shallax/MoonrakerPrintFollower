"""Deterministic Preview-pane captures: render the real
plugins/PreviewActionPanelControls.qml in an offscreen engine with the
real Cura/UM theme components, the real cura-light theme and fake pane
data, and write a PNG for release notes and layout regression checks.

The pane is data-driven: production feeds every displayed value through
QML item properties (PrintCoordinator/BedMeshPresenter publish into
PreviewPresentation, which calls control.setProperty(...)) and consumes
only plain signals, so the capture mirrors production by setting the
document properties with values formatted exactly like the presenters
and only faking Cura's engine-level context (CuraApplication,
screenScaleFactor, themeBackend). The output is deterministic for a
given toolchain (the dev container pins the fonts), so captures can be
diffed across releases.

Import-path note: Qt 6.11 searches import paths newest-first and a
module's types come from a single import-path directory, so the capture
overlay (dist/.capture-theme, built by the shared
materialise_theme_assets helper) is appended last of all.  The overlay
carries the real Cura/UM component files, spliced stubs for
Python-registered types, and a generated UM.Theme singleton with the
real cura-light values (see tests/theme_assets/README.md).

Usage:  python3 tools/capture_preview.py <output-directory>
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuick import QQuickWindow

def qml_errors(component) -> str:
    lines = []
    for error in component.errors():
        location = error.url().toString() if error.url().isValid() else "<component>"
        lines.append("%s:%s:%s %s" % (location, error.line(), error.column(), error.description()))
    return "\n".join(lines) if lines else "<no errors>"

class CuraApplicationStub(QObject):
    """The pane only reads CuraApplication.platformActivity (real Cura
    sets the CuraApplication instance as an engine context property).
    The notify signal keeps the property bindable, matching the real
    Q_PROPERTY and silencing Qt 6.11's non-bindable-expression warning."""

    platformActivityChanged = pyqtSignal()

    @pyqtProperty(bool, notify=platformActivityChanged)
    def platformActivity(self) -> bool:
        return True

def pane_values():
    """Pane property values, formatted exactly as the production
    presenters format them (see PrintCoordinator._publish and
    BedMeshPresenter._publish in plugins/)."""
    return {
        # Visibility chain: previewStageActive && configuredForFollowing
        # && CuraApplication.platformActivity.
        "previewStageActive": True,
        "configuredForFollowing": True,
        "followingEnabled": True,
        "followingPaused": False,
        "hasToolpath": True,
        "activePrinterName": "Voron v2.4 250",  # binding identity[1]
        "statusText": "Following",          # status_text(detail="Following")
        "statusIconName": "CheckCircle",    # status_icon("Following")
        # Bed-mesh card: f"{value:+.3f} mm", f"{range:.3f} mm range".
        "bedMeshAvailable": True,
        "bedMeshVisible": True,
        "bedMeshRangeText": "0.412 mm range",
        "bedMeshMinimumText": "-0.326 mm",
        "bedMeshMaximumText": "+0.086 mm",
        # ETA line: "Selected layer N — in HH:MM:SS · ~HH:MM".
        "selectedLayerEtaText": "Selected layer 96 — in 00:18:42 · ~14:36",
        # Pause-at-layer: current layer printed up to 95, selection on 96.
        "pauseAtLayerActive": True,
        "pauseAtLayerCandidate": 96,
        "pauseAtLayerCanToggle": True,
        "pauseAtLayerScheduled": False,
        "pauseAtLayerItems": [
            {"layer": 51, "eta": "in 00:06:18"},   # pause_eta(..., format_duration)
            {"layer": 78, "eta": "in 00:12:05"},
        ],
        "pauseAtLayerUnavailableText": "",
    }

def render(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    app = QGuiApplication([])

    from theme_support import install_capture_warning_filter

    install_capture_warning_filter()

    from theme_support import ThemeBackend, materialise_theme_assets as _shared_materialise, verify_capture_tree
    theme_backend = ThemeBackend(os.path.join(ROOT, "tests", "theme_assets", "cura-light"))
    theme_import = _shared_materialise(os.path.join(ROOT, "dist", ".capture-theme"), theme_backend)
    try:
        engine = QQmlEngine()
        # Qt 6.11 searches import paths newest-first and resolves a
        # module from a single directory, so add the fallbacks first:
        # the final search order is [theme assets, plugins, qml stubs].
        engine.addImportPath(os.path.join(ROOT, "tests", "qml_stubs"))
        engine.addImportPath(os.path.join(ROOT, "plugins"))
        engine.addImportPath(theme_import)
        verify_capture_tree(engine, theme_backend)
        engine_context = engine.rootContext()
        # Engine context production Cura provides for every QML document.
        # The Python reference must outlive the render: PyQt6 releases
        # the QObject when the wrapper is garbage-collected, which QML
        # then sees as a null context property.
        cura_application = CuraApplicationStub()
        engine_context.setContextProperty("CuraApplication", cura_application)
        engine_context.setContextProperty("screenScaleFactor", 1.0)

        component = QQmlComponent(engine)
        component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "PreviewActionPanelControls.qml")))
        if component.isError():
            raise RuntimeError(qml_errors(component))

        window = QQuickWindow()
        window.resize(600, 500)
        window.setTitle("moonraker preview action panel capture")
        item = component.create()
        if item is None:
            raise RuntimeError(qml_errors(component))
        # The preview pane document is Item-rooted (only the full-screen
        # MoonrakerMonitor* documents are Component-rooted), so one
        # create() yields the pane itself; guard anyway in case that ever
        # changes.
        if getattr(item, "create", None) is not None:
            item = item.create()
            if item is None:
                raise RuntimeError(qml_errors(component))

        # Feed the pane exactly like PreviewPresentation.publish() does.
        for name, value in pane_values().items():
            item.setProperty(name, value)

        item.setParentItem(window.contentItem())
        window.show()
        for _ in range(5):
            app.processEvents()

        # The pane sizes itself (width: externalGap + followerPanel.width,
        # height: followerPanel.height) and anchors its card to its right
        # edge with a vertical-centre offset, so the card can hang above
        # the document origin. Measure the actual card (the pane's single
        # child) and translate the document so the card sits centred in
        # the ~600x500 capture window (grown only if the card needs room).
        cards = item.childItems()
        if not cards:
            raise RuntimeError("pane laid out with no card child")
        card = cards[0]
        margin_x = float(item.property("horizontalPadding") or 0.0) * 2
        margin_y = float(item.property("verticalPadding") or 0.0) * 2
        card_width = float(card.property("width") or 0.0)
        card_height = float(card.property("height") or 0.0)
        card_x = float(card.x())
        card_y = float(card.y())
        if card_width <= 0.0 or card_height <= 0.0:
            raise RuntimeError("pane card did not lay out: %s x %s" % (card_width, card_height))

        window_width = max(600, int(card_width + 2 * max(margin_x, 20.0)))
        window_height = max(500, int(card_height + 2 * max(margin_y, 20.0)))
        window.resize(window_width, window_height)
        item.setX((window_width - card_width) / 2.0 - card_x)
        item.setY((window_height - card_height) / 2.0 - card_y)
        for _ in range(3):
            app.processEvents()

        path = os.path.join(output_dir, "04-preview-panel.png")
        image = window.grabWindow()
        if not image.save(path):
            raise RuntimeError("failed to save " + path)

        # Non-blank check: the card must show real content diversity.
        colors = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())}
        if len(colors) < 8:
            raise RuntimeError("capture looks blank: only %d distinct colours" % len(colors))
        print("captured", path, "(%dx%d, %d distinct colours)" % (image.width(), image.height(), len(colors)))

        # Tear the scene down in dependency order while the context-property
        # wrappers (CuraApplication, themeBackend) are still referenced: at
        # function exit the wrappers free in arbitrary order and the engine
        # re-evaluates bindings against already-collected ones, spewing
        # nondeterministic null-context TypeErrors (harmless to the PNG,
        # noisy in CI logs). See capture_settings.py for the same pattern.
        item.setParentItem(None)
        item.deleteLater()
        for _ in range(5):
            app.processEvents()
        window.close()
        component = None
        engine.clearComponentCache()
        engine = None
        for _ in range(5):
            app.processEvents()
    finally:
        shutil.rmtree(theme_import, ignore_errors=True)

def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "screenshots")
    render(output_dir)

if __name__ == "__main__":
    main()
