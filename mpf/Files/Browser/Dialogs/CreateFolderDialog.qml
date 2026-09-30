import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../../Widgets"
import "../../../Resources/Theme"

Popup {
    id: root
    property var printerModel: null

    function submitNewFolder() {
        var name = createFolderField.text.trim();
        if (name !== "" && root.printerModel != null) {
            root.printerModel.fileCreateDirectory(name);
        }
        root.close();
    }
    padding: UM.Theme.getSize("default_margin").width
    modal: true
    closePolicy: Popup.CloseOnEscape
    focus: false
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    onOpened: {
        createFolderField.text = "";
        createFolderField.forceActiveFocus();
    }
    contentItem: Column {
        id: createFolderDialogFocus
        focus: true
        Keys.onEscapePressed: root.close()
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        UM.Label {
            text: "New folder"
            font: UM.Theme.getFont("large_bold")
        }
        TextField {
            id: createFolderField
            width: parent.width
            color: UM.Theme.getColor("text")
            palette.highlight: UM.Theme.getColor("primary")
            palette.highlightedText: "white"
            background: Rectangle {
                color: UM.Theme.getColor("setting_category")
                border.color: createFolderField.activeFocus ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                border.width: createFolderField.activeFocus ? 2 * screenScaleFactor : UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
            }
            Keys.onReturnPressed: root.submitNewFolder()
            Keys.onEnterPressed: root.submitNewFolder()
        }
        RowLayout {
            width: parent.width
            Cura.SecondaryButton {
                objectName: "createFolderCancelButton"
                Layout.fillWidth: true
                text: "Cancel"
                onClicked: root.close()
            }
            Cura.PrimaryButton {
                objectName: "createFolderCreateButton"
                Layout.fillWidth: true
                text: "Create"
                onClicked: root.submitNewFolder()
            }
        }
    }
}
