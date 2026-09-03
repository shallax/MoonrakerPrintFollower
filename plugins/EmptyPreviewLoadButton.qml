import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    id: base
    objectName: "moonrakerEmptyPreviewLoadControl"
    anchors.fill: parent
    z: 10000
    visible: previewStageActive && !CuraApplication.platformActivity

    property bool previewStageActive: false
    property bool followingPaused: false
    property bool followingEnabled: false
    property bool hasToolpath: false
    property string statusText: ""

    signal loadClicked()

    Column
    {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: UM.Theme.getSize("thick_margin").width * 2
        anchors.bottomMargin: UM.Theme.getSize("thick_margin").height * 2
        spacing: UM.Theme.getSize("default_margin").height

        UM.Label
        {
            anchors.right: parent.right
            visible: base.statusText.length > 0
            text: base.statusText
            elide: Text.ElideRight
            width: visible ? Math.min(240 * screenScaleFactor, implicitWidth) : 0
            horizontalAlignment: Text.AlignRight
        }

        Cura.SecondaryButton
        {
            id: loadButton
            anchors.right: parent.right
            height: UM.Theme.getSize("action_button").height
            text: "Load current print"
            tooltip: "Download the G-code currently printing in Moonraker and replace everything currently loaded in Cura."
            fixedWidthMode: false
            onClicked: base.loadClicked()
        }
    }
}
