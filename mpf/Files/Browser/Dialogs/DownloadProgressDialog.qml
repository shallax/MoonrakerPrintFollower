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

    function downloadProgress() {
        return root.printerModel != null ? root.printerModel.fileDownloadProgress : "";
    }

    function downloadProgressName() {
        var progress = root.downloadProgress();
        return progress !== "" ? progress.name : "";
    }

    function downloadProgressPercent() {
        var progress = root.downloadProgress();
        return progress !== "" ? progress.percent : 0;
    }

    function downloadProgressIndeterminate() {
        // No declared length yet: the window stays open, the bytes
        // received stand in for the fraction and the Cancel stays
        // reachable. A determinate percentage replaces this the moment
        // the total arrives.
        var progress = root.downloadProgress();
        return progress !== "" && progress.indeterminate === true;
    }

    function humanBytes(bytes) {
        // Bytes stay bytes; anything larger steps through the binary
        // units at 1024 (the live request).
        if (bytes < 1024) {
            return bytes + " B";
        }
        var units = ["KiB", "MiB", "GiB", "TiB"];
        var value = bytes;
        var unit = "";
        for (var i = 0; i < units.length && value >= 1024; ++i) {
            value /= 1024;
            unit = units[i];
        }
        return value.toFixed(1) + " " + unit;
    }

    function downloadProgressSize() {
        var progress = root.downloadProgress();
        if (progress === "") {
            return "";
        }
        if (!(progress.total > 0)) {
            // An undeclared total leaves the bytes received as the only
            // honest readout (the total is a 0 sentinel, never absent).
            return root.humanBytes(progress.received);
        }
        return root.humanBytes(progress.received) + " / " + root.humanBytes(progress.total);
    }
    visible: root.downloadProgressName() !== ""
    modal: true
    closePolicy: Popup.NoAutoClose  // only Cancel (button or Esc) ends it
    // The content owns focus and answers Esc itself; a popup-held
    // focus swallows the key (the upload dialog's live-proven
    // pattern).
    onOpened: downloadProgressDialogFocus.forceActiveFocus()
    padding: UM.Theme.getSize("default_margin").width
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        id: downloadProgressDialogFocus
        focus: true
        // Esc cancels the download itself, not just the window
        // (the live request).
        Keys.onEscapePressed: {
            if (root.printerModel != null) {
                root.printerModel.fileDownloadCancel();
            }
        }
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        // The popup is only about the download: one large
        // spinning hourglass over the title (the live request).
        UM.ColorImage {
            id: downloadHourglass
            anchors.horizontalCenter: parent.horizontalCenter
            source: Qt.resolvedUrl("../../../Resources/Svg/Hourglass.svg")
            color: UM.Theme.getColor("text")
            width: 56 * screenScaleFactor
            height: 56 * screenScaleFactor
            SequentialAnimation on rotation {
                running: root.visible
                loops: Animation.Infinite
                NumberAnimation {
                    from: 0
                    to: 180
                    duration: 350
                }
                NumberAnimation {
                    from: 180
                    to: 360
                    duration: 350
                }
            }
        }
        UM.Label {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Downloading"
            font: UM.Theme.getFont("large_bold")
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.downloadProgressName()
            font: UM.Theme.getFont("medium")
        }
        OutlineProgressBar {
            width: parent.width
            // An undeclared total has no fraction to draw: the bar
            // yields to the byte counter and the spinner until the
            // headers name one (a bar pinned at 0% would read as a
            // stalled transfer).
            height: root.downloadProgressIndeterminate() ? 0 : 10 * screenScaleFactor
            from: 0
            to: 100
            value: root.downloadProgressPercent()
        }
        RowLayout {
            width: parent.width
            UM.Label {
                text: root.downloadProgressIndeterminate() ? "Received" : root.downloadProgressPercent() + "%"
                font: UM.Theme.getFont("small")
                Layout.fillWidth: true
            }
            UM.Label {
                text: root.downloadProgressSize()
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text_inactive")
            }
        }
        Cura.SecondaryButton {
            width: parent.width
            objectName: "downloadProgressCancelButton"
            text: "Cancel"
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.fileDownloadCancel();
                }
            }
        }
    }
}
