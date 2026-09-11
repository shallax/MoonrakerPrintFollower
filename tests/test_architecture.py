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
    "Core", "FollowerBootstrap", "FollowerConfiguration", "FollowerCoordinator", "FollowerTransport",
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
    """Structural anchors and rule tokens, not sentence-level prose checks.

    Rewording the doc must not fail CI; dropping a rule, a section or a
    component from it must.
    """

    def test_document_keeps_its_section_structure(self):
        for heading in (
            "## 1. Design rules", "## 2. Composition roots and public APIs",
            "## 3. Binding and migration", "## 4. Shared networking and polling",
            "## 5. Physical state and Preview", "## 6. Remote files, leases and bounded indexing",
            "## 7. Commands and scheduled PAUSE", "## 8. Monitor and bed mesh",
            "## 9. File-backed upload lifecycle", "## 10. Extending the architecture",
            "## 11. Verification and release gates",
        ):
            self.assertIn(heading, ARCH)

    def test_document_names_the_runtime_components_and_services(self):
        for module in (
            "PrinterBinding.py", "PrinterConfig.py", "CuraIntegration.py", "CuraAdapter.py",
            "CuraLifecycleBridge.py", "NativeNozzleLifecycle.py", "FollowController.py",
            "PreviewPresentation.py", "PreviewFollower.py", "PreviewFormatting.py",
            "PreviewMotion.py", "PreviewSmoothing.py",
            "PrintCoordinator.py", "PrintState.py", "RemoteFileService.py", "DownloadStream.py",
            "GCodeIndexService.py", "MonitorData.py", "MonitorCommands.py", "MonitorTuning.py",
            "MonitorControls.py", "MonitorFormatting.py", "MonitorCamera.py", "BedMeshPresenter.py",
            "BedMeshSceneNode.py", "MoonrakerMonitorModel.py", "MoonrakerFollowerMachineAction.py",
            "MoonrakerProtocol.py", "UploadController.py", "CuraOutputWriter.py",
            "ToolheadPolicy.py", "ToolheadController.py", "MonitorTemperatureHistory.py", "ConsolePolicy.py", "ConsoleController.py",
            "FileManagerPolicy.py", "FileManager.py",
        ):
            self.assertIn(f"`{module}`", ARCH)

    def test_document_records_preview_reset_scopes(self):
        self.assertIn("PreviewState", ARCH)
        self.assertIn("`reset_tracking()`", ARCH)
        self.assertIn("`reset_print()`", ARCH)
        self.assertIn("immutable print observations", ARCH)

    def test_document_records_polling_cadence_and_migration(self):
        self.assertIn("Legacy follower preferences", ARCH)
        self.assertIn("Standalone Moonraker Connection settings", ARCH)
        # The exact table row, not the substring: the idle row also says
        # "2500 ms", so a bare substring cannot catch drift in the active row.
        self.assertIn("| Monitor auxiliary, active/paused | 2500 ms |", ARCH)
        self.assertIn("`MonitorData` alone applies Monitor timer policy", ARCH)

    def test_document_records_output_rebind_cleanup_and_network_law(self):
        self.assertIn("`MoonrakerClient.sessionInvalidated`", ARCH)
        self.assertIn("QHttpPart.setBodyDevice()", ARCH)
        self.assertIn("HTTP only", ARCH)
        self.assertIn("no WebSocket transport", ARCH)

    def test_document_distinguishes_harness_from_live_cura_validation(self):
        self.assertIn("The harness is not Cura or printer firmware", ARCH)
        self.assertIn("Stdlib-only local runs explicitly skip", ARCH)

    def test_instructions_document_records_the_version_bump_checklist(self):
        instructions = (ROOT / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        for token in (
            "package.json", "plugins/plugin.json", "CHANGELOG.md", "README.md",
            "release workflow", "v<version>",
        ):
            self.assertIn(token, instructions)


class SourceContractTests(unittest.TestCase):
    def test_release_metadata_and_license_remain_canonical(self):
        package = json.loads((ROOT / "package.json").read_text())
        plugin = json.loads((PLUGINS / "plugin.json").read_text())
        # The absolute version is validated against the git tag by the release
        # workflow; here the two metadata files must stay in sync.
        self.assertEqual(package["package_version"], plugin["version"])
        self.assertNotEqual(package["package_version"], "")
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
            "BedMeshPresenter": {"BedMeshSceneNode"},
            "BedMeshSceneNode": set(),
            "CuraAdapter": set(),
            "CuraIntegration": {"CuraLifecycleBridge", "NativeNozzleLifecycle"},
            "CuraLifecycleBridge": set(),
            "CuraOutputWriter": set(),
            "DownloadStream": set(),
            "FollowController": set(),
            "FollowerRuntime": {"BedMeshPresenter", "CuraIntegration", "FileDownload", "GCodeIndex", "GCodeIndexService",
                "MoonrakerClient", "PauseController", "PreviewFollower", "PreviewMotion",
                "PreviewPresentation", "PrintCoordinator", "PrinterBinding", "RemoteFileService"},
            "GCodeIndex": {"MoonrakerProtocol"},
            "GCodeIndexService": {"GCodeIndex"},
            "MonitorCamera": set(),
            "MonitorCommands": set(),
            "MonitorControls": {"MonitorFormatting"},
            "MonitorData": {"ConsolePolicy", "MonitorFormatting", "MoonrakerSession"},
            "MonitorFormatting": set(),
            "MonitorTuning": set(),
            "MoonrakerClient": {"MoonrakerProtocol", "MoonrakerSession"},
            "MoonrakerFollowerMachineAction": {"FollowController", "MoonrakerProtocol", "MoonrakerSession", "MoonrakerTransport", "PrinterConfig"},
            "MoonrakerMonitorModel": {"ConsoleController", "FileManager", "FileManagerPolicy", "MonitorCamera", "MonitorCommands", "MonitorControls", "MonitorData", "MonitorFormatting", "MonitorTemperatureHistory", "MonitorTuning", "PrinterConfig", "ToolheadController", "ToolheadPolicy"},
            "ConsoleController": {"ConsolePolicy"},
            "ToolheadController": {"ToolheadPolicy"},
            "MonitorTemperatureHistory": {"MonitorFormatting"},
            "ConsolePolicy": set(),
            "ToolheadPolicy": set(),
            "MoonrakerOutputDevice": {"CuraOutputWriter", "UploadController"},
            "MoonrakerOutputDevicePlugin": {"MoonrakerMonitorModel", "MoonrakerOutputDevice"},
            "MoonrakerPrintFollower": {"FollowerRuntime"},
            "MoonrakerProtocol": set(),
            "MoonrakerSession": {"MoonrakerTransport"},
            "MoonrakerTransport": {"MoonrakerProtocol"},
            "NativeNozzleLifecycle": set(),
            "PauseController": {"PauseScheduleService"},
            "PauseScheduleService": set(),
            "PreviewFollower": {"CuraAdapter", "FollowController", "MoonrakerProtocol"},
            "PreviewFormatting": set(),
            "PreviewMotion": {"CuraAdapter", "PreviewSmoothing"},
            "PreviewPresentation": set(),
            "PreviewSmoothing": set(),
            "PrintCoordinator": {"MonitorFormatting", "PreviewFormatting", "PrintState", "RemoteJobService"},
            "PrinterBinding": {"CuraAdapter", "PrinterConfig"},
            "PrinterConfig": set(),
            "PrintState": {"RemoteJobService"},
            "RemoteFileService": {"DownloadStream", "MoonrakerProtocol"},
            "RemoteJobService": set(),
            "UploadController": {"PrinterConfig"},
        }
        # Cura adapters sanctioned to import cura APIs.
        cura_exceptions = {"CuraOutputWriter", "MoonrakerFollowerMachineAction", "MoonrakerMonitorModel", "MoonrakerOutputDevice", "PrinterBinding"}
        # The output plugin and Machine Action receive the follower at the
        # documented composition boundary; PrinterConfig and BedMeshPresenter
        # only contain the string inside preference-key literals.
        follower_exceptions = {"BedMeshPresenter", "MoonrakerFollowerMachineAction", "MoonrakerOutputDevicePlugin",
                               "PrinterConfig", "MoonrakerMonitorModel"}  # plugin-namespaced preference keys / file names
        for module, dependencies in allowed.items():
            source = (PLUGINS / (module + ".py")).read_text()
            imported = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.level}
            self.assertLessEqual(imported, dependencies, module)
            if module not in follower_exceptions:
                self.assertNotIn("_follower", source, module)
            if module not in cura_exceptions:
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

    def test_network_replies_connect_into_bound_handlers_not_bare_closures(self):
        # The author's live crash report: a SIGSEGV in PyQtSlot::call
        # on the main thread, delivered from a QtNetwork signal right
        # after the file-manager popup opened. A bare closure connected
        # to QNetworkReply.finished is a use-after-free trap in PyQt —
        # every reply connection must follow the transport's pattern:
        # the reply registered in a dict, the signal connected via a
        # default-argument lambda into a bound method of the owning
        # QObject, so nothing can be collected mid-flight.
        for path in PLUGINS.glob("*.py"):
            source = path.read_text()
            self.assertIsNone(re.search(r"\.finished\.connect\(finished\)", source), path.name)
            # PyQt6 enums never equal plain ints: ``error() != 0`` is
            # ALWAYS true and failed every successful thumbnail fetch
            # (the author's live report). Compare against the enum.
            self.assertIsNone(re.search(r"\.error\(\)\s*[!=]=\s*0\b", source), path.name)
        manager = (PLUGINS / "FileManager.py").read_text()
        self.assertIn("self._thumb_replies[relpath] = reply", manager)
        self.assertIn(
            "lambda r=reply, p=relpath, g=generation, t=path, l=large: self._thumb_finished(p, r, g, t, l)",
            manager,
        )

    def test_redirect_policy_guards_the_api_key(self):
        # The X-Api-Key rides redirects unless the transport pins the
        # same-origin redirect policy (round-2 security F1) — the pin
        # exists because a dropped policy is invisible to the suite.
        transport = (PLUGINS / "MoonrakerTransport.py").read_text()
        self.assertIn("SameOriginRedirectPolicy", transport)

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

    def test_qt_imports_name_the_module_that_owns_the_class(self):
        # QHostAddress broke the plugin on Cura 5.13's bundled PyQt6:
        # the container's build re-exports it from QtCore, so the gates
        # stayed green while the real Cura raised ImportError at plugin
        # registration. Pin EVERY Qt import against the module that
        # owns the class — newer PyQt6 re-exports liberally, older
        # bundled builds do not.
        owners = {
            "QAbstractListModel": "QtCore",
            "QByteArray": "QtCore",
            "QCoreApplication": "QtCore",
            "QModelIndex": "QtCore",
            "QObject": "QtCore",
            "QPointF": "QtCore",
            "QRect": "QtCore",
            "QSettings": "QtCore",
            "QThread": "QtCore",
            "QTimer": "QtCore",
            "QUrl": "QtCore",
            "QVariant": "QtCore",
            "qInstallMessageHandler": "QtCore",
            "QColor": "QtGui",
            "QDesktopServices": "QtGui",
            "QFont": "QtGui",
            "QFontMetrics": "QtGui",
            "QGuiApplication": "QtGui",
            "QImage": "QtGui",
            "QPixmap": "QtGui",
            "QPainter": "QtGui",
            "QColorConstants": "QtGui",
            "QHostAddress": "QtNetwork",
            "QNetworkAccessManager": "QtNetwork",
            "QNetworkReply": "QtNetwork",
            "QNetworkRequest": "QtNetwork",
            "QAbstractAnimation": "QtCore",
            "QEasingCurve": "QtCore",
            "QPropertyAnimation": "QtCore",
        }
        import ast
        for path in PLUGINS.glob("*.py"):
            try:
                tree = ast.parse(path.read_text(), filename=path.name)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not str(node.module or "").startswith("PyQt6."):
                    continue
                imported_module = str(node.module).split(".", 1)[1]
                for alias in node.names:
                    name = alias.name
                    if name in owners and owners[name] != imported_module:
                        self.fail(f"{path.name}: {name} belongs to PyQt6.{owners[name]}, "
                                  f"not PyQt6.{imported_module}")

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
        # The output plugin and Machine Action are the sanctioned composition
        # boundary that receives the follower facade; every other component
        # takes explicit capabilities only.
        for name in ("BedMeshPresenter", "BedMeshSceneNode", "CuraAdapter", "CuraIntegration", "CuraLifecycleBridge",
                     "CuraOutputWriter", "DownloadStream", "FollowController", "GCodeIndex", "GCodeIndexService",
                     "MonitorCamera", "MonitorCommands", "MonitorControls", "MonitorData", "MonitorFormatting",
                     "MonitorTuning", "MoonrakerClient", "MoonrakerMonitorModel", "MoonrakerPrintFollower",
                     "MoonrakerProtocol", "MoonrakerSession", "MoonrakerTransport", "NativeNozzleLifecycle",
                     "PauseController", "PauseScheduleService", "PreviewFollower", "PreviewFormatting",
                     "PreviewMotion", "PreviewPresentation", "PreviewSmoothing", "PrintCoordinator",
                     "PrinterBinding", "PrinterConfig", "PrintState",
                     "ConsoleController", "ConsolePolicy", "RemoteFileService", "RemoteJobService",
                     "ToolheadController", "ToolheadPolicy", "UploadController"):
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
        self.assertIn("_client.transport.send_json", monitor)
        self.assertIn("_client.force_refresh", monitor)
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
