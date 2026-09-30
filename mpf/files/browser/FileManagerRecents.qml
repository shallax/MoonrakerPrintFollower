import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The recents strip: the distinct recently printed files from the
// printer's history, horizontally scrolling. History is the source of
// truth, so gone files never render here and there is no dismissal
// glyph. The strip owns its scroller, its thumbnails and the label
// elision; the print gate stays with the shell, which is the only
// holder of the connection and job state the gate reads.
RowLayout {
    id: root

    // [name, relpath, time] items in history order.
    property var recents: []
    // relpath -> {state, url}: the same thumbnail table the grid cells
    // read, so the two surfaces can never disagree about a file.
    property var thumbs: ({})
    // The shell's name formatting (the ubiquitous .gcode suffix is
    // noise): a declared capability rather than a second copy of the
    // rule. The identity default keeps a bare mount rendering names
    // rather than failing to render at all.
    property var displayName: function (name) {
        return name;
    }
    signal printRequested(string relpath)

    function thumbState(relpath) {
        var entry = root.thumbs[relpath];
        return entry !== undefined ? entry.state : "none";
    }

    function thumbUrl(relpath) {
        var entry = root.thumbs[relpath];
        return entry !== undefined ? entry.url : "";
    }

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
        ScrollBar.horizontal: ScrollBar {}
        Row {
            id: recentsRow
            height: parent.height
            spacing: UM.Theme.getSize("default_margin").width / 2
            Repeater {
                model: root.recents
                Rectangle {
                    width: 168 * screenScaleFactor
                    height: 56 * screenScaleFactor
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    radius: UM.Theme.getSize("default_radius").width
                    color: UM.Theme.getColor("main_background")
                    HoverHandler {
                        id: tooltipHover1
                    }
                    UM.ToolTip {
                        visible: tooltipHover1.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
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
                        // The strip's own thumbnail: the same
                        // fetch/cache as the grid cells, with the same
                        // fallbacks.
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
                    MouseArea {
                        // The strip prints too: a click asks the shell,
                        // which opens the same confirmation the rows
                        // do. The request carries the relpath only —
                        // the gate reads connection and job state the
                        // strip does not hold.
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.printRequested(modelData.relpath)
                    }
                    UM.Label {
                        id: recentsName
                        anchors.top: parent.top
                        anchors.topMargin: UM.Theme.getSize("narrow_margin").height
                        anchors.left: parent.left
                        anchors.leftMargin: 36 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").width
                        anchors.right: parent.right
                        anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                        // Inert; the harness's rendered-follows scenarios read
                        // this label's text (shared by rows; the
                        // lookup resolves any instance).
                        objectName: "moonrakerFileRowName"
                        text: root.displayName(modelData.name)
                        elide: Text.ElideMiddle
                        // Elide, never wrap: a wrapped name
                        // pushes the date out of the card (the
                        // live report).
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
