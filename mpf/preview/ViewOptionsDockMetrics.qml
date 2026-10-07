import QtQuick 2.15

// The visible dock and Cura's extension row must reserve exactly the same
// scaled width and gap so neighbouring plugin controls cannot overlap it.
QtObject {
    readonly property real width: 280 * screenScaleFactor
    readonly property real gap: 12 * screenScaleFactor
}
