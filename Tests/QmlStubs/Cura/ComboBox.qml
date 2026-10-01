import QtQuick 2.15

Item {
    property var model: []
    property int currentIndex: -1
    property bool editable: false
    property string editText: ""
    signal activated(int index)
    signal currentTextChanged
    function find(text) {
        for (var i = 0; i < model.length; ++i)
            if (model[i] === text)
                return i;
        return -1;
    }
    implicitWidth: 180
    implicitHeight: 32
    Rectangle {
        anchors.fill: parent
        color: "#ffffff"
        border.color: "#b0b0b0"
        radius: 3
        Text {
            anchors.left: parent.left
            anchors.leftMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: currentIndex >= 0 ? String(model[currentIndex]) : editText
            font.pixelSize: 14
            color: "#202020"
        }
    }
    MouseArea {
        anchors.fill: parent
        onClicked: parent.activated((parent.currentIndex + 1) % Math.max(1, parent.model.length))
    }
}
