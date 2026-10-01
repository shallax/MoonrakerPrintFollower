import QtQuick 2.15

Item {
    property alias text: label.text
    property bool checked: false
    property string tooltip: ""
    implicitWidth: 120
    implicitHeight: 32
    signal clicked
    Rectangle {
        anchors.fill: parent
        color: "#e8e8e8"
        border.color: "#b0b0b0"
        Text {
            id: label
            anchors.centerIn: parent
            font.pixelSize: 14
        }
    }
    MouseArea {
        anchors.fill: parent
        onClicked: parent.clicked()
    }
}
