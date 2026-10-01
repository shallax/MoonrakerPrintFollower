import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "PreviewColours.js" as PreviewColours
import "../resources/theme"

ColumnLayout {
    id: root
    objectName: "moonrakerFollowerColourControls"
    property var printerModel: null
    property var face: null
    property var scheme: face != null ? face.colourScheme : ({
            mode: 1
        })
    property int mode: scheme.mode === undefined ? 1 : scheme.mode
    property var limits: face != null ? face.colourRanges[PreviewColours.key(mode)] || [0, 0] : [0, 0]
    property string units: mode === 2 ? "mm/s" : (mode === 5 ? "mm³/s" : "mm")
    spacing: UM.Theme.getSize("thin_margin").height
    RowLayout {
        Layout.fillWidth: true
        UM.Label {
            text: "Colour scheme"
        }
        Cura.ComboBox {
            objectName: "moonrakerFollowerColourMode"
            Layout.preferredWidth: 210 * screenScaleFactor
            model: ["Material Colour", "Line type", "Speed", "Layer thickness", "Line thickness", "Flow rate"]
            currentIndex: root.mode
            onActivated: function (index) {
                if (root.printerModel != null)
                    root.printerModel.setFollowerColourMode(index);
            }
        }
        Item {
            Layout.fillWidth: true
        }
    }
    Flow {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").width
        UM.Label {
            text: "Travels:"
        }
        Repeater {
            model: [
                {
                    name: "TRAVEL",
                    label: "Non retracted"
                },
                {
                    name: "TRAVEL_RETRACTING",
                    label: "Retracting"
                },
                {
                    name: "TRAVEL_RETRACTED",
                    label: "Retracted"
                },
                {
                    name: "TRAVEL_PRIMING",
                    label: "Priming"
                }
            ]
            delegate: Row {
                spacing: 3 * screenScaleFactor
                Rectangle {
                    width: 12 * screenScaleFactor
                    height: 1 * screenScaleFactor
                    color: root.face != null ? root.face.classColour(modelData.name) : MoonrakerTheme.plateTravel
                    anchors.verticalCenter: parent.verticalCenter
                }
                UM.Label {
                    text: modelData.label
                }
            }
        }
        Row {
            spacing: 3 * screenScaleFactor
            UM.Label {
                text: "Layer:"
            }
            Rectangle {
                width: 12 * screenScaleFactor
                height: 2 * screenScaleFactor
                color: MoonrakerTheme.seriesDefault
                opacity: 0.55
                anchors.verticalCenter: parent.verticalCenter
            }
            UM.Label {
                text: "Ghost"
            }
        }
    }
    Item {
        // Hidden keys still determine the reserved height. Switching modes
        // never reflows the canvas; a larger material tool list may wrap.
        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(materialKey.implicitHeight, gradientUnitsMetric.implicitHeight, featureKey.implicitHeight)
        Layout.minimumHeight: Layout.preferredHeight
        Layout.maximumHeight: Layout.preferredHeight
        UM.Label {
            id: gradientUnitsMetric
            visible: false
            // Superscript flow-rate units can be taller than plain mm/s.
            // Reserve those font metrics in every mode, including Line type.
            text: "0.00 mm³/s"
        }
        Flow {
            id: materialKey
            anchors.left: parent.left
            anchors.right: parent.right
            visible: root.mode === 0
            spacing: UM.Theme.getSize("thin_margin").width
            UM.Label {
                text: "Line type:"
            }
            Repeater {
                model: root.scheme.materials || []
                delegate: Row {
                    spacing: 3 * screenScaleFactor
                    Rectangle {
                        width: 12 * screenScaleFactor
                        height: 3 * screenScaleFactor
                        color: modelData
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    UM.Label {
                        text: "Tool " + (index + 1)
                    }
                }
            }
        }
        Flow {
            id: featureKey
            anchors.left: parent.left
            anchors.right: parent.right
            visible: root.mode === 1
            spacing: UM.Theme.getSize("thin_margin").width
            UM.Label {
                text: "Line type:"
            }
            Row {
                spacing: 2 * screenScaleFactor
                Rectangle {
                    width: 10 * screenScaleFactor
                    height: 2 * screenScaleFactor
                    color: root.face != null ? root.face.classColour("WALL-OUTER") : MoonrakerTheme.seriesDefault
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
                    color: root.face != null ? root.face.classColour("WALL-INNER") : MoonrakerTheme.seriesDefault
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
                    color: root.face != null ? root.face.classColour("SKIN") : MoonrakerTheme.seriesDefault
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
                    color: root.face != null ? root.face.classColour("FILL") : MoonrakerTheme.seriesDefault
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
                    color: root.face != null ? root.face.classColour("SUPPORT") : MoonrakerTheme.seriesDefault
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
                    color: root.face != null ? root.face.classColour("SKIRT") : MoonrakerTheme.seriesDefault
                    anchors.verticalCenter: parent.verticalCenter
                }
                UM.Label {
                    text: "Skirt"
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
        RowLayout {
            id: gradientKey
            anchors.left: parent.left
            anchors.right: parent.right
            visible: root.mode >= 2
            UM.Label {
                text: Number(root.limits[0]).toFixed(2) + " " + root.units
            }
            Row {
                objectName: "moonrakerFollowerColourGradient"
                Layout.fillWidth: true
                Layout.preferredHeight: 8 * screenScaleFactor
                Repeater {
                    model: 64
                    delegate: Rectangle {
                        width: parent.width / 64
                        height: parent.height
                        color: PreviewColours.gradient(root.mode, root.limits[0] + (root.limits[1] - root.limits[0]) * index / 63, root.limits)
                    }
                }
            }
            UM.Label {
                text: Number(root.limits[1]).toFixed(2) + " " + root.units
            }
        }
    }
}
