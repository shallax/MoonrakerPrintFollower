import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Toolhead section (4.3.0 extraction): the header, the jog pad,
// the extrusion controls, the endstops and the status rows moved out
// of the dashboard as one property-driven component.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Toolhead"
        sectionId: "toolhead"
        sectionIcon: "Nozzle"
    }
    ColumnLayout {
        id: toolheadSection
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel != null && root.printerModel.sectionExpandedMap["toolhead"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height / 2
        readonly property var jogPresets: [0.1, 0.5, 1, 5, 10, 25, 50, 100, 125]

        // The readout leads the section: current state
        // first, controls below. Two rows on purpose —
        // the one-line readout wrapped at narrow widths
        // and made the content jump. Elide instead of
        // wrap so the section height never changes.
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            Layout.fillWidth: true

            UM.Label {
                text: "Homed"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            Row {
                Layout.fillWidth: true
                spacing: 4 * screenScaleFactor
                UM.Label {
                    text: root.printerModel != null && root.printerModel.homedAxes.length > 0 ? root.printerModel.homedAxes.toUpperCase().split('').join(' ') + "  · " : "—"
                    color: UM.Theme.getColor("text")
                }
                // The abs/rel toggle (the
                // live ruling: the mode TEXT is the
                // control, never a separate button)
                // — clicking the word switches and
                // sends the real G90/G91 through the
                // command lane.
                UM.Label {
                    text: root.printerModel != null ? root.printerModel.positionMode : "Absolute"
                    color: root.printerModel != null && root.printerModel.jogEnabled ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: root.printerModel != null && root.printerModel.jogEnabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            // The click itself must obey the
                            // SAME gate as the styling: while
                            // the controls are locked the word
                            // reads, it must not act (the
                            // author's catch).
                            if (root.printerModel != null && root.printerModel.jogEnabled) {
                                root.printerModel.setPositionMode(root.printerModel.positionMode !== "Absolute");
                            }
                        }
                    }
                }
                UM.Label {
                    text: " moves"
                    color: UM.Theme.getColor("text")
                }
            }
        }
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            Layout.fillWidth: true

            UM.Label {
                text: "Position"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                // The grey-label / black-value
                // readout pattern (the
                // ruling, after the MCUs section),
                // with an honest emdash when there
                // is no value.
                Layout.fillWidth: true
                text: root.printerModel != null ? root.printerModel.monitorPosition : "—"
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            UM.Label {
                text: "Move distance"
                color: UM.Theme.getColor("text_inactive")
            }
            Cura.ComboBox {
                id: jogDistanceSelector
                Layout.fillWidth: true
                model: toolheadSection.jogPresets
                currentIndex: toolheadSection.jogPresets.indexOf(root.printerModel != null ? root.printerModel.jogDistance : 25)
                onActivated: function (index) {
                    if (root.printerModel != null) {
                        root.printerModel.setJogDistance(toolheadSection.jogPresets[index]);
                    }
                }
            }
            Cura.TextField {
                id: jogDistanceField
                Layout.fillWidth: true
                text: root.printerModel != null ? root.printerModel.jogDistance.toString() : ""
                onEditingFinished: {
                    var value = parseFloat(jogDistanceField.text);
                    if (root.printerModel != null) {
                        root.printerModel.setJogDistance(value);
                        jogDistanceField.text = root.printerModel.jogDistance.toString();
                    }
                }
            }
            UM.Label {
                text: "mm"
                color: UM.Theme.getColor("text_inactive")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            GridLayout {
                Layout.fillWidth: true
                columns: 3
                columnSpacing: UM.Theme.getSize("thin_margin").width
                rowSpacing: UM.Theme.getSize("thin_margin").height
                Item {
                    Layout.fillWidth: true
                }
                PreviewSecondaryButton {

                    Layout.fillWidth: true

                    text: "↑ Y"

                    tooltip: "Move the toolhead towards the Y maximum."
                    objectName: "moonrakerJogYPlus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("y", 1)
                }
                Item {
                    Layout.fillWidth: true
                }
                PreviewSecondaryButton {

                    Layout.fillWidth: true

                    text: "← X"

                    tooltip: "Move the toolhead towards the X minimum."
                    objectName: "moonrakerJogXMinus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("x", -1)
                }
                // The compass centre is deliberately
                // empty (the old Home-all button used
                // to live here).
                Item {
                    Layout.fillWidth: true
                }
                PreviewSecondaryButton {

                    Layout.fillWidth: true

                    text: "→ X"

                    tooltip: "Move the toolhead towards the X maximum."
                    objectName: "moonrakerJogXPlus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("x", 1)
                }
                Item {
                    Layout.fillWidth: true
                }
                PreviewSecondaryButton {

                    Layout.fillWidth: true

                    text: "↓ Y"

                    tooltip: "Move the toolhead towards the Y minimum."
                    objectName: "moonrakerJogYMinus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("y", -1)
                }
                Item {
                    Layout.fillWidth: true
                }
            }
            ColumnLayout {
                spacing: UM.Theme.getSize("thin_margin").height
                PreviewSecondaryButton {

                    text: "↑ Z"

                    tooltip: "Move the toolhead up."
                    objectName: "moonrakerJogZPlus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("z", 1)
                }
                PreviewSecondaryButton {

                    text: "↓ Z"

                    tooltip: "Move the toolhead down."
                    objectName: "moonrakerJogZMinus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("z", -1)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Home X"
                objectName: "moonrakerHomeX"
                tooltip: "Home the X axis."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.home("x")
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Home Y"
                objectName: "moonrakerHomeY"
                tooltip: "Home the Y axis."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.home("y")
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Home Z"
                objectName: "moonrakerHomeZ"
                tooltip: "Home the Z axis."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.home("z")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Centre toolhead"
                tooltip: "Move X and Y to the build plate centre, 5 cm above the plate."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.centerToolhead()
            }
            Cura.SecondaryButton {
                text: "Z to 0"
                tooltip: "Move Z down to 0, the bed level after homing."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.zToZero()
            }
            Cura.SecondaryButton {
                text: "Motors off"
                tooltip: "Disable the stepper motors so the toolhead can be moved by hand."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.motorsOff()
            }
        }

        UM.Label {
            text: "Extrusion"
            font: UM.Theme.getFont("medium_bold")
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (the
            // author's live report — the boxes
            // never stayed highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance5Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance5Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 5
                    text: "5"
                    tooltip: "Extrude distance: 5 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(5)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 5
                    text: "5"
                    tooltip: "Extrude distance: 5 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(5)
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (the
            // author's live report — the boxes
            // never stayed highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance10Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance10Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 10
                    text: "10"
                    tooltip: "Extrude distance: 10 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(10)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 10
                    text: "10"
                    tooltip: "Extrude distance: 10 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(10)
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (the
            // author's live report — the boxes
            // never stayed highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance25Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance25Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 25
                    text: "25"
                    tooltip: "Extrude distance: 25 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(25)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 25
                    text: "25"
                    tooltip: "Extrude distance: 25 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(25)
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (the
            // author's live report — the boxes
            // never stayed highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance75Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance75Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 75
                    text: "75"
                    tooltip: "Extrude distance: 75 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(75)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 75
                    text: "75"
                    tooltip: "Extrude distance: 75 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(75)
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (the
            // author's live report — the boxes
            // never stayed highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance100Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance100Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 100
                    text: "100"
                    tooltip: "Extrude distance: 100 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(100)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 100
                    text: "100"
                    tooltip: "Extrude distance: 100 mm."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(100)
                }
            }
            UM.Label {
                // The unit rides the row, like the
                // speed row's "mm/s" (the
                // ruling) — the free-text length
                // box was dropped as unnecessary.
                text: "mm"
                color: UM.Theme.getColor("text_inactive")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Extrude"
                tooltip: "Extrude the configured distance at the configured speed."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.extrude(1)
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Retract"
                tooltip: "Retract the configured distance at the configured speed."
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.extrude(-1)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            UM.Label {
                text: "Speed"
                color: UM.Theme.getColor("text_inactive")
            }
            // Same highlight pattern as the
            // distance row (the live
            // report).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeSpeed60Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeSpeed60Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeSpeed === 60
                    text: "1"
                    tooltip: "Extrusion speed: 1 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(60)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 60
                    text: "1"
                    tooltip: "Extrusion speed: 1 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(60)
                }
            }
            // Same highlight pattern as the
            // distance row (the live
            // report).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeSpeed120Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeSpeed120Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeSpeed === 120
                    text: "2"
                    tooltip: "Extrusion speed: 2 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(120)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 120
                    text: "2"
                    tooltip: "Extrusion speed: 2 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(120)
                }
            }
            // Same highlight pattern as the
            // distance row (the live
            // report).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeSpeed300Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeSpeed300Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeSpeed === 300
                    text: "5"
                    tooltip: "Extrusion speed: 5 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(300)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 300
                    text: "5"
                    tooltip: "Extrusion speed: 5 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(300)
                }
            }
            // Same highlight pattern as the
            // distance row (the live
            // report).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeSpeed1500Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeSpeed1500Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeSpeed === 1500
                    text: "25"
                    tooltip: "Extrusion speed: 25 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(1500)
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 1500
                    text: "25"
                    tooltip: "Extrusion speed: 25 mm/s."
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(1500)
                }
            }
            UM.Label {
                text: "mm/s"
                color: UM.Theme.getColor("text_inactive")
            }
        }

        // The endstop readout sits BELOW the jog pad
        // (the panel): the chips arrive at the first
        // homing of a session, and appearing above
        // the pad shifted it under the pointer — the
        // original hazard family. It is a tri-state:
        // values only exist after the first homing,
        // so an empty list says so instead of
        // reading as a bug; TRIGGERED axes get the
        // normal text colour, open axes stay muted.
        UM.Label {
            // Pin names like STEPPER_X are not
            // self-evidently endstops — the block
            // gets its own bold title, like the
            // MCUs section (the ruling).
            // The title stays always: it labels the
            // chips too.
            text: "Endstops"
            font: UM.Theme.getFont("medium_bold")
            color: UM.Theme.getColor("text")
        }
        UM.Label {
            Layout.fillWidth: true
            height: 36 * screenScaleFactor
            // The summary line yields to the chips
            // once they exist — they ARE the
            // readout, and a bare emdash beside
            // them read as an error (the
            // live report). The chips sit below the
            // jog pad, so this follows their
            // accepted appearance carve-out.
            visible: root.printerModel == null || root.printerModel.endstopItems.length === 0
            text: root.printerModel != null ? (root.printerModel.endstopSummary.length > 0 ? root.printerModel.endstopSummary : "—") : "—"
            color: UM.Theme.getColor("text")
            elide: Text.ElideRight
            wrapMode: Text.NoWrap
        }
        Flow {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width
            Repeater {
                model: root.printerModel != null ? root.printerModel.endstopItems : []
                UM.Label {
                    text: modelData.name + ": " + modelData.state
                    color: modelData.triggered ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                }
            }
        }

        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            UM.Label {
                text: "Status"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                // NO-REFLOW RULE: permanent slot,
                // fixed single-line height — the
                // text changes, never the layout.
                // Grey label, black value (the MCUs
                // pattern), an honest emdash when
                // nothing applies. The caption is
                // the policy's short form (4.2.0):
                // the reason when disabled, the
                // pause-first warning, the paused
                // note — one derivation, and the
                // full sentence lives in the
                // tooltip: elide must never hide
                // the safety clause (the panel).
                objectName: "toolheadStatusCaption"
                height: 36 * screenScaleFactor
                text: root.printerModel != null && root.printerModel.jogReason !== "" ? root.printerModel.jogReason : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
                UM.TooltipArea {
                    anchors.fill: parent
                    // Short value in the row, full
                    // sentence in the tooltip (the
                    // author's ruling).
                    text: root.printerModel != null ? root.printerModel.jogReasonDetail : ""
                    acceptedButtons: Qt.NoButton
                }
            }

            UM.Label {
                // The jog feedback row RESERVES its
                // space at all times and fades in
                // only when it has something to say
                // — an idle "Jog —" is noise (the
                // author's live report), but hiding
                // the row would be a reflow (the
                // author's rule), so opacity, never
                // visibility.
                opacity: root.printerModel != null && root.printerModel.jogStatus.length > 0 ? 1 : 0
                text: "Jog"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                // The jog feedback keeps its own row:
                // the pause-timeout and resumed-drop
                // errors must never hide behind the
                // state hint (the panel's top
                // finding — queued moves were
                // cancelled silently).
                opacity: root.printerModel != null && root.printerModel.jogStatus.length > 0 ? 1 : 0
                height: 36 * screenScaleFactor
                text: root.printerModel != null ? root.printerModel.jogStatus : ""
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
            }
        }
    }
}
