"""References the engine check cannot see, resolved the runtime way.

A glyph whose URL misses the file is not a component error, it is an
empty slot in the UI, so a document that lost its `../resources/svg/`
prefix on a move ships silently. A source that names a plugin file by
path fails the same way: the module reads the wrong file, or the
failure is swallowed and the code falls back.

Two scans, both resolving references the way they resolve at runtime —
the URL against the CALLING DOCUMENT's directory, a path against the
tree root — and failing on anything that is not a file.
"""
import ast
import pathlib
import re
import unittest

from tests.source_root import SourceRoot

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
    for folder in ("mpf", "tests", "tools"):
        for source in sorted(pathlib.Path(folder).rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Call, ast.BinOp)) or not computable(node):
                    continue
                parts = literals(node, [])
                if "mpf" not in parts or not parts[-1].endswith(EXTENSIONS):
                    continue
                yield source, node.lineno, root.joinpath(*parts[parts.index("mpf") + 1:])


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
