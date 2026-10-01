import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The follower pop-over's pause schedule: the rows published on the
// print model, their scroll, and the two foot actions. It is the VIEW
// only -- the scheduling itself stays with the model that publishes the
// rows and takes the intents.
ColumnLayout {
    id: root

    property var printerModel: null
    // Wide enough for a row's longest suffix ("— baked ·
    // passed") at the schedule's own font, and it yields
    // to the plate first when the pane is tight: with no
    // minimum of its own it takes only the room that is
    // left over.
    Layout.preferredWidth: 340 * screenScaleFactor
    Layout.minimumWidth: 0
    // The column fills the card: the schedule's list takes
    // whatever height the plate's column does not use.
    Layout.fillHeight: true
    spacing: UM.Theme.getSize("thin_margin").height
    // Whether the column's clear action has anything to
    // clear: the foot row collapses its slot on this.
    readonly property bool clearAvailable: root.printerModel != null && root.printerModel.pauseAtLayerHasClearable === true
    readonly property real clearButtonWidth: Math.ceil(clearTextMetrics.width + 2 * UM.Theme.getSize("default_margin").width + 2 * screenScaleFactor)
    TextMetrics {
        id: clearTextMetrics
        text: "Clear"
        font: UM.Theme.getFont("medium")
    }
    // The rows are read once at open: the block carries
    // values before the popover exists, so a signal-only
    // sync would leave the list blank until the next
    // publish (~2.5 s).
    Component.onCompleted: syncPauseRows()

    // The rows live in a STABLE model the publishes never
    // replace: the block arrives every poll with fresh ETA
    // strings, and handing that array straight to the view
    // replaced the model and jumped the scroll (the card's
    // own live report). syncPauseRows() diffs it in place
    // instead, so the model identity — and the scroll —
    // never move on their own.
    ListModel {
        id: pauseBlockModel
        objectName: "moonrakerFollowerPauseListModel"
    }

    function syncPauseRows() {
        var incoming = root.printerModel != null && root.printerModel.pauseAtLayerItems != null ? root.printerModel.pauseAtLayerItems : [];
        var keep = {};
        for (var i = 0; i < incoming.length; i++) {
            keep[Number(incoming[i].layer || 0)] = true;
        }
        for (var r = pauseBlockModel.count - 1; r >= 0; r--) {
            if (keep[pauseBlockModel.get(r).layerNo] !== true) {
                pauseBlockModel.remove(r);
            }
        }
        for (var k = 0; k < incoming.length; k++) {
            var row = incoming[k];
            // EVERY role is normalised to a concrete value:
            // a role whose first value is undefined is
            // dropped from a ListModel, and the delegate's
            // bare role lookup then throws.
            var payload = {
                "layerNo": Number(row.layer || 0),
                "eta": String(row.eta || ""),
                "pauseWord": String(row.state || "scheduled"),
                "passed": row.passed === true
            };
            var at = -1;
            for (var f = 0; f < pauseBlockModel.count; f++) {
                if (pauseBlockModel.get(f).layerNo === payload.layerNo) {
                    at = f;
                    break;
                }
            }
            if (at === -1) {
                // The published list is sorted, so a fresh
                // entry lands at its sorted position among
                // the rows already there — an append alone
                // would bury a pause scheduled below one
                // that already exists.
                var pos = pauseBlockModel.count;
                for (var s = 0; s < pauseBlockModel.count; s++) {
                    if (pauseBlockModel.get(s).layerNo > payload.layerNo) {
                        pos = s;
                        break;
                    }
                }
                pauseBlockModel.insert(pos, payload);
            } else if (pauseBlockModel.get(at).eta !== payload.eta || pauseBlockModel.get(at).pauseWord !== payload.pauseWord || pauseBlockModel.get(at).passed !== payload.passed) {
                pauseBlockModel.set(at, payload);
            }
        }
    }

    Connections {
        target: root.printerModel
        function onPauseAtLayerChanged() {
            root.syncPauseRows();
        }
    }

    UM.Label {
        Layout.fillWidth: true
        // The heading collapses with its own text: it is
        // for a schedule that has rows (a height, never a
        // visibility — the no-reflow rule).
        Layout.preferredHeight: text.length > 0 ? implicitHeight : 0
        text: pauseBlockModel.count > 0 ? "Enabled pauses" : ""
        color: UM.Theme.getColor("text")
        font: UM.Theme.getFont("default_bold")
    }

    Item {
        id: pauseListViewport
        Layout.fillWidth: true
        // The list owns the column's remaining height (the
        // live request): as many rows as fit, and the
        // chevrons mark the ones that do not.
        Layout.fillHeight: true
        property real rowSpacing: 2 * screenScaleFactor

        // The wheel over the list belongs to the LIST: an
        // unaccepted notch fell through to the camera under
        // the card and zoomed the webcam (the live report).
        // A list with nothing to scroll swallows it too.
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: function (wheel) {
                wheel.accepted = true;
            }
        }

        ListView {
            id: pauseListView
            anchors.fill: parent
            clip: true
            spacing: pauseListViewport.rowSpacing
            // A schedule of five or fewer rows is never
            // dragged off its own content.
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            model: pauseBlockModel
            delegate: Row {
                id: pauseRow
                width: pauseListView.width
                height: UM.Theme.getSize("action_button").height
                spacing: UM.Theme.getSize("thin_margin").width
                // The roles are DIRECT delegate-context
                // properties (the canonical ListModel
                // idiom): the layer and state ROLES carry
                // non-colliding names, because bare `layer`
                // and `state` hit Qt's built-in Item.layer /
                // Item.state on some engines — every row
                // then read layer 0 (the card's live
                // report) while eta and passed still
                // resolved.
                property int pauseLayer: Number(layerNo)
                property string pauseEta: String(eta || "")
                // "scheduled" | "fired" | "passed" |
                // "failed" | "timed_out" | "baked" — a
                // missed pause stays listed; a baked pause
                // is read-only (the card's rulings).
                property string pauseState: String(pauseWord || "scheduled")
                readonly property bool pauseMissed: pauseState === "failed" || pauseState === "timed_out"
                readonly property bool pauseBaked: pauseState === "baked"
                readonly property bool pausePassed: passed === true || pauseState === "passed"

                UM.Label {
                    width: Math.max(0, parent.width - removePauseGlyph.width - parent.spacing)
                    height: parent.height
                    text: "End of layer " + pauseRow.pauseLayer + (!pauseRow.pausePassed && pauseRow.pauseEta.length > 0 ? " · " + pauseRow.pauseEta : "") + (pauseRow.pauseMissed ? " — pause not taken" : "") + (pauseRow.pauseBaked ? (pauseRow.pausePassed ? " — baked · passed" : " — baked") : (pauseRow.pauseState === "passed" ? " — passed" : ""))
                    color: pauseRow.pauseMissed ? UM.Theme.getColor("error") : (pauseRow.pausePassed ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text"))
                    font: UM.Theme.getFont("default")
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                UM.Label {
                    id: removePauseGlyph
                    // The narrow ✕ (the card's ruling): a
                    // glyph, not a chrome button — it never
                    // hides; a baked row dims it and the
                    // click does nothing.
                    width: parent.height
                    height: parent.height
                    text: "✕"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    color: pauseRow.pauseBaked ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("error")
                    font: UM.Theme.getFont("medium_bold")
                    MouseArea {
                        anchors.fill: parent
                        enabled: !pauseRow.pauseBaked
                        hoverEnabled: true
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.removePauseAtLayer(pauseRow.pauseLayer);
                            }
                        }
                    }
                }
            }
        }

        // Scroll affordances, the card's idiom: small
        // chevrons centred over the list — an up arrow
        // while more content is above, a down arrow while
        // more is below (the ruling).
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

    UM.Label {
        // Why the button below is dead, in the card's own
        // words. The line collapses by height while it has
        // nothing to say — a height, never a visibility.
        Layout.fillWidth: true
        Layout.preferredHeight: text.length > 0 ? implicitHeight : 0
        text: {
            var model = root.printerModel;
            if (model == null || model.pauseAtLayerScheduled === true || model.pauseAtLayerCanToggle === true) {
                return "";
            }
            var why = model.pauseAtLayerUnavailableText;
            return why ? "Can't schedule: " + why : "";
        }
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("default_italic")
        wrapMode: Text.WordWrap
        elide: Text.ElideRight
    }

    // The two actions sit together at the foot of the
    // column (the live request): schedule or remove the
    // pause the slider stands on, and clear the rest.
    RowLayout {
        Layout.fillWidth: true
        // Nothing to clear means no slot, no gap: the
        // schedule button then takes the whole row (the
        // live request).
        spacing: root.clearAvailable ? UM.Theme.getSize("thin_margin").width : 0
        Cura.SecondaryButton {
            id: pauseAtLayerButton
            objectName: "moonrakerFollowerPauseButton"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            // The label centres in the slack this button
            // takes (the live request); the theme's own
            // content row packs from the left, so the
            // fixed-width mode is what centring needs.
            fixedWidthMode: true
            // The button never hides (the no-reflow rule): it
            // disables, and the line below it says why.
            enabled: root.printerModel != null && root.printerModel.pauseAtLayerActive === true && (root.printerModel.pauseAtLayerScheduled === true || root.printerModel.pauseAtLayerCanToggle === true)
            text: {
                // The candidate is a 1-based human layer, 0
                // while no layer is known (the card's own
                // contract).
                var layer = Number(root.printerModel != null ? root.printerModel.pauseAtLayerCandidate : 0) || 0;
                if (layer <= 0) {
                    return "Pause at end of layer";
                }
                return root.printerModel.pauseAtLayerScheduled === true ? "Remove pause after layer " + layer : "Pause at end of layer " + layer;
            }
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.togglePauseAtLayer(root.printerModel.pauseAtLayerCandidate);
                }
            }
        }

        Cura.SecondaryButton {
            id: clearPausesButton
            // Collapses in place while nothing is clearable (a
            // baked-only list has no manual rows — the card's
            // own ruling): a height, never a visibility — and
            // the width goes with it, so the schedule button
            // beside it takes the whole row.
            // Its own screen's word (the live request): the
            // card keeps the longer line. Measure the theme font
            // and side padding: fixed 60 px elides Clear in Cura.
            fixedWidthMode: true
            Layout.minimumWidth: root.clearAvailable ? root.clearButtonWidth : 0
            Layout.preferredWidth: root.clearAvailable ? root.clearButtonWidth : 0
            Layout.preferredHeight: root.clearAvailable ? UM.Theme.getSize("action_button").height : 0
            enabled: root.clearAvailable
            text: "Clear"
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.clearPauseAtLayer();
                }
            }
        }
    }
}
