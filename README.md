# Moonraker Print Follower

A Cura 5.x extension that follows the print currently running through Klipper/Moonraker.

## Features

- Follows Moonraker's current layer in Cura Preview.
- Follows progress through the current layer using Cura's horizontal toolpath slider.
- **Load current print** explicitly downloads the active Moonraker G-code and replaces the current Cura contents after a standard Yes/No confirmation.
- **Pause following / Resume following** freezes or resumes automatic Preview movement without stopping Moonraker polling.
- Controls are Preview-only. **Load current print** remains available in an empty Preview; Pause/Resume appears only when Cura has toolpath data.
- Poll interval accepts any positive whole-number millisecond value with no snapping or plugin-side upper cap.
- Optional Moonraker API key, layer-number offset handling, automatic Preview switching, and Z-height fallback.
- Remote G-code is cached for manual loading; Cura's G-code parser is given CPU priority during an explicit load.

## Install

Drag the `.curapackage` file onto Cura, accept the installation, and restart Cura.

Configure it under **Extensions → Moonraker Print Follower → Configure…**.

## Manual remote load

Open **Preview** and click **Load current print**. Choosing **Yes** replaces whatever Cura currently has loaded with the G-code currently printing through Moonraker. There is no automatic empty-build-plate load.

## Following

Whole-layer following uses `print_stats.info.current_layer`. Within-layer following maps Moonraker's `virtual_sdcard.file_position` against motion-command offsets in the active G-code. The motion index uses the same G-code marker/motion matching behaviour as the known-good v0.9.8 implementation.

Klipper may process G-code slightly ahead of physical motion, so very short layers can still show a small visual lead.
