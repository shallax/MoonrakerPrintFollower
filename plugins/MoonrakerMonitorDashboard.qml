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
        // Slider sections report interaction through this sink — the
        // freeze and the refocus target stay single-owner here.
        function receiveSliderInteraction(interacting, object, kind) {
            tuningSliderPressed = interacting;
            if (interacting) {
                tuningSliderObject = object;
                tuningSliderKind = kind;
            }
        }
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
                        FileManagerSection {
                            id: fileManagerSection
                            Layout.fillWidth: true
                            printerModel: root.printer
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

                        TuningSection {
                            id: tuningSection
                            Layout.fillWidth: true
                            printerModel: root.printer
                            interactionSink: root.receiveSliderInteraction
                        }
                        MacrosSection {
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        ProfilesSection {
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }

                        FansSection {
                            id: fansSection
                            Layout.fillWidth: true
                            printerModel: root.printer
                            freezeRepeaters: root.tuningSliderPressed
                            frozenItems: root.frozenFanItems
                            interactionSink: root.receiveSliderInteraction
                        }

                        LedsSection {
                            id: ledsSection
                            Layout.fillWidth: true
                            printerModel: root.printer
                            freezeRepeaters: root.tuningSliderPressed
                            frozenItems: root.frozenLedItems
                            interactionSink: root.receiveSliderInteraction
                        }

                        PwmSection {
                            id: pwmSection
                            Layout.fillWidth: true
                            printerModel: root.printer
                            freezeRepeaters: root.tuningSliderPressed
                            frozenItems: root.frozenPwmOutputItems
                            interactionSink: root.receiveSliderInteraction
                        }

                        PowerSection {
                            id: powerSection
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onPowerOffConfirmRequested: function (deviceName) {
                                powerOffDialog.deviceName = deviceName;
                                powerOffDialog.open();
                            }
                        }

                        SystemSection {
                            id: systemSection
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        SaveSection {
                            id: saveSection
                            Layout.fillWidth: true
                            printerModel: root.printer
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
