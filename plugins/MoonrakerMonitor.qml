import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
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
    id: monitorComponent
    Item {
        id: root

        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property bool cameraConfigured: printer != null && printer.cameraUrl != null && printer.cameraUrl.toString().length > 0
        // One open pop-over at a time ("" | "chart" | "mesh"); every
        // opener and closer writes this, so the shells can never
        // overlap or trap a close button under another card.
        property string openPopOver: ""
        property string selectedChartSensor: ""

        // The mini widget's series: primary sensors (extruders, bed,
        // chamber heater) by default; when EVERY primary is hidden,
        // up to two of the remaining visible sensors stand in so the
        // preview never goes blank while data exists (the author's
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
            openPopOver = "";
            selectedChartSensor = "";
            // The gcode-store poll follows the console's OWN collapse
            // state (the author's ruling: poll only while the console
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
        }
        Keys.onEscapePressed: {
            openPopOver = "";
            selectedChartSensor = "";
        }
        focus: true
        property bool infoCollapsed: root.printer != null ? root.printer.infoCollapsed : false
        property bool statusCollapsed: root.printer != null ? root.printer.statusCollapsed : false

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
                meshMiniMap.refresh();
                // The detail map refreshes via the Connections inside
                // meshContent — its id is component-scoped and invisible
                // here; calling it threw ReferenceError and killed this
                // handler's auto-close before it ever ran.
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
                // (the author's report).
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
                        // and status toggles (the author's ruling: all
                        // pane collapse buttons uniform). This pane is
                        // leftmost and collapses left.
                        iconSource: root.infoCollapsed ? UM.Theme.getIcon("ChevronSingleRight") : UM.Theme.getIcon("ChevronSingleLeft")
                        tooltip: root.infoCollapsed ? "Show the information." : "Hide the information."
                        onClicked: {
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

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Bed mesh"
                            sectionId: "meshmap"
                            sectionIcon: "Buildplate"
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["meshmap"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            // The mini map is the at-a-glance widget; a
                            // click opens the pop-over detail view with
                            // the probe-snapping crosshair.
                            BedMeshMap {
                                id: meshMiniMap
                                Layout.fillWidth: true
                                Layout.preferredHeight: 90 * screenScaleFactor
                                compact: true
                                printer: root.printer
                                visible: root.printer != null && root.printer.bedMeshAvailable
                                tooltipText: root.printer != null ? "Click for the full bed mesh map (" + root.printer.bedMeshRangeText + ")." : "Click for the full bed mesh map."
                                onClicked: {
                                    root.openPopOver = root.openPopOver === "mesh" ? "" : "mesh";
                                }
                            }

                            UM.Label {
                                Layout.fillWidth: true
                                visible: root.printer == null || !root.printer.bedMeshAvailable
                                text: "No bed mesh to show"
                                color: UM.Theme.getColor("text_inactive")
                                wrapMode: Text.WordWrap
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Temperature history"
                            sectionId: "temphistory"
                            sectionIconUrl: Qt.resolvedUrl("Thermometer.svg")
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["temphistory"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            // The mini widget graphs the primary sensors
                            // (extruders, bed, chamber heater) honouring
                            // the legend's visibility; the full legend,
                            // targets and power live in the click-to-
                            // enlarge pop-over.
                            TemperatureChart {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 90 * screenScaleFactor
                                compact: true
                                tooltipText: "Click for the full temperature history (last 30 minutes)."
                                chart: {
                                    var payload = root.printer != null ? root.printer.temperatureChart : null;
                                    if (payload == null) {
                                        return {
                                            "series": [],
                                            "showTargets": false,
                                            "showPower": false,
                                            "palette": [],
                                            "filling": false,
                                            "wallOrigin": 0
                                        };
                                    }
                                    return {
                                        "series": root.miniChartSeries,
                                        "showTargets": false,
                                        "showPower": false,
                                        "palette": payload.palette,
                                        "filling": payload.filling,
                                        "wallOrigin": payload.wallOrigin
                                    };
                                }
                                visible: root.miniChartHasSeries
                                onClicked: {
                                    if (root.printer != null) {
                                        root.openPopOver = root.openPopOver === "chart" ? "" : "chart";
                                    }
                                }
                            }

                            // The mini chart's own legend: colour dot,
                            // sensor name and live value, so the
                            // unlabelled sparklines stay readable.
                            Flow {
                                Layout.fillWidth: true
                                visible: root.miniChartHasSeries
                                spacing: UM.Theme.getSize("default_margin").width
                                Repeater {
                                    model: root.miniChartSeries
                                    RowLayout {
                                        spacing: UM.Theme.getSize("narrow_margin").width
                                        Rectangle {
                                            width: 8 * screenScaleFactor
                                            height: 8 * screenScaleFactor
                                            radius: 4 * screenScaleFactor
                                            color: modelData.color
                                        }
                                        UM.Label {
                                            text: {
                                                var payload = root.printer != null ? root.printer.temperatureChart.series : [];
                                                var value = "—";
                                                for (var i = 0; i < payload.length; ++i) {
                                                    if (payload[i].name === modelData.name && payload[i].points.length > 0) {
                                                        value = payload[i].points[payload[i].points.length - 1][1].toFixed(1) + "°C";
                                                    }
                                                }
                                                return modelData.label + " " + value;
                                            }
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }

                            // The label doubles as the pop-over opener
                            // when the mini chart is gone: hiding every
                            // primary sensor must not lock the user out
                            // of the legend that re-enables them.
                            UM.Label {
                                Layout.fillWidth: true
                                visible: root.printer != null && !root.miniChartHasSeries
                                text: root.printer != null && root.printer.temperatureChart.series.length > 0 ? "All sensors hidden — click to re-enable one in the chart." : "No hotend or bed temperature data yet"
                                color: root.printer != null && !root.miniChartHasSeries && root.printer.temperatureChart.series.length > 0 ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                                wrapMode: Text.WordWrap
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    enabled: root.printer != null && !root.miniChartHasSeries && root.printer.temperatureChart.series.length > 0
                                    onClicked: {
                                        if (root.printer != null) {
                                            root.openPopOver = root.openPopOver === "chart" ? "" : "chart";
                                        }
                                    }
                                }
                            }
                        }
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
            }

            Item {
                id: cameraArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 180 * screenScaleFactor

                // Two SIBLING cards in the middle column: the webcam
                // card on top, the console card below it (the author's
                // ruling — the console nested inside the webcam card
                // read as one mis-anchored pane).
                ColumnLayout {
                    anchors.fill: parent
                    spacing: UM.Theme.getSize("default_margin").height

                    Cura.RoundedRectangle {
                        id: cameraPanel
                        Layout.fillWidth: true
                        // The webcam card ALWAYS fills: the layout
                        // allocates the console's capped preferred
                        // height first and the webcam card absorbs the
                        // rest — collapsing the console shrinks its
                        // preferred to the header row and the webcam
                        // grows into the freed space automatically.
                        Layout.fillHeight: true
                        Layout.preferredHeight: cameraColumn.implicitHeight
                        Layout.minimumHeight: 0
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        color: UM.Theme.getColor("main_background")
                        radius: UM.Theme.getSize("default_radius").width

                        ColumnLayout {
                            id: cameraColumn
                            anchors.fill: parent
                            spacing: 0

                            UM.Label {
                                // The pane title, in the other panes' style.
                                text: "Webcam"
                                font: UM.Theme.getFont("medium_bold")
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                Layout.topMargin: UM.Theme.getSize("default_margin").height
                                Layout.leftMargin: UM.Theme.getSize("default_margin").width
                            }

                            Item {
                                id: cameraViewport
                                Layout.fillWidth: true
                                // The camera fills the pane ONLY while the
                                // console is collapsed; expanded, the camera
                                // fits its stream and the console (the
                                // column's last child) absorbs the leftover
                                // space (the author's rulings).
                                // The viewport fills the webcam card: a
                                // small stream centres inside the card, and
                                // the card itself grows or fits with the
                                // console's collapse state.
                                Layout.fillHeight: true
                                Layout.margins: UM.Theme.getSize("default_margin").width

                                UM.Label {
                                    anchors.centerIn: parent
                                    // "Not configured" and "offline" are
                                    // different states: a configured webcam is
                                    // merely unreachable while Moonraker is
                                    // down, and must not read as missing.
                                    visible: !root.cameraConfigured && (root.printer == null || root.printer.webcamNames.length === 0)
                                    text: "No webcam configured in Moonraker"
                                    color: UM.Theme.getColor("text_inactive")
                                    font: UM.Theme.getFont("default")
                                }

                                UM.Label {
                                    anchors.centerIn: parent
                                    visible: !root.cameraConfigured && root.printer != null && root.printer.webcamNames.length > 0
                                    text: "Camera offline — reconnecting to Moonraker…"
                                    color: UM.Theme.getColor("text_inactive")
                                    font: UM.Theme.getFont("default")
                                }

                                Cura.NetworkMJPGImage {
                                    id: cameraImage
                                    visible: root.cameraConfigured
                                    source: root.cameraConfigured ? (root.printer.cameraRefreshNonce > 0 ? root.printer.cameraUrl.toString() + (root.printer.cameraUrl.toString().indexOf("?") >= 0 ? "&" : "?") + "mpf_reload=" + root.printer.cameraRefreshNonce : root.printer.cameraUrl) : ""
                                    rotation: root.printer != null ? root.printer.cameraRotation : 0
                                    anchors.centerIn: parent

                                    property bool imageRotated: rotation === 90 || rotation === 270
                                    property real maxViewWidth: cameraViewport.width
                                    property real maxViewHeight: cameraViewport.height
                                    property real fitScale: {
                                        if (imageWidth <= 0 || imageHeight <= 0) {
                                            return 1;
                                        }
                                        if (imageRotated) {
                                            return Math.min(maxViewWidth / imageHeight, maxViewHeight / imageWidth);
                                        }
                                        return Math.min(maxViewWidth / imageWidth, maxViewHeight / imageHeight);
                                    }

                                    width: Math.max(1, Math.floor(imageWidth * fitScale))
                                    height: Math.max(1, Math.floor(imageHeight * fitScale))

                                    transform: Scale {
                                        origin.x: cameraImage.width / 2
                                        origin.y: cameraImage.height / 2
                                        xScale: root.printer != null && root.printer.cameraFlipHorizontal ? -1 : 1
                                        yScale: root.printer != null && root.printer.cameraFlipVertical ? -1 : 1
                                    }

                                    onVisibleChanged: {
                                        if (source !== "") {
                                            if (visible)
                                                start();
                                            else
                                                stop();
                                        }
                                    }

                                    onSourceChanged: {
                                        if (visible && source !== "") {
                                            start();
                                        }
                                    }

                                    Component.onCompleted: {
                                        if (source !== "") {
                                            start();
                                        }
                                    }
                                }
                            }

                            // Camera control bar: a centred "Camera: <webcam>
                            // <refresh>" group tucked under the feed. The dropdown
                            // already carries the selected name, so no label repeats
                            // it. (A plain Column ignores Layout.alignment, so the
                            // bar must be a ColumnLayout for the centring to hold.)
                            ColumnLayout {
                                // No cameras, no bar: "Camera:" with an empty
                                // dropdown and a refresh button is dead chrome
                                // under the "No webcam configured" message.
                                visible: root.printer != null && root.printer.webcamNames.length > 0
                                Layout.fillWidth: true
                                spacing: 0

                                Rectangle {
                                    width: parent.width
                                    height: UM.Theme.getSize("default_lining").height
                                    color: UM.Theme.getColor("lining")
                                }

                                Item {
                                    Layout.fillWidth: true
                                    height: cameraControls.height + 2 * UM.Theme.getSize("narrow_margin").height

                                    RowLayout {
                                        id: cameraControls
                                        anchors.centerIn: parent
                                        spacing: UM.Theme.getSize("narrow_margin").width

                                        UM.Label {
                                            text: "Camera:"
                                            font: UM.Theme.getFont("medium")
                                            color: UM.Theme.getColor("text")
                                        }

                                        Cura.ComboBox {
                                            id: cameraSelector
                                            visible: root.printer != null && root.printer.webcamNames.length > 1
                                            Layout.preferredWidth: 180 * screenScaleFactor
                                            Layout.minimumWidth: 160 * screenScaleFactor
                                            enabled: visible
                                            model: root.printer != null ? root.printer.webcamNames : []
                                            currentIndex: root.printer != null ? root.printer.activeWebcamIndex : -1
                                            onActivated: function (index) {
                                                if (root.printer != null) {
                                                    root.printer.selectWebcam(index);
                                                }
                                            }
                                        }

                                        UM.SimpleButton {
                                            Layout.alignment: Qt.AlignVCenter
                                            width: UM.Theme.getSize("small_button_icon").width
                                            height: UM.Theme.getSize("small_button_icon").height
                                            color: UM.Theme.getColor("text_inactive")
                                            hoverColor: UM.Theme.getColor("text")
                                            iconSource: UM.Theme.getIcon("ArrowDoubleCircleRight")
                                            onClicked: {
                                                if (root.printer != null) {
                                                    root.printer.refreshWebcams();
                                                }
                                            }

                                            UM.TooltipArea {
                                                anchors.fill: parent
                                                text: "Refresh Moonraker's webcam list."
                                                acceptedButtons: Qt.NoButton
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    // Console: a collapsing pane beneath the
                    // webcam. printer/gcode/script returns after
                    // Klipper processes the script, and its output
                    // streams back through the gcode store.
                    Cura.RoundedRectangle {
                        Layout.fillWidth: true
                        // NO fillHeight: the card hugs the webcam card
                        // directly (a fill slot plus a maximum clamp
                        // left a huge gap between the cards — the
                        // author's live report). Its height is its
                        // content's, bounded by the hard cap below:
                        // the author's live test found even 55% of the
                        // column "way too high" — ~28% it is.
                        // Collapsed, the card is a header strip sized
                        // BY THE BUTTON with equal top/bottom margins —
                        // the button is the largest element and decides
                        // the strip (the author's ruling). The inner
                        // column's implicit does not shrink reliably
                        // once its content hides, so the collapsed
                        // height is explicit.
                        // EXPLICIT height, never the inner column's
                        // implicit: the real Cura engine computed the
                        // implicit from a collapsed chain and the card
                        // rendered two lines tall with a white gap (the
                        // author's report; the harness engine disagreed).
                        Layout.preferredHeight: (root.printer != null && root.printer.sectionExpandedMap["console"] !== false) ? Math.max(190 * screenScaleFactor, cameraArea.height * 0.28) : consoleCollapseButton.height + 2 * UM.Theme.getSize("thin_margin").height
                        Layout.maximumHeight: Math.max(190 * screenScaleFactor, cameraArea.height * 0.28)
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        color: UM.Theme.getColor("main_background")
                        radius: UM.Theme.getSize("default_radius").width

                        // While collapsed, a click ANYWHERE on the
                        // strip expands the pane, like the other
                        // panes; the header button sits above this
                        // area and keeps its own clicks.
                        MouseArea {
                            visible: root.printer != null && root.printer.sectionExpandedMap["console"] === false
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (root.printer != null) {
                                    root.printer.setSectionExpanded("console", true);
                                    root.printer.setConsoleExpanded(true);
                                }
                            }
                        }

                        ColumnLayout {
                            id: consoleColumn
                            anchors.fill: parent
                            spacing: 0

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: UM.Theme.getSize("thin_margin").height
                                // The bottom breathing room keeps the
                                // collapsed card from hugging the title
                                // on both edges (the author's report).
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
                                // (the author's ruling).
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
                                    // (the author's ruling — the first direction read
                                    // inverted).
                                    iconSource: root.printer != null && root.printer.sectionExpandedMap["console"] !== false ? UM.Theme.getIcon("ChevronSingleDown") : UM.Theme.getIcon("ChevronSingleUp")
                                    tooltip: root.printer != null && root.printer.sectionExpandedMap["console"] !== false ? "Collapse the console." : "Expand the console."
                                    onClicked: {
                                        if (root.printer != null) {
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
                                    color: root.printer != null && root.printer.sectionExpandedMap["console"] !== false ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                                }
                            }

                            ColumnLayout {
                                id: consoleSection
                                visible: root.printer != null && root.printer.sectionExpandedMap["console"] !== false
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
                                    // (the author's rulings): commands
                                    // carry ">", Moonraker's responses
                                    // carry "<", and the plugin's own
                                    // notes carry "#" in amber — a hue
                                    // Klipper's red/green/grey never
                                    // uses, so anything the plugin adds
                                    // is obviously its own. Responses
                                    // render BRIGHT (red/green); saved
                                    // commands keep their TEXT light
                                    // grey and put the verdict on the
                                    // ">" only (the author's live
                                    // ruling: the whole line turning
                                    // green was too much) — green
                                    // matches the input row's prompt,
                                    // red is a failure, quiet grey is
                                    // no verdict. While unsaved the
                                    // line stays blue (the author's
                                    // ruling). The muted hues are
                                    // contrast-checked (≥4.5:1 on the
                                    // dark well) — the old muted
                                    // family sat near 2:1.
                                    var hue = "#d9dde3";
                                    var promptHue = "";
                                    if (entry.kind === "response") {
                                        hue = entry.error ? "#f85149" : (entry.success ? "#57ab5a" : "#8b949e");
                                    } else if (entry.kind === "note") {
                                        hue = "#d29922";
                                    } else if (entry.saved === false) {
                                        // Sent but not yet flushed to
                                        // disk: blue until the save
                                        // lands (the author's ruling —
                                        // the API verdict flips too
                                        // fast to read live).
                                        hue = "#58a6ff";
                                    } else {
                                        promptHue = entry.error ? "#e05650" : (entry.success ? "#3fb950" : "#8b949e");
                                    }
                                    if (entry.restored) {
                                        // Restored lines grey uniformly:
                                        // the verdict colours are LIVE
                                        // signals, and a restored command
                                        // showing its old green read as
                                        // current state (the author's
                                        // report). Restored responses
                                        // keep their muted hues.
                                        hue = entry.kind === "command" ? "#9da7b3" : (entry.error ? "#d0635e" : (entry.success ? "#4f9a5d" : "#9da7b3"));
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
                                        // text='' — the author's smoking
                                        // gun), while the append path's
                                        // insert works there. The
                                        // restored history then opens at
                                        // the NEWEST line (the author's
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
                                    // (the author's report).
                                    if (wasAtEnd && consoleRenderedLines > 0) {
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
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 190 * screenScaleFactor
                                    // The terminal is the pane's
                                    // filler: the extra height goes
                                    // to the feed, not to dead space
                                    // under it.
                                    Layout.fillHeight: true
                                    color: "#161b22"
                                    border.color: UM.Theme.getColor("lining")
                                    border.width: UM.Theme.getSize("default_lining").width
                                    radius: UM.Theme.getSize("default_radius").width

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: UM.Theme.getSize("narrow_margin").width
                                        spacing: UM.Theme.getSize("thin_margin").height

                                        Flickable {
                                            id: consoleFlick
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            clip: true
                                            // The restore's tail scroll
                                            // fires when the metrics
                                            // SETTLE: the content height
                                            // updates over several frames
                                            // after setting the text, and
                                            // one-shot scrolls measured
                                            // stale values (the author's
                                            // reports).
                                            property bool restoreScrollPending: false
                                            // The scroll follows every
                                            // metric change until the
                                            // layout goes quiet — the
                                            // text height settles over
                                            // several frames and a
                                            // one-shot scroll kept
                                            // landing off by the command
                                            // bar (the author's reports).
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
                                            // (the author's ruling).
                                            onMovementStarted: {
                                                restoreScrollPending = false;
                                            }
                                            onContentHeightChanged: {
                                                if (restoreScrollPending) {
                                                    contentY = contentHeight - height;
                                                    restoreQuietTimer.restart();
                                                }
                                            }
                                            onHeightChanged: {
                                                if (restoreScrollPending) {
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
                                                    }
                                                }
                                            }
                                            Column {
                                                width: consoleFlick.width
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
                                                    width: parent.width
                                                    readOnly: true
                                                    selectByMouse: true
                                                    selectByKeyboard: true
                                                    textFormat: TextEdit.RichText
                                                    wrapMode: TextEdit.NoWrap
                                                    font.family: consoleSection.monoFamily()
                                                    color: "#d9dde3"
                                                    // No blinking caret: a read-only
                                                    // terminal pane has no cursor, and
                                                    // the caret's phase made the
                                                    // captures nondeterministic.
                                                    cursorVisible: false
                                                }
                                            }
                                        }

                                        UM.Label {
                                            visible: root.printer == null || root.printer.consoleLines.length === 0
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            text: "No commands yet — lines you send appear here."
                                            font.family: consoleSection.monoFamily()
                                            color: "#7d8590"
                                            elide: Text.ElideRight
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
                                                color: "#3fb950"
                                            }
                                            Cura.TextField {
                                                id: consoleInput
                                                Layout.fillWidth: true
                                                placeholderText: "G-code command…"
                                                font.family: consoleSection.monoFamily()
                                                Keys.onReturnPressed: consoleSection.consoleSend()
                                                Keys.onUpPressed: consoleSection.consoleRecall(1)
                                                Keys.onDownPressed: consoleSection.consoleRecall(-1)
                                            }
                                            Cura.SecondaryButton {
                                                text: "Send"
                                                onClicked: consoleSection.consoleSend()
                                            }
                                            Cura.SecondaryButton {
                                                text: "Clear"
                                                visible: root.printer != null && root.printer.consoleLines.length > 0
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
                        width: statusFlick.width - statusScrollbar.width - UM.Theme.getSize("default_margin").width
                        // Spacing lives on the children: collapsed sections
                        // must contribute nothing so headers stack flush.
                        spacing: 0

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Print job"
                            sectionId: "job"
                            sectionIcon: "Printer"
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["job"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            UM.Label {
                                text: root.printer != null ? root.printer.monitorState : "Not connected"
                                font: UM.Theme.getFont("medium_bold")
                                Layout.fillWidth: true
                            }

                            UM.Label {
                                text: root.printer != null && root.printer.monitorFilename.length > 0 ? root.printer.monitorFilename : "No active file"
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }

                            UM.Label {
                                visible: root.printer != null && root.printer.monitorMessage.length > 0
                                text: root.printer != null ? root.printer.monitorMessage : ""
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            OutlineProgressBar {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 10 * screenScaleFactor
                                from: 0
                                to: 100
                                value: root.printer != null ? root.printer.monitorProgress : 0
                            }

                            UM.Label {
                                text: root.printer != null ? root.printer.monitorProgress.toFixed(2) + "%" : "0.00%"
                                font: UM.Theme.getFont("medium_bold")
                                Layout.alignment: Qt.AlignHCenter
                            }

                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                // "Last action": the shared one-shot
                                // lane's status row, first in the grid so
                                // its columns ARE the grid's columns (a
                                // separate row above read as misaligned —
                                // the author's report). The caption is
                                // permanent so the row explains itself
                                // before its first event; the value is
                                // "—" until then.
                                UM.Label {
                                    text: "Last action"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null && root.printer.actionStatus.length > 0 ? root.printer.actionStatus : "—"
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                }

                                UM.Label {
                                    text: "Layer"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: UM.Theme.getSize("narrow_margin").width
                                        UM.Label {
                                            text: root.printer != null ? root.printer.monitorLayer : "—"
                                            Layout.fillWidth: true
                                            // Which source produced the layer —
                                            // Klipper's stats, the file index, or
                                            // the extrusion-guarded Z estimate.
                                            UM.TooltipArea {
                                                anchors.fill: parent
                                                text: root.printer != null && root.printer.monitorLayerSource !== undefined && root.printer.monitorLayerSource.length > 0 ? "Layer source: " + root.printer.monitorLayerSource : ""
                                                acceptedButtons: Qt.NoButton
                                            }
                                        }
                                        UM.Label {
                                            visible: root.printer != null && root.printer.monitorLayerProgress >= 0
                                            text: root.printer != null ? (root.printer.monitorLayerProgress * 100).toFixed(2) + "%" : ""
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                        Layout.topMargin: UM.Theme.getSize("thin_margin").height
                                        height: 8 * screenScaleFactor
                                        visible: root.printer != null && root.printer.monitorLayerProgress >= 0
                                        OutlineProgressBar {
                                            anchors.fill: parent
                                            from: 0
                                            to: 1
                                            value: root.printer != null ? root.printer.monitorLayerProgress : 0
                                            inset: 1 * screenScaleFactor
                                        }
                                        UM.TooltipArea {
                                            anchors.fill: parent
                                            text: "Layer progress — how far through the current layer."
                                            acceptedButtons: Qt.NoButton
                                        }
                                    }
                                }

                                UM.Label {
                                    text: "Elapsed"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorElapsed : "00:00:00"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Remaining"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: UM.Theme.getSize("narrow_margin").width
                                    UM.Label {
                                        text: root.printer != null ? root.printer.monitorEta : "—"
                                        // The ETA colour shows its basis:
                                        // normal text for the layer-timed
                                        // estimate, muted for the plain
                                        // blend — the tooltip spells both out.
                                        color: root.printer != null && root.printer.monitorEtaBasis === "blend" ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text")
                                        // The row must compress when the
                                        // phase label appears, or the
                                        // whole bar spills off the pane.
                                        elide: Text.ElideRight
                                        UM.TooltipArea {
                                            anchors.fill: parent
                                            // No basis claim while the value itself is
                                            // paused or absent — "Moonraker's estimate"
                                            // under a dash would lie (panel UX P2).
                                            text: root.printer == null || root.printer.monitorEta === "—" || root.printer.monitorEta === "Paused" ? "" : root.printer.monitorEtaBasis === "index" ? "Estimated from the G-code's layer timings × the observed speed." : "Moonraker's estimate — download the G-code for the accurate layer-timed estimate."
                                            acceptedButtons: Qt.NoButton
                                        }
                                    }
                                    // The Improve-ETA affordance: a small
                                    // download glyph beside the value it
                                    // improves, shown only while the
                                    // plain blend is the active basis.
                                    Item {
                                        width: 16 * screenScaleFactor
                                        height: 16 * screenScaleFactor
                                        visible: root.printer != null && root.printer.printActive && (root.printer.monitorEtaBasis === "blend" || root.printer.improvingEta)
                                        UM.TooltipArea {
                                            anchors.fill: parent
                                            text: root.printer != null && root.printer.improvingEta ? "Downloading and indexing the print…" : "Improve the estimate — download and index this print's G-code without loading it into the preview."
                                            acceptedButtons: Qt.NoButton
                                        }
                                        UM.ColorImage {
                                            anchors.fill: parent
                                            // The hourglass is the non-clickable
                                            // in-progress state; the download
                                            // glyph returns on failure (timeout).
                                            source: root.printer != null && root.printer.improvingEta ? Qt.resolvedUrl("Hourglass.svg") : Qt.resolvedUrl("Download.svg")
                                            color: UM.Theme.getColor("text")
                                            // The hourglass flips and rests at
                                            // each 180-degree stop while the
                                            // sand drains, then flips again.
                                            SequentialAnimation on rotation  {
                                                running: root.printer != null && root.printer.improvingEta
                                                loops: Animation.Infinite
                                                NumberAnimation {
                                                    from: 0
                                                    to: 180
                                                    duration: 350
                                                    easing.type: Easing.InOutCubic
                                                }
                                                PauseAnimation {
                                                    duration: 700
                                                }
                                                NumberAnimation {
                                                    from: 180
                                                    to: 360
                                                    duration: 350
                                                    easing.type: Easing.InOutCubic
                                                }
                                                PauseAnimation {
                                                    duration: 700
                                                }
                                            }
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            // Clickable while busy too: a click mid-pull is a
                                            // legitimate retry, and after a failed download
                                            // the glyph is the ONLY in-UI recovery (the
                                            // hourglass state ends on failure — panel P1-1).
                                            enabled: root.printer != null
                                            cursorShape: root.printer != null ? Qt.PointingHandCursor : Qt.ArrowCursor
                                            onClicked: root.printer.improveEta()
                                        }
                                    }
                                    // The spacer keeps the glyph hugging
                                    // the ETA text rather than drifting to
                                    // the cell's right edge.
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                }

                                // The download+index progress: its OWN
                                // full-width grid row, so the bar is never
                                // crushed beside the ETA text. The bar
                                // CLIPS its children, so the sweep cannot
                                // render past the bar's edge whatever the
                                // layout around it does; the sweep also
                                // restarts when the bar resizes so its
                                // captured endpoints stay current.
                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.columnSpan: 2
                                    Layout.topMargin: UM.Theme.getSize("narrow_margin").height
                                    spacing: UM.Theme.getSize("narrow_margin").width
                                    visible: root.printer != null && root.printer.improvingEta
                                    Item {
                                        id: improveEtaBar
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 8 * screenScaleFactor
                                        clip: true
                                        // A trivial 0..1 phase animation;
                                        // the sweep's POSITION is a binding
                                        // on it, so it always tracks the
                                        // current width — no captured
                                        // endpoints, no restart tricks.
                                        property real sweepPhase: 0
                                        NumberAnimation on sweepPhase  {
                                            running: root.printer != null && root.printer.improvingEta && root.printer.improveEtaProgress < 0
                                            from: 0
                                            to: 1
                                            duration: 1100
                                            loops: Animation.Infinite
                                        }
                                        Cura.RoundedRectangle {
                                            anchors.fill: parent
                                            color: "transparent"
                                            border.color: UM.Theme.getColor("lining")
                                            border.width: UM.Theme.getSize("default_lining").width
                                            radius: UM.Theme.getSize("progressbar_radius").width
                                            cornerSide: Cura.RoundedRectangle.Direction.All
                                        }
                                        Cura.RoundedRectangle {
                                            anchors.top: parent.top
                                            anchors.bottom: parent.bottom
                                            anchors.left: parent.left
                                            anchors.margins: 1 * screenScaleFactor
                                            width: Math.max(0, Math.min(1, root.printer != null ? root.printer.improveEtaProgress : 0)) * (parent.width - 2 * screenScaleFactor)
                                            color: UM.Theme.getColor("primary")
                                            radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
                                            cornerSide: Cura.RoundedRectangle.Direction.All
                                            visible: root.printer != null && root.printer.improveEtaProgress >= 0
                                        }
                                        Cura.RoundedRectangle {
                                            anchors.top: parent.top
                                            anchors.bottom: parent.bottom
                                            anchors.margins: 1 * screenScaleFactor
                                            width: (parent.width - 2 * screenScaleFactor) / 3
                                            color: UM.Theme.getColor("primary")
                                            radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
                                            cornerSide: Cura.RoundedRectangle.Direction.All
                                            visible: root.printer != null && root.printer.improvingEta && root.printer.improveEtaProgress < 0
                                            // Qualified through the bar's id:
                                            // unqualified names do NOT resolve
                                            // through the visual parent (the
                                            // engine logs a ReferenceError and
                                            // the sweep never moves).
                                            x: (1 - Math.abs(2 * improveEtaBar.sweepPhase - 1)) * (parent.width - width) + screenScaleFactor
                                        }
                                    }
                                    UM.Label {
                                        visible: root.printer != null && root.printer.improvingEta
                                        text: root.printer != null ? (root.printer.improveEtaPhase + (root.printer.improveEtaProgress >= 0 ? " " + (root.printer.improveEtaProgress * 100).toFixed(0) + "%" : "")) : ""
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.maximumWidth: 140 * screenScaleFactor
                                        elide: Text.ElideRight
                                    }
                                }

                                UM.Label {
                                    text: "Finish"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorFinish : "—"
                                    Layout.fillWidth: true
                                }

                                // Filament rows sit after Finish, beside
                                // the progress block they belong to (the
                                // author's placement). They outlive the
                                // print: "used" is exactly the figure a
                                // user wants to record after the job
                                // completes, so the rows stay through
                                // complete/cancelled until the next job
                                // starts (the UX panel). The dash means
                                // Moonraker did not report a value.
                                UM.Label {
                                    visible: root.printer != null && root.printer.filamentReadoutVisible
                                    text: "Filament used"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    visible: root.printer != null && root.printer.filamentReadoutVisible
                                    text: root.printer != null ? root.printer.filamentUsed : "—"
                                    Layout.fillWidth: true
                                }
                                UM.Label {
                                    visible: root.printer != null && root.printer.filamentReadoutVisible
                                    text: "Filament remaining"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    visible: root.printer != null && root.printer.filamentReadoutVisible
                                    text: root.printer != null ? root.printer.filamentRemaining : "—"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Speed"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorSpeed : "100%"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Flow"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorFlow : "100%"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Position"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorPosition : "—"
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        Column {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.temperatureItems.length > 0
                            CollapsibleSectionHeader {
                                width: parent.width
                                printerModel: root.printer
                                title: "Temperatures"
                                sectionId: "temps"
                                sectionIcon: "PrintQuality"
                            }
                            ColumnLayout {
                                visible: root.printer == null || root.printer.sectionExpandedMap["temps"] !== false
                                anchors.left: parent.left
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                                anchors.right: parent.right
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").height / 2

                                Repeater {
                                    model: root.printer != null ? root.printer.temperatureItems : []
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: UM.Theme.getSize("default_margin").width
                                        UM.Label {
                                            text: modelData.name
                                            color: UM.Theme.getColor("text_inactive")
                                            Layout.preferredWidth: 110 * screenScaleFactor
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: modelData.detail
                                        }
                                    }
                                }
                            }
                        }

                        Column {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.fanItems.length > 0
                            CollapsibleSectionHeader {
                                width: parent.width
                                printerModel: root.printer
                                title: "Fans"
                                sectionId: "fansinfo"
                                sectionIcon: "Fan"
                            }
                            ColumnLayout {
                                visible: root.printer == null || root.printer.sectionExpandedMap["fansinfo"] !== false
                                anchors.left: parent.left
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                                anchors.right: parent.right
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").height / 2

                                Repeater {
                                    model: root.printer != null ? root.printer.fanItems : []
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: UM.Theme.getSize("default_margin").width
                                        UM.Label {
                                            text: modelData.name
                                            color: UM.Theme.getColor("text_inactive")
                                            Layout.preferredWidth: 110 * screenScaleFactor
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: modelData.detail
                                        }
                                    }
                                }
                            }
                        }

                        Column {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.filamentSensorItems.length > 0
                            CollapsibleSectionHeader {
                                width: parent.width
                                printerModel: root.printer
                                title: "Filament sensors"
                                sectionId: "filament"
                                sectionIcon: "Spool"
                            }
                            ColumnLayout {
                                visible: root.printer == null || root.printer.sectionExpandedMap["filament"] !== false
                                anchors.left: parent.left
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                                anchors.right: parent.right
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").height / 2

                                Repeater {
                                    model: root.printer != null ? root.printer.filamentSensorItems : []
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: UM.Theme.getSize("default_margin").width
                                        UM.Label {
                                            text: modelData.name
                                            color: UM.Theme.getColor("text_inactive")
                                            Layout.preferredWidth: 110 * screenScaleFactor
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: modelData.state
                                            color: !modelData.enabled ? UM.Theme.getColor("text_inactive") : (modelData.detected ? "#43a047" : "#fb8c00")
                                            font: UM.Theme.getFont("medium")
                                        }
                                    }
                                }
                            }
                        }

                        Column {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.excludeObjectItems.length > 0
                            CollapsibleSectionHeader {
                                width: parent.width
                                printerModel: root.printer
                                title: "Objects"
                                sectionId: "objects"
                                sectionIcon: "MeshTypeNormal"
                            }
                            ColumnLayout {
                                visible: root.printer == null || root.printer.sectionExpandedMap["objects"] !== false
                                anchors.left: parent.left
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                                anchors.right: parent.right
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").height / 2

                                Repeater {
                                    model: root.printer != null ? root.printer.excludeObjectItems : []
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: UM.Theme.getSize("default_margin").width
                                        UM.Label {
                                            text: modelData.name + (modelData.current ? "  · current" : "") + (modelData.excluded ? "  · excluded" : "")
                                            color: modelData.excluded ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text")
                                            Layout.preferredWidth: 170 * screenScaleFactor
                                            elide: Text.ElideRight
                                        }
                                        Cura.SecondaryButton {
                                            visible: root.printer != null && root.printer.printActive && !modelData.excluded
                                            enabled: root.printer != null && !root.printer.actionBusy
                                            text: "Exclude"
                                            onClicked: {
                                                excludeObjectDialog.targetName = modelData.name;
                                                excludeObjectDialog.open();
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "System"
                            sectionId: "systeminfo"
                            sectionIcon: "Information"
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["systeminfo"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            GridLayout {
                                columns: 2
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                Layout.fillWidth: true

                                UM.Label {
                                    text: "Klippy"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.klippyState : "—"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Host load"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.hostLoad : "—"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Memory free"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.memoryAvailable : "—"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "CPU temp"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.cpuTemperature : "—"
                                    Layout.fillWidth: true
                                }

                                UM.Label {
                                    text: "Klipper"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.klipperVersion : "—"
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                }

                                UM.Label {
                                    text: "Moonraker"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.moonrakerVersion : "—"
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                }
                            }
                        }

                        Column {
                            Layout.fillWidth: true
                            visible: root.printer != null && root.printer.mcuItems.length > 0
                            CollapsibleSectionHeader {
                                width: parent.width
                                printerModel: root.printer
                                title: "MCUs"
                                sectionId: "mcus"
                                sectionIcon: "Plugin"
                            }
                            ColumnLayout {
                                visible: root.printer == null || root.printer.sectionExpandedMap["mcus"] !== false
                                anchors.left: parent.left
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                                anchors.right: parent.right
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").height

                                Repeater {
                                    model: root.printer != null ? root.printer.mcuItems : []
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: UM.Theme.getSize("thin_margin").height
                                        UM.Label {
                                            Layout.fillWidth: true
                                            text: modelData.name
                                            font: UM.Theme.getFont("medium_bold")
                                            elide: Text.ElideRight
                                        }
                                        // Each datum gets its own labelled row,
                                        // in the same fixed 110 px column as
                                        // the other readouts in the pane.
                                        GridLayout {
                                            columns: 2
                                            columnSpacing: UM.Theme.getSize("default_margin").width
                                            rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                            Layout.fillWidth: true

                                            UM.Label {
                                                text: "Load"
                                                color: UM.Theme.getColor("text_inactive")
                                                Layout.preferredWidth: 110 * screenScaleFactor
                                            }
                                            UM.Label {
                                                text: modelData.load
                                                Layout.fillWidth: true
                                            }

                                            UM.Label {
                                                text: "Task"
                                                color: UM.Theme.getColor("text_inactive")
                                                Layout.preferredWidth: 110 * screenScaleFactor
                                            }
                                            UM.Label {
                                                text: modelData.task
                                                Layout.fillWidth: true
                                            }

                                            UM.Label {
                                                text: "Clock"
                                                color: UM.Theme.getColor("text_inactive")
                                                Layout.preferredWidth: 110 * screenScaleFactor
                                            }
                                            UM.Label {
                                                text: modelData.frequency
                                                Layout.fillWidth: true
                                            }

                                            UM.Label {
                                                text: "Memory"
                                                color: UM.Theme.getColor("text_inactive")
                                                Layout.preferredWidth: 110 * screenScaleFactor
                                            }
                                            UM.Label {
                                                text: modelData.memory
                                                Layout.fillWidth: true
                                            }

                                            UM.Label {
                                                text: "Connection"
                                                color: UM.Theme.getColor("text_inactive")
                                                Layout.preferredWidth: 110 * screenScaleFactor
                                            }
                                            UM.Label {
                                                text: modelData.transport
                                                Layout.fillWidth: true
                                            }

                                            UM.Label {
                                                text: "Version"
                                                color: UM.Theme.getColor("text_inactive")
                                                Layout.preferredWidth: 110 * screenScaleFactor
                                            }
                                            UM.Label {
                                                text: modelData.version
                                                Layout.fillWidth: true
                                                elide: Text.ElideMiddle
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // The collapsed strip: the toggle stays at the top and the
                // pane title reads bottom-to-top directly under it.
                Item {
                    id: statusCollapsedTitleBox
                    visible: root.statusCollapsed
                    anchors.top: statusHeader.bottom
                    anchors.topMargin: UM.Theme.getSize("thin_margin").height
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: statusCollapsedTitle.implicitHeight
                    height: statusCollapsedTitle.implicitWidth
                    UM.Label {
                        id: statusCollapsedTitle
                        text: "Printer status"
                        font: UM.Theme.getFont("medium_bold")
                        color: UM.Theme.getColor("text_inactive")
                        rotation: 90
                        anchors.centerIn: parent
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
            height: Math.min(430 * screenScaleFactor, parent.height - 2 * UM.Theme.getSize("default_margin").height)
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
                    Layout.minimumHeight: 215 * screenScaleFactor
                    printer: root.printer
                    // Rehydrated at creation: the model already holds the
                    // persisted value, and a fresh map must never open
                    // with the dots missing while the checkbox shows on.
                    showProbePoints: root.printer != null ? root.printer.showProbePoints : false
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
                // the cursor snaps to a probe point.
                UM.Label {
                    Layout.fillWidth: true
                    text: meshDetail.hoverColumn >= 0 ? meshDetail.hoverText : "Hover the map for probe coordinates"
                    color: meshDetail.hoverColumn >= 0 ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
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
                    text: "Preview uses 20× vertical exaggeration. The solid area is Klipper's mesh; the faded perimeter is extrapolated to Cura's bed edge. Values shown here are the actual Klipper mesh heights."
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
            // boundary per the author's ruling. The y side is chosen
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
