"""Executable qt settings integration contracts."""
from tests import qt_integration_support as harness

class SettingsSaveRefusalTests(harness.SettingsSaveRefusalTests):
    def test_a_saved_setting_reports_success(self):
        _, follower = self._follower()
        action = self._action(follower)
        changed = []
        action.settingsChanged.connect(lambda: changed.append(True))
        self.assertTrue(action.saveConfig(self._params()))
        self.assertEqual(len(changed), 1)

    def test_a_refused_write_is_not_reported_as_saved(self):
        _, follower = self._follower()
        action = self._action(follower)
        # A first, successful save: there is a live configuration to
        # fall back to.
        self.assertTrue(action.saveConfig(self._params()))
        self.fail_save = True
        changed = []
        action.settingsChanged.connect(lambda: changed.append(True))
        self.assertFalse(action.saveConfig(self._params(url="http://moved:7125")))
        self.assertEqual(changed, [])
        # The refused write left the document alone: the previous usable
        # connection is what remains configured.
        self.assertEqual(follower.current_printer_config().url, "http://printer-a:7125")
        # And the disk recovering brings the dialog's success back.
        self.fail_save = False
        self.assertTrue(action.saveConfig(self._params(url="http://moved:7125")))
        self.assertEqual(follower.current_printer_config().url, "http://moved:7125")


