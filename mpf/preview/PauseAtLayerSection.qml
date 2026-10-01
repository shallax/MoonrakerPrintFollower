import QtQuick 2.15
import UM 1.5 as UM
import "../widgets"

// The end-of-layer pause section: the schedule toggle, the hint that
// explains it and the list of scheduled pauses with its clear-all. The
// rows live in a STABLE ListModel this component owns and diffs in
// place (the published list arrives every poll, and handing it straight
// to the view moved the scroll); the card publishes the values and
// forwards the three verbs.
Column {
    id: root
    property bool hasToolpath: false
    property bool followingEnabled: false
    property bool pauseAtLayerActive: false
    property bool pauseAtLayerScheduled: false
    property bool pauseAtLayerCanToggle: false
    property int pauseAtLayerCandidate: 0
    property string pauseAtLayerUnavailableText: ""
    property var pauseAtLayerItems: []
    property bool pauseAtLayerHasBaked: false
    property bool pauseAtLayerHasClearable: false
    property real buttonSpacing: UM.Theme.getSize("default_margin").width
    // The hint below the toggle keeps its slot at every gate (a
    // permanent slot — the M117 precedent), so the section can stand
    // with NOTHING showing but a zero-height label. An empty Column is
    // still a visible child of the card's column and would take a row
    // gap for itself, so the section hides whole instead.
    visible: root.implicitHeight > 0
    signal pauseAtLayerRequested(int layer)
    signal removePauseAtLayerRequested(int layer)
    signal clearPauseAtLayersRequested

    // The pause rows live in a STABLE ListModel the coordinator never
    // touches directly: syncPauseRows() diffs the published list into
    // it in place, so the ListView's model identity never changes and
    // the scroll stays put across publishes, adds and removals.
    ListModel {
        id: pauseListModel
        objectName: "moonrakerPauseListModel"
    }

    function syncPauseRows() {
        var incoming = root.pauseAtLayerItems || [];
        var keep = {};
        for (var i = 0; i < incoming.length; i++) {
            keep[Number(incoming[i].layer || 0)] = true;
        }
        for (var r = pauseListModel.count - 1; r >= 0; r--) {
            if (!keep[pauseListModel.get(r).layerNo]) {
                pauseListModel.remove(r);
            }
        }
        for (var k = 0; k < incoming.length; k++) {
            var row = incoming[k];
            // EVERY role is normalised to a concrete value: a role whose
            // first value is undefined is dropped from the ListModel, and
            // the delegate's bare role lookup then throws ReferenceError
            // (the capture leg's live catch for pauseWord).
            var payload = {
                "layerNo": Number(row.layer || 0),
                "eta": String(row.eta || ""),
                "pauseWord": String(row.state || "scheduled"),
                "passed": row.passed === true
            };
            var at = -1;
            for (var f = 0; f < pauseListModel.count; f++) {
                if (pauseListModel.get(f).layerNo === payload.layerNo) {
                    at = f;
                    break;
                }
            }
            if (at === -1) {
                // The published list is sorted; a fresh entry lands
                // at its sorted position among the existing rows.
                var pos = pauseListModel.count;
                for (var s = 0; s < pauseListModel.count; s++) {
                    if (pauseListModel.get(s).layerNo > payload.layerNo) {
                        pos = s;
                        break;
                    }
                }
                pauseListModel.insert(pos, payload);
            } else if (pauseListModel.get(at).eta !== payload.eta || pauseListModel.get(at).pauseWord !== payload.pauseWord || pauseListModel.get(at).passed !== payload.passed) {
                pauseListModel.set(at, payload);
            }
        }
    }

    onPauseAtLayerItemsChanged: syncPauseRows()

    PreviewSecondaryButton {
        id: pauseAtLayerButton
        // Hides without a toolpath (the 2026-09-17 ruling:
        // scheduling against an absent layer view is a lie)
        // — with one, it disables until a schedulable layer
        // is selected.
        visible: root.hasToolpath
        width: parent.width
        height: UM.Theme.getSize("action_button").height
        enabled: (root.pauseAtLayerScheduled || root.pauseAtLayerCanToggle) && root.followingEnabled && root.pauseAtLayerActive
        text: root.pauseAtLayerCandidate <= 0 ? "⏸  Pause at end of selected layer" : (root.pauseAtLayerScheduled ? "Remove pause after layer " + root.pauseAtLayerCandidate : "⏸  Enable pause at end of layer " + root.pauseAtLayerCandidate)
        onClicked: root.pauseAtLayerRequested(root.pauseAtLayerCandidate)
        UM.ToolTip {
            visible: parent.hovered
            targetPoint: Qt.point(parent.width / 2, 0)
            x: 0
            y: parent.height + UM.Theme.getSize("default_margin").height
            width: UM.Theme.getSize("tooltip").width
            text: root.pauseAtLayerScheduled ? "Remove the scheduled end-of-layer PAUSE." : (root.pauseAtLayerCanToggle ? "Call the Klipper PAUSE macro once this layer has finished and Moonraker advances to the following layer." : "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE.")
        }
    }

    UM.Label {
        // The scheduling hint lives only while a toolpath
        // exists; with the card visible in every state, a
        // permanent empty slot would read as a gap on an
        // empty scene.
        width: parent.width
        height: root.hasToolpath ? 36 * screenScaleFactor : 0
        text: (!root.pauseAtLayerScheduled && !root.pauseAtLayerCanToggle && root.pauseAtLayerUnavailableText.length > 0) ? "Can't schedule: " + root.pauseAtLayerUnavailableText : (root.hasToolpath ? "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE." : "")
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("default_italic")
        wrapMode: Text.WordWrap
        elide: Text.ElideRight
        clip: true
    }

    Column {
        id: scheduledPauseList
        // The improve-Eta path (the ruling): the index alone
        // reveals the baked rows, no toolpath needed — manual
        // rows can't exist without one, so the baked flag
        // keeps a stale manual schedule out of the light view.
        visible: root.followingEnabled && root.pauseAtLayerActive && root.pauseAtLayerItems.length > 0 && (root.hasToolpath || root.pauseAtLayerHasBaked)
        width: parent.width
        height: visible ? implicitHeight : 0
        spacing: 2 * screenScaleFactor

        UM.Label {
            width: parent.width
            text: "Enabled pauses"
            color: UM.Theme.getColor("text")
            font: UM.Theme.getFont("default_bold")
        }

        Item {
            width: parent.width
            // Five visible entries at most (the
            // ruling): a long schedule scrolls instead of
            // growing the card past the viewport.
            height: Math.min(root.pauseAtLayerItems.length, 5) * (UM.Theme.getSize("action_button").height + scheduledPauseList.spacing) - scheduledPauseList.spacing

            ListView {
                id: pauseListView
                anchors.fill: parent
                clip: true
                spacing: scheduledPauseList.spacing
                interactive: contentHeight > height
                boundsBehavior: Flickable.StopAtBounds
                // The STABLE model: the coordinator re-publishes
                // the list every cycle (~2.5 s) with fresh ETA
                // strings — handing that straight to the view
                // replaced the model on every poll and the view
                // jumped (the live reports). The card syncs the
                // rows IN PLACE here, so the model never
                // changes identity and the scroll never moves
                // on its own.
                model: pauseListModel
                delegate: Row {
                    id: pauseRow
                    width: scheduledPauseList.width
                    height: UM.Theme.getSize("action_button").height
                    spacing: root.buttonSpacing
                    // The roles are DIRECT delegate-context
                    // properties for a ListModel (the canonical
                    // idiom) — `modelData` is a JS-array-model
                    // concept and read undefined (the live
                    // report: every row read layer 0), and
                    // `model.layer` crashed the pinned
                    // container's engine at load. The layer and
                    // state ROLES carry non-colliding names
                    // (layerNo, pauseWord): bare `layer` and
                    // `state` hit Qt's built-in Item.layer /
                    // Item.state properties on some engines,
                    // which shadow the roles and read
                    // NaN / "" — every row then shows layer 0
                    // (the live report) while eta and passed
                    // still resolve.
                    property int pauseLayer: Number(layerNo)
                    property string pauseEta: String(eta || "")
                    // "scheduled" | "fired" | "failed" | "timed_out" |
                    // "baked" — a missed pause STAYS listed, restyled
                    // in the error colour (the verified-pause-only
                    // ruling); a baked pause is read-only (the
                    // ruling).
                    property string pauseState: String(pauseWord || "scheduled")
                    readonly property bool pauseMissed: pauseState === "failed" || pauseState === "timed_out"
                    readonly property bool pauseBaked: pauseState === "baked"
                    readonly property bool pausePassed: passed === true || pauseState === "passed"

                    UM.Label {
                        width: Math.max(0, parent.width - removePauseButton.width - parent.spacing)
                        height: parent.height
                        text: "End of layer " + parent.pauseLayer + (!parent.pausePassed && parent.pauseEta.length > 0 ? " · " + parent.pauseEta : "") + (parent.pauseMissed ? " — pause not taken" : "") + (parent.pauseBaked ? (parent.pausePassed ? " — baked · passed" : " — baked") : (pauseState === "passed" ? " — passed" : ""))
                        color: parent.pauseMissed ? UM.Theme.getColor("error") : (parent.pausePassed ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text"))
                        font: UM.Theme.getFont("default")
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }

                    UM.Label {
                        id: removePauseButton
                        // The narrow red ✕ (the 2026-09-16
                        // ruling): a glyph, not a chrome
                        // button — it buys the row text the
                        // room the clock-time ETA needs. It
                        // NEVER hides (the no-reflow rule): a
                        // baked pause dims it and the click
                        // does nothing.
                        width: parent.height
                        height: parent.height
                        text: "✕"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        color: parent.pauseBaked ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("error")
                        font: UM.Theme.getFont("medium_bold")
                        MouseArea {
                            anchors.fill: parent
                            // The row's properties, not the
                            // label's — parent here is the
                            // glyph, which carries neither
                            // (the dead-click report).
                            enabled: !pauseRow.pauseBaked
                            hoverEnabled: true
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: root.removePauseAtLayerRequested(pauseRow.pauseLayer)
                        }
                    }
                }
            }

            // Scroll affordances, the whats-new idiom: small
            // blue chevrons centred over the list — an up
            // arrow near the top while more content is above,
            // a down arrow near the bottom while more content
            // is below (the ruling). They are
            // SIBLINGS of the ListView, overlaying it.
            UM.Label {
                text: "↑"
                visible: pauseListView.height > 0 && pauseListView.contentY > 2
                anchors.top: pauseListView.top
                anchors.horizontalCenter: pauseListView.horizontalCenter
                anchors.topMargin: 4 * screenScaleFactor
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                text: "↓"
                visible: pauseListView.height > 0 && pauseListView.contentY < pauseListView.contentHeight - pauseListView.height - 2
                anchors.bottom: pauseListView.bottom
                anchors.horizontalCenter: pauseListView.horizontalCenter
                anchors.bottomMargin: 4 * screenScaleFactor
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("medium_bold")
            }
        }

        PreviewSecondaryButton {
            // Hides while nothing is clearable — a baked-only
            // list (the improve-Eta path) has no manual rows
            // to remove (the 2026-09-17 ruling).
            visible: root.pauseAtLayerHasClearable
            width: parent.width
            height: UM.Theme.getSize("action_button").height
            text: "Clear all pauses"
            onClicked: root.clearPauseAtLayersRequested()
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "Remove every scheduled end-of-layer PAUSE for the current print."
            }
        }
    }
}
