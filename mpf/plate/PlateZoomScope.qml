import QtQuick 2.15
import UM 1.5 as UM
import "PlateViewPolicy.js" as ViewPolicy

// The zoom scope (the live request): a cockpit-HUD scale beside the
// canvas — a RESERVED readout strip for the percentage, and below it the
// graduated bar: a complete line at every 100%, a 50%-in edge pair at
// the half marks and a 25%-in edge pair at the quarters. The marker
// rides the zoom and doubles as the drag handle; the bottom is the 100%
// fit.
//
// The camera itself belongs to the face: this reports the scale the
// handle was dragged to and nothing else, so the scope can never become
// a second writer of the view transform.
Rectangle {
    id: root

    objectName: "moonrakerPlateZoomScope"
    // The scale shown, and the cap the track and the graduations share
    // with the wheel.
    property real viewScale: 1.0
    property real maxScale: ViewPolicy.MAX_SCALE
    // Docked it hugs the right edge of the frame; parked it slides fully
    // out of view (the live request — it starts invisible). The frame is
    // handed in rather than read off `parent`: the engine gate loads
    // every document standalone, where a root item has no parent.
    property bool docked: false
    property Item frame: null
    signal scaleRequested(real target)

    // The scope's graduation labels: every 25% of scale from the fit to
    // the cap, log-positioned along the bar.
    readonly property var graduations: ViewPolicy.graduations(root.maxScale, 0.25)

    // Wide enough for the "800%" label (the live report: the percentage
    // overflowed the scope's bounds).
    width: 38 * screenScaleFactor
    // The full canvas height (the live request: the bar spans the canvas
    // edge to edge now the reset control lives in the host's checkbox
    // row).
    height: root.frame != null ? root.frame.height : 0
    y: 0
    x: root.frame == null ? 0 : (root.docked ? root.frame.width - width - UM.Theme.getSize("narrow_margin").width : root.frame.width + 6 * screenScaleFactor)
    Behavior on x {
        NumberAnimation {
            duration: 180
            easing.type: Easing.OutCubic
        }
    }
    radius: 3 * screenScaleFactor
    color: UM.Theme.getColor("main_background")
    opacity: 0.85
    border.color: UM.Theme.getColor("lining")
    border.width: 1
    Item {
        // The reserved readout: the label never shares space with the
        // bar (the live report: the text overlapped the marker).
        id: scopeReadout
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 16 * screenScaleFactor
        UM.Label {
            anchors.centerIn: parent
            text: Math.round(root.viewScale * 100) + "%"
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text")
        }
    }
    Item {
        id: scopeBar
        anchors.top: scopeReadout.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 4 * screenScaleFactor
        Repeater {
            model: root.graduations
            Item {
                readonly property real fraction: ViewPolicy.trackFraction(modelData, root.maxScale)
                readonly property bool major: Math.round(modelData * 100) % 100 === 0
                readonly property bool half: Math.round(modelData * 100) % 50 === 0
                anchors.left: parent.left
                anchors.right: parent.right
                y: parent.height - fraction * parent.height
                height: major ? 2 : 1
                Rectangle {
                    // The left edge's reach: the full width at the
                    // majors, 50% in at the halves, 25% in at the
                    // quarters (the cockpit-HUD request).
                    anchors.left: parent.left
                    width: parent.width * (major ? 1.0 : half ? 0.5 : 0.25)
                    height: parent.height
                    color: major ? UM.Theme.getColor("border") : UM.Theme.getColor("lining")
                }
                Rectangle {
                    // The mirrored right edge's reach (the halves and
                    // quarters draw as an edge pair).
                    visible: !major
                    anchors.right: parent.right
                    width: parent.width * (half ? 0.5 : 0.25)
                    height: parent.height
                    color: UM.Theme.getColor("lining")
                }
            }
        }
        Rectangle {
            // The marker: log-rides the zoom — and doubles as the drag
            // handle.
            id: scopeMarker
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height - ViewPolicy.trackFraction(root.viewScale, root.maxScale) * parent.height - height / 2
            width: parent.width
            height: 3 * screenScaleFactor
            radius: height / 2
            color: UM.Theme.getColor("primary")
        }
        MouseArea {
            // The draggable scale: the pointer's track position is the
            // zoom — bottom is the 100% fit (centred, no pan), the top
            // is the cap.
            anchors.fill: parent
            onPressed: function (mouse) {
                root.scaleRequested(ViewPolicy.scaleFromTrack(mouse.y, height, root.maxScale));
            }
            onPositionChanged: function (mouse) {
                if (pressed) {
                    root.scaleRequested(ViewPolicy.scaleFromTrack(mouse.y, height, root.maxScale));
                }
            }
        }
    }
}
