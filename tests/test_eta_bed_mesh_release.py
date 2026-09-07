import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
PREVIEW_ETA = (PLUGINS / "PreviewEta.py").read_text(encoding="utf-8")
PREVIEW_STATUS = (PLUGINS / "PreviewStatus.py").read_text(encoding="utf-8")
FOLLOW_ENGINE = (PLUGINS / "PreviewFollowEngine.py").read_text(encoding="utf-8")
PREVIEW_SERVICE = (PLUGINS / "PreviewFollowerService.py").read_text(encoding="utf-8")
FOLLOWER = "\n".join([PREVIEW_ETA, PREVIEW_STATUS, FOLLOW_ENGINE, PREVIEW_SERVICE])
PREVIEW = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")
TYPED = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text(encoding="utf-8")
DASHBOARD = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")


class EtaAndBedMeshReleaseTests(unittest.TestCase):
    def test_selected_layer_eta_uses_live_remote_layer_and_duration_anchor(self):
        self.assertIn("def _estimate_layer_boundary_remaining", PREVIEW_ETA)
        self.assertIn("runtime.observed_remote_layer", FOLLOWER)
        self.assertIn("tracking.eta_anchor_print_duration", FOLLOWER)
        self.assertIn("tracking.eta_current_print_duration", FOLLOWER)
        self.assertIn("self._preview_follower_service.observe_remote_layer", FOLLOW_ENGINE)
        self.assertIn("class PreviewTrackingState", PREVIEW_SERVICE)
        self.assertIn("class PreviewRuntimeState", PREVIEW_SERVICE)
        self.assertIn("actual_into_layer * speed / planned_layer_duration", PREVIEW_ETA)
        self.assertIn("self._remote_job_service.printer_state in self.ACTIVE_STATES", PREVIEW_ETA)
        for retired in (
            "self._last_observed_remote_layer",
            "self._last_remote_state",
            "self._eta_anchor_print_duration",
            "self._eta_current_print_duration",
        ):
            self.assertNotIn(retired, FOLLOWER)

    def test_each_scheduled_pause_has_a_live_end_of_layer_eta(self):
        self.assertIn("end_of_layer=True", PREVIEW_STATUS)
        self.assertIn('items.append({"layer": layer + 1, "eta": eta})', PREVIEW_STATUS)
        self.assertIn("property string pauseEta", PREVIEW)
        self.assertIn('" · " + parent.pauseEta', PREVIEW)

    def test_saved_bed_mesh_profiles_are_discovered_and_loadable(self):
        self.assertIn("_bed_mesh_profiles_from_status", TYPED)
        self.assertIn('status.get("profiles")', TYPED)
        self.assertIn("bedMeshProfileNames", TYPED)
        self.assertIn("loadBedMeshProfile", TYPED)
        self.assertIn("shlex.quote(name)", TYPED)
        self.assertIn('BED_MESH_PROFILE LOAD={safe}', TYPED)
        self.assertIn('text: "Load saved mesh"', DASHBOARD)
        self.assertIn("bedMeshProfileSelector", DASHBOARD)

    def test_active_bed_mesh_can_be_cleared_without_deleting_saved_profiles(self):
        self.assertIn("def clearBedMesh", TYPED)
        self.assertIn('"BED_MESH_CLEAR"', TYPED)
        self.assertIn('text: "Clear mesh"', DASHBOARD)
        self.assertIn("root.printer.bedMeshAvailable", DASHBOARD)
        self.assertIn('text: "Calibrate mesh"', DASHBOARD)


if __name__ == "__main__":
    unittest.main()
