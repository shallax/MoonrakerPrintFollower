pragma Singleton
import QtQuick 2.15

// The plugin's own colour constants (the live ruling: no magic hexes
// in the documents — either Cura's theme colours or these). The
// qmldir beside this file registers the singleton, so sibling
// documents import the DIRECTORY: `import "theme"` and then read
// MoonrakerTheme.axisX etc. (File imports with a .qml suffix do not
// resolve on the shipped engine — probe-proven.)
QtObject {
    // ── identity colours shared across surfaces ─────────────────
    // The axis identity colours: X red, Y green, Z blue — used by
    // the toolhead readouts and the collapsed position cells.
    readonly property color axisX: "#ef5350"
    readonly property color axisY: "#66bb6a"
    readonly property color axisZ: "#42a5f5"
    // The neon orange: the probed-mesh bounds outline and clamped
    // crosshair, and the next scheduled pause's fill.
    readonly property color neonOrange: "#FF5A00"
    // The warning amber: not-homed states and detected-filament
    // highlights.
    readonly property color warningOrange: "#fb8c00"
    // The danger red: error borders, the emergency fill, bulk delete.
    readonly property color dangerRed: "#d32f2f"
    // The error red: console errors, the camera's record dot, the
    // disconnected connection dot.
    readonly property color errorRed: "#f85149"
    // The success green: the connected dot and console successes.
    readonly property color successGreen: "#3fb950"

    // ── the console's palette ───────────────────────────────────
    readonly property color consoleBackground: "#161b22"
    readonly property color consoleBackgroundOffline: "#2d333b"
    readonly property color consoleText: "#d9dde3"
    readonly property color consoleTextMuted: "#7d8590"
    readonly property color consoleMuted: "#8b949e"
    readonly property color consoleCommand: "#9da7b3"
    readonly property color consoleWarn: "#d29922"
    readonly property color consoleInfo: "#58a6ff"
    readonly property color consoleSuccess: "#57ab5a"
    readonly property color consolePromptError: "#e05650"
    readonly property color consoleHistoryError: "#d0635e"
    readonly property color consoleHistorySuccess: "#4f9a5d"
    readonly property color consoleBell: "#e53935"

    // ── the camera pane ─────────────────────────────────────────
    readonly property color cameraVeil: "#c0202428"
    // The translucent pill is deliberate: it lets the live frame
    // read through its edges. The census exempts camera-overlay
    // text — its ground is the feed's arbitrary content, and the
    // pill's own fill carries the pair's contrast.
    readonly property color cameraLivePill: "#99000000"
    readonly property color cameraLiveText: "#ffffff"

    // ── the file manager ────────────────────────────────────────
    readonly property color scrim: "#66000000"
    readonly property color dangerHover: "#26d32f2f"

    // ── the temperature chart ───────────────────────────────────
    readonly property color seriesDefault: "#888888"

    // ── the bed-mesh threshold bands ────────────────────────────
    readonly property color bandBlue: "#1a47f2"
    readonly property color bandCyan: "#00b8ff"
    readonly property color bandGreen: "#33db61"
    readonly property color bandYellow: "#ffd11f"
    readonly property color bandRed: "#eb291f"
    readonly property color outOfWindowGrey: "#8a8f98"

    // ── the filament section ────────────────────────────────────
    readonly property color filamentDetected: "#43a047"

    // ── the plate map (4.6.0) ────────────────────────────────────
    // The current object's stroke: a NEW measured token — every
    // existing green fails the 3:1 floor on the light ground
    // (measured: 3.45 light / 4.78 dark).
    readonly property color plateCurrent: "#2f9e44"
    // The follower's feature-class table: the chart palette's own
    // both-theme measured tokens, one fixed mapping — travel and
    // unlabelled motions stay the honest unknown grey.
    readonly property color plateClassWallOuter: "#d32f2f"
    readonly property color plateClassWallInner: "#388e3c"
    readonly property color plateClassSkin: "#e65100"
    readonly property color plateClassFill: "#1976d2"
    readonly property color plateClassSupport: "#00838f"
    readonly property color plateClassSkirt: "#00897b"
}
