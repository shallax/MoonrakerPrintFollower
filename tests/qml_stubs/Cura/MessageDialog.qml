import QtQuick 2.15

Item {
    property string title: ""
    property string text: ""
    property int standardButtons: 0
    signal accepted
    signal rejected
    function open() {
    }
}
