"""Deterministic offscreen capture of the Moonraker upload dialog.

Renders plugins/MoonrakerUploadDialog.qml at ~520x420 with a minimal
``manager`` QObject mirroring the surface the production
MoonrakerOutputDevice exposes to the dialog (uploadPathOptions,
initialUploadPath, initialUploadFilename, initialStartPrint,
acceptUpload, cancelUpload) and with the REAL Cura/UM widget components
from tests/theme_assets plus the real cura-light theme.

Engine import-path semantics in Qt 6 make the LAST added path that
declares a module the provider for that whole module (earlier providers
are shadowed and per-type fallback to other paths does not happen), so
the capture overlay (dist/.capture-theme, built by the shared
materialise_theme_assets helper) is appended last of all.  The overlay
carries the real Cura/UM component files, spliced stubs for
Python-registered types, and a generated UM.Theme singleton with the
real cura-light values, produced by the same ThemeBackend used by
tools/capture_monitor.py.

The dialog document roots directly in UM.Dialog (a Window), so one
component.create() yields the dialog window; grabWindow() captures it.
Qt 6.11 ignores ``Layout.fillWidth`` on the dialog's button RowLayout
(see _align_button_row), so the capture right-aligns that row before the
grab to reproduce the production bottom-right button placement.

Usage:  python3 tools/capture_upload.py <output-directory>
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt6.QtCore import QObject, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuick import QQuickWindow  # noqa: F401  (type-registers the QML Window wrapper)

THEME_ASSETS = os.path.join(ROOT, "tests", "theme_assets")
QML_STUBS = os.path.join(ROOT, "tests", "qml_stubs")

# Target render size; the dialog sizes itself from minimumWidth/minimumHeight.
TARGET_WIDTH, TARGET_HEIGHT = 520, 420

class UploadDialogManager(QObject):
    """Stand-in for the ``manager`` object production passes to the dialog
    (MoonrakerOutputDevice.requestWrite creates the dialog with
    ``{"manager": self}``).  Exposes exactly the surface the QML touches.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._options = ["<root>", "calibration", "models", "spare-parts"]
        self._path = "models"
        self._filename = "benchystand.gcode"
        self._start_print = True

    @pyqtProperty(QVariant, notify=changed)
    def uploadPathOptions(self):
        return QVariant(self._options)

    @pyqtProperty(str, notify=changed)
    def initialUploadPath(self):
        return self._path

    @pyqtProperty(str, notify=changed)
    def initialUploadFilename(self):
        return self._filename

    @pyqtProperty(bool, notify=changed)
    def initialStartPrint(self):
        return self._start_print

    @pyqtSlot(str, str, bool)
    def acceptUpload(self, path, filename, start_print):
        self._path, self._filename, self._start_print = path, filename, bool(start_print)
        self.changed.emit()

    @pyqtSlot()
    def cancelUpload(self):
        pass

def _align_button_row(dialog):
    """Reproduce the dialog's production button layout on the capture side.

    The upload dialog's bottom RowLayout asks for ``Layout.fillWidth: true``
    (with ``layoutDirection: Qt.RightToLeft``) so the Cancel/Upload pair
    packs against the form's right edge.  Qt 6.11's QQuickRowLayout does not
    honour that attached property when the row is itself a layout type
    child of a ColumnLayout (the row keeps its compact implicit width), and
    giving the row extra width by hand makes Qt misplace the non-fill
    children (free space is distributed along the row instead of packing
    from the RightToLeft edge).  Both defects leave the buttons pinned at
    the bottom-LEFT of the form.

    Workaround: keep the row at the compact width Qt arranges correctly
    (its own packing is right-to-left and gap-correct) and shift the row to
    the right edge of the form column.  That reproduces the intended
    production geometry pixel for pixel; on a Qt where fillWidth works the
    row already spans the column and this becomes a no-op.
    """
    def walk(item):
        meta = item.metaObject()
        cls = meta.className() if meta else type(item).__name__
        if cls == "QQuickRowLayout" and item.height() > 0:
            parent = item.parentItem()
            pmeta = parent.metaObject() if parent is not None else None
            if pmeta is not None and pmeta.className() == "QQuickColumnLayout":
                return item
        for child in item.childItems():
            found = walk(child)
            if found is not None:
                return found
        return None

    row = walk(dialog.contentItem())
    if row is None:
        return
    parent = row.parentItem()
    if row.width() + 1.0 < parent.width():
        row.setX(parent.width() - row.width())

def pixel_diversity(image):
    """Number of distinct sampled pixels, to reject blank captures."""
    sampled = set()
    step = 4
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            sampled.add(image.pixelColor(x, y).rgba())
    return len(sampled)

def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "screenshots")
    os.makedirs(output_dir, exist_ok=True)
    app = QGuiApplication([])

    from theme_support import install_capture_warning_filter

    install_capture_warning_filter()

    from theme_support import ThemeBackend
    backend = ThemeBackend(os.path.join(THEME_ASSETS, "cura-light"))
    from theme_support import materialise_theme_assets as _shared_materialise, verify_capture_tree
    overlay = _shared_materialise(os.path.join(ROOT, "dist", ".capture-theme"), backend)
    # The shared materialiser already wrote the full Theme.qml (palette,
    # sizes, fonts and the icon map) into the overlay.

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
    context = engine.rootContext()
    # Keep Python references alive: Qt does not own context-property
    # QObjects, and PyQt would otherwise destroy them at garbage collection.
    manager = UploadDialogManager()
    context.setContextProperty("manager", manager)
    context.setContextProperty("screenScaleFactor", 1.0)

    component = QQmlComponent(engine)
    component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "MoonrakerUploadDialog.qml")))
    if component.isError():
        raise RuntimeError("\n".join(e.toString() for e in component.errors()))

    # The upload dialog document roots directly in UM.Dialog (a Window), so
    # a single create() yields the dialog itself.
    dialog = component.create()
    if dialog is None:
        raise RuntimeError("\n".join(e.toString() for e in component.errors()))
    dialog.show()
    for _ in range(8):
        app.processEvents()

    # Pin the render to the deterministic target size.
    dialog.setProperty("minimumWidth", TARGET_WIDTH)
    dialog.setProperty("minimumHeight", TARGET_HEIGHT)
    dialog.setProperty("width", TARGET_WIDTH)
    dialog.setProperty("height", TARGET_HEIGHT)
    for _ in range(5):
        app.processEvents()

    # Qt 6.11 does not honour Layout.fillWidth on the button RowLayout (see
    # _align_button_row); shift the row right afterwards, before the grab.
    _align_button_row(dialog)

    image = dialog.grabWindow()
    path = os.path.join(output_dir, "06-upload-dialog.png")
    image.save(path)
    diversity = pixel_diversity(image)
    print(f"captured {path} ({image.width()}x{image.height()}, {diversity} sampled colors)")
    if image.isNull():
        raise RuntimeError("grabWindow produced a null image")
    if diversity < 20:
        raise RuntimeError(f"capture looks blank ({diversity} sampled colors)")

    # Tear the scene down in dependency order while the context-property
    # wrappers (manager) are still referenced: at exit the wrappers free
    # in arbitrary order and the engine re-evaluates bindings against
    # already-collected ones, spewing nondeterministic null-context
    # TypeErrors (harmless to the PNG, noisy in CI logs). See
    # capture_settings.py for the same pattern.
    dialog.close()
    dialog = None
    component = None
    engine.clearComponentCache()
    engine = None
    for _ in range(5):
        app.processEvents()

if __name__ == "__main__":
    main()
