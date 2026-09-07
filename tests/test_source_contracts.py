"""Executable structural laws; behavioural details live in component/Qt tests."""
import ast
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
RETIRED = {
    "FollowerBootstrap", "FollowerConfiguration", "FollowerCoordinator", "FollowerTransport",
    "CuraLifecycleRuntime", "CuraViewBridge", "CuraFileLifecycle", "PreviewFollowerRuntime",
    "PreviewStatus", "PreviewEta", "PreviewControls", "PreviewLoad", "PreviewFollowEngine",
    "PathFollowEngine", "GCodeIndexRuntime", "RemoteFileTransfer", "PreviewFollowerService",
    "MoonrakerMonitorRuntime", "MoonrakerMonitorControls", "MoonrakerMonitorTypedControls",
    "MoonrakerOutputDeviceLifecycle", "NativeNozzleFallback", "FollowerStateBridge",
    "FollowerSession", "MoonrakerMonitorSession", "MoonrakerOutputSession",
}


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

    def test_source_contains_no_private_network_examples_or_literal_api_key(self):
        candidates = list(PLUGINS.rglob("*")) + list((ROOT / "tools").rglob("*")) + list(ROOT.glob("*"))
        text = "\n".join(p.read_text(errors="replace") for p in candidates if p.is_file() and p.suffix.lower() in {".py", ".qml", ".md", ".json", ".txt"})
        for pattern in (r"\b(?:10|127)\.\d+\.\d+\.\d+\b", r"\b192\.168\.\d+\.\d+\b", r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b"):
            self.assertIsNone(re.search(pattern, text))
        self.assertIsNone(re.search(r"api_key\s*[=:]\s*[\"'][^\"']+[\"']", text, re.I))


if __name__ == "__main__": unittest.main()
