"""Executable runtime monitor composition contracts."""
from tests import composed_runtime_support as harness

class ComposedComponentTests(harness.ComposedComponentTests):
    def test_thumbnail_publishes_coalesce_onto_one_flush(self):
        # A burst of landings repaints the QML once, not once per
        # callback (the landing storm stalled scrolling). The flush
        # rides a 100 ms debounce, so the "nothing before the
        # debounce" reading races the wall clock on slow runners —
        # the two load-independent invariants are one flush per
        # burst and no flush for a no-change emit.
        model = self.monitor()
        count = []
        model.fileManagerThumbsChanged.connect(lambda: count.append(1))
        # A real change in the payload, then a burst of signals: the
        # flush emits once, and a no-change flush emits nothing.
        model._file_manager._thumbnails._entries["x.gcode"] = {"state": "ready", "url": ""}
        for _ in range(3):
            model._file_manager.thumbsChanged.emit()
        self.qt.events(200)
        self.assertEqual(count, [1])
        model._file_manager.thumbsChanged.emit()
        self.qt.events(200)
        self.assertEqual(count, [1])

    def test_monitor_and_preview_consume_one_physical_observation(self):
        model = self.monitor()
        self.deliver(self.status(layer=10))
        self.qt.events(60)  # Commit the model's coalesced publication.
        before = self.follower.print_state
        self.assertEqual(before.layer.index, 9)
        self.assertEqual(model.monitorLayer, "10 / 50")
        for _ in range(5):
            model._publish()
        self.assertIs(self.follower.print_state, before)
        with self.assertRaises(harness.FrozenInstanceError):
            before.layer.index = 20

    def test_monitor_snapshot_is_deeply_immutable(self):
        model = self.monitor()
        self.deliver(self.status())
        snapshot = model._data.snapshot
        with self.assertRaises(TypeError): snapshot.core["print_stats"]["state"] = "paused"
        with self.assertRaises(TypeError): snapshot.core["print_stats"] = {}

    def test_a_completed_seek_republishes_without_new_telemetry(self):
        # A completed manual seek republishes
        # off the worker's own completion — never waiting on the next
        # printer heartbeat.
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity(
            "part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        layers = b"".join(
            b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\nG1 X3 Y3 E1\n" % layer
            for layer in range(12))
        target = self.plant_download(files, layers)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(target, compact=True)
        self.qt.load("GCodeIndexService")
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        files._path = target
        files._want_file = True
        model = self.monitor()
        model.setFollowerPopoverOpen(True)
        self.deliver(self.status(layer=2, filename="part.gcode"))
        self.qt.events(30)
        model.setFollowerLayerAnchor(8)
        for _ in range(400):
            self.qt.events(5)
            if model.plateProgressAvailable:
                break
        self.assertTrue(model.plateProgressAvailable,
                        "the seek's layer never published without new telemetry")

    def test_tuning_debounce_keeps_latest_value_and_ignores_old_callbacks(self):
        model = self.monitor()
        self.deliver(self.status())
        tuning = model._tuning
        tuning.DEBOUNCE_MS = 10
        for value in range(110, 140): model.setSpeedFactor(value)
        self.qt.events(25)
        commands = [r for r in self.transport.requests if r.channel.startswith("quick-")]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S139"})
        model.setMonitoringActive(False)
        commands[0].callback({}, None)
        self.assertFalse(tuning._pending)

    def test_malformed_monitor_objects_degrade_without_throwing(self):
        model = self.monitor()
        model.updateMoonrakerStatus({"print_stats": "bad", "virtual_sdcard": [], "gcode_move": 7, "motion_report": None})
        self.assertEqual(model.monitorPosition, "—")
        self.assertEqual(model.monitorProgress, 0)

    def test_mesh_observation_flows_through_coordinator_not_monitor(self):
        model = self.monitor()
        status = self.status()
        status["bed_mesh"] = {"mesh_matrix": [[0, 1], [2, 3]], "mesh_min": [0, 0], "mesh_max": [10, 10]}
        self.deliver(status)
        self.assertTrue(self.follower.bed_mesh.snapshot)
        # Monitor deactivation must not touch the shared mesh observation;
        # the coordinator is its only writer.
        model.setMonitoringActive(False)
        model._controls.observe()
        self.assertTrue(self.follower.bed_mesh.snapshot)
        self.follower.client.stop()
        self.assertFalse(self.follower.bed_mesh.snapshot)

    def test_qml_public_api_is_present_without_model_subclasses(self):
        model = self.monitor()
        properties = "monitorState monitorConnected connectionDetail monitorFilename monitorProgress monitorLayer monitorElapsed monitorEta monitorFinish monitorSpeed monitorFlow monitorPosition monitorPositionCompact monitorVelocity monitorFlowRate monitorFlowDiameter monitorAccelLimit monitorMessage printActive printJobCaption canPausePrint canResumePrint pauseReason pauseReasonDetail resumeReason resumeReasonDetail canCancelPrint actionBusy actionStatus temperatureItems fanItems filamentSensorItems powerDevices klippyState moonrakerVersion klipperVersion hostLoad memoryAvailable cpuTemperature mcuSummary mcuItems webcamNames activeWebcamIndex cameraName cameraRotation cameraFlipHorizontal cameraFlipVertical cameraFps cameraFpsMin cameraFpsMax monitorLayerHeight macroNames hasQuadGantryLevel hasBedMesh canRunSetup temperaturePresetNames temperaturePresetItems canApplyTemperaturePreset speedFactorPercent flowFactorPercent zOffset zOffsetText fanControlItems ledItems pwmOutputItems saveConfigPending saveConfigSummary canSaveConfig emergencyStopClicks bedMeshAvailable bedMeshProfile bedMeshProfileNames bedMeshRows bedMeshColumns bedMeshValues bedMeshMinimum bedMeshMaximum bedMeshRange bedMeshXMin bedMeshXMax bedMeshYMin bedMeshYMax bedMeshRangeText bedMeshPreviewVisible bedMeshThresholdLow bedMeshThresholdHigh bedMeshMachineWidth bedMeshMachineDepth bedMeshCenterIsZero jogEnabled jogDistance extrudeDistance extrudeSpeed homedAxes positionMode jogStatus jogReason jogReasonDetail canRestart restartReason restartReasonDetail sectionReason sectionReasonDetail controlsLocked controlsCollapsed infoCollapsed statusCollapsed sectionLayout sectionHiddenMap consoleHeight cameraRefreshNonce cameraRecovering emergencyHoldProgress temperatureChartMini temperatureChartFull temperatureChartLatest temperatureChartLegend consoleHistory consolePending consoleErrorBell endstopItems endstopSummary monitorEtaBasis showProbePoints fileManagerRows fileManagerRecents fileManagerDirectory fileManagerDirectories fileManagerDiskText fileManagerRefreshedAt fileManagerShown fileManagerPage fileManagerPageIndex fileManagerPageCount fileManagerPageSize fileManagerPageSelection fileManagerEmptyKind fileManagerSelected fileManagerSortColumn fileManagerSortAscending fileManagerSearch fileManagerOpen fileManagerFilters filePrintConfirm fileDeleteConfirm fileRenameTarget fileRenameConflict fileUploadConfirm fileUploadProgress fileManagerThumbs fileManagerFilterCounts fileManagerFilterOptions fileManagerHistoryLoaded fileManagerHistoryExhausted fileManagerWalkError fileManagerNote".split()
        meta = model.metaObject()
        for name in properties: self.assertGreaterEqual(meta.indexOfProperty(name), 0, name)
        for name in "pausePrint resumePrint cancelPrint reconnect refreshAll refreshWebcams selectWebcam setCameraFps runMacro homeAll runQuadGantryLevel calibrateBedMesh applyTemperaturePreset setSpeedFactor setFlowFactor adjustZOffset clearZOffset setFanSpeed setLedBrightness setLedColor setPwmOutput saveConfig emergencyStopClick emergencyHoldStarted emergencyHoldReleased loadBedMeshProfile clearBedMesh setBedMeshPreviewVisible setBedMeshThresholds macroParameterDefinitions jog setJogDistance setExtrudeDistance setExtrudeSpeed home motorsOff centerToolhead zToZero extrude heatersOff firmwareRestart klipperRestart hostRestart setControlsLocked setControlsCollapsed setInfoCollapsed setStatusCollapsed setSectionLayout sectionLayoutFor setConsoleHeight setTemperatureSensorVisible setTemperatureSensorColor setShowTemperatureTargets setShowTemperaturePower sendConsoleCommand clearConsoleHistory improveEta setShowProbePoints openFileManager refreshFileManager fileNavigateTo setFileSearch setFileSort setFileManagerOpen setPositionMode setFilePageSize setFilePage setFileFilter clearFileFilters toggleFileSelection toggleFilePageSelection clearFileSelection fileLoadAllHistory fileScanMetadata fileRequestDelete fileRequestDeleteFile fileRequestDeleteDir fileCreateDirectory fileConfirmDelete fileCancelDelete fileRequestRename fileRequestRenameDir filePreviewRename fileConfirmRename fileCancelRename fileUpload fileConfirmUpload fileCancelUpload fileUploadDismiss fileClearWalkError fileRequestVisibleThumbnails".split():
            self.assertTrue(any(bytes(meta.method(i).name()).decode() == name for i in range(meta.methodCount())), name)
        self.assertEqual(type(model).__bases__[0].__name__, "PrinterModel")


