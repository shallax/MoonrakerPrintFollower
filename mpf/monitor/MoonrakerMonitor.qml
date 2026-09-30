import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import QtQuick.Window 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../bedmesh"
import "../plate"
import "../widgets"
import "camera"
import "console"
import "controls"
import "temperature"
import "../resources/theme"

// Component-rooted DELIBERATELY: Cura's monitor-view loader
// (setMonitorViewQmlPath) creates this document and expects a
// Component it can instantiate — unwrapping to the modern item-root
// form loaded fine in the capture harness but made the real Monitor
// stage fall back to Cura's placeholder. Qt logs a typecompiler
// deprecation for the pattern; that warning is accepted.
Component {
    id: monitorComponent
    Item {
        id: root

        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        // NOT a binding: root-level bindings on this dynamically
        // created document don't re-evaluate when the model's camera
        // URL lands (engine-proven — the stream sat dead until a
        // manual refresh). The handlers below maintain it
        // imperatively, and the inner bindings on it re-evaluate on
        // every write.
        property bool cameraConfigured: false

        // The popover registration re-runs whenever the printer
        // changes, not only when the open state changes: a printer
        // that becomes available (or switches) while the popover is
        // already open must re-claim the open state, or the model's
        // gate freezes the payload forever (the review's attach
        // finding).
        function syncPopoverRegistration() {
            if (root.printer == null) {
                return;
            }
            root.printer.setChartOpen(openPopOver === "chart");
            root.printer.setFollowerPopoverOpen(openPopOver === "plateprogress");
            root.printer.setPickerPopoverOpen(openPopOver === "plate");
        }
        onOpenPopOverChanged: syncPopoverRegistration()

        // The camera image's visible AND source are applied
        // IMPERATIVELY: bindings on this dynamically created
        // document do not reliably re-evaluate when the model's
        // camera values land (the first-entry stream never
        // starting — the refresh button worked because its nonce
        // bump is the one path that provably re-drives the image on
        // their machine). The model bumps the nonce on the first URL
        // transition too, so the first entry now rides that same
        // proven path. The URL is built here; CameraPane applies it.
        function updateCameraImage() {
            var configured = printer != null && printer.cameraUrl != null && printer.cameraUrl.toString().length > 0;
            if (cameraConfigured !== configured) {
                cameraConfigured = configured;
            }
            var url = "";
            if (configured) {
                url = printer.cameraUrl;
                if (printer.cameraRefreshNonce > 0) {
                    url = url.toString() + (url.toString().indexOf("?") >= 0 ? "&" : "?") + "mpf_reload=" + printer.cameraRefreshNonce;
                }
            }
            cameraPane.applyCamera(url, configured);
        }

        // One publish cycle can change BOTH cameraUrl and
        // cameraRefreshNonce (the first discovery does): without
        // coalescing each change applies the camera separately and
        // the first discovery drives TWO stream starts (the
        // camera-delay find's second cause). One callLater per
        // cycle: the initial attach, an explicit refresh and a
        // camera switch all still apply exactly once.
        property bool _cameraApplyPending: false
        function scheduleCameraApply() {
            if (_cameraApplyPending) {
                return;
            }
            _cameraApplyPending = true;
            Qt.callLater(function () {
                _cameraApplyPending = false;
                updateCameraImage();
            });
        }

        Component.onCompleted: {
            // The coalescer, not a direct application: completion
            // races the queued URL/nonce notifications, and a direct
            // call here was one of the two start-owners the
            // duplicate-start find named. The callLater deferral also
            // guarantees the reconciliation runs after the printer
            // binding and the pane have settled.
            scheduleCameraApply();
            // Re-claim the popover gates with the live state: loading
            // raced the printer binding, and the model's gate stays
            // frozen until something re-claims it.
            syncPopoverRegistration();
        }

        Connections {
            target: root.printer
            function onCameraUrlChanged() {
                root.scheduleCameraApply();
            }
            function onCameraRefreshChanged() {
                root.scheduleCameraApply();
            }
            function onCameraFpsChanged() {
                root.scheduleCameraApply();
            }
        }
        // One open pop-over at a time ("" | "chart" | "mesh"); every
        // opener and closer writes this, so the shells can never
        // overlap or trap a close button under another card.
        property string openPopOver: ""
        property string selectedChartSensor: ""
        // The configure pop-overs' rows (the pop-ups ride the
        // openPopOver switch like the chart and mesh cards).
        property var infoConfigureRows: []
        property var infoConfigureHidden: []
        property var statusConfigureRows: []
        property var statusConfigureHidden: []
        // The configure popups' rows (the live headers carry the
        // titles): root-level so the header triggers can call it.
        function sectionHeader(item) {
            if (!item || !item.children) {
                return null;
            }
            var header = item.children[0];
            return (header && header.sectionId !== undefined) ? header : null;
        }
        function buildConfigureRows(paneId) {
            var layout = root.printer != null ? root.printer.sectionLayoutFor(paneId) : null;
            var order = layout ? layout.order : [];
            var container = paneId === "information" ? infoContent : statusContent;
            var byId = {};
            for (var i = 0; i < container.children.length; i++) {
                var header = root.sectionHeader(container.children[i]);
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
            if (paneId === "information") {
                root.infoConfigureRows = rows;
                root.infoConfigureHidden = layout ? layout.hidden : [];
            } else {
                root.statusConfigureRows = rows;
                root.statusConfigureHidden = layout ? layout.hidden : [];
            }
        }
        // The section-order application: the configure popup and the
        // state hydration both flow through sectionLayout; each pane
        // re-parents only when the live order differs (the
        // probe-verified recipe — detach-all, re-attach in target
        // order, strays re-attach last). A ROOT-level named function,
        // the shell's attachDashboard lesson: nested functions called
        // from signal handlers resolve through the type scope and
        // throw on some engines (the probe's finding — the handler
        // logged its entry, the call never ran its first line).
        function applySectionOrder(container, paneId) {
            // The EFFECTIVE layout, never the raw property: a restore
            // to the pane's default order drops the entry entirely
            // (the normaliser keeps only touched panes), and the raw
            // {} would leave the sections stranded in the previous
            // order (the probe's finding).
            var effective = root.printer ? root.printer.sectionLayoutFor(paneId) : null;
            var target = effective ? effective.order : null;
            if (!target)
                return;
            var current = [];
            for (var i = 0; i < container.children.length; i++) {
                var header = root.sectionHeader(container.children[i]);
                if (header)
                    current.push(header.sectionId);
            }
            if (JSON.stringify(current) === JSON.stringify(target))
                return;
            var items = [];
            for (var j = 0; j < container.children.length; j++)
                items.push(container.children[j]);
            for (var k = 0; k < items.length; k++)
                items[k].parent = null;
            var attached = [];
            for (var m = 0; m < target.length; m++) {
                for (var n = 0; n < items.length; n++) {
                    var header2 = root.sectionHeader(items[n]);
                    if (header2 && header2.sectionId === target[m]) {
                        items[n].parent = container;
                        attached.push(items[n]);
                        break;
                    }
                }
            }
            for (var p = 0; p < items.length; p++) {
                if (attached.indexOf(items[p]) === -1)
                    items[p].parent = container;
            }
            // The reparent reorders the children, but a reparent that
            // lands inside the pane's own layout pass leaves the
            // layout's item list stale — the live panes kept the old
            // order until a visibility toggle forced the rebuild (the
            // 4.5.0 live find). A zero-size child added and removed
            // in the same block schedules that rebuild invisibly.
            var nudge = Qt.createQmlObject("import QtQuick 2.15; Item {}", container, "orderNudge");
            nudge.parent = null;
            nudge.destroy();
        }

        // The mini widget's series: the legend's stable mini row list
        // (the model selects it with the same policy that picks the
        // mini payload's series — primaries first, topped up to two,
        // or two visible others when no primary shows). The identity
        // only changes when the config or the sensor set does, so the
        // mini legend's delegates never rebuild at the sample cadence;
        // the live values ride the latest projection instead.
        property var miniChartSeries: {
            var legend = root.printer != null ? root.printer.temperatureChartLegend : null;
            if (legend == null) {
                return [];
            }
            return legend.miniSeries;
        }
        property bool miniChartHasSeries: root.miniChartSeries.length > 0

        // The collapsed strip's readout text: the PRINTER's own
        // peripherals (the ruling — never the mini chart's series,
        // which is empty before the history builds). The hotend row
        // reads the first extruder/hotend object, the bed row the
        // bed — each in the strip's pair form with the setpoint.
        function infoReadoutText(kind) {
            var items = root.printer != null ? root.printer.temperatureItems : [];
            for (var i = 0; i < items.length; ++i) {
                var item = items[i];
                var name = String(item.name || "").toLowerCase();
                var match = kind === "bed" ? name.indexOf("bed") >= 0 : name.indexOf("extruder") >= 0 || name.indexOf("hotend") >= 0;
                if (!match)
                    continue;
                // A matched object with NO reading is still
                // unavailable (the panel's catch: the old "— °C"
                // passed the gate and rendered a dash-unit pair the
                // ruling says must hide whole).
                if (item.temperature == null)
                    return "—";
                var text = Number(item.temperature).toFixed(1);
                if (item.target != null && Number(item.target) > 0)
                    text += " → " + Number(item.target).toFixed(0);
                return text + " °C";
            }
            return "—";
        }
        property string infoHotendText: "—"
        property string infoBedText: "—"
        property bool etaAvailable: false
        property bool finishAvailable: false
        property bool layerCountAvailable: false
        property bool flowAvailable: false
        function refreshAvailabilityGates() {
            infoHotendText = root.infoReadoutText("hotend");
            infoBedText = root.infoReadoutText("bed");
            etaAvailable = root.printer != null && root.printer.printActive && root.printer.monitorEta !== "—";
            finishAvailable = root.printer != null && root.printer.printActive && root.printer.monitorFinish !== "—";
            // The CURRENT layer is the value — a total-only form
            // ("— / 40", the scene's lingering heights) is still
            // unavailable (the x8 report): the whole pair hides.
            layerCountAvailable = root.printer != null && root.printer.monitorLayer !== "—" && root.printer.monitorLayer.indexOf("— /") !== 0;
            flowAvailable = root.printer != null && root.printer.monitorFlowRate !== "—";
            // A readout appearing mid-print changes the strip's
            // length — the fit must re-measure against the new
            // layout (the panel's catch).
            Qt.callLater(root.updateInfoReadoutFits);
            Qt.callLater(root.updateStatusReadoutFits);
        }
        Connections {
            target: root.printer
            function onTemperatureItemsChanged() {
                root.refreshAvailabilityGates();
            }
            function onMonitorEtaChanged() {
                root.refreshAvailabilityGates();
            }
            function onMonitorFinishChanged() {
                root.refreshAvailabilityGates();
            }
            function onMonitorLayerChanged() {
                root.refreshAvailabilityGates();
            }
            function onMonitorFlowRateChanged() {
                root.refreshAvailabilityGates();
            }
        }

        // The whole-group fit, written IMPERATIVELY against the
        // pane's actual rendered geometry. The group's TRUE visual
        // bounds come from its last child's two main-axis endpoints
        // mapped into the pane — the rotated row's main axis maps
        // onto the pane's y, and a single corner read wrong on the
        // engine (the live report). Hysteresis: hiding
        // needs the bottom past the pane's edge, re-showing needs
        // comfortable slack — an intermediate resize geometry must
        // never flicker the group at the threshold (the live
        // report).
        function fitGroup(row, stride, first, pane) {
            var children = row.children;
            if (first >= children.length)
                return;
            var last = children[first + stride - 1];
            // The box carries no rotation — map IT once (mapping
            // through the rotated row read wrong on the container's
            // engine, the x7 report) and add the child's local
            // offset along the row's main axis: +90 rows run
            // UPWARD, -90 downward (the sign lesson), and the
            // child's along-strip extent is its width.
            var box = row.parent;
            // The +90 row's main axis runs DOWNWARD on screen (Qt's
            // clockwise rotation — the probe-measured correction),
            // the -90 row's upward; the two panes' opposite reading
            // directions are DELIBERATE (the ruling).
            var sign = row.rotation === 90 ? 1 : -1;
            var offset = sign * (last.x + last.width / 2 - row.width / 2);
            var bottom = box.mapToItem(pane, 0, 0).y + box.height / 2 + offset + last.width / 2;
            // The pane's full height can EXCEED the visible viewport
            // (the page column runs past the window — the x7
            // report): the fit must bound against the pane's VISIBLE
            // extent, the WINDOW's height minus the pane's position
            // in the scene (mapToItem(null) — the document's own
            // root is the page, not the viewport). On a pane that
            // ends at the window the min leaves the height
            // unchanged.
            var windowHeight = pane.Window != null ? pane.Window.height : 0;
            var paneTop = pane.mapToItem(null, 0, 0).y;
            var visibleHeight = Math.min(pane.height, windowHeight - paneTop);
            var hidden = children[first].fitHidden;
            // No top guard: the -90-rotated info row's mapping read
            // the guard false on the engine and its groups
            // never hid (the live report) while the +90 rows worked.
            // Bottom-only is safe even on zero geometry — the
            // constant then reads the group's length, which hides
            // only below a genuinely tiny pane.
            var hide = hidden ? bottom > visibleHeight - 14 * screenScaleFactor : bottom > visibleHeight - 6 * screenScaleFactor;
            for (var i = first; i < first + stride && i < children.length; ++i)
                children[i].fitHidden = hide;
        }
        function updateInfoReadoutFits() {
            fitGroup(infoReadoutRow, 2, 0, infoPanel);
            // The spacer between the groups is a child too.
            fitGroup(infoReadoutRow, 2, 3, infoPanel);
        }
        function updateStatusReadoutFits() {
            // The ETA pairs lead, then the layer count, then (past
            // the margin child) the stacked progress group, then
            // the flow pair at the strip's end (the live ruling).
            fitGroup(statusReadoutRow, 2, 0, statusPanel);
            fitGroup(statusReadoutRow, 2, 2, statusPanel);
            fitGroup(statusReadoutRow, 2, 4, statusPanel);
            fitGroup(statusReadoutRow, 3, 7, statusPanel);
            fitGroup(statusReadoutRow, 2, 10, statusPanel);
        }
        onInfoCollapsedChanged: {
            Qt.callLater(root.updateInfoReadoutFits);
            // The deferred retry: the callLater can run BEFORE the
            // collapsed layout settles (the x7 report on the
            // container's engine) — the timer re-measures with the
            // settled geometry.
            fitInfoRetry.restart();
        }
        onStatusCollapsedChanged: {
            Qt.callLater(root.updateStatusReadoutFits);
            fitStatusRetry.restart();
        }
        Timer {
            id: fitInfoRetry
            interval: 200
            repeat: false
            onTriggered: root.updateInfoReadoutFits()
        }
        Timer {
            id: fitStatusRetry
            interval: 200
            repeat: false
            onTriggered: root.updateStatusReadoutFits()
        }
        onPrinterChanged: {
            refreshAvailabilityGates();
            openPopOver = "";
            // The openPopOverChanged signal only fires on a CHANGE, so
            // a printer swap while the popover was already closed
            // never re-claims the gates and a stale open can freeze
            // the payload. Re-claim the now-closed state explicitly.
            syncPopoverRegistration();
            selectedChartSensor = "";
            // The stored section order applies on the model's ARRIVAL
            // (the deterministic trigger — no polling): the panes'
            // onCompleted ran before the printer existed, and the
            // hydration publish can fire sectionLayoutChanged before
            // the Connections below attached. The apply reads the
            // CURRENT effective layout, so one arrival-time pass
            // covers both windows.
            if (root.printer != null) {
                root.applySectionOrder(infoContent, "information");
                root.applySectionOrder(statusContent, "status");
            }
            // The camera's configured flag is maintained imperatively
            // (root bindings here freeze); a printer attach is one of
            // its triggers — through the SAME coalescer as every
            // other camera trigger (the duplicate-start find).
            scheduleCameraApply();
        }
        focus: true
        // The Esc ladder lives in the DASHBOARD document now: that
        // document hosts every layer (this document's pop-overs, the
        // controls pane's pop-up, the file manager), so one
        // window-level shortcut there can close them all in order —
        // a ladder here could not see the controls pop-up and fired
        // the stage-exit branch from under it (the harness probe's
        // finding). This document only answers openPopOver, which
        // the dashboard's ladder writes through baseMonitorLoader.
        // The narrow-window collapse/lock contract — see INSTRUCTIONS.md
        // "Standing UI rules"; changes here require explicit
        // double-confirmation.
        // The ruling (the narrow-window rule, re-based on the camera):
        // every decision below reads the WEBCAM pane's own width, never
        // the stage's. The camera is what the panes' expansions take
        // their room from, and its width already carries whatever the
        // panes around it gave up (the controls pane's yield widens the
        // camera with no stage change at all). An expansion is blocked
        // while it would take the camera under its comfort minimum;
        // folding is what gives that room back.
        // One evaluation serves the fold and the release, and it is
        // driven by the camera rather than the window: a stage that
        // lands under the squeeze without ever crossing it (a jump)
        // still folds, because the rules run where the camera's width
        // settles.
        readonly property real cameraViewportWidth: cameraPane.viewportWidth
        property bool webcamSqueezed: root.cameraViewportWidth > 0 && root.cameraViewportWidth < 220 * screenScaleFactor
        // The expansion cost: the pane's expanded width less the
        // collapsed strip it replaces — what opening it takes off the
        // camera.
        readonly property real infoExpandCost: (270 - 44) * screenScaleFactor
        readonly property real statusExpandCost: (410 - 44) * screenScaleFactor
        // The forward check: an expansion is refused while the camera
        // could not stay at its comfort minimum through it.
        readonly property bool infoExpandBlocked: root.cameraViewportWidth - root.infoExpandCost < 220 * screenScaleFactor
        readonly property bool statusExpandBlocked: root.cameraViewportWidth - root.statusExpandCost < 220 * screenScaleFactor
        property bool infoPersistedCollapsed: root.printer != null ? root.printer.infoCollapsed : false
        property bool statusPersistedCollapsed: root.printer != null ? root.printer.statusCollapsed : false
        property bool infoAutoCollapsed: false
        property bool statusAutoCollapsed: false
        // The camera seen with a pane's OWN fold released: the room that
        // fold holds is credited back out, so the figure is the same
        // before and after the layout takes the fold's room and no
        // decision depends on how many passes the layout needed. It is
        // also the forward check read backwards — the fold releases
        // exactly when the lock would stop refusing the expansion.
        readonly property real infoOpenWidth: root.cameraViewportWidth - (root.infoAutoCollapsed && !root.infoPersistedCollapsed ? root.infoExpandCost : 0)
        readonly property real statusOpenWidth: root.cameraViewportWidth - (root.statusAutoCollapsed && !root.statusPersistedCollapsed ? root.statusExpandCost : 0)
        // The camera can only be trusted once the layout has taken the
        // fold the flags just made: in the pass that writes a flag the
        // camera still reads the pre-fold width, and folding the next
        // pane against it would fold one the first fold just made room
        // for (the probe's transient, and the click-twice report behind
        // it). The pane's own width is the layout's statement that its
        // fold has landed — and where the camera is pinned at its floor
        // it is the only signal there is, a fold there moving the panes
        // and not the camera. The room measure itself stays the camera.
        readonly property bool infoFoldLanded: infoPanel.width < 200 * screenScaleFactor
        onCameraViewportWidthChanged: root.applyNarrowWindowRules()
        onWidthChanged: root.applyNarrowWindowRules()
        function applyNarrowWindowRules() {
            // The un-laid-out document reads zero: no camera, no rule.
            if (!(root.cameraViewportWidth > 0)) {
                return;
            }
            // Read every measure before either flag moves: the cascade's
            // order is decided by the flags as they stand, never by a
            // value this evaluation is about to write.
            var infoOpen = root.infoOpenWidth;
            var statusOpen = root.statusOpenWidth;
            var infoWasCollapsed = root.infoCollapsed;
            var statusWasFolded = root.statusAutoCollapsed;
            var comfort = 220 * screenScaleFactor;
            if (!root.infoPersistedCollapsed) {
                // The information pane folds while the camera cannot
                // hold it, and reclaims its room LAST: while the status
                // pane's fold stands, reopening it would spend the very
                // room that fold is holding and the pair would land the
                // camera under its comfort (the release churn). Once
                // the status pane is back it measures its own room
                // again. A collapse the user made themselves is never
                // the fold's to take or to drop.
                root.infoAutoCollapsed = infoOpen < comfort || statusWasFolded;
            }
            if (!root.statusPersistedCollapsed) {
                // The cascade: the status pane folds once the
                // information pane's room is spent and the camera is
                // STILL under comfort, and unfolds again the moment the
                // camera can absorb it — the very check the lock
                // publishes, so the refusal and the release cannot
                // disagree.
                root.statusAutoCollapsed = statusOpen < comfort && root.infoFoldLanded && (infoWasCollapsed || statusWasFolded);
            }
        }
        onInfoAutoCollapsedChanged: {
            if (infoAutoCollapsed) {
                root.openPopOver = "";
            }
        }
        property bool infoCollapsed: root.infoPersistedCollapsed || root.infoAutoCollapsed
        // The narrow-window lock: an expansion that would crush the
        // camera is refused on every path — the fold's own reasons (the
        // auto collapse, a camera already under its comfort) and the
        // forward check — and the refusal says so (the console's
        // too-narrow precedent). Hiding a pane stays available; only
        // the expand direction waits for the camera's room.
        readonly property bool infoExpandLocked: root.infoCollapsed && (root.infoAutoCollapsed || root.webcamSqueezed || root.infoExpandBlocked)
        property bool statusCollapsed: root.statusPersistedCollapsed || root.statusAutoCollapsed
        readonly property bool statusExpandLocked: root.statusCollapsed && (root.statusAutoCollapsed || root.webcamSqueezed || root.statusExpandBlocked)
        property string connectionDotColour: root.printer != null && root.printer.monitorConnected ? MoonrakerTheme.successGreen : MoonrakerTheme.errorRed

        Connections {
            target: root.printer
            function onTypedControlsChanged() {
                meshSection.refreshMap();
                // The detail card refreshes its own map in its own
                // scope; this handler only owns the auto-close, and it
                // must still run when the card is not instantiated.
                if (root.printer == null) {
                    root.openPopOver = "";
                } else if (!root.printer.bedMeshAvailable && root.openPopOver === "mesh") {
                    // Only the pop-over that needs the mesh closes: the
                    // chart card must survive a mesh loss.
                    root.openPopOver = "";
                }
            }
        }

        RowLayout {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").width

            Cura.RoundedRectangle {
                id: infoPanel
                objectName: "infoPanel"
                // The collapsed readout may outrun a short pane: the
                // PANE clips, so no child can ever spill past its
                // bounds (the live report: every readout overflowed).
                clip: true
                // The pane's own width is where its fold LANDS — the one
                // signal left where the camera is pinned at its floor
                // (see the rule): the stage's width alone would stall
                // the cascade one pane short.
                onWidthChanged: root.applyNarrowWindowRules()
                onHeightChanged: root.updateInfoReadoutFits()
                // Collapsed, the pane shrinks to the toggle button and its
                // margins; the vertical title below explains the strip.
                Layout.preferredWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 270 * screenScaleFactor)
                Layout.minimumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 200 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 270 * screenScaleFactor)
                Layout.fillWidth: true
                Layout.fillHeight: true
                // No Layout.leftMargin here: the host RowLayout's
                // anchors.margins already indents every pane, and the
                // doubled left edge read wider than the right pane's
                // (the report).
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

                // While collapsed, a click anywhere on the strip expands
                // the pane; the header button stays on top of this area.
                MouseArea {
                    visible: root.infoCollapsed
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        // Auto-collapsed-by-width is not a clickable
                        // expand: only a wider window restores the
                        // pane (the header button's guard, and the
                        // console's strip precedent). Unguarded, this
                        // click discarded the user's own collapse and
                        // the pane sprang open on the next widen.
                        if (root.infoExpandLocked) {
                            return;
                        }
                        if (root.printer != null) {
                            root.printer.setInfoCollapsed(false);
                        }
                    }
                }

                RowLayout {
                    id: infoHeader
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
                    anchors.leftMargin: UM.Theme.getSize("thin_margin").width
                    anchors.rightMargin: UM.Theme.getSize("thin_margin").width
                    spacing: UM.Theme.getSize("thin_margin").width
                    // The toggle hugs the edge the pane collapses into:
                    // this pane is leftmost, so the button leads.
                    Cura.SecondaryButton {
                        id: infoCollapseButton
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        // Square at the OLD button width: the theme
                        // adds its padding around the 32px content, so
                        // the height tracks the rendered width (the
                        // ruling).
                        width: 28 * screenScaleFactor
                        iconSize: 12 * screenScaleFactor
                        height: width
                        implicitHeight: width

                        // The SAME theme-chevron family as the console
                        // and status toggles (the ruling: all
                        // pane collapse buttons uniform). This pane is
                        // leftmost and collapses left.
                        iconSource: root.infoCollapsed ? UM.Theme.getIcon("ChevronSingleRight") : UM.Theme.getIcon("ChevronSingleLeft")
                        onClicked: {
                            // Auto-collapsed-by-width is not a
                            // clickable toggle: only a wider window
                            // restores the pane (the console's
                            // too-narrow precedent).
                            if (root.infoExpandLocked) {
                                return;
                            }
                            // The NEW state is computed locally: the
                            // property binding may not have re-evaluated
                            // yet when this handler reads it back.
                            var collapsing = root.printer == null || !root.infoCollapsed;
                            if (root.printer != null) {
                                root.printer.setInfoCollapsed(collapsing);
                            }
                            // Collapsing the pane hides the pop-over's
                            // opener with it — the card must close too
                            // (the UX adjudication: only the section-
                            // collapse deviation stands).
                            if (collapsing) {
                                root.openPopOver = "";
                            }
                        }
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: root.infoExpandLocked ? "The window is too narrow — widen it to show the information." : (root.infoCollapsed ? "Show the information." : "Hide the information.")
                        }
                    }
                    // The configure trigger: the column configurer's
                    // glyph, adjacent to the collapse toggle (the
                    // adjudicated placement).
                    Cura.SecondaryButton {
                        id: infoConfigureButton
                        objectName: "configureInfoSectionsButton"
                        visible: !root.infoCollapsed
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        width: 28 * screenScaleFactor
                        height: width
                        implicitHeight: width
                        text: "⇄"
                        onClicked: {
                            root.buildConfigureRows("information");
                            // Positioned imperatively at open time,
                            // the dashboard popup's lesson: mapToItem
                            // bindings evaluate once and latch the
                            // pre-layout position (the live report:
                            // the status card opened inside the
                            // controls pane).
                            var infoEdge = infoHeader.mapToItem(root, infoHeader.x, 0);
                            infoConfigurePopOver.x = infoEdge.x;
                            infoConfigurePopOver.y = infoEdge.y + infoHeader.height + UM.Theme.getSize("thin_margin").height;
                            root.openPopOver = "sections-info";
                        }
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: "Configure the information sections."
                        }
                    }
                    UM.Label {
                        Layout.fillWidth: true
                        visible: !root.infoCollapsed
                        text: "Information"
                        font: UM.Theme.getFont("large_bold")
                        elide: Text.ElideRight
                    }
                }

                Flickable {
                    id: infoFlick
                    visible: !root.infoCollapsed
                    anchors.top: infoHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    // No right inset: the content's own 14px gutter is
                    // the only dead band right of the sections (the
                    // 4.5.0 live ruling — a margin's width, no more).
                    anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                    clip: true
                    contentWidth: width
                    contentHeight: infoContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: UM.ScrollBar {
                        id: infoScrollbar
                    }

                    ColumnLayout {
                        id: infoContent
                        objectName: "moonrakerInfoContent"
                        // The stored order applies HERE — before the
                        // first frame paints (the 4.5.0 live find).
                        Component.onCompleted: root.applySectionOrder(infoContent, "information")
                        // The constant gutter (the status pane's own
                        // precedent): binding the content width to the
                        // LIVE scrollbar width feeds the polish loop —
                        // the scrollbar overlays the gutter instead of
                        // squeezing the content in a feedback cycle.
                        // Layout.fillWidth is inert here (the Flickable
                        // is not a layout) — the 4.5.0 live find's
                        // 1px crush; the explicit width stays, trimmed
                        // to the 14px gutter alone.
                        width: infoFlick.width - 14
                        // Spacing lives on the children: collapsed sections
                        // must contribute nothing so headers stack flush.
                        spacing: 0
                        MeshSection {
                            id: meshSection
                            visible: root.printer == null || root.printer.sectionHiddenMap["meshmap"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onPopOverToggleRequested: function (name) {
                                root.openPopOver = root.openPopOver === name ? "" : name;
                            }
                        }
                        PlateProgressSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["plateprogress"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onPopOverToggleRequested: function (name) {
                                root.openPopOver = root.openPopOver === name ? "" : name;
                            }
                        }
                        PlateSection {
                            // During a print the picker shows either
                            // the plate (when the print carries the
                            // exclude-object data) or the download
                            // offer (the live rulings); it clears
                            // with the job epoch.
                            visible: root.printer != null && root.printer.sectionHiddenMap["plate"] !== true && (root.printer.printActive || root.printer.plateHasObjects)
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onPopOverToggleRequested: function (name) {
                                root.openPopOver = root.openPopOver === name ? "" : name;
                            }
                        }
                        TempHistorySection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["temphistory"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            miniSeries: root.miniChartSeries
                            miniHasSeries: root.miniChartHasSeries
                            onPopOverToggleRequested: function (name) {
                                root.openPopOver = root.openPopOver === name ? "" : name;
                            }
                        }
                    }
                }

                Connections {
                    target: root.printer
                    function onSectionLayoutChanged() {
                        root.applySectionOrder(infoContent, "information");
                        root.applySectionOrder(statusContent, "status");
                        root.buildConfigureRows("information");
                        root.buildConfigureRows("status");
                    }
                }

                // The collapsed strip: the toggle stays at the top and the
                // pane title reads bottom-to-top directly under it (the
                // mirror of the controls pane, which reads top-to-bottom).
                Item {
                    id: infoCollapsedTitleBox
                    visible: root.infoCollapsed
                    anchors.top: infoHeader.bottom
                    anchors.topMargin: UM.Theme.getSize("thin_margin").height
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: infoCollapsedTitle.implicitHeight
                    height: infoCollapsedTitle.implicitWidth
                    UM.Label {
                        id: infoCollapsedTitle
                        text: "Information"
                        font: UM.Theme.getFont("medium_bold")
                        color: UM.Theme.getColor("text_inactive")
                        rotation: -90
                        anchors.centerIn: parent
                    }
                }
                // The collapsed readout (the 2026-09-17
                // ruling): the hotend and bed temperatures from the
                // PRINTER's peripherals fill the empty space BELOW
                // the title — regular text, not the title's face.
                // The collapsed readout (the 2026-09-17
                // ruling): the hotend and bed temperatures from the
                // PRINTER's peripherals in ONE rotated flat row of
                // explicit children — the structure the
                // engine actually lays out (the wrapper/implicit
                // strips stayed zero-sized there, the live report).
                Item {
                    id: infoCollapsedReadoutBox
                    visible: root.infoCollapsed
                    clip: true
                    anchors.top: infoCollapsedTitleBox.bottom
                    anchors.topMargin: 2 * UM.Theme.getSize("default_margin").height
                    anchors.horizontalCenter: infoCollapsedTitleBox.horizontalCenter
                    // The box hugs the content: its height tracks
                    // the row's implicit width, so the centred row
                    // fills it and the strip starts at the margin
                    // under the title (direct row positioning hid
                    // the content on the engine, the live
                    // report). 18 is the label line height.
                    width: 18 * screenScaleFactor
                    height: infoReadoutRow.implicitWidth
                    Row {
                        id: infoReadoutRow
                        anchors.centerIn: parent
                        spacing: 2 * screenScaleFactor
                        rotation: -90
                        // The fit hides through OPACITY, never
                        // the visibility flag: an invisible group
                        // keeps its place in the layout, so the
                        // survivors can never re-centre in the box
                        // (the live report) — the strip stays
                        // anchored under
                        // the title.
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.infoHotendText !== "—"
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Thermometer.svg")
                        }
                        UM.Label {
                            objectName: "infoCollapsedReadoutText"
                            property bool fitHidden: false
                            visible: root.infoHotendText !== "—"
                            opacity: fitHidden ? 0 : 1
                            text: root.infoHotendText
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }
                        Item {
                            visible: root.infoBedText !== "—"
                            width: 8 * screenScaleFactor
                            height: 16 * screenScaleFactor
                        }
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.infoBedText !== "—"
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Bed.svg")
                        }
                        UM.Label {
                            property bool fitHidden: false
                            visible: root.infoBedText !== "—"
                            opacity: fitHidden ? 0 : 1
                            text: root.infoBedText
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            Item {
                id: cameraArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 180 * screenScaleFactor

                // Two SIBLING cards in the middle column: the webcam
                // card on top, the console card below it (the
                // ruling — the console nested inside the webcam card
                // read as one mis-anchored pane).
                ColumnLayout {
                    anchors.fill: parent
                    spacing: UM.Theme.getSize("default_margin").height

                    CameraPane {
                        id: cameraPane
                        traceCameraTiming: printer != null && printer.traceCameraTiming
                        Layout.fillWidth: true
                        // The webcam card ALWAYS fills: the layout
                        // allocates the console's capped preferred
                        // height first and the webcam card absorbs the
                        // rest — collapsing the console shrinks its
                        // preferred to the header row and the webcam
                        // grows into the freed space automatically.
                        Layout.fillHeight: true
                        Layout.preferredHeight: cameraPane.contentHeight
                        Layout.minimumHeight: 0
                        printerModel: root.printer
                        configured: root.cameraConfigured
                    }
                    // Console: a collapsing pane beneath the
                    // webcam. printer/gcode/script returns after
                    // Klipper processes the script, and its output
                    // streams back through the gcode store.
                    ConsolePane {
                        id: consolePanel
                        printerModel: root.printer
                        resizeFrame: cameraArea
                        Layout.fillWidth: true
                    }
                }
            }
            Cura.RoundedRectangle {
                id: statusPanel
                objectName: "statusPanel"
                // The collapsed readout may outrun a short pane: the
                // PANE clips, so no child can ever spill past its
                // bounds (the live report).
                clip: true
                onHeightChanged: root.updateStatusReadoutFits()
                // The pane's own width is where its fold LANDS — the one
                // signal left where the camera is pinned at its floor
                // (see the rule): the stage's width alone would stall
                // the cascade one pane short.
                onWidthChanged: root.applyNarrowWindowRules()
                // Collapsed, the pane shrinks to the toggle button and its
                // margins; the vertical title below explains the strip.
                Layout.preferredWidth: (root.statusCollapsed ? statusCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 410 * screenScaleFactor)
                Layout.minimumWidth: (root.statusCollapsed ? statusCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 260 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.statusCollapsed ? statusCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 410 * screenScaleFactor)
                Layout.fillWidth: true
                Layout.fillHeight: true
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

                // While collapsed, a click anywhere on the strip expands
                // the pane; the header button stays on top of this area.
                MouseArea {
                    visible: root.statusCollapsed
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        // Auto-collapsed-by-width is not a clickable
                        // expand: only a wider window restores the
                        // pane (the information pane's strip and the
                        // console's strip carry the same guard).
                        if (root.statusExpandLocked) {
                            return;
                        }
                        if (root.printer != null) {
                            root.printer.setStatusCollapsed(false);
                        }
                    }
                }

                RowLayout {
                    id: statusHeader
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
                    anchors.leftMargin: UM.Theme.getSize("thin_margin").width
                    anchors.rightMargin: UM.Theme.getSize("thin_margin").width
                    spacing: UM.Theme.getSize("thin_margin").width
                    UM.Label {
                        Layout.fillWidth: true
                        visible: !root.statusCollapsed
                        text: "Printer status"
                        font: UM.Theme.getFont("large_bold")
                        elide: Text.ElideRight
                    }
                    Rectangle {
                        // The connection dot rides the Printer status
                        // pane's title — the chosen spot for
                        // the always-readable connection state. Green
                        // when connected, red when not.
                        Layout.alignment: Qt.AlignVCenter
                        visible: !root.statusCollapsed
                        width: 10 * screenScaleFactor
                        height: 10 * screenScaleFactor
                        radius: 5 * screenScaleFactor
                        color: connectionDotColour
                        HoverHandler {
                            id: tooltipHover2
                        }
                        UM.ToolTip {
                            visible: tooltipHover2.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            // The transport detail rides the dot's
                            // tooltip: "connected over websocket" or
                            // "connected over HTTP polling" (the
                            // chosen spot for it).
                            text: root.printer != null && root.printer.monitorConnected ? (root.printer.connectionDetail.length > 0 ? "Connected to Moonraker — " + root.printer.connectionDetail + "." : "Connected to Moonraker.") : "Disconnected from Moonraker."
                        }
                    }
                    // Open the Moonraker UI in a browser, icon-style in the
                    // title row.
                    UM.SimpleButton {
                        id: frontendButton
                        visible: !root.statusCollapsed
                        Layout.alignment: Qt.AlignVCenter
                        width: 28 * screenScaleFactor
                        height: 28 * screenScaleFactor
                        color: UM.Theme.getColor("text_inactive")
                        hoverColor: UM.Theme.getColor("text")
                        iconSource: UM.Theme.getIcon("LinkExternal")
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.openFrontend();
                            }
                        }

                        HoverHandler {
                            id: tooltipHover3
                        }
                        UM.ToolTip {
                            visible: tooltipHover3.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: "Open the Moonraker frontend."
                        }
                    }
                    // The configure trigger, beside its collapse
                    // toggle (the adjudicated placement).
                    Cura.SecondaryButton {
                        id: statusConfigureButton
                        objectName: "configureStatusSectionsButton"
                        visible: !root.statusCollapsed
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        width: 28 * screenScaleFactor
                        height: width
                        implicitHeight: width
                        text: "⇄"
                        onClicked: {
                            root.buildConfigureRows("status");
                            // Imperative positioning, the same reason
                            // as the information card.
                            var statusEdge = statusHeader.mapToItem(root, statusHeader.x + statusHeader.width, 0);
                            statusConfigurePopOver.x = statusEdge.x - statusConfigurePopOver.width;
                            statusConfigurePopOver.y = statusEdge.y + statusHeader.height + UM.Theme.getSize("thin_margin").height;
                            root.openPopOver = "sections-status";
                        }
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: "Configure the printer-status sections."
                        }
                    }
                    // The toggle hugs the right edge: the pane is on the
                    // right of the screen and collapses into that edge.
                    // Left when collapsed (expand left), right when open.
                    Cura.SecondaryButton {
                        id: statusCollapseButton
                        Layout.alignment: Qt.AlignVCenter
                        fixedWidthMode: true
                        // Square at the OLD button width: the theme
                        // adds its padding around the 32px content, so
                        // the height tracks the rendered width (the
                        // ruling).
                        width: 28 * screenScaleFactor
                        iconSize: 12 * screenScaleFactor
                        height: width
                        implicitHeight: width

                        // The SAME theme-chevron family as the console
                        // and info toggles; this pane is rightmost and
                        // collapses right.
                        iconSource: root.statusCollapsed ? UM.Theme.getIcon("ChevronSingleLeft") : UM.Theme.getIcon("ChevronSingleRight")
                        onClicked: {
                            // Auto-collapsed-by-width is not a
                            // clickable toggle: only a wider window
                            // restores the pane (the information
                            // pane's toggle carries the same guard).
                            if (root.statusExpandLocked) {
                                return;
                            }
                            if (root.printer != null) {
                                root.printer.setStatusCollapsed(!root.statusCollapsed);
                            }
                        }
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: root.statusExpandLocked ? "The window is too narrow — widen it to show the printer status." : (root.statusCollapsed ? "Show the printer status." : "Hide the printer status.")
                        }
                    }
                }

                Flickable {
                    id: statusFlick
                    objectName: "moonrakerStatusFlick"
                    visible: !root.statusCollapsed
                    anchors.top: statusHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    // No right inset: the content's own 14px gutter is
                    // the only dead band right of the sections — the
                    // same ruling the information pane above carries,
                    // and the inset that used to double the pane's
                    // right gap against the scroll bar.
                    anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                    clip: true
                    contentWidth: width
                    contentHeight: statusContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: UM.ScrollBar {
                        id: statusScrollbar
                    }

                    Column {
                        id: statusContent
                        objectName: "moonrakerStatusContent"
                        // The stored order applies HERE — the column's
                        // own completion, after its children exist and
                        // BEFORE the first frame paints (the 4.5.0 live
                        // find: any later apply is a visible jump).
                        Component.onCompleted: root.applySectionOrder(statusContent, "status")
                        // The constant gutter, exactly as the information
                        // pane above rules it: the scrollbar overlays the
                        // gutter rather than squeezing the content in a
                        // live-width feedback cycle. Layout.fillWidth is
                        // inert here (a Flickable is not a layout), so the
                        // explicit viewport-relative width is the only
                        // thing keeping the column at its own implicit
                        // width while the sections paint past the pane.
                        width: statusFlick.width - 14
                        // Spacing lives on the children: collapsed sections
                        // must contribute nothing so headers stack flush.
                        // Sections own their implicit heights. A positioner
                        // stacks them without a second layout solver feeding
                        // changing row heights back through the whole pane.
                        spacing: 0
                        JobSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["job"] !== true
                            width: statusContent.width
                            printerModel: root.printer
                        }

                        TempsSection {
                            width: statusContent.width
                            visible: root.printer != null && root.printer.temperatureItems.length > 0 && root.printer.sectionHiddenMap["temps"] !== true
                            printerModel: root.printer
                        }

                        FansInfoSection {
                            width: statusContent.width
                            visible: root.printer != null && root.printer.fanItems.length > 0 && root.printer.sectionHiddenMap["fansinfo"] !== true
                            printerModel: root.printer
                        }
                        FilamentSection {
                            width: statusContent.width
                            visible: root.printer != null && root.printer.filamentSensorItems.length > 0 && root.printer.sectionHiddenMap["filament"] !== true
                            printerModel: root.printer
                        }
                        SystemInfoSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["systeminfo"] !== true
                            width: statusContent.width
                            printerModel: root.printer
                        }
                        McusSection {
                            width: statusContent.width
                            visible: root.printer != null && root.printer.mcuItems.length > 0 && root.printer.sectionHiddenMap["mcus"] !== true
                            printerModel: root.printer
                        }
                    }
                }

                // The collapsed strip: the toggle stays at the top and the
                // pane title reads bottom-to-top directly under it.
                // The loading prompt (the 2026-09-16 request): an
                // overlay ABOVE the flick, never a layout child — a
                // layout child flipping visibility reflowed the
                // section stack and fed a polish loop (the
                // Windows run).
                Item {
                    anchors.fill: statusFlick
                    visible: !root.statusCollapsed && (root.printer == null || root.printer.monitorLoading)
                    UM.Label {
                        anchors.centerIn: parent
                        text: "Loading printer data…"
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                }

                Item {
                    id: statusCollapsedTitleBox
                    visible: root.statusCollapsed
                    anchors.top: statusHeader.bottom
                    anchors.topMargin: UM.Theme.getSize("thin_margin").height
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: statusCollapsedTitle.implicitHeight
                    height: statusCollapsedTitle.implicitWidth + 24 * screenScaleFactor
                    UM.Label {
                        id: statusCollapsedTitle
                        text: "Printer status"
                        font: UM.Theme.getFont("medium_bold")
                        color: UM.Theme.getColor("text_inactive")
                        rotation: 90
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 12 * screenScaleFactor
                    }
                    Rectangle {
                        // The dot stays visible while the pane is
                        // collapsed too — leading the title, in its
                        // own band at the top.
                        anchors.top: parent.top
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.topMargin: 3 * screenScaleFactor
                        width: 10 * screenScaleFactor
                        height: 10 * screenScaleFactor
                        radius: 5 * screenScaleFactor
                        color: connectionDotColour
                    }
                }
                // The collapsed readout (the 2026-09-17
                // ruling): the dual-stacked progress bars fill the
                // empty space BELOW the title — print above layer,
                // thin tracks at the pill weight, each labelled and
                // shown only while it means something. The fills run
                // top-to-bottom, the strip's reading direction.
                Item {
                    id: statusCollapsedBarsBox
                    visible: root.statusCollapsed
                    clip: true
                    anchors.top: statusCollapsedTitleBox.bottom
                    // The standard margin: the readout's text must
                    // sit LEVEL with the controls pane's readout (the
                    // live report).
                    anchors.topMargin: 2 * UM.Theme.getSize("default_margin").height
                    anchors.horizontalCenter: statusCollapsedTitleBox.horizontalCenter
                    // The box hugs the content: its height tracks
                    // the row's implicit width, so the centred row
                    // fills it and the strip starts at the margin
                    // under the title (the live report). 18 is the
                    // label line height.
                    width: 18 * screenScaleFactor
                    height: statusReadoutRow.implicitWidth
                    // ONE rotated flat row of explicit children —
                    // the structure the engine lays out.
                    // Each bar is its own pair: glyph, label, then
                    // the TRACK — whose 60 px span lies ALONG the
                    // row's main axis, so the rotation makes it run
                    // along the strip (vertical) with the 4 px
                    // thickness across. The fill grows from the
                    // label end along the span.
                    Row {
                        id: statusReadoutRow
                        anchors.centerIn: parent
                        spacing: 2 * screenScaleFactor
                        rotation: 90
                        // The fit hides through OPACITY, never
                        // the visibility flag: the survivors keep
                        // their places (the live report).
                        // The ETA leads the strip (the live ruling):
                        // duration and finish clock, with the preview
                        // pane's own glyphs — the hourglass then the
                        // clock.
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            // Unavailable values do not render — the
                            // value and its glyph both hide (the
                            // live ruling).
                            visible: root.etaAvailable
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Hourglass.svg")
                            HoverHandler {
                                id: tooltipHover4
                            }
                            UM.ToolTip {
                                visible: tooltipHover4.hovered
                                targetPoint: Qt.point(parent.width / 2, 0)
                                x: 0
                                y: parent.height + UM.Theme.getSize("default_margin").height
                                width: UM.Theme.getSize("tooltip").width
                                // The improve-Eta mirror names the
                                // action (the panel's catch): the
                                // strip's glyph is not the button.
                                text: "Improve the estimate — download and index this print's G-code without loading it into the preview."
                            }
                        }
                        UM.Label {
                            objectName: "statusCollapsedReadoutLabel"
                            property bool fitHidden: false
                            visible: root.etaAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorEta : "—"
                            // The longest ETA form must clear the slot
                            // or the value wraps — the same live-report
                            // width the finish clock's slot carries.
                            width: 84 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                            HoverHandler {
                                id: tooltipHover5
                            }
                            UM.ToolTip {
                                visible: tooltipHover5.hovered
                                targetPoint: Qt.point(parent.width / 2, 0)
                                x: 0
                                y: parent.height + UM.Theme.getSize("default_margin").height
                                width: UM.Theme.getSize("tooltip").width
                                text: "Improve the estimate — download and index this print's G-code without loading it into the preview."
                            }
                        }
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.finishAvailable
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Clock.svg")
                        }
                        UM.Label {
                            objectName: "statusCollapsedReadoutLabel"
                            property bool fitHidden: false
                            visible: root.finishAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorFinish : "—"
                            // The finish reads day-first on a print
                            // crossing midnight, and that form is the
                            // widest the strip holds: the slot must
                            // clear it or the value wraps (the live
                            // report).
                            width: 84 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            // The layer count (the live ruling): after
                            // the ETA, before the print progress, with
                            // the preview card's layer glyph.
                            visible: root.layerCountAvailable
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Layer.svg")
                        }
                        UM.Label {
                            objectName: "statusCollapsedReadoutLabel"
                            property bool fitHidden: false
                            visible: root.layerCountAvailable
                            opacity: fitHidden ? 0 : 1
                            // Implicit width (the live ruling: the
                            // layer info may reflow — it changes
                            // slowly — so the gap to the print bar
                            // stays tight while "888 / 888" still
                            // fits).
                            text: root.printer != null ? root.printer.monitorLayer : "—"
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }

                        Item {
                            // The margin between the layer count and
                            // the progress group (the live ruling) —
                            // the stacked fills themselves stay
                            // touching.
                            visible: root.printer != null && root.printer.printActive
                            width: 8 * screenScaleFactor
                            height: 16 * screenScaleFactor
                        }
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.printer != null && root.printer.printActive
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Progress.svg")
                        }
                        UM.Label {
                            objectName: "statusCollapsedReadoutLabel"
                            property bool fitHidden: false
                            visible: root.printer != null && root.printer.printActive
                            opacity: fitHidden ? 0 : 1
                            text: "Progress"
                            width: 60 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                        }
                        Rectangle {
                            id: progressTrack
                            property bool fitHidden: false
                            visible: root.printer != null && root.printer.printActive
                            opacity: fitHidden ? 0 : 1
                            width: 60 * screenScaleFactor
                            // THE STACKED BAR (the live ruling): the
                            // print fill is the BOTTOM half and the
                            // layer fill the TOP half, touching at
                            // the centre line — no gap. Without layer
                            // info the print fill takes the whole
                            // height. The transparent body with the
                            // 2 px text outline frames the extent, so
                            // the fills read against the work
                            // remaining.
                            height: 18 * screenScaleFactor
                            color: "transparent"
                            border.width: 2 * screenScaleFactor
                            border.color: UM.Theme.getColor("text")
                            Rectangle {
                                anchors.left: parent.left
                                anchors.bottom: parent.bottom
                                // The three fills share the height in
                                // thirds when the pause is scheduled,
                                // halves without one, and the print
                                // takes the whole height without
                                // layer info (the live ruling).
                                height: parent.height * (root.printer != null && root.printer.monitorLayerProgress >= 0 ? (root.printer.nextPauseFraction >= 0 ? 1 / 3 : 0.5) : 1.0)
                                // monitorProgress is a PERCENTAGE
                                // (0..100) while the clamp read it as
                                // a fraction — anything past 1%
                                // pegged the bar full (the live
                                // report). The layer value is
                                // already 0..1.
                                width: parent.width * Math.max(0, Math.min(1, root.printer != null ? root.printer.monitorProgress / 100 : 0))
                                color: UM.Theme.getColor("primary")
                            }
                            Rectangle {
                                // The next scheduled pause's fill (the
                                // live ruling): the MIDDLE of the
                                // stack, the mesh's neon orange — NOT
                                // RENDERED while no pause lies ahead
                                // (the gate, not a zero width).
                                objectName: "statusNextPauseFill"
                                visible: root.printer != null && root.printer.nextPauseFraction >= 0
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                height: parent.height / 3
                                width: parent.width * Math.max(0, Math.min(1, root.printer != null ? root.printer.nextPauseFraction : 0))
                                color: MoonrakerTheme.neonOrange
                            }
                            Rectangle {
                                anchors.left: parent.left
                                anchors.top: parent.top
                                height: parent.height * (root.printer != null && root.printer.nextPauseFraction >= 0 ? 1 / 3 : 0.5)
                                width: parent.width * Math.max(0, Math.min(1, root.printer != null ? root.printer.monitorLayerProgress : 0))
                                color: UM.Theme.getColor("primary")
                            }
                        }
                        UM.ColorImage {
                            color: UM.Theme.getColor("text")
                            property bool fitHidden: false
                            visible: root.flowAvailable
                            opacity: fitHidden ? 0 : 1
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            source: Qt.resolvedUrl("../resources/svg/Flow.svg")
                        }
                        UM.Label {
                            // The flow pair's own name (the harness
                            // rule): the OTHER strip labels share
                            // statusCollapsedReadoutLabel, and the
                            // standby scenario proves the available
                            // flow renders while the unavailable
                            // groups hide.
                            objectName: "statusCollapsedFlowLabel"
                            property bool fitHidden: false
                            visible: root.flowAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorFlowRate : ""
                            // "888.8 mm^3/s" must sit comfortably
                            // (the live ruling).
                            width: 110 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        // The pop-overs are overlay siblings of the pane RowLayout, not
        // layout children: anchored children inside a layout reflow
        // every pane (and Qt logs undefined-behavior warnings), and a
        // layout child cannot overlap the layout. A click outside any
        // open card closes it — but a click on the card's own surface
        // must not: the card's background owns no mouse handler, so
        // the press lands here and the bounds check keeps the card
        // open (the live report: in-bounds clicks dismissed the
        // pop-over).
        function clickInsideOpenPopOver(x, y) {
            var cards = [infoConfigurePopOver, statusConfigurePopOver, chartPanel, meshPanel, platePanel, plateProgressPanel];
            for (var i = 0; i < cards.length; i++) {
                var card = cards[i];
                if (card.visible && x >= card.x && x <= card.x + card.width && y >= card.y && y <= card.y + card.height) {
                    return true;
                }
            }
            return false;
        }
        MouseArea {
            id: outsideClickLayer
            visible: root.openPopOver !== ""
            anchors.fill: parent
            z: 998
            acceptedButtons: Qt.LeftButton
            onClicked: {
                if (root.clickInsideOpenPopOver(mouse.x, mouse.y)) {
                    return;
                }
                root.openPopOver = "";
                root.selectedChartSensor = "";
            }
        }

        SectionConfigurePopOver {
            id: infoConfigurePopOver
            visible: root.openPopOver === "sections-info"
            title: "Information sections"
            paneId: "information"
            rows: root.infoConfigureRows
            hidden: root.infoConfigureHidden
            width: 320 * screenScaleFactor
            // Positioned by mapToItem, never anchors to a header
            // row's inner items (an illegal anchor drops silently
            // and the card lands at the window's top-left — the live
            // report). The header's own x/y are read into the
            // bindings, so the placement re-evaluates once the layout
            // positions the header (the dashboard card's -292
            // lesson).
            // The x/y land from the trigger's onClicked (the
            // imperative positioning above) — bindings here latched
            // the pre-layout position (the live report).
            onLayoutCommitted: function (order, hidden) {
                if (root.printer != null) {
                    root.printer.setSectionLayout("information", order, hidden);
                }
            }
        }
        SectionConfigurePopOver {
            id: statusConfigurePopOver
            visible: root.openPopOver === "sections-status"
            title: "Printer-status sections"
            paneId: "status"
            rows: root.statusConfigureRows
            hidden: root.statusConfigureHidden
            width: 320 * screenScaleFactor
            // The x/y land from the trigger's onClicked (the
            // imperative positioning above) — bindings here latched
            // the pre-layout position (the live report).
            onLayoutCommitted: function (order, hidden) {
                if (root.printer != null) {
                    root.printer.setSectionLayout("status", order, hidden);
                }
            }
        }

        // The overlay frame stays here: the camera column's coordinates
        // and the one-at-a-time open flag belong to this document. The
        // chosen sensor is the host's too — the dashboard's escape
        // ladder clears it — so the card takes it in and answers by
        // signal.
        TemperatureDetailPopover {
            id: chartPanel
            objectName: "moonrakerChartDetail"
            open: root.openPopOver === "chart"
            printerModel: root.printer
            selectedChartSensor: root.selectedChartSensor
            onSensorSelected: root.selectedChartSensor = sensor
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            // Grows with the legend: three rows of sensors fit the base
            // height; each further row adds its line height, capped at
            // the monitor area.
            height: Math.min(590 * screenScaleFactor + Math.max(0, Math.ceil((root.printer != null ? root.printer.temperatureChartLegend.series.length : 0) / 2) - 3) * 30 * screenScaleFactor, parent.height - 2 * UM.Theme.getSize("default_margin").height)
        }

        // The overlay frame stays here: this is the document that owns
        // the camera column's coordinates and the one-at-a-time open
        // flag. The card keeps its own content and refresh.
        BedMeshDetail {
            id: meshPanel
            open: root.openPopOver === "mesh"
            printerModel: root.printer
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            height: Math.min((520 * screenScaleFactor) + UM.Theme.getSize("default_margin").height, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            contentWidth: 390 * screenScaleFactor
        }

        MonitorPopOver {
            id: platePanel
            visible: root.openPopOver === "plate" && root.printer != null
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            height: Math.min((520 * screenScaleFactor) + UM.Theme.getSize("default_margin").height, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            contentWidth: 390 * screenScaleFactor
            title: "Exclude Object Picker"

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                active: root.openPopOver === "plate" && root.printer != null
                sourceComponent: plateContent
            }
        }

        MonitorPopOver {
            id: plateProgressPanel
            visible: root.openPopOver === "plateprogress" && root.printer != null
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            height: Math.min((780 * screenScaleFactor) + UM.Theme.getSize("default_margin").height, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            // The plate's 585 plus the pause column beside it (the live
            // report: the schedule belongs in the width, not below the
            // plate) — clamped to the room right of the card's own x, so
            // the pause column is never the part that leaves the pane.
            contentWidth: Math.min(940 * screenScaleFactor, root.width - x - UM.Theme.getSize("default_margin").width)
            title: "Print Follower"

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                active: root.openPopOver === "plateprogress" && root.printer != null
                sourceComponent: plateProgressContent
            }
        }

        Component {
            id: plateContent
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: UM.Theme.getSize("thin_margin").height

                // The picker's own download offer (the live request):
                // an empty plate carries the action instead of a dead
                // canvas.
                PlateDownloadAction {
                    Layout.fillWidth: true
                    visible: root.printer != null && !root.printer.plateHasObjects
                    printerModel: root.printer
                    idleInstruction: "Click here to download and index the print — the picker draws the objects from the print's data."
                }

                PlateExcludeFace {
                    id: plateFace
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 240 * screenScaleFactor
                    printerModel: root.printer
                    plate: root.printer != null ? root.printer.plateObjects : null
                    // No toolhead dot in the picker (the live ruling)
                    // — the map reads as the CONTROL, not a follower.
                    onExcludeRequested: function (name) {
                        if (root.printer != null) {
                            root.printer.excludeObject(name);
                        }
                    }
                    onRestoreRequested: function (name) {
                        if (root.printer != null) {
                            root.printer.restoreObject(name);
                        }
                    }
                }

                UM.Label {
                    // The permanent single line (the mesh "hover row"
                    // idiom): counter > hover (name and state) > hint.
                    // NoWrap is the reflow lock: the theme label
                    // defaults to wrapping, and a wrapped verdict
                    // grew the row and reflowed the canvas above
                    // (the live report).
                    Layout.fillWidth: true
                    wrapMode: Text.NoWrap
                    text: plateFace.clickProgress >= 2 ? "Click again to " + plateFace.pendingAction + " (" + plateFace.clickProgress + " of 3)" : (plateFace.hoveredName !== "" ? plateFace.hoverDetail() : (root.printer != null && root.printer.plateObjects.truncated > 0 ? root.printer.plateObjects.truncated + " objects omitted from this map (256-object limit)" : "Triple-click to exclude, or restore an excluded object"))
                    elide: Text.ElideRight
                    color: plateFace.hoveredName !== "" ? plateFace.hoverInk() : UM.Theme.getColor("text_inactive")
                    horizontalAlignment: Text.AlignHCenter
                }

                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: UM.Theme.getSize("narrow_margin").width
                    Row {
                        spacing: 4 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: width
                            radius: width / 2
                            color: UM.Theme.getColor("text")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Included"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 4 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: width
                            radius: width / 2
                            color: UM.Theme.getColor("primary")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Current"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        // The printed state derives from the index's
                        // executed motions — no index, no green (the
                        // live ruling: the legend must not promise
                        // what the data cannot say).
                        visible: root.printer != null && root.printer.plateTrackingAvailable
                        spacing: 4 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: width
                            radius: width / 2
                            color: MoonrakerTheme.plateCurrent
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Printed"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 4 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: width
                            radius: width / 2
                            color: MoonrakerTheme.dangerRed
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Excluded"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }
                UM.Label {
                    // The outcome slot: the gesture receipts land here —
                    // the no-confirm ruling's confirmation (a refusal
                    // must never be silent).
                    Layout.fillWidth: true
                    text: root.printer != null ? root.printer.actionStatus : ""
                    color: UM.Theme.getColor("text")
                    font: UM.Theme.getFont("default_italic")
                    horizontalAlignment: Text.AlignHCenter
                }
                // The index offer for the printed state (the live
                // rulings): the picker itself carries the download
                // control — a compact row, back in the layout after
                // the overlay overlapped the plate.
                PlateDownloadAction {
                    Layout.fillWidth: true
                    visible: root.printer != null && root.printer.plateHasObjects && !root.printer.plateTrackingAvailable
                    printerModel: root.printer
                    idleInstruction: "Download and index the print to track the printed state."
                }

                UM.Label {
                    Layout.fillWidth: true
                    text: "A triple-click sends the command immediately — there is no confirmation dialog. Restoring an object does not replace skipped layers."
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default_italic")
                    wrapMode: Text.WordWrap
                }
            }
        }

        Component {
            id: plateProgressContent
            // Two columns: the schedule's rows lived under the plate and
            // squeezed the face onto the card's clipped bottom edge (the live
            // report), so the card is wider and the pause block pays in width
            // — never in the plate's height.
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: UM.Theme.getSize("default_margin").width

                // The slider functions live on the COMPONENT ROOT: a
                // function declared on a nested item is invisible to its
                // own descendants, and these are called from all over the
                // plate's column (the probe's ReferenceError).
                // The slider follows the model's anchor — the live
                // layer while attached, the frozen one after a seek —
                // and never fights a drag in flight or a settle.
                function syncLayerSlider() {
                    if (layerSlider.interacting || layerSeekTimer.running) {
                        return;
                    }
                    if (root.printer != null && root.printer.plateProgressAnchor >= 0) {
                        layerSlider.value = root.printer.plateProgressAnchor;
                    }
                }
                function commitLayerSeek() {
                    if (root.printer != null) {
                        root.printer.setFollowerLayerAnchor(layerSlider.selectedValue());
                    }
                }
                function layerReadout() {
                    if (root.printer == null || root.printer.plateLayerCount <= 0) {
                        return "—";
                    }
                    var index = root.printer.followerAttached ? root.printer.plateProgressAnchor : layerSlider.selectedValue();
                    if (index < 0) {
                        return "—";
                    }
                    return (Math.round(index) + 1) + " / " + root.printer.plateLayerCount;
                }
                function syncProgressSlider() {
                    if (layerProgressSlider.interacting) {
                        return;
                    }
                    var split = root.printer != null ? root.printer.plateSplit : null;
                    layerProgressSlider.value = split != null ? Math.max(0, split) : 0;
                }
                function commitProgressSeek() {
                    if (root.printer != null) {
                        var total = root.printer.plateLayerMotionCount;
                        var selected = layerProgressSlider.selectedValue();
                        if (total >= 10000)
                            selected = Math.round(Math.round(selected / total * 10000) * total / 10000);
                        root.printer.setFollowerLayerProgress(selected);
                    }
                }
                // On the component root with the other slider functions: a
                // function declared on a nested item is invisible to that
                // item's own descendants, and the plate's column calls this
                // one from beside its face (the engine's ReferenceError).
                function _feedRenderView() {
                    if (root.printer != null) {
                        // The device-pixel backing (bounded supersampling):
                        // the worker paints at the screen's physical
                        // resolution and the scene-graph samples down to
                        // the logical face — never an enlarged 1x raster.
                        root.printer.setFollowerView("popover", progressFace.viewScale, progressFace.lineScale, progressFace.width, progressFace.height, progressFace.compact, progressFace.viewPanX, progressFace.viewPanY, Math.min(2.0, Math.max(1.0, Screen.devicePixelRatio)), progressFace.toolpathWidthPx());
                    }
                }

                ColumnLayout {
                    // The plate's width is the layout's invariant — the
                    // popover's old content width, held at every pane
                    // width. Without it the plate absorbs the whole
                    // deficit itself and the empty schedule column beside
                    // it takes the face's width instead.
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 585 * screenScaleFactor - 2 * UM.Theme.getSize("default_margin").width
                    Layout.minimumWidth: 585 * screenScaleFactor - 2 * UM.Theme.getSize("default_margin").width
                    spacing: UM.Theme.getSize("thin_margin").height
                    // The keyboard path: the layer slider takes the focus
                    // on open, so the arrows drive it immediately (the
                    // live request).
                    Component.onCompleted: layerSlider.forceActiveFocus()
                    Connections {
                        target: progressFace
                        // The settle owns the native-render feed: the
                        // view re-rasters ONCE, 150 ms after the last
                        // zoom/pan/line change (the review's finding 1 —
                        // per-tick feeds churned the renderer).
                        function onViewSettled() {
                            _feedRenderView();
                        }
                        function onManuallyPanned() {
                            if (root.printer != null)
                                root.printer.setFollowerKeepCentred(false);
                        }
                        function onPlotChanged() {
                            var plot = progressFace.plot;
                            if (root.printer != null && plot != null) {
                                // The SURFACE is explicit (the review's
                                // finding 1): this face is the popover.
                                root.printer.setFollowerPlot("popover", plot.bed.offsetX, plot.bed.offsetY, plot.sx, plot.sy, plot.bed.bedXMin, plot.bed.bedYMax);
                                _feedRenderView();
                            }
                        }
                    }
                    PlateProgressFace {
                        id: progressFace
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 200 * screenScaleFactor
                        printerModel: root.printer
                        scrubVector: root.printer != null ? root.printer.plateScrubVector : null
                        progress: root.printer != null ? ({
                                "available": root.printer.plateProgressAvailable,
                                "scrubVector": progressFace.scrubVector,
                                "reason": root.printer.plateProgressReason,
                                "layers": root.printer.plateLayers,
                                "split": root.printer.plateSplit,
                                "partial": root.printer.platePartial,
                                "anchor": root.printer.plateProgressAnchor,
                                "method": "motion index",
                                "navigationData": root.printer.plateNavigationData,
                                "navigationSplit": root.printer.plateNavigationSplit,
                                "navigationBacking": root.printer.plateNavigationBacking,
                                "sceneEpoch": root.printer.plateSceneEpoch
                            }) : null
                        dot: root.printer != null ? root.printer.plateDot : null
                        // The persisted global view settings (the live
                        // ruling) — the face's own handlers fire on the
                        // rebinds and re-raster.
                        showPrevious: root.printer != null ? root.printer.followerShowPrevious : true
                        showNext: root.printer != null ? root.printer.followerShowNext : true
                        // The layer ghost frames the frozen layer's
                        // partial fill on ANY layer — attached or
                        // detached (the live request).
                        showBase: root.printer != null ? root.printer.followerShowBase : true
                        showTravels: root.printer != null ? root.printer.followerShowTravels : false
                        showRetractions: root.printer != null ? root.printer.followerShowRetractions : false
                        showUnretractions: root.printer != null ? root.printer.followerShowUnretractions : false
                        motionSmoothing: root.printer != null ? root.printer.followerMotionSmoothing : false
                        smoothToolpaths: root.printer != null ? root.printer.followerAntialiasing : false
                        softwareRendering: root.printer != null ? root.printer.followerSoftwareRendering : false
                        trueThickness: root.printer != null && root.printer.followerTrueThickness
                        pixelLineWidth: true
                        keepCentred: root.printer != null && root.printer.followerKeepCentred === true
                        lineScale: root.printer != null ? root.printer.followerLineScale : 1.0
                        // The follow state (the centred follow is
                        // retired — the per-poll re-pan was too slow).
                        attached: root.printer == null || root.printer.followerAttached
                    }

                    // The toolhead view controls (the 4.6.0 request): the
                    // jump is one shot onto the dot's bed position, the
                    // option keeps it there. The jump is disabled with
                    // nothing to centre on; the option is a preference and
                    // persists, so it stays live. At 100% the whole bed
                    // fits and both are moot — they hide in place (the
                    // live request), the preference itself is untouched.

                    // The checkbox's OWN text label (the live reports: a
                    // separate Label neither toggles on click nor hugs the
                    // indicator, and the wrapper rows warped the heights).
                    // The control's text is part of its clickable area and
                    // carries the theme's own snug indicator gap — so the
                    // rows read as pairs at the standard flow gap.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("narrow_margin").height
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("narrow_margin").height
                            Flow {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("narrow_margin").height
                                UM.CheckBox {
                                    text: "True thickness"
                                    checked: root.printer != null && root.printer.followerTrueThickness
                                    onToggled: if (root.printer != null)
                                        root.printer.setFollowerTrueThickness(checked)
                                }
                                UM.CheckBox {
                                    text: "Previous layer"
                                    checked: root.printer != null ? root.printer.followerShowPrevious : true
                                    onToggled: {
                                        if (root.printer != null) {
                                            root.printer.setFollowerShowPrevious(checked);
                                        }
                                    }
                                }
                                UM.CheckBox {
                                    text: "Next layer"
                                    checked: root.printer != null ? root.printer.followerShowNext : true
                                    onToggled: {
                                        if (root.printer != null) {
                                            root.printer.setFollowerShowNext(checked);
                                        }
                                    }
                                }
                                UM.CheckBox {
                                    text: "Layer ghost"
                                    checked: root.printer != null ? root.printer.followerShowBase : true
                                    onToggled: {
                                        if (root.printer != null) {
                                            root.printer.setFollowerShowBase(checked);
                                        }
                                    }
                                }
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("narrow_margin").height
                                UM.CheckBox {
                                    text: "Travels"
                                    checked: root.printer != null ? root.printer.followerShowTravels : false
                                    onToggled: {
                                        if (root.printer != null) {
                                            root.printer.setFollowerShowTravels(checked);
                                        }
                                    }
                                }
                                UM.CheckBox {
                                    text: "Retractions"
                                    checked: root.printer != null ? root.printer.followerShowRetractions : false
                                    onToggled: if (root.printer != null)
                                        root.printer.setFollowerShowRetractions(checked)
                                }
                                UM.CheckBox {
                                    text: "Unretractions"
                                    checked: root.printer != null ? root.printer.followerShowUnretractions : false
                                    onToggled: if (root.printer != null)
                                        root.printer.setFollowerShowUnretractions(checked)
                                }
                                UM.CheckBox {
                                    visible: progressFace.gpuRendering
                                    text: "Antialiasing"
                                    checked: root.printer != null ? root.printer.followerAntialiasing : false
                                    onToggled: {
                                        if (root.printer != null) {
                                            root.printer.setFollowerAntialiasing(checked);
                                        }
                                    }
                                }
                            }
                        }
                        // The view reset: right of the checkbox row's free
                        // space, outside the canvas entirely — its
                        // appearance never reflows the plate.
                        UM.Label {
                            visible: progressFace.available() && !progressFace.compact && progressFace.viewScale > 1.0
                            text: "Reset view"
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("primary")
                            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                            MouseArea {
                                anchors.fill: parent
                                onClicked: progressFace.resetView()
                            }
                        }
                    }

                    FollowerColourControls {
                        Layout.fillWidth: true
                        printerModel: root.printer
                        face: progressFace
                    }

                    // The stroke thickness control (the live request):
                    // scales every follower stroke, 0.5x to 2x.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("thin_margin").width
                        UM.Label {
                            text: "Line thickness"
                            color: UM.Theme.getColor("text_inactive")
                        }
                        Cura.SecondaryButton {
                            id: thinnerButton
                            fixedWidthMode: true
                            width: 28 * screenScaleFactor
                            text: "−"
                            enabled: root.printer != null && (root.printer.followerTrueThickness || root.printer.followerLineScale > 1.0)
                            onClicked: {
                                if (root.printer != null) {
                                    root.printer.setFollowerLineScale(Math.max(1.0, root.printer.followerLineScale - 1.0));
                                }
                            }
                        }
                        UM.Label {
                            text: root.printer != null && root.printer.followerTrueThickness ? "True" : (root.printer != null ? root.printer.followerLineScale : 1.0).toFixed(0) + " px"
                            width: 34 * screenScaleFactor
                            horizontalAlignment: Text.AlignHCenter
                        }
                        Cura.SecondaryButton {
                            id: thickerButton
                            fixedWidthMode: true
                            width: 28 * screenScaleFactor
                            text: "+"
                            enabled: root.printer != null && (root.printer.followerTrueThickness || root.printer.followerLineScale < 8.0)
                            onClicked: {
                                if (root.printer != null) {
                                    root.printer.setFollowerLineScale(Math.min(8.0, root.printer.followerLineScale + 1.0));
                                }
                            }
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        // The toolhead controls ride the line-thickness
                        // row (the live request: the centred follow's
                        // retirement emptied their own row — the sliders
                        // below close the gap). The jump hides at 100%
                        // and the attach persists the row.
                        UM.CheckBox {
                            objectName: "moonrakerFollowerKeepCentred"
                            visible: jumpButton.visible
                            text: "Keep centred"
                            enabled: progressFace.dotAvailable()
                            checked: root.printer != null && root.printer.followerKeepCentred === true
                            onToggled: {
                                if (root.printer != null)
                                    root.printer.setFollowerKeepCentred(checked);
                            }
                        }
                        Cura.SecondaryButton {
                            id: jumpButton
                            objectName: "moonrakerFollowerJump"
                            visible: progressFace.viewScale > 1.0 && progressFace.attached
                            text: "Jump to toolhead"
                            enabled: progressFace.dotAvailable()
                            onClicked: progressFace.centreOnToolhead()
                        }
                        Cura.SecondaryButton {
                            id: attachButton
                            objectName: "moonrakerFollowerAttach"
                            fixedWidthMode: true
                            width: 76 * screenScaleFactor
                            text: root.printer != null && root.printer.followerAttached ? "Detach" : "Attach"
                            // Before the print reaches an indexed layer,
                            // detach starts manual viewing at the first layer.
                            enabled: root.printer != null && (root.printer.printIndexReady || root.printer.plateLayerCount > 0)
                            onClicked: {
                                if (root.printer != null) {
                                    root.printer.setFollowerAttached(!root.printer.followerAttached);
                                }
                            }
                        }
                    }

                    // The layer selection (the 4.6.0 request): the slider
                    // seeks the anchor the face draws. A seek from the
                    // LIVE layer is itself the detach — the model freezes
                    // on the committed layer (the live request: the slider
                    // must never sit dead while attached). A seek commits
                    // only once the drag quietens: every step rebuilds a
                    // layer window and rehydrates it, so a release commits
                    // at once and a drag settles first.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("thin_margin").width
                        UM.Label {
                            // The reserved label column: both slider rows
                            // share it, so the tracks line up exactly over
                            // each other (the live request).
                            Layout.preferredWidth: 100 * screenScaleFactor
                            Layout.maximumWidth: 100 * screenScaleFactor
                            text: "Layer"
                            color: UM.Theme.getColor("text_inactive")
                            elide: Text.ElideRight
                        }
                        OutlineSlider {
                            id: layerSlider
                            objectName: "moonrakerFollowerLayerSlider"
                            Layout.fillWidth: true
                            from: 0
                            to: Math.max(0, (root.printer != null ? root.printer.plateLayerCount : 0) - 1)
                            stepSize: 1
                            enabled: root.printer != null && root.printer.plateLayerCount > 0
                            onValueTuning: {
                                // The raw tick rides to the model: the
                                // seek's perceived latency includes the
                                // debounce, so the trace records it. The
                                // signal is the slider's own — it never
                                // fires during teardown.
                                if (root.printer != null) {
                                    root.printer.seekAnchorTicked();
                                }
                                layerSeekTimer.restart();
                            }
                            onValueCommitted: {
                                layerSeekTimer.stop();
                                progressFace.endInteraction();
                                commitLayerSeek();
                            }
                        }
                        UM.Label {
                            objectName: "moonrakerFollowerLayerReadout"
                            Layout.preferredWidth: 64 * screenScaleFactor
                            Layout.maximumWidth: 64 * screenScaleFactor
                            horizontalAlignment: Text.AlignRight
                            text: layerReadout()
                        }
                    }

                    // The within-layer progress (the 4.6.0 request): a
                    // SCRUBBER, not a display bar — the slider plays the
                    // frozen layer through manually (the live request).
                    // Attached it mirrors the live split; a scrub is
                    // itself the detach (the layer slider's rule).
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("thin_margin").width
                        UM.Label {
                            // The same reserved column as the layer row:
                            // the two tracks align exactly (the live
                            // request).
                            Layout.preferredWidth: 100 * screenScaleFactor
                            Layout.maximumWidth: 100 * screenScaleFactor
                            text: "Layer progress"
                            color: UM.Theme.getColor("text_inactive")
                            elide: Text.ElideRight
                        }
                        OutlineSlider {
                            id: layerProgressSlider
                            objectName: "moonrakerFollowerLayerProgress"
                            Layout.fillWidth: true
                            from: 0
                            to: Math.max(0, root.printer != null ? root.printer.plateLayerMotionCount : 0)
                            // One tenth of a percent, bounded by one motion.
                            stepSize: Math.max(1, to / 10000)
                            enabled: root.printer != null && root.printer.plateProgressAvailable && root.printer.plateLayerMotionCount > 0
                            // The scrub commits on every drag tick — the
                            // fill tracks the thumb at frame rate (the
                            // live request), and the release commits once
                            // more for the final position. A scrub input
                            // also ends any camera interaction outright:
                            // a latched warm raster standing over the
                            // hidden exact scene during scrub repaints is
                            // the wrong picture between frames.
                            onValueTuning: {
                                progressFace.endInteraction();
                                commitProgressSeek();
                            }
                            onValueCommitted: {
                                progressFace.endInteraction();
                                commitProgressSeek();
                            }
                        }
                        UM.Label {
                            objectName: "moonrakerFollowerLayerProgressReadout"
                            // The same reserved width as the layer row's
                            // readout: the tracks stay equal.
                            Layout.preferredWidth: 64 * screenScaleFactor
                            Layout.maximumWidth: 64 * screenScaleFactor
                            horizontalAlignment: Text.AlignRight
                            text: {
                                // An expression, not a call: the readout
                                // must re-bind on the split and the count
                                // (the live report — a call froze it and
                                // an unset count read NaN%).
                                var total = root.printer != null ? root.printer.plateLayerMotionCount : 0;
                                var split = root.printer != null ? root.printer.plateSplit : null;
                                if (total <= 0 || split == null || isNaN(split) || isNaN(total)) {
                                    return "—";
                                }
                                var pct = Math.max(0, split) / total * 100;
                                return isNaN(pct) ? "—" : pct.toFixed(2) + "%";
                            }
                        }
                    }

                    Timer {
                        id: layerSeekTimer
                        // 40 ms (was 80/250): current-layer demand now
                        // commits independently of ghosts and can preempt
                        // background preparation. This still coalesces a
                        // drag burst while removing another 40 ms from the
                        // raw-tick -> available path. Explicit commits
                        // remain immediate (the timer is stopped above).
                        interval: 40
                        onTriggered: commitLayerSeek()
                    }
                    Connections {
                        target: root.printer
                        function onPlateProgressChanged() {
                            syncLayerSlider();
                            syncProgressSlider();
                        }
                        function onFollowerViewChanged() {
                            syncLayerSlider();
                        }
                    }
                }

                // The rule between the plate and the schedule. It is a
                // hairline, and it takes its width from the schedule
                // column's slack — that column's minimum is zero — so the
                // plate's own width, the layout's invariant, is untouched.
                Rectangle {
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    Layout.minimumWidth: 1
                    Layout.maximumWidth: 1
                    color: UM.Theme.getColor("border")
                }

                // The pause at the end of a layer (the 4.6.0 request):
                // the popover's OWN slider picks the layer, so the
                // schedule targets the END of the layer it stands on —
                // the card's schedule, the popover's own candidate. Its
                // own column (the live report): stacked under the plate
                // the list squeezed the face and ran onto the card's
                // clipped bottom edge, so the width pays for it instead.
                ColumnLayout {
                    id: pauseColumn
                    // Wide enough for a row's longest suffix ("— baked ·
                    // passed") at the schedule's own font, and it yields
                    // to the plate first when the pane is tight: with no
                    // minimum of its own it takes only the room that is
                    // left over.
                    Layout.preferredWidth: 340 * screenScaleFactor
                    Layout.minimumWidth: 0
                    // The column fills the card: the schedule's list takes
                    // whatever height the plate's column does not use.
                    Layout.fillHeight: true
                    spacing: UM.Theme.getSize("thin_margin").height
                    // Whether the column's clear action has anything to
                    // clear: the foot row collapses its slot on this.
                    readonly property bool clearAvailable: root.printer != null && root.printer.pauseAtLayerHasClearable === true
                    // The rows are read once at open: the block carries
                    // values before the popover exists, so a signal-only
                    // sync would leave the list blank until the next
                    // publish (~2.5 s).
                    Component.onCompleted: syncPauseRows()

                    // The rows live in a STABLE model the publishes never
                    // replace: the block arrives every poll with fresh ETA
                    // strings, and handing that array straight to the view
                    // replaced the model and jumped the scroll (the card's
                    // own live report). syncPauseRows() diffs it in place
                    // instead, so the model identity — and the scroll —
                    // never move on their own.
                    ListModel {
                        id: pauseBlockModel
                        objectName: "moonrakerFollowerPauseListModel"
                    }

                    function syncPauseRows() {
                        var incoming = root.printer != null && root.printer.pauseAtLayerItems != null ? root.printer.pauseAtLayerItems : [];
                        var keep = {};
                        for (var i = 0; i < incoming.length; i++) {
                            keep[Number(incoming[i].layer || 0)] = true;
                        }
                        for (var r = pauseBlockModel.count - 1; r >= 0; r--) {
                            if (keep[pauseBlockModel.get(r).layerNo] !== true) {
                                pauseBlockModel.remove(r);
                            }
                        }
                        for (var k = 0; k < incoming.length; k++) {
                            var row = incoming[k];
                            // EVERY role is normalised to a concrete value:
                            // a role whose first value is undefined is
                            // dropped from a ListModel, and the delegate's
                            // bare role lookup then throws.
                            var payload = {
                                "layerNo": Number(row.layer || 0),
                                "eta": String(row.eta || ""),
                                "pauseWord": String(row.state || "scheduled"),
                                "passed": row.passed === true
                            };
                            var at = -1;
                            for (var f = 0; f < pauseBlockModel.count; f++) {
                                if (pauseBlockModel.get(f).layerNo === payload.layerNo) {
                                    at = f;
                                    break;
                                }
                            }
                            if (at === -1) {
                                // The published list is sorted, so a fresh
                                // entry lands at its sorted position among
                                // the rows already there — an append alone
                                // would bury a pause scheduled below one
                                // that already exists.
                                var pos = pauseBlockModel.count;
                                for (var s = 0; s < pauseBlockModel.count; s++) {
                                    if (pauseBlockModel.get(s).layerNo > payload.layerNo) {
                                        pos = s;
                                        break;
                                    }
                                }
                                pauseBlockModel.insert(pos, payload);
                            } else if (pauseBlockModel.get(at).eta !== payload.eta || pauseBlockModel.get(at).pauseWord !== payload.pauseWord || pauseBlockModel.get(at).passed !== payload.passed) {
                                pauseBlockModel.set(at, payload);
                            }
                        }
                    }

                    Connections {
                        target: root.printer
                        function onPauseAtLayerChanged() {
                            pauseColumn.syncPauseRows();
                        }
                    }

                    UM.Label {
                        Layout.fillWidth: true
                        // The heading collapses with its own text: it is
                        // for a schedule that has rows (a height, never a
                        // visibility — the no-reflow rule).
                        Layout.preferredHeight: text.length > 0 ? implicitHeight : 0
                        text: pauseBlockModel.count > 0 ? "Enabled pauses" : ""
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default_bold")
                    }

                    Item {
                        id: pauseListViewport
                        Layout.fillWidth: true
                        // The list owns the column's remaining height (the
                        // live request): as many rows as fit, and the
                        // chevrons mark the ones that do not.
                        Layout.fillHeight: true
                        property real rowSpacing: 2 * screenScaleFactor

                        // The wheel over the list belongs to the LIST: an
                        // unaccepted notch fell through to the camera under
                        // the card and zoomed the webcam (the live report).
                        // A list with nothing to scroll swallows it too.
                        WheelHandler {
                            acceptedDevices: PointerDevice.Mouse
                            onWheel: function (wheel) {
                                wheel.accepted = true;
                            }
                        }

                        ListView {
                            id: pauseListView
                            anchors.fill: parent
                            clip: true
                            spacing: pauseListViewport.rowSpacing
                            // A schedule of five or fewer rows is never
                            // dragged off its own content.
                            interactive: contentHeight > height
                            boundsBehavior: Flickable.StopAtBounds
                            model: pauseBlockModel
                            delegate: Row {
                                id: pauseRow
                                width: pauseListView.width
                                height: UM.Theme.getSize("action_button").height
                                spacing: UM.Theme.getSize("thin_margin").width
                                // The roles are DIRECT delegate-context
                                // properties (the canonical ListModel
                                // idiom): the layer and state ROLES carry
                                // non-colliding names, because bare `layer`
                                // and `state` hit Qt's built-in Item.layer /
                                // Item.state on some engines — every row
                                // then read layer 0 (the card's live
                                // report) while eta and passed still
                                // resolved.
                                property int pauseLayer: Number(layerNo)
                                property string pauseEta: String(eta || "")
                                // "scheduled" | "fired" | "passed" |
                                // "failed" | "timed_out" | "baked" — a
                                // missed pause stays listed; a baked pause
                                // is read-only (the card's rulings).
                                property string pauseState: String(pauseWord || "scheduled")
                                readonly property bool pauseMissed: pauseState === "failed" || pauseState === "timed_out"
                                readonly property bool pauseBaked: pauseState === "baked"
                                readonly property bool pausePassed: passed === true || pauseState === "passed"

                                UM.Label {
                                    width: Math.max(0, parent.width - removePauseGlyph.width - parent.spacing)
                                    height: parent.height
                                    text: "End of layer " + pauseRow.pauseLayer + (!pauseRow.pausePassed && pauseRow.pauseEta.length > 0 ? " · " + pauseRow.pauseEta : "") + (pauseRow.pauseMissed ? " — pause not taken" : "") + (pauseRow.pauseBaked ? (pauseRow.pausePassed ? " — baked · passed" : " — baked") : (pauseRow.pauseState === "passed" ? " — passed" : ""))
                                    color: pauseRow.pauseMissed ? UM.Theme.getColor("error") : (pauseRow.pausePassed ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text"))
                                    font: UM.Theme.getFont("default")
                                    verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                }

                                UM.Label {
                                    id: removePauseGlyph
                                    // The narrow ✕ (the card's ruling): a
                                    // glyph, not a chrome button — it never
                                    // hides; a baked row dims it and the
                                    // click does nothing.
                                    width: parent.height
                                    height: parent.height
                                    text: "✕"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    color: pauseRow.pauseBaked ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("error")
                                    font: UM.Theme.getFont("medium_bold")
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: !pauseRow.pauseBaked
                                        hoverEnabled: true
                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                        onClicked: {
                                            if (root.printer != null) {
                                                root.printer.removePauseAtLayer(pauseRow.pauseLayer);
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Scroll affordances, the card's idiom: small
                        // chevrons centred over the list — an up arrow
                        // while more content is above, a down arrow while
                        // more is below (the ruling).
                        UM.Label {
                            text: "↑"
                            visible: pauseListView.height > 0 && pauseListView.contentY > 2
                            anchors.top: pauseListView.top
                            anchors.horizontalCenter: pauseListView.horizontalCenter
                            anchors.topMargin: 4 * screenScaleFactor
                            color: UM.Theme.getColor("primary")
                            font: UM.Theme.getFont("medium_bold")
                        }
                        UM.Label {
                            text: "↓"
                            visible: pauseListView.height > 0 && pauseListView.contentY < pauseListView.contentHeight - pauseListView.height - 2
                            anchors.bottom: pauseListView.bottom
                            anchors.horizontalCenter: pauseListView.horizontalCenter
                            anchors.bottomMargin: 4 * screenScaleFactor
                            color: UM.Theme.getColor("primary")
                            font: UM.Theme.getFont("medium_bold")
                        }
                    }

                    UM.Label {
                        // Why the button below is dead, in the card's own
                        // words. The line collapses by height while it has
                        // nothing to say — a height, never a visibility.
                        Layout.fillWidth: true
                        Layout.preferredHeight: text.length > 0 ? implicitHeight : 0
                        text: {
                            var model = root.printer;
                            if (model == null || model.pauseAtLayerScheduled === true || model.pauseAtLayerCanToggle === true) {
                                return "";
                            }
                            var why = model.pauseAtLayerUnavailableText;
                            return why ? "Can't schedule: " + why : "";
                        }
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default_italic")
                        wrapMode: Text.WordWrap
                        elide: Text.ElideRight
                    }

                    // The two actions sit together at the foot of the
                    // column (the live request): schedule or remove the
                    // pause the slider stands on, and clear the rest.
                    RowLayout {
                        Layout.fillWidth: true
                        // Nothing to clear means no slot, no gap: the
                        // schedule button then takes the whole row (the
                        // live request).
                        spacing: pauseColumn.clearAvailable ? UM.Theme.getSize("thin_margin").width : 0
                        Cura.SecondaryButton {
                            id: pauseAtLayerButton
                            objectName: "moonrakerFollowerPauseButton"
                            Layout.fillWidth: true
                            // The label centres in the slack this button
                            // takes (the live request); the theme's own
                            // content row packs from the left, so the
                            // fixed-width mode is what centring needs.
                            fixedWidthMode: true
                            // The button never hides (the no-reflow rule): it
                            // disables, and the line below it says why.
                            enabled: root.printer != null && root.printer.pauseAtLayerActive === true && (root.printer.pauseAtLayerScheduled === true || root.printer.pauseAtLayerCanToggle === true)
                            text: {
                                // The candidate is a 1-based human layer, 0
                                // while no layer is known (the card's own
                                // contract).
                                var layer = Number(root.printer != null ? root.printer.pauseAtLayerCandidate : 0) || 0;
                                if (layer <= 0) {
                                    return "Pause at end of layer";
                                }
                                return root.printer.pauseAtLayerScheduled === true ? "Remove pause after layer " + layer : "Pause at end of layer " + layer;
                            }
                            onClicked: {
                                if (root.printer != null) {
                                    root.printer.togglePauseAtLayer(root.printer.pauseAtLayerCandidate);
                                }
                            }
                        }

                        Cura.SecondaryButton {
                            id: clearPausesButton
                            // Collapses in place while nothing is clearable (a
                            // baked-only list has no manual rows — the card's
                            // own ruling): a height, never a visibility — and
                            // the width goes with it, so the schedule button
                            // beside it takes the whole row.
                            // Its own screen's word (the live request): the
                            // card keeps the longer line. The width is the
                            // short word plus the theme's own side padding,
                            // with a hair of slack so the label never elides.
                            fixedWidthMode: true
                            Layout.preferredWidth: pauseColumn.clearAvailable ? 60 * screenScaleFactor : 0
                            Layout.preferredHeight: pauseColumn.clearAvailable ? UM.Theme.getSize("action_button").height : 0
                            enabled: pauseColumn.clearAvailable
                            text: "Clear"
                            onClicked: {
                                if (root.printer != null) {
                                    root.printer.clearPauseAtLayer();
                                }
                            }
                        }
                    }
                }
            }
        }
        // The chart's hover values: an immediate tooltip that sticks to
        // the cursor tail. It lives OUTSIDE the clipped pop-over card
        // deliberately — a tooltip is allowed (and expected) to overflow
        // any boundary, and this way the card itself never reflows.
        Item {
            id: chartHoverTooltip
            visible: root.openPopOver === "chart" && chartPanel.hoverClockProxy !== ""
            z: 1000
            // Sized to the content, margins included — the box must
            // always contain its values, and it may overflow any
            // boundary per the ruling. The y side is chosen
            // by the room BELOW the cursor, so a tall box flips above
            // instead of sliding off the bottom of the stage.
            width: tooltipColumn.implicitWidth + 2 * UM.Theme.getSize("narrow_margin").width
            height: tooltipColumn.implicitHeight + 2 * UM.Theme.getSize("narrow_margin").height
            // The card publishes its cursor in its OWN frame; the
            // overlay's frame is this document's.
            property point anchorPoint: chartPanel.mapToItem(root, chartPanel.hoverCursor.x, chartPanel.hoverCursor.y)
            x: Math.min(Math.max(0, anchorPoint.x + 14), Math.max(0, root.width - width - 4))
            y: anchorPoint.y + height + 16 > root.height ? Math.max(0, anchorPoint.y - height - 10) : anchorPoint.y + 16

            Cura.RoundedRectangle {
                anchors.fill: parent
                color: UM.Theme.getColor("main_background")
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
            }
            ColumnLayout {
                id: tooltipColumn
                anchors.fill: parent
                anchors.margins: UM.Theme.getSize("narrow_margin").width
                spacing: UM.Theme.getSize("narrow_margin").height
                UM.Label {
                    text: chartPanel.hoverClockProxy
                    font: UM.Theme.getFont("medium")
                    color: UM.Theme.getColor("text")
                }
                // The rows are the legend's stable identities: a hover
                // publish updates each row's text by sensor name, so
                // the delegates are never torn down while the cursor
                // moves.
                Repeater {
                    model: root.printer != null ? root.printer.temperatureChartLegend.series : []
                    RowLayout {
                        visible: modelData.visible
                        spacing: UM.Theme.getSize("narrow_margin").width
                        Rectangle {
                            width: 8 * screenScaleFactor
                            height: 8 * screenScaleFactor
                            radius: 4 * screenScaleFactor
                            color: modelData.color
                        }
                        UM.Label {
                            text: modelData.label + ": " + (chartPanel.hoverValuesProxy[modelData.name] !== undefined ? chartPanel.hoverValuesProxy[modelData.name] : "—")
                            font: UM.Theme.getFont("default")
                        }
                    }
                }
            }
        }
    }
}
