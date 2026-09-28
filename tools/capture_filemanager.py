"""Deterministic capture of the file-manager page.

Renders the real plugins/FileManager.qml in an offscreen engine with the
real Cura/UM theme components and the real cura-light theme, against a
stub model carrying the values production publishes.

The face reads its printer model through property bindings only, so the
capture is the same shape as production: one ``printerModel`` object
exposing the file-manager slice of MoonrakerMonitorModel, exactly as
``_publish_file_manager`` builds it — the rows are the dicts
``MonitorFormatting.file_row_payload`` produces, already formatted. The
stub is a CAPTURE fixture: it is never imported by the plugin.

Import-path note: Qt 6.11 searches import paths newest-first and a
module's types come from a single import-path directory, so the capture
overlay (dist/.capture-theme, built by the shared
materialise_theme_assets helper) is appended last of all.

Usage:  python3 tools/capture_filemanager.py <output-directory>
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuick import QQuickWindow

import capture_contrast


def qml_errors(component) -> str:
    lines = []
    for error in component.errors():
        location = error.url().toString() if error.url().isValid() else "<component>"
        lines.append("%s:%s:%s %s" % (location, error.line(), error.column(), error.description()))
    return "\n".join(lines) if lines else "<no errors>"


# MonitorFormatting.STATUS_COLOURS plus its fallback, with the display
# text the face shows for each.
STATUS = {
    "completed": ("Completed", "#43a047"),
    "error": ("Failed", "#e53935"),
    "in_progress": ("In progress", "#1e88e5"),
    "never": ("Never printed", "text_inactive"),
}


def _row(name, size="1.84 MB", status="completed", **over):
    """One row as MonitorFormatting.file_row_payload shapes it: every
    field is already a formatted string by the time QML sees it."""
    text, colour = STATUS[status]
    row = {
        "name": name,
        "relpath": "gcodes/" + name,
        "folder": "gcodes",
        "modified": "14 Sep 2026 20:14",
        "size": size,
        "attempts": "2",
        "status": text,
        "statusColour": colour,
        "objH": "42.00 mm",
        "layerH": "0.20 mm",
        "est": "1h 24m",
        "lastPrint": "15 Sep 2026 09:02",
        "slicer": "Cura 5.13.0",
        "extr": "210 \u00b0C",
        "bed": "60 \u00b0C",
        "filament": "4.21 m",
        "hasThumb": False,
        "unparsed": False,
    }
    row.update(over)
    return row


class FileManagerModelStub(QObject):
    """The file-manager slice of MoonrakerMonitorModel, as the dashboard
    hands it to the face. Values are the production spellings; the notify
    signals keep every binding live so the engine does not warn."""

    fileManagerChanged = pyqtSignal()
    fileManagerThumbsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = {
            "fileManagerRows": [
                _row("phone-stand.gcode", "3.41 MB",
                     lastPrint="15 Sep 2026 09:02", est="1h 24m", filament="4.21 m"),
                _row("voron-cube-0.2mm.gcode", "1.84 MB",
                     lastPrint="16 Sep 2026 18:31", est="2h 06m", filament="6.02 m"),
                _row("calibration-cube.gcode", "412.6 kB",
                     est="38m", filament="1.02 m"),
                _row("benchy-hollow.gcode", "962.1 kB", status="error",
                     attempts="3", est="52m", filament="2.14 m"),
                _row("toolholder-plate.gcode", "6.02 MB",
                     est="5h 12m", filament="16.84 m", layerH="0.28 mm", objH="96.00 mm"),
                _row("nozzle-organiser.gcode", "2.11 MB", status="never",
                     attempts="\u2014", lastPrint="\u2014", est="1h 47m"),
                _row("spool-holder.gcode", "1.20 MB",
                     est="1h 02m", filament="3.55 m"),
                _row("dragon-head.gcode", "8.77 MB", status="in_progress",
                     est="11h 20m", filament="31.60 m", layerH="0.12 mm", objH="128.00 mm"),
            ],
            "fileManagerRecents": [
                {"name": "voron-cube-0.2mm.gcode", "relpath": "gcodes/voron-cube-0.2mm.gcode",
                 "time": "Today 18:31"},
                {"name": "phone-stand.gcode", "relpath": "gcodes/phone-stand.gcode",
                 "time": "Yesterday 20:14"},
                {"name": "calibration-cube.gcode", "relpath": "gcodes/calibration-cube.gcode",
                 "time": "16 Sep 2026"},
            ],
            # A LIST of segments: the breadcrumb is a Repeater over it,
            # so a bare string renders one crumb per character.
            "fileManagerDirectory": ["gcodes"],
            "fileManagerDirectories": ["gcodes/calibration", "gcodes/tools"],
            "fileManagerDiskText": "128.4 GB free of 238.5 GB",
            "fileManagerRefreshedAt": "Last refreshed at 20:14",
            "fileManagerShown": "Showing 8 of 8 files",
            "fileManagerPage": "Page 1 of 1",
            "fileManagerPageIndex": 0,
            "fileManagerPageCount": 1,
            "fileManagerPageSize": "all",
            "fileManagerPageSelection": False,
            "fileManagerEmptyKind": "",
            "fileManagerSelected": 0,
            "fileManagerSortColumn": "name",
            "fileManagerSortAscending": True,
            "fileManagerSearch": "",
            "fileManagerFilters": {},
            "fileManagerFilterCounts": {},
            "fileManagerFilterOptions": {
                "slicer": ["Cura 5.13.0", "Cura 5.12.0", "PrusaSlicer 2.8"],
                "modified": ["24h", "7d", "30d"],
                "print_time": ["< 1h", "1-4h", "> 4h"],
                "never_printed": ["Never printed"],
            },
            "fileManagerHistoryLoaded": True,
            "fileManagerHistoryExhausted": True,
            "fileManagerWalkError": "",
            "fileManagerNote": "",
            "fileManagerThumbs": {},
            "fileManagerColumnOrder": [
                "Modified", "Size", "Attempts", "Status", "Object height",
                "Layer height", "Est. time", "Last print", "Slicer",
                "Extruder", "Bed", "Filament",
            ],
            "fileManagerColumnWidths": {},
            "fileManagerColumnHidden": [],
            # The verbs' confirm state (all quiet: the capture is the
            # resting face, not a dialog).
            "filePrintConfirm": "",
            "fileDeleteConfirm": [],
            "fileRenameTarget": "",
            "fileRenameConflict": False,
            "fileUploadConfirm": "",
            "fileUploadProgress": -1,
            "fileDownloadProgress": "",
            # The face reads these for its print/upgrade affordances.
            "monitorConnected": True,
            "printActive": False,
            "canResumePrint": True,
        }

    def _get(self, name):
        return self._values[name]

    @pyqtProperty("QVariant", notify=fileManagerThumbsChanged)
    def fileManagerThumbs(self):
        return self._values["fileManagerThumbs"]

    # The face's action verbs. They are declared with the decorator ON
    # the def, not assigned from a shared helper: PyQt builds the
    # meta-object at class creation and only registers a slot the
    # decorator wrapped in place — an assignment reads as "not a
    # function" to QML (probed). The face CALLS openFileManager from
    # onOpenChanged and the thumbnail request on first paint; none of
    # the rest is reached by a capture that presses nothing.
    @pyqtSlot()
    def clearFileFilters(self):
        return None

    @pyqtSlot()
    def clearFileSelection(self):
        return None

    @pyqtSlot()
    def fileCancelDelete(self):
        return None

    @pyqtSlot()
    def fileCancelPrint(self):
        return None

    @pyqtSlot()
    def fileCancelRename(self):
        return None

    @pyqtSlot()
    def fileCancelUpload(self):
        return None

    @pyqtSlot()
    def fileClearWalkError(self):
        return None

    @pyqtSlot()
    def fileConfirmDelete(self):
        return None

    @pyqtSlot()
    def fileConfirmPrint(self):
        return None

    @pyqtSlot()
    def fileConfirmRename(self):
        return None

    @pyqtSlot()
    def fileConfirmUpload(self):
        return None

    @pyqtSlot()
    def fileCreateDirectory(self):
        return None

    @pyqtSlot()
    def fileDownload(self):
        return None

    @pyqtSlot()
    def fileDownloadCancel(self):
        return None

    @pyqtSlot()
    def fileLoadAllHistory(self):
        return None

    @pyqtSlot()
    def fileNavigateTo(self):
        return None

    @pyqtSlot()
    def filePreviewRename(self):
        return None

    @pyqtSlot()
    def fileRequestDelete(self):
        return None

    @pyqtSlot()
    def fileRequestDeleteDir(self):
        return None

    @pyqtSlot()
    def fileRequestDeleteFile(self):
        return None

    @pyqtSlot()
    def fileRequestPrint(self):
        return None

    @pyqtSlot()
    def fileRequestRename(self):
        return None

    @pyqtSlot()
    def fileRequestRenameDir(self):
        return None

    @pyqtSlot()
    def fileRequestVisibleThumbnails(self):
        return None

    @pyqtSlot()
    def fileScanMetadata(self):
        return None

    @pyqtSlot()
    def fileUpload(self):
        return None

    @pyqtSlot()
    def fileUploadDismiss(self):
        return None

    @pyqtSlot()
    def openFileManager(self):
        return None

    @pyqtSlot()
    def refreshFileManager(self):
        return None

    @pyqtSlot()
    def setFileColumnOrder(self):
        return None

    @pyqtSlot()
    def setFileColumnVisible(self):
        return None

    @pyqtSlot()
    def setFileColumnWidth(self):
        return None

    @pyqtSlot()
    def setFileFilter(self):
        return None

    @pyqtSlot()
    def setFilePage(self):
        return None

    @pyqtSlot()
    def setFilePageSize(self):
        return None

    @pyqtSlot()
    def setFileSearch(self):
        return None

    @pyqtSlot()
    def setFileSort(self):
        return None

    @pyqtSlot()
    def toggleFilePageSelection(self):
        return None

    @pyqtSlot()
    def toggleFileSelection(self):
        return None

# The exposed surface: every name the face reads, bound after the class
# exists (a class body cannot reference its own notify signal).
_BOUND = (
    "fileManagerRows", "fileManagerRecents", "fileManagerDirectory",
    "fileManagerDirectories", "fileManagerDiskText", "fileManagerRefreshedAt",
    "fileManagerShown", "fileManagerPage", "fileManagerPageIndex",
    "fileManagerPageCount", "fileManagerPageSize", "fileManagerPageSelection",
    "fileManagerEmptyKind", "fileManagerSelected", "fileManagerSortColumn",
    "fileManagerSortAscending", "fileManagerSearch", "fileManagerFilters",
    "fileManagerFilterCounts", "fileManagerFilterOptions",
    "fileManagerHistoryLoaded", "fileManagerHistoryExhausted",
    "fileManagerWalkError", "fileManagerNote", "fileManagerColumnOrder",
    "fileManagerColumnWidths", "fileManagerColumnHidden", "filePrintConfirm",
    "fileDeleteConfirm", "fileRenameTarget", "fileRenameConflict",
    "fileUploadConfirm", "fileUploadProgress", "fileDownloadProgress",
    "monitorConnected", "printActive", "canResumePrint",
)
for _name in _BOUND:
    setattr(FileManagerModelStub, _name, pyqtProperty(
        "QVariant",
        (lambda self, n=_name: self._values[n]),
        notify=FileManagerModelStub.fileManagerChanged))


# The face is a full page: the size Cura's Monitor stage gives it.
TARGET_WIDTH, TARGET_HEIGHT = 1440, 900


def render(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    app = QGuiApplication([])

    from theme_support import install_capture_warning_filter

    install_capture_warning_filter()

    from theme_support import ThemeBackend, materialise_theme_assets as _shared_materialise, verify_capture_tree
    theme = os.environ.get("CAPTURE_THEME") or "cura-light"
    theme_backend = ThemeBackend(os.path.join(ROOT, "tests", "theme_assets", theme))
    theme_import = _shared_materialise(
        os.environ.get("CAPTURE_THEME_TREE") or os.path.join(ROOT, "dist", ".capture-theme"), theme_backend)
    try:
        engine = QQmlEngine()
        engine.addImportPath(os.path.join(ROOT, "tests", "qml_stubs"))
        engine.addImportPath(os.path.join(ROOT, "plugins"))
        engine.addImportPath(theme_import)
        verify_capture_tree(engine, theme_backend)
        engine_context = engine.rootContext()
        engine_context.setContextProperty("screenScaleFactor", 1.0)

        component = QQmlComponent(engine)
        component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "FileManager.qml")))
        if component.isError():
            raise RuntimeError(qml_errors(component))

        window = QQuickWindow()
        window.setColor(theme_backend.getColor("main_background"))
        window.resize(TARGET_WIDTH, TARGET_HEIGHT)
        window.setTitle("moonraker file manager capture")

        model = FileManagerModelStub()
        item = component.create()
        if item is None:
            raise RuntimeError(qml_errors(component))
        # The document is Item-rooted and fills its host (the dashboard
        # anchors it to the pane area above the emergency dock).
        item.setParentItem(window.contentItem())
        item.setWidth(TARGET_WIDTH)
        item.setHeight(TARGET_HEIGHT)
        item.setProperty("printerModel", model)
        item.setProperty("open", True)
        window.show()
        for _ in range(8):
            app.processEvents()

        path = os.path.join(output_dir, "09-file-manager.png")
        image = window.grabWindow()
        if not image.save(path):
            raise RuntimeError("failed to save " + path)

        colors = {image.pixelColor(x, y).name()
                  for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)}
        if len(colors) < 8:
            raise RuntimeError("capture looks blank: only %d distinct colours" % len(colors))
        capture_contrast.audit(window.contentItem(), image, "09-file-manager.png")
        print("captured", path, "(%dx%d, %d distinct colours)"
              % (image.width(), image.height(), len(colors)))

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
