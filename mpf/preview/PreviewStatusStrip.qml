import QtQuick 2.15
import UM 1.5 as UM
import "../widgets"

// The live strip (4.3.0): the print's pause/resume control and the two
// wrapped readout rows, with the cell state machine that feeds them.
// The values arrive from the card and the two verbs leave as signals.
//
// The cells are still written IMPERATIVELY from the change handlers,
// never left to bindings: bindings on the setProperty-fed values of the
// dynamically created card go stale (engine-proven).
Column {
    id: root
    // The published block, the monitor's pause/resume verdicts and the
    // hourglass's own gate.
    property var previewBlock: ({})
    property bool previewBlockStale: true
    property string previewEtaText: ""
    property bool stripCanPause: false
    property bool stripCanResume: false
    property string stripPauseReason: ""
    property string stripResumeReason: ""
    property string stripPauseReasonDetail: ""
    property string stripResumeReasonDetail: ""
    property bool improveEtaAvailable: false
    // The printer-side readouts: the live layer and Z height, each with
    // its own availability gate (the height is independently optional —
    // a resolved layer with no height must not render the banned
    // emdash stand-in).
    property bool layerReadoutAvailable: false
    property string layerReadoutText: "—"
    property string heightReadoutText: "—"
    property bool heightReadoutAvailable: false
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    // Derived in updateStrip(): a stale or inactive block reads absent
    // everywhere; the layer/height rows carry their own gate.
    property bool stripValid: false
    property bool stripPaused: false
    property bool layerHeightRowsVisible: false
    signal printPauseRequested
    signal improveEtaRequested

    // THE STRIP (4.3.0): two fixed rows after the status
    // line (the 2026-09-16 ruling) — one full-width
    // Pause/Resume control on row 1; the temps cell and the
    // middle slot on row 2.
    // Every cell is explicitly width-bound: an implicit-width
    // row paints past the card edge (the load-indicator
    // precedent). The cells are driven IMPERATIVELY from the
    // block's change handlers — bindings on setProperty-fed
    // values go stale on this dynamically created component
    // (engine-proven).
    PreviewSecondaryButton {
        id: stripPauseButton
        objectName: "moonrakerStripPauseButton"
        width: parent.width
        height: UM.Theme.getSize("action_button").height
        // The action word is ALWAYS visible; the enablement
        // and the words are written by updateStrip().
        onClicked: root.printPauseRequested()
    }

    Row {
        width: parent.width
        // Two ALWAYS-WRAPPED columns (the live request): the
        // left box is two lines — the hotend temperature then
        // the bed, left-aligned; the right box is two lines
        // — the countdown then the finish time, right-aligned.
        // Each line carries its icon (thermometer, bed,
        // hourglass, clock). The boxes are layout only — no
        // outlines.
        spacing: root.buttonSpacing

        Column {
            id: stripTempsColumn
            width: 200 * screenScaleFactor
            spacing: 2 * screenScaleFactor
            // Each row lives in a plain-Item wrapper so the
            // tooltip can cover the GLYPH too — a hover area
            // anchored inside a Row would be positioned by
            // the positioner instead of filling it. The
            // wrapper's height rides the row's own implicit
            // height (content-derived, not a layout read).
            Item {
                visible: stripValid
                width: 200 * screenScaleFactor
                height: stripTempsRow.implicitHeight
                Row {
                    id: stripTempsRow
                    width: parent.width
                    spacing: 2 * screenScaleFactor
                    UM.ColorImage {
                        color: UM.Theme.getColor("text")
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        source: Qt.resolvedUrl("../resources/svg/Thermometer.svg")
                    }
                    UM.Label {
                        id: stripTemps
                        objectName: "moonrakerStripTemps"
                        width: 182 * screenScaleFactor
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        clip: true
                    }
                }
                HoverHandler {
                    id: tooltipHover1
                }
                UM.ToolTip {
                    visible: tooltipHover1.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "The current hotend temperature and its setpoint."
                }
            }
            Item {
                visible: stripValid
                width: 200 * screenScaleFactor
                height: stripBedRow.implicitHeight
                Row {
                    id: stripBedRow
                    width: parent.width
                    spacing: 2 * screenScaleFactor
                    UM.ColorImage {
                        color: UM.Theme.getColor("text")
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        source: Qt.resolvedUrl("../resources/svg/Bed.svg")
                    }
                    UM.Label {
                        id: stripBed
                        objectName: "moonrakerStripBed"
                        width: 182 * screenScaleFactor
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        clip: true
                    }
                }
                HoverHandler {
                    id: tooltipHover2
                }
                UM.ToolTip {
                    visible: tooltipHover2.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "The current heated-bed temperature and its setpoint."
                }
            }
            // The printer-side readout (the 2026-09-17
            // ruling): the current layer in the LEFT column —
            // the visibility rides a LOCAL property written
            // imperatively (bindings on the setProperty-fed
            // values go stale on this dynamically created
            // component).
            Item {
                visible: layerHeightRowsVisible
                width: 200 * screenScaleFactor
                height: layerReadoutRow.implicitHeight
                Row {
                    id: layerReadoutRow
                    width: parent.width
                    spacing: 2 * screenScaleFactor
                    UM.ColorImage {
                        color: UM.Theme.getColor("text")
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        source: Qt.resolvedUrl("../resources/svg/Layer.svg")
                    }
                    UM.Label {
                        id: layerReadout
                        objectName: "moonrakerLayerReadout"
                        width: 182 * screenScaleFactor
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        clip: true
                    }
                }
                HoverHandler {
                    id: tooltipHover3
                }
                UM.ToolTip {
                    visible: tooltipHover3.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "The printer's current layer."
                }
            }
        }

        Column {
            id: stripSlotColumn
            // 200 + 90 + spacing = the card's 300 px content
            // width: any wider and the column rides past the
            // pane's edge (the live report).
            width: 90 * screenScaleFactor
            spacing: 2 * screenScaleFactor
            Item {
                visible: stripValid
                width: 90 * screenScaleFactor
                height: stripSlotRow.implicitHeight
                Row {
                    id: stripSlotRow
                    width: parent.width
                    spacing: 2 * screenScaleFactor
                    UM.ColorImage {
                        color: UM.Theme.getColor("text")
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        source: Qt.resolvedUrl("../resources/svg/Hourglass.svg")
                        // The improve-Eta mirror (the
                        // 2026-09-17 ruling): the hourglass
                        // glyph is a real control exactly
                        // when the monitor's improve icon is
                        // — the print is active and the
                        // estimate still rides the plain
                        // blend with no download running.
                        // The click is the monitor's
                        // improveEta itself.
                        MouseArea {
                            anchors.fill: parent
                            enabled: root.improveEtaAvailable
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: root.improveEtaRequested()
                        }
                    }
                    UM.Label {
                        // The permanent middle slot: its TEXT
                        // changes — the print countdown while
                        // a gate passes, the policy's refusal
                        // reason whenever one refuses. The
                        // slot discharges "nothing silently
                        // unclickable": whenever the control
                        // is disabled, this cell says why in
                        // the policy's own words. The full
                        // sentence rides the tooltip; the ETA
                        // renders in the full text colour
                        // (the 2026-09-16 ruling).
                        id: stripSlot
                        objectName: "moonrakerStripSlot"
                        // Left-aligned: the text flows from
                        // its icon instead of squeezing
                        // against the pane's right edge (the
                        // live report).
                        width: 72 * screenScaleFactor
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        clip: true
                    }
                }
                HoverHandler {
                    id: tooltipHover4
                }
                UM.ToolTip {
                    visible: tooltipHover4.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "The print's remaining time and its expected finish time."
                }
            }
            Item {
                visible: stripValid
                width: 90 * screenScaleFactor
                height: stripFinishRow.implicitHeight
                Row {
                    id: stripFinishRow
                    width: parent.width
                    spacing: 2 * screenScaleFactor
                    UM.ColorImage {
                        color: UM.Theme.getColor("text")
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        source: Qt.resolvedUrl("../resources/svg/Clock.svg")
                    }
                    UM.Label {
                        id: stripFinish
                        objectName: "moonrakerStripFinish"
                        width: 72 * screenScaleFactor
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        clip: true
                    }
                }
                HoverHandler {
                    id: tooltipHover5
                }
                UM.ToolTip {
                    visible: tooltipHover5.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "The print's expected finish clock time."
                }
            }
            // The printer-side readout (the 2026-09-17
            // ruling): the current Z height in the right
            // column — same local-visibility discipline as
            // the layer row.
            Item {
                visible: heightReadoutAvailable
                width: 90 * screenScaleFactor
                height: heightReadoutRow.implicitHeight
                Row {
                    id: heightReadoutRow
                    width: parent.width
                    spacing: 2 * screenScaleFactor
                    UM.ColorImage {
                        color: UM.Theme.getColor("text")
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        source: Qt.resolvedUrl("../resources/svg/Height.svg")
                    }
                    UM.Label {
                        id: heightReadout
                        objectName: "moonrakerHeightReadout"
                        width: 72 * screenScaleFactor
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        clip: true
                    }
                }
                HoverHandler {
                    id: tooltipHover6
                }
                UM.ToolTip {
                    visible: tooltipHover6.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "The printer's current Z height."
                }
            }
        }
    }

    // The strip's state machine (4.3.0): the block's verdicts, the
    // staleness flag and the state word arrive as one setProperty-fed
    // value — the cells are rewritten imperatively on every change,
    // never left to bindings. The absent case names WHICH absence:
    // never-arrived, stale, and not-following are three different
    // facts — one connection claim for all three was a lie.
    function stripSlotText() {
        var b = root.previewBlock;
        if (b === null || b === undefined)
            return "Waiting for the printer";
        if (root.previewBlockStale)
            return "Feed is stale";
        if (b.inactive === true)
            return "Not following";
        if (b.state === "paused")
            return root.stripCanResume ? root.previewEtaText : (root.stripResumeReason.length > 0 ? root.stripResumeReason : "—");
        if (b.state === "printing")
            return root.stripCanPause ? root.previewEtaText : (root.stripPauseReason.length > 0 ? root.stripPauseReason : "—");
        return root.stripPauseReason.length > 0 ? root.stripPauseReason : "—";
    }

    function stripPauseTooltip() {
        var b = root.previewBlock;
        if (b === null || b === undefined)
            return "Waiting for the printer's first live values.";
        if (root.previewBlockStale)
            return "The feed is stale — the strip reads the last live values as '—'.";
        if (b.inactive === true)
            return "The monitor is not following this printer — the strip stays quiet.";
        if (stripPaused) {
            if (root.stripCanResume)
                return "Resume the paused print (Klipper RESUME).";
            return root.stripResumeReasonDetail.length > 0 ? root.stripResumeReasonDetail : root.stripResumeReason;
        }
        if (root.stripCanPause)
            return "Pause the current print immediately (Klipper PAUSE).";
        return root.stripPauseReasonDetail.length > 0 ? root.stripPauseReasonDetail : root.stripPauseReason;
    }

    function updateStrip() {
        stripValid = !root.previewBlockStale && root.previewBlock !== null && root.previewBlock !== undefined && root.previewBlock.inactive !== true;
        stripPaused = stripValid && root.previewBlock.state === "paused";
        // The two wrapped lines (the live request): the hotend and
        // the bed each get their own labelled line, and the slot
        // splits into the countdown and the finish time. A refusal
        // reason (no separator) occupies the countdown line alone.
        stripTemps.text = stripValid && typeof root.previewBlock.hotend === "string" ? root.previewBlock.hotend : "—";
        stripBed.text = stripValid && typeof root.previewBlock.bed === "string" ? root.previewBlock.bed : "—";
        var slotText = stripSlotText();
        var parts = slotText.indexOf(" · ") >= 0 ? slotText.split(" · ") : [slotText, ""];
        stripSlot.text = stripValid ? parts[0] : "—";
        stripFinish.text = stripValid ? parts[1] : "";
        stripPauseButton.enabled = stripValid && (stripPaused ? root.stripCanResume : root.stripCanPause);
        stripPauseButton.text = stripPaused ? "Resume print" : "Pause print";
        stripPauseButton.tooltip = stripPauseTooltip();
    }

    onPreviewBlockChanged: updateStrip()
    onPreviewBlockStaleChanged: updateStrip()
    onPreviewEtaTextChanged: updateStrip()
    // The cells read the pause/resume verdicts too (the slot's refusal
    // word, the button's enablement and its tooltip): a verdict-only
    // change — the block and the ETA held constant — must refresh them,
    // or the strip keeps the outgoing verdict's copy.
    onStripCanPauseChanged: updateStrip()
    onStripCanResumeChanged: updateStrip()
    onStripPauseReasonChanged: updateStrip()
    onStripResumeReasonChanged: updateStrip()
    onStripPauseReasonDetailChanged: updateStrip()
    onStripResumeReasonDetailChanged: updateStrip()
    onLayerReadoutAvailableChanged: layerHeightRowsVisible = root.layerReadoutAvailable
    onLayerReadoutTextChanged: layerReadout.text = root.layerReadoutText
    onHeightReadoutTextChanged: heightReadout.text = root.heightReadoutText

    Component.onCompleted: updateStrip()
}
