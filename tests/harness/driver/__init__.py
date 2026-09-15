"""Test-only observation driver for the real-Cura UI harness.

A loopback TCP listener served on Cura's GUI thread. Verbs are generic
(find / inspect / wait); input injection and screen capture are the
OUT-OF-PROCESS runner's job (XTEST + xwd/ffmpeg), so this plugin never
synthesizes events itself. Staged into the run's plugin directory at
test time only — it must never ship (see TESTING.md 2.1).
"""
from __future__ import annotations

import json
import os
import time

from PyQt6.QtCore import QEvent, QEventLoop, QObject, QPoint, QPointF, QTimer, Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QMouseEvent
from PyQt6.QtNetwork import QHostAddress, QTcpServer
from PyQt6.QtQml import QQmlComponent, qmlEngine
from PyQt6.QtQuick import QQuickItem, QQuickWindow
from PyQt6.QtWidgets import QApplication, QMessageBox
from UM.Application import Application

PORT_FILE = "/tmp/mpf/harness_port.txt"
TOKEN_FILE = "/tmp/mpf/harness_token.txt"


def _mint_token():
    """A per-run secret for the loopback channel: any same-UID process
    in the container could otherwise connect and exec arbitrary code
    inside Cura (the security panel's H1). The runner reads the 0600
    file and presents the token on every request."""
    try:
        import secrets
        token = secrets.token_hex(16)
        with open(TOKEN_FILE, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.chmod(TOKEN_FILE, 0o600)
        return token
    except Exception:
        return ""


class HarnessServer(QObject):
    _engine = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        self._pending = []  # (request_id, deadline, predicate, reply_builder)
        self._mounted_switch = None
        self._clicked_flag = False
        self._win_events = []
        self._py_clicks = []
        self._buffers = {}
        self._token = _mint_token()
        if not self._server.listen(QHostAddress.SpecialAddress.LocalHost, 0):
            return
        with open(PORT_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(self._server.serverPort()))
        os.chmod(PORT_FILE, 0o600)
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._drain)
        self._poll.start()

    @pyqtSlot()
    def markClicked(self):
        self._clicked_flag = True

    def eventFilter(self, obj, event):
        # Installed on the click-target window for the duration of a
        # qclick: proves whether the synthesized events arrive at the
        # window at all (QEvent delivery), the first fork in the
        # no-activation puzzle.
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseMove):
            name = str(event.type()).split(".")[-1]
            try:
                pos = (round(event.position().x()), round(event.position().y()))
            except Exception:
                pos = None
            self._win_events.append((name, pos))
        return False

    @pyqtSlot(str)
    def switchStage(self, stage_id):
        # The mounted fixture buttons' handler: the same call Cura's
        # own (non-rendering) stage buttons make.
        try:
            Application.getInstance().getController().setActiveStage(str(stage_id))
        except Exception:
            pass

    # The registry's post-register bookkeeping calls setVersion,
    # setMetaData and setPluginId on every returned object; a missing
    # one marks the driver "could not be loaded" and Cura shows the
    # corrupted-plugin card over the UI while the already-running
    # server keeps the suite passing. The server is only an
    # observation channel, so the bookkeeping is accepted and stored.
    def setVersion(self, *_args):
        pass

    def setMetaData(self, *_args):
        pass

    def setPluginId(self, plugin_id):
        self._plugin_id = plugin_id

    # The full Extension interface: the Extensions menu's model
    # (UM/Qt/Bindings/ExtensionModel.py) iterates every registered
    # extension at window construction and calls these — a missing
    # one aborts the main window's build, and Cura comes up with no
    # header and no stages while everything else keeps running.
    def getPluginId(self):
        return getattr(self, "_plugin_id", None)

    def getMenuName(self):
        return ""

    def getMenuItemList(self):
        return []

    # -- plumbing ----------------------------------------------------

    def _accept(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            self._buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._buffers.pop(s, None))

    def _read(self, sock):
        self._buffers[sock].extend(bytes(sock.readAll()))
        raw = bytes(self._buffers[sock])
        while b"\n" in raw:
            line, rest = raw.split(b"\n", 1)
            self._buffers[sock] = bytearray(rest)
            raw = rest
            try:
                request = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            reply = self._handle(request)
            if reply is not None:
                try:
                    sock.write(json.dumps(reply).encode("utf-8") + b"\n")
                except Exception:
                    pass

    def _drain(self):
        now = time.monotonic()
        kept = []
        for entry in self._pending:
            request_id, deadline, predicate, builder = entry
            if predicate():
                for sock in list(self._buffers):
                    try:
                        sock.write(json.dumps(builder()).encode("utf-8") + b"\n")
                    except Exception:
                        pass
            elif now < deadline:
                kept.append(entry)
            else:
                for sock in list(self._buffers):
                    try:
                        sock.write(json.dumps({"id": request_id, "ok": False, "timeout": True}).encode("utf-8") + b"\n")
                    except Exception:
                        pass
        self._pending = kept

    # -- observation --------------------------------------------------

    @staticmethod
    def _stage():
        try:
            stage = Application.getInstance().getController().getActiveStage()
            return stage.getId() if stage is not None else ""
        except Exception:
            return ""

    @staticmethod
    def _windows(visible_only=False):
        windows = []
        for window in QGuiApplication.topLevelWindows():
            if isinstance(window, QQuickWindow) and (not visible_only or window.isVisible()):
                windows.append(window)
        return windows

    @staticmethod
    def _items(window, needle=""):
        found = []
        for item in window.contentItem().findChildren(QQuickItem):
            name = item.objectName() or ""
            klass = item.metaObject().className()
            try:
                text = item.property("text")
            except Exception:
                text = None
            text = text if isinstance(text, str) else ""
            if needle and needle not in name and needle not in klass and needle not in text:
                continue
            found.append(item)
        return found

    @staticmethod
    def _rect(item):
        # mapToScene is window-relative and reliable; add the window's
        # own position for screen coordinates (mapToGlobal proved
        # desynced under a WM-less Xvfb after an X-level move).
        try:
            scene = item.mapToScene(QPointF(0, 0))
            window = item.window()
            origin = window.position() if window is not None else None
        except Exception:
            scene, origin = None, None
        if scene is None or origin is None:
            top_left = item.mapToGlobal(QPointF(0, 0))
            return {"x": round(top_left.x()), "y": round(top_left.y()),
                    "w": round(item.width()), "h": round(item.height())}
        return {"x": round(scene.x() + origin.x()), "y": round(scene.y() + origin.y()),
                "w": round(item.width()), "h": round(item.height())}

    def _handle(self, request):
        request_id = request.get("id")
        cmd = request.get("cmd")
        windows = self._windows()
        # Under a WM-less Xvfb, QWindow.isVisible can report False while
        # the window is genuinely on screen: the main window is admitted
        # by title too, and the truly hidden popups are excluded by
        # title as well (their items were polluting the dumps).
        # The main window is the large one; popup windows inherit the
        # app title and would pollute every dump with their hidden
        # content (the welcome's buttons shadowed the real toolbar).
        visible_windows = [w for w in windows
                           if w.isVisible() and w.width() >= 1000]
        if cmd == "hello":
            try:
                from cura.CuraApplication import CuraApplication
                registry = CuraApplication.getInstance().getPluginRegistry()
                registry.getPluginObject("HarnessDriver")
                registered = True
            except Exception:
                registered = False
            return {"id": request_id, "ok": True, "pid": os.getpid(),
                    "platform": QGuiApplication.platformName(),
                    "stage": self._stage(), "windows": len(windows),
                    "registered": registered}
        if cmd == "stage":
            return {"id": request_id, "ok": True, "stage": self._stage()}
        if cmd == "wait_stage":
            wanted = str(request.get("stage") or "")
            deadline = time.monotonic() + float(request.get("timeout_ms", 15000)) / 1000.0
            self._pending.append((request_id, deadline, lambda w=wanted: self._stage() == w,
                                  lambda w=wanted: {"id": request_id, "ok": True, "stage": w}))
            return None
        if cmd == "window":
            if not windows:
                return {"id": request_id, "ok": False, "error": "no QQuickWindow"}
            geometry = windows[0].geometry()
            return {"id": request_id, "ok": True,
                    "x": geometry.x(), "y": geometry.y(),
                    "w": geometry.width(), "h": geometry.height()}
        if cmd == "window_pin":
            # The boot gate pins the editor window's geometry (Cura's
            # first-boot size is nondeterministic, and a narrow window
            # collapses the stage header into an overflow menu). The
            # editor is the window that owns the stage header; until
            # the header appears, the largest window is resized anyway
            # — harmless on the closing splash, and on the editor it
            # expands the collapsed header, which then exposes the
            # stageId delegates. Cura re-applies its own size during
            # init, so the pin re-applies.
            try:
                from PyQt6.QtQuick import QQuickWindow
                w = int(request.get("w", 1500))
                h = int(request.get("h", 900))
                deadline = time.monotonic() + 40.0
                target = [None]
                loop = QEventLoop()

                def search():
                    if time.monotonic() > deadline:
                        loop.quit()
                        return
                    for window in QGuiApplication.topLevelWindows():
                        if not isinstance(window, QQuickWindow):
                            continue
                        try:
                            content = window.contentItem()
                        except Exception:
                            continue
                        found = False
                        for item in _walk(content, depth=48):
                            try:
                                if item.property("stageId"):
                                    found = True
                                    break
                            except Exception:
                                pass
                        if found:
                            target[0] = window
                            break
                    if target[0] is None:
                        # The stageId may simply not exist yet: pin the
                        # largest window so the editor's collapsed
                        # header expands when its turn comes.
                        largest = None
                        largest_area = 0
                        for window in QGuiApplication.topLevelWindows():
                            if isinstance(window, QQuickWindow):
                                area = window.width() * window.height()
                                if area > largest_area:
                                    largest_area = area
                                    largest = window
                        if largest is not None and (
                                largest.width() != w or largest.height() != h):
                            largest.resize(w, h)
                        QTimer.singleShot(1000, search)
                        return
                    window = target[0]
                    window.resize(w, h)
                    attempts = [0]

                    def verify():
                        # The resize is async to the event loop, and
                        # the pin must report the size the window
                        # ACTUALLY took — the old immediate read
                        # returned the pre-resize size on slow boots
                        # (the parallel matrix's gate failures).
                        if window.width() == w and window.height() == h:
                            loop.quit()
                            return
                        if attempts[0] >= 30:
                            # Give up and report the truth — the
                            # runner's size check fails honestly.
                            loop.quit()
                            return
                        attempts[0] += 1
                        window.resize(w, h)
                        QTimer.singleShot(500, verify)

                    QTimer.singleShot(1500, verify)
                    loop.exec()

                QTimer.singleShot(500, search)
                loop.exec()
                window = target[0]
                if window is None:
                    return {"id": request_id, "ok": False,
                            "error": "no window with a stage header appeared"}
                try:
                    _geom = QGuiApplication.primaryScreen().geometry()
                    _screen = [_geom.width(), _geom.height()]
                except Exception:
                    _screen = None
                return {"id": request_id, "ok": True,
                        "title": window.title() or "",
                        "size": (window.width(), window.height()),
                        "screen": _screen}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "windows":
            rows = []
            for window in windows:
                geometry = window.geometry()
                rows.append({"x": geometry.x(), "y": geometry.y(),
                             "w": geometry.width(), "h": geometry.height(),
                             "visible": bool(window.isVisible()),
                             "title": window.title() or ""})
            return {"id": request_id, "ok": True, "windows": rows}
        if cmd == "find":
            for window in visible_windows:
                for item in self._items(window):
                    if item.objectName() == request.get("name"):
                        rect = self._rect(item)
                        rect.update({"ok": True, "id": request_id,
                                     "visible": bool(item.isVisible()),
                                     "class": item.metaObject().className()})
                        return rect
            return {"id": request_id, "ok": False, "error": "not found"}
        if cmd == "list":
            rows = []
            for window in visible_windows:
                for item in self._items(window, str(request.get("needle") or "")):
                    rect = self._rect(item)
                    rows.append({"name": item.objectName() or "",
                                 "class": item.metaObject().className(),
                                 "visible": bool(item.isVisible()),
                                 "x": rect["x"], "y": rect["y"],
                                 "w": rect["w"], "h": rect["h"]})
            rows.sort(key=lambda row: (row["x"], row["y"]))
            return {"id": request_id, "ok": True, "items": rows[:60]}
        if cmd == "visible":
            # The childItems walk, like every lookup: findChildren on
            # the QObject graph misses popup/overlay content entirely
            # (it dumped only the overlay while the walk saw the FM).
            rows = []
            for window in _lookup_windows():
                for item in _walk(window.contentItem(), depth=64):
                    # Geometry + the effective-visibility walk: Qt's
                    # isVisible() lies for deep repeater content and
                    # was filtering the whole dump empty.
                    if item.width() < 8 or item.height() < 8:
                        continue
                    if not _effectively_visible(item):
                        continue
                    try:
                        text = item.property("text")
                    except Exception:
                        text = None
                    text = text if isinstance(text, str) else ""
                    rect = self._rect(item)
                    rows.append({"class": item.metaObject().className(),
                                 "name": item.objectName() or "",
                                 "text": text[:40], "visible": True,
                                 "x": rect["x"], "y": rect["y"],
                                 "w": rect["w"], "h": rect["h"]})
            rows.sort(key=lambda row: (row["y"], row["x"]))
            return {"id": request_id, "ok": True, "items": rows[:2000]}
        if cmd == "resize":
            # Programmatic window geometry — the min/max layout
            # exercise (no WM under Xvfb, so the app resizes itself).
            # The reply reports the size the window actually took,
            # after a reapply loop: a single setGeometry can be
            # reverted by a late boot-pin reapply (the stale-read
            # class window_pin was rewritten to fix), so the verb
            # keeps applying until the read-back holds.
            try:
                window = _main_window()
                if window is None:
                    return {"id": request_id, "ok": False, "error": "no window"}
                _win = os.environ.get("HARNESS_WINDOW", "1840x1040").split("x")
                want = [int(request.get("w", int(_win[0]))),
                        int(request.get("h", int(_win[1])))]
                got = [0, 0]
                for _attempt in range(5):
                    window.setGeometry(0, 0, want[0], want[1])
                    time.sleep(0.8)
                    got = [window.width(), window.height()]
                    if got == want:
                        break
                return {"id": request_id, "ok": True, "size": got,
                        "wanted": want}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "quit":
            try:
                Application.getInstance().closeApplication()
                return {"id": request_id, "ok": True}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "header_tree":
            import collections
            window = _main_window()
            if window is not None:
                for item in _walk(window.contentItem()):
                    if "MainWindowHeader" in item.metaObject().className():
                        hist = collections.Counter()
                        texts = []
                        for child in _walk(item):
                            hist[child.metaObject().className()] += 1
                            try:
                                t = child.property("text")
                            except Exception:
                                t = None
                            if isinstance(t, str) and t:
                                texts.append(t[:30])
                        rect = self._rect(item)
                        return {"id": request_id, "ok": True,
                                "rect": rect, "vis": bool(item.isVisible()),
                                "classes": dict(hist), "texts": texts[:12]}
            return {"id": request_id, "ok": False, "error": "no MainWindowHeader"}
        if cmd == "deep_children":
            rows = []
            for window in visible_windows:
                for item in window.contentItem().childItems():
                    entry = {"class": item.metaObject().className(),
                             "w": round(item.width()), "h": round(item.height()),
                             "children": []}
                    for child in item.childItems():
                        entry["children"].append({"class": child.metaObject().className(),
                                                  "x": round(child.x()), "y": round(child.y()),
                                                  "w": round(child.width()), "h": round(child.height()),
                                                  "vis": bool(child.isVisible())})
                    rows.append(entry)
            return {"id": request_id, "ok": True, "items": rows[:12]}
        if cmd == "set_stage":
            # Environment workaround, NOT a test mechanism: under this
            # Xvfb Cura's stage-switch header never instantiates (the
            # stages register, the buttons don't), so the harness's
            # Phase-A gallery drives the stage change through the
            # controller — the same call the missing buttons make —
            # and the captures show the real UI follow. The gate
            # scenarios still click real buttons; this verb exists
            # only until the header renders.
            wanted = str(request.get("stage") or "")
            try:
                controller = Application.getInstance().getController()
                before = str(controller.getActiveStage().getId())
                controller.setActiveStage(wanted)
                after = str(controller.getActiveStage().getId())
                return {"id": request_id, "ok": before != after or before == wanted,
                        "before": before, "after": after}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "stages":
            try:
                controller = Application.getInstance().getController()
                methods = [name for name in ("getStages", "getAllStages", "getRegisteredStages")
                           if hasattr(controller, name)]
                for name in methods:
                    stages = getattr(controller, name)()
                    ids = [stage.getId() if hasattr(stage, "getId") else str(stage) for stage in stages]
                    return {"id": request_id, "ok": True, "via": name, "stages": ids}
                return {"id": request_id, "ok": False, "error": "no stage list API"}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "qml_warnings":
            return {"id": request_id, "ok": True, "warnings": QML_WARNINGS[-20:]}
        if cmd == "qtest_state":
            result = _import_qtest()
            import sys
            return {"id": request_id, "ok": bool(result), "error": QT_TEST_ERROR,
                    "sys_path_has_wheel": any("qt6wheel" in p for p in sys.path)}
        if cmd == "qclick":
            # QTest choreography click at window-relative coordinates,
            # targeting the window that actually owns the mounted row
            # (visible_windows[0] is registration order, not the row's
            # window). An event filter on that window proves the
            # synthesized press/release arrive; button pointer state
            # and the clicked flag are sampled right after.
            qtest = _import_qtest()
            if not qtest:
                return {"id": request_id, "ok": False, "error": "QtTest injection unavailable"}
            try:
                x = int(request.get("x", 0))
                y = int(request.get("y", 0))
                stage_target = str(request.get("stage") or "")
                if stage_target:
                    # Click CURA'S OWN header stage button: find the
                    # delegate by its stageId and aim at its center in
                    # window (scene) coordinates.
                    window = _main_window()
                    target = None
                    if window is not None:
                        for item in _walk(window.contentItem()):
                            try:
                                stage_id = item.property("stageId")
                            except Exception:
                                continue
                            if stage_id == stage_target:
                                target = item
                                break
                    if target is None:
                        return {"id": request_id, "ok": False, "error": "stage button not found",
                                "stage": stage_target}
                    scene = target.mapToScene(QPointF(0, 0))
                    x = round(scene.x() + target.width() / 2)
                    y = round(scene.y() + target.height() / 2)
                    label = target.property("text")
                else:
                    row = getattr(self, "_mounted_switch", None)
                    window = row.window() if row is not None else (visible_windows[0] if visible_windows else None)
                    label = "Preview"
                if window is None:
                    return {"id": request_id, "ok": False, "error": "no window"}
                self._win_events = []
                window.installEventFilter(self)
                qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier, QPoint(x, y))
                qtest.QTest.qWait(60)
                pressed_after = _sample_button_state(getattr(self, "_mounted_switch", None), str(label))
                qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton,
                                         Qt.KeyboardModifier.NoModifier, QPoint(x, y))
                qtest.QTest.qWait(60)
                window.removeEventFilter(self)
                released_after = _sample_button_state(getattr(self, "_mounted_switch", None), str(label))
                stage = None
                try:
                    stage = Application.getInstance().getController().getActiveStage().getId()
                except Exception:
                    pass
                try:
                    hit = window.contentItem().childAt(x, y)
                    hit_name = hit.metaObject().className() if hit is not None else None
                except Exception:
                    hit_name = None
                return {"id": request_id, "ok": True, "aim": [x, y], "label": str(label),
                        "hit": hit_name,
                        "window": window.objectName() or "",
                        "size": (window.width(), window.height()),
                        "events": list(self._win_events),
                        "pressed_after": pressed_after, "released_after": released_after,
                        "clicked": bool(self._clicked_flag), "stage": stage}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "hit_test":
            try:
                x = float(request.get("x", 0))
                y = float(request.get("y", 0))
                for window in visible_windows:
                    hit = window.contentItem().childAt(x, y)
                    if hit is None:
                        return {"id": request_id, "ok": True, "hit": None}
                    try:
                        label = hit.property("text")
                    except Exception:
                        label = None
                    return {"id": request_id, "ok": True,
                            "hit": {"class": hit.metaObject().className(),
                                    "text": str(label)[:20],
                                    "w": round(hit.width()), "h": round(hit.height())}}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "inject_click":
            # Direct DeliveryAgent injection: QQuickWindow.sendEvent is
            # Qt's sanctioned programmatic path (its own autotests use
            # it). It drives the same delivery machinery a real click
            # uses but skips window-system synthesis entirely — the
            # second fork of the no-activation puzzle.
            try:
                row = getattr(self, "_mounted_switch", None)
                if row is None:
                    return {"id": request_id, "ok": False, "error": "nothing mounted"}
                window = row.window()
                if not hasattr(window, "sendEvent"):
                    return {"id": request_id, "ok": False, "error": "QQuickWindow.sendEvent unavailable"}
                label = request.get("text", "Preview")
                target = None
                for child in row.childItems():
                    try:
                        if child.property("text") == label:
                            target = child
                    except Exception:
                        pass
                if target is None:
                    return {"id": request_id, "ok": False, "error": "button not found"}
                local = QPointF(10, 10)
                scene = target.mapToScene(local)
                window.sendEvent(target, QMouseEvent(QEvent.Type.MouseButtonPress, local, scene, scene,
                                                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                                     Qt.KeyboardModifier.NoModifier))
                pressed = _sample_button_state(row, label)
                window.sendEvent(target, QMouseEvent(QEvent.Type.MouseButtonRelease, local, scene, scene,
                                                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                                     Qt.KeyboardModifier.NoModifier))
                released = _sample_button_state(row, label)
                stage = None
                try:
                    stage = Application.getInstance().getController().getActiveStage().getId()
                except Exception:
                    pass
                return {"id": request_id, "ok": True, "pressed": pressed, "released": released,
                        "clicked": bool(self._clicked_flag), "stage": stage}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "arm_py_click":
            # Python-side connection to each fixture button's clicked
            # signal: distinguishes "clicked never emitted" from "the
            # QML-side handler never invoked" (PyQt only exposes
            # @pyqtSlot methods to QML).
            try:
                row = getattr(self, "_mounted_switch", None)
                if row is None:
                    return {"id": request_id, "ok": False, "error": "nothing mounted"}
                self._py_clicks = []
                armed = []
                for child in row.childItems():
                    try:
                        label = child.property("text")
                    except Exception:
                        label = None
                    if label not in ("Prepare", "Preview", "Monitor"):
                        continue
                    child.clicked.connect(lambda checked=False, lab=label: self._py_clicks.append(lab))
                    armed.append(str(label))
                return {"id": request_id, "ok": True, "armed": armed}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "row_scan":
            # Live anatomy of the mounted fixture row: own rect, each
            # direct child's scene rect, and what a hit-test sweep
            # actually returns across the row. The mount report's
            # rects were right at mount+800ms but hits miss the
            # buttons — this shows where everything is NOW.
            try:
                item = getattr(self, "_mounted_switch", None)
                if item is None:
                    return {"id": request_id, "ok": False, "error": "nothing mounted"}
                window = item.window()
                origin = item.mapToScene(QPointF(0, 0))
                children = []
                for child in item.childItems():
                    try:
                        label = child.property("text")
                    except Exception:
                        label = ""
                    top_left = child.mapToScene(QPointF(0, 0))
                    children.append({"class": child.metaObject().className(),
                                     "text": str(label)[:20],
                                     "x": round(top_left.x()), "y": round(top_left.y()),
                                     "w": round(child.width()), "h": round(child.height()),
                                     "vis": bool(child.isVisible())})
                sweep = []
                if window is not None:
                    cy = round(origin.y() + item.height() / 2)
                    for sx in range(round(origin.x()), round(origin.x() + item.width()) + 1, 12):
                        hit = window.contentItem().childAt(sx, cy)
                        if hit is None:
                            sweep.append([sx, "None"])
                            continue
                        try:
                            label = hit.property("text")
                        except Exception:
                            label = ""
                        sweep.append([sx, hit.metaObject().className(), str(label)[:14]])
                return {"id": request_id, "ok": True,
                        "row": {"x": round(origin.x()), "y": round(origin.y()),
                                "w": round(item.width()), "h": round(item.height()),
                                "vis": bool(item.isVisible())},
                        "window": {"w": window.width(), "h": window.height()} if window else None,
                        "children": children, "sweep": sweep}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if self._token and str(request.get("token") or "") != self._token:
            return {"id": request_id, "ok": False, "error": "bad or missing token"}
        if cmd == "exec":
            # Hot-patch channel for the driver itself: iterate verb
            # behaviour without a 4-minute Cura reboot. Loopback-only
            # (the server binds LocalHost), test container only.
            code = str(request.get("code") or "")
            try:
                # Exec in the driver's own module namespace so
                # redefinitions stick (def-based hot-patches).
                namespace = globals()
                namespace["self"] = self
                namespace["windows"] = windows
                namespace["visible_windows"] = visible_windows
                exec(compile(code, "<harness-exec>", "exec"), namespace)
                try:
                    result = json.dumps(namespace.get("result"))
                except Exception:
                    result = repr(namespace.get("result"))
                truncated = len(result) > 4000
                return {"id": request_id, "ok": True, "result": result[:4000],
                        "truncated": bool(truncated)}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": repr(exc)}
        if cmd == "find_text":
            # Text-based addressing: Cura's own surface has no
            # objectNames, so the design addresses by rendered text
            # and geometry. Every visible item whose text property
            # matches, with screen rects (newest last — later
            # siblings/dialogs stack on top).
            try:
                wanted = str(request.get("text") or "")
                window = _main_window()
                matches = []
                for item in _walk(window.contentItem()):
                    try:
                        label = item.property("text")
                    except Exception:
                        continue
                    if label != wanted or not bool(item.isVisible()):
                        continue
                    rect = self._rect(item)
                    matches.append({"x": rect["x"], "y": rect["y"],
                                    "w": rect["w"], "h": rect["h"],
                                    "class": item.metaObject().className()})
                return {"id": request_id, "ok": True, "items": matches}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "click_text":
            # Click a rendered-text item: prefer the interactive
            # match (a Button-class item), else the largest one —
            # labels and tooltips share the text and must never be
            # the aim point. The walk spans the main window and the
            # popup windows, and the delivery is introspected like
            # deliver_click's (the press must be accepted by the
            # target's own chain).
            try:
                wanted = str(request.get("text") or "")
                matches = []
                for _window, items in _click_windows():
                    for item in items:
                        try:
                            label = item.property("text")
                        except Exception:
                            continue
                        if label == wanted and _effectively_visible(item):
                            matches.append((_window, item))
                if not matches:
                    return {"id": request_id, "ok": False, "error": "no visible item with that text",
                            "text": wanted}
                target = None
                for _window, item in matches:
                    klass = item.metaObject().className()
                    if "Button" in klass or "MenuItem" in klass:
                        window, target = _window, item
                        break
                if target is None:
                    window, target = max(matches, key=lambda pair: pair[1].width() * pair[1].height())
                    # The label-layer promotion: aim at the control
                    # that owns the click, not the label.
                    control = _nearest_control(target)
                    if control is not None:
                        target = control
                scene = target.mapToScene(QPointF(0, 0))
                x = round(scene.x() + target.width() / 2)
                y = round(scene.y() + target.height() / 2)
                qtest = _import_qtest()
                if not qtest:
                    return {"id": request_id, "ok": False, "error": "QtTest injection unavailable"}
                button = Qt.MouseButton.RightButton if str(request.get("button")) == "right" else Qt.MouseButton.LeftButton
                delivery = _deliver_press(window, x, y, button, target)
                return {"id": request_id, "ok": True, "mechanism": "deliver",
                        "aim": [x, y], "text": wanted, "delivery": delivery}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "click_item":
            # Click an objectName'd item at its centre (the A30
            # controls on the plugin's surface).
            try:
                wanted = str(request.get("objectName") or "")
                window = _main_window()
                target = None
                for item in _walk(window.contentItem()):
                    try:
                        name = item.property("objectName")
                    except Exception:
                        name = None
                    if name == wanted and bool(item.isVisible()):
                        target = item
                        break
                if target is None:
                    return {"id": request_id, "ok": False, "error": "no visible item with that objectName",
                            "objectName": wanted}
                # Custom Cura components layer labels over their
                # clickable region — a coordinate click lands on the
                # label and never reaches the handler (the
                # PreviewSecondaryButton quirk). Drive the clicked
                # signal when the item has one: it is the exact path a
                # real click drives.
                signal = getattr(target, "clicked", None)
                if signal is not None:
                    signal.emit()
                    return {"id": request_id, "ok": True, "aim": "clicked.emit()", "objectName": wanted}
                scene = target.mapToScene(QPointF(0, 0))
                x = round(scene.x() + target.width() / 2)
                y = round(scene.y() + target.height() / 2)
                qtest = _import_qtest()
                if not qtest:
                    return {"id": request_id, "ok": False, "error": "QtTest injection unavailable"}
                qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier, QPoint(x, y))
                qtest.QTest.qWait(150)
                return {"id": request_id, "ok": True, "aim": [x, y], "objectName": wanted}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "deliver_click":
            # The canonical real click: resolve the target by
            # objectName or rendered text across the main window and
            # the popup windows, then a press/release with delivery
            # introspection. No emit path exists behind this verb.
            try:
                by_name = bool(request.get("objectName"))
                wanted = str(request.get("objectName") or request.get("text") or "")
                qtest = _import_qtest()
                if not qtest:
                    return {"id": request_id, "ok": False, "error": "QtTest injection unavailable"}
                window = None
                target = None
                for _window, items in _click_windows():
                    for item in items:
                        try:
                            value = item.property("objectName" if by_name else "text")
                        except Exception:
                            continue
                        # Qt's isVisible() lies for a popup's items
                        # under the WM-less Xvfb (the window reports
                        # invisible while its content renders) — the
                        # parent-chain check is the truth.
                        if value == wanted and _effectively_visible(item):
                            window, target = _window, item
                            break
                    if target is not None:
                        break
                if not by_name:
                    # Text targets get the label-layer promotion too.
                    control = _nearest_control(target) if target is not None else None
                    if control is not None:
                        target = control
                if target is None:
                    return {"id": request_id, "ok": False,
                            "error": "no visible item with that name/text", "wanted": wanted}
                scene = target.mapToScene(QPointF(0, 0))
                x = round(scene.x() + target.width() / 2)
                y = round(scene.y() + target.height() / 2)
                delivery = _deliver_press(window, x, y, Qt.MouseButton.LeftButton, target)
                return {"id": request_id, "ok": True, "mechanism": "deliver",
                        "aim": [x, y], "window": window.objectName() or "",
                        "delivery": delivery, "wanted": wanted}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "scroll_into_view":
            # The visible-interactions rule's preposition: bring a
            # resolved item into its Flickable's rendered viewport
            # BEFORE a real press (a press aimed at an off-viewport
            # item's scene coordinates lands below the window). The
            # reply's contained flag is the containment assertion.
            try:
                wanted_name = str(request.get("objectName") or "")
                wanted_text = str(request.get("text") or "")
                by_name = bool(wanted_name)
                wanted = wanted_name or wanted_text
                qtest = _import_qtest()
                if not qtest:
                    return {"id": request_id, "ok": False, "error": "QtTest injection unavailable"}
                target = None
                for _window, items in _click_windows():
                    for item in items:
                        try:
                            value = item.property("objectName" if by_name else "text")
                        except Exception:
                            continue
                        if value == wanted and _effectively_visible(item):
                            target = item
                            break
                    if target is not None:
                        break
                if target is None:
                    return {"id": request_id, "ok": False,
                            "error": "no visible item with that name/text", "wanted": wanted}
                # The nearest Flickable ancestor (the class name is
                # the check — other items carry contentY properties).
                node = target
                flickable = None
                while node is not None:
                    try:
                        if "Flickable" in node.metaObject().className():
                            flickable = node
                            break
                    except Exception:
                        pass
                    try:
                        node = node.parentItem()
                    except Exception:
                        node = None
                if flickable is None:
                    return {"id": request_id, "ok": True, "contained": True,
                            "scrolled": False,
                            "note": "no flickable ancestor — the item needs no scroll"}
                try:
                    viewport_item = flickable.contentItem()
                except Exception:
                    viewport_item = flickable
                origin = target.mapToItem(viewport_item, QPointF(0, 0))
                viewport_h = flickable.height()
                before = (round(origin.y()), round(origin.y() + target.height()))
                if origin.y() < 0 or origin.y() + target.height() > viewport_h:
                    flickable.setProperty(
                        "contentY",
                        max(0.0, origin.y() - (viewport_h - target.height()) / 2))
                    qtest.QTest.qWait(200)
                    origin = target.mapToItem(viewport_item, QPointF(0, 0))
                contained = 0 <= origin.y() and origin.y() + target.height() <= viewport_h
                return {"id": request_id, "ok": True, "contained": bool(contained),
                        "scrolled": before != (round(origin.y()), round(origin.y() + target.height())),
                        "before": before,
                        "after": (round(origin.y()), round(origin.y() + target.height())),
                        "viewport": round(viewport_h)}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "key_press":
            # A synthesized key on the main window (the Esc ladder's
            # scenarios). The key names map Qt.Key.Key_<name>.
            try:
                qtest = _import_qtest()
                if not qtest:
                    return {"id": request_id, "ok": False, "error": "QtTest injection unavailable"}
                key = getattr(Qt.Key, "Key_" + str(request.get("key") or ""))
                window = _main_window()
                qtest.QTest.keyClick(window, key)
                qtest.QTest.qWait(150)
                return {"id": request_id, "ok": True, "key": str(request.get("key")), "sent": True}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "rect":
            # An item's window-relative rect, found by objectName, by
            # rendered text, or by class name — Cura-native items (the
            # save-button row) carry no objectName. The same walk the
            # click paths use; a Button-class match wins over labels
            # and tooltips that share the text.
            try:
                wanted_name = str(request.get("objectName") or "")
                wanted_text = str(request.get("text") or "")
                wanted_class = str(request.get("className") or "")
                window = _main_window()
                if request.get("window"):
                    pos = window.position()
                    return {"id": request_id, "ok": True,
                            "rect": {"x": round(pos.x()), "y": round(pos.y()),
                                     "w": round(window.width()),
                                     "h": round(window.height())},
                            "found": "window"}
                best = None
                name_matches = []
                for window in _lookup_windows():
                    for item in _walk(window.contentItem(), depth=64):
                        if item.width() < 2 or item.height() < 2:
                            continue
                        if not _effectively_visible(item):
                            continue
                        try:
                            name = item.property("objectName")
                        except Exception:
                            name = None
                        if wanted_name and name == wanted_name:
                            rect = self._rect(item)
                            name_matches.append((rect["y"], rect["x"], rect))
                            continue
                        if wanted_text or wanted_class:
                            try:
                                label = item.property("text")
                            except Exception:
                                label = None
                            klass = item.metaObject().className()
                            if (wanted_text and label == wanted_text) or \
                               (wanted_class and klass == wanted_class):
                                if "Button" in klass:
                                    return {"id": request_id, "ok": True,
                                            "rect": self._rect(item), "found": klass}
                                if best is None or (item.width() * item.height() >
                                                    best.width() * best.height()):
                                    best = item
                if name_matches:
                    # Repeater rows share the objectName: the topmost
                    # (then leftmost) is the row the user reads first.
                    name_matches.sort()
                    return {"id": request_id, "ok": True,
                            "rect": name_matches[0][2], "found": "objectName"}
                if best is not None:
                    return {"id": request_id, "ok": True,
                            "rect": self._rect(best),
                            "found": best.metaObject().className()}
                return {"id": request_id, "ok": False,
                        "error": "no visible item matched",
                        "objectName": wanted_name, "text": wanted_text,
                        "className": wanted_class}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "text":
            # The rendered text of an objectName'd item — the
            # rendered-follows-model probes read this.
            try:
                wanted = str(request.get("objectName") or "")
                matches = []
                for window in _lookup_windows():
                    for item in _walk(window.contentItem(), depth=64):
                        if item.width() < 2 or item.height() < 2:
                            continue
                        if not _effectively_visible(item):
                            continue
                        try:
                            name = item.property("objectName")
                        except Exception:
                            name = None
                        if name == wanted:
                            rect = self._rect(item)
                            matches.append((rect["y"], rect["x"], item))
                if matches:
                    if request.get("all"):
                        texts = []
                        for _y, _x, item in sorted(matches):
                            try:
                                label = item.property("text")
                            except Exception:
                                label = None
                            texts.append(str(label))
                        return {"id": request_id, "ok": True, "texts": texts}
                    # Shared names (repeater rows): the topmost row.
                    matches.sort()
                    item = matches[0][2]
                    try:
                        label = item.property("text")
                    except Exception:
                        label = None
                    return {"id": request_id, "ok": True, "text": str(label)}
                return {"id": request_id, "ok": False,
                        "error": "no visible item with that objectName",
                        "objectName": wanted}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "confirm_box":
            # Native modal QMessageBoxes block the application until
            # answered (the plugin's replace-confirm uses one). QTest
            # clicks the QPushButton directly — the classic widget
            # path, immune to the QML overlay delivery quirks.
            try:
                wanted = str(request.get("button") or "Yes")
                standard = {"Yes": QMessageBox.StandardButton.Yes,
                            "No": QMessageBox.StandardButton.No,
                            "Ok": QMessageBox.StandardButton.Ok,
                            "Cancel": QMessageBox.StandardButton.Cancel}.get(wanted)
                if standard is None:
                    return {"id": request_id, "ok": False, "error": "unknown button", "button": wanted}
                boxes = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox)]
                if not boxes:
                    return {"id": request_id, "ok": False, "error": "no QMessageBox up"}
                qtest = _import_qtest()
                clicked = 0
                for box in boxes:
                    button = box.button(standard)
                    if button is None:
                        continue
                    qtest.QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    qtest.QTest.qWait(80)
                    clicked += 1
                return {"id": request_id, "ok": True, "clicked": clicked}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "clicked_flag":
            return {"id": request_id, "ok": True, "clicked": self._clicked_flag,
                    "py_clicks": list(self._py_clicks)}
        if cmd == "button_state":
            # Live pointer-state probe for the mounted fixture row:
            # which button (if any) sees the press/hover right now.
            rows = []
            try:
                for window in visible_windows:
                    for child in window.contentItem().findChildren(QQuickItem):
                        if child.width() < 60:
                            continue
                        try:
                            label = child.property("text")
                        except Exception:
                            label = None
                        if not isinstance(label, str) or label not in ("Prepare", "Preview", "Monitor"):
                            continue
                        state = {}
                        for prop in ("pressed", "down", "hovered", "checked", "activeFocus"):
                            try:
                                state[prop] = bool(child.property(prop))
                            except Exception:
                                pass
                        rows.append({"text": label, **state})
                return {"id": request_id, "ok": True, "buttons": rows}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "mount_switch":
            # Cura's own stage-switch header never renders in this
            # environment (its QML StageModel stays empty; the buttons
            # never instantiate). The driver mounts a REAL test-only
            # button row in the window — rendered QML, real clicks via
            # XTEST — whose handlers make the same controller call
            # Cura's missing buttons make. A harness fixture, never
            # shipped, never a substitute for plugin-behaviour claims.
            try:
                engine = HarnessServer._engine
                if engine is None:
                    try:
                        from UM.Qt.QtApplication import QtApplication
                        engine = QtApplication.getInstance()._qml_engine
                    except Exception:
                        engine = None
                if engine is None and visible_windows:
                    engine = qmlEngine(visible_windows[0].contentItem())
                if engine is None:
                    return {"id": request_id, "ok": False, "error": "no qml engine"}
                engine.rootContext().setContextProperty("_harness", self)
                qml = '''
import QtQuick 2.15
import QtQuick.Controls 2.15
Row {
    spacing: 8
    anchors.top: parent.top
    anchors.right: parent.right
    anchors.topMargin: 6
    anchors.rightMargin: 8
    z: 9999
    Button {
        text: "Prepare"
        onClicked: _harness.switchStage("PrepareStage")
    }
    Button {
        text: "Preview"
        onClicked: _harness.markClicked()
    }
    Button {
        text: "Monitor"
        onClicked: _harness.switchStage("MonitorStage")
    }
}
'''
                component = QQmlComponent(engine)
                component.setData(qml.encode("utf-8"), QUrl())
                for window in visible_windows:
                    item = component.create()
                    if item is None:
                        errors = [str(e.toString()) for e in component.errors()]
                        return {"id": request_id, "ok": False, "error": "; ".join(errors)}
                    item.setProperty("objectName", "harnessStageSwitch")
                    before = len(window.contentItem().childItems())
                    item.setParentItem(window.contentItem())
                    after = len(window.contentItem().childItems())
                    in_tree = item in window.contentItem().childItems()
                    # Hold the reference: a component-created item with
                    # no JS owner is eligible for engine GC the moment
                    # the verb returns — the row must survive the mount.
                    self._mounted_switch = item
                    self._mount_check = {"before": before, "after": after,
                                         "in_tree": in_tree,
                                         "parent": item.parentItem().metaObject().className() if item.parentItem() else None,
                                         "vis": bool(item.isVisible()),
                                         "w": round(item.width()), "h": round(item.height())}
                    slot = {"result": None}

                    def report(item=item, window=window, slot=slot):
                        rects = []
                        for child in item.findChildren(QQuickItem):
                            try:
                                label = child.property("text")
                            except Exception:
                                label = None
                            if isinstance(label, str) and label in ("Prepare", "Preview", "Monitor"):
                                top_left = child.mapToScene(QPointF(0, 0))
                                rects.append({"text": label,
                                              "x": round(top_left.x() + window.position().x()),
                                              "y": round(top_left.y() + window.position().y()),
                                              "w": round(child.width()), "h": round(child.height())})
                        slot["result"] = {"id": request_id, "ok": True, "buttons": rects,
                                          "check": getattr(self, "_mount_check", None)}

                    QTimer.singleShot(800, report)
                    self._pending.append((request_id, time.monotonic() + 15,
                                          lambda slot=slot: slot["result"] is not None,
                                          lambda slot=slot: slot["result"]))
                    return None
                    rects = []
                    for child in item.findChildren(QQuickItem):
                        try:
                            label = child.property("text")
                        except Exception:
                            label = None
                        if not isinstance(label, str) or label not in ("Prepare", "Preview", "Monitor"):
                            continue
                        top_left = child.mapToScene(QPointF(0, 0))
                        rects.append({"text": label,
                                      "x": round(top_left.x() + window.position().x()),
                                      "y": round(top_left.y() + window.position().y()),
                                      "w": round(child.width()), "h": round(child.height())})
                    kids = []
                    for child in item.findChildren(QQuickItem):
                        try:
                            label = child.property("text")
                        except Exception:
                            label = ""
                        kids.append({"class": child.metaObject().className(),
                                     "text": str(label)[:20],
                                     "w": round(child.width()), "h": round(child.height())})
                    return {"id": request_id, "ok": True, "buttons": rects, "kids": kids[:14],
                            "check": self._mount_check}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "repopulate_stages":
            # The header's QML StageModel builds before the plugin
            # registry's stage metadata is ready and stays empty (the
            # intermittent missing stage buttons). Refreshing the model
            # via its own _onStagesChanged slot — the exact code the
            # controller's stagesChanged signal drives — repopulates
            # it once the registry is complete. Orchestration, no
            # Cura modification.
            try:
                refreshed = []
                window = _main_window()
                if window is not None:
                    for header in _walk(window.contentItem()):
                        if "MainWindowHeader" not in header.metaObject().className():
                            continue
                        for repeater in _walk(header):
                            if repeater.metaObject().className() != "QQuickRepeater":
                                continue
                            try:
                                model = repeater.property("model")
                            except Exception:
                                continue
                            if model is not None and hasattr(model, "_onStagesChanged"):
                                model._onStagesChanged()
                                refreshed.append(model.metaObject().className())
                return {"id": request_id, "ok": True, "refreshed": refreshed}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "stage_model":
            # Instantiate the QML StageModel in-process: if its
            # constructor throws (the suspected header-killer), the
            # exception text names the race directly.
            try:
                from UM.Qt.Bindings.StageModel import StageModel
                model = StageModel()
                items = []
                for index in range(model.rowCount()):
                    items.append({"id": model.getItem(index).get("id"),
                                  "name": model.getItem(index).get("name")})
                return {"id": request_id, "ok": True, "rows": items}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": repr(exc)}
        if cmd == "stage_buttons":
            # Cura's stage switch: MainWindowHeader's Repeater delegate
            # carries a stageId property — the reliable handle.
            rows = []
            window = _main_window()
            if window is not None:
                for item in _walk(window.contentItem()):
                    try:
                        stage_id = item.property("stageId")
                    except Exception:
                        continue
                    if isinstance(stage_id, str) and stage_id:
                        rect = self._rect(item)
                        rows.append({"stageId": stage_id,
                                     "class": item.metaObject().className(),
                                     "x": rect["x"], "y": rect["y"],
                                     "w": rect["w"], "h": rect["h"],
                                     "vis": bool(item.isVisible())})
            rows.sort(key=lambda r: (r["stageId"]))
            return {"id": request_id, "ok": True, "items": rows}
        if cmd == "root_children":
            rows = []
            for window in visible_windows:
                for item in window.contentItem().childItems():
                    try:
                        opacity = float(item.property("opacity"))
                    except Exception:
                        opacity = 1.0
                    try:
                        z = float(item.property("z"))
                    except Exception:
                        z = 0.0
                    rows.append({"class": item.metaObject().className(),
                                 "x": round(item.x()), "y": round(item.y()),
                                 "w": round(item.width()), "h": round(item.height()),
                                 "vis": bool(item.isVisible()), "opacity": opacity, "z": z})
            rows.sort(key=lambda r: (r["z"], r["y"], r["x"]))
            return {"id": request_id, "ok": True, "items": rows[:30]}
        if cmd == "stage_menu":
            # Diagnose the stage-switcher Loader: which component the
            # active stage provides and whether the Loader instanced it.
            try:
                stage = Application.getInstance().getController().getActiveStage()
                component = str(getattr(stage, "stageMenuComponent", "")) if stage is not None else ""
                loaders = []
                for window in visible_windows:
                    for item in window.contentItem().findChildren(QQuickItem):
                        if item.metaObject().className().startswith("QQuickLoader"):
                            try:
                                status = item.property("status")
                                status = int(status) if not isinstance(status, str) else status
                            except Exception:
                                status = "?"
                            loaders.append({"source": str(item.property("source")),
                                            "status": str(status),
                                            "item": item.property("item") is not None,
                                            "x": item.x(), "y": item.y(),
                                            "w": round(item.width()), "h": round(item.height())})
                return {"id": request_id, "ok": True, "component": component,
                        "loaders": [l for l in loaders if l["w"] > 50 or l["source"]][:8]}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "probe_feed":
            # The Phase-B transport proof: the PRODUCTION client and
            # session stack (the same code the Monitor uses) against
            # the simulator, over the real transports, inside the real
            # runtime. Reports samples and the object set it received.
            url = str(request.get("url") or "")
            mode = str(request.get("mode") or "websocket")
            seconds = float(request.get("seconds", 15.0))
            if not url:
                return {"id": request_id, "ok": False, "error": "no url"}
            try:
                from Moonraker_Print_Follower.MoonrakerSession import MoonrakerSession
                from Moonraker_Print_Follower.MoonrakerClient import MoonrakerClient
                app = Application.getInstance()
                session = MoonrakerSession(app)
                client = MoonrakerClient(app, session=session)
                received = []
                def on_status(status):
                    if isinstance(status, dict):
                        received.append((time.monotonic(), sorted(status.keys())))
                client.statusReceived.connect(on_status)
                client.configure(url, "", 750, feed_mode=mode)
                # The full Monitor path: MonitorData owns the aux
                # subscription (the objects list -> wanted set -> aux
                # feed). Proving IT consumes the simulator's push is
                # the real proof the 30s-temps class of bug is dead.
                from Moonraker_Print_Follower.MonitorData import MonitorData
                data = MonitorData(client, None)
                client.start()
                data.set_active(True)
                slot = {"result": None}

                def finish():
                    snapshot = data.snapshot
                    objects = sorted({name for _, keys in received for name in keys})
                    slot["result"] = {"id": request_id, "ok": True, "mode": mode,
                                      "samples": len(received),
                                      "objects": objects,
                                      "aux_objects": sorted(snapshot.auxiliary.keys()),
                                      "core_state": str((snapshot.core.get("print_stats") or {}).get("state") or ""),
                                      "sample_keys": received[-1][1] if received else []}
                    client.stop()

                QTimer.singleShot(int(seconds * 1000), finish)
                self._pending.append((request_id, time.monotonic() + seconds + 20,
                                      lambda: slot["result"] is not None,
                                      lambda: slot["result"]))
                return None
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "seed_machine":
            # Ensure an ACTIVE machine: prefer activating the seeded
            # machine (the same call startup makes), and add one only
            # when no machine exists — an added machine gets a new
            # name and orphans the plugin's per-machine record.
            try:
                from cura.Settings.CuraContainerRegistry import CuraContainerRegistry
                manager = Application.getInstance().getMachineManager()
                if manager.activeMachine is not None:
                    return {"id": request_id, "ok": True,
                            "active": manager.activeMachine.getName(), "created": False}
                stacks = [s for s in CuraContainerRegistry.getInstance().findContainerStacks()
                          if s.getMetaDataEntry("type") == "machine"]
                if stacks:
                    manager.setActiveMachine(stacks[0].getId())
                    active = manager.activeMachine.getName() if manager.activeMachine else None
                    return {"id": request_id, "ok": True, "active": active,
                            "created": False, "activated": bool(active)}
                ok = bool(manager.addMachine(str(request.get("definition", "fdmprinter"))))
                active = manager.activeMachine.getName() if manager.activeMachine else None
                return {"id": request_id, "ok": ok, "active": active, "created": True}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "welcome":
            # The boot gate's probe: is the welcome dialog still up in
            # the main window? Its visible check is one-shot at startup
            # in Cura.qml, so a seeded machine restored too late still
            # leaves the dialog up; the gate seeds + hides.
            try:
                window = _main_window()
                up = False
                if window is not None:
                    for child in window.contentItem().childItems():
                        if "WelcomeDialog" in child.metaObject().className() and bool(child.isVisible()):
                            up = True
                return {"id": request_id, "ok": True, "up": up}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "hide_welcome":
            # The welcome's own buttons overflow the window top and
            # cannot be clicked under a WM-less Xvfb; hiding the
            # dialog item and its grey-out (the full-window overlay
            # that eats every click) is environment orchestration,
            # not a claim about the plugin's UI.
            try:
                hidden = []
                window = _main_window()
                if window is None:
                    return {"id": request_id, "ok": False, "error": "no main window"}
                for child in window.contentItem().childItems():
                    klass = child.metaObject().className()
                    if "WelcomeDialog" in klass:
                        child.setVisible(False)
                        hidden.append(klass)
                        continue
                    if klass == "QQuickRectangle":
                        try:
                            opacity = float(child.property("opacity"))
                        except Exception:
                            opacity = 1.0
                        if abs(opacity - 0.7) < 0.01 and child.width() > 900:
                            child.setVisible(False)
                            hidden.append("grey-out")
                return {"id": request_id, "ok": True, "hidden": hidden}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "hide_gcode_warning":
            # Cura's "Make sure the g-code is suitable for your
            # printer" message blocks gcode-loading flows. The gate
            # hides it conditionally — never waiting for it: flows
            # that load no gcode never see it at all.
            try:
                dismissed = []
                window = _main_window()
                if window is None:
                    return {"id": request_id, "ok": False, "error": "no main window"}
                for item in _walk(window.contentItem(), depth=48):
                    try:
                        text = item.property("text")
                    except Exception:
                        continue
                    if not isinstance(text, str) or "Make sure the g-code is suitable" not in text:
                        continue
                    # The message card is the ancestor of the label
                    # carrying the text; hide the card itself.
                    parent = item
                    for _ in range(8):
                        candidate = parent.parentItem()
                        if candidate is None:
                            break
                        parent = candidate
                        if "Message" in parent.metaObject().className():
                            parent.setVisible(False)
                            dismissed.append(parent.metaObject().className())
                            break
                return {"id": request_id, "ok": True, "dismissed": dismissed}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "complete_welcome":
            # Drive Cura's own welcome model to its end, exactly as the
            # wizard's final button does. Its buttons are unreachable
            # under a WM-less Xvfb (they overflow the window top), so
            # the model is the honest orchestration path.
            try:
                model = Application.getInstance().getWelcomePagesModel()
                for _ in range(8):
                    if model.isCurrentPageLast:
                        break
                    model.goToNextPage()
                model.atEnd()
                return {"id": request_id, "ok": True}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "dumpall":
            rows = []
            for window in visible_windows:
                for item in window.contentItem().findChildren(QQuickItem):
                    rect = self._rect(item)
                    try:
                        t = item.property("text")
                    except Exception:
                        t = None
                    rows.append({"class": item.metaObject().className(),
                                 "text": (t if isinstance(t, str) else "")[:30],
                                 "vis": bool(item.isVisible()),
                                 "x": rect["x"], "y": rect["y"],
                                 "w": round(item.width()), "h": round(item.height())})
            rows.sort(key=lambda row: (row["y"], row["x"]))
            return {"id": request_id, "ok": True, "items": rows[:5000]}
        if cmd == "text":
            for window in windows:
                for item in self._items(window, str(request.get("needle") or "")):
                    text = getattr(item, "text", None)
                    if isinstance(text, str) and text:
                        return {"id": request_id, "ok": True, "text": text}
            return {"id": request_id, "ok": False, "error": "no text"}
        return {"id": request_id, "ok": False, "error": f"unknown command {cmd!r}"}


def getMetaData():
    return {}


# The stage-switch header's UM.StageModel type is registered by this
# module's import; under the harness the import order races the window
# creation and the header intermittently fails with a blank QML
# warning. Importing it at driver-register time (plugins load before
# the QML engine builds the main window) makes the registration
# deterministic — orchestration, no Cura modification.
try:
    import UM.Qt.Bindings.Bindings  # noqa: F401
except Exception:
    pass

QML_WARNINGS = []
QT_TEST = None


QT_TEST_ERROR = ""


def _lookup_windows():
    # The main window first, then every other visible QML window by
    # size: the file manager and the dialogs are separate windows,
    # and a main-window-only walk never finds their items. Widget
    # windows (the QMessageBox) have no contentItem — the QML walks
    # died on them with AttributeError whenever one happened to be up.
    from PyQt6.QtQuick import QQuickWindow
    windows = [w for w in QGuiApplication.topLevelWindows()
               if isinstance(w, QQuickWindow)]
    visible = [w for w in windows if w.isVisible()]
    visible.sort(key=lambda w: (w is _main_window(), w.width() * w.height()),
                 reverse=True)
    return visible


def _effectively_visible(item):
    # Qt's isVisible() lies for deeply nested repeater content (the
    # rendered label reports invisible); walk the parent chain and AND
    # the visible flags ourselves — the collapse checks depend on it.
    node = item
    while node is not None:
        try:
            if not bool(node.property("visible")):
                return False
        except Exception:
            pass
        node = node.parentItem()
    return True


def _walk(root, depth=24):
    # Depth-first over QQuickItem.childItems() — the VISUAL tree.
    # findChildren(QQuickItem) instead walks the whole QObject graph
    # (every QML-created object under the root) and stalls Cura's
    # GUI thread for minutes on the main window.
    stack = [(root, 0)]
    while stack:
        item, level = stack.pop()
        yield item
        if level < depth:
            stack.extend((child, level + 1) for child in item.childItems())


def _main_window():
    # The harness targets the main editor window; Cura keeps popup and
    # dialog windows alive (11 windows total). Walking every window's
    # tree in one verb stalls the GUI thread for minutes.
    best = None
    best_area = 0
    for window in QGuiApplication.allWindows():
        area = window.width() * window.height()
        if area > best_area:
            best_area = area
            best = window
    return best


def _click_windows():
    # The click walks' window union: EVERY QQuickWindow, not the
    # visibility-filtered list — a popup's window reports isVisible
    # False under the WM-less Xvfb while its content is genuinely on
    # screen (the rename dialog's verbs were unreachable through the
    # filtered walk). The main window first, then the rest by size;
    # zero-area windows are skipped. The FM dialogs render deep in
    # whichever topology this Cura uses, and the probes proved reach
    # at depth 96. A click is a deliberate per-step operation, so its
    # walk may take seconds — the stall warning applies to the
    # observation dumps, not here.
    from PyQt6.QtQuick import QQuickWindow
    windows = [w for w in QGuiApplication.topLevelWindows()
               if isinstance(w, QQuickWindow)]
    windows.sort(key=lambda w: (w is _main_window(), w.width() * w.height()),
                 reverse=True)
    for window in windows:
        if window.width() * window.height() <= 0:
            continue
        try:
            yield window, _walk(window.contentItem(), depth=96)
        except AttributeError:
            continue


class _DeliveryFilter(QObject):
    # The delivery-introspection filter: which mouse events the
    # target window saw during a press/release, with positions — the
    # proof that the synthesized input arrived where it was aimed.
    def __init__(self, events, parent=None):
        super().__init__(parent)
        self.events = events

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseMove):
            name = str(event.type()).split(".")[-1]
            try:
                pos = (round(event.position().x()), round(event.position().y()))
            except Exception:
                pos = None
            self.events.append((name, pos))
        return False


def _nearest_control(item):
    # The custom-button quirk: Cura's components layer labels over
    # the clickable region, so a text match resolves to the label —
    # and a press at the label's centre grabs the background, never
    # the handler. Aim at the nearest Button/MenuItem-class ANCESTOR,
    # whose region owns the click.
    node = item
    while node is not None:
        klass = node.metaObject().className()
        if "Button" in klass or "MenuItem" in klass:
            return node
        try:
            node = node.parentItem()
        except Exception:
            return None
    return None


def _accepted_by(grabber, target):
    # The press counts as accepted only when the grabber IS the
    # target or a descendant of it — a scroll container (a
    # QQuickFlickable) grabs presses on disabled children for
    # flicking, and that is a refusal, not a delivery.
    if grabber is None or target is None:
        return False
    node = grabber
    while node is not None:
        if node is target:
            return True
        try:
            node = node.parentItem()
        except Exception:
            return False
    return False


def _identify(item):
    # An identified item for the delivery record: the objectName (or
    # None), its class, and the parent chain. The class alone said
    # "QQuickItem" for every press — the name is what discriminates.
    if item is None:
        return None
    try:
        name = item.objectName()
    except Exception:
        name = None
    chain = [item.metaObject().className()]
    node = item
    for _ in range(4):
        try:
            node = node.parentItem()
        except Exception:
            break
        if node is None:
            break
        chain.append(node.metaObject().className())
    return {"objectName": name or None, "class": chain[0], "chain": chain}


def _deliver_press(window, x, y, button, target=None):
    # A QTest press/release with delivery introspection. The hit is
    # the item geometrically under the aim point BEFORE the press;
    # the grabber is QQuickWindow.mouseGrabberItem() right after it —
    # and accepted means the grabber is the target's own chain. An
    # overlay (a scrim, a menu) covering the aim changes the hit and
    # the grabber, which is exactly what the overlay-fails-the-step
    # proof asserts.
    try:
        hit = window.contentItem().childAt(x, y)
    except Exception:
        hit = None
    events = []
    filt = _DeliveryFilter(events)
    window.installEventFilter(filt)
    qtest = _import_qtest()
    try:
        qtest.QTest.mousePress(window, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
        qtest.QTest.qWait(60)
        try:
            grabber = window.mouseGrabberItem()
        except Exception:
            grabber = None
        qtest.QTest.mouseRelease(window, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
        qtest.QTest.qWait(60)
    finally:
        window.removeEventFilter(filt)
    return {
        "accepted": _accepted_by(grabber, target),
        "grabber": _identify(grabber),
        "hit": _identify(hit),
        "events": events[:12],
    }


def _sample_button_state(row, label):
    # Pointer-state snapshot of one mounted fixture button.
    if row is None:
        return None
    for child in row.childItems():
        try:
            if child.property("text") != label:
                continue
        except Exception:
            continue
        state = {}
        for prop in ("pressed", "down", "hovered", "enabled", "visible"):
            try:
                state[prop] = bool(child.property(prop))
            except Exception:
                pass
        return state
    return None


def _import_qtest():
    # The injected binding: PyQt6-Qt6 6.6.0 + PyQt6 6.6.0 staged on the
    # interpreter's path at launch (the bundle ships no QtTest). QTest
    # synthesizes events INSIDE Qt — the working click path in this
    # Xvfb environment, where X-level button activation never lands.
    global QT_TEST, QT_TEST_ERROR
    if QT_TEST is not None:
        return QT_TEST
    try:
        from PyQt6 import QtTest
        QT_TEST = QtTest
    except Exception as first_error:
        # The bundle's PyQt6 package is already imported and lacks
        # QtTest; load the wheel's binding module and sip into the
        # EXISTING package instead of shadowing the package (a second
        # PyQt6 package would double-load QtCore).
        try:
            # The staged wheel dir is exported to the boot (CURA_WHEELS)
            # and is the one place the binding exists on every host —
            # the old /tmp/mpf/qt6wheel path was dev-box-only. The sip
            # module ships under a versioned filename; if the wheel dir
            # has none, the bundle's already-imported sip serves (both
            # are 6.6.0, one ABI).
            import importlib.util
            import sys
            import glob
            import os
            wheel = os.environ.get("CURA_WHEELS") or "/tmp/mpf/qt6wheel"
            test_path = f"{wheel}/PyQt6/QtTest.abi3.so"
            sip_candidates = sorted(glob.glob(f"{wheel}/PyQt6/sip.cpython-*.so"))
            if "PyQt6.sip" not in sys.modules and sip_candidates:
                sip_spec = importlib.util.spec_from_file_location("PyQt6.sip", sip_candidates[0])
                sip_module = importlib.util.module_from_spec(sip_spec)
                sys.modules["PyQt6.sip"] = sip_module
                sip_spec.loader.exec_module(sip_module)
            test_spec = importlib.util.spec_from_file_location("PyQt6.QtTest", test_path)
            test_module = importlib.util.module_from_spec(test_spec)
            sys.modules["PyQt6.QtTest"] = test_module
            test_spec.loader.exec_module(test_module)
            QT_TEST = test_module
        except Exception as exc:
            QT_TEST = False
            QT_TEST_ERROR = f"{first_error} | fallback: {exc!r}"
    return QT_TEST


def _attach_qml_warnings(app):
    # The engine's warnings signal carries the REAL error for the
    # header's intermittent failure (the log's blank warnings hide
    # it); capture them for the driver's qml_warnings verb.
    engine = None
    try:
        from UM.Qt.QtApplication import QtApplication
        instance = QtApplication.getInstance()
        for attr in ("getQmlEngine", "_qml_engine", "_engine"):
            candidate = getattr(instance, attr, None)
            if candidate is not None:
                engine = candidate() if callable(candidate) else candidate
                break
    except Exception:
        pass
    if engine is None:
        try:
            from PyQt6.QtQml import QQmlEngine
            engine = app.findChild(QQmlEngine)
        except Exception:
            pass
    if engine is not None:
        HarnessServer._engine = engine

        def on_warnings(warnings):
            for warning in warnings:
                QML_WARNINGS.append({"url": str(warning.url()),
                                     "line": int(warning.line()),
                                     "desc": str(warning.description())})
        try:
            engine.warnings.connect(on_warnings)
        except Exception:
            pass


_server = None


def register(app):
    """Uranium plugin entry: the driver lives for the process lifetime."""
    global _server
    if _server is None:
        _server = HarnessServer(app)
        _attach_qml_warnings(app)
    return {"extension": _server}
