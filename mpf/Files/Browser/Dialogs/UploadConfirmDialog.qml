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

    function uploadConfirm() {
        return root.printerModel != null && root.printerModel.fileUploadConfirm !== "" ? root.printerModel.fileUploadConfirm : null;
    }

    function uploadName() {
        var confirm = root.uploadConfirm();
        return confirm !== null ? confirm.filename : "";
    }
    padding: UM.Theme.getSize("default_margin").width
    modal: true
    closePolicy: Popup.CloseOnEscape
    // The dialog's content owns focus and answers Escape
    // itself; a popup-held focus swallows the key.
    focus: false
    onOpened: uploadConfirmDialogFocus.forceActiveFocus()
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        id: uploadConfirmDialogFocus
        focus: true
        Keys.onEscapePressed: {
            root.close();
            if (root.printerModel != null) {
                root.printerModel.fileCancelUpload();
            }
        }
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        UM.Label {
            text: "File already exists"
            font: UM.Theme.getFont("large_bold")
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.uploadName() + " exists on the printer — uploading will overwrite it."
            font: UM.Theme.getFont("medium")
        }
        RowLayout {
            width: parent.width
            spacing: UM.Theme.getSize("narrow_margin").width
            Cura.PrimaryButton {
                objectName: "uploadConfirmOverwriteButton"
                focusPolicy: Qt.StrongFocus
                text: "Overwrite"
                Layout.fillWidth: true
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileConfirmUpload();
                    }
                }
            }
            Cura.SecondaryButton {
                objectName: "uploadConfirmCancelButton"
                focusPolicy: Qt.StrongFocus
                text: "Cancel"
                Layout.fillWidth: true
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileCancelUpload();
                    }
                }
            }
        }
    }
}
