import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../widgets"

// The file manager's table: the column header, the virtualized rows,
// both scroll strips, the column widths and their resize gestures, and
// the visible-row window that drives the thumbnail fetch. The card
// hands it the model it renders; every state change the table
// originates comes back as a signal, so all model writes and the modal
// flow stay with the card.
Item {
    id: root

    // The published model slice (null in the engine gate and captures).
    property var printerModel: null
    // The page's rows, the directories behind the empty state and the
    // code-default column order the chooser resets to: all three are
    // projections of the model, so the card computes them once.
    property var activeRows: []
    property var activeDirectories: []
    property var defaultColumnOrder: []
    // The card's row helpers: shortened names, the print gate and the
    // gcode test. Handed in so one definition serves the whole card.
    property var displayName: function (name) {
        return name;
    }
    property var printStartAllowed: function () {
        return false;
    }
    property var isGcodeName: function (name) {
        return false;
    }
    // The narrow-window rule belongs to the card, not the table.
    property bool narrowMode: false

    // Model writes the table originates: the card applies them.
    signal columnVisibilityToggled(string name, bool visible)
    signal columnMoveRequested(string name, int steps)
    signal columnsResetRequested
    signal columnWidthCommitted(string key, real width)
    signal sortRequested(string key)
    signal selectionToggled(var row)
    signal pageSelectionToggled
    signal printRequested(string relpath)
    signal renameRequested(string relpath)
    signal downloadRequested(string relpath)
    signal deleteRequested(string relpath)
    signal metadataScanRequested(string relpath)
    signal walkErrorCleared
    signal refreshRequested
    signal filtersClearRequested
    signal visibleThumbnailsRequested(var relpaths)

    // The columns popup's state, exposed to the card's Esc ladder
    // through FUNCTIONS, never an alias: an alias to the popup's id
    // resolves at binding time, before the popup exists (the
    // forward-reference latch), while function bodies resolve at call
    // time.
    function columnsPopupOpen() {
        return columnsPopup.opened;
    }
    function closeColumnsPopup() {
        columnsPopup.close();
    }

    // Sticky identity block (checkbox + thumbnail + name)
    // and the trailing columns share these geometry constants with the
    // header and every row — one column model, no per-row arithmetic.
    readonly property real rowHeight: 48 * screenScaleFactor
    readonly property int rowWindowStart: Math.max(0, Math.floor(gridVertical.contentY / root.rowHeight) - 10)
    readonly property int rowWindowEnd: Math.min(root.activeRows.length, Math.ceil((gridVertical.contentY + gridVertical.height) / root.rowHeight) + 20)
    readonly property var visibleRows: root.activeRows.slice(root.rowWindowStart, root.rowWindowEnd)
    onVisibleRowsChanged: thumbsWindowTimer.restart()
    function requestVisibleThumbnails() {
        // Fires when the scroll SETTLES: fast scrolling fetches once,
        // not once per frame (the live report: the fetch
        // storm made long scrolls crawl).
        if (root.printerModel != null) {
            var relpaths = [];
            for (var i = 0; i < root.visibleRows.length; ++i) {
                relpaths.push(root.visibleRows[i].relpath);
            }
            root.visibleThumbnailsRequested(relpaths);
        }
    }
    Timer {
        id: thumbsWindowTimer
        interval: 300
        onTriggered: root.requestVisibleThumbnails()
    }
    // Column widths are sized to fit their HEADERS on one line —
    // headers never elide or wrap (the live ruling). The
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
    function visibleColumnCount() {
        var visible = 0;
        var order = columnOrderList();
        for (var i = 0; i < order.length; i++) {
            if (columnVisible(order[i])) {
                visible += 1;
            }
        }
        return visible;
    }
    function toggleAllColumns() {
        var order = columnOrderList();
        var target = visibleColumnCount() !== order.length;
        if (printerModel != null) {
            for (var i = 0; i < order.length; i++) {
                if (columnVisible(order[i]) !== target) {
                    root.columnVisibilityToggled(order[i], target);
                }
            }
        }
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
        // on release (the live report).
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
            root.columnWidthCommitted(root.columnDrag.key, root.columnDrag.width);
        }
        root.columnDrag = null;
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
        // live ruling).
        if (root.rowChecked(row)) {
            return "white";
        }
        if (column !== "Status") {
            return UM.Theme.getColor("text");
        }
        return row.statusColour === "text_inactive" ? UM.Theme.getColor("text_inactive") : row.statusColour;
    }
    // Selection lives in the model (Snapshot 1): the payload carries
    // the checked flag; the click toggles through the model. The
    // mock fallback keeps its old behaviour when no printer is
    // attached.
    function rowChecked(row) {
        return row.checked === true;
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
    function rowNeedsMetadata(row) {
        // A host-parsed file always carries these; both null means
        // the host never parsed it (a legacy file dropped into
        // gcodes/). The scan entry only appears where it has work
        // to do — offering it on complete rows looked like a dead
        // option (the live report).
        return row.slicer === null || row.estimated_time === null;
    }
    function pageSelectionState() {
        return root.printerModel != null ? root.printerModel.fileManagerPageSelection : "none";
    }
    function walkErrorText() {
        return root.printerModel != null ? root.printerModel.fileManagerWalkError : "";
    }
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
        // model: the list scrolls as one,
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
                // The page-level three states: the native
                // themed checkbox's tri-state — empty, a
                // filled square for a partial page, a
                // tick for a full one. A click fills a
                // partial page or drops a full one.
                UM.CheckBox {
                    id: pageSelectCheck
                    anchors.left: parent.left
                    anchors.leftMargin: 3 * screenScaleFactor
                    anchors.verticalCenter: parent.verticalCenter
                    checkState: root.pageSelectionState() === "all" ? Qt.Checked : (root.pageSelectionState() === "some" ? Qt.PartiallyChecked : Qt.Unchecked)
                    onClicked: {
                        if (root.printerModel != null) {
                            root.pageSelectionToggled();
                        }
                        pageSelectCheck.checkState = Qt.binding(function () {
                            return root.pageSelectionState() === "all" ? Qt.Checked : (root.pageSelectionState() === "some" ? Qt.PartiallyChecked : Qt.Unchecked);
                        });
                    }
                }
                // A "/" separator keeps the select-all
                // checkbox and the columns trigger from
                // reading as one control (the
                // live ruling).
                UM.Label {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.verticalCenter: parent.verticalCenter
                    text: "/"
                    color: UM.Theme.getColor("text_inactive")
                }
                // The columns menu lives IN this header
                // cell, beside the select-all checkbox
                // (the live ruling): reorder
                // and resize live behind it, not as
                // text in the toolbar. The swishy ⇄
                // glyph in the primary colour stands
                // out where a bare hamburger was too
                // easy to miss (the live
                // report).
                UM.Label {
                    anchors.right: parent.right
                    anchors.rightMargin: 3 * screenScaleFactor
                    anchors.verticalCenter: parent.verticalCenter
                    text: "⇄"
                    font: UM.Theme.getFont("medium_bold")
                    color: UM.Theme.getColor("primary")
                }
                HoverHandler {
                    id: tooltipHover3
                }
                UM.ToolTip {
                    visible: tooltipHover3.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
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
                FileColumnChooser {
                    id: columnsPopup
                    columnOrder: root.columnOrderList()
                    hiddenColumns: root.printerModel != null ? root.printerModel.fileManagerColumnHidden : []
                    onColumnVisibilityRequested: function (name, visible) {
                        root.columnVisibilityToggled(name, visible);
                    }
                    onColumnMoved: function (name, steps) {
                        root.columnMoveRequested(name, steps);
                    }
                    onAllVisibilityRequested: root.toggleAllColumns()
                    onResetRequested: root.columnsResetRequested()
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
                        // the arrow (the live
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
                            root.sortRequested("name");
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
                            // live ruling: no "—"
                            // placeholder). It hugs the
                            // title — at the column edge it
                            // read as belonging to the NEXT
                            // header and overlapped the text
                            // on tight columns (the
                            // live reports).
                            UM.Label {
                                text: root.printerModel != null ? (root.printerModel.fileManagerSortColumn === root.sortKeyFor(modelData[0]) ? (root.printerModel.fileManagerSortAscending ? "↑" : "↓") : "") : ""
                                color: UM.Theme.getColor("primary")
                            }
                        }
                        // A click on the header sorts that
                        // column (clicking the current one
                        // flips its direction — the
                        // ruling).
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (root.printerModel != null) {
                                    root.sortRequested(root.sortKeyFor(modelData[0]));
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
        // The row list is VIRTUALIZED (the
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
                    // invisible (the delta review's
                    // catch, as ruled).
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
                        // The row's selection checkbox:
                        // the native themed control (the
                        // Uranium-controls-first ruling),
                        // centred in the checkbox column.
                        UM.CheckBox {
                            id: rowCheck
                            anchors.centerIn: parent
                            checked: root.rowChecked(modelData)
                            onClicked: {
                                root.selectionToggled(modelData);
                                rowCheck.checked = Qt.binding(function () {
                                    return root.rowChecked(modelData);
                                });
                            }
                        }
                        // Wired mock selection: a
                        // click toggles the row's
                        // selected state (a request —
                        // this is the ONLY wired
                        // behaviour in the mock).
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.selectionToggled(modelData)
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
                                source: Qt.resolvedUrl("../../resources/svg/Hourglass.svg")
                                color: UM.Theme.getColor("text_inactive")
                                // The spin: a static glyph reads as dead.
                                RotationAnimation on rotation {
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
                        HoverHandler {
                            id: tooltipHover4
                        }
                        UM.ToolTip {
                            visible: tooltipHover4.hovered
                            targetPoint: Qt.point(parent.width / 2, 0)
                            x: 0
                            y: parent.height + UM.Theme.getSize("default_margin").height
                            width: UM.Theme.getSize("tooltip").width
                            text: modelData.name
                        }
                        Column {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2 * screenScaleFactor
                            UM.Label {
                                width: parent.width - 6 * screenScaleFactor
                                // Inert; the harness's rendered-follows scenarios read
                                // this label's text (shared by rows; the
                                // lookup resolves any instance).
                                objectName: "moonrakerFileRowName"
                                text: root.displayName(modelData.name)
                                wrapMode: Text.NoWrap
                                elide: Text.ElideMiddle
                                font: UM.Theme.getFont("default")
                                color: root.rowChecked(modelData) ? "white" : UM.Theme.getColor("text")
                            }
                            // The folder breadcrumb (the
                            // live request):
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
                                // live report:
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
                                // reports).
                                enabled: root.printerModel != null && root.isGcodeName(modelData.relpath) && root.rowNeedsMetadata(modelData)
                                text: "Scan metadata"
                                onTriggered: {
                                    if (root.printerModel != null) {
                                        root.metadataScanRequested(modelData.relpath);
                                    }
                                }
                            }
                            MenuItem {
                                // Snapshot 2: Print opens the
                                // confirmation; every row's entry
                                // stands down while one is up and
                                // while a job is active or the
                                // printer is disconnected (the
                                // rulings).
                                enabled: root.printerModel != null && root.printerModel.filePrintConfirm === "" && root.printStartAllowed() && root.isGcodeName(modelData.relpath)
                                text: "Print"
                                onTriggered: {
                                    if (root.printerModel != null) {
                                        root.printRequested(modelData.relpath);
                                    }
                                }
                            }
                            MenuItem {
                                // Snapshot 3: the
                                // printing file never
                                // offers the mutations
                                // (the gate).
                                enabled: root.printerModel != null && root.printerModel.monitorConnected && !modelData.printing
                                text: "Rename"
                                onTriggered: {
                                    if (root.printerModel != null) {
                                        root.renameRequested(modelData.relpath);
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
                                        root.downloadRequested(modelData.relpath);
                                    }
                                }
                            }
                            MenuItem {
                                enabled: root.printerModel != null && root.printerModel.monitorConnected && !modelData.printing
                                text: "Delete"
                                onTriggered: {
                                    if (root.printerModel != null) {
                                        root.deleteRequested(modelData.relpath);
                                    }
                                }
                            }
                        }
                    }
                }
                // Row-level pointer surface (the
                // live requests):
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
                            root.printRequested(modelData.relpath);
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
                // names (the live ruling).
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
                        // trailing half too (the
                        // live request).
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onDoubleClicked: {
                            if (root.printerModel != null && root.printStartAllowed() && root.isGcodeName(modelData.relpath)) {
                                root.printRequested(modelData.relpath);
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
                                    // neighbour (the
                                    // live report).
                                    anchors.leftMargin: 5 * screenScaleFactor
                                    anchors.rightMargin: 5 * screenScaleFactor
                                    spacing: 4 * screenScaleFactor
                                    // On a ticked row the
                                    // status word goes white
                                    // and its colour survives
                                    // as a dot beside it (the
                                    // ruling — the
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
        ScrollBar.vertical: ScrollBar {}
    }

    Flickable {
        id: gridHorizontal
        anchors.left: gridVertical.left
        anchors.right: gridVertical.right
        // Anchored to the card's bottom, NOT to the
        // grid's: a grid↔strip anchor pair is a binding
        // loop that collapsed the ListView to zero
        // height — the live report: no rows
        // at all.
        anchors.bottom: parent.bottom
        height: 12 * screenScaleFactor
        contentWidth: root.stickyWidth + root.trailingWidth
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.HorizontalFlick
        ScrollBar.horizontal: ScrollBar {}
    }

    // Scroll affordances, the design: small
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
        // sort arrows (the live ruling).
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
        // The dismiss affordance (the live
        // ruling): the banner overlays the first row, so
        // it must be closable, not just transient.
        UM.Label {
            anchors.right: parent.right
            anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
            anchors.verticalCenter: parent.verticalCenter
            text: "✕"
            font: UM.Theme.getFont("default")
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    if (root.printerModel != null) {
                        root.walkErrorCleared();
                    }
                }
            }
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
                            root.refreshRequested();
                        } else {
                            root.filtersClearRequested();
                        }
                    }
                }
            }
        }
    }
}
