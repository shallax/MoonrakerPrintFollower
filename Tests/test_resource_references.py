"""References the engine check cannot see, resolved the runtime way.

A glyph whose URL misses the file is not a component error, it is an
empty slot in the UI, so a document that lost its `../Resources/Svg/`
prefix on a move ships silently. A source that names a plugin file by
path fails the same way: the module reads the wrong file, or the
failure is swallowed and the code falls back.

Two scans, both resolving references the way they resolve at runtime —
the URL against the CALLING DOCUMENT's directory, a path against the
tree root — and failing on anything that is not a file.
"""
import ast
import json
import subprocess
import sys
import tempfile
import zipfile
import pathlib
import re
import unittest

from Tests.source_root import SourceRoot

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = SourceRoot(ROOT / "mpf")

# A quoted string literal, escapes included. Run against the text with
# comments blanked, so a path quoted in prose is not read as a reference.
LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
EXTENSIONS = (".py", ".qml", ".js", ".svg", ".frag", ".vert", ".qsb")


def strip_comments(text: str) -> str:
    """Blank `//` and `/* */` comments, keeping every line count.

    Length and newlines are preserved so reported line numbers are the
    document's own.
    """
    out = []
    state = "code"
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                state, out = "line", out + [" ", " "]
                index += 2
                continue
            if char == "/" and nxt == "*":
                state, out = "block", out + [" ", " "]
                index += 2
                continue
            if char == '"':
                state = "string"
            out.append(char)
        elif state == "string":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                state = "code"
            out.append(char)
        elif state == "line":
            if char == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
        else:
            if char == "*" and nxt == "/":
                state, out = "code", out + [" ", " "]
                index += 2
                continue
            out.append("\n" if char == "\n" else " ")
        index += 1
    return "".join(out)


def documents():
    """Every plugin QML document. Recursive: the tree nests by domain,
    and a top-level glob would find nothing and pass having checked no
    reference at all."""
    return sorted(PLUGINS.rglob("*.qml"))


def references():
    """(document, line, literal) for every .svg reference in the tree."""
    for path in documents():
        body = strip_comments(path.read_text(encoding="utf-8"))
        for match in LITERAL.finditer(body):
            literal = match.group(1)
            if literal.endswith(".svg"):
                yield path, body.count("\n", 0, match.start()) + 1, literal


def literals(node, found):
    """The string constants of a path expression, in source order."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        found.append(node.value)
    elif isinstance(node, ast.BinOp):
        literals(node.left, found)
        literals(node.right, found)
    elif isinstance(node, ast.Call):
        for argument in list(node.args) + [word.value for word in node.keywords]:
            literals(argument, found)
    elif isinstance(node, ast.Tuple):
        for element in node.elts:
            literals(element, found)
    return found


# The names a path expression may mention without making it unreadable
# here: the file, a tree root, and the path helpers. Anything else is a
# value only the running program knows, and a guess at it would fail
# this gate on a correct module.
ANCHORS = {"__file__", "ROOT", "PLUGINS", "PLUGIN_ROOT", "mpf", "os", "pathlib", "Path", "__name__"}
HELPERS = {"join", "dirname", "abspath", "resolve", "parents", "parent", "path", "root", "Path", "fspath"}

# The one path that is deliberately not a file: the base URL an inline
# QML caller is compiled against, which only has to sit under mpf/.
SYNTHETIC = {pathlib.Path("mpf/follower-view-caller.qml")}


def computable(node) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id not in ANCHORS:
            return False
        if isinstance(child, ast.Attribute) and child.attr not in HELPERS:
            return False
    return True


def plugin_paths():
    """(file, line, path) for every plugin file a source names by path.

    A restructure rewrites these by hand, and a miss is quiet: the
    module reads the wrong file, or falls back at runtime. Keyed on the
    literal `mpf` segment, so either spelling — `os.path.join(…, "mpf",
    …)` or `ROOT / "mpf" / …` — is read the same way."""
    root = PLUGINS.root
    for folder in ("mpf", "Tests", "Tools"):
        for source in sorted(pathlib.Path(folder).rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Call, ast.BinOp)) or not computable(node):
                    continue
                parts = literals(node, [])
                if "mpf" not in parts or not parts[-1].endswith(EXTENSIONS):
                    continue
                yield source, node.lineno, root.joinpath(*parts[parts.index("mpf") + 1:])


def runtime_paths(root=None):
    """Read the literal paths used by actual Python resource-loading callers.

    Unlike the old literal-mpf scan, this includes __file__-independent
    plugin_path calls and Cura's relative Machine Action QML registration.
    Dynamic arguments fail rather than silently disappearing from the gate.
    """
    root = pathlib.Path(root) if root is not None else PLUGINS.root
    for source in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            parts = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "plugin_path":
                if node.keywords or not node.args or not all(
                        isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args):
                    raise AssertionError(f"{source}:{node.lineno}: resource path is not explicit")
                parts = [arg.value for arg in node.args]
            elif isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Attribute) and target.attr == "_qml_url" for target in node.targets):
                if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                    raise AssertionError(f"{source}:{node.lineno}: QML registration is not explicit")
                parts = [node.value.value]
            if parts is not None:
                target = root.joinpath(*parts).resolve()
                if not target.is_relative_to(root.resolve()):
                    raise AssertionError(f"{source}:{node.lineno}: resource escapes plugin root")
                yield source, node.lineno, target


def qml_file_paths():
    for source in documents():
        body = strip_comments(source.read_text(encoding="utf-8"))
        for match in LITERAL.finditer(body):
            value = match.group(1)
            if value.endswith((".qml", ".js", ".svg", ".qsb")) and not SCHEME.match(value):
                yield source, body.count("\n", 0, match.start()) + 1, (source.parent / value).resolve()
    for registration in sorted(PLUGINS.rglob("qmldir")):
        for line, text in enumerate(registration.read_text(encoding="utf-8").splitlines(), 1):
            words = text.split("#", 1)[0].split()
            if words and words[-1].endswith((".qml", ".js")):
                yield registration, line, (registration.parent / words[-1]).resolve()


class ResourceReferenceTests(unittest.TestCase):
    def test_every_referenced_svg_resolves_from_its_document(self):
        missing = []
        for path, line, literal in references():
            where = path.relative_to(PLUGINS.root)
            if SCHEME.match(literal):
                missing.append(f"{where}:{line}: {literal} is not a document-relative path")
            elif not (path.parent / literal).resolve().is_file():
                missing.append(f"{where}:{line}: {literal} resolves to no file")
        self.assertEqual(missing, [],
                         "svg references that do not resolve: %s" % missing)

    def test_every_plugin_file_named_by_path_exists(self):
        missing = []
        for source, line, target in plugin_paths():
            where = target.relative_to(ROOT)
            if where not in SYNTHETIC and not target.exists():
                missing.append(f"{source}:{line}: {where}")
        self.assertEqual(missing, [],
                         "plugin files named by path that are not there: %s" % missing)

    def test_python_entrypoints_and_qml_registrations_resolve(self):
        paths = list(runtime_paths()) + list(qml_file_paths())
        self.assertTrue(paths)
        for source, line, target in paths:
            self.assertTrue(target.is_file(), f"{source}:{line}: no resource at {target}")
        # Inventory the real loading callers: reverting one to unscanned
        # dirname(__file__) arithmetic cannot silently remove it from this gate.
        entrypoints = {(source.relative_to(PLUGINS.root).as_posix(), target.relative_to(PLUGINS.root).as_posix())
                       for source, _line, target in runtime_paths()}
        self.assertEqual(entrypoints, {
            ("CuraHost/MoonrakerOutputDevice.py", "Files/Transfers/MoonrakerUploadDialog.qml"),
            ("CuraHost/MoonrakerOutputDevicePlugin.py", "Monitor/MoonrakerMonitorDashboard.qml"),
            ("CuraHost/MoonrakerOutputDevicePlugin.py", "Monitor/MoonrakerMonitorBedMesh.qml"),
            ("CuraHost/MoonrakerFollowerMachineAction.py", "Settings/MoonrakerFollowerConfiguration.qml"),
            ("Preview/PreviewPresentation.py", "Preview/MoonrakerPreviewCardPanelHost.qml"),
            ("Preview/PreviewPresentation.py", "Preview/MoonrakerPreviewCardOverlayHost.qml"),
            ("WhatsNew/WhatsNewOverlay.py", "WhatsNew/WhatsNewOverlay.qml"),
            ("Plate/GpuStrokeMaterial.py", "Resources/Shaders/stroke.vert.qsb"),
            ("Plate/GpuStrokeMaterial.py", "Resources/Shaders/stroke.frag.qsb"),
        })

    def test_stale_upload_dialog_path_is_detected(self):
        source = (PLUGINS.root / "CuraHost/MoonrakerOutputDevice.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            # Compare canonical paths on macOS (/var -> /private/var) and Windows (8.3 aliases).
            root = pathlib.Path(directory).resolve()
            caller = root / "CuraHost/MoonrakerOutputDevice.py"
            caller.parent.mkdir()
            caller.write_text(source, encoding="utf-8")
            dialog = root / "Files/Transfers/MoonrakerUploadDialog.qml"
            dialog.parent.mkdir(parents=True)
            dialog.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
            self.assertTrue(all(target.is_file() for _, _, target in runtime_paths(root)))
            old = 'plugin_path("Files", "Transfers", "MoonrakerUploadDialog.qml")'
            self.assertIn(old, source)
            caller.write_text(source.replace(old, 'plugin_path("Monitor", "MoonrakerUploadDialog.qml")'),
                              encoding="utf-8")
            missing = [target for _, _, target in runtime_paths(root) if not target.is_file()]
            self.assertEqual(missing, [root / "Monitor/MoonrakerUploadDialog.qml"])

    def test_missing_shader_and_unreadable_runtime_path_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            # Compare canonical paths on macOS (/var -> /private/var) and Windows (8.3 aliases).
            root = pathlib.Path(directory).resolve()
            caller = root / "Shader.py"
            caller.write_text('path = plugin_path("Resources", "Shaders", "missing.qsb")\n', encoding="utf-8")
            targets = [target for _, _, target in runtime_paths(root)]
            self.assertEqual(targets, [root / "Resources/Shaders/missing.qsb"])
            self.assertFalse(targets[0].exists())
            caller.write_text('path = plugin_path(dynamic_name)\n', encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "not explicit"):
                list(runtime_paths(root))

    def test_entrypoints_and_resources_ship_in_both_archive_formats(self):
        package_id = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["package_id"]
        targets = {target for _, _, target in (*runtime_paths(), *qml_file_paths())}
        self.assertGreater(len(targets), 20)
        with tempfile.TemporaryDirectory() as directory:
            for script, extension, prefix in (
                    ("build_curapackage.py", ".curapackage", f"files/plugins/{package_id}/"),
                    ("build_marketplace_source.py", ".zip", f"{package_id}/")):
                archive = pathlib.Path(directory) / ("plugin" + extension)
                subprocess.run([sys.executable, str(ROOT / "Tools" / script), "--output", str(archive)],
                               cwd=ROOT, check=True, capture_output=True, text=True)
                with zipfile.ZipFile(archive) as package:
                    for target in targets:
                        entry = prefix + target.relative_to(PLUGINS.root).as_posix()
                        self.assertIn(entry, package.namelist())
                        self.assertEqual(package.read(entry), target.read_bytes(), entry)

    def test_the_scan_is_not_vacuous(self):
        # The failure this guards is the one the nesting already caused
        # elsewhere on this branch: a walk that stopped descending finds
        # nothing, and a gate over nothing is green.
        paths = documents()
        self.assertGreater(len(paths), 1, "no plugin documents were found")
        self.assertGreater(len({path.parent for path in paths}), 1,
                           "the walk reached one directory only")
        self.assertGreater(len(list(references())), 0,
                           "no svg reference was read — the literal scan matches nothing")
        self.assertGreater(len(list(plugin_paths())), 0,
                           "no plugin file was named by path — the AST scan matches nothing")


if __name__ == "__main__":
    unittest.main()
