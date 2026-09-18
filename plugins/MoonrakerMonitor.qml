import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
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

        Component.onCompleted: {
            updateCameraImage();
        }

        // The section-order apply (the 4.5.0 live find): the shell
        // constructs BEFORE the printer model arrives, and the
        // model's hydration publish can fire sectionLayoutChanged
        // before the Connections below attach — the panes kept the
        // default order until an interaction re-emitted. This retry
        // waits for the printer, then applies once; the Connections
        // handler carries the live changes afterwards.
        Timer {
            id: sectionOrderApply
            interval: 200
            repeat: true
            running: true
            property int attempts: 0
            onTriggered: {
                if (root.printer != null) {
                    root.applySectionOrder(infoContent, "information");
                    root.applySectionOrder(statusContent, "status");
                    sectionOrderApply.running = false;
                } else if (++sectionOrderApply.attempts > 50) {
                    // The printer never arrived: the Connections path
                    // still covers a late-arriving model.
                    sectionOrderApply.running = false;
                }
            }
        }

        Connections {
            target: root.printer
            function onCameraUrlChanged() {
                root.updateCameraImage();
            }
            function onCameraRefreshChanged() {
                root.updateCameraImage();
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
        }

        // The mini widget's series: primary sensors (extruders, bed,
        // chamber heater) by default; when EVERY primary is hidden,
        // up to two of the remaining visible sensors stand in so the
        // preview never goes blank while data exists (the
        // request).
        property var miniChartSeries: {
            var payload = root.printer != null ? root.printer.temperatureChart : null;
            if (payload == null) {
                return [];
            }
            var series = payload.series;
            var primary = [];
            var others = [];
            for (var i = 0; i < series.length; ++i) {
                if (!series[i].visible) {
                    continue;
                }
                if (series[i].primary) {
                    primary.push(series[i]);
                } else {
                    others.push(series[i]);
                }
            }
            if (primary.length === 0) {
                return others.slice(0, 2);
            }
            // Primaries always get priority; when fewer than two are
            // visible, non-primaries top the preview up to two so it
            // never shrinks to a single line.
            return primary.concat(others.slice(0, Math.max(0, 2 - primary.length)));
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
        // author's engine (the live report). Hysteresis: hiding
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
            // the guard false on the author's engine and its groups
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
            selectedChartSensor = "";
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
            // its triggers.
            updateCameraImage();
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
        // The author's ruling (2026-09-10): when the stage is too
        // narrow for the Webcam pane at its minimum, the Information
        // pane auto-collapses to make room. The trigger is computed
        // from FIXED constants — the expanded Info width, the webcam
        // pane's label-free minimum and the status pane's minimum —
        // NEVER from the post-collapse layout, so collapsing
        // Information cannot move the goal post and no
        // hysteresis oscillation can form. Re-expansion waits for
        // the required width PLUS a margin, so the boundary cannot
        // jitter either.
        property real infoComfortWidth: (240 + 220 + 410) * screenScaleFactor + 4 * UM.Theme.getSize("default_margin").width
        property bool infoPersistedCollapsed: root.printer != null ? root.printer.infoCollapsed : false
        // The author's ruling: fold the Information pane before the
        // WEBCAM pane starts being crushed. Empirically probed in the
        // harness (probe3): the camera column squeezes below its
        // 220 px comfort width at a stage width of ~900 px — the old
        // release threshold (734 px) sat BELOW the squeeze boundary,
        // so every shrink released the latch the instant it fired
        // (and the latch only fires on transitions, so it never
        // re-armed below that). The comfort width — info 240 +
        // camera 220 + status 410 + margins — plus a 40 px margin
        // puts the release ABOVE the squeeze boundary: the latch
        // holds, and the dead zone between the two prevents
        // flapping. Both thresholds come from the same fixed
        // constant, never from the post-collapse layout.
        property bool webcamSqueezed: cameraPane.viewportWidth > 0 && cameraPane.viewportWidth < 220 * screenScaleFactor
        property bool infoAutoCollapsed: false
        onWebcamSqueezedChanged: {
            if (webcamSqueezed && !root.infoPersistedCollapsed) {
                root.infoAutoCollapsed = true;
            }
        }
        onWidthChanged: {
            if (root.width >= infoComfortWidth + 40 * screenScaleFactor) {
                root.infoAutoCollapsed = false;
            }
        }
        onInfoAutoCollapsedChanged: {
            if (infoAutoCollapsed) {
                root.openPopOver = "";
            }
        }
        property bool infoCollapsed: root.infoPersistedCollapsed || root.infoAutoCollapsed
        property bool statusCollapsed: root.printer != null ? root.printer.statusCollapsed : false
        property string connectionDotColour: root.printer != null && root.printer.monitorConnected ? MoonrakerTheme.successGreen : MoonrakerTheme.errorRed

        ColorDialog {
            id: chartColorDialog
            title: root.printer != null && root.printer.britishSpelling ? "Sensor colour" : "Sensor color"
            onAccepted: {
                var colour = selectedColor;
                var hex = "#" + ((1 << 24) + (Math.round(colour.r * 255) << 16) + (Math.round(colour.g * 255) << 8) + Math.round(colour.b * 255)).toString(16).slice(-6);
                if (root.printer != null && root.selectedChartSensor !== "") {
                    root.printer.setTemperatureSensorColor(root.selectedChartSensor, hex);
                }
            }
        }

        Cura.MessageDialog {
            id: excludeObjectDialog
            property string targetName: ""
            title: "Exclude object?"
            text: targetName.length > 0 ? "Stop printing '" + targetName + "' for the rest of this job? This cannot be undone without restarting the print." : "Stop printing this object for the rest of this job?"
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted: {
                if (root.printer != null && targetName.length > 0) {
                    root.printer.excludeObject(targetName);
                }
                targetName = "";
            }
            onRejected: targetName = ""
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
                onHeightChanged: root.updateInfoReadoutFits()
                // Collapsed, the pane shrinks to the toggle button and its
                // margins; the vertical title below explains the strip.
                Layout.preferredWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 240 * screenScaleFactor)
                Layout.minimumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 170 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 240 * screenScaleFactor)
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
                        // author's ruling).
                        width: 28 * screenScaleFactor
                        iconSize: 12 * screenScaleFactor
                        height: width
                        implicitHeight: width

                        // The SAME theme-chevron family as the console
                        // and status toggles (the ruling: all
                        // pane collapse buttons uniform). This pane is
                        // leftmost and collapses left.
                        iconSource: root.infoCollapsed ? UM.Theme.getIcon("ChevronSingleRight") : UM.Theme.getIcon("ChevronSingleLeft")
                        tooltip: root.infoAutoCollapsed ? "The window is too narrow — widen it to show the information." : (root.infoCollapsed ? "Show the information." : "Hide the information.")
                        onClicked: {
                            // Auto-collapsed-by-width is not a
                            // clickable toggle: only a wider window
                            // restores the pane (the console's
                            // too-narrow precedent).
                            if (root.infoAutoCollapsed) {
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
                        tooltip: "Configure the information sections."
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
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width
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
                        width: infoFlick.width - infoScrollbar.width - UM.Theme.getSize("default_margin").width
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
                // The collapsed readout (the author's 2026-09-17
                // ruling): the hotend and bed temperatures from the
                // PRINTER's peripherals fill the empty space BELOW
                // the title — regular text, not the title's face.
                // The collapsed readout (the author's 2026-09-17
                // ruling): the hotend and bed temperatures from the
                // PRINTER's peripherals in ONE rotated flat row of
                // explicit children — the structure the author's
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
                    // the content on the author's engine, the live
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
                        // left a huge gap between the cards — the
                        // author's live report). Its height is the
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
                        // rendered two lines tall with a white gap (the
                        // author's report; the harness engine disagreed).
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

                                UM.TooltipArea {
                                    anchors.fill: parent
                                    text: "Drag to resize the console."
                                    acceptedButtons: Qt.NoButton
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
                                    // author's ruling).
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
                                    tooltip: consolePanel.tooNarrow ? "The window is too narrow — widen it to expand the console." : (root.printer != null && root.printer.sectionExpandedMap["console"] !== false ? "Collapse the console." : "Expand the console.")
                                    onClicked: {
                                        if (root.printer != null && !consolePanel.tooNarrow) {
                                            // The poll follows the pane:
                                            // collapsing stops the
                                            // gcode-store fetch (the
                                            // author's expanded-only
                                            // ruling), expanding starts
                                            // it with a backfill seed.
                                            var expanding = root.printer.sectionExpandedMap["console"] === false;
                                            root.printer.setSectionExpanded("console", expanding);
                                            root.printer.setConsoleExpanded(expanding);
                                        }
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
                                // The author's cheat: if the app started
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
                                    } catch (e) {
                                    }
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
                                            // author's cheat). Recolour
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
                                    // outside the black border (the
                                    // author's live report).
                                    clip: true

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: UM.Theme.getSize("narrow_margin").width
                                        spacing: UM.Theme.getSize("thin_margin").height

                                        Item {
                                            id: consoleOutputHost
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true

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
                                                    // lines run underneath (the
                                                    // author's live report). A
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
                                                        // word boundaries (the
                                                        // author's live report).
                                                        wrapMode: TextEdit.Wrap
                                                        font.family: consoleSection.monoFamily()
                                                        color: MoonrakerTheme.consoleText
                                                        // No blinking caret: a read-only
                                                        // terminal pane has no cursor, and
                                                        // the caret's phase made the
                                                        // captures nondeterministic.
                                                        cursorVisible: false
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
                                                placeholderText: "G-code command…"
                                                font.family: consoleSection.monoFamily()
                                                enabled: root.printer != null && root.printer.monitorConnected
                                                Keys.onReturnPressed: consoleSection.consoleSend()
                                                Keys.onUpPressed: consoleSection.consoleRecall(1)
                                                Keys.onDownPressed: consoleSection.consoleRecall(-1)
                                            }
                                            Cura.SecondaryButton {
                                                text: "Send"
                                                objectName: "moonrakerConsoleSend"
                                                enabled: root.printer != null && root.printer.monitorConnected
                                                onClicked: consoleSection.consoleSend()
                                            }
                                            Cura.SecondaryButton {
                                                text: "Clear"
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            // The transport detail rides the dot's
                            // tooltip: "connected over websocket" or
                            // "connected over HTTP polling" (the
                            // author's chosen spot for it).
                            text: root.printer != null && root.printer.monitorConnected ? (root.printer.connectionDetail.length > 0 ? "Connected to Moonraker — " + root.printer.connectionDetail + "." : "Connected to Moonraker.") : "Disconnected from Moonraker."
                            acceptedButtons: Qt.NoButton
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

                        UM.TooltipArea {
                            anchors.fill: parent
                            text: "Open the Moonraker frontend."
                            acceptedButtons: Qt.NoButton
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
                        tooltip: "Configure the printer-status sections."
                        onClicked: {
                            root.buildConfigureRows("status");
                            // Imperative positioning, the same reason
                            // as the information card.
                            var statusEdge = statusHeader.mapToItem(root, statusHeader.x + statusHeader.width, 0);
                            statusConfigurePopOver.x = statusEdge.x - statusConfigurePopOver.width;
                            statusConfigurePopOver.y = statusEdge.y + statusHeader.height + UM.Theme.getSize("thin_margin").height;
                            root.openPopOver = "sections-status";
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
                        // author's ruling).
                        width: 28 * screenScaleFactor
                        iconSize: 12 * screenScaleFactor
                        height: width
                        implicitHeight: width

                        // The SAME theme-chevron family as the console
                        // and info toggles; this pane is rightmost and
                        // collapses right.
                        iconSource: root.statusCollapsed ? UM.Theme.getIcon("ChevronSingleLeft") : UM.Theme.getIcon("ChevronSingleRight")
                        tooltip: root.statusCollapsed ? "Show the printer status." : "Hide the printer status."
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setStatusCollapsed(!root.statusCollapsed);
                            }
                        }
                    }
                }

                Flickable {
                    id: statusFlick
                    visible: !root.statusCollapsed
                    anchors.top: statusHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width
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
                        // The reflow loggers (the 4.5.0 polish-loop
                        // hunt): Cura's own warning names only the
                        // layout — each child logs ITS height change
                        // the moment it happens, so the flipper is
                        // named in the session log.
                        onImplicitHeightChanged: console.log("MPF-REFLOW status column height:", implicitHeight)
                        // The constant gutter (the whats-new overlay's
                        // precedent): binding the content width to the
                        // LIVE scrollbar width fed a layout polish loop
                        // on the author's Windows run — the scrollbar
                        // overlays the gutter instead of squeezing the
                        // content in a feedback cycle.
                        width: statusFlick.width - 14 - UM.Theme.getSize("default_margin").width
                        // Spacing lives on the children: collapsed sections
                        // must contribute nothing so headers stack flush.
                        spacing: 0
                        JobSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW jobsection height:", implicitHeight, "vis=", visible)
                            visible: root.printer == null || root.printer.sectionHiddenMap["job"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }

                        TempsSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW tempssection height:", implicitHeight, "vis=", visible)
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.temperatureItems.length > 0 && root.printer.sectionHiddenMap["temps"] !== true
                            printerModel: root.printer
                        }

                        FansInfoSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW fansinfosection height:", implicitHeight, "vis=", visible)
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.fanItems.length > 0 && root.printer.sectionHiddenMap["fansinfo"] !== true
                            printerModel: root.printer
                        }
                        FilamentSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW filamentsection height:", implicitHeight, "vis=", visible)
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.filamentSensorItems.length > 0 && root.printer.sectionHiddenMap["filament"] !== true
                            printerModel: root.printer
                        }
                        ObjectsSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW objectssection height:", implicitHeight, "vis=", visible)
                            visible: root.printer == null || root.printer.sectionHiddenMap["objects"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                            onExcludeRequested: function (name) {
                                excludeObjectDialog.targetName = name;
                                excludeObjectDialog.open();
                            }
                        }
                        SystemInfoSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW systeminfosection height:", implicitHeight, "vis=", visible)
                            visible: root.printer == null || root.printer.sectionHiddenMap["systeminfo"] !== true
                            Layout.fillWidth: true
                            printerModel: root.printer
                        }
                        McusSection {
                            onImplicitHeightChanged: console.log("MPF-REFLOW mcussection height:", implicitHeight, "vis=", visible)
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
                // section stack and fed a polish loop (the author's
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
                // The collapsed readout (the author's 2026-09-17
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
                    // the structure the author's engine lays out.
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
                            UM.TooltipArea {
                                anchors.fill: parent
                                // The improve-Eta mirror names the
                                // action (the panel's catch): the
                                // strip's glyph is not the button.
                                text: "Improve the estimate — download and index this print's G-code without loading it into the preview."
                                acceptedButtons: Qt.NoButton
                            }
                        }
                        UM.Label {
                            objectName: "statusCollapsedReadoutLabel"
                            property bool fitHidden: false
                            visible: root.etaAvailable
                            opacity: fitHidden ? 0 : 1
                            text: root.printer != null ? root.printer.monitorEta : "—"
                            width: 64 * screenScaleFactor
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                            UM.TooltipArea {
                                anchors.fill: parent
                                text: "Improve the estimate — download and index this print's G-code without loading it into the preview."
                                acceptedButtons: Qt.NoButton
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
                            width: 56 * screenScaleFactor
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
        // open card closes it; the pop-overs sit above this layer.
        MouseArea {
            id: outsideClickLayer
            visible: root.openPopOver !== ""
            anchors.fill: parent
            z: 998
            acceptedButtons: Qt.LeftButton
            onClicked: {
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
            onClosed: root.openPopOver = ""
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
            onClosed: root.openPopOver = ""
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
            onClosed: {
                root.openPopOver = "";
                root.selectedChartSensor = "";
            }
            // Reset the tooltip proxies on EVERY close path — the Close
            // button, the opener's second click, the outside-click layer
            // and auto-close all flip `visible` — so a reopen never
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
                    // card's zero-sized canvas must never paint.
                    visible: root.openPopOver === "chart" && root.printer != null && root.printer.temperatureChart.series.length > 0
                    chart: root.printer != null ? root.printer.temperatureChart : ({
                            "series": [],
                            "showTargets": true,
                            "showPower": true
                        })
                }

                UM.Label {
                    Layout.fillWidth: true
                    visible: root.printer != null && root.printer.temperatureChart.series.length === 0
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
                                    var payload = root.printer != null ? root.printer.temperatureChart.series : [];
                                    for (var i = 0; i < payload.length; ++i) {
                                        if (payload[i].name === modelData.name && payload[i].points.length > 0) {
                                            return payload[i].points[payload[i].points.length - 1][1].toFixed(1) + "°C";
                                        }
                                    }
                                    return "—";
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
                        tooltip: "Pick any " + (root.printer != null && root.printer.britishSpelling ? "colour" : "color") + " for " + root.selectedChartSensorLabel + "."
                        onClicked: chartColorDialog.open()
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
            onClosed: root.openPopOver = ""

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                active: root.openPopOver === "mesh" && root.printer != null && root.printer.bedMeshAvailable
                sourceComponent: meshContent
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

                // The dual-ended range filter (the author's request):
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
                    // The Klipper-clamped disclaimer (the author's
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
                Repeater {
                    model: chartPanel.hoverValuesProxy
                    RowLayout {
                        spacing: UM.Theme.getSize("narrow_margin").width
                        Rectangle {
                            width: 8 * screenScaleFactor
                            height: 8 * screenScaleFactor
                            radius: 4 * screenScaleFactor
                            color: modelData.color
                        }
                        UM.Label {
                            text: modelData.label + ": " + modelData.text
                            font: UM.Theme.getFont("default")
                        }
                    }
                }
            }
        }
    }
}
