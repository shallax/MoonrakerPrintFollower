import pathlib
import unittest
from plugins.MonitorFormatting import mesh_profiles

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


class EtaAndBedMeshReleaseTests(unittest.TestCase):
    def test_selected_layer_eta_uses_live_observation_and_anchor(self):
        preview = (PLUGINS / "PreviewFollower.py").read_text()
        self.assertIn("state.observed_layer", preview)
        self.assertIn("state.duration - state.anchor_duration", preview)
        self.assertIn("@dataclass(frozen=True)", preview)
        self.assertIn("def remaining", preview)
        self.assertIn("datetime.now().astimezone()", preview)

    def test_each_scheduled_pause_has_end_of_layer_eta(self):
        coordinator = (PLUGINS / "PrintCoordinator.py").read_text()
        qml = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
        self.assertIn("self._preview.remaining(layer, self._index.view, end=True)", coordinator)
        self.assertIn("property string pauseEta", qml)
        self.assertIn("parent.pauseEta.length > 0", qml)

    def test_saved_profiles_are_ordered_and_loadable(self):
        self.assertEqual(mesh_profiles({"profiles": {"summer": {}, "default": {}, "winter": {}}, "profile_name": "winter"}), ["winter", "default", "summer"])
        controls = (PLUGINS / "MonitorControls.py").read_text()
        self.assertIn("shlex.quote(name)", controls)
        self.assertIn("BED_MESH_PROFILE LOAD=", controls)
        dashboard = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
        self.assertIn('text: "Load saved mesh"', dashboard)

    def test_clearing_active_mesh_does_not_delete_saved_profiles(self):
        controls = (PLUGINS / "MonitorControls.py").read_text()
        self.assertIn('"BED_MESH_CLEAR"', controls)
        self.assertNotIn("BED_MESH_PROFILE REMOVE", controls)
        dashboard = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()
        self.assertIn('text: "Clear mesh"', dashboard)
        self.assertIn('text: "Calibrate mesh"', dashboard)


if __name__ == "__main__": unittest.main()
