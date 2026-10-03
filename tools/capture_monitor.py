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

from PyQt6.QtCore import QObject, QPointF, QUrl
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
        # The plate's seed (4.6.0): a constant three-object plate so
        # the capture legs render the map deterministically — the same
        # payload in both determinism legs (the engineering F15 rule).
        "exclude_object": {
            "objects": [
                {"name": "BENCHY_STL", "center": [62.0, 62.0],
                 "polygon": [[42.0, 42.0], [42.0, 82.0], [82.0, 82.0], [82.0, 42.0]]},
                {"name": "BENCHY_STL_1", "center": [125.0, 125.0],
                 "polygon": [[105.0, 105.0], [105.0, 145.0], [145.0, 145.0], [145.0, 105.0]]},
                {"name": "BENCHY_STL_2", "center": [188.0, 62.0],
                 "polygon": [[168.0, 42.0], [168.0, 82.0], [208.0, 82.0], [208.0, 42.0]]},
            ],
            "excluded_objects": ["BENCHY_STL_1"],
            "current_object": "BENCHY_STL_2",
        },
    }


def _preload_dashboard(engine):
    dashboard = QQmlComponent(engine)
    dashboard.loadUrl(QUrl.fromLocalFile(os.path.join(
        ROOT, "mpf", "monitor", "MoonrakerMonitorDashboard.qml")))
    if not dashboard.isReady():
        raise RuntimeError("dashboard compilation failed: " +
                           "\n".join(str(e) for e in dashboard.errors()))
    return dashboard


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
            os.path.join(ROOT, "mpf", "monitor", "MonitorFormatting.py"),
            os.path.join(ROOT, "mpf", "preview", "PreviewFollower.py"),
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
        # "mpf.monitor.MoonrakerMonitorModel" again would patch the wrong
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
                # The chart's series feeds from the 1 s ticks — never
                # from the data updates — so observe it HERE with the
                # frozen clock, or the scenes' event pump would fill
                # it with live-ticked samples stamped with the real
                # wall minute (the 01/07 byte drift CI's compare
                # catches: the axis clock text carried the capture's
                # own minute, and the curve's extent carried the
                # pump's duration).
                model._temperature._history.observe(auxiliary, tick[0],
                                       1700000000.0 + tick[0])
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

        # The seeded history is the chart's whole story: stop the tick
        # before the scenes pump the event loop, or a live sample
        # stamped with the real wall minute lands on top of the frozen
        # series (the console's settle timer above froze the same way).
        model._chart_timer.stop()

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
        engine.addImportPath(os.path.join(ROOT, "mpf"))
        engine.addImportPath(theme_tree)
        verify_capture_tree(engine, theme_backend)
        engine_context = engine.rootContext()
        engine_context.setContextProperty("OutputDevice", {"activePrinter": model})
        engine_context.setContextProperty("screenScaleFactor", 1.0)

        # Capture has no startup latency requirement: warm the component
        # before the shell's asynchronous Loader begins its readiness wait.
        dashboard = _preload_dashboard(engine)
        component = QQmlComponent(engine)
        component.loadUrl(QUrl.fromLocalFile(os.path.join(ROOT, "mpf", "monitor", "MoonrakerMonitorBedMesh.qml")))
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
            raise RuntimeError(
                "the dashboard never rendered inside the capture shell "
                f"(preloaded component status: {dashboard.status()})")

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

        # The consecutive-frame contract AND its span: the capture
        # points sit right after batches of layout mutations (pane
        # expansions, section collapses), whose reflow, readout-fit
        # timers and the last scheduled render-thread paint keep
        # changing the frame after the chart itself has painted.
        # Under load the frames arrive slowly, so a frame count alone
        # can certify a quiet gap BETWEEN two bursts — the identical
        # run must also span long enough for every pending one-shot
        # (the 200 ms fit timers, the threaded paint) to have landed.
        REQUIRED_IDENTICAL_FRAMES = 3
        SETTLE_SPAN_SECONDS = 0.5

        def pending_layout_retries():
            """The QML readout-fit retries can fire after a quiet frame run.

            Under CI load their 200 ms timers may be delivered late. A
            screenshot taken while one is armed records the old strip or
            section layout even when its pixels have been stable so far.
            """
            return any(
                timer.property("running")
                for timer in item.findChildren(QObject)
                if timer.metaObject().className() == "QQmlTimer"
                and timer.property("interval") == 200
                and timer.property("repeat") is False
            )

        def settled_window(timeout_ms=10000):
            """The capture transaction: pump events, grab the whole
            window, and require three consecutive complete frames to
            be pixel-identical AND the identical run to span the
            settle span before the scene counts as settled. The exact
            image that proved the stability is RETURNED — the caller
            saves this image and never grabs again, so the proven
            frame and the saved frame can never diverge (the
            settle-then-re-grab race behind the 03-sections-collapsed
            nondeterminism)."""
            deadline = time.monotonic() + timeout_ms / 1000
            previous = None
            identical = 0
            first_identical = None
            image = None
            while time.monotonic() < deadline:
                app.processEvents()
                image = window.grabWindow()
                if previous is not None and image == previous:
                    if identical == 0:
                        first_identical = time.monotonic()
                    identical += 1
                    if not pending_layout_retries() \
                            and identical >= REQUIRED_IDENTICAL_FRAMES - 1 \
                            and time.monotonic() - first_identical >= SETTLE_SPAN_SECONDS:
                        return image
                else:
                    identical = 0
                    first_identical = None
                previous = image
                time.sleep(0.02)
            raise RuntimeError(
                "the capture window never settled across %d identical frames over %.1fs"
                % (REQUIRED_IDENTICAL_FRAMES, SETTLE_SPAN_SECONDS))

        def grab(name, card=None):
            image = settled_window()
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
            # Contrast census reads actual pixels, holding the verdict until
            # every scene has been captured. Audit card descendants against
            # their scene coordinates;
            # covered dashboard text is not part of a popover photograph.
            census.audit(card or window.contentItem(), image, name)
            path = os.path.join(output_dir, name)
            if card is not None:
                corner = card.mapToScene(QPointF(0, 0))
                image = image.copy(int(corner.x()), int(corner.y()),
                                   int(card.width()), int(card.height()))
            image.save(path)
            print("captured", path)

        def popover_card(title):
            # Cards are found through the shell's own pointer barrier:
            # a card that derives from MonitorPopOver in its own file
            # carries that file's class name, not the shell's.
            cards = []
            for barrier in item.findChildren(QQuickItem, "monitorPopoverPointerBarrier"):
                card = barrier.parentItem()
                if card is not None and card.property("title") == title and card.isVisible():
                    cards.append(card)
            if len(cards) != 1:
                raise RuntimeError("expected one visible popover: " + title)
            return cards[0]

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
        control_flick = item.findChild(QQuickItem, "moonrakerControlsFlick")
        if control_flick is None:
            raise RuntimeError("the controls flickable is missing")
        slider_blue = 0
        for slider in sliders:
            # The detection section can move every slider below the
            # viewport; bring each candidate into view before checking
            # pixels rather than sampling beyond the captured window.
            position = slider.mapToItem(control_flick, QPointF(0, 0))
            scroll = max(0, min(control_flick.property("contentHeight") - control_flick.height(),
                                control_flick.property("contentY") + position.y()
                                - control_flick.height() / 2))
            control_flick.setProperty("contentY", scroll)
            collapsed = settled_window()
            top_left = slider.mapToScene(QPointF(0, 0))
            hits = sum(1 for x in range(5, min(int(slider.width()), 400), 4)
                       for y in range(1, max(2, int(slider.height()) - 1))
                       if 0 <= top_left.x() + x < collapsed.width()
                       and 0 <= top_left.y() + y < collapsed.height()
                       and collapsed.pixelColor(int(top_left.x() + x), int(top_left.y() + y)).name() == blue)
            if hits >= 10:
                slider_blue += 1
        control_flick.setProperty("contentY", 0)
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
        opened = settled_window()
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
        scene = settled_window()
        top_left = chart.mapToScene(QPointF(0, 0))
        colours = {scene.pixelColor(int(top_left.x() + x), int(top_left.y() + y)).name()
                   for x in range(5, min(160, int(chart.width())), 7)
                   for y in range(5, int(chart.height()), 4)}
        # The mini is a SPARKLINE (2 grid lines, flat series, no
        # labels): its correct signature is the background, the grid
        # and at least ONE series colour — the old 12-colour floor
        # dated from the full chart's gradient era and flagged the
        # correctly-rendered mini as blank (the 2026-09-22 capture
        # failure: 4 colours, two of them the drawn series).
        if len(colours) < 3:
            raise RuntimeError("mini chart region looks blank (%d colours)" % len(colours))
        print("mini chart region colours:", len(colours))
        for _ in range(3):
            app.processEvents()

        # Capture the two 4.6.0 popovers through the same real dashboard,
        # theme, stable-frame and contrast checks as the existing gallery.
        host.setProperty("openPopOver", "plate")
        settled_window()
        grab("10-exclude-object-picker.png", popover_card("Exclude Object Picker"))
        host.setProperty("openPopOver", "plateprogress")
        settled_window()
        faces = [child for child in item.findChildren(QQuickItem)
                 if child.objectName() == "moonrakerPlateProgressFace"
                 and not child.property("compact") and child.isVisible()]
        if len(faces) != 1:
            raise RuntimeError("expected one visible follower popover canvas")
        face = faces[0]
        from capture_penguin import make_gcode
        index = qt.load("GCodeIndex").build_index_from_bytes(make_gcode().encode("ascii"))
        payload = qt.load("PlateProgress").layer_polylines(index, 1)
        if payload is None or payload["motions"] < 100:
            raise RuntimeError("the penguin G-code did not produce indexed toolpaths")
        layer = qt.load("PlateQt").PlateLayer(payload)
        # Seed production model properties, not a painted screenshot overlay.
        # The material palette is a capture-only equivalent of Cura's three
        # configured material colours; real per-motion tool IDs choose it.
        model._colour_scheme = SimpleNamespace(snapshot={
            "mode": 0, "materials": ["#20252b", "#f4f4f4", "#e9ad20"]})
        model._values.update({"plateLayerCount": 3, "plateLayerMotionCount": payload["motions"],
                              "plateProgressAnchor": 1, "plateProgressAvailable": True,
                              "plateProgressReason": "", "plateTrackingAvailable": True,
                              "plateLayers": {"prev": None, "current": layer, "next": None},
                              "plateSplit": payload["motions"], "followerLineScale": 2.0})
        model.followerViewChanged.emit()
        model.plateProgressChanged.emit()
        # Offscreen Qt has no native GPU surface. Feed the real fallback's
        # prepared raster, painted from exactly the same indexed layer.
        plate_qt = qt.load("PlateQt")
        plot_value = face.property("plot").toVariant()
        bed = plot_value["bed"]
        plot = {"offsetX": bed["offsetX"], "offsetY": bed["offsetY"],
                "sx": plot_value["sx"], "sy": plot_value["sy"],
                "bedXMin": bed["bedXMin"], "bedYMax": bed["bedYMax"]}
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineWidthPx": 2.0, "lineScale": 2.0,
                "colourScheme": model.followerColourScheme}
        coloured, _, _ = plate_qt.render_layer_raster(payload, plot, view)
        layer.set_expected_key("capture-penguin")
        layer.set_raster(coloured, "capture-penguin", plate_qt.png_file(
            coloured, os.path.join(output_dir, ".layers"), "penguin"))
        # A fixed observation of the indexed layer prevents the synthetic
        # transport's unrelated dashboard job from replacing this illustration.
        face.setProperty("progress", {"available": True, "reason": "",
                                     "layers": {"prev": None, "current": layer, "next": None},
                                     "split": payload["motions"], "anchor": 1,
                                     "method": "motion index", "sceneEpoch": "capture-penguin"})
        face.setProperty("scrubVector", payload)
        face.setProperty("dot", {"valid": False})
        face.setProperty("motionSmoothing", False)
        scene = settled_window()
        corner = face.mapToScene(QPointF(0, 0))
        yellow = sum(1 for y in range(int(face.height()))
                     for x in range(0, int(face.width()), 2)
                     if scene.pixelColor(int(corner.x()) + x, int(corner.y()) + y).name() == "#e9ad20")
        if yellow < 100:
            raise RuntimeError("the indexed penguin toolpaths did not paint")
        grab("11-print-follower.png", popover_card("Print Follower"))
        host.setProperty("openPopOver", "")

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
