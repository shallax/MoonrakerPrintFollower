import QtQuick 2.15
import MoonrakerPrintFollower 1.0

// Edge smoothing happens in the stroke shader, without an offscreen layer.
GpuFollower {
    property bool smoothToolpaths: false
}
