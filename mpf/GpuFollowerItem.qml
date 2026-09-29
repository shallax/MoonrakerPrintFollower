import QtQuick 2.15
import MoonrakerPrintFollower 1.0

// Edge smoothing happens in the stroke shader. Translucent layer consumers
// may isolate opacity in a GPU texture while sharing the prepared buffers.
GpuFollower {
    property bool smoothToolpaths: false
}
