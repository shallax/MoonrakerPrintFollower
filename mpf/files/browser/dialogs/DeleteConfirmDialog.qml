import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../../widgets"
import "../../../resources/theme"

Popup {
    id: root
    property var printerModel: null

    function deleteConfirm() {
        return root.printerModel != null && root.printerModel.fileDeleteConfirm !== "" ? root.printerModel.fileDeleteConfirm : null;
    }

    function deleteWording() {
        var confirm = root.deleteConfirm();
        if (confirm === null) {
            return "";
        }
        if (confirm.kind === "dir") {
            return confirm.first + " and everything inside it will be deleted from the printer.";
        }
        if (confirm.count === 1) {
            return confirm.first + " will be deleted from the printer.";
        }
        return confirm.count + " files will be deleted from the printer, including " + confirm.first + ".";
    }

    function deleteKind() {
        var confirm = root.deleteConfirm();
        return confirm !== null ? confirm.kind : "file";
    }

    function deleteBlockedCount() {
        var confirm = root.deleteConfirm();
        return confirm !== null ? confirm.blocked : 0;
    }
    padding: UM.Theme.getSize("default_margin").width
    modal: true
    closePolicy: Popup.CloseOnEscape
    // The dialog's content owns focus and answers Escape
    // itself; a popup-held focus swallows the key.
    focus: false
    onOpened: deleteConfirmDialogFocus.forceActiveFocus()
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        id: deleteConfirmDialogFocus
        focus: true
        Keys.onEscapePressed: {
            root.close();
            if (root.printerModel != null) {
                root.printerModel.fileCancelDelete();
            }
        }
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        UM.Label {
            text: root.deleteKind() === "dir" ? "Delete folder?" : "Delete files?"
            font: UM.Theme.getFont("large_bold")
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.deleteWording()
            font: UM.Theme.getFont("medium")
        }
        UM.Label {
            visible: root.deleteBlockedCount() > 0
            width: parent.width
            wrapMode: Text.Wrap
            text: root.deleteBlockedCount() + " selected can't be deleted — the printer is using them."
            color: MoonrakerTheme.warningOrange
        }
        RowLayout {
            width: parent.width
            spacing: UM.Theme.getSize("narrow_margin").width
            Cura.PrimaryButton {
                objectName: "deleteConfirmDeleteButton"
                focusPolicy: Qt.StrongFocus
                text: "Delete"
                Layout.fillWidth: true
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileConfirmDelete();
                    }
                }
            }
            Cura.SecondaryButton {
                objectName: "deleteConfirmCancelButton"
                focusPolicy: Qt.StrongFocus
                text: "Cancel"
                Layout.fillWidth: true
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileCancelDelete();
                    }
                }
            }
        }
    }
}
