import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import ".."
import "../Controls"
import "../Temperature"
import "../../Resources/Theme"

// The printer-status pane: the title row with the connection dot and
// the frontend, configure and collapse triggers; the column of status
// sections it folds away; and the collapsed strip's readout. The host
// owns the narrow-window fold, the open pop-over and the stored section
// order — the fold and the readouts arrive as inputs, the rest as
// intents.
Cura.RoundedRectangle {
    id: root

    objectName: "statusPanel"
    property var printerModel: null
    // The frozen narrow-window fold, bound at the call site.
    property bool statusCollapsed: false
    property bool statusExpandLocked: false
    // The collapsed strip's availability gates, derived by the host
    // from the model's own readouts.
    property bool etaAvailable: false
    property bool finishAvailable: false
    property bool layerCountAvailable: false
    property bool flowAvailable: false

    // The two items the host's own passes work on: the sections column
    // (the stored order) and the strip's readout row (the fit). Handing
    // them out keeps those passes where they were.
    property alias content: statusContent
    property alias readoutRow: statusCollapsedReadout.readoutRow
    // Where the configure card hangs from, in this pane's frame: this
    // pane's trigger closes the header row.
    readonly property point headerAnchor: Qt.point(statusHeader.x + statusHeader.width, statusHeader.y + statusHeader.height)
    // The connection dot's colour rides the transport state, and the
    // dot is drawn in both the open header and the collapsed strip.
    property string connectionDotColour: root.printerModel != null && root.printerModel.monitorConnected ? MoonrakerTheme.successGreen : MoonrakerTheme.errorRed

    signal collapseToggled(bool collapsing)
    signal configureRequested
    signal contentReady

    // The collapsed readout may outrun a short pane: the
    // PANE clips, so no child can ever spill past its
    // bounds (the live report).
    clip: true
    // Collapsed, the pane shrinks to the toggle button and its
    // margins; the vertical title below explains the strip.
    Layout.preferredWidth: (root.statusCollapsed ? statusCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 410 * screenScaleFactor)
    Layout.minimumWidth: (root.statusCollapsed ? statusCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 260 * screenScaleFactor)
    // Shrink-only: max == preferred keeps the wide layout
    // unchanged, but narrow stages may compress the pane.
    Layout.maximumWidth: (root.statusCollapsed ? statusCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 410 * screenScaleFactor)
    Layout.fillWidth: true
    Layout.fillHeight: true
    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    color: UM.Theme.getColor("main_background")
    radius: UM.Theme.getSize("default_radius").width

    MouseArea {
        visible: root.statusCollapsed
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            // Auto-collapsed-by-width is not a clickable
            // expand: only a wider window restores the
            // pane (the information pane's strip and the
            // console's strip carry the same guard).
            if (root.statusExpandLocked) {
                return;
            }
            if (root.printerModel != null) {
                root.printerModel.setStatusCollapsed(false);
            }
            root.collapseToggled(false);
        }
    }

    RowLayout {
        id: statusHeader
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
        anchors.leftMargin: UM.Theme.getSize("thin_margin").width
        anchors.rightMargin: UM.Theme.getSize("thin_margin").width
        spacing: UM.Theme.getSize("thin_margin").width
        UM.Label {
            Layout.fillWidth: true
            visible: !root.statusCollapsed
            text: "Printer status"
            font: UM.Theme.getFont("large_bold")
            elide: Text.ElideRight
        }
        Rectangle {
            // The connection dot rides the Printer status
            // pane's title — the chosen spot for
            // the always-readable connection state. Green
            // when connected, red when not.
            Layout.alignment: Qt.AlignVCenter
            visible: !root.statusCollapsed
            width: 10 * screenScaleFactor
            height: 10 * screenScaleFactor
            radius: 5 * screenScaleFactor
            color: connectionDotColour
            HoverHandler {
                id: tooltipHover2
            }
            UM.ToolTip {
                visible: tooltipHover2.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                // The transport detail rides the dot's
                // tooltip: "connected over websocket" or
                // "connected over HTTP polling" (the
                // chosen spot for it).
                text: root.printerModel != null && root.printerModel.monitorConnected ? (root.printerModel.connectionDetail.length > 0 ? "Connected to Moonraker — " + root.printerModel.connectionDetail + "." : "Connected to Moonraker.") : "Disconnected from Moonraker."
            }
        }
        // Open the Moonraker UI in a browser, icon-style in the
        // title row.
        UM.SimpleButton {
            id: frontendButton
            visible: !root.statusCollapsed
            Layout.alignment: Qt.AlignVCenter
            width: 28 * screenScaleFactor
            height: 28 * screenScaleFactor
            color: UM.Theme.getColor("text_inactive")
            hoverColor: UM.Theme.getColor("text")
            iconSource: UM.Theme.getIcon("LinkExternal")
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.openFrontend();
                }
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
                text: "Open the Moonraker frontend."
            }
        }
        // The configure trigger, beside its collapse
        // toggle (the adjudicated placement).
        Cura.SecondaryButton {
            id: statusConfigureButton
            objectName: "configureStatusSectionsButton"
            visible: !root.statusCollapsed
            Layout.alignment: Qt.AlignVCenter
            fixedWidthMode: true
            width: 28 * screenScaleFactor
            height: width
            implicitHeight: width
            text: "⇄"
            onClicked: root.configureRequested()
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "Configure the printer-status sections."
            }
        }
        // The toggle hugs the right edge: the pane is on the
        // right of the screen and collapses into that edge.
        // Left when collapsed (expand left), right when open.
        Cura.SecondaryButton {
            id: statusCollapseButton
            Layout.alignment: Qt.AlignVCenter
            fixedWidthMode: true
            // Square at the OLD button width: the theme
            // adds its padding around the 32px content, so
            // the height tracks the rendered width (the
            // ruling).
            width: 28 * screenScaleFactor
            iconSize: 12 * screenScaleFactor
            height: width
            implicitHeight: width

            // The SAME theme-chevron family as the console
            // and info toggles; this pane is rightmost and
            // collapses right.
            iconSource: root.statusCollapsed ? UM.Theme.getIcon("ChevronSingleLeft") : UM.Theme.getIcon("ChevronSingleRight")
            onClicked: {
                // Auto-collapsed-by-width is not a
                // clickable toggle: only a wider window
                // restores the pane (the information
                // pane's toggle carries the same guard).
                if (root.statusExpandLocked) {
                    return;
                }
                if (root.printerModel != null) {
                    root.printerModel.setStatusCollapsed(!root.statusCollapsed);
                }
                // Collapsing the pane hides the pop-over's
                // opener with it — the host closes the card
                // too (the UX adjudication: only the
                // section-collapse deviation stands).
                root.collapseToggled(!root.statusCollapsed);
            }
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: root.statusExpandLocked ? "The window is too narrow — widen it to show the printer status." : (root.statusCollapsed ? "Show the printer status." : "Hide the printer status.")
            }
        }
    }

    Flickable {
        id: statusFlick
        objectName: "moonrakerStatusFlick"
        visible: !root.statusCollapsed
        anchors.top: statusHeader.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.topMargin: UM.Theme.getSize("default_margin").height
        anchors.leftMargin: UM.Theme.getSize("default_margin").width
        // No right inset: the content's own 14px gutter is
        // the only dead band right of the sections — the
        // same ruling the information pane above carries,
        // and the inset that used to double the pane's
        // right gap against the scroll bar.
        anchors.bottomMargin: UM.Theme.getSize("default_margin").height
        clip: true
        contentWidth: width
        contentHeight: statusContent.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            id: statusScrollbar
        }

        Column {
            id: statusContent
            objectName: "moonrakerStatusContent"
            // The stored order applies HERE — the column's
            // own completion, after its children exist and
            // BEFORE the first frame paints (the 4.5.0 live
            // find: any later apply is a visible jump).
            Component.onCompleted: root.contentReady()
            // The constant gutter, exactly as the information
            // pane above rules it: the scrollbar overlays the
            // gutter rather than squeezing the content in a
            // live-width feedback cycle. Layout.fillWidth is
            // inert here (a Flickable is not a layout), so the
            // explicit viewport-relative width is the only
            // thing keeping the column at its own implicit
            // width while the sections paint past the pane.
            width: statusFlick.width - 14
            // Spacing lives on the children: collapsed sections
            // must contribute nothing so headers stack flush.
            // Sections own their implicit heights. A positioner
            // stacks them without a second layout solver feeding
            // changing row heights back through the whole pane.
            spacing: 0
            JobSection {
                visible: root.printerModel == null || root.printerModel.sectionHiddenMap["job"] !== true
                width: statusContent.width
                printerModel: root.printerModel
            }

            TempsSection {
                width: statusContent.width
                visible: root.printerModel != null && root.printerModel.temperatureItems.length > 0 && root.printerModel.sectionHiddenMap["temps"] !== true
                printerModel: root.printerModel
            }

            FansInfoSection {
                width: statusContent.width
                visible: root.printerModel != null && root.printerModel.fanItems.length > 0 && root.printerModel.sectionHiddenMap["fansinfo"] !== true
                printerModel: root.printerModel
            }
            FilamentSection {
                width: statusContent.width
                visible: root.printerModel != null && root.printerModel.filamentSensorItems.length > 0 && root.printerModel.sectionHiddenMap["filament"] !== true
                printerModel: root.printerModel
            }
            SystemInfoSection {
                visible: root.printerModel == null || root.printerModel.sectionHiddenMap["systeminfo"] !== true
                width: statusContent.width
                printerModel: root.printerModel
            }
            McusSection {
                width: statusContent.width
                visible: root.printerModel != null && root.printerModel.mcuItems.length > 0 && root.printerModel.sectionHiddenMap["mcus"] !== true
                printerModel: root.printerModel
            }
        }
    }

    Item {
        anchors.fill: statusFlick
        visible: !root.statusCollapsed && (root.printerModel == null || root.printerModel.monitorLoading)
        UM.Label {
            anchors.centerIn: parent
            text: "Loading printer data…"
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("default")
        }
    }

    StatusCollapsedReadout {
        id: statusCollapsedReadout
        anchors.top: statusHeader.bottom
        anchors.topMargin: UM.Theme.getSize("thin_margin").height
        anchors.horizontalCenter: parent.horizontalCenter
        statusCollapsed: root.statusCollapsed
        printerModel: root.printerModel
        connectionDotColour: root.connectionDotColour
        etaAvailable: root.etaAvailable
        finishAvailable: root.finishAvailable
        layerCountAvailable: root.layerCountAvailable
        flowAvailable: root.flowAvailable
    }
}
