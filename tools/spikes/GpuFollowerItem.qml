import QtQuick 2.15
import MPFGpuSpike 1.0

// Optional GPU multisampling. Disabled means the original crisp line
// primitives, with no extra offscreen texture or resolve pass.
GpuFollowerSpike {
    property bool smoothToolpaths: false
    layer.enabled: smoothToolpaths
    layer.samples: 4
    layer.smooth: true
}
