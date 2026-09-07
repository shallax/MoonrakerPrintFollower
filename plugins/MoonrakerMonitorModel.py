"""One Cura Qt model: declarations and composition, not an inheritance stack."""
from __future__ import annotations
from copy import deepcopy
from PyQt6.QtCore import QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from .MonitorCamera import MonitorCamera
from .MonitorCommands import MonitorCommands
from .MonitorControls import MonitorControls
from .MonitorData import MonitorData
from .MonitorFormatting import core_values, peripheral_values
from .MonitorTuning import MonitorTuning


def value_property(kind, name, signal, default=None):
    """Declarative Qt binding, not a domain-state forwarding mechanism."""
    def read(self):
        value = deepcopy(self._values.get(name, default))
        return QVariant(value) if kind is QVariant else value
    return pyqtProperty(kind, read, notify=signal)


class MoonrakerMonitorModel(PrinterOutputModel):
    monitorChanged = pyqtSignal()
    webcamsChanged = pyqtSignal()
    cameraTransformChanged = pyqtSignal()
    peripheralsChanged = pyqtSignal()
    excludeObjectsChanged = pyqtSignal()
    powerDevicesChanged = pyqtSignal()
    systemChanged = pyqtSignal()
    actionChanged = pyqtSignal()
    controlsChanged = pyqtSignal()
    emergencyStopChanged = pyqtSignal()
    typedControlsChanged = pyqtSignal()

    _SIGNAL_KEYS = (
        ("monitorChanged", ("monitorState", "monitorFilename", "monitorProgress", "monitorLayer", "monitorElapsed",
                            "monitorEta", "monitorFinish", "monitorSpeed", "monitorFlow", "monitorPosition", "monitorMessage")),
        ("webcamsChanged", ("webcamNames", "activeWebcamIndex")),
        ("cameraTransformChanged", ("cameraName", "cameraRotation", "cameraFlipHorizontal", "cameraFlipVertical")),
        ("peripheralsChanged", ("temperatureItems", "fanItems", "filamentSensorItems")),
        ("excludeObjectsChanged", ("excludeObjectItems",)),
        ("powerDevicesChanged", ("powerDevices",)),
        ("systemChanged", ("klippyState", "moonrakerVersion", "klipperVersion", "hostLoad", "memoryAvailable",
                           "cpuTemperature", "mcuSummary", "mcuItems")),
        ("actionChanged", ("printActive", "canPausePrint", "canResumePrint", "canCancelPrint", "actionBusy", "actionStatus")),
        ("controlsChanged", ("monitorLayerHeight", "macroNames", "hasQuadGantryLevel", "hasBedMesh", "canRunSetup",
                             "temperaturePresetNames", "canApplyTemperaturePreset", "speedFactorPercent", "flowFactorPercent",
                             "zOffset", "zOffsetText", "fanControlItems", "ledItems", "saveConfigPending", "saveConfigSummary",
                             "canSaveConfig")),
        ("emergencyStopChanged", ("emergencyStopClicks",)),
        ("typedControlsChanged", ("temperaturePresetItems", "pwmOutputItems", "bedMeshAvailable", "bedMeshProfile",
                                  "bedMeshProfileNames", "bedMeshRows", "bedMeshColumns", "bedMeshValues", "bedMeshMinimum",
                                  "bedMeshMaximum", "bedMeshRange", "bedMeshXMin", "bedMeshXMax", "bedMeshYMin", "bedMeshYMax",
                                  "bedMeshRangeText", "bedMeshPreviewVisible")),
    )

    def __init__(self, output_controller, number_of_extruders, *, client, print_state, config, apply_config, bed_mesh):
        super().__init__(output_controller, number_of_extruders)
        self._client, self._print_state, self._config, self._mesh = client, print_state, config, bed_mesh
        self._values = {}
        self._data = MonitorData(client, self)
        self._commands = MonitorCommands(self._data, self)
        self._tuning = MonitorTuning(self._data, self._commands, self)
        self._controls = MonitorControls(self._data, self._commands, self._tuning, bed_mesh, config, self)
        self._camera = MonitorCamera(self._data, config, apply_config, self)
        for signal in (self._data.changed, self._commands.changed, self._controls.changed, self._camera.changed, bed_mesh.changed):
            signal.connect(self._publish)
        self._data.set_active(True)
        self._publish()

    def setMonitoringActive(self, active): self._data.set_active(active)

    def _publish(self):
        previous = self._values
        values = core_values(self._data.snapshot, self._print_state(), self._client.connected)
        values.update(peripheral_values(self._data.snapshot))
        values.update(self._controls.values)
        values.update(self._camera.values)
        commands, mesh = self._commands, self._mesh.snapshot
        values.update(printActive=commands.print_active, canPausePrint=commands.state == "printing" and not commands.busy,
            canResumePrint=commands.state == "paused" and not commands.busy,
            canCancelPrint=commands.print_active and not commands.busy, actionBusy=commands.busy,
            actionStatus=commands.status, emergencyStopClicks=commands.clicks, powerDevices=self._controls.power_devices(),
            bedMeshAvailable=bool(mesh), bedMeshProfile=str(mesh.get("profile") or "Current mesh") if mesh else "",
            bedMeshRows=int(mesh.get("rows") or 0), bedMeshColumns=int(mesh.get("columns") or 0),
            bedMeshValues=list(mesh.get("values") or ()), bedMeshMinimum=float(mesh.get("minimum") or 0),
            bedMeshMaximum=float(mesh.get("maximum") or 0), bedMeshRange=float(mesh.get("range") or 0),
            bedMeshXMin=float(mesh.get("xMin") or 0), bedMeshXMax=float(mesh.get("xMax") or 0),
            bedMeshYMin=float(mesh.get("yMin") or 0), bedMeshYMax=float(mesh.get("yMax") or 0),
            bedMeshRangeText=f"{float(mesh.get('range') or 0):.3f} mm range" if mesh else "",
            bedMeshPreviewVisible=self._mesh.visible)
        self._values = values
        try: self.setCameraUrl(QUrl(self._camera.url))
        except AttributeError: pass

        # Qt notify signals are part of control ownership. Broadcasting every
        # signal for every poll was re-evaluating bound ComboBox/Slider values
        # while the user was interacting with them, and QVariant-list updates
        # could also rebuild Repeater delegates mid-drag. Only notify the group
        # whose published values actually changed.
        for signal_name, keys in self._SIGNAL_KEYS:
            if any(previous.get(key) != values.get(key) for key in keys):
                getattr(self, signal_name).emit()

    monitorState = value_property(str, "monitorState", monitorChanged, "Not connected")
    monitorFilename = value_property(str, "monitorFilename", monitorChanged, "")
    monitorProgress = value_property(int, "monitorProgress", monitorChanged, 0)
    monitorLayer = value_property(str, "monitorLayer", monitorChanged, "—")
    monitorElapsed = value_property(str, "monitorElapsed", monitorChanged, "00:00:00")
    monitorEta = value_property(str, "monitorEta", monitorChanged, "—")
    monitorFinish = value_property(str, "monitorFinish", monitorChanged, "—")
    monitorSpeed = value_property(str, "monitorSpeed", monitorChanged, "100%")
    monitorFlow = value_property(str, "monitorFlow", monitorChanged, "100%")
    monitorPosition = value_property(str, "monitorPosition", monitorChanged, "—")
    monitorMessage = value_property(str, "monitorMessage", monitorChanged, "")
    printActive = value_property(bool, "printActive", actionChanged, False)
    canPausePrint = value_property(bool, "canPausePrint", actionChanged, False)
    canResumePrint = value_property(bool, "canResumePrint", actionChanged, False)
    canCancelPrint = value_property(bool, "canCancelPrint", actionChanged, False)
    actionBusy = value_property(bool, "actionBusy", actionChanged, False)
    actionStatus = value_property(str, "actionStatus", actionChanged, "")
    temperatureItems = value_property(QVariant, "temperatureItems", peripheralsChanged, [])
    fanItems = value_property(QVariant, "fanItems", peripheralsChanged, [])
    filamentSensorItems = value_property(QVariant, "filamentSensorItems", peripheralsChanged, [])
    excludeObjectItems = value_property(QVariant, "excludeObjectItems", excludeObjectsChanged, [])
    powerDevices = value_property(QVariant, "powerDevices", powerDevicesChanged, [])
    klippyState = value_property(str, "klippyState", systemChanged, "Unknown")
    moonrakerVersion = value_property(str, "moonrakerVersion", systemChanged, "—")
    klipperVersion = value_property(str, "klipperVersion", systemChanged, "—")
    hostLoad = value_property(str, "hostLoad", systemChanged, "—")
    memoryAvailable = value_property(str, "memoryAvailable", systemChanged, "—")
    cpuTemperature = value_property(str, "cpuTemperature", systemChanged, "—")
    mcuSummary = value_property(str, "mcuSummary", systemChanged, "—")
    mcuItems = value_property(QVariant, "mcuItems", systemChanged, [])
    webcamNames = value_property(QVariant, "webcamNames", webcamsChanged, [])
    activeWebcamIndex = value_property(int, "activeWebcamIndex", webcamsChanged, -1)
    cameraName = value_property(str, "cameraName", cameraTransformChanged, "")
    cameraRotation = value_property(int, "cameraRotation", cameraTransformChanged, 0)
    cameraFlipHorizontal = value_property(bool, "cameraFlipHorizontal", cameraTransformChanged, False)
    cameraFlipVertical = value_property(bool, "cameraFlipVertical", cameraTransformChanged, False)
    monitorLayerHeight = value_property(str, "monitorLayerHeight", controlsChanged, "—")
    macroNames = value_property(QVariant, "macroNames", controlsChanged, [])
    hasQuadGantryLevel = value_property(bool, "hasQuadGantryLevel", controlsChanged, False)
    hasBedMesh = value_property(bool, "hasBedMesh", controlsChanged, False)
    canRunSetup = value_property(bool, "canRunSetup", controlsChanged, False)
    temperaturePresetNames = value_property(QVariant, "temperaturePresetNames", controlsChanged, [])
    temperaturePresetItems = value_property(QVariant, "temperaturePresetItems", typedControlsChanged, [])
    canApplyTemperaturePreset = value_property(bool, "canApplyTemperaturePreset", controlsChanged, False)
    speedFactorPercent = value_property(int, "speedFactorPercent", controlsChanged, 100)
    flowFactorPercent = value_property(int, "flowFactorPercent", controlsChanged, 100)
    zOffset = value_property(float, "zOffset", controlsChanged, 0.0)
    zOffsetText = value_property(str, "zOffsetText", controlsChanged, "0.000 mm")
    fanControlItems = value_property(QVariant, "fanControlItems", controlsChanged, [])
    ledItems = value_property(QVariant, "ledItems", controlsChanged, [])
    pwmOutputItems = value_property(QVariant, "pwmOutputItems", typedControlsChanged, [])
    saveConfigPending = value_property(bool, "saveConfigPending", controlsChanged, False)
    saveConfigSummary = value_property(str, "saveConfigSummary", controlsChanged, "")
    canSaveConfig = value_property(bool, "canSaveConfig", controlsChanged, False)
    emergencyStopClicks = value_property(int, "emergencyStopClicks", emergencyStopChanged, 0)
    bedMeshAvailable = value_property(bool, "bedMeshAvailable", typedControlsChanged, False)
    bedMeshProfile = value_property(str, "bedMeshProfile", typedControlsChanged, "")
    bedMeshProfileNames = value_property(QVariant, "bedMeshProfileNames", typedControlsChanged, [])
    bedMeshRows = value_property(int, "bedMeshRows", typedControlsChanged, 0)
    bedMeshColumns = value_property(int, "bedMeshColumns", typedControlsChanged, 0)
    bedMeshValues = value_property(QVariant, "bedMeshValues", typedControlsChanged, [])
    bedMeshMinimum = value_property(float, "bedMeshMinimum", typedControlsChanged, 0.0)
    bedMeshMaximum = value_property(float, "bedMeshMaximum", typedControlsChanged, 0.0)
    bedMeshRange = value_property(float, "bedMeshRange", typedControlsChanged, 0.0)
    bedMeshXMin = value_property(float, "bedMeshXMin", typedControlsChanged, 0.0)
    bedMeshXMax = value_property(float, "bedMeshXMax", typedControlsChanged, 0.0)
    bedMeshYMin = value_property(float, "bedMeshYMin", typedControlsChanged, 0.0)
    bedMeshYMax = value_property(float, "bedMeshYMax", typedControlsChanged, 0.0)
    bedMeshRangeText = value_property(str, "bedMeshRangeText", typedControlsChanged, "")
    bedMeshPreviewVisible = value_property(bool, "bedMeshPreviewVisible", typedControlsChanged, True)

    @pyqtSlot()
    def refreshAll(self): self._data.refresh_all()
    @pyqtSlot()
    def refreshWebcams(self): self._data.refresh_webcams()
    @pyqtSlot(object)
    def updateMoonrakerStatus(self, status): self._data.observe(status)
    @pyqtSlot()
    def pausePrint(self):
        if self.canPausePrint: self._commands.send("Pause", "printer/print/pause")
    @pyqtSlot()
    def resumePrint(self):
        if self.canResumePrint: self._commands.send("Resume", "printer/print/resume")
    @pyqtSlot()
    def cancelPrint(self):
        if self.canCancelPrint: self._commands.send("Cancel", "printer/print/cancel")
    @pyqtSlot(str)
    def excludeObject(self, name): self._controls.exclude(name)
    @pyqtSlot(str, bool)
    def setPowerDevice(self, name, on): self._controls.set_power(name, on)
    @pyqtSlot(int)
    def selectWebcam(self, index): self._camera.select(index)
    @pyqtSlot()
    def openFrontend(self):
        config = self._config()
        QDesktopServices.openUrl(QUrl(config.frontend_target))
    @pyqtSlot(str, str)
    def runMacro(self, name, arguments=""): self._controls.run_macro(name, arguments)
    @pyqtSlot()
    def homeAll(self): self._controls.setup("home")
    @pyqtSlot()
    def runQuadGantryLevel(self): self._controls.setup("qgl")
    @pyqtSlot()
    def calibrateBedMesh(self): self._controls.setup("mesh")
    @pyqtSlot(int)
    def applyTemperaturePreset(self, index): self._controls.apply_preset(index)
    @pyqtSlot(int)
    def previewSpeedFactor(self, percent): self._controls.factor("speed", percent, True)
    @pyqtSlot(int)
    def setSpeedFactor(self, percent): self._controls.factor("speed", percent)
    @pyqtSlot(int)
    def previewFlowFactor(self, percent): self._controls.factor("flow", percent, True)
    @pyqtSlot(int)
    def setFlowFactor(self, percent): self._controls.factor("flow", percent)
    @pyqtSlot(float)
    def adjustZOffset(self, amount): self._controls.z_offset(amount)
    @pyqtSlot()
    def clearZOffset(self): self._controls.z_offset()
    @pyqtSlot(str, int)
    def previewFanSpeed(self, name, percent): self._controls.output("fan", name, percent, True)
    @pyqtSlot(str, int)
    def setFanSpeed(self, name, percent): self._controls.output("fan", name, percent)
    @pyqtSlot(str, int)
    def previewLedBrightness(self, name, percent): self._controls.output("led-brightness", name, percent, True)
    @pyqtSlot(str, int)
    def setLedBrightness(self, name, percent): self._controls.output("led-brightness", name, percent)
    @pyqtSlot(str, int, int, int, int, int)
    def previewLedColor(self, name, r, g, b, w=0, brightness=-1): self._controls.led_color(name, r, g, b, w, brightness, True)
    @pyqtSlot(str, int, int, int, int, int)
    def setLedColor(self, name, r, g, b, w=0, brightness=-1): self._controls.led_color(name, r, g, b, w, brightness)
    @pyqtSlot(str, int)
    def previewPwmOutput(self, name, percent): self._controls.output("pwm-output", name, percent, True)
    @pyqtSlot(str, int)
    def setPwmOutput(self, name, percent): self._controls.output("pwm-output", name, percent)
    @pyqtSlot()
    def saveConfig(self): self._controls.setup("save")
    @pyqtSlot()
    def emergencyStopClick(self): self._commands.emergency_click()
    @pyqtSlot(str)
    def loadBedMeshProfile(self, name): self._controls.mesh_profile(name)
    @pyqtSlot()
    def clearBedMesh(self): self._controls.clear_mesh()
    @pyqtSlot(bool)
    def setBedMeshPreviewVisible(self, visible): self._mesh.set_visible(visible)
    @pyqtSlot(str, result=QVariant)
    def macroParameterDefinitions(self, name): return QVariant(self._controls.macro_parameters(name))
