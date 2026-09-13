"""Webcam selection/transform policy with an explicit configuration capability."""
from dataclasses import replace
from urllib.parse import urljoin
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QHostAddress

from UM.Logger import Logger

from .CameraBridge import CameraBridge


class MonitorCamera(QObject):
    changed = pyqtSignal()
    # The webcam watchdog's signals, forwarded from the bridge: a dead
    # stream and its restart. Direct (non-bridge) URLs never emit
    # these — the plugin cannot see a raw stream's health.
    streamFailed = pyqtSignal()
    streamRecovered = pyqtSignal()

    def __init__(self, data, config, apply_config, parent=None):
        super().__init__(parent)
        self._data, self._config, self._apply_config = data, config, apply_config
        self._key = None
        self._camera_signature = None
        self._restore_pending = False
        self._index = -1
        self._values = {}
        self._url = ""
        self._last_url_logged = ""
        self._camera_bridge = None
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
                QTimer.singleShot(0, lambda: self._restore_after_population(signature))
                return
            self._restore_pending = False

        if self._restore_pending:
            return
        self._restore_selection(config, cameras)

    def _restore_after_population(self, scheduled_signature=None):
        if not self._restore_pending:
            return
        if scheduled_signature is not None and scheduled_signature != self._camera_signature:
            # The webcam set changed (or the session was invalidated) between
            # scheduling and firing. Keep the restore pending; the next
            # observe() re-schedules against the current snapshot.
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
        # Absolute stream URLs may only be http/https, and
        # protocol-relative inputs ("//host/...") are rejected
        # outright: a hostile Moonraker listing could otherwise point
        # Cura's image loader at an arbitrary host (panel security
        # P3). Plain relative paths — the normal webcam case — pass.
        if stream:
            # QUrl strips surrounding whitespace, so the guard runs
            # on the stripped form too: " //evil.example/x" would
            # otherwise resolve to a real external host (the
            # adversarial round's catch).
            parsed = QUrl(stream.strip())
            if stream.strip().startswith("//") or (parsed.isValid() and parsed.scheme()
                                                   and parsed.scheme().lower() not in ("http", "https")):
                stream = ""
        self._url = urljoin(config.url.rstrip("/") + "/", stream) if stream and self._data.active else ""
        self._url = self._bridge_url(config, self._url)
        if self._url != self._last_url_logged:
            # The first-load failures were invisible in the logs: the
            # stream decision (direct vs bridged vs none) logs here so
            # a capture names where the loader went.
            self._last_url_logged = self._url
            if self._url:
                parsed = QUrl(self._url)
                shown = f"{parsed.scheme()}://{parsed.host()}" + (f":{parsed.port()}" if parsed.port() > 0 else "") + parsed.path()
                kind = "bridged" if self._camera_bridge is not None and parsed.host() in ("127.0.0.1", "localhost") else "direct"
                Logger.log("i", "Moonraker camera stream: %s (%s)", shown, kind)
            else:
                Logger.log("i", "Moonraker camera stream: none")
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

    @staticmethod
    def _remote_stream(url):
        parsed = QUrl(url)
        return parsed.scheme().lower() in ("http", "https") and not QHostAddress(parsed.host()).isLoopback()

    def _bridge_url(self, config, url):
        # A camera behind the header-auth proxy cannot render through
        # Cura's loader (NetworkMJPGImage sends no headers): republish
        # it on the keyless loopback bridge (the author's 4.0.0
        # ruling). The key travels with the bridge's own upstream
        # fetch; the loader sees a plain local URL.
        if not url or not config.api_key or not self._remote_stream(url):
            return url
        if self._camera_bridge is None:
            self._camera_bridge = CameraBridge(self)
            # The watchdog's feed-health signals ride the bridge.
            self._camera_bridge.upstreamFailed.connect(self.streamFailed.emit)
            self._camera_bridge.upstreamStarted.connect(self.streamRecovered.emit)
            Logger.log("i", "Moonraker camera bridge created for the key-carrying stream")
        # The upstream is the STREAM'S own origin: an absolute
        # stream_url on another host/port (a separate webcam box) must
        # not be re-homed onto the Moonraker base.
        parsed = QUrl(url)
        upstream = f"{parsed.scheme()}://{parsed.host()}"
        if parsed.port(0) > 0:
            upstream += f":{parsed.port(0)}"
        if not self._camera_bridge.configure(upstream, config.api_key):
            return url
        path = parsed.path() + (("?" + parsed.query()) if parsed.query() else "")
        return self._camera_bridge.local_url(path)

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

