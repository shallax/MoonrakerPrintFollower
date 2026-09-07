from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

RUNTIME_COMPONENTS = (
    "FollowerBootstrap.py",
    "FollowerConfiguration.py",
    "CuraLifecycleRuntime.py",
    "CuraViewBridge.py",
    "CuraFileLifecycle.py",
    "PreviewFollowerRuntime.py",
    "PreviewStatus.py",
    "PreviewEta.py",
    "PreviewControls.py",
    "PreviewLoad.py",
    "PreviewFollowEngine.py",
    "PathFollowEngine.py",
    "GCodeIndexRuntime.py",
    "RemoteFileTransfer.py",
)


class V31RuntimeSplitTests(unittest.TestCase):
    def test_runtime_imports_every_focused_component(self):
        source = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
        }
        for filename in RUNTIME_COMPONENTS:
            module = filename[:-3]
            self.assertIn(module, imported, module)
            self.assertTrue((PLUGINS / filename).is_file(), filename)

    def test_runtime_mixins_do_not_shadow_each_other(self):
        owners = {}
        for filename in RUNTIME_COMPONENTS:
            tree = ast.parse((PLUGINS / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or not node.name.endswith("Mixin"):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    previous = owners.get(item.name)
                    if previous is not None and previous != filename:
                        self.fail(f"{item.name} is implemented by both {previous} and {filename}")
                    owners.setdefault(item.name, filename)

    def test_extracted_services_are_used_as_policy_owners(self):
        lifecycle = (PLUGINS / "CuraLifecycleRuntime.py").read_text(encoding="utf-8")
        index_runtime = (PLUGINS / "GCodeIndexRuntime.py").read_text(encoding="utf-8")
        preview = (PLUGINS / "PreviewFollowEngine.py").read_text(encoding="utf-8")
        coordinator = (PLUGINS / "FollowerCoordinator.py").read_text(encoding="utf-8")

        self.assertIn("bridge.invalidate(reason)", lifecycle)
        self.assertIn("self._gcode_index_service.begin_build", index_runtime)
        self.assertIn("self._gcode_index_service.invalidate_build", index_runtime)
        self.assertIn("self._gcode_index_service.begin_hydration", index_runtime)
        self.assertIn("self._gcode_index_service.finish_hydration", index_runtime)
        self.assertIn("self._preview_follower_service.apply_layer_decision", preview)
        self.assertIn("self._remote_job_service.observe", coordinator)
        self.assertIn("self._remote_file_service.adopt", coordinator)
        self.assertIn("self._pause_schedule_service.consume_due", coordinator)

    def test_shutdown_has_no_pre_client_legacy_poll_timer(self):
        lifecycle = (PLUGINS / "CuraFileLifecycle.py").read_text(encoding="utf-8")
        self.assertNotIn("self._timer.stop()", lifecycle)
        self.assertIn("self._client.stop()", lifecycle)

    def test_monitor_and_output_are_natively_shared_transport(self):
        monitor = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")
        output = (PLUGINS / "MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "MoonrakerTransport.py").read_text(encoding="utf-8")
        self.assertFalse((PLUGINS / "MoonrakerMonitorSession.py").exists())
        self.assertFalse((PLUGINS / "MoonrakerOutputSession.py").exists())
        self.assertNotIn("QNetworkAccessManager", monitor)
        self.assertNotIn("QNetworkAccessManager", output)
        self.assertIn("transport.send_json", monitor)
        self.assertIn("transport.send_json", output)
        self.assertIn("transport.network.post", output)
        self.assertIn("QNetworkAccessManager", transport)

    def test_focused_runtime_cannot_own_moonraker_http_requests(self):
        source = "\n".join(
            (PLUGINS / name).read_text(encoding="utf-8")
            for name in ("FollowerRuntime.py",) + RUNTIME_COMPONENTS
        )
        self.assertNotIn("QNetworkAccessManager", source)
        self.assertNotIn("QNetworkRequest", source)
        for endpoint in (
            "status_endpoint",
            "metadata_endpoint",
            "download_endpoint",
            "gcode_script_endpoint",
        ):
            self.assertNotIn(endpoint, source)


if __name__ == "__main__":
    unittest.main()
