from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


follower = ROOT / "plugins" / "MoonrakerPrintFollower.py"
replace_once(
    follower,
    "            and selected_layer > current\n            and not is_final_layer\n",
    "            and selected_layer >= current\n            and not is_final_layer\n",
    "current-layer scheduling eligibility",
)
replace_once(
    follower,
    "            elif selected_layer <= current:\n                unavailable = f\"Layer {selected_layer + 1} already reached\"\n",
    "            elif selected_layer < current:\n                unavailable = f\"Layer {selected_layer + 1} already printed\"\n",
    "already-printed wording",
)

qml = ROOT / "plugins" / "PreviewActionPanelControls.qml"
replace_once(
    qml,
    '                        : "Scroll Cura Preview to a future non-final layer to schedule an end-of-layer PAUSE.")\n',
    '                        : "Scroll Cura Preview to the current or a future non-final layer to schedule an end-of-layer PAUSE.")\n',
    "pause tooltip current-layer wording",
)

pause_tests = ROOT / "tests" / "test_pause_at_layer.py"
replace_once(
    pause_tests,
    "    def test_preview_can_toggle_and_manage_multiple_future_layers(self):\n",
    "    def test_preview_can_toggle_and_manage_current_or_future_layers(self):\n",
    "pause test name",
)
replace_once(
    pause_tests,
    '        self.assertIn("selected_layer > current", FOLLOWER)\n',
    '        self.assertIn("selected_layer >= current", FOLLOWER)\n'
    '        self.assertIn("selected_layer < current", FOLLOWER)\n'
    '        self.assertIn("already printed", FOLLOWER)\n'
    '        self.assertIn("current or a future non-final layer", QML)\n',
    "pause eligibility assertions",
)

print("Enabled end-of-layer PAUSE scheduling for the currently printing layer")
