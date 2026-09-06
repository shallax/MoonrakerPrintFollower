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


class PreviewFollowerService:
    """Authoritative owner of Preview attachment and follower-written position."""

    def __init__(self) -> None:
        self.expected = PreviewExpectation()
        self.following_paused = False

    def clear(self) -> None:
        self.expected = PreviewExpectation()

    def set_paused(self, paused: bool) -> None:
        self.following_paused = bool(paused)

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
