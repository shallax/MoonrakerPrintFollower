.pragma library
.import "PlateViewPolicy.js" as ViewPolicy
.import "PreviewColours.js" as PreviewColours
// The plate's Canvas drawing, with no QML object anywhere: every painter
// takes the context, the payload, the boundaries and one explicit style
// record. The face owns the canvases, decides WHICH picture to draw and
// builds the style; nothing here reads a property or an id.
//
// The style record:
//   plot           the bed mapping the strokes land through
//   scale/panX/panY the camera the pixels are baked at
//   lineWidth      the physical extrusion stroke, already scaled
//   travelWidth    the travel stroke, already scaled
//   trueThickness  per-motion widths from the payload instead
//   colourScheme   the user's colour mode and class palette
//   baseColour     the grey the unprinted base draws in
//   classColour    the one-argument class-to-colour resolver, which is a
//                  theme read the face owns (a JS library cannot import
//                  a QML singleton)
//
// The painter's ONE rule, shared by the strokes and the glyphs: the
// payload's vertices are the G-code's own motion edges (edge i runs from
// points[i - 1] to points[i] and belongs to the motion points[i][2]
// names, and a segment's indices never decrease), the split is a COUNT
// of printed motions, and an edge is drawn exactly when its own motion
// is below it. Both halves of the paint read it the same way: the full
// repaint from -1, the accumulated delta on top of the last count.
function firstEdge(points, from) {
    // The motions within a segment never decrease: the first edge at or
    // after `from` is a binary search, never a linear walk — the tail
    // after a native prefix would otherwise re-scan the whole printed
    // portion on every paint.
    if (from <= 0 || points.length < 2) {
        return 1;
    }
    var low = 1;
    var high = points.length - 1;
    while (low < high) {
        var mid = (low + high) >> 1;
        if (points[mid][2] < from) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    // Every motion below `from`: the first edge past the segment's end —
    // exactly the linear walk's off-the-end result, which the callers
    // read as "nothing to draw".
    if (points[low][2] < from) {
        return points.length;
    }
    return low;
}

// The first run of a class that can still meet `from`: the runs ascend
// in motion order, so a run whose LAST motion is below the boundary
// holds nothing the walk could draw, and the runs below it can be
// skipped unread. Without the bound a delta paint reads EVERY run of the
// class — on a layer whose runs are short, that per-run read is the
// paint's cost, not the stroke it draws.
function firstRunAt(segments, from) {
    if (from <= 0) {
        // The reset path repaints from the layer's start.
        return 0;
    }
    var low = 0;
    var high = segments.length;
    while (low < high) {
        var mid = (low + high) >> 1;
        var points = segments[mid];
        if (points.length > 0 && points[points.length - 1][2] >= from) {
            high = mid;
        } else {
            low = mid + 1;
        }
    }
    return low;
}

function edgePrinted(points, i, split) {
    return i < points.length && (split < 0 || points[i][2] < split);
}

function drawLayer(ctx, layer, alpha, split, base, from, style) {
    // The transform inlined: hundreds of thousands of ViewPolicy.toScene
    // calls per paint were the follower's cost. This is the one place
    // that repeats that arithmetic, and only for that reason.
    var plot = style.plot;
    var sx = plot.sx;
    var sy = plot.sy;
    var offsetX = plot.bed.offsetX;
    var offsetY = plot.bed.offsetY;
    var bedXMin = plot.bed.bedXMin;
    var bedYMax = plot.bed.bedYMax;
    var scale = style.scale;
    var panX = style.panX;
    var panY = style.panY;
    // The physical stroke: one width for every channel (the
    // ghost/pending/printed parity rule), subpixel at 100%.
    ctx.lineWidth = style.lineWidth;
    // Round joins: the default miter spikes at acute corners with a
    // length that grows with the stroke width — thick lines sprouted
    // sharp edges at every text corner (the live report).
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (var name in layer.classes) {
        var segments = layer.classes[name];
        ctx.strokeStyle = base ? style.baseColour : style.classColour(name);
        ctx.globalAlpha = alpha;
        // ONE path per class, stroked ONCE. A beginPath/stroke per
        // SEGMENT was the follower's dominant cost: every contiguous run
        // became its own software stroke, hundreds or thousands per
        // repaint, each 100-500 ms on the live machine. The fresh path
        // was never what kept a stroke from bridging a travel — a stroke
        // never joins subpaths, and moveTo opens one, so batching is
        // pixel-identical for the opaque classes. For the translucent
        // base (alpha 0.55) it removes the double-compositing where two
        // runs overlap, which is the alpha-accumulation this library
        // documents elsewhere rather than a look worth keeping.
        ctx.beginPath();
        // The walk starts at the first run that can still draw, so the
        // printed runs below the boundary are never read.
        var first_run = firstRunAt(segments, from);
        for (var s = first_run; s < segments.length; ++s) {
            var points = segments[s];
            // Every segment is at least one EDGE — two vertices — so a
            // one-motion extrusion draws its true line, never a dot: the
            // payload carries the move's start position.
            if (points.length < 2) {
                continue;
            }
            var i = firstEdge(points, from);
            if (style.trueThickness || (!base && style.colourScheme.mode !== 1)) {
                var widths = layer.widths || [];
                while (edgePrinted(points, i, split)) {
                    ctx.stroke();
                    ctx.beginPath();
                    var mm = widths[Math.floor(points[i][2])];
                    if (style.trueThickness)
                        ctx.lineWidth = (mm > 0 ? mm : 0.4) * Math.abs(sx * scale);
                    if (!base)
                        ctx.strokeStyle = PreviewColours.colour(layer, name, Math.floor(points[i][2]), style.colourScheme);
                    ctx.moveTo((offsetX + (points[i - 1][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i - 1][1]) * sy) * scale + panY);
                    ctx.lineTo((offsetX + (points[i][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i][1]) * sy) * scale + panY);
                    ctx.stroke();
                    ctx.beginPath();
                    ++i;
                }
                continue;
            }
            if (!edgePrinted(points, i, split)) {
                continue;
            }
            // Opened at the first edge's OWN start vertex: the stroke
            // never bridges a travel, a feature change, or the boundary
            // the last poll painted. No pan term when the style carries
            // none: the canvas item's translation carries the view.
            ctx.moveTo((offsetX + (points[i - 1][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i - 1][1]) * sy) * scale + panY);
            while (edgePrinted(points, i, split)) {
                ctx.lineTo((offsetX + (points[i][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i][1]) * sy) * scale + panY);
                ++i;
            }
        }
        ctx.stroke();
        ctx.globalAlpha = 1.0;
    }
}

function drawTravelClasses(ctx, layer, split, from, style) {
    if (layer.travelClasses !== undefined) {
        for (var name in layer.travelClasses)
            drawTravels(ctx, layer.travelClasses[name], split, from, name, style);
    } else {
        drawTravels(ctx, layer.travels, split, from, "TRAVEL", style);
    }
}

function drawTravels(ctx, segments, split, from, name, style) {
    if (segments == null) {
        return;
    }
    var plot = style.plot;
    var scale = style.scale;
    var panX = style.panX;
    var panY = style.panY;
    ctx.strokeStyle = style.classColour(name || "TRAVEL");
    ctx.globalAlpha = 0.8;
    ctx.lineWidth = style.travelWidth;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (var s = 0; s < segments.length; ++s) {
        var points = segments[s];
        if (points.length < 2) {
            continue;
        }
        var i = firstEdge(points, from);
        if (!edgePrinted(points, i, split)) {
            continue;
        }
        ctx.beginPath();
        var scene = ViewPolicy.toScene(plot, points[i - 1][0], points[i - 1][1]);
        if (scene == null) {
            continue;
        }
        ctx.moveTo(scene.x * scale + panX, scene.y * scale + panY);
        while (edgePrinted(points, i, split)) {
            scene = ViewPolicy.toScene(plot, points[i][0], points[i][1]);
            if (scene != null) {
                ctx.lineTo(scene.x * scale + panX, scene.y * scale + panY);
            }
            ++i;
        }
        ctx.stroke();
    }
    ctx.globalAlpha = 1.0;
}

// The retraction/unretraction glyphs: a chevron per event, pointing up
// for a retraction and down for its recovery. Only the events the
// playback has already reached draw, and events landing in the same
// screen cell collapse to one glyph — a dense layer would otherwise
// stroke thousands of overlapping chevrons. `view` carries scale, panX,
// panY, plot, up, down, compact, completed and total; `ink` is the
// glyph colour the caller reads from the theme.
function drawExtruderMarkers(ctx, points, view, ink) {
    var plot = view.plot;
    if (plot == null)
        return;
    var cells = {};
    var scale = view.scale;
    var half = (view.compact ? 3 : Math.min(8, Math.max(4, 4 * Math.sqrt(Math.max(1, scale))))) / 2;
    var outline = [[0, -half], [half, 0], [half * .35, 0], [half * .35, half], [-half * .35, half], [-half * .35, 0], [-half, 0], [0, -half]];
    ctx.strokeStyle = ink;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (var i = 0; i < points.length; ++i) {
        var point = points[i];
        var up = point[3];
        if (!(up ? view.up : view.down))
            continue;
        if (point[2] >= view.completed && !(view.total > 0 && view.completed >= view.total && point[2] === view.total))
            continue;
        var cell = up + ":" + Math.floor(point[0] * Math.abs(plot.sx * scale) / 12) + ":" + Math.floor(point[1] * Math.abs(plot.sy * scale) / 12);
        if (cells[cell])
            continue;
        cells[cell] = true;
        var scene = ViewPolicy.toScene(plot, point[0], point[1]);
        if (scene == null)
            continue;
        var x = scene.x * scale + view.panX;
        var y = scene.y * scale + view.panY;
        var direction = up ? 1 : -1;
        ctx.moveTo(x + outline[0][0], y + direction * outline[0][1]);
        for (var j = 1; j < outline.length; ++j)
            ctx.lineTo(x + outline[j][0], y + direction * outline[j][1]);
    }
    ctx.stroke();
}
