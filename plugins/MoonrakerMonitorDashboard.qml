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
        // matter where focus actually landed (the Snapshot
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
        // The freeze lists (the author's live report): while a tuning
        // slider is mid-gesture — a drag or a pending keyboard nudge —
        // the fan/LED/PWM repeaters must not rebuild, or the rebuild
        // replaces the focused delegate and the interaction dies.
        property var frozenFanItems: []
        property var frozenLedItems: []
        property var frozenPwmOutputItems: []
        // The slider that was being tuned when the freeze lifted: the
        // live lists rebuild the repeaters then, so the delegate's
        // focus dies with the rebuild — the dashboard re-grants it
        // once the new delegate exists (the author's live report).
        property string tuningSliderObject: ""
        property string tuningSliderKind: ""
        onTuningSliderPressedChanged: {
            if (tuningSliderPressed && root.printer != null) {
                root.frozenFanItems = root.printer.fanControlItems;
                root.frozenLedItems = root.printer.ledItems;
                root.frozenPwmOutputItems = root.printer.pwmOutputItems;
            }
            if (!tuningSliderPressed && root.tuningSliderObject !== "") {
                refocusTimer.attempts = 0;
                refocusTimer.start();
            }
        }
        // The refocus RETRIES until the walk lands: the repeater
        // rebuild that follows the submit is asynchronous against the
        // unfreeze edge, and a one-shot walk could focus a delegate
        // that dies a moment later (the author's live report — fan
        // and LED sliders lost focus on the apply, the singletons
        // never rebuild).
        Timer {
            id: refocusTimer
            interval: 25
            repeat: true
            property int attempts: 0
            onTriggered: {
                ++refocusTimer.attempts;
                if (root.tuningSliderObject === "") {
                    refocusTimer.stop();
                    refocusTimer.attempts = 0;
                    return;
                }
                if (root.focusTuningSliderOnce() || refocusTimer.attempts >= 20) {
                    refocusTimer.stop();
                    refocusTimer.attempts = 0;
                    // The confirm-time publish can rebuild once more
                    // after the apply; hold the focus across that
                    // window before clearing the target.
                    focusHoldTimer.start();
                }
            }
        }
        Timer {
            id: focusHoldTimer
            interval: 400
            onTriggered: {
                root.focusTuningSliderOnce();
                root.tuningSliderObject = "";
                root.tuningSliderKind = "";
            }
        }
        function focusSliderIn(item, targetObject, targetKind) {
            // Recursive: one LED row holds five sliders, nested a
            // level down in the channel grid.
            if (item == null) {
                return false;
            }
            for (var i = 0; i < item.children.length; ++i) {
                var child = item.children[i];
                if (child != null && child.controlObject === targetObject && child.controlKind === targetKind) {
                    child.forceActiveFocus();
                    return true;
                }
                if (child != null && root.focusSliderIn(child, targetObject, targetKind)) {
                    return true;
                }
            }
            return false;
        }
        function focusTuningSliderOnce() {
            var target = root.tuningSliderObject;
            var kind = root.tuningSliderKind;
            if (target === "" || root.printer == null) {
                return false;
            }
            // The delegates are parented to the REPEATER'S PARENT,
            // not the repeater — iterating the repeater's children
            // found nothing (the probe's reproduction: focus died on
            // the apply and the walk never saw a slider). itemAt is
            // the real delegate accessor.
            for (var r = 0; r < fanRepeater.count; ++r) {
                if (root.focusSliderIn(fanRepeater.itemAt(r), target, kind)) {
                    return true;
                }
            }
            for (var l = 0; l < ledRepeater.count; ++l) {
                if (root.focusSliderIn(ledRepeater.itemAt(l), target, kind)) {
                    return true;
                }
            }
            for (var p = 0; p < pwmRepeater.count; ++p) {
                if (root.focusSliderIn(pwmRepeater.itemAt(p), target, kind)) {
                    return true;
                }
            }
            return false;
        }
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

        MoonrakerMonitor {
            id: baseMonitorComponent
        }

        Component {
            id: emergencyButtonComponent
            Item {
                id: emergencyButton
                objectName: "moonrakerEmergencyButton"
                property int clicks: root.printer != null ? root.printer.emergencyStopClicks : 0
                // The author's ruling (2026-09-10): while DISCONNECTED
                // no Monitor-page control is enabled — the emergency
                // stop included. It dims and refuses input instead of
                // pretending it could fire. The INHERITED Item
                // enabled carries the gate (a redeclaration tripped
                // the engine's member-override warning).
                enabled: root.printer != null && root.printer.monitorConnected
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
                    // by letter (the request).
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
                // Inert; the harness's margin-symmetry pin reads this
                // pane's outer edge.
                objectName: "moonrakerControlsPane"
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
                        objectName: "moonrakerLockButton"
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
                        // pane toggles (the ruling: all pane
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
                        // The attached scrollbar overlays the content, so
                        // the column spans the full width while the bar is
                        // hidden (a constant reservation left a dead band
                        // on the right — the pane's right gap read three
                        // margins wide against the left pane's one, the
                        // harness's margin-symmetry pin). While the bar
                        // IS visible, the column yields its width so the
                        // rows' right edges stay clear of it (the
                        // author's clipping report). Narrower content
                        // only grows taller, so the visibility never
                        // oscillates.
                        width: controlScrollbar.visible ? controlFlick.width - controlScrollbar.width - UM.Theme.getSize("default_margin").width : controlFlick.width
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

                        PrintSection {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onCancelRequested: cancelPrintDialog.open()
                        }

                        SetupSection {
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        ToolheadSection {
                            Layout.fillWidth: true
                            printerModel: root.printer
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
                                    text: root.printer != null ? (root.printer.sectionReason !== "" ? root.printer.sectionReason : (root.printer.printActive ? "Disabled during a print" : "—")) : "—"
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null ? (root.printer.sectionReasonDetail !== "" ? root.printer.sectionReasonDetail : (root.printer.printActive ? "Temperature profiles are disabled during a print, matching Mainsail." : "")) : ""
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
                                        // The fixed width keeps the row from
                                        // reflowing as the percentage changes
                                        // (the author's live report).
                                        width: 52 * screenScaleFactor
                                        horizontalAlignment: Text.AlignRight
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
                                    onValueTuning: {
                                        if (root.printer != null)
                                            root.printer.previewSpeedFactor(value);
                                    }
                                    onValueCommitted: {
                                        if (root.printer != null)
                                            root.printer.setSpeedFactor(value);
                                    }
                                    onInteractingChanged: root.tuningSliderPressed = interacting
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
                                        width: 52 * screenScaleFactor
                                        horizontalAlignment: Text.AlignRight
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
                                    onValueTuning: {
                                        if (root.printer != null)
                                            root.printer.previewFlowFactor(value);
                                    }
                                    onValueCommitted: {
                                        if (root.printer != null)
                                            root.printer.setFlowFactor(value);
                                    }
                                    onInteractingChanged: root.tuningSliderPressed = interacting
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
                                                enabled: root.printer != null && !root.printer.actionBusy && root.printer.sectionReason === ""
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
                                                enabled: root.printer != null && !root.printer.actionBusy && root.printer.sectionReason === ""
                                                onClicked: root.printer.adjustZOffset(modelData)
                                            }
                                        }
                                    }
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Clear Z offset"
                                    enabled: root.printer != null && !root.printer.actionBusy && root.printer.sectionReason === ""
                                    onClicked: root.printer.clearZOffset()
                                }
                            }
                        }
                        MacrosSection {
                            Layout.fillWidth: true
                            printerModel: root.printer
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
                                id: fanRepeater
                                model: root.tuningSliderPressed ? root.frozenFanItems : (root.printer != null ? root.printer.fanControlItems : [])
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
                                            width: 52 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
                                            text: modelData.writable ? root.sliderSelection(fanSlider) + "%" : modelData.percent + "%"
                                        }
                                    }
                                    OutlineSlider {
                                        id: fanSlider
                                        Layout.fillWidth: true
                                        visible: modelData.writable
                                        controlObject: modelData.object
                                        controlKind: "fan"
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onValueTuning: {
                                            if (root.printer != null)
                                                root.printer.previewFanSpeed(modelData.object, value);
                                        }
                                        onValueCommitted: {
                                            if (root.printer != null)
                                                root.printer.setFanSpeed(modelData.object, value);
                                        }
                                        onInteractingChanged: {
                                            root.tuningSliderPressed = interacting;
                                            if (interacting) {
                                                root.tuningSliderObject = modelData.object;
                                                root.tuningSliderKind = "fan";
                                            }
                                        }
                                    }
                                    // Firmware-regulated fans render their
                                    // value without a slider (the author's
                                    // live report — the command never
                                    // sticks).
                                    UM.Label {
                                        visible: !modelData.writable
                                        Layout.fillWidth: true
                                        text: "Firmware-controlled — speed is read-only"
                                        color: UM.Theme.getColor("text_inactive")
                                        font: UM.Theme.getFont("default_italic")
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
                                id: ledRepeater
                                model: root.tuningSliderPressed ? root.frozenLedItems : (root.printer != null ? root.printer.ledItems : [])
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: UM.Theme.getSize("thin_margin").height

                                    function previewLedColour() {
                                        if (root.printer == null)
                                            return;
                                        root.printer.previewLedColor(modelData.object, root.sliderSelection(redSlider), root.sliderSelection(greenSlider), root.sliderSelection(blueSlider), modelData.hasWhite ? root.sliderSelection(whiteSlider) : 0, -1);
                                    }

                                    function applyLedColour() {
                                        if (root.printer == null)
                                            return;
                                        // The channels are ABSOLUTE values; the
                                        // brightness slider is NOT passed —
                                        // its own lane scales the colour, and
                                        // passing it here as the gain zeroed
                                        // every channel nudge while the LED
                                        // was off (the author's live report).
                                        root.printer.setLedColor(modelData.object, root.sliderSelection(redSlider), root.sliderSelection(greenSlider), root.sliderSelection(blueSlider), modelData.hasWhite ? root.sliderSelection(whiteSlider) : 0, -1);
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            width: 150 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
                                            text: "Brightness " + root.sliderSelection(ledSlider) + "%"
                                        }
                                    }
                                    OutlineSlider {
                                        id: ledSlider
                                        Layout.fillWidth: true
                                        controlObject: modelData.object
                                        controlKind: "led-brightness"
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onValueTuning: {
                                            if (root.printer != null)
                                                root.printer.previewLedBrightness(modelData.object, value);
                                        }
                                        onValueCommitted: {
                                            if (root.printer != null)
                                                root.printer.setLedBrightness(modelData.object, value);
                                        }
                                        onInteractingChanged: {
                                            root.tuningSliderPressed = interacting;
                                            if (interacting) {
                                                root.tuningSliderObject = modelData.object;
                                                root.tuningSliderKind = "led-brightness";
                                            }
                                        }
                                    }

                                    GridLayout {
                                        columns: 3
                                        Layout.fillWidth: true
                                        columnSpacing: UM.Theme.getSize("thin_margin").width
                                        rowSpacing: UM.Theme.getSize("thin_margin").height

                                        UM.Label {
                                            width: 16 * screenScaleFactor
                                            text: "R"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: redSlider
                                            Layout.fillWidth: true
                                            controlObject: modelData.object
                                            controlKind: "led-red"
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.redPercent
                                            onValueTuning: previewLedColour()
                                            onValueCommitted: applyLedColour()
                                            onInteractingChanged: {
                                                root.tuningSliderPressed = interacting;
                                                if (interacting) {
                                                    root.tuningSliderObject = modelData.object;
                                                    root.tuningSliderKind = "led-red";
                                                }
                                            }
                                        }
                                        UM.Label {
                                            width: 52 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
                                            text: root.sliderSelection(redSlider) + "%"
                                        }

                                        UM.Label {
                                            width: 16 * screenScaleFactor
                                            text: "G"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: greenSlider
                                            Layout.fillWidth: true
                                            controlObject: modelData.object
                                            controlKind: "led-green"
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.greenPercent
                                            onValueTuning: previewLedColour()
                                            onValueCommitted: applyLedColour()
                                            onInteractingChanged: {
                                                root.tuningSliderPressed = interacting;
                                                if (interacting) {
                                                    root.tuningSliderObject = modelData.object;
                                                    root.tuningSliderKind = "led-green";
                                                }
                                            }
                                        }
                                        UM.Label {
                                            width: 52 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
                                            text: root.sliderSelection(greenSlider) + "%"
                                        }

                                        UM.Label {
                                            width: 16 * screenScaleFactor
                                            text: "B"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: blueSlider
                                            Layout.fillWidth: true
                                            controlObject: modelData.object
                                            controlKind: "led-blue"
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.bluePercent
                                            onValueTuning: previewLedColour()
                                            onValueCommitted: applyLedColour()
                                            onInteractingChanged: {
                                                root.tuningSliderPressed = interacting;
                                                if (interacting) {
                                                    root.tuningSliderObject = modelData.object;
                                                    root.tuningSliderKind = "led-blue";
                                                }
                                            }
                                        }
                                        UM.Label {
                                            width: 52 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
                                            text: root.sliderSelection(blueSlider) + "%"
                                        }

                                        UM.Label {
                                            visible: modelData.hasWhite
                                            width: 16 * screenScaleFactor
                                            text: "W"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        OutlineSlider {
                                            id: whiteSlider
                                            visible: modelData.hasWhite
                                            Layout.fillWidth: true
                                            controlObject: modelData.object
                                            controlKind: "led-white"
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.whitePercent
                                            onValueTuning: previewLedColour()
                                            onValueCommitted: applyLedColour()
                                            onInteractingChanged: {
                                                root.tuningSliderPressed = interacting;
                                                if (interacting) {
                                                    root.tuningSliderObject = modelData.object;
                                                    root.tuningSliderKind = "led-white";
                                                }
                                            }
                                        }
                                        UM.Label {
                                            visible: modelData.hasWhite
                                            width: 52 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
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
                                id: pwmRepeater
                                model: root.tuningSliderPressed ? root.frozenPwmOutputItems : (root.printer != null ? root.printer.pwmOutputItems : [])
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
                                            width: 52 * screenScaleFactor
                                            horizontalAlignment: Text.AlignRight
                                            text: root.sliderSelection(pwmSlider) + "%"
                                        }
                                    }
                                    OutlineSlider {
                                        id: pwmSlider
                                        Layout.fillWidth: true
                                        controlObject: modelData.object
                                        controlKind: "pwm"
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onValueTuning: {
                                            if (root.printer != null)
                                                root.printer.previewPwmOutput(modelData.object, value);
                                        }
                                        onValueCommitted: {
                                            if (root.printer != null)
                                                root.printer.setPwmOutput(modelData.object, value);
                                        }
                                        onInteractingChanged: {
                                            root.tuningSliderPressed = interacting;
                                            if (interacting) {
                                                root.tuningSliderObject = modelData.object;
                                                root.tuningSliderKind = "pwm";
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
                                    text: root.printer != null && root.printer.sectionReason !== "" ? root.printer.sectionReason : (root.anyPowerLocked ? "Locked during this print" : "—")
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null && root.printer.sectionReasonDetail !== "" ? root.printer.sectionReasonDetail : (root.anyPowerLocked ? "Power control is locked by Moonraker while this print is active." : "")
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
                                    objectName: "moonrakerFirmwareRestart"
                                    // The policy gate (4.2.0): the
                                    // reason rides the tooltip when
                                    // the button is denied.
                                    tooltip: "Restart Klipper's firmware process (FIRMWARE_RESTART)." + (root.printer != null && !root.printer.canRestart && root.printer.restartReasonDetail !== "" ? " " + root.printer.restartReasonDetail : "")
                                    enabled: root.printer != null && root.printer.canRestart
                                    onClicked: root.printer.firmwareRestart()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Host restart"
                                    objectName: "moonrakerHostRestart"
                                    tooltip: "Reboot the host Moonraker runs on (machine/reboot)." + (root.printer != null && !root.printer.canRestart && root.printer.restartReasonDetail !== "" ? " " + root.printer.restartReasonDetail : "")
                                    enabled: root.printer != null && root.printer.canRestart
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
                                    objectName: "moonrakerKlipperRestart"
                                    tooltip: "Restart Klipper entirely (printer/restart): reloads the config and reconnects the MCU." + (root.printer != null && !root.printer.canRestart && root.printer.restartReasonDetail !== "" ? " " + root.printer.restartReasonDetail : "")
                                    enabled: root.printer != null && root.printer.canRestart
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
                                    // The restart reason is the policy's
                                    // own (4.2.0): the row carries it,
                                    // the tooltip is enrichment.
                                    text: root.printer != null && root.printer.restartReason !== "" ? root.printer.restartReason : "—"
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null ? root.printer.restartReasonDetail : ""
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
                                    text: root.printer != null ? (root.printer.sectionReason !== "" ? root.printer.sectionReason : (root.printer.printActive ? "Disabled during a print" : root.printer.canSaveConfig ? "Restarts Klipper" : "—")) : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    wrapMode: Text.NoWrap
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        // Short value in the row, full
                                        // sentence in the tooltip (the
                                        // author's ruling).
                                        text: root.printer != null ? (root.printer.sectionReasonDetail !== "" ? root.printer.sectionReasonDetail : (root.printer.printActive ? "SAVE_CONFIG is disabled during a print." : root.printer.canSaveConfig ? "Saving configuration restarts Klipper." : "")) : ""
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
