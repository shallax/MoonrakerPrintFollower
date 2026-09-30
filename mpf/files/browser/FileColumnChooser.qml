import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import "../../widgets"

// The Columns menu (Snapshot 3): the header cell opens it, its rows
// toggle a column's visibility and reorder the trailing set by drag or
// arrow. The popup owns the whole gesture — the proxy, the drop
// target and the one commit on release — and reports order and
// visibility as signals; the model writes stay with the shell.
Popup {
    id: root
    // The harness locates the menu by this name, not by its position.
    objectName: "columnsPopup"

    // The columns in the user's order, and the names switched off.
    // Both arrive from the shell, so the chooser never reads the
    // model or a parent chain.
    property var columnOrder: []
    property var hiddenColumns: []
    // The opener's press state: the header cell records whether the
    // popup was already open when the press landed, so a second press
    // closes instead of re-opening.
    property bool wasOpenAtPress: false
    // The drag state and its proxy commit (the shared row emits
    // deltas; this host owns the visual proxy and the one commit).
    property int dragIndex: -1
    property int dragTarget: -1
    property string dragTitle: ""

    signal columnVisibilityRequested(string name, bool visible)
    signal columnMoved(string name, int steps)
    signal allVisibilityRequested
    signal resetRequested

    function columnVisible(name) {
        return root.hiddenColumns.indexOf(name) < 0;
    }
    function visibleColumnCount() {
        var visible = 0;
        for (var i = 0; i < root.columnOrder.length; i++) {
            if (root.columnVisible(root.columnOrder[i])) {
                visible += 1;
            }
        }
        return visible;
    }
    function displayColumns() {
        var order = root.columnOrder;
        if (root.dragIndex === -1) {
            return order;
        }
        var list = [];
        // The same leading-by-one rule as the pane popup: the dragged
        // row's removal shifts every later entry up one, so the slot
        // only renders at the drop point when pushed one past the
        // target.
        var slotAt = root.dragTarget > root.dragIndex ? root.dragTarget + 1 : root.dragTarget;
        for (var i = 0; i < order.length; i++) {
            if (i === root.dragIndex) {
                if (i === slotAt) {
                    list.push({
                        "slot": true
                    });
                }
                continue;
            }
            if (i === slotAt) {
                list.push({
                    "slot": true
                });
            }
            list.push(order[i]);
        }
        if (root.dragTarget === order.length - 1) {
            list.push({
                "slot": true
            });
        }
        return list;
    }
    function startColumnDrag(id) {
        root.dragIndex = root.columnOrder.indexOf(id);
        root.dragTarget = root.dragIndex;
        root.dragTitle = id;
        columnDragProxy.y = root.dragIndex * 32 * screenScaleFactor;
    }
    function columnDragDelta(mouseY) {
        if (root.dragIndex === -1) {
            return;
        }
        root.dragTarget = Math.max(0, Math.min(root.columnOrder.length - 1, Math.round((mouseY - 16 * screenScaleFactor) / (32 * screenScaleFactor))));
        columnDragProxy.y = mouseY - 16 * screenScaleFactor;
    }
    function columnDragCommit() {
        if (root.dragIndex === -1) {
            return;
        }
        var steps = root.dragTarget - root.dragIndex;
        var id = root.columnOrder[root.dragIndex];
        root.dragIndex = -1;
        root.dragTarget = -1;
        if (steps !== 0) {
            root.columnMoved(id, steps);
        }
    }

    x: 0
    // Directly under the cell that owns the menu. The null guard is
    // the bare-mount fallback: the engine gate creates every document
    // on its own, with no parent to measure.
    y: parent !== null ? parent.height : 0
    padding: 0
    // EXPLICIT sizes: the shared row's root carries no implicit width
    // and the rows carry explicit heights (implicit zero), so the popup
    // sized to a sliver and a 0-tall background — the rows rendered
    // OUTSIDE it and every click landed on the card (the live report:
    // no background, any interaction dismissed it).
    width: (240 + 2 * UM.Theme.getSize("narrow_margin").width) * screenScaleFactor
    height: 32 * screenScaleFactor + root.columnOrder.length * 32 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").height + 24 * screenScaleFactor + UM.Theme.getSize("narrow_margin").height
    // Outside PRESSES dismiss. Esc is NOT claimed here: the popup's own
    // escape handling consumed the key without closing and starved the
    // window's ladder (the probe's finding) — the dashboard's ladder
    // owns Esc and closes this popup first, per the one-claimant
    // doctrine. The outside-press form is safe for the drag: the
    // gesture's own press was inside, so only a fresh press outside
    // closes — an outside RELEASE dismissal was what killed a
    // mid-gesture drag (the security round's finding).
    closePolicy: Popup.CloseOnPressOutside
    // The themed surface, like every other popup in the card (the live
    // report: the default background was a black slab).
    background: Rectangle {
        objectName: "columnsPopupBackground"
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        width: parent.width
        topPadding: UM.Theme.getSize("narrow_margin").height
        bottomPadding: UM.Theme.getSize("narrow_margin").height
        // The same inset the pane popups carry (the live report: the
        // content hugged the left edge).
        leftPadding: UM.Theme.getSize("narrow_margin").width
        rightPadding: UM.Theme.getSize("narrow_margin").width
        // The selector row.
        Row {
            width: parent.width - parent.leftPadding - parent.rightPadding
            spacing: UM.Theme.getSize("narrow_margin").width
            VisibilitySelector {
                id: columnsSelector
                height: 32 * screenScaleFactor
                total: root.columnOrder.length
                visibleCount: root.visibleColumnCount()
                onToggled: root.allVisibilityRequested()
            }
        }
        // A plain host item holds the rows and the drag chrome: an
        // anchored child inside a Column is undefined behaviour (the
        // card's own note), and the overlay was exactly that — the
        // container's engine then refused to lay the column out at
        // all, stacking every row at one y (the probe's finding).
        Item {
            id: columnsHost
            width: parent.width - parent.leftPadding - parent.rightPadding
            height: root.columnOrder.length * 32 * screenScaleFactor
            Column {
                anchors.fill: parent
                Repeater {
                    model: root.displayColumns()
                    delegate: Item {
                        width: parent.width
                        height: 32 * screenScaleFactor
                        // The insertion slot renders as a darker blank
                        // (colour-driven, never a visibility binding);
                        // the row hides beneath it via opacity and
                        // stops answering clicks.
                        Rectangle {
                            anchors.fill: parent
                            radius: 2 * screenScaleFactor
                            color: modelData.slot === true ? UM.Theme.getColor("setting_category") : "transparent"
                        }
                        SectionConfigureRow {
                            anchors.fill: parent
                            opacity: modelData.slot === true ? 0 : 1
                            interactive: modelData.slot !== true
                            rowId: modelData
                            rowTitle: modelData
                            rowVisible: root.columnVisible(modelData)
                            onToggleRequested: root.columnVisibilityRequested(modelData, !root.columnVisible(modelData))
                            onMoveRequested: function (steps) {
                                root.columnMoved(modelData, steps);
                            }
                            onDragRequested: root.startColumnDrag(modelData)
                        }
                    }
                }
            }

            // The drag proxy: the floating copy of the dragged card.
            // Hidden via opacity — a visibility binding would trip the
            // no-reflow scan.
            Rectangle {
                id: columnDragProxy
                opacity: root.dragIndex !== -1 ? 1 : 0
                width: parent.width
                height: root.dragIndex !== -1 ? 32 * screenScaleFactor : 0
                radius: 2
                color: UM.Theme.getColor("setting_category_hover")
                z: 20
                UM.Label {
                    anchors.fill: parent
                    anchors.leftMargin: 12 * screenScaleFactor
                    text: root.dragTitle
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                    font: UM.Theme.getFont("default")
                }
            }

            // The gesture overlay: ALWAYS enabled, the owner of every
            // press over the rows. A press on a handle band starts the
            // drag and grabs the whole gesture — the release always
            // lands here, never on a delegate the rebuild has removed
            // (the live report: the release needed a follow-up click).
            // Anywhere else the press is refused and falls through to
            // the row's own controls.
            MouseArea {
                id: columnDragOverlay
                anchors.fill: parent
                z: 30
                onPressed: function (mouse) {
                    if (mouse.x > 10 * screenScaleFactor) {
                        mouse.accepted = false;
                        return;
                    }
                    var band = Math.floor(mouse.y / (32 * screenScaleFactor));
                    if (band < 0 || band >= root.columnOrder.length) {
                        mouse.accepted = false;
                        return;
                    }
                    // Unticked columns drag too — the visibility never
                    // gates the handle (the live report).
                    root.startColumnDrag(root.columnOrder[band]);
                }
                onPositionChanged: {
                    if (root.dragIndex !== -1) {
                        root.columnDragDelta(mouseY);
                    }
                }
                onReleased: {
                    root.columnDragCommit();
                }
                onCanceled: {
                    root.columnDragCommit();
                }
            }
        }

        // Reset to defaults (the live request): the blue text label at
        // the popup's bottom restores the default order and shows every
        // column again.
        UM.Label {
            objectName: "resetToDefaultsLabel"
            width: parent.width - parent.leftPadding - parent.rightPadding
            height: 24 * screenScaleFactor
            text: "Reset to defaults"
            color: UM.Theme.getColor("primary")
            font: UM.Theme.getFont("default")
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.resetRequested()
            }
        }
    }
}
