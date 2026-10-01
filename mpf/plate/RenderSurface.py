"""One follower surface's native render context.

The popover and the mini each own their own view, plot, generation,
layer wrappers and scheduler state, so neither can overwrite the
other's context or invalidate its rasters. The decoded payloads stay
shared between surfaces; the rendered images (and their keys) never
are. This record is the state; PlateRenderController is the policy
that drives it.
"""
from __future__ import annotations

import json
from collections import OrderedDict


class RenderSurface:
    """The per-surface record: its view/plot context, its generation,
    its retained layer wrappers and the scheduler's bookkeeping for
    the bounded demand queue."""

    def __init__(self, name):
        self.name = name
        self.plot = None
        self.view = {}
        self.generation = 0
        self.layers = OrderedDict()          # layer -> PlateLayer (bound 6)
        self.anchor = None
        self.anchor_epoch = 0
        # The desired demand, set per publish: the current layer and
        # the ghost pair, stamped with the anchor epoch they belong
        # to.
        self.desired = None
        self.gpu_rendering = False
        self.tokens = {}                     # layer -> demand token
        self.job = None                      # {"layer", "token", "generation", "state", "cancel"}
        self.render_count = {}               # layer -> raster requests
        self.render_serial = 0               # the immutable-asset serial
        self.job_epoch = 0                   # the print epoch (set by the controller)
        self.visible = False                 # the surface's QML consumer gate
        self.stats = {"started": 0, "committed": 0, "superseded": 0,
                      "cancelled": 0, "failed": 0, "discarded": 0, "depth_max": 0}
        self.job_failures = 0                # the persistent-failure latch
        # The staged plot/view pair: the setters stage, one zero-tick
        # flush commits the burst.
        self.stage = {"plot": None, "view": None, "armed": False}
        # The navigation raster's double-buffered slot: `url` is the
        # READY interaction scene the face may switch to instantly,
        # `job` the in-flight background update (one per surface —
        # live updates coalesce), both camera-independent and
        # epoch-keyed. The mini never carries one.
        self.nav = {"key": None, "url": "", "job": None, "cancel": None,
                    "serial": 0,
                    # The attached follow's start-time throttle: `hard`
                    # is the last submitted job's hard key (the key
                    # with the volatile split neutralised), `wake_at`
                    # when the next bake is permitted, `failed_hard`
                    # the hard key whose render failed (retried once
                    # per window, never per poll).
                    "hard": None, "wake_at": None, "failed_hard": None,
                    # Which kind of window the armed wake belongs to:
                    # the attached follow's catch-up, or the zoom's own
                    # settle. The settle must land its raster even with
                    # the popover closed (nothing else re-fires that
                    # demand), so the wake is not gated on visibility.
                    "wake_settle": False,
                    # The committed composite itself, with the split it
                    # was painted to and the key it carries. It is the
                    # next bake's incremental base: a follow tick that
                    # only advances the split strokes the delta over a
                    # copy instead of re-walking the whole scene (the
                    # measured cadence cost). The SPLIT is why this
                    # image exists, so the key is kept beside it — a
                    # demand whose hard key differs must never reuse
                    # pixels from another scene.
                    "image": None, "image_key": None, "image_split": None}

    def render_key(self):
        """The key a raster must carry to display on this surface:
        the generation (which bumps exactly when the context changes)
        plus the explicit pixel-affecting inputs and the PRINT epoch,
        so a key is self-describing and a stale worker from a previous
        print can never match it."""
        view = self.view
        plot = self.plot or {}
        return (self.name, self.job_epoch, self.generation,
                int(view.get("width") or 0), int(view.get("height") or 0),
                bool(view.get("compact")), round(float(view.get("scale") or 1.0), 6),
                round(float(view.get("lineScale") or 0.7), 6),
                round(float(view.get("panX") or 0.0), 3), round(float(view.get("panY") or 0.0), 3),
                round(float(view.get("dpr") or 1.0), 6),
                round(float(plot.get("offsetX") or 0.0), 6), round(float(plot.get("offsetY") or 0.0), 6),
                round(float(plot.get("sx") or 0.0), 6), round(float(plot.get("sy") or 0.0), 6),
                round(float(plot.get("bedXMin") or 0.0), 6), round(float(plot.get("bedYMax") or 0.0), 6),
                bool(view.get("trueThickness")), float(view.get("lineWidthPx") or 0.0), json.dumps(view.get("colourScheme") or {}, sort_keys=True))
