import QtQuick 2.15

Item {
    property string text: ""
    property int maximumLength: 32767
    property bool selectByMouse: false
    signal editingFinished
    property var validator: null
    property string placeholderText: ""
    implicitWidth: 200
    implicitHeight: 32
    Rectangle {
        anchors.fill: parent
        color: "#ffffff"
        border.color: "#b0b0b0"
    }
}
