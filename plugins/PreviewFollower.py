"""Preview policy/state, composed with a Cura port and read-only print/index inputs."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional

from .CuraAdapter import (
    apply_preview_decision,
    preview_current_layer,
    preview_current_path,
    preview_max_paths,
    preview_minimum_layer,
    preview_minimum_path,
    set_preview_minimum_path,
    set_preview_path,
)
from .FollowController import decide_layers
from .MoonrakerProtocol import live_position_in_gcode_space


def preview_override_kind(
    *,
    expected_layer: Optional[int],
    current_layer: int,
    expected_minimum_layer: Optional[int] = None,
    current_minimum_layer: Optional[int] = None,
    expected_path: Optional[float] = None,
    current_path: Optional[float] = None,
    expected_minimum_path: Optional[int] = None,
    current_minimum_path: Optional[int] = None,
    path_tolerance: float = 0.75,
) -> Optional[str]:
    """Classify a user-visible Preview deviation from the follower position.

    Cura has *two* independently movable handles for both layers and paths.
    The upper/current and lower/minimum handles emit the same change signals,
    so checking only ``getCurrentLayer()`` / ``getCurrentPath()`` misses manual
    movement of the lower handles.  ``expected_layer is None`` means the
    follower has not yet armed a position and therefore cannot safely infer
    user intent.
    """
    if expected_layer is None:
        return None

    if current_layer != expected_layer:
        return "layer"

    if (
        expected_minimum_layer is not None
        and current_minimum_layer is not None
        and current_minimum_layer != expected_minimum_layer
    ):
        return "layer"

    if (
        expected_path is not None
        and current_path is not None
        and abs(current_path - expected_path) >= path_tolerance
    ):
        return "path"

    if (
        expected_minimum_path is not None
        and current_minimum_path is not None
        and current_minimum_path != expected_minimum_path
    ):
        return "path"

    return None


@dataclass(frozen=True)
class PreviewState:
    attached: bool = True
    expected_layer: Optional[int] = None
    expected_minimum: Optional[int] = None
    expected_path: Optional[float] = None
    expected_minimum_path: Optional[int] = None
    observed_layer: Optional[int] = None
    path_layer: Optional[int] = None
    path_fraction: Optional[float] = None
    speed: float = 1.0
    duration: Optional[float] = None
    # The auto-improve-ETA opt-in: the learned drift between the
    # slicer's per-layer estimates and the observed print duration.
    eta_learn: bool = False
    drift: Optional[float] = None
    anchor_layer: Optional[int] = None
    anchor_duration: Optional[float] = None
    nozzle_valid: bool = False
    switched: bool = False
    eta_text: str = ""


class PreviewFollower:
    def __init__(self, cura, motion=None):
        self._cura = cura
        self._motion = motion
        self._state = PreviewState()

    @property
    def state(self): return self._state

    def bind_motion(self, motion):
        """The optional Qt display driver for path smoothing (see PreviewMotion)."""
        self._motion = motion

    def reset_print(self):
        self._reset_motion()
        state = self._state
        # The view handles survive a print-end reset: the print stopping
        # does not move Cura's view, so the armed baseline stays valid.
        # Wiping it on every inactive observation left the window
        # between observations permanently unarmed — drags and scrolls
        # in that window were ignored (the author's live report).
        self._state = PreviewState(attached=state.attached,
            expected_layer=state.expected_layer, expected_minimum=state.expected_minimum,
            expected_path=state.expected_path, expected_minimum_path=state.expected_minimum_path)

    def reset_tracking(self):
        self._reset_motion()
        self._state = replace(self._state, path_layer=None, path_fraction=None,
            anchor_layer=None, anchor_duration=None, nozzle_valid=False, eta_text="")

    def invalidate_view(self):
        self._reset_motion()
        self._state = replace(self._state, expected_layer=None, expected_minimum=None,
            expected_path=None, expected_minimum_path=None, nozzle_valid=False)

    def attach(self, attached=True):
        if not attached:
            self._reset_motion()
        self._state = replace(self._state, attached=bool(attached), nozzle_valid=False, eta_text="")
        self.remember()

    def _reset_motion(self):
        if self._motion is not None:
            self._motion.reset()

    def remember(self):
        view = self._cura.view
        self._state = replace(self._state,
            expected_layer=preview_current_layer(view),
            expected_minimum=preview_minimum_layer(view),
            expected_path=preview_current_path(view),
            expected_minimum_path=preview_minimum_path(view))

    def detect_override(self):
        state, view = self._state, self._cura.view
        if not state.attached or self._cura.suspended or view is None:
            return None
        current = preview_current_layer(view)
        if current is None: return None
        if state.expected_layer is None:
            # Unarmed (a view swap, a dropped connection or an absorbed
            # echo): adopt the view's current position as the baseline
            # so the NEXT change — a continuing drag — detaches. Passing
            # forever meant any drag in the unarmed window was ignored
            # until an observe happened to re-arm.
            self.remember()
            return None
        kind = preview_override_kind(expected_layer=state.expected_layer, current_layer=current,
            expected_minimum_layer=state.expected_minimum, current_minimum_layer=preview_minimum_layer(view),
            expected_path=state.expected_path, current_path=preview_current_path(view),
            expected_minimum_path=state.expected_minimum_path, current_minimum_path=preview_minimum_path(view))
        if kind:
            # The author's ruling: ANY user intervention to the layer
            # selection detaches the follower — no absorption window,
            # no auto re-attach. A spurious detach from Cura's own
            # restoration is the accepted cost; a missed detach is not.
            self.attach(False)
        return kind

    def observe(self, snapshot, status, config, index):
        """Observe physical state even when detached; apply only through the Cura port.

        Returns (status detail, hydration requests). No networking/index mutation.
        """
        stats = status.get("print_stats") or {}
        move = status.get("gcode_move") or {}
        try: speed = max(0.05, float(move.get("speed_factor") or 1))
        except (TypeError, ValueError): speed = 1.0
        try: duration = max(0.0, float(stats.get("print_duration") or 0))
        except (TypeError, ValueError): duration = None
        layer = snapshot.layer.index
        state = replace(self._state, speed=speed, duration=duration, nozzle_valid=False,
                        eta_learn=bool(getattr(config, "eta_learn", False)))
        if layer is not None:
            if layer != state.anchor_layer:
                state = replace(state, anchor_layer=layer, anchor_duration=duration)
            state = replace(state, observed_layer=layer)
        # The auto-improve-ETA opt-in (the author's ruling): learn the
        # print's drift from the slicer's elapsed estimate at the
        # current layer, clamped so an early-layer wobble cannot swing
        # the remaining estimate wildly.
        if state.eta_learn and duration and index is not None and layer is not None:
            times = index.elapsed_times
            if times and 0 < layer < len(times):
                boundary = times[layer - 1]
                if isinstance(boundary, (int, float)) and boundary and boundary > 60:
                    state = replace(state, drift=min(2.0, max(0.5, duration / boundary)))
        self._state = state
        if not snapshot.active:
            self.reset_print()
            return "Connected", ()
        if not config.enabled: return "Print active", ()
        if not state.attached:
            self.update_eta(snapshot, index)
            return "Detached", ()
        if self._cura.suspended: return "Cura busy", ()
        view = self._cura.view
        if view is None or not self._cura.has_toolpath: return "Print active", ()
        if layer is None: return "Waiting for layer data", ()
        maximum = self._cura.max_layer
        if maximum is None: return "Cura layer data unavailable", ()
        decision = decide_layers(layer, maximum, config.follow_mode)
        if config.auto_preview and not state.switched:
            if self._cura.switch_to_preview(): self._state = replace(self._state, switched=True)
        hydration = ()
        with self._cura.writing_preview():
            if (preview_current_layer(view) != decision.current_layer
                    or preview_minimum_layer(view) != decision.minimum_layer):
                apply_preview_decision(view, decision.current_layer, decision.minimum_layer)
            if config.path_follow and decision.follow_path:
                detail, hydration = self._follow_path(view, min(layer, maximum), status, index,
                    smooth=bool(getattr(config, "path_smoothing", True)))
                self._state = replace(self._state, nozzle_valid=detail.startswith("path "))
        # Re-arm expectations only once the view has actually accepted the
        # drive. Cura can defer view writes while it hangs (e.g. recovering
        # from rapid stage switches); arming against a not-yet-applied write
        # would let Cura's late restoration read as a user override and
        # detach the follower. Only the handles the decision drives are
        # verified: a None minimum means the handle was left alone.
        armed = preview_current_layer(view) == decision.current_layer
        if decision.minimum_layer is not None:
            armed = armed and preview_minimum_layer(view) == decision.minimum_layer
        if armed:
            self.remember()
            # NOTE: the echo window is armed ONLY at attach(). It used
            # to refresh here on every unarmed->armed transition, and
            # an absorbed drag deviation unarmed the follower — each
            # observe then re-armed the window, absorbing a slow drag
            # for the window's whole 3.5 s (the author's live report:
            # a slow drag detached only after ~3 s). The window now
            # covers just the moment after an attach.
        self.update_eta(snapshot, index)
        if self._state.nozzle_valid and config.show_toolhead_indicator: self._cura.show_nozzle()
        return "Printer paused" if snapshot.observation.state == "paused" else "Following", hydration

    def _follow_path(self, view, layer, status, index, *, smooth=True):
        if not hasattr(view, "setPath") or not hasattr(view, "getMaxPaths"):
            return "Path tracking unavailable", ()
        state = self._state
        if state.path_layer != layer:
            state = replace(state, path_layer=layer, path_fraction=None)
            self._state = state
        if index is None or layer >= len(index.ranges):
            if self._motion is not None:
                self._motion.reset()
            set_preview_path(view, 0.0)
            return "Waiting for index", ()
        if not index.hydrated(layer):
            # Stop the animation too: a stale target must not fight the
            # follower's own writes while the layer hydrates.
            if self._motion is not None:
                self._motion.reset()
            self._state = replace(state, path_fraction=0.0)
            set_preview_path(view, 0.0)
            return "Hydrating layer", (layer,)
        try:
            position = int((status.get("virtual_sdcard") or {}).get("file_position"))
        except (TypeError, ValueError, AttributeError):
            return "Waiting for file position", ()
        maximum = preview_max_paths(view)
        if maximum is None:
            return "Waiting for file position", ()
        if maximum <= 0: return "Layer has no paths", ()
        live = live_position_in_gcode_space(status.get("motion_report") or {}, status.get("gcode_move") or {})
        fraction, method = index.fraction(layer, position, live, state.path_fraction)
        fraction = max(state.path_fraction or 0.0, max(0.0, min(1.0, fraction)))
        self._state = replace(state, path_fraction=fraction)
        if preview_minimum_path(view) != 0:
            set_preview_minimum_path(view, 0)
        target = fraction * maximum
        if smooth and self._motion is not None:
            self._motion.write(layer, fraction, method)
        else:
            if self._motion is not None:
                self._motion.reset()
            current = preview_current_path(view)
            if current is None or abs(current - target) >= 0.5: set_preview_path(view, target)
        return f"path {round(target)}/{maximum} ({method})", (layer + 1,)

    @staticmethod
    def format_duration(seconds):
        hours, rest = divmod(max(0, int(round(seconds))), 3600)
        minutes, seconds = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def remaining(self, layer, index, *, end=False):
        state = self._state
        current = state.observed_layer
        if current is None or index is None: return None
        times = index.elapsed_times
        def boundary(n):
            if n < 0: return 0.0
            if n >= len(times) or times[n] is None: return None
            return float(times[n])
        target = boundary(layer if end else layer - 1)
        start, finish = boundary(current - 1), boundary(current)
        if target is None or start is None: return None
        fraction = (state.path_fraction or 0.0) if state.path_layer == current else 0.0
        if (state.anchor_layer == current and state.anchor_duration is not None and state.duration is not None
                and finish is not None and finish > start):
            observed = (state.duration - state.anchor_duration) * state.speed / (finish - start)
            fraction = max(fraction, min(1.0, max(0.0, observed)))
        now = start + ((finish - start) * fraction if finish is not None and finish >= start else 0)
        return max(0.0, target - now) / state.speed

    def remaining_end(self, index, estimated_time):
        """Remaining seconds until the END of the print (the Monitor's
        ETA), using the same anchors and observed speed ratio as
        remaining(). The final layer has no next boundary, so the end
        anchor is the last boundary plus the layer's share of the
        remaining slicer estimate — falling back to the mean layer
        duration when no estimate is available."""
        state = self._state
        current = state.observed_layer
        if current is None or index is None: return None
        times = index.elapsed_times
        if not times or times[0] is None: return None
        def boundary(n):
            if n < 0: return 0.0
            if n >= len(times) or times[n] is None: return None
            return float(times[n])
        start, finish = boundary(current - 1), boundary(current)
        if start is None: return None
        fraction = (state.path_fraction or 0.0) if state.path_layer == current else 0.0
        if (state.anchor_layer == current and state.anchor_duration is not None and state.duration is not None
                and finish is not None and finish > start):
            observed = (state.duration - state.anchor_duration) * state.speed / (finish - start)
            fraction = max(fraction, min(1.0, max(0.0, observed)))
        now = start + ((finish - start) * fraction if finish is not None and finish >= start else 0)
        last = len(times) - 1
        while last > 0 and times[last] is None:
            last -= 1
        end = boundary(last)
        if end is None: return None
        if estimated_time and estimated_time > end:
            end = float(estimated_time)
        else:
            durations = [times[i] - times[i - 1] for i in range(1, last + 1)
                         if times[i] is not None and times[i - 1] is not None]
            if durations:
                end += sum(durations) / len(durations)
        remaining = max(0.0, end - now) / state.speed
        if state.eta_learn and state.drift:
            remaining *= state.drift
        return remaining

    def update_eta(self, snapshot, index):
        selected, current = self._cura.selected_layer, self._state.observed_layer
        text = ""
        if snapshot.active and selected is not None and current is not None:
            prefix = f"Selected layer {selected + 1} — "
            if selected < current: text = prefix + "already printed"
            elif selected == current: text = prefix + "current print layer"
            elif selected > current:
                remaining = self.remaining(selected, index)
                if remaining is None: text = prefix + "ETA unavailable (no layer timing)"
                else:
                    finish = datetime.now().astimezone() + timedelta(seconds=remaining)
                    clock = finish.strftime("%a %H:%M" if remaining >= 20 * 3600 else "%H:%M")
                    text = prefix + f"in {self.format_duration(remaining)} · ~{clock}"
        self._state = replace(self._state, eta_text=text)

