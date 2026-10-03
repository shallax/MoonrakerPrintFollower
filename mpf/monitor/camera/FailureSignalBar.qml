import QtQuick 2.15
import UM 1.5 as UM

Rectangle {
    id: root
    property int score: 0
    property bool waiting: false
    // What the readout says while there is no score to show: the
    // first-analysis "Wait", or a stale analysis's own word.
    property string waitingText: "Wait"
    property color signalColor: UM.Theme.getColor("primary")

    radius: 3 * screenScaleFactor
    color: UM.Theme.getColor("main_background")
    border.color: UM.Theme.getColor("lining")
    border.width: 1

    Item {
        id: signalReadout
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 16 * screenScaleFactor
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
        anchors.margins: 4 * screenScaleFactor

        Repeater {
            model: 5
            Item {
                objectName: "failureSignalTick"
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
}
