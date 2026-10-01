import QtQuick 2.15
import UM 1.5 as UM
import "../../resources/theme"

// The printer-status strip's readout: the bottom-to-top title with the
// connection dot, and the rotated row of readouts below it. The pane
// frames it against the header; the row is handed out so the host's fit
// pass can measure it where it lands.
Item {
    id: root

    property bool statusCollapsed: false
    property var printerModel: null
    property string connectionDotColour: ""
    property bool etaAvailable: false
    property bool finishAvailable: false
    property bool layerCountAvailable: false
    property bool flowAvailable: false

    readonly property alias readoutRow: statusReadoutRow

    visible: root.statusCollapsed
    width: statusCollapsedTitle.implicitHeight
    height: statusCollapsedTitle.implicitWidth + 24 * screenScaleFactor
    UM.Label {
        id: statusCollapsedTitle
        text: "Printer status"
        font: UM.Theme.getFont("medium_bold")
        color: UM.Theme.getColor("text_inactive")
        rotation: 90
        anchors.centerIn: parent
        anchors.verticalCenterOffset: 12 * screenScaleFactor
    }
    Rectangle {
        // The dot stays visible while the pane is
        // collapsed too — leading the title, in its
        // own band at the top.
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 3 * screenScaleFactor
        width: 10 * screenScaleFactor
        height: 10 * screenScaleFactor
        radius: 5 * screenScaleFactor
        color: connectionDotColour
    }

    Item {
        id: statusCollapsedBarsBox
        visible: root.statusCollapsed
        clip: true
        anchors.top: parent.bottom
        // The standard margin: the readout's text must
        // sit LEVEL with the controls pane's readout (the
        // live report).
        anchors.topMargin: 2 * UM.Theme.getSize("default_margin").height
        anchors.horizontalCenter: parent.horizontalCenter
        // The box hugs the content: its height tracks
        // the row's implicit width, so the centred row
        // fills it and the strip starts at the margin
        // under the title (the live report). 18 is the
        // label line height.
        width: 18 * screenScaleFactor
        height: statusReadoutRow.implicitWidth
        // ONE rotated flat row of explicit children —
        // the structure the engine lays out.
        // Each bar is its own pair: glyph, label, then
        // the TRACK — whose 60 px span lies ALONG the
        // row's main axis, so the rotation makes it run
        // along the strip (vertical) with the 4 px
        // thickness across. The fill grows from the
        // label end along the span.
        Row {
            id: statusReadoutRow
            anchors.centerIn: parent
            spacing: 2 * screenScaleFactor
            rotation: 90
            // The fit hides through OPACITY, never
            // the visibility flag: the survivors keep
            // their places (the live report).
            // The ETA leads the strip (the live ruling):
            // duration and finish clock, with the preview
            // pane's own glyphs — the hourglass then the
            // clock.
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                // Unavailable values do not render — the
                // value and its glyph both hide (the
                // live ruling).
                visible: root.etaAvailable
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Hourglass.svg")
                HoverHandler {
                    id: tooltipHover4
                }
                UM.ToolTip {
                    visible: tooltipHover4.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    // The improve-Eta mirror names the
                    // action (the panel's catch): the
                    // strip's glyph is not the button.
                    text: "Improve the estimate — download and index this print's G-code without loading it into the preview."
                }
            }
            UM.Label {
                objectName: "statusCollapsedReadoutLabel"
                property bool fitHidden: false
                visible: root.etaAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printerModel != null ? root.printerModel.monitorEta : "—"
                // The longest ETA form must clear the slot
                // or the value wraps — the same live-report
                // width the finish clock's slot carries.
                width: 84 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
                HoverHandler {
                    id: tooltipHover5
                }
                UM.ToolTip {
                    visible: tooltipHover5.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Improve the estimate — download and index this print's G-code without loading it into the preview."
                }
            }
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.finishAvailable
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Clock.svg")
            }
            UM.Label {
                objectName: "statusCollapsedReadoutLabel"
                property bool fitHidden: false
                visible: root.finishAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printerModel != null ? root.printerModel.monitorFinish : "—"
                // The finish reads day-first on a print
                // crossing midnight, and that form is the
                // widest the strip holds: the slot must
                // clear it or the value wraps (the live
                // report).
                width: 84 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                // The layer count (the live ruling): after
                // the ETA, before the print progress, with
                // the preview card's layer glyph.
                visible: root.layerCountAvailable
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Layer.svg")
            }
            UM.Label {
                objectName: "statusCollapsedReadoutLabel"
                property bool fitHidden: false
                visible: root.layerCountAvailable
                opacity: fitHidden ? 0 : 1
                // Implicit width (the live ruling: the
                // layer info may reflow — it changes
                // slowly — so the gap to the print bar
                // stays tight while "888 / 888" still
                // fits).
                text: root.printerModel != null ? root.printerModel.monitorLayer : "—"
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }

            Item {
                // The margin between the layer count and
                // the progress group (the live ruling) —
                // the stacked fills themselves stay
                // touching.
                visible: root.printerModel != null && root.printerModel.printActive
                width: 8 * screenScaleFactor
                height: 16 * screenScaleFactor
            }
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.printerModel != null && root.printerModel.printActive
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Progress.svg")
            }
            UM.Label {
                objectName: "statusCollapsedReadoutLabel"
                property bool fitHidden: false
                visible: root.printerModel != null && root.printerModel.printActive
                opacity: fitHidden ? 0 : 1
                text: "Progress"
                width: 60 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
            }
            Rectangle {
                id: progressTrack
                property bool fitHidden: false
                visible: root.printerModel != null && root.printerModel.printActive
                opacity: fitHidden ? 0 : 1
                width: 60 * screenScaleFactor
                // THE STACKED BAR (the live ruling): the
                // print fill is the BOTTOM half and the
                // layer fill the TOP half, touching at
                // the centre line — no gap. Without layer
                // info the print fill takes the whole
                // height. The transparent body with the
                // 2 px text outline frames the extent, so
                // the fills read against the work
                // remaining.
                height: 18 * screenScaleFactor
                color: "transparent"
                border.width: 2 * screenScaleFactor
                border.color: UM.Theme.getColor("text")
                Rectangle {
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    // The three fills share the height in
                    // thirds when the pause is scheduled,
                    // halves without one, and the print
                    // takes the whole height without
                    // layer info (the live ruling).
                    height: parent.height * (root.printerModel != null && root.printerModel.monitorLayerProgress >= 0 ? (root.printerModel.nextPauseFraction >= 0 ? 1 / 3 : 0.5) : 1.0)
                    // monitorProgress is a PERCENTAGE
                    // (0..100) while the clamp read it as
                    // a fraction — anything past 1%
                    // pegged the bar full (the live
                    // report). The layer value is
                    // already 0..1.
                    width: parent.width * Math.max(0, Math.min(1, root.printerModel != null ? root.printerModel.monitorProgress / 100 : 0))
                    color: UM.Theme.getColor("primary")
                }
                Rectangle {
                    // The next scheduled pause's fill (the
                    // live ruling): the MIDDLE of the
                    // stack, the mesh's neon orange — NOT
                    // RENDERED while no pause lies ahead
                    // (the gate, not a zero width).
                    objectName: "statusNextPauseFill"
                    visible: root.printerModel != null && root.printerModel.nextPauseFraction >= 0
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    height: parent.height / 3
                    width: parent.width * Math.max(0, Math.min(1, root.printerModel != null ? root.printerModel.nextPauseFraction : 0))
                    color: MoonrakerTheme.neonOrange
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    height: parent.height * (root.printerModel != null && root.printerModel.nextPauseFraction >= 0 ? 1 / 3 : 0.5)
                    width: parent.width * Math.max(0, Math.min(1, root.printerModel != null ? root.printerModel.monitorLayerProgress : 0))
                    color: UM.Theme.getColor("primary")
                }
            }
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.flowAvailable
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Flow.svg")
            }
            UM.Label {
                // The flow pair's own name (the harness
                // rule): the OTHER strip labels share
                // statusCollapsedReadoutLabel, and the
                // standby scenario proves the available
                // flow renders while the unavailable
                // groups hide.
                objectName: "statusCollapsedFlowLabel"
                property bool fitHidden: false
                visible: root.flowAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printerModel != null ? root.printerModel.monitorFlowRate : ""
                // "888.8 mm^3/s" must sit comfortably
                // (the live ruling).
                width: 110 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }
        }
    }
}
