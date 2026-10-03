"""Qualified package boundaries and feature ownership, not basename lookup.

The existing architecture contract checks individual collaborators. This gate
also checks the package direction and proves nested imports remain visible.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "mpf"


def module_name(path, root=ROOT):
    parts = path.relative_to(root).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("mpf", *parts))


def imported_modules(source, module, known, *, package=False):
    """Resolve local imports at any depth, including package-import aliases."""
    parent = module.split(".") if package else module.split(".")[:-1]
    result = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names
                          if alias.name == "mpf" or alias.name.startswith("mpf."))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(parent):
                    raise ValueError(f"{module}: import escapes its package")
                parts = parent[:len(parent) - node.level + 1]
                target = ".".join(parts + (node.module.split(".") if node.module else []))
            else:
                target = node.module or ""
            if target != "mpf" and not target.startswith("mpf."):
                continue
            children = {target + "." + alias.name for alias in node.names
                        if target + "." + alias.name in known}
            result.update(children or {target})
    return result


def underneath(module, package):
    return module == package or module.startswith(package + ".")


# Exact module exceptions are intentional: allowing the whole diagnostics or
# Moonraker package here would let a core service acquire a screen/transport.
RULES = {
    "mpf.geometry": (("mpf.geometry",), ()),
    "mpf.printing": (("mpf.printing",), ()),
    "mpf.gcode": (("mpf.gcode", "mpf.geometry", "mpf.printing"),
                  ("mpf.moonraker.MoonrakerProtocol",)),
    "mpf.settings": (("mpf.settings",), ()),
    "mpf.moonraker": (("mpf.moonraker",), ("mpf.diagnostics.CameraTiming",)),
    "mpf.files.browser": (("mpf.files.browser",), ("mpf.moonraker.MoonrakerProtocol",)),
    "mpf.files.transfers": (("mpf.files.transfers", "mpf.moonraker"),
                           ("mpf.settings.PrinterConfig",)),
}


def allowed_dependency(source, target):
    for owner, (packages, modules) in RULES.items():
        if underneath(source, owner):
            return target in modules or any(underneath(target, package) for package in packages)
    return True  # Presentation and application composition have broader collaborators.


class DomainLayoutTests(unittest.TestCase):
    def test_all_local_imports_resolve_and_core_packages_do_not_import_screens(self):
        modules = {module_name(path): path for path in ROOT.rglob("*.py")}
        self.assertGreater(len(modules), 90, "the recursive scan found too little source")
        self.assertIn("mpf.monitor.camera.MonitorCamera", modules)
        edges = 0
        for module, path in modules.items():
            targets = imported_modules(path.read_text(encoding="utf-8"), module, modules,
                                       package=path.name == "__init__.py")
            for target in targets:
                edges += 1
                self.assertIn(target, modules, f"{module} imports missing {target}")
                self.assertTrue(allowed_dependency(module, target),
                                f"forbidden package dependency: {module} -> {target}")
        self.assertGreater(edges, 100, "the import scan silently missed dependency edges")

    def test_nested_module_and_package_imports_are_both_resolved(self):
        known = {"mpf.monitor.camera.MonitorCamera", "mpf.gcode.ArcGeometry"}
        source = "from ..monitor.camera.MonitorCamera import MonitorCamera\nfrom . import ArcGeometry\n"
        self.assertEqual(imported_modules(source, "mpf.gcode.Index", known), known)
        self.assertEqual(imported_modules("from .camera import MonitorCamera", "mpf.monitor", known,
                                          package=True), {"mpf.monitor.camera.MonitorCamera"})

    def test_nested_screen_import_cannot_evade_the_core_boundary(self):
        self.assertFalse(allowed_dependency("mpf.gcode.Index", "mpf.monitor.camera.MonitorCamera"))
        self.assertFalse(allowed_dependency("mpf.gcode.Index", "mpf.plate.PreviewColours"))
        self.assertFalse(allowed_dependency("mpf.printing.PrintState", "mpf.preview.PreviewFollower"))
        self.assertFalse(allowed_dependency("mpf.moonraker.MoonrakerClient", "mpf.diagnostics.LeakProbe"))
        self.assertTrue(allowed_dependency("mpf.gcode.Index", "mpf.geometry.Polygons"))
        self.assertTrue(allowed_dependency("mpf.moonraker.MoonrakerClient", "mpf.diagnostics.CameraTiming"))

    def test_owned_feature_files_are_colocated(self):
        features = {
            "monitor/camera": ("CameraPane.qml", "MonitorCamera.py", "MoonrakerMJPGImage.py", "CameraBridge.py"),
            "detection": ("DetectionPolicy.py", "LocalFailureModel.py", "DetectionAssets.py", "AssetInstaller.py", "LocalDetectionService.py", "DetectionOffer.qml"),
            "monitor/console": ("ConsoleController.py", "ConsolePolicy.py"),
            "monitor/temperature": ("TemperatureChart.qml", "MonitorTemperatureHistory.py"),
            "monitor/toolhead": ("ToolheadSection.qml", "ToolheadController.py", "ToolheadPolicy.py"),
            "files/browser": ("FileManager.qml", "FileManager.py", "FileManagerPolicy.py", "FilesViewModel.py"),
            "files/transfers": ("UploadController.py", "MoonrakerUploadDialog.qml", "FileDownload.py", "RemoteFileService.py", "DownloadStream.py"),
            "preview": ("PreviewFollower.py", "PreviewPresentation.py", "MoonrakerPreviewCard.qml"),
            "printing": ("PrintState.py", "RemoteJobService.py", "PauseScheduleService.py"),
            "settings": ("PrinterConfig.py", "PluginPersistence.py", "MoonrakerFollowerConfiguration.qml"),
            "bedmesh": ("BedMeshPresenter.py", "BedMeshSceneNode.py", "BedMeshMap.qml", "BedMeshRangeSlider.qml"),
            "gcode": ("GCodeIndex.py", "GCodeIndexService.py", "ArcGeometry.py", "TravelStates.py", "PlateProgress.py", "MotionRanges.py"),
        }
        for owner, names in features.items():
            for name in names:
                self.assertTrue((ROOT / owner / name).is_file(), f"{owner} must own {name}")
        for retired in ("printer", "index", "filemanager"):
            self.assertFalse((ROOT / retired).exists(), f"retired package {retired} must not be a forwarding shim")


if __name__ == "__main__":
    unittest.main()
