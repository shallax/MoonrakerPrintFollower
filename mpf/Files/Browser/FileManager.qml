import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../Widgets"
import "Dialogs"
import "../../Resources/Theme"

// The file-manager popup (3.6.0): the real QML surface over the
// published model slice — no synthetic rows ship (the
// ruling). The popup is exempt from the no-reflow rule by the
// ruling ("Reflowing the file manager is fine, there's
// nothing critical on that"); every state-gated visibility
// expression lands in the test's line-level allow list citing that
// ruling.
Item {
    id: root

    property bool open: false
    // The columns popup's state, exposed for the dashboard's Esc
    // ladder through FUNCTIONS, never an alias: an alias to the
    // popup's id resolves at binding time, before the popup exists
    // (the forward-reference latch), while function bodies resolve
    // at call time.
    function columnsPopupOpen() {
        return grid.columnsPopupOpen();
    }
    // The model behind the face (Snapshot 1): the published page
    // slice, recents, breadcrumb, disk and view metadata. Null in
    // the engine gate and captures — the mock rows below then serve
    // as the fallback so those still render.
    property var printerModel: null
    // No mock fallbacks (the ruling: the synthetic data
    // must not ship) — without a printer the face simply renders
    // empty; the engine gate and captures pass a stub model when
    // they need faces.
    readonly property var activeRows: root.printerModel != null ? root.printerModel.fileManagerRows : []
    readonly property var activeRecents: root.printerModel != null ? root.printerModel.fileManagerRecents : []
    readonly property var activeDirectories: root.printerModel != null ? root.printerModel.fileManagerDirectories : []
    // All dismissal paths request the close through this signal and
    // the dashboard owns the flag — assigning `open` internally would
    // break the binding and leave the button unable to reopen the
    // popup (the Snapshot 0 report).
    signal closeRequested

    visible: open
    focus: true
    // Opening the popup triggers the fetch (refetch on open — the
    // ruling): the walk and the history window ride the
    // model's own lane. Nothing else flips this; the button in the
    // dashboard only sets `open`.
    onOpenChanged: {
        if (open && root.printerModel != null) {
            // Fetching republishes fileManagerChanged, the same signal
            // that drives open. Finish this binding evaluation first.
            var model = root.printerModel;
            Qt.callLater(function () {
                if (root.open && root.printerModel === model) {
                    model.openFileManager();
                }
            });
        }
        if (!open) {
            // The columns popup lives in the window's overlay, so
            // hiding the card would leave it floating over the stage
            // (the probe's finding).
            grid.closeColumnsPopup();
        }
    }
    function closeColumnsPopup() {
        grid.closeColumnsPopup();
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
                uploadConfirmDialog.close();
                uploadProgressDialog.open();
            }
        }
    }
    Connections {
        target: root
        function onOpenChanged() {
            if (!root.open) {
                printConfirmDialog.close();
                deleteConfirmDialog.close();
                renameDialog.close();
                uploadConfirmDialog.close();
                uploadProgressDialog.close();
                createFolderDialog.close();
            }
        }
    }
    onVisibleChanged: {
        if (visible) {
            // Esc only reached us while a descendant held focus (the
            // Snapshot 0 reports) — give focus to a real
            // target on every open. The search field is the natural
            // first stop; in narrow mode (no search field) the focus
            // anchor below carries it.
            if (!root.narrowMode) {
                searchBar.focusField();
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

    // Esc has three layers, in order: an open dialog's content owns
    // the key (Esc CANCELS it — the ruling); this root
    // ladder is the fallback while focus sits on a plain item
    // inside the popup; the monitor's window-level shortcut owns
    // the rest of the stage. The model's fileManagerOpen is the
    // single source of truth for that shortcut's branch.

    // A FocusScope that always exists and always accepts focus, so
    // Esc has a home even when the search field does not exist (the
    // Snapshot 0 report: narrow windows have no search bar,
    // and then Esc had nothing to bubble from).
    FocusScope {
        id: focusAnchor
        anchors.fill: parent
        focus: true
    }

    // The print confirmation (Snapshot 2): filename, est. time,
    // filament, the target printer's name and a readiness line, with
    // the pinned verbs. While it is up, Esc cancels IT — the top
    // layer owns the key (the ruling), and every row's
    // Print entry stands down.
    PrintConfirmDialog {
        id: printConfirmDialog
        printerModel: root.printerModel
        anchors.centerIn: root
        startAllowed: root.printStartAllowed()
    }

    // The delete confirmation (Snapshot 3): the selection's count, a
    // blocked line when some of it is printing, and the pinned verbs.
    DeleteConfirmDialog {
        id: deleteConfirmDialog
        printerModel: root.printerModel
        anchors.centerIn: root
    }

    // The New-folder dialog (a live request): a plain
    // name, Enter creates, Esc cancels.
    CreateFolderDialog {
        id: createFolderDialog
        printerModel: root.printerModel
        anchors.centerIn: root
    }

    // The rename dialog (Snapshot 3): the name field pre-filled on
    // open, a live collision line while typing (the host's move
    // silently overwrites — the dialog asks first, round-1 C2/C3),
    // and the pinned verbs.
    RenameDialog {
        id: renameDialog
        printerModel: root.printerModel
        anchors.centerIn: root
    }

    // The folder context menu (a live request): right-click
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
    UploadConfirmDialog {
        id: uploadConfirmDialog
        printerModel: root.printerModel
        anchors.centerIn: root
    }

    // The upload progress popup (Snapshot 3 finish — the
    // live request): a bar while the upload runs, and a success/fail
    // verdict in the SAME popup at the end.
    UploadProgressDialog {
        id: uploadProgressDialog
        printerModel: root.printerModel
        anchors.centerIn: root
    }

    // The download progress window (the live request): the file
    // manager's own control — a bar, the percentage and a Cancel.
    // It opens while a save download streams and closes when the
    // stream ends or the user cancels.
    DownloadProgressDialog {
        id: downloadProgressDialog
        printerModel: root.printerModel
        anchors.centerIn: root
    }

    // The local-file picker for uploads (Snapshot 3): gcode files
    // only — sliced prints upload from the Preview view (the
    // ruling).
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
    // strip and a resize hint (the live ruling: a crushed
    // window makes the manager useless — only recent prints stay).
    readonly property bool narrowMode: card.width < 700 * screenScaleFactor || card.height < 500 * screenScaleFactor

    // The render window (the live report: a 400-file
    // "all / page" listing beachballed the popup — two panes of
    // eager delegates). Only the rows inside the window plus a
    // margin instantiate; the scroll geometry stays honest.
    readonly property var defaultColumnOrder: ["Modified", "Size", "Attempts", "Status", "Object height", "Layer height", "Est. time", "Last print", "Slicer", "Extruder", "Bed", "Filament"]
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

    // The ubiquitous .gcode suffix is noise; the rarer extensions
    // (.ufp, .nc, .gco, .g) stay — they signal a different file kind
    // (the live ruling).
    function displayName(name) {
        return name.endsWith(".gcode") ? name.substring(0, name.length - 6) : name;
    }

    function filterValues(category) {
        if (root.printerModel == null) {
            return [];
        }
        var values = root.printerModel.fileManagerFilters[category];
        return values !== undefined ? values : [];
    }
    function clearAllFilters() {
        if (root.printerModel == null) {
            return;
        }
        root.printerModel.clearFileFilters();
        root.printerModel.setFileSearch("");
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
        // live ruling: OR-checkboxes make no sense for
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
        // The start gate (the rulings): no active job —
        // printing OR paused — and the printer connected. Not-homed
        // and not-ready states stay allowed: the confirmation warns
        // and the watchdog explains a start that never happens.
        return root.printerModel != null && root.printerModel.monitorConnected && !root.printerModel.printActive && !root.printerModel.canResumePrint;
    }
    // The print dialog's large variant (the list cells use the small
    // one via thumbState/thumbUrl above).

    // The confirmation dialog's file: the relpath inside the payload,
    // or "" while the dialog is closed.

    function isGcodeName(name) {
        // The host refuses to metascan/print anything else
        // ("not a valid gcode file", live-proven).
        var lower = String(name).toLowerCase();
        return lower.endsWith(".gcode") || lower.endsWith(".g") || lower.endsWith(".gco");
    }

    // The scrim covers the stage above the e-stop dock, which stays
    // visible and live behind it (the UX panel's placement ruling).
    Rectangle {
        id: scrim
        anchors.fill: parent
        color: MoonrakerTheme.scrim
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
        // (the Snapshot 0 report: the content spilled into
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
                    searchBar.focusField();
                } else {
                    focusAnchor.forceActiveFocus();
                }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height

            // Recents strip FIRST (FileManagerRecents.qml): history is
            // the source of truth, so gone files never render and there
            // is no dismissal glyph.
            FileManagerRecents {
                recents: root.activeRecents
                thumbs: root.printerModel != null ? root.printerModel.fileManagerThumbs : ({})
                displayName: root.displayName
                onPrintRequested: function (relpath) {
                    // The strip reports the intent; the gate needs the
                    // connection and job state only the shell holds.
                    if (root.printerModel != null && root.printStartAllowed() && root.isGcodeName(relpath)) {
                        root.printerModel.fileRequestPrint(relpath);
                    }
                }
            }

            // Divider: the recents strip must read as part of the
            // card, not float above the files list (the
            // live report).
            Rectangle {
                Layout.fillWidth: true
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            FileManagerToolbar {
                visible: !root.narrowMode
                directory: root.printerModel != null ? root.printerModel.fileManagerDirectory : []
                search: root.printerModel != null ? root.printerModel.fileManagerSearch : ""
                refreshedAt: root.printerModel != null ? root.printerModel.fileManagerRefreshedAt : ""
                canLoadAllHistory: root.printerModel != null && root.printerModel.fileManagerHistoryLoaded > 0 && !root.printerModel.fileManagerHistoryExhausted
                connected: root.printerModel != null
                onNavigateRequested: function (segments, up) {
                    if (root.printerModel != null) {
                        root.printerModel.fileNavigateTo(segments, up);
                    }
                }
                onDirectoryMenuRequested: function (path) {
                    dirActionsMenu.dirPath = path;
                    dirActionsMenu.popup();
                }
                onRefreshRequested: {
                    if (root.printerModel != null) {
                        root.printerModel.refreshFileManager();
                    }
                }
                onLoadAllHistoryRequested: {
                    if (root.printerModel != null) {
                        root.printerModel.fileLoadAllHistory();
                    }
                }
            }

            // Search — name-only, full width; the filters stack UNDER
            // it (FileManagerSearch.qml owns the field, its clear
            // button and the 250 ms settle).
            FileManagerSearch {
                id: searchBar
                visible: !root.narrowMode
                search: root.printerModel != null ? root.printerModel.fileManagerSearch : ""
                onSearchSettled: function (text) {
                    if (root.printerModel != null) {
                        root.printerModel.setFileSearch(text);
                    }
                }
                onSearchCleared: {
                    if (root.printerModel != null) {
                        root.printerModel.setFileSearch("");
                    }
                }
            }

            // The filter row (FileManagerFilters.qml): one dropdown
            // per category, each self-contained with its scrollable
            // options, plus the Never printed toggle and Clear all.
            // The row reports intent — every model write stays here.
            FileManagerFilters {
                visible: !root.narrowMode
                filters: root.printerModel != null ? root.printerModel.fileManagerFilters : ({})
                filterCounts: root.printerModel != null ? root.printerModel.fileManagerFilterCounts : ({})
                filterOptions: root.printerModel != null ? root.printerModel.fileManagerFilterOptions : ({})
                search: root.printerModel != null ? root.printerModel.fileManagerSearch : ""
                onFilterToggled: function (category, key) {
                    root.toggleFilter(category, key);
                }
                onFilterValueSet: function (category, key) {
                    root.setFilterValue(category, key);
                }
                onClearAllRequested: root.clearAllFilters()
            }

            // The folder strip (the ruling: directories
            // never join the metadata list — they carry no print
            // history and no meaningful sizes). One chip per
            // subdirectory of the CURRENT level: a click descends,
            // the breadcrumb climbs. Hidden while a search is
            // active — the scope is then the whole tree.
            // The strip scrolls horizontally: a wrapping Flow grew
            // rows of chips on folder-heavy printers and crushed the
            // grid (the live report).
            FileDirectoryStrip {
                visible: !root.narrowMode && root.printerModel != null && root.printerModel.fileManagerSearch.length === 0 && (root.printerModel.fileManagerDirectory.length > 0 || root.activeDirectories.length > 0)
                directory: root.printerModel != null ? root.printerModel.fileManagerDirectory : []
                directories: root.activeDirectories
                onNavigateRequested: function (segments, up) {
                    if (root.printerModel != null) {
                        root.printerModel.fileNavigateTo(segments, up);
                    }
                }
                onDirectoryMenuRequested: function (path) {
                    dirActionsMenu.dirPath = path;
                    dirActionsMenu.popup();
                }
            }

            // Narrow mode: the file area gives way to the recents
            // strip and a resize hint (the live ruling —
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
            // the Snapshot 0 reports). The trailing header
            // follows the rows' horizontal scroll one-way, so the
            // two halves can never drift.
            // An Item, NOT a Column: the scroll affordances and the
            // header are anchored to each other, and anchored
            // children inside a Column break its layout entirely
            // ("Column will not function") — the rows then scrolled
            // through the column titles (the live reports;
            // the fades hit it first, the chevrons hit it again).
            FileGrid {
                id: grid
                visible: !root.narrowMode
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Squeeze down to just the header: a negative-height
                // scroller breaks its clip and the rows spill OVER
                // the column titles (the live report).
                Layout.minimumHeight: grid.rowHeight
                activeRows: root.activeRows
                activeDirectories: root.activeDirectories
                defaultColumnOrder: root.defaultColumnOrder
                narrowMode: root.narrowMode
                displayName: root.displayName
                printStartAllowed: root.printStartAllowed
                isGcodeName: root.isGcodeName
                printerModel: root.printerModel
                // Every write below is the card's, applied on the
                // table's report (the card keeps the model).
                onColumnVisibilityToggled: function (name, visible) {
                    if (root.printerModel != null) {
                        root.printerModel.setFileColumnVisible(name, visible);
                    }
                }
                onColumnMoveRequested: function (name, steps) {
                    root.moveColumn(name, steps);
                }
                onColumnsResetRequested: {
                    if (root.printerModel != null) {
                        // The order resets to the CODE-DEFAULT
                        // sequence, never an empty list (an empty
                        // list fills from the current order and read
                        // as "no change" — the live report).
                        root.printerModel.setFileColumnOrder(root.defaultColumnOrder);
                        for (var i = 0; i < root.defaultColumnOrder.length; i++) {
                            root.printerModel.setFileColumnVisible(root.defaultColumnOrder[i], true);
                        }
                    }
                }
                onColumnWidthCommitted: function (key, width) {
                    if (root.printerModel != null) {
                        root.printerModel.setFileColumnWidth(key, width);
                    }
                }
                onSortRequested: function (key) {
                    if (root.printerModel != null) {
                        root.printerModel.setFileSort(key);
                    }
                }
                onSelectionToggled: function (row) {
                    if (root.printerModel != null) {
                        root.printerModel.toggleFileSelection(row.relpath);
                    }
                }
                onPageSelectionToggled: {
                    if (root.printerModel != null) {
                        root.printerModel.toggleFilePageSelection();
                    }
                }
                onPrintRequested: function (relpath) {
                    if (root.printerModel != null) {
                        root.printerModel.fileRequestPrint(relpath);
                    }
                }
                onRenameRequested: function (relpath) {
                    if (root.printerModel != null) {
                        root.printerModel.fileRequestRename(relpath);
                    }
                }
                onDownloadRequested: function (relpath) {
                    if (root.printerModel != null) {
                        root.printerModel.fileDownload(relpath);
                    }
                }
                onDeleteRequested: function (relpath) {
                    if (root.printerModel != null) {
                        root.printerModel.fileRequestDeleteFile(relpath);
                    }
                }
                onMetadataScanRequested: function (relpath) {
                    if (root.printerModel != null) {
                        root.printerModel.fileScanMetadata(relpath);
                    }
                }
                onWalkErrorCleared: {
                    if (root.printerModel != null) {
                        root.printerModel.fileClearWalkError();
                    }
                }
                onRefreshRequested: {
                    if (root.printerModel != null) {
                        root.printerModel.refreshFileManager();
                    }
                }
                onFiltersClearRequested: root.clearAllFilters()
                onVisibleThumbnailsRequested: function (relpaths) {
                    if (root.printerModel != null) {
                        root.printerModel.fileRequestVisibleThumbnails(relpaths);
                    }
                }
            }
            // The page strip: the selection summary and the page chrome.
            FileManagerPagination {
                printerModel: root.printerModel
                activeRows: root.activeRows
                narrowMode: root.narrowMode
                onBulkDeleteRequested: {
                    if (root.printerModel != null) {
                        root.printerModel.fileRequestDelete();
                    }
                }
                onSelectionClearRequested: {
                    if (root.printerModel != null) {
                        root.printerModel.clearFileSelection();
                    }
                }
                onPageSizeRequested: function (size) {
                    if (root.printerModel != null) {
                        root.printerModel.setFilePageSize(size);
                    }
                }
                onPageRequested: function (index) {
                    if (root.printerModel != null) {
                        root.printerModel.setFilePage(index);
                    }
                }
            }

            // Bottom band: the disk readout (the containing mount's
            // free space) and the single Close button.
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2

                Cura.SecondaryButton {
                    // The New-folder dialog (the live
                    // request).
                    text: "New folder…"
                    enabled: root.printerModel != null && root.printerModel.monitorConnected
                    onClicked: createFolderDialog.open()
                }
                Cura.SecondaryButton {
                    // Snapshot 3 upload (the ruling): LOCAL
                    // gcode files only — sliced prints already upload
                    // from the Preview view. Bottom-left, beside the
                    // disk readout (the placement).
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
                    objectName: "fileManagerCloseButton"
                    text: "Close"
                    onClicked: root.closeRequested()
                }
            }
        }
    }
}
