import QtQuick 2.15
import UM 1.5 as UM

// The action-panel host for the shared card: Cura's saveButton row
// contract. The row centres its components on a line two thick
// margins above the action panel's bottom, so this host reports a
// short strip and the card overflows upward from it — the row never
// grows, and Cura's Post Processing </> button and Slice panel keep
// their own docking. ActionPanelWidget already inserts one default
// margin between extensions; the strip reserves one more so the gap
// from </> to the card matches the gap from the card to Cura's panel.
Item {
    id: host
    objectName: "moonrakerPreviewCardPanelHost"

    property real externalGap: UM.Theme.getSize("default_margin").width
    property real verticalPadding: UM.Theme.getSize("thick_margin").height

    width: card.panelVisible ? externalGap + card.panelWidth : 0
    height: card.panelVisible ? 4 * verticalPadding : 0

    MoonrakerPreviewCard {
        id: card
        anchors.right: parent.right
        anchors.bottom: parent.bottom
    }
}
