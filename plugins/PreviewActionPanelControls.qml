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

    // ActionPanelWidget already inserts a default margin between saveButton
    // extension components. Reserve one further default margin inside this
    // component so the visual gap from Cura's Post Processing </> button to
    // our card matches the gap from our card to Cura's native action panel.
    property real externalGap: UM.Theme.getSize("default_margin").width
    property real horizontalPadding: UM.Theme.getSize("thick_margin").width
    property real verticalPadding: UM.Theme.getSize("thick_margin").height
    property real rowSpacing: UM.Theme.getSize("thin_margin").height
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    property real followButtonWidth: 92 * screenScaleFactor
    property real loadButtonWidth: 120 * screenScaleFactor
    property real contentWidth: Math.max(260 * screenScaleFactor, followButtonWidth + buttonSpacing + loadButtonWidth)

    signal loadClicked()
    signal pauseClicked()

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
                    width: base.followButtonWidth
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
                    width: base.loadButtonWidth
                    height: UM.Theme.getSize("action_button").height
                    text: "Load print"
                    tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
                    fixedWidthMode: true
                    onClicked: base.loadClicked()
                }
            }
        }
    }
}
