from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PreviewExpectation:
    layer: Optional[int] = None
    minimum_layer: Optional[int] = None
    path: Optional[float] = None
    minimum_path: Optional[int] = None


class PreviewController:
    """Own the Preview position last written by the follower."""

    def __init__(self) -> None:
        self.expected = PreviewExpectation()

    def clear(self) -> None:
        self.expected = PreviewExpectation()

    def remember(self, view) -> PreviewExpectation:
        def read(name, cast):
            getter = getattr(view, name, None)
            if not callable(getter):
                return None
            try:
                return cast(getter())
            except Exception:
                return None
        self.expected = PreviewExpectation(
            layer=read("getCurrentLayer", int),
            minimum_layer=read("getMinimumLayer", int),
            path=read("getCurrentPath", float),
            minimum_path=read("getMinimumPath", int),
        )
        return self.expected
