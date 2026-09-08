"""Theme backend for the deterministic Monitor captures.

Resolves the real cura-light theme — colours (with base-colour
references and inheritance), sizes (scaled by the font em, exactly as
Uranium's Theme binding does), fonts and the icons copied into
tests/theme_assets — and exposes it to QML as the ``themeBackend``
context property behind the ``UM.Theme`` singleton stub
(tests/theme_assets/UM/Theme.qml).

This is capture-harness tooling only; production Cura supplies the real
Theme binding.
"""
from __future__ import annotations

import json
import os
import sys

from PyQt6.QtCore import QObject, QSizeF, QUrl, pyqtSlot
from PyQt6.QtQml import QQmlComponent
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication


class ThemeBackend(QObject):
    def __init__(self, theme_dir: str, parent=None):
        super().__init__(parent)
        self._colors: dict = {}
        self._sizes: dict = {}
        self._fonts: dict = {}
        self._icons: dict = {}
        self._em = int(QFontMetrics(QGuiApplication.font()).ascent()) or 14
        self._system_font_size = QGuiApplication.font().pointSizeF() or 10.0
        self._load(os.path.join(theme_dir, "theme.json"))
        self._load_icons(os.path.join(theme_dir, "icons"))

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        inherited = (data.get("metadata") or {}).get("inherits")
        if inherited:
            parent = os.path.normpath(os.path.join(os.path.dirname(path), "..", inherited))
            self._load(os.path.join(parent, "theme.json"))
        # Base colours first, then the named colour table (string values
        # reference a base colour by name, exactly like Uranium).
        for name, rgba in (data.get("base_colors") or {}).items():
            self._colors[name] = QColor(*rgba)
        for name, value in (data.get("colors") or {}).items():
            if isinstance(value, str):
                referenced = self._colors.get(value)
                self._colors[name] = QColor(referenced) if referenced else QColor()
            else:
                self._colors[name] = QColor(*value)
        # Sizes are grid units scaled by the font em.
        for name, size in (data.get("sizes") or {}).items():
            self._sizes[name] = QSizeF(round(size[0] * self._em), round(size[1] * self._em))
        for name, font in (data.get("fonts") or {}).items():
            qfont = QFont(font.get("family", QGuiApplication.font().family()))
            if font.get("bold"):
                qfont.setBold(True)
            else:
                qfont.setWeight(font.get("weight", 500))
            qfont.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, font.get("letterSpacing", 0))
            qfont.setItalic(font.get("italic", False))
            qfont.setPointSizeF(int(font.get("size", 1) * self._system_font_size))
            self._fonts[name] = qfont

    def _load_icons(self, icons_dir: str) -> None:
        default = os.path.join(icons_dir, "default")
        if not os.path.isdir(default):
            return
        for filename in os.listdir(default):
            if filename.endswith(".svg"):
                self._icons[filename[:-4]] = QUrl.fromLocalFile(os.path.join(default, filename))

    @pyqtSlot(str, result=QColor)
    def getColor(self, name: str) -> QColor:
        return self._colors.get(name, QColor())

    @pyqtSlot(str, result=QSizeF)
    def getSize(self, name: str) -> QSizeF:
        return self._sizes.get(name, QSizeF())

    @pyqtSlot(str, result=QFont)
    def getFont(self, name: str) -> QFont:
        return self._fonts.get(name, QGuiApplication.font())

    @pyqtSlot(str, result=QUrl)
    def getIcon(self, name: str) -> QUrl:
        return self._icons.get(name, QUrl())

    @pyqtSlot(str, result=QUrl)
    def getImage(self, name: str) -> QUrl:
        return QUrl()

    @pyqtSlot(str, result=bool)
    def iconExists(self, name: str) -> bool:
        return name in self._icons

    def generate_theme_qml(self) -> str:
        """A self-contained ``UM.Theme`` singleton as literal QML.

        Qt 6.11 pragma-Singletons cannot read root-context properties, so
        the bridge-singleton approach (Theme.qml calling a ``themeBackend``
        context object) resolves to undefined at render time. Capture
        scripts materialise this generated singleton instead, with the
        palette/sizes/fonts embedded verbatim from the loaded theme.
        """
        colors = {}
        for name, color in sorted(self._colors.items()):
            colors[f'            "{name}": "{color.name()}"'] = None
        sizes = {}
        for name, size in sorted(self._sizes.items()):
            sizes[f'            "{name}": Qt.size({round(size.width())}, {round(size.height())})'] = None
        fonts = {}
        for name, qfont in sorted(self._fonts.items()):
            fonts[f'            "{name}": Qt.font({{ family: "{qfont.family()}", pointSize: {qfont.pointSizeF()}, '
                 f'bold: {"true" if qfont.bold() else "false"}, italic: {"true" if qfont.italic() else "false"} }})'] = None
        icons = {}
        for name, url in sorted(self._icons.items()):
            icons[f'            "{name}": "{url.toLocalFile()}"'] = None
        return """pragma Singleton
import QtQuick 2.15
QtObject {
    function getColor(name) {
        var palette = {
%s
        }
        return palette[name] !== undefined ? palette[name] : "#000000"
    }
    function getSize(name) {
        var sizes = {
%s
        }
        return sizes[name] !== undefined ? sizes[name] : Qt.size(0, 0)
    }
    function getFont(name) {
        var fonts = {
%s
        }
        return fonts[name] !== undefined ? fonts[name] : Qt.font({ family: "sans-serif", pointSize: 10 })
    }
    function getIcon(name) {
        var icons = {
%s
        }
        return icons[name] !== undefined ? icons[name] : ""
    }
    function getImage(name) { return "" }
    function iconExists(name) { return false }
}
""" % (",\n".join(colors), ",\n".join(sizes), ",\n".join(fonts), ",\n".join(icons))


INERT_TOOLTIP = """// Capture-only stand-in: the real UM.ToolTip depends on UM.Enums and
// UM.PointingRectangle, which have no QML assets, so the real chain
// cannot compile in the capture engine. This Item provides the surface
// the real widgets touch (UM.CheckBox, UM.TooltipArea,
// Cura.ActionButton) and stays invisible and inert.
import QtQuick 2.15

Item {
    property string text: ""
    property string tooltipText: ""
    property int delay: 0
    property int contentAlignment: Qt.AlignLeft
    property real arrowSize: 0
    property var targetPoint: Qt.point(0, 0)
    function show() {}
    function hide() {}
}
"""

INERT_POINTING_RECTANGLE = """// Capture-only stand-in for Uranium's Python-registered
// UM.PointingRectangle; hover-only paths never draw it offscreen.
import QtQuick 2.15
Item {
    property var target: null
    property real offset: 0
    property real arrowSize: 5
    property color color: "#000000"
}
"""

# Qt 6.11 informationals that are inherent to the plugin's deliberate
# architecture and appear in production Cura too:
#   * the plugin documents root in Component (the Loader-style pattern
#     Cura's monitor-view loading expects) — deprecated but honoured;
#   * windows defined by pure-QML modules (UM.Dialog and friends) cannot
#     propagate size hints to a module plugin that does not exist.
# Everything else still prints, so new warnings stay visible.
BENIGN_WARNINGS = (
    "Using a Component as the root of a QML document is deprecated",
    "This plugin does not support propagateSizeHints()",
)


def install_capture_warning_filter():
    """Drop the known-benign Qt informationals above from capture output."""
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    def handler(msg_type, context, message):
        if msg_type == QtMsgType.QtWarningMsg and any(marker in message for marker in BENIGN_WARNINGS):
            return
        location = "%s:%d: " % (context.file, context.line) if context.file else ""
        print(location + message, file=sys.stderr)

    qInstallMessageHandler(handler)


def verify_capture_tree(engine, backend):
    """Fail loudly unless the engine resolves the REAL theme and icons.

    The silent-fallback trap: a broken import chain renders with the
    stub palette (#f5f5f5 background, no icons) while every script
    reports success. Probing the singleton proves the materialised
    tree serves the engine before any pixels are captured.
    """
    component = QQmlComponent(engine)
    component.setData(b"import QtQuick 2.15\nimport UM 1.5 as UM\n"
                      b"Item { property string bg: UM.Theme.getColor(\"main_background\")\n"
                      b"property string icon: String(UM.Theme.getIcon(\"House\")) }",
                      QUrl("verify.qml"))
    if component.isError():
        raise RuntimeError("capture tree verification failed: %s" % component.errors()[0].toString())
    probe = component.create()
    if probe is None or probe.property("bg") != backend.getColor("main_background").name():
        raise RuntimeError("capture tree resolved the stub theme, not the real one")
    if not probe.property("icon"):
        raise RuntimeError("capture tree resolved no icons")


def materialise_theme_assets(target_dir, backend, *, stub_dir=None, root=None):
    """Copy the committed theme assets into ``target_dir`` and complete the
    tree so the real Cura/UM component chain loads offscreen:

    - stub UM.ColorImage and UM.I18nCatalog (Python-registered in
      production) spliced in from tests/qml_stubs;
    - an inert UM.ToolTip stand-in (removes the Enums/PointingRectangle
      dependency chain that only matters on hover);
    - the UM qmldir regenerated across versions 1.0-1.8;
    - a literal UM.Theme singleton generated from the loaded theme
      (Qt 6.11 pragma-Singletons must be self-contained).

    Returns the target directory.
    """
    import shutil
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stub_dir = stub_dir or os.path.join(root, "tests", "qml_stubs")
    assets = os.path.join(root, "tests", "theme_assets")
    shutil.copytree(assets, target_dir, dirs_exist_ok=True)
    um_dir = os.path.join(target_dir, "UM")
    for name in ("ColorImage.qml", "I18nCatalog.qml"):
        shutil.copy(os.path.join(stub_dir, "UM", name), os.path.join(um_dir, name))
    with open(os.path.join(um_dir, "ToolTip.qml"), "w", encoding="utf-8") as handle:
        handle.write(INERT_TOOLTIP)
    # The real RecolorImage overlays a shader, which draws nothing under
    # the offscreen platform; render the SVG natively instead (the tint
    # is ignored — Cura's icons are black, right for the light theme).
    with open(os.path.join(um_dir, "RecolorImage.qml"), "w", encoding="utf-8") as handle:
        handle.write("""import QtQuick 2.15
Item {
    property url source: ""
    property color color: "#000000"
    Image {
        anchors.fill: parent
        source: parent.source
        fillMode: Image.PreserveAspectFit
        visible: parent.source != ""
    }
}
""")
    with open(os.path.join(um_dir, "PointingRectangle.qml"), "w", encoding="utf-8") as handle:
        handle.write(INERT_POINTING_RECTANGLE)
    files = sorted(f[:-4] for f in os.listdir(um_dir) if f.endswith(".qml") and f != "Theme.qml")
    lines = ["module UM"]
    for version in ("1.0", "1.4", "1.5", "1.7", "1.8"):
        lines.append(f"singleton Theme {version} Theme.qml")
        for name in files:
            lines.append(f"{name} {version} {name}.qml")
    with open(os.path.join(um_dir, "qmldir"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    # Module resolution is per-module, not per-type: splice the stub Cura
    # types the real tree does not declare (NetworkMJPGImage, CheckBox,
    # TabRowButton, ...) into the materialised Cura module.
    cura_dir = os.path.join(target_dir, "Cura")
    real_qmldir = os.path.join(cura_dir, "qmldir")
    declared = set()
    for line in open(real_qmldir, encoding="utf-8"):
        parts = line.split()
        if len(parts) == 3:
            declared.add(parts[0])
    additions = []
    for line in open(os.path.join(stub_dir, "Cura", "qmldir"), encoding="utf-8"):
        parts = line.split()
        if len(parts) == 3 and parts[0] not in declared:
            stub_file = os.path.join(stub_dir, "Cura", parts[2])
            if os.path.exists(stub_file):
                shutil.copy(stub_file, os.path.join(cura_dir, os.path.basename(parts[2])))
                additions.append("%s %s %s" % (parts[0], parts[1], os.path.basename(parts[2])))
                declared.add(parts[0])
    if additions:
        with open(real_qmldir, "a", encoding="utf-8") as handle:
            handle.write("\n".join(additions) + "\n")
    with open(os.path.join(um_dir, "Theme.qml"), "w", encoding="utf-8") as handle:
        handle.write(backend.generate_theme_qml())
    return target_dir
