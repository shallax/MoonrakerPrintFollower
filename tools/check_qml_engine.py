#!/usr/bin/env python3
"""Load every plugin QML document on the REAL engine and fail on any
component error or engine diagnostic the text linters cannot see:
ReferenceErrors from unresolved names, dropped bindings ("Unable to
assign [undefined]"), invalid component bodies and binding loops.
Runs inside the pinned dev container, where the Qt toolchain and the
capture theme live. Each failure prints file + message."""
from __future__ import annotations

import os
import re
import sys

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, qInstallMessageHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from theme_support import ThemeBackend, materialise_theme_assets  # noqa: E402

app = QGuiApplication([])
backend = ThemeBackend(os.path.join(ROOT, "tests", "theme_assets", "cura-light"))
theme_tree = materialise_theme_assets(os.path.join(ROOT, "dist", ".capture-theme"), backend)

engine = QQmlEngine()
engine.addImportPath(os.path.join(ROOT, "tests", "qml_stubs"))
engine.addImportPath(os.path.join(ROOT, "plugins"))
engine.addImportPath(theme_tree)
engine.rootContext().setContextProperty("screenScaleFactor", 1.0)

from capture_settings import FakeActionDialog, SettingsManager  # noqa: E402


class UploadManager(SettingsManager):
    """The upload-dialog surface of MoonrakerOutputDevice (the dialog
    reads its manager through the same ``manager`` context property, so
    the gate stubs one merged object for both documents). Declared as
    pyqtProperty: dynamic setProperty values are not visible to QML
    bindings as typed properties (engine-proven)."""

    uploadPathsChanged = pyqtSignal()

    @pyqtProperty("QVariantList", notify=uploadPathsChanged)
    def uploadPathOptions(self):
        return ["<root>"]

    @pyqtProperty(str, notify=uploadPathsChanged)
    def initialUploadPath(self):
        return "<root>"

    @pyqtProperty(str, notify=uploadPathsChanged)
    def initialUploadFilename(self):
        return "print"

    @pyqtProperty(bool, notify=uploadPathsChanged)
    def initialStartPrint(self):
        return False


# KEEP PYTHON REFERENCES for every QObject handed to the engine as a
# context property: PyQt6 releases wrappers that go out of scope and
# QML then reads the property as null (capture_settings learned this
# the hard way — see its comment on the manager references).
config_manager = UploadManager()
action_dialog = FakeActionDialog()

# External context contracts provided by Cura's machine-action and
# monitor plumbing in production; stubbed so only genuine binding
# mistakes trip the diagnostic filter.
engine.rootContext().setContextProperty("manager", config_manager)
engine.rootContext().setContextProperty("actionDialog", action_dialog)
engine.rootContext().setContextProperty("OutputDevice", {"activePrinter": None})

BAD = re.compile(
    r"ReferenceError|Unable to assign \[undefined\]|is not defined"
    r"|Invalid component body|Binding loop detected|TypeError"
    r"|Cannot assign to non-existent property"
)
diagnostics = []


def handler(mode, context, message) -> None:
    text = str(message)
    if "/plugins/" in text and BAD.search(text):
        diagnostics.append(text)


qInstallMessageHandler(handler)


failures = []
for name in sorted(item for item in os.listdir(os.path.join(ROOT, "plugins")) if item.endswith(".qml")):
    component = QQmlComponent(engine)
    component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", name)))
    if component.isError():
        failures.extend(f"{name}: {error.toString()}" for error in component.errors())
    created = component.create()
    if created is not None:
        created.deleteLater()
    app.processEvents()
# NOTE: the handler is deliberately NEVER restored. The engine's
# destruction at process exit re-evaluates bindings against cleared
# context properties and emits a burst of manager-null TypeErrors;
# with the default handler restored those print to stderr as noise.
# Kept installed, they land in diagnostics AFTER the verdict below —
# swallowed. (Probe-proven: restoring it early re-armed the noise.)
failures.extend(f"engine: {text}" for text in diagnostics)

# Behavioural check: the preview's publish mechanism drives the
# indicator via setProperty, and the gate must follow both ways
# (a root-level visible binding never tracked those changes —
# engine-proven — so the gate lives on an inner row).
indicator = QQmlComponent(engine)
indicator.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "LoadProgressIndicator.qml")))
if indicator.isError():
    failures.extend(f"LoadProgressIndicator: {error.toString()}" for error in indicator.errors())
else:
    instance = indicator.create()
    if instance is None:
        failures.append("LoadProgressIndicator: create() returned None")
    else:
        gate = instance.findChild(QObject, "loadIndicatorContent")
        if gate is None:
            failures.append("LoadProgressIndicator: the inner gate is missing")
        else:
            for busy in (True, False):
                instance.setProperty("busy", busy)
                for _ in range(5):
                    app.processEvents()
                if bool(gate.property("visible")) is not busy:
                    failures.append(f"LoadProgressIndicator: visible did not follow busy={busy}")
        instance.deleteLater()
if failures:
    for failure in failures:
        print(f"QML engine failure: {failure}")
    sys.exit(1)
print(f"QML engine check passed for {len([f for f in os.listdir(os.path.join(ROOT, 'plugins')) if f.endswith('.qml')])} files")
