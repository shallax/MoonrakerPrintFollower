import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCH = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")


class ArchitectureDocumentTests(unittest.TestCase):
    def test_document_matches_runtime_composition_and_service_ownership(self):
        self.assertIn("`FollowerRuntime.py` constructs and closes", ARCH)
        for module in (
            "PrinterBinding.py", "CuraIntegration.py", "PreviewPresentation.py", "PreviewFollower.py",
            "PrintCoordinator.py", "PrintState.py", "RemoteFileService.py", "GCodeIndexService.py",
            "MonitorData.py", "MonitorCommands.py", "MonitorTuning.py", "MonitorControls.py", "MonitorCamera.py",
            "BedMeshPresenter.py", "UploadController.py", "CuraOutputWriter.py",
        ):
            self.assertIn(f"`{module}`", ARCH)

    def test_document_records_preview_reset_scopes(self):
        self.assertIn("PreviewState", ARCH)
        self.assertIn("`reset_tracking()`", ARCH)
        self.assertIn("`reset_print()`", ARCH)
        self.assertIn("immutable print observations", ARCH)

    def test_document_records_compatibility_and_shared_polling(self):
        self.assertIn("Legacy follower preferences", ARCH)
        self.assertIn("Standalone Moonraker Connection settings", ARCH)
        self.assertIn("Monitor auxiliary, idle", ARCH)
        self.assertIn("2500 ms", ARCH)
        self.assertIn("`MonitorData` alone applies Monitor timer policy", ARCH)

    def test_document_records_output_rebind_cleanup_and_network_law(self):
        self.assertIn("`MoonrakerClient.sessionInvalidated`", ARCH)
        self.assertIn("QHttpPart.setBodyDevice()", ARCH)
        self.assertIn("only production module constructing", ARCH)
        self.assertIn("HTTP only — no WebSocket transport", ARCH)

    def test_document_distinguishes_harness_from_live_cura_validation(self):
        self.assertIn("The harness is not Cura or printer firmware", ARCH)
        self.assertIn("Stdlib-only local runs explicitly skip", ARCH)


if __name__ == "__main__":
    unittest.main()
