import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "../../Widgets"

// The extrusion cluster: the distance and speed ladders (the selected
// step keeps its primary face) and the extrude/retract pair. The gate
// is the model's jogEnabled and the refusal copy is the policy's; the
// section above keeps the shell and the readouts.
ColumnLayout {
    id: root
    Layout.fillWidth: true
    // The printer model the section passes: every model read inside
    // the cluster goes through this property.
    property var printerModel: null
    spacing: UM.Theme.getSize("default_margin").height / 2

    UM.Label {
        text: "Extrusion"
        font: UM.Theme.getFont("medium_bold")
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").width
        // The selected distance keeps its
        // highlight: the primary face shows
        // while it IS the selection (a live
        // report — the boxes never stayed
        // highlighted).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeDistance5Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeDistance5Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeDistance === 5
                text: "5"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(5)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 5 mm."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeDistance !== 5
                text: "5"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(5)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 5 mm."
                }
            }
        }
        // The selected distance keeps its
        // highlight: the primary face shows
        // while it IS the selection (a live
        // report — the boxes never stayed
        // highlighted).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeDistance10Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeDistance10Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeDistance === 10
                text: "10"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(10)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 10 mm."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeDistance !== 10
                text: "10"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(10)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 10 mm."
                }
            }
        }
        // The selected distance keeps its
        // highlight: the primary face shows
        // while it IS the selection (a live
        // report — the boxes never stayed
        // highlighted).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeDistance25Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeDistance25Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeDistance === 25
                text: "25"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(25)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 25 mm."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeDistance !== 25
                text: "25"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(25)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 25 mm."
                }
            }
        }
        // The selected distance keeps its
        // highlight: the primary face shows
        // while it IS the selection (a live
        // report — the boxes never stayed
        // highlighted).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeDistance75Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeDistance75Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeDistance === 75
                text: "75"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(75)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 75 mm."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeDistance !== 75
                text: "75"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(75)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 75 mm."
                }
            }
        }
        // The selected distance keeps its
        // highlight: the primary face shows
        // while it IS the selection (a live
        // report — the boxes never stayed
        // highlighted).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeDistance100Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeDistance100Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeDistance === 100
                text: "100"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(100)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 100 mm."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeDistance !== 100
                text: "100"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeDistance(100)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude distance: 100 mm."
                }
            }
        }
        UM.Label {
            // The unit rides the row, like the
            // speed row's "mm/s" (the
            // ruling) — the free-text length
            // box was dropped as unnecessary.
            text: "mm"
            color: UM.Theme.getColor("text_inactive")
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").width
        CentredSecondaryButton {
            Layout.fillWidth: true
            text: "Extrude"
            enabled: root.printerModel != null && root.printerModel.jogEnabled
            onClicked: root.printerModel.extrude(1)
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "Extrude the configured distance at the configured speed."
            }
        }
        CentredSecondaryButton {
            Layout.fillWidth: true
            text: "Retract"
            enabled: root.printerModel != null && root.printerModel.jogEnabled
            onClicked: root.printerModel.extrude(-1)
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "Retract the configured distance at the configured speed."
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").width
        UM.Label {
            text: "Speed"
            color: UM.Theme.getColor("text_inactive")
        }
        // Same highlight pattern as the
        // distance row (the live
        // report).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeSpeed60Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeSpeed60Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeSpeed === 60
                text: "1"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(60)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 1 mm/s."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 60
                text: "1"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(60)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 1 mm/s."
                }
            }
        }
        // Same highlight pattern as the
        // distance row (the live
        // report).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeSpeed120Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeSpeed120Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeSpeed === 120
                text: "2"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(120)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 2 mm/s."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 120
                text: "2"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(120)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 2 mm/s."
                }
            }
        }
        // Same highlight pattern as the
        // distance row (the live
        // report).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeSpeed300Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeSpeed300Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeSpeed === 300
                text: "5"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(300)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 5 mm/s."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 300
                text: "5"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(300)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 5 mm/s."
                }
            }
        }
        // Same highlight pattern as the
        // distance row (the live
        // report).
        Item {
            Layout.fillWidth: true
            implicitHeight: extrudeSpeed1500Primary.implicitHeight
            CentredPrimaryButton {
                id: extrudeSpeed1500Primary
                anchors.fill: parent
                visible: root.printerModel != null && root.printerModel.extrudeSpeed === 1500
                text: "25"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(1500)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 25 mm/s."
                }
            }
            CentredSecondaryButton {
                anchors.fill: parent
                visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 1500
                text: "25"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.setExtrudeSpeed(1500)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrusion speed: 25 mm/s."
                }
            }
        }
        UM.Label {
            text: "mm/s"
            color: UM.Theme.getColor("text_inactive")
        }
    }
}
