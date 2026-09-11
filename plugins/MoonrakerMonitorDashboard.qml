import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// Component-rooted DELIBERATELY: Cura's monitor-view loader
// (setMonitorViewQmlPath) creates this document and expects a
// Component it can instantiate — unwrapping to the modern item-root
// form loaded fine in the capture harness but made the real Monitor
// stage fall back to Cura's placeholder. Qt logs a typecompiler
// deprecation for the pattern; that warning is accepted.
Component {
    id: dashboardComponent
    Item {
        id: root
        property variant catalog: UM.I18nCatalog {
            name: "cura"
        }
        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property bool controlsCollapsed: root.printer != null ? root.printer.controlsCollapsed : false
        property var macroParameters: []
        // The file-manager popup's open state (Snapshot 0: the mock).
        // A printer switch closes it — a stale popup must never carry
        // actions from one machine to the next (round-2 A15).
        property bool fileManagerOpen: root.printer != null && root.printer.fileManagerOpen
        onPrinterChanged: {
            if (root.printer != null) {
                root.printer.setFileManagerOpen(false);
            }
        }
        // The document root sits in the bubbling chain of EVERY
        // focused item in the stage, so Esc closes the popup no
        // matter where focus actually landed (the author's Snapshot
        // 0 report: Esc only worked while the search field was
        // focused).
        Keys.onEscapePressed: {
            if (fileManagerOpen) {
                if (root.printer != null) {
                    root.printer.setFileManagerOpen(false);
                }
                event.accepted = true;
            }
        }

        Cura.MessageDialog {
            id: cancelPrintDialog
            title: "Cancel print?"
            text: "This will cancel the current print on the printer."
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted: {
                if (root.printer != null) {
                    root.printer.cancelPrint();
                }
            }
        }

        Cura.MessageDialog {
            id: powerOffDialog
            property string deviceName: ""
            title: "Turn off power device?"
            text: "A print is active. Turning this device off may stop the printer immediately."
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted: {
                if (root.printer != null && deviceName.length > 0) {
                    root.printer.setPowerDevice(deviceName, false);
                }
            }
        }
        property bool tuningSliderPressed: false
        property bool anyPowerLocked: {
            if (printer == null || printer.powerDevices == null)
                return false;
            for (var i = 0; i < printer.powerDevices.length; ++i) {
                if (printer.powerDevices[i].locked && !printer.powerDevices[i].can_toggle)
                    return true;
            }
            return false;
        }

        function sliderSelection(slider) {
            if (slider == null)
                return 0;
            return Math.round(slider.valueAt(slider.position));
        }

        function refreshMacroParameters() {
            if (root.printer == null || macroSelector.currentIndex < 0) {
                root.macroParameters = [];
                return;
            }
            root.macroParameters = root.printer.macroParameterDefinitions(macroSelector.currentText);
        }

        function macroArgumentsValid() {
            for (var i = 0; i < macroParameterRepeater.count; ++i) {
                var item = macroParameterRepeater.itemAt(i);
                if (item != null && item.argumentRequired && item.argumentValue.trim().length === 0) {
                    return false;
                }
            }
            return true;
        }

        function macroArgumentString() {
            var args = [];
            for (var i = 0; i < macroParameterRepeater.count; ++i) {
                var item = macroParameterRepeater.itemAt(i);
                if (item == null)
                    continue;
                var value = item.argumentValue.trim();
                if (value.length > 0)
                    args.push(item.argumentName + "=" + value);
            }
            return args.join(" ");
        }

        MoonrakerMonitor {
            id: baseMonitorComponent
        }

        Connections {
            target: root.printer
            function onControlsChanged() {
                root.refreshMacroParameters();
            }
            function onTypedControlsChanged() {
                root.refreshMacroParameters();
            }
        }

        Component {
            id: emergencyButtonComponent
            Item {
                id: emergencyButton
                property int clicks: root.printer != null ? root.printer.emergencyStopClicks : 0
                // The author's ruling (2026-09-10): while DISCONNECTED
                // no Monitor-page control is enabled — the emergency
                // stop included. It dims and refuses input instead of
                // pretending it could fire.
                property bool enabled: root.printer != null && root.printer.monitorConnected
                opacity: emergencyButton.enabled ? 1 : 0.4
                Rectangle {
                    anchors.fill: parent
                    radius: UM.Theme.getSize("default_radius").width
                    color: "transparent"
                    border.color: "#d32f2f"
                    border.width: 2 * screenScaleFactor
                    clip: true
                    Rectangle {
                        id: emergencyFill
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: parent.width * Math.min(1.0, (emergencyButton.clicks + (root.printer != null ? root.printer.emergencyHoldProgress : 0)) / 3.0)
                        color: "#d32f2f"
                    }
                    // The label flips white PROGRESSIVELY as the red
                    // fill sweeps over it: two copies of the same
                    // text, the white one clipped to the fill's
                    // exact width and the black one clipped to the
                    // remainder. The clip boundary is pixel-exact —
                    // glyphs cut mid-stroke, so the colour boundary
                    // follows the fill edge continuously, not letter
                    // by letter (the author's request).
                    Item {
                        id: emergencyTextWhite
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.left: parent.left
                        width: emergencyFill.width
                        clip: true
                        UM.Label {
                            width: emergencyButton.width
                            height: parent.height
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            text: (root.printer != null && root.printer.emergencyHoldProgress > 0) ? "EMERGENCY STOP — keep holding" : (emergencyButton.clicks === 0 ? "EMERGENCY STOP — click twice, then hold" : (emergencyButton.clicks === 1 ? "EMERGENCY STOP — one more click, then hold" : "EMERGENCY STOP — press and hold to fire"))
                            font: UM.Theme.getFont("medium_bold")
                            color: "white"
                        }
                    }
                    Item {
                        id: emergencyTextBlack
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.right: parent.right
                        width: parent.width - emergencyFill.width
                        clip: true
                        UM.Label {
                            width: emergencyButton.width
                            height: parent.height
                            // Shifted so the text sits at the SAME
                            // position as the white copy: the clip
                            // region starts at the fill's right edge.
                            x: -emergencyFill.width
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            text: (root.printer != null && root.printer.emergencyHoldProgress > 0) ? "EMERGENCY STOP — keep holding" : (emergencyButton.clicks === 0 ? "EMERGENCY STOP — click twice, then hold" : (emergencyButton.clicks === 1 ? "EMERGENCY STOP — one more click, then hold" : "EMERGENCY STOP — press and hold to fire"))
                            font: UM.Theme.getFont("medium_bold")
                            color: "black"
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: emergencyButton.enabled
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (root.printer != null)
                            root.printer.emergencyStopClick()
                        onPressed: if (root.printer != null)
                            root.printer.emergencyHoldStarted()
                        onReleased: if (root.printer != null)
                            root.printer.emergencyHoldReleased()
                        // A grab stolen mid-hold (modal dialog, window
                        // deactivation) delivers a canceled instead of a
                        // release; without this the armed hold timer would
                        // still fire the stop after the user let go.
                        onCanceled: if (root.printer != null)
                            root.printer.emergencyHoldReleased()
                    }
                }
            }
        }

        // The emergency stop is a permanently visible section pinned to the
        // bottom of the Monitor panel, outside the status scroll.
        Column {
            id: emergencyDock
            z: 20
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: UM.Theme.getSize("default_margin").width
            anchors.rightMargin: UM.Theme.getSize("default_margin").width
            anchors.bottomMargin: UM.Theme.getSize("default_margin").height
            spacing: UM.Theme.getSize("thin_margin").height

            Rectangle {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }
            UM.Label {
                width: parent.width
                text: "Emergency"
                font: UM.Theme.getFont("medium_bold")
            }
            Loader {
                width: parent.width
                height: 46 * screenScaleFactor
                sourceComponent: emergencyButtonComponent
            }
        }

        RowLayout {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: emergencyDock.top
            spacing: 0

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 640 * screenScaleFactor
                sourceComponent: baseMonitorComponent
            }

            Cura.RoundedRectangle {
                // Collapsed, the pane shrinks to the toggle button and its
                // margins; the vertical title below explains the strip.
                Layout.preferredWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 390 * screenScaleFactor)
                Layout.minimumWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 340 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 390 * screenScaleFactor)
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: UM.Theme.getSize("default_margin").width
                Layout.leftMargin: 0
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

                // While collapsed, a click anywhere on the strip expands
                // the pane; the header button stays on top of this area.
                MouseArea {
                    visible: root.controlsCollapsed
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (root.printer != null) {
                            root.printer.setControlsCollapsed(false);
                        }
                    }
                }

                RowLayout {
                    id: controlHeader
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
                    anchors.leftMargin: UM.Theme.getSize("thin_margin").width
                    anchors.rightMargin: UM.Theme.getSize("thin_margin").width
                    spacing: UM.Theme.getSize("thin_margin").width
                    // The pane title stays frozen in the header row; the
                    // lock sits beside it and the collapse toggle hugs the
                    // edge the pane collapses into (right, as the rightmost
                    // pane).
                    UM.Label {
                        Layout.fillWidth: true
                        visible: !root.controlsCollapsed
                        text: "Printer controls"
                        font: UM.Theme.getFont("large_bold")
                        elide: Text.ElideRight
                    }
                    // Lock/unlock as a padlock glyph; the state is the
                    // icon (closed = locked), the tooltip carries the action.
                    UM.SimpleButton {
                        id: lockButton
                        visible: !root.controlsCollapsed
                        Layout.alignment: Qt.AlignVCenter
                        width: 28 * screenScaleFactor
                        height: 28 * screenScaleFactor
                        color: root.printer != null && root.printer.controlsLocked ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
                        hoverColor: root.printer != null && root.printer.controlsLocked ? UM.Theme.getColor("primary") : UM.Theme.getColor("text")
                        iconSource: root.printer != null && root.printer.controlsLocked ? Qt.resolvedUrl("PadlockLocked.svg") : Qt.resolvedUrl("PadlockUnlocked.svg")
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setControlsLocked(!root.printer.controlsLocked);
                            }
                        }

                        UM.TooltipArea {
                            anchors.fill: parent
                            text: root.printer != null && root.printer.controlsLocked ? "Unlock all controls." : "Lock all controls."
                            acceptedButtons: Qt.NoButton
                        }
                    }
                    Cura.SecondaryButton {
                        id: collapseButton
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        // Square at the OLD button width: the theme adds
                        // its padding around the 32px content, so the
                        // height tracks the rendered width (the
                        // author's ruling).
                        width: 28 * screenScaleFactor
                        iconSize: 12 * screenScaleFactor
                        height: width
                        implicitHeight: width
                        // The SAME theme-chevron family as the monitor's
                        // pane toggles (the author's ruling: all pane
                        // collapse buttons uniform).
                        iconSource: root.controlsCollapsed ? UM.Theme.getIcon("ChevronSingleLeft") : UM.Theme.getIcon("ChevronSingleRight")
                        tooltip: root.controlsCollapsed ? "Show the printer controls." : "Hide the printer controls."
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setControlsCollapsed(!root.controlsCollapsed);
                            }
                        }
                    }
                }

                Flickable {
                    id: controlFlick
                    visible: !root.controlsCollapsed
                    anchors.top: controlHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width
                    anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                    clip: true
                    contentWidth: width
                    contentHeight: controlContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: !root.tuningSliderPressed
                    ScrollBar.vertical: UM.ScrollBar {
                        id: controlScrollbar
                    }

                    ColumnLayout {
                        id: controlContent
                        // The attached scrollbar OVERLAYS the content, and
                        // its own width swings with visibility — which
                        // depends on this column's height. Subtracting it
                        // here closed the loop (the dashboard's layout
                        // polish loop in every capture).
                        width: controlFlick.width - UM.Theme.getSize("default_margin").width
                        // Spacing lives on the children, not the layout: a
                        // collapsed section's hidden content must contribute
                        // nothing, so stacked headers sit flush like Cura's.
                        spacing: 0

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "File manager"
                            sectionId: "fileManager"
                            sectionIconUrl: Qt.resolvedUrl("Download.svg")
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["fileManager"] !== false
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            UM.Label {
                                Layout.fillWidth: true
                                height: 36 * screenScaleFactor
                                text: "Browse, print and manage the printer's gcode files."
                                color: UM.Theme.getColor("text_inactive")
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                            }
                            Cura.SecondaryButton {
                                Layout.fillWidth: true
                                text: "File manager"
                                enabled: root.printer != null && root.printer.monitorConnected
                                onClicked: {
                                    if (root.printer != null) {
                                        root.printer.setFileManagerOpen(true);
                                    }
                                }
                                UM.TooltipArea {
                                    anchors.fill: parent
                                    acceptedButtons: Qt.NoButton
                                    text: root.printer != null && root.printer.monitorConnected ? "Open the file manager." : "The printer is disconnected."
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Print"
                            sectionId: "print"
                            sectionIcon: "Printer"
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["print"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            UM.Label {
                                // A permanent state caption gives the
                                // always-present action row context (the
                                // UX panel: a dead Pause|Resume|Cancel
                                // band with no caption read as an error
                                // state on idle printers).
                                height: 36 * screenScaleFactor
                                text: root.printer != null ? (root.printer.printActive ? (root.printer.canResumePrint ? "Paused" : "Printing") : "Idle") : ""
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                            }

                            RowLayout {
                                // NO-REFLOW RULE (the author's ruling):
                                // the action buttons never disappear —
                                // they disable. The row used to vanish
                                // entirely when no action applied and
                                // reappear mid-session, reflowing every
                                // control beneath it (the jog-reflow
                                // hazard).
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Pause"
                                    enabled: root.printer != null && root.printer.canPausePrint
                                    onClicked: root.printer.pausePrint()
                                }

                                Cura.PrimaryButton {
                                    Layout.fillWidth: true
                                    text: "Resume"
                                    enabled: root.printer != null && root.printer.canResumePrint
                                    onClicked: root.printer.resumePrint()
                                }

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Cancel"
                                    enabled: root.printer != null && root.printer.canCancelPrint
                                    onClicked: cancelPrintDialog.open()
                                }
                            }

                            GridLayout {
                                columns: 2
                                Layout.fillWidth: true
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                UM.Label {
                                    text: "Layer height"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorLayerHeight : "—"
                                    Layout.fillWidth: true
                                }
                                UM.Label {
                                    text: "Current Z offset"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.zOffsetText : "—"
                                    Layout.fillWidth: true
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Setup"
                            sectionId: "setup"
                            sectionIcon: "House"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["setup"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home"
                                    enabled: root.printer != null && root.printer.canRunSetup
                                    onClicked: root.printer.homeAll()
                                }
                                Cura.SecondaryButton {
                                    // Capability-static gate (the UX
                                    // panel's ruling): QGL support never
                                    // changes mid-session — only on a
                                    // printer switch, which is
                                    // user-initiated — so the button may
                                    // stay visibility-gated instead of
                                    // reading as permanently broken on
                                    // machines without quad gantry.
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.hasQuadGantryLevel
                                    text: "QGL"
                                    tooltip: "Level the quad gantry."
                                    enabled: root.printer != null && root.printer.canRunSetup
                                    onClicked: root.printer.runQuadGantryLevel()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.hasBedMesh
                                    text: "Calibrate mesh"
                                    tooltip: "Probe the bed now and replace the active mesh with a newly calibrated one."
                                    enabled: root.printer != null && root.printer.canRunSetup
                                    onClicked: root.printer.calibrateBedMesh()
                                }
                            }

                            RowLayout {
                                // Capability-static gate (see the QGL
                                // button): visibility, not enabled.
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.hasBedMesh
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                Cura.ComboBox {
                                    id: bedMeshProfileSelector
                                    Layout.fillWidth: true
                                    model: root.printer != null ? root.printer.bedMeshProfileNames : []
                                    enabled: root.printer != null && root.printer.canRunSetup && root.printer.bedMeshProfileNames.length > 0
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Load saved mesh"
                                    enabled: root.printer != null && root.printer.canRunSetup && bedMeshProfileSelector.currentText.length > 0
                                    tooltip: "Load the selected saved Klipper bed mesh without probing the bed again."
                                    onClicked: root.printer.loadBedMeshProfile(bedMeshProfileSelector.currentText)
                                }

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Clear mesh"
                                    enabled: root.printer != null && root.printer.canRunSetup && root.printer.bedMeshAvailable
                                    tooltip: "Clear the active Klipper bed mesh and remove its Z adjustment."
                                    onClicked: root.printer.clearBedMesh()
                                }
                            }
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    text: "Status"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    // One labelled row instead of two
                                    // always-present blank slots (the
                                    // author's "white space at the
                                    // bottom of Setup" report). The
                                    // emdash is the honest empty value.
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null ? (root.printer.printActive ? "Setup disabled during a print" : (root.printer.hasBedMesh && root.printer.bedMeshProfileNames.length === 0 ? "No saved bed mesh profiles" : "—")) : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    wrapMode: Text.NoWrap
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        text: root.printer != null ? (root.printer.printActive ? "Homing and bed-mesh setup controls are disabled during a print." : (root.printer.hasBedMesh && root.printer.bedMeshProfileNames.length === 0 ? "No saved bed mesh profiles reported by Klipper." : "")) : ""
                                        acceptedButtons: Qt.NoButton
                                    }
                                }
                            }
                        }
                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Toolhead"
                            sectionId: "toolhead"
                            sectionIcon: "Nozzle"
                        }
                        ColumnLayout {
                            id: toolheadSection
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.sectionExpandedMap["toolhead"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2
                            readonly property var jogPresets: [0.1, 0.5, 1, 5, 10, 25, 50, 100, 125]

                            // The readout leads the section: current state
                            // first, controls below. Two rows on purpose —
                            // the one-line readout wrapped at narrow widths
                            // and made the content jump. Elide instead of
                            // wrap so the section height never changes.
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                Layout.fillWidth: true

                                UM.Label {
                                    text: "Homed"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                Row {
                                    Layout.fillWidth: true
                                    spacing: 4 * screenScaleFactor
                                    UM.Label {
                                        text: root.printer != null && root.printer.homedAxes.length > 0 ? root.printer.homedAxes.toUpperCase().split('').join(' ') + "  · " : "—"
                                        color: UM.Theme.getColor("text")
                                    }
                                    // The abs/rel toggle (the author's
                                    // live ruling: the mode TEXT is the
                                    // control, never a separate button)
                                    // — clicking the word switches and
                                    // sends the real G90/G91 through the
                                    // command lane.
                                    UM.Label {
                                        text: root.printer != null ? root.printer.positionMode : "Absolute"
                                        color: root.printer != null && root.printer.jogEnabled ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: root.printer != null && root.printer.jogEnabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                            onClicked: {
                                                // The click itself must obey the
                                                // SAME gate as the styling: while
                                                // the controls are locked the word
                                                // reads, it must not act (the
                                                // author's catch).
                                                if (root.printer != null && root.printer.jogEnabled) {
                                                    root.printer.setPositionMode(root.printer.positionMode !== "Absolute");
                                                }
                                            }
                                        }
                                    }
                                    UM.Label {
                                        text: " moves"
                                        color: UM.Theme.getColor("text")
                                    }
                                }
                            }
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                Layout.fillWidth: true

                                UM.Label {
                                    text: "Position"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    // The grey-label / black-value
                                    // readout pattern (the author's
                                    // ruling, after the MCUs section),
                                    // with an honest emdash when there
                                    // is no value.
                                    Layout.fillWidth: true
                                    text: root.printer != null ? root.printer.monitorPosition : "—"
                                    color: UM.Theme.getColor("text")
                                    elide: Text.ElideRight
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                UM.Label {
                                    text: "Move distance"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                Cura.ComboBox {
                                    id: jogDistanceSelector
                                    Layout.fillWidth: true
                                    model: toolheadSection.jogPresets
                                    currentIndex: toolheadSection.jogPresets.indexOf(root.printer != null ? root.printer.jogDistance : 25)
                                    onActivated: function (index) {
                                        if (root.printer != null) {
                                            root.printer.setJogDistance(toolheadSection.jogPresets[index]);
                                        }
                                    }
                                }
                                Cura.TextField {
                                    id: jogDistanceField
                                    Layout.fillWidth: true
                                    text: root.printer != null ? root.printer.jogDistance.toString() : ""
                                    onEditingFinished: {
                                        var value = parseFloat(jogDistanceField.text);
                                        if (root.printer != null) {
                                            root.printer.setJogDistance(value);
                                            jogDistanceField.text = root.printer.jogDistance.toString();
                                        }
                                    }
                                }
                                UM.Label {
                                    text: "mm"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 3
                                    columnSpacing: UM.Theme.getSize("thin_margin").width
                                    rowSpacing: UM.Theme.getSize("thin_margin").height
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "↑ Y"

                                        tooltip: "Move the toolhead towards the Y maximum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("y", 1)
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "← X"

                                        tooltip: "Move the toolhead towards the X minimum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("x", -1)
                                    }
                                    // The compass centre is deliberately
                                    // empty (the old Home-all button used
                                    // to live here).
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "→ X"

                                        tooltip: "Move the toolhead towards the X maximum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("x", 1)
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "↓ Y"

                                        tooltip: "Move the toolhead towards the Y minimum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("y", -1)
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                }
                                ColumnLayout {
                                    spacing: UM.Theme.getSize("thin_margin").height
                                    PreviewSecondaryButton {

                                        text: "↑ Z"

                                        tooltip: "Move the toolhead up."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("z", 1)
                                    }
                                    PreviewSecondaryButton {

                                        text: "↓ Z"

                                        tooltip: "Move the toolhead down."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("z", -1)
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home X"
                                    tooltip: "Home the X axis."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.home("x")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home Y"
                                    tooltip: "Home the Y axis."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.home("y")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home Z"
                                    tooltip: "Home the Z axis."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.home("z")
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Centre toolhead"
                                    tooltip: "Move X and Y to the build plate centre, 5 cm above the plate."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.centerToolhead()
                                }
                                Cura.SecondaryButton {
                                    text: "Z to 0"
                                    tooltip: "Move Z down to 0, the bed level after homing."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.zToZero()
                                }
                                Cura.SecondaryButton {
                                    text: "Motors off"
                                    tooltip: "Disable the stepper motors so the toolhead can be moved by hand."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.motorsOff()
                                }
                            }

                            UM.Label {
                                text: "Extrusion"
                                font: UM.Theme.getFont("medium_bold")
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                // The selected distance keeps its
                                // highlight: the primary face shows
                                // while it IS the selection (the
                                // author's live report — the boxes
                                // never stayed highlighted).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeDistance5Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeDistance5Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeDistance === 5
                                        text: "5"
                                        tooltip: "Extrude distance: 5 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(5)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeDistance !== 5
                                        text: "5"
                                        tooltip: "Extrude distance: 5 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(5)
                                    }
                                }
                                // The selected distance keeps its
                                // highlight: the primary face shows
                                // while it IS the selection (the
                                // author's live report — the boxes
                                // never stayed highlighted).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeDistance10Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeDistance10Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeDistance === 10
                                        text: "10"
                                        tooltip: "Extrude distance: 10 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(10)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeDistance !== 10
                                        text: "10"
                                        tooltip: "Extrude distance: 10 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(10)
                                    }
                                }
                                // The selected distance keeps its
                                // highlight: the primary face shows
                                // while it IS the selection (the
                                // author's live report — the boxes
                                // never stayed highlighted).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeDistance25Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeDistance25Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeDistance === 25
                                        text: "25"
                                        tooltip: "Extrude distance: 25 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(25)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeDistance !== 25
                                        text: "25"
                                        tooltip: "Extrude distance: 25 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(25)
                                    }
                                }
                                // The selected distance keeps its
                                // highlight: the primary face shows
                                // while it IS the selection (the
                                // author's live report — the boxes
                                // never stayed highlighted).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeDistance75Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeDistance75Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeDistance === 75
                                        text: "75"
                                        tooltip: "Extrude distance: 75 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(75)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeDistance !== 75
                                        text: "75"
                                        tooltip: "Extrude distance: 75 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(75)
                                    }
                                }
                                // The selected distance keeps its
                                // highlight: the primary face shows
                                // while it IS the selection (the
                                // author's live report — the boxes
                                // never stayed highlighted).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeDistance100Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeDistance100Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeDistance === 100
                                        text: "100"
                                        tooltip: "Extrude distance: 100 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(100)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeDistance !== 100
                                        text: "100"
                                        tooltip: "Extrude distance: 100 mm."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeDistance(100)
                                    }
                                }
                                UM.Label {
                                    // The unit rides the row, like the
                                    // speed row's "mm/s" (the author's
                                    // ruling) — the free-text length
                                    // box was dropped as unnecessary.
                                    text: "mm"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Extrude"
                                    tooltip: "Extrude the configured distance at the configured speed."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.extrude(1)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Retract"
                                    tooltip: "Retract the configured distance at the configured speed."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.extrude(-1)
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                UM.Label {
                                    text: "Speed"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                // Same highlight pattern as the
                                // distance row (the author's live
                                // report).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeSpeed60Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeSpeed60Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeSpeed === 60
                                        text: "1"
                                        tooltip: "Extrusion speed: 1 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(60)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeSpeed !== 60
                                        text: "1"
                                        tooltip: "Extrusion speed: 1 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(60)
                                    }
                                }
                                // Same highlight pattern as the
                                // distance row (the author's live
                                // report).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeSpeed120Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeSpeed120Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeSpeed === 120
                                        text: "2"
                                        tooltip: "Extrusion speed: 2 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(120)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeSpeed !== 120
                                        text: "2"
                                        tooltip: "Extrusion speed: 2 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(120)
                                    }
                                }
                                // Same highlight pattern as the
                                // distance row (the author's live
                                // report).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeSpeed300Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeSpeed300Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeSpeed === 300
                                        text: "5"
                                        tooltip: "Extrusion speed: 5 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(300)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeSpeed !== 300
                                        text: "5"
                                        tooltip: "Extrusion speed: 5 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(300)
                                    }
                                }
                                // Same highlight pattern as the
                                // distance row (the author's live
                                // report).
                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: extrudeSpeed1500Primary.implicitHeight
                                    Cura.PrimaryButton {
                                        id: extrudeSpeed1500Primary
                                        anchors.fill: parent
                                        visible: root.printer != null && root.printer.extrudeSpeed === 1500
                                        text: "25"
                                        tooltip: "Extrusion speed: 25 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(1500)
                                    }
                                    Cura.SecondaryButton {
                                        anchors.fill: parent
                                        visible: root.printer == null || root.printer.extrudeSpeed !== 1500
                                        text: "25"
                                        tooltip: "Extrusion speed: 25 mm/s."
                                        enabled: root.printer != null && root.printer.jogEnabled
                                        onClicked: root.printer.setExtrudeSpeed(1500)
                                    }
                                }
                                UM.Label {
                                    text: "mm/s"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            // The endstop readout sits BELOW the jog pad
                            // (the panel): the chips arrive at the first
                            // homing of a session, and appearing above
                            // the pad shifted it under the pointer — the
                            // original hazard family. It is a tri-state:
                            // values only exist after the first homing,
                            // so an empty list says so instead of
                            // reading as a bug; TRIGGERED axes get the
                            // normal text colour, open axes stay muted.
                            UM.Label {
                                // Pin names like STEPPER_X are not
                                // self-evidently endstops — the block
                                // gets its own bold title, like the
                                // MCUs section (the author's ruling).
                                // The title stays always: it labels the
                                // chips too.
                                text: "Endstops"
                                font: UM.Theme.getFont("medium_bold")
                                color: UM.Theme.getColor("text")
                            }
                            UM.Label {
                                Layout.fillWidth: true
                                height: 36 * screenScaleFactor
                                // The summary line yields to the chips
                                // once they exist — they ARE the
                                // readout, and a bare emdash beside
                                // them read as an error (the author's
                                // live report). The chips sit below the
                                // jog pad, so this follows their
                                // accepted appearance carve-out.
                                visible: root.printer == null || root.printer.endstopItems.length === 0
                                text: root.printer != null ? (root.printer.endstopSummary.length > 0 ? root.printer.endstopSummary : "—") : "—"
                                color: UM.Theme.getColor("text")
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width
                                Repeater {
                                    model: root.printer != null ? root.printer.endstopItems : []
                                    UM.Label {
                                        text: modelData.name + ": " + modelData.state
                                        color: modelData.triggered ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                                    }
                                }
                            }

                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    text: "Status"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    // NO-REFLOW RULE: permanent slot,
                                    // fixed single-line height — the
                                    // text changes, never the layout.
                                    // Grey label, black value (the MCUs
                                    // pattern), an honest emdash when
                                    // nothing applies. The full sentence
                                    // lives in the tooltip: elide must
                                    // never hide the safety clause (the
                                    // panel).
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null ? (root.printer.canResumePrint ? "Paused — moves run immediately" : root.printer.canPausePrint ? "Moves disabled — pause first" : "—") : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    wrapMode: Text.NoWrap
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null ? (root.printer.canResumePrint ? "Printer is paused — moves run immediately; a print resumes from Klipper's recorded position." : root.printer.canPausePrint ? "Toolhead moves are disabled during a print — pause first." : "") : ""
                                        acceptedButtons: Qt.NoButton
                                    }
                                }

                                UM.Label {
                                    // The jog feedback row RESERVES its
                                    // space at all times and fades in
                                    // only when it has something to say
                                    // — an idle "Jog —" is noise (the
                                    // author's live report), but hiding
                                    // the row would be a reflow (the
                                    // author's rule), so opacity, never
                                    // visibility.
                                    opacity: root.printer != null && root.printer.jogStatus.length > 0 ? 1 : 0
                                    text: "Jog"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    // The jog feedback keeps its own row:
                                    // the pause-timeout and resumed-drop
                                    // errors must never hide behind the
                                    // state hint (the panel's top
                                    // finding — queued moves were
                                    // cancelled silently).
                                    opacity: root.printer != null && root.printer.jogStatus.length > 0 ? 1 : 0
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null ? root.printer.jogStatus : ""
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    wrapMode: Text.NoWrap
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Macros"
                            sectionId: "macros"
                            sectionIcon: "Function"
                        }
                        ColumnLayout {
                            id: macroSection
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.macroNames.length > 0 && root.printer.sectionExpandedMap["macros"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            Cura.ComboBox {
                                id: macroSelector
                                Layout.fillWidth: true
                                model: root.printer != null ? root.printer.macroNames : []
                                onCurrentTextChanged: root.refreshMacroParameters()
                                Component.onCompleted: root.refreshMacroParameters()
                            }

                            Repeater {
                                id: macroParameterRepeater
                                model: root.macroParameters

                                ColumnLayout {
                                    id: argumentRow
                                    Layout.fillWidth: true
                                    property string argumentName: String(modelData.name)
                                    property bool argumentRequired: Boolean(modelData.required)
                                    property string argumentValue: String(modelData.default)
                                    spacing: 2 * screenScaleFactor

                                    UM.Label {
                                        Layout.fillWidth: true
                                        text: argumentRow.argumentName + "  (" + String(modelData.type) + (argumentRow.argumentRequired ? ", required" : "") + ")"
                                        color: UM.Theme.getColor("text_inactive")
                                    }

                                    Cura.ComboBox {
                                        id: boolInput
                                        Layout.fillWidth: true
                                        visible: String(modelData.type) === "bool"
                                        model: Boolean(modelData.hasDefault) ? ["Use macro default", "True", "False"] : ["True", "False"]
                                        currentIndex: {
                                            if (!Boolean(modelData.hasDefault))
                                                return String(modelData.default).toLowerCase() === "false" ? 1 : 0;
                                            return 0;
                                        }
                                        onCurrentTextChanged: {
                                            argumentRow.argumentValue = currentText === "Use macro default" ? "" : currentText;
                                        }
                                    }

                                    IntValidator {
                                        id: integerValidator
                                    }
                                    DoubleValidator {
                                        id: floatingValidator
                                        notation: DoubleValidator.StandardNotation
                                    }

                                    Cura.TextField {
                                        id: typedInput
                                        Layout.fillWidth: true
                                        visible: String(modelData.type) !== "bool"
                                        text: String(modelData.default)
                                        placeholderText: argumentRow.argumentRequired ? "Required" : "Optional"
                                        selectByMouse: true
                                        validator: String(modelData.type) === "int" ? integerValidator : (String(modelData.type) === "float" ? floatingValidator : null)
                                        onTextChanged: argumentRow.argumentValue = text
                                    }
                                }
                            }

                            UM.Label {
                                visible: root.macroParameters.length === 0
                                text: "This macro has no detectable params.* inputs."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            Cura.SecondaryButton {
                                Layout.fillWidth: true
                                text: "Run macro"
                                enabled: root.printer != null && macroSelector.currentIndex >= 0 && !root.printer.actionBusy && !root.printer.printActive && root.macroArgumentsValid()
                                onClicked: root.printer.runMacro(macroSelector.currentText, root.macroArgumentString())
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Temperature profiles"
                            sectionId: "profiles"
                            sectionIcon: "PrintQuality"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.temperaturePresetItems.length > 0 && root.printer.sectionExpandedMap["profiles"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            Repeater {
                                model: root.printer != null ? root.printer.temperaturePresetItems : []
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: modelData.active ? "Active — " + modelData.name : modelData.name
                                    enabled: root.printer != null && root.printer.canApplyTemperaturePreset
                                    onClicked: root.printer.applyTemperaturePreset(modelData.index)
                                }
                            }
                            Cura.SecondaryButton {
                                Layout.fillWidth: true
                                text: "Cooldown"
                                tooltip: "Turn every heater off: all targets to 0 °C."
                                enabled: root.printer != null && root.printer.canApplyTemperaturePreset
                                onClicked: root.printer.heatersOff()
                            }
                            UM.Label {
                                text: "A profile is marked Active only when all of its enabled heater targets match the printer. G-code-only profiles are never assumed active."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: "Status"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null && root.printer.printActive ? "Disabled during a print" : "—"
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null && root.printer.printActive ? "Temperature profiles are disabled during a print, matching Mainsail." : ""
                                        acceptedButtons: Qt.NoButton
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Live tuning"
                            sectionId: "tuning"
                            sectionIcon: "Sliders"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["tuning"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height
                            UM.Label {
                                text: "Drag to preview a value. After release, the latest value is applied once it has been unchanged for 250 ms."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: "Speed factor"
                                        Layout.fillWidth: true
                                    }
                                    UM.Label {
                                        text: root.sliderSelection(speedSlider) + "%"
                                    }
                                }
                                OutlineSlider {
                                    id: speedSlider
                                    Layout.fillWidth: true
                                    from: 10
                                    to: Math.max(200, root.printer != null ? Math.ceil(root.printer.speedFactorPercent * 2) : 200)
                                    stepSize: 1
                                    live: false
                                    value: root.printer != null ? root.printer.speedFactorPercent : 100
                                    enabled: root.printer != null
                                    onMoved: {
                                        if (root.printer == null)
                                            return;
                                        var selected = root.sliderSelection(speedSlider);
                                        root.printer.previewSpeedFactor(selected);
                                        if (!pressed)
                                            root.printer.setSpeedFactor(selected);
                                    }
                                    onPressedChanged: {
                                        root.tuningSliderPressed = pressed;
                                        if (!pressed && root.printer != null)
                                            root.printer.setSpeedFactor(root.sliderSelection(speedSlider));
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: "Extrusion multiplier"
                                        Layout.fillWidth: true
                                    }
                                    UM.Label {
                                        text: root.sliderSelection(flowSlider) + "%"
                                    }
                                }
                                OutlineSlider {
                                    id: flowSlider
                                    Layout.fillWidth: true
                                    from: 50
                                    to: Math.max(200, root.printer != null ? Math.ceil(root.printer.flowFactorPercent * 2) : 200)
                                    stepSize: 1
                                    live: false
                                    value: root.printer != null ? root.printer.flowFactorPercent : 100
                                    enabled: root.printer != null
                                    onMoved: {
                                        if (root.printer == null)
                                            return;
                                        var selected = root.sliderSelection(flowSlider);
                                        root.printer.previewFlowFactor(selected);
                                        if (!pressed)
                                            root.printer.setFlowFactor(selected);
                                    }
                                    onPressedChanged: {
                                        root.tuningSliderPressed = pressed;
                                        if (!pressed && root.printer != null)
                                            root.printer.setFlowFactor(root.sliderSelection(flowSlider));
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").height / 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: "Z-offset nudges"
                                        font: UM.Theme.getFont("medium_bold")
                                        Layout.fillWidth: true
                                    }
                                    UM.Label {
                                        text: root.printer != null ? "Current " + root.printer.zOffsetText : "Current —"
                                        font: UM.Theme.getFont("medium_bold")
                                    }
                                }
                                Column {
                                    id: zOffsetGrid
                                    Layout.fillWidth: true
                                    spacing: 2 * screenScaleFactor
                                    property real buttonSpacing: 2 * screenScaleFactor

                                    // The original two-row layout: all up
                                    // nudges on the top row, all down on
                                    // the bottom. Each button takes an
                                    // exact quarter of the row: fillWidth
                                    // alone leaves each button its label's
                                    // implicit width as a base, and the
                                    // layout shares the leftover in
                                    // proportion — "↑ 0.005" and "↑ 0.05"
                                    // came out different widths (the
                                    // author's report). A bound preferred
                                    // width — (row - 3 gaps) / 4 — makes
                                    // every button the same width without
                                    // depending on layout distribution.
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: zOffsetGrid.buttonSpacing
                                        Repeater {
                                            model: [0.005, 0.01, 0.025, 0.05]
                                            Cura.SecondaryButton {
                                                Layout.fillWidth: true
                                                Layout.preferredWidth: (zOffsetGrid.width - 3 * zOffsetGrid.buttonSpacing) / 4
                                                height: UM.Theme.getSize("action_button").height
                                                text: "↑ " + modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                                tooltip: "Moves the nozzle up, away from the bed."
                                                enabled: root.printer != null && !root.printer.actionBusy
                                                onClicked: root.printer.adjustZOffset(modelData)
                                            }
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: zOffsetGrid.buttonSpacing
                                        Repeater {
                                            model: [-0.005, -0.01, -0.025, -0.05]
                                            Cura.SecondaryButton {
                                                Layout.fillWidth: true
                                                Layout.preferredWidth: (zOffsetGrid.width - 3 * zOffsetGrid.buttonSpacing) / 4
                                                height: UM.Theme.getSize("action_button").height
                                                text: "↓ " + Math.abs(modelData).toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                                tooltip: "Moves the nozzle down, closer to the bed."
                                                enabled: root.printer != null && !root.printer.actionBusy
                                                onClicked: root.printer.adjustZOffset(modelData)
                                            }
                                        }
                                    }
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Clear Z offset"
                                    enabled: root.printer != null && !root.printer.actionBusy
                                    onClicked: root.printer.clearZOffset()
                                }
                            }
                        }
                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Fan speed"
                            sectionId: "fans"
                            sectionIcon: "Fan"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.fanControlItems.length > 0 && root.printer.sectionExpandedMap["fans"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            Repeater {
                                model: root.printer != null ? root.printer.fanControlItems : []
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(fanSlider) + "%"
                                        }
                                    }
                                    OutlineSlider {
                                        id: fanSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onMoved: {
                                            if (root.printer == null)
                                                return;
                                            var selected = root.sliderSelection(fanSlider);
                                            root.printer.previewFanSpeed(modelData.object, selected);
                                            if (!pressed)
                                                root.printer.setFanSpeed(modelData.object, selected);
                                        }
                                        onPressedChanged: {
                                            root.tuningSliderPressed = pressed;
                                            if (!pressed && root.printer != null)
                                                root.printer.setFanSpeed(modelData.object, root.sliderSelection(fanSlider));
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "LEDs"
                            sectionId: "leds"
                            sectionIcon: "Star"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.ledItems.length > 0 && root.printer.sectionExpandedMap["leds"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("thin_margin").height
                            Repeater {
                                model: root.printer != null ? root.printer.ledItems : []
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: UM.Theme.getSize("thin_margin").height

                                    function previewLedColour() {
                                        if (root.printer == null)
                                            return;
                                        root.printer.previewLedColor(modelData.object, root.sliderSelection(redSlider), root.sliderSelection(greenSlider), root.sliderSelection(blueSlider), modelData.hasWhite ? root.sliderSelection(whiteSlider) : 0, root.sliderSelection(ledSlider));
                                    }

                                    function applyLedColour() {
                                        if (root.printer == null)
                                            return;
                                        root.printer.setLedColor(modelData.object, root.sliderSelection(redSlider), root.sliderSelection(greenSlider), root.sliderSelection(blueSlider), modelData.hasWhite ? root.sliderSelection(whiteSlider) : 0, root.sliderSelection(ledSlider));
                                    }

                                    function ledColourMoved(slider) {
                                        previewLedColour();
                                        if (!slider.pressed)
                                            applyLedColour();
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: "Brightness " + root.sliderSelection(ledSlider) + "%"
                                        }
                                    }
                                    OutlineSlider {
                                        id: ledSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onMoved: {
                                            if (root.printer == null)
                                                return;
                                            var selected = root.sliderSelection(ledSlider);
                                            root.printer.previewLedBrightness(modelData.object, selected);
                                            if (!pressed)
                                                root.printer.setLedBrightness(modelData.object, selected);
                                        }
                                        onPressedChanged: {
                                            root.tuningSliderPressed = pressed;
                                            if (!pressed && root.printer != null)
                                                root.printer.setLedBrightness(modelData.object, root.sliderSelection(ledSlider));
                                        }
                                    }

                                    GridLayout {
                                        columns: 3
                                        Layout.fillWidth: true
                                        columnSpacing: UM.Theme.getSize("thin_margin").width
                                        rowSpacing: UM.Theme.getSize("thin_margin").height

                                        UM.Label {
                                            text: "R"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: redSlider
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.redPercent
                                            onMoved: ledColourMoved(redSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(redSlider) + "%"
                                        }

                                        UM.Label {
                                            text: "G"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: greenSlider
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.greenPercent
                                            onMoved: ledColourMoved(greenSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(greenSlider) + "%"
                                        }

                                        UM.Label {
                                            text: "B"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: blueSlider
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.bluePercent
                                            onMoved: ledColourMoved(blueSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(blueSlider) + "%"
                                        }

                                        UM.Label {
                                            visible: modelData.hasWhite
                                            text: "W"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: whiteSlider
                                            visible: modelData.hasWhite
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.whitePercent
                                            onMoved: ledColourMoved(whiteSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            visible: modelData.hasWhite
                                            text: root.sliderSelection(whiteSlider) + "%"
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "PWM outputs"
                            sectionId: "pwm"
                            sectionIcon: "ThreeDots"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.pwmOutputItems.length > 0 && root.printer.sectionExpandedMap["pwm"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            Repeater {
                                model: root.printer != null ? root.printer.pwmOutputItems : []
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(pwmSlider) + "%"
                                        }
                                    }
                                    OutlineSlider {
                                        id: pwmSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onMoved: {
                                            if (root.printer == null)
                                                return;
                                            var selected = root.sliderSelection(pwmSlider);
                                            root.printer.previewPwmOutput(modelData.object, selected);
                                            if (!pressed)
                                                root.printer.setPwmOutput(modelData.object, selected);
                                        }
                                        onPressedChanged: {
                                            root.tuningSliderPressed = pressed;
                                            if (!pressed && root.printer != null) {
                                                root.printer.setPwmOutput(modelData.object, root.sliderSelection(pwmSlider));
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Power"
                            sectionId: "power"
                            sectionIconUrl: Qt.resolvedUrl("Power.svg")
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.powerDevices.length > 0 && root.printer.sectionExpandedMap["power"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            Repeater {
                                model: root.printer != null ? root.printer.powerDevices : []
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: modelData.name + "  · " + modelData.status
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Cura.SecondaryButton {
                                        enabled: root.printer != null && modelData.can_toggle && !root.printer.actionBusy
                                        text: modelData.status === "on" ? "Turn off" : "Turn on"
                                        fixedWidthMode: true
                                        width: 104 * screenScaleFactor
                                        onClicked: {
                                            if (modelData.status === "on" && root.printer.printActive) {
                                                powerOffDialog.deviceName = modelData.name;
                                                powerOffDialog.open();
                                            } else {
                                                root.printer.setPowerDevice(modelData.name, modelData.status !== "on");
                                            }
                                        }
                                    }
                                }
                            }
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: "Status"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: root.anyPowerLocked ? "Locked during this print" : "—"
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.anyPowerLocked ? "Power control is locked by Moonraker while this print is active." : ""
                                        acceptedButtons: Qt.NoButton
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "System"
                            sectionId: "system"
                            sectionIcon: "Settings"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["system"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Firmware restart"
                                    tooltip: "Restart Klipper's firmware process (FIRMWARE_RESTART)."
                                    enabled: root.printer != null && !root.printer.printActive
                                    onClicked: root.printer.firmwareRestart()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Host restart"
                                    tooltip: "Reboot the host Moonraker runs on (machine/reboot)."
                                    enabled: root.printer != null && !root.printer.printActive
                                    onClicked: root.printer.hostRestart()
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2
                                Cura.SecondaryButton {
                                    // On its own row: three long labels
                                    // in one row crushed each other and
                                    // the text left its bounds (the
                                    // author's live report).
                                    Layout.fillWidth: true
                                    text: "Klipper restart"
                                    tooltip: "Restart Klipper entirely (printer/restart): reloads the config and reconnects the MCU."
                                    enabled: root.printer != null && !root.printer.printActive
                                    onClicked: root.printer.klipperRestart()
                                }
                            }
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: "Status"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null && root.printer.printActive ? "Disabled during a print" : "—"
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null && root.printer.printActive ? "System restarts are disabled during a print." : ""
                                        acceptedButtons: Qt.NoButton
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Configuration changes"
                            sectionId: "save"
                            sectionIcon: "Save"
                        }
                        ColumnLayout {
                            // NO-REFLOW RULE: this whole section used to
                            // pop into existence when Klipper flagged a
                            // pending config (save_config_pending flips
                            // mid-session, e.g. after a mesh calibration),
                            // shoving every section beneath it. It now
                            // always renders — the button disables and
                            // the summary goes quiet when nothing is
                            // pending.
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["save"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || (!root.printer.controlsLocked && root.printer.monitorConnected)
                            Layout.fillWidth: true

                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: "Pending"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null && root.printer.saveConfigSummary.length > 0 ? root.printer.saveConfigSummary : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    wrapMode: Text.NoWrap
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        text: parent.text
                                        acceptedButtons: Qt.NoButton
                                    }
                                }
                            }
                            Cura.PrimaryButton {
                                Layout.fillWidth: true
                                text: "Save configuration"
                                enabled: root.printer != null && root.printer.canSaveConfig
                                onClicked: root.printer.saveConfig()
                            }
                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: "Save"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    height: 36 * screenScaleFactor
                                    text: root.printer != null ? (root.printer.printActive ? "Disabled during a print" : root.printer.canSaveConfig ? "Restarts Klipper" : "—") : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    wrapMode: Text.NoWrap
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null ? (root.printer.printActive ? "SAVE_CONFIG is disabled during a print." : root.printer.canSaveConfig ? "Saving configuration restarts Klipper." : "") : ""
                                        acceptedButtons: Qt.NoButton
                                    }
                                }
                            }
                        }
                    }
                }

                // The collapsed strip: the toggle stays at the top and the
                // pane title reads top-to-bottom directly under it. The
                // wrapper box swaps the label's extents so the rotated
                // text occupies the box exactly, starting at its top.
                Item {
                    id: collapsedTitleBox
                    visible: root.controlsCollapsed
                    // Anchor to the header ROW (a sibling): anchoring to
                    // the toggle inside it is illegal in QML and silently
                    // drops the anchor, which un-pinned the title.
                    anchors.top: controlHeader.bottom
                    anchors.topMargin: UM.Theme.getSize("thin_margin").height
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: collapsedTitle.implicitHeight
                    height: collapsedTitle.implicitWidth
                    UM.Label {
                        id: collapsedTitle
                        text: "Printer controls"
                        font: UM.Theme.getFont("medium_bold")
                        color: UM.Theme.getColor("text_inactive")
                        rotation: 90
                        anchors.centerIn: parent
                    }
                }
            }
        }

        // The file-manager popup: a stage-level sibling of the panes,
        // stopping above the emergency dock so the e-stop stays
        // visible and live behind it (the UX panel's placement ruling).
        FileManager {
            id: fileManagerCard
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: emergencyDock.top
            open: root.fileManagerOpen
            printerModel: root.printer
            onCloseRequested: {
                if (root.printer != null) {
                    root.printer.setFileManagerOpen(false);
                }
            }
        }
    }
}
