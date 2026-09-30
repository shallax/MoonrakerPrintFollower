import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import ".."
import "../temperature"
import "../../plate"
import "../../widgets"

// The information pane: the left strip, the column of section widgets
// it folds away, and the collapsed readout. The host owns the
// narrow-window fold, the open pop-over and the stored section order —
// the first two arrive as inputs, the rest as intents.
Cura.RoundedRectangle {
    id: root

    objectName: "infoPanel"
    property var printerModel: null
    // The frozen narrow-window fold, bound at the call site.
    property bool infoCollapsed: false
    property bool infoExpandLocked: false
    property string hotendText: "—"
    property string bedText: "—"
    property var miniChartSeries: []
    property bool miniChartHasSeries: false

    // The two items the host's own passes work on: the sections column
    // (the stored order) and the strip's readout row (the fit). Handing
    // them out keeps those passes where they were.
    property alias content: infoContent
    property alias readoutRow: infoCollapsedReadout.readoutRow
    // Where the configure card hangs from, in this pane's frame.
    readonly property point headerAnchor: Qt.point(infoHeader.x, infoHeader.y + infoHeader.height)

    signal collapseToggled(bool collapsing)
    signal configureRequested
    signal popOverToggleRequested(string name)
    signal contentReady

    // The collapsed readout may outrun a short pane: the
    // PANE clips, so no child can ever spill past its
    // bounds (the live report: every readout overflowed).
    clip: true
    // Collapsed, the pane shrinks to the toggle button and its
    // margins; the vertical title below explains the strip.
    Layout.preferredWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 270 * screenScaleFactor)
    Layout.minimumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 200 * screenScaleFactor)
    // Shrink-only: max == preferred keeps the wide layout
    // unchanged, but narrow stages may compress the pane.
    Layout.maximumWidth: (root.infoCollapsed ? infoCollapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 270 * screenScaleFactor)
    Layout.fillWidth: true
    Layout.fillHeight: true
    // No Layout.leftMargin here: the host RowLayout's
    // anchors.margins already indents every pane, and the
    // doubled left edge read wider than the right pane's
    // (the report).
    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    color: UM.Theme.getColor("main_background")
    radius: UM.Theme.getSize("default_radius").width

    MouseArea {
        visible: root.infoCollapsed
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            // Auto-collapsed-by-width is not a clickable
            // expand: only a wider window restores the
            // pane (the header button's guard, and the
            // console's strip precedent). Unguarded, this
            // click discarded the user's own collapse and
            // the pane sprang open on the next widen.
            if (root.infoExpandLocked) {
                return;
            }
            if (root.printerModel != null) {
                root.printerModel.setInfoCollapsed(false);
            }
            root.collapseToggled(false);
        }
    }

    RowLayout {
        id: infoHeader
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
        anchors.leftMargin: UM.Theme.getSize("thin_margin").width
        anchors.rightMargin: UM.Theme.getSize("thin_margin").width
        spacing: UM.Theme.getSize("thin_margin").width
        // The toggle hugs the edge the pane collapses into:
        // this pane is leftmost, so the button leads.
        Cura.SecondaryButton {
            id: infoCollapseButton
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
            // and status toggles (the ruling: all
            // pane collapse buttons uniform). This pane is
            // leftmost and collapses left.
            iconSource: root.infoCollapsed ? UM.Theme.getIcon("ChevronSingleRight") : UM.Theme.getIcon("ChevronSingleLeft")
            onClicked: {
                // Auto-collapsed-by-width is not a
                // clickable toggle: only a wider window
                // restores the pane (the console's
                // too-narrow precedent).
                if (root.infoExpandLocked) {
                    return;
                }
                // The NEW state is computed locally: the
                // property binding may not have re-evaluated
                // yet when this handler reads it back.
                var collapsing = root.printerModel == null || !root.infoCollapsed;
                if (root.printerModel != null) {
                    root.printerModel.setInfoCollapsed(collapsing);
                }
                // Collapsing the pane hides the pop-over's
                // opener with it — the host closes the card
                // too (the UX adjudication: only the
                // section-collapse deviation stands).
                root.collapseToggled(collapsing);
            }
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: root.infoExpandLocked ? "The window is too narrow — widen it to show the information." : (root.infoCollapsed ? "Show the information." : "Hide the information.")
            }
        }
        // The configure trigger: the column configurer's
        // glyph, adjacent to the collapse toggle (the
        // adjudicated placement).
        Cura.SecondaryButton {
            id: infoConfigureButton
            objectName: "configureInfoSectionsButton"
            visible: !root.infoCollapsed
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
                text: "Configure the information sections."
            }
        }
        UM.Label {
            Layout.fillWidth: true
            visible: !root.infoCollapsed
            text: "Information"
            font: UM.Theme.getFont("large_bold")
            elide: Text.ElideRight
        }
    }

    Flickable {
        id: infoFlick
        visible: !root.infoCollapsed
        anchors.top: infoHeader.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.topMargin: UM.Theme.getSize("default_margin").height
        anchors.leftMargin: UM.Theme.getSize("default_margin").width
        // No right inset: the content's own 14px gutter is
        // the only dead band right of the sections (the
        // 4.5.0 live ruling — a margin's width, no more).
        anchors.bottomMargin: UM.Theme.getSize("default_margin").height
        clip: true
        contentWidth: width
        contentHeight: infoContent.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            id: infoScrollbar
        }

        ColumnLayout {
            id: infoContent
            objectName: "moonrakerInfoContent"
            // The stored order applies HERE — before the
            // first frame paints (the 4.5.0 live find).
            Component.onCompleted: root.contentReady()
            // The constant gutter (the status pane's own
            // precedent): binding the content width to the
            // LIVE scrollbar width feeds the polish loop —
            // the scrollbar overlays the gutter instead of
            // squeezing the content in a feedback cycle.
            // Layout.fillWidth is inert here (the Flickable
            // is not a layout) — the 4.5.0 live find's
            // 1px crush; the explicit width stays, trimmed
            // to the 14px gutter alone.
            width: infoFlick.width - 14
            // Spacing lives on the children: collapsed sections
            // must contribute nothing so headers stack flush.
            spacing: 0
            MeshSection {
                id: meshSection
                visible: root.printerModel == null || root.printerModel.sectionHiddenMap["meshmap"] !== true
                Layout.fillWidth: true
                printerModel: root.printerModel
                onPopOverToggleRequested: function (name) {
                    root.popOverToggleRequested(name);
                }
            }
            PlateProgressSection {
                visible: root.printerModel == null || root.printerModel.sectionHiddenMap["plateprogress"] !== true
                Layout.fillWidth: true
                printerModel: root.printerModel
                onPopOverToggleRequested: function (name) {
                    root.popOverToggleRequested(name);
                }
            }
            PlateSection {
                // During a print the picker shows either
                // the plate (when the print carries the
                // exclude-object data) or the download
                // offer (the live rulings); it clears
                // with the job epoch.
                visible: root.printerModel != null && root.printerModel.sectionHiddenMap["plate"] !== true && (root.printerModel.printActive || root.printerModel.plateHasObjects)
                Layout.fillWidth: true
                printerModel: root.printerModel
                onPopOverToggleRequested: function (name) {
                    root.popOverToggleRequested(name);
                }
            }
            TempHistorySection {
                visible: root.printerModel == null || root.printerModel.sectionHiddenMap["temphistory"] !== true
                Layout.fillWidth: true
                printerModel: root.printerModel
                miniSeries: root.miniChartSeries
                miniHasSeries: root.miniChartHasSeries
                onPopOverToggleRequested: function (name) {
                    root.popOverToggleRequested(name);
                }
            }
        }
    }

    // The mini map re-reads the control set its own map depends on;
    // the pop-over's auto-close stays with the host, which owns the
    // open card.
    Connections {
        target: root.printerModel
        function onTypedControlsChanged() {
            meshSection.refreshMap();
        }
    }

    InfoCollapsedReadout {
        id: infoCollapsedReadout
        anchors.top: infoHeader.bottom
        anchors.topMargin: UM.Theme.getSize("thin_margin").height
        anchors.horizontalCenter: parent.horizontalCenter
        infoCollapsed: root.infoCollapsed
        hotendText: root.hotendText
        bedText: root.bedText
    }
}
