import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The file-manager popup (3.6.0): the real QML surface over the
// published model slice — no synthetic rows ship (the author's
// ruling). The popup is exempt from the no-reflow rule by the
// author's ruling ("Reflowing the file manager is fine, there's
// nothing critical on that"); every state-gated visibility
// expression lands in the test's line-level allow list citing that
// ruling.
Item {
    id: root

    property bool open: false
    // The model behind the face (Snapshot 1): the published page
    // slice, recents, breadcrumb, disk and view metadata. Null in
    // the engine gate and captures — the mock rows below then serve
    // as the fallback so those still render.
    property var printerModel: null
    // No mock fallbacks (the author's ruling: the synthetic data
    // must not ship) — without a printer the face simply renders
    // empty; the engine gate and captures pass a stub model when
    // they need faces.
    readonly property var activeRows: root.printerModel != null ? root.printerModel.fileManagerRows : []
    readonly property var activeRecents: root.printerModel != null ? root.printerModel.fileManagerRecents : []
    readonly property var activeDirectories: root.printerModel != null ? root.printerModel.fileManagerDirectories : []
    // All dismissal paths request the close through this signal and
    // the dashboard owns the flag — assigning `open` internally would
    // break the binding and leave the button unable to reopen the
    // popup (the author's Snapshot 0 report).
    signal closeRequested

    visible: open
    focus: true
    // Opening the popup triggers the fetch (refetch on open — the
    // author's ruling): the walk and the history window ride the
    // model's own lane. Nothing else flips this; the button in the
    // dashboard only sets `open`.
    onOpenChanged: {
        if (open && root.printerModel != null) {
            root.printerModel.openFileManager();
        }
    }
    Connections {
        // The confirmation arrives through the model's publish, not
        // through printerModel changing — the signal is the trigger.
        target: root.printerModel
        enabled: root.printerModel != null
        function onFileManagerChanged() {
            if (!root.open) {
                return;
            }
            if (root.printerModel != null && root.printerModel.filePrintConfirm !== "") {
                printConfirmDialog.open();
            }
            if (root.printerModel != null && root.printerModel.fileDeleteConfirm !== "") {
                deleteConfirmDialog.open();
            }
            if (root.printerModel != null && root.printerModel.fileRenameTarget !== "" && !renameDialog.opened) {
                renameDialog.open();
            }
            if (root.printerModel != null && root.printerModel.fileUploadConfirm !== "") {
                uploadConfirmDialog.open();
            }
            if (root.printerModel != null && root.printerModel.fileUploadProgress !== "" && !uploadProgressDialog.opened) {
                uploadProgressDialog.open();
            }
        }
    }
    onVisibleChanged: {
        if (visible) {
            // Esc only reached us while a descendant held focus (the
            // author's Snapshot 0 reports) — give focus to a real
            // target on every open. The search field is the natural
            // first stop; in narrow mode (no search field) the focus
            // anchor below carries it.
            if (!root.narrowMode) {
                searchField.forceActiveFocus();
            } else {
                focusAnchor.forceActiveFocus();
            }
        }
    }
    Keys.onEscapePressed: {
        // An open dialog owns the key: Esc closes the TOP one,
        // never the whole popup (the root itself holds focus).
        if (printConfirmDialog.opened) {
            printConfirmDialog.close();
        } else if (deleteConfirmDialog.opened) {
            deleteConfirmDialog.close();
        } else if (renameDialog.opened) {
            renameDialog.close();
        } else if (uploadConfirmDialog.opened) {
            uploadConfirmDialog.close();
        } else if (uploadProgressDialog.opened) {
            uploadProgressDialog.close();
        } else {
            root.closeRequested();
        }
        event.accepted = true;
    }

    // Esc ownership moved to the MONITOR root: one window-level
    // shortcut owns the whole ladder (popup → popover → stage), so
    // the key can never have two competing claimants (the author's
    // live report: the popup's own shortcut and the monitor's
    // fought, and the popup lost). The popup's Keys handlers stay
    // for the focus-in-popup case; the model's fileManagerOpen is
    // the single source of truth.
    property bool innerLayerOwnsEsc: printConfirmDialog.opened || deleteConfirmDialog.opened || renameDialog.opened || uploadConfirmDialog.opened || uploadProgressDialog.opened

    // A FocusScope that always exists and always accepts focus, so
    // Esc has a home even when the search field does not exist (the
    // author's Snapshot 0 report: narrow windows have no search bar,
    // and then Esc had nothing to bubble from).
    FocusScope {
        id: focusAnchor
        anchors.fill: parent
        focus: true
    }

    // The print confirmation (Snapshot 2): filename, est. time,
    // filament, the target printer's name and a readiness line, with
    // the pinned verbs. While it is up, Esc cancels IT — the top
    // layer owns the key (the author's ruling), and every row's
    // Print entry stands down.
    Popup {
        id: printConfirmDialog
        objectName: "printConfirmDialog"
        anchors.centerIn: root
        padding: UM.Theme.getSize("default_margin").width
        // The dialog owns the interaction while it is up: the rows
        // behind it must not answer clicks (the author's live
        // report).
        modal: true
        closePolicy: Popup.CloseOnEscape
        // The dialog's content owns focus and answers Escape
        // itself; a popup-held focus swallows the key.
        focus: false
        onOpened: printConfirmDialogFocus.forceActiveFocus()
        // The popup's own background (the author's live report: the
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
                printConfirmDialog.close();
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
            // The confirmation's own large thumbnail (the author's
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
                        source: Qt.resolvedUrl("Hourglass.svg")
                        color: UM.Theme.getColor("text_inactive")
                        // The spin: a static glyph reads as dead.
                        RotationAnimation on rotation  {
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
                color: root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? (root.printerModel.filePrintConfirm.homed ? (root.printerModel.filePrintConfirm.ready ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")) : "#fb8c00") : UM.Theme.getColor("text_inactive")
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("narrow_margin").width
                Cura.PrimaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Start print"
                    Layout.fillWidth: true
                    onClicked: {
                        printConfirmDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileConfirmPrint();
                        }
                    }
                }
                Cura.SecondaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Cancel"
                    Layout.fillWidth: true
                    onClicked: {
                        printConfirmDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileCancelPrint();
                        }
                    }
                }
            }
        }
    }

    // The delete confirmation (Snapshot 3): the selection's count, a
    // blocked line when some of it is printing, and the pinned verbs.
    Popup {
        id: deleteConfirmDialog
        anchors.centerIn: root
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
                deleteConfirmDialog.close();
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
                color: "#fb8c00"
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("narrow_margin").width
                Cura.PrimaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Delete"
                    Layout.fillWidth: true
                    onClicked: {
                        deleteConfirmDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileConfirmDelete();
                        }
                    }
                }
                Cura.SecondaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Cancel"
                    Layout.fillWidth: true
                    onClicked: {
                        deleteConfirmDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileCancelDelete();
                        }
                    }
                }
            }
        }
    }

    // The New-folder dialog (the author's live request): a plain
    // name, Enter creates, Esc cancels.
    Popup {
        id: createFolderDialog
        anchors.centerIn: root
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
            Keys.onEscapePressed: createFolderDialog.close()
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
                    Layout.fillWidth: true
                    text: "Cancel"
                    onClicked: createFolderDialog.close()
                }
                Cura.PrimaryButton {
                    Layout.fillWidth: true
                    text: "Create"
                    onClicked: root.submitNewFolder()
                }
            }
        }
    }

    // The rename dialog (Snapshot 3): the name field pre-filled on
    // open, a live collision line while typing (the host's move
    // silently overwrites — the dialog asks first, round-1 C2/C3),
    // and the pinned verbs.
    Popup {
        id: renameDialog
        anchors.centerIn: root
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
            // The whole stem pre-selects (the author's live
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
                renameDialog.close();
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
                // under the popup (the author's live report): the
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
                    // author's live report — tabbing gave no cue).
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
                color: "#fb8c00"
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("narrow_margin").width
                Cura.PrimaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: root.printerModel != null && root.printerModel.fileRenameConflict ? "Overwrite" : "Rename"
                    Layout.fillWidth: true
                    onClicked: root.confirmRename()
                }
                Cura.SecondaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Cancel"
                    Layout.fillWidth: true
                    onClicked: {
                        renameDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileCancelRename();
                        }
                    }
                }
            }
        }
    }

    // The folder context menu (the author's live request): right-click
    // a breadcrumb segment or a strip chip to rename or delete the
    // folder. The root has no menu — it cannot be renamed or deleted.
    Menu {
        id: dirActionsMenu
        property string dirPath: ""
        MenuItem {
            enabled: root.printerModel != null && root.printerModel.monitorConnected
            text: "Rename"
            onTriggered: {
                if (root.printerModel != null) {
                    root.printerModel.fileRequestRenameDir(dirActionsMenu.dirPath);
                }
            }
        }
        MenuItem {
            enabled: root.printerModel != null && root.printerModel.monitorConnected
            text: "Delete"
            onTriggered: {
                if (root.printerModel != null) {
                    root.printerModel.fileRequestDeleteDir(dirActionsMenu.dirPath);
                }
            }
        }
    }

    // The upload-overwrite confirmation (Snapshot 3): the host
    // silently overwrites, so a name collision asks first.
    Popup {
        id: uploadConfirmDialog
        anchors.centerIn: root
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
                uploadConfirmDialog.close();
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
                    focusPolicy: Qt.StrongFocus
                    text: "Overwrite"
                    Layout.fillWidth: true
                    onClicked: {
                        uploadConfirmDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileConfirmUpload();
                        }
                    }
                }
                Cura.SecondaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Cancel"
                    Layout.fillWidth: true
                    onClicked: {
                        uploadConfirmDialog.close();
                        if (root.printerModel != null) {
                            root.printerModel.fileCancelUpload();
                        }
                    }
                }
            }
        }
    }

    // The upload progress popup (Snapshot 3 finish — the author's
    // live request): a bar while the upload runs, and a success/fail
    // verdict in the SAME popup at the end.
    Popup {
        id: uploadProgressDialog
        anchors.centerIn: root
        padding: UM.Theme.getSize("default_margin").width
        modal: true
        closePolicy: Popup.CloseOnEscape
        // The dialog's content owns focus and answers Escape
        // itself; a popup-held focus swallows the key.
        focus: false
        onOpened: uploadProgressDialogFocus.forceActiveFocus()
        // ANY close dismisses the payload (the author's ruling: the
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
                uploadProgressDialog.close();
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
                // plain Column, and a zero-sized bar was the author's
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
                color: "#fb8c00"
            }
            RowLayout {
                // Always present: closing mid-upload dismisses the
                // popup, and the upload runs on (the author's
                // ruling: the dialog can never get stuck).
                width: parent.width
                spacing: UM.Theme.getSize("narrow_margin").width
                Cura.PrimaryButton {
                    focusPolicy: Qt.StrongFocus
                    text: "Close"
                    Layout.fillWidth: true
                    onClicked: uploadProgressDialog.close()
                }
            }
        }
    }

    // The local-file picker for uploads (Snapshot 3): gcode files
    // only — sliced prints upload from the Preview view (the
    // author's ruling).
    FileDialog {
        id: filePicker
        title: "Upload a gcode file to the printer"
        nameFilters: ["G-code files (*.gcode *.g *.gco)"]
        onAccepted: {
            if (root.printerModel != null) {
                root.printerModel.fileUpload(String(filePicker.selectedFile));
            }
        }
    }

    // Below this size the full file area gives way to the recents
    // strip and a resize hint (the author's live ruling: a crushed
    // window makes the manager useless — only recent prints stay).
    readonly property bool narrowMode: card.width < 700 * screenScaleFactor || card.height < 500 * screenScaleFactor

    // Sticky identity block (checkbox + thumbnail + name)
    // and the trailing columns share these geometry constants with the
    // header and every row — one column model, no per-row arithmetic.
    readonly property real rowHeight: 48 * screenScaleFactor
    // The render window (the author's live report: a 400-file
    // "all / page" listing beachballed the popup — two panes of
    // eager delegates). Only the rows inside the window plus a
    // margin instantiate; the scroll geometry stays honest.
    readonly property int rowWindowStart: Math.max(0, Math.floor(gridVertical.contentY / root.rowHeight) - 10)
    readonly property int rowWindowEnd: Math.min(root.activeRows.length, Math.ceil((gridVertical.contentY + gridVertical.height) / root.rowHeight) + 20)
    readonly property var visibleRows: root.activeRows.slice(root.rowWindowStart, root.rowWindowEnd)
    onVisibleRowsChanged: thumbsWindowTimer.restart()
    function requestVisibleThumbnails() {
        // Fires when the scroll SETTLES: fast scrolling fetches once,
        // not once per frame (the author's live report: the fetch
        // storm made long scrolls crawl).
        if (root.printerModel != null) {
            var relpaths = [];
            for (var i = 0; i < root.visibleRows.length; ++i) {
                relpaths.push(root.visibleRows[i].relpath);
            }
            root.printerModel.fileRequestVisibleThumbnails(relpaths);
        }
    }
    Timer {
        id: thumbsWindowTimer
        interval: 300
        onTriggered: root.requestVisibleThumbnails()
    }
    // Column widths are sized to fit their HEADERS on one line —
    // headers never elide or wrap (the author's live ruling). The
    // widths are USER-RESIZABLE and persist in the model's column
    // config (Snapshot 3); the fallbacks are the pinned Snapshot 0
    // sizes.
    readonly property real checkboxWidth: root.columnWidth("checkbox", 52)
    readonly property real thumbWidth: root.columnWidth("thumb", 60)
    readonly property real nameWidth: root.columnWidth("name", 190)
    // The resize handles' grab band: one at each sticky boundary
    // and each trailing cell's right edge.
    readonly property real resizeHandleWidth: 10 * screenScaleFactor
    // The sticky handles are layout members, not overlays — without
    // them the sticky row overflows and the last handle ends up
    // under the header Flickable, unreachable.
    readonly property real stickyWidth: checkboxWidth + thumbWidth + nameWidth + 2 * root.resizeHandleWidth
    readonly property var defaultColumnOrder: ["Modified", "Size", "Attempts", "Status", "Object height", "Layer height", "Est. time", "Last print", "Slicer", "Extruder", "Bed", "Filament"]
    readonly property var defaultColumnWidths: {
        "Modified": 100,
        "Size": 60,
        "Attempts": 80,
        "Status": 90,
        "Object height": 112,
        "Layer height": 100,
        "Est. time": 80,
        "Last print": 100,
        "Slicer": 80,
        "Extruder": 72,
        "Bed": 52,
        "Filament": 82
    }
    // The live resize drag (null while not dragging): the preview
    // feeds columnWidth below and commits ONCE on release.
    property var columnDrag: null
    // A column can never shrink below its own heading text: the
    // static floors hold from the first frame; the header labels'
    // creation records refine them for this theme's fonts.
    property var titleMinimums: {
        "Modified": 84,
        "Size": 56,
        "Attempts": 90,
        "Status": 88,
        "Object height": 116,
        "Layer height": 108,
        "Est. time": 88,
        "Last print": 102,
        "Slicer": 74,
        "Extruder": 82,
        "Bed": 58,
        "Filament": 96,
        "thumb": 70,
        "name": 64
    }
    function recordTitleMinimum(name, width) {
        var mins = root.titleMinimums;
        mins[name] = Math.max(mins[name] || 0, width + 20 * screenScaleFactor);
        root.titleMinimums = mins;
    }
    function columnMinWidth(name) {
        var mins = root.titleMinimums;
        return mins[name] > 0 ? mins[name] : 40 * screenScaleFactor;
    }
    function columnWidth(key, fallback) {
        if (root.columnDrag !== null && root.columnDrag.key === key && root.columnDrag.width > 0) {
            return root.columnDrag.width * screenScaleFactor;
        }
        var widths = root.printerModel != null ? root.printerModel.fileManagerColumnWidths : {};
        var stored = widths[key];
        if (stored > 0) {
            // The title floor holds on EVERY path — a stored width
            // from before the floors existed must still rise to its
            // title's minimum, not render narrow forever.
            return Math.max(root.columnMinWidth(key), stored * screenScaleFactor);
        }
        return Math.max(root.columnMinWidth(key), fallback * screenScaleFactor);
    }
    function visibleTrailingColumns() {
        // [name, fallbackWidth] pairs in the user's order, minus
        // hidden columns. The width stays the FALLBACK: baking the
        // live width in rebuilt every Repeater per drag frame,
        // destroying the handle being dragged.
        var result = [];
        var order = root.printerModel != null && root.printerModel.fileManagerColumnOrder.length > 0 ? root.printerModel.fileManagerColumnOrder : root.defaultColumnOrder;
        var hidden = root.printerModel != null ? root.printerModel.fileManagerColumnHidden : [];
        for (var i = 0; i < order.length; ++i) {
            var name = order[i];
            if (hidden.indexOf(name) >= 0) {
                continue;
            }
            result.push([name, root.defaultColumnWidths[name] || 100]);
        }
        return result;
    }
    function columnVisible(name) {
        var hidden = root.printerModel != null ? root.printerModel.fileManagerColumnHidden : [];
        return hidden.indexOf(name) < 0;
    }
    function columnOrderList() {
        // The Columns popup's model: the user's order when the model
        // has one, the defaults before the config loads.
        return root.printerModel != null && root.printerModel.fileManagerColumnOrder.length > 0 ? root.printerModel.fileManagerColumnOrder : root.defaultColumnOrder;
    }
    function columnDragStart(key, base, x) {
        // The pointer rides the HEADER's frame (the console resize's
        // lesson: a local delta feeds back into its own measurement).
        root.columnDrag = {
            "key": key,
            "base": base / screenScaleFactor,
            "start": x
        };
    }
    function columnDragMove(x) {
        if (root.columnDrag === null) {
            return;
        }
        var dx = (x - root.columnDrag.start) / screenScaleFactor;
        // REASSIGN, never mutate: QML tracks the property, not the
        // object's fields — mutating meant the widths only applied
        // on release (the author's live report).
        root.columnDrag = {
            "key": root.columnDrag.key,
            "base": root.columnDrag.base,
            "start": root.columnDrag.start,
            "width": Math.max(root.columnMinWidth(root.columnDrag.key) / screenScaleFactor, Math.min(600, root.columnDrag.base + dx))
        };
    }
    function columnDragEnd() {
        if (root.columnDrag === null) {
            return;
        }
        if (root.printerModel != null && root.columnDrag.width > 0) {
            root.printerModel.setFileColumnWidth(root.columnDrag.key, root.columnDrag.width);
        }
        root.columnDrag = null;
    }
    function moveColumn(name, delta) {
        // The Columns popup's reorder arrows.
        if (root.printerModel == null) {
            return;
        }
        var order = root.printerModel.fileManagerColumnOrder.slice();
        var index = order.indexOf(name);
        var target = index + delta;
        if (index < 0 || target < 0 || target >= order.length) {
            return;
        }
        order.splice(index, 1);
        order.splice(target, 0, name);
        root.printerModel.setFileColumnOrder(order);
    }
    readonly property real trailingWidth: {
        // The live width — the rows and the header both follow the
        // pointer during a resize drag and commit once on release.
        var total = 0;
        var columns = root.visibleTrailingColumns();
        for (var i = 0; i < columns.length; ++i) {
            total += root.columnWidth(columns[i][0], columns[i][1]);
        }
        return total;
    }

    function cellText(row, column) {
        switch (column) {
        case "Modified":
            return row.modified;
        case "Size":
            return row.size;
        case "Attempts":
            return row.attempts;
        case "Status":
            return row.status;
        case "Object height":
            return row.objH;
        case "Layer height":
            return row.layerH;
        case "Est. time":
            return row.est;
        case "Last print":
            return row.lastPrint;
        case "Slicer":
            return row.slicer;
        case "Extruder":
            return row.extr;
        case "Bed":
            return row.bed;
        case "Filament":
            return row.filament;
        }
        return "";
    }

    function cellColour(row, column) {
        // A ticked row is all Cura blue with white text (the
        // author's live ruling).
        if (root.rowChecked(row)) {
            return "white";
        }
        if (column !== "Status") {
            return UM.Theme.getColor("text");
        }
        return row.statusColour === "text_inactive" ? UM.Theme.getColor("text_inactive") : row.statusColour;
    }

    // The ubiquitous .gcode suffix is noise; the rarer extensions
    // (.ufp, .nc, .gco, .g) stay — they signal a different file kind
    // (the author's live ruling).
    function displayName(name) {
        return name.endsWith(".gcode") ? name.substring(0, name.length - 6) : name;
    }

    // Selection lives in the model (Snapshot 1): the payload carries
    // the checked flag; the click toggles through the model. The
    // mock fallback keeps its old behaviour when no printer is
    // attached.
    function rowChecked(row) {
        return row.checked === true;
    }
    function toggleRow(row) {
        if (root.printerModel != null) {
            root.printerModel.toggleFileSelection(row.relpath);
        }
    }

    // Snapshot 1 helpers: the model-facing vocabulary shared by the
    // headers, the filter menus and the pagination row.
    function sortKeyFor(header) {
        switch (header) {
        case "Name":
            return "name";
        case "Modified":
            return "modified";
        case "Size":
            return "size";
        case "Attempts":
            return "attempts";
        case "Status":
            return "status";
        case "Object height":
            return "object_height";
        case "Layer height":
            return "layer_height";
        case "Est. time":
            return "estimated_time";
        case "Last print":
            return "last_print";
        case "Slicer":
            return "slicer";
        case "Extruder":
            return "extruder";
        case "Bed":
            return "bed";
        case "Filament":
            return "filament";
        }
        return "";
    }
    function filterValues(category) {
        if (root.printerModel == null) {
            return [];
        }
        var values = root.printerModel.fileManagerFilters[category];
        return values !== undefined ? values : [];
    }
    function filterActive(category) {
        return root.filterValues(category).length > 0;
    }
    function filterCount(category) {
        var counts = root.printerModel != null ? root.printerModel.fileManagerFilterCounts : {};
        return counts[category] !== undefined ? counts[category] : 0;
    }
    function filterAnyActive() {
        // "Clear all" covers the search too (the author's live
        // ruling), so a search alone lights the link.
        if (root.printerModel != null && root.printerModel.fileManagerSearch.length > 0) {
            return true;
        }
        var counts = root.printerModel != null ? root.printerModel.fileManagerFilterCounts : {};
        for (var key in counts) {
            if (counts[key] > 0) {
                return true;
            }
        }
        return false;
    }
    function clearAllFilters() {
        if (root.printerModel == null) {
            return;
        }
        root.printerModel.clearFileFilters();
        root.printerModel.setFileSearch("");
    }
    function filterOptionsFor(category) {
        if (root.printerModel == null) {
            return [];
        }
        var options = root.printerModel.fileManagerFilterOptions[category];
        return options !== undefined ? options : [];
    }
    function filterOptionRows(category) {
        // Option triples become self-contained row dicts — the row
        // delegate must not reach up through parent chains (the
        // probe showed that arithmetic is off by one and the
        // category arrives empty).
        var options = root.filterOptionsFor(category);
        var rows = [];
        for (var i = 0; i < options.length; ++i) {
            rows.push({
                    "key": options[i][0],
                    "label": options[i][1],
                    "count": options[i][2],
                    "category": category,
                    "radio": category === "modified" || category === "print_time"
                });
        }
        return rows;
    }
    function toggleFilter(category, key) {
        if (root.printerModel == null) {
            return;
        }
        var values = root.filterValues(category).slice();
        var index = values.indexOf(key);
        if (index >= 0) {
            values.splice(index, 1);
        } else {
            values.push(key);
        }
        root.printerModel.setFileFilter(category, values);
    }
    function setFilterValue(category, key) {
        // Radio semantics for the single-value categories (the
        // author's live ruling: OR-checkboxes make no sense for
        // windows and bounds). The ENGINE's autoExclusive only
        // unchecks the siblings visually — toggleFilter would still
        // accumulate both values (probe-proven: Modified held 7d
        // AND year). One value at a time, and clicking the active
        // one clears the filter.
        if (root.printerModel == null) {
            return;
        }
        var values = root.filterValues(category);
        root.printerModel.setFileFilter(category, values.indexOf(key) >= 0 ? [] : [key]);
    }
    function printStartAllowed() {
        // The start gate (the author's rulings): no active job —
        // printing OR paused — and the printer connected. Not-homed
        // and not-ready states stay allowed: the confirmation warns
        // and the watchdog explains a start that never happens.
        return root.printerModel != null && root.printerModel.monitorConnected && !root.printerModel.printActive && !root.printerModel.canResumePrint;
    }
    function thumbState(relpath) {
        var thumbs = root.printerModel != null ? root.printerModel.fileManagerThumbs : {};
        var entry = thumbs[relpath];
        return entry !== undefined ? entry.state : "none";
    }
    function thumbUrl(relpath) {
        var thumbs = root.printerModel != null ? root.printerModel.fileManagerThumbs : {};
        var entry = thumbs[relpath];
        return entry !== undefined ? entry.url : "";
    }
    // The print dialog's large variant (the list cells use the small
    // one via thumbState/thumbUrl above).
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
    // The confirmation dialog's file: the relpath inside the payload,
    // or "" while the dialog is closed.
    function confirmRelpath() {
        return root.printerModel != null && root.printerModel.filePrintConfirm !== "" ? root.printerModel.filePrintConfirm.relpath : "";
    }
    function submitNewFolder() {
        var name = createFolderField.text.trim();
        if (name !== "" && root.printerModel != null) {
            root.printerModel.fileCreateDirectory(name);
        }
        createFolderDialog.close();
    }
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
    function renameTarget() {
        return root.printerModel != null && root.printerModel.fileRenameTarget !== "" ? root.printerModel.fileRenameTarget : null;
    }
    function renameKind() {
        var target = root.renameTarget();
        return target !== null ? target.kind : "file";
    }
    function uploadConfirm() {
        return root.printerModel != null && root.printerModel.fileUploadConfirm !== "" ? root.printerModel.fileUploadConfirm : null;
    }
    function uploadName() {
        var confirm = root.uploadConfirm();
        return confirm !== null ? confirm.filename : "";
    }
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
    function confirmRename() {
        // The button and the field's Return key share this path.
        renameDialog.close();
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
    function isGcodeName(name) {
        // The host refuses to metascan/print anything else
        // ("not a valid gcode file", live-proven).
        var lower = String(name).toLowerCase();
        return lower.endsWith(".gcode") || lower.endsWith(".g") || lower.endsWith(".gco");
    }
    function rowNeedsMetadata(row) {
        // A host-parsed file always carries these; both null means
        // the host never parsed it (a legacy file dropped into
        // gcodes/). The scan entry only appears where it has work
        // to do — offering it on complete rows looked like a dead
        // option (the author's live report).
        return row.slicer === null || row.estimated_time === null;
    }
    function pageSelectionState() {
        return root.printerModel != null ? root.printerModel.fileManagerPageSelection : "none";
    }
    function walkErrorText() {
        return root.printerModel != null ? root.printerModel.fileManagerWalkError : "";
    }

    // One option row in a filter dropdown (the author's live
    // rulings: a selection NEVER dismisses the dropdown — Qt menus
    // close on item activation regardless of closePolicy, so the
    // dropdowns are Popups; Modified/Print time are radios — the
    // model holds ONE value, clicking the active one clears; Slicer
    // stays checkboxes). The row reads its category and radio-ness
    // off the hosting Popup (delegate → Column → Rectangle → Popup).
    Component {
        id: filterOptionRow
        Item {
            width: 240 * screenScaleFactor
            height: 28 * screenScaleFactor
            Rectangle {
                anchors.fill: parent
                // Inset by the border width: a flush hover rect
                // paints OVER the dropdown's outline (the author's
                // live report: the border vanished on hover).
                anchors.margins: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
                color: rowMouse.containsMouse ? UM.Theme.getColor("setting_category") : "transparent"
            }
            Item {
                anchors.left: parent.left
                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                anchors.verticalCenter: parent.verticalCenter
                width: 14 * screenScaleFactor
                height: 14 * screenScaleFactor
                // The UNCHECKED outline shows from the start (the
                // author's live report: the glyphs only appeared
                // once an option was clicked) — a circle for
                // radios, a square for checkboxes.
                Rectangle {
                    visible: root.filterValues(modelData.category).indexOf(modelData.key) < 0
                    anchors.fill: parent
                    radius: modelData.radio ? 7 * screenScaleFactor : 2 * screenScaleFactor
                    color: "transparent"
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                }
                UM.Label {
                    visible: root.filterValues(modelData.category).indexOf(modelData.key) >= 0
                    anchors.centerIn: parent
                    text: modelData.radio ? "◉" : "✓"
                    color: UM.Theme.getColor("primary")
                    font: UM.Theme.getFont("default")
                }
            }
            UM.Label {
                anchors.left: parent.left
                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + 18 * screenScaleFactor
                anchors.right: parent.right
                anchors.rightMargin: 60 * screenScaleFactor
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.label
                elide: Text.ElideRight
                font: UM.Theme.getFont("default")
            }
            UM.Label {
                anchors.right: parent.right
                anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                anchors.verticalCenter: parent.verticalCenter
                text: "(" + modelData.count + ")"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }
            MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    if (modelData.radio) {
                        root.setFilterValue(modelData.category, modelData.key);
                    } else {
                        root.toggleFilter(modelData.category, modelData.key);
                    }
                }
            }
        }
    }

    // The filter slots live inline in the Filters row below; the
    // options are [key, label, count] triples from the model and the
    // count per option sits beside its label (the author's ruling:
    // numbers next to each filter option).

    // The scrim covers the stage above the e-stop dock, which stays
    // visible and live behind it (the UX panel's placement ruling).
    Rectangle {
        id: scrim
        anchors.fill: parent
        color: "#66000000"
    }
    MouseArea {
        anchors.fill: scrim
        cursorShape: Qt.PointingHandCursor
        onClicked: root.closeRequested()
    }

    Cura.RoundedRectangle {
        id: card
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        color: UM.Theme.getColor("main_background")
        radius: UM.Theme.getSize("default_radius").width
        // Nothing may paint past the card onto the scrim or the dock
        // (the author's Snapshot 0 report: the content spilled into
        // the emergency pane on small windows).
        clip: true

        // The card swallows presses on its own background so a click
        // between rows cannot fall through to the scrim and dismiss
        // the popup mid-task (the UX panel's dismissal ruling). It
        // also re-asserts focus after every click, so the key-event
        // handlers have a home even before the shortcut lands.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onPressed: {
                if (!root.narrowMode) {
                    searchField.forceActiveFocus();
                } else {
                    focusAnchor.forceActiveFocus();
                }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height

            // Recents strip FIRST: the top 50 distinct recently
            // printed files from Moonraker history, a HORIZONTALLY
            // SCROLLING bar (the author's live request, 2026-09-10)
            // — the strip's leading spot is the author's live-test
            // reversal (Snapshot 0): "I've changed my opinion on the
            // recent prints bit. I think that does belong as the
            // first thing in the window." No local persistence at
            // all: history is the source of truth, gone files never
            // render, and there is no dismissal glyph.
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2

                UM.Label {
                    text: "Recent prints"
                    font: UM.Theme.getFont("medium_bold")
                }
                Flickable {
                    id: recentsScroller
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56 * screenScaleFactor
                    clip: true
                    contentWidth: recentsRow.width
                    contentHeight: recentsRow.height
                    boundsBehavior: Flickable.StopAtBounds
                    flickableDirection: Flickable.HorizontalFlick
                    ScrollBar.horizontal: ScrollBar {
                    }
                    Row {
                        id: recentsRow
                        height: parent.height
                        spacing: UM.Theme.getSize("default_margin").width / 2
                        Repeater {
                            model: root.activeRecents
                            Rectangle {
                                width: 168 * screenScaleFactor
                                height: 56 * screenScaleFactor
                                border.color: UM.Theme.getColor("lining")
                                border.width: UM.Theme.getSize("default_lining").width
                                radius: UM.Theme.getSize("default_radius").width
                                color: UM.Theme.getColor("main_background")
                                UM.TooltipArea {
                                    anchors.fill: parent
                                    acceptedButtons: Qt.NoButton
                                    text: modelData.name
                                }
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                                    anchors.topMargin: UM.Theme.getSize("narrow_margin").height
                                    width: 36 * screenScaleFactor
                                    height: 36 * screenScaleFactor
                                    border.color: UM.Theme.getColor("lining")
                                    border.width: UM.Theme.getSize("default_lining").width
                                    color: root.thumbState(modelData.relpath) === "ready" ? UM.Theme.getColor("setting_category") : "transparent"
                                    // The strip's own thumbnail (the
                                    // author's live request): the same
                                    // fetch/cache as the grid cells, with
                                    // the same fallbacks.
                                    Image {
                                        id: recentsThumb
                                        visible: root.thumbState(modelData.relpath) === "ready" && recentsThumb.status !== Image.Error
                                        anchors.fill: parent
                                        anchors.margins: 2 * screenScaleFactor
                                        source: root.thumbUrl(modelData.relpath)
                                        fillMode: Image.PreserveAspectFit
                                        smooth: true
                                        // Decode off the UI thread.
                                        asynchronous: true
                                    }
                                    UM.ColorImage {
                                        visible: root.thumbState(modelData.relpath) === "loading"
                                        anchors.centerIn: parent
                                        width: 12 * screenScaleFactor
                                        height: 12 * screenScaleFactor
                                        source: Qt.resolvedUrl("Hourglass.svg")
                                        color: UM.Theme.getColor("text_inactive")
                                        // The spin: a static glyph reads as dead.
                                        RotationAnimation on rotation  {
                                            from: 0
                                            to: 360
                                            duration: 2000
                                            loops: Animation.Infinite
                                            running: root.thumbState(modelData.relpath) === "loading"
                                        }
                                    }
                                    UM.Label {
                                        visible: root.thumbState(modelData.relpath) === "failed" || root.thumbState(modelData.relpath) === "none"
                                        anchors.centerIn: parent
                                        text: "◇"
                                        color: UM.Theme.getColor("text_inactive")
                                    }
                                }
                                MouseArea {
                                    // The recents strip prints too (the
                                    // author's live request): a click opens
                                    // the confirmation, the same gate as the
                                    // rows. Gone files never render here:
                                    // recents are Moonraker's history, and
                                    // unlike a local store there is no way
                                    // for the user to clean a recently
                                    // deleted entry out — so it must not
                                    // appear at all (the author's live
                                    // ruling).
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (root.printerModel != null && root.printStartAllowed() && root.isGcodeName(modelData.relpath)) {
                                            root.printerModel.fileRequestPrint(modelData.relpath);
                                        }
                                    }
                                }
                                UM.Label {
                                    id: recentsName
                                    anchors.top: parent.top
                                    anchors.topMargin: UM.Theme.getSize("narrow_margin").height
                                    anchors.left: parent.left
                                    anchors.leftMargin: 36 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").width
                                    anchors.right: parent.right
                                    anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                                    text: root.displayName(modelData.name)
                                    elide: Text.ElideMiddle
                                    // Elide, never wrap: a wrapped name
                                    // pushes the date out of the card (the
                                    // author's live report).
                                    wrapMode: Text.NoWrap
                                    font: UM.Theme.getFont("default")
                                }
                                UM.Label {
                                    anchors.top: recentsName.bottom
                                    anchors.topMargin: 2 * screenScaleFactor
                                    anchors.left: recentsName.left
                                    text: modelData.time
                                    color: UM.Theme.getColor("text_inactive")
                                    font: UM.Theme.getFont("small")
                                }
                            }
                        }
                    }
                }
            }

            // Divider: the recents strip must read as part of the
            // card, not float above the files list (the author's
            // live report).
            Rectangle {
                Layout.fillWidth: true
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            RowLayout {
                visible: !root.narrowMode
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("narrow_margin").width

                UM.Label {
                    text: "Files"
                    font: UM.Theme.getFont("large_bold")
                }
                // Breadcrumb: one directory at a time, every segment
                // clickable — a click navigates to that directory
                // (Snapshot 1 wires the mock's inert segments).
                // While a search is active the scope is the whole
                // tree, not a directory, so the breadcrumb goes
                // (the author's live ruling).
                RowLayout {
                    visible: root.printerModel == null || root.printerModel.fileManagerSearch.length === 0
                    spacing: 0
                    UM.Label {
                        // The root segment reads "<root>", not
                        // Moonraker's raw root name — "/" collided
                        // with the segment separators (the author's
                        // live ruling).
                        text: "<root>"
                        color: UM.Theme.getColor("primary")
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (root.printerModel != null) {
                                    root.printerModel.fileNavigateTo([], false);
                                }
                            }
                        }
                    }
                    // ONE delegate per segment: a Repeater with two
                    // bare children keeps only the LAST as its
                    // delegate (engine-proven in the probe — the
                    // " / " separators never made it to the tree),
                    // so the slash and the segment share a Row.
                    Repeater {
                        model: root.printerModel != null ? root.printerModel.fileManagerDirectory : []
                        Row {
                            spacing: 0
                            UM.Label {
                                text: " / "
                            }
                            UM.Label {
                                text: modelData
                                color: UM.Theme.getColor("primary")
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (root.printerModel == null) {
                                            return;
                                        }
                                        if (mouse.button === Qt.RightButton) {
                                            // The folder context menu
                                            // (the author's live
                                            // request): rename or
                                            // delete this segment's
                                            // directory.
                                            dirActionsMenu.dirPath = root.printerModel.fileManagerDirectory.slice(0, index + 1).join("/");
                                            dirActionsMenu.popup();
                                            return;
                                        }
                                        root.printerModel.fileNavigateTo(root.printerModel.fileManagerDirectory.slice(0, index + 1), false);
                                    }
                                }
                            }
                        }
                    }
                }
                UM.Label {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignRight
                    text: root.printerModel != null ? root.printerModel.fileManagerRefreshedAt : ""
                    color: UM.Theme.getColor("text_inactive")
                }
                // The bounded-window escape hatch (the author's
                // ruling: 200 jobs by default, one click loads the
                // complete history — even a file printed a thousand
                // jobs ago resolves). It sits with the refresh
                // affordance, not the recents strip (the author's
                // live ruling).
                UM.Label {
                    visible: root.printerModel != null && root.printerModel.fileManagerHistoryLoaded > 0 && !root.printerModel.fileManagerHistoryExhausted
                    text: "Load all history"
                    color: UM.Theme.getColor("primary")
                    font: UM.Theme.getFont("small")
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.fileLoadAllHistory();
                            }
                        }
                    }
                }
                // The refresh button (the author's live request):
                // re-walks the tree and re-fetches the history
                // window on demand.
                Cura.SecondaryButton {
                    text: "⟳"
                    enabled: root.printerModel != null
                    onClicked: {
                        if (root.printerModel != null) {
                            root.printerModel.refreshFileManager();
                        }
                    }
                    UM.TooltipArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.NoButton
                        text: "Refresh"
                    }
                }
            }

            // Search — name-only, full width (the author's live-test
            // ruling: filters stack UNDER the search bar, not beside).
            // The field is an Item so the in-field clear button can
            // anchor without touching the TextField's own layout
            // (anchored children inside a layout-managed field are
            // undefined behaviour).
            Item {
                visible: !root.narrowMode
                Layout.fillWidth: true
                implicitHeight: 28 * screenScaleFactor
                TextField {
                    id: searchField
                    anchors.fill: parent
                    // User typing breaks a plain `text:` binding for
                    // good; a Binding element with RestoreBinding
                    // survives the edit, so the model stays the one
                    // source of truth in both directions.
                    Binding {
                        target: searchField
                        property: "text"
                        value: root.printerModel != null ? root.printerModel.fileManagerSearch : ""
                        restoreMode: Binding.RestoreBinding
                    }
                    placeholderText: "Search by name"
                    rightPadding: searchClear.visible ? searchClear.width + 8 * screenScaleFactor : 6 * screenScaleFactor
                    // The keystroke settles for 250 ms before the
                    // search hits the model (the author's live
                    // ruling) — typing never stutters the grid.
                    onTextEdited: searchDebounce.restart()
                    Timer {
                        id: searchDebounce
                        interval: 250
                        onTriggered: {
                            if (root.printerModel != null) {
                                root.printerModel.setFileSearch(searchField.text);
                            }
                        }
                    }
                }
                // The clear button: a circled × on the right of the
                // field, shown only while a search is active (the
                // author's live request).
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
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.setFileSearch("");
                            }
                        }
                    }
                }
            }

            // One dropdown PER filter category, each self-contained
            // with scrollable multi-select options (the author's
            // live ruling). A category with an active filter turns
            // SOLID BLUE and its selected options stay inside the
            // dropdown's own menu — NO chips band spilling into the
            // form (the author's live ruling). The mock shows
            // Slicer and Modified active, the other two inactive.
            // The over-filtered empty state carries its own
            // "Clear all filters" action, so nothing needs the
            // chips row it used to point at.
            RowLayout {
                visible: !root.narrowMode
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2

                UM.Label {
                    text: "Filters:"
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("medium")
                }
                // The filter slots: BOTH button faces coexist and
                // visibility flips (never a Loader swap — rebuilding
                // the button destroys the open menu the moment a
                // selection lands, the author's live report: the
                // dropdown dismissed itself). Each menu parents to
                // its SLOT, so `y: parent.height` opens it BELOW the
                // button, not over it (the author's live report).
                // No CloseOnRelease: a selection never dismisses the
                // menu — the user does (the author's live ruling).
                Item {
                    implicitWidth: slicerPrimary.implicitWidth
                    implicitHeight: slicerPrimary.implicitHeight
                    Cura.PrimaryButton {
                        id: slicerPrimary
                        visible: root.filterActive("slicer")
                        text: "Slicer ▾ " + root.filterCount("slicer")
                        onPressed: slicerPopup.wasOpenAtPress = slicerPopup.opened
                        onClicked: {
                            if (slicerPopup.wasOpenAtPress) {
                                slicerPopup.close();
                            } else {
                                slicerPopup.open();
                            }
                        }
                    }
                    Cura.SecondaryButton {
                        visible: !root.filterActive("slicer")
                        text: "Slicer ▾"
                        onPressed: slicerPopup.wasOpenAtPress = slicerPopup.opened
                        onClicked: {
                            if (slicerPopup.wasOpenAtPress) {
                                slicerPopup.close();
                            } else {
                                slicerPopup.open();
                            }
                        }
                    }
                    Popup {
                        id: slicerPopup
                        objectName: "slicerPopup"
                        property bool wasOpenAtPress: false
                        property string category: "slicer"
                        property bool radio: false
                        y: parent.height
                        x: 0
                        padding: 0
                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
                        contentItem: Rectangle {
                            implicitWidth: 240 * screenScaleFactor
                            implicitHeight: slicerColumn.height
                            color: UM.Theme.getColor("main_background")
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width
                            Column {
                                id: slicerColumn
                                Repeater {
                                    model: root.filterOptionRows("slicer")
                                    delegate: filterOptionRow
                                }
                            }
                        }
                    }
                }
                // Modified and Print time are RADIOS (the author's
                // live ruling: OR-checkboxes make no sense for
                // windows and bounds — one at a time; clicking the
                // active radio clears the filter).
                Item {
                    implicitWidth: modifiedPrimary.implicitWidth
                    implicitHeight: modifiedPrimary.implicitHeight
                    Cura.PrimaryButton {
                        id: modifiedPrimary
                        visible: root.filterActive("modified")
                        text: "Modified ▾ " + root.filterCount("modified")
                        onPressed: modifiedPopup.wasOpenAtPress = modifiedPopup.opened
                        onClicked: {
                            if (modifiedPopup.wasOpenAtPress) {
                                modifiedPopup.close();
                            } else {
                                modifiedPopup.open();
                            }
                        }
                    }
                    Cura.SecondaryButton {
                        visible: !root.filterActive("modified")
                        text: "Modified ▾"
                        onPressed: modifiedPopup.wasOpenAtPress = modifiedPopup.opened
                        onClicked: {
                            if (modifiedPopup.wasOpenAtPress) {
                                modifiedPopup.close();
                            } else {
                                modifiedPopup.open();
                            }
                        }
                    }
                    Popup {
                        id: modifiedPopup
                        objectName: "modifiedPopup"
                        property bool wasOpenAtPress: false
                        property string category: "modified"
                        property bool radio: true
                        y: parent.height
                        x: 0
                        padding: 0
                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
                        contentItem: Rectangle {
                            implicitWidth: 240 * screenScaleFactor
                            implicitHeight: modifiedColumn.height
                            color: UM.Theme.getColor("main_background")
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width
                            Column {
                                id: modifiedColumn
                                Repeater {
                                    model: root.filterOptionRows("modified")
                                    delegate: filterOptionRow
                                }
                            }
                        }
                    }
                }
                Item {
                    implicitWidth: printTimePrimary.implicitWidth
                    implicitHeight: printTimePrimary.implicitHeight
                    Cura.PrimaryButton {
                        id: printTimePrimary
                        visible: root.filterActive("print_time")
                        text: "Print time ▾ " + root.filterCount("print_time")
                        onPressed: printTimePopup.wasOpenAtPress = printTimePopup.opened
                        onClicked: {
                            if (printTimePopup.wasOpenAtPress) {
                                printTimePopup.close();
                            } else {
                                printTimePopup.open();
                            }
                        }
                    }
                    Cura.SecondaryButton {
                        visible: !root.filterActive("print_time")
                        text: "Print time ▾"
                        onPressed: printTimePopup.wasOpenAtPress = printTimePopup.opened
                        onClicked: {
                            if (printTimePopup.wasOpenAtPress) {
                                printTimePopup.close();
                            } else {
                                printTimePopup.open();
                            }
                        }
                    }
                    Popup {
                        id: printTimePopup
                        objectName: "printTimePopup"
                        property bool wasOpenAtPress: false
                        property string category: "print_time"
                        property bool radio: true
                        y: parent.height
                        x: 0
                        padding: 0
                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
                        contentItem: Rectangle {
                            implicitWidth: 240 * screenScaleFactor
                            implicitHeight: printTimeColumn.height
                            color: UM.Theme.getColor("main_background")
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width
                            Column {
                                id: printTimeColumn
                                Repeater {
                                    model: root.filterOptionRows("print_time")
                                    delegate: filterOptionRow
                                }
                            }
                        }
                    }
                }
                // The Never printed filter is a bare TOGGLE, not a
                // dropdown (the author's live ruling on the fourth
                // filter category).
                Item {
                    implicitWidth: neverPrintedPrimary.implicitWidth
                    implicitHeight: neverPrintedPrimary.implicitHeight
                    Cura.PrimaryButton {
                        id: neverPrintedPrimary
                        visible: root.filterActive("never_printed")
                        text: "Never printed"
                        onClicked: root.toggleFilter("never_printed", "true")
                    }
                    Cura.SecondaryButton {
                        visible: !root.filterActive("never_printed")
                        text: "Never printed"
                        onClicked: root.toggleFilter("never_printed", "true")
                    }
                }
                // A clickable Clear all in the same style as the
                // [⇄ Columns] trigger (the author's live ruling);
                // it lights up only while a filter is active.
                UM.Label {
                    text: "Clear all"
                    color: root.filterAnyActive() ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: root.filterAnyActive() ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: root.clearAllFilters()
                    }
                }
            }

            // The folder strip (the author's ruling: directories
            // never join the metadata list — they carry no print
            // history and no meaningful sizes). One chip per
            // subdirectory of the CURRENT level: a click descends,
            // the breadcrumb climbs. Hidden while a search is
            // active — the scope is then the whole tree.
            // The strip scrolls horizontally: a wrapping Flow grew
            // rows of chips on folder-heavy printers and crushed the
            // grid (the author's live report).
            Flickable {
                visible: !root.narrowMode && root.printerModel != null && root.printerModel.fileManagerSearch.length === 0 && (root.printerModel.fileManagerDirectory.length > 0 || root.activeDirectories.length > 0)
                Layout.fillWidth: true
                Layout.preferredHeight: 28 * screenScaleFactor
                clip: true
                contentWidth: chipsRow.width
                contentHeight: 28 * screenScaleFactor
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.HorizontalFlick
                ScrollBar.horizontal: ScrollBar {
                }
                Row {
                    id: chipsRow
                    spacing: UM.Theme.getSize("narrow_margin").width
                    // The up directory (the author's live ruling): a
                    // chip whenever a parent exists — the root view has
                    // nowhere to go. It leads the strip, like a file
                    // manager's ".." entry.
                    Rectangle {
                        visible: root.printerModel != null && root.printerModel.fileManagerDirectory.length > 0
                        width: upName.width + 48 * screenScaleFactor
                        height: 28 * screenScaleFactor
                        radius: UM.Theme.getSize("default_radius").width
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        color: "transparent"
                        UM.Label {
                            anchors.left: parent.left
                            anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                            anchors.verticalCenter: parent.verticalCenter
                            text: "↑"
                            color: UM.Theme.getColor("primary")
                            font: UM.Theme.getFont("default")
                        }
                        UM.Label {
                            id: upName
                            anchors.left: parent.left
                            anchors.leftMargin: 16 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").width
                            anchors.verticalCenter: parent.verticalCenter
                            text: ".."
                            font: UM.Theme.getFont("default")
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (root.printerModel != null) {
                                    root.printerModel.fileNavigateTo(root.printerModel.fileManagerDirectory, true);
                                }
                            }
                        }
                    }
                    Repeater {
                        model: root.activeDirectories
                        Rectangle {
                            width: folderName.width + 48 * screenScaleFactor
                            height: 28 * screenScaleFactor
                            radius: UM.Theme.getSize("default_radius").width
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            color: "transparent"
                            UM.ColorImage {
                                anchors.left: parent.left
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                                anchors.verticalCenter: parent.verticalCenter
                                width: 16 * screenScaleFactor
                                height: 16 * screenScaleFactor
                                source: UM.Theme.getIcon("Folder")
                                color: UM.Theme.getColor("primary")
                            }
                            UM.Label {
                                id: folderName
                                anchors.left: parent.left
                                anchors.leftMargin: 16 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").width
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData
                                elide: Text.ElideRight
                                font: UM.Theme.getFont("default")
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (root.printerModel == null) {
                                        return;
                                    }
                                    if (mouse.button === Qt.RightButton) {
                                        // The folder context menu (the
                                        // author's live request).
                                        dirActionsMenu.dirPath = root.printerModel.fileManagerDirectory.concat(modelData).join("/");
                                        dirActionsMenu.popup();
                                        return;
                                    }
                                    root.printerModel.fileNavigateTo(root.printerModel.fileManagerDirectory.concat(modelData), false);
                                }
                            }
                            // The visible menu affordance: the right-click
                            // menu alone was undiscoverable (the author
                            // could not find folder deletion at all).
                            UM.Label {
                                anchors.right: parent.right
                                anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                                anchors.verticalCenter: parent.verticalCenter
                                text: "⋮"
                                font: UM.Theme.getFont("medium_bold")
                                color: UM.Theme.getColor("primary")
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (root.printerModel != null) {
                                            dirActionsMenu.dirPath = root.printerModel.fileManagerDirectory.concat(modelData).join("/");
                                            dirActionsMenu.popup();
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Bulk delete lives in the PAGINATION row, far left,
            // nudging the page controls over (the author's live
            // ruling). The header checkbox selects the whole page,
            // so the row carries only the count and the verb. The
            // verb is red, never primary-blue: blue reads as "the
            // suggested next step" for a destructive action (the
            // author's live ruling). In the real build the verb and
            // the count appear only while a selection exists.

            // Narrow mode: the file area gives way to the recents
            // strip and a resize hint (the author's live ruling —
            // a crushed window makes the manager useless, and only
            // recent prints stay).
            UM.Label {
                visible: root.narrowMode
                Layout.fillWidth: true
                Layout.fillHeight: true
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: "Make the window larger to browse and manage files.\nRecent prints stay available above."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }

            // The grid. The column HEADERS are frozen above one
            // vertical scroller that owns both the sticky block and
            // the trailing rows (the names scroll with the rows —
            // the author's Snapshot 0 reports). The trailing header
            // follows the rows' horizontal scroll one-way, so the
            // two halves can never drift.
            // An Item, NOT a Column: the scroll affordances and the
            // header are anchored to each other, and anchored
            // children inside a Column break its layout entirely
            // ("Column will not function") — the rows then scrolled
            // through the column titles (the author's live reports;
            // the fades hit it first, the chevrons hit it again).
            Item {
                visible: !root.narrowMode
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Squeeze down to just the header: a negative-height
                // scroller breaks its clip and the rows spill OVER
                // the column titles (the author's live report).
                Layout.minimumHeight: root.rowHeight
                // A horizontal wheel anywhere over the grid scrolls
                // the strip (the author's live report: the wheel
                // only worked over the scrollbar). Vertical wheels
                // pass through to the ListView untouched.
                WheelHandler {
                    orientation: Qt.Horizontal
                    onWheel: wheel => {
                        if (gridHorizontal.contentWidth > gridHorizontal.width) {
                            gridHorizontal.contentX = Math.max(0, Math.min(gridHorizontal.contentWidth - gridHorizontal.width, gridHorizontal.contentX - wheel.angleDelta.x));
                        }
                    }
                }

                Item {
                    id: gridHeader
                    objectName: "gridHeader"
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: root.rowHeight
                    // The WHOLE header rides the strip (the
                    // author's model: the list scrolls as one,
                    // headers and rows together) and clips at the
                    // grid edge.
                    clip: true
                    RowLayout {
                        width: root.stickyWidth
                        height: root.rowHeight
                        spacing: 0
                        Rectangle {
                            Layout.preferredWidth: root.checkboxWidth
                            Layout.fillHeight: true
                            color: "transparent"
                            Rectangle {
                                anchors.left: parent.left
                                anchors.leftMargin: 3 * screenScaleFactor
                                anchors.verticalCenter: parent.verticalCenter
                                width: 16 * screenScaleFactor
                                height: 16 * screenScaleFactor
                                radius: 2 * screenScaleFactor
                                border.color: UM.Theme.getColor("lining")
                                border.width: UM.Theme.getSize("default_lining").width
                                color: root.pageSelectionState() === "some" ? UM.Theme.getColor("primary") : "transparent"
                                // The page-level three states (the
                                // author's ruling): empty, a dash
                                // for a partial page, a tick for a
                                // full one. A click fills a partial
                                // page or drops a full one.
                                UM.Label {
                                    visible: root.pageSelectionState() !== "none"
                                    anchors.centerIn: parent
                                    text: root.pageSelectionState() === "all" ? "✓" : "–"
                                    color: root.pageSelectionState() === "some" ? "white" : UM.Theme.getColor("primary")
                                    font: UM.Theme.getFont("small")
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (root.printerModel != null) {
                                            root.printerModel.toggleFilePageSelection();
                                        }
                                    }
                                }
                            }
                            // A "/" separator keeps the select-all
                            // checkbox and the columns trigger from
                            // reading as one control (the author's
                            // live ruling).
                            UM.Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.verticalCenter: parent.verticalCenter
                                text: "/"
                                color: UM.Theme.getColor("text_inactive")
                            }
                            // The columns menu lives IN this header
                            // cell, beside the select-all checkbox
                            // (the author's live ruling): reorder
                            // and resize live behind it, not as
                            // text in the toolbar. The swishy ⇄
                            // glyph in the primary colour stands
                            // out where a bare hamburger was too
                            // easy to miss (the author's live
                            // report).
                            UM.Label {
                                anchors.right: parent.right
                                anchors.rightMargin: 3 * screenScaleFactor
                                anchors.verticalCenter: parent.verticalCenter
                                text: "⇄"
                                font: UM.Theme.getFont("medium_bold")
                                color: UM.Theme.getColor("primary")
                            }
                            UM.TooltipArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.NoButton
                                text: "Columns: reorder and resize"
                            }
                            // The Columns menu (Snapshot 3): the cell
                            // opens it; visibility toggles and the
                            // arrows reorder the trailing columns.
                            MouseArea {
                                // Right-side band over the glyph
                                // only: a full-cell opener sat
                                // above the select-all checkbox
                                // and ate its clicks.
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                width: 26 * screenScaleFactor
                                cursorShape: Qt.PointingHandCursor
                                onPressed: columnsPopup.wasOpenAtPress = columnsPopup.opened
                                onClicked: {
                                    if (columnsPopup.wasOpenAtPress) {
                                        columnsPopup.close();
                                    } else {
                                        columnsPopup.open();
                                    }
                                }
                            }
                            Popup {
                                id: columnsPopup
                                objectName: "columnsPopup"
                                property bool wasOpenAtPress: false
                                x: 0
                                y: parent.height
                                padding: 0
                                closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
                                // The themed surface, like every other
                                // popup in the card (the author's live
                                // report: the default background was a
                                // black slab).
                                background: Rectangle {
                                    color: UM.Theme.getColor("main_background")
                                    border.color: UM.Theme.getColor("lining")
                                    border.width: UM.Theme.getSize("default_lining").width
                                    radius: UM.Theme.getSize("default_radius").width
                                }
                                contentItem: Column {
                                    width: 240 * screenScaleFactor
                                    topPadding: UM.Theme.getSize("narrow_margin").height
                                    bottomPadding: UM.Theme.getSize("narrow_margin").height
                                    Repeater {
                                        model: root.columnOrderList()
                                        Item {
                                            width: parent.width
                                            height: 32 * screenScaleFactor
                                            // A click anywhere on the
                                            // row toggles the column;
                                            // the arrow labels keep
                                            // their own clicks. The
                                            // fill MouseArea is a
                                            // SIBLING of the Row, not
                                            // a child — an anchored
                                            // child disables the
                                            // positioner.
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    if (root.printerModel != null) {
                                                        root.printerModel.setFileColumnVisible(modelData, !root.columnVisible(modelData));
                                                    }
                                                }
                                            }
                                            Row {
                                                anchors.fill: parent
                                                leftPadding: 12 * screenScaleFactor
                                                rightPadding: 8 * screenScaleFactor
                                                spacing: 8 * screenScaleFactor
                                                // The grid: the
                                                // checkbox owns its
                                                // column, the name
                                                // takes the rest, the
                                                // arrows their slots
                                                // — padded so nothing
                                                // can overlap.
                                                // A REAL checkbox: a
                                                // bordered square that
                                                // fills blue with a white
                                                // tick when checked —
                                                // the bare tick was too
                                                // small and blue to read
                                                // either state.
                                                Rectangle {
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    width: 20 * screenScaleFactor
                                                    height: 20 * screenScaleFactor
                                                    radius: 4 * screenScaleFactor
                                                    color: root.columnVisible(modelData) ? UM.Theme.getColor("primary") : UM.Theme.getColor("main_background")
                                                    border.color: root.columnVisible(modelData) ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                                                    border.width: UM.Theme.getSize("default_lining").width
                                                    UM.Label {
                                                        anchors.centerIn: parent
                                                        text: root.columnVisible(modelData) ? "✓" : ""
                                                        color: UM.Theme.getColor("main_background")
                                                        font: UM.Theme.getFont("medium_bold")
                                                    }
                                                }
                                                UM.Label {
                                                    width: parent.width - (20 + 28 + 28) * screenScaleFactor - 3 * parent.spacing - parent.leftPadding - parent.rightPadding
                                                    height: parent.height
                                                    verticalAlignment: Text.AlignVCenter
                                                    text: modelData
                                                    elide: Text.ElideRight
                                                }
                                                UM.Label {
                                                    width: 28 * screenScaleFactor
                                                    height: parent.height
                                                    verticalAlignment: Text.AlignVCenter
                                                    horizontalAlignment: Text.AlignHCenter
                                                    // The top row has no up
                                                    // arrow, the bottom row
                                                    // no down arrow.
                                                    text: index === 0 ? "" : "▲"
                                                    color: UM.Theme.getColor("primary")
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        enabled: index > 0
                                                        onClicked: root.moveColumn(modelData, -1)
                                                    }
                                                }
                                                UM.Label {
                                                    width: 28 * screenScaleFactor
                                                    height: parent.height
                                                    verticalAlignment: Text.AlignVCenter
                                                    horizontalAlignment: Text.AlignHCenter
                                                    text: index === root.columnOrderList().length - 1 ? "" : "▼"
                                                    color: UM.Theme.getColor("primary")
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        enabled: index < root.columnOrderList().length - 1
                                                        onClicked: root.moveColumn(modelData, 1)
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        UM.Label {
                            Layout.preferredWidth: root.thumbWidth
                            Layout.fillHeight: true
                            verticalAlignment: Text.AlignVCenter
                            text: "Thumb"
                            font: UM.Theme.getFont("medium_bold")
                            Component.onCompleted: root.recordTitleMinimum("thumb", implicitWidth)
                            // No header label wraps or elides — the
                            // title floor holds the column open
                            // instead.
                            wrapMode: Text.NoWrap
                        }
                        MouseArea {
                            // The column resize handles (Snapshot 3):
                            // the cell's right edge drags its width.
                            objectName: "columnResizeHandle"
                            Layout.preferredWidth: root.resizeHandleWidth
                            Layout.fillHeight: true
                            cursorShape: Qt.SizeHorCursor
                            // A stolen grab cancels the drag and
                            // resets the widths.
                            preventStealing: true
                            Rectangle {
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.verticalCenter: parent.verticalCenter
                                width: 4 * screenScaleFactor
                                height: 20 * screenScaleFactor
                                radius: 2 * screenScaleFactor
                                color: parent.containsMouse ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                            }
                            onPressed: root.columnDragStart("thumb", root.thumbWidth, mapToItem(gridHeader, mouse.x, mouse.y).x)
                            onPositionChanged: if (pressed)
                                root.columnDragMove(mapToItem(gridHeader, mouse.x, mouse.y).x)
                            onReleased: root.columnDragEnd()
                            onCanceled: root.columnDragEnd()
                        }
                        Item {
                            Layout.preferredWidth: root.nameWidth
                            Layout.fillHeight: true
                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 4 * screenScaleFactor
                                UM.Label {
                                    // No arrow here unless Name IS
                                    // the sorted column: only ONE
                                    // column is sorted at a time,
                                    // and the sorted column carries
                                    // the arrow (the author's live
                                    // report caught the mock's
                                    // stray "Name ↓").
                                    text: "Name"
                                    font: UM.Theme.getFont("medium_bold")
                                    wrapMode: Text.NoWrap
                                    Component.onCompleted: root.recordTitleMinimum("name", implicitWidth)
                                }
                                UM.Label {
                                    text: root.printerModel != null && root.printerModel.fileManagerSortColumn === "name" ? (root.printerModel.fileManagerSortAscending ? "↑" : "↓") : ""
                                    color: UM.Theme.getColor("primary")
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFileSort("name");
                                    }
                                }
                            }
                        }
                        MouseArea {
                            objectName: "columnResizeHandle"
                            Layout.preferredWidth: root.resizeHandleWidth
                            Layout.fillHeight: true
                            cursorShape: Qt.SizeHorCursor
                            // A stolen grab cancels the drag and
                            // resets the widths.
                            preventStealing: true
                            Rectangle {
                                // The grip: a rounded pill that
                                // brightens on hover.
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.verticalCenter: parent.verticalCenter
                                width: 4 * screenScaleFactor
                                height: 20 * screenScaleFactor
                                radius: 2 * screenScaleFactor
                                color: parent.containsMouse ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                            }
                            onPressed: root.columnDragStart("name", root.nameWidth, mapToItem(gridHeader, mouse.x, mouse.y).x)
                            onPositionChanged: if (pressed)
                                root.columnDragMove(mapToItem(gridHeader, mouse.x, mouse.y).x)
                            onReleased: root.columnDragEnd()
                            onCanceled: root.columnDragEnd()
                        }
                    }
                    Flickable {
                        id: headerFlick
                        x: root.stickyWidth
                        width: parent.width - root.stickyWidth
                        height: root.rowHeight
                        clip: true
                        interactive: false
                        contentWidth: root.trailingWidth
                        contentHeight: root.rowHeight
                        contentX: gridHorizontal.contentX
                        Row {
                            height: root.rowHeight
                            Repeater {
                                model: root.visibleTrailingColumns()
                                Item {
                                    width: root.columnWidth(modelData[0], modelData[1])
                                    height: root.rowHeight
                                    // Cells clip so a tight column's
                                    // text never spills onto the
                                    // neighbouring handle.
                                    clip: true
                                    Row {
                                        anchors.left: parent.left
                                        anchors.leftMargin: 5 * screenScaleFactor
                                        anchors.right: parent.right
                                        anchors.rightMargin: 14 * screenScaleFactor
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 2 * screenScaleFactor
                                        UM.Label {
                                            text: modelData[0]
                                            font: UM.Theme.getFont("medium_bold")
                                            wrapMode: Text.NoWrap
                                            Component.onCompleted: root.recordTitleMinimum(modelData[0], implicitWidth)
                                        }
                                        // The reserved sort slot shows the
                                        // arrow ONLY on the sorted column
                                        // and nothing elsewhere (the
                                        // author's live ruling: no "—"
                                        // placeholder). It hugs the
                                        // title — at the column edge it
                                        // read as belonging to the NEXT
                                        // header and overlapped the text
                                        // on tight columns (the author's
                                        // live reports).
                                        UM.Label {
                                            text: root.printerModel != null ? (root.printerModel.fileManagerSortColumn === root.sortKeyFor(modelData[0]) ? (root.printerModel.fileManagerSortAscending ? "↑" : "↓") : "") : ""
                                            color: UM.Theme.getColor("primary")
                                        }
                                    }
                                    // A click on the header sorts that
                                    // column (clicking the current one
                                    // flips its direction — the
                                    // author's ruling).
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (root.printerModel != null) {
                                                root.printerModel.setFileSort(root.sortKeyFor(modelData[0]));
                                            }
                                        }
                                    }
                                    MouseArea {
                                        objectName: "columnResizeHandle"
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: root.resizeHandleWidth
                                        height: parent.height
                                        cursorShape: Qt.SizeHorCursor
                                        preventStealing: true
                                        Rectangle {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            anchors.verticalCenter: parent.verticalCenter
                                            width: 4 * screenScaleFactor
                                            height: 20 * screenScaleFactor
                                            radius: 2 * screenScaleFactor
                                            color: parent.containsMouse ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                                        }
                                        onPressed: root.columnDragStart(modelData[0], root.columnWidth(modelData[0], modelData[1]), mapToItem(gridHeader, mouse.x, mouse.y).x)
                                        onPositionChanged: if (pressed)
                                            root.columnDragMove(mapToItem(gridHeader, mouse.x, mouse.y).x)
                                        onReleased: root.columnDragEnd()
                                        onCanceled: root.columnDragEnd()
                                    }
                                }
                            }
                        }
                    }
                }

                ListView {
                    id: gridVertical
                    objectName: "gridVertical"
                    anchors.top: gridHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: gridHorizontal.top
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    // The row list is VIRTUALIZED (the author's
                    // live reports: an "all / page" listing
                    // beachballed the popup, and a lazy render
                    // window churned delegates per scroll frame).
                    // Only the visible rows plus the display
                    // margin instantiate; both halves ride one
                    // delegate, and the trailing half follows
                    // the horizontal scroll.
                    model: root.activeRows
                    displayMarginBeginning: 400
                    displayMarginEnd: 400
                    delegate: Item {
                        width: root.stickyWidth + root.trailingWidth
                        height: root.rowHeight
                        Rectangle {
                            width: root.stickyWidth
                            height: root.rowHeight
                            // The render window's slice starts at
                            // rowWindowStart, not 0.
                            color: root.rowChecked(modelData) ? UM.Theme.getColor("primary") : "transparent"
                            // The printing row's 3 px accent bar.
                            Rectangle {
                                visible: modelData.printing === true
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                width: 3 * screenScaleFactor
                                // White on a ticked row — the
                                // blue bar on blue was
                                // invisible (the delta
                                // review's catch, ruled by
                                // the author).
                                color: root.rowChecked(modelData) ? "white" : UM.Theme.getColor("primary")
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 3 * screenScaleFactor
                                spacing: 0
                                Rectangle {
                                    Layout.preferredWidth: root.checkboxWidth - 3 * screenScaleFactor
                                    Layout.fillHeight: true
                                    color: "transparent"
                                    Rectangle {
                                        anchors.centerIn: parent
                                        width: 16 * screenScaleFactor
                                        height: 16 * screenScaleFactor
                                        radius: 2 * screenScaleFactor
                                        // On a selected row the box
                                        // flips WHITE with a blue
                                        // tick — a blue box on a
                                        // blue row was invisible
                                        // (the author's live
                                        // report).
                                        border.color: root.rowChecked(modelData) ? "white" : UM.Theme.getColor("lining")
                                        border.width: UM.Theme.getSize("default_lining").width
                                        color: root.rowChecked(modelData) ? "white" : "transparent"
                                        UM.Label {
                                            visible: root.rowChecked(modelData)
                                            anchors.centerIn: parent
                                            text: "✓"
                                            color: UM.Theme.getColor("primary")
                                            font: UM.Theme.getFont("small")
                                        }
                                    }
                                    // Wired mock selection: a
                                    // click toggles the row's
                                    // selected state (the
                                    // author's request — this
                                    // is the ONLY wired
                                    // behaviour in the mock).
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: root.toggleRow(modelData)
                                    }
                                }
                                Rectangle {
                                    Layout.preferredWidth: root.thumbWidth
                                    Layout.fillHeight: true
                                    color: "transparent"
                                    Rectangle {
                                        anchors.centerIn: parent
                                        width: 40 * screenScaleFactor
                                        height: 40 * screenScaleFactor
                                        border.color: UM.Theme.getColor("lining")
                                        border.width: UM.Theme.getSize("default_lining").width
                                        color: root.thumbState(modelData.relpath) === "ready" ? UM.Theme.getColor("setting_category") : "transparent"
                                        // The thumbnail (Snapshot 2,
                                        // iteration 1): the fetched
                                        // image, the hourglass while
                                        // it loads, the diamond on a
                                        // failure or a file without
                                        // one.
                                        Image {
                                            id: thumbImage
                                            // An image that fails to
                                            // render falls back to the
                                            // placeholder too — never a
                                            // stuck blank cell.
                                            visible: root.thumbState(modelData.relpath) === "ready" && thumbImage.status !== Image.Error
                                            anchors.fill: parent
                                            anchors.margins: 2 * screenScaleFactor
                                            source: root.thumbUrl(modelData.relpath)
                                            fillMode: Image.PreserveAspectFit
                                            smooth: true
                                            // Decode off the UI
                                            // thread.
                                            asynchronous: true
                                        }
                                        UM.ColorImage {
                                            visible: root.thumbState(modelData.relpath) === "loading"
                                            anchors.centerIn: parent
                                            width: 16 * screenScaleFactor
                                            height: 16 * screenScaleFactor
                                            source: Qt.resolvedUrl("Hourglass.svg")
                                            color: UM.Theme.getColor("text_inactive")
                                            // The spin: a static glyph reads as dead.
                                            RotationAnimation on rotation  {
                                                from: 0
                                                to: 360
                                                duration: 2000
                                                loops: Animation.Infinite
                                                running: root.thumbState(modelData.relpath) === "loading"
                                            }
                                        }
                                        UM.Label {
                                            visible: root.thumbState(modelData.relpath) === "failed" || root.thumbState(modelData.relpath) === "none"
                                            anchors.centerIn: parent
                                            text: "◇"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                    }
                                }
                                Item {
                                    Layout.preferredWidth: root.nameWidth
                                    Layout.fillHeight: true
                                    UM.TooltipArea {
                                        anchors.fill: parent
                                        acceptedButtons: Qt.NoButton
                                        text: modelData.name
                                    }
                                    Column {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 2 * screenScaleFactor
                                        UM.Label {
                                            width: parent.width - 6 * screenScaleFactor
                                            text: root.displayName(modelData.name)
                                            wrapMode: Text.NoWrap
                                            elide: Text.ElideMiddle
                                            font: UM.Theme.getFont("default")
                                            color: root.rowChecked(modelData) ? "white" : UM.Theme.getColor("text")
                                        }
                                        // The folder breadcrumb (the
                                        // author's live request):
                                        // while a search is active,
                                        // same-named files in
                                        // different folders must be
                                        // tellable — a dimmed,
                                        // elided path under the
                                        // name.
                                        UM.Label {
                                            visible: root.printerModel != null && root.printerModel.fileManagerSearch.length > 0 && modelData.folder !== ""
                                            width: parent.width - 6 * screenScaleFactor
                                            text: modelData.folder
                                            wrapMode: Text.NoWrap
                                            elide: Text.ElideMiddle
                                            font: UM.Theme.getFont("small")
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        Row {
                                            spacing: 4 * screenScaleFactor
                                            // The printing badge is
                                            // the accent bar plus a
                                            // small printer glyph,
                                            // not a text label (the
                                            // author's live report:
                                            // icons, not words).
                                            UM.ColorImage {
                                                visible: modelData.printing === true
                                                width: 14 * screenScaleFactor
                                                height: 14 * screenScaleFactor
                                                source: UM.Theme.getIcon("Printer")
                                                color: root.rowChecked(modelData) ? "white" : UM.Theme.getColor("primary")
                                            }
                                        }
                                    }
                                    Menu {
                                        id: rowActionsMenu
                                        MenuItem {
                                            // Only where the host
                                            // has left the columns
                                            // empty: gcode files
                                            // whose metadata was
                                            // never parsed (the host
                                            // refuses non-gcode with
                                            // "not a valid gcode
                                            // file", and a rescan of
                                            // a complete row changes
                                            // nothing — both
                                            // live-proven; the
                                            // author's reports).
                                            enabled: root.printerModel != null && root.isGcodeName(modelData.relpath) && root.rowNeedsMetadata(modelData)
                                            text: "Scan metadata"
                                            onTriggered: {
                                                if (root.printerModel != null) {
                                                    root.printerModel.fileScanMetadata(modelData.relpath);
                                                }
                                            }
                                        }
                                        MenuItem {
                                            // Snapshot 2: Print opens the
                                            // confirmation; every row's entry
                                            // stands down while one is up and
                                            // while a job is active or the
                                            // printer is disconnected (the
                                            // author's rulings).
                                            enabled: root.printerModel != null && root.printerModel.filePrintConfirm === "" && root.printStartAllowed() && root.isGcodeName(modelData.relpath)
                                            text: "Print"
                                            onTriggered: {
                                                if (root.printerModel != null) {
                                                    root.printerModel.fileRequestPrint(modelData.relpath);
                                                }
                                            }
                                        }
                                        MenuItem {
                                            // Snapshot 3: the
                                            // printing file never
                                            // offers the mutations
                                            // (the author's gate).
                                            enabled: root.printerModel != null && root.printerModel.monitorConnected && !modelData.printing
                                            text: "Rename"
                                            onTriggered: {
                                                if (root.printerModel != null) {
                                                    root.printerModel.fileRequestRename(modelData.relpath);
                                                }
                                            }
                                        }
                                        MenuItem {
                                            // Snapshot 2: stream the file and
                                            // load it into Cura.
                                            enabled: root.printerModel != null && root.printerModel.monitorConnected
                                            text: "Download"
                                            onTriggered: {
                                                if (root.printerModel != null) {
                                                    root.printerModel.fileDownload(modelData.relpath);
                                                }
                                            }
                                        }
                                        MenuItem {
                                            enabled: root.printerModel != null && root.printerModel.monitorConnected && !modelData.printing
                                            text: "Delete"
                                            onTriggered: {
                                                if (root.printerModel != null) {
                                                    root.printerModel.fileRequestDeleteFile(modelData.relpath);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            // Row-level pointer surface (the
                            // author's live requests):
                            // double-click opens the print
                            // confirmation, right-click opens
                            // the actions menu — both stand
                            // down while a print is active.
                            // The surface sits OUTSIDE the
                            // RowLayout as the row Rectangle's
                            // last child: a MouseArea anchored
                            // inside a layout is undefined
                            // behaviour (Cura logged the
                            // warning once per row on open)
                            // and it still renders above the
                            // row's cells from here.
                            MouseArea {
                                anchors.fill: parent
                                // The new parent is the row
                                // Rectangle, which starts 3 sf
                                // left of the old RowLayout
                                // parent: the checkbox
                                // exclusion becomes the plain
                                // checkbox width.
                                anchors.leftMargin: root.checkboxWidth
                                cursorShape: Qt.PointingHandCursor
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                onDoubleClicked: {
                                    if (root.printerModel != null && root.printStartAllowed() && root.isGcodeName(modelData.relpath)) {
                                        root.printerModel.fileRequestPrint(modelData.relpath);
                                    }
                                }
                                onClicked: {
                                    if (mouse.button === Qt.RightButton && root.printerModel != null) {
                                        rowActionsMenu.popup();
                                    }
                                }
                            }
                        }
                        Item {
                            // The frozen columns end here: the
                            // sliding trailing half clips at this
                            // edge so it can never paint over the
                            // names (the author's live ruling).
                            x: root.stickyWidth
                            width: root.trailingWidth
                            height: root.rowHeight
                            clip: true
                            Item {
                                id: rowDelegate
                                x: -gridHorizontal.contentX
                                width: root.trailingWidth
                                height: root.rowHeight
                                property var rowData: modelData
                                Rectangle {
                                    anchors.fill: parent
                                    color: root.rowChecked(modelData) ? UM.Theme.getColor("primary") : "transparent"
                                }
                                MouseArea {
                                    // Double-click-to-print covers the
                                    // trailing half too (the author's
                                    // live request).
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onDoubleClicked: {
                                        if (root.printerModel != null && root.printStartAllowed() && root.isGcodeName(modelData.relpath)) {
                                            root.printerModel.fileRequestPrint(modelData.relpath);
                                        }
                                    }
                                }
                                Row {
                                    Repeater {
                                        model: root.visibleTrailingColumns()
                                        Item {
                                            width: root.columnWidth(modelData[0], modelData[1])
                                            height: root.rowHeight
                                            Row {
                                                anchors.left: parent.left
                                                anchors.right: parent.right
                                                anchors.verticalCenter: parent.verticalCenter
                                                // Breathing room so an
                                                // elided cell never reads
                                                // as joined to its
                                                // neighbour (the author's
                                                // live report).
                                                anchors.leftMargin: 5 * screenScaleFactor
                                                anchors.rightMargin: 5 * screenScaleFactor
                                                spacing: 4 * screenScaleFactor
                                                // On a ticked row the
                                                // status word goes white
                                                // and its colour survives
                                                // as a dot beside it (the
                                                // author's ruling — the
                                                // colour coding matters
                                                // most while curating a
                                                // bulk delete).
                                                Rectangle {
                                                    visible: modelData[0] === "Status" && root.rowChecked(rowDelegate.rowData)
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    width: 8 * screenScaleFactor
                                                    height: 8 * screenScaleFactor
                                                    radius: 4 * screenScaleFactor
                                                    color: rowDelegate.rowData.statusColour === "text_inactive" ? UM.Theme.getColor("text_inactive") : rowDelegate.rowData.statusColour
                                                }
                                                UM.Label {
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    text: root.cellText(rowDelegate.rowData, modelData[0])
                                                    color: root.cellColour(rowDelegate.rowData, modelData[0])
                                                    // Cells elide, never wrap or
                                                    // overlap: the cap makes the
                                                    // elide engage (an uncapped
                                                    // label keeps its implicit
                                                    // width), minus the ticked
                                                    // Status dot's 12 px.
                                                    wrapMode: Text.NoWrap
                                                    elide: Text.ElideRight
                                                    width: Math.min(implicitWidth, parent.width - (modelData[0] === "Status" && root.rowChecked(rowDelegate.rowData) ? 12 : 0) * screenScaleFactor)
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    ScrollBar.vertical: ScrollBar {
                    }
                }

                Flickable {
                    id: gridHorizontal
                    anchors.left: gridVertical.left
                    anchors.right: gridVertical.right
                    // Anchored to the card's bottom, NOT to the
                    // grid's: a grid↔strip anchor pair is a binding
                    // loop that collapsed the ListView to zero
                    // height — the author's live report: no rows
                    // at all.
                    anchors.bottom: parent.bottom
                    height: 12 * screenScaleFactor
                    contentWidth: root.stickyWidth + root.trailingWidth
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    flickableDirection: Flickable.HorizontalFlick
                    ScrollBar.horizontal: ScrollBar {
                    }
                }

                // Scroll affordances, the author's design: small
                // chevrons CENTRED in the data section, overlaying
                // the table — an up arrow near the top while more
                // data is above, a down arrow near the bottom
                // while more data is below (the earlier fade/
                // always-on-scrollbar attempt regressed the table
                // and was reverted).
                UM.Label {
                    text: "↑"
                    visible: gridVertical.height > 0 && gridVertical.contentY > 2
                    anchors.top: gridVertical.top
                    anchors.horizontalCenter: gridVertical.horizontalCenter
                    anchors.topMargin: 4 * screenScaleFactor
                    // Same glyph family and colour as the column
                    // sort arrows (the author's live ruling).
                    color: UM.Theme.getColor("primary")
                    font: UM.Theme.getFont("medium_bold")
                }
                UM.Label {
                    text: "↓"
                    visible: gridVertical.height > 0 && gridVertical.contentY < gridVertical.contentHeight - gridVertical.height - 2
                    anchors.bottom: gridVertical.bottom
                    anchors.horizontalCenter: gridVertical.horizontalCenter
                    anchors.bottomMargin: 4 * screenScaleFactor
                    color: UM.Theme.getColor("primary")
                    font: UM.Theme.getFont("medium_bold")
                }

                // A walk failure with a cached listing still says
                // so — the error face below needs an EMPTY grid, so
                // a failed refresh used to read as silence.
                Item {
                    visible: root.printerModel != null && root.printerModel.fileManagerWalkError !== ""
                    anchors.top: gridVertical.top
                    anchors.left: gridVertical.left
                    anchors.right: gridVertical.right
                    height: 24 * screenScaleFactor
                    Rectangle {
                        anchors.fill: parent
                        color: UM.Theme.getColor("warning")
                    }
                    UM.Label {
                        anchors.centerIn: parent
                        text: root.printerModel != null ? root.printerModel.fileManagerWalkError : ""
                        font: UM.Theme.getFont("default")
                    }
                }

                // The live grid's empty face (Snapshot 1): a walk
                // failure, the first load, an empty printer, or an
                // over-filtered view — each with its own copy and
                // its own way out. The mock never shows it (its
                // synthetic rows are the fallback).
                Item {
                    visible: root.printerModel != null && root.activeRows.length === 0
                    anchors.fill: gridVertical
                    Column {
                        anchors.centerIn: parent
                        spacing: UM.Theme.getSize("narrow_margin").height
                        UM.Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.printerModel != null ? (root.walkErrorText() !== "" ? "Couldn't load the file list" : (root.printerModel.fileManagerRefreshedAt === "Not yet refreshed" ? "Loading files…" : (root.printerModel.fileManagerEmptyKind === "over_filtered" ? "No files match the current filters" : (root.activeDirectories.length > 0 ? "No files in this folder" : "No files here yet")))) : ""
                            font: UM.Theme.getFont("medium_bold")
                        }
                        UM.Label {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.printerModel != null ? (root.walkErrorText() !== "" ? root.walkErrorText() : (root.printerModel.fileManagerRefreshedAt === "Not yet refreshed" ? "Reading the printer's file list…" : (root.printerModel.fileManagerEmptyKind === "over_filtered" ? "Adjust the search and filters, or clear them all." : (root.activeDirectories.length > 0 ? "Open a folder above, or send a print from Cura and it will appear here." : "Send a print from Cura and it will appear here.")))) : ""
                            color: UM.Theme.getColor("text_inactive")
                        }
                        Cura.SecondaryButton {
                            visible: root.printerModel != null && (root.walkErrorText() !== "" || (root.printerModel.fileManagerRefreshedAt !== "Not yet refreshed" && root.printerModel.fileManagerEmptyKind === "over_filtered"))
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.walkErrorText() !== "" ? "Retry" : "Clear all filters"
                            onClicked: {
                                if (root.printerModel != null) {
                                    if (root.walkErrorText() !== "") {
                                        root.printerModel.refreshFileManager();
                                    } else {
                                        root.clearAllFilters();
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Pagination (mock: one resident page).
            RowLayout {
                visible: !root.narrowMode
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2

                Rectangle {
                    Layout.preferredWidth: 88 * screenScaleFactor
                    Layout.preferredHeight: 28 * screenScaleFactor
                    radius: UM.Theme.getSize("default_radius").width
                    color: bulkDeleteHover.containsMouse ? "#26d32f2f" : "transparent"
                    border.color: "#d32f2f"
                    border.width: 2 * screenScaleFactor
                    // The verb and the count are GONE, not greyed,
                    // while no selection exists (the author's live
                    // ruling); the delete itself arrives with
                    // Snapshot 3.
                    visible: root.printerModel == null || root.printerModel.fileManagerSelected > 0
                    UM.Label {
                        anchors.centerIn: parent
                        text: "Delete"
                        color: "#d32f2f"
                        font: UM.Theme.getFont("medium_bold")
                    }
                    MouseArea {
                        id: bulkDeleteHover
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.fileRequestDelete();
                            }
                        }
                    }
                }
                UM.Label {
                    text: (root.printerModel != null ? root.printerModel.fileManagerSelected : 0) + " selected ✕"
                    font: UM.Theme.getFont("medium_bold")
                    visible: root.printerModel == null || root.printerModel.fileManagerSelected > 0
                    // The count IS the global clear (the author's
                    // ruling): with selection accumulated across
                    // pages, only the count can drop it all without
                    // perturbing the view.
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.clearFileSelection();
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
                    // author's live report). The mock keeps its
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
                    // author's live report: the row-bottom-left
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
                                    Item {
                                        anchors.left: parent.left
                                        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: 14 * screenScaleFactor
                                        height: 14 * screenScaleFactor
                                        Rectangle {
                                            visible: root.printerModel == null || String(root.printerModel.fileManagerPageSize) !== String(modelData)
                                            anchors.fill: parent
                                            radius: 7 * screenScaleFactor
                                            color: "transparent"
                                            border.color: UM.Theme.getColor("lining")
                                            border.width: UM.Theme.getSize("default_lining").width
                                        }
                                        UM.Label {
                                            visible: root.printerModel != null && String(root.printerModel.fileManagerPageSize) === String(modelData)
                                            anchors.centerIn: parent
                                            text: "◉"
                                            color: UM.Theme.getColor("primary")
                                            font: UM.Theme.getFont("default")
                                        }
                                    }
                                    UM.Label {
                                        anchors.left: parent.left
                                        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + 18 * screenScaleFactor
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
                                                root.printerModel.setFilePageSize(modelData);
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
                            root.printerModel.setFilePage(root.printerModel.fileManagerPageIndex - 1);
                        }
                    }
                }
                Cura.SecondaryButton {
                    visible: root.printerModel == null || root.activeRows.length > 0
                    text: "›"
                    enabled: root.printerModel != null && root.printerModel.monitorConnected && root.printerModel.fileManagerPageIndex < root.printerModel.fileManagerPageCount
                    onClicked: {
                        if (root.printerModel != null) {
                            root.printerModel.setFilePage(root.printerModel.fileManagerPageIndex + 1);
                        }
                    }
                }
            }

            // Bottom band: the disk readout (the containing mount's
            // free space) and the single Close button.
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2

                Cura.SecondaryButton {
                    // The New-folder dialog (the author's live
                    // request).
                    text: "New folder…"
                    enabled: root.printerModel != null && root.printerModel.monitorConnected
                    onClicked: createFolderDialog.open()
                }
                Cura.SecondaryButton {
                    // Snapshot 3 upload (the author's ruling): LOCAL
                    // gcode files only — sliced prints already upload
                    // from the Preview view. Bottom-left, beside the
                    // disk readout (the author's placement).
                    text: "Upload file…"
                    enabled: root.printerModel != null && root.printerModel.monitorConnected
                    onClicked: filePicker.open()
                }
                UM.Label {
                    text: root.printerModel != null ? root.printerModel.fileManagerDiskText : ""
                    color: UM.Theme.getColor("text_inactive")
                }
                UM.Label {
                    Layout.fillWidth: true
                    // The latest note (refusals and outcomes — the
                    // popup's own feedback surface), and the
                    // right-click hint when nothing needs saying.
                    text: root.printerModel != null && root.printerModel.fileManagerNote !== "" ? root.printerModel.fileManagerNote : "Right-click a file or folder for actions"
                    color: UM.Theme.getColor("text_inactive")
                    elide: Text.ElideRight
                }
                Cura.PrimaryButton {
                    text: "Close"
                    onClicked: root.closeRequested()
                }
            }
        }
    }
}
