"""Real Qt, minimal Cura host doubles, and isolated production-package imports.

No plugin method is copied or mocked by this harness. Qt tests are optional for
stdlib-only development and mandatory in the dedicated CI runtime job.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import importlib.util
import pathlib
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

QT_AVAILABLE = importlib.util.find_spec("PyQt6") is not None
ROOT = pathlib.Path(__file__).resolve().parents[1]


class Preferences:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def addPreference(self, key, default):
        self.values.setdefault(key, default)

    def getValue(self, key):
        return self.values.get(key)

    def setValue(self, key, value):
        self.values[key] = value


class ScriptedTransport:
    """Record requests; tests explicitly deliver replies, including stale replies."""
    def __init__(self):
        self.identity = ("", "")
        self.generation = 0
        self.requests = []
        self.cancelled = []
        self.metrics = {}

    def configure(self, url, key):
        identity = (url.rstrip("/"), key)
        if identity == self.identity:
            return False
        self.cancel_all()
        self.identity = identity
        self.generation += 1
        return True

    def send_json(self, owner, channel, method, path, callback, **kwargs):
        self.requests.append(SimpleNamespace(owner=owner, channel=channel,
            method=method, path=path, callback=callback, options=kwargs))
        return True

    def cancel(self, owner, channel):
        self.cancelled.append((owner, channel))

    def cancel_owner(self, owner):
        self.cancelled.append((owner, None))

    def cancel_all(self):
        self.cancelled.append((None, None))

    def request(self, path, *, timeout_ms=5000):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtNetwork import QNetworkRequest
        return QNetworkRequest(QUrl(path if path.startswith("http") else self.identity[0] + "/" + path.lstrip("/")))


@contextmanager
def runtime():
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, pyqtSignal

    app = QCoreApplication.instance() or QCoreApplication([])

    class PrinterModel(QObject):
        def __init__(self, *_args):
            super().__init__()
            self._extruders = []

        def updateName(self, value):
            self.name = value

        def updateUniqueName(self, value):
            self.unique_name = value

        def updateBuildplate(self, value):
            self.buildplate = value

    class OutputDevice(QObject):
        writeStarted = pyqtSignal(object)
        writeFinished = pyqtSignal(object)
        writeError = pyqtSignal(object)
        writeSuccess = pyqtSignal(object)
        writeProgress = pyqtSignal(object, int)

        def __init__(self, device_id, connection_type):
            super().__init__()
            self._id = device_id
            self._name = "Printer"

        def getId(self):
            return self._id

        def setName(self, value):
            self._name = value

        def getName(self):
            return self._name

        def setDescription(self, value): pass
        def setShortDescription(self, value): pass
        def setIconName(self, value): pass
        def setConnectionText(self, value): pass
        def setPriority(self, value): pass

        @property
        def activePrinter(self):
            return self._printers[0]

    class OutputPlugin:
        def __init__(self):
            self.manager = SimpleNamespace(addOutputDevice=Mock(), removeOutputDevice=Mock())

        def getOutputDeviceManager(self):
            return self.manager

    class Message(QObject):
        actionTriggered = pyqtSignal(object, str)
        def __init__(self, *_args): super().__init__()
        def setTitle(self, value): pass
        def setText(self, value): pass
        def setProgress(self, value): pass
        def addAction(self, *_args): pass
        def show(self): pass
        def hide(self): pass

    class Root(QObject):
        childrenChanged = pyqtSignal()

    class Scene(QObject):
        sceneChanged = pyqtSignal(object)
        def __init__(self):
            super().__init__()
            self.root = Root()
        def getRoot(self): return self.root

    class Controller(QObject):
        activeViewChanged = pyqtSignal()
        activeStageChanged = pyqtSignal()
        def __init__(self):
            super().__init__()
            self.scene = Scene()
            self.view = None
        def getScene(self): return self.scene
        def getView(self, _name): return self.view
        def getActiveStage(self): return None

    class Machine:
        def __init__(self, machine_id="A"):
            self.machine_id = machine_id
        def getId(self): return self.machine_id
        def getName(self): return self.machine_id
        def getProperty(self, name, role):
            return {"machine_extruder_count": 1, "machine_width": 200,
                    "machine_depth": 200, "machine_center_is_zero": False}.get(name, "glass")

    class Application(QObject):
        globalContainerStackChanged = pyqtSignal()
        mainWindowChanged = pyqtSignal()
        fileCompleted = pyqtSignal(str)
        additionalComponentsChanged = pyqtSignal(str)
        def __init__(self, preferences=None, machine=True):
            super().__init__()
            self.preferences = preferences or Preferences()
            self.stack = Machine() if machine else None
            self.controller = Controller()
            self.loaded_paths = []
            self.writer = SimpleNamespace(write=lambda stream, node: bool(stream.write("G1 X0\n")))
        def getPreferences(self): return self.preferences
        def getGlobalContainerStack(self): return self.stack
        def getController(self): return self.controller
        def getBackend(self): return None
        def getMainWindow(self): return None
        def getPrintInformation(self): return SimpleNamespace(jobName="part", preSliced=False)
        def getPluginRegistry(self): return SimpleNamespace(getPluginObject=lambda name: self.writer)
        def readLocalFile(self, url, add_to_recent_files=False): self.loaded_paths.append(url.toLocalFile())

    modules = {}
    def module(name, **attrs):
        parent, _, _leaf = name.rpartition(".")
        if parent and parent not in modules:
            module(parent)
        result = ModuleType(name)
        result.__path__ = []
        result.__dict__.update(attrs)
        modules[name] = result
        return result

    with tempfile.TemporaryDirectory(prefix="moonraker-test-cache-") as cache:
        module("UM.Logger", Logger=SimpleNamespace(log=Mock(), logException=Mock()))
        module("UM.Extension", Extension=type("Extension", (), {}))
        module("UM.Resources", Resources=SimpleNamespace(getCacheStoragePath=lambda: cache))
        module("UM.Backend.Backend", BackendState=SimpleNamespace(Done=1))
        module("UM.Mesh.MeshWriter", MeshWriter=type("MeshWriter", (), {}))
        module("UM.Message", Message=Message)
        module("UM.OutputDevice.OutputDevicePlugin", OutputDevicePlugin=OutputPlugin)
        modules["UM.OutputDevice"].OutputDeviceError = SimpleNamespace(DeviceBusyError=type("DeviceBusyError", (Exception,), {}))
        module("cura.PrinterOutput.Models.PrinterOutputModel", PrinterOutputModel=PrinterModel)
        module("cura.PrinterOutput.PrinterOutputController", PrinterOutputController=type("Controller", (), {"__init__": lambda self, device: None}))
        module("cura.PrinterOutput.PrinterOutputDevice", PrinterOutputDevice=OutputDevice,
               ConnectionType=SimpleNamespace(NetworkConnection=1))
        package = module("_moonraker_runtime_test")
        package.__path__ = [str(ROOT / "plugins")]

        def load(name):
            return importlib.import_module(package.__name__ + "." + name)

        def process_events(milliseconds=0):
            if milliseconds:
                loop = QEventLoop()
                QTimer.singleShot(milliseconds, loop.quit)
                loop.exec()
            else:
                app.processEvents()
                app.processEvents()

        with patch.dict(sys.modules, modules):
            yield SimpleNamespace(load=load, app=app, Application=Application,
                Machine=Machine, events=process_events, QObject=QObject, QTimer=QTimer)


