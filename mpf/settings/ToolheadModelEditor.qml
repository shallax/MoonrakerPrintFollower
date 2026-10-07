import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import MoonrakerPrintFollower 1.0

RowLayout {
    id: root
    property var model: null
    spacing: UM.Theme.getSize("default_margin").width
    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: UM.Theme.getSize("thin_margin").height
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180 * screenScaleFactor
            color: UM.Theme.getColor("detail_background")
            border.color: UM.Theme.getColor("lining")
            clip: true
            ToolheadModelPreview {
                id: preview
                objectName: "toolheadModelPreview"
                anchors.fill: parent
                model: root.model
                Accessible.name: "Toolhead model preview. Right drag rotates, middle drag pans, scroll zooms; XYZ fields provide keyboard nozzle alignment."
            }
            MouseArea {
                objectName: "toolheadModelInteraction"
                anchors.fill: parent
                preventStealing: true
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
                property real lastX: 0
                property real lastY: 0
                cursorShape: preview.picking ? Qt.CrossCursor : (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                enabled: root.model && !root.model.busy
                onPressed: mouse => {
                    lastX = mouse.x;
                    lastY = mouse.y;
                    forceActiveFocus();
                }
                onPositionChanged: mouse => {
                    if (pressed) {
                        if (mouse.buttons & Qt.MiddleButton)
                            preview.pan(mouse.x - lastX, mouse.y - lastY);
                        else if (mouse.buttons & Qt.RightButton)
                            preview.orbit(mouse.x - lastX, mouse.y - lastY);
                        lastX = mouse.x;
                        lastY = mouse.y;
                    }
                }
                onClicked: mouse => {
                    if (mouse.button === Qt.LeftButton)
                        preview.pick(mouse.x, mouse.y);
                }
                onWheel: wheel => {
                    preview.zoomBy(wheel.angleDelta.y);
                    wheel.accepted = true;
                }
                Keys.onEscapePressed: event => {
                    if (preview.picking) {
                        preview.picking = false;
                        event.accepted = true;
                    } else {
                        event.accepted = false;
                    }
                }
            }
        }
        UM.Label {
            Layout.fillWidth: true
            text: preview.addingLight ? "Click a surface to place an outward-facing light." : preview.picking ? "Click a visible nozzle surface. Use XYZ to centre a hole. Escape cancels picking." : "Right drag: rotate · Middle drag: pan · Scroll: zoom · Orange marker: nozzle tip"
            wrapMode: Text.WordWrap
            color: UM.Theme.getColor("text_inactive")
        }
        Flow {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            Cura.SecondaryButton {
                objectName: "toolheadPickTip"
                text: preview.picking ? "Cancel picking" : "Pick nozzle tip"
                enabled: root.model && !root.model.busy
                onClicked: preview.picking = !preview.picking
            }
            Cura.SecondaryButton {
                text: "Reset view"
                onClicked: preview.resetCamera()
            }
            Cura.SecondaryButton {
                objectName: "toolheadAutomaticTip"
                text: "Reset automatic"
                enabled: root.model && !root.model.busy
                onClicked: {
                    preview.picking = false;
                    root.model.automatic();
                }
            }
        }
        UM.Label {
            text: (root.model && root.model.manual ? "Manual" : "Automatic") + " nozzle tip · model XYZ (mm)"
        }
        RowLayout {
            Layout.fillWidth: true
            enabled: root.model && !root.model.busy
            UM.Label {
                text: "X"
            }
            Cura.TextField {
                objectName: "toolheadTipX"
                Accessible.name: "Nozzle tip X in model millimetres"
                Layout.fillWidth: true
                maximumLength: 16
                text: root.model ? root.model.tipX : "0"
                onTextEdited: root.model.setTip(0, text)
            }
            UM.Label {
                text: "Y"
            }
            Cura.TextField {
                objectName: "toolheadTipY"
                Accessible.name: "Nozzle tip Y in model millimetres"
                Layout.fillWidth: true
                maximumLength: 16
                text: root.model ? root.model.tipY : "0"
                onTextEdited: root.model.setTip(1, text)
            }
            UM.Label {
                text: "Z"
            }
            Cura.TextField {
                objectName: "toolheadTipZ"
                Accessible.name: "Nozzle tip Z in model millimetres"
                Layout.fillWidth: true
                maximumLength: 16
                text: root.model ? root.model.tipZ : "0"
                onTextEdited: root.model.setTip(2, text)
            }
        }
        UM.Label {
            Layout.fillWidth: true
            height: 22 * screenScaleFactor
            text: root.model && !root.model.valid && !root.model.busy && !root.model.needsDownload ? "Enter finite XYZ values between −10,000 and 10,000 mm." : ""
            color: UM.Theme.getColor("error")
        }
    }
    ToolheadLightSettings {
        Layout.preferredWidth: 300 * screenScaleFactor
        Layout.fillHeight: true
        model: root.model
        preview: preview
    }
}
