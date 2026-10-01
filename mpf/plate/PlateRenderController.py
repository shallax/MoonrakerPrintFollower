"""The follower's native renderer: one owner for the surfaces' worker
and resource lifecycle.

The model keeps the Qt facade and the publication transaction; this
controller owns what sits behind it — the per-surface render contexts,
their bounded demand schedulers, the raster workers and the tickets
that identify them, the raster cache directory and the assets a live
face still displays, the decoded-layer pins the retained wrappers hold,
and the navigation raster's double buffer.

Every capability it needs from the model is injected: a collaborator
for decoded pinning and the memory tiers, and narrow callables for the
presentation state (attach, the popover's open gate, the scene inputs)
and for the publication the model already performs. It never reaches
back into the model.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from typing import NamedTuple

from PyQt6.QtCore import QObject, QThreadPool, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QImage

from .PlateQt import (
    PlateLayer, RasterBridge, _RasterJob, _CheckpointBudget, _bridge_emit, png_file,
    qml_geometry, render_layer_prefix, render_layer_raster,
    render_navigation_layer,
)
from .PlateSceneIdentity import (
    NavigationSceneKey, navigation_compatible, navigation_hard_key, navigation_zoom,
)
from .RenderSurface import RenderSurface

# The warm-raster follow window: while attached, the poll advances
# the split constantly — the 4x navigation bake runs at most once per
# window, measured from the JOB'S START (a commit-time stamp starves
# the throttle whenever the split outruns the render — the
# render/discard/retry loop). A hard scene change (layer, print,
# zoom, dimensions, toggles) bypasses the window, and a drag's PRESS
# takes a one-off snapshot outside the cadence (the press-time bake
# leads the first movement). The raster serves the HELD gesture; the
# release returns the picture to the exact scene, so the window only
# needs to keep the gesture entries fresh.
_NAV_FOLLOW_BAKE_S = 3.0
# The zoom's own settle: the zoom rides the navigation key (the
# raster's grid and stroke floor are baked at the level they present
# at), so a wheel step is a genuine demand change. A BURST of them is
# one intent, and baking the whole 4x composite per step spent the
# worker and the GUI thread on pictures the next step superseded — so
# a demand whose zoom slot moved rides this short trailing settle
# instead, and the demand the wheel stops on bakes once. Far shorter
# than the follow window: the gesture needs a raster at the level it
# is now presenting at, not three seconds later.
_NAV_ZOOM_SETTLE_S = 0.12
# The navigation key's shape: the builder below is the ONE length and
# the derived keys read slots by position, so the count is named here
# rather than repeated as a bare number.
_NAV_KEY_FIELDS = len(NavigationSceneKey._fields)
# The attached prefix checkpoint cadence: the native prefix advances
# at most once per window, snapshotting the latest split — the QML
# tail accumulates [P, split) cheaply between checkpoints.
_PREFIX_CHECKPOINT_S = 5.0


class SceneInputs(NamedTuple):
    """The presentation settings a scene key or bake reads: the legend
    toggles and the bed's machine bounds. A snapshot taken at the call,
    so the renderer never reaches back into the model's properties."""

    show_previous: bool
    show_next: bool
    show_base: bool
    show_travels: bool
    bed_width: float
    bed_depth: float
    show_axis_arrows: bool = True


class PlateRenderController(QObject):
    """The renderer's policy and resource lifecycle for both surfaces."""

    def __init__(self, *, pins=None, trace_requested=None, attached=None,
                 popover_open=None, scene_inputs=None, notify=None, parent=None):
        super().__init__(parent)
        # The decoded cache's owner: the retained wrappers pin their
        # payloads there, so the decoded budget counts what the wrappers
        # keep alive and the memory accounting reads the tiers back.
        # Optional — a model mounted without an index service renders
        # nothing and pins nothing.
        self._pins = pins
        self._trace_requested = trace_requested or (lambda: False)
        self._attached = attached or (lambda: True)
        self._popover_open = popover_open or (lambda: False)
        self._scene = scene_inputs or (lambda: SceneInputs(True, True, True, False, 0.0, 0.0))
        self._notify = notify or (lambda: None)
        # The surfaces' records: the popover and the mini hold their own
        # view, plot, generation, layer wrappers and scheduler state, so
        # neither can overwrite the other's context or invalidate its
        # rasters.
        self._surfaces = {"popover": RenderSurface("popover"),
                          "mini": RenderSurface("mini")}
        # The PRINT epoch: a monotonic counter bumped on every job
        # switch. It rides every ticket, render key and asset file
        # name, so a stale worker from the previous print can never
        # structurally match the new print's request — even when the
        # layer, token and generation all coincide.
        self._job_epoch = 0
        # The scheduler's accounting: the worker reports its start AND
        # its completion through the bridge, so a job superseded BEFORE
        # it ran is countable.
        self._bridge = RasterBridge(self)
        self._bridge.started.connect(self._raster_started)
        self._bridge.done.connect(self._raster_committed)
        # The raster cache directory (the transport ruling): the QML
        # Images consume file:// PNGs the workers write here — a data:
        # URL loads but never renders, and a QImage variant segfaults
        # the Canvas (both engine-proven). INSTANCE-OWNED: every model
        # gets its own directory, so two printers can never collide on
        # filenames or prune each other's assets; the owner's
        # destruction removes it.
        self._raster_cache_dir = tempfile.mkdtemp(prefix="mpf-raster-%d-" % os.getpid())
        # Presentation has its own lifetime: a wrapper can supersede an
        # asset while a face still loads or retains it. Each face
        # publishes an atomic set of file references under a unique
        # owner token.
        self._asset_owners = {}
        self._asset_serial = 0
        # The navigation raster URL an ACTIVE camera gesture has
        # latched. The face presents that exact file for the gesture's
        # whole life, so neither a supersede's unlink nor the cache
        # prune may remove it — both key off the face's CURRENT
        # references, which a mid-gesture bake moves on, taking the
        # picture off the screen. Cleared when the gesture ends, so
        # nothing accumulates.
        self._gesture_raster = ""
        # Presentation holds the entry image while a gesture is live;
        # the bakes resume when it settles.
        self._interacting = False
        # The seek trace: disabled by default; the environment variable
        # (a live debug run) or the config's own flag turns it on.
        self._seek_trace_enabled = os.environ.get("MOONRAKER_FOLLOWER_SEEK_TRACE") == "1"
        self._seek_trace = []
        self._seek_start = 0.0
        self._seek_tick_mono = None
        # The owner's death: the pins and checkpoints release before the
        # directory goes, the order the connections were made in.
        self.destroyed.connect(self.cleanup_raster_cache)
        self.destroyed.connect(self.release_all_pins)

    # ------------------------------------------------------------------
    # The model's facade: the render contexts, their staging and the
    # projections the publication reads.
    # ------------------------------------------------------------------

    def surface(self, name):
        """The named surface's record, or a served record mapped to
        itself (the QML publishes "popover"/"mini" strings; the tests
        may hand the object)."""
        if isinstance(name, RenderSurface):
            return name
        return self._surfaces.get(name)

    def stage_context(self, surface, slot, value):
        """Stage one half of the plot/view pair: an exact repeat is a
        no-op compared against the EFFECTIVE value — the staged one
        when a burst is pending, so A -> B -> A before the flush ends
        at A rather than committing the intermediate B."""
        staged = surface.stage[slot]
        effective = staged if staged is not None else (
            surface.view if slot == "view" else surface.plot)
        if value == effective:
            return
        surface.stage[slot] = value
        self.arm_context_flush(surface)

    def patch_view(self, **fields):
        """Re-stamp presentation fields on every surface's effective
        view and re-flush: the legend toggles and the colour scheme are
        the scene's CONTENT, so every live raster invalidates the same
        way a view change invalidates it."""
        for surface in self._surfaces.values():
            effective = surface.stage["view"] or surface.view
            if effective:
                surface.stage["view"] = dict(effective, **fields)
                self.arm_context_flush(surface)

    def navigation_values(self, surface):
        """The published navigation block for one surface: the ready
        URL (only while its key matches the current demand), the split
        it was painted to, the backing it was baked at and the scene
        epoch the face keys its own composition on."""
        stored = surface.nav.get("key")
        return {
            "plateNavigationData": self.navigation_data(surface),
            "plateNavigationSplit": (stored[3] if stored is not None
                                     and surface.nav["url"] else None),
            "plateNavigationBacking": surface.nav.get("backing", 4.0),
            "plateSceneEpoch": "%d:%d" % (surface.job_epoch, surface.anchor_epoch),
        }

    def retire(self, name):
        """The named surface's QML consumer has gone: its pending
        demand retires (the rasters stay hot) and the next publication
        rebuilds it from the payload."""
        surface = self._surfaces.get(name)
        if surface is not None:
            self.retire_surface(surface)

    def gpu_rendering(self, name):
        """Whether the named face mounted a GPU backend: a GPU face
        needs no raster jobs, so the publication serves no scrub vector
        and the scheduler refuses its demand."""
        surface = self._surfaces.get(name)
        return bool(surface.gpu_rendering) if surface is not None else False

    def current_layer(self, name):
        """The layer a surface is presenting, or None before its first
        demand: the detach's fallback when the published anchor lags."""
        surface = self._surfaces.get(name)
        desired = surface.desired if surface is not None else None
        return desired.get("current") if desired is not None else None

    def begin_print(self):
        """A new print retires every surface's render state. The epoch
        bumps so an in-flight worker from the previous print can never
        match a new print's ticket, whatever its layer, token and
        generation; each surface's wrappers, tokens, demand and
        navigation slot go with the geometry they were built from; and
        the wrappers' decoded pins release with them."""
        self._job_epoch += 1
        for surface in self._surfaces.values():
            surface.job_epoch = self._job_epoch
            self.clear_surface_checkpoints(surface)
            if surface.job is not None:
                surface.job["cancel"].set()
            self.unpin_surface(surface)
            surface.layers.clear()
            surface.tokens.clear()
            surface.job = None
            surface.desired = None
            surface.render_count.clear()
            self.retire_navigation(surface)

    def set_gpu_rendering(self, name, enabled):
        """A mounted face selects its backend; GPU faces need no raster
        jobs. Retire the outstanding CPU demand on a change and tell
        the decoded cache's owner, whose tier follows the consumer
        count. An unchanged backend reports False — the caller
        publishes only on a real switch."""
        surface = self.surface(name)
        if surface is None or surface.gpu_rendering == bool(enabled):
            return False
        self.retire_surface(surface)
        surface.gpu_rendering = bool(enabled)
        update_gpu = getattr(self._pins, "set_gpu_rendering", None)
        if callable(update_gpu):
            update_gpu((id(self), surface.name), bool(enabled))
        return True

    def set_interacting(self, interacting):
        """Defer only the 4x navigation bakes; never freeze live plate
        state. The flag changes no published value, so the caller
        publishes nothing. On settle schedule the latest warm demand
        once — the exact scene served every poll behind the latch."""
        flag = bool(interacting)
        if flag == self._interacting:
            return
        self._interacting = flag
        if not flag and self._popover_open():
            self.schedule_navigation(self._surfaces["popover"])

    def gesture_bake(self, name="popover"):
        """The face's press hook: the drag is about to latch the warm
        raster — bake the current split once, outside the cadence."""
        self.nav_gesture_bake(self._surfaces.get(name))

    def hold_gesture_raster(self, url):
        """The navigation raster the face's live gesture is holding.

        The gesture latches its entry raster and presents that exact
        file for the gesture's whole life, so it has to survive a
        supersede: a bake committing mid-gesture otherwise unlinked the
        very picture on screen. Empty when no gesture is live.
        """
        url = str(url or "")
        if url == self._gesture_raster:
            return
        self._gesture_raster = url

    def acquire_asset_owner(self):
        self._asset_serial += 1
        owner = self._asset_serial
        self._asset_owners[owner] = frozenset()
        return owner

    def set_asset_references(self, owner, urls):
        """Replace one face's references without walking the cache or
        publishing model state in a QML callback. Unknown/released
        tokens cannot resurrect an owner. Only this run's own assets
        count."""
        if owner not in self._asset_owners:
            return
        directory = self._raster_file_key(self._raster_cache_dir)
        files = set()
        for url in urls:
            parsed = QUrl(str(url))
            if not parsed.isLocalFile():
                continue
            path = self._raster_file_key(parsed.toLocalFile())
            if os.path.dirname(path) == directory:
                files.add(path)
        self._asset_owners[owner] = frozenset(files)

    def release_asset_owner(self, owner):
        self._asset_owners.pop(owner, None)

    def accounting(self):
        """The renderer's share of the memory story: the wrappers'
        pixel bytes, the raster directory's disk bytes and the largest
        backing scale any surface presents at. The service's RAM tiers
        belong to the index, not here. The lifecycle frees them on
        their own paths — wrappers unpin on eviction and print change,
        the directory prunes on commit and print change, and the
        owner's destruction rmtrees it."""
        wrappers = 0
        wrapper_images = 0
        for surface in self._surfaces.values():
            wrappers += len(surface.layers)
            for wrapped in surface.layers.values():
                wrapper_images += wrapped.memory_bytes()
        dir_files = 0
        dir_bytes = 0
        try:
            for name in os.listdir(self._raster_cache_dir):
                try:
                    dir_bytes += os.stat(os.path.join(self._raster_cache_dir, name)).st_size
                    dir_files += 1
                except OSError:
                    pass
        except OSError:
            pass
        backing = 1.0
        for surface in self._surfaces.values():
            backing = max(backing, float(surface.view.get("dpr") or 1.0))
        return {"wrapperCount": wrappers, "wrapperImageBytes": wrapper_images,
                "rasterDirFiles": dir_files, "rasterDirBytes": dir_bytes,
                "backingScale": backing}

    def trace_enabled(self):
        """The seek trace's switch: the environment variable (a live
        debug run) or the config's own seek_trace."""
        return bool(self._seek_trace_enabled or self._trace_requested())

    def seek_ticked(self):
        """The slider's raw tick, the debounce's start."""
        self._seek_tick_mono = time.monotonic()

    def trace_seek_entry(self, layer):
        """The debounced slider commit: the trace records it with the
        debounce measured from the raw tick. A stray programmatic seek
        carries no tick and reports none."""
        extra = {"layer": layer}
        tick = self._seek_tick_mono
        self._seek_tick_mono = None
        if tick is not None:
            elapsed = (time.monotonic() - tick) * 1000.0
            if elapsed <= 5000.0:
                extra["debounce_ms"] = round(elapsed, 1)
        self.trace("T1 seek entry", extra)

    # ------------------------------------------------------------------
    # The scheduler, the workers and the asset lifecycle.
    # ------------------------------------------------------------------

    def _qt_layer(self, surface, payload, layer):
        """The layer's retained native-render object FOR ONE SURFACE
        : the mini and the popover hold
        separate PlateLayers, so neither's raster can ever be
        consumed by the other. Raster demand is the scheduler's —
        never this path's.

        The wrapper's identity is (surface, layer, print epoch):
        within ONE epoch a layer's payload is content-addressed and
        immutable — the decoded LRU reuses the same object per layer
        and the repair/hydration paths never replace a layer's
        geometry under a surviving wrapper — and the epoch boundary
        retires the wrappers wholesale, so a payload object can never
        be swapped under a live wrapper (the pinned invariant;
        test_a_layer_payload_is_immutable_within_a_print_epoch)."""
        if payload is None or layer < 0:
            return None
        cached = surface.layers.get(layer)
        if cached is not None:
            surface.layers.move_to_end(layer)
            return cached
        wrapped = PlateLayer(payload)
        wrapped.set_expected_key(surface.render_key())
        surface.layers[layer] = wrapped
        # The wrapper pins the payload against the decoded budget:
        # the LRU's eviction must not uncharge bytes the wrapper
        # keeps alive.
        if self._pins is not None:
            self._pins.pin_decoded(layer)
        while len(surface.layers) > 6:
            evicted, _ = surface.layers.popitem(last=False)
            # The bookkeeping must not outlive the wrapper: an
            # evicted layer's tokens go with it.
            surface.tokens.pop(evicted, None)
            if self._pins is not None:
                self._pins.unpin_decoded(evicted)
        return wrapped

    def _raster_hot(self, surface, layer):
        """The render key's verdict: a
        raster is usable only when its key matches the surface's
        CURRENT key — an old-view image never reads as current
        after a zoom/pan/resize."""
        wrapped = surface.layers.get(layer)
        return wrapped is not None and wrapped.rasterValid

    def scrub_vector(self, popover):
        """The vector crosses into QML ONLY for the partial progress
        states: a full 100% seek — and the
        empty 0% — display through the raster alone, so the measured
        ~500 ms nested QVariant wrap never rides an ordinary seek.
        The first partial scrub activates it (one wrap per layer,
        lazily)."""
        if popover is None:
            return None
        layers = popover.get("layers") or {}
        current = layers.get("current")
        split = popover.get("split")
        motions = popover.get("motionTotal") or 0
        if current is None or split is None or motions <= 0:
            return None
        if split <= 0 or split >= motions:
            return None
        return qml_geometry(current)

    def window_for(self, surface, layers, anchor, method=None, split=None,
                   lookup_ms=None):
        """The prev/current/next window for ONE SURFACE: the
        wrappers are created lazily and
        the desired state — current first, then the ghosts — feeds
        the surface's scheduler. The anchor lives ON the surface:
        the mini validates against its live anchor, the popover
        against whichever layer it displays (frozen included). The
        SPLIT rides the desired state too: a partial layer's
        printed prefix is its own demand. `lookup_ms` is only the
        coordinator's cheap plate_progress() lookup; service worker time
        is measured separately and must never be inferred from it."""
        surface = self.surface(surface)
        if surface is None or not layers:
            return {}
        if not isinstance(anchor, int):
            anchor = 0
        if surface.anchor != anchor:
            self.clear_surface_checkpoints(surface)
            surface.anchor = anchor
            surface.anchor_epoch += 1
        current = self._qt_layer(surface, layers.get("current"), anchor)
        self.trace("T6 payload obtained", {
            "surface": surface.name, "layer": anchor,
            "method": method or (layers.get("method") if isinstance(layers, dict) else None),
            "lookup_ms": round(lookup_ms, 1) if lookup_ms is not None else None})
        self.trace("T7/T8 layer obtained", {
            "surface": surface.name, "layer": anchor,
            "raster": "hot" if self._raster_hot(surface, anchor) else "miss"})
        window = {"prev": self._qt_layer(surface, layers.get("prev"), anchor - 1),
                  "current": current,
                  "next": self._qt_layer(surface, layers.get("next"), anchor + 1)}
        # The desired ghost state: a full
        # pair, never a single overwritable slot; each ghost renders
        # at most once per demand — the scheduler's hot-check skips
        # a ghost that is already rasterised or in flight.
        surface.desired = {
            "current": anchor,
            "ghosts": {role: layer
                       for role, layer in (("prev", anchor - 1), ("next", anchor + 1))
                       if layers.get(role) is not None and layer >= 0},
            "epoch": surface.anchor_epoch,
            "split": split,
        }
        # A running job whose layer left the window cancels at the
        # renderer's next segment boundary — the new current never
        # waits out a full obsolete render. A new demand re-arms the
        # persistent-failure latch.
        surface.job_failures = 0
        self._cancel_obsolete_job(surface)
        if current is not None and split is not None and split > 0:
            current.restore_prefix(current._expected_key, split)
        self._schedule_surface(surface)
        # The interaction raster follows the CONTENT state (the
        # window, the split, the toggles) — its warm background
        # update schedules here, never on the camera path.
        self.schedule_navigation(surface)
        return window

    def _prefix_wanted(self, surface, layer, split):
        """The partial layer's prefix demand: none yet, a backward
        move, or the live split has run a quarter of the layer past
        the rendered prefix — the tail's QML walk stays a cheap
        delta between prefix refreshes. While attached the advance
        is a time-budgeted CHECKPOINT instead of a motion threshold:
        the QML tail accumulates [P, split) per poll, and the native
        prefix advances at most once per cadence, snapshotting the
        latest split when it does."""
        wrapped = surface.layers.get(layer)
        if wrapped is None or split is None or split <= 0:
            return False
        motions = wrapped.motions
        if split >= motions:
            return False
        # A prefix rendered for an old view/plot key is no prefix at all.
        # QML hides it; the scheduler must therefore request a replacement
        # even when the numeric split did not move.
        if not wrapped.prefixValid:
            return True
        have = wrapped.prefixSplit
        if have < 0:
            return True
        if split < have:
            return True
        if self._attached():
            return split > have and time.monotonic() >= getattr(
                wrapped, "_prefix_checkpoint_at", 0.0)
        # The refresh threshold rides the INCREMENTAL render: the
        # worker strokes only [have, split) over the committed
        # picture, so a refresh costs O(delta), not O(split) — the
        # threshold tightens to keep the canvas's tail walk (the
        # visible gap between refreshes) small.
        return split - have > max(100, int(motions * 0.03))

    def _schedule_surface(self, surface):
        """The bounded demand scheduler :
        ONE job in flight per surface; the current layer's demand
        always outranks the ghosts; a hot layer never creates work;
        the newest desired current supersedes an obsolete one —
        rapid slider movement through 100..104 starts at most one
        current job plus its ghosts, never one per visited layer."""
        if surface.gpu_rendering:
            return
        if surface.job is not None:
            return  # the running job's completion re-schedules
        if surface.plot is None or not surface.view.get("width"):
            return  # no context yet — the demand waits for the feed
        desired = surface.desired
        if desired is None:
            return
        split = desired.get("split")
        # The demand queue, highest priority first: a partial
        # layer's printed PREFIX (the measured verdict — the QML
        # walk for the partial states costs ~900 ms at 500k), then
        # the full current (the grey base rides it), then the
        # ghosts.
        demand = []
        current = desired["current"]
        if self._prefix_wanted(surface, current, split):
            demand.append(("prefix", current, split))
        demand.append(("full", current, None))
        for role in ("prev", "next"):
            layer = desired["ghosts"].get(role)
            if layer is not None:
                demand.append(("full", layer, None))
        # The scheduler's depth: the not-yet-hot demands this pass
        # could burn work for (the trace and the rapid-drag report
        # read it).
        depth = 0
        for _kind, layer, _split in demand:
            if _kind == "prefix":
                depth += 1
            elif not self._raster_hot(surface, layer):
                depth += 1
        surface.stats["depth_max"] = max(surface.stats["depth_max"], depth)
        for kind, layer, prefix_split in demand:
            wrapped = surface.layers.get(layer)
            if wrapped is None:
                continue
            if kind == "prefix":
                if not self._prefix_wanted(surface, layer, split):
                    continue
            elif self._raster_hot(surface, layer):
                continue
            token = surface.tokens.get(layer, 0) + 1
            surface.tokens[layer] = token
            surface.render_count[layer] = surface.render_count.get(layer, 0) + 1
            generation = surface.generation
            surface.render_serial += 1
            serial = surface.render_serial
            epoch = surface.job_epoch
            self.trace("T9 raster start", {
                "surface": surface.name, "layer": layer, "queue": depth,
                "generation": generation, "token": token, "kind": kind,
                "split": prefix_split, "epoch": epoch, "serial": serial})
            plot = surface.plot
            view = dict(surface.view)
            key = surface.render_key()
            cancel = threading.Event()
            surface.job = {"layer": layer, "token": token,
                           "generation": generation, "state": "submitted",
                           "cancel": cancel, "epoch": epoch, "serial": serial,
                           "kind": kind, "split": prefix_split}
            if kind == "prefix":
                wrapped.set_prefix_pending(True)
            ticket = (surface.name, layer, token, generation, key, kind,
                      prefix_split, epoch, serial)
            payload = wrapped._payload
            # The incremental render's base (the forward scrub's
            # refresh cost): the wrapper's committed prefix picture
            # and its boundary — the worker copies the image and
            # strokes only [previous_split, prefix_split). A stale
            # context falls back to the full walk: the picture bakes
            # the view transform, so only the SAME render key may
            # seed the copy — a zoom or pan between the commit and
            # this render would stroke the new view over old-scale
            # pixels (the out-of-scale ghost).
            previous_image, previous_boundary = wrapped.prefix_seed(key, prefix_split if kind == "prefix" else 0)
            previous_file, file_boundary = wrapped.prefix_file_seed(key, prefix_split if kind == "prefix" else 0)
            if previous_file and file_boundary > previous_boundary:
                previous_image, previous_boundary = None, file_boundary
            else:
                previous_file = ""
            if previous_image is None and not previous_file:
                previous_image = getattr(wrapped, "_prefix", None)
                previous_boundary = wrapped.prefixSplit
            if not previous_file and getattr(wrapped, "_prefix_key", None) != key:
                previous_image = None
                previous_boundary = 0

            def build(ticket=ticket, payload=payload, plot=plot, view=view,
                      surface=surface, layer=layer, generation=generation,
                      kind=kind, prefix_split=prefix_split, epoch=epoch,
                      serial=serial, cancel=cancel,
                      directory=self._raster_cache_dir,
                      bridge=self._bridge,
                      previous_image=previous_image,
                      previous_file=previous_file,
                      previous_boundary=previous_boundary):
                # Every job ends in exactly ONE terminal emit: the
                # success payload, a cancelled marker, or a failure
                # marker. A worker that throws can never wedge the
                # surface's job slot. The emits ride the teardown
                # guard — a bridge whose owner died mid-build drops
                # the job instead of aborting the pool thread.
                def emit(payload):
                    _bridge_emit(bridge, "done", payload, ticket)
                if not _bridge_emit(bridge, "started", ticket):
                    return
                try:
                    if cancel.is_set():
                        emit(("cancelled",))
                        return
                    stem = "r-%s-e%d-%d-g%d-s%d" % (
                        surface.name, epoch, layer, generation, serial)
                    if kind == "prefix":
                        if previous_file:
                            previous_image = QImage(QUrl(previous_file).toLocalFile())
                        image = render_layer_prefix(
                            payload, plot, view, prefix_split, cancel=cancel,
                            previous=previous_image,
                            previous_split=previous_boundary
                            if previous_boundary is not None else 0)
                        if cancel.is_set():
                            emit(("cancelled",))
                            return
                        url = png_file(image, directory, stem + "-p%d" % prefix_split)
                        emit(("prefix", image, url, prefix_split))
                        return
                    coloured, base, travels = render_layer_raster(
                        payload, plot, view, cancel=cancel)
                    if cancel.is_set():
                        emit(("cancelled",))
                        return
                    emit(("full", coloured, png_file(coloured, directory, stem + "-c"),
                          base, png_file(base, directory, stem + "-b"),
                          travels, png_file(travels, directory, stem + "-t")))
                except Exception as exc:
                    emit(("failed", str(exc)))
            QThreadPool.globalInstance().start(_RasterJob(build))
            return
        self._schedule_rewind_checkpoints(surface)

    def _schedule_rewind_checkpoints(self, surface):
        if surface.gpu_rendering:
            return
        if surface.name != "popover" or surface.desired is None:
            return
        layer = surface.desired["current"]
        wrapped = surface.layers.get(layer)
        if wrapped is None or wrapped.motions < 20 or wrapped._rewind_ticket is not None:
            return
        surface.render_serial += 1
        ticket = (surface.name, layer, 0, surface.generation, surface.render_key(),
                  "checkpoints", None, surface.job_epoch, surface.render_serial)
        cancel = threading.Event()
        wrapped._rewind_ticket, wrapped._rewind_cancel = ticket, cancel
        payload, plot, view = wrapped._payload, dict(surface.plot), dict(surface.view)
        motions = wrapped.motions
        directory, bridge = self._raster_cache_dir, self._bridge
        boundaries = sorted({int(motions * i / 20) for i in range(1, 20)})
        stems = {p: "rewind-%s-e%d-s%d-p%d" % (surface.name, ticket[7], ticket[8], p)
                 for p in boundaries}
        wrapped._rewind_pending = [QUrl.fromLocalFile(os.path.join(directory, stem + ".png")).toString()
                                  for stem in stems.values()]

        def build():
            assets = []
            budget = _CheckpointBudget(cancel)
            try:
                # Independent prefixes preserve stroke joins and class ordering.
                for boundary in boundaries:
                    if cancel.is_set():
                        break
                    image = render_layer_prefix(payload, plot, view, boundary, cancel=budget)
                    if cancel.is_set():
                        break
                    url = png_file(image, directory, stems[boundary])
                    if not url:
                        break
                    assets.append((boundary, url))
            except Exception as exc:
                logging.getLogger("MoonrakerPrintFollower").warning(
                    "rewind checkpoint worker failed: %s", exc)
            finally:
                if cancel.is_set():
                    for _boundary, url in assets:
                        try:
                            os.unlink(QUrl(url).toLocalFile())
                        except OSError:
                            pass
                    assets = []
                _bridge_emit(bridge, "done", ("checkpoints", assets), ticket)
        QTimer.singleShot(150, lambda: None if cancel.is_set() else
                          QThreadPool.globalInstance().start(_RasterJob(build), -1))

    def clear_surface_checkpoints(self, surface):
        files = []
        for wrapped in surface.layers.values():
            files.extend(wrapped._rewind_files.values())
            wrapped.clear_prefix_checkpoints()
        for url in files:
            self._unlink_asset_files(("prefix", None, url, 0))

    def _navigation_backing(self, surface):
        """The interaction raster's backing: 400% of the 100%-fit
        view, reduced when 4x would blow the safe single-buffer
        budget (the double-buffered peak holds two CPU images and
        two GPU textures — the fallback logs once and shrinks)."""
        width = int(surface.view.get("width") or 0)
        height = int(surface.view.get("height") or 0)
        if width <= 0 or height <= 0:
            return 4.0
        budget = 64 * 1024 * 1024  # one CPU buffer's safe share
        backing = min(4.0, (budget / (width * height * 4.0)) ** 0.5)
        if backing < 4.0:
            logging.getLogger("MoonrakerPrintFollower").warning(
                "navigation raster backing reduced to %.1fx for the "
                "%dx%d surface (memory safety)", backing, width, height)
        return max(1.0, backing)

    def navigation_data(self, surface):
        """The face-eligible navigation URL: the retained raster
        reaches QML ONLY while it is READY for the current demand —
        its key matches the demand key. A stale raster (a demand that
        moved, a replacement still rendering, a failed replacement)
        reads "" and the exact scene serves the gesture."""
        demand = self._navigation_key(surface)
        if surface.nav["url"] and demand is not None:
            stored = surface.nav.get("key")
            if stored == demand or (self._attached()
                                    and navigation_compatible(stored, demand)):
                return surface.nav["url"]
        return ""

    def _navigation_key(self, surface):
        """The interaction raster's content key: the job epoch, the
        window's payload identity, the split, the toggles, the line
        style, the plot and the surface's dimensions. PAN and ZOOM
        are presentation transforms and never appear here — this is
        why both stay free while interacting."""
        desired = surface.desired
        if desired is None:
            return None
        window = []
        for layer in (desired["current"], desired["ghosts"].get("prev"),
                      desired["ghosts"].get("next")):
            wrapped = surface.layers.get(layer) if layer is not None else None
            window.append(id(wrapped._payload) if wrapped is not None else None)
        return NavigationSceneKey(
            surface=surface.name,
            job_epoch=surface.job_epoch,
            payload_ids=tuple(window),
            split=desired.get("split"),
            show_previous=bool(self._scene().show_previous),
            show_next=bool(self._scene().show_next),
            show_base=bool(self._scene().show_base),
            show_travels=bool(self._scene().show_travels),
            show_axis_arrows=bool(self._scene().show_axis_arrows),
            line_scale=round(float(surface.view.get("lineScale") or 0.7), 6),
            width=int(surface.view.get("width") or 0),
            height=int(surface.view.get("height") or 0),
            bed_width=round(float(self._scene().bed_width or 0.0), 6),
            bed_depth=round(float(self._scene().bed_depth or 0.0), 6),
            plot=tuple(sorted((k, round(float(v), 6))
                              for k, v in (surface.plot or {}).items())),
            # Device ratio changes stroke width; a zoom changes the
            # adaptive grid width. Both genuinely invalidate the bake.
            dpr=round(min(2.0, max(1.0, float(
                surface.view.get("dpr") or 1.0))), 6),
            zoom=round(float(surface.view.get("scale") or 1.0), 6),
            true_thickness=bool(surface.view.get("trueThickness")),
            colour_scheme=json.dumps(surface.view.get("colourScheme") or {}, sort_keys=True))

    @staticmethod
    def _nav_key_hard(key):
        return navigation_hard_key(key)

    @staticmethod
    def _nav_key_zoom(key):
        return navigation_zoom(key)

    def _nav_arm_wake(self, surface):
        """Arm the attached throttle's expiry wake: when the start-
        time window passes, the LATEST demand schedules once — the
        coalesced catch-up, independent of any further poll. The
        captured deadline makes a superseded arm inert: a wake
        armed for an older window must never clear the newer one."""
        wake_at = surface.nav.get("wake_at")
        if not wake_at:
            return
        remaining_ms = max(0, int((wake_at - time.monotonic()) * 1000))
        QTimer.singleShot(remaining_ms,
                          lambda s=surface, deadline=wake_at:
                          self._nav_wake(s, deadline))

    def _nav_wake(self, surface, deadline=None):
        """The window expired: clear the throttle and the failed-hard
        latch, then let the scheduler take the latest demand.

        A settle wake is the zoom's own expiry and is not gated on the
        popover being open: the demand it fires for was scheduled by
        the gesture and nothing else would re-fire it, so dropping it
        would strand the raster at the level the wheel left behind."""
        surface = self.surface(surface)
        if surface is None or surface.name != "popover":
            return
        if deadline is not None and surface.nav.get("wake_at") != deadline:
            return  # a newer submit owns the window now
        settle = bool(surface.nav.get("wake_settle"))
        if not settle and not self._popover_open():
            return
        surface.nav["wake_at"] = None
        surface.nav["failed_hard"] = None
        # Expiry must also release the exact failed demand. When the last
        # poll and failed job have the same split, retaining this latch
        # otherwise prevents the promised retry until another poll changes it.
        surface.nav["failed"] = None
        # The wake IS the settle's own expiry: the demand it fires for
        # must bake, never re-enter the zoom's coalescing rule (whose
        # anchor — the promoted key — has not moved yet).
        self.schedule_navigation(surface, coalesce_zoom=False)

    def schedule_navigation(self, surface, coalesce_zoom=True):
        """The warm interaction raster's demand: ONE background job
        per surface (the live updates coalesce on the key), never on
        the camera path, and only for the popover — the mini does
        not carry this feature. A ready URL is what the face can
        switch to INSTANTLY on the first camera input."""
        if surface.gpu_rendering:
            return
        # Presentation holds the entry image; the exact scene still
        # receives every poll. Do not burn 4x composites mid-gesture.
        if self._interacting:
            return
        if surface.name != "popover" or surface.nav["job"] is not None \
                or surface.plot is None:
            return
        key = self._navigation_key(surface)
        # The failed-key latch: a demand whose last attempt FAILED is
        # never retried while it stays identical (every publish would
        # re-arm it at render cost). Any demand change produces a new
        # key and re-arms; a success clears the latch.
        if key is None or key == surface.nav["key"] \
                or key == surface.nav.get("failed"):
            return
        # The zoom's own settle (the measured storm): the zoom rides
        # the key, so every wheel step was a hard change that bypassed
        # the follow window — and a burst bought a CHAIN of full 4x
        # composites, each one a whole-scene walk plus its publication,
        # every one superseded by the next step. The wheel's steps are
        # one intent: a demand whose zoom slot moved off the last
        # PROMOTED raster rides this short trailing window instead, and
        # the level the wheel stops on bakes once. The split may move
        # with the zoom — the bake that follows carries the latest
        # demand anyway — but a split-only drift never fires this rule
        # (its zoom slot is unchanged) and keeps its follow window.
        if coalesce_zoom and surface.nav["key"] is not None \
                and self._nav_key_zoom(key) \
                != self._nav_key_zoom(surface.nav["key"]):
            surface.nav["wake_at"] = time.monotonic() + _NAV_ZOOM_SETTLE_S
            surface.nav["wake_settle"] = True
            self._nav_arm_wake(surface)
            return
        # The attached start-time throttle: the window runs from the
        # job's START, so a render overtaken by the split can never
        # starve the stamp and loop (the commit-time stamp's hole).
        # A soft split drift coalesces until the wake fires the
        # LATEST demand; a hard change (layer, print, zoom, dims,
        # toggles, plot) bypasses immediately. A failed hard key
        # retries once per window, never per poll.
        if self._attached():
            hard = self._nav_key_hard(key)
            if hard is None:
                return
            wake_at = surface.nav.get("wake_at")
            if wake_at is not None and hard == surface.nav.get("hard") \
                    and time.monotonic() < wake_at:
                self._nav_arm_wake(surface)
                return
            surface.nav["wake_at"] = None
            surface.nav["wake_settle"] = False
        desired = surface.desired
        window = {}
        for role, layer in (("current", desired["current"]),
                            ("prev", desired["ghosts"].get("prev")),
                            ("next", desired["ghosts"].get("next"))):
            wrapped = surface.layers.get(layer) if layer is not None else None
            window[role] = wrapped._payload if wrapped is not None else None
        if window["current"] is None:
            return
        backing = self._navigation_backing(surface)
        view = {"width": int(surface.view.get("width") or 0),
                "height": int(surface.view.get("height") or 0),
                "scale": 1.0,
                # The grid's adaptive width: the pen painted at
                # backing / zoom presents as the canvas's 1 px at
                # this zoom (the raster's camera transform scales
                # it back up — the live ruling). The dpr rides the
                # same coverage contract: the stroke floor presents
                # min(2/dpr, 1) logical px at this zoom.
                "zoom": float(surface.view.get("scale") or 1.0),
                "dpr": min(2.0, max(1.0, float(surface.view.get("dpr") or 1.0))),
                "lineScale": float(surface.view.get("lineScale") or 0.7),
                "travelVisualRatio": surface.view.get("travelVisualRatio"),
                "compact": False, "panX": 0.0, "panY": 0.0,
                "backing": backing,
                # The legend checkboxes are the scene's CONTENT: the
                # warm raster must mirror the exact view's toggles.
                "showPrevious": bool(self._scene().show_previous),
                "showNext": bool(self._scene().show_next),
                "showBase": bool(self._scene().show_base),
                "showTravels": bool(self._scene().show_travels),
                "showAxisArrows": bool(self._scene().show_axis_arrows),
                # The bed's machine bounds: the grid rides the same
                # composite — the COMPLETE scene (the grid AND the
                # geometry) switches to the warm raster as one.
                "bedWidth": float(self._scene().bed_width or 0.0),
                "bedDepth": float(self._scene().bed_depth or 0.0)}
        view["colourScheme"] = surface.view.get("colourScheme") or {}
        view["trueThickness"] = bool(surface.view.get("trueThickness"))
        if "lineWidthPx" in surface.view:
            view["lineWidthPx"] = surface.view["lineWidthPx"]
        plot = dict(surface.plot)
        split = desired.get("split")
        epoch = surface.job_epoch
        # The incremental base: the last committed composite serves as
        # the delta's canvas when it carries the SAME scene — the hard
        # key matches on every field but the split — and the demand
        # only moved the boundary forward. Anything else (a layer, a
        # toggle, the zoom, a backward move) is a different picture
        # and bakes whole.
        previous = None
        previous_split = 0
        if surface.nav["image"] is not None and split is not None \
                and self._nav_key_hard(surface.nav["image_key"]) \
                == self._nav_key_hard(key):
            held_split = surface.nav["image_split"]
            if held_split is not None and 0 < held_split <= split:
                previous = surface.nav["image"]
                previous_split = held_split
        surface.nav["serial"] += 1
        serial = surface.nav["serial"]
        cancel = threading.Event()
        surface.nav["cancel"] = cancel
        surface.nav["job"] = {"key": key, "cancel": cancel, "epoch": epoch,
                             "serial": serial, "backing": backing}
        if self._attached():
            # The throttle stamps at the START: the next bake is
            # permitted one window from now, whatever happens to this
            # render — and the wake fires the latest demand when the
            # window expires.
            surface.nav["hard"] = self._nav_key_hard(key)
            surface.nav["wake_at"] = time.monotonic() + _NAV_FOLLOW_BAKE_S
            surface.nav["wake_settle"] = False
            self._nav_arm_wake(surface)
        ticket = (surface.name, -1, 0, 0, key, "nav", split, epoch, serial)

        def build(ticket=ticket, window=window, plot=plot, view=view,
                  split=split, cancel=cancel, surface=surface,
                  serial=serial, epoch=epoch, key=key, previous=previous,
                  previous_split=previous_split,
                  directory=self._raster_cache_dir,
                  bridge=self._bridge):
            def emit(payload):
                _bridge_emit(bridge, "done", payload, ticket)
            try:
                if cancel.is_set():
                    emit(("cancelled",))
                    return
                image = render_navigation_layer(window, plot, view,
                                                split, cancel=cancel,
                                                previous=previous,
                                                previous_split=previous_split)
                if cancel.is_set():
                    emit(("cancelled",))
                    return
                url = png_file(image, directory,
                               "n-%s-e%d-s%d" % (surface.name, epoch, serial))
                if not url:
                    # An unpublished PNG is a FAILED render, never a
                    # successful one: the terminal's failure latch
                    # holds the demand until it moves (no hot-retry),
                    # and the stale ready raster stays ineligible for
                    # the failed demand.
                    logging.getLogger("MoonrakerPrintFollower").warning(
                        "navigation raster publication failed (%s, epoch %d, "
                        "serial %d)", surface.name, epoch, serial)
                    emit(("failed", "the navigation PNG could not publish"))
                    return
                emit(("nav", image, url, key))
            except Exception as exc:
                emit(("failed", str(exc)))
        QThreadPool.globalInstance().start(_RasterJob(build))

    def _nav_committed(self, images, ticket):
        """The interaction raster's commit: the epoch and the
        content key gate the double buffer — a superseded or stale
        generation's file dies on arrival, the ready URL is promoted
        atomically, and presentation references govern retirement."""
        name, _layer, _token, _gen, key, _kind, _split, epoch, serial = ticket
        surface = self._surfaces.get(name)
        if surface is None:
            self._unlink_asset_files(images)
            return
        job = surface.nav["job"]
        if images and isinstance(images, tuple) and images[0] in ("failed", "cancelled"):
            # The terminal must belong to the ACTIVE job — the serial
            # is the job's own identity. A stale cancellation from a
            # superseded job (A cancelled, B started, A's terminal
            # arrives) must never clear the slot B owns (the review's
            # finding).
            if job is not None and job["serial"] == serial \
                    and surface.job_epoch == epoch:
                surface.nav["job"] = None
                # The ACTIVE job ended without a picture: its key
                # latches so the identical demand never hot-retries,
                # and a demand that has SINCE CHANGED reschedules.
                # While attached the latch rides the HARD key — the
                # advancing split would re-arm the full key every
                # poll — and the retry comes once per window.
                surface.nav["failed"] = job["key"]
                if self._attached():
                    surface.nav["failed_hard"] = self._nav_key_hard(job["key"])
                    surface.nav["wake_at"] = time.monotonic() + _NAV_FOLLOW_BAKE_S
                    surface.nav["wake_settle"] = False
                    self._nav_arm_wake(surface)
                self.schedule_navigation(surface)
            return
        if not images or not isinstance(images, tuple) or images[0] != "nav":
            return
        _kind, _image, url, painted_key = images
        if job is None or surface.job_epoch != epoch or job["serial"] != serial \
                or job["key"] != key or painted_key != key:
            self._unlink_asset_files(images)
            return
        # The demand gate (the review's stale-promotion finding): an
        # obsolete-but-internally-consistent job must not promote —
        # the content it painted is no longer what the surface needs,
        # even though its own ticket and key still match themselves.
        # While attached, a key that differs ONLY in the split still
        # presents the same scene: the render commits as a slightly
        # older, compatible warm raster instead of feeding the
        # render/discard/retry loop — the QML tail owns the exact
        # progress.
        demand = self._navigation_key(surface)
        compatible = self._attached() \
            and navigation_compatible(key, demand)
        if demand is None or (demand != key and not compatible):
            self._unlink_asset_files(images)
            surface.nav["job"] = None
            self.schedule_navigation(surface)
            return
        surface.nav["job"] = None
        surface.nav["failed"] = None
        surface.nav["backing"] = job["backing"]
        if self._attached():
            surface.nav["failed_hard"] = None
            if surface.nav.get("wake_at") is None:
                # The window expired while this render ran (the wake
                # already cleared the throttle and found the slot
                # busy): the LATEST demand schedules immediately —
                # stamping a fresh window here would strand the
                # demand the wake fired for (the stale-split bake).
                self.schedule_navigation(surface)
            else:
                # The next catch-up bake one window after this
                # commit — the wake coalesces whatever the polls
                # advance to.
                surface.nav["wake_at"] = time.monotonic() + _NAV_FOLLOW_BAKE_S
                surface.nav["wake_settle"] = False
                self._nav_arm_wake(surface)
        surface.nav["url"] = url
        surface.nav["key"] = key
        # The composite this promotion came from becomes the next
        # bake's incremental base. It is assigned on the handover, so
        # exactly one composite beyond the live one is ever resident —
        # the double-buffered peak the backing policy is written
        # against, never a growing cache.
        surface.nav["image"] = images[1]
        surface.nav["image_key"] = key
        surface.nav["image_split"] = _split
        self._notify()
        # Presentation may still own the superseded URL. All published
        # assets retire through the same bounded, reference-aware sweep.
        self._prune_raster_cache()

    def _unlink_asset_files(self, images):
        """A discarded job's files are dead on arrival — remove
        them now, never wait for the generic pruning."""
        if not isinstance(images, tuple) or not images:
            return
        referenced = self._referenced_raster_files()
        if images[0] == "checkpoints":
            for boundary, url in images[1]:
                self._unlink_asset_files(("prefix", None, url, boundary))
        elif images[0] == "full":
            for url in images[2], images[4], images[6]:
                if not url or not url.startswith("file://"):
                    continue
                if self._raster_file_key(QUrl(url).toLocalFile()) in referenced:
                    continue
                try:
                    os.unlink(QUrl(url).toLocalFile())
                except OSError:
                    pass
        elif images[0] == "prefix":
            url = images[2]
            if url and url.startswith("file://") and self._raster_file_key(QUrl(url).toLocalFile()) not in referenced:
                try:
                    os.unlink(QUrl(url).toLocalFile())
                except OSError:
                    pass
        elif images[0] == "nav":
            url = images[2]
            if url and url.startswith("file://") and self._raster_file_key(QUrl(url).toLocalFile()) not in referenced:
                try:
                    os.unlink(QUrl(url).toLocalFile())
                except OSError:
                    pass

    def cleanup_raster_cache(self):
        """The instance's own raster directory goes with the model —
        never another model's assets."""
        try:
            import shutil
            shutil.rmtree(self._raster_cache_dir, ignore_errors=True)
        except OSError:
            pass

    @staticmethod
    def _raster_file_key(path):
        """The raster assets' comparison spelling.

        QUrl spells a local file with '/' on EVERY platform — Windows'
        toLocalFile() included — while the directory scan builds the
        native form, so the reference set and the scan only agree once
        both are keyed this way. Compared raw on Windows, the set
        matched nothing and the prune unlinked the live wrapper's own
        picture."""
        return os.path.normcase(os.path.normpath(path))

    def _referenced_raster_files(self):
        """The asset files the live wrappers still display, plus the
        retained navigation raster: the prune must never unlink a URL
        a wrapper or the warm-scene face still reads. A retired
        navigation asset (the url cleared) drops out of the set and
        the next prune collects it."""
        referenced = set()
        for files in self._asset_owners.values():
            referenced.update(files)
        # The live gesture's latch, before anything else: it is the
        # one file whose removal is visible immediately.
        if self._gesture_raster:
            referenced.add(self._raster_file_key(
                QUrl(self._gesture_raster).toLocalFile()))
        for surface in self._surfaces.values():
            nav_url = surface.nav.get("url") if surface.nav else None
            if nav_url:
                referenced.add(self._raster_file_key(QUrl(nav_url).toLocalFile()))
            for wrapped in surface.layers.values():
                for url in wrapped.prefix_references():
                    if url:
                        referenced.add(self._raster_file_key(QUrl(url).toLocalFile()))
                for url in (wrapped.rasterData, wrapped.baseData,
                            wrapped.travelData, wrapped.prefixData):
                    if url:
                        referenced.add(self._raster_file_key(QUrl(url).toLocalFile()))
        return referenced

    def _prune_raster_cache(self, keep=64):
        """The raster cache's bound (the file-URL transport): the
        newest `keep` PNGs survive, a file a live wrapper still
        displays ALWAYS survives (the old newest-N sweep could
        unlink the picture on screen), and an in-flight publication's
        temp is never touched. Scoped to THIS model's directory, so
        another printer's assets are never touched."""
        try:
            referenced = self._referenced_raster_files()
            entries = []
            for name in os.listdir(self._raster_cache_dir):
                path = os.path.join(self._raster_cache_dir, name)
                if self._raster_file_key(path) in referenced or ".tmp-" in name:
                    continue
                try:
                    entries.append((os.stat(path).st_mtime, path))
                except OSError:
                    continue
            for _mtime, path in sorted(entries)[:-keep] if keep else entries:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except OSError:
            pass

    @staticmethod
    def _raster_job_matches(job, layer, token, generation, epoch, serial):
        """Exact identity of one submitted raster job.

        Tokens can restart after a surface retire/reopen, while generation
        and print epoch may stay unchanged. The monotonic serial is what
        makes those otherwise-identical jobs collision-proof.
        """
        return bool(job is not None
                    and job["layer"] == layer
                    and job["token"] == token
                    and job["generation"] == generation
                    and job["epoch"] == epoch
                    and job["serial"] == serial)

    @pyqtSlot(object)
    def _raster_started(self, ticket):
        """The worker's first line: a job the demand replaced while
        still queued is countable as superseded-before-start."""
        name, layer, token, generation, _key, _kind, _split, epoch, serial = ticket
        surface = self._surfaces.get(name)
        if surface is None:
            return
        job = surface.job
        if self._raster_job_matches(job, layer, token, generation, epoch, serial):
            job["state"] = "running"
            surface.stats["started"] += 1
            self.trace("T10 raster running", {"surface": name, "layer": layer})

    def unpin_surface(self, surface):
        """Release the surface's wrapper pins: the decoded budget
        uncharges the payloads only when the wrappers actually go
        (a retire keeps the wrappers hot, so it does NOT unpin)."""
        if self._pins is None:
            return
        for layer in list(surface.layers.keys()):
            self._pins.unpin_decoded(layer)

    def release_all_pins(self):
        """The model's death releases every wrapper pin: the index
        service outlives the monitor (the follower owns it)."""
        if self._pins is None:
            return
        for surface in self._surfaces.values():
            self.clear_surface_checkpoints(surface)
            self.unpin_surface(surface)
            update_gpu = getattr(self._pins, "set_gpu_rendering", None)
            if callable(update_gpu):
                update_gpu((id(self), surface.name), False)

    def retire_surface(self, surface):
        """A surface whose QML consumer has gone retires its
        demand: the running/queued job cancels cooperatively, the
        desired state goes, and the next publish rebuilds it from
        the payload. The hot rasters stay cached — only the
        no-longer-needed work stops."""
        self.clear_surface_checkpoints(surface)
        if surface.job is not None:
            surface.job["cancel"].set()
            surface.stats["superseded"] += 1
        surface.desired = None
        surface.job = None
        surface.tokens.clear()
        self.retire_navigation(surface)

    def retire_navigation(self, surface):
        """The navigation raster's retirement (the review's coherent
        lifecycle): the in-flight update cancels, the job slot frees,
        the content key and the ready URL invalidate, the failure
        latch resets, and the retained asset drops into the prune's
        reach. Every lifecycle that ends a surface's or a print's
        ownership — the popover close, the print switch, the model's
        destruction — runs exactly this, so no path can leave a
        stale slot the new print cannot schedule through."""
        if surface.nav["job"] is not None:
            surface.nav["cancel"].set()
            surface.nav["job"] = None
        surface.nav["key"] = None
        surface.nav["url"] = ""
        # The retained composite dies with the scene it belongs to:
        # it is the incremental base for a demand whose hard key
        # matches, and every lifecycle that lands here has changed
        # the scene (a print switch, a popover close, a model
        # teardown) — holding its pixels would be a residency with no
        # reader.
        surface.nav["image"] = None
        surface.nav["image_key"] = None
        surface.nav["image_split"] = None
        # The failure latch resets with the lifecycle: a reopened
        # popover (or a new print) retries a demand whose earlier
        # failure may have been transient (a payload since rebuilt).
        surface.nav["failed"] = None
        # The follow throttle retires too: a wake armed for the old
        # print must find nothing to clear (and the new print's hard
        # key differs anyway — the epoch rides it).
        surface.nav["wake_at"] = None
        surface.nav["wake_settle"] = False
        surface.nav["failed_hard"] = None

    def _cancel_obsolete_job(self, surface):
        """The running job no longer matches the desired demand —
        the anchor moved, the demand was replaced, or a prefix's
        requested split changed: cancel it at the renderer's next
        segment boundary so the new current never waits out a full
        obsolete render."""
        job = surface.job
        if job is None:
            return
        desired = surface.desired
        if desired is None:
            job["cancel"].set()
            return
        layer = job["layer"]
        if job.get("split") is not None:
            # A prefix render only ever serves the desired current:
            # it becomes obsolete when the current moves on (a ghost
            # wants a full, never a prefix) or when the split moves
            # BACKWARD under it — the painted interval would exceed
            # the demand. A forward advance keeps the render: the
            # prefix at P still owns [0..P] of the newer demand, and
            # the tail covers [P..Q] (the review's scrub policy —
            # cancelling useful work fed the disappearance).
            if layer != desired["current"] \
                    or desired.get("split") is None \
                    or desired.get("split") < job["split"]:
                job["cancel"].set()
        elif layer != desired["current"] and layer not in desired["ghosts"].values():
            # Submitted or running: the flag stops a queued job at
            # its pre-render check and a running one at the next
            # segment boundary.
            job["cancel"].set()

    @pyqtSlot(object, object)
    def trace(self, stage, extra=None):
        """The seek timeline, disabled by default:
        MOONRAKER_FOLLOWER_SEEK_TRACE=1 (or the config's seek_trace)
        records each stage with its wall-clock offset from the
        seek's entry.

        The measurable stages: T1 the debounced slider commit,
        carrying the debounce measured from the raw slider tick
        (the slider reports it), T6 the payload's arrival (its
        method says which index path served it, the coordinator's
        decode duration rides beside it), T7/T8 the
        PlateLayer obtained with the raster hot/miss verdict, T9
        the job's demand with the queue depth, generation and
        token, T10 the worker's first line, T11 the owner-thread
        commit with the scheduler's counters, T12 a context
        commit, T13 the publish that hands the committed picture
        to the scene — the composition beyond is the engine's own
        and is not measurable from the model's side."""
        if not self._seek_trace_enabled and not self._trace_requested():
            return
        if stage == "T1 seek entry":
            self._seek_trace = []
            self._seek_start = time.monotonic()
        if not self._seek_trace and stage != "T1 seek entry":
            return
        entry = {"stage": stage, "ms": (time.monotonic() - self._seek_start) * 1000}
        if extra:
            entry.update(extra)
        self._seek_trace.append(entry)

    @pyqtSlot(object, object)
    def _raster_committed(self, images, ticket):
        """The owner-thread commit: validate the print epoch, the
        generation, the layer's token and the retained identity,
        hand the images to the wrapper, and let the scheduler take
        the next demand. Only an EXACT ticket match may clear the
        active job — a stale completion can never clear an
        unrelated submitted one."""
        name, layer, token, generation, key, kind, prefix_split, epoch, serial = ticket
        surface = self._surfaces.get(name)
        if surface is None:
            self._unlink_asset_files(images)
            return
        if kind == "nav":
            # The navigation raster's own commit: the exact scene's
            # job slot and counters never see these tickets.
            self._nav_committed(images, ticket)
            return
        if kind == "checkpoints":
            wrapped = surface.layers.get(layer)
            if wrapped is None or wrapped._rewind_ticket != ticket \
                    or key != wrapped._expected_key or surface.desired is None \
                    or layer != surface.desired["current"]:
                self._unlink_asset_files(images)
                return
            wrapped._rewind_files = dict(images[1])
            wrapped._rewind_pending = []
            wrapped._rewind_cancel = None
            self._prune_raster_cache()
            return
        exact_job = self._raster_job_matches(
            surface.job, layer, token, generation, epoch, serial)
        if exact_job and kind == "prefix" and layer in surface.layers:
            surface.layers[layer].set_prefix_pending(False)
        # The terminal kinds arrive without rendered assets: a
        # cancelled job stops where it was told, a failed one
        # reports the exception.
        if kind == "cancelled" or (images and isinstance(images, tuple)
                                   and images[0] == "cancelled"):
            if not exact_job:
                # A retired/replaced job may finish cancellation after a
                # same-layer token has been reused. It is stale terminal
                # noise, not a cancellation of the active job.
                surface.stats["discarded"] += 1
                self._schedule_surface(surface)
                return
            surface.stats["cancelled"] += 1
            surface.job = None
            self.trace("T11 raster cancelled", {"surface": name, "layer": layer})
            self._schedule_surface(surface)
            return
        if images and isinstance(images, tuple) and images[0] == "failed":
            if not exact_job:
                # Stale failures must not poison the current job's
                # persistent-failure latch. Serial identity applies to
                # every terminal path, not only successful commits.
                surface.stats["discarded"] += 1
                self._schedule_surface(surface)
                return
            surface.stats["failed"] += 1
            surface.job_failures = getattr(surface, "job_failures", 0) + 1
            logging.getLogger("MoonrakerPrintFollower").warning(
                "raster worker failed: %s", images[1])
            surface.job = None
            self.trace("T11 raster failed", {"surface": name, "layer": layer,
                                              "error": images[1][:120]})
            if surface.job_failures >= 5:
                # A persistently failing render must not retry every
                # cycle; the demand retires and the next payload's
                # change re-arms it.
                surface.desired = None
                self._notify()
                return
            self._schedule_surface(surface)
            return
        # Only the EXACT ticket clears or commits against the active job.
        # A retired surface can restart token numbering at one, so
        # layer/token/generation/epoch without serial is insufficient.
        if exact_job:
            surface.job = None
        if not exact_job or epoch != surface.job_epoch or generation != surface.generation \
                or surface.tokens.get(layer) != token \
                or surface.layers.get(layer) is None:
            surface.stats["discarded"] += 1
            self._unlink_asset_files(images)
            self._schedule_surface(surface)
            return
        wrapped = surface.layers[layer]
        if kind == "prefix":
            _kind, prefix, prefix_data, prefix_split = images
            desired = surface.desired
            if desired is None or layer != desired["current"] \
                    or desired.get("split") is None \
                    or prefix_split > desired.get("split"):
                # The demand moved under this render: the painted
                # interval is beyond the requested one (a backward
                # move) or the layer left the current slot — the
                # prefix must never supersede the newer demand's
                # picture. A FORWARD advance keeps the render: the
                # prefix at P still owns [0..P] of the newer demand,
                # and the tail covers [P..Q] (the review's scrub
                # policy — discarding useful work fed the
                # disappearance).
                surface.stats["discarded"] += 1
                self._unlink_asset_files(images)
                self._schedule_surface(surface)
                return
            wrapped.set_prefix(prefix, prefix_data, prefix_split, key)
            # The attached checkpoint's cadence re-arms from this
            # commit: the next advance is due one window later, and
            # the demand snapshots the split that is live THEN.
            if self._attached():
                wrapped._prefix_checkpoint_at = time.monotonic() + _PREFIX_CHECKPOINT_S
            if surface.tokens.get(layer) == token:
                surface.tokens.pop(layer, None)
            surface.stats["committed"] += 1
            self.trace("T11 raster committed", {
                "surface": name, "layer": layer, "kind": "prefix",
                "split": prefix_split})
            self._prune_raster_cache()
            self._schedule_surface(surface)
            self._notify()
            self.trace("T13 raster published", {
                "surface": name, "kind": "prefix"})
            return
        _kind, coloured, coloured_data, base, base_data, travels, travel_data = images
        wrapped.set_raster(coloured, key, coloured_data)
        wrapped.set_base(base, key, base_data)
        wrapped.set_travels(travels, key, travel_data)
        if surface.tokens.get(layer) == token:
            surface.tokens.pop(layer, None)
        desired = surface.desired
        if desired is not None and (layer == desired["current"]
                                    or layer in desired["ghosts"].values()):
            surface.stats["committed"] += 1
        else:
            surface.stats["discarded"] += 1
        self.trace("T11 raster committed", {
            "surface": name, "layer": layer,
            "started": surface.stats["started"],
            "committed": surface.stats["committed"],
            "superseded": surface.stats["superseded"],
            "discarded": surface.stats["discarded"],
            "depth_max": surface.stats["depth_max"]})
        self._prune_raster_cache()
        self._schedule_surface(surface)
        self._notify()
        self.trace("T13 raster published", {
            "surface": name, "kind": "full"})

    def arm_context_flush(self, surface):
        """One zero-tick flush per burst of staged context changes
        : the plot+view pair a transition
        publishes coalesces into ONE generation and ONE wave."""
        if surface.stage["armed"]:
            return
        surface.stage["armed"] = True
        QTimer.singleShot(0, lambda s=surface: self.flush_surface_context(s))

    def flush_surface_context(self, surface):
        """The staged context commits: one generation per settled
        burst, and only the visible current + ghosts re-raster —
        historical LRU entries stay stale and re-render lazily when
        revisited."""
        surface.stage["armed"] = False
        staged_plot = surface.stage["plot"]
        staged_view = surface.stage["view"]
        if staged_plot is None and staged_view is None:
            return
        plot = staged_plot if staged_plot is not None else surface.plot
        view = staged_view if staged_view is not None else surface.view
        if plot == surface.plot and view == surface.view:
            surface.stage["plot"] = None
            surface.stage["view"] = None
            return
        surface.plot = plot
        surface.view = view
        surface.generation += 1
        surface.stage["plot"] = None
        surface.stage["view"] = None
        key = surface.render_key()
        # The retained wrappers' rasters read invalid at the new key
        #  but STAY cached — only the
        # visible window re-rasters.
        self.clear_surface_checkpoints(surface)
        for wrapped in surface.layers.values():
            wrapped.set_expected_key(key)
        self.trace("T12 context committed", {"surface": surface.name,
                                              "generation": surface.generation})
        self._schedule_surface(surface)
        # A view publish is the moment the warm raster is about to be
        # latched — the one-off snapshot clears the follow throttle
        # so the CURRENT split bakes immediately (tens of
        # milliseconds at a live layer) instead of the window's older
        # raster snapping the view back with a gap where the tail has
        # since advanced (the live report).
        if surface.name == "popover" and self._attached():
            self.nav_gesture_bake(surface)

    def nav_gesture_bake(self, surface):
        """The one-off drag snapshot: clear the follow throttle and
        schedule with the CURRENT split — the press-time bake leads
        the first movement, so the latch catches a raster that has
        caught up with the painted lines. An in-flight cadence job is
        SUPERSEDED: the scheduler's one-job rule would otherwise
        swallow the press's demand, the stale job's commit would
        re-arm the window, and the gesture would latch a raster
        behind the painted lines (the live snap-back report)."""
        surface = self.surface(surface)
        if surface is None or surface.gpu_rendering or surface.name != "popover" \
                or not self._attached():
            return
        if surface.nav["job"] is not None:
            surface.nav["cancel"].set()
            surface.nav["job"] = None
        surface.nav["wake_at"] = None
        surface.nav["wake_settle"] = False
        surface.nav["failed"] = None
        surface.nav["failed_hard"] = None
        self.schedule_navigation(surface)
