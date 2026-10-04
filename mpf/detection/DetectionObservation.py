"""Immutable identities travel with the exact sampled frame and its detections."""

from dataclasses import dataclass

from PyQt6.QtGui import QImage


@dataclass(frozen=True)
class DetectionBox:
    x: float
    y: float
    width: float
    height: float
    confidence: float

    def as_dict(self):
        return {"x": self.x, "y": self.y, "width": self.width,
                "height": self.height, "confidence": self.confidence}


@dataclass(frozen=True)
class ModelDetections:
    score: float
    boxes: tuple[DetectionBox, ...] = ()


@dataclass(frozen=True)
class DetectionSample:
    image: QImage
    context: tuple
    captured_at: float
    regions: tuple = ()


@dataclass(frozen=True)
class DetectionResult:
    sample: DetectionSample
    detections: ModelDetections
    unavailable_reason: str = ""


FRESH_SECONDS = 30.0
MAX_BOXES = 50
