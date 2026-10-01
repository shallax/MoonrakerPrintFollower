"""The render controller's own contracts: the presentation restamp, the
decoded cache's consumer identity, the pixel-width stroke, the
checkpoint worker's terminal paths and the teardown release.

These are the seams the extraction made explicit — the controller owns
them now, so they are driven through the controller rather than through
the model's delegates, which the model's own suites already cover.

Leftover, with its reason:
  - 922 (schedule_navigation's hard-key guard): _nav_key_hard returns
    None only for a None key, and the demand has already returned on
    that key lines earlier, so the guard cannot fire.
"""

from __future__ import annotations

import pathlib
import time
from unittest.mock import patch

from tests import composed_runtime_support as harness


def local_url(path):
    """The renderer's own spelling of a raster file.

    png_file returns the QUrl.fromLocalFile form, and the sweeps resolve
    a URL back to a path before they compare or unlink, so a hand-built
    'file://' prefix only round trips on POSIX. On Windows it resolves
    to nothing, the unlink raises into the caller's OSError guard and a
    reference test then passes for the wrong reason.
    """
    from PyQt6.QtCore import QUrl
    return QUrl.fromLocalFile(str(path)).toString()


class PlateRenderControllerTests(harness.NativeRenderSchedulerTests):
    def controller(self, model):
        return model.plate_renderer

    def traced_monitor(self):
        """The seek trace is a config switch read at the call, so the
        monitor has to be mounted under the config that asks for it."""
        self.follower.apply_printer_config(self.config_type(
            url="http://printer-a", path_follow=False, feed_mode="http", seek_trace=True))
        return self.monitor()

    def test_a_scene_content_change_restamps_every_live_surfaces_view(self):
        # The legend toggles and the colour scheme are the scene's
        # CONTENT: a change re-stamps BOTH surfaces' effective views and
        # re-flushes each, so a raster baked under the old content can
        # never read as current on either face.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        self._feed(model, "mini", width=90, height=90, compact=True)
        controller = self.controller(model)
        surfaces = {name: controller._surfaces[name] for name in ("popover", "mini")}
        before = {name: surface.generation for name, surface in surfaces.items()}

        model.setFollowerTrueThickness(True)
        self.qt.events(5)
        for name, surface in surfaces.items():
            self.assertTrue(surface.view.get("trueThickness"), "the %s view never took the new content" % name)
            self.assertGreater(surface.generation, before[name], "the %s surface never re-flushed" % name)

    def test_the_warm_raster_carries_the_pixel_width_stroke(self):
        # The ten-argument view call's pixel width rides the navigation
        # key AND the warm composite's own view: a face that asks for a
        # device-pixel stroke must not be handed a bake without it.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        model.setFollowerAttached(True)
        model.setFollowerPopoverOpen(True)
        surface = self.controller(model)._surfaces["popover"]
        module = self.qt.load("PlateRenderController")
        captured = []
        real = module.render_navigation_layer

        def navigation(window, plot, view, split=None, cancel=None, previous=None, previous_split=0):
            captured.append(dict(view))
            return real(window, plot, view, split, cancel=cancel, previous=previous, previous_split=previous_split)

        with patch.object(module, "render_navigation_layer", navigation):
            model.setFollowerView("popover", 1.0, 0.7, 400, 300, False, 0.0, 0.0, 1.0, 3.0)
            self.qt.events(5)
            controller = self.controller(model)
            controller.window_for(
                surface, {"prev": None, "current": self._payload(), "next": None}, 5, "motion index", None
            )
            controller.schedule_navigation(surface)
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline and not captured:
                self.qt.events(6)
        self.assertTrue(captured, "the warm raster never rendered")
        self.assertEqual(captured[0].get("lineWidthPx"), 3.0, "the warm composite lost the pixel-width stroke")

    def test_a_backend_switch_moves_the_decoded_cache_consumer(self):
        # The decoded cache's tier follows a consumer COUNT keyed on the
        # renderer's identity and the surface name. Nothing else may key
        # on it, so the key is pinned here rather than inferred.
        model = self.monitor()
        controller = self.controller(model)
        service = controller._pins
        self.assertIsNotNone(service, "the harness model carries no index service")
        calls = []

        def record(key, enabled):
            calls.append((key, enabled))

        with patch.object(service, "set_gpu_rendering", record):
            model.setFollowerGpuRendering("popover", True)
            model.setFollowerGpuRendering("popover", False)
        self.assertEqual(calls, [((id(controller), "popover"), True), ((id(controller), "popover"), False)])

    def test_the_teardown_releases_every_surfaces_decoded_tier(self):
        # The index service outlives the monitor, so the renderer's own
        # release has to hand back both the wrappers' pins and the
        # GPU-mode tiers it claimed — a stale tier would keep the
        # decoded cache's consumer count above zero forever.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        self._feed(model, "mini", width=90, height=90, compact=True)
        self._window(model, "popover", 5)
        self._window(model, "mini", 5)
        controller = self.controller(model)
        surfaces = list(controller._surfaces.values())
        service = controller._pins
        tiers = []
        unpinned = []
        with (
            patch.object(service, "set_gpu_rendering", lambda key, enabled: tiers.append((key, enabled))),
            patch.object(service, "unpin_decoded", lambda *args, **kwargs: unpinned.append(args)),
        ):
            controller.release_all_pins()
        self.assertEqual(sorted(key[1] for key, _ in tiers), ["mini", "popover"])
        self.assertTrue(all(not enabled for _, enabled in tiers), "a tier stayed claimed after the teardown")
        self.assertEqual(
            len(unpinned),
            sum(len(surface.layers) for surface in surfaces),
            "a retained wrapper's pin outlived the renderer",
        )

    def test_a_repeated_gesture_hold_is_the_same_hold(self):
        # The face reports its held raster on every gesture update; the
        # repeat must not tear down the hold it is reporting.
        model = self.monitor()
        controller = self.controller(model)
        url = local_url(pathlib.Path(controller._raster_cache_dir) / "held-nav.png")
        controller.hold_gesture_raster(url)
        self.assertEqual(controller._gesture_raster, url)
        controller.hold_gesture_raster(url)
        self.assertEqual(controller._gesture_raster, url)
        controller.hold_gesture_raster("")
        self.assertEqual(controller._gesture_raster, "")

    def test_the_seek_trace_records_the_slider_debounce(self):
        # The seek's own instrument: the raw tick arms the debounce and
        # the committed layer carries it into T1. The switch is read at
        # the call, so the trace exists only under a tracing config.
        model = self.traced_monitor()
        self._feed(model, "popover", width=400, height=300)
        model.seekAnchorTicked()
        model.setFollowerLayerAnchor(5)
        trace = model.plate_renderer._seek_trace
        self.assertTrue(trace, "the seek trace recorded nothing")
        self.assertEqual(trace[0]["stage"], "T1 seek entry")
        self.assertEqual(trace[0].get("layer"), 5)
        self.assertIn("debounce_ms", trace[0], "the slider's debounce never reached the trace")

    def test_a_navigation_wake_for_another_surface_leaves_its_window_alone(self):
        # The wake is the popover's own window expiry. A wake routed at
        # the mini must not consume or re-arm a window the popover owns
        # — the mini bakes on its own cadence.
        model = self.monitor()
        self._feed(model, "mini", width=90, height=90, compact=True)
        controller = self.controller(model)
        mini = controller.surface("mini")
        mini.nav["wake_at"] = 12345.0
        controller._nav_wake(mini, 12345.0)
        self.assertEqual(mini.nav.get("wake_at"), 12345.0, "a non-popover wake consumed its own window")

    def test_a_discarded_job_never_unlinks_a_raster_a_live_face_references(self):
        # The discard sweep is reference-aware: the gesture's held raster
        # is what a face is reading right now, so it survives the sweep
        # and only the truly dead file goes.
        model = self.monitor()
        controller = self.controller(model)
        directory = pathlib.Path(controller._raster_cache_dir)
        held = directory / "discard-held.png"
        dead = directory / "discard-dead.png"
        held.write_bytes(b"held")
        dead.write_bytes(b"dead")
        held_url = local_url(held)
        dead_url = local_url(dead)
        controller.hold_gesture_raster(held_url)
        controller._unlink_asset_files(("full", None, held_url, None, dead_url, None, None))
        self.assertTrue(held.exists(), "the discard sweep unlinked the gesture's held raster")
        self.assertFalse(dead.exists(), "a discarded job's file outlived its job")


class CheckpointWorkerTests(harness.NativeRenderSchedulerTests):
    """The rewind checkpoint job: every path ends in exactly one
    terminal emit, and a cancelled walk leaves no raster behind."""

    def checkpoint_surface(self, model, motions=40, layer=5):
        controller = model.plate_renderer
        surface = controller._surfaces["popover"]
        surface.plot = {"offsetX": 0.0, "offsetY": 0.0, "sx": 1.0, "sy": 1.0, "bedXMin": 0.0, "bedYMax": 300.0}
        surface.view = {"width": 400, "height": 300, "scale": 1.0, "lineScale": 0.7, "dpr": 1.0, "compact": False}
        wrapped = self.qt.load("PlateQt").PlateLayer({"motions": motions, "classes": {}})
        wrapped.set_expected_key(surface.render_key())
        surface.layers[layer] = wrapped
        surface.desired = {"current": layer, "ghosts": {}, "epoch": 0, "split": None}
        return surface, wrapped

    def await_checkpoints(self, wrapped, timeout=15.0):
        """The commit clears the pending list the scheduler filled; the
        ticket stays set, because the snapshot set is built once per
        wrapper and replayed by the rewind gesture afterwards."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.qt.events(6)
            if wrapped._rewind_pending == []:
                return True
        return False

    def rewinds(self, model):
        directory = pathlib.Path(model.plate_renderer._raster_cache_dir)
        return sorted(path.name for path in directory.glob("rewind-*.png"))

    def test_a_cancelled_checkpoint_walk_leaves_no_raster_behind(self):
        # The supersede lands while the walk is writing its second
        # snapshot: the job stops where it was told and unlinks what it
        # had already written — a cancelled job's files are dead on
        # arrival and must never wait for the generic pruning.
        model = self.monitor()
        surface, wrapped = self.checkpoint_surface(model)
        module = self.qt.load("PlateRenderController")
        real_png = module.png_file
        writes = {"n": 0}

        def png(image, directory, stem):
            url = real_png(image, directory, stem)
            writes["n"] += 1
            if writes["n"] == 1 and wrapped._rewind_cancel is not None:
                wrapped._rewind_cancel.set()
            return url

        with patch.object(module, "png_file", png):
            model.plate_renderer._schedule_rewind_checkpoints(surface)
            self.assertTrue(wrapped._rewind_pending, "the checkpoint job never started")
            self.assertTrue(self.await_checkpoints(wrapped), "the cancelled checkpoint job never ended")
        self.assertEqual(writes["n"], 1, "the cancelled walk kept rendering")
        self.assertEqual(self.rewinds(model), [], "the cancelled walk left its raster behind")
        self.assertEqual(wrapped._rewind_files, {})

    def test_a_checkpoint_walk_that_throws_still_ends_its_job(self):
        # A worker that throws can never wedge the surface's checkpoint
        # slot: the exception is logged and the job still emits its
        # terminal payload.
        model = self.monitor()
        surface, wrapped = self.checkpoint_surface(model)
        module = self.qt.load("PlateRenderController")

        def explode(*args, **kwargs):
            raise RuntimeError("the checkpoint walk blew up")

        with patch.object(module, "render_layer_prefix", explode):
            model.plate_renderer._schedule_rewind_checkpoints(surface)
            self.assertTrue(wrapped._rewind_pending, "the checkpoint job never started")
            self.assertTrue(self.await_checkpoints(wrapped), "the failed checkpoint job never ended")
        self.assertEqual(self.rewinds(model), [])
        self.assertIsNone(wrapped._rewind_cancel)

    def test_a_cancelled_walk_whose_asset_vanished_still_ends_its_job(self):
        # The cancel cleanup unlinks what it already wrote, and a file
        # that is already gone must not escape the finally: an
        # exception there would swallow the terminal emit and strand
        # the surface's checkpoint slot for the session.
        model = self.monitor()
        surface, wrapped = self.checkpoint_surface(model)
        module = self.qt.load("PlateRenderController")
        real = module.render_layer_prefix
        calls = {"n": 0}

        def render(payload, plot, view, split, cancel=None, previous=None, previous_split=0):
            calls["n"] += 1
            image = real(payload, plot, view, split, cancel=cancel, previous=previous, previous_split=previous_split)
            if calls["n"] == 2 and wrapped._rewind_cancel is not None:
                wrapped._rewind_cancel.set()
            return image

        def vanished(image, directory, stem):
            # The URL the job believes it wrote: a real spelling of the
            # path, and the file never landed at it.
            return local_url(pathlib.Path(directory) / (stem + ".png"))

        with patch.object(module, "render_layer_prefix", render), patch.object(module, "png_file", vanished):
            model.plate_renderer._schedule_rewind_checkpoints(surface)
            self.assertTrue(wrapped._rewind_pending, "the checkpoint job never started")
            self.assertTrue(self.await_checkpoints(wrapped), "the vanished asset escaped the cleanup")
        self.assertEqual(calls["n"], 2, "the cancelled walk kept rendering")
        self.assertEqual(wrapped._rewind_files, {}, "a cancelled walk committed its boundaries")
        self.assertEqual(self.rewinds(model), [])

    def test_a_checkpoint_walk_that_cannot_write_its_raster_stops_there(self):
        # A boundary whose raster never landed is not a checkpoint: the
        # walk stops at the failed write and commits nothing, rather
        # than handing the rewind gesture a file that does not exist.
        model = self.monitor()
        surface, wrapped = self.checkpoint_surface(model)
        module = self.qt.load("PlateRenderController")
        real = module.render_layer_prefix
        calls = {"n": 0}

        def render(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        with patch.object(module, "render_layer_prefix", render), patch.object(module, "png_file", lambda *args: ""):
            model.plate_renderer._schedule_rewind_checkpoints(surface)
            self.assertTrue(wrapped._rewind_pending, "the checkpoint job never started")
            self.assertTrue(self.await_checkpoints(wrapped), "the unwritable checkpoint job never ended")
        self.assertEqual(calls["n"], 1, "the walk kept rendering past the failed write")
        self.assertEqual(wrapped._rewind_files, {}, "the walk committed a boundary it could not write")
        self.assertEqual(self.rewinds(model), [])

    def test_a_rewind_checkpoint_file_seeds_the_refreshed_prefix(self):
        # The refresh reaches back into the checkpoint FILES when the
        # in-memory seed is older: the fresher file boundary becomes the
        # incremental base, so the refreshed prefix strokes the tail
        # beyond the checkpoint instead of the whole printed prefix.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        controller = model.plate_renderer
        surface = controller._surfaces["popover"]
        payload = self._payload(400)
        controller.window_for(surface, {"prev": None, "current": payload, "next": None}, 5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        self.assertEqual(wrapped.prefixSplit, 50, "the first prefix never landed")
        controller._schedule_rewind_checkpoints(surface)
        self.assertTrue(wrapped._rewind_pending, "the checkpoint walk never started")
        self.assertTrue(self.await_checkpoints(wrapped), "the checkpoint walk never ended")
        self.assertTrue(wrapped._rewind_files, "the walk committed no checkpoint files")

        module = self.qt.load("PlateRenderController")
        real = module.render_layer_prefix
        seen = []

        def capture(payload, plot, view, split, cancel=None, previous=None, previous_split=0):
            seen.append((split, previous is not None, previous_split))
            return real(payload, plot, view, split, cancel=cancel, previous=previous, previous_split=previous_split)

        # The follower is attached, whose prefix advance is a time
        # budget rather than a motion threshold: elapse the budget to
        # reach the refresh this pins.
        wrapped._prefix_checkpoint_at = 0.0
        with patch.object(module, "render_layer_prefix", capture):
            controller.window_for(surface, {"prev": None, "current": payload, "next": None}, 5, "motion index", 160)
            self._pump_rasters(model, "popover")
        self.assertEqual(wrapped.prefixSplit, 160, "the quarter-layer advance never refreshed")
        self.assertIn((160, True, 160), seen,
                      "the refreshed prefix walked from motion zero instead of the checkpoint file")
