.pragma library
// One Qt-free owner for the camera's arithmetic: the bed transform, the
// pan clamps, the zoom's focal/eased terms, the scope's scale and the
// physical stroke width. All functions are pure — the face and the
// scope own the camera STATE and every Item that reads it; nothing here
// touches a QML object.
//
// The cap the zoom scale and the scope's graduations share.
var MAX_SCALE = 20.0;

// The single printer-to-widget transform. PlateCanvas.plateToScene and
// every per-point walk that is not the hot stroke loop resolve here, so
// one bed mapping serves the dot, the glyphs, the travels and the hit
// test. PlatePainter.drawLayer keeps the same arithmetic INLINED on
// purpose: a call per vertex over hundreds of thousands of points was
// the follower's dominant cost.
function toScene(plot, x, y) {
    if (plot == null)
        return null;
    return {
        x: plot.bed.offsetX + (x - plot.bed.bedXMin) * plot.sx,
        y: plot.bed.offsetY + (plot.bed.bedYMax - y) * plot.sy
    };
}

// The plotted bed's rectangle in scene pixels at `scale`.
function bedBox(bed, scale) {
    var left = bed.offsetX * scale;
    var top = bed.offsetY * scale;
    return {left: left, right: left + bed.plotWidth * scale,
            top: top, bottom: top + bed.plotHeight * scale};
}

// The zoom/pan soft clamp: the camera moves freely while any of the bed
// shows, but the bed may never leave the viewport wholly — `margin`
// pixels stay visible on every side (the standing ruling: a fully
// off-bed view is never useful, and the corner camera must not snap the
// bed to the viewport's edge). An axis whose interval is empty keeps
// the requested value.
function softClampPan(plot, scale, width, height, margin, x, y) {
    if (plot == null)
        return {x: x, y: y};
    var box = bedBox(plot.bed, scale);
    var lowX = margin - box.right;
    var highX = width - margin - box.left;
    var lowY = margin - box.bottom;
    var highY = height - margin - box.top;
    return {
        x: lowX > highX ? x : Math.min(highX, Math.max(lowX, x)),
        y: lowY > highY ? y : Math.min(highY, Math.max(lowY, y))
    };
}

// The pan that puts one bed point at the viewport's centre, unclamped:
// the plate may show empty space beyond the bed edge (the jump must
// always put the toolhead at the middle, never pin the bed to the
// view's edge). Null when there is no plot to transform through.
function centrePan(plot, plateX, plateY, scale, width, height) {
    var scene = toScene(plot, plateX, plateY);
    if (scene == null)
        return null;
    return {x: width / 2 - scene.x * scale, y: height / 2 - scene.y * scale};
}

// The point under `pointer` stays put across a scale change: the pan
// that holds it, read from the transform the camera is actually at.
function focalPan(pointer, pan, fromScale, toScale) {
    return pointer - (pointer - pan) / fromScale * toScale;
}

// The bed-fit pixel under `pointer` at the transform named by `pan` and
// `scale`: the glide derives its pan from this every tick, so scale and
// pan converge as one camera transform and the focal point never
// wanders.
function focalBed(pointer, pan, scale) {
    return (pointer - pan) / scale;
}

// The eased pan for one axis during a glide: the focal bed point at the
// current scale, plus the drags accumulated since the last wheel.
function glidePan(anchor, bedPoint, scale, dragDelta) {
    return anchor - bedPoint * scale + dragDelta;
}

// One exponential ease-out tick toward the CURRENT target, with the
// exact snap at the end so the glide terminates rather than creeping.
function easeScale(from, target, step) {
    var next = from + (target - from) * step;
    return Math.abs(target - next) < 0.005 ? target : next;
}

// A wheel notch or a touchpad's pixel distance as a scale. A mouse
// wheel sends discrete angle notches, while a touchpad sends many small
// pixel deltas during one gesture: treating every touchpad event as a
// whole notch raced from bed fit to the limit, so the notch keeps its
// 25% step and pixel movement scales continuously.
function wheelScale(pixels, angle, scale, max) {
    var factor = pixels !== 0 ? Math.pow(1.25, pixels / 180.0) : (angle > 0 ? 1.25 : 0.8);
    return Math.min(max, Math.max(1.0, scale * factor));
}

// The scope's two directions: a track position as a scale, and a scale
// as its log fraction of the bar. The bottom is the 100% fit.
function scaleFromTrack(y, height, max) {
    var fraction = Math.min(1.0, Math.max(0.0, (height - y) / height));
    return Math.pow(max, fraction);
}
function trackFraction(scale, max) {
    return Math.log(scale) / Math.log(max);
}

// The scope's graduations: every `step` of scale from the 100% fit to
// the cap, log-positioned along the bar by trackFraction.
function graduations(max, step) {
    var steps = [];
    for (var v = 1.0; v <= max + 1e-9; v += step)
        steps.push(v);
    return steps;
}

// The ONE physical stroke-width calculation, shared by the ghost,
// pending, printed and travel painters: nominal bed mm through the live
// plot's px-per-mm and the view zoom, times the user's line scale. The
// plot is aspect-preserved so sx equals sy (the mapping's own
// contract). Screen-space annotations — the toolhead dot, the travel
// glyphs, the grid — never read this.
//
// The same geometry-width policy the native renderer's pen applies (the
// parity contract): the nominal width scaled to the presentation, then
// the same device-coverage floor — min(2/dpr, 1) logical px — so a
// sub-floor stroke presents at the same full-intensity footprint on
// both renderers. Without the floor the native raster's backed
// downscale faded thin strokes while the canvas stroked them raw, and
// the two halves of ONE layer read as different inks.
function toolpathWidth(p) {
    if (p.pixelLineWidth)
        return Math.min(8, Math.max(1, Math.round(p.lineScale)));
    if (p.plot == null)
        return 0;
    var width = p.nominalMm * Math.abs(p.plot.sx) * Math.abs(p.view.scale)
        * p.view.lineScale * (p.view.compact ? p.compactBoost : 1.0);
    var dpr = p.view.dpr !== undefined ? Math.max(1.0, p.view.dpr) : 1.0;
    return Math.max(width, Math.min(2.0 / dpr, 1.0));
}

// Travels draw thinner than extrusion ink: a VISUAL ratio over the same
// physical basis, never its own pixel count.
function travelWidth(toolpath, ratio, trueThickness) {
    return trueThickness ? 1.0 : toolpath * ratio;
}
