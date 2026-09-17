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
        // The controls configure pop-up's open state and its rows —
        // the pane pop-over pattern, not the file-manager popup.
        property string configurePaneOpen: ""
        property var controlsConfigureRows: []
        property var controlsConfigureHidden: []
        onPrinterChanged: {
            if (root.printer != null) {
                root.printer.setFileManagerOpen(false);
            }
            // A machine switch must not carry the old printer's
            // frozen lists or focus target into the new session —
            // the sink state is per-printer.
            tuningSliderPressed = false;
            tuningSliderObject = "";
            tuningSliderKind = "";
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
        // The configure pop-up overlays the pane, not the layout (the
        // pop-over precedent: layout children cannot overlap). The
        // scrim sits below the card and closes it on any outside
        // click.
        MouseArea {
            visible: root.configurePaneOpen !== ""
            anchors.fill: parent
            onClicked: root.configurePaneOpen = ""
        }
        SectionConfigurePopOver {
            id: controlsConfigurePopOver
            visible: root.configurePaneOpen === "controls"
            title: "Printer-control sections"
            paneId: "controls"
            rows: root.controlsConfigureRows
            hidden: root.controlsConfigureHidden
            width: 320 * screenScaleFactor
            anchors.top: collapseButton.bottom
            anchors.topMargin: UM.Theme.getSize("thin_margin").height
            anchors.right: collapseButton.right
            onLayoutCommitted: function (order, hidden) {
                if (root.printer != null) {
                    root.printer.setSectionLayout("controls", order, hidden);
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
        // freeze and the refocus target stay single-owner here. The
        // press edge snapshots the live lists BEFORE the flag flips:
        // the repeaters' freeze bindings re-evaluate on the flag, so
        // the frozen lists must already hold this gesture's snapshot.
        // A gesture that dies without its release edge (the lane's
        // enabled flips mid-drag, a grab stolen) can never latch the
        // dashboard — the watchdog clears the freeze, and a printer
        // change resets everything.
        function receiveSliderInteraction(interacting, object, kind) {
            if (interacting && !tuningSliderPressed && root.printer != null) {
                root.frozenFanItems = root.printer.fanControlItems;
                root.frozenLedItems = root.printer.ledItems;
                root.frozenPwmOutputItems = root.printer.pwmOutputItems;
            }
            tuningSliderPressed = interacting;
            if (interacting) {
                tuningSliderObject = object;
                tuningSliderKind = kind;
                sliderWatchdog.restart();
            }
        }
        onTuningSliderPressedChanged: {
            if (!tuningSliderPressed && root.tuningSliderObject !== "") {
                refocusTimer.attempts = 0;
                refocusTimer.start();
            }
        }
        // The watchdog (the security re-review's sink latch): every
        // press re-arms it; a cancelled gesture never re-arms, so the
        // freeze and the focus target clear instead of latching the
        // pane on a stale snapshot.
        Timer {
            id: sliderWatchdog
            interval: 10000
            repeat: false
            onTriggered: {
                if (!tuningSliderPressed) {
                    return;
                }
                tuningSliderPressed = false;
                tuningSliderObject = "";
                tuningSliderKind = "";
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
            // the real delegate accessor, and the repeaters ride
            // their sections (4.3.0) — the walk roots at the section
            // instantiations; the host never names a section's ids.
            if (root.focusSliderIn(fansSection, target, kind)) {
                return true;
            }
            if (root.focusSliderIn(ledsSection, target, kind)) {
                return true;
            }
            if (root.focusSliderIn(pwmSection, target, kind)) {
                return true;
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
                    // The configure trigger: the same glyph as the
                    // column configurer, one pane per header (the
                    // author's ruling).
                    Cura.SecondaryButton {
                        id: configureSectionsButton
                        objectName: "configureControlsSectionsButton"
                        visible: !root.controlsCollapsed
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        width: 28 * screenScaleFactor
                        height: width
                        implicitHeight: width
                        text: "⇄"
                        tooltip: "Configure the printer-control sections."
                        onClicked: {
                            root.buildControlsConfigureRows();
                            root.configurePaneOpen = "controls";
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
                            visible: root.printer == null || root.printer.sectionHiddenMap["fileManager"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }

                        PrintSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["print"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onCancelRequested: cancelPrintDialog.open()
                        }

                        SetupSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["setup"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        ToolheadSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["toolhead"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }

                        MacrosSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["macros"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        ProfilesSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["profiles"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        TuningSection {
                            id: tuningSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["tuning"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            interactionSink: root.receiveSliderInteraction
                        }

                        FansSection {
                            id: fansSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["fans"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            freezeRepeaters: root.tuningSliderPressed
                            frozenItems: root.frozenFanItems
                            interactionSink: root.receiveSliderInteraction
                        }

                        LedsSection {
                            id: ledsSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["leds"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            freezeRepeaters: root.tuningSliderPressed
                            frozenItems: root.frozenLedItems
                            interactionSink: root.receiveSliderInteraction
                        }

                        PwmSection {
                            id: pwmSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["pwm"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            freezeRepeaters: root.tuningSliderPressed
                            frozenItems: root.frozenPwmOutputItems
                            interactionSink: root.receiveSliderInteraction
                        }

                        PowerSection {
                            id: powerSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["power"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onPowerOffConfirmRequested: function (deviceName) {
                                powerOffDialog.deviceName = deviceName;
                                powerOffDialog.open();
                            }
                        }

                        SystemSection {
                            id: systemSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["system"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        SaveSection {
                            id: saveSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["save"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                    }

                    // The section-order application: the configure popup
                    // and the state hydration both flow through
                    // sectionLayout; the pane re-parents only when the
                    // live order differs (the probe-verified recipe —
                    // detach-all, re-attach in target order, strays
                    // re-attach last).
                    function sectionHeader(item) {
                        if (!item || !item.children)
                            return null;
                        var header = item.children[0];
                        return (header && header.sectionId !== undefined) ? header : null;
                    }
                    function applyControlsOrder() {
                        var layout = root.printer ? root.printer.sectionLayout : null;
                        var entry = layout ? layout["controls"] : null;
                        var target = entry ? entry.order : null;
                        if (!target)
                            return;
                        var current = [];
                        for (var i = 0; i < controlContent.children.length; i++) {
                            var header = sectionHeader(controlContent.children[i]);
                            if (header)
                                current.push(header.sectionId);
                        }
                        if (JSON.stringify(current) === JSON.stringify(target))
                            return;
                        var items = [];
                        for (var j = 0; j < controlContent.children.length; j++)
                            items.push(controlContent.children[j]);
                        for (var k = 0; k < items.length; k++)
                            items[k].parent = null;
                        var attached = [];
                        for (var m = 0; m < target.length; m++) {
                            for (var n = 0; n < items.length; n++) {
                                var header2 = sectionHeader(items[n]);
                                if (header2 && header2.sectionId === target[m]) {
                                    items[n].parent = controlContent;
                                    attached.push(items[n]);
                                    break;
                                }
                            }
                        }
                        for (var p = 0; p < items.length; p++) {
                            if (attached.indexOf(items[p]) === -1)
                                items[p].parent = controlContent;
                        }
                    }
                    function buildControlsConfigureRows() {
                        var layout = root.printer != null ? root.printer.sectionLayoutFor("controls") : null;
                        var order = layout ? layout.order : [];
                        var byId = {};
                        for (var i = 0; i < controlContent.children.length; i++) {
                            var header = sectionHeader(controlContent.children[i]);
                            if (header) {
                                byId[header.sectionId] = header.title;
                            }
                        }
                        var rows = [];
                        for (var j = 0; j < order.length; j++) {
                            rows.push({
                                    "id": order[j],
                                    "title": byId[order[j]] !== undefined ? byId[order[j]] : order[j]
                                });
                        }
                        root.controlsConfigureRows = rows;
                        root.controlsConfigureHidden = layout ? layout.hidden : [];
                    }
                    Connections {
                        target: root.printer
                        function onSectionLayoutChanged() {
                            applyControlsOrder();
                            root.buildControlsConfigureRows();
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
