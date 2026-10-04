"""CPU-only inference for Obico's single-class ONNX failure detector."""

from pathlib import Path
import sys
import threading
import time

from .DetectionMask import masked_image, region_path, box_intersects_regions
from .DetectionObservation import DetectionBox, ModelDetections, MAX_BOXES

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage


def foreign_runtime(runtime_directory: str) -> str | None:
    """The location of an already-imported onnxruntime that is not the
    pinned install, or None. Checked before any download and again at
    load: a foreign build cannot be swapped out from under its owner,
    so detection refuses rather than fighting it."""
    existing = sys.modules.get("onnxruntime")
    if existing is None:
        return None
    path = getattr(existing, "__file__", None)
    if not path:
        return "<already imported>"
    resolved = Path(path).resolve()
    if resolved.is_relative_to(Path(runtime_directory).resolve()):
        return None
    return str(resolved)


class LocalFailureModel:
    def __init__(self, session, run_options_factory=None):
        inputs = session.get_inputs()
        if len(inputs) != 1 or len(inputs[0].shape) != 4:
            raise ValueError("Unexpected failure model input")
        batch, channels, height, width = inputs[0].shape
        if batch != 1 or channels != 3 or not all(
            isinstance(side, int) and 1 <= side <= 1024 for side in (height, width)
        ):
            raise ValueError("Unsupported failure model dimensions")
        self._session = session
        self._run_options_factory = run_options_factory
        self._run_lock = threading.Lock()
        self._active_run = None
        self._input_name = inputs[0].name
        self._width = width
        self._height = height

    @classmethod
    def load(cls, path: str, runtime_directory: str):
        """Load the pinned runtime directory, then the model.

        ``runtime_directory`` is required and fail-closed: the pinned
        build is the only one this adapter may run, so a caller that
        omits it refuses instead of importing whatever onnxruntime the
        process happens to carry."""
        if not runtime_directory:
            raise RuntimeError("The pinned inference runtime directory is required")
        installed = Path(runtime_directory).resolve()
        if foreign_runtime(runtime_directory) is not None:
            raise RuntimeError("Another inference runtime is already loaded in Cura")
        if str(installed) not in sys.path:
            sys.path.insert(0, str(installed))
        import onnxruntime as ort

        if (not Path(ort.__file__).resolve().is_relative_to(installed)
                or ort.__version__ != "1.23.2"):
            raise RuntimeError("The pinned inference runtime did not load")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        return cls(ort.InferenceSession(path, sess_options=options,
                                        providers=["CPUExecutionProvider"]), ort.RunOptions)

    def score(self, image: QImage) -> float:
        return self.detect(image).score

    def cancel_current(self):
        with self._run_lock:
            if self._active_run is not None:
                self._active_run.terminate = True

    def detect(self, image: QImage, regions=()) -> ModelDetections:
        import numpy as np

        deadline = time.monotonic() + 5
        if image.isNull():
            raise ValueError("Cannot analyse an empty camera frame")
        rgb = masked_image(image, regions).convertToFormat(QImage.Format.Format_RGB888).scaled(
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
        options = self._run_options_factory() if self._run_options_factory else None
        timer = None
        with self._run_lock:
            self._active_run = options
        if options is not None:
            timer = threading.Timer(5.0, lambda: setattr(options, "terminate", True))
            timer.daemon = True
            timer.start()
        try:
            arguments = (None, {self._input_name: tensor})
            outputs = self._session.run(*arguments, options) if options is not None else self._session.run(*arguments)
            if options is not None and options.terminate:
                raise TimeoutError("Local inference was cancelled or exceeded five seconds")
        finally:
            if timer is not None:
                timer.cancel()
            with self._run_lock:
                self._active_run = None
        if len(outputs) != 2:
            raise ValueError("Unexpected failure model outputs")
        boxes = np.asarray(outputs[0])
        confidences = np.asarray(outputs[1])
        if (confidences.ndim != 3 or confidences.shape[0] != 1 or confidences.shape[2] != 1 or confidences.shape[1] > 32768
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
        coords = np.clip(coords, 0, 1)
        valid = (coords[:, 2] > coords[:, 0]) & (coords[:, 3] > coords[:, 1])
        if regions:
            path = region_path(regions)
            valid &= np.array([box_intersects_regions((x1, y1, x2 - x1, y2 - y1), path)
                               for x1, y1, x2, y2 in coords], dtype=bool)
        coords, scores = coords[valid], scores[valid]
        retained = []
        areas = (coords[:, 2] - coords[:, 0]) * (coords[:, 3] - coords[:, 1])
        order = scores.argsort()[::-1]
        total = 0.0
        while order.size:
            if time.monotonic() > deadline:
                raise TimeoutError("Local inference exceeded five seconds")
            first, rest = order[0], order[1:]
            total += float(scores[first])
            if len(retained) < MAX_BOXES:
                x1, y1, x2, y2 = (float(value) for value in coords[first])
                retained.append(DetectionBox(x1, y1, x2 - x1, y2 - y1, float(scores[first])))
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
        return ModelDetections(total, tuple(retained))
