import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "theme"

// The plate map's exclude face (4.6.0): the shared canvas plus the
// triple-click gesture. The gesture is the confirmation — there are
// no dialogs (the walked ruling); the click-progress cue and the
// host's counter line make it legible before the third click.
Item {
    id: root
    objectName: "moonrakerPlateExcludeFace"

    property var printerModel: null
    property var plate: null
    property bool compact: false
    property string hoveredName: ""
    property int clickProgress: 0  // 1..3, the host's counter line
    property string pendingAction: ""  // "exclude" | "restore" while arming
    signal progressChanged(int clicks)
    signal excludeRequested(string name)
    signal restoreRequested(string name)

    // The gesture window, a named constant: the harness's inline
    // script drives its clicks well inside it, and the pin asserts
    // the RELATIONSHIP, never the literal (the review's rule).
    readonly property int tripleClickWindowMs: 400

    property string _pendingName: ""
    property int _pendingClicks: 0
    property real _pendingSince: 0

    // The gesture decision as a pure callable (the review's rule:
    // unit-testable with zero timing, the TemperatureChart.
    // _nearestIndex shape).
    function resolveGesture(count, firstName, lastName, windowMs) {
        if (count <= 0 || windowMs < 0) {
            return {
                "fire": false
            };
        }
        if (count === 1) {
            return {
                "fire": false,
                "progress": 1
            };
        }
        if (count === 2) {
            return {
                "fire": false,
                "progress": 2
            };
        }
        return {
            "fire": firstName === lastName,
            "name": firstName,
            "progress": 3
        };
    }

    function _registerClick(name) {
        var now = Date.now();
        if (name === "" || (root._pendingName !== "" && name !== root._pendingName)) {
            root._pendingName = name;
            root._pendingClicks = 1;
            root._pendingSince = now;
        } else if (root._pendingName === name && now - root._pendingSince <= root.tripleClickWindowMs) {
            root._pendingClicks += 1;
        } else {
            root._pendingName = name;
            root._pendingClicks = 1;
            root._pendingSince = now;
        }
        var verdict = root.resolveGesture(root._pendingClicks, root._pendingName, name, root.tripleClickWindowMs);
        var row = root._rowFor(name);
        root.pendingAction = row != null && row.excluded === true ? "restore" : "exclude";
        root.clickProgress = verdict.progress;
        root.progressChanged(verdict.progress);
        if (verdict.fire) {
            root._pendingName = "";
            root._pendingClicks = 0;
            root.clickProgress = 0;
            root.progressChanged(0);
            root.pendingAction = "";
            var row = root._rowFor(verdict.name);
            if (row != null && row.excluded === true) {
                root.restoreRequested(verdict.name);
            } else if (row != null) {
                root.excludeRequested(verdict.name);
            }
        }
    }

    function _rowFor(name) {
        if (root.plate == null) {
            return null;
        }
        for (var i = 0; i < root.plate.objects.length; ++i) {
            if (root.plate.objects[i].name === name) {
                return root.plate.objects[i];
            }
        }
        return null;
    }

    function hoverDetail() {
        // The hover readout: the name and its state — the map's
        // colours carry the same state, the words carry it legibly.
        var row = root._rowFor(root.hoveredName);
        if (row == null) {
            return "";
        }
        if (row.excluded === true) {
            return row.name + " — excluded";
        }
        if (row.current === true) {
            return row.name + " — current";
        }
        return row.name + " — included";
    }

    function hoverInk() {
        // The label wears the map's own state ink (the live request):
        // the words and the outline always agree on the colour.
        var row = root._rowFor(root.hoveredName);
        if (row == null) {
            return UM.Theme.getColor("text_inactive");
        }
        if (row.excluded === true) {
            return MoonrakerTheme.dangerRed;
        }
        if (row.current === true) {
            return UM.Theme.getColor("primary");
        }
        return row.passed === true ? MoonrakerTheme.plateCurrent : UM.Theme.getColor("text");
    }

    PlateCanvas {
        id: canvas
        anchors.fill: parent
        printerModel: root.printerModel
        plate: root.plate
        compact: root.compact
        hoveredName: root.hoveredName
        onObjectHovered: function (name) {
            root.hoveredName = name;
        }
        MouseArea {
            anchors.fill: parent
            // The MINI never fires the gesture — the section's own
            // click opens the popover; this face is only ever the
            // enlarged canvas. The gesture lives here alone.
            enabled: !root.compact
            hoverEnabled: true
            onPositionChanged: function (mouse) {
                // The face's own area sits above the canvas's hover
                // area, so it drives the hover itself.
                canvas.hoveredName = canvas.hitTest(mouse.x, mouse.y);
                root.hoveredName = canvas.hoveredName;
            }
            onExited: {
                canvas.hoveredName = "";
                root.hoveredName = "";
            }
            onClicked: function (mouse) {
                var name = canvas.hitTest(mouse.x, mouse.y);
                root._registerClick(name);
            }
        }
    }
}
