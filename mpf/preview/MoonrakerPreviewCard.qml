import QtQuick 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura
import "../widgets"

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
    // The replace prompt's visibility, driven by the coordinator: the
    // dialog owns no state of its own, so there is exactly one owner of
    // "is the question up" and a scenario can see it in the tree.
    property bool replacePromptVisible: false
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
    signal replaceConfirmed
    signal replaceCancelled

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

    // The prompt follows the card's own gate as well as the model: the
    // card is instantiated TWICE (the action-panel shell and the corner
    // overlay) and both receive every published value, so an ungated
    // prompt would put two identical dialogs up at once. Gated, only
    // the card the user can actually see asks.
    function updateReplacePrompt() {
        replacePromptDialog.visible = base.replacePromptVisible && base.gateVisible;
    }

    onReplacePromptVisibleChanged: updateReplacePrompt()
    onGateVisibleChanged: {
        updateCardGate();
        updateReplacePrompt();
    }

    // The replace prompt's body is ReplacePromptDialog.qml; this card
    // keeps the gate that decides WHICH copy asks and the two answers.
    ReplacePromptDialog {
        id: replacePromptDialog
        onReplaceConfirmed: base.replaceConfirmed()
        onReplaceCancelled: base.replaceCancelled()
    }

    Component.onCompleted: updateCardGate()

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

            // The live strip: the pause/resume control and the two
            // readout rows, with the cell state machine that feeds them.
            PreviewStatusStrip {
                width: parent.width
                spacing: base.rowSpacing
                previewBlock: base.previewBlock
                previewBlockStale: base.previewBlockStale
                previewEtaText: base.previewEtaText
                stripCanPause: base.stripCanPause
                stripCanResume: base.stripCanResume
                stripPauseReason: base.stripPauseReason
                stripResumeReason: base.stripResumeReason
                stripPauseReasonDetail: base.stripPauseReasonDetail
                stripResumeReasonDetail: base.stripResumeReasonDetail
                improveEtaAvailable: base.improveEtaAvailable
                layerReadoutAvailable: base.layerReadoutAvailable
                layerReadoutText: base.layerReadoutText
                heightReadoutText: base.heightReadoutText
                heightReadoutAvailable: base.heightReadoutAvailable
                buttonSpacing: base.buttonSpacing
                onPrintPauseRequested: base.printPauseRequested()
                onImproveEtaRequested: base.improveEtaRequested()
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
                    enabled: base.followingEnabled || base.followingPaused
                    onClicked: base.pauseClicked()
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: base.followingPaused ? "Attach Cura Preview to the live Moonraker print and resume automatic synchronisation." : "Detach Cura Preview from automatic synchronisation while Moonraker status polling continues. This does not pause the printer."
                    }
                }

                PreviewSecondaryButton {
                    id: loadButton
                    width: base.hasToolpath ? buttons.width - base.buttonSpacing - followButton.width : buttons.width
                    height: UM.Theme.getSize("action_button").height
                    text: "Load current print"
                    // Non-clickable until the load reaches a terminal state.
                    enabled: !base.loadBusy
                    onClicked: base.loadClicked()
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
                    }
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

            // The end-of-layer pause section: the toggle, the hint and
            // the scheduled rows, with its own stable list model.
            PauseAtLayerSection {
                width: parent.width
                spacing: base.rowSpacing
                hasToolpath: base.hasToolpath
                followingEnabled: base.followingEnabled
                pauseAtLayerActive: base.pauseAtLayerActive
                pauseAtLayerScheduled: base.pauseAtLayerScheduled
                pauseAtLayerCanToggle: base.pauseAtLayerCanToggle
                pauseAtLayerCandidate: base.pauseAtLayerCandidate
                pauseAtLayerUnavailableText: base.pauseAtLayerUnavailableText
                pauseAtLayerItems: base.pauseAtLayerItems
                pauseAtLayerHasBaked: base.pauseAtLayerHasBaked
                pauseAtLayerHasClearable: base.pauseAtLayerHasClearable
                buttonSpacing: base.buttonSpacing
                onPauseAtLayerRequested: function (layer) {
                    base.pauseAtLayerRequested(layer);
                }
                onRemovePauseAtLayerRequested: function (layer) {
                    base.removePauseAtLayerRequested(layer);
                }
                onClearPauseAtLayersRequested: base.clearPauseAtLayersRequested()
            }

            // The bed-mesh display section: the shared range filter,
            // the exaggeration and the show/hide toggle.
            BedMeshLegend {
                width: parent.width
                spacing: base.rowSpacing
                bedMeshAvailable: base.bedMeshAvailable
                bedMeshVisible: base.bedMeshVisible
                bedMeshRangeText: base.bedMeshRangeText
                bedMeshMinimumText: base.bedMeshMinimumText
                bedMeshMaximumText: base.bedMeshMaximumText
                bedMeshMinimum: base.bedMeshMinimum
                bedMeshMaximum: base.bedMeshMaximum
                bedMeshThresholdLow: base.bedMeshThresholdLow
                bedMeshThresholdHigh: base.bedMeshThresholdHigh
                bedMeshExaggeration: base.bedMeshExaggeration
                buttonSpacing: base.buttonSpacing
                onBedMeshVisibilityRequested: function (visible) {
                    base.bedMeshVisibilityRequested(visible);
                }
                onBedMeshThresholdsRequested: function (low, high) {
                    base.bedMeshThresholdsRequested(low, high);
                }
                onBedMeshExaggerationRequested: function (scale) {
                    base.bedMeshExaggerationRequested(scale);
                }
            }
        }
    }
}
