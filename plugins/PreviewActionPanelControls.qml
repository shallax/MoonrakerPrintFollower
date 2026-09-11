import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item {
    id: base
    objectName: "moonrakerPreviewActionPanelControls"

    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool configuredForFollowing: false
    // Declared so the bindings below exist from creation: undeclared
    // dynamic names read as undefined at load time and the bindings
    // are dropped before setProperty can ever reach them.
    property bool loadBusy: false
    property real loadProgress: -1
    property string loadPhase: ""
    property bool hasToolpath: false
    property string activePrinterName: ""
    property string statusText: ""
    property string statusIconName: "Information"
    property bool bedMeshAvailable: false
    property bool bedMeshVisible: true
    property string bedMeshRangeText: ""
    property string bedMeshMinimumText: ""
    property string bedMeshMaximumText: ""
    property string selectedLayerEtaText: ""
    property bool pauseAtLayerActive: false
    property int pauseAtLayerCandidate: 0
    property bool pauseAtLayerCanToggle: false
    property bool pauseAtLayerScheduled: false
    property string pauseAtLayerSummary: ""
    property var pauseAtLayerItems: []
    property string pauseAtLayerUnavailableText: ""

    // ActionPanelWidget already inserts a default margin between saveButton
    // extension components. Reserve one further default margin inside this
    // component so the visual gap from Cura's Post Processing </> button to
    // our card matches the gap from our card to Cura's native action panel.
    property real externalGap: UM.Theme.getSize("default_margin").width
    property real horizontalPadding: UM.Theme.getSize("thick_margin").width
    property real verticalPadding: UM.Theme.getSize("thick_margin").height
    property real rowSpacing: UM.Theme.getSize("thin_margin").height
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    property real contentWidth: 300 * screenScaleFactor

    signal loadClicked
    signal pauseClicked
    signal bedMeshVisibilityRequested(bool visible)
    signal pauseAtLayerRequested(int layer)
    signal removePauseAtLayerRequested(int layer)
    signal clearPauseAtLayersRequested

    visible: previewStageActive && configuredForFollowing && CuraApplication.platformActivity
    width: visible ? externalGap + followerPanel.width : 0
    // Cura's saveButton row centres its components on a line two thick
    // margins above the action panel's bottom. If this extension
    // reported the card's full height, the row would grow to it and
    // Cura's own components (the Post Processing button) would centre
    // far above the bottom. Report a short strip instead — the visible
    // card anchors to its bottom and overflows upward — so the row
    // stays small and everything docks to the bottom. Four thick
    // margins puts the strip's bottom edge exactly on the action
    // panel's bottom, keeping the card level with the Upload card.
    height: visible ? 4 * base.verticalPadding : 0

    Rectangle {
        id: followerPanel
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

            Row {
                id: buttons
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                spacing: base.buttonSpacing

                PreviewSecondaryButton {
                    id: followButton
                    // NOT gated on hasToolpath: the follower attaches
                    // at the view swap, but hasToolpath only flips once
                    // the model finishes RENDERING — the button used to
                    // wait for the render (the author's "Detach takes a
                    // long time" report). The follower null-checks the
                    // view on every drive, so attaching early is safe.
                    // NO-REFLOW RULE: never hidden — it disables instead
                    // of vanishing when following changes state.
                    width: Math.round((buttons.width - base.buttonSpacing) * 0.32)
                    height: UM.Theme.getSize("action_button").height
                    text: base.followingPaused ? "Attach" : "Detach"
                    tooltip: base.followingPaused ? "Attach Cura Preview to the live Moonraker print and resume automatic synchronisation." : "Detach Cura Preview from automatic synchronisation while Moonraker status polling continues. This does not pause the printer."
                    enabled: base.followingEnabled || base.followingPaused
                    onClicked: base.pauseClicked()
                }

                PreviewSecondaryButton {
                    id: loadButton
                    width: buttons.width - base.buttonSpacing - followButton.width
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
            LoadProgressIndicator {
                width: parent.width
                busy: base.loadBusy
                progress: base.loadProgress
                phase: base.loadPhase
            }

            PreviewSecondaryButton {
                id: bedMeshButton
                // NO-REFLOW RULE: never hidden — it disables when the
                // loaded job has no mesh.
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                enabled: base.bedMeshAvailable
                text: base.bedMeshVisible ? "Hide bed mesh" : "Show bed mesh"
                tooltip: "Show the active Klipper bed mesh as a coloured 3D surface on Cura's build plate" + (base.bedMeshRangeText.length > 0 ? " (" + base.bedMeshRangeText + ")." : ".")
                onClicked: base.bedMeshVisibilityRequested(!base.bedMeshVisible)
            }

            UM.Label {
                // The current-layer info slot (the author's 2026-09-11
                // ruling): filled while the print is active; when it has
                // nothing to say the slot collapses instead of leaving a
                // blank gap between the bed-mesh and pause buttons.
                width: parent.width
                height: base.selectedLayerEtaText.length > 0 ? 36 * screenScaleFactor : 0
                text: base.selectedLayerEtaText.length > 0 ? base.selectedLayerEtaText : " "
                opacity: base.selectedLayerEtaText.length > 0 ? 1.0 : 0.0
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                clip: true
            }

            PreviewSecondaryButton {
                id: pauseAtLayerButton
                // NO-REFLOW RULE: never hidden — it disables until a
                // schedulable layer is selected.
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                enabled: (base.pauseAtLayerScheduled || base.pauseAtLayerCanToggle) && base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive
                text: base.pauseAtLayerCandidate <= 0 ? "⏸  Pause at end of selected layer" : (base.pauseAtLayerScheduled ? "Remove pause after layer " + base.pauseAtLayerCandidate : "⏸  Enable pause at end of layer " + base.pauseAtLayerCandidate)
                tooltip: base.pauseAtLayerScheduled ? "Remove the scheduled end-of-layer PAUSE." : (base.pauseAtLayerCanToggle ? "Call the Klipper PAUSE macro once this layer has finished and Moonraker advances to the following layer." : "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE.")
                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            UM.Label {
                // NO-REFLOW RULE: a permanent single-line slot — the
                // text fills it with the error reason, or the scheduling
                // hint while a toolpath exists, never resizes it (the
                // UX panel: a blank slot read as broken spacing).
                width: parent.width
                height: 36 * screenScaleFactor
                text: (!base.pauseAtLayerScheduled && !base.pauseAtLayerCanToggle && base.pauseAtLayerUnavailableText.length > 0) ? "Can't schedule: " + base.pauseAtLayerUnavailableText : (base.hasToolpath ? "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE." : "")
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                wrapMode: Text.WordWrap
                elide: Text.ElideRight
                clip: true
            }

            Column {
                id: scheduledPauseList
                visible: base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive && base.pauseAtLayerItems.length > 0
                width: parent.width
                height: visible ? implicitHeight : 0
                spacing: 2 * screenScaleFactor

                UM.Label {
                    width: parent.width
                    text: "Enabled pauses"
                    color: UM.Theme.getColor("text")
                    font: UM.Theme.getFont("default_bold")
                }

                Repeater {
                    model: base.pauseAtLayerItems
                    delegate: Row {
                        width: scheduledPauseList.width
                        height: UM.Theme.getSize("action_button").height
                        spacing: base.buttonSpacing
                        property int pauseLayer: Number(modelData.layer)
                        property string pauseEta: String(modelData.eta || "")

                        UM.Label {
                            width: Math.max(0, parent.width - removePauseButton.width - parent.spacing)
                            height: parent.height
                            text: "End of layer " + parent.pauseLayer + (parent.pauseEta.length > 0 ? " · " + parent.pauseEta : "")
                            color: UM.Theme.getColor("text")
                            font: UM.Theme.getFont("default")
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }

                        PreviewSecondaryButton {
                            id: removePauseButton
                            width: 88 * screenScaleFactor
                            height: parent.height
                            text: "Remove"
                            tooltip: "Remove the scheduled PAUSE after layer " + parent.pauseLayer + "."
                            onClicked: base.removePauseAtLayerRequested(parent.pauseLayer)
                        }
                    }
                }

                PreviewSecondaryButton {
                    width: parent.width
                    height: UM.Theme.getSize("action_button").height
                    text: "Clear all pauses"
                    tooltip: "Remove every scheduled end-of-layer PAUSE for the current print."
                    onClicked: base.clearPauseAtLayersRequested()
                }
            }

            Column {
                // NO-REFLOW RULE: the legend keeps its space — it fades
                // instead of vanishing when the mesh state flips (the
                // card used to grow +59 px when the mesh arrived).
                opacity: base.bedMeshAvailable && base.bedMeshVisible ? 1.0 : 0.0
                enabled: base.bedMeshAvailable && base.bedMeshVisible
                width: parent.width
                height: implicitHeight
                spacing: 2 * screenScaleFactor

                Rectangle {
                    width: parent.width
                    height: 8 * screenScaleFactor
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

                Row {
                    width: parent.width
                    UM.Label {
                        width: parent.width / 2
                        text: "Low " + base.bedMeshMinimumText
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                    UM.Label {
                        width: parent.width / 2
                        text: "High " + base.bedMeshMaximumText
                        horizontalAlignment: Text.AlignRight
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                }

                UM.Label {
                    width: parent.width
                    text: "Neon orange outline = Klipper mesh bounds; outside = extrapolated"
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default_italic")
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
