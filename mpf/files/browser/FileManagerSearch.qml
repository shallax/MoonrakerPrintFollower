import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The search row: name-only, full width — the filters stack UNDER it,
// never beside it (the live-test ruling). The field is hosted by an
// Item so the in-field clear button can anchor without touching the
// TextField's own layout (anchored children inside a layout-managed
// field are undefined behaviour). The row owns the settle timer: the
// keystroke reaches the shell 250 ms after typing stops, so the grid
// never stutters.
Item {
    id: root

    // The shell's search value. A Binding with RestoreBinding keeps it
    // authoritative over the user's edit, so the model stays the one
    // source of truth in both directions.
    property string search: ""

    signal searchSettled(string text)
    signal searchCleared

    // The shell hands focus here on open and on any click inside the
    // card; the field itself stays private to this document.
    function focusField() {
        searchField.forceActiveFocus();
    }

    Layout.fillWidth: true
    implicitHeight: 28 * screenScaleFactor
    TextField {
        id: searchField
        // Inert; the narrow-mode exercise asserts this field's presence
        // in the rendered tree.
        objectName: "moonrakerFileSearch"
        anchors.fill: parent
        Binding {
            target: searchField
            property: "text"
            value: root.search
            restoreMode: Binding.RestoreBinding
        }
        placeholderText: "Search by name"
        rightPadding: searchClear.visible ? searchClear.width + 8 * screenScaleFactor : 6 * screenScaleFactor
        onTextEdited: searchDebounce.restart()
        Timer {
            id: searchDebounce
            interval: 250
            onTriggered: root.searchSettled(searchField.text)
        }
    }
    // The clear button: a circled × on the right of the field, shown
    // only while a search is active (a live request). Clearing is
    // immediate — the settle timer covers typing, not a deliberate
    // reset.
    Rectangle {
        id: searchClear
        visible: searchField.text.length > 0
        anchors.right: parent.right
        anchors.rightMargin: 6 * screenScaleFactor
        anchors.verticalCenter: parent.verticalCenter
        width: 16 * screenScaleFactor
        height: 16 * screenScaleFactor
        radius: 8 * screenScaleFactor
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        color: "transparent"
        UM.Label {
            anchors.centerIn: parent
            text: "✕"
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("small")
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.searchCleared()
        }
    }
}
