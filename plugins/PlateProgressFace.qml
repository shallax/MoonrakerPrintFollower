import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The plate map's progress face (4.6.0): the OctoApp-style
// previous/current/next layer view. The current layer draws as a
// grey base with the printed portion coloured in at poll cadence
// (the walked ruling); the ghost layers and the base are legend
// toggles, and the travel boundaries carry the start/end glyphs.
Item {
    id: root
    objectName: "moonrakerPlateProgressFace"
    clip: true

    property var printerModel: null
    property var progress: null   // the model's plateProgress payload
    property var dot: null
    property bool compact: false
    property bool showPrevious: true
    property bool showNext: true
    property bool showBase: true

    function classColour(name) {
        switch (name) {
        case "WALL-OUTER":
            return MoonrakerTheme.plateClassWallOuter;
        case "WALL-INNER":
            return MoonrakerTheme.plateClassWallInner;
        case "SKIN":
            return MoonrakerTheme.plateClassSkin;
        case "FILL":
            return MoonrakerTheme.plateClassFill;
        case "SUPPORT":
            return MoonrakerTheme.plateClassSupport;
        case "SKIRT":
            return MoonrakerTheme.plateClassSkirt;
        default:
            return MoonrakerTheme.seriesDefault;
        }
    }

    function available() {
        return root.progress != null && root.progress.available === true;
    }

    onProgressChanged: layerCanvas.requestPaint()
    onShowPreviousChanged: layerCanvas.requestPaint()
    onShowNextChanged: layerCanvas.requestPaint()
    onShowBaseChanged: layerCanvas.requestPaint()
    onDotChanged: layerCanvas.requestPaint()

    // The shared base carries the bed mapping — the face's canvas
    // draws the layers through ITS transform, so the one-mapping rule
    // holds across both faces.
    PlateCanvas {
        id: mapping
        anchors.fill: parent
        printerModel: root.printerModel
        plate: null
        dot: null
    }

    Canvas {
        id: layerCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            if (!root.available() || mapping._plot == null) {
                return;
            }
            var progress = root.progress;
            var layers = progress.layers;
            // The ghost layers: full paths at low alpha — the stack
            // reads through (the walked transparency ruling).
            if (root.showPrevious && layers.prev != null) {
                _drawLayer(ctx, layers.prev, 0.30, -1, false);
            }
            if (root.showNext && layers.next != null) {
                _drawLayer(ctx, layers.next, 0.30, -1, false);
            }
            var current = layers.current;
            if (current == null) {
                return;
            }
            // The grey base: the whole layer, one honest colour.
            if (root.showBase) {
                _drawLayer(ctx, current, 0.55, -1, true);
            }
            // The printed portion, coloured in per feature class up
            // to the motion split (the H3 floor).
            _drawLayer(ctx, current, 1.0, progress.split, false);
            // The travel boundaries: a glyph at every start and end.
            _drawGlyphs(ctx, current.travelStarts, true);
            _drawGlyphs(ctx, current.travelEnds, false);
        }
    }

    function _drawLayer(ctx, layer, alpha, split, base) {
        for (var name in layer.classes) {
            var points = layer.classes[name];
            if (points.length < 2) {
                continue;
            }
            ctx.strokeStyle = base ? MoonrakerTheme.seriesDefault : root.classColour(name);
            ctx.globalAlpha = alpha;
            ctx.lineWidth = 1;
            ctx.beginPath();
            var started = false;
            for (var i = 0; i < points.length; ++i) {
                if (split >= 0 && points[i][2] > split) {
                    break;
                }
                var scene = mapping.plateToScene(points[i][0], points[i][1]);
                if (scene == null) {
                    continue;
                }
                if (!started) {
                    ctx.moveTo(scene.x, scene.y);
                    started = true;
                } else {
                    ctx.lineTo(scene.x, scene.y);
                }
            }
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }
    }

    function _drawGlyphs(ctx, marks, start) {
        for (var i = 0; i < marks.length; ++i) {
            var scene = mapping.plateToScene(marks[i][0], marks[i][1]);
            if (scene == null) {
                continue;
            }
            ctx.fillStyle = UM.Theme.getColor("text");
            ctx.beginPath();
            if (start) {
                ctx.moveTo(scene.x, scene.y - 3.5 * screenScaleFactor);
                ctx.lineTo(scene.x - 3 * screenScaleFactor, scene.y + 2.5 * screenScaleFactor);
                ctx.lineTo(scene.x + 3 * screenScaleFactor, scene.y + 2.5 * screenScaleFactor);
            } else {
                ctx.moveTo(scene.x, scene.y + 3.5 * screenScaleFactor);
                ctx.lineTo(scene.x - 3 * screenScaleFactor, scene.y - 2.5 * screenScaleFactor);
                ctx.lineTo(scene.x + 3 * screenScaleFactor, scene.y - 2.5 * screenScaleFactor);
            }
            ctx.closePath();
            ctx.fill();
        }
    }

    // The toolhead dot: the same scene-graph marker as the exclude
    // face, walking the layer path (the H3 alignment — the dot and
    // the fill derive from the same index).
    Rectangle {
        id: toolheadDot
        width: 7 * screenScaleFactor
        height: width
        radius: width / 2
        color: MoonrakerTheme.plateDot
        border.color: UM.Theme.getColor("main_background")
        border.width: 2
        visible: mapping._plot != null && root.dot != null && root.dot.valid === true
        x: visible ? mapping.plateToScene(root.dot.x, root.dot.y).x - width / 2 : 0
        y: visible ? mapping.plateToScene(root.dot.x, root.dot.y).y - height / 2 : 0
    }

    // The unavailable state (the live ruling): the shared download
    // action — glyph, bar, percentage and stage — so the follower
    // never reads as a dead end. The mini suppresses the action (its
    // section hosts the placeholder).
    ColumnLayout {
        anchors.centerIn: parent
        width: parent.width * 0.8
        visible: !root.available() && !root.compact
        spacing: UM.Theme.getSize("narrow_margin").height

        PlateDownloadAction {
            Layout.fillWidth: true
            printerModel: root.printerModel
            idleInstruction: root.progress != null && root.progress.reason !== "" ? root.progress.reason : "No index yet — the download button builds one without loading the preview."
        }
    }

    UM.Label {
        anchors.centerIn: parent
        visible: !root.available() && root.compact
        text: "The follower builds its index from the print — open the pop-over."
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("small")
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        width: parent.width * 0.8
    }
}
