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
    property bool bedMeshAvailable: false
    property bool bedMeshVisible: true
    property string bedMeshRangeText: ""
    property string bedMeshMinimumText: ""
    property string bedMeshMaximumText: ""

    signal loadClicked()
    signal bedMeshVisibilityRequested(bool visible)

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

            Cura.SecondaryButton
            {
                visible: base.bedMeshAvailable
                width: parent.width
                height: visible ? UM.Theme.getSize("action_button").height : 0
                text: base.bedMeshVisible ? "Hide bed mesh" : "Show bed mesh"
                tooltip: "Show the active Klipper bed mesh as a coloured 3D surface on Cura's build plate"
                    + (base.bedMeshRangeText.length > 0 ? " (" + base.bedMeshRangeText + ")." : ".")
                fixedWidthMode: true
                onClicked: base.bedMeshVisibilityRequested(!base.bedMeshVisible)
            }

            Column
            {
                visible: base.bedMeshAvailable && base.bedMeshVisible
                width: parent.width
                height: visible ? implicitHeight : 0
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
                    text: "Faded edge = extrapolated outside probe bounds"
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default_italic")
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
