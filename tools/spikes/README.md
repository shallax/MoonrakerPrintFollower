# Retained GPU follower experiment

This historical prototype has been integrated into the default production
renderer in `plugins/GpuFollower.py`. The production implementation uses the
pixel-width stroke shader in `plugins/GpuStrokeMaterial.py`; this experiment
and its original measurements remain here as investigation records.

`GpuFollowerSpike.py` is an opt-in investigation prototype for Cura 5.13 / Qt 6.6.
It is not registered by the released plugin and is not included in packages.

It subclasses `QQuickItem`, builds `QSGGeometryNode` children with Qt's flat-colour
materials, and lets Qt own their graphics resources. Immutable bed-space edge
arrays are sorted by motion once on a preparation thread. Progress uses a binary
search and a native copy of the printed prefix into its geometry buffer; pan and
zoom change a scene-graph transform. The base and ghost geometry remain retained.
Qt's scene graph handles buffer uploads, clipping, shaders and compositing.

## Live integration used during the investigation

In a development install, register the class as
`qmlRegisterType(GpuFollowerSpike, "MPFGpuSpike", 1, 0, "GpuFollowerSpike")`.
Copy the accompanying `GpuFollowerItem.qml` into the development plugin,
import that module into `PlateProgressFace.qml`, and mount an item beside the
existing exact scene with these inputs:

```qml
GpuFollowerItem {
    id: gpuFollower
    anchors.fill: parent
    smoothToolpaths: root.smoothToolpaths
    layers: root.progress && root.progress.layers ? root.progress.layers : ({})
    settings: ({
        plot: mapping._plot,
        split: root.progress ? root.progress.split : null,
        scale: root.displayScale,
        panX: root.displayPanX,
        panY: root.displayPanY,
        lineWidth: root.toolpathWidthPx(),
        showBase: root.showBase,
        showPrevious: root.showPrevious,
        showNext: root.showNext,
        showTravels: root.showTravels,
        travelRatio: root.travelVisualRatio
    })
}
```

The development switch hides the old exact scene, preparing cover and warm
navigation scene, bypasses the old progress Canvas paint scheduler, and uses
`gpuFollower.ready` for gesture completion. The existing grid, gestures,
toolhead dot, layer selection and print controls remain in use. Both popover and
minimap were exercised in the installed Cura 5.13 application with an indexed
active print, including scrubbing to a distant layer.

## Measurements and remaining work

With the captured dense layer, 200 boundary changes on native Windows OpenGL / Qt
6.6 measured a median scene-node update of 0.193 ms, p95 0.275 ms, max 0.337 ms.
Request-to-frame-swap median was 6.325 ms, p95 7.352 ms. This isolated test does
not establish a whole-application FPS improvement. Live camera timing remained
inconsistent over the configured HTTPS path even with the old Canvas disabled.

This prototype uses driver line primitives. Optional antialiasing uses a
4-sample Qt Quick item layer on the GPU, supported by Cura 5.13 / Qt 6.6.
The development install sets `gpuRendering: true` on each face; this exposes
the Antialiasing checkbox, backed by the model's persistent global
`followerAntialiasing` preference. It defaults to false and controls both
minimap and popover. Joins, caps, subpixel widths and high-DPI coverage do not
yet match the released renderer. It can show
an empty target while a new layer prepares, rather than the existing loading
composition. It also reads `PlateLayer._payload` as a temporary development seam.
Before becoming a released renderer it needs an explicit immutable geometry
capability, bounded shared preparation/cache ownership, cancellation, exact
presentation receipts, native worker suppression, image parity tests, and
resource-lifetime tests across scene-graph invalidation and printer switches.

With the same dense-layer benchmark, crisp rendering measured node-update
median 0.211 ms, p95 0.282 ms; request-to-swap median 6.834 ms, p95 8.065 ms.
Optional 4-sample rendering measured node-update median 0.217 ms, p95
0.281 ms; request-to-swap median 16.201 ms, p95 16.639 ms. The extra
offscreen/resolve pass increases presentation latency on this GPU; it stays
below the 33 ms benchmark update period. These are isolated measurements,
not a claim of live Cura FPS parity. Keep this mode user selected.
