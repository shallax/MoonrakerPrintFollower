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
    property bool startAllowed: false

    function thumbStateLarge(relpath) {
        var thumbs = root.printerModel != null ? root.printerModel.fileManagerThumbs : {};
        var entry = thumbs[relpath];
        return entry !== undefined ? entry.state_large : "none";
    }

    function thumbUrlLarge(relpath) {
        var thumbs = root.printerModel != null ? root.printerModel.fileManagerThumbs : {};
        var entry = thumbs[relpath];
        return entry !== undefined ? entry.url_large : "";
    }

    function confirmRelpath() {
        return root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.relpath : "";
    }
    objectName: "printConfirmDialog"
    padding: UM.Theme.getSize("default_margin").width
    // The dialog owns the interaction while it is up: the rows
    // behind it must not answer clicks (the live
    // report).
    modal: true
    closePolicy: Popup.CloseOnEscape
    // The dialog's content owns focus and answers Escape
    // itself; a popup-held focus swallows the key.
    focus: false
    onOpened: printConfirmDialogFocus.forceActiveFocus()
    // The popup's own background (the live report: the
    // default background was a dark slab under dark text) —
    // the same surface as the filter dropdowns.
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        id: printConfirmDialogFocus
        focus: true
        Keys.onEscapePressed: {
            root.close();
            if (root.printerModel != null) {
                root.printerModel.fileCancelPrint();
            }
        }
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        UM.Label {
            text: "Start print?"
            font: UM.Theme.getFont("large_bold")
        }
        // The confirmation's own large thumbnail (the
        // live request): the grid's fetch/cache at dialog scale
        // with the same fallbacks — hourglass while loading,
        // diamond when the file has none. The centring Item is
        // deliberate: anchored children inside a Column are
        // undefined behaviour.
        Item {
            width: parent.width
            height: 128 * screenScaleFactor
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 128 * screenScaleFactor
                height: 128 * screenScaleFactor
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: root.thumbStateLarge(root.confirmRelpath()) === "ready" ? UM.Theme.getColor("setting_category") : "transparent"
                Image {
                    id: confirmThumb
                    visible: root.thumbStateLarge(root.confirmRelpath()) === "ready" && confirmThumb.status !== Image.Error
                    anchors.fill: parent
                    anchors.margins: 4 * screenScaleFactor
                    source: root.thumbUrlLarge(root.confirmRelpath())
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    asynchronous: true
                }
                UM.ColorImage {
                    visible: root.thumbStateLarge(root.confirmRelpath()) === "loading"
                    anchors.centerIn: parent
                    width: 24 * screenScaleFactor
                    height: 24 * screenScaleFactor
                    source: Qt.resolvedUrl("../../../Resources/Svg/Hourglass.svg")
                    color: UM.Theme.getColor("text_inactive")
                    // The spin: a static glyph reads as dead.
                    RotationAnimation on rotation {
                        from: 0
                        to: 360
                        duration: 2000
                        loops: Animation.Infinite
                        running: root.thumbStateLarge(root.confirmRelpath()) === "loading"
                    }
                }
                UM.Label {
                    visible: root.thumbStateLarge(root.confirmRelpath()) === "failed" || root.thumbStateLarge(root.confirmRelpath()) === "none"
                    anchors.centerIn: parent
                    text: "◇"
                    color: UM.Theme.getColor("text_inactive")
                }
            }
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.name : ""
            font: UM.Theme.getFont("medium")
        }
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("narrow_margin").height / 2
            width: parent.width
            UM.Label {
                text: "Est. time"
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                text: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.est : ""
            }
            UM.Label {
                text: "Filament"
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                text: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.filament : ""
            }
            UM.Label {
                text: "Printer"
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                text: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.printerName : ""
            }
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.readyText : ""
            color: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? (root.printerModel.filePrintConfirm.homed ? (root.printerModel.filePrintConfirm.ready ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")) : MoonrakerTheme.warningOrange) : UM.Theme.getColor("text_inactive")
        }
        RowLayout {
            width: parent.width
            spacing: UM.Theme.getSize("narrow_margin").width
            Cura.PrimaryButton {
                objectName: "printConfirmStartButton"
                focusPolicy: Qt.StrongFocus
                text: "Start print"
                Layout.fillWidth: true
                // The gate re-evaluates while the dialog stays
                // open (4.2.0, N2): the click-time check ran at
                // dialog-open; the dispatch re-checks again in
                // Python — this binding keeps the button honest
                // for the states the dialog can witness.
                enabled: root.printerModel != null && root.startAllowed
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileConfirmPrint();
                    }
                }
            }
            Cura.SecondaryButton {
                objectName: "printConfirmCancelButton"
                focusPolicy: Qt.StrongFocus
                text: "Cancel"
                Layout.fillWidth: true
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileCancelPrint();
                    }
                }
            }
        }
    }
}
