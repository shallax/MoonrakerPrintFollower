import QtQuick 2.15
import UM 1.5 as UM
import "PlatePainter.js" as Painter

// A small independent overlay for the software fallback: one chevron per
// retraction or unretraction the playback has already reached. It reads
// only the event points, never the complete layer's toolpath QVariant,
// so it repaints on its own inputs and nothing else.
Canvas {
    id: root
    objectName: "moonrakerExtruderMarkers"

    // The layer whose events draw, and the camera they draw at. The
    // view rides one var carrier because this canvas' paint reads it
    // (worker paints see var properties fresh, primitives stale).
    property var eventLayer: null
    property var markerView: ({
            scale: 1.0,
            panX: 0.0,
            panY: 0.0,
            plot: null,
            up: false,
            down: false,
            compact: false,
            completed: 0,
            total: 0
        })
    readonly property var points: visible && eventLayer != null && eventLayer.extruderEvents !== undefined ? eventLayer.extruderEvents : []

    onPointsChanged: requestPaint()
    onMarkerViewChanged: requestPaint()
    onVisibleChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d");
        ctx.clearRect(0, 0, width, height);
        if (!visible)
            return;
        Painter.drawExtruderMarkers(ctx, root.points, root.markerView, UM.Theme.getColor("text"));
    }
}
