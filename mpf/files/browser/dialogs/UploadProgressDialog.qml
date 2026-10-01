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

    function uploadProgress() {
        return root.printerModel != null && root.printerModel.fileUploadProgress !== "" ? root.printerModel.fileUploadProgress : null;
    }

    function uploadProgressState() {
        var progress = root.uploadProgress();
        return progress !== null ? progress.state : "";
    }

    function uploadProgressName() {
        var progress = root.uploadProgress();
        return progress !== null ? progress.name : "";
    }

    function uploadProgressPercent() {
        var progress = root.uploadProgress();
        return progress !== null ? progress.percent : 0;
    }

    function uploadProgressError() {
        var progress = root.uploadProgress();
        return progress !== null ? progress.error : "";
    }
    padding: UM.Theme.getSize("default_margin").width
    modal: true
    closePolicy: Popup.CloseOnEscape
    // The dialog's content owns focus and answers Escape
    // itself; a popup-held focus swallows the key.
    focus: false
    onOpened: uploadProgressDialogFocus.forceActiveFocus()
    // ANY close dismisses the payload (the ruling: the
    // dialog can never get stuck) — Esc and the button alike;
    // a dismissed upload runs on and its verdict lands in the
    // console.
    onClosed: {
        if (root.printerModel != null) {
            root.printerModel.fileUploadDismiss();
        }
    }
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        id: uploadProgressDialogFocus
        focus: true
        Keys.onEscapePressed: {
            root.close();
            if (root.printerModel != null) {
                root.printerModel.fileUploadDismiss();
            }
        }
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        UM.Label {
            text: root.uploadProgressState() === "uploading" ? "Uploading" : (root.uploadProgressState() === "done" ? "Upload complete" : "Upload failed")
            font: UM.Theme.getFont("large_bold")
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.uploadProgressName()
            font: UM.Theme.getFont("medium")
        }
        OutlineProgressBar {
            visible: root.uploadProgressState() === "uploading"
            // Explicit geometry: Layout.* is IGNORED inside this
            // plain Column, and a zero-sized bar was the
            // live report ("no progress bar").
            width: parent.width
            height: 10 * screenScaleFactor
            from: 0
            to: 100
            value: root.uploadProgressPercent()
        }
        UM.Label {
            visible: root.uploadProgressState() === "failed"
            width: parent.width
            wrapMode: Text.Wrap
            text: root.uploadProgressError()
            color: MoonrakerTheme.warningOrange
        }
        RowLayout {
            // Always present: closing mid-upload dismisses the
            // popup, and the upload runs on (the
            // ruling: the dialog can never get stuck).
            width: parent.width
            spacing: UM.Theme.getSize("narrow_margin").width
            Cura.PrimaryButton {
                objectName: "uploadProgressCloseButton"
                focusPolicy: Qt.StrongFocus
                text: "Close"
                Layout.fillWidth: true
                onClicked: root.close()
            }
        }
    }
}
