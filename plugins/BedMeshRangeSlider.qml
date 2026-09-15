import QtQuick 2.15
import UM 1.5 as UM

// The heightmap range filter (the author's request): a dual-ended
// rainbow slider shared by the Information pop-over and the Preview
// card legend — one window drives both surfaces, so the two stay
// synchronised. The groove is the same five-stop blue-to-red scale
// the mesh surfaces use; outside the window the bar greys out to
// match the out-of-window cells. Dragging a handle narrows the
// window, dragging BETWEEN the handles moves it, and a click on the
// grey groove jumps the nearest handle there. A handle click without
// movement changes nothing (the locked slider ruling).
Item {
    id: root
    // Inert; the harness asserts the rendered control's presence.
    objectName: "moonrakerBedMeshRangeSlider"

    property real minimum: 0
    property real maximum: 0
    property real low: 0
    property real high: 0
    // `enabled` is the inherited Item property (Qt 6) — declaring it
    // again triggered the engine's member-override warning.
    property string outOfWindowColor: "#8a8f98"
    property real outOfWindowAlpha: 0.90  // a desaturating wash, not a cover
    // The two handles track these during a drag; the host-bound
    // low/high stay untouched so the host's bindings survive, and a
    // release re-syncs to whatever the model clamped.
    property real _low: 0
    property real _high: 0
    property real handleWidth: 5 * screenScaleFactor
    property real handleHeight: 18 * screenScaleFactor
    property real handleHit: 10 * screenScaleFactor  // the forgiving grab zone
    property real grooveHeight: 8 * screenScaleFactor
    property real minWindowFraction: 0.005  // the narrowest selectable window
    // NOT named windowChanged: Qt 6's Item owns that notify signal.
    signal windowAdjusted(real low, real high)

    height: 22 * screenScaleFactor

    function span() {
        return root.maximum - root.minimum;
    }
    function minWindow() {
        return Math.max(0.0005, root.span() * root.minWindowFraction);
    }
    function usable() {
        return Math.max(1, width - root.handleWidth);
    }
    function valueAt(x) {
        // Handle centres span the full width; the endpoints read
        // minimum/maximum exactly.
        var v = root.minimum + (x - root.handleWidth / 2) / root.usable() * root.span();
        return Math.max(root.minimum, Math.min(root.maximum, v));
    }
    function centre(value) {
        if (root.span() <= 0)
            return root.handleWidth / 2;
        var t = (value - root.minimum) / root.span();
        return root.handleWidth / 2 + t * root.usable();
    }
    function syncInternal() {
        root._low = root.low;
        root._high = root.high;
    }
    onMinimumChanged: syncInternal()
    onMaximumChanged: syncInternal()
    onLowChanged: {
        if (!dragArea.pressed)
            syncInternal();
    }
    onHighChanged: {
        if (!dragArea.pressed)
            syncInternal();
    }
    onEnabledChanged: syncInternal()
    Component.onCompleted: syncInternal()

    // The locked slider ruling's keyboard half: a click focuses the
    // bar and the arrow keys nudge — left/right the low handle,
    // up/down the high one, one minimum window per press.
    focus: true
    Keys.onLeftPressed: {
        root._low = Math.max(root.minimum, Math.min(root._low - root.minWindow(), root._high - root.minWindow()));
        root.windowAdjusted(root._low, root._high);
    }
    Keys.onRightPressed: {
        root._low = Math.max(root.minimum, Math.min(root._low + root.minWindow(), root._high - root.minWindow()));
        root.windowAdjusted(root._low, root._high);
    }
    Keys.onDownPressed: {
        root._high = Math.min(root.maximum, Math.max(root._high - root.minWindow(), root._low + root.minWindow()));
        root.windowAdjusted(root._low, root._high);
    }
    Keys.onUpPressed: {
        root._high = Math.min(root.maximum, Math.max(root._high + root.minWindow(), root._low + root.minWindow()));
        root.windowAdjusted(root._low, root._high);
    }

    Rectangle {
        // The full-scale rainbow groove.
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.right: parent.right
        height: root.grooveHeight
        radius: 2 * screenScaleFactor
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop {
                position: 0.00
                color: "#1a47f2"
            }
            GradientStop {
                position: 0.25
                color: "#00b8ff"
            }
            GradientStop {
                position: 0.50
                color: "#33db61"
            }
            GradientStop {
                position: 0.75
                color: "#ffd11f"
            }
            GradientStop {
                position: 1.00
                color: "#eb291f"
            }
        }
    }

    // The bar DESATURATES outside the window (the author's request):
    // a translucent grey wash lets the rainbow show through washed
    // out, so the full scale stays readable while only the section
    // between the handles reads at full colour.
    Rectangle {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        width: Math.max(0, root.centre(root._low) - root.handleWidth / 2)
        height: root.grooveHeight
        color: root.outOfWindowColor
        opacity: root.outOfWindowAlpha
    }
    Rectangle {
        id: highSideGrey
        anchors.verticalCenter: parent.verticalCenter
        x: root.centre(root._high) + root.handleWidth / 2
        width: Math.max(0, root.width - x)
        height: root.grooveHeight
        color: root.outOfWindowColor
        opacity: root.outOfWindowAlpha
    }

    Rectangle {
        id: lowHandle
        x: root.centre(root._low) - root.handleWidth / 2
        width: root.handleWidth
        height: root.handleHeight
        anchors.verticalCenter: parent.verticalCenter
        radius: 1 * screenScaleFactor
        color: UM.Theme.getColor("text")
        border.width: 1
        border.color: UM.Theme.getColor("lining")
    }
    Rectangle {
        id: highHandle
        x: root.centre(root._high) - root.handleWidth / 2
        width: root.handleWidth
        height: root.handleHeight
        anchors.verticalCenter: parent.verticalCenter
        radius: 1 * screenScaleFactor
        color: UM.Theme.getColor("text")
        border.width: 1
        border.color: UM.Theme.getColor("lining")
    }

    MouseArea {
        id: dragArea
        anchors.fill: parent
        enabled: root.enabled
        cursorShape: Qt.SizeHorCursor
        property int mode: 0  // 1 low handle, 2 high handle, 3 window, 4 groove jump
        property real lastX: 0
        onPressed: function (mouse) {
            root.forceActiveFocus();
            dragArea.lastX = mouse.x;
            var lowX = root.centre(root._low);
            var highX = root.centre(root._high);
            if (Math.abs(mouse.x - lowX) <= root.handleHit) {
                dragArea.mode = 1;
            } else if (Math.abs(mouse.x - highX) <= root.handleHit) {
                dragArea.mode = 2;
            } else if (mouse.x > lowX + root.handleHit && mouse.x < highX - root.handleHit) {
                dragArea.mode = 3;
            } else {
                // Groove click: the nearest handle jumps there (the
                // factor-slider groove behaviour — the handle
                // no-op rule covers the handles only).
                dragArea.mode = 4;
                var v = root.valueAt(mouse.x);
                if (Math.abs(mouse.x - lowX) <= Math.abs(mouse.x - highX)) {
                    root._low = Math.max(root.minimum, Math.min(v, root._high - root.minWindow()));
                } else {
                    root._high = Math.min(root.maximum, Math.max(v, root._low + root.minWindow()));
                }
                root.windowAdjusted(root._low, root._high);
            }
            mouse.accepted = true;
        }
        onPositionChanged: function (mouse) {
            if (dragArea.mode === 0) {
                return;
            }
            var v = root.valueAt(mouse.x);
            var deltaValue = (mouse.x - dragArea.lastX) / root.usable() * root.span();
            dragArea.lastX = mouse.x;
            if (dragArea.mode === 1) {
                root._low = Math.max(root.minimum, Math.min(v, root._high - root.minWindow()));
            } else if (dragArea.mode === 2) {
                root._high = Math.min(root.maximum, Math.max(v, root._low + root.minWindow()));
            } else if (dragArea.mode === 3) {
                // The centre drag moves the WHOLE window (the
                // author's request), clamping at the scale ends.
                var shift = 0;
                var newLow = root._low + deltaValue;
                var newHigh = root._high + deltaValue;
                if (newLow < root.minimum)
                    shift = root.minimum - newLow;
                else if (newHigh > root.maximum)
                    shift = root.maximum - newHigh;
                root._low = newLow + shift;
                root._high = newHigh + shift;
            }
            root.windowAdjusted(root._low, root._high);
        }
        onReleased: {
            dragArea.mode = 0;
            // Re-sync to the host values: the model clamps, and the
            // other surface may have moved the window mid-drag.
            root._low = root.low;
            root._high = root.high;
        }
    }
}
