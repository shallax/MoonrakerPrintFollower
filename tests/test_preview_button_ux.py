import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
BUTTON = (PLUGINS / "PreviewSecondaryButton.qml").read_text(encoding="utf-8")
PANEL = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")
EMPTY = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text(encoding="utf-8")


class PreviewButtonUxTests(unittest.TestCase):
    def test_preview_button_centres_text_both_axes(self):
        self.assertIn("horizontalAlignment: Text.AlignHCenter", BUTTON)
        self.assertIn("verticalAlignment: Text.AlignVCenter", BUTTON)
        self.assertIn("elide: Text.ElideRight", BUTTON)
        self.assertIn('textColor: "transparent"', BUTTON)

    def test_preview_controls_use_centred_wrapper(self):
        self.assertGreaterEqual(PANEL.count("PreviewSecondaryButton"), 6)
        self.assertNotIn("Cura.SecondaryButton", PANEL)
        self.assertGreaterEqual(EMPTY.count("PreviewSecondaryButton"), 2)
        self.assertNotIn("Cura.SecondaryButton", EMPTY)

    def test_following_control_is_detach_attach_not_printer_pause(self):
        self.assertIn('text: base.followingPaused ? "Attach" : "Detach"', PANEL)
        self.assertIn("This does not pause the printer.", PANEL)
        self.assertIn("Attach Cura Preview to the live Moonraker print", PANEL)

    def test_load_button_gets_majority_of_top_row(self):
        self.assertIn("* 0.32", PANEL)
        self.assertIn("buttons.width - base.buttonSpacing - followButton.width", PANEL)
        self.assertIn('text: "Load current print"', PANEL)
        self.assertIn("contentWidth: 300 * screenScaleFactor", PANEL)

    def test_pause_action_keeps_pause_symbol(self):
        self.assertIn('"⏸  Pause at end of selected layer"', PANEL)
        self.assertIn('"⏸  Enable pause at end of layer "', PANEL)


if __name__ == "__main__":
    unittest.main()
