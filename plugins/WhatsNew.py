"""The what's-new content and its once-per-version marker logic.

The overlay shows once per installation, per version: the persisted
state carries the last SEEN version, and a mismatch with the content
list's head (the shipped version) shows the popup.

The content is hand-curated for the popup — every release carries a
headline (what the version IS, in one or two sentences) and a short,
user-facing bullet list. It deliberately reads like release notes
for a user, not the maintainer-level detail CHANGELOG.md keeps; the
release checklist updates this list alongside the version bump, and
the latest-version pin in tests/test_whatsnew.py fails a release
that bumps the package version without a matching entry.

Shipped release notes are FROZEN: a later release adds its own entry
and never edits the older ones — the frozen-history pin in
tests/test_whatsnew.py enforces it mechanically.

Qt-free on purpose: the pure logic is unit-testable outside the
container.
"""
from __future__ import annotations

from typing import List, Tuple

# One entry per release, latest first.
WHATS_NEW: Tuple[dict, ...] = (
    {
        "version": "4.6.0",
        "headline": "Version 4.6.0 draws your build plate in the Monitor — "
            "every object where it actually sits — so a failed print points "
            "at a place on the plate instead of a name in a list.",
        "items": (
            "The plate map is here: the objects the slicer defined, drawn "
            "where they are, with the object printing right now "
            "highlighted, excluded objects red and the ones already printed "
            "green. It sits in the Information pane as a small map; click it "
            "to open the picker full size.",
            "Excluding is a gesture now: triple-click an object on the map "
            "to exclude it, and triple-click an excluded object to bring it "
            "back. There is no confirmation dialog — the pop-over counts "
            "your clicks and the status line under the map reports the "
            "outcome, a refusal included.",
            "The old list of object names is gone: the map is the control. "
            "Hover any object to read its name and state.",
            "A new Print Follower section draws the print itself on the "
            "plate: the previous and next layers ghosted, the current layer "
            "with its printed part filling in as the print runs, and the "
            "toolhead where it is. Walls, skin, infill and the other "
            "features have their own colours with a key underneath, and "
            "travel moves stay hidden until you switch them on.",
            "The follower is a tool, not a picture: zoom and pan the plate, "
            "drag the Layer slider to any layer (that alone detaches the "
            "follow), scrub through the current layer, set the line "
            "thickness, jump the view onto the toolhead and keep it "
            "centred — and detach or attach whenever you like.",
            "The temperature chart samples on its own steady one-second "
            "clock now. A fast auxiliary update setting used to cut the "
            "advertised 30-minute window down to minutes.",
            "A print you have already looked at opens quickly the next "
            "time: the prepared geometry is kept per printer and per "
            "print, so the second visit skips the whole preparation walk. "
            "The Diagnostics tab carries the size limit for that store "
            "(512 MiB per printer by default), beside the clear button, "
            "which still clears every printer.",
            "Fixes: quick X and Y jog taps can no longer overshoot their "
            "limits, the settings sliders grab from either side of the "
            "handle and keep the keyboard while a save applies.",
        ),
    },
    {
        "version": "4.5.0",
        "headline": "Version 4.5.0 moves the plugin's settings into its own "
            "folder, so your configuration and console history carry over "
            "cleanly and Cura's configuration file stays its own.",
        "items": (
            "All of the plugin's saved data now lives in one "
            "MoonrakerPrintFollower folder beside Cura's settings — "
            "your printers, cameras and panel layout move across "
            "automatically on the first start, with a backup taken "
            "first.",
            "The Status pane's Position row reads in the axis colours, "
            "matching the collapsed readout and the Toolhead.",
            "Dark mode: the emergency-stop label is readable on the "
            "button's dark ground.",
            "The webcam now appears in milliseconds when you open "
            "the Monitor — the startup no longer restarts its own "
            "connection or applies the stream twice.",
            "The webcam stream now plays through the plugin's own "
            "engine: a healthy stream stays connected for as long "
            "as it runs instead of restarting every few seconds on "
            "large frames, playback paces smoothly with the newest "
            "frame winning, and re-announcing the same camera no "
            "longer interrupts it.",
            "As the Monitor window narrows, the Information, Printer "
            "status and Printer controls panes fold away on their own "
            "so the webcam keeps its room — widen the window and "
            "they come back. The webcam never collapses.",
            "The temperature chart stays light: a full 30 minutes of "
            "history no longer weighs on the Monitor page, the "
            "pop-over chart paints in the background, and hovering "
            "the chart no longer redraws it.",
            "The Monitor page publishes once per heartbeat, reads "
            "nothing from disk while it runs, and batches the "
            "console's writes — the page stays responsive on a "
            "chatty printer.",
            "Cura 5.7 or newer (SDK 8.7+) is now the floor — every "
            "version in between verifies end to end, with 5.11's "
            "preview limitation documented in the README.",
        ),
    },
    {
        "version": "4.4.0",
        "headline": "Version 4.4.0 is the configurable-sections release: every "
            "Monitor pane orders and hides its own sections, the collapsed "
            "panes gain live readouts, and the next scheduled pause is "
            "visible ahead of time.",
        "items": (
            "Each Monitor pane's sections can be reordered and hidden "
            "independently from a configure pop-over, and reset to "
            "defaults without touching the other panes.",
            "The collapsed panes now carry live readouts — temperatures, "
            "ETAs, axis positions, the layer count, and the flow rate — "
            "each one hidden until its value arrives, arranged so the "
            "strip clips instead of overflowing.",
            "The print progress readouts in the Print-job section and the "
            "status strip stack the overall and current-layer fills, with "
            "an orange third fill marking the next scheduled pause (baked "
            "or manual) when one lies ahead.",
            "A Next pause row under Finish gives the countdown and the "
            "wall-clock deadline for the next scheduled pause.",
            "The Preview card's strip gains the current layer and height, "
            "and the card hides the pause controls until a toolpath is "
            "available.",
            "The G-code index is built in a single pass with real byte-"
            "offset progress, so the indexing bar shows an actual "
            "percentage.",
            "Pauses already baked into the G-code are now found when "
            "the file loads, so they appear in the pause list and the "
            "next-pause bar like manually scheduled ones.",
        ),
    },
    {
        "version": "4.3.0",
        "headline": "Version 4.3.0 is the QML componentisation release: the "
            "Monitor page is rebuilt out of small self-contained sections, "
            "and the Preview card gains a status strip.",
        "items": (
            "The Monitor page is now built from self-contained sections — "
            "every collapsible section is its own component, so the page "
            "stays readable and each part can change on its own.",
            "The Preview card gains a status strip under the title: Pause "
            "and Resume, the hotend and bed temperatures, and the remaining "
            "time — and when a pause is not available the strip says why.",
            "Pause and Resume answer to the same permission policy as the "
            "rest of the Monitor: busy, disconnected and unknown states are "
            "all named.",
            "Clicking a slider's track now moves the handle and applies the "
            "value — a track click used to move the handle while "
            "discarding the request.",
            "The print-job caption names Disconnected, Printer state "
            "unknown and Locked — never a lying Idle.",
            "Section expansion persists through the same state file "
            "the rest of the plugin uses.",
            "The test harness records what each step looked at and where "
            "it was, and draws the highlight onto the captured screenshots.",
            "Cura 5.11 or newer (SDK 8.11+) is now the floor — older "
            "Cura versions are no longer supported.",
            "The live nozzle indicator in Preview is reliable again for "
            "prints loaded while Preview is already open.",
            "Settings that refuse to save now say which field is wrong "
            "instead of silently discarding the change.",
            "Open Browser after an upload now actually opens the "
            "browser.",
            "Memory diagnostics (toggles on the Diagnostics page) log "
            "the physical footprint, QML and collection growth, and "
            "camera gauges every ten seconds, with a separate Python "
            "allocation trace, so a memory climb can be tracked down.",
        ),
    },
    {
        "version": "4.2.0",
        "headline": "Version 4.2.0 is the printer-state release: three live motion "
            "readouts join the Monitor card, and the controls that cannot be used "
            "now say why.",
        "items": (
            "Three new readouts on the Monitor card: Velocity, the acceleration "
            "limit Klipper has configured, and the Flow rate in mm³/s — like "
            "Position they read a dash until their values arrive (Flow rate "
            "also waits for the printer.cfg diameter).",
            "Flow rate is the commanded volumetric flow — Klipper's own live "
            "extruder velocity times the filament cross-section from your "
            "printer.cfg diameter. A retraction reads negative, which is "
            "correct, and the number can lag for up to half a minute after "
            "the last extrusion.",
            "The filament diameter is read from printer.cfg for the tool in "
            "use, so a mixed 1.75 mm / 2.85 mm machine reads correctly. "
            "There is no override: a wrong diameter is a printer.cfg error.",
            "Controls that are unavailable now say why, instead of only "
            "greying out — a printer that has not reported its state yet, "
            "or a print already running.",
            "A printer state that is not yet known is no longer treated as "
            "ready: the power switches and the restart buttons wait for a "
            "real state before they can be pressed.",
            "The bed-mesh views gain a dual-ended range filter: one shared "
            "window on the Information pop-over and the Preview card, drag "
            "between the handles to move it, and everything outside the "
            "window greys out on both surfaces so peaks and troughs stand "
            "out.",
            "Scale z-max: the Preview's bed-mesh exaggeration now adjusts "
            "from 0 (flat) to 1000×, remembered between sessions.",
            "Fans Klipper regulates itself (controller fans, heater fans, "
            "temperature fans) show their speed read-only instead of a "
            "slider that could not take effect.",
            "The LED channels and their brightness work independently: the "
            "channel sliders hold your percentages, the brightness slider "
            "scales the result, and nudging one never moves the other.",
            "The first connection is eager: the printer's data appears as "
            "soon as Moonraker answers, instead of waiting out a "
            "quiet-period delay.",
        ),
    },
    {
        "version": "4.1.0",
        "headline": "Version 4.1.0 is the deep-harness-coverage release: the real-Cura "
            "gate's coverage and proof machinery, the parallel local matrix, and the "
            "review-driven product repairs, each shipped with its evidence.",
        "items": (
            "The file list no longer re-sorts on a temperature tick, so it "
            "never flickers while you browse.",
            "Upload refusals say why: the printer's own reason appears in "
            "the upload status line.",
            "The ETA improvement works right after loading a print into "
            "the Preview (the hourglass no longer goes missing).",
            "A one-time \"What's new\" popup per version: when Cura starts "
            "after an update, this changelog appears — the new release's "
            "notes open at the top, older releases collapsed below, with a "
            "link to the project's home. Esc, a click outside, or Close "
            "dismisses it, and it stays gone until the next release.",
            "The release test suite got stronger and faster (parallel "
            "local runs), so every release ships with its proof.",
        ),
    },
    {
        "version": "4.0.2",
        "headline": "Version 4.0.2 is a correctness release: five repairs to the transfer "
            "and print-identity paths, each shipped with its regression tests.",
        "items": (
            "Downloads cancel cleanly: cancelling or switching printers "
            "mid-download can no longer freeze Cura or corrupt a file.",
            "Download progress and the size cap reset properly on each "
            "attempt.",
            "The Download button honestly reports what happened, and can "
            "no longer get stuck \"loading\" forever.",
            "Uploads report once, and a second upload of the same file "
            "while one runs is refused with a message.",
            "The previous print's details can never be mistaken for the "
            "new print's.",
        ),
    },
    {
        "version": "4.0.1",
        "headline": "Version 4.0.1 is the harness release: no product changes — the "
            "plugin behaves exactly as in 4.0.0. The test infrastructure around the "
            "release gate hardened, and the documentation was cleaned up.",
        "items": (
            "No user-facing changes: this release hardens the plugin's "
            "test and release tooling and cleans up its documentation.",
        ),
    },
    {
        "version": "4.0.0",
        "headline": "Version 4.0.0 is the websocket release: the Moonraker status transport "
            "moves from per-request HTTP polling to Moonraker's websocket subscription, "
            "with HTTP kept as a selectable, automatic-fallback mode.",
        "items": (
            "A live websocket connection keeps the status feed flowing, "
            "with HTTP polling kept as an automatic fallback.",
            "A transport choice on the Connection tab (websocket is the "
            "default), and the connected status names the live "
            "connection.",
            "The monitor's other requests ride the live connection where "
            "possible, falling back to HTTP when the socket is down.",
            "Delivery-cadence sliders tune how often the status, "
            "auxiliary data and console update — per printer.",
            "The camera bridge: a camera behind a password-protected "
            "proxy now works — the plugin re-serves the stream locally "
            "with the key.",
            "Preview polish: the current-layer label fills while attached "
            "and collapses when idle, and dragging the slider detaches "
            "immediately.",
            "Monitor completeness: steady temperatures and devices "
            "switched on mid-print appear without reconnecting.",
            "Settings polish: a wrong API key on Test connection now "
            "reads \"the API key was rejected (HTTP 401)\".",
            "The 4.0.1 scope folded in: scroll-to-prompt, the "
            "verified-pause-only list, opt-in ETA auto-improvement, a "
            "recovering-camera veil, and restart arming.",
            "Fixes from the live-test round: subscription deadlocks, "
            "drag echo, watchdog snap-back and wrong-card flips.",
        ),
    },
    {
        "version": "3.6.0",
        "headline": "Version 3.6.0 is the file-manager release, closed out with the "
            "post-review sweep and the adversarial round's fixes.",
        "items": (
            "File manager popup: the Monitor tab's Files button opens "
            "the full store browser — search, filters, paging, sortable "
            "columns, folder chips and thumbnails.",
            "Mutations: print with a confirmation, download, upload with "
            "progress and guards, rename with live collision checks, "
            "delete, and create-folder.",
            "Dialog discipline: every dialog closes with Esc or with the "
            "popup, and an upload's progress supersedes its "
            "confirmation.",
            "Print-start watchdog: the verdict follows the printer's "
            "actual state, so re-printing the same file is watched "
            "properly.",
            "Reconnect: a Reconnect button in the System section "
            "recovers a dropped connection.",
            "Console: the pane resizes by dragging its divider, and the "
            "error bell stays fed while collapsed.",
        ),
    },
    {
        "version": "3.5.1",
        "headline": "Version 3.5.1 is the stability patch for 3.5.0: the Monitor tab "
            "never shifts under the pointer, and it behaves sensibly while the printer "
            "is disconnected.",
        "items": (
            "No reflow, ever: no control on the Monitor tab disappears "
            "any more — every state-gated control stays put and simply "
            "disables.",
            "Disconnected state: every control disables while the "
            "console stays readable, and the camera dims with an "
            "\"offline\" caption and shows a Live badge while streaming.",
            "Readout restyle: the two-column label/value pattern applies "
            "across the Printer-controls sections.",
            "Klipper restart button in the System section, a larger "
            "Webcam pane title, and the free-text extrusion distance box "
            "is gone (the presets cover it).",
        ),
    },
    {
        "version": "3.5.0",
        "headline": "Version 3.5.0 is the informational half of Mainsail parity: the "
            "Monitor tab becomes the at-a-glance view of a running printer, with "
            "temperature history, a bed-mesh map, a console with Klipper's live output, "
            "endstop readouts and a better remaining-time estimate.",
        "items": (
            "Temperature history chart in the Information pane: a "
            "30-minute window per sensor, toggleable setpoints and "
            "power, a hover tooltip and a mini-chart. The history resets "
            "when Cura restarts.",
            "Bed-mesh mini map in the Information pane, with a crosshair "
            "that snaps to probe points.",
            "G-code console below the webcam: send commands, recall "
            "history and watch Klipper's live replies — the transcript "
            "persists per printer across sessions.",
            "\"Last action\" row in the Print job grid, showing sent "
            "commands and their confirmed outcome.",
            "Filament used/remaining readouts, correct for "
            "multi-extruder prints.",
            "Live endstop readouts, with an explicit not-homed-yet "
            "state.",
            "A better remaining-time estimate that prefers the G-code's "
            "own per-layer timing.",
            "One shared pop-over shell for the pane's widgets — every "
            "pop-over closes with Esc, its Close button, or an outside "
            "click.",
        ),
    },
    {
        "version": "3.4.0",
        "headline": "Version 3.4.0 is the first Monitor-parity release: manual control "
            "of the physical toolhead from the Monitor tab, and a full overhaul of the "
            "Monitor layout into Cura-style panes and collapsible sections.",
        "items": (
            "Toolhead section: jog arrows with distance presets, "
            "per-axis home, motors off, centre-toolhead and park moves, "
            "plus a live position readout.",
            "KlipperScreen-style extrusion controls with distance and "
            "speed presets.",
            "Jogs respect the axis limits and the pause-first safety "
            "gate: moves queue while the print pauses, then run.",
            "Three Cura-style panes — Information, Printer status and "
            "Printer controls — with collapsible sections that remember "
            "their state across restarts.",
            "Lock-all disables only the controls: the accordion stays "
            "navigable, with padlock icons showing the state.",
            "Setup commands queue in order, and the emergency stop "
            "docks across the bottom of the window.",
            "Camera bar: a compact webcam selector and an icon-only "
            "refresh.",
            "Honest command replies: an uncertain outcome says so "
            "instead of claiming cancellation.",
            "Fixes from the pre-release review: rapid Preview ⇄ Monitor "
            "switching no longer detaches the follower, and narrow "
            "stages compress gracefully.",
        ),
    },
    {
        "version": "3.3.1",
        "headline": "Version 3.3.1 is an audit-driven hardening pass over 3.3.0: an "
            "adversarial multi-agent review against the architecture contract confirmed "
            "and fixed the following.",
        "items": (
            "The smoothing trace is now opt-in instead of writing to "
            "Cura's cache on every smoothed print.",
            "The display timer no longer ticks through a whole pause.",
            "Failed downloads retry with backoff instead of wedging the "
            "file service for the rest of the print.",
            "Start-print power probing covers every configured power "
            "device.",
            "A batch of review-driven repairs, each with a regression "
            "test.",
        ),
    },
    {
        "version": "3.3.0",
        "headline": "Version 3.3.0 is the first feature release after the debt payoff: "
            "follower quality.",
        "items": (
            "Smooth Preview following: the displayed position glides "
            "along the toolpath at the observed physical speed, with no "
            "snapping back within a layer.",
            "The glide is equally smooth at any polling rate.",
            "Slow moves are observed accurately, so the progress reads "
            "smoothly.",
            "A Smooth path progress option (on by default) in the "
            "Following tab.",
        ),
    },
    {
        "version": "3.2.0",
        "headline": "Version 3.2.0 closes the remaining 3.1.0 architecture gaps and "
            "codifies how the repository changes. User-facing behaviour is unchanged.",
        "items": (
            "No user-facing changes: this release reorganises the "
            "internals and codifies how the repository changes.",
        ),
    },
    {
        "version": "3.1.0",
        "headline": "Version 3.1.0 is primarily an internal architecture and "
            "reliability release. It preserves the v3.0.0 user-facing workflow while "
            "reducing duplicated state/transport ownership and making the high-risk "
            "parts independently testable.",
        "items": (
            "No user-facing changes: this release consolidates the "
            "internals into one shared session layer and makes the "
            "risky parts independently testable.",
        ),
    },
    {
        "version": "3.0.0",
        "headline": "Version 3.0.0 turns Moonraker Print Follower into a much more "
            "complete Cura-side companion for Klipper/Moonraker while preserving the "
            "core live Preview follower.",
        "items": (
            "Moonraker connection and G-code upload built into the one "
            "plugin.",
            "A live printer dashboard: temperatures, print state, "
            "macros, power controls, Z offset, speed/flow tuning, fans, "
            "LEDs and an emergency stop.",
            "Rich bed-mesh support, with a 3D Preview overlay and mesh "
            "controls.",
            "Schedule end-of-layer pauses directly from the Preview, "
            "with multiple pauses and ETA display.",
            "Better selected-layer ETA while inspecting future layers.",
            "Preview following controls renamed to Detach / Attach, so "
            "they cannot be confused with pausing the printer.",
            "Better multi-printer behaviour, large-print performance "
            "and stale-response protection.",
        ),
    },
    {
        "version": "2.0.0",
        "headline": "Version 2.0.0 makes Moonraker Print Follower feel like part of "
            "Cura rather than a separate utility.",
        "items": (
            "Configuration moves into Settings → Printer → Manage "
            "Printers → Configure Moonraker Follower.",
            "Full per-printer settings, with one active printer at a "
            "time.",
            "Improved live nozzle handling in the Preview, using Cura's "
            "native nozzle model.",
            "Smoother within-layer following, without visible rewind or "
            "retrace.",
            "Existing 1.x settings migrate automatically.",
        ),
    },
    {
        "version": "1.1.0",
        "headline": "Version 1.1.0 moves Moonraker Print Follower from a single global "
            "setup to a proper per-printer Cura workflow.",
        "items": (
            "Separate connection and following settings for each Cura "
            "printer.",
            "Automatic migration of existing 1.0.x settings.",
            "New follow modes: exact layer, last completed layer, "
            "one-layer look-ahead and a layer window.",
            "Resilient Moonraker polling with automatic retry backoff.",
            "Built-in connection testing and capability detection.",
            "Large G-code files handled through compact indexing and "
            "on-demand loading.",
            "Refined Cura-styled Preview controls and clearer live "
            "status.",
        ),
    },
    {
        "version": "1.0.3",
        "headline": "Version 1.0.3 is a performance and accuracy release aimed "
            "particularly at larger G-code files and long-running prints.",
        "items": (
            "Streams G-code downloads and indexing instead of holding "
            "the whole file in memory.",
            "Persistent path indexes make repeated loads much faster.",
            "Uses the printer's motion data to match the nozzle position "
            "better.",
            "Better layer mapping from the G-code itself.",
            "Stronger manual-override detection and stale-work "
            "protection.",
        ),
    },
    {
        "version": "1.0.2",
        "headline": "Version 1.0.2 focuses on making following behave predictably "
            "while Cura is loading, slicing or changing scenes.",
        "items": (
            "Predictable following while Cura loads, slices or changes "
            "scenes.",
            "More reliable manual-override detection when the Preview "
            "rebuilds.",
            "Cleaner cancellation of downloads, requests and background "
            "indexing.",
            "Better stale-data protection on same-name re-prints.",
            "Lower memory use while indexing large files.",
        ),
    },
    {
        "version": "1.0.1",
        "headline": "This release makes it much easier to inspect a print without "
            "fighting the follower.",
        "items": (
            "Moving the layer or toolpath slider suspends automatic "
            "following.",
            "Resuming following catches the Preview back up to the live "
            "print.",
            "Plugin-driven movement is never mistaken for your own "
            "interaction.",
        ),
    },
    {
        "version": "1.0.0",
        "headline": "Moonraker Print Follower brings a live Klipper/Moonraker print "
            "into Cura Preview.",
        "items": (
            "Follow the printer's current layer and progress in the "
            "Cura Preview.",
            "Load the printing G-code into Cura on demand.",
            "Pause and resume Preview following without pausing the "
            "printer itself.",
            "Configure connection details, polling, layer handling and "
            "Preview behaviour.",
        ),
    },
)


def latest_version() -> str:
    return str(WHATS_NEW[0]["version"])


def should_show(seen: str) -> bool:
    """The once-per-version gate: show when the stored marker is not
    the shipped version (a fresh install stores nothing)."""
    return str(seen or "") != latest_version()


def entries() -> List[dict]:
    """The overlay's content shape: every version with its headline
    and bullets, with the latest flagged so the overlay can render it
    open at the top and the rest as pre-collapsed sections."""
    return [
        {"version": entry["version"], "headline": entry["headline"],
         "items": list(entry["items"]), "isLatest": index == 0}
        for index, entry in enumerate(WHATS_NEW)
    ]
