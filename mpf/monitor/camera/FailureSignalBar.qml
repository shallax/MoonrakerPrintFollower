import QtQuick 2.15
import UM 1.5 as UM

Rectangle {
    id: root
    property int score: 0
    property bool waiting: false
    property bool lineOnly: false
    signal collapseRequested(int steps)
    // What the readout says while there is no score to show: the
    // first-analysis "Wait", or a stale analysis's own word.
    property string waitingText: "Wait"
    property color signalColor: UM.Theme.getColor("primary")

    implicitWidth: lineOnly ? 8 * screenScaleFactor : Math.ceil(Math.max(readoutMetrics.advanceWidth("0.00"), readoutMetrics.advanceWidth("1.00"), readoutMetrics.advanceWidth("Wait"), readoutMetrics.advanceWidth("Stale"))) + 8 * screenScaleFactor
    radius: 3 * screenScaleFactor
    color: UM.Theme.getColor("main_background")
    border.color: UM.Theme.getColor("lining")
    border.width: 1

    FontMetrics {
        id: readoutMetrics
        font: UM.Theme.getFont("small")
    }

    Item {
        id: signalReadout
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.lineOnly ? 0 : 16 * screenScaleFactor
        visible: !root.lineOnly
        UM.Label {
            anchors.centerIn: parent
            text: root.waiting ? root.waitingText : (root.score / 100).toFixed(2)
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text")
        }
    }
    Item {
        objectName: "failureSignalTrack"
        anchors.top: signalReadout.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: (root.lineOnly ? 1 : 4) * screenScaleFactor

        Repeater {
            model: 5
            Item {
                objectName: "failureSignalTick"
                visible: !root.lineOnly
                readonly property bool major: index % 2 === 0
                anchors.left: parent.left
                anchors.right: parent.right
                y: parent.height * (1 - index / 4)
                height: major ? 2 : 1
                Rectangle {
                    anchors.left: parent.left
                    width: parent.width * (major ? 1 : 0.25)
                    height: parent.height
                    color: major ? UM.Theme.getColor("border") : UM.Theme.getColor("lining")
                }
                Rectangle {
                    visible: !major
                    anchors.right: parent.right
                    width: parent.width * 0.25
                    height: parent.height
                    color: UM.Theme.getColor("lining")
                }
            }
        }
        Rectangle {
            objectName: "failureSignalMarker"
            visible: !root.waiting
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height * (1 - root.score / 100) - height / 2
            width: parent.width
            height: 3 * screenScaleFactor
            radius: height / 2
            color: root.signalColor
        }
    }
    MouseArea {
        id: collapseDrag
        objectName: "failureSignalDrag"
        anchors.fill: parent
        anchors.margins: root.lineOnly ? -6 * screenScaleFactor : 0
        preventStealing: true
        cursorShape: Qt.SizeHorCursor
        property real pressX: 0
        property int appliedSteps: 0
        property int initialMode: 0
        onPressed: function (mouse) {
            pressX = mapToItem(null, mouse.x, mouse.y).x;
            appliedSteps = 0;
            initialMode = root.lineOnly ? 1 : 0;
        }
        onPositionChanged: function (mouse) {
            if (!pressed)
                return;
            var distance = mapToItem(null, mouse.x, mouse.y).x - pressX;
            var steps = distance < -55 * screenScaleFactor ? 2 : distance < -12 * screenScaleFactor ? 1 : distance > 12 * screenScaleFactor ? -1 : 0;
            steps = Math.max(0, Math.min(2, initialMode + steps)) - initialMode;
            if (steps !== appliedSteps) {
                var change = steps - appliedSteps;
                appliedSteps = steps;
                root.collapseRequested(change);
            }
        }
    }
}
