import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    id: base
    objectName: "moonrakerEmptyPreviewLoadControl"
    anchors.fill: parent
    z: 10000
    visible: previewStageActive && configuredForFollowing && !CuraApplication.platformActivity

    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool configuredForFollowing: false
    property bool hasToolpath: false
    property string activePrinterName: ""
    property string statusText: ""
    property string statusIconName: "Information"

    signal loadClicked()

    Rectangle
    {
        id: followerPanel
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: UM.Theme.getSize("thick_margin").width * 2
        anchors.bottomMargin: UM.Theme.getSize("thick_margin").height

        property real horizontalPadding: UM.Theme.getSize("thick_margin").width
        property real verticalPadding: UM.Theme.getSize("thick_margin").height
        property real rowSpacing: UM.Theme.getSize("thin_margin").height
        property real contentWidth: 260 * screenScaleFactor

        width: contentWidth + 2 * horizontalPadding
        height: contentColumn.implicitHeight + 2 * verticalPadding
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
                leftMargin: followerPanel.horizontalPadding
                verticalCenter: parent.verticalCenter
            }
            width: followerPanel.contentWidth
            spacing: followerPanel.rowSpacing

            Cura.IconWithText
            {
                width: parent.width
                text: "Moonraker Print Follower"
                source: UM.Theme.getIcon("Nozzle")
                font: UM.Theme.getFont("medium_bold")
            }

            Cura.IconWithText
            {
                width: parent.width
                text: base.activePrinterName + (base.statusText.length > 0 ? " — " + base.statusText : "")
                source: UM.Theme.getIcon(base.statusIconName)
                font: UM.Theme.getFont("default")
            }

            Cura.SecondaryButton
            {
                id: loadButton
                width: parent.width
                height: UM.Theme.getSize("action_button").height
                text: "Load print"
                tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
                fixedWidthMode: true
                onClicked: base.loadClicked()
            }
        }
    }
}
