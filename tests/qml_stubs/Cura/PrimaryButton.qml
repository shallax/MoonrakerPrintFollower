import QtQuick 2.15

Item {
    id: base
    signal clicked
    property alias text: label.text
    property string tooltip: ""
    property bool fixedWidthMode: false
    property color color: "#e8e8e8"
    property color textColor: "#202020"
    property color textHoverColor: "#202020"
    property color textDisabledColor: "#7a7a7a"
    property alias hovered: mouse.containsMouse
    implicitWidth: Math.max(48, label.implicitWidth + 20)
    implicitHeight: 36
    Rectangle {
        anchors.fill: parent
        color: base.enabled ? (mouse.containsMouse ? "#dcdcdc" : "#e8e8e8") : "#d0d0d0"
        radius: 4
        border.color: "#b0b0b0"
        Text {
            id: label
            anchors.centerIn: parent
            font.pixelSize: 14
            color: "#202020"
        }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        onClicked: base.clicked()
    }
}
