from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .Core import preview_override_kind
from .CuraAdapter import apply_preview_decision


@dataclass
class PreviewExpectation:
    layer: Optional[int] = None
    minimum_layer: Optional[int] = None
    path: Optional[float] = None
    minimum_path: Optional[int] = None


@dataclass
class PreviewTrackingState:
    """Path/ETA state that may be reset when the active G-code index changes."""

    path_layer: Optional[int] = None
    path_fraction: Optional[float] = None
    resolved_remote_layer: Optional[int] = None
    selected_layer_eta_text: str = ""
    speed_factor: float = 1.0
    eta_anchor_layer: Optional[int] = None
    eta_anchor_print_duration: Optional[float] = None
    eta_current_print_duration: Optional[float] = None


@dataclass
class PreviewRuntimeState:
    """Print-local Preview state that survives ordinary index invalidation."""

    observed_remote_layer: Optional[int] = None
    last_extruder_position: Optional[float] = None
    preview_switched_for_job: bool = False
    toolhead_path_valid: bool = False


class PreviewFollowerService:
    """Authoritative owner of Preview attachment, expectations and live state."""

    def __init__(self) -> None:
        self.expected = PreviewExpectation()
        self.tracking = PreviewTrackingState()
        self.runtime = PreviewRuntimeState()
        self.following_paused = False

    def clear(self) -> None:
        self.expected = PreviewExpectation()

    def reset_tracking(self) -> None:
        """Reset index-derived path/ETA state without inventing a new print run."""
        self.tracking = PreviewTrackingState()
        self.runtime.toolhead_path_valid = False

    def reset_print_state(self) -> None:
        """Reset all print-local Preview state for a new/inactive print binding."""
        self.tracking = PreviewTrackingState()
        self.runtime = PreviewRuntimeState()

    def set_paused(self, paused: bool) -> None:
        self.following_paused = bool(paused)

    def begin_path_layer(self, layer: int) -> None:
        layer = int(layer)
        if self.tracking.path_layer == layer:
            return
        self.tracking.path_layer = layer
        self.tracking.path_fraction = None

    def update_path_fraction(self, fraction: float) -> float:
        fraction = max(0.0, min(1.0, float(fraction)))
        current = self.tracking.path_fraction
        if current is not None:
            fraction = max(float(current), fraction)
        self.tracking.path_fraction = fraction
        return fraction

    def observe_remote_layer(self, layer: int) -> None:
        layer = max(0, int(layer))
        self.runtime.observed_remote_layer = layer
        self.observe_eta_layer(layer)

    def observe_eta_layer(self, layer: int) -> None:
        layer = int(layer)
        if self.tracking.eta_anchor_layer == layer:
            return
        self.tracking.eta_anchor_layer = layer
        self.tracking.eta_anchor_print_duration = self.tracking.eta_current_print_duration

    def set_selected_layer_eta_text(self, text: str) -> bool:
        text = str(text or "")
        if text == self.tracking.selected_layer_eta_text:
            return False
        self.tracking.selected_layer_eta_text = text
        return True

    def remember(self, view) -> None:
        try:
            self.expected.layer = int(view.getCurrentLayer())
        except Exception:
            self.expected.layer = None
        try:
            self.expected.minimum_layer = (
                int(view.getMinimumLayer()) if hasattr(view, "getMinimumLayer") else None
            )
        except Exception:
            self.expected.minimum_layer = None
        try:
            self.expected.path = (
                float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else None
            )
        except Exception:
            self.expected.path = None
        try:
            self.expected.minimum_path = (
                int(view.getMinimumPath()) if hasattr(view, "getMinimumPath") else None
            )
        except Exception:
            self.expected.minimum_path = None

    def classify_manual_override(self, view) -> Optional[str]:
        if self.expected.layer is None:
            return None
        try:
            current_layer = int(view.getCurrentLayer())
        except Exception:
            return None
        try:
            current_minimum_layer = int(view.getMinimumLayer()) if hasattr(view, "getMinimumLayer") else None
        except Exception:
            current_minimum_layer = None
        try:
            current_path = float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else None
        except Exception:
            current_path = None
        try:
            current_minimum_path = int(view.getMinimumPath()) if hasattr(view, "getMinimumPath") else None
        except Exception:
            current_minimum_path = None
        return preview_override_kind(
            expected_layer=self.expected.layer,
            current_layer=current_layer,
            expected_minimum_layer=self.expected.minimum_layer,
            current_minimum_layer=current_minimum_layer,
            expected_path=self.expected.path,
            current_path=current_path,
            expected_minimum_path=self.expected.minimum_path,
            current_minimum_path=current_minimum_path,
        )

    @staticmethod
    def apply_layer_decision(view, current_layer: int, minimum_layer: Optional[int]) -> None:
        apply_preview_decision(view, int(current_layer), minimum_layer)
