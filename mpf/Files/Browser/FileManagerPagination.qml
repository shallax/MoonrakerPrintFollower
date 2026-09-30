import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../Widgets"
import "../../Resources/Theme"

// The page strip: the selection summary on the left — the bulk delete
// and the count that clears it — and the page readouts with the
// page-size control on the right. The card hands it the model slice it
// reads and the page's rows; every write the strip originates comes
// back as a signal, so the card keeps the model and the bulk delete's
// modal.
RowLayout {
    id: root

    // The published model slice (null in the engine gate and captures).
    property var printerModel: null
    // The page's rows: the empty-state rule reads their count.
    property var activeRows: []
    // The narrow-window rule belongs to the card, not the strip.
    property bool narrowMode: false

    // Model writes the strip originates: the card applies them.
    signal bulkDeleteRequested
    signal selectionClearRequested
    signal pageSizeRequested(string size)
    signal pageRequested(int index)

    visible: !root.narrowMode
    Layout.fillWidth: true
    spacing: UM.Theme.getSize("default_margin").width / 2

    // Bulk delete lives in this row, far left, nudging the page
    // controls over (the live ruling). The header checkbox selects
    // the whole page, so the row carries only the count and the
    // verb. The verb is red, never primary-blue: blue reads as "the
    // suggested next step" for a destructive action (the live
    // ruling). In the real build the verb and the count appear only
    // while a selection exists.

    Rectangle {
        Layout.preferredWidth: 88 * screenScaleFactor
        Layout.preferredHeight: 28 * screenScaleFactor
        radius: UM.Theme.getSize("default_radius").width
        color: bulkDeleteHover.containsMouse ? MoonrakerTheme.dangerHover : "transparent"
        border.color: MoonrakerTheme.dangerRed
        border.width: 2 * screenScaleFactor
        // The verb and the count are GONE, not greyed,
        // while no selection exists (the live
        // ruling); the delete itself arrives with
        // Snapshot 3.
        visible: root.printerModel == null || root.printerModel.fileManagerSelected > 0
        UM.Label {
            anchors.centerIn: parent
            text: "Delete"
            color: MoonrakerTheme.dangerRed
            font: UM.Theme.getFont("medium_bold")
        }
        MouseArea {
            id: bulkDeleteHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                if (root.printerModel != null) {
                    root.bulkDeleteRequested();
                }
            }
        }
    }
    UM.Label {
        text: (root.printerModel != null ? root.printerModel.fileManagerSelected : 0) + " selected ✕"
        font: UM.Theme.getFont("medium_bold")
        visible: root.printerModel == null || root.printerModel.fileManagerSelected > 0
        // The count IS the global clear (the
        // ruling): with selection accumulated across
        // pages, only the count can drop it all without
        // perturbing the view.
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                if (root.printerModel != null) {
                    root.selectionClearRequested();
                }
            }
        }
    }
    UM.Label {
        Layout.fillWidth: true
        text: ""
    }
    UM.Label {
        text: root.printerModel != null ? root.printerModel.fileManagerShown : ""
        color: UM.Theme.getColor("text_inactive")
        // No data means no page chrome at all — no
        // "Showing 0 of 0", no "Page 1 of 0" (the
        // live report). The mock keeps its
        // pinned faces.
        visible: root.printerModel == null || root.activeRows.length > 0
    }
    UM.Label {
        text: root.printerModel != null ? root.printerModel.fileManagerPage : ""
        color: UM.Theme.getColor("text_inactive")
        visible: root.printerModel == null || root.activeRows.length > 0
    }
    Cura.SecondaryButton {
        id: pageSizeButton
        visible: root.printerModel == null || root.activeRows.length > 0
        text: (root.printerModel != null ? root.printerModel.fileManagerPageSize : "25") + " / page ▾"
        onPressed: pageSizePopup.wasOpenAtPress = pageSizePopup.opened
        onClicked: {
            if (pageSizePopup.wasOpenAtPress) {
                pageSizePopup.close();
            } else {
                pageSizePopup.open();
            }
        }
    }
    Popup {
        id: pageSizePopup
        objectName: "pageSizePopup"
        property bool wasOpenAtPress: false
        // Below the OPENER, right-aligned with it (the
        // live report: the row-bottom-left
        // position opened over the upload button).
        x: pageSizeButton.x + pageSizeButton.width - width
        y: pageSizeButton.y + pageSizeButton.height
        padding: 0
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
        contentItem: Rectangle {
            implicitWidth: 140 * screenScaleFactor
            implicitHeight: pageSizeColumn.height
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: UM.Theme.getSize("default_radius").width
            Column {
                id: pageSizeColumn
                Repeater {
                    model: ["25", "50", "100", "all"]
                    Item {
                        width: 140 * screenScaleFactor
                        height: 28 * screenScaleFactor
                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width
                            color: sizeMouse.containsMouse ? UM.Theme.getColor("setting_category") : "transparent"
                        }
                        Cura.RadioButton {
                            id: sizeRadio
                            anchors.left: parent.left
                            anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                            anchors.verticalCenter: parent.verticalCenter
                            checked: root.printerModel != null && String(root.printerModel.fileManagerPageSize) === String(modelData)
                            onClicked: {
                                if (root.printerModel != null) {
                                    root.pageSizeRequested(modelData);
                                }
                                sizeRadio.checked = Qt.binding(function () {
                                    return root.printerModel != null && String(root.printerModel.fileManagerPageSize) === String(modelData);
                                });
                            }
                        }
                        UM.Label {
                            anchors.left: parent.left
                            anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + 22 * screenScaleFactor
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData === "all" ? "All" : modelData + " / page"
                            font: UM.Theme.getFont("default")
                        }
                        MouseArea {
                            id: sizeMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (root.printerModel != null) {
                                    root.pageSizeRequested(modelData);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Cura.SecondaryButton {
        visible: root.printerModel == null || root.activeRows.length > 0
        text: "‹"
        enabled: root.printerModel != null && root.printerModel.monitorConnected && root.printerModel.fileManagerPageIndex > 1
        onClicked: {
            if (root.printerModel != null) {
                root.pageRequested(root.printerModel.fileManagerPageIndex - 1);
            }
        }
    }
    Cura.SecondaryButton {
        visible: root.printerModel == null || root.activeRows.length > 0
        text: "›"
        enabled: root.printerModel != null && root.printerModel.monitorConnected && root.printerModel.fileManagerPageIndex < root.printerModel.fileManagerPageCount
        onClicked: {
            if (root.printerModel != null) {
                root.pageRequested(root.printerModel.fileManagerPageIndex + 1);
            }
        }
    }
}
