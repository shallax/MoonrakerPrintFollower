import QtQuick 2.15
Item {
    property url source: ""
    property color color: "#000000"
    Image {
        anchors.fill: parent
        source: parent.source
        fillMode: Image.PreserveAspectFit
        visible: parent.source != ""
    }
}
