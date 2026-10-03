"""CPU-only inference for Obico's single-class ONNX failure detector."""

from pathlib import Path
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage


class LocalFailureModel:
    def __init__(self, session):
        inputs = session.get_inputs()
        if len(inputs) != 1 or len(inputs[0].shape) != 4:
            raise ValueError("Unexpected failure model input")
        batch, channels, height, width = inputs[0].shape
        if batch != 1 or channels != 3 or not all(
            isinstance(side, int) and 1 <= side <= 1024 for side in (height, width)
        ):
            raise ValueError("Unsupported failure model dimensions")
        self._session = session
        self._input_name = inputs[0].name
        self._width = width
        self._height = height

    @classmethod
    def load(cls, path: str, runtime_directory: str | None = None):
        if runtime_directory is not None:
            installed = Path(runtime_directory).resolve()
            existing = sys.modules.get("onnxruntime")
            if existing is not None and not Path(existing.__file__).resolve().is_relative_to(installed):
                raise RuntimeError("Another inference runtime is already loaded in Cura")
            if str(installed) not in sys.path:
                sys.path.insert(0, str(installed))
        import onnxruntime as ort

        if runtime_directory is not None:
            if (not Path(ort.__file__).resolve().is_relative_to(installed)
                    or ort.__version__ != "1.23.2"):
                raise RuntimeError("The pinned inference runtime did not load")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        return cls(ort.InferenceSession(path, sess_options=options,
                                        providers=["CPUExecutionProvider"]))

    def score(self, image: QImage) -> float:
        import numpy as np

        if image.isNull():
            raise ValueError("Cannot analyse an empty camera frame")
        rgb = image.convertToFormat(QImage.Format.Format_RGB888).scaled(
            self._width, self._height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        buffer = rgb.constBits()
        buffer.setsize(rgb.sizeInBytes())
        pixels = np.frombuffer(buffer, dtype=np.uint8)
        pixels = pixels.reshape(self._height, rgb.bytesPerLine())[:, :self._width * 3]
        tensor = np.ascontiguousarray(
            pixels.reshape(self._height, self._width, 3).transpose(2, 0, 1)[None],
            dtype=np.float32,
        ) / 255.0
        outputs = self._session.run(None, {self._input_name: tensor})
        if len(outputs) != 2:
            raise ValueError("Unexpected failure model outputs")
        boxes = np.asarray(outputs[0])
        confidences = np.asarray(outputs[1])
        if (confidences.ndim != 3 or confidences.shape[0] != 1 or confidences.shape[2] != 1
                or boxes.shape != (1, confidences.shape[1], 1, 4)):
            raise ValueError("Unexpected failure model output shapes")
        if not np.isfinite(confidences).all() or np.any((confidences < 0) | (confidences > 1)):
            raise ValueError("Invalid failure model confidence")
        scores = confidences[0, :, 0]
        selected = scores > .08
        coords = boxes[0, selected, 0]
        scores = scores[selected]
        if not np.isfinite(coords).all():
            raise ValueError("Invalid failure model coordinates")
        areas = (coords[:, 2] - coords[:, 0]) * (coords[:, 3] - coords[:, 1])
        order = scores.argsort()[::-1]
        total = 0.0
        while order.size:
            first, rest = order[0], order[1:]
            total += float(scores[first])
            intersection = (
                np.maximum(0, np.minimum(coords[first, 2], coords[rest, 2])
                           - np.maximum(coords[first, 0], coords[rest, 0]))
                * np.maximum(0, np.minimum(coords[first, 3], coords[rest, 3])
                             - np.maximum(coords[first, 1], coords[rest, 1]))
            )
            union = areas[first] + areas[rest] - intersection
            overlap = np.divide(intersection, union, out=np.zeros_like(intersection),
                                where=union > 0)
            order = rest[overlap <= .45]
        return total
