import QtQuick 2.15

Item {
    signal clicked
    property url iconSource: ""
    property color color: "#606060"
    property color hoverColor: "#202020"
    property string tooltip: ""
    MouseArea {
        anchors.fill: parent
        onClicked: parent.clicked()
    }
}
