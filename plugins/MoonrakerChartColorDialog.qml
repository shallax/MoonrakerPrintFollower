import QtQuick 2.15
import QtQuick.Dialogs

// The chart's colour picker. It is its own document because the dialog
// implementation is a platform module (QtQuick.Dialogs): the monitor
// creates it on the first click, so building the monitor never waits on
// that module being usable on the host. The monitor owns the behaviour —
// it sets the title and connects accepted to the sensor-colour apply —
// so this file stays the plain dialog.
ColorDialog {}
