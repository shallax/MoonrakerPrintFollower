import QtQuick 2.15
Item {
    property var model: null
    property bool picking: false
    property bool addingLight: false
    property bool addingRotor: false
    property bool selectingOpacity: false
    property bool paintingMaterial: false
    property bool slowRotation: false
    property bool animationAvailable: true
    function resetCamera() {}
    property int orbitCalls: 0
    property int panCalls: 0
    property int pickCalls: 0
    function orbit(dx, dy) { orbitCalls++; }
    function pan(dx, dy) { panCalls++; }
    function zoomBy(delta) {}
    function pick(x, y) { if (picking || addingLight) pickCalls++; }
}
