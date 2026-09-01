import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    id: base
    objectName: "moonrakerEmptyPreviewLoadControl"

    // This placement exists only for an otherwise empty Preview. Cura hides its
    // entire ActionPanelWidget when platformActivity is false, so the official
    // saveButton extension row is unavailable in that state.
    anchors.fill: parent
    z: 10000
    visible: previewStageActive && !CuraApplication.platformActivity

    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool hasToolpath: false

    signal loadClicked()

    Cura.SecondaryButton
    {
        id: loadButton
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: UM.Theme.getSize("thick_margin").width * 2
        anchors.bottomMargin: UM.Theme.getSize("thick_margin").height * 2
        height: UM.Theme.getSize("action_button").height
        text: "Load current print"
        tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
        fixedWidthMode: false
        onClicked: base.loadClicked()
    }
}
