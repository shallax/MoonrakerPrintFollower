import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

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

        // Mini-chart helpers: primary sensors only (extruders, bed,
        // chamber heater), honouring the legend's visibility.
        property bool miniChartHasSeries: {
            var payload = root.printer != null ? root.printer.temperatureChart : ({
                    "series": []
                });
            var series = payload.series;
            for (var i = 0; i < series.length; ++i) {
                if (series[i].primary && series[i].visible) {
                    return true;
                }
            }
            return false;
        }
        property bool miniChartFilling: root.printer != null ? (root.printer.temperatureChart.series.length > 0 && root.printer.temperatureChart.filling) : false
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
        }
        Keys.onEscapePressed: {
            openPopOver = "";
            selectedChartSensor = "";
        }
        focus: true
        property bool infoCollapsed: root.printer != null ? root.printer.infoCollapsed : false
        property bool statusCollapsed: root.printer != null ? root.printer.statusCollapsed : false

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
                meshDetail.refresh();
                if (root.printer == null || !root.printer.bedMeshAvailable) {
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
                Layout.preferredWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 220 * screenScaleFactor)
                Layout.minimumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 150 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 220 * screenScaleFactor)
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: UM.Theme.getSize("default_margin").width
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
                        fixedWidthMode: true
                        width: 32 * screenScaleFactor
                        text: root.infoCollapsed ? "›" : "‹"
                        tooltip: root.infoCollapsed ? "Show the information." : "Hide the information."
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setInfoCollapsed(!root.infoCollapsed);
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
                                    if (root.openPopOver === "mesh")
                                        meshDetail.refresh();
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
                            sectionIcon: "Spinner"
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
                                    var payload = root.printer != null ? root.printer.temperatureChart : ({
                                            "series": [],
                                            "showTargets": true,
                                            "showPower": true,
                                            "palette": [],
                                            "filling": false,
                                            "wallOrigin": 0
                                        });
                                    var series = payload.series.filter(function (s) {
                                            return s.primary && s.visible;
                                        });
                                    return {
                                        "series": series,
                                        "showTargets": false,
                                        "showPower": false,
                                        "palette": payload.palette,
                                        "filling": payload.filling,
                                        "wallOrigin": payload.wallOrigin
                                    };
                                }
                                visible: root.miniChartHasSeries && !root.miniChartFilling
                                onClicked: {
                                    if (root.printer != null) {
                                        root.openPopOver = root.openPopOver === "chart" ? "" : "chart";
                                    }
                                }
                            }

                            UM.Label {
                                Layout.fillWidth: true
                                visible: root.printer != null && (root.miniChartFilling || !root.miniChartHasSeries)
                                text: root.miniChartFilling ? "Collecting temperature history…" : "No hotend or bed temperature data yet"
                                color: UM.Theme.getColor("text_inactive")
                                wrapMode: Text.WordWrap
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

                Cura.RoundedRectangle {
                    id: cameraPanel
                    anchors.fill: parent
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    color: UM.Theme.getColor("main_background")
                    radius: UM.Theme.getSize("default_radius").width

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 0

                        Item {
                            id: cameraViewport
                            Layout.fillWidth: true
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
                        fixedWidthMode: true
                        width: 32 * screenScaleFactor
                        text: root.statusCollapsed ? "‹" : "›"
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

                            ProgressBar {
                                Layout.fillWidth: true
                                from: 0
                                to: 100
                                value: root.printer != null ? root.printer.monitorProgress : 0
                            }

                            UM.Label {
                                text: root.printer != null ? root.printer.monitorProgress + "%" : "0%"
                                font: UM.Theme.getFont("medium_bold")
                                Layout.alignment: Qt.AlignHCenter
                            }

                            UM.Label {
                                visible: root.printer != null && root.printer.actionStatus.length > 0
                                text: root.printer != null ? root.printer.actionStatus : ""
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
                                    text: "Layer"
                                    color: UM.Theme.getColor("text_inactive")
                                    Layout.preferredWidth: 110 * screenScaleFactor
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorLayer : "—"
                                    Layout.fillWidth: true
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
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorEta : "—"
                                    Layout.fillWidth: true
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
            x: UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            onClosed: {
                root.openPopOver = "";
                root.selectedChartSensor = "";
            }

            TemperatureChart {
                id: chartPanelChart
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 180 * screenScaleFactor
                visible: root.printer != null && root.printer.temperatureChart.series.length > 0
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

            // Hover readout: the clock at the snapped cursor plus
            // each visible series' value. A Flow wraps on machines
            // with many sensors instead of eliding.
            Flow {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width
                visible: chartPanelChart.hoverClock !== ""
                UM.Label {
                    text: chartPanelChart.hoverClock
                    font: UM.Theme.getFont("medium")
                    color: UM.Theme.getColor("text_inactive")
                }
                Repeater {
                    model: chartPanelChart.hoverValues
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
                                        return payload[i].points[payload[i].points.length - 1][1].toFixed(1) + "°";
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
                    text: root.selectedChartSensorLabel + " color:"
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

        MonitorPopOver {
            id: meshPanel
            visible: root.openPopOver === "mesh" && root.printer != null && root.printer.bedMeshAvailable
            x: cameraArea.x + UM.Theme.getSize("default_margin").width
            y: UM.Theme.getSize("default_margin").height
            contentWidth: 390 * screenScaleFactor
            title: "Bed mesh — " + (root.printer != null ? root.printer.bedMeshProfile : "")
            onClosed: root.openPopOver = ""
            onVisibleChanged: {
                if (visible)
                    meshDetail.refresh();
            }

            BedMeshMap {
                id: meshDetail
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 215 * screenScaleFactor
                printer: root.printer
            }

            // The crosshair readout: coordinates and Z offset of
            // the snapped probe point.
            UM.Label {
                Layout.fillWidth: true
                visible: meshDetail.hoverColumn >= 0
                text: meshDetail.hoverText
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
}
