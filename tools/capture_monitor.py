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

from PyQt6.QtCore import QPointF, QUrl
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine

from qt_runtime_support import ScriptedTransport, runtime


def fake_status(state="printing"):
    return {
        # filament_used is TOP-LEVEL in real Klipper print_stats (the
        # info dict only ever carries layer counters) — the seed mirrors
        # the live shape so the captures test the real parse.
        "print_stats": {"filename": "benchy.gcode", "state": state, "print_duration": 1800,
                        "filament_used": 3500.0,
                        "info": {"current_layer": 12, "total_layer": 150},
                        "message": ""},
        "virtual_sdcard": {"file_size": 3_000_000, "file_position": 720_000, "progress": 0.24},
        "gcode_move": {"gcode_position": [110.0, 95.0, 4.2, 12.5], "speed_factor": 1.0,
                       "extrude_factor": 1.0, "absolute_coordinates": True},
        "motion_report": {"live_position": [110.0, 95.0, 4.2, 12.5]},
        "fan": {"fan": {"speed": 0.6}},
        "temperature_sensor extruder": {"temperature": 211.4, "target": 210.0},
        "temperature_sensor heater_bed": {"temperature": 58.2, "target": 60.0},
        "filament_switch_sensor runout": {"filament_detected": True},
        "system_stats": {"sysload": 0.42, "memavail": 412000, "cputime": 3.2},
        "mcu mcu": {"mcu_temp": 34.1},
        "bed_mesh": {
            "profile_name": "default",
            "mesh_min": [10.0, 10.0], "mesh_max": [240.0, 240.0],
            "probed_matrix": [
                [0.02, 0.05, 0.08, 0.11, 0.14],
                [0.03, 0.06, 0.09, 0.12, 0.15],
                [0.01, 0.04, 0.07, 0.10, 0.13],
                [0.00, 0.03, 0.06, 0.09, 0.12],
                [-0.01, 0.02, 0.05, 0.08, 0.11],
            ],
        },
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
        # DETERMINISM: every live input the scene renders must be mocked.
        # The model's time module is patched during seeding below, but
        # wall-clock text rendered from plugin modules is not: the
        # formatter's finish-clock reads datetime.now() straight off the
        # wall (MonitorFormatting.monitorFinish), and so does the
        # Preview follower's ETA finish (PreviewFollower.update_eta), so
        # captures made in different minutes differed by one clock glyph
        # and CI's byte-compare failed. Freeze both clocks for the life
        # of this process: the pinned container then renders the same
        # bytes regardless of when the capture runs. The Qt runtime
        # registers plugin modules under synthetic names (the same trap
        # documented for the model below), so EVERY module object loaded
        # from one of the frozen source files is patched — after the
        # plugin tree has been loaded by the runtime.
        from datetime import datetime as _real_datetime

        class FrozenDatetime(_real_datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 9, 12, 0, 0)

        _frozen_clock_sources = (
            os.path.join(ROOT, "plugins", "MonitorFormatting.py"),
            os.path.join(ROOT, "plugins", "PreviewFollower.py"),
        )

        def _freeze_plugin_clocks():
            for _mod in list(sys.modules.values()):
                if getattr(_mod, "__file__", "") in _frozen_clock_sources:
                    _mod.datetime = FrozenDatetime
        transport = ScriptedTransport()
        root = qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        follower_app = qt.Application(machine_name="Voron v2.4 250")
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=transport)):
            follower = qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(follower_app)
        _freeze_plugin_clocks()
        config_type = qt.load("PrinterConfig").PrinterConfig
        # The console transcript seeds the terminal pane: a typed line,
        # a plain response and a "!!" error, all restored (they came
        # from config, so the pane greys them — exercising the feed's
        # three delegate styles in the captures).
        follower.apply_printer_config(config_type(url="http://printer-a", path_follow=False, console_transcript=[
            {"kind": "command", "text": "M104 S200", "error": False},
            {"kind": "response", "text": "ok", "error": False},
            {"kind": "response", "text": "!! Heater extruder not heating", "error": True},
        ]))
        output = qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(follower_app, follower)
        output.start()
        model = output._current.activePrinter

        client = follower.client
        client._handle_http_status({"result": {"status": fake_status("printing")}}, None, client._generation)
        # The filament readouts need the slicer's total, which arrives
        # with the metadata fetch — answer it like Moonraker would.
        for request in transport.requests:
            if getattr(request, "channel", "") == "mr-metadata":
                request.callback({"result": {"layer_height": 0.2, "filament_total": 42000.0,
                                             "estimated_time": 3600}}, None)
                break
        # The filament readouts need the slicer's total, which arrives
        # with the metadata fetch — answer it like Moonraker would.
        for request in transport.requests:
            if getattr(request, "channel", "") == "mr-metadata":
                request.callback({"result": {"layer_height": 0.2, "filament_total": 42000.0,
                                             "estimated_time": 3600}}, None)
                break
        # One configured webcam: the camera bar and a fitted placeholder
        # stream render in the captures.
        model._data._update(webcams=(
            {"uid": "cam-1", "name": "Front", "stream_url": "/webcam?action=stream",
             "enabled": True, "source": "mjpg"},))
        model._data._update(endstops={"x": "TRIGGERED", "y": "open", "z": "open"})

        # Synthetic temperature history: ~10 minutes at the 1 s cadence,
        # a warm-up curve for the hotend, a steady bed, and a chamber
        # sensor that starts late (exercises the snap-by-time hover).
        # Only the MODEL's time reference is patched — patching the
        # global time module makes unrelated machinery that busy-waits
        # on monotonic spin forever.
        from types import SimpleNamespace
        # Patch the module the MODEL INSTANCE actually uses — the Qt
        # runtime registers it under a synthetic name, so importing
        # "plugins.MoonrakerMonitorModel" again would patch the wrong
        # object and leave all samples at elapsed 0 (filling forever).
        model_module = sys.modules[type(model).__module__]
        tick = [1000.0]
        fake_time = SimpleNamespace(monotonic=lambda: tick[0], time=lambda: 1700000000.0 + tick[0])
        with patch.object(model_module, "time", fake_time):
            for step in range(610):
                auxiliary = {
                    "extruder": {"temperature": min(210.0, 24.0 + step * 0.4), "target": 210.0,
                                 "power": 0.9 if step < 460 else 0.3},
                    "heater_bed": {"temperature": min(60.0, 24.0 + step * 0.1), "target": 60.0,
                                   "power": 0.5 if step < 350 else 0.1},
                }
                if step >= 200:
                    auxiliary["heater_generic chamber"] = {"temperature": min(45.0, 24.0 + (step - 200) * 0.15),
                                                           "target": 45.0, "power": 0.2}
                model._data._update(auxiliary=auxiliary)
                model._data.auxiliaryChanged.emit()
                tick[0] += 1.0
        model.sendConsoleCommand("M220 S90")
        model.sendConsoleCommand("M104 S210")

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

        from PyQt6.QtQuick import QQuickItem, QQuickWindow
        window = QQuickWindow()
        window.resize(1600, 900)
        window.setTitle("capture")
        item = component.create()
        if item is None:
            raise RuntimeError("\n".join(str(e) for e in component.errors()))
        # Item-rooted documents return the item directly; the old
        # Component-rooted shape returned a Component needing one more
        # create(). Handle both.
        if hasattr(item, "create"):
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
        # The outline bars must render their Cura-blue fill: sample each
        # visible bar's interior for the accent colour. This doubles as
        # the styling regression test — a fill that silently stops
        # rendering leaves the captures looking unstyled, and this is
        # how the seed missing virtual_sdcard.progress was caught.
        bars = [child for child in item.findChildren(QQuickItem)
                if "OutlineProgressBar" in child.metaObject().className()
                and child.isVisible() and child.width() > 10 and child.height() > 4]
        if not bars:
            raise RuntimeError("visible OutlineProgressBar not found in the scene")
        scene = window.grabWindow()
        blue = QColor(25, 110, 240).name()
        lining = QColor(192, 193, 194).name()
        filled = 0
        for bar in bars:
            top_left = bar.mapToScene(QPointF(0, 0))
            # ≥10 hits at 4 px pitch = ≥40 px of contiguous blue: the
            # assertion is calibrated to the 24%-progress seed, so the
            # fixture's bar must stay ~165 px wide or more — a layout
            # change that shrinks the pane (or zeroes the seed's
            # progress) would false-fail here before the render itself.
            hits = sum(1 for x in range(5, min(int(bar.width()), 400), 4)
                       for y in range(1, max(2, int(bar.height()) - 1))
                       if scene.pixelColor(int(top_left.x() + x), int(top_left.y() + y)).name() == blue)
            if hits >= 10:
                filled += 1
            # The "little rounded ends" contract: Cura.RoundedRectangle
            # forces radius 0 unless cornerSide is set, so a square track
            # paints the corner pixel in the border colour while a
            # rounded one leaves the corner clear (this pins that fix).
            corner = scene.pixelColor(int(top_left.x()), int(top_left.y())).name()
            if corner == lining:
                raise RuntimeError("progress bar corners render square (cornerSide missing)")
        if not filled:
            raise RuntimeError("progress bar fill looks empty (no accent-blue pixels)")
        print("bar fill accent-blue:", filled, "of", len(bars), "bars")
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
        collapsed = window.grabWindow()
        # Same styling contract for the outline sliders: at least one
        # visible slider must show the blue fill inside its track.
        sliders = [child for child in item.findChildren(QQuickItem)
                   if "OutlineSlider" in child.metaObject().className()
                   and child.isVisible() and child.width() > 10]
        slider_blue = 0
        for slider in sliders:
            top_left = slider.mapToScene(QPointF(0, 0))
            hits = sum(1 for x in range(5, min(int(slider.width()), 400), 4)
                       for y in range(1, max(2, int(slider.height()) - 1))
                       if collapsed.pixelColor(int(top_left.x() + x), int(top_left.y() + y)).name() == blue)
            if hits >= 10:
                slider_blue += 1
        if not slider_blue:
            raise RuntimeError("no slider shows the blue fill")
        print("slider fill accent-blue:", slider_blue, "of", len(sliders), "sliders")
        # The dashboard document wraps the monitor in an outer Item (the
        # wrapper has only `printer`), so the pop-over flag lives on the
        # inner monitor root — find it by property, never by assumption.
        popover_hosts = [child for child in item.findChildren(QQuickItem)
                         if child.metaObject().indexOfProperty("openPopOver") >= 0]
        if not popover_hosts:
            raise RuntimeError("no item owns the openPopOver property")
        host = popover_hosts[0]
        if not host.setProperty("openPopOver", "chart"):
            raise RuntimeError("openPopOver setProperty returned False")
        for _ in range(3):
            app.processEvents()
        grab("07-chart-popover.png")
        opened = window.grabWindow()
        if opened == collapsed:
            raise RuntimeError("the chart pop-over capture is identical to the collapsed scene")
        host.setProperty("openPopOver", "")

        # The mini-chart region must contain series-coloured pixels after
        # the synthetic feed: this is also the regression test for the
        # requestPaint discipline (a chart that never repaints leaves the
        # region near-uniform).
        # The Loader caches the deactivated pop-over chart (0×0,
        # invisible), so the live mini chart is the visible, sized one.
        chart_items = [child for child in item.findChildren(QQuickItem)
                       if "TemperatureChart" in child.metaObject().className()
                       and child.property("compact") is True
                       and child.isVisible() and child.width() > 5]
        if not chart_items:
            raise RuntimeError("visible compact TemperatureChart not found in the scene")
        chart = chart_items[0]
        top_left = chart.mapToScene(QPointF(0, 0))
        scene = window.grabWindow()
        colours = {scene.pixelColor(int(top_left.x() + x), int(top_left.y() + y)).name()
                   for x in range(5, min(160, int(chart.width())), 7)
                   for y in range(5, int(chart.height()), 4)}
        if len(colours) < 12:
            raise RuntimeError("mini chart region looks blank (%d colours)" % len(colours))
        print("mini chart region colours:", len(colours))
        for _ in range(3):
            app.processEvents()

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
