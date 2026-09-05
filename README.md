# Moonraker Print Follower

Moonraker Print Follower is a unified Cura integration for Klipper/Moonraker. Version 3.0.0 keeps Cura Preview synchronised with a live print, provides Cura's Moonraker upload/print destination, and adds a live Cura Monitor view with Moonraker webcams, so the separate Moonraker Connection plugin is no longer required.

- **Author:** shallax
- **Maintainer:** moonrakerprintfollower@maintain.contact
- **Project:** https://github.com/shallax/MoonrakerPrintFollower
- **Release:** 3.0.0
- **Target:** Cura 5.0–5.13 / SDK 8.0–8.12

## What changed in 3.0.0

Version 3.0.0 combines the follower and the Cura-to-Moonraker connection/output/monitor workflow into one plugin and one per-printer configuration.

For each Cura printer, the same Moonraker URL and optional API key now drive:

- live Cura Preview following
- Cura's **Upload to _printer_** output destination
- G-code or UFP output
- an optional upload filename/folder dialog
- remembered remote folders
- optional immediate printing after upload
- optional Moonraker power-device startup before printing
- non-blocking waits for Klippy to become ready
- upload progress and success/error reporting
- optional browser handoff to a configured frontend URL
- filename character translation/removal
- Cura's **Monitor** stage with Moonraker webcam discovery
- multiple-camera selection, camera rotation and flips
- live print state, file, progress, layer, elapsed time, speed/flow multipliers and XYZ position

The output controller does not advertise pause, abort, preheat or manual-control buttons until those commands are actually implemented by this plugin.

### Upgrading from Moonraker Connection

On first v3 startup, Moonraker Print Follower looks for the standalone plugin's existing per-printer preference data under `moonraker/instances` and imports compatible settings once.

Existing Moonraker Print Follower URL/API-key values take precedence when already configured. Output-specific settings such as upload format/path, start-print behaviour, power devices, retry interval, frontend URL and filename translation are imported from Moonraker Connection. Its legacy camera URL, rotation and mirror settings are also imported as a fallback for Moonraker installations that do not expose webcam configuration through the webcam API. The old preference data is left untouched so rollback remains possible.

After verifying v3 with your printers, the separate Moonraker Connection plugin can be removed.

## Cura / SDK compatibility

Version 3.0.0 targets the complete Cura 5.x SDK 8 line from **Cura 5.0 / SDK 8.0** through **Cura 5.13 / SDK 8.12**. The package declares SDK 8.0 as its minimum package SDK, while `plugin.json` explicitly records SDK 8.0 through 8.12 support.

The implementation stays on APIs already present in Cura 5.0 where practical: Machine Actions, `globalContainerStackChanged`, public `readLocalFile()`, output devices, `NetworkMJPGImage`, SimulationView layer/path controls and Cura's native nozzle interface. Optional Qt conveniences such as request transfer timeouts are capability-checked where required.

Cura 4.x / SDK 7.x is not supported. Cura 5.0 is the Qt 6 / PyQt6 boundary and this plugin intentionally targets that runtime.

Actual rendering, output-device presentation, webcam streaming and printer interaction should still be smoke-tested on representative Cura releases before publishing a compatibility claim.

## Configuration

There is no **Extensions → Moonraker Print Follower** settings dialog. Configuration lives with the Cura printer it belongs to:

1. Open **Settings → Printer → Manage Printers**.
2. Select the Cura printer you want to configure.
3. Click **Configure Moonraker**.
4. Use the **Connection**, **Following** and **Output** tabs.
5. Click **Save**.

The settings UI is implemented as a native Cura Machine Action QML page, so Cura owns the dialog and its modal lifecycle.

## Connection tab

The Connection tab contains the settings shared by following, output and monitoring:

- Moonraker URL
- optional API key
- live-status polling interval
- **Test connection**

For documentation or testing, use a deliberately non-routable example such as `http://printer.example.invalid:7125`. Do not commit real printer addresses or credentials to the source tree.

A valid Moonraker URL makes a Moonraker output destination and Monitor view available for the currently active Cura printer even when automatic Preview following is disabled.

## Following tab

Each Cura printer has its own follower settings. Only Cura's **currently active printer** can own the live follower session; changing the active Cura printer tears down the old printer's polling/download/index work before the new printer can connect.

Settings include:

- enable or disable automatic following
- follow mode
- within-layer path following
- native Cura live printhead fallback
- fallback layer-number convention
- automatic switching to Preview
- Z-height fallback and tolerance

### Follow modes

- **Exact current layer** — follows the layer currently being printed.
- **Last completed layer** — shows the previous completed layer.
- **Look ahead one layer** — shows the layer after the current printer layer.
- **Window around current layer (±2)** — shows a five-layer window around the live layer where Cura supports it.

Manual movement of either Cura layer handle or either within-layer path handle pauses following. **Resume** in the Preview card catches the view back up without stopping Moonraker polling. Within a live layer, follower progress is monotonic so repeated or closed toolpaths cannot make Cura visibly rewind and retrace a section when Moonraker's live position is ambiguous.

## Output tab

The Output tab configures Cura-to-Moonraker uploads:

- **Frontend URL** — optional destination for the success message's Open Browser action; falls back to the Moonraker URL.
- **Output format** — G-code or UFP. Pre-sliced jobs fall back to G-code.
- **Show filename/path dialog** — lets you edit the remote folder, filename and start-print choice for each upload.
- **Default remote folder** — path below Moonraker's `gcodes` root.
- **Start printing after upload by default**.
- **Remember upload choices** — remembers remote folders and, when enabled, the last folder/start-print choice.
- **Auto-hide successful upload message**.
- **Power devices** — comma-separated Moonraker power device names. When a print is requested and the first device is off, v3 powers the configured devices on before waiting for Klippy.
- **Printer-ready retry interval** — v3 uses Qt timers rather than blocking sleeps, so Cura remains responsive while a powered-on printer starts.
- **Filename translation** — position-for-position replacement plus a set of characters to remove.

Upload-only mode does not require Klippy to be ready; Moonraker's file service can still accept a G-code file while the printer MCU is unavailable. Immediate-print mode waits for `server/info` to report `klippy_state: ready` before sending the upload with `print=true`.

## Monitor tab

The unified output device supplies Cura's normal **Monitor** stage with a dedicated Moonraker view.

The camera panel queries Moonraker's webcam API and automatically uses enabled webcams already configured for Mainsail/Fluidd/Moonraker. Relative stream URLs are resolved against the configured Moonraker host. If more than one webcam is available, Monitor displays a selector. Moonraker rotation plus horizontal/vertical flip settings are applied in Cura. A Refresh action re-reads Moonraker's webcam list without restarting Cura.

If Moonraker does not expose a webcam list, v3 falls back to camera URL/rotation/mirror settings imported from the standalone Moonraker Connection plugin.

Alongside the camera, Monitor currently shows:

- printer/job state and active filename
- overall print progress
- current/total layer where reported by Klipper
- elapsed print duration
- speed and extrusion multipliers
- live X/Y/Z position when `motion_report` is available
- **Open Moonraker frontend**

When automatic Preview following is enabled, Monitor consumes the follower's existing status stream rather than creating a duplicate poller. When following is disabled, the Monitor model uses a lightweight one-second status fallback so upload/Monitor remain useful independently of Preview following.

## Preview controls

The follower controls live in their own Cura-styled action-panel card in Preview. The card contains:

- Cura's native nozzle icon and a bold **Moonraker Print Follower** title
- a state icon plus the active Cura printer name and live follower status
- **Pause/Resume** and **Load print** actions

The panel uses a fixed layout so status changes do not resize it. If the currently active Cura printer is not enabled and configured with a usable Moonraker URL, the follower card is hidden. A configured printer that is temporarily offline still shows the card with its disconnected state.

When exact within-layer following is active, the plugin can keep **Cura's own native SimulationView nozzle** visible when Cura's Preview lifecycle would otherwise leave it uninitialised or suppress it during a live layer change. The plugin does not draw a second nozzle model, so Cura's normal mesh, visibility, depth and transparency behaviour are preserved.

## Moonraker transport

Follower live status uses HTTP polling only.

- the configured interval is used while the connection is healthy
- failed requests back off through 1 s → 2 s → 5 s → 10 s → 30 s
- the normal interval resumes immediately after a successful response
- capabilities are inferred from the objects Moonraker actually exposes

Monitor reuses that stream while following is enabled and otherwise polls status once per second. Webcam configuration is discovered independently because it changes rarely. Uploads use Moonraker's HTTP file API with multipart form data. Power-device and printer-readiness requests also use Moonraker HTTP endpoints. There is no WebSocket transport and no automatic printer discovery.

## Large G-code handling

Large G-code files used by the follower are streamed and indexed without loading the complete file into Python memory. Very large files use a compact layer index and hydrate detailed motion information only for layers that need it. The next layer is prepared ahead of the transition; if its detailed index is not ready yet, Preview briefly holds at the start of that layer rather than showing a coarse estimate and then jumping backwards.

Persistent indexes are validated against remote file identity before reuse. Layer markers are recognised for Cura, PrusaSlicer, SuperSlicer and OrcaSlicer, with `SET_PRINT_STATS_INFO CURRENT_LAYER=...` available as a self-describing fallback.

## Installation

### Cura package

Release builds use the canonical Cura/Marketplace package id `Moonraker_Print_Follower`. A v3 `.curapackage` should be generated from the audited v3 source as part of the release build.

The repository may still contain an older v2 `.curapackage` for historical/testing purposes; do not treat that file as a v3 build.

### Manual installation from source

For development/testing of v3:

1. Open **Help → Show Configuration Folder** in Cura.
2. Open that configuration folder's `plugins` directory.
3. Create a `Moonraker_Print_Follower` directory there if necessary.
4. Copy the contents of this repository's `plugins` directory into it.
5. Restart Cura completely.
6. Open **Settings → Printer → Manage Printers** and select **Configure Moonraker**.

When replacing another development build with the same version number, uninstall/remove the existing plugin files and restart Cura before installing the replacement so Cura cannot retain stale Python or QML files.

## Upgrade compatibility

The 1.0.x and 1.1.x follower-settings migration remains supported. Existing legacy global follower settings are migrated once to the active Cura printer after Cura has established its global machine stack.

Version 3 additionally migrates compatible output and fallback-camera settings from the standalone Moonraker Connection plugin. Neither migration forces Cura's lazy `MachineManager` into existence during early plugin loading.

## Internal structure

High-risk logic is separated into focused modules:

- `PrinterConfig.py` — unified per-Cura-machine settings plus follower and Moonraker Connection migration
- `MoonrakerFollowerMachineAction.py` — native Manage Printers configuration backend
- `MoonrakerFollowerConfiguration.qml` — Connection / Following / Output settings UI
- `MoonrakerOutputDevicePlugin.py` — exposes the Moonraker output destination and Monitor view for the active Cura printer
- `MoonrakerOutputDevice.py` — G-code/UFP writing, power/readiness orchestration, multipart upload, progress and browser handoff
- `MoonrakerUploadDialog.qml` — per-upload remote path/name/start-print dialog
- `MoonrakerMonitorModel.py` — Monitor status model, fallback transport, webcam discovery and camera selection
- `MoonrakerMonitor.qml` — Cura Monitor webcam/status presentation
- `MoonrakerClient.py` — resilient live-status HTTP polling, retry backoff and capability detection
- `FollowController.py` — follower state machine and follow-mode decisions
- `CuraAdapter.py` — Cura machine identity, Preview writes and toolpath-head position mapping
- `NativeNozzleFallback.py` — repairs Cura's native SimulationView nozzle lifecycle during exact live following
- `GCodeIndex.py` — streaming/compact parsing, lazy layer hydration and persistent index cache
- `MoonrakerProtocol.py` — endpoint construction and coordinate conversion
- `DownloadStream.py` — bounded streaming G-code downloads
- `Core.py` — shared operation, identity and manual-override primitives

## Development and release checks

The standard-library `unittest` suite under `tests/` protects the established follower behaviour and v3's unified output/Monitor path. Contracts cover single-active-printer ownership, per-printer settings, standalone-plugin migration, HTTP status handling, follow modes, startup safety, manual Preview override detection, multiple slicer layer markers, compact/lazy indexes, G-code/UFP output, power-device startup, non-blocking readiness waits, multipart uploads, webcam migration/discovery, Monitor wiring and Cura SDK compatibility.

Release auditing also checks that the source contains no hard-coded real printer names, real local-network addresses or literal sample API keys. Example network values must use reserved non-routable domains.
