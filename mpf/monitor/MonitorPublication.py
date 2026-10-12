"""The committed publication: one value map, its QVariant cache, the
notification order.

A presenter hands the facade its value group; the facade merges the
groups into one frame and commits it here whole. Presenters never commit
a frame of their own — a partial commit shows a half-built map — so the
map and the cache change on exactly one call per poll.

The groups below are the notification contract: their order is the order
the facade emits them in, and a published key absent from its signal's
group can never notify. Group membership and the declarations it guards
therefore live in two files, and `tests/test_monitor_qml_contracts.py`
reads both.
"""
from __future__ import annotations

from PyQt6.QtCore import QVariant

SIGNAL_GROUPS = (
    ("monitorChanged", ("previewToolheadReadout", "toolheadFanReadings", "monitorState", "monitorConnected", "monitorFilename", "monitorProgress", "monitorLayer", "monitorLayerProgress",
                        "platePassFraction",
                        "improvingEta", "printIndexReady", "improveEtaProgress", "improveEtaPhase", "monitorElapsed",
                        "monitorEta", "monitorEtaBasis", "monitorFinish", "monitorSpeed", "monitorFlow",
                        "monitorPosition", "monitorPositionCompact", "monitorVelocity", "monitorFlowRate", "monitorFlowDiameter",
                        "monitorAccelLimit", "monitorMessage", "monitorLayerSource", "filamentUsed", "filamentRemaining",
                        "sectionReason", "sectionReasonDetail",
                        # The round's additions (the panel's catch):
                        # a key outside its signal's group never
                        # notifies — the next-pause readout and
                        # the collapsed cells went stale while
                        # PAUSED (the other keys in the group
                        # masked it while printing).
                        "nextPauseLayer", "nextPauseEta", "nextPauseFraction", "nextPauseBaked",
                        "monitorPositionX", "monitorPositionY", "monitorPositionZ",
                        "migrationBannerVisible", "migrationBannerText", "migrationBackupAvailable",
                        "migrationDiagnosticsVisible", "migrationDiagnosticsText")),
    ("webcamsChanged", ("webcamNames", "activeWebcamIndex")),
    ("temperatureChartMiniChanged", ("temperatureChartMini",)),
    ("temperatureChartFullChanged", ("temperatureChartFull",)),
    ("temperatureChartLatestChanged", ("temperatureChartLatest",)),
    ("temperatureChartLegendChanged", ("temperatureChartLegend",)),
    ("cameraTransformChanged", ("cameraName", "cameraRotation", "cameraFlipHorizontal", "cameraFlipVertical")),
    ("detectionChanged", ("detectionState", "detectionScore", "detectionRawScore",
                          "detectionStatus")),
    ("peripheralsChanged", ("temperatureItems", "fanItems", "filamentSensorItems")),
    ("plateObjectsChanged", ("plateObjects", "plateDot", "plateHasObjects")),
    # The follower view's state precedes the plate payloads: a
    # detaching seek flips followerAttached in the SAME emission
    # cycle BEFORE the new layer's payload arrives, so QML never
    # paints the new current layer as a pending base while it
    # still reads the previous attached state and then clears it
    # .
    ("followerViewChanged", ("followerShowPrevious", "followerShowNext", "followerShowBase", "followerShowTravels", "followerShowAxisArrows", "followerShowRetractions", "followerShowUnretractions", "followerTrueThickness", "followerAntialiasing", "followerKeepCentred", "followerSoftwareRendering", "followerMotionSmoothing", "followerLineScale",
                             "followerTravelVisualRatio", "followerAttached", "followerLayerAnchor")),
    # The popover's pause block: the schedule's rows and the
    # candidate-derived gates. Its own group — a pause landing
    # while the follower view stands still must not re-wrap the
    # plate payloads.
    ("pauseAtLayerChanged", ("pauseAtLayerActive", "pauseAtLayerCandidate", "pauseAtLayerCanToggle",
                             "pauseAtLayerScheduled", "pauseAtLayerSummary", "pauseAtLayerItems",
                             "pauseAtLayerUnavailableText", "pauseAtLayerHasBaked",
                             "pauseAtLayerHasClearable")),
    ("plateScrubVectorChanged", ("plateScrubVector",)),
    ("plateLiveScrubVectorChanged", ("plateLiveScrubVector",)),
    ("plateProgressChanged", ("plateLayers", "plateSplit", "platePartial", "plateProgressAnchor", "plateAnchorEta", "plateProgressAvailable", "plateTrackingAvailable", "plateProgressReason", "plateSourceStatus", "plateSourceBusy", "plateSourceResolving", "plateSourceProgress",
                              "plateLayerCount", "plateLayerMotionCount",
                              "plateLiveLayers", "plateLiveSplit", "plateLivePartial", "plateLiveAnchor", "plateLiveAvailable",
                              "plateNavigationData", "plateNavigationSplit",
                              "plateNavigationBacking", "plateSceneEpoch")),
    ("powerDevicesChanged", ("powerDevices",)),
    ("systemChanged", ("klippyState", "moonrakerVersion", "klipperVersion", "hostLoad", "memoryAvailable",
                       "cpuTemperature", "mcuSummary", "mcuItems")),
    ("endstopsChanged", ("endstopItems", "endstopSummary")),
    ("actionChanged", ("printActive", "printJobCaption", "canPausePrint", "canResumePrint", "pauseReason", "pauseReasonDetail", "resumeReason", "resumeReasonDetail", "canCancelPrint", "canRestartLastPrint", "actionBusy",
                       "actionStatus", "actionTimestamp", "emergencyHoldProgress")),
    ("controlsChanged", ("monitorLayerHeight", "macroNames", "hasQuadGantryLevel", "hasBedMesh", "canRunSetup",
                         "temperaturePresetNames", "canApplyTemperaturePreset", "speedFactorPercent", "flowFactorPercent",
                         "zOffset", "zOffsetText", "zOffsetApplyTarget", "canApplyZOffset",
                         "fanControlItems", "ledItems", "saveConfigPending", "saveConfigSummary",
                         "canSaveConfig")),
    ("emergencyStopChanged", ("emergencyStopClicks",)),
    ("toolheadChanged", ("jogEnabled", "jogDistance", "extrudeDistance", "extrudeSpeed",
                         "homedAxes", "positionMode", "jogStatus", "jogReason", "jogReasonDetail")),
    ("restartChanged", ("canRestart", "restartReason", "restartReasonDetail")),
    ("controlsLockChanged", ("controlsLocked", "controlsCollapsed")),
    ("infoPaneChanged", ("infoCollapsed",)),
    ("statusPaneChanged", ("statusCollapsed",)),
    ("consoleHeightChanged", ("consoleHeight",)),
    ("sectionsChanged", ("sectionExpandedMap",)),
    ("sectionLayoutChanged", ("sectionLayout", "sectionHiddenMap")),
    ("showProbePointsChanged", ("showProbePoints",)),
    ("cameraRefreshChanged", ("cameraRefreshNonce",)),
    ("webcamStreamEnabledChanged", ("webcamStreamEnabled",)),
    ("cameraFpsChanged", ("cameraFps", "cameraFpsMin", "cameraFpsMax",
                          "cameraSnapshotMaxFps", "cameraSnapshotAvailable", "cameraSnapshotMode")),
    ("traceCameraTimingChanged", ("traceCameraTiming",)),
    ("cameraRecoveringChanged", ("cameraRecovering",)),
    ("connectionDetailChanged", ("connectionDetail",)),
    ("fileManagerChanged", ("fileManagerRows", "fileManagerRecents", "fileManagerDirectory", "fileManagerDirectories", "fileManagerDiskText", "fileManagerNote",
                            "fileManagerRefreshedAt", "fileManagerShown", "fileManagerPage", "fileManagerPageIndex",
                            "fileManagerPageCount", "fileManagerPageSize", "fileManagerPageSelection",
                            "fileManagerEmptyKind", "fileManagerSelected", "fileManagerSortColumn",
                            "fileManagerSortAscending", "fileManagerSearch", "fileManagerOpen", "fileManagerFilters",
                            "filePrintConfirm", "fileDeleteConfirm", "fileRenameTarget",
                            "fileRenameConflict", "fileUploadConfirm", "fileUploadProgress",
                            "fileDownloadProgress",
                            "fileManagerColumnWidths", "fileManagerColumnOrder", "fileManagerColumnHidden",
                            "fileManagerFilterCounts", "fileManagerFilterOptions", "fileManagerHistoryLoaded",
                            "fileManagerHistoryExhausted", "fileManagerWalkError")),
    # Thumbnails publish ALONE (the live report: each
    # scroll-triggered fetch reply rebuilt the whole payload).
    ("fileManagerThumbsChanged", ("fileManagerThumbs",)),
    ("consoleChanged", ("consoleHistory", "consoleLines", "consoleDropped", "consoleRevisions", "consolePending", "consoleErrorBell")),
    ("typedControlsChanged", ("temperaturePresetItems", "pwmOutputItems", "bedMeshAvailable", "bedMeshProfile",
                              "bedMeshProfileNames", "bedMeshRows", "bedMeshColumns", "bedMeshValues", "bedMeshMinimum",
                              "bedMeshMaximum", "bedMeshRange", "bedMeshXMin", "bedMeshXMax", "bedMeshYMin", "bedMeshYMax",
                              "bedMeshRangeText", "bedMeshPreviewVisible", "bedMeshThresholdLow", "bedMeshThresholdHigh",
                              "bedMeshMachineWidth", "bedMeshMachineDepth",
                              "bedMeshCenterIsZero")),
)


class MonitorPublication:
    """The committed map, the QVariant cache and the changed-group decision."""

    def __init__(self):
        self._values = {}
        self._qv_cache = {}

    @property
    def values(self):
        return self._values

    def get(self, name, default=None):
        return self._values.get(name, default)

    def set(self, name, value) -> None:
        """Amend the committed frame in place for a key owned by the light
        publish (the thumbnail flush), so it never rebuilds the rows."""
        self._values[name] = value

    def read(self, kind, name, default):
        """One QVariant conversion per VALUE REBUILD, not per read: at the
        mature 1800-sample payload a conversion costs ~6.75 ms (measured in the
        pinned container), and the chart is read by several bindings per aux
        feed. The stored value's identity is stable across publishes by design
        (payloads rebuild only on real changes), so the cache hits for every
        unchanged publish."""
        value = self._values.get(name, default)
        cached = self._qv_cache.get(name)
        if cached is None or cached[0] is not value:
            cached = (value, QVariant(value) if kind is QVariant else value)
            self._qv_cache[name] = cached
        return cached[1]

    def store(self, values):
        """Commit the next map. The whole frame lands at once, and the same
        object is what the callers still hold while the frame is amended."""
        self._values = values

    def changed(self, previous):
        """The signal groups whose keys differ from `previous`, in the order
        the facade must emit them. Broadcasting every signal for every poll was
        re-evaluating bound ComboBox/Slider values while the user was
        interacting with them, and QVariant-list updates could rebuild Repeater
        delegates mid-drag."""
        current = self._values
        return [signal for signal, keys in SIGNAL_GROUPS
                if any(previous.get(key) != current.get(key) for key in keys)]
