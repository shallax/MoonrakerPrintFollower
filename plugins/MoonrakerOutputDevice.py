"""Cura output-device adapter. Upload policy and file preparation are composed."""
from dataclasses import replace
import os

from PyQt6.QtCore import QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from cura.PrinterOutput.PrinterOutputController import PrinterOutputController
from cura.PrinterOutput.PrinterOutputDevice import ConnectionType, PrinterOutputDevice
from UM.Logger import Logger
from UM.Message import Message
from UM.OutputDevice import OutputDeviceError

from .CuraOutputWriter import CuraOutputWriter
from .UploadController import UploadController


class MoonrakerOutputController(PrinterOutputController):
    def __init__(self, output_device):
        super().__init__(output_device)
        self.can_pause = self.can_abort = False
        self.can_pre_heat_bed = self.can_pre_heat_hotends = False
        self.can_send_raw_gcode = self.can_control_manually = False
        self.can_update_firmware = False


class MoonrakerOutputDevice(PrinterOutputDevice):
    DEVICE_PREFIX = "MoonrakerPrintFollower@"
    uploadPathsChanged = pyqtSignal()

    def __init__(self, application, machine_id, *, client, config, apply_config, active_identity):
        super().__init__(device_id=self.DEVICE_PREFIX + machine_id, connection_type=ConnectionType.NetworkConnection)
        self._application, self._config, self._apply_config = application, config, apply_config
        self._writer = CuraOutputWriter(application)
        self._upload = UploadController(client, machine_id, active_identity, self)
        self._dialog = self._message = None
        self._monitor_view_qml_path = None
        self._upload.changed.connect(self.uploadPathsChanged.emit)
        self._upload.dialogClosed.connect(self._release_dialog)
        self._upload.choicesAccepted.connect(self._remember)
        self._upload.status.connect(self._status)
        self._upload.progress.connect(self._progress)
        self._upload.finished.connect(self._finished)
        stack = application.getGlobalContainerStack()
        try: extruders = int(stack.getProperty("machine_extruder_count", "value"))
        except Exception: extruders = 1
        self._printers = [PrinterOutputModel(MoonrakerOutputController(self), extruders)]
        self.updateConfig(active_identity)

    def updateConfig(self, identity):
        """Set the device's display name from its binding identity.

        The name comes from the machine this device was created for —
        never the application's current global stack, which a machine
        switch or a reordered refresh could change independently.
        """
        try:
            _machine_id, name = identity()
        except Exception:
            name = "Printer"
        self.setName(name)
        self.setDescription("Upload to " + name)
        self.setShortDescription("Upload to " + name)
        self.setIconName("print")
        self.setConnectionText("Connected via Moonraker")
        self.setPriority(5)

    @pyqtProperty(str, notify=uploadPathsChanged)
    def initialUploadPath(self): return self._upload.path or "<root>"

    def setMonitorViewQmlPath(self, path: str) -> None:
        """Explicit capability the output plugin supplies for the Monitor panel."""
        self._monitor_view_qml_path = str(path or "")
    @pyqtProperty(str, notify=uploadPathsChanged)
    def initialUploadFilename(self): return self._upload.filename
    @pyqtProperty(bool, notify=uploadPathsChanged)
    def initialStartPrint(self): return bool(self._upload.start_print)
    @pyqtProperty(QVariant, notify=uploadPathsChanged)
    def uploadPathOptions(self): return QVariant(self._upload.paths)

    def requestWrite(self, node, fileName=None, *args, **kwargs):
        if self._upload.busy: raise OutputDeviceError.DeviceBusyError()
        config = self._config()
        try:
            self._upload.begin(config, fileName or "print")
        except (ValueError, RuntimeError) as error:
            self._error(str(error))
            return
        self.writeStarted.emit(self)
        try:
            self._upload.prepared(self._writer.prepare(config, fileName))
            if config.upload_dialog:
                try:
                    path = os.path.join(os.path.dirname(__file__), "MoonrakerUploadDialog.qml")
                    self._dialog = self._application.createQmlComponent(path, {"manager": self})
                    self._dialog.show()
                    self._upload.discover()
                except Exception:
                    self._release_dialog()
                    self._upload.start()
            else:
                self._upload.start()
        except Exception as error:
            self._upload.fail("Could not prepare the Moonraker upload: " + str(error))

    @pyqtSlot(str, str, bool)
    def acceptUpload(self, path, filename, start_print): self._upload.accept(path, filename, start_print)
    @pyqtSlot()
    def cancelUpload(self): self._upload.cancel()
    def deactivate(self): self._upload.abort()

    def _remember(self, path, start_print):
        try:
            config = self._config()
            paths = list(config.upload_paths)
            if path and path not in paths: paths.append(path)
            updated = replace(config, upload_paths=paths)
            if config.upload_remember_state: updated = replace(updated, upload_path=path, upload_start_print=start_print)
            self._apply_config(updated)
        except Exception as error:
            # Remembering convenience choices must never block the actual upload.
            Logger.log("w", "Moonraker Print Follower: could not remember upload choices: %s", error)

    def _release_dialog(self):
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            try: dialog.deleteLater()
            except RuntimeError: pass

    def _status(self, text):
        if self._message is None:
            self._message = Message(text, 0, False)
            self._message.setTitle("Moonraker")
            self._message.show()
        else: self._message.setText(text)

    def _progress(self, percent):
        if self._message is not None: self._message.setProgress(percent)
        self.writeProgress.emit(self, percent)

    def _error(self, text):
        message = Message(text, 0, False)
        message.setTitle("Moonraker - Error")
        message.show()

    def _finished(self, success, error):
        try:
            if self._message is not None:
                self._message.hide()
                self._message = None
            if success:
                config = self._upload.config
                suffix = " and started the print" if self._upload.start_print else ""
                self._message = Message(f"Uploaded '{self._upload.filename}' to {self.getName()}{suffix}.", 30 if config.upload_autohide_message else 0, True)
                self._message.setTitle("Moonraker")
                self._message.addAction("open_browser", "Open Browser", "globe", "Open the configured Moonraker frontend")
                self._message.actionTriggered.connect(lambda message, action:
                    QDesktopServices.openUrl(QUrl(config.frontend_target)) if action == "open_browser" else None)
                self._message.show()
                self.writeSuccess.emit(self)
            elif error:
                self._error(error)
                self.writeError.emit(self)
        finally:
            self._upload.terminal_delivered()
            self.writeFinished.emit(self)
