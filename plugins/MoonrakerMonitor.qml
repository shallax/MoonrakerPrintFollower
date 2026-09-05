import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.1 as Cura

Component
{
    id: monitorComponent

    Item
    {
        id: root

        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property bool cameraConfigured: printer != null && printer.cameraUrl != null && printer.cameraUrl.toString().length > 0

        RowLayout
        {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").width

            Cura.RoundedRectangle
            {
                id: cameraPanel
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 420 * screenScaleFactor
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width
                cornerSide: Cura.RoundedRectangle.Direction.All

                ColumnLayout
                {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    spacing: UM.Theme.getSize("default_margin").height

                    RowLayout
                    {
                        Layout.fillWidth: true

                        UM.Label
                        {
                            text: root.printer != null && root.printer.cameraName.length > 0 ? root.printer.cameraName : "Camera"
                            font: UM.Theme.getFont("large_bold")
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }

                        Cura.ComboBox
                        {
                            id: cameraSelector
                            visible: root.printer != null && root.printer.webcamNames.length > 1
                            enabled: visible
                            model: root.printer != null ? root.printer.webcamNames : []
                            currentIndex: root.printer != null ? root.printer.activeWebcamIndex : -1
                            onActivated:
                            {
                                if (root.printer != null)
                                {
                                    root.printer.selectWebcam(index)
                                }
                            }
                        }

                        Cura.SecondaryButton
                        {
                            text: "Refresh"
                            onClicked:
                            {
                                if (root.printer != null)
                                {
                                    root.printer.refreshWebcams()
                                }
                            }
                        }
                    }

                    Item
                    {
                        id: cameraViewport
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        UM.Label
                        {
                            anchors.centerIn: parent
                            visible: !root.cameraConfigured
                            text: "No webcam configured in Moonraker"
                            color: UM.Theme.getColor("text_inactive")
                            font: UM.Theme.getFont("medium")
                        }

                        Cura.NetworkMJPGImage
                        {
                            id: cameraImage
                            visible: root.cameraConfigured
                            source: root.cameraConfigured ? root.printer.cameraUrl : ""
                            rotation: root.printer != null ? root.printer.cameraRotation : 0
                            anchors.centerIn: parent

                            property bool imageRotated: rotation === 90 || rotation === 270
                            property real maxViewWidth: cameraViewport.width
                            property real maxViewHeight: cameraViewport.height
                            property real fitScale:
                            {
                                if (imageWidth <= 0 || imageHeight <= 0)
                                {
                                    return 1
                                }
                                if (imageRotated)
                                {
                                    return Math.min(maxViewWidth / imageHeight, maxViewHeight / imageWidth)
                                }
                                return Math.min(maxViewWidth / imageWidth, maxViewHeight / imageHeight)
                            }

                            width: Math.max(1, Math.floor(imageWidth * fitScale))
                            height: Math.max(1, Math.floor(imageHeight * fitScale))

                            transform: Scale
                            {
                                origin.x: cameraImage.width / 2
                                origin.y: cameraImage.height / 2
                                xScale: root.printer != null && root.printer.cameraFlipHorizontal ? -1 : 1
                                yScale: root.printer != null && root.printer.cameraFlipVertical ? -1 : 1
                            }

                            onVisibleChanged:
                            {
                                if (source !== "")
                                {
                                    if (visible) start()
                                    else stop()
                                }
                            }

                            Component.onCompleted:
                            {
                                if (source !== "")
                                {
                                    start()
                                }
                            }
                        }
                    }
                }
            }

            Cura.RoundedRectangle
            {
                id: statusPanel
                Layout.preferredWidth: 330 * screenScaleFactor
                Layout.minimumWidth: 280 * screenScaleFactor
                Layout.fillHeight: true
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width
                cornerSide: Cura.RoundedRectangle.Direction.All

                ColumnLayout
                {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    spacing: UM.Theme.getSize("default_margin").height

                    UM.Label
                    {
                        text: OutputDevice != null ? OutputDevice.name : "Moonraker"
                        font: UM.Theme.getFont("large_bold")
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    UM.Label
                    {
                        text: root.printer != null ? root.printer.monitorState : "Not connected"
                        font: UM.Theme.getFont("medium_bold")
                        Layout.fillWidth: true
                    }

                    UM.Label
                    {
                        text: root.printer != null && root.printer.monitorFilename.length > 0 ? root.printer.monitorFilename : "No active file"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.fillWidth: true
                        elide: Text.ElideMiddle
                    }

                    ProgressBar
                    {
                        Layout.fillWidth: true
                        from: 0
                        to: 100
                        value: root.printer != null ? root.printer.monitorProgress : 0
                    }

                    UM.Label
                    {
                        text: root.printer != null ? root.printer.monitorProgress + "%" : "0%"
                        font: UM.Theme.getFont("medium_bold")
                        Layout.alignment: Qt.AlignHCenter
                    }

                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: UM.Theme.getSize("default_lining").height
                        color: UM.Theme.getColor("lining")
                    }

                    GridLayout
                    {
                        columns: 2
                        columnSpacing: UM.Theme.getSize("default_margin").width
                        rowSpacing: UM.Theme.getSize("default_margin").height / 2
                        Layout.fillWidth: true

                        UM.Label { text: "Layer"; color: UM.Theme.getColor("text_inactive") }
                        UM.Label { text: root.printer != null ? root.printer.monitorLayer : "—"; Layout.fillWidth: true }

                        UM.Label { text: "Elapsed"; color: UM.Theme.getColor("text_inactive") }
                        UM.Label { text: root.printer != null ? root.printer.monitorElapsed : "00:00:00"; Layout.fillWidth: true }

                        UM.Label { text: "Speed"; color: UM.Theme.getColor("text_inactive") }
                        UM.Label { text: root.printer != null ? root.printer.monitorSpeed : "100%"; Layout.fillWidth: true }

                        UM.Label { text: "Flow"; color: UM.Theme.getColor("text_inactive") }
                        UM.Label { text: root.printer != null ? root.printer.monitorFlow : "100%"; Layout.fillWidth: true }

                        UM.Label { text: "Position"; color: UM.Theme.getColor("text_inactive") }
                        UM.Label
                        {
                            text: root.printer != null ? root.printer.monitorPosition : "—"
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }

                    Item { Layout.fillHeight: true }

                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: "Open Moonraker frontend"
                        onClicked:
                        {
                            if (root.printer != null)
                            {
                                root.printer.openFrontend()
                            }
                        }
                    }
                }
            }
        }
    }
}
