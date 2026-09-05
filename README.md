# Moonraker Print Follower

Moonraker Print Follower keeps Cura Preview synchronised with a print running through Klipper/Moonraker.

- **Author:** shallax
- **Maintainer:** moonrakerprintfollower@maintain.contact
- **Project:** https://github.com/shallax/MoonrakerPrintFollower
- **Release:** 2.0.0
- **Target:** Cura 5.0–5.13 / SDK 8.0–8.12

## Cura / SDK compatibility

Version 2.0.0 targets the complete Cura 5.x SDK 8 line from **Cura 5.0 / SDK 8.0** through **Cura 5.13 / SDK 8.12**. The Cura package declares SDK 8.0 as its minimum package SDK, while `plugin.json` explicitly records SDK 8.0 through 8.12 support. Cura SDK 8 minor versions are backwards-compatible with an SDK 8.0 package floor.

The implementation deliberately stays on APIs already present in Cura 5.0 where practical: the Machine Action framework, `globalContainerStackChanged`, public `readLocalFile()`, additional `saveButton` components, SimulationView layer/path controls and Cura's native `NozzleNode` interface. Optional Qt conveniences such as request transfer timeouts remain capability-checked before use. The settings QML avoids Machine Action properties and UM QML controls introduced after SDK 8.0.

Cura 4.x / SDK 7.x is not supported: Cura 5.0 is the Qt 6 / PyQt6 boundary and this plugin intentionally targets that runtime.

The automated suite verifies the SDK floor and guards against accidental dependencies on selected post-8.0 APIs. Actual rendering and interaction should still be smoke-tested on representative Cura releases when publishing a compatibility claim.

## What changed in 2.0.0

Version 2.0.0 makes configuration fully printer-scoped in Cura.

There is no **Extensions → Moonraker Print Follower** menu. Runtime controls remain in Cura Preview, while configuration lives with the printer it belongs to:

1. Open **Settings → Printer → Manage Printers**.
2. Select the Cura printer you want to configure.
3. Click **Configure Moonraker Follower**.
4. Edit the settings in Cura's native Machine Action page.
5. Click **Save**.

The configuration UI is implemented as a Cura Machine Action QML page, so Cura owns the dialog and its modal lifecycle. It does not open a separate Qt Widgets settings window.

## Per-printer settings

Each Cura printer has its own follower configuration. Only Cura's **currently active printer** can own the live follower session; changing the active Cura printer tears down the old printer's polling/download/index work before the new printer can connect. Multiple printers are never followed concurrently.


- enable or disable automatic following
- Moonraker URL
- optional API key
- HTTP polling interval
- follow mode
- within-layer path following
- native Cura live printhead fallback
- fallback layer-number convention
- automatic switching to Preview
- Z-height fallback and tolerance

A connection test is available on the **Connection** tab. The API key field is optional and is not populated with an example value.

For documentation or testing, use a deliberately non-routable example such as `http://printer.example.invalid:7125`. Do not commit real printer addresses or credentials to the source tree.

## Follow modes

- **Exact current layer** — follows the layer currently being printed.
- **Last completed layer** — shows the previous completed layer.
- **Look ahead one layer** — shows the layer after the current printer layer.
- **Window around current layer (±2)** — shows a five-layer window around the live layer where Cura supports it.

Manual movement of either Cura layer handle or either within-layer path handle pauses following. **Resume** in the Preview card catches the view back up without stopping Moonraker polling. Within a live layer, follower progress is kept monotonic so repeated/closed toolpaths cannot make Cura visibly rewind and retrace a section when Moonraker's live position is ambiguous.

## Preview controls

The follower controls live in their own Cura-styled action-panel card in Preview. The card contains:

- Cura's native nozzle icon and a bold **Moonraker Print Follower** title
- a state icon plus **the active Cura printer name and live follower status**
- **Pause/Resume** and **Load print** actions

The panel uses a fixed layout so status changes do not resize it, and it reserves Cura's normal action-panel spacing from neighbouring plugin controls. If the currently active Cura printer is not enabled and configured with a usable Moonraker URL, the entire follower card is hidden because none of its runtime controls apply. A configured printer that is temporarily offline still shows the card with its disconnected state.

When exact within-layer following is active, the plugin can keep **Cura's own native SimulationView nozzle** visible even when Cura's Preview lifecycle would otherwise leave the nozzle uninitialised or suppress it during a live layer change. If a live file is loaded while Preview is already open, the follower ensures Cura's own NozzleNode is created and attached to the current scene before allowing SimulationPass to render it. The plugin still does not draw a second nozzle model, so Cura's normal mesh, visibility, depth and transparency behaviour are preserved. The fallback can be enabled or disabled per printer on the **Following** tab.

## Moonraker transport

Live status uses HTTP polling only.

- the configured interval is used while the connection is healthy
- failed requests back off through 1 s → 2 s → 5 s → 10 s → 30 s
- the normal interval resumes immediately after a successful response
- capabilities are inferred from the objects Moonraker actually exposes

There is no WebSocket transport and no automatic printer discovery.

## Large G-code handling

Large G-code files are streamed and indexed without loading the complete file into Python memory. Very large files use a compact layer index and hydrate detailed motion information only for layers that need it. The next layer is prepared ahead of the transition; if its detailed index is not ready yet, Preview briefly holds at the start of that layer rather than showing a coarse estimate and then jumping backwards. Persistent indexes are validated against remote file identity before reuse.

Layer markers are recognised for Cura, PrusaSlicer, SuperSlicer and OrcaSlicer, with `SET_PRINT_STATS_INFO CURRENT_LAYER=...` available as a self-describing fallback.

## Installation

### Recommended: install the Cura package

The release source archive contains a ready-to-install file named:

`MoonrakerPrintFollower-v2.0.0.curapackage`

The canonical Cura/Marketplace package id is `Moonraker_Print_Follower`.

To install it:

1. Extract the source archive if you downloaded the source ZIP.
2. Start Cura.
3. Drag `MoonrakerPrintFollower-v2.0.0.curapackage` onto the Cura window.
4. Accept the installation prompt.
5. Quit Cura completely and start it again.
6. Open **Settings → Printer → Manage Printers**.
7. Select a printer and click **Configure Moonraker Follower**.

When replacing another build with the same version number during development, uninstall the existing plugin and restart Cura before installing the replacement package so Cura cannot retain stale plugin files.

### Manual installation from source

If you specifically want to install the source tree rather than the bundled Cura package:

1. Open **Help → Show Configuration Folder** in Cura.
2. Open that configuration folder's `plugins` directory.
3. Create a `Moonraker_Print_Follower` directory there if necessary.
4. Copy the contents of the source archive's `plugins` directory into that `Moonraker_Print_Follower` directory.
5. Restart Cura.

The `.curapackage` route is recommended because it lets Cura perform the package installation itself.

## Upgrade compatibility

The 1.0.x and 1.1.x settings migration remains supported. Existing legacy global settings are migrated once to the active Cura printer after Cura has established its global machine stack. The plugin deliberately does not force Cura's lazy `MachineManager` into existence during plugin loading.

## Internal structure

High-risk logic is separated into focused modules:

- `PrinterConfig.py` — per-Cura-machine persisted settings and legacy migration
- `MoonrakerFollowerMachineAction.py` — native Manage Printers configuration surface
- `MoonrakerFollowerConfiguration.qml` — Cura-owned settings UI
- `MoonrakerClient.py` — resilient HTTP polling, retry backoff and capability detection
- `FollowController.py` — follower state machine and follow-mode decisions
- `CuraAdapter.py` — Cura machine identity, Preview writes and toolpath-head position mapping
- `NativeNozzleFallback.py` — repairs Cura's native SimulationView nozzle lifecycle during exact live following, including when a live file is loaded while Preview is already open
- `GCodeIndex.py` — streaming/compact parsing, lazy layer hydration and persistent index cache
- `MoonrakerProtocol.py` — endpoint construction and coordinate conversion
- `DownloadStream.py` — bounded streaming G-code downloads
- `Core.py` — shared operation, identity and manual-override primitives

## Development and release checks

The source archive includes a standard-library `unittest` suite under `tests/`. It covers the established follower behaviour, single-active-printer ownership, per-printer settings, HTTP status handling, follow modes, startup safety, manual Preview override detection, multiple slicer layer markers, compact/lazy indexes, persistent-cache round trips and Cura integration contracts.

Release auditing also checks that the source contains no hard-coded real printer names, real printer/network addresses, or literal sample API keys. Example network values must use reserved non-routable domains.
