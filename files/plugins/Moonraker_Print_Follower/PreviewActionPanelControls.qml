import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    id: base
    objectName: "moonrakerPreviewActionPanelControls"

    // Cura reparents this component into ActionPanelWidget's official
    // additionalComponentsRow. That row is anchored immediately to the left of
    // Slice/Save/Upload and also contains controls from other plugins, so Cura
    // handles spacing and prevents collisions for us.
    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool hasToolpath: false

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
