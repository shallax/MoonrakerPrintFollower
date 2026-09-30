"""Executable monitor qml contracts contracts."""
from tests import monitor_test_support as harness

class MonitorModelContractTests(harness.MonitorModelContractTests):
    def test_single_qt_model_exposes_dashboard_features(self):
        for token in ("monitorEta", "monitorFinish", "temperatureItems", "fanItems", "filamentSensorItems",
            "powerDevices", "pausePrint", "resumePrint", "cancelPrint", "excludeObject",
            "setPowerDevice", "hostLoad", "memoryAvailable", "cpuTemperature", "klipperVersion", "moonrakerVersion",
            "mcuSummary", "macroNames", "runMacro", "temperaturePresetNames", "applyTemperaturePreset",
            "homeAll", "runQuadGantryLevel", "calibrateBedMesh", "macroParameterDefinitions", "temperaturePresetItems",
            "setSpeedFactor", "setFlowFactor", "adjustZOffset", "clearZOffset", "setFanSpeed", "setLedBrightness",
            "speedFactorPercent", "flowFactorPercent", "fanControlItems", "ledItems", "zOffsetText", "canSaveConfig"):
            self.assertIn(token, harness.MONITOR_MODEL)
        self.assertIn("class MoonrakerMonitorModel(PrinterOutputModel)", harness.MONITOR_MODEL)
        self.assertNotIn("_BaseMoonrakerMonitorModel", harness.MONITOR_MODEL)

    def test_section_content_insets_pair_left_and_right(self):
        # The 4.6.0 right inset: every section
        # content column with the left inset carries the SAME
        # expression on the right — the pair pin, never one side.
        left = 'Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2'
        right = 'Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2'
        carried = []
        for path in sorted(harness.PLUGINS.rglob("*.qml")):
            text = path.read_text(encoding="utf-8")
            if left in text:
                carried.append(path.name)
                self.assertIn(right, text, path.name)

    def test_toolhead_control_surface(self):
        policy = (harness.PLUGINS / "ToolheadPolicy.py").read_text(encoding="utf-8")
        for token in ("G91", "G28", "M18", "jog_gate", "push_op", "JogOp",
                      "JOG_DISTANCE_DEFAULT", "EXTRUDE_SPEEDS_MM_PER_MIN", "extrude_distance_ok"):
            self.assertIn(token, policy)
        # Motion scripts have exactly one owner: MonitorControls gains none.
        self.assertNotIn("G91", harness.CONTROLS)
        self.assertNotIn("M18", harness.CONTROLS)
        for token in ("jogEnabled", "jogDistance", "extrudeDistance", "extrudeSpeed", "homedAxes",
                      "positionMode", "jogStatus", "toolheadChanged", "def jog(",
                      "def setJogDistance(", "def setExtrudeDistance(", "def setExtrudeSpeed(",
                      "def home(", "def motorsOff(", "def extrude(", "def heatersOff(",
                      "def centerToolhead(", "def zToZero("):
            self.assertIn(token, harness.MONITOR_MODEL)
        # The toolhead block rides its components (4.3.0; the pad and the
        # extrusion cluster became their own leaves in 4.6.2): the section
        # keeps the shell, the readouts, the homing rows and the status,
        # the pad keeps the compass, the cluster keeps the ladders. The
        # pins follow the tokens.
        for token in ("id: toolheadSection", 'title: "Toolhead"',
                      'home("x")', 'home("y")', 'home("z")', '"Motors off"',
                      'text: "Centre toolhead"', 'text: "Z to 0"',
                      "root.printerModel.monitorPosition"):
            self.assertIn(token, harness.TOOLHEAD_SECTION_QML)
        for token in ('jog("x", -1)', 'jog("z", 1)', "setJogDistance(",
                      'text: "↑ Y"', 'text: "← X"', 'text: "→ X"', 'text: "↓ Y"',
                      'text: "↑ Z"', 'text: "↓ Z"'):
            self.assertIn(token, harness.JOG_PAD_QML)
        for token in ('"Extrude"', '"Retract"', "setExtrudeDistance(", "setExtrudeSpeed("):
            self.assertIn(token, harness.EXTRUSION_CONTROLS_QML)
        for token in ('"Cooldown"', "heatersOff"):
            self.assertIn(token, harness.PROFILES_SECTION_QML)
        # The safety clause lives in the policy's copy (4.2.0): the QML
        # reads the published caption, never builds the sentence.
        self.assertIn("Toolhead moves are disabled during a print", harness.POLICY)
        # The six directional buttons carry no +/- signs (the arrows are the
        # direction) and use the PreviewSecondaryButton idiom: Cura's
        # native button underneath (hover/tooltip), a centred theme-coloured
        # label on top — Cura's own label does not vertically centre.
        # Home-all lives in the Setup section only: the toolhead section
        # keeps per-axis home buttons, so no duplicate home-all controls.
        motion_only = harness.TOOLHEAD_SECTION_QML + harness.JOG_PAD_QML + harness.EXTRUSION_CONTROLS_QML
        self.assertNotIn('home("")', motion_only)
        self.assertEqual(harness.JOG_PAD_QML.count("PreviewSecondaryButton"), 6)
        self.assertNotIn("contentItem", motion_only)
        # The Z-offset nudges carry direction glyphs, up row first, and no
        # +/- signs: the arrows carry the direction.
        self.assertIn('"↓ " + Math.abs(modelData)', harness.ZOFFSET_CONTROLS_QML)
        self.assertIn('"↑ " + modelData', harness.ZOFFSET_CONTROLS_QML)
        self.assertLess(harness.ZOFFSET_CONTROLS_QML.index("model: [0.005"), harness.ZOFFSET_CONTROLS_QML.index("model: [-0.005"))
        # The jog-gated motion controls take jogEnabled alone, never
        # actionBusy: taps must keep working while the queue drains. The
        # z-offset nudges are deliberately outside this union — they run on
        # the controls domain's own interlock.
        self.assertIn("jogEnabled", motion_only)
        self.assertNotIn("actionBusy", motion_only)
        # The compass is a 3×3 grid (9 cells) with the empty centre: the
        # four arrows must appear in north-west-east-south order so the
        # south button sits under north, never under west.
        grid = harness.JOG_PAD_QML[harness.JOG_PAD_QML.index('text: "↑ Y"'):harness.JOG_PAD_QML.index('text: "↓ Y"') + len('text: "↓ Y"')]
        positions = [grid.index(token) for token in ('text: "↑ Y"', 'text: "← X"', 'text: "→ X"', 'text: "↓ Y"')]
        self.assertEqual(positions, sorted(positions))
        compass = harness.JOG_PAD_QML[harness.JOG_PAD_QML.index('columns: 3'):harness.JOG_PAD_QML.index('ColumnLayout {', harness.JOG_PAD_QML.index('text: "↑ Y"'))]
        self.assertEqual(compass.count('PreviewSecondaryButton {'), 4)
        self.assertEqual(compass.count('Item {'), 5)

    def test_same_dashboard_chain_and_power_lock_explanation(self):
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', harness.OUTPUT_PLUGIN)
        self.assertIn('Qt.createComponent("MoonrakerMonitorDashboard.qml"', harness.BED_MESH_QML)  # the shell's async load
        self.assertIn("MoonrakerMonitor", harness.DASHBOARD_QML)
        self.assertIn("Power control is locked by Moonraker while this print is active.", harness.POWER_SECTION_QML)

    def test_output_plugin_selects_the_same_dashboard_through_one_model(self):
        self.assertIn("from ..monitor.MoonrakerMonitorModel import MoonrakerMonitorModel", harness.OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', harness.OUTPUT_PLUGIN)
        self.assertIn('Qt.createComponent("MoonrakerMonitorDashboard.qml"', harness.BED_MESH_QML)

    def test_setup_and_save_commands_have_one_policy_owner(self):
        for command in ("G28", "QUAD_GANTRY_LEVEL", "BED_MESH_CALIBRATE", "SAVE_CONFIG", "SET_GCODE_OFFSET", "SET_FAN_SPEED", "SET_LED"):
            self.assertIn(command, harness.CONTROLS)
        self.assertIn("self._commands.setup_allowed", harness.CONTROLS)
        self.assertIn("configfile.get(\"save_config_pending\")", harness.CONTROLS)

    def test_emergency_stop_requires_two_clicks_and_a_held_third_press(self):
        source = (harness.PLUGINS / "MonitorCommands.py").read_text(encoding="utf-8")
        self.assertIn("HOLD_MS = 600", source)
        self.assertIn("self._reset_timer.setInterval(1000)", source)
        self.assertIn('"printer/emergency_stop"', source)
        self.assertIn("def emergency_hold_started", source)
        self.assertIn("def emergency_hold_released", source)
        # Firing the stop clears every pending item and releases busy.
        self.assertIn("emergencyStopped.emit()", source)
        self.assertIn("self.reset()", source)
        self.assertIn("emergencyButton.clicks +", harness.DASHBOARD_QML)
        self.assertIn('"EMERGENCY STOP — press and hold to fire"', harness.DASHBOARD_QML)
        self.assertIn("EMERGENCY STOP", harness.DASHBOARD_QML)
        self.assertNotIn("Emergency stop?", harness.DASHBOARD_QML)

    def test_section_ids_are_pinned_as_the_persistence_keys(self):
        # The 22 sectionId: literals are pinned as an exact set and the
        # console pane — whose id is not a sectionId: property — by its
        # expansion reference: a renamed id or an unlisted section must
        # not slip through silently (an unknown key defaults to expanded,
        # so the pin is the only guard on the persistence vocabulary).
        # The section-id literals ride their components (4.3.0): the
        # extraction scans the hosts AND every extracted section file.
        literals = set(harness.re.findall(r'sectionId: "([^"]+)"', harness.DASHBOARD_QML + harness.MONITOR_QML + harness.PRINT_SECTION_QML + harness.SETUP_SECTION_QML + harness.TOOLHEAD_SECTION_QML + harness.MACROS_SECTION_QML + harness.PROFILES_SECTION_QML + harness.TUNING_SECTION_QML + harness.FANS_SECTION_QML + harness.LEDS_SECTION_QML + harness.PWM_SECTION_QML + harness.POWER_SECTION_QML + harness.SYSTEM_SECTION_QML + harness.SAVE_SECTION_QML + harness.FILE_MANAGER_SECTION_QML + harness.MESH_SECTION_QML + harness.TEMP_HISTORY_SECTION_QML + harness.FANS_INFO_SECTION_QML + harness.FILAMENT_SECTION_QML + harness.TEMPS_SECTION_QML + harness.SYSTEM_INFO_SECTION_QML + harness.MCUS_SECTION_QML + harness.JOB_SECTION_QML))
        self.assertEqual(literals, harness.SECTION_IDS - {"console"})
        self.assertEqual(len(harness.SECTION_IDS), 22)
        self.assertIn('sectionExpandedMap["console"]', harness.CONSOLE_PANE_QML)
        # Header width must be owned by its container. Layout-rooted
        # sections use Layout.fillWidth; the two dynamic sections use an
        # explicit Item shell to avoid nested layout feedback. Their live
        # collapse/width/content transitions run in the real-engine suite.
        for section_qml in (harness.PRINT_SECTION_QML, harness.SETUP_SECTION_QML, harness.TOOLHEAD_SECTION_QML,
                            harness.MACROS_SECTION_QML, harness.PROFILES_SECTION_QML, harness.TUNING_SECTION_QML,
                            harness.FANS_SECTION_QML, harness.LEDS_SECTION_QML, harness.PWM_SECTION_QML,
                            harness.POWER_SECTION_QML, harness.SYSTEM_SECTION_QML, harness.SAVE_SECTION_QML,
                            harness.FILE_MANAGER_SECTION_QML, harness.MESH_SECTION_QML,
                            harness.TEMP_HISTORY_SECTION_QML, harness.FANS_INFO_SECTION_QML,
                            harness.FILAMENT_SECTION_QML, harness.TEMPS_SECTION_QML,
                            harness.SYSTEM_INFO_SECTION_QML, harness.MCUS_SECTION_QML, harness.JOB_SECTION_QML):
            header = section_qml[section_qml.index("CollapsibleSectionHeader {"):
                                 section_qml.index("CollapsibleSectionHeader {") + 400]
            if section_qml in (harness.PROFILES_SECTION_QML, harness.JOB_SECTION_QML):
                self.assertIn("Item {\n    id: root", section_qml)
                self.assertIn("width: parent.width", header)
                self.assertIn("sectionBody.visible ? sectionBody.implicitHeight", section_qml)
            else:
                self.assertIn("ColumnLayout {\n    id: root\n    spacing: 0", section_qml)
                self.assertIn("Layout.fillWidth: true", header)
                self.assertNotIn("width: parent.width", header)
        # The system/mcu sections sit in the STATUS PANE, not inside
        # the chart pop-over's legend repeater (the adversarial
        # critic's misplaced-insertion catch). Objects moved to the
        # controls pane in 4.6.0 — its own ordering pin lives with
        # the dashboard pins below.
        pane = harness.STATUS_PANE_QML
        column = pane[pane.index("id: statusContent"):pane.index("StatusCollapsedReadout {")]
        system_at = column.index("SystemInfoSection {")
        mcus_at = column.index("McusSection {")
        self.assertLess(system_at, mcus_at)
        # The mesh section's refresh rides an accessor — the monitor's
        # handler calls it through the instantiation id, never the
        # component's own id (the dangling-id fix).
        self.assertIn("function refreshMap()", harness.MESH_SECTION_QML)
        self.assertIn("meshSection.refreshMap()", harness.INFO_PANE_QML)

    def test_the_narrow_window_rule_hinges_on_the_camera_pane(self):
        # The frozen contract (INSTRUCTIONS.md, "Standing UI rules"): the
        # narrow-window rule reads the WEBCAM pane's own width — never the
        # stage's — and refuses an expansion the camera could not survive.
        # Whitespace is flattened before matching: qmlformat owns the
        # wrapping, the pins own the arithmetic.
        flat = harness.re.sub(r"\s+", " ", harness.MONITOR_QML)
        self.assertIn("The narrow-window collapse/lock contract — see INSTRUCTIONS.md", flat)
        self.assertIn("readonly property real cameraViewportWidth: cameraPane.viewportWidth", flat)
        squeeze = harness.re.search(
            r'webcamSqueezed: ([A-Za-z0-9_.]+) > 0 && \1 < ([0-9]+) \* screenScaleFactor',
            flat,
        )
        self.assertIsNotNone(squeeze, "the squeeze threshold changed shape")
        self.assertEqual((squeeze.group(1), int(squeeze.group(2))),
                         ("root.cameraViewportWidth", 220),
                         "the squeeze must read the camera at its comfort minimum")
        # The expansion costs: each pane's expanded width less the
        # collapsed strip it replaces — the same strip on both panes.
        costs = harness.re.findall(
            r'readonly property real (info|status)ExpandCost: \(([0-9]+) - ([0-9]+)\) \* screenScaleFactor',
            flat,
        )
        self.assertEqual([(name, int(wide) - int(strip)) for name, wide, strip in costs],
                         [("info", 226), ("status", 366)],
                         "an expansion cost changed")
        self.assertEqual(len({strip for _, _, strip in costs}), 1,
                         "the two panes must share the collapsed strip's width")
        # The forward check: the pane's own cost against the comfort
        # minimum, on the camera as it stands.
        for pane, cost in (("info", "infoExpandCost"), ("status", "statusExpandCost")):
            forward = harness.re.search(
                r'readonly property bool %sExpandBlocked: root\.cameraViewportWidth - root\.%s '
                r'< ([0-9]+) \* screenScaleFactor' % (pane, cost),
                flat,
            )
            self.assertIsNotNone(forward, "the %s forward check changed shape" % pane)
            self.assertEqual(int(forward.group(1)), 220,
                             "the %s forward check left the comfort minimum" % pane)
        # The open-width measures: the camera seen with the pane's own
        # auto fold credited back out, so no decision depends on how many
        # layout passes the fold needed — and the release is the forward
        # check read backwards.
        for pane, flag in (("info", "infoAutoCollapsed"), ("status", "statusAutoCollapsed")):
            measure = harness.re.search(
                r'readonly property real %sOpenWidth: root\.cameraViewportWidth - \(root\.%s && '
                r'!root\.%sPersistedCollapsed \? root\.%sExpandCost : 0\)' % (pane, flag, pane, pane),
                flat,
            )
            self.assertIsNotNone(measure, "the %s open-width measure changed shape" % pane)
        # The fold-landed gate: the pane's own width is where the fold
        # LANDS, and where the camera is pinned at its floor it is the
        # only signal there is.
        self.assertIn(
            "readonly property bool infoFoldLanded: infoPanel.width < 200 * screenScaleFactor", flat)
        # The LIFO guard and the cascade gate: the information pane folds
        # under comfort and reclaims its room LAST — while the status
        # pane's fold stands it holds, because reopening it would spend
        # the room that fold is holding; the status pane folds only once
        # the information pane's fold has landed. The old
        # simultaneous-release form (both flags dropped in the same
        # evaluation) is pinned out: it landed the pair back on both
        # folded wherever one pane alone would have fit.
        self.assertIn(
            "root.infoAutoCollapsed = infoOpen < comfort || statusWasFolded;",
            flat,
        )
        self.assertNotIn("statusWasFolded && statusOpen < comfort", flat,
                         "the information pane releases with the status pane's fold again")
        self.assertIn(
            "root.statusAutoCollapsed = statusOpen < comfort && root.infoFoldLanded "
            "&& (infoWasCollapsed || statusWasFolded);", flat)
        # One evaluation, driven by the camera and by each pane's own
        # width, and every measure is read before either flag moves: the
        # cascade's order is the flags as they stand, never a value this
        # evaluation is about to write.
        self.assertIn("onCameraViewportWidthChanged: root.applyNarrowWindowRules()", flat)
        self.assertEqual(flat.count("onWidthChanged: root.applyNarrowWindowRules()"), 3,
                         "the rule must run on the stage and on both panes' own widths")
        self.assertLess(flat.index("var statusOpen = root.statusOpenWidth"),
                        flat.index("root.infoAutoCollapsed = infoOpen"),
                        "the measures must be read before either flag moves")
        self.assertLess(flat.index("root.infoAutoCollapsed = infoOpen"),
                        flat.index("root.statusAutoCollapsed = statusOpen"),
                        "the cascade must fold the information pane first")
        # The retired latch: the stage-width constants and the single
        # camera measure they shared are gone, and so is the squeeze-edge
        # handler the camera-hinged rule replaced.
        for retired in ("infoComfortWidth", "statusComfortWidth", "cameraOpenWidth",
                        "onWebcamSqueezedChanged"):
            self.assertNotIn(retired, flat, "%s outlived the camera-hinged rule" % retired)

    def test_the_costs_ride_the_panes_own_layout_widths(self):
        # The costs are derived, not free-standing: each is a pane's
        # expanded width less its collapsed strip, and the pane layout
        # carries both numbers. A width changed on either side has to be
        # reflected in the cost in the same pass, or the camera
        # arithmetic drifts from the layout it is deciding about — and
        # the status pane's cost must stay the larger one, or the fold
        # order (and with it the reclaim-last guard) is inverted.
        flat = harness.re.sub(r"\s+", " ", harness.MONITOR_QML)
        panes = {"info": harness.INFO_PANE_QML, "status": harness.STATUS_PANE_QML}
        expanded = {}
        for pane, source in panes.items():
            # The widths are the pane's own now; the cost is the host's
            # arithmetic about them.
            layout = harness.re.search(
                r'objectName: "%sPanel".*?Layout\.preferredWidth: \(root\.%sCollapsed \? '
                r'%sCollapseButton\.width \+ 2 \* UM\.Theme\.getSize\("thin_margin"\)\.width : '
                r'([0-9]+) \* screenScaleFactor\).*?Layout\.minimumWidth: \(root\.%sCollapsed \? '
                r'%sCollapseButton\.width \+ 2 \* UM\.Theme\.getSize\("thin_margin"\)\.width : '
                r'([0-9]+) \* screenScaleFactor\)' % (pane, pane, pane, pane, pane),
                harness.re.sub(r"\s+", " ", source),
            )
            self.assertIsNotNone(layout, "the %s pane's layout widths changed shape" % pane)
            cost = harness.re.search(
                r'readonly property real %sExpandCost: \(([0-9]+) - ([0-9]+)\) \* screenScaleFactor'
                % pane, flat)
            self.assertIsNotNone(cost, "the %s expansion cost changed shape" % pane)
            self.assertEqual((int(cost.group(1)), int(cost.group(2))),
                             (int(layout.group(1)), 44),
                             "the %s cost stopped riding the pane's own widths" % pane)
            self.assertLess(int(layout.group(2)), int(layout.group(1)),
                            "the %s pane's minimum must stay under its expanded width" % pane)
            expanded[pane] = int(layout.group(1))
        self.assertGreater(expanded["status"], expanded["info"],
                           "the status pane must fold after the information pane")

    def test_every_expand_path_refuses_while_the_camera_has_no_room(self):
        # The narrow-window lock: a collapsed pane refuses on the fold's
        # own reasons (the auto collapse, a camera already under its
        # comfort) and on the forward check — all three named in the
        # expression — and every expand path carries the guard with the
        # tooltip that explains the refusal (the console's too-narrow
        # precedent).
        panes = {"info": harness.INFO_PANE_QML, "status": harness.STATUS_PANE_QML}
        flat = harness.re.sub(r"\s+", " ", harness.MONITOR_QML)
        for lock, pane in (("infoExpandLocked", "info"), ("statusExpandLocked", "status")):
            lock_line = harness.re.search(
                r'readonly property bool %s: root\.%sCollapsed && \(root\.%sAutoCollapsed \|\| '
                r'root\.webcamSqueezed \|\| root\.%sExpandBlocked\)' % (lock, pane, pane, pane),
                flat,
            )
            self.assertIsNotNone(lock_line, "the %s lock changed shape" % pane)
            # The lock is the host's; the guards and the refusal's
            # wording ride the pane that owns the strip and the toggle.
            pane_flat = harness.re.sub(r"\s+", " ", panes[pane])
            self.assertGreaterEqual(pane_flat.count("if (root.%s) {" % lock), 2,
                                    "the %s pane needs the guard on its strip and its toggle" % pane)
        self.assertIn("The window is too narrow — widen it to show the information.",
                      harness.re.sub(r"\s+", " ", harness.INFO_PANE_QML))
        self.assertIn("The window is too narrow — widen it to show the printer status.",
                      harness.re.sub(r"\s+", " ", harness.STATUS_PANE_QML))
        # The dashboard's controls pane follows the same rule through the
        # loaded monitor document's camera: its own cost and forward
        # check, the same guard on both expand paths, and the same
        # widen-first message. It carries no auto fold of its own, so the
        # user's collapse is the only state the lock ever meets.
        dash = harness.re.sub(r"\s+", " ", harness.DASHBOARD_QML)
        self.assertIn("readonly property real cameraViewportWidth: baseMonitorLoader.item !== null ? "
                      "baseMonitorLoader.item.cameraViewportWidth : 0", dash)
        self.assertIn("property bool controlsCollapsed: root.printer != null ? "
                      "root.printer.controlsCollapsed : false", dash)
        controls_cost = harness.re.search(
            r'readonly property real controlsExpandCost: \(([0-9]+) - ([0-9]+)\) \* screenScaleFactor',
            dash,
        )
        self.assertIsNotNone(controls_cost, "the controls expansion cost changed shape")
        self.assertEqual(int(controls_cost.group(1)) - int(controls_cost.group(2)), 342,
                         "the controls expansion cost changed")
        self.assertIn("readonly property bool controlsExpandBlocked: root.cameraViewportWidth > 0 && "
                      "root.cameraViewportWidth - root.controlsExpandCost < 220 * screenScaleFactor", dash)
        self.assertIn("readonly property bool webcamSqueezed: root.cameraViewportWidth > 0 && "
                      "root.cameraViewportWidth < 220 * screenScaleFactor", dash)
        self.assertIn("readonly property bool controlsExpandLocked: root.controlsCollapsed && "
                      "(root.webcamSqueezed || root.controlsExpandBlocked)", dash)
        self.assertGreaterEqual(dash.count("if (root.controlsExpandLocked) {"), 2,
                                "the controls pane needs the guard on its strip and its toggle")
        self.assertIn("The window is too narrow — widen it to show the printer controls.", dash)

    def test_controls_live_in_the_collapsible_column_and_the_left_is_read_only(self):
        dialogs = {name: (harness.PLUGINS / (name + ".qml")).read_text(encoding="utf-8")
                   for name in ("PrintConfirmDialog", "DeleteConfirmDialog", "CreateFolderDialog", "RenameDialog", "UploadConfirmDialog", "UploadProgressDialog", "DownloadProgressDialog")}
        for name in dialogs:
            self.assertIn(name + " {", harness.FILE_MANAGER_QML)
            self.assertIn("property var printerModel: null", dialogs[name])
        # The browser's extracted leaves: each owns one visual region of
        # the popup, so the pins below follow the code into the document
        # that now holds it. Mounting stays pinned in the shell, except
        # where the mount itself moved one level down.
        leaves = {name: (harness.PLUGINS / (name + ".qml")).read_text(encoding="utf-8")
                  for name in ("FileManagerRecents", "FileManagerToolbar", "FileDirectoryStrip", "FileManagerSearch", "FileManagerFilters", "FileFilterOptionRow", "FileColumnChooser", "FileGrid", "FileManagerPagination")}
        for name in ("FileManagerRecents", "FileManagerToolbar", "FileDirectoryStrip", "FileManagerSearch", "FileManagerFilters", "FileGrid", "FileManagerPagination"):
            self.assertIn(name + " {", harness.FILE_MANAGER_QML)
        # The shell mounts the table, the table mounts the chooser: the
        # popup sits under the header cell that opens it.
        self.assertIn("FileGrid {", harness.FILE_MANAGER_QML)
        self.assertIn("FileColumnChooser {", leaves["FileGrid"])
        # The option row is the filters leaf's delegate, never a child
        # of the shell.
        self.assertIn("FileFilterOptionRow {", leaves["FileManagerFilters"])
        # The left panel carries no printer commands: only the camera list,
        # the read-outs and view configuration remain there.
        self.assertNotIn("root.printer.pausePrint", harness.MONITOR_QML)
        self.assertNotIn("root.printer.setPowerDevice", harness.MONITOR_QML)
        self.assertIn("root.printer.emergencyStopClick", harness.DASHBOARD_QML)  # the one permitted command
        # The information pane sits left of the webcam with the mesh map.
        self.assertIn("id: infoPanel", harness.MONITOR_QML)
        # The mesh section hosts the mini map; a click opens the shared
        # pop-over. The button is gone; the mini map's tooltip remains.
        self.assertIn('root.printerModel.bedMeshAvailable', harness.MESH_SECTION_QML)
        self.assertIn('root.printerModel.bedMeshRangeText !== ""', harness.MESH_SECTION_QML)
        self.assertIn('"No bed mesh loaded."', harness.MESH_SECTION_QML)
        self.assertNotIn('id: mapButton', harness.MONITOR_QML)
        # Cura-style collapsible sections, persisted per section, sharing
        # the CollapsibleSectionHeader type across all three panes. The
        # sections ride their components (4.3.0) — the expansion map is
        # read by every one of them.
        self.assertIn("sectionExpandedMap", harness.PRINT_SECTION_QML + harness.SETUP_SECTION_QML + harness.TOOLHEAD_SECTION_QML + harness.MACROS_SECTION_QML + harness.PROFILES_SECTION_QML + harness.TUNING_SECTION_QML + harness.FANS_SECTION_QML + harness.LEDS_SECTION_QML + harness.PWM_SECTION_QML + harness.POWER_SECTION_QML + harness.SYSTEM_SECTION_QML + harness.SAVE_SECTION_QML + harness.FILE_MANAGER_SECTION_QML)
        self.assertIn("setSectionExpanded", harness.MONITOR_MODEL)
        self.assertIn('sectionId: "toolhead"', harness.TOOLHEAD_SECTION_QML)
        # Direct instantiations must ASSIGN the type's properties: the old
        # Loader syntax ('property string sectionId: ...') declares a local
        # property instead, which silently un-wires every header.
        self.assertNotIn("property string sectionId:", harness.DASHBOARD_QML)
        self.assertNotIn("property string title:", harness.DASHBOARD_QML)
        self.assertNotIn("property string sectionIcon:", harness.DASHBOARD_QML)
        self.assertIn('sectionIcon: "Nozzle"', harness.TOOLHEAD_SECTION_QML)
        self.assertIn('sectionIcon: "Printer"', harness.PRINT_SECTION_QML)
        self.assertIn('sectionId: "meshmap"', harness.MESH_SECTION_QML)
        self.assertIn('sectionId: "systeminfo"', harness.SYSTEM_INFO_SECTION_QML)
        # Plugin-drawn glyphs feed the header through a url, and the
        # frontend launcher lives in the Printer status title row.
        self.assertIn('sectionIcon: "Fan"', harness.FANS_INFO_SECTION_QML)
        self.assertIn('sectionIconUrl: Qt.resolvedUrl("../../resources/svg/Thermometer.svg")', harness.TEMP_HISTORY_SECTION_QML)
        self.assertIn('Qt.resolvedUrl("../resources/svg/Download.svg")', harness.JOB_SECTION_QML)
        self.assertIn('sectionIconUrl: Qt.resolvedUrl("../../resources/svg/Power.svg")', harness.POWER_SECTION_QML)
        # The Position row's axis-coloured cells (the 4.5.0 ruling):
        # three fixed cells in the axis tokens, no-wrap — the row
        # must never reflow per poll (the status stack's polish-loop
        # class), and the cells carry objectNames so a future
        # scenario can assert the colour mapping.
        for axis in ("X", "Y", "Z"):
            self.assertIn(f'objectName: "jobPositionCell{axis}"', harness.JOB_SECTION_QML)
            self.assertIn(f"MoonrakerTheme.axis{axis}", harness.JOB_SECTION_QML)
        self.assertEqual(harness.JOB_SECTION_QML.count("wrapMode: Text.WordWrap"), 0)
        self.assertIn('text: "Open the Moonraker frontend."', harness.STATUS_PANE_QML)
        # The tooltip discipline (the live ruling): every tooltip is a
        # UM.ToolTip child in the Cura placement pattern (below the
        # control, arrow at its top-centre, hover-driven) — never the
        # native tooltip property or a TooltipArea over a control,
        # either of which can swallow a click when the pointer crosses
        # the popup.
        for path in harness.PLUGINS.rglob("*.qml"):
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if line.lstrip().startswith("tooltip:"):
                    self.fail("%s carries a native tooltip property: %s"
                              % (path.name, line.strip()[:60]))
        for path in harness.PLUGINS.rglob("*.qml"):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"onClicked[\s\S]{0,600}?UM\.TooltipArea",
                "%s holds a TooltipArea inside a clickable control" % path.name,
            )
        self.assertNotIn('text: "Open Moonraker frontend"', harness.MONITOR_QML)
        # The section machinery (4.3.0): per-file counts PLUS the
        # totals — a moved section decrements one file and increments
        # another, and the totals catch a dropped section that a
        # per-file pin alone would read as "moved".
        self.assertEqual(harness.DASHBOARD_QML.count("CollapsibleSectionHeader"), 0)
        # Every extracted section is a SIBLING instantiation in the
        # pane — a section nested inside another's instantiation is
        # valid QML and loads, but renders inside the wrong Column.
        self.assertIn("                        }\n                        SaveSection {", harness.DASHBOARD_QML)
        self.assertEqual(harness.PRINT_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.SETUP_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.TOOLHEAD_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.MACROS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.PROFILES_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.TUNING_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.FANS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.LEDS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.PWM_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.POWER_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.SYSTEM_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.SAVE_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.FILE_MANAGER_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.MESH_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.TEMP_HISTORY_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.FANS_INFO_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.FILAMENT_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.TEMPS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.SYSTEM_INFO_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.MCUS_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.JOB_SECTION_QML.count("CollapsibleSectionHeader"), 1)
        self.assertEqual(harness.MONITOR_QML.count("CollapsibleSectionHeader"), 0)
        self.assertEqual(harness.DASHBOARD_QML.count("CollapsibleSectionHeader")
                         + harness.PRINT_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.SETUP_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.TOOLHEAD_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.MACROS_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.PROFILES_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.TUNING_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.FANS_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.LEDS_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.PWM_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.POWER_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.SYSTEM_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.SAVE_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.FILE_MANAGER_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.MESH_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.TEMP_HISTORY_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.FANS_INFO_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.FILAMENT_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.TEMPS_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.SYSTEM_INFO_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.MCUS_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.JOB_SECTION_QML.count("CollapsibleSectionHeader")
                         + harness.MONITOR_QML.count("CollapsibleSectionHeader"), 21)
        self.assertEqual(harness.DASHBOARD_QML.count('sectionIcon: "'), 0)
        self.assertEqual(harness.PRINT_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.SETUP_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.TOOLHEAD_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.MACROS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.PROFILES_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.TUNING_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.FANS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.LEDS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.PWM_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.POWER_SECTION_QML.count('sectionIcon: "'), 0)  # Power uses the plugin glyph url
        self.assertEqual(harness.SYSTEM_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.SAVE_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.FILE_MANAGER_SECTION_QML.count('sectionIcon: "'), 0)  # The plugin glyph url
        self.assertEqual(harness.MESH_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.TEMP_HISTORY_SECTION_QML.count('sectionIcon: "'), 0)  # The plugin glyph url
        self.assertEqual(harness.FANS_INFO_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.FILAMENT_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.TEMPS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.SYSTEM_INFO_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.MCUS_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.JOB_SECTION_QML.count('sectionIcon: "'), 1)
        self.assertEqual(harness.MONITOR_QML.count('sectionIcon: "'), 0)
        self.assertEqual(harness.DASHBOARD_QML.count('sectionIcon: "')
                         + harness.PRINT_SECTION_QML.count('sectionIcon: "')
                         + harness.SETUP_SECTION_QML.count('sectionIcon: "')
                         + harness.TOOLHEAD_SECTION_QML.count('sectionIcon: "')
                         + harness.MACROS_SECTION_QML.count('sectionIcon: "')
                         + harness.PROFILES_SECTION_QML.count('sectionIcon: "')
                         + harness.TUNING_SECTION_QML.count('sectionIcon: "')
                         + harness.FANS_SECTION_QML.count('sectionIcon: "')
                         + harness.LEDS_SECTION_QML.count('sectionIcon: "')
                         + harness.PWM_SECTION_QML.count('sectionIcon: "')
                         + harness.POWER_SECTION_QML.count('sectionIcon: "')
                         + harness.SYSTEM_SECTION_QML.count('sectionIcon: "')
                         + harness.SAVE_SECTION_QML.count('sectionIcon: "')
                         + harness.MESH_SECTION_QML.count('sectionIcon: "')
                         + harness.FANS_INFO_SECTION_QML.count('sectionIcon: "')
                         + harness.FILAMENT_SECTION_QML.count('sectionIcon: "')
                         + harness.TEMPS_SECTION_QML.count('sectionIcon: "')
                         + harness.SYSTEM_INFO_SECTION_QML.count('sectionIcon: "')
                         + harness.MCUS_SECTION_QML.count('sectionIcon: "')
                         + harness.JOB_SECTION_QML.count('sectionIcon: "')
                         + harness.MONITOR_QML.count('sectionIcon: "'), 18)
        # The File manager section (Snapshot 0) leads the controls pane
        # and opens the popup; it uses the plugin glyph, so the
        # sectionIcon: count is unchanged.
        self.assertIn('sectionId: "fileManager"', harness.FILE_MANAGER_SECTION_QML)
        self.assertIn('text: "File manager"', harness.FILE_MANAGER_SECTION_QML)
        self.assertIn("fileManagerOpen", harness.DASHBOARD_QML)
        self.assertIn("FileManager 1.0 files/browser/FileManager.qml", harness.QMLDIR)
        # Opening the popup must trigger the walk (the Snapshot 1
        # live-test regression: the button flipped the flag but
        # nothing fetched, and the grid sat on "Loading files…").
        self.assertIn("onOpenChanged", harness.FILE_MANAGER_QML)
        self.assertIn("openFileManager()", harness.FILE_MANAGER_QML)
        # The live-test rulings: the 250 ms search settle,
        # the refresh button, the circled search clear, folders as a
        # strip (never in the metadata list).
        self.assertIn("interval: 250", leaves["FileManagerSearch"])
        self.assertIn("refreshFileManager()", harness.FILE_MANAGER_QML)
        self.assertIn('text: "⟳"', leaves["FileManagerToolbar"])
        self.assertIn("restoreMode: Binding.RestoreBinding", leaves["FileManagerSearch"])
        self.assertIn("id: searchClear", leaves["FileManagerSearch"])
        self.assertIn("activeDirectories", harness.FILE_MANAGER_QML)
        self.assertIn('UM.Theme.getIcon("Folder")', leaves["FileDirectoryStrip"])
        self.assertIn("filterOptionRow", leaves["FileManagerFilters"])
        # Probe-proven engine traps: a Repeater with two bare
        # children keeps only the last as its delegate, and
        # Component ids must never be reached through an object
        # reference (a Loader's sourceComponent silently loads
        # nothing) — both were live reports.
        self.assertIn('text: " / "', leaves["FileManagerToolbar"])
        # The filter dropdowns (the live rulings): radios
        # for Modified/Print time (single-value model semantics —
        # the engine's exclusive group only unchecks visually), no
        # auto-dismiss on selection (Qt menus close on item
        # activation regardless of closePolicy, so the dropdowns are
        # Popups — probe-proven), the dropdown below its button, the
        # faces toggled by visibility (a Loader swap would destroy
        # the open dropdown), and the up-directory chip in the
        # folder strip.
        self.assertIn("setFilterValue", harness.FILE_MANAGER_QML)
        self.assertIn("modelData.radio", leaves["FileManagerFilters"])
        # The toggle dropdowns close on RELEASE outside (the press
        # still opens state capture — a press-outside policy closed
        # before the opener could record it and every dismissal click
        # re-opened the popup); the dialogs close on Escape only.
        self.assertIn("closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside", leaves["FileManagerFilters"])
        self.assertIn("closePolicy: Popup.CloseOnEscape\n", dialogs['PrintConfirmDialog'])
        self.assertIn("y: parent.height", leaves["FileManagerFilters"])
        self.assertIn("visible: !root.filterActive(\"slicer\")", leaves["FileManagerFilters"])
        self.assertIn('text: ".."', leaves["FileDirectoryStrip"])
        self.assertIn('text: "<root>"', leaves["FileManagerToolbar"])
        # Snapshot 2 live refinements: double-click-to-print, the
        # themed confirmation background.
        self.assertIn("onDoubleClicked", leaves["FileGrid"])
        # The row reports the relpath; the shell keeps the model write.
        self.assertIn("root.printRequested(modelData.relpath)", leaves["FileGrid"])
        self.assertIn("root.printerModel.fileRequestPrint(relpath)", harness.FILE_MANAGER_QML)
        self.assertIn('id: printConfirmDialog', harness.FILE_MANAGER_QML)
        # The Columns menu's themed surface rides the chooser now. The
        # popup stays a Popup in its own document — the dashboard ladder
        # still opens and closes it through the shell's instance id.
        self.assertIn('background: Rectangle', leaves["FileColumnChooser"])
        self.assertIn('objectName: "columnsPopup"', leaves["FileColumnChooser"])
        self.assertIn("closePolicy: Popup.CloseOnPressOutside\n", leaves["FileColumnChooser"])
        # The confirmation's large thumbnail and the metadata-scan
        # gate (the live reports: the dialog's thumbnail
        # request, and a scan entry offered where it cannot work).
        self.assertIn('id: confirmThumb', dialogs['PrintConfirmDialog'])
        # The dialog reads the LARGE variant (the list cells use the
        # small one) and the thumbnail Images decode off the UI
        # thread.
        self.assertIn("thumbUrlLarge(root.confirmRelpath())", dialogs['PrintConfirmDialog'])
        self.assertIn("asynchronous: true", leaves["FileGrid"])
        # The dialogs own their Esc: a popup-held focus swallows the
        # key into the overlay (live-proven), so the content FocusScope
        # answers it.
        self.assertIn("focus: false", dialogs['PrintConfirmDialog'])
        # Esc CANCELS, not just closes: the payload must not survive
        # the dismissal (a dismissed confirmation used to resurrect).
        # Pinned INSIDE the Esc handlers — a bare substring would
        # also match the dialogs' Cancel buttons (the adversarial
        # round's pin-strength point).
        for name, cancel in (("PrintConfirmDialog", "fileCancelPrint"),
                             ("DeleteConfirmDialog", "fileCancelDelete"),
                             ("RenameDialog", "fileCancelRename"),
                             ("UploadConfirmDialog", "fileCancelUpload")):
            self.assertRegex(dialogs[name], r"Keys.onEscapePressed:\s*\{\s*root.close\(\);\s*if \(root.printerModel != null\) \{\s*root.printerModel\." + cancel + r"\(\);")
        self.assertIn("onOpened: printConfirmDialogFocus.forceActiveFocus()", dialogs['PrintConfirmDialog'])
        # Closing the popup closes its dialogs (a surviving dialog
        # stays painted over the dashboard with dead buttons — the
        # adversarial round's live repro).
        self.assertIn('''    Connections {
        target: root
        function onOpenChanged() {
            if (!root.open) {
                printConfirmDialog.close();''', harness.FILE_MANAGER_QML)
        # The walk-error banner's dismiss (the live ruling:
        # it overlays the first row, so it must be closable).
        self.assertIn('text: "✕"', leaves["FileGrid"])
        # The banner reports; the shell keeps the model write.
        self.assertIn("root.walkErrorCleared()", leaves["FileGrid"])
        self.assertIn("root.printerModel.fileClearWalkError()", harness.FILE_MANAGER_QML)
        # The New-folder dialog (the live request).
        self.assertIn("id: createFolderDialog", harness.FILE_MANAGER_QML)
        self.assertIn('text: "New folder…"', harness.FILE_MANAGER_QML)
        # The left columns are FROZEN (the live ruling);
        # the trailing half slides inside a clip wrapper at the
        # frozen edge, and the header mirrors it: sticky frozen,
        # the flick following the strip's contentX. A horizontal
        # wheel anywhere over the grid scrolls the strip (the wheel
        # used to work only over the scrollbar). The table owns the
        # scroll strips now, so these pins ride the grid document.
        self.assertIn("contentWidth: root.stickyWidth + root.trailingWidth", leaves["FileGrid"])
        self.assertIn("ScrollBar.horizontal: ScrollBar {", leaves["FileGrid"])
        self.assertIn('''                x: root.stickyWidth
                width: root.trailingWidth
                height: root.rowHeight
                clip: true''', leaves["FileGrid"])
        self.assertIn("x: -gridHorizontal.contentX", leaves["FileGrid"])
        self.assertIn("contentX: gridHorizontal.contentX", leaves["FileGrid"])
        self.assertIn("WheelHandler {", leaves["FileGrid"])
        self.assertIn("orientation: Qt.Horizontal", leaves["FileGrid"])
        self.assertIn("wheel.angleDelta.x", leaves["FileGrid"])
        # Search shows the folder breadcrumb under the name (the
        # live request — same-named files in different
        # folders must be tellable).
        self.assertIn('visible: root.printerModel != null && root.printerModel.fileManagerSearch.length > 0 && modelData.folder !== ""', leaves["FileGrid"])
        # The title floors hold from the first frame (static seed) —
        # headers never elide, wrap or overflow.
        self.assertIn('"thumb": 70', leaves["FileGrid"])
        # The content cells elide through a width cap (an uncapped
        # label keeps its implicit width and overflows).
        self.assertIn('width: Math.min(implicitWidth, parent.width - (modelData[0] === "Status"', leaves["FileGrid"])
        self.assertIn("root.rowNeedsMetadata(modelData)", leaves["FileGrid"])
        # Snapshot 3 mutations: the delete confirmation, the rename
        # dialog with its live collision line, and the wiring.
        self.assertIn('id: deleteConfirmDialog', harness.FILE_MANAGER_QML)
        self.assertIn('id: renameDialog', harness.FILE_MANAGER_QML)
        # The page strip reports the bulk delete; the shell writes the
        # model and the confirmation opens from its publish. The
        # page-size menu rides the strip.
        self.assertIn("root.bulkDeleteRequested()", leaves["FileManagerPagination"])
        self.assertIn('objectName: "pageSizePopup"', leaves["FileManagerPagination"])
        self.assertIn("fileRequestDelete()", harness.FILE_MANAGER_QML)
        # The row reports the relpath; the shell keeps the model write.
        self.assertIn("root.deleteRequested(modelData.relpath)", leaves["FileGrid"])
        self.assertIn("root.printerModel.fileRequestDeleteFile(relpath)", harness.FILE_MANAGER_QML)
        self.assertIn("root.renameRequested(modelData.relpath)", leaves["FileGrid"])
        self.assertIn("root.printerModel.fileRequestRename(relpath)", harness.FILE_MANAGER_QML)
        # The dialogs are modal over the manager and the rename field
        # pre-selects the stem (the live reports).
        self.assertEqual(sum(source.count("modal: true") for source in dialogs.values()), 7)
        self.assertIn("renameField.select(0, root.renameStemLength(target.name))", dialogs['RenameDialog'])
        # The helper the open handler calls must be DEFINED — a
        # ReferenceError inside onOpened only fires on open, which
        # the engine gate (closed popovers) cannot see; the missing
        # helper was the live "no pre-populated name" report.
        self.assertIn("function renameTarget()", dialogs['RenameDialog'])
        self.assertIn('palette.highlight: UM.Theme.getColor("primary")', dialogs['CreateFolderDialog'])
        # The field takes focus on open and Return confirms (the
        # live requests).
        self.assertIn("renameField.forceActiveFocus()", dialogs['RenameDialog'])
        self.assertIn("Keys.onReturnPressed: root.confirmRename()", dialogs['RenameDialog'])
        # Tab-focus cues: the field's outline flips blue on focus and
        # the six popup buttons take tab focus (the live
        # report — no cue while tabbing).
        self.assertIn('border.color: renameField.activeFocus ? UM.Theme.getColor("primary")', dialogs['RenameDialog'])
        self.assertEqual(sum(source.count("focusPolicy: Qt.StrongFocus") for source in dialogs.values()), 9)
        # Snapshot 3 finish: the upload affordance, the local-file
        # picker, and the folder context menu.
        self.assertIn('text: "Upload file…"', harness.FILE_MANAGER_QML)
        self.assertIn('id: filePicker', harness.FILE_MANAGER_QML)
        self.assertIn('id: dirActionsMenu', harness.FILE_MANAGER_QML)
        self.assertIn("fileRequestRenameDir(dirActionsMenu.dirPath)", harness.FILE_MANAGER_QML)
        self.assertIn("fileRequestDeleteDir(dirActionsMenu.dirPath)", harness.FILE_MANAGER_QML)
        self.assertIn('id: uploadProgressDialog', harness.FILE_MANAGER_QML)
        self.assertIn("root.uploadProgressState() === \"uploading\"", dialogs['UploadProgressDialog'])
        # The recents strip: a scrolling 50, no dismissal glyph, and
        # the strip's own thumbnails (the live requests).
        self.assertIn('id: recentsScroller', leaves["FileManagerRecents"])
        self.assertIn('id: recentsThumb', leaves["FileManagerRecents"])
        for name, source in [("FileManager", harness.FILE_MANAGER_QML)] + sorted(leaves.items()):
            self.assertNotIn('fileHideRecent', source, name)
            self.assertNotIn('text: "×"', source, name)
        # The console grab bar hides under the auto-collapse width
        # (the live request).
        self.assertIn("visible: !consolePanel.tooNarrow", harness.CONSOLE_PANE_QML)
        # Esc on the Monitor page leaves the stage (the
        # live request): Preview when sliced, Prepare otherwise. The
        # popover and chart close on the same key first. The ONE
        # ladder lives in the DASHBOARD document now — it hosts every
        # layer, so no other claimant can fire the stage-exit branch
        # from under a popup it cannot see.
        self.assertIn("leaveMonitorStage", harness.DASHBOARD_QML)
        # The console error bell (the live request): a red
        # bell beside the Console header while collapsed until
        # expanded.
        self.assertIn("consoleErrorBell", harness.CONSOLE_PANE_QML)
        self.assertIn('Qt.resolvedUrl("../../resources/svg/Bell.svg")', harness.CONSOLE_PANE_QML)
        self.assertIn("consoleErrorBell", harness.MONITOR_MODEL)
        # The extrude distance/speed rows keep their selection
        # highlighted (the live report).
        self.assertIn("extrudeDistance === 5", harness.EXTRUSION_CONTROLS_QML)
        self.assertIn("extrudeSpeed === 1500", harness.EXTRUSION_CONTROLS_QML)
        # The abs/rel toggle (the live request) and the
        # dropped 15 mm distance button.
        self.assertIn("setPositionMode", harness.TOOLHEAD_SECTION_QML)
        self.assertNotIn('"15"', harness.DASHBOARD_QML)
        # The mode text is the toggle control (the live
        # ruling), now on its own "Moves" row under the Position
        # readout (the 2026-09-17 ruling), and the Move distance
        # combo restores the persisted selection.
        self.assertIn('text: "Moves"', harness.TOOLHEAD_SECTION_QML)
        self.assertIn("jogPresets.indexOf", harness.JOG_PAD_QML)
        # Filament state is colour-coded: green detected, orange runout.
        self.assertIn("MoonrakerTheme.filamentDetected", harness.FILAMENT_SECTION_QML)
        self.assertIn("MoonrakerTheme.warningOrange", harness.FILAMENT_SECTION_QML)
        self.assertNotIn('id: powerOffDialog', harness.MONITOR_QML)
        self.assertNotIn('id: cancelPrintDialog', harness.MONITOR_QML)
        # The right column hosts the print actions, power and the lock.
        # The print actions ride their component (4.3.0).
        for token in ('text: "Pause"', 'text: "Resume"', 'text: "Cancel"', "cancelRequested()"):
            self.assertIn(token, harness.PRINT_SECTION_QML)
        self.assertIn('title: "Power"', harness.POWER_SECTION_QML)
        self.assertIn("onPowerOffConfirmRequested", harness.DASHBOARD_QML)
        self.assertIn("powerOffConfirmRequested(modelData.name)", harness.POWER_SECTION_QML)
        self.assertIn("property bool anyPowerLocked", harness.POWER_SECTION_QML)
        self.assertNotIn("anyPowerLocked", harness.DASHBOARD_QML)
        for token in ("powerOffDialog.open()", "controlsCollapsed",
                      '"Lock all controls."', '"Unlock all controls."', "PadlockLocked.svg", "PadlockUnlocked.svg",
                      "setControlsLocked", "setControlsCollapsed"):
            self.assertIn(token, harness.DASHBOARD_QML)
        # The collapsed strip's vertical title rides its own leaf (4.6.2),
        # which owns the readout row, its gates and the pane fit.
        for token in ("id: collapsedTitle", "rotation: 90",
                      'text: "Printer controls"', "property bool controlsCollapsed: false",
                      "controlsCollapsed: root.controlsCollapsed"):
            self.assertIn(token, harness.CONTROLS_COLLAPSED_READOUT_QML + harness.DASHBOARD_QML)
        self.assertIn("controlsLocked", harness.MONITOR_MODEL)
        self.assertIn("controlsCollapsed", harness.MONITOR_MODEL)
        # The Information and Printer status panes collapse and persist too.
        # Collapsed titles must anchor to their header ROW: anchoring to
        # the toggle inside it is illegal in QML and silently drops the
        # anchor, which is what un-pinned the titles for so long.
        self.assertIn("anchors.top: controlHeader.bottom", harness.DASHBOARD_QML)
        self.assertIn("anchors.top: infoHeader.bottom", harness.INFO_PANE_QML)
        self.assertIn("anchors.top: statusHeader.bottom", harness.STATUS_PANE_QML)
        for token in ('text: "Printer status"', 'title: "Print job"', 'title: "Bed mesh"',
                      "id: infoCollapseButton", "id: statusCollapseButton",
                      "id: infoCollapsedTitle", "id: statusCollapsedTitle",
                      "setInfoCollapsed", "setStatusCollapsed"):
            self.assertIn(token, harness.MONITOR_QML + harness.MONITOR_MODEL + harness.MESH_SECTION_QML + harness.JOB_SECTION_QML + harness.INFO_PANE_QML + harness.INFO_COLLAPSED_READOUT_QML + harness.STATUS_PANE_QML + harness.STATUS_COLLAPSED_READOUT_QML)
        self.assertIn("infoCollapsed", harness.MONITOR_MODEL)
        self.assertIn("statusCollapsed", harness.MONITOR_MODEL)
        self.assertIn("cameraRefreshNonce", harness.MONITOR_MODEL)
        self.assertIn("mpf_reload", harness.MONITOR_QML)  # Refresh camera restarts the stream

    def test_temperature_chart_repaints_and_popovers_are_overlays(self):
        # A QML Canvas paints exactly once unless asked: the data
        # canvas must requestPaint on payload, geometry and visibility
        # changes (it used to render one frame and freeze) — and it
        # paints OFF the main thread (Image target + Threaded
        # strategy). The hover surface is scene-graph geometry: the
        # overlay Canvas is gone, and NOTHING in the hover path may
        # request a paint.
        self.assertIn("onChartChanged", harness.TEMP_CHART_QML)
        self.assertIn("dataCanvas.requestPaint()", harness.TEMP_CHART_QML)
        self.assertIn("onVisibleChanged", harness.TEMP_CHART_QML)
        self.assertIn("renderTarget: Canvas.Image", harness.TEMP_CHART_QML)
        self.assertIn("renderStrategy: Canvas.Threaded", harness.TEMP_CHART_QML)
        self.assertNotIn("overlay.requestPaint()", harness.TEMP_CHART_QML)
        self.assertNotIn("id: overlay", harness.TEMP_CHART_QML)
        self.assertIn("id: hoverCursorLine", harness.TEMP_CHART_QML)
        self.assertIn("id: hoverMarkers", harness.TEMP_CHART_QML)
        # One open pop-over at a time; the shells are overlay siblings
        # of the pane RowLayout, never layout children (anchored layout
        # children reflow every pane and log undefined-behavior
        # warnings).
        self.assertIn('property string openPopOver: ""', harness.MONITOR_QML)
        self.assertNotIn("bedMeshPanelOpen", harness.MONITOR_QML)
        self.assertNotIn("chartPanelOpen", harness.MONITOR_QML)
        self.assertIn("id: outsideClickLayer", harness.MONITOR_QML)
        # Esc closes the popovers through the same window-level
        # Shortcut that leaves the stage (the Keys handler died with
        # focus — the live report). The shortcut and the
        # ladder live in the dashboard document — one claimant for
        # the key across every layer.
        self.assertIn('sequence: "Esc"', harness.DASHBOARD_QML)
        self.assertIn("leaveMonitorStage", harness.DASHBOARD_QML)
        # The legend binds to the legend property (notifies only on real
        # changes, so delegates are never rebuilt at the 1 Hz sample
        # cadence) and toggles on user intent only — re-bound checkboxes
        # used to rewrite the state file every second.
        self.assertIn("temperatureChartLegend.series", harness.MONITOR_QML)
        self.assertIn("onToggled: root.printerModel.setTemperatureSensorVisible", harness.TEMPERATURE_DETAIL_QML)
        self.assertNotIn("onCheckedChanged: root.printerModel.setTemperatureSensorVisible", harness.TEMPERATURE_DETAIL_QML)
        # Target bands, not dashed lines (the ruling), and the
        # hover readout carries the clock.
        self.assertIn("Target bands", harness.TEMP_CHART_QML)
        self.assertIn("hoverClock", harness.TEMP_CHART_QML)
        self.assertIn('"wallOrigin"', harness.TEMP_CHART_QML)
        # The mini chart carries a live legend row (dot, name, value) so
        # the unlabelled sparklines stay readable, and the mesh detail's
        # readout row is permanent so the map never resizes on hover.
        self.assertIn("modelData.label + \" \" + value", harness.TEMP_HISTORY_SECTION_QML)
        self.assertIn("Hover the map for probe coordinates", harness.BED_MESH_DETAIL_QML)
        # The chart's hover values live in a cursor-following tooltip
        # OUTSIDE the clipped card (allowed to overflow any boundary) —
        # there is no in-card readout row to stretch the pop-up, and the
        # chart is declared exactly once in the pop-over.
        self.assertIn("id: chartHoverTooltip", harness.MONITOR_QML)
        self.assertNotIn("Hover the chart for per-series values", harness.MONITOR_QML)
        self.assertEqual(harness.TEMPERATURE_DETAIL_QML.count("id: chartPanelChart"), 1)
        self.assertIn("mapToItem(root, chartPanelChart.hoverCursor", harness.TEMPERATURE_DETAIL_QML)
        self.assertIn("hoverCursor", harness.TEMP_CHART_QML)
        # The top gridline's temperature label must be clamped by the
        # FONT ASCENT into the canvas (it used to baseline at y = -3,
        # always off-screen, and the fixed 10 px clamp shaved digit
        # tops on larger desktop fonts). The clamp now reads the paint
        # job's snapshot (the threaded paint must not reach the theme).
        self.assertIn("Math.ceil(job.fontPixels * 0.8) + 3", harness.TEMP_CHART_QML)
        # Units are explicit (°C — Cura has no temperature-unit
        # preference, so the plugin follows Cura) on the axis, the
        # tooltip rows and both legend live values; the X-axis tick
        # strip keeps breathing room below the plot.
        self.assertIn('toFixed(0) + "°C"', harness.TEMP_CHART_QML)
        self.assertIn('points[index][1].toFixed(1) + "°C"', harness.TEMP_CHART_QML)
        # The collapsed readout builds the pair form through the
        # printer-side infoReadoutText (the 2026-09-17 ruling), which
        # no longer shares the legend's exact expression — the chart's
        # own form is the one occurrence.
        self.assertEqual(harness.TEMPERATURE_DETAIL_QML.count('toFixed(1) + "°C"'), 1)
        self.assertEqual(harness.TEMP_HISTORY_SECTION_QML.count('toFixed(1) + "°C"'), 1)
        self.assertIn("Math.max(1, height - 22)", harness.TEMP_CHART_QML)
        # The console history lives in a terminal-styled pane: dark,
        # fixed-width, newest line pinned to the bottom, with a prompt
        # glyph on the input row.
        for token in ('color: consolePanel.printerModel != null && consolePanel.printerModel.monitorConnected ? MoonrakerTheme.consoleBackground : MoonrakerTheme.consoleBackgroundOffline', "No commands yet — lines you send appear here.", 'text: ">"'):
            self.assertIn(token, harness.CONSOLE_PANE_QML)
        # Both pop-overs open at the same offset over the camera column
        # so a second click on the opener dismisses without moving the
        # mouse (the chosen position, mesh-style).
        self.assertEqual(harness.MONITOR_QML.count("x: cameraArea.x + UM.Theme.getSize(\"default_margin\").width"), 4)
        # meshDetail is card-scoped: exactly one refresh may reference
        # it, and it lives in the same document that declares the id.
        self.assertEqual(harness.BED_MESH_DETAIL_QML.count("meshDetail.refresh()"), 1)
        self.assertGreater(harness.BED_MESH_DETAIL_QML.index("meshDetail.refresh()"), harness.BED_MESH_DETAIL_QML.index("id: meshDetail"))
        # The snapped-second gate must WRAP the publications, and the
        # chart-changed re-snap must clear the snap first so a gap
        # reset or legend toggle republishes even on a snap collision.
        self.assertGreater(harness.TEMP_CHART_QML.index("hoverClock = _clockText(snapped);"),
                           harness.TEMP_CHART_QML.index("if (snapped !== _hoverSnap) {"))
        on_chart = harness.TEMP_CHART_QML[harness.TEMP_CHART_QML.index("onChartChanged: {"):]
        self.assertIn("_hoverSnap = -1;", on_chart[:800])
        self.assertIn("_updateHover(root.hoverX);", on_chart[:800])
        # The freshly created detail map rehydrates the persisted
        # probe-points toggle at creation (never after a toggle event).
        self.assertIn("showProbePoints: root.printerModel != null ? root.printerModel.showProbePoints : false", harness.BED_MESH_DETAIL_QML)
        # A refused console send keeps the typed draft.
        self.assertIn("if (consolePanel.printerModel.sendConsoleCommand(consoleInput.text)) {", harness.CONSOLE_PANE_QML)
        # When every primary sensor is hidden, up to two visible
        # non-primary sensors stand in for the mini chart. The policy
        # lives ONCE in the chart owner (mini_names), and the legend carries
        # the selection as a stable row list — the section's chart
        # reads the bounded mini payload directly.
        chart_owner = (harness.PLUGINS / "TemperaturePresentation.py").read_text(encoding="utf-8")
        self.assertIn("mini_names(", chart_owner)
        self.assertNotIn("@pyqtSlot", chart_owner)
        self.assertIn("return self._temperature.setChartOpen(opened)", harness.MONITOR_MODEL)
        self.assertIn("legend.miniSeries", harness.MONITOR_QML)
        self.assertIn("temperatureChartMini", harness.TEMP_HISTORY_SECTION_QML)
        # The Layer row discloses which source produced the value, and
        # the terminal picks an installed monospace face at runtime
        # (the generic and comma lists do not resolve everywhere).
        self.assertIn("monitorLayerSource", harness.JOB_SECTION_QML)
        self.assertIn("monitorLayerSource !== undefined", harness.JOB_SECTION_QML)
        self.assertIn("Layer source: ", harness.JOB_SECTION_QML)
        # The model DECLARES the source (a dynamic setProperty would be
        # undefined at QML creation and the .length read would throw).
        self.assertIn('value_property(str, "monitorLayerSource", monitorChanged, "")', harness.MONITOR_MODEL)

    def test_the_chart_paints_each_point_without_re_reading_its_geometry(self):
        # The chart is open while a print runs, and every payload landing
        # repaints it: the data layers map each sample with the geometry
        # resolved once per paint (a scale and offset per axis) rather
        # than calling root._xFor/root._yFor per coordinate, which
        # re-reads six QML properties each time.
        self.assertIn("var plotWidth = width - gutter;", harness.TEMP_CHART_QML)
        self.assertIn("points[j][0] * mapScaleX + mapOffsetX", harness.TEMP_CHART_QML)
        self.assertIn("targetSeg[u][1] * mapScaleY + mapOffsetY", harness.TEMP_CHART_QML)
        self.assertIn("powerSeg[q][1] * plotBottom", harness.TEMP_CHART_QML)
        # The hover search runs once per mouse move, not again in any
        # repaint: the markers it published are what the scene-graph
        # Repeater draws directly. One call site plus the definition.
        self.assertEqual(harness.TEMP_CHART_QML.count("_nearestIndex("), 2)
        self.assertIn("model: root._hoverMarks", harness.TEMP_CHART_QML)
        self.assertIn("_hoverMarks = marks;", harness.TEMP_CHART_QML)
        # The render domain comes off the payload, with the scan kept for
        # a payload that predates it.
        self.assertIn("if (series.bounds !== undefined) {", harness.TEMP_CHART_QML)

    def test_every_value_property_rides_its_signal_group(self):
        # The panel's catch: a key declared with a notify signal but
        # absent from that signal's group can never notify — the
        # next-pause readout went stale while PAUSED (the other keys
        # in the group masked it while printing). Every declaration
        # must appear in its signal's group; the allowlist holds the
        # two pre-existing gaps the round did not add.
        import ast
        module = ast.parse(harness.MONITOR_MODEL)
        groups = {}
        for node in ast.walk(module):
            if isinstance(node, ast.Assign) and any(
                    getattr(target, "id", "") == "_SIGNAL_KEYS" for target in node.targets):
                for element in ast.walk(node.value):
                    if isinstance(element, ast.Tuple) and len(element.elts) >= 2 \
                            and isinstance(element.elts[0], ast.Constant) \
                            and isinstance(element.elts[1], ast.Tuple):
                        groups[str(element.elts[0].value)] = {
                            str(entry.value) for entry in element.elts[1].elts
                            if isinstance(entry, ast.Constant)}
        allowlist = {"britishSpelling", "monitorLoading"}
        missing = []
        for name, signal in harness.re.findall(r'value_property\([^,]+,\s*"([A-Za-z0-9]+)",\s*(\w+)', harness.MONITOR_MODEL):
            if name in allowlist:
                continue
            if name not in groups.get(signal, set()):
                missing.append(f"{name} ({signal})")
        self.assertEqual(missing, [],
                         "value properties outside their signal groups: %s" % missing)
        self.assertIn('"monitorLayerSource"', harness.MONITOR_MODEL)
        # A slim bar under the layer value shows the within-layer
        # progress; it hides while the layer has no height anchor.
        self.assertIn("monitorLayerProgress >= 0", harness.JOB_SECTION_QML)
        self.assertIn("Layer progress — how far through the current layer.", harness.JOB_SECTION_QML)
        self.assertIn("without loading it into the preview", harness.JOB_SECTION_QML)
        # The glyph's in-progress state: a non-clickable hourglass.
        self.assertIn('Qt.resolvedUrl("../resources/svg/Hourglass.svg")', harness.JOB_SECTION_QML)
        self.assertIn("root.printerModel.improvingEta", harness.JOB_SECTION_QML)
        # Both progress figures carry two decimals.
        self.assertIn("monitorProgress.toFixed(2)", harness.JOB_SECTION_QML)
        self.assertIn("(root.printerModel.monitorLayerProgress * 100).toFixed(2)", harness.JOB_SECTION_QML)
        # The Improve-ETA bar: determinate during the download, a
        # plugin-owned sweep while resolving/indexing (Cura's themed
        # indeterminate renders as a static full bar).
        self.assertIn("improveEtaProgress", harness.JOB_SECTION_QML)
        self.assertIn("NumberAnimation on sweepPhase", harness.JOB_SECTION_QML)
        self.assertIn("(1 - Math.abs(2 * improveEtaBar.sweepPhase - 1))", harness.JOB_SECTION_QML)
        self.assertIn("The spacer keeps the glyph hugging", harness.JOB_SECTION_QML)
        self.assertIn("SequentialAnimation on rotation", harness.JOB_SECTION_QML)
        self.assertIn("PauseAnimation", harness.JOB_SECTION_QML)
        self.assertIn("root.printerModel.improveEtaPhase", harness.JOB_SECTION_QML)
        self.assertIn("download_fraction", harness.MONITOR_MODEL + (harness.PLUGINS / "RemoteFileService.py").read_text(encoding="utf-8"))
        self.assertIn('"monitorLayerProgress"', harness.MONITOR_MODEL)
        self.assertIn("function monoFamily()", harness.CONSOLE_PANE_QML)
        self.assertIn("Qt.fontFamilies()", harness.CONSOLE_PANE_QML)
        # The tooltip sizes to its content (no width cap: the ruling
        # lets it overflow any boundary) and flips above only when
        # there is no room below the cursor.
        self.assertIn("width: tooltipColumn.implicitWidth + 2", harness.MONITOR_QML)
        self.assertIn("y: anchorPoint.y + height + 16 > root.height", harness.MONITOR_QML)
        # Send and Clear share one row beside the input (the
        # side-by-side request) — no RowLayout may open between them.
        send_clear = harness.CONSOLE_PANE_QML[harness.CONSOLE_PANE_QML.index('text: "Send"'):harness.CONSOLE_PANE_QML.index('text: "Clear"')]
        self.assertNotIn("RowLayout {", send_clear)
        # The mesh readout says Height, not a third coordinate, and a
        # live refresh re-snaps a parked cursor.
        self.assertIn('Height " + value.toFixed(3)', harness.BED_MESH_MAP_QML)
        self.assertIn("root.snap(root._hoverMouseX", harness.BED_MESH_MAP_QML)
        self.assertIn('"Probe points"', harness.BED_MESH_DETAIL_QML)
        self.assertIn("showProbePoints", harness.BED_MESH_MAP_QML)
        # The colour row offers a full picker beside the quick swatches,
        # and the picker is a platform dialog: the monitor must not
        # import the module itself, and must build the dialog from its
        # own document on the click — a host whose platform builds no
        # colour dialog still gets the whole monitor.
        for qml in (harness.MONITOR_QML, harness.TEMPERATURE_DETAIL_QML):
            self.assertNotIn("import QtQuick.Dialogs", qml)
        self.assertIn('Qt.createComponent("MoonrakerChartColorDialog.qml")', harness.TEMPERATURE_DETAIL_QML)
        self.assertIn("chartColorDialog", harness.TEMPERATURE_DETAIL_QML)
        self.assertIn("applyChartColorChoice", harness.TEMPERATURE_DETAIL_QML)
        self.assertIn('text: "Custom…"', harness.TEMPERATURE_DETAIL_QML)
        self.assertIn("import QtQuick.Dialogs", harness.CHART_COLOUR_DIALOG_QML)
        self.assertIn("ColorDialog {", harness.CHART_COLOUR_DIALOG_QML)
        self.assertIn("setShowProbePoints", harness.BED_MESH_DETAIL_QML)
        # Terminal order: the history sits above the input row.
        self.assertLess(harness.CONSOLE_PANE_QML.index("id: consoleText"), harness.CONSOLE_PANE_QML.index("id: consoleInput"))
        self.assertIn("All sensors hidden — click to re-enable one in the chart.", harness.TEMP_HISTORY_SECTION_QML)

    def test_system_section_has_the_manual_reconnect(self):
        # A live request: a Reconnect in the System
        # section for a UI stuck after a printer error.
        self.assertIn('text: "Reconnect"', harness.SYSTEM_INFO_SECTION_QML)
        self.assertIn("root.printerModel.reconnect()", harness.SYSTEM_INFO_SECTION_QML)

    def test_system_restart_surface(self):
        for token in ("firmwareRestart", "hostRestart", "FIRMWARE_RESTART", "machine/reboot"):
            self.assertIn(token, harness.MONITOR_MODEL + harness.CONTROLS)
        for token in ('text: "Firmware restart"', 'text: "Host restart"', 'text: "Klipper restart"'):
            self.assertIn(token, harness.SYSTEM_SECTION_QML)
        # The reason copy lives in the policy (4.2.0): the row reads
        # the published restartReason — the old QML sentence was
        # superseded by the policy's short form.
        self.assertIn('"A print is running"', harness.POLICY)
        self.assertIn("restartReason", harness.SYSTEM_SECTION_QML)

    def test_emergency_stop_is_pinned_outside_scrollable_controls(self):
        # The dock lives at the bottom of the dashboard, spanning the whole
        # window width (including under the controls pane), outside the
        # scrollable panes, so it stays visible in every collapse state.
        self.assertIn("anchors.bottom: emergencyDock.top", harness.DASHBOARD_QML)
        self.assertIn("id: emergencyDock", harness.DASHBOARD_QML)
        self.assertNotIn("id: emergencyDock", harness.MONITOR_QML)
        self.assertEqual(harness.DASHBOARD_QML.count("id: emergencyButton\n"), 1)

    def test_emergency_stop_remainder_copy_follows_the_theme_text_colour(self):
        # The 4.5.0 dark-mode ruling: the idle copy was hardcoded
        # black and unreadable on dark mode's grey ground. The
        # remainder follows the theme's text colour; the white
        # over-the-red-fill sweep copy stays white.
        self.assertIn('color: UM.Theme.getColor("text")', harness.DASHBOARD_QML)
        self.assertIn('color: "white"', harness.DASHBOARD_QML)

    def test_dashboard_shows_current_z_offset_beside_nudges(self):
        self.assertIn('text: "Current Z offset"', harness.PRINT_SECTION_QML)
        self.assertIn('"Current " + root.printerModel.zOffsetText', harness.ZOFFSET_CONTROLS_QML)
        self.assertIn("adjustZOffset", harness.ZOFFSET_CONTROLS_QML)

    def test_z_offset_buttons_are_opposites_with_equal_click_zones(self):
        self.assertIn("id: zOffsetGrid", harness.ZOFFSET_CONTROLS_QML)
        self.assertIn("model: [-0.005, -0.01, -0.025, -0.05]", harness.ZOFFSET_CONTROLS_QML)
        self.assertIn("model: [0.005, 0.01, 0.025, 0.05]", harness.ZOFFSET_CONTROLS_QML)
        # A two-column grid (up left, down right): both Repeater
        # delegates fill their cell equally, so click zones stay
        # matched and the labels cannot elide at narrow pane widths.
        grid = harness.ZOFFSET_CONTROLS_QML[harness.ZOFFSET_CONTROLS_QML.index("id: zOffsetGrid"):harness.ZOFFSET_CONTROLS_QML.index('text: "Clear Z offset"')]
        # Up row first, down row second, each four-across with equal
        # layout cells; the up model must precede the down model.
        self.assertLess(grid.index('text: "↑ "'), grid.index('text: "↓ "'))
        self.assertEqual(grid.count("RowLayout {"), 2)
        self.assertGreaterEqual(grid.count("Layout.fillWidth: true"), 2)
        # fixedWidthMode must NOT combine with Layout.fillWidth: the two
        # fight on every layout pass and sent the grid into an endless
        # invalidate loop that froze pane collapses.
        self.assertNotIn("fixedWidthMode: true", grid)

    def test_temperature_presets_are_buttons_not_an_implied_selection(self):
        self.assertIn("temperaturePresetItems", harness.PROFILES_SECTION_QML)
        self.assertIn('modelData.active ? "Active — "', harness.PROFILES_SECTION_QML)
        self.assertIn("applyTemperaturePreset(modelData.index)", harness.PROFILES_SECTION_QML)
        self.assertNotIn("temperaturePresetSelector", harness.DASHBOARD_QML)

    def test_pwm_controls_ride_their_component(self):
        self.assertIn("pwmOutputItems", harness.PWM_SECTION_QML)
        self.assertIn("setPwmOutput", harness.PWM_SECTION_QML)
        self.assertIn('title: "PWM outputs"', harness.PWM_SECTION_QML)

    def test_monitor_layer_tracks_remote_print_not_cura_slider(self):
        resolver = (harness.PLUGINS / "PrintState.py").read_text(encoding="utf-8")
        self.assertIn("class LayerResolver", resolver)
        self.assertIn("total = len(index.ranges)", resolver)
        self.assertNotIn("getCurrentLayer", resolver)
        self.assertIn("self._print_state()", harness.MONITOR_MODEL)

    def test_eta_anchors_to_slicer_metadata_instead_of_gcode_bytes(self):
        self.assertIn("def estimate_remaining", harness.FORMATTING)
        self.assertIn("remaining = max(0, estimate - elapsed)", harness.FORMATTING)
        self.assertIn("0.60 * estimate <= elapsed + by_file <= 1.75 * estimate", harness.FORMATTING)
        self.assertIn("physical.metadata_complete", harness.FORMATTING)
        self.assertNotIn("self._metadata_estimated_time * (1.0 - progress)", harness.MONITOR_MODEL)

    def test_mcu_stats_are_exposed_individually(self):
        for token in (
            "parse_mcu_stats",
            "mcu_awake",
            "mcu_task_avg",
            "bytes_retransmit",
            "mcuItems",
            '"Main MCU"',
        ):
            self.assertIn(token, harness.TYPED)
        self.assertIn("modelData.load", harness.MCUS_SECTION_QML)
        self.assertIn("modelData.frequency", harness.MCUS_SECTION_QML)
        self.assertIn("modelData.transport", harness.MCUS_SECTION_QML)

    def test_addressable_led_colour_is_controllable(self):
        for token in (
            "redPercent",
            "greenPercent",
            "bluePercent",
            "whitePercent",
            "hasWhite",
            "setLedColor",
            "SET_LED LED=",
        ):
            self.assertIn(token, harness.CONTROLS + harness.MONITOR_MODEL)
        self.assertIn("function applyLedColour()", harness.LEDS_SECTION_QML)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("applyLedColour()"), 4)
        self.assertIn('root.interactionSink(interacting, modelData.object, "led-red")', harness.LEDS_SECTION_QML)
        self.assertNotIn('text: "Set colour"', harness.DASHBOARD_QML)
        self.assertIn("root.printerModel.setLedColor", harness.LEDS_SECTION_QML)

    def test_live_tuning_slider_ranges_expand_from_accepted_value(self):
        self.assertIn("to: Math.min(50000, Math.max(200, root.printerModel != null ? Math.ceil(root.printerModel.speedFactorPercent * 2) : 200))", harness.TUNING_SECTION_QML)
        self.assertIn("to: Math.min(50000, Math.max(200, root.printerModel != null ? Math.ceil(root.printerModel.flowFactorPercent * 2) : 200))", harness.TUNING_SECTION_QML)
        self.assertEqual(harness.TUNING_SECTION_QML.count("from: 1"), 2)
        self.assertIn('min(50000, max(1, number(value, 100)))', harness.CONTROLS)
        self.assertNotIn("min(200, int(percent))", harness.CONTROLS)
        self.assertNotIn("min(150, int(percent))", harness.CONTROLS)

    def test_monitor_sliders_only_commit_on_release(self):
        # Speed, flow, fan, LED brightness, RGBW and PWM sliders all use
        # Qt Quick Controls' deferred-value mode. The control itself
        # funnels every interaction path (groove, handle drag, keyboard)
        # into valueTuning (the live preview) and valueCommitted (the
        # apply on completion) — the usage sites never re-derive the
        # interaction state. The tuning pair rides its component
        # (4.3.0); per-file counts plus the total.
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("live: false"), 2)
        self.assertGreaterEqual(harness.FANS_SECTION_QML.count("live: false"), 1)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("live: false"), 5)
        self.assertGreaterEqual(harness.PWM_SECTION_QML.count("live: false"), 1)
        self.assertEqual(harness.DASHBOARD_QML.count("live: false"), 0)
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("live: false") + harness.FANS_SECTION_QML.count("live: false") + harness.LEDS_SECTION_QML.count("live: false") + harness.PWM_SECTION_QML.count("live: false"), 9)
        for slider_id in ("speedSlider", "flowSlider"):
            marker = "id: " + slider_id
            start = harness.TUNING_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", harness.TUNING_SECTION_QML[start:start + 700], slider_id)
        for slider_id in ("fanSlider",):
            marker = "id: " + slider_id
            start = harness.FANS_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", harness.FANS_SECTION_QML[start:start + 700], slider_id)
        for slider_id in ("ledSlider", "redSlider", "greenSlider", "blueSlider", "whiteSlider"):
            marker = "id: " + slider_id
            start = harness.LEDS_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", harness.LEDS_SECTION_QML[start:start + 700], slider_id)
        for slider_id in ("pwmSlider",):
            marker = "id: " + slider_id
            start = harness.PWM_SECTION_QML.find(marker)
            self.assertGreaterEqual(start, 0, slider_id)
            self.assertIn("live: false", harness.PWM_SECTION_QML[start:start + 700], slider_id)
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("onValueCommitted:"), 2)
        self.assertGreaterEqual(harness.FANS_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("onValueCommitted:"), 5)
        self.assertGreaterEqual(harness.PWM_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertEqual(harness.DASHBOARD_QML.count("onValueCommitted:"), 0)
        self.assertIn("previewSpeedFactor", harness.TUNING_SECTION_QML)
        self.assertIn("previewFlowFactor", harness.TUNING_SECTION_QML)
        self.assertIn("previewFanSpeed", harness.FANS_SECTION_QML)
        self.assertIn("previewLedBrightness", harness.LEDS_SECTION_QML)
        self.assertIn("previewLedColor", harness.LEDS_SECTION_QML)
        self.assertIn("previewPwmOutput", harness.PWM_SECTION_QML)
        # The four component-local copies of the slider value helper
        # are gone (the engineering re-review): every call site reads
        # the slider's own selectedValue() — one definition in
        # OutlineSlider, the components cannot drift.
        self.assertNotIn("function sliderSelection", harness.TUNING_SECTION_QML + harness.FANS_SECTION_QML + harness.LEDS_SECTION_QML + harness.PWM_SECTION_QML)
        self.assertIn("pwmSlider.selectedValue() + \"%\"", harness.PWM_SECTION_QML)
        self.assertIn("speedSlider.selectedValue() + \"%\"", harness.TUNING_SECTION_QML)
        self.assertIn("fanSlider.selectedValue() + \"%\"", harness.FANS_SECTION_QML)
        self.assertIn('controlKind: "fan"', harness.FANS_SECTION_QML)
        self.assertIn("root.printerModel.setSpeedFactor(value)", harness.TUNING_SECTION_QML)
        self.assertIn("root.printerModel.setFlowFactor(value)", harness.TUNING_SECTION_QML)

    def test_monitor_sliders_do_not_repeat_qml_properties(self):
        # The sliders ride their components now: the same duplicate
        # can creep back in any of them, so the union is swept.
        haystack = harness.DASHBOARD_QML + harness.TUNING_SECTION_QML + harness.FANS_SECTION_QML + harness.LEDS_SECTION_QML + harness.PWM_SECTION_QML
        self.assertIsNone(harness.re.search(r"from: 0; to: 100; stepSize: 1\s*from: 0;", haystack))

    def test_slider_qml_prevents_parent_flickable_from_stealing_drag(self):
        self.assertIn("property bool tuningSliderPressed: false", harness.DASHBOARD_QML)
        self.assertIn("function receiveSliderInteraction(interacting, object, kind)", harness.DASHBOARD_QML)
        self.assertIn("interactive: !root.tuningSliderPressed", harness.DASHBOARD_QML)
        for slider_id in ("speedSlider", "flowSlider"):
            self.assertIn("id: " + slider_id, harness.TUNING_SECTION_QML)
        self.assertIn("id: fanSlider", harness.FANS_SECTION_QML)
        for slider_id in ("ledSlider", "redSlider",
                          "greenSlider", "blueSlider", "whiteSlider"):
            self.assertIn("id: " + slider_id, harness.LEDS_SECTION_QML)
        self.assertIn("id: pwmSlider", harness.PWM_SECTION_QML)
        self.assertNotIn("root.tuningSliderPressed = interacting", harness.DASHBOARD_QML)
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count('root.interactionSink(interacting, "", "")'), 2)
        self.assertIn('root.interactionSink(interacting, modelData.object, "fan")', harness.FANS_SECTION_QML)
        for kind in ("led-brightness", "led-red", "led-green", "led-blue", "led-white"):
            self.assertIn('root.interactionSink(interacting, modelData.object, "%s")' % kind, harness.LEDS_SECTION_QML)
        self.assertIn('root.interactionSink(interacting, modelData.object, "pwm")', harness.PWM_SECTION_QML)

    def test_monitor_uses_plugin_outline_bars_and_sliders(self):
        # The themed ProgressBar/Slider render a black slab in the
        # inactive-window palette (a screenshot) — the monitor's
        # bars and sliders are all plugin-owned outline components now,
        # so a bare themed control may not creep back in.
        for file_text in (harness.MONITOR_QML, harness.DASHBOARD_QML, harness.TUNING_SECTION_QML, harness.FANS_SECTION_QML, harness.LEDS_SECTION_QML, harness.PWM_SECTION_QML, harness.JOB_SECTION_QML):
            # Every "ProgressBar {"/"Slider {" token must be the plugin
            # outline components (the substring check covers both) —
            # the range-filter bar is the other plugin-owned
            # slider-shaped component (its name embeds "Slider {").
            self.assertEqual(file_text.count("ProgressBar {"), file_text.count("OutlineProgressBar {"))
            self.assertEqual(file_text.count("Slider {"),
                file_text.count("OutlineSlider {") + file_text.count("BedMeshRangeSlider {"))
        # The Print-job section's bar is the stacked Rectangle (the
        # 2026-09-17 ruling) — no themed bar, the rule's intent.
        self.assertIn("THE STACKED BAR", harness.JOB_SECTION_QML)
        # The three fills own their stack positions: the print fill
        # the bottom, the pause the middle, the layer the top (the
        # live report: the fills piled at the top and the bar read
        # broken whenever the values moved).
        self.assertIn("anchors.bottom: parent.bottom", harness.JOB_SECTION_QML)
        self.assertIn("anchors.verticalCenter: parent.verticalCenter", harness.JOB_SECTION_QML)
        self.assertIn("anchors.top: parent.top", harness.JOB_SECTION_QML)
        self.assertEqual(harness.DASHBOARD_QML.count("OutlineSlider {"), 0)
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("OutlineSlider {"), 2)
        self.assertGreaterEqual(harness.FANS_SECTION_QML.count("OutlineSlider {"), 1)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("OutlineSlider {"), 5)
        self.assertGreaterEqual(harness.PWM_SECTION_QML.count("OutlineSlider {"), 1)
        indicator = (harness.PLUGINS / "LoadProgressIndicator.qml").read_text(encoding="utf-8")
        # The indicator bar's track is an outline too: transparent
        # interior, lining border, Cura-blue fill.
        self.assertIn('color: "transparent"', indicator)
        self.assertIn('border.color: UM.Theme.getColor("lining")', indicator)
        self.assertIn('border.width: UM.Theme.getSize("default_lining").width', indicator)
        self.assertIn('UM.Theme.getColor("primary")', indicator)
        # The outline components fill in Cura's brand blue (the same
        # accent as buttons and slider handles), never the text colour.
        bar = (harness.PLUGINS / "OutlineProgressBar.qml").read_text(encoding="utf-8")
        slider = (harness.PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        self.assertIn('UM.Theme.getColor("primary")', bar)
        self.assertNotIn('color: UM.Theme.getColor("text")', bar)
        self.assertNotIn('border.color: UM.Theme.getColor("text")', slider)
        self.assertIn('UM.Theme.getColor("primary")', slider)
        # Every bar corner uses Cura's own progressbar radius ("little
        # rounded ends"), never a full pill — and every bar/slider
        # radius needs cornerSide, because Cura.RoundedRectangle forces
        # radius 0 without it (the corners silently render square).
        for text in (bar, indicator, harness.JOB_SECTION_QML):
            self.assertIn('UM.Theme.getSize("progressbar_radius")', text)
        for text, corners in ((bar, 2), (slider, 3), (indicator, 3), (harness.JOB_SECTION_QML, 3)):
            self.assertEqual(text.count("cornerSide:"), corners, text[:40])
        # The pop-over shell must tolerate instantiation without a
        # parent (the engine gate creates every document standalone):
        # an unguarded parent.width read is a TypeError there.
        self.assertIn("parent != null ?", harness.POPOVER_QML)
        # The preview load indicator must stay OUT of the buttons Row
        # (panel UX P1: as the Row's third child it painted off-card),
        # the pane collapse must close the pop-over, the ETA tooltip
        # must not claim a basis for a paused/absent value, and the
        # chart's filling state must actually render its copy.
        panel = (harness.PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")
        self.assertIn("The indicator is a SIBLING of the buttons Row", panel)
        self.assertIn("Collapsing the pane hides the pop-over's", harness.MONITOR_QML)
        self.assertIn('monitorEta === "Paused" ? ""', harness.JOB_SECTION_QML)
        # The "Collecting temperature history…" placeholder stays ABSENT:
        # the waiting state read as annoying and was dropped
        # before; the changelog quote was struck instead (the filling
        # flag remains plumbed, unused by the UI).
        self.assertNotIn("Collecting temperature history", harness.MONITOR_QML + harness.CHANGELOG)
        self.assertIn("_clockTextMinutes", harness.TEMP_CHART_QML)
        # Disabled sliders grey the fill and the handle ring.
        slider_source = (harness.PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        self.assertGreaterEqual(slider_source.count("control.enabled ? UM.Theme.getColor(\"primary\") : UM.Theme.getColor(\"text_disabled\")"), 2)
        # The colour/colour strings follow the user's locale.
        self.assertIn("britishSpelling", harness.MONITOR_QML + harness.TEMPERATURE_DETAIL_QML)
        self.assertIn("britishSpelling", harness.MONITOR_MODEL)
        self.assertIn("Accessible.name: \"Show \"", harness.TEMPERATURE_DETAIL_QML)
        # The console's input row lives inside the dark well.
        self.assertIn("Layout.preferredHeight: 190 * screenScaleFactor", harness.CONSOLE_PANE_QML)

    def test_deferred_slider_and_monitor_ux_contracts(self):
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("live: false"), 2)
        self.assertGreaterEqual(harness.FANS_SECTION_QML.count("live: false"), 1)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("live: false"), 5)
        self.assertGreaterEqual(harness.PWM_SECTION_QML.count("live: false"), 1)
        self.assertEqual(harness.DASHBOARD_QML.count("live: false"), 0)
        self.assertGreaterEqual(harness.TUNING_SECTION_QML.count("onValueCommitted:"), 2)
        self.assertGreaterEqual(harness.FANS_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertGreaterEqual(harness.LEDS_SECTION_QML.count("onValueCommitted:"), 5)
        self.assertGreaterEqual(harness.PWM_SECTION_QML.count("onValueCommitted:"), 1)
        self.assertEqual(harness.DASHBOARD_QML.count("onValueCommitted:"), 0)
        self.assertNotIn("function sliderSelection(slider)", harness.DASHBOARD_QML)
        # The refocus walk roots at the section instantiations (the
        # architecture re-review's dangling-id fix) — the dashboard
        # never names a section's repeater id.
        for repeater_id in ("fanRepeater", "ledRepeater", "pwmRepeater"):
            self.assertNotIn(repeater_id, harness.DASHBOARD_QML)
        self.assertIn("root.focusSliderIn(fansSection, target, kind)", harness.DASHBOARD_QML)
        self.assertIn("root.focusSliderIn(ledsSection, target, kind)", harness.DASHBOARD_QML)
        self.assertIn("root.focusSliderIn(pwmSection, target, kind)", harness.DASHBOARD_QML)
        self.assertIn("After release, the latest value is applied once it has been unchanged for 250 ms.", harness.TUNING_SECTION_QML)
        self.assertIn('text: "Refresh Moonraker\'s webcam list."', harness.CAMERA_PANE_QML)
        # The exclude dialog died with the 4.6.0 rework: the gesture
        # is the confirmation, and the stale "cannot be undone" copy
        # must never survive anywhere in the monitor document.
        self.assertNotIn("Exclude object?", harness.MONITOR_QML)
        self.assertNotIn("cannot be undone", harness.MONITOR_QML)
        tuning = (harness.PLUGINS / "MonitorTuning.py").read_text(encoding="utf-8")
        self.assertIn("DEBOUNCE_MS = 250", tuning)
        self.assertIn("current.revision != revision", tuning)

    def test_full_config_is_discovered_not_polled_every_second(self):
        self.assertIn('["save_config_pending", "save_config_pending_items"]', harness.DATA)
        self.assertIn('"config-static"', harness.DATA)
        self.assertIn('category="discovery"', harness.DATA)

    def test_monitor_consumes_shared_session_poll_policy(self):
        self.assertIn("self._client.session.snapshot.printer_state", harness.DATA)
        self.assertIn("poll_policy.interval_ms(", harness.DATA)
        self.assertIn("if timer.interval() != interval:", harness.DATA)
        for category in (
            "RequestCategory.AUXILIARY",
            "RequestCategory.POWER",
            "RequestCategory.SYSTEM",
            "RequestCategory.DISCOVERY",
        ):
            self.assertIn(category, harness.DATA)

    def test_monitor_has_one_timer_policy_owner(self):
        owners = [source for source in (harness.MONITOR_MODEL, harness.DATA) if any(
            isinstance(node, harness.ast.FunctionDef) and node.name == "_intervals"
            for node in harness.ast.walk(harness.ast.parse(source))
        )]
        self.assertEqual(len(owners), 1)
        self.assertIs(owners[0], harness.DATA)

    def test_camera_identity_and_selection_are_typed_and_sized(self):
        self.assertIn("def identity(camera", harness.TYPED)
        self.assertIn("camera_selected", harness.TYPED)
        # The camera bar's final shape (the 2026-09-19 live-run
        # ruling): the controls ride LEVEL with the pane title with
        # no separate "Camera" label — the selector carries the name.
        # The bar lives in CameraPane.
        self.assertIn("Layout.preferredWidth: 180 * screenScaleFactor", harness.CAMERA_PANE_QML)
        self.assertIn("Layout.minimumWidth: 60 * screenScaleFactor", harness.CAMERA_PANE_QML)
        self.assertEqual(harness.CAMERA_PANE_QML.count('text: "Camera"'), 0)

    def test_camera_qml_uses_the_activated_signal_index_not_bound_current_index(self):
        self.assertIn("onActivated: function (index)", harness.CAMERA_PANE_QML)
        self.assertIn("selectWebcam(index)", harness.CAMERA_PANE_QML)
        self.assertNotIn("selectWebcam(cameraSelector.currentIndex)", harness.CAMERA_PANE_QML)

    def test_the_first_camera_apply_is_coalesced_through_one_callback(self):
        # The camera-delay fix's second cause: a first discovery
        # changes BOTH the url and the nonce, and each handler used to
        # apply the camera separately — two stream starts per first
        # entry. One callLater coalescer collapses the pair into one
        # apply; the initial attach, an explicit refresh and a camera
        # switch all still apply exactly once.
        self.assertIn('property bool _cameraApplyPending: false', harness.MONITOR_QML)
        self.assertIn("Qt.callLater(function () {", harness.MONITOR_QML)
        self.assertIn("root.scheduleCameraApply();", harness.MONITOR_QML)
        self.assertEqual(harness.MONITOR_QML.count("root.scheduleCameraApply();"), 3)

    def test_camera_render_watchdogs_are_wired(self):
        # The live reports: a stream that CONNECTED but never
        # painted a frame raises no error signal — the viewport's stall
        # watchdog watches the frame size; and a suspend/wake leaves a
        # frozen frame whose size is already set — the model's wake
        # hook reloads the source.
        self.assertIn("cameraStallWatchdog", harness.CAMERA_VIEWPORT_QML)
        self.assertIn("cameraRenderStalled()", harness.CAMERA_VIEWPORT_QML)
        self.assertIn("def cameraRenderStalled", harness.MONITOR_MODEL)
        self.assertIn("applicationStateChanged.connect(self._on_app_state_changed)", harness.MONITOR_MODEL)

    def test_slider_click_behaviours_are_wired(self):
        # A live report: a click on a slider's grab handle
        # must not move it, and a click focuses the slider so the
        # arrow keys nudge one step.
        config = (harness.PLUGINS / "ConnectionSettings.qml").read_text(encoding="utf-8")
        for token in ("handlePress", "pressIsOnHandle", "parent.value = parent.valueBeforePress",
                      "focusPolicy: Qt.StrongFocus", "Keys.onUpPressed: {", "increase()",
                      "forceActiveFocus()", "mouse.accepted = parent.handlePress",
                      # The handle-centre formula subtracts the
                      # handle's own width — the old availableWidth
                      # centre read a handle press as a track jump at
                      # the track ends (the reviewer's finding).
                      "availableWidth - handle.width"):
            self.assertIn(token, config)
        # The dashboard's OutlineSliders carry the same behaviours in
        # the shared component (4.2.0): the handle path drives the
        # value through the overlay, and every interaction path
        # (groove, handle, keyboard) funnels into the component's own
        # valueTuning/valueCommitted signals — the usage sites never
        # re-derive the interaction state.
        outline = (harness.PLUGINS / "OutlineSlider.qml").read_text(encoding="utf-8")
        for token in ("handlePress", "pressIsOnHandle", "tuningActive", "focusPolicy: Qt.StrongFocus",
                      "forceActiveFocus()", "control.value = control.valueBeforePress",
                      "signal valueTuning", "signal valueCommitted", "readonly property bool interacting",
                      "keyDebounce.restart()", "control.tuningActive = true",
                      "availableWidth - handle.width",
                      # The rebuild focus contract (the reviewer's
                      # finding): the dying slider reports itself.
                      "focusLostByDestruction", "_heldFocus"):
            self.assertIn(token, outline)
        dashboard = (harness.PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")
        for token in ("receiveSliderFocus", "focusSink: root.receiveSliderFocus"):
            self.assertIn(token, dashboard)
        for section in (harness.FANS_SECTION_QML, harness.LEDS_SECTION_QML, harness.PWM_SECTION_QML):
            for token in ("focusSink", "onFocusLostByDestruction"):
                self.assertIn(token, section)
        # The preview card's exaggeration slider carries the same
        # corrected formula (the shared ruling — no surface may keep
        # the drift). The slider lives in the card's legend leaf.
        preview = (harness.PLUGINS / "BedMeshLegend.qml").read_text(encoding="utf-8")
        self.assertIn("availableWidth - handle.width", preview)
        self.assertIn("onValueCommitted", harness.DASHBOARD_QML + harness.TUNING_SECTION_QML + harness.FANS_SECTION_QML + harness.LEDS_SECTION_QML + harness.PWM_SECTION_QML)
        # The keyboard nudge holds the interaction state until its
        # value submits, and the fan/LED/PWM repeaters freeze while a
        # tuning slider is mid-gesture — without the hold, the
        # commit's publish rebuilt the repeaters mid-nudge and killed
        # the focused delegate (a live report).
        for token in ("root.frozenFanItems = root.printer.fanControlItems",):
            self.assertIn(token, harness.DASHBOARD_QML)
        self.assertIn("root.freezeRepeaters ? root.frozenItems", harness.FANS_SECTION_QML)
        self.assertIn("root.freezeRepeaters ? root.frozenItems", harness.LEDS_SECTION_QML)
        self.assertIn("root.freezeRepeaters ? root.frozenItems", harness.PWM_SECTION_QML)
        # The bed-mesh range filter's keyboard half: focus + arrow keys.
        range_slider = (harness.PLUGINS / "BedMeshRangeSlider.qml").read_text(encoding="utf-8")
        for token in ("Keys.onLeftPressed", "Keys.onRightPressed", "Keys.onUpPressed", "forceActiveFocus()"):
            self.assertIn(token, range_slider)

    def test_mesh_rainbow_bar_matches_the_preview_scale(self):
        # A live request: the expanded bed-mesh view shows
        # the SAME blue-to-red min/max bar as the Preview's overlay —
        # the shared dual-ended range-filter component (4.2.0) owns
        # the stops now, so the two surfaces cannot drift.
        slider = (harness.PLUGINS / "BedMeshRangeSlider.qml").read_text(encoding="utf-8")
        for stop in ("MoonrakerTheme.bandBlue", "MoonrakerTheme.bandCyan", "MoonrakerTheme.bandGreen", "MoonrakerTheme.bandYellow", "MoonrakerTheme.bandRed"):
            self.assertIn(stop, slider)
        # The Preview's own copy lives in the card's legend leaf (4.6.2).
        for qml in (harness.BED_MESH_DETAIL_QML, harness.BED_MESH_LEGEND_QML):
            self.assertIn("BedMeshRangeSlider", qml)
        self.assertIn('text: root.printerModel != null ? "Low " + root.printerModel.bedMeshMinimum.toFixed(3)', harness.BED_MESH_DETAIL_QML)

    def test_mesh_map_bed_space_visualisation_is_klipper_faithful(self):
        # The accuracy ruling: the expanded map draws the
        # probed cells within the real bed, extends the BOUNDARY
        # values to the bed edges (Klipper clamps its lookup to the
        # boundary cells — _get_linear_index constrains index and t),
        # and outlines the measured bounds in the Preview's neon
        # orange. The extension is fainter because it is the
        # boundary's continuation, not a measurement.
        for token in ("MoonrakerTheme.neonOrange", "measured ? 0.58 : 0.28", "clampedValue",
                      "bedMeshMachineWidth", "bedMeshMachineDepth", "bedMeshCenterIsZero",
                      "printerToWidget", "constrains both", "hoverClamped",
                      'root.hoverClamped ? MoonrakerTheme.neonOrange'):
            self.assertIn(token, harness.BED_MESH_MAP_QML + harness.MONITOR_MODEL)
        # The popover carries the same clamped disclaimer the Preview's
        # legend makes (the request).
        self.assertIn("Neon orange outline = the probed mesh bounds; outside = the boundary values, continued as Klipper clamps them", harness.BED_MESH_DETAIL_QML)

    def test_the_popover_schedules_the_pause_at_its_own_layer(self):
        # The popover's pause-at-layer block (4.6.0): the CARD's own
        # schedule read from the POPOVER's own layer — the nine keys
        # published on the monitor model, the three intents its
        # controls send, and the QML that reads them. The card's own
        # behaviour is untouched (the locked-surface ruling).
        for token in (
            'pauseAtLayerItems = value_property(QVariant, "pauseAtLayerItems", pauseAtLayerChanged, [])',
            'pauseAtLayerCandidate = value_property(int, "pauseAtLayerCandidate", pauseAtLayerChanged, 0)',
            'pauseAtLayerHasClearable = value_property(bool, "pauseAtLayerHasClearable", pauseAtLayerChanged, False)',
            "def togglePauseAtLayer(self, layer):",
            "def removePauseAtLayer(self, layer):",
            "def clearPauseAtLayer(self):",
            # The candidate's source is the FOLLOWER's layer, never
            # Cura's Preview selection.
            "index = self._follower_layer_anchor",
        ):
            self.assertIn(token, harness.MONITOR_MODEL)
        for token in (
            "contentWidth: Math.min(940 * screenScaleFactor, root.width - x - UM.Theme.getSize(\"default_margin\").width)",
        ):
            self.assertIn(token, harness.MONITOR_QML)
        # The schedule's own view carries the rest: the pause column
        # moved out of the pop-over's card into the view that owns it.
        for token in (
            'objectName: "moonrakerFollowerPauseButton"',
            "root.printerModel.togglePauseAtLayer(root.printerModel.pauseAtLayerCandidate);",
            "root.printerModel.removePauseAtLayer(pauseRow.pauseLayer);",
            "root.printerModel.clearPauseAtLayer();",
            "root.printerModel.pauseAtLayerItems",
            "model.pauseAtLayerUnavailableText",
            "root.printerModel.pauseAtLayerHasClearable === true",
            # The schedule's column and its scrollable list (the live
            # report): the rows belong BESIDE the plate — under it they
            # squeezed the face onto the card's clipped bottom edge —
            # and the list scrolls with the card's own chevrons rather
            # than stopping at a counted remainder.
            "id: root",
            "id: pauseBlockModel",
            "model: pauseBlockModel",
            "interactive: contentHeight > height",
            # The list owns the column's remaining height (the live
            # request) — the popover only, never the card's capped list.
            "Layout.fillHeight: true",
            "function onPauseAtLayerChanged() {",
            "Component.onCompleted: syncPauseRows()",
            '"↑"',
            '"↓"',
        ):
            self.assertIn(token, harness.PAUSE_SCHEDULE_QML)
        # The width split, scoped to each column's own body: the plate
        # holds the width the popover's content had before the schedule
        # moved beside it, and the schedule's column yields whatever is
        # left. A floor on the schedule's column took 143 px off the
        # 900 px pane's face and moved every geometry contract this
        # face has, so the pair is pinned where it stands — a bare
        # "minimumWidth: 0" matched anywhere would pass vacuously.
        plate_column = harness.PRINT_FOLLOWER_QML[harness.PRINT_FOLLOWER_QML.index("id: followerContent"):harness.PRINT_FOLLOWER_QML.index("PlateProgressFace {")]
        plate_width = "585 * screenScaleFactor - 2 * UM.Theme.getSize(\"default_margin\").width"
        self.assertIn("Layout.preferredWidth: %s" % plate_width, plate_column)
        self.assertIn("Layout.minimumWidth: %s" % plate_width, plate_column)
        pause_column = harness.PAUSE_SCHEDULE_QML[:harness.PAUSE_SCHEDULE_QML.index("id: pauseBlockModel")]
        self.assertIn("Layout.preferredWidth: 340 * screenScaleFactor", pause_column)
        self.assertIn("Layout.minimumWidth: 0", pause_column)
        # The chevrons reuse the card's own expressions, so the
        # no-reflow allow-list carries them once for both hosts.
        for token in (
            "visible: pauseListView.height > 0 && pauseListView.contentY > 2",
            "visible: pauseListView.height > 0 && pauseListView.contentY < pauseListView.contentHeight - pauseListView.height - 2",
        ):
            self.assertIn(token, harness.PAUSE_SCHEDULE_QML)
        # No control disappears with the schedule: the list, its heading,
        # the reason line and the clear button collapse by HEIGHT while
        # empty (the no-reflow rule's own replacement for a visibility
        # gate). The reason line and the heading share the one idiom.
        self.assertEqual(harness.PAUSE_SCHEDULE_QML.count("Layout.preferredHeight: text.length > 0 ? implicitHeight : 0"), 2)
        self.assertIn("readonly property bool clearAvailable: root.printerModel != null && root.printerModel.pauseAtLayerHasClearable === true", harness.PAUSE_SCHEDULE_QML)
        # The two actions share one row at the foot of the column (the
        # live request): the pause button takes the slack and Clear keeps
        # a width sized to its own word, side by side rather than stacked.
        self.assertLess(harness.PAUSE_SCHEDULE_QML.index('objectName: "moonrakerFollowerPauseButton"'),
                        harness.PAUSE_SCHEDULE_QML.index("id: clearPausesButton"))
        # The popover's own screen says the short word; the card keeps the
        # longer line (its own file, its own pin). Both the width AND the
        # height collapse with the clear action, so the row's other button
        # expands into the whole foot — a collapsed slot that kept its
        # width would leave the button beside a gap (the live request).
        clear_block = harness.PAUSE_SCHEDULE_QML[harness.PAUSE_SCHEDULE_QML.index("id: clearPausesButton"):harness.PAUSE_SCHEDULE_QML.index("root.printerModel.clearPauseAtLayer();")]
        self.assertIn('text: "Clear"', clear_block)
        self.assertIn("Layout.preferredWidth: root.clearAvailable ? 60 * screenScaleFactor : 0", clear_block)
        self.assertIn("Layout.preferredHeight: root.clearAvailable ? UM.Theme.getSize(\"action_button\").height : 0", clear_block)
        self.assertIn("enabled: root.clearAvailable", clear_block)
        # Both foot buttons centre their labels: the theme's content row
        # packs from the left, so the fixed-width mode is what centring
        # needs — without it the label hugs its text against the left edge
        # (the live report).
        pause_block = harness.PAUSE_SCHEDULE_QML[harness.PAUSE_SCHEDULE_QML.index('objectName: "moonrakerFollowerPauseButton"'):harness.PAUSE_SCHEDULE_QML.index("root.printerModel.togglePauseAtLayer(")]
        for block in (pause_block, clear_block):
            self.assertIn("fixedWidthMode: true", block)
        self.assertNotIn("scheduledPauseList", harness.PAUSE_SCHEDULE_QML, "the popover kept the static capped list")
        self.assertNotIn("+ root.hiddenRows", harness.PAUSE_SCHEDULE_QML, "the popover counts a remainder instead of scrolling")

