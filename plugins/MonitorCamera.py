"""Webcam selection/transform policy with an explicit configuration capability."""
from dataclasses import replace
from urllib.parse import urljoin
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class MonitorCamera(QObject):
    changed = pyqtSignal()

    def __init__(self, data, config, apply_config, parent=None):
        super().__init__(parent)
        self._data, self._config, self._apply_config = data, config, apply_config
        self._key = None
        self._camera_signature = None
        self._restore_pending = False
        self._index = -1
        self._values = {}
        self._url = ""
        data.changed.connect(self.observe)
        self.observe()

    @staticmethod
    def identity(camera, index=0):
        return str(camera.get("uid") or camera.get("id") or camera.get("name") or f"camera-{index}").strip()

    @staticmethod
    def identity_aliases(camera, index=0):
        """Return every stable identifier we can recognise for a webcam.

        New selections are persisted by Moonraker UID when available, but older
        configurations and some Moonraker/front-end integrations may have stored
        a friendly name or an alternate id. Accept those on restore so an upgrade
        does not silently fall back to the first camera.
        """
        values = (camera.get("uid"), camera.get("id"), camera.get("name"), f"camera-{index}")
        return {str(value).strip() for value in values if value is not None and str(value).strip()}

    @staticmethod
    def camera_signature(cameras):
        """Stable projection of list content so unrelated monitor polls do nothing."""
        return tuple(
            (
                MonitorCamera.identity(camera, index),
                str(camera.get("name") or ""),
                str(camera.get("stream_url") or ""),
                str(camera.get("rotation") or ""),
                bool(camera.get("flip_horizontal", False)),
                bool(camera.get("flip_vertical", False)),
            )
            for index, camera in enumerate(cameras)
        )

    @property
    def values(self): return dict(self._values)
    @property
    def url(self): return self._url

    def observe(self):
        config = self._config()
        cameras = self._data.snapshot.webcams
        signature = self.camera_signature(cameras)

        if signature != self._camera_signature:
            self._camera_signature = signature
            self._key = None
            if cameras:
                # First publish only the model. Qt ComboBox can otherwise try to
                # apply a restored currentIndex while its delegate model is still
                # empty, leaving the control at the wrong item. Restore selection
                # on the next Qt turn, after QML has consumed webcamNames.
                self._restore_pending = True
                self._index = -1
                values = dict(self._values)
                values["webcamNames"] = [str(item.get("name") or f"Camera {i + 1}") for i, item in enumerate(cameras)]
                values["activeWebcamIndex"] = -1
                self._values = values
                self.changed.emit()
                QTimer.singleShot(0, self._restore_after_population)
                return
            self._restore_pending = False

        if self._restore_pending:
            return
        self._restore_selection(config, cameras)

    def _restore_after_population(self):
        if not self._restore_pending:
            return
        self._restore_pending = False
        self._key = None
        self._restore_selection(self._config(), self._data.snapshot.webcams)

    def _restore_selection(self, config, cameras):
        key = (
            self._camera_signature,
            config.camera_selected,
            config.camera_url,
            config.camera_rotation,
            config.camera_mirror,
            config.url,
            self._data.active,
        )
        if key == self._key:
            return
        self._key = key

        selected = str(config.camera_selected or "").strip()
        remembered = next((i for i, camera in enumerate(cameras) if selected and selected in self.identity_aliases(camera, i)), None)
        if remembered is not None:
            self._index = remembered
        elif cameras:
            self._index = min(max(0, self._index), len(cameras) - 1)
        else:
            self._index = -1

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
        self._restore_pending = False
        self._index = index
        selected = self.identity(cameras[index], index)
        config = replace(self._config(), camera_selected=selected)
        # apply_config is synchronous; PrinterBinding writes camera-only changes
        # directly to the active machine's settings entry and flushes preferences
        # before this method proceeds.
        self._apply_config(config)
        self._key = None
        self._restore_selection(self._config(), cameras)

