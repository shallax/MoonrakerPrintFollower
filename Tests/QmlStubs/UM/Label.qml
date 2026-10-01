import QtQuick 2.15

Item {
    property alias text: label.text
    property alias color: label.color
    property alias font: label.font
    property alias wrapMode: label.wrapMode
    property alias elide: label.elide
    property alias horizontalAlignment: label.horizontalAlignment
    property alias verticalAlignment: label.verticalAlignment
    implicitWidth: label.implicitWidth
    implicitHeight: label.implicitHeight
    Text {
        id: label
        anchors.fill: parent
        renderType: Text.QtRendering
    }
}
