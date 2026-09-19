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
    // The stream chip's bandwidth readout (CameraPane binds it):
    // the stub declares it so the production binding resolves.
    property real recentBytesPerSec: 0
    // The renderer's trace gate (CameraPane binds it): the stub
    // declares it so the production binding resolves.
    property bool traceEnabled: false
    // The ownership counters: the camera start/stop lifecycle tests
    // count every call and every source assignment — Cura's real
    // start() is destructive, so the count IS the contract.
    property int startCount: 0
    property int stopCount: 0
    property int sourceSetCount: 0
    onSourceChanged: sourceSetCount += 1
    function start() {
        startCount += 1;
    }
    function stop() {
        stopCount += 1;
    }
    Rectangle {
        anchors.fill: parent
        color: "#dddddd"
        border.color: "#999999"
    }
}
