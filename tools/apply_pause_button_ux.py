from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


qml = ROOT / "plugins" / "PreviewActionPanelControls.qml"
replace_once(
    qml,
    '''                text: base.pauseAtLayerCandidate <= 0
                    ? "Pause at end of selected layer"
                    : (base.pauseAtLayerScheduled
                        ? "Remove pause after layer " + base.pauseAtLayerCandidate
                        : (base.pauseAtLayerCanToggle
                            ? "Enable pause at end of layer " + base.pauseAtLayerCandidate
                            : (base.pauseAtLayerUnavailableText.length > 0
                                ? base.pauseAtLayerUnavailableText
                                : "Pause unavailable")))
''',
    '''                text: base.pauseAtLayerCandidate <= 0
                    ? "⏸  Pause at end of selected layer"
                    : (base.pauseAtLayerScheduled
                        ? "Remove pause after layer " + base.pauseAtLayerCandidate
                        : "⏸  Enable pause at end of layer " + base.pauseAtLayerCandidate)
''',
    "pause button action wording",
)
replace_once(
    qml,
    '''                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            Column
''',
    '''                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            UM.Label
            {
                visible: pauseAtLayerButton.visible
                    && !base.pauseAtLayerScheduled
                    && !base.pauseAtLayerCanToggle
                    && base.pauseAtLayerUnavailableText.length > 0
                width: parent.width
                height: visible ? implicitHeight : 0
                text: "Can't schedule: " + base.pauseAtLayerUnavailableText
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                wrapMode: Text.WordWrap
            }

            Column
''',
    "pause unavailable explanation",
)

pause_tests = ROOT / "tests" / "test_pause_at_layer.py"
replace_once(
    pause_tests,
    '''        self.assertIn('"Enable pause at end of layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause after layer " + base.pauseAtLayerCandidate', QML)
''',
    '''        self.assertIn('"⏸  Enable pause at end of layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause after layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('text: "Can\\'t schedule: " + base.pauseAtLayerUnavailableText', QML)
''',
    "pause UX assertions",
)

print("Made pause action explicit with icon and separate unavailable reason")
