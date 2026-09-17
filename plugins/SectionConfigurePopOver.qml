import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The pane's configure pop-over (4.4.0): the section rows in their
// configured order, the shared row per entry, and the drag proxy for
// the handle gesture. View-only: the host owns the open flag and the
// rows; every change commits once through layoutCommitted.
MonitorPopOver {
    id: root

    property string paneId: ""
    property var rows: []   // [{id, title}] in the current order
    property var hidden: [] // ids currently hidden

    signal layoutCommitted(var order, var hidden)

    property int dragIndex: -1
    property int dragTarget: -1
    property string dragTitle: ""
    property real dragOffset: 0

    objectName: "sectionConfigurePopOver"
    height: 2 * UM.Theme.getSize("default_margin").height + 32 * screenScaleFactor + rows.length * 32 * screenScaleFactor + UM.Theme.getSize("default_margin").height

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
    function dragDelta(id, title, delta) {
        if (dragIndex === -1) {
            dragIndex = orderIds().indexOf(id);
            dragTarget = dragIndex;
            dragTitle = title;
            dragOffset = 0;
        }
        dragOffset += delta;
        // The live snap: the target slot tracks the drag position,
        // and the drop slot renders where a release would land.
        dragTarget = Math.max(0, Math.min(rows.length - 1, Math.round((dragIndex * 32 * screenScaleFactor + dragOffset) / (32 * screenScaleFactor))));
        proxy.y = dragIndex * 32 * screenScaleFactor + dragOffset;
        dropSlot.y = dragTarget * 32 * screenScaleFactor;
    }
    function commitDrag() {
        if (dragIndex === -1) {
            return;
        }
        var steps = dragTarget - dragIndex;
        var id = rows[dragIndex].id;
        dragIndex = -1;
        dragTarget = -1;
        dragOffset = 0;
        if (steps !== 0) {
            commitMove(id, steps);
        }
    }

    Column {
        id: rowsColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: UM.Theme.getSize("default_margin").height
        spacing: 0

        // The all/none three-state selector (the author's live
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
            model: root.rows
            delegate: SectionConfigureRow {
                rowId: modelData.id
                rowTitle: modelData.title
                rowVisible: root.hidden.indexOf(modelData.id) === -1
                onToggleRequested: root.commitToggle(modelData.id)
                onMoveRequested: function (steps) {
                    root.commitMove(modelData.id, steps);
                }
                onDragMoved: function (delta) {
                    root.dragDelta(modelData.id, modelData.title, delta);
                }
                onDragReleased: root.commitDrag()
            }
        }

        // The drop slot: a slightly darker blank where a release
        // would land (the author's live ruling) — the rows never
        // shift mid-gesture.
        Rectangle {
            id: dropSlot
            opacity: root.dragIndex !== -1 ? 1 : 0
            height: root.dragIndex !== -1 ? 32 * screenScaleFactor : 0
            width: parent.width
            radius: 2
            color: UM.Theme.getColor("setting_category")
            z: 5
        }
        // The drag proxy: the handle gesture moves this visual copy;
        // the list rebuilds once, on release, so the dragged row's
        // delegate never dies mid-gesture (the column popup's
        // documented Repeater trap). Hidden via opacity, never a
        // literal visible: false (the no-reflow scan's ban).
        Rectangle {
            id: proxy
            opacity: root.dragIndex !== -1 ? 1 : 0
            width: parent.width
            height: root.dragIndex !== -1 ? 32 * screenScaleFactor : 0
            radius: 2
            color: UM.Theme.getColor("setting_category_hover")
            z: 10
            UM.Label {
                anchors.fill: parent
                anchors.leftMargin: 12 * screenScaleFactor
                text: root.dragTitle
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                font: UM.Theme.getFont("default")
            }
        }
    }
}
