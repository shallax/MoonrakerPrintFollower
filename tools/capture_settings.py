"""Deterministic offscreen capture of the Moonraker settings page.

Renders plugins/MoonrakerFollowerConfiguration.qml the way production
does: the document's root is the Cura.MachineAction component and the
action object MoonrakerFollowerMachineAction exposes to it is supplied
as the ``manager`` engine context property (with ``actionDialog`` and
``catalog`` next to it, exactly like Cura's machine-action dialog
context).  The manager stand-in below mirrors the pyqtProperty /
pyqtSlot surface of the production action one-to-one, so every field,
check state and validation message renders as it would in Cura.

The real Cura/UM components from tests/theme_assets render the page
(import path priority), resolved through the ``themeBackend`` context
property against the real cura-light theme.  Three gaps in that tree
are bridged in a throwaway copy of it (see materialise_theme_assets);
nothing in the repo tree is modified by this script:

  * the legacy bare registrations for TextField/ComboBox/RadioButton
    point at the Cura/ directory but the real files live under
    Cura/Widgets/, so the real files are also copied to the module
    root of the throwaway tree;
  * the real widgets reference UM.ColorImage, which the real UM tree
    does not ship (only tests/qml_stubs does), so the stub file is
    spliced into the throwaway UM module;
  * the real UM.ToolTip paints its background from UM.PointingRectangle,
    which no repository tree ships, so an inert stand-in with the same
    property surface is spliced in (tooltips never show offscreen);
  * the real Cura TextField imports UM 1.7 but the real UM module's
    newest registration is 1.5, so an extra Enums 1.7 registration
    lifts the module version ceiling of the throwaway copy.

One PNG is written per tab (05-settings-connection.png,
05-settings-following.png, 05-settings-upload.png), each fitted to its
tab's content height.  The output is deterministic for a given toolchain
(the dev container pins the fonts), so captures can be diffed across
releases.

Usage:  python3 tools/capture_settings.py <output-directory>
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt6.QtCore import QObject, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuick import QQuickWindow


# ---------------------------------------------------------------------------
# Fake context objects (the QML surface of MoonrakerFollowerMachineAction)
# ---------------------------------------------------------------------------


class SettingsManager(QObject):
    """Stand-in for the ``manager`` context object production passes to the
    settings QML (MoonrakerFollowerMachineAction).  Mirrors its pyqtProperty
    surface and validation slots; values match a first-configured printer.
    """

    settingsChanged = pyqtSignal()
    testStatusChanged = pyqtSignal()
    testBusyChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._finished = False
        self._test_status = "Not tested"
        self._test_busy = False

    @pyqtProperty(bool, notify=settingsChanged)
    def finished(self):
        return self._finished

    # --- Connection / following settings (see PrinterConfig defaults) ---
    @pyqtProperty(str, notify=settingsChanged)
    def machineName(self):
        return "Voron v2.4 250"

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsEnabled(self):
        return True

    @pyqtProperty(str, notify=settingsChanged)
    def settingsUrl(self):
        return "http://voron-0.2.local:7125"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsApiKey(self):
        return "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsPollInterval(self):
        return "750"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsFollowMode(self):
        return "exact"

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsLayerOneBased(self):
        return True

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsPathFollow(self):
        return True

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsPathSmoothing(self):
        return True

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsAutoPreview(self):
        return False

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsToolheadIndicator(self):
        return True

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsZFallback(self):
        return True

    @pyqtProperty(str, notify=settingsChanged)
    def settingsZTolerance(self):
        return "0.040"

    # --- Integrated Moonraker output settings ---
    @pyqtProperty(str, notify=settingsChanged)
    def settingsFrontendUrl(self):
        return "http://voron-0.2.local:4408"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsOutputFormat(self):
        return "gcode"

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadDialog(self):
        return True

    @pyqtProperty(str, notify=settingsChanged)
    def settingsUploadPath(self):
        return "models"

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadStartPrint(self):
        return False

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadRememberState(self):
        return False

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsUploadAutohideMessage(self):
        return False

    @pyqtProperty(str, notify=settingsChanged)
    def settingsPowerDevices(self):
        return ""

    @pyqtProperty(str, notify=settingsChanged)
    def settingsReadyRetryInterval(self):
        return "0.5"

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTranslateInput(self):
        return ""

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTranslateOutput(self):
        return ""

    @pyqtProperty(str, notify=settingsChanged)
    def settingsTranslateRemove(self):
        return ""

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsTraceLayer(self):
        return False

    @pyqtProperty(bool, notify=settingsChanged)
    def settingsTraceHttp(self):
        return False

    # --- Connection test ---
    @pyqtProperty(str, notify=testStatusChanged)
    def testStatus(self):
        return self._test_status

    @pyqtProperty(bool, notify=testBusyChanged)
    def testBusy(self):
        return self._test_busy

    # --- Validation (same rules as MoonrakerFollowerMachineAction) ---
    @pyqtSlot(str, str, result=bool)
    def insecureKeyWarning(self, url, key):
        return False

    @pyqtSlot(str, result=bool)
    def validUrl(self, value):
        parsed = QUrl(str(value or ""))
        return parsed.isValid() and parsed.scheme() in ("http", "https") and bool(parsed.host())

    @pyqtSlot(str, result=bool)
    def validPollInterval(self, value):
        try:
            return int(str(value).strip()) > 0
        except (TypeError, ValueError):
            return False

    @pyqtSlot(str, result=bool)
    def validZTolerance(self, value):
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return False
        return 0.005 <= number <= 0.250

    @pyqtSlot(str, result=bool)
    def validRetryInterval(self, value):
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return False
        return 0.1 <= number <= 60.0

    @pyqtSlot(str, str, result=bool)
    def validTranslation(self, source, target):
        return len(str(source or "")) == len(str(target or ""))

    @pyqtSlot(QVariant, result=bool)
    def saveConfig(self, params):
        return True

    @pyqtSlot(str, str)
    def testConnection(self, url, api_key):
        self._test_status = "Testing connection…"
        self.testStatusChanged.emit()
        self._test_busy = True
        self.testBusyChanged.emit()

    @pyqtSlot()
    def cancelTest(self):
        if self._test_busy:
            self._test_busy = False
            self.testBusyChanged.emit()

    @pyqtSlot()
    def reset(self):
        self._finished = False
        self.settingsChanged.emit()


class FakeActionDialog(QObject):
    """The ``actionDialog`` context object Cura wires to machine-action QML:
    the page connects to its accepted/rejected/closing signals and calls
    close() from its Save/Cancel buttons."""

    accepted = pyqtSignal()
    rejected = pyqtSignal()
    closing = pyqtSignal()

    @pyqtSlot()
    def close(self):
        pass


class FakeCatalog(QObject):
    """The ``catalog`` context object Cura exposes to every dialog document;
    the real Cura ComboBox reads defaultTextOnEmptyModel through it."""

    @pyqtSlot(str, str, result=str)
    def i18nc(self, _context, text):
        return text

    @pyqtSlot(str, result=str)
    def i18n(self, text):
        return text


# ---------------------------------------------------------------------------
# Throwaway theme tree with the gaps bridged
# ---------------------------------------------------------------------------


def materialise_theme_assets() -> str:
    """Copy tests/theme_assets into a temp tree and bridge the three gaps in
    it (see the module docstring).  Returns the temp import path; the caller
    removes the parent directory on exit."""
    tmp = tempfile.mkdtemp(prefix="moonraker-settings-theme-")
    target = os.path.join(tmp, "theme_assets")
    source = os.path.join(ROOT, "tests", "theme_assets")
    shutil.copytree(source, target)

    # 1. The legacy bare qmldir registrations resolve files in Cura/, but the
    #    real TextField/ComboBox/RadioButton live in Cura/Widgets/.  Copy them
    #    to the module root of the throwaway tree so those registrations (and
    #    any engine that prefers the 1.0 over the appended 1.1 line) load the
    #    real widget files.
    for name in ("TextField", "ComboBox", "RadioButton"):
        shutil.copy2(os.path.join(target, "Cura", "Widgets", name + ".qml"),
                     os.path.join(target, "Cura", name + ".qml"))

    # 2. The real widgets reference UM.ColorImage, which the real UM tree
    #    does not ship.  Splice the stub file into the throwaway UM module.
    color_image = os.path.join(ROOT, "tests", "qml_stubs", "UM", "ColorImage.qml")
    if not os.path.isfile(os.path.join(target, "UM", "ColorImage.qml")) and os.path.isfile(color_image):
        shutil.copy2(color_image, os.path.join(target, "UM", "ColorImage.qml"))

    # 3. The real UM.ToolTip (used by every real Cura/UM widget) paints its
    #    background from Uranium's UM.PointingRectangle, which no repository
    #    tree ships.  Write a capture-only stand-in (tooltips never appear
    #    in offscreen renders; only the geometry surface is needed for the
    #    type to compile) and register it at the same versions as ToolTip.
    pointing_rectangle = """import QtQuick 2.15
Rectangle {
    // Capture-only stand-in for Uranium's PointingRectangle, the arrowed
    // bubble behind UM.ToolTip.  Tooltips only appear while hovered, which
    // offscreen captures never trigger, so only the property surface the
    // real UM.ToolTip.qml touches is needed here.
    property point target: Qt.point(0, 0)
    property real arrowSize: 0
    property int arrowPosition: 0
}
"""
    pr_path = os.path.join(target, "UM", "PointingRectangle.qml")
    if not os.path.isfile(pr_path):
        with open(pr_path, "w", encoding="utf-8") as handle:
            handle.write(pointing_rectangle)

    # 4. The real Cura TextField imports UM 1.7 while the real UM module's
    #    newest registration is 1.5 (imports above the module ceiling are
    #    rejected).  Registering the already-present Enums at 1.7 lifts the
    #    ceiling of the throwaway copy.
    additions = {
        "UM/qmldir": [
            "ColorImage 1.5 ColorImage.qml",
            "PointingRectangle 1.0 PointingRectangle.qml",
            "PointingRectangle 1.4 PointingRectangle.qml",
            "PointingRectangle 1.5 PointingRectangle.qml",
            "Enums 1.7 Enums.qml",
        ],
        "Cura/qmldir": [
            "MachineAction 1.0 MachineAction.qml",
            "MachineAction 1.1 MachineAction.qml",
        ],
    }
    for rel_qmldir, lines in additions.items():
        qmldir_path = os.path.join(target, rel_qmldir)
        registered = set()
        with open(qmldir_path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 3 and parts[0] not in ("module", "singleton", "internal", "prefer"):
                    registered.add((parts[0], parts[1]))
        missing = [line for line in lines if tuple(line.split()[:2]) not in registered]
        if missing:
            with open(qmldir_path, "a", encoding="utf-8") as handle:
                handle.write("\n# settings-capture bridges (see tools/capture_settings.py)\n")
                for line in missing:
                    handle.write(line + "\n")
    return os.path.join(tmp, "theme_assets")


def descendants(item):
    """Yield item and its full subtree, for scanning live object geometry."""
    stack = [item]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.childItems())


def qml_errors(component) -> str:
    lines = []
    for error in component.errors():
        location = error.url().toString() if error.url().isValid() else "<component>"
        lines.append("%s:%s:%s %s" % (location, error.line(), error.column(), error.description()))
    return "\n".join(lines) if lines else "<no errors>"


def pixel_diversity(image) -> int:
    """Number of distinct sampled pixels, to reject blank captures."""
    sampled = set()
    step = 2
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

    from theme_support import ThemeBackend, materialise_theme_assets as _shared_materialise, verify_capture_tree
    theme_backend = ThemeBackend(os.path.join(ROOT, "tests", "theme_assets", "cura-light"))
    theme_import = _shared_materialise(os.path.join(ROOT, "dist", ".capture-theme"), theme_backend)
    try:
        engine = QQmlEngine()
        # Qt 6.11 searches import paths newest-first and resolves a module
        # from a single directory, so the fallbacks are added first: the
        # final search order is [theme assets, plugins, qml stubs].
        engine.addImportPath(os.path.join(ROOT, "tests", "qml_stubs"))
        engine.addImportPath(os.path.join(ROOT, "plugins"))
        engine.addImportPath(theme_import)
        verify_capture_tree(engine, theme_backend)
        context = engine.rootContext()
        # Keep Python references alive for the whole render: PyQt6 releases
        # context-property QObjects when their wrappers are garbage-collected,
        # which QML then sees as a null context property.
        from theme_support import ThemeBackend
        manager = SettingsManager()
        action_dialog = FakeActionDialog()
        catalog = FakeCatalog()
        # theme_backend is built above the engine setup and shared by the
        # materialiser, the verification probe and the context property.
        context.setContextProperty("manager", manager)
        context.setContextProperty("actionDialog", action_dialog)
        context.setContextProperty("catalog", catalog)
        context.setContextProperty("screenScaleFactor", 1.0)
        context.setContextProperty("themeBackend", theme_backend)

        component = QQmlComponent(engine)
        component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "MoonrakerFollowerConfiguration.qml")))
        if component.isError():
            raise RuntimeError(qml_errors(component))

        window = QQuickWindow()
        window.resize(700, 600)
        window.setTitle("moonraker settings capture")
        item = component.create()
        if item is None:
            raise RuntimeError(qml_errors(component))
        # Most plugin documents are Component-rooted (Loader style), so the
        # first create() returns the inner Component and a second create()
        # yields the page Item.  The settings page is Cura.MachineAction-
        # rooted; the guard keeps both shapes working.
        if getattr(item, "create", None) is not None:
            item = item.create()
            if item is None:
                raise RuntimeError(qml_errors(component))
        item.setParentItem(window.contentItem())
        item.setWidth(700)
        item.setHeight(600)
        window.show()
        for _ in range(5):
            app.processEvents()

        # The page has three tabs (Connection / Following / Upload) driven
        # by a TabBar + StackLayout; capture every tab, each fitted to its
        # own content height (the Upload tab is taller than the real
        # dialog's fixed frame, so a shared height would clip it).
        tab_bar = None
        for candidate in descendants(item):
            meta = candidate.metaObject()
            cls = meta.className() if meta else ""
            # UM.TabRow is a QML composite wrapping the controls TabBar, so
            # its class name is TabRow_QMLTYPE_nn; keep the plain-name
            # match too for engines that expose the C++ type instead.
            if cls == "QQuickTabBar" or cls.startswith("TabRow_"):
                tab_bar = candidate
                break
        if tab_bar is None:
            raise RuntimeError("settings tab bar not found in the rendered page")

        tab_names = ("connection", "following", "upload", "diagnostics")
        if tab_bar.property("count") != len(tab_names):
            raise RuntimeError("settings page tab count changed: expected %d, got %s"
                               % (len(tab_names), tab_bar.property("count")))

        def fitted_height():
            """Fitted height for the ACTIVE tab's visible Flickable content.

            Everything below the Flickable's content bottom is fixed
            chrome: 11 px page margin, the tab panel's 10 px bottom gap
            and the action-buttons row (~28 px).
            """
            flickable = None
            for candidate in descendants(item):
                meta = candidate.metaObject()
                if meta.className() == "QQuickFlickable" and candidate.isVisible():
                    try:
                        content = float(candidate.property("contentHeight") or 0.0)
                    except TypeError:
                        content = 0.0
                    if content > 50.0:
                        flickable = candidate
                        break
            if flickable is None:
                return None
            absolute_y = 0.0
            ancestor = flickable
            while ancestor is not None:
                absolute_y += ancestor.y()
                ancestor = ancestor.parentItem()
            content_bottom = absolute_y + float(flickable.property("contentHeight"))
            return int(content_bottom) + 49 + 6  # margins + footer row + air

        for index, name in enumerate(tab_names):
            tab_bar.setProperty("currentIndex", index)
            for _ in range(5):
                app.processEvents()
            # Each tab's page is a Flickable whose content is shorter or
            # taller than the fixed 600-px canvas, so fit the window to the
            # active tab's content (the contentHeight is independent of the
            # viewport size, so measuring before resizing is stable).  The
            # tab panel never stretches.  Width stays at the requested 700.
            target = fitted_height()
            if target is not None and 300 <= target <= 900:
                item.setHeight(target)
                window.resize(700, target)
                for _ in range(5):
                    app.processEvents()
            path = os.path.join(output_dir, "05-settings-%s.png" % name)
            image = window.grabWindow()
            if not image.save(path):
                raise RuntimeError("failed to save " + path)
            diversity = pixel_diversity(image)
            print("captured %s (%dx%d, %d sampled colors)" % (path, image.width(), image.height(), diversity))
            if diversity < 30:
                raise RuntimeError("capture looks blank (%d sampled colors)" % diversity)

        # Tear the scene down in dependency order while the context-property
        # wrappers (manager/actionDialog/catalog) are still referenced.
        # Interpreter-exit destruction order is arbitrary; without this the
        # engine re-evaluates bindings against already-collected wrappers
        # and spews nondeterministic "Cannot read property ... of null"
        # TypeErrors after the grabs (harmless to the PNGs, noisy in CI).
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


if __name__ == "__main__":
    main()
