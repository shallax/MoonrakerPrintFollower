import QtQuick 2.15

Item {
    property url source: ""
    // Production NetworkMJPGImage (a Python QQuickPaintedItem) exposes the
    // current frame's dimensions; the monitor's fit-to-viewport bindings
    // read them, so the stub declares them or the bindings throw
    // ReferenceErrors in the capture engine.
    // A placeholder frame for capture renders: real dimensions let the
    // monitor's fit-to-viewport bindings lay out a visible stream area.
    property real imageWidth: 640
    property real imageHeight: 480
    function start() {
    }
    function stop() {
    }
    Rectangle {
        anchors.fill: parent
        color: "#dddddd"
        border.color: "#999999"
    }
}
