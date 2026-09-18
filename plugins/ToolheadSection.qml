import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The Toolhead section (4.3.0 extraction): the header, the jog pad,
// the extrusion controls, the endstops and the status rows moved out
// of the dashboard as one property-driven component.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    // The Position row's availability (the tuple rule: ANY axis
    // unavailable empties the whole row). ROOT-scoped: QML cannot
    // resolve a non-root ancestor's property from its descendants
    // (the engine's ReferenceError) — the monitor's own
    // root.etaAvailable pattern.
    readonly property bool positionRowAvailable: root.printerModel != null && root.printerModel.monitorPositionX !== "—" && root.printerModel.monitorPositionX !== "" && root.printerModel.monitorPositionY !== "—" && root.printerModel.monitorPositionY !== "" && root.printerModel.monitorPositionZ !== "—" && root.printerModel.monitorPositionZ !== ""

    // The homed-axes readout: each axis letter in its axis colour
    // (the live ruling — X red, Y green, Z blue).
    function homedAxesRichText(axes) {
        var colours = {
            "X": MoonrakerTheme.axisX,
            "Y": MoonrakerTheme.axisY,
            "Z": MoonrakerTheme.axisZ
        };
        var letters = axes.toUpperCase().split("");
        var parts = [];
        for (var i = 0; i < letters.length; ++i) {
            var letter = letters[i];
            if (colours[letter] !== undefined)
                parts.push("<font color=\"" + colours[letter] + "\">" + letter + "</font>");
            else
                parts.push(letter);
        }
        return parts.join(" ");
    }

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
                    text: root.printerModel != null && root.printerModel.homedAxes.length > 0 ? root.homedAxesRichText(root.printerModel.homedAxes) : "—"
                    textFormat: Text.RichText
                    // The rich-text label's implicit width read too
                    // narrow and each axis letter wrapped onto its
                    // own line (the live report); the explicit width
                    // stays TIGHT.
                    width: 56 * screenScaleFactor
                    wrapMode: Text.NoWrap
                    color: UM.Theme.getColor("text")
                }
            }
        }
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            Layout.fillWidth: true

            // The tuple rule (the live ruling): if ANY axis value is
            // unavailable, the whole Position row reads empty — the
            // label and the cells. A PERMANENT slot (the M117 /
            // Next-pause precedent): flipping the row's visibility
            // in a layout is the polish-loop pattern (the panel's
            // catch) — the cells empty themselves instead.
            UM.Label {
                text: positionRowAvailable ? "Position" : ""
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            // The collapsed readout's style (the live ruling): the
            // axes as separate fixed-width cells in their axis
            // colours — X red, Y green, Z blue — each able to hold
            // "N 888.88" without reflowing.
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").width
                UM.Label {
                    // "X 888.88" still sits, and the trio must fit
                    // the pane's minimum width without overflowing
                    // (the live report: 50 px per cell).
                    Layout.preferredWidth: 50 * screenScaleFactor
                    text: positionRowAvailable ? (root.printerModel != null ? root.printerModel.monitorPositionX : "") : ""
                    color: MoonrakerTheme.axisX
                    elide: Text.ElideRight
                }
                UM.Label {
                    Layout.preferredWidth: 50 * screenScaleFactor
                    text: positionRowAvailable ? (root.printerModel != null ? root.printerModel.monitorPositionY : "") : ""
                    color: MoonrakerTheme.axisY
                    elide: Text.ElideRight
                }
                UM.Label {
                    Layout.preferredWidth: 50 * screenScaleFactor
                    text: positionRowAvailable ? (root.printerModel != null ? root.printerModel.monitorPositionZ : "") : ""
                    color: MoonrakerTheme.axisZ
                    elide: Text.ElideRight
                }
            }
        }
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            Layout.fillWidth: true

            UM.Label {
                text: "Moves"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            // The abs/rel toggle on its own row under the Position
            // readout (the live ruling: "Moves:
            // <absolute|relative>" — the mode TEXT is the control,
            // never a separate button) — clicking the word switches
            // and sends the real G90/G91 through the command lane.
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
                        // reads, it must not act (a catch).
                        if (root.printerModel != null && root.printerModel.jogEnabled) {
                            root.printerModel.setPositionMode(root.printerModel.positionMode !== "Absolute");
                        }
                    }
                }
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

                    objectName: "moonrakerJogYPlus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("y", 1)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Move the toolhead towards the Y maximum."
                    }
                }
                Item {
                    Layout.fillWidth: true
                }
                PreviewSecondaryButton {

                    Layout.fillWidth: true

                    text: "← X"

                    objectName: "moonrakerJogXMinus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("x", -1)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Move the toolhead towards the X minimum."
                    }
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

                    objectName: "moonrakerJogXPlus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("x", 1)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Move the toolhead towards the X maximum."
                    }
                }
                Item {
                    Layout.fillWidth: true
                }
                PreviewSecondaryButton {

                    Layout.fillWidth: true

                    text: "↓ Y"

                    objectName: "moonrakerJogYMinus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("y", -1)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Move the toolhead towards the Y minimum."
                    }
                }
                Item {
                    Layout.fillWidth: true
                }
            }
            ColumnLayout {
                spacing: UM.Theme.getSize("thin_margin").height
                PreviewSecondaryButton {

                    text: "↑ Z"

                    objectName: "moonrakerJogZPlus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("z", 1)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Move the toolhead up."
                    }
                }
                PreviewSecondaryButton {

                    text: "↓ Z"

                    objectName: "moonrakerJogZMinus"

                    enabled: root.printerModel != null && root.printerModel.jogEnabled

                    onClicked: root.printerModel.jog("z", -1)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Move the toolhead down."
                    }
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
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.home("x")
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Home the X axis."
                }
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Home Y"
                objectName: "moonrakerHomeY"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.home("y")
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Home the Y axis."
                }
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Home Z"
                objectName: "moonrakerHomeZ"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.home("z")
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Home the Z axis."
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("thin_margin").width
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Centre toolhead"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.centerToolhead()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move X and Y to the build plate centre, 5 cm above the plate."
                }
            }
            Cura.SecondaryButton {
                text: "Z to 0"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.zToZero()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Move Z down to 0, the bed level after homing."
                }
            }
            Cura.SecondaryButton {
                text: "Motors off"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.motorsOff()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Disable the stepper motors so the toolhead can be moved by hand."
                }
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
            // while it IS the selection (a live
            // report — the boxes never stayed
            // highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance5Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance5Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 5
                    text: "5"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(5)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 5 mm."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 5
                    text: "5"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(5)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 5 mm."
                    }
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (a live
            // report — the boxes never stayed
            // highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance10Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance10Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 10
                    text: "10"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(10)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 10 mm."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 10
                    text: "10"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(10)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 10 mm."
                    }
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (a live
            // report — the boxes never stayed
            // highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance25Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance25Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 25
                    text: "25"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(25)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 25 mm."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 25
                    text: "25"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(25)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 25 mm."
                    }
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (a live
            // report — the boxes never stayed
            // highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance75Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance75Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 75
                    text: "75"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(75)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 75 mm."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 75
                    text: "75"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(75)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 75 mm."
                    }
                }
            }
            // The selected distance keeps its
            // highlight: the primary face shows
            // while it IS the selection (a live
            // report — the boxes never stayed
            // highlighted).
            Item {
                Layout.fillWidth: true
                implicitHeight: extrudeDistance100Primary.implicitHeight
                Cura.PrimaryButton {
                    id: extrudeDistance100Primary
                    anchors.fill: parent
                    visible: root.printerModel != null && root.printerModel.extrudeDistance === 100
                    text: "100"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(100)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 100 mm."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeDistance !== 100
                    text: "100"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeDistance(100)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrude distance: 100 mm."
                    }
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
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.extrude(1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Extrude the configured distance at the configured speed."
                }
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Retract"
                enabled: root.printerModel != null && root.printerModel.jogEnabled
                onClicked: root.printerModel.extrude(-1)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Retract the configured distance at the configured speed."
                }
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
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(60)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 1 mm/s."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 60
                    text: "1"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(60)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 1 mm/s."
                    }
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
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(120)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 2 mm/s."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 120
                    text: "2"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(120)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 2 mm/s."
                    }
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
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(300)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 5 mm/s."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 300
                    text: "5"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(300)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 5 mm/s."
                    }
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
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(1500)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 25 mm/s."
                    }
                }
                Cura.SecondaryButton {
                    anchors.fill: parent
                    visible: root.printerModel == null || root.printerModel.extrudeSpeed !== 1500
                    text: "25"
                    enabled: root.printerModel != null && root.printerModel.jogEnabled
                    onClicked: root.printerModel.setExtrudeSpeed(1500)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Extrusion speed: 25 mm/s."
                    }
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
                HoverHandler {
                    id: tooltipHover1
                }
                UM.ToolTip {
                    visible: tooltipHover1.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    // Short value in the row, full
                    // sentence in the tooltip (the ruling).
                    text: root.printerModel != null ? root.printerModel.jogReasonDetail : ""
                }
            }

            UM.Label {
                // The jog feedback row RESERVES its
                // space at all times and fades in
                // only when it has something to say
                // — an idle "Jog —" is noise (the
                // live report), but hiding the row
                // would be a reflow (a rule), so
                // opacity, never visibility.
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
