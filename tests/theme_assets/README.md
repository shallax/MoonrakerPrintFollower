# Vendored Cura / Uranium theme assets

This directory holds **upstream Ultimaker assets** vendored solely so the
deterministic capture harness (`tools/capture_*.py`) can render the
plugin's QML with the real Cura look: real Cura widgets, the real Uranium
component chain and the real cura-light theme.

## Contents

- `Cura/` — real Cura QML components (buttons, widgets, dialog types).
- `UM/` — real Uranium QML components (`Label`, `TextField`, `Dialog`,
  `TabRow`, …).
- `cura-light/` — the real cura-light theme (`theme.json`) and Cura's
  icon set under `icons/default/`.

## Provenance

Extracted from the Cura 5.9.1 AppImage (Ultimaker Cura). Upstream sources:
<https://github.com/Ultimaker/Cura> and <https://github.com/Ultimaker/Uranium>.

## Licence

Cura and Uranium are released under the **LGPLv3 or later**
(`LGPL-3.0-or-later`); individual files carry their upstream copyright
headers. The files here are vendored verbatim except where the capture
harness requires a stand-in for a type that does not exist as a QML file
(see `tools/theme_support.py` for what is spliced at materialise time —
nothing under this directory is modified in place).

## Refreshing

These assets are refreshed deliberately, not automatically. To update to
a newer Cura release:

1. Download the target Cura AppImage and extract it (e.g.
   `./Ultimaker-Cura-5.x.y-linux-X64.AppImage --appimage-extract`,
   then locate the resources under `squashfs-root/`).
2. Copy the widget QML files into `Cura/` / `UM/`, the theme JSON into
   `cura-light/` and the icons into `cura-light/icons/default/`.
3. Regenerate the captures inside the dev container
   (`tools/docker_gates.sh`) and commit the refreshed `screenshots/`
   copies — the Screenshot sync CI job enforces the byte-for-byte match.
4. Update the attribution in this README.
