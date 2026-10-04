"""Executable contracts for the completed component boundaries."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from concurrent.futures import Future
from http.server import ThreadingHTTPServer
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from tests.qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, ScriptedSocket, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ComposedComponentTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        self.socket = ScriptedSocket()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=self.socket)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.parts = self.follower._runtime
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        # The harness tests HTTP semantics; the product default stays in
        # PrinterConfig, never in the harness.
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def status(self, *, layer=10, filename="part.gcode", duration=30, position=40, state="printing"):
        return {"print_stats": {"filename": filename, "state": state, "print_duration": duration,
                "info": {"current_layer": layer, "total_layer": 50}},
            "virtual_sdcard": {"file_size": 100, "file_position": position},
            "gcode_move": {"gcode_position": [1, 1, 2, 10], "speed_factor": 1, "extrude_factor": 1}}

    def deliver(self, status):
        import time
        client = self.follower.client
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

    def connect(self):
        # The dispatch gate's connection clause (4.2.0): the real
        # confirm dialog can only be reached with an observed
        # connection — the fixture establishes it the same way.
        client = self.follower.client
        client._connected = True
        client.connectionChanged.emit(True, "Moonraker connected over http polling")

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def plant_download(self, files, layers):
        """A downloaded G-code file under the service's own root, torn
        down once the service has let go of it.

        The last assert is not the last READ: a worker lane can still be
        walking the file (the reader holds the lease its lane took), and
        Windows refuses a delete while any handle is open (WinError 32).
        The teardown waits on the service's own release discipline — one
        lane at a time, the reader's lease — so a real leak still fails
        here rather than passing silently."""
        target = os.path.join(files._root, "job-1", "part.gcode")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(layers)
        service = self.parts.index

        def drop_the_download():
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and (service._busy or files._leases):
                self.qt.events(5)
            try:
                os.remove(target)
            except FileNotFoundError:
                pass

        self.addCleanup(drop_the_download)
        return target


    @staticmethod
    def _generated_gcode(layers, motions):
        lines = ["; generated seek benchmark", "G90"]
        for layer in range(layers):
            lines.append(";LAYER:%d" % layer)
            for m in range(motions):
                lines.append("G1 X%.2f Y%.2f E0.02"
                             % ((m % 200) * 0.5, (m // 200) * 0.4))
        return "\n".join(lines).encode("utf-8")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeRenderSchedulerTests(unittest.TestCase):
    """The round-4 render architecture: per-surface contexts, bounded demand scheduling,
    ghost state, idempotent setters and scheduler hygiene."""

    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        self.socket = ScriptedSocket()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=self.socket)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    @staticmethod
    def _payload(motions=2):
        points = [[float(i), 0.0, float(i)] for i in range(motions + 1)]
        return {"classes": {"SKIN": [points]},
                "travels": [], "travelStarts": [], "travelEnds": [], "motions": motions}

    @staticmethod
    def _dense(motions=200000):
        points = [[i * 0.5 % 240.0, 2.0, float(i)] for i in range(motions + 1)]
        return {"classes": {"FILL": [points]},
                "travels": [], "travelStarts": [], "travelEnds": [], "motions": motions}

    def _feed(self, model, name, width=400, height=300, compact=False, pan_x=0.0, pan_y=0.0):
        model.setFollowerPlot(name, 0.0, 0.0, 1.0, 1.0, 0.0, float(height))
        model.setFollowerView(name, 1.0, 0.7, width, height, compact, pan_x, pan_y)
        self.qt.events(5)  # the staged flush

    def _window(self, model, name, anchor, payload=None):
        surface = model.plate_renderer._surfaces[name]
        payload = payload or self._payload()
        model.plate_renderer.window_for(surface, {"prev": payload if anchor > 0 else None,
                                   "current": payload, "next": payload}, anchor)

    def _hot(self, surface, layer):
        wrapped = surface.layers.get(layer)
        return wrapped is not None and wrapped.rasterValid

    def _pump_rasters(self, model, name, timeout=400, deadline_s=30.0):
        """Drive the event loop until the surface's desired window is
        fully rasterised (the workers deliver through the queued
        bridge signals).

        The claim is that the rasters SETTLE, not that they settle
        within a number of loop turns — and a loaded runner spends
        longer per turn than an idle one while the worker delivering
        through the queued bridge is asynchronous either way. The turn
        budget stays as the floor (an idle machine returns in a handful
        of turns); the wall-clock deadline is what decides, so a slow
        machine waits rather than fails and a genuine hang still fails
        rather than hangs (the Windows CI flake, 2026-09-24)."""
        surface = model.plate_renderer._surfaces[name]

        def settled():
            if surface.job is not None:
                return False
            desired = surface.desired
            if desired is None:
                return True
            if not self._hot(surface, desired["current"]):
                return False
            return all(self._hot(surface, layer) for layer in desired["ghosts"].values())

        started = time.monotonic()
        for _ in range(timeout):
            self.qt.events(5)
            if settled():
                return
        while time.monotonic() - started < deadline_s:
            self.qt.events(5)
            if settled():
                return
        self.fail("the rasters did not settle")


class AttachCadenceTests(NativeRenderSchedulerTests):
    """The attached live-follow cadences: a continuously advancing
    split must NOT re-bake the 4x navigation raster or the native
    prefix per poll (the dire-follow report). The nav bake runs at
    most once per start-time window and commits as a slightly older,
    compatible raster; the prefix checkpoints on its own cadence
    while the QML tail accumulates. A fake clock simulates the 30 s
    print deterministically — the workers stay real."""

    POLL_S = 0.75
    POLLS = 40  # 30 s of print at the monitor's poll cadence

    def _attached(self, model, name="popover", width=400, height=300):
        """Attach, open the popover, feed the surface, and install the
        deterministic clock plus the render-start counters."""
        self._feed(model, name, width=width, height=height)
        model.setFollowerAttached(True)
        model.setFollowerPopoverOpen(True)
        surface = model.plate_renderer._surfaces[name]
        module = self.qt.load("MoonrakerMonitorModel")
        controller_module = self.qt.load("PlateRenderController")
        clock = _FakeClock(controller_module)
        starts = {"nav": [], "prefix": []}
        # The counters stamp the SCHEDULER's decision, on this thread,
        # inside the product's own call — which is the instant the
        # product stamps into its own window. Stamping the worker's
        # entry instead put the record on a pool thread, asynchronously,
        # and the drain that ends the test freezes the fake clock: two
        # renders entered 88 real ms apart then carried one identical
        # reading, which reads as two bakes starting together when the
        # product's own starts were 12.75 fake seconds apart.
        controller_cls = controller_module.PlateRenderController
        real_schedule_nav = controller_cls.schedule_navigation
        real_schedule_surface = controller_cls._schedule_surface

        def schedule_nav(pane, plate_surface, **kwargs):
            # The wake fires with coalesce_zoom=False (the settle's own
            # expiry must bake): the seam forwards it, so the patched
            # scheduler behaves exactly as the product's does.
            before = plate_surface.nav.get("job")
            real_schedule_nav(pane, plate_surface, **kwargs)
            after = plate_surface.nav.get("job")
            if after is not None and after is not before:
                starts["nav"].append(
                    (clock.t, (plate_surface.desired or {}).get("split")))

        def schedule_surface(pane, plate_surface):
            before = plate_surface.job
            real_schedule_surface(pane, plate_surface)
            after = plate_surface.job
            if after is not None and after is not before \
                    and after.get("kind") == "prefix":
                starts["prefix"].append((clock.t, after.get("split")))

        self.patches = [patch.object(module, "time", clock),
                        patch.object(controller_module, "time", clock),
                        patch.object(controller_cls, "schedule_navigation",
                                     schedule_nav),
                        patch.object(controller_cls, "_schedule_surface",
                                     schedule_surface)]
        for entry in self.patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in self.patches])
        armed = []
        # The wake seam: the tests fire the wakes themselves at the
        # fake clock's deadlines — a real singleShot would wait out
        # the (simulated) window.
        model.plate_renderer._nav_arm_wake = lambda s: armed.append((s, s.nav.get("wake_at"))) or None
        # Drop the instance attribute on cleanup: re-attaching the
        # class function here stored it UNBOUND on the instance, so
        # every later call passed the surface as self and the arm
        # died with a missing-surface TypeError.
        self.addCleanup(lambda:
            model.plate_renderer.__dict__.pop("_nav_arm_wake", None))
        return model, surface, clock, armed, starts

    @staticmethod
    def _fire_due_wakes(model, armed, clock):
        due = [entry for entry in armed
               if entry[1] is not None and clock.t >= entry[1]]
        armed[:] = [entry for entry in armed if entry not in due]
        for surface, deadline in due:
            model.plate_renderer._nav_wake(surface, deadline)

    @staticmethod
    def _await_prefix_job(surface, layer_number, final_split, qt, timeout=15.0):
        """Await the prefix job a demand started, by its TOKEN.

        `surface.tokens` is keyed by the LAYER NUMBER — the same key as
        `surface.layers`, whose VALUE is the PlateLayer wrapper
        (`wrapped = surface.layers.get(layer)` in _schedule_surface). The
        token is bumped when a job is submitted and popped when it
        commits. It is per LAYER, not per job: a layer's prefix and full
        demands share the key, so the disappearance of a token this
        waiter saw pending means NO job is outstanding for that layer —
        which is the completion this test needs, but is not "the prefix
        job finished" and is not claimed to be. Its disappearance is the
        signal;

            layer = surface.layers[5]      # a PlateLayer
            surface.tokens.get(layer)      # ALWAYS None — wrong key

        answers None immediately, which skips the wait and passes the
        completion assertion unconditionally.

        Absence alone is not completion, so this watches the TRANSITION:
        a token seen pending for this layer must be gone. A demand that
        needs no render — the split is already current — never assigns
        one, and is accepted as such rather than waited on. A DISCARDED
        job leaves its token behind (_raster_committed returns before the
        pop on that path), so this times out and the caller fails loudly
        instead of reading a discard as a commit.
        """
        deadline = time.monotonic() + timeout
        # SAMPLED BEFORE ANY PUMP. A job already submitted can complete
        # inside the first qt.events(), and its token is then gone before
        # the loop ever sees it — the waiter would fall through to the
        # already-current path and report a completion it never witnessed
        # (entry token=1, exit token=None, the committed split older than
        # the demanded one, so that fallback cannot rescue it).
        seen_pending = surface.tokens.get(layer_number) is not None
        while time.monotonic() < deadline:
            qt.events(6)
            if surface.tokens.get(layer_number) is not None:
                seen_pending = True
                continue
            if seen_pending:
                return True                      # the watched job committed
            if surface.layers[layer_number].prefixSplit == final_split:
                return True                      # already current: no render due
        return False

    @staticmethod
    def _drain_job(model, surface, qt, timeout=400, deadline_s=30.0):
        started = time.monotonic()
        for _ in range(timeout):
            qt.events(6)
            if surface.job is None and surface.nav["job"] is None:
                return
        while time.monotonic() - started < deadline_s:
            qt.events(6)
            if surface.job is None and surface.nav["job"] is None:
                return
        raise AssertionError(
            "the render queue never drained (layer job=%s, navigation job=%s)" %
            (surface.job is not None, surface.nav["job"] is not None))

    def _poll(self, model, surface, payload, anchor, split, clock, armed, qt):
        model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                   "next": None}, anchor, "motion index", split)
        clock.t += self.POLL_S
        self._fire_due_wakes(model, armed, clock)
        qt.events(6)

    @staticmethod
    def _spacing(starts, minimum):
        for (a, _), (b, _) in zip(starts, starts[1:], strict=False):
            if b - a < minimum:
                raise AssertionError(
                    "two starts %ss apart, the cadence is %ss" %
                    (round(b - a, 2), minimum))


        # No final-split assertion HERE, deliberately: this test parks
        # EVERY prefix render, so they unblock together and commit in
        # whichever order the pool runs them — the last commit is not
        # necessarily the frozen split. The final-split and cadence
        # assertions belong to the tests that drive the real cadence,
        # and they are untouched; this one proves the waiter's contract.


class _FakeClock:
    """The deterministic test clock: only `monotonic` is simulated
    (the model's cadences read it); every other attribute delegates
    to the real module."""

    def __init__(self, module):
        self.t = 1000.0
        self._real = module.time

    def monotonic(self):
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


class RendererOnlySeekBenchmarks(NativeRenderSchedulerTests):
    """A RENDERER/SCHEDULER microbenchmark, not an end-to-end latency
    proof: the payloads arrive pre-decoded via _qt_window(), so the
    request/classify/read/decode/prepare/coordinator/publish stages
    are NOT in these numbers. The true slider-to-available and
    slider-to-picture measurements live in the end-to-end
    benchmarks."""

    def _time_seek(self, model, name, anchor, payload, split=None):
        surface = model.plate_renderer._surfaces[name]
        start = time.monotonic()
        model.plate_renderer.window_for(surface, {"prev": payload if anchor > 0 else None,
                                   "current": payload, "next": payload},
                         anchor, "motion index", split)
        self._pump_rasters(model, name)
        return (time.monotonic() - start) * 1000.0


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['AttachCadenceTests', 'ComposedComponentTests', 'FrozenInstanceError', 'Future', 'NativeRenderSchedulerTests', 'PipeSafeHandler', 'QT_AVAILABLE', 'RendererOnlySeekBenchmarks', 'ScriptedSocket', 'ScriptedTransport', 'SimpleNamespace', 'ThreadingHTTPServer', '_FakeClock', 'annotations', 'json', 'os', 'patch', 'pathlib', 'runtime', 'sys', 'tempfile', 'threading', 'time', 'unittest']
