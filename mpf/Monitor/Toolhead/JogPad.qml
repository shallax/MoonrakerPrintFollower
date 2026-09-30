import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../Widgets"

// The manual-motion pad: the move distance and the compass that jogs
// X/Y, with the Z pair beside it. Every control here reads the model's
// own jogEnabled — the section above keeps the shell, the readouts and
// the homing rows.
ColumnLayout {
    id: root
    Layout.fillWidth: true
    // The printer model the section passes: every model read inside
    // the pad goes through this property.
    property var printerModel: null
    // The steps the distance combo offers; the persisted selection is
    // read back from the model.
    readonly property var jogPresets: [0.1, 0.5, 1, 5, 10, 25, 50, 100, 125]
    spacing: UM.Theme.getSize("default_margin").height / 2

    RowLayout {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").width
        UM.Label {
            text: "Move distance"
            color: UM.Theme.getColor("text_inactive")
        }
        Cura.ComboBox {
            id: jogDistanceSelector
            Layout.fillWidth: true
            model: root.jogPresets
            currentIndex: root.jogPresets.indexOf(root.printerModel != null ? root.printerModel.jogDistance : 25)
            onActivated: function (index) {
                if (root.printerModel != null) {
                    root.printerModel.setJogDistance(root.jogPresets[index]);
                }
            }
        }
        Cura.TextField {
            id: jogDistanceField
            Layout.fillWidth: true
            text: root.printerModel != null ? root.printerModel.jogDistance.toString() : ""
            onEditingFinished: {
                var value = parseFloat(jogDistanceField.text);
                if (root.printerModel != null) {
                    root.printerModel.setJogDistance(value);
                    jogDistanceField.text = root.printerModel.jogDistance.toString();
                }
            }
        }
        UM.Label {
            text: "mm"
            color: UM.Theme.getColor("text_inactive")
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").width
        GridLayout {
            Layout.fillWidth: true
            columns: 3
            columnSpacing: UM.Theme.getSize("thin_margin").width
            rowSpacing: UM.Theme.getSize("thin_margin").height
            Item {
                Layout.fillWidth: true
            }
            PreviewSecondaryButton {

                Layout.fillWidth: true

                text: "↑ Y"

                objectName: "moonrakerJogYPlus"

                enabled: root.printerModel != null && root.printerModel.jogEnabled

                onClicked: root.printerModel.jog("y", 1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move the toolhead towards the Y maximum."
                }
            }
            Item {
                Layout.fillWidth: true
            }
            PreviewSecondaryButton {

                Layout.fillWidth: true

                text: "← X"

                objectName: "moonrakerJogXMinus"

                enabled: root.printerModel != null && root.printerModel.jogEnabled

                onClicked: root.printerModel.jog("x", -1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move the toolhead towards the X minimum."
                }
            }
            // The compass centre is deliberately
            // empty (the old Home-all button used
            // to live here).
            Item {
                Layout.fillWidth: true
            }
            PreviewSecondaryButton {

                Layout.fillWidth: true

                text: "→ X"

                objectName: "moonrakerJogXPlus"

                enabled: root.printerModel != null && root.printerModel.jogEnabled

                onClicked: root.printerModel.jog("x", 1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move the toolhead towards the X maximum."
                }
            }
            Item {
                Layout.fillWidth: true
            }
            PreviewSecondaryButton {

                Layout.fillWidth: true

                text: "↓ Y"

                objectName: "moonrakerJogYMinus"

                enabled: root.printerModel != null && root.printerModel.jogEnabled

                onClicked: root.printerModel.jog("y", -1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move the toolhead towards the Y minimum."
                }
            }
            Item {
                Layout.fillWidth: true
            }
        }
        ColumnLayout {
            spacing: UM.Theme.getSize("thin_margin").height
            PreviewSecondaryButton {

                text: "↑ Z"

                objectName: "moonrakerJogZPlus"

                enabled: root.printerModel != null && root.printerModel.jogEnabled

                onClicked: root.printerModel.jog("z", 1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move the toolhead up."
                }
            }
            PreviewSecondaryButton {

                text: "↓ Z"

                objectName: "moonrakerJogZMinus"

                enabled: root.printerModel != null && root.printerModel.jogEnabled

                onClicked: root.printerModel.jog("z", -1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move the toolhead down."
                }
            }
        }
    }
}
