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

    function renameTarget() {
        return root.printerModel != null && root.printerModel.fileRenameTarget !== "" ? root.printerModel.fileRenameTarget : null;
    }

    function renameKind() {
        var target = root.renameTarget();
        return target !== null ? target.kind : "file";
    }

    function confirmRename() {
        // The button and the field's Return key share this path.
        root.close();
        if (root.printerModel != null) {
            root.printerModel.fileConfirmRename();
        }
    }

    function renameStemLength(name) {
        // Everything before the LAST dot: "bench.tar.gcode" keeps
        // ".gcode" unselected; no dot selects the whole name.
        var lower = String(name).toLowerCase();
        for (var i = lower.length - 1; i >= 0; --i) {
            if (lower.charAt(i) === ".") {
                return i;
            }
        }
        return name.length;
    }
    padding: UM.Theme.getSize("default_margin").width
    modal: true
    closePolicy: Popup.CloseOnEscape
    // The dialog's content owns focus and answers Escape
    // itself; a popup-held focus swallows the key.
    focus: false
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    onOpened: {
        // The whole stem pre-selects (the live
        // request): typing replaces the name and the extension
        // survives. onOpened fires once per open, so a republish
        // mid-typing never re-selects or moves the cursor. The
        // field takes focus so typing starts immediately.
        var target = root.renameTarget();
        if (target !== null) {
            renameField.text = target.name;
            renameField.select(0, root.renameStemLength(target.name));
        }
        renameField.forceActiveFocus();
    }
    contentItem: Column {
        id: renameDialogFocus
        focus: true
        Keys.onEscapePressed: {
            root.close();
            if (root.printerModel != null) {
                root.printerModel.fileCancelRename();
            }
        }
        spacing: UM.Theme.getSize("narrow_margin").height
        width: 320 * screenScaleFactor
        UM.Label {
            text: root.renameKind() === "dir" ? "Rename folder" : "Rename file"
            font: UM.Theme.getFont("large_bold")
        }
        TextField {
            id: renameField
            width: parent.width
            // The theme's default field reads as a black slab
            // under the popup (the live report): the
            // input well matches the thumbnail tiles' surface,
            // with the theme's text colour (white-on-white was
            // the second report) and a Cura-blue selection.
            color: UM.Theme.getColor("text")
            palette.highlight: UM.Theme.getColor("primary")
            palette.highlightedText: "white"
            background: Rectangle {
                color: UM.Theme.getColor("setting_category")
                // The focus cue: the well's outline flips to the
                // Cura blue while the field holds focus (the
                // live report — tabbing gave no cue).
                border.color: renameField.activeFocus ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                border.width: renameField.activeFocus ? 2 * screenScaleFactor : UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
            }
            onTextEdited: {
                if (root.printerModel != null) {
                    root.printerModel.filePreviewRename(renameField.text);
                }
            }
            Keys.onReturnPressed: root.confirmRename()
            Keys.onEnterPressed: root.confirmRename()
        }
        UM.Label {
            visible: root.printerModel != null && root.printerModel.fileRenameConflict
            width: parent.width
            wrapMode: Text.Wrap
            text: "A file with this name already exists — it will be overwritten."
            color: MoonrakerTheme.warningOrange
        }
        RowLayout {
            width: parent.width
            spacing: UM.Theme.getSize("narrow_margin").width
            Cura.PrimaryButton {
                objectName: "renameConfirmButton"
                focusPolicy: Qt.StrongFocus
                text: root.printerModel != null && root.printerModel.fileRenameConflict ? "Overwrite" : "Rename"
                Layout.fillWidth: true
                onClicked: root.confirmRename()
            }
            Cura.SecondaryButton {
                objectName: "renameConfirmCancelButton"
                focusPolicy: Qt.StrongFocus
                text: "Cancel"
                Layout.fillWidth: true
                onClicked: {
                    root.close();
                    if (root.printerModel != null) {
                        root.printerModel.fileCancelRename();
                    }
                }
            }
        }
    }
}
