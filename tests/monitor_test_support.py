"""Monitor domain: model/QML contracts, formatting and real-Qt tuning/camera.

The Monitor owns one Qt model, deep snapshots, debounced tuning and camera
selection. Contract tests assert the source keeps those boundaries; the Qt
tests drive the real production components through the shared harness.
"""
from __future__ import annotations

import time

import ast
from dataclasses import replace
import json
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mpf.monitor.MonitorFormatting import (
    core_values,
    estimate_remaining,
    file_row_payload,
    height_readout,
    infer_macro_parameters,
    layer_readout,
    parse_bed_mesh,
    parse_mcu_stats,
    preview_block,
    preview_temperature_pair,
    print_job_caption,
)
from mpf.monitor.MonitorPermissions import Observation
from mpf.printer.PrintState import LayerResolver, PhysicalLayer
from tests.qt_runtime_support import QT_AVAILABLE, ROOT, ScriptedSocket, ScriptedTransport, runtime
from tests.source_root import SourceRoot

PLUGINS = SourceRoot(ROOT / "mpf")
MONITOR_MODEL = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
DATA = (PLUGINS / "MonitorData.py").read_text(encoding="utf-8")
CONTROLS = (PLUGINS / "MonitorControls.py").read_text(encoding="utf-8")
FORMATTING = (PLUGINS / "MonitorFormatting.py").read_text(encoding="utf-8")
POLICY = (PLUGINS / "MonitorPermissions.py").read_text(encoding="utf-8")
TYPED = "\n".join((PLUGINS / name).read_text(encoding="utf-8") for name in ("MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py", "CuraIntegration.py", "MoonrakerMonitorModel.py"))
DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")
MONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text(encoding="utf-8")
CAMERA_PANE_QML = (PLUGINS / "CameraPane.qml").read_text(encoding="utf-8")
PRINT_SECTION_QML = (PLUGINS / "PrintSection.qml").read_text(encoding="utf-8")
SETUP_SECTION_QML = (PLUGINS / "SetupSection.qml").read_text(encoding="utf-8")
TOOLHEAD_SECTION_QML = (PLUGINS / "ToolheadSection.qml").read_text(encoding="utf-8")
PROFILES_SECTION_QML = (PLUGINS / "ProfilesSection.qml").read_text(encoding="utf-8")
TUNING_SECTION_QML = (PLUGINS / "TuningSection.qml").read_text(encoding="utf-8")
FANS_SECTION_QML = (PLUGINS / "FansSection.qml").read_text(encoding="utf-8")
LEDS_SECTION_QML = (PLUGINS / "LedsSection.qml").read_text(encoding="utf-8")
PWM_SECTION_QML = (PLUGINS / "PwmSection.qml").read_text(encoding="utf-8")
POWER_SECTION_QML = (PLUGINS / "PowerSection.qml").read_text(encoding="utf-8")
SYSTEM_SECTION_QML = (PLUGINS / "SystemSection.qml").read_text(encoding="utf-8")
SAVE_SECTION_QML = (PLUGINS / "SaveSection.qml").read_text(encoding="utf-8")
FILE_MANAGER_SECTION_QML = (PLUGINS / "FileManagerSection.qml").read_text(encoding="utf-8")
MESH_SECTION_QML = (PLUGINS / "MeshSection.qml").read_text(encoding="utf-8")
TEMP_HISTORY_SECTION_QML = (PLUGINS / "TempHistorySection.qml").read_text(encoding="utf-8")
FANS_INFO_SECTION_QML = (PLUGINS / "FansInfoSection.qml").read_text(encoding="utf-8")
FILAMENT_SECTION_QML = (PLUGINS / "FilamentSection.qml").read_text(encoding="utf-8")
TEMPS_SECTION_QML = (PLUGINS / "TempsSection.qml").read_text(encoding="utf-8")
SYSTEM_INFO_SECTION_QML = (PLUGINS / "SystemInfoSection.qml").read_text(encoding="utf-8")
MCUS_SECTION_QML = (PLUGINS / "McusSection.qml").read_text(encoding="utf-8")
JOB_SECTION_QML = (PLUGINS / "JobSection.qml").read_text(encoding="utf-8")
MACROS_SECTION_QML = (PLUGINS / "MacrosSection.qml").read_text(encoding="utf-8")
PREVIEW_CONTROLS_QML = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
BED_MESH_QML = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text(encoding="utf-8")
BED_MESH_MAP_QML = (PLUGINS / "BedMeshMap.qml").read_text(encoding="utf-8")
POPOVER_QML = (PLUGINS / "MonitorPopOver.qml").read_text(encoding="utf-8")
TEMP_CHART_QML = (PLUGINS / "TemperatureChart.qml").read_text(encoding="utf-8")
CHART_COLOUR_DIALOG_QML = (PLUGINS / "MoonrakerChartColorDialog.qml").read_text(encoding="utf-8")
FILE_MANAGER_QML = (PLUGINS / "FileManager.qml").read_text(encoding="utf-8")
# The plugin root's manifest: the theme ships one of its own, so the
# bare name is ambiguous now.
QMLDIR = (PLUGINS.root / "qmldir").read_text(encoding="utf-8")
OUTPUT_PLUGIN = (PLUGINS / "MoonrakerOutputDevicePlugin.py").read_text(encoding="utf-8")
CAPTURE_HARNESS = (ROOT / "tools" / "capture_monitor.py").read_text(encoding="utf-8")

# The 23 section ids are the persistence keys (INSTRUCTIONS: the stored
# map only records touched sections; unknown keys default to expanded).
# 22 exist as sectionId: literals across the panes; the console pane's
# id is not a sectionId: property — it is pinned separately.
SECTION_IDS = {
    # Controls pane
    "print", "setup", "toolhead", "macros", "profiles", "tuning",
    "fans", "leds", "pwm", "power", "system", "save",
    # Information and Printer status panes
    "meshmap", "job", "temps", "fansinfo", "filament",
    "systeminfo", "mcus", "temphistory",
    # The Monitor's own surface
    "console", "fileManager",
}


class MonitorModelContractTests(unittest.TestCase):
    pass


class MonitorFormattingTests(unittest.TestCase):
    pass


class MonitorPolicyConsistencyTests(unittest.TestCase):
    """Behavior constants restated as QML prose must not drift."""


class EndstopAndEtaBasisTests(unittest.TestCase):
    pass


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MonitorQtTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=ScriptedSocket())):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self._stamp = 0.0
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def feed_chart(self, model, auxiliary):
        """The chart's feed path (the 4.6.0 decoupling): the fixed 1 s
        tick samples the latest aux snapshot while connected — aux
        arrivals alone never feed the history."""
        self.deliver()  # the client's connect transition
        model._data._update(auxiliary=auxiliary)
        model._on_chart_tick()
        self.qt.events()  # the publish coalescer flushes on the next turn

    def stored_transcript(self):
        """The persisted transcript's home (4.5.0): the per-machine
        state shard under the persistence folder."""
        machine_id = self.follower.current_printer_identity()[0]
        shard = self.follower.persistence.get_machine_state(machine_id) or {}
        return shard.get("consoleTranscript", [])

    def status_stamp(self):
        """A strictly increasing issue stamp for a delivered frame.

        Production stamps a poll when it is ISSUED, and the client
        drops a sync that is not strictly newer than the last applied
        one (the out-of-order-reply guard). The double delivers whole
        frames back to back, so the sequence must carry its own
        increasing stamps: a platform clock that quantises — Windows'
        GetTickCount64 ticks at ~15.6 ms — would otherwise hand two
        frames the same stamp and the second would be dropped whole as
        a stale reply.
        """
        self._stamp = max(time.monotonic(), self._stamp + 0.001)
        return self._stamp

    def deliver(self):
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": "printing", "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())

    def deliver_state(self, state):
        client = self.follower.client
        status = {
            "print_stats": {"filename": "part.gcode", "state": state, "print_duration": 30,
                            "info": {"current_layer": 2, "total_layer": 20}},
            "virtual_sdcard": {"file_size": 100, "file_position": 20},
            "gcode_move": {"gcode_position": [1, 1, 0.4, 10], "speed_factor": 1, "extrude_factor": 1,
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [1.0, 1.0, 0.4, 10.0]},
        }
        client._handle_http_status({"result": {"status": status}}, None, client._generation, self.status_stamp())

    def scripts(self):
        return [r for r in self.transport.requests if r.path == "printer/gcode/script"]


    def _with_layers(self, model, anchor, count, split=0):
        """Install a live plate payload at the coordinator's own seam:
        the model's keys publish from the snapshot the face reads."""
        coordinator = self.follower._runtime.coordinator
        coordinator._snapshot = replace(
            coordinator._snapshot,
            plate_progress={"layers": {"prev": None, "current": {"classes": {}}, "next": None},
                            "split": split, "anchor": anchor, "method": "motion index",
                            "motionTotal": 100},
            plate_layer_count=count)
        model._publish()


    def chart_of(self, model):
        chart = model.temperatureChartFull
        return chart if isinstance(chart, dict) else chart.value()

    def mini_of(self, model):
        chart = model.temperatureChartMini
        return chart if isinstance(chart, dict) else chart.value()

    def legend_of(self, model):
        legend = model.temperatureChartLegend
        return legend if isinstance(legend, dict) else legend.value()


        # No "sent to Klipper" caption (the ruling): the typed
        # line's verdict colouring carries the feedback, and nothing
        # else speaks on a successful send.


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['BED_MESH_MAP_QML', 'BED_MESH_QML', 'CAMERA_PANE_QML', 'CAPTURE_HARNESS', 'CHANGELOG', 'CHART_COLOUR_DIALOG_QML', 'CONTROLS', 'DASHBOARD_QML', 'DATA', 'EndstopAndEtaBasisTests', 'FANS_INFO_SECTION_QML', 'FANS_SECTION_QML', 'FILAMENT_SECTION_QML', 'FILE_MANAGER_QML', 'FILE_MANAGER_SECTION_QML', 'FORMATTING', 'JOB_SECTION_QML', 'LEDS_SECTION_QML', 'LayerResolver', 'MACROS_SECTION_QML', 'MCUS_SECTION_QML', 'MESH_SECTION_QML', 'MONITOR_MODEL', 'MONITOR_QML', 'MonitorFormattingTests', 'MonitorModelContractTests', 'MonitorPolicyConsistencyTests', 'MonitorQtTests', 'OUTPUT_PLUGIN', 'Observation', 'PLUGINS', 'POLICY', 'POPOVER_QML', 'POWER_SECTION_QML', 'PREVIEW_CONTROLS_QML', 'PRINT_SECTION_QML', 'PROFILES_SECTION_QML', 'PWM_SECTION_QML', 'PhysicalLayer', 'QMLDIR', 'QT_AVAILABLE', 'ROOT', 'SAVE_SECTION_QML', 'SECTION_IDS', 'SETUP_SECTION_QML', 'SYSTEM_INFO_SECTION_QML', 'SYSTEM_SECTION_QML', 'ScriptedSocket', 'ScriptedTransport', 'SimpleNamespace', 'TEMPS_SECTION_QML', 'TEMP_CHART_QML', 'TEMP_HISTORY_SECTION_QML', 'TOOLHEAD_SECTION_QML', 'TUNING_SECTION_QML', 'TYPED', 'annotations', 'ast', 'core_values', 'estimate_remaining', 'file_row_payload', 'height_readout', 'infer_macro_parameters', 'json', 'layer_readout', 'parse_bed_mesh', 'parse_mcu_stats', 'patch', 'preview_block', 'preview_temperature_pair', 'print_job_caption', 're', 'replace', 'runtime', 'time', 'unittest']
