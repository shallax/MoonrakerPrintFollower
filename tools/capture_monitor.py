"""Deterministic Monitor captures: render the real dashboard QML in an
offscreen engine with stubbed Cura/UM types and fake printer data, and
write PNGs for release notes and layout regression checks.

Real Cura and Uranium QML components and the real cura-light theme
(colours, sizes, fonts and icons under tests/theme_assets) render the
plugin with Cura's actual look; tests/qml_stubs remains the last-resort
fallback for types the upstream trees do not ship. The model and
printer state are the real production objects fed by the shared Qt test
harness. The output is deterministic for a given toolchain (the dev
container pins the fonts), so captures can be diffed across releases.

Usage:  python3 tools/capture_monitor.py <output-directory>
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from unittest.mock import patch

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine

from qt_runtime_support import ScriptedTransport, runtime


def fake_status(state="printing"):
    return {
        "print_stats": {"filename": "benchy.gcode", "state": state, "print_duration": 1800,
                        "info": {"current_layer": 12, "total_layer": 150},
                        "message": ""},
        "virtual_sdcard": {"file_size": 3_000_000, "file_position": 720_000},
        "gcode_move": {"gcode_position": [110.0, 95.0, 4.2, 12.5], "speed_factor": 1.0,
                       "extrude_factor": 1.0, "absolute_coordinates": True},
        "motion_report": {"live_position": [110.0, 95.0, 4.2, 12.5]},
        "fan": {"fan": {"speed": 0.6}},
        "temperature_sensor extruder": {"temperature": 211.4, "target": 210.0},
        "temperature_sensor heater_bed": {"temperature": 58.2, "target": 60.0},
        "filament_switch_sensor runout": {"filament_detected": True},
        "system_stats": {"sysload": 0.42, "memavail": 412000, "cputime": 3.2},
        "mcu mcu": {"mcu_temp": 34.1},
    }


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "dist/screenshots"
    os.makedirs(output_dir, exist_ok=True)
    app = QGuiApplication([])

    from theme_support import install_capture_warning_filter

    install_capture_warning_filter()

    context = runtime()
    qt = context.__enter__()
    try:
        transport = ScriptedTransport()
        root = qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        follower_app = qt.Application(machine_name="Voron v2.4 250")
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=transport)):
            follower = qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(follower_app)
        config_type = qt.load("PrinterConfig").PrinterConfig
        follower.apply_printer_config(config_type(url="http://printer-a", path_follow=False))
        output = qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(follower_app, follower)
        output.start()
        model = output._current.activePrinter

        client = follower.client
        client._handle_http_status({"result": {"status": fake_status("printing")}}, None, client._generation)
        # One configured webcam: the camera bar and a fitted placeholder
        # stream render in the captures.
        model._data._update(webcams=(
            {"uid": "cam-1", "name": "Front", "stream_url": "/webcam?action=stream",
             "enabled": True, "source": "mjpg"},))

        from theme_support import ThemeBackend, materialise_theme_assets, verify_capture_tree
        theme_backend = ThemeBackend(os.path.join(ROOT, "tests", "theme_assets", "cura-light"))
        theme_tree = materialise_theme_assets(os.path.join(ROOT, "dist", ".capture-theme"), theme_backend)

        engine = QQmlEngine()
        # Module resolution is last-path-wins for the Cura/UM modules, so
        # the materialised real tree must be added LAST and the minimal
        # stubs first (they only serve types the real tree lacks).
        engine.addImportPath(os.path.join(ROOT, "tests", "qml_stubs"))
        engine.addImportPath(os.path.join(ROOT, "plugins"))
        engine.addImportPath(theme_tree)
        verify_capture_tree(engine, theme_backend)
        engine_context = engine.rootContext()
        engine_context.setContextProperty("OutputDevice", {"activePrinter": model})
        engine_context.setContextProperty("screenScaleFactor", 1.0)

        component = QQmlComponent(engine)
        component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "plugins", "MoonrakerMonitorBedMesh.qml")))
        if component.isError():
            raise RuntimeError("\n".join(str(e) for e in component.errors()))

        from PyQt6.QtQuick import QQuickWindow
        window = QQuickWindow()
        window.resize(1600, 900)
        window.setTitle("capture")
        item = component.create()
        if item is None:
            raise RuntimeError("\n".join(str(e) for e in component.errors()))
        # The plugin documents are Component-rooted (Loader style), so the
        # first create() returns the inner Component; the second gives the
        # dashboard Item.
        item = item.create()
        if item is None:
            raise RuntimeError("\n".join(str(e) for e in component.errors()))
        item.setParentItem(window.contentItem())
        item.setWidth(1600)
        item.setHeight(900)
        window.show()
        for _ in range(5):
            app.processEvents()

        def grab(name):
            image = window.grabWindow()
            if image.isNull():
                raise RuntimeError("grabWindow produced a null image for " + name)
            # Reject blank renders like the other capture scripts: the
            # dashboard always shows dense content, so a near-uniform
            # frame means the render failed silently.
            sampled = {image.pixelColor(x, y).rgba()
                       for y in range(0, image.height(), 8)
                       for x in range(0, image.width(), 8)}
            if len(sampled) < 20:
                raise RuntimeError("capture looks blank (%d sampled colors)" % len(sampled))
            path = os.path.join(output_dir, name)
            image.save(path)
            print("captured", path)

        grab("01-dashboard-default.png")
        model.setControlsCollapsed(True)
        model.setStatusCollapsed(True)
        model.setInfoCollapsed(True)
        for _ in range(3):
            app.processEvents()
        grab("02-panes-collapsed.png")
        model.setControlsCollapsed(False)
        model.setStatusCollapsed(False)
        model.setInfoCollapsed(False)
        model.setSectionExpanded("toolhead", False)
        model.setSectionExpanded("power", False)
        for _ in range(3):
            app.processEvents()
        grab("03-sections-collapsed.png")

        # Tear the scene down in dependency order while the context-property
        # wrappers are still referenced: at exit the wrappers free in
        # arbitrary order and the engine re-evaluates bindings against
        # already-collected ones, spewing nondeterministic null-context
        # TypeErrors (harmless to the PNGs, noisy in CI logs). See
        # capture_settings.py for the same pattern.
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
        context.__exit__(None, None, None)


if __name__ == "__main__":
    main()
