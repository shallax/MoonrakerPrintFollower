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

from PyQt6.QtCore import QObject, QTimer, QPointF
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtNetwork import QHostAddress, QTcpServer
from PyQt6.QtQuick import QQuickItem, QQuickWindow
from UM.Application import Application

PORT_FILE = "/tmp/mpf/harness_port.txt"


class HarnessServer(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        self._pending = []  # (request_id, deadline, predicate, reply_builder)
        self._buffers = {}
        if not self._server.listen(QHostAddress.SpecialAddress.LocalHost, 0):
            return
        with open(PORT_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(self._server.serverPort()))
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._drain)
        self._poll.start()

    def setVersion(self, *_args):
        # Cura's plugin loader treats returned "extension" objects as
        # Cura extensions; the server is only an observation channel.
        pass

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
            return {"id": request_id, "ok": True, "pid": os.getpid(),
                    "platform": QGuiApplication.platformName(),
                    "stage": self._stage(), "windows": len(windows)}
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
            rows = []
            for window in visible_windows:
                for item in window.contentItem().findChildren(QQuickItem):
                    if not item.isVisible() or item.width() < 8 or item.height() < 8:
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
        if cmd == "quit":
            try:
                Application.getInstance().closeApplication()
                return {"id": request_id, "ok": True}
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
            # The same code path the Add-printer wizard drives; the
            # welcome dialog leaves once an active machine exists.
            try:
                manager = Application.getInstance().getMachineManager()
                ok = bool(manager.addMachine(str(request.get("definition", "fdmprinter"))))
                return {"id": request_id, "ok": ok}
            except Exception as exc:
                return {"id": request_id, "ok": False, "error": str(exc)}
        if cmd == "hide_welcome":
            # The wizard's buttons overflow the window top and cannot
            # be clicked; hiding the overlay (and its grey-out, which
            # follows the dialog item's visible) is environment
            # orchestration, not a claim about the plugin's UI.
            try:
                target = None
                for window in visible_windows:
                    for item in window.contentItem().findChildren(QQuickItem):
                        try:
                            t = item.property("text")
                        except Exception:
                            t = None
                        if isinstance(t, str) and "Cura is developed by" in t:
                            target = item
                            break
                    if target is not None:
                        break
                if target is None:
                    return {"id": request_id, "ok": False, "error": "welcome label not found"}
                chain = []
                item = target
                for _ in range(6):
                    item.setProperty("visible", False)
                    chain.append(item.metaObject().className())
                    item = item.parentItem()
                    if item is None:
                        break
                return {"id": request_id, "ok": True, "chain": chain}
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


_server = None


def register(app):
    """Uranium plugin entry: the driver lives for the process lifetime."""
    global _server
    if _server is None:
        _server = HarnessServer(app)
    return {"extension": _server}
