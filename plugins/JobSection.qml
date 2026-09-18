import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The Print-job section (4.3.0 extraction): the status, progress,
// layer, ETA and Improve-ETA rows out of the monitor as one
// property-driven component.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    // The tuple rule (the live ruling): if ANY axis value is
    // unavailable, the Position row's cells empty themselves — a
    // permanent slot, never a visibility flip (the polish-loop
    // class).
    readonly property bool positionRowAvailable: root.printerModel != null && root.printerModel.monitorPositionX !== "—" && root.printerModel.monitorPositionX !== "" && root.printerModel.monitorPositionY !== "—" && root.printerModel.monitorPositionY !== "" && root.printerModel.monitorPositionZ !== "—" && root.printerModel.monitorPositionZ !== ""

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Print job"
        sectionId: "job"
        sectionIcon: "Printer"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["job"] !== false
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height

        Row {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width
            UM.Label {
                // The two status lines carry labels
                // ("Status" and "Message"); the label
                // column stays just wide enough for
                // the words so the values keep the
                // room (the ruling).
                width: 64 * screenScaleFactor
                text: "Status"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }
            UM.Label {
                // Inert; the harness's rendered-follows
                // scenarios read this label's text.
                objectName: "moonrakerStatusStateText"
                width: Math.max(0, parent.width - parent.spacing - 64 * screenScaleFactor)
                text: root.printerModel != null ? root.printerModel.monitorState : "Not connected"
                font: UM.Theme.getFont("medium_bold")
                elide: Text.ElideRight
            }
        }

        UM.Label {
            text: root.printerModel != null && root.printerModel.monitorFilename.length > 0 ? root.printerModel.monitorFilename : "No active file"
            color: UM.Theme.getColor("text_inactive")
            Layout.fillWidth: true
            elide: Text.ElideMiddle
        }

        Row {
            // NO-REFLOW RULE: a permanent slot — an
            // M117 message arriving mid-print used to
            // shove the grid down and back. The
            // objectName stays on the VALUE label so
            // the harness's rendered-text assertions
            // keep reading the raw message.
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width
            UM.Label {
                width: 64 * screenScaleFactor
                height: 36 * screenScaleFactor
                text: "Message"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }
            UM.Label {
                objectName: "moonrakerM117Slot"
                width: Math.max(0, parent.width - parent.spacing - 64 * screenScaleFactor)
                height: 36 * screenScaleFactor
                text: root.printerModel != null ? root.printerModel.monitorMessage : ""
                // The message is primary content: full
                // text colour, not the inactive grey
                // (the ruling).
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
            }
        }

        // THE STACKED BAR (the live ruling, mirroring the collapsed
        // status readout): the print fill is the bottom half and the
        // layer fill the top half, touching at the centre — no gap.
        // Without layer info the print fill takes the whole height.
        Item {
            Layout.fillWidth: true
            // Double the strip's height (the live ruling): with all
            // three fills stacked the original 10 px was too tiny to
            // read the sections.
            Layout.preferredHeight: 20 * screenScaleFactor
            UM.TooltipArea {
                anchors.fill: parent
                acceptedButtons: Qt.NoButton
                // Lists only the fills that actually render (the live
                // ruling), top to bottom. A position qualifier only
                // makes sense beside other fills — the print fill is
                // always present, so a lone bar names it plainly.
                text: {
                    var shown = [];
                    if (root.printerModel != null && root.printerModel.monitorLayerProgress >= 0)
                        shown.push("the current layer's progress (top)");
                    if (root.printerModel != null && root.printerModel.nextPauseFraction >= 0)
                        shown.push("progress towards the next scheduled pause (middle, orange)");
                    shown.push("overall print progress (bottom)");
                    if (shown.length === 1)
                        shown[0] = "overall print progress";
                    var body = shown.slice(0, -1).join(", ");
                    if (shown.length > 1)
                        body += (shown.length > 2 ? "," : "") + " and ";
                    return "The stacked progress: " + body + shown[shown.length - 1] + ".";
                }
                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.width: 1 * screenScaleFactor
                    border.color: UM.Theme.getColor("lining")
                    Rectangle {
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        // Thirds with a scheduled pause, halves without,
                        // the whole height without layer info (the live
                        // ruling).
                        height: parent.height * (root.printerModel != null && root.printerModel.monitorLayerProgress >= 0 ? (root.printerModel.nextPauseFraction >= 0 ? 1 / 3 : 0.5) : 1.0)
                        // monitorProgress is a PERCENTAGE (0..100); the
                        // layer value is already 0..1.
                        width: parent.width * Math.max(0, Math.min(1, root.printerModel != null ? root.printerModel.monitorProgress / 100 : 0))
                        color: UM.Theme.getColor("primary")
                    }
                    Rectangle {
                        // The next scheduled pause's fill (the live
                        // ruling): the MIDDLE of the stack, the mesh's
                        // neon orange — NOT RENDERED while no pause
                        // lies ahead (the gate, not a zero width).
                        objectName: "nextPauseFill"
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
            }
        }

        Row {
            Layout.alignment: Qt.AlignHCenter
            spacing: 4 * screenScaleFactor
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorProgress.toFixed(2) + "%" : "0.00%"
                font: UM.Theme.getFont("medium_bold")
                // Both percentages share one line height and centre
                // their glyphs in it (the live report: the pair sat
                // top-aligned).
                height: 24 * screenScaleFactor
                verticalAlignment: Text.AlignVCenter
            }
            UM.Label {
                // The layer percentage rides beside the big overall
                // one in grey (the live ruling) — the Layer row keeps
                // just the count.
                text: root.printerModel != null && root.printerModel.monitorLayerProgress >= 0 ? "(" + (root.printerModel.monitorLayerProgress * 100).toFixed(2) + "%)" : ""
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
                height: 24 * screenScaleFactor
                verticalAlignment: Text.AlignVCenter
                UM.TooltipArea {
                    anchors.fill: parent
                    text: "Layer progress — how far through the current layer."
                    acceptedButtons: Qt.NoButton
                }
            }
        }

        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            // "Last action": the shared one-shot
            // lane's status row, first in the grid so
            // its columns ARE the grid's columns (a
            // separate row above read as misaligned —
            // the report). The caption is
            // permanent so the row explains itself
            // before its first event; the value is
            // "—" until then.
            UM.Label {
                text: "Last action"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null && root.printerModel.actionStatus.length > 0 ? root.printerModel.actionStatus : "—"
                Layout.fillWidth: true
                // A live value in the status stack must never wrap:
                // a per-poll wrap flip reflows the column (the
                // polish-loop class — the panel's catch).
                wrapMode: Text.NoWrap
                elide: Text.ElideRight
            }

            UM.Label {
                text: "Layer"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("narrow_margin").width
                    UM.Label {
                        text: root.printerModel != null ? root.printerModel.monitorLayer : "—"
                        Layout.fillWidth: true
                        // Which source produced the layer —
                        // Klipper's stats, the file index, or
                        // the extrusion-guarded Z estimate.
                        UM.TooltipArea {
                            anchors.fill: parent
                            text: root.printerModel != null && root.printerModel.monitorLayerSource !== undefined && root.printerModel.monitorLayerSource.length > 0 ? "Layer source: " + root.printerModel.monitorLayerSource : ""
                            acceptedButtons: Qt.NoButton
                        }
                    }
                }
            }

            UM.Label {
                text: "Elapsed"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorElapsed : "00:00:00"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "Remaining"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("narrow_margin").width
                UM.Label {
                    text: root.printerModel != null ? root.printerModel.monitorEta : "—"
                    // The ETA colour shows its basis:
                    // normal text for the layer-timed
                    // estimate, muted for the plain
                    // blend — the tooltip spells both out.
                    color: root.printerModel != null && root.printerModel.monitorEtaBasis === "blend" ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text")
                    // The row must compress when the
                    // phase label appears, or the
                    // whole bar spills off the pane.
                    elide: Text.ElideRight
                    UM.TooltipArea {
                        anchors.fill: parent
                        // No basis claim while the value itself is
                        // paused or absent — "Moonraker's estimate"
                        // under a dash would lie (panel UX P2).
                        text: root.printerModel == null || root.printerModel.monitorEta === "—" || root.printerModel.monitorEta === "Paused" ? "" : root.printerModel.monitorEtaBasis === "index" ? "Estimated from the G-code's layer timings × the observed speed." : "Moonraker's estimate — download the G-code for the accurate layer-timed estimate."
                        acceptedButtons: Qt.NoButton
                    }
                }
                // The Improve-ETA affordance: a small
                // download glyph beside the value it
                // improves, shown only while the
                // plain blend is the active basis.
                Item {
                    // NO-REFLOW RULE: the 16 px glyph
                    // slot is always reserved — it
                    // fades instead of shifting the
                    // ETA value sideways.
                    width: 16 * screenScaleFactor
                    height: 16 * screenScaleFactor
                    opacity: root.printerModel != null && root.printerModel.printActive && (root.printerModel.monitorEtaBasis === "blend" || root.printerModel.improvingEta) ? 1 : 0
                    UM.TooltipArea {
                        anchors.fill: parent
                        text: root.printerModel != null && root.printerModel.improvingEta ? "Downloading and indexing the print…" : "Improve the estimate — download and index this print's G-code without loading it into the preview."
                        acceptedButtons: Qt.NoButton
                    }
                    UM.ColorImage {
                        id: etaGlyph
                        anchors.fill: parent
                        // The hourglass is the non-clickable
                        // in-progress state; the download
                        // glyph returns on failure (timeout).
                        source: root.printerModel != null && root.printerModel.improvingEta ? Qt.resolvedUrl("Hourglass.svg") : Qt.resolvedUrl("Download.svg")
                        color: UM.Theme.getColor("text")
                        // The download glyph must never
                        // carry the angle the hourglass
                        // froze at (the author's live
                        // report): an assignment from
                        // inside the animation cannot
                        // win against the animation
                        // binding, so the IDLE STATE
                        // forces the reset instead.
                        states: [
                            State {
                                name: "idle"
                                when: !(root.printerModel != null && root.printerModel.improvingEta)
                                PropertyChanges {
                                    target: etaGlyph
                                    rotation: 0
                                }
                            }
                        ]
                        // The hourglass flips and rests at
                        // each 180-degree stop while the
                        // sand drains, then flips again.
                        SequentialAnimation on rotation  {
                            running: root.printerModel != null && root.printerModel.improvingEta
                            loops: Animation.Infinite
                            NumberAnimation {
                                from: 0
                                to: 180
                                duration: 350
                                easing.type: Easing.InOutCubic
                            }
                            PauseAnimation {
                                duration: 700
                            }
                            NumberAnimation {
                                from: 180
                                to: 360
                                duration: 350
                                easing.type: Easing.InOutCubic
                            }
                            PauseAnimation {
                                duration: 700
                            }
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        // Clickable while busy too: a click mid-pull is a
                        // legitimate retry, and after a failed download
                        // the glyph is the ONLY in-UI recovery (the
                        // hourglass state ends on failure — panel P1-1).
                        enabled: root.printerModel != null && root.printerModel.monitorConnected
                        cursorShape: root.printerModel != null ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: root.printerModel.improveEta()
                    }
                }
                // The spacer keeps the glyph hugging
                // the ETA text rather than drifting to
                // the cell's right edge.
                Item {
                    Layout.fillWidth: true
                }
            }

            // The download+index progress: its OWN
            // full-width grid row, so the bar is never
            // crushed beside the ETA text. The bar
            // CLIPS its children, so the sweep cannot
            // render past the bar's edge whatever the
            // layout around it does; the sweep also
            // restarts when the bar resizes so its
            // captured endpoints stay current.
            RowLayout {
                // NO-REFLOW RULE: the Improve-ETA
                // progress row reserves its space at
                // all times (opacity, never
                // visibility) — its automatic
                // disappearance on completion used to
                // shift the rows beneath it.
                Layout.fillWidth: true
                Layout.columnSpan: 2
                Layout.topMargin: UM.Theme.getSize("narrow_margin").height
                spacing: UM.Theme.getSize("narrow_margin").width
                Item {
                    id: improveEtaBar
                    Layout.fillWidth: true
                    Layout.preferredHeight: 8 * screenScaleFactor
                    opacity: root.printerModel != null && root.printerModel.improvingEta ? 1 : 0
                    clip: true
                    // A trivial 0..1 phase animation;
                    // the sweep's POSITION is a binding
                    // on it, so it always tracks the
                    // current width — no captured
                    // endpoints, no restart tricks.
                    property real sweepPhase: 0
                    NumberAnimation on sweepPhase  {
                        running: root.printerModel != null && root.printerModel.improvingEta && root.printerModel.improveEtaProgress < 0
                        from: 0
                        to: 1
                        duration: 1100
                        loops: Animation.Infinite
                    }
                    Cura.RoundedRectangle {
                        anchors.fill: parent
                        color: "transparent"
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        radius: UM.Theme.getSize("progressbar_radius").width
                        cornerSide: Cura.RoundedRectangle.Direction.All
                    }
                    Cura.RoundedRectangle {
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.left: parent.left
                        anchors.margins: 1 * screenScaleFactor
                        width: Math.max(0, Math.min(1, root.printerModel != null ? root.printerModel.improveEtaProgress : 0)) * (parent.width - 2 * screenScaleFactor)
                        color: UM.Theme.getColor("primary")
                        radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
                        cornerSide: Cura.RoundedRectangle.Direction.All
                        visible: root.printerModel != null && root.printerModel.improveEtaProgress >= 0
                    }
                    Cura.RoundedRectangle {
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.margins: 1 * screenScaleFactor
                        width: (parent.width - 2 * screenScaleFactor) / 3
                        color: UM.Theme.getColor("primary")
                        radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
                        cornerSide: Cura.RoundedRectangle.Direction.All
                        visible: root.printerModel != null && root.printerModel.improvingEta && root.printerModel.improveEtaProgress < 0
                        // Qualified through the bar's id:
                        // unqualified names do NOT resolve
                        // through the visual parent (the
                        // engine logs a ReferenceError and
                        // the sweep never moves).
                        x: (1 - Math.abs(2 * improveEtaBar.sweepPhase - 1)) * (parent.width - width) + screenScaleFactor
                    }
                }
                UM.Label {
                    text: root.printerModel != null && root.printerModel.improvingEta ? (root.printerModel.improveEtaPhase + (root.printerModel.improveEtaProgress >= 0 ? " " + (root.printerModel.improveEtaProgress * 100).toFixed(0) + "%" : "")) : ""
                    color: UM.Theme.getColor("text_inactive")
                    Layout.maximumWidth: 140 * screenScaleFactor
                    elide: Text.ElideRight
                }
            }

            UM.Label {
                text: "Finish"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorFinish : "—"
                Layout.fillWidth: true
            }

            // The next scheduled pause's ETA (the live ruling):
            // countdown and deadline, under Finish — the row hides
            // whole while no pause lies ahead.
            // NO-REFLOW RULE (the M117 slot's precedent): the row is
            // a PERMANENT slot — flipping its visibility reflowed
            // the section stack and fed a layout polish loop (the
            // live report, the pause's clear/re-add cycle). The
            // labels read empty while no pause lies ahead.
            UM.Label {
                text: root.printerModel != null && root.printerModel.nextPauseEta.length > 0 ? "Next pause" : ""
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                // "(baked)" marks the gcode's own pauses (the live
                // ruling) — the manual schedule reads plain.
                text: root.printerModel != null ? (root.printerModel.nextPauseEta + (root.printerModel.nextPauseBaked ? " (baked)" : "")) : ""
                Layout.fillWidth: true
            }

            // Filament rows sit after Finish, beside
            // the progress block they belong to (the
            // author's placement). NO-REFLOW RULE:
            // the rows are permanent — the values
            // read "—" until Klipper reports them, so
            // the grid never shifts when a job
            // starts or finishes.
            UM.Label {
                text: "Filament used"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.filamentUsed : "—"
                Layout.fillWidth: true
            }
            UM.Label {
                text: "Filament remaining"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.filamentRemaining : "—"
                Layout.fillWidth: true
            }

            UM.Label {
                // "Speed factor" (the 4.2.0
                // ruling): the row is the M220
                // multiplier, and the live speed
                // row below is titled Velocity.
                text: "Speed factor"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorSpeed : "100%"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "Flow"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorFlow : "100%"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "Position"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            // The axis-coloured cells (the 4.5.0 ruling): the
            // Toolhead's ruled pattern — three fixed-width cells in
            // the axis colours, no-wrap and elided, so the row can
            // never reflow per poll (the status stack's polish-loop
            // class). The tuple rule empties the whole row when any
            // axis is unavailable.
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").width
                UM.Label {
                    objectName: "jobPositionCellX"
                    Layout.preferredWidth: 50 * screenScaleFactor
                    text: positionRowAvailable ? (root.printerModel != null ? root.printerModel.monitorPositionX : "") : ""
                    color: MoonrakerTheme.axisX
                    elide: Text.ElideRight
                    wrapMode: Text.NoWrap
                }
                UM.Label {
                    objectName: "jobPositionCellY"
                    Layout.preferredWidth: 50 * screenScaleFactor
                    text: positionRowAvailable ? (root.printerModel != null ? root.printerModel.monitorPositionY : "") : ""
                    color: MoonrakerTheme.axisY
                    elide: Text.ElideRight
                    wrapMode: Text.NoWrap
                }
                UM.Label {
                    objectName: "jobPositionCellZ"
                    Layout.preferredWidth: 50 * screenScaleFactor
                    text: positionRowAvailable ? (root.printerModel != null ? root.printerModel.monitorPositionZ : "") : ""
                    color: MoonrakerTheme.axisZ
                    elide: Text.ElideRight
                    wrapMode: Text.NoWrap
                }
            }

            // The motion block (4.2.0): the two
            // core-poll values, then the aux-poll
            // ceiling. NO-REFLOW RULE: permanent
            // rows — idle reads 0, "—" only when
            // the printer reports no motion
            // object. The values must not wrap.
            UM.Label {
                text: "Velocity"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorVelocity : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                UM.TooltipArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.NoButton
                    // No claim under a dash (the
                    // panel UX ruling).
                    text: root.printerModel != null && root.printerModel.monitorVelocity !== "—" ? "Klipper's live toolhead speed — a magnitude, with no direction." : ""
                }
            }
            UM.Label {
                text: "Flow rate"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorFlowRate : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                UM.TooltipArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.NoButton
                    text: root.printerModel != null && root.printerModel.monitorFlowRate !== "—" ? "The commanded volumetric flow — Klipper's live extruder velocity × the filament cross-section. Uses printer.cfg's filament_diameter for the active tool (" + root.printerModel.monitorFlowDiameter + "). Pressure advance is excluded, the value can lag for up to 30 seconds after the last extrusion, and a negative reading while retracting is correct." : ""
                }
            }
            UM.Label {
                text: "Filament diameter"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorFlowDiameter : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                UM.TooltipArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.NoButton
                    text: root.printerModel != null && root.printerModel.monitorFlowDiameter !== "—" ? "The active tool's filament_diameter from printer.cfg — the Flow rate row multiplies by this cross-section. There is deliberately no override: a wrong value is a printer.cfg error." : ""
                }
            }
            UM.Label {
                text: "Accel limit"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorAccelLimit : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                UM.TooltipArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.NoButton
                    text: root.printerModel != null && root.printerModel.monitorAccelLimit !== "—" ? "The acceleration ceiling Klipper has configured — SET_VELOCITY_LIMIT or M204 can change it mid-print. Not the acceleration currently in use." : ""
                }
            }
        }
    }
}
