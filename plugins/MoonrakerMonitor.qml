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
        property bool allChartSensorsHidden: {
            var legend = root.printer != null ? root.printer.temperatureChartLegend : ({
                    "series": []
                });
            if (legend.series.length === 0) {
                return false;
            }
            for (var i = 0; i < legend.series.length; ++i) {
                if (legend.series[i].visible) {
                    return false;
                }
            }
            return true;
        }
        property string selectedChartSensorLabel: {
            var legend = root.printer != null ? root.printer.temperatureChartLegend : ({
                    "series": []
                });
            for (var i = 0; i < legend.series.length; ++i) {
                if (legend.series[i].name === root.selectedChartSensor) {
                    return legend.series[i].label;
                }
            }
            return root.selectedChartSensor;
        }
        property string selectedChartColor: {
            var legend = root.printer != null ? root.printer.temperatureChartLegend : ({
                    "series": []
                });
            for (var i = 0; i < legend.series.length; ++i) {
                if (legend.series[i].name === root.selectedChartSensor) {
                    return legend.series[i].color;
                }
            }
            return "";
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
            // The gcode-store poll follows the console's OWN collapse
            // state (the ruling: poll only while the console
            // is on screen, with a backfill on expand). A printer that
            // attaches with the console collapsed starts without the
            // poll; expanding starts it (and seeds the backfill).
            if (root.printer != null) {
                root.printer.setConsoleExpanded(root.printer.sectionExpandedMap["console"] !== false);
            }
            consoleSection.consoleRenderedLines = 0;
            consoleSection.consoleDroppedSeen = root.printer != null ? root.printer.consoleDropped : 0;
            // Revisions are monotonic per model lifetime; on a new
            // printer the pane re-renders from scratch anyway, so the
            // cursor resets with the other two (the architecture
            // panel's asymmetry note).
            consoleSection.consoleRevisionsSeen = root.printer != null ? root.printer.consoleRevisions : 0;
            consoleText.text = "";
            consoleSection.consoleSyncLines();
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

        // The picker's dialog implementation is a platform module, so it
        // is created on the first click rather than at construction: a
        // host whose platform builds no colour dialog must still get a
        // monitor, and the quick swatches work without it either way.
        property var chartColorDialogComponent: null
        property var chartColorDialog: null

        function openChartColorDialog() {
            if (chartColorDialog === null) {
                if (chartColorDialogComponent === null) {
                    chartColorDialogComponent = Qt.createComponent("MoonrakerChartColorDialog.qml");
                }
                if (chartColorDialogComponent.status !== Component.Ready) {
                    console.log("Moonraker colour picker unavailable: " + chartColorDialogComponent.errorString());
                    return;
                }
                chartColorDialog = chartColorDialogComponent.createObject(root);
                if (chartColorDialog === null) {
                    console.log("Moonraker colour picker failed to instantiate: " + chartColorDialogComponent.errorString());
                    return;
                }
                chartColorDialog.title = root.printer != null && root.printer.britishSpelling ? "Sensor colour" : "Sensor color";
                chartColorDialog.accepted.connect(root.applyChartColorChoice);
            }
            chartColorDialog.open();
        }

        function applyChartColorChoice() {
            if (chartColorDialog === null || root.printer == null || root.selectedChartSensor === "") {
                return;
            }
            var colour = chartColorDialog.selectedColor;
            var hex = "#" + ((1 << 24) + (Math.round(colour.r * 255) << 16) + (Math.round(colour.g * 255) << 8) + Math.round(colour.b * 255)).toString(16).slice(-6);
            root.printer.setTemperatureSensorColor(root.selectedChartSensor, hex);
        }

        Connections {
            target: root.printer
            function onTypedControlsChanged() {
                meshSection.refreshMap();
                // The detail map refreshes via the Connections inside
                // meshContent — its id is component-scoped and invisible
                // here; the section exposes its refresh as an accessor
                // so this handler's auto-close always runs.
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
                            source: Qt.resolvedUrl("Thermometer.svg")
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
                            source: Qt.resolvedUrl("Bed.svg")
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
                    Cura.RoundedRectangle {
                        id: consolePanel
                        Layout.fillWidth: true
                        // Auto-collapse below 350 px (the
                        // live report: the console's buttons overflowed
                        // when the window was crushed). The persisted
                        // expand state is untouched — the width decides
                        // the EFFECTIVE state, and the gcode-store poll
                        // follows it (poll only while expanded).
                        readonly property bool tooNarrow: consoleColumn.width < 350 * screenScaleFactor
                        onTooNarrowChanged: {
                            if (root.printer != null) {
                                if (tooNarrow || root.printer.sectionExpandedMap["console"] === false) {
                                    root.printer.setConsoleExpanded(false);
                                } else {
                                    root.printer.setConsoleExpanded(true);
                                }
                            }
                        }
                        // NO fillHeight: the card hugs the webcam card
                        // directly (a fill slot plus a maximum clamp
                        // left a huge gap between the cards — a
                        // live report). Its height is the
                        // user's, bounded by the clamp window below.
                        // UNTIL they drag the handle, the card keeps the
                        // pane default: the live test found even
                        // 55% of the column "way too high" — ~28% it is.
                        // Collapsed, the card is a header strip sized by
                        // the HANDLE and the BUTTON with equal top/bottom
                        // margins — those two are the largest elements and
                        // decide the strip (the ruling). The
                        // inner column's implicit does not shrink reliably
                        // once its content hides, so the collapsed height
                        // is explicit.
                        // EXPLICIT height, never the inner column's
                        // implicit: the real Cura engine computed the
                        // implicit from a collapsed chain and the card
                        // rendered two lines tall with a white gap (a
                        // report; the harness engine disagreed).
                        // The pane bounds are the clamp window: the
                        // console's floor keeps the header row and the
                        // input row usable, and its ceiling leaves the
                        // webcam card its own header plus a viewport — a
                        // pane crushed under the pointer is the hazard of
                        // the earlier report. A stored height
                        // that no longer fits a smaller stage renders
                        // inside the clamps WITHOUT losing the user's
                        // intent: the model keeps what they set.
                        readonly property real consoleHandleHeight: Math.max(12 * screenScaleFactor, UM.Theme.getSize("thin_margin").height)
                        readonly property real consoleCollapsedHeight: consoleCollapseButton.height + 2 * UM.Theme.getSize("thin_margin").height + consoleHandleHeight
                        readonly property real consoleMinHeight: 140 * screenScaleFactor
                        readonly property real consoleWebcamFloor: 150 * screenScaleFactor
                        readonly property real consoleMaxHeight: Math.max(consoleMinHeight, cameraArea.height - consoleWebcamFloor - UM.Theme.getSize("default_margin").height)
                        readonly property real consoleDefaultHeight: Math.max(190 * screenScaleFactor, cameraArea.height * 0.28)
                        readonly property bool consoleExpanded: root.printer != null && root.printer.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow
                        // The live drag height (0 = not dragging): the
                        // drag previews locally and commits ONCE on
                        // release, like the tuning sliders — a commit per
                        // move would rewrite the state file ~60 times a
                        // second for a value nobody reads until the pane
                        // settles.
                        property real consoleDragHeight: 0
                        property real consoleResizeStartY: 0
                        property real consoleResizeStartHeight: 0
                        readonly property real consoleStoredHeight: root.printer != null && root.printer.consoleHeight > 0 ? root.printer.consoleHeight : consoleDefaultHeight
                        readonly property real consoleSettledHeight: Math.max(consoleMinHeight, Math.min(consoleMaxHeight, consoleStoredHeight))
                        readonly property real consoleCurrentHeight: consoleDragHeight > 0 ? consoleDragHeight : consoleSettledHeight
                        // Below the pane's own minimum there is no room
                        // for the body at all: the well cannot hold the
                        // prompt, the input and its buttons any more, and
                        // a squeezed column pushed them past the black
                        // border (the live report). The body
                        // FADES out just below the minimum and is gone for
                        // the rest of the travel to the collapse position,
                        // so the closing pane reads as an empty shell
                        // rather than a crushed one — the fade follows the
                        // DRAG, so dragging back up brings the console
                        // straight back. A settled height never lands in
                        // the band (the clamp floor is the minimum), so it
                        // is only ever seen mid-drag.
                        readonly property real consoleBodyFadeSpan: 24 * screenScaleFactor
                        readonly property real consoleBodyOpacity: Math.max(0, Math.min(1, (height - (consoleMinHeight - consoleBodyFadeSpan)) / consoleBodyFadeSpan))
                        Layout.preferredHeight: consoleExpanded ? consoleCurrentHeight : consoleCollapsedHeight
                        Layout.maximumHeight: consoleExpanded ? consoleCurrentHeight : consoleCollapsedHeight
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        color: UM.Theme.getColor("main_background")
                        radius: UM.Theme.getSize("default_radius").width
                        // The drag passes through heights SHORTER than the
                        // inner column's minimum (on the way down to the
                        // collapse position), so the card clips: its
                        // content must never paint over the webcam card
                        // above it.
                        clip: true

                        // One owner for the resize. The pointer is read
                        // in the PANE's frame, never the handle's: the
                        // handle rides the edge it is moving, so a local
                        // measurement feeds the new height back into its
                        // own delta and the console runs away under the
                        // pointer. The height the drag reports is
                        // CONTINUOUS — the collapse position is its floor,
                        // not a jump — so dragging down shrinks the pane
                        // to the strip and collapses it, and dragging back
                        // up expands it at the same spot (the edge never
                        // detaches from the pointer).
                        function consoleSetExpanded(expanded) {
                            if (root.printer == null) {
                                return;
                            }
                            // Only a real crossing writes: a drag that
                            // wiggles across the threshold must not
                            // rewrite the state file per event.
                            if ((root.printer.sectionExpandedMap["console"] !== false) === expanded) {
                                return;
                            }
                            root.printer.setSectionExpanded("console", expanded);
                            root.printer.setConsoleExpanded(expanded);
                        }
                        function consoleResizeTo(paneY) {
                            var height = consoleResizeStartHeight + (consoleResizeStartY - paneY);
                            // Up is taller. The collapse position is the
                            // drag floor, so the pane can never be pulled
                            // below the strip it collapses into.
                            height = Math.max(consoleCollapsedHeight, Math.min(consoleMaxHeight, height));
                            consoleDragHeight = height;
                            consoleSetExpanded(height > consoleCollapsedHeight + 0.5);
                        }
                        function consoleResizeCommit() {
                            if (consoleDragHeight <= 0) {
                                return;  // a click on the handle, not a drag
                            }
                            var height = consoleDragHeight;
                            consoleDragHeight = 0;
                            if (height < consoleMinHeight) {
                                // Released on the way down, above the
                                // collapse position: a crushed console is
                                // not a size to keep. Collapse it, and
                                // leave the model holding the user's last
                                // usable height for the next expand.
                                consoleSetExpanded(false);
                                return;
                            }
                            consoleSetExpanded(true);
                            if (root.printer != null) {
                                // Whole pixels: the model's property is an
                                // int, the drag delta a real.
                                root.printer.setConsoleHeight(Math.round(height));
                            }
                        }

                        // While collapsed, a click ANYWHERE on the
                        // strip expands the pane, like the other
                        // panes; the header button sits above this
                        // area and keeps its own clicks.
                        MouseArea {
                            visible: root.printer != null && (root.printer.sectionExpandedMap["console"] === false || consolePanel.tooNarrow)
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                // Auto-collapsed-by-width is not a
                                // clickable expand: only a wider window
                                // restores the panel.
                                if (root.printer != null && !consolePanel.tooNarrow) {
                                    root.printer.setSectionExpanded("console", true);
                                    root.printer.setConsoleExpanded(true);
                                }
                            }
                        }

                        ColumnLayout {
                            id: consoleColumn
                            anchors.fill: parent
                            spacing: 0

                            // The resize handle: the card's TOP edge. It
                            // holds its own strip in the layout — never an
                            // overlay across the header row — so the
                            // collapse toggle keeps every pixel of its hit
                            // area. It rides BOTH states: pulling the strip
                            // down to the collapse position collapses the
                            // pane, and dragging back out of it expands the
                            // pane (the request).
                            Item {
                                id: consoleResizeHandle
                                objectName: "consoleResizeHandle"
                                // Hidden while the auto-collapse width
                                // holds: a grab bar for an expansion
                                // that cannot happen would be a lie
                                // (a live request).
                                visible: !consolePanel.tooNarrow
                                Layout.fillWidth: true
                                Layout.preferredHeight: consolePanel.consoleHandleHeight

                                MouseArea {
                                    id: consoleResizeArea
                                    objectName: "consoleResizeArea"
                                    anchors.fill: parent
                                    cursorShape: Qt.SizeVerCursor
                                    hoverEnabled: true
                                    onPressed: {
                                        consolePanel.consoleResizeStartY = mapToItem(cameraArea, mouse.x, mouse.y).y;
                                        consolePanel.consoleResizeStartHeight = consolePanel.height;
                                        // A reader at the tail stays at the
                                        // tail through the resize; one
                                        // scrolled up is never yanked (the
                                        // golden rule, drag variant).
                                        if (consoleFlick.contentY + consoleFlick.height >= consoleFlick.contentHeight - 2) {
                                            consoleFlick.restoreScrollPending = true;
                                        }
                                    }
                                    onPositionChanged: {
                                        if (pressed) {
                                            consolePanel.consoleResizeTo(mapToItem(cameraArea, mouse.x, mouse.y).y);
                                        }
                                    }
                                    onReleased: consolePanel.consoleResizeCommit()
                                    // A stolen grab (the window losing the
                                    // pointer, an ancestor's drag) must
                                    // still land the height the user
                                    // dragged to.
                                    onCanceled: consolePanel.consoleResizeCommit()
                                }

                                // The grip: the strip's affordance — the
                                // resize cursor alone is invisible until
                                // the pointer is already on it.
                                Rectangle {
                                    anchors.centerIn: parent
                                    // Wide and thick enough to read as a
                                    // grab bar at a glance (the
                                    // live ruling — the first grip was
                                    // too subtle to find).
                                    width: 72 * screenScaleFactor
                                    height: 5 * screenScaleFactor
                                    radius: height / 2
                                    color: consoleResizeArea.containsMouse ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
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
                                    text: "Drag to resize the console."
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: UM.Theme.getSize("thin_margin").height
                                // The bottom breathing room keeps the
                                // collapsed card from hugging the title
                                // on both edges (the report).
                                Layout.bottomMargin: UM.Theme.getSize("thin_margin").height
                                Layout.leftMargin: UM.Theme.getSize("thin_margin").width
                                Layout.rightMargin: UM.Theme.getSize("thin_margin").width
                                spacing: UM.Theme.getSize("thin_margin").width

                                // The collapse toggle hugs the top
                                // LEFT like the other panes' buttons;
                                // the chevron points the way the
                                // pane will move (up = collapse).
                                // The toggle hugs the top LEFT and
                                // matches the OTHER panes' collapse
                                // buttons (‹/›), not a theme chevron
                                // (the ruling).
                                Cura.SecondaryButton {
                                    id: consoleCollapseButton
                                    Layout.alignment: Qt.AlignVCenter
                                    fixedWidthMode: true
                                    // Square at the OLD button width:
                                    // the theme adds its padding around
                                    // the 32px content, so the height
                                    // tracks the rendered width (the
                                    // ruling).
                                    width: 28 * screenScaleFactor
                                    iconSize: 12 * screenScaleFactor
                                    height: width
                                    implicitHeight: width

                                    // The same button style as the other
                                    // panes, with the theme's UP/DOWN
                                    // chevrons inside it (the pane
                                    // collapses upward); the button
                                    // centres the icon itself.
                                    // The accordion convention: DOWN when expanded
                                    // (the ruling — the first direction read
                                    // inverted).
                                    iconSource: root.printer != null && root.printer.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow ? UM.Theme.getIcon("ChevronSingleDown") : UM.Theme.getIcon("ChevronSingleUp")
                                    onClicked: {
                                        if (root.printer != null && !consolePanel.tooNarrow) {
                                            // The poll follows the pane:
                                            // collapsing stops the
                                            // gcode-store fetch (the
                                            // expanded-only
                                            // ruling), expanding starts
                                            // it with a backfill seed.
                                            var expanding = root.printer.sectionExpandedMap["console"] === false;
                                            root.printer.setSectionExpanded("console", expanding);
                                            root.printer.setConsoleExpanded(expanding);
                                        }
                                    }
                                    UM.ToolTip {
                                        visible: parent.hovered
                                        targetPoint: Qt.point(parent.width / 2, 0)
                                        x: 0
                                        y: parent.height + UM.Theme.getSize("default_margin").height
                                        width: UM.Theme.getSize("tooltip").width
                                        text: consolePanel.tooNarrow ? "The window is too narrow — widen it to expand the console." : (root.printer != null && root.printer.sectionExpandedMap["console"] !== false ? "Collapse the console." : "Expand the console.")
                                    }
                                }

                                UM.Label {
                                    Layout.fillWidth: true
                                    text: "Console"
                                    font: UM.Theme.getFont("medium_bold")
                                    // Expanded, a proper title reads in
                                    // the normal text colour; collapsed
                                    // it greys like the other panes'
                                    // collapsed strips.
                                    color: root.printer != null && root.printer.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                                }
                                // The error bell (the live
                                // request): while the console is
                                // collapsed, a NEW error line rings a
                                // red bell next to the header until
                                // the console expands.
                                UM.ColorImage {
                                    visible: root.printer != null && root.printer.consoleErrorBell
                                    Layout.preferredWidth: 14 * screenScaleFactor
                                    Layout.preferredHeight: 14 * screenScaleFactor
                                    source: Qt.resolvedUrl("Bell.svg")
                                    color: MoonrakerTheme.consoleBell
                                }
                            }

                            ColumnLayout {
                                id: consoleSection
                                visible: root.printer != null && root.printer.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow
                                // The body fades as the pane closes (see
                                // consoleBodyOpacity): a squeezed body
                                // spilled past the card, and the fade
                                // keeps the closing pane clean.
                                opacity: consolePanel.consoleBodyOpacity
                                // The cheat: if the app started
                                // with the console collapsed, the FIRST
                                // expand scrolls to the tail once (the
                                // restore ran collapsed and its metrics
                                // were stale). Later collapse/expands
                                // never scroll.
                                property bool consoleStartedCollapsed: false
                                property bool consoleFirstExpandHandled: false
                                Component.onCompleted: {
                                    consoleStartedCollapsed = root.printer != null && root.printer.sectionExpandedMap["console"] === false;
                                }
                                onVisibleChanged: {
                                    if (visible && consoleStartedCollapsed && !consoleFirstExpandHandled) {
                                        consoleFirstExpandHandled = true;
                                        consoleFlick.restoreScrollPending = true;
                                    }
                                }
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.margins: UM.Theme.getSize("default_margin").width
                                Layout.topMargin: UM.Theme.getSize("narrow_margin").height
                                Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                                spacing: UM.Theme.getSize("narrow_margin").height
                                // The section stays ENABLED while
                                // disconnected: scrolling, selecting and
                                // copying the restored history must keep
                                // working (the ruling). Only
                                // the INPUT surface disables.
                                enabled: root.printer != null
                                property int consoleRecallIndex: -1
                                property string consoleDraft: ""
                                // Pick an actually-installed monospace face at
                                // runtime: the generic "monospace" and comma
                                // lists do not resolve on every machine.
                                function monoFamily() {
                                    try {
                                        var names = Qt.fontFamilies();
                                        var known = ["consolas", "menlo", "courier", "mono"];
                                        for (var i = 0; i < names.length; ++i) {
                                            var lower = String(names[i]).toLowerCase();
                                            for (var k = 0; k < known.length; ++k) {
                                                if (lower.indexOf(known[k]) >= 0) {
                                                    return names[i];
                                                }
                                            }
                                        }
                                    } catch (e) {}
                                    return "monospace";
                                }
                                // The transcript arrives oldest-first; the pane
                                // is ONE rich TextEdit so text selection spans
                                // lines (per-line delegates could not). The
                                // sync appends only the NEW lines, inserting
                                // at the end with the reader's selection
                                // saved and restored around it — new output
                                // never disturbs a selection or yanks the
                                // scroll position.
                                property int consoleRenderedLines: 0
                                // Ring-rotation lines the pane already saw
                                // dropped out of the transcript's head.
                                property int consoleDroppedSeen: 0
                                property int consoleRevisionsSeen: 0

                                function consoleLineHtml(entry) {
                                    // Terminal voice, three speakers
                                    // (the rulings): commands
                                    // carry ">", Moonraker's responses
                                    // carry "<", and the plugin's own
                                    // notes carry "#" in amber — a hue
                                    // Klipper's red/green/grey never
                                    // uses, so anything the plugin adds
                                    // is obviously its own. Responses
                                    // render BRIGHT (red/green); saved
                                    // commands keep their TEXT light
                                    // grey and put the verdict on the
                                    // ">" only (the live
                                    // ruling: the whole line turning
                                    // green was too much) — green
                                    // matches the input row's prompt,
                                    // red is a failure, quiet grey is
                                    // no verdict. While unsaved the
                                    // line stays blue (the
                                    // ruling). The muted hues are
                                    // contrast-checked (≥4.5:1 on the
                                    // dark well) — the old muted
                                    // family sat near 2:1.
                                    var hue = MoonrakerTheme.consoleText;
                                    var promptHue = "";
                                    if (entry.kind === "response") {
                                        hue = entry.error ? MoonrakerTheme.errorRed : (entry.success ? MoonrakerTheme.consoleSuccess : MoonrakerTheme.consoleMuted);
                                    } else if (entry.kind === "note") {
                                        hue = MoonrakerTheme.consoleWarn;
                                    } else if (entry.saved === false) {
                                        // Sent but not yet flushed to
                                        // disk: blue until the save
                                        // lands (the ruling —
                                        // the API verdict flips too
                                        // fast to read live).
                                        hue = MoonrakerTheme.consoleInfo;
                                    } else {
                                        promptHue = entry.error ? MoonrakerTheme.consolePromptError : (entry.success ? MoonrakerTheme.successGreen : MoonrakerTheme.consoleMuted);
                                    }
                                    if (entry.restored) {
                                        // Restored lines grey uniformly:
                                        // the verdict colours are LIVE
                                        // signals, and a restored command
                                        // showing its old green read as
                                        // current state (the
                                        // report). Restored responses
                                        // keep their muted hues.
                                        hue = entry.kind === "command" ? MoonrakerTheme.consoleCommand : (entry.error ? MoonrakerTheme.consoleHistoryError : (entry.success ? MoonrakerTheme.consoleHistorySuccess : MoonrakerTheme.consoleCommand));
                                    }
                                    var escaped = String(entry.text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                                    if (entry.kind === "note") {
                                        return "<span style=\"color:" + hue + ";\"># " + escaped + "</span>";
                                    }
                                    if (entry.kind === "response") {
                                        return "<span style=\"color:" + hue + ";\">&lt; " + escaped + "</span>";
                                    }
                                    if (promptHue) {
                                        return "<span style=\"color:" + promptHue + ";\">&gt; </span><span style=\"color:" + hue + ";\">" + escaped + "</span>";
                                    }
                                    return "<span style=\"color:" + hue + ";\">&gt; " + escaped + "</span>";
                                }

                                function consoleSyncLines() {
                                    var lines = root.printer != null ? root.printer.consoleLines : [];
                                    var dropped = root.printer != null ? root.printer.consoleDropped : 0;
                                    var revisions = root.printer != null ? root.printer.consoleRevisions : 0;
                                    // Captured BEFORE any rebuild below: a
                                    // rebuild collapses the content height,
                                    // which would otherwise read as "at the
                                    // end" and yank a scrolled-up reader.
                                    var wasAtEnd = consoleFlick.contentY + consoleFlick.height >= consoleFlick.contentHeight - 2;
                                    // A recolour rebuild (a verdict or a
                                    // saved-flush) must keep the reader's
                                    // EXACT place; only a first load may
                                    // follow the tail.
                                    var fromEmpty = consoleRenderedLines === 0;
                                    var rebuildContentY = consoleFlick.contentY;
                                    // The selection is captured BEFORE the
                                    // rebuild wipe: capturing it after left
                                    // the wiped document empty, so the
                                    // restore below was a silent no-op and
                                    // a mid-copy selection died on every
                                    // recolour (the UX panel).
                                    var selStart = consoleText.selectionStart;
                                    var selEnd = consoleText.selectionEnd;
                                    var prevTextHeight = consoleText.height;
                                    if (lines.length < consoleRenderedLines || dropped > consoleDroppedSeen || revisions > consoleRevisionsSeen) {
                                        // A Clear, a printer switch, or the
                                        // session ring rotating at its cap
                                        // (its length stops growing there, so
                                        // the count alone would stall this
                                        // sync forever): rebuild the pane
                                        // from the transcript.
                                        consoleText.text = "";
                                        consoleRenderedLines = 0;
                                        consoleDroppedSeen = dropped;
                                        consoleRevisionsSeen = revisions;
                                    }
                                    if (lines.length === consoleRenderedLines) {
                                        return;
                                    }
                                    var html = "";
                                    for (var i = consoleRenderedLines; i < lines.length; ++i) {
                                        if (i > consoleRenderedLines) {
                                            html += "<br>";
                                        }
                                        html += consoleLineHtml(lines[i]);
                                    }
                                    if (consoleRenderedLines === 0) {
                                        // The rebuild sets the document
                                        // DIRECTLY: clearing the text and
                                        // insert()ing afterwards produced
                                        // an EMPTY pane in the real Cura
                                        // engine (total=53 rendered=53
                                        // text='' — the smoking
                                        // gun), while the append path's
                                        // insert works there. The
                                        // restored history then opens at
                                        // the NEWEST line (the
                                        // ruling).
                                        consoleText.text = html;
                                        if (fromEmpty) {
                                            // The FIRST load follows the
                                            // tail (waits for the flick's
                                            // metrics to settle, or for the
                                            // first expand of a start-
                                            // collapsed console — the
                                            // cheat). Recolour
                                            // rebuilds never follow.
                                            consoleFlick.restoreScrollPending = true;
                                        } else {
                                            // The reader keeps their exact
                                            // place across the recolour
                                            // (the golden rule). A ring
                                            // rotation ALSO removed lines
                                            // above the viewport: the
                                            // document shrank by exactly
                                            // their height, so the position
                                            // shifts up by that amount to
                                            // keep the visible text
                                            // stationary (the engineering
                                            // panel's rotation yank).
                                            var rotationDrop = Math.max(0, prevTextHeight - consoleText.height);
                                            consoleFlick.contentY = Math.max(0, Math.min(rebuildContentY - rotationDrop, consoleFlick.contentHeight - consoleFlick.height));
                                        }
                                    } else {
                                        // A chunk appended into a pane
                                        // that already shows lines must
                                        // start on a fresh line: without
                                        // the leading break it glued onto
                                        // the last rendered line.
                                        html = "<br>" + html;
                                        consoleText.cursorPosition = consoleText.length;
                                        consoleText.insert(consoleText.length, html);
                                    }
                                    consoleRenderedLines = lines.length;
                                    if (selStart !== selEnd && selStart >= 0) {
                                        consoleText.select(selStart, selEnd);
                                    }
                                    // Follow the tail ONLY while the
                                    // reader was already at it AND the
                                    // pane had content before this sync:
                                    // the FIRST sync renders an empty
                                    // pane that reads as "at the end",
                                    // and auto-scrolling then buried
                                    // the restored commands at the head
                                    // (the report).
                                    if (wasAtEnd && consoleRenderedLines > 0) {
                                        consoleFlick.stickToEnd = true;
                                        consoleFlick.contentY = consoleFlick.contentHeight - consoleFlick.height;
                                    }
                                }

                                function consoleSend() {
                                    if (root.printer != null) {
                                        // A refused send (queue full, lane
                                        // busy, Moonraker down) must keep
                                        // the typed line: losing an unsent
                                        // G-code draft on refusal is data
                                        // loss, and the status line already
                                        // explains the refusal.
                                        if (root.printer.sendConsoleCommand(consoleInput.text)) {
                                            consoleInput.text = "";
                                            consoleDraft = "";
                                            consoleRecallIndex = -1;
                                            // A send is the reader signalling
                                            // they want to follow the tail
                                            // again: return the view to the
                                            // prompt even from a scrolled-up
                                            // position (the ruling).
                                            consoleFlick.stickToEnd = true;
                                            consoleFlick.contentY = consoleFlick.contentHeight - consoleFlick.height;
                                        }
                                        consoleInput.forceActiveFocus();
                                    }
                                }
                                function consoleRecall(step) {
                                    var history = root.printer != null ? root.printer.consoleHistory : [];
                                    if (history.length === 0) {
                                        return;
                                    }
                                    if (consoleRecallIndex < 0) {
                                        consoleDraft = consoleInput.text;
                                    }
                                    consoleRecallIndex = Math.max(-1, Math.min(history.length - 1, consoleRecallIndex + step));
                                    consoleInput.text = consoleRecallIndex < 0 ? consoleDraft : history[history.length - 1 - consoleRecallIndex];
                                }

                                // A shell-terminal-styled console: dark,
                                // fixed-width, newest line pinned to the
                                // bottom — the list slides to the end as
                                // each line lands, and the input row lives
                                // INSIDE the dark well with the prompt, so
                                // the green ">" keeps its contrast on both
                                // themes (panel UX P3).
                                Cura.RoundedRectangle {
                                    id: consoleWell
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 190 * screenScaleFactor
                                    // The terminal is the pane's
                                    // filler: the extra height goes
                                    // to the feed, not to dead space
                                    // under it.
                                    Layout.fillHeight: true
                                    // The disconnected state reads in the
                                    // well itself: grey instead of the
                                    // terminal black (the live
                                    // ruling).
                                    color: root.printer != null && root.printer.monitorConnected ? MoonrakerTheme.consoleBackground : MoonrakerTheme.consoleBackgroundOffline
                                    border.color: UM.Theme.getColor("lining")
                                    border.width: UM.Theme.getSize("default_lining").width
                                    radius: UM.Theme.getSize("default_radius").width
                                    // The well is a real CONTAINER, not a
                                    // backdrop: the prompt, the input and
                                    // its buttons live inside it, and when
                                    // the pane is dragged shorter than
                                    // they need they are cut at the well's
                                    // own edge instead of floating
                                    // outside the black border (a
                                    // live report).
                                    clip: true

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: UM.Theme.getSize("narrow_margin").width
                                        spacing: UM.Theme.getSize("thin_margin").height

                                        Item {
                                            id: consoleOutputHost
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            // 5.11's TextArea metrics inflate the flick's content
                                            // implicit height, which FLOORS this fillHeight slot
                                            // and pushes the input row out of the well — the
                                            // transcript then grabs the Send/Clear presses (the
                                            // sweep's d1-07/d3-01 find). The explicit floor
                                            // keeps the slot shrinkable on every version.
                                            Layout.minimumHeight: 0

                                            Flickable {
                                                id: consoleFlick
                                                anchors.fill: parent
                                                clip: true
                                                // The restore's tail scroll
                                                // fires when the metrics
                                                // SETTLE: the content height
                                                // updates over several frames
                                                // after setting the text, and
                                                // one-shot scrolls measured
                                                // stale values (the
                                                // reports).
                                                property bool restoreScrollPending: false
                                                // Stick-to-end (the
                                                // live report: when the pane
                                                // is crushed and the text
                                                // wraps, the sync pins to a
                                                // height that has not settled,
                                                // the viewport lands short of
                                                // the tail, and the next poll
                                                // snaps back). While the
                                                // reader was at the end, every
                                                // metric change re-pins — the
                                                // wrap's own settle can never
                                                // leave the viewport stranded
                                                // above the tail.
                                                property bool stickToEnd: false
                                                // The scroll follows every
                                                // metric change until the
                                                // layout goes quiet — the
                                                // text height settles over
                                                // several frames and a
                                                // one-shot scroll kept
                                                // landing off by the command
                                                // bar (the reports).
                                                Timer {
                                                    id: restoreQuietTimer
                                                    interval: 120
                                                    repeat: false
                                                    onTriggered: consoleFlick.restoreScrollPending = false
                                                }
                                                // GOLDEN RULE: the reader's own
                                                // movement cancels the restore's
                                                // follow — a user scrolling up
                                                // mid-history is never yanked
                                                // (the ruling).
                                                onMovementStarted: {
                                                    restoreScrollPending = false;
                                                    stickToEnd = false;
                                                }
                                                onContentHeightChanged: {
                                                    if (restoreScrollPending || stickToEnd) {
                                                        contentY = contentHeight - height;
                                                        restoreQuietTimer.restart();
                                                    }
                                                }
                                                onHeightChanged: {
                                                    if (restoreScrollPending || stickToEnd) {
                                                        contentY = contentHeight - height;
                                                        restoreQuietTimer.restart();
                                                    }
                                                }
                                                contentWidth: consoleText.width
                                                contentHeight: Math.max(consoleText.height, consoleFlick.height)
                                                ScrollBar.vertical: UM.ScrollBar {
                                                    id: consoleScrollbar
                                                    // GOLDEN RULE, scrollbar
                                                    // variant: a handle drag
                                                    // drives contentY directly
                                                    // and never fires
                                                    // onMovementStarted — the
                                                    // reader's drag cancels the
                                                    // pending restore itself
                                                    // (the UX panel).
                                                    onPressedChanged: {
                                                        if (pressed) {
                                                            restoreScrollPending = false;
                                                            consoleFlick.stickToEnd = false;
                                                        }
                                                    }
                                                }
                                                Column {
                                                    // The vertical scrollbar
                                                    // overlays the well's right
                                                    // edge: the text must stop
                                                    // short of it or wrapped
                                                    // lines run underneath (a
                                                    // live report). A
                                                    // CONSTANT reserve (the
                                                    // status gutter's
                                                    // precedent): the
                                                    // scrollbar's own width
                                                    // fed the column's width →
                                                    // the text's wrap → the
                                                    // content height → the
                                                    // scrollbar's visibility —
                                                    // a binding loop (the
                                                    // capture's report).
                                                    width: consoleFlick.width - 14 * screenScaleFactor
                                                    height: consoleFlick.contentHeight
                                                    // The spacer pins the sparse
                                                    // transcript to the shell's
                                                    // bottom edge; once the text
                                                    // fills the viewport it scrolls
                                                    // exactly like a terminal.
                                                    Item {
                                                        width: 1
                                                        height: Math.max(0, consoleFlick.height - consoleText.height)
                                                    }
                                                    TextEdit {
                                                        id: consoleText
                                                        // Inert; the harness's rendered-follows
                                                        // scenarios read this pane's text.
                                                        objectName: "moonrakerConsoleOutput"
                                                        width: parent.width
                                                        readOnly: true
                                                        selectByMouse: true
                                                        selectByKeyboard: true
                                                        textFormat: TextEdit.RichText
                                                        // Long Klipper lines wrap
                                                        // instead of overflowing the
                                                        // well; wrapping breaks on
                                                        // word boundaries (a
                                                        // live report).
                                                        wrapMode: TextEdit.Wrap
                                                        font.family: consoleSection.monoFamily()
                                                        color: MoonrakerTheme.consoleText
                                                        // No blinking caret: a read-only
                                                        // terminal pane has no cursor, and
                                                        // the caret's phase made the
                                                        // captures nondeterministic.
                                                        cursorVisible: false
                                                        // No Esc handling here on
                                                        // purpose: this read-only
                                                        // pane declines the key
                                                        // (as any text item
                                                        // does), and the host
                                                        // page's ladder answers
                                                        // it — see
                                                        // answerEscape() in
                                                        // MoonrakerMonitorDashboard.
                                                    }
                                                }
                                            }

                                            UM.Label {
                                                // The empty-state hint is an
                                                // OVERLAY, not a layout child: a
                                                // layout slot stole a line from
                                                // the output area and the feed
                                                // stopped short of the input row
                                                // (the live report).
                                                anchors.left: parent.left
                                                anchors.right: parent.right
                                                anchors.bottom: parent.bottom
                                                anchors.bottomMargin: 8 * screenScaleFactor
                                                opacity: root.printer == null || root.printer.consoleLines.length === 0 ? 1 : 0
                                                text: "No commands yet — lines you send appear here."
                                                font.family: consoleSection.monoFamily()
                                                color: MoonrakerTheme.consoleTextMuted
                                                elide: Text.ElideRight
                                            }
                                        }

                                        Connections {
                                            target: root.printer
                                            function onConsoleChanged() {
                                                consoleSection.consoleSyncLines();
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: UM.Theme.getSize("thin_margin").width
                                            UM.Label {
                                                text: ">"
                                                font: UM.Theme.getFont("medium_bold")
                                                color: MoonrakerTheme.successGreen
                                            }
                                            Cura.TextField {
                                                id: consoleInput
                                                objectName: "moonrakerConsoleInput"
                                                Layout.fillWidth: true
                                                // The row's only shrink absorber: on 5.11's
                                                // theme metrics the field's implicit minimum
                                                // outran the pane, pushing Send and Clear out
                                                // of their cells — the presses landed on the
                                                // field's wider region.
                                                Layout.minimumWidth: 0
                                                // Nothing of the field paints or takes input
                                                // outside its own cell.
                                                clip: true
                                                placeholderText: "G-code command…"
                                                // The placeholder must clear the contrast
                                                // census on the light pane: the theme's
                                                // muted tone, never UM's grey-on-grey.
                                                placeholderTextColor: MoonrakerTheme.consoleTextMuted
                                                font.family: consoleSection.monoFamily()
                                                enabled: root.printer != null && root.printer.monitorConnected
                                                Keys.onReturnPressed: consoleSection.consoleSend()
                                                Keys.onUpPressed: consoleSection.consoleRecall(1)
                                                Keys.onDownPressed: consoleSection.consoleRecall(-1)
                                            }
                                            Cura.SecondaryButton {
                                                text: "Send"
                                                objectName: "moonrakerConsoleSend"
                                                // The buttons hold their cells; the field
                                                // gives up the width instead.
                                                Layout.fillWidth: false
                                                enabled: root.printer != null && root.printer.monitorConnected
                                                onClicked: consoleSection.consoleSend()
                                            }
                                            Cura.SecondaryButton {
                                                text: "Clear"
                                                objectName: "moonrakerConsoleClear"
                                                Layout.fillWidth: false
                                                enabled: root.printer != null && root.printer.monitorConnected && root.printer.consoleLines.length > 0
                                                onClicked: root.printer.clearConsoleHistory()
                                            }
                                        }
                                    }
                                }
                            }
                        }
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

                    ColumnLayout {
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
                        spacing: 0
                        JobSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["job"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }

                        TempsSection {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.temperatureItems.length > 0 && root.printer.sectionHiddenMap["temps"] !== true
                            printerModel: root.printer
                        }

                        FansInfoSection {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.fanItems.length > 0 && root.printer.sectionHiddenMap["fansinfo"] !== true
                            printerModel: root.printer
                        }
                        FilamentSection {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.filamentSensorItems.length > 0 && root.printer.sectionHiddenMap["filament"] !== true
                            printerModel: root.printer
                        }
                        SystemInfoSection {
                            visible: root.printer == null || root.printer.sectionHiddenMap["systeminfo"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        McusSection {
                            Layout.fillWidth: true
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
                            source: Qt.resolvedUrl("Hourglass.svg")
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
                            source: Qt.resolvedUrl("Clock.svg")
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
                            source: Qt.resolvedUrl("Layer.svg")
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
                            source: Qt.resolvedUrl("Progress.svg")
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
                            source: Qt.resolvedUrl("Flow.svg")
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

        MonitorPopOver {
            id: chartPanel
            visible: root.openPopOver === "chart" && root.printer != null
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            // Grows with the legend: three rows of sensors fit the base
            // height; each further row adds its line height, capped at
            // the monitor area.
            height: Math.min(590 * screenScaleFactor + Math.max(0, Math.ceil((root.printer != null ? root.printer.temperatureChartLegend.series.length : 0) / 2) - 3) * 30 * screenScaleFactor, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            // Reset the tooltip proxies on EVERY close path — the
            // outside-click layer, the opener's second click and
            // auto-close all flip `visible` — so a reopen never
            // flashes the previous hover's values at a stale position.
            onVisibleChanged: {
                if (!visible) {
                    hoverClockProxy = "";
                    hoverValuesProxy = [];
                    hoverCursor = Qt.point(-1, -1);
                }
            }

            // Proxied hover state for the floating tooltip, which lives
            // outside the clipped card so it may overflow any boundary.
            property string hoverClockProxy: ""
            property var hoverValuesProxy: []
            property point hoverCursor: Qt.point(-1, -1)

            // The content loads only while the card is open: a
            // zero-sized Canvas inside a closed card sent the engine
            // into an endless relayout on pane collapses, and hidden
            // bindings never evaluate.
            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                active: root.openPopOver === "chart"
                sourceComponent: chartContent
            }
        }

        Component {
            id: chartContent
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: UM.Theme.getSize("thin_margin").height

                TemperatureChart {
                    id: chartPanelChart
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 180 * screenScaleFactor
                    Connections {
                        target: chartPanelChart
                        function onHoverClockChanged() {
                            chartPanel.hoverClockProxy = chartPanelChart.hoverClock;
                        }
                        function onHoverValuesChanged() {
                            chartPanel.hoverValuesProxy = chartPanelChart.hoverValues;
                        }
                        function onHoverCursorChanged() {
                            var p = chartPanelChart.mapToItem(root, chartPanelChart.hoverCursor.x, chartPanelChart.hoverCursor.y);
                            chartPanel.hoverCursor = p;
                        }
                    }
                    // Hidden when the pop-over is closed: the closed
                    // card's zero-sized canvas must never paint. The
                    // full payload is the model's dormant object until
                    // the pop-over opens, so this gate also reads the
                    // legend (which never goes dormant) for the
                    // has-data case.
                    visible: root.openPopOver === "chart" && root.printer != null && root.printer.temperatureChartLegend.series.length > 0
                    chart: root.printer != null ? root.printer.temperatureChartFull : ({
                            "series": [],
                            "showTargets": true,
                            "showPower": true
                        })
                }

                UM.Label {
                    Layout.fillWidth: true
                    visible: root.printer != null && root.printer.temperatureChartLegend.series.length === 0
                    text: "No temperature data yet — the chart fills once the printer reports temperatures."
                    color: UM.Theme.getColor("text_inactive")
                    wrapMode: Text.WordWrap
                }

                UM.Label {
                    Layout.fillWidth: true
                    visible: root.allChartSensorsHidden
                    text: "All sensors hidden — use the legend to show them again."
                    color: UM.Theme.getColor("text_inactive")
                    wrapMode: Text.WordWrap
                }

                // Legend: a two-column grid of visibility toggles with
                // live values. It binds to the legend property, which
                // only notifies on real changes, and `toggled` fires on
                // user interaction only — so the delegates are never
                // rebuilt at the 1 Hz sample cadence and re-bound
                // checkboxes cannot rewrite the state file.
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: UM.Theme.getSize("default_margin").width
                    rowSpacing: UM.Theme.getSize("narrow_margin").height
                    Repeater {
                        model: root.printer != null ? root.printer.temperatureChartLegend.series : []
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("narrow_margin").width
                            UM.CheckBox {
                                checked: modelData.visible
                                // The label lives in the neighbouring
                                // cell — name the control for screen
                                // readers (panel UX P3).
                                Accessible.name: "Show " + modelData.label
                                onToggled: root.printer.setTemperatureSensorVisible(modelData.name, checked)
                            }
                            Rectangle {
                                width: 10 * screenScaleFactor
                                height: 10 * screenScaleFactor
                                radius: 5 * screenScaleFactor
                                color: modelData.color
                                border.color: root.selectedChartSensor === modelData.name ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                                border.width: root.selectedChartSensor === modelData.name ? 2 : 1
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.selectedChartSensor = root.selectedChartSensor === modelData.name ? "" : modelData.name
                                }
                            }
                            UM.Label {
                                Layout.fillWidth: true
                                text: modelData.label
                                elide: Text.ElideRight
                            }
                            UM.Label {
                                text: {
                                    // The live value rides the latest
                                    // projection — one scalar per
                                    // sensor, never a search through
                                    // the chart payload.
                                    var latest = root.printer != null ? root.printer.temperatureChartLatest : null;
                                    if (latest == null || latest[modelData.name] === undefined) {
                                        return "—";
                                    }
                                    return Number(latest[modelData.name]).toFixed(1) + "°C";
                                }
                                color: UM.Theme.getColor("text_inactive")
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: root.selectedChartSensor !== ""
                    spacing: UM.Theme.getSize("thin_margin").width
                    UM.Label {
                        text: root.selectedChartSensorLabel + (root.printer != null && root.printer.britishSpelling ? " colour:" : " color:")
                        color: UM.Theme.getColor("text_inactive")
                    }
                    Repeater {
                        model: root.printer != null ? root.printer.temperatureChartLegend.palette : []
                        Rectangle {
                            width: 16 * screenScaleFactor
                            height: 16 * screenScaleFactor
                            radius: 8 * screenScaleFactor
                            color: modelData
                            border.color: root.selectedChartColor === modelData ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                            border.width: root.selectedChartColor === modelData ? 2 : 1
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.printer.setTemperatureSensorColor(root.selectedChartSensor, modelData)
                            }
                        }
                    }
                    Cura.SecondaryButton {
                        text: "Custom…"
                        onClicked: root.openChartColorDialog()
                        UM.ToolTip {
                            visible: parent.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: "Pick any " + (root.printer != null && root.printer.britishSpelling ? "colour" : "color") + " for " + root.selectedChartSensorLabel + "."
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("default_margin").width
                    UM.CheckBox {
                        checked: root.printer != null ? root.printer.temperatureChartLegend.showTargets : true
                        onToggled: root.printer.setShowTemperatureTargets(checked)
                    }
                    UM.Label {
                        text: "Targets"
                    }
                    UM.CheckBox {
                        checked: root.printer != null ? root.printer.temperatureChartLegend.showPower : true
                        onToggled: root.printer.setShowTemperaturePower(checked)
                    }
                    UM.Label {
                        text: "Heater power"
                    }
                }
            }
        }

        MonitorPopOver {
            id: meshPanel
            visible: root.openPopOver === "mesh" && root.printer != null && root.printer.bedMeshAvailable
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            height: Math.min((520 * screenScaleFactor) + UM.Theme.getSize("default_margin").height, parent.height - 2 * UM.Theme.getSize("default_margin").height)
            contentWidth: 390 * screenScaleFactor
            title: "Bed mesh — " + (root.printer != null ? root.printer.bedMeshProfile : "")

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                active: root.openPopOver === "mesh" && root.printer != null && root.printer.bedMeshAvailable
                sourceComponent: meshContent
            }
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
            contentWidth: 585 * screenScaleFactor
            title: "Print Follower"

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                active: root.openPopOver === "plateprogress" && root.printer != null
                sourceComponent: plateProgressContent
            }
        }

        Component {
            id: meshContent
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: UM.Theme.getSize("thin_margin").height

                BedMeshMap {
                    id: meshDetail
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 190 * screenScaleFactor
                    printer: root.printer
                    // Rehydrated at creation: the model already holds the
                    // persisted value, and a fresh map must never open
                    // with the dots missing while the checkbox shows on.
                    showProbePoints: root.printer != null ? root.printer.showProbePoints : false
                }

                // The dual-ended range filter (a request):
                // the SAME five-stop blue-to-red scale the Preview's
                // bed-mesh overlay uses, shared by both surfaces. The
                // window lives in the model, so the Preview card's
                // slider and this one stay synchronised.
                BedMeshRangeSlider {
                    id: meshRangeSlider
                    Layout.fillWidth: true
                    enabled: root.printer != null && root.printer.bedMeshAvailable
                    minimum: root.printer != null ? root.printer.bedMeshMinimum : 0
                    maximum: root.printer != null ? root.printer.bedMeshMaximum : 0
                    low: root.printer != null ? root.printer.bedMeshThresholdLow : 0
                    high: root.printer != null ? root.printer.bedMeshThresholdHigh : 0
                    onWindowAdjusted: {
                        if (root.printer != null) {
                            root.printer.setBedMeshThresholds(low, high);
                        }
                    }
                }

                Row {
                    Layout.fillWidth: true
                    UM.Label {
                        width: parent.width / 2
                        text: root.printer != null ? "Low " + root.printer.bedMeshMinimum.toFixed(3) + " mm" : "Low"
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                    UM.Label {
                        width: parent.width / 2
                        text: root.printer != null ? "High " + root.printer.bedMeshMaximum.toFixed(3) + " mm" : "High"
                        horizontalAlignment: Text.AlignRight
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                }

                UM.Label {
                    // The Klipper-clamped disclaimer (a
                    // request): the same honest claim the Preview's
                    // legend makes.
                    Layout.fillWidth: true
                    text: "Neon orange outline = the probed mesh bounds; outside = the boundary values, continued as Klipper clamps them"
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default_italic")
                    wrapMode: Text.WordWrap
                }

                // The detail map is component-scoped, so its live
                // refresh lives here in scope.
                Connections {
                    target: root.printer
                    function onTypedControlsChanged() {
                        meshDetail.refresh();
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("thin_margin").width
                    UM.CheckBox {
                        checked: root.printer != null ? root.printer.showProbePoints : false
                        onToggled: {
                            if (root.printer != null) {
                                root.printer.setShowProbePoints(checked);
                            }
                        }
                    }
                    UM.Label {
                        text: "Probe points"
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                }

                // The crosshair readout row is permanent so the map
                // never resizes on hover; it shows the placeholder until
                // the cursor snaps to a probe point OR reads a clamped
                // (extended) value.
                UM.Label {
                    Layout.fillWidth: true
                    text: (meshDetail.hoverColumn >= 0 || meshDetail.hoverClamped) ? meshDetail.hoverText : "Hover the map for probe coordinates"
                    color: (meshDetail.hoverColumn >= 0 || meshDetail.hoverClamped) ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                    horizontalAlignment: Text.AlignHCenter
                }

                GridLayout {
                    columns: 3
                    Layout.fillWidth: true
                    UM.Label {
                        text: "Min " + (root.printer != null ? root.printer.bedMeshMinimum.toFixed(3) : "0.000") + " mm"
                    }
                    UM.Label {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: "Range " + (root.printer != null ? root.printer.bedMeshRange.toFixed(3) : "0.000") + " mm"
                    }
                    UM.Label {
                        horizontalAlignment: Text.AlignRight
                        text: "Max " + (root.printer != null ? root.printer.bedMeshMaximum.toFixed(3) : "0.000") + " mm"
                    }
                }

                UM.Label {
                    Layout.fillWidth: true
                    text: root.printer != null ? "X " + root.printer.bedMeshXMin.toFixed(1) + "…" + root.printer.bedMeshXMax.toFixed(1) + " mm   ·   Y " + root.printer.bedMeshYMin.toFixed(1) + "…" + root.printer.bedMeshYMax.toFixed(1) + " mm" : ""
                    color: UM.Theme.getColor("text_inactive")
                    horizontalAlignment: Text.AlignHCenter
                }

                UM.Label {
                    Layout.fillWidth: true
                    text: "The faded perimeter is extrapolated to Cura's bed edge; values shown are the actual Klipper mesh heights. The Preview's height exaggeration adjusts from its card."
                    color: UM.Theme.getColor("text_inactive")
                    wrapMode: Text.WordWrap
                }
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
                    text: plateFace.clickProgress >= 2 ? "Click again to " + plateFace.pendingAction + " (" + plateFace.clickProgress + " of 3)" : (plateFace.hoveredName !== "" ? plateFace.hoverDetail() : "Triple-click to exclude, or restore an excluded object")
                    elide: Text.ElideRight
                    color: plateFace.hoveredName !== "" ? plateFace.hoverInk() : UM.Theme.getColor("text_inactive")
                    horizontalAlignment: Text.AlignHCenter
                }

                Flow {
                    Layout.fillWidth: true
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
                            text: "included"
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
                            text: "current"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        // The printed state derives from the index's
                        // executed motions — no index, no green (the
                        // live ruling: the legend must not promise
                        // what the data cannot say).
                        visible: root.printer != null && root.printer.plateProgressAvailable
                        spacing: 4 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: width
                            radius: width / 2
                            color: MoonrakerTheme.plateCurrent
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "printed"
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
                            text: "excluded"
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
                    visible: root.printer != null && root.printer.plateHasObjects && !root.printer.plateProgressAvailable
                    printerModel: root.printer
                    idleInstruction: "Download and index the print to track the printed state."
                }

                UM.Label {
                    Layout.fillWidth: true
                    text: "A triple-click sends the command immediately — there is no confirmation dialog. A restore is allowed only inside the grace window."
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default_italic")
                    wrapMode: Text.WordWrap
                }
            }
        }

        Component {
            id: plateProgressContent
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
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
                function _feedRenderView() {
                    if (root.printer != null) {
                        // The device-pixel backing (bounded supersampling):
                        // the worker paints at the screen's physical
                        // resolution and the scene-graph samples down to
                        // the logical face — never an enlarged 1x raster.
                        root.printer.setFollowerView("popover", progressFace.viewScale, progressFace.lineScale, progressFace.width, progressFace.height, progressFace.compact, progressFace.viewPanX, progressFace.viewPanY, Math.min(2.0, Math.max(1.0, Screen.devicePixelRatio)));
                    }
                }

                PlateProgressFace {
                    id: progressFace
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 200 * screenScaleFactor
                    printerModel: root.printer
                    progress: root.printer != null ? ({
                            "available": root.printer.plateProgressAvailable,
                            "scrubVector": root.printer.plateScrubVector,
                            "reason": root.printer.plateProgressReason,
                            "layers": root.printer.plateLayers,
                            "split": root.printer.plateSplit,
                            "anchor": root.printer.plateProgressAnchor,
                            "method": "motion index",
                            "navigationData": root.printer.plateNavigationData,
                            "navigationSplit": root.printer.plateNavigationSplit,
                            "navigationBacking": root.printer.plateNavigationBacking
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
                    lineScale: root.printer != null ? root.printer.followerLineScale : 0.7
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
                    Flow {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("narrow_margin").height
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
                        UM.CheckBox {
                            text: "Travels"
                            checked: root.printer != null ? root.printer.followerShowTravels : false
                            onToggled: {
                                if (root.printer != null) {
                                    root.printer.setFollowerShowTravels(checked);
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

                // The colour key: the class palette the progress canvas
                // paints with, plus the pending and travel styles (the
                // live request). One wrapping flow of swatch+label
                // pairs, the picker's legend idiom.
                Flow {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("thin_margin").width
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: progressFace.classColour("WALL-OUTER")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Wall outer"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: progressFace.classColour("WALL-INNER")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Wall inner"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: progressFace.classColour("SKIN")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Skin"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: progressFace.classColour("FILL")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Infill"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: progressFace.classColour("SUPPORT")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Support"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: progressFace.classColour("SKIRT")
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Skirt"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 1 * screenScaleFactor
                            color: MoonrakerTheme.plateTravel
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Travel"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 2 * screenScaleFactor
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 2 * screenScaleFactor
                            color: MoonrakerTheme.seriesDefault
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        UM.Label {
                            text: "Layer ghost"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
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
                        enabled: root.printer != null && root.printer.followerLineScale > 0.5
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setFollowerLineScale(Math.max(0.5, root.printer.followerLineScale - 0.25));
                            }
                        }
                    }
                    UM.Label {
                        text: (root.printer != null ? root.printer.followerLineScale : 0.7).toFixed(2) + "×"
                        width: 34 * screenScaleFactor
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Cura.SecondaryButton {
                        id: thickerButton
                        fixedWidthMode: true
                        width: 28 * screenScaleFactor
                        text: "+"
                        enabled: root.printer != null && root.printer.followerLineScale < 2.0
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setFollowerLineScale(Math.min(2.0, root.printer.followerLineScale + 0.25));
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
                        // A detach holds the layer the face shows; an
                        // attach needs a live index to rejoin.
                        enabled: root.printer != null && (root.printer.followerAttached ? root.printer.plateProgressAnchor >= 0 : root.printer.plateLayerCount > 0)
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
                        // The keyboard nudge steps a PERCENT of the
                        // layer (a single motion over tens of
                        // thousands is invisible — the live report);
                        // the pointer keeps the full 1-motion
                        // granularity.
                        stepSize: layerProgressSlider.activeFocus ? Math.max(1, Math.round((root.printer != null ? root.printer.plateLayerMotionCount : 0) / 100)) : 1
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
                            var pct = Math.round(Math.max(0, split) / total * 100);
                            return isNaN(pct) ? "—" : pct + "%";
                        }
                    }
                }

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
                        root.printer.setFollowerLayerProgress(layerProgressSlider.selectedValue());
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
            x: Math.min(Math.max(0, chartPanel.hoverCursor.x + 14), Math.max(0, root.width - width - 4))
            y: chartPanel.hoverCursor.y + height + 16 > root.height ? Math.max(0, chartPanel.hoverCursor.y - height - 10) : chartPanel.hoverCursor.y + 16

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
