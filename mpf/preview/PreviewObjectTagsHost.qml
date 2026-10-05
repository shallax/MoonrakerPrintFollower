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
    property string pickMode: "none"
    property bool tagsEnabled: false
    property bool hoverOnly: false
    property bool controlsExpanded: false
    property real previewCardLeft: -1
    property real previewCardBottom: -1
    property var tagRows: []
    property double hoveredNode: 0
    property var hoveredNames: []
    readonly property bool hasHover: root.hoveredNode !== 0 || root.hoveredNames.length > 0
    property double etaNow: Date.now() / 1000
    property double marqueeClock: 0
    readonly property real dockVerticalPadding: UM.Theme.getSize("thick_margin").height
    readonly property real dockContentGap: UM.Theme.getSize("default_margin").height

    signal tagsEnabledRequested(bool enabled)
    signal hoverOnlyRequested(bool enabled)
    signal pointerMoved(real x, real y)

    function isSelected(row) {
        return (root.hoveredNode !== 0 && row.nodeId === root.hoveredNode) || root.hoveredNames.indexOf(row.name) !== -1;
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

    function marqueeOffset(textWidth, viewportWidth) {
        var travel = Math.max(0, textWidth - viewportWidth);
        if (travel <= 1)
            return 0;
        var duration = Math.max(1800, Math.round(travel * 22));
        var pause = 550;
        var phase = root.marqueeClock % (2 * (duration + pause));
        if (phase < pause)
            return 0;
        phase -= pause;
        if (phase < duration)
            return -travel * cubicEase(phase / duration);
        phase -= duration;
        if (phase < pause)
            return -travel;
        return -travel * (1 - cubicEase((phase - pause) / duration));
    }

    function cubicEase(t) {
        return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    }

    Timer {
        interval: 1000
        running: root.dockVisible && root.tagsEnabled
        repeat: true
        onTriggered: root.etaNow = Date.now() / 1000
    }

    Timer {
        interval: 33
        running: root.dockVisible && root.tagsEnabled && root.tagRows.length > 0
        repeat: true
        onTriggered: root.marqueeClock += interval
    }

    onTagRowsChanged: {
        tagRepeater.model = root.tagRows;
        leaders.requestPaint();
    }
    onHoveredNodeChanged: leaders.requestPaint()
    onHoveredNamesChanged: leaders.requestPaint()
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
                var selected = root.isSelected(row);
                if (root.hoverOnly && root.pickAvailable && !selected)
                    continue;
                ctx.globalAlpha = selected ? 1.0 : (root.hasHover ? 0.25 : 0.8);
                ctx.strokeStyle = UM.Theme.getColor("text").toString();
                ctx.fillStyle = UM.Theme.getColor("text").toString();
                ctx.lineWidth = selected ? 2 : 1;
                var labelCenter = row.labelX + row.labelWidth / 2;
                var labelBottom = row.labelY + row.labelHeight;
                ctx.beginPath();
                ctx.moveTo(row.anchorX, row.anchorY);
                if (Math.abs(labelCenter - row.anchorX) > 0.5)
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
            readonly property bool selected: root.isSelected(modelData)
            x: modelData.labelX
            y: modelData.labelY
            width: modelData.labelWidth
            height: modelData.labelHeight
            visible: root.dockVisible && root.tagsEnabled && (!root.hoverOnly || !root.pickAvailable || selected)
            z: selected ? 100 : 1
            opacity: selected ? 1 : (root.hasHover ? 0.25 : 0.85)
            color: UM.Theme.getColor("main_background")
            border.color: selected ? MoonrakerTheme.plateCurrent : UM.Theme.getColor("lining")
            border.width: selected ? 2 : 1
            radius: UM.Theme.getSize("default_radius").width

            Item {
                id: nameViewport
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                height: modelData.deadline != null ? 23 : (modelData.progress === null ? parent.height : parent.height - 7)
                clip: true

                UM.Label {
                    id: nameText
                    x: root.marqueeOffset(implicitWidth, nameViewport.width)
                    width: implicitWidth
                    height: parent.height
                    text: modelData.name
                    color: UM.Theme.getColor("text")
                    elide: Text.ElideNone
                    verticalAlignment: Text.AlignVCenter
                    font: UM.Theme.getFont("default")
                }
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
        x: root.previewCardLeft > 0 ? Math.max(8 * screenScaleFactor, root.previewCardLeft - width - 12 * screenScaleFactor) : Math.max(8 * screenScaleFactor, root.width - width - 16 * screenScaleFactor)
        y: (root.previewCardBottom > 0 ? root.previewCardBottom : root.height - 24 * screenScaleFactor) - height
        width: 230 * screenScaleFactor
        height: root.controlsExpanded ? header.height + root.dockContentGap + controlsColumn.implicitHeight + root.dockVerticalPadding : header.height
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width

        Item {
            id: header
            objectName: "moonrakerPreviewObjectTagsHandle"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: root.controlsExpanded ? dockTitle.implicitHeight + 2 * root.dockVerticalPadding : 34 * screenScaleFactor

            UM.Label {
                id: dockTitle
                objectName: "moonrakerPreviewObjectTagsTitle"
                anchors.left: parent.left
                anchors.leftMargin: UM.Theme.getSize("default_margin").width
                anchors.verticalCenter: parent.verticalCenter
                text: "Object banners"
                font: UM.Theme.getFont("medium")
                color: UM.Theme.getColor("text")
            }
            UM.Label {
                anchors.right: expandIcon.left
                anchors.rightMargin: UM.Theme.getSize("thin_margin").width
                anchors.verticalCenter: parent.verticalCenter
                text: root.tagsEnabled ? "On" : "Off"
                color: UM.Theme.getColor("text_medium")
            }
            UM.ColorImage {
                id: expandIcon
                anchors.right: parent.right
                anchors.rightMargin: UM.Theme.getSize("default_margin").width
                anchors.verticalCenter: parent.verticalCenter
                width: UM.Theme.getSize("standard_arrow").width
                height: UM.Theme.getSize("standard_arrow").height
                source: UM.Theme.getIcon(root.controlsExpanded ? "ChevronSingleDown" : "ChevronSingleUp")
                color: UM.Theme.getColor("text")
            }
            MouseArea {
                anchors.fill: parent
                onClicked: root.controlsExpanded = !root.controlsExpanded
            }
        }

        Column {
            id: controlsColumn
            objectName: "moonrakerPreviewObjectTagsControls"
            visible: root.controlsExpanded
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: header.bottom
            anchors.leftMargin: UM.Theme.getSize("default_margin").width
            anchors.rightMargin: UM.Theme.getSize("default_margin").width
            anchors.topMargin: root.dockContentGap
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
                    objectName: "moonrakerPreviewObjectTagsHoverOnlyTooltip"
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: !root.projectionAvailable ? "This Cura version does not provide camera projection to plugins." : !root.tagsEnabled ? "Enable Object name banners to use this option." : !root.pickAvailable ? "No object footprints are available for this toolpath." : root.pickMode === "exact" ? "Show a banner only for the front-most object under the pointer." : root.pickMode === "footprint" ? "Show banners for every object footprint under the pointer. G-code picking is approximate." : "No object footprints are available for this toolpath."
                }
            }
        }
    }
}
