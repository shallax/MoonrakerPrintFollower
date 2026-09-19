import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The pane's configure pop-over (4.4.0): the section rows in their
// configured order, the shared row per entry, the drag proxy and the
// live insertion slot. View-only: the host owns the open flag and
// the rows; every change commits once through layoutCommitted.
// The drag reflows LIVE (a ruling): the dragged row
// leaves the list the moment the gesture starts, the rows after the
// drop point step down by one around a blank slot, and the floating
// proxy carries the dragged card. The gesture itself lives in a
// full-content overlay, so the dragged delegate can die without
// dropping the grab.
MonitorPopOver {
    id: root

    property string paneId: ""
    property var rows: []   // [{id, title}] in the current order
    property var hidden: [] // ids currently hidden

    signal layoutCommitted(var order, var hidden)

    property int dragIndex: -1
    property int dragTarget: -1
    property string dragTitle: ""
    readonly property int rowHeight: 32 * screenScaleFactor

    objectName: "sectionConfigurePopOver"
    height: 2 * UM.Theme.getSize("default_margin").height + 36 * screenScaleFactor + 2 * UM.Theme.getSize("thin_margin").height + 32 * screenScaleFactor + rows.length * 32 * screenScaleFactor + 24 * screenScaleFactor + UM.Theme.getSize("thin_margin").height

    function orderIds() {
        return rows.map(function (row) {
            return row.id;
        });
    }
    function commitToggle(id) {
        var next = hidden.slice();
        var at = next.indexOf(id);
        if (at === -1) {
            next.push(id);
        } else {
            next.splice(at, 1);
        }
        layoutCommitted(orderIds(), next);
    }
    function commitMove(id, steps) {
        var order = orderIds();
        var at = order.indexOf(id);
        var to = Math.max(0, Math.min(order.length - 1, at + steps));
        if (to === at) {
            return;
        }
        order.splice(at, 1);
        order.splice(to, 0, id);
        layoutCommitted(order, hidden);
    }
    function toggleAll() {
        if (hidden.length === 0) {
            layoutCommitted(orderIds(), rows.map(function (row) {
                return row.id;
            }));
        } else {
            layoutCommitted(orderIds(), []);
        }
    }
    function displayRows() {
        // The live display: the dragged row leaves the list; a slot
        // placeholder marks where a release would land, so every row
        // after the drop point steps down by one. The slot replaces
        // the dragged row's OWN spot while the target sits there —
        // its space must never collapse (the live report: the rows
        // shuffled up the moment the drag started).
        if (dragIndex === -1) {
            return rows;
        }
        var list = [];
        // Downward, the slot leads by one list index: the dragged
        // row's removal shifts every later entry up one, so the slot
        // only renders at the drop point when it is pushed one past
        // the target (the live report: the shuffle needed ~2.5 cards
        // of travel before the slot showed).
        var slotAt = dragTarget > dragIndex ? dragTarget + 1 : dragTarget;
        for (var i = 0; i < rows.length; i++) {
            if (i === dragIndex) {
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
            list.push(rows[i]);
        }
        if (dragTarget === rows.length - 1) {
            list.push({
                "slot": true
            });
        }
        return list;
    }
    function startDrag(id, title) {
        dragIndex = orderIds().indexOf(id);
        dragTarget = dragIndex;
        dragTitle = title;
        // The rows start BELOW the selector: the proxy overlays the
        // rows, so its y carries the same offset (the live report:
        // the proxy floated a row high).
        proxy.y = rowHeight + dragIndex * rowHeight;
    }
    function dragDelta(mouseY) {
        if (dragIndex === -1) {
            return;
        }
        // The overlay lives INSIDE rowsHost (the layout-warning fix):
        // the mouse y is rowsHost-relative now, so the target index
        // drops only the selector band (the live report: drops landed
        // one row low).
        dragTarget = Math.max(0, Math.min(rows.length - 1, Math.round((mouseY - rowHeight - rowHeight / 2) / rowHeight)));
        proxy.y = mouseY - rowHeight / 2;
    }
    function commitDrag() {
        if (dragIndex === -1) {
            return;
        }
        var steps = dragTarget - dragIndex;
        var id = rows[dragIndex].id;
        dragIndex = -1;
        dragTarget = -1;
        if (steps !== 0) {
            commitMove(id, steps);
        }
    }

    // A plain host item is the MANAGED layout child: a positioner
    // placed directly inside a layout has its own layout disabled on
    // some engines, so the rows all collapse onto one point (the
    // harness probe's finding: 13 rows stacked at one y). Inside the
    // plain host the Column lays out normally on both engines. The
    // rows carry EXPLICIT heights, so the host's preferred height is
    // declared too.
    Item {
        id: rowsHost
        Layout.fillWidth: true
        Layout.preferredHeight: 32 * screenScaleFactor + root.rows.length * 32 * screenScaleFactor

        Column {
            id: rowsColumn
            anchors.fill: parent
            spacing: 0

            // The all/none three-state selector (a live
            // ruling) — the shared component, counts owned here.
            VisibilitySelector {
                width: parent.width
                height: 32 * screenScaleFactor
                total: root.rows.length
                visibleCount: root.rows.length - root.hidden.length
                onToggled: root.toggleAll()
            }

            Repeater {
                id: rowRepeater
                model: root.displayRows()
                delegate: Item {
                    width: parent.width
                    height: 32 * screenScaleFactor
                    // The insertion slot renders as a darker blank
                    // (colour-driven, never a visibility binding); the
                    // row hides beneath it via opacity and stops
                    // answering clicks.
                    Rectangle {
                        anchors.fill: parent
                        radius: 2 * screenScaleFactor
                        color: modelData.slot === true ? UM.Theme.getColor("setting_category") : "transparent"
                    }
                    SectionConfigureRow {
                        anchors.fill: parent
                        opacity: modelData.slot === true ? 0 : 1
                        interactive: modelData.slot !== true
                        rowId: modelData.id
                        rowTitle: modelData.title
                        rowVisible: root.hidden.indexOf(modelData.id) === -1
                        onToggleRequested: root.commitToggle(modelData.id)
                        onMoveRequested: function (steps) {
                            root.commitMove(modelData.id, steps);
                        }
                        onDragRequested: root.startDrag(modelData.id, modelData.title)
                        onDragReleased: root.commitDrag()
                    }
                }
            }
        }

        // The drag proxy: the floating copy of the dragged card.
        // Hidden via opacity — a visibility binding would trip the
        // no-reflow scan.
        Rectangle {
            id: proxy
            opacity: root.dragIndex !== -1 ? 1 : 0
            height: root.dragIndex !== -1 ? 32 * screenScaleFactor : 0
            width: parent.width
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

        // The gesture overlay: ALWAYS enabled, the topmost surface in
        // the rows host. A press on a row's handle band starts the
        // drag and grabs the whole gesture — press, moves and
        // release all land here, so the commit never depends on the
        // dragged delegate surviving its own removal from the list
        // (the live report: the release alone never committed, and a
        // follow-up click did). Anywhere else the press is refused
        // and falls through to the row's own controls. The plain host
        // keeps the anchors legal — an anchored child of the card's
        // layout is undefined behaviour and the engine warns.
        MouseArea {
            id: dragOverlay
            anchors.fill: parent
            z: 30
            onPressed: function (mouse) {
                if (mouse.x > 10 * screenScaleFactor) {
                    mouse.accepted = false;
                    return;
                }
                var band = Math.floor((mouse.y - rowHeight) / rowHeight);
                if (band < 0 || band >= root.rows.length) {
                    mouse.accepted = false;
                    return;
                }
                // Hidden sections drag too — the tick state never
                // gates the handle (the live report: the drag broke
                // on an unticked row).
                root.startDrag(root.rows[band].id, root.rows[band].title);
            }
            onPositionChanged: {
                if (root.dragIndex !== -1) {
                    root.dragDelta(mouseY);
                }
            }
            onReleased: {
                root.commitDrag();
            }
            onCanceled: {
                root.commitDrag();
            }
        }
    }

    // Reset to defaults (the live request): the blue text label at
    // the card's bottom commits the EMPTY layout — the normaliser
    // treats the pane's default order with nothing hidden as
    // all-default and drops the entry, so every section returns.
    UM.Label {
        objectName: "resetToDefaultsLabel"
        Layout.fillWidth: true
        Layout.preferredHeight: 24 * screenScaleFactor
        text: "Reset to defaults"
        color: UM.Theme.getColor("primary")
        font: UM.Theme.getFont("default")
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.layoutCommitted([], [])
        }
    }
}
