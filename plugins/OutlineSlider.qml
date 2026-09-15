import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

// A plugin-owned outline-style slider: the themed slider's rail
// rendered as a black slab in the inactive-window palette; this one
// draws a thin outlined track with the progress filled inside it in
// Cura's brand blue, and the blue-ringed handle Cura's own sliders
// use. All slider properties (from/to/stepSize/live/value…) pass
// straight through.
// The control owns EVERY interaction path (the author's ruling):
// groove clicks and drags, the overlay's handle drags, and keyboard
// nudges all funnel through the same two semantic signals —
// valueTuning fires on every user-driven change (the live preview),
// valueCommitted fires when a change is complete (the apply). A
// handle click without movement changes nothing and commits nothing;
// a click focuses the slider so the arrow keys nudge one step.
Slider {
    id: control
    implicitHeight: 28 * screenScaleFactor

    focusPolicy: Qt.StrongFocus
    // The repeater hosts set these to the row's object name and the
    // slider's role so the dashboard can re-grant focus to the RIGHT
    // slider after the submit's rebuild (one LED row holds five).
    property string controlObject: ""
    property string controlKind: ""
    property bool tuningActive: false
    readonly property bool interacting: pressed || tuningActive
    property bool handlePress: false
    property bool handleDragged: false
    property real valueBeforePress: 0
    signal valueTuning(real value)
    signal valueCommitted(real value)
    function selectedValue() {
        return Math.round(valueAt(position));
    }
    function pressIsOnHandle(mouseX) {
        var centre = leftPadding + visualPosition * availableWidth;
        return Math.abs(mouseX - centre) <= 10 * screenScaleFactor;
    }

    // The native groove path: a click/drag commits through onMoved
    // (live: false defers the value to the release, where pressed is
    // already false). A track CLICK's single move happens DURING the
    // press and the release fires no further onMoved — the commit
    // must come from the press-release edge or a click moves the
    // handle and never submits (the author's live report, 2026-09-15).
    property bool movedWhilePressed: false
    onMoved: {
        valueTuning(selectedValue());
        if (!pressed) {
            valueCommitted(selectedValue());
            movedWhilePressed = false;
        } else {
            movedWhilePressed = true;
        }
    }
    onPressedChanged: {
        if (!pressed && movedWhilePressed) {
            movedWhilePressed = false;
            valueCommitted(selectedValue());
        }
    }

    // The handle path: swallowed by the overlay so a press within the
    // handle's extent never jumps; the value is driven here instead.
    MouseArea {
        anchors.fill: parent
        onPressed: function (mouse) {
            control.forceActiveFocus();
            control.valueBeforePress = control.value;
            control.handleDragged = false;
            control.handlePress = control.pressIsOnHandle(mouse.x);
            control.tuningActive = control.handlePress;
            mouse.accepted = control.handlePress;
        }
        onPositionChanged: function (mouse) {
            if (!control.handlePress) {
                return;
            }
            var steps = Math.round((mouse.x - control.leftPadding) / Math.max(1, control.availableWidth) * (control.to - control.from));
            control.value = Math.max(control.from, Math.min(control.to, control.from + steps * control.stepSize));
            if (Math.abs(control.value - control.valueBeforePress) > 0.001) {
                control.handleDragged = true;
                control.valueTuning(control.selectedValue());
            }
        }
        onReleased: function (mouse) {
            if (!control.handlePress) {
                return;
            }
            control.handlePress = false;
            control.tuningActive = false;
            if (!control.handleDragged) {
                control.value = control.valueBeforePress;
            } else {
                control.valueCommitted(control.selectedValue());
            }
            mouse.accepted = true;
        }
    }

    // The keyboard path: one step per press, committed like a release
    // (the author's live report): the debounce holds the value until
    // the nudges are quiet, and tuningActive stays true for the whole
    // pending so the host surfaces freeze model-driven rewrites —
    // without the hold, the commit's publish rebuilt the fan/LED
    // repeaters mid-nudge and killed the focused delegate.
    Timer {
        id: keyDebounce
        interval: 250
        onTriggered: {
            control.valueCommitted(control.selectedValue());
            control.tuningActive = false;
        }
    }
    Keys.onUpPressed: {
        increase();
        control.tuningActive = true;
        control.valueTuning(control.selectedValue());
        keyDebounce.restart();
    }
    Keys.onDownPressed: {
        decrease();
        control.tuningActive = true;
        control.valueTuning(control.selectedValue());
        keyDebounce.restart();
    }
    Keys.onRightPressed: {
        increase();
        control.tuningActive = true;
        control.valueTuning(control.selectedValue());
        keyDebounce.restart();
    }
    Keys.onLeftPressed: {
        decrease();
        control.tuningActive = true;
        control.valueTuning(control.selectedValue());
        keyDebounce.restart();
    }

    background: Item {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 200 * screenScaleFactor
        implicitHeight: 24 * screenScaleFactor
        width: control.availableWidth
        height: 8 * screenScaleFactor

        Cura.RoundedRectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
            cornerSide: Cura.RoundedRectangle.Direction.All
        }
        Cura.RoundedRectangle {
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.margins: 2 * screenScaleFactor
            width: Math.max(0, control.visualPosition) * (parent.width - 4 * screenScaleFactor)
            // Disabled sliders grey out (the live report):
            // the stock dimming does not reach a custom-styled fill.
            color: control.enabled ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_disabled")
            radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
            cornerSide: Cura.RoundedRectangle.Direction.All
        }
    }

    handle: Cura.RoundedRectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 16 * screenScaleFactor
        implicitHeight: 16 * screenScaleFactor
        radius: 8 * screenScaleFactor
        cornerSide: Cura.RoundedRectangle.Direction.All
        color: UM.Theme.getColor("main_background")
        border.color: control.enabled ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_disabled")
        border.width: UM.Theme.getSize("default_lining").width
    }
}
