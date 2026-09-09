import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The download+index+render progress for the Preview's load flow —
// the same contract as the Monitor's Improve-ETA bar: a determinate
// byte fraction while the file downloads, a sweeping segment while
// resolving/indexing, and the flipping hourglass throughout. The bar
// clips its children, and the sweep's position is a binding on the
// 0..1 phase (qualified through the bar's id: unqualified names do
// NOT resolve through the visual parent).
RowLayout {
    id: root
    property bool busy: false
    property real progress: -1
    property string phase: ""

    spacing: UM.Theme.getSize("narrow_margin").width

    // The busy gate lives on an INNER row: bindings on the ROOT
    // object's own visible do not track setProperty-driven changes
    // (engine-proven — the root stayed hidden while a child flipped).
    RowLayout {
        id: content
        objectName: "loadIndicatorContent"
        visible: root.busy
        spacing: root.spacing

        Item {
            width: 16 * screenScaleFactor
            height: 16 * screenScaleFactor
            UM.ColorImage {
                anchors.fill: parent
                source: Qt.resolvedUrl("Hourglass.svg")
                color: UM.Theme.getColor("text")
                // The hourglass flips and rests at each 180-degree stop
                // while the sand drains, then flips again.
                SequentialAnimation on rotation  {
                    running: root.busy
                    loops: Animation.Infinite
                    NumberAnimation {
                        from: 0
                        to: 180
                        duration: 350
                        easing.type: Easing.InOutCubic
                    }
                    PauseAnimation {
                        duration: 700
                    }
                    NumberAnimation {
                        from: 180
                        to: 360
                        duration: 350
                        easing.type: Easing.InOutCubic
                    }
                    PauseAnimation {
                        duration: 700
                    }
                }
            }
        }
        Item {
            id: indicatorBar
            Layout.fillWidth: true
            Layout.preferredHeight: 8 * screenScaleFactor
            clip: true
            property real sweepPhase: 0
            NumberAnimation on sweepPhase  {
                running: root.busy && root.progress < 0
                from: 0
                to: 1
                duration: 1100
                loops: Animation.Infinite
            }
            Cura.RoundedRectangle {
                anchors.fill: parent
                color: "transparent"
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("progressbar_radius").width
                cornerSide: Cura.RoundedRectangle.Direction.All
            }
            Cura.RoundedRectangle {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.margins: 1 * screenScaleFactor
                width: Math.max(0, Math.min(1, root.progress)) * (parent.width - 2 * screenScaleFactor)
                color: UM.Theme.getColor("primary")
                radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
                cornerSide: Cura.RoundedRectangle.Direction.All
                visible: root.progress >= 0
            }
            Cura.RoundedRectangle {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.margins: 1 * screenScaleFactor
                width: (parent.width - 2 * screenScaleFactor) / 3
                x: (1 - Math.abs(2 * indicatorBar.sweepPhase - 1)) * (parent.width - width) + screenScaleFactor
                color: UM.Theme.getColor("primary")
                radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
                cornerSide: Cura.RoundedRectangle.Direction.All
                visible: root.busy && root.progress < 0
            }
        }
        UM.Label {
            text: root.phase + (root.progress >= 0 ? " " + (root.progress * 100).toFixed(0) + "%" : "")
            color: UM.Theme.getColor("text_inactive")
            Layout.maximumWidth: 140 * screenScaleFactor
            elide: Text.ElideRight
        }
    }
}
