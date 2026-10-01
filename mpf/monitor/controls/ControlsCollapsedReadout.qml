import QtQuick 2.15
import UM 1.5 as UM
import "../../resources/theme"

// The printer-controls strip's readout: the vertical pane title with the
// row of fixed-width position and z-offset fields beneath it. The pane
// frames it against the header and hands itself over as the frame the
// strip's fit measures against: the fields hide through the fit when
// the pane's VISIBLE height runs out, and through the availability
// gates when the model has no value to show.
Item {
    id: root

    property bool controlsCollapsed: false
    property var printer: null
    // The pane the strip lives in: the fit reads its rendered height
    // and the window's, and maps the row into it.
    property Item frame: null

    // The box swaps the label's extents so the rotated text
    // occupies it exactly, starting at its top.
    visible: root.controlsCollapsed
    width: collapsedTitle.implicitHeight
    height: collapsedTitle.implicitWidth
    UM.Label {
        id: collapsedTitle
        text: "Printer controls"
        font: UM.Theme.getFont("medium_bold")
        color: UM.Theme.getColor("text_inactive")
        rotation: 90
        anchors.centerIn: parent
    }

    // The collapsed readout (the 2026-09-17
    // ruling): position, Z offset and flow rate fill the
    // empty space BELOW the title — regular text, not the
    // title's face. ONE line of FIXED-WIDTH fields, each
    // its own label with an icon glyph, no separators:
    // the values changing never reflows the strip and
    // the fields can never overlap (the live request).
    Item {
        id: controlsCollapsedReadoutBox
        visible: root.controlsCollapsed
        // The same short-window discipline as the
        // monitor's readouts: the box clips, and the
        // line hides when it cannot fit whole.
        clip: true
        anchors.top: parent.bottom
        anchors.topMargin: 2 * UM.Theme.getSize("default_margin").height
        anchors.horizontalCenter: parent.horizontalCenter
        // The box hugs the content: its height tracks
        // the row's implicit width, so the centred row
        // fills it and the strip starts at the margin
        // under the title (the live report). 18 is the
        // label line height.
        width: 18 * screenScaleFactor
        height: controlsReadoutRow.implicitWidth
        Row {
            id: controlsReadoutRow
            anchors.centerIn: parent
            spacing: 2 * screenScaleFactor
            rotation: 90
            // The fit hides through OPACITY, never
            // the visibility flag: a hidden pair keeps
            // its place in the layout, so the survivors
            // can never re-centre in the box (the live
            // report) — the strip stays anchored under
            // the title. The labels' fixed widths are
            // the field boundaries.
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.positionAvailable
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Position.svg")
            }
            UM.Label {
                objectName: "controlsCollapsedReadoutText"
                property bool fitHidden: false
                visible: root.positionAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printer != null ? root.printer.monitorPositionX : ""
                // Each axis independent and fixed-width:
                // "X 888.88" must sit without reflowing
                // (the live ruling). The axis cells wear
                // their axis colours: X red, Y green, Z
                // blue — the other cells stay themed.
                width: 70 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: MoonrakerTheme.axisX
                elide: Text.ElideRight
            }
            UM.Label {
                objectName: "controlsCollapsedReadoutText"
                property bool fitHidden: false
                visible: root.positionAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printer != null ? root.printer.monitorPositionY : ""
                width: 70 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: MoonrakerTheme.axisY
                elide: Text.ElideRight
            }
            UM.Label {
                objectName: "controlsCollapsedReadoutText"
                property bool fitHidden: false
                visible: root.positionAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printer != null ? root.printer.monitorPositionZ : ""
                width: 70 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: MoonrakerTheme.axisZ
                elide: Text.ElideRight
            }
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.zOffsetAvailable
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/ZOffset.svg")
            }
            UM.Label {
                // The z pair's own name (the harness
                // rule): the position cells share
                // controlsCollapsedReadoutText, and the
                // standby scenario proves the z offset's
                // honest zero renders while the
                // unavailable position hides.
                objectName: "controlsCollapsedZOffsetLabel"
                property bool fitHidden: false
                visible: root.zOffsetAvailable
                opacity: fitHidden ? 0 : 1
                text: root.printer != null ? root.printer.zOffsetText : ""
                // "88.000 mm" must sit comfortably (the
                // live ruling).
                width: 96 * screenScaleFactor
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }
        }
    }

    // The availability gates (the live ruling): a value that is
    // unavailable must not render — neither the value nor its
    // glyph. The X/Y/Z tuple hides WHOLE when any one axis is
    // absent. Maintained IMPERATIVELY from the model's change
    // signals: bindings on the setProperty-fed values go stale
    // on both engines (the camera lesson), and a stale gate
    // keeps rendering "—" cells forever.
    property bool positionAvailable: false
    property bool zOffsetAvailable: false
    function refreshAvailabilityGates() {
        positionAvailable = root.printer != null && root.printer.monitorPositionX !== "—" && root.printer.monitorPositionX !== "" && root.printer.monitorPositionY !== "—" && root.printer.monitorPositionY !== "" && root.printer.monitorPositionZ !== "—" && root.printer.monitorPositionZ !== "";
        zOffsetAvailable = root.printer != null && root.printer.zOffsetText !== "—" && root.printer.zOffsetText !== "";
        // The strip's length changes with the availability — the
        // fit re-measures (the panel's catch).
        Qt.callLater(root.updateControlsReadoutFits);
    }
    Connections {
        target: root.printer
        function onMonitorPositionXChanged() {
            root.refreshAvailabilityGates();
        }
        function onMonitorPositionYChanged() {
            root.refreshAvailabilityGates();
        }
        function onMonitorPositionZChanged() {
            root.refreshAvailabilityGates();
        }
        function onZOffsetTextChanged() {
            root.refreshAvailabilityGates();
        }
    }
    onPrinterChanged: root.refreshAvailabilityGates()

    // The pane's extents change with the stage: the fit re-measures
    // against the new visible height rather than the one it was
    // fitted into at collapse time.
    Connections {
        target: root.frame
        function onHeightChanged() {
            root.updateControlsReadoutFits();
        }
    }

    // The whole-pair fit, written IMPERATIVELY against the
    // pane's actual rendered geometry: the label's two
    // main-axis endpoints mapped into the pane bound the pair
    // (a single corner read wrong — the live report), and the
    // hysteresis keeps an intermediate resize geometry from
    // flickering the pair at the threshold.
    function fitControlsPair(first, stride) {
        var children = controlsReadoutRow.children;
        if (first + stride - 1 >= children.length)
            return;
        var last = children[first + stride - 1];
        var a = last.mapToItem(root.frame, 0, 0).y;
        var b = last.mapToItem(root.frame, last.width, 0).y;
        var bottom = Math.max(a, b);
        var hidden = children[first].fitHidden;
        // Bottom-only, the info fit's lesson: the top guard read
        // false under some engine mappings and froze the hides.
        // The pane's full height can exceed the visible viewport
        // (the monitor's x7 report) — bound against the pane's
        // VISIBLE extent, the window's height minus the pane's
        // scene position.
        var windowHeight = root.frame.Window != null ? root.frame.Window.height : 0;
        var paneTop = root.frame.mapToItem(null, 0, 0).y;
        var visibleHeight = Math.min(root.frame.height, windowHeight - paneTop);
        var hide = hidden ? bottom > visibleHeight - 14 * screenScaleFactor : bottom > visibleHeight - 6 * screenScaleFactor;
        for (var i = first; i < first + stride; ++i)
            children[i].fitHidden = hide;
    }
    function updateControlsReadoutFits() {
        // The position group is the glyph plus the three axis
        // cells; the z group is a pair (the flow pair moved to
        // the status strip, the live ruling).
        fitControlsPair(0, 4);
        fitControlsPair(4, 2);
    }
    onControlsCollapsedChanged: {
        Qt.callLater(root.updateControlsReadoutFits);
        // The deferred retry: the callLater can run BEFORE the
        // collapsed layout settles (the monitor's x7 report) —
        // the timer re-measures with the settled geometry.
        fitControlsRetry.restart();
    }
    Timer {
        id: fitControlsRetry
        interval: 200
        repeat: false
        onTriggered: root.updateControlsReadoutFits()
    }
}
