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

import time

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

from qt_runtime_support import ScriptedSocket, ScriptedTransport, runtime

import capture_contrast


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
        # The motion rows (4.2.0) read the live scalars — the seed
        # carries them so the captures render real values instead of
        # the "—" defaults (the engineering F12 ruling).
        "motion_report": {"live_position": [110.0, 95.0, 4.2, 12.5],
                          "live_velocity": 60.0, "live_extruder_velocity": 0.8},
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
    # Collected across the scenes, re-raised once they are all captured.
    census = capture_contrast.Report()
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
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=transport, socket=ScriptedSocket())):
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
        client._handle_http_status({"result": {"status": fake_status("printing")}}, None, client._generation, time.monotonic())
        # The filament readouts need the slicer's total, which arrives
        # with the metadata fetch — answer it like Moonraker would, on
        # the lane 4.3.0 actually sends (channel "files", method
        # "metadata-only" — the retired "mr-metadata" channel is dead).
        for request in transport.requests:
            if getattr(request, "channel", "") == "files" and \
                    getattr(request, "method", "") == "metadata-only":
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
                    # The motion rows' aux sources (4.2.0): the accel
                    # ceiling and the per-tool filament diameter. The
                    # homed state matches a printing machine so the
                    # jog surfaces render as before.
                    "toolhead": {"extruder": "extruder", "max_accel": 5000.0, "homed_axes": "xyz"},
                    "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
                }
                if step >= 200:
                    auxiliary["heater_generic chamber"] = {"temperature": min(45.0, 24.0 + (step - 200) * 0.15),
                                                           "target": 45.0, "power": 0.2}
                model._data._update(auxiliary=auxiliary)
                model._data.auxiliaryChanged.emit()
                tick[0] += 1.0
        model.sendConsoleCommand("M220 S90")
        model.sendConsoleCommand("M104 S210")
        # DETERMINISM: the console's 2 s settle timer flips those two
        # lines from the pending blue to the saved grey, so which colour
        # a grab caught depended on how long the setup happened to take
        # — the same leg rendered both states on different runs (and the
        # pinned gallery shows the pending blue). Freeze it the way the
        # plugin clocks above are frozen: stop the timer, clear the
        # settle's worklist, and pin the entries pending.
        console = model._console
        console._saved_timer.stop()
        console._persisted = []
        for entry in console._transcript:
            if entry["kind"] == "command":
                entry["saved"] = False
        console.changed.emit()

        from theme_support import ThemeBackend, materialise_theme_assets, verify_capture_tree
        # `or`, not a get() default: an empty CAPTURE_THEME is a value,
        # and it used to select the whole theme-assets parent as the theme.
        theme = os.environ.get("CAPTURE_THEME") or "cura-light"
        theme_backend = ThemeBackend(os.path.join(ROOT, "tests", "theme_assets", theme))
        # CAPTURE_THEME_TREE: parallel capture legs need their own overlay.
        theme_tree = materialise_theme_assets(
            os.environ.get("CAPTURE_THEME_TREE") or os.path.join(ROOT, "dist", ".capture-theme"), theme_backend)

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
        # The harness stands in for Cura's monitor stage, which paints the
        # page ground: this view is transparent by design, so an unpainted
        # window shows Qt's white default and the dark leg's contrast gate
        # would measure a ground the product never has.
        window.setColor(theme_backend.getColor("main_background"))
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
        # The shell loads the dashboard asynchronously
        # (Qt.createComponent); the first grab must wait for the inner
        # Loader to produce the dashboard, or the capture reads blank
        # (the determinism gate caught exactly that after the shell
        # rework).
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            app.processEvents()
            if any(child.property("objectName") == "moonrakerControlsPane"
                   for child in item.findChildren(QQuickItem)):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("the dashboard never rendered inside the capture shell")

        # The e-stop label's contrast gate (the 4.5.0 dark-mode
        # ruling): the idle copy must contrast with the button's
        # ground in BOTH themes — hardcoded black on dark grey is
        # exactly the class the dark capture leg exists to catch.
        # Sampled as a lightness spread over the label's interior:
        # correct text sits far from its ground in either theme.
        emergency = [child for child in item.findChildren(QQuickItem)
                     if child.property("objectName") == "moonrakerEmergencyButton"]
        if emergency:
            button = emergency[0]
            if button.isVisible() and button.width() > 20 and button.height() > 10:
                corner = button.mapToScene(QPointF(0, 0))
                inset_x = max(4, int(button.width() * 0.08))
                inset_y = max(4, int(button.height() * 0.15))
                scene_shot = window.grabWindow()
                lightness = set()
                for y in range(inset_y, int(button.height()) - inset_y, 3):
                    for x in range(inset_x, int(button.width()) - inset_x, 3):
                        lightness.add(scene_shot.pixelColor(
                            int(corner.x() + x), int(corner.y() + y)).lightness())
                spread = (max(lightness) - min(lightness)) if lightness else 0
                if spread < 76:
                    raise RuntimeError(
                        "the e-stop label lacks contrast with its ground (lightness spread %d) — "
                        "a wrong-coloured glyph slipped past the capture theme" % spread)
                print("e-stop label lightness spread:", spread)
        else:
            print("e-stop label contrast: the emergency button was not visible — skipped")

        def chart_region(scene, chart):
            """One visible chart's pixel signature — the sampled
            colours of its bounding rect. The threaded canvas never
            reports its painted-size properties in this runtime, so
            completion is settled on the PIXELS instead."""
            top_left = chart.mapToScene(QPointF(0, 0))
            pixels = []
            for y in range(4, max(5, int(chart.height())), 6):
                for x in range(4, max(5, int(chart.width())), 6):
                    pixels.append(scene.pixelColor(int(top_left.x() + x),
                                                   int(top_left.y() + y)).rgba())
            return tuple(pixels)

        def settle_chart_canvases(timeout_ms=5000):
            """The chart's data canvas paints on the RENDER thread:
            after any state flip that requests a paint, wait until
            every visible chart has landed its FINAL frame — two
            consecutive grabs whose chart regions are identical (and
            non-blank) mean the paint landed and nothing else is
            pending. A grab before that catches a blank or
            half-painted texture, which is the dark-theme
            03-sections-collapsed determinism failure."""
            deadline = time.monotonic() + timeout_ms / 1000
            previous = None
            while time.monotonic() < deadline:
                scene = window.grabWindow()
                charts = [child for child in item.findChildren(QQuickItem)
                          if "TemperatureChart" in child.metaObject().className()
                          and child.isVisible() and child.width() > 5]
                if not charts:
                    return  # no visible chart: nothing to settle
                signature = tuple((index, chart_region(scene, chart))
                                  for index, chart in enumerate(charts))
                blank = any(len(set(region)) < 12 for _, region in signature)
                if not blank and signature == previous:
                    return
                previous = signature
                app.processEvents()
                time.sleep(0.01)
            raise RuntimeError(
                "the chart capture never settled: the threaded canvas paint did not land")

        def grab(name):
            settle_chart_canvases()
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
            # Contrast census: the pinned screenshots catch drift, not
            # unreadability, so every text element in this frame is read
            # against the ground its pixels actually show. It reads the
            # image only, and runs AFTER the save so a census failure is a
            # verdict on the scene, never a missing screenshot. The report
            # holds the verdict until the last scene is captured.
            census.audit(window.contentItem(), image, name)

        grab("01-dashboard-default.png")
        # The stacked progress track (the 4.4.0 bars replaced the
        # OutlineProgressBar family): find the track through the
        # pause fill's parent and sample the PRINT fill's interior
        # for the accent blue — the styling regression proof, the
        # old bars' role. The seed's 24% progress must render as a
        # real fill, never a silent styling loss.
        tracks = []
        for child in item.findChildren(QQuickItem):
            if child.objectName() == "nextPauseFill":
                track = child.parentItem()
                if track is not None and track.isVisible() \
                        and track.width() > 10 and track.height() > 4:
                    tracks.append(track)
        if not tracks:
            raise RuntimeError("visible stacked progress track not found in the scene")
        scene = window.grabWindow()
        blue = QColor(25, 110, 240).name()
        filled = 0
        for track in tracks:
            top_left = track.mapToScene(QPointF(0, 0))
            # The print fill occupies the BOTTOM half of the track —
            # one sample row just above the bottom edge, along the
            # fill's leading span (≥10 hits at 4 px pitch = ≥40 px
            # of contiguous blue, calibrated to the 24% seed).
            hits = sum(1 for x in range(5, min(int(track.width()), 400), 4)
                       if scene.pixelColor(int(top_left.x() + x),
                                           int(top_left.y() + track.height() - 2)).name() == blue)
            if hits >= 10:
                filled += 1
        if not filled:
            raise RuntimeError("the stacked bar's print fill looks empty (no accent-blue pixels)")
        print("stacked print fill accent-blue:", filled, "of", len(tracks), "tracks")
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
        settle_chart_canvases()
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
    census.require_clean()


if __name__ == "__main__":
    main()
