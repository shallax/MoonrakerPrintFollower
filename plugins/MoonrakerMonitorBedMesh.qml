import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.1 as Cura

Component
{
    id: bedMeshDashboardComponent

    Item
    {
        id: root
        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property bool bedMeshPanelOpen: false

        function bedMeshColor(value, minimum, maximum)
        {
            var limit = Math.max(Math.abs(minimum), Math.abs(maximum), 0.000000001)
            var t = Math.max(0.0, Math.min(1.0, 0.5 + 0.5 * value / limit))
            var stops = [
                [0.00, 0.10, 0.28, 0.95],
                [0.25, 0.00, 0.72, 1.00],
                [0.50, 0.20, 0.86, 0.38],
                [0.75, 1.00, 0.82, 0.12],
                [1.00, 0.92, 0.16, 0.12]
            ]
            for (var i = 1; i < stops.length; ++i)
            {
                if (t <= stops[i][0])
                {
                    var left = stops[i - 1]
                    var right = stops[i]
                    var f = (t - left[0]) / Math.max(0.000000001, right[0] - left[0])
                    return Qt.rgba(
                        left[1] + (right[1] - left[1]) * f,
                        left[2] + (right[2] - left[2]) * f,
                        left[3] + (right[3] - left[3]) * f,
                        1.0
                    )
                }
            }
            return Qt.rgba(0.92, 0.16, 0.12, 1.0)
        }

        MoonrakerMonitorDashboard
        {
            id: baseDashboardComponent
        }

        Loader
        {
            anchors.fill: parent
            sourceComponent: baseDashboardComponent
        }

        Connections
        {
            target: root.printer
            function onTypedControlsChanged()
            {
                bedMeshCanvas.requestPaint()
                if (root.printer == null || !root.printer.bedMeshAvailable)
                {
                    root.bedMeshPanelOpen = false
                }
            }
        }

        Cura.SecondaryButton
        {
            id: mapButton
            visible: root.printer != null && root.printer.bedMeshAvailable
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            anchors.leftMargin: UM.Theme.getSize("thick_margin").width
            anchors.bottomMargin: UM.Theme.getSize("thick_margin").height
            z: 1000
            text: root.bedMeshPanelOpen ? "Hide bed mesh map" : "Bed mesh map"
            tooltip: root.printer != null ? "Show the active Klipper mesh (" + root.printer.bedMeshRangeText + ")." : ""
            onClicked:
            {
                root.bedMeshPanelOpen = !root.bedMeshPanelOpen
                if (root.bedMeshPanelOpen) bedMeshCanvas.requestPaint()
            }
        }

        Cura.RoundedRectangle
        {
            id: meshPanel
            visible: root.bedMeshPanelOpen && root.printer != null && root.printer.bedMeshAvailable
            anchors.left: mapButton.left
            anchors.bottom: mapButton.top
            anchors.bottomMargin: UM.Theme.getSize("default_margin").height
            width: 390 * screenScaleFactor
            height: 365 * screenScaleFactor
            z: 999
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: UM.Theme.getSize("default_radius").width

            ColumnLayout
            {
                anchors.fill: parent
                anchors.margins: UM.Theme.getSize("default_margin").width
                spacing: UM.Theme.getSize("thin_margin").height

                RowLayout
                {
                    Layout.fillWidth: true
                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: "Bed mesh — " + (root.printer != null ? root.printer.bedMeshProfile : "")
                        font: UM.Theme.getFont("medium_bold")
                        elide: Text.ElideRight
                    }
                    Cura.SecondaryButton
                    {
                        text: "×"
                        fixedWidthMode: true
                        onClicked: root.bedMeshPanelOpen = false
                    }
                }

                Canvas
                {
                    id: bedMeshCanvas
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 215 * screenScaleFactor

                    onPaint:
                    {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        if (root.printer == null || !root.printer.bedMeshAvailable) return
                        var rows = root.printer.bedMeshRows
                        var columns = root.printer.bedMeshColumns
                        var values = root.printer.bedMeshValues
                        if (rows < 2 || columns < 2 || values.length !== rows * columns) return
                        var cellWidth = width / columns
                        var cellHeight = height / rows
                        for (var screenRow = 0; screenRow < rows; ++screenRow)
                        {
                            var meshRow = rows - 1 - screenRow
                            for (var column = 0; column < columns; ++column)
                            {
                                var value = Number(values[meshRow * columns + column])
                                ctx.fillStyle = root.bedMeshColor(value, root.printer.bedMeshMinimum, root.printer.bedMeshMaximum)
                                ctx.fillRect(column * cellWidth, screenRow * cellHeight, cellWidth + 1, cellHeight + 1)
                            }
                        }
                    }
                }

                GridLayout
                {
                    columns: 3
                    Layout.fillWidth: true
                    UM.Label { text: "Min " + (root.printer != null ? root.printer.bedMeshMinimum.toFixed(3) : "0.000") + " mm" }
                    UM.Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; text: "Range " + (root.printer != null ? root.printer.bedMeshRange.toFixed(3) : "0.000") + " mm" }
                    UM.Label { horizontalAlignment: Text.AlignRight; text: "Max " + (root.printer != null ? root.printer.bedMeshMaximum.toFixed(3) : "0.000") + " mm" }
                }

                UM.Label
                {
                    Layout.fillWidth: true
                    text: root.printer != null
                        ? "X " + root.printer.bedMeshXMin.toFixed(1) + "…" + root.printer.bedMeshXMax.toFixed(1)
                          + " mm   ·   Y " + root.printer.bedMeshYMin.toFixed(1) + "…" + root.printer.bedMeshYMax.toFixed(1) + " mm"
                        : ""
                    color: UM.Theme.getColor("text_inactive")
                    horizontalAlignment: Text.AlignHCenter
                }

                Cura.SecondaryButton
                {
                    Layout.fillWidth: true
                    text: root.printer != null && root.printer.bedMeshPreviewVisible
                        ? "Hide bed mesh from Preview"
                        : "Show bed mesh in Preview"
                    onClicked: if (root.printer != null) root.printer.setBedMeshPreviewVisible(!root.printer.bedMeshPreviewVisible)
                }

                UM.Label
                {
                    Layout.fillWidth: true
                    text: "Preview uses 20× vertical exaggeration; the colours and values here are the actual Klipper mesh heights."
                    color: UM.Theme.getColor("text_inactive")
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
