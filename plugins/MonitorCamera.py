"""Webcam selection/transform policy with an explicit configuration capability."""
from dataclasses import replace
from urllib.parse import urljoin
from PyQt6.QtCore import QObject, pyqtSignal


class MonitorCamera(QObject):
    changed = pyqtSignal()

    def __init__(self, data, config, apply_config, parent=None):
        super().__init__(parent)
        self._data, self._config, self._apply_config = data, config, apply_config
        self._key = None
        self._index = -1
        self._values = {}
        self._url = ""
        data.changed.connect(self.observe)
        self.observe()

    @staticmethod
    def identity(camera, index=0):
        return str(camera.get("uid") or camera.get("id") or camera.get("name") or f"camera-{index}").strip()

    @property
    def values(self): return dict(self._values)
    @property
    def url(self): return self._url

    def observe(self):
        config = self._config()
        cameras = self._data.snapshot.webcams
        key = (id(cameras), config.camera_selected, config.camera_url, config.camera_rotation, config.camera_mirror, config.url, self._data.active)
        if key == self._key: return
        self._key = key
        remembered = next((i for i, camera in enumerate(cameras) if self.identity(camera, i) == config.camera_selected), None)
        self._index = remembered if remembered is not None else min(max(0, self._index), len(cameras) - 1)
        camera = cameras[self._index] if self._index >= 0 else {}
        stream = str(camera.get("stream_url") or config.camera_url or "")
        self._url = urljoin(config.url.rstrip("/") + "/", stream) if stream and self._data.active else ""
        try: rotation = int(camera.get("rotation", config.camera_rotation) or 0)
        except (TypeError, ValueError): rotation = 0
        self._values = {
            "webcamNames": [str(item.get("name") or f"Camera {i + 1}") for i, item in enumerate(cameras)],
            "activeWebcamIndex": self._index,
            "cameraName": str(camera.get("name") or ("Configured camera" if self._url else "")),
            "cameraRotation": rotation if rotation in {0, 90, 180, 270} else 0,
            "cameraFlipHorizontal": bool(camera.get("flip_horizontal", config.camera_mirror)),
            "cameraFlipVertical": bool(camera.get("flip_vertical", False)),
        }
        self.changed.emit()

    def select(self, index):
        cameras = self._data.snapshot.webcams
        if not 0 <= index < len(cameras): return
        config = replace(self._config(), camera_selected=self.identity(cameras[index], index))
        self._apply_config(config)
        self._key = None
        self.observe()

