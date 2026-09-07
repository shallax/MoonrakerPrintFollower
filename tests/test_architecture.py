"""The architecture contract: document assertions, source laws and composition.

Every rule in ARCHITECTURE.md that can be checked mechanically is checked
here. Behavioural details of the tested components live in their own files.
"""
import ast
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
ARCH = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

RETIRED = {
    "FollowerBootstrap", "FollowerConfiguration", "FollowerCoordinator", "FollowerTransport",
    "CuraLifecycleRuntime", "CuraViewBridge", "CuraFileLifecycle", "PreviewFollowerRuntime",
    "PreviewStatus", "PreviewEta", "PreviewControls", "PreviewLoad", "PreviewFollowEngine",
    "PathFollowEngine", "GCodeIndexRuntime", "RemoteFileTransfer", "PreviewFollowerService",
    "MoonrakerMonitorRuntime", "MoonrakerMonitorControls", "MoonrakerMonitorTypedControls",
    "MoonrakerOutputDeviceLifecycle", "NativeNozzleFallback", "FollowerStateBridge",
    "FollowerSession", "MoonrakerMonitorSession", "MoonrakerOutputSession",
}

RUNTIME_COMPONENTS = (
    "PrinterBinding.py", "CuraIntegration.py", "PreviewFollower.py",
    "PreviewPresentation.py", "PrintCoordinator.py", "RemoteFileService.py",
    "GCodeIndexService.py", "PauseController.py", "BedMeshPresenter.py",
)


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


class SourceContractTests(unittest.TestCase):
    def test_release_metadata_and_license_remain_canonical(self):
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((PLUGINS / "plugin.json").read_text())
        self.assertEqual(package["package_version"], "3.1.0")
        self.assertEqual(plugin["version"], "3.1.0")
        self.assertEqual(package["package_id"], "Moonraker_Print_Follower")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text())

    def test_retired_runtime_is_removed_not_hidden_behind_shims(self):
        for name in RETIRED:
            self.assertFalse((PLUGINS / (name + ".py")).exists(), name)
        for path in PLUGINS.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ClassDef):
                    self.assertFalse(node.name.endswith("Mixin"), path.name)
                if isinstance(node, ast.FunctionDef):
                    self.assertNotIn(node.name, {"__getattr__", "__setattr__"}, path.name)
                if isinstance(node, ast.ImportFrom) and node.level:
                    self.assertNotIn(node.module, RETIRED, path.name)

    def test_components_import_only_their_declared_dependencies(self):
        allowed = {
            "RemoteFileService": {"Core", "DownloadStream", "MoonrakerProtocol"},
            "GCodeIndexService": {"GCodeIndex"},
            "PrintState": {"RemoteJobService"},
            "PreviewFollower": {"Core", "CuraAdapter", "FollowController", "MoonrakerProtocol"},
            "PauseController": {"PauseScheduleService"},
            "PrinterBinding": {"CuraAdapter", "PrinterConfig"},
            "MonitorData": {"MoonrakerSession"},
            "MonitorCommands": set(), "MonitorTuning": set(), "MonitorCamera": set(),
            "MonitorControls": {"MonitorFormatting"}, "MonitorFormatting": set(),
            "UploadController": {"PrinterConfig"}, "CuraOutputWriter": set(),
        }
        for module, dependencies in allowed.items():
            source = (PLUGINS / (module + ".py")).read_text()
            imported = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.level}
            self.assertLessEqual(imported, dependencies, module)
            self.assertNotIn("_follower", source, module)
            if module not in {"PrinterBinding", "CuraOutputWriter"}:
                self.assertNotIn("from cura.", source, module)

    def test_local_import_graph_is_acyclic(self):
        graph = {path.stem: {n.module for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.ImportFrom) and n.level and n.module}
            for path in PLUGINS.glob("*.py") if path.stem != "__init__"}
        done = set()
        def visit(name, stack):
            if name in done or name not in graph: return
            self.assertNotIn(name, stack, " -> ".join(stack + [name]))
            for dependency in graph[name]: visit(dependency, stack + [name])
            done.add(name)
        for name in graph: visit(name, [])

    def test_external_integrations_never_access_private_follower_members(self):
        for path in PLUGINS.glob("*.py"):
            if path.name in {"MoonrakerPrintFollower.py", "FollowerRuntime.py"}: continue
            source = path.read_text()
            self.assertIsNone(re.search(r"(?:self\.)?_follower\._\w+", source), path.name)
            self.assertIsNone(re.search(r'(?:getattr|setattr)\([^,]*follower,\s*[\"\']_', source), path.name)

    def test_only_shared_transport_constructs_network_managers(self):
        owners = []
        for path in PLUGINS.glob("*.py"):
            source = path.read_text()
            if "QNetworkAccessManager(" in source: owners.append(path.name)
            self.assertNotIn("QWebSocket", source, path.name)
            self.assertNotIn("_pref_str(", source, path.name)
            self.assertNotIn("_pref_bool(", source, path.name)
        self.assertEqual(owners, ["MoonrakerTransport.py"])

    def test_qt_adapters_do_not_own_worker_or_http_implementations(self):
        for module in ("MoonrakerPrintFollower", "MoonrakerMonitorModel", "MoonrakerOutputDevice"):
            source = (PLUGINS / (module + ".py")).read_text()
            for forbidden in ("QNetworkAccessManager", "QNetworkReply", "ThreadPoolExecutor", "Thread(", "build_index_from_file"):
                self.assertNotIn(forbidden, source, module)

    def test_preview_qml_keeps_public_workflow(self):
        panel = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
        empty = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text()
        self.assertIn('text: "Load current print"', panel)
        self.assertIn('base.followingPaused ? "Attach" : "Detach"', panel)
        self.assertNotIn('base.followingPaused ? "Attach" : "Detach"', empty)
        self.assertIn("This does not pause the printer.", panel)

    def test_removed_preference_api_and_private_follower_access_are_absent(self):
        for path in PLUGINS.glob("*.py"):
            source = path.read_text()
            self.assertNotIn("self._pref_", source, path.name)
            self.assertNotIn("self._follower._", source, path.name)

    def test_runtime_sources_do_not_contain_release_nicknames(self):
        for path in PLUGINS.iterdir():
            if path.suffix in {".py", ".qml"}:
                self.assertIsNone(re.search(r"\bv3\b", path.read_text(), re.I), path.name)

    def test_source_contains_no_private_network_examples_or_literal_api_key(self):
        candidates = list(PLUGINS.rglob("*")) + list((ROOT / "tools").rglob("*")) + list(ROOT.glob("*"))
        text = "\n".join(p.read_text(errors="replace") for p in candidates if p.is_file() and p.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"})
        for pattern in (r"\b(?:10|127)\.\d+\.\d+\.\d+\b", r"\b192\.168\.\d+\.\d+\b", r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b"):
            self.assertIsNone(re.search(pattern, text))
        self.assertIsNone(re.search(r"api_key\s*[=:]\s*[\"'][^\"']+[\"']", text, re.I))


class CompositionStructureTests(unittest.TestCase):
    """The composition rules: what constructs what, and what nothing else owns."""

    def test_exact_service_boundaries_exist_without_duplicate_wrappers(self):
        required = (
            "RemoteJobService.py", "RemoteFileService.py", "GCodeIndexService.py",
            "PreviewFollower.py", "PauseScheduleService.py", "CuraLifecycleBridge.py",
            "MoonrakerSession.py",
        )
        for name in required:
            self.assertTrue((PLUGINS / name).is_file(), name)
        redundant = (
            "PrintTracker.py", "GCodeRepository.py", "PreviewController.py", "PauseScheduler.py",
            "FollowerSession.py", "FollowerStateBridge.py", "MoonrakerMonitorSession.py",
            "MoonrakerOutputSession.py", "NativeNozzleFallback.py",
        )
        for name in redundant:
            self.assertFalse((PLUGINS / name).exists(), name)

    def test_runtime_is_construction_and_teardown_only(self):
        tree = ast.parse((PLUGINS / "FollowerRuntime.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        self.assertEqual(cls.bases, [])
        self.assertEqual({n.name for n in cls.body if isinstance(n, ast.FunctionDef)}, {"__init__", "close"})
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "super" for n in ast.walk(cls)))

    def test_runtime_composition_is_complete_and_has_no_private_http_stack(self):
        runtime = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
        tree = ast.parse(runtime)
        relative_modules = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
        }
        for name in RUNTIME_COMPONENTS:
            module = name[:-3]
            self.assertIn(module, relative_modules, module)
            self.assertTrue((PLUGINS / name).is_file(), name)
        implementation = "\n".join(
            (PLUGINS / name).read_text(encoding="utf-8")
            for name in ("FollowerRuntime.py",) + RUNTIME_COMPONENTS
        )
        self.assertNotIn("QNetworkAccessManager", implementation)
        self.assertNotIn("QNetworkRequest", implementation)

    def test_domain_constructors_do_not_accept_whole_follower_or_model(self):
        for name in ("RemoteFileService", "GCodeIndexService", "PreviewFollower", "PauseController", "MonitorData", "MonitorCommands", "MonitorControls", "MonitorCamera", "MonitorTuning", "UploadController"):
            source = (PLUGINS / (name + ".py")).read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                    self.assertFalse({arg.arg for arg in node.args.args} & {"follower", "model", "context"}, name)
            self.assertNotIn("__getattr__", source)

    def test_public_follower_is_thin_and_coordinator_uses_exact_services(self):
        facade = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")
        root = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
        coordinator = (PLUGINS / "PrintCoordinator.py").read_text(encoding="utf-8")
        self.assertLess(len(facade.splitlines()), 70)
        self.assertIn("class MoonrakerPrintFollower(QObject, Extension)", facade)
        for token in (
            "RemoteFileService", "GCodeIndexService", "PreviewFollower", "PauseController", "CuraIntegration",
        ):
            self.assertIn(token, root)
        self.assertIn("RemoteJobService", coordinator)
        self.assertNotIn("Mixin", root)

    def test_monitor_core_and_peripheral_json_use_shared_transport(self):
        client = (PLUGINS / "MoonrakerClient.py").read_text(encoding="utf-8")
        session = (PLUGINS / "MoonrakerSession.py").read_text(encoding="utf-8")
        monitor = (PLUGINS / "MonitorData.py").read_text(encoding="utf-8")
        transport = (PLUGINS / "MoonrakerTransport.py").read_text(encoding="utf-8")
        self.assertIn("MoonrakerSession", client)
        self.assertIn("self._session.transport.send_json", client)
        self.assertIn("MoonrakerHttpTransport", session)
        self.assertIn("transport.send_json", monitor)
        self.assertIn("client.force_refresh", monitor)
        self.assertNotIn("status_endpoint", monitor)
        self.assertNotIn("QNetworkAccessManager", monitor)
        self.assertIn("QNetworkAccessManager", transport)
        self.assertNotIn("QNetworkAccessManager", client)
        self.assertNotIn("QWebSocket", client)
        self.assertNotIn("websocket", client.lower())

    def test_output_and_follower_reuse_shared_transport(self):
        output = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        follower_transport = (PLUGINS / "RemoteFileService.py").read_text(encoding="utf-8")
        self.assertIn("transport.send_json", output)
        self.assertIn("transport.network.post", output)
        self.assertNotIn("QNetworkAccessManager", output)
        self.assertIn("self._transport.send_json", follower_transport)
        self.assertIn("self._transport.request", follower_transport)
        self.assertIn("self._transport.network.get", follower_transport)

    def test_connection_probe_reuses_transport_implementation_but_is_isolated(self):
        action = (PLUGINS / "MoonrakerFollowerMachineAction.py").read_text(encoding="utf-8")
        self.assertIn("MoonrakerHttpTransport", action)
        self.assertIn("self._probe_transport", action)
        self.assertIn("self._probe_transport.send_json", action)
        self.assertNotIn("QNetworkAccessManager", action)

    def test_transport_centralizes_cancellation_generation_and_observability(self):
        source = (PLUGINS / "MoonrakerTransport.py").read_text(encoding="utf-8")
        for token in (
            "self._generation", "cancel_owner", "cancel_all", "request_id",
            "category=", "elapsed_ms=", "TransportMetrics", "average_elapsed_ms",
        ):
            self.assertIn(token, source)

    def test_output_reuses_shared_readiness(self):
        source = (PLUGINS / "UploadController.py").read_text(encoding="utf-8")
        self.assertIn("self._client.connected", source)
        self.assertIn("self._upload()", source)
        self.assertNotIn("getvalue()", source)
        self.assertIn("part.setBodyDevice(file)", source)

    def test_active_machine_switch_resets_session_before_new_binding(self):
        source = (PLUGINS / "PrinterBinding.py").read_text(encoding="utf-8")
        start = source.index("def _machine_changed")
        end = source.index("def _apply", start)
        switch = source[start:end]

        invalidate_pos = switch.index("self._client.stop()")
        assign_pos = switch.index("self._machine_id, self._machine_name = machine_id, name")
        apply_pos = switch.index("self._apply()")

        self.assertLess(invalidate_pos, assign_pos)
        self.assertLess(assign_pos, apply_pos)

        client = (PLUGINS / "MoonrakerClient.py").read_text(encoding="utf-8")
        self.assertIn("def stop(self, *, reset_session: bool = True)", client)
        self.assertIn("if reset_session:\n            self._session.reset()", client)

    def test_specialised_follower_replies_validate_lifecycle_and_job_identity(self):
        files = (PLUGINS / "RemoteFileService.py").read_text()
        pause = (PLUGINS / "PauseController.py").read_text()
        upload = (PLUGINS / "UploadController.py").read_text()
        for source in (files, pause):
            self.assertIn("generation != self._generation", source)
            self.assertIn("job != self._job", source)
        self.assertIn("self._session == self._client.session.generation", upload)
        self.assertIn("self._machine_id == self._active_identity()[0]", upload)


if __name__ == "__main__":
    unittest.main()
