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
import "info"
import "status"
import "temperature"

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
            var container = paneId === "information" ? infoPanel.content : statusPanel.content;
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
            fitGroup(infoPanel.readoutRow, 2, 0, infoPanel);
            // The spacer between the groups is a child too.
            fitGroup(infoPanel.readoutRow, 2, 3, infoPanel);
        }
        function updateStatusReadoutFits() {
            // The ETA pairs lead, then the layer count, then (past
            // the margin child) the stacked progress group, then
            // the flow pair at the strip's end (the live ruling).
            fitGroup(statusPanel.readoutRow, 2, 0, statusPanel);
            fitGroup(statusPanel.readoutRow, 2, 2, statusPanel);
            fitGroup(statusPanel.readoutRow, 2, 4, statusPanel);
            fitGroup(statusPanel.readoutRow, 3, 7, statusPanel);
            fitGroup(statusPanel.readoutRow, 2, 10, statusPanel);
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
                root.applySectionOrder(infoPanel.content, "information");
                root.applySectionOrder(statusPanel.content, "status");
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

        Connections {
            target: root.printer
            function onTypedControlsChanged() {
                // The mini map's refresh is the pane's own; this
                // handler only owns the auto-close, and it must still
                // run when the card is not instantiated.
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

            InfoPane {
                id: infoPanel
                printerModel: root.printer
                // The frozen fold contract's own state, read here and
                // written back through the pane's intents.
                infoCollapsed: root.infoCollapsed
                infoExpandLocked: root.infoExpandLocked
                hotendText: root.infoHotendText
                bedText: root.infoBedText
                miniChartSeries: root.miniChartSeries
                miniChartHasSeries: root.miniChartHasSeries
                // The pane's own width is where its fold LANDS — the one
                // signal left where the camera is pinned at its floor
                // (see the rule): the stage's width alone would stall
                // the cascade one pane short.
                onWidthChanged: root.applyNarrowWindowRules()
                onHeightChanged: root.updateInfoReadoutFits()
                onContentReady: root.applySectionOrder(infoPanel.content, "information")
                onPopOverToggleRequested: function (name) {
                    root.openPopOver = root.openPopOver === name ? "" : name;
                }
                onConfigureRequested: {
                    root.buildConfigureRows("information");
                    // Positioned imperatively at open time,
                    // the dashboard popup's lesson: mapToItem
                    // bindings evaluate once and latch the
                    // pre-layout position (the live report:
                    // the status card opened inside the
                    // controls pane).
                    var infoEdge = infoPanel.mapToItem(root, infoPanel.headerAnchor.x, infoPanel.headerAnchor.y);
                    infoConfigurePopOver.x = infoEdge.x;
                    infoConfigurePopOver.y = infoEdge.y + UM.Theme.getSize("thin_margin").height;
                    root.openPopOver = "sections-info";
                }
                onCollapseToggled: function (collapsing) {
                    // Collapsing the pane hides the pop-over's opener
                    // with it — the card must close too (the UX
                    // adjudication: only the section-collapse
                    // deviation stands).
                    if (collapsing) {
                        root.openPopOver = "";
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
            StatusPane {
                id: statusPanel
                printerModel: root.printer
                // The frozen fold contract's own state, read here and
                // written back through the pane's intents.
                statusCollapsed: root.statusCollapsed
                statusExpandLocked: root.statusExpandLocked
                etaAvailable: root.etaAvailable
                finishAvailable: root.finishAvailable
                layerCountAvailable: root.layerCountAvailable
                flowAvailable: root.flowAvailable
                // The pane's own width is where its fold LANDS — the one
                // signal left where the camera is pinned at its floor
                // (see the rule): the stage's width alone would stall
                // the cascade one pane short.
                onWidthChanged: root.applyNarrowWindowRules()
                onHeightChanged: root.updateStatusReadoutFits()
                onContentReady: root.applySectionOrder(statusPanel.content, "status")
                onConfigureRequested: {
                    root.buildConfigureRows("status");
                    // Positioned imperatively at open time, the
                    // dashboard popup's lesson: mapToItem bindings
                    // evaluate once and latch the pre-layout position.
                    var statusEdge = statusPanel.mapToItem(root, statusPanel.headerAnchor.x, statusPanel.headerAnchor.y);
                    statusConfigurePopOver.x = statusEdge.x - statusConfigurePopOver.width;
                    statusConfigurePopOver.y = statusEdge.y + UM.Theme.getSize("thin_margin").height;
                    root.openPopOver = "sections-status";
                }
                onCollapseToggled: function (collapsing) {
                    // Collapsing the pane hides the pop-over's opener
                    // with it — the card must close too (the UX
                    // adjudication: only the section-collapse
                    // deviation stands).
                    if (collapsing) {
                        root.openPopOver = "";
                    }
                }
            }
        }

        // The stored section layout: one signal re-applies BOTH panes'
        // order and rebuilds BOTH configure cards — the stage's own
        // pass, kept above the panes.
        Connections {
            target: root.printer
            function onSectionLayoutChanged() {
                root.applySectionOrder(infoPanel.content, "information");
                root.applySectionOrder(statusPanel.content, "status");
                root.buildConfigureRows("information");
                root.buildConfigureRows("status");
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

        ObjectPickerPopover {
            id: platePanel
            open: root.openPopOver === "plate"
            printerModel: root.printer
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            height: Math.min((520 * screenScaleFactor) + UM.Theme.getSize("default_margin").height, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            contentWidth: 390 * screenScaleFactor
        }

        PrintFollowerPopover {
            id: plateProgressPanel
            open: root.openPopOver === "plateprogress"
            printerModel: root.printer
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            height: Math.min((780 * screenScaleFactor) + UM.Theme.getSize("default_margin").height, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            // The plate's 585 plus the pause column beside it (the live
            // report: the schedule belongs in the width, not below the
            // plate) — clamped to the room right of the card's own x, so
            // the pause column is never the part that leaves the pane.
            contentWidth: Math.min(940 * screenScaleFactor, root.width - x - UM.Theme.getSize("default_margin").width)
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
