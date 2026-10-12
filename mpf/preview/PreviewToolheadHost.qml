import QtQuick 2.15
import UM 1.5 as UM

// Siblings in Cura's viewport, obtained by its QML context IDs. Bind their
// actual geometry: no guessed header or perspective-button dimensions.
Item {
    id: host
    objectName: "previewToolheadHost"
    anchors.fill: parent
    z: 9600
    property Item orientationControls: null
    property Item viewport: null
    property Item jobSpecs: null
    property Item objectSelector: null
    property Item stageMenu: null
    property bool previewActive: false
    readonly property real gap: UM.Theme.getSize("default_margin").width
    readonly property real dockBottom: objectSelector && objectSelector.visible ? objectSelector.y : jobSpecs && jobSpecs.visible ? jobSpecs.y : orientationControls ? orientationControls.y : 0
    readonly property real dockTop: Math.max(viewport ? viewport.y : 0, stageMenu && stageMenu.visible ? stageMenu.y + stageMenu.height : 0) + gap
    visible: previewActive && orientationControls !== null && viewport !== null && dockBottom - dockTop > 112 * screenScaleFactor + gap

    PreviewToolheadPane {
        id: pane
        x: host.orientationControls ? host.orientationControls.x : 0
        y: host.dockTop
        availableHeight: Math.max(0, host.dockBottom - host.dockTop - host.gap)
    }
}
