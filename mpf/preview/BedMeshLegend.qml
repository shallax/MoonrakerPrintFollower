import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import "../bedmesh"
import "../widgets"

// The bed-mesh display section: the shared range filter, the z-max
// exaggeration and the show/hide toggle. The window and the scale
// belong to the model; the sliders and their labels are fed
// IMPERATIVELY from the published values, because bindings on the
// dynamically created card's setProperty-fed values go stale
// (engine-proven), and every adjustment leaves as a signal.
Column {
    id: root
    property bool bedMeshAvailable: false
    property bool bedMeshVisible: true
    property string bedMeshRangeText: ""
    property string bedMeshMinimumText: ""
    property string bedMeshMaximumText: ""
    property real bedMeshMinimum: 0
    property real bedMeshMaximum: 0
    property real bedMeshThresholdLow: 0
    property real bedMeshThresholdHigh: 0
    property real bedMeshExaggeration: 20
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    signal bedMeshVisibilityRequested(bool visible)
    signal bedMeshThresholdsRequested(real low, real high)
    signal bedMeshExaggerationRequested(real scale)

    // The setProperty-fed mesh values drive the sliders imperatively
    // (bindings on dynamically created cards go stale — engine-proven).
    onBedMeshMinimumChanged: meshRangeSlider.minimum = root.bedMeshMinimum
    onBedMeshMaximumChanged: meshRangeSlider.maximum = root.bedMeshMaximum
    onBedMeshThresholdLowChanged: meshRangeSlider.low = root.bedMeshThresholdLow
    onBedMeshThresholdHighChanged: meshRangeSlider.high = root.bedMeshThresholdHigh
    onBedMeshExaggerationChanged: {
        exaggerationSlider.value = root.bedMeshExaggeration;
        exaggerationValueLabel.text = "×" + Math.round(root.bedMeshExaggeration);
    }

    Column {
        // The legend collapses when the mesh is hidden (the
        // 2026-09-16 ruling): the card reflows instead of
        // keeping a faded gap.
        visible: root.bedMeshAvailable && root.bedMeshVisible
        width: parent.width
        spacing: 2 * screenScaleFactor

        BedMeshRangeSlider {
            id: meshRangeSlider
            width: parent.width
            enabled: root.bedMeshAvailable
            onWindowAdjusted: root.bedMeshThresholdsRequested(low, high)
        }

        Row {
            width: parent.width
            spacing: root.buttonSpacing
            UM.Label {
                id: scaleLabel
                height: exaggerationSlider.implicitHeight
                text: "Scale z-max"
                // The label is primary content (the 2026-09-16
                // ruling): full text colour, not inactive grey.
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
                verticalAlignment: Text.AlignVCenter
            }
            Slider {
                id: exaggerationSlider
                width: parent.width - scaleLabel.width - exaggerationValueLabel.width - 2 * parent.spacing
                from: 0
                to: 1000
                stepSize: 1
                // The locked slider behaviours (the
                // ruling): a press within the handle's extent
                // of the current value is a no-op, and a click
                // focuses the slider so the arrow keys nudge.
                focusPolicy: Qt.StrongFocus
                property bool handlePress: false
                property bool handleDragged: false
                property real valueBeforePress: 0
                function pressIsOnHandle(mouseX) {
                    // The handle's own extent: the painted LEFT
                    // edge minus the width correction — the window
                    // centres on the handle's middle and spans its
                    // full width plus the margin (the reviewer's
                    // finding: one side of the grab handle moved
                    // the slider, the other never grabbed).
                    var leftEdge = leftPadding + visualPosition * (availableWidth - handle.width);
                    var centre = leftEdge + handle.width / 2;
                    return Math.abs(mouseX - centre) <= handle.width / 2 + 2 * screenScaleFactor;
                }
                MouseArea {
                    anchors.fill: parent
                    onPressed: function (mouse) {
                        parent.forceActiveFocus();
                        parent.valueBeforePress = parent.value;
                        parent.handleDragged = false;
                        parent.handlePress = parent.pressIsOnHandle(mouse.x);
                        mouse.accepted = parent.handlePress;
                    }
                    onPositionChanged: function (mouse) {
                        if (!parent.handlePress) {
                            return;
                        }
                        var steps = Math.round((mouse.x - parent.leftPadding) / Math.max(1, parent.availableWidth) * (parent.to - parent.from));
                        parent.value = Math.max(parent.from, Math.min(parent.to, parent.from + steps * parent.stepSize));
                        if (Math.abs(parent.value - parent.valueBeforePress) > 0.001) {
                            parent.handleDragged = true;
                        }
                    }
                    onReleased: function (mouse) {
                        if (!parent.handlePress) {
                            return;
                        }
                        parent.handlePress = false;
                        if (!parent.handleDragged) {
                            parent.value = parent.valueBeforePress;
                        }
                        mouse.accepted = true;
                    }
                }
                Keys.onUpPressed: {
                    increase();
                    // The apply must not steal the keyboard
                    // path (the reviewer's finding).
                    forceActiveFocus();
                }
                Keys.onDownPressed: {
                    decrease();
                    forceActiveFocus();
                }
                Keys.onRightPressed: {
                    increase();
                    forceActiveFocus();
                }
                Keys.onLeftPressed: {
                    decrease();
                    forceActiveFocus();
                }
                onValueChanged: {
                    exaggerationValueLabel.text = "×" + Math.round(value);
                    root.bedMeshExaggerationRequested(value);
                }
            }
            UM.Label {
                id: exaggerationValueLabel
                width: 44 * screenScaleFactor
                height: exaggerationSlider.implicitHeight
                text: "×" + Math.round(root.bedMeshExaggeration)
                horizontalAlignment: Text.AlignRight
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
                verticalAlignment: Text.AlignVCenter
            }
        }

        Row {
            width: parent.width
            UM.Label {
                width: parent.width / 2
                text: "Low " + root.bedMeshMinimumText
                // Primary content (the 2026-09-16 ruling):
                // full text colour, not inactive grey.
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
            }
            UM.Label {
                width: parent.width / 2
                text: "High " + root.bedMeshMaximumText
                horizontalAlignment: Text.AlignRight
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
            }
        }

        UM.Label {
            width: parent.width
            text: "Neon orange outline = the probed mesh bounds; outside = the boundary values, continued as Klipper clamps them"
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("default_italic")
            wrapMode: Text.WordWrap
        }
    }

    PreviewSecondaryButton {
        id: bedMeshButton
        // The bottom of the card, after the scheduling hint
        // (the 2026-09-16 ruling) — the mesh controls above
        // it, the toggle last. NO-REFLOW RULE: never hidden —
        // it disables when the loaded job has no mesh.
        width: parent.width
        height: UM.Theme.getSize("action_button").height
        enabled: root.bedMeshAvailable
        text: root.bedMeshVisible ? "Hide bed mesh" : "Show bed mesh"
        onClicked: root.bedMeshVisibilityRequested(!root.bedMeshVisible)
        UM.ToolTip {
            visible: parent.hovered
            targetPoint: Qt.point(parent.width / 2, 0)
            x: 0
            y: parent.height + UM.Theme.getSize("default_margin").height
            width: UM.Theme.getSize("tooltip").width
            text: "Show the active Klipper bed mesh as a coloured 3D surface on Cura's build plate" + (root.bedMeshRangeText.length > 0 ? " (" + root.bedMeshRangeText + ")." : ".")
        }
    }
}
