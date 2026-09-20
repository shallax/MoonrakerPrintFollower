import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Print Follower section (4.6.0): the progress mini out of the
// monitor — the current layer path and the toolhead dot in the
// reserved slot; a click opens the pop-over with the full
// prev/current/next view and the legend toggles. Separate from the
// exclude picker by the live ruling: two controls, no face toggle.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    signal popOverToggleRequested(string name)

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Print Follower"
        sectionId: "plateprogress"
        sectionIcon: ""
        sectionIconUrl: Qt.resolvedUrl("Follower.svg")
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["plateprogress"] !== false
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height

        // NO-REFLOW: the reserved slot fades the follower in and out;
        // the placeholder overlays the same slot.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 90 * screenScaleFactor

            PlateProgressFace {
                id: progressMini
                anchors.fill: parent
                compact: true
                printerModel: root.printerModel
                progress: root.printerModel != null ? ({
                        "available": root.printerModel.plateProgressAvailable,
                        "reason": root.printerModel.plateProgressReason,
                        "layers": root.printerModel.plateLayers,
                        "split": root.printerModel.plateSplit,
                        "anchor": root.printerModel.plateProgressAnchor,
                        "method": "motion index"
                    }) : null
                dot: root.printerModel != null ? root.printerModel.plateDot : null
                // The same persisted global view settings the
                // popover face reads — the mini and the popover never
                // disagree (the live ruling).
                showPrevious: root.printerModel != null ? root.printerModel.followerShowPrevious : true
                showNext: root.printerModel != null ? root.printerModel.followerShowNext : true
                showBase: root.printerModel != null ? root.printerModel.followerShowBase : true
                showTravels: root.printerModel != null ? root.printerModel.followerShowTravels : false
                lineScale: root.printerModel != null ? root.printerModel.followerLineScale : 1.0
                opacity: root.printerModel != null && root.printerModel.plateProgressAvailable ? 1 : 0
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.popOverToggleRequested("plateprogress")
                }
            }

            UM.Label {
                anchors.centerIn: parent
                opacity: root.printerModel == null || !root.printerModel.plateProgressAvailable ? 1 : 0
                text: "The follower appears once the print's index is built — open the pop-over to build it."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("small")
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                width: parent.width * 0.9
            }
        }
    }
}
