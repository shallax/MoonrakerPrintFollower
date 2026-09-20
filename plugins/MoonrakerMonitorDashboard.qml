import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import QtQuick.Window 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

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
        // The narrow-window lock reaches this pane too (the rule is
        // universal): a controls expansion is refused while it would
        // take the WEBCAM's viewport under its comfort minimum. The
        // camera's own width is the model's — the loaded monitor holds
        // it, and it already carries whatever the panes inside the
        // monitor gave up — so the two documents cannot disagree about
        // the room. The cost is this pane's expanded width less the
        // collapsed strip opening it replaces.
        readonly property real cameraViewportWidth: baseMonitorLoader.item !== null ? baseMonitorLoader.item.cameraViewportWidth : 0
        readonly property real controlsExpandCost: (386 - 44) * screenScaleFactor
        readonly property bool webcamSqueezed: root.cameraViewportWidth > 0 && root.cameraViewportWidth < 220 * screenScaleFactor
        readonly property bool controlsExpandBlocked: root.cameraViewportWidth > 0 && root.cameraViewportWidth - root.controlsExpandCost < 220 * screenScaleFactor
        readonly property bool controlsExpandLocked: root.controlsCollapsed && (root.webcamSqueezed || root.controlsExpandBlocked)
        // The availability gates (the live ruling): a value that is
        // unavailable must not render — neither the value nor its
        // glyph. The X/Y/Z tuple hides WHOLE when any one axis is
        // absent. Maintained IMPERATIVELY from the model's change
        // signals: bindings on the setProperty-fed values go stale
        // on both engines (the camera lesson), and a stale gate
        // keeps rendering "—" cells forever.
        property bool positionAvailable: false
        property bool zOffsetAvailable: false
        function refreshAvailabilityGates() {
            positionAvailable = root.printer != null && root.printer.monitorPositionX !== "—" && root.printer.monitorPositionX !== "" && root.printer.monitorPositionY !== "—" && root.printer.monitorPositionY !== "" && root.printer.monitorPositionZ !== "—" && root.printer.monitorPositionZ !== "";
            zOffsetAvailable = root.printer != null && root.printer.zOffsetText !== "—" && root.printer.zOffsetText !== "";
            // The strip's length changes with the availability — the
            // fit re-measures (the panel's catch).
            Qt.callLater(root.updateControlsReadoutFits);
        }
        Connections {
            target: root.printer
            function onMonitorPositionXChanged() {
                root.refreshAvailabilityGates();
            }
            function onMonitorPositionYChanged() {
                root.refreshAvailabilityGates();
            }
            function onMonitorPositionZChanged() {
                root.refreshAvailabilityGates();
            }
            function onZOffsetTextChanged() {
                root.refreshAvailabilityGates();
            }
        }
        // The file-manager popup's open state (Snapshot 0: the mock).
        // A printer switch closes it — a stale popup must never carry
        // actions from one machine to the next (round-2 A15).
        property bool fileManagerOpen: root.printer != null && root.printer.fileManagerOpen
        // The controls configure pop-up's open state and its rows —
        // the pane pop-over pattern, not the file-manager popup.
        property string configurePaneOpen: ""
        property var controlsConfigureRows: []
        property var controlsConfigureHidden: []
        // The configure popup's rows (the live headers carry the
        // titles): root-level so the header trigger can call it.
        function sectionHeader(item) {
            if (!item || !item.children) {
                return null;
            }
            var header = item.children[0];
            return (header && header.sectionId !== undefined) ? header : null;
        }
        function buildControlsConfigureRows() {
            var layout = root.printer != null ? root.printer.sectionLayoutFor("controls") : null;
            var order = layout ? layout.order : [];
            var byId = {};
            for (var i = 0; i < controlContent.children.length; i++) {
                var header = root.sectionHeader(controlContent.children[i]);
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
        // The whole-pair fit, written IMPERATIVELY against the
        // pane's actual rendered geometry: the label's two
        // main-axis endpoints mapped into the pane bound the pair
        // (a single corner read wrong — the live report), and the
        // hysteresis keeps an intermediate resize geometry from
        // flickering the pair at the threshold.
        function fitControlsPair(first, stride) {
            var children = controlsReadoutRow.children;
            if (first + stride - 1 >= children.length)
                return;
            var last = children[first + stride - 1];
            var a = last.mapToItem(controlsPane, 0, 0).y;
            var b = last.mapToItem(controlsPane, last.width, 0).y;
            var bottom = Math.max(a, b);
            var hidden = children[first].fitHidden;
            // Bottom-only, the info fit's lesson: the top guard read
            // false under some engine mappings and froze the hides.
            // The pane's full height can exceed the visible viewport
            // (the monitor's x7 report) — bound against the pane's
            // VISIBLE extent, the window's height minus the pane's
            // scene position.
            var windowHeight = controlsPane.Window != null ? controlsPane.Window.height : 0;
            var paneTop = controlsPane.mapToItem(null, 0, 0).y;
            var visibleHeight = Math.min(controlsPane.height, windowHeight - paneTop);
            var hide = hidden ? bottom > visibleHeight - 14 * screenScaleFactor : bottom > visibleHeight - 6 * screenScaleFactor;
            for (var i = first; i < first + stride; ++i)
                children[i].fitHidden = hide;
        }
        function updateControlsReadoutFits() {
            // The position group is the glyph plus the three axis
            // cells; the z group is a pair (the flow pair moved to
            // the status strip, the live ruling).
            fitControlsPair(0, 4);
            fitControlsPair(4, 2);
        }
        onControlsCollapsedChanged: {
            Qt.callLater(root.updateControlsReadoutFits);
            // The deferred retry: the callLater can run BEFORE the
            // collapsed layout settles (the monitor's x7 report) —
            // the timer re-measures with the settled geometry.
            fitControlsRetry.restart();
        }
        Timer {
            id: fitControlsRetry
            interval: 200
            repeat: false
            onTriggered: root.updateControlsReadoutFits()
        }

        // The section-order application: the configure popup and the
        // state hydration both flow through sectionLayout; the pane
        // re-parents only when the live order differs (the
        // probe-verified recipe — detach-all, re-attach in target
        // order, strays re-attach last). Root-level, like the
        // monitor's — a nested function called from the signal
        // handler threw on the container's engine.
        function applyControlsOrder() {
            // The EFFECTIVE layout, never the raw property: a
            // restore to the pane's default order drops the entry
            // entirely, and the raw {} would strand the sections in
            // the previous order.
            var effective = root.printer ? root.printer.sectionLayoutFor("controls") : null;
            var target = effective ? effective.order : null;
            if (!target)
                return;
            var current = [];
            for (var i = 0; i < controlContent.children.length; i++) {
                var header = root.sectionHeader(controlContent.children[i]);
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
                    var header2 = root.sectionHeader(items[n]);
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
            // The monitor's recipe (the 4.5.0 live find): the reparent
            // alone leaves the layout stale until an interaction — a
            // zero-size child added and removed in the same block
            // schedules the rebuild invisibly.
            var nudge = Qt.createQmlObject("import QtQuick 2.15; Item {}", controlContent, "orderNudge");
            nudge.parent = null;
            nudge.destroy();
        }

        onPrinterChanged: {
            refreshAvailabilityGates();
            if (root.printer != null) {
                root.printer.setFileManagerOpen(false);
                // The hydration publish can fire before the dashboard
                // (an async Loader) attaches — the arrival hook is the
                // boot-time apply the signal path can miss (the 4.5.0
                // live find: the controls pane snapped on interaction).
                root.applyControlsOrder();
            }
            // A machine switch must not carry the old printer's
            // frozen lists or focus target into the new session —
            // the sink state is per-printer.
            tuningSliderPressed = false;
            tuningSliderObject = "";
            tuningSliderKind = "";
        }
        // Esc on the Monitor page (a live request): the pop-overs
        // close first, then the page itself — Preview when anything
        // is sliced, Prepare otherwise. THE one window-level
        // shortcut: the whole Esc ladder in one place, so the key
        // can never have two claimants (the live report: the popup's
        // own shortcut and this one fought, and the popup lost).
        // This document hosts every layer — the monitor's pop-ups
        // ride openPopOver on the loaded monitor root, this pane's
        // pop-up is configurePaneOpen, and the file manager is
        // fileManagerOpen — so the ladder lives here, not in the
        // monitor document (its own shortcut fired the stage-exit
        // branch for this pane's pop-up, which it cannot see — the
        // harness probe's finding).
        Shortcut {
            sequence: "Esc"
            onActivated: {
                var monitor = baseMonitorLoader.item;
                if (root.printer != null && root.printer.fileManagerOpen) {
                    if (fileManagerCard.columnsPopupOpen()) {
                        // The columns popup is the TOP layer inside
                        // the card: Esc closes it first (the live
                        // report: Esc ignored the popup).
                        fileManagerCard.closeColumnsPopup();
                    } else if (root.printer.filePrintConfirm !== "") {
                        // The print confirmation is the TOP layer: Esc
                        // cancels it, not the popup (the ruling).
                        root.printer.fileCancelPrint();
                    } else {
                        root.printer.setFileManagerOpen(false);
                    }
                } else if (monitor !== null && (monitor.openPopOver === "sections-info" || monitor.openPopOver === "sections-status")) {
                    // The configure pop-ups close FIRST — Esc must
                    // never leave the page from under an open popup
                    // (the live report).
                    monitor.openPopOver = "";
                } else if (configurePaneOpen !== "") {
                    configurePaneOpen = "";
                } else if (monitor !== null && (monitor.openPopOver !== "" || monitor.selectedChartSensor !== "")) {
                    monitor.openPopOver = "";
                    monitor.selectedChartSensor = "";
                } else if (OutputDevice != null) {
                    OutputDevice.leaveMonitorStage();
                }
            }
        }

        // ONE popover at a time, the other direction: when the
        // monitor's cards open, this pane's popup closes (the live
        // report: the information card could stack under the
        // controls card).
        Connections {
            target: baseMonitorLoader.item
            function onOpenPopOverChanged() {
                if (baseMonitorLoader.item !== null && baseMonitorLoader.item.openPopOver !== "") {
                    configurePaneOpen = "";
                }
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
        // click, with a bounds check so an in-bounds click on the
        // card's own surface never dismisses it (the live report).
        // Its z sits above the pane content but below the card's own
        // 999, mirroring the monitor's outside-click layer.
        MouseArea {
            visible: root.configurePaneOpen !== ""
            anchors.fill: parent
            z: 995
            onClicked: {
                if (controlsConfigurePopOver.visible && mouse.x >= controlsConfigurePopOver.x && mouse.x <= controlsConfigurePopOver.x + controlsConfigurePopOver.width && mouse.y >= controlsConfigurePopOver.y && mouse.y <= controlsConfigurePopOver.y + controlsConfigurePopOver.height) {
                    return;
                }
                root.configurePaneOpen = "";
            }
        }

        property bool tuningSliderPressed: false
        // The freeze lists (a live report): while a tuning
        // slider is mid-gesture — a drag or a pending keyboard nudge —
        // the fan/LED/PWM repeaters must not rebuild, or the rebuild
        // replaces the focused delegate and the interaction dies.
        property var frozenFanItems: []
        property var frozenLedItems: []
        property var frozenPwmOutputItems: []
        // The slider that was being tuned when the freeze lifted: the
        // live lists rebuild the repeaters then, so the delegate's
        // focus dies with the rebuild — the dashboard re-grants it
        // once the new delegate exists (a live report).
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
        // that dies a moment later (a live report — fan
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
                // The ruling (2026-09-10): while DISCONNECTED
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
                    border.color: MoonrakerTheme.dangerRed
                    border.width: 2 * screenScaleFactor
                    clip: true
                    Rectangle {
                        id: emergencyFill
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: parent.width * Math.min(1.0, (emergencyButton.clicks + (root.printer != null ? root.printer.emergencyHoldProgress : 0)) / 3.0)
                        color: MoonrakerTheme.dangerRed
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
                            // The remainder copy follows the theme's
                            // text colour (the 4.5.0 dark-mode
                            // ruling): hardcoded black was unreadable
                            // on dark mode's grey button ground. The
                            // white-over-red sweep copy stays white.
                            color: UM.Theme.getColor("text")
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
                id: baseMonitorLoader
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 640 * screenScaleFactor
                sourceComponent: baseMonitorComponent
                // The monitor's pop-ups must float above THIS pane:
                // the pane is declared after the loader, so its z-0
                // content paints over the whole monitor subtree — a
                // popup's own z stays inside the subtree and loses
                // the cross-sibling fight (the probe's finding: the
                // pane's header grabbed clicks aimed at the popup's
                // rows).
                z: baseMonitorLoader.item !== null && baseMonitorLoader.item.openPopOver !== "" ? 10 : 0
            }

            Cura.RoundedRectangle {
                // Inert; the harness's margin-symmetry pin reads this
                // pane's outer edge.
                id: controlsPane
                objectName: "moonrakerControlsPane"
                // The collapsed readout may outrun a short pane: the
                // PANE clips, so no child can ever spill past its
                // bounds (the live report).
                clip: true
                onHeightChanged: root.updateControlsReadoutFits()
                // Collapsed, the pane shrinks to the toggle button and its
                // margins; the vertical title below explains the strip.
                Layout.preferredWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 386 * screenScaleFactor)
                Layout.minimumWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 340 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 386 * screenScaleFactor)
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
                        // Auto-collapsed-by-width is not a clickable
                        // expand: only a wider stage restores the pane
                        // (the monitor panes' guard, the same rule).
                        if (root.controlsExpandLocked) {
                            return;
                        }
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

                        HoverHandler {
                            id: tooltipHover1
                        }
                        UM.ToolTip {
                            visible: tooltipHover1.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: root.printer != null && root.printer.controlsLocked ? "Unlock all controls." : "Lock all controls."
                        }
                    }
                    // The configure trigger: the same glyph as the
                    // column configurer, one pane per header (the
                    // ruling).
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
                        onClicked: {
                            root.buildControlsConfigureRows();
                            // ONE popover at a time: the monitor's
                            // cards close when this one opens (the
                            // live report: the status card could
                            // stack under the controls card).
                            if (baseMonitorLoader.item !== null) {
                                baseMonitorLoader.item.openPopOver = "";
                            }
                            // Positioned imperatively at open time: a
                            // mapToItem binding evaluates once, before
                            // the pane's layout has settled, and no
                            // dependency re-runs it (the trigger's
                            // own x never changes when the layout
                            // moves the pane — the probe's finding:
                            // the card 292px left of the window, then
                            // 163px right of it).
                            var edge = configureSectionsButton.mapToItem(root, configureSectionsButton.width, 0);
                            controlsConfigurePopOver.x = edge.x - controlsConfigurePopOver.width;
                            controlsConfigurePopOver.y = edge.y + configureSectionsButton.height + UM.Theme.getSize("thin_margin").height;
                            root.configurePaneOpen = "controls";
                        }
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: "Configure the printer-control sections."
                        }
                    }
                    Cura.SecondaryButton {
                        id: collapseButton
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        // Square at the OLD button width: the theme adds
                        // its padding around the 32px content, so the
                        // height tracks the rendered width (the
                        // ruling).
                        width: 28 * screenScaleFactor
                        iconSize: 12 * screenScaleFactor
                        height: width
                        implicitHeight: width
                        // The SAME theme-chevron family as the monitor's
                        // pane toggles (the ruling: all pane
                        // collapse buttons uniform).
                        iconSource: root.controlsCollapsed ? UM.Theme.getIcon("ChevronSingleLeft") : UM.Theme.getIcon("ChevronSingleRight")
                        onClicked: {
                            // The same guard as the strip: while the
                            // camera cannot spare the room, the expand
                            // direction waits (the tooltip says so).
                            if (root.controlsExpandLocked) {
                                return;
                            }
                            if (root.printer != null) {
                                root.printer.setControlsCollapsed(!root.controlsCollapsed);
                            }
                        }
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: root.controlsExpandLocked ? "The window is too narrow — widen it to show the printer controls." : (root.controlsCollapsed ? "Show the printer controls." : "Hide the printer controls.")
                        }
                    }
                }

                Flickable {
                    id: controlFlick
                    objectName: "moonrakerControlsFlick"
                    visible: !root.controlsCollapsed
                    anchors.top: controlHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    // No right inset: the content's own 14px gutter is
                    // the only dead band right of the sections, exactly
                    // as the monitor's information and status panes rule
                    // it. A right margin here plus the gutter widened
                    // this pane's right gap against its scroll bar while
                    // the other panes butted up.
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
                        objectName: "moonrakerControlsContent"
                        // The stored order applies HERE — before the
                        // first frame paints (the monitor's 4.5.0 find;
                        // without it the controls pane waited for an
                        // interaction and snapped visibly).
                        Component.onCompleted: root.applyControlsOrder()
                        // The constant gutter, exactly as the monitor's
                        // information and status panes rule it: the
                        // column keeps 14px clear of the flickable's
                        // right edge and the attached scrollbar overlays
                        // that gutter. Yielding a bar's width plus a
                        // margin instead left this pane's rows an extra
                        // gap short of the bar while the other panes
                        // butted up, and a width that followed the bar's
                        // visibility could oscillate with the bar's own
                        // show/hide.
                        width: controlFlick.width - 14
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

                        ObjectsSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["objects"] !== true
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

                    Connections {
                        target: root.printer
                        function onSectionLayoutChanged() {
                            root.applyControlsOrder();
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
                // The collapsed readout (the 2026-09-17
                // ruling): position, Z offset and flow rate fill the
                // empty space BELOW the title — regular text, not the
                // title's face. ONE line of FIXED-WIDTH fields, each
                // its own label with an icon glyph, no separators:
                // the values changing never reflows the strip and
                // the fields can never overlap (the live request).
                Item {
                    id: controlsCollapsedReadoutBox
                    visible: root.controlsCollapsed
                    // The same short-window discipline as the
                    // monitor's readouts: the box clips, and the
                    // line hides when it cannot fit whole.
                    clip: true
                    anchors.top: collapsedTitleBox.bottom
                    anchors.topMargin: 2 * UM.Theme.getSize("default_margin").height
                    anchors.horizontalCenter: collapsedTitleBox.horizontalCenter
                    // The box hugs the content: its height tracks
                    // the row's implicit width, so the centred row
                    // fills it and the strip starts at the margin
                    // under the title (the live report). 18 is the
                    // label line height.
                    width: 18 * screenScaleFactor
                    height: controlsReadoutRow.implicitWidth
                    Row {
                        id: controlsReadoutRow
                        anchors.centerIn: parent
                        spacing: 2 * screenScaleFactor
                        rotation: 90
                        // The fit hides through OPACITY, never
                        // the visibility flag: a hidden pair keeps
                        // its place in the layout, so the survivors
                        // can never re-centre in the box (the live
                        // report) — the strip stays anchored under
                        // the title. The labels' fixed widths are
                        // the field boundaries.
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.positionAvailable
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("Position.svg")
                        }
                        UM.Label {
                            objectName: "controlsCollapsedReadoutText"
                            property bool fitHidden: false
                            visible: root.positionAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorPositionX : ""
                            // Each axis independent and fixed-width:
                            // "X 888.88" must sit without reflowing
                            // (the live ruling). The axis cells wear
                            // their axis colours: X red, Y green, Z
                            // blue — the other cells stay themed.
                            width: 70 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: MoonrakerTheme.axisX
                            elide: Text.ElideRight
                        }
                        UM.Label {
                            objectName: "controlsCollapsedReadoutText"
                            property bool fitHidden: false
                            visible: root.positionAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorPositionY : ""
                            width: 70 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: MoonrakerTheme.axisY
                            elide: Text.ElideRight
                        }
                        UM.Label {
                            objectName: "controlsCollapsedReadoutText"
                            property bool fitHidden: false
                            visible: root.positionAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorPositionZ : ""
                            width: 70 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: MoonrakerTheme.axisZ
                            elide: Text.ElideRight
                        }
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.zOffsetAvailable
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("ZOffset.svg")
                        }
                        UM.Label {
                            // The z pair's own name (the harness
                            // rule): the position cells share
                            // controlsCollapsedReadoutText, and the
                            // standby scenario proves the z offset's
                            // honest zero renders while the
                            // unavailable position hides.
                            objectName: "controlsCollapsedZOffsetLabel"
                            property bool fitHidden: false
                            visible: root.zOffsetAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.zOffsetText : ""
                            // "88.000 mm" must sit comfortably (the
                            // live ruling).
                            width: 96 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }
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

        // The configure pop-up, declared LAST so its anchor ids exist
        // when the x/y bindings evaluate: an early declaration falls
        // through to an outer-scope id and latches the card to the
        // wrong button (the harness probe's finding: x=-198, 198px
        // off the window's left edge).
        SectionConfigurePopOver {
            id: controlsConfigurePopOver
            visible: root.configurePaneOpen === "controls"
            title: "Printer-control sections"
            paneId: "controls"
            rows: root.controlsConfigureRows
            hidden: root.controlsConfigureHidden
            width: 320 * screenScaleFactor
            // The x/y land from the trigger's onClicked (the
            // imperative positioning above) — never anchors, never
            // bindings: an anchor to a header row's inner items
            // drops silently and the card lands at the top-left (the
            // live report).
            onLayoutCommitted: function (order, hidden) {
                if (root.printer != null) {
                    root.printer.setSectionLayout("controls", order, hidden);
                }
            }
        }
    }
}
