import QtQuick 2.15
import QtQuick.Layouts 1.3
import QtQuick.Window 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../widgets"
import "../preview"

// The print-follower card: the plate as the control, its view options
// and scrubbers, and the pause schedule beside it. The host owns the
// overlay frame (position, one-at-a-time open state) and passes the
// printer in.
MonitorPopOver {
    id: root

    property var printerModel: null
    property bool open: false

    visible: root.open && root.printerModel != null
    title: "Print Follower"

    Loader {
        Layout.fillWidth: true
        Layout.fillHeight: true
        active: root.open && root.printerModel != null
        sourceComponent: followerContent
    }

    Component {
        id: followerContent
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: UM.Theme.getSize("default_margin").width

            // The slider functions live on the COMPONENT ROOT: a
            // function declared on a nested item is invisible to its
            // own descendants, and these are called from all over the
            // plate's column (the probe's ReferenceError).
            // The slider follows the model's anchor — the live
            // layer while attached, the frozen one after a seek —
            // and never fights a drag in flight or a settle.
            function syncLayerSlider() {
                if (layerSlider.interacting || layerSeekTimer.running) {
                    return;
                }
                if (root.printerModel != null && root.printerModel.plateProgressAnchor >= 0) {
                    layerSlider.value = root.printerModel.plateProgressAnchor;
                }
            }
            function commitLayerSeek() {
                if (root.printerModel != null) {
                    root.printerModel.setFollowerLayerAnchor(layerSlider.selectedValue());
                }
            }
            function layerReadout() {
                if (root.printerModel == null || root.printerModel.plateLayerCount <= 0) {
                    return "—";
                }
                var index = root.printerModel.followerAttached ? root.printerModel.plateProgressAnchor : layerSlider.selectedValue();
                if (index < 0) {
                    return "—";
                }
                return (Math.round(index) + 1) + " / " + root.printerModel.plateLayerCount;
            }
            function syncProgressSlider() {
                if (layerProgressSlider.interacting) {
                    return;
                }
                var split = root.printerModel != null ? root.printerModel.plateSplit : null;
                layerProgressSlider.value = split != null ? Math.max(0, split) : 0;
            }
            function commitProgressSeek() {
                if (root.printerModel != null) {
                    var total = root.printerModel.plateLayerMotionCount;
                    var selected = layerProgressSlider.selectedValue();
                    if (total >= 10000)
                        selected = Math.round(Math.round(selected / total * 10000) * total / 10000);
                    root.printerModel.setFollowerLayerProgress(selected);
                }
            }
            // On the component root with the other slider functions: a
            // function declared on a nested item is invisible to that
            // item's own descendants, and the plate's column calls this
            // one from beside its face (the engine's ReferenceError).
            function _feedRenderView() {
                if (root.printerModel != null) {
                    // The device-pixel backing (bounded supersampling):
                    // the worker paints at the screen's physical
                    // resolution and the scene-graph samples down to
                    // the logical face — never an enlarged 1x raster.
                    root.printerModel.setFollowerView("popover", progressFace.viewScale, progressFace.lineScale, progressFace.width, progressFace.height, progressFace.compact, progressFace.viewPanX, progressFace.viewPanY, Math.min(2.0, Math.max(1.0, Screen.devicePixelRatio)), progressFace.toolpathWidthPx());
                }
            }

            ColumnLayout {
                // The plate's width is the layout's invariant — the
                // popover's old content width, held at every pane
                // width. Without it the plate absorbs the whole
                // deficit itself and the empty schedule column beside
                // it takes the face's width instead.
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 585 * screenScaleFactor - 2 * UM.Theme.getSize("default_margin").width
                Layout.minimumWidth: 585 * screenScaleFactor - 2 * UM.Theme.getSize("default_margin").width
                spacing: UM.Theme.getSize("thin_margin").height
                // The keyboard path: the layer slider takes the focus
                // on open, so the arrows drive it immediately (the
                // live request).
                Component.onCompleted: layerSlider.forceActiveFocus()
                Connections {
                    target: progressFace
                    // The settle owns the native-render feed: the
                    // view re-rasters ONCE, 150 ms after the last
                    // zoom/pan/line change (the review's finding 1 —
                    // per-tick feeds churned the renderer).
                    function onViewSettled() {
                        _feedRenderView();
                    }
                    function onManuallyPanned() {
                        if (root.printerModel != null)
                            root.printerModel.setFollowerKeepCentred(false);
                    }
                    function onPlotChanged() {
                        var plot = progressFace.plot;
                        if (root.printerModel != null && plot != null) {
                            // The SURFACE is explicit (the review's
                            // finding 1): this face is the popover.
                            root.printerModel.setFollowerPlot("popover", plot.bed.offsetX, plot.bed.offsetY, plot.sx, plot.sy, plot.bed.bedXMin, plot.bed.bedYMax);
                            _feedRenderView();
                        }
                    }
                }
                PlateProgressFace {
                    id: progressFace
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 200 * screenScaleFactor
                    printerModel: root.printerModel
                    scrubVector: root.printerModel != null ? root.printerModel.plateScrubVector : null
                    progress: root.printerModel != null ? ({
                            "available": root.printerModel.plateProgressAvailable,
                            "scrubVector": progressFace.scrubVector,
                            "reason": root.printerModel.plateProgressReason,
                            "layers": root.printerModel.plateLayers,
                            "split": root.printerModel.plateSplit,
                            "partial": root.printerModel.platePartial,
                            "anchor": root.printerModel.plateProgressAnchor,
                            "method": "motion index",
                            "navigationData": root.printerModel.plateNavigationData,
                            "navigationSplit": root.printerModel.plateNavigationSplit,
                            "navigationBacking": root.printerModel.plateNavigationBacking,
                            "sceneEpoch": root.printerModel.plateSceneEpoch
                        }) : null
                    dot: root.printerModel != null ? root.printerModel.plateDot : null
                    // The persisted global view settings (the live
                    // ruling) — the face's own handlers fire on the
                    // rebinds and re-raster.
                    showPrevious: root.printerModel != null ? root.printerModel.followerShowPrevious : true
                    showNext: root.printerModel != null ? root.printerModel.followerShowNext : true
                    // The layer ghost frames the frozen layer's
                    // partial fill on ANY layer — attached or
                    // detached (the live request).
                    showBase: root.printerModel != null ? root.printerModel.followerShowBase : true
                    showTravels: root.printerModel != null ? root.printerModel.followerShowTravels : false
                    showAxisArrows: root.printerModel != null ? root.printerModel.followerShowAxisArrows : true
                    showRetractions: root.printerModel != null ? root.printerModel.followerShowRetractions : false
                    showUnretractions: root.printerModel != null ? root.printerModel.followerShowUnretractions : false
                    motionSmoothing: root.printerModel != null ? root.printerModel.followerMotionSmoothing : false
                    smoothToolpaths: root.printerModel != null ? root.printerModel.followerAntialiasing : false
                    softwareRendering: root.printerModel != null ? root.printerModel.followerSoftwareRendering : false
                    trueThickness: root.printerModel != null && root.printerModel.followerTrueThickness
                    pixelLineWidth: true
                    keepCentred: root.printerModel != null && root.printerModel.followerKeepCentred === true
                    lineScale: root.printerModel != null ? root.printerModel.followerLineScale : 1.0
                    // The follow state (the centred follow is
                    // retired — the per-poll re-pan was too slow).
                    attached: root.printerModel == null || root.printerModel.followerAttached
                }

                // The toolhead view controls (the 4.6.0 request): the
                // jump is one shot onto the dot's bed position, the
                // option keeps it there. The jump is disabled with
                // nothing to centre on; the option is a preference and
                // persists, so it stays live. At 100% the whole bed
                // fits and both are moot — they hide in place (the
                // live request), the preference itself is untouched.

                // The checkbox's OWN text label (the live reports: a
                // separate Label neither toggles on click nor hugs the
                // indicator, and the wrapper rows warped the heights).
                // The control's text is part of its clickable area and
                // carries the theme's own snug indicator gap — so the
                // rows read as pairs at the standard flow gap.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("narrow_margin").height
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("narrow_margin").height
                        Flow {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("narrow_margin").height
                            UM.CheckBox {
                                text: "True thickness"
                                checked: root.printerModel != null && root.printerModel.followerTrueThickness
                                onToggled: if (root.printerModel != null)
                                    root.printerModel.setFollowerTrueThickness(checked)
                            }
                            UM.CheckBox {
                                text: "Previous layer"
                                checked: root.printerModel != null ? root.printerModel.followerShowPrevious : true
                                onToggled: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFollowerShowPrevious(checked);
                                    }
                                }
                            }
                            UM.CheckBox {
                                text: "Next layer"
                                checked: root.printerModel != null ? root.printerModel.followerShowNext : true
                                onToggled: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFollowerShowNext(checked);
                                    }
                                }
                            }
                            UM.CheckBox {
                                text: "Layer ghost"
                                checked: root.printerModel != null ? root.printerModel.followerShowBase : true
                                onToggled: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFollowerShowBase(checked);
                                    }
                                }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("narrow_margin").height
                            UM.CheckBox {
                                text: "Travels"
                                checked: root.printerModel != null ? root.printerModel.followerShowTravels : false
                                onToggled: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFollowerShowTravels(checked);
                                    }
                                }
                            }
                            UM.CheckBox {
                                text: "Retractions"
                                checked: root.printerModel != null ? root.printerModel.followerShowRetractions : false
                                onToggled: if (root.printerModel != null)
                                    root.printerModel.setFollowerShowRetractions(checked)
                            }
                            UM.CheckBox {
                                text: "Unretractions"
                                checked: root.printerModel != null ? root.printerModel.followerShowUnretractions : false
                                onToggled: if (root.printerModel != null)
                                    root.printerModel.setFollowerShowUnretractions(checked)
                            }
                            UM.CheckBox {
                                objectName: "followerShowAxisArrows"
                                text: "Axis arrows"
                                checked: root.printerModel != null ? root.printerModel.followerShowAxisArrows : true
                                onToggled: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFollowerShowAxisArrows(checked);
                                    }
                                }
                            }
                            UM.CheckBox {
                                visible: progressFace.gpuRendering
                                text: "Antialiasing"
                                checked: root.printerModel != null ? root.printerModel.followerAntialiasing : false
                                onToggled: {
                                    if (root.printerModel != null) {
                                        root.printerModel.setFollowerAntialiasing(checked);
                                    }
                                }
                            }
                        }
                    }
                    // The view reset: right of the checkbox row's free
                    // space, outside the canvas entirely — its
                    // appearance never reflows the plate.
                    UM.Label {
                        readonly property bool canReset: progressFace.available() && !progressFace.compact && progressFace.viewScale > 1.0
                        opacity: canReset ? 1 : 0
                        enabled: canReset
                        text: "Reset view"
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("primary")
                        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                        MouseArea {
                            anchors.fill: parent
                            onClicked: progressFace.resetView()
                        }
                    }
                }

                FollowerColourControls {
                    Layout.fillWidth: true
                    printerModel: root.printerModel
                    face: progressFace
                }

                // The stroke thickness control (the live request):
                // scales every follower stroke, 0.5x to 2x.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("thin_margin").width
                    UM.Label {
                        text: "Line thickness"
                        color: UM.Theme.getColor("text_inactive")
                    }
                    Cura.SecondaryButton {
                        id: thinnerButton
                        fixedWidthMode: true
                        width: 28 * screenScaleFactor
                        text: "−"
                        enabled: root.printerModel != null && (root.printerModel.followerTrueThickness || root.printerModel.followerLineScale > 1.0)
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.setFollowerLineScale(Math.max(1.0, root.printerModel.followerLineScale - 1.0));
                            }
                        }
                    }
                    UM.Label {
                        text: root.printerModel != null && root.printerModel.followerTrueThickness ? "True" : (root.printerModel != null ? root.printerModel.followerLineScale : 1.0).toFixed(0) + " px"
                        width: 34 * screenScaleFactor
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Cura.SecondaryButton {
                        id: thickerButton
                        fixedWidthMode: true
                        width: 28 * screenScaleFactor
                        text: "+"
                        enabled: root.printerModel != null && (root.printerModel.followerTrueThickness || root.printerModel.followerLineScale < 8.0)
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.setFollowerLineScale(Math.min(8.0, root.printerModel.followerLineScale + 1.0));
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    // The toolhead controls ride the line-thickness
                    // row (the live request: the centred follow's
                    // retirement emptied their own row — the sliders
                    // below close the gap). The jump hides at 100%
                    // and the attach persists the row.
                    UM.CheckBox {
                        objectName: "moonrakerFollowerKeepCentred"
                        visible: jumpButton.visible
                        text: "Keep centred"
                        enabled: progressFace.dotAvailable()
                        checked: root.printerModel != null && root.printerModel.followerKeepCentred === true
                        onToggled: {
                            if (root.printerModel != null)
                                root.printerModel.setFollowerKeepCentred(checked);
                        }
                    }
                    Cura.SecondaryButton {
                        id: jumpButton
                        objectName: "moonrakerFollowerJump"
                        visible: progressFace.viewScale > 1.0 && progressFace.attached
                        text: "Jump to toolhead"
                        enabled: progressFace.dotAvailable()
                        onClicked: progressFace.centreOnToolhead()
                    }
                    Cura.SecondaryButton {
                        id: attachButton
                        objectName: "moonrakerFollowerAttach"
                        fixedWidthMode: true
                        width: 76 * screenScaleFactor
                        text: root.printerModel != null && root.printerModel.followerAttached ? "Detach" : "Attach"
                        // Before the print reaches an indexed layer,
                        // detach starts manual viewing at the first layer.
                        enabled: root.printerModel != null && (root.printerModel.printIndexReady || root.printerModel.plateLayerCount > 0)
                        onClicked: {
                            if (root.printerModel != null) {
                                root.printerModel.setFollowerAttached(!root.printerModel.followerAttached);
                            }
                        }
                    }
                }

                // The layer selection (the 4.6.0 request): the slider
                // seeks the anchor the face draws. A seek from the
                // LIVE layer is itself the detach — the model freezes
                // on the committed layer (the live request: the slider
                // must never sit dead while attached). A seek commits
                // only once the drag quietens: every step rebuilds a
                // layer window and rehydrates it, so a release commits
                // at once and a drag settles first.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("thin_margin").width
                    UM.Label {
                        // The reserved label column: both slider rows
                        // share it, so the tracks line up exactly over
                        // each other (the live request).
                        Layout.preferredWidth: 100 * screenScaleFactor
                        Layout.maximumWidth: 100 * screenScaleFactor
                        text: "Layer"
                        color: UM.Theme.getColor("text_inactive")
                        elide: Text.ElideRight
                    }
                    OutlineSlider {
                        id: layerSlider
                        objectName: "moonrakerFollowerLayerSlider"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(0, (root.printerModel != null ? root.printerModel.plateLayerCount : 0) - 1)
                        stepSize: 1
                        enabled: root.printerModel != null && root.printerModel.plateLayerCount > 0
                        onValueTuning: {
                            // The raw tick rides to the model: the
                            // seek's perceived latency includes the
                            // debounce, so the trace records it. The
                            // signal is the slider's own — it never
                            // fires during teardown.
                            if (root.printerModel != null) {
                                root.printerModel.seekAnchorTicked();
                            }
                            layerSeekTimer.restart();
                        }
                        onValueCommitted: {
                            layerSeekTimer.stop();
                            progressFace.endInteraction();
                            commitLayerSeek();
                        }
                    }
                    UM.Label {
                        objectName: "moonrakerFollowerLayerReadout"
                        Layout.preferredWidth: 64 * screenScaleFactor
                        Layout.maximumWidth: 64 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: layerReadout()
                    }
                }

                // The within-layer progress (the 4.6.0 request): a
                // SCRUBBER, not a display bar — the slider plays the
                // frozen layer through manually (the live request).
                // Attached it mirrors the live split; a scrub is
                // itself the detach (the layer slider's rule).
                RowLayout {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("thin_margin").width
                    UM.Label {
                        // The same reserved column as the layer row:
                        // the two tracks align exactly (the live
                        // request).
                        Layout.preferredWidth: 100 * screenScaleFactor
                        Layout.maximumWidth: 100 * screenScaleFactor
                        text: "Layer progress"
                        color: UM.Theme.getColor("text_inactive")
                        elide: Text.ElideRight
                    }
                    OutlineSlider {
                        id: layerProgressSlider
                        objectName: "moonrakerFollowerLayerProgress"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(0, root.printerModel != null ? root.printerModel.plateLayerMotionCount : 0)
                        // One tenth of a percent, bounded by one motion.
                        stepSize: Math.max(1, to / 10000)
                        enabled: root.printerModel != null && root.printerModel.plateProgressAvailable && root.printerModel.plateLayerMotionCount > 0
                        // The scrub commits on every drag tick — the
                        // fill tracks the thumb at frame rate (the
                        // live request), and the release commits once
                        // more for the final position. A scrub input
                        // also ends any camera interaction outright:
                        // a latched warm raster standing over the
                        // hidden exact scene during scrub repaints is
                        // the wrong picture between frames.
                        onValueTuning: {
                            progressFace.endInteraction();
                            commitProgressSeek();
                        }
                        onValueCommitted: {
                            progressFace.endInteraction();
                            commitProgressSeek();
                        }
                    }
                    UM.Label {
                        objectName: "moonrakerFollowerLayerProgressReadout"
                        // The same reserved width as the layer row's
                        // readout: the tracks stay equal.
                        Layout.preferredWidth: 64 * screenScaleFactor
                        Layout.maximumWidth: 64 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: {
                            // An expression, not a call: the readout
                            // must re-bind on the split and the count
                            // (the live report — a call froze it and
                            // an unset count read NaN%).
                            var total = root.printerModel != null ? root.printerModel.plateLayerMotionCount : 0;
                            var split = root.printerModel != null ? root.printerModel.plateSplit : null;
                            if (total <= 0 || split == null || isNaN(split) || isNaN(total)) {
                                return "—";
                            }
                            var pct = Math.max(0, split) / total * 100;
                            return isNaN(pct) ? "—" : pct.toFixed(2) + "%";
                        }
                    }
                }

                UM.Label {
                    objectName: "moonrakerFollowerSourceStatus"
                    Layout.fillWidth: true
                    visible: root.printerModel != null && root.printerModel.plateSourceStatus !== ""
                    text: root.printerModel != null ? root.printerModel.plateSourceStatus : ""
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("small")
                }
                LoadProgressIndicator {
                    objectName: "moonrakerFollowerSourceProgress"
                    Layout.fillWidth: true
                    visible: busy
                    busy: root.printerModel != null && root.printerModel.plateSourceStatus !== ""
                    progress: root.printerModel != null ? root.printerModel.plateSourceProgress : -1
                    phase: "G-code download"
                }

                Timer {
                    id: layerSeekTimer
                    // 40 ms (was 80/250): current-layer demand now
                    // commits independently of ghosts and can preempt
                    // background preparation. This still coalesces a
                    // drag burst while removing another 40 ms from the
                    // raw-tick -> available path. Explicit commits
                    // remain immediate (the timer is stopped above).
                    interval: 40
                    onTriggered: commitLayerSeek()
                }
                Connections {
                    target: root.printerModel
                    function onPlateProgressChanged() {
                        syncLayerSlider();
                        syncProgressSlider();
                    }
                    function onFollowerViewChanged() {
                        syncLayerSlider();
                    }
                }
            }

            // The rule between the plate and the schedule. It is a
            // hairline, and it takes its width from the schedule
            // column's slack — that column's minimum is zero — so the
            // plate's own width, the layout's invariant, is untouched.
            Rectangle {
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                Layout.minimumWidth: 1
                Layout.maximumWidth: 1
                color: UM.Theme.getColor("border")
            }

            // The pause at the end of a layer (the 4.6.0 request):
            // the popover's OWN slider picks the layer, so the
            // schedule targets the END of the layer it stands on —
            // the card's schedule, the popover's own candidate. Its
            // own column (the live report): stacked under the plate
            // the list squeezed the face and ran onto the card's
            // clipped bottom edge, so the width pays for it instead.
            PauseScheduleView {
                printerModel: root.printerModel
            }
        }
    }
}
