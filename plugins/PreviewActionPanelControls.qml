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
    property bool hasToolpath: false
    property string statusText: ""

    signal loadClicked()
    signal pauseClicked()

    visible: previewStageActive && CuraApplication.platformActivity
    width: visible ? controls.childrenRect.width : 0
    height: visible ? UM.Theme.getSize("action_button").height : 0

    Row
    {
        id: controls
        height: UM.Theme.getSize("action_button").height
        spacing: UM.Theme.getSize("default_margin").width

        UM.Label
        {
            id: followerStatus
            visible: base.statusText.length > 0
            height: parent.height
            verticalAlignment: Text.AlignVCenter
            text: base.statusText
            elide: Text.ElideRight
            width: visible ? Math.min(180 * screenScaleFactor, implicitWidth) : 0
        }

        Cura.SecondaryButton
        {
            id: followButton
            visible: base.hasToolpath
            height: parent.height
            text: base.followingPaused ? "Resume following" : "Pause following"
            tooltip: base.followingPaused
                ? "Resume synchronising Cura Preview with the current Moonraker print."
                : "Pause Cura Preview synchronisation while Moonraker status polling continues."
            fixedWidthMode: false
            enabled: base.hasToolpath && (base.followingEnabled || base.followingPaused)
            onClicked: base.pauseClicked()
        }

        Cura.SecondaryButton
        {
            id: loadButton
            height: parent.height
            text: "Load current print"
            tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
            fixedWidthMode: false
            onClicked: base.loadClicked()
        }
    }
}
