import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item {
    id: base
    objectName: "moonrakerPreviewCard"

    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool configuredForFollowing: false
    property bool gateVisible: false
    // Declared so the bindings below exist from creation: undeclared
    // dynamic names read as undefined at load time and the bindings
    // are dropped before setProperty can ever reach them.
    property bool loadBusy: false
    property real loadProgress: -1
    property string loadPhase: ""
    property bool hasToolpath: false
    property bool sceneHasObjects: false
    property string activePrinterName: ""
    property string statusText: ""
    property string statusIconName: "Information"
    property bool bedMeshAvailable: false
    property bool bedMeshVisible: true
    // The Preview value block (4.3.0): the Monitor's per-poll
    // carrier — the strip reads the fixed pair, the verdicts and
    // the aux-landing stamp through these.
    property var previewBlock: ({})
    property bool previewBlockStale: true
    property string previewEtaText: ""
    // The strip's derived state, computed in updateStrip(): a stale
    // or inactive block reads absent everywhere.
    property bool stripValid: false
    property bool stripPaused: false
    // The pause/resume grey-out's single authority (the debt pack's
    // two-clock unification): the monitor model's verdicts, pushed by
    // the presentation — the strip's enable and reasons read these,
    // never the preview block's own copies.
    property bool stripCanPause: false
    property bool stripCanResume: false
    property string stripPauseReason: ""
    property string stripResumeReason: ""
    property string stripPauseReasonDetail: ""
    property string stripResumeReasonDetail: ""
    property string bedMeshRangeText: ""
    property string bedMeshMinimumText: ""
    property string bedMeshMaximumText: ""
    property real bedMeshMinimum: 0
    property real bedMeshMaximum: 0
    // The heightmap range filter (a request): the window
    // the Monitor model owns; both surfaces show the same handles.
    property real bedMeshThresholdLow: 0
    property real bedMeshThresholdHigh: 0
    // The "scale z-max" exaggeration (a request): 0
    // flattens the Preview surface, 1000 is the ceiling.
    property real bedMeshExaggeration: 20
    property string selectedLayerEtaText: ""
    property bool pauseAtLayerActive: false
    property int pauseAtLayerCandidate: 0
    property bool pauseAtLayerCanToggle: false
    property bool pauseAtLayerScheduled: false
    property string pauseAtLayerSummary: ""
    property var pauseAtLayerItems: []
    property string pauseAtLayerUnavailableText: ""
    property bool pauseAtLayerHasBaked: false
    property bool pauseAtLayerHasClearable: false
    // The status bar's printer-side readouts (the ruling): the live
    // layer and Z height from the Moonraker observation. The row
    // hides whole while the resolver has no layer. The strip's own
    // discipline: the setProperty-fed values are copied into LOCAL
    // state that the rows' bindings actually track.
    property bool layerReadoutAvailable: false
    property string layerReadoutText: "—"
    property string heightReadoutText: "—"
    // The height's own gate (the panel's catch): it is independently
    // optional — a resolved layer with no height must not render the
    // banned "—" stand-in.
    property bool heightReadoutAvailable: false
    property bool layerHeightRowsVisible: false
    onLayerReadoutAvailableChanged: layerHeightRowsVisible = base.layerReadoutAvailable
    onHeightReadoutAvailableChanged: heightReadoutAvailable = base.heightReadoutAvailable
    onLayerReadoutTextChanged: layerReadout.text = base.layerReadoutText
    onHeightReadoutTextChanged: heightReadout.text = base.heightReadoutText
    // The improve-Eta mirror (the ruling): the hourglass glyph is
    // clickable exactly while the monitor's improve icon is.
    property bool improveEtaAvailable: false

    // The root sizes to the panel so each host shell can place it
    // freely (the panel shell collapses to a strip; the overlay shell
    // corners it).
    width: followerPanel.width
    height: followerPanel.height
    property real horizontalPadding: UM.Theme.getSize("thick_margin").width
    property real verticalPadding: UM.Theme.getSize("thick_margin").height
    property real rowSpacing: UM.Theme.getSize("thin_margin").height
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    property real contentWidth: 300 * screenScaleFactor

    onLoadBusyChanged: loadIndicator.busy = base.loadBusy
    onLoadProgressChanged: loadIndicator.progress = base.loadProgress
    onLoadPhaseChanged: loadIndicator.phase = base.loadPhase
    // The setProperty-fed mesh values drive the sliders imperatively
    // (bindings on dynamically created cards go stale — engine-proven).
    onBedMeshMinimumChanged: meshRangeSlider.minimum = base.bedMeshMinimum
    onBedMeshMaximumChanged: meshRangeSlider.maximum = base.bedMeshMaximum
    onBedMeshThresholdLowChanged: meshRangeSlider.low = base.bedMeshThresholdLow
    onBedMeshThresholdHighChanged: meshRangeSlider.high = base.bedMeshThresholdHigh
    onBedMeshExaggerationChanged: {
        exaggerationSlider.value = base.bedMeshExaggeration;
        exaggerationValueLabel.text = "×" + Math.round(base.bedMeshExaggeration);
    }

    signal loadClicked
    signal pauseClicked
    signal printPauseRequested
    signal improveEtaRequested
    signal bedMeshVisibilityRequested(bool visible)
    signal bedMeshThresholdsRequested(real low, real high)
    signal bedMeshExaggerationRequested(real scale)
    signal pauseAtLayerRequested(int layer)
    signal removePauseAtLayerRequested(int layer)
    signal clearPauseAtLayersRequested

    // THE shared card content: hosted by whichever shell the presenter
    // places it in (the action-panel shell while Cura's panel exists,
    // the corner overlay while Cura's platform is idle and its panel
    // is gone). The gate arrives pre-computed per instance as
    // gateVisible — bindings on setProperty-fed values go stale on
    // this dynamically created component (engine-proven), so the
    // visibility is written imperatively from the change handlers.
    function updateCardGate() {
        followerPanel.visible = base.gateVisible;
    }

    onGateVisibleChanged: updateCardGate()

    // The pause rows live in a STABLE ListModel the coordinator never
    // touches directly: syncPauseRows() diffs the published list into
    // it in place, so the ListView's model identity never changes and
    // the scroll stays put across publishes, adds and removals.
    ListModel {
        id: pauseListModel
        objectName: "moonrakerPauseListModel"
    }

    function syncPauseRows() {
        var incoming = base.pauseAtLayerItems || [];
        var keep = {};
        for (var i = 0; i < incoming.length; i++) {
            keep[incoming[i].layer] = true;
        }
        for (var r = pauseListModel.count - 1; r >= 0; r--) {
            if (!keep[pauseListModel.get(r).layerNo]) {
                pauseListModel.remove(r);
            }
        }
        for (var k = 0; k < incoming.length; k++) {
            var row = incoming[k];
            var payload = {
                "layerNo": row.layer,
                "eta": row.eta,
                "pauseWord": row.state,
                "passed": row.passed === true
            };
            var at = -1;
            for (var f = 0; f < pauseListModel.count; f++) {
                if (pauseListModel.get(f).layerNo === row.layer) {
                    at = f;
                    break;
                }
            }
            if (at === -1) {
                // The published list is sorted; a fresh entry lands
                // at its sorted position among the existing rows.
                var pos = pauseListModel.count;
                for (var s = 0; s < pauseListModel.count; s++) {
                    if (pauseListModel.get(s).layerNo > row.layer) {
                        pos = s;
                        break;
                    }
                }
                pauseListModel.insert(pos, payload);
            } else if (pauseListModel.get(at).eta !== row.eta || pauseListModel.get(at).pauseWord !== row.state || pauseListModel.get(at).passed !== payload.passed) {
                pauseListModel.set(at, payload);
            }
        }
    }

    onPauseAtLayerItemsChanged: syncPauseRows()

    // The strip's state machine (4.3.0): the block's verdicts, the
    // staleness flag and the state word arrive as one setProperty-fed
    // value — the cells are rewritten imperatively on every change,
    // never left to bindings. The absent case names WHICH absence:
    // never-arrived, stale, and not-following are three different
    // facts — one connection claim for all three was a lie.
    function stripSlotText() {
        var b = base.previewBlock;
        if (b === null || b === undefined)
            return "Waiting for the printer";
        if (base.previewBlockStale)
            return "Feed is stale";
        if (b.inactive === true)
            return "Not following";
        if (b.state === "paused")
            return base.stripCanResume ? base.previewEtaText : (base.stripResumeReason.length > 0 ? base.stripResumeReason : "—");
        if (b.state === "printing")
            return base.stripCanPause ? base.previewEtaText : (base.stripPauseReason.length > 0 ? base.stripPauseReason : "—");
        return base.stripPauseReason.length > 0 ? base.stripPauseReason : "—";
    }

    function stripPauseTooltip() {
        var b = base.previewBlock;
        if (b === null || b === undefined)
            return "Waiting for the printer's first live values.";
        if (base.previewBlockStale)
            return "The feed is stale — the strip reads the last live values as '—'.";
        if (b.inactive === true)
            return "The monitor is not following this printer — the strip stays quiet.";
        if (stripPaused) {
            if (base.stripCanResume)
                return "Resume the paused print (Klipper RESUME).";
            return base.stripResumeReasonDetail.length > 0 ? base.stripResumeReasonDetail : base.stripResumeReason;
        }
        if (base.stripCanPause)
            return "Pause the current print immediately (Klipper PAUSE).";
        return base.stripPauseReasonDetail.length > 0 ? base.stripPauseReasonDetail : base.stripPauseReason;
    }

    function updateStrip() {
        stripValid = !base.previewBlockStale && base.previewBlock !== null && base.previewBlock !== undefined && base.previewBlock.inactive !== true;
        stripPaused = stripValid && base.previewBlock.state === "paused";
        // The two wrapped lines (the live request): the hotend and
        // the bed each get their own labelled line, and the slot
        // splits into the countdown and the finish time. A refusal
        // reason (no separator) occupies the countdown line alone.
        stripTemps.text = stripValid ? base.previewBlock.hotend : "—";
        stripBed.text = stripValid ? base.previewBlock.bed : "—";
        var slotText = stripSlotText();
        var parts = slotText.indexOf(" · ") >= 0 ? slotText.split(" · ") : [slotText, ""];
        stripSlot.text = stripValid ? parts[0] : "—";
        stripFinish.text = stripValid ? parts[1] : "";
        stripPauseButton.enabled = stripValid && (stripPaused ? base.stripCanResume : base.stripCanPause);
        stripPauseButton.text = stripPaused ? "Resume print" : "Pause print";
        stripPauseButton.tooltip = stripPauseTooltip();
    }

    onPreviewBlockChanged: updateStrip()
    onPreviewBlockStaleChanged: updateStrip()
    onPreviewEtaTextChanged: updateStrip()

    Component.onCompleted: {
        updateCardGate();
        updateStrip();
    }

    // The panel shell's strip sizing reads these.
    readonly property bool panelVisible: followerPanel.visible
    property real panelWidth: followerPanel.width
    property real panelHeight: followerPanel.height

    Rectangle {
        id: followerPanel
        objectName: "moonrakerPreviewCardPanel"
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        width: base.contentWidth + 2 * base.horizontalPadding
        height: contentColumn.implicitHeight + 2 * base.verticalPadding
        color: UM.Theme.getColor("main_background")
        border.width: UM.Theme.getSize("default_lining").width
        border.color: UM.Theme.getColor("lining")
        radius: UM.Theme.getSize("default_radius").width

        Column {
            id: contentColumn
            anchors {
                left: parent.left
                leftMargin: base.horizontalPadding
                verticalCenter: parent.verticalCenter
            }
            width: base.contentWidth
            spacing: base.rowSpacing

            Cura.IconWithText {
                id: followerTitle
                width: parent.width
                text: "Moonraker Print Follower"
                source: UM.Theme.getIcon("Nozzle")
                font: UM.Theme.getFont("medium_bold")
            }

            Cura.IconWithText {
                id: followerStatus
                width: parent.width
                text: base.activePrinterName + (base.statusText.length > 0 ? " — " + base.statusText : "")
                source: UM.Theme.getIcon(base.statusIconName)
                font: UM.Theme.getFont("default")
            }

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
                onClicked: base.printPauseRequested()
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
                spacing: base.buttonSpacing

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
                                source: Qt.resolvedUrl("Thermometer.svg")
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
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
                                source: Qt.resolvedUrl("Bed.svg")
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
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
                                source: Qt.resolvedUrl("Layer.svg")
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
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
                                source: Qt.resolvedUrl("Hourglass.svg")
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
                                    enabled: base.improveEtaAvailable
                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: base.improveEtaRequested()
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
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
                                source: Qt.resolvedUrl("Clock.svg")
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
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
                                source: Qt.resolvedUrl("Height.svg")
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
                        UM.TooltipArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
                            text: "The printer's current Z height."
                        }
                    }
                }
            }

            Row {
                id: buttons
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                spacing: base.buttonSpacing

                PreviewSecondaryButton {
                    id: followButton
                    // The attach/detach control (the
                    // 2026-09-17 ruling): without a toolpath the
                    // follower has nothing to drive, so the button
                    // hides entirely and the load button takes the
                    // whole row.
                    visible: base.hasToolpath
                    width: Math.round((buttons.width - base.buttonSpacing) * 0.32)
                    height: UM.Theme.getSize("action_button").height
                    text: base.followingPaused ? "Attach" : "Detach"
                    tooltip: base.followingPaused ? "Attach Cura Preview to the live Moonraker print and resume automatic synchronisation." : "Detach Cura Preview from automatic synchronisation while Moonraker status polling continues. This does not pause the printer."
                    enabled: base.followingEnabled || base.followingPaused
                    onClicked: base.pauseClicked()
                }

                PreviewSecondaryButton {
                    id: loadButton
                    width: base.hasToolpath ? buttons.width - base.buttonSpacing - followButton.width : buttons.width
                    height: UM.Theme.getSize("action_button").height
                    text: "Load current print"
                    tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
                    // Non-clickable until the load reaches a terminal state.
                    enabled: !base.loadBusy
                    onClicked: base.loadClicked()
                }
            }

            // The indicator is a SIBLING of the buttons Row, not its
            // third child: a Row lays children side by side, so a
            // full-width indicator inside it painted entirely past the
            // card's right edge and the load feedback was invisible
            // (panel UX P1 — "the second load shows no progress bar").
            // Fed through the card's change handlers: bindings written
            // here do not track the card's setProperty-driven updates
            // (engine-proven), while imperative writes notify and the
            // indicator's inner bindings on its own properties track.
            LoadProgressIndicator {
                id: loadIndicator
                width: parent.width
            }

            UM.Label {
                // The current-layer info slot (the 2026-09-11
                // ruling): filled while the print is active; when it has
                // nothing to say the slot collapses instead of leaving a
                // blank gap before the pause button.
                width: parent.width
                height: base.selectedLayerEtaText.length > 0 ? 36 * screenScaleFactor : 0
                text: base.selectedLayerEtaText.length > 0 ? base.selectedLayerEtaText : " "
                opacity: base.selectedLayerEtaText.length > 0 ? 1.0 : 0.0
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                clip: true
                // The selection readout means nothing without a
                // toolpath (the 2026-09-17 ruling) — it hides with
                // the pause button.
                visible: base.hasToolpath
            }

            PreviewSecondaryButton {
                id: pauseAtLayerButton
                // Hides without a toolpath (the 2026-09-17 ruling:
                // scheduling against an absent layer view is a lie)
                // — with one, it disables until a schedulable layer
                // is selected.
                visible: base.hasToolpath
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                enabled: (base.pauseAtLayerScheduled || base.pauseAtLayerCanToggle) && base.followingEnabled && base.pauseAtLayerActive
                text: base.pauseAtLayerCandidate <= 0 ? "⏸  Pause at end of selected layer" : (base.pauseAtLayerScheduled ? "Remove pause after layer " + base.pauseAtLayerCandidate : "⏸  Enable pause at end of layer " + base.pauseAtLayerCandidate)
                tooltip: base.pauseAtLayerScheduled ? "Remove the scheduled end-of-layer PAUSE." : (base.pauseAtLayerCanToggle ? "Call the Klipper PAUSE macro once this layer has finished and Moonraker advances to the following layer." : "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE.")
                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            UM.Label {
                // The scheduling hint lives only while a toolpath
                // exists; with the card visible in every state, a
                // permanent empty slot would read as a gap on an
                // empty scene.
                width: parent.width
                height: base.hasToolpath ? 36 * screenScaleFactor : 0
                text: (!base.pauseAtLayerScheduled && !base.pauseAtLayerCanToggle && base.pauseAtLayerUnavailableText.length > 0) ? "Can't schedule: " + base.pauseAtLayerUnavailableText : (base.hasToolpath ? "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE." : "")
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                wrapMode: Text.WordWrap
                elide: Text.ElideRight
                clip: true
            }

            Column {
                id: scheduledPauseList
                // The improve-Eta path (the ruling): the index alone
                // reveals the baked rows, no toolpath needed — manual
                // rows can't exist without one, so the baked flag
                // keeps a stale manual schedule out of the light view.
                visible: base.followingEnabled && base.pauseAtLayerActive && base.pauseAtLayerItems.length > 0 && (base.hasToolpath || base.pauseAtLayerHasBaked)
                width: parent.width
                height: visible ? implicitHeight : 0
                spacing: 2 * screenScaleFactor

                UM.Label {
                    width: parent.width
                    text: "Enabled pauses"
                    color: UM.Theme.getColor("text")
                    font: UM.Theme.getFont("default_bold")
                }

                Item {
                    width: parent.width
                    // Five visible entries at most (the
                    // ruling): a long schedule scrolls instead of
                    // growing the card past the viewport.
                    height: Math.min(base.pauseAtLayerItems.length, 5) * (UM.Theme.getSize("action_button").height + scheduledPauseList.spacing) - scheduledPauseList.spacing

                    ListView {
                        id: pauseListView
                        anchors.fill: parent
                        clip: true
                        spacing: scheduledPauseList.spacing
                        interactive: contentHeight > height
                        boundsBehavior: Flickable.StopAtBounds
                        // The STABLE model: the coordinator re-publishes
                        // the list every cycle (~2.5 s) with fresh ETA
                        // strings — handing that straight to the view
                        // replaced the model on every poll and the view
                        // jumped (the live reports). The card syncs the
                        // rows IN PLACE here, so the model never
                        // changes identity and the scroll never moves
                        // on its own.
                        model: pauseListModel
                        delegate: Row {
                            id: pauseRow
                            width: scheduledPauseList.width
                            height: UM.Theme.getSize("action_button").height
                            spacing: base.buttonSpacing
                            // The roles are DIRECT delegate-context
                            // properties for a ListModel (the canonical
                            // idiom) — `modelData` is a JS-array-model
                            // concept and read undefined (the live
                            // report: every row read layer 0), and
                            // `model.layer` crashed the pinned
                            // container's engine at load. The layer and
                            // state ROLES carry non-colliding names
                            // (layerNo, pauseWord): bare `layer` and
                            // `state` hit Qt's built-in Item.layer /
                            // Item.state properties on some engines,
                            // which shadow the roles and read
                            // NaN / "" — every row then shows layer 0
                            // (the live report) while eta and passed
                            // still resolve.
                            property int pauseLayer: Number(layerNo)
                            property string pauseEta: String(eta || "")
                            // "scheduled" | "fired" | "failed" | "timed_out" |
                            // "baked" — a missed pause STAYS listed, restyled
                            // in the error colour (the verified-pause-only
                            // ruling); a baked pause is read-only (the
                            // ruling).
                            property string pauseState: String(pauseWord || "scheduled")
                            readonly property bool pauseMissed: pauseState === "failed" || pauseState === "timed_out"
                            readonly property bool pauseBaked: pauseState === "baked"
                            readonly property bool pausePassed: passed === true || pauseState === "passed"

                            UM.Label {
                                width: Math.max(0, parent.width - removePauseButton.width - parent.spacing)
                                height: parent.height
                                text: "End of layer " + parent.pauseLayer + (!parent.pausePassed && parent.pauseEta.length > 0 ? " · " + parent.pauseEta : "") + (parent.pauseMissed ? " — pause not taken" : "") + (parent.pauseBaked ? (parent.pausePassed ? " — baked · passed" : " — baked") : (pauseState === "passed" ? " — passed" : ""))
                                color: parent.pauseMissed ? UM.Theme.getColor("error") : (parent.pausePassed ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text"))
                                font: UM.Theme.getFont("default")
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }

                            UM.Label {
                                id: removePauseButton
                                // The narrow red ✕ (the 2026-09-16
                                // ruling): a glyph, not a chrome
                                // button — it buys the row text the
                                // room the clock-time ETA needs. It
                                // NEVER hides (the no-reflow rule): a
                                // baked pause dims it and the click
                                // does nothing.
                                width: parent.height
                                height: parent.height
                                text: "✕"
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                color: parent.pauseBaked ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("error")
                                font: UM.Theme.getFont("medium_bold")
                                MouseArea {
                                    anchors.fill: parent
                                    // The row's properties, not the
                                    // label's — parent here is the
                                    // glyph, which carries neither
                                    // (the dead-click report).
                                    enabled: !pauseRow.pauseBaked
                                    hoverEnabled: true
                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: base.removePauseAtLayerRequested(pauseRow.pauseLayer)
                                }
                            }
                        }
                    }

                    // Scroll affordances, the whats-new idiom: small
                    // blue chevrons centred over the list — an up
                    // arrow near the top while more content is above,
                    // a down arrow near the bottom while more content
                    // is below (the ruling). They are
                    // SIBLINGS of the ListView, overlaying it.
                    UM.Label {
                        text: "↑"
                        visible: pauseListView.height > 0 && pauseListView.contentY > 2
                        anchors.top: pauseListView.top
                        anchors.horizontalCenter: pauseListView.horizontalCenter
                        anchors.topMargin: 4 * screenScaleFactor
                        color: UM.Theme.getColor("primary")
                        font: UM.Theme.getFont("medium_bold")
                    }
                    UM.Label {
                        text: "↓"
                        visible: pauseListView.height > 0 && pauseListView.contentY < pauseListView.contentHeight - pauseListView.height - 2
                        anchors.bottom: pauseListView.bottom
                        anchors.horizontalCenter: pauseListView.horizontalCenter
                        anchors.bottomMargin: 4 * screenScaleFactor
                        color: UM.Theme.getColor("primary")
                        font: UM.Theme.getFont("medium_bold")
                    }
                }

                PreviewSecondaryButton {
                    // Hides while nothing is clearable — a baked-only
                    // list (the improve-Eta path) has no manual rows
                    // to remove (the 2026-09-17 ruling).
                    visible: base.pauseAtLayerHasClearable
                    width: parent.width
                    height: UM.Theme.getSize("action_button").height
                    text: "Clear all pauses"
                    tooltip: "Remove every scheduled end-of-layer PAUSE for the current print."
                    onClicked: base.clearPauseAtLayersRequested()
                }
            }

            Column {
                // The legend collapses when the mesh is hidden (the
                // 2026-09-16 ruling): the card reflows instead of
                // keeping a faded gap.
                visible: base.bedMeshAvailable && base.bedMeshVisible
                width: parent.width
                spacing: 2 * screenScaleFactor

                BedMeshRangeSlider {
                    id: meshRangeSlider
                    width: parent.width
                    enabled: base.bedMeshAvailable
                    onWindowAdjusted: base.bedMeshThresholdsRequested(low, high)
                }

                Row {
                    width: parent.width
                    spacing: base.buttonSpacing
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
                            var centre = leftPadding + visualPosition * availableWidth;
                            return Math.abs(mouseX - centre) <= 10 * screenScaleFactor;
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
                        Keys.onUpPressed: increase()
                        Keys.onDownPressed: decrease()
                        Keys.onRightPressed: increase()
                        Keys.onLeftPressed: decrease()
                        onValueChanged: {
                            exaggerationValueLabel.text = "×" + Math.round(value);
                            base.bedMeshExaggerationRequested(value);
                        }
                    }
                    UM.Label {
                        id: exaggerationValueLabel
                        width: 44 * screenScaleFactor
                        height: exaggerationSlider.implicitHeight
                        text: "×" + Math.round(base.bedMeshExaggeration)
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
                        text: "Low " + base.bedMeshMinimumText
                        // Primary content (the 2026-09-16 ruling):
                        // full text colour, not inactive grey.
                        color: UM.Theme.getColor("text")
                        font: UM.Theme.getFont("default")
                    }
                    UM.Label {
                        width: parent.width / 2
                        text: "High " + base.bedMeshMaximumText
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
                enabled: base.bedMeshAvailable
                text: base.bedMeshVisible ? "Hide bed mesh" : "Show bed mesh"
                tooltip: "Show the active Klipper bed mesh as a coloured 3D surface on Cura's build plate" + (base.bedMeshRangeText.length > 0 ? " (" + base.bedMeshRangeText + ")." : ".")
                onClicked: base.bedMeshVisibilityRequested(!base.bedMeshVisible)
            }
        }
    }
}
