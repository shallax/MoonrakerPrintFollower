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
    "moonrakerM117Slot": "s6",
    "moonrakerPreviewCard": "v1",
    "moonrakerStripPauseButton": "v19",
    "moonrakerStripTemps": "v19", "moonrakerStripSlot": "v19",
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
    # The section-layout surfaces land with the 4.4.0 configure
    # popup; until the popup round the slot and keys carry no
    # scenario that addresses them (unit tests drive the normaliser
    # and the store write).
    "MoonrakerMonitorModel.setSectionLayout": {
        "reason": "the configure popup is unbuilt; the slot is unit-tested in test_section_layout",
        "evidence": "unit tests drive normalise_section_layout and the store write",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the popup scenario drives this slot",
    },
    "MoonrakerMonitorModel.sectionLayoutFor": {
        "reason": "the configure popup is unbuilt; the slot is unit-tested in test_section_layout",
        "evidence": "unit tests drive layout_for and the store write",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the popup scenario drives this slot",
    },
    "sectionLayout": {
        "reason": "the configure popup is unbuilt; the key is unit-tested in test_section_layout",
        "evidence": "unit tests drive normalise_section_layout and the store write",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the popup scenario drives this slot",
    },
    "sectionHiddenMap": {
        "reason": "the configure popup is unbuilt; the key is unit-tested in test_section_layout",
        "evidence": "unit tests drive normalise_section_layout and the store write",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the popup scenario drives this slot",
    },
    "sectionConfigureHandle": {
        "reason": "the popup round is unbuilt; the handle is geometry-addressed once the scenario lands",
        "evidence": "the shared row component ships with the objectName from birth",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the drag scenario addresses the handle",
    },
    "sectionConfigurePopOver": {
        "reason": "the popup round is unbuilt; the popover is named from birth for the scenario",
        "evidence": "the shared popover ships with the objectName from birth",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the scenario opens and drives this popover",
    },
    "configureControlsSectionsButton": {
        "reason": "the popup round is unbuilt; the trigger is named from birth for the scenario",
        "evidence": "the dashboard trigger ships with the objectName from birth",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the scenario presses this trigger",
    },
    "configureInfoSectionsButton": {
        "reason": "the popup round is unbuilt; the trigger is named from birth for the scenario",
        "evidence": "the information trigger ships with the objectName from birth",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the scenario presses this trigger",
    },
    "configureStatusSectionsButton": {
        "reason": "the popup round is unbuilt; the trigger is named from birth for the scenario",
        "evidence": "the status trigger ships with the objectName from birth",
        "date": "2026-09-17",
        "recheck": "the popup round lands — the scenario presses this trigger",
    },
    "monitorPositionCompact": {
        "reason": "the collapsed readouts are presentation-only; the key rides the readout scenario",
        "evidence": "the compact form is a pure formatter, unit-tested via the payload",
        "date": "2026-09-17",
        "recheck": "the readout scenario lands — the collapsed-strip scenario reads this key",
    },
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
    "moonrakerEmergencyButton": {
        "reason": "named but never pressed or addressed — the scenario presses the confirm verb; the chrome and cancel verbs ride the deferred popup round",
        "evidence": "the confirm scenarios' presses; the popup-window addressing follow-up (DECISIONS 4.1.0)",
        "date": "2026-09-15",
        "recheck": "the deferred popup round lands",
    },
}
