"""The theme ruling as a gate: no magic colour hexes outside the theme.

The author's 4.4.0 ruling — either Cura's theme colours or the
plugin's own theme document; a hex literal in any other QML file is
a regression this test refuses to let through.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{8}\b")


class ThemeColourGateTests(unittest.TestCase):
    def test_every_hex_lives_in_the_theme_document(self):
        # Recursive (the panel's catch): a QML added in any
        # subdirectory must answer to the ruling too — the theme
        # document itself is the one explicit exemption.
        offenders = []
        for path in sorted(PLUGINS.rglob("*.qml")):
            if path.parent == PLUGINS / "theme":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if HEX.search(line):
                    offenders.append(f"{path.relative_to(PLUGINS)}:{number}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "magic colour hexes outside the theme: %s" % offenders)

    def test_every_cited_theme_token_is_declared(self):
        # The cross-check (the panel's catch): a hand-pinned list
        # cannot rot — every MoonrakerTheme.* token the documents
        # cite must exist in the theme document.
        theme = (PLUGINS / "theme" / "MoonrakerTheme.qml").read_text()
        declared = set(re.findall(r"readonly property \w+ (\w+)", theme))
        cited = set()
        for path in PLUGINS.rglob("*.qml"):
            cited.update(re.findall(r"MoonrakerTheme\.(\w+)", path.read_text()))
        self.assertEqual(cited - declared, set(),
                         "cited theme tokens not declared: %s" % sorted(cited - declared))


if __name__ == "__main__":
    unittest.main()
