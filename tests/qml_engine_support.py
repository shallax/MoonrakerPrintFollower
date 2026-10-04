"""Contracts measured on the REAL engine, offscreen: geometry the text
linters cannot see and delegate rendering that only exists once the
documents are mounted.

MoonrakerMonitor.qml and MoonrakerPreviewCard.qml need a GUI
application (QFontDatabase is a hard requirement of every Text), while
the shared harness builds a core application — so this file owns its
own application and skips when the process already has one. The
per-file leg of tools/run_tests.sh runs each test file in its own
process, which is where these run for real.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The Cura widget stubs extend QtQuick Controls, whose default style is
# the host's native one. On Windows and macOS that style lives in a
# plugin the pip wheel cannot load, so a mount of any document holding
# one of those stubs fails there — the cascade reads only as "Type
# CameraPane unavailable". Basic is the engine-independent style, and
# pinning it is what makes a mount behave the same on every host.
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from tests.qt_runtime_support import QT_AVAILABLE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

_ENV_REPORTED = False


def _report_environment():
    """One line per process naming the environment the pixel censuses
    are measured in: the resolved default family and how many families
    that platform's font database offers. A leg that lays out
    differently from the pinned container says so here, once — instead
    of leaving a dozen width assertions to disagree without saying why.
    Never raises: a probe that fails the suite it is diagnosing is
    worse than no probe."""
    global _ENV_REPORTED
    if _ENV_REPORTED:
        return
    _ENV_REPORTED = True
    try:
        from PyQt6.QtGui import QFontDatabase, QGuiApplication
        families = sorted(QFontDatabase.families())
        sys.stderr.write(
            "harness environment: default family=%r families=%d "
            "QT_QPA_FONTDIR=%r\n"
            % (QGuiApplication.font().family(), len(families),
               os.environ.get("QT_QPA_FONTDIR", "")))
        sys.stderr.write("harness environment: families=%r\n" % (families[:8],))
    except Exception as exc:  # noqa: BLE001 - a probe must never fail the suite
        sys.stderr.write("harness environment: unavailable (%r)\n" % (exc,))
    sys.stderr.flush()

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QMetaObject, QObject, QPointF, QRectF, Qt, QUrl, qInstallMessageHandler
    from PyQt6.QtCore import pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtGui import QColor, QGuiApplication
    from PyQt6.QtQml import QQmlComponent, QQmlEngine
    from PyQt6.QtQuick import QQuickItem, QQuickWindow

    from mpf.gcode.GCodeIndex import build_index_from_bytes
    from mpf.geometry.Polygons import point_in_polygon, polygon_bounds
    from mpf.plate.PlateQt import _PLATE_TRAVEL_VISUAL_RATIO, qml_geometry
    from mpf.gcode.PlateProgress import layer_polylines

    class CuraApplicationDouble(QObject):
        """The one context property both documents read for idleness."""

        platformActivityChanged = pyqtSignal()

        @pyqtProperty(bool, notify=platformActivityChanged)
        def platformActivity(self):
            return True


    class CameraModelDouble(QObject):
        """The webcam surface the camera card reads — enough for its
        title row to build with the selector and refresh button
        showing, which is the widest that row ever gets. The FPS
        surface (the idle-load request) records every rate the control
        commits, so one wheel notch is observable."""

        monitorConnectedChanged = pyqtSignal()
        cameraRecoveringChanged = pyqtSignal()
        cameraRotationChanged = pyqtSignal()
        cameraFlipChanged = pyqtSignal()
        activeWebcamChanged = pyqtSignal()
        webcamNamesChanged = pyqtSignal()
        cameraFpsChanged = pyqtSignal()
        webcamStreamEnabledChanged = pyqtSignal()
        detectionChanged = pyqtSignal()

        def __init__(self, fps=15.0, maximum=30.0):
            super().__init__()
            self.fps_calls = []
            self._camera_fps = float(fps)
            self._camera_fps_maximum = float(maximum)
            self._stream_enabled = True
            self._snapshot_available = False
            self._detection_state = "idle"
            self._detection_score = -1
            self.training_resets = 0

        @pyqtProperty(bool, notify=detectionChanged)
        def detectionEnabled(self): return self._detection_state != "idle"
        @pyqtProperty(bool, notify=detectionChanged)
        def detectionCameraReady(self): return True
        @pyqtProperty(str, notify=detectionChanged)
        def detectionRegionCamera(self): return "camera-one"
        @pyqtProperty(str, notify=detectionChanged)
        def cameraName(self): return "webcam"
        @pyqtSlot()
        def resetDetectionBaseline(self): self.training_resets += 1

        @pyqtProperty(bool, notify=detectionChanged)
        def detectionEditingRegions(self): return False
        @pyqtProperty(bool, notify=detectionChanged)
        def detectionShowBoxes(self): return True
        @pyqtProperty("QVariant", notify=detectionChanged)
        def detectionRegions(self): return []
        @pyqtProperty("QVariant", notify=detectionChanged)
        def detectionBoxes(self): return []
        @pyqtProperty(float, notify=detectionChanged)
        def detectionAnalysisAge(self): return -1.0

        @pyqtProperty(str, notify=detectionChanged)
        def detectionState(self):
            return self._detection_state

        @pyqtProperty(bool, notify=detectionChanged)
        def detectionGlobalEnabled(self):
            return True

        @pyqtProperty(int, notify=detectionChanged)
        def detectionScore(self):
            return self._detection_score

        @pyqtProperty(str, notify=detectionChanged)
        def detectionStatus(self):
            return {"waiting": "Waiting for an analysed frame",
                    "stale": "Camera analysis is stale",
                    "normal": "Normal", "warning": "Warning",
                    "failure": "Possible failure"}.get(self._detection_state, "Off for this printer")

        def set_detection(self, state, score=-1):
            self._detection_state = state
            self._detection_score = score
            self.detectionChanged.emit()

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraFps(self):
            return self._camera_fps

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraFpsMin(self):
            return 0.5

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraFpsMax(self):
            return self._camera_fps_maximum

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraSnapshotMaxFps(self):
            return 5.0

        @pyqtProperty(bool, notify=cameraFpsChanged)
        def cameraSnapshotAvailable(self):
            return self._snapshot_available

        @pyqtProperty(bool, notify=cameraFpsChanged)
        def cameraSnapshotMode(self):
            return self._snapshot_available and self._camera_fps <= 5.0

        def set_snapshot_available(self, available):
            self._snapshot_available = bool(available)
            self.cameraFpsChanged.emit()

        def set_camera_ceiling(self, maximum, fps=None):
            """A camera re-selected (or re-configured) behind the pane:
            the ceiling changes and the published rate follows it down
            exactly as MonitorCamera republishes."""
            self._camera_fps_maximum = float(maximum)
            if fps is None:
                self._camera_fps = min(self._camera_fps, self._camera_fps_maximum)
            else:
                self._camera_fps = float(fps)
            self.cameraFpsChanged.emit()

        @pyqtSlot(float)
        def setCameraFps(self, fps):
            self.fps_calls.append(float(fps))
            self._camera_fps = float(fps)
            self.cameraFpsChanged.emit()

        @pyqtProperty(bool, notify=webcamStreamEnabledChanged)
        def webcamStreamEnabled(self):
            return self._stream_enabled

        def set_stream_enabled(self, enabled):
            """The stream toggle, as the model publishes it: the flag
            and the URL move together (setWebcamStreamEnabled)."""
            self._stream_enabled = bool(enabled)
            self.webcamStreamEnabledChanged.emit()

        @pyqtProperty(bool, notify=monitorConnectedChanged)
        def monitorConnected(self):
            return True

        @pyqtProperty(bool, notify=cameraRecoveringChanged)
        def cameraRecovering(self):
            return False

        @pyqtProperty(int, notify=cameraRotationChanged)
        def cameraRotation(self):
            return 0

        @pyqtProperty(bool, notify=cameraFlipChanged)
        def cameraFlipHorizontal(self):
            return False

        @pyqtProperty(bool, notify=cameraFlipChanged)
        def cameraFlipVertical(self):
            return False

        @pyqtProperty("QVariant", notify=webcamNamesChanged)
        def webcamNames(self):
            return ["webcam", "webcam2"]

        @pyqtProperty(int, notify=activeWebcamChanged)
        def activeWebcamIndex(self):
            return 0

        @pyqtSlot(result=int)
        def cameraPaneInstanceId(self):
            return 1

        @pyqtSlot(int, str)
        def cameraPaneTrace(self, pane_id, message):
            pass

        @pyqtSlot()
        def cameraFirstFrameRendered(self):
            pass

        @pyqtSlot()
        def cameraRenderStalled(self):
            pass

        @pyqtSlot()
        def refreshWebcams(self):
            pass

        @pyqtSlot(int)
        def selectWebcam(self, index):
            pass


    class PrinterModelDouble(QObject):
        """A printer model that RECORDS the pane commands. A click on a
        null model looks the same whether or not a guard ran, so the
        refusal is only observable through the call log."""

        infoCollapsedChanged = pyqtSignal()
        statusCollapsedChanged = pyqtSignal()
        controlsCollapsedChanged = pyqtSignal()
        monitorEtaChanged = pyqtSignal()
        printActiveChanged = pyqtSignal()
        fanItemsChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.info_calls = []
            self.status_calls = []
            self.controls_calls = []
            self.section_calls = []
            self.sensor_colors = []
            self._fan_items = []
            self._info_collapsed = False
            self._status_collapsed = False
            self._controls_collapsed = False
            self._eta = "—"
            self._finish = "—"
            self._print_active = False

        @pyqtProperty("QVariant", notify=fanItemsChanged)
        def fanControlItems(self):
            return self._fan_items

        def republishFans(self, items):
            """The Moonraker refresh: a NEW fan list (the same fans,
            fresh values) replaces the repeater's model."""
            self._fan_items = items
            self.fanItemsChanged.emit()

        @pyqtProperty(str, notify=monitorEtaChanged)
        def monitorEta(self):
            return self._eta

        @pyqtSlot(str)
        def setMonitorEta(self, text):
            self._eta = text
            self.monitorEtaChanged.emit()

        @pyqtProperty(bool, notify=printActiveChanged)
        def printActive(self):
            return self._print_active

        @pyqtSlot(bool)
        def setPrintActive(self, active):
            self._print_active = bool(active)
            self.printActiveChanged.emit()

        @pyqtProperty(str, notify=monitorEtaChanged)
        def monitorFinish(self):
            return self._finish

        @pyqtSlot(str)
        def setMonitorFinish(self, text):
            self._finish = text
            self.monitorEtaChanged.emit()

        @pyqtProperty("QVariant")
        def temperatureItems(self):
            # The strip's availability gates read the temperature
            # items first: without the key the gate refresh throws
            # and the readouts never render in the harness.
            return []

        @pyqtProperty(bool, notify=infoCollapsedChanged)
        def infoCollapsed(self):
            return self._info_collapsed

        @pyqtSlot(bool)
        def setInfoCollapsed(self, collapsed):
            self.info_calls.append(bool(collapsed))
            self._info_collapsed = bool(collapsed)
            self.infoCollapsedChanged.emit()

        @pyqtProperty(bool, notify=statusCollapsedChanged)
        def statusCollapsed(self):
            return self._status_collapsed

        @pyqtSlot(bool)
        def setStatusCollapsed(self, collapsed):
            self.status_calls.append(bool(collapsed))
            self._status_collapsed = bool(collapsed)
            self.statusCollapsedChanged.emit()

        @pyqtSlot(str, bool)
        def setSectionExpanded(self, section, expanded):
            self.section_calls.append((section, bool(expanded)))

        @pyqtProperty(bool, notify=controlsCollapsedChanged)
        def controlsCollapsed(self):
            return self._controls_collapsed

        @pyqtSlot(bool)
        def setControlsCollapsed(self, collapsed):
            self.controls_calls.append(bool(collapsed))
            self._controls_collapsed = bool(collapsed)
            self.controlsCollapsedChanged.emit()

        @pyqtSlot(bool)
        def setConsoleExpanded(self, expanded):
            pass

        @pyqtProperty("QVariant")
        def sectionExpandedMap(self):
            return {}

        @pyqtProperty("QVariant")
        def sectionHiddenMap(self):
            return {}

        @pyqtProperty(bool)
        def monitorConnected(self):
            return True

        @pyqtProperty(bool)
        def britishSpelling(self):
            return False

        @pyqtSlot(str, str)
        def setTemperatureSensorColor(self, sensor, colour):
            self.sensor_colors.append((sensor, colour))


    class OutputDeviceDouble(QObject):
        """The output device the monitor documents read as a context
        property, with the stage exit recorded: the Esc ladder's last
        rung leaves the monitor stage, and a null device would look the
        same whether or not that rung ran."""

        activePrinterChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self._printer = PrinterModelDouble()
            self.leaves = 0

        @pyqtProperty(QObject, notify=activePrinterChanged)
        def activePrinter(self):
            return self._printer

        @pyqtSlot()
        def leaveMonitorStage(self):
            self.leaves += 1


_APPLICATION = {"app": None, "engine": None, "theme": None, "messages": []}


def qml_error_report(component, tail=40):
    """A component's errors, readable.

    PyQt6's QQmlError has no usable __str__ — it falls back to the
    object repr, so a leg we cannot re-run locally reports four
    pointers instead of the message naming the offending file and
    line. toString() carries it. The captured Qt messages ride along:
    a component that returns None has usually logged its cause as a
    warning first (the tail, since a long file accumulates them)."""
    lines = [error.toString() for error in component.errors()]
    lines.extend(_APPLICATION["messages"][-tail:])
    return "\n".join(lines) or "<no errors reported>"


def qml_source(name):
    """A plugin document by name, wherever the tree files it.

    The QML nests by domain the way the Python does, and the nesting
    keeps changing. A test that wants a document should name the
    document: ``mount("CameraPane.qml")`` has to keep working after the
    file moves into ``monitor/``, or every future move rewrites the
    suite again — the same rule tests/source_root.py states for
    modules. Ambiguity raises rather than resolving to a guess.
    """
    matches = [path for path in sorted((ROOT / "mpf").rglob(name)) if path.name == name]
    if not matches:
        raise FileNotFoundError("no plugin document %r under mpf/" % name)
    if len(matches) > 1:
        raise AssertionError("%r is ambiguous under mpf/" % name)
    return matches[0]


def _start_application():
    """The application, engine and capture theme — built once for the
    whole file (the application cannot be replaced mid-process)."""
    if _APPLICATION["app"] is not None:
        return _APPLICATION["app"]
    if QCoreApplication.instance() is not None:
        raise unittest.SkipTest("the process already owns an application")
    sys.path.insert(0, str(ROOT / "tools"))
    from theme_support import ThemeBackend, materialise_theme_assets
    _APPLICATION["app"] = QGuiApplication([])
    _APPLICATION["theme"] = tempfile.mkdtemp(prefix="qml-probe-theme-")
    backend = ThemeBackend(str(ROOT / "tests" / "theme_assets" / "cura-light"))
    theme_tree = materialise_theme_assets(_APPLICATION["theme"], backend)
    engine = QQmlEngine()
    engine.addImportPath(str(ROOT / "tests" / "qml_stubs"))
    engine.addImportPath(str(ROOT / "mpf"))
    engine.addImportPath(theme_tree)
    _APPLICATION["cura"] = CuraApplicationDouble()
    engine.rootContext().setContextProperty("CuraApplication", _APPLICATION["cura"])
    engine.rootContext().setContextProperty("screenScaleFactor", 1.0)
    engine.rootContext().setContextProperty("OutputDevice", None)
    _APPLICATION["engine"] = engine
    qInstallMessageHandler(lambda message_type, context, message: _APPLICATION["messages"].append(str(message)))
    return _APPLICATION["app"]


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class RealEngineTestCase(unittest.TestCase):
    """Mounts plugin documents offscreen, with the capture theme."""

    @classmethod
    def setUpClass(cls):
        cls.app = _start_application()
        cls.engine = _APPLICATION["engine"]

    def setUp(self):
        self._message_start = len(_APPLICATION["messages"])

    def tearDown(self):
        result = getattr(self, "_result_for_shots", None)
        if result is not None and len(result.failures) + len(result.errors) > self._shot_failure_count:
            self._capture_failure_shots()
            self._shots_captured = True

    def pump(self, rounds=20):
        for _ in range(rounds):
            self.app.processEvents()

    def _pump_ms(self, milliseconds):
        """Real wall-clock pumping: the threaded canvases' paints and
        the QML timers (the 150 ms view settle) only advance with
        time — processEvents alone starves them."""
        deadline = time.monotonic() + milliseconds / 1000.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def _wait_until(self, window, predicate, timeout=15.0):
        """The first grab the predicate holds on.

        A wait that clears on one landmark while the caller asserts
        another measures the second on a frame its own landmark had not
        reached yet, so the predicate is the caller's assertions
        themselves. The timeout is a hang guard, not a budget: the
        caller's assertions are what fail when the ink never lands.
        """
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            if predicate(image):
                return image
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image

    def _settle_picture(self, window, face, timeout=15.0):
        """Pump, with grabs, until the full raster's texture is up.

        The full state's decode is off-thread (asynchronous: true), and
        the composition holds the standing picture until it lands: a
        sample taken a fixed distance after the install reads the HELD
        frame, so the settled composition needs this landmark. False on
        the cap means the caller's own assertion reads the held frame —
        the failure stays visible instead of being waited away.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if face.property("_rasterStatusReady"):
                return True
            self.app.processEvents()
            time.sleep(0.05)
            window.grabWindow()
        return bool(face.property("_rasterStatusReady"))

    def new_messages(self):
        return [message for message in _APPLICATION["messages"][self._message_start:]
                if "MoonrakerMonitor.qml" in message or "MoonrakerPreviewCard.qml" in message]

    def mount(self, filename):
        component = QQmlComponent(self.engine)
        component.loadUrl(QUrl.fromLocalFile(str(qml_source(filename))))
        document = component.create()
        self.assertIsNotNone(document, qml_error_report(component))
        if isinstance(document, QQmlComponent):
            # A Component-rooted document (MoonrakerMonitor.qml) creates
            # the component; the instance is one more call away.
            component = document
            document = component.create()
            self.assertIsNotNone(document, qml_error_report(component))
        self.addCleanup(self._delete_document, document)
        return document

    @staticmethod
    def _delete_document(document):
        from PyQt6 import sip
        if not sip.isdeleted(document):
            document.deleteLater()

    def find(self, document, object_name):
        item = document.findChild(QQuickItem, object_name)
        self.assertIsNotNone(item, object_name)
        return item

    @staticmethod
    def rect(item, base):
        return QRectF(item.mapToItem(base, QPointF(0.0, 0.0)), item.size())

    def mount_monitor(self, width, height=760):
        monitor = self.mount("MoonrakerMonitor.qml")
        monitor.setWidth(width)
        monitor.setHeight(height)
        self.pump()
        return monitor

    def mount_window(self, filename, width, height):
        """A document in a real window: a windowless mount lays out
        once and never again, so only a window replays what the live
        run does on every resize."""
        document = self.mount(filename)
        window = QQuickWindow()
        window.resize(width, height)
        document.setParentItem(window.contentItem())
        document.setWidth(width)
        document.setHeight(height)
        window.show()
        # Registered for the failure shots: the scene a failing test
        # shows is the scene IT opened, not whatever else is on screen.
        shot_windows = getattr(self, "_shot_windows", None)
        if shot_windows is None:
            shot_windows = self._shot_windows = []
        shot_windows.append(window)
        # The multi-test crash (engine-proven in the probe): a loaded
        # raster texture's teardown races the next mount unless the
        # window drains its frames hidden first — every window-mount
        # takes the safe teardown.
        self.addCleanup(self._destroy_window, document, window)
        self.pump(30)
        # A platform may lay the window out at a size it chose rather
        # than the one asked for, and the document follows — so every
        # census downstream measures a scene this helper never
        # requested. Reports rather than asserts: the point is to name
        # the actual geometry ONCE, in the leg that disagrees, instead
        # of leaving six pixel assertions to fail confusingly.
        got = (int(document.width()), int(document.height()),
               int(window.width()), int(window.height()))
        if got != (int(width), int(height), int(width), int(height)):
            sys.stderr.write(
                "mount_window: asked %dx%d, got document %dx%d window %dx%d\n"
                % (int(width), int(height), got[0], got[1], got[2], got[3]))
            sys.stderr.flush()
        _report_environment()
        return document, window

    def run(self, result=None):
        """Every failing real-engine test publishes the windows it had
        open, named for the test.

        A leg that disagrees about geometry — the Windows and macOS
        runners do — is diagnosable only by LOOKING at the scene, and
        an assertion message cannot say what the layout actually was.
        The count of failures before and after is the detection: it is
        version-stable, where poking at unittest's private outcome
        structures is not.

        Off unless HARNESS_SHOT_DIR names a directory, so ordinary
        local runs write nothing."""
        if result is None:
            result = self.defaultTestResult()
        before = len(result.failures) + len(result.errors)
        self._result_for_shots = result
        self._shot_failure_count = before
        self._shots_captured = False
        outcome = super().run(result)
        if not self._shots_captured and len(result.failures) + len(result.errors) > before:
            self._capture_failure_shots()
        return outcome

    def _capture_failure_shots(self):
        """The windows this test opened, as PNGs beside a manifest that
        names the test each one belongs to. Never raises: evidence that
        fails the run it is gathering evidence for is worse than none."""
        folder = os.environ.get("HARNESS_SHOT_DIR")
        if not folder:
            return
        try:
            os.makedirs(folder, exist_ok=True)
            from PyQt6.QtGui import QGuiApplication
            candidates = list(getattr(self, "_shot_windows", []))
            for window in QGuiApplication.topLevelWindows():
                if window not in candidates:
                    candidates.append(window)
            stem = "%s.%s" % (type(self).__name__, self._testMethodName)
            written = []
            # Newest first, and only a handful: a test that mounts a
            # window per stage accumulates dozens, and an artefact
            # nobody can review is not evidence. The last windows are
            # the scene the failure happened in.
            for index, window in enumerate(reversed(candidates[-6:])):
                grab = getattr(window, "grabWindow", None)
                if grab is None:
                    continue
                try:
                    image = grab()
                except RuntimeError:
                    continue  # a window the teardown already deleted
                if image is None or image.isNull():
                    continue
                path = os.path.join(folder, "%s-%d.png" % (stem, index))
                if image.save(path):
                    written.append(os.path.basename(path))
            if written:
                with open(os.path.join(folder, "manifest.txt"), "a",
                          encoding="utf-8") as handle:
                    handle.write("%s: %s\n" % (stem, ", ".join(written)))
                sys.stderr.write("harness shots: %s -> %s\n"
                                 % (stem, ", ".join(written)))
                sys.stderr.flush()
        except Exception:  # noqa: BLE001 - see the docstring
            pass

    def _settle_window(self, window):
        # The teardown's last binding evaluations must never wrap a
        # QObject: while the engine's property-cache registry dies,
        # a PlateLayer inside progress.layers.current is exactly the
        # wrap that segfaults it (QObjectWrapper::wrap ->
        # QQmlMetaType::propertyCache). Restore the plain-dict
        # payload FIRST — dicts wrap inertly — then drain hidden.
        printer = getattr(self, "_printer", None)
        printer = getattr(window, "_plate_printer", printer)
        if printer is not None and hasattr(printer, "setLayers"):
            printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
            self.pump(20)
        window.setProperty("visible", False)
        self.pump(60)

    def _destroy_window(self, document, window):
        # Drain destruction before an earlier cleanup restores the
        # production model's runtime modules or drops its Python owner.
        from PyQt6.QtCore import QCoreApplication, QEvent
        self._settle_window(window)
        self._delete_document(document)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def resize_window(self, document, window, width, height):
        window.resize(width, height)
        document.setWidth(width)
        document.setHeight(height)
        self.pump(20)

    def camera_pane(self, monitor):
        """The camera card in the monitor's middle column."""
        for item in monitor.findChildren(QQuickItem):
            if item.property("viewportWidth") is not None and item.property("contentHeight") is not None:
                return item
        self.fail("the camera card did not mount")

    def pause_card(self, items):
        """The preview card with a pause schedule, mounted in a window
        (a windowless ListView builds no delegates)."""
        card = self.mount("MoonrakerPreviewCard.qml")
        for name, value in (("gateVisible", True), ("previewStageActive", True),
                            ("configuredForFollowing", True), ("followingEnabled", True),
                            ("hasToolpath", True), ("pauseAtLayerActive", True),
                            ("pauseAtLayerItems", items)):
            card.setProperty(name, value)
        window = QQuickWindow()
        window.resize(800, 640)
        card.setParentItem(window.contentItem())
        card.setWidth(800)
        card.setHeight(640)
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(30)
        return card

    def pause_rows(self, card):
        """The rendered text of every pause row, in model order."""
        view = None
        for item in card.findChildren(QQuickItem):
            if item.property("count"):
                view = item
                break
        self.assertIsNotNone(view, "the pause list view did not build")
        content = view.property("contentItem")
        rows = []
        for row in content.childItems():
            labels = [child.property("text") for child in row.childItems()]
            rows.append(next((text for text in labels if text), ""))
        return [row for row in rows if row]


class StatusColumnGeometryTests(RealEngineTestCase):
    pass


class ConsoleInputRowTests(RealEngineTestCase):
    pass


class CollapseOnShrinkTests(RealEngineTestCase):
    """The squeeze latch on the LIVE path (the 2026-09-19 report: a
    slow window shrink stopped folding the panes). The camera column
    is the fixed one — it must never collapse — and the panes around
    it fold or yield as the window narrows."""


class ReExpansionGuardTests(RealEngineTestCase):
    """The narrow-window lock on re-expansion (the standing rule, frozen
    in INSTRUCTIONS.md): every decision hinges on the WEBCAM pane's own
    width. An expansion is refused while it would take the camera under
    its comfort minimum — on the monitor panes and on the dashboard's
    controls pane alike — and the wider stage that makes the room
    restores each pane in reverse fold order. The status pane folds
    after the information pane down the cascade; the camera pane stays
    open throughout."""

    def _mount(self, width=1100, document="MoonrakerMonitor.qml", seed_fans=None):
        class OutputDouble(QObject):
            activePrinterChanged = pyqtSignal()

            def __init__(self, printer):
                super().__init__()
                self._printer = printer

            @pyqtProperty(QObject, notify=activePrinterChanged)
            def activePrinter(self):
                return self._printer

        printer = PrinterModelDouble()
        if seed_fans is not None:
            printer._fan_items = list(seed_fans)
        # The double stays referenced from Python: the context property
        # alone does not keep it alive, and a collected double reads as
        # a null printer — every click then looks refused.
        self._output = OutputDouble(printer)
        self.engine.rootContext().setContextProperty("OutputDevice", self._output)
        self.addCleanup(self.engine.rootContext().setContextProperty, "OutputDevice", None)
        root, window = self.mount_window(document, width, 760)
        return root, window, printer

    def _monitor_in(self, dashboard):
        """The dashboard hosts the monitor document behind a Loader:
        the narrow-window state this contract reads lives on the loaded
        document, not on the host."""
        for item in dashboard.findChildren(QQuickItem):
            if (item.property("cameraViewportWidth") is not None
                    and item.property("infoCollapsed") is not None):
                return item
        self.fail("the dashboard's monitor document did not load")

    def _settle(self, root, window, width):
        """The offscreen layout re-runs only where the window's own size
        changes: after a model-driven pane change it keeps the widths it
        settled on, and a 1 px wobble is what makes it read the model
        (the harness quirk the dashboard probe documented)."""
        self.resize_window(root, window, width - 1, 760)
        self.resize_window(root, window, width, 760)

    def _shrink(self, monitor, window, low, high=1100):
        """The live slow shrink, with the standing camera rule checked
        at every step; returns the stage width each pane folded at."""
        camera = self.camera_pane(monitor)
        folds = {}
        for width in range(high, low - 1, -20):
            self.resize_window(monitor, window, width, 760)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            for name, flag in (("info", "infoCollapsed"), ("status", "statusCollapsed")):
                if name not in folds and monitor.property(flag):
                    folds[name] = width
        return folds

    def _click(self, item, window, x_ratio=0.5, y_ratio=0.5):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        point = item.mapToScene(QPointF(item.width() * x_ratio,
                                        item.height() * y_ratio)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        self.pump(10)

    def _toggle(self, monitor, pane, word):
        """The pane's collapse toggle: the visible button whose own
        tooltip names the pane (the panes' other header buttons — the
        configure glyph, the reconnect — carry no such text)."""
        for item in self.find(monitor, pane).findChildren(QQuickItem):
            if "Button" not in item.metaObject().className() or not item.isVisible():
                continue
            for child in item.findChildren(QQuickItem):
                text = child.property("text")
                if isinstance(text, str) and word in text:
                    return item
        self.fail("the %s collapse toggle did not build" % pane)

    def _message(self, monitor, pane, word):
        """The toggle's tooltip text — the 'say so' half of the guard."""
        for item in self.find(monitor, pane).findChildren(QQuickItem):
            text = item.property("text")
            if isinstance(text, str) and word in text:
                return text
        self.fail("the %s collapse toggle's tooltip did not build" % pane)


class CameraTitleRowTests(RealEngineTestCase):
    """The camera card's title row at the widths the folded row
    allocates for it: the refresh button keeps its place inside the
    pane (the crush report)."""

    # The camera column's widths through a shrink that has already
    # folded the Information pane: the row allocates 456 px at the fold
    # and 216 px at a 720 px window, and the refresh button measures 11
    # px clear of the pane's right edge at every one of them (it ran
    # over the border while the fold was broken). The row's own
    # minimum is reached at the host's 180 px camera floor, below the
    # folded range pinned here.
    FOLDED_WIDTHS = (456, 396, 336, 296, 256, 216)

    def _mount_pane(self, width):
        pane, window = self.mount_window("CameraPane.qml", width, 420)
        pane.setProperty("configured", True)
        pane.setProperty("printerModel", CameraModelDouble())
        self.pump(30)
        return pane, window

    def _refresh_button(self, pane):
        for item in pane.findChildren(QQuickItem):
            if "SimpleButton" in item.metaObject().className():
                return item
        self.fail("the refresh button did not build")


class PaneGutterTests(RealEngineTestCase):
    """The constant right gutter (the 1f68f08 ruling): the attached
    scroll bar overlays the 14 px the content keeps clear of its
    pane's right edge — in EVERY pane, so no pane's right gap reads
    double against its neighbours. The panes' content also stops
    following the bar's own visibility, which is what re-opened the
    gap after the ruling shipped."""

    def assert_gutter(self, pane, content, label):
        pane_rect = self.rect(pane, pane)
        content_rect = self.rect(content, pane)
        self.assertGreater(pane_rect.width(), 0.0, label)
        self.assertAlmostEqual(pane_rect.right() - content_rect.right(), 14.0,
                               delta=0.5, msg="%s: the right gap is not the gutter" % label)


class PauseRowRoleTests(RealEngineTestCase):
    SPARSE = [{"layer": 5}, {"layer": 7}, {"layer": 9}]
    MIXED = [{"layer": 5}, {"layer": 7, "eta": "in 00:02:00"},
             {"layer": 9, "state": "passed"}, {"layer": 12, "eta": None, "state": None, "passed": None}]

    def assert_roles_are_concrete(self, card):
        model = card.findChild(QObject, "moonrakerPauseListModel")
        self.assertIsNotNone(model)
        roles = {bytes(name).decode() for name in model.roleNames().values()}
        # A role whose FIRST value is undefined is dropped from the
        # model entirely, and the delegate's bare role lookup then
        # throws ReferenceError (eta, pauseWord and passed each did).
        self.assertEqual(roles, {"layerNo", "eta", "pauseWord", "passed"})


class StripVerdictRefreshTests(RealEngineTestCase):
    def verdict_card(self):
        card = self.mount("MoonrakerPreviewCard.qml")
        for name, value in (("previewBlock", {"state": "printing", "hotend": "205.2/210.0 C",
                                              "bed": "60.0/60.0 C", "inactive": False}),
                            ("previewBlockStale", False),
                            ("previewEtaText", "00:18:42 · ~14:36"),
                            ("stripCanPause", False), ("stripCanResume", False),
                            ("stripPauseReason", "Print is not printing"),
                            ("stripResumeReason", "Print is not paused")):
            card.setProperty(name, value)
        self.pump()
        return card


class SectionOrderArrivalTests(RealEngineTestCase):
    """The stored section order applies on the printer's ARRIVAL — the
    deterministic trigger: the panes' onCompleted ran before the model
    existed, so the arrival event must reorder them. This is the
    removed polling timer's regression pin.

    Two contracts, both measured: the SOURCE pin (the arrival-time
    apply lives in onPrinterChanged and no Timer waits for the model —
    the removed polling timer cannot come back silently), and the
    APPLY pin (invoked against a live model, the panes reorder). The
    notify delivery itself is Cura's C++ property, which the harness's
    machine-switch scenarios exercise — this fixture's Python-defined
    properties do not round-trip the engine's change detection."""

    @staticmethod
    def _header_order(container):
        """The rendered section ids of a pane's children, top to bottom."""
        order = []
        for child in container.childItems():
            header = child.childItems()[0] if child.childItems() else None
            if header is not None and header.property("sectionId") is not None:
                order.append(header.property("sectionId"))
        return order


class TuningResetTests(RealEngineTestCase):
    """The factor sliders' reset buttons command the printer to 100%
    through the same setSpeedFactor/setFlowFactor path the slider
    release uses (the camera refresh button's glyph and styling)."""


class TuningResetConvergenceTests(RealEngineTestCase):
    """The reset's end-to-end convergence: the click commands 100, the
    printer's confirmation publishes 100, and the SLIDER must read 100
    — never the to-clamp (the live 200-reset find)."""


class CameraOwnershipTests(RealEngineTestCase):
    """The camera start/stop ownership: one logical desired state,
    one application, at most one start/stop transition. The forked
    renderer's start() used to be destructive — it stopped the live
    reply first — so a duplicate application must never touch the
    image."""

    def _apply(self, pane, url, visible):
        from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant
        QMetaObject.invokeMethod(pane, "applyCamera",
                                 Q_ARG(QVariant, QUrl(url)), Q_ARG(QVariant, visible))

    def _mount_pane(self):
        pane = self.mount("CameraPane.qml")
        image = self.find(pane, "cameraImage")
        return pane, image

    def _counts(self, image):
        return (image.property("startCount"), image.property("stopCount"),
                image.property("sourceSetCount"))


class CameraFpsControlTests(RealEngineTestCase):
    """The camera control bar's QML surface (the idle-load and zoom
    requests): the status chip carries the decode rate and yields to the
    Live badge when the frame cannot carry both, one bar carries two
    faces — the zoom scale at rest, the rate scale while the throttle is
    being driven — the rate face's range is the selected camera's own
    ceiling with evenly spaced graduations, the bar parks clear of the
    picture after five idle seconds, the compact chip stands in where the
    bar has no room, and the wheel reaches the rate whether or not any
    control is on screen. The CPU saving itself is the renderer's own leg
    (test_moonraker_mjpg.py); the persistence is
    test_printer_config.py's."""

    def _fps_pane(self, width, height, fps=15.0, maximum=30.0, displayed_fps=0.0):
        """The card with a live frame and a recording FPS model. The
        bandwidth readout rides along: a live chip always carries it,
        and its width is what the occlusion rule has to reckon with.
        Returns (pane, window, model, image, frame) — the frame being
        the picture's own box, the box every FPS surface rides and the
        clip they are held to."""
        pane, window = self.mount_window("CameraPane.qml", width, height)
        pane.setProperty("configured", True)
        model = CameraModelDouble(fps=fps, maximum=maximum)
        pane.setProperty("printerModel", model)
        self.pump(30)
        image = self.find(pane, "cameraImage")
        image.setProperty("visible", True)
        image.setProperty("imageWidth", 640)
        image.setProperty("imageHeight", 480)
        image.setProperty("recentBytesPerSec", 1375000)
        image.setProperty("recentDisplayedFPS", displayed_fps)
        # The first layout re-seats the parked controls through their
        # 180 ms slide: let it land before any position is measured.
        self._pump_ms(300)
        return pane, window, model, image, self.find(pane, "cameraFrame")

    def _wheel(self, window, item, delta=120, modifiers=None, position=None,
               pixels=None, horizontal_pixels=0):
        """One real wheel notch over *item*. QTest's QWindow-level
        mouseWheel is unavailable in this Qt build — the event is posted
        directly (the plate zoom suite's own pattern). No modifier is
        the picture's own gesture (the zoom); Shift is the FPS
        throttle's."""
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtGui import QWheelEvent
        if position is None:
            position = QPointF(item.width() / 2, item.height() / 2)
        scene = item.mapToItem(window.contentItem(), position)
        event = QWheelEvent(
            QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            QPoint(horizontal_pixels, pixels if pixels is not None else 0),
            QPoint(0, 0 if pixels is not None or horizontal_pixels else delta),
            Qt.MouseButton.NoButton,
            modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)

    def _mouse(self, window, item, kind, x, y, buttons=None, button=None):
        """One real mouse event at item-local (x, y) — the drag pan's
        own path (the plate face suite's idiom). *button* is the button
        the event is ABOUT.

        A press must carry its own button or the delivery agent files it
        as a bare update; a MOVE must carry NONE, because a move that
        names a button reads as a fresh press (`isBeginEvent`), and Qt
        then re-runs target selection mid-drag — which hands the grab to
        whatever animated control has arrived under the pointer and
        stops the pan the drag was driving. The buttons still HELD ride
        *buttons*, which is what a move is classified by.
        """
        from PyQt6.QtCore import QEvent, QPoint, Qt
        from PyQt6.QtGui import QMouseEvent
        # EVERY move, whatever the caller names for it: a right drag
        # passes RightButton for the event's button AND the held mask,
        # and taking that at face value built the same malformed event a
        # defaulted left move did. Press and release keep their own
        # changed button — that is what they are about.
        if kind == QEvent.Type.MouseMove:
            button = Qt.MouseButton.NoButton
        elif button is None:
            button = Qt.MouseButton.LeftButton
        if buttons is None:
            buttons = Qt.MouseButton.NoButton
        scene = item.mapToItem(window.contentItem(), QPointF(x, y))
        event = QMouseEvent(kind, QPointF(scene),
                            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                            button, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QGuiApplication.sendEvent(window, event)
        return event

    def _drag(self, window, item, dx, dy):
        """Press at the item's centre, move by (dx, dy), release."""
        from PyQt6.QtCore import QEvent, Qt
        cx, cy = item.width() / 2, item.height() / 2
        self._mouse(window, item, QEvent.Type.MouseButtonPress, cx, cy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, item, QEvent.Type.MouseMove, cx + dx, cy + dy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, item, QEvent.Type.MouseButtonRelease, cx + dx, cy + dy)
        self.pump(20)

    def _fps_face(self, window, wheel_target):
        """Bring the RATE face up. The bar rests on the zoom face; the
        shift wheel is the rate's own gesture, and a rate change docks
        the face it belongs to. A parked bar (every mount starts parked)
        arrives on the new face at once."""
        self._wheel(window, wheel_target, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self._pump_ms(300)

    def _double_click(self, window, item):
        """A real double click on *item*, through QTest's own app-level
        path. A DblClick posted by hand with no press outstanding is an
        UPDATE event to the delivery agent — the legacy MouseArea never
        sees it and the item's onDoubleClicked never runs (probe-verified
        on both engines), so the QTest helper is the only spelling that
        reaches it."""
        from PyQt6.QtCore import QPoint
        from PyQt6.QtTest import QTest
        scene = item.mapToItem(window.contentItem(), QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseDClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                          QPoint(int(scene.x()), int(scene.y())))
        self.pump(30)


    def _rate_drag(self, window, item, dy, x_ratio=0.5, y_ratio=0.5, steps=1,
                   release=True):
        """A real RIGHT-button drag: press, *steps* moves of dy pixels
        each, release. The rate's own gesture — the left button pans,
        and the rate must be reachable over the picture whether or not
        any control is on screen. The whole track must stay inside the
        item: a synthetic move past its own edge is not delivered to it
        (the harness's own condition, probe-verified — a real platform
        drag holds the grab and leaves the item freely), so a track that
        ran off would silently lose its tail and read as a dropped
        gesture."""
        from PyQt6.QtCore import QEvent, Qt
        x, y = item.width() * x_ratio, item.height() * y_ratio
        self.assertGreaterEqual(y, 0, "the drag must start on the item")
        self.assertLessEqual(y + dy * steps, item.height(),
                             "the drag's whole track must stay on the item")
        self._mouse(window, item, QEvent.Type.MouseButtonPress, x, y,
                    Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        for step in range(1, steps + 1):
            self._mouse(window, item, QEvent.Type.MouseMove, x, y + dy * step,
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        self.pump(20)
        if release:
            self._mouse(window, item, QEvent.Type.MouseButtonRelease, x, y + dy * steps,
                        Qt.MouseButton.NoButton, Qt.MouseButton.RightButton)
            self.pump(20)


class EscapeLadderTests(RealEngineTestCase):
    """Esc on the Monitor page (the live report: the key did nothing at
    all once the console output had been clicked into). The page keeps ONE
    ladder, and a press reaches it by two routes. With nothing focused the
    window shortcut carries the key; with a TEXT item focused the engine
    in the field answers Escape inside that item and the shortcut never
    fires at all — which is the whole bug, and why the press also has to
    bubble up the item chain to the page's own handler. The container's
    engine (Qt 6.11) cannot reproduce that steal — its shortcut fires
    either way (probe-verified, Cura ships Qt 6.6) — so the test disables
    the shortcut to stand in the field engine's shoes, and then checks
    that one press is one rung with it back on."""

    def _dashboard(self, width=1250, height=760):
        output = OutputDeviceDouble()
        previous = self.engine.rootContext().contextProperty("OutputDevice")
        self.engine.rootContext().setContextProperty("OutputDevice", output)
        self.addCleanup(self.engine.rootContext().setContextProperty,
                        "OutputDevice", previous)
        dashboard, window = self.mount_window("MoonrakerMonitorDashboard.qml", width, height)
        self._pump_ms(400)
        return dashboard, window

    def _loaded_monitor(self, dashboard):
        """The monitor document the dashboard hosts — it declares no
        objectName, so its own pop-over property is the handle."""
        def search(item):
            if item.property("openPopOver") is not None:
                return item
            for child in item.childItems():
                found = search(child)
                if found is not None:
                    return found
            return None
        monitor = search(dashboard)
        self.assertIsNotNone(monitor, "the hosted monitor document did not load")
        return monitor

    def _escape_shortcut(self, dashboard):
        """The page's own window shortcut, reachable only as a QObject."""
        from PyQt6.QtGui import QKeySequence

        for child in dashboard.findChildren(QObject):
            if child.metaObject().className() != "QQuickShortcut":
                continue
            sequence = child.property("sequence")
            if sequence is not None and QKeySequence(sequence) == QKeySequence(Qt.Key.Key_Escape):
                return child
        self.fail("the page's Esc shortcut did not mount")

    def _press(self, dashboard, window, monitor, armed):
        """One Escape through QTest's app-level path, with two layers
        stacked under the ladder: one rung must run, so the monitor's
        pop-over closes and this pane's pop-up stays."""
        from PyQt6.QtTest import QTest

        monitor.setProperty("openPopOver", "sections-status")
        dashboard.setProperty("configurePaneOpen", "controls")
        self.pump(30)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.pump(60)
        if armed:
            self.assertEqual(monitor.property("openPopOver"), "",
                             "the top layer closes")
        else:
            self.assertEqual(monitor.property("openPopOver"), "sections-status",
                             "the ladder never ran")
        self.assertEqual(dashboard.property("configurePaneOpen"), "controls",
                         "one press is one rung, whichever route carried it")


class ChartSurfaceTests(RealEngineTestCase):
    """The temperature chart's rendering architecture: the data canvas
    must request the threaded image strategy (read back through the
    engine — the offscreen probe platform never drives a render pass,
    so the painted-size properties cannot testify here; the capture
    census leg renders the chart's actual pixels in this same
    container), the paint's input must be the main-thread plain-data
    snapshot, and the hover surface must be scene-graph geometry whose
    publications never request a canvas paint."""

    def chart_payload(self, with_tracks=True):
        series = [{
            "name": "extruder", "label": "Extruder", "color": "#d32f2f",
            "visible": True, "primary": True,
            "points": [[float(tick), 200.0 + tick * 0.1] for tick in range(40)],
            "bounds": {"tempMin": 200.0, "tempMax": 204.0,
                       "elapsedMin": 0.0, "elapsedMax": 39.0},
        }]
        if with_tracks:
            series[0]["targets"] = [[[0.0, 210.0], [39.0, 210.0]]]
            series[0]["powers"] = [[[float(tick), 0.5] for tick in range(40)]]
        return {
            "series": series,
            "showTargets": True,
            "showPower": True,
            "palette": [],
            "filling": False,
            "wallOrigin": 100000.0,
        }

    def mount_chart(self, compact=False, with_tracks=True):
        chart = self.mount("TemperatureChart.qml")
        chart.setProperty("compact", compact)
        chart.setProperty("chart", self.chart_payload(with_tracks))
        window = QQuickWindow()
        window.resize(800, 500)
        chart.setParentItem(window.contentItem())
        chart.setWidth(800)
        chart.setHeight(500)
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(30)
        return chart, window

    @staticmethod
    def enum_value(item, enumerator, key):
        """The enum value QML's `key` resolves to, from the item's own
        QMetaObject — PyQt6 cannot convert the C++ enum instance that
        property() returns, and Qt 6 reordered the Canvas enums, so
        hard-coded values would rot. keyToValue returns (value, ok)."""
        meta = item.metaObject()
        for index in range(meta.enumeratorCount()):
            enum = meta.enumerator(index)
            if enum.name() == enumerator:
                return enum.keyToValue(key)[0]
        raise AssertionError("no enumerator %s" % enumerator)

    @staticmethod
    def read_js(engine, item, expression):
        """A QML-side property read as (value, isUndefined): PyQt6's
        QQmlExpression returns that pair, and this is the only read
        path that survives the C++ enum instance."""
        from PyQt6.QtQml import QQmlExpression

        js = QQmlExpression(engine.rootContext(), item, expression)
        return js.evaluate()


    def canvas_messages(self):
        return [message for message in self.new_messages()
                if "Canvas" in message or "canvas" in message]


if QT_AVAILABLE:
    class PlatePrinterDouble(QObject):
        """The plate surfaces' printer double. Module-level inside the
        guard (the house pattern): a function-local QObject class's
        meta-object fails the QML var write — the engine stores null
        and the popover never opens."""

        plateSplitChanged = pyqtSignal()
        plateDotChanged = pyqtSignal()
        plateObjectsChanged = pyqtSignal()
        plateLayersChanged = pyqtSignal()
        plateScrubVectorChanged = pyqtSignal()
        # The follower's own publish groups, mirroring the model's
        # _SIGNAL_KEYS: the anchor rides the plate group, the follow
        # state and the option ride the view group.
        plateProgressChanged = pyqtSignal()
        followerViewChanged = pyqtSignal()
        pauseAtLayerChanged = pyqtSignal()
        improvingEtaChanged = pyqtSignal()

        # The picker's own payload: a test installs one before it mounts
        # (the class-attribute pattern the follower half's PAYLOAD uses).
        PLATE = None
        BED = (250.0, 250.0)

        def __init__(self):
            super().__init__()
            self._split = PlateFaceRenderTests.PAYLOAD["split"]
            self._anchor = int(PlateFaceRenderTests.PAYLOAD["anchor"])
            self._anchor_eta = ""
            self._layers = PlateFaceRenderTests.PAYLOAD["layers"]
            self._scrub = None
            self.scrub_reads = 0
            self._navigation = ""
            # Zero is the whole layer — what the property's absence
            # stood for before it existed, so a fixture that never sets
            # it measures the frame it always measured.
            self._navigation_split = 0
            self._navigation_backing = 4.0
            self._layer_count = 40
            self._index_ready = True
            self._progress_available = True
            self._progress_reason = ""
            self._source_status = ""
            self._source_progress = -1.0
            self._motion_count = 21
            self._dot = {"x": 125.0, "y": 125.0, "valid": True}
            self._attached = True
            self._layer_anchor = -1
            self._improving_eta = False
            self._show_base = True
            self._show_axis_arrows = True
            self._pause_clear_available = False
            self.calls = []
            self.asset_owners = {}
            self.asset_serial = 0
            self._plate = PlatePrinterDouble.PLATE if PlatePrinterDouble.PLATE is not None else {
                "objects": [
                    {"name": "Widget", "center": [125.0, 125.0],
                     "polygon": [[0.0, 0.0], [250.0, 0.0], [250.0, 250.0], [0.0, 250.0]],
                     "current": False, "excluded": False, "restoreAllowed": True},
                ]
            }
        @pyqtSlot(str, float, float, int, int, bool, float, float, float, float)
        def setFollowerView(self, surface, scale, line_scale, width, height,
                            compact, pan_x, pan_y, backing, width_px):
            self.last_follower_view = (surface, scale, line_scale, width, height,
                                       compact, pan_x, pan_y, backing, width_px)

        @pyqtSlot()
        def seekAnchorTicked(self):
            # The debounce's raw tick (the model stamps it for the
            # trace); the double records nothing.
            pass

        @pyqtProperty(float, constant=True)
        def bedMeshMachineWidth(self):
            return self.BED[0]

        @pyqtProperty(float, constant=True)
        def bedMeshMachineDepth(self):
            return self.BED[1]

        @pyqtProperty(bool, constant=True)
        def bedMeshCenterIsZero(self):
            return False

        @pyqtProperty("QVariant", notify=plateLayersChanged)
        def plateLayers(self):
            return self._layers

        def setLayers(self, layers):
            """Install a window payload after the mount (the native
            PlateLayer fixtures need the face's own plot first)."""
            self._layers = layers
            self.plateLayersChanged.emit()

        @pyqtProperty("QVariant", notify=plateScrubVectorChanged)
        def plateScrubVector(self):
            # The production partial state publishes the scrub
            # vector beside the PlateLayer window — the face's
            # _scrubVector reads it here.
            self.scrub_reads += 1
            return self._scrub

        def setScrub(self, scrub):
            self._scrub = scrub
            self.plateScrubVectorChanged.emit()

        @pyqtProperty(str, notify=plateLayersChanged)
        def plateNavigationData(self):
            # The interaction raster's ready URL (the model's warm
            # background composite — the fixture sets a real PNG).
            return self._navigation

        def setNavigation(self, url):
            self._navigation = url or ""
            self.plateLayersChanged.emit()

        @pyqtProperty("QVariant", notify=plateLayersChanged)
        def plateNavigationSplit(self):
            # The warm raster's own split: the carried tail repaints
            # only the interval above it. Zero is the whole layer —
            # the definition the absent property used to stand for,
            # so a fixture that never sets it measures what it always
            # did.
            return self._navigation_split

        @pyqtProperty("QVariant", notify=plateLayersChanged)
        def plateNavigationBacking(self):
            return self._navigation_backing

        def setNavigationSplit(self, split, backing=4.0):
            self._navigation_split = int(split)
            self._navigation_backing = float(backing)
            self.plateLayersChanged.emit()

        def setSplit(self, split):
            """Move the printed boundary: the layers are the mount's
            fixed half, the split is the volatile half every poll
            rewrites (the model emits the same change)."""
            self._split = int(split)
            self.plateSplitChanged.emit()

        @pyqtProperty(int, notify=plateSplitChanged)
        def plateSplit(self):
            return self._split

        @pyqtProperty(int, notify=plateProgressChanged)
        def plateProgressAnchor(self):
            return self._anchor

        @pyqtProperty(str, notify=plateProgressChanged)
        def plateAnchorEta(self):
            return self._anchor_eta

        def setAnchorEta(self, text):
            """The coordinator's anchor estimate lands: the popover's
            Layer ETA row follows it."""
            self._anchor_eta = str(text)
            self.plateProgressChanged.emit()

        def setAnchor(self, anchor):
            """Move the served layer: the follow's own publish."""
            self._anchor = int(anchor)
            self.plateProgressChanged.emit()

        @pyqtProperty(int, notify=plateProgressChanged)
        def plateLayerCount(self):
            return self._layer_count

        @pyqtProperty(bool, notify=plateProgressChanged)
        def printIndexReady(self):
            return self._index_ready

        def setIndexState(self, ready, count):
            self._index_ready = bool(ready)
            self._layer_count = int(count)
            self.plateProgressChanged.emit()

        def setProgressState(self, available, reason):
            self._progress_available = bool(available)
            self._progress_reason = str(reason)
            self.plateProgressChanged.emit()

        @pyqtProperty(int, constant=True)
        def plateLayerMotionCount(self):
            return self._motion_count

        @pyqtProperty(bool, notify=plateProgressChanged)
        def plateProgressAvailable(self):
            return self._progress_available

        @pyqtProperty(str, notify=plateProgressChanged)
        def plateProgressReason(self):
            return self._progress_reason

        @pyqtProperty(str, notify=plateProgressChanged)
        def plateSourceStatus(self):
            return self._source_status

        @pyqtProperty(bool, notify=plateProgressChanged)
        def plateSourceBusy(self):
            return self._source_status in (
                "Resolving G-code for precise tracking",
                "Downloading G-code for precise tracking")

        @pyqtProperty(bool, notify=plateProgressChanged)
        def plateSourceResolving(self):
            return self._source_status == "Resolving G-code for precise tracking"
        @pyqtProperty(float, notify=plateProgressChanged)
        def plateSourceProgress(self):
            return self._source_progress

        def setSourceDownload(self, status, progress):
            self._source_status = status
            self._source_progress = progress
            self.plateProgressChanged.emit()

        @pyqtProperty(bool, notify=improvingEtaChanged)
        def improvingEta(self):
            # The load gate `PlateDownloadAction.busy()` reads, declared
            # for the same reason production's model declares it: a
            # property a QML binding cannot resolve reads as undefined,
            # and a property animation whose `running:` binding fails to
            # resolve keeps its own default — the action's two infinite
            # animations then run for as long as the face is mounted.
            return self._improving_eta

        @pyqtProperty(float, notify=improvingEtaChanged)
        def improveEtaProgress(self):
            return -1.0

        @pyqtProperty(str, notify=improvingEtaChanged)
        def improveEtaPhase(self):
            return ""

        def setImprovingEta(self, improving):
            """A load starting or finishing: the action's animations and
            the phase row's own bindings follow the gate."""
            improving = bool(improving)
            if improving == self._improving_eta:
                return
            self._improving_eta = improving
            self.improvingEtaChanged.emit()

        @pyqtProperty("QVariant", notify=plateDotChanged)
        def plateDot(self):
            return self._dot

        def setDot(self, x, y, valid=True):
            """The toolhead moves: the follow's per-poll clock."""
            self._dot = {"x": float(x), "y": float(y), "valid": bool(valid)}
            self.plateDotChanged.emit()

        @pyqtProperty(bool, notify=followerViewChanged)
        def followerAttached(self):
            return self._attached

        @pyqtProperty(int, notify=followerViewChanged)
        def followerLayerAnchor(self):
            return self._layer_anchor

        @pyqtProperty(bool, notify=followerViewChanged)
        def followerShowBase(self):
            return self._show_base

        @pyqtProperty(bool, notify=followerViewChanged)
        def followerShowAxisArrows(self):
            return self._show_axis_arrows

        @pyqtSlot(bool)
        def setFollowerShowAxisArrows(self, show):
            self._show_axis_arrows = show
            self.followerViewChanged.emit()

        @pyqtProperty(bool, notify=pauseAtLayerChanged)
        def pauseAtLayerHasClearable(self):
            return self._pause_clear_available

        def setPauseClearAvailable(self, available):
            self._pause_clear_available = available
            self.pauseAtLayerChanged.emit()

        @pyqtProperty(float, notify=followerViewChanged)
        def followerTravelVisualRatio(self):
            # The MODEL publishes the parity constant: the face's
            # travel stroke arithmetic (`toolpathWidthPx() *
            # travelVisualRatio`) and the renderer's own travels pen
            # read ONE number, so the canvas' travels and the travels
            # raster are the same width. Without it here the face falls
            # back to `lineScale` — 8.0 in these fixtures against the
            # renderer's 0.7 — and the canvas' travel run then runs 12
            # px past the raster's at EACH end: whichever producer the
            # frame happened to hold changed the run's length by 25 px,
            # which is what made the pan travels pin red on some runs
            # and green on others.
            return _PLATE_TRAVEL_VISUAL_RATIO

        @pyqtSlot(bool)
        def setFollowerShowBase(self, show):
            self.calls.append(("showBase", bool(show)))
            if self._show_base == bool(show):
                return
            self._show_base = bool(show)
            self.followerViewChanged.emit()

        # The model's own slots, mirrored: the double's state follows
        # the same rules so the controls' surface is a real state
        # machine (the freeze, the rejoin, the manual anchor).
        @pyqtSlot(bool)
        def setFollowerAttached(self, attached):
            self.calls.append(("attached", bool(attached)))
            attached = bool(attached)
            if attached == self._attached:
                return
            if attached:
                self._attached = True
                self._layer_anchor = -1
            else:
                frozen = self._layer_anchor if self._layer_anchor >= 0 else self._anchor
                if frozen < 0:
                    if self._layer_count <= 0 and not self._index_ready:
                        return
                    frozen = 0
                self._attached = False
                self._layer_anchor = frozen
                self._anchor = frozen
            self.followerViewChanged.emit()
            self.plateProgressChanged.emit()

        @pyqtSlot(int)
        def setFollowerLayerAnchor(self, layer):
            self.calls.append(("layer", int(layer)))
            if layer < 0:
                return
            self._attached = False
            self._layer_anchor = int(layer)
            self._anchor = int(layer)
            self.followerViewChanged.emit()
            self.plateProgressChanged.emit()

        @pyqtSlot(int)
        def setFollowerLayerProgress(self, motions):
            self.calls.append(("progress", int(motions)))
            if self._attached:
                # Production semantics: the scrub from the live layer
                # is itself the detach — the anchor freezes where the
                # print stood (the P0 zero-index contract).
                self._attached = False
                self._layer_anchor = self._anchor
                self.followerViewChanged.emit()
            self._split = int(motions)
            self.plateProgressChanged.emit()

        @pyqtSlot(bool)
        def setFollowerPopoverOpen(self, popover_open):
            self.calls.append(("followerOpen", bool(popover_open)))

        @pyqtSlot(bool)
        def setPickerPopoverOpen(self, popover_open):
            self.calls.append(("pickerOpen", bool(popover_open)))

        # The camera gesture's freeze pair: the face calls these on
        # entry and on every exit, and an undefined slot aborts the
        # calling handler (the slider's commit sat after endInteraction).
        @pyqtSlot(bool)
        def setFollowerInteracting(self, interacting):
            self.calls.append(("interacting", bool(interacting)))

        # The gesture's file hold: the face names the navigation
        # raster it is presenting, so a mid-gesture supersede cannot
        # unlink it. An undefined slot aborts the calling handler.
        @pyqtSlot(str)
        def setFollowerGestureRaster(self, url):
            self.calls.append(("gestureRaster", str(url or "")))

        @pyqtSlot(result=int)
        def acquirePlateAssetOwner(self):
            self.asset_serial += 1
            self.asset_owners[self.asset_serial] = []
            return self.asset_serial

        @pyqtSlot(int, "QVariantList")
        def setPlateAssetReferences(self, owner, urls):
            self.asset_owners[owner] = list(urls)

        @pyqtSlot(int)
        def releasePlateAssetOwner(self, owner):
            self.asset_owners.pop(owner, None)

        # The face's barrier report: the production model writes it with
        # Logger.log, because Cura's QML handler carries warnings only.
        @pyqtSlot(str)
        def followerHoldReport(self, text):
            self.calls.append(("holdReport", str(text or "")))

        @pyqtSlot()
        def setFollowerGestureBake(self):
            self.calls.append(("gestureBake", None))

        @pyqtProperty("QVariant", notify=plateObjectsChanged)
        def plateObjects(self):
            return self._plate

        @pyqtSlot(str)
        def excludeObject(self, name):
            """The picker's destructive command: the row takes the
            verdict and the payload republishes, as the model does."""
            self.calls.append(("exclude", name))
            self._set_excluded(name, True)

        @pyqtSlot(str)
        def restoreObject(self, name):
            self.calls.append(("restore", name))
            self._set_excluded(name, False)

        def _set_excluded(self, name, excluded):
            for row in self._plate.get("objects", []):
                if row["name"] == name:
                    row["excluded"] = bool(excluded)
            self.plateObjectsChanged.emit()

        @pyqtProperty(bool, constant=True)
        def plateHasObjects(self):
            return True

        @pyqtProperty("QVariant", constant=True)
        def sectionHiddenMap(self):
            return {}

        @pyqtProperty("QVariant", constant=True)
        def sectionExpandedMap(self):
            return {}

        @pyqtProperty("QVariant", constant=True)
        def temperatureChartLegend(self):
            return {"series": [], "showPower": True}


class ChartColourPickerTests(RealEngineTestCase):
    """The chart's colour picker is built on the first click, never at
    load. Its dialog comes out of a platform module (QtQuick.Dialogs):
    the Windows CI leg mounts the monitor on a Qt whose platform cannot
    build one (probe-proven by hiding the module — the mount returned
    None while every other document mounted), so a display document must
    not need that module to construct. The quick swatches stay usable
    without it, which is what makes the deferral safe."""

    # The monitor's mount wiring (a recording printer behind the output
    # device context property) is the same for every monitor contract.
    _mount = ReExpansionGuardTests._mount

    @staticmethod
    def chart_card(document):
        """The card that owns the picker's build-on-first-click
        lifecycle. The picker's build and reuse are the CARD's
        behaviour now; the host only frames it."""
        card = document.findChild(QObject, "moonrakerChartDetail")
        assert card is not None, "moonrakerChartDetail"
        return card

    @staticmethod
    def colour_pickers(document):
        """Every colour dialog under a document. The dialog's own
        selectedColor marks it: nothing else in the tree exposes that
        property, and the platform's dialog class is named per host (the
        fallback is an anonymous QQuickItem subclass), so a class-name
        lookup would be a host assumption of its own."""
        pickers = []
        for child in document.findChildren(
                QObject, options=Qt.FindChildOption.FindChildrenRecursively):
            meta = child.metaObject()
            if any(meta.property(index).name() == "selectedColor"
                   for index in range(meta.propertyCount())):
                pickers.append(child)
        return pickers


class PlateFaceRenderTests(RealEngineTestCase):
    """The plate family's painted contracts: the follower fills its
    plot with sample geometry pinned to the bed's extreme corners (a
    truncated or mis-signed mapping cannot hide — the live crushed
    strip report), the picker's canvas never reflows on hover, and
    the picker draws no toolhead dot."""

    # A 250 mm bed; the layer strokes the full bed rectangle, corners
    # included (the live instruction: corner-pinned sample geometry).
    CORNER_PAYLOAD = {
        "available": True, "reason": "",
        "layers": {
            "prev": None,
            "current": {
                "classes": {
                    # The travel-broken segment shape: one segment.
                    "WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                    [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                    [0.0, 0.0, 20.0]]],
                },
                "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": 21,
            },
            "next": None,
        },
        "split": 12, "method": "motion index", "anchor": 0,
    }

    # The double reads this one attribute, so a test can install a
    # payload of its own before it mounts its window (the face binds its
    # payload once per mount).
    PAYLOAD = CORNER_PAYLOAD

    @staticmethod
    def _printer():
        return PlatePrinterDouble()

    def _open(self, monitor, popover):
        # The reference is RETAINED: a Python-created QObject dies
        # with its last Python ref (the QML var takes no ownership),
        # and the monitor's printer dangles null — no plot, no dot.
        self._printer = type(self)._printer()
        # A parity test mounts several windows. Keep each window's
        # Python-owned model alive until that window has drained; QML
        # QVariant references do not own these Python QObjects.
        fixtures = getattr(self, "_plate_printers", None)
        if fixtures is None:
            fixtures = self._plate_printers = []
        fixtures.append(self._printer)
        for window in getattr(self, "_shot_windows", []):
            if monitor.parentItem() is window.contentItem():
                window._plate_printer = self._printer
                break
        monitor.setProperty("printer", self._printer)
        monitor.setProperty("openPopOver", popover)
        self.pump(30)

    @staticmethod
    def _popover_faces(monitor, name):
        # The follower has a compact mini in the section; the popover
        # instance is the non-compact one.
        return [face for face in monitor.findChildren(QQuickItem, name)
                if not face.property("compact")]

    @staticmethod
    def _ink_rows(image, face, window):
        """Every 2 px row that carries a pixel differing from the
        face's background — the threaded canvas's painted ink."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def pixel(col, row):
            return image.pixel(int(origin.x()) + col, int(origin.y()) + row)

        background = pixel(4, 4)
        rows = []
        for row in range(0, int(face.height()), 2):
            for col in range(0, int(face.width()), 2):
                value = pixel(col, row)
                if any(abs(((value >> shift) & 0xFF) - ((background >> shift) & 0xFF)) > 20
                       for shift in (0, 8, 16)):
                    rows.append(row)
                    break
        return rows

    def _grab_when_inked(self, window, face, top=None, timeout=15.0):
        # The timeout is a hang guard, not a budget: on a starved
        # runner the threaded raster legitimately takes longer than a
        # couple of seconds, and the caller's own ink assertion is
        # what fails when it never lands.
        # With a top edge: the wait targets the plot's top band — the
        # scene-graph dot inks instantly and must not satisfy the
        # wait before the threaded strokes land.
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            rows = self._ink_rows(image, face, window)
            if rows and (top is None or min(rows) <= top + 12):
                return image
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image

    def _native_layer(self, payload, face, prefix_split=None, dpr=1.0,
                      line_scale=8.0, grey=True, pan_x=0.0, scale=1.0,
                      pan_y=0.0, layer_type=None):
        """A REAL PlateLayer whose rasters the native renderer
        painted with the face's own mapping — the production object
        the plain-dict fixtures never provide: no .classes, so the
        scrub vector's fallback cannot rescue a missing blit.
        `grey=False` leaves the base sibling unset — the pre-arrival
        wrapper the fallback exists for."""
        from mpf.plate.PlateQt import PlateLayer, png_file, render_layer_prefix, render_layer_raster
        raster_dir = "/tmp/mpf/raster-probe"
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        # A THICK lineScale by default: the physical stroke at 100%
        # zoom is legitimately sub-pixel (the live ruling), which a
        # colour census cannot see — the fixture paints its proofs
        # solid. The parity test passes the production 0.7.
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": scale, "lineScale": line_scale, "compact": False,
                "panX": pan_x, "panY": pan_y, "dpr": dpr,
                # The model's own view does this (`_surface_view`): the
                # renderer's travel pen takes the ratio EXPLICITLY, from
                # the same constant the printer double publishes to the
                # face — one number, two channels, as production has it.
                "travelVisualRatio": _PLATE_TRAVEL_VISUAL_RATIO}
        if face.property("pixelLineWidth"):
            view["lineWidthPx"] = line_scale
        PlateFaceRenderTests._raster_stem = getattr(
            PlateFaceRenderTests, "_raster_stem", 0) + 1
        stem = "fixture-%d-%d" % (os.getpid(), PlateFaceRenderTests._raster_stem)
        layer = (layer_type or PlateLayer)(payload)
        coloured, base, travels = render_layer_raster(payload, plot, view)
        layer.set_raster(coloured, "fixture-key",
                         png_file(coloured, raster_dir, stem + "-c"))
        layer.set_expected_key("fixture-key")
        if grey and base.width() > 0:
            layer.set_base(base, "fixture-key",
                           png_file(base, raster_dir, stem + "-b"))
        if travels.width() > 0:
            layer.set_travels(travels, "fixture-key",
                              png_file(travels, raster_dir, stem + "-t"))
        if prefix_split is not None:
            prefix = render_layer_prefix(payload, plot, view, prefix_split)
            layer.set_prefix(prefix, png_file(prefix, raster_dir, stem + "-p"),
                             prefix_split, "fixture-key")
        return layer

    def _slow_raster(self, url, tag, factor=6):
        """The same PNG at an integer multiple of its size — the SLOW
        source, for sampling a window too short to sample.

        `anchors.fill` with the default Stretch and `smooth: false`
        presents a nearest-neighbour downscale of a nearest-neighbour
        upscale, which is the identity: the composed picture cannot
        change by a pixel, only the decode's length. A raster at the
        face's own size decodes in a few ms — no sampling can catch the
        state inside that, so the fixture lengthens the same decode to
        the hundreds of ms a cold bake's own (encoded at the transport's
        sizes and read back off disk) can take on a loaded host."""
        if factor <= 1:
            return url
        from PyQt6.QtGui import QImage
        source = QImage(QUrl(url).toLocalFile())
        big = source.scaled(source.width() * factor, source.height() * factor,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.FastTransformation)
        path = "/tmp/mpf/raster-probe/%s-%d-%dx.png" % (tag, os.getpid(), factor)
        big.save(path, "PNG")
        return QUrl.fromLocalFile(path).toString()

    def _mount_empty(self):
        """Mount with EMPTY geometry: the mount's own vector paths
        can draw nothing, and the returned BASELINE grab holds the
        bed's own picture — the face's grid/background reads as ink
        to _ink_rows (its reference pixel sits outside the bed), so
        the raster proofs compare against this grab instead."""
        previous = PlateFaceRenderTests.PAYLOAD
        PlateFaceRenderTests.PAYLOAD = {
            "available": True, "reason": "",
            "layers": {
                "prev": None,
                "current": {"classes": {}, "travels": [], "travelStarts": [],
                            "travelEnds": [], "motions": 21},
                "next": None,
            },
            "split": 12, "method": "motion index", "anchor": 0,
        }
        self.addCleanup(setattr, PlateFaceRenderTests, "PAYLOAD", previous)
        monitor, window, face = self._follower_popover()
        face.setProperty("dot", None)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        baseline = window.grabWindow()
        # The threaded bed canvas can still be painting: settle the
        # picture until two grabs agree (the baseline must be the
        # bed's FINAL frame, not a mid-paint one).
        for _ in range(20):
            self.pump(10)
            check = window.grabWindow()
            if self._pixel_diff(check, baseline, face, window) == 0:
                baseline = check
                break
            baseline = check
        return monitor, window, face, baseline


    @staticmethod
    def _is_travel(pixel):
        """The travels' purple — the face's own established test, not a
        loose tolerance: the bed's grey fill sits inside a wide colour
        window and reads as ink otherwise."""
        red, green, blue = (pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF
        return blue - red > 40 and blue > 120 and green < red + 60

    def _colour_runs(self, image, face, window, predicate):
        """The CONTIGUOUS runs of face columns carrying the predicate's
        colour.

        A per-column colour match on antialiased ink catches isolated
        specks at a run's ends, so the raw column bounds wobble by a
        pixel or two between two pictures of the same geometry. A drawn
        stroke is a long run, so the run's own start and end are the
        placements worth comparing — and its LENGTH is the control: a
        translation preserves it, a threshold artifact does not."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        runs, start = [], None
        for col in range(0, int(face.width())):
            hit = False
            for row in range(0, int(face.height())):
                if predicate(image.pixel(int(origin.x()) + col,
                                         int(origin.y()) + row)):
                    hit = True
                    break
            if hit and start is None:
                start = col
            elif not hit and start is not None:
                runs.append((start, col - 1))
                start = None
        if start is not None:
            runs.append((start, int(face.width()) - 1))
        return runs

    @staticmethod
    def _longest_run(runs):
        return max(runs, key=lambda run: run[1] - run[0]) if runs else None

    def _feature_run(self, image, face, window, plot, bed_y, predicate,
                     band=8, inset=20):
        """The columns one bed row's feature occupies, within a BAND
        around that row — the census a handover can be sampled with.

        `_colour_runs` walks every face row for every column, and one
        such pass outlasts the decode window a handover has to be
        sampled inside: the sampling would then read the settled picture
        and never see the state it is asking about. The feature's own
        bed row is known (the fixtures paint one stroke per row), so the
        band around it is the whole search. A run is the same
        contiguous-column answer `_longest_run` reads, with None for a
        feature that is not on the face at all."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        row = int(origin.y() + plot["offsetY"]
                  + (plot["bedYMax"] - bed_y) * plot["sy"])
        first = last = None
        for col in range(inset, int(face.width()) - inset):
            for scan in range(max(0, row - band),
                              min(image.height(), row + band + 1)):
                if predicate(image.pixel(int(origin.x()) + col, scan)):
                    if first is None:
                        first = col
                    last = col
                    break
        return (first, last)

    def _pixel_diff(self, image, baseline, face, window, sample_step=4):
        """The sampled pixels that differ from the baseline grab (a
        threaded canvas's late frame reads as a diff; a settled
        identical picture reads zero)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        diffs = 0
        for row in range(0, int(face.height()), sample_step):
            for col in range(0, int(face.width()), sample_step):
                if image.pixel(int(origin.x()) + col, int(origin.y()) + row) \
                        != baseline.pixel(int(origin.x()) + col, int(origin.y()) + row):
                    diffs += 1
        return diffs

    def _stroke_ink(self, image, face, window, plot, bed_x, bed_y, tolerance=20):
        """The CORE stroke-ink rows at a bed point: strict matches
        only — antialiased fringes and a grey wash over native
        geometry must not read as the feature colour."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
        row = int(origin.y() + plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
        return sum(
            1 for py in range(max(0, row - 12), min(image.height(), row + 13))
            if self._matches(image.pixel(col, py), (0xD3, 0x2F, 0x2F),
                             tolerance=tolerance)
        )

    def _band_changed(self, image, baseline, face, window, plot, bed_x, bed_y, radius=8):
        """Any pixel changed inside a bed-space point's screen band."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
        row = int(origin.y() + plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
        for dy in range(-radius, radius + 1, 2):
            for dx in range(-radius, radius + 1, 2):
                if image.pixel(col + dx, row + dy) != baseline.pixel(col + dx, row + dy):
                    return True
        return False

    def _wait_diff(self, window, face, baseline, want, timeout=2.5):
        """Wait until the grabbed picture differs from (want=True) or
        equals (want=False) the baseline."""
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            diff = self._pixel_diff(image, baseline, face, window)
            if (want and diff > 0) or (not want and diff == 0):
                return image, diff
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image, self._pixel_diff(image, baseline, face, window)

    def _bed_point(self, face, bed_x, bed_y):
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        return {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}

    @staticmethod
    def _matches(pixel, colour, tolerance=60):
        return abs(((pixel >> 16) & 0xFF) - colour[0]) < tolerance \
            and abs(((pixel >> 8) & 0xFF) - colour[1]) < tolerance \
            and abs((pixel & 0xFF) - colour[2]) < tolerance

    def _red_pixels(self, image, face, window):
        """The wall-outer ink's red — nothing else on the face wears
        it, so the raster's presence is a colour census (robust to
        the bed's own jitter, which the pixel-diff baseline was
        not)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        count = 0
        for row in range(0, int(face.height())):
            for col in range(0, int(face.width())):
                if self._matches(image.pixel(int(origin.x()) + col, int(origin.y()) + row),
                                 (0xD3, 0x2F, 0x2F), tolerance=20):
                    count += 1
        return count

    def _red_in_band(self, image, face, window, plot, bed_x, bed_y, radius=10):
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
        row = int(origin.y() + plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
        for dy in range(-radius, radius + 1, 2):
            for dx in range(-radius, radius + 1, 2):
                if self._matches(image.pixel(col + dx, row + dy),
                                 (0xD3, 0x2F, 0x2F), tolerance=20):
                    return True
        return False

    def _purple_pixels(self, image, face, window):
        """The travel ink's purple — nothing else on the face wears
        it (the bed is grey, the toolpath red)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        count = 0
        for row in range(0, int(face.height())):
            for col in range(0, int(face.width())):
                pixel = image.pixel(int(origin.x()) + col, int(origin.y()) + row)
                r, g, b = (pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF
                if b - r > 40 and b > 120 and g < r + 60:
                    count += 1
        return count

    def _wait_purple(self, window, face, timeout=5.0):
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            count = self._purple_pixels(image, face, window)
            if count > 0:
                return image, count
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image, self._purple_pixels(image, face, window)

    def _handover_frame(self, window):
        """The frame a handover step actually paints.

        A grab returns the picture the last completed pass left, so on a
        host whose pass trails the evaluation (the macOS CI read a blank
        frame mid-handover) the first grab is the frame BEFORE the swap:
        the first drives the pass, the second reads it. Nothing is
        tolerated away — a composition with no ink at all is blank in
        both grabs, and the census still examines every beat."""
        window.grabWindow()
        return window.grabWindow()

    def _settled_frame(self, window, face, differs_from=None, painted=None,
                       timeout=10.0):
        """The frame the face has SETTLED at, judged by its own picture.

        A fixed beat after an install is not a settled read. The grab
        returns the picture the last completed pass painted, so on a
        host whose frames trail the evaluation it is the HELD
        pre-install picture; and mid-handover it can be the
        composition's own blank beat, which is a stable frame of its
        own that two equal grabs agree on happily. Settling therefore
        needs all three: the install has landed (the picture differs
        from `differs_from`), the caller's landmark stands (`painted`),
        and two consecutive grabs agree. `painted` is what keeps a
        blank beat from reading as a settle, and `differs_from` is what
        keeps the held frame from reading as one."""
        deadline = time.monotonic() + timeout
        previous = window.grabWindow()
        image = previous
        while time.monotonic() < deadline:
            self._pump_ms(10)
            window.grabWindow()
            image = window.grabWindow()
            if differs_from is not None and \
                    self._pixel_diff(image, differs_from, face, window) == 0:
                previous = image
                continue
            if painted is not None and not painted(image):
                previous = image
                continue
            if self._pixel_diff(image, previous, face, window) == 0:
                return image
            previous = image
        return image

    def _image_with_source(self, face, marker):
        """The Image ITEM whose own source carries `marker` — how the
        plate rasters are found from the test side, since the face names
        only the prefix pair."""
        for item in face.findChildren(QQuickItem):
            if item.metaObject().className() != "QQuickImage":
                continue
            if marker in str(item.property("source") or ""):
                return item
        return None

    def _prefix_stack_owners(self, face):
        """The prefix stack's standing owners, by ITEM.

        The census reads pixels; this reads ownership. The two prefix
        Images ARE the stack (the face names them), and the live
        replacement counts as an owner only once its pixels are there —
        a visible Image still loading draws nothing. A beat with neither
        standing leaves the interior to the canvas's just-delivered
        bitmap alone, whose scene texture commits one sync after its
        painted signal: that is the frame with no ink at all (the macOS
        blank mid-advance)."""
        record = face.findChild(QQuickItem,
                                "moonrakerPlateRetainedPrefixImage")
        live = face.findChild(QQuickItem, "moonrakerPlatePrefixImage")
        record_owner = record is not None and record.isVisible()
        source = live.property("source") if live is not None else None
        source = str(source.toString()) if source is not None else ""
        live_owner = bool(live is not None and live.isVisible() and source
                          and face.property("_prefixStatusReady"))
        return record_owner, live_owner

    def _wait_display_scale(self, face, above, timeout=1.5):
        """The eased display's first read past `above`.

        The animator ticks on the host's frame clock, so the fixed beat
        that sufficed in this container (30 ms) was short on the macOS
        CI's two cores and read the previous target exactly. Waiting for
        the tick is the host-independent form of the same read; a
        display that never moves still fails, which is the guard."""
        deadline = time.monotonic() + timeout
        value = face.property("displayScale")
        while value <= above and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
            value = face.property("displayScale")
        return value

    def _wait_red(self, window, face, want, timeout=5.0):
        """Wait until the raster's red ink appears (want=True) or
        leaves the picture entirely (want=False)."""
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            count = self._red_pixels(image, face, window)
            if (want and count > 0) or (not want and count == 0):
                return image, count
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image, self._red_pixels(image, face, window)


    _PARITY_ORIENTATIONS = {
        # The prefix boundary sits at motion 9 (bed position 110 or
        # its orientation's equivalent): the seam probes bracket it.
        "horizontal": {
            "points": [[20.0 + m * 10.0, 125.0, float(m)] for m in range(21)],
            "prefix": (60.0, 125.0), "tail": (160.0, 125.0),
            "seam_before": (105.0, 125.0), "seam_after": (115.0, 125.0),
            "across_columns": False,
        },
        "vertical": {
            "points": [[125.0, 20.0 + m * 10.0, float(m)] for m in range(21)],
            "prefix": (125.0, 60.0), "tail": (125.0, 160.0),
            "seam_before": (125.0, 105.0), "seam_after": (125.0, 115.0),
            "across_columns": True,
        },
        "diagonal": {
            "points": [[20.0 + m * 7.0, 20.0 + m * 7.0, float(m)]
                       for m in range(21)],
            "prefix": (55.0, 55.0), "tail": (118.0, 118.0),
            "seam_before": (78.0, 78.0), "seam_after": (90.0, 90.0),
            "across_columns": False,
        },
    }

    def _parity_leg(self, dpr, orientation):
        spec = self._PARITY_ORIENTATIONS[orientation]
        # Each leg mounts fresh, and _open replaces the _printer
        # factory with the instance — restore it so the next mount
        # can build one (the old expectedFailure swallowed exactly
        # this TypeError, so its second leg never ran).
        self._printer = PlateFaceRenderTests._printer
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 0.7)
        self.pump(10)
        payload = {
            "classes": {"WALL-OUTER": [spec["points"]]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10,
                                   dpr=dpr, line_scale=0.7)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image = window.grabWindow()
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def column_for(bed_x):
            return int(round(float(bed["offsetX"])
                             + (bed_x - float(bed["bedXMin"]))
                             * float(plot_value["sx"])))

        def row_for(bed_y):
            return int(round(float(bed["offsetY"])
                             + (float(bed["bedYMax"]) - bed_y)
                             * float(plot_value["sy"])))

        def band_at(bed_x, bed_y, picture=None):
            # The red excess over the underlying background across
            # the stroke's PERPENDICULAR (rows for a horizontal run,
            # columns for a vertical one; a diagonal crosses the
            # sampled band either way).
            col = column_for(bed_x)
            row = row_for(bed_y)
            picture = image if picture is None else picture
            values = []
            if spec["across_columns"]:
                for c in range(col - 8, col + 9):
                    pixel = picture.pixel(int(origin.x()) + c,
                                        int(origin.y()) + row)
                    values.append(max(0, ((pixel >> 16) & 0xFF)
                                     - ((pixel >> 8) & 0xFF)))
            else:
                for r in range(row - 8, row + 9):
                    pixel = picture.pixel(int(origin.x()) + col,
                                        int(origin.y()) + r)
                    values.append(max(0, ((pixel >> 16) & 0xFF)
                                     - ((pixel >> 8) & 0xFF)))
            return values

        def metrics(band):
            return max(band), sum(band), sum(1 for v in band if v > 4)

        def centre(band):
            # The stroke's sub-pixel centre along the perpendicular
            # band, in pixels from the band's middle sample.
            total = sum(band)
            if total <= 0:
                return None
            return sum(i * v for i, v in enumerate(band)) / float(total) - 8.0

        label = "dpr %s %s" % (dpr, orientation)
        # Wait for this composition, not an arbitrary 400 ms after the
        # install. Under parallel suite load that sampled the held empty
        # frame while the native prefix was still loading. Pixel presence
        # and the tail's ownership must BOTH land before measuring them.
        image = self._settled_frame(
            window, face,
            painted=lambda candidate: bool(face.property("_prefixStatusReady"))
            and face.property("_vectorCoversFrom") == 10
            and max(band_at(*spec["prefix"], picture=candidate)) > 0
            and max(band_at(*spec["tail"], picture=candidate)) > 0)
        self.assertEqual(face.property("_vectorCoversFrom"), 10,
                         "%s: the tail never ceded the prefix's ownership" % label)
        prefix_band = band_at(*spec["prefix"])
        tail_band = band_at(*spec["tail"])
        prefix_peak, prefix_energy, prefix_extent = metrics(prefix_band)
        tail_peak, tail_energy, tail_extent = metrics(tail_band)
        # WHERE the stroke sits, not merely how much of it there is:
        # the prefix raster and the canvas tail draw the same path
        # through the same plot, so their perpendicular centres of mass
        # must land on the same device pixel: a sub-pixel disagreement
        # between the two producers is what the eye reads as the layer
        # bouncing a pixel up and down while the composition hands over
        # between them.
        #
        # The measurement's own floor, stated: at the production width
        # the stroke is one or two device rows with a flat profile, so
        # the centre quantises to about half a pixel (a one-row profile
        # and a symmetric two-row one are the same physical position).
        # The slack below is that floor and nothing more -- a whole
        # pixel of seam, which is the reported magnitude, still fails
        # here; a sub-half-pixel seam is not something this measurement
        # can see, and is not claimed to be.
        def probe_phase(bed_x, bed_y):
            # Rounding X and Y independently changes the phase of a diagonal
            # at the sampled column. Compare each centroid with the path's
            # actual intersection there, not with the rounded bed point.
            x = float(bed["offsetX"]) + (bed_x - float(bed["bedXMin"])) * float(plot_value["sx"])
            y = float(bed["offsetY"]) + (float(bed["bedYMax"]) - bed_y) * float(plot_value["sy"])
            if spec["across_columns"]:
                return x - round(x)
            a, b = spec["points"][0], spec["points"][-1]
            slope = -(b[1] - a[1]) * float(plot_value["sy"]) / ((b[0] - a[0]) * float(plot_value["sx"]))
            return y + (round(x) - x) * slope - round(y)

        prefix_centre = centre(prefix_band)
        tail_centre = centre(tail_band)
        if prefix_centre is not None:
            prefix_centre -= probe_phase(*spec["prefix"])
        if tail_centre is not None:
            tail_centre -= probe_phase(*spec["tail"])
        self.assertIsNotNone(prefix_centre,
                             "%s: no ink at the prefix probe" % label)
        self.assertLessEqual(abs(prefix_centre - tail_centre), 0.75,
                             "%s: the prefix raster and the canvas tail draw "
                             "the same path at different device positions "
                             "(centre %s vs %s)"
                             % (label, prefix_centre, tail_centre))
        self.assertGreater(prefix_peak, 10,
                           "%s: the native prefix drew nothing measurable" % label)
        self.assertGreater(tail_peak, 10,
                           "%s: the canvas tail drew nothing measurable" % label)
        # The parity contract's DIRECTION (the live report's own):
        # the raster must never be the FAINTER side — the device
        # floor's coverage presents min(2/dpr, 1) logical px of full
        # ink, so the native prefix carries the canvas's device-grid
        # footprint while the harness's canvas paints the faithful
        # subpixel AA (the offscreen rasteriser does not floor). The
        # 0.7 slack absorbs only the harness's AA jitter.
        self.assertGreater(prefix_peak, tail_peak * 0.7,
                           "%s: the native prefix is ghostly against the "
                           "canvas tail (peak %s vs %s)"
                           % (label, prefix_peak, tail_peak))
        self.assertGreater(prefix_energy, tail_energy * 0.7,
                           "%s: the native prefix's band energy washes "
                           "out (%s vs %s)" % (label, prefix_energy, tail_energy))
        # The effective stroke extent (the thickness in rows/columns
        # above the noise floor) must not step at the seam.
        self.assertLessEqual(abs(prefix_extent - tail_extent), 1,
                             "%s: the stroke's effective extent steps at "
                             "the seam (%s vs %s rows)"
                             % (label, prefix_extent, tail_extent))
        # The seam itself: both sides of the ownership handoff stay
        # inked and comparable — a hole or a doubled seam reads here.
        seam_before = band_at(*spec["seam_before"])
        seam_after = band_at(*spec["seam_after"])
        self.assertGreater(max(seam_before), 8,
                           "%s: the seam's prefix side lost its ink" % label)
        self.assertGreater(max(seam_after), 8,
                           "%s: the seam's tail side lost its ink" % label)
        self.assertGreater(max(seam_before), max(seam_after) * 0.6,
                           "%s: the seam's prefix side washes out at the "
                           "handoff (%s vs %s)"
                           % (label, max(seam_before), max(seam_after)))
        self.pump(20)


    def _mount_interaction_fixture(self):
        """The interaction's own fixture: a mounted face carrying a REAL
        4x warm navigation raster (the model's role) and one native layer
        with a painted prefix — enough for the gesture to enter the
        interaction and for the exit barrier to pass."""
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        from mpf.plate.PlateQt import render_navigation_layer, png_file
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot,
            {"width": int(face.width()), "height": int(face.height()),
             "scale": 1.0, "lineScale": 8.0, "compact": False,
             "panX": 0.0, "panY": 0.0, "backing": 4.0,
             "bedWidth": 250.0, "bedDepth": 250.0}, split=18)
        self._printer.setNavigation(png_file(
            nav, "/tmp/mpf/raster-probe", "nav-fixture-%d" % time.monotonic_ns()))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        self._wait_red(window, face, want=True)
        return monitor, window, face, payload


    def _stroke_payload(self, motions=320, dx=0.6):
        # A dense horizontal stroke: motion m's edge ends at
        # 20 + m*dx, so the printed boundary at split S sits at
        # 20 + S*dx on the bed's own coordinates.
        return {"classes": {"WALL-OUTER": [
                    [[20.0 + m * dx, 125.0, float(m)] for m in range(motions + 1)]]},
                "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": motions}

    def _bed_plot(self, face):
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        return {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}

    def _prefix_for(self, payload, face, split, stem):
        from mpf.plate.PlateQt import render_layer_prefix, png_file
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0, "dpr": 1.0}
        prefix = render_layer_prefix(payload, self._bed_plot(face), view, split)
        return prefix, png_file(prefix, "/tmp/mpf/raster-probe",
                                "%s-%d" % (stem, time.monotonic_ns()))

    def _nav_raster_for(self, payload, face, split, stem):
        """The 4x interaction raster at the size the model publishes
        it — the transport whose decode is 66 ms at the face's own
        geometry."""
        from mpf.plate.PlateQt import render_navigation_layer, png_file
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0, "backing": 4.0,
                "bedWidth": 250.0, "bedDepth": 250.0}
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload},
            self._bed_plot(face), view, split=split)
        return nav, png_file(nav, "/tmp/mpf/raster-probe",
                             "%s-%d" % (stem, time.monotonic_ns()))

    def _status_probe(self):
        """A QML-side reader for an Image's status.

        The engine hands a C++ enum back to Python as an opaque
        handle (reading the property throws), but QML itself reads it
        as the number the face's own gates compare against — so the
        probe asks the engine, exactly as the face does.
        """
        from PyQt6.QtCore import QUrl as _QUrl
        from PyQt6.QtQml import QQmlComponent
        component = QQmlComponent(self.engine)
        component.setData(
            b"import QtQuick\nItem { function statusOf(item) "
            b"{ return item ? item.status : -1 } }", _QUrl())
        self.assertFalse(component.isError(),
                         [str(error) for error in component.errors()])
        probe = component.create()
        self.assertIsNotNone(probe, "the status probe never built")
        return probe


    # The painter-fidelity fixtures: the bed-space runs a payload
    # carries as separate prepared segments. Every vertex names the
    # motion that owns the edge ENDING there (a run's first vertex
    # carries its first edge's motion too), so each single-motion run
    # here is owned by one motion index. Two straight runs with a gap
    # between them, and two parallel diagonals 50 mm apart.
    HORIZONTAL_RUNS = (
        [[30.0, 203.0, 1.0], [90.0, 203.0, 1.0]],
        [[130.0, 203.0, 2.0], [190.0, 203.0, 2.0]],
    )
    PARALLEL_DIAGONALS = (
        [[30.0, 30.0, 1.0], [90.0, 90.0, 1.0]],
        [[30.0, 80.0, 2.0], [90.0, 140.0, 2.0]],
    )

    # Arcs straight from literal G-code, and their payloads from the
    # real index: a 120 mm semicircle whose apex stands 60 mm off its own
    # chord (a painter that connects source endpoints draws the chord,
    # and the apex band is where that shows), the clockwise mirror of the
    # same endpoints, the same arc followed by a straight move, and two
    # arcs a travel apart.
    ARC_CCW = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
               "G0 X185 Y125\n"
               "G3 X65 Y125 I-60 J0 E1\n")
    ARC_CW = ARC_CCW.replace("G3", "G2")
    ARC_THEN_LINE = ARC_CCW + "G1 X185 Y60 E2\n"
    # The second arc's E rises too: under M82 a repeated E deposits
    # nothing, so an E1-then-E1 pair would reach the face as a travel.
    ARC_RUNS = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
                "G0 X105 Y125\nG3 X15 Y125 I-45 J0 E1\n"
                "G0 X235 Y125\nG3 X145 Y125 I-45 J0 E2\n")

    # The pan fixture: a wall and a travel on DIFFERENT rows but the
    # same bed columns, so a displacement between the two producers
    # shows as a change in their horizontal separation, whatever the
    # rest of the face does.
    PAN_WALL = [[30.0 + i * 4.0, 200.0, float(i)] for i in range(26)]
    PAN_TRAVEL = [[30.0 + i * 4.0, 120.0, float(i)] for i in range(26)]


        # The teeth: a one-device-row displacement of the full raster
        # (a bare `y: 1` against `anchors.fill` is inert, and
        # `anchors.topMargin` under it STRETCHES rather than
        # translates — both read as "the pin has no teeth" if you stop
        # there and neither moves the row set; explicit geometry is the
        # form that actually displaces it)
        # (progressRasterImage, explicit geometry — a bare `y: 1`
        # against `anchors.fill` is inert, and `anchors.topMargin`
        # stretches rather than translates) splits the sweep to
        # s2..s20 = 263.50, s21 = 264.50 and fails it, so the pin sees
        # the raster producer move. A one-row displacement of
        # progressCanvas fails it too, by a full pixel on the
        # canvas-only splits, which is where that canvas is the
        # producer. The two mutations together are what says the sweep
        # covers both producers rather than one that happens to be on
        # screen throughout.


    @staticmethod
    def _arc_payload(gcode, split=None):
        """The follower payload a literal file produces: the painted
        geometry is the index's own output, so an arc's curve here is the
        one the follower would draw. *split* defaults to every motion."""
        index = build_index_from_bytes(gcode.encode("ascii"))
        return {
            "available": True, "reason": "",
            "layers": {"prev": None, "current": qml_geometry(layer_polylines(index, 0)), "next": None},
            "split": index.motion_count(0) if split is None else int(split),
            "method": "motion index", "anchor": 0,
        }

    @staticmethod
    def _centres(ink):
        """Each painted column's ink band centres, top to bottom."""
        columns = {}
        for col, row in ink:
            columns.setdefault(col, []).append(row)
        centres = {}
        for col, rows in columns.items():
            rows.sort()
            bands, run = [], []
            for row in rows:
                if run and row > run[-1] + 2:
                    bands.append(sum(run) / len(run))
                    run = []
                run.append(row)
            if run:
                bands.append(sum(run) / len(run))
            centres[col] = bands
        return centres

    @staticmethod
    def _near(ink, point, reach):
        return any(abs(col - round(point[0])) <= reach and abs(row - round(point[1])) <= reach
                   for col, row in ink)

    @staticmethod
    def _components(ink, reach=2):
        """The count of connected ink blobs. The tolerance is raster, not
        geometry: the strokes are 0.7 px wide on a 1 px grid, so a single
        antialiased line can drop a pixel, while a real gap at a join is a
        whole tessellation vertex away."""
        neighbours = [(dc, dr) for dc in range(-reach, reach + 1)
                      for dr in range(-reach, reach + 1)]
        remaining = set(ink)
        counted = 0
        while remaining:
            counted += 1
            stack = [remaining.pop()]
            while stack:
                col, row = stack.pop()
                for dc, dr in neighbours:
                    step = (col + dc, row + dr)
                    if step in remaining:
                        remaining.discard(step)
                        stack.append(step)
        return counted

    @staticmethod
    def _layer_payload(segments):
        """The follower payload the model hands the face: one class of
        prepared runs and the printed count, in PlateProgress's own
        shape (the count moves afterwards, as a poll moves it)."""
        return {
            "available": True, "reason": "",
            "layers": {
                "prev": None,
                "current": {
                    "classes": {"SKIN": [list(segment) for segment in segments]},
                    "travels": [], "travelStarts": [], "travelEnds": [],
                    "motions": 64,
                },
                "next": None,
            },
            "split": 0, "method": "motion index", "anchor": 0,
        }

    def _painted(self, payload):
        """Mount the follower on *payload* and hand back the face, the
        window, the live mapping and the baseline grab. The boundary
        opens at zero and the grey base is off, so what the printed
        strokes ADD over the baseline is exactly the coloured ink."""
        previous = PlateFaceRenderTests.PAYLOAD
        PlateFaceRenderTests.PAYLOAD = dict(payload, split=0)
        self.addCleanup(setattr, PlateFaceRenderTests, "PAYLOAD", previous)
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        face = faces[0]
        face.setProperty("dot", None)
        face.setProperty("showBase", False)
        self.pump(40)
        grid = face.findChild(QQuickItem, "moonrakerPlateCanvas")
        plot = grid.property("_plot")
        self.assertIsNotNone(plot, "the bed mapping never built")
        # The physical-width model renders the default 0.7 lineScale
        # as a subpixel stroke at this face size; the painter tests
        # pin the same 0.7 px weight the old fixed-width painter used,
        # so the geometry assertions measure a solid line.
        painted = grid.property("_paints")
        face.setProperty("lineScale", 0.7 / (0.2 * plot.property("sx").toNumber()))
        self.pump(20)
        # The grown popover lays the face out AFTER the first paint;
        # the threaded rasters only re-upload their textures on a
        # geometry sync, so a 1 px nudge replays the live resize path
        # and lands the repaint at the settled size.
        face.setWidth(face.width() + 1)
        self.pump(20)
        face.setWidth(face.width() - 1)
        self.pump(20)
        # At this face size the canvas can still be blank when the
        # baseline is grabbed, and two blank grabs agree — so a settle
        # check alone accepts the blank frame, and every painter test
        # below then diffs against an empty baseline and passes while
        # proving nothing. The arrival has to be PROVED, on the frame
        # that was returned, before the settle is trusted; a canvas
        # that never paints now fails here and says so. The arrival is
        # the PAINT, never the agreement: the view just set is serviced
        # a frame late, and a baseline from the previous one is a
        # census of two different pictures (the 2-CPU rig's "painted as
        # separate runs", eight blobs where the stroke is one).
        self._await_painted(face, grid, painted)
        inked = self._grab_when_inked(window, face)
        self.assertTrue(self._ink_rows(inked, face, window),
                        "the baseline was grabbed before the grid painted")
        return face, window, self._mapping(plot), self._settled(window)

    def _printed(self, split, window, face, baseline, box, span):
        """Move the boundary to *split* and return the pixels the
        printed strokes added over the baseline.

        The face must have PAINTED the split before any pixel is read.
        `_await_ink` returns the moment ink reaches the span's ends, and
        a frame grabbed before that repaint still holds the PREVIOUS
        split's ink — which is what the callers' "nothing was painted
        ahead of the split" assertions then read. Two tests failed that
        way on the 2-CPU rig, the second only after the first was fixed
        by hand, which is why the wait lives here rather than at either
        call site.
        """
        self._printer.setSplit(split)
        self._await_split(face, split)
        _image, added = self._await_ink(window, face, baseline, box, span)
        return added

    @staticmethod
    def _mapping(plot):
        """The face's own transform, read from the live plot: the test
        asserts screen geometry against the mapping the painter uses,
        never against a second opinion about it."""
        bed = plot.property("bed")
        return {
            "sx": plot.property("sx").toNumber(),
            "sy": plot.property("sy").toNumber(),
            "offsetX": bed.property("offsetX").toNumber(),
            "offsetY": bed.property("offsetY").toNumber(),
            "bedXMin": bed.property("bedXMin").toNumber(),
            "bedYMax": bed.property("bedYMax").toNumber(),
        }

    @staticmethod
    def _scene(mapping, x, y):
        return (mapping["offsetX"] + (x - mapping["bedXMin"]) * mapping["sx"],
                mapping["offsetY"] + (mapping["bedYMax"] - y) * mapping["sy"])

    @staticmethod
    def _sample(image):
        return [image.pixel(col, row)
                for row in range(0, image.height(), 7)
                for col in range(0, image.width(), 7)]

    def _await_painted(self, face, grid, painted, timeout=15.0):
        """The face once its canvases have painted the state the test
        just set.

        Both the grid and the base raster on the threaded strategy: the
        repaint is serviced a frame after it is requested, so a grab
        taken in between reads the PREVIOUS picture — and a census that
        diffs one picture against another counts whatever moved with
        it, which is how one stroke reads as eight runs under load.
        *painted* is the grid's own count from before the view was set;
        `_lastPendingKey` is the base canvas's word. Both are arrivals:
        the key is written by the paint routine, never by the request.
        """
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        deadline = time.monotonic() + timeout
        asked = None
        while time.monotonic() < deadline:
            asked = QMetaObject.invokeMethod(face, "_pendingKeyOf", Q_RETURN_ARG(QVariant))
            if grid.property("_paints") != painted \
                    and face.property("_lastPendingKey") == asked:
                return
            self.app.processEvents()
            time.sleep(0.05)
        self.fail("the face never painted the view under test: the grid is at "
                  "%r paints (was %r), the base at %r against %r asked"
                  % (grid.property("_paints"), painted,
                     face.property("_lastPendingKey"), asked))

    def _settled(self, window, rounds=10):
        """The window's image once two grabs agree: the canvases raster
        on the threaded render strategy, so one grab can catch the
        strokes mid-flight."""
        image = window.grabWindow()
        for _ in range(rounds):
            self.pump(10)
            nxt = window.grabWindow()
            if self._sample(image) == self._sample(nxt):
                return nxt
            image = nxt
        return image

    def _added(self, image, baseline, face, window, box):
        """The face's pixels the printed strokes added over the empty
        baseline, as {(col, row)} inside *box* (face coordinates)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        left, top, right, bottom = box
        added = set()
        for row in range(max(0, top), min(int(face.height()), bottom + 1)):
            for col in range(max(0, left), min(int(face.width()), right + 1)):
                now = image.pixel(ox + col, oy + row)
                was = baseline.pixel(ox + col, oy + row)
                if any(abs(((now >> shift) & 0xFF) - ((was >> shift) & 0xFF)) > 24
                       for shift in (0, 8, 16)):
                    added.add((col, row))
        return added

    def _await_split(self, face, split, timeout=3.0):
        """The face once it has PAINTED *split*.

        A grab taken before the repaint lands reads the PREVIOUS frame, and
        at a reduced split that frame still holds the very ink the caller is
        about to assert is absent — which is how a "painted nothing" check
        becomes a one-in-eight failure under load (the 2-CPU rig caught this
        one). ``_lastSplit`` is written inside the paint routine, so it is
        the face's own word that the frame now belongs to the split it was
        asked for; waiting on it makes the absence assertion mean what it
        says, and a genuine product fault still fails it.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if face.property("_lastSplit") == split:
                return
            self.app.processEvents()
            time.sleep(0.05)
        self.fail("the face never painted split %r (its last was %r)"
                  % (split, face.property("_lastSplit")))

    def _await_ink(self, window, face, baseline, box, span, timeout=3.0):
        """The image once ink reaches both ends of *span*: a paint lands
        whole, so ink at the stroke's own ends means the run is drawn."""
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while True:
            added = self._added(image, baseline, face, window, box)
            columns = [col for col, _row in added]
            if columns and min(columns) <= span[0] and max(columns) >= span[1]:
                return image, added
            if time.monotonic() >= deadline:
                image.save("/tmp/mpf/follower_painter_fail.png")
                return image, added
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()


    def _settled_height(self, face, timeout=2.0):
        """The face's height once the popover's layout has stopped
        moving. The open lays the picker out over a few passes, so a
        baseline read one processEvents beat after it is a layout still
        in flight: the 3.10/3.11 legs recorded 401 there and 375 once
        settled, and the hover then read as the reflow. Three steady
        reads are the settlement; a hover that really reflows still
        moves the settled value."""
        deadline = time.monotonic() + timeout
        last = face.height()
        steady = 0
        while steady < 3 and time.monotonic() < deadline:
            self._pump_ms(20)
            now = face.height()
            steady = steady + 1 if now == last else 0
            last = now
        return last


    def _click(self, window, item, at=None):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        point = at if at is not None else QPointF(item.width() / 2, item.height() / 2)
        centre = item.mapToScene(point).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=centre)
        self.pump(30)

    def _follower_popover(self, width=900, height=760):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", width, height)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        # These existing composition fixtures deliberately exercise the
        # original physical-width software contract. Pixel-width policy
        # has its own native/Canvas parity test below.
        faces[0].setProperty("pixelLineWidth", False)
        return monitor, window, faces[0]


    def _fill_zoom(self, face, plot):
        """A zoom whose bed overfills the face on both axes: the pan
        clamp never bites, so "centred" is exactly the view's centre."""
        side = plot.property("bed").property("plotWidth").toNumber()
        face.setProperty("viewScale", max(face.width(), face.height()) / side + 0.5)
        self.pump(20)

    @staticmethod
    def _running_animations(face):
        """Every animation-family object under the face that is running.
        The face's animations are all gated on a load being busy; an
        idle face must own none."""
        return [str(child.metaObject().className())
                for child in face.findChildren(QObject)
                if "Animation" in str(child.metaObject().className())
                and child.property("running")]

    def _driver_ticks(self, window, milliseconds):
        """QML animation-driver Timer events delivered app-wide while
        the rig pumps `milliseconds`. The driver is what turns a running
        animation into a 60 Hz repaint loop: the events are the cost,
        independent of how much ink the frames carry."""
        from PyQt6.QtCore import QEvent

        class DriverCensus(QObject):
            def __init__(self):
                super().__init__()
                self.ticks = 0

            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Type.Timer
                        and "AnimationDriver" in str(obj.metaObject().className())):
                    self.ticks += 1
                return False

        census = DriverCensus()
        self.app.installEventFilter(census)
        try:
            self._pump_ms(milliseconds)
        finally:
            self.app.removeEventFilter(census)
        return census.ticks

    def _wait_for_a_quiet_driver(self, window, reads=2, milliseconds=300,
                                 timeout=15.0):
        """The idle half's precondition: a driver that has gone quiet.

        The mount lays the faces out over several passes, and the pass
        that resolves a face's width moves the zoom scope parked beside
        it: the scope's `Behavior on x` then spends 180 ms ticking the
        app-wide animation driver with no load anywhere. That is the
        MOUNT settling — its passes land whenever the host's font
        metrics do, so a census taken a fixed beat after the mount can
        read it as the idle face's own cost (the 3.10/3.11 legs read 7
        to 11 ticks that way). Consecutive clean windows are the
        settling run out; a face that animates while idle owns no clean
        window at all, and the timeout is a hang stop, not a budget."""
        clean = 0
        deadline = time.monotonic() + timeout
        while clean < reads and time.monotonic() < deadline:
            if self._driver_ticks(window, milliseconds) == 0:
                clean += 1
            else:
                clean = 0
        self.assertEqual(
            clean, reads,
            "the idle face never stopped ticking the animation driver")


    def _settle(self, face, text):
        """Evaluate *text* against the face's own settle Timer.

        The Timer is the face's own object, reached through its context
        because a property alias hands back a void pointer rather than
        the item.
        """
        from PyQt6.QtQml import QQmlEngine, QQmlExpression
        expression = QQmlExpression(QQmlEngine.contextForObject(face), face,
                                    "settleTimer." + text)
        expression.setNotifyOnValueChanged(False)
        value = expression.evaluate()
        self.assertFalse(expression.hasError(), expression.error().toString())
        # PyQt6 pairs the result with its undefined flag.
        return value[0] if isinstance(value, tuple) else value


if QT_AVAILABLE:
    from mpf.monitor.MonitorFormatting import PlateProjectionMemo

    class PlateJobBoundaryTests(RealEngineTestCase):
        """The picker's payload ties its GEOMETRY to the current job.
        The core lane owns exclude_object (CORE_OBJECTS) and the
        auxiliary copy is that lane's first landing alone: the finished
        print's polygons decorated with the new job's flags put an old
        object on the map, and a tap then acted on the same-named
        object at the position it held in the previous print.

        Projection-level — the method under test reads the snapshot's
        two lanes and its own memo, nothing else of the model (the
        monitor-level harness lives in the model's coverage file)."""

        # The finished print's plate, still in the auxiliary copy while
        # the new job's core report has already landed.
        OLD = {
            "objects": [{"name": "PART_A", "center": [10.0, 10.0],
                         "polygon": [[5.0, 5.0], [5.0, 15.0], [15.0, 15.0], [15.0, 5.0]]}],
            "excluded_objects": [], "current_object": None,
        }
        MOVED = {
            "objects": [{"name": "PART_A", "center": [100.0, 100.0],
                         "polygon": [[95.0, 95.0], [95.0, 105.0], [105.0, 105.0],
                                     [105.0, 95.0]]}],
            "excluded_objects": [], "current_object": "PART_A",
        }

        @classmethod
        def setUpClass(cls):
            super().setUpClass()
            from tests.qt_runtime_support import runtime

            # The model module reads Uranium's UM.* at import, which
            # only the harness supplies (this file's QGuiApplication is
            # the same instance the harness reuses).
            with runtime():
                from mpf.monitor.MoonrakerMonitorModel import MoonrakerMonitorModel
            # Bound as a plain function on the class: the unbound method
            # called through the instance would take the test case as
            # its `self`.
            cls.projection = staticmethod(MoonrakerMonitorModel._plate_objects_value)

        def _model(self, core, auxiliary):
            """The projection bound to a model-shaped carrier: the
            snapshot's lanes plus the projection's own state — the
            memo, the job it last read, the cached geometry."""
            from types import SimpleNamespace

            return SimpleNamespace(
                _data=SimpleNamespace(snapshot=SimpleNamespace(core=core or {},
                                                               auxiliary=auxiliary or {})),
                _plate_memo=PlateProjectionMemo(),
                _plate_job_seen=None,
                _plate_geometry=None,
                _plate_payload=None)

        def _rows(self, model, visited=frozenset()):
            plate = self.projection(model, visited)
            return {row["name"]: row for row in plate["objects"]}


if QT_AVAILABLE:

    class LateBedDouble(QObject):
        """A printer whose bed geometry arrives late and can switch:
        the attach the canvas must re-map without a resize. Module
        level inside the guard (the house pattern)."""

        bedChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self._bed = (0.0, 0.0)
            self._centre_is_zero = False

        def setBed(self, width, depth):
            self._bed = (float(width), float(depth))
            self.bedChanged.emit()

        def setCentreIsZero(self, value):
            self._centre_is_zero = bool(value)
            self.bedChanged.emit()

        @pyqtProperty(float, notify=bedChanged)
        def bedMeshMachineWidth(self):
            return self._bed[0]

        @pyqtProperty(float, notify=bedChanged)
        def bedMeshMachineDepth(self):
            return self._bed[1]

        @pyqtProperty(bool, notify=bedChanged)
        def bedMeshCenterIsZero(self):
            return self._centre_is_zero


class PlateCanvasHitTests(RealEngineTestCase):
    """The picker's hit test against real polygons: the click target is
    the object the user sees. Containment is authoritative (the Python
    probe's own ray cast, aimed through the canvas's own bed mapping),
    an overlap resolves to the first row in the payload's order, and the
    centre radius serves only the rows that carry no geometry at all —
    the gesture is destructive, so a centre the object does not own must
    never answer for it."""

    @staticmethod
    def _row(name, center=None, polygon=None, excluded=False):
        """A payload row in the model's plate_values shape."""
        return {"name": name, "center": center, "polygon": polygon,
                "order": 0, "excluded": bool(excluded), "current": False,
                "restoreAllowed": True}

    @classmethod
    def _payload(cls, rows):
        return {"objects": rows, "truncated": 0, "excludedCount": 0}

    @classmethod
    def _polygon_bed(cls):
        """Five objects on the double's 250 mm bed, every one of them
        with real geometry — the shapes a printer reports once the
        exclude_object payload is live."""
        return [
            # A bar 35 mm deep: a click near its right end stands ~107 mm
            # from its own centre, well past the 18 mm degraded radius.
            cls._row("Long_Bracket", [125.0, 37.5],
                     [[10.0, 20.0], [240.0, 20.0], [240.0, 55.0], [10.0, 55.0]]),
            # Two objects 2 mm apart, with the right-hand one's centre
            # inside the left-hand object's degraded reach.
            cls._row("Left_Block", [115.0, 215.0],
                     [[40.0, 180.0], [190.0, 180.0], [190.0, 250.0], [40.0, 250.0]]),
            cls._row("Right_Block", [202.0, 230.0],
                     [[192.0, 220.0], [212.0, 220.0], [212.0, 240.0], [192.0, 240.0]]),
            # An overlapping pair whose centres both stand inside the
            # degraded radius of a shared interior point.
            cls._row("Over_A", [90.0, 110.0],
                     [[60.0, 80.0], [120.0, 80.0], [120.0, 140.0], [60.0, 140.0]]),
            cls._row("Over_B", [110.0, 130.0],
                     [[80.0, 100.0], [140.0, 100.0], [140.0, 160.0], [80.0, 160.0]]),
        ]

    def _picker(self, rows):
        """Open the exclude popover on *rows* and hand back the window,
        the face and the shared canvas."""
        previous = PlatePrinterDouble.PLATE
        PlatePrinterDouble.PLATE = self._payload(rows)
        self.addCleanup(setattr, PlatePrinterDouble, "PLATE", previous)
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        # The reference is retained: a Python-created QObject dies with
        # its last Python ref (the QML var takes no ownership).
        self._printer = PlatePrinterDouble()
        monitor.setProperty("printer", self._printer)
        monitor.setProperty("openPopOver", "plate")
        self.pump(30)
        face = self.find(monitor, "moonrakerPlateExcludeFace")
        canvas = face.findChild(QQuickItem, "moonrakerPlateCanvas")
        self.assertIsNotNone(canvas, "the picker canvas never built")
        self.assertIsNotNone(canvas.property("_plot"), "the bed mapping never built")
        self._settle_picker_geometry(window, face, canvas)
        return window, face, canvas

    def _settle_picker_geometry(self, window, face, canvas):
        # processEvents alone does not guarantee a Qt Quick polish/render
        # pass. Linux CI once aimed using a 393 px face, then delivered the
        # event after the popover's wrapped footer shrank it to 337 px.
        # Establish the drawn mapping before deriving any pointer target.
        previous = None
        stable = 0
        def settled(image):
            nonlocal previous, stable
            origin = canvas.mapToScene(QPointF())
            geometry = (face.width(), face.height(), canvas.width(), canvas.height(),
                        origin.x(), origin.y(), *self._scene(canvas, 0, 0),
                        *self._scene(canvas, 250, 250))
            stable = stable + 1 if geometry == previous else 0
            previous = geometry
            return not image.isNull() and canvas.width() > 0 and canvas.height() > 0 and stable >= 2
        frame = self._wait_until(window, settled, timeout=3.0)
        self.assertFalse(frame.isNull(), "the picker never rendered a frame")
        self.assertGreaterEqual(stable, 2, "picker geometry never settled: %r" % (previous,))

    @staticmethod
    def _scene(canvas, bed_x, bed_y):
        """The canvas's own bed-to-item transform, read from the live
        plot: the test aims through the mapping the painter uses, never
        through a second opinion about it."""
        plot = canvas.property("_plot")
        bed = plot.property("bed")
        return (bed.property("offsetX").toNumber()
                + (bed_x - bed.property("bedXMin").toNumber()) * plot.property("sx").toNumber(),
                bed.property("offsetY").toNumber()
                + (bed.property("bedYMax").toNumber() - bed_y) * plot.property("sy").toNumber())

    def _hover(self, window, canvas, face, bed_x, bed_y):
        """Put the pointer on the bed point through the real mouse path
        and return the name the face published for the hover."""
        from PyQt6.QtTest import QTest
        self._settle_picker_geometry(window, face, canvas)
        scene_x, scene_y = self._scene(canvas, bed_x, bed_y)
        target_size = (canvas.width(), canvas.height())
        # The face publishes on a POSITION CHANGE: a move onto the
        # point the pointer already holds is no hover at all. The
        # pointer outlives the window (see _park_pointer), so a test
        # that ended on this very point starves the hover below.
        # Lead with a step off the point so every hover is a real
        # move.
        # Native pointer events are asynchronous, especially under coverage.
        # Wait for the MouseArea's coordinates, never for the expected object
        # name: a wrong hit-test must still fail the caller's assertion.
        area = next(child for child in reversed(canvas.childItems())
                    if child.inherits("QQuickMouseArea"))
        class Delivery(QObject):
            def __init__(self):
                super().__init__()
                self.moves = []

            @pyqtSlot(float, float)
            def received(self, x, y):
                # Coordinates can update on entry before positionChanged's
                # QML handler runs. Observe the signal, not just its inputs;
                # QML forwards plain coordinates so PyQt never converts the
                # private QQuickMouseEvent pointer.
                self.moves.append((x, y))

        delivery = Delivery()
        from PyQt6.QtQml import QQmlContext
        context = QQmlContext(self.engine.rootContext())
        context.setContextProperty("watchedArea", area)
        context.setContextProperty("delivery", delivery)
        component = QQmlComponent(self.engine)
        component.setData(b"import QtQuick 2.15; Connections { target: watchedArea; "
                          b"function onPositionChanged(mouse) { delivery.received(mouse.x, mouse.y) } }", QUrl())
        connection = component.create(context)
        self.assertIsNotNone(connection, qml_error_report(component))
        def delivered_move(point, entering=False):
            first = len(delivery.moves)
            QTest.mouseMove(window, point)
            local = area.mapFromScene(QPointF(point))
            deadline = time.monotonic() + 1.5
            def received():
                # An earlier matching event is not evidence that the pointer
                # still occupies it: native cursor warps can deliver another
                # move in the same event-loop turn.
                if entering and area.property("containsMouse"):
                    return (abs(area.property("mouseX") - local.x()) < 0.75
                            and abs(area.property("mouseY") - local.y()) < 0.75)
                if len(delivery.moves) <= first:
                    return False
                x, y = delivery.moves[-1]
                return abs(x - local.x()) < 0.75 and abs(y - local.y()) < 0.75
            while time.monotonic() < deadline:
                self.app.processEvents()
                if received():
                    break
                QTest.qWait(10)
            self.assertTrue(received(), "hover positionChanged was not delivered: %s -> %s" %
                            (delivery.moves[first:], (local.x(), local.y())))

        # Establish the off-point before submitting the target. Otherwise
        # an old target coordinate can satisfy the waiter while the off-point
        # signal still owns hoveredName; queued native moves then race it.
        off_x = scene_x + (8.0 if scene_x + 8.0 < canvas.width() - 1 else -8.0)
        off_y = scene_y + (8.0 if scene_y + 8.0 < canvas.height() - 1 else -8.0)
        try:
            # Qt 6.6 updates coordinates on entry without emitting
            # positionChanged. The off-point only establishes the pointer;
            # the target must still deliver the actual hover handler.
            delivered_move(canvas.mapToScene(QPointF(off_x, off_y)).toPoint(), entering=True)
            delivered_move(canvas.mapToScene(QPointF(scene_x, scene_y)).toPoint())
        finally:
            from PyQt6 import sip
            sip.delete(connection)
            sip.delete(context)
        result = face.property("hoveredName")
        # Keep coordinates and geometry in failures, including Linux CI where
        # the last off-point move once appeared as the selected neighbour.
        self._last_hover_evidence = {
            "bed": (bed_x, bed_y), "target": (scene_x, scene_y),
            "target_canvas": target_size,
            "moves": delivery.moves, "canvas": (canvas.width(), canvas.height()),
            "origin": (canvas.mapToScene(QPointF()).x(), canvas.mapToScene(QPointF()).y()),
            "hovered": result,
        }
        self.assertEqual(target_size, (canvas.width(), canvas.height()), self._last_hover_evidence)
        self.assertEqual((scene_x, scene_y), self._scene(canvas, bed_x, bed_y), self._last_hover_evidence)
        return result

    def _click_bed(self, window, canvas, face, bed_x, bed_y):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        scene_x, scene_y = self._scene(canvas, bed_x, bed_y)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=canvas.mapToScene(QPointF(scene_x, scene_y)).toPoint())
        self.pump(10)

    @staticmethod
    def _containing(rows, x, y):
        """The payload's own answer at a bed point, in payload order:
        the Python ray cast the QML test mirrors, so a test can state
        what the geometry says before it asserts what the canvas did."""
        return [row["name"] for row in rows
                if row.get("polygon") and point_in_polygon(x, y, row["polygon"])]

    def _nearest_centre(self, canvas, rows, bed_x, bed_y):
        """The nearest centre the payload carries, with its pixel
        distance — the answer the replaced centre rule gave."""
        target = self._scene(canvas, bed_x, bed_y)
        best = None
        for row in rows:
            if not row.get("center"):
                continue
            point = self._scene(canvas, row["center"][0], row["center"][1])
            distance = (((point[0] - target[0]) ** 2 + (point[1] - target[1]) ** 2) ** 0.5)
            if best is None or distance < best[1]:
                best = (row["name"], distance)
        return best

    @staticmethod
    def _radius_px(canvas):
        """The degraded radius as the canvas measures it: bed
        millimetres through the plot's own scale."""
        plot = canvas.property("_plot")
        return canvas.property("hitRadiusMm") * max(abs(plot.property("sx").toNumber()),
                                                    abs(plot.property("sy").toNumber()))


    def _acts(self):
        """Every object command the gesture dispatched, in order."""
        return [call for call in self._printer.calls if call[0] in ("exclude", "restore")]

    def _status_moves(self, name, excluded):
        """The plate's status changing under the gesture, as a second
        client's command reaches this face: the payload republishes
        with the row's verdict already rewritten."""
        self._printer._set_excluded(name, excluded)
        self.pump(20)

    def _park_pointer(self, window, canvas, face):
        """Leave the pointer in the open, off every object. The
        pointer outlives the test's window and the offscreen platform
        drops a move onto the position it already holds, so a test
        that ended on a point a later test hovers first would starve
        that hover instead of failing on its own subject."""
        self._hover(window, canvas, face, 5.0, 5.0)


    def _bed_rect(self, canvas):
        """The mapped bed rectangle as the painter holds it."""
        bed = canvas.property("_plot").property("bed")
        return tuple(bed.property(name).toNumber() for name in
                     ("bedXMin", "bedXMax", "bedYMin", "bedYMax"))

    @staticmethod
    def _ink_count(image):
        """Pixels off the window's clear colour (the corner) — the
        grid's own ink, counted without assuming a palette."""
        blank = image.pixelColor(0, 0)
        return sum(1 for y in range(image.height())
                   for x in range(image.width())
                   if image.pixelColor(x, y) != blank)


if QT_AVAILABLE:

    class PlateDownloadPrinterDouble(QObject):
        """The download action's printer surface. The call counter is
        the click oracle for the whole-row target: a row that stopped
        routing clicks to the label would leave it at zero."""

        improvingEtaChanged = pyqtSignal()
        monitorConnectedChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.improve_eta_calls = 0

        @pyqtProperty(bool, notify=improvingEtaChanged)
        def improvingEta(self):
            return False

        @pyqtProperty(float, notify=improvingEtaChanged)
        def improveEtaProgress(self):
            return -1.0

        @pyqtProperty(str, notify=improvingEtaChanged)
        def improveEtaPhase(self):
            return ""

        @pyqtProperty(bool, notify=monitorConnectedChanged)
        def monitorConnected(self):
            return True

        @pyqtSlot()
        def improveEta(self):
            self.improve_eta_calls += 1


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class PlateDownloadActionTests(RealEngineTestCase):
    """The plate cards' download action must stay ONE click target
    without anchoring a layout-managed item: anchoring a child of a
    layout is undefined behaviour and the engine warns about it on
    every mount. The MouseArea therefore lives in a plain Item whose
    anchored RowLayout carries the glyph and the label."""

    def _mount_action(self, width=260):
        # The printer is RETAINED: a Python-created QObject dies with
        # its last Python ref and the QML var then reads null.
        printer = PlateDownloadPrinterDouble()
        self._printer = printer
        document, window = self.mount_window("PlateDownloadAction.qml", width, 240)
        document.setProperty("printerModel", printer)
        self.pump(30)
        return document, window, printer

    def _click_centre(self, item, window):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        self.pump(10)

    def _instruction_row(self, document):
        """The label the user reads and the MouseArea over it."""
        label = area = None
        for item in document.findChildren(QQuickItem):
            name = item.metaObject().className()
            text = item.property("text")
            if label is None and "Label" in name and isinstance(text, str) and text:
                label = item
            if area is None and name == "QQuickMouseArea":
                area = item
        self.assertIsNotNone(label, "the instruction label did not build")
        self.assertIsNotNone(area, "the instruction row's MouseArea did not build")
        return label, area


# --------------------------------------------------------------------------
# The settings pane (MoonrakerFollowerConfiguration.qml) on the real
# engine: the Diagnostics tab's persistent-cache-size field and its
# range copy, the seek-trace toggle, the cache-clear button's effect
# and status row, and the interval sliders' grab contract. The page
# mounts with the REAL MoonrakerFollowerMachineAction as its manager
# context object; the controls are clicked the way a user hits them
# and the assertions read the PrinterConfig the save wrote. The
# validator semantics stay in test_machine_action_coverage.py; this
# block owns the QML wiring.
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.qt_runtime_support import runtime  # noqa: E402

if QT_AVAILABLE:
    from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, pyqtSignal
    from PyQt6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
    from PyQt6.QtQuick import QQuickItem, QQuickWindow

    # Stands in for Cura's DefinitionContainer: the action is imported
    # over it and the registry double passes its instances through.
    _DefinitionContainer = type("DefinitionContainer", (), {})

    class _MachineActionBase(QObject):
        """Cura's MachineAction contract: the stored key and label."""

        def __init__(self, key, label):
            super().__init__()
            self._key = key
            self._label = label

        def getKey(self):
            return self._key

        def getLabel(self):
            return self._label

    class _Registry(QObject):
        containerAdded = pyqtSignal(object)

    class _Application(QObject):
        """The host the action registers itself with. The page itself
        never touches it (its QML context object is the action)."""

        globalContainerStackChanged = pyqtSignal()

        def getContainerRegistry(self):
            return _Registry()

        def getMachineActionManager(self):
            return SimpleNamespace(addSupportedAction=Mock())

    class _Follower:
        """The facade the action reads and writes: the current printer's
        config, and whether the persistence layer accepted the write.
        ``apply_printer_config`` installs the new config the way the real
        facade does, so a saved setting republishes through the manager's
        properties."""

        def __init__(self, config):
            self.config = config
            self.identity = ("printer-a", "Printer A")
            self.applied = []
            self.apply_result = True
            self.persistence = None

        def current_printer_config(self):
            return self.config

        def current_printer_identity(self):
            return self.identity

        def apply_printer_config(self, config):
            self.applied.append(config)
            self.config = config
            return self.apply_result

    class _ActionDialogDouble(QObject):
        """The dialog the page calls close() on after a save."""

        accepted = pyqtSignal()
        rejected = pyqtSignal()
        closing = pyqtSignal()

        def close(self):
            pass

    class _CatalogDouble(QObject):
        """Cura's translation catalog, read by the themed controls."""

        @pyqtSlot(str, str, result=str)
        def i18nc(self, _context, text):
            return text

        @pyqtSlot(str, result=str)
        def i18n(self, text):
            return text

    # The context-property wrappers must outlive the documents they are
    # bound to: PyQt6 releases a wrapper when the last Python reference
    # goes, and QML then sees a null context object mid-teardown.
    _LIVE_CONTEXT_OBJECTS = []


class SettingsPageCase(RealEngineTestCase):
    """The settings pane mounted offscreen against the real machine
    action, in a real window (a windowless mount never lays out twice)."""

    DIAGNOSTICS_TAB = 4

    def setUp(self):
        super().setUp()
        # runtime() brings the Cura module stubs the plugin imports over
        # (UM.Logger, UM.Resources, UM.Preferences...); the registry and
        # container stubs are added for the action's registration path.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.qt = stack.enter_context(runtime())
        stack.enter_context(patch.dict(sys.modules, {
            "cura.MachineAction": SimpleNamespace(MachineAction=_MachineActionBase),
            "UM.Settings": SimpleNamespace(
                DefinitionContainer=SimpleNamespace(DefinitionContainer=_DefinitionContainer)),
            "UM.Settings.DefinitionContainer": SimpleNamespace(
                DefinitionContainer=_DefinitionContainer),
        }))
        self.module = self.qt.load("MoonrakerFollowerMachineAction")
        self.printer_config = self.qt.load("PrinterConfig")

    # ---- mounting ----------------------------------------------------

    def settings_config(self, **overrides):
        """A configured printer. The usable URL is what the page reads to
        make its Save button live: a placeholder URL leaves canSave false
        and every click on Save inert."""
        settings = {"url": "http://printer-a:7125/"}
        settings.update(overrides)
        return self.printer_config.PrinterConfig(**settings)

    def open_settings(self, config=None, *, tab=0, width=760, height=1100):
        """Mount the page in a window and make one tab current."""
        self.follower = _Follower(config if config is not None else self.settings_config())
        self.action = self.module.MoonrakerFollowerMachineAction(_Application(), self.follower, None)
        dialog = _ActionDialogDouble()
        catalog = _CatalogDouble()
        _LIVE_CONTEXT_OBJECTS.extend((self.action, dialog, catalog))
        context = self.engine.rootContext()
        context.setContextProperty("manager", self.action)
        context.setContextProperty("actionDialog", dialog)
        context.setContextProperty("catalog", catalog)
        document = self.mount("MoonrakerFollowerConfiguration.qml")
        window = QQuickWindow()
        window.resize(width, height)
        document.setParentItem(window.contentItem())
        document.setWidth(width)
        document.setHeight(height)
        window.show()
        self.addCleanup(self._close_page, document, window)
        self.pump(30)
        self.show_tab(document, tab)
        return document, window

    def _close_page(self, document, window):
        # Detach before the engine tears the scene down, while the
        # context-object wrappers are still referenced.
        document.setParentItem(None)
        document.deleteLater()
        window.close()
        self.pump(20)

    def show_tab(self, document, index):
        for candidate in document.findChildren(QQuickItem):
            meta = candidate.metaObject()
            name = meta.className() if meta is not None else ""
            if name == "QQuickTabBar" or name.startswith("TabRow_"):
                candidate.setProperty("currentIndex", index)
                self.pump(20)
                self._pump_ms(100)
                return
        self.fail("the settings tab bar did not mount")

    # ---- finding -----------------------------------------------------

    def item_with_text(self, document, text):
        """The control whose text is exactly this. The themed controls
        alias their text onto an inner label, so the match is narrowed to
        the one item that owns the text and knows how to be clicked."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if item.property("text") == text
                   and not item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(matches),
                         "expected exactly one control reading %r, got %d" % (text, len(matches)))
        return matches[0]

    def cache_size_field(self, document):
        """The persistent-cache-size field: the TextField on the row the
        Diagnostics copy labels 'Persistent cache size (MiB)'."""
        row = self._row_of(self.label_with_text(document, "Persistent cache size (MiB)"))
        fields = [child for child in row.childItems()
                  if child.property("maximumLength") is not None]
        self.assertEqual(1, len(fields), "the cache-size row holds no single field")
        return fields[0]

    def cache_range_copy(self, document):
        """The inline range copy on the cache-size row."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if isinstance(item.property("text"), str)
                   and item.property("text").startswith("Cache size must be between")]
        self.assertEqual(1, len(matches), "the cache-size range copy never rendered")
        return matches[0]

    def seek_trace_box(self, document):
        """The seek-timeline diagnostics toggle."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if isinstance(item.property("text"), str)
                   and item.property("text").startswith("Log follower seek timelines")
                   and item.property("checked") is not None]
        self.assertEqual(1, len(matches), "the seek-trace toggle never rendered")
        return matches[0]

    def clear_cache_button(self, document):
        return self.item_with_text(document, "Clear cached downloads and indexes")

    def label_with_text(self, document, text):
        matches = [item for item in document.findChildren(QQuickItem)
                   if item.property("text") == text
                   and item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(matches),
                         "expected exactly one label reading %r, got %d" % (text, len(matches)))
        return matches[0]

    def interval_slider(self, document, label_text):
        """The slider on the row the caption labels — the caption is a
        sibling of its RowLayout in the column, the row directly below."""
        row = self._row_of(self.label_with_text(document, label_text))
        sliders = [child for child in row.childItems()
                   if "Slider" in child.metaObject().className()
                   and child.property("handle") is not None]
        self.assertEqual(1, len(sliders), "the %s row holds no single slider" % label_text)
        return sliders[0]

    @staticmethod
    def _row_of(label):
        """The RowLayout that lays the label out: the label's own parent
        when it sits inside its row (the Diagnostics cache row), else the
        first row under it in the same column (the interval captions)."""
        parent = label.parentItem()
        if parent.metaObject().className() == "QQuickRowLayout":
            return parent
        label_top = label.mapToItem(parent, QPointF(0.0, 0.0)).y()
        rows = [child for child in parent.childItems()
                if child.metaObject().className() == "QQuickRowLayout"
                and child.mapToItem(parent, QPointF(0.0, 0.0)).y() > label_top]
        if not rows:
            raise AssertionError("no row renders under the label")
        return min(rows, key=lambda row: row.mapToItem(parent, QPointF(0.0, 0.0)).y())

    # ---- driving -----------------------------------------------------

    def click_item(self, window, item, *, dx=0.5, dy=0.5):
        """A real press-and-release at a point inside the item, refused
        unless the point is actually on screen (a click outside the
        window is silently dropped and would read as a pass)."""
        scene = item.mapToScene(QPointF(item.width() * dx, item.height() * dy))
        self._assert_on_screen(window, scene)
        self._send_mouse(window, QEvent.Type.MouseButtonPress, scene,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        self._send_mouse(window, QEvent.Type.MouseButtonRelease, scene,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.pump(20)

    def activate_item(self, window, item):
        """Drive the control the way the packaged harness's click_item
        drives objectName'd controls: a themed Cura control layers its
        label over the clickable region, so a coordinate click can be
        swallowed before the handler — the driver emits ``clicked`` when
        the control carries that signal (the exact path a real click
        drives, TESTING.md). Controls without one still get the real
        press-and-release. This proves the handler wiring, not
        coordinate hit-testing — a control occluded by an overlapping
        item stays green here; the harness scenario suite owns that."""
        emit = getattr(getattr(item, "clicked", None), "emit", None)
        if emit is None:
            self.click_item(window, item)
            return
        emit()
        self.pump(20)

    @staticmethod
    def _assert_on_screen(window, scene):
        if not (0.0 <= scene.x() <= window.width() and 0.0 <= scene.y() <= window.height()):
            raise AssertionError("the click at (%.1f, %.1f) is outside the %dx%d window"
                                 % (scene.x(), scene.y(), window.width(), window.height()))

    @staticmethod
    def _send_mouse(window, kind, scene, buttons, button):
        QGuiApplication.sendEvent(window, QMouseEvent(
            kind, QPointF(scene),
            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            button, buttons, Qt.KeyboardModifier.NoModifier))

    def press_key(self, window, key):
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(window, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))
        self.pump(20)

    def save_button(self, document):
        return self.item_with_text(document, "Save")

    def type_cache_size(self, document, text):
        """Type into the field the way a user does: the text changes, the
        binding on the field is replaced, and the page re-validates."""
        field = self.cache_size_field(document)
        field.setProperty("text", text)
        self.pump(20)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsCacheSizeTests(SettingsPageCase):
    """The Diagnostics tab's persistent-cache-size field (4.6.0): the
    limit is per printer, the field is seeded from the stored one, and an
    out-of-range entry is refused before it reaches the settings file."""


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsSeekTraceTests(SettingsPageCase):
    """The Diagnostics tab's seek-timeline toggle (4.6.0): it shows the
    stored setting, follows a change to it, and saves as its own key."""


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsCacheClearTests(SettingsPageCase):
    """The Diagnostics tab's cache-clear button: it wipes the persistent
    cache root (the drop that forces a full re-download and re-index) and
    reports the outcome on its own row rather than in a dialog."""


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class IntervalSliderGrabTests(SettingsPageCase):
    """The interval sliders' grab contract (the 4.6.0 reviewer finding:
    the window ignored the handle's width, so the half of the painted
    handle away from the value never grabbed and the click fell through
    to the track, which jumps). A track click still jumps — that is the
    control that keeps this suite honest."""

    SLIDERS = ("Status update interval (milliseconds)",
               "Auxiliary status interval (milliseconds)",
               "Console output interval (milliseconds)")

    def _painted_handle(self, slider):
        """The handle's own geometry — where it was actually painted, not
        where a formula says it should be."""
        handle = slider.property("handle")
        return handle.x(), handle.x() + handle.width()

    def _press_and_release(self, window, slider, x, dx=0.0):
        y = slider.height() / 2
        start = slider.mapToItem(window.contentItem(), QPointF(x, y))
        end = slider.mapToItem(window.contentItem(), QPointF(x + dx, y))
        self._assert_on_screen(window, start)
        self._send_mouse(window, QEvent.Type.MouseButtonPress, start,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        if dx:
            self._send_mouse(window, QEvent.Type.MouseMove, end,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self._send_mouse(window, QEvent.Type.MouseButtonRelease, end,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.pump(20)


    def _check_grab(self, window, slider):
        left, right = self._painted_handle(slider)
        floor = slider.property("from")
        anchor = slider.property("value")
        # A press on the handle is inert wherever on the handle it lands.
        for x in (left + 1.0, (left + right) / 2.0, right - 0.5):
            self._press_and_release(window, slider, x)
            self.assertEqual(anchor, slider.property("value"),
                             "a press at %.1f moved the slider off its handle" % x)
        # ... and a drag out of either tip grabs it and carries it along.
        # Setting the value directly is the harness's stand-in for the
        # operator having put the handle there.
        for x in (left + 1.0, right - 0.5):
            slider.setProperty("value", floor)
            self.pump(20)
            self._press_and_release(window, slider, x, dx=60.0)
            self.assertGreater(slider.property("value"), floor,
                               "a drag from the handle at %.1f never grabbed" % x)


# --------------------------------------------------------------------------
# The follower-view feed's device-pixel contract: the popover's own QML
# call passes the device-pixel-ratio as a NINTH argument, the mini's
# compact feed stops at eight, and the ratio is a pixel input of the
# navigation raster — a change must re-bake it. This block runs the REAL
# MoonrakerMonitorModel in this process (the runtime() stubs) beside the
# engine's documents: the model is the only thing that can answer for
# its own slot arity.
if QT_AVAILABLE:
    class _DprMesh(QObject):
        """The bed-mesh double the model wants: the navigation raster's
        grid reads only the machine bounds off it."""

        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.snapshot = {}
            self.visible = True

        def set_thresholds(self, low, high):
            pass

        def set_visible(self, value):
            self.visible = value
            self.changed.emit()


    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    class FollowerViewDprTests(RealEngineTestCase):
        """The follower view feed's argument list and the DPR's ride
        through the model's view dict into the navigation raster."""

        # The two production call sites' argument lists verbatim
        # (MoonrakerMonitor.qml's _feedRenderView and
        # PlateProgressSection.qml's compact feed). The DPR expression
        # around it is the production one; Screen.devicePixelRatio is
        # read-only and 1.0 offscreen, so a property stands in for the
        # screen.
        CALLER = b'''
import QtQuick 2.15
QtObject {
    property var printer: null
    property real screenDpr: 1.0
    property string failure: ""

    function feedPopover() {
        printer.setFollowerView("popover", 1.0, 0.7, 400, 300, false, 0.0, 0.0,
                                Math.min(2.0, Math.max(1.0, screenDpr)));
    }
    function feedMini() {
        printer.setFollowerView("mini", 1.0, 0.7, 120, 120, true, 0.0, 0.0);
    }
    function runPopover() {
        try { feedPopover(); failure = ""; } catch (error) { failure = String(error); }
    }
    function runMini() {
        try { feedMini(); failure = ""; } catch (error) { failure = String(error); }
    }
}
'''

        def setUp(self):
            super().setUp()
            stack = contextlib.ExitStack()
            self.addCleanup(stack.close)
            self.qt = stack.enter_context(runtime())
            from tests.qt_runtime_support import ScriptedTransport
            client = self.qt.load("MoonrakerClient").MoonrakerClient(
                transport=ScriptedTransport())
            client.configure("http://printer-a", "test-key", 750)
            client._poll_timer.stop()
            self.addCleanup(client.stop)
            print_state = self.qt.load("PrintState").PrintSnapshot()
            config = self.qt.load("PrinterConfig").PrinterConfig(
                url="http://printer-a", api_key="test-key")
            self.model = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel(
                None, 1,
                client=client,
                print_state=lambda: print_state,
                config=lambda: config,
                apply_config=lambda value: None,
                bed_mesh=_DprMesh(),
                identity=lambda: ("A", "Printer A"))
            self.addCleanup(self.model.setMonitoringActive, False)
            component = QQmlComponent(self.engine)
            component.setData(self.CALLER, QUrl.fromLocalFile(
                str(ROOT / "mpf" / "follower-view-caller.qml")))
            self.caller = component.create()
            self.assertIsNotNone(self.caller, qml_error_report(component))
            self.addCleanup(self.caller.deleteLater)
            self.caller.setProperty("printer", self.model)

        # ---- helpers --------------------------------------------------

        def _call(self, name):
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(self.caller, name)
            self.assertEqual(self.caller.property("failure"), "",
                             "the QML view call raised")
            self.qt.events(10)

        def _surface(self, name):
            return self.model.plate_renderer._surfaces[name]

        def _navigation_demand(self, surface, dpr):
            """A navigation demand on a hand-set context: no scheduler
            runs, so the key under test is the only thing moving."""
            surface.plot = {"offsetX": 10.0, "offsetY": 10.0, "sx": 3.0,
                            "sy": 3.0, "bedXMin": 0.0, "bedYMax": 250.0}
            surface.view = {"scale": 1.0, "lineScale": 0.7, "width": 400,
                            "height": 300, "compact": False,
                            "panX": 0.0, "panY": 0.0, "dpr": dpr}
            payload = {"classes": {"SKIN": [[[0.0, 0.0, 0.0], [250.0, 0.0, 3.0]]]},
                       "travels": [], "travelStarts": [], "travelEnds": [],
                       "motions": 4}
            self.model.plate_renderer._qt_layer(surface, payload, 5)
            surface.desired = {"current": 5, "ghosts": {}, "split": 2}
            return self.model.plate_renderer._navigation_key(surface)

        # ---- the slot's signatures ------------------------------------


        # ---- the DPR's ride into the navigation raster -----------------


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['CameraFpsControlTests', 'CameraModelDouble', 'CameraOwnershipTests', 'CameraTitleRowTests', 'ChartColourPickerTests', 'ChartSurfaceTests', 'CollapseOnShrinkTests', 'ConsoleInputRowTests', 'CuraApplicationDouble', 'EscapeLadderTests', 'FollowerViewDprTests', 'IntervalSliderGrabTests', 'LateBedDouble', 'Mock', 'OutputDeviceDouble', 'PaneGutterTests', 'PauseRowRoleTests', 'PlateCanvasHitTests', 'PlateDownloadActionTests', 'PlateDownloadPrinterDouble', 'PlateFaceRenderTests', 'PlateJobBoundaryTests', 'PlatePrinterDouble', 'PlateProjectionMemo', 'PrinterModelDouble', 'QColor', 'QCoreApplication', 'QEvent', 'QGuiApplication', 'QKeyEvent', 'QMetaObject', 'QMouseEvent', 'QObject', 'QPoint', 'QPointF', 'QQmlComponent', 'QQmlEngine', 'QQuickItem', 'QQuickWindow', 'QRectF', 'QT_AVAILABLE', 'QUrl', 'Qt', 'ROOT', 'ReExpansionGuardTests', 'RealEngineTestCase', 'SectionOrderArrivalTests', 'SettingsCacheClearTests', 'SettingsCacheSizeTests', 'SettingsPageCase', 'SettingsSeekTraceTests', 'SimpleNamespace', 'StatusColumnGeometryTests', 'StripVerdictRefreshTests', 'TuningResetConvergenceTests', 'TuningResetTests', '_APPLICATION', '_ActionDialogDouble', '_Application', '_CatalogDouble', '_DefinitionContainer', '_DprMesh', '_ENV_REPORTED', '_Follower', '_LIVE_CONTEXT_OBJECTS', '_MachineActionBase', '_PLATE_TRAVEL_VISUAL_RATIO', '_Registry', 'point_in_polygon', '_report_environment', '_start_application', 'annotations', 'build_index_from_bytes', 'contextlib', 'layer_polylines', 'os', 'patch', 'pathlib', 'polygon_bounds', 'pyqtProperty', 'pyqtSignal', 'pyqtSlot', 'qInstallMessageHandler', 'qml_error_report', 'qml_geometry', 'runtime', 'sys', 'tempfile', 'time', 'unittest']
