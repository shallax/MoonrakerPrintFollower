# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-10-01 [1d8bd42] Capitalise the remaining top-level directories
# 2026-09-18 [39b9a74] 4.4.0: the verification round — the panel-fix batch (the download guard and its pins, the
#   load supersede, the pause-items stash keyed on the states, the strip's own fill address with x10's collapse
#   pin, the card's independent height gate, the dashboard's visible-window fit bound, the popover's overlay
#   host, the slider's colour type, the theme-covered lint globs, the hourglass tooltip, the settled docs), the
#   p6/p7 determinism pins, the timestamped evidence dirs, and the ratchet re-census; every gate green on the
#   fixed tree

"""The theme ruling as a gate: no magic colour hexes outside the theme.

The 4.4.0 ruling — either Cura's theme colours or the
plugin's own theme document; a hex literal in any other QML file is
a regression this test refuses to let through.
"""
import pathlib
import re
import unittest
from tests.source_root import SourceRoot

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = SourceRoot(ROOT / "mpf")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{8}\b")
NAMED = re.compile(r'color:\s*"(black|white|red|green|blue|grey|gray|darkgrey|darkgray|lightgrey|lightgray)"')
# The named-literal allowlist (the panel's M2): the two justified
# whites — the e-stop's white-over-red sweep copy and the visibility
# selector's white glyph on its coloured fill. Every exemption is
# documented here rather than accidental.
NAMED_ALLOWED = {
    (PLUGINS / "MoonrakerMonitorDashboard.qml", 'color: "white"'),
    (PLUGINS / "VisibilitySelector.qml", 'color: "white"'),
}
# The theme document by name: the resources tree has moved once
# already and the exemption below has to follow it.
THEME_DIR = PLUGINS.path("MoonrakerTheme.qml").parent


class ThemeColourGateTests(unittest.TestCase):
    def test_every_hex_lives_in_the_theme_document(self):
        # Recursive (the panel's catch): a QML added in any
        # subdirectory must answer to the ruling too — the theme
        # document itself is the one explicit exemption.
        offenders = []
        for path in sorted(PLUGINS.rglob("*.qml")):
            if path.parent == THEME_DIR:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if HEX.search(line):
                    offenders.append(f"{path.relative_to(PLUGINS.root)}:{number}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "magic colour hexes outside the theme: %s" % offenders)

    def test_no_named_colour_literals_outside_the_allowlist(self):
        # The panel's M2: the dark-mode e-stop label was hardcoded
        # black and slipped past the hex-only gate — named colour
        # literals answer to the same ruling now, with the two
        # justified whites exempted explicitly.
        offenders = []
        for path in sorted(PLUGINS.rglob("*.qml")):
            if path.parent == THEME_DIR:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = NAMED.search(line)
                if match and (path, match.group(0)) not in NAMED_ALLOWED:
                    offenders.append(f"{path.relative_to(PLUGINS.root)}:{number}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "named colour literals outside the allowlist: %s" % offenders)

    def test_every_cited_theme_token_is_declared(self):
        # The cross-check (the panel's catch): a hand-pinned list
        # cannot rot — every MoonrakerTheme.* token the documents
        # cite must exist in the theme document.
        theme = PLUGINS.path("MoonrakerTheme.qml").read_text(encoding="utf-8")
        declared = set(re.findall(r"readonly property \w+ (\w+)", theme))
        cited = set()
        for path in PLUGINS.rglob("*.qml"):
            cited.update(re.findall(r"MoonrakerTheme\.(\w+)", path.read_text(encoding="utf-8")))
        self.assertEqual(cited - declared, set(),
                         "cited theme tokens not declared: %s" % sorted(cited - declared))


if __name__ == "__main__":
    unittest.main()
