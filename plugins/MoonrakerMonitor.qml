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

        Cura.MessageDialog
        {
            id: cancelPrintDialog
            title: "Cancel print?"
            text: "This will cancel the current print on the printer."
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted:
            {
                if (root.printer != null)
                {
                    root.printer.cancelPrint()
                }
            }
        }

        Cura.MessageDialog
        {
            id: powerOffDialog
            property string deviceName: ""
            title: "Turn off power device?"
            text: "A print is active. Turning this device off may stop the printer immediately."
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted:
            {
                if (root.printer != null && deviceName.length > 0)
                {
                    root.printer.setPowerDevice(deviceName, false)
                }
            }
        }

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
                Layout.minimumWidth: 400 * screenScaleFactor
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

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
                                    root.printer.refreshAll()
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
                            font: UM.Theme.getFont("default")
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

                            onSourceChanged:
                            {
                                if (visible && source !== "")
                                {
                                    start()
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
                Layout.preferredWidth: 410 * screenScaleFactor
                Layout.minimumWidth: 330 * screenScaleFactor
                Layout.fillHeight: true
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

                Flickable
                {
                    id: statusFlick
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    clip: true
                    contentWidth: width
                    contentHeight: statusContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: UM.ScrollBar { id: statusScrollbar }

                    ColumnLayout
                    {
                        id: statusContent
                        width: statusFlick.width - statusScrollbar.width - UM.Theme.getSize("default_margin").width
                        spacing: UM.Theme.getSize("default_margin").height

                        UM.Label
                        {
                            text: root.printer != null ? root.printer.name : "Moonraker"
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

                        UM.Label
                        {
                            visible: root.printer != null && root.printer.monitorMessage.length > 0
                            text: root.printer != null ? root.printer.monitorMessage : ""
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
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

                        RowLayout
                        {
                            visible: root.printer != null && (root.printer.canPausePrint || root.printer.canResumePrint || root.printer.canCancelPrint)
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").width / 2

                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.canPausePrint
                                text: "Pause"
                                onClicked: root.printer.pausePrint()
                            }

                            Cura.PrimaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.canResumePrint
                                text: "Resume"
                                onClicked: root.printer.resumePrint()
                            }

                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.canCancelPrint
                                text: "Cancel"
                                onClicked: cancelPrintDialog.open()
                            }
                        }

                        UM.Label
                        {
                            visible: root.printer != null && root.printer.actionStatus.length > 0
                            text: root.printer != null ? root.printer.actionStatus : ""
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
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

                            UM.Label { text: "Remaining"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.monitorEta : "—"; Layout.fillWidth: true }

                            UM.Label { text: "Finish"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.monitorFinish : "—"; Layout.fillWidth: true }

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

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.temperatureItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Temperatures"; font: UM.Theme.getFont("medium_bold") }

                            Repeater
                            {
                                model: root.printer != null ? root.printer.temperatureItems : []
                                RowLayout
                                {
                                    Layout.fillWidth: true
                                    UM.Label
                                    {
                                        text: modelData.name
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    UM.Label { text: modelData.detail }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.fanItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Fans"; font: UM.Theme.getFont("medium_bold") }

                            Repeater
                            {
                                model: root.printer != null ? root.printer.fanItems : []
                                RowLayout
                                {
                                    Layout.fillWidth: true
                                    UM.Label
                                    {
                                        text: modelData.name
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    UM.Label { text: modelData.detail }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.filamentSensorItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Filament sensors"; font: UM.Theme.getFont("medium_bold") }

                            Repeater
                            {
                                model: root.printer != null ? root.printer.filamentSensorItems : []
                                RowLayout
                                {
                                    Layout.fillWidth: true
                                    UM.Label
                                    {
                                        text: modelData.name
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    UM.Label { text: modelData.state }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.excludeObjectItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Objects"; font: UM.Theme.getFont("medium_bold") }

                            Repeater
                            {
                                model: root.printer != null ? root.printer.excludeObjectItems : []
                                RowLayout
                                {
                                    Layout.fillWidth: true
                                    UM.Label
                                    {
                                        text: modelData.name + (modelData.current ? "  · current" : "") + (modelData.excluded ? "  · excluded" : "")
                                        color: modelData.excluded ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Cura.SecondaryButton
                                    {
                                        visible: root.printer != null && root.printer.printActive && !modelData.excluded
                                        enabled: root.printer != null && !root.printer.actionBusy
                                        text: "Exclude"
                                        onClicked: root.printer.excludeObject(modelData.name)
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.powerDevices.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Power"; font: UM.Theme.getFont("medium_bold") }

                            Repeater
                            {
                                model: root.printer != null ? root.printer.powerDevices : []
                                RowLayout
                                {
                                    Layout.fillWidth: true
                                    UM.Label
                                    {
                                        text: modelData.name + "  · " + modelData.status
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Cura.SecondaryButton
                                    {
                                        enabled: root.printer != null && modelData.can_toggle && !root.printer.actionBusy
                                        text: modelData.status === "on" ? "Turn off" : "Turn on"
                                        onClicked:
                                        {
                                            if (modelData.status === "on" && root.printer.printActive)
                                            {
                                                powerOffDialog.deviceName = modelData.name
                                                powerOffDialog.open()
                                            }
                                            else
                                            {
                                                root.printer.setPowerDevice(modelData.name, modelData.status !== "on")
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: UM.Theme.getSize("default_lining").height
                            color: UM.Theme.getColor("lining")
                        }

                        UM.Label { text: "System"; font: UM.Theme.getFont("medium_bold") }

                        GridLayout
                        {
                            columns: 2
                            columnSpacing: UM.Theme.getSize("default_margin").width
                            rowSpacing: UM.Theme.getSize("default_margin").height / 2
                            Layout.fillWidth: true

                            UM.Label { text: "Klippy"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.klippyState : "—"; Layout.fillWidth: true }

                            UM.Label { text: "Host load"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.hostLoad : "—"; Layout.fillWidth: true }

                            UM.Label { text: "Memory free"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.memoryAvailable : "—"; Layout.fillWidth: true }

                            UM.Label { text: "CPU temp"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.cpuTemperature : "—"; Layout.fillWidth: true }

                            UM.Label { text: "Klipper"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label
                            {
                                text: root.printer != null ? root.printer.klipperVersion : "—"
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }

                            UM.Label { text: "Moonraker"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label
                            {
                                text: root.printer != null ? root.printer.moonrakerVersion : "—"
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                            }
                        }

                        UM.Label
                        {
                            text: root.printer != null ? "MCU  " + root.printer.mcuSummary : "MCU  —"
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }

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
}
