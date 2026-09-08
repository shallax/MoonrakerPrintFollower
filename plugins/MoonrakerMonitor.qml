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
        property bool bedMeshPanelOpen: false
        property bool infoCollapsed: root.printer != null ? root.printer.infoCollapsed : false
        property bool statusCollapsed: root.printer != null ? root.printer.statusCollapsed : false

        function bedMeshColor(value, minimum, maximum) {
            var limit = Math.max(Math.abs(minimum), Math.abs(maximum), 0.000000001);
            var t = Math.max(0.0, Math.min(1.0, 0.5 + 0.5 * value / limit));
            var stops = [[0.00, 0.10, 0.28, 0.95], [0.25, 0.00, 0.72, 1.00], [0.50, 0.20, 0.86, 0.38], [0.75, 1.00, 0.82, 0.12], [1.00, 0.92, 0.16, 0.12]];
            for (var i = 1; i < stops.length; ++i) {
                if (t <= stops[i][0]) {
                    var left = stops[i - 1];
                    var right = stops[i];
                    var f = (t - left[0]) / Math.max(0.000000001, right[0] - left[0]);
                    return Qt.rgba(left[1] + (right[1] - left[1]) * f, left[2] + (right[2] - left[2]) * f, left[3] + (right[3] - left[3]) * f, 1.0);
                }
            }
            return Qt.rgba(0.92, 0.16, 0.12, 1.0);
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
                bedMeshCanvas.requestPaint();
                if (root.printer == null || !root.printer.bedMeshAvailable) {
                    root.bedMeshPanelOpen = false;
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

                            Cura.SecondaryButton {
                                id: mapButton
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.bedMeshAvailable
                                text: root.bedMeshPanelOpen ? "Hide bed mesh map" : "Bed mesh map"
                                tooltip: root.printer != null ? "Show the active Klipper mesh (" + root.printer.bedMeshRangeText + ")." : ""
                                onClicked: {
                                    root.bedMeshPanelOpen = !root.bedMeshPanelOpen;
                                    if (root.bedMeshPanelOpen)
                                        bedMeshCanvas.requestPaint();
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
                Cura.RoundedRectangle {
                    id: meshPanel
                    visible: root.bedMeshPanelOpen && root.printer != null && root.printer.bedMeshAvailable
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    width: 390 * screenScaleFactor
                    height: 430 * screenScaleFactor
                    z: 999
                    color: UM.Theme.getColor("main_background")
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    radius: UM.Theme.getSize("default_radius").width

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        spacing: UM.Theme.getSize("thin_margin").height

                        UM.Label {
                            Layout.fillWidth: true
                            text: "Bed mesh — " + (root.printer != null ? root.printer.bedMeshProfile : "")
                            font: UM.Theme.getFont("medium_bold")
                            elide: Text.ElideRight
                        }

                        Canvas {
                            id: bedMeshCanvas
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 215 * screenScaleFactor

                            onPaint: {
                                var ctx = getContext("2d");
                                ctx.clearRect(0, 0, width, height);
                                if (root.printer == null || !root.printer.bedMeshAvailable)
                                    return;
                                var rows = root.printer.bedMeshRows;
                                var columns = root.printer.bedMeshColumns;
                                var values = root.printer.bedMeshValues;
                                if (rows < 2 || columns < 2 || values.length !== rows * columns)
                                    return;
                                // Fit the mesh to its real X/Y aspect: the plot mirrors the
                                // bed shape instead of forcing the probe grid square.
                                var xSpan = root.printer.bedMeshXMax - root.printer.bedMeshXMin;
                                var ySpan = root.printer.bedMeshYMax - root.printer.bedMeshYMin;
                                var plotWidth = width;
                                var plotHeight = height;
                                var offsetX = 0;
                                var offsetY = 0;
                                if (xSpan > 0 && ySpan > 0) {
                                    var target = xSpan / ySpan;
                                    if (width / height > target) {
                                        plotWidth = height * target;
                                        offsetX = (width - plotWidth) / 2;
                                    } else {
                                        plotHeight = width / target;
                                        offsetY = (height - plotHeight) / 2;
                                    }
                                }
                                var cellWidth = plotWidth / columns;
                                var cellHeight = plotHeight / rows;
                                for (var screenRow = 0; screenRow < rows; ++screenRow) {
                                    var meshRow = rows - 1 - screenRow;
                                    for (var column = 0; column < columns; ++column) {
                                        var value = Number(values[meshRow * columns + column]);
                                        ctx.fillStyle = root.bedMeshColor(value, root.printer.bedMeshMinimum, root.printer.bedMeshMaximum);
                                        ctx.fillRect(offsetX + column * cellWidth, offsetY + screenRow * cellHeight, cellWidth + 1, cellHeight + 1);
                                    }
                                }
                            }
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
    }
}
