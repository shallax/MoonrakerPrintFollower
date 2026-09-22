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
    // The decode throttle the pane's FPS control writes, and the rate
    // the chip reports: the stub declares both so the production
    // bindings resolve (the throttle's own contracts are measured on
    // the real renderer in test_moonraker_mjpg.py).
    property real targetFps: 0
    property real recentDisplayedFPS: 0
    // The ownership counters: the camera start/stop lifecycle tests
    // count every call and every source assignment — Cura's real
    // start() is destructive, so the count IS the contract.
    property int startCount: 0
    property int stopCount: 0
    property int sourceSetCount: 0
    property int clearCount: 0
    onSourceChanged: sourceSetCount += 1
    function start() {
        startCount += 1;
    }
    function stop() {
        stopCount += 1;
    }
    // The blank (the live request): production blanks the painted frame
    // and re-announces its size, so a consumer that latched imageWidth
    // re-reads it. Without the zero here the stub can never express the
    // stream off/on that killed the pane's gesture surface.
    function clearFrame() {
        clearCount += 1;
        imageWidth = 0;
        imageHeight = 0;
    }
    Rectangle {
        anchors.fill: parent
        color: "#dddddd"
        border.color: "#999999"
    }
}
