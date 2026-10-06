"""The surface→scenario map (coverage.py's check consumes this).

Values are the suite spec ids in the scenarios package (``a1``..``z14``).
``PREFIX_RULES`` cover whole families with one rule; ``EXCLUSIONS``
are justified surfaces the unit/Qt suites already own or the probe
evidence defers (the design allows an explicit, justified exclusion
and nothing else) — each entry carries reason/evidence/date/recheck
under the workstream-4 schema.
"""
from __future__ import annotations

SCENARIO_MAP = {
    "MoonrakerMonitorModel.setFollowerColourMode": "b11",
    "moonrakerFollowerColourMode": "b11",
    "MoonrakerMonitorModel.setFollowerShowRetractions": "b11",
    "MoonrakerMonitorModel.setFollowerTrueThickness": "b11",
    "MoonrakerMonitorModel.setFollowerShowUnretractions": "b11",
    # The QML-facing verbs on the settings machine action (the settings group).
    "MoonrakerFollowerMachineAction.cancelTest": "i2",
    "MoonrakerFollowerMachineAction.clearCache": "i6",
    "MoonrakerFollowerMachineAction.insecureKeyWarning": "i2",
    "MoonrakerFollowerMachineAction.saveConfig": "i6",
    "MoonrakerFollowerMachineAction.testConnection": "i2",
    "MoonrakerFollowerMachineAction.validAuxInterval": "i3",
    "MoonrakerFollowerMachineAction.validCacheMax": "i3",
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
    "MoonrakerMonitorModel.restartLastPrint": "g6",
    "MoonrakerMonitorModel.runMacro": "g5",
    "MoonrakerMonitorModel.runQuadGantryLevel": "g2",
    "MoonrakerMonitorModel.saveConfig": "i6",
    "MoonrakerMonitorModel.selectWebcam": "e2",
    "LocalDetectionService.set_enabled": "e4",
    "MoonrakerMonitorModel.setWebcamStreamEnabled": "e2",
    "MoonrakerMJPGImage.clearFrame": "e2",
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
    "MoonrakerMonitorModel.setFollowerShowPrevious": "b11",
    "MoonrakerMonitorModel.setFollowerShowNext": "b11",
    "MoonrakerMonitorModel.setFollowerShowBase": "b11",
    "MoonrakerMonitorModel.setFollowerShowTravels": "b11",
    "MoonrakerMonitorModel.setFollowerShowAxisArrows": "b11",
    "MoonrakerMonitorModel.setFollowerAntialiasing": "b11",
    "MoonrakerMonitorModel.setFollowerGpuRendering": "b11",
    "MoonrakerMonitorModel.setFollowerKeepCentred": "b11",
    "MoonrakerMonitorModel.setFollowerLineScale": "b11",
    # The follower's view and layer seeks (4.6.0): the attach/detach
    # freeze and the layer slider's anchor.
    "MoonrakerMonitorModel.setFollowerAttached": "b11",
    "MoonrakerMonitorModel.setFollowerLayerAnchor": "b11",
    # The slider's raw tick (the debounce's start for the seek trace).
    "MoonrakerMonitorModel.seekAnchorTicked": "b11",
    # The progress slider's within-layer scrub (the 4.6.0 request).
    "MoonrakerMonitorModel.setFollowerLayerProgress": "b11",
    # The native renderer's inputs (4.6.0 round 3): the view and the
    # bed plot the raster bakes in.
    "MoonrakerMonitorModel.setFollowerView": "b11",
    "MoonrakerMonitorModel.setFollowerPlot": "b11",
    # The owner-thread raster commit (an internal handler, not a
    # QML-driven surface — exercised through the follower scenarios).
    "PlateRenderController._raster_committed": "b11",
    # The worker's start report (the scheduler's superseded-before-
    # start accounting — exercised through the follower scenarios).
    "PlateRenderController._raster_started": "b11",
    # The seek-trace stage log (a diagnostics instrument, gated on
    # the seek_trace config — exercised by the trace scenarios).
    "PlateRenderController.trace": "b11",
    # The plate popovers' open states (the closed-surface freeze).
    "MoonrakerMonitorModel.setFollowerPopoverOpen": "b11",
    "MoonrakerMonitorModel.setFollowerInteracting": "b11",
    "MoonrakerMonitorModel.setFollowerGestureBake": "b11",
    # The gesture's navigation-file hold: the face names the raster it
    # is presenting so a mid-gesture supersede cannot unlink it.
    "MoonrakerMonitorModel.setFollowerGestureRaster": "b11",
    # The face's barrier report (the warm raster's hold diagnostic).
    "MoonrakerMonitorModel.followerHoldReport": "b11",
    "MoonrakerMonitorModel.setPickerPopoverOpen": "b11",
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
    # The follower's own controls (4.6.0): the toolhead jump, the
    # layer seek with its attach button and readout, and the
    # display-only within-layer bar.
    "moonrakerFollowerJump": "b11",
    "moonrakerFollowerKeepCentred": "b11",
    "moonrakerFollowerLayerEta": "b11",
    "moonrakerFollowerLayerSlider": "b11",
    "moonrakerFollowerLayerReadout": "b11",
    "moonrakerFollowerAttach": "b11",
    "moonrakerFollowerLayerProgress": "b11",
    "moonrakerFollowerLayerProgressReadout": "b11",
    "moonrakerFollowerPauseButton": "b11",
    "followerShowPrevious": "b11",
    # The webcam pane's FPS/zoom controls (4.6.0): the bar, its chip
    # readouts, the gesture area and the badges — the camera scenario.
    "cameraBar": "e2",
    "cameraBarChip": "e2",
    "cameraBarChipText": "e2",
    "cameraFpsBar": "e2",
    "cameraSnapshotRegion": "e2",
    "cameraFpsMarker": "e2",
    "cameraFpsReadout": "e2",
    "cameraFpsScale": "e2",
    "cameraFrame": "e2",
    "failureDetectionSection": "e4",
    "cameraGestureArea": "e2",
    "cameraLiveBadge": "e2",
    "cameraZoomBar": "e2",
    "cameraZoomMarker": "e2",
    "cameraZoomReadout": "e2",
    "cameraZoomScale": "e2",
    "followerShowNext": "b11",
    "followerShowBase": "b11",
    "followerShowTravels": "b11",
    "followerShowAxisArrows": "b11",
    "followerAntialiasing": "b11",
    "followerSoftwareRendering": "b11",
    "followerKeepCentred": "b11",
    "followerLineScale": "b11",
    "followerTravelVisualRatio": "b11",
    # The popover's pause-at-layer block (4.6.0): the card's own
    # schedule, read from the popover's own layer — the nine published
    # keys, the three intents its controls send, and the button itself.
    # b11 opens the popover over the live print and reads the block
    # back off the model.
    "pauseAtLayerActive": "b11",
    "pauseAtLayerCandidate": "b11",
    "pauseAtLayerCanToggle": "b11",
    "pauseAtLayerScheduled": "b11",
    "pauseAtLayerSummary": "b11",
    "pauseAtLayerItems": "b11",
    "pauseAtLayerUnavailableText": "b11",
    "pauseAtLayerHasBaked": "b11",
    "pauseAtLayerHasClearable": "b11",
    "MoonrakerMonitorModel.togglePauseAtLayer": "b11",
    "MoonrakerMonitorModel.removePauseAtLayer": "b11",
    "MoonrakerMonitorModel.clearPauseAtLayer": "b11",
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
    # The replace prompt (the card's own dialog): h2 asks, cancels and
    # asks again, so all three surfaces ride that one scenario — the
    # cancel surface has no other caller.
    "moonrakerReplacePrompt": "h2",
    "moonrakerReplaceCancelButton": "h2",
    # Its PRESS lives in the preview leg: h2 answers by keyboard
    # (Escape, then Return) so both keyboard answers are witnessed,
    # and p1 presses the same button with the mouse.
    "moonrakerReplaceConfirmButton": "p1",
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
    ("objectName", "moonrakerExtruderMarkers", "b11"),
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
    ("slot", "MoonrakerMonitorModel.setCameraFps", "e2"),
    # The published keys by family.
    ("key", "console", "d1"),
    ("key", "camera", "e2"),
    ("key", "temperature", "c1"),
    ("key", "fan", "c2"),
    ("key", "fileManager", "f1"),
    ("key", "fileUpload", "f2"),
    ("key", "fileDownloadProgress", "f2"),
    ("slot", "MoonrakerMonitorModel.fileDownloadCancel", "f2"),
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
    ("key", "webcamStreamEnabled", "e2"),
    ("key", "speedFactorPercent", "c2"),
    ("key", "flowFactorPercent", "c2"),
    ("key", "ledItems", "c3"),
    ("key", "pwmOutputItems", "c3"),
    ("key", "fanControlItems", "c2"),
    ("key", "fanItems", "c2"),
    ("key", "filamentSensorItems", "c4"),
    ("key", "plateObjects", "b11"),
    ("key", "plateDot", "b11"),
    ("key", "plateLayers", "b11"),
    ("key", "plateSplit", "b11"),
    ("key", "platePartial", "b11"),
    ("key", "plateSceneEpoch", "b11"),
    ("key", "plateProgressAnchor", "b11"),
    ("key", "plateAnchorEta", "b11"),
    ("key", "plateProgressAvailable", "b11"),
    ("key", "plateProgressReason", "b11"),
    ("key", "plateHasObjects", "b11"),
    ("key", "plateLayerMotionCount", "b11"),
    ("key", "plateLiveLayers", "b11"),
    ("key", "plateLiveSplit", "b11"),
    ("key", "plateLivePartial", "b11"),
    ("key", "plateLiveAnchor", "b11"),
    ("key", "plateLiveAvailable", "b11"),
    # The layer slider's range and the follower's follow state (4.6.0).
    ("key", "plateLayerCount", "b11"),
    ("key", "followerAttached", "b11"),
    ("key", "followerMotionSmoothing", "b11"),
    ("key", "followerShowRetractions", "b11"),
    ("key", "followerTrueThickness", "b11"),
    ("key", "followerShowUnretractions", "b11"),
    ("key", "followerLayerAnchor", "b11"),
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
    ("key", "canRestartLastPrint", "g6"),
    ("key", "pauseReason", "g6"),
    ("key", "pauseReasonDetail", "g6"),
    # The next scheduled pause's published keys ride the improve-ETA
    # flow: h8's index build leaves the snapshot (and so these keys)
    # carrying the pause ahead.
    ("key", "nextPauseEta", "h8"),
    ("key", "nextPauseFraction", "h8"),
    ("key", "nextPauseLayer", "h8"),
    ("key", "nextPauseBaked", "h8"),
    ("key", "platePassFraction", "h8"),
    ("key", "plateScrubVector", "h8"),
    ("key", "plateLiveScrubVector", "h8"),
    ("key", "plateNavigationData", "h8"),
    ("key", "plateNavigationSplit", "h8"),
    ("key", "plateNavigationBacking", "h8"),
    ("key", "resumeReason", "g6"),
    ("key", "resumeReasonDetail", "g6"),
    ("key", "canCancelPrint", "g6"),
    ("key", "actionBusy", "b10"),
    ("key", "actionStatus", "b10"),
    ("key", "actionTimestamp", "b10"),
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
    "moonrakerFollowerSourceProgress": {
        "reason": "read-only source-download indicator; no native scenario downloads an uncached print",
        "evidence": "test_qml_plate_navigation: follower progress, unknown length and animation",
        "date": "2026-10-01",
        "recheck": "a native follower scenario observes a partial source download",
    },
    "moonrakerFollowerSourceStatus": {
        "reason": "read-only source-download caption beside the follower indicator",
        "evidence": "test_qml_plate_navigation: status appears and clears with download phase",
        "date": "2026-10-01",
        "recheck": "a native follower scenario observes a partial source download",
    },
    "moonrakerJobSourceProgress": {
        "reason": "read-only job download indicator; native scenarios do not stream a partial file",
        "evidence": "test_qml_dashboard_layout: 0%, 42%, 100% and unknown-length progress",
        "date": "2026-10-01",
        "recheck": "a native job scenario observes a partial source download",
    },
    "moonrakerJobSourceStatus": {
        "reason": "read-only job download caption beside the progress indicator",
        "evidence": "test_qml_dashboard_layout: visible during transfer and hidden when ready",
        "date": "2026-10-01",
        "recheck": "a native job scenario observes a partial source download",
    },
    "plateSourceProgress": {
        "reason": "read-only model fraction for both source-download indicators",
        "evidence": "test_monitor_model_runtime: unknown and partial fractions; test_coordinator_coverage: file-event refresh",
        "date": "2026-10-01",
        "recheck": "a native scenario streams and asserts source progress",
    },
    "plateSourceStatus": {
        "reason": "read-only model status for both source-download captions",
        "evidence": "test_monitor_model_runtime: downloading, failed and ready transitions",
        "date": "2026-10-01",
        "recheck": "a native scenario asserts source download failure and status",
    },
    "plateSourceBusy": {
        "reason": "read-only indicator gate; a failure caption must not animate",
        "evidence": "test_monitor_model_runtime: busy resolving/transferring, idle on failure",
        "date": "2026-10-01",
        "recheck": "a native scenario asserts source resolution, transfer and failure",
    },
    "plateSourceResolving": {
        "reason": "read-only metadata phase for the missing-source indicator",
        "evidence": "test_monitor_model_runtime: resolving is indeterminate, not a download",
        "date": "2026-10-01",
        "recheck": "a native scenario asserts source metadata lookup before a download",
    },
    "plateDownloadProgressRow": {
        "reason": "noninteractive progress readout; the enclosing download action is mapped",
        "evidence": "test_qml_object_picker: instruction and progress rows are centred on the real engine",
        "date": "2026-09-27",
        "recheck": "the progress row gains an input action",
    },
    "plateTrackingAvailable": {
        "reason": "ungated live tracking flag used by the picker, independent of follower visibility",
        "evidence": "test_monitor_model_runtime: live tracking readiness updates with both follower surfaces closed",
        "date": "2026-09-27",
        "recheck": "the native picker scenario closes both follower surfaces before testing tracking",
    },
    "printIndexReady": {
        "reason": "read-only download guard; startup with an index but no physical layer is covered in Qt runtime tests",
        "evidence": "test_monitor_model_runtime: indexed PRINT_START does not offer another download",
        "date": "2026-09-27",
        "recheck": "a native PRINT_START scenario covers this waiting phase",
    },
    "moonrakerFollowerColourControls": {
        "reason": "noninteractive legend container; its colour selector is mapped to b11",
        "evidence": "test_qml_plate_navigation: colour keys preserve geometry and paint gradients",
        "date": "2026-09-27",
        "recheck": "the legend container becomes interactive",
    },
    "moonrakerFollowerColourGradient": {
        "reason": "paint-only gradient key, not an input surface",
        "evidence": "test_qml_plate_navigation: all gradient modes paint distinct sampled colours",
        "date": "2026-09-27",
        "recheck": "the gradient key gains an input action",
    },
    "GpuFollower.pointAtMotion": {
        "reason": "internal retained-geometry lookup for the animated marker, not a user command",
        "evidence": "tests/test_gpu_follower.py: test_marker_tracks_every_curve_subedge_and_matches_shader_fraction",
        "date": "2026-09-27", "recheck": "marker geometry lookup or fractional stroke encoding changes",
    },
    "monitorPopoverPointerBarrier": {
        "reason": "internal input shield; real Qt mouse and wheel delivery is covered by the engine suite",
        "evidence": "tests/test_qml_camera_controls.py: test_a_popover_blocks_camera_gestures_but_the_uncovered_webcam_still_works",
        "date": "2026-09-27", "recheck": "popover input shielding changes",
    },
    "MoonrakerMonitorModel.acquirePlateAssetOwner": {
        "reason": "internal face lifetime protocol, not a user command",
        "evidence": "tests/test_monitor_model_coverage.py: asset_owners_release_independently_and_cannot_be_resurrected",
        "date": "2026-09-26", "recheck": "asset lifetime API becomes a user command",
    },
    "MoonrakerMonitorModel.setPlateAssetReferences": {
        "reason": "internal atomic asset snapshot, not a user command",
        "evidence": "tests/test_monitor_model_coverage.py: presentation_references_survive_arbitrary_prefix_supersedes",
        "date": "2026-09-26", "recheck": "asset lifetime API becomes a user command",
    },
    "MoonrakerMonitorModel.releasePlateAssetOwner": {
        "reason": "internal face destruction protocol, not a user command",
        "evidence": "tests/test_monitor_model_coverage.py: asset_owners_release_independently_and_cannot_be_resurrected",
        "date": "2026-09-26", "recheck": "asset lifetime API becomes a user command",
    },

    # The T0-T9 cold-camera timing chain:
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
        "evidence": "test_migration_notice's latch tests; the model's record values in test_monitor_model_coverage",
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
        "evidence": "test_migration_notice; the model's record values in test_monitor_model_coverage",
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
        "evidence": "the model's migrationDiagnosticsVisible/Text values in test_monitor_model_coverage",
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
        "evidence": "test_monitor_qml_contracts pins the cells' axis tokens and no-wrap; the Toolhead precedent",
        "date": "2026-09-18",
        "recheck": "a scenario asserts the Status pane's axis colours",
    },
    "moonrakerInfoContent": {
        "reason": "the information pane's container, addressed by the real-engine tests; the section ORDER is asserted by the configure scenarios through the headers, not by this name",
        "evidence": "test_qml_dashboard_interaction's SectionOrderArrivalTests address it; the s-scenarios pin the rendered order end-to-end",
        "date": "2026-09-18",
        "recheck": "the configure scenarios adopt the objectName directly",
    },
    "jobPositionCellY": {
        "reason": "the Position row's axis cell (the 4.5.0 colour ruling); the colour mapping is a QML pin, not a scenario surface",
        "evidence": "test_monitor_qml_contracts pins the cells' axis tokens and no-wrap; the Toolhead precedent",
        "date": "2026-09-18",
        "recheck": "a scenario asserts the Status pane's axis colours",
    },
    "jobPositionCellZ": {
        "reason": "the Position row's axis cell (the 4.5.0 colour ruling); the colour mapping is a QML pin, not a scenario surface",
        "evidence": "test_monitor_qml_contracts pins the cells' axis tokens and no-wrap; the Toolhead precedent",
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
    # The follower popover's own pause list: the same seam as the
    # card's model above — the b11 scenario reads the block off the
    # live model and the rows through the list view, never the model
    # object itself.
    "moonrakerFollowerPauseListModel": {
        "reason": "probe seam for the popover list's in-place sync; no scenario reads the model object itself",
        "evidence": "b11 reads the published pause block off the model and the QML syncs it into this model in place",
        "date": "2026-09-25",
        "recheck": "a popover pause-row scenario that presses the row's ✕ lands",
    },
    # The status column's two geometry address points (the status-width
    # fix): the flickable and the column it holds are measured, never
    # pressed — a scenario would only be reading their rects.
    "moonrakerStatusFlick": {
        "reason": "geometry address point of the status pane; no scenario presses it",
        "evidence": "test_qml_dashboard_layout's StatusColumnGeometryTests measures the column against it",
        "date": "2026-09-18",
        "recheck": "a scenario scrolls or presses inside the status pane",
    },
    "jobTelemetryGrid": {
        "reason": "passive geometry seam for the print-job telemetry layout, not an interactive surface",
        "evidence": "test_qml_dashboard_layout.SectionContentSizingTests checks no-wrap labels and stable height across widths and telemetry states",
        "date": "2026-09-28",
        "recheck": "the telemetry grid gains an interactive action",
    },
    "moonrakerStatusContent": {
        "reason": "geometry address point of the status column; no scenario presses it",
        "evidence": "test_qml_dashboard_layout's StatusColumnGeometryTests measures the sections against it",
        "date": "2026-09-18",
        "recheck": "a scenario scrolls or presses inside the status pane",
    },
    # The controls pane's two geometry address points (the constant
    # gutter fix): the flickable and the column it holds are measured,
    # never pressed — a scenario would only be reading their rects.
    "moonrakerControlsFlick": {
        "reason": "geometry address point of the controls pane; no scenario presses it",
        "evidence": "test_qml_dashboard_layout's PaneGutterTests measures the column against it",
        "date": "2026-09-19",
        "recheck": "a scenario scrolls or presses inside the controls pane",
    },
    "moonrakerControlsContent": {
        "reason": "geometry address point of the controls column; no scenario presses it",
        "evidence": "test_qml_dashboard_layout's PaneGutterTests measures the gutter against it",
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
    # The background optimisation's sweep: a passive scanline on the
    # same stacked track, asserted through the model value rather than
    # rendered pixels (it vanishes at completion).
    "optimisationBand": {
        "reason": "the job section's optimisation scanline is styling, not a scenario surface",
        "evidence": "test_runtime_index_composition.test_the_job_bar_band_tracks_the_prepared_share pins the value; the census pins the visibility",
        "date": "2026-09-21",
        "recheck": "a scenario asserts the optimisation band's rendered state",
    },
    # The drag GESTURE stays excluded: the synthetic drag cannot drive
    # a QML MouseArea grab under Xvfb (the console-resize precedent).
    # Retired: x6 drives a real press/move/release on the handle via
    # QTest (the s8 track-click precedent) and gates the rendered
    # reorder the plain release commits.
    # British-spelling formatting is a pure function of the locale —
    # unit-tested in test_monitor_qml_contracts, invisible to scenarios.
    "britishSpelling": {
        "reason": "locale formatting, a pure function",
        "evidence": "unit-tested in test_monitor_qml_contracts.py",
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
    "downloadProgressCancelButton": {
        "reason": "the download popup's chrome — the scenario addresses the cancel verb directly (fileDownloadCancel), the button rides the same flow",
        "evidence": "the model coverage presses the cancel verb; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-20",
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
        "evidence": "test_qml_camera_lifecycle's chip visibility/text contract; the captures' non-live camera hides it",
        "date": "2026-09-19",
        "recheck": "the chip gains an interactive surface",
    },
    "temperatureDataCanvas": {
        "reason": "the temperature chart's data canvas: display-only, painted from the model's payloads; no scenario verb addresses a canvas",
        "evidence": "test_qml_chart's ChartSurfaceTests (strategy, paint-job snapshot, hover); the capture census renders its pixels",
        "date": "2026-09-19",
        "recheck": "the canvas gains an interactive surface",
    },
    "temperatureHoverCursor": {
        "reason": "the chart hover cursor line: display-only scene-graph geometry following the snapped hover second",
        "evidence": "test_qml_chart's ChartSurfaceTests hover-scene-graph contract",
        "date": "2026-09-19",
        "recheck": "the cursor gains an interactive surface",
    },
    "temperatureHoverMarkers": {
        "reason": "the chart hover markers' repeater: display-only scene-graph dots at each series' nearest sample",
        "evidence": "test_qml_chart's ChartSurfaceTests hover-scene-graph contract",
        "date": "2026-09-19",
        "recheck": "the markers gain an interactive surface",
    },
    "moonrakerChartDetail": {
        "reason": "the chart pop-over's card: a noninteractive overlay container whose opener, legend and colour verbs are mapped; the card is addressed only by the picker's lifecycle test",
        "evidence": "test_qml_chart's ChartColourPickerTests invoke the card's own build-on-first-click verb; capture scene 07 renders the card",
        "date": "2026-09-30",
        "recheck": "the chart pop-over scenario lands",
    },
    "moonrakerEmergencyButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
    # The prefix handover's two Images (the composition transaction):
    # identity address points for the real-engine ownership read, which
    # proves no beat of a boundary advance leaves the interior to the
    # canvas's bitmap alone. No scenario presses an Image.
    "moonrakerPlateProgressCanvas": {
        "reason": "internal texture producer addressed only by real-engine delivery tests",
        "evidence": "tests/test_qml_plate_prefix_refresh.py: repeated_forward_refreshes_keep_every_frame_complete",
        "date": "2026-09-26",
        "recheck": "the harness directly addresses the Canvas",
    },
    "moonrakerPlatePreparingCover": {
        "reason": "internal compositor presentation cover, not an interactive surface",
        "evidence": "test_qml_plate_paint_delivery: obsolete-world upload stays masked until current-view paint delivery",
        "date": "2026-09-26",
        "recheck": "exact compositor presentation or Canvas delivery changes",
    },
    "moonrakerPlatePrefixImage": {
        "reason": "the live replacement in the prefix handover: addressed by the ownership read, never pressed",
        "evidence": "test_qml_plate_prefix_ownership's prefix tests (the ownership invariant, the stroke census)",
        "date": "2026-09-23",
        "recheck": "a scenario addresses the prefix images directly",
    },
    "moonrakerPlateRetainedPrefixImage": {
        "reason": "the retained record in the prefix handover: addressed by the ownership read, never pressed",
        "evidence": "test_qml_plate_prefix_ownership's prefix tests (the ownership invariant, the stroke census)",
        "date": "2026-09-23",
        "recheck": "a scenario addresses the prefix images directly",
    },
    # The zoom rail down the plate's edge: raised only by a wheel or a
    # right-drag, and the harness's input set carries neither — the
    # same limit moonrakerFollowerJump's absent-wait records. No
    # scenario can reach it, so it carries an exclusion rather than a
    # mapping. The name exists for the Qt suite, which addresses it to
    # take it out of the miter stroke census's frame.
    "moonrakerPlateZoomScope": {
        "reason": "the plate's zoom rail: raised only by a wheel or a right-drag, neither of which the harness input set carries",
        "evidence": "test_zoom_raster_real_engine.py's chrome exclusion in the miter stroke census",
        "date": "2026-09-24",
        "recheck": "the harness gains wheel or drag input over the plate face",
    },
}

# Consented downloads and analysed live frames are unavailable to the
# simulator; the unit and real-QML suites exercise these new surfaces.
for _name in (
    "LocalDetectionService.cancel", "LocalDetectionService.decline_offer",
    "LocalDetectionService.reset", "LocalDetectionService.setup",
    "LocalDetectionService.remove_assets",
    "MoonrakerFollowerMachineAction.cancelDetectionSetup",
    "MoonrakerFollowerMachineAction.declineDetectionOffer",
    "MoonrakerFollowerMachineAction.resetOnboardingForNextRun",
    "MoonrakerFollowerMachineAction.resetDetectionAssets",
    "MoonrakerFollowerMachineAction.startDetectionSetup",
    "detectionDownloadHourglass", "detectionDownloadHourglassRotation",
    "detectionEnabled", "detectionGlobalEnabledCheckbox",
    "detectionReliabilityWarning",
    "detectionSetupProgress", "detectionThresholdSlider",
    "resetOnboardingButton", "resetDetectionAssetsButton", "settingsScrollbar",
    "detectionModelAttribution", "detectionSetupLicense", "detectionOfferLicense",
    "detectionFirstRunOffer", "detectionOfferDismiss",
    "MoonrakerFollowerMachineAction.setDetectionGlobalEnabled",
):
    EXCLUSIONS[_name] = {
        "reason": "consented local setup and settings controls are outside the simulator's network-free scenarios",
        "evidence": "test_detection_service, test_qml_settings and the QML engine import gate",
        "date": "2026-10-03",
        "recheck": "a consented local-setup scenario is available in real Cura",
    }

for _name in ("detectionFirstRunOffer", "detectionOfferDismiss"):
    EXCLUSIONS[_name] = {
        "reason": "the first-install two-boot mode verifies offer ordering and decline without downloading assets",
        "evidence": "tests/harness/runner.py: first_install1 and first_install2",
        "date": "2026-10-03",
        "recheck": "first-install mode gains a consented local-setup branch",
    }

EXCLUSIONS["MoonrakerFollowerMachineAction.revealDetectionEvidence"] = {
    "reason": "the evidence folder only exists after a consented local setup, which the simulator's network-free scenarios never perform",
    "evidence": "test_qml_settings drives the Diagnostics button and the status it writes",
    "date": "2026-10-03",
    "recheck": "a consented local-setup scenario is available in real Cura",
}
EXCLUSIONS["revealDetectionEvidenceButton"] = dict(
    EXCLUSIONS["MoonrakerFollowerMachineAction.revealDetectionEvidence"])

for _name in ("sectionAlertDot",):
    EXCLUSIONS[_name] = {
        "reason": "a standing detection alert needs a real alert on a real print, which the simulator cannot raise",
        "evidence": "test_qml_dashboard_layout and test_qml_camera_controls drive both dots and their level colours",
        "date": "2026-10-03",
        "recheck": "the simulator can raise an unacknowledged detection alert",
    }

for _name in ("MoonrakerMonitorModel.applyZOffset", "applyZOffsetButton", "canApplyZOffset"):
    EXCLUSIONS[_name] = {
        "reason": "the simulator has no unambiguous Z-reference configuration, so Apply is disabled there",
        "evidence": "test_monitor_controls and test_qml_dashboard_layout exercise staged commands and button gating",
        "date": "2026-10-03",
        "recheck": "the harness gains a configured probe or mechanical endstop fixture",
    }

for _name in ("MoonrakerMonitorModel.rearmDetectionPause", "detectionRearmPauseButton",
              "detectionPauseRearmable"):
    EXCLUSIONS[_name] = {
        "reason": "the simulator cannot produce a confirmed local-inference pause latch without a real model",
        "evidence": "test_monitor_model_runtime and test_qml_dashboard_layout cover re-arm and its button",
        "date": "2026-10-03",
        "recheck": "the harness gains a synthetic confirmed-pause fixture",
    }

for _name in (
    "detectionMainEnabledCheckbox", "detectionControlsThresholdSlider",
    "detectionSafePeriodSlider", "detectionSafePeriodValue",
    "detectionNotifyCheckbox", "detectionPauseCheckbox",
    "detectionAcknowledgeButton",
    "MoonrakerMonitorModel.setDetectionEnabled",
    "MoonrakerMonitorModel.setDetectionThresholds",
    "MoonrakerMonitorModel.setDetectionSafeSeconds",
    "MoonrakerMonitorModel.setDetectionNotifyEnabled",
    "MoonrakerMonitorModel.setDetectionPauseEnabled",
    "MoonrakerMonitorModel.acknowledgeDetectionAlert",
):
    EXCLUSIONS[_name] = {
        "reason": "printer-local detection controls have no simulated failure feed or printer-command scenario",
        "evidence": "test_qml_dashboard_layout: FailureDetectionSectionTests exercises real QML toggles and slider, readiness gates, acknowledgment, printer switching and collapse",
        "date": "2026-10-03",
        "recheck": "a native failure-detection controls scenario lands",
    }

for _name in (
    "cameraStreamDisabledNotice", "failureSignalPill",
    "failureSignalPillText", "failureSignalTick", "failureSignalTrack",
    "failureSignalFrame", "failureSignalMarker", "failureSignalDrag", "failureSignalExpand",
    "detectionScore", "detectionRawScore", "detectionGlobalEnabled",
    "detectionState", "detectionStatus",
):
    EXCLUSIONS[_name] = {
        "reason": "local inference camera signal needs an analysed live frame absent from the simulator",
        "evidence": "test_qml_camera_controls, test_monitor_model_runtime and test_detection_policy",
        "date": "2026-10-03",
        "recheck": "permission-cleared recording replay can exercise the live camera scenario",
    }

# The native detector and camera editor have real Qt/owner contracts; the
# network-free desktop simulator has no model-generated failure observations.
for _name in (
    "MonitorDetection.acknowledgeDetectionAlert", "MonitorDetection.rearmDetectionPause",
    "MonitorDetection.resetDetectionTuning", "MonitorDetection.resetDetectionBaseline", "MonitorDetection.saveDetectionRegions",
    "MonitorDetection.resetPrinterDetectionTraining", "MoonrakerMonitorModel.resetPrinterDetectionTraining",
    "resetPrinterTrainingDialog", "resetCameraTrainingDialog", "resetCameraTrainingButton",
    "MonitorDetection.setDetectionEditingRegions", "MonitorDetection.setDetectionEnabled",
    "MonitorDetection.setDetectionMuted", "MonitorDetection.setDetectionNotifyEnabled",
    "MonitorDetection.setDetectionPauseEnabled", "MonitorDetection.setDetectionSafeSeconds",
    "MonitorDetection.setDetectionSensitivity", "MonitorDetection.setDetectionShowBoxes",
    "MonitorDetection.setDetectionThresholds", "MonitorDetection.validateDetectionRegions",
    "MoonrakerMonitorModel.resetDetectionTuning", "MoonrakerMonitorModel.resetDetectionBaseline", "MoonrakerMonitorModel.saveDetectionRegions",
    "MoonrakerMonitorModel.setDetectionEditingRegions", "MoonrakerMonitorModel.setDetectionMuted",
    "MoonrakerMonitorModel.setDetectionSensitivity", "MoonrakerMonitorModel.setDetectionShowBoxes",
    "MoonrakerMonitorModel.validateDetectionRegions",
    "detectionAdvancedButton", "detectionAnalysisAgeLabel", "detectionAnalysisAgePill",
    "detectionCancelRegionsButton", "detectionEditRegionsButton", "detectionAddRegionButton",
    "detectionFullFrameButton", "detectionMutePrintButton", "detectionOverlay",
    "detectionRegionEditor", "detectionRegionEditorStatus", "detectionResetTuningButton",
    "detectionSaveRegionsButton", "detectionSensitivitySlider", "detectionShowBoxesCheckbox",
    "detectionStatusLabel", "detectionUndoRegionEditButton", "detectionDeleteVertexButton",
    "detectionDeleteRegionButton", "detectionBaseline", "detectionBaselineLabel", "detectionResetBaselineButton",
):
    EXCLUSIONS[_name] = {
        "reason": "local inference and transactional camera regions have no synthetic model feed in the desktop simulator",
        "evidence": "test_monitor_detection, test_monitor_model_runtime, test_qml_detection_regions and test_qml_dashboard_layout; capture_monitor scenes 12–14",
        "date": "2026-10-04",
        "recheck": "the desktop simulator gains a synthetic local detector fixture",
    }

for _name in (
    "moonrakerObjectNameBanner", "moonrakerObjectNameRepeater",
    "moonrakerPreviewObjectTags", "moonrakerPreviewObjectTagsDock",
    "moonrakerPreviewObjectTagsHandle", "moonrakerPreviewObjectTagsTitle",
    "moonrakerPreviewObjectTagsControls",
    "moonrakerPreviewObjectTagsEnabled", "moonrakerPreviewObjectTagsHoverOnly",
):
    EXCLUSIONS[_name] = {
        "reason": "the native Preview scenarios do not yet supply per-object G-code and a depth-pickable Cura model scene",
        "evidence": "test_preview_object_tags exercises the real QML engine; test_preview_presentation exercises Cura's selection-pass boundary; test_object_work verifies index and cache data",
        "date": "2026-10-04",
        "recheck": "a native Preview scenario loads an object-marked print with matching model meshes",
    }

EXCLUSIONS["moonrakerPreviewObjectTagsHoverOnlyTooltip"] = {
    "reason": "the native Preview scenarios do not hover the disabled Object banners control",
    "evidence": "test_preview_object_tags checks the tooltip text for disabled banners, unavailable picking and unavailable projection in the real QML engine",
    "date": "2026-10-05",
    "recheck": "a native Preview scenario hovers the disabled Object banners control",
}

EXCLUSIONS["moonrakerPreviewCardCollapseToggle"] = {
    "reason": "the native Preview scenarios exercise the load controls but do not toggle the card layout",
    "evidence": "test_qml_dashboard_layout clicks the real QML card and verifies its compact actions",
    "date": "2026-10-05",
    "recheck": "a native Preview scenario also exercises the card collapse toggle",
}

# Import requires a local picker and download consent, outside the simulator's
# native suite. Real Qt draft/preview tests and captures provide evidence.
for _name in (
    "MoonrakerFollowerMachineAction.cancelToolheadModel",
    "ToolheadModelPreview.orbit", "ToolheadModelPreview.pick", "ToolheadModelPreview.resetCamera", "ToolheadModelPreview.zoomBy",
    "ToolheadModels.automatic", "ToolheadModels.cancel", "ToolheadModels.choose", "ToolheadModels.downloadAndImport",
    "ToolheadModels.reset", "ToolheadModels.setTip", "ToolheadModels.useDefault",
    "ToolheadModels.beginEdit", "ToolheadModels.endEdit", "ToolheadModels.removeLight",
    "ToolheadModels.previewLightBrightness", "ToolheadModels.setLightBrightness", "ToolheadModels.setLightColour", "ToolheadModels.setLightPaint",
    "ToolheadModelPreview.pan", "moonrakerToolheadControls", "moonrakerViewOptionsDivider", "moonrakerBedMeshDivider", "moonrakerViewOptionsBedMesh", "moonrakerLightBed", "moonrakerLightModels", "moonrakerShowToolhead", "moonrakerEstimatedToolheadPosition", "moonrakerToolheadOpacity", "toolheadAddLight", "toolheadConfigureModel",
    "toolheadEditorCancel", "toolheadEditorDone", "toolheadLightBrightness", "toolheadLightColour",
    "toolheadLightPaint", "toolheadLightsScroll", "toolheadLightsScrollbar", "toolheadModelEditorDialog",
    "toolheadModelInteraction", "toolheadRemoveLight",
    "toolheadAutomaticTip", "toolheadChooseModel", "toolheadImportStatus", "toolheadModelPreview", "toolheadPickTip",
    "toolheadTipX", "toolheadTipY", "toolheadTipZ", "toolheadUseDefault",
):
    EXCLUSIONS[_name] = {
        "reason": "native simulator scenarios do not choose local CAD files or consent to optional runtime downloads",
        "evidence": "test_toolhead_models, test_toolhead_geometry, test_toolhead_settings, test_preview_object_tags and test_qml_toolhead_editor exercise drafts, visibility, disabled controls and previews; test_cad_runtime loads coloured assemblies",
        "date": "2026-10-05",
        "recheck": "native settings scenarios exercise a local custom model picker and preview surface alignment",
    }
SCENARIO_MAP["moonrakerReportedToolheadPosition"] = "p9"
SCENARIO_MAP["moonrakerEstimatedToolheadPosition"] = "p9"
EXCLUSIONS.pop("moonrakerEstimatedToolheadPosition", None)
SCENARIO_MAP["moonrakerSetupToolhead"] = "p10"
SCENARIO_MAP["moonrakerToolheadSetupDialog"] = "p10"
EXCLUSIONS["moonrakerBannerCountdown"] = {
    "reason": "a nonvisual timer has no native input rectangle",
    "evidence": "test_preview_object_tags.test_hidden_banner_countdowns_sleep_and_refresh_when_rows_return verifies idle stop and visible restart in real QML",
    "date": "2026-10-06",
    "recheck": "native banner journeys also observe timer lifecycle",
}
for _name in ("toolheadImportElapsed", "toolheadCancelImport"):
    EXCLUSIONS[_name] = {
        "reason": "native journeys do not install the optional CAD reader or hold an opaque conversion in progress",
        "evidence": "test_qml_toolhead_editor.test_elapsed_and_triangle_progress_stay_visible_and_cancel_preserves_model clicks Cancel in real QML; test_toolhead_import terminates and reaps a stalled child process",
        "date": "2026-10-06",
        "recheck": "native settings journeys include cancellable CAD conversion fixtures",
    }
EXCLUSIONS["moonrakerEnableLighting"] = {
    "reason": "native simulator journeys do not yet exercise the new lighting master and retained receiver choices",
    "evidence": "test_preview_presentation tests persistence and signal delivery; test_toolhead_presenter and test_toolhead_scene verify renderer propagation and bypass; test_toolhead_shader pins unlit paint/alpha; live Cura verified disabled receiver controls on 2026-10-06",
    "date": "2026-10-06",
    "recheck": "a native Preview scenario exercises master off/on and retained bed/model selections",
}
