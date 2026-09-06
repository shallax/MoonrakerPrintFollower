import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    id: base
    objectName: "moonrakerPreviewActionPanelControls"

    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool configuredForFollowing: false
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

    // ActionPanelWidget already inserts a default margin between saveButton
    // extension components. Reserve one further default margin inside this
    // component so the visual gap from Cura's Post Processing </> button to
    // our card matches the gap from our card to Cura's native action panel.
    property real externalGap: UM.Theme.getSize("default_margin").width
    property real horizontalPadding: UM.Theme.getSize("thick_margin").width
    property real verticalPadding: UM.Theme.getSize("thick_margin").height
    property real rowSpacing: UM.Theme.getSize("thin_margin").height
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    property real contentWidth: 260 * screenScaleFactor

    signal loadClicked()
    signal pauseClicked()
    signal bedMeshVisibilityRequested(bool visible)
    signal pauseAtLayerRequested(int layer)

    visible: previewStageActive && configuredForFollowing && CuraApplication.platformActivity
    width: visible ? externalGap + followerPanel.width : 0
    height: visible ? followerPanel.height : 0

    Rectangle
    {
        id: followerPanel
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter

        // Cura vertically centres saveButton extensions around the native output
        // button, which sits two thick margins above ActionPanelWidget's bottom.
        // A multi-row extension card therefore hangs too low if simply centred.
        // Offset it so our card bottom lines up with Cura's native action card.
        anchors.verticalCenterOffset: (2 * base.verticalPadding) - (height / 2)

        width: base.contentWidth + 2 * base.horizontalPadding
        height: contentColumn.implicitHeight + 2 * base.verticalPadding
        color: UM.Theme.getColor("main_background")
        border.width: UM.Theme.getSize("default_lining").width
        border.color: UM.Theme.getColor("lining")
        radius: UM.Theme.getSize("default_radius").width

        Column
        {
            id: contentColumn
            anchors
            {
                left: parent.left
                leftMargin: base.horizontalPadding
                verticalCenter: parent.verticalCenter
            }
            width: base.contentWidth
            spacing: base.rowSpacing

            Cura.IconWithText
            {
                id: followerTitle
                width: parent.width
                text: "Moonraker Print Follower"
                source: UM.Theme.getIcon("Nozzle")
                font: UM.Theme.getFont("medium_bold")
            }

            Cura.IconWithText
            {
                id: followerStatus
                width: parent.width
                text: base.activePrinterName + (base.statusText.length > 0 ? " — " + base.statusText : "")
                source: UM.Theme.getIcon(base.statusIconName)
                font: UM.Theme.getFont("default")
            }

            Row
            {
                id: buttons
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                spacing: base.buttonSpacing

                Cura.SecondaryButton
                {
                    id: followButton
                    visible: base.hasToolpath && (base.followingEnabled || base.followingPaused)
                    width: (buttons.width - base.buttonSpacing) / 2
                    height: UM.Theme.getSize("action_button").height
                    text: base.followingPaused ? "Resume" : "Pause"
                    tooltip: base.followingPaused
                        ? "Resume synchronising Cura Preview with the current Moonraker print."
                        : "Pause Cura Preview synchronisation while Moonraker status polling continues."
                    fixedWidthMode: true
                    enabled: base.hasToolpath && (base.followingEnabled || base.followingPaused)
                    onClicked: base.pauseClicked()
                }

                Cura.SecondaryButton
                {
                    id: loadButton
                    width: followButton.visible ? (buttons.width - base.buttonSpacing) / 2 : buttons.width
                    height: UM.Theme.getSize("action_button").height
                    text: "Load current print"
                    tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
                    fixedWidthMode: true
                    onClicked: base.loadClicked()
                }
            }

            Cura.SecondaryButton
            {
                id: bedMeshButton
                visible: base.bedMeshAvailable
                width: parent.width
                height: visible ? UM.Theme.getSize("action_button").height : 0
                text: base.bedMeshVisible ? "Hide bed mesh" : "Show bed mesh"
                tooltip: "Show the active Klipper bed mesh as a coloured 3D surface on Cura's build plate"
                    + (base.bedMeshRangeText.length > 0 ? " (" + base.bedMeshRangeText + ")." : ".")
                fixedWidthMode: true
                onClicked: base.bedMeshVisibilityRequested(!base.bedMeshVisible)
            }

            UM.Label
            {
                width: parent.width
                height: 36 * screenScaleFactor
                text: base.selectedLayerEtaText.length > 0 ? base.selectedLayerEtaText : " "
                opacity: base.selectedLayerEtaText.length > 0 ? 1.0 : 0.0
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                clip: true
            }

            Cura.SecondaryButton
            {
                id: pauseAtLayerButton
                visible: base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive
                width: parent.width
                height: visible ? UM.Theme.getSize("action_button").height : 0
                enabled: base.pauseAtLayerCanToggle
                text: base.pauseAtLayerCandidate <= 0
                    ? "Pause at selected layer"
                    : (base.pauseAtLayerScheduled
                        ? "Remove pause at layer " + base.pauseAtLayerCandidate
                        : (base.pauseAtLayerCanToggle
                            ? "Enable pause at layer " + base.pauseAtLayerCandidate
                            : "Layer " + base.pauseAtLayerCandidate + " already reached"))
                tooltip: base.pauseAtLayerCanToggle
                    ? (base.pauseAtLayerScheduled
                        ? "Remove the scheduled PAUSE for this future layer."
                        : "Call the Klipper PAUSE macro when Moonraker reaches this future layer.")
                    : "Scroll Cura Preview to a layer ahead of the current print layer to schedule PAUSE."
                fixedWidthMode: true
                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            UM.Label
            {
                visible: base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive
                width: parent.width
                height: visible ? 20 * screenScaleFactor : 0
                text: base.pauseAtLayerSummary.length > 0 ? base.pauseAtLayerSummary : " "
                opacity: base.pauseAtLayerSummary.length > 0 ? 1.0 : 0.0
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                clip: true
            }

            Column
            {
                visible: base.bedMeshAvailable
                opacity: base.bedMeshVisible ? 1.0 : 0.0
                enabled: base.bedMeshVisible
                width: parent.width
                height: implicitHeight
                spacing: 2 * screenScaleFactor

                Rectangle
                {
                    width: parent.width
                    height: 8 * screenScaleFactor
                    radius: 2 * screenScaleFactor
                    gradient: Gradient
                    {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.00; color: "#1a47f2" }
                        GradientStop { position: 0.25; color: "#00b8ff" }
                        GradientStop { position: 0.50; color: "#33db61" }
                        GradientStop { position: 0.75; color: "#ffd11f" }
                        GradientStop { position: 1.00; color: "#eb291f" }
                    }
                }

                Row
                {
                    width: parent.width
                    UM.Label
                    {
                        width: parent.width / 2
                        text: "Low " + base.bedMeshMinimumText
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                    UM.Label
                    {
                        width: parent.width / 2
                        text: "High " + base.bedMeshMaximumText
                        horizontalAlignment: Text.AlignRight
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                    }
                }

                UM.Label
                {
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
