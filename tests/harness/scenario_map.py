"""The surface→scenario map (coverage.py's check consumes this).

Values are the suite spec ids in scenarios.py (``a1``..``z14``).
``PREFIX_RULES`` cover whole families with one rule; ``EXCLUSIONS``
are justified surfaces the unit/Qt suites already own or the probe
evidence defers (the design allows an explicit, justified exclusion
and nothing else) — each entry carries reason/evidence/date/recheck
under the workstream-4 schema.
"""
from __future__ import annotations

SCENARIO_MAP = {
    # The QML-facing verbs on the settings machine action (the settings group).
    "MoonrakerFollowerMachineAction.cancelTest": "i2",
    "MoonrakerFollowerMachineAction.clearCache": "i6",
    "MoonrakerFollowerMachineAction.insecureKeyWarning": "i2",
    "MoonrakerFollowerMachineAction.saveConfig": "i6",
    "MoonrakerFollowerMachineAction.testConnection": "i2",
    "MoonrakerFollowerMachineAction.validAuxInterval": "i3",
    "MoonrakerFollowerMachineAction.validConsoleInterval": "i3",
    "MoonrakerFollowerMachineAction.validPollInterval": "i3",
    "MoonrakerFollowerMachineAction.validRetryInterval": "i3",
    "MoonrakerFollowerMachineAction.validTranslation": "i4",
    "MoonrakerFollowerMachineAction.validUrl": "i1",
    "MoonrakerFollowerMachineAction.validZTolerance": "i4",
    # The model's user-intent verbs, per group.
    "MoonrakerMonitorModel.adjustZOffset": "g4",
    "MoonrakerMonitorModel.applyTemperaturePreset": "c1",
    "MoonrakerMonitorModel.calibrateBedMesh": "g2",
    "MoonrakerMonitorModel.cancelPrint": "g6",
    "MoonrakerMonitorModel.centerToolhead": "g2",
    "MoonrakerMonitorModel.clearFileFilters": "f1",
    "MoonrakerMonitorModel.clearFileSelection": "f1",
    "MoonrakerMonitorModel.clearBedMesh": "g2",
    "MoonrakerMonitorModel.clearConsoleHistory": "d3",
    "MoonrakerMonitorModel.clearZOffset": "g4",
    "MoonrakerMonitorModel.emergencyHoldReleased": "s7",
    "MoonrakerMonitorModel.emergencyHoldStarted": "s7",
    "MoonrakerMonitorModel.emergencyStopClick": "s7",
    "MoonrakerMonitorModel.excludeObject": "b11",
    "MoonrakerMonitorModel.restoreObject": "b11",
    "MoonrakerMonitorModel.excludeCurrent": "b11",
    "MoonrakerMonitorModel.extrude": "g4",
    "MoonrakerMonitorModel.firmwareRestart": "g9b",
    "MoonrakerMonitorModel.heatersOff": "g9b",
    "MoonrakerMonitorModel.home": "g2",
    "MoonrakerMonitorModel.homeAll": "g2",
    "MoonrakerMonitorModel.hostRestart": "g9b",
    "MoonrakerMonitorModel.improveEta": "h8",
    "MoonrakerMonitorModel.jog": "g1",
    "MoonrakerMonitorModel.klipperRestart": "g9b",
    "MoonrakerMonitorModel.loadBedMeshProfile": "g2",
    "MoonrakerMonitorModel.macroParameterDefinitions": "g5",
    "MoonrakerMonitorModel.motorsOff": "g2",
    "MoonrakerMonitorModel.openFileManager": "f1",
    "MoonrakerMonitorModel.openFrontend": "i7",
    "MoonrakerMonitorModel.pausePrint": "g6",
    "MoonrakerMonitorModel.stripPausePrint": "v19",
    "MoonrakerMonitorModel.previewFanSpeed": "c2",
    "MoonrakerMonitorModel.previewFlowFactor": "c2",
    "MoonrakerMonitorModel.previewLedBrightness": "c3",
    "MoonrakerMonitorModel.previewLedColor": "c3",
    "MoonrakerMonitorModel.previewPwmOutput": "c3",
    "MoonrakerMonitorModel.previewSpeedFactor": "c2",
    "MoonrakerMonitorModel.reconnect": "a8",
    "MoonrakerMonitorModel.refreshAll": "a7",
    "MoonrakerMonitorModel.refreshFileManager": "f1",
    "MoonrakerMonitorModel.refreshWebcams": "e1",
    "MoonrakerMonitorModel.resumePrint": "g6",
    "MoonrakerMonitorModel.runMacro": "g5",
    "MoonrakerMonitorModel.runQuadGantryLevel": "g2",
    "MoonrakerMonitorModel.saveConfig": "i6",
    "MoonrakerMonitorModel.selectWebcam": "e2",
    "MoonrakerMonitorModel.sendConsoleCommand": "d1",
    "MoonrakerMonitorModel.setBedMeshPreviewVisible": "h6",
    "MoonrakerMonitorModel.setBedMeshThresholds": "h6c",
    "MoonrakerMonitorModel.setConsoleExpanded": "d5",
    "MoonrakerMonitorModel.setConsoleHeight": "d5",
    "MoonrakerMonitorModel.setControlsCollapsed": "g8",
    "MoonrakerMonitorModel.setControlsLocked": "g8",
    "MoonrakerMonitorModel.setExtrudeDistance": "g4",
    "MoonrakerMonitorModel.setExtrudeSpeed": "g4",
    "MoonrakerMonitorModel.setFanSpeed": "c2",
    "MoonrakerMonitorModel.setFlowFactor": "c2",
    "MoonrakerMonitorModel.setInfoCollapsed": "d5",
    "MoonrakerMonitorModel.setJogDistance": "g1",
    "MoonrakerMonitorModel.setLedBrightness": "c3",
    "MoonrakerMonitorModel.setLedColor": "c3",
    "MoonrakerMonitorModel.setPositionMode": "g3",
    "MoonrakerMonitorModel.setPowerDevice": "g9",
    "MoonrakerMonitorModel.setPwmOutput": "c3",
    "MoonrakerMonitorModel.setSectionExpanded": "d5",
    "MoonrakerMonitorModel.setSectionLayout": "x3",
    "MoonrakerMonitorModel.sectionLayoutFor": "x1",
    "MoonrakerMonitorModel.setShowProbePoints": "h6b",
    "MoonrakerMonitorModel.setShowTemperaturePower": "c3",
    "MoonrakerMonitorModel.setShowTemperatureTargets": "c3",
    "MoonrakerMonitorModel.setSpeedFactor": "c2",
    "MoonrakerMonitorModel.setStatusCollapsed": "d5",
    "MoonrakerMonitorModel.setTemperatureSensorColor": "c3",
    "MoonrakerMonitorModel.setTemperatureSensorVisible": "c3",
    "MoonrakerMonitorModel.updateMoonrakerStatus": "b10",
    # The what's-new overlay's lifecycle verbs (the z16 probe).
    "MoonrakerMonitorModel.checkWhatsNew": "z16",
    "MoonrakerMonitorModel.showWhatsNew": "z16",
    "MoonrakerMonitorModel.dismissWhatsNew": "z16",
    "MoonrakerMonitorModel.zToZero": "g2",
    "MoonrakerOutputDevice.acceptUpload": "f2",
    "MoonrakerOutputDevice.cancelUpload": "f2",
    "MoonrakerOutputDevice.leaveMonitorStage": "a9",
    "MoonrakerPrintFollower.confirmForceLoadCurrentPrint": "h2",
    "MoonrakerPrintFollower.toggleFollowingPause": "h3",
    # The interactive items by objectName.
    "moonrakerTemperatureDetail": "v6",
    "moonrakerPlateCanvas": "b11",
    "moonrakerPlateExcludeFace": "b11",
    "moonrakerPlateProgressFace": "b11",
    "moonrakerPlateToolheadDot": "b11",
    "excludeCurrentButton": "b11",
    "sectionConfigurePopOver": "x1",
    "sectionConfigureRowTitle": "x1",
    "configureControlsSectionsButton": "x1",
    "configureStatusSectionsButton": "x2",
    "configureInfoSectionsButton": "x4",
    "infoCollapsedReadoutText": "x4",
    "statusCollapsedReadoutLabel": "x4",
    "statusCollapsedFlowLabel": "x8",
    "controlsCollapsedZOffsetLabel": "x8",
    "controlsCollapsedReadoutText": "x4",
    "visibilitySelectorBox": "x2",
    "columnsPopupBackground": "x5",
    "sectionConfigureHandle": "x6",
    "moonrakerStripBed": "v19",
    "moonrakerStripFinish": "v19",
    "resetToDefaultsLabel": "x2",
    "moonrakerControlsPane": "v9",
    "moonrakerStatusStateText": "v9", "moonrakerConsoleOutput": "v9",
    "moonrakerFileRowName": "v10", "moonrakerFileSearch": "v11",
    "moonrakerBedMeshMap": "v12",
    "moonrakerBedMeshRangeSlider": "h6c",
    "moonrakerJogXPlus": "g1", "moonrakerJogXMinus": "g1",
    "moonrakerJogYPlus": "g1", "moonrakerJogYMinus": "g1",
    "moonrakerJogZPlus": "g1", "moonrakerJogZMinus": "g1",
    # The policy-fed surfaces (4.2.0): the caption's rendered state
    # and the restart buttons (v18 presses the firmware one).
    "toolheadStatusCaption": "g6",
    "moonrakerFirmwareRestart": "v18", "moonrakerHostRestart": "v18",
    "moonrakerKlipperRestart": "v18",
    "moonrakerHomeX": "g2", "moonrakerHomeY": "g2", "moonrakerHomeZ": "g2",
    "moonrakerConsoleInput": "d1", "moonrakerConsoleSend": "d1",
    # The Clear button gained its own address (the hit-region fix): d3
    # presses it by name instead of by its rendered text.
    "moonrakerConsoleClear": "d3",
    "moonrakerTuningSpeedReset": "c2", "moonrakerTuningFlowReset": "c2",
    "moonrakerM117Slot": "s6",
    "moonrakerPreviewCard": "v1",
    "moonrakerStripPauseButton": "v19",
    "moonrakerLayerReadout": "v19", "moonrakerHeightReadout": "v19",
    "moonrakerStripTemps": "v19", "moonrakerStripSlot": "v19",
    # The strip's next-pause fill renders once the improve-ETA flow
    # lands the index and the snapshot carries the pause fraction —
    # x10 observes it (the strip's own pause surfaces are v19). The
    # job section's stacked-track fill keeps the bare nextPauseFill.
    "statusNextPauseFill": "x10",
    # The fans section's harness address (the s8 track-click scenario
    # scrolls it into view before the press).
    "moonrakerFansSection": "s8",
    "moonrakerPreviewCardPanel": "z9",
    "moonrakerPreviewCardOverlay": "z9",
    "moonrakerPreviewCardOverlayHost": "z9",
    "infoPanel": "v4", "statusPanel": "v4",
    "loadIndicatorContent": "h2",
    # The what's-new overlay's controls: the Close button, the repo
    # link, and the per-version section headers (the extractor reads
    # the dynamic objectName's static prefix).
    "whatsNewCloseButton": "z16", "whatsNewRepoLink": "z16",
    "whatsNewSection_": "z16",
    # The dialog verbs (the round-2 H3 naming pass): the CONFIRM
    # verbs are really pressed by their scenarios; the cancel verbs
    # and the chrome ride the deferred popup round (excluded below).
    "printConfirmStartButton": "f6",
    "deleteConfirmDeleteButton": "f3",
    "renameConfirmButton": "f7",
    # The protocol endpoints: the simulator's contract test owns the
    # wire shapes; the scenarios drive them through the real UI.
    "status_endpoint": "b1", "websocket_endpoint": "a1",
    "metadata_endpoint": "f1", "download_endpoint": "h2",
    "print_start_endpoint": "f6", "delete_endpoint": "f3",
    "directory_delete_endpoint": "f4", "directory_create_endpoint": "f4",
    "move_endpoint": "f5", "server_info_endpoint": "b1",
    "objects_list_endpoint": "a1", "gcode_script_endpoint": "d1",
}

PREFIX_RULES = [
    # The policy projections (4.2.0) ride the motion scenarios that
    # exercise the gates: the caption pair and the restart pair.
    ("key", "jogReason", "g6"),
    ("key", "canRestart", "g6"),
    ("key", "restartReason", "g6"),
    ("key", "sectionReason", "g8"),
    # The file manager's verbs all ride the files-group scenarios.
    ("slot", "MoonrakerMonitorModel.file", "f1"),
    ("slot", "MoonrakerMonitorModel.toggleFile", "f1"),
    ("slot", "MoonrakerMonitorModel.setFile", "f1"),
    # The camera slots ride the camera-group scenario.
    ("slot", "MoonrakerMonitorModel.cameraRenderStalled", "e2"),
    # The published keys by family.
    ("key", "console", "d1"),
    ("key", "camera", "e2"),
    ("key", "temperature", "c1"),
    ("key", "fan", "c2"),
    ("key", "fileManager", "f1"),
    ("key", "fileUpload", "f2"),
    ("key", "filePrint", "f6"),
    ("key", "fileDelete", "f3"),
    ("key", "fileRename", "f5"),
    ("key", "bedMesh", "h6"),
    ("key", "jog", "g1"),
    ("key", "extrude", "g4"),
    ("key", "monitor", "b2"),
    ("key", "filament", "b9"),
    ("key", "endstop", "c5"),
    ("key", "mcu", "c4"),
    ("key", "macro", "g5"),
    ("key", "powerDevices", "g9"),
    ("key", "emergency", "s7"),
    ("key", "controls", "g8"),
    ("key", "infoCollapsed", "d5"),
    ("key", "statusCollapsed", "d5"),
    ("key", "sectionExpandedMap", "d5"),
    ("key", "sectionLayout", "x3"),
    ("key", "sectionHiddenMap", "x2"),
    ("key", "monitorPositionCompact", "x4"),
    ("key", "improvingEta", "h8"),
    ("key", "improveEta", "h8"),
    ("key", "showProbePoints", "h6b"),
    ("key", "saveConfig", "i6"),
    ("key", "canSaveConfig", "i6"),
    ("key", "webcamNames", "e2"),
    ("key", "activeWebcamIndex", "e2"),
    ("key", "speedFactorPercent", "c2"),
    ("key", "flowFactorPercent", "c2"),
    ("key", "ledItems", "c3"),
    ("key", "pwmOutputItems", "c3"),
    ("key", "fanControlItems", "c2"),
    ("key", "fanItems", "c2"),
    ("key", "filamentSensorItems", "c4"),
    ("key", "excludeObjectItems", "b11"),
    ("key", "currentObjectName", "b11"),
    ("key", "plateObjects", "b11"),
    ("key", "plateDot", "b11"),
    ("key", "plateProgress", "b11"),
    ("key", "plateHasObjects", "b11"),
    ("key", "zOffset", "g4"),
    ("key", "homedAxes", "b8"),
    ("key", "positionMode", "g3"),
    ("key", "klippyState", "b1"),
    ("key", "klipperVersion", "b1"),
    ("key", "moonrakerVersion", "b1"),
    ("key", "cpuTemperature", "c4"),
    ("key", "hostLoad", "c4"),
    ("key", "memoryAvailable", "c4"),
    ("key", "hasBedMesh", "h6"),
    ("key", "hasQuadGantryLevel", "g2"),
    ("key", "printActive", "b1"),
    ("key", "printJobCaption", "g6"),
    ("key", "canPausePrint", "g6"),
    ("key", "canResumePrint", "g6"),
    ("key", "pauseReason", "g6"),
    ("key", "pauseReasonDetail", "g6"),
    # The next scheduled pause's published keys ride the improve-ETA
    # flow: h8's index build leaves the snapshot (and so these keys)
    # carrying the pause ahead.
    ("key", "nextPauseEta", "h8"),
    ("key", "nextPauseFraction", "h8"),
    ("key", "nextPauseLayer", "h8"),
    ("key", "nextPauseBaked", "h8"),
    ("key", "resumeReason", "g6"),
    ("key", "resumeReasonDetail", "g6"),
    ("key", "canCancelPrint", "g6"),
    ("key", "actionBusy", "b10"),
    ("key", "actionStatus", "b10"),
    ("key", "connectionDetail", "a9"),
    ("key", "canApplyTemperaturePreset", "c1"),
    ("key", "temperaturePresetItems", "c1"),
    ("key", "temperaturePresetNames", "c1"),
    ("key", "canRunSetup", "i2"),
]

# The exclusion schema (the panel's workstream-4 ruling): an
# exclusion is a NAMED surface with its reason, the evidence that
# justified it, the date it was taken, and the condition that
# re-opens it. `check()` treats the names as covered; the schema
# fields are validated by test_coverage.py. An entry whose re-check
# trigger fires must be re-probed, not carried forward silently.
EXCLUSIONS = {
    # The T0-T9 cold-camera timing chain (the reviewer's diagnostics):
    # the QML-invoked first-frame slot and the trace-gate key are
    # instrumentation, never scenario verbs.
    "MoonrakerMonitorModel.cameraFirstFrameRendered": {
        "reason": "the timing chain's T9 hook, invoked by the pane on the first decoded frame",
        "evidence": "test_camera_timing's mark/mark_once contract; the harness legs' trace logs",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "traceCameraTiming": {
        "reason": "the trace gate mirrored to QML; diagnostics-only",
        "evidence": "test_camera_timing's begin/enabled contract",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "MoonrakerMonitorModel.cameraPaneInstanceId": {
        "reason": "the pane's diagnostic id source; instrumentation, never a scenario verb",
        "evidence": "the camera ownership tests' pane-id assignments; the trace logs",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "MoonrakerMonitorModel.cameraPaneTrace": {
        "reason": "the pane's trace sink for apply/start/stop lines; diagnostics-only",
        "evidence": "the camera ownership tests' apply traces; the trace logs",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "MoonrakerMJPGImage.start": {
        "reason": "the renderer's stream start, driven by the pane's applyCamera; the harness scenarios exercise the pane, never the raw verb",
        "evidence": "the camera ownership tests' start/stop counter assertions",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "MoonrakerMJPGImage.stop": {
        "reason": "the renderer's stream stop, driven by the pane's applyCamera; the harness scenarios exercise the pane, never the raw verb",
        "evidence": "the camera ownership tests' start/stop counter assertions",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "cameraImage": {
        "reason": "the stream viewport item, named for the ownership tests' counter reads; never a scenario target",
        "evidence": "the camera ownership tests' start/stop counter assertions",
        "date": "2026-09-19",
        "recheck": "the cold-camera timing scenario lands",
    },
    "MoonrakerMonitorModel.setChartOpen": {
        "reason": "the chart pop-over's hydration gate; driven by the pane's own openPopOver state, never a scenario verb",
        "evidence": "the chart hydration tests' open/close transitions",
        "date": "2026-09-19",
        "recheck": "the chart pop-over scenario lands",
    },
    # The migration-failure surfaces: the settings-dialog scenario is
    # deferred (no harness scenario drives the dialog yet), so the
    # banner's end-to-end proof rides the unit tests — the notice's
    # state machine and the model's record values — until it lands.
    "MoonrakerMonitorModel.dismissMigrationBanner": {
        "reason": "the dialog's Dismiss verb; the dialog scenario is deferred",
        "evidence": "test_migration_notice's latch tests; the model's record values in test_monitor",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "MoonrakerMonitorModel.openMigrationBackupFolder": {
        "reason": "the backup folder open; the dialog scenario is deferred",
        "evidence": "the QDesktopServices recipe matches Cura's own CrashHandler",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    # The settings page reads its migration surface off the ACTION (the
    # live find: the page's bindings pointed at the wrong manager), so
    # the action's two verbs join the model's in the deferred set.
    "MoonrakerFollowerMachineAction.dismissMigrationBanner": {
        "reason": "the settings page's Dismiss verb; the dialog scenario is deferred",
        "evidence": "test_config_formatting_coverage's SettingsPageMigrationMirrorTests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "MoonrakerFollowerMachineAction.openMigrationBackupFolder": {
        "reason": "the settings page's backup folder open; the dialog scenario is deferred",
        "evidence": "SettingsPageMigrationMirrorTests pins the config-path handover",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationNotice": {
        "reason": "the failure banner; the dialog scenario is deferred",
        "evidence": "test_migration_notice; the model's record values in test_monitor",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationDismissButton": {
        "reason": "the banner's only dismiss path; the dialog scenario is deferred",
        "evidence": "test_migration_notice's latch tests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationShowBackupButton": {
        "reason": "the backup folder action; the dialog scenario is deferred",
        "evidence": "test_migration_notice; the QDesktopServices recipe",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationDiagnosticsRow": {
        "reason": "the permanent post-dismissal row; the dialog scenario is deferred",
        "evidence": "the model's migrationDiagnosticsVisible/Text values in test_monitor",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationBannerVisible": {
        "reason": "the banner's visibility key; the dialog scenario is deferred",
        "evidence": "the model's record-value unit tests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationBannerText": {
        "reason": "the banner's copy key; the dialog scenario is deferred",
        "evidence": "the model's record-value unit tests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationBackupAvailable": {
        "reason": "the backup-action's gate key; the dialog scenario is deferred",
        "evidence": "the model's record-value unit tests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationDiagnosticsVisible": {
        "reason": "the diagnostics row's visibility key; the dialog scenario is deferred",
        "evidence": "the model's record-value unit tests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "migrationDiagnosticsText": {
        "reason": "the diagnostics row's copy key; the dialog scenario is deferred",
        "evidence": "the model's record-value unit tests",
        "date": "2026-09-18",
        "recheck": "the settings-dialog scenario lands",
    },
    "jobPositionCellX": {
        "reason": "the Position row's axis cell (the 4.5.0 colour ruling); the colour mapping is a QML pin, not a scenario surface",
        "evidence": "test_monitor pins the cells' axis tokens and no-wrap; the Toolhead precedent",
        "date": "2026-09-18",
        "recheck": "a scenario asserts the Status pane's axis colours",
    },
    "moonrakerInfoContent": {
        "reason": "the information pane's container, addressed by the real-engine tests; the section ORDER is asserted by the configure scenarios through the headers, not by this name",
        "evidence": "test_qml_real_engine's SectionOrderArrivalTests address it; the s-scenarios pin the rendered order end-to-end",
        "date": "2026-09-18",
        "recheck": "the configure scenarios adopt the objectName directly",
    },
    "jobPositionCellY": {
        "reason": "the Position row's axis cell (the 4.5.0 colour ruling); the colour mapping is a QML pin, not a scenario surface",
        "evidence": "test_monitor pins the cells' axis tokens and no-wrap; the Toolhead precedent",
        "date": "2026-09-18",
        "recheck": "a scenario asserts the Status pane's axis colours",
    },
    "jobPositionCellZ": {
        "reason": "the Position row's axis cell (the 4.5.0 colour ruling); the colour mapping is a QML pin, not a scenario surface",
        "evidence": "test_monitor pins the cells' axis tokens and no-wrap; the Toolhead precedent",
        "date": "2026-09-18",
        "recheck": "a scenario asserts the Status pane's axis colours",
    },
    # The pause list's stable ListModel: a probe-only seam (the
    # 2026-09-16 layer-0 diagnosis reads the synced rows back through
    # it); the pause-row scenario that supersedes this lands with the
    # baked-pause harness fixture.
    "moonrakerPauseListModel": {
        "reason": "probe seam for the sync diagnosis; no scenario reads the model object itself",
        "evidence": "the container repro reads the synced rows end to end",
        "date": "2026-09-16",
        "recheck": "the baked-pause pause-row scenario lands",
    },
    # The status column's two geometry address points (the status-width
    # fix): the flickable and the column it holds are measured, never
    # pressed — a scenario would only be reading their rects.
    "moonrakerStatusFlick": {
        "reason": "geometry address point of the status pane; no scenario presses it",
        "evidence": "test_qml_real_engine's StatusColumnGeometryTests measures the column against it",
        "date": "2026-09-18",
        "recheck": "a scenario scrolls or presses inside the status pane",
    },
    "moonrakerStatusContent": {
        "reason": "geometry address point of the status column; no scenario presses it",
        "evidence": "test_qml_real_engine's StatusColumnGeometryTests measures the sections against it",
        "date": "2026-09-18",
        "recheck": "a scenario scrolls or presses inside the status pane",
    },
    # The controls pane's two geometry address points (the constant
    # gutter fix): the flickable and the column it holds are measured,
    # never pressed — a scenario would only be reading their rects.
    "moonrakerControlsFlick": {
        "reason": "geometry address point of the controls pane; no scenario presses it",
        "evidence": "test_qml_real_engine's PaneGutterTests measures the column against it",
        "date": "2026-09-19",
        "recheck": "a scenario scrolls or presses inside the controls pane",
    },
    "moonrakerControlsContent": {
        "reason": "geometry address point of the controls column; no scenario presses it",
        "evidence": "test_qml_real_engine's PaneGutterTests measures the gutter against it",
        "date": "2026-09-19",
        "recheck": "a scenario scrolls or presses inside the controls pane",
    },
    # The job section's stacked-track pause fill: the strip's own
    # fill took the mapped statusNextPauseFill name (the shared
    # objectName made the capture's findChildren ambiguous — the
    # panel's catch); the capture tool addresses this one directly.
    "nextPauseFill": {
        "reason": "the job section's stacked-track fill is the capture tool's styling proof, not a scenario surface",
        "evidence": "capture_monitor.py samples the track's print fill through it on every make all",
        "date": "2026-09-18",
        "recheck": "a scenario asserts the job section's stacked track directly",
    },
    # The drag GESTURE stays excluded: the synthetic drag cannot drive
    # a QML MouseArea grab under Xvfb (the console-resize precedent).
    # Retired: x6 drives a real press/move/release on the handle via
    # QTest (the s8 track-click precedent) and gates the rendered
    # reorder the plain release commits.
    # British-spelling formatting is a pure function of the locale —
    # unit-tested in test_monitor, invisible to scenarios.
    "britishSpelling": {
        "reason": "locale formatting, a pure function",
        "evidence": "unit-tested in test_monitor.py",
        "date": "2026-09-15",
        "recheck": "the formatting moves out of a pure function",
    },
    # The console's resize GESTURE: no synthetic drag drives a QML
    # MouseArea's grab under Xvfb (QTest moves carry no button state,
    # injected moves never register as position changes). The commit
    # handler's model call is covered by d5 via setConsoleHeight; the
    # gesture itself stays unit/QML-covered.
    "consoleResizeHandle": {
        "reason": "the synthetic drag cannot drive a QML MouseArea grab under Xvfb",
        "evidence": "d5 covers the commit via setConsoleHeight; the phase-0 drag probe",
        "date": "2026-09-15",
        "recheck": "a drag primitive that carries button state lands",
    },
    "consoleResizeArea": {
        "reason": "the synthetic drag cannot drive a QML MouseArea grab under Xvfb",
        "evidence": "d5 covers the commit via setConsoleHeight; the phase-0 drag probe",
        "date": "2026-09-15",
        "recheck": "a drag primitive that carries button state lands",
    },
    # The lock button's position inside the Loader-built dashboard is
    # unreachable by the harness's item walk on every boot; g8 covers
    # the lock's model path via setControlsLocked in both directions.
    "moonrakerLockButton": {
        "reason": "unreachable in the Loader-built dashboard",
        "evidence": "g8 covers the lock via setControlsLocked; the visual group's walk dumps",
        "date": "2026-09-15",
        "recheck": "the dashboard's construction changes",
    },
    # The create-folder dialog: no scenario opens it yet — its
    # scenario lands with the deferred panel round (2026-09-15),
    # which converts this exclusion into map entries.
    "createFolderCreateButton": {
        "reason": "no scenario opens the create-folder dialog yet",
        "evidence": "the deferred panel round (recorded in ROADMAP 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred panel scenarios land",
    },
    "createFolderCancelButton": {
        "reason": "no scenario opens the create-folder dialog yet",
        "evidence": "the deferred panel round (recorded in ROADMAP 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred panel scenarios land",
    },
    # The settings dialog: the machine-action manager exposes no
    # public show/activate API (the z12 probe's dump), and the
    # dialog's items are absent from the main-window walk when
    # unopened. The i-group carries the action's handlers over the
    # real transport; the dialog itself is a named exclusion.
    "settingsDialog": {
        "reason": "no public API opens the machine-settings dialog; its items are outside the walk",
        "evidence": "the z12 probe (empty actions, absent walk items) — DECISIONS 4.1.0",
        "date": "2026-09-15",
        "recheck": "a public API to open the dialog is found, or Cura's stage walk changes",
    },
    # The Esc ladder: a window-level Shortcut stays gated while the
    # FM popup is open, and key_press targets the main window only —
    # an Esc scenario would need popup-window key routing first.
    "escLadder": {
        "reason": "key_press addresses the main window; the popup's window-level Esc routing is unbuilt",
        "evidence": "f1's unverified Esc (the slot-close replacement) — DECISIONS 4.1.0",
        "date": "2026-09-15",
        "recheck": "key_press gains popup-window addressing",
    },
    # The file rows' context-menu MouseArea is a SIBLING of the name
    # label — a real press aimed at the row grabs the row's
    # background rectangle, never the menu trigger. The requests stay
    # declared slots; the menu's actions are the dialog scenarios.
    "rowContextMenu": {
        "reason": "the context-menu MouseArea is a sibling of the name label — not addressable",
        "evidence": "the f3/f6 probe dumps (the menu trigger never grabbed)",
        "date": "2026-09-15",
        "recheck": "the row layout moves the MouseArea onto the label",
    },
    # The FM popup renders in its own QQuickWindow; the named verbs
    # inside it land (the f-group presses), but the popup's chrome
    # (its own close/decoration) has no objectNames yet.
    "popupChrome": {
        "reason": "the popup window's own chrome carries no objectNames",
        "evidence": "f1 closes via the model slot; the named-verb presses in f3/f5/f6/f7",
        "date": "2026-09-15",
        "recheck": "the popup chrome gains objectNames",
    },
    "printConfirmDialog": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "printConfirmCancelButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "slicerPopup": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "modifiedPopup": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "fileManagerCloseButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "deleteConfirmCancelButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "renameConfirmCancelButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "uploadConfirmOverwriteButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "uploadConfirmCancelButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "uploadProgressCloseButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "printTimePopup": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "columnsPopup": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "pageSizePopup": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "gridHeader": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "gridVertical": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "columnResizeHandle": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "moonrakerPreviewCardPanelHost": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "cameraViewport": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "cameraControls": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    "cameraStreamChipText": {
        "reason": "the stream chip's label: display-only text over the live camera feed (the capture census exempts camera-overlay text by name)",
        "evidence": "the chip visibility contract test; the census exemption in capture_contrast.CAMERA_OVERLAY_TEXT",
        "date": "2026-09-19",
        "recheck": "the chip's text gains an interactive surface",
    },
    "cameraLiveBadgeText": {
        "reason": "the Live badge's label: display-only text over the live camera feed (the capture census exempts camera-overlay text by name)",
        "evidence": "the camera pane's badge renders in the capture scenes; the census exemption in capture_contrast.CAMERA_OVERLAY_TEXT",
        "date": "2026-09-19",
        "recheck": "the badge's text gains an interactive surface",
    },
    "cameraStreamChip": {
        "reason": "the camera stream chip (decoded resolution + recent bandwidth): display-only, driven by the renderer's published statistics",
        "evidence": "test_qml_real_engine's chip visibility/text contract; the captures' non-live camera hides it",
        "date": "2026-09-19",
        "recheck": "the chip gains an interactive surface",
    },
    "temperatureDataCanvas": {
        "reason": "the temperature chart's data canvas: display-only, painted from the model's payloads; no scenario verb addresses a canvas",
        "evidence": "test_qml_real_engine's ChartSurfaceTests (strategy, paint-job snapshot, hover); the capture census renders its pixels",
        "date": "2026-09-19",
        "recheck": "the canvas gains an interactive surface",
    },
    "temperatureHoverCursor": {
        "reason": "the chart hover cursor line: display-only scene-graph geometry following the snapped hover second",
        "evidence": "test_qml_real_engine's ChartSurfaceTests hover-scene-graph contract",
        "date": "2026-09-19",
        "recheck": "the cursor gains an interactive surface",
    },
    "temperatureHoverMarkers": {
        "reason": "the chart hover markers' repeater: display-only scene-graph dots at each series' nearest sample",
        "evidence": "test_qml_real_engine's ChartSurfaceTests hover-scene-graph contract",
        "date": "2026-09-19",
        "recheck": "the markers gain an interactive surface",
    },
    "moonrakerEmergencyButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
}
