"""The code-derived coverage matrix (TESTING.md 3, Tier 2).

A surface is anything a scenario can address or a user can touch:
@pyqtSlot verbs on the QML-facing objects, objectName'd interactive
items, MoonrakerProtocol endpoint builders, and the model's published
value_property keys. ``extract()`` walks the plugin sources and
returns every surface; ``check(map_)`` fails for any surface the
scenario map does not cover (an explicit exclusion is the only other
option). The map lives in tier2_map.py — the scenarios it names live
in tier2_scenarios.py.
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGINS = os.path.join(ROOT, "plugins")


def extract() -> Dict[str, List[str]]:
    """Every addressable surface, grouped by kind."""
    surfaces: Dict[str, List[str]] = {"slot": [], "objectName": [], "route": [], "key": []}
    for name in sorted(os.listdir(PLUGINS)):
        path = os.path.join(PLUGINS, name)
        if name.endswith(".py") and os.path.isfile(path):
            source = open(path, encoding="utf-8").read()
            if "@pyqtSlot" not in source:
                continue
            module = os.path.splitext(name)[0]
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    dec_name = getattr(dec, "id", None) or getattr(getattr(dec, "func", None), "id", None)
                    if dec_name == "pyqtSlot":
                        surfaces["slot"].append(f"{module}.{node.name}")
                        break
        if name.endswith(".qml") and os.path.isfile(path):
            source = open(path, encoding="utf-8").read()
            for match in re.finditer(r'objectName:\s*"([^"]+)"', source):
                surfaces["objectName"].append(match.group(1))
    protocol = open(os.path.join(PLUGINS, "MoonrakerProtocol.py"), encoding="utf-8").read()
    for match in re.finditer(r"def (\w*endpoint)\(", protocol):
        surfaces["route"].append(match.group(1))
    model = open(os.path.join(PLUGINS, "MoonrakerMonitorModel.py"), encoding="utf-8").read()
    for match in re.finditer(r'(\w+) = value_property\(', model):
        surfaces["key"].append(match.group(1))
    for kind in surfaces:
        surfaces[kind] = sorted(set(surfaces[kind]))
    return surfaces


def check(mapping) -> List[str]:
    """Failures: surfaces the map neither covers nor excludes."""
    surfaces = extract()
    failures: List[str] = []
    for kind, names in surfaces.items():
        for name in names:
            covered = name in mapping
            if covered:
                continue
            prefix = next((rule for rule in mapping.get("_prefix_rules", [])
                           if isinstance(rule, tuple) and rule[0] == kind and name.startswith(rule[1])), None)
            if prefix is None and name not in mapping.get("_exclusions", {}):
                failures.append(f"{kind}:{name}")
    return failures


def report(mapping) -> str:
    surfaces = extract()
    lines = []
    for kind in ("slot", "objectName", "route", "key"):
        names = surfaces[kind]
        covered = sum(1 for name in names if name in mapping
                      or any(r[0] == kind and name.startswith(r[1]) for r in mapping.get("_prefix_rules", [])))
        excluded = sum(1 for name in names if name in mapping.get("_exclusions", {}))
        lines.append(f"{kind}: {len(names)} surfaces, {covered} mapped, {excluded} excluded, "
                     f"{len(names) - covered - excluded} UNCOVERED")
    return "\n".join(lines)
