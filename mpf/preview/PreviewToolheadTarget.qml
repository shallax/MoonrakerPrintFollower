import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: root
    property string axis: ""
    property alias text: input.text
    implicitWidth: 60 * screenScaleFactor
    implicitHeight: column.implicitHeight
    Column {
        id: column
        width: parent.width
        spacing: 2 * screenScaleFactor
        UM.Label {
            objectName: root.objectName + "Axis"
            text: root.axis
            font: UM.Theme.getFont("small")
        }
        Cura.TextField {
            id: input
            objectName: root.objectName + "Input"
            width: parent.width
            height: 30 * screenScaleFactor
            placeholderText: "—"
            Accessible.name: "Absolute G-code " + root.axis + " target in millimetres; leave blank to keep unchanged"
        }
    }
}
