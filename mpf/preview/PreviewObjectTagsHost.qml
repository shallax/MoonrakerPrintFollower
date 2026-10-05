import QtQuick 2.15
import UM 1.5 as UM
import "../resources/theme"

Item {
    id: root
    objectName: "moonrakerPreviewObjectTags"
    anchors.fill: parent
    z: 9500

    property bool dockVisible: false
    property bool projectionAvailable: false
    property bool pickAvailable: false
    property bool tagsEnabled: false
    property bool hoverOnly: false
    property var tagRows: []
    property double hoveredNode: 0
    property double etaNow: Date.now() / 1000

    signal tagsEnabledRequested(bool enabled)
    signal hoverOnlyRequested(bool enabled)
    signal pointerMoved(real x, real y)

    function hoveredDistance(row) {
        if (root.hoveredNode === 0)
            return 10000;
        for (var i = 0; i < root.tagRows.length; ++i) {
            if (root.tagRows[i].nodeId === root.hoveredNode)
                return Math.hypot(row.anchorX - root.tagRows[i].anchorX, row.anchorY - root.tagRows[i].anchorY);
        }
        return 10000;
    }

    function etaText(deadline) {
        if (deadline === null || deadline === undefined)
            return "";
        var seconds = Math.max(0, Math.ceil(deadline - root.etaNow));
        var hours = Math.floor(seconds / 3600);
        var minutes = Math.floor((seconds % 3600) / 60);
        var count = hours > 0 ? hours + "h " + minutes + "m" : minutes + "m " + (seconds % 60) + "s";
        return count + " · " + Qt.formatDateTime(new Date(deadline * 1000), "ddd HH:mm");
    }

    Timer {
        interval: 1000
        running: root.dockVisible && root.tagsEnabled
        repeat: true
        onTriggered: root.etaNow = Date.now() / 1000
    }

    onTagRowsChanged: {
        tagRepeater.model = root.tagRows;
        leaders.requestPaint();
    }
    onHoveredNodeChanged: leaders.requestPaint()
    onHoverOnlyChanged: leaders.requestPaint()

    HoverHandler {
        enabled: root.dockVisible && root.tagsEnabled && root.pickAvailable
        onPointChanged: root.pointerMoved(point.position.x, point.position.y)
        onHoveredChanged: {
            if (!hovered)
                root.pointerMoved(-1, -1);
        }
    }

    Canvas {
        id: leaders
        anchors.fill: parent
        visible: root.dockVisible && root.tagsEnabled
        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            for (var i = 0; i < root.tagRows.length; ++i) {
                var row = root.tagRows[i];
                var selected = root.hoveredNode !== 0 && row.nodeId === root.hoveredNode;
                if (root.hoverOnly && root.pickAvailable && !selected)
                    continue;
                var distance = root.hoveredDistance(row);
                ctx.globalAlpha = selected ? 1.0 : (root.hoveredNode !== 0 && distance < 140 ? 0.25 : 0.8);
                ctx.strokeStyle = UM.Theme.getColor("text").toString();
                ctx.fillStyle = UM.Theme.getColor("text").toString();
                ctx.lineWidth = selected ? 2 : 1;
                var labelCenter = row.labelX + row.labelWidth / 2;
                var labelBottom = row.labelY + row.labelHeight;
                ctx.beginPath();
                ctx.moveTo(row.anchorX, row.anchorY);
                ctx.lineTo(row.anchorX, labelBottom + 10);
                ctx.lineTo(labelCenter, labelBottom);
                ctx.stroke();
                ctx.beginPath();
                ctx.arc(row.anchorX, row.anchorY, selected ? 4 : 3, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.globalAlpha = 1;
        }
    }

    Repeater {
        id: tagRepeater
        objectName: "moonrakerObjectNameRepeater"
        model: []
        delegate: Rectangle {
            id: tag
            objectName: "moonrakerObjectNameBanner"
            required property var modelData
            readonly property bool selected: root.hoveredNode !== 0 && modelData.nodeId === root.hoveredNode
            readonly property real hoverDistance: root.hoveredDistance(modelData)
            x: modelData.labelX
            y: modelData.labelY
            width: modelData.labelWidth
            height: modelData.labelHeight
            visible: root.dockVisible && root.tagsEnabled && (!root.hoverOnly || !root.pickAvailable || selected)
            z: selected ? 100 : 1
            opacity: selected ? 1 : (root.hoveredNode !== 0 && hoverDistance < 140 ? 0.25 : 0.85)
            color: UM.Theme.getColor("main_background")
            border.color: selected ? MoonrakerTheme.plateCurrent : UM.Theme.getColor("lining")
            border.width: selected ? 2 : 1
            radius: UM.Theme.getSize("default_radius").width

            UM.Label {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                height: modelData.deadline != null ? 23 : (modelData.progress === null ? parent.height : parent.height - 7)
                text: modelData.name
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                font: UM.Theme.getFont("default")
            }

            UM.Label {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.topMargin: 22
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                height: 16
                visible: modelData.deadline != null
                text: root.etaText(modelData.deadline)
                color: UM.Theme.getColor("text_medium")
                elide: Text.ElideRight
                font: UM.Theme.getFont("default")
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: 2
                anchors.rightMargin: 2
                anchors.bottomMargin: 2
                height: 3
                visible: modelData.progress !== null
                color: UM.Theme.getColor("lining")
                Rectangle {
                    width: parent.width * Math.max(0, Math.min(1, modelData.progress || 0))
                    height: parent.height
                    color: MoonrakerTheme.plateCurrent
                }
            }
        }
    }

    Rectangle {
        id: dock
        objectName: "moonrakerPreviewObjectTagsDock"
        visible: root.dockVisible
        anchors.top: parent.top
        anchors.topMargin: 100 * screenScaleFactor
        anchors.right: parent.right
        anchors.rightMargin: UM.Theme.getSize("thick_margin").width * 2
        width: 218 * screenScaleFactor
        height: 84 * screenScaleFactor
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width

        Column {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("thin_margin").height

            UM.CheckBox {
                id: enabledBox
                objectName: "moonrakerPreviewObjectTagsEnabled"
                text: "Object name banners"
                checked: root.tagsEnabled
                enabled: root.projectionAvailable
                onToggled: root.tagsEnabledRequested(checked)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: root.projectionAvailable ? "Show object names over Cura Preview." : "This Cura version does not provide camera projection to plugins."
                }
            }
            UM.CheckBox {
                id: hoverBox
                objectName: "moonrakerPreviewObjectTagsHoverOnly"
                text: "Hovered object only"
                checked: root.hoverOnly && root.pickAvailable
                enabled: root.tagsEnabled && root.pickAvailable
                onToggled: root.hoverOnlyRequested(checked)
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: root.pickAvailable ? "Show a banner only for the front-most object under the pointer." : "Cura does not expose object picking for this loaded toolpath."
                }
            }
        }
    }
}
